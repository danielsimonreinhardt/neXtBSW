/* SYS-73 (doc/safety/iso26262-gap.md section 6 task 18, SM-55): start-up latent-fault test of the RAM ECC through
 * the error injection module (EIM) of the S32K1 family.
 *
 * SM-15 relies on the ECC decoder of each SRAM array and on the error reporting module (ERM) that records its
 * events. Neither is ever exercised in normal operation, so a broken decoder or a broken record would stay latent
 * until the double-bit error it should have caught. The test makes both show that they work, once per start and
 * before the BSP runs:
 *
 * - per ECC channel of the derivative (ERM/EIM channel n = one SRAM array, [RM] Tables 21-1 and 22-1) one reserved
 *   RAM word of this image that lies in that array; the word is written with a pattern first (the write computes
 *   fresh check bits), the EIM only inverts bits on the read path, the stored word is never changed;
 * - single-bit case: one data bit inverted on one read - the value read must be the pattern (corrected), the ERM
 *   must record a single-bit correction of that channel with the word's address, and no non-correctable event;
 * - double-bit case: two data bits inverted - the ERM must record a non-correctable event of that channel with the
 *   word's address (only where the core can take such a read, see NX_ECC_LATENT_TEST_DOUBLE_BIT);
 * - the EIM is off after every injected read (checked, and switched off if it is not), the ERM record of the channel
 *   is cleared (write one to clear, exactly the channel's flags) and must be clear afterwards, the interrupt
 *   notification of the channel (ERM CR0) is off during the test and restored afterwards, and a plain read of the
 *   word returns the pattern without a new record.
 *
 * The injected read itself (enable, read, disable) is one register-only instruction sequence with all interrupts
 * masked (eimInjectedRead, target file EccLatentTestTarget.cpp): while a channel is enabled every read of its array
 * is inverted ([RM] 21.3.3), so no stack or other RAM access may fall into that window.
 *
 * The result is reported by the caller (main() of the family port, SafetyShell) to the safe supervisor as
 * INTEGRITY_EVENT_DMA_ECC_ERROR with context bit ECC_SOURCE_LATENT_TEST: S1 + S2, and after the configured number of
 * such resets in a row the reset-loop limit holds S1 (SYS-43). */

#pragma once

#include <cstdint>

// Double-bit case: on the S32K14x (Cortex-M4) a read of a word with a non-correctable error returns without a
// fault ([RM] Table 31-5; the sequence still masks faults and sets CCR.BFHFNMIGN in case the bus reports one). On
// the S32K11x (Cortex-M0+) the same read raises a hard fault, which the core cannot mask - there the test injects
// single-bit errors only (the double-bit case would need a DMA read, not implemented).
#ifndef NX_ECC_LATENT_TEST_DOUBLE_BIT
#if defined(__ARM_ARCH_6M__)
#define NX_ECC_LATENT_TEST_DOUBLE_BIT 0
#else
#define NX_ECC_LATENT_TEST_DOUBLE_BIT 1
#endif
#endif

namespace safety
{
namespace safe_memory
{
/** Failure bits of one channel (bits 8.. of the event context, see eccLatentTestContext()). */
enum EccLatentTestFailure : uint32_t
{
    ECC_TEST_PRECONDITION         = 0x001U, ///< EIM enabled or ERM record of the channel set before the test
    ECC_TEST_NOT_CORRECTED        = 0x002U, ///< single-bit case: the value read is not the stored pattern
    ECC_TEST_SINGLE_NOT_REPORTED  = 0x004U, ///< single-bit case: no single-bit correction recorded
    ECC_TEST_SINGLE_UNCORRECTABLE = 0x008U, ///< single-bit case: recorded as non-correctable
    ECC_TEST_DOUBLE_NOT_DETECTED  = 0x010U, ///< double-bit case: no non-correctable event recorded
    ECC_TEST_ADDRESS              = 0x020U, ///< the recorded address is not the test word
    ECC_TEST_EIM_LEFT_ON          = 0x040U, ///< the EIM was still enabled after an injected read
    ECC_TEST_RECORD_NOT_CLEARED   = 0x080U, ///< the ERM record of the channel stayed after the clear
    ECC_TEST_WORD_CHANGED         = 0x100U, ///< a plain read of the word afterwards differs or is recorded
    ECC_TEST_NOT_COVERED          = 0x200U, ///< no test word of this image lies in the array of the channel
};

constexpr uint32_t ECC_NO_CHANNEL = 0xFFU;

/** A reserved test word and the ECC channel whose array holds it (ECC_NO_CHANNEL: an array without ECC). */
struct EccTestWord
{
    uint32_t volatile* word;
    uint32_t channel;
};

struct EccLatentTestResult
{
    uint32_t channels; ///< channels that passed
    uint32_t context;  ///< 0 = passed; else the context of the event (eccLatentTestContext())
};

/** ECC channels of the derivative (ERM channels of the device header: 2 on the S32K14x, 1 on the S32K11x). */
uint32_t eccChannelCount();

/** The ECC channel whose SRAM array holds `address` ([RM] Tables 21-1, 22-1), or ECC_NO_CHANNEL. */
uint32_t eccChannelOfAddress(uint32_t address);

/** Event context of a failure: ECC_SOURCE_LATENT_TEST (bit 3), failure bits in 17..8, channel in 27..24. */
uint32_t eccLatentTestContext(uint32_t channel, uint32_t failures);

/** Tests one channel with its word (EccLatentTestFailure bits, 0 = passed). Supervisor mode, before the BSP. */
uint32_t testEccChannel(uint32_t channel, uint32_t volatile* word);

/** Tests every channel with the first word of `words` that lies in its array; stops at the first failure. */
EccLatentTestResult runEccLatentTest(EccTestWord const* words, uint32_t count);

/** The test with the reserved words of this image (target, EccLatentTestTarget.cpp). */
EccLatentTestResult eccLatentTestAtStart();

/**
 * One read of `word` with the injection channels `channelEnable` (EIM EICHEN value) enabled: enables the channel,
 * then the EIM, reads, disables the EIM, then the channel - register-only, with all interrupts masked. The masks of
 * the channel descriptor are set by the caller before. Returns the value read. Target: EccLatentTestTarget.cpp;
 * the host test provides a model.
 */
uint32_t eimInjectedRead(uint32_t channelEnable, uint32_t volatile const* word);
} // namespace safe_memory
} // namespace safety
