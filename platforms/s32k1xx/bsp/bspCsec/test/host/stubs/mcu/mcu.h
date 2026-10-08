// Host stub of mcu/mcu.h for the CSEc recovery test (NVM-13): FTFC FSTAT/FCNFG/FCSESTAT and SIM SDID/FCFG1 as far as
// src/CsecRecovery.cpp and flexnvm/FlexNvmChip.h use them. FSTAT answers from a hook (the CSEc model clears CCIF while
// a command runs). Masks as in the NXP headers.
// NVM-17: WDOG (CS, CNT, TOVAL, WIN) with CNT read and written through hooks (the watchdog model of the test counts
// simulated LPO time), SMC PMSTAT.

#pragma once

#include <cstdint>

struct FakeReg8
{
    uint8_t (*onRead)(FakeReg8 const&) = nullptr;
    uint8_t value                      = 0U;
    mutable uint32_t reads             = 0U;

    operator uint8_t() const
    {
        ++reads;
        return (onRead != nullptr) ? onRead(*this) : value;
    }
};

struct FakeFtfc
{
    FakeReg8 FSTAT;
    uint8_t FCNFG    = 0U;
    uint8_t FCSESTAT = 0U;
};

struct FakeSim
{
    uint32_t SDID  = 0U;
    uint32_t FCFG1 = 0xFFFFFFFFU;
};

// NVM-17: WDOG counter register: reads and writes go to the watchdog model of the test
struct FakeWdogCnt
{
    uint32_t (*onRead)()        = nullptr;
    void (*onWrite)(uint32_t)   = nullptr;

    operator uint32_t() const { return (onRead != nullptr) ? onRead() : 0U; }

    FakeWdogCnt& operator=(uint32_t const value)
    {
        if (onWrite != nullptr)
        {
            onWrite(value);
        }
        return *this;
    }
};

struct FakeWdog
{
    uint32_t CS = 0U;
    FakeWdogCnt CNT;
    uint32_t TOVAL = 0U;
    uint32_t WIN   = 0U;
};

struct FakeSmc
{
    uint32_t PMSTAT = 0x01U; // RUN
};

extern FakeFtfc fakeFtfc;
extern FakeSim fakeSim;
extern FakeWdog fakeWdog;
extern FakeSmc fakeSmc;

#define FTFC   (&fakeFtfc)
#define SIM    (&fakeSim)
#define WDOG   (&fakeWdog)
#define IP_SMC (&fakeSmc)

#define FEATURE_WDOG_TRIGGER_VALUE (0xB480A602U)
#define WDOG_CS_EN_MASK            (0x80U)
#define WDOG_CS_CLK_MASK           (0x300U)
#define WDOG_CS_CLK_SHIFT          (8U)
#define WDOG_CS_PRES_MASK          (0x1000U)
#define WDOG_CS_CMD32EN_MASK       (0x2000U)
#define WDOG_CS_WIN_MASK           (0x8000U)
#define SMC_PMSTAT_PMSTAT_MASK     (0xFFU)

#define FTFC_FSTAT_CCIF_MASK       (0x80U)
#define FTFC_FCNFG_EEERDY_MASK     (0x1U)
#define FTFC_FCNFG_RAMRDY_MASK     (0x2U)
#define FTFC_FCSESTAT_RIN_MASK     (0x20U)
#define SIM_FCFG1_DEPART_MASK      (0xF000U)
#define SIM_FCFG1_DEPART_SHIFT     (12U)
#define SIM_FCFG1_EEERAMSIZE_MASK  (0xF0000U)
#define SIM_FCFG1_EEERAMSIZE_SHIFT (16U)
#define SIM_SDID_FEATURES_MASK     (0xFFU)
#define SIM_SDID_FEATURES_SHIFT    (0U)
#define SIM_SDID_PACKAGE_MASK      (0xF00U)
#define SIM_SDID_PACKAGE_SHIFT     (8U)
#define SIM_SDID_REVID_MASK        (0xF000U)
#define SIM_SDID_REVID_SHIFT       (12U)
#define SIM_SDID_RAMSIZE_MASK      (0xF0000U)
#define SIM_SDID_RAMSIZE_SHIFT     (16U)
#define SIM_SDID_DERIVATE_MASK     (0xF00000U)
#define SIM_SDID_DERIVATE_SHIFT    (20U)
#define SIM_SDID_SUBSERIES_MASK    (0xF000000U)
#define SIM_SDID_SUBSERIES_SHIFT   (24U)
