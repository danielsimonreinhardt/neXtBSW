// SPDX-License-Identifier: Apache-2.0
#pragma once

/* Drain detection and limit of the console output flush (SYS-09), pure logic (host test:
 * libs/bsw/ecuState/test/host/EcuOutputFlushHostTest.cpp). The only thing taken from OpenBSW is the size of the
 * logger buffer, and only if its header is on the include path (SYS-10, see below).
 *
 * The OpenBSW application serves the logger and the console in one output context: every run of the idle handler
 * outputs at most one log entry (::logger::run(), LoggerComposition) and the console (::console::run()), both
 * written byte by byte through stdIo to bspUart, which waits for every byte to leave the transmit data register.
 * A probe runnable posted into the same context runs alternately with that handler (FIFO queue of the async
 * executor). The time between two runs of the probe therefore tells whether the handler wrote something in
 * between: a log line has at least 11 characters (timestamp, ": ", application name, ": ", component, ": ",
 * level, ": ") and blocks the context for at least 10 character times, a run without output returns in a few
 * microseconds. A gap below QUIET_GAP_CHARACTERS character times means "nothing written"; QUIET_RUNS such gaps in a
 * row mean the logger buffer is empty. Preemption by other tasks only makes gaps longer - it can delay the result,
 * never fake it.
 */

#include <ecuState/IOutputFlush.h>

#include <cstdint>

/* SYS-10: the size of the logger ring buffer comes from its source, logger/Config.h of the OpenBSW
 * loggerIntegration (main links it, platforms/s32k1xx/port/main/CMakeLists.txt). The host test of the pure logic
 * of this header (libs/bsw/ecuState/test/host/EcuOutputFlushHostTest.cpp) compiles it with zig c++ without any
 * OpenBSW include path and falls back to LOGGER_BUFFER_BYTES_HOST_TEST; every target build compares both, so the
 * two cannot drift apart unnoticed. */
#if defined(__has_include)
#if __has_include(<logger/Config.h>)
#define NX_INTEGRATION_HAS_LOGGER_CONFIG 1
#endif
#endif

#ifdef NX_INTEGRATION_HAS_LOGGER_CONFIG
#include <logger/Config.h>
#endif

namespace integration
{

/** Buffer size the OpenBSW-free host test of this header compiles against (checked against the real one below). */
constexpr uint32_t LOGGER_BUFFER_BYTES_HOST_TEST = 1024U * 8U;

#ifdef NX_INTEGRATION_HAS_LOGGER_CONFIG
namespace detail
{
/** BufferSize template argument of a logger::declare::BufferedLoggerOutput (logger/BufferedLoggerOutput.h). */
template<class T>
struct LoggerBufferSize;

template<
    uint32_t BufferSize,
    class Lock,
    uint8_t MaxEntrySize,
    class T,
    class E,
    class Timestamp,
    class ReadOnlyPredicate>
struct LoggerBufferSize<::logger::declare::BufferedLoggerOutput<
    BufferSize,
    Lock,
    MaxEntrySize,
    T,
    E,
    Timestamp,
    ReadOnlyPredicate>>
{
    static constexpr uint32_t VALUE = BufferSize;
};
} // namespace detail

/** Size of the logger ring buffer: BufferSize of logger::BufferedLoggerOutputType (logger/Config.h). */
constexpr uint32_t LOGGER_BUFFER_BYTES = detail::LoggerBufferSize<::logger::BufferedLoggerOutputType>::VALUE;

#ifdef NX_LOGGER_BUFFER_BYTES_DEFAULT
// Generated logger/Config.h (PLAT-04): compare the default, so that a test build with an explicit
// -DNX_LOGGER_BUFFER_BYTES (SYS-12, board check of lost log lines) still builds; its limit follows its own size.
static_assert(
    NX_LOGGER_BUFFER_BYTES_DEFAULT == LOGGER_BUFFER_BYTES_HOST_TEST,
    "the default of NX_LOGGER_BUFFER_BYTES changed: update LOGGER_BUFFER_BYTES_HOST_TEST and the limits "
    "checked in libs/bsw/ecuState/test/host/EcuOutputFlushHostTest.cpp");
#else
static_assert(
    LOGGER_BUFFER_BYTES == LOGGER_BUFFER_BYTES_HOST_TEST,
    "logger/Config.h changed the size of the logger buffer: update LOGGER_BUFFER_BYTES_HOST_TEST and the limits "
    "checked in libs/bsw/ecuState/test/host/EcuOutputFlushHostTest.cpp");
#endif
#else
constexpr uint32_t LOGGER_BUFFER_BYTES = LOGGER_BUFFER_BYTES_HOST_TEST;
#endif

/**
 * Printed characters per byte of the logger buffer, upper range: the smallest entry (format string without
 * arguments) takes 12 bytes (length, timestamp, component, level, format pointer) and prints as timestamp,
 * application name, component and level with VT100 colours plus the message and "\r\n", about 60 + message
 * characters; entries with arguments print fewer characters per byte.
 */
constexpr uint32_t CHARACTERS_PER_LOGGER_BYTE = 8U;

/** Limit of the flush for a console line of \p baudRate: one full logger buffer, printed. */
constexpr uint32_t consoleFlushLimitMs(uint32_t const baudRate)
{
    return ::ecustate::serialTransmitTimeMs(LOGGER_BUFFER_BYTES * CHARACTERS_PER_LOGGER_BYTE, baudRate);
}

class IdleOutputDrain
{
public:
    /** Gap between two probe runs, in character times of the console line, below which nothing was written. */
    static constexpr uint32_t QUIET_GAP_CHARACTERS = 4U;
    /** Quiet gaps in a row that count as drained. */
    static constexpr uint8_t QUIET_RUNS = 2U;

    /** \param quietGapUs QUIET_GAP_CHARACTERS character times of the console line in µs */
    explicit IdleOutputDrain(uint32_t const quietGapUs) : _quietGapUs(quietGapUs) {}

    /** Forget earlier runs: the next sample() starts a new measurement. */
    void restart()
    {
        _hasLast   = false;
        _quietRuns = 0U;
    }

    /** Call in every run of the probe with the current time in µs; true once the output is drained. */
    bool sample(uint32_t const nowUs)
    {
        if (_hasLast)
        {
            if ((nowUs - _lastUs) < _quietGapUs)
            {
                if (_quietRuns < QUIET_RUNS)
                {
                    ++_quietRuns;
                }
            }
            else
            {
                _quietRuns = 0U; // the handler wrote something (or the context was preempted)
            }
        }
        _hasLast = true;
        _lastUs  = nowUs;
        return _quietRuns >= QUIET_RUNS;
    }

private:
    uint32_t _quietGapUs;
    uint32_t _lastUs   = 0U;
    bool _hasLast      = false;
    uint8_t _quietRuns = 0U;
};

} // namespace integration
