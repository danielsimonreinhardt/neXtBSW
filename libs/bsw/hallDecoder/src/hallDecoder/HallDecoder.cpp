#include "hallDecoder/HallDecoder.h"

namespace hallDecoder
{
namespace
{
constexpr uint32_t US_PER_MINUTE = 60U * 1000U * 1000U;

/** Next sector in the sequence of the arrangement: 1..6, cyclic. */
constexpr uint8_t nextSector(uint8_t const sector)
{
    return (sector >= SECTORS) ? 1U : static_cast<uint8_t>(sector + 1U);
}
} // namespace

void HallDecoder::start(uint8_t const state, uint32_t const nowUs)
{
    _core            = Core{};
    _core.lastEdgeUs = nowUs;
    _core.direction  = DIRECTION_UNKNOWN;
    // Start from the position the motor stands in, so the first real change is an edge and not a step out of an
    // invented state.
    _core.lastState  = static_cast<uint8_t>(state & 7U);
    _core.levelSeen  = _core.lastState;
    _before          = _core;
    _statistics      = Statistics{};
    _statisticsBefore = Statistics{};
}

void HallDecoder::settle(uint8_t const level, uint32_t const /* nowUs */)
{
    // The debounce window of the last accepted edge is over: the level that stood at its end decides. Back at
    // the state before that edge means the edge was a glitch - the step did not happen, so it is taken back
    // completely instead of turning the next real edge into a jumped sector (or into a skipped step).
    _core.provisional = false;
    if (level == _core.lastState)
    {
        return; // the accepted edge stands
    }
    uint32_t const glitchUs = _core.lastEdgeUs;
    uint32_t const bounces  = _statistics.bounces; // the edges inside the window stay counted
    _core                   = _before;
    _core.levelSeen         = level;
    _statistics             = _statisticsBefore;
    _statistics.bounces     = bounces;
    ++_statistics.glitches;
    if (level != _core.lastState)
    {
        // Neither the accepted nor the old level: the window ended on a third state (a real edge right behind
        // the glitch, within debounceUs). Accepted with the time of the glitch - off by less than debounceUs -
        // so the next edge continues from where the pins really are instead of reporting a jump.
        accept(level, glitchUs);
        _core.provisional = false;
    }
}

void HallDecoder::edge(uint8_t const sampled, uint32_t const nowUs)
{
    uint8_t const state = static_cast<uint8_t>(sampled & 7U);
    if (_core.provisional && ((nowUs - _core.lastEdgeUs) >= _config.debounceUs))
    {
        settle(_core.levelSeen, nowUs);
    }
    _core.levelSeen = state;
    if (state == _core.lastState)
    {
        return; // the edge belonged to another sensor, or the level is already back at the accepted one
    }
    if ((nowUs - _core.lastEdgeUs) < _config.debounceUs)
    {
        // Chatter around a transition: the accepted edge already carries the new level, so the ignored edges
        // must not move lastState. Whether the accepted edge stands is decided when the window is over.
        ++_statistics.bounces;
        return;
    }
    accept(state, nowUs);
}

void HallDecoder::accept(uint8_t const state, uint32_t const nowUs)
{
    _before           = _core;
    _statisticsBefore = _statistics;
    _core.provisional = (_config.debounceUs != 0U);

    _core.lastState  = state;
    _core.lastEdgeUs = nowUs;
    _core.standstill = false; // there is movement again; read() latches it anew if the edges stop
    ++_statistics.edges;

    uint8_t const sector = _config.sectors[state];
    if (sector == 0U)
    {
        // A combination this arrangement cannot produce: an open sensor line, no sensor at all (with pull-ups
        // that reads 111), a swapped signal or the wrong arrangement. Reported, never turned into a sector.
        ++_statistics.invalidStates;
        _core.faulted    = true;
        _core.steps      = 0U;
        _core.direction  = DIRECTION_UNKNOWN;
        _core.lastSector = 0U; // the next valid state starts a new run instead of looking like a jump
        return;
    }
    uint8_t const previous = _core.lastSector;
    _core.lastSector       = sector;
    int8_t direction       = DIRECTION_UNKNOWN;
    if (previous != 0U)
    {
        if (sector == nextSector(previous))
        {
            direction = _config.forwardSign;
        }
        else if (previous == nextSector(sector))
        {
            direction = static_cast<int8_t>(-_config.forwardSign);
        }
        else
        {
            // A sector was skipped: a missed edge, chatter longer than debounceUs or two swapped signals.
            ++_statistics.sectorJumps;
            _core.faulted = true;
        }
    }
    if (direction == DIRECTION_UNKNOWN)
    {
        _core.steps = 0U; // first valid sector, or a jump: no complete revolution from here yet
    }
    else if (direction == _core.direction)
    {
        if (_core.steps < SECTORS)
        {
            ++_core.steps;
        }
    }
    else
    {
        _core.steps = 1U; // the direction changed: this step is the first of the new run
    }
    _core.direction = direction;

    // Every accepted edge with a valid sector is stamped, so the stamp SECTORS pushes back bounds exactly one
    // electrical revolution (six sector steps) - no assumption about equal sector widths.
    _core.stamps[_core.write] = nowUs;
    uint8_t const oldest      = static_cast<uint8_t>((_core.write + STAMPS - SECTORS) & STAMP_MASK);
    _core.write               = static_cast<uint8_t>((_core.write + 1U) & STAMP_MASK);
    if (_core.steps < SECTORS)
    {
        return;
    }
    uint32_t const period = nowUs - _core.stamps[oldest];
    if (period == 0U)
    {
        return; // six edges within one microsecond: not a speed, and it would divide by zero
    }
    _core.periodUs         = period;
    uint64_t const divisor = static_cast<uint64_t>(period) * _config.polePairs;
    _core.speedRpm         = static_cast<uint32_t>((US_PER_MINUTE + (divisor / 2U)) / divisor);
    _core.faulted          = false; // a complete revolution in one direction: the earlier fault is over
}

Reading HallDecoder::read(uint8_t const sampled, uint32_t const nowUs)
{
    uint8_t const state = static_cast<uint8_t>(sampled & 7U);
    if (_core.provisional && ((nowUs - _core.lastEdgeUs) >= _config.debounceUs))
    {
        settle(state, nowUs);
    }
    if (((nowUs - _core.lastEdgeUs) > _config.standstillUs) && !_core.standstill)
    {
        // The running revolution is thrown away: the interval from the last edge into the standstill is no
        // measurement, so the next edge has to start a new run (lastSector 0 does that) instead of counting an
        // interval that contains the whole standstill. Latched, so a wrapping counter cannot make an old edge
        // look fresh.
        _core.standstill = true;
        _core.steps      = 0U;
        _core.direction  = DIRECTION_UNKNOWN;
        _core.lastSector = 0U;
        ++_statistics.standstills;
    }
    // The position is the sampled one and not the last accepted edge: a motor that stands still produces no
    // edge, but its sensors still say where it stands.
    uint8_t const sector = _config.sectors[state];
    Reading r;
    r.state          = state;
    r.sector         = sector; // 0 for a combination the arrangement cannot produce: no fake position
    r.period_us      = _core.periodUs;
    r.mech_period_us = _core.periodUs * _config.polePairs;
    r.speed_rpm      = _core.speedRpm;
    r.direction      = _core.direction;
    if (_core.standstill)
    {
        // No edge within standstillUs - also the case of a board without a sensor. Position may still be valid;
        // the movement values are kept like a held CAN signal (COM-03) and the quality says so.
        r.quality = QUALITY_TIMEOUT;
    }
    else if (sector == 0U)
    {
        r.quality = QUALITY_ERROR; // invalid combination: no sector, no speed - and it is said
    }
    else if (_core.faulted)
    {
        r.quality = QUALITY_ERROR; // an invalid state or a jumped sector, no clean revolution since
    }
    else if ((_core.steps < SECTORS) || (_core.periodUs == 0U))
    {
        r.quality = QUALITY_DEBOUNCE; // turning, but no full electrical revolution in one direction yet
    }
    else
    {
        r.quality = QUALITY_OK;
    }
    return r;
}

} // namespace hallDecoder
