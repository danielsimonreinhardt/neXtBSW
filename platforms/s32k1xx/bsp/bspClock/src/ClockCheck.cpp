/********************************************************************************
 * Copyright (c) 2026 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

// PLAT-72: clock start-up check of the family s32k1xx (contract clock/nxClockCheck.h), same check as for stm32.
//
// PLAT-85: the clock driver of the family (clockConfig.cpp next to this file, a copy of the OpenBSW driver) waits
// with a time limit for every clock it starts and goes on without a clock that did not become valid - like the
// stm32 driver. It stores nothing (it runs before the RAM initialization); what it could not start is left in the
// SCG, and this check reads it there:
//   - SIRCVLD/FIRCVLD 0: an internal oscillator did not start (OSC_NOT_READY; the registers of the line show
//     SOSC and SPLL valid in that case),
//   - SOSCVLD 0: the crystal did not start (OSC_NOT_READY), the SPLL was not started either,
//   - SPLLVLD 0: the SPLL did not lock (PLL_NOT_LOCKED),
//   - SCG_CSR not the plan: the planned source was not valid (the driver then does not switch) or the switch was
//     not confirmed (SOURCE_MISMATCH).
// Beyond the start-up it also sees
//   - a later clock switch (low power idle, clock/systemClockSwitch.h) that left another source or divider,
//   - the clock monitors of SOSC and SPLL (CM with reset, enabled by the driver): a reset whose reason
//     RCM_SRS[LOC]/[LOL] would otherwise only appear as a number in the "main(RCM::SRS ...)" line.
//
// PLAT-72 (board 2026-10-02): the result also names the clock the core really runs from (source, pllInput), so the
// start-up line says SPLL on the crystal or a fallback to FIRC/SIRC instead of only the frequency.
//
// Registers in the result: [0] SCG_CSR, [1] SCG_SOSCCSR, [2] SCG_SPLLCSR (0 on a derivative without SPLL).

#include "clock/nxClockCheck.h"

#include "mcu/mcu.h"
// clang-format off
// included after mcu.h, in the same order as the OpenBSW clock driver
#include <bsp/clock/boardClock.h>
// clang-format on

// Same condition as the OpenBSW driver: the SPLL exists on the derivative and is started when there is a crystal
#ifdef SCG_SPLLCSR_SPLLEN_MASK
#define NX_CLOCK_CHECK_HAS_SPLL 1
#else
#define NX_CLOCK_CHECK_HAS_SPLL 0
#endif

namespace
{
// SCG_CSR[SCS] codes of the reference manual (the generated boardClock.h names them SYSTEMCLICK_SCS_*_CLK)
constexpr uint32_t SCS_SOSC = 1U;
constexpr uint32_t SCS_SIRC = 2U;
constexpr uint32_t SCS_FIRC = 3U;
constexpr uint32_t SCS_SPLL = 6U;

// Source and dividers of the run mode clock; CSR must read what the clock plan writes into RCCR
constexpr uint32_t CSR_CHECKED
    = SCG_CSR_SCS_MASK | SCG_CSR_DIVCORE_MASK | SCG_CSR_DIVBUS_MASK | SCG_CSR_DIVSLOW_MASK;

// Frequency of a system clock source in the clock plan, 0 if not derivable (source without plan value)
uint32_t sourceHz(uint32_t const scs)
{
    switch (scs)
    {
        case SCS_SOSC:
#ifdef NX_CLOCK_SOSC_HZ
            return NX_CLOCK_SOSC_HZ;
#else
            return 0U;
#endif
        case SCS_SIRC: return NX_CLOCK_SIRC_HZ;
        case SCS_FIRC: return NX_CLOCK_FIRC_HZ;
        case SCS_SPLL:
#ifdef NX_CLOCK_SPLL_HZ
            return NX_CLOCK_SPLL_HZ;
#else
            return 0U;
#endif
        default: return 0U;
    }
}

// PLAT-85: SIRC and FIRC run in every clock plan (the driver starts both, whatever the system clock is); they
// come first in the start order of the driver
bool internalOscillatorsValid()
{
    return ((SCG->SIRCCSR & SCG_SIRCCSR_SIRCVLD_MASK) != 0U) && ((SCG->FIRCCSR & SCG_FIRCCSR_FIRCVLD_MASK) != 0U);
}

// Source of the system clock from SCG_CSR[SCS] for the report line (the SPLL always runs from the crystal here)
uint32_t sourceOf(uint32_t const scs)
{
    switch (scs)
    {
        case SCS_SOSC: return NX_CLOCK_SOURCE_EXTERNAL;
        case SCS_SIRC: return NX_CLOCK_SOURCE_INTERNAL_SLOW;
        case SCS_FIRC: return NX_CLOCK_SOURCE_INTERNAL_FAST;
        case SCS_SPLL: return NX_CLOCK_SOURCE_PLL;
        default: return NX_CLOCK_SOURCE_UNKNOWN;
    }
}

uint32_t fault(uint32_t const csr, uint32_t const sosccsr, uint32_t const spllcsr)
{
    if (!internalOscillatorsValid())
    {
        return NX_CLOCK_CHECK_OSC_NOT_READY;
    }
#ifdef CPU_XTAL_CLK_HZ
    if ((sosccsr & SCG_SOSCCSR_SOSCVLD_MASK) == 0U)
    {
        return NX_CLOCK_CHECK_OSC_NOT_READY;
    }
#if NX_CLOCK_CHECK_HAS_SPLL
    if ((spllcsr & SCG_SPLLCSR_SPLLVLD_MASK) == 0U)
    {
        return NX_CLOCK_CHECK_PLL_NOT_LOCKED;
    }
#endif
#endif
    (void)sosccsr;
    (void)spllcsr;
    if ((csr & CSR_CHECKED) != (static_cast<uint32_t>(_SCG_RCCR_) & CSR_CHECKED))
    {
        return NX_CLOCK_CHECK_SOURCE_MISMATCH;
    }
    return NX_CLOCK_CHECK_OK;
}

// Loss of clock or of PLL lock: the reset reason of this start (monitor with reset enable) or a pending error flag
// (monitor without reset). The driver clears SOSCERR/SPLLERR while it starts the clocks, so after a monitor reset
// only RCM_SRS keeps the reason.
bool monitorEvent(uint32_t const sosccsr, uint32_t const spllcsr, uint32_t const srs)
{
    uint32_t resetMask = RCM_SRS_LOC_MASK;
#ifdef RCM_SRS_LOL_MASK
    resetMask |= RCM_SRS_LOL_MASK;
#endif
    bool event = ((srs & resetMask) != 0U) || ((sosccsr & SCG_SOSCCSR_SOSCERR_MASK) != 0U);
#if NX_CLOCK_CHECK_HAS_SPLL
    event = event || ((spllcsr & SCG_SPLLCSR_SPLLERR_MASK) != 0U);
#else
    (void)spllcsr;
#endif
    return event;
}

} // namespace

extern "C" void nxClockCheck(nxClockCheckResult* const result)
{
    uint32_t const csr     = SCG->CSR;
    uint32_t const sosccsr = SCG->SOSCCSR;
#if NX_CLOCK_CHECK_HAS_SPLL
    uint32_t const spllcsr = SCG->SPLLCSR;
#else
    uint32_t const spllcsr = 0U;
#endif
    uint32_t const divCore = ((csr & SCG_CSR_DIVCORE_MASK) >> SCG_CSR_DIVCORE_SHIFT) + 1U;

    result->fault         = fault(csr, sosccsr, spllcsr);
    result->monitor       = monitorEvent(sosccsr, spllcsr, RCM->SRS) ? NX_CLOCK_CHECK_MONITOR_EVENT
                                                                     : NX_CLOCK_CHECK_MONITOR_NONE;
    result->plannedCoreHz = NX_CLOCK_CORE_HZ;
    result->actualCoreHz  = sourceHz((csr & SCG_CSR_SCS_MASK) >> SCG_CSR_SCS_SHIFT) / divCore;
    result->registers[0]  = csr;
    result->registers[1]  = sosccsr;
    result->registers[2]  = spllcsr;
    result->source        = sourceOf((csr & SCG_CSR_SCS_MASK) >> SCG_CSR_SCS_SHIFT);
    result->pllInput      = (result->source == NX_CLOCK_SOURCE_PLL) ? NX_CLOCK_SOURCE_EXTERNAL : NX_CLOCK_SOURCE_UNKNOWN;
}
