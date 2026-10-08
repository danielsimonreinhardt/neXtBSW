/* SYS-117: the degraded reaction SD - report, switch the affected safe-output groups to their safe state, keep the
 * rest of the ECU running, no reset (doc/safety/partner-and-degraded.md, part B; decisions B1-B6 of the user,
 * 2026-09-29, all in their recommended variant).
 *
 * What lives here (family neutral, no register access, no dependency):
 * - the closed list of the events a project may map to SD (decision B2 (a), concept section 4.2) and, since SYS-135,
 *   of the reports outside SafeSupervisor::Event (HW_SHUTOFF) - a code fact of the
 *   supervisor, not a project value. tools/nxconfig/degraded.py reads NX_SAFE_DEGRADABLE_EVENTS below and refuses a
 *   mapping of any other event with its YAML path; SafeSupervisor.cpp refuses it once more at compile time
 *   (static_assert per generated mapping) and at run time (a mapping of another event is ignored, the event keeps its
 *   fail-safe reaction). Faults of the MCU itself - program flow, time base, memory, image, kernel, clock, supply,
 *   MPU, watchdog - are never on it;
 * - the record of the degraded groups in no-init RAM (decision B4 (a)): it survives every reset except a power-on,
 *   so a group that is degraded stays degraded when another event resets the ECU; its plausibility follows the
 *   reset-loop record of SYS-43 (key, check word over all fields);
 * - the layout of the mapping words that tools/nxconfig derives into integration/safeSupervisorConfiguration.h.
 *
 * The decision itself stays in SafeSupervisor::handle() (SSR-001). Nothing here is compiled into a project without a
 * degraded mapping: SafeSupervisor.cpp uses it only if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS is defined,
 * which tools/nxconfig does only for a project with `safety.degraded` entries.
 */

#pragma once

#include <cstdint>

/* The closed list of degradable events (decision B2 (a)). One X(<Event>) per event of SafeSupervisor::Event.
 * DEVICE_FAULT is degradable only for a well-formed word of a fault class other than IDENTITY, and on a bus the
 * supervisor regards as lost only if every device of that bus is mapped (checked in SafeSupervisor.cpp). The later
 * candidates of the concept (PARTNER_FAULT/PARTNER_LOST of SYS-115, the fast-loop events of ARCH-65, plausibility,
 * signal age and the active-short start test) join this list with the task that brings the event. */
#define NX_SAFE_DEGRADABLE_EVENTS(X) X(DEVICE_FAULT)

/* SYS-135 (safety review SYS-128/SYS-130, point C1, decision of the safety responsible 2026-09-30): the closed list of
 * the reports that reach the supervisor outside SafeSupervisor::Event and may be degraded. One X(<Report>, <id>) per
 * report; <id> is the number its mappings carry in the event field (above every event number - SafeSupervisor.cpp
 * checks that - and never EVENT_IMPLAUSIBLE). tools/nxconfig/degraded.py reads this list like the one above.
 * HW_SHUTOFF: the latched fault of a hardware shut-off path of a PWM group (safeSupervisor/HwShutoff.h). Its mapping
 * names the timer of the path as instance (bits 31..24 of the report's context word) and exactly the group of that
 * PWM group: a trip degrades only that group, no reset (the path stays latched, H1); a report with findings of the path
 * (bits 23..8), without context, or with an implausible record keeps S1 held. */
#define NX_SAFE_DEGRADABLE_REPORTS(X) X(HW_SHUTOFF, 0xFE)

namespace safety
{
namespace degraded
{
/** SYS-135: event field of a mapping of the report HW_SHUTOFF (NX_SAFE_DEGRADABLE_REPORTS). */
constexpr uint32_t REPORT_HW_SHUTOFF = 0xFEU;

/** Largest number of safe-output groups (bits of the RTE signal safety.degraded, uint8 at least). */
constexpr uint32_t MAX_GROUPS = 8U;
/** Largest number of mappings event -> groups of a project. */
constexpr uint32_t MAX_MAPPINGS = 16U;

/* One mapping, as tools/nxconfig derives it (NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_<n>):
 *   31..24  event id (SafeSupervisor::Event)
 *   23..16  device instance (DEVICE_FAULT: the instance of the context word, safeSupervisor/DeviceReport.h)
 *   15..8   bus id of that device (NO_BUS for a device on no bus)
 *    7..0   groups the event degrades, bit k = safety.output_groups[k]
 * ..._DEGRADED_MAPPING_<n>_BUS_GROUPS: the groups of every device on that bus if all of them are mapped (a lost bus
 * is a common cause of its devices, concept section 4.2), 0 otherwise - then a fault on a lost bus stays fail-safe. */
constexpr uint32_t mappingEvent(uint32_t const mapping) { return (mapping >> 24U) & 0xFFU; }

constexpr uint32_t mappingInstance(uint32_t const mapping) { return (mapping >> 16U) & 0xFFU; }

constexpr uint32_t mappingBus(uint32_t const mapping) { return (mapping >> 8U) & 0xFFU; }

constexpr uint32_t mappingGroups(uint32_t const mapping) { return mapping & 0xFFU; }

/** Mask of `count` groups (count <= MAX_GROUPS). */
constexpr uint32_t allGroupsOf(uint32_t const count) { return (count >= 32U) ? 0xFFFFFFFFU : ((1U << count) - 1U); }

/** Event field of a record that was implausible and is read as "all groups degraded". */
constexpr uint32_t EVENT_IMPLAUSIBLE = 0xFFFFFFFFU;

/**
 * The record in no-init RAM (SafeSupervisor.cpp defines it in the section NX_SAFE_DEGRADED_RECORD_SECTION). Plain words
 * only. Content after power-on is random; `check` tells a written record from that.
 */
struct Record
{
    uint32_t groups;  // degraded groups, bit k = safety.output_groups[k]
    uint32_t event;   // event id of the last degradation (EVENT_IMPLAUSIBLE: read as all groups)
    uint32_t context; // its context word (DEVICE_FAULT: the device context word)
    uint32_t check;   // checkOf(groups, event, context)
};

constexpr uint32_t RECORD_KEY = 0x53443137UL; // "SD17"

constexpr uint32_t rotl(uint32_t const value, uint32_t const bits)
{
    return (value << bits) | (value >> (32U - bits));
}

constexpr uint32_t checkOf(uint32_t const groups, uint32_t const event, uint32_t const context)
{
    return ~(RECORD_KEY ^ groups ^ rotl(event, 11U) ^ rotl(context, 21U));
}

/** Intact and naming only groups the project has (`allGroups`). */
inline bool isValid(Record const volatile& record, uint32_t const allGroups)
{
    uint32_t const groups = record.groups;
    return ((groups & ~allGroups) == 0U) && (record.check == checkOf(groups, record.event, record.context));
}

inline void write(Record volatile& record, uint32_t const groups, uint32_t const event, uint32_t const context)
{
    record.groups  = groups;
    record.event   = event;
    record.context = context;
    record.check   = checkOf(groups, event, context);
}

/** Power-on: nothing degraded. Writes before anything reads (no-init RAM with ECC is undefined after power-on). */
inline void clear(Record volatile& record) { write(record, 0U, 0U, 0U); }

/** Result of the start. */
struct StartResult
{
    uint32_t groups;  // groups degraded from the start on
    bool recordValid; // false: the record was implausible and is read as all groups (fail-safe)
};

/**
 * Once per start (SafeSupervisor::startResetLoopLimit, from main() of the family port before the BSP starts).
 * Power-on: cleared, nothing degraded - the only way back (decision B6 (a)). Any other start: the groups of the
 * record stay degraded; an implausible record reads as every group degraded (after a start without power-on only a
 * wild write can have made it implausible, concept section 4.5) and is written back so.
 */
inline StartResult onStart(Record volatile& record, bool const powerOn, uint32_t const allGroups)
{
    if (powerOn)
    {
        clear(record);
        return StartResult{0U, true};
    }
    if (!isValid(record, allGroups))
    {
        write(record, allGroups, EVENT_IMPLAUSIBLE, 0U);
        return StartResult{allGroups, false};
    }
    return StartResult{record.groups, true};
}

} // namespace degraded
} // namespace safety

/* SYS-140 (decision C4 of the user, 2026-09-29; doc/safety/partner-and-degraded.md 5.9): the device fault classes that
 * indicate a fault in a bridge - a switch that may itself be shorted, a shorted phase, a gate driver without its
 * supply or with an internal fault. An active short onto such a bridge can connect the DC link through one leg, so a
 * DEVICE_FAULT of one of these classes from a device bound to a PWM group (board pwm[].gate_driver) removes the active
 * short of that group for the power cycle before the supervisor walks any table (SafeSupervisor.cpp,
 * blockShortOnBridgeFault()): every walk of the reaction and every later one writes freewheel. A malformed DEVICE_FAULT
 * word cannot be attributed and removes the short of every group.
 *
 * A code fact of the supervisor, not a project value: tools/nxconfig/safestate.py reads this list and refuses every
 * active short while it is missing or lacks one of OUTPUT, SUPPLY, INTERNAL - a project cannot shorten it. The hardware
 * fault input of the timer (the second source of 5.9) forces freewheel by itself; the family port removes the short
 * when it sees it (S32K1: integration/HwThresholdWatch.h). A failed start test of the short removes it in the selection
 * service itself (nxSafeStateShortTestBegin/End). */
#define NX_SAFE_BRIDGE_FAULT_CLASSES(X) X(OUTPUT) X(SUPPLY) X(INTERNAL)

namespace safety
{
namespace bridge
{
/** Largest number of devices bound to a PWM group with an active short (derived words of a project). */
constexpr uint32_t MAX_DEVICES = 8U;

/* One device bound to a PWM group that allows an active short, as tools/nxconfig/safestate.py derives it
 * (NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICE_<n>, only for a project with an active short):
 *   15..8  device instance (the byte of the device context word, safeSupervisor/DeviceReport.h)
 *    7..0  selection groups of the device, bit g = the g-th used PWM group (hardFaultHandler: nxSafeStateSelection[g]) */
constexpr uint32_t deviceInstance(uint32_t const device) { return (device >> 8U) & 0xFFU; }

constexpr uint32_t deviceGroups(uint32_t const device) { return device & 0xFFU; }

/** Groups of a DEVICE_FAULT that cannot be attributed (malformed word): every group. */
constexpr uint32_t ALL_GROUPS = 0xFFFFFFFFU;
} // namespace bridge
} // namespace safety
