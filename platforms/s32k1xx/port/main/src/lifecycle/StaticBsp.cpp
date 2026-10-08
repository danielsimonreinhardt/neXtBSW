/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "lifecycle/StaticBsp.h"

#include "SpiBusPins.h"
#include "bsp/SystemTime.h"
#include "flexnvm/FlexNvmEeprom.h"
#include "mcu/mcu.h"

// NVM-07: time limit of one EEPROM write from the partition of the project and the data sheet (tools/bswgen)
#include <nvm/nvmPartition.h>
#include <util/logger/Logger.h>

DECLARE_LOGGER_COMPONENT(BSP)

extern "C"
{
extern void initSystemTimerHelper(bool sleep);
// PLAT-67: restarts of the DWT cycle counter after a debugger switched it off (bspConfiguration SystemTimer.cpp)
extern uint32_t getSystemTimerCycleCounterRestarts(void);
}

namespace platform
{
namespace
{
// NVM-02: the FlexNVM EEPROM emulation as persistent memory of the NvM - only used if it already exists.
// NVM-07: each store is bounded in time (NX_NVM_WRITE_LIMIT_US, derived from mcudb eeprom_emulation.timing).
#ifdef NX_NVM_WRITE_LIMIT_US
FlexNvmEeprom flexNvmEeprom(NX_NVM_WRITE_LIMIT_US);
#else
FlexNvmEeprom flexNvmEeprom;
#endif

char nvmDetail[] = "DEPART 0x?, EEERAMSIZE 0x?";

// PLAT-67: restarts of the system time counter already reported in the log
uint32_t reportedCycleCounterRestarts = 0U;

char hexDigit(uint8_t const value)
{
    return static_cast<char>((value < 10U) ? ('0' + value) : ('A' + (value - 10U)));
}
} // namespace

/** NVM-07: the EEPROM driver for the console command "flexnvm show" (systems/FlexNvmCommand.cpp). */
FlexNvmEeprom& flexNvmEepromInstance() { return flexNvmEeprom; }

/**
 * NVM-02: persistent memory of the NvM (declared in ecu/application/src/systems/RteSystem.cpp, weak default there).
 * Reports the FlexNVM partition as it is (SIM FCFG1) and hands the emulated EEPROM out only if it is partitioned
 * and loaded. No flash command is launched: the partitioning (PGMPART) is not part of the start-up.
 */
::eeprom::IEepromDriver* nvmEeprom(
    uint32_t const windowStart,
    uint32_t const windowSize,
    uint32_t& usableBytes,
    char const*& state,
    char const*& detail)
{
    flexNvmEeprom.setWindow(windowStart, windowSize);
    FlexNvmEeprom::State const s = flexNvmEeprom.state();
    nvmDetail[9]                 = hexDigit(FlexNvmEeprom::departCode());
    nvmDetail[25]                = hexDigit(FlexNvmEeprom::eeeSizeCode());
    state                        = FlexNvmEeprom::stateText(s);
    detail                       = nvmDetail;
    usableBytes                  = flexNvmEeprom.usableBytes();
    return (s == FlexNvmEeprom::State::READY) ? &flexNvmEeprom : nullptr;
}
} // namespace platform

StaticBsp::StaticBsp() : fCyclic10Msec(0U), _output(), _digitalInput(), _adc() { initSystemTimer(); }

void StaticBsp::init() { hwInit(); }

void StaticBsp::hwInit()
{
    initSystemTimerHelper(false);
    // ARCH-38: the pins of every SPI bus of the project (the SPI master takes none), before any system starts a bus
    ::platform::muxSpiBusPins();
    _output.init(0);
    _digitalInput.init(0);
#if NX_PWM_GROUP_COUNT > 0
    // IO-02: pairs of the PWM group (eval board MOT1 on FTM0), outputs masked until the first duty is set
#define STATIC_BSP_PWM_INIT(group) _pwm##group.init();
    NX_PWM_GROUP_INSTANCES(STATIC_BSP_PWM_INIT)
#undef STATIC_BSP_PWM_INIT
#endif

    sysDelayUs(150U);

    _adc.init();
    _adc.start();

    IP_MPU->RGDAAC[0] = IP_MPU->RGDAAC[0]
                        | (MPU_RGDAAC_M3SM(3) /* 11b - Same as User mode defined in M3UM */
                           | MPU_RGDAAC_M3UM(7) /* 111b - r/w/x */);
}

void StaticBsp::shutdown() {}

void StaticBsp::cyclic()
{
    _adc.cyclic();

    // PLAT-67: one line per debugger disconnect that stopped the DWT cycle counter (the system time restarted it)
    uint32_t const restarts = getSystemTimerCycleCounterRestarts();
    if (restarts != ::platform::reportedCycleCounterRestarts)
    {
        ::platform::reportedCycleCounterRestarts = restarts;
        ::util::logger::Logger::warn(
            ::util::logger::BSP,
            "system time: DWT stopped by debugger (DEMCR.TRCENA 0), restarted, count %u",
            static_cast<unsigned>(restarts));
    }

    fCyclic10Msec++;
    if (fCyclic10Msec >= 10)
    {
        fCyclic10Msec = 0;
    }
}
