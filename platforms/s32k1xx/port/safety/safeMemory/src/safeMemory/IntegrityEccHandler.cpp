/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "safeMemory/IntegrityEccHandler.h"

#include <mcu/mcu.h>

namespace safety
{
namespace safe_memory
{

// PLAT-12: ERM memory channels of the derivative. The S32K14x/S32K148 monitor two channels (SRAM_L and SRAM_U
// as NCE0/NCE1, EARn[0..1]), the S32K11x only one ([RM] ERM chapter, "number of ERM channels"; the finding of
// the symbol comparison in PLAT-11). Both the status masks and the length of the EARn array are macros of the
// device header, so the derivative decides which channels are read - nothing is written twice.
#ifdef ERM_SR0_NCE1_MASK
#define NX_ERM_NONCORRECTABLE_MASK (ERM_SR0_NCE0_MASK | ERM_SR0_NCE1_MASK)
#else
#define NX_ERM_NONCORRECTABLE_MASK (ERM_SR0_NCE0_MASK)
#endif
#ifdef ERM_SR0_SBC1_MASK
#define NX_ERM_CORRECTION_MASK (ERM_SR0_SBC0_MASK | ERM_SR0_SBC1_MASK)
#else
#define NX_ERM_CORRECTION_MASK (ERM_SR0_SBC0_MASK)
#endif

namespace
{
// SYS-41: single-bit corrections seen since start (diagnostic, no reaction). Written by the safety task only.
uint32_t eccCorrections = 0U;
} // namespace

bool checkRamDoubleBitError()
{
    if (0U != (ERM->SR0 & NX_ERM_NONCORRECTABLE_MASK))
    {
        return true;
    }
    else
    {
        return false;
    }
}

bool checkFlashDoubleBitError()
{
    if (0U != (FTFC->FERSTAT & FTFC_FERSTAT_DFDIF_MASK))
    {
        return true;
    }
    else
    {
        return false;
    }
}

uint32_t readErmMemoryErrorAddress()
{
#if ERM_EARn_COUNT > 1
    return ERM->EARn[0].EAR != 0 ? ERM->EARn[0].EAR : ERM->EARn[1].EAR;
#else
    return ERM->EARn[0].EAR;
#endif
}

EccErrors takeEccErrors()
{
    EccErrors errors{0U, 0U, 0U};
    uint32_t const erm = ERM->SR0 & (NX_ERM_NONCORRECTABLE_MASK | NX_ERM_CORRECTION_MASK);
    if ((erm & ERM_SR0_NCE0_MASK) != 0U)
    {
        errors.sources |= ECC_SOURCE_RAM_CHANNEL_0;
        errors.ramAddress = ERM->EARn[0].EAR;
    }
#ifdef ERM_SR0_NCE1_MASK
    if ((erm & ERM_SR0_NCE1_MASK) != 0U)
    {
        errors.sources |= ECC_SOURCE_RAM_CHANNEL_1;
        if (errors.ramAddress == 0U)
        {
            errors.ramAddress = ERM->EARn[1].EAR;
        }
    }
#endif
    if ((erm & ERM_SR0_SBC0_MASK) != 0U)
    {
        ++errors.corrections;
    }
#ifdef ERM_SR0_SBC1_MASK
    if ((erm & ERM_SR0_SBC1_MASK) != 0U)
    {
        ++errors.corrections;
    }
#endif
    if (erm != 0U)
    {
        // write one to clear: only the flags read above, so an event recorded after the read is not lost
        ERM->SR0 = erm;
    }
    if (checkFlashDoubleBitError())
    {
        errors.sources |= ECC_SOURCE_FLASH;
        FTFC->FERSTAT = FTFC_FERSTAT_DFDIF_MASK; // write one to clear; the other bits are not writable
    }
    eccCorrections += errors.corrections;
    return errors;
}

uint32_t eccCorrectionCount() { return eccCorrections; }

} // namespace safe_memory
} // namespace safety
