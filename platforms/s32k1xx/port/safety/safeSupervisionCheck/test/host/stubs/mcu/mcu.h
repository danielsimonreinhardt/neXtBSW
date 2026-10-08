/* SYS-41 host test stub of mcu/mcu.h: register models of the S32K1 LPIT and of the NVIC functions the deadman check
 * reads (CMSIS NVIC_GetEnableIRQ / NVIC_GetPriority, priority as the level passed to NVIC_SetPriority). Names and
 * masks of external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/S32K148_LPIT.h. */

#pragma once

#include <cstdint>

#define LPIT_TMR_COUNT 4u

struct LpitType
{
    uint32_t VERID;
    uint32_t PARAM;
    uint32_t MCR;
    uint32_t MSR;
    uint32_t MIER;
    uint32_t SETTEN;
    uint32_t CLRTEN;
    uint32_t RESERVED0;

    struct
    {
        uint32_t TVAL;
        uint32_t CVAL;
        uint32_t TCTRL;
        uint32_t RESERVED;
    } TMR[LPIT_TMR_COUNT];
};

extern LpitType lpitRegisters;

#define LPIT0 (&lpitRegisters)

#define LPIT_MCR_M_CEN_MASK       (0x1U)
#define LPIT_MCR_SW_RST_MASK      (0x2U)
#define LPIT_MCR_DOZE_EN_MASK     (0x4U)
#define LPIT_MCR_DBG_EN_MASK      (0x8U)
#define LPIT_TMR_TCTRL_T_EN_MASK  (0x1U)
#define LPIT_TMR_TCTRL_MODE_MASK  (0xCU)
#define LPIT_TMR_TCTRL_TSOT_MASK  (0x10000U)

enum IRQn_Type : int
{
    LPIT0_Ch0_IRQn = 48,
    LPIT0_Ch1_IRQn = 49,
    NVIC_MODEL_SIZE = 64
};

struct NvicModel
{
    bool enabled[NVIC_MODEL_SIZE];
    uint32_t level[NVIC_MODEL_SIZE];
};

extern NvicModel nvicModel;

inline uint32_t NVIC_GetEnableIRQ(IRQn_Type const irq) { return nvicModel.enabled[irq] ? 1U : 0U; }

inline uint32_t NVIC_GetPriority(IRQn_Type const irq) { return nvicModel.level[irq]; }
