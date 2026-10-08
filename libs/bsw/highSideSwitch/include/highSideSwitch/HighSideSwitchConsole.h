#pragma once

#include "highSideSwitch/HighSideSwitchChannel.h"

#include <cstdint>

/**
 * IO-42: console command "<name>" of a high-side switch in the console context (SYS-13 pattern): the snapshot that
 * HighSideSwitchSystem takes in its own task and the text printed from it afterwards in the console context.
 *
 * Board finding 2026-09-21 (session d3): "kl30b status" triggered the halt deadman of the debug halt guard every
 * time (4 of 4, "debug halt or stall (timer)"), then KL30B and DRV_EN were off; "gd status", "adc get" and
 * "ecu status" did not. The command ran completely in TASK_BSP - the task of the switch, the gate driver and the
 * deadman poll - and its four lines (about 340 characters at 115200 Bd, 86.8 us each: about 30 ms) went to the
 * UART synchronously from that task. The deadman is retriggered every 1 ms in TASK_BSP and fires after 10 ms, so
 * a status command switched a running load and the power stage off. "gd status" had been moved into the console
 * context with SYS-13; the high-side switch had not.
 *
 * Now the task of the switch spends only the copy of the snapshot (a few microseconds) resp. the command itself
 * on a console command, independent of the length of the output. Independent of OpenBSW and the board: the text
 * goes through any object with a printf member (util::format::SharedStringWriter on the target); host test
 * test/host/HighSideSwitchConsoleHostTest.cpp. The text is the one before IO-42.
 */
namespace highside
{
namespace console
{

/** Everything "<name> status" prints, taken in the task of the switch. */
struct StatusSnapshot
{
    HighSideSwitch::State state;
    HighSideSwitch::Fault lastFault;
    bool blanking;
    bool locked; // SYS-36: safe state lock
    bool enable;
    bool diagEnable;
    bool openLoad;
    uint32_t lastMa;
    uint32_t maxOnMa;
    uint32_t senseRaw;
    uint32_t outputErrors;
    HighSideSwitchConfig config;
    HighSideSwitch::Counters counters;
};

/** Copies the state of \p channel; runs in the task of the switch. No output, no waiting. */
inline StatusSnapshot takeStatus(HighSideSwitchChannel const& channel)
{
    HighSideSwitch const& sw = channel.getSwitch();
    StatusSnapshot s{};
    s.state        = sw.getState();
    s.lastFault    = sw.getLastFault();
    s.blanking     = sw.isBlanking();
    s.locked       = sw.isLocked();
    s.enable       = sw.isEnableOutput();
    s.diagEnable   = sw.isDiagEnableOutput();
    s.openLoad     = sw.isOpenLoad();
    s.lastMa       = sw.getLastMa();
    s.maxOnMa      = sw.getMaxOnMa();
    s.senseRaw     = channel.getLastSenseRaw();
    s.outputErrors = channel.getOutputErrors();
    s.config       = sw.getConfig();
    s.counters     = sw.getCounters();
    return s;
}

/** The text of "<name> status" (four lines); runs in the console context. */
template<class Out>
void writeStatus(Out& out, char const* const label, StatusSnapshot const& s)
{
    (void)out.printf(
        "%s %s%s%s, last fault %s, EN %u, DEN %u%s\r\n",
        label,
        HighSideSwitch::stateName(s.state),
        s.blanking ? " (blanking)" : "",
        s.locked ? " (locked by the safe state)" : "",
        HighSideSwitch::faultName(s.lastFault),
        s.enable ? 1U : 0U,
        s.diagEnable ? 1U : 0U,
        s.openLoad ? ", open load" : "");
    (void)out.printf(
        "load %u mA, max since on %u mA (IS raw %u)\r\n",
        static_cast<unsigned>(s.lastMa),
        static_cast<unsigned>(s.maxOnMa),
        static_cast<unsigned>(s.senseRaw));
    HighSideSwitchConfig const& c = s.config;
    (void)out.printf(
        "limits: overcurrent >= %u mA for %u ms, fault >= %u mA for %u ms, blanking %u ms, "
        "open load < %u mA, off <= %u mA (%u ms), cycle %u ms\r\n",
        static_cast<unsigned>(c.overcurrentMa),
        static_cast<unsigned>(c.overcurrentDebounceMs),
        static_cast<unsigned>(c.faultMa),
        static_cast<unsigned>(c.faultDebounceMs),
        static_cast<unsigned>(c.switchOnBlankingMs),
        static_cast<unsigned>(c.openLoadMa),
        static_cast<unsigned>(c.offMaxMa),
        static_cast<unsigned>(c.offDebounceMs),
        static_cast<unsigned>(c.cycleMs));
    HighSideSwitch::Counters const& cnt = s.counters;
    (void)out.printf(
        "switch-ons %u, overcurrent trips %u, diagnosis faults %u, ADC errors %u, "
        "open-load cycles %u, output errors %u\r\n",
        static_cast<unsigned>(cnt.switchOns),
        static_cast<unsigned>(cnt.overcurrentTrips),
        static_cast<unsigned>(cnt.diagFaults),
        static_cast<unsigned>(cnt.adcErrors),
        static_cast<unsigned>(cnt.openLoadCycles),
        static_cast<unsigned>(s.outputErrors));
}

/** Commands that change the switch: "on", "off", "reset". */
enum class Command : uint8_t
{
    ON,
    OFF,
    RESET,
};

/** Result of a switching command, taken in the task of the switch right after the command. */
struct CommandSnapshot
{
    HighSideSwitch::CommandResult result;
    HighSideSwitch::State state;
};

/** Runs \p command on \p channel; runs in the task of the switch. */
inline CommandSnapshot execute(HighSideSwitchChannel& channel, Command const command)
{
    CommandSnapshot r{};
    r.result = (command == Command::ON)    ? channel.requestOn()
               : (command == Command::OFF) ? channel.requestOff()
                                           : channel.reset();
    r.state  = channel.getSwitch().getState();
    return r;
}

/** The answer line of a switching command; runs in the console context. */
template<class Out>
void writeCommandResult(Out& out, char const* const name, Command const command, CommandSnapshot const& r)
{
    (void)out.printf(
        "%s %s: %s, state %s\r\n",
        name,
        (command == Command::ON) ? "on" : ((command == Command::OFF) ? "off" : "reset"),
        HighSideSwitch::resultName(r.result),
        HighSideSwitch::stateName(r.state));
}

} // namespace console
} // namespace highside
