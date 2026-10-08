// SPDX-License-Identifier: Apache-2.0
#pragma once

/* SYS-15: console input on stdin/stdout that keeps its line apart from the log output.
 *
 * Drop-in for OpenBSW console::StdioConsoleInput (same constructor, init(), shutdown(), run(), same callback type,
 * same tagged output stream for the answer of a command); the line editing lives in console/InputLine.h, which
 * also implements IOutputInterleave for the logger. Header only, so the submodule stays unchanged.
 */

#include "console/InputLine.h"

#include <console/StdioConsoleInput.h>
#include <util/stream/BspStubs.h>
#include <util/stream/SharedOutputStream.h>
#include <util/stream/StdoutStream.h>
#include <util/stream/TaggedSharedOutputStream.h>

namespace console
{

class InterleavedConsoleInput
{
public:
    using OnLineProcessed = StdioConsoleInput::OnLineProcessed;
    using OnLineReceived  = StdioConsoleInput::OnLineReceived;

    InterleavedConsoleInput(InterleavedConsoleInput const&)            = delete;
    InterleavedConsoleInput& operator=(InterleavedConsoleInput const&) = delete;

    /** \param prefix, suffix decoration of the answer lines (util::stream::TaggedOutputHelper) */
    InterleavedConsoleInput(char const* const prefix, char const* const suffix)
    : _stdoutStream()
    , _sharedOutputStream(_stdoutStream)
    , _taggedSharedOutputStream(_sharedOutputStream, prefix, suffix)
    , _inputLine(&putByteToStdout)
    , _onLineReceived(OnLineReceived::create<&InterleavedConsoleInput::ignoreLine>())
    , _isSuspended(false)
    {}

    void init(OnLineReceived const& onLineReceived) { _onLineReceived = onLineReceived; }

    void shutdown() { _onLineReceived = OnLineReceived::create<&InterleavedConsoleInput::ignoreLine>(); }

    /** Reads at most one byte (as StdioConsoleInput); nothing while a command runs. */
    void run()
    {
        if (_isSuspended)
        {
            return;
        }
        switch (_inputLine.onByte(getByteFromStdin()))
        {
            case InputLine::Input::LINE:
            {
                _inputLine.startCommand();
                _isSuspended = true;
                _onLineReceived(
                    _taggedSharedOutputStream,
                    _inputLine.line(),
                    OnLineProcessed::create<InterleavedConsoleInput, &InterleavedConsoleInput::onLineProcessed>(
                        *this));
                break;
            }
            case InputLine::Input::PROMPT:
            {
                _inputLine.prompt();
                break;
            }
            default:
            {
                break;
            }
        }
    }

    /** The input line, for the logger (IOutputInterleave). */
    IOutputInterleave& outputInterleave() { return _inputLine; }

private:
    /** Default reaction to a line, as in StdioConsoleInput: done right away. */
    static void ignoreLine(
        ::util::stream::ISharedOutputStream& /* outputStream */,
        ::etl::istring const& /* line */,
        OnLineProcessed const& callback)
    {
        callback();
    }

    void onLineProcessed()
    {
        _isSuspended = false;
        _inputLine.prompt();
    }

    ::util::stream::StdoutStream _stdoutStream;
    ::util::stream::SharedOutputStream _sharedOutputStream;
    ::util::stream::TaggedSharedOutputStream _taggedSharedOutputStream;
    InputLine _inputLine;
    OnLineReceived _onLineReceived;
    bool _isSuspended;
};

} // namespace console
