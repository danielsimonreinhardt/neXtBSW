// SPDX-License-Identifier: Apache-2.0
#pragma once

/* SYS-12: logger composition of the application that reports lost log entries on the console.
 *
 * Same parts and same behaviour as OpenBSW logger::LoggerComposition (loggerIntegration, unchanged submodule): one
 * ring buffer of logger::BufferedLoggerOutputType (logger/Config.h, size NX_LOGGER_BUFFER_BYTES, PLAT-04), the
 * ConsoleEntryFormatter and the ConsoleEntryOutput; run() prints at most one entry, stop() prints all pending ones.
 * The only difference is LossReportingEntryOutput between the buffer and the console output: when the index of an
 * entry shows that the ring buffer dropped entries before it, one extra line "... n log entries lost (total m)" is
 * printed directly before that entry - at the place where the entries are missing.
 *
 * - The loss line is written straight to the console stream by the reader, it is not logged. It never enters the
 *   ring buffer and therefore cannot push out another entry (no loss caused by the report of a loss).
 * - Normal case (no loss): one subtraction and one comparison per printed entry, in the output context - next to
 *   the 13 and more UART character times of the entry itself. Only after a loss: one line of at most 51
 *   characters (4.4 ms at 115 200 Bd), no heap, 64 bytes of stack.
 * - It is printed in the same run of the idle handler as the entry it precedes, so every run still prints at most
 *   one "block" of output (drain detection of the console flush, SYS-09/SYS-10, IdleOutputDrain.h, unaffected).
 *
 * SYS-15: the console input line shares the terminal (::console::IOutputInterleave, libs/bsw/consoleContext). With
 * setOutputInterleave(), beginOutput() is called before every printed entry (and before its loss line), and
 * outputIdle() in every run() that finds the buffer empty: typed text never lands in front of a log line and shows
 * up again below the log. Entries are neither held back nor dropped - run() still prints one entry per call.
 */

#include "InterleavedEntryOutput.h"
#include "LossReportingEntryOutput.h"

#include <logger/Config.h>
#include <logger/ConsoleEntryFormatter.h>
#include <logger/ConsoleEntryOutput.h>
#include <logger/DefaultLoggerTime.h>

#include <etl/delegate.h>
#include <util/logger/IComponentMapping.h>
#include <util/logger/ILoggerOutput.h>
#include <util/stream/NormalizeLfOutputStream.h>
#include <util/stream/StdoutStream.h>

namespace logger
{

/**
 * Log entries the ring buffer dropped before the console printed them, since start (defined in logger.cpp; console
 * command "logger lost"). Callable from any context. To be moved to logger/logger.h when "ecu status" shows it.
 */
uint32_t lostEntries();

/** BufferSize template argument of a logger::declare::BufferedLoggerOutput (logger/BufferedLoggerOutput.h). */
template<class T>
struct BufferBytesOf;

template<
    uint32_t BufferSize,
    class Lock,
    uint8_t MaxEntrySize,
    class T,
    class Entry,
    class Timestamp,
    class ReadOnlyPredicate>
struct BufferBytesOf<
    declare::BufferedLoggerOutput<BufferSize, Lock, MaxEntrySize, T, Entry, Timestamp, ReadOnlyPredicate>>
{
    static constexpr uint32_t VALUE = BufferSize;
};

class LossReportingLoggerComposition
{
public:
    using ConfigStart = ::etl::delegate<void(::util::logger::ILoggerOutput&)>;
    using ConfigStop  = ::etl::delegate<void()>;
    using E           = BufferedLoggerOutputType::EntryIndexType;
    using Timestamp   = BufferedLoggerOutputType::TimestampType;

    LossReportingLoggerComposition(::util::logger::IComponentMapping& componentMapping, char const* const name)
    : _loggerTime("%u")
    , _loggerFormatter(_loggerTime, name)
    , _consoleLoggerOutput(_loggerFormatter)
    , _lossStdout()
    , _lossStream(_lossStdout)
    , _lossReportingOutput(_consoleLoggerOutput, _lossStream)
    , _interleavedOutput(_lossReportingOutput)
    , _bufferedLoggerOutput(componentMapping, _loggerTime)
    , _entryRef(0)
    {}

    /** SYS-15: share the terminal with the console input line; before start(), from the output context. */
    void setOutputInterleave(::console::IOutputInterleave& interleave)
    {
        _interleavedOutput.setInterleave(interleave);
    }

    void start(ConfigStart const& configStart) { configStart(_bufferedLoggerOutput); }

    /** Print at most one entry (and the loss line before it, if any). Output context only. */
    void run() { _interleavedOutput.afterRun(_bufferedLoggerOutput.outputEntry(_interleavedOutput, _entryRef)); }

    /** Print all pending entries. Output context only. The input line is not drawn again (a reset follows). */
    void stop(ConfigStop const& configStop)
    {
        while (_bufferedLoggerOutput.outputEntry(_interleavedOutput, _entryRef)) {}
        configStop();
    }

    uint32_t getLostEntries() const { return _lossReportingOutput.getLostEntries(); }

private:
    DefaultLoggerTime<> _loggerTime;
    ConsoleEntryFormatter<E, Timestamp> _loggerFormatter;
    ConsoleEntryOutput<E, Timestamp> _consoleLoggerOutput;
    // Same way to the UART as ConsoleEntryOutput (stdout, "\n" -> "\r\n"); both streams are stateless.
    ::util::stream::StdoutStream _lossStdout;
    ::util::stream::NormalizeLfOutputStream _lossStream;
    LossReportingEntryOutput<E, Timestamp> _lossReportingOutput;
    InterleavedEntryOutput<E, Timestamp> _interleavedOutput;
    BufferedLoggerOutputType _bufferedLoggerOutput;
    BufferedLoggerOutputType::EntryRefType _entryRef;
};

} // namespace logger
