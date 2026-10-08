/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

// PLAT-85: copy of external/openbsw/platforms/s32k1xx/bsp/bspClock/src/clockConfig.cpp with a bounded wait for
// every clock the driver starts (tools/openbswsync/copies.yaml, root bspClock-s32k1xx, status modified).
//
// Finding PLAT-72: sircStart(), fircStart(), soscStart() and spllStart() of the original wait without a limit for
// the valid flag, and so do the clock switches (while (SCG->CSR != cfg)). A missing or broken crystal therefore
// stopped the start-up in boardInit() - where the MCU watchdog is already disabled: the board stayed silent, and
// without an SBC watchdog for ever. Here every wait gives up after a budget of polls and the driver goes on
// without the clock that did not come:
//   - SIRC or FIRC not valid: no switch to it, the core stays on the clock it runs on;
//   - SOSC not valid: no clock monitor on it (its reset would repeat on every start), no SPLL;
//   - SPLL not locked: no clock monitor on it;
//   - the planned system clock source (_SCG_RCCR_) is not valid: SCG_RCCR is not written at all, the core stays
//     on FIRC (a write for an invalid source could switch later, silently);
//   - a switch the SCG does not confirm (SCG_CSR) ends after its budget.
// Nothing is stored in RAM (the driver runs before the RAM initialization): the result stays in the SCG, and
// nxClockCheck() (ClockCheck.cpp next to this file) reads it from there after the start-up - SOSCVLD, SPLLVLD,
// SIRCVLD, FIRCVLD and SCG_CSR against the clock plan.
//
// A normal start-up is not slower: every loop still ends on the first poll that sees the flag, with the same
// register writes in the same order and the same number of polls as the original; the only extra access is one read
// of the planned source's valid flag before SCG_RCCR is written (host test clock-config-s32k-*, trace comparison).
// Everything else is the original; the finding pllOff() without PLL_AVAILABLE (PLAT-11/PLAT-03) is still worked
// around by the forced include clock/nxSpllUnavailable.h (platforms/s32k1xx/CMakeLists.txt).

#include "clock/clockConfig.h"

#include <mcu/mcu.h>
// clang-format off
// needs to be included after mcu.h
#include <bsp/clock/boardClock.h>
// clang-format on

#include <cstdint>

#include <etl/infinite_loop.h>

#ifdef SCG_SPLLCSR_SPLLEN_MASK
#define PLL_AVAILABLE 1
#else
#define PLL_AVAILABLE 0
#endif

// internal primitives
namespace
{
// PLAT-85: budgets of the bounded waits, in polls. One poll takes at least one core cycle, and the core runs at most
// at the fastest clock of the family (S32K1xx HSRUN 112 MHz, [DS] S32K1xx Data Sheet Rev. 13 "Operating modes"), so
// a budget of POLLS_PER_US * t never gives up before t. In practice a poll (peripheral read, compare, count) takes
// several cycles at 48 MHz FIRC, so the real wait before giving up is about ten times longer - still bounded.
// Minimum waits against the data sheet ([DS] tables 29-34):
//   SIRC start-up  12.5 us max         -> 1 ms
//   FIRC start-up  5 us max            -> 1 ms
//   crystal (tcst) 1.5-2.5 ms typical  -> 20 ms (no maximum given; board layout dependent)
//   SPLL lock      150 us + 1075 / FSPLL_REF (<= 285 us at 8 MHz) -> 5 ms
//   clock switch   a few cycles of the old and the new clock      -> 1 ms
constexpr uint32_t POLLS_PER_US       = 112U;
constexpr uint32_t SIRC_START_POLLS   = 1000U * POLLS_PER_US;
constexpr uint32_t FIRC_START_POLLS   = 1000U * POLLS_PER_US;
constexpr uint32_t SOSC_START_POLLS   = 20000U * POLLS_PER_US;
#if PLL_AVAILABLE
constexpr uint32_t SPLL_START_POLLS = 5000U * POLLS_PER_US;
#endif
constexpr uint32_t CLOCK_SWITCH_POLLS = 1000U * POLLS_PER_US;

// PLAT-85: the core clock switch of the original (write SCG_RCCR until SCG_CSR shows it), bounded
bool switchSystemClock(uint32_t const cfg)
{
    uint32_t polls = CLOCK_SWITCH_POLLS;
    do
    {
        SCG->RCCR = cfg;
        if (SCG->CSR == cfg)
        {
            return true;
        }
    } while (--polls > 0U);
    return false;
}

void disablePeripheralClocks(void)
{
    PCC->PCCn[PCC_FTFC_INDEX]     = 0U;
    PCC->PCCn[PCC_DMAMUX_INDEX]   = 0U;
    PCC->PCCn[PCC_FlexCAN0_INDEX] = 0U;
#ifdef PCC_FlexCAN1_INDEX
    PCC->PCCn[PCC_FlexCAN1_INDEX] = 0U;
#endif
#ifdef PCC_FTM3_INDEX
    PCC->PCCn[PCC_FTM3_INDEX] = 0U;
#endif
#ifdef PCC_ADC1_INDEX
    PCC->PCCn[PCC_ADC1_INDEX] = 0U;
#endif
#ifdef PCC_FlexCAN2_INDEX
    PCC->PCCn[PCC_FlexCAN2_INDEX] = 0U;
#endif
    PCC->PCCn[PCC_LPSPI0_INDEX] = 0U;
#ifdef PCC_LPSPI1_INDEX
    PCC->PCCn[PCC_LPSPI1_INDEX] = 0U;
#endif
#ifdef PCC_LPSPI2_INDEX
    PCC->PCCn[PCC_LPSPI2_INDEX] = 0U;
#endif
#ifdef PCC_PDB1_INDEX
    PCC->PCCn[PCC_PDB1_INDEX] = 0U;
#endif
    PCC->PCCn[PCC_CRC_INDEX]  = 0U;
    PCC->PCCn[PCC_PDB0_INDEX] = 0U;
    PCC->PCCn[PCC_LPIT_INDEX] = 0U;
    PCC->PCCn[PCC_FTM0_INDEX] = 0U;
    PCC->PCCn[PCC_FTM1_INDEX] = 0U;
#ifdef PCC_FTM2_INDEX
    PCC->PCCn[PCC_FTM2_INDEX] = 0U;
#endif
    PCC->PCCn[PCC_ADC0_INDEX]   = 0U;
    PCC->PCCn[PCC_RTC_INDEX]    = 0U;
    PCC->PCCn[PCC_LPTMR0_INDEX] = 0U;
    PCC->PCCn[PCC_PORTA_INDEX]  = 0U;
    PCC->PCCn[PCC_PORTB_INDEX]  = 0U;
    PCC->PCCn[PCC_PORTC_INDEX]  = 0U;
    PCC->PCCn[PCC_PORTD_INDEX]  = 0U;
    PCC->PCCn[PCC_PORTE_INDEX]  = 0U;
    PCC->PCCn[PCC_FlexIO_INDEX] = 0U;
#ifdef PCC_EWM_INDEX
    PCC->PCCn[PCC_EWM_INDEX] = 0U;
#endif
    PCC->PCCn[PCC_LPI2C0_INDEX]  = 0U;
    PCC->PCCn[PCC_LPUART0_INDEX] = 0U;
    PCC->PCCn[PCC_LPUART1_INDEX] = 0U;
#ifdef PCC_LPUART2_INDEX
    PCC->PCCn[PCC_LPUART2_INDEX] = 0U;
#endif
#ifdef PCC_FTM4_INDEX
    PCC->PCCn[PCC_FTM4_INDEX] = 0U;
#endif
#ifdef PCC_FTM5_INDEX
    PCC->PCCn[PCC_FTM5_INDEX] = 0U;
#endif
    PCC->PCCn[PCC_CMP0_INDEX] = 0U;
#ifdef PCC_CMU0_INDEX
    PCC->PCCn[PCC_CMU0_INDEX] = 0U;
#endif
#ifdef PCC_CMU1_INDEX
    PCC->PCCn[PCC_CMU1_INDEX] = 0U;
#endif
#ifdef PCC_ENET_INDEX
    PCC->PCCn[PCC_ENET_INDEX] = 0U;
#endif
}

void enablePeripheralClocks(void)
{
    PCC->PCCn[PCC_FTFC_INDEX]     = 0xC0000000U;
    PCC->PCCn[PCC_DMAMUX_INDEX]   = 0xC0000000U;
    PCC->PCCn[PCC_FlexCAN0_INDEX] = 0xC0000000U;
#ifdef PCC_FlexCAN1_INDEX
    PCC->PCCn[PCC_FlexCAN1_INDEX] = 0xC0000000U;
#endif
#ifdef PCC_FTM3_INDEX
    PCC->PCCn[PCC_FTM3_INDEX] = CLOCK_SOURCE_FTM3;
    PCC->PCCn[PCC_FTM3_INDEX] = PCC->PCCn[PCC_FTM3_INDEX] | 0xC0000000U;
#endif
#ifdef PCC_ADC1_INDEX
    PCC->PCCn[PCC_ADC1_INDEX] = CLOCK_SOURCE_ADC1;
    PCC->PCCn[PCC_ADC1_INDEX] = PCC->PCCn[PCC_ADC1_INDEX] | 0xC0000000U;
#endif
#ifdef PCC_FlexCAN2_INDEX
    PCC->PCCn[PCC_FlexCAN2_INDEX] = 0xC0000000U;
#endif
    PCC->PCCn[PCC_LPSPI0_INDEX] = CLOCK_SOURCE_LPSPI0;
    PCC->PCCn[PCC_LPSPI0_INDEX] = PCC->PCCn[PCC_LPSPI0_INDEX] | 0xC0000000U;
#ifdef PCC_LPSPI1_INDEX
    PCC->PCCn[PCC_LPSPI1_INDEX] = CLOCK_SOURCE_LPSPI1;
    PCC->PCCn[PCC_LPSPI1_INDEX] = PCC->PCCn[PCC_LPSPI1_INDEX] | 0xC0000000U;
#endif
#ifdef PCC_LPSPI2_INDEX
    PCC->PCCn[PCC_LPSPI2_INDEX] = CLOCK_SOURCE_LPSPI2;
    PCC->PCCn[PCC_LPSPI2_INDEX] = PCC->PCCn[PCC_LPSPI2_INDEX] | 0xC0000000U;
#endif
#ifdef PCC_PDB1_INDEX
    PCC->PCCn[PCC_PDB1_INDEX] = 0xC0000000U;
#endif
    PCC->PCCn[PCC_CRC_INDEX]  = 0xC0000000U;
    PCC->PCCn[PCC_PDB0_INDEX] = 0xC0000000U;
    PCC->PCCn[PCC_LPIT_INDEX] = CLOCK_SOURCE_LPIT;
    PCC->PCCn[PCC_LPIT_INDEX] = PCC->PCCn[PCC_LPIT_INDEX] | 0xC0000000U;
    PCC->PCCn[PCC_FTM0_INDEX] = CLOCK_SOURCE_FTM0;
    PCC->PCCn[PCC_FTM0_INDEX] = PCC->PCCn[PCC_FTM0_INDEX] | 0xC0000000U;
    PCC->PCCn[PCC_FTM1_INDEX] = CLOCK_SOURCE_FTM1;
    PCC->PCCn[PCC_FTM1_INDEX] = PCC->PCCn[PCC_FTM1_INDEX] | 0xC0000000U;
#ifdef PCC_FTM2_INDEX
    PCC->PCCn[PCC_FTM2_INDEX] = CLOCK_SOURCE_FTM2;
    PCC->PCCn[PCC_FTM2_INDEX] = PCC->PCCn[PCC_FTM2_INDEX] | 0xC0000000U;
#endif
    PCC->PCCn[PCC_ADC0_INDEX]   = CLOCK_SOURCE_ADC0;
    PCC->PCCn[PCC_ADC0_INDEX]   = PCC->PCCn[PCC_ADC0_INDEX] | 0xC0000000U;
    PCC->PCCn[PCC_RTC_INDEX]    = 0xC0000000U;
    PCC->PCCn[PCC_LPTMR0_INDEX] = CLOCK_SOURCE_LPTMR0;
    PCC->PCCn[PCC_LPTMR0_INDEX] = PCC->PCCn[PCC_LPTMR0_INDEX] | 0xC0000000U;
    PCC->PCCn[PCC_PORTA_INDEX]  = 0xC0000000U;
    PCC->PCCn[PCC_PORTB_INDEX]  = 0xC0000000U;
    PCC->PCCn[PCC_PORTC_INDEX]  = 0xC0000000U;
    PCC->PCCn[PCC_PORTD_INDEX]  = 0xC0000000U;
    PCC->PCCn[PCC_PORTE_INDEX]  = 0xC0000000U;
    PCC->PCCn[PCC_FlexIO_INDEX] = CLOCK_SOURCE_FLEXIO;
    PCC->PCCn[PCC_FlexIO_INDEX] = PCC->PCCn[PCC_FlexIO_INDEX] | 0xC0000000U;
#ifdef PCC_EWM_INDEX
    PCC->PCCn[PCC_EWM_INDEX] = 0xC0000000U;
#endif
    PCC->PCCn[PCC_LPI2C0_INDEX]  = CLOCK_SOURCE_LPI2C0;
    PCC->PCCn[PCC_LPI2C0_INDEX]  = PCC->PCCn[PCC_LPI2C0_INDEX] | 0xC0000000U;
    PCC->PCCn[PCC_LPUART0_INDEX] = CLOCK_SOURCE_LPUART0;
    PCC->PCCn[PCC_LPUART0_INDEX] = PCC->PCCn[PCC_LPUART0_INDEX] | 0xC0000000U;
    PCC->PCCn[PCC_LPUART1_INDEX] = CLOCK_SOURCE_LPUART1;
    PCC->PCCn[PCC_LPUART1_INDEX] = PCC->PCCn[PCC_LPUART1_INDEX] | 0xC0000000U;
#ifdef PCC_LPUART2_INDEX
    PCC->PCCn[PCC_LPUART2_INDEX] = CLOCK_SOURCE_LPUART2;
    PCC->PCCn[PCC_LPUART2_INDEX] = PCC->PCCn[PCC_LPUART2_INDEX] | 0xC0000000U;
#endif
#ifdef PCC_FTM4_INDEX
    PCC->PCCn[PCC_FTM4_INDEX] = CLOCK_SOURCE_FTM4;
    PCC->PCCn[PCC_FTM4_INDEX] = PCC->PCCn[PCC_FTM4_INDEX] | 0xC0000000U;
#endif
#ifdef PCC_FTM5_INDEX
    PCC->PCCn[PCC_FTM5_INDEX] = CLOCK_SOURCE_FTM5;
    PCC->PCCn[PCC_FTM5_INDEX] = PCC->PCCn[PCC_FTM5_INDEX] | 0xC0000000U;
#endif
    PCC->PCCn[PCC_CMP0_INDEX] = 0xC0000000U;
#ifdef PCC_CMU0_INDEX
    PCC->PCCn[PCC_CMU0_INDEX] = 0xC0000000U;
#endif
#ifdef PCC_CMU1_INDEX
    PCC->PCCn[PCC_CMU1_INDEX] = 0xC0000000U;
#endif
#ifdef PCC_ENET_INDEX
    PCC->PCCn[PCC_ENET_INDEX] = CLOCK_SOURCE_ENET;
    PCC->PCCn[PCC_ENET_INDEX] = PCC->PCCn[PCC_ENET_INDEX] | 0xC0000000U;
#endif
}

// PLAT-85: bounded, true when the clock is valid
bool sircStart()
{
    uint32_t polls = SIRC_START_POLLS;
    do
    {
        SCG->SIRCCSR = SCG->SIRCCSR | SCG_SIRCCSR_SIRCEN_MASK;
        if (0 != (SCG->SIRCCSR & SCG_SIRCCSR_SIRCVLD_MASK))
        {
            return true;
        }
    } while (--polls > 0U);
    return false;
}

void sircStop()
{
    do
    {
        SCG->SIRCCSR = 0;
    } while (0 != (SCG->SIRCCSR & SCG_SIRCCSR_SIRCEN_MASK));
}

void sircConfig()
{
    SCG->SIRCCFG = SCG_SIRCCFG_RANGE_MASK;
    SCG->SIRCDIV = SCG_SIRCDIV_SIRCDIV2(_SIRCDIV2) | SCG_SIRCDIV_SIRCDIV1(_SIRCDIV1);
}

void sircSysClk()
{
    uint32_t const cfg = SCG_RCCR_SCS(2) | SCG_RCCR_DIVSLOW(1);
    (void)switchSystemClock(cfg); // PLAT-85: bounded
    SysTick->LOAD = 8000 - 1;
}

bool isSircSysClk() { return (SCG->SIRCCSR & SCG_SIRCCSR_SIRCSEL_MASK) != 0; }

bool isSircValid() { return (SCG->SIRCCSR & SCG_SIRCCSR_SIRCVLD_MASK) != 0; }

// PLAT-85: bounded, true when the clock is valid
bool fircStart()
{
    uint32_t polls = FIRC_START_POLLS;
    do
    {
        SCG->FIRCCSR = SCG->FIRCCSR | (SCG_FIRCCSR_FIRCEN_MASK | SCG_FIRCCSR_FIRCERR_MASK);
        if (0 != (SCG->FIRCCSR & SCG_FIRCCSR_FIRCVLD_MASK))
        {
            return true;
        }
    } while (--polls > 0U);
    return false;
}

void fircStop()
{
    do
    {
        SCG->FIRCCSR = SCG_FIRCCSR_FIRCERR_MASK;
    } while (0 != (SCG->FIRCCSR & SCG_FIRCCSR_FIRCEN_MASK));
}

void fircConfig()
{
    SCG->FIRCCFG = 0;
    SCG->FIRCDIV = SCG_FIRCDIV_FIRCDIV2(_FIRCDIV2) | SCG_FIRCDIV_FIRCDIV1(_FIRCDIV1);
}

void fircSysClk()
{
    uint32_t const cfg = SCG_RCCR_SCS(3) | SCG_RCCR_DIVSLOW(1);
    (void)switchSystemClock(cfg); // PLAT-85: bounded
    SysTick->LOAD = 48000 - 1;
}

bool isFircSysClk() { return (SCG->FIRCCSR & SCG_FIRCCSR_FIRCSEL_MASK) != 0; }

bool isFircValid() { return (SCG->FIRCCSR & SCG_FIRCCSR_FIRCVLD_MASK) != 0; }

#if PLL_AVAILABLE
// PLAT-85: bounded, true when the clock is valid
bool spllStart()
{
    uint32_t polls = SPLL_START_POLLS;
    do
    {
        SCG->SPLLCSR = SCG->SPLLCSR | (SCG_SPLLCSR_SPLLEN_MASK | SCG_SPLLCSR_SPLLERR_MASK);
        if (0 != (SCG->SPLLCSR & SCG_SPLLCSR_SPLLVLD_MASK))
        {
            return true;
        }
    } while (--polls > 0U);
    return false;
}

void spllStop()
{
    SCG->SPLLCSR = SCG->SPLLCSR & ~(SCG_SPLLCSR_SPLLCM_MASK | SCG_SPLLCSR_SPLLCMRE_MASK);
    do
    {
        SCG->SPLLCSR = SCG_SPLLCSR_SPLLERR_MASK;
    } while (0 != (SCG->SPLLCSR & SCG_SPLLCSR_SPLLEN_MASK));
}

void spllConfig()
{
    SCG->SPLLCFG = PLL_CONFIG;
    SCG->SPLLDIV = SCG_SPLLDIV_SPLLDIV2(_SPLLDIV2) | SCG_SPLLDIV_SPLLDIV1(_SPLLDIV1);
}

void spllSysClk()
{
    // assumption: _SCG_RCCR_ is configured for SPLL@80MHz
    (void)switchSystemClock(_SCG_RCCR_); // PLAT-85: bounded
    SysTick->LOAD = 80000 - 1;
}

void spllMon()
{
    SCG->SPLLCSR = SCG->SPLLCSR | (SCG_SPLLCSR_SPLLCM_MASK | SCG_SPLLCSR_SPLLCMRE_MASK);
}

bool isSpllSysClk() { return (SCG->SPLLCSR & SCG_SPLLCSR_SPLLSEL_MASK) != 0; }

bool isSpllValid() { return (SCG->SPLLCSR & SCG_SPLLCSR_SPLLVLD_MASK) != 0; }
#endif // PLL_AVAILABLE

// PLAT-85: bounded, true when the clock is valid
bool soscStart()
{
    uint32_t polls = SOSC_START_POLLS;
    do
    {
        SCG->SOSCCSR = SCG->SOSCCSR | (SCG_SOSCCSR_SOSCEN_MASK | SCG_SOSCCSR_SOSCERR_MASK);
        if (0 != (SCG->SOSCCSR & SCG_SOSCCSR_SOSCVLD_MASK))
        {
            return true;
        }
    } while (--polls > 0U);
    return false;
}

void soscStop()
{
    SCG->SOSCCSR = SCG->SOSCCSR & ~(SCG_SOSCCSR_SOSCCM_MASK | SCG_SOSCCSR_SOSCCMRE_MASK);
    do
    {
        SCG->SOSCCSR = SCG_SOSCCSR_SOSCERR_MASK;
    } while (0 != (SCG->SOSCCSR & SCG_SOSCCSR_SOSCEN_MASK));
}

void soscConfig()
{
    SCG->SOSCCFG = SCG_SOSCCFG_RANGE(3) | SCG_SOSCCFG_EREFS_MASK;
    SCG->SOSCDIV = SCG_SOSCDIV_SOSCDIV2(_SOSCDIV2) | SCG_SOSCDIV_SOSCDIV1(_SOSCDIV1);
}

void soscMon()
{
    SCG->SOSCCSR = SCG->SOSCCSR | (SCG_SOSCCSR_SOSCCM_MASK | SCG_SOSCCSR_SOSCCMRE_MASK);
}

bool isSoscSysClk() { return (SCG->SOSCCSR & SCG_SOSCCSR_SOSCSEL_MASK) != 0; }

bool isSoscValid() { return (SCG->SOSCCSR & SCG_SOSCCSR_SOSCVLD_MASK) != 0; }

// PLAT-85: source of the clock plan (_SCG_RCCR_ SCS) valid? Codes of SCG_RCCR[SCS]: 1 SOSC, 2 SIRC, 3 FIRC, 6 SPLL
bool isPlannedSourceValid()
{
    switch ((static_cast<uint32_t>(_SCG_RCCR_) & SCG_RCCR_SCS_MASK) >> SCG_RCCR_SCS_SHIFT)
    {
        case 1U: return isSoscValid();
        case 2U: return isSircValid();
        case 3U: return isFircValid();
#if PLL_AVAILABLE
        case 6U: return isSpllValid();
#endif
        default: return false;
    }
}

// PLAT-85: the planned system clock only when its source is valid, bounded
void rccrConfig()
{
    if (isPlannedSourceValid())
    {
        (void)switchSystemClock(_SCG_RCCR_);
    }
}

} // namespace

extern "C"
{
void configurPll()
{
    disablePeripheralClocks();

    // legacy
    RCM->SRIE = RCM->SRIE | static_cast<uint32_t>(0x8f);
    RCM->SSRS = static_cast<uint32_t>(0x1c);

    // PLAT-85: a clock that does not become valid is neither selected nor monitored; the start-up goes on with the
    // clock the core runs on, and nxClockCheck() reports what is missing.
    bool sircNeedsConfig = true;
    if (!isSircSysClk())
    {
        sircStop();
        sircConfig();
        if (sircStart())
        {
            sircSysClk();
        }
        sircNeedsConfig = false;
    }

    if (!isFircSysClk())
    {
        fircStop();
        fircConfig();
        if (fircStart())
        {
            fircSysClk();
        }
    }

    // PLAT-85: still on SIRC when FIRC did not start - SIRC cannot be stopped while it is the system clock
    if (sircNeedsConfig && !isSircSysClk())
    {
        sircStop();
        sircConfig();
        (void)sircStart();
    }

#if PLL_AVAILABLE && CPU_XTAL_CLK_HZ
    spllStop();
#endif

#ifdef CPU_XTAL_CLK_HZ
    soscStop();
    soscConfig();
    bool const soscValid = soscStart();
    if (soscValid)
    {
        soscMon();
    }
#endif

#if PLL_AVAILABLE && CPU_XTAL_CLK_HZ
    // the SPLL needs the SOSC as reference
    if (soscValid)
    {
        spllConfig();
        if (spllStart())
        {
            spllMon();
        }
    }
#endif

    rccrConfig();

    SCG->CLKOUTCNFG = SCG_CLKOUTCNFG_CLKOUTSEL(CLOCKoUT_SELECT);

    // legacy
    RCM->SRIE = _RCM_SRIE_;

    enablePeripheralClocks();
}

void fircOff()
{
    if (!isFircSysClk())
    {
        fircStop();
    }
    else
    {
        etl::infinite_loop(); // panic!
    }
}

void extOscOff()
{
    if (!isSoscSysClk())
    {
        soscStop();
    }
    else
    {
        etl::infinite_loop(); // panic!
    }
}

void pllOff()
{
    if (!isSpllSysClk())
    {
        spllStop();
    }
    else
    {
        etl::infinite_loop(); // panic!
    }
}

void systemClock2Sirc()
{
    if (isSircSysClk())
    {
        return; // nothing to do
    }

    if (!isSircValid())
    {
        sircStop();
        sircConfig();
        if (!sircStart())
        {
            return; // PLAT-85: stay on the current clock
        }
    }

    sircSysClk();
}

void systemClock2Firc()
{
    if (isFircSysClk())
    {
        return; // nothing to do
    }

    if (!isFircValid())
    {
        fircStop();
        fircConfig();
        if (!fircStart())
        {
            return; // PLAT-85: stay on the current clock
        }
    }

    fircSysClk();
}

void systemClock2Pll()
{
#if PLL_AVAILABLE
    if (isSpllSysClk())
    {
        return; // nothing to do
    }

    if (!isSpllValid())
    {
        spllStop();
        if (!isSoscValid())
        {
            soscStop();
            soscConfig();
            if (!soscStart())
            {
                return; // PLAT-85: stay on the current clock
            }
            soscMon();
        }
        spllConfig();
        if (!spllStart())
        {
            return; // PLAT-85: stay on the current clock
        }
        spllMon();
    }

    spllSysClk();
#else
    etl::infinite_loop(); // panic!
#endif // PLL_AVAILABLE
}
} // extern "C"
