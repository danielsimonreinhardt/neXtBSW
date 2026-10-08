/* SYS-37: host test with a memory protection that really faults - the host MMU in the role of the MPU fusa gate.
 *
 * Board finding 2026-09-21 (SYS-05, captures 25/26): a deadline violation in the RTE task called
 * SafeSupervisor::getInstance().rteTimingMonitor.trigger(context). safeMonitor::Trigger stores the context in the
 * monitor, which lies in the MPU protected safety RAM, read-only outside the safety shell - IMPRECISERR, HardFault,
 * reset loop. The host tests before did not see it: on the host every byte is writable.
 *
 * Here the supervisor is constructed in a page of its own, and the page is made read-only whenever the test plays
 * a context without write access (an RTE task: "gate closed") and writable when it plays the safety task ("gate
 * open", SafetyManager::cyclic). A write into the closed page is caught by the fault handler of the host (vectored
 * exception handler on Windows, SIGSEGV elsewhere), counted as an MPU violation and then allowed, so the test ends
 * with a count instead of a crash. What it shows:
 * - rteTimingMonitor.trigger() with the gate closed writes nothing into the supervisor's page (0 violations): the
 *   report waits in the box, which lies in ordinary RAM;
 * - the safety task, gate open, delivers it: handle() runs with the reporter's context (0 violations);
 * - SYS-98 (STK-3/STK-4): a failed ETL_ASSERT (reportEtlAssert(), from the handler registered with
 *   etl::set_assert_function) and an ECC error from a fault interrupt (reportEccError()) in a context without write
 *   access reset with its line and marks the reset-loop record in ordinary RAM
 *   without one write into the page; positive control: the kernel fault reaction, which sets limp home, is caught
 *   when called there;
 * - positive control: the declaration before SYS-37 - a plain safeMonitor::Trigger in the protected page,
 *   triggered with the gate closed - is caught as a violation. That is the board finding, reproduced on the host.
 *
 * Build and run (zig c++, no hardware; also in tools/test/tests.yaml as safe-supervisor-mpu):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iecu/safety/safeSupervisor/include -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       ecu/safety/safeSupervisor/test/host/SafetyContextMpuHostTest.cpp -o build/host/safetycontextmpu.exe
 */

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <new>
#include <string>

#ifdef _WIN32
#include <windows.h>
#else
#include <signal.h>
#include <sys/mman.h>
#include <unistd.h>
#endif

namespace test_trace
{
std::string trace;
int lockDepth = 0;

void add(char const c) { trace.push_back(c); }

void print(char const*, char const*) { add('P'); }
} // namespace test_trace

#define NX_SAFE_SUPERVISOR_HOLDING()                 (false)
#define NX_SAFE_SUPERVISOR_CONSOLE(format, event) (::test_trace::print(format, event))

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

#include "../../src/safeSupervisor/SafeSupervisor.cpp" // NOLINT: the unit under test

namespace safety
{
// The box of the deferred RTE monitor: ordinary RAM, outside the protected page (on the target: main.cpp of the
// family port, general .bss outside .mpu_bss)
SafetyEventMailbox rteTimingMailbox;
} // namespace safety

namespace mpu
{
uint8_t* page         = nullptr;
size_t pageSize       = 0U;
int volatile faults   = 0; // writes into the closed page
bool volatile closed  = false;

bool inPage(void const* const address)
{
    auto const a = reinterpret_cast<uintptr_t>(address);
    auto const p = reinterpret_cast<uintptr_t>(page);
    return (a >= p) && (a < (p + pageSize));
}

#ifdef _WIN32
void protect(bool const readOnly)
{
    DWORD old = 0U;
    (void)VirtualProtect(page, pageSize, readOnly ? PAGE_READONLY : PAGE_READWRITE, &old);
}

LONG CALLBACK onFault(EXCEPTION_POINTERS* const info)
{
    EXCEPTION_RECORD const& record = *info->ExceptionRecord;
    if ((record.ExceptionCode == EXCEPTION_ACCESS_VIOLATION) && (record.NumberParameters >= 2U)
        && (record.ExceptionInformation[0] == 1U)
        && inPage(reinterpret_cast<void const*>(record.ExceptionInformation[1])))
    {
        faults = faults + 1;
        protect(false); // let the write through, count it
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

bool setUp()
{
    SYSTEM_INFO info;
    GetSystemInfo(&info);
    pageSize = info.dwPageSize;
    page     = static_cast<uint8_t*>(VirtualAlloc(nullptr, pageSize, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    return (page != nullptr) && (AddVectoredExceptionHandler(1U, &onFault) != nullptr);
}
#else
void protect(bool const readOnly)
{
    (void)mprotect(page, pageSize, readOnly ? PROT_READ : (PROT_READ | PROT_WRITE));
}

void onFault(int, siginfo_t* const info, void*)
{
    if (!inPage(info->si_addr))
    {
        _exit(3);
    }
    faults = faults + 1;
    protect(false);
}

bool setUp()
{
    pageSize = static_cast<size_t>(sysconf(_SC_PAGESIZE));
    void* const p = mmap(nullptr, pageSize, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED)
    {
        return false;
    }
    page = static_cast<uint8_t*>(p);
    struct sigaction action = {};
    action.sa_sigaction     = &onFault;
    action.sa_flags         = SA_SIGINFO;
    return (sigaction(SIGSEGV, &action, nullptr) == 0) && (sigaction(SIGBUS, &action, nullptr) == 0);
}
#endif

/** Context without write access (RTE task, console task, BSP task): the page is read-only. */
void closeGate()
{
    protect(true);
    closed = true;
}

/** Safety task (SafetyManager::cyclic, ProtectedRamScopedUnlock) or SafetyShell: the page is writable. */
void openGate()
{
    protect(false);
    closed = false;
}
} // namespace mpu

namespace
{
using ::safety::SafeSupervisor;
using Event = SafeSupervisor::Event;

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

/** The supervisor in the protected page, constructed with the gate open as main() does inside its SafetyShell. */
SafeSupervisor& supervisorInProtectedPage()
{
    ::mpu::openGate();
    static_assert(sizeof(SafeSupervisor) <= 4096U, "the supervisor must fit into one page");
    auto* const supervisor = new (::mpu::page) SafeSupervisor();
    supervisor->leaveLimpHome();
    (void)::safety::rteTimingMailbox.drain([](uint32_t) {});
    ::test_trace::trace.clear();
    ::mpu::faults = 0;
    return *supervisor;
}

/** The report of the RTE task writes nothing into the protected RAM; the safety task delivers it. */
void testRteReportFromClosedGate()
{
    auto& supervisor = supervisorInProtectedPage();
    CHECK(::mpu::inPage(&supervisor.rteTimingMonitor));
    CHECK(!::mpu::inPage(&::safety::rteTimingMailbox));

    ::mpu::closeGate(); // RTE task
    supervisor.rteTimingMonitor.trigger(0x01010010U); // what RteSystem.cpp reportTimingViolation() calls
    CHECK(::mpu::faults == 0);
    CHECK(::test_trace::trace.empty());
    CHECK(::safety::rteTimingMailbox.pending() == 1U);

    ::mpu::openGate(); // safety task, SafetyManager::cyclic
    supervisor.deliverDeferredEvents();
    ::mpu::closeGate();
    CHECK(::mpu::faults == 0);
    CHECK(::test_trace::trace == "E");
    CHECK(supervisor.rteTimingMonitor.getContext() == 0x01010010U);
    CHECK(!supervisor.inLimpHomeState());
    CHECK(::safety::rteTimingMailbox.pending() == 0U);
    ::mpu::openGate();
}

/** Several reporters with the gate closed, a full box and the loss report: still not one write into the page. */
void testBurstFromClosedGate()
{
    auto& supervisor = supervisorInProtectedPage();
    ::mpu::closeGate();
    for (uint32_t i = 0U; i < (::safety::SafetyEventMailbox::CAPACITY + 2U); ++i)
    {
        supervisor.rteTimingMonitor.trigger(0x02000000U | (i << 16U) | 1U);
    }
    CHECK(::mpu::faults == 0);
    ::mpu::openGate();
    supervisor.deliverDeferredEvents();
    CHECK(::mpu::faults == 0);
    CHECK(::test_trace::trace == std::string(::safety::SafetyEventMailbox::CAPACITY + 1U, 'E'));
    CHECK(supervisor.rteTimingMonitor.getContext() == ::safety::SafetyEventMailbox::CONTEXT_LOST);
}

/** Positive control: the monitor as declared before SYS-37 (a plain safeMonitor::Trigger in the supervisor's RAM),
 * triggered from a context without write access, is caught - the board finding on the host. Without this the
 * 0 above could also mean that the protection does not work. */
void testDirectTriggerFromClosedGateIsCaught()
{
    auto& supervisor = supervisorInProtectedPage();
    // the old member: TriggerMonitor rteTimingMonitor, in the same protected RAM as the supervisor
    auto* const before
        = new (::mpu::page + 2048U) SafeSupervisor::TriggerMonitor(supervisor, Event::RTE_TIMING_VIOLATION);
    ::mpu::closeGate();
    before->trigger(0x01010010U);
    CHECK(::mpu::faults >= 1);
    CHECK(::test_trace::trace == "E"); // handled, but only because the host lets the write through
    ::mpu::openGate();

    // the same with a monitor that is still direct: allowed only in the safety context (test_safety_context.py)
    ::mpu::faults = 0;
    ::mpu::closeGate();
    supervisor.checkRegisterMonitor.trigger(1U);
    CHECK(::mpu::faults >= 1);
    ::mpu::openGate();
    ::mpu::faults = 0;
    supervisor.checkRegisterMonitor.trigger(1U); // gate open: fine
    CHECK(::mpu::faults == 0);
}
/** SYS-98 (STK-3): an ETL_ASSERT fails in an RTE task (gate closed). The reaction reads the supervisor and writes
 * only the reset-loop record, which the family port keeps in no-init RAM outside the protected page. */
void testEtlAssertFromClosedGate()
{
    auto& supervisor = supervisorInProtectedPage();
    ::safety::resetloop::Record volatile record{}; // ordinary RAM, like .noinit.resetLoop of the family port
    ::safety::resetloop::clear(record);
    (void)supervisor.startResetLoopLimit(record, ::safety::resetloop::Start{}, 3U, 100U); // main(), gate open
    ::test_trace::trace.clear();
    ::mpu::closeGate();
    supervisor.reportEtlAssert(0x1a328U);
    ::mpu::openGate();
    CHECK(::mpu::faults == 0);
    CHECK(::test_trace::trace == "PR"); // SAFETY RESET line, reset (no power outputs in this build)
    CHECK(record.pending
          == (::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(Event::ETL_ASSERT)));
    CHECK(!supervisor.inLimpHomeState());

    // STK-4: the same for an ECC error from a fault interrupt (SAFE_ISR closes the gate before the handler)
    ::safety::resetloop::clear(record);
    ::test_trace::trace.clear();
    ::mpu::closeGate();
    supervisor.reportEccError(0x1U);
    ::mpu::openGate();
    CHECK(::mpu::faults == 0);
    CHECK(::test_trace::trace == "PR");
    CHECK(record.pending
          == (::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(Event::INTEGRITY_EVENT_DMA_ECC_ERROR)));

    // positive control: the kernel fault reaction sets limp home - a write the closed gate catches (the family port
    // therefore calls it only inside its SafetyShell)
    ::mpu::faults = 0;
    ::mpu::closeGate();
    supervisor.rtosAssertMonitor.trigger(0x2000U);
    ::mpu::openGate();
    CHECK(::mpu::faults >= 1);
}
} // namespace

int main()
{
    if (!::mpu::setUp())
    {
        std::printf("no page protection on this host\n");
        return 2;
    }
    testRteReportFromClosedGate();
    testBurstFromClosedGate();
    testDirectTriggerFromClosedGateIsCaught();
    testEtlAssertFromClosedGate();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
