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

#include <mcu/mcu.h>

// ARCH-16: region descriptors of this derivative, generated from mcudb by tools/bswgen. Included here and not
// only through mcu.h, because the board ports still carry their own copy of the family mcu.h (PLAT-23).
#include <mcu/derivativeFacts.h>

#include <cstddef>
#include <cstdint>

#if !defined(NX_MPU_REGIONS) || !defined(NX_MPU_CESR_NRGD)
#error "no MPU in the derivative database (mcudb mpu.regions); see generated mcu/derivativeFacts.h (ARCH-16)"
#endif

namespace safety
{
namespace mpu
{
// ARCH-16: number of region descriptors of this derivative, generated from the derivative database
// (mcudb mpu.regions; S32K144/142/146/116 8, S32K148 16). Accesses above the implemented descriptors end in a
// bus fault, so this must not be a constant of the board port: the S32K148 has twice as many (finding ARCH-09 B3).
static constexpr uint8_t SMPU_ENTRYS = NX_MPU_REGIONS;
// The device header may describe a larger derivative (the S32K144 builds with S32K148.h, MPU_RGD_COUNT 16), but
// never a smaller one - otherwise RGD[SMPU_ENTRYS - 1] would be outside the register struct.
static_assert(
    SMPU_ENTRYS <= MPU_RGD_COUNT,
    "the device header declares fewer MPU region descriptors than the derivative database (mcudb mpu.regions)");

enum class ReadAccess : uint8_t
{
    nR = 0,
    R  = 1
};

enum class WriteAccess : uint8_t
{
    nW = 0,
    W  = 1
};

enum class AccessSupervisorMode : uint8_t
{
    SM_RWX      = 0,
    SM_RX       = 1,
    SM_RW       = 2,
    SM_UserMode = 3
};

enum class AccessUserMode : uint8_t
{
    UM_nXnWnR = 0,
    UM_X      = 1,
    UM_W      = 2,
    UM_R      = 4,
    UM_XW     = UM_X | UM_W,
    UM_XR     = UM_X | UM_R,
    UM_RW     = UM_R | UM_W,
    UM_RWX    = UM_R | UM_W | UM_X
};

enum class ProcessIdentifier : uint8_t
{
    nPI = 0,
    PI  = 1
};

union CESR
{
    uint32_t R;

    struct
    {
        uint32_t VLD            : 1;
        uint32_t Reserved_CESR1 : 7;
        uint32_t NRGD           : 4;
        uint32_t NSP            : 4;
        uint32_t HRL            : 4;
        uint32_t Reserved_CESR0 : 7;
        uint32_t SPERR4         : 1;
        uint32_t SPERR3         : 1;
        uint32_t SPERR2         : 1;
        uint32_t SPERR1         : 1;
        uint32_t SPERR0         : 1;
    } B;
};

struct Descriptor
{
    uint32_t startAddress;
    uint32_t endAddress;

    union
    {
        uint32_t R;

        struct
        {
            uint32_t M0UM            : 3;
            uint32_t M0SM            : 2;
            uint32_t M0PE            : 1;
            uint32_t M1UM            : 3;
            uint32_t M1SM            : 2;
            uint32_t M1PE            : 1;
            uint32_t M2UM            : 3;
            uint32_t M2SM            : 2;
            uint32_t ReservedWord2_0 : 1;
            uint32_t M3UM            : 3;
            uint32_t M3SM            : 2;
            uint32_t ReservedWord2_1 : 1;
            uint32_t M4WE            : 1;
            uint32_t M4RE            : 1;
            uint32_t M5WE            : 1;
            uint32_t M5RE            : 1;
            uint32_t M6WE            : 1;
            uint32_t M6RE            : 1;
            uint32_t M7WE            : 1;
            uint32_t M7RE            : 1;
        } B;
    } word2;

    union
    {
        uint32_t R;

        struct
        {
            uint32_t PID           : 8;
            uint32_t PIDMASK       : 8;
            uint32_t ReservedWord3 : 15;
            uint32_t VLD           : 1;
        } B;
    } word3;
};

inline void globalDisable() { IP_MPU->CESR = MPU_CESR_VLD(0); }

inline void globalEnable() { IP_MPU->CESR = MPU_CESR_VLD(1); }

/**
 * Region descriptors the CESR[NRGD] encoding stands for, 0 for a code the hardware does not define.
 * S32K1xx Reference Manual Rev. 14 (09/2021), 15.4.2 CESR field NRGD "Number Of Region Descriptors":
 * 0000b = 8, 0001b = 12, 0010b = 16 region descriptors. The same table is in tools/bswgen (MPU_NRGD_CODES),
 * which generates NX_MPU_CESR_NRGD; the static_assert below keeps both in step.
 */
constexpr uint8_t regionsOfNrgd(uint32_t const nrgd)
{
    return (nrgd <= 2U) ? static_cast<uint8_t>(8U + (4U * nrgd)) : 0U;
}

static_assert(
    regionsOfNrgd(NX_MPU_CESR_NRGD) == SMPU_ENTRYS,
    "generated NX_MPU_CESR_NRGD does not encode NX_MPU_REGIONS (tools/bswgen MPU_NRGD_CODES)");

/** NRGD field of the control/error status register: how many regions the silicon reports. */
inline uint32_t reportedNrgd() { return (IP_MPU->CESR & MPU_CESR_NRGD_MASK) >> MPU_CESR_NRGD_SHIFT; }

/**
 * ARCH-16: the global enable bit CESR[VLD] alone. It used to demand NRGD == 0 as well, which is an 8-region
 * derivative: on the S32K148 (NRGD = 2) the MPU looked disabled in every cycle although it was running.
 */
inline bool isGlobalEnabled() { return ((IP_MPU->CESR & MPU_CESR_VLD_MASK) != 0U); }

/**
 * The silicon implements as many region descriptors as the derivative database promises. A mismatch means the
 * build was configured for the wrong derivative, so the descriptors above the reported ones do not exist.
 */
inline bool hasRegionsOfDerivative() { return (regionsOfNrgd(reportedNrgd()) == SMPU_ENTRYS); }

inline void setDescriptor(size_t const slot, Descriptor const& d)
{
    IP_MPU->RGD[slot].WORD0 = d.startAddress;
    IP_MPU->RGD[slot].WORD1 = d.endAddress;
    IP_MPU->RGD[slot].WORD2 = d.word2.R;
    IP_MPU->RGD[slot].WORD3 = d.word3.R;
}

template<uint8_t slot, uint8_t wordNumber, uint32_t word>
void setWordInDescriptor()
{
    static_assert(slot < SMPU_ENTRYS, "");
    if (wordNumber == 2U)
    {
        IP_MPU->RGD[slot].WORD2 = word;
    }
    else if (wordNumber == 3U)
    {
        IP_MPU->RGD[slot].WORD3 = word;
    }
    else
    {
        // intentionally left empty
    }
}

template<uint8_t wordNumber>
uint32_t getWordInDescriptor(uint8_t const slot)
{
    if (slot >= SMPU_ENTRYS)
    {
        return 0U;
    }
    switch (wordNumber)
    {
        case 0:
        {
            return IP_MPU->RGD[slot].WORD0;
        }
        case 1:
        {
            return IP_MPU->RGD[slot].WORD1;
        }
        case 2:
        {
            return IP_MPU->RGD[slot].WORD2;
        }
        case 3:
        {
            return IP_MPU->RGD[slot].WORD3;
        }
        default:
        {
            return 0U;
        }
    }
}
}; // namespace mpu

constexpr uint32_t mpuWord2(
    mpu::ReadAccess m7re,
    mpu::WriteAccess m7we,
    mpu::ReadAccess m6re,
    mpu::WriteAccess m6we,
    mpu::ReadAccess m5re,
    mpu::WriteAccess m5we,
    mpu::ReadAccess m4re,
    mpu::WriteAccess m4we,
    mpu::AccessSupervisorMode m3sm,
    mpu::AccessUserMode m3um,
    mpu::AccessSupervisorMode m2sm,
    mpu::AccessUserMode m2um,
    mpu::ProcessIdentifier m1pe,
    mpu::AccessSupervisorMode m1sm,
    mpu::AccessUserMode m1um,
    mpu::ProcessIdentifier m0pe,
    mpu::AccessSupervisorMode m0sm,
    mpu::AccessUserMode m0um)
{
    return (static_cast<uint32_t>(m7re) << 31) | (static_cast<uint32_t>(m7we) << 30)
           | (static_cast<uint32_t>(m6re) << 29) | (static_cast<uint32_t>(m6we) << 28)
           | (static_cast<uint32_t>(m5re) << 27) | (static_cast<uint32_t>(m5we) << 26)
           | (static_cast<uint32_t>(m4re) << 25) | (static_cast<uint32_t>(m4we) << 24)
           | (static_cast<uint32_t>(m3sm) << 21) | (static_cast<uint32_t>(m3um) << 18)
           | (static_cast<uint32_t>(m2sm) << 15) | (static_cast<uint32_t>(m2um) << 12)
           | (static_cast<uint32_t>(m1pe) << 11) | (static_cast<uint32_t>(m1sm) << 9)
           | (static_cast<uint32_t>(m1um) << 6) | (static_cast<uint32_t>(m0pe) << 5)
           | (static_cast<uint32_t>(m0sm) << 3) | (static_cast<uint32_t>(m0um));
}

constexpr uint32_t mpuWord3(uint32_t pid, uint32_t pidmask, uint32_t vld)
{
    return (static_cast<uint32_t>(pid) << 24) | (static_cast<uint32_t>(pidmask) << 16)
           | (static_cast<uint32_t>(vld));
}

} // namespace safety
