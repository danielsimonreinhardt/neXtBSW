/* SYS-40 (finding F9 of doc/safety/fmea.md, SM-04): the independent time base of the safety task's period check on
 * the S32K1 family - the LPTMR as a free-running 16-bit counter on the 1 kHz LPO clock.
 *
 * Why this timer (variants weighed in doc/changelog.d/SYS-40.md):
 * - **Own peripheral, own oscillator.** The OS tick (SysTick) and the OpenBSW system time run on the core clock
 *   (DWT) or, on ARMv6-M, on LPIT channel 0 - the very time base whose faults the check must see. LPIT channel 1 is
 *   the deadman of the debug halt guard. The LPTMR is used by nothing else in neXtBSW; it is reserved for this
 *   mechanism from now on (its hardware trigger to TRGMUX must not be used by a project).
 * - **LPO1K_CLK** (LPTMR0_PSR[PCS] = 01, [RM13] Table 27-9 p. 574) comes from the always-on 128 kHz LPO of the PMC,
 *   the oscillator the MCU watchdog counts on. It needs no SCG divider and no PCC clock select, so it does not
 *   depend on the clock plan of the project, and no project can run its core from it - a wrong core or bus clock
 *   (crystal, PLL multiplier, divider) is always seen against it.
 * - **Stops while the core is halted** in time counter mode ([RM13] 49.5.4 p. 1574), like the watchdog - a debugger
 *   halt is not a period violation. (The LPIT keeps counting with MCR[DBG_EN], which the deadman sets.)
 * - **No interrupt, no vector:** only read by the safety task. The check therefore reports a slowed task with its
 *   first late cycle; a task that does not come back at all is SM-01's (watchdog).
 *
 * Tolerance of the clock: [DS] Rev. 13 Table 33 p. 41 gives F_LPO 113 ... 139 kHz for the S32K1xx and up to 141 kHz
 * for the S32K14xW, nominal 128 kHz. SYS-53: this range is a fact of the derivative (mcudb clock.lpo), no longer a
 * constant of this header; tools/nxconfig derives the limits in per mille of nominal from it, rounded outwards
 * (S32K144: 882 ... 1086), and the SafetyManager derives its bounds from those generated values
 * (NX_MODULE_SAFELIFECYCLE_DERIVED_LPO_MIN/MAX_PER_MILLE), so a healthy LPO anywhere in that range never trips the
 * check.
 *
 * Register access: PCC_LPTMR0[CGC] must be set, or every LPTMR access is a bus fault. The clock driver of the
 * family switches the gate on (mcudb clock.pcc LPTMR0); checkCycleTimer() reads the gate first and reports instead
 * of touching the timer. The LPTMR registers are reset only by POR/LVD ([RM13] 49.5.1): after a warm reset the
 * counter may still run with an old configuration, so startCycleTimer() disables it first, as the RM prescribes.
 * EEERAM commands affect LPTMR reads ([RM13] 49.1.1 note) - relevant once FlexRAM is used as EEPROM (NVM-02).
 */

#pragma once

#include <cstdint>

namespace safety
{
namespace supervision
{
/** Nominal tick of the counter: LPO1K_CLK. */
constexpr uint32_t CYCLE_TIMER_NOMINAL_HZ = 1000U;
/** Width of the counter (LPTMR CNR, 16 bit). */
constexpr uint32_t CYCLE_TIMER_MASK = 0xFFFFU;

/** Deviating fields of the cycle timer, as a bit mask (0 = as started). */
enum CycleTimerField : uint32_t
{
    CYCLE_TIMER_FIELD_CLOCK_GATE = 0x01U, ///< PCC_LPTMR0[CGC] cleared - the timer was not read
    CYCLE_TIMER_FIELD_ENABLE     = 0x02U, ///< CSR[TEN] cleared: the counter is stopped and reset
    CYCLE_TIMER_FIELD_MODE       = 0x04U, ///< CSR other than time counter, free running, no interrupt, no DMA
    CYCLE_TIMER_FIELD_PRESCALER  = 0x08U, ///< PSR other than LPO1K_CLK with prescaler bypass
    CYCLE_TIMER_FIELD_LPO1K      = 0x10U, ///< SIM_LPOCLKS[LPO1KCLKEN] cleared: the tick clock is off
};

/** Configures and starts the counter (safety task init). Disables it first; the counter starts at 0. */
void startCycleTimer();

/** Current counter value (CNR is written first to latch it, [RM13] 49.5.4). Only after checkCycleTimer() found the
 * clock gate set. */
uint32_t readCycleTimer();

/**
 * Compares the timer's configuration with what startCycleTimer() wrote. Pure register read, no side effect.
 * \return mask of CycleTimerField (0 = as started)
 */
uint32_t checkCycleTimer();

} // namespace supervision
} // namespace safety
