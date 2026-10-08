// Host test of bsp/capture/InputCapture.h/.cpp with the configuration bswgen generates for a project that uses
// input_capture (IO-03). Every FTM is a RAM model with the real register layout (S32K148_FTM.h) and a model of
// the dual edge capture hardware: it feeds a PWM signal into the pin of the even channel, captures the edges
// the driver armed (one-shot, edge per channel), mirrors CHF in CnSC and STATUS, disarms DECAP when the pair
// completed and sets TOF when the free running counter wraps.
//
// The test takes its expectations from the generated configuration (clock, period range, timeout, update
// cycle), so it runs against any profile: the eval board demo (one channel on FTM2) and a test profile with
// two channels on two timers. Checked: register configuration after init, edges and the alternating
// measurements, frequency/period/duty of a normal signal, a very small and a very large duty cycle, counter
// overflows, a missing signal, a period below and above the configured range, and an update() that comes too
// late. Build and run: tools/bswgen/test_bswgen.py (BswGenCaptureHostTest).

#include "bsp/capture/InputCapture.h"

#include <cstdio>
#include <cstring>

#if __has_include("io/Io.h")
#include "io/Io.h"

namespace bios
{
// InputCapture::init() muxes the pin of every channel (IO-48); the pin itself is not part of this test.
bsp::BspReturnCode Io::setDefaultConfiguration(uint16_t const io)
{
    static_cast<void>(io);
    return bsp::BSP_OK;
}
} // namespace bios
#endif

FTM_Type fakeFtm[8] = {};

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

using bios::InputCapture;
namespace cap = bios::capture;

constexpr uint16_t CHANNELS = static_cast<uint16_t>(cap::_captureChannelMax);

// ------------------------------------------------------------------ model of the capture hardware and the signal

struct Signal
{
    uint32_t period  = 0U;
    uint32_t high    = 0U;
    bool present     = false;
    uint32_t nextRise = 0U;
    uint32_t nextFall = 0U;
};

Signal signals[CHANNELS];
uint32_t modelNow = 0U;

FTM_Type& regsOf(uint16_t const channel) { return cap::ftm(cap::CHANNELS[channel].timer); }

uint32_t armBitOf(uint16_t const channel)
{
    return cap::COMBINE_DECAP_PAIR << cap::pairShift(cap::CHANNELS[channel].channel);
}

bool edgeWanted(uint32_t const cnsc, bool const rising)
{
    return rising ? ((cnsc & FTM_CnSC_ELSA_MASK) != 0U) : ((cnsc & FTM_CnSC_ELSB_MASK) != 0U);
}

/** CHF is mirrored in CnSC and STATUS: a CnSC write with CHF = 0 clears the flag in both. */
void syncFlags()
{
    for (uint16_t i = 0U; i < CHANNELS; ++i)
    {
        FTM_Type& regs   = regsOf(i);
        uint8_t const ch = cap::CHANNELS[i].channel;
        for (uint8_t k = 0U; k < 2U; ++k)
        {
            if ((regs.CONTROLS[ch + k].CnSC & FTM_CnSC_CHF_MASK) == 0U)
            {
                regs.STATUS &= ~static_cast<uint32_t>(1UL << (ch + k));
            }
        }
    }
}

/** One edge on the pin of a capture channel at absolute model time `at`. */
void edge(uint16_t const channel, bool const rising, uint32_t const at)
{
    FTM_Type& regs = regsOf(channel);
    if ((regs.COMBINE & armBitOf(channel)) == 0U)
    {
        return; // not armed: the hardware ignores the edge
    }
    // The pair waits for its second edge exactly while the flag of the even channel is set; the driver clearing
    // that flag (it writes CnSC when it arms) restarts the pair, like the hardware does.
    uint8_t const base   = cap::CHANNELS[channel].channel;
    bool const second    = (regs.CONTROLS[base].CnSC & FTM_CnSC_CHF_MASK) != 0U;
    uint8_t const ch     = static_cast<uint8_t>(base + (second ? 1U : 0U));
    if (!edgeWanted(regs.CONTROLS[ch].CnSC, rising))
    {
        return;
    }
    regs.CONTROLS[ch].CnV = at & 0xFFFFU;
    regs.CONTROLS[ch].CnSC |= FTM_CnSC_CHF_MASK;
    regs.STATUS |= static_cast<uint32_t>(1UL << ch);
    if (second)
    {
        regs.COMBINE &= ~armBitOf(channel); // one-shot: the hardware disarms the pair
    }
}

/** Moves the model by `ticks` counter ticks, with all edges in between, and updates CNT and TOF. */
void advance(uint32_t const ticks)
{
    syncFlags();
    uint32_t const start  = modelNow;
    uint32_t const target = modelNow + ticks;
    while (true)
    {
        // next pending edge of any channel up to the end of this step (an edge that was processed moves one
        // period ahead, so the loop always makes progress)
        uint32_t next    = target + 1U;
        uint16_t channel = CHANNELS;
        bool rising      = true;
        for (uint16_t i = 0U; i < CHANNELS; ++i)
        {
            Signal const& s = signals[i];
            if (!s.present)
            {
                continue;
            }
            if ((s.nextRise <= target) && (s.nextRise < next))
            {
                next    = s.nextRise;
                channel = i;
                rising  = true;
            }
            if ((s.nextFall <= target) && (s.nextFall < next))
            {
                next    = s.nextFall;
                channel = i;
                rising  = false;
            }
        }
        if (channel == CHANNELS)
        {
            break;
        }
        edge(channel, rising, next);
        Signal& s = signals[channel];
        if (rising)
        {
            s.nextRise += s.period;
        }
        else
        {
            s.nextFall += s.period;
        }
        modelNow = next;
    }
    modelNow = target;
    for (uint8_t t = 0U; t < cap::TIMER_COUNT; ++t)
    {
        FTM_Type& regs = cap::ftm(t);
        if ((start >> 16U) != (target >> 16U))
        {
            regs.SC |= FTM_SC_TOF_MASK; // the counter wrapped at least once in this step
        }
        regs.CNT = target & 0xFFFFU;
    }
}

void setSignal(uint16_t const channel, uint32_t const period, uint32_t const high)
{
    Signal& s  = signals[channel];
    s.period   = period;
    s.high     = high;
    s.present  = true;
    s.nextRise = modelNow + 1U;
    s.nextFall = s.nextRise + high;
}

void stopSignal(uint16_t const channel) { signals[channel].present = false; }

cap::Timer const& limitsOf(uint16_t const channel) { return cap::TIMERS[cap::CHANNELS[channel].timer]; }

/** A period inside the range, divisible by 4, so that a quarter and a half of it are exact duty values. */
uint32_t normalPeriod(uint16_t const channel) { return 4U * (limitsOf(channel).maxPeriodTicks / 8U); }

/** Channel status and control without the capture flag the hardware sets (CHF is mirrored in CnSC). */
uint32_t edgeSelection(uint16_t const channel, uint8_t const offset)
{
    uint8_t const ch = static_cast<uint8_t>(cap::CHANNELS[channel].channel + offset);
    return regsOf(channel).CONTROLS[ch].CnSC & ~static_cast<uint32_t>(FTM_CnSC_CHF_MASK);
}

/** `cycles` update cycles of the project, with the model running in between. */
void run(uint16_t const channel, unsigned const cycles)
{
    for (unsigned i = 0U; i < cycles; ++i)
    {
        advance(limitsOf(channel).updateTicks);
        InputCapture::update();
    }
}

uint32_t expectedFrequencyMilliHz(uint16_t const channel, uint32_t const period)
{
    uint64_t const clock = limitsOf(channel).clockHz;
    return static_cast<uint32_t>((clock * 1000ULL + (period / 2U)) / period);
}

uint32_t expectedPeriodMicros(uint16_t const channel, uint32_t const period)
{
    uint64_t const clock = limitsOf(channel).clockHz;
    return static_cast<uint32_t>((static_cast<uint64_t>(period) * 1000000ULL + (clock / 2U)) / clock);
}

// ------------------------------------------------------------------------------------------------- test cases

void testInitialRegisters()
{
    for (uint8_t t = 0U; t < cap::TIMER_COUNT; ++t)
    {
        FTM_Type const& regs                      = cap::ftm(t);
        bios::tFtm::tFtmConfiguration const& cfg = cap::configuration(t);
        CHECK((regs.SC & FTM_SC_CLKS_MASK) == FTM_SC_CLKS(1U)); // counter runs from the system clock
        CHECK((regs.SC & FTM_SC_PS_MASK) == (cfg.sc & FTM_SC_PS_MASK));
        CHECK(regs.MOD == (cap::COUNTER_RANGE - 1U));
        CHECK(regs.CNTIN == 0U);
        CHECK((regs.MODE & FTM_MODE_FTMEN_MASK) != 0U); // dual edge capture needs FTMEN
        CHECK((regs.SC & FTM_SC_PWMEN0_MASK) == 0U);    // no output is driven by a capture timer
    }
    for (uint16_t i = 0U; i < CHANNELS; ++i)
    {
        FTM_Type const& regs = regsOf(i);
        uint8_t const ch     = cap::CHANNELS[i].channel;
        uint32_t const shift = cap::pairShift(ch);
        CHECK((regs.COMBINE & (cap::COMBINE_DECAPEN_PAIR << shift)) != 0U); // pair in dual edge capture mode
        CHECK((regs.COMBINE & (cap::COMBINE_DECAP_PAIR << shift)) != 0U);   // first measurement armed
        CHECK(regs.CONTROLS[ch].CnSC == cap::CNSC_RISING);                  // period: rising to rising edge
        CHECK(regs.CONTROLS[ch + 1U].CnSC == cap::CNSC_RISING);
        CHECK((regs.CONTROLS[ch].CnSC & FTM_CnSC_MSA_MASK) == 0U); // one-shot, not continuous
        InputCapture::Measurement const m = InputCapture::get(i);
        CHECK(m.quality == InputCapture::QUALITY_DEBOUNCE); // nothing measured yet, no silent zero as a value
        CHECK((m.frequency_mhz == 0U) && (m.period_us == 0U) && (m.duty_permille == 0U));
    }
}

/** Right after the start without a signal: first not valid yet, then a timeout - never a value. */
void testNoSignalFromTheStart(uint16_t const channel)
{
    cap::Timer const& limits          = limitsOf(channel);
    InputCapture::Measurement const m = InputCapture::get(channel);
    CHECK(m.quality == InputCapture::QUALITY_DEBOUNCE);
    run(channel, 1U);
    CHECK(InputCapture::get(channel).quality == InputCapture::QUALITY_DEBOUNCE);
    run(channel, static_cast<unsigned>(limits.timeoutTicks / limits.updateTicks) + 2U);
    InputCapture::Measurement const late = InputCapture::get(channel);
    CHECK(late.quality == InputCapture::QUALITY_TIMEOUT);
    CHECK((late.frequency_mhz == 0U) && (late.period_us == 0U) && (late.duty_permille == 0U));
}

/** A normal signal: two measurements (period and pulse) give frequency, period and duty cycle. */
void testNormalSignal(uint16_t const channel)
{
    uint32_t const period = normalPeriod(channel);
    setSignal(channel, period, period / 4U);
    run(channel, 8U);
    InputCapture::Measurement const m = InputCapture::get(channel);
    CHECK(m.quality == InputCapture::QUALITY_OK);
    CHECK(m.frequency_mhz == expectedFrequencyMilliHz(channel, period));
    CHECK(m.period_us == expectedPeriodMicros(channel, period));
    CHECK(m.duty_permille == 250U);
    // the pulse measurement is armed with the falling edge on the odd channel, the period with the rising one
    uint32_t const odd = edgeSelection(channel, 1U);
    CHECK((odd == cap::CNSC_RISING) || (odd == cap::CNSC_FALLING));
    CHECK(edgeSelection(channel, 0U) == cap::CNSC_RISING); // the first edge is always the rising one
}

void testDutyExtremes(uint16_t const channel)
{
    cap::Timer const& limits = limitsOf(channel);
    uint32_t const period    = normalPeriod(channel);
    setSignal(channel, period, 1U); // one tick of high time: not a duty cycle of zero
    run(channel, 8U);
    InputCapture::Measurement m = InputCapture::get(channel);
    CHECK(m.quality == InputCapture::QUALITY_OK);
    CHECK(m.duty_permille == 1U);
    CHECK(m.frequency_mhz == expectedFrequencyMilliHz(channel, period));

    setSignal(channel, period, period - 1U); // 99.99 %: the last tick is still low
    run(channel, 8U);
    m = InputCapture::get(channel);
    CHECK(m.quality == InputCapture::QUALITY_OK);
    CHECK(m.duty_permille == 999U);
    CHECK(m.frequency_mhz == expectedFrequencyMilliHz(channel, period));

    // the shortest period the range allows, with a small duty cycle
    uint32_t const shortest = limits.minPeriodTicks;
    setSignal(channel, shortest, shortest / 100U);
    run(channel, 8U);
    m = InputCapture::get(channel);
    CHECK(m.quality == InputCapture::QUALITY_OK);
    CHECK(m.duty_permille == 10U);
    CHECK(m.frequency_mhz == expectedFrequencyMilliHz(channel, shortest));
}

/** Counter overflows: measurements across the wrap of the 16-bit counter stay right. */
void testOverflow(uint16_t const channel)
{
    cap::Timer const& limits = limitsOf(channel);
    uint32_t const period    = limits.maxPeriodTicks / 3U;
    setSignal(channel, period, period / 2U);
    unsigned const cycles    = static_cast<unsigned>((3U * cap::COUNTER_RANGE) / limits.updateTicks) + 1U;
    unsigned overflowsSeen   = 0U;
    for (unsigned i = 0U; i < cycles; ++i)
    {
        advance(limits.updateTicks);
        if ((regsOf(channel).SC & FTM_SC_TOF_MASK) != 0U)
        {
            ++overflowsSeen;
        }
        InputCapture::update();
        InputCapture::Measurement const m = InputCapture::get(channel);
        if (i > 4U) // after the first two measurements every cycle has a valid result
        {
            CHECK(m.quality == InputCapture::QUALITY_OK);
            CHECK(m.frequency_mhz == expectedFrequencyMilliHz(channel, period));
            CHECK(m.duty_permille == 500U);
        }
    }
    CHECK(overflowsSeen >= 3U); // the test really crossed the counter wrap
}

/** A missing signal is reported as a timeout, the last values are kept (no silent zero). */
void testMissingSignal(uint16_t const channel)
{
    cap::Timer const& limits = limitsOf(channel);
    uint32_t const period    = normalPeriod(channel);
    setSignal(channel, period, period / 4U);
    run(channel, 8U);
    InputCapture::Measurement const good = InputCapture::get(channel);
    CHECK(good.quality == InputCapture::QUALITY_OK);

    stopSignal(channel); // constant level: no edge at all
    unsigned const cycles = static_cast<unsigned>(limits.timeoutTicks / limits.updateTicks) + 3U;
    run(channel, cycles);
    InputCapture::Measurement const m = InputCapture::get(channel);
    CHECK(m.quality == InputCapture::QUALITY_TIMEOUT);
    CHECK(m.frequency_mhz == good.frequency_mhz); // held, the quality says that it is old
    CHECK(m.duty_permille == good.duty_permille);

    setSignal(channel, period, period / 2U); // and back: the driver recovers without init()
    run(channel, 10U);
    InputCapture::Measurement const back = InputCapture::get(channel);
    CHECK(back.quality == InputCapture::QUALITY_OK);
    CHECK(back.duty_permille == 500U);
}

/** A period outside the configured range is an error, never a value. */
void testOutOfRange(uint16_t const channel)
{
    cap::Timer const& limits = limitsOf(channel);
    uint32_t const period    = normalPeriod(channel);
    setSignal(channel, period, period / 4U);
    run(channel, 8U);
    InputCapture::Measurement const good = InputCapture::get(channel);
    CHECK(good.quality == InputCapture::QUALITY_OK);

    // slower than min_frequency_hz, and slower than the counter range: the 16-bit difference would alias to a
    // plausible looking value (here exactly the period measured before)
    uint32_t const aliasing = cap::COUNTER_RANGE + period;
    setSignal(channel, aliasing, aliasing / 4U);
    run(channel, static_cast<unsigned>((4U * aliasing) / limits.updateTicks) + 4U);
    InputCapture::Measurement const slow = InputCapture::get(channel);
    CHECK(slow.quality != InputCapture::QUALITY_OK); // error or timeout, but no wrong frequency
    CHECK(slow.quality == InputCapture::QUALITY_ERROR);

    // faster than the range: the period would not resolve 0.1 % of duty any more
    uint32_t const fast = limits.minPeriodTicks / 2U;
    setSignal(channel, fast, fast / 2U);
    run(channel, 8U);
    CHECK(InputCapture::get(channel).quality == InputCapture::QUALITY_ERROR);

    setSignal(channel, period, period / 4U); // recovery
    run(channel, 10U);
    CHECK(InputCapture::get(channel).quality == InputCapture::QUALITY_OK);
}

/** update() later than the counter range: the time base is ambiguous, so the driver reports an error. */
void testUpdateTooSlow(uint16_t const channel)
{
    cap::Timer const& limits = limitsOf(channel);
    uint32_t const period    = normalPeriod(channel);
    setSignal(channel, period, period / 4U);
    run(channel, 8U);
    CHECK(InputCapture::get(channel).quality == InputCapture::QUALITY_OK);
    for (unsigned i = 0U; i < 3U; ++i)
    {
        advance(cap::COUNTER_RANGE + limits.updateTicks); // more than one wrap between two calls
        InputCapture::update();
    }
    CHECK(InputCapture::get(channel).quality == InputCapture::QUALITY_ERROR);
    InputCapture::init(); // a fresh start is possible
    modelNow += 1U;
    setSignal(channel, period, period / 4U);
    run(channel, 10U);
    CHECK(InputCapture::get(channel).quality == InputCapture::QUALITY_OK);
}

/** Channels do not influence each other: a missing signal on one leaves the other valid. */
void testChannelsAreIndependent()
{
    if (CHANNELS < 2U)
    {
        return;
    }
    for (uint16_t i = 0U; i < CHANNELS; ++i)
    {
        uint32_t const period = normalPeriod(i) / (1U + i);
        setSignal(i, period, period / 4U);
    }
    run(0U, 10U);
    for (uint16_t i = 0U; i < CHANNELS; ++i)
    {
        CHECK(InputCapture::get(i).quality == InputCapture::QUALITY_OK);
        CHECK(InputCapture::get(i).duty_permille == 250U);
    }
    stopSignal(1U);
    run(0U, static_cast<unsigned>(limitsOf(1U).timeoutTicks / limitsOf(1U).updateTicks) + 3U);
    CHECK(InputCapture::get(0U).quality == InputCapture::QUALITY_OK);
    CHECK(InputCapture::get(1U).quality == InputCapture::QUALITY_TIMEOUT);
    stopSignal(0U);
}

void testNames()
{
    for (uint16_t i = 0U; i < CHANNELS; ++i)
    {
        CHECK(std::strcmp(InputCapture::getName(i), cap::NAMES[i]) == 0);
        CHECK(InputCapture::getName(i)[0] != '\0');
    }
    CHECK(InputCapture::getName(CHANNELS)[0] == '\0');                                  // unknown channel
    CHECK(InputCapture::get(CHANNELS).quality == InputCapture::QUALITY_DEBOUNCE);        // and no value
}

void restart()
{
    for (uint16_t i = 0U; i < CHANNELS; ++i)
    {
        stopSignal(i);
    }
    InputCapture::init();
    modelNow += 1U;
}
} // namespace

int main()
{
    // all capture timers of a profile run at the same clock (same min_frequency_hz), the model uses one time base
    for (uint8_t t = 1U; t < cap::TIMER_COUNT; ++t)
    {
        CHECK(cap::TIMERS[t].clockHz == cap::TIMERS[0].clockHz);
        CHECK(cap::TIMERS[t].updateTicks == cap::TIMERS[0].updateTicks);
    }
    InputCapture::init();
    testInitialRegisters();
    testNames();
    for (uint16_t channel = 0U; channel < CHANNELS; ++channel)
    {
        restart();
        testNoSignalFromTheStart(channel);
        restart();
        testNormalSignal(channel);
        restart();
        testDutyExtremes(channel);
        restart();
        testOverflow(channel);
        restart();
        testMissingSignal(channel);
        restart();
        testOutOfRange(channel);
        restart();
        testUpdateTooSlow(channel);
    }
    restart();
    testChannelsAreIndependent();
    std::printf(
        "input_capture_host_test: %u channel(s), %u timer(s), %u checks, %u failures\n",
        static_cast<unsigned>(CHANNELS),
        static_cast<unsigned>(cap::TIMER_COUNT),
        checks,
        failures);
    return (failures == 0U) ? 0 : 1;
}
