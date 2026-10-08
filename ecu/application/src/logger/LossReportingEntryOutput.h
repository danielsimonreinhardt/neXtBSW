// SPDX-License-Identifier: Apache-2.0
#pragma once

/* SYS-12: report lost log entries on the console, in front of the entry that follows the gap.
 *
 * EntryLossReporter is the part the host test runs against the real OpenBSW EntryBuffer
 * (ecu/application/src/logger/test/host/EntryLossHostTest.cpp); LossReportingEntryOutput only puts it in front of
 * any IEntryOutput (the application: ConsoleEntryOutput, LossReportingLoggerComposition.h).
 */

#include "EntryLoss.h"

#include <etl/span.h>
#include <logger/IEntryOutput.h>
#include <util/stream/IOutputStream.h>

#include <cstdint>

namespace logger
{

/**
 * Feeds the index of every printed entry to an EntryLossDetector and writes the loss line to \p stream when there
 * is a gap. The line goes straight to the stream: it is not a log entry and never enters the ring buffer.
 */
template<class E = uint32_t>
class EntryLossReporter
{
public:
    explicit EntryLossReporter(::util::stream::IOutputStream& stream) : _stream(stream) {}

    /** \param deliveredIndex index handed to IEntryOutput::outputEntry(); call before the entry is printed. */
    void onEntry(E const deliveredIndex)
    {
        E const lost = _detector.onEntry(deliveredIndex);
        if (lost != 0U)
        {
            char line[LOSS_LINE_MAX];
            size_t const length = formatLossLine(line, static_cast<uint32_t>(lost), _detector.getTotal());
            _stream.write(::etl::span<uint8_t const>(reinterpret_cast<uint8_t const*>(line), length));
        }
    }

    /** Entries lost since start (EntryLossDetector::getTotal()). */
    uint32_t getTotal() const { return _detector.getTotal(); }

private:
    ::util::stream::IOutputStream& _stream;
    EntryLossDetector<E> _detector;
};

/** IEntryOutput decorator: loss line (if any) on \p lossStream, then the entry through \p output. */
template<class E = uint32_t, class Timestamp = uint32_t>
class LossReportingEntryOutput : public IEntryOutput<E, Timestamp>
{
public:
    LossReportingEntryOutput(IEntryOutput<E, Timestamp>& output, ::util::stream::IOutputStream& lossStream)
    : IEntryOutput<E, Timestamp>(), _output(output), _reporter(lossStream)
    {}

    void outputEntry(
        E const entryIndex,
        Timestamp const timestamp,
        ::util::logger::ComponentInfo const& componentInfo,
        ::util::logger::LevelInfo const& levelInfo,
        char const* const str,
        ::util::format::IPrintfArgumentReader& argReader) override
    {
        _reporter.onEntry(entryIndex);
        _output.outputEntry(entryIndex, timestamp, componentInfo, levelInfo, str, argReader);
    }

    uint32_t getLostEntries() const { return _reporter.getTotal(); }

private:
    IEntryOutput<E, Timestamp>& _output;
    EntryLossReporter<E> _reporter;
};

} // namespace logger
