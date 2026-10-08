// SPDX-License-Identifier: Apache-2.0
#pragma once

/* SYS-15: log entries and the console input line on one terminal.
 *
 * IEntryOutput decorator in front of the console output of the logger (LossReportingLoggerComposition.h): before
 * every entry it asks the console input line to leave the terminal (::console::IOutputInterleave::beginOutput),
 * and after a run of the logger that printed nothing it lets the line come back (outputIdle). Without an interleave
 * (setInterleave() not called) it only passes the entries on. It never holds an entry back and adds no buffer; the
 * cost per entry is one call, the output of the console input line (console/InputLine.h) is bounded there.
 *
 * Host test: libs/bsw/consoleContext/test/host/InputLineHostTest.cpp (scheduler model, together with the real
 * console input).
 */

#include <console/IOutputInterleave.h>
#include <logger/IEntryOutput.h>

namespace logger
{

template<class E = uint32_t, class Timestamp = uint32_t>
class InterleavedEntryOutput : public IEntryOutput<E, Timestamp>
{
public:
    explicit InterleavedEntryOutput(IEntryOutput<E, Timestamp>& output)
    : IEntryOutput<E, Timestamp>(), _output(output), _interleave(nullptr)
    {}

    /** Share the terminal with \p interleave; from the context that prints the log (and runs the console). */
    void setInterleave(::console::IOutputInterleave& interleave) { _interleave = &interleave; }

    /** After one run of the logger: \p printed is false when the buffer was empty (the input line may return). */
    void afterRun(bool const printed)
    {
        if ((!printed) && (_interleave != nullptr))
        {
            _interleave->outputIdle();
        }
    }

    void outputEntry(
        E const entryIndex,
        Timestamp const timestamp,
        ::util::logger::ComponentInfo const& componentInfo,
        ::util::logger::LevelInfo const& levelInfo,
        char const* const str,
        ::util::format::IPrintfArgumentReader& argReader) override
    {
        if (_interleave != nullptr)
        {
            _interleave->beginOutput();
        }
        _output.outputEntry(entryIndex, timestamp, componentInfo, levelInfo, str, argReader);
    }

private:
    IEntryOutput<E, Timestamp>& _output;
    ::console::IOutputInterleave* _interleave;
};

} // namespace logger
