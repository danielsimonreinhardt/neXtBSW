/* Host test of the behaviour after bus off (COM-35): log series, doubling pause, dropping the pending frames.
 *
 * Board finding 2026-09-22 (COM-26 P9, CANH shorted to CANL on CAN1, captures 88-92): a frame hung in a transmit
 * buffer, the controller recovered by itself and went bus off again every 30-120 ms, 9812 times in about ten
 * minutes, with one ERROR and one INFO line each; the frame was never given up.
 *
 * The test runs that fault in a model of one FlexCAN controller (time step 100 us, 500 kbit/s): with a pending
 * frame and the short the controller is bus off 2 ms after joining the bus (32 failed attempts, TEC > 255), it
 * leaves bus off after 128 x 11 recessive bits (2.8 ms) by itself (CTRL1[BOFFREC] = 0) or, held (BOFFREC = 1),
 * when released - as the reference manual describes BOFFREC, including "reasserted during bus off, effective only
 * the next time". ESR1[BOFFINT] latches every entry. On top runs the same sequence as
 * CanSystem::Channel::updateBusState() every 10 ms: read FLTCONF and BOFFINT, clear BOFFINT, feed the monitor,
 * log after CanBusOffRecovery, drop the pending frames, release.
 *
 * Counter-checks (must fail the expectations of the fixed behaviour): the old behaviour (no hold, no drop, a line
 * per edge) reproduces the flood of the board; the hold without the BOFFINT reading loses the bus offs after a
 * release (the pause never grows).
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/canSystem/include libs/bsw/canSystem/test/host/CanBusOffRecoveryHostTest.cpp \
 *       -o build/host/can_bus_off_recovery_test.exe
 *   build/host/can_bus_off_recovery_test.exe
 */

#include "can/CanBusOffRecovery.h"

#include <cstdio>

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

using ::can::CanBusMonitor;
using ::can::CanBusOffRecovery;
using ::can::CanBusState;
using Event = CanBusMonitor::Event;

constexpr uint32_t STEP_US        = 100U;
constexpr uint32_t CYCLE_MS       = 10U;     // CanSupervisionCycle::BUS_STATE_CYCLE_MS
constexpr uint32_t TO_BUS_OFF_US  = 2000U;   // 32 attempts with bit error and error flag at 500 kbit/s
constexpr uint32_t RECOVERY_US    = 2816U;   // 128 x 11 recessive bits at 500 kbit/s
constexpr uint32_t REJOIN_US      = 22U;     // 11 recessive bits, when released after the 128 x 11
constexpr uint32_t TEN_MINUTES_MS = 600000U;

/* ------------------------------------------------------------------ model of one FlexCAN controller */

class FlexCanModel
{
public:
    // environment
    bool shorted          = false; // CANH-CANL: every own frame fails, the bus reads recessive
    bool ignoresRelease   = false; // a controller that does not react to the first release (robustness)
    uint32_t pendingFrames = 0U;   // frames in the transmit buffers
    // registers
    bool boffrec = false; // CTRL1[BOFFREC]
    bool boffint = false; // ESR1[BOFFINT], latched

    // counters of the model (the truth)
    uint32_t busOffEntries = 0U;
    uint32_t framesSent    = 0U;
    uint32_t releases      = 0U;

    CanBusState state() const { return _busOff ? CanBusState::BUS_OFF : CanBusState::ERROR_ACTIVE; }

    void step()
    {
        if (_busOff)
        {
            _offUs += STEP_US;
            bool const mayLeave = !_heldThisTime || _released;
            uint32_t const needUs
                = (_heldThisTime && _released && (_releasedAtUs >= RECOVERY_US)) ? (_releasedAtUs + REJOIN_US)
                                                                                  : RECOVERY_US;
            if (mayLeave && (_offUs >= needUs))
            {
                _busOff = false;
                _tryUs  = 0U;
            }
            return;
        }
        if (pendingFrames == 0U)
        {
            _tryUs = 0U;
            return;
        }
        if (!shorted)
        {
            // the frame goes out (a second node acknowledges)
            --pendingFrames;
            ++framesSent;
            _tryUs = 0U;
            return;
        }
        _tryUs += STEP_US;
        if (_tryUs >= TO_BUS_OFF_US)
        {
            _busOff       = true;
            _offUs        = 0U;
            _released     = false;
            _heldThisTime = boffrec; // BOFFREC as it is when the controller enters bus off
            boffint       = true;
            ++busOffEntries;
        }
    }

    /** CTRL1[BOFFREC] negated and asserted again (releaseBusOff() of CanSystem.cpp). */
    void release()
    {
        ++releases;
        if (ignoresRelease)
        {
            ignoresRelease = false;
            return;
        }
        if (_busOff && _heldThisTime && !_released)
        {
            _released     = true;
            _releasedAtUs = _offUs;
        }
        boffrec = true;
    }

    /** CanFlex2Transceiver::mute() + unmute(): every transmit buffer inactive. */
    void dropPendingFrames() { pendingFrames = 0U; }

private:
    bool _busOff          = false;
    bool _heldThisTime    = false;
    bool _released        = false;
    uint32_t _offUs       = 0U;
    uint32_t _releasedAtUs = 0U;
    uint32_t _tryUs       = 0U;
};

/* ------------------------------------------------------------------ the sequence of updateBusState() */

struct Lines
{
    uint32_t busOff    = 0U; // ERROR "... bus off (n times) ..."
    uint32_t recovered = 0U; // INFO  "... back on the bus ..."
    uint32_t passive   = 0U;
    uint32_t dropped   = 0U; // WARN  "... pending frames dropped ..."
    uint32_t summary   = 0U; // WARN  "... more bus offs in ..."
    uint32_t seriesEnd = 0U; // INFO  "... bus off series over ..."

    uint32_t total() const { return busOff + recovered + passive + dropped + summary + seriesEnd; }
};

class Channel
{
public:
    explicit Channel(CanBusOffRecovery::Config const& config, bool readBoffint = true)
    : recovery(config), _readBoffint(readBoffint)
    {
        if (recovery.holdsRecovery())
        {
            controller.boffrec = true; // start(): holdBusOffRecovery()
        }
    }

    FlexCanModel controller;
    CanBusMonitor monitor;
    CanBusOffRecovery recovery;
    Lines lines;
    uint32_t drops = 0U;

    /** Old behaviour before COM-35: one line per edge, no hold, no drop. */
    bool oldBehaviour = false;

    void supervise(uint32_t const elapsedMs)
    {
        CanBusState const state = controller.state();
        bool entered            = false;
        if (_readBoffint && controller.boffint)
        {
            controller.boffint = false; // reg->ESR1 = BOFFINT
            entered            = true;
        }
        Event events[2];
        uint8_t const count = CanBusOffRecovery::feedMonitor(monitor, state, entered, elapsedMs, events);
        for (uint8_t i = 0U; i < count; ++i)
        {
            switch (events[i])
            {
                case Event::WENT_BUS_OFF:
                    if (recovery.wentBusOff() || oldBehaviour)
                    {
                        ++lines.busOff;
                    }
                    break;
                case Event::RECOVERED:
                    if (recovery.recovered() || oldBehaviour)
                    {
                        ++lines.recovered;
                    }
                    break;
                case Event::WENT_PASSIVE:
                    if (!recovery.inSeries() || oldBehaviour)
                    {
                        ++lines.passive;
                    }
                    break;
                case Event::STOPPED: recovery.stop(); break;
                default: break;
            }
        }
        CanBusOffRecovery::Step const step = recovery.cycle(monitor.isBusOff(), elapsedMs);
        if (oldBehaviour)
        {
            return;
        }
        if (step.discard)
        {
            controller.dropPendingFrames();
            ++drops;
        }
        lines.dropped += step.logDiscard ? 1U : 0U;
        if (step.release)
        {
            controller.release();
        }
        lines.summary += step.logSummary ? 1U : 0U;
        lines.seriesEnd += step.logSeriesEnd ? 1U : 0U;
    }

    /** Runs the controller for \p ms, supervision every CYCLE_MS; \p sendEveryMs > 0: one frame per period. */
    void run(uint32_t const ms, uint32_t const sendEveryMs = 0U)
    {
        for (uint32_t t = 0U; t < ms * 1000U; t += STEP_US)
        {
            _nowUs += STEP_US;
            if ((sendEveryMs > 0U) && ((_nowUs % (sendEveryMs * 1000U)) == 0U))
            {
                ++controller.pendingFrames; // the RTE writes its periodic frame (a buffer per id)
                if (controller.pendingFrames > 1U)
                {
                    controller.pendingFrames = 1U; // same id: the driver reuses the buffer
                }
            }
            controller.step();
            if ((_nowUs % (CYCLE_MS * 1000U)) == 0U)
            {
                supervise(CYCLE_MS);
            }
        }
    }

private:
    bool _readBoffint;
    uint64_t _nowUs = 0U;
};

constexpr CanBusOffRecovery::Config OLD_CONFIG = {0U, 0U, 0U, 2000U, 10000U};

/* ------------------------------------------------------------------ tests */

void testPauseSequence()
{
    std::printf("pause doubles from firstPauseMs up to maxPauseMs\n");
    CanBusOffRecovery const recovery;
    uint32_t const expected[] = {10U, 20U, 40U, 80U, 160U, 320U, 640U, 1000U, 1000U};
    for (uint32_t n = 1U; n <= 9U; ++n)
    {
        CHECK(recovery.pauseFor(n) == expected[n - 1U]);
    }
    CHECK(recovery.pauseFor(100000U) == 1000U); // no overflow for a long series
    CHECK(recovery.holdsRecovery());
    CanBusOffRecovery const automatic(OLD_CONFIG);
    CHECK(!automatic.holdsRecovery());
    CHECK(automatic.pauseFor(5U) == 0U);
}

void testBoardFindingOldBehaviour()
{
    std::printf("counter-check: behaviour before COM-35 floods the log like the board (9812 bus offs)\n");
    Channel bus(OLD_CONFIG, false); // before COM-35 BOFFINT was only shown in "can status"
    bus.oldBehaviour = true;
    bus.run(100U);
    bus.controller.shorted       = true;
    bus.controller.pendingFrames = 1U; // "can send CAN1 0x125 9"
    bus.run(TEN_MINUTES_MS);
    std::printf("  model: %u bus offs, %u log lines in 10 min\n",
                static_cast<unsigned>(bus.controller.busOffEntries), static_cast<unsigned>(bus.lines.total()));
    CHECK(bus.controller.busOffEntries > 9000U); // the frame is never given up
    CHECK(bus.controller.pendingFrames == 1U);
    CHECK(bus.lines.total() > 2U * 1000U);       // the flood of the board: a line per edge
}

void testHangingFrameIsDropped()
{
    std::printf("hanging frame with short, 10 min: dropped at the 3rd bus off, then quiet\n");
    Channel bus(CanBusOffRecovery::DEFAULT_CONFIG);
    bus.run(100U);
    bus.controller.shorted       = true;
    bus.controller.pendingFrames = 1U;
    bus.run(TEN_MINUTES_MS);
    std::printf("  model: %u bus offs, %u log lines, %u drops\n",
                static_cast<unsigned>(bus.controller.busOffEntries),
                static_cast<unsigned>(bus.lines.total()),
                static_cast<unsigned>(bus.drops));
    CHECK(bus.controller.busOffEntries == 3U);
    CHECK(bus.monitor.busOffCount() == 3U);       // the monitor saw every bus off
    CHECK(bus.monitor.recoveries() == 3U);
    CHECK(bus.controller.pendingFrames == 0U);
    CHECK(bus.drops == 1U);
    CHECK(bus.lines.busOff == 1U);                // first bus off only...
    CHECK(bus.lines.recovered == 1U);             // ...and its recovery (seen through BOFFINT)
    CHECK(bus.lines.dropped == 1U);
    CHECK(bus.lines.seriesEnd == 1U);             // the end of the series with its counters
    CHECK(bus.recovery.seriesBusOffs() == 3U);
    CHECK(bus.recovery.seriesDiscards() == 1U);
    CHECK(bus.lines.total() <= 4U);
    CHECK(!bus.monitor.isBusOff());
}

void testPeriodicSenderIsThrottled()
{
    std::printf("periodic frame every 100 ms into the short, 10 min: at most one bus off per second\n");
    Channel bus(CanBusOffRecovery::DEFAULT_CONFIG);
    bus.run(100U);
    bus.controller.shorted = true;
    bus.run(TEN_MINUTES_MS, 100U);
    std::printf("  model: %u bus offs, %u log lines (%u summaries), %u drops, pause %u ms\n",
                static_cast<unsigned>(bus.controller.busOffEntries),
                static_cast<unsigned>(bus.lines.total()),
                static_cast<unsigned>(bus.lines.summary),
                static_cast<unsigned>(bus.drops),
                static_cast<unsigned>(bus.recovery.pauseMs()));
    CHECK(bus.controller.busOffEntries <= 620U);  // 10 min at one per second after the ramp
    CHECK(bus.controller.busOffEntries >= 550U);
    CHECK(bus.monitor.busOffCount() == bus.controller.busOffEntries);
    CHECK(bus.recovery.pauseMs() == 1000U);
    CHECK(bus.recovery.inSeries());               // one lasting fault = one series
    CHECK(bus.lines.busOff == 1U);
    CHECK(bus.lines.dropped == 1U);
    CHECK(bus.lines.summary >= 59U);
    CHECK(bus.lines.summary <= 60U);              // one line per 10 s
    CHECK(bus.lines.total() <= 64U);
    CHECK(bus.lines.passive == 0U);

    std::printf("short removed: bus back within the pause, series over after quietMs\n");
    bus.controller.shorted = false;
    uint32_t const before  = bus.controller.framesSent;
    bus.run(1100U, 100U);
    CHECK(!bus.monitor.isBusOff());
    CHECK(bus.controller.framesSent > before);    // frames go out again
    CHECK(bus.lines.seriesEnd == 0U);             // quietMs 2000 not over yet
    bus.run(2000U, 100U);
    CHECK(!bus.recovery.inSeries());
    CHECK(bus.lines.seriesEnd == 1U);

    std::printf("next fault: a new series is logged again from the start (pause 10 ms)\n");
    bus.controller.shorted = true;
    bus.run(200U, 100U);
    CHECK(bus.lines.busOff == 2U);
    CHECK(bus.recovery.seriesBusOffs() >= 1U);
    CHECK(bus.recovery.pauseMs() <= 80U);
}

void testSingleBusOffLikeBefore()
{
    std::printf("one bus off (transient): logged with its recovery, back after about 20 ms\n");
    Channel bus(CanBusOffRecovery::DEFAULT_CONFIG);
    bus.run(100U);
    bus.controller.shorted       = true;
    bus.controller.pendingFrames = 1U;
    bus.run(10U);                                 // bus off happened, short gone again
    bus.controller.shorted = false;
    bus.run(5000U);
    CHECK(bus.controller.busOffEntries == 1U);
    CHECK(bus.controller.framesSent == 1U);       // the frame was not dropped, it went out
    CHECK(bus.lines.busOff == 1U);
    CHECK(bus.lines.recovered == 1U);
    CHECK(bus.lines.seriesEnd == 0U);             // a single bus off needs no summary
    CHECK(bus.monitor.longestBusOffMs() <= 20U);
    CHECK(bus.lines.total() == 2U);
}

void testLostRecoveryWithoutBoffint()
{
    std::printf("counter-check: held, but without BOFFINT the bus offs after a release are lost\n");
    Channel bus(CanBusOffRecovery::DEFAULT_CONFIG, false);
    bus.run(100U);
    bus.controller.shorted = true;
    bus.run(60000U, 100U);
    std::printf("  model: %u bus offs, monitor %u\n",
                static_cast<unsigned>(bus.controller.busOffEntries),
                static_cast<unsigned>(bus.monitor.busOffCount()));
    CHECK(bus.monitor.busOffCount() < bus.controller.busOffEntries); // the monitor misses them...
    CHECK(bus.controller.busOffEntries > 1000U);                      // ...and the pause never grows
}

void testReleaseRepeated()
{
    std::printf("a release that is missed is repeated in the next cycle\n");
    Channel bus(CanBusOffRecovery::DEFAULT_CONFIG);
    bus.run(100U);
    bus.controller.shorted        = true;
    bus.controller.pendingFrames  = 1U;
    bus.controller.ignoresRelease = true;
    bus.run(5000U);
    CHECK(bus.controller.releases >= 4U);
    CHECK(bus.controller.busOffEntries == 3U);
    CHECK(!bus.monitor.isBusOff());
}

void testWithoutHold()
{
    std::printf("controller without hold (maxPauseMs 0): drop and throttled log still work\n");
    CanBusOffRecovery::Config const config = {0U, 0U, 3U, 2000U, 10000U};
    Channel bus(config);
    bus.run(100U);
    bus.controller.shorted       = true;
    bus.controller.pendingFrames = 1U;
    bus.run(10000U);
    CHECK(!bus.controller.boffrec);
    CHECK(bus.controller.pendingFrames == 0U);
    CHECK(bus.drops == 1U);
    CHECK(bus.lines.busOff == 1U);
    CHECK(bus.lines.total() <= 4U);
    CHECK(bus.controller.releases == 0U);
}

void testNoDrop()
{
    std::printf("discardAfter 0: the frame is kept, only the pause limits the bus offs\n");
    CanBusOffRecovery::Config const config = {10U, 1000U, 0U, 2000U, 10000U};
    Channel bus(config);
    bus.run(100U);
    bus.controller.shorted       = true;
    bus.controller.pendingFrames = 1U;
    bus.run(60000U);
    CHECK(bus.controller.pendingFrames == 1U);
    CHECK(bus.drops == 0U);
    CHECK(bus.controller.busOffEntries <= 70U);   // 60 s: ramp (7 in 1.3 s), then one per second
    CHECK(bus.lines.dropped == 0U);
}

void testSummaryOnlyWithNews()
{
    std::printf("summary line only for a period with bus offs that were not logged\n");
    CanBusOffRecovery::Config const config = {10U, 1000U, 3U, 30000U, 1000U};
    Channel bus(config);
    bus.run(100U);
    bus.controller.shorted       = true;
    bus.controller.pendingFrames = 1U;
    bus.run(900U);                                // three bus offs, the frame dropped, all within the first second
    CHECK(bus.controller.busOffEntries == 3U);
    bus.run(20000U);                              // series goes on (quietMs 30 s), nothing new to report
    CHECK(bus.recovery.inSeries());
    CHECK(bus.lines.summary == 1U);               // the first period: 2 bus offs after the logged one
    CHECK(bus.recovery.summaryBusOffs() == 0U);   // the last period had none
}

void testTwoBuses()
{
    std::printf("two buses: the fault of one does not touch the other\n");
    Channel can0(CanBusOffRecovery::DEFAULT_CONFIG);
    Channel can1(CanBusOffRecovery::DEFAULT_CONFIG);
    can1.controller.shorted = true;
    for (uint32_t s = 0U; s < 60U; ++s)
    {
        can0.run(1000U, 100U);
        can1.run(1000U, 100U);
    }
    CHECK(can0.monitor.busOffCount() == 0U);
    CHECK(can0.lines.total() == 0U);
    CHECK(can0.controller.framesSent >= 590U);
    CHECK(can1.monitor.busOffCount() > 0U);
    CHECK(can1.lines.busOff == 1U);
}

void testProjectValuesPerBus()
{
    // COM-37: values of project.yaml can.<bus>.bus_off, one instance per bus with the values of its bus (not the
    // eval board's defaults): CAN1 of an own profile pauses 20..200 ms, drops at every bus off, series end 500 ms,
    // one summary per second; CAN0 keeps the defaults.
    std::printf("project values per bus: CAN1 20..200 ms, drop at once, summary 1 s; CAN0 defaults\n");
    constexpr CanBusOffRecovery::Config CAN1_BUS_OFF_CONFIG = {20U, 200U, 1U, 500U, 1000U};
    CanBusOffRecovery const pauses(CAN1_BUS_OFF_CONFIG);
    uint32_t const expected[] = {20U, 40U, 80U, 160U, 200U, 200U};
    for (uint32_t n = 1U; n <= 6U; ++n)
    {
        CHECK(pauses.pauseFor(n) == expected[n - 1U]);
    }
    Channel can0(CanBusOffRecovery::DEFAULT_CONFIG);
    Channel can1(CAN1_BUS_OFF_CONFIG);
    can0.run(100U);
    can1.run(100U);
    can0.controller.shorted = true;
    can1.controller.shorted = true;
    for (uint32_t s = 0U; s < 60U; ++s)
    {
        can0.run(1000U, 100U);
        can1.run(1000U, 100U);
    }
    std::printf("  model: CAN0 %u bus offs, %u drops, %u summaries; CAN1 %u bus offs, %u drops, %u summaries\n",
                static_cast<unsigned>(can0.controller.busOffEntries),
                static_cast<unsigned>(can0.drops),
                static_cast<unsigned>(can0.lines.summary),
                static_cast<unsigned>(can1.controller.busOffEntries),
                static_cast<unsigned>(can1.drops),
                static_cast<unsigned>(can1.lines.summary));
    CHECK(can1.recovery.pauseMs() == 200U);
    CHECK(can0.recovery.pauseMs() == 1000U);     // the other bus keeps its own values
    CHECK(can1.drops == can1.controller.busOffEntries); // discardAfter 1: at every bus off
    CHECK(can1.lines.dropped == 1U);             // logged once per series
    CHECK(can0.drops == can0.controller.busOffEntries - 2U); // discardAfter 3: from the 3rd bus off on
    // one bus off per pause plus the wait for the next periodic frame (100 ms): 200..300 ms
    CHECK(can1.controller.busOffEntries >= 190U);
    CHECK(can1.controller.busOffEntries <= 300U);
    CHECK(can0.controller.busOffEntries <= 62U);
    CHECK(can1.lines.summary >= 58U);            // one line per second
    CHECK(can1.lines.summary <= 60U);
    CHECK(can0.lines.summary <= 6U);             // one line per 10 s
    CHECK(can1.recovery.inSeries());

    std::printf("short removed: CAN1 series over after 500 ms, CAN0 after 2000 ms\n");
    can0.controller.shorted = false;
    can1.controller.shorted = false;
    can0.run(800U, 100U);
    can1.run(800U, 100U);
    CHECK(!can1.recovery.inSeries());
    CHECK(can1.lines.seriesEnd == 1U);
    CHECK(can0.recovery.inSeries());
    can0.run(2000U, 100U);
    CHECK(!can0.recovery.inSeries());
    CHECK(can0.lines.seriesEnd == 1U);
}

void testStopEndsSeries()
{
    std::printf("controller closed during a series: no hold, no release afterwards\n");
    CanBusOffRecovery recovery;
    CanBusMonitor monitor;
    Event events[2];
    (void)CanBusOffRecovery::feedMonitor(monitor, CanBusState::ERROR_ACTIVE, false, 0U, events);
    CHECK(CanBusOffRecovery::feedMonitor(monitor, CanBusState::BUS_OFF, true, 10U, events) == 1U);
    CHECK(events[0] == Event::WENT_BUS_OFF);
    CHECK(recovery.wentBusOff());
    CHECK(CanBusOffRecovery::feedMonitor(monitor, CanBusState::UNKNOWN, false, 10U, events) == 1U);
    CHECK(events[0] == Event::STOPPED);
    recovery.stop();
    CanBusOffRecovery::Step const step = recovery.cycle(false, 10U);
    CHECK(!step.release && !step.discard && !step.logSummary && !step.logSeriesEnd);
    CHECK(!recovery.inSeries());
}

void testFeedMonitorEdges()
{
    std::printf("feedMonitor: both edges of a bus off between two readings\n");
    CanBusMonitor monitor;
    Event events[2];
    (void)CanBusOffRecovery::feedMonitor(monitor, CanBusState::ERROR_ACTIVE, false, 0U, events);
    // off and back between two readings
    CHECK(CanBusOffRecovery::feedMonitor(monitor, CanBusState::ERROR_ACTIVE, true, 10U, events) == 2U);
    CHECK((events[0] == Event::WENT_BUS_OFF) && (events[1] == Event::RECOVERED));
    // off, then (after a release) back and off again between two readings
    CHECK(CanBusOffRecovery::feedMonitor(monitor, CanBusState::BUS_OFF, true, 10U, events) == 1U);
    CHECK(CanBusOffRecovery::feedMonitor(monitor, CanBusState::BUS_OFF, true, 10U, events) == 2U);
    CHECK((events[0] == Event::RECOVERED) && (events[1] == Event::WENT_BUS_OFF));
    CHECK(monitor.busOffCount() == 3U);
    CHECK(monitor.recoveries() == 2U);
    // still off, no new entry: nothing
    CHECK(CanBusOffRecovery::feedMonitor(monitor, CanBusState::BUS_OFF, false, 10U, events) == 1U);
    CHECK(events[0] == Event::NONE);
}
} // namespace

int main()
{
    testPauseSequence();
    testBoardFindingOldBehaviour();
    testHangingFrameIsDropped();
    testPeriodicSenderIsThrottled();
    testSingleBusOffLikeBefore();
    testLostRecoveryWithoutBoffint();
    testReleaseRepeated();
    testWithoutHold();
    testNoDrop();
    testSummaryOnlyWithNews();
    testTwoBuses();
    testProjectValuesPerBus();
    testStopEndsSeries();
    testFeedMonitorEdges();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
