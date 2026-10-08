// SPDX-License-Identifier: Apache-2.0
// Host test of pinEvent/PinEvent.h/.cpp and pinEvent/PinEventConsole.h (IO-53): events on digital inputs without
// any MCU, register or generator. The test owns a simulated 64-bit time whose low 32 bits are the module's
// microsecond time, so every case - including the 32-bit wrap and a quiet input for longer than one wrap - is exact.
// An "interrupt" is a direct call of PinEvents::onEdge(); an interrupt during a reader's copy is injected from the
// time source, which the reader calls inside its copy.
//
// Checked: configuration rules and the detector edges; counting without debounce; debounce with bounces, level
// confirmation (in read() and committed by the next edge, no double count), glitches taken back; the edge filter
// (activate/deactivate/both); rate limit (trip, QUALITY_ERROR, disarm, nothing counted while off, automatic re-arm
// after the hold-off with a fresh window, window expiry, candidates that were glitches count, bounce storm inside
// one lock-out); counter wrap; time wrap; a quiet input for longer than one wrap (lock-out and window expire,
// pending edge stays confirmed); consistent reads with interrupts during the copy and a writer inside; the table
// (unknown input, start arms with the detector edges); console text.
// Build and run: tools/test -k pinevent (zig), mutants: test_pinevent_mutations.py.

#include "pinEvent/PinEvent.h"
#include "pinEvent/PinEventConsole.h"

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>

namespace pinevent
{
struct PinEventTestAccess
{
    static void setCount(PinEvent& e, uint32_t const count) { e._state.count = count; }

    static void setSeq(PinEvent& e, uint32_t const seq) { e._seq = seq; }

    static uint32_t seq(PinEvent const& e) { return e._seq; }
};
} // namespace pinevent

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

using namespace pinevent;

// ---------------------------------------------------------------------------------------------- simulated world
uint64_t realUs = 0U; // the true time; the module sees its low 32 bits

// Interrupts injected into the next reads: each call of the time source fires one edge while > 0
struct Injection
{
    PinEvents* events = nullptr;
    uint8_t input     = 0U;
    unsigned left     = 0U;
    bool level        = true;
} injection;

uint32_t clockUs()
{
    uint32_t const now = static_cast<uint32_t>(realUs);
    if ((injection.events != nullptr) && (injection.left > 0U))
    {
        --injection.left;
        realUs += 7U; // the interrupt comes a little later than the time the reader just took
        (void)injection.events->onEdge(injection.input, static_cast<uint32_t>(realUs), injection.level);
        injection.level = !injection.level;
    }
    return now;
}

void at(uint64_t const us) { realUs = us; }

uint32_t now32() { return static_cast<uint32_t>(realUs); }

class FakeHardware : public IPinEventHardware
{
public:
    bool levels[4]     = {false, false, false, false};
    Detect armed[4]    = {Detect::NONE, Detect::NONE, Detect::NONE, Detect::NONE};
    unsigned arms[4]   = {0U, 0U, 0U, 0U};

    bool level(uint8_t const input) const override { return levels[input]; }

    void arm(uint8_t const input, Detect const edges) override
    {
        armed[input] = edges;
        ++arms[input];
    }
};

/** One input in its own table, like a generated project with a single event input. */
struct Rig
{
    explicit Rig(PinEventConfig const& c, bool startLevel = false)
    : config(c), event(config), table(&event, 1U, hw, &clockUs)
    {
        hw.levels[0] = startLevel;
        table.start();
    }

    /** The dispatcher: an edge at the current time; a false answer disarms the pin like the family does. */
    bool edge(bool const level)
    {
        bool const keep = table.onEdge(0U, now32(), level);
        if (!keep)
        {
            hw.armed[0] = Detect::NONE;
        }
        return keep;
    }

    Reading reading()
    {
        Reading r{0xDEADU, 0xDEADU, 0xEEU};
        CHECK(table.read(0U, r));
        return r;
    }

    Statistics statistics()
    {
        Statistics s{};
        CHECK(table.statistics(0U, s));
        return s;
    }

    PinEventConfig config;
    FakeHardware hw;
    PinEvent event;
    PinEvents table;
};

PinEventConfig cfg(Edge edge, uint32_t debounceUs, uint32_t maxEvents, uint32_t windowUs, uint32_t rearmUs)
{
    return PinEventConfig{"IN", edge, debounceUs, maxEvents, windowUs, rearmUs};
}

// ------------------------------------------------------------------------------------------------------ cases
void testConfig()
{
    PinEventConfig const good = cfg(Edge::ACTIVATE, 2000U, 10U, 100000U, 100000U);
    CHECK(isValidConfig(good));
    PinEventConfig c = good;
    c.debounceUs     = 10000U; // 10 * 10 ms = window: the debounce and not the limit would bound the rate
    CHECK(!isValidConfig(c));
    c            = good;
    c.maxEvents  = 0U;
    CHECK(!isValidConfig(c));
    c            = good;
    c.maxEvents  = MAX_EVENTS + 1U;
    CHECK(!isValidConfig(c));
    c            = good;
    c.windowUs   = MAX_WINDOW_US + 1U;
    CHECK(!isValidConfig(c));
    c            = good;
    c.rearmUs    = 0U;
    CHECK(!isValidConfig(c));
    c            = good;
    c.rearmUs    = MAX_REARM_US + 1U;
    CHECK(!isValidConfig(c));
    c            = good;
    c.debounceUs = MAX_DEBOUNCE_US + 1U;
    c.windowUs   = MAX_WINDOW_US;
    c.maxEvents  = 1U;
    CHECK(!isValidConfig(c));
    c      = good;
    c.name = nullptr;
    CHECK(!isValidConfig(c));
    c      = good;
    c.edge = static_cast<Edge>(0U);
    CHECK(!isValidConfig(c));
    // the largest values the project allows are valid, and the expiry is longer than all of them
    CHECK(isValidConfig(PinEventConfig{"MAX", Edge::BOTH, MAX_DEBOUNCE_US, 59U, MAX_WINDOW_US, MAX_REARM_US}));
    CHECK(EXPIRE_AFTER_US > MAX_WINDOW_US);
    CHECK(EXPIRE_AFTER_US > MAX_REARM_US);
    CHECK(EXPIRE_AFTER_US > MAX_DEBOUNCE_US);

    CHECK(detectorEdges(cfg(Edge::ACTIVATE, 0U, 1U, 1U, 1U)) == Detect::ACTIVATE);
    CHECK(detectorEdges(cfg(Edge::DEACTIVATE, 0U, 1U, 1U, 1U)) == Detect::DEACTIVATE);
    CHECK(detectorEdges(cfg(Edge::BOTH, 0U, 1U, 1U, 1U)) == Detect::BOTH);
    // level confirmation needs every change of the level
    CHECK(detectorEdges(cfg(Edge::ACTIVATE, 1U, 1U, 10U, 1U)) == Detect::BOTH);
    CHECK(detectorEdges(cfg(Edge::DEACTIVATE, 1U, 1U, 10U, 1U)) == Detect::BOTH);
}

void testStartAndCountWithoutDebounce()
{
    at(1000U);
    Rig rig(cfg(Edge::ACTIVATE, 0U, 100U, 1000000U, 100000U));
    CHECK(rig.hw.armed[0] == Detect::ACTIVATE);
    CHECK(rig.hw.arms[0] == 1U);
    Reading r = rig.reading();
    CHECK(r.count == 0U);
    CHECK(r.timeUs == 0U);
    CHECK(r.quality == QUALITY_DEBOUNCE); // no edge yet: the time stamp means nothing
    Statistics s = rig.statistics();
    CHECK(s.armed);

    at(1100U);
    CHECK(rig.edge(true));
    r = rig.reading();
    CHECK(r.count == 1U);
    CHECK(r.timeUs == 1100U);
    CHECK(r.quality == QUALITY_OK);
    at(1150U); // an edge right behind: no debounce, counted as well
    CHECK(rig.edge(true));
    at(5000U);
    r = rig.reading();
    CHECK(r.count == 2U);
    CHECK(r.timeUs == 1150U);
    s = rig.statistics();
    CHECK((s.bounces == 0U) && (s.glitches == 0U) && (s.trips == 0U));
}

void testDebounceConfirmsAfterLockOut()
{
    at(1000U);
    Rig rig(cfg(Edge::ACTIVATE, 1000U, 10U, 100000U, 100000U));
    CHECK(rig.hw.armed[0] == Detect::BOTH);
    at(5000U);
    CHECK(rig.edge(true)); // press: opens the lock-out
    at(5100U);
    CHECK(rig.edge(false)); // bouncing
    at(5200U);
    CHECK(rig.edge(true));
    at(5300U);
    CHECK(rig.edge(false));
    at(5400U);
    CHECK(rig.edge(true));
    at(5999U);
    Reading r = rig.reading();
    CHECK(r.count == 0U); // still inside the lock-out: not confirmed yet
    CHECK(r.quality == QUALITY_DEBOUNCE);
    at(6000U);
    r = rig.reading(); // the lock-out has passed, the level stood active at its end
    CHECK(r.count == 1U);
    CHECK(r.timeUs == 5000U); // the time of the opening edge, not of the confirmation
    CHECK(r.quality == QUALITY_OK);
    Statistics s = rig.statistics();
    CHECK(s.bounces == 4U);
    CHECK(s.glitches == 0U);

    at(20000U); // release: commits the press, opens a lock-out towards inactive, not counted
    CHECK(rig.edge(false));
    at(20100U);
    CHECK(rig.edge(true));
    at(20200U);
    CHECK(rig.edge(false));
    at(22000U);
    r = rig.reading();
    CHECK(r.count == 1U); // committed once, no double count; the release does not count
    CHECK(r.timeUs == 5000U);

    at(30000U); // second press
    CHECK(rig.edge(true));
    at(31000U);
    r = rig.reading();
    CHECK(r.count == 2U);
    CHECK(r.timeUs == 30000U);
    s = rig.statistics();
    CHECK(s.bounces == 6U);
}

void testGlitchIsTakenBack()
{
    at(1000U);
    Rig rig(cfg(Edge::ACTIVATE, 1000U, 10U, 100000U, 100000U));
    at(5000U);
    CHECK(rig.edge(true)); // a spike
    at(5050U);
    CHECK(rig.edge(false)); // gone again inside the lock-out
    at(7000U);
    Reading r = rig.reading();
    CHECK(r.count == 0U);
    CHECK(r.quality == QUALITY_DEBOUNCE);
    Statistics s = rig.statistics();
    CHECK(s.glitches == 1U);

    // an edge whose sampled level is already back (pulse shorter than the ISR entry): a glitch as well
    at(9000U);
    CHECK(rig.edge(false)); // commits the first glitch; candidate towards active with level inactive seen
    at(11000U);
    r = rig.reading();
    CHECK(r.count == 0U);
    s = rig.statistics();
    CHECK(s.glitches == 2U);

    // a real press after the glitches is counted
    at(20000U);
    CHECK(rig.edge(true));
    at(21000U);
    r = rig.reading();
    CHECK(r.count == 1U);
    CHECK(r.timeUs == 20000U);
}

void testEdgeFilter()
{
    // deactivate: only releases count
    {
        at(1000U);
        Rig rig(cfg(Edge::DEACTIVATE, 500U, 10U, 100000U, 100000U), true);
        at(2000U);
        CHECK(rig.edge(false)); // release
        at(3000U);
        CHECK(rig.edge(true)); // press
        at(4000U);
        CHECK(rig.edge(false)); // release
        at(5000U);
        Reading const r = rig.reading();
        CHECK(r.count == 2U);
        CHECK(r.timeUs == 4000U);
    }
    // both: every confirmed change
    {
        at(1000U);
        Rig rig(cfg(Edge::BOTH, 500U, 10U, 100000U, 100000U));
        at(2000U);
        CHECK(rig.edge(true));
        at(3000U);
        CHECK(rig.edge(false));
        at(4000U);
        CHECK(rig.edge(true));
        at(5000U);
        Reading const r = rig.reading();
        CHECK(r.count == 3U);
        CHECK(r.timeUs == 4000U);
    }
    // activate: releases do not count
    {
        at(1000U);
        Rig rig(cfg(Edge::ACTIVATE, 500U, 10U, 100000U, 100000U));
        at(2000U);
        CHECK(rig.edge(true));
        at(3000U);
        CHECK(rig.edge(false));
        at(4000U);
        CHECK(rig.edge(true));
        at(5000U);
        CHECK(rig.edge(false));
        at(6000U);
        Reading const r = rig.reading();
        CHECK(r.count == 2U);
        CHECK(r.timeUs == 4000U);
    }
}

void testRateLimitTripsAndReArms()
{
    at(1000U);
    Rig rig(cfg(Edge::ACTIVATE, 0U, 3U, 10000U, 50000U));
    at(2000U);
    CHECK(rig.edge(true));
    at(2100U);
    CHECK(rig.edge(true));
    at(2200U);
    CHECK(rig.edge(true)); // three in the window: still fine
    Reading r = rig.reading();
    CHECK(r.quality == QUALITY_OK);
    at(2300U);
    CHECK(!rig.edge(true)); // the fourth inside 10 ms: disarm
    CHECK(rig.hw.armed[0] == Detect::NONE);
    r = rig.reading();
    CHECK(r.quality == QUALITY_ERROR);
    CHECK(r.count == 3U); // the tripping edge is not counted
    CHECK(r.timeUs == 2200U);
    Statistics s = rig.statistics();
    CHECK(s.trips == 1U);
    CHECK(!s.armed);
    at(2300U + 20000U);
    s = rig.statistics();
    CHECK(s.reArmInUs == 30000U);

    // a dispatcher that calls anyway: nothing counted, still off
    at(22400U);
    CHECK(!rig.table.onEdge(0U, now32(), true));
    r = rig.reading();
    CHECK(r.count == 3U);
    s = rig.statistics();
    CHECK(s.trips == 1U);

    // the task side does not re-arm before the hold-off
    unsigned const armsBefore = rig.hw.arms[0];
    at(2300U + 49999U);
    rig.table.cycle();
    CHECK(rig.hw.arms[0] == armsBefore);
    CHECK(rig.reading().quality == QUALITY_ERROR);
    // ... and does after it, with the detector edges of the configuration
    at(2300U + 50000U);
    rig.table.cycle();
    CHECK(rig.hw.arms[0] == armsBefore + 1U);
    CHECK(rig.hw.armed[0] == Detect::ACTIVATE);
    r = rig.reading();
    CHECK(r.quality == QUALITY_OK); // armed again, the last accepted edge is still valid
    CHECK(r.count == 3U);
    s = rig.statistics();
    CHECK(s.armed);
    CHECK(s.reArmInUs == 0U);
    rig.table.cycle(); // nothing more to do
    CHECK(rig.hw.arms[0] == armsBefore + 1U);

    // fresh window after the re-arm: three accepted, the fourth trips again
    at(60000U);
    CHECK(rig.edge(true));
    at(60001U);
    CHECK(rig.edge(true));
    at(60002U);
    CHECK(rig.edge(true));
    at(60003U);
    CHECK(!rig.edge(true));
    r = rig.reading();
    CHECK(r.count == 6U);
    CHECK(r.quality == QUALITY_ERROR);
    CHECK(rig.statistics().trips == 2U);
}

void testReArmOpensFreshWindow()
{
    // hold-off shorter than the window: after the re-arm the old window must not count any more
    at(1000U);
    Rig rig(cfg(Edge::ACTIVATE, 0U, 3U, 100000U, 1000U));
    for (uint64_t t = 2000U; t < 2004U; ++t)
    {
        at(t);
        (void)rig.edge(true);
    }
    CHECK(rig.statistics().trips == 1U);
    at(2003U + 1000U);
    rig.table.cycle();
    CHECK(rig.hw.armed[0] == Detect::ACTIVATE);
    at(5000U);
    CHECK(rig.edge(true));
    at(5001U);
    CHECK(rig.edge(true));
    at(5002U);
    CHECK(rig.edge(true));
    at(5003U);
    CHECK(!rig.edge(true));
    CHECK(rig.reading().count == 6U);
    CHECK(rig.statistics().trips == 2U);
}

void testRateWindowEnds()
{
    at(1000U);
    Rig rig(cfg(Edge::BOTH, 0U, 3U, 10000U, 50000U));
    at(2000U);
    CHECK(rig.edge(true));
    at(3000U);
    CHECK(rig.edge(false));
    at(11999U);
    CHECK(rig.edge(true)); // third, the window from 2000 is still open
    at(12000U);
    CHECK(rig.edge(false)); // window from 2000 is over: a new one opens here
    at(12001U);
    CHECK(rig.edge(true));
    at(12002U);
    CHECK(rig.edge(false));
    at(12003U);
    CHECK(!rig.edge(true)); // fourth in the window from 12000
    CHECK(rig.reading().count == 6U);
}

void testRateLimitCountsGlitchesAndIgnoresReleases()
{
    // a storm of short spikes towards active trips although none of them is ever confirmed
    {
        at(1000U);
        Rig rig(cfg(Edge::ACTIVATE, 100U, 3U, 10000U, 50000U));
        bool keep = true;
        unsigned spikes = 0U;
        for (uint64_t t = 2000U; keep && (t < 9000U); t += 200U)
        {
            at(t);
            keep = rig.edge(true);
            if (keep)
            {
                at(t + 50U);
                keep = rig.edge(false);
            }
            ++spikes;
        }
        CHECK(!keep);
        CHECK(spikes == 4U);
        Reading const r = rig.reading();
        CHECK(r.count == 0U);
        CHECK(r.quality == QUALITY_ERROR);
        CHECK(rig.statistics().glitches == 3U);
    }
    // three clean presses with their releases in one window: the releases do not count
    {
        at(1000U);
        Rig rig(cfg(Edge::ACTIVATE, 100U, 3U, 10000U, 50000U));
        for (uint64_t t = 2000U; t < 5000U; t += 1000U)
        {
            at(t);
            CHECK(rig.edge(true));
            at(t + 500U);
            CHECK(rig.edge(false));
        }
        at(6000U);
        Reading const r = rig.reading();
        CHECK(r.count == 3U);
        CHECK(r.quality == QUALITY_OK);
    }
}

void testBounceStormTrips()
{
    at(1000U);
    Rig rig(cfg(Edge::ACTIVATE, 1000U, 10U, 100000U, 50000U));
    at(2000U);
    CHECK(rig.edge(true));
    bool level = false;
    for (uint32_t i = 1U; i <= MAX_BOUNCES; ++i)
    {
        at(2000U + (i * 10U));
        CHECK(rig.edge(level));
        level = !level;
    }
    at(2000U + ((MAX_BOUNCES + 1U) * 10U));
    CHECK(!rig.edge(level)); // one bounce more than a contact makes: a storm
    at(10000U);
    Reading const r = rig.reading();
    CHECK(r.count == 0U); // the provisional edge is not confirmed by a storm
    CHECK(r.quality == QUALITY_ERROR);
    Statistics const s = rig.statistics();
    CHECK(s.trips == 1U);
    CHECK(s.bounces == MAX_BOUNCES + 1U);
    // after the hold-off it counts again from the level it stands on
    rig.hw.levels[0] = true;
    at(2000U + 50000U + 400U);
    rig.table.cycle();
    CHECK(rig.hw.armed[0] == Detect::BOTH);
    at(60000U);
    CHECK(rig.edge(false)); // release: not counted
    at(70000U);
    CHECK(rig.edge(true));
    at(72000U);
    CHECK(rig.reading().count == 1U);
}

void testCounterWraps()
{
    at(1000U);
    Rig rig(cfg(Edge::ACTIVATE, 0U, 10U, 100000U, 100000U));
    PinEventTestAccess::setCount(rig.event, 0xFFFFFFFEU);
    at(2000U);
    CHECK(rig.edge(true));
    CHECK(rig.reading().count == 0xFFFFFFFFU);
    at(3000U);
    CHECK(rig.edge(true));
    Reading const r = rig.reading();
    CHECK(r.count == 0U); // defined wrap, the consumer counts differences
    CHECK(r.timeUs == 3000U);
    CHECK(r.quality == QUALITY_OK);
}

void testTimeWraps()
{
    uint64_t const wrap = 0x100000000ULL;
    {
        at(wrap - 0x100U);
        Rig rig(cfg(Edge::ACTIVATE, 1000U, 10U, 100000U, 100000U));
        at(wrap - 0x80U);
        CHECK(rig.edge(true));
        at(wrap + 0x10U);
        CHECK(rig.edge(false)); // bounce across the wrap: still inside the lock-out
        at(wrap + 0x20U);
        CHECK(rig.edge(true));
        at(wrap + 0x10U + 700U);
        CHECK(rig.reading().count == 0U);
        at(wrap - 0x80U + 1000U);
        Reading const r = rig.reading();
        CHECK(r.count == 1U);
        CHECK(r.timeUs == static_cast<uint32_t>(wrap - 0x80U));
        CHECK(rig.statistics().bounces == 2U);
    }
    {
        at(wrap - 0x100U);
        Rig rig(cfg(Edge::ACTIVATE, 0U, 3U, 10000U, 100000U));
        at(wrap - 0x10U);
        CHECK(rig.edge(true));
        at(wrap + 5U);
        CHECK(rig.edge(true));
        at(wrap + 100U);
        CHECK(rig.edge(true));
        at(wrap + 200U);
        CHECK(!rig.edge(true)); // the window from before the wrap is still open
        // hold-off across the wrap as well
        at(wrap + 200U + 99999U);
        rig.table.cycle();
        CHECK(rig.hw.armed[0] == Detect::NONE);
        at(wrap + 200U + 100000U);
        rig.table.cycle();
        CHECK(rig.hw.armed[0] == Detect::ACTIVATE);
    }
}

void testQuietLongerThanOneWrap()
{
    uint64_t const wrap = 0x100000000ULL;
    // a pending edge that nothing commits: read() keeps it confirmed, and an edge one wrap later is no bounce
    {
        at(1000U);
        Rig rig(cfg(Edge::ACTIVATE, 1000U, 10U, 100000U, 100000U));
        uint64_t const t0 = 2000U;
        at(t0);
        CHECK(rig.edge(true));
        bool stable = true;
        for (uint64_t t = t0 + 60000000U; t < t0 + wrap + 600000000U; t += 60000000U) // the task every 60 s
        {
            at(t);
            rig.table.cycle();
            Reading const r = rig.reading();
            stable = stable && (r.count == 1U) && (r.timeUs == 2000U) && (r.quality == QUALITY_OK);
        }
        CHECK(stable);
        at(t0 + wrap + 500U); // the wrapped time lies inside the old lock-out
        rig.table.cycle();
        CHECK(rig.reading().count == 1U); // still confirmed, although 500 us after the edge modulo 2^32
        CHECK(rig.edge(false)); // release: a new candidate, not a bounce
        Statistics s = rig.statistics();
        CHECK(s.bounces == 0U);
        CHECK(s.reading.count == 1U);
        at(t0 + wrap + 10000U);
        CHECK(rig.edge(true));
        at(t0 + wrap + 11000U);
        s = rig.statistics();
        CHECK(s.reading.count == 2U);
        CHECK(s.reading.timeUs == static_cast<uint32_t>(t0 + wrap + 10000U));
    }
    // an open rate window does not alias either
    {
        at(1000U);
        Rig rig(cfg(Edge::ACTIVATE, 0U, 1U, 10000U, 100000U));
        uint64_t const t0 = 2000U;
        at(t0);
        CHECK(rig.edge(true));
        for (uint64_t t = t0 + 60000000U; t < t0 + wrap; t += 60000000U)
        {
            at(t);
            rig.table.cycle();
        }
        at(t0 + wrap + 100U); // inside the old window if the time is only compared modulo 2^32
        CHECK(rig.edge(true));
        Reading const r = rig.reading();
        CHECK(r.count == 2U);
        CHECK(r.quality == QUALITY_OK);
    }
    // a cycle right behind an edge does not expire the young history: the window stays open
    {
        at(1000U);
        Rig rig(cfg(Edge::ACTIVATE, 0U, 1U, 10000U, 100000U));
        at(2000U);
        CHECK(rig.edge(true));
        rig.table.cycle();
        at(3000U);
        CHECK(!rig.edge(true));
    }
}

void testConsistentRead()
{
    at(1000U);
    Rig rig(cfg(Edge::BOTH, 0U, 100U, 1000000U, 100000U));
    at(2000U);
    CHECK(rig.edge(true));
    // one interrupt during the copy: the reader copies again and sees the edge (count and time as one pair)
    injection.events = &rig.table;
    injection.input  = 0U;
    injection.left   = 1U;
    injection.level  = false;
    Reading r{};
    CHECK(rig.table.read(0U, r));
    CHECK(r.count == 2U);
    CHECK(r.timeUs == 2007U);
    CHECK(injection.left == 0U);
    // an interrupt during every copy: the reader gives up after READ_ATTEMPTS and leaves the output alone
    injection.left = READ_ATTEMPTS;
    r              = Reading{11U, 22U, 33U};
    CHECK(!rig.table.read(0U, r));
    CHECK((r.count == 11U) && (r.timeUs == 22U) && (r.quality == 33U));
    CHECK(injection.left == 0U);
    // READ_ATTEMPTS - 1 interrupts: the last attempt succeeds with all of them
    injection.left = READ_ATTEMPTS - 1U;
    CHECK(rig.table.read(0U, r));
    CHECK(r.count == 2U + READ_ATTEMPTS + READ_ATTEMPTS - 1U);
    injection.events = nullptr;

    // a writer is inside (odd sequence, e.g. the task side re-arming, preempted by the console): no copy
    uint32_t const seq = PinEventTestAccess::seq(rig.event);
    PinEventTestAccess::setSeq(rig.event, seq + 1U);
    CHECK(!rig.table.read(0U, r));
    Statistics s{};
    CHECK(!rig.table.statistics(0U, s));
    PinEventTestAccess::setSeq(rig.event, seq + 2U);
    CHECK(rig.table.read(0U, r));
}

void testTable()
{
    at(1000U);
    PinEventConfig const configs[] = {
        {"DOOR", Edge::ACTIVATE, 2000U, 10U, 100000U, 100000U},
        {"PULSE", Edge::BOTH, 0U, 100U, 10000U, 1000U},
    };
    PinEvent inputs[] = {PinEvent(configs[0]), PinEvent(configs[1])};
    FakeHardware hw;
    hw.levels[1] = true;
    PinEvents table(inputs, 2U, hw, &clockUs);
    // not started: an edge is refused (disarm), reads say nothing was counted
    CHECK(!table.onEdge(0U, 1000U, true));
    Statistics s{};
    CHECK(table.statistics(0U, s));
    CHECK(!s.armed);
    CHECK(s.reading.quality == QUALITY_DEBOUNCE);

    table.start();
    CHECK(hw.armed[0] == Detect::BOTH);
    CHECK(hw.armed[1] == Detect::BOTH);
    CHECK(table.count() == 2U);
    CHECK(std::strcmp(table.config(1U).name, "PULSE") == 0);
    // an input the table does not have
    CHECK(!table.onEdge(2U, 1000U, true));
    Reading r{};
    CHECK(!table.read(2U, r));
    CHECK(!table.statistics(2U, s));
    // the inputs are independent
    at(2000U);
    CHECK(table.onEdge(1U, now32(), false));
    CHECK(table.read(1U, r));
    CHECK(r.count == 1U);
    CHECK(table.read(0U, r));
    CHECK(r.count == 0U);
}

// ----------------------------------------------------------------------------------------------------- console
struct TextOut
{
    std::string text;

    int printf(char const* format, ...)
    {
        char line[256];
        va_list args;
        va_start(args, format);
        int const n = std::vsnprintf(line, sizeof(line), format, args);
        va_end(args);
        text += line;
        return n;
    }
};

bool contains(std::string const& text, char const* part) { return text.find(part) != std::string::npos; }

void testConsole()
{
    at(1000U);
    PinEventConfig const configs[] = {
        {"DOOR", Edge::ACTIVATE, 2000U, 10U, 100000U, 100000U},
        {"PULSE", Edge::BOTH, 0U, 2U, 10000U, 250000U},
    };
    PinEvent inputs[] = {PinEvent(configs[0]), PinEvent(configs[1])};
    FakeHardware hw;
    PinEvents table(inputs, 2U, hw, &clockUs);
    table.start();
    at(5000U);
    CHECK(table.onEdge(0U, now32(), true));
    CHECK(table.onEdge(1U, now32(), true));
    CHECK(table.onEdge(1U, now32() + 1U, false));
    CHECK(!table.onEdge(1U, now32() + 2U, true));
    at(10000U);
    TextOut out;
    console::writeShow(out, table);
    CHECK(contains(out.text, "pin events: 2\r\n"));
    CHECK(contains(out.text, "DOOR: count 1, last 5000 us, qlf OK (1), trips 0, bounces 0, glitches 0, armed\r\n"));
    CHECK(contains(out.text,
                   "PULSE: count 2, last 5001 us, qlf ERROR (16), trips 1, bounces 0, glitches 0, off, re-arm in "
                   "246 ms\r\n"));

    TextOut config;
    console::writeConfig(config, table);
    CHECK(contains(config.text,
                   "DOOR: edge activate, debounce 2000 us, limit 10 per 100000 us, re-arm after 100000 us, "
                   "detector both\r\n"));
    CHECK(contains(config.text, "PULSE: edge both, debounce 0 us, limit 2 per 10000 us, re-arm after 250000 us, "
                                "detector both\r\n"));

    // busy: an interrupt during every copy
    injection.events = &table;
    injection.input  = 0U;
    injection.left   = READ_ATTEMPTS;
    TextOut busy;
    console::writeShow(busy, table);
    injection.events = nullptr;
    CHECK(contains(busy.text, "DOOR: busy"));
    CHECK(contains(busy.text, "PULSE: count 2"));

    CHECK(std::strcmp(console::qualityName(QUALITY_DEBOUNCE), "DEBOUNCE") == 0);
    CHECK(std::strcmp(console::detectName(Detect::NONE), "none") == 0);
    CHECK(std::strcmp(console::detectName(Detect::DEACTIVATE), "deactivate") == 0);
}

} // namespace

int main()
{
    testConfig();
    testStartAndCountWithoutDebounce();
    testDebounceConfirmsAfterLockOut();
    testGlitchIsTakenBack();
    testEdgeFilter();
    testRateLimitTripsAndReArms();
    testReArmOpensFreshWindow();
    testRateWindowEnds();
    testRateLimitCountsGlitchesAndIgnoresReleases();
    testBounceStormTrips();
    testCounterWraps();
    testTimeWraps();
    testQuietLongerThanOneWrap();
    testConsistentRead();
    testTable();
    testConsole();
    std::printf("PinEventHostTest: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
