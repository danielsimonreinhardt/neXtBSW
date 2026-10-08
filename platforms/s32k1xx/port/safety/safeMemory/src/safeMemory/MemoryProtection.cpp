/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "safeMemory/MemoryProtection.h"

#include "safeMemory/MemoryLocations.h"
#include "safeMemory/mpu.h"

#include <interrupts/SuspendResumeAllInterruptsScopedLock.h>

namespace safety
{
static constexpr uint32_t REGION_FULL_ACCESS = mpuWord2(
    mpu::ReadAccess::R,
    mpu::WriteAccess::W,
    mpu::ReadAccess::R,
    mpu::WriteAccess::W,
    mpu::ReadAccess::R,
    mpu::WriteAccess::W,
    mpu::ReadAccess::R,
    mpu::WriteAccess::W,
    mpu::AccessSupervisorMode::SM_RWX,
    mpu::AccessUserMode::UM_RWX,
    mpu::AccessSupervisorMode::SM_RWX,
    mpu::AccessUserMode::UM_RWX,
    mpu::ProcessIdentifier::nPI,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_RWX,
    mpu::ProcessIdentifier::nPI,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_RWX);
static constexpr uint32_t REGION_VALID              = mpuWord3(0, 0, 1);
static constexpr uint32_t REGION_LOCKED_READ_ACCESS = mpuWord2(
    mpu::ReadAccess::nR,
    mpu::WriteAccess::nW,
    mpu::ReadAccess::nR,
    mpu::WriteAccess::nW,
    mpu::ReadAccess::nR,
    mpu::WriteAccess::nW,
    mpu::ReadAccess::nR,
    mpu::WriteAccess::nW,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_nXnWnR,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_nXnWnR,
    mpu::ProcessIdentifier::nPI,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_R,
    mpu::ProcessIdentifier::nPI,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_R);

/**
 * SYS-171 (finding SFP-F02 of doc/safety/reviews/code-inspection/INS-SFP-1.md): access word of descriptor 0, the
 * debugger region over the whole address space. The core (M0) gets no access through it, the debugger (M1) keeps its
 * reset rights, M2 (DMA) and M3 (ENET, S32K148 only) keep rwx (finding SFP-F03): the SYSMPU grants an access when any
 * valid descriptor grants it, so while descriptor 0 is valid it overrides the "no access" of these masters in the
 * locked words below. No neXtBSW driver enables a bus master other than the core today (dfa.md DF-44, fmea.md N3).
 * init() writes this word and areRegionsConfiguredCorrectly() compares it every cycle.
 * M3 is written as "supervisor same as user, user rwx" (M3SM 11b, M3UM 111b), which is the same right as M3SM 00b
 * (rwx): platform::StaticBsp::init() sets exactly these bits through the alias RGDAAC0 after this init(), and the word
 * must read back unchanged afterwards. The M4..M7 bits are written as before; on the S32K1 they are reserved.
 */
static constexpr uint32_t REGION0_ACCESS = mpuWord2(
    mpu::ReadAccess::R,
    mpu::WriteAccess::W,
    mpu::ReadAccess::R,
    mpu::WriteAccess::W,
    mpu::ReadAccess::R,
    mpu::WriteAccess::W,
    mpu::ReadAccess::R,
    mpu::WriteAccess::W,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_RWX,
    mpu::AccessSupervisorMode::SM_RWX,
    mpu::AccessUserMode::UM_RWX,
    mpu::ProcessIdentifier::nPI,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_RWX,
    mpu::ProcessIdentifier::nPI,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_nXnWnR);

/**
 * SYS-171 (finding SFP-F04): every field of RGDn_WORD2 the S32K1 implements - user and supervisor mode of the bus
 * masters 0..3 and the process identifier enables M0PE/M1PE. The cyclic check compares this part of the word, so a
 * changed right of any bus master is found, not only of M0..M2 as with the former mask 0x0001FFFF. Left out: the
 * reserved bits 17 and 23 and the read/write enables of the masters 4..7, which the S32K1 does not implement (S32K1xx
 * RM Rev. 13, 15.1.2 Table 15-3: "fields for these master IDs are Reserved"); the device header still declares their
 * masks, but a written 1 there need not read back, and comparing it would raise a configuration error in every cycle.
 */
static constexpr uint32_t WORD2_FIELDS = mpuWord2(
    mpu::ReadAccess::nR,
    mpu::WriteAccess::nW,
    mpu::ReadAccess::nR,
    mpu::WriteAccess::nW,
    mpu::ReadAccess::nR,
    mpu::WriteAccess::nW,
    mpu::ReadAccess::nR,
    mpu::WriteAccess::nW,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_RWX,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_RWX,
    mpu::ProcessIdentifier::PI,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_RWX,
    mpu::ProcessIdentifier::PI,
    mpu::AccessSupervisorMode::SM_UserMode,
    mpu::AccessUserMode::UM_RWX);
static_assert(WORD2_FIELDS == 0x007DFFFFU, "RGDn_WORD2 fields of the bus masters 0..3");
#if defined(MPU_RGD_WORD2_M0UM_MASK)
// the field masks of the device header (M1PE is missing in the S32K116 header, where the bit is unused)
static_assert(
    (WORD2_FIELDS & ~0x800U)
        == (MPU_RGD_WORD2_M0UM_MASK | MPU_RGD_WORD2_M0SM_MASK | MPU_RGD_WORD2_M0PE_MASK | MPU_RGD_WORD2_M1UM_MASK
            | MPU_RGD_WORD2_M1SM_MASK | MPU_RGD_WORD2_M2UM_MASK | MPU_RGD_WORD2_M2SM_MASK | MPU_RGD_WORD2_M3UM_MASK
            | MPU_RGD_WORD2_M3SM_MASK),
    "WORD2_FIELDS differs from the MPU_RGD_WORD2 field masks of the device header");
#endif

// SYS-97: five regions of the safety part plus two for the guard below the interrupt stack (the guard itself and
// the rest of the full-access region it was cut out of)
static constexpr uint8_t numOfMemoryProtectionConfigurationRamRegions = 7;
static_assert(
    (numOfMemoryProtectionConfigurationRamRegions + 1U) <= mpu::SMPU_ENTRYS,
    "the MPU of this derivative has too few region descriptors for the safety regions and the stack guard");
// index of the guard in the configuration (descriptor GUARD_REGION = index + 1) and of the region after it
static constexpr uint8_t GUARD_CONFIG_INDEX = MemoryProtection::STACK_GUARD_REGION - 1U;
static constexpr uint8_t GUARD_TAIL_CONFIG_INDEX = GUARD_CONFIG_INDEX + 1U;
static_assert(GUARD_TAIL_CONFIG_INDEX < numOfMemoryProtectionConfigurationRamRegions, "");

// linker script asserts that .data and .rodata are empty
static mpu::Descriptor __attribute__((section(".bss")))
memoryProtectionConfigurationRam[numOfMemoryProtectionConfigurationRamRegions];
// SYS-97: false when the guard is not inside one full-access region (layout error); the region check then fails
static bool __attribute__((section(".bss"))) stackGuardPlaced;

/**
 * SYS-97 (finding STK-2 of doc/safety/sw-architecture.md): cut the guard below the interrupt stack out of the
 * full-access region that contains it. The SYSMPU grants an access when any descriptor grants it, so the guard is
 * protected only if no full-access descriptor covers it: the containing region ends below the guard, the guard gets
 * its own descriptor that lets the core read but not write (a hard fault may read the stack frame there), and a new
 * full-access descriptor covers the rest up to the old end. [guardStart, guardEnd) are the linker symbols, on the
 * 32-byte granularity (asserted by the linker script).
 */
static void placeStackGuard(uint32_t const guardStart, uint32_t const guardEnd)
{
    stackGuardPlaced = false;
    memoryProtectionConfigurationRam[GUARD_CONFIG_INDEX]      = {0U, 0U, {REGION_LOCKED_READ_ACCESS}, {0U}};
    memoryProtectionConfigurationRam[GUARD_TAIL_CONFIG_INDEX] = {0U, 0U, {REGION_FULL_ACCESS}, {0U}};
    if ((guardEnd <= guardStart) || (guardStart == 0U))
    {
        return;
    }
    uint32_t const guardLast = guardEnd - 1U;
    // the full-access regions of the safety layout: before the protected RAM, between it and the safety stack,
    // behind the safety stack (indices 0, 2, 4)
    static constexpr uint8_t FULL_ACCESS_REGIONS[] = {0U, 2U, 4U};
    for (uint8_t const i : FULL_ACCESS_REGIONS)
    {
        mpu::Descriptor& region = memoryProtectionConfigurationRam[i];
        if ((region.startAddress < guardStart) && (guardLast < region.endAddress))
        {
            uint32_t const tailEnd = region.endAddress;
            region.endAddress      = guardStart - 1U;
            memoryProtectionConfigurationRam[GUARD_CONFIG_INDEX]
                = {guardStart, guardLast, {REGION_LOCKED_READ_ACCESS}, {REGION_VALID}};
            memoryProtectionConfigurationRam[GUARD_TAIL_CONFIG_INDEX]
                = {guardLast + 1U, tailEnd, {REGION_FULL_ACCESS}, {REGION_VALID}};
            stackGuardPlaced = true;
            return;
        }
    }
}

void MemoryProtection::init()
{
    initWithStackGuard(
        static_cast<uint32_t>(reinterpret_cast<uintptr_t>(__StackGuardStart)),
        static_cast<uint32_t>(reinterpret_cast<uintptr_t>(__StackGuardEnd)));
}

void MemoryProtection::initWithStackGuard(uint32_t const guardStart, uint32_t const guardEnd)
{
    mpu::globalDisable();
    /* the cortex-m microcontroller has by default the Debugger Bus Master active in region 0 and
       this cannot be changed. Therefore this Region is configured with no Read/Write/Execute access
       for the Core Bus and implicitly deactivated.*/
    mpu::setWordInDescriptor<0U, RegionDescriptor_AccessControl, REGION0_ACCESS>();

    // clang-format off
    uint32_t const PROTECTED_RAM_START_ADDR_MINUS_1
        = reinterpret_cast<uintptr_t>(__MPU_BSS_START) - 1U;
    uint32_t const PROTECTED_RAM_END_ADDR = reinterpret_cast<uintptr_t>(__MPU_BSS_END) - 1U;
    uint32_t const SAFETY_TASK_START_ADDR = reinterpret_cast<uint32_t>(&safetyStack[0]);
    uint32_t const SAFETY_TASK_END_ADDR
        = SAFETY_TASK_START_ADDR + (static_cast<uint32_t>(safety_task_stackSize) - 1U);

    memoryProtectionConfigurationRam[0] =
        {0x0000000U,                            PROTECTED_RAM_START_ADDR_MINUS_1, {REGION_FULL_ACCESS},        {REGION_VALID}};
    memoryProtectionConfigurationRam[1] =
        {PROTECTED_RAM_START_ADDR_MINUS_1 + 1U, PROTECTED_RAM_END_ADDR,           {REGION_LOCKED_READ_ACCESS}, {REGION_VALID}};
    memoryProtectionConfigurationRam[2] =
        {PROTECTED_RAM_END_ADDR + 1U,           SAFETY_TASK_START_ADDR - 1U,      {REGION_FULL_ACCESS},        {REGION_VALID}};
    memoryProtectionConfigurationRam[3] =
        {SAFETY_TASK_START_ADDR,                SAFETY_TASK_END_ADDR,             {REGION_FULL_ACCESS},        {REGION_VALID}};
    memoryProtectionConfigurationRam[4] =
        {SAFETY_TASK_END_ADDR + 1U,             0xFFFFFFFFU,                      {REGION_FULL_ACCESS},        {REGION_VALID}};
    // clang-format on
    placeStackGuard(guardStart, guardEnd);

    for (size_t i = 0U; i < numOfMemoryProtectionConfigurationRamRegions; ++i)
    {
        mpu::setDescriptor(i + 1U, (mpu::Descriptor const&)memoryProtectionConfigurationRam[i]);
    }
    mpu::globalEnable();
}

void MemoryProtection::fusaGateOpen()
{
    mpu::setWordInDescriptor<FUSA_REGION, RegionDescriptor_AccessControl, REGION_FULL_ACCESS>();
    mpu::setWordInDescriptor<FUSA_REGION, RegionDescriptor_Validity, REGION_VALID>();
}

void MemoryProtection::fusaGateClose()
{
    mpu::setWordInDescriptor<
        FUSA_REGION,
        RegionDescriptor_AccessControl,
        REGION_LOCKED_READ_ACCESS>();
    mpu::setWordInDescriptor<FUSA_REGION, RegionDescriptor_Validity, REGION_VALID>();
}

bool MemoryProtection::fusaGateGetLockAndUnlock()
{
    // the lock is needed here (although it's also done before calling this function)
    // otherwise compiler will break the code when optimizing assembler and C++ code
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    bool const gateLocked = fusaGateIsLocked();
    mpu::setWordInDescriptor<FUSA_REGION, RegionDescriptor_AccessControl, REGION_FULL_ACCESS>();
    mpu::setWordInDescriptor<FUSA_REGION, RegionDescriptor_Validity, REGION_VALID>();
    return gateLocked;
}

void MemoryProtection::fusaGateRestoreLock(bool const gateLocked)
{
    if (gateLocked)
    {
        mpu::setWordInDescriptor<
            FUSA_REGION,
            RegionDescriptor_AccessControl,
            REGION_LOCKED_READ_ACCESS>();
    }
    else
    {
        mpu::setWordInDescriptor<FUSA_REGION, RegionDescriptor_AccessControl, REGION_FULL_ACCESS>();
    }
    mpu::setWordInDescriptor<FUSA_REGION, RegionDescriptor_Validity, REGION_VALID>();
}

bool MemoryProtection::fusaGateIsLocked()
{
    uint32_t const lockWord = mpu::getWordInDescriptor<RegionDescriptor_AccessControl>(FUSA_REGION);
    return (lockWord == REGION_LOCKED_READ_ACCESS);
}

void MemoryProtection::fusaTaskStackCloseIsr()
{
    mpu::setWordInDescriptor<
        SAFETY_STACK_REGION,
        RegionDescriptor_AccessControl,
        REGION_LOCKED_READ_ACCESS>();
    mpu::setWordInDescriptor<SAFETY_STACK_REGION, RegionDescriptor_Validity, REGION_VALID>();
}

void MemoryProtection::fusaTaskStackOpenIsr()
{
    mpu::setWordInDescriptor<
        SAFETY_STACK_REGION,
        RegionDescriptor_AccessControl,
        REGION_FULL_ACCESS>();
    mpu::setWordInDescriptor<SAFETY_STACK_REGION, RegionDescriptor_Validity, REGION_VALID>();
}

bool MemoryProtection::checkLockProtection(uint8_t const region)
{
    uint32_t const current_lock_word
        = mpu::getWordInDescriptor<RegionDescriptor_AccessControl>(region + 1);
    // SYS-171: every implemented field of the word (WORD2_FIELDS), not only those of M0..M2
    uint32_t const expected_lock_word = ((region + 1) == MemoryProtection::FUSA_REGION)
                                            // always checked in open state
                                            ? REGION_FULL_ACCESS
                                            : memoryProtectionConfigurationRam[region].word2.R;
    return ((current_lock_word & WORD2_FIELDS) == (expected_lock_word & WORD2_FIELDS));
}

bool MemoryProtection::checkDebuggerRegion()
{
    // SYS-171 (finding SFP-F02): descriptor 0 covers the whole address space; one changed bit of its M0 fields would
    // give the core write access everywhere (gate and stack guard void), so it is checked like the others
    uint32_t const word2 = mpu::getWordInDescriptor<RegionDescriptor_AccessControl>(0U);
    return ((word2 & WORD2_FIELDS) == (REGION0_ACCESS & WORD2_FIELDS));
}

bool MemoryProtection::checkAddresses(uint8_t const region)
{
    if (numOfMemoryProtectionConfigurationRamRegions <= region)
    {
        return false;
    }

    uint32_t const start_address
        = mpu::getWordInDescriptor<RegionDescriptor_StartAddress>(region + 1);
    uint32_t const end_address = mpu::getWordInDescriptor<RegionDescriptor_EndAddress>(region + 1);

    static constexpr uint32_t MASK_START_ADDRESS = 0xFFFFFFE0U;
    static constexpr uint32_t MASK_END_ADDRESS   = 0x0000001FU;

    uint32_t const config_start_address
        = (memoryProtectionConfigurationRam[region].startAddress & MASK_START_ADDRESS);
    uint32_t const config_end_address
        = (memoryProtectionConfigurationRam[region].endAddress | MASK_END_ADDRESS);

    if ((start_address != config_start_address) || (end_address != config_end_address))
    {
        return false;
    }
    return true;
}

bool MemoryProtection::checkValidity(uint8_t const region)
{
    uint32_t const validity = mpu::getWordInDescriptor<RegionDescriptor_Validity>(region + 1);
    if ((memoryProtectionConfigurationRam[region].word3.R & REGION_VALID)
        != (validity & REGION_VALID))
    {
        return false;
    }
    return true;
}

bool MemoryProtection::areRegionsConfiguredCorrectly(uint8_t& failed_region)
{
    // ARCH-16: region descriptors of this derivative (mcudb mpu.regions). On the S32K148 the loop below now also
    // checks RGD[8..15], which the fixed 8 of the board port left unchecked.
    constexpr uint8_t MAX_MPU_REGIONS = mpu::SMPU_ENTRYS;

    // SYS-97: a layout in which the guard could not be cut out of a full-access region has no overflow detection
    // for the interrupt stack - reported like a wrong descriptor
    if (!stackGuardPlaced)
    {
        failed_region = STACK_GUARD_REGION;
        return false;
    }
    // SYS-171: descriptor 0 (debugger region), reported as region 0
    if (!checkDebuggerRegion())
    {
        failed_region = 0U;
        return false;
    }
    for (uint8_t i = 0U; i < numOfMemoryProtectionConfigurationRamRegions; ++i)
    {
        if (!MemoryProtection::checkAddresses(i))
        {
            return false;
        }
        if (!MemoryProtection::checkLockProtection(i))
        {
            failed_region = i + 1;
            return false;
        }
        if (!MemoryProtection::checkValidity(i))
        {
            failed_region = i + 1;
            return false;
        }
    }
    for (uint8_t i = numOfMemoryProtectionConfigurationRamRegions + 1; i < MAX_MPU_REGIONS; ++i)
    {
        uint32_t const validity
            = (mpu::getWordInDescriptor<RegionDescriptor_Validity>(i) & REGION_VALID);
        if (validity != 0U)
        {
            return false;
        }
    }
    return true;
}
} // namespace safety
