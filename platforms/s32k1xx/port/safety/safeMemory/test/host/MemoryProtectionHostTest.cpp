/* SYS-171 (safety review 2026-10-02 check point SYS69-S2, findings SFP-F02 and SFP-F04 of
 * doc/safety/reviews/code-inspection/INS-SFP-1.md, SM-13): host test of the cyclic MPU region check
 * MemoryProtection::areRegionsConfiguredCorrectly() with the real MemoryProtection.cpp and mpu.h on a register model
 * of the system MPU (mpStubs/mcu/mcu.h) and a target-like layout of the linker symbols (mpStubs/safeMemory).
 *
 * Every case runs twice: with the reserved bits of WORD2 (17, 23 and the enables of the bus masters 4..7, which the
 * S32K1 does not implement, RM Rev. 13 15.1.2) reading back as written, and reading as 0 - the silicon's behaviour
 * there is not documented, and the check must neither depend on it nor raise a false alarm.
 *
 * What it shows:
 * - healthy: after init() on a reset MPU the check passes; descriptor 0 (debugger region over the whole address
 *   space) holds the word of the HSI - core (M0) no access, debugger (M1) its reset rights, M2/M3 rwx (finding
 *   SFP-F03, documented, not changed);
 * - the write of platform::StaticBsp::init() through RGDAAC0 (M3SM = 11b, M3UM = 111b), which runs after init(),
 *   leaves descriptor 0 unchanged, so the check passes on the target as well;
 * - descriptor 0: every single changed bit of the 22 implemented WORD2 field bits fails the check and is reported as
 *   region 0 (SFP-F02; before SYS-171 descriptor 0 was never read); in particular a change of the M0 fields that
 *   would give the core write access everywhere;
 * - descriptors 1 .. 7: every single changed bit of every implemented WORD2 field fails the check and names the
 *   descriptor, also the M3 fields the former mask 0x0001FFFF left out (SFP-F04), e.g. M3 write access to the guard;
 * - the reserved bits are not compared: changing them (where the model keeps them) passes;
 * - descriptor 0 is checked first: a fault in it and in another descriptor reports region 0;
 * - the address and validity checks of the other descriptors are unchanged (samples).
 *
 * Built-in faults: test_memory_protection_mutations.py (SYS-171) applies deliberate faults to MemoryProtection.cpp
 * and expects this test to fail for each of them.
 *
 * -Wno-shorten-64-to-32: MemoryProtection::init() stores linker addresses (uintptr_t) in 32-bit descriptor fields,
 * which is exact on the target; the stub gives them as 32-bit target addresses.
 *
 * Build and run (zig c++, no hardware; tools/test entries safe-memory-mpu-regions and safe-memory-mpu-regions-16,
 * the latter with -DNX_MPU_REGIONS=16 -DNX_MPU_CESR_NRGD=2):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion \
 *       -Wno-shorten-64-to-32 -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeMemory/test/host/mpStubs \
 *       -Iplatforms/s32k1xx/port/safety/safeMemory/include \
 *       platforms/s32k1xx/port/safety/safeMemory/test/host/MemoryProtectionHostTest.cpp \
 *       platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/MemoryProtection.cpp -o build/host/mpu_regions.exe
 *   build/host/mpu_regions.exe
 */

#include <safeMemory/MemoryLocations.h>
#include <safeMemory/MemoryProtection.h>
#include <safeMemory/mpu.h>

#include <cstdio>
#include <initializer_list>

MpuType mpuRegisters{};
::async::internal::Stack<safety_task_stackSize> safetyStack{0x20001000U};

namespace test_mpu
{
bool reservedReadsZero = false;
} // namespace test_mpu

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

using ::safety::MemoryProtection;
using ::safety::mpu::SMPU_ENTRYS;
using ::test_mpu::WORD2_IMPLEMENTED;

// Descriptors init() writes: 0 (debugger region) and 1 .. 7 (safety layout with the stack guard)
constexpr uint8_t CONFIGURED_DESCRIPTORS = 8U;
static_assert(CONFIGURED_DESCRIPTORS <= SMPU_ENTRYS, "");
// implemented field bits of WORD2: M0..M3 user/supervisor mode (4 x 5) and M0PE, M1PE
constexpr int FIELD_BITS = 22;

/**
 * RGD0_WORD2 as the HSI states it, written out from the field table of the reference manual (RM Rev. 13, 15.4.7)
 * and not taken from the code: M0UM 000b, M0SM 11b (core: no access), M0PE 0, M1UM 111b, M1SM 11b (debugger: reset
 * rights), M1PE 0, M2UM 111b, M2SM 00b, M3UM 111b, M3SM 11b (both rwx); M4..M7 not implemented.
 */
constexpr uint32_t RGD0_WORD2_OF_THE_HSI = 0x18U | 0x1C0U | 0x600U | 0x7000U | 0x1C0000U | 0x600000U;
// the mask the cyclic comparison used before SYS-171 (fields of M0 .. M2 only)
constexpr uint32_t FORMER_MASK = 0x0001FFFFU;

/** MPU after reset (RM Rev. 13, 15.4.1: RGD0 whole address space, WORD2 0x0061F7DF, valid; the others invalid). */
void resetMpu()
{
    mpuRegisters              = MpuType{};
    mpuRegisters.CESR         = MPU_CESR_NRGD(NX_MPU_CESR_NRGD) | MPU_CESR_VLD(1U);
    mpuRegisters.RGD[0].WORD0 = 0x00000000U;
    mpuRegisters.RGD[0].WORD1 = 0xFFFFFFFFU;
    mpuRegisters.RGD[0].WORD2 = 0x0061F7DFU;
    mpuRegisters.RGD[0].WORD3 = 0x00000001U;
}

uint32_t word2(uint8_t const slot) { return mpuRegisters.RGD[slot].WORD2; }

void flipWord2(uint8_t const slot, uint32_t const bits) { mpuRegisters.RGD[slot].WORD2 = word2(slot) ^ bits; }

/** The start-up of main(): MemoryProtection::init(), later platform::StaticBsp::init() writing RGDAAC0. */
void powerOn()
{
    resetMpu();
    MemoryProtection::init();
    // StaticBsp.cpp: IP_MPU->RGDAAC[0] = IP_MPU->RGDAAC[0] | MPU_RGDAAC_M3SM(3) | MPU_RGDAAC_M3UM(7); RGDAAC0 is an
    // alias of RGD0_WORD2 (RM 15.4.12), so the model applies it to the descriptor
    mpuRegisters.RGD[0].WORD2 = word2(0U) | (MPU_RGDAAC_M3SM(3U) | MPU_RGDAAC_M3UM(7U));
}

/** The region check as the safety cycle calls it (gate open); failedRegion 0xFF = not written. */
bool regionCheck(uint8_t& failedRegion)
{
    failedRegion = 0xFFU;
    MemoryProtection::fusaGateOpen();
    return MemoryProtection::areRegionsConfiguredCorrectly(failedRegion);
}

/** May the core (M0, privileged) write anywhere through descriptor 0 (M0SM rwx/rw, or same as user with w)? */
bool coreMayWriteThroughDescriptor0()
{
    uint32_t const w  = word2(0U);
    uint32_t const sm = (w >> 3U) & 3U;
    uint32_t const um = w & 7U;
    return (sm == 0U) || (sm == 2U) || ((sm == 3U) && ((um & 2U) != 0U));
}

void testHealthy()
{
    powerOn();
    uint8_t failedRegion = 0U;
    CHECK(regionCheck(failedRegion));
    CHECK(failedRegion == 0xFFU); // untouched when the check passes
    CHECK((word2(0U) & WORD2_IMPLEMENTED) == RGD0_WORD2_OF_THE_HSI);
    CHECK(!coreMayWriteThroughDescriptor0());
    // the descriptors of the layout are valid, the ones above invalid
    for (uint8_t slot = 1U; slot < CONFIGURED_DESCRIPTORS; ++slot)
    {
        CHECK((mpuRegisters.RGD[slot].WORD3 & 1U) == 1U);
    }
    for (uint8_t slot = CONFIGURED_DESCRIPTORS; slot < SMPU_ENTRYS; ++slot)
    {
        CHECK((mpuRegisters.RGD[slot].WORD3 & 1U) == 0U);
    }
    // right after init(), before StaticBsp: the same word (the write of StaticBsp changes nothing)
    resetMpu();
    MemoryProtection::init();
    CHECK((word2(0U) & WORD2_IMPLEMENTED) == RGD0_WORD2_OF_THE_HSI);
    CHECK(regionCheck(failedRegion));
    MemoryProtection::fusaGateClose();
}

void testDescriptor0EveryFieldBit()
{
    int detected = 0;
    for (uint32_t bit = 0U; bit < 32U; ++bit)
    {
        uint32_t const flip = 1UL << bit;
        powerOn();
        MemoryProtection::fusaGateOpen();
        flipWord2(0U, flip);
        uint8_t failedRegion = 0xFFU;
        bool const ok        = MemoryProtection::areRegionsConfiguredCorrectly(failedRegion);
        if ((flip & WORD2_IMPLEMENTED) == 0U)
        {
            CHECK(ok); // reserved or not implemented: not compared
            continue;
        }
        CHECK(!ok);
        CHECK(failedRegion == 0U);
        if (!ok)
        {
            ++detected;
        }
    }
    CHECK(detected == FIELD_BITS);
    std::printf("    descriptor 0: %d of %d field bits detected (before SYS-171: 0)\n", detected, FIELD_BITS);

    // the fault that matters most: M0 fields that give the core write access over the whole address space
    for (uint32_t const m0 : {0x00U, 0x10U, 0x1AU, 0x1EU})
    {
        powerOn();
        mpuRegisters.RGD[0].WORD2 = (word2(0U) & ~0x3FU) | m0;
        CHECK(coreMayWriteThroughDescriptor0());
        uint8_t failedRegion = 0U;
        CHECK(!regionCheck(failedRegion));
        CHECK(failedRegion == 0U);
    }
}

void testDescriptors1To7EveryFieldBit()
{
    int detected       = 0;
    int formerlyMissed = 0;
    for (uint8_t slot = 1U; slot < CONFIGURED_DESCRIPTORS; ++slot)
    {
        for (uint32_t bit = 0U; bit < 32U; ++bit)
        {
            uint32_t const flip = 1UL << bit;
            powerOn();
            MemoryProtection::fusaGateOpen(); // the check sees the gate open; flip in that state
            flipWord2(slot, flip);
            uint8_t failedRegion = 0xFFU;
            bool const ok        = MemoryProtection::areRegionsConfiguredCorrectly(failedRegion);
            if ((flip & WORD2_IMPLEMENTED) == 0U)
            {
                CHECK(ok);
                continue;
            }
            CHECK(!ok);
            CHECK(failedRegion == slot);
            if (!ok)
            {
                ++detected;
            }
            if ((flip & FORMER_MASK) == 0U)
            {
                ++formerlyMissed;
            }
        }
    }
    CHECK(detected == (FIELD_BITS * (CONFIGURED_DESCRIPTORS - 1)));
    CHECK(formerlyMissed == (5 * (CONFIGURED_DESCRIPTORS - 1))); // M3SM and M3UM per word
    std::printf(
        "    descriptors 1..7: %d of %d field bits detected, %d of them outside the former mask 0x0001FFFF\n",
        detected,
        FIELD_BITS * (CONFIGURED_DESCRIPTORS - 1),
        formerlyMissed);
}

void testM3GetsWriteAccessToTheGuard()
{
    // SFP-F04: bus master 3 (ENET on the S32K148) gets write access to the guard below the interrupt stack: M3UM
    // write bit (with M3SM 11b "same as user") or M3SM 10b (rw) - both outside the former mask
    for (uint32_t const grant : {0x00080000U, 0x00200000U})
    {
        powerOn();
        MemoryProtection::fusaGateOpen();
        flipWord2(MemoryProtection::STACK_GUARD_REGION, grant);
        uint8_t failedRegion = 0xFFU;
        CHECK(!MemoryProtection::areRegionsConfiguredCorrectly(failedRegion));
        CHECK(failedRegion == MemoryProtection::STACK_GUARD_REGION);
        CHECK((grant & FORMER_MASK) == 0U);
    }
}

void testDescriptor0IsReportedFirst()
{
    powerOn();
    MemoryProtection::fusaGateOpen();
    flipWord2(0U, 0x00400000U);
    flipWord2(3U, 0x00000001U);
    uint8_t failedRegion = 0xFFU;
    CHECK(!MemoryProtection::areRegionsConfiguredCorrectly(failedRegion));
    CHECK(failedRegion == 0U);
}

void testUnchangedChecksOfDescriptors1To7()
{
    // the address and validity checks of SYS-97 and before are unchanged; one sample each
    powerOn();
    MemoryProtection::fusaGateOpen();
    mpuRegisters.RGD[3].WORD3 = 0U;
    uint8_t failedRegion      = 0xFFU;
    CHECK(!MemoryProtection::areRegionsConfiguredCorrectly(failedRegion));
    CHECK(failedRegion == 3U);
    powerOn();
    MemoryProtection::fusaGateOpen();
    mpuRegisters.RGD[1].WORD1 = mpuRegisters.RGD[1].WORD1 + 0x20U;
    CHECK(!MemoryProtection::areRegionsConfiguredCorrectly(failedRegion));
    // a descriptor above the layout that became valid
    if (SMPU_ENTRYS > CONFIGURED_DESCRIPTORS)
    {
        powerOn();
        MemoryProtection::fusaGateOpen();
        mpuRegisters.RGD[SMPU_ENTRYS - 1U].WORD3 = 1U;
        CHECK(!MemoryProtection::areRegionsConfiguredCorrectly(failedRegion));
    }
}

void runAll()
{
    testHealthy();
    testDescriptor0EveryFieldBit();
    testDescriptors1To7EveryFieldBit();
    testM3GetsWriteAccessToTheGuard();
    testDescriptor0IsReportedFirst();
    testUnchangedChecksOfDescriptors1To7();
}
} // namespace

int main()
{
    std::printf(
        "MemoryProtectionHostTest: derivative with %u MPU region descriptors\n", static_cast<unsigned>(SMPU_ENTRYS));
    for (bool const zero : {false, true})
    {
        ::test_mpu::reservedReadsZero = zero;
        std::printf("  reserved WORD2 bits %s\n", zero ? "read as 0" : "read back as written");
        runAll();
    }
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
