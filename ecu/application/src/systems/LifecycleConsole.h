#pragma once

#include <cstdint>

/**
 * Console command "lc" in the console context (SYS-13).
 *
 * The command (ecu/consoleCommands, LifecycleControlCommand) was registered with the context of the lifecycle
 * manager, TASK_SYSADMIN, which runs above task can: its output - "ok" and the prompt, an error - held both at
 * the UART. The command itself is not ours to split, but everything it changes goes through the
 * ILifecycleManager it is given. SysAdminSystem therefore hands it this proxy and registers it in the console
 * context: level queries are answered directly (constant after the start), a transition is requested in the
 * task of the lifecycle manager, as before. "lc bootloader" and "lc assert" do not touch the manager and run in
 * the console context.
 *
 * A template over the interface, so that the host test (libs/bsw/consoleContext/test/host/LcConsoleHostTest.cpp)
 * does not need OpenBSW: Manager = ::lifecycle::ILifecycleManager, Listener = ::lifecycle::ILifecycleListener on
 * the target; RunIn(context, work) runs work in that context and returns when it is done.
 */
namespace systems
{

template<class Manager, class Listener, class ContextT, class RunIn>
class LifecycleConsole final : public Manager
{
public:
    LifecycleConsole(Manager& target, ContextT const owner) : Manager(), _target(target), _owner(owner) {}

    LifecycleConsole(LifecycleConsole const&)            = delete;
    LifecycleConsole& operator=(LifecycleConsole const&) = delete;

    uint8_t getLevelCount() const override { return _target.getLevelCount(); }

    void transitionToLevel(uint8_t const level) override
    {
        Manager& target  = _target;
        auto const work = [&target, level]() { target.transitionToLevel(level); };
        RunIn()(_owner, work);
    }

    void addLifecycleListener(Listener& listener) override { _target.addLifecycleListener(listener); }

    void removeLifecycleListener(Listener& listener) override { _target.removeLifecycleListener(listener); }

private:
    Manager& _target;
    ContextT const _owner;
};

} // namespace systems
