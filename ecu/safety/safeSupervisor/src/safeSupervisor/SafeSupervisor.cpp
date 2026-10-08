/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "safeSupervisor/SafeSupervisor.h"

#include "safeSupervisor/EtlAssert.h"
#include "safeSupervisor/SafeConsole.h"

#include <etl/utility.h>
#include <interrupts/SuspendResumeAllInterruptsScopedLock.h>
#include <reset/softwareSystemReset.h>
#include <safeUtils/SafetyLogger.h>

#ifdef PLATFORM_SUPPORT_IO
#include <safeIo/SafeState.h>
#endif
#ifdef PLATFORM_SUPPORT_WATCHDOG
#include <watchdog/Watchdog.h>
#endif

/* SYS-130: set by the build (safeSupervisor/CMakeLists.txt) only for an image whose family port reports a hardware
 * shut-off path (safeSupervisor/HwShutoff.h); every other image keeps this file's object code. */
#ifndef NX_SAFE_SUPERVISOR_HW_SHUTOFF
#define NX_SAFE_SUPERVISOR_HW_SHUTOFF 0
#endif
#if NX_SAFE_SUPERVISOR_HW_SHUTOFF
#include "safeSupervisor/HwShutoff.h"
#endif
#if NX_SAFE_SUPERVISOR_BRIDGE_FAULT
// SYS-140: the family port's removal of the active short (a project with an active short only)
#include "safeSupervisor/SafeStateShort.h"
#endif

/* SYS-138 (safety review SYS-116/SYS-30, K6): set by the build (safeSupervisor/CMakeLists.txt) together with the link
 * to safeIo, whose family port implements the read-back of the safe state (safeSupervisor/SafeStateReadback.h). Only
 * then the hold of S1, the degraded groups and the S1 + S2 sequence read back; without it this file's object code is
 * the one before SYS-138. */
#ifndef NX_SAFE_SUPERVISOR_READBACK
#define NX_SAFE_SUPERVISOR_READBACK 0
#endif
#if NX_SAFE_SUPERVISOR_READBACK
#ifndef PLATFORM_SUPPORT_IO
#error "SYS-138: read-back of the safe state without PLATFORM_SUPPORT_IO (no safe outputs)"
#endif
#include "safeSupervisor/SafeStateReadback.h"
#endif

#ifndef NX_SAFE_SUPERVISOR_HOLDING
/* Condition of the hold loop of holdSafeState(). On the target the hold has no end; the host test
 * (test/host/SafeSupervisorHostTest.cpp) replaces it to bound the rounds. */
#define NX_SAFE_SUPERVISOR_HOLDING() (true)
#endif

#ifndef NX_SAFE_SUPERVISOR_RESET_CONSOLE
#ifdef NX_SAFE_SUPERVISOR_CONSOLE
// SYS-52: a host test that records the line of the hold records the line before a reset the same way
#define NX_SAFE_SUPERVISOR_RESET_CONSOLE(line, length) NX_SAFE_SUPERVISOR_CONSOLE("%s", line)
#else
/* SYS-52/SYS-59: synchronous console line before the reset of every S1 + S2 reaction - the complete line, written
 * by safeConsoleWrite() like the line of the hold. Built without formatConsoleLine(): a second caller changes how the
 * compiler lays out holdSafeState(), whose order test_hold_console_elf.py checks in the image. */
#define NX_SAFE_SUPERVISOR_RESET_CONSOLE(line, length) \
    ((void)::safety::safeConsoleWrite(line, static_cast<uint32_t>(length)))
#endif
#endif

#ifndef NX_SAFE_SUPERVISOR_RESET_FLUSH
#ifdef NX_SAFE_SUPERVISOR_CONSOLE
// SYS-59: a host test that records the console lines has no transmitter to wait for
#define NX_SAFE_SUPERVISOR_RESET_FLUSH() (true)
#else
/* SYS-59: waits (bounded) until the console transmitter is idle - before the line for the characters another writer
 * left in it, after the line for its own last character, which the reset would cut (safeSupervisor/SafeConsole.h) */
#define NX_SAFE_SUPERVISOR_RESET_FLUSH() (::safety::safeConsoleFlush())
#endif
#endif

#if NX_SAFE_SUPERVISOR_DEGRADED
#ifndef PLATFORM_SUPPORT_IO
/* SYS-117: a degraded mapping switches safe-output groups; tools/nxconfig derives it only for a project with PWM
 * groups, which the family builds with PLATFORM_SUPPORT_IO. Without it SD would switch nothing - refused. */
#error "SYS-117: degraded mapping without PLATFORM_SUPPORT_IO (no safe outputs to switch)"
#endif
#ifndef NX_SAFE_DEGRADED_RECORD_ATTRIBUTE
#if defined(__ARM_ARCH)
/* SYS-117: the record of the degraded groups in the no-init RAM of the family's application linker script. */
#define NX_SAFE_DEGRADED_RECORD_ATTRIBUTE __attribute__((section(".noinit.safeDegraded")))
#else
#define NX_SAFE_DEGRADED_RECORD_ATTRIBUTE
#endif
#endif
#endif

#ifndef NX_SAFE_SUPERVISOR_CONSOLE
/* Synchronous console line of holdSafeState(). SYS-39: formatted here and written by safeConsoleWrite() of the
 * family port (safeSupervisor/SafeConsole.h: polling, no interrupt, no task, no buffer, bounded), not through
 * printf and the charIo layer of OpenBSW. Host tests may replace it to record the line. */
#define NX_SAFE_SUPERVISOR_CONSOLE(format, event) ::safety::writeHoldLine(format, event)
#endif

namespace safety
{

using ::util::logger::Logger;
using ::util::logger::SAFETY;

namespace
{
// SYS-39: the console line of the hold, on the stack of the caller (the hold never returns, the line is written
// once). Unused if a host test replaces NX_SAFE_SUPERVISOR_CONSOLE.
[[maybe_unused]] void writeHoldLine(char const* const format, char const* const event)
{
    char line[SAFE_CONSOLE_LINE_SIZE];
    size_t const length = formatConsoleLine(line, sizeof(line), format, event);
    (void)safeConsoleWrite(line, static_cast<uint32_t>(length));
}

// SYS-59: how the context of a resetting event appears in its console line
enum class ResetDetail : uint8_t
{
    NONE,    // the event has no context
    HEX,     // "<label> 0x<context>" (field and source bits)
    DECIMAL, // "<label> <context>"
    PERIOD,  // SYS-52: "kind <bits 31..24>, value <bits 23..0>" of SAFETY_CYCLE_PERIOD_VIOLATION
    TASK,    // SYS-80: "task <text>, <label> <context>" of STACK_OVERFLOW
    ASSERT   // SYS-80: "<label> 0x<context>, line <line>" of RTOS_ASSERT
};

// SYS-59: what the console line before the reset says - the event and its context, in the words of its log line
struct ResetLine
{
    char const* event;
    char const* label;
    ResetDetail detail;
    uint32_t context;
    char const* text = nullptr; // SYS-80: ResetDetail::TASK only, the task name
    uint32_t line    = 0U;      // SYS-80: ResetDetail::ASSERT only, the source line of the failed check
};

// SYS-80: printable copy of a name that comes from the kernel's data next to an overflowed stack - bounded, and
// nothing but printable ASCII reaches the console line
void copyPrintable(char* const text, size_t const size, char const* const source)
{
    size_t length = 0U;
    if (source != nullptr)
    {
        while (((length + 1U) < size) && (source[length] != '\0'))
        {
            char const c = source[length];
            text[length] = ((c >= ' ') && (c <= '~')) ? c : '?';
            ++length;
        }
    }
    if ((length == 0U) && (size >= 2U))
    {
        text[0] = '?';
        length  = 1U;
    }
    text[length] = '\0';
}

size_t appendHex(char* const text, size_t const size, size_t length, uint32_t const value)
{
    length        = resetloop::detail::append(text, size, length, "0x");
    char digits[9];
    size_t n      = 0U;
    uint32_t rest = value;
    do
    {
        uint32_t const digit = rest & 0xFU;
        digits[n]            = static_cast<char>((digit < 10U) ? ('0' + digit) : ('a' + (digit - 10U)));
        ++n;
        rest >>= 4U;
    } while ((rest != 0U) && (n < (sizeof(digits) - 1U)));
    char reversed[9];
    for (size_t i = 0U; i < n; ++i)
    {
        reversed[i] = digits[n - 1U - i];
    }
    reversed[n] = '\0';
    return resetloop::detail::append(text, size, length, reversed);
}

// SYS-52/SYS-59: the synchronous console line before the reset of an S1 + S2 reaction, e.g.
// "SAFETY RESET (DEADMAN_CONFIGURATION_ERROR, fields 0x10): outputs off". The log line of the event never gets out
// (asynchronous logger, reset right after, see the note above handle()); without this line a correct reaction is a
// silent reset on the board. It starts with CR LF: the reset may have interrupted another writer in the middle of a
// line (board 2026-09-22, capture 145: "inject deadman: now " and then the restart).
void writeResetLine(ResetLine const& reason)
{
    char line[SAFE_CONSOLE_LINE_SIZE];
    size_t length = resetloop::detail::append(line, sizeof(line), 0U, "\r\nSAFETY RESET (");
    length        = resetloop::detail::append(line, sizeof(line), length, reason.event);
    if (reason.detail == ResetDetail::PERIOD)
    {
        length = resetloop::detail::append(line, sizeof(line), length, ", kind ");
        length = resetloop::detail::appendNumber(line, sizeof(line), length, (reason.context >> 24U) & 0xFFU);
        length = resetloop::detail::append(line, sizeof(line), length, ", value ");
        length = resetloop::detail::appendNumber(line, sizeof(line), length, reason.context & 0xFFFFFFU);
    }
    else if (reason.detail == ResetDetail::TASK)
    {
        length = resetloop::detail::append(line, sizeof(line), length, ", task ");
        bool const named = (reason.text != nullptr) && (reason.text[0] != '\0');
        length           = resetloop::detail::append(line, sizeof(line), length, named ? reason.text : "?");
        length = resetloop::detail::append(line, sizeof(line), length, ", ");
        length = resetloop::detail::append(line, sizeof(line), length, reason.label);
        length = resetloop::detail::append(line, sizeof(line), length, " ");
        length = resetloop::detail::appendNumber(line, sizeof(line), length, reason.context);
    }
    else if (reason.detail == ResetDetail::ASSERT)
    {
        length = resetloop::detail::append(line, sizeof(line), length, ", ");
        length = resetloop::detail::append(line, sizeof(line), length, reason.label);
        length = resetloop::detail::append(line, sizeof(line), length, " ");
        length = appendHex(line, sizeof(line), length, reason.context);
        length = resetloop::detail::append(line, sizeof(line), length, ", line ");
        length = resetloop::detail::appendNumber(line, sizeof(line), length, reason.line);
    }
    else if (reason.detail != ResetDetail::NONE)
    {
        length = resetloop::detail::append(line, sizeof(line), length, ", ");
        length = resetloop::detail::append(line, sizeof(line), length, reason.label);
        length = resetloop::detail::append(line, sizeof(line), length, " ");
        length = (reason.detail == ResetDetail::HEX)
                     ? appendHex(line, sizeof(line), length, reason.context)
                     : resetloop::detail::appendNumber(line, sizeof(line), length, reason.context);
    }
    else
    {
        // the event name says it all
    }
    length = resetloop::detail::append(line, sizeof(line), length, "): outputs off\r\n");
    NX_SAFE_SUPERVISOR_RESET_CONSOLE(line, length);
}

// The S1 + S2 reaction after the event is decided - one sequence for handle(), the kernel faults and ETL_ASSERT
// (SYS-98). Writes nothing of the supervisor (the callers set the limp-home flag where they may), so it can run
// without write access to the protected RAM. Does not return on the target (softwareSystemReset).
void resetWithLine(ResetLine const& reason, SafeSupervisor::Event const event, resetloop::Record volatile* const record)
{
#ifdef PLATFORM_SUPPORT_IO
    // SYS-06: the power outputs go off before the reset, not only with it. Between this point and the reset the
    // application still runs (log, pending interrupts), and a bridge that keeps switching in that window is the
    // very thing the safe state exists for. enterSafeState() walks the generated safe outputs table of the
    // project; on a board without power outputs the table is empty and this writes nothing.
    ::safety::SafeState::enterSafeState();
#endif
    // SYS-43 (F13): the next start counts this reset as a safety reset (resetloop::onStart), with the event as
    // cause. Written right after the outputs are off - SYS-59: before the console line, so a watchdog reset or a
    // fault while the line is written still counts with this cause. Without a record (before main() registered it,
    // or a port without one) the reset happens unmarked, as before.
    if (record != nullptr)
    {
        resetloop::markSafetyReset(*record, resetloop::CAUSE_EVENT | ::etl::to_underlying(event));
    }
#if NX_SAFE_SUPERVISOR_READBACK
    /* SYS-138 (review K6, the fault handlers' path in ecu/safety): read back what the walk above left behind - the
     * rule of SafeIo (safeSupervisor/SafeStateReadback.h). S1 that did not take effect gets no console line: the line
     * costs up to two flush time-outs plus its own transmission (SYS-59) during which an output would stay energised,
     * and the reset right after is the one reaction left that puts the pins into their reset state (K5). The cause is
     * already in the reset-loop record. A read-back taken too early (a pin level a few bus clocks behind its port)
     * costs the line only - the reset follows in either case. */
    bool const confirmed = (::safety::safeStateReadback() == 0U);
#else
    bool const confirmed = true;
#endif
    // SYS-52/SYS-59: one synchronous console line naming the event, after the outputs and before the reset, so
    // writing it never delays switching off. First the transmitter finishes what another writer (logger, console,
    // interrupted by this task) left in it, then the line, then the line's last character leaves the transmitter -
    // the reset would cut it otherwise. Every step is bounded (safeSupervisor/SafeConsole.h); a transmitter that does
    // not get idle costs one time-out and no line. Bound of the time added before the reset: doc/changelog.d/SYS-59.md.
    if (confirmed && NX_SAFE_SUPERVISOR_RESET_FLUSH())
    {
        writeResetLine(reason);
        (void)NX_SAFE_SUPERVISOR_RESET_FLUSH();
    }
    // reset the MCU
    softwareSystemReset();
}
} // namespace

SafeSupervisor::SafeSupervisor()
: safetyManagerSequenceMonitor(
    *this,
    Event::SAFETY_MANAGER_SEQUENCE_DEVIATION,
    SafetyManagerSequence::SAFETY_MANAGER_ENTER,
    SafetyManagerSequence::SAFETY_MANAGER_LEAVE)
, watchdogStartupCheckMonitor(*this, Event::WATCHDOG_STARTUP_CHECK_FAILURE)
, safeWatchdogSequenceMonitor(
      *this,
      Event::SAFE_WATCHDOG_SEQUENCE_DEVIATION,
      EnterLeaveSequence::ENTER,
      EnterLeaveSequence::LEAVE)
, safeWatchdogConfigMonitor(*this, Event::SAFE_WATCHDOG_CONFIGURATION_ERROR, true)
, serviceWatchdogMonitor(*this, Event::SAFE_WATCHDOG_SERVICE_DEVIATION)
, mpuStatusCheckOnEnterMonitor(*this, Event::MPU_UNLOCKED_ON_SAFETY_MANAGER_ENTRY, true)
, mpuStatusCheckOnLeaveMonitor(*this, Event::MPU_LOCKED_ON_SAFETY_MANAGER_EXIT, false)
, dmaEccMonitor(*this, Event::INTEGRITY_EVENT_DMA_ECC_ERROR)
, romCheckMonitor(*this, Event::ROM_CHECK_FAILURE)
, mpuEnableMonitor(*this, Event::MPU_NOT_ENABLED)
, mpuConfigMonitor(*this, Event::MPU_CONFIGURATION_ERROR)
, adcReferenceMonitor(*this, Event::ADC_REFERENCE_ERROR)
, internalSuppliesMonitor(*this, Event::INTERNAL_SUPPLIES_ERROR)
, checkRegisterMonitor(*this, Event::CHECK_REGISTER_ERROR)
, lockRegisterMonitor(*this, Event::LOCK_REGISTER_ERROR)
, deadmanConfigMonitor(*this, Event::DEADMAN_CONFIGURATION_ERROR)
, rteTimingMonitor(*this, Event::RTE_TIMING_VIOLATION, rteTimingMailbox)
, safetyCyclePeriodMonitor(*this, Event::SAFETY_CYCLE_PERIOD_VIOLATION)
, deviceReportMonitor(*this, Event::DEVICE_REPORT)
, deviceFaultMonitor(*this, Event::DEVICE_FAULT)
, resetLoopMonitor(*this, Event::RESET_LOOP_LIMIT)
, _kernelFaultReaction(*this)
, stackOverflowMonitor(_kernelFaultReaction, Event::STACK_OVERFLOW)
, rtosAssertMonitor(_kernelFaultReaction, Event::RTOS_ASSERT)
, _holdRounds(0U)
, _safetyCycles(0U)
, _deviceBuses()
, _resetLoopRecord(nullptr)
, _resetLoopClearCycles(0U)
, _resetLoopStartCycle(0U)
, _resetLoopCleared(false)
, _limpHome(true)
, _kernelFaultText()
, _kernelFaultLine(0U)
#if NX_SAFE_SUPERVISOR_DEGRADED
, _degradedGroups(0U)
, _degradedGroupsInverted(~0U)
#endif
{}

void SafeSupervisor::init() const
{
    // TODO: Read and clear the no-init RAM
}

// SYS-37: reports of reporters outside the safety task arrive here, in the safety task with the protected RAM
// writable. handle() runs per report as if the reporter had triggered the monitor itself, up to one safety cycle
// later; for SM-05 (report only, S3) that delay changes nothing but the time stamp of the log line.
// SYS-29: the call per safety cycle is also the time base of the device-bus correlation (F5).
// SYS-43: the same call is the time base of the fault-free time of the reset-loop limit.
void SafeSupervisor::deliverDeferredEvents()
{
    _safetyCycles = _safetyCycles + 1U;
    (void)rteTimingMonitor.deliver();
    clearResetLoopAfterFaultFreeTime();
#if NX_SAFE_SUPERVISOR_DEGRADED
    // SYS-117: the degraded groups are held, not only entered - the same rule as the hold of S1 (SYS-36)
    holdDegradedGroups();
#endif
}

// SYS-43 (F13, SSR-065): see SafeSupervisor.h. The start is counted before anything else of the application runs;
// the hold follows at once, so a start at the limit never reaches the BSP, the tasks or the application.
resetloop::StartResult SafeSupervisor::startResetLoopLimit(
    resetloop::Record volatile& record,
    resetloop::Start const& start,
    uint32_t const limit,
    uint32_t const clearCycles)
{
#if NX_SAFE_SUPERVISOR_DEGRADED
    // SYS-117 (decision B4 (a)): the groups degraded before this reset go to their safe state again before the BSP
    // and the application run, and before a reset-loop hold (which covers them anyway)
    startDegraded(start.powerOn);
#endif
    resetloop::StartResult const result = resetloop::onStart(record, start);
    _resetLoopRecord      = &record;
    _resetLoopClearCycles = (clearCycles != 0U) ? clearCycles : 1U;
    _resetLoopStartCycle  = _safetyCycles;
    _resetLoopCleared     = (result.count == 0U);
    if (resetloop::limitReached(result.count, limit))
    {
        resetLoopMonitor.trigger(resetloop::contextOf(result.count, result.lastCause));
    }
    return result;
}

// SYS-43: fault-free operation for the configured time - the safety resets before no longer form a loop. Counted in
// safety cycles (one call per cycle), so a safety task that runs slower than nominal clears later, never earlier.
// Once per run: a later safety reset is marked on top of the cleared count and counts as the first again.
void SafeSupervisor::clearResetLoopAfterFaultFreeTime()
{
    if ((_resetLoopRecord == nullptr) || _resetLoopCleared)
    {
        return;
    }
    if ((_safetyCycles - _resetLoopStartCycle) >= _resetLoopClearCycles)
    {
        resetloop::clear(*_resetLoopRecord);
        _resetLoopCleared = true;
    }
}

// SYS-80 (R1, R2): faults the kernel detects in itself. The family port calls these from its kernel hooks inside a
// SafetyShell, from whatever context the kernel found the fault in; handle() then runs the S1 + S2 reaction there.
void SafeSupervisor::reportStackOverflow(char const* const taskName, uint32_t const taskNumber)
{
    copyPrintable(_kernelFaultText, sizeof(_kernelFaultText), taskName);
    stackOverflowMonitor.trigger(taskNumber);
}

void SafeSupervisor::reportRtosAssert(uint32_t const callSite, uint32_t const line)
{
    _kernelFaultLine = line;
    rtosAssertMonitor.trigger(callSite);
}

// SYS-29 (G5): the box of a device package holds report-only messages. Whatever a word in it says, it arrives as
// DEVICE_REPORT - the event whose reaction is S3 - so a message that another task posted can never switch anything
// off (decision `sys37-postfach-abschaltend`); a switching-off fault is raised as DEVICE_FAULT by a check in the
// safety task. The box's own CONTEXT_LOST/CONTEXT_CORRUPTED words arrive the same way.
uint32_t SafeSupervisor::deliverDeviceReports(DeviceReportBox& box)
{
    return box.drain([this](uint32_t const word) { deviceReportMonitor.trigger(word); });
}

SafeSupervisor::DeviceBus const* SafeSupervisor::findDeviceBus(uint8_t const bus) const
{
    for (DeviceBus const& slot : _deviceBuses)
    {
        if ((slot.bus == bus) && (bus != 0U))
        {
            return &slot;
        }
    }
    return nullptr;
}

bool SafeSupervisor::deviceBusLost(uint8_t const bus) const
{
    DeviceBus const* const slot = findDeviceBus(bus);
    return (slot != nullptr) && slot->lost && ((_safetyCycles - slot->lastCycle) <= DEVICE_BUS_WINDOW_CYCLES);
}

// SYS-29 (F5, fmea.md section 5.3): communication faults of several devices on one bus within one window are one
// event ("bus lost"), because their common cause is the bus (the SPI master, a line, a supply of the bus), not the
// devices. A slot keeps the last faulting device and when it reported; a fault of another device within the window
// marks the bus lost, once. The mark ends when the window passes without a communication fault on the bus.
bool SafeSupervisor::correlateDeviceBus(uint8_t const bus, uint8_t const deviceInstance)
{
    uint32_t const now = _safetyCycles;
    DeviceBus* slot    = nullptr;
    DeviceBus* stale   = nullptr;
    for (DeviceBus& candidate : _deviceBuses)
    {
        if (candidate.bus == bus)
        {
            slot = &candidate;
            break;
        }
        bool const expired = (candidate.bus == 0U) || ((now - candidate.lastCycle) > DEVICE_BUS_WINDOW_CYCLES);
        if ((stale == nullptr) && expired)
        {
            stale = &candidate; // a free slot, or one whose window is over - it correlates nothing any more
        }
    }
    if (slot == nullptr)
    {
        if (stale == nullptr)
        {
            Logger::warn(SAFETY, "SafeSupervisor: no correlation slot for device bus 0x%x", bus);
            return false;
        }
        slot             = stale;
        slot->bus        = bus;
        slot->lastDevice = deviceInstance;
        slot->lastCycle  = now;
        slot->lost       = false;
        return false;
    }
    bool const inWindow = (now - slot->lastCycle) <= DEVICE_BUS_WINDOW_CYCLES;
    bool const becameLost = inWindow && !slot->lost && (slot->lastDevice != deviceInstance);
    if (!inWindow)
    {
        slot->lost = false;
    }
    else if (becameLost)
    {
        slot->lost = true;
    }
    slot->lastDevice = deviceInstance;
    slot->lastCycle  = now;
    return becameLost;
}

// SYS-29 (G5): S3 whatever the fault class - the log, the correlation per bus and, through deviceBusLost(), the
// qualifier of the device values in the RTE. Stage 1 decides nothing more for a report; the fault memory (NVM-03)
// takes device events once the device packages (SYS-28) define their DTCs.
void SafeSupervisor::handleDeviceReport(uint32_t const word)
{
    if ((word == SafetyEventMailbox::CONTEXT_LOST) || (word == SafetyEventMailbox::CONTEXT_CORRUPTED))
    {
        Logger::error(SAFETY, "Event: DEVICE_REPORT, %s",
                      (word == SafetyEventMailbox::CONTEXT_LOST) ? "further reports lost (device box full)"
                                                                  : "device box corrupted, reports discarded");
        return;
    }
    if (!device::isWellFormed(word))
    {
        Logger::error(SAFETY, "Event: DEVICE_REPORT, malformed context 0x%x", word);
        return;
    }
    uint8_t const bus = device::busOf(word);
    if ((device::faultOf(word) == static_cast<uint8_t>(device::FaultClass::COMMUNICATION)) && device::isBus(bus))
    {
        bool const wasLost = deviceBusLost(bus);
        if (correlateDeviceBus(bus, device::deviceOf(word)))
        {
            // communication faults of several devices on this bus: one "bus lost" event (F5)
            Logger::error(SAFETY, "Event: DEVICE_REPORT, bus 0x%x lost, last device %d, detail %d", bus,
                          device::deviceOf(word), device::detailOf(word));
            return;
        }
        if (wasLost && deviceBusLost(bus))
        {
            return; // folded into the "bus lost" event already reported for this window
        }
    }
    Logger::error(SAFETY, "Event: DEVICE_REPORT, bus 0x%x, device %d, class %d, detail %d", bus,
                  device::deviceOf(word), device::faultOf(word), device::detailOf(word));
}

// SYS-29 (G5): the switching-off reaction per fault class. IDENTITY - a wrong or unknown part - survives every
// reset, so the ECU holds the safe state without one (S1 held, like SYS-23); every other class, and a malformed
// word (fail-safe), ends in S1 + S2. The communication fault of a bus already marked lost says so in the log.
void SafeSupervisor::handleDeviceFault(uint32_t const word)
{
    uint8_t const bus  = device::busOf(word);
    bool const busLost = device::isBus(bus) && deviceBusLost(bus);
    Logger::error(SAFETY, "Event: DEVICE_FAULT, bus 0x%x%s, device %d, class %d, detail %d%s", bus,
                  busLost ? " (lost)" : "", device::deviceOf(word), device::faultOf(word),
                  device::detailOf(word), device::isWellFormed(word) ? "" : " malformed");
}

// Note: Currently the asynchronous logs here are broken due to the reset at the end of the
// function. This should be fixed after integration of no-init RAM, so tht the logs will be written
// after a restart.
// TODO: Make sure these logs are processed after a restart once no-init RAM is available
void SafeSupervisor::handle(Event const& event)
{
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    // SYS-59: the console line before the reset (writeResetLine); an event without its own case keeps this one
    ResetLine reason{"unknown event", "id", ResetDetail::DECIMAL, static_cast<uint32_t>(::etl::to_underlying(event))};
    switch (event)
    {
        case Event::SAFETY_MANAGER_SEQUENCE_DEVIATION:
        {
            Logger::error(SAFETY, "Event: SAFETY_MANAGER_SEQUENCE_DEVIATION");
            reason = ResetLine{"SAFETY_MANAGER_SEQUENCE_DEVIATION", nullptr, ResetDetail::NONE, 0U};
            // TODO: remove the log, and replace it with writing the event to no-init ram
            // Note: use event.getContext to get extra information about the event
            break;
        }
        case Event::WATCHDOG_STARTUP_CHECK_FAILURE:
        {
            // SYS-23 (finding F1 of doc/safety/concept.md, SM-07): the watchdog hardware did not reset the MCU
            // in its self-test, so the program flow supervision SM-01 is gone. A reset would run into the same
            // self-test again (a reset loop), so the ECU holds the safe state without a reset instead.
            Logger::error(SAFETY, "Event: WATCHDOG_STARTUP_CHECK_FAILURE, safe state held without reset");
            holdSafeState("WATCHDOG_STARTUP_CHECK_FAILURE");
            return;
        }
        case Event::SAFE_WATCHDOG_SEQUENCE_DEVIATION:
        {
            Logger::error(SAFETY, "Event: SAFE_WATCHDOG_SEQUENCE_DEVIATION");
            reason = ResetLine{"SAFE_WATCHDOG_SEQUENCE_DEVIATION", nullptr, ResetDetail::NONE, 0U};
            // TODO: remove the log, and replace it with writing the event to no-init ram
            // Note: use event.getContext to get extra information about the event
            break;
        }
        case Event::SAFE_WATCHDOG_CONFIGURATION_ERROR:
        {
            // SYS-41 (F12): deviating fields of the watchdog configuration (family port,
            // safeSupervisionCheck/WatchdogControlCheck.h: 1 = enable/clock/time-out, 2 = INT, 4 = WIN, 8 = PRES,
            // 16 = UPDATE)
            uint32_t const fields = safeWatchdogConfigMonitor.getContext();
            Logger::error(SAFETY, "Event: SAFE_WATCHDOG_CONFIGURATION_ERROR, fields: 0x%x", fields);
            reason = ResetLine{"SAFE_WATCHDOG_CONFIGURATION_ERROR", "fields", ResetDetail::HEX, fields};
            // TODO: remove the log, and replace it with writing the event to no-init ram
            break;
        }
        case Event::SAFE_WATCHDOG_SERVICE_DEVIATION:
        {
            Logger::error(SAFETY, "Event: SAFE_WATCHDOG_SERVICE_DEVIATION");
            reason = ResetLine{"SAFE_WATCHDOG_SERVICE_DEVIATION", nullptr, ResetDetail::NONE, 0U};
            // TODO: remove the log, and replace it with writing the event to no-init ram
            // Note: use event.getContext to get extra information about the event
            break;
        }
        case Event::MPU_UNLOCKED_ON_SAFETY_MANAGER_ENTRY:
        {
            Logger::error(SAFETY, "Event: MPU_UNLOCKED_ON_SAFETY_MANAGER_ENTRY");
            reason = ResetLine{"MPU_UNLOCKED_ON_SAFETY_MANAGER_ENTRY", nullptr, ResetDetail::NONE, 0U};
            // TODO: remove the log, and replace it with writing the event to no-init ram
            // Note: use event.getContext to get extra information about the event
            break;
        }
        case Event::MPU_LOCKED_ON_SAFETY_MANAGER_EXIT:
        {
            Logger::error(SAFETY, "Event: MPU_LOCKED_ON_SAFETY_MANAGER_EXIT");
            reason = ResetLine{"MPU_LOCKED_ON_SAFETY_MANAGER_EXIT", nullptr, ResetDetail::NONE, 0U};
            // TODO: remove the log, and replace it with writing the event to no-init ram
            // Note: use event.getContext to get extra information about the event
            break;
        }
        case Event::INTEGRITY_EVENT_DMA_ECC_ERROR:
        {
            // SYS-41 (F11): the sources, bit 0 RAM channel 0, bit 1 RAM channel 1, bit 2 flash. The status flags
            // were cleared before the report, so the reset neither loops on an old flag nor masks a new one.
            uint32_t const sources = dmaEccMonitor.getContext();
            Logger::error(SAFETY, "Event: INTEGRITY_EVENT_DMA_ECC_ERROR (ECC, sources 0x%x: "
                                  "1 = RAM ch 0, 2 = RAM ch 1, 4 = flash), System Reset", sources);
            reason = ResetLine{"INTEGRITY_EVENT_DMA_ECC_ERROR", "sources", ResetDetail::HEX, sources};
            // TODO: remove the log, and replace it with writing the event to no-init ram
            break;
        }
        case Event::ROM_CHECK_FAILURE:
        {
            // SYS-23 (finding F1, SM-14): the program image is not the one that was linked. The fault is in the
            // flash and survives a reset, and the ROM check runs only after the application has started - a
            // reset would restart the wrong image in a loop, with the application running each time until the
            // check fails again. The ECU holds the safe state without a reset instead.
            Logger::error(SAFETY, "Event: ROM_CHECK_FAILURE, safe state held without reset");
            holdSafeState("ROM_CHECK_FAILURE");
            return;
        }
        case Event::MPU_NOT_ENABLED:
        {
            Logger::error(SAFETY, "Event: MPU_NOT_ENABLED");
            reason = ResetLine{"MPU_NOT_ENABLED", nullptr, ResetDetail::NONE, 0U};
            // TODO: remove the log, and replace it with writing the event to no-init ram
            // Note: use event.getContext to get extra information about the event
            break;
        }
        case Event::MPU_CONFIGURATION_ERROR:
        {
            uint32_t const region = mpuConfigMonitor.getContext();
            Logger::error(SAFETY, "Event: MPU_CONFIGURATION_ERROR, failed region: %d", region);
            reason = ResetLine{"MPU_CONFIGURATION_ERROR", "region", ResetDetail::DECIMAL, region};
            // TODO: remove the log, and replace it with writing the event to no-init ram
            // Note: use event.getContext to get extra information about the event
            break;
        }
        case Event::ADC_REFERENCE_ERROR:
        {
            // Context of the monitor (IO-16, safety::SafeSystem::AdcErrorContext): 1 = a reading succeeded and
            // the value is out of range, 2 = no reading succeeded for adc_read_error_limit cycles, so the
            // reference can no longer be monitored. Without it both causes look the same in the log.
            // SYS-95 (DFA-3): 3 = the bandgap and the supply channel do not stand in their ratio (converter stuck).
            uint32_t const context = adcReferenceMonitor.getContext();
            Logger::error(SAFETY, "Event: ADC_REFERENCE_ERROR, context: %d "
                                  "(1 = value out of range, 2 = no measurement, 3 = converter stuck)", context);
            reason = ResetLine{"ADC_REFERENCE_ERROR", "context", ResetDetail::DECIMAL, context};
            // TODO: remove the log, and replace it with writing the event to no-init ram
            break;
        }
        case Event::INTERNAL_SUPPLIES_ERROR:
        {
            uint32_t const context = internalSuppliesMonitor.getContext();
            Logger::error(SAFETY, "Event: INTERNAL_SUPPLIES_ERROR, context: %d "
                                  "(1 = value out of range, 2 = no measurement)", context);
            reason = ResetLine{"INTERNAL_SUPPLIES_ERROR", "context", ResetDetail::DECIMAL, context};
            // TODO: remove the log, and replace it with writing the event to no-init ram
            break;
        }
        case Event::RTE_TIMING_VIOLATION:
        {
            // SYS-05: kind in bits 31..24 (1 = task cycle, 2 = runnable), row of rte::TASKS resp.
            // rte::RUNNABLES in bits 23..16, overshoot in ms in bits 15..0 (saturating).
            uint32_t const context = rteTimingMonitor.getContext();
            if (context == SafetyEventMailbox::CONTEXT_LOST)
            {
                // SYS-37: more violations than the box holds in one safety cycle; the ones before were logged
                Logger::error(SAFETY, "Event: RTE_TIMING_VIOLATION, further reports lost (event box full)");
            }
            else if (context == SafetyEventMailbox::CONTEXT_CORRUPTED)
            {
                // SYS-37: the box in ordinary RAM failed its check - something wrote into it
                Logger::error(SAFETY, "Event: RTE_TIMING_VIOLATION, event box corrupted, reports discarded");
            }
            else
            {
                Logger::error(SAFETY, "Event: RTE_TIMING_VIOLATION, kind: %d, index: %d, over: %d ms",
                              (context >> 24U) & 0xFFU, (context >> 16U) & 0xFFU, context & 0xFFFFU);
            }
            // No reset: a single missed deadline must not restart the ECU. The reaction belongs to the
            // error memory once it exists (NVM-03); until then the log and the counters of "rte" are it.
            return;
        }
        case Event::CHECK_REGISTER_ERROR:
        {
#ifdef PLATFORM_SUPPORT_IO
            uint32_t const checkContext = checkRegisterMonitor.getContext();
            bool const ok               = (checkContext == CHECK_REGISTER_CONTEXT_OK);
#if NX_SAFE_SUPERVISOR_HW_SHUTOFF
            // SYS-130: a reported hardware shut-off path holds S1 until reset (safeSupervisor/HwShutoff.h) - the
            // return of the register check does not release it
            if (ok && ::safety::hwShutoff::latched())
            {
                Logger::error(SAFETY, "Event: CHECK_REGISTER_ERROR (safe state stays: HW_SHUTOFF)");
                return;
            }
#endif
            if (ok)
            {
                Logger::error(SAFETY, "Event: CHECK_REGISTER_ERROR (leave safe state)");
                ::safety::SafeState::leaveSafeState();
            }
            else
            {
                // SYS-169: the context says what deviated - an entry of the register list (class in bits 31..24, index
                // in bits 15..0, safeIo/RegisterCheck.h) or, as before SYS-24, the SAFETY_TEST pin of the demo (0)
                if (checkContext == 0U)
                {
                    Logger::error(SAFETY, "Event: CHECK_REGISTER_ERROR (enter safe state, SAFETY_TEST pin)");
                }
                else
                {
                    Logger::error(
                        SAFETY,
                        "Event: CHECK_REGISTER_ERROR (enter safe state, register class %d, entry %d)",
                        (checkContext >> 24U) & 0xFFU,
                        checkContext & 0xFFFFU);
                }
                ::safety::SafeState::enterSafeState();
            }
#else
            Logger::error(SAFETY, "Event: CHECK_REGISTER_ERROR");
#endif
            // This is not a fatal error, so we do not enter limp home or reset the MCU.
            return;
        }
        case Event::LOCK_REGISTER_ERROR:
        {
            auto const reg_id = lockRegisterMonitor.getContext();
            Logger::error(SAFETY, "Event: LOCK_REGISTER_ERROR, failed register id: %d", reg_id);
            reason = ResetLine{"LOCK_REGISTER_ERROR", "register", ResetDetail::DECIMAL, reg_id};
            // TODO: remove the log, and replace it with writing the event to no-init ram
            break;
        }
        case Event::DEADMAN_CONFIGURATION_ERROR:
        {
            // SYS-41 (F12, SM-06): a corrupted deadman would disable or stretch the halt/stall detection
            // silently. The reset sets it up again (S1 + S2 like the watchdog configuration check).
            uint32_t const fields = deadmanConfigMonitor.getContext();
            Logger::error(SAFETY, "Event: DEADMAN_CONFIGURATION_ERROR, fields: 0x%x", fields);
            reason = ResetLine{"DEADMAN_CONFIGURATION_ERROR", "fields", ResetDetail::HEX, fields};
            break;
        }
        case Event::SAFETY_CYCLE_PERIOD_VIOLATION:
        {
            // SYS-40 (F9, SM-04): the checks of the safety task no longer run at the period their detection times
            // are based on (or the time base that proves it is broken). S1 + S2 like a sequence deviation: the
            // reset restarts the scheduler and the timer. Kind in bits 31..24 (1 = interval too long, 2 = window
            // too short, 3 = timer configuration), ticks of the timer resp. deviating fields in bits 23..0.
            uint32_t const context = safetyCyclePeriodMonitor.getContext();
            // kind 1 = interval too long, 2 = window too short (value in ticks), 3 = timer fields (value = field bits)
            // SYS-95 (DFA-6 a): 4 = system time too slow or stopped, 5 = too fast against the timer (value in ms)
            Logger::error(SAFETY, "Event: SAFETY_CYCLE_PERIOD_VIOLATION, kind: %d, value: %d",
                          (context >> 24U) & 0xFFU, context & 0xFFFFFFU);
            // SYS-52: the log line above is lost with the reset; the same words go out synchronously once the
            // outputs are off (board check B7 of doc/safety/fmea.md needs to see which kind reset the ECU)
            reason = ResetLine{"SAFETY_CYCLE_PERIOD_VIOLATION", nullptr, ResetDetail::PERIOD, context};
            break;
        }
        case Event::DEVICE_REPORT:
        {
            // SYS-29 (G5): report only (S3) by decision - no reset, no safe state, whatever the word says
            handleDeviceReport(deviceReportMonitor.getContext());
            return;
        }
        case Event::DEVICE_FAULT:
        {
            // SYS-29 (G5): raised in the safety task only; the fault class chooses between S1 held and S1 + S2
            uint32_t const word = deviceFaultMonitor.getContext();
#if NX_SAFE_SUPERVISOR_BRIDGE_FAULT
            // SYS-140 (decision C4): a bridge fault removes the active short first - every walk of the reaction below
            // (SD, S1 held, S1 + S2) and every later one writes freewheel for the groups concerned
            blockShortOnBridgeFault(word);
#endif
            handleDeviceFault(word);
#if NX_SAFE_SUPERVISOR_DEGRADED
            // SYS-117: SD instead of the fail-safe reaction, if the project maps this device (and only then)
            if (degradeDeviceFault(word))
            {
                return;
            }
#endif
            if (device::isWellFormed(word)
                && (device::faultOf(word) == static_cast<uint8_t>(device::FaultClass::IDENTITY)))
            {
                holdSafeState("DEVICE_FAULT");
                return;
            }
            reason = ResetLine{"DEVICE_FAULT", "word", ResetDetail::HEX, word};
            break;
        }
        case Event::RESET_LOOP_LIMIT:
        {
            // SYS-43 (F13, SSR-065): the configured number of safety resets in a row - another reset would only
            // start the next round of the loop (and the application with it). S1 held without a reset; the
            // console line names the count and the cause of the last safety reset.
            uint32_t const context = resetLoopMonitor.getContext();
            Logger::error(SAFETY, "Event: RESET_LOOP_LIMIT, safety resets: %d, last cause: 0x%x, safe state held",
                          context & 0xFFFFU, context >> 16U);
            char text[SAFE_CONSOLE_LINE_SIZE];
            (void)resetloop::formatHoldText(text, sizeof(text), context & 0xFFFFU, context >> 16U);
            holdSafeState(text);
            return;
        }
        case Event::STACK_OVERFLOW:
        case Event::RTOS_ASSERT:
        {
            // SYS-98: their monitors call resetOnKernelFault() through KernelFaultReaction; handle() only forwards
            resetOnKernelFault(event);
            return;
        }
        case Event::ETL_ASSERT:
        {
            // SYS-98: raised through reportEtlAssert(), which leaves the limp-home flag alone (it may run without
            // write access). handle() has that access: set the flag like every event, then the same reaction (the
            // event carries no address here).
            enterLimpHome();
            reportEtlAssert(0U);
            return;
        }
        default:
        {
            Logger::warn(
                SAFETY,
                "SafeSupervisor: Received unknown event with ID %d",
                ::etl::to_underlying(event));
            // TODO: write event id (int) to no-init ram as unknown error
            break;
        }
    }
    enterLimpHome();
    resetWithLine(reason, event, _resetLoopRecord);
}

// SYS-80 (findings R1/R2 of doc/safety/rtos-comparison.md), SYS-98 (STK-4): the S1 + S2 reaction of the kernel
// faults, from whatever context the kernel found the fault in (inside the SafetyShell of the family port). No log
// line, unlike the events of handle(): it would be lost with the reset like every one (SYS-59), writing it runs the
// logger and the formatter over RAM that may be what an overflowed stack overwrote, and the kernel may be called
// from the logger. Not part of handle(), so that nothing of its switch (log lines of the other events, the device
// correlation) is reachable from an interrupt through configASSERT.
void SafeSupervisor::KernelFaultReaction::handle(Event const& event) { _supervisor.resetOnKernelFault(event); }

void SafeSupervisor::resetOnKernelFault(Event const& event)
{
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    // not a kernel fault (only if called wrongly): the line names the id, the reaction stays S1 + S2 (fail-safe)
    ResetLine reason{"unknown event", "id", ResetDetail::DECIMAL, static_cast<uint32_t>(::etl::to_underlying(event))};
    switch (event)
    {
        case Event::STACK_OVERFLOW:
        {
            // R1: the memory below the task's stack may be overwritten - another task's control block, a driver's
            // data. The console line names the task (printable copy, reportStackOverflow()) and its context number.
            reason = ResetLine{"STACK_OVERFLOW", "context", ResetDetail::TASK, stackOverflowMonitor.getContext(),
                               _kernelFaultText};
            break;
        }
        case Event::RTOS_ASSERT:
        {
            // R2: the kernel's state is not what its own code expects. The line gives the code address of the call
            // of the check (addr2line on the ELF names the function, hence the kernel file - the compiler may share
            // one call among the checks of a function) and its source line.
            reason = ResetLine{"RTOS_ASSERT", "at", ResetDetail::ASSERT, rtosAssertMonitor.getContext(), nullptr,
                               _kernelFaultLine};
            break;
        }
        default:
        {
            break;
        }
    }
    enterLimpHome();
    resetWithLine(reason, event, _resetLoopRecord);
}

// SYS-98 (STK-3, SYS-83): see SafeSupervisor.h. Reads only _resetLoopRecord; the record lies in no-init RAM of the
// family port, outside the protected RAM.
void SafeSupervisor::reportEtlAssert(uint32_t const callSite) const
{
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    resetWithLine(ResetLine{"ETL_ASSERT", "at", ResetDetail::HEX, callSite}, Event::ETL_ASSERT, _resetLoopRecord);
}

// SYS-98 (STK-4): see SafeSupervisor.h - the line of INTEGRITY_EVENT_DMA_ECC_ERROR in handle(), without its log line
void SafeSupervisor::reportEccError(uint32_t const sources) const
{
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    resetWithLine(
        ResetLine{"INTEGRITY_EVENT_DMA_ECC_ERROR", "sources", ResetDetail::HEX, sources},
        Event::INTEGRITY_EVENT_DMA_ECC_ERROR,
        _resetLoopRecord);
}

// SYS-23: reaction to a fault that a reset cannot remove (finding F1 of doc/safety/concept.md): safe state S1
// latched, no reset (S2). Called with all interrupts suspended (handle()), so from here on nothing but this loop
// runs - no task, no interrupt, in particular no application that could switch a power output on again.
// - The safe outputs table is walked in every round, not once: S1 stays asserted even if a register was changed
//   between two rounds.
// - The MCU watchdog is serviced in every round, so it does not end the hold with a reset that would start the
//   same image (and the application) again. The loop is the only code that runs, so it is the only thing the
//   watchdog could supervise. An external watchdog (SBC) is not serviced: with one, its reset and its reset
//   counter decide (SM-52), and on a counter overflow the SBC removes the supply.
// - The hold ends only with a power cycle (or a reset from outside). The asynchronous log line of the caller is
//   not written any more (see the note above handle()), so the hold writes one synchronous console line
//   itself, once, before the loop - otherwise a board in the hold is silent. SYS-39: the line goes through
//   safeConsoleWrite() of the family port (safeSupervisor/SafeConsole.h), which polls the console's transmitter
//   and needs neither an interrupt nor a task nor the charIo/printf layer - the same for both callers: the
//   watchdog self-test failure from main() before the BSP starts, the ROM check from the idle context with
//   everything running and the logger's buffer full of lines that will never be written. The line is written
//   after the first safe outputs walk, so writing it (about 7 ms at 115200 baud, at most one transmitter
//   time-out if the console is dead) never delays switching the outputs off. The error memory belongs to NVM-03.
void SafeSupervisor::holdSafeState(char const* const event)
{
    enterLimpHome();
#ifdef PLATFORM_SUPPORT_IO
    ::safety::SafeState::enterSafeState();
#endif
    NX_SAFE_SUPERVISOR_CONSOLE(
        "SAFE STATE HELD (%s): outputs off, no reset - power cycle required\r\n", event);
    while (NX_SAFE_SUPERVISOR_HOLDING())
    {
#if NX_SAFE_SUPERVISOR_READBACK
        /* SYS-138 (review K6): the hold confirms S1 by read-back, in every round, before its walk - so what is read
         * is what the previous walk left (the first round: the walk before the console line), never a pin level a
         * few bus clocks after its store. Here and not in the halt deadman or the idle context: the hold runs with
         * every interrupt suspended (handle()), so neither the deadman interrupt nor the idle task (nor any task) runs
         * again - the loop is the only code that runs, in every case of the hold (from main() before the BSP, from
         * the idle context, from the safety task). S1 that did not take effect ends the hold with the reaction of
         * SafeIo (K5): LOCK_REGISTER_ERROR, class SAFE_OUTPUT and the entry, S1 + S2 - the reset puts the pins into
         * their reset state; after it the reset-loop limit (SYS-43) decides as for every safety reset. Before
         * SafeIo::init() nothing is judged (safeSupervisor/SafeStateReadback.h). */
        uint32_t const unconfirmed = ::safety::safeStateReadback();
        if (unconfirmed != 0U)
        {
            resetWithLine(
                ResetLine{"LOCK_REGISTER_ERROR", "register", ResetDetail::DECIMAL, unconfirmed},
                Event::LOCK_REGISTER_ERROR,
                _resetLoopRecord);
            return; // host test only: softwareSystemReset() does not return on the target
        }
#endif
#ifdef PLATFORM_SUPPORT_IO
        ::safety::SafeState::enterSafeState();
#endif
#ifdef PLATFORM_SUPPORT_WATCHDOG
        ::safety::bsp::Watchdog::serviceWatchdog();
#endif
        // An observable side effect in every round, also on a board without outputs and watchdog: a loop
        // without one may be removed by the compiler (forward progress rule of C++).
        _holdRounds = _holdRounds + 1U;
    }
}

#if NX_SAFE_SUPERVISOR_DEGRADED
// SYS-117 (decision B4 (a)): see SafeSupervisor.h. No-init RAM: never cleared by the start-up code; onStart() writes it
// before the first read after a power-on.
NX_SAFE_DEGRADED_RECORD_ATTRIBUTE degraded::Record volatile degradedRecord;

namespace
{
constexpr uint32_t DEVICE_FAULT_ID = static_cast<uint32_t>(::etl::to_underlying(SafeSupervisor::Event::DEVICE_FAULT));

// SYS-135: the ids of the reports of Degraded.h lie above every event and below the implausible marker, and the list
// names the id of the constant the code uses
static_assert(
    static_cast<uint32_t>(::etl::to_underlying(SafeSupervisor::Event::ETL_ASSERT)) < degraded::REPORT_HW_SHUTOFF,
    "SYS-135: report ids above every event number");
#define NX_SAFE_DEGRADED_REPORT_ID(name, id)                                                  \
    static_assert(                                                                            \
        (degraded::REPORT_##name == (id##U)) && ((id##U) < 0xFFU),                            \
        "SYS-135: report id of the closed list");
NX_SAFE_DEGRADABLE_REPORTS(NX_SAFE_DEGRADED_REPORT_ID)
#undef NX_SAFE_DEGRADED_REPORT_ID

/* SYS-135: the event field of a mapping the supervisor degrades - an event of the closed list, or a report of the
 * closed list whose reporter is in this image (HW_SHUTOFF: NX_SAFE_SUPERVISOR_HW_SHUTOFF, set by the build for a family
 * port with a hardware shut-off path). A HW_SHUTOFF mapping in an image without the path does not build. */
constexpr bool isDegradableId(uint32_t const id)
{
    return ((id <= 0xFFU) && SafeSupervisor::isDegradableEvent(static_cast<SafeSupervisor::Event>(id)))
           || ((NX_SAFE_SUPERVISOR_HW_SHUTOFF != 0) && (id == degraded::REPORT_HW_SHUTOFF));
}
} // namespace

// SYS-117: the generated mappings are checked here once more, at compile time - a mapping of an event that is not on
// the closed list (a hand-edited or stale configuration header) does not build, and neither does one without a group
// or with a group the project does not have (concept section 4.2/4.3; tools/nxconfig/degraded.py refuses them first).
#define NX_SAFE_DEGRADED_CHECK_MAPPING(mapping)                                                                 \
    static_assert(                                                                                              \
        isDegradableId(degraded::mappingEvent(mapping)),                                                        \
        "SYS-117: a mapping of an event that is not degradable (fail-safe only)");                              \
    static_assert(                                                                                              \
        (degraded::mappingGroups(mapping) != 0U)                                                                \
            && ((degraded::mappingGroups(mapping) & ~SafeSupervisor::DEGRADED_ALL_GROUPS) == 0U),               \
        "SYS-117: a mapping without a group or with a group the project does not have")
#define NX_SAFE_DEGRADED_CHECK_BUS_GROUPS(busGroups)                \
    static_assert(                                                  \
        ((busGroups) & ~SafeSupervisor::DEGRADED_ALL_GROUPS) == 0U, \
        "SYS-117: bus groups of a mapping with a group the project does not have")

static_assert(
    NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS <= degraded::MAX_MAPPINGS, "SYS-117: at most MAX_MAPPINGS");

bool SafeSupervisor::degradedGroups(uint32_t& groups) const
{
    uint32_t const value    = _degradedGroups;
    uint32_t const inverted = _degradedGroupsInverted;
    if (((value ^ inverted) != 0xFFFFFFFFU) || ((value & ~DEGRADED_ALL_GROUPS) != 0U))
    {
        groups = DEGRADED_ALL_GROUPS;
        return false;
    }
    groups = value;
    return true;
}

// IO-45: see SafeSupervisor.h - the inverse of entriesOf() for one entry.
uint32_t SafeSupervisor::groupsOfEntry(uint32_t const entry)
{
    uint32_t groups = 0U;
    if (entry >= 32U)
    {
        return groups;
    }
    for (uint32_t group = 0U; group < DEGRADED_GROUP_COUNT; ++group)
    {
        if ((entriesOf(1U << group) & (1U << entry)) != 0U)
        {
            groups |= 1U << group;
        }
    }
    return groups;
}

bool SafeSupervisor::isEntryDegraded(uint32_t const entry) const
{
    uint32_t groups = 0U;
    (void)degradedGroups(groups); // implausible: every group, so every entry of a group is locked
    return (groups & groupsOfEntry(entry)) != 0U;
}

// SYS-123: see SafeSupervisor.h. The latch as it stands (implausible: every group), the walk of the hold.
void SafeSupervisor::applyDegradedGroups() const
{
    uint32_t groups = 0U;
    (void)degradedGroups(groups);
    if (groups == 0U)
    {
        return;
    }
    if (groups == DEGRADED_ALL_GROUPS)
    {
        ::safety::SafeState::enterSafeState();
    }
    else
    {
        ::safety::SafeState::enterDegradedState(entriesOf(groups));
    }
}

void SafeSupervisor::setDegradedGroups(uint32_t const groups)
{
    _degradedGroups         = groups;
    _degradedGroupsInverted = ~groups;
}

// SYS-117: the table entries of the groups (tools/nxconfig: bit i = entry i of the safe outputs table of bswgen).
uint32_t SafeSupervisor::entriesOf(uint32_t const groups)
{
    uint32_t entries = 0U;
    auto const add   = [&entries, groups](uint32_t const group, uint32_t const groupEntries)
    {
        if ((groups & (1U << group)) != 0U)
        {
            entries |= groupEntries;
        }
    };
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS > 0
    add(0U, NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_0_ENTRIES);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS > 1
    add(1U, NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_1_ENTRIES);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS > 2
    add(2U, NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_2_ENTRIES);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS > 3
    add(3U, NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_3_ENTRIES);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS > 4
    add(4U, NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_4_ENTRIES);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS > 5
    add(5U, NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_5_ENTRIES);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS > 6
    add(6U, NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_6_ENTRIES);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS > 7
    add(7U, NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_7_ENTRIES);
#endif
    return entries;
}

// SYS-117: the first mapping of (event, instance) decides. A mapping of a non-degradable event never matches (belt and
// braces behind the static_assert); a word whose bus differs from the configured bus of the device is inconsistent
// and keeps the fail-safe reaction; on a bus the supervisor regards as lost only the bus groups count (0 when a device
// of that bus is not mapped - a lost bus is a common cause of all its devices, concept section 4.2).
uint32_t SafeSupervisor::degradedGroupsOf(
    Event const event, uint32_t const instance, uint32_t const bus, bool const busLost)
{
    if (!isDegradableEvent(event))
    {
        return 0U;
    }
    return degradedGroupsOfId(static_cast<uint32_t>(::etl::to_underlying(event)), instance, bus, busLost);
}

// SYS-135: the probe of the generated mappings for the event field `id` (an event or a report of Degraded.h).
uint32_t SafeSupervisor::degradedGroupsOfId(
    uint32_t const id, uint32_t const instance, uint32_t const bus, bool const busLost)
{
    if (!isDegradableId(id))
    {
        return 0U;
    }
    bool found        = false;
    uint32_t groups   = 0U;
    auto const probe  = [&found, &groups, id, instance, bus, busLost](uint32_t const mapping, uint32_t const busGroups)
    {
        if (found || (degraded::mappingEvent(mapping) != id) || (degraded::mappingInstance(mapping) != instance))
        {
            return;
        }
        found = true;
        if (degraded::mappingBus(mapping) != bus)
        {
            return;
        }
        groups = busLost ? busGroups : degraded::mappingGroups(mapping);
    };
#define NX_SAFE_DEGRADED_PROBE(n)                                                                      \
    NX_SAFE_DEGRADED_CHECK_MAPPING(NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_##n);              \
    NX_SAFE_DEGRADED_CHECK_BUS_GROUPS(NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_##n##_BUS_GROUPS); \
    probe(                                                                                             \
        NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_##n,                                         \
        NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_##n##_BUS_GROUPS)
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 0
    NX_SAFE_DEGRADED_PROBE(0);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 1
    NX_SAFE_DEGRADED_PROBE(1);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 2
    NX_SAFE_DEGRADED_PROBE(2);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 3
    NX_SAFE_DEGRADED_PROBE(3);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 4
    NX_SAFE_DEGRADED_PROBE(4);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 5
    NX_SAFE_DEGRADED_PROBE(5);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 6
    NX_SAFE_DEGRADED_PROBE(6);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 7
    NX_SAFE_DEGRADED_PROBE(7);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 8
    NX_SAFE_DEGRADED_PROBE(8);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 9
    NX_SAFE_DEGRADED_PROBE(9);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 10
    NX_SAFE_DEGRADED_PROBE(10);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 11
    NX_SAFE_DEGRADED_PROBE(11);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 12
    NX_SAFE_DEGRADED_PROBE(12);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 13
    NX_SAFE_DEGRADED_PROBE(13);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 14
    NX_SAFE_DEGRADED_PROBE(14);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS > 15
    NX_SAFE_DEGRADED_PROBE(15);
#endif
#undef NX_SAFE_DEGRADED_PROBE
    return groups & DEGRADED_ALL_GROUPS;
}

// SYS-117 (concept section 4.2): a device fault is degradable only as a well-formed word of a class other than
// IDENTITY (a wrong part or a corrupt report is not local to a function), and only if the project maps the device.
bool SafeSupervisor::degradeDeviceFault(uint32_t const word)
{
    if (!device::isWellFormed(word)
        || (device::faultOf(word) == static_cast<uint8_t>(device::FaultClass::IDENTITY)))
    {
        return false;
    }
    uint8_t const bus     = device::busOf(word);
    bool const busLost    = device::isBus(bus) && deviceBusLost(bus);
    uint32_t const groups = degradedGroupsOf(Event::DEVICE_FAULT, device::deviceOf(word), bus, busLost);
    if (groups == 0U)
    {
        return false;
    }
    enterDegraded(groups, DEVICE_FAULT_ID, word);
    return true;
}

// SYS-135 (safety review SYS-128/SYS-130 C1, decision of the safety responsible 2026-09-30): see SafeSupervisor.h. Only
// the latched fault itself is local to the group - the path tripped and holds the group's outputs in hardware. The
// findings of the path (switched off, reconfigured, stuck, not latched; SYS-128) may come from a wild write or a fault
// of the MCU and keep S1 held, as does a malformed context (hwShutoff::ALL).
bool SafeSupervisor::degradeHwShutoff(uint32_t const context)
{
    constexpr uint32_t TRIPPED = 0x00000001U; // context bit 0: the timer has latched a fault (FMS[FAULTF])
    constexpr uint32_t TIMER   = 0xFF000000U; // context bits 31..24: index of the timer
    if ((context & ~TIMER) != TRIPPED)
    {
        return false;
    }
    uint32_t const groups = degradedGroupsOfId(degraded::REPORT_HW_SHUTOFF, context >> 24U, device::NO_BUS, false);
    if (groups == 0U)
    {
        return false;
    }
    enterDegraded(groups, degraded::REPORT_HW_SHUTOFF, context);
    return true;
}

// SYS-117 (concept section 4.1/4.4): the order of every switching reaction - outputs first (SSR-002), then the latch,
// then the record, then the report. Groups only accumulate (no return within the power cycle, decision B6 (a)); when
// the last group is degraded there is nothing left to continue, and SD becomes S1 without a reset (decision B5 (a)).
void SafeSupervisor::enterDegraded(uint32_t const groups, uint32_t const event, uint32_t const context)
{
    uint32_t before      = 0U;
    bool const plausible = degradedGroups(before); // implausible: every group (fail-safe)
    uint32_t const now   = (before | groups) & DEGRADED_ALL_GROUPS;
    bool const last      = (now == DEGRADED_ALL_GROUPS);
    if (last)
    {
        ::safety::SafeState::enterSafeState();
    }
    else
    {
        ::safety::SafeState::enterDegradedState(entriesOf(now));
    }
    setDegradedGroups(now);
    degraded::write(degradedRecord, now, event, context);
    Logger::error(
        SAFETY,
        "Event: %d degraded (SD), ctx 0x%x: groups 0x%x, now 0x%x of 0x%x%s",
        event,
        context,
        groups,
        now,
        DEGRADED_ALL_GROUPS,
        plausible ? "" : " (latch implausible)");
    if (last)
    {
        Logger::error(SAFETY, "SafeSupervisor: every safe-output group degraded - safe state S1, no reset");
    }
}

// SYS-117: every safety cycle (deliverDeferredEvents()). The degraded groups are walked again - a driver, the debug
// halt guard or a wild write that released a timer of a degraded group is overruled within one safety cycle, as the
// hold of S1 does for its latch (SYS-36). The record is compared with the latch: an implausible latch degrades every
// group, a valid record with more groups than the latch adds them (both only ever in the safe direction), a record
// that no longer matches is written again.
void SafeSupervisor::holdDegradedGroups()
{
    uint32_t groups        = 0U;
    bool const plausible   = degradedGroups(groups);
    bool const recordValid = degraded::isValid(degradedRecord, DEGRADED_ALL_GROUPS);
    uint32_t const kept    = recordValid ? degradedRecord.groups : 0U;
    if (!plausible || ((kept & ~groups) != 0U))
    {
        enterDegraded(
            groups | kept,
            recordValid ? degradedRecord.event : degraded::EVENT_IMPLAUSIBLE,
            recordValid ? degradedRecord.context : 0U);
        return;
    }
    if (groups == 0U)
    {
        return;
    }
    if (groups == DEGRADED_ALL_GROUPS)
    {
        // every group: S1 with its latch, read back by SafeIo::cyclic() (SYS-30)
        ::safety::SafeState::enterSafeState();
    }
    else
    {
#if NX_SAFE_SUPERVISOR_READBACK
        /* SYS-138 (review K6): the degraded groups are confirmed by read-back like S1 - their entries only, before the
         * walk of this cycle, so what is read is what the walk one safety cycle earlier (or SD itself) left. SD has no
         * latch, so SafeIo::cyclic() does not look at them. Not confirmed: the reaction of SafeIo (K5),
         * LOCK_REGISTER_ERROR with class SAFE_OUTPUT and the entry, S1 + S2 - SD stays fail-degraded only while its
         * groups are really off. An active short selected for a group (SYS-116/SYS-139) is a safe state here too. */
        uint32_t const unconfirmed = ::safety::degradedStateReadback(entriesOf(groups));
        if (unconfirmed != 0U)
        {
            lockRegisterMonitor.trigger(unconfirmed);
            return;
        }
#endif
        ::safety::SafeState::enterDegradedState(entriesOf(groups));
    }
    if (!recordValid || (kept != groups))
    {
        degraded::write(
            degradedRecord,
            groups,
            recordValid ? degradedRecord.event : degraded::EVENT_IMPLAUSIBLE,
            recordValid ? degradedRecord.context : 0U);
    }
}

// SYS-117 (decision B4 (a)): once per start, before the BSP. A power-on clears the record (the one way back, B6 (a));
// after any other start its groups go to their safe state again - all of them if it is implausible.
void SafeSupervisor::startDegraded(bool const powerOn)
{
    degraded::StartResult const result = degraded::onStart(degradedRecord, powerOn, DEGRADED_ALL_GROUPS);
    setDegradedGroups(result.groups);
    if (result.groups == 0U)
    {
        return;
    }
    if (result.groups == DEGRADED_ALL_GROUPS)
    {
        ::safety::SafeState::enterSafeState();
    }
    else
    {
        ::safety::SafeState::enterDegradedState(entriesOf(result.groups));
    }
    Logger::error(
        SAFETY,
        "SafeSupervisor: degraded groups 0x%x of 0x%x kept over the reset%s",
        result.groups,
        DEGRADED_ALL_GROUPS,
        result.recordValid ? "" : " (record implausible: every group)");
}
#undef NX_SAFE_DEGRADED_CHECK_MAPPING
#undef NX_SAFE_DEGRADED_CHECK_BUS_GROUPS
#endif

#if NX_SAFE_SUPERVISOR_BRIDGE_FAULT
// SYS-140 (decision C4, partner-and-degraded.md 5.9): the derived devices are checked here once more, at compile time -
// a stale or hand-edited configuration header with a word that names no group does not build.
static_assert(
    NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES <= bridge::MAX_DEVICES, "SYS-140: at most MAX_DEVICES");
#define NX_SAFE_BRIDGE_CHECK_DEVICE(device) \
    static_assert(bridge::deviceGroups(device) != 0U, "SYS-140: a bridge device without a group")

// SYS-140: every derived device with this instance adds its groups (tools/nxconfig: one word per bound device).
uint32_t SafeSupervisor::bridgeGroupsOf(uint32_t const instance)
{
    uint32_t groups  = 0U;
    auto const probe = [&groups, instance](uint32_t const device)
    {
        if (bridge::deviceInstance(device) == instance)
        {
            groups |= bridge::deviceGroups(device);
        }
    };
#define NX_SAFE_BRIDGE_PROBE(n)                                                         \
    NX_SAFE_BRIDGE_CHECK_DEVICE(NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICE_##n); \
    probe(NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICE_##n)
#if NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES > 0
    NX_SAFE_BRIDGE_PROBE(0);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES > 1
    NX_SAFE_BRIDGE_PROBE(1);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES > 2
    NX_SAFE_BRIDGE_PROBE(2);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES > 3
    NX_SAFE_BRIDGE_PROBE(3);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES > 4
    NX_SAFE_BRIDGE_PROBE(4);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES > 5
    NX_SAFE_BRIDGE_PROBE(5);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES > 6
    NX_SAFE_BRIDGE_PROBE(6);
#endif
#if NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES > 7
    NX_SAFE_BRIDGE_PROBE(7);
#endif
#undef NX_SAFE_BRIDGE_PROBE
    (void)probe;
    return groups;
}

// SYS-140 (decision C4): a well-formed word of a bridge fault class removes the short of the groups its device is bound
// to; a malformed word cannot be attributed and removes the short of every group (fail-safe: freewheel is always a safe
// state of every group, concept 5.2); every other class keeps the selection. Runs before any table walk of handle().
void SafeSupervisor::blockShortOnBridgeFault(uint32_t const word)
{
    bool const wellFormed = device::isWellFormed(word);
    if (wellFormed && !isBridgeFaultClass(device::faultOf(word)))
    {
        return;
    }
    uint32_t const groups = wellFormed ? bridgeGroupsOf(device::deviceOf(word)) : bridge::ALL_GROUPS;
    uint32_t const count  = nxSafeStateGroupCount();
    uint32_t blocked      = 0U;
    for (uint32_t group = 0U; (group < count) && (group < 32U); ++group)
    {
        if ((groups & (1UL << group)) != 0U)
        {
            nxSafeStateBlockShort(group);
            blocked |= 1UL << group;
        }
    }
    if (blocked != 0U)
    {
        Logger::error(
            SAFETY,
            "Event: DEVICE_FAULT, bridge fault (class %d%s): short of groups 0x%x removed until power-on",
            device::faultOf(word),
            wellFormed ? "" : ", malformed",
            blocked);
    }
}
#undef NX_SAFE_BRIDGE_CHECK_DEVICE
#endif

SafeSupervisorConstructor safeSupervisorConstructor;

SafeSupervisor& SafeSupervisor::getInstance() { return *safeSupervisorConstructor; }

// SYS-98 (STK-3): see safeSupervisor/EtlAssert.h. noinline: the return address is the one of the call through the
// ETL's handler pointer, i.e. the assert site.
__attribute__((noinline)) void etlAssertFailed(::etl::exception const& exception)
{
    (void)exception;
    uint32_t const callSite = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(__builtin_return_address(0)));
    // Before main() constructed the supervisor its storage is still zero (.mpu_bss is cleared by the startup code):
    // no record, the reset happens unmarked.
    SafeSupervisor::getInstance().reportEtlAssert(callSite);
    // Not reached: ETL_ASSERT is an S1 + S2 reaction. Should it ever come back, reset anyway.
    softwareSystemReset();
}

} // namespace safety
