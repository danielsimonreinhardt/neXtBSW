// SYS-129 host test model: the fault input and the fault latch of one FTM as the reference manual describes them,
// for the path test of the hardware threshold (hwThreshold::pathTest). FTM_Type of the vendor header is plain RAM,
// so the model works in steps: the test calls step() from its sysDelayUs - a wait is the time in which the hardware
// moves on. Independent of the family code; written from [RM13] S32K1xx RM Rev. 13:
//   - fault input j of FTMn: SIM_FTMOPT0 FTMnFLTjSEL selects TRGMUX_FTMn SEL(j+1) (otherwise the pin, held inactive
//     here); the TRGMUX delivers CMP0_OUT for the code 0x0E and 0 for every other code used here (Figure 19-2)
//   - CMPO is 0 while the comparator is disabled (45.7.1); after a change of the comparator its output is valid one
//     step later (settling), and the fault input follows the output one step after that (synchronizer and filter,
//     Table 47-15) - so a check without both waits sees the old state
//   - a fault is detected only with fault control on (MODE[FAULTM] != 0), the input enabled (FLTCTRL FAULTjEN), the
//     timer clocked (SC[CLKS] != 0) and the input asserted with its polarity (FLTPOL, 47.5.18.3); FAULTFj latches,
//     FAULTF is their OR, FAULTIN the OR of the asserted enabled inputs (47.4.3.17)
//   - clearing (47.4.3.17): 0 written to FAULTFj clears it only while input j is not asserted; 0 written to FAULTF
//     clears every FAULTFj while no enabled input is asserted; writing 1 has no effect; WPEN is kept
// Writes of the test itself (fakeFtm[n].FMS = ...) between two steps are taken as writes of the software.
// Include it after the code under test (it needs the register models of test/host/stubs/mcu/mcu.h).
#pragma once

#include <cstdint>

struct NxTestFtmFaultModel
{
    bool cmpoSettled;  // the comparator output as the fault input sees it after the settling step
    uint32_t asserted; // fault inputs asserted after synchronizer and filter (bit j)
    uint32_t latched;  // FAULTFj as the hardware holds them
    // fault injection of the test: inputs held asserted / never asserted whatever the trigger mux delivers
    uint32_t forceAsserted;
    uint32_t forceInactive;

    /** One wait. `cmpoNow`: the comparator output from the current C0/C1 (0 if disabled). */
    void step(
        FTM_Type& ftm,
        SIM_Type const& sim,
        NxTestTrgmux const& trgmux,
        uint32_t const ftmIndex,
        uint8_t const trgmuxIndex,
        bool const cmpoNow)
    {
        constexpr uint32_t FLAGS = 0x0FU;
        uint32_t const written   = ftm.FMS;
        bool const control       = ((ftm.MODE & FTM_MODE_FAULTM_MASK) != 0U) && ((ftm.SC & FTM_SC_CLKS_MASK) != 0U);
        uint32_t const enabled   = control ? (ftm.FLTCTRL & FLAGS) : 0U;
        // 1. clearing by the software, against the inputs as they are before this step
        if (((written & FTM_FMS_FAULTF_MASK) == 0U) && (latched != 0U) && ((asserted & enabled) == 0U))
        {
            latched = 0U;
        }
        uint32_t const clearRequest = latched & ~written & FLAGS;
        latched &= ~(clearRequest & ~asserted);
        // 2. the inputs follow the settled comparator output
        uint32_t inputs = 0U;
        for (uint32_t j = 0U; j < 3U; ++j)
        {
            bool fromMux = false;
            if ((sim.FTMOPT0 & (1UL << ((4U * ftmIndex) + j))) != 0U)
            {
                uint32_t const sel = (static_cast<uint32_t>(trgmux.TRGMUXn[trgmuxIndex]) >> (8U * (j + 1U))) & 0x7FU;
                fromMux            = (sel == 0x0EU) && cmpoSettled;
            }
            bool const active = fromMux != ((ftm.FLTPOL & (1UL << j)) != 0U);
            if (active)
            {
                inputs |= 1UL << j;
            }
        }
        asserted = (inputs | forceAsserted) & ~forceInactive;
        latched |= asserted & enabled;
        // 3. the comparator output settles for the next step
        cmpoSettled = cmpoNow;
        ftm.FMS     = (written & FTM_FMS_WPEN_MASK) | latched | ((latched != 0U) ? FTM_FMS_FAULTF_MASK : 0U)
                  | (((asserted & enabled) != 0U) ? FTM_FMS_FAULTIN_MASK : 0U);
    }
};
