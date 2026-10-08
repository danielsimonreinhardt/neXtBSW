/* Host test of safety::mpu (ARCH-16), no target hardware needed.
 *
 * safeMemory/mpu.h is compiled against a register model of the NXP system MPU (stubs/mcu/mcu.h) and against the
 * mcu/derivativeFacts.h that tools/bswgen really generates for a project. The test driver
 * tools/bswgen/test_bswgen_mpu.py runs it twice: once for a derivative with 8 region descriptors (eval board
 * S32K144) and once for one with 16 (build test S32K148). NX_MPU_REGIONS says which one is expected, so the same
 * source covers both.
 *
 * Finding ARCH-09 B3: isGlobalEnabled() used to compare CESR against MPU_CESR_NRGD(0) | MPU_CESR_VLD(1), i.e. it
 * demanded an 8-region derivative. The S32K148 reports NRGD = 2, so the enabled MPU looked disabled, the safe
 * supervisor was triggered in every safety cycle and the ECU would have gone to limp home - a silent error, the
 * build was warning free. The check OLD_CHECK below reproduces the old expression, so the regression stays visible.
 *
 * Build and run (from the repository root, <gen> is a bswgen output directory):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -I<gen> -Iplatforms/s32k1xx/port/safety/safeMemory/include \
 *       -Iplatforms/s32k1xx/port/safety/safeMemory/test/host/stubs \
 *       platforms/s32k1xx/port/safety/safeMemory/test/host/MpuHostTest.cpp -o build/host/mpu_test.exe
 *   build/host/mpu_test.exe
 */

#include "safeMemory/mpu.h"

#include <cstdio>

MpuType mpuRegisters{};

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

using ::safety::mpu::hasRegionsOfDerivative;
using ::safety::mpu::isGlobalEnabled;
using ::safety::mpu::regionsOfNrgd;
using ::safety::mpu::SMPU_ENTRYS;

/** CESR as the silicon presents it: hardware revision, slave ports, region code and the enable bit. */
uint32_t cesr(uint32_t const nrgd, uint32_t const slavePorts, bool const enabled)
{
    return MPU_CESR_HRL(1U) | MPU_CESR_NSP(slavePorts) | MPU_CESR_NRGD(nrgd)
           | MPU_CESR_VLD(enabled ? 1U : 0U);
}

/** The comparison isGlobalEnabled() used before ARCH-16 (finding ARCH-09 B3). */
bool oldIsGlobalEnabled()
{
    return ((mpuRegisters.CESR & (MPU_CESR_NRGD_MASK | MPU_CESR_VLD_MASK))
            == (MPU_CESR_NRGD(0U) | MPU_CESR_VLD(1U)));
}

/** NRGD code and slave ports of the derivative this binary was generated for (RM Table 15-4). */
uint32_t derivativeNrgd() { return NX_MPU_CESR_NRGD; }

uint32_t derivativeSlavePorts() { return (SMPU_ENTRYS == 16U) ? 5U : 4U; }

void testEncodingOfTheReferenceManual()
{
    // RM Rev. 14, 15.4.2 CESR[NRGD]: 0000b = 8, 0001b = 12, 0010b = 16 region descriptors
    CHECK(regionsOfNrgd(0U) == 8U);
    CHECK(regionsOfNrgd(1U) == 12U);
    CHECK(regionsOfNrgd(2U) == 16U);
    // every other code is not defined by the hardware and must not be read as a region count
    for (uint32_t code = 3U; code <= 15U; ++code)
    {
        CHECK(regionsOfNrgd(code) == 0U);
    }
    // the generated code and the generated count belong together (also a static_assert in mpu.h)
    CHECK(regionsOfNrgd(derivativeNrgd()) == SMPU_ENTRYS);
    CHECK(SMPU_ENTRYS <= MPU_RGD_COUNT);
}

void testGlobalEnableLooksAtVldOnly()
{
    // the derivative of this build, enabled: both checks pass
    mpuRegisters.CESR = cesr(derivativeNrgd(), derivativeSlavePorts(), true);
    CHECK(isGlobalEnabled());
    CHECK(hasRegionsOfDerivative());

    // switched off, same silicon: only the enable check fails
    mpuRegisters.CESR = cesr(derivativeNrgd(), derivativeSlavePorts(), false);
    CHECK(!isGlobalEnabled());
    CHECK(hasRegionsOfDerivative());

    // the region count must not influence the enable bit: every NRGD code with VLD = 1 counts as enabled
    for (uint32_t code = 0U; code <= 15U; ++code)
    {
        mpuRegisters.CESR = cesr(code, 4U, true);
        CHECK(isGlobalEnabled());
        mpuRegisters.CESR = cesr(code, 4U, false);
        CHECK(!isGlobalEnabled());
    }
    // and neither must the error flags of the slave ports (bits 27..31), which software clears by writing 1
    mpuRegisters.CESR = cesr(derivativeNrgd(), derivativeSlavePorts(), true) | 0xF8000000U;
    CHECK(isGlobalEnabled());
    CHECK(hasRegionsOfDerivative());
}

void testRegionCountIsCheckedAgainstTheDerivative()
{
    for (uint32_t code = 0U; code <= 15U; ++code)
    {
        mpuRegisters.CESR = cesr(code, derivativeSlavePorts(), true);
        bool const fits = (code == derivativeNrgd());
        CHECK(hasRegionsOfDerivative() == fits);
        // an undefined code (3..15) never fits, a defined one only if it is this derivative's
        CHECK(!(hasRegionsOfDerivative() && (regionsOfNrgd(code) == 0U)));
    }
}

void testResetValuesOfTheReferenceManual()
{
    // RM Rev. 14, Table 15-4 "MPU configurations": CESR reset value per chip. The MPU is enabled after reset.
    struct Configuration
    {
        char const* chip;
        uint32_t reset;
        uint8_t regions;
        uint32_t slavePorts;
    };
    static Configuration const table[] = {
        {"S32K116", 0x00812001U, 8U, 2U},
        {"S32K118", 0x00812001U, 8U, 2U},
        {"S32K142", 0x00814001U, 8U, 4U},
        {"S32K144", 0x00814001U, 8U, 4U},
        {"S32K146", 0x00814001U, 8U, 4U},
        {"S32K148", 0x00815201U, 16U, 5U},
    };
    for (auto const& c : table)
    {
        mpuRegisters.CESR = c.reset;
        uint32_t const nrgd = ::safety::mpu::reportedNrgd();
        CHECK(regionsOfNrgd(nrgd) == c.regions);
        CHECK(((c.reset & MPU_CESR_NSP_MASK) >> MPU_CESR_NSP_SHIFT) == c.slavePorts);
        CHECK(isGlobalEnabled());                       // VLD = 1 in every reset value
        CHECK(hasRegionsOfDerivative() == (c.regions == SMPU_ENTRYS));
        // the old check accepted only the 8 region derivatives - this is finding ARCH-09 B3
        CHECK(oldIsGlobalEnabled() == (c.regions == 8U));
        if (c.regions != 8U)
        {
            std::printf(
                "  %s: enabled MPU (CESR 0x%08X, NRGD %u, %u regions) looked disabled before ARCH-16\n",
                c.chip,
                c.reset,
                nrgd,
                c.regions);
        }
    }
}

void testDescriptorsOfThisDerivativeAreReachable()
{
    // the header writes and reads every descriptor the derivative has, and nothing above it
    for (uint8_t slot = 0U; slot < MPU_RGD_COUNT; ++slot)
    {
        mpuRegisters.RGD[slot] = MpuRgdType{};
    }
    ::safety::mpu::Descriptor d{};
    d.startAddress = 0x20000000U;
    d.endAddress   = 0x2000001FU;
    d.word2.R      = 0x0061F7DFU;
    d.word3.R      = 0x00000001U;
    for (uint8_t slot = 0U; slot < SMPU_ENTRYS; ++slot)
    {
        ::safety::mpu::setDescriptor(slot, d);
        CHECK(::safety::mpu::getWordInDescriptor<0>(slot) == 0x20000000U);
        CHECK(::safety::mpu::getWordInDescriptor<3>(slot) == 0x00000001U);
    }
    // a slot the derivative does not have is refused instead of touching a foreign register
    CHECK(::safety::mpu::getWordInDescriptor<0>(SMPU_ENTRYS) == 0U);
    CHECK(::safety::mpu::getWordInDescriptor<3>(static_cast<uint8_t>(MPU_RGD_COUNT - 1U))
          == ((SMPU_ENTRYS == MPU_RGD_COUNT) ? 0x00000001U : 0U));
}

} // namespace

int main()
{
    std::printf("MpuHostTest: derivative with %u MPU region descriptors (NRGD %u)\n",
                static_cast<unsigned>(SMPU_ENTRYS), static_cast<unsigned>(derivativeNrgd()));
    testEncodingOfTheReferenceManual();
    testGlobalEnableLooksAtVldOnly();
    testRegionCountIsCheckedAgainstTheDerivative();
    testResetValuesOfTheReferenceManual();
    testDescriptorsOfThisDerivativeAreReachable();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
