#pragma once

/* Free running counter of the updater's time measurement (upd_micros), core architecture ARMv6-M (PLAT-28).
 *
 * ARMv6-M (Cortex-M0+) has neither DWT nor CoreDebug. A SysTick count is no substitute: the measured windows are
 * mostly flash erase time, and a sector erase runs with interrupts disabled (upd_flash_s32k1xx.c) - the ticks of
 * that time would be lost. The family port counts LPIT0 channel 0 instead, like the system time of the
 * application on ARMv6-M (PLAT-12, SystemTimer.cpp). Every s32k1xx derivative has the LPIT.
 *
 * Clock: the LPIT runs on FIRCDIV2 with divider 1, i.e. on FIRC. The updater keeps the reset clock, FIRC with
 * DIVCORE 1 (generated bl_board.h, BL_CORE_CLOCK_HZ from the derivative database), so the channel counts at
 * BL_CORE_CLOCK_HZ exactly like the DWT cycle counter of ARMv7-M and upd_micros() stays the same arithmetic.
 * [RM] S32K1xx Series Reference Manual Rev. 14: 27.6 p. 572 PCC selection of LPIT, FIRCDIV2_CLK = 011b; 28.3.15 SCG_FIRCDIV
 * FIRCDIV2 001 = divide by 1 (reset 000 = output disabled); 29.4 PCS only while CGC = 0; Table 48-4 LPIT init
 * (M_CEN, wait 4 peripheral clocks, then channel setup). At the reset value 000 no module uses FIRCDIV2 yet, so
 * enabling the output here cannot disturb anything (28.3.15 recommends changes with FIRC off - it is the core clock).
 *
 * The channel counts down from TVAL 0xFFFFFFFF and reloads, so (0xFFFFFFFF - CVAL) counts up and wraps at 2^32. */

#include "mcu/mcu.h"

#include <stdbool.h>
#include <stdint.h>

#define UPD_PCC_PCS_FIRCDIV2 3U
#define UPD_SCG_FIRCDIV_1    1U
#define UPD_LPIT_CHANNEL     0U

static inline void upd_cycle_counter_start(void)
{
    if ((IP_SCG->FIRCDIV & SCG_FIRCDIV_FIRCDIV2_MASK) != SCG_FIRCDIV_FIRCDIV2(UPD_SCG_FIRCDIV_1))
    {
        IP_SCG->FIRCDIV = (IP_SCG->FIRCDIV & ~SCG_FIRCDIV_FIRCDIV2_MASK) | SCG_FIRCDIV_FIRCDIV2(UPD_SCG_FIRCDIV_1);
    }
    IP_PCC->PCCn[PCC_LPIT_INDEX] = 0U;
    IP_PCC->PCCn[PCC_LPIT_INDEX] = PCC_PCCn_PCS(UPD_PCC_PCS_FIRCDIV2);
    IP_PCC->PCCn[PCC_LPIT_INDEX] |= PCC_PCCn_CGC_MASK;

    IP_LPIT0->MCR = LPIT_MCR_M_CEN_MASK;
    for (uint32_t volatile i = 0U; i < 4U; ++i)
    {
        /* 4 peripheral clocks for synchronisation; one pass takes several core cycles at the same clock */
    }
    IP_LPIT0->TMR[UPD_LPIT_CHANNEL].TCTRL = 0U; /* periodic counter, no trigger */
    IP_LPIT0->TMR[UPD_LPIT_CHANNEL].TVAL  = 0xFFFFFFFFUL;
    IP_LPIT0->TMR[UPD_LPIT_CHANNEL].TCTRL = LPIT_TMR_TCTRL_T_EN_MASK;
}

/* Counts up at BL_CORE_CLOCK_HZ, wraps at 2^32 */
static inline uint32_t upd_cycle_count(void) { return 0xFFFFFFFFUL - IP_LPIT0->TMR[UPD_LPIT_CHANNEL].CVAL; }

/* PLAT-86: the LPIT is no debug resource, a debugger that closes its session does not switch it off (unlike the
 * DWT of ARMv7-M, cycle_counter/dwt). Same interface as there, so that upd_micros.c needs no case per counter. */
static inline bool upd_cycle_counter_running(void) { return true; }

static inline void upd_cycle_counter_restart(void) {}
