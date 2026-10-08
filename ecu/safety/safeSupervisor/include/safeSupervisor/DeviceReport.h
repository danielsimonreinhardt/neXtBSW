#pragma once

/* SYS-29 (gap G5 of doc/safety/concept.md, section 13.1; finding F5 of doc/safety/fmea.md): the context word of a
 * device report - the base-software half of the interface through which the device packages of stage 2 (SYS-28)
 * and the base-software services over device values (G2 plausibility, G3 age, G4 actuator read-back) report to the
 * safe supervisor. Format only: no driver logic, no dependency, so a driver package can include it without taking
 * any safety code along.
 *
 * Two event classes carry this word (safeSupervisor/SafeSupervisor.h), split by the reaction, not by the source
 * (decision of the user 2026-09-21, decision board `sys37-postfach-abschaltend`):
 * - DEVICE_REPORT: report only (S3). May come from any context through the package's own event box
 *   (safeSupervisor/DeviceReportBox.h, delivered by the safety task) or from a check in the safety task. It never
 *   switches anything off, whatever the fault class says.
 * - DEVICE_FAULT: switching-off reaction (S1 + S2, or S1 held), chosen per fault class in SafeSupervisor::handle().
 *   Only from the safety context: the detection itself has to run in the safety task (its own supervision there),
 *   never from a report that another task posted.
 *
 * Layout (bit 31 left):
 *   31..24  bus id: kind in 31..29 (BusKind), index in 28..24 - the position of the bus among the buses of that
 *           kind the project uses, in board order (the rule of tools/rtegen device_bus_id). NO_BUS for a device
 *           that hangs on no bus (pins, analogue path). Communication faults are correlated per bus id (F5).
 *   23..16  device instance, 0..255, assigned by the integration of the project (unique per ECU); SYS-117: the
 *           position of the device among the board devices the project integrates (tools/nxconfig/degraded.py)
 *   15..8   fault class (FaultClass)
 *    7..0   detail of the package (logged, never interpreted by the supervisor)
 * A word with bus kind INVALID or the reserved kind, or with an unknown fault class, is malformed: the box refuses
 * it, and the supervisor still reacts to it in the fail-safe direction of its event class. The all-zero word is
 * malformed on purpose (a forgotten field), and so are the box's own CONTEXT_LOST/CONTEXT_CORRUPTED words (fault
 * class 0xFF).
 */

#include <cstdint>

namespace safety
{
namespace device
{
/** Kind of the bus a device hangs on (bits 31..29 of the context word). */
enum class BusKind : uint8_t
{
    INVALID  = 0U, // never used by a well-formed word
    SPI      = 1U,
    I2C      = 2U,
    UART     = 3U, // UART, LIN
    CAN      = 4U,
    OTHER    = 5U,
    RESERVED = 6U,
    NONE     = 7U // the device hangs on no bus (NO_BUS)
};

/** Highest bus index per kind (5 bits). */
constexpr uint8_t BUS_INDEX_MAX = 31U;
/** Bus id of a device that hangs on no bus: kind NONE, index 31. */
constexpr uint8_t NO_BUS = 0xFFU;

/** SYS-194: device instance of a report that comes from a mechanism of the integration and belongs to no board device
 *  (the board devices count from 0, tools/nxconfig/degraded.py). Used with NO_BUS by the hold of the safe_off levels
 *  of the family port (fault class ACTUATOR_STATE, detail = the safe_off output's number of the console group out). */
constexpr uint8_t NO_DEVICE = 0xFFU;

/** Fault class (bits 15..8). The supervisor decides the reaction per class (SafeSupervisor::handle()). */
enum class FaultClass : uint8_t
{
    INVALID        = 0U,
    COMMUNICATION  = 1U,  // no answer, frame/CRC/counter error on the bus - correlated per bus (F5)
    CONFIGURATION  = 2U,  // the device's configuration was lost or deviates from what was written
    DEVICE_RESET   = 3U,  // the device restarted by itself
    IDENTITY       = 4U,  // wrong device or version: a reset cannot remove it
    SUPPLY         = 5U,  // supply of the device out of range
    TEMPERATURE    = 6U,  // over-temperature of the device
    OUTPUT         = 7U,  // output stage: short, over-current, open load
    MEASUREMENT    = 8U,  // measurement chain of the device
    INTERNAL       = 9U,  // failed self-test or internal fault of the device
    PLAUSIBILITY   = 10U, // G2: two independent paths disagree
    STALE          = 11U, // G3: a device value was not refreshed in time
    ACTUATOR_STATE = 12U  // G4: a bus-commanded actuator is on although commanded off
};

constexpr uint8_t FAULT_CLASS_LAST = static_cast<uint8_t>(FaultClass::ACTUATOR_STATE);

/** Bus id of the index-th used bus of `kind`; 0 (malformed) if the index does not fit or the kind is no bus. */
constexpr uint8_t busId(BusKind const kind, uint8_t const index)
{
    return ((index > BUS_INDEX_MAX) || (kind == BusKind::INVALID) || (kind == BusKind::RESERVED)
            || (kind == BusKind::NONE))
               ? uint8_t{0U}
               : static_cast<uint8_t>((static_cast<uint32_t>(kind) << 5U) | index);
}

constexpr BusKind busKind(uint8_t const bus) { return static_cast<BusKind>(bus >> 5U); }

constexpr uint8_t busIndex(uint8_t const bus) { return static_cast<uint8_t>(bus & BUS_INDEX_MAX); }

/** True for a bus id of a real bus (not NO_BUS, not malformed): only those are correlated. */
constexpr bool isBus(uint8_t const bus)
{
    return (busKind(bus) != BusKind::INVALID) && (busKind(bus) != BusKind::RESERVED)
           && (busKind(bus) != BusKind::NONE);
}

constexpr uint32_t context(
    uint8_t const bus, uint8_t const deviceInstance, FaultClass const fault, uint8_t const detail = 0U)
{
    return (static_cast<uint32_t>(bus) << 24U) | (static_cast<uint32_t>(deviceInstance) << 16U)
           | (static_cast<uint32_t>(fault) << 8U) | static_cast<uint32_t>(detail);
}

constexpr uint8_t busOf(uint32_t const word) { return static_cast<uint8_t>(word >> 24U); }

constexpr uint8_t deviceOf(uint32_t const word) { return static_cast<uint8_t>(word >> 16U); }

constexpr uint8_t faultOf(uint32_t const word) { return static_cast<uint8_t>(word >> 8U); }

constexpr uint8_t detailOf(uint32_t const word) { return static_cast<uint8_t>(word); }

/** A word the box accepts: a real bus or NO_BUS, and a known fault class. */
constexpr bool isWellFormed(uint32_t const word)
{
    return (isBus(busOf(word)) || (busOf(word) == NO_BUS)) && (faultOf(word) >= 1U)
           && (faultOf(word) <= FAULT_CLASS_LAST);
}

} // namespace device
} // namespace safety
