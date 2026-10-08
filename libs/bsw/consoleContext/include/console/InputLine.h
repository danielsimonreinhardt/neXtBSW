// SPDX-License-Identifier: Apache-2.0
#pragma once

/* SYS-15: line editor of the console input that shares the terminal with the log output.
 *
 * Keys and echo as in OpenBSW StdioConsoleInput (printable 0x20..0x7E, backspace 0x08/0x7F, ESC, CR/LF, prompt
 * "> "), plus two pieces of state: whether the input line - prompt and typed text - is on the last line of the
 * terminal right now ("shown"), and whether other output is under way ("log busy": between beginOutput() and the
 * next outputIdle()). The logger tells both (IOutputInterleave):
 *
 * - beginOutput(), before a log line: if the line is shown, VT100 "erase line" ESC [ 2 K and then CR - the log line
 *   starts in column 0 of an empty line. 5 characters whatever the length of the typed text (blanks over a full
 *   line would cost up to 132 characters, 11.5 ms, in front of every log line). The log output uses VT100 colours
 *   already, so the terminal has to understand VT100 anyway. The sequence stands BEFORE the CR: a raw capture split
 *   at CR and LF (tools/boardtest) sees it at the end of the input line, the log line itself stays clean.
 * - outputIdle(), when the logger found its buffer empty: prompt and typed text again, once, if there is typed
 *   text. The input line is thus redrawn after the log line, below it. An empty prompt is not drawn again: nobody
 *   is typing, so the log output stays byte for byte as before (no extra line in captures, SYS-11: nothing after
 *   the last line before the power down).
 * - A key while the line is not shown: stored, and drawn together with prompt and line only when the log is not
 *   busy; during a burst of log lines the echo waits for the gap after the burst (at most one log line when the
 *   log is not saturated). No key is lost - they are all in the line - and the burst is not slowed down by
 *   redraws. Enter always draws the line first, so the command stays readable above its answer.
 * - While a command runs (after LINE until prompt()), the line is not active: output passes untouched, and the
 *   console does not read (type-ahead stays in the receive buffer of the UART, IO-17).
 *
 * Costs per gap in the log, only while text is typed: one erase (5 characters, 0.43 ms at 115 200 Bd) in front of
 * the first log line and one redraw (2 + typed characters) after the last one; for the bare prompt the erase once.
 * No buffer besides the line itself (128 characters, as OpenBSW).
 *
 * Pure logic without OS and hardware (etl only); the only output is \p PutByte. Host test with the scheduler model:
 * test/host/InputLineHostTest.cpp. Not thread safe: every call from the context that runs the console input.
 */

#include "console/IOutputInterleave.h"

#include <etl/string.h>

#include <cstdint>

namespace console
{

class InputLine final : public IOutputInterleave
{
public:
    /** Output of one byte to the terminal (the target: putByteToStdout). */
    using PutByte = void (*)(uint8_t);

    /** Capacity of the line, as OpenBSW StdioConsoleInput. */
    static constexpr size_t CAPACITY = 128U;

    enum class Input : uint8_t
    {
        NONE,  ///< nothing to do (no byte, a key that edits the line)
        LINE,  ///< Enter with text: hand line() to the command interpreter, then startCommand()
        PROMPT ///< Enter on an empty line or ESC: prompt()
    };

    explicit InputLine(PutByte const put) : _put(put) {}

    /**
     * One byte from the terminal (the target: getByteFromStdin(), negative when there is none).
     * Printable characters beyond CAPACITY are dropped without echo, so the terminal always shows the line
     * (OpenBSW cleared the whole line silently at that point).
     */
    Input onByte(int32_t const byte)
    {
        if ((byte < 0) || (byte > 0xFF))
        {
            return Input::NONE;
        }
        char const chr = static_cast<char>(byte);
        if ((MIN_VALID_CHR <= chr) && (chr <= MAX_VALID_CHR))
        {
            if (_line.full())
            {
                return Input::NONE;
            }
            _line.push_back(chr);
            if (_shown)
            {
                _put(static_cast<uint8_t>(chr));
            }
            else
            {
                drawUnlessLogBusy();
            }
            return Input::NONE;
        }
        if (ESCAPE == chr)
        {
            _line.clear();
            return Input::PROMPT;
        }
        if ((BACK_SPACE == chr) || (BACK_SPACE_7F == chr))
        {
            if (!_line.empty())
            {
                _line.pop_back();
                if (_shown)
                {
                    _put(static_cast<uint8_t>(BACK_SPACE));
                    _put(static_cast<uint8_t>(' '));
                    _put(static_cast<uint8_t>(BACK_SPACE));
                }
                else
                {
                    drawUnlessLogBusy();
                }
            }
            return Input::NONE;
        }
        if ((CARRIAGE_RETURN == chr) || (LINE_FEED == chr))
        {
            if (_line.empty())
            {
                return Input::PROMPT;
            }
            if (!_shown)
            {
                draw(); // the command stays readable above its answer
            }
            return Input::LINE;
        }
        return Input::NONE;
    }

    /** After Input::LINE: new line for the answer; the line stays in line() until prompt(). */
    void startCommand()
    {
        newline();
        _active = false;
        _shown  = false;
    }

    /** Command done (or Input::PROMPT): empty line and a new prompt. */
    void prompt()
    {
        _line.clear();
        newline();
        putPrompt();
        _active = true;
        _shown  = true;
    }

    ::etl::istring const& line() const { return _line; }

    /** True while prompt and line are on the last line of the terminal (for tests and diagnostics). */
    bool isShown() const { return _shown; }

    void beginOutput() override
    {
        _logBusy = true;
        if (!(_active && _shown))
        {
            return;
        }
        for (char const c : ERASE_LINE)
        {
            _put(static_cast<uint8_t>(c));
        }
        _shown = false;
    }

    void outputIdle() override
    {
        _logBusy = false;
        if (_active && (!_shown) && (!_line.empty()))
        {
            draw();
        }
    }

private:
    static constexpr char BACK_SPACE      = 0x08;
    static constexpr char BACK_SPACE_7F   = 0x7F;
    static constexpr char LINE_FEED       = 0x0A;
    static constexpr char CARRIAGE_RETURN = 0x0D;
    static constexpr char ESCAPE          = 0x1B;
    static constexpr char MIN_VALID_CHR   = 0x20; // space
    static constexpr char MAX_VALID_CHR   = 0x7E; // tilde
    // VT100 EL2 (erase the whole line, the cursor stays), then CR; no terminating NUL is sent
    static constexpr char ERASE_LINE[5] = {ESCAPE, '[', '2', 'K', CARRIAGE_RETURN};

    void newline()
    {
        _put(static_cast<uint8_t>(CARRIAGE_RETURN));
        _put(static_cast<uint8_t>(LINE_FEED));
    }

    void putPrompt()
    {
        _put(static_cast<uint8_t>('>'));
        _put(static_cast<uint8_t>(' '));
    }

    /** Prompt and line from column 0; the cursor is in column 0 whenever the line is not shown. */
    void draw()
    {
        putPrompt();
        for (char const c : _line)
        {
            _put(static_cast<uint8_t>(c));
        }
        _shown = true;
    }

    /** Echo of a key while the line is off the terminal: now if the log is quiet, else after the burst. */
    void drawUnlessLogBusy()
    {
        if (!_logBusy)
        {
            draw();
        }
    }

    PutByte _put;
    ::etl::string<CAPACITY> _line;
    bool _active  = true;
    bool _shown   = false;
    bool _logBusy = false;
};

} // namespace console
