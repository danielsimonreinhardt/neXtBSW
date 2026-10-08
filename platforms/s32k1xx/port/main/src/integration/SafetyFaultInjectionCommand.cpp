// Console group "inject" (SYS-51): fault injection for the board checks SYS-48 point 2 (deadman configuration
// error) and SYS-45 B6 (WDOG_CS[INT] cleared). Test builds only: compiled with the CMake option
// NX_SAFETY_FAULT_INJECTION_COMMAND=ON (default OFF, platforms/s32k1xx/port/main/CMakeLists.txt), never part of the
// standard build (guard: test/test_safety_fault_injection_guard.py). Both commands end in a reset by the safety
// task - that is what they test. The register access itself is SafetyFaultInjection.h (host tested).
// SYS-97 adds the board checks of the interrupt stack: "mspuse" (no fault, high-water mark of the fill pattern) and
// "mspoverflow" (the MSP onto its guard, ends in the hard fault reaction).

#include "SafetyFaultInjection.h"

#include <console/AsyncCommandWrapper.h>
#include <os/IsrStack.h>
#include <console/ConsoleContext.h>
#include <safeSupervisor/ResetLoopLimit.h>
#include <safeSupervisor/SafeSupervisor.h>
#include <util/command/CommandContext.h>
#include <util/command/GroupCommand.h>

#include <etl/utility.h>

#include <cstdint>
#include <cstdio>

namespace
{
namespace fi = ::integration::faultinjection;
namespace sv = ::safety::supervision;

enum Id
{
    ID_DEADMAN,
    ID_WDOGINT,
    ID_MSPUSE,
    ID_MSPOVERFLOW
};

// SYS-97: Interrupt Control and State Register of the core (ARMv6-M/ARMv7-M system control block), PENDSTSET
constexpr uintptr_t SCB_ICSR_ADDRESS = 0xE000ED04U;
constexpr uint32_t SCB_ICSR_PENDSTSET = 1UL << 26U;
// SYS-97: bytes above the guard the injection leaves on the interrupt stack: less than any exception frame (32 bytes
// without, 104 with the FPU context), so the next exception entry writes into the guard
constexpr uint32_t MSP_OVERFLOW_HEADROOM = 16U;

// Cause the next start prints in its "reset loop" line (SafeSupervisor::handle marks CAUSE_EVENT | event id)
constexpr uint32_t causeOf(::safety::SafeSupervisor::Event const event)
{
    return ::safety::resetloop::CAUSE_EVENT | static_cast<uint32_t>(::etl::to_underlying(event));
}

char const* deadmanFieldName(fi::DeadmanFault const fault)
{
    switch (fault)
    {
        case fi::DeadmanFault::IRQ_ENABLE: return "MIER";
        case fi::DeadmanFault::DEBUG_ENABLE: return "MCR[DBG_EN]";
        case fi::DeadmanFault::NVIC_PRIORITY: return "NVIC level";
        case fi::DeadmanFault::TIMER_VALUE:
        default: return "TVAL";
    }
}

char const* watchdogStatusText(fi::WatchdogStatus const status)
{
    switch (status)
    {
        case fi::WatchdogStatus::CLEARED: return "INT cleared";
        case fi::WatchdogStatus::ALREADY_CLEAR: return "INT was 0 already, nothing written";
        case fi::WatchdogStatus::NOT_UPDATABLE: return "UPDATE 0, not reconfigurable, nothing written";
        case fi::WatchdogStatus::NOT_32BIT: return "CMD32EN 0, nothing written";
        case fi::WatchdogStatus::UNLOCK_TIMEOUT: return "no ULK after the unlock, nothing written";
        case fi::WatchdogStatus::RECONFIG_TIMEOUT:
        default: return "written, but no RCS";
    }
}

/** Test build only (NX_SAFETY_FAULT_INJECTION_COMMAND): falsifies a supervision peripheral, the safety task resets. */
class SafetyFaultInjectionCommand : public ::util::command::GroupCommand
{
protected:
    DECLARE_COMMAND_GROUP_GET_INFO
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;

private:
    static void deadman(::util::command::CommandContext& context);
    static void watchdogInterrupt(::util::command::CommandContext& context);
    static void interruptStackUse(::util::command::CommandContext& context);
    static void interruptStackOverflow(::util::command::CommandContext& context);
};

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(
    SafetyFaultInjectionCommand, "inject", "Safety fault injection, ends in a safety reset (test build only)")
COMMAND_GROUP_COMMAND(
    ID_DEADMAN,
    "deadman",
    "falsify the LPIT deadman channel (param: [tval|mier|dbgen|prio], default tval) - DEADMAN_CONFIGURATION_ERROR")
COMMAND_GROUP_COMMAND(
    ID_WDOGINT, "wdogint", "clear WDOG_CS[INT] while running - SAFE_WATCHDOG_CONFIGURATION_ERROR fields 0x2")
COMMAND_GROUP_COMMAND(
    ID_MSPUSE, "mspuse", "no fault: high-water mark of the interrupt stack since start (fill pattern, SYS-97)")
COMMAND_GROUP_COMMAND(
    ID_MSPOVERFLOW,
    "mspoverflow",
    "move the interrupt stack pointer onto its guard and pend SysTick - hard fault, interrupt stack overflow")
DEFINE_COMMAND_GROUP_GET_INFO_END

void SafetyFaultInjectionCommand::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    // printf, not the logger: the reset by the safety task follows within a few safety cycles, a buffered log line
    // would not be written any more.
    if (idx == ID_DEADMAN)
    {
        deadman(context);
    }
    else if (idx == ID_WDOGINT)
    {
        watchdogInterrupt(context);
    }
    else if (idx == ID_MSPUSE)
    {
        interruptStackUse(context);
    }
    else if (idx == ID_MSPOVERFLOW)
    {
        interruptStackOverflow(context);
    }
    else
    {
        // no other subcommand
    }
}

void SafetyFaultInjectionCommand::interruptStackUse(::util::command::CommandContext& context)
{
    if (!context.checkEol())
    {
        return;
    }
    // SYS-97 board counter-check of the tools/stackuse budget: everything since the fill at the start of main()
    ::isrstack::Use const use = ::isrstack::measure();
    (void)printf(
        "inject mspuse: interrupt stack %lu B, used %lu B since start (fill pattern), %lu B never reached; guard "
        "%lu B at 0x%08lx\r\n",
        static_cast<unsigned long>(use.stackBytes),
        static_cast<unsigned long>(use.usedBytes),
        static_cast<unsigned long>(use.stackBytes - use.usedBytes),
        static_cast<unsigned long>(use.guardBytes),
        static_cast<unsigned long>(use.guardStart));
}

void SafetyFaultInjectionCommand::interruptStackOverflow(::util::command::CommandContext& context)
{
    if (!context.checkEol())
    {
        return;
    }
    ::isrstack::Use const use  = ::isrstack::measure();
    uint32_t const guardEnd    = use.guardStart + use.guardBytes;
    uint32_t const overflowMsp = guardEnd + MSP_OVERFLOW_HEADROOM;
    (void)printf(
        "inject mspoverflow: MSP -> 0x%08lx (%lu B above the guard 0x%08lx..0x%08lx), SysTick pending; expect "
        "HARD FAULT RESET: interrupt stack overflow (guard), outputs off - or a lockup reset without a line\r\n",
        static_cast<unsigned long>(overflowMsp),
        static_cast<unsigned long>(MSP_OVERFLOW_HEADROOM),
        static_cast<unsigned long>(use.guardStart),
        static_cast<unsigned long>(guardEnd));
    // Give the line time to leave (115200 Bd: about 18 ms for 200 characters) - the reaction resets.
    for (uint32_t volatile i = 0U; i < 500000U; ++i) {}
    // The console task is a privileged thread on the process stack: with interrupts masked, move the (idle) main
    // stack pointer 16 bytes above its guard, pend SysTick and unmask. The exception entry of SysTick (or of any
    // interrupt pending first) stacks its frame into the guard - the overflow the MPU guard must catch.
    uint32_t savedMsp = 0U;
    asm volatile("cpsid i\n"
                 "mrs %0, msp\n"
                 "msr msp, %1\n"
                 "isb\n"
                 : "=&r"(savedMsp)
                 : "r"(overflowMsp)
                 : "memory");
    *reinterpret_cast<uint32_t volatile*>(SCB_ICSR_ADDRESS) = SCB_ICSR_PENDSTSET;
    asm volatile("cpsie i\n"
                 "isb\n"
                 :
                 :
                 : "memory");
    // Not reached: the exception entry faults. Should it come back, the guard did not work: the main stack pointer
    // back where it was, and say so.
    asm volatile("cpsid i\n"
                 "msr msp, %0\n"
                 "isb\n"
                 "cpsie i\n"
                 :
                 : "r"(savedMsp)
                 : "memory");
    (void)printf("inject mspoverflow: no fault - the guard below the interrupt stack is not active\r\n");
}

void SafetyFaultInjectionCommand::deadman(::util::command::CommandContext& context)
{
    fi::DeadmanFault fault = fi::DeadmanFault::TIMER_VALUE;
    if (context.hasToken())
    {
        fault = context.scanEnumToken<fi::DeadmanFault>()
                    .check("tval", fi::DeadmanFault::TIMER_VALUE)
                    .check("mier", fi::DeadmanFault::IRQ_ENABLE)
                    .check("dbgen", fi::DeadmanFault::DEBUG_ENABLE)
                    .check("prio", fi::DeadmanFault::NVIC_PRIORITY)
                    .getValue();
    }
    if (!context.checkEol())
    {
        return;
    }
    sv::LpitDeadmanConfiguration const* const expected = sv::deadmanExpectedConfiguration();
    if (expected == nullptr)
    {
        // weak default of safeSupervisionCheck: a project without a power stage has no deadman and no check of it
        (void)printf("inject deadman: this project has no deadman (no power stage), nothing written\r\n");
        return;
    }
    if (expected->channel >= LPIT_TMR_COUNT)
    {
        (void)printf("inject deadman: expected configuration names channel %lu, nothing written\r\n",
                     static_cast<unsigned long>(expected->channel));
        return;
    }
    // The announcement first: the check of the safety task reacts within one cycle (10 ms), about as long as this
    // line takes at 115200 Bd - a line written after the injection could be cut by the reset.
    uint32_t const before = fi::readDeadmanField(*expected, fault);
    (void)printf(
        "inject deadman: %s of LPIT0 channel %lu 0x%08lx; expect DEADMAN_CONFIGURATION_ERROR fields 0x%lx, reset, "
        "last cause 0x%lx\r\n",
        deadmanFieldName(fault),
        static_cast<unsigned long>(expected->channel),
        static_cast<unsigned long>(before),
        static_cast<unsigned long>(fi::deadmanFieldOf(fault)),
        static_cast<unsigned long>(causeOf(::safety::SafeSupervisor::Event::DEADMAN_CONFIGURATION_ERROR)));
    (void)fi::injectDeadmanFault(*expected, fault);
    // Short, and possibly cut by the reset; a write the peripheral ignored is visible here (no reset follows).
    (void)printf("inject deadman: now 0x%08lx\r\n",
                 static_cast<unsigned long>(fi::readDeadmanField(*expected, fault)));
}

void SafetyFaultInjectionCommand::watchdogInterrupt(::util::command::CommandContext& context)
{
    if (!context.checkEol())
    {
        return;
    }
    // Announcement first, as for the deadman: the next watchdog check may come right after the injection.
    (void)printf(
        "inject wdogint: expect SAFE_WATCHDOG_CONFIGURATION_ERROR fields 0x%lx, reset, last cause 0x%lx\r\n",
        static_cast<unsigned long>(sv::WATCHDOG_FIELD_INT),
        static_cast<unsigned long>(causeOf(::safety::SafeSupervisor::Event::SAFE_WATCHDOG_CONFIGURATION_ERROR)));
    fi::WatchdogInjection const result = fi::clearWatchdogInterrupt();
    (void)printf(
        "inject wdogint: WDOG_CS 0x%08lx -> 0x%08lx, %s\r\n",
        static_cast<unsigned long>(result.csBefore),
        static_cast<unsigned long>(result.csAfter),
        watchdogStatusText(result.status));
}

// Registers itself with the console during static initialization (like the logger command of the application).
// Console context (IO-42, ecu/consoleCommands/test/test_console_context.py): the output never holds TASK_BSP and its
// deadman poll.
SafetyFaultInjectionCommand safetyFaultInjectionCommand;
::console::AsyncCommandWrapper
    asyncSafetyFaultInjectionCommand(safetyFaultInjectionCommand, ::console::CONSOLE_CONTEXT);
} // namespace
