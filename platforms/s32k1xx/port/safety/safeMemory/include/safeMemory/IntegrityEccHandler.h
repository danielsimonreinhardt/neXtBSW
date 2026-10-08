/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#pragma once

#include <cstdint>

namespace safety
{
namespace safe_memory
{
bool checkRamDoubleBitError();
bool checkFlashDoubleBitError();
uint32_t readErmMemoryErrorAddress();

/* SYS-41 (finding F11 of doc/safety/fmea.md, SM-15/SM-16). Reference manual S32K1xx Rev. 13: the ERM records a
 * non-correctable or single-bit event in SR0 (and the address in EARn) whether or not the interrupt enables in CR0
 * are set - CR0 only adds the interrupt notification ([RM] 22.4.1, 22.4.2). SR0 and FTFC FERSTAT[DFDIF] are
 * write-1-to-clear ([RM] 22.3.3, 36.4.4.1.11) and hold only the last event. Before SYS-41 nothing cleared them: the
 * outcome after the reset depended on whether that reset clears them (reset loop on a stale flag, or a second
 * error hidden behind the first). Now the poll takes the flags: it reads them, keeps the address, and clears
 * exactly the flags it read, before anything reacts. */

/** Sources of a non-correctable ECC error (context of SafeSupervisor::dmaEccMonitor). */
enum EccSource : uint32_t
{
    ECC_SOURCE_RAM_CHANNEL_0 = 0x1U, ///< ERM channel 0 (S32K14x SRAM_L, S32K11x SRAM_U)
    ECC_SOURCE_RAM_CHANNEL_1 = 0x2U, ///< ERM channel 1 (S32K14x SRAM_U; not present on the S32K11x)
    ECC_SOURCE_FLASH         = 0x4U, ///< FTFC double-bit fault on a program or data flash read
    /// SYS-73 (SM-55): the start-up latent-fault test of the RAM ECC failed; failure bits in 17..8, channel in
    /// 27..24 (safeMemory/EccLatentTest.h)
    ECC_SOURCE_LATENT_TEST = 0x8U,
};

struct EccErrors
{
    uint32_t sources;      ///< EccSource bits of the non-correctable errors taken (0 = none)
    uint32_t ramAddress;   ///< EARn of the first RAM channel with a non-correctable error (0 = no RAM error)
    uint32_t corrections;  ///< ERM channels that reported a single-bit correction since the last poll
};

/**
 * Reads the non-correctable and single-bit flags of the ERM and the double-bit flag of the flash controller, keeps
 * the address of a RAM error and clears exactly the flags that were read (write one to clear) - a flag set after the
 * read stays for the next poll. Safety context only (the ERM is accessible in supervisor mode only).
 */
EccErrors takeEccErrors();

/** Single-bit corrections taken by takeEccErrors() since start (count of polls with a correction per channel). */
uint32_t eccCorrectionCount();
} // namespace safe_memory
} // namespace safety
