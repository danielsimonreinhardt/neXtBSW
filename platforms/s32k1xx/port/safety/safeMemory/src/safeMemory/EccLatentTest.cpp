// SYS-73 (SM-55): start-up latent-fault test of the RAM ECC through the EIM - the part without the injected read
// itself, host tested against register models of the EIM and the ERM (test/host/EccLatentTestHostTest.cpp). The
// register-only read and the reserved words of the image are in EccLatentTestTarget.cpp.

#include "safeMemory/EccLatentTest.h"

#include "safeMemory/IntegrityEccHandler.h"

#include <mcu/mcu.h>

namespace safety
{
namespace safe_memory
{
namespace
{
// [RM] Rev. 13, 21.3.3 and 22.3: channel 0 has the top bits of EICHEN, ERM SR0 and ERM CR0, channel 1 the next.
// The masks come from the device header; a derivative with one channel (S32K11x) has no *1 macros.
#if ERM_EARn_COUNT > 1
#if !defined(ERM_SR0_NCE1_MASK) || !defined(EIM_EICHEN_EICH1EN_MASK) || (EIM_EICHDn_COUNT < 2)
#error "two ERM channels, but the device header lacks channel 1 of the ERM or the EIM"
#endif
constexpr uint32_t CHANNELS = 2U;
#else
constexpr uint32_t CHANNELS = 1U;
#endif

// Start of SRAM_U, end of SRAM_L: the boundary of the code and the SRAM region of the Cortex-M memory map, where the
// S32K1 places its two SRAM arrays ([RM] 31.3 "SRAM L and SRAM U").
constexpr uint32_t SRAM_U_START = 0x20000000U;

// Stored pattern and the inverted data bits (at most two per read, [RM] 21.4.1).
constexpr uint32_t PATTERN     = 0x5AA5F00FU;
constexpr uint32_t SINGLE_MASK = 0x00010000U;
#if NX_ECC_LATENT_TEST_DOUBLE_BIT
constexpr uint32_t DOUBLE_MASK = 0x00000081U;
#endif

uint32_t eimEnableOf(uint32_t const channel)
{
#if ERM_EARn_COUNT > 1
    return (channel == 0U) ? EIM_EICHEN_EICH0EN_MASK : EIM_EICHEN_EICH1EN_MASK;
#else
    (void)channel;
    return EIM_EICHEN_EICH0EN_MASK;
#endif
}

uint32_t singleFlagOf(uint32_t const channel)
{
#if ERM_EARn_COUNT > 1
    return (channel == 0U) ? ERM_SR0_SBC0_MASK : ERM_SR0_SBC1_MASK;
#else
    (void)channel;
    return ERM_SR0_SBC0_MASK;
#endif
}

uint32_t uncorrectableFlagOf(uint32_t const channel)
{
#if ERM_EARn_COUNT > 1
    return (channel == 0U) ? ERM_SR0_NCE0_MASK : ERM_SR0_NCE1_MASK;
#else
    (void)channel;
    return ERM_SR0_NCE0_MASK;
#endif
}

uint32_t interruptEnablesOf(uint32_t const channel)
{
#if ERM_EARn_COUNT > 1
    return (channel == 0U) ? (ERM_CR0_ESCIE0_MASK | ERM_CR0_ENCIE0_MASK)
                           : (ERM_CR0_ESCIE1_MASK | ERM_CR0_ENCIE1_MASK);
#else
    (void)channel;
    return ERM_CR0_ESCIE0_MASK | ERM_CR0_ENCIE0_MASK;
#endif
}

uint32_t addressOf(uint32_t volatile const* const word)
{
    return static_cast<uint32_t>(reinterpret_cast<uintptr_t>(word));
}

bool eimOff() { return (IP_EIM->EIMCR == 0U) && (IP_EIM->EICHEN == 0U); }

void switchEimOff()
{
    IP_EIM->EIMCR  = 0U;
    IP_EIM->EICHEN = 0U;
}

/** One injected read with the masks given; checks that the EIM is off afterwards (switches it off if not). */
uint32_t injectedRead(
    uint32_t const channel, uint32_t volatile const* const word, uint32_t const dataMask, uint32_t& failures)
{
    // writing a descriptor word clears the channel enable ([RM] 21.3.4, 21.3.5): masks first, enables after
    IP_EIM->EICHDn[channel].WORD0 = 0U;
    IP_EIM->EICHDn[channel].WORD1 = dataMask;
    uint32_t const value          = eimInjectedRead(eimEnableOf(channel), word);
    if (!eimOff())
    {
        failures |= ECC_TEST_EIM_LEFT_ON;
        switchEimOff();
    }
    return value;
}

/** Reads the channel's ERM record and clears exactly its flags (write one to clear). */
uint32_t takeRecord(uint32_t const channel, uint32_t& address)
{
    uint32_t const flags = ERM->SR0 & (singleFlagOf(channel) | uncorrectableFlagOf(channel));
    address              = ERM->EARn[channel].EAR;
    if (flags != 0U)
    {
        ERM->SR0 = flags;
    }
    return flags;
}
} // namespace

uint32_t eccChannelCount() { return CHANNELS; }

uint32_t eccChannelOfAddress(uint32_t const address)
{
#if ERM_EARn_COUNT > 1
    // S32K14x: channel 0 SRAM_L, channel 1 SRAM_U
    return (address < SRAM_U_START) ? 0U : 1U;
#else
    // S32K11x: channel 0 SRAM_U; SRAM_L (the MTB RAM) has no ECC ([RM] Table 31-2)
    return (address >= SRAM_U_START) ? 0U : ECC_NO_CHANNEL;
#endif
}

uint32_t eccLatentTestContext(uint32_t const channel, uint32_t const failures)
{
    return ECC_SOURCE_LATENT_TEST | ((failures & 0x3FFU) << 8U) | ((channel & 0xFU) << 24U);
}

uint32_t testEccChannel(uint32_t const channel, uint32_t volatile* const word)
{
    uint32_t const flags = singleFlagOf(channel) | uncorrectableFlagOf(channel);
    // A record of the channel before the test belongs to a real event: it stays for the cyclic poll (SM-15), and the
    // test cannot tell its own events from it. An enabled EIM would falsify every read - off, and not passed.
    if (!eimOff())
    {
        switchEimOff();
        return ECC_TEST_PRECONDITION;
    }
    if ((ERM->SR0 & flags) != 0U)
    {
        return ECC_TEST_PRECONDITION;
    }
    uint32_t failures = 0U;
    uint32_t address  = 0U;
    // no interrupt notification of the test's own events (restored below, after the record is cleared)
    uint32_t const interruptEnables = ERM->CR0;
    ERM->CR0                        = interruptEnables & ~interruptEnablesOf(channel);
    *word                           = PATTERN;

    // single-bit error: corrected and recorded as a correction at the word's address
    uint32_t const corrected = injectedRead(channel, word, SINGLE_MASK, failures);
    uint32_t record          = takeRecord(channel, address);
    if (corrected != PATTERN)
    {
        failures |= ECC_TEST_NOT_CORRECTED;
    }
    if ((record & singleFlagOf(channel)) == 0U)
    {
        failures |= ECC_TEST_SINGLE_NOT_REPORTED;
    }
    if ((record & uncorrectableFlagOf(channel)) != 0U)
    {
        failures |= ECC_TEST_SINGLE_UNCORRECTABLE;
    }
    if (address != addressOf(word))
    {
        failures |= ECC_TEST_ADDRESS;
    }

#if NX_ECC_LATENT_TEST_DOUBLE_BIT
    // double-bit error: recorded as non-correctable at the word's address (the value read is not used)
    (void)injectedRead(channel, word, DOUBLE_MASK, failures);
    record = takeRecord(channel, address);
    if ((record & uncorrectableFlagOf(channel)) == 0U)
    {
        failures |= ECC_TEST_DOUBLE_NOT_DETECTED;
    }
    if (address != addressOf(word))
    {
        failures |= ECC_TEST_ADDRESS;
    }
#endif

    // descriptor back to its reset value (also clears the channel enable), then a plain read: pattern, no record
    IP_EIM->EICHDn[channel].WORD1 = 0U;
    IP_EIM->EICHDn[channel].WORD0 = 0U;
    if (!eimOff())
    {
        failures |= ECC_TEST_EIM_LEFT_ON;
        switchEimOff();
    }
    uint32_t const plain = *word;
    if (plain != PATTERN)
    {
        failures |= ECC_TEST_WORD_CHANGED;
    }
    if ((ERM->SR0 & flags) != 0U)
    {
        failures |= ECC_TEST_RECORD_NOT_CLEARED;
    }
    ERM->CR0 = interruptEnables;
    return failures;
}

EccLatentTestResult runEccLatentTest(EccTestWord const* const words, uint32_t const count)
{
    EccLatentTestResult result{0U, 0U};
    for (uint32_t channel = 0U; channel < CHANNELS; ++channel)
    {
        uint32_t volatile* word = nullptr;
        for (uint32_t i = 0U; (i < count) && (word == nullptr); ++i)
        {
            if (words[i].channel == channel)
            {
                word = words[i].word;
            }
        }
        uint32_t const failures = (word == nullptr) ? ECC_TEST_NOT_COVERED : testEccChannel(channel, word);
        if (failures != 0U)
        {
            result.context = eccLatentTestContext(channel, failures);
            return result;
        }
        ++result.channels;
    }
    return result;
}

} // namespace safe_memory
} // namespace safety
