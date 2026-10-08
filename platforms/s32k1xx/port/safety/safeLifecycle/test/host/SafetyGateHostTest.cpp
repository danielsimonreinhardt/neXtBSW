/* SYS-42 (finding F15 of doc/safety/fmea.md, SM-11): host test of the MPU state check on entry and exit of the safety
 * cycle with the real code of the target - SafetyManager::cyclic (safeLifecycle/SafetyManager.cpp), the real fusa
 * gate (safeMemory/MemoryProtection.cpp with ProtectedRamScopedUnlock.h and mpu.h) and the real SafeSupervisor
 * (reaction) - on a register model of the system MPU (gateStubs/mcu/mcu.h: stuck bits and lost writes per
 * descriptor word). The descriptors are the ones MemoryProtection::init() writes; the other sub-checks of the cycle
 * are the stubs of the safety cycle test (stubs/, p period timer, m memory, s system, o IO, W watchdog; E log error,
 * R reset). The period timer's hook (checkCycleTimer) runs inside the cycle with the gate open and serves as the
 * place where a fault is injected "during the cycle".
 *
 * What it shows:
 * - healthy: the gate is closed (read-only word in RGD[FUSA_REGION]) before and after every cycle and open while the
 *   checks run; no event in 24 cycles, the interrupt lock of the scoped unlock is balanced; a nested scoped unlock
 *   inside the cycle is harmless;
 * - left open: a gate that is open when the cycle starts (opened and not closed by other code, or the restore at the
 *   end of the previous cycle lost) is MPU_UNLOCKED_ON_SAFETY_MANAGER_ENTRY, S1 + S2 - before the first check;
 * - corrupted: every single bit flipped in the gate word reads as "not locked" and is reported on entry - the check
 *   compares the whole word, so a corrupted descriptor errs on the safe side;
 * - still closed: a gate closed during the cycle, or an unlock whose write never reached the MPU, is
 *   MPU_LOCKED_ON_SAFETY_MANAGER_EXIT, S1 + S2 (on the target the safety task's writes would fault as well; the exit
 *   check is the witness of the software).
 *
 * Built-in faults: test_safety_gate_mutations.py (SYS-42) applies deliberate faults to SafetyManager.cpp,
 * MemoryProtection.cpp and ProtectedRamScopedUnlock.h and expects this test to fail for each of them.
 *
 * -Wno-shorten-64-to-32: MemoryProtection::init() stores linker addresses (uintptr_t) in 32-bit descriptor fields,
 * which is exact on the target and truncates on the 64-bit host; the test does not depend on those addresses.
 *
 * Build and run (zig c++, no hardware; tools/test entry safety-gate):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion \
 *       -Wno-shorten-64-to-32 -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeLifecycle/test/host/gateStubs \
 *       -Iplatforms/s32k1xx/port/safety/safeMemory/include \
 *       -Iplatforms/s32k1xx/port/safety/safeLifecycle/test/host/stubs \
 *       -Iplatforms/s32k1xx/port/safety/safeLifecycle/include \
 *       -Iecu/safety/safeSupervisor/include -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       platforms/s32k1xx/port/safety/safeLifecycle/test/host/SafetyGateHostTest.cpp \
 *       platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/SafetyManager.cpp \
 *       platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/MemoryProtection.cpp \
 *       ecu/safety/safeWatchdog/src/safeWatchdog/CyclePeriodCheck.cpp -o build/host/safetygate.exe
 *   build/host/safetygate.exe
 */

#include <cstdint>
#include <cstdio>
#include <string>

namespace test_trace
{
std::string trace;
int lockDepth = 0;

void add(char const c) { trace.push_back(c); }
} // namespace test_trace

#define NX_SAFE_SUPERVISOR_HOLDING()              (false)
#define NX_SAFE_SUPERVISOR_CONSOLE(format, event) ((void)(format), (void)(event), ::test_trace::add('P'))

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

#include "../../../../../../../ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp" // NOLINT

#include <safeLifecycle/SafetyManager.h>
#include <safeMemory/MemoryLocations.h>
#include <safeMemory/MemoryProtection.h>
#include <safeMemory/ProtectedRamScopedUnlock.h>
#include <safeMemory/mpu.h>
#include <safeSupervisionCheck/LpitDeadmanCheck.h>
#include <safeSupervisionCheck/LptmrCycleTimer.h>
#include <safeWatchdog/CyclePeriodCheck.h>

// The register model and the linker symbols MemoryProtection::init() reads (safeMemory/MemoryLocations.h)
MpuType mpuRegisters{};
uint32_t const __MPU_BSS_START[1] = {0U};
uint32_t const __MPU_BSS_END[1]   = {0U};
// SYS-97: linker symbols of the guard below the interrupt stack; init() reads them, the tests below place the guard
// themselves (initWithStackGuard) because host addresses say nothing about the target layout
uint32_t const __StackGuardStart[1] = {0U};
uint32_t const __StackGuardEnd[1]   = {0U};
::async::internal::Stack<safety_task_stackSize> safetyStack{0x20006000U};

namespace test_cycle
{
bool gateLocked = true; // declared by stubs/cycleStubs.h, unused here: the gate is the real one
} // namespace test_cycle

namespace test_gate
{
void (*duringCycle)() = nullptr; // fault injected inside the cycle, with the gate open
int cycles            = 0;
int openDuringCycle   = 0; // cycles in which the checks saw the gate open
} // namespace test_gate

namespace safety
{
SafetyEventMailbox rteTimingMailbox;

namespace supervision
{
namespace test
{
LpitDeadmanConfiguration const* expected = nullptr;
uint32_t result                          = 0U;
uint32_t counter                         = 0U;
uint32_t timerFields                     = 0U;
int timerReads                           = 0;
int timerStarts                          = 0;
} // namespace test

void startCycleTimer() { ++test::timerStarts; }

uint32_t readCycleTimer()
{
    ++test::timerReads;
    return test::counter & CYCLE_TIMER_MASK;
}

// The first sub-check of the cycle: samples the gate and injects the fault of the test
uint32_t checkCycleTimer()
{
    ::test_trace::add('p');
    ++::test_gate::cycles;
    if (!MemoryProtection::fusaGateIsLocked())
    {
        ++::test_gate::openDuringCycle;
    }
    if (::test_gate::duringCycle != nullptr)
    {
        ::test_gate::duringCycle();
    }
    return test::timerFields;
}
} // namespace supervision
} // namespace safety

extern ::safety::CyclePeriodCheck safetyCyclePeriodCheck;

namespace
{
using ::safety::MemoryProtection;
using ::safety::SafeSupervisor;
using ::safety::SafetyManager;
namespace svt = ::safety::supervision::test;

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

test_mpu::Word& gateWord() { return mpuRegisters.RGD[MemoryProtection::FUSA_REGION].WORD2; }

// SYS-97: size of the guard of the tests (the demo linker script has 0x200)
constexpr uint32_t GUARD_BYTES = 0x200U;
uint32_t guardStart            = 0U; // where powerOn() placed the guard

void clearMpu()
{
    for (auto& rgd : mpuRegisters.RGD)
    {
        rgd.WORD0.reset();
        rgd.WORD1.reset();
        rgd.WORD2.reset();
        rgd.WORD3.reset();
        // 32-byte granularity of the silicon: start address bits 4..0 read 0, end address bits 4..0 read 1
        rgd.WORD0.stuckAtZero = 0x1FU;
        rgd.WORD1.stuckAtOne  = 0x1FU;
    }
    mpuRegisters.CESR = MPU_CESR_NRGD(NX_MPU_CESR_NRGD);
}

/**
 * SYS-97: a guard address inside the full-access region behind the safety stack (descriptor 5 as init() wrote it),
 * above every other region: the protected RAM comes from host addresses (__MPU_BSS_START/END, truncated to 32 bits),
 * so descriptors 1 and 3 may end anywhere - the guard goes above both, the safety stack of the stand-in is fixed.
 */
uint32_t guardInsideAFullAccessRegion()
{
    clearMpu();
    MemoryProtection::initWithStackGuard(0U, 0U); // descriptors 1..5 without a guard
    uint32_t lowest = static_cast<uint32_t>(mpuRegisters.RGD[5].WORD0);
    for (unsigned const slot : {1U, 3U})
    {
        uint32_t const start = mpuRegisters.RGD[slot].WORD0;
        uint32_t const end   = mpuRegisters.RGD[slot].WORD1;
        if ((start <= end) && (end >= lowest) && (end < 0xFFFF0000U))
        {
            lowest = end + 1U;
        }
    }
    return (lowest + 0x1000U) & ~0x1FU;
}

/** Power-on: MPU registers and faults cleared, the descriptors of MemoryProtection::init(), a fresh supervisor. */
SafeSupervisor& powerOn()
{
    guardStart = guardInsideAFullAccessRegion();
    clearMpu();
    MemoryProtection::initWithStackGuard(guardStart, guardStart + GUARD_BYTES);
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    supervisor.leaveLimpHome();
    svt::counter     = 0U;
    svt::timerFields = 0U;
    ::safetyCyclePeriodCheck.restart();
    ::test_gate::duringCycle     = nullptr;
    ::test_gate::cycles          = 0;
    ::test_gate::openDuringCycle = 0;
    ::test_trace::trace.clear();
    ::test_trace::lockDepth = 0;
    return supervisor;
}

void cycle(SafetyManager& manager)
{
    svt::counter += 10U; // a nominal 10 ms cycle of the period check
    manager.cyclic();
}

std::string healthy(int const cycles)
{
    std::string t;
    for (int i = 1; i <= cycles; ++i)
    {
        t += 'p';
        if ((i % 8) == 0)
        {
            t += 'W';
        }
        t += "mso";
    }
    return t;
}

void testHealthyCyclesOpenTheGateOnlyInside()
{
    auto& supervisor = powerOn();
    uint8_t failedRegion = 0U;
    CHECK(::safety::mpu::isGlobalEnabled());
    CHECK(MemoryProtection::fusaGateIsLocked()); // init() leaves the gate closed
    SafetyManager manager;
    for (int i = 0; i < 24; ++i)
    {
        cycle(manager);
        CHECK(MemoryProtection::fusaGateIsLocked()); // closed again after every cycle
    }
    CHECK(::test_trace::trace == healthy(24));
    CHECK(!supervisor.inLimpHomeState());
    CHECK(::test_gate::cycles == 24);
    CHECK(::test_gate::openDuringCycle == 24); // open while the checks run
    CHECK(::test_trace::lockDepth == 0);
    // the cycle touches the gate word only: the other descriptors are still what init() wrote
    MemoryProtection::fusaGateOpen(); // areRegionsConfiguredCorrectly() expects the gate open (safety context)
    CHECK(MemoryProtection::areRegionsConfiguredCorrectly(failedRegion));
    MemoryProtection::fusaGateClose();
    if (::test_trace::trace != healthy(24))
    {
        std::printf("  trace '%s'\n", ::test_trace::trace.c_str());
    }
}

void nestedUnlock() { ::safety::ProtectedRamScopedUnlock const nested; }

void testNestedUnlockInsideTheCycleIsHarmless()
{
    auto& supervisor = powerOn();
    SafetyManager manager;
    ::test_gate::duringCycle = &nestedUnlock;
    for (int i = 0; i < 3; ++i)
    {
        cycle(manager);
    }
    CHECK(::test_trace::trace == healthy(3));
    CHECK(!supervisor.inLimpHomeState());
    CHECK(MemoryProtection::fusaGateIsLocked());
}

void testGateLeftOpenIsReportedOnEntry()
{
    auto& supervisor = powerOn();
    SafetyManager manager;
    cycle(manager);
    MemoryProtection::fusaGateOpen(); // other code opened the gate and did not close it
    ::test_trace::trace.clear();
    cycle(manager);
    CHECK(::test_trace::trace == "EPRpmso"); // before the first check of the cycle (SYS-59: with its line)
    CHECK(supervisor.inLimpHomeState());
}

void testCorruptedGateWordIsReportedOnEntry()
{
    int reported = 0;
    for (uint32_t bit = 0U; bit < 32U; ++bit)
    {
        powerOn();
        SafetyManager manager;
        cycle(manager);
        uint32_t const mask = static_cast<uint32_t>(1U) << bit;
        if ((gateWord() & mask) != 0U)
        {
            gateWord().stuckAtZero = mask;
        }
        else
        {
            gateWord().stuckAtOne = mask;
        }
        ::test_trace::trace.clear();
        cycle(manager);
        if (::test_trace::trace.substr(0, 3) == "EPR")
        {
            ++reported;
        }
        else
        {
            std::printf("  bit %u: trace '%s'\n", static_cast<unsigned>(bit), ::test_trace::trace.c_str());
        }
    }
    CHECK(reported == 32);
}

void closeGate() { MemoryProtection::fusaGateClose(); }

void testGateClosedDuringTheCycleIsReportedOnExit()
{
    auto& supervisor = powerOn();
    SafetyManager manager;
    cycle(manager);
    ::test_gate::duringCycle = &closeGate;
    ::test_trace::trace.clear();
    cycle(manager);
    CHECK(::test_trace::trace == "pmsoEPR"); // after the last check, at the exit (SYS-59: with its line)
    CHECK(supervisor.inLimpHomeState());
}

void testLostUnlockIsReportedOnExit()
{
    auto& supervisor = powerOn();
    SafetyManager manager;
    cycle(manager);
    gateWord().dropWrites = true; // the unlock never reaches the MPU
    ::test_trace::trace.clear();
    cycle(manager);
    CHECK(::test_gate::openDuringCycle == 1); // the second cycle ran with the gate closed
    CHECK(::test_trace::trace == "pmsoEPR");
    CHECK(supervisor.inLimpHomeState());
}

void loseTheRestore() { gateWord().dropWrites = true; }

void testLostRestoreIsReportedAtTheNextEntry()
{
    auto& supervisor = powerOn();
    SafetyManager manager;
    ::test_gate::duringCycle = &loseTheRestore; // after the unlock: the closing write at the end is lost
    cycle(manager);
    CHECK(::test_trace::trace == "pmso"); // nothing to see in this cycle
    CHECK(!MemoryProtection::fusaGateIsLocked());
    ::test_gate::duringCycle = nullptr;
    gateWord().dropWrites    = false;
    ::test_trace::trace.clear();
    cycle(manager);
    CHECK(::test_trace::trace == "EPRpmso");
    CHECK(supervisor.inLimpHomeState());
}

// ---------------------------------------------------------------- SYS-97: guard below the interrupt stack

/**
 * What the core (bus master 0, privileged) may do at an address after the descriptors in the model: the SYSMPU
 * grants an access when any valid descriptor covering the address grants it (RGD0 gives the core nothing).
 * M0SM (word 2 bits 4..3): 0 = rwx, 1 = r-x, 2 = rw-, 3 = as M0UM (bits 2..0: r = 4, w = 2, x = 1).
 */
bool coreMay(uint32_t const address, bool const write)
{
    for (uint8_t slot = 1U; slot < ::safety::mpu::SMPU_ENTRYS; ++slot)
    {
        auto const& rgd = mpuRegisters.RGD[slot];
        if (((static_cast<uint32_t>(rgd.WORD3) & 1U) == 0U) || (address < static_cast<uint32_t>(rgd.WORD0))
            || (address > static_cast<uint32_t>(rgd.WORD1)))
        {
            continue;
        }
        uint32_t const word2 = rgd.WORD2;
        uint32_t const sm    = (word2 >> 3U) & 3U;
        uint32_t const um    = word2 & 7U;
        bool const granted   = write ? ((sm == 0U) || (sm == 2U) || ((sm == 3U) && ((um & 2U) != 0U)))
                                     : ((sm != 3U) || ((um & 4U) != 0U));
        if (granted)
        {
            return true;
        }
    }
    return false;
}

void testStackGuardIsReadOnlyForTheCore()
{
    powerOn();
    uint32_t const guardEnd = guardStart + GUARD_BYTES; // the interrupt stack starts here
    uint8_t failedRegion    = 0U;
    MemoryProtection::fusaGateOpen();
    CHECK(MemoryProtection::areRegionsConfiguredCorrectly(failedRegion));
    MemoryProtection::fusaGateClose();
    // the guard has its own descriptor, exactly the guard
    auto const& guard = mpuRegisters.RGD[MemoryProtection::STACK_GUARD_REGION];
    CHECK(static_cast<uint32_t>(guard.WORD0) == guardStart);
    CHECK(static_cast<uint32_t>(guard.WORD1) == (guardEnd - 1U));
    CHECK((static_cast<uint32_t>(guard.WORD3) & 1U) == 1U);
    // every word of the guard: the core reads, never writes
    for (uint32_t a = guardStart; a < guardEnd; a += 4U)
    {
        CHECK(!coreMay(a, true));
        CHECK(coreMay(a, false));
    }
    // around it everything is as before: the heap below and the stack above stay writable
    CHECK(coreMay(guardStart - 4U, true));
    CHECK(coreMay(guardEnd, true));
    CHECK(coreMay(guardEnd + 0x100U, true));
    // the rest of the address space the old region covered is still full access (descriptor 7 up to its end)
    CHECK(coreMay(0xFFFFFFF0U, true));
}

void testGuardOutsideAFullAccessRegionIsReported()
{
    // a guard on the safety stack (descriptor 4, fixed address of the stand-in): no full-access region contains it,
    // so no descriptor can make it read-only - the region check must fail and name the guard, not pass without one
    // descriptor 1 ends below the protected RAM, a host address: only when it ends below the safety stack is the
    // layout the one of the target (on a host where it does not, descriptor 1 legitimately contains the guard)
    clearMpu();
    MemoryProtection::initWithStackGuard(0U, 0U);
    uint32_t const beforeProtectedRamEnd = mpuRegisters.RGD[1].WORD1;
    clearMpu();
    MemoryProtection::initWithStackGuard(0x20006000U, 0x20006000U + GUARD_BYTES);
    uint8_t failedRegion = 0U;
    MemoryProtection::fusaGateOpen();
    if (beforeProtectedRamEnd < 0x20006000U)
    {
        CHECK(!MemoryProtection::areRegionsConfiguredCorrectly(failedRegion));
        CHECK(failedRegion == MemoryProtection::STACK_GUARD_REGION);
    }
    else
    {
        std::printf("  note: host layout puts the protected RAM above the safety stack, guard-on-stack case skipped\n");
    }
    // and an empty guard (linker symbols missing or swapped) likewise
    clearMpu();
    MemoryProtection::initWithStackGuard(0x1000U, 0x1000U);
    failedRegion = 0U;
    MemoryProtection::fusaGateOpen();
    CHECK(!MemoryProtection::areRegionsConfiguredCorrectly(failedRegion));
    CHECK(failedRegion == MemoryProtection::STACK_GUARD_REGION);
    MemoryProtection::fusaGateClose();
}

void testChangedGuardDescriptorIsReported()
{
    // the cyclic region check covers the guard like every other descriptor: full access written into its word 2
    // (a guard that no longer protects) or its valid bit cleared is a configuration error
    for (int variant = 0; variant < 2; ++variant)
    {
        powerOn();
        auto& guard = mpuRegisters.RGD[MemoryProtection::STACK_GUARD_REGION];
        if (variant == 0)
        {
            guard.WORD2 = static_cast<uint32_t>(mpuRegisters.RGD[1].WORD2); // full access of descriptor 1
        }
        else
        {
            guard.WORD3 = 0U;
        }
        uint8_t failedRegion = 0U;
        MemoryProtection::fusaGateOpen();
        CHECK(!MemoryProtection::areRegionsConfiguredCorrectly(failedRegion));
        MemoryProtection::fusaGateClose();
        CHECK(coreMay(guardStart, true) == (variant == 0));
    }
}
} // namespace

int main()
{
    testHealthyCyclesOpenTheGateOnlyInside();
    testNestedUnlockInsideTheCycleIsHarmless();
    testGateLeftOpenIsReportedOnEntry();
    testCorruptedGateWordIsReportedOnEntry();
    testGateClosedDuringTheCycleIsReportedOnExit();
    testLostUnlockIsReportedOnExit();
    testLostRestoreIsReportedAtTheNextEntry();
    testStackGuardIsReadOnlyForTheCore();
    testGuardOutsideAFullAccessRegionIsReported();
    testChangedGuardDescriptorIsReported();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
