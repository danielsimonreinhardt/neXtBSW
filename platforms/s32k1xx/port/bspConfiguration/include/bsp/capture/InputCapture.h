#pragma once

#include "bsp/capture/inputCaptureConfiguration.h"

#include <cstdint>

namespace bios
{
/**
 * Period and duty cycle of a PWM input, measured with the dual edge capture of an FTM (IO-03).
 *
 * The configuration is generated from board and project into bsp/capture/inputCaptureConfiguration.h: which
 * channels exist, the counter clock (clock model, ARCH-06), the prescaler derived from the lowest expected
 * frequency, the measurable period range, the timeout and the update cycle - all in counter ticks.
 *
 * How it measures: the FTM pair (2k, 2k+1) captures two edges of the signal on the pin of the even channel in
 * one-shot mode. Two measurements alternate on every channel:
 *   - period: both channels capture the rising edge, the difference is one period
 *   - pulse:  the even channel captures the rising, the odd one the falling edge (the high time)
 * The counter runs free over its whole range, so no interrupt is needed: update() reads what the hardware
 * captured, re-arms the other measurement and derives frequency, period and duty cycle.
 *
 * Quality instead of a silent zero: a value is only reported as QUALITY_OK if a period and a pulse measurement
 * of the same channel completed within the timeout and the period lies inside the configured range. Otherwise
 * the last valid values are kept (like the qualifier of a received CAN message, COM-03) and the quality says
 * why: QUALITY_TIMEOUT for a missing signal (no edges, also a constant high or low level), QUALITY_ERROR for a
 * period outside the range, an implausible pulse or an update() that came too late to be unambiguous.
 *
 * Call it from one task: update() and get() have no lock (the generated RTE plans both into the same task,
 * like the ADC inputs). update() has to be called in the cycle the project configured (update_period_ms);
 * if two consecutive calls see a counter overflow, the driver reports QUALITY_ERROR instead of a value that
 * could be wrong by whole counter wraps.
 */
class InputCapture
{
public:
    /**
     * Quality of a measurement. The values are the signal qualifier values of the RTE (QLF_* of the generated
     * Rte_Qlf.h, COM-03, doc/architecture/signal-qualifier.md), so a component compares the same constants no
     * matter whether a value comes from CAN or from a capture input. The BSP must not include a generated RTE
     * header, so the values stand here as well; tools/rtegen/test_rtegen.py compares both lists.
     */
    static constexpr uint8_t QUALITY_DEBOUNCE = 0U;  // no valid measurement yet (after the start)
    static constexpr uint8_t QUALITY_OK       = 1U;
    static constexpr uint8_t QUALITY_TIMEOUT  = 8U;  // no completed measurement within timeout_ms
    static constexpr uint8_t QUALITY_ERROR    = 16U; // out of range, implausible, or update() too slow

    struct Measurement
    {
        uint32_t frequency_mhz; // milli-Hertz (Hz * 1000) of the last valid measurement, 0 until the first one
        uint32_t period_us;     // period of the same measurement in microseconds
        uint16_t duty_permille; // high time of one period in 0.1 %; at least 1 while there is a pulse at all
        uint8_t quality;        // QUALITY_*
    };

    /** Value of a channel that was never measured: the quality says so, the values are zero. */
    static constexpr Measurement noMeasurement() { return Measurement{0U, 0U, 0U, QUALITY_DEBOUNCE}; }

#if NX_CAPTURE_CHANNEL_COUNT > 0
    /**
     * Configures and starts the counters, muxes every channel's pin to its FTM (IO-48: PORTE_PCR8 stayed 0
     * without this - the FTM ran, but the pin still carried its GPIO reset state) and arms the first
     * measurement of every channel.
     */
    static void init();

    /** Reads what the hardware captured and re-arms; call it every update_period_ms of the project. */
    static void update();

    /** Last values of a channel (capture::ChannelId); an unknown channel returns noMeasurement(). */
    static Measurement get(uint16_t channel);

    /** Board name of a channel, "" for an unknown one. */
    static char const* getName(uint16_t channel);

    static uint16_t const ChannelCountAll = static_cast<uint16_t>(capture::_captureChannelMax);

private:
    /** What one measurement of a channel captures next. */
    enum Phase : uint8_t
    {
        PHASE_PERIOD = 0U, // rising to rising edge
        PHASE_PULSE  = 1U, // rising to falling edge
    };

    struct TimerState
    {
        uint32_t time;      // counter ticks since init(), extended over the 16-bit counter
        uint16_t lastCount; // CNT of the previous update()
        bool overflow;      // counter overflow seen by the previous update()
        bool tooSlow;       // two overflows in a row: update() cannot keep the time base unambiguous
    };

    struct ChannelState
    {
        Phase phase;
        uint32_t armedAt;     // time the current measurement was armed
        uint32_t periodTicks; // last period measurement, valid while hasPeriod
        uint32_t periodAt;    // time it completed
        uint32_t highTicks;   // last pulse measurement, valid while hasHigh
        uint32_t highAt;
        uint32_t resultAt;    // time of the last result of both measurements (valid or not)
        bool hasPeriod;
        bool hasHigh;
        bool hasResult;
        bool stale;           // a measurement was thrown away because it did not complete within the timeout
        bool tooLong;         // a measurement took longer than its own value: the counter wrapped more than once
        Measurement value;
    };

    static void arm(FTM_Type& ftm, uint8_t channel, Phase phase);
    static void evaluate(uint16_t channel, TimerState const& timer);

    static TimerState _timers[capture::TIMER_COUNT];
    static ChannelState _channels[capture::_captureChannelMax];
#else
    // The project uses no input_capture entry: nothing is measured, every channel reports "no measurement".
    static void init() {}

    static void update() {}

    static Measurement get(uint16_t /* channel */) { return noMeasurement(); }

    static char const* getName(uint16_t /* channel */) { return ""; }

    static uint16_t const ChannelCountAll = 0U;
#endif
};

} // namespace bios
