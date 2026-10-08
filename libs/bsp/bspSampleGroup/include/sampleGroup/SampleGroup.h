// SPDX-License-Identifier: Apache-2.0
/* IO-62: L1 contract of a sample group (doc/architecture/fast-control-loop.md section 3.1) - a named set of analogue
 * channels converted after every hardware trigger of a PWM group, board and family neutral.
 *
 * What the contract fixes for every family implementation (s32k1xx: platforms/s32k1xx/bsp/bspSampleGroup, trigger
 * chain FTM -> TRGMUX -> PDB -> ADC; later stm32: timer TRGO -> injected group, rm4x: PWM SOC -> MibADC group):
 *
 *   init()        converters and sequencers configured, nothing triggers yet
 *   arm()         the chain follows the triggers of the PWM group; the PWM itself is not touched
 *   disarm()      the chain ignores the triggers again (route first, then the sequencers); the PWM is not touched
 *   group complete  one interrupt per trigger, raised after the LAST conversion of the whole group. The family entry
 *                 calls the consumer - a static type (architecture decision D1), no run-time registration, as with
 *                 the pin-interrupt dispatcher: Consumer::complete(Result const&). The fast-loop dispatcher of
 *                 ARCH-65 will be such a consumer; until then NoConsumer only lets the family count.
 *   result        raw values in the order of the project (sample_groups.<name>.channels), the completion count,
 *                 the error bits of this completion and the counter of the PWM group at entry (timer ticks)
 *
 * What the contract deliberately does not know: FOC, phases, motors, registers. Nothing here converts a raw value;
 * the dispatcher of the fast loop (IO-63/IO-64) does that.
 *
 * Bookkeeping is the neutral part every family implementation shares (host tested in
 * libs/bsp/bspSampleGroup/test/host): counts per arm, completion, error kind and recovery, and the sequence number
 * a consumer uses to see a missed completion (a jump by more than one).
 */
#pragma once

#include <cstdint>

namespace bsp
{
namespace sampleGroup
{
/** Error bits of one completion (Result::errors). */
enum Error : uint8_t
{
    ERROR_NONE               = 0U,
    ERROR_SEQUENCE           = 1U, // a conversion was triggered while the previous one of its converter was running
    ERROR_CONVERSION_MISSING = 2U, // another converter of the group had not finished when the group completed
};

/** What a consumer gets at "group complete". Valid only during Consumer::complete(). */
struct Result
{
    uint16_t const* raw; // raw values in project order
    uint8_t count;       // number of raw values
    uint8_t errors;      // Error bits of this completion; values of a completion with errors are not trustworthy
    uint32_t sequence;   // completions since the last arm(), 1 for the first; wraps
    uint32_t timerTicks; // counter of the PWM group at the entry of the interrupt
};

struct Statistics
{
    uint32_t arms;               // arm() calls that armed the chain
    uint32_t completions;        // group complete interrupts since init
    uint32_t sequenceErrors;     // completions with ERROR_SEQUENCE
    uint32_t missingConversions; // completions with ERROR_CONVERSION_MISSING
    uint32_t recoveries;         // restarts of the sequencers after a sequence error
};

/** Consumer that takes nothing (the family still counts). */
struct NoConsumer
{
    static void complete(Result const&) {}
};

/** Neutral bookkeeping of one sample group; no hardware, single writer (the interrupt of the group). */
class Bookkeeping
{
public:
    /** The chain was armed: the sequence starts again at 1. */
    void armed()
    {
        _sequence = 0U;
        ++_stats.arms;
    }

    /** One group complete with its error bits; returns the sequence number of this completion. */
    uint32_t complete(uint8_t const errors)
    {
        ++_stats.completions;
        if ((errors & ERROR_SEQUENCE) != 0U)
        {
            ++_stats.sequenceErrors;
        }
        if ((errors & ERROR_CONVERSION_MISSING) != 0U)
        {
            ++_stats.missingConversions;
        }
        ++_sequence;
        if (_sequence == 0U)
        {
            _sequence = 1U; // 0 is "none since arm" for a reader; the wrap skips it
        }
        return _sequence;
    }

    void recovered() { ++_stats.recoveries; }

    uint32_t sequence() const { return _sequence; }

    Statistics const& statistics() const { return _stats; }

    void resetStatistics() { _stats = Statistics{}; }

private:
    Statistics _stats{};
    uint32_t _sequence = 0U;
};

/** Completions a consumer missed between two sequence numbers (0: none; wrap of the counter included). */
constexpr uint32_t missedBetween(uint32_t const previous, uint32_t const current)
{
    if (previous == 0U)
    {
        return 0U; // first completion after arm()
    }
    uint32_t const step = (current >= previous) ? (current - previous) : ((UINT32_MAX - previous) + current);
    return (step == 0U) ? 0U : (step - 1U);
}

} // namespace sampleGroup
} // namespace bsp
