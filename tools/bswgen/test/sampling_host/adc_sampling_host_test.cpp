/* Host test of the generated bsp/adc/analogInputSamplingConfiguration.h (IO-31), run by test_bswgen.py
 * (BswGenAdcSamplingHostTest) for the synthetic profile testproject_adc_sampling and the eval board demo.
 *
 * The test compiles the generated table together with adc/AdcAveraging.h (-Werror, -Wconversion) and checks:
 *   - one entry per channel (NX_TEST_CHANNELS, from the generator's channel list),
 *   - the averaged channel (NX_TEST_CHANNEL) covers the realized PWM period (NX_TEST_PERIOD_PS, picoseconds, from
 *     the FTM ticks) within the rounding of the spacing, every other channel converts once,
 *   - with a ripple of exactly that period (fundamental plus harmonics 2..5, virtual time), the averaged reads of
 *     the generated spacing agree within 2 LSB (rounding of the single samples) at any start phase, a single
 *     conversion scatters by the ripple.
 */

#include <adc/AdcAveraging.h>

#include <cmath>
#include <cstdint>
#include <cstdio>

#include "bsp/adc/analogInputSamplingConfiguration.h"

#ifndef NX_TEST_CHANNELS
#error "NX_TEST_CHANNELS: number of channels of the generated table"
#endif

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

#if NX_ADC_AVERAGED_CHANNEL_COUNT > 0
constexpr double PI = 3.14159265358979323846;

struct Clock
{
    using State = int;
    uint64_t nowPs = 0U; // picoseconds, so that the realized period is exact
    uint32_t nowNs()
    {
        nowPs += 40000U; // one poll: 40 ns
        return static_cast<uint32_t>(nowPs / 1000U);
    }
    State enterExclusive() { return 0; }
    void exitExclusive(State) {}
};

// ripple with the period of the PWM group: 2000 raw +- 300 (fundamental and harmonics 2..5)
uint16_t sample(uint64_t const ps)
{
    double const phase = 2.0 * PI * static_cast<double>(ps % NX_TEST_PERIOD_PS) / static_cast<double>(NX_TEST_PERIOD_PS);
    double v           = 2000.0;
    for (int k = 1; k <= 5; ++k)
    {
        v += (300.0 / k) * std::sin((k * phase) + (0.7 * k));
    }
    return static_cast<uint16_t>(std::lround(v));
}

void spreadOf(adc::AveragingSpec const& spec, uint16_t& lo, uint16_t& hi)
{
    Clock clock;
    adc::AdcAveraging<Clock> avg(clock);
    lo = 0xFFFFU;
    hi = 0U;
    for (uint64_t start = 0U; start < NX_TEST_PERIOD_PS; start += NX_TEST_PERIOD_PS / 97U)
    {
        clock.nowPs          = (1000000000ULL * 7U) + start;
        uint16_t value       = 0U;
        auto const conversion = [&clock](uint16_t& raw) {
            raw = sample(clock.nowPs);
            clock.nowPs += 1700000U; // 1.7 us
            return bsp::BSP_OK;
        };
        CHECK(avg.read(spec, conversion, value) == bsp::BSP_OK);
        lo = (value < lo) ? value : lo;
        hi = (value > hi) ? value : hi;
    }
}
#endif
} // namespace

int main()
{
#if NX_ADC_AVERAGED_CHANNEL_COUNT > 0
    constexpr size_t count = sizeof(analogInputSamplingConfiguration) / sizeof(adc::AveragingSpec);
    CHECK(count == NX_TEST_CHANNELS);
    unsigned averaged = 0U;
    for (size_t i = 0U; i < count; ++i)
    {
        adc::AveragingSpec const& s = analogInputSamplingConfiguration[i];
        if (s.samples > 1U)
        {
            ++averaged;
            CHECK(i == NX_TEST_CHANNEL);
        }
        else
        {
            CHECK(s.samples == 1U);
            CHECK(s.spacingNs == 0U);
        }
    }
    CHECK(averaged == NX_ADC_AVERAGED_CHANNEL_COUNT);
    adc::AveragingSpec const& spec = analogInputSamplingConfiguration[NX_TEST_CHANNEL];
    // samples * spacing is the realized period, up to the rounding of the spacing to whole ns
    double const windowPs = static_cast<double>(spec.samples) * spec.spacingNs * 1000.0;
    CHECK(std::fabs(windowPs - static_cast<double>(NX_TEST_PERIOD_PS)) <= spec.samples * 500.0);
    std::printf("channel %u: %u samples every %u ns, period %.1f ns\n", static_cast<unsigned>(NX_TEST_CHANNEL),
                spec.samples, spec.spacingNs, static_cast<double>(NX_TEST_PERIOD_PS) / 1000.0);

    uint16_t lo = 0U;
    uint16_t hi = 0U;
    spreadOf(spec, lo, hi);
    std::printf("averaged: %u..%u\n", lo, hi);
    CHECK((hi - lo) <= 2);                                        // rounding of the single samples
    CHECK(std::abs(static_cast<int>(lo) - 2000) <= 2);
    CHECK(std::abs(static_cast<int>(hi) - 2000) <= 2);
    spreadOf(adc::AveragingSpec{1U, 0U}, lo, hi);
    std::printf("single conversion: %u..%u\n", lo, hi);
    CHECK((hi - lo) > 300);
#else
    std::printf("no averaged channel\n");
#endif
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
