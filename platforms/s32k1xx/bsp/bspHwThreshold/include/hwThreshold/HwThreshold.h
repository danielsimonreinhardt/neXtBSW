// SPDX-License-Identifier: Apache-2.0
/* SYS-86: hardware threshold of the family s32k1xx - comparator CMP0 -> TRGMUX_FTMx -> fault input of the FTM that
 * generates a PWM group (doc/safety/hardware-mechanisms.md section 2.2). When the signal at the comparator input
 * crosses the DAC level, the FTM forces the channels of the group to their POL values (inactive) in hardware:
 * no instruction, no interrupt, no ADC conversion is involved. Recovery is manual (FTM MODE[FAULTM] 10b): the
 * outputs stay forced until a reset.
 *
 * Header only, no state. The values come from the generated bsp/hwThreshold/hwThresholdConfiguration.h (tools/bswgen,
 * plan checked by tools/nxconfig/hwthreshold.py); the FTM part (MODE[FAULTM], COMBINE[FAULTENm], FLTCTRL, FLTPOL) is
 * in the FTM configuration of the group and written by Ftm::init(). The PWM driver calls the two steps in this order
 * (bsp/io/outputPwm/ComplementaryPwm.h init()):
 *
 *   1. prepare()  before the FTM is configured: comparator and DAC on, the fault input of the timer takes its
 *                 source from the trigger mux, which still delivers 0 (TRGMUX SELn = 0: "trigger disabled")
 *   2. Ftm::init() with fault control, Ftm::start() - the FTM input clock runs, the fault logic is clocked
 *   3. wait settleUs (comparator and DAC settled, data sheet), then route(): the comparator output reaches the fault
 *      input and the selection is locked until reset (TRGMUX LK). A threshold that is already exceeded now is a
 *      rising edge at the fault input: FAULTF latches and the outputs stay forced from the start.
 *
 * Register facts ([RM13] S32K1xx Reference Manual Rev. 13): CMP_C0/C1/C2 45.8 (S. 1289-1297); TRGMUX Figure 19-2
 * (S. 421, TRGMUX_FTMx SEL1..SEL3 = FTMx_FAULT0..2), 19.4 (LK: write once until reset); SIM_FTMOPT0
 * FTMxFLTxSEL 11.3.1.3 (S. 185); FTM FMS[FAULTF] 47.4.3.17 (S. 1387).
 */
#pragma once

#include "mcu/mcu.h"

#include <cstdint>

namespace bios
{
namespace hwThreshold
{
struct Config
{
    uint32_t cmpC0;          // CMP_C0: EN, PMODE, INVT (C0 is written last: the comparator starts configured)
    uint32_t cmpC1;          // CMP_C1: INPSEL mux, INNSEL DAC, DACEN, VRSEL, PSEL, VOSEL
    uint8_t trgmuxIndex;     // TRGMUX_FTMx_INDEX of the group's timer
    uint32_t trgmuxSelMask;  // SELn field of the fault input (n = fault input + 1)
    uint32_t trgmuxSel;      // CMP0_OUT in that field
    uint32_t simFaultSelect; // SIM_FTMOPT0 FTMxFLTxSEL bit of the fault input: source TRGMUX instead of the pin
    uint32_t settleUs;       // settling time of comparator and DAC before the output reaches the fault input
};

constexpr uint32_t CMP_OUT_SOURCE   = 0x0EU; // TRGMUX source code of CMP0_OUT ([RM13] Table 19-1, S. 425)
constexpr uint8_t FIRST_FTM_INDEX   = 10U;   // TRGMUX_FTM0_INDEX .. TRGMUX_FTM3_INDEX (device header S32K148_TRGMUX.h)
constexpr uint8_t LAST_FTM_INDEX    = 13U;
constexpr uint32_t SIM_FAULT_SELECT = 0x7777U; // FTM3FLTxSEL .. FTM0FLTxSEL of SIM_FTMOPT0

/** Structure of a configuration: a route from CMP0 to a fault input 0..2 of FTM0..FTM3 and a sensible comparator. */
constexpr bool isValid(Config const& c)
{
    uint32_t const inpsel = (c.cmpC1 & CMP_C1_INPSEL_MASK) >> CMP_C1_INPSEL_SHIFT;
    uint32_t const innsel = (c.cmpC1 & CMP_C1_INNSEL_MASK) >> CMP_C1_INNSEL_SHIFT;
    bool const comparator = ((c.cmpC0 & CMP_C0_EN_MASK) != 0U) && ((c.cmpC0 & CMP_C0_PMODE_MASK) != 0U)
                            && ((c.cmpC0 & (CMP_C0_SE_MASK | CMP_C0_WE_MASK | CMP_C0_OPE_MASK)) == 0U)
                            && ((c.cmpC1 & CMP_C1_DACEN_MASK) != 0U) && (inpsel == 1U) && (innsel == 0U)
                            && ((c.cmpC1 & 0x00FF0000U) == 0U); // no round-robin channel
    if (!comparator || (c.trgmuxIndex < FIRST_FTM_INDEX) || (c.trgmuxIndex > LAST_FTM_INDEX) || (c.settleUs == 0U))
    {
        return false;
    }
    for (uint32_t input = 0U; input < 3U; ++input)
    {
        uint32_t const shift = 8U * (input + 1U); // SEL1..SEL3 = fault input 0..2
        uint32_t const sim   = 1UL << ((4U * static_cast<uint32_t>(c.trgmuxIndex - FIRST_FTM_INDEX)) + input);
        if ((c.trgmuxSelMask == (0x7FUL << shift)) && (c.trgmuxSel == (CMP_OUT_SOURCE << shift))
            && (c.simFaultSelect == sim))
        {
            return true;
        }
    }
    return false;
}

/** Step 1, before the FTM is configured: comparator and DAC on; the fault input's source becomes the trigger mux. */
inline void prepare(CMP_Type& cmp, SIM_Type& sim, Config const& c)
{
    cmp.C0 = 0U; // off while the inputs and the DAC level change
    cmp.C2 = 0U; // no round robin
    cmp.C1 = c.cmpC1;
    cmp.C0 = c.cmpC0;
    sim.FTMOPT0 = sim.FTMOPT0 | c.simFaultSelect;
}

/** Step 3, with the FTM running and after settleUs: the comparator output reaches the fault input, locked. */
inline void route(TRGMUX_Type& trgmux, Config const& c)
{
    uint32_t const value = (trgmux.TRGMUXn[c.trgmuxIndex] & ~c.trgmuxSelMask) | c.trgmuxSel;
    trgmux.TRGMUXn[c.trgmuxIndex] = value;
    // LK is write once and freezes every SEL field of the register until the next reset: selection first
    trgmux.TRGMUXn[c.trgmuxIndex] = value | TRGMUX_TRGMUXn_LK_MASK;
}

/** The FTM has detected a fault (FMS[FAULTF]): the outputs of the pairs with FAULTEN are forced to POL. */
inline bool faultDetected(FTM_Type const& ftm) { return (ftm.FMS & FTM_FMS_FAULTF_MASK) != 0U; }

} // namespace hwThreshold
} // namespace bios
