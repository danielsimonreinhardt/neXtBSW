/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "systems/BspSystem.h"

#include "bsp/pinIrq/PinIrqConfig.h"
#include "bsp/timer/SystemTimer.h"
#include "console/ConsoleContext.h"
#include "integration/SafeOffTakeBackReport.h"
#include "integration/SafetyResetReport.h"
#include "outputPwm/OutputPwm.h"

namespace systems
{

BspSystem::BspSystem(::async::ContextType const context, StaticBsp& staticBsp)
: ::lifecycle::SingleContextLifecycleComponent(context)
, _context(context)
, _staticBsp(staticBsp)
#if !defined(NX_CONSOLE) || NX_CONSOLE
, _analogTester()
#ifndef NX_PRODUCT_BUILD // SYS-162: no console "pwm" in the product image
, _outputPwmTester()
#endif
, _digitalInputTester()
, _outputTester()
// SYS-13: the testers run in the console context, not in the BSP task next to its 10 ms cycle (and the SBC
// watchdog trigger, overcurrent and gate driver supervision of the same task). They only call the BSP accessors
// (AnalogInput, DigitalInput, Output, OutputPwm, AdcAccess), which the RTE tasks call from their own contexts as
// well - so the console adds a caller, not a new kind of access. "hardfault" stays in the BSP task: it faults
// on purpose, and its board test (PLAT-20) was made from that task; its output is one line.
, _asyncCommandWrapper_for_analogTester(_analogTester, ::console::CONSOLE_CONTEXT)
#ifndef NX_PRODUCT_BUILD // SYS-162
, _asyncOutputPwmTester(_outputPwmTester, ::console::CONSOLE_CONTEXT)
#endif
, _asyncDigitalInputTester(_digitalInputTester, ::console::CONSOLE_CONTEXT)
, _asyncOutputTester(_outputTester, ::console::CONSOLE_CONTEXT)
, _adcAccessCommand()
, _asyncAdcAccessCommand(_adcAccessCommand, ::console::CONSOLE_CONTEXT)
// NVM-07: console context like the testers; "flexnvm partition" blocks the core for the PGMPART command anyway
, _flexNvmCommand()
, _asyncFlexNvmCommand(_flexNvmCommand, ::console::CONSOLE_CONTEXT)
#ifdef NX_FLEXNVM_PARTITION_COMMAND
// NVM-17: console context like "flexnvm"; its CSEc commands block the core with interrupts suspended anyway
, _csecCommand()
, _asyncCsecCommand(_csecCommand, ::console::CONSOLE_CONTEXT)
#endif
#ifdef NX_HARDFAULT_TEST_COMMAND
, _hardFaultTestCommand()
, _asyncHardFaultTestCommand(_hardFaultTestCommand, context)
#endif
#if NX_PIN_EVENT_COUNT > 0
, _pinEventCommand(::bsp::pinEvents())
, _asyncPinEventCommand(_pinEventCommand, ::console::CONSOLE_CONTEXT)
#endif
#endif // NX_CONSOLE
{}

void BspSystem::init() { transitionDone(); }

void BspSystem::run()
{
    ::async::scheduleAtFixedRate(_context, *this, _timeout, 10u, ::async::TimeUnit::MILLISECONDS);
    transitionDone();
}

void BspSystem::shutdown()
{
    _timeout.cancel();
    transitionDone();
}

void BspSystem::execute()
{
    cyclic();
    _staticBsp.cyclic();
}

uint8_t bspSystemCycleCount = 0;

// SYS-113: the cause of the safety reset before this start goes to the fault memory once the application registered
// it (recording only; nothing to do once handed over). SYS-196: the take-backs of foreign safe_off levels that the hold
// (same task) counted, as the DTC safety.safe_off_take_back (recording only, QM)
void BspSystem::cyclic()
{
    ::integration::reportSafetyResetStart();
    ::integration::reportSafeOffTakeBacks();
}

} // namespace systems
