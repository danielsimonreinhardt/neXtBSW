/* SYS-95 host test stub of mcu/mcu.h for the deadman progress check (LpitDeadmanProgressHostTest.cpp): an LPIT whose
 * channel counters are models - every read of CVAL is one poll; a running channel counts down one tick every
 * `pollsPerTick` reads (the ratio of core to LPIT clock), a stopped clock keeps the value. The model can also disable
 * the channel after a number of reads (the deadman's interrupt after an expiry). Names and masks of
 * external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/S32K148_LPIT.h. */

#pragma once

#include <cstdint>

#define LPIT_TMR_COUNT 4u

#define LPIT_TMR_TCTRL_T_EN_MASK (0x1U)

struct LpitCounterModel
{
    uint32_t value       = 0xFFFFFFFFU;
    bool clockRunning    = true;
    uint32_t pollsPerTick = 1U;
    uint32_t reads       = 0U;
    uint32_t disableAfter = 0U; // > 0: TCTRL[T_EN] is cleared at this read (the deadman expired meanwhile)
    uint32_t* tctrl      = nullptr;

    operator uint32_t()
    {
        ++reads;
        if ((disableAfter != 0U) && (reads >= disableAfter) && (tctrl != nullptr))
        {
            *tctrl = *tctrl & ~LPIT_TMR_TCTRL_T_EN_MASK;
        }
        if (clockRunning && ((tctrl == nullptr) || ((*tctrl & LPIT_TMR_TCTRL_T_EN_MASK) != 0U))
            && ((reads % pollsPerTick) == 0U))
        {
            --value;
        }
        return value;
    }
};

struct LpitType
{
    uint32_t MCR;
    uint32_t MIER;

    struct
    {
        uint32_t TVAL;
        LpitCounterModel CVAL;
        uint32_t TCTRL;
    } TMR[LPIT_TMR_COUNT];
};

extern LpitType lpitRegisters;

#define LPIT0 (&lpitRegisters)

enum IRQn_Type : int
{
    LPIT0_Ch0_IRQn = 48,
    LPIT0_Ch1_IRQn = 49
};
