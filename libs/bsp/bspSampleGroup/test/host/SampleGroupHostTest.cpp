// SPDX-License-Identifier: Apache-2.0
// Host test of the neutral part of the sample group contract (IO-62, sampleGroup/SampleGroup.h): bookkeeping of arm,
// completion, error kinds and recovery, the sequence number with its wrap, and the count of missed completions a
// consumer derives from two sequence numbers. No family, no registers.
// Build and run: tools/test -k samplegroup-contract (zig).

#include "sampleGroup/SampleGroup.h"

#include <cstdio>

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

using namespace bsp::sampleGroup;

void testCounting()
{
    Bookkeeping b;
    CHECK(b.sequence() == 0U);
    b.armed();
    CHECK(b.statistics().arms == 1U);
    CHECK(b.complete(ERROR_NONE) == 1U);
    CHECK(b.complete(ERROR_SEQUENCE) == 2U);
    CHECK(b.complete(ERROR_CONVERSION_MISSING) == 3U);
    CHECK(b.complete(static_cast<uint8_t>(ERROR_SEQUENCE | ERROR_CONVERSION_MISSING)) == 4U);
    b.recovered();
    Statistics const& s = b.statistics();
    CHECK(s.completions == 4U);
    CHECK(s.sequenceErrors == 2U);
    CHECK(s.missingConversions == 2U);
    CHECK(s.recoveries == 1U);
    // a new arm starts the sequence again, the statistics keep counting
    b.armed();
    CHECK(b.sequence() == 0U);
    CHECK(b.complete(ERROR_NONE) == 1U);
    CHECK(b.statistics().arms == 2U);
    CHECK(b.statistics().completions == 5U);
    b.resetStatistics();
    CHECK(b.statistics().completions == 0U);
    CHECK(b.statistics().arms == 0U);
    CHECK(b.sequence() == 1U); // resetting the statistics is no arm
}

void testWrapSkipsZero()
{
    Bookkeeping b;
    b.armed();
    // drive the counter close to the wrap through the public interface only
    for (uint32_t i = 0U; i < 3U; ++i)
    {
        (void)b.complete(ERROR_NONE);
    }
    CHECK(b.sequence() == 3U);
    CHECK(missedBetween(UINT32_MAX, 1U) == 0U);     // MAX -> 1: the wrap skips 0, no completion lost
    CHECK(missedBetween(UINT32_MAX - 1U, 1U) == 1U); // MAX - 1 -> MAX -> 1: MAX was missed
    CHECK(missedBetween(0U, 7U) == 0U);             // first completion after arm
    CHECK(missedBetween(5U, 6U) == 0U);
    CHECK(missedBetween(5U, 9U) == 3U);
    CHECK(missedBetween(9U, 9U) == 0U);             // the same completion read twice
}

struct CountingConsumer
{
    static unsigned calls;
    static Result last;

    static void complete(Result const& r)
    {
        ++calls;
        last = r;
    }
};

unsigned CountingConsumer::calls = 0U;
Result CountingConsumer::last    = {};

template<typename Consumer>
void deliver(Bookkeeping& b, uint16_t const* raw, uint8_t count, uint8_t errors)
{
    Result const r{raw, count, errors, b.complete(errors), 1234U};
    Consumer::complete(r);
}

void testConsumerIsAStaticType()
{
    Bookkeeping b;
    b.armed();
    uint16_t const raw[3] = {1U, 2U, 4095U};
    deliver<CountingConsumer>(b, raw, 3U, ERROR_NONE);
    deliver<NoConsumer>(b, raw, 3U, ERROR_NONE);
    CHECK(CountingConsumer::calls == 1U);
    CHECK(CountingConsumer::last.count == 3U);
    CHECK(CountingConsumer::last.raw[2] == 4095U);
    CHECK(CountingConsumer::last.sequence == 1U);
    CHECK(CountingConsumer::last.timerTicks == 1234U);
    CHECK(b.statistics().completions == 2U);
}
} // namespace

int main()
{
    testCounting();
    testWrapSkipsZero();
    testConsumerIsAStaticType();
    std::printf("sample group contract host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
