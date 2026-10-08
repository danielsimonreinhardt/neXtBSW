// SYS-113 (follow-up of TOOL-131, decision 2026-09-29): the cause of a safety reset as BSW sources of the fault memory,
// reported after the next start (dem/StartReport.h).
//
// A safety reset (S1 + S2 of the safe supervisor, a fault exception, the MCU watchdog) restarts the ECU before the
// fault memory could store anything. The reset-loop limit of SYS-43 already keeps what is needed in no-init RAM and
// evaluates it once per start (ecu/safety/safeSupervisor ResetLoopLimit.h, resetloop::onStart): whether this start
// followed a safety reset (`counted`), how many there were in a row (`count`) and the cause of the last one
// (`lastCause`). The family port hands those three values to failedOf() below and feeds the result into a
// StartReport over EVENTS - nothing of the safety layer is included or written here (strict separation: recording
// only, the reaction was the supervisor's).
//
// BSW event names and numbers (neXtBSW range, block +0x05xx of tools/nxconfig/bswdtc.py, low byte = cause: the event
// number as `bsw_instance` of the DTC in diag.yaml (bswdtc.SAFETY_CAUSES), the four others fixed sub-types of PLAN):
//   safety.<event>         the supervisor's event that reset (SafeSupervisor::Event in lower case), nn = its number
//   safety.mcu_watchdog    0xFD  MCU watchdog reset (program-flow supervision SM-01, not the fast test of the start)
//   safety.fault_exception 0xFC  fault exception of the core (hard fault dump SM-17, or a core lockup)
//   safety.reset_other     0xFB  a safety reset whose cause has no DTC of its own in the project (fallback)
//   safety.reset_loop      0xFF  this start followed RESET_LOOP_MIN_COUNT or more safety resets in a row
// Events of the supervisor that never reset are left out, so a DTC of their name stays "not reported" (rtegen warns)
// instead of passing at every start: WATCHDOG_STARTUP_CHECK_FAILURE, ROM_CHECK_FAILURE and RESET_LOOP_LIMIT hold the
// safe state without a reset until a power cycle, which clears the record (SafeSupervisor::holdSafeState marks
// nothing); CHECK_REGISTER_ERROR enters and leaves S1 without a reset, RTE_TIMING_VIOLATION only logs, DEVICE_REPORT
// is S3 by construction (open points of the SYS-113 review). DEVICE_FAULT is left out as well: its name
// `safety.device_fault` is the degraded reaction SD reported as a level by the application (ecu/application
// SafetyFaultReport.h, NVM-08) - a reset after DEVICE_FAULT is reported as safety.reset_other.
//
// The cause codes are those of resetloop (ResetLoopLimit.h); the host test compares both tables with the safety
// headers (libs/bsw/dem/test/test_start_report.py), so neither can drift. Header only, family and board neutral.

#pragma once

#include "dem/StartReport.h"

#include <cstddef>
#include <cstdint>

namespace dem
{
namespace safetyreset
{
/** Cause codes of the reset-loop record (resetloop::CAUSE_*, safeSupervisor/ResetLoopLimit.h). */
constexpr uint32_t CAUSE_NONE            = 0U;
constexpr uint32_t CAUSE_WATCHDOG        = 1U;
constexpr uint32_t CAUSE_FAULT_EXCEPTION = 2U;
constexpr uint32_t CAUSE_EVENT           = 0x100U; // | number of SafeSupervisor::Event

/** A start after this many safety resets in a row reports safety.reset_loop (1 would be every safety reset). */
constexpr uint32_t RESET_LOOP_MIN_COUNT = 2U;

/** Index of the sources in EVENTS / CAUSES. */
constexpr size_t RESET_LOOP  = 0U;
constexpr size_t RESET_OTHER = 1U;
constexpr size_t FIRST_CAUSE = 2U;

/** BSW event names of the sources (diag.yaml dtcs[].event.bsw). */
constexpr char const* EVENTS[] = {
    "safety.reset_loop",
    "safety.reset_other",
    "safety.mcu_watchdog",
    "safety.fault_exception",
    "safety.safety_manager_sequence_deviation",
    "safety.safe_watchdog_sequence_deviation",
    "safety.safe_watchdog_configuration_error",
    "safety.safe_watchdog_service_deviation",
    "safety.mpu_unlocked_on_safety_manager_entry",
    "safety.mpu_locked_on_safety_manager_exit",
    "safety.integrity_event_dma_ecc_error",
    "safety.mpu_not_enabled",
    "safety.mpu_configuration_error",
    "safety.adc_reference_error",
    "safety.internal_supplies_error",
    "safety.lock_register_error",
    "safety.deadman_configuration_error",
    "safety.safety_cycle_period_violation",
    "safety.stack_overflow",
    "safety.rtos_assert",
    "safety.etl_assert",
};

/** Cause code of every source (same order as EVENTS); CAUSE_NONE for the two that are no cause. */
constexpr uint32_t CAUSES[] = {
    CAUSE_NONE,               // reset_loop
    CAUSE_NONE,               // reset_other
    CAUSE_WATCHDOG,           // mcu_watchdog
    CAUSE_FAULT_EXCEPTION,    // fault_exception
    CAUSE_EVENT | 0x00U,      // SAFETY_MANAGER_SEQUENCE_DEVIATION
    CAUSE_EVENT | 0x02U,      // SAFE_WATCHDOG_SEQUENCE_DEVIATION
    CAUSE_EVENT | 0x03U,      // SAFE_WATCHDOG_CONFIGURATION_ERROR
    CAUSE_EVENT | 0x04U,      // SAFE_WATCHDOG_SERVICE_DEVIATION
    CAUSE_EVENT | 0x05U,      // MPU_UNLOCKED_ON_SAFETY_MANAGER_ENTRY
    CAUSE_EVENT | 0x06U,      // MPU_LOCKED_ON_SAFETY_MANAGER_EXIT
    CAUSE_EVENT | 0x07U,      // INTEGRITY_EVENT_DMA_ECC_ERROR
    CAUSE_EVENT | 0x09U,      // MPU_NOT_ENABLED
    CAUSE_EVENT | 0x0AU,      // MPU_CONFIGURATION_ERROR
    CAUSE_EVENT | 0x0BU,      // ADC_REFERENCE_ERROR
    CAUSE_EVENT | 0x0CU,      // INTERNAL_SUPPLIES_ERROR
    CAUSE_EVENT | 0x0EU,      // LOCK_REGISTER_ERROR
    CAUSE_EVENT | 0x10U,      // DEADMAN_CONFIGURATION_ERROR
    CAUSE_EVENT | 0x11U,      // SAFETY_CYCLE_PERIOD_VIOLATION
    CAUSE_EVENT | 0x15U,      // STACK_OVERFLOW
    CAUSE_EVENT | 0x16U,      // RTOS_ASSERT
    CAUSE_EVENT | 0x17U,      // ETL_ASSERT
};

constexpr size_t SOURCES = sizeof(EVENTS) / sizeof(EVENTS[0]);
static_assert(SOURCES == (sizeof(CAUSES) / sizeof(CAUSES[0])), "one cause code per event name");
static_assert(SOURCES <= StartReport::MAX_SOURCES, "one bit per source");

/** Low byte of the DTC number (bsw_instance) of the sources that are no event of the supervisor. */
constexpr uint8_t INSTANCE_RESET_LOOP      = 0xFFU;
constexpr uint8_t INSTANCE_MCU_WATCHDOG    = 0xFDU;
constexpr uint8_t INSTANCE_FAULT_EXCEPTION = 0xFCU;
constexpr uint8_t INSTANCE_RESET_OTHER     = 0xFBU;

/**
 * Failed sources of this start (bit i = EVENTS[i]) from the reset-loop start result: \p counted this start followed a
 * safety reset, \p count safety resets in a row including it, \p lastCause the cause of the last one. A start that
 * did not follow a safety reset fails nothing - also when the record still holds an older cause (reset pin,
 * debugger or diagnostic reset after a safety reset keep the record).
 */
constexpr uint32_t failedOf(bool const counted, uint32_t const count, uint32_t const lastCause)
{
    if (!counted)
    {
        return 0U;
    }
    uint32_t failed = (count >= RESET_LOOP_MIN_COUNT) ? StartReport::bit(RESET_LOOP) : 0U;
    bool known      = false;
    for (size_t i = FIRST_CAUSE; i < SOURCES; ++i)
    {
        if ((CAUSES[i] != CAUSE_NONE) && (CAUSES[i] == lastCause))
        {
            failed |= StartReport::bit(i);
            known = true;
        }
    }
    // a cause outside the table (DEVICE_FAULT, a new event, a damaged record) is still a safety reset
    return known ? failed : (failed | StartReport::bit(RESET_OTHER));
}

/** Sources the fallback safety.reset_other stands for when their DTC is missing: every cause. */
constexpr uint32_t otherCovers()
{
    uint32_t covers = 0U;
    for (size_t i = FIRST_CAUSE; i < SOURCES; ++i)
    {
        covers |= StartReport::bit(i);
    }
    return covers;
}

/** The report of the safety reset of one ECU: names, fallback and the result of the start. */
class Report
{
public:
    constexpr Report() : _report(EVENTS, SOURCES) {}

    /** Once per start, after the reset-loop limit evaluated the record and before the first update(). */
    void setStart(bool const counted, uint32_t const count, uint32_t const lastCause)
    {
        _report.setFailed(failedOf(counted, count, lastCause));
        _report.setFallback(RESET_OTHER, otherCovers());
    }

    /** One cycle of a BSW task; \p memory: the fault memory (nullptr: none yet). */
    void update(IFaultMemory* const memory) { _report.update(memory); }

    /** The same with the registered fault memory of the ECU (dem::registry.memory). */
    void update() { _report.update(); }

    StartReport const& report() const { return _report; }

private:
    StartReport _report;
};

} // namespace safetyreset
} // namespace dem
