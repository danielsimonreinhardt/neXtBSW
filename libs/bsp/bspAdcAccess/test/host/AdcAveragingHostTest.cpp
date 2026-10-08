/* Host test of the averaging over one PWM period (adc::AdcAveraging, IO-31), no target hardware needed.
 *
 * Model: a virtual time source (every poll costs a few ns, every conversion its conversion time) and a
 * converter that samples a signal model at the current virtual time and quantizes it like a 12-bit ADC.
 *
 * Signal model (synthetic, shaped after the IO-15 load test, not measured data): DC link current of a
 * three-phase bridge with centre-aligned PWM. Two active-vector pulses per period carry the motor current,
 * the zero vectors (period start with all low sides on, period centre with all high sides on) carry none.
 * The shunt amplifier path is modelled as a first-order low pass. The amplifier output is
 * U = 2500 mV - 50 mV/A * I (one LSB = 1 mV = 20 mA), the same characteristic as the eval board channel,
 * but no eval board value enters a decision of the code under test.
 *
 * Profiles (neither is the eval board configuration):
 *   A  20 kHz (50 us), conversion 1709 ns, pulses 2 x 5 % of 5 A, tau 12 us
 *   B  25 kHz (40 us), conversion 1741 ns, pulses 2 x 4 % of 8 A, tau 6 us
 *
 * Cases:
 *   asynchronous   one conversion at a random phase (today): scatter by the ripple
 *   synchronous    one conversion at the counter start (FTM initialization trigger, PDB delay 0): no scatter,
 *                  but the value of the zero vector, not the mean - the reason IO-31 averages
 *   averaged       N equally spaced conversions over one period at a random start phase: the mean within 2 LSB,
 *                  the spread falls with N (harmonics k * N of the period alias into the mean)
 *   cross-checks   a window that is not one period (10 % too long, a quarter period) scatters again; an
 *                  unfiltered pulse train keeps an error below pulses * amplitude / N
 *   behaviour      preemption (late samples counted), failed conversion, stalled time source, samples <= 1,
 *                  rounding, time wrap, balanced exclusive sections
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsp/bspAdcAccess/include -isystem external/openbsw/libs/bsw/bsp/include \
 *       libs/bsp/bspAdcAccess/test/host/AdcAveragingHostTest.cpp -o build/host/adc_averaging_test.exe
 *   build/host/adc_averaging_test.exe
 */

#include "adc/AdcAveraging.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

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

constexpr double MV_PER_A   = 50.0;
constexpr double ZERO_MV    = 2500.0;
constexpr double MA_PER_LSB = 1000.0 / MV_PER_A; // 1 LSB = 1 mV here: 20 mA

/* Periodic DC link current, tabulated over one period in steps of 10 ns (steady state of the low pass). */
struct Signal
{
    uint32_t periodNs;
    std::vector<double> amps; // one value per 10 ns
    double meanA;

    double at(uint64_t const t) const
    {
        return amps[static_cast<size_t>((t % periodNs) / 10U)];
    }
};

/* Two active-vector pulses per period, centred at 1/4 and 3/4 of the period (centre-aligned PWM with the zero
 * vectors at the counter start and at the centre), each `pulse` of the period long; tau 0: no filter. */
Signal makeSignal(uint32_t const periodNs, double const pulse, double const amps, double const tauNs)
{
    Signal s{periodNs, std::vector<double>(periodNs / 10U), 0.0};
    size_t const n = s.amps.size();
    auto raw       = [&](size_t const i) {
        double const phase = static_cast<double>(i) / static_cast<double>(n);
        bool const on      = (std::fabs(phase - 0.25) < pulse / 2.0) || (std::fabs(phase - 0.75) < pulse / 2.0);
        return on ? amps : 0.0;
    };
    double y = 0.0;
    for (int period = 0; period < 40; ++period)
    {
        for (size_t i = 0; i < n; ++i)
        {
            if (tauNs <= 0.0)
            {
                y = raw(i);
            }
            else
            {
                y += (raw(i) - y) * (10.0 / tauNs);
            }
            if (period == 39)
            {
                s.amps[i] = y;
            }
        }
    }
    double sum = 0.0;
    for (double const a : s.amps)
    {
        sum += a;
    }
    s.meanA = sum / static_cast<double>(n);
    return s;
}

uint16_t quantize(double const amps)
{
    double const mv = ZERO_MV - (MV_PER_A * amps);
    return static_cast<uint16_t>(std::lround(mv * 4095.0 / 4096.0));
}

double toAmps(uint16_t const raw) { return (ZERO_MV - (static_cast<double>(raw) * 4096.0 / 4095.0)) / MV_PER_A; }

/* Virtual time; nowNs() is the lock policy's time source of adc::AdcAccess. */
struct VirtualLock
{
    using State = uint32_t;

    uint64_t now       = 0U;
    uint32_t pollNs    = 40U;  // one poll of the time source (loop, call, DWT read)
    bool stalled       = false;
    int open           = 0;    // balance of the exclusive sections
    int maxOpen        = 0;

    uint32_t nowNs()
    {
        uint32_t const t = static_cast<uint32_t>(now);
        if (!stalled)
        {
            now += pollNs;
        }
        return t;
    }

    State enterExclusive()
    {
        ++open;
        maxOpen = (open > maxOpen) ? open : maxOpen;
        return 0U;
    }

    void exitExclusive(State) { --open; }
};

struct Converter
{
    Converter(VirtualLock& c, Signal const* s, uint32_t const ns) : clock(c), signal(s), conversionNs(ns) {}

    VirtualLock& clock;
    Signal const* signal;
    uint32_t conversionNs;
    int calls           = 0;
    int failAt          = -1;       // call index that fails
    int delayAt         = -1;       // call index before which a preemption delays the caller
    uint32_t delayNs    = 0U;
    std::vector<uint16_t> script;   // fixed raw values instead of the signal

    bsp::BspReturnCode operator()(uint16_t& raw)
    {
        int const call = calls++;
        if (call == delayAt)
        {
            clock.now += delayNs;
        }
        if (call == failAt)
        {
            clock.now += conversionNs;
            return bsp::BSP_ERROR;
        }
        if (!script.empty())
        {
            raw = script[static_cast<size_t>(call) % script.size()];
        }
        else
        {
            raw = quantize(signal->at(clock.now));
        }
        clock.now += conversionNs;
        return bsp::BSP_OK;
    }
};

struct Spread
{
    double minA  = 1e9;
    double maxA  = -1e9;
    double sumA  = 0.0;
    int count    = 0;
    int errors   = 0;

    void add(uint16_t const raw)
    {
        double const a = toAmps(raw);
        minA           = (a < minA) ? a : minA;
        maxA           = (a > maxA) ? a : maxA;
        sumA += a;
        ++count;
    }

    double width() const { return maxA - minA; }

    double mean() const { return sumA / count; }
};

uint32_t lcg(uint32_t& state)
{
    state = (state * 1664525U) + 1013904223U;
    return state >> 8;
}

/* `reads` reads with random start phases (and a random start time up to 1 ms). */
Spread run(Signal const& sig, uint32_t const conversionNs, adc::AveragingSpec const spec, int const reads,
           bool const synchronous = false)
{
    VirtualLock lock;
    adc::AdcAveraging<VirtualLock> avg(lock);
    Converter conv{lock, &sig, conversionNs};
    Spread s;
    uint32_t seed = 12345U;
    for (int i = 0; i < reads; ++i)
    {
        uint64_t const base = (lock.now / sig.periodNs + 20U) * sig.periodNs;
        lock.now            = synchronous ? base : base + (lcg(seed) % sig.periodNs);
        uint16_t value      = 0U;
        if (avg.read(spec, conv, value) == bsp::BSP_OK)
        {
            s.add(value);
        }
        else
        {
            ++s.errors;
        }
    }
    CHECK(lock.open == 0);
    CHECK(lock.maxOpen <= 1);
    return s;
}

adc::AveragingSpec spec(uint32_t const periodNs, uint16_t const samples)
{
    return adc::AveragingSpec{samples, (periodNs + samples / 2U) / samples};
}

void report(char const* what, Signal const& sig, Spread const& s)
{
    std::printf("  %-44s mean %+.3f A (true %+.3f A), spread %.3f A = %.0f LSB\n", what, s.mean(), sig.meanA,
                s.width(), s.width() * 1000.0 / MA_PER_LSB);
}

constexpr double LSB_A = MA_PER_LSB / 1000.0;

struct Expect
{
    uint16_t samples;
    double maxSpreadLsb;
};

void testProfile(char const* name, uint32_t const periodNs, uint32_t const conversionNs, double const pulse,
                 double const amps, double const tauNs, std::vector<Expect> const& expected)
{
    std::printf("profile %s: period %u ns, conversion %u ns\n", name, periodNs, conversionNs);
    Signal const sig = makeSignal(periodNs, pulse, amps, tauNs);

    // today: one conversion at a random phase -> scatter by the ripple
    Spread const single = run(sig, conversionNs, adc::AveragingSpec{1U, 0U}, 2000);
    report("asynchronous single conversion", sig, single);
    CHECK(single.errors == 0);
    CHECK(single.width() > 0.3);                                   // the ripple is visible
    CHECK(std::fabs(single.mean() - sig.meanA) < 0.05);            // but random phases average out over many reads

    // PWM synchronous single conversion at the counter start: deterministic, but the zero vector
    Spread const sync = run(sig, conversionNs, adc::AveragingSpec{1U, 0U}, 200, true);
    report("synchronous single conversion (counter start)", sig, sync);
    CHECK(sync.width() <= LSB_A + 1e-9);
    CHECK(std::fabs(sync.mean() - sig.meanA) > 0.05);              // biased: not the mean current

    // averaged over one period: the spread falls with the number of samples (harmonics k * N of the period alias
    // into the mean, so a sharper ripple needs more samples), the mean stays the true mean
    double previous = single.width();
    for (Expect const& e : expected)
    {
        Spread const av = run(sig, conversionNs, spec(periodNs, e.samples), 2000);
        char what[64];
        std::snprintf(what, sizeof(what), "averaged, %u samples over one period", e.samples);
        report(what, sig, av);
        CHECK(e.samples * conversionNs <= periodNs);               // the profile only lists counts that fit
        CHECK(av.errors == 0);
        CHECK(av.width() <= e.maxSpreadLsb * LSB_A + 1e-9);
        CHECK(av.width() < previous);
        CHECK(std::fabs(av.mean() - sig.meanA) <= 2.0 * LSB_A);
        previous = av.width();
    }
    CHECK(previous * 10.0 < single.width());                       // the largest count: an order of magnitude
    // cross-checks: the window must be one period of the PWM
    Spread const wrong = run(sig, conversionNs, adc::AveragingSpec{16U, (periodNs * 11U / 10U) / 16U}, 2000);
    report("cross-check: window 10 % too long", sig, wrong);
    CHECK(wrong.width() > 4.0 * LSB_A);                            // 7 LSB with 8 samples in profile A
    Spread const half = run(sig, conversionNs, adc::AveragingSpec{8U, (periodNs / 2U) / 8U + 1U}, 2000);
    report("cross-check: window half a period", sig, half);
    // half a period covers exactly one of the two symmetric pulses: this model hides that case by symmetry,
    // a quarter period does not
    Spread const quarter = run(sig, conversionNs, adc::AveragingSpec{4U, (periodNs / 4U) / 4U}, 2000);
    report("cross-check: window a quarter period", sig, quarter);
    CHECK(quarter.width() > 4.0 * LSB_A);
}

void testUnfilteredPulse()
{
    // no low pass: a rectangular current pulse. N samples hit the pulse floor or ceil(N * duty) times, the error
    // of one read stays below pulses * amplitude / N (plus quantization), here two pulses per period
    Signal const sig = makeSignal(50000U, 0.05, 5.0, 0.0);
    for (uint16_t const n : {uint16_t{8U}, uint16_t{16U}})
    {
        Spread const av = run(sig, 1709U, spec(50000U, n), 2000);
        char what[64];
        std::snprintf(what, sizeof(what), "unfiltered pulse, %u samples", n);
        report(what, sig, av);
        CHECK(std::fabs(av.maxA - sig.meanA) <= 2.0 * 5.0 / n + 2.0 * LSB_A);
        CHECK(std::fabs(av.minA - sig.meanA) <= 2.0 * 5.0 / n + 2.0 * LSB_A);
    }
}

void testSingleConversionIsUnchanged()
{
    VirtualLock lock;
    adc::AdcAveraging<VirtualLock> avg(lock);
    Converter conv{lock, nullptr, 1000U};
    conv.script     = {1234U};
    uint16_t value  = 0U;
    CHECK(avg.read(adc::AveragingSpec{1U, 0U}, conv, value) == bsp::BSP_OK);
    CHECK(value == 1234U);
    CHECK(conv.calls == 1);
    CHECK(avg.read(adc::AveragingSpec{0U, 500U}, conv, value) == bsp::BSP_OK);
    CHECK(conv.calls == 2);
    CHECK(avg.getStatistics().reads == 0U);                        // no averaging statistics for plain reads
    CHECK(lock.open == 0);
}

void testRoundingAndSpacing()
{
    VirtualLock lock;
    lock.pollNs = 10U;
    adc::AdcAveraging<VirtualLock> avg(lock);
    Converter conv{lock, nullptr, 700U};
    conv.script    = {100U, 101U, 101U, 101U};  // mean 100.75 -> 101
    uint16_t value = 0U;
    CHECK(avg.read(adc::AveragingSpec{4U, 2500U}, conv, value) == bsp::BSP_OK);
    CHECK(value == 101U);
    conv.script = {100U, 100U, 100U, 101U};     // mean 100.25 -> 100
    CHECK(avg.read(adc::AveragingSpec{4U, 2500U}, conv, value) == bsp::BSP_OK);
    CHECK(value == 100U);
    conv.script = {4095U};                      // 64 full-scale samples do not overflow the sum
    CHECK(avg.read(adc::AveragingSpec{64U, 800U}, conv, value) == bsp::BSP_OK);
    CHECK(value == 4095U);
    adc::AdcAveragingStatistics const s = avg.getStatistics();
    CHECK(s.reads == 3U);
    CHECK(s.late == 0U);
    // the last sample instant is (N - 1) spacings after the first, up to one poll
    CHECK(s.maxWindowNs >= 63U * 800U);
    CHECK(s.maxWindowNs <= 63U * 800U + 20U);
    CHECK(s.maxLateNs <= 20U);
    avg.resetStatistics();
    CHECK(avg.getStatistics().reads == 0U);
    CHECK(avg.getStatistics().maxWindowNs == 0U);
    CHECK(lock.open == 0);
}

void testPreemption()
{
    Signal const sig = makeSignal(50000U, 0.05, 5.0, 12000.0);
    VirtualLock lock;
    adc::AdcAveraging<VirtualLock> avg(lock);
    Converter conv{lock, &sig, 1709U};
    conv.delayAt   = 3;                         // a higher priority task runs 20 us before the 4th sample
    conv.delayNs   = 20000U;
    uint16_t value = 0U;
    CHECK(avg.read(spec(50000U, 8U), conv, value) == bsp::BSP_OK);
    adc::AdcAveragingStatistics const s = avg.getStatistics();
    CHECK(s.reads == 1U);
    CHECK(s.late == 1U);
    CHECK(s.maxLateNs >= 20000U - 6250U);       // the samples after the delay are late until the grid catches up
    CHECK(s.errors == 0U);
    CHECK(conv.calls == 8);
    // still a usable value: the samples keep their grid after the delay
    CHECK(std::fabs(toAmps(value) - sig.meanA) < 0.3);
}

void testFailedConversion()
{
    VirtualLock lock;
    adc::AdcAveraging<VirtualLock> avg(lock);
    Converter conv{lock, nullptr, 1000U};
    conv.script    = {500U};
    conv.failAt    = 5;
    uint16_t value = 777U;
    CHECK(avg.read(adc::AveragingSpec{8U, 5000U}, conv, value) == bsp::BSP_ERROR);
    CHECK(value == 777U);                       // no substitute value (ADC contract, property 4)
    CHECK(conv.calls == 6);                     // stops at the failure
    CHECK(avg.getStatistics().errors == 1U);
    CHECK(avg.getStatistics().reads == 0U);
    CHECK(lock.open == 0);
}

void testStalledTimeSource()
{
    VirtualLock lock;
    adc::AdcAveraging<VirtualLock> avg(lock);
    Converter conv{lock, nullptr, 0U};          // the converter does not advance the time either
    conv.script    = {500U};
    lock.stalled   = true;
    uint16_t value = 777U;
    CHECK(avg.read(adc::AveragingSpec{8U, 5000U}, conv, value) == bsp::BSP_ERROR);
    CHECK(value == 777U);
    CHECK(conv.calls == 1);                     // first sample at t0, then the wait gives up
    CHECK(avg.getStatistics().errors == 1U);
    CHECK(lock.open == 0);
}

void testTimeWrap()
{
    // the 32-bit time source wraps inside the window
    Signal const sig = makeSignal(50000U, 0.05, 5.0, 12000.0);
    VirtualLock lock;
    adc::AdcAveraging<VirtualLock> avg(lock);
    Converter conv{lock, &sig, 1709U};
    lock.now       = 0xFFFFFFFFULL - 20000U;
    uint16_t value = 0U;
    CHECK(avg.read(spec(50000U, 8U), conv, value) == bsp::BSP_OK);
    CHECK(conv.calls == 8);
    CHECK(avg.getStatistics().late == 0U);
    CHECK(avg.getStatistics().maxWindowNs <= 7U * 6250U + 100U);
    CHECK(std::fabs(toAmps(value) - sig.meanA) <= 3.0 * LSB_A);
}

} // namespace

int main()
{
    testProfile("A", 50000U, 1709U, 0.05, 5.0, 12000.0, {{4U, 24.0}, {8U, 10.0}, {16U, 2.0}});
    testProfile("B", 40000U, 1741U, 0.04, 8.0, 6000.0, {{8U, 20.0}, {16U, 6.0}, {22U, 2.0}});
    testUnfilteredPulse();
    testSingleConversionIsUnchanged();
    testRoundingAndSpacing();
    testPreemption();
    testFailedConversion();
    testStalledTimeSource();
    testTimeWrap();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
