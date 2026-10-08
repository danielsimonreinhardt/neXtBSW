/* SYS-171 host test stub of mcu/mcu.h for MemoryProtectionHostTest.cpp: a register model of the NXP system MPU with
 * plain words, except WORD2: its reserved bits (17, 23 and the enables of the bus masters 4..7, which the S32K1 does
 * not implement - S32K1xx RM Rev. 13, 15.1.2 Table 15-3) either read back as written or read as 0
 * (test_mpu::reservedReadsZero), since the silicon's behaviour there is not documented. Layout and field macros follow
 * external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/S32K148_MPU.h; the MPU_RGD_WORD2 field masks
 * are here so that the static_assert of MemoryProtection.cpp against the device header is compiled as on the target,
 * the MPU_RGDAAC_M3 macros for the write of platform::StaticBsp::init() the test repeats.
 */
#pragma once

#include <cstdint>

#define MPU_RGD_COUNT    16u
#define MPU_RGDAAC_COUNT 16u

namespace test_mpu
{
/** Bits of RGDn_WORD2 that the S32K1 implements (RM Rev. 13, 15.4.7 and 15.1.2): fields of the bus masters 0..3. */
constexpr uint32_t WORD2_IMPLEMENTED = ~static_cast<uint32_t>((1U << 17U) | (1U << 23U) | 0xFF000000U);

extern bool reservedReadsZero;

/** RGDn_WORD2 with the reserved bits of the model. */
class Word2
{
public:
    Word2& operator=(uint32_t const written)
    {
        _value = written;
        return *this;
    }

    operator uint32_t() const { return reservedReadsZero ? (_value & WORD2_IMPLEMENTED) : _value; }

private:
    uint32_t _value = 0U;
};
} // namespace test_mpu

struct MpuRgdType
{
    uint32_t WORD0;
    uint32_t WORD1;
    test_mpu::Word2 WORD2;
    uint32_t WORD3;
};

struct MpuType
{
    uint32_t CESR;
    uint32_t EAR[5];
    uint32_t EDR[5];
    MpuRgdType RGD[MPU_RGD_COUNT];
    uint32_t RGDAAC[MPU_RGDAAC_COUNT];
};

extern MpuType mpuRegisters;

#define IP_MPU (&mpuRegisters)

/* Field macros of S32K148_MPU.h (identical on every S32K1 derivative, M1PE missing in the S32K116 header) */
#define MPU_CESR_VLD_MASK   (0x1U)
#define MPU_CESR_VLD_SHIFT  (0U)
#define MPU_CESR_VLD(x)     (((uint32_t)(((uint32_t)(x)) << MPU_CESR_VLD_SHIFT)) & MPU_CESR_VLD_MASK)
#define MPU_CESR_NRGD_MASK  (0xF00U)
#define MPU_CESR_NRGD_SHIFT (8U)
#define MPU_CESR_NRGD(x)    (((uint32_t)(((uint32_t)(x)) << MPU_CESR_NRGD_SHIFT)) & MPU_CESR_NRGD_MASK)

#define MPU_RGD_WORD2_M0UM_MASK (0x7U)
#define MPU_RGD_WORD2_M0SM_MASK (0x18U)
#define MPU_RGD_WORD2_M0PE_MASK (0x20U)
#define MPU_RGD_WORD2_M1UM_MASK (0x1C0U)
#define MPU_RGD_WORD2_M1SM_MASK (0x600U)
#define MPU_RGD_WORD2_M1PE_MASK (0x800U)
#define MPU_RGD_WORD2_M2UM_MASK (0x7000U)
#define MPU_RGD_WORD2_M2SM_MASK (0x18000U)
#define MPU_RGD_WORD2_M3UM_MASK (0x1C0000U)
#define MPU_RGD_WORD2_M3SM_MASK (0x600000U)
#define MPU_RGD_WORD2_M4WE_MASK (0x1000000U)
#define MPU_RGD_WORD2_M4RE_MASK (0x2000000U)
#define MPU_RGD_WORD2_M5WE_MASK (0x4000000U)
#define MPU_RGD_WORD2_M5RE_MASK (0x8000000U)
#define MPU_RGD_WORD2_M6WE_MASK (0x10000000U)
#define MPU_RGD_WORD2_M6RE_MASK (0x20000000U)
#define MPU_RGD_WORD2_M7WE_MASK (0x40000000U)
#define MPU_RGD_WORD2_M7RE_MASK (0x80000000U)

#define MPU_RGDAAC_M3UM_SHIFT (18U)
#define MPU_RGDAAC_M3UM_MASK  (0x1C0000U)
#define MPU_RGDAAC_M3UM(x)    (((uint32_t)(((uint32_t)(x)) << MPU_RGDAAC_M3UM_SHIFT)) & MPU_RGDAAC_M3UM_MASK)
#define MPU_RGDAAC_M3SM_SHIFT (21U)
#define MPU_RGDAAC_M3SM_MASK  (0x600000U)
#define MPU_RGDAAC_M3SM(x)    (((uint32_t)(((uint32_t)(x)) << MPU_RGDAAC_M3SM_SHIFT)) & MPU_RGDAAC_M3SM_MASK)
