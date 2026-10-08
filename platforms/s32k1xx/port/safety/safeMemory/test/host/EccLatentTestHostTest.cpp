/* SYS-73 (SM-55): host test of the start-up latent-fault test of the RAM ECC with the real EccLatentTest.cpp (and
 * IntegrityEccHandler.cpp for the poll after it) against register models of the EIM and the ERM
 * (eccLatentStubs/mcu/mcu.h). The injected read of the target (EccLatentTestTarget.cpp, register-only assembler) is
 * replaced by a model of what the hardware does with a read while the EIM is on: one inverted bit is corrected and
 * recorded as a single-bit correction, two inverted bits are recorded as non-correctable ([RM] 21.4.1, 22.4).
 *
 * What it shows:
 * - healthy hardware passes, every channel is tested with its word, one single-bit and (where built) one double-bit
 *   read per channel, at most two inverted bits per read;
 * - afterwards the EIM is off (EIMCR, EICHEN, every descriptor word 0), the ERM record is clear, the interrupt
 *   notification (ERM CR0) is back to what it was and was off for the channel during every injected read, and the
 *   cyclic ECC poll finds nothing (no reaction, no counted correction);
 * - every fault of the hardware model is found with its failure bit and channel in the event context: no
 *   correction, no single-bit record, single-bit recorded as non-correctable, no double-bit record, wrong address,
 *   EIM left on, record that does not clear, word changed; the EIM is off after every run, also after a fault;
 * - a record or an enabled EIM before the test: not passed, no injected read, a real record is left for the poll;
 * - a channel without a word in its array is not passed (placement), the address to channel mapping of the family.
 *
 * Two builds (tools/test entries safe-memory-ecc-latent and safe-memory-ecc-latent-s32k11x): the S32K14x layout (two
 * channels, double-bit case), and with NX_TEST_ONE_ECC_CHANNEL and NX_ECC_LATENT_TEST_DOUBLE_BIT=0 the S32K11x
 * layout (one channel, single-bit case only).
 * Mutants: test_ecc_latent_test_mutations.py. */

#include "safeMemory/EccLatentTest.h"
#include "safeMemory/IntegrityEccHandler.h"

#include <mcu/mcu.h>

#include <cstdio>

ErmType ermRegisters{};
EimType eimRegisters{};
FtfcType ftfcRegisters{};

namespace
{
namespace sm = ::safety::safe_memory;

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

#ifdef NX_TEST_ONE_ECC_CHANNEL
constexpr uint32_t CHANNELS = 1U;
#else
constexpr uint32_t CHANNELS = 2U;
#endif
constexpr uint32_t READS_PER_CHANNEL = NX_ECC_LATENT_TEST_DOUBLE_BIT ? 2U : 1U;
constexpr uint32_t PATTERN           = 0x5AA5F00FU; // EccLatentTest.cpp

uint32_t const ENABLE[2] = {
    EIM_EICHEN_EICH0EN_MASK,
#ifdef NX_TEST_ONE_ECC_CHANNEL
    0U,
#else
    EIM_EICHEN_EICH1EN_MASK,
#endif
};
uint32_t const SBC[2] = {
    ERM_SR0_SBC0_MASK,
#ifdef NX_TEST_ONE_ECC_CHANNEL
    0U,
#else
    ERM_SR0_SBC1_MASK,
#endif
};
uint32_t const NCE[2] = {
    ERM_SR0_NCE0_MASK,
#ifdef NX_TEST_ONE_ECC_CHANNEL
    0U,
#else
    ERM_SR0_NCE1_MASK,
#endif
};
uint32_t const CR0_OF[2] = {
    ERM_CR0_ESCIE0_MASK | ERM_CR0_ENCIE0_MASK,
#ifdef NX_TEST_ONE_ECC_CHANNEL
    0U,
#else
    ERM_CR0_ESCIE1_MASK | ERM_CR0_ENCIE1_MASK,
#endif
};

/* Faults of the hardware model, each on one channel (`faultChannel`). */
struct Model
{
    uint32_t faultChannel      = 0xFFU;
    bool noCorrection          = false; // decoder passes the inverted bit through
    bool noSingleRecord        = false; // ERM misses the correction
    bool singleAsUncorrectable = false; // decoder flags one bit as non-correctable
    bool noDoubleRecord        = false; // decoder misses two bits
    bool wrongAddress          = false; // ERM records another address
    bool eimLeftOn             = false; // the EIM does not switch off
    bool corruptsWord          = false; // the stored word changes (write path)

    uint32_t reads           = 0U;
    uint32_t readsPerChannel[2] = {0U, 0U};
    uint32_t maxInverted     = 0U;
    uint32_t invertedSingle  = 0U; // inverted bits of the first read of a channel
    uint32_t invertedDouble  = 0U; // of the second
    uint32_t cr0OfChannelAtRead = 0U; // interrupt enables of the channel at any injected read
    bool badEnable           = false; // not exactly one known channel enabled
};

Model model;

uint32_t words[2] = {0U, 0U};

uint32_t popcount(uint32_t v)
{
    uint32_t n = 0U;
    while (v != 0U)
    {
        n += v & 1U;
        v >>= 1U;
    }
    return n;
}

uint32_t addressOf(uint32_t volatile const* const word)
{
    return static_cast<uint32_t>(reinterpret_cast<uintptr_t>(word));
}

void reset()
{
    ermRegisters  = ErmType{};
    eimRegisters  = EimType{};
    ftfcRegisters = FtfcType{};
    for (uint32_t ch = 0U; ch < EIM_EICHDn_COUNT; ++ch)
    {
        eimRegisters.EICHDn[ch].WORD0.channel = ch;
        eimRegisters.EICHDn[ch].WORD1.channel = ch;
    }
    model    = Model{};
    words[0] = 0xFFFFFFFFU;
    words[1] = 0xFFFFFFFFU;
}

bool eimAllOff()
{
    bool off = (eimRegisters.EIMCR == 0U) && (eimRegisters.EICHEN == 0U);
    for (uint32_t ch = 0U; ch < EIM_EICHDn_COUNT; ++ch)
    {
        off = off && (eimRegisters.EICHDn[ch].WORD0.value == 0U) && (eimRegisters.EICHDn[ch].WORD1.value == 0U);
    }
    return off;
}

sm::EccLatentTestResult runOnWords()
{
    sm::EccTestWord table[2] = {{&words[0], 0U}, {&words[1], 1U}};
    return sm::runEccLatentTest(table, CHANNELS);
}
} // namespace

void eimDescriptorWritten(uint32_t const channel) { eimRegisters.EICHEN &= ~ENABLE[channel]; }

namespace safety
{
namespace safe_memory
{
/* Model of the injected read on the S32K1: the EIM inverts the masked bits of the read data and check bits of the
 * enabled channel; the SEC-DED decoder corrects one and records it (SBCn), flags two (NCEn); EARn = the address. */
uint32_t eimInjectedRead(uint32_t const channelEnable, uint32_t volatile const* const word)
{
    ++model.reads;
    uint32_t channel = 0xFFU;
    for (uint32_t ch = 0U; ch < CHANNELS; ++ch)
    {
        if (channelEnable == ENABLE[ch])
        {
            channel = ch;
        }
    }
    if (channel == 0xFFU)
    {
        model.badEnable = true;
        return *word;
    }
    ++model.readsPerChannel[channel];
    model.cr0OfChannelAtRead |= ermRegisters.CR0 & CR0_OF[channel];
    eimRegisters.EICHEN = channelEnable;
    eimRegisters.EIMCR  = EIM_EIMCR_GEIEN_MASK;

    uint32_t const dataMask = eimRegisters.EICHDn[channel].WORD1.value;
    uint32_t const inverted = popcount(dataMask) + popcount(eimRegisters.EICHDn[channel].WORD0.value >> 25U);
    model.maxInverted       = (inverted > model.maxInverted) ? inverted : model.maxInverted;
    if (model.readsPerChannel[channel] == 1U)
    {
        model.invertedSingle = inverted;
    }
    else
    {
        model.invertedDouble = inverted;
    }
    bool const faulty = (channel == model.faultChannel);
    uint32_t const address
        = (faulty && model.wrongAddress) ? (addressOf(word) + 4U) : addressOf(word);
    uint32_t value = *word;
    if (inverted == 1U)
    {
        value = (faulty && model.noCorrection) ? (value ^ dataMask) : value;
        if (!(faulty && model.noSingleRecord))
        {
            ermRegisters.SR0.value |= (faulty && model.singleAsUncorrectable) ? NCE[channel] : SBC[channel];
            ermRegisters.EARn[channel].EAR = address;
        }
    }
    else if (inverted == 2U)
    {
        value ^= dataMask;
        if (!(faulty && model.noDoubleRecord))
        {
            ermRegisters.SR0.value |= NCE[channel];
            ermRegisters.EARn[channel].EAR = address;
        }
    }
    if (faulty && model.corruptsWord)
    {
        *const_cast<uint32_t volatile*>(word) |= 0x10U; // pattern bit 4 is 0
    }
    if (!(faulty && model.eimLeftOn))
    {
        eimRegisters.EIMCR  = 0U;
        eimRegisters.EICHEN = 0U;
    }
    return value;
}
} // namespace safe_memory
} // namespace safety

namespace
{
void checkLeftClean()
{
    CHECK(eimAllOff());
    CHECK(!model.badEnable);
    CHECK(model.maxInverted <= 2U);
    CHECK(model.cr0OfChannelAtRead == 0U);
}

void testHealthyPasses()
{
    reset();
    uint32_t const allEnables  = 0xCC000000U & (CR0_OF[0] | CR0_OF[1]);
    ermRegisters.CR0           = allEnables;
    sm::EccLatentTestResult r = runOnWords();
    CHECK(r.context == 0U);
    CHECK(r.channels == CHANNELS);
    CHECK(sm::eccChannelCount() == CHANNELS);
    CHECK(model.reads == CHANNELS * READS_PER_CHANNEL);
    for (uint32_t ch = 0U; ch < CHANNELS; ++ch)
    {
        CHECK(model.readsPerChannel[ch] == READS_PER_CHANNEL);
        CHECK(words[ch] == PATTERN);
    }
    CHECK(model.invertedSingle == 1U);
    CHECK(model.invertedDouble == (NX_ECC_LATENT_TEST_DOUBLE_BIT ? 2U : 0U));
    checkLeftClean();
    CHECK(ermRegisters.SR0.value == 0U);
    CHECK(ermRegisters.CR0 == allEnables);
    // the cyclic poll after the test: nothing to report, no correction counted
    sm::EccErrors const e = sm::takeEccErrors();
    CHECK(e.sources == 0U);
    CHECK(e.corrections == 0U);
    CHECK(sm::eccCorrectionCount() == 0U);
}

void expectFailure(char const* name, uint32_t const channel, uint32_t const failure)
{
    sm::EccLatentTestResult const r = runOnWords();
    uint32_t const expected         = sm::eccLatentTestContext(channel, failure);
    if ((r.context & expected) != expected)
    {
        std::printf("  %s: context 0x%08x, expected bits 0x%08x\n", name, static_cast<unsigned>(r.context),
                    static_cast<unsigned>(expected));
    }
    CHECK((r.context & expected) == expected);
    CHECK(((r.context >> 24U) & 0xFU) == channel);
    CHECK((r.context & sm::ECC_SOURCE_LATENT_TEST) != 0U);
    CHECK(r.channels == channel);
    CHECK(eimAllOff()); // never left on, also after a fault
}

void testHardwareFaultsAreFound()
{
    uint32_t const last = CHANNELS - 1U;

    reset();
    model.faultChannel = last;
    model.noCorrection = true;
    expectFailure("no correction", last, sm::ECC_TEST_NOT_CORRECTED);

    reset();
    model.faultChannel   = 0U;
    model.noSingleRecord = true;
    expectFailure("no single-bit record", 0U, sm::ECC_TEST_SINGLE_NOT_REPORTED);
    CHECK(model.reads == READS_PER_CHANNEL); // stops at the failing channel

    reset();
    model.faultChannel          = last;
    model.singleAsUncorrectable = true;
    expectFailure("single as non-correctable", last, sm::ECC_TEST_SINGLE_UNCORRECTABLE);

#if NX_ECC_LATENT_TEST_DOUBLE_BIT
    reset();
    model.faultChannel   = last;
    model.noDoubleRecord = true;
    expectFailure("no double-bit record", last, sm::ECC_TEST_DOUBLE_NOT_DETECTED);
#endif

    reset();
    model.faultChannel = 0U;
    model.wrongAddress = true;
    expectFailure("wrong address", 0U, sm::ECC_TEST_ADDRESS);

    reset();
    model.faultChannel = last;
    model.eimLeftOn    = true;
    expectFailure("EIM left on", last, sm::ECC_TEST_EIM_LEFT_ON);

    reset();
    model.faultChannel = 0U;
    model.corruptsWord = true;
    expectFailure("word changed", 0U, sm::ECC_TEST_WORD_CHANGED);

    reset();
    ermRegisters.SR0.stuck = true;
    expectFailure("record does not clear", 0U, sm::ECC_TEST_RECORD_NOT_CLEARED);
}

void testPreconditions()
{
    // a real record before the test: left for the poll (SM-15), no injected read, CR0 untouched
    reset();
    ermRegisters.CR0       = CR0_OF[0];
    ermRegisters.SR0.value = SBC[0];
    expectFailure("record before", 0U, sm::ECC_TEST_PRECONDITION);
    CHECK(model.reads == 0U);
    CHECK(ermRegisters.SR0.value == SBC[0]);
    CHECK(ermRegisters.SR0.writes == 0U);
    CHECK(ermRegisters.CR0 == CR0_OF[0]);

    reset();
    ermRegisters.SR0.value = NCE[CHANNELS - 1U];
    sm::EccLatentTestResult const r = runOnWords();
    CHECK(r.context == sm::eccLatentTestContext(CHANNELS - 1U, sm::ECC_TEST_PRECONDITION));
    CHECK(ermRegisters.SR0.value == NCE[CHANNELS - 1U]);

    // an enabled EIM before the test: switched off, not passed
    reset();
    eimRegisters.EIMCR  = EIM_EIMCR_GEIEN_MASK;
    eimRegisters.EICHEN = ENABLE[0];
    expectFailure("EIM on before", 0U, sm::ECC_TEST_PRECONDITION);
    CHECK(model.reads == 0U);
}

void testCoverage()
{
    // no word in the array of the last channel
    reset();
    sm::EccTestWord const onlyFirst[1] = {{&words[0], 0U}};
    sm::EccLatentTestResult r          = sm::runEccLatentTest(onlyFirst, 1U);
    if (CHANNELS > 1U)
    {
        CHECK(r.context == sm::eccLatentTestContext(1U, sm::ECC_TEST_NOT_COVERED));
        CHECK(r.channels == 1U);
    }
    else
    {
        CHECK(r.context == 0U);
    }
    // a word in an array without ECC does not count
    reset();
    sm::EccTestWord const noEcc[1] = {{&words[0], sm::ECC_NO_CHANNEL}};
    r                              = sm::runEccLatentTest(noEcc, 1U);
    CHECK(r.context == sm::eccLatentTestContext(0U, sm::ECC_TEST_NOT_COVERED));
    CHECK(model.reads == 0U);
    // the first word of a channel is used
    reset();
    sm::EccTestWord const two[2] = {{&words[1], 0U}, {&words[0], 0U}};
    r                            = sm::runEccLatentTest(two, 2U);
    CHECK(words[1] == PATTERN);
    CHECK(words[0] == 0xFFFFFFFFU);
    CHECK(eimAllOff());
}

void testChannelOfAddress()
{
#ifdef NX_TEST_ONE_ECC_CHANNEL
    // S32K11x: channel 0 is SRAM_U, SRAM_L (MTB RAM) has no ECC
    CHECK(sm::eccChannelOfAddress(0x1FFFFC00U) == sm::ECC_NO_CHANNEL);
    CHECK(sm::eccChannelOfAddress(0x1FFFFFFCU) == sm::ECC_NO_CHANNEL);
    CHECK(sm::eccChannelOfAddress(0x20000000U) == 0U);
    CHECK(sm::eccChannelOfAddress(0x200037FCU) == 0U);
#else
    // S32K14x: channel 0 SRAM_L, channel 1 SRAM_U
    CHECK(sm::eccChannelOfAddress(0x1FFF8000U) == 0U);
    CHECK(sm::eccChannelOfAddress(0x1FFFFFFCU) == 0U);
    CHECK(sm::eccChannelOfAddress(0x20000000U) == 1U);
    CHECK(sm::eccChannelOfAddress(0x20006FFCU) == 1U);
#endif
}

void testContextLayout()
{
    CHECK(sm::eccLatentTestContext(0U, sm::ECC_TEST_NOT_CORRECTED) == 0x00000208U);
    CHECK(sm::eccLatentTestContext(1U, sm::ECC_TEST_NOT_COVERED) == 0x01020008U);
    // never mistaken for a RAM or flash source of the cyclic poll
    CHECK((sm::eccLatentTestContext(1U, 0x3FFU)
           & (sm::ECC_SOURCE_RAM_CHANNEL_0 | sm::ECC_SOURCE_RAM_CHANNEL_1 | sm::ECC_SOURCE_FLASH))
          == 0U);
}
} // namespace

int main()
{
    testHealthyPasses();
    testHardwareFaultsAreFound();
    testPreconditions();
    testCoverage();
    testChannelOfAddress();
    testContextLayout();
    std::printf("%d checks, %d failures (%u channel(s), double-bit case %s)\n", checks, failures,
                static_cast<unsigned>(CHANNELS), NX_ECC_LATENT_TEST_DOUBLE_BIT ? "on" : "off");
    return (failures == 0) ? 0 : 1;
}
