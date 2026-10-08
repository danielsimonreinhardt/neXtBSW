/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "safeMemory/SafeMemory.h"

#include "safeMemory/IntegrityEccHandler.h"

#include <safeMemory/MemoryProtection.h>
#include <safeMemory/mpu.h>
#include <safeSupervisor/SafeSupervisor.h>
#include <safeUtils/SafetyLogger.h>

namespace safety
{
using ::util::logger::Logger;
using ::util::logger::SAFETY;

namespace safe_memory
{
void cyclic()
{
    // ARCH-16 (finding ARCH-09 B3): the MPU is usable when it is switched on (CESR[VLD]) and reports as many
    // region descriptors as the derivative database promises (mcudb mpu.regions -> NX_MPU_REGIONS). Both used to
    // be one comparison against NRGD == 0, so on a derivative with 16 regions (S32K148) the check failed in every
    // cycle and the safe supervisor went to limp home although the MPU was working. The monitor context is the
    // whole CESR, which holds VLD and NRGD, so the log tells the two cases apart.
    if (!mpu::isGlobalEnabled() || !mpu::hasRegionsOfDerivative())
    {
        auto& supervisor = safety::SafeSupervisor::getInstance();
        supervisor.mpuEnableMonitor.trigger(static_cast<uint32_t>(IP_MPU->CESR));
    }
    uint8_t failed_region = 0U;
    if (!MemoryProtection::areRegionsConfiguredCorrectly(failed_region))
    {
        auto& supervisor = safety::SafeSupervisor::getInstance();
        supervisor.mpuConfigMonitor.trigger(failed_region);
    }
    // SM-15/SM-16. SYS-41 (finding F11): the flags are taken - read and cleared - before the report, so the reset
    // of the reaction starts clean whether or not a reset clears the ERM/FTFC status, and a later error sets them
    // again instead of hiding behind the first. The context names the sources (RAM channel 0/1, flash).
    EccErrors const ecc = takeEccErrors();
    if (ecc.sources != 0U)
    {
        if ((ecc.sources & (ECC_SOURCE_RAM_CHANNEL_0 | ECC_SOURCE_RAM_CHANNEL_1)) != 0U)
        {
            Logger::debug(SAFETY, "ECC_RAM_DOUBLE_BIT_ERROR at address 0x%08X", ecc.ramAddress);
        }
        if ((ecc.sources & ECC_SOURCE_FLASH) != 0U)
        {
            Logger::debug(SAFETY, "ECC_FLASH_DOUBLE_BIT_ERROR");
        }
        auto& supervisor = safety::SafeSupervisor::getInstance();
        supervisor.dmaEccMonitor.trigger(ecc.sources);
    }
}

void checkEccErrors()
{
    auto const hasRamDoubleBitError = checkRamDoubleBitError();
    if (hasRamDoubleBitError)
    {
        auto errorAddress = readErmMemoryErrorAddress();
        Logger::debug(SAFETY, "ECC_RAM_DOUBLE_BIT_ERROR at address 0x%08X", errorAddress);
    }
    // TODO: remove the log, and replace it with writing the event to no-init ram
}

} // namespace safe_memory
} // namespace safety
