#include "highSideSwitch/HighSideSwitchSystem.h"

#include "console/ConsoleContext.h"
#include "highSideSwitch/HighSideSwitchConsole.h"

#include <util/format/SharedStringWriter.h>
#include <util/logger/Logger.h>

DECLARE_LOGGER_COMPONENT(BSP)

namespace
{
enum Id : uint8_t
{
    ID_STATUS,
    ID_ON,
    ID_OFF,
    ID_RESET,
};
} // namespace

namespace highside
{
using ::util::logger::BSP;
using ::util::logger::Logger;

HighSideSwitchSystem::HighSideSwitchSystem(
    ::async::ContextType const context,
    IHighSideSwitchIo& io,
    HighSideSwitchConfig const& config,
    HighSideSwitchNames const& names)
: ::lifecycle::SingleContextLifecycleComponent(context)
, _context(context)
, _timeout()
, _names(names)
, _channel(io, config)
#if !defined(NX_CONSOLE) || NX_CONSOLE
, _command(*this)
// IO-42: the command runs in the console context (SYS-13), only the snapshot resp. the switching runs in this
// system's task. Before, "kl30b status" wrote ~340 characters from TASK_BSP (~30 ms) and the 10 ms halt deadman
// of the debug halt guard, polled in the same task, switched KL30B and DRV_EN off.
, _asyncCommand(_command, ::console::CONSOLE_CONTEXT)
#endif
, _reportedState(HighSideSwitch::State::OFF)
, _reportedOpenLoad(false)
, _reportedLocked(false)
, _reportedOutputErrors(0U)
{}

void HighSideSwitchSystem::init()
{
    // Safe default: switch and diagnosis off (also the reset state of the output configuration).
    _channel.init();
    transitionDone();
}

void HighSideSwitchSystem::run()
{
    ::async::scheduleAtFixedRate(
        _context,
        *this,
        _timeout,
        _channel.getSwitch().getConfig().cycleMs,
        ::async::TimeUnit::MILLISECONDS);
    transitionDone();
}

void HighSideSwitchSystem::shutdown()
{
    _timeout.cancel();
    if (_channel.requestOff() == HighSideSwitch::CommandResult::OK)
    {
        Logger::info(BSP, "%s off (shutdown)", _names.label);
    }
    _reportedState = _channel.getSwitch().getState();
    transitionDone();
}

void HighSideSwitchSystem::execute()
{
    _channel.cycle();
    reportChanges();
}

void HighSideSwitchSystem::reportChanges()
{
    HighSideSwitch const& sw = _channel.getSwitch();
    if (_channel.getOutputErrors() != _reportedOutputErrors)
    {
        _reportedOutputErrors = _channel.getOutputErrors();
        Logger::error(BSP, "%s output write failed, outputs off", _names.label);
    }
    HighSideSwitch::State const state = sw.getState();
    if (state != _reportedState)
    {
        _reportedState = state;
        if ((state == HighSideSwitch::State::OVERCURRENT)
            || (state == HighSideSwitch::State::DIAG_FAULT))
        {
            Logger::error(
                BSP,
                "%s off: %s (%u mA, max %u mA, IS raw %u), reset needed",
                _names.label,
                HighSideSwitch::faultName(sw.getLastFault()),
                static_cast<unsigned>(sw.getLastMa()),
                static_cast<unsigned>(sw.getMaxOnMa()),
                static_cast<unsigned>(_channel.getLastSenseRaw()));
        }
        else
        {
            Logger::info(BSP, "%s %s", _names.label, HighSideSwitch::stateName(state));
        }
    }
    bool const openLoad = sw.isOpenLoad();
    if (openLoad != _reportedOpenLoad)
    {
        _reportedOpenLoad = openLoad;
        Logger::warn(
            BSP,
            "%s open load %s (%u mA, IS raw %u)",
            _names.label,
            openLoad ? "detected" : "cleared",
            static_cast<unsigned>(sw.getLastMa()),
            static_cast<unsigned>(_channel.getLastSenseRaw()));
    }
    // SYS-36: the safe state lock is no fault of the switch - an expected consequence of S1, reported once
    bool const locked = sw.isLocked();
    if (locked != _reportedLocked)
    {
        _reportedLocked = locked;
        if (locked)
        {
            Logger::warn(BSP, "%s locked by the safe state: off until reset", _names.label);
        }
        else
        {
            Logger::info(BSP, "%s safe state lock released, stays off", _names.label);
        }
    }
}

HighSideSwitchSystem::Command::Command(HighSideSwitchSystem& system)
: _system(system)
, _info{
      {system._names.command, system._names.description, 0U},
      {"status", "state, fault, load current, IS raw value, thresholds, counters", ID_STATUS},
      // SYS-17: "(board rule)" is the one marker for a command that needs a person at the board; whether it
      // applies is a property of the board (its README), tools/docgen reads the marker for the reference.
#ifndef NX_PRODUCT_BUILD
      // SYS-119 (review points S1, S2): the product image keeps "status" and "off" - "on" switches the load supply
      // without a specified function behind it, "reset" releases a latched fault without a reset
      {"on", "switch on, load supply (board rule)", ID_ON},
#endif
      {"off", "switch off", ID_OFF},
#ifndef NX_PRODUCT_BUILD // SYS-119 (S2)
      {"reset", "clear a latched overcurrent/diagnosis fault (stays off)", ID_RESET},
#endif
      {nullptr, nullptr, 0U},
  }
{}

void HighSideSwitchSystem::Command::executeCommand(
    ::util::command::CommandContext& context, uint8_t const idx)
{
    // IO-42: this runs in the console context (TASK_IDLE). The switch - its state and the switching commands - is
    // used in the task of this system (inOwner), the output is printed here, so TASK_BSP (the switch, the gate
    // driver and the deadman poll of the debug halt guard) spends only the snapshot on a command.
    HighSideSwitchSystem& s = _system;
    auto const inOwner      = [&s](auto& work) { ::console::runInOwnerContext(s._context, work); };
    switch (idx)
    {
        case ID_STATUS:
        {
            if (!context.checkEol())
            {
                break;
            }
            console::StatusSnapshot snapshot{};
            auto take = [&s, &snapshot]() { snapshot = console::takeStatus(s._channel); };
            inOwner(take);
            ::util::format::SharedStringWriter out(context);
            console::writeStatus(out, s._names.label, snapshot);
            break;
        }
#ifndef NX_PRODUCT_BUILD // SYS-119 (S1, S2)
        case ID_ON:
#endif
        case ID_OFF:
#ifndef NX_PRODUCT_BUILD // SYS-119 (S1, S2)
        case ID_RESET:
#endif
        {
            if (!context.checkEol())
            {
                break;
            }
#ifndef NX_PRODUCT_BUILD // SYS-119 (S1, S2)
            console::Command const command = (idx == ID_ON)    ? console::Command::ON
                                             : (idx == ID_OFF) ? console::Command::OFF
                                                               : console::Command::RESET;
#else
            console::Command const command = console::Command::OFF; // SYS-119: "off" only in the product image
#endif
            console::CommandSnapshot result{};
            auto switchWork = [&s, &result, command]()
            {
                result = console::execute(s._channel, command);
                s.reportChanges(); // logger only (buffered), no UART in the owner
            };
            inOwner(switchWork);
            ::util::format::SharedStringWriter out(context);
            console::writeCommandResult(out, s._names.command, command, result);
            break;
        }
        default:
        {
            break;
        }
    }
}

} // namespace highside
