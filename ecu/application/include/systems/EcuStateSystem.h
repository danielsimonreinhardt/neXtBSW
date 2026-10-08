#pragma once

#include <async/Async.h>
#include <console/AsyncCommandWrapper.h>
#include <ecuState/EcuStateController.h>
#include <lifecycle/ILifecycleListener.h>
#include <lifecycle/ILifecycleManager.h>
#include <lifecycle/SingleContextLifecycleComponent.h>
#include <util/command/GroupCommand.h>

namespace systems
{

/**
 * ECU state management (libs/bsw/ecuState, SYS-01) on the OpenBSW lifecycle: listens to the lifecycle levels,
 * runs the EcuStateController every 10 ms in the context of the power supply, logs state changes, the run-down
 * steps before the sleep and the armed wake-up sources (SYS-02), console "ecu".
 * Registered in the shutdown level (together with the power supply provider), so it outlives the shutdown.
 */
class EcuStateSystem
: public ::lifecycle::SingleContextLifecycleComponent
, private ::lifecycle::ILifecycleListener
, private ::ecustate::ILifecycleControl
, private ::async::RunnableType
{
public:
    EcuStateSystem(
        ::async::ContextType context,
        ::lifecycle::ILifecycleManager& lifecycleManager,
        ::ecustate::IPowerSupply& supply,
        ::ecustate::EcuStateConfig const& config,
        uint8_t runLevel,
        uint8_t shutdownLevel);
    EcuStateSystem(EcuStateSystem const&)            = delete;
    EcuStateSystem& operator=(EcuStateSystem const&) = delete;

    void init() override;
    void run() override;
    void shutdown() override;

private:
    class Command : public ::util::command::GroupCommand
    {
    public:
        explicit Command(EcuStateSystem& system) : _system(system) {}

    protected:
        DECLARE_COMMAND_GROUP_GET_INFO
        void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;

    private:
        EcuStateSystem& _system;
    };

    void lifecycleLevelReached(
        uint8_t level, ::lifecycle::ILifecycleComponent::Transition::Type transition) override;
    void transitionToLevel(uint8_t level) override;
    void execute() override;
    void report(uint32_t nowMs);

    ::async::ContextType const _context;
    ::async::TimeoutType _timeout;
    ::lifecycle::ILifecycleManager& _lifecycleManager;
    ::ecustate::EcuStateController _controller;
#if !defined(NX_CONSOLE) || NX_CONSOLE
    // PLAT-106: the console command only in an image with console (project.yaml console.enabled, NX_CONSOLE)
    Command _command;
    ::console::AsyncCommandWrapper _asyncCommand;
#endif
    ::ecustate::State _reportedState;
    uint8_t _reportedSteps; // SYS-02: bit per run-down step whose result was logged
    bool _reportedRefused;
    bool _reportedRestart;
    bool _reportedPowerDown;
    bool _reportedMissingSources;
};

} // namespace systems
