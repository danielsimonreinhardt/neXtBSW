/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include <cinttypes>
#include "clock/clockConfig.h"
// ARCH-18: PCC-Taktschalter der genutzten Peripherie (aus Derivat und Projekt erzeugt)
#include "bsp/clock/peripheralClocks.h"
// PLAT-85: start-up report of the clock check (platforms/common/clockCheck, nxClockCheck() of bspClock)
#include "clock/nxClockCheckReport.h"
// PLAT-21: generated derivative features (mcudb derivative.features) and interrupt levels of the project's ranks
#include "features/derivativeFeatures.h"
#include "integration/Integration.h"
// SYS-113: the cause of the safety reset before this start, reported to the fault memory by the BSP task
#include "integration/SafetyResetReport.h"
#include "interrupt_manager.h"
#include "interrupts/interruptPriorities.h"
// PLAT-12: interrupt vectors of the used peripherals under family neutral names (generated from the derivative)
#include "interrupts/nxIrqNames.h"
#include "lifecycle/StaticBsp.h"
#include "mcu/mcu.h"
// SYS-125 (AN-066): low-voltage detect reset of the PMC, switched on in boardInit()
#include "pmc/LowVoltageDetectReset.h"
#include "watchdog/Watchdog.h"

#if NX_FEATURE_LMEM_CACHE
#include "cache/cache.h"
#endif

#include <hardFaultHandler/HardFaultDump.h>

// SYS-43: start reasons of the MCU (SM-50, the capture before main() of platforms/s32k1xx/port/mcuResetReason)
#include <ecuState/EcuState.h>
#include <ecuState/McuStartReasons.h>
// SYS-84: start-up line with the reset status of the start (capture before main(), mcuResetReason)
#include <mcuResetReason/StartStatusReport.h>
// SYS-148: which safety reset the fault-handler record stands for (watchdog vector or fault exception)
#include <mcuResetReason/SafetyResetSources.h>
#include <safeLifecycle/SafetyManager.h>
#include <safeMemory/EccLatentTest.h>
#include <safeMemory/MemoryProtection.h>
#include <safeMemory/SafetyShell.h>
#include <safeSupervisor/SafeSupervisor.h>
#include <os/IsrStack.h>
#include <safeWatchdog/SafeWatchdog.h>
// SYS-137: record of the safe console lines of the start before (no-init RAM, SafeConsole.cpp)
#include "safeConsole/SafeConsoleRecord.h"

#if defined(SUPPORT_FREERTOS)
#include <os/FreeRtosPlatformConfig.h>
#elif defined(SUPPORT_THREADX)
#include <async/Config.h>
#include <bsp/clock/boardClock.h>
#include <tx_api.h>
#endif

#include <cstdio>

/* SYS-43 (F13, SSR-065): the two values of the reset-loop limit are project values (decision
 * `sys43-resetschleife`). SYS-53: module parameters of the safe supervisor (project.yaml
 * modules.safeSupervisor.reset_loop_limit / reset_loop_clear_ms, manifest platforms/s32k1xx/port/safety/
 * safeSupervisor/module.yaml, defaults 3 and 60000 ms), checked by tools/nxconfig with their YAML path and generated
 * by tools/bswgen - no default of its own here. */
#include <integration/safeSupervisorConfiguration.h>
static_assert(NX_MODULE_SAFESUPERVISOR_PARAM_RESET_LOOP_LIMIT >= 1U,
              "reset-loop limit: at least one safety reset (modules.safeSupervisor.reset_loop_limit)");
static_assert(NX_MODULE_SAFESUPERVISOR_PARAM_RESET_LOOP_CLEAR_MS >= ::safety::SafetyManager::CYCLE_PERIOD_MS,
              "reset-loop limit: the fault-free time must span a safety cycle (reset_loop_clear_ms)");

#if defined(SUPPORT_FREERTOS)
// PLAT-21: the RTOS priorities of the board port must agree with the generated values, otherwise an interrupt with
// a valid rank could still be masked by the kernel (FreeRtosPlatformConfig.h becomes a family template, ARCH-05).
static_assert(
    configPRIO_BITS == NX_NVIC_PRIORITY_BITS,
    "configPRIO_BITS (__NVIC_PRIO_BITS of the device header) differs from the NVIC priority bits of the "
    "derivative (mcudb nvic_priority_bits)");
static_assert(
    configMAX_SYSCALL_INTERRUPT_PRIORITY == NX_FREERTOS_MAX_SYSCALL_INTERRUPT_PRIORITY,
    "configMAX_SYSCALL_INTERRUPT_PRIORITY of FreeRtosPlatformConfig.h differs from the project's "
    "interrupts.max_syscall_level");
static_assert(
    configKERNEL_INTERRUPT_PRIORITY == NX_FREERTOS_KERNEL_INTERRUPT_PRIORITY,
    "configKERNEL_INTERRUPT_PRIORITY of FreeRtosPlatformConfig.h is not the lowest NVIC level of the derivative");
#elif defined(SUPPORT_THREADX)
// PLAT-71, counterpart of PLAT-21: the Cortex-M4 port of ThreadX locks the kernel with PRIMASK, so there is no
// kernel boundary that could mask an interrupt with a valid rank. Only with TX_PORT_USE_BASEPRI it masks up to
// TX_PORT_BASEPRI, which then has to be the project's boundary (interrupts.max_syscall_level).
#if defined(TX_PORT_USE_BASEPRI)
static_assert(
    TX_PORT_BASEPRI == NX_FREERTOS_MAX_SYSCALL_INTERRUPT_PRIORITY,
    "TX_PORT_BASEPRI differs from the project's interrupts.max_syscall_level");
#endif
// tx_initialize_low_level.S of the OpenBSW reference (used unchanged) loads SysTick with a fixed
// SYSTEM_CLOCK = 80000000 as SYSTEM_CLOCK / ASYNC_CONFIG_TICK_IN_US - 1. That is one tick of ASYNC_CONFIG_TICK_IN_US
// only for the core clock the reference was written for; any other project clock would give a wrong tick silently.
static_assert(
    80000000U / ASYNC_CONFIG_TICK_IN_US == (NX_CLOCK_CORE_HZ / 1000000U) * ASYNC_CONFIG_TICK_IN_US,
    "ThreadX: the SysTick reload of tx_initialize_low_level.S (80 MHz / ASYNC_CONFIG_TICK_IN_US) is not one tick at "
    "the project's core clock NX_CLOCK_CORE_HZ (PLAT-71)");
#endif

extern void app_main();

extern ::safety::SafeWatchdog safeWatchdog;

namespace safety
{
// SYS-43 (F13): record of the reset-loop limit (safeSupervisor/ResetLoopLimit.h) in the no-init RAM of the
// application linker script (.noinit.*, not cleared by startUp.S, outside the MPU protected safety RAM - this file
// belongs to the library "main"). It survives the software reset of a safety reaction, the watchdog reset and the
// resets of the watchdog fast test; boardInit() clears it after power-on/low voltage, before anything reads it.
// The bootloader does not touch it: its RAM is checked against the no-init area by tools/nxconfig/memory.py (error
// at memory.bootloader.ram), and its start-up only copies .data and clears .bss inside that RAM. The ELF test
// ecu/safety/safeSupervisor/test/test_safety_context.py checks the placement.
__attribute__((section(".noinit.resetLoop"))) resetloop::Record volatile resetLoopRecord;
} // namespace safety

namespace
{
// SYS-43: core lockup (RCM_SRS[LOCKUP]) of the start, i.e. of the last pass through boardInit() that was not a
// reset of the watchdog fast test. The wake-up bits of mcuResetReason fold LOCKUP into RESET, so boardInit() keeps
// this one bit itself. Written in every such pass before it is read (the first pass after power-on is one), so its
// random content and ECC state after power-on are never read.
__attribute__((section(".noinit.resetLoop"))) uint32_t volatile resetLoopLockup;
} // namespace

namespace
{
// SYS-43: how this start came about, from the two independent sources (fmea.md SM-50 and F13): the MCU's reset
// status captured before main() (mcuResetReason: power-on/low voltage, watchdog - not the fast test) and the
// hard fault dump of this port (SM-17 resets after writing it; cleared on power-on, reported and cleared later in
// main()). The supervisor's own mark in the record is the third input (resetloop::onStart).
// Board finding 2026-09-22: a J-Link reset starts with RESET | WATCHDOG (0x06), a real watchdog time-out with
// WATCHDOG alone - otherReset keeps the debugger reset from counting (resetloop::onStart). A core lockup (a fault
// while the fault handler runs, so the dump may be missing) counts as a fault exception.
// SYS-148 (SYS-104): the watchdog's pre-reset interrupt writes the same dump as a fault (isr_wdg.cpp); a valid dump
// with the watchdog's exception number counts as watchdog (cause 0x1), every other dump as fault exception (0x2) -
// board 2026-09-30 showed 0x2 after `safety wdg` and after `safety mpu` (mcuResetReason/SafetyResetSources.h).
::safety::resetloop::Start resetLoopStart()
{
    uint32_t const* const dump = ::hardfault::getDumpRegion();
    ::rcm::HandlerRecord const handler = ::rcm::handlerRecordOf(
        ::hardfault::getDumpState(dump) != ::hardfault::DumpState::NONE,
        ::hardfault::getDumpException(dump),
        resetLoopLockup != 0U,
        static_cast<uint32_t>(WDOG_EWM_IRQn) + 16U); // exception number = IRQ number + 16 (ARMv7-M and ARMv6-M)
    ::rcm::SafetyResetSources const sources = ::rcm::safetyResetSourcesOf(::ecustate::mcuStartReasons(), handler);
    ::safety::resetloop::Start start{};
    start.powerOn        = sources.powerOn;
    start.watchdog       = sources.watchdog;
    start.otherReset     = sources.otherReset;
    start.faultException = sources.faultException;
    return start;
}

} // namespace

extern "C"
{
/* SYS-137: the same record for J-Link (mem32 nxSafeConsoleBefore 6, any time after the start, also when no console
 * reader saw the start line): [0] bit 0 present, bit 1 intact; [1] lines; [2] requested and [3] written bytes of the
 * last line; [4] idle flushes; [5] flush time-outs. General .bss, written once per start by main(). */
uint32_t volatile nxSafeConsoleBefore[6];
}

namespace
{
/* SYS-137: one start line with the safe console record of the start before (safeConsole/SafeConsoleRecord.h) - did
 * the SAFETY RESET line (or a hold or fault line) of that start leave the MCU, and did its flushes find the
 * transmitter idle. Board finding 2026-09-30 (SYS-73 (1)): after the start-up ECC fault in main() the captures showed
 * no SAFETY RESET line and partly no output at all; this line answers from the ECU what a late console reader cannot.
 * Nothing if the start before wrote no line (the usual case). */
void reportSafeConsoleOfStartBefore(::platform::safeconsole::Snapshot const& before)
{
    nxSafeConsoleBefore[0] = (before.present ? 1U : 0U) | (before.intact ? 2U : 0U);
    nxSafeConsoleBefore[1] = before.lines;
    nxSafeConsoleBefore[2] = before.requested;
    nxSafeConsoleBefore[3] = before.written;
    nxSafeConsoleBefore[4] = before.flushesOk;
    nxSafeConsoleBefore[5] = before.flushTimeouts;
    if (!before.present)
    {
        return;
    }
    if (!before.intact)
    {
        printf("safe console of the start before: record not intact\r\n");
        return;
    }
    printf("safe console of the start before: %" PRIu32 " line(s), last %" PRIu32 " of %" PRIu32
           " bytes to the transmitter, flushes %" PRIu32 " idle, %" PRIu32 " timed out\r\n",
           before.lines,
           before.written,
           before.requested,
           before.flushesOk,
           before.flushTimeouts);
}
} // namespace

extern "C"
{
void ExceptionHandler()
{
    while (true)
    {
        printf("ExceptionHandler :(");
    }
}

void boardInit()
{
    /* Disables the watchdog early before the timeout occurs, which is then enabled later in the
       main function. */
    ::safety::bsp::Watchdog::disableWatchdog();
    // SYS-125 (AN-066): reset when VDD falls below the low-voltage detect threshold, not only below the low-voltage
    // reset threshold. LVDRE is cleared by power-on only; written in every start (pmc/LowVoltageDetectReset.h).
    ::platform::pmc::enableLowVoltageDetectReset(IP_PMC->LVDSC1);
    // PLAT-20: after power-on or low voltage the no-init RAM (content and ECC) is undefined: clear the hard
    // fault dump before anything reads it. Runs before the RAM initialization, uses no global data.
    ::hardfault::prepareDump((RCM->SRS & (RCM_SRS_POR_MASK | RCM_SRS_LVD_MASK)) == 0U);
    // SYS-43: the same for the record of the reset-loop limit - written before its first read after power-on
    if ((RCM->SRS & (RCM_SRS_POR_MASK | RCM_SRS_LVD_MASK)) != 0U)
    {
        ::safety::resetloop::clear(::safety::resetLoopRecord);
        // SYS-137: the same for the record of the safe console lines (whole words, before its first read)
        ::platform::safeconsole::clear(nxSafeConsoleRecord);
    }
    // SYS-43: LOCKUP of the start, kept across the resets of the watchdog fast test (WDOG_CS[TST] 2 or 3)
    {
        uint32_t const testField = (WDOG->CS & WDOG_CS_TST_MASK) >> WDOG_CS_TST_SHIFT;
        bool const fastTestReset = ((RCM->SRS & RCM_SRS_WDOG_MASK) != 0U) && ((testField == 2U) || (testField == 3U));
        if (!fastTestReset)
        {
            resetLoopLockup = RCM->SRS & RCM_SRS_LOCKUP_MASK;
        }
    }
    configurPll();
    // configurPll() ruft enablePeripheralClocks() des OpenBSW-Treibers, der nur die PCC-Register des
    // S32K148-Referenzboards kennt. Hier kommen die Gates der Peripherie dazu, die dieses Projekt nutzt und
    // die dort fehlt (z. B. FTM6, LPI2C1 eines S32K148); fuer die uebrigen ist der Wert derselbe.
    nxEnablePeripheralClocks();
#if NX_FEATURE_LMEM_CACHE
    // Processor code cache of the derivative (S32K14x LMEM); derivatives without it (e.g. S32K116) have no LMEM
    // NVM-23 (safety review NVM12-S4): region 2 of the cache (0x1000_0000..0x17FF_FFFF: FlexNVM and FlexRAM) non-
    // cacheable (PCCRMR R2 = 00b) before the cache is switched on. The reference manual asks for it on the S32K148
    // (reset value 10b, write-through); it is done on every derivative with the cache, so the data flash and the
    // emulated EEPROM, which the flash module changes behind the core, are never served from a cache line.
    LMEM->PCCRMR = (LMEM->PCCRMR & ~LMEM_PCCRMR_R2_MASK) | LMEM_PCCRMR_R2(0U);
    cacheEnable();
#endif
}

void setupApplicationsIsr(void)
{
#ifdef PLATFORM_SUPPORT_CAN
    // Message buffer interrupts of every used CAN bus (receive and transmit), level from the bus' interrupt rank
    // (project interrupts.ranks, source can -> NX_IRQ_LEVEL_CAN_<bus>).
    // PLAT-12: the vectors come from the generated interrupts/nxIrqNames.h, because a derivative either splits
    // them (S32K14x: buffers 0-15 and 16-31) or has one vector for all buffers (S32K11x: CAN0_ORed_0_31_MB).
    // ARCH-61: the buses come from the generated list NX_CAN_MB_IRQS as well, whatever the board calls them and
    // whichever FlexCAN instance they sit on; until then this block named the bus CAN0 and a board with other
    // names did not compile. A used bus without a rank is refused by nxconfig with its YAML path (and by bswgen,
    // same text) before this is compiled. CanSystem::run() sets the same levels again and enables a vector only
    // where the board port points it at the handler.
    // First all levels, then all enables (same order as before, so a S32K14x image stays byte identical).
#define NX_SET_CAN_MB_IRQ_LEVEL(bus, irq) SYS_SetPriority(irq, NX_IRQ_LEVEL_CAN_##bus);
#define NX_ENABLE_CAN_MB_IRQ(bus, irq)    SYS_EnableIRQ(irq);
    NX_CAN_MB_IRQS(NX_SET_CAN_MB_IRQ_LEVEL)
    NX_CAN_MB_IRQS(NX_ENABLE_CAN_MB_IRQ)
#undef NX_SET_CAN_MB_IRQ_LEVEL
#undef NX_ENABLE_CAN_MB_IRQ
#endif
    ENABLE_INTERRUPTS();
}
} // extern "C"

namespace safety
{
// SYS-37: the event box of the RTE deadline monitor (SafeSupervisor::rteTimingMonitor), written by the RTE tasks.
// It must lie outside the MPU protected safety RAM, which the linker script fills with every .bss of a libsafe*
// archive (.mpu_bss): this file belongs to the library "main", so the box lands in the general .bss (SRAM_U,
// cleared by the startup code) with write access for every task. The ELF test of
// ecu/safety/safeSupervisor/test/test_safety_context.py checks the placement.
SafetyEventMailbox rteTimingMailbox;
} // namespace safety

namespace platform
{
StaticBsp staticBsp;

StaticBsp& getStaticBsp() { return staticBsp; }

// platformLifecycleAdd(): module integration in src/integration/Integration.cpp
} // namespace platform

int main()
{
    // SYS-97: fill the free part of the interrupt stack (MSP) first, so that its high-water mark (isrstack::measure,
    // console "inject mspuse" of a test build) covers the whole start-up and every interrupt since - the board
    // counter-check of the budget tools/stackuse proves
    ::isrstack::fillBelowStackPointer();
    // Role early_init of the project's modules (e.g. SBC reset counter compensation, SYS-07).
    ::integration::earlyInit();
    ::safety::MemoryProtection::init();
    ::safety::resetloop::StartResult resetLoop{};
    {
        ::safety::SafetyShell const safetyShell;
        ::safety::safeSupervisorConstructor.construct();
        auto& safeSupervisor = safety::SafeSupervisor::getInstance();
        safeSupervisor.enterLimpHome();
        uint32_t const TIME_OUT_WD_FAST_TEST_US = 6000U;
        auto const isWatchdogTestSuccess
            = ::safety::bsp::Watchdog::executeFastTest(TIME_OUT_WD_FAST_TEST_US);
        // SYS-137: taken here, once per start - the resets of the fast test above keep the record, and every safe
        // console line of this start (the earliest: the hold of the next lines) comes after
        ::platform::safeconsole::Snapshot const safeConsoleBefore
            = ::platform::safeconsole::take(nxSafeConsoleRecord);
        if (isWatchdogTestSuccess)
        {
            printf("MCU watchdog fast tests successful\r\n");
            reportSafeConsoleOfStartBefore(safeConsoleBefore);
            safeSupervisor.leaveLimpHome();
        }
        else
        {
            reportSafeConsoleOfStartBefore(safeConsoleBefore);
            safeSupervisor.watchdogStartupCheckMonitor.trigger();
        }
        safeWatchdog.enableMcuWatchdog();
        // SYS-43 (F13, SSR-065): once per start, after the resets of the fast test and before anything of the BSP or
        // the application runs. At the limit this holds the safe state without a reset and does not return; the
        // watchdog is already enabled then, so the hold is supervised like the hold after the ROM check (it services
        // the watchdog in every round; if it stopped, the watchdog reset would find the count and hold again).
        resetLoop = safeSupervisor.startResetLoopLimit(
            ::safety::resetLoopRecord,
            resetLoopStart(),
            NX_MODULE_SAFESUPERVISOR_PARAM_RESET_LOOP_LIMIT,
            ::safety::resetloop::clearCyclesOf(
                NX_MODULE_SAFESUPERVISOR_PARAM_RESET_LOOP_CLEAR_MS, ::safety::SafetyManager::CYCLE_PERIOD_MS));
        // SYS-73 (SM-55): latent-fault test of the RAM ECC through the error injection module, once per start, before
        // anything of the BSP runs (no DMA, no interrupt) and after the reset-loop limit, so a failure (S1 + S2)
        // counts as a safety reset and a permanent fault ends in the held safe state after the configured number.
        auto const eccTest = ::safety::safe_memory::eccLatentTestAtStart();
        if (eccTest.context != 0U)
        {
            safeSupervisor.dmaEccMonitor.trigger(eccTest.context);
        }
        else
        {
            printf("RAM ECC latent test (EIM) passed, %" PRIu32 " channel(s)\r\n", eccTest.channels);
        }
    }
    // SYS-113: what the reset-loop limit found, for the fault memory (BspSystem hands it over once one runs)
    ::integration::setSafetyResetStart(resetLoop.counted, resetLoop.count, resetLoop.lastCause);
    // Hooks before the BSP initialization (e.g. board test "sbc failstart <n>").
    ::integration::preBspInit();
    ::platform::staticBsp.init();
#if NX_SAFE_SUPERVISOR_DEGRADED
    {
        // SYS-123: the degraded groups again, right after the BSP start - the PWM driver has just written the timer
        // from its configuration (its own group lock already forced a degraded group, ComplementaryPwm), this walk
        // covers every entry of the groups before anything of the application runs
        ::safety::SafetyShell const safetyShell;
        ::safety::SafeSupervisor::getInstance().applyDegradedGroups();
    }
#endif
    // SYS-84: the reset status of the start, not RCM_SRS now - after the watchdog fast test above that is always the
    // test's own watchdog reset (0x20), also after a power-on or the reset pin
    ::rcm::printStartLine();
    // SYS-43: one line after a start that followed safety resets (board check of the reset-loop limit)
    if (resetLoop.count != 0U)
    {
        printf("reset loop: %" PRIu32 " of %" PRIu32 " safety resets in a row, last cause 0x%" PRIx32
               "%s, cleared after %" PRIu32 " ms without one\r\n",
               resetLoop.count,
               static_cast<uint32_t>(NX_MODULE_SAFESUPERVISOR_PARAM_RESET_LOOP_LIMIT),
               resetLoop.lastCause,
               resetLoop.recordValid ? "" : " (record was not intact)",
               static_cast<uint32_t>(NX_MODULE_SAFESUPERVISOR_PARAM_RESET_LOOP_CLEAR_MS));
    }
    // PLAT-85: clock check against the clock plan (PLAT-72) - one line "clock: ok, core <Hz> Hz" or "clock: FAULT
    // ..."/"clock: WARNING ...". The clock driver (boardInit(), before the RAM initialization) gives up on a clock
    // that does not start and leaves the result in the SCG; this is the first place with a console. Only the report:
    // the reaction (safe state, no CAN start, event) belongs to the safety layer (SYS-50).
    (void)nxClockCheckReport(nullptr);
    // PLAT-20: report of a hard fault before the last reset(s), once
    (void)::hardfault::reportAndClearDump();
    app_main(); // entry point for the generic part
    return (1); // we never reach this point
}
