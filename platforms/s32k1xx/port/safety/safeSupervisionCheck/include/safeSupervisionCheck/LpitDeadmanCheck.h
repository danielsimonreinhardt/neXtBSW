/* SYS-41 (finding F12 of doc/safety/fmea.md, SM-06): configuration re-check of the debug-halt/stall deadman.
 *
 * The deadman (IO-32, LpitHaltDeadman in platforms/s32k1xx/port/main/src/integration/DebugHaltGuardSystem.h) is an
 * LPIT channel that keeps counting while the core is halted, retriggered by the BSP task every millisecond. Its
 * retrigger re-enables the channel (SETTEN), which heals a cleared T_EN, but nothing re-read the rest of its
 * configuration - a single corrupted field disabled or stretched it silently:
 * - MCR[M_CEN] cleared: the whole module stops, the channel never expires,
 * - MCR[DBG_EN] cleared: the counter stops while the core is halted - exactly the case it exists for,
 * - MIER bit of the channel cleared: the timer expires without an interrupt,
 * - TCTRL other than "32-bit periodic counter, no trigger": another mode or a trigger source changes the time,
 * - TVAL changed: the time-out is not the configured one (the retrigger reloads TVAL, so a changed TVAL is used),
 * - NVIC: interrupt disabled, or its priority changed (below the RTOS syscall boundary an RTOS critical section
 *   would delay the walk of the safe outputs table).
 * T_EN itself is not checked: the channel is off between CLRTEN and SETTEN of every retrigger, before the first
 * poll and after an expiry until the next poll - all of them legitimate.
 *
 * The expected values belong to the owner of the deadman, not to this module: the integration of a project with a
 * power stage provides them through deadmanExpectedConfiguration() (a strong definition next to the strong
 * LPIT0_Ch1_IRQHandler). A project without a deadman keeps the weak default here, which returns nullptr - then
 * there is nothing to check. The safety task calls the check in every cycle (SafetyManager::cyclic), so the check
 * does not depend on the BSP task that retriggers the deadman.
 */

#pragma once

#include <mcu/mcu.h>

#include <cstdint>

namespace safety
{
namespace supervision
{
/** Configuration of the deadman as its owner set it up. Constant data (flash), no RAM of the safety region. */
struct LpitDeadmanConfiguration
{
    uint32_t channel;       ///< LPIT channel of the deadman
    uint32_t timerValue;    ///< TVAL of the channel (period - 1 ticks)
    IRQn_Type irq;          ///< interrupt of the channel (shared LPIT vector on derivatives that have only one)
    uint32_t irqPriority;   ///< NVIC level as passed to SYS_SetPriority / NVIC_SetPriority
};

/** Deviating fields of the deadman configuration, as a bit mask (0 = configuration as expected). */
enum LpitDeadmanField : uint32_t
{
    DEADMAN_FIELD_MODULE_ENABLE  = 0x01U, ///< MCR[M_CEN] cleared
    DEADMAN_FIELD_DEBUG_ENABLE   = 0x02U, ///< MCR[DBG_EN] cleared
    DEADMAN_FIELD_IRQ_ENABLE     = 0x04U, ///< MIER bit of the channel cleared
    DEADMAN_FIELD_TIMER_CONTROL  = 0x08U, ///< TCTRL other than 32-bit periodic without trigger (T_EN ignored)
    DEADMAN_FIELD_TIMER_VALUE    = 0x10U, ///< TVAL differs
    DEADMAN_FIELD_NVIC_ENABLE    = 0x20U, ///< interrupt disabled in the NVIC
    DEADMAN_FIELD_NVIC_PRIORITY  = 0x40U, ///< NVIC priority differs
    DEADMAN_FIELD_CONFIGURATION  = 0x80U, ///< the expected configuration itself is implausible (channel number)
};

/**
 * The deadman's configuration of this project, or nullptr if the project has no deadman. Weak default in this
 * module (nullptr); the owner of the deadman provides the strong definition.
 */
LpitDeadmanConfiguration const* deadmanExpectedConfiguration();

/**
 * Compares the deadman registers with `expected`. Pure register read, no side effect.
 * \return mask of LpitDeadmanField (0 = as expected)
 */
uint32_t checkLpitDeadman(LpitDeadmanConfiguration const& expected);

} // namespace supervision
} // namespace safety
