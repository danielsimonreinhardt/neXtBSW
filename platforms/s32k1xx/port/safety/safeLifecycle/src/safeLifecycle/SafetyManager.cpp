/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "safeLifecycle/SafetyManager.h"

#include <safeIo/SafeIo.h>
#include <safeLifecycle/IsrHooks.h>
#include <safeLifecycle/ProgressSupervision.h>
#include <safeMemory/MemoryProtection.h>
#include <safeMemory/ProtectedRamScopedUnlock.h>
#include <safeMemory/RomCheck.h>
#include <safeMemory/SafeMemory.h>
#include <safeMemory/SafetyShell.h>
#include <safeSupervisionCheck/LpitDeadmanCheck.h>
#include <safeSupervisionCheck/LptmrCycleTimer.h>
#include <safeSupervisor/SafeSupervisor.h>
#include <safeSystem/SafeSystem.h>
#include <safeUtils/SafetyLogger.h>
#include <safeWatchdog/CyclePeriodCheck.h>
#include <safeWatchdog/SafeWatchdog.h>

#ifdef PLATFORM_SUPPORT_IO
#include <adcSafetyConsole/AdcSafetyConsole.h>
#endif

// SYS-130: NX_HW_THRESHOLD_COUNT - a project with a hardware threshold reports its shut-off path from here
#include <bsp/hwThreshold/hwThresholdConfiguration.h>
#if NX_HW_THRESHOLD_COUNT > 0
#include <bsp/io/outputPwm/ftmPwmConfiguration.h>
#include <hwThreshold/HwThresholdMonitor.h>
#include <safeSupervisor/HwShutoff.h>
#endif

::safety::SafeWatchdog safeWatchdog;
::safety::SafeSystem safeSystem;
::safety::SafeIo safeIo;
// SYS-40: state of the period check - zero initialized, so in the MPU protected .bss of this archive
::safety::CyclePeriodCheck safetyCyclePeriodCheck;

namespace safety
{
namespace
{
/* SYS-53: the LPO range of the derivative (mcudb clock.lpo, data sheet) relative to nominal, in per mille and rounded
 * outwards - derived by tools/nxconfig, before SYS-53 constants of safeSupervisionCheck/LptmrCycleTimer.h. The LPO
 * clocks the cycle timer (LPO1K_CLK) and the MCU watchdog. */
constexpr uint32_t LPO_MIN_PER_MILLE = NX_MODULE_SAFELIFECYCLE_DERIVED_LPO_MIN_PER_MILLE;
constexpr uint32_t LPO_MAX_PER_MILLE = NX_MODULE_SAFELIFECYCLE_DERIVED_LPO_MAX_PER_MILLE;
static_assert((LPO_MIN_PER_MILLE <= 1000U) && (LPO_MAX_PER_MILLE >= 1000U), "LPO range around its nominal value");
/* SYS-40 (F9, SM-04): bounds of the period check in ticks of the family's cycle timer, derived from the nominal
 * period P, the tolerated lateness D of a cycle start and the tolerance of the timer's clock (f_min ... f_max ticks
 * per nominal tick, the LPO range above). A counter difference misses the true time by less than one tick either
 * way.
 * - Interval: a healthy interval is at most P + D long and counts at most floor((P + D) * f_max) + 1 ticks - that is
 *   the largest value that passes. For the demo (10 ms, 2 ms, 1.086): 14 ticks; an interval of 17 ms or more is
 *   always reported at every LPO of the data sheet (17 ms at 113/128 kHz are 15.008 ticks), one of 12 ms or less
 *   never.
 * - Window of WATCHDOG_CYCLIC_COUNTER intervals: at least W * P - D long (the fixed-rate grid only shifts by the
 *   lateness of the first and the last start), so at least floor((W * P - D) * f_min) ticks: 59 for the demo (7 cycles
 *   since SYS-53, 68 with 8 before). A window below that means the task runs too fast against the timer (mean
 *   period below about 7.6 ms), or the timer
 *   runs too slowly or not at all. */
// ms * Hz * per mille / 10^6 = ticks
constexpr uint32_t CYCLE_INTERVAL_MAX_TICKS
    = (((SafetyManager::CYCLE_PERIOD_MS + SafetyManager::CYCLE_LATENESS_MS) * supervision::CYCLE_TIMER_NOMINAL_HZ
        * LPO_MAX_PER_MILLE)
       / 1000000U)
      + 1U;
constexpr uint32_t CYCLE_WINDOW_CYCLES = SafetyManager::CYCLE_PERIOD_WINDOW;
constexpr uint32_t CYCLE_WINDOW_MIN_TICKS
    = ((CYCLE_WINDOW_CYCLES * SafetyManager::CYCLE_PERIOD_MS - SafetyManager::CYCLE_LATENESS_MS)
       * supervision::CYCLE_TIMER_NOMINAL_HZ * LPO_MIN_PER_MILLE)
      / 1000000U;
/* SYS-25: the watchdog relation of finding F2 (doc/safety/concept.md 7.1) on the generated values, as a counter-check
 * of the derivation in tools/nxconfig (checks.py safety_timing reports the same with the YAML path first). The
 * window of the period check is the watchdog service interval (CYCLE_PERIOD_WINDOW = WATCHDOG_CYCLIC_COUNTER).
 * SYS-53 (decision sys25-lpo): at the fastest LPO of the data sheet - the WDOG counts the LPO, its driver loads the
 * counter for the nominal 128 kHz, so the time-out can be as short as time-out x 1000 / LPO_MAX_PER_MILLE (demo
 * 250 ms -> 230.2 ms, three intervals of 7 cycles = 210 ms). */
static_assert(SafetyManager::CYCLE_PERIOD_WINDOW >= 1U, "watchdog service interval of at least one cycle");
static_assert(
    (3U * SafetyManager::CYCLE_PERIOD_WINDOW * SafetyManager::CYCLE_PERIOD_MS * LPO_MAX_PER_MILLE)
        <= (NX_MODULE_SAFELIFECYCLE_PARAM_WATCHDOG_TIMEOUT_MS * 1000U),
    "three watchdog service intervals must fit into the watchdog time-out at the fastest LPO");
static_assert(SafetyManager::CYCLE_LATENESS_MS < SafetyManager::CYCLE_PERIOD_MS, "lateness of a whole period");
static_assert(CYCLE_WINDOW_MIN_TICKS > 0U, "period check: the window must span at least one tick of the timer");
static_assert(
    CYCLE_INTERVAL_MAX_TICKS < CYCLE_WINDOW_MIN_TICKS, "period check: one interval must be shorter than a window");
static_assert(
    (CYCLE_WINDOW_CYCLES * CYCLE_INTERVAL_MAX_TICKS) < supervision::CYCLE_TIMER_MASK,
    "period check: a window must fit into one wrap-around of the timer");

constexpr CyclePeriodLimits CYCLE_PERIOD_LIMITS{
    CYCLE_INTERVAL_MAX_TICKS, CYCLE_WINDOW_MIN_TICKS, CYCLE_WINDOW_CYCLES, supervision::CYCLE_TIMER_MASK};

#if NX_HW_THRESHOLD_COUNT > 0
/* SYS-130 (safety review SYS-86 H7): the hardware shut-off path of the PWM group, read in the safety cycle and reported
 * to the safe supervisor (safeSupervisor/HwShutoff.h), which holds S1 until reset: the timer has latched a fault
 * (FMS[FAULTF] - the threshold tripped), and SYS-128's findings of the path - the registers read here again (not
 * only by the power stage poll, whose task may have stopped), the start test and the consistency from the findings
 * latch. The supervisor reacts to new bits only, so the call in every cycle costs a few register reads. */
constexpr ::bios::hwThreshold::FaultControl HW_THRESHOLD_FAULT_CONTROL{
    ::bios::hwThresholdConfig::FTM_MODE_FAULT,
    ::bios::hwThresholdConfig::FTM_COMBINE_FAULTEN,
    ::bios::hwThresholdConfig::FTM_FLTCTRL,
    ::bios::hwThresholdConfig::FTM_FLTPOL};

void checkHwShutoff()
{
    uint32_t const context = ::bios::hwThreshold::shutoffContext(
        ::bios::hwThresholdConfig::cmp(),
        ::bios::hwThresholdConfig::trgmux(),
        ::bios::hwThresholdConfig::sim(),
        ::bios::ftmPwm::ftm(),
        ::bios::hwThresholdConfig::CONFIG,
        HW_THRESHOLD_FAULT_CONTROL);
    if (context != 0U)
    {
        (void)::safety::hwShutoff::report(context);
    }
}
#endif
} // namespace


#ifndef NX_SAFE_LIFECYCLE_NO_WEAK_SAFE_OFF_BOX
// SYS-194: a project without safe_off outputs has no hold and nothing to deliver; the integration replaces this with
// a strong definition (SafetyManager.h). Host tests that provide their own definition set the switch above.
__attribute__((weak)) DeviceReportBox* safeOffReportBox() { return nullptr; }
#endif

SafeSystem& getSafeSystem() { return ::safeSystem; }

using ::util::logger::Logger;
using ::util::logger::SAFETY;

SafetyManager::SafetyManager() : _counter(0) {}

void SafetyManager::init()
{
    Logger::debug(SAFETY, "SafetyManager initialized");
    {
        SafetyShell const safetyShell;
        resetCounterSafeIsrHook();
        safeWatchdog.init();
        safeSystem.init();
        safeIo.init();
        // SYS-40: the time base of the period check; the first cycle sets the reference
        supervision::startCycleTimer();
        ::safetyCyclePeriodCheck.restart();
    }
    RomCheck::init();
#ifdef PLATFORM_SUPPORT_IO
    // Console group "safeadc" (IO-19): the call only makes the linker keep the library, the group registers
    // itself during static initialization (adcSafetyConsole/AdcSafetyConsole.h).
    registerAdcSafetyConsole();
#endif
}

void SafetyManager::checkSupervisionPeripherals()
{
    // SYS-41 (finding F12 of doc/safety/fmea.md, SM-06): the deadman's configuration, from the safety task and not
    // from the BSP task that retriggers it. A project without a deadman has no expected configuration.
    auto const* const deadman = supervision::deadmanExpectedConfiguration();
    if (deadman != nullptr)
    {
        uint32_t const fields = supervision::checkLpitDeadman(*deadman);
        if (fields != 0U)
        {
            SafeSupervisor::getInstance().deadmanConfigMonitor.trigger(fields);
        }
    }
    // SYS-95 (DFA-1, DFA-2, DFA-6 a of doc/safety/dfa.md): the deadman's counter moves, the system time advances like
    // the LPTMR, the ROM re-check of the idle context still completes passes
    ::safety::progress::cyclic();
#if NX_HW_THRESHOLD_COUNT > 0
    // SYS-130: the hardware shut-off path of the PWM group (tripped or broken) to the safe supervisor
    checkHwShutoff();
#endif
}

void SafetyManager::checkCyclePeriod()
{
    // SYS-40 (F9, SM-04). The timer's configuration first: a stopped or re-clocked timer would make the period check
    // itself blind (latent fault), and a cleared clock gate would make the read a bus fault.
    uint32_t const fields = supervision::checkCycleTimer();
    uint32_t context      = 0U;
    if (fields != 0U)
    {
        context = CyclePeriodCheck::context(CyclePeriodCheck::TIMER_CONFIGURATION, fields);
    }
    else
    {
        context = ::safetyCyclePeriodCheck.sample(supervision::readCycleTimer(), CYCLE_PERIOD_LIMITS);
    }
    if (context != 0U)
    {
        SafeSupervisor::getInstance().safetyCyclePeriodMonitor.trigger(context);
    }
}

void SafetyManager::run() {}

void SafetyManager::shutdown() {}

void SafetyManager::cyclic()
{
    bool const safeRamLockStatusOnEnter = MemoryProtection::fusaGateIsLocked();
    ProtectedRamScopedUnlock const protectedRamUnlock;
    auto& supervisor = SafeSupervisor::getInstance();
    supervisor.mpuStatusCheckOnEnterMonitor.check(safeRamLockStatusOnEnter);
    supervisor.safetyManagerSequenceMonitor.hit(
        SafeSupervisor::SafetyManagerSequence::SAFETY_MANAGER_ENTER);
    // SYS-40: the period first - a cycle that comes too late reacts before its checks run (their results are
    // already older than the period promises)
    checkCyclePeriod();
    supervisor.safetyManagerSequenceMonitor.hit(
        SafeSupervisor::SafetyManagerSequence::SAFETY_MANAGER_PERIOD_CHECKED);
    ++_counter;
    // The safeWatchdog cyclic is called every WATCHDOG_CYCLIC_COUNTER cycles (demo 70 ms), to service the watchdog
    // thrice before the timeout occurs, also at the fastest LPO (demo 250 ms nominal, 230 ms at the fastest LPO)
    if (_counter >= WATCHDOG_CYCLIC_COUNTER)
    {
        supervisor.safeWatchdogSequenceMonitor.hit(
            safety::SafeSupervisor::EnterLeaveSequence::ENTER);
        safeWatchdog.cyclic();
        _counter = 0;
    }
    // SYS-41 (SM-04, fmea.md T2): one checkpoint after each sub-check. A cycle that returns early or leaves out a
    // check hits the next checkpoint out of order (or never reaches LEAVE, which the next ENTER sees):
    // SAFETY_MANAGER_SEQUENCE_DEVIATION.
    safe_memory::cyclic();
    supervisor.safetyManagerSequenceMonitor.hit(
        SafeSupervisor::SafetyManagerSequence::SAFETY_MANAGER_MEMORY_CHECKED);
    safeSystem.cyclic();
    supervisor.safetyManagerSequenceMonitor.hit(
        SafeSupervisor::SafetyManagerSequence::SAFETY_MANAGER_SYSTEM_CHECKED);
    safeIo.cyclic();
    supervisor.safetyManagerSequenceMonitor.hit(
        SafeSupervisor::SafetyManagerSequence::SAFETY_MANAGER_IO_CHECKED);
    checkSupervisionPeripherals();
    supervisor.safetyManagerSequenceMonitor.hit(
        SafeSupervisor::SafetyManagerSequence::SAFETY_MANAGER_PERIPHERALS_CHECKED);
    // SYS-37: reports from tasks without write access to this RAM (RTE deadlines, SM-05), delivered here with the
    // gate open instead of opening the gate for the reporting task
    supervisor.deliverDeferredEvents();
    // SYS-194: the take-backs of foreign safe_off levels (hold of the integration, TASK_BSP) - report only, S3
    DeviceReportBox* const safeOffReports = safeOffReportBox();
    if (safeOffReports != nullptr)
    {
        (void)supervisor.deliverDeviceReports(*safeOffReports);
    }
#ifdef PLATFORM_SUPPORT_IO
    // SYS-37: "safeadc reset" runs in the console task, which may not write safeSystem; it leaves a request that
    // is carried out here
    if (takeAdcDiagnosticsResetRequest())
    {
        safeSystem.resetAdcDiagnostics();
    }
#endif
    supervisor.safetyManagerSequenceMonitor.hit(
        SafeSupervisor::SafetyManagerSequence::SAFETY_MANAGER_LEAVE);
    bool const safeRamLockStatusOnLeave = MemoryProtection::fusaGateIsLocked();
    supervisor.mpuStatusCheckOnLeaveMonitor.check(safeRamLockStatusOnLeave);
}
} // namespace safety
