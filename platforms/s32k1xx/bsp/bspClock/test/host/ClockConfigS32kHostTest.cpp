// Host test of the s32k1xx clock driver with bounded waits (PLAT-85): src/clockConfig.cpp, the unchanged OpenBSW
// driver next to it (OpenBswClockConfig.cpp) and the start-up check src/ClockCheck.cpp with the report of
// platforms/common/clockCheck, against the behaviour model driverModel/mcu/mcu.h. No hardware, no board.
//
// What is shown:
//   1. Normal start-up: the copy writes exactly what the original writes, in the same order, with the same number of
//      polls (one read more: the valid flag of the planned source before SCG_RCCR is written; entered on SIRC one
//      more: is SIRC still the system clock) - a normal start-up is not slower. The plan is in place, both clock
//      monitors are on, the check says ok.
//   2. A clock that never comes (SOSC, SPLL, FIRC, SIRC) or a switch the SCG never confirms: the driver returns after
//      its budget of polls - at least the minimum wait at 112 MHz, one core cycle per poll - does not select the
//      clock, does not enable its monitor (a loss-of-clock reset would repeat on every start), still enables the
//      peripheral clocks, and nxClockCheck() reports what is missing. The original hangs in these cases (PLAT-72).
//   3. The budget is exact: a clock that becomes valid on the last poll is used, one poll later it is not.
//   4. Runtime switches (systemClock2Pll/Firc/Sirc) with a clock that does not come stay on the current clock.
//
// Profiles (include path): profiles/s32k148 (8 MHz crystal, SPLL 160 MHz, core 80 MHz; generated for
// projects/buildtest_s32k148_min), profiles/s32k116 (8 MHz crystal, core on FIRC, no SPLL; NX_TEST_S32K116),
// profiles/firc (S32K14x without crystal, core on FIRC).

#include "bsp/clock/boardClock.h"
#include "clock/clockConfig.h"
#include "clock/nxClockCheck.h"
#include "clock/nxClockCheckReport.h"
#include "mcu/mcu.h"

#include <cstdio>
#include <cstring>
#include <vector>

extern "C"
{
void openbsw_configurPll();
void openbsw_systemClock2Firc();
}

ScgModel scgModel;
PccModel pccModel;
RcmModel rcmModel;
SysTickModel sysTickModel;

#ifdef SCG_SPLLCSR_SPLLEN_MASK
#define HAS_SPLL 1
#else
#define HAS_SPLL 0
#endif

namespace
{
// Budgets of src/clockConfig.cpp, written down here on purpose: minimum wait in us times 112 polls per us (one core
// cycle per poll at the S32K1xx maximum of 112 MHz). A start loop reads its CSR twice per poll (read-modify-write of
// the enable, then the valid flag), a switch reads SCG_CSR once.
constexpr uint32_t SIRC_BUDGET   = 1000U * 112U;
constexpr uint32_t FIRC_BUDGET   = 1000U * 112U;
constexpr uint32_t SWITCH_BUDGET = 1000U * 112U;
#ifdef CPU_XTAL_CLK_HZ
constexpr uint32_t SOSC_BUDGET = 20000U * 112U;
#if HAS_SPLL
constexpr uint32_t SPLL_BUDGET = 5000U * 112U;
#endif
#endif

constexpr int32_t NEVER = -1;

// SCG_CSR after reset: FIRC, DIVCORE 1, DIVBUS 1, DIVSLOW 2
constexpr uint32_t CSR_RESET = SCG_CSR_SCS(3U) | SCG_CSR_DIVSLOW(1U);
constexpr uint32_t CSR_SIRC  = SCG_CSR_SCS(2U) | SCG_CSR_DIVSLOW(1U);

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
} // namespace

// ------------------------------------------------------------------------------------------------ behaviour model
namespace model
{
struct Oscillator
{
    char const* name;
    Reg* csr;
    uint32_t en, vld, sel, err, scs;
    bool enabled;
    bool valid;
    int32_t validAfterReads; // reads of the CSR while enabled until valid; NEVER: never valid, 0: at once
    uint32_t readsWhileStarting;
    uint32_t other; // monitor and other control bits as written
};

Oscillator sirc = {"SIRC", &scgModel.SIRCCSR, SCG_SIRCCSR_SIRCEN_MASK, SCG_SIRCCSR_SIRCVLD_MASK,
                   SCG_SIRCCSR_SIRCSEL_MASK, 0U, 2U, false, false, 0, 0U, 0U};
Oscillator firc = {"FIRC", &scgModel.FIRCCSR, SCG_FIRCCSR_FIRCEN_MASK, SCG_FIRCCSR_FIRCVLD_MASK,
                   SCG_FIRCCSR_FIRCSEL_MASK, SCG_FIRCCSR_FIRCERR_MASK, 3U, false, false, 0, 0U, 0U};
Oscillator sosc = {"SOSC", &scgModel.SOSCCSR, SCG_SOSCCSR_SOSCEN_MASK, SCG_SOSCCSR_SOSCVLD_MASK,
                   SCG_SOSCCSR_SOSCSEL_MASK, SCG_SOSCCSR_SOSCERR_MASK, 1U, false, false, 0, 0U, 0U};
#if HAS_SPLL
Oscillator spll = {"SPLL", &scgModel.SPLLCSR, SCG_SPLLCSR_SPLLEN_MASK, SCG_SPLLCSR_SPLLVLD_MASK,
                   SCG_SPLLCSR_SPLLSEL_MASK, SCG_SPLLCSR_SPLLERR_MASK, 6U, false, false, 0, 0U, 0U};
Oscillator* const oscillators[] = {&sirc, &firc, &sosc, &spll};
#else
Oscillator* const oscillators[] = {&sirc, &firc, &sosc};
#endif

bool switchIgnored = false;
uint32_t reads     = 0U;
uint32_t csrReads  = 0U;

struct Write
{
    Reg const* reg;
    uint32_t value;
};

std::vector<Write> writes;
std::vector<uint32_t> rccrWrites; // every clock switch request, apart from the trace

uint32_t systemClockCode() { return (scgModel.CSR.value & SCG_CSR_SCS_MASK) >> SCG_CSR_SCS_SHIFT; }

Oscillator* find(Reg const& reg)
{
    for (Oscillator* const osc : oscillators)
    {
        if (osc->csr == &reg)
        {
            return osc;
        }
    }
    return nullptr;
}

bool referenceValid(Oscillator const& osc)
{
#if HAS_SPLL
    return (&osc != &spll) || sosc.valid;
#else
    (void)osc;
    return true;
#endif
}

bool sourceValid(uint32_t const code)
{
    for (Oscillator const* const osc : oscillators)
    {
        if (osc->scs == code)
        {
            return osc->valid;
        }
    }
    return false;
}

uint32_t read(Reg const& reg)
{
    ++reads;
    if (&reg == &scgModel.CSR)
    {
        ++csrReads;
    }
    Oscillator* const osc = find(reg);
    if (osc == nullptr)
    {
        return reg.value;
    }
    if (osc->enabled && !osc->valid)
    {
        ++osc->readsWhileStarting;
        if ((osc->validAfterReads >= 0) && (osc->readsWhileStarting >= static_cast<uint32_t>(osc->validAfterReads))
            && referenceValid(*osc))
        {
            osc->valid = true;
        }
    }
    return osc->other | (osc->enabled ? osc->en : 0U) | (osc->valid ? osc->vld : 0U)
           | ((systemClockCode() == osc->scs) ? osc->sel : 0U);
}

void write(Reg& reg, uint32_t const value)
{
    // a start-up writes about a thousand registers; a budget that never ends must not fill the memory
    if (writes.size() < 100000U)
    {
        writes.push_back({&reg, value});
    }
    if ((&reg == &scgModel.RCCR) && (rccrWrites.size() < 1000000U))
    {
        rccrWrites.push_back(value);
    }
    Oscillator* const osc = find(reg);
    if (osc != nullptr)
    {
        if ((value & osc->en) != 0U)
        {
            if (!osc->enabled)
            {
                osc->enabled            = true;
                osc->readsWhileStarting = 0U;
                osc->valid              = (osc->validAfterReads == 0) && referenceValid(*osc);
            }
        }
        else if (systemClockCode() != osc->scs) // the SCG keeps the system clock running
        {
            osc->enabled = false;
            osc->valid   = false;
        }
        osc->other = value & ~(osc->en | osc->vld | osc->sel | osc->err);
        return;
    }
    if (&reg == &scgModel.CSR)
    {
        return; // read only
    }
    reg.value = value;
    if ((&reg == &scgModel.RCCR) && !switchIgnored
        && sourceValid((value & SCG_RCCR_SCS_MASK) >> SCG_RCCR_SCS_SHIFT))
    {
        scgModel.CSR.value = value;
    }
}

// Clears the storage of every register of a model block (the blocks consist of Reg only) without the write hook
template<typename Block>
void clear(Block& block)
{
    Reg* const regs = reinterpret_cast<Reg*>(&block);
    for (size_t i = 0U; i < (sizeof(Block) / sizeof(Reg)); ++i)
    {
        regs[i].value = 0U;
    }
}

// Power-on state: FIRC is the system clock, SIRC runs, SOSC and SPLL are off; every oscillator becomes valid a
// few reads after an enable (typical start-up), unless a test says otherwise
void reset()
{
    clear(scgModel);
    clear(pccModel);
    clear(rcmModel);
    clear(sysTickModel);
    for (Oscillator* const osc : oscillators)
    {
        osc->enabled            = false;
        osc->valid              = false;
        osc->readsWhileStarting = 0U;
        osc->other              = 0U;
    }
    sirc.enabled = sirc.valid = true;
    firc.enabled = firc.valid = true;
    sirc.validAfterReads      = 3;
    firc.validAfterReads      = 2;
    sosc.validAfterReads      = 1000;
#if HAS_SPLL
    spll.validAfterReads = 200;
#endif
    scgModel.CSR.value  = CSR_RESET;
    scgModel.RCCR.value = CSR_RESET;
    rcmModel.SRS.value  = RCM_SRS_POR_MASK;
    switchIgnored       = false;
    reads               = 0U;
    csrReads            = 0U;
    writes.clear();
    rccrWrites.clear();
}

// Entry on SIRC (e.g. a bootloader or a low power mode left it): FIRC off
void resetOnSirc()
{
    reset();
    scgModel.CSR.value  = CSR_SIRC;
    scgModel.RCCR.value = CSR_SIRC;
    firc.enabled = firc.valid = false;
}

char const* name(Reg const* const reg)
{
    for (Oscillator const* const osc : oscillators)
    {
        if (osc->csr == reg)
        {
            return osc->name;
        }
    }
    if (reg == &scgModel.RCCR)
    {
        return "RCCR";
    }
    if ((reg >= &pccModel.PCCn[0]) && (reg < &pccModel.PCCn[PCC_PCCn_COUNT]))
    {
        return "PCC";
    }
    return "other";
}
} // namespace model

// ------------------------------------------------------------------------------------------------ tests
namespace
{
using model::firc;
using model::sirc;
using model::sosc;
#if HAS_SPLL
using model::spll;
#endif

nxClockCheckResult runCheck()
{
    nxClockCheckResult result;
    memset(&result, 0xA5, sizeof(result));
    nxClockCheck(&result);
    return result;
}

// Reads of the CSR of an oscillator after its enable when its start loop ran exactly `polls` polls: two per poll
// (read-modify-write of the enable, then the valid flag), less the first read, which comes before the enable
uint32_t readsAfterEnable(uint32_t const polls) { return (2U * polls) - 1U; }

// The start loop of an oscillator that never came ran exactly its budget; one more read of the status afterwards is
// allowed (valid flag of the planned source, SIRC check of the driver)
bool budgetUsed(model::Oscillator const& osc, uint32_t const budget)
{
    return (osc.readsWhileStarting == readsAfterEnable(budget))
           || (osc.readsWhileStarting == (readsAfterEnable(budget) + 1U));
}

#ifdef CPU_XTAL_CLK_HZ
bool monitorOn(model::Oscillator const& osc, uint32_t const cm, uint32_t const cmre)
{
    return (osc.other & (cm | cmre)) == (cm | cmre);
}
#endif

bool rccrWrittenWith(uint32_t const value)
{
    for (uint32_t const written : model::rccrWrites)
    {
        if (written == value)
        {
            return true;
        }
    }
    return false;
}

// The peripheral clocks are enabled at the end of configurPll() also when a clock did not come (LPUART1 is the
// console of the eval board, on FIRCDIV2): the start-up got through
bool peripheralClocksEnabled()
{
    return ((pccModel.PCCn[PCC_LPUART1_INDEX].value & 0xC0000000U) == 0xC0000000U)
           && (rcmModel.SRIE.value == static_cast<uint32_t>(_RCM_SRIE_));
}

void printLine(nxClockCheckResult const& r)
{
    char line[256];
    (void)nxClockCheckFormat(&r, line, sizeof(line));
    printf("  %s\n", line);
}

// 1. Normal start-up: copy and original write the same, the copy reads one register more
void compareWithOriginal(bool const onSirc, char const* const what)
{
    char text[160];
    onSirc ? model::resetOnSirc() : model::reset();
    configurPll();
    std::vector<model::Write> const copyWrites = model::writes;
    uint32_t const copyReads                   = model::reads;
    uint32_t const copyCsr                     = scgModel.CSR.value;

    onSirc ? model::resetOnSirc() : model::reset();
    openbsw_configurPll();
    std::vector<model::Write> const originalWrites = model::writes;
    uint32_t const originalReads                   = model::reads;

    bool same = copyWrites.size() == originalWrites.size();
    for (size_t i = 0U; same && (i < copyWrites.size()); ++i)
    {
        if ((copyWrites[i].reg != originalWrites[i].reg) || (copyWrites[i].value != originalWrites[i].value))
        {
            printf("  write %u differs: copy %s 0x%08x, original %s 0x%08x\n", static_cast<unsigned>(i),
                   model::name(copyWrites[i].reg), copyWrites[i].value, model::name(originalWrites[i].reg),
                   originalWrites[i].value);
            same = false;
        }
    }
    snprintf(text, sizeof(text), "%s: same writes in the same order as the OpenBSW driver (%u writes)", what,
             static_cast<unsigned>(copyWrites.size()));
    check(same, text);
    // one read more: the valid flag of the planned source; on SIRC one more: is SIRC still the system clock
    uint32_t const extraReads = onSirc ? 2U : 1U;
    snprintf(text, sizeof(text), "%s: %u reads more than the OpenBSW driver (%u / %u)", what,
             static_cast<unsigned>(extraReads), static_cast<unsigned>(copyReads), static_cast<unsigned>(originalReads));
    check(copyReads == (originalReads + extraReads), text);
    snprintf(text, sizeof(text), "%s: system clock as planned", what);
    check(copyCsr == static_cast<uint32_t>(_SCG_RCCR_), text);
}

void testNormalStart()
{
    compareWithOriginal(false, "normal start on FIRC");
    compareWithOriginal(true, "normal start on SIRC");

    model::reset();
    configurPll();
    check(scgModel.CSR.value == static_cast<uint32_t>(_SCG_RCCR_), "normal start: SCG_CSR is the plan");
#ifdef CPU_XTAL_CLK_HZ
    check(monitorOn(sosc, SCG_SOSCCSR_SOSCCM_MASK, SCG_SOSCCSR_SOSCCMRE_MASK), "normal start: SOSC monitor on");
#if HAS_SPLL
    check(monitorOn(spll, SCG_SPLLCSR_SPLLCM_MASK, SCG_SPLLCSR_SPLLCMRE_MASK), "normal start: SPLL monitor on");
#endif
#endif
    check(peripheralClocksEnabled(), "normal start: peripheral clocks enabled");
    nxClockCheckResult const r = runCheck();
    check(r.fault == NX_CLOCK_CHECK_OK, "normal start: check ok");
    check(r.actualCoreHz == NX_CLOCK_CORE_HZ, "normal start: actual core clock is the planned one");
    check(nxClockCheckReport(nullptr) == 0, "normal start: report returns 0");
    printLine(r);
}

#ifdef CPU_XTAL_CLK_HZ
// 2. The crystal never starts
void testOscillatorNeverValid()
{
    model::reset();
    sosc.validAfterReads = NEVER;
    configurPll();
    check(budgetUsed(sosc, SOSC_BUDGET), "no crystal: gives up after the SOSC budget (20 ms at 112 MHz)");
    check(!monitorOn(sosc, SCG_SOSCCSR_SOSCCM_MASK, SCG_SOSCCSR_SOSCCMRE_MASK),
          "no crystal: no SOSC monitor (its reset would repeat on every start)");
    check(sosc.enabled, "no crystal: SOSC stays enabled (visible in SOSCCSR of the report line)");
#if HAS_SPLL
    check(!spll.enabled, "no crystal: SPLL not started without its reference");
#endif
    check(peripheralClocksEnabled(), "no crystal: start-up got through, peripheral clocks enabled");
    if ((static_cast<uint32_t>(_SCG_RCCR_) & SCG_RCCR_SCS_MASK) != SCG_RCCR_SCS(3U))
    {
        check(!rccrWrittenWith(static_cast<uint32_t>(_SCG_RCCR_)), "no crystal: planned source never requested");
        check(scgModel.CSR.value == CSR_RESET, "no crystal: core stays on FIRC");
    }
    nxClockCheckResult const r = runCheck();
    check(r.fault == NX_CLOCK_CHECK_OSC_NOT_READY, "no crystal: check reports OSC_NOT_READY");
    check(r.actualCoreHz == NX_CLOCK_FIRC_HZ, "no crystal: actual core clock FIRC");
    check((r.registers[1] & SCG_SOSCCSR_SOSCEN_MASK) != 0U, "no crystal: report shows SOSCCSR enabled, not valid");
    check(nxClockCheckReport(nullptr) == 1, "no crystal: report returns 1");
    printLine(r);
}

// 3. The budget is exact: valid on the last poll is used, one poll later is not
void testOscillatorBudgetEdge()
{
    model::reset();
    sosc.validAfterReads = static_cast<int32_t>(readsAfterEnable(SOSC_BUDGET));
    configurPll();
    check(sosc.valid && monitorOn(sosc, SCG_SOSCCSR_SOSCCM_MASK, SCG_SOSCCSR_SOSCCMRE_MASK),
          "crystal valid on the last poll of the budget: used and monitored");
    check(runCheck().fault == NX_CLOCK_CHECK_OK, "crystal valid on the last poll: check ok");

    model::reset();
    sosc.validAfterReads = static_cast<int32_t>(readsAfterEnable(SOSC_BUDGET) + 10U);
    configurPll();
    check(!monitorOn(sosc, SCG_SOSCCSR_SOSCCM_MASK, SCG_SOSCCSR_SOSCCMRE_MASK),
          "crystal valid a few polls after the budget: not monitored");
    check(runCheck().fault == NX_CLOCK_CHECK_OSC_NOT_READY, "crystal valid too late: reported");
}
#endif

#if defined(CPU_XTAL_CLK_HZ) && HAS_SPLL
void testPllNeverLocks()
{
    model::reset();
    spll.validAfterReads = NEVER;
    configurPll();
    check(budgetUsed(spll, SPLL_BUDGET), "SPLL without lock: gives up after the SPLL budget (5 ms)");
    check(monitorOn(sosc, SCG_SOSCCSR_SOSCCM_MASK, SCG_SOSCCSR_SOSCCMRE_MASK), "SPLL without lock: SOSC monitor on");
    check(!monitorOn(spll, SCG_SPLLCSR_SPLLCM_MASK, SCG_SPLLCSR_SPLLCMRE_MASK), "SPLL without lock: no SPLL monitor");
    check(scgModel.CSR.value == CSR_RESET, "SPLL without lock: core stays on FIRC");
    check(peripheralClocksEnabled(), "SPLL without lock: peripheral clocks enabled");
    nxClockCheckResult const r = runCheck();
    check(r.fault == NX_CLOCK_CHECK_PLL_NOT_LOCKED, "SPLL without lock: check reports PLL_NOT_LOCKED");
    printLine(r);
}
#endif

// FIRC does not start while the core runs on SIRC: stay on SIRC, do not try to stop it
void testFircNeverValid()
{
    model::resetOnSirc();
    firc.validAfterReads = NEVER;
    configurPll();
    check(budgetUsed(firc, FIRC_BUDGET), "FIRC missing: gives up after the FIRC budget (1 ms)");
    check(!rccrWrittenWith(CSR_RESET), "FIRC missing: no switch to FIRC requested");
    check(sirc.enabled && sirc.valid, "FIRC missing: SIRC (the running clock) kept");
    check(peripheralClocksEnabled(), "FIRC missing: start-up got through");
    if ((static_cast<uint32_t>(_SCG_RCCR_) & SCG_RCCR_SCS_MASK) == SCG_RCCR_SCS(3U))
    {
        check(scgModel.CSR.value == CSR_SIRC, "FIRC missing, plan FIRC: core stays on SIRC");
    }
    else
    {
        check(scgModel.CSR.value == static_cast<uint32_t>(_SCG_RCCR_), "FIRC missing: planned SPLL still reached");
    }
    nxClockCheckResult const r = runCheck();
    check(r.fault == NX_CLOCK_CHECK_OSC_NOT_READY, "FIRC missing: check reports OSC_NOT_READY");
    printLine(r);
}

// SIRC does not start again after the driver stopped it: stay on FIRC
void testSircNeverValid()
{
    model::reset();
    sirc.validAfterReads = NEVER;
    configurPll();
    check(budgetUsed(sirc, SIRC_BUDGET), "SIRC missing: gives up after the SIRC budget (1 ms)");
    check(!rccrWrittenWith(CSR_SIRC), "SIRC missing: no switch to SIRC requested");
    check(scgModel.CSR.value == static_cast<uint32_t>(_SCG_RCCR_), "SIRC missing: plan still reached");
    check(peripheralClocksEnabled(), "SIRC missing: start-up got through");
    nxClockCheckResult const r = runCheck();
    check(r.fault == NX_CLOCK_CHECK_OSC_NOT_READY, "SIRC missing: check reports OSC_NOT_READY");
    printLine(r);
}

// The SCG does not confirm a switch
void testSwitchNotConfirmed()
{
    model::reset();
    model::switchIgnored = true;
    configurPll();
    check(peripheralClocksEnabled(), "switch not confirmed: start-up got through");
    check(model::csrReads <= 3U * SWITCH_BUDGET, "switch not confirmed: every switch bounded by its budget");
    check(model::csrReads >= SWITCH_BUDGET, "switch not confirmed: waited at least one budget");
    if (static_cast<uint32_t>(_SCG_RCCR_) != CSR_RESET)
    {
        check(runCheck().fault == NX_CLOCK_CHECK_SOURCE_MISMATCH, "switch not confirmed: SOURCE_MISMATCH");
    }
}

// 4. Runtime switches with a clock that does not come
void testRuntimeSwitches()
{
    model::reset();
    configurPll();
    systemClock2Firc();
    uint32_t const onFirc = scgModel.CSR.value; // the plan itself when it is FIRC, else FIRC with the driver dividers
    check((onFirc & SCG_CSR_SCS_MASK) == SCG_CSR_SCS(3U), "runtime: switch to FIRC");
#if HAS_SPLL
    pllOff();
#endif
#ifdef CPU_XTAL_CLK_HZ
    extOscOff();
    sosc.validAfterReads = NEVER;
#if HAS_SPLL
    systemClock2Pll();
    check(scgModel.CSR.value == onFirc, "runtime: systemClock2Pll without crystal stays on FIRC");
    check(!monitorOn(sosc, SCG_SOSCCSR_SOSCCM_MASK, SCG_SOSCCSR_SOSCCMRE_MASK),
          "runtime: no SOSC monitor without crystal");
    check(!spll.enabled, "runtime: SPLL not started without crystal");
    check(budgetUsed(sosc, SOSC_BUDGET), "runtime: SOSC start bounded");
#endif
#endif
    sirc.validAfterReads = NEVER;
    scgModel.SIRCCSR     = 0U; // stopped
    systemClock2Sirc();
    check(scgModel.CSR.value == onFirc, "runtime: systemClock2Sirc without SIRC stays on FIRC");

    model::reset();
    scgModel.CSR.value = CSR_SIRC;
    firc.enabled = firc.valid = false;
    firc.validAfterReads      = NEVER;
    systemClock2Firc();
    check(scgModel.CSR.value == CSR_SIRC, "runtime: systemClock2Firc without FIRC stays on SIRC");
    check(!rccrWrittenWith(CSR_RESET), "runtime: no switch to FIRC requested without FIRC");
    check(budgetUsed(firc, FIRC_BUDGET), "runtime: FIRC start bounded");

    // same situation, original: would wait for ever - so only shown with a FIRC that comes late
    model::reset();
    scgModel.CSR.value = CSR_SIRC;
    firc.enabled = firc.valid = false;
    firc.validAfterReads      = 50;
    openbsw_systemClock2Firc();
    check(scgModel.CSR.value == (SCG_CSR_SCS(3U) | SCG_CSR_DIVSLOW(1U)), "runtime original: FIRC late, switched");
}

} // namespace

int main()
{
    testNormalStart();
#ifdef CPU_XTAL_CLK_HZ
    testOscillatorNeverValid();
    testOscillatorBudgetEdge();
#endif
#if defined(CPU_XTAL_CLK_HZ) && HAS_SPLL
    testPllNeverLocks();
#endif
    testFircNeverValid();
    testSircNeverValid();
    testSwitchNotConfirmed();
    testRuntimeSwitches();
    printf("clock_config_s32k_host_test (%lu Hz): %d checks, %d failures\n",
           static_cast<unsigned long>(NX_CLOCK_CORE_HZ), checks, failures);
    return (failures == 0) ? 0 : 1;
}
