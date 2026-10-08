// SYS-73 (SM-55): target part of the latent-fault test of the RAM ECC - the reserved RAM words and the injected
// read (safeMemory/EccLatentTest.h). Not host tested: the host test replaces eimInjectedRead() by a model; the
// sequence itself is checked on the board (doc/changelog.d/SYS-73.md) and its placement on the linked image
// (test/test_ecc_latent_test_elf.py).

#include "safeMemory/EccLatentTest.h"

#include <mcu/mcu.h>

extern "C"
{
// The reserved words, nothing else reads or writes them. One at the start of the application RAM (hot bss: the first
// RAM region, SRAM_L on the S32K14x) and one at its end (no-init RAM: the last region, SRAM_U), so every ECC channel
// of the derivative has a word in its array - checked at run time (eccChannelOfAddress, ECC_TEST_NOT_COVERED) and on
// the linked image. Neither section name matches the MPU protected .bss of the libsafe* archives.
uint32_t volatile nxEccLatentTestWordFirst __attribute__((section(".hot_bss.nx_ecc_latent_test")));
uint32_t volatile nxEccLatentTestWordLast __attribute__((section(".noinit.nx_ecc_latent_test")));
}

namespace safety
{
namespace safe_memory
{
namespace
{
uint32_t addressOf(uint32_t volatile const* const word)
{
    return static_cast<uint32_t>(reinterpret_cast<uintptr_t>(word));
}
} // namespace

uint32_t eimInjectedRead(uint32_t const channelEnable, uint32_t volatile const* const word)
{
    // All interrupts off: while the channel is enabled every read of its array is inverted ([RM] 21.3.3), an
    // interrupt handler's stack or data read would be falsified and recorded. On the Cortex-M4 the fault mask also
    // stops the fault handlers, and CCR.BFHFNMIGN lets a bus error of the injected read (not expected on the
    // S32K14x, [RM] Table 31-5) be ignored instead of escalating. NMI stays possible; its window is a few cycles.
#if defined(__ARM_ARCH_7EM__)
    uint32_t const faultMask = __get_FAULTMASK();
    __set_FAULTMASK(1U);
    uint32_t const ccr = SCB->CCR;
    SCB->CCR           = ccr | SCB_CCR_BFHFNMIGN_Msk;
    __DSB();
    __ISB();
#else
    uint32_t const priMask = __get_PRIMASK();
    __disable_irq();
#endif
    uint32_t value = 0U;
    uint32_t temp  = 0U;
    // Register-only from enabling to disabling: no stack, no literal in RAM. The read-backs of the EIM and the
    // barriers make sure the injection is on before the read and off before the next instruction touches RAM.
    __asm volatile(
        "str   %[enable], [%[eim], #4]  \n" // EICHEN: the channel
        "movs  %[temp], #1              \n"
        "str   %[temp], [%[eim], #0]    \n" // EIMCR: GEIEN
        "ldr   %[temp], [%[eim], #0]    \n" // read back: the enables have reached the EIM
        "dsb                            \n"
        "ldr   %[value], [%[word], #0]  \n" // the injected read
        "dsb                            \n"
        "movs  %[temp], #0              \n"
        "str   %[temp], [%[eim], #0]    \n" // EIMCR off
        "str   %[temp], [%[eim], #4]    \n" // EICHEN off
        "ldr   %[temp], [%[eim], #4]    \n" // read back: off before anything else reads RAM
        "dsb                            \n"
        : [value] "=&l"(value), [temp] "=&l"(temp)
        : [eim] "l"(IP_EIM), [enable] "l"(channelEnable), [word] "l"(word)
        : "cc", "memory");
    (void)temp;
#if defined(__ARM_ARCH_7EM__)
    SCB->CCR = ccr;
    __DSB();
    __ISB();
    __set_FAULTMASK(faultMask);
#else
    __set_PRIMASK(priMask);
#endif
    return value;
}

EccLatentTestResult eccLatentTestAtStart()
{
    EccTestWord const words[] = {
        {&nxEccLatentTestWordFirst, eccChannelOfAddress(addressOf(&nxEccLatentTestWordFirst))},
        {&nxEccLatentTestWordLast, eccChannelOfAddress(addressOf(&nxEccLatentTestWordLast))},
    };
    return runEccLatentTest(words, static_cast<uint32_t>(sizeof(words) / sizeof(words[0])));
}

} // namespace safe_memory
} // namespace safety
