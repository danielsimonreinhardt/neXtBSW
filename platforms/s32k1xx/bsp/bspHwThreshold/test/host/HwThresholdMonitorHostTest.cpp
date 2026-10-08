// Host test of SYS-128 (safety review SYS-86 H5): detection of a hardware threshold path that is switched off,
// reconfigured or stuck - hwThreshold/HwThresholdMonitor.h (start test, register check, consistency of the comparator
// output with the timer's fault latch, findings latch), called by the real PWM driver bsp/io/outputPwm/
// ComplementaryPwm.h (start test in init(), group forced after a failed one) and by the power stage poll
// integration/HwThresholdWatch.h (the fast check, group forced while anything is latched), with fault injection.
//
// Configuration: the generat of tools/bswgen/test/profiles/projects/testproject_hw_threshold (bridge on FTM3, DC link
// current above 40 A at CMP0_IN3 on the VDDA reference 5000 mV) - the same profile as HwThresholdHostTest.cpp.
// Register models of FTM0..3, CMP0, TRGMUX (LK) and SIM (test/host/stubs/mcu/mcu.h); the comparator model below forms
// C0[COUT] from the written C0/C1 and the pin like [RM13] 45.8.1/45.8.2 describe it, with an injectable stuck output.
//
// Expected values by hand ([RM13] 45.8.2: INPSEL/INNSEL 00 = DAC, 01 = mux; VOSEL 255 -> DAC = VDDA):
//   start test A, pin (+) against VDDA (-): INPSEL 01 (0x08000000) | DACEN (0x8000) | PSEL 3 (0x1800) | VOSEL 0xFF
//     = 0x080098FF, the comparator must read 0 (the pin is below VDDA)
//   start test B, VDDA (+) against the pin (-): INNSEL 01 (0x01000000) | DACEN | MSEL 3 (0x300) | VOSEL 0xFF
//     = 0x010083FF, the comparator must read 1
// SYS-130: the context word the safety cycle reports to the safe supervisor (hwThreshold::shutoffContext: FAULTF,
// the registers read again, the findings latch) - the reaction to it is SafeSupervisorHwShutoffHostTest.cpp.
// SYS-129: the path test behind the comparator (hwThreshold::pathTest, route -> fault input -> FAULTF tripped once and
// cleared at start) on the step model of the fault logic in FtmFaultModel.h (one step per sysDelayUs), with injected
// faults: fault input stuck asserted or dead, route locked before the test, fault control off, timer not clocked,
// fault source select on the pin, polarity inverted, a fault of another input latched.
// Build and run: platforms/s32k1xx/bsp/bspHwThreshold/test/test_hw_threshold_monitor.py (mutants: ..._mutations.py).

#include "HwThresholdWatch.h"
#include "bsp/io/outputPwm/ComplementaryPwm.h"
// after the code under test: the model uses the register types of the stub mcu/mcu.h as that code sees them
#include "FtmFaultModel.h"

#include <cstdio>
#include <vector>

#if NX_HW_THRESHOLD_COUNT != 1
#error "the generat of testproject_hw_threshold has one hardware threshold"
#endif

FTM_Type fakeFtm[4]       = {};
SIM_Type fakeSim          = {};
NxTestTrgmux fakeTrgmux   = {};
NxTestCmp fakeCmp0        = {};
NxTestCmpEnable cmpEnable = {};

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

constexpr uint8_t TRGMUX_FTM3 = 13U;
constexpr uint32_t PIN_INPUT  = 3U;        // CMP0_IN3
constexpr uint64_t VDDA       = 5000000ULL; // 1/1000 mV

enum class Stuck
{
    NONE,
    ABOVE,    // output stuck at 1
    NOT_ABOVE // output stuck at 0
};

// the comparator model
uint64_t pinMicroMv = 2500000ULL; // 0 A
Stuck stuck         = Stuck::NONE;

std::vector<uint32_t> switchOnC1;       // C1 at every switch-on of the comparator
std::vector<NxTestCmpEnable> switchOns; // SYS-129: the snapshot of every switch-on (prepare() is the third)
uint32_t waits = 0U;                    // calls of sysDelayUs
NxTestFtmFaultModel faultModel{};       // SYS-129: fault input and fault latch of FTM3, one step per wait
struct PinCall
{
    uint16_t pin;
    uint32_t swoctrl; // SWOCTRL of FTM3 when the pin was switched to the FTM
    uint32_t trgmux;
    uint32_t fms;     // SYS-129: FMS of FTM3 then (no fault of the path test left)
};
std::vector<PinCall> pinCalls;

/** The comparison of the enabled comparator ([RM13] 45.8.2): plus and minus from the DAC or the mux, INVT. */
bool compare()
{
    uint32_t const c0 = fakeCmp0.C0.value;
    uint32_t const c1 = fakeCmp0.C1;
    if (stuck != Stuck::NONE)
    {
        return stuck == Stuck::ABOVE;
    }
    uint64_t const vin  = ((c1 & CMP_C1_VRSEL_MASK) != 0U) ? 1000000ULL : VDDA;
    uint64_t const dac  = ((c1 & CMP_C1_DACEN_MASK) != 0U) ? (vin * ((c1 & CMP_C1_VOSEL_MASK) + 1U)) / 256U : 0U;
    uint32_t const psel = (c1 & CMP_C1_PSEL_MASK) >> CMP_C1_PSEL_SHIFT;
    uint32_t const msel = (c1 & CMP_C1_MSEL_MASK) >> CMP_C1_MSEL_SHIFT;
    uint64_t const plus
        = (((c1 & CMP_C1_INPSEL_MASK) >> CMP_C1_INPSEL_SHIFT) == 1U) ? ((psel == PIN_INPUT) ? pinMicroMv : 0U) : dac;
    uint64_t const minus
        = (((c1 & CMP_C1_INNSEL_MASK) >> CMP_C1_INNSEL_SHIFT) == 1U) ? ((msel == PIN_INPUT) ? pinMicroMv : 0U) : dac;
    return (plus > minus) != ((c0 & CMP_C0_INVT_MASK) != 0U);
}

void resetModels()
{
    for (FTM_Type& f : fakeFtm)
    {
        f = FTM_Type{};
    }
    fakeSim    = SIM_Type{};
    fakeTrgmux = NxTestTrgmux{};
    fakeCmp0   = NxTestCmp{};
    cmpEnable  = NxTestCmpEnable{};
    pinMicroMv = 2500000ULL;
    stuck      = Stuck::NONE;
    switchOnC1.clear();
    switchOns.clear();
    waits      = 0U;
    faultModel = NxTestFtmFaultModel{};
    pinCalls.clear();
    bios::hwThreshold::findingsLatch = bios::hwThreshold::latchWordOf(0U); // the reset value (.data)
}

/** A started driver on healthy models. */
void startDriver()
{
    bios::ComplementaryPwm pwm;
    pwm.init();
}
} // namespace

NxTestCmpC0& NxTestCmpC0::operator=(uint32_t const written)
{
    if ((written & CMP_C0_EN_MASK) != 0U)
    {
        switchOnC1.push_back(fakeCmp0.C1);
        cmpEnable = NxTestCmpEnable{true, fakeCmp0.C1, fakeFtm[3].FLTCTRL, fakeFtm[3].SC, cmpEnable.count + 1U};
        switchOns.push_back(cmpEnable);
    }
    value = written;
    return *this;
}

// C0[COUT]: the comparison while enabled, INVT while disabled ([RM13] 45.8.1)
bool nxTestCout()
{
    uint32_t const c0 = fakeCmp0.C0.value;
    return ((c0 & CMP_C0_EN_MASK) != 0U) ? compare() : ((c0 & CMP_C0_INVT_MASK) != 0U);
}

// a wait moves the hardware on: the fault logic of FTM3 takes one step (SYS-129, FtmFaultModel.h); CMPO is 0 while
// the comparator is disabled ([RM13] 45.7.1)
void sysDelayUs(uint32_t const)
{
    ++waits;
    bool const cmpo = ((fakeCmp0.C0.value & CMP_C0_EN_MASK) != 0U) && compare();
    faultModel.step(fakeFtm[3], fakeSim, fakeTrgmux, 3U, TRGMUX_FTM3, cmpo);
}

namespace bios
{
bsp::BspReturnCode Io::setDefaultConfiguration(uint16_t const io)
{
    pinCalls.push_back(PinCall{io, fakeFtm[3].SWOCTRL, fakeTrgmux.TRGMUXn[TRGMUX_FTM3], fakeFtm[3].FMS});
    return bsp::BSP_OK;
}

bsp::BspReturnCode OutputPwm::setDynamicClient(uint16_t, uint16_t, IDynamicPwmClient*) { return bsp::BSP_OK; }

bsp::BspReturnCode OutputPwm::clrDynamicClient(uint16_t) { return bsp::BSP_OK; }
} // namespace bios

namespace
{
using namespace bios;
namespace ht = bios::hwThreshold;

uint32_t sampleOnce(ht::Monitor& m)
{
    return m.sample(
        fakeCmp0, fakeTrgmux, fakeSim, fakeFtm[3], hwThresholdConfig::CONFIG, integration::HW_THRESHOLD_FAULT_CONTROL);
}

// ------------------------------------------------------------------------------------------------ start test
void testStartTest()
{
    // healthy comparator, signal at 0 A (2500 mV)
    resetModels();
    fakeCmp0.C2 = CMP_C2_RRE_MASK; // left on by someone
    CHECK(ht::startTest(fakeCmp0, hwThresholdConfig::CONFIG, &sysDelayUs) == 0U);
    CHECK(switchOnC1.size() == 2U);
    CHECK((switchOnC1.size() == 2U) && (switchOnC1[0] == 0x080098FFU));
    CHECK((switchOnC1.size() == 2U) && (switchOnC1[1] == 0x010083FFU));
    CHECK(waits == 2U);
    CHECK(fakeCmp0.C0.value == 0U); // left off for prepare()
    CHECK(fakeCmp0.C1 == 0U);
    CHECK(fakeCmp0.C2 == 0U);

    // the signal anywhere between 0 V and just below VDDA passes; at VDDA the comparison "above" cannot be forced
    for (uint64_t const pin : {0ULL, 100000ULL, 4082000ULL, 4999000ULL})
    {
        resetModels();
        pinMicroMv = pin;
        CHECK(ht::startTest(fakeCmp0, hwThresholdConfig::CONFIG, &sysDelayUs) == 0U);
    }
    resetModels();
    pinMicroMv = VDDA; // a signal at the rail: fail-safe, reported as stuck (assumption of use)
    CHECK(ht::startTest(fakeCmp0, hwThresholdConfig::CONFIG, &sysDelayUs) == ht::START_STUCK_NOT_ABOVE);

    // fault injection: a stuck output fails one of the two comparisons
    resetModels();
    stuck = Stuck::ABOVE;
    CHECK(ht::startTest(fakeCmp0, hwThresholdConfig::CONFIG, &sysDelayUs) == ht::START_STUCK_ABOVE);
    resetModels();
    stuck = Stuck::NOT_ABOVE;
    CHECK(ht::startTest(fakeCmp0, hwThresholdConfig::CONFIG, &sysDelayUs) == ht::START_STUCK_NOT_ABOVE);
}

// ------------------------------------------------------------------------------------------------ in the driver
void testDriverStart()
{
    // healthy: nothing latched, the group not forced, the path routed as before
    resetModels();
    startDriver();
    CHECK(ht::latchedFindings() == 0U);
    CHECK(fakeFtm[3].SWOCTRL == 0U);
    // two of the start test, the configured one of prepare(), then SYS-129: three of the path test and the
    // configuration again at its end
    CHECK(cmpEnable.count == 7U);
    CHECK(cmpEnable.c1 == hwThresholdConfig::CMP_C1);
    CHECK((switchOns.size() == 7U) && (switchOns[2].c1 == hwThresholdConfig::CMP_C1));
    CHECK((switchOns.size() == 7U) && (switchOns[2].ftmFltctrl == 0U)); // before the FTM enables fault control
    for (size_t i = 3U; (i < 7U) && (i < switchOns.size()); ++i)
    {
        CHECK((switchOns[i].ftmSc & FTM_SC_CLKS_MASK) != 0U); // the path test runs with the timer clocked
        CHECK(switchOns[i].ftmFltctrl == hwThresholdConfig::FTM_FLTCTRL);
    }
    CHECK((switchOnC1.size() == 7U) && (switchOnC1[3] == 0x080098FFU)); // A: pin against VDDA, "not above"
    CHECK((switchOnC1.size() == 7U) && (switchOnC1[4] == 0x010083FFU)); // B: VDDA against the pin, "above"
    CHECK((switchOnC1.size() == 7U) && (switchOnC1[5] == 0x080098FFU)); // C: "not above" again
    CHECK(fakeCmp0.C0.value == hwThresholdConfig::CMP_C0);
    CHECK(fakeCmp0.C2 == 0U);
    CHECK(waits == 3U + ht::PATH_TEST_WAITS);
    CHECK(waits == 10U);
    CHECK((static_cast<uint32_t>(fakeTrgmux.TRGMUXn[TRGMUX_FTM3]) & 0x80007F00U) == 0x80000E00U);
    CHECK(fakeTrgmux.TRGMUXn[TRGMUX_FTM3].writes == 4U); // test select, test deselect, select, lock
    CHECK(fakeTrgmux.TRGMUXn[TRGMUX_FTM3].ignoredWrites == 0U);
    CHECK(pinCalls.size() == 6U);
    for (PinCall const& p : pinCalls)
    {
        CHECK(p.swoctrl == 0U);
        CHECK(p.fms == 0U); // the test's fault is cleared before any pin is muxed
    }

    // stuck output: latched, the group forced inactive before any pin is muxed, the hardware path still armed
    for (Stuck const s : {Stuck::ABOVE, Stuck::NOT_ABOVE})
    {
        resetModels();
        stuck = s;
        startDriver();
        uint32_t const expected = (s == Stuck::ABOVE) ? ht::START_STUCK_ABOVE : ht::START_STUCK_NOT_ABOVE;
        CHECK(ht::latchedFindings() == expected);
        CHECK(fakeFtm[3].SWOCTRL == ftmPwm::CHANNEL_MASK);
        CHECK(pinCalls.size() == 6U);
        for (PinCall const& p : pinCalls)
        {
            CHECK(p.swoctrl == ftmPwm::CHANNEL_MASK);
        }
        CHECK((static_cast<uint32_t>(fakeTrgmux.TRGMUXn[TRGMUX_FTM3]) & 0x80007F00U) == 0x80000E00U);
        CHECK(fakeCmp0.C1 == hwThresholdConfig::CMP_C1); // configured for real after the test
        // SYS-129: no path test after a failed start test - only the settling wait before the route
        CHECK(waits == 3U);
        CHECK(fakeTrgmux.TRGMUXn[TRGMUX_FTM3].writes == 2U);
    }
}

// ------------------------------------------------------------------------------------------------ SYS-129 path test
/** The timer as ComplementaryPwm::init() leaves it before the path test: comparator prepared, fault control from the
 * generat, counter clocked; TRGMUX_FTM3 SEL0 used by someone else (hardware trigger). */
void armedBeforePathTest()
{
    resetModels();
    fakeTrgmux.TRGMUXn[TRGMUX_FTM3] = 0x00000016U;
    ht::prepare(fakeCmp0, fakeSim, hwThresholdConfig::CONFIG);
    fakeFtm[3].MODE    = hwThresholdConfig::FTM_MODE_FAULT | FTM_MODE_WPDIS_MASK;
    fakeFtm[3].COMBINE = hwThresholdConfig::FTM_COMBINE_FAULTEN;
    fakeFtm[3].FLTCTRL = hwThresholdConfig::FTM_FLTCTRL;
    fakeFtm[3].FLTPOL  = hwThresholdConfig::FTM_FLTPOL;
    fakeFtm[3].SC      = FTM_SC_CLKS(1U);
}

uint32_t pathTestNow()
{
    return ht::pathTest(fakeCmp0, fakeTrgmux, fakeFtm[3], hwThresholdConfig::CONFIG, &sysDelayUs);
}

void testPathTest()
{
    CHECK(ht::faultInputOf(hwThresholdConfig::CONFIG) == 0U);
    CHECK(ht::PATH_TEST_WAITS == 7U);
    constexpr uint32_t SEEN = FTM_FMS_FAULTF_MASK | FTM_FMS_FAULTF0_MASK | FTM_FMS_FAULTIN_MASK;

    // healthy: tripped and cleared, nothing found; the route left unselected and unlocked, SEL0 kept, the
    // comparator configured as prepare() left it
    armedBeforePathTest();
    CHECK(pathTestNow() == 0U);
    CHECK(waits == ht::PATH_TEST_WAITS);
    CHECK(faultModel.latched == 0U);
    CHECK((fakeFtm[3].FMS & SEEN) == 0U);
    CHECK(static_cast<uint32_t>(fakeTrgmux.TRGMUXn[TRGMUX_FTM3]) == 0x00000016U);
    CHECK(fakeTrgmux.TRGMUXn[TRGMUX_FTM3].writes == 3U); // preset, select, deselect - never the lock
    CHECK(fakeCmp0.C0.value == hwThresholdConfig::CMP_C0);
    CHECK(fakeCmp0.C1 == hwThresholdConfig::CMP_C1);
    CHECK(fakeCmp0.C2 == 0U);
    CHECK(switchOnC1.size() == 5U); // prepare, A, B, C, configuration again

    // the signal anywhere below VDDA does not matter: the comparator is forced through the DAC
    for (uint64_t const pin : {0ULL, 4100000ULL, 4999000ULL})
    {
        armedBeforePathTest();
        pinMicroMv = pin;
        CHECK(pathTestNow() == 0U);
    }

    // fault input stuck asserted: latched before the forcing, and it cannot be cleared - FAULTF stays set, the
    // outputs stay forced by the hardware (fail-safe)
    armedBeforePathTest();
    faultModel.forceAsserted = 1U;
    CHECK(pathTestNow() == (ht::PATH_FAULT_UNFORCED | ht::PATH_NO_CLEAR));
    CHECK((fakeFtm[3].FMS & FTM_FMS_FAULTF_MASK) != 0U);

    // fault input dead (or a broken connection behind the trigger mux): no trip
    armedBeforePathTest();
    faultModel.forceInactive = 1U;
    CHECK(pathTestNow() == ht::PATH_NO_TRIP);

    // route register locked before the test with SEL1 = 0 (the selection is ignored): no trip
    armedBeforePathTest();
    fakeTrgmux.TRGMUXn[TRGMUX_FTM3] = 0x80000016U;
    CHECK(pathTestNow() == ht::PATH_NO_TRIP);

    // fault input disabled, fault control off, timer not clocked, fault source back on the pin, polarity inverted
    void (*const broken[])() = {
        [] { fakeFtm[3].FLTCTRL &= ~FTM_FLTCTRL_FAULT0EN_MASK; },
        [] { fakeFtm[3].MODE &= ~FTM_MODE_FAULTM_MASK; },
        [] { fakeFtm[3].SC = 0U; },
        [] { fakeSim.FTMOPT0 &= ~hwThresholdConfig::CONFIG.simFaultSelect; },
        [] { fakeFtm[3].FLTPOL = FTM_FLTPOL_FLT0POL_MASK; }, // active low: asserted while "not above"
    };
    uint32_t const expected[] = {
        ht::PATH_NO_TRIP,
        ht::PATH_NO_TRIP,
        ht::PATH_NO_TRIP,
        ht::PATH_NO_TRIP,
        ht::PATH_FAULT_UNFORCED | ht::PATH_NO_TRIP | ht::PATH_NO_CLEAR,
    };
    for (size_t i = 0U; i < (sizeof(expected) / sizeof(expected[0])); ++i)
    {
        armedBeforePathTest();
        broken[i]();
        uint32_t const found = pathTestNow();
        if (found != expected[i])
        {
            std::printf("  path test case %u: found 0x%x, expected 0x%x\n", static_cast<unsigned>(i), found,
                        expected[i]);
        }
        CHECK(found == expected[i]);
    }

    // a fault of another enabled input (latched before, its input quiet again): seen as a fault without the forcing,
    // and the test does not clear it - its flag and FAULTF stay set
    armedBeforePathTest();
    fakeFtm[3].FLTCTRL |= FTM_FLTCTRL_FAULT1EN_MASK;
    faultModel.forceAsserted = 2U;
    sysDelayUs(1U);
    faultModel.forceAsserted = 0U;
    sysDelayUs(1U);
    CHECK((fakeFtm[3].FMS & (FTM_FMS_FAULTF_MASK | FTM_FMS_FAULTF1_MASK))
          == (FTM_FMS_FAULTF_MASK | FTM_FMS_FAULTF1_MASK));
    CHECK(pathTestNow() == (ht::PATH_FAULT_UNFORCED | ht::PATH_NO_CLEAR));
    CHECK((fakeFtm[3].FMS & (FTM_FMS_FAULTF_MASK | FTM_FMS_FAULTF1_MASK | FTM_FMS_FAULTF0_MASK))
          == (FTM_FMS_FAULTF_MASK | FTM_FMS_FAULTF1_MASK));
}

void testDriverPathTest()
{
    // the dead input in the real driver: latched, the group forced before any pin is muxed, the path still armed
    resetModels();
    faultModel.forceInactive = 1U;
    startDriver();
    CHECK(ht::latchedFindings() == ht::PATH_NO_TRIP);
    CHECK(fakeFtm[3].SWOCTRL == ftmPwm::CHANNEL_MASK);
    CHECK(pinCalls.size() == 6U);
    for (PinCall const& p : pinCalls)
    {
        CHECK(p.swoctrl == ftmPwm::CHANNEL_MASK);
        CHECK((p.trgmux & 0x80007F00U) == 0x80000E00U);
    }

    // the stuck input: latched, forced, and the fault stays latched in the timer when the pins are muxed
    resetModels();
    faultModel.forceAsserted = 1U;
    startDriver();
    CHECK(ht::latchedFindings() == (ht::PATH_FAULT_UNFORCED | ht::PATH_NO_CLEAR));
    CHECK(fakeFtm[3].SWOCTRL == ftmPwm::CHANNEL_MASK);
    CHECK(pinCalls.size() == 6U);
    for (PinCall const& p : pinCalls)
    {
        CHECK((p.fms & FTM_FMS_FAULTF_MASK) != 0U);
        CHECK(p.swoctrl == ftmPwm::CHANNEL_MASK);
    }
    // the safety cycle reports both: tripped (bit 0) and the findings 0x2800 in bits 23..8, timer 3
    CHECK(ht::shutoffContext(
              fakeCmp0, fakeTrgmux, fakeSim, fakeFtm[3], hwThresholdConfig::CONFIG,
              integration::HW_THRESHOLD_FAULT_CONTROL)
          == 0x03280001U);
    // the first poll of the power stage reports the finding and keeps the group forced
    fakeFtm[3].SWOCTRL = 0U;
    integration::HwThresholdWatch watch;
    CHECK(watch.cyclic() == (ht::PATH_FAULT_UNFORCED | ht::PATH_NO_CLEAR));
    CHECK(fakeFtm[3].SWOCTRL == ftmPwm::CHANNEL_MASK);
}

// ------------------------------------------------------------------------------------------------ register check
void testConfigurationFindings()
{
    struct Case
    {
        char const* what;
        void (*inject)();
        uint32_t expected;
    };
    Case const cases[] = {
        {"comparator switched off", [] { fakeCmp0.C0.value &= ~CMP_C0_EN_MASK; }, ht::COMPARATOR_OFF},
        {"all of C0 written 0", [] { fakeCmp0.C0.value = 0U; }, ht::COMPARATOR_OFF | ht::COMPARATOR_MODE},
        {"inverted", [] { fakeCmp0.C0.value |= CMP_C0_INVT_MASK; }, ht::COMPARATOR_MODE},
        {"low speed", [] { fakeCmp0.C0.value &= ~CMP_C0_PMODE_MASK; }, ht::COMPARATOR_MODE},
        {"window", [] { fakeCmp0.C0.value |= CMP_C0_WE_MASK; }, ht::COMPARATOR_MODE},
        {"filter", [] { fakeCmp0.C0.value |= CMP_C0_FILTER_CNT(7U) | CMP_C0_FPR(0xFFU); }, ht::COMPARATOR_MODE},
        {"unfiltered output", [] { fakeCmp0.C0.value |= CMP_C0_COS_MASK; }, ht::COMPARATOR_MODE},
        {"hysteresis", [] { fakeCmp0.C0.value |= CMP_C0_HYSTCTR(3U); }, ht::COMPARATOR_MODE},
        {"DAC off", [] { fakeCmp0.C1 &= ~CMP_C1_DACEN_MASK; }, ht::REFERENCE},
        {"DAC level one step up", [] { fakeCmp0.C1 += 1U; }, ht::REFERENCE},
        {"bandgap reference", [] { fakeCmp0.C1 |= CMP_C1_VRSEL_MASK; }, ht::REFERENCE},
        {"other input", [] { fakeCmp0.C1 ^= CMP_C1_PSEL(1U); }, ht::REFERENCE},
        {"minus from the mux", [] { fakeCmp0.C1 |= CMP_C1_INNSEL(1U); }, ht::REFERENCE},
        {"round robin", [] { fakeCmp0.C2 |= CMP_C2_RRE_MASK; }, ht::ROUND_ROBIN},
        {"route to another source", [] { fakeTrgmux.TRGMUXn[TRGMUX_FTM3].value ^= 0x00000100U; }, ht::ROUTE},
        {"route unlocked", [] { fakeTrgmux.TRGMUXn[TRGMUX_FTM3].value &= ~TRGMUX_TRGMUXn_LK_MASK; }, ht::ROUTE},
        {"fault input from its pin", [] { fakeSim.FTMOPT0 &= ~(1UL << 12U); }, ht::SOURCE},
        {"fault control off", [] { fakeFtm[3].MODE &= ~FTM_MODE_FAULTM_MASK; }, ht::FAULT_CONTROL},
        {"fault input disabled", [] { fakeFtm[3].FLTCTRL &= ~FTM_FLTCTRL_FAULT0EN_MASK; }, ht::FAULT_CONTROL},
        {"fault filter changed", [] { fakeFtm[3].FLTCTRL ^= FTM_FLTCTRL_FFVAL(1U); }, ht::FAULT_CONTROL},
        {"fault input active low", [] { fakeFtm[3].FLTPOL |= FTM_FLTPOL_FLT0POL_MASK; }, ht::FAULT_CONTROL},
        {"FAULTEN of a pair", [] { fakeFtm[3].COMBINE &= ~(FTM_COMBINE_FAULTEN0_MASK << 8U); }, ht::FAULT_CONTROL},
        // status bits and results are no configuration: no finding
        {"edge flags set", [] { fakeCmp0.C0.value |= CMP_C0_CFR_MASK | CMP_C0_CFF_MASK; }, 0U},
        {"round-robin results", [] { fakeCmp0.C2 |= 0xFFU; }, 0U},
        {"other SIM bits", [] { fakeSim.FTMOPT0 |= 0x00000002U; }, 0U},
        {"other SEL field", [] { fakeTrgmux.TRGMUXn[TRGMUX_FTM3].value ^= 0x00000016U; }, 0U},
        {"fault latched", [] { fakeFtm[3].FMS |= FTM_FMS_FAULTF_MASK; }, 0U},
    };
    for (Case const& c : cases)
    {
        resetModels();
        startDriver();
        ht::Monitor m;
        uint32_t const before = sampleOnce(m);
        c.inject();
        uint32_t const found = sampleOnce(m);
        if ((before != 0U) || (found != c.expected))
        {
            std::printf("  case '%s': before 0x%x, found 0x%x, expected 0x%x\n", c.what, before, found, c.expected);
        }
        CHECK(before == 0U);
        CHECK(found == c.expected);
        CHECK((ht::configurationFindings(fakeCmp0, fakeTrgmux, fakeSim, hwThresholdConfig::CONFIG)
               & ~ht::FAULT_CONTROL)
              == (c.expected & ~ht::FAULT_CONTROL));
    }
}

// ------------------------------------------------------------------------------------------------ consistency
void testNotLatched()
{
    resetModels();
    startDriver();
    ht::Monitor m;
    pinMicroMv = 4100000ULL; // 40.4 A: above the threshold, the model does not latch FAULTF by itself
    CHECK(sampleOnce(m) == 0U);                // one sample may race the timer's filter
    CHECK(sampleOnce(m) == ht::NOT_LATCHED);   // the second one does not
    CHECK(sampleOnce(m) == ht::NOT_LATCHED);
    fakeFtm[3].FMS = FTM_FMS_FAULTF_MASK;      // the timer latched: consistent
    CHECK(sampleOnce(m) == 0U);
    fakeFtm[3].FMS = 0U;
    CHECK(sampleOnce(m) == 0U);                // the run starts again
    pinMicroMv = 2500000ULL;                   // below again: the run is broken
    CHECK(sampleOnce(m) == 0U);
    pinMicroMv = 4100000ULL;
    CHECK(sampleOnce(m) == 0U);
    CHECK(sampleOnce(m) == ht::NOT_LATCHED);

    // a disabled comparator reads INVT in COUT - it is COMPARATOR_OFF, never "above"
    resetModels();
    startDriver();
    ht::Monitor off;
    fakeCmp0.C0.value = CMP_C0_INVT_MASK;
    CHECK(sampleOnce(off) == (ht::COMPARATOR_OFF | ht::COMPARATOR_MODE));
    CHECK(sampleOnce(off) == (ht::COMPARATOR_OFF | ht::COMPARATOR_MODE));

    // a stuck output at "above" with the signal at 0 A: the timer latches in hardware, nothing inconsistent
    resetModels();
    startDriver();
    ht::Monitor s;
    stuck          = Stuck::ABOVE;
    fakeFtm[3].FMS = FTM_FMS_FAULTF_MASK;
    CHECK(sampleOnce(s) == 0U);
    CHECK(sampleOnce(s) == 0U);
}

// ------------------------------------------------------------------------------------------------ latch
void testLatch()
{
    resetModels();
    CHECK(ht::latchedFindings() == 0U);
    ht::latchFindings(ht::ROUTE);
    ht::latchFindings(ht::SOURCE);
    CHECK(ht::latchedFindings() == (ht::ROUTE | ht::SOURCE));
    ht::latchFindings(0U);
    CHECK(ht::latchedFindings() == (ht::ROUTE | ht::SOURCE)); // never removes one
    CHECK(ht::findingsLatch == ((ht::ROUTE | ht::SOURCE) | ((~(ht::ROUTE | ht::SOURCE) & 0xFFFFU) << 16U)));
    // a wild write: implausible, read as a finding
    ht::findingsLatch = 0U;
    CHECK((ht::latchedFindings() & ht::LATCH_IMPLAUSIBLE) != 0U);
    ht::findingsLatch = ht::latchWordOf(ht::ROUTE) ^ 0x00010000U;
    CHECK(ht::latchedFindings() == (ht::ROUTE | ht::LATCH_IMPLAUSIBLE));
    // and stays so once latched again
    ht::latchFindings(ht::SOURCE);
    CHECK(ht::latchedFindings() == (ht::ROUTE | ht::SOURCE | ht::LATCH_IMPLAUSIBLE));
}

// ------------------------------------------------------------------------------------------------ fast check
void testWatch()
{
    resetModels();
    startDriver();
    integration::HwThresholdWatch watch;
    CHECK(watch.cyclic() == 0U);
    CHECK(fakeFtm[3].SWOCTRL == 0U);
    CHECK(watch.cyclic() == 0U);

    // comparator switched off between two polls: found in the next poll, the group forced at once
    fakeCmp0.C0.value &= ~CMP_C0_EN_MASK;
    CHECK(watch.cyclic() == ht::COMPARATOR_OFF);
    CHECK(fakeFtm[3].SWOCTRL == ftmPwm::CHANNEL_MASK);
    CHECK(ht::latchedFindings() == ht::COMPARATOR_OFF);

    // switched on again (a second wild write): the latch stays, the group stays forced
    fakeCmp0.C0.value = hwThresholdConfig::CMP_C0;
    fakeFtm[3].SWOCTRL = 0U; // e.g. the guard's release
    CHECK(watch.cyclic() == 0U); // nothing new
    CHECK(fakeFtm[3].SWOCTRL == ftmPwm::CHANNEL_MASK);
    CHECK(ht::latchedFindings() == ht::COMPARATOR_OFF);

    // a further finding is reported once
    fakeSim.FTMOPT0 = 0U;
    CHECK(watch.cyclic() == ht::SOURCE);
    CHECK(watch.cyclic() == 0U);
    CHECK(integration::HwThresholdWatch::findings() == (ht::COMPARATOR_OFF | ht::SOURCE));

    // the start test's finding is forced by the first poll as well (and reported by it)
    resetModels();
    stuck = Stuck::NOT_ABOVE;
    startDriver();
    stuck              = Stuck::NONE;
    fakeFtm[3].SWOCTRL = 0U;
    integration::HwThresholdWatch first;
    CHECK(first.cyclic() == ht::START_STUCK_NOT_ABOVE);
    CHECK(fakeFtm[3].SWOCTRL == ftmPwm::CHANNEL_MASK);

    // not latched: consistent output -> forced after the second poll
    resetModels();
    startDriver();
    integration::HwThresholdWatch w;
    pinMicroMv = 4100000ULL;
    CHECK(w.cyclic() == 0U);
    CHECK(fakeFtm[3].SWOCTRL == 0U);
    CHECK(w.cyclic() == ht::NOT_LATCHED);
    CHECK(fakeFtm[3].SWOCTRL == ftmPwm::CHANNEL_MASK);
}
// ------------------------------------------------------------------------------------------------ SYS-130 report
uint32_t contextNow()
{
    return ht::shutoffContext(
        fakeCmp0, fakeTrgmux, fakeSim, fakeFtm[3], hwThresholdConfig::CONFIG, integration::HW_THRESHOLD_FAULT_CONTROL);
}

void testShutoffContext()
{
    // the word by hand: timer 3 in bits 31..24, findings in 23..8, FAULTF in bit 0; nothing at all is 0
    CHECK(ht::reportContext(false, 0U, 3U) == 0U);
    CHECK(ht::reportContext(true, 0U, 3U) == 0x03000001U);
    CHECK(ht::reportContext(false, ht::COMPARATOR_OFF, 3U) == 0x03000100U);
    CHECK(ht::reportContext(true, ht::LATCH_IMPLAUSIBLE | ht::NOT_LATCHED, 0U) == 0x00810001U);
    CHECK(ht::timerOf(hwThresholdConfig::CONFIG) == 3U);

    // healthy path after the start: nothing to report
    resetModels();
    startDriver();
    CHECK(contextNow() == 0U);
    // the threshold tripped: the timer has latched its fault
    fakeFtm[3].FMS = FTM_FMS_FAULTF_MASK;
    CHECK(contextNow() == 0x03000001U);

    // read again in the safety cycle, independent of the power stage poll: switched off, fault control changed
    resetModels();
    startDriver();
    fakeCmp0.C0.value &= ~CMP_C0_EN_MASK;
    CHECK(contextNow() == 0x03000100U);
    resetModels();
    startDriver();
    fakeFtm[3].FLTCTRL = 0U;
    CHECK(contextNow() == 0x03004000U);

    // from the findings latch: the start test (stuck at not above) and a tripped timer together
    resetModels();
    stuck = Stuck::NOT_ABOVE;
    startDriver();
    stuck          = Stuck::NONE;
    fakeFtm[3].FMS = FTM_FMS_FAULTF_MASK;
    CHECK(contextNow() == 0x03040001U);

    // a corrupted latch word is reported (fail-safe)
    resetModels();
    startDriver();
    ht::findingsLatch = 0U;
    CHECK(contextNow() == 0x03800000U);
}
} // namespace

int main()
{
    testStartTest();
    testDriverStart();
    testPathTest();
    testDriverPathTest();
    testConfigurationFindings();
    testNotLatched();
    testLatch();
    testWatch();
    testShutoffContext();
    std::printf("hardware threshold monitor host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
