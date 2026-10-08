// Host test of hallDecoder/HallDecoder.h/.cpp (IO-07): the hardware independent Hall evaluation, without any MCU,
// register or generator. The test drives sampled states and its own microsecond time, so every case - including
// the 32-bit wrap - is exact.
//
// Configurations: both arrangements (120 and 60 degrees), both direction signs, 1, 2, 4 and 8 pole pairs,
// debouncing 0, 20 and 50 us. Checked per configuration: a full electrical revolution forwards and backwards
// (sector, state, period electrical/mechanical, speed, direction), a direction change, both invalid states, a
// jumped sector, bouncing, a glitch that ends on the old level (forwards and backwards, confirmed at the next edge
// and in read()), a glitch into an invalid state, a glitch followed by a real edge inside the window, standstill
// (latch, counted once, restart after exactly seven edges), no sensor at all, the wrap of the time base.
// Build and run: tools/bswgen/test_bswgen.py (HallDecoderHostTest) or tools/test (host group).

#include "hallDecoder/HallDecoder.h"

#include <cstdio>
#include <initializer_list>

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

using namespace hallDecoder;

// state -> sector of the documented sequences (tools/bswgen/bswgen.py HALL_SEQUENCES)
//   deg120: 100, 110, 010, 011, 001, 101    deg60: 111, 110, 100, 000, 001, 011
#define DEG120 {0U, 5U, 3U, 4U, 1U, 6U, 2U, 0U}
#define DEG60  {4U, 5U, 0U, 6U, 3U, 0U, 2U, 1U}

HallDecoderConfig const CONFIGS[] = {
    {DEG120, 1, 1U, 50U, 300000U},  // one pole pair, 50 us debouncing
    {DEG60, -1, 8U, 20U, 500000U},  // 60 degrees, sector_down, 8 pole pairs
    {DEG120, 1, 4U, 20U, 200000U},  // the eval board probe build values
    {DEG120, -1, 2U, 0U, 100000U},  // debouncing switched off
};
constexpr unsigned CONFIG_COUNT = sizeof(CONFIGS) / sizeof(CONFIGS[0]);

constexpr uint8_t STEPS = SECTORS;
// After start() the first edge only sets the start of the run, so a full revolution needs one edge more.
constexpr unsigned EDGES_TO_OK = STEPS + 1U;

struct Rig
{
    explicit Rig(HallDecoderConfig const& c) : cfg(c), decoder(c) {}

    HallDecoderConfig const& cfg;
    HallDecoder decoder;
    uint32_t now   = 1000U;
    uint8_t sector = 1U;

    uint8_t stateOf(uint8_t const s) const
    {
        for (uint8_t state = 0U; state < 8U; ++state)
        {
            if (cfg.sectors[state] == s)
            {
                return state;
            }
        }
        return 0xFFU;
    }

    uint8_t invalidState(unsigned const which) const
    {
        unsigned seen = 0U;
        for (uint8_t state = 0U; state < 8U; ++state)
        {
            if (cfg.sectors[state] == 0U)
            {
                if (seen == which)
                {
                    return state;
                }
                ++seen;
            }
        }
        return 0xFFU;
    }

    uint8_t level = 0U; // state of the pins right now

    void start(uint8_t const s)
    {
        sector = s;
        level  = stateOf(s);
        decoder.start(level, now);
    }

    void set(uint8_t const state)
    {
        level = state;
        decoder.edge(state, now);
    }

    void advance(uint32_t const us) { now += us; }

    Reading read() { return decoder.read(level, now); }

    uint32_t step() const
    {
        uint32_t s = cfg.standstillUs / 10U;
        return (s <= (2U * cfg.debounceUs)) ? (2U * cfg.debounceUs + 1U) : s;
    }

    static uint8_t next(uint8_t const s) { return (s >= STEPS) ? 1U : static_cast<uint8_t>(s + 1U); }

    static uint8_t prev(uint8_t const s) { return (s <= 1U) ? STEPS : static_cast<uint8_t>(s - 1U); }

    /** One step forward (up the sequence) or backward after `us`. */
    void turn(bool const up, uint32_t const us)
    {
        advance(us);
        sector = up ? next(sector) : prev(sector);
        set(stateOf(sector));
    }

    /** Runs until the quality is OK (a full revolution up the sequence). */
    void spinUp()
    {
        for (unsigned e = 0U; e < EDGES_TO_OK; ++e)
        {
            turn(true, step());
        }
    }

    uint32_t speedOf(uint32_t const period) const
    {
        uint64_t const divisor = static_cast<uint64_t>(period) * cfg.polePairs;
        return static_cast<uint32_t>((60000000ULL + (divisor / 2U)) / divisor);
    }
};

void testConfigChecks()
{
    for (auto const& c : CONFIGS)
    {
        CHECK(isValidSectorTable(c));
        CHECK(isValidTiming(c));
    }
    HallDecoderConfig bad = CONFIGS[0];
    bad.sectors[6]        = 5U; // a sector twice
    CHECK(!isValidSectorTable(bad));
    bad            = CONFIGS[0];
    bad.sectors[6] = 0U; // only five valid states
    CHECK(!isValidSectorTable(bad));
    bad             = CONFIGS[0];
    bad.forwardSign = 0;
    CHECK(!isValidSectorTable(bad));
    bad           = CONFIGS[0];
    bad.polePairs = 0U;
    CHECK(!isValidSectorTable(bad));
    bad              = CONFIGS[0];
    bad.standstillUs = 400000000U; // six of them do not fit half a wrap
    CHECK(!isValidTiming(bad));
    bad            = CONFIGS[0];
    bad.debounceUs = bad.standstillUs;
    CHECK(!isValidTiming(bad));
    Reading const none = noReading();
    CHECK((none.quality == QUALITY_DEBOUNCE) && (none.sector == 0U) && (none.period_us == 0U));
}

void testStart(HallDecoderConfig const& cfg)
{
    Rig rig(cfg);
    rig.start(3U);
    Reading const r = rig.read();
    CHECK(r.quality == QUALITY_DEBOUNCE); // no edge yet, but no fault either
    CHECK(r.sector == 3U);                // the position is the sampled one
    CHECK(r.state == rig.stateOf(3U));
    CHECK((r.period_us == 0U) && (r.speed_rpm == 0U) && (r.mech_period_us == 0U));
    CHECK(r.direction == DIRECTION_UNKNOWN);
    Statistics const s = rig.decoder.statistics();
    CHECK((s.edges == 0U) && (s.bounces == 0U) && (s.invalidStates == 0U) && (s.sectorJumps == 0U)
          && (s.glitches == 0U) && (s.standstills == 0U));
}

void testRevolution(HallDecoderConfig const& cfg, bool const up)
{
    Rig rig(cfg);
    rig.start(1U);
    uint32_t const step = rig.step();
    int8_t const expect = up ? cfg.forwardSign : static_cast<int8_t>(-cfg.forwardSign);
    for (unsigned edge = 1U; edge <= (2U * EDGES_TO_OK); ++edge)
    {
        rig.turn(up, step);
        Reading const r = rig.read();
        CHECK(r.sector == rig.sector);
        CHECK(r.state == rig.stateOf(rig.sector));
        if (edge < EDGES_TO_OK)
        {
            CHECK(r.quality == QUALITY_DEBOUNCE); // no full revolution yet: no period, and it is said
            CHECK(r.period_us == 0U);
        }
        else
        {
            CHECK(r.quality == QUALITY_OK);
            CHECK(r.period_us == STEPS * step);
            CHECK(r.mech_period_us == STEPS * step * cfg.polePairs);
            CHECK(r.speed_rpm == rig.speedOf(STEPS * step));
        }
        if (edge >= 2U)
        {
            CHECK(r.direction == expect); // from the transition, from the second edge on
        }
    }
    Statistics const s = rig.decoder.statistics();
    CHECK(s.edges == (2U * EDGES_TO_OK));
    CHECK((s.bounces == 0U) && (s.invalidStates == 0U) && (s.sectorJumps == 0U) && (s.glitches == 0U));
}

void testDirectionChange(HallDecoderConfig const& cfg)
{
    Rig rig(cfg);
    rig.start(1U);
    uint32_t const step = rig.step();
    rig.spinUp();
    CHECK(rig.read().quality == QUALITY_OK);
    rig.turn(false, step);
    Reading const turned = rig.read();
    CHECK(turned.direction == static_cast<int8_t>(-cfg.forwardSign));
    CHECK(turned.quality == QUALITY_DEBOUNCE);
    for (unsigned edge = 1U; edge < (STEPS - 1U); ++edge)
    {
        rig.turn(false, step);
        CHECK(rig.read().quality == QUALITY_DEBOUNCE);
    }
    rig.turn(false, step);
    Reading const back = rig.read();
    CHECK(back.quality == QUALITY_OK);
    CHECK(back.period_us == STEPS * step);
    CHECK(back.direction == static_cast<int8_t>(-cfg.forwardSign));
}

void testInvalidState(HallDecoderConfig const& cfg)
{
    Rig rig(cfg);
    rig.start(1U);
    uint32_t const step = rig.step();
    rig.spinUp();
    Reading const good = rig.read();
    CHECK(good.quality == QUALITY_OK);
    for (unsigned which = 0U; which < 2U; ++which)
    {
        uint8_t const bad = rig.invalidState(which);
        CHECK(bad != 0xFFU);
        rig.advance(step);
        rig.set(bad);
        Reading const r = rig.read();
        CHECK(r.quality == QUALITY_ERROR);
        CHECK(r.state == bad);
        CHECK(r.sector == 0U);
        CHECK(r.period_us == good.period_us);
        CHECK(rig.decoder.statistics().invalidStates == (which + 1U));
        rig.turn(true, step);
        CHECK(rig.read().quality == QUALITY_ERROR); // still a fault until a clean revolution
        CHECK(rig.read().sector == rig.sector);
        CHECK(rig.decoder.statistics().sectorJumps == 0U); // an invalid state is not a jump
    }
    rig.spinUp();
    CHECK(rig.read().quality == QUALITY_OK);
}

void testSectorJump(HallDecoderConfig const& cfg)
{
    Rig rig(cfg);
    rig.start(1U);
    uint32_t const step = rig.step();
    rig.spinUp();
    rig.advance(step);
    rig.sector = Rig::next(Rig::next(rig.sector)); // one edge lost
    rig.set(rig.stateOf(rig.sector));
    Reading const r = rig.read();
    CHECK(r.quality == QUALITY_ERROR);
    CHECK(r.sector == rig.sector);
    CHECK(rig.decoder.statistics().sectorJumps == 1U);
    rig.spinUp();
    CHECK(rig.read().quality == QUALITY_OK);
    CHECK(rig.read().period_us == STEPS * step);
}

/** Chatter around a transition that ends on the new level: counted, ignored, the step stands. */
void testBouncing(HallDecoderConfig const& cfg)
{
    // four chatter edges a fifth of the window apart: the last one still lies inside the window. An edge at or
    // after debounceUs is no bounce any more but decides the window (see testChatterLongerThanTheWindow).
    uint32_t const quarter = cfg.debounceUs / 5U;
    Rig rig(cfg);
    uint32_t const step = rig.step();
    if (quarter == 0U)
    {
        return; // debouncing switched off: nothing to check
    }
    rig.start(1U);
    for (unsigned edge = 0U; edge < (2U * EDGES_TO_OK); ++edge)
    {
        rig.advance(step - (4U * quarter));
        uint8_t const previous = rig.sector;
        rig.sector             = Rig::next(rig.sector);
        rig.set(rig.stateOf(rig.sector));
        for (unsigned k = 0U; k < 2U; ++k)
        {
            rig.advance(quarter);
            rig.set(rig.stateOf(previous));
            rig.advance(quarter);
            rig.set(rig.stateOf(rig.sector));
        }
    }
    Reading const r    = rig.read();
    Statistics const s = rig.decoder.statistics();
    CHECK(s.bounces == (4U * EDGES_TO_OK));
    CHECK((s.sectorJumps == 0U) && (s.invalidStates == 0U) && (s.glitches == 0U));
    CHECK(s.edges == (2U * EDGES_TO_OK));
    CHECK(r.quality == QUALITY_OK);
    CHECK(r.period_us == STEPS * step);
    CHECK(r.sector == rig.sector);
}

/**
 * A glitch in the middle of a sector that ends on the old level (review IO-07): the accepted step did not happen.
 * `up` chooses the direction of the glitch, `byRead` whether read() or the next real edge ends the window.
 */
void testGlitchBackToOldLevel(HallDecoderConfig const& cfg, bool const up, bool const byRead)
{
    if (cfg.debounceUs < 4U)
    {
        return; // without a debounce window every change is an edge
    }
    Rig rig(cfg);
    uint32_t const step  = rig.step();
    uint32_t const quart = cfg.debounceUs / 4U;
    rig.start(1U);
    rig.spinUp();
    Statistics const before = rig.decoder.statistics();
    CHECK(rig.read().quality == QUALITY_OK);
    uint8_t const stay = rig.sector;
    // half a sector later the level jumps to a neighbour and comes back within the debounce window
    rig.advance(step / 2U);
    rig.set(rig.stateOf(up ? Rig::next(stay) : Rig::prev(stay)));
    rig.advance(quart);
    rig.set(rig.stateOf(stay));
    if (byRead)
    {
        rig.advance(cfg.debounceUs); // window over, no further edge: read() decides
        Reading const r = rig.read();
        CHECK(r.sector == stay);
        CHECK(r.quality == QUALITY_OK);                // no fault, the run goes on
        CHECK(r.direction == cfg.forwardSign);         // not turned around by the glitch
        CHECK(r.period_us == STEPS * step);
        rig.advance(step - (step / 2U) - quart - cfg.debounceUs);
    }
    else
    {
        rig.advance(step - (step / 2U) - quart); // the next real edge ends the window
    }
    rig.turn(true, 0U);
    Reading const r    = rig.read();
    Statistics const s = rig.decoder.statistics();
    CHECK(r.quality == QUALITY_OK);
    CHECK(r.sector == rig.sector);
    CHECK(r.direction == cfg.forwardSign);
    CHECK(r.period_us == STEPS * step); // the stamps of the glitch are gone: the period is exact
    CHECK(s.sectorJumps == before.sectorJumps);
    CHECK(s.glitches == (before.glitches + 1U));
    CHECK(s.edges == (before.edges + 1U)); // only the real edge stood
    CHECK(s.bounces == (before.bounces + 1U));
    // and it keeps running cleanly
    for (unsigned e = 0U; e < EDGES_TO_OK; ++e)
    {
        rig.turn(true, step);
    }
    CHECK(rig.read().quality == QUALITY_OK);
    CHECK(rig.read().period_us == STEPS * step);
    CHECK(rig.decoder.statistics().sectorJumps == 0U);
}

/**
 * Chatter that lasts until the window is over and whose last level inside the window was the old one: the level
 * at the end of the window decides, so the step is taken back and the edge that ends the chatter is the step -
 * later by the chatter time, but no jump and no lost step.
 */
void testChatterLongerThanTheWindow(HallDecoderConfig const& cfg)
{
    if (cfg.debounceUs < 4U)
    {
        return;
    }
    Rig rig(cfg);
    uint32_t const step = rig.step();
    uint32_t const half = cfg.debounceUs / 2U;
    rig.start(1U);
    rig.spinUp();
    uint8_t const previous = rig.sector;
    rig.turn(true, step);
    rig.advance(half);
    rig.set(rig.stateOf(previous)); // inside the window, back at the old level
    rig.advance(cfg.debounceUs);
    rig.set(rig.stateOf(rig.sector)); // after the window: this is the step
    Reading const r    = rig.read();
    Statistics const s = rig.decoder.statistics();
    CHECK(s.glitches == 1U);
    CHECK(s.sectorJumps == 0U);
    CHECK(r.sector == rig.sector);
    CHECK(r.quality == QUALITY_OK);
    CHECK(r.period_us == (STEPS * step + half + cfg.debounceUs)); // the step counts from the edge that stood
}

/** A glitch into an invalid state and back is no fault. */
void testGlitchThroughInvalidState(HallDecoderConfig const& cfg)
{
    if (cfg.debounceUs < 4U)
    {
        return;
    }
    Rig rig(cfg);
    uint32_t const step = rig.step();
    rig.start(1U);
    rig.spinUp();
    rig.advance(step / 2U);
    rig.set(rig.invalidState(0U));
    rig.advance(cfg.debounceUs / 4U);
    rig.set(rig.stateOf(rig.sector));
    rig.advance(cfg.debounceUs);
    Reading const r = rig.read();
    CHECK(r.quality == QUALITY_OK);
    CHECK(rig.decoder.statistics().invalidStates == 0U); // taken back with the step
    CHECK(rig.decoder.statistics().glitches == 1U);
}

/** The window ends on a third state: a glitch backwards with the real edge forwards right behind it. */
void testGlitchThenRealEdgeInsideWindow(HallDecoderConfig const& cfg)
{
    if (cfg.debounceUs < 4U)
    {
        return;
    }
    Rig rig(cfg);
    uint32_t const step = rig.step();
    uint32_t const half = cfg.debounceUs / 2U;
    rig.start(1U);
    rig.spinUp();
    uint8_t const stay = rig.sector;
    rig.advance(step - half);
    rig.set(rig.stateOf(Rig::prev(stay))); // glitch backwards, accepted
    rig.advance(half);
    rig.sector = Rig::next(stay);
    rig.set(rig.stateOf(rig.sector)); // the real edge, inside the window: a bounce
    rig.advance(cfg.debounceUs);
    Reading const r = rig.read();
    CHECK(r.sector == rig.sector);
    CHECK(r.direction == cfg.forwardSign);
    CHECK(rig.decoder.statistics().sectorJumps == 0U);
    CHECK(rig.decoder.statistics().glitches == 1U);
    CHECK(r.quality == QUALITY_OK);
    // the following steps continue without a jump
    rig.turn(true, step);
    CHECK(rig.read().quality == QUALITY_OK);
    CHECK(rig.decoder.statistics().sectorJumps == 0U);
}

void testStandstill(HallDecoderConfig const& cfg)
{
    Rig rig(cfg);
    uint32_t const step = rig.step();
    rig.start(1U);
    rig.spinUp();
    Reading const good = rig.read();
    CHECK(good.quality == QUALITY_OK);
    rig.advance(cfg.standstillUs + 1U);
    Reading const stopped = rig.read();
    CHECK(stopped.quality == QUALITY_TIMEOUT);
    CHECK(stopped.sector == rig.sector);
    CHECK(stopped.period_us == good.period_us);
    CHECK(stopped.direction == DIRECTION_UNKNOWN);
    CHECK(rig.decoder.statistics().standstills == 1U);
    rig.advance(10U * cfg.standstillUs);
    CHECK(rig.read().quality == QUALITY_TIMEOUT);
    CHECK(rig.decoder.statistics().standstills == 1U);
    for (unsigned edge = 1U; edge <= EDGES_TO_OK; ++edge)
    {
        rig.turn(true, step);
        Reading const r = rig.read();
        if (edge < EDGES_TO_OK)
        {
            CHECK(r.quality == QUALITY_DEBOUNCE);
            CHECK(r.period_us == good.period_us);
        }
        else
        {
            CHECK(r.quality == QUALITY_OK);
            CHECK(r.period_us == STEPS * step); // never a period that contains the standstill
        }
    }
}

void testWithoutSensor(HallDecoderConfig const& cfg)
{
    for (uint8_t const state : {uint8_t{0U}, uint8_t{7U}})
    {
        Rig rig(cfg);
        rig.now   = 5U;
        rig.level = state;
        rig.decoder.start(state, rig.now);
        rig.advance(cfg.standstillUs + 1U);
        Reading const r = rig.read();
        CHECK(r.quality == QUALITY_TIMEOUT);
        CHECK(r.state == state);
        CHECK((r.period_us == 0U) && (r.speed_rpm == 0U));
        CHECK(r.direction == DIRECTION_UNKNOWN);
        CHECK(r.sector == cfg.sectors[state]);
    }
}

void testTimeOverflow(HallDecoderConfig const& cfg)
{
    Rig rig(cfg);
    uint32_t const step = rig.step();
    rig.now             = 0xFFFFFFFFU - (3U * step);
    rig.start(1U);
    for (unsigned edge = 0U; edge < (2U * EDGES_TO_OK); ++edge)
    {
        rig.turn(true, step);
    }
    Reading const r = rig.read();
    CHECK(r.quality == QUALITY_OK);
    CHECK(r.period_us == STEPS * step);
    CHECK(r.speed_rpm == rig.speedOf(STEPS * step));
    rig.advance(cfg.standstillUs + 1U);
    CHECK(rig.read().quality == QUALITY_TIMEOUT);
    uint64_t waited = 0U;
    while (waited <= 0x100000000ULL)
    {
        rig.advance(cfg.standstillUs);
        waited += cfg.standstillUs;
        if (rig.read().quality != QUALITY_TIMEOUT)
        {
            CHECK(false); // a wrapping counter made the last edge look fresh
            break;
        }
    }
    CHECK(rig.read().quality == QUALITY_TIMEOUT);
}

/** The position of a reading is the state sampled for it, not the last accepted edge (a lost interrupt). */
void testReadUsesTheSampledState(HallDecoderConfig const& cfg)
{
    Rig rig(cfg);
    rig.start(1U);
    rig.level = rig.stateOf(4U); // the pins moved without an edge() call
    Reading const r = rig.read();
    CHECK(r.sector == 4U);
    CHECK(r.state == rig.stateOf(4U));
}

/** An edge() call without a change (another sensor on the same port) changes nothing. */
void testEdgeWithoutChange(HallDecoderConfig const& cfg)
{
    Rig rig(cfg);
    rig.start(2U);
    rig.advance(rig.step());
    rig.set(rig.stateOf(2U));
    CHECK(rig.decoder.statistics().edges == 0U);
    CHECK(rig.read().quality == QUALITY_DEBOUNCE);
}
} // namespace

int main()
{
    testConfigChecks();
    for (auto const& cfg : CONFIGS)
    {
        testStart(cfg);
        testRevolution(cfg, true);
        testRevolution(cfg, false);
        testDirectionChange(cfg);
        testInvalidState(cfg);
        testSectorJump(cfg);
        testBouncing(cfg);
        for (bool const up : {true, false})
        {
            testGlitchBackToOldLevel(cfg, up, false);
            testGlitchBackToOldLevel(cfg, up, true);
        }
        testChatterLongerThanTheWindow(cfg);
        testGlitchThroughInvalidState(cfg);
        testGlitchThenRealEdgeInsideWindow(cfg);
        testStandstill(cfg);
        testWithoutSensor(cfg);
        testTimeOverflow(cfg);
        testEdgeWithoutChange(cfg);
        testReadUsesTheSampledState(cfg);
    }
    std::printf("hall_decoder_host_test: %u configuration(s), %u checks, %u failures\n", CONFIG_COUNT, checks, failures);
    return (failures == 0U) ? 0 : 1;
}
