/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#pragma once

#include "safeSupervisor/Degraded.h"
#include "safeSupervisor/DeviceReport.h"
#include "safeSupervisor/DeviceReportBox.h"
#include "safeSupervisor/ResetLoopLimit.h"
#include "safeSupervisor/SafetyEventMailbox.h"
#include "safeSupervisor/utils/explicitly_constructible.h"

// SYS-53: module parameters of the supervisor (tools/bswgen from modules.safeSupervisor of the project)
#include <integration/safeSupervisorConfiguration.h>
#include <safeMonitor/Sequence.h>
#include <safeMonitor/Trigger.h>
#include <safeMonitor/Value.h>

#include <cstdint>

/* SYS-117: the degraded reaction SD (safeSupervisor/Degraded.h) is compiled in only for a project with a degraded
 * mapping - tools/nxconfig derives NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS (and the group and mapping words)
 * only then. Without it not one line of this file or of SafeSupervisor.cpp changes, so every other project keeps its
 * reactions exactly (doc/changelog.d/SYS-117.md: the object code of the demo is the same). */
#ifdef NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS
#define NX_SAFE_SUPERVISOR_DEGRADED 1
#else
#define NX_SAFE_SUPERVISOR_DEGRADED 0
#endif

/* SYS-140 (decision C4): the removal of the active short on a bridge fault (safeSupervisor/Degraded.h,
 * NX_SAFE_BRIDGE_FAULT_CLASSES) is compiled in only for a project in which a PWM group allows an active short -
 * tools/nxconfig/safestate.py derives NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES only then. Every other
 * project keeps this file's and SafeSupervisor.cpp's object code. */
#ifdef NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES
#define NX_SAFE_SUPERVISOR_BRIDGE_FAULT 1
#else
#define NX_SAFE_SUPERVISOR_BRIDGE_FAULT 0
#endif

namespace safety
{

class SafeSupervisor
{
public:
    enum class Event : uint8_t
    {
        SAFETY_MANAGER_SEQUENCE_DEVIATION,
        WATCHDOG_STARTUP_CHECK_FAILURE,
        SAFE_WATCHDOG_SEQUENCE_DEVIATION,
        SAFE_WATCHDOG_CONFIGURATION_ERROR,
        SAFE_WATCHDOG_SERVICE_DEVIATION,
        MPU_UNLOCKED_ON_SAFETY_MANAGER_ENTRY,
        MPU_LOCKED_ON_SAFETY_MANAGER_EXIT,
        INTEGRITY_EVENT_DMA_ECC_ERROR,
        ROM_CHECK_FAILURE,
        MPU_NOT_ENABLED,
        MPU_CONFIGURATION_ERROR,
        ADC_REFERENCE_ERROR,
        INTERNAL_SUPPLIES_ERROR,
        CHECK_REGISTER_ERROR,
        LOCK_REGISTER_ERROR,
        RTE_TIMING_VIOLATION,
        /* SYS-41 (finding F12 of doc/safety/fmea.md, SM-06): the configuration of the debug-halt/stall deadman
         * (timer, its interrupt enable and priority) differs from what its owner set up. Context: the
         * deviating fields (family port, safeSupervisionCheck/LpitDeadmanCheck.h on the S32K1). */
        DEADMAN_CONFIGURATION_ERROR,
        /* SYS-40 (finding F9 of doc/safety/fmea.md, SM-04): the safety task's own period, measured against an
         * independent timer of the family port, is out of its bounds - one cycle came too late, a window of cycles
         * was too short (task too fast, or the timer too slow or stopped), or the timer's configuration deviates.
         * Context: kind in bits 31..24 (1 = interval too long, 2 = window too short, 3 = timer configuration), the
         * measured ticks resp. the deviating fields in bits 23..0 (safeWatchdog/CyclePeriodCheck.h). SYS-95 (DFA-6 a
         * of doc/safety/dfa.md): kind 4 = the OpenBSW system time advanced less than the same timer allows over the
         * window (stopped: value 0), 5 = more; the system time of the window in ms in bits 23..0
         * (safeWatchdog/ProgressChecks.h, TimeBaseCheck). */
        SAFETY_CYCLE_PERIOD_VIOLATION,
        /* SYS-29 (gap G5, SM-34): a device reports a fault that needs no switching-off reaction - S3 by
         * construction, whatever its fault class. Context: the device context word (safeSupervisor/
         * DeviceReport.h: bus id, device instance, fault class, detail). From any context through the package's
         * DeviceReportBox (delivered by the safety task), or from a check in the safety task. Communication faults
         * of two devices on one bus within DEVICE_BUS_WINDOW_CYCLES are one event "bus lost" (F5). */
        DEVICE_REPORT,
        /* SYS-29 (G5, SM-34): a device fault with a switching-off reaction, chosen per fault class in handle()
         * (IDENTITY: S1 held; every other class and a malformed word: S1 + S2). Safety context only - the
         * detection runs in the safety task (decision `sys37-postfach-abschaltend`). Context: device context word. */
        DEVICE_FAULT,
        /* SYS-43 (finding F13 / task T4 of doc/safety/fmea.md, SSR-065): this start followed the configured number
         * of consecutive safety resets (safeSupervisor/ResetLoopLimit.h) - S1 held without a reset, only a power
         * cycle ends it (decision `sys43-resetschleife`). Raised once per start by startResetLoopLimit() in main()
         * of the family port, before the BSP starts. Context: cause of the last safety reset in bits 31..16, the
         * count in bits 15..0 (resetloop::contextOf). */
        RESET_LOOP_LIMIT,
        /* SYS-80 (finding R1 of doc/safety/rtos-comparison.md): the kernel's stack check found the stack of a task
         * overflowed (FreeRTOS: vApplicationStackOverflowHook, configCHECK_FOR_STACK_OVERFLOW 2, at the context
         * switch). S1 + S2: the memory next to that stack may already be overwritten, the kernel must not go on.
         * Raised through reportStackOverflow() by the kernel fault hook of the family port. Context: the context
         * number of the task (its OS task number); the console line also names the task. */
        STACK_OVERFLOW,
        /* SYS-80 (R2): a consistency check of the kernel failed (FreeRTOS: configASSERT) - from a task, an interrupt
         * or before the scheduler runs. S1 + S2. Raised through reportRtosAssert() by the family port. Context: the
         * code address of the call of the port's assert hook (0 if the port cannot tell); the console line adds the
         * source line of the check. */
        RTOS_ASSERT,
        /* SYS-98 (finding STK-3 of doc/safety/sw-architecture.md, SYS-83): an ETL_ASSERT failed - anywhere, from any
         * context. S1 + S2 with its own console line and cause, counted as a safety reset. Raised through
         * reportEtlAssert(), which writes nothing of the supervisor, so no SafetyShell is needed. Context: the code
         * address the assert handler returns to (the ETL_ASSERT call site). */
        ETL_ASSERT
    };

    /* Checkpoints of one safety cycle, hit in this order by SafetyManager::cyclic of the family port. SYS-41
     * (SM-04, fmea.md T2): one checkpoint per sub-check instead of only the brackets, so a cycle that returns
     * early or leaves out one of the checks is a SAFETY_MANAGER_SEQUENCE_DEVIATION at the next hit. */
    enum class SafetyManagerSequence : uint8_t
    {
        SAFETY_MANAGER_ENTER,
        SAFETY_MANAGER_PERIOD_CHECKED, // SYS-40: the period check, first in the cycle
        SAFETY_MANAGER_MEMORY_CHECKED,
        SAFETY_MANAGER_SYSTEM_CHECKED,
        SAFETY_MANAGER_IO_CHECKED,
        SAFETY_MANAGER_PERIPHERALS_CHECKED,
        SAFETY_MANAGER_LEAVE
    };

    enum class EnterLeaveSequence : uint8_t
    {
        ENTER,
        LEAVE
    };

    using SafetyManagerSequenceMonitor
        = ::safeMonitor::Sequence<SafeSupervisor, Event, SafetyManagerSequence>;
    using TriggerMonitor
        = ::safeMonitor::Trigger<SafeSupervisor, Event, ::safeMonitor::DefaultMutex, uint32_t>;
    using SequenceMonitor = ::safeMonitor::Sequence<SafeSupervisor, Event, EnterLeaveSequence>;
    /* SYS-41: with a context, so a failed configuration check can say which fields deviate (0 = none given) */
    using ValueMonitor
        = ::safeMonitor::Value<SafeSupervisor, Event, bool, ::safeMonitor::DefaultMutex, uint32_t>;
    /* SYS-37: a trigger monitor for reporters outside the safety task (safeSupervisor/SafetyEventMailbox.h) */
    using DeferredTriggerMonitor = DeferredTrigger<SafeSupervisor, Event>;

    /* SYS-98 (finding STK-4 of doc/safety/sw-architecture.md): handler of the kernel fault monitors. It runs the
     * S1 + S2 reaction of STACK_OVERFLOW and RTOS_ASSERT alone - not the switch of handle() with the log lines of the
     * other events, which a configASSERT in an interrupt (SysTick) would otherwise bring onto the interrupt stack
     * (tools/stackuse follows every branch). */
    class KernelFaultReaction
    {
    public:
        explicit KernelFaultReaction(SafeSupervisor& supervisor) : _supervisor(supervisor) {}

        void handle(Event const& event);

    private:
        SafeSupervisor& _supervisor;
    };

    using KernelFaultMonitor
        = ::safeMonitor::Trigger<KernelFaultReaction, Event, ::safeMonitor::DefaultMutex, uint32_t>;

    SafeSupervisor();

    void init() const;
    void handle(Event const& event);
    /**
     * SYS-37: hands the reports that reporters outside the safety task posted to the deferred monitors
     * (rteTimingMonitor) to handle(). Safety context only - the safety task calls it in every cycle with the
     * protected RAM writable (SafetyManager::cyclic of the family port). SYS-117: also walks the degraded groups
     * again in every cycle (a project with a degraded mapping only).
     */
    void deliverDeferredEvents();
    /**
     * SYS-29: hands the report-only messages of one device package (its DeviceReportBox in ordinary RAM) to
     * deviceReportMonitor - every one ends as DEVICE_REPORT (S3); nothing from a box can switch anything off.
     * Safety context only, like deliverDeferredEvents(); the supervisor keeps no reference to the box.
     * \return number of words handed to handle() (including CONTEXT_LOST/CONTEXT_CORRUPTED of the box)
     */
    uint32_t deliverDeviceReports(DeviceReportBox& box);
    /**
     * SYS-43 (F13, SSR-065): the limit on consecutive safety resets, once per start from main() of the family port
     * inside its SafetyShell, after the start-up's own resets (watchdog fast test) and before the BSP starts.
     * Counts this start in `record` (no-init RAM of the family port) if it followed a safety reset (`start`, see
     * resetloop::onStart); from `limit` (>= 1) consecutive ones on, raises RESET_LOOP_LIMIT: S1 held without a
     * reset, so on the target this call does not return then. Otherwise the supervisor keeps the record: handle()
     * marks it before every reset of an S1 + S2 reaction, and after `clearCycles` safety cycles
     * (deliverDeferredEvents(), one per cycle) without a reset the count is cleared (fault-free operation).
     * SYS-117: in a project with a degraded mapping it first applies the degraded groups of the record in no-init RAM
     * again (every start but a power-on, which clears the record), before the BSP and the application run.
     * 
eturn the start as counted (count including this start)
     */
    resetloop::StartResult startResetLoopLimit(
        resetloop::Record volatile& record, resetloop::Start const& start, uint32_t limit, uint32_t clearCycles);
    /**
     * SYS-29 (F5): true while the supervisor regards bus `bus` as lost - communication faults of at least two
     * different devices on it, the last one at most DEVICE_BUS_WINDOW_CYCLES safety cycles ago. Read only, from
     * any task (the RTE sets the qualifier of the device values on that bus to QLF_ERROR); a value may be one
     * safety cycle old. Stays true while the safety task does not run (its cycles are the time base).
     */
    bool deviceBusLost(uint8_t bus) const;
    /**
     * SYS-80 (R1): the kernel found the stack of a task overflowed. Keeps a printable copy of `taskName` (at most
     * KERNEL_FAULT_TEXT_SIZE - 1 characters, anything outside printable ASCII becomes '?', nullptr or empty gives
     * "?") for the console line and raises STACK_OVERFLOW with `taskNumber` as context: S1 + S2, so on the target
     * this call does not return.
     * From every context - a task, an interrupt, the kernel's context switch, before the scheduler - but only with
     * write access to the protected RAM: the family port calls it inside its SafetyShell (S32K1: the kernel fault
     * hooks in main/src/os/isr/isr_sys.cpp). Kernel neutral: the name and the number are what every kernel knows
     * of a task.
     */
    void reportStackOverflow(char const* taskName, uint32_t taskNumber);
    /**
     * SYS-80 (R2): a consistency check of the kernel failed. Raises RTOS_ASSERT with `callSite` (code address of the
     * call of the port's assert hook, 0 if unknown) as context and names `line` (source line of the check, 0 if
     * unknown) in the console line: S1 + S2, does not return on the target. Same contexts and the same SafetyShell
     * rule as reportStackOverflow().
     */
    void reportRtosAssert(uint32_t callSite, uint32_t line);
    /**
     * SYS-98 (STK-3): an ETL_ASSERT failed. Raises ETL_ASSERT with `callSite` (code address of the assert site, 0 if
     * unknown): safe outputs off (S1), mark of the reset-loop record, the console line
     * "SAFETY RESET (ETL_ASSERT, at 0x...): outputs off" through safeConsoleWrite(), reset (S2) - the order of
     * handle(), without a log line and without formatting through printf or etl::print. Does not return on the
     * target.
     * From every context - a task, an interrupt, before the scheduler, before the supervisor is constructed (then
     * there is no record to mark yet) - and without a SafetyShell: const, it only reads the supervisor (the
     * protected RAM is readable everywhere), so an assert in a QM task does not end in an MPU fault. The limp-home
     * flag is not set; the reset follows at once. Registered for ETL through etlAssertFailed() (EtlAssert.h).
     */
    void reportEtlAssert(uint32_t callSite) const;
    /**
     * SYS-98 (STK-4): a non-correctable ECC error reported by a fault interrupt of the family port (S32K1: ERM
     * double-bit and FTFC fault, level 0, on the interrupt stack). The same S1 + S2 reaction and console line as
     * INTEGRITY_EVENT_DMA_ECC_ERROR in handle() ("SAFETY RESET (INTEGRITY_EVENT_DMA_ECC_ERROR, sources 0x...)",
     * `sources` as for dmaEccMonitor), but without its log line and the switch of handle(), and const like
     * reportEtlAssert(): an interrupt wrapped by SAFE_ISR runs with the protected RAM closed. Does not return on the
     * target. The poll of the safety task keeps using dmaEccMonitor.
     */
    void reportEccError(uint32_t sources) const;
    static SafeSupervisor& getInstance();

    /**
     * SYS-117 (decision B2 (a)): true for the events a project may map to the degraded reaction SD - the closed list
     * NX_SAFE_DEGRADABLE_EVENTS of safeSupervisor/Degraded.h. A fact of the supervisor, not a project value.
     */
    static constexpr bool isDegradableEvent(Event const event)
    {
#define NX_SAFE_DEGRADABLE_CASE(name) (event == Event::name) ||
        return NX_SAFE_DEGRADABLE_EVENTS(NX_SAFE_DEGRADABLE_CASE) false;
#undef NX_SAFE_DEGRADABLE_CASE
    }

    /**
     * SYS-140 (decision C4): true for the device fault classes that indicate a bridge fault - the list
     * NX_SAFE_BRIDGE_FAULT_CLASSES of safeSupervisor/Degraded.h. A DEVICE_FAULT of such a class from a device bound to
     * a PWM group removes the active short of that group before the reaction walks a table.
     */
    static constexpr bool isBridgeFaultClass(uint8_t const faultClass)
    {
#define NX_SAFE_BRIDGE_FAULT_CASE(name) (faultClass == static_cast<uint8_t>(device::FaultClass::name)) ||
        return NX_SAFE_BRIDGE_FAULT_CLASSES(NX_SAFE_BRIDGE_FAULT_CASE) false;
#undef NX_SAFE_BRIDGE_FAULT_CASE
    }

#if NX_SAFE_SUPERVISOR_DEGRADED
    /** SYS-117: safe-output groups of the project (safety.output_groups), bit k = group k. */
    static constexpr uint32_t DEGRADED_GROUP_COUNT = NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS;
    static_assert(
        (DEGRADED_GROUP_COUNT >= 1U) && (DEGRADED_GROUP_COUNT <= degraded::MAX_GROUPS),
        "safe-output groups: 1 .. degraded::MAX_GROUPS");
    static constexpr uint32_t DEGRADED_ALL_GROUPS = degraded::allGroupsOf(DEGRADED_GROUP_COUNT);

    /**
     * SYS-117: the safe-output groups in the degraded reaction SD, bit k = safety.output_groups[k]; they stay there
     * until the next power cycle (decision B6 (a)). Read only, from any task - the RTE signal safety.degraded
     * (DegradedRte.cpp). 
eturn false if the supervisor's copy is implausible; `groups` then reads every group
     * (fail-safe), and the safety task degrades them all in its next cycle.
     */
    bool degradedGroups(uint32_t& groups) const;

    /**
     * IO-45: the safe-output groups that contain entry `entry` of the safe outputs table (bit k =
     * safety.output_groups[k]; 0 for an entry of no group, e.g. a safe_off output of a device that drives no PWM
     * group). From the derived entry masks of the groups (tools/nxconfig/degraded.py).
     */
    static uint32_t groupsOfEntry(uint32_t entry);

    /**
     * IO-45: true while entry `entry` of the safe outputs table belongs to a degraded group - the lock of the drivers
     * of that group (the enable of a gate driver bound to the group's PWM group, SYS-123: the PWM group itself). Read
     * only, from any task, like degradedGroups(); an implausible latch reads as every group degraded (fail-safe).
     */
    bool isEntryDegraded(uint32_t entry) const;

    /**
     * SYS-123: walks the degraded groups again (every group degraded: S1 as the hold does) - from main() of the family
     * port right after the BSP start, whose drivers wrote their registers from their configuration (the PWM driver:
     * the software output control of the timer). Together with the per-group lock of the drivers it closes the window
     * of at most one safety cycle that SYS-117 left after a safety reset (partner-and-degraded.md 4.1 step 1). Const:
     * writes no supervisor state, only the outputs (and, for every group, the S1 latch in ordinary RAM).
     */
    void applyDegradedGroups() const;

    /**
     * SYS-135 (safety review SYS-128/SYS-130 C1): the degraded reaction to a report of a hardware shut-off path
     * (safeSupervisor/HwShutoff.h) - called by hwShutoff::report() in the safety context, with the interrupt lock
     * taken. Degrades only the group of the tripped path if `context` says "tripped" and nothing else (bit 0, no
     * findings of the path in bits 23..8) and the project maps HW_SHUTOFF of that timer (bits 31..24) to it
     * (Degraded.h NX_SAFE_DEGRADABLE_REPORTS); the last group escalates to S1 without a reset as for any SD. No reset
     * in either case - the path stays latched until reset (H1).
     * \return true if the report was degraded; false: the caller keeps S1 held (image without a HW_SHUTOFF mapping,
     *         unmapped timer, findings, malformed context)
     */
    bool degradeHwShutoff(uint32_t context);
#endif

    void enterLimpHome() { _limpHome = true; }

    void leaveLimpHome() { _limpHome = false; }

    bool inLimpHomeState() const { return _limpHome; }

    SafetyManagerSequenceMonitor safetyManagerSequenceMonitor;
    TriggerMonitor watchdogStartupCheckMonitor;
    SequenceMonitor safeWatchdogSequenceMonitor;
    ValueMonitor safeWatchdogConfigMonitor;
    TriggerMonitor serviceWatchdogMonitor;
    ValueMonitor mpuStatusCheckOnEnterMonitor;
    ValueMonitor mpuStatusCheckOnLeaveMonitor;
    /* SM-15/SM-16. SYS-41 (F11): context = the sources of the non-correctable ECC error, bit 0 RAM channel 0,
     * bit 1 RAM channel 1, bit 2 program/data flash (safeMemory/IntegrityEccHandler.h, EccSource). The name is
     * the historical one (it is RAM and flash, not DMA); kept because doc/safety refers to it. */
    TriggerMonitor dmaEccMonitor;
    TriggerMonitor romCheckMonitor;
    TriggerMonitor mpuEnableMonitor;
    TriggerMonitor mpuConfigMonitor;
    TriggerMonitor adcReferenceMonitor;
    TriggerMonitor internalSuppliesMonitor;
    /* SM-22. Context (SYS-169): CHECK_REGISTER_CONTEXT_OK = the register check is fine again (leave S1), 0 = the
     * SAFETY_TEST pin of the OpenBSW demo changed (enter S1), any other value = context of the deviating entry of the
     * register list (registers::context(): class in bits 31..24, index in bits 15..0; enter S1) - the log line names
     * it. A register context is never 1: its class is not 0. */
    static constexpr uint32_t CHECK_REGISTER_CONTEXT_OK = 1U;
    TriggerMonitor checkRegisterMonitor;
    TriggerMonitor lockRegisterMonitor;
    /* SYS-41 (F12, SM-06): configuration re-check of the deadman, safety task only. Context: deviating fields.
     * SYS-95 (DFA-2): field 0x100 = the deadman's counter stands still (its LPIT clock stopped). */
    TriggerMonitor deadmanConfigMonitor;
    /* SYS-05: a task cycle or a runnable took longer than its configured period (ecu/application,
     * systems/RteSystem.cpp). Context: kind in bits 31..24 (1 = task cycle, 2 = runnable), row of
     * rte::TASKS resp. rte::RUNNABLES in bits 23..16, overshoot in ms in bits 15..0.
     * SYS-37: deferred - the RTE tasks have no write access to the protected RAM, so trigger() only posts into
     * rteTimingMailbox and the safety task delivers the report (deliverDeferredEvents). */
    DeferredTriggerMonitor rteTimingMonitor;
    /* SYS-40 (F9, SM-04): period of the safety task, safety task only (SafetyManager::cyclic of the family port).
     * Context: see Event::SAFETY_CYCLE_PERIOD_VIOLATION. */
    TriggerMonitor safetyCyclePeriodMonitor;
    /* SYS-29 (G5): report-only device messages (Event::DEVICE_REPORT, S3). Triggered only in the safety context:
     * by deliverDeviceReports() for the boxes of the packages, or by a check in the safety task. */
    TriggerMonitor deviceReportMonitor;
    /* SYS-29 (G5): switching-off device faults (Event::DEVICE_FAULT), safety task only. */
    TriggerMonitor deviceFaultMonitor;
    /* SYS-43 (F13): the reset-loop limit is reached (Event::RESET_LOOP_LIMIT), raised by startResetLoopLimit(). */
    TriggerMonitor resetLoopMonitor;

private:
    // SYS-98: declared before the two monitors below, which keep a reference to it
    KernelFaultReaction _kernelFaultReaction;

public:
    /* SYS-80 (R1): stack overflow found by the kernel (Event::STACK_OVERFLOW), raised by reportStackOverflow().
     * SYS-98: handled by KernelFaultReaction, not handle(). */
    KernelFaultMonitor stackOverflowMonitor;
    /* SYS-80 (R2): failed kernel assertion (Event::RTOS_ASSERT), raised by reportRtosAssert(). SYS-98: the same. */
    KernelFaultMonitor rtosAssertMonitor;

    /** SYS-80: longest task name kept for the console line of STACK_OVERFLOW, including the terminating 0. */
    static constexpr uint32_t KERNEL_FAULT_TEXT_SIZE = 16U;

    /** SYS-29 (F5): window of the per-bus correlation in safety cycles. SYS-53: a project value in time
     * (modules.safeSupervisor.device_bus_window_ms, default 200 ms: two supervision cycles of the slowest device
     * period a manifest allows today, cycle_ms up to 100 ms), converted by tools/nxconfig into cycles of the safety
     * task, ceil(window / task_period_ms) - demo 20. */
    static constexpr uint32_t DEVICE_BUS_WINDOW_CYCLES = NX_MODULE_SAFESUPERVISOR_DERIVED_DEVICE_BUS_WINDOW_CYCLES;
    static_assert(DEVICE_BUS_WINDOW_CYCLES >= 1U, "device-bus window of at least one safety cycle");
    /** SYS-29: buses the correlation tracks at the same time; a further bus is reported without correlation. */
    static constexpr uint32_t DEVICE_BUS_SLOTS = 8U;

    /** Rounds of the hold loop of holdSafeState() (SYS-23); 0 as long as no event led to a hold. */
    uint32_t holdRounds() const { return _holdRounds; }

private:
    // SYS-23: safe state held without reset, for faults that a reset cannot remove (the watchdog self-test and
    // the ROM check). Writes one console line naming `event`, then holds. Returns only in the host test
    // (NX_SAFE_SUPERVISOR_HOLDING in SafeSupervisor.cpp).
    void holdSafeState(char const* event);
    // SYS-80/SYS-98: S1 + S2 of STACK_OVERFLOW and RTOS_ASSERT with their console line, without a log line - the
    // handler of the kernel fault monitors (KernelFaultReaction), not part of handle()
    void resetOnKernelFault(Event const& event);

    // SYS-29 (F5): one tracked bus of the device correlation
    struct DeviceBus
    {
        uint32_t lastCycle; // safety cycle of the last communication fault on the bus
        uint8_t bus;        // bus id, 0 = slot free (0 is never a real bus)
        uint8_t lastDevice; // device instance of that fault
        bool lost;          // two different devices within the window: "bus lost" reported
    };

    // SYS-29: DEVICE_REPORT and DEVICE_FAULT with their context word
    void handleDeviceReport(uint32_t word);
    void handleDeviceFault(uint32_t word);
    // SYS-29 (F5): records a communication fault on `bus`; true if it made the bus "lost" just now
    bool correlateDeviceBus(uint8_t bus, uint8_t deviceInstance);
    DeviceBus const* findDeviceBus(uint8_t bus) const;
    // SYS-43: clears the reset-loop count once the fault-free time is over (from deliverDeferredEvents())
    void clearResetLoopAfterFaultFreeTime();
#if NX_SAFE_SUPERVISOR_DEGRADED
    // SYS-117: the degraded reaction SD (Degraded.h, doc/safety/partner-and-degraded.md section 4)
    // true if DEVICE_FAULT with `word` is degraded (the groups are switched, the fault is handled), false if it keeps
    // its fail-safe reaction
    bool degradeDeviceFault(uint32_t word);
    // groups a mapping of the project gives `event` of device `instance` on `bus`; 0 = none (fail-safe reaction)
    static uint32_t degradedGroupsOf(Event event, uint32_t instance, uint32_t bus, bool busLost);
    // SYS-135: the same for the event field `id` of a mapping word - an event or a report of Degraded.h
    static uint32_t degradedGroupsOfId(uint32_t id, uint32_t instance, uint32_t bus, bool busLost);
    // entries of the safe outputs table that belong to `groups` (bit i = entry i of the table)
    static uint32_t entriesOf(uint32_t groups);
    // SD for `groups`: their outputs, the latch, the record, the report; the last group escalates to S1 (no reset)
    void enterDegraded(uint32_t groups, uint32_t event, uint32_t context);
    // every safety cycle: the degraded groups again, the record against the latch (from deliverDeferredEvents())
    void holdDegradedGroups();
    // once per start (from startResetLoopLimit()): the groups of the record before the BSP starts
    void startDegraded(bool powerOn);
    void setDegradedGroups(uint32_t groups);
#endif
#if NX_SAFE_SUPERVISOR_BRIDGE_FAULT
    // SYS-140 (decision C4): removes the active short of the groups a bridge fault of DEVICE_FAULT `word` concerns -
    // first in the reaction, before any table walk (Degraded.h, NX_SAFE_BRIDGE_FAULT_CLASSES)
    static void blockShortOnBridgeFault(uint32_t word);
    // selection groups of the devices with instance `instance` bound to a PWM group with an active short (0: none)
    static uint32_t bridgeGroupsOf(uint32_t instance);
#endif

    uint32_t volatile _holdRounds;
    // SYS-29: safety cycles, counted by deliverDeferredEvents() (one call per cycle) - time base of the correlation
    uint32_t volatile _safetyCycles;
    DeviceBus _deviceBuses[DEVICE_BUS_SLOTS];
    // SYS-43: record of the reset-loop limit (no-init RAM of the family port), nullptr until startResetLoopLimit();
    // safety cycles of the fault-free time, cycle count at the start, and whether the count was cleared this run
    resetloop::Record volatile* _resetLoopRecord;
    uint32_t _resetLoopClearCycles;
    uint32_t _resetLoopStartCycle;
    bool _resetLoopCleared;
    bool _limpHome;
    // SYS-80: printable copy of the task name of the last STACK_OVERFLOW report, for its console line
    char _kernelFaultText[KERNEL_FAULT_TEXT_SIZE];
    // SYS-80: source line of the last RTOS_ASSERT report
    uint32_t _kernelFaultLine;
#if NX_SAFE_SUPERVISOR_DEGRADED
    // SYS-117: latch of the degraded groups (protected RAM, written in the safety context only) and its inverted copy
    uint32_t volatile _degradedGroups;
    uint32_t volatile _degradedGroupsInverted;
#endif
};

using SafeSupervisorConstructor = ::safety::explicitly_constructible<SafeSupervisor>;
extern SafeSupervisorConstructor safeSupervisorConstructor;

/* SYS-37: box of rteTimingMonitor. Defined by the family port outside the MPU protected safety RAM (S32K1:
 * platforms/s32k1xx/port/main/src/main.cpp) - the port owns the memory map; a port without it does not link. */
extern SafetyEventMailbox rteTimingMailbox;

#if NX_SAFE_SUPERVISOR_DEGRADED
/* SYS-117 (decision B4 (a)): record of the degraded groups in no-init RAM, outside the MPU protected safety RAM (it is
 * read before the BSP starts and must survive every reset but a power-on). Defined in SafeSupervisor.cpp in the section
 * NX_SAFE_DEGRADED_RECORD_ATTRIBUTE - on the target `.noinit.safeDegraded`, which the application linker script of the
 * family places in its no-init RAM (`*(.noinit .noinit.*)`, like `.noinit.resetLoop` of SYS-43). */
extern degraded::Record volatile degradedRecord;
#endif

} // namespace safety
