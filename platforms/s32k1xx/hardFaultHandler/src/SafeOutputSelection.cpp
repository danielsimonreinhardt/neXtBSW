/* SYS-116: the extended safe outputs table, the selection words and the selection service of a project in which a PWM
 * group allows an active short (hardFaultHandler/safeOutputs.h, hardFaultHandler/safeStateSelection.h).
 *
 * Built only with NX_SAFE_OUTPUT_SELECT (library nxSafeOutputSelection, hardFaultHandler/CMakeLists.txt); every other
 * project does not compile this file. The table comes from the generated NX_SAFE_OUTPUTS_SELECT (tools/bswgen,
 * bsp/safeOutputs/safeOutputsConfiguration.h) and is checked here, at compile time, entry by entry against
 * NX_SAFE_OUTPUTS - the list the family port defines nxSafeOutputs from - so the walks with and without selection
 * switch the same registers in the same order, and the freewheel value of every entry is the one of nxSafeOutputs.
 *
 * Own library (not the handler library itself): it needs the generated headers (bspMcu), and its initialised
 * read-only data must not lie in a library the linker script treats as safety library (*libsafe*, .mpu_rodata).
 */

#include "bsp/safeOutputs/safeOutputsConfiguration.h"
#include "hardFaultHandler/safeOutputs.h"
#include "hardFaultHandler/safeStateSelection.h"

#include <cstddef>
#include <cstdint>

#if defined(NX_SAFE_STATE_LOCK_MEASURE)
// SYS-172 test build: measured lock (NX_SHORT_LOCK_MEASURE_COMMAND)
#include "hardFaultHandler/safeStateLockMeasure.h"
#if defined(NX_SAFE_STATE_LOCK_PROBE_GPIO)
#include "mcu/mcu.h" // GPIO register block of the probe pin
#endif
#endif

#if !defined(NX_SAFE_OUTPUT_SELECT) || !defined(NX_SAFE_OUTPUT_SELECT_GENERATED)
#error "SYS-116: the build (NX_SAFE_OUTPUT_SELECT) and the generated safe outputs table disagree - configure again"
#endif

/* SYS-172 (HFH-F06, review point SYS69-S3, decision C4): the service may be called from any task or interrupt - the
 * application task selects every cycle, the safety task and the hardware-path hold block. Every check of the block
 * together with the store that depends on it (select, start test begin), the stores of a block and the restore of the
 * start test end run under a short interrupt lock, so a block can never fall between a check and its store: a short
 * removed for the power cycle is never selected or forced again. The lock saves and restores PRIMASK (nesting
 * allowed: a failed start test blocks under the lock it already holds); it is held for a few loads and stores and, in
 * the start test and in the block of a group whose start test runs, one walk over the table.
 *
 * NX_SAFE_STATE_LOCK and NX_SAFE_STATE_PREEMPTION_POINT() are a test seam: a host test may define them before it
 * includes this file (test/host/SafeStateSelectHostTest.cpp: a lock model and the point at which a pending interrupt
 * would run); every other build gets the lock below and an empty point. Cortex-M only - this is the family port of
 * S32K1; a core of another family brings its own lock in its own port. */
#if !defined(NX_SAFE_STATE_LOCK)
namespace
{
#if defined(__ARM_ARCH_6M__) || defined(__ARM_ARCH_7M__) || defined(__ARM_ARCH_7EM__)
class InterruptLock
{
public:
    InterruptLock() { asm volatile("mrs %0, primask\n\tcpsid i" : "=r"(_primask) : : "memory"); }

    ~InterruptLock() { asm volatile("msr primask, %0" : : "r"(_primask) : "memory"); }

    InterruptLock(InterruptLock const&)            = delete;
    InterruptLock& operator=(InterruptLock const&) = delete;

private:
    uint32_t _primask;
};
#if defined(NX_SAFE_STATE_LOCK_MEASURE)
/* SYS-172 board proof (test build NX_SHORT_LOCK_MEASURE_COMMAND only, platforms/s32k1xx/port/main/CMakeLists.txt):
 * the same interrupt lock, measured. Inside the lock the cycles between taking and giving it back go per call site
 * into nxSafeStateLockStats (DWT cycle counter, ARMv7-M; ARMv6-M has none and counts 0), and an optional probe pin
 * (NX_SAFE_STATE_LOCK_PROBE_GPIO/_BIT, a GPIO output of the board chosen at configure time) is high while the
 * outermost lock is held - for the oscilloscope. Only written with interrupts disabled. */
#if defined(__ARM_ARCH_7M__) || defined(__ARM_ARCH_7EM__)
// ARMv7-M debug registers ([v7M] C1.6.5 DEMCR, C1.8.6 DWT_CTRL/DWT_CYCCNT, D1.2 software lock)
constexpr uintptr_t DEMCR_ADDRESS      = 0xE000EDFCU;
constexpr uint32_t DEMCR_TRCENA        = 1UL << 24U;
constexpr uintptr_t DWT_CTRL_ADDRESS   = 0xE0001000U;
constexpr uintptr_t DWT_CYCCNT_ADDRESS = 0xE0001004U;
constexpr uintptr_t DWT_LAR_ADDRESS    = 0xE0001FB0U;
constexpr uint32_t DWT_LAR_UNLOCK      = 0xC5ACCE55U;
constexpr uint32_t DWT_CTRL_CYCCNTENA  = 1UL;

inline volatile uint32_t& debugRegister(uintptr_t const address)
{
    return *reinterpret_cast<volatile uint32_t*>(address);
}

inline void enableCycleCounter()
{
    if ((debugRegister(DWT_CTRL_ADDRESS) & DWT_CTRL_CYCCNTENA) == 0U)
    {
        debugRegister(DEMCR_ADDRESS) |= DEMCR_TRCENA;
        debugRegister(DWT_LAR_ADDRESS) = DWT_LAR_UNLOCK; // RAZ/WI where the DWT has no software lock
        debugRegister(DWT_CTRL_ADDRESS) |= DWT_CTRL_CYCCNTENA;
    }
}

inline uint32_t cycleCount() { return debugRegister(DWT_CYCCNT_ADDRESS); }
#else
inline void enableCycleCounter() {}

inline uint32_t cycleCount() { return 0U; }
#endif

#if defined(NX_SAFE_STATE_LOCK_PROBE_GPIO) && defined(NX_SAFE_STATE_LOCK_PROBE_BIT)
inline void probeHigh() { NX_SAFE_STATE_LOCK_PROBE_GPIO->PSOR = (1UL << NX_SAFE_STATE_LOCK_PROBE_BIT); }

inline void probeLow() { NX_SAFE_STATE_LOCK_PROBE_GPIO->PCOR = (1UL << NX_SAFE_STATE_LOCK_PROBE_BIT); }
#else
inline void probeHigh() {}

inline void probeLow() {}
#endif

NxSafeStateLockStat lockStats[NX_SAFE_STATE_LOCK_SITE_COUNT];
uint32_t lockDepth; // nesting of the measured lock (a failed start test blocks under the lock it holds)

class MeasuredInterruptLock
{
public:
    MeasuredInterruptLock() : _primask(0U), _start(0U), _site(NX_SAFE_STATE_LOCK_SITE_COUNT)
    {
        asm volatile("mrs %0, primask\n\tcpsid i" : "=r"(_primask) : : "memory");
        enableCycleCounter();
        if (lockDepth == 0U)
        {
            probeHigh();
        }
        ++lockDepth;
        _start = cycleCount();
    }

    ~MeasuredInterruptLock()
    {
        uint32_t const cycles = cycleCount() - _start;
        if (_site < NX_SAFE_STATE_LOCK_SITE_COUNT)
        {
            NxSafeStateLockStat& stat = lockStats[_site];
            ++stat.count;
            stat.lastCycles = cycles;
            stat.maxCycles  = (cycles > stat.maxCycles) ? cycles : stat.maxCycles;
        }
        --lockDepth;
        if (lockDepth == 0U)
        {
            probeLow();
        }
        asm volatile("msr primask, %0" : : "r"(_primask) : "memory");
    }

    // the call site the measured cycles belong to (NX_SAFE_STATE_LOCK_SITE_*), set right after taking the lock
    void site(uint32_t const where) const { _site = where; }

    MeasuredInterruptLock(MeasuredInterruptLock const&)            = delete;
    MeasuredInterruptLock& operator=(MeasuredInterruptLock const&) = delete;

private:
    uint32_t _primask;
    uint32_t _start;
    mutable uint32_t _site;
};
#endif
#elif defined(__ARM_ARCH)
#error "SYS-172: no interrupt lock for this core in the S32K1 safe state selection"
#else
// host build of another unit's test (one thread, no interrupts): nothing to lock
class InterruptLock
{
public:
    InterruptLock() = default;
    ~InterruptLock() {}

    InterruptLock(InterruptLock const&)            = delete;
    InterruptLock& operator=(InterruptLock const&) = delete;
};
#endif
} // namespace
#if defined(NX_SAFE_STATE_LOCK_MEASURE) && defined(__ARM_ARCH)
#define NX_SAFE_STATE_LOCK              MeasuredInterruptLock
#define NX_SAFE_STATE_LOCK_SITE(lock, where) (lock).site(where)
#else
#define NX_SAFE_STATE_LOCK InterruptLock
#endif
#endif
#if !defined(NX_SAFE_STATE_PREEMPTION_POINT)
#define NX_SAFE_STATE_PREEMPTION_POINT() static_cast<void>(0)
#endif
// SYS-172: call site of a lock for the measured lock of the test build; nothing in every other build
#if !defined(NX_SAFE_STATE_LOCK_SITE)
#define NX_SAFE_STATE_LOCK_SITE(lock, site) static_cast<void>(0)
#endif

namespace
{
// clang-format off
#define NX_SAFE_OUTPUT_ENTRY(name, address, value, gate, gateMask) \
    {static_cast<uint32_t>(address), static_cast<uint32_t>(value), static_cast<uint32_t>(gate), \
     static_cast<uint32_t>(gateMask)},
#define NX_SAFE_SELECT_ENTRY(name, address, value, gate, gateMask, group, groupGate, groupGateMask, low, high) \
    {static_cast<uint32_t>(address), static_cast<uint32_t>(value), static_cast<uint32_t>(gate), \
     static_cast<uint32_t>(gateMask), static_cast<uint32_t>(group), static_cast<uint32_t>(groupGate), \
     static_cast<uint32_t>(groupGateMask), static_cast<uint32_t>(low), static_cast<uint32_t>(high)},
#define NX_SAFE_STATE_GROUP_ENTRY(name, index, allowed, test, testUs) \
    {static_cast<uint32_t>(index), static_cast<uint32_t>(allowed), static_cast<uint32_t>(test), \
     static_cast<uint32_t>(testUs)},
// clang-format on

struct Group
{
    uint32_t index;
    uint32_t allowed;
    uint32_t test;
    uint32_t testUs;
};

constexpr NxSafeOutput PLAIN[NX_SAFE_OUTPUT_COUNT + 1U] = {NX_SAFE_OUTPUTS(NX_SAFE_OUTPUT_ENTRY){0U, 0U, 0U, 0U}};
constexpr NxSafeOutputSelect SELECT[NX_SAFE_OUTPUT_COUNT + 1U]
    = {NX_SAFE_OUTPUTS_SELECT(NX_SAFE_SELECT_ENTRY){0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U}};
constexpr Group GROUPS[NX_SAFE_STATE_GROUP_COUNT] = {NX_SAFE_STATE_GROUPS(NX_SAFE_STATE_GROUP_ENTRY)};

constexpr uint32_t ALL_STATES
    = NX_SAFE_STATE_BIT(NX_SAFE_STATE_FREEWHEEL) | NX_SAFE_STATE_BIT(NX_SAFE_STATE_SHORT_LOW)
      | NX_SAFE_STATE_BIT(NX_SAFE_STATE_SHORT_HIGH);

// The same entries as nxSafeOutputs, in the same order, with its freewheel values; every short value of a group is
// either the freewheel value (state not allowed), 0 (an enable left as it is) or a value for the group's timer;
// every entry of a group has the timer's clock gate; the group numbers exist; the end entry last.
constexpr bool selectTableValid()
{
    uint32_t entries = 0U;
    for (uint32_t i = 0U; i < NX_SAFE_OUTPUT_COUNT; ++i)
    {
        NxSafeOutput const& p       = PLAIN[i];
        NxSafeOutputSelect const& s = SELECT[i];
        if ((s.address != p.address) || (s.value != p.value) || (s.gateAddress != p.gateAddress)
            || (s.gateMask != p.gateMask) || (s.address == 0U))
        {
            return false;
        }
        if (s.group == 0U)
        {
            if ((s.shortLow != s.value) || (s.shortHigh != s.value) || (s.groupGateAddress != 0U))
            {
                return false;
            }
        }
        else if ((s.group > NX_SAFE_STATE_GROUP_COUNT) || (s.groupGateAddress == 0U) || (s.groupGateMask == 0U))
        {
            return false;
        }
        else
        {
            uint32_t const allowed = GROUPS[s.group - 1U].allowed;
            if ((((allowed & NX_SAFE_STATE_BIT(NX_SAFE_STATE_SHORT_LOW)) == 0U) && (s.shortLow != s.value))
                || (((allowed & NX_SAFE_STATE_BIT(NX_SAFE_STATE_SHORT_HIGH)) == 0U) && (s.shortHigh != s.value)))
            {
                return false;
            }
        }
        ++entries;
    }
    return (entries == NX_SAFE_OUTPUT_COUNT) && (SELECT[NX_SAFE_OUTPUT_COUNT].address == 0U)
           && (PLAIN[NX_SAFE_OUTPUT_COUNT].address == 0U);
}

// Groups numbered 0..n-1 in order, freewheel always allowed, nothing beyond the three states, a test only with a
// short and a duration.
constexpr bool groupsValid()
{
    for (uint32_t g = 0U; g < NX_SAFE_STATE_GROUP_COUNT; ++g)
    {
        Group const& group = GROUPS[g];
        bool const shortAllowed = (group.allowed & ~NX_SAFE_STATE_BIT(NX_SAFE_STATE_FREEWHEEL)) != 0U;
        if ((group.index != g) || ((group.allowed & NX_SAFE_STATE_BIT(NX_SAFE_STATE_FREEWHEEL)) == 0U)
            || ((group.allowed & ~ALL_STATES) != 0U) || ((group.test != 0U) && (!shortAllowed || (group.testUs == 0U))))
        {
            return false;
        }
    }
    return true;
}

static_assert(selectTableValid(), "SYS-116: extended safe outputs table differs from nxSafeOutputs or is malformed");
static_assert(groupsValid(), "SYS-116: safe state groups malformed");
static_assert(NX_SAFE_STATE_WORD_SHORT_LOW != NX_SAFE_STATE_WORD_SHORT_HIGH, "patterns must differ");
#if defined(__ARM_ARCH)
static_assert(sizeof(NxSafeOutputSelect) == NX_SAFE_SELECT_SIZE, "layout shared with customHardFaultHandler");
#endif

// Short patterns by state; the selection words hold one of them or anything else (freewheel)
constexpr uint32_t wordOf(uint32_t const state)
{
    return (state == NX_SAFE_STATE_SHORT_LOW)    ? NX_SAFE_STATE_WORD_SHORT_LOW
           : (state == NX_SAFE_STATE_SHORT_HIGH) ? NX_SAFE_STATE_WORD_SHORT_HIGH
                                                 : 0U;
}

// Shorts removed for this power cycle (bridge fault, failed start test). Ordinary RAM like the selection words; a
// corrupted value other than 0 reads as blocked (safe side). A wild write of 0 only lets the service accept a request
// again - it selects nothing by itself.
volatile uint32_t shortBlocked[NX_SAFE_STATE_GROUP_COUNT];

// Start test: values found by begin, per entry of the table (only the entries of the tested group are used)
uint32_t testBefore[NX_SAFE_OUTPUT_COUNT + 1U];
uint32_t testGroup; // group + 1 while a test runs, 0 otherwise

bool isTestEntry(NxSafeOutputSelect const& e, uint32_t const group, uint32_t const state)
{
    uint32_t const value = (state == NX_SAFE_STATE_SHORT_LOW) ? e.shortLow : e.shortHigh;
    return (e.group == (group + 1U)) && (value != e.value) && (value != 0U);
}

bool groupClockOn(uint32_t const group)
{
    for (uint32_t i = 0U; i < NX_SAFE_OUTPUT_COUNT; ++i)
    {
        NxSafeOutputSelect const& e = nxSafeOutputsSelect[i];
        if (e.group == (group + 1U))
        {
            return (*NX_SAFE_OUTPUT_REG(e.groupGateAddress) & e.groupGateMask) != 0U;
        }
    }
    return false;
}

uint32_t testState; // state under test while testGroup != 0

// Under the lock: freewheel on the entries a running start test of `group` forced (the timer's output control), then
// no test running. Nothing if no test of `group` runs.
void endTestInFreewheel(uint32_t const group)
{
    if (testGroup != (group + 1U))
    {
        return;
    }
    uint32_t const state = testState;
    for (uint32_t i = 0U; i < NX_SAFE_OUTPUT_COUNT; ++i)
    {
        NxSafeOutputSelect const& e = nxSafeOutputsSelect[i];
        if (isTestEntry(e, group, state))
        {
            *NX_SAFE_OUTPUT_REG(e.address) = e.value;
        }
    }
    testGroup = 0U;
}
} // namespace

extern "C"
{
// Constant initializer only: placed in the flash (.rodata), read by the handlers without RAM.
NxSafeOutputSelect const nxSafeOutputsSelect[NX_SAFE_OUTPUT_COUNT + 1U]
    = {NX_SAFE_OUTPUTS_SELECT(NX_SAFE_SELECT_ENTRY){0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U}};

// Zero initialized (.bss of this library, ordinary RAM): freewheel after every reset.
volatile uint32_t nxSafeStateSelection[NX_SAFE_STATE_GROUP_COUNT];

uint32_t nxSafeStateGroupCount(void) { return NX_SAFE_STATE_GROUP_COUNT; }

uint32_t nxSafeStateAllowed(uint32_t const group)
{
    if (group >= NX_SAFE_STATE_GROUP_COUNT)
    {
        return 0U;
    }
    uint32_t const allowed = GROUPS[group].allowed;
    return (shortBlocked[group] != 0U) ? (allowed & NX_SAFE_STATE_BIT(NX_SAFE_STATE_FREEWHEEL)) : allowed;
}

int32_t nxSafeStateSelect(uint32_t const group, uint32_t const state)
{
    if (group >= NX_SAFE_STATE_GROUP_COUNT)
    {
        return NX_SAFE_STATE_ERROR_GROUP;
    }
    if ((state >= NX_SAFE_STATE_COUNT) || ((GROUPS[group].allowed & NX_SAFE_STATE_BIT(state)) == 0U))
    {
        return NX_SAFE_STATE_ERROR_NOT_ALLOWED;
    }
    NX_SAFE_STATE_LOCK const lock; // SYS-172 select: the check of the block and the store as one
    NX_SAFE_STATE_LOCK_SITE(lock, NX_SAFE_STATE_LOCK_SITE_SELECT);
    if ((state != NX_SAFE_STATE_FREEWHEEL) && (shortBlocked[group] != 0U))
    {
        return NX_SAFE_STATE_ERROR_BLOCKED;
    }
    NX_SAFE_STATE_PREEMPTION_POINT();
    nxSafeStateSelection[group] = wordOf(state);
    return NX_SAFE_STATE_OK;
}

uint32_t nxSafeStateSelected(uint32_t const group)
{
    if (group >= NX_SAFE_STATE_GROUP_COUNT)
    {
        return NX_SAFE_STATE_FREEWHEEL;
    }
    uint32_t const word = nxSafeStateSelection[group];
    return (word == NX_SAFE_STATE_WORD_SHORT_LOW)    ? NX_SAFE_STATE_SHORT_LOW
           : (word == NX_SAFE_STATE_WORD_SHORT_HIGH) ? NX_SAFE_STATE_SHORT_HIGH
                                                     : NX_SAFE_STATE_FREEWHEEL;
}

void nxSafeStateBlockShort(uint32_t const group)
{
    if (group < NX_SAFE_STATE_GROUP_COUNT)
    {
        NX_SAFE_STATE_LOCK const lock; // SYS-172 block: both stores and the end of a running test as one
        NX_SAFE_STATE_LOCK_SITE(lock, NX_SAFE_STATE_LOCK_SITE_BLOCK);
        // freewheel first: from here on every walk applies it, whatever happens to the block below
        nxSafeStateSelection[group] = 0U;
        NX_SAFE_STATE_PREEMPTION_POINT();
        shortBlocked[group] = 1U;
        // a running start test of the group forces the short as well: it ends in freewheel now, and its end reports a
        // sequence error (a failed test for the caller, SafeStateRte.h runStartTests)
        endTestInFreewheel(group);
    }
}

int32_t nxSafeStateShortTestBegin(uint32_t const group, uint32_t const state)
{
    if (group >= NX_SAFE_STATE_GROUP_COUNT)
    {
        return NX_SAFE_STATE_ERROR_GROUP;
    }
    NX_SAFE_STATE_LOCK const lock; // SYS-172 start test begin: the check of the block and the forcing as one
    NX_SAFE_STATE_LOCK_SITE(lock, NX_SAFE_STATE_LOCK_SITE_TEST_BEGIN);
    if (testGroup != 0U)
    {
        return NX_SAFE_STATE_ERROR_SEQUENCE;
    }
    if ((state == NX_SAFE_STATE_FREEWHEEL) || (state >= NX_SAFE_STATE_COUNT)
        || ((GROUPS[group].allowed & NX_SAFE_STATE_BIT(state)) == 0U))
    {
        return NX_SAFE_STATE_ERROR_NOT_ALLOWED;
    }
    if (shortBlocked[group] != 0U)
    {
        return NX_SAFE_STATE_ERROR_BLOCKED;
    }
    if (!groupClockOn(group))
    {
        return NX_SAFE_STATE_ERROR_CLOCK; // nothing forced; the test did not run (not a failure of the short)
    }
    NX_SAFE_STATE_PREEMPTION_POINT();
    testGroup = group + 1U;
    testState = state;
    int32_t result = NX_SAFE_STATE_OK;
    for (uint32_t i = 0U; i < NX_SAFE_OUTPUT_COUNT; ++i)
    {
        NxSafeOutputSelect const& e = nxSafeOutputsSelect[i];
        if (isTestEntry(e, group, state))
        {
            uint32_t const value = (state == NX_SAFE_STATE_SHORT_LOW) ? e.shortLow : e.shortHigh;
            testBefore[i]        = *NX_SAFE_OUTPUT_REG(e.address);
            *NX_SAFE_OUTPUT_REG(e.address) = value;
            if (*NX_SAFE_OUTPUT_REG(e.address) != value)
            {
                result = NX_SAFE_STATE_ERROR_READBACK;
            }
        }
    }
    if (result != NX_SAFE_STATE_OK)
    {
        nxSafeStateBlockShort(group); // begin failed: blocked, the forced entries in freewheel, no test running
    }
    return result;
}

int32_t nxSafeStateShortTestEnd(uint32_t const group)
{
    if (group >= NX_SAFE_STATE_GROUP_COUNT)
    {
        return NX_SAFE_STATE_ERROR_SEQUENCE;
    }
    NX_SAFE_STATE_LOCK const lock; // SYS-172 start test end: the check of the running test and the restore as one
    NX_SAFE_STATE_LOCK_SITE(lock, NX_SAFE_STATE_LOCK_SITE_TEST_END);
    if (testGroup != (group + 1U))
    {
        return NX_SAFE_STATE_ERROR_SEQUENCE; // never begun, or ended by a block (endTestInFreewheel)
    }
    uint32_t const state = testState;
    int32_t result       = NX_SAFE_STATE_OK;
    for (uint32_t i = 0U; i < NX_SAFE_OUTPUT_COUNT; ++i)
    {
        NxSafeOutputSelect const& e = nxSafeOutputsSelect[i];
        if (isTestEntry(e, group, state))
        {
            *NX_SAFE_OUTPUT_REG(e.address) = testBefore[i];
            if (*NX_SAFE_OUTPUT_REG(e.address) != testBefore[i])
            {
                result = NX_SAFE_STATE_ERROR_READBACK;
            }
        }
    }
    if (result != NX_SAFE_STATE_OK)
    {
        nxSafeStateBlockShort(group); // end failed: blocked, the forced entries in freewheel
    }
    testGroup = 0U;
    return result;
}

#if defined(NX_SAFE_STATE_LOCK_MEASURE) && defined(__ARM_ARCH)
// SYS-172 test build: readout of the measured lock (hardFaultHandler/safeStateLockMeasure.h)
int32_t nxSafeStateLockStatRead(uint32_t const site, NxSafeStateLockStat* const stat)
{
    if ((site >= NX_SAFE_STATE_LOCK_SITE_COUNT) || (stat == nullptr))
    {
        return -1;
    }
    InterruptLock const lock; // the three words of one site as one, not measured itself
    *stat = lockStats[site];
    return 0;
}

void nxSafeStateLockStatReset(void)
{
    InterruptLock const lock;
    for (NxSafeStateLockStat& stat : lockStats)
    {
        stat = NxSafeStateLockStat{0U, 0U, 0U};
    }
}

uint32_t nxSafeStateLockCycleCounter(void)
{
#if defined(__ARM_ARCH_7M__) || defined(__ARM_ARCH_7EM__)
    enableCycleCounter();
    return ((debugRegister(DWT_CTRL_ADDRESS) & DWT_CTRL_CYCCNTENA) != 0U) ? 1U : 0U;
#else
    return 0U;
#endif
}
#endif
}
