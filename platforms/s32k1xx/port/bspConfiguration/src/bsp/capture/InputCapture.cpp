#include "bsp/capture/InputCapture.h"

#if NX_CAPTURE_CHANNEL_COUNT > 0

#include "ftm/Ftm.h"
#include "io/Io.h"

namespace bios
{
namespace
{
constexpr uint32_t PERMILLE = 1000U;
// The period and the pulse of one result are captured in different periods of the signal, so jitter can make
// the high time longer than the period measured before. Up to this share of the period it is clamped to 100 %,
// above it the signal changed too much to report one result (QUALITY_ERROR).
constexpr uint32_t JITTER_DIVIDER = 8U; // period / 8 = 12.5 %

// IO-48: found on the board 2026-09-28 (charge project, 9c) - PORTE_PCR8 stayed 0 after start, so PILOT_DUTY
// never saw an edge although FTM0 was running and ioConfiguration.h already had the correct ALT function for
// PTE8. init() configured the FTM but never muxed the pin to it, the same gap IO-25 found for the TMC6200 SPI
// pins. AnalogInput and ComplementaryPwm mux every channel's pin from a generated Io::PinId field; the input
// capture generat (tools/bswgen/templates/inputCaptureConfiguration.h.j2) does not carry one (ChannelConfig
// only has timer/channel), so the pin is looked up here instead: tools/bswgen adds every input_capture channel
// to the same board pin table under its own name (Board.collect()), so Io::<name> always exists for a channel
// named <name> - NX_CAPTURE_CHANNELS(X), one X(<name>) per channel in CHANNELS[] order, turns that into a
// compile-time array. A channel the generator ever failed to register in the pin table would fail the build
// here (unknown member of Io) instead of silently muxing nothing.
#define NX_CAPTURE_CHANNEL_PIN_ENTRY(name) Io::name,
constexpr uint16_t CHANNEL_PINS[] = {NX_CAPTURE_CHANNELS(NX_CAPTURE_CHANNEL_PIN_ENTRY)};
#undef NX_CAPTURE_CHANNEL_PIN_ENTRY
static_assert(
    sizeof(CHANNEL_PINS) / sizeof(CHANNEL_PINS[0]) == static_cast<size_t>(capture::_captureChannelMax),
    "one pin per capture channel");

uint32_t elapsedTicks(FTM_Type const& ftm, uint8_t const channel)
{
    uint16_t const first  = static_cast<uint16_t>(ftm.CONTROLS[channel].CnV);
    uint16_t const second = static_cast<uint16_t>(ftm.CONTROLS[channel + 1U].CnV);
    // the counter may have wrapped once between the two captures; the 16-bit difference is the elapsed time
    return static_cast<uint16_t>(second - first);
}
} // namespace

InputCapture::TimerState InputCapture::_timers[capture::TIMER_COUNT]                 = {};
InputCapture::ChannelState InputCapture::_channels[capture::_captureChannelMax] = {};

void InputCapture::arm(FTM_Type& ftm, uint8_t const channel, Phase const phase)
{
    uint32_t const armBit = capture::COMBINE_DECAP_PAIR << capture::pairShift(channel);
    // disarm first: the edge selection of the pair must not change while a capture is running
    ftm.COMBINE = ftm.COMBINE & ~armBit;
    // Writing CnSC with CHF = 0 clears the capture flag and selects the edge of this measurement in one write.
    // Clearing CHF needs the register to be read while the flag is set (CHF is write-0-to-clear), so both
    // reads below belong to the sequence and must not be optimised away (the registers are volatile).
    (void)ftm.CONTROLS[channel].CnSC;
    (void)ftm.CONTROLS[channel + 1U].CnSC;
    ftm.CONTROLS[channel].CnSC = capture::CNSC_RISING;
    ftm.CONTROLS[channel + 1U].CnSC
        = (phase == PHASE_PERIOD) ? capture::CNSC_RISING : capture::CNSC_FALLING;
    ftm.COMBINE = ftm.COMBINE | armBit;
}

void InputCapture::init()
{
    for (uint8_t i = 0U; i < capture::TIMER_COUNT; ++i)
    {
        FTM_Type& regs = capture::ftm(i);
        tFtm timer(regs);
        timer.stop();
        timer.init(&capture::configuration(i));
        timer.start();
        _timers[i] = TimerState{0U, static_cast<uint16_t>(regs.CNT), false, false};
    }
    for (uint16_t ch = 0U; ch < static_cast<uint16_t>(capture::_captureChannelMax); ++ch)
    {
        capture::ChannelConfig const& cfg = capture::CHANNELS[ch];
        _channels[ch]                     = ChannelState{};
        _channels[ch].value               = noMeasurement();
        // IO-48: mux the pin to the FTM before arming it - see the CHANNEL_PINS comment above.
        (void)Io::setDefaultConfiguration(CHANNEL_PINS[ch]);
        arm(capture::ftm(cfg.timer), cfg.channel, PHASE_PERIOD);
    }
}

void InputCapture::update()
{
    for (uint8_t i = 0U; i < capture::TIMER_COUNT; ++i)
    {
        FTM_Type& regs  = capture::ftm(i);
        TimerState& t   = _timers[i];
        uint16_t const count = static_cast<uint16_t>(regs.CNT);
        bool const overflow  = (regs.SC & FTM_SC_TOF_MASK) != 0U;
        if (overflow)
        {
            regs.SC = regs.SC & ~static_cast<uint32_t>(FTM_SC_TOF_MASK);
        }
        // The 16-bit difference carries the elapsed time as long as the counter wrapped at most once. Two
        // overflows in a row mean update() is slower than the counter range, and a slow signal could then be
        // measured as a fast one: that is reported, not measured (the generator sizes the prescaler so that
        // update_period_ms of the project stays well below one wrap).
        t.time += static_cast<uint16_t>(count - t.lastCount);
        t.lastCount = count;
        t.tooSlow   = overflow && t.overflow;
        t.overflow  = overflow;
    }
    for (uint16_t ch = 0U; ch < static_cast<uint16_t>(capture::_captureChannelMax); ++ch)
    {
        capture::ChannelConfig const& cfg = capture::CHANNELS[ch];
        capture::Timer const& limits      = capture::TIMERS[cfg.timer];
        TimerState const& t               = _timers[cfg.timer];
        FTM_Type& regs                    = capture::ftm(cfg.timer);
        ChannelState& c                   = _channels[ch];
        // the flag of the odd channel says that both edges of this measurement were captured (one-shot: the
        // hardware has cleared DECAP as well)
        bool const complete = (regs.STATUS & (1UL << (cfg.channel + 1U))) != 0UL;
        if (complete)
        {
            uint32_t const ticks = elapsedTicks(regs, cfg.channel);
            // A capture difference is only 16 bit wide: a signal slower than the counter range would alias to
            // a shorter value and could even look healthy. The measurement is therefore compared with the time
            // it really took (waiting for the first edge is at most one period of the lowest frequency, the
            // completion is noticed at most one update cycle later). Both bounds are smaller than the counter
            // range by construction (the generator checks it), so an aliased value is always caught here.
            uint32_t const bound = limits.maxPeriodTicks + ticks + limits.updateTicks;
            if ((t.time - c.armedAt) > bound)
            {
                c.tooLong = true;
            }
            else if (c.phase == PHASE_PERIOD)
            {
                c.periodTicks = ticks;
                c.periodAt    = t.time;
                c.hasPeriod   = true;
            }
            else
            {
                c.highTicks = ticks;
                c.highAt    = t.time;
                c.hasHigh   = true;
            }
            c.phase   = (c.phase == PHASE_PERIOD) ? PHASE_PULSE : PHASE_PERIOD;
            c.armedAt = t.time;
            arm(regs, cfg.channel, c.phase);
        }
        else if ((t.time - c.armedAt) > limits.timeoutTicks)
        {
            // Nothing completed within the timeout: throw the half measurement away and arm the same
            // measurement again. Otherwise the captured first edge would pair with an edge of a much later
            // period once the signal comes back.
            c.stale   = true;
            c.armedAt = t.time;
            arm(regs, cfg.channel, c.phase);
        }
        evaluate(ch, t);
    }
}

void InputCapture::evaluate(uint16_t const channel, TimerState const& t)
{
    ChannelState& c              = _channels[channel];
    capture::Timer const& limits = capture::TIMERS[capture::CHANNELS[channel].timer];
    if (t.tooSlow)
    {
        c.value.quality = QUALITY_ERROR; // time base not unambiguous, the values would be a guess
        return;
    }
    if (c.tooLong)
    {
        // the signal is slower than the counter range: the captured difference would be a wrong value
        c.tooLong       = false;
        c.hasPeriod     = false;
        c.hasHigh       = false;
        c.hasResult     = true;
        c.resultAt      = t.time;
        c.value.quality = QUALITY_ERROR;
        return;
    }
    bool const fresh = c.hasPeriod && c.hasHigh && ((t.time - c.periodAt) <= limits.timeoutTicks)
                       && ((t.time - c.highAt) <= limits.timeoutTicks);
    if (!fresh)
    {
        if (!c.hasResult)
        {
            // nothing measured yet: not valid, but not a fault either until one timeout has passed
            c.value.quality = c.stale ? QUALITY_TIMEOUT : QUALITY_DEBOUNCE;
        }
        else if ((t.time - c.resultAt) > limits.timeoutTicks)
        {
            c.value.quality = QUALITY_TIMEOUT; // missing signal, constant level, or below the period range
        }
        return;
    }
    // one period and one pulse of the same channel: from here on there is a result (valid or not)
    uint32_t const period = c.periodTicks;
    uint32_t high         = c.highTicks;
    c.hasPeriod           = false;
    c.hasHigh             = false;
    c.hasResult           = true;
    c.stale               = false;
    c.resultAt            = t.time;
    if ((period < limits.minPeriodTicks) || (period > limits.maxPeriodTicks))
    {
        c.value.quality = QUALITY_ERROR; // outside the configured range: the value would be misleading
        return;
    }
    if (high > period)
    {
        if ((high - period) > (period / JITTER_DIVIDER))
        {
            c.value.quality = QUALITY_ERROR;
            return;
        }
        high = period; // jitter between the two measurements: 100 %
    }
    uint64_t const clock  = limits.clockHz;
    uint32_t duty         = static_cast<uint32_t>((static_cast<uint64_t>(high) * PERMILLE) / period);
    if ((duty == 0U) && (high > 0U))
    {
        duty = 1U; // a very short pulse is not a duty cycle of zero
    }
    c.value.frequency_mhz
        = static_cast<uint32_t>((clock * PERMILLE + (period / 2U)) / period); // milli-Hertz, rounded
    c.value.period_us    = static_cast<uint32_t>((static_cast<uint64_t>(period) * 1000000ULL + (clock / 2U)) / clock);
    c.value.duty_permille = static_cast<uint16_t>(duty);
    c.value.quality       = QUALITY_OK;
}

InputCapture::Measurement InputCapture::get(uint16_t const channel)
{
    if (channel >= static_cast<uint16_t>(capture::_captureChannelMax))
    {
        return noMeasurement();
    }
    return _channels[channel].value;
}

char const* InputCapture::getName(uint16_t const channel)
{
    if (channel >= static_cast<uint16_t>(capture::_captureChannelMax))
    {
        return "";
    }
    return capture::NAMES[channel];
}

} // namespace bios

#endif // NX_CAPTURE_CHANNEL_COUNT > 0
