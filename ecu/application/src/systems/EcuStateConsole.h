#pragma once

#include <ecuState/EcuState.h>

#include <cstdint>

/**
 * Console command "ecu" in the console context (SYS-13): the snapshot of the ECU state management that
 * EcuStateSystem takes in its own task (the task of the power supply, TASK_BSP on the eval board) and the text
 * printed from it afterwards in the console context.
 *
 * Until SYS-13 "ecu status" (15 lines, about 1100 characters, ~95 ms at 115200 Bd) ran completely in the task of
 * the power supply: the same task as the SBC watchdog trigger and the overcurrent supervision of the high-side
 * switches. The values of one "ecu status" are now also from one moment.
 *
 * Independent of OpenBSW: every name is resolved in the owner's task (the snapshot holds the strings), the text
 * goes through any object with a printf member. Host test: libs/bsw/consoleContext/test/host/EcuConsoleHostTest.cpp.
 * The text is the one before SYS-13.
 */
namespace systems
{
namespace ecuconsole
{

struct StepSnapshot
{
    char const* name;     // shutdownStepName
    char const* state;    // stepStateName
    char const* provider; // provider name or "no provider"
};

/** Everything "ecu status" prints. */
struct StatusSnapshot
{
    uint32_t nowMs;
    char const* state;
    uint32_t timeInStateMs;
    uint8_t runLevel;
    uint8_t shutdownLevel;
    uint32_t startupWakeupSources;
    uint32_t wakeupEvents;
    uint32_t selectedWakeupSources;
    uint32_t armedWakeupSources;
    uint32_t missingWakeupSources;
    uint32_t requestedCyclicMs;
    uint32_t usedCyclicMs;
    uint32_t supportedWakeupSources;
    StepSnapshot steps[::ecustate::SHUTDOWN_STEP_COUNT];
    bool stepRunning; // RUNDOWN with a step in progress
    char const* runningStep;
    uint32_t runningStepMs;
    uint32_t runningStepLimitMs;
    bool stepAborted;
    char const* abortStep;
    char const* abortState;
    char const* abortReason;
    bool rteSleepRequested;
    bool rteSleepConnected;
    bool consoleSleepRequested;
    bool sleepForced;
    bool sleepRefused;
    char const* powerSupply;
    bool powerDownSupported;
    char const* lastPowerDown; // "-" without result
    char const* outputFlush;   // provider name or "no provider"
    char const* outputFlushState;
    uint32_t outputFlushLimitMs;
    uint32_t outputFlushTookMs;
    uint32_t logEntriesLost;
    uint32_t minRunMs;
    uint32_t postRunMs;
    uint32_t shutdownTimeoutMs;
    uint32_t sleepTimeoutMs;
    bool wakeupDuringShutdownRestarts;
    bool afterWatchdogResetRuns;
    char const* stepLimitName[::ecustate::SHUTDOWN_STEP_COUNT];
    uint32_t stepLimitMs[::ecustate::SHUTDOWN_STEP_COUNT];
    bool stepErrorRestarts;
    uint16_t sleepRefusedCount;
    uint16_t standbyEntered;
    uint16_t standbyCancelled;
    uint16_t postRunRestarted;
    uint16_t stepFailed;
    uint16_t stepNotPresent;
    char const* restartReason;
    uint8_t stepsAborted;   // SYS-174: steps cut off by their limit and told through IShutdownStep::abort()
    bool restartRequested;  // SYS-173: restart (lifecycle level 0) requested and supervised
    bool directResetIssued; // SYS-173: restart overdue, direct reset of the family called (without hook: overdue)
};

// Note: the OpenBSW printf treats the 'l' length modifier as 64 bit, so 32 bit values use %u/%x.
template<class Out>
void writeWakeupSources(Out& out, uint32_t const sources)
{
    using namespace ::ecustate;
    struct Name
    {
        uint32_t bit;
        char const* name;
    };
    static Name const NAMES[] = {
        {wakeup::POWER, "power-on"},
        {wakeup::RESET, "reset"},
        {wakeup::WATCHDOG, "watchdog"},
        {wakeup::SUPPLY, "supply"},
        {wakeup::TIMER, "timer"},
        {wakeup::UNKNOWN, "unknown"},
    };
    (void)out.printf("0x%08x", static_cast<unsigned>(sources));
    for (Name const& n : NAMES)
    {
        if ((sources & n.bit) != 0U)
        {
            (void)out.printf(" %s", n.name);
        }
    }
    for (uint8_t i = 0U; i < wakeup::CAN_BUS_COUNT; ++i)
    {
        if ((sources & wakeup::canBus(i)) != 0U)
        {
            (void)out.printf(" CAN bus %u", static_cast<unsigned>(i));
        }
    }
    for (uint8_t i = 0U; i < wakeup::LOCAL_COUNT; ++i)
    {
        if ((sources & wakeup::local(i)) != 0U)
        {
            (void)out.printf(" local %u", static_cast<unsigned>(i));
        }
    }
}

/** SYS-02: run-down before the sleep, step by step with result, provider and the reason of an abort. */
template<class Out>
void writeRunDown(Out& out, StatusSnapshot const& s)
{
    (void)out.printf("run-down:");
    for (StepSnapshot const& step : s.steps)
    {
        (void)out.printf(" %s %s (%s)", step.name, step.state, step.provider);
    }
    if (s.stepRunning)
    {
        (void)out.printf(
            "\r\nrun-down step %s running for %u ms of %u ms",
            s.runningStep,
            static_cast<unsigned>(s.runningStepMs),
            static_cast<unsigned>(s.runningStepLimitMs));
    }
    if (s.stepAborted)
    {
        (void)out.printf("\r\nlast abort: step %s %s, reason %s", s.abortStep, s.abortState, s.abortReason);
    }
    (void)out.printf("\r\n");
}

template<class Out>
void writeStatus(Out& out, StatusSnapshot const& s)
{
    (void)out.printf(
        "state %s for %u ms, lifecycle run level %u, shutdown level %u\r\n",
        s.state,
        static_cast<unsigned>(s.timeInStateMs),
        static_cast<unsigned>(s.runLevel),
        static_cast<unsigned>(s.shutdownLevel));
    (void)out.printf("wake-up sources ");
    writeWakeupSources(out, s.startupWakeupSources);
    (void)out.printf("\r\nwake-up events since standby ");
    writeWakeupSources(out, s.wakeupEvents);
    // SYS-02: what the project selected, what this board arms and what it cannot serve
    (void)out.printf("\r\nwake-up selected ");
    writeWakeupSources(out, s.selectedWakeupSources);
    (void)out.printf("\r\nwake-up armed ");
    writeWakeupSources(out, s.armedWakeupSources);
    (void)out.printf("\r\nwake-up not on this board ");
    writeWakeupSources(out, s.missingWakeupSources);
    (void)out.printf(
        "\r\nwake-up cyclic: requested %u ms, used %u ms; supply offers ",
        static_cast<unsigned>(s.requestedCyclicMs),
        static_cast<unsigned>(s.usedCyclicMs));
    writeWakeupSources(out, s.supportedWakeupSources);
    (void)out.printf("\r\n");
    writeRunDown(out, s);
    (void)out.printf(
        "sleep request: rte %s (%s), console %s, forced %s, refused %s\r\n",
        s.rteSleepRequested ? "yes" : "no",
        s.rteSleepConnected ? "connected" : "not connected",
        s.consoleSleepRequested ? "yes" : "no",
        s.sleepForced ? "yes" : "no",
        s.sleepRefused ? "yes" : "no");
    (void)out.printf(
        "power supply %s, power down %s, last result %s\r\n",
        s.powerSupply,
        s.powerDownSupported ? "supported" : "not supported",
        s.lastPowerDown);
    // SYS-10: the flush of the output before the power down (SYS-09)
    (void)out.printf(
        "output flush %s: %s, limit %u ms, took %u ms\r\n",
        s.outputFlush,
        s.outputFlushState,
        static_cast<unsigned>(s.outputFlushLimitMs),
        static_cast<unsigned>(s.outputFlushTookMs));
    // SYS-12: entries the logger ring buffer dropped before the console printed them (also "logger lost")
    (void)out.printf("log entries lost: %u\r\n", static_cast<unsigned>(s.logEntriesLost));
    (void)out.printf(
        "parameters: min run %u ms, post-run %u ms, shutdown timeout %u ms, sleep timeout %u ms, "
        "wake-up during shutdown %s, after watchdog reset %s\r\n",
        static_cast<unsigned>(s.minRunMs),
        static_cast<unsigned>(s.postRunMs),
        static_cast<unsigned>(s.shutdownTimeoutMs),
        static_cast<unsigned>(s.sleepTimeoutMs),
        s.wakeupDuringShutdownRestarts ? "restart" : "sleep",
        s.afterWatchdogResetRuns ? "run" : "sleep");
    (void)out.printf("parameters: step limits");
    for (uint8_t i = 0U; i < ::ecustate::SHUTDOWN_STEP_COUNT; ++i)
    {
        (void)out.printf(
            "%s %s %u ms", (i == 0U) ? "" : ",", s.stepLimitName[i], static_cast<unsigned>(s.stepLimitMs[i]));
    }
    (void)out.printf(", on step error %s\r\n", s.stepErrorRestarts ? "restart" : "continue");
    (void)out.printf(
        "counters: sleep refused %u, standby %u, standby cancelled %u, post-run restarted %u, "
        "step failed %u, step not present %u, restart %s\r\n",
        static_cast<unsigned>(s.sleepRefusedCount),
        static_cast<unsigned>(s.standbyEntered),
        static_cast<unsigned>(s.standbyCancelled),
        static_cast<unsigned>(s.postRunRestarted),
        static_cast<unsigned>(s.stepFailed),
        static_cast<unsigned>(s.stepNotPresent),
        s.restartReason);
    (void)out.printf(
        "supervision: steps aborted %u, restart requested %s, direct reset %s\r\n",
        static_cast<unsigned>(s.stepsAborted),
        s.restartRequested ? "yes" : "no",
        s.directResetIssued ? "issued" : "no");
}

} // namespace ecuconsole
} // namespace systems
