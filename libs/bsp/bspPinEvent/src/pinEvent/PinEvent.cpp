// SPDX-License-Identifier: Apache-2.0
#include "pinEvent/PinEvent.h"

#include <atomic>

namespace pinevent
{
namespace
{
// Single core: the ISR and the tasks see the same memory in program order; only the compiler must not move the
// accesses of the state across the sequence counter (no exclusive access, nothing from libatomic).
inline void barrier() { std::atomic_signal_fence(std::memory_order_seq_cst); }

/** The sequence counter and the idle mark are shared between contexts: every access is a real load or store. */
inline uint32_t load(uint32_t const& word) { return *static_cast<uint32_t const volatile*>(&word); }

inline void store(uint32_t& word, uint32_t const value) { *static_cast<uint32_t volatile*>(&word) = value; }

/** nowUs - sinceUs has reached durationUs (unsigned, wraps; valid while the real distance is below 2^32). */
inline bool passed(uint32_t nowUs, uint32_t sinceUs, uint32_t durationUs)
{
    return static_cast<uint32_t>(nowUs - sinceUs) >= durationUs;
}
} // namespace

void PinEvent::beginWrite()
{
    store(_seq, load(_seq) + 1U);
    barrier();
}

void PinEvent::endWrite()
{
    barrier();
    store(_seq, load(_seq) + 1U);
}

bool PinEvent::countsTowards(bool const newLevel) const
{
    switch (_config.edge)
    {
        case Edge::ACTIVATE:   return newLevel;
        case Edge::DEACTIVATE: return !newLevel;
        default:               return true;
    }
}

void PinEvent::settle(State& s, bool const levelAtEnd)
{
    s.pending = false;
    bool const expected = !s.level;
    if (levelAtEnd != expected)
    {
        ++s.glitches; // back on the old level: the provisional edge never happened
        return;
    }
    s.level = expected;
    if (s.counts)
    {
        ++s.count; // unsigned: wraps from 0xFFFFFFFF to 0
        s.timeUs   = s.edgeUs;
        s.accepted = true;
    }
}

bool PinEvent::onEdge(uint32_t const nowUs, bool const level)
{
    State& s = _state;
    if ((!s.started) || s.tripped)
    {
        return false; // the dispatcher calls only armed inputs; if it does not, stay off
    }
    uint32_t const seq = load(_seq);
    beginWrite();
    if (load(_idleMark) == seq)
    {
        // the task side found this history older than EXPIRE_AFTER_US: lock-out and window are over, whatever
        // the wrapped time differences say
        if (s.pending)
        {
            settle(s, s.lastSeen);
        }
        s.windowCount = 0U;
    }
    s.lastUs = nowUs;

    bool keep = true;
    if (s.pending && (!passed(nowUs, s.edgeUs, _config.debounceUs)))
    {
        // bounce inside the lock-out: only remember where the level went
        ++s.bounces;
        ++s.lockBounces;
        s.lastSeen = level;
        if (s.lockBounces > MAX_BOUNCES)
        {
            s.pending = false; // the provisional edge is not confirmed by a storm
            keep      = false;
        }
    }
    else
    {
        if (s.pending)
        {
            settle(s, s.lastSeen); // the lock-out has passed: its last level is the level at its end
        }
        // a candidate edge: towards the other level of the stable one (with debounce), or any reported edge
        bool const toward = (_config.debounceUs != 0U) ? (!s.level) : level;
        bool const counts = (_config.debounceUs != 0U) ? countsTowards(toward) : true;
        if (counts)
        {
            if ((s.windowCount == 0U) || passed(nowUs, s.windowUs, _config.windowUs))
            {
                s.windowUs    = nowUs;
                s.windowCount = 0U;
            }
            ++s.windowCount;
            keep = (s.windowCount <= _config.maxEvents);
        }
        if (!keep)
        {
            // rate limit: this candidate is not taken
        }
        else if (_config.debounceUs != 0U)
        {
            s.pending     = true;
            s.counts      = counts;
            s.edgeUs      = nowUs;
            s.lastSeen    = level;
            s.lockBounces = 0U;
        }
        else
        {
            // no debounce: the edge detector reports only the configured edges, each one is accepted
            s.level = level;
            ++s.count;
            s.timeUs   = nowUs;
            s.accepted = true;
        }
    }
    if (!keep)
    {
        s.tripped = true;
        s.tripUs  = nowUs;
        ++s.trips;
    }
    endWrite();
    return keep;
}

void PinEvent::start(bool const level, uint32_t const nowUs)
{
    beginWrite();
    _state         = State();
    _state.level   = level;
    _state.lastUs  = nowUs;
    _state.started = true;
    endWrite();
}

void PinEvent::rearm(bool const level, uint32_t const nowUs)
{
    State& s = _state;
    beginWrite();
    s.level       = level;
    s.lastUs      = nowUs;
    s.pending     = false;
    s.windowCount = 0U;
    s.lockBounces = 0U;
    s.tripped     = false;
    endWrite();
}

bool PinEvent::snapshot(TimeSource const now, Snapshot& out) const
{
    for (uint8_t attempt = 0U; attempt < READ_ATTEMPTS; ++attempt)
    {
        uint32_t const before = load(_seq);
        barrier();
        out.state = _state;
        out.nowUs = now(); // inside the copy: no edge of the copy is younger than this time
        barrier();
        if (((before & 1U) == 0U) && (load(_seq) == before))
        {
            out.seq = before;
            return true;
        }
    }
    return false;
}

void PinEvent::settledView(Snapshot& snap) const
{
    State& s = snap.state;
    if (s.pending && ((load(_idleMark) == snap.seq) || passed(snap.nowUs, s.edgeUs, _config.debounceUs)))
    {
        // no edge since the copy's last one: the level reported last is the level at the end of the lock-out
        settle(s, s.lastSeen);
    }
}

Reading PinEvent::readingOf(State const& s)
{
    uint8_t quality = QUALITY_DEBOUNCE;
    if (s.tripped)
    {
        quality = QUALITY_ERROR;
    }
    else if (s.accepted)
    {
        quality = QUALITY_OK;
    }
    return Reading{s.count, s.timeUs, quality};
}

bool PinEvent::service(TimeSource const now)
{
    Snapshot snap{};
    if (!snapshot(now, snap))
    {
        return false; // edges kept coming during each copy: try again next cycle
    }
    State const& s = snap.state;
    if (!s.started)
    {
        return false;
    }
    if (s.tripped)
    {
        return passed(snap.nowUs, s.tripUs, _config.rearmUs);
    }
    if ((s.pending || (s.windowCount != 0U)) && passed(snap.nowUs, s.lastUs, EXPIRE_AFTER_US))
    {
        store(_idleMark, snap.seq); // valid until the next write: the ISR expires it at the next edge
    }
    else if (load(_idleMark) != snap.seq)
    {
        store(_idleMark, 1U); // odd: matches no stable sequence value, so no old mark can alias after a wrap
    }
    else
    {
        // mark still current
    }
    return false;
}

bool PinEvent::read(TimeSource const now, Reading& out) const
{
    Snapshot snap{};
    if (!snapshot(now, snap))
    {
        return false;
    }
    settledView(snap);
    out = readingOf(snap.state);
    return true;
}

bool PinEvent::statistics(TimeSource const now, Statistics& out) const
{
    Snapshot snap{};
    if (!snapshot(now, snap))
    {
        return false;
    }
    settledView(snap);
    State const& s = snap.state;
    out.reading    = readingOf(s);
    out.trips      = s.trips;
    out.bounces    = s.bounces;
    out.glitches   = s.glitches;
    out.armed      = s.started && (!s.tripped);
    out.reArmInUs  = 0U;
    if (s.tripped)
    {
        uint32_t const since = snap.nowUs - s.tripUs;
        out.reArmInUs        = (since < _config.rearmUs) ? (_config.rearmUs - since) : 0U;
    }
    return true;
}

void PinEvents::start()
{
    for (uint8_t i = 0U; i < _count; ++i)
    {
        _inputs[i].start(_hardware.level(i), _now());
        _hardware.arm(i, detectorEdges(_inputs[i].config()));
    }
}

void PinEvents::cycle()
{
    for (uint8_t i = 0U; i < _count; ++i)
    {
        if (_inputs[i].service(_now))
        {
            _inputs[i].rearm(_hardware.level(i), _now());
            _hardware.arm(i, detectorEdges(_inputs[i].config()));
        }
    }
}

} // namespace pinevent
