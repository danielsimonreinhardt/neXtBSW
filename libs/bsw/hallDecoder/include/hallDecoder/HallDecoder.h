#pragma once

#include <cstdint>

namespace hallDecoder
{
/**
 * Evaluation of three digital Hall signals of a brushless motor (IO-07): state, sector, electrical and
 * mechanical period, speed, direction and a quality - hardware independent.
 *
 * The decoder knows no MCU, no register, no pin and no clock. Its access (for the s32k1xx the pin interrupt of
 * the PORT, platforms/s32k1xx/bsp/bspHall; later maybe a timer capture) hands it two things only:
 *   - edge(state, nowUs) whenever one of the three levels may have changed, with the state sampled right then,
 *   - read(state, nowUs) when a value is wanted, with the state sampled right then,
 * where state = (u << 2) | (v << 1) | w with the inversion of the board already applied, and nowUs is a
 * free-running 32-bit microsecond time (wraps). The access serializes both calls (edge() runs in an interrupt,
 * read() under an interrupt lock); the decoder itself takes no lock.
 *
 * How it measures:
 *   - The sector 1..6 follows from the sector table of the arrangement; the states the arrangement cannot
 *     produce map to 0.
 *   - One electrical revolution is six sector steps, so the period is the time between an edge and the sixth
 *     edge before it - no assumption about equal sector widths, and only reported after six consecutive steps
 *     in the same direction, so a missed edge cannot shorten it. The mechanical period is the electrical one
 *     times the pole pairs; the speed follows from it.
 *   - The direction comes from the transition (is the new sector the neighbour above or below the last one);
 *     `forwardSign` only chooses the sign.
 *   - Debouncing: an edge closer than debounceUs to the last accepted edge is counted as bouncing and ignored.
 *     The accepted edge is only provisional while its debounce window is open: once the window has passed, the
 *     level that stood at its end is compared with the accepted one. A glitch that ended on the old level (the
 *     accepted step never happened) is taken back completely - state, sector, run, stamps, fault - and counted
 *     as a glitch. A window that ended on a third state (a real edge right behind the glitch) takes the glitch
 *     back and accepts that state with the time of the glitch. The check runs at the next edge (with the last
 *     level seen inside the window) and in read() (with the level sampled then), whichever comes first.
 *
 * Quality (the values are the QLF_* signal qualifiers of the RTE, COM-03), in this order:
 *   - QUALITY_TIMEOUT for standstill: no accepted edge within standstillUs - also the case of no sensor at all.
 *     Position (state, sector) stays valid, period and speed are the last measured ones, the direction becomes
 *     DIRECTION_UNKNOWN. A standstill is latched until the next accepted edge and throws the running revolution
 *     away, so neither a wrapping counter nor the time the motor stood still can enter a period.
 *   - QUALITY_ERROR for a state the arrangement cannot produce (sector 0) and for a jumped sector, until a clean
 *     revolution follows.
 *   - QUALITY_DEBOUNCE until six consecutive steps in one direction have been seen.
 *   - QUALITY_OK otherwise. The cause of a fault is visible in statistics().
 */

/** Signal qualifier values of the RTE (QLF_* of Rte_Qlf.h); tools/rtegen/test_rtegen.py compares both lists. */
constexpr uint8_t QUALITY_DEBOUNCE = 0U;  // not enough steps yet for a period
constexpr uint8_t QUALITY_OK       = 1U;
constexpr uint8_t QUALITY_TIMEOUT  = 8U;  // standstill: no edge within standstillUs
constexpr uint8_t QUALITY_ERROR    = 16U; // invalid state combination or jumped sector

constexpr int8_t DIRECTION_UNKNOWN  = 0;
constexpr int8_t DIRECTION_FORWARD  = 1; // the way through the sector sequence the project calls forward
constexpr int8_t DIRECTION_BACKWARD = -1;

/** Six sector steps are one electrical revolution; both supported arrangements have six valid states. */
constexpr uint8_t SECTORS = 6U;

/** Everything the decoder needs to know about one motor; the generator derives it from the project. */
struct HallDecoderConfig
{
    uint8_t sectors[8];    // state -> sector 1..6 of the arrangement, 0 = the arrangement cannot produce it
    int8_t forwardSign;    // +1 if a step up the sector sequence is forward, -1 if it is backward
    uint16_t polePairs;    // electrical revolutions per mechanical revolution
    uint32_t debounceUs;   // an edge closer than this to the last accepted one is bouncing (0: off)
    uint32_t standstillUs; // no accepted edge for this long: QUALITY_TIMEOUT
};

struct Reading
{
    uint32_t period_us;      // one electrical revolution (six sectors); 0 until the first valid one
    uint32_t mech_period_us; // one mechanical revolution: period_us * polePairs
    uint32_t speed_rpm;      // mechanical speed of the same revolution, magnitude
    uint8_t state;           // (u << 2) | (v << 1) | w as sampled for this reading
    uint8_t sector;          // 1..6 of the arrangement, 0 while the state is not a valid combination
    int8_t direction;        // DIRECTION_*
    uint8_t quality;         // QUALITY_*
};

/** Counters of the decoder; for a console or a diagnosis, not part of the RTE values. */
struct Statistics
{
    uint32_t edges;         // accepted edges that stood (state changes)
    uint32_t bounces;       // edges ignored because they came within debounceUs
    uint32_t invalidStates; // accepted edges into a state the arrangement cannot produce
    uint32_t sectorJumps;   // accepted edges that skipped a sector (no neighbour of the last one)
    uint32_t standstills;   // times a standstill was detected (a running revolution thrown away)
    uint32_t glitches;      // accepted edges taken back: the level was back at the old state after debounceUs
};

/** Value of a sensor that was never read: the quality says so, the values are zero. */
constexpr Reading noReading() { return Reading{0U, 0U, 0U, 0U, 0U, DIRECTION_UNKNOWN, QUALITY_DEBOUNCE}; }

/** The sector table maps exactly six states to the sectors 1..6, each once, and the motor is plausible. */
constexpr bool isValidSectorTable(HallDecoderConfig const& c)
{
    uint32_t seen = 0U;
    uint8_t valid = 0U;
    for (uint8_t state = 0U; state < 8U; ++state)
    {
        uint8_t const sector = c.sectors[state];
        if (sector == 0U)
        {
            continue;
        }
        if ((sector > SECTORS) || ((seen & (1UL << sector)) != 0U))
        {
            return false; // a sector number twice, or outside 1..6
        }
        seen |= (1UL << sector);
        ++valid;
    }
    return (valid == SECTORS) && (seen == 0x7EU) && (c.polePairs != 0U)
           && ((c.forwardSign == DIRECTION_FORWARD) || (c.forwardSign == DIRECTION_BACKWARD));
}

/**
 * Debounce and standstill fit the 32-bit microsecond time base: six standstill times stay inside half a wrap,
 * so the stamps of one reported revolution are never ambiguous.
 */
constexpr bool isValidTiming(HallDecoderConfig const& c)
{
    return (c.standstillUs != 0U) && (c.standstillUs <= (0xFFFFFFFFU / 12U)) && (c.debounceUs < c.standstillUs);
}

class HallDecoder
{
public:
    explicit constexpr HallDecoder(HallDecoderConfig const& config)
    : _config(config), _core(), _before(), _statistics(), _statisticsBefore()
    {}

    /** Starts from the position the motor stands in (state sampled now): no run, no fault, no standstill yet. */
    void start(uint8_t state, uint32_t nowUs);

    /** One of the levels may have changed: `state` sampled right now, `nowUs` the time of the change. */
    void edge(uint8_t state, uint32_t nowUs);

    /**
     * Current values: `state` sampled right now, `nowUs` taken at the same time and not older than the last
     * edge() (the access samples both under the same lock as the call). Latches a standstill and confirms or
     * takes back a provisional step, so it is not const.
     */
    Reading read(uint8_t state, uint32_t nowUs);

    Statistics statistics() const { return _statistics; }

    HallDecoderConfig const& config() const { return _config; }

private:
    static constexpr uint8_t STAMPS     = 8U; // ring of edge stamps, power of two > SECTORS
    static constexpr uint8_t STAMP_MASK = STAMPS - 1U;

    struct Core
    {
        uint32_t stamps[STAMPS]; // time of the last accepted edges, newest at (write - 1) & STAMP_MASK
        uint32_t lastEdgeUs;     // time of the last accepted edge (the start time after start())
        uint32_t periodUs;       // last valid electrical revolution, 0 until there was one
        uint32_t speedRpm;
        uint8_t write;           // next index in stamps
        uint8_t steps;           // consecutive steps in `direction`, capped at SECTORS
        uint8_t lastState;       // accepted state
        uint8_t lastSector;      // last valid sector, 0 after the start and after an invalid combination
        int8_t direction;
        bool faulted;            // invalid state or jumped sector since the last complete revolution
        bool standstill;         // latched by read(), cleared by the next accepted edge
        bool provisional;        // the last accepted edge waits for the end of its debounce window
        uint8_t levelSeen;       // last state handed to edge(), accepted or not
    };

    void accept(uint8_t state, uint32_t nowUs);
    void settle(uint8_t level, uint32_t nowUs);

    HallDecoderConfig const& _config;
    Core _core;
    Core _before; // the core before the provisional edge, to take it back
    Statistics _statistics;
    Statistics _statisticsBefore;
};

} // namespace hallDecoder
