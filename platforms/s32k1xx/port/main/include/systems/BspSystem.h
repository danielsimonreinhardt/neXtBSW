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

#include "bsp/adc/AdcAccessCommand.h"
#include "bsp/pinEvent/pinEventConfiguration.h"
#include "console/AsyncCommandWrapper.h"
#include "inputManager/DigitalInputTester.h"
#include "lifecycle/SingleContextLifecycleComponent.h"
#include "lifecycle/StaticBsp.h"
#include "outputManager/OutputTester.h"
// SYS-150: "out" refuses the safe_off outputs (out set past their drivers, SR-147-2 a)
#include "systems/SafeOffOutputTester.h"
#ifndef NX_PRODUCT_BUILD // SYS-162 (SR-110-1 c): no console "pwm" in the product image
#include "outputPwm/OutputPwmTester.h"
// PLAT-110: "pwm" set/setPeriod only for a complete line and a channel in range (SR-150-1 b); SYS-162: never for a
// channel of a PWM group of the safe outputs table (SR-110-1 b)
#include "systems/CheckedOutputPwmTester.h"
#endif
#include "systems/FlexNvmCommand.h"
#include "tester/AnalogTester.h"
#ifdef NX_HARDFAULT_TEST_COMMAND
#include "systems/HardFaultTestCommand.h"
#endif
#ifdef NX_FLEXNVM_PARTITION_COMMAND
#include "systems/CsecCommand.h"
#endif
#if NX_PIN_EVENT_COUNT > 0
#include "pinEvent/PinEventCommand.h"
#endif

namespace systems
{

class BspSystem
: public ::lifecycle::SingleContextLifecycleComponent
, private ::async::RunnableType
{
public:
    BspSystem(::async::ContextType context, StaticBsp& staticBsp);

    void init() override;
    void run() override;
    void shutdown() override;

    void execute() override;

    void cyclic();

private:
    ::async::ContextType const _context;
    ::async::TimeoutType _timeout;
    StaticBsp& _staticBsp;
#if !defined(NX_CONSOLE) || NX_CONSOLE
    // PLAT-106: the console commands (testers) only in an image with console (project.yaml console.enabled,
    // NX_CONSOLE): a constructed AsyncCommandWrapper registers itself, so its command is linked even if nothing
    // could ever run it
    bios::AnalogTester _analogTester;
#ifndef NX_PRODUCT_BUILD
    /* Console "pwm" (OpenBSW): walks the generated OutputPwm channels. It stays in every development image, also
     * in one without a PWM group (ARCH-17): the channel table then holds only OpenBSW's static dummy channel, so
     * "pwm all" lists that one and "pwm set"/"pwm setPeriod" answer through OutputPwm's dummy branch. No
     * channel of a missing group can be addressed, so the command reports instead of reaching a driver that
     * does not exist. SYS-162: the channels of the PWM groups of the safe outputs table are refused (their duties
     * belong to the function that drives the group), and the product image has no "pwm" at all (SR-110-1 b+c). */
    CheckedOutputPwmTester _outputPwmTester; // PLAT-110: bios::OutputPwmTester with complete argument checks
#endif
    bios::DigitalInputTester _digitalInputTester;
    SafeOffOutputTester _outputTester; // SYS-150: bios::OutputTester without "set" for safe_off outputs
    ::console::AsyncCommandWrapper _asyncCommandWrapper_for_analogTester;
#ifndef NX_PRODUCT_BUILD // SYS-162
    ::console::AsyncCommandWrapper _asyncOutputPwmTester;
#endif
    ::console::AsyncCommandWrapper _asyncDigitalInputTester;
    ::console::AsyncCommandWrapper _asyncOutputTester;
    bios::AdcAccessCommand _adcAccessCommand;
    ::console::AsyncCommandWrapper _asyncAdcAccessCommand;
    // NVM-07: "flexnvm show" (and "flexnvm partition" in test builds with NX_FLEXNVM_PARTITION_COMMAND)
    FlexNvmCommand _flexNvmCommand;
    ::console::AsyncCommandWrapper _asyncFlexNvmCommand;
#ifdef NX_FLEXNVM_PARTITION_COMMAND
    // NVM-17: "csec" (CSEc way back to the factory state) - test builds with NX_FLEXNVM_PARTITION_COMMAND only
    CsecCommand _csecCommand;
    ::console::AsyncCommandWrapper _asyncCsecCommand;
#endif
#ifdef NX_HARDFAULT_TEST_COMMAND
    HardFaultTestCommand _hardFaultTestCommand;
    ::console::AsyncCommandWrapper _asyncHardFaultTestCommand;
#endif
#if NX_PIN_EVENT_COUNT > 0
    // IO-54: "pinevent show|config" of project pin_events; read only and lock free, so in the console context
    ::pinevent::PinEventCommand _pinEventCommand;
    ::console::AsyncCommandWrapper _asyncPinEventCommand;
#endif
#endif // NX_CONSOLE
};

} // namespace systems
