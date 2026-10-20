#include "systems/EcuStateSystem.h"

#include "EcuStateConsole.h"
#include "integration/Integration.h" // run-down steps of the board integration (SYS-08)
#include "logger/logger.h"            // lostEntries() (SYS-12)
#include "stats/StartReasonHandover.h" // NVM-05: supply share of the start reasons for the ECU statistics

#include <bsp/timer/SystemTimer.h>
#include <console/ConsoleContext.h>
#include <ecuState/EcuStateRte.h>
#include <lifecycle/LifecycleLogger.h>
#include <util/format/SharedStringWriter.h>
#include <util/logger/Logger.h>

namespace
{
constexpr uint32_t CYCLE_MS = 10U;

enum Id
{
    ID_STATUS,
    ID_SLEEP,
    ID_RUN,
};

using ::util::logger::LIFECYCLE;
using ::util::logger::Logger;

/**
 * SYS-10: an output flush that ran into its limit (SYS-09) is warned about once, not in every cycle of the ECU
 * state management. There is exactly one EcuStateSystem per ECU (StaticBsp, Integration.cpp) and the merker is
 * only touched in the task of the power supply, so it does not have to be a member of the system.
 */
bool reportedOutputFlushTimeout = false;

/**
 * SYS-13: everything "ecu status" prints, taken in the task of the power supply (EcuStateConsole.h). Every name is
 * resolved here, so the console context only formats; the providers' strings are constants.
 */
void takeStatus(::ecustate::EcuStateController const& controller, ::systems::ecuconsole::StatusSnapshot& s)
{
    using namespace ::ecustate;
    EcuStateMachine const& machine = controller.getStateMachine();
    EcuStateConfig const& config   = machine.getConfig();
    uint32_t const nowMs           = getSystemTimeMs32Bit();
    s.nowMs                        = nowMs;
    s.state                        = stateName(machine.getState());
    s.timeInStateMs                = machine.getTimeInStateMs(nowMs);
    s.runLevel                     = controller.getRunLevel();
    s.shutdownLevel                = controller.getShutdownLevel();
    s.startupWakeupSources         = machine.getStartupWakeupSources();
    s.wakeupEvents                 = machine.getWakeupEvents();
    s.selectedWakeupSources        = config.wakeup.sources;
    s.armedWakeupSources           = controller.getArmedWakeupSources();
    s.missingWakeupSources         = controller.getMissingWakeupSources();
    s.requestedCyclicMs            = config.wakeup.cyclicMs;
    s.usedCyclicMs                 = controller.getCyclicWakeupMs();
    s.supportedWakeupSources       = controller.getPowerSupply().getSupportedWakeupSources();
    // SYS-02: run-down before the sleep, step by step with result, provider and the reason of an abort
    for (uint8_t i = 0U; i < SHUTDOWN_STEP_COUNT; ++i)
    {
        auto const step                     = static_cast<ShutdownStep>(i);
        IShutdownStep const* const provider = controller.getShutdownSequence().get(step);
        s.steps[i].name                     = shutdownStepName(step);
        s.steps[i].state                    = stepStateName(machine.getStepState(step));
        s.steps[i].provider                 = (provider != nullptr) ? provider->getName() : "no provider";
        s.stepLimitName[i]                  = shutdownStepName(step);
        s.stepLimitMs[i]                    = config.stepTimeoutMs[i];
    }
    s.stepRunning = (machine.getState() == State::RUNDOWN) && (machine.getStepAbortState() == StepState::PENDING);
    s.runningStep = s.stepRunning ? shutdownStepName(machine.getShutdownStep()) : "-";
    s.runningStepMs      = machine.getTimeInStepMs(nowMs);
    s.runningStepLimitMs = machine.getShutdownStepTimeoutMs();
    s.stepAborted        = (machine.getStepAbortState() != StepState::PENDING);
    if (s.stepAborted)
    {
        IShutdownStep const* const provider = controller.getShutdownSequence().get(machine.getStepAbort());
        s.abortStep                         = shutdownStepName(machine.getStepAbort());
        s.abortState                        = stepStateName(machine.getStepAbortState());
        s.abortReason                       = (provider != nullptr) ? provider->getFailureReason() : "-";
    }
    s.rteSleepRequested     = ::ecustate::rte::isSleepRequested();
    s.rteSleepConnected     = ::ecustate::rte::isSleepRequestConnected();
    s.consoleSleepRequested = controller.isConsoleSleepRequested();
    s.sleepForced           = machine.isSleepForced();
    s.sleepRefused          = machine.isSleepRefused();
    s.powerSupply           = controller.getPowerSupply().getName();
    s.powerDownSupported    = controller.getPowerSupply().isPowerDownSupported();
    s.lastPowerDown         = controller.hasPowerDownResult()
                                  ? EcuStateController::powerDownResultName(controller.getPowerDownResult())
                                  : "-";
    // SYS-10: the limit comes from the provider, not from getOutputFlushLimitMs(): the controller only records it
    // when a flush starts, so before the first sleep - the state the board test asks "ecu status" in - it would
    // read 0 ms.
    IOutputFlush const* const flush = controller.getShutdownSequence().getOutputFlush();
    s.outputFlush                   = (flush != nullptr) ? flush->getName() : "no provider";
    s.outputFlushState              = outputFlushStateName(controller.getOutputFlushState());
    s.outputFlushLimitMs = (flush != nullptr) ? flush->getLimitMs() : controller.getOutputFlushLimitMs();
    s.outputFlushTookMs  = controller.getOutputFlushDurationMs();
    s.logEntriesLost     = ::logger::lostEntries();
    s.minRunMs           = config.minRunMs;
    s.postRunMs          = config.postRunMs;
    s.shutdownTimeoutMs  = config.shutdownTimeoutMs;
    s.sleepTimeoutMs     = config.sleepTimeoutMs;
    s.wakeupDuringShutdownRestarts = (config.wakeupDuringShutdown == WakeupDuringShutdown::RESTART);
    s.afterWatchdogResetRuns       = (config.afterWatchdogReset == AfterWatchdogReset::RUN);
    s.stepErrorRestarts            = (config.shutdownStepError == ShutdownStepError::RESTART);
    EcuStateMachine::Counters const& c = machine.getCounters();
    s.sleepRefusedCount                = c.sleepRefused;
    s.standbyEntered                   = c.standbyEntered;
    s.standbyCancelled                 = c.standbyCancelled;
    s.postRunRestarted                 = c.postRunRestarted;
    s.stepFailed                       = c.stepFailed;
    s.stepNotPresent                   = c.stepNotPresent;
    s.restartReason                    = EcuStateMachine::restartReasonName(machine.getRestartReason());
    s.stepsAborted                     = controller.getAbortedSteps();     // SYS-174
    s.restartRequested                 = controller.isRestartRequested();  // SYS-173
    s.directResetIssued                = controller.isDirectResetIssued(); // SYS-173
}

} // namespace

namespace systems
{

EcuStateSystem::EcuStateSystem(
    ::async::ContextType const context,
    ::lifecycle::ILifecycleManager& lifecycleManager,
    ::ecustate::IPowerSupply& supply,
    ::ecustate::EcuStateConfig const& config,
    uint8_t const runLevel,
    uint8_t const shutdownLevel)
: ::lifecycle::SingleContextLifecycleComponent(context)
, _context(context)
, _timeout()
, _lifecycleManager(lifecycleManager)
// SYS-02: the run-down steps come from the integration (NvM without provider today, NVM-01)
, _controller(config, supply, *this, ::integration::shutdownSequence(), runLevel, shutdownLevel)
#if !defined(NX_CONSOLE) || NX_CONSOLE
, _command(*this)
// SYS-13: the command runs in the console context, only the snapshot runs in the task of the power supply
, _asyncCommand(_command, ::console::CONSOLE_CONTEXT)
#endif
, _reportedState(::ecustate::State::STARTUP)
, _reportedSteps(0U)
, _reportedRefused(false)
, _reportedRestart(false)
, _reportedPowerDown(false)
, _reportedMissingSources(false)
{}

void EcuStateSystem::init()
{
    _lifecycleManager.addLifecycleListener(*this);
    transitionDone();
}

void EcuStateSystem::run()
{
    ::async::scheduleAtFixedRate(_context, *this, _timeout, CYCLE_MS, ::async::TimeUnit::MILLISECONDS);
    transitionDone();
}

void EcuStateSystem::shutdown()
{
    // only on the way to level 0 (restart): the state management stays active during the ECU shutdown.
    // The listener stays registered (removing it while the manager notifies is not safe), events are ignored.
    _timeout.cancel();
    transitionDone();
}

void EcuStateSystem::lifecycleLevelReached(
    uint8_t const level, ::lifecycle::ILifecycleComponent::Transition::Type const transition)
{
    _controller.lifecycleLevelReached(
        level, transition == ::lifecycle::ILifecycleComponent::Transition::Type::SHUTDOWN);
}

void EcuStateSystem::transitionToLevel(uint8_t const level) { _lifecycleManager.transitionToLevel(level); }

void EcuStateSystem::execute()
{
    uint32_t const nowMs = getSystemTimeMs32Bit();
    _controller.cyclic(nowMs);
    report(nowMs);
}

void EcuStateSystem::report(uint32_t const /* nowMs */)
{
    // SYS-11: from the start of the output flush on nothing is logged. A line logged now would be printed after
    // the flush and cut off by the power down (board test SYS-09, 2026-09-18: "ECU power down: started" behind
    // the last line). The line of the flush provider stays the last one; the reports below are held back and
    // follow only if the power down is refused or a restart is issued, i.e. when the ECU keeps running.
    if (_controller.isOutputClosed())
    {
        return;
    }
    auto const& machine = _controller.getStateMachine();
    ::ecustate::State const state = machine.getState();
    // SYS-02: a selected wake-up source the supply of this board cannot arm, reported once after the start
    if (!_reportedMissingSources && (state != ::ecustate::State::STARTUP))
    {
        _reportedMissingSources = true;
        if (_controller.getMissingWakeupSources() != 0U)
        {
            Logger::warn(
                LIFECYCLE,
                "ECU wake-up sources 0x%08x not available on this board (power supply %s offers 0x%08x)",
                static_cast<unsigned>(_controller.getMissingWakeupSources()),
                _controller.getPowerSupply().getName(),
                static_cast<unsigned>(_controller.getPowerSupply().getSupportedWakeupSources()));
        }
    }
    // SYS-02: every finished run-down step with its result and time limit, so an abort can be followed
    for (uint8_t i = 0U; i < ::ecustate::SHUTDOWN_STEP_COUNT; ++i)
    {
        auto const step                 = static_cast<::ecustate::ShutdownStep>(i);
        ::ecustate::StepState const now = machine.getStepState(step);
        uint8_t const bit               = static_cast<uint8_t>(1U << i);
        bool const finished = (now != ::ecustate::StepState::PENDING)
                              && (now != ::ecustate::StepState::RUNNING);
        if (!finished || ((_reportedSteps & bit) != 0U))
        {
            continue;
        }
        _reportedSteps = static_cast<uint8_t>(_reportedSteps | bit);
        ::ecustate::IShutdownStep const* const provider = _controller.getShutdownSequence().get(step);
        char const* const reason = (provider != nullptr) ? provider->getFailureReason() : "-";
        if (now == ::ecustate::StepState::DONE)
        {
            Logger::info(
                LIFECYCLE,
                "ECU run-down step %s done within %u ms",
                ::ecustate::shutdownStepName(step),
                static_cast<unsigned>(machine.getConfig().stepTimeoutMs[i]));
        }
        else if (now == ::ecustate::StepState::NOT_PRESENT)
        {
            Logger::info(
                LIFECYCLE,
                "ECU run-down step %s not present: no provider in this project",
                ::ecustate::shutdownStepName(step));
        }
        else
        {
            Logger::warn(
                LIFECYCLE,
                "ECU run-down step %s %s (limit %u ms): %s",
                ::ecustate::shutdownStepName(step),
                ::ecustate::stepStateName(now),
                static_cast<unsigned>(machine.getConfig().stepTimeoutMs[i]),
                reason);
        }
    }
    if (state != _reportedState)
    {
        if (_reportedState == ::ecustate::State::STARTUP)
        {
            // NVM-05: the share of the role power_supply is known from the run level on; the statistics count it
            // NVM-34: an MCU-only start (the supply did not restart) is not counted in the supply row
            ::ecustats::handover::publishSupplyStartReasons(
                _controller.getPowerSupply().getStartupWakeupSources(),
                _controller.getPowerSupply().restartedForThisStart());
            Logger::info(
                LIFECYCLE,
                "ECU state Run, wake-up sources 0x%08x, power supply %s",
                static_cast<unsigned>(machine.getStartupWakeupSources()),
                _controller.getPowerSupply().getName());
        }
        else
        {
            Logger::info(
                LIFECYCLE,
                "ECU state %s -> %s",
                ::ecustate::stateName(_reportedState),
                ::ecustate::stateName(state));
        }
        _reportedState = state;
    }
    if (machine.isSleepRefused() != _reportedRefused)
    {
        _reportedRefused = machine.isSleepRefused();
        if (_reportedRefused)
        {
            Logger::warn(
                LIFECYCLE,
                "ECU sleep request refused: power supply %s cannot power down",
                _controller.getPowerSupply().getName());
        }
    }
    // SYS-10: the output did not drain within its limit (SYS-09), the ECU powered down anyway. The line is only
    // logged when the power down fails (held back before, SYS-11) - it is meant for that case, and it is logged
    // once.
    if ((_controller.getOutputFlushState() == ::ecustate::OutputFlushState::TIMEOUT)
        && !reportedOutputFlushTimeout)
    {
        reportedOutputFlushTimeout = true;
        ::ecustate::IOutputFlush const* const flush = _controller.getShutdownSequence().getOutputFlush();
        Logger::warn(
            LIFECYCLE,
            "ECU output flush %s not finished within %u ms: powered down with the output still pending",
            (flush != nullptr) ? flush->getName() : "-",
            static_cast<unsigned>(_controller.getOutputFlushLimitMs()));
    }
    // SYS-11: with a started power down this line comes only after a sleep timeout (MCU still running)
    if (_controller.hasPowerDownResult() && !_reportedPowerDown)
    {
        _reportedPowerDown = true;
        Logger::info(
            LIFECYCLE,
            "ECU power down: %s",
            ::ecustate::EcuStateController::powerDownResultName(_controller.getPowerDownResult()));
    }
    if (machine.isRestartIssued() && !_reportedRestart)
    {
        _reportedRestart = true;
        Logger::warn(
            LIFECYCLE,
            "ECU restart: %s",
            ::ecustate::EcuStateMachine::restartReasonName(machine.getRestartReason()));
    }
}

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(EcuStateSystem::Command, "ecu", "ECU state management")
COMMAND_GROUP_COMMAND(
    ID_STATUS, "status", "state, wake-up sources, run-down steps, sleep requests, parameters, counters")
COMMAND_GROUP_COMMAND(ID_SLEEP, "sleep", "request sleep from the console, MCU supply off with a SBC, wake-up by CAN (board rule)")
COMMAND_GROUP_COMMAND(ID_RUN, "run", "withdraw the console sleep request")
DEFINE_COMMAND_GROUP_GET_INFO_END

void EcuStateSystem::Command::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    // SYS-13: this runs in the console context (TASK_IDLE). The state management is read and changed in the task
    // of the power supply (_context), as one snapshot; the text is printed here.
    EcuStateSystem& system = _system;
    auto& controller       = system._controller;
    ::util::format::SharedStringWriter out(context);
    switch (idx)
    {
        case ID_STATUS:
        {
            ecuconsole::StatusSnapshot s{};
            auto const take = [&controller, &s]() { takeStatus(controller, s); };
            ::console::runInOwnerContext(system._context, take);
            ecuconsole::writeStatus(out, s);
            break;
        }
        case ID_SLEEP:
        {
            uint32_t postRunMs = 0U;
            uint32_t armed     = 0U;
            auto const request = [&controller, &postRunMs, &armed]()
            {
                controller.setConsoleSleepRequest(true);
                postRunMs = controller.getStateMachine().getConfig().postRunMs;
                armed     = controller.getArmedWakeupSources();
            };
            ::console::runInOwnerContext(system._context, request);
            (void)out.printf(
                "sleep requested: Standby after min run, Rundown after %u ms post-run, then Shutdown and "
                "Sleep (withdraw: ecu run). Armed wake-up sources ",
                static_cast<unsigned>(postRunMs));
            ecuconsole::writeWakeupSources(out, armed);
            (void)out.printf("\r\n");
            break;
        }
        case ID_RUN:
        {
            auto const withdraw = [&controller]() { controller.setConsoleSleepRequest(false); };
            ::console::runInOwnerContext(system._context, withdraw);
            (void)out.printf("console sleep request withdrawn\r\n");
            break;
        }
        default: break;
    }
}

} // namespace systems
