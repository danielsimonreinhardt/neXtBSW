// Host test of the hardware threshold of the family s32k1xx (SYS-86): hwThreshold/HwThreshold.h driven by the PWM
// driver bsp/io/outputPwm/ComplementaryPwm.h with the configuration bswgen generates for
// tools/bswgen/test/profiles/projects/testproject_hw_threshold (bridge on FTM3, DC link current above 40 A at CMP0_IN3
// on the supply reference 5000 mV, filter 200 ns). Register models of FTM0..3, CMP0, TRGMUX (LK behaviour) and SIM
// with the vendor layouts (test/host/stubs/mcu/mcu.h); OpenBSW ftm/Ftm.h unchanged; Io and OutputPwm are stubs.
//
// Expected values by hand from the board and the reference manual ([RM13] S32K1xx RM Rev. 13):
//   pin threshold 2500 mV + 40 A * 40 mV/A = 4100 mV; 4100 * 256 / 5000 = 209.92 -> VOSEL 208 (DAC 4082.03 mV)
//   CMP_C1 = INPSEL 01 (bit 27) | DACEN (bit 15) | VRSEL 0 (VDDA) | PSEL 3 (bits 13..11) | VOSEL 208 = 0x080098D0
//   CMP_C0 = EN (bit 8) | PMODE (bit 12) = 0x1100, no INVT (the pin trips above the DAC)
//   TRGMUX_FTM3 (index 13) SEL1 (fault input 0) = 0x0E (CMP0_OUT) -> 0x00000E00 | LK
//   SIM_FTMOPT0 FTM3FLTxSEL bit 0 = bit 12; FTM3 MODE FAULTM 10b, FLTCTRL FAULT0EN | FFLTR0EN | FFVAL 13 = 0x0D11,
//   COMBINE FAULTEN of the pairs 0..2 = 0x404040, FLTPOL 0
// The fault path model below (faultForcesOutputs) evaluates the registers the way the reference manual describes
// the hardware - independent of the generator - and is the counter-check that the generated values together switch
// at the DAC level and nowhere else.
// SYS-128: init() runs the start test of the comparator output first (two switch-ons with forced comparisons, two
// waits before the FTM is configured); the comparator model forms C0[COUT] at every read. The start test itself, the
// run-time checks and their fault injection are HwThresholdMonitorHostTest.cpp.
// SYS-129: after the counter runs, init() trips the path once with the forced comparator and clears it again (path
// test, seven waits with the route selected but not locked); the fault logic of FTM3 takes one step per wait
// (FtmFaultModel.h). Its findings and fault injection are HwThresholdMonitorHostTest.cpp; here the order around it.
// Build and run: tools/bswgen/test_bswgen_hwthreshold.py (with mutants).

#include "bsp/io/outputPwm/ComplementaryPwm.h"
// after the code under test: the model uses the register types of the stub mcu/mcu.h as that code sees them
#include "FtmFaultModel.h"

#include <cstdio>
#include <vector>

FTM_Type fakeFtm[4]            = {};
SIM_Type fakeSim               = {};
NxTestTrgmux fakeTrgmux        = {};
NxTestCmp fakeCmp0             = {};
NxTestCmpEnable cmpEnable      = {};
std::vector<NxTestCmpEnable> switchOns; // SYS-129: every switch-on (start test 2, prepare 1, path test 4)

NxTestCmpC0& NxTestCmpC0::operator=(uint32_t const written)
{
    // SYS-128: the last switch-on counts (the start test switches the comparator on twice before prepare())
    if ((written & CMP_C0_EN_MASK) != 0U)
    {
        cmpEnable
            = NxTestCmpEnable{true, fakeCmp0.C1, fakeFtm[3].FLTCTRL, fakeFtm[3].SC, cmpEnable.count + 1U};
        switchOns.push_back(cmpEnable);
    }
    value = written;
    return *this;
}

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

struct Delay
{
    uint32_t us;
    uint32_t trgmux; // TRGMUX_FTM3 while waiting: not routed yet
    uint32_t ftmSc;  // FTM3 SC while waiting: counter clocked
    uint32_t fltctrl;
};

struct PinCall
{
    uint16_t pin;
    uint32_t trgmux; // TRGMUX_FTM3 when the pin was switched to the FTM
    uint32_t fms;    // SYS-129: FMS of FTM3 then
};

NxTestFtmFaultModel faultModel{}; // SYS-129

std::vector<Delay> delays;
std::vector<PinCall> pinCalls;

constexpr uint8_t TRGMUX_FTM3 = 13U;

/** Comparator output as the hardware forms it from C0/C1 ([RM13] 45.8.1, 45.8.2): plus input from the multiplexer
 * (INPSEL 01) or the DAC (00), minus input likewise (INNSEL); DAC = Vin / 256 * (VOSEL + 1), Vin = VDDA (VRSEL 0)
 * or the bandgap (1); COUT inverted by INVT; a disabled comparator reads INVT. pinMilliMv: 1/1000 mV. */
bool cout(uint32_t const pinInput, uint64_t const pinMicroMv, uint64_t const vddaMicroMv)
{
    uint32_t const c0 = fakeCmp0.C0.value; // the written bits (reading C0 itself asks this model for COUT)
    uint32_t const c1 = fakeCmp0.C1;
    bool const invt   = (c0 & CMP_C0_INVT_MASK) != 0U;
    if ((c0 & CMP_C0_EN_MASK) == 0U)
    {
        return invt;
    }
    uint64_t const vin = ((c1 & CMP_C1_VRSEL_MASK) != 0U) ? 1000000ULL : vddaMicroMv;
    uint64_t const dac = ((c1 & CMP_C1_DACEN_MASK) != 0U) ? (vin * ((c1 & CMP_C1_VOSEL_MASK) + 1U)) / 256U : 0U;
    uint32_t const psel = (c1 & CMP_C1_PSEL_MASK) >> CMP_C1_PSEL_SHIFT;
    uint32_t const msel = (c1 & CMP_C1_MSEL_MASK) >> CMP_C1_MSEL_SHIFT;
    uint64_t const plus  = (((c1 & CMP_C1_INPSEL_MASK) >> CMP_C1_INPSEL_SHIFT) == 1U)
                               ? ((psel == pinInput) ? pinMicroMv : 0U)
                               : dac;
    uint64_t const minus = (((c1 & CMP_C1_INNSEL_MASK) >> CMP_C1_INNSEL_SHIFT) == 1U)
                               ? ((msel == pinInput) ? pinMicroMv : 0U)
                               : dac;
    return (plus > minus) != invt;
}

/** Fault input `input` of FTM3 ([RM13] Figure 47-1, 11.3.1.3, Figure 19-2): SIM selects the pin (FTM3_FLTx, held
 * inactive here) or TRGMUX_FTM3 SEL(input + 1), which delivers CMP0_OUT for the code 0x0E and 0 when disabled. */
bool faultInput(uint32_t const input, bool const comparatorOut)
{
    if ((fakeSim.FTMOPT0 & (1UL << (12U + input))) == 0U)
    {
        return false;
    }
    uint32_t const sel = (static_cast<uint32_t>(fakeTrgmux.TRGMUXn[TRGMUX_FTM3]) >> (8U * (input + 1U))) & 0x7FU;
    return (sel == 0x0EU) && comparatorOut;
}

/** Channels of pair `pair` forced to POL by FTM3's fault control ([RM13] 47.5.18): fault control on (FAULTM),
 * an enabled fault input asserted with its polarity (FLTPOL), FAULTEN of the pair. */
bool faultForcesPair(uint32_t const pair, uint64_t const pinMicroMv)
{
    FTM_Type const& f = fakeFtm[3];
    if ((f.MODE & FTM_MODE_FAULTM_MASK) == 0U)
    {
        return false;
    }
    // SYS-128: CMPO, the signal to the trigger mux, is 0 while the comparator is disabled ([RM13] 45.7.1) - only
    // C0[COUT] reads INVT then; before SYS-128 this model let a disabled, inverted comparator trip the fault
    bool const c = ((fakeCmp0.C0.value & CMP_C0_EN_MASK) != 0U) && cout(3U, pinMicroMv, 5000000ULL);
    bool fault   = false;
    for (uint32_t input = 0U; input < 3U; ++input)
    {
        bool const asserted = faultInput(input, c) != ((f.FLTPOL & (1UL << input)) != 0U);
        fault               = fault || (((f.FLTCTRL & (1UL << input)) != 0U) && asserted);
    }
    return fault && ((f.COMBINE & (FTM_COMBINE_FAULTEN0_MASK << (8U * pair))) != 0U);
}

/** DC link current in mA -> pin voltage in 1/1000 mV (board: 2500 mV + 40 mV/A). */
uint64_t pinForCurrent(uint64_t const milliAmpere) { return 2500000ULL + 40ULL * milliAmpere; }

/** SYS-128: the signal at CMP0_IN3 while init() runs (0 A: 2500 mV) - what the start test compares against VDDA. */
uint64_t const START_PIN_MICRO_MV = pinForCurrent(0U);
} // namespace

// SYS-128: C0[COUT] as the comparator forms it from the written C0/C1 and the pin (register model of mcu.h)
bool nxTestCout() { return cout(3U, START_PIN_MICRO_MV, 5000000ULL); }

void sysDelayUs(uint32_t const delay)
{
    delays.push_back(Delay{delay, fakeTrgmux.TRGMUXn[TRGMUX_FTM3], fakeFtm[3].SC, fakeFtm[3].FLTCTRL});
    // SYS-129: the fault logic of FTM3 moves on (CMPO is 0 while the comparator is disabled, [RM13] 45.7.1)
    bool const cmpo = ((fakeCmp0.C0.value & CMP_C0_EN_MASK) != 0U) && nxTestCout();
    faultModel.step(fakeFtm[3], fakeSim, fakeTrgmux, 3U, TRGMUX_FTM3, cmpo);
}

namespace bios
{
bsp::BspReturnCode Io::setDefaultConfiguration(uint16_t const io)
{
    pinCalls.push_back(PinCall{io, fakeTrgmux.TRGMUXn[TRGMUX_FTM3], fakeFtm[3].FMS});
    return bsp::BSP_OK;
}

bsp::BspReturnCode OutputPwm::setDynamicClient(uint16_t, uint16_t, IDynamicPwmClient*) { return bsp::BSP_OK; }

bsp::BspReturnCode OutputPwm::clrDynamicClient(uint16_t) { return bsp::BSP_OK; }
} // namespace bios

int main()
{
    using namespace bios;
    // generated values against the hand calculation
    CHECK(NX_HW_THRESHOLD_COUNT == 1);
    CHECK(hwThresholdConfig::CMP_C1 == 0x080098D0U);
    CHECK(hwThresholdConfig::CMP_C0 == 0x00001100U);
    CHECK(hwThresholdConfig::CONFIG.trgmuxIndex == TRGMUX_FTM3);
    CHECK(hwThresholdConfig::CONFIG.trgmuxSel == 0x00000E00U);
    CHECK(hwThresholdConfig::CONFIG.simFaultSelect == (1UL << 12U));
    CHECK(hwThresholdConfig::CONFIG.settleUs == 30U);
    CHECK(ftmPwm::CONFIGURATION.fltctrl == 0x0D11U);

    // other users of SIM_FTMOPT0 and of TRGMUX_FTM3 SEL0 (hardware trigger) must survive
    fakeSim.FTMOPT0                   = 0x00000020U; // FTM1 fault input 1 from TRGMUX (someone else's)
    fakeTrgmux.TRGMUXn[TRGMUX_FTM3]   = 0x00000016U; // SEL0: FTM0_INIT_TRIG as hardware trigger 0
    fakeCmp0.C2                       = 0xC0000000U; // round robin left on by someone: must be switched off
    ComplementaryPwm pwm;
    pwm.init();

    // step 1 before the FTM configuration: comparator switched on configured, FTM3 fault control not yet set
    // (SYS-129: the third switch-on is prepare()'s; the path test switches it on four more times, last as configured)
    CHECK(cmpEnable.seen);
    CHECK(cmpEnable.c1 == hwThresholdConfig::CMP_C1);
    CHECK((switchOns.size() == 7U) && (switchOns[2].c1 == hwThresholdConfig::CMP_C1));
    CHECK((switchOns.size() == 7U) && (switchOns[2].ftmFltctrl == 0U));
    CHECK(fakeCmp0.C0 == hwThresholdConfig::CMP_C0);
    CHECK(fakeCmp0.C1 == hwThresholdConfig::CMP_C1);
    CHECK(fakeCmp0.C2 == 0U);
    CHECK(fakeSim.FTMOPT0 == (0x00000020U | (1UL << 12U)));

    // FTM3: fault control on all channels with manual clearing, fault input 0 with filter, active high, FAULTEN
    FTM_Type const& f = fakeFtm[3];
    CHECK((f.MODE & FTM_MODE_FAULTM_MASK) == FTM_MODE_FAULTM(2U));
    CHECK(f.FLTCTRL == 0x0D11U);
    CHECK((f.FLTCTRL & FTM_FLTCTRL_FSTATE_MASK) == 0U);
    CHECK(f.FLTPOL == 0U);
    CHECK((f.COMBINE & 0x00404040U) == 0x00404040U);
    CHECK((f.COMBINE & 0xFF000000U) == 0U); // no pair 3

    // step 3: the wait comes after the counter runs with fault control and before the route
    // (SYS-128: the two waits of the start test come first, before the FTM is configured or clocked)
    // (SYS-129: then the seven waits of the path test with the counter clocked, the route selected but never locked)
    CHECK(cmpEnable.count == 7U);
    CHECK(hwThreshold::latchedFindings() == 0U); // both forced comparisons followed, the path tripped and cleared
    CHECK(delays.size() == 10U);
    for (size_t i = 0U; (i < 2U) && (i < delays.size()); ++i)
    {
        CHECK(delays[i].us >= 30U);
        CHECK((delays[i].ftmSc & FTM_SC_CLKS_MASK) == 0U);
        CHECK(delays[i].fltctrl == 0U);
        CHECK((delays[i].trgmux & TRGMUX_TRGMUXn_SEL1_MASK) == 0U);
    }
    for (size_t i = 2U; (i < 9U) && (i < delays.size()); ++i)
    {
        CHECK(delays[i].us >= 30U);
        CHECK((delays[i].ftmSc & FTM_SC_CLKS_MASK) != 0U);
        CHECK(delays[i].fltctrl == 0x0D11U);
        CHECK((delays[i].trgmux & TRGMUX_TRGMUXn_LK_MASK) == 0U);
    }
    CHECK((delays.size() == 10U) && ((delays[3].trgmux & TRGMUX_TRGMUXn_SEL1_MASK) == 0x00000E00U));
    CHECK((delays.size() == 10U) && (delays[9].us >= 30U));
    CHECK((delays.size() == 10U) && ((delays[9].ftmSc & FTM_SC_CLKS_MASK) != 0U));
    CHECK((delays.size() == 10U) && (delays[9].fltctrl == 0x0D11U));
    CHECK((delays.size() == 10U) && ((delays[9].trgmux & TRGMUX_TRGMUXn_SEL1_MASK) == 0U));
    // routed and locked, SEL0 untouched, the selection written before the lock (a model write after LK is ignored)
    uint32_t const trg = fakeTrgmux.TRGMUXn[TRGMUX_FTM3];
    CHECK(trg == (0x80000000U | 0x00000E00U | 0x00000016U));
    CHECK(fakeTrgmux.TRGMUXn[TRGMUX_FTM3].ignoredWrites == 0U);
    // every pin is switched to the FTM only after the route
    CHECK(pinCalls.size() == 6U);
    for (PinCall const& p : pinCalls)
    {
        CHECK(p.trgmux == trg);
        CHECK(p.fms == 0U); // SYS-129: the fault of the path test is cleared before any pin is muxed
    }

    // fault path, evaluated from the registers like the hardware: switches between 4082.03 and 4082.04 mV at the pin
    // (DAC 5000 * 209 / 256 = 4082.03125 mV), i.e. between 39550 and 39551 mA - never later than 40000 mA
    for (uint32_t pair = 0U; pair < 3U; ++pair)
    {
        CHECK(!faultForcesPair(pair, 4082031ULL));
        CHECK(faultForcesPair(pair, 4082032ULL));
        CHECK(!faultForcesPair(pair, pinForCurrent(39550U)));
        CHECK(faultForcesPair(pair, pinForCurrent(39551U)));
        CHECK(faultForcesPair(pair, pinForCurrent(40000U)));
        CHECK(!faultForcesPair(pair, pinForCurrent(0U)));
    }
    CHECK(!faultForcesPair(3U, pinForCurrent(60000U))); // no FAULTEN outside the group

    // once locked, a later write (a wild pointer, a second init) does not move the route
    fakeTrgmux.TRGMUXn[TRGMUX_FTM3] = 0U;
    CHECK(fakeTrgmux.TRGMUXn[TRGMUX_FTM3] == trg);

    // FAULTF read through the family function
    CHECK(!hwThreshold::faultDetected(f));
    fakeFtm[3].FMS = FTM_FMS_FAULTF_MASK;
    CHECK(hwThreshold::faultDetected(f));

    // structure check of the family (the static_assert of the generat uses it): every route of FTM0..3 SEL1..3
    hwThreshold::Config c = hwThresholdConfig::CONFIG;
    CHECK(hwThreshold::isValid(c));
    for (uint8_t idx = 10U; idx <= 13U; ++idx)
    {
        for (uint32_t input = 0U; input < 3U; ++input)
        {
            c.trgmuxIndex    = idx;
            c.trgmuxSelMask  = 0x7FUL << (8U * (input + 1U));
            c.trgmuxSel      = 0x0EUL << (8U * (input + 1U));
            c.simFaultSelect = 1UL << ((4U * (idx - 10U)) + input);
            CHECK(hwThreshold::isValid(c));
            c.simFaultSelect = 1UL << ((4U * (idx - 10U)) + ((input + 1U) % 3U)); // other input than SEL
            CHECK(!hwThreshold::isValid(c));
        }
    }
    c = hwThresholdConfig::CONFIG;
    c.trgmuxSel = 0x00000D00U; // TRGMUX_IN11, not CMP0_OUT
    CHECK(!hwThreshold::isValid(c));
    c = hwThresholdConfig::CONFIG;
    c.trgmuxSelMask = 0x0000007FU; // SEL0 = hardware trigger, no fault input
    c.trgmuxSel     = 0x0000000EU;
    CHECK(!hwThreshold::isValid(c));
    c = hwThresholdConfig::CONFIG;
    c.trgmuxIndex = 14U; // PDB0
    CHECK(!hwThreshold::isValid(c));
    c = hwThresholdConfig::CONFIG;
    c.cmpC1 &= ~CMP_C1_DACEN_MASK;
    CHECK(!hwThreshold::isValid(c));
    c = hwThresholdConfig::CONFIG;
    c.cmpC1 = (c.cmpC1 & ~CMP_C1_INNSEL_MASK) | CMP_C1_INNSEL(1U); // minus input from the mux, not the DAC
    CHECK(!hwThreshold::isValid(c));
    c = hwThresholdConfig::CONFIG;
    c.cmpC0 &= ~CMP_C0_EN_MASK;
    CHECK(!hwThreshold::isValid(c));
    c = hwThresholdConfig::CONFIG;
    c.cmpC0 |= CMP_C0_WE_MASK; // window: output frozen outside the window
    CHECK(!hwThreshold::isValid(c));
    c = hwThresholdConfig::CONFIG;
    c.settleUs = 0U;
    CHECK(!hwThreshold::isValid(c));

    std::printf("hardware threshold host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
