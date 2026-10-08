// Host test of the s32k1xx clock start-up check (PLAT-72): ClockCheck.cpp and the start-up report of
// platforms/common/clockCheck against the register model in model/mcu/mcu.h (SCG and RCM with the layout and bit masks
// of the real device headers). No hardware, no board.
//
// The test sets the register images a start-up leaves behind - as planned, without a valid oscillator, without PLL
// lock, after a clock switch, after a clock monitor reset. That the clock driver of the family (src/clockConfig.cpp,
// bounded waits since PLAT-85) really leaves these images, and that the check then reports them, shows the driver
// test ClockConfigS32kHostTest.cpp against a behaviour model of the SCG.
//
// Profiles (include path, tools/test/tests.yaml): profiles/evalboard (40 MHz SOSC, SPLL, core 80 MHz; S32K148 device
// headers), profiles/s32k116 (8 MHz crystal, core on FIRC, derivative without SPLL; NX_TEST_S32K116), profiles/firc
// (S32K14x without crystal).

#include "bsp/clock/boardClock.h"
#include "clock/nxClockCheck.h"
#include "clock/nxClockCheckReport.h"
#include "mcu/mcu.h"

#include <cstdio>
#include <cstring>

SCG_Type scgModel;
RCM_Type rcmModel;

#ifdef SCG_SPLLCSR_SPLLEN_MASK
#define HAS_SPLL 1
#else
#define HAS_SPLL 0
#endif

namespace
{
int checks   = 0;
int failures = 0;

void check(bool const condition, char const* const what)
{
    ++checks;
    if (!condition)
    {
        ++failures;
        printf("FAIL: %s\n", what);
    }
}

// SCG_CSR after fircSysClk() of the driver: FIRC, DIVCORE 1, DIVBUS 1, DIVSLOW 2 (SCS 3, DIVSLOW code 1)
constexpr uint32_t CSR_FIRC_OF_DRIVER = SCG_CSR_SCS(3U) | SCG_CSR_DIVSLOW(1U);

void reset()
{
    scgModel = SCG_Type{};
    rcmModel = RCM_Type{};
}

// Register image after a start-up as planned: CSR = RCCR of the plan, oscillator and PLL valid with their clock
// monitors enabled (soscMon/spllMon of the driver), reset reason power-on
void setPlannedState()
{
    reset();
    scgModel.CSR = static_cast<uint32_t>(_SCG_RCCR_);
    // PLAT-85: SIRC and FIRC run in every plan (the driver starts both)
    scgModel.SIRCCSR = SCG_SIRCCSR_SIRCEN_MASK | SCG_SIRCCSR_SIRCVLD_MASK;
    scgModel.FIRCCSR = SCG_FIRCCSR_FIRCEN_MASK | SCG_FIRCCSR_FIRCVLD_MASK;
#ifdef CPU_XTAL_CLK_HZ
    scgModel.SOSCCSR = SCG_SOSCCSR_SOSCEN_MASK | SCG_SOSCCSR_SOSCCM_MASK | SCG_SOSCCSR_SOSCCMRE_MASK
                       | SCG_SOSCCSR_SOSCVLD_MASK;
#if HAS_SPLL
    scgModel.SPLLCSR = SCG_SPLLCSR_SPLLEN_MASK | SCG_SPLLCSR_SPLLCM_MASK | SCG_SPLLCSR_SPLLCMRE_MASK
                       | SCG_SPLLCSR_SPLLVLD_MASK;
#endif
#endif
    rcmModel.SRS = RCM_SRS_POR_MASK;
}

nxClockCheckResult runCheck()
{
    nxClockCheckResult result;
    memset(&result, 0xA5, sizeof(result));
    nxClockCheck(&result);
    return result;
}

void testPlannedState()
{
    setPlannedState();
    nxClockCheckResult const r = runCheck();
    check(r.fault == NX_CLOCK_CHECK_OK, "planned state: no fault");
    check(r.monitor == NX_CLOCK_CHECK_MONITOR_NONE, "planned state: no monitor event after power-on");
    check(r.plannedCoreHz == NX_CLOCK_CORE_HZ, "planned core clock from the clock plan");
    check(r.actualCoreHz == NX_CLOCK_CORE_HZ, "planned state: actual core clock derived from SCG_CSR");
#if HAS_SPLL && defined(CPU_XTAL_CLK_HZ)
    check(r.source == NX_CLOCK_SOURCE_PLL, "planned state: source is the SPLL");
    check(r.pllInput == NX_CLOCK_SOURCE_EXTERNAL, "planned state: SPLL reference is the crystal");
#else
    check(r.source != NX_CLOCK_SOURCE_UNKNOWN, "planned state: source known");
    check(r.pllInput == NX_CLOCK_SOURCE_UNKNOWN, "planned state without SPLL: no PLL reference");
#endif
    char okLine[256];
    (void)nxClockCheckFormat(&r, okLine, sizeof(okLine));
    check(strncmp(okLine, "clock: ok, core ", 16U) == 0, "planned state: line starts with ok");
    check(strstr(okLine, " Hz, source ") != nullptr, "planned state: line names the clock source");
#if HAS_SPLL && defined(CPU_XTAL_CLK_HZ)
    check(strstr(okLine, ", source PLL on external oscillator") != nullptr, "planned state: SPLL on the crystal");
#endif
    printf("  %s\n", okLine);
    check(r.registers[0] == scgModel.CSR, "register 0 is SCG_CSR");
    check(r.registers[1] == scgModel.SOSCCSR, "register 1 is SCG_SOSCCSR");
#if HAS_SPLL
    check(r.registers[2] == scgModel.SPLLCSR, "register 2 is SCG_SPLLCSR");
#else
    check(r.registers[2] == 0U, "register 2 is 0 without SPLL");
#endif
}

#ifdef CPU_XTAL_CLK_HZ
// The crystal does not start: the driver gives up after its budget and stays on FIRC (PLAT-85)
void testOscillatorMissing()
{
    setPlannedState();
    scgModel.SOSCCSR = SCG_SOSCCSR_SOSCEN_MASK; // enabled, never valid
#if HAS_SPLL
    scgModel.SPLLCSR = 0U;
#endif
    scgModel.CSR = CSR_FIRC_OF_DRIVER;
    nxClockCheckResult const r = runCheck();
    check(r.fault == NX_CLOCK_CHECK_OSC_NOT_READY, "missing crystal: OSC_NOT_READY");
    check(r.actualCoreHz == NX_CLOCK_FIRC_HZ, "missing crystal: core on FIRC");
    check(r.source == NX_CLOCK_SOURCE_INTERNAL_FAST, "missing crystal: source is the internal fast RC (FIRC)");
    check(r.pllInput == NX_CLOCK_SOURCE_UNKNOWN, "missing crystal: no PLL reference");
#if NX_CLOCK_CORE_HZ == NX_CLOCK_FIRC_HZ
    // S32K116 profile: the core clock is right, but the peripherals on SOSC (LPUART0, FTM0, LPTMR0 ...) have none
    check(r.actualCoreHz == r.plannedCoreHz, "missing crystal with core on FIRC: fault although the core clock fits");
#endif
    char line[256];
    int const length = nxClockCheckFormat(&r, line, sizeof(line));
    check((length > 0) && (static_cast<size_t>(length) < sizeof(line)), "report line fits");
    check(strstr(line, "clock: FAULT oscillator not ready") == line, "report line names the fault");
    check(strstr(line, ", source internal fast RC (regs ") != nullptr, "report line names the fallback source");
    printf("  %s\n", line);
}
#endif

#if defined(CPU_XTAL_CLK_HZ) && HAS_SPLL
void testPllNotLocked()
{
    setPlannedState();
    scgModel.SPLLCSR = SCG_SPLLCSR_SPLLEN_MASK;
    scgModel.CSR     = CSR_FIRC_OF_DRIVER;
    nxClockCheckResult const r = runCheck();
    check(r.fault == NX_CLOCK_CHECK_PLL_NOT_LOCKED, "SPLL without lock: PLL_NOT_LOCKED");
    check(r.actualCoreHz == NX_CLOCK_FIRC_HZ, "SPLL without lock: core on FIRC");
}
#endif

// PLAT-85: an internal oscillator the driver could not start (it gives up after its budget and does not switch to
// it) - OSC_NOT_READY first, in start order before the crystal
void testInternalOscillatorMissing()
{
    setPlannedState();
    scgModel.SIRCCSR = SCG_SIRCCSR_SIRCEN_MASK; // enabled, never valid
    nxClockCheckResult r = runCheck();
    check(r.fault == NX_CLOCK_CHECK_OSC_NOT_READY, "SIRC not valid: OSC_NOT_READY");
    check(r.actualCoreHz == NX_CLOCK_CORE_HZ, "SIRC not valid: core clock itself as planned");

    setPlannedState();
    scgModel.FIRCCSR = SCG_FIRCCSR_FIRCEN_MASK;
    check(runCheck().fault == NX_CLOCK_CHECK_OSC_NOT_READY, "FIRC not valid: OSC_NOT_READY");

#if defined(CPU_XTAL_CLK_HZ) && HAS_SPLL
    // FIRC missing and SPLL not locked: the first in start order is reported
    setPlannedState();
    scgModel.FIRCCSR = 0U;
    scgModel.SPLLCSR = SCG_SPLLCSR_SPLLEN_MASK;
    check(runCheck().fault == NX_CLOCK_CHECK_OSC_NOT_READY, "FIRC and SPLL missing: FIRC first (start order)");
#endif
}

void testSourceAndDividers()
{
    // low power idle (clock/systemClockSwitch.h switchToFirc) left active: FIRC / 1
    setPlannedState();
    scgModel.CSR = CSR_FIRC_OF_DRIVER;
    nxClockCheckResult r = runCheck();
    if (static_cast<uint32_t>(_SCG_RCCR_) != CSR_FIRC_OF_DRIVER)
    {
        check(r.fault == NX_CLOCK_CHECK_SOURCE_MISMATCH, "FIRC instead of the plan: SOURCE_MISMATCH");
    }
    check(r.actualCoreHz == NX_CLOCK_FIRC_HZ, "FIRC / 1: FIRC frequency");

    // each divider of the plan changed on its own
    uint32_t const fields[3] = {SCG_CSR_DIVCORE(1U), SCG_CSR_DIVBUS(1U), SCG_CSR_DIVSLOW(1U)};
    for (uint32_t const field : fields)
    {
        setPlannedState();
        scgModel.CSR = scgModel.CSR ^ field;
        check(runCheck().fault == NX_CLOCK_CHECK_SOURCE_MISMATCH, "other divider: SOURCE_MISMATCH");
    }

    // DIVCORE decoded: planned source with divider 4
    setPlannedState();
    scgModel.CSR = (scgModel.CSR & ~SCG_CSR_DIVCORE_MASK) | SCG_CSR_DIVCORE(3U);
    r            = runCheck();
    uint32_t const plannedDivider
        = ((static_cast<uint32_t>(_SCG_RCCR_) & SCG_CSR_DIVCORE_MASK) >> SCG_CSR_DIVCORE_SHIFT) + 1U;
    check(r.actualCoreHz == ((NX_CLOCK_CORE_HZ * plannedDivider) / 4U), "DIVCORE 4 decoded");

    // every source code
    setPlannedState();
    scgModel.CSR = SCG_CSR_SCS(2U) | SCG_CSR_DIVCORE(1U);
    check(runCheck().actualCoreHz == (NX_CLOCK_SIRC_HZ / 2U), "SIRC / 2");
    check(runCheck().source == NX_CLOCK_SOURCE_INTERNAL_SLOW, "source of SCS 2: internal slow RC (SIRC)");
    scgModel.CSR = SCG_CSR_SCS(1U);
    check(runCheck().source == NX_CLOCK_SOURCE_EXTERNAL, "source of SCS 1: external oscillator (SOSC)");
    check(runCheck().pllInput == NX_CLOCK_SOURCE_UNKNOWN, "no PLL reference without the PLL");
#ifdef NX_CLOCK_SOSC_HZ
    check(runCheck().actualCoreHz == NX_CLOCK_SOSC_HZ, "SOSC / 1");
#else
    check(runCheck().actualCoreHz == 0U, "SOSC without crystal in the plan: not derivable");
#endif
    scgModel.CSR = SCG_CSR_SCS(6U);
#ifdef NX_CLOCK_SPLL_HZ
    check(runCheck().actualCoreHz == NX_CLOCK_SPLL_HZ, "SPLL / 1");
#else
    check(runCheck().actualCoreHz == 0U, "SPLL without PLL in the plan: not derivable");
#endif
    scgModel.CSR = 0U; // reserved code
    check(runCheck().actualCoreHz == 0U, "reserved source code: not derivable");
}

void testMonitor()
{
    setPlannedState();
    rcmModel.SRS                = RCM_SRS_LOC_MASK;
    nxClockCheckResult const r1 = runCheck();
    check(r1.monitor == NX_CLOCK_CHECK_MONITOR_EVENT, "reset by loss of clock: monitor event");
    check(r1.fault == NX_CLOCK_CHECK_OK, "reset by loss of clock: the clock is fine again");
    char line[256];
    (void)nxClockCheckFormat(&r1, line, sizeof(line));
    check(strstr(line, "clock: WARNING") == line, "report line of a monitor event is a warning");
    check(strstr(line, "clock monitor event") != nullptr, "report line names the monitor event");
#ifdef RCM_SRS_LOL_MASK
    setPlannedState();
    rcmModel.SRS = RCM_SRS_LOL_MASK;
    check(runCheck().monitor == NX_CLOCK_CHECK_MONITOR_EVENT, "reset by loss of lock: monitor event");
#endif
    setPlannedState();
    scgModel.SOSCCSR = scgModel.SOSCCSR | SCG_SOSCCSR_SOSCERR_MASK;
    check(runCheck().monitor == NX_CLOCK_CHECK_MONITOR_EVENT, "SOSCERR pending: monitor event");
#if HAS_SPLL
    setPlannedState();
    scgModel.SPLLCSR = scgModel.SPLLCSR | SCG_SPLLCSR_SPLLERR_MASK;
    check(runCheck().monitor == NX_CLOCK_CHECK_MONITOR_EVENT, "SPLLERR pending: monitor event");
#endif
    setPlannedState();
    rcmModel.SRS = RCM_SRS_POR_MASK | 0x20U; // power-on and watchdog: no clock reason
    check(runCheck().monitor == NX_CLOCK_CHECK_MONITOR_NONE, "other reset reasons: no monitor event");
}

void testReport()
{
    setPlannedState();
    nxClockCheckResult r;
    check(nxClockCheckReport(&r) == 0, "report of the planned state returns 0");
    check(r.actualCoreHz == NX_CLOCK_CORE_HZ, "report copies the result");
    rcmModel.SRS = RCM_SRS_LOC_MASK;
    check(nxClockCheckReport(nullptr) == 1, "report of a monitor event returns 1");
    setPlannedState();
    scgModel.CSR = scgModel.CSR ^ SCG_CSR_DIVBUS(1U);
    check(nxClockCheckReport(nullptr) == 1, "report of a deviating divider returns 1");
}

// Longest possible line: every number at its maximum, longest fault text, monitor event
void testLongestLine()
{
    nxClockCheckResult r;
    r.fault         = NX_CLOCK_CHECK_SOURCE_MISMATCH;
    r.monitor       = NX_CLOCK_CHECK_MONITOR_EVENT;
    r.plannedCoreHz = 0xFFFFFFFFU;
    r.actualCoreHz  = 0xFFFFFFFFU;
    r.source        = NX_CLOCK_SOURCE_PLL;
    r.pllInput      = NX_CLOCK_SOURCE_EXTERNAL;
    for (uint32_t& value : r.registers)
    {
        value = 0xFFFFFFFFU;
    }
    char line[256];
    int const length = nxClockCheckFormat(&r, line, sizeof(line));
    check(length == 200, "longest line has 200 characters (buffer of nxClockCheckReport: 256)");
    check(strlen(nxClockSourceText(NX_CLOCK_SOURCE_PLL, NX_CLOCK_SOURCE_EXTERNAL)) == 26U, "longest source text");
    check(strcmp(nxClockCheckFaultText(99U), "unknown fault") == 0, "unknown fault code");
}

} // namespace

int main()
{
    testPlannedState();
#ifdef CPU_XTAL_CLK_HZ
    testOscillatorMissing();
#endif
#if defined(CPU_XTAL_CLK_HZ) && HAS_SPLL
    testPllNotLocked();
#endif
    testInternalOscillatorMissing();
    testSourceAndDividers();
    testMonitor();
    testReport();
    testLongestLine();
    printf("clock_check_s32k_host_test (%lu Hz): %d checks, %d failures\n",
           static_cast<unsigned long>(NX_CLOCK_CORE_HZ), checks, failures);
    return (failures == 0) ? 0 : 1;
}
