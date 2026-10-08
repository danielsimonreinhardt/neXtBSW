#pragma once

#include "console/AsyncCommandWrapper.h"
#include "highSideSwitch/HighSideSwitchChannel.h"
#include "highSideSwitch/HighSideSwitchNames.h"
#include "lifecycle/SingleContextLifecycleComponent.h"

#include <util/command/GroupCommand.h>

namespace highside
{

/**
 * Lifecycle component of one smart high-side switch.
 *
 * Every cycleMs of the configuration: sample IS, run the state machine and drive IN/DEN
 * (HighSideSwitchChannel), log state changes. Off at start, at shutdown and after every fault; a
 * latched fault needs "<command> reset" and a new "<command> on".
 *
 * Console "<command>": status, on, off, reset. IO-42: registered in the console context (SYS-13); only the
 * snapshot of the state resp. the switching command runs in this system's task (HighSideSwitchConsole.h), the
 * output is formatted and written in TASK_IDLE - never at the UART from the task of the switch.
 * "on" switches the load supply: whether that is permitted on a given board is a rule of the
 * board, not of this driver. Its help text therefore
 * ends in the marker "(board rule)" (SYS-17), the one wording for "only with a person at the
 * board"; the board's README names the commands it applies to and tools/docgen reads the marker.
 */
class HighSideSwitchSystem
: public ::lifecycle::SingleContextLifecycleComponent
, private ::async::RunnableType
{
public:
    HighSideSwitchSystem(
        ::async::ContextType context,
        IHighSideSwitchIo& io,
        HighSideSwitchConfig const& config,
        HighSideSwitchNames const& names);
    HighSideSwitchSystem(HighSideSwitchSystem const&)            = delete;
    HighSideSwitchSystem& operator=(HighSideSwitchSystem const&) = delete;

    void init() override;
    void run() override;
    void shutdown() override;

    HighSideSwitchChannel const& getChannel() const { return _channel; }

    /**
     * IO-37: the channel for an integration that has to drive the switch besides the console, e.g. the power
     * stage output of the debug halt guard (integration::IPowerStageOutput). Same context as every other call.
     */
    HighSideSwitchChannel& getChannel() { return _channel; }

private:
    class Command : public ::util::command::GroupCommand
    {
    public:
        explicit Command(HighSideSwitchSystem& system);

    protected:
        PlainCommandInfo const* getInfo() const override { return _info; }

        void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;

    private:
        HighSideSwitchSystem& _system;
        PlainCommandInfo _info[6];
    };

    void execute() override;
    void reportChanges();

    ::async::ContextType const _context;
    ::async::TimeoutType _timeout;
    HighSideSwitchNames const _names;
    HighSideSwitchChannel _channel;
#if !defined(NX_CONSOLE) || NX_CONSOLE
    // PLAT-106: the console command only in an image with console (project.yaml console.enabled, NX_CONSOLE)
    Command _command;
    ::console::AsyncCommandWrapper _asyncCommand;
#endif
    HighSideSwitch::State _reportedState;
    bool _reportedOpenLoad;
    bool _reportedLocked;
    uint32_t _reportedOutputErrors;
};

} // namespace highside
