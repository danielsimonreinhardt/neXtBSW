/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "safeWatchdog/SafeWatchdog.h"

#include <safeSupervisor/SafeSupervisor.h>

#ifdef PLATFORM_SUPPORT_WATCHDOG
#include <safeSupervisionCheck/WatchdogControlCheck.h>
#include <watchdog/Watchdog.h>

namespace
{
// The warning interrupt walks the safe outputs table before the watchdog reset (SM-01); enableMcuWatchdog() and the
// configuration check use the same value.
constexpr bool WATCHDOG_INTERRUPT_ACTIVE = true;
} // namespace
#endif

namespace safety
{

SafeWatchdog::SafeWatchdog() : _serviceCounter(SERVICE_COUNTER_INIT) {}

void SafeWatchdog::init()
{
    // Variables will be initialized in first cyclic call
    _serviceCounter = SERVICE_COUNTER_INIT;
    enableMcuWatchdog();
}

void SafeWatchdog::cyclic()
{
    auto& safeSupervisor = SafeSupervisor::getInstance();
#ifdef PLATFORM_SUPPORT_WATCHDOG
    // SM-02. SYS-41 (finding F12 of doc/safety/fmea.md): the driver's check covers enable, clock and time-out only;
    // the family check adds the warning interrupt, window, prescaler and update fields. Context = deviating fields.
    uint32_t fields = supervision::checkWatchdogControl(WATCHDOG_INTERRUPT_ACTIVE);
    if (!bsp::Watchdog::checkWatchdogConfiguration(WATCHDOG_TIMEOUT_MS))
    {
        fields |= supervision::WATCHDOG_FIELD_DRIVER_CHECK;
    }
    safeSupervisor.safeWatchdogConfigMonitor.check(fields == 0U, fields);

    auto const currentServiceCounter = bsp::Watchdog::getWatchdogServiceCounter();
    // check if watchdog was not triggered more often than allowed
    if (currentServiceCounter > (_serviceCounter + 1U))
    {
        safeSupervisor.serviceWatchdogMonitor.trigger();
    }
    _serviceCounter = currentServiceCounter;
    bsp::Watchdog::serviceWatchdog();
#endif
    safeSupervisor.safeWatchdogSequenceMonitor.hit(SafeSupervisor::EnterLeaveSequence::LEAVE);
}

void SafeWatchdog::enableMcuWatchdog()
{
#ifdef PLATFORM_SUPPORT_WATCHDOG
    bsp::Watchdog::enableWatchdog(WATCHDOG_TIMEOUT_MS, WATCHDOG_INTERRUPT_ACTIVE);
#endif
}

} // namespace safety
