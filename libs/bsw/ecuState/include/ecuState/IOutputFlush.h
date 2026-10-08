// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstdint>

namespace ecustate
{

/**
 * Diagnostic output drained before the power down (SYS-09): the log and console lines of the run-down (last step,
 * Rundown -> Shutdown, Shutdown -> Sleep) are still in the buffer of the output when the power supply removes the
 * MCU supply. The ECU state management starts the flush one cycle after entering State::SLEEP, polls it every
 * cycle and calls IPowerSupply::powerDown() when it is done or its limit has passed - it never blocks.
 *
 * Provided by the integration (it knows the output: logger buffer, console UART and its baud rate) and wired
 * through ShutdownSequence::setOutputFlush(). Without provider the power down follows POWER_DOWN_DELAY_MS after
 * entering State::SLEEP as before. Both calls come from the task of the power supply.
 */
class IOutputFlush
{
public:
    /** Short name for console and log, e.g. "console": a string literal of at most 16 characters (SYS-16). */
    virtual char const* getName() const = 0;

    /**
     * Upper bound of the flush in ms, derived by the provider from what it drains (e.g. buffer size and baud
     * rate, see serialTransmitTimeMs()). Read once when the flush starts. 0: the flush is skipped.
     */
    virtual uint32_t getLimitMs() const = 0;

    /** Begins draining; everything written to the output before this call is to leave the ECU. */
    virtual void start(uint32_t nowMs) = 0;

    /** True once everything written before start() has left the ECU. */
    virtual bool isFlushed(uint32_t nowMs) = 0;

protected:
    IOutputFlush()                               = default;
    IOutputFlush(IOutputFlush const&)            = default;
    IOutputFlush& operator=(IOutputFlush const&) = default;
    ~IOutputFlush()                              = default;
};

/** How far the flush before the power down got (SYS-09). */
enum class OutputFlushState : uint8_t
{
    NOT_PRESENT, // no provider in this project: fixed log delay POWER_DOWN_DELAY_MS instead
    PENDING,     // not started yet
    RUNNING,     // started, being polled
    DONE,        // drained, power down requested
    TIMEOUT,     // not drained within the limit, power down requested anyway
};

char const* outputFlushStateName(OutputFlushState state);

/** Bits per character on an asynchronous serial line with 8 data bits, no parity, one stop bit (8N1). */
constexpr uint32_t SERIAL_BITS_PER_CHARACTER = 10U;

/** Time in ms (rounded up) to send \p characters at \p baudRate (8N1); 0 for a baud rate of 0. */
constexpr uint32_t serialTransmitTimeMs(uint32_t const characters, uint32_t const baudRate)
{
    if (baudRate == 0U)
    {
        return 0U;
    }
    uint64_t const bitsTimesMs = static_cast<uint64_t>(characters) * SERIAL_BITS_PER_CHARACTER * 1000U;
    uint64_t const ms          = (bitsTimesMs + baudRate - 1U) / baudRate;
    return (ms > 0xFFFFFFFFU) ? 0xFFFFFFFFU : static_cast<uint32_t>(ms);
}

/** Time in µs (rounded up) to send \p characters at \p baudRate (8N1); 0 for a baud rate of 0. */
constexpr uint32_t serialTransmitTimeUs(uint32_t const characters, uint32_t const baudRate)
{
    if (baudRate == 0U)
    {
        return 0U;
    }
    uint64_t const bitsTimesUs = static_cast<uint64_t>(characters) * SERIAL_BITS_PER_CHARACTER * 1000000U;
    uint64_t const us          = (bitsTimesUs + baudRate - 1U) / baudRate;
    return (us > 0xFFFFFFFFU) ? 0xFFFFFFFFU : static_cast<uint32_t>(us);
}

} // namespace ecustate
