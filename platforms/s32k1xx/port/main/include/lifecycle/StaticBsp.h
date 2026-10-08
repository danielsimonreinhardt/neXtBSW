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

#include "bsp/Bsp.h"
#include "bsp/adc/AnalogInput.h"
// ARCH-17: complementary PWM only if the project uses a PWM group. The generated header says how many groups
// there are (NX_PWM_GROUP_COUNT) and names them (NX_PWM_GROUP_INSTANCES); a board without a motor bridge has
// none, and then neither the driver nor its FTM configuration is part of this port.
#include "bsp/io/outputPwm/ftmPwmConfiguration.h"
#if NX_PWM_GROUP_COUNT > 0
#include "bsp/io/outputPwm/ComplementaryPwm.h"
#endif
#include "bsp/power/IEcuPowerStateController.h"
#include "inputManager/DigitalInput.h"
#include "mcu/mcu.h"
#include "outputManager/Output.h"

#include <cstdint>

class StaticBsp
{
public:
    StaticBsp();

    bios::IEcuPowerStateController& getPowerStateController() { return fPowerStateController; }

    bios::AnalogInput& getAdc() { return _adc; }

    void init();
    void shutdown();

    void cyclic();

private:
    void hwInit();

    class DummyEcuPowerStateController : public bios::IEcuPowerStateController
    {
    public:
        void startPreSleep() override {}

        uint32_t powerDown(uint8_t /* mode */, tCheckWakeupDelegate /* delegate */) override
        {
            return 0;
        }

        uint32_t powerDown(uint8_t /* mode */) override { return 0; }

        void fullPowerUp() override {}

        void setWakeupSourceMonitoring(
            uint32_t /* source */, bool /* active */ = true, bool /* fallingEdge */ = true) override
        {}

        void clearWakeupSourceMonitoring(uint32_t /* source */) override {}

        bool setWakeupDelegate(tCheckWakeupDelegate& /* delegate */) override { return true; }

        uint32_t getWakeupSource(void) override { return 0; }
    };

    DummyEcuPowerStateController fPowerStateController;

    uint8_t fCyclic10Msec;

    bios::Output _output;
    bios::DigitalInput _digitalInput;
    bios::AnalogInput _adc;
#if NX_PWM_GROUP_COUNT > 0
    // One driver per PWM group of the project (generated instance list, like the module instances in
    // integration/moduleConfiguration.h). Without a group there is no element at all (ARCH-17).
#define STATIC_BSP_PWM_MEMBER(group) bios::ComplementaryPwm _pwm##group;
    NX_PWM_GROUP_INSTANCES(STATIC_BSP_PWM_MEMBER)
#undef STATIC_BSP_PWM_MEMBER
#endif
};
