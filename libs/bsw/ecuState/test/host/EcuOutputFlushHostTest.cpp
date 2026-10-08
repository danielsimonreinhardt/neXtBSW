// SPDX-License-Identifier: Apache-2.0
/* Host test of the output flush before the power down (SYS-09, libs/bsw/ecuState): the log lines of the run-down
 * and of the change to Sleep are written first, the output is drained next, the power supply is called last; a
 * flush that does not finish is cut off at its limit; the sleep timeout starts with the power down request. Plus the
 * pure parts of the console flush of the s32k1xx board integration (IdleOutputDrain.h): limit from baud rate and
 * logger buffer, drain detection from the cadence of the output context.
 * SYS-11: from the start of the flush on no log line reaches the output (the system holds its reports back).
 * Scripted flush, supply and lifecycle, a "system" that logs like ecu/application EcuStateSystem, no hardware.
 * Build and run: libs/bsw/ecuState/test/test_ecustate_host.py (zig c++). */

#include "IdleOutputDrain.h"
#include "ecuState/EcuStateController.h"
#include "ecuState/EcuStateMachine.h"
#include "ecuState/EcuStateRte.h"
#include "ecuState/IOutputFlush.h"
#include "ecuState/PermanentPowerSupply.h"

#include <cstdio>
#include <string>
#include <vector>

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

using namespace ::ecustate;
using Action          = EcuStateMachine::Action;
using PowerDownResult = IPowerSupply::PowerDownResult;
using RestartReason   = EcuStateMachine::RestartReason;

constexpr uint8_t RUN_LEVEL      = 9U;
constexpr uint8_t SHUTDOWN_LEVEL = 1U;

// Own test profile: straight to the run-down, sleep timeout 200 ms (shorter than the flushes below on purpose).
constexpr EcuStateConfig PROFILE{
    0U,          // min run
    0U,          // post-run
    800U,        // shutdown timeout
    200U,        // sleep timeout
    {100U, 50U}, // step limits: NvM, CAN
    WakeupSelection{wakeup::canBus(0U), 0U},
    WakeupDuringShutdown::SLEEP,
    AfterWatchdogReset::RUN,
    ShutdownStepError::RESTART};

using Events = std::vector<std::string>;

/** Flush with a scripted duration; every call goes into the shared event list. */
class ScriptedFlush final : public IOutputFlush
{
public:
    explicit ScriptedFlush(Events& events) : _events(events) {}

    uint32_t limitMs      = 1000U;
    uint32_t pollsToFlush = 3U; // isFlushed() answers true on this poll (0xFFFFFFFF: never)
    uint32_t starts       = 0U;
    uint32_t polls        = 0U;
    uint32_t startedAtMs  = 0U;

    char const* getName() const override { return "scripted"; }

    uint32_t getLimitMs() const override { return limitMs; }

    void start(uint32_t const nowMs) override
    {
        ++starts;
        polls       = 0U;
        startedAtMs = nowMs;
        _events.push_back("flush start");
    }

    bool isFlushed(uint32_t const /* nowMs */) override
    {
        ++polls;
        if (polls >= pollsToFlush)
        {
            if (polls == pollsToFlush)
            {
                _events.push_back("flush done");
            }
            return true;
        }
        return false;
    }

private:
    Events& _events;
};

class FakeSupply final : public IPowerSupply
{
public:
    Events* events         = nullptr; // shared with the rig, so the order of flush and power down is one list
    PowerDownResult result = PowerDownResult::STARTED;
    std::vector<uint32_t> powerDownCalls;

    char const* getName() const override { return "fake"; }

    bool isPowerDownSupported() const override { return true; }

    uint32_t getStartupWakeupSources() const override { return wakeup::POWER; }

    uint32_t takeWakeupEvents() override { return 0U; }

    uint32_t getSupportedWakeupSources() const override { return wakeup::CAN_BUSES; }

    uint32_t getCyclicWakeupMs(uint32_t const requestedMs) const override { return requestedMs; }

    PowerDownResult powerDown(uint32_t const nowMs, WakeupSelection const& /* selection */) override
    {
        powerDownCalls.push_back(nowMs);
        if (events != nullptr)
        {
            events->push_back("power down");
        }
        return result;
    }
};

class FakeLifecycle final : public ILifecycleControl
{
public:
    uint8_t level  = 0U;
    uint8_t target = RUN_LEVEL;
    std::vector<uint8_t> requests;
    EcuStateController* listener = nullptr;

    void transitionToLevel(uint8_t const l) override
    {
        requests.push_back(l);
        target = l;
    }

    void step()
    {
        if (level != target)
        {
            bool const down = level > target;
            level           = down ? static_cast<uint8_t>(level - 1U) : static_cast<uint8_t>(level + 1U);
            listener->lifecycleLevelReached(level, down);
        }
    }

    bool resetRequested() const { return !requests.empty() && (requests.back() == 0U); }
};

struct Rig
{
    Events events;
    ScriptedFlush flush{events};
    ShutdownSequence sequence;
    FakeLifecycle lifecycle;
    EcuStateController controller;
    State logged   = State::STARTUP;
    uint32_t nowMs = 0U;
    uint32_t sleepEnteredMs = 0U;

    Rig(EcuStateConfig const& config, FakeSupply& supply, bool const withFlush)
    : controller(config, supply, lifecycle, withSequence(withFlush), RUN_LEVEL, SHUTDOWN_LEVEL)
    {
        supply.events = &events;
        ::ecustate::rte::reset();
        lifecycle.listener = &controller;
    }

    ShutdownSequence const& withSequence(bool const withFlush)
    {
        if (withFlush)
        {
            sequence.setOutputFlush(flush);
        }
        return sequence;
    }

    bool loggedPowerDown    = false;
    bool loggedFlushTimeout = false;
    bool loggedRestart      = false;

    /**
     * One 10-ms cycle like EcuStateSystem::execute(): controller first, then report() - the state change, the
     * flush timeout, the power down result and the restart, each once, and nothing while the output is closed
     * (SYS-11).
     */
    void cycle()
    {
        nowMs += 10U;
        lifecycle.step();
        controller.cyclic(nowMs);
        State const state = controller.getStateMachine().getState();
        if ((state == State::SLEEP) && (sleepEnteredMs == 0U))
        {
            sleepEnteredMs = nowMs;
        }
        if (controller.isOutputClosed())
        {
            return;
        }
        if (state != logged)
        {
            events.push_back(std::string("log ") + stateName(logged) + " -> " + stateName(state));
            logged = state;
        }
        if ((controller.getOutputFlushState() == OutputFlushState::TIMEOUT) && !loggedFlushTimeout)
        {
            loggedFlushTimeout = true;
            events.push_back("log flush timeout");
        }
        if (controller.hasPowerDownResult() && !loggedPowerDown)
        {
            loggedPowerDown = true;
            events.push_back(
                std::string("log power down: ")
                + EcuStateController::powerDownResultName(controller.getPowerDownResult()));
        }
        if (controller.getStateMachine().isRestartIssued() && !loggedRestart)
        {
            loggedRestart = true;
            events.push_back(
                std::string("log restart: ")
                + EcuStateMachine::restartReasonName(controller.getStateMachine().getRestartReason()));
        }
    }

    /** Log lines (events "log ...") after \p event, -1 if \p event is missing. */
    int logLinesAfter(std::string const& event) const
    {
        int const from = indexOf(event);
        if (from < 0)
        {
            return -1;
        }
        int lines = 0;
        for (size_t i = static_cast<size_t>(from) + 1U; i < events.size(); ++i)
        {
            if (events[i].rfind("log ", 0U) == 0U)
            {
                ++lines;
            }
        }
        return lines;
    }

    void run(uint32_t const durationMs)
    {
        for (uint32_t t = 0U; t < durationMs; t += 10U)
        {
            cycle();
        }
    }

    /** Runs until the state machine is in SLEEP (at most \p maxMs). */
    void runToSleep(uint32_t const maxMs = 2000U)
    {
        controller.setConsoleSleepRequest(true);
        for (uint32_t t = 0U; (t < maxMs) && (controller.getStateMachine().getState() != State::SLEEP); t += 10U)
        {
            cycle();
        }
    }

    State state() const { return controller.getStateMachine().getState(); }

    /** Position of \p event in the event list, -1 if missing. */
    int indexOf(std::string const& event) const
    {
        for (size_t i = 0U; i < events.size(); ++i)
        {
            if (events[i] == event)
            {
                return static_cast<int>(i);
            }
        }
        return -1;
    }
};

// ---------------------------------------------------------------------------------------------------------------

/** Log of the change to Sleep first, flush next (not in the same cycle), power down only after the flush. */
void orderLogFlushPowerDown()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply, true);
    rig.run(100U); // lifecycle up to the run level, one level per cycle
    CHECK(rig.state() == State::RUN);
    CHECK(rig.controller.getOutputFlushState() == OutputFlushState::PENDING);
    rig.runToSleep();
    CHECK(rig.state() == State::SLEEP);
    CHECK(rig.flush.starts == 0U); // not in the cycle that entered SLEEP: its log line is not written yet
    CHECK(rig.controller.getOutputFlushState() == OutputFlushState::PENDING);
    rig.cycle();
    CHECK(rig.flush.starts == 1U);
    CHECK(rig.flush.startedAtMs == rig.sleepEnteredMs + 10U);
    CHECK(rig.controller.getOutputFlushState() == OutputFlushState::RUNNING);
    CHECK(supply.powerDownCalls.empty());
    rig.run(10U);
    CHECK(supply.powerDownCalls.empty()); // second poll: not drained yet
    rig.run(10U);
    CHECK(supply.powerDownCalls.size() == 1U); // third poll: drained -> power down in the same cycle
    CHECK(!supply.powerDownCalls.empty() && (supply.powerDownCalls[0] == rig.flush.startedAtMs + 20U));
    CHECK(rig.controller.getOutputFlushState() == OutputFlushState::DONE);
    CHECK(rig.controller.getOutputFlushDurationMs() == 20U);
    CHECK(rig.controller.getOutputFlushLimitMs() == 1000U);
    // the whole order in one list: log lines of the run-down, flush, power down
    int const toShutdown = rig.indexOf("log Rundown -> Shutdown");
    int const toSleep    = rig.indexOf("log Shutdown -> Sleep");
    int const start      = rig.indexOf("flush start");
    int const done       = rig.indexOf("flush done");
    int const powerDown  = rig.indexOf("power down");
    CHECK((toShutdown >= 0) && (toShutdown < toSleep));
    CHECK((toSleep >= 0) && (toSleep < start));
    CHECK((start >= 0) && (start < done));
    CHECK((done >= 0) && (powerDown == done + 1)); // the power down follows the flush directly
    CHECK(static_cast<size_t>(powerDown) == rig.events.size() - 1U);
    CHECK(rig.flush.starts == 1U);
    rig.run(500U);
    CHECK(rig.flush.starts == 1U);             // one flush per power down
    CHECK(supply.powerDownCalls.size() == 1U); // not repeated
}

/** A flush that never finishes is cut off at its limit, the ECU powers down anyway. */
void flushTimeoutPowersDownAnyway()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply, true);
    rig.flush.limitMs      = 80U;
    rig.flush.pollsToFlush = 0xFFFFFFFFU;
    rig.runToSleep();
    rig.cycle();
    uint32_t const startMs = rig.flush.startedAtMs;
    CHECK(rig.flush.starts == 1U);
    rig.run(70U);
    CHECK(supply.powerDownCalls.empty()); // 70 ms < 80 ms
    CHECK(rig.controller.getOutputFlushState() == OutputFlushState::RUNNING);
    rig.run(10U);
    CHECK(supply.powerDownCalls.size() == 1U);
    CHECK(!supply.powerDownCalls.empty() && (supply.powerDownCalls[0] == startMs + 80U));
    CHECK(rig.controller.getOutputFlushState() == OutputFlushState::TIMEOUT);
    CHECK(rig.controller.getOutputFlushDurationMs() == 80U);
    CHECK(!rig.lifecycle.resetRequested());
}

/** Limit 0 from the provider: no flush, power down in the cycle after SLEEP. */
void zeroLimitSkipsTheFlush()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply, true);
    rig.flush.limitMs = 0U;
    rig.runToSleep();
    CHECK(supply.powerDownCalls.empty());
    rig.cycle();
    CHECK(rig.flush.starts == 0U);
    CHECK(supply.powerDownCalls.size() == 1U);
    CHECK(rig.controller.getOutputFlushState() == OutputFlushState::DONE);
}

/** Without provider the fixed log delay of SYS-01 stays: power down POWER_DOWN_DELAY_MS after SLEEP. */
void withoutProviderTheLogDelayStays()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply, false);
    CHECK(rig.controller.getOutputFlushState() == OutputFlushState::NOT_PRESENT);
    rig.runToSleep();
    rig.run(POWER_DOWN_DELAY_MS - 10U);
    CHECK(supply.powerDownCalls.empty());
    rig.run(10U);
    CHECK(supply.powerDownCalls.size() == 1U);
    CHECK(!supply.powerDownCalls.empty() && (supply.powerDownCalls[0] == rig.sleepEnteredMs + POWER_DOWN_DELAY_MS));
    CHECK(rig.controller.getOutputFlushState() == OutputFlushState::NOT_PRESENT);
}

/** A flush longer than the sleep timeout: no restart during the flush, the timeout starts with the request. */
void sleepTimeoutStartsWithThePowerDownRequest()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply, true);
    rig.flush.pollsToFlush = 40U; // 390 ms, twice the sleep timeout of the profile
    rig.runToSleep();
    rig.run(390U);
    CHECK(!rig.lifecycle.resetRequested());
    CHECK(supply.powerDownCalls.empty());
    rig.run(10U);
    CHECK(supply.powerDownCalls.size() == 1U);
    uint32_t const requestMs = supply.powerDownCalls.empty() ? 0U : supply.powerDownCalls[0];
    // the MCU keeps running: restart sleepTimeoutMs after the request, not after entering SLEEP
    rig.run(PROFILE.sleepTimeoutMs - 10U);
    CHECK(!rig.lifecycle.resetRequested());
    rig.run(10U);
    CHECK(rig.lifecycle.resetRequested());
    CHECK(rig.nowMs == requestMs + PROFILE.sleepTimeoutMs);
    CHECK(rig.controller.getStateMachine().getRestartReason() == RestartReason::SLEEP_TIMEOUT);
}

/** The supply refuses after the flush: restart once, the flush is not repeated. */
void refusedAfterTheFlushRestarts()
{
    FakeSupply supply;
    supply.result = PowerDownResult::WAKEUP_PENDING;
    Rig rig(PROFILE, supply, true);
    rig.runToSleep();
    rig.run(100U);
    CHECK(supply.powerDownCalls.size() == 1U);
    CHECK(rig.lifecycle.resetRequested());
    CHECK(rig.controller.getStateMachine().getRestartReason() == RestartReason::POWER_DOWN_REFUSED);
    rig.run(1000U);
    CHECK(rig.flush.starts == 1U);
    CHECK(supply.powerDownCalls.size() == 1U);
}

/**
 * SYS-11: from the start of the flush on no log line reaches the output - the line of the provider ("flush start")
 * stays the last one. Board test SYS-09 (2026-09-18) showed "ECU power down: started" cut off behind it. The held
 * reports follow only when the ECU keeps running (here: sleep timeout of the fake supply that never switches off).
 */
void noLogLineAfterTheFlushStarts()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply, true);
    rig.run(100U);
    CHECK(!rig.controller.isOutputClosed()); // Run
    rig.runToSleep();
    CHECK(!rig.controller.isOutputClosed()); // the cycle that entered SLEEP logs its change
    CHECK(rig.indexOf("log Shutdown -> Sleep") >= 0);
    rig.cycle();
    CHECK(rig.flush.starts == 1U);
    CHECK(rig.controller.isOutputClosed()); // flush running
    rig.run(20U);
    CHECK(supply.powerDownCalls.size() == 1U);
    CHECK(rig.controller.isOutputClosed()); // power down started
    uint32_t const requestMs = supply.powerDownCalls.empty() ? 0U : supply.powerDownCalls[0];
    // as long as the MCU could still be switched off: nothing after the start of the flush
    rig.run(PROFILE.sleepTimeoutMs - 10U);
    CHECK(rig.logLinesAfter("flush start") == 0);
    CHECK(rig.logLinesAfter("power down") == 0);
    CHECK(rig.indexOf("log power down: started") < 0);
    CHECK(rig.controller.isOutputClosed());
    // the MCU is still running after the sleep timeout: restart, the held lines follow in their order
    rig.run(10U);
    CHECK(rig.nowMs == requestMs + PROFILE.sleepTimeoutMs);
    CHECK(rig.lifecycle.resetRequested());
    CHECK(!rig.controller.isOutputClosed());
    int const powerDown = rig.indexOf("power down");
    int const result    = rig.indexOf("log power down: started");
    int const restart   = rig.indexOf("log restart: sleep timeout");
    CHECK((powerDown >= 0) && (result == powerDown + 1) && (restart == result + 1));
    CHECK(rig.logLinesAfter("flush start") == 2);
}

/** SYS-11: a flush cut off at its limit - nothing logged behind it either, the warning follows only on a restart. */
void noLogLineAfterAFlushTimeout()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply, true);
    rig.flush.limitMs      = 80U;
    rig.flush.pollsToFlush = 0xFFFFFFFFU;
    rig.runToSleep();
    rig.run(90U);
    CHECK(supply.powerDownCalls.size() == 1U);
    CHECK(rig.controller.getOutputFlushState() == OutputFlushState::TIMEOUT);
    rig.run(PROFILE.sleepTimeoutMs - 10U);
    CHECK(rig.logLinesAfter("flush start") == 0);
    CHECK(rig.indexOf("log flush timeout") < 0);
    rig.run(10U);
    CHECK(rig.lifecycle.resetRequested());
    int const powerDown = rig.indexOf("power down");
    int const warning   = rig.indexOf("log flush timeout");
    CHECK((powerDown >= 0) && (warning == powerDown + 1));
    CHECK(rig.indexOf("log power down: started") == warning + 1);
    CHECK(rig.indexOf("log restart: sleep timeout") == warning + 2);
}

/** SYS-11: a refused power down leaves the ECU running - result and restart are logged in the same cycle. */
void refusedPowerDownIsLogged()
{
    FakeSupply supply;
    supply.result = PowerDownResult::FAILED;
    Rig rig(PROFILE, supply, true);
    rig.runToSleep();
    rig.run(30U);
    CHECK(supply.powerDownCalls.size() == 1U);
    CHECK(!rig.controller.isOutputClosed());
    int const powerDown = rig.indexOf("power down");
    CHECK((powerDown >= 0) && (rig.indexOf("log power down: failed") == powerDown + 1));
    CHECK(rig.indexOf("log restart: power down refused") == powerDown + 2);
}

/** SYS-11 without flush provider (fixed delay of SYS-01): closed from the started power down on. */
void withoutProviderNothingAfterThePowerDown()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply, false);
    rig.runToSleep();
    rig.run(POWER_DOWN_DELAY_MS - 10U);
    CHECK(!rig.controller.isOutputClosed()); // nothing flushed, nothing to protect yet
    rig.run(10U);
    CHECK(supply.powerDownCalls.size() == 1U);
    CHECK(rig.controller.isOutputClosed());
    rig.run(PROFILE.sleepTimeoutMs - 10U);
    CHECK(rig.logLinesAfter("power down") == 0);
    rig.run(10U);
    CHECK(rig.logLinesAfter("power down") == 2);
}

/** SYS-11 with limit 0 (flush skipped): closed from the started power down on. */
void zeroLimitNothingAfterThePowerDown()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply, true);
    rig.flush.limitMs = 0U;
    rig.runToSleep();
    CHECK(!rig.controller.isOutputClosed());
    rig.cycle();
    CHECK(supply.powerDownCalls.size() == 1U);
    CHECK(rig.controller.isOutputClosed());
    rig.run(PROFILE.sleepTimeoutMs - 10U);
    CHECK(rig.logLinesAfter("power down") == 0);
}

/** Board without switchable supply (no SBC): no Sleep, so no flush either. */
void boardWithoutSbcNeverFlushes()
{
    PermanentPowerSupply supply;
    Events events;
    ScriptedFlush flush(events);
    ShutdownSequence sequence;
    sequence.setOutputFlush(flush);
    FakeLifecycle lifecycle;
    EcuStateController controller(PROFILE, supply, lifecycle, sequence, RUN_LEVEL, SHUTDOWN_LEVEL);
    lifecycle.listener = &controller;
    ::ecustate::rte::reset();
    controller.setConsoleSleepRequest(true);
    for (uint32_t now = 10U; now < 3000U; now += 10U)
    {
        lifecycle.step();
        controller.cyclic(now);
    }
    CHECK(controller.getStateMachine().getState() == State::RUN);
    CHECK(controller.getStateMachine().isSleepRefused());
    CHECK(flush.starts == 0U);
    CHECK(controller.getOutputFlushState() == OutputFlushState::PENDING);
}

/** Serial transmit times of the limit derivation (8N1, rounded up). */
void serialTransmitTimes()
{
    CHECK(serialTransmitTimeUs(1U, 115200U) == 87U);   // 86.8 us
    CHECK(serialTransmitTimeUs(4U, 115200U) == 348U);  // 347.2 us
    CHECK(serialTransmitTimeMs(11520U, 115200U) == 1000U);
    CHECK(serialTransmitTimeMs(11521U, 115200U) == 1001U); // rounded up
    CHECK(serialTransmitTimeMs(1U, 9600U) == 2U);          // 1.04 ms
    CHECK(serialTransmitTimeMs(100U, 0U) == 0U);
    CHECK(serialTransmitTimeUs(1U, 0U) == 0U);
    CHECK(serialTransmitTimeMs(0xFFFFFFFFU, 1U) == 0xFFFFFFFFU); // saturated, no overflow
    CHECK(serialTransmitTimeMs(0U, 115200U) == 0U);
}

/** Limit of the console flush of the board integration: one full logger buffer, printed, at the console baud rate. */
void consoleFlushLimit()
{
    using namespace ::integration;
    CHECK(LOGGER_BUFFER_BYTES == 8192U);
    CHECK(consoleFlushLimitMs(115200U) == 5689U); // 65536 characters: 5688.9 ms
    CHECK(consoleFlushLimitMs(9600U) == 68267U);  // a slow console line takes proportionally longer
    CHECK(consoleFlushLimitMs(1000000U) == 656U);
    CHECK(consoleFlushLimitMs(0U) == 0U); // no baud rate: flush skipped instead of waiting forever
}

/** Drain detection from the cadence of the output context (IdleOutputDrain.h), console at 115200 Bd. */
void drainDetection()
{
    using ::integration::IdleOutputDrain;
    uint32_t const quietGapUs = serialTransmitTimeUs(IdleOutputDrain::QUIET_GAP_CHARACTERS, 115200U); // 348 us
    uint32_t const lineUs     = serialTransmitTimeUs(13U, 115200U); // shortest log line with "\r\n"
    CHECK(lineUs > quietGapUs);
    IdleOutputDrain drain(quietGapUs);
    uint32_t now = 1000000U;
    // first run: nothing to compare with
    CHECK(!drain.sample(now));
    // the handler writes a line between every two probe runs
    for (int i = 0; i < 20; ++i)
    {
        now += lineUs + 50U;
        CHECK(!drain.sample(now));
    }
    // one quiet gap is not enough, a line afterwards starts over
    now += 40U;
    CHECK(!drain.sample(now));
    now += lineUs;
    CHECK(!drain.sample(now));
    now += 40U;
    CHECK(!drain.sample(now));
    // preemption by another task makes a gap long: counts as "written", never as drained
    now += 5000U;
    CHECK(!drain.sample(now));
    // two quiet gaps in a row: drained, and it stays drained
    now += 40U;
    CHECK(!drain.sample(now));
    now += quietGapUs - 1U;
    CHECK(drain.sample(now));
    now += 40U;
    CHECK(drain.sample(now));
    // restart forgets everything
    drain.restart();
    now += 40U;
    CHECK(!drain.sample(now));
    now += 40U;
    CHECK(!drain.sample(now));
    now += 40U;
    CHECK(drain.sample(now));
    // exactly the threshold is not quiet
    drain.restart();
    CHECK(!drain.sample(now));
    now += quietGapUs;
    CHECK(!drain.sample(now));
    now += quietGapUs;
    CHECK(!drain.sample(now));
    // wrap-around of the 32-bit microsecond timer
    drain.restart();
    uint32_t wrap = 0xFFFFFFF0U;
    CHECK(!drain.sample(wrap));
    wrap += 40U;
    CHECK(!drain.sample(wrap));
    wrap += 40U;
    CHECK(drain.sample(wrap));
}

} // namespace

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"orderLogFlushPowerDown", orderLogFlushPowerDown},
        {"flushTimeoutPowersDownAnyway", flushTimeoutPowersDownAnyway},
        {"zeroLimitSkipsTheFlush", zeroLimitSkipsTheFlush},
        {"withoutProviderTheLogDelayStays", withoutProviderTheLogDelayStays},
        {"sleepTimeoutStartsWithThePowerDownRequest", sleepTimeoutStartsWithThePowerDownRequest},
        {"refusedAfterTheFlushRestarts", refusedAfterTheFlushRestarts},
        {"noLogLineAfterTheFlushStarts", noLogLineAfterTheFlushStarts},
        {"noLogLineAfterAFlushTimeout", noLogLineAfterAFlushTimeout},
        {"refusedPowerDownIsLogged", refusedPowerDownIsLogged},
        {"withoutProviderNothingAfterThePowerDown", withoutProviderNothingAfterThePowerDown},
        {"zeroLimitNothingAfterThePowerDown", zeroLimitNothingAfterThePowerDown},
        {"boardWithoutSbcNeverFlushes", boardWithoutSbcNeverFlushes},
        {"serialTransmitTimes", serialTransmitTimes},
        {"consoleFlushLimit", consoleFlushLimit},
        {"drainDetection", drainDetection},
    };
    for (auto const& test : tests)
    {
        int const before = failures;
        test.fn();
        std::printf("%s %s\n", (failures == before) ? "ok  " : "FAIL", test.name);
    }
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
