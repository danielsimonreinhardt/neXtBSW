/* Host test SYS-15: the console echo does not cut log lines any more.
 *
 * Board 2026-09-18 evening: typing while the log runs put every echoed character in front of the next log line
 * ("s170158: RefApp: ..."), because OpenBSW StdioConsoleInput echoes each key at once and the logger prints its
 * entries in the same context (TASK_IDLE, one entry per run of the idle handler).
 *
 * The test runs the idle handler of the application in the scheduler model (ConsoleModel.h: fixed priorities,
 * preemption, 86.8 us per character on the UART) with REAL code on both sides:
 *  - the new way: console/InterleavedConsoleInput.h (with console/InputLine.h) and the logger decorator
 *    ecu/application/src/logger/InterleavedEntryOutput.h, as wired in console.cpp/logger.cpp;
 *  - the old way (the deliberately inserted fault): OpenBSW console::StdioConsoleInput (StdioConsoleInput.cpp of
 *    the submodule), the logger without interleave.
 * The logger is modelled as a FIFO of entries (the ring buffer, no loss here - SYS-12 is tested elsewhere): each
 * run of the handler prints at most one entry and reports an empty buffer. Keys arrive in the UART receive buffer
 * (IO-17) at given times; commands are answered asynchronously a few ms later (AsyncCommandWrapper), with the
 * "Received console command" log line of AsyncConsole.
 *
 * Checked on the terminal (a small emulator: CR, LF, BS, printable) and on the raw byte stream split at CR/LF as
 * tools/boardtest reads it:
 *  - every log line stands alone on its row / in its piece: no echo character and no prompt in front of it;
 *  - every log entry is printed, in order, and none waits longer than in the old way plus one erase and one
 *    redraw of the input line;
 *  - the command interpreter receives exactly the typed lines (backspace, DEL, ESC, overlong line), every command
 *    is readable as "> <command>" above its answer, a line typed at the end is on the last row;
 *  - without typing, the output is byte for byte the old one.
 * Own test profile (tasks, periods, log texts), no values of the eval board.
 *
 * Build and run (tools/test -k console-input):
 *   python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Werror -Ilibs/bsw/consoleContext/include
 *       -Iecu/application/src/logger -isystem external/openbsw/libs/bsw/stdioConsoleInput/include
 *       -isystem external/openbsw/libs/bsw/util/include -isystem external/openbsw/libs/bsw/logger/include
 *       -isystem external/openbsw/libs/3rdparty/etl/include libs/bsw/consoleContext/test/host/InputLineHostTest.cpp
 *       external/openbsw/libs/bsw/stdioConsoleInput/src/console/StdioConsoleInput.cpp
 *       external/openbsw/libs/bsw/util/src/util/stream/{StdoutStream,SharedOutputStream,
 *       TaggedSharedOutputStream,TaggedOutputHelper}.cpp -o inputline.exe
 */

#include "ConsoleModel.h"
#include "InterleavedEntryOutput.h"
#include "console/InputLine.h"
#include "console/InterleavedConsoleInput.h"

#include <console/StdioConsoleInput.h>
#include <util/stream/BspStubs.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <set>
#include <string>
#include <vector>

// Only passed through the logger decorator, never read: stand-ins for the OpenBSW types (not linked here).
namespace util
{
namespace logger
{
class ComponentInfo
{};

class LevelInfo
{};
} // namespace logger

namespace format
{
class IPrintfArgumentReader
{};
} // namespace format
} // namespace util

using namespace consolemodel;

namespace
{
constexpr uint64_t LOOP_NS         = 4U * NS_PER_US;  // one run of the idle handler without output
constexpr uint64_t COMMAND_DELAY   = 3U * NS_PER_MS;  // AsyncCommandWrapper: the answer comes a bit later
constexpr size_t RX_CAPACITY       = 127U;            // UartRxBuffer (IO-17)
constexpr uint64_t TAIL            = 100U * NS_PER_MS; // quiet end of every scenario: the log drains
constexpr char const* const MARKER = ": RefApp: ";    // every log line has it

struct Key
{
    uint64_t at;
    uint8_t byte;
};

struct LogEntry
{
    uint64_t at;
    std::string text;
};

/* ---------------------------------------------------------------- UART and stdio stubs */

struct Uart
{
    std::deque<Key> rx;
    std::string tx;
    size_t maxBacklog = 0U;
} uart;

} // namespace

extern "C" int32_t getByteFromStdin()
{
    size_t backlog = 0U;
    for (Key const& k : uart.rx)
    {
        if (k.at > model->now())
        {
            break;
        }
        ++backlog;
    }
    uart.maxBacklog = std::max(uart.maxBacklog, backlog);
    if (backlog == 0U)
    {
        return -1;
    }
    uint8_t const byte = uart.rx.front().byte;
    uart.rx.pop_front();
    return byte;
}

extern "C" void putByteToStdout(uint8_t const byte)
{
    uart.tx += static_cast<char>(byte);
    model->character();
}

namespace
{
/* ---------------------------------------------------------------- logger: console output of an entry */

class TextEntryOutput : public ::logger::IEntryOutput<uint32_t, uint32_t>
{
public:
    void outputEntry(
        uint32_t,
        uint32_t,
        ::util::logger::ComponentInfo const&,
        ::util::logger::LevelInfo const&,
        char const* const str,
        ::util::format::IPrintfArgumentReader&) override
    {
        for (char const* p = str; *p != '\0'; ++p)
        {
            putByteToStdout(static_cast<uint8_t>(*p));
        }
        putByteToStdout('\r');
        putByteToStdout('\n');
    }
};

/* ---------------------------------------------------------------- command interpreter (AsyncConsole) */

class Commands
{
public:
    std::vector<std::string> received;
    std::deque<std::string>* logFifo = nullptr;

    void onLine(
        ::util::stream::ISharedOutputStream& stream,
        ::etl::istring const& line,
        ::console::StdioConsoleInput::OnLineProcessed const& done)
    {
        std::string const text(line.c_str(), line.size());
        received.push_back(text);
        logFifo->push_back(stamp() + "CONSOLE: INFO: Received console command \"" + text + "\"");
        _stream  = &stream;
        _done    = done;
        _pending = true;
        _at      = model->now() + COMMAND_DELAY;
    }

    /** Answer and "ok" in a later run of the idle context; true if something was done. */
    bool step()
    {
        if ((!_pending) || (model->now() < _at))
        {
            return false;
        }
        _pending = false;
        ::util::stream::IOutputStream& out = _stream->startOutput(nullptr);
        // same length in both ways (the old way received a different overlong line)
        write(out, "answer " + std::to_string(received.size()) + "\n");
        _stream->endOutput(nullptr);
        ::util::stream::IOutputStream& end = _stream->startOutput(nullptr);
        write(end, "ok\n");
        _stream->endOutput(nullptr);
        logFifo->push_back(stamp() + "CONSOLE: INFO: Console command succeeded");
        _done();
        return true;
    }

    bool pending() const { return _pending; }

    uint64_t dueAt() const { return _at; }

    static std::string stamp()
    {
        return std::to_string(static_cast<unsigned>(model->now() / NS_PER_US)) + MARKER;
    }

private:
    static void write(::util::stream::IOutputStream& out, std::string const& text)
    {
        for (char const c : text)
        {
            out.write(static_cast<uint8_t>(c));
        }
    }

    ::util::stream::ISharedOutputStream* _stream = nullptr;
    ::console::StdioConsoleInput::OnLineProcessed _done;
    bool _pending = false;
    uint64_t _at  = 0U;
};

/* ---------------------------------------------------------------- scenario and run */

enum class Way
{
    NEW,
    OLD
};

struct Scenario
{
    char const* name;
    std::vector<TaskSpec> tasks;
    uint64_t horizon;
    std::vector<LogEntry> logs; // sorted by time
    std::vector<Key> keys;      // sorted by time
};

struct Result
{
    std::string tx;
    std::vector<std::string> received;
    std::vector<std::string> printed; // log texts in the order they were printed
    std::vector<std::string> logged;  // log texts in the order they entered the buffer
    uint64_t maxLatency = 0U;         // entered the buffer -> last character on the UART
    size_t maxBacklog   = 0U;
    size_t leftInRx     = 0U;
    bool shown          = false;
    std::string line; // typed, not yet entered
};

Result run(Scenario const& s, Way const way)
{
    Model m(s.tasks, s.horizon);
    model = &m;
    uart  = Uart();
    for (Key const& k : s.keys)
    {
        uart.rx.push_back(k);
    }

    std::deque<std::string> fifo;       // the ring buffer of the logger
    std::deque<uint64_t> fifoAt;        // when each entry entered it
    Commands commands;
    commands.logFifo = &fifo;

    ::console::InterleavedConsoleInput newInput(" ", "\r\n");
    ::console::StdioConsoleInput oldInput(" ", "\r\n");
    newInput.init(::console::InterleavedConsoleInput::OnLineReceived::create<Commands, &Commands::onLine>(commands));
    oldInput.init(::console::StdioConsoleInput::OnLineReceived::create<Commands, &Commands::onLine>(commands));

    TextEntryOutput text;
    ::logger::InterleavedEntryOutput<uint32_t, uint32_t> entryOutput(text);
    if (way == Way::NEW)
    {
        entryOutput.setInterleave(newInput.outputInterleave());
    }
    ::util::logger::ComponentInfo const component;
    ::util::logger::LevelInfo const level;
    ::util::format::IPrintfArgumentReader reader;

    Result r;
    size_t nextLog = 0U;
    uint32_t index = 0U;
    while (m.now() < s.horizon)
    {
        // entries of the command interpreter (pushed in the last run), then those the tasks logged by now
        while (fifoAt.size() < fifo.size())
        {
            fifoAt.push_back(m.now());
            r.logged.push_back(fifo[fifoAt.size() - 1U]);
        }
        while ((nextLog < s.logs.size()) && (s.logs[nextLog].at <= m.now()))
        {
            fifo.push_back(s.logs[nextLog].text);
            fifoAt.push_back(s.logs[nextLog].at);
            r.logged.push_back(s.logs[nextLog].text);
            ++nextLog;
        }

        size_t const txBefore = uart.tx.size();
        size_t const rxBefore = uart.rx.size();

        // ::logger::run(): at most one entry (LossReportingLoggerComposition::run)
        bool printed = false;
        if (!fifo.empty())
        {
            std::string const entry = fifo.front();
            uint64_t const at       = fifoAt.front();
            fifo.pop_front();
            fifoAt.pop_front();
            entryOutput.outputEntry(index++, 0U, component, level, entry.c_str(), reader);
            r.printed.push_back(entry);
            r.maxLatency = std::max(r.maxLatency, m.now() - at);
            printed      = true;
        }
        entryOutput.afterRun(printed);

        // ::console::run(): at most one key
        if (way == Way::NEW)
        {
            newInput.run();
        }
        else
        {
            oldInput.run();
        }

        // the command itself, queued in the same context
        bool const answered = commands.step();

        m.work(LOOP_NS);
        bool const busy = printed || answered || (uart.tx.size() != txBefore) || (uart.rx.size() != rxBefore)
                          || !fifo.empty();
        if (!busy)
        {
            // nothing to do: sleep until the next log entry, key or answer
            uint64_t next = s.horizon;
            if (nextLog < s.logs.size())
            {
                next = std::min(next, s.logs[nextLog].at);
            }
            if (!uart.rx.empty())
            {
                next = std::min(next, std::max(uart.rx.front().at, m.now()));
            }
            if (commands.pending())
            {
                next = std::min(next, commands.dueAt());
            }
            if (next > m.now())
            {
                m.idleUntil(next);
            }
            else if (!uart.rx.empty() && (uart.rx.front().at <= m.now()))
            {
                // a key waits while a command runs (the console does not read then)
                m.idleUntil(std::min(s.horizon, commands.pending() ? commands.dueAt() : m.now() + LOOP_NS));
            }
            else
            {
                // no progress possible
            }
        }
    }
    r.tx         = uart.tx;
    r.received   = commands.received;
    r.maxBacklog = uart.maxBacklog;
    r.leftInRx   = uart.rx.size();
    model        = nullptr;
    return r;
}

/* ---------------------------------------------------------------- views of the output */

/** Rows of a terminal that shows \p tx (CR, LF, BS, VT100 ESC [ 2 K, printable; no wrap). */
std::vector<std::string> screen(std::string const& tx)
{
    std::vector<std::string> rows(1U);
    size_t row = 0U;
    size_t col = 0U;
    for (size_t i = 0U; i < tx.size(); ++i)
    {
        char const c = tx[i];
        if (c == '\x1b')
        {
            CHECK(tx.compare(i, 4U, "\x1b[2K") == 0); // the only sequence the console input sends
            rows[row].clear();                         // erase the whole line, the cursor stays
            i += 3U;
        }
        else if (c == '\r')
        {
            col = 0U;
        }
        else if (c == '\n')
        {
            ++row;
            if (row == rows.size())
            {
                rows.emplace_back();
            }
        }
        else if (c == '\b')
        {
            if (col > 0U)
            {
                --col;
            }
        }
        else
        {
            std::string& r = rows[row];
            if (r.size() <= col)
            {
                r.resize(col + 1U, ' ');
            }
            r[col] = c;
            ++col;
        }
    }
    for (std::string& r : rows)
    {
        r.erase(r.find_last_not_of(' ') + 1U);
    }
    return rows;
}

/** Pieces of \p tx split at CR LF, LF or CR, as tools/boardtest/console.py reads the console. */
std::vector<std::string> pieces(std::string const& tx)
{
    std::vector<std::string> out(1U);
    for (size_t i = 0U; i < tx.size(); ++i)
    {
        char const c = tx[i];
        if ((c == '\r') || (c == '\n'))
        {
            if ((c == '\r') && ((i + 1U) < tx.size()) && (tx[i + 1U] == '\n'))
            {
                ++i;
            }
            out.emplace_back();
        }
        else
        {
            out.back() += c;
        }
    }
    return out;
}

bool isLogRow(std::string const& row) { return row.find(MARKER) != std::string::npos; }

/** Rows (or pieces) with a log marker that are not exactly one log line; the first one in \p example. */
size_t cutLogLines(std::vector<std::string> const& rows, std::set<std::string> const& texts, std::string& example)
{
    size_t bad = 0U;
    for (std::string const& row : rows)
    {
        if (isLogRow(row) && (texts.count(row) == 0U))
        {
            if (bad == 0U)
            {
                example = row;
            }
            ++bad;
        }
    }
    return bad;
}

/** Lines the command interpreter must receive for \p keys (reference editor: same keys as InputLine). */
std::vector<std::string> expectedLines(std::vector<Key> const& keys, std::string& rest)
{
    std::vector<std::string> lines;
    std::string line;
    for (Key const& k : keys)
    {
        uint8_t const b = k.byte;
        if ((b >= 0x20U) && (b <= 0x7EU))
        {
            if (line.size() < ::console::InputLine::CAPACITY)
            {
                line += static_cast<char>(b);
            }
        }
        else if ((b == 0x08U) || (b == 0x7FU))
        {
            if (!line.empty())
            {
                line.pop_back();
            }
        }
        else if (b == 0x1BU)
        {
            line.clear();
        }
        else if ((b == '\r') || (b == '\n'))
        {
            if (!line.empty())
            {
                lines.push_back(line);
            }
            line.clear();
        }
        else
        {
            // ignored
        }
    }
    rest = line;
    return lines;
}

/* ---------------------------------------------------------------- scenario builders */

struct Rng
{
    uint32_t state;

    uint32_t next()
    {
        state = (state * 1664525U) + 1013904223U;
        return state >> 8U;
    }

    uint64_t between(uint64_t const lo, uint64_t const hi) { return lo + (next() % (hi - lo + 1U)); }
};

std::vector<TaskSpec> profileTasks()
{
    // own profile: a fast control task and a slower communication task above the console
    return {
        {"ctl", 6U, 1U * NS_PER_MS, 150U * NS_PER_US, 120U * NS_PER_US},
        {"com", 3U, 10U * NS_PER_MS, 2U * NS_PER_MS, 900U * NS_PER_US},
    };
}

std::string logText(uint64_t const at, char const* const what, unsigned const n)
{
    return std::to_string(static_cast<unsigned>(at / NS_PER_US)) + MARKER + "APP: INFO: " + what + " "
           + std::to_string(n);
}

void periodicLogs(std::vector<LogEntry>& logs, uint64_t from, uint64_t const to, uint64_t const period,
                  char const* const what)
{
    unsigned n = 0U;
    for (; from < to; from += period)
    {
        logs.push_back({from, logText(from, what, n++)});
    }
}

void burst(std::vector<LogEntry>& logs, uint64_t const at, unsigned const count)
{
    for (unsigned i = 0U; i < count; ++i)
    {
        logs.push_back({at, logText(at, "Initialize component", i)});
    }
}

/** Types \p text from \p at, one key every \p gapLo..gapHi; returns the time after the last key. */
uint64_t type(std::vector<Key>& keys, uint64_t at, std::string const& text, Rng& rng, uint64_t const gapLo,
              uint64_t const gapHi)
{
    for (char const c : text)
    {
        keys.push_back({at, static_cast<uint8_t>(c)});
        at += rng.between(gapLo, gapHi);
    }
    return at;
}

/** All of \p text at once (tools/boardtest writes the whole command). */
void paste(std::vector<Key>& keys, uint64_t const at, std::string const& text)
{
    for (char const c : text)
    {
        keys.push_back({at, static_cast<uint8_t>(c)});
    }
}

void sortScenario(Scenario& s)
{
    std::stable_sort(
        s.logs.begin(), s.logs.end(), [](LogEntry const& a, LogEntry const& b) { return a.at < b.at; });
    std::stable_sort(s.keys.begin(), s.keys.end(), [](Key const& a, Key const& b) { return a.at < b.at; });
}

/* ---------------------------------------------------------------- checks */

struct Summary
{
    size_t cutRows   = 0U;
    size_t cutPieces = 0U;
    std::string example;
};

Summary checkNew(Scenario const& s, Result const& r, Result const& old, uint64_t const maxTyped)
{
    Summary sum;
    std::set<std::string> const texts(r.logged.begin(), r.logged.end());
    std::vector<std::string> const rows = screen(r.tx);

    // 1. no log line with an echo character or a prompt in front, on the terminal and in the raw stream
    sum.cutRows   = cutLogLines(rows, texts, sum.example);
    std::string ignored;
    sum.cutPieces = cutLogLines(pieces(r.tx), texts, ignored);
    CHECK(sum.cutRows == 0U);
    CHECK(sum.cutPieces == 0U);
    if (sum.cutRows != 0U)
    {
        std::printf("    %s: cut log row \"%s\"\n", s.name, sum.example.c_str());
    }

    // 2. every entry printed, in order, and on the terminal in that order
    CHECK(r.printed == r.logged);
    std::vector<std::string> onScreen;
    for (std::string const& row : rows)
    {
        if (isLogRow(row))
        {
            onScreen.push_back(row);
        }
    }
    CHECK(onScreen == r.logged);

    // 3. no entry waits longer than in the old way plus one erase (5) and one redraw (2 + typed) of the longest
    //    input line, and one more redraw per command (Enter draws the line if a log line took it off)
    uint64_t const perDraw = (5U + 2U + maxTyped) * CHARACTER_NS;
    uint64_t const bound   = old.maxLatency + ((r.received.size() + 1U) * perDraw);
    CHECK(r.maxLatency <= bound);
    if (r.maxLatency > bound)
    {
        std::printf("    %s: latency %.3f ms > %.3f ms\n", s.name, ms(r.maxLatency), ms(bound));
    }

    // 4. input complete: the interpreter got exactly the typed lines, each readable above its answer
    std::string rest;
    std::vector<std::string> const expected = expectedLines(s.keys, rest);
    CHECK(r.received == expected);
    CHECK(r.leftInRx == 0U);
    CHECK(r.maxBacklog <= RX_CAPACITY);
    for (size_t i = 0U; i < expected.size(); ++i)
    {
        auto const prompt = std::find(rows.begin(), rows.end(), "> " + expected[i]);
        CHECK(prompt != rows.end());
        CHECK(std::find(prompt, rows.end(), " answer " + std::to_string(i + 1U)) != rows.end());
    }

    // 5. a line typed but not entered is on the last row, with its prompt
    if (!rest.empty())
    {
        std::string last;
        for (std::string const& row : rows)
        {
            if (!row.empty())
            {
                last = row;
            }
        }
        CHECK(last == ("> " + rest));
    }
    return sum;
}

/* ---------------------------------------------------------------- tests */

// A: periodic log (every 40 ms), a command typed by hand, then a second one left unfinished.
Scenario scenarioTyping(uint32_t const seed)
{
    Scenario s{"typing during a periodic log", profileTasks(), 2500U * NS_PER_MS, {}, {}};
    periodicLogs(s.logs, 5U * NS_PER_MS, s.horizon - TAIL, 40U * NS_PER_MS, "cycle");
    Rng rng{seed};
    uint64_t const t = type(s.keys, 300U * NS_PER_MS, "sbc status\r", rng, 30U * NS_PER_MS, 160U * NS_PER_MS);
    (void)type(s.keys, t + (200U * NS_PER_MS), "ecu st", rng, 30U * NS_PER_MS, 160U * NS_PER_MS);
    sortScenario(s);
    return s;
}

// B: start burst of 60 entries, then a CAN log every 13 ms; a command pasted into the burst (boardtest), a second
// one pasted while the first still runs (type-ahead), a third typed by hand in the 13-ms log.
Scenario scenarioBurst(uint32_t const seed)
{
    Scenario s{"burst and fast log, pasted commands", profileTasks(), 2000U * NS_PER_MS, {}, {}};
    burst(s.logs, 20U * NS_PER_MS, 60U);
    periodicLogs(s.logs, 400U * NS_PER_MS, s.horizon - TAIL, 13U * NS_PER_MS, "can rx 0x210");
    paste(s.keys, 60U * NS_PER_MS, "rte runnables\r\n");
    paste(s.keys, 61U * NS_PER_MS, "adc all\r\n");
    Rng rng{seed};
    (void)type(s.keys, 700U * NS_PER_MS, "gd status\r", rng, 20U * NS_PER_MS, 90U * NS_PER_MS);
    sortScenario(s);
    return s;
}

// C: editing keys in the log: backspace, DEL, ESC, an overlong line (the rest beyond 128 is dropped).
Scenario scenarioEditing(uint32_t const seed)
{
    Scenario s{"editing keys and an overlong line", profileTasks(), 3000U * NS_PER_MS, {}, {}};
    periodicLogs(s.logs, 1U * NS_PER_MS, s.horizon - TAIL, 25U * NS_PER_MS, "tick");
    Rng rng{seed};
    uint64_t t = type(s.keys, 100U * NS_PER_MS, "sbx\bc stat\x7fus\r", rng, 20U * NS_PER_MS, 70U * NS_PER_MS);
    t          = type(s.keys, t + (100U * NS_PER_MS), "junk\x1b", rng, 20U * NS_PER_MS, 70U * NS_PER_MS);
    t          = type(s.keys, t, "ecu status\r", rng, 20U * NS_PER_MS, 70U * NS_PER_MS);
    std::string longLine(140U, 'x');
    longLine += "\r";
    (void)type(s.keys, t + (100U * NS_PER_MS), longLine, rng, 2U * NS_PER_MS, 6U * NS_PER_MS);
    sortScenario(s);
    return s;
}

// D: random keys (printable, backspace, Enter) in random log bursts.
Scenario scenarioRandom(uint32_t const seed)
{
    Scenario s{"random keys in random log bursts", profileTasks(), 1500U * NS_PER_MS, {}, {}};
    Rng rng{seed};
    for (uint64_t t = 0U; t < (s.horizon - TAIL); t += rng.between(15U * NS_PER_MS, 150U * NS_PER_MS))
    {
        unsigned const n = static_cast<unsigned>(rng.between(1U, 5U));
        for (unsigned i = 0U; i < n; ++i)
        {
            s.logs.push_back({t, logText(t, "event", i)});
        }
    }
    for (uint64_t t = 50U * NS_PER_MS; t < (s.horizon - (200U * NS_PER_MS));
         t += rng.between(8U * NS_PER_MS, 150U * NS_PER_MS))
    {
        uint32_t const k = rng.next() % 20U;
        uint8_t const byte
            = (k == 0U) ? static_cast<uint8_t>('\r') : ((k == 1U) ? 0x08U : static_cast<uint8_t>('a' + (k % 26U)));
        s.keys.push_back({t, byte});
    }
    sortScenario(s);
    return s;
}

uint64_t longestTyped(Scenario const& s)
{
    uint64_t longest = 0U;
    uint64_t current = 0U;
    for (Key const& k : s.keys)
    {
        if ((k.byte == '\r') || (k.byte == '\n') || (k.byte == 0x1BU))
        {
            current = 0U;
        }
        else if ((k.byte == 0x08U) || (k.byte == 0x7FU))
        {
            current = (current > 0U) ? (current - 1U) : 0U;
        }
        else
        {
            current = std::min<uint64_t>(current + 1U, ::console::InputLine::CAPACITY);
        }
        longest = std::max(longest, current);
    }
    return longest;
}

void runScenario(Scenario const& s, bool const expectOldCut)
{
    Result const newWay = run(s, Way::NEW);
    Result const oldWay = run(s, Way::OLD);
    Summary const sum   = checkNew(s, newWay, oldWay, longestTyped(s));

    // the deliberately inserted fault: OpenBSW StdioConsoleInput without interleave cuts log lines
    std::set<std::string> const texts(oldWay.logged.begin(), oldWay.logged.end());
    std::string example;
    size_t const oldCut = cutLogLines(screen(oldWay.tx), texts, example);
    if (expectOldCut)
    {
        CHECK(oldCut > 0U);
    }
    std::printf(
        "  %-40s entries %3zu, commands %zu | new: cut rows %zu, max latency %7.3f ms, %6zu bytes | old: cut rows "
        "%zu, max latency %7.3f ms, %6zu bytes%s%s%s\n",
        s.name,
        newWay.logged.size(),
        newWay.received.size(),
        sum.cutRows,
        ms(newWay.maxLatency),
        newWay.tx.size(),
        oldCut,
        ms(oldWay.maxLatency),
        oldWay.tx.size(),
        oldCut != 0U ? " (e.g. \"" : "",
        oldCut != 0U ? example.substr(0U, 40U).c_str() : "",
        oldCut != 0U ? "...\")" : "");
}

void testInputLineUnit()
{
    static std::string out;
    out.clear();
    ::console::InputLine line([](uint8_t const b) { out += static_cast<char>(b); });
    using Input = ::console::InputLine::Input;

    // no prompt yet: the first key draws prompt and line (the cursor is in column 0 after a log line)
    CHECK(line.onByte(-1) == Input::NONE);
    CHECK(line.onByte('a') == Input::NONE);
    CHECK(out == "> a");
    CHECK(line.isShown());
    // shown: plain echo, backspace as "\b \b"
    CHECK(line.onByte('b') == Input::NONE);
    CHECK(line.onByte(0x7F) == Input::NONE);
    CHECK(out == "> ab\b \b");
    // a log line: erase the whole line (VT100 EL2), then CR - the sequence stands in front of the CR
    out.clear();
    line.beginOutput();
    CHECK(out == "\x1b[2K\r");
    CHECK(!line.isShown());
    line.beginOutput(); // twice: nothing
    CHECK(out == "\x1b[2K\r");
    // buffer empty: draw again once
    out.clear();
    line.outputIdle();
    line.outputIdle();
    CHECK(out == "> a");
    // a key while not shown and the log busy: stored, drawn after the burst (no redraw per key in a burst)
    line.beginOutput();
    out.clear();
    CHECK(line.onByte('c') == Input::NONE);
    CHECK(line.onByte('d') == Input::NONE);
    CHECK(line.onByte(0x08) == Input::NONE);
    CHECK(out.empty());
    line.outputIdle();
    CHECK(out == "> ac");
    // Enter while not shown: draw first, then the command
    line.beginOutput();
    out.clear();
    CHECK(line.onByte('\r') == Input::LINE);
    CHECK(out == "> ac");
    CHECK(std::string(line.line().c_str()) == "ac");
    out.clear();
    line.startCommand();
    CHECK(out == "\r\n");
    // while the command runs: output passes, nothing is erased or drawn
    out.clear();
    line.beginOutput();
    line.outputIdle();
    CHECK(out.empty());
    line.prompt();
    CHECK(out == "\r\n> ");
    CHECK(line.line().empty());
    // empty prompt: erased before a log line, not drawn again (nobody types)
    out.clear();
    line.beginOutput();
    line.outputIdle();
    CHECK(out == "\x1b[2K\r");
    // a key while not shown and the log quiet: prompt, line and the key at once
    out.clear();
    CHECK(line.onByte('q') == Input::NONE);
    CHECK(out == "> q");
    CHECK(line.onByte(0x7F) == Input::NONE);
    // Enter or ESC on an empty line: new prompt
    CHECK(line.onByte('\n') == Input::PROMPT);
    CHECK(line.onByte('x') == Input::NONE);
    CHECK(line.onByte(0x1B) == Input::PROMPT);
    CHECK(line.line().empty());
    // other control and 8-bit bytes are ignored
    out.clear();
    CHECK(line.onByte(0x01) == Input::NONE);
    CHECK(line.onByte(0xC3) == Input::NONE);
    CHECK(line.onByte(0x100) == Input::NONE);
    CHECK(out.empty());
    // capacity: the 129th character is dropped without echo
    line.prompt();
    for (size_t i = 0U; i < ::console::InputLine::CAPACITY; ++i)
    {
        (void)line.onByte('y');
    }
    out.clear();
    CHECK(line.onByte('z') == Input::NONE);
    CHECK(out.empty());
    CHECK(line.line().size() == ::console::InputLine::CAPACITY);
    // the erase costs 5 characters whatever the length of the line
    line.beginOutput();
    CHECK(out == "\x1b[2K\r");
}

void testNoTypingSameBytes()
{
    // without keys the output is byte for byte the old one (nothing is erased or drawn)
    Scenario s{"no typing", profileTasks(), 1000U * NS_PER_MS, {}, {}};
    burst(s.logs, 10U * NS_PER_MS, 30U);
    periodicLogs(s.logs, 200U * NS_PER_MS, s.horizon - TAIL, 20U * NS_PER_MS, "cycle");
    sortScenario(s);
    Result const newWay = run(s, Way::NEW);
    Result const oldWay = run(s, Way::OLD);
    CHECK(newWay.tx == oldWay.tx);
    CHECK(newWay.printed == newWay.logged);
    CHECK(newWay.maxLatency == oldWay.maxLatency);
    std::printf("  %-40s entries %3zu, output identical: %s\n", s.name, newWay.logged.size(),
                (newWay.tx == oldWay.tx) ? "yes" : "NO");
}

void testDecoratorWithoutInterleave()
{
    // the logger decorator alone (no console): passes entries on, nothing else
    Model m(profileTasks(), 10U * NS_PER_MS);
    model = &m;
    uart  = Uart();
    TextEntryOutput text;
    ::logger::InterleavedEntryOutput<uint32_t, uint32_t> entryOutput(text);
    ::util::logger::ComponentInfo const component;
    ::util::logger::LevelInfo const level;
    ::util::format::IPrintfArgumentReader reader;
    entryOutput.outputEntry(1U, 0U, component, level, "1: RefApp: X", reader);
    entryOutput.afterRun(false);
    CHECK(uart.tx == "1: RefApp: X\r\n");
    model = nullptr;
}

} // namespace

int main()
{
    std::printf("console input line and log output (SYS-15)\n");
    testInputLineUnit();
    testDecoratorWithoutInterleave();
    testNoTypingSameBytes();
    runScenario(scenarioTyping(7U), true);
    runScenario(scenarioBurst(11U), true);
    runScenario(scenarioEditing(13U), true);
    for (uint32_t seed = 1U; seed <= 12U; ++seed)
    {
        runScenario(scenarioRandom(seed * 7919U), false);
    }
    return report("console-input");
}
