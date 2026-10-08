/* SYS-42 host test stub of mcu/mcu.h for SafetyGateHostTest.cpp: a register model of the NXP system MPU in which
 * every word of a region descriptor can be given a fault - bits stuck at 1 or 0 (seen on read) and writes that are
 * lost. The layout and the field macros follow
 * external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/S32K148_MPU.h (as the model of MpuHostTest.cpp,
 * safeMemory/test/host/stubs/mcu/mcu.h, which has plain words and no faults). */
#pragma once

#include <cstdint>

#define MPU_RGD_COUNT    16u
#define MPU_RGDAAC_COUNT 16u

namespace test_mpu
{
/** One word of a region descriptor, with its writes counted and injectable faults. */
class Word
{
public:
    Word() = default;

    Word(Word const&)            = delete;
    Word& operator=(Word const&) = delete;

    Word& operator=(uint32_t const written)
    {
        ++writes;
        if (!dropWrites)
        {
            _value = written;
        }
        return *this;
    }

    operator uint32_t() const { return (_value | stuckAtOne) & ~stuckAtZero; }

    /** Reset of the register and of its faults. */
    void reset()
    {
        _value      = 0U;
        writes      = 0;
        stuckAtOne  = 0U;
        stuckAtZero = 0U;
        dropWrites  = false;
    }

    uint32_t stuckAtOne  = 0U;
    uint32_t stuckAtZero = 0U;
    bool dropWrites      = false;
    int writes           = 0;

private:
    uint32_t _value = 0U;
};
} // namespace test_mpu

struct MpuRgdType
{
    test_mpu::Word WORD0;
    test_mpu::Word WORD1;
    test_mpu::Word WORD2;
    test_mpu::Word WORD3;
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
#define MPU_CESR_VLD_MASK   (0x1U)
#define MPU_CESR_VLD_SHIFT  (0U)
#define MPU_CESR_VLD(x)     (((uint32_t)(((uint32_t)(x)) << MPU_CESR_VLD_SHIFT)) & MPU_CESR_VLD_MASK)
#define MPU_CESR_NRGD_MASK  (0xF00U)
#define MPU_CESR_NRGD_SHIFT (8U)
#define MPU_CESR_NRGD(x)    (((uint32_t)(((uint32_t)(x)) << MPU_CESR_NRGD_SHIFT)) & MPU_CESR_NRGD_MASK)
