/* SYS-51 host test stub of mcu/mcu.h: register models of the S32K1 LPIT (as deadmanStubs/mcu/mcu.h: SETTEN/CLRTEN
 * set and clear T_EN, MSR write one to clear), the NVIC functions of the deadman check, and the WDOG with its
 * reconfiguration rules ([RM] Rev. 13 23.3.2, "Reconfiguring the watchdog"):
 * - a write of the unlock word to CNT (32-bit command mode, CS[CMD32EN] = 1) clears CS[RCS] and sets CS[ULK] after
 *   ULK_DELAY_READS reads of CS (the unlock takes a few WDOG clocks);
 * - the unlock window lasts 128 bus clocks; every access to a WDOG register costs BUS_CLOCKS_PER_ACCESS of them. A
 *   write to CS or TOVAL outside an open window is a violation (on the chip: a reset);
 * - the write to CS completes the reconfiguration: ULK clears, FLG is write one to clear, RCS sets after
 *   RCS_DELAY_READS reads;
 * - any other word written to CNT than the unlock and the refresh sequence is a violation.
 * Each write records whether all interrupts were suspended (test_trace::lockDepth of the stub lock).
 * Names and masks of external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/S32K148_LPIT.h and
 * _WDOG.h (identical on every S32K1 derivative). */

#pragma once

#include <cstdint>

namespace test_trace
{
extern int lockDepth;
}

#define __NVIC_PRIO_BITS 4U

/* ------------------------------------------------------------------------------------------------ LPIT */

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
    WDOG_EWM_IRQn   = 22,
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

/* ------------------------------------------------------------------------------------------------ WDOG */

#define WDOG_CS_STOP_MASK    (0x1U)
#define WDOG_CS_WAIT_MASK    (0x2U)
#define WDOG_CS_DBG_MASK     (0x4U)
#define WDOG_CS_TST_MASK     (0x18U)
#define WDOG_CS_UPDATE_MASK  (0x20U)
#define WDOG_CS_INT_MASK     (0x40U)
#define WDOG_CS_EN_MASK      (0x80U)
#define WDOG_CS_CLK_MASK     (0x300U)
#define WDOG_CS_RCS_MASK     (0x400U)
#define WDOG_CS_ULK_MASK     (0x800U)
#define WDOG_CS_PRES_MASK    (0x1000U)
#define WDOG_CS_CMD32EN_MASK (0x2000U)
#define WDOG_CS_FLG_MASK     (0x4000U)
#define WDOG_CS_WIN_MASK     (0x8000U)

#define FEATURE_WDOG_UNLOCK_VALUE  (0xD928C520U)
#define FEATURE_WDOG_TRIGGER_VALUE (0xB480A602U)

struct WdogModel
{
    static constexpr uint32_t WINDOW_BUS_CLOCKS     = 128U;
    static constexpr uint32_t BUS_CLOCKS_PER_ACCESS = 4U; // a peripheral bridge access, generous

    uint32_t cs        = 0U;
    uint32_t toval     = 0U;
    uint32_t win       = 0U;
    uint32_t ulkDelay  = 2U;    // reads of CS until ULK sets after the unlock (0xFFFFFFFF: never)
    uint32_t rcsDelay  = 2U;    // reads of CS until RCS sets after the CS write (0xFFFFFFFF: never)
    bool windowOpen    = false; // unlock written, reconfiguration not yet completed
    uint32_t windowUsed = 0U;   // bus clocks since the unlock
    uint32_t pendingUlk = 0U;
    uint32_t pendingRcs = 0U;
    bool rcsPending     = false;
    // trace
    uint32_t unlocks          = 0U;
    uint32_t csWrites         = 0U;
    uint32_t tovalWrites      = 0U;
    uint32_t violations       = 0U;
    uint32_t writesUnlocked   = 0U; // writes done without all interrupts suspended
    uint32_t lastCsWritten    = 0U;
    uint32_t windowAtCsWrite  = 0U;

    void access()
    {
        if (windowOpen)
        {
            windowUsed += BUS_CLOCKS_PER_ACCESS;
        }
    }

    void noteLock()
    {
        if (::test_trace::lockDepth <= 0)
        {
            ++writesUnlocked;
        }
    }

    uint32_t readCs()
    {
        access();
        if (windowOpen && ((cs & WDOG_CS_ULK_MASK) == 0U) && (pendingUlk != 0xFFFFFFFFU))
        {
            if (pendingUlk == 0U)
            {
                cs |= WDOG_CS_ULK_MASK;
            }
            else
            {
                --pendingUlk;
            }
        }
        if (rcsPending && (pendingRcs != 0xFFFFFFFFU))
        {
            if (pendingRcs == 0U)
            {
                cs |= WDOG_CS_RCS_MASK;
                rcsPending = false;
            }
            else
            {
                --pendingRcs;
            }
        }
        return cs;
    }

    void writeCs(uint32_t const v)
    {
        access();
        noteLock();
        ++csWrites;
        if (!windowOpen || ((cs & WDOG_CS_ULK_MASK) == 0U) || (windowUsed > WINDOW_BUS_CLOCKS))
        {
            ++violations;
            return;
        }
        windowAtCsWrite = windowUsed;
        lastCsWritten   = v;
        uint32_t const flag = ((v & WDOG_CS_FLG_MASK) != 0U) ? 0U : (cs & WDOG_CS_FLG_MASK); // write one to clear
        cs = (v & ~(WDOG_CS_FLG_MASK | WDOG_CS_ULK_MASK | WDOG_CS_RCS_MASK)) | flag;
        windowOpen = false;
        rcsPending = true;
        pendingRcs = rcsDelay;
    }

    void writeCnt(uint32_t const v)
    {
        access();
        noteLock();
        if ((v == FEATURE_WDOG_UNLOCK_VALUE) && ((cs & WDOG_CS_CMD32EN_MASK) != 0U))
        {
            ++unlocks;
            windowOpen = true;
            windowUsed = 0U;
            pendingUlk = ulkDelay;
            cs &= ~WDOG_CS_RCS_MASK;
        }
        else if ((v == FEATURE_WDOG_TRIGGER_VALUE) && ((cs & WDOG_CS_CMD32EN_MASK) != 0U))
        {
            // refresh
        }
        else
        {
            ++violations;
        }
    }

    void writeToval(uint32_t const v)
    {
        access();
        noteLock();
        ++tovalWrites;
        if (!windowOpen || (windowUsed > WINDOW_BUS_CLOCKS))
        {
            ++violations;
            return;
        }
        toval = v;
    }
};

extern WdogModel wdogModel;

/** Register proxy of one WDOG register: reads and writes go through the model's rules. */
template<int Index>
struct WdogRegister
{
    operator uint32_t() const
    {
        if (Index == 0)
        {
            return wdogModel.readCs();
        }
        wdogModel.access();
        return (Index == 2) ? wdogModel.toval : ((Index == 3) ? wdogModel.win : 0U);
    }

    WdogRegister& operator=(uint32_t const v)
    {
        if (Index == 0)
        {
            wdogModel.writeCs(v);
        }
        else if (Index == 1)
        {
            wdogModel.writeCnt(v);
        }
        else if (Index == 2)
        {
            wdogModel.writeToval(v);
        }
        else
        {
            ++wdogModel.violations; // WIN is not written by any code under test
        }
        return *this;
    }
};

struct WdogType
{
    WdogRegister<0> CS;
    WdogRegister<1> CNT;
    WdogRegister<2> TOVAL;
    WdogRegister<3> WIN;
};

extern WdogType wdogRegisters;

#define WDOG (&wdogRegisters)
