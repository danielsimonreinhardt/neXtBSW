/* Host test stub of mcu/mcu.h (ARCH-16): a register model of the NXP system MPU.
 *
 * MPU is a plain struct here, so the test can put any CESR (VLD, NRGD) and any region descriptor in front of
 * safeMemory/mpu.h and read back what the header wrote. The names, widths and reset values follow
 * external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/S32K148_MPU.h; MPU_RGD_COUNT is 16 there,
 * which is also the largest count the family has, so the same model serves an 8 and a 16 region derivative.
 *
 * The generated mcu/derivativeFacts.h (NX_MPU_REGIONS, NX_MPU_CESR_NRGD) is not stubbed and not included here:
 * safeMemory/mpu.h includes it itself, and the test compiles against the file tools/bswgen really generates.
 */

#pragma once

#include <cstdint>

#define MPU_RGD_COUNT    16u
#define MPU_RGDAAC_COUNT 16u

struct MpuRgdType
{
    uint32_t WORD0;
    uint32_t WORD1;
    uint32_t WORD2;
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

/* Field macros of S32K148_MPU.h (identical on every S32K1 derivative) */
#define MPU_CESR_VLD_MASK    (0x1U)
#define MPU_CESR_VLD_SHIFT   (0U)
#define MPU_CESR_VLD(x)      (((uint32_t)(((uint32_t)(x)) << MPU_CESR_VLD_SHIFT)) & MPU_CESR_VLD_MASK)
#define MPU_CESR_NRGD_MASK   (0xF00U)
#define MPU_CESR_NRGD_SHIFT  (8U)
#define MPU_CESR_NRGD(x)     (((uint32_t)(((uint32_t)(x)) << MPU_CESR_NRGD_SHIFT)) & MPU_CESR_NRGD_MASK)
#define MPU_CESR_NSP_MASK    (0xF000U)
#define MPU_CESR_NSP_SHIFT   (12U)
#define MPU_CESR_NSP(x)      (((uint32_t)(((uint32_t)(x)) << MPU_CESR_NSP_SHIFT)) & MPU_CESR_NSP_MASK)
#define MPU_CESR_HRL_MASK    (0xF0000U)
#define MPU_CESR_HRL_SHIFT   (16U)
#define MPU_CESR_HRL(x)      (((uint32_t)(((uint32_t)(x)) << MPU_CESR_HRL_SHIFT)) & MPU_CESR_HRL_MASK)
