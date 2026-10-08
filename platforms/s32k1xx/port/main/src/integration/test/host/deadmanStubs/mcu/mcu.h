/* SYS-48 host test stub of mcu/mcu.h: a register model of the S32K1 LPIT with the write semantics the deadman uses
 * (SETTEN/CLRTEN set and clear T_EN of the channels, MSR write one to clear) and of the NVIC functions of the
 * configuration check (CMSIS NVIC_GetEnableIRQ / NVIC_GetPriority, priority as the level passed to
 * NVIC_SetPriority). Names and masks of external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/
 * S32K148_LPIT.h. */

#pragma once

#include <cstdint>

#define LPIT_TMR_COUNT 4u

#define LPIT_MCR_M_CEN_MASK      (0x1U)
#define LPIT_MCR_SW_RST_MASK     (0x2U)
#define LPIT_MCR_DOZE_EN_MASK    (0x4U)
#define LPIT_MCR_DBG_EN_MASK     (0x8U)
#define LPIT_TMR_TCTRL_T_EN_MASK (0x1U)
#define LPIT_TMR_TCTRL_MODE_MASK (0xCU)
#define LPIT_TMR_TCTRL_TSOT_MASK (0x10000U)

/** One 32-bit register: reads the stored value, a write goes through the register's write rule. */
struct LpitRegister
{
    using Write = void (*)(LpitRegister&, uint32_t);

    uint32_t value = 0U;
    Write write    = nullptr; // nullptr: plain read/write register

    operator uint32_t() const { return value; }

    LpitRegister& operator=(uint32_t const v)
    {
        if (write != nullptr)
        {
            write(*this, v);
        }
        else
        {
            value = v;
        }
        return *this;
    }

    LpitRegister()                               = default;
    LpitRegister(LpitRegister const&)            = delete;
    LpitRegister& operator=(LpitRegister const&) = delete;
};

struct LpitType
{
    LpitRegister VERID;
    LpitRegister PARAM;
    LpitRegister MCR;
    LpitRegister MSR;
    LpitRegister MIER;
    LpitRegister SETTEN;
    LpitRegister CLRTEN;
    LpitRegister RESERVED0;

    struct
    {
        LpitRegister TVAL;
        LpitRegister CVAL;
        LpitRegister TCTRL;
        LpitRegister RESERVED;
    } TMR[LPIT_TMR_COUNT];
};

extern LpitType lpitRegisters;

#define LPIT0 (&lpitRegisters)

enum IRQn_Type : int
{
    LPIT0_Ch0_IRQn  = 48,
    LPIT0_Ch1_IRQn  = 49,
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
