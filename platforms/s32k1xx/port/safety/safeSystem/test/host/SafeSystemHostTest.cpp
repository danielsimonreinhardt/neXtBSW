/* Host test of safety::SafeSystem (IO-16), no target hardware needed.
 *
 * The production SafeSystem.cpp is compiled against stubs of its four dependencies (stubs/):
 * a plain SIM register struct, a MemoryProtection that records the fusa gate, a SafeSupervisor
 * that records every trigger with its context instead of resetting the MCU, and a scriptable
 * AnalogInput::get. What is tested is therefore the real decision logic.
 *
 * Focus: a failed AnalogInput::get. Before IO-16 the raw value stayed 0, checkAdcReference
 * divided 1000 * 4095 by it (UDIV without DIV_0_TRP yields 0 on Cortex-M4), the reference looked
 * broken and SafeSupervisor::handle() answered ADC_REFERENCE_ERROR with limp home and a software
 * reset - one transient ADC error rebooted the ECU.
 *
 * ARCH-32: the band of the reference check (nominal value of the board, tolerance of the project) comes from the
 * configuration. The stubs configure 5000 mV +- 300 mV on purpose, not the eval board (4096 mV +- 200 mV), and all
 * expectations are derived from the macros: tools/bswgen/test_bswgen.py compiles the same test once more against
 * the headers generated from its own profile (tools/bswgen/test/profiles/projects/testproject_adc_reference).
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeSystem/include \
 *       -Iplatforms/s32k1xx/port/safety/safeSystem/test/host/stubs \
 *       -isystem external/openbsw/libs/bsw/bsp/include \
 *       -isystem external/openbsw/libs/bsp/bspInterrupts/include \
 *       -isystem external/openbsw/libs/bsp/bspInterrupts/mock/include \
 *       platforms/s32k1xx/port/safety/safeSystem/test/host/SafeSystemHostTest.cpp \
 *       platforms/s32k1xx/port/safety/safeSystem/src/safeSystem/SafeSystem.cpp \
 *       -o build/host/safe_system_test.exe
 *   build/host/safe_system_test.exe
 */

#include "safeSystem/SafeSystem.h"

#include <bsp/adc/AnalogInput.h>
#include <bsp/adc/adcReferenceConfiguration.h>
#include <interrupts/suspendResumeAllInterrupts.h>
#include <mcu/mcu.h>
#include <safeMemory/MemoryProtection.h>
#include <safeSupervisor/SafeSupervisor.h>

#include <cstdio>
#include <initializer_list>

// ---- storage of the stubs and of the OpenBSW interrupt mock

SimType simRegisters{};

namespace safety
{
uint32_t MemoryProtection::closeCalls = 0U;
uint32_t MemoryProtection::openCalls  = 0U;
bool MemoryProtection::gateClosed     = false;

namespace
{
SafeSupervisor supervisorInstance;
} // namespace

SafeSupervisor& SafeSupervisor::getInstance() { return supervisorInstance; }

void SafeSupervisor::resetInstance() { supervisorInstance = SafeSupervisor(); }
} // namespace safety

namespace bios
{
AnalogInput::Response AnalogInput::bandgap{};
AnalogInput::Response AnalogInput::internalSupply{};
uint32_t AnalogInput::calls             = 0U;
uint32_t AnalogInput::callsWithOpenGate = 0U;
uint32_t AnalogInput::garbageOnError    = 0U;
} // namespace bios

extern "C"
{
uint32_t suspendCalls = 0U;
uint32_t resumeCalls  = 0U;

uint32_t getMachineStateRegisterValueAndSuspendAllInterrupts(void)
{
    ++suspendCalls;
    return 0U;
}

OldIntEnabledStatusValueType getOldIntEnabledStatusValueAndSuspendAllInterrupts()
{
    ++suspendCalls;
    return 0U;
}

void resumeAllInterrupts(uint32_t) { ++resumeCalls; }
}

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

using ::bios::AnalogInput;
using ::safety::MemoryProtection;
using ::safety::SafeSupervisor;
using ::safety::SafeSystem;

constexpr uint32_t ADC_MAX_RAW   = 0xFFFU;
constexpr uint32_t BANDGAP_MV    = 1000U;
// ARCH-32: the band the reference is checked against comes from the configuration - nominal value of the board
// (generated bsp/adc/adcReferenceConfiguration.h), tolerance of the project (integration/safeSystemConfiguration.h).
// Everything below is derived from these two, so the test also runs against generated headers of another profile.
#if !defined(NX_ADC_REFERENCE_MV)
// same message as SafeSystem.cpp, so a board without reference reads the same whichever file the compiler takes first
#error "safeSystem needs the nominal ADC reference voltage: board.adc_reference_mv in board.yaml"
#endif
constexpr uint32_t NOMINAL_MV    = NX_ADC_REFERENCE_MV;
constexpr uint32_t TOLERANCE_MV  = NX_MODULE_SAFESYSTEM_PARAM_ADC_REFERENCE_TOLERANCE_MV;
constexpr uint32_t EVAL_BOARD_MV = 4096U; // VREFH of the eval board (LM4132), the constant before ARCH-32
// healthy bandgap reading of the configured board (the eval board measured 981, IO-01/IO-14)
constexpr uint32_t BANDGAP_RAW   = (BANDGAP_MV * ADC_MAX_RAW) / NOMINAL_MV;
constexpr uint32_t REF_MV        = (BANDGAP_MV * ADC_MAX_RAW) / BANDGAP_RAW;
constexpr uint32_t VDD_1V2_CORE  = 5U; // SIM_CHIPCTL ADC_SUPPLY selector of the 1.2 V core supply

/** Reference voltage SafeSystem computes from a bandgap raw value. */
constexpr uint32_t refOfRaw(uint32_t const raw) { return (BANDGAP_MV * ADC_MAX_RAW) / raw; }

/** Smallest raw value whose reference is not above the upper limit: the highest reference still accepted. */
uint32_t rawAtUpperLimit()
{
    uint32_t raw = 1U;
    while (refOfRaw(raw) > (NOMINAL_MV + TOLERANCE_MV))
    {
        ++raw;
    }
    return raw;
}

/** Largest raw value whose reference is not below the lower limit: the lowest reference still accepted. */
uint32_t rawAtLowerLimit()
{
    uint32_t raw = ADC_MAX_RAW;
    while (refOfRaw(raw) < (NOMINAL_MV - TOLERANCE_MV))
    {
        --raw;
    }
    return raw;
}

/** Raw value of a supply voltage, as the check computes it back. */
uint32_t rawOfSupply(uint32_t const mv, uint32_t const refMv)
{
    return (mv * ADC_MAX_RAW) / refMv;
}

/** Selector the supply multiplexer currently points at. */
uint32_t selectedSupply()
{
    return (simRegisters.CHIPCTL & SIM_CHIPCTL_ADC_SUPPLY_MASK) >> SIM_CHIPCTL_ADC_SUPPLY_SHIFT;
}

/** Reading the selected supply would deliver on healthy hardware. */
void answerSelectedSupply()
{
    uint32_t const mv = (selectedSupply() == VDD_1V2_CORE) ? 1200U : 3300U;
    ::bios::AnalogInput::internalSupply.value = rawOfSupply(mv, REF_MV);
}

struct Fixture
{
    SafeSystem system;

    Fixture()
    {
        simRegisters = SimType{};
        MemoryProtection::reset();
        AnalogInput::reset();
        SafeSupervisor::resetInstance();
        suspendCalls = 0U;
        resumeCalls  = 0U;
        // healthy readings by default
        AnalogInput::bandgap.ret          = bsp::BSP_OK;
        AnalogInput::bandgap.value        = BANDGAP_RAW;
        AnalogInput::internalSupply.ret   = bsp::BSP_OK;
        AnalogInput::internalSupply.value = rawOfSupply(3300U, REF_MV);
    }

    SafeSupervisor& supervisor() const { return SafeSupervisor::getInstance(); }

    uint32_t refTriggers() const { return supervisor().adcReferenceMonitor.triggers; }

    uint32_t supplyTriggers() const { return supervisor().internalSuppliesMonitor.triggers; }
};

void testInit()
{
    Fixture f;
    f.system.init();
    CHECK((simRegisters.CHIPCTL & SIM_CHIPCTL_ADC_SUPPLYEN_MASK) != 0U);
}

void testNominalCycles()
{
    Fixture f;
    f.system.init();
    for (uint32_t i = 0U; i < 24U; ++i) // four full rounds of the supply multiplexer
    {
        answerSelectedSupply(); // the reading follows the selector of the previous cycle
        f.system.cyclic();
    }
    CHECK(f.refTriggers() == 0U);
    CHECK(f.supplyTriggers() == 0U);
    auto const d = f.system.getAdcDiagnostics();
    CHECK(d.readErrors == 0U);
    CHECK(d.consecutiveRefErrors == 0U);
    CHECK(d.skippedRefChecks == 0U);
    CHECK(d.skippedSupplyChecks == 0U);
    CHECK(d.refVoltageValid);
    CHECK(d.refVoltageMv == REF_MV);
    // both readings of every cycle, all of them inside the closed fusa gate
    CHECK(AnalogInput::calls == 48U);
    CHECK(AnalogInput::callsWithOpenGate == 0U);
    CHECK(MemoryProtection::closeCalls == 24U);
    CHECK(MemoryProtection::openCalls == 24U);
    CHECK(!MemoryProtection::gateClosed);
    // 24 cycles plus the copy of the diagnostics above (IO-19: the counters are copied in an exclusive section)
    CHECK(suspendCalls == 25U);
    CHECK(resumeCalls == 25U);
}

void testSingleReadErrorDoesNotTrigger()
{
    // The regression of IO-16: one failed get must not reset the ECU.
    Fixture f;
    f.system.init();
    f.system.cyclic(); // one healthy cycle, so a reference voltage is known
    AnalogInput::bandgap.ret        = bsp::BSP_ERROR;
    AnalogInput::internalSupply.ret = bsp::BSP_ERROR;
    AnalogInput::garbageOnError     = 0U; // what the old code divided by
    for (uint32_t i = 1U; i < SafeSystem::ADC_REF_ERROR_LIMIT; ++i)
    {
        f.system.cyclic();
        CHECK(f.refTriggers() == 0U);
        CHECK(f.supplyTriggers() == 0U);
    }
    auto const d = f.system.getAdcDiagnostics();
    CHECK(d.consecutiveRefErrors == SafeSystem::ADC_REF_ERROR_LIMIT - 1U);
    CHECK(d.skippedRefChecks == SafeSystem::ADC_REF_ERROR_LIMIT - 1U);
    CHECK(d.readErrors == 2U * (SafeSystem::ADC_REF_ERROR_LIMIT - 1U));
    CHECK(d.refVoltageMv == REF_MV); // the last valid value is kept
    CHECK(d.refVoltageValid);
}

void testPersistentReadErrorTriggersOnce()
{
    Fixture f;
    f.system.init();
    AnalogInput::bandgap.ret    = bsp::BSP_ERROR;
    AnalogInput::garbageOnError = 0U;
    for (uint32_t i = 0U; i < SafeSystem::ADC_REF_ERROR_LIMIT; ++i)
    {
        f.system.cyclic();
    }
    CHECK(f.refTriggers() == 1U);
    CHECK(f.supervisor().adcReferenceMonitor.lastContext == SafeSystem::ADC_CONTEXT_READ_FAILURE);
    // further failures do not repeat the report (the real supervisor has reset the MCU by now)
    for (uint32_t i = 0U; i < 5U; ++i)
    {
        f.system.cyclic();
    }
    CHECK(f.refTriggers() == 1U);
    auto const d = f.system.getAdcDiagnostics();
    CHECK(d.consecutiveRefErrors == SafeSystem::ADC_REF_ERROR_LIMIT + 5U);
    CHECK(!d.refVoltageValid); // never had a valid reading
}

void testReadErrorSeriesInterruptedByOneGoodReading()
{
    Fixture f;
    f.system.init();
    for (uint32_t round = 0U; round < 3U; ++round)
    {
        AnalogInput::bandgap.ret = bsp::BSP_ERROR;
        for (uint32_t i = 0U; i < SafeSystem::ADC_REF_ERROR_LIMIT - 1U; ++i)
        {
            answerSelectedSupply(); // SYS-95: a healthy converter follows the supply multiplexer
            f.system.cyclic();
        }
        AnalogInput::bandgap.ret = bsp::BSP_OK;
        answerSelectedSupply();
        f.system.cyclic();
        CHECK(f.system.getAdcDiagnostics().consecutiveRefErrors == 0U);
    }
    CHECK(f.refTriggers() == 0U); // single failures, however many, never trigger
    CHECK(f.system.getAdcDiagnostics().readErrors == 3U * (SafeSystem::ADC_REF_ERROR_LIMIT - 1U));
}

void testRawZeroCountsAsReadError()
{
    // get reports success with a raw value of 0: physically impossible for the bandgap and the
    // division by zero of the old code. Must behave like a failed reading.
    Fixture f;
    f.system.init();
    f.system.cyclic();
    AnalogInput::bandgap.ret   = bsp::BSP_OK;
    AnalogInput::bandgap.value = 0U;
    f.system.cyclic();
    CHECK(f.refTriggers() == 0U);
    auto const d = f.system.getAdcDiagnostics();
    CHECK(d.skippedRefChecks == 1U);
    CHECK(d.consecutiveRefErrors == 1U);
    CHECK(d.readErrors == 0U); // get itself did not fail
    CHECK(d.refVoltageMv == REF_MV);
    for (uint32_t i = 1U; i < SafeSystem::ADC_REF_ERROR_LIMIT; ++i)
    {
        f.system.cyclic();
    }
    CHECK(f.refTriggers() == 1U);
    CHECK(f.supervisor().adcReferenceMonitor.lastContext == SafeSystem::ADC_CONTEXT_READ_FAILURE);
}

/** Reference triggers after one cycle of a fresh system that reads the bandgap raw value `raw`. */
uint32_t refTriggersFor(uint32_t const raw)
{
    Fixture f;
    f.system.init();
    AnalogInput::bandgap.value = raw;
    f.system.cyclic();
    if (f.refTriggers() != 0U)
    {
        CHECK(f.supervisor().adcReferenceMonitor.lastContext == SafeSystem::ADC_CONTEXT_OUT_OF_RANGE);
        CHECK(f.system.getAdcDiagnostics().skippedRefChecks == 0U);
    }
    CHECK(f.system.getAdcDiagnostics().refVoltageMv == refOfRaw(raw));
    return f.refTriggers();
}

void testReferenceOutOfRangeStillTriggersImmediately()
{
    // ARCH-32: the edges of the configured band NX_ADC_REFERENCE_MV +- tolerance, as close as the 12-bit
    // bandgap reading can get to them
    uint32_t const upper = rawAtUpperLimit();
    uint32_t const lower = rawAtLowerLimit();
    CHECK(refOfRaw(upper) <= (NOMINAL_MV + TOLERANCE_MV));
    CHECK(refOfRaw(upper - 1U) > (NOMINAL_MV + TOLERANCE_MV));
    CHECK(refOfRaw(lower) >= (NOMINAL_MV - TOLERANCE_MV));
    CHECK(refOfRaw(lower + 1U) < (NOMINAL_MV - TOLERANCE_MV));
    CHECK(refTriggersFor(upper) == 0U);       // highest reference inside the band
    CHECK(refTriggersFor(upper - 1U) == 1U);  // reference too high (raw too low)
    CHECK(refTriggersFor(lower) == 0U);       // lowest reference inside the band
    CHECK(refTriggersFor(lower + 1U) == 1U);  // reference too low (raw too high)
    CHECK(refTriggersFor(2000U) == 1U);       // 1000 * 4095 / 2000 = 2047 mV, far too low for every profile
    CHECK(refTriggersFor(BANDGAP_RAW) == 0U); // the nominal value itself
}

void testConfiguredReferenceBand()
{
    // ARCH-32: nominal value and tolerance come from the configuration. Before, SafeSystem.cpp held
    // 4096 mV +- 200 mV (the eval board) as constants, and a board with another VREFH - the S32K148 build test and
    // the S32K116 EVB have 5000 mV - would have triggered the reference monitor in every cycle.
#if defined(NX_SAFESYSTEM_HOST_STUB_PROFILE)
    CHECK(NOMINAL_MV == 5000U);  // stubs/bsp/adc/adcReferenceConfiguration.h, not the eval board
    CHECK(TOLERANCE_MV == 300U); // stubs/integration/safeSystemConfiguration.h, not the default of 200 mV
#endif
    {
        // before the first reading the diagnostics report the configured nominal value (not valid yet)
        Fixture f;
        auto const d = f.system.getAdcDiagnostics();
        CHECK(d.refVoltageMv == NOMINAL_MV);
        CHECK(!d.refVoltageValid);
    }
    uint32_t const evalBoardDeviation
        = (NOMINAL_MV > EVAL_BOARD_MV) ? (NOMINAL_MV - EVAL_BOARD_MV) : (EVAL_BOARD_MV - NOMINAL_MV);
    uint32_t const evalBoardRaw = (BANDGAP_MV * ADC_MAX_RAW) / EVAL_BOARD_MV; // 999: 4099 mV
    if (evalBoardDeviation > (TOLERANCE_MV + 10U))
    {
        // a board with another reference: the reference of the eval board is a fault there
        CHECK(refTriggersFor(evalBoardRaw) == 1U);
    }
    // a tolerance of 200 mV instead of the configured one would move the edges (both profiles of the tests
    // configure more than 200 mV, the eval board demo exactly 200 mV)
    if (TOLERANCE_MV > 210U)
    {
        CHECK(refTriggersFor((BANDGAP_MV * ADC_MAX_RAW) / (NOMINAL_MV + TOLERANCE_MV - 10U)) == 0U);
        CHECK(refTriggersFor((BANDGAP_MV * ADC_MAX_RAW) / (NOMINAL_MV - TOLERANCE_MV + 10U) + 1U) == 0U);
    }
}

void testSupplyCheckSkippedWithoutReading()
{
    // The supply comparison must not run with a raw value of 0: it would report an undervoltage
    // of a healthy supply. The multiplexer is stepped on anyway.
    Fixture f;
    f.system.init();
    f.system.cyclic(); // counter 0: switches the multiplexer, no comparison
    AnalogInput::internalSupply.ret = bsp::BSP_ERROR;
    AnalogInput::garbageOnError     = 0U;
    f.system.cyclic(); // counter 1: comparison, but no reading
    CHECK(f.supplyTriggers() == 0U);
    CHECK(f.system.getAdcDiagnostics().skippedSupplyChecks == 1U);
    CHECK(f.system.getAdcDiagnostics().readErrors == 1U);
    // the multiplexer still advances: counter 2 selects the oscillator supply
    f.system.cyclic();
    CHECK(
        ((simRegisters.CHIPCTL & SIM_CHIPCTL_ADC_SUPPLY_MASK) >> SIM_CHIPCTL_ADC_SUPPLY_SHIFT)
        == 3U);
    // a good reading is compared again
    AnalogInput::internalSupply.ret   = bsp::BSP_OK;
    AnalogInput::internalSupply.value = rawOfSupply(3300U, REF_MV);
    f.system.cyclic();
    CHECK(f.supplyTriggers() == 0U);
    CHECK(f.system.getAdcDiagnostics().skippedSupplyChecks == 1U);
}

void testSupplyCheckSkippedWithoutValidReference()
{
    // No successful bandgap reading yet: the supply voltage cannot be computed at all.
    Fixture f;
    f.system.init();
    AnalogInput::bandgap.ret = bsp::BSP_ERROR;
    f.system.cyclic(); // counter 0
    f.system.cyclic(); // counter 1: comparison
    CHECK(f.supplyTriggers() == 0U);
    CHECK(f.system.getAdcDiagnostics().skippedSupplyChecks == 1U);
    CHECK(!f.system.getAdcDiagnostics().refVoltageValid);
}

void testSupplyOutOfRangeStillTriggers()
{
    Fixture f;
    f.system.init();
    f.system.cyclic();                                                 // counter 0
    AnalogInput::internalSupply.value = rawOfSupply(2800U, REF_MV);    // 3300 - 500 mV
    f.system.cyclic();                                                 // counter 1: comparison
    CHECK(f.supplyTriggers() == 1U);
    CHECK(
        f.supervisor().internalSuppliesMonitor.lastContext == SafeSystem::ADC_CONTEXT_OUT_OF_RANGE);
}

/* ---- SYS-41 (finding F14 of doc/safety/fmea.md, SM-20): a supply channel that keeps failing is reported ---- */

/** One cycle with the supply reading scripted: healthy value of the selected supply, or a failed reading. */
void supplyCycle(Fixture& f, bool const readingOk)
{
    answerSelectedSupply();
    AnalogInput::internalSupply.ret = readingOk ? bsp::BSP_OK : bsp::BSP_ERROR;
    f.system.cyclic();
}

void testPersistentSupplyReadFailureTriggersOnce()
{
    // Before SYS-41 every failed reading was skipped (skippedSupplyChecks) without a limit: a channel that never
    // delivers ended the supervision of all three supplies without any event.
    Fixture f;
    f.system.init();
    supplyCycle(f, true); // a reference is known, the bandgap stays healthy
    for (uint32_t i = 1U; i < SafeSystem::ADC_REF_ERROR_LIMIT; ++i)
    {
        supplyCycle(f, false);
        CHECK(f.supplyTriggers() == 0U);
    }
    supplyCycle(f, false); // the limit-th failure in a row
    CHECK(f.supplyTriggers() == 1U);
    CHECK(f.supervisor().internalSuppliesMonitor.lastContext == SafeSystem::ADC_CONTEXT_READ_FAILURE);
    CHECK(f.system.getAdcDiagnostics().consecutiveSupplyErrors == SafeSystem::ADC_REF_ERROR_LIMIT);
    // reported once, not in every further cycle (on the target the reaction resets anyway)
    for (uint32_t i = 0U; i < (3U * SafeSystem::ADC_REF_ERROR_LIMIT); ++i)
    {
        supplyCycle(f, false);
    }
    CHECK(f.supplyTriggers() == 1U);
    CHECK(f.refTriggers() == 0U);
}

void testSupplySeriesInterruptedByOneGoodReading()
{
    Fixture f;
    f.system.init();
    supplyCycle(f, true);
    for (int round = 0; round < 3; ++round)
    {
        for (uint32_t i = 1U; i < SafeSystem::ADC_REF_ERROR_LIMIT; ++i)
        {
            supplyCycle(f, false);
        }
        supplyCycle(f, true); // one good reading ends the series
        CHECK(f.system.getAdcDiagnostics().consecutiveSupplyErrors == 0U);
    }
    CHECK(f.supplyTriggers() == 0U);
}

void testMissingReferenceIsNotASupplyReadFailure()
{
    // Without a reference the supply comparison is left out as well, but that is the bandgap's limit (SM-19), not a
    // failure of the supply channel: no second event with a misleading cause.
    Fixture f;
    f.system.init();
    AnalogInput::bandgap.ret = bsp::BSP_ERROR;
    for (uint32_t i = 0U; i < (2U * SafeSystem::ADC_REF_ERROR_LIMIT); ++i)
    {
        supplyCycle(f, true);
    }
    CHECK(f.refTriggers() == 1U);
    CHECK(f.supplyTriggers() == 0U);
    CHECK(f.system.getAdcDiagnostics().consecutiveSupplyErrors == 0U);
}

/* ---- IO-19: the limit comes from the configuration, the counters can be read and cleared ---- */

void testConfiguredErrorLimit()
{
    // The stub of the generated module parameters says 4 (the demo project says 10): the production code must
    // use the configured value, not a constant of its own.
#if defined(NX_SAFESYSTEM_HOST_STUB_PROFILE)
    CHECK(SafeSystem::ADC_REF_ERROR_LIMIT == 4U);
#endif
    CHECK(SafeSystem::ADC_REF_ERROR_LIMIT == NX_MODULE_SAFESYSTEM_PARAM_ADC_READ_ERROR_LIMIT);
    Fixture f;
    f.system.init();
    AnalogInput::bandgap.ret = bsp::BSP_ERROR;
    for (uint32_t i = 1U; i < SafeSystem::ADC_REF_ERROR_LIMIT; ++i)
    {
        f.system.cyclic();
        CHECK(f.refTriggers() == 0U);
    }
    f.system.cyclic(); // the limit-th failure in a row (the fourth with the stubs)
    CHECK(f.refTriggers() == 1U);
    CHECK(f.supervisor().adcReferenceMonitor.lastContext == SafeSystem::ADC_CONTEXT_READ_FAILURE);
}

void testDiagnosticsAreCopiedAtomically()
{
    // The console task reads while the safety task writes: one exclusive section per read and per reset, so no
    // mixture of two cycles can be shown.
    Fixture f;
    f.system.init();
    f.system.cyclic();
    uint32_t const before = suspendCalls;
    (void)f.system.getAdcDiagnostics();
    CHECK(suspendCalls == before + 1U);
    CHECK(resumeCalls == before + 1U);
    f.system.resetAdcDiagnostics();
    CHECK(suspendCalls == before + 2U);
    CHECK(resumeCalls == before + 2U);
}

void testResetKeepsTheSupervisionState()
{
    Fixture f;
    f.system.init();
    f.system.cyclic();                          // one healthy cycle: a reference voltage is known
    AnalogInput::bandgap.ret = bsp::BSP_ERROR;
    f.system.cyclic();
    f.system.cyclic();
    auto const before = f.system.getAdcDiagnostics();
    CHECK(before.readErrors > 0U);
    CHECK(before.skippedRefChecks == 2U);
    CHECK(before.consecutiveRefErrors == 2U);
    f.system.resetAdcDiagnostics();
    auto const after = f.system.getAdcDiagnostics();
    CHECK(after.readErrors == 0U);
    CHECK(after.skippedRefChecks == 0U);
    CHECK(after.skippedSupplyChecks == 0U);
    // state of the supervision, not a statistic: clearing the counters must not hide a running series or
    // forget the last measured reference voltage
    CHECK(after.consecutiveRefErrors == 2U);
    CHECK(after.refVoltageMv == REF_MV);
    CHECK(after.refVoltageValid);
    // the series continues where it was: the remaining failures reach the limit (4 with the stubs) and report once
    for (uint32_t i = 3U; i < SafeSystem::ADC_REF_ERROR_LIMIT; ++i)
    {
        f.system.cyclic();
        CHECK(f.refTriggers() == 0U);
    }
    f.system.cyclic();
    CHECK(f.refTriggers() == 1U);
}

/* ---- SYS-95 (finding DFA-3 of doc/safety/dfa.md, DF-46): a converter that answers every channel alike ---- */

constexpr uint32_t VDD_3V3_FLASH = 4U; // SIM_CHIPCTL ADC_SUPPLY selector of the 3.3 V flash supply

/** Cycles until the multiplexer selects `selector` for the next reading, with healthy readings on the way. */
void stepTo(Fixture& f, uint32_t const selector)
{
    for (int i = 0; (i < 12) && (selectedSupply() != selector); ++i)
    {
        answerSelectedSupply();
        f.system.cyclic();
    }
}

void testStuckConverterOneCodeForBothChannels()
{
    // The converter answers every channel with the healthy bandgap code: the reference check is satisfied, only the
    // second channel tells. Reported in the first cycle that reads a 3.3 V supply, before the supply check's own
    // comparison (which needs the next odd step of the multiplexer).
    Fixture f;
    f.system.init();
    stepTo(f, VDD_3V3_FLASH);
    CHECK(f.refTriggers() == 0U);
    AnalogInput::internalSupply.value = BANDGAP_RAW;
    f.system.cyclic();
    CHECK(f.refTriggers() == 1U);
    CHECK(f.supervisor().adcReferenceMonitor.lastContext == SafeSystem::ADC_CONTEXT_CONVERTER_STUCK);
    // and in every cycle that reads a 3.3 V supply while the converter stays stuck: four of the six steps of the
    // multiplexer (the core supply, about 1.2 V, is too close to the bandgap to tell one code from the other)
    uint32_t const before = f.refTriggers();
    for (int i = 0; i < 6; ++i)
    {
        AnalogInput::internalSupply.value = BANDGAP_RAW;
        f.system.cyclic();
    }
    CHECK(f.refTriggers() == before + 4U);
}

void testConverterThatStoppedAtTheCoreSupply()
{
    // The converter stops converting: every slot keeps its last result, the supply slot a 3.3 V code while the
    // multiplexer has moved on to the 1.2 V core supply.
    Fixture f;
    f.system.init();
    stepTo(f, VDD_1V2_CORE);
    CHECK(f.refTriggers() == 0U);
    AnalogInput::internalSupply.value = rawOfSupply(3300U, REF_MV); // the result of the 3.3 V step before
    f.system.cyclic();
    CHECK(f.refTriggers() == 1U);
    CHECK(f.supervisor().adcReferenceMonitor.lastContext == SafeSystem::ADC_CONTEXT_CONVERTER_STUCK);
}

void testSeparationHoldsAcrossTheBands()
{
    // Healthy extremes never trip the converter check: the reference at both ends of its band, the 3.3 V supplies at
    // 3100 mV and the core at 1400 mV (the edges of the supply check), bandgap code as measured.
    for (uint32_t const bandgapRaw : {rawAtUpperLimit(), BANDGAP_RAW, rawAtLowerLimit()})
    {
        Fixture f;
        f.system.init();
        AnalogInput::bandgap.value = bandgapRaw;
        uint32_t const ref         = refOfRaw(bandgapRaw);
        for (uint32_t i = 0U; i < 24U; ++i)
        {
            uint32_t const mv = (selectedSupply() == VDD_1V2_CORE) ? 1400U : 3100U;
            AnalogInput::internalSupply.value = rawOfSupply(mv, ref);
            f.system.cyclic();
        }
        CHECK(f.refTriggers() == 0U);
    }
}

void testNoComparisonWithAFailedReading()
{
    // A failed reading is not compared: the bandgap failing while the supply returns the same garbage stays the case
    // of the read-error limit (context 2), and a failed supply reading never trips the converter check.
    Fixture f;
    f.system.init();
    stepTo(f, VDD_3V3_FLASH);
    AnalogInput::garbageOnError       = BANDGAP_RAW;
    AnalogInput::internalSupply.ret   = bsp::BSP_ERROR;
    f.system.cyclic();
    CHECK(f.refTriggers() == 0U);
    AnalogInput::internalSupply.ret   = bsp::BSP_OK;
    AnalogInput::internalSupply.value = BANDGAP_RAW;
    AnalogInput::bandgap.ret          = bsp::BSP_ERROR;
    for (uint32_t i = 0U; i < SafeSystem::ADC_REF_ERROR_LIMIT; ++i)
    {
        f.system.cyclic();
    }
    CHECK(f.refTriggers() == 1U);
    CHECK(f.supervisor().adcReferenceMonitor.lastContext == SafeSystem::ADC_CONTEXT_READ_FAILURE);
}

} // namespace

int main()
{
    testInit();
    testNominalCycles();
    testSingleReadErrorDoesNotTrigger();
    testPersistentReadErrorTriggersOnce();
    testReadErrorSeriesInterruptedByOneGoodReading();
    testRawZeroCountsAsReadError();
    testReferenceOutOfRangeStillTriggersImmediately();
    testSupplyCheckSkippedWithoutReading();
    testSupplyCheckSkippedWithoutValidReference();
    testSupplyOutOfRangeStillTriggers();
    testPersistentSupplyReadFailureTriggersOnce();
    testSupplySeriesInterruptedByOneGoodReading();
    testMissingReferenceIsNotASupplyReadFailure();
    testConfiguredReferenceBand();
    testConfiguredErrorLimit();
    testDiagnosticsAreCopiedAtomically();
    testResetKeepsTheSupervisionState();
    testStuckConverterOneCodeForBothChannels();
    testConverterThatStoppedAtTheCoreSupply();
    testSeparationHoldsAcrossTheBands();
    testNoComparisonWithAFailedReading();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
