// SPDX-License-Identifier: Apache-2.0
/* SYS-128 (safety review SYS-86 H5, 2026-09-30): detection of a hardware threshold path that is switched off,
 * reconfigured or stuck - family s32k1xx, header only, no state of its own except the findings latch below.
 *
 * Why: a comparator that is switched off never trips the timer - in disabled mode CMPO is 0 ([RM13] 45.7.1; C0[COUT]
 * reads INVT then, but the signal to the trigger mux is 0 in both directions of the threshold) - and before SYS-128
 * only the register check of the safety task (SM-22, one safety cycle, reaction S1) saw it. A comparator output that
 * is stuck was not seen at all. Three parts:
 *
 *  1. startTest(): once per start, **before the route exists** (TRGMUX SELn still 0, the timer not even configured,
 *     the pins not muxed): the comparator output is driven both ways with the DAC at VDDA - pin (+) against the DAC
 *     (-) must read "not above", the DAC (+) against the pin (-) must read "above". A stuck output fails one of the
 *     two. Nothing of it reaches the fault input, so there is no pulse on a power output. Assumption of use: at the
 *     start the pin is further below VDDA than the comparator's offset plus its hysteresis (data sheet: offset at most
 *     25 mV, hysteresis level 0) - a signal at the rail reads as "stuck at above" (fail-safe).
 *  2. configurationFindings() / faultControlIntact(): the registers of the path against the configuration -
 *     comparator on and configured, DAC, no round robin, route with its lock, fault source select, fault control of
 *     the timer. The same registers SM-22 checks, but read in the 1 ms poll of the power stage
 *     (integration/HwThresholdWatch.h) instead of once per safety cycle.
 *  3. Monitor::sample(): consistency of the output and the timer - the comparator says "above" on two samples in a
 *     row and the timer has not latched a fault: the path after the comparator is broken (NOT_LATCHED).
 *
 * SYS-129 (safety review SYS-86 H6) adds pathTest(): once per start, after the timer runs and before the route is
 * locked and any pin is muxed, the path behind the comparator (route, fault input, fault latch) is tripped once with
 * the comparator forced through the DAC and cleared again - see section 1b.
 *
 * The findings are kept in one word of ordinary RAM (findings in bits 15..0, their complement in bits 31..16, written
 * with one store): the driver writes the start test, the poll of the power stage writes what it finds, the safety
 * task reads it (SYS-130: it reports to the safe supervisor). A word that fails its check reads as LATCH_IMPLAUSIBLE
 * (fail-safe). The reaction is not decided here: the poll of the power stage forces the group inactive (as the
 * hardware path would), the safe supervisor decides the rest.
 *
 * Not detected by this header: an output stuck at "not above" that fails between two starts (only the start test
 * drives the output; at run time a stuck output can only be told from the signal by a second, independent
 * measurement of it - plausibility G2, SYS-31). Register facts: [RM13] 45.8.1-45.8.3 (CMP_C0/C1/C2, INPSEL/INNSEL
 * 00 = DAC, 01 = mux), 45.7.1 (disabled mode), 47.4.3.17 (FMS[FAULTF]).
 */
#pragma once

#include "hwThreshold/HwThreshold.h"
#include "mcu/mcu.h"

#include <cstdint>

namespace bios
{
namespace hwThreshold
{
/** Findings of the path (bits 15..0 of the latch word). */
enum Finding : uint32_t
{
    COMPARATOR_OFF          = 0x0001U, // C0[EN] clear: CMPO is 0, the threshold never trips
    COMPARATOR_MODE         = 0x0002U, // another bit of C0 deviates (speed, inversion, filter, window, sample, output)
    REFERENCE               = 0x0004U, // C1 deviates: DAC off, level, reference, inputs
    ROUND_ROBIN             = 0x0008U, // C2 round robin switched on
    ROUTE                   = 0x0010U, // TRGMUX selection of the fault input or its lock
    SOURCE                  = 0x0020U, // SIM_FTMOPT0: the fault input takes its pin again instead of the TRGMUX
    FAULT_CONTROL           = 0x0040U, // timer: FAULTM, FLTCTRL, FLTPOL or FAULTEN of the pairs
    NOT_LATCHED             = 0x0100U, // the comparator says "above" twice in a row, the timer has no fault latched
    START_STUCK_ABOVE       = 0x0200U, // start test: "above" where "not above" is forced
    START_STUCK_NOT_ABOVE   = 0x0400U, // start test: "not above" where "above" is forced
    PATH_FAULT_UNFORCED     = 0x0800U, // SYS-129 path test: a fault latched while the comparator was held "not above"
    PATH_NO_TRIP            = 0x1000U, // SYS-129 path test: comparator forced "above", no fault at the timer's input
    PATH_NO_CLEAR           = 0x2000U, // SYS-129 path test: the test's fault could not be cleared again
    LATCH_IMPLAUSIBLE       = 0x8000U  // the findings word itself failed its check
};

/** Every finding that SM-22's register list sees as well (the configuration part). */
constexpr uint32_t CONFIGURATION_FINDINGS
    = COMPARATOR_OFF | COMPARATOR_MODE | REFERENCE | ROUND_ROBIN | ROUTE | SOURCE | FAULT_CONTROL;

/** Writable configuration bits of CMP_C0 (without COUT and the write-one-to-clear flags CFR/CFF). */
constexpr uint32_t CMP_C0_CONFIG_MASK = CMP_C0_DMAEN_MASK | CMP_C0_IER_MASK | CMP_C0_IEF_MASK | CMP_C0_FPR_MASK
                                        | CMP_C0_SE_MASK | CMP_C0_WE_MASK | CMP_C0_PMODE_MASK | CMP_C0_INVT_MASK
                                        | CMP_C0_COS_MASK | CMP_C0_OPE_MASK | CMP_C0_EN_MASK | CMP_C0_FILTER_CNT_MASK
                                        | CMP_C0_OFFSET_MASK | CMP_C0_HYSTCTR_MASK;
/** Writable bits of CMP_C1. */
constexpr uint32_t CMP_C1_CONFIG_MASK
    = CMP_C1_INPSEL_MASK | CMP_C1_INNSEL_MASK | 0x00FF0000U /* CHN0..7 */ | CMP_C1_DACEN_MASK | CMP_C1_VRSEL_MASK
      | CMP_C1_PSEL_MASK | CMP_C1_MSEL_MASK | CMP_C1_VOSEL_MASK;
/** Round robin of CMP_C2 (prepare() writes C2 = 0). */
constexpr uint32_t CMP_C2_ROUND_ROBIN_MASK = CMP_C2_RRE_MASK | CMP_C2_RRIE_MASK;
/** Fault control fields of FTM FLTCTRL (FAULTnEN, FFLTRnEN, FFVAL, FSTATE) and FLTPOL. */
constexpr uint32_t FTM_FLTCTRL_CONFIG_MASK = 0x8FFFU;
constexpr uint32_t FTM_FLTPOL_CONFIG_MASK  = 0x0FU;

/** The fault control of the group's timer as the FTM configuration writes it (generated, hwThresholdConfig). */
struct FaultControl
{
    uint32_t mode;    // MODE[FAULTM]
    uint32_t faulten; // FAULTEN bits of COMBINE of the group's pairs
    uint32_t fltctrl; // FLTCTRL
    uint32_t fltpol;  // FLTPOL
};

// ------------------------------------------------------------------------------------------------ findings latch
/** Latch word: findings in bits 15..0, their complement in bits 31..16 (one store, so a reader sees both halves). */
constexpr uint32_t latchWordOf(uint32_t const findings)
{
    return (findings & 0xFFFFU) | ((~findings & 0xFFFFU) << 16U);
}

/** Ordinary RAM (not the protected RAM of the safety task): written by the driver and the power stage poll. */
inline uint32_t volatile findingsLatch = latchWordOf(0U);

/** Findings latched so far; LATCH_IMPLAUSIBLE (with the low half as read) if the word failed its check. */
inline uint32_t latchedFindings()
{
    uint32_t const word     = findingsLatch;
    uint32_t const findings = word & 0xFFFFU;
    return ((word >> 16U) == (~findings & 0xFFFFU)) ? findings : (findings | LATCH_IMPLAUSIBLE);
}

/** Adds `findings` to the latch (never removes one; only a reset clears it). */
inline void latchFindings(uint32_t const findings)
{
    uint32_t const kept = latchedFindings();
    findingsLatch       = latchWordOf(kept | findings);
}

// ------------------------------------------------------------------------------------------------ 1. start test
/** Busy wait in microseconds (sysDelayUs of the BSP; a model in the host test). */
using Wait = void (*)(uint32_t us);

/** C1 of the two test comparisons: DAC at the top of the VDDA ladder (VRSEL 0, VOSEL 255 -> VDDA), the input pin. */
constexpr uint32_t startTestC1(Config const& c, bool const dacOnPlus)
{
    uint32_t const input = (c.cmpC1 & CMP_C1_PSEL_MASK) >> CMP_C1_PSEL_SHIFT;
    uint32_t const dac   = CMP_C1_DACEN_MASK | CMP_C1_VOSEL(0xFFU);
    return dacOnPlus ? (dac | CMP_C1_INPSEL(0U) | CMP_C1_INNSEL(1U) | CMP_C1_MSEL(input))
                     : (dac | CMP_C1_INPSEL(1U) | CMP_C1_INNSEL(0U) | CMP_C1_PSEL(input));
}

/**
 * Start test of the comparator output (part 1 above). Call before prepare(), while the fault input of the timer does
 * not take the comparator yet. Leaves the comparator switched off and C1/C2 at 0: prepare() configures it.
 * \return START_STUCK_ABOVE / START_STUCK_NOT_ABOVE, 0 if the output followed both comparisons
 */
inline uint32_t startTest(CMP_Type& cmp, Config const& c, Wait const wait)
{
    constexpr uint32_t ON = CMP_C0_EN_MASK | CMP_C0_PMODE_MASK; // high speed, not inverted, no filter, no window
    uint32_t findings     = 0U;
    cmp.C0                = 0U;
    cmp.C2                = 0U;
    // pin (+) against VDDA (-): "not above"
    cmp.C1 = startTestC1(c, false);
    cmp.C0 = ON;
    wait(c.settleUs);
    if ((cmp.C0 & CMP_C0_COUT_MASK) != 0U)
    {
        findings |= START_STUCK_ABOVE;
    }
    // VDDA (+) against the pin (-): "above"
    cmp.C0 = 0U;
    cmp.C1 = startTestC1(c, true);
    cmp.C0 = ON;
    wait(c.settleUs);
    if ((cmp.C0 & CMP_C0_COUT_MASK) == 0U)
    {
        findings |= START_STUCK_NOT_ABOVE;
    }
    cmp.C0 = 0U;
    cmp.C1 = 0U;
    return findings;
}

// ------------------------------------------------------------------------------------------------ 1b. path test
/** SYS-129: waits of settleUs in pathTest() (for the start-up time and the host test). */
constexpr uint32_t PATH_TEST_WAITS = 7U;

/** Fault input 0..2 of the timer that the route of `c` feeds (SEL1..SEL3), 3 if none. */
constexpr uint32_t faultInputOf(Config const& c)
{
    for (uint32_t input = 0U; input < 3U; ++input)
    {
        if (c.trgmuxSelMask == (0x7FUL << (8U * (input + 1U))))
        {
            return input;
        }
    }
    return 3U;
}

/*
 * SYS-129 (safety review SYS-86 H6, 2026-09-30): latent-fault test of the path behind the comparator - CMP0 output ->
 * TRGMUX -> fault input of the timer -> FMS[FAULTF] - once per start. The start test above proves only that the
 * comparator output can move; a broken route, a fault input that does not see it or a fault logic that does not
 * latch stayed latent until a real over-current.
 *
 * Preconditions (the PWM driver calls it so, ComplementaryPwm::init()): the timer is configured with its fault
 * control and its counter runs (the fault input is clocked), every output is masked (OUTMASK) and **no pin of the
 * group is muxed to the timer yet**, the trigger mux is not locked, the fault input takes its source from the
 * trigger mux (prepare()), the fault input is active high (FLTPOL 0, as generated). The forced fault therefore never
 * reaches a pin: the power stage is not switched.
 *
 *   A  comparator forced "not above" (pin (+) against VDDA (-)), then the route selected without its lock: no fault
 *      may latch (PATH_FAULT_UNFORCED: the fault input is active without the comparator - stuck or another source)
 *   B  comparator forced "above" (VDDA (+) against the pin (-)): FAULTF, the flag of exactly the configured fault
 *      input and FAULTIN must be set (PATH_NO_TRIP)
 *   C  comparator forced "not above" again, then the test's fault cleared ([RM13] 47.4.3.17: FMS read while set,
 *      0 written to the flag of the configured input only - 1 written to FAULTF and the other flags has no effect):
 *      FAULTF, the flag and FAULTIN must be 0 afterwards (PATH_NO_CLEAR)
 *   D  the route back to "trigger disabled" (still unlocked), the comparator configured as prepare() leaves it
 *
 * Every step that changes the comparator or the route is followed by two waits of settleUs: one for the comparator
 * and its DAC ([DS13] Table 44), one for the synchronizer and the filter of the fault input ((4 + FFVAL) FTM input
 * clocks, [RM13] Table 47-15 - the driver checks by static_assert that this is shorter than settleUs). With an
 * output stuck at either level the start test has already failed; the driver then does not run this test.
 * Same assumption of use as the start test (AoU-37): the pin is below VDDA by more than offset and hysteresis.
 * \return PATH_FAULT_UNFORCED / PATH_NO_TRIP / PATH_NO_CLEAR, 0 if the path tripped and cleared as configured
 */
inline uint32_t pathTest(CMP_Type& cmp, TRGMUX_Type& trgmux, FTM_Type& ftm, Config const& c, Wait const wait)
{
    constexpr uint32_t ON    = CMP_C0_EN_MASK | CMP_C0_PMODE_MASK; // as the start test
    constexpr uint32_t FLAGS = FTM_FMS_FAULTF0_MASK | FTM_FMS_FAULTF1_MASK | FTM_FMS_FAULTF2_MASK
                               | FTM_FMS_FAULTF3_MASK;
    uint32_t const flag      = 1UL << faultInputOf(c); // FAULTFn of the configured input
    uint32_t const seen      = FTM_FMS_FAULTF_MASK | flag | FTM_FMS_FAULTIN_MASK;
    uint32_t const disabled  = trgmux.TRGMUXn[c.trgmuxIndex] & ~c.trgmuxSelMask; // other SEL fields kept
    uint32_t findings        = 0U;

    // A: held "not above", route selected (not locked): nothing may latch
    cmp.C0 = 0U;
    cmp.C2 = 0U;
    cmp.C1 = startTestC1(c, false);
    cmp.C0 = ON;
    wait(c.settleUs);
    trgmux.TRGMUXn[c.trgmuxIndex] = disabled | c.trgmuxSel;
    wait(c.settleUs);
    if ((ftm.FMS & seen) != 0U)
    {
        findings |= PATH_FAULT_UNFORCED;
    }

    // B: forced "above": the timer latches a fault at exactly the configured input
    cmp.C0 = 0U;
    cmp.C1 = startTestC1(c, true);
    cmp.C0 = ON;
    wait(c.settleUs);
    wait(c.settleUs);
    if ((ftm.FMS & seen) != seen)
    {
        findings |= PATH_NO_TRIP;
    }

    // C: "not above" again, then the test's fault cleared; no fault may remain
    cmp.C0 = 0U;
    cmp.C1 = startTestC1(c, false);
    cmp.C0 = ON;
    wait(c.settleUs);
    wait(c.settleUs);
    uint32_t const status = ftm.FMS; // read while set: first half of the clearing sequence
    ftm.FMS               = (status & FTM_FMS_WPEN_MASK) | FTM_FMS_FAULTF_MASK | (FLAGS & ~flag);
    wait(c.settleUs);
    if ((ftm.FMS & seen) != 0U)
    {
        findings |= PATH_NO_CLEAR;
    }

    // D: route back to "trigger disabled" before the comparator changes, then the comparator as prepare() leaves it
    trgmux.TRGMUXn[c.trgmuxIndex] = disabled;
    cmp.C0                        = 0U;
    cmp.C2                        = 0U;
    cmp.C1                        = c.cmpC1;
    cmp.C0                        = c.cmpC0;
    return findings;
}

// ------------------------------------------------------------------------------------------------ 2. registers
/** The comparator, the route and the fault source select against the configuration (part 2 above). */
inline uint32_t configurationFindings(
    CMP_Type const& cmp, TRGMUX_Type const& trgmux, SIM_Type const& sim, Config const& c)
{
    uint32_t findings = 0U;
    uint32_t const c0 = cmp.C0;
    if ((c0 & CMP_C0_EN_MASK) == 0U)
    {
        findings |= COMPARATOR_OFF;
    }
    if (((c0 ^ c.cmpC0) & CMP_C0_CONFIG_MASK & ~CMP_C0_EN_MASK) != 0U)
    {
        findings |= COMPARATOR_MODE;
    }
    if (((cmp.C1 ^ c.cmpC1) & CMP_C1_CONFIG_MASK) != 0U)
    {
        findings |= REFERENCE;
    }
    if ((cmp.C2 & CMP_C2_ROUND_ROBIN_MASK) != 0U)
    {
        findings |= ROUND_ROBIN;
    }
    uint32_t const route = trgmux.TRGMUXn[c.trgmuxIndex];
    if (((route & c.trgmuxSelMask) != c.trgmuxSel) || ((route & TRGMUX_TRGMUXn_LK_MASK) == 0U))
    {
        findings |= ROUTE;
    }
    if ((sim.FTMOPT0 & c.simFaultSelect) != c.simFaultSelect)
    {
        findings |= SOURCE;
    }
    return findings;
}

/** The fault control of the group's timer as configured (part 2 above). */
inline bool faultControlIntact(FTM_Type const& ftm, FaultControl const& f)
{
    return ((ftm.MODE & FTM_MODE_FAULTM_MASK) == f.mode) && ((ftm.COMBINE & f.faulten) == f.faulten)
           && ((ftm.FLTCTRL & FTM_FLTCTRL_CONFIG_MASK) == f.fltctrl)
           && ((ftm.FLTPOL & FTM_FLTPOL_CONFIG_MASK) == f.fltpol);
}

// ------------------------------------------------------------------------------------------------ 3. consistency
/** Samples of the path at run time (part 3 above); one object per caller, it keeps only the run of "above". */
class Monitor
{
public:
    /** Samples of "above" without a latched fault that make NOT_LATCHED (two: one sample may race the filter). */
    static constexpr uint32_t NOT_LATCHED_SAMPLES = 2U;

    /** \return the findings of this sample (registers, fault control and NOT_LATCHED), 0 if the path is intact */
    uint32_t sample(
        CMP_Type const& cmp,
        TRGMUX_Type const& trgmux,
        SIM_Type const& sim,
        FTM_Type const& ftm,
        Config const& c,
        FaultControl const& f)
    {
        uint32_t findings = configurationFindings(cmp, trgmux, sim, c);
        if (!faultControlIntact(ftm, f))
        {
            findings |= FAULT_CONTROL;
        }
        uint32_t const c0 = cmp.C0;
        // "above" only from a comparator that is on (a disabled one reads INVT, COMPARATOR_OFF says that); the
        // timer is read after the comparator, so a crossing between the two reads is at most one sample
        bool const above = ((c0 & CMP_C0_EN_MASK) != 0U) && ((c0 & CMP_C0_COUT_MASK) != 0U);
        if (above && !faultDetected(ftm))
        {
            if (_aboveWithoutFault < NOT_LATCHED_SAMPLES)
            {
                ++_aboveWithoutFault;
            }
        }
        else
        {
            _aboveWithoutFault = 0U;
        }
        if (_aboveWithoutFault >= NOT_LATCHED_SAMPLES)
        {
            findings |= NOT_LATCHED;
        }
        return findings;
    }

private:
    uint32_t _aboveWithoutFault = 0U;
};

// ------------------------------------------------------------------------------------------------ SYS-130 report
/**
 * SYS-130: context word of the report to the safe supervisor (Event HW_SHUTOFF): bit 0 = the timer has latched a
 * fault (FMS[FAULTF], the threshold tripped), bits 23..8 = findings of the path, bits 31..24 = index of the timer
 * (FTMn). 0 while there is nothing to report.
 */
constexpr uint32_t reportContext(bool const faultLatched, uint32_t const findings, uint32_t const timer)
{
    return ((faultLatched || (findings != 0U)) ? ((timer & 0xFFU) << 24U) : 0U) | ((findings & 0xFFFFU) << 8U)
           | (faultLatched ? 1U : 0U);
}

/** Index of the timer whose fault input the route feeds (TRGMUX_FTM0_INDEX .. TRGMUX_FTM3_INDEX). */
constexpr uint32_t timerOf(Config const& c) { return static_cast<uint32_t>(c.trgmuxIndex - FIRST_FTM_INDEX); }

/**
 * SYS-130: the context of the report in the safety cycle - the timer's fault latch, the registers of the path read
 * again here (the power stage poll that also reads them may have stopped with its task), the fault control and the
 * findings latch (start test, consistency, the poll's findings). 0 while the path is intact and has not tripped.
 */
inline uint32_t shutoffContext(
    CMP_Type const& cmp,
    TRGMUX_Type const& trgmux,
    SIM_Type const& sim,
    FTM_Type const& ftm,
    Config const& c,
    FaultControl const& f)
{
    uint32_t findings = configurationFindings(cmp, trgmux, sim, c) | latchedFindings();
    if (!faultControlIntact(ftm, f))
    {
        findings |= FAULT_CONTROL;
    }
    return reportContext(faultDetected(ftm), findings, timerOf(c));
}

} // namespace hwThreshold
} // namespace bios
