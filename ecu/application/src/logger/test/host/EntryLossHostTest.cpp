/* Host test of the report of lost log entries (SYS-12).
 *
 * Finding PLAT-04 / board test 2026-09-18: with a 4096-byte logger buffer the first nine start-up lines were missing
 * without any hint. logger::EntryBuffer (OpenBSW) drops the oldest entries on overflow and moves a lagging reader on
 * to the oldest remaining one; the console never printed the entry index, so the gap was invisible. Since SYS-12
 * the application puts EntryLossReporter in front of the console output (LossReportingEntryOutput.h): it prints
 * "... n log entries lost (total m)" directly before the first entry after a gap.
 *
 * The test runs the REAL OpenBSW EntryBuffer (external/openbsw/libs/bsw/logger/include/logger/EntryBuffer.h,
 * header only) with a deliberately small buffer, and the real NormalizeLfOutputStream ("\n" -> "\r\n", as on the
 * console). The reader mirrors BufferedLoggerOutput::outputEntry() (getNextEntry(), then the entry with
 * entryRef.getIndex()) followed by LossReportingEntryOutput::outputEntry() (reporter first, then the entry) and
 * ConsoleEntryOutput (entry text + "\n"). Every entry carries its sequence number, so the printed text can be
 * checked against what was logged.
 *
 * Parts:
 *  1. EntryLossDetector: normal sequence, gap at the start, gap in between, wrap of the index, saturation.
 *  2. formatLossLine: exact text, singular, longest line fits LOSS_LINE_MAX.
 *  3. No loss: a reader that keeps up and bursts below the capacity print no loss line.
 *  4. Start-up burst larger than the buffer (the board finding): one loss line first, then the survivors in order.
 *  5. Loss in between: the line sits exactly between the last entry before and the first entry after the gap.
 *  6. Random interleaving of writes and reads (32-bit and 8-bit index, the latter wraps many times): every gap is
 *     reported with its exact size, no line without a gap, order kept, printed + lost = logged.
 *  7. Deliberately inserted fault: without the reporter (the state before SYS-12) the check of part 4 fails.
 * No target hardware needed.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iecu/application/src/logger -isystem external/openbsw/libs/bsw/logger/include \
 *       -isystem external/openbsw/libs/bsw/util/include -isystem external/openbsw/libs/3rdparty/etl/include \
 *       ecu/application/src/logger/test/host/EntryLossHostTest.cpp \
 *       external/openbsw/libs/bsw/util/src/util/stream/NormalizeLfOutputStream.cpp -o build/host/entry_loss_test.exe
 *   build/host/entry_loss_test.exe
 */

#include "EntryLoss.h"
#include "LossReportingEntryOutput.h"

#include <logger/EntryBuffer.h>
#include <util/stream/NormalizeLfOutputStream.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

/* ---------------------------------------------------------------- console model */

/** Captures what reaches the "UART". */
class CaptureStream : public ::util::stream::IOutputStream
{
public:
    bool isEof() const override { return false; }

    void write(uint8_t const data) override { text.push_back(static_cast<char>(data)); }

    void write(::etl::span<uint8_t const> const& buffer) override
    {
        text.append(reinterpret_cast<char const*>(buffer.data()), buffer.size());
    }

    std::string text;
};

constexpr uint8_t MAX_ENTRY = 64U;

/** Logger with a small ring buffer, one reader, console output as in the application (see file header). */
template<class E, size_t BufferBytes>
class LoggerModel
{
public:
    explicit LoggerModel(bool const reportLosses = true)
    : _buffer(::etl::span<uint8_t>(_storage)), _ref(0), _lf(_console), _reporter(_lf), _report(reportLosses)
    {}

    /** Log one entry of \p size bytes (at least 4) carrying the sequence number. */
    void log(size_t const size)
    {
        uint8_t entry[MAX_ENTRY] = {};
        uint32_t const seq       = _logged;
        std::memcpy(entry, &seq, sizeof(seq));
        _buffer.addEntry(::etl::span<uint8_t const>(entry, size));
        ++_logged;
    }

    /** One run of the reader (LoggerComposition::run()): at most one entry. */
    bool readOne()
    {
        uint8_t entry[MAX_ENTRY];
        uint8_t const size = _buffer.getNextEntry(::etl::span<uint8_t>(entry), _ref);
        if (size == 0U)
        {
            return false;
        }
        if (_report)
        {
            _reporter.onEntry(_ref.getIndex());
        }
        uint32_t seq = 0U;
        std::memcpy(&seq, entry, sizeof(seq));
        char line[32];
        int const length = std::snprintf(line, sizeof(line), "entry %u\n", static_cast<unsigned>(seq));
        _lf.write(::etl::span<uint8_t const>(reinterpret_cast<uint8_t const*>(line), static_cast<size_t>(length)));
        return true;
    }

    void drain()
    {
        while (readOne()) {}
    }

    std::string const& text() const { return _console.text; }

    uint32_t logged() const { return _logged; }

    uint32_t lostTotal() const { return _reporter.getTotal(); }

private:
    uint8_t _storage[BufferBytes] = {};
    ::logger::EntryBuffer<MAX_ENTRY, E> _buffer;
    typename ::logger::EntryBuffer<MAX_ENTRY, E>::EntryRef _ref;
    CaptureStream _console;
    ::util::stream::NormalizeLfOutputStream _lf;
    ::logger::EntryLossReporter<E> _reporter;
    bool _report;
    uint32_t _logged = 0U;
};

/** One printed line: an entry (seq) or a loss line (lost, total). */
struct Line
{
    bool isLoss;
    uint32_t seq;
    uint32_t lost;
    uint32_t total;
};

/** Split the console text into lines; every line must end in "\r\n" and be one of the two known forms. */
bool parse(std::string const& text, std::vector<Line>& lines)
{
    size_t pos = 0U;
    while (pos < text.size())
    {
        size_t const end = text.find("\r\n", pos);
        if (end == std::string::npos)
        {
            return false;
        }
        std::string const line = text.substr(pos, end - pos);
        unsigned a = 0U;
        unsigned b = 0U;
        char word[8] = {};
        if (std::sscanf(line.c_str(), "entry %u", &a) == 1)
        {
            lines.push_back({false, a, 0U, 0U});
        }
        else if (std::sscanf(line.c_str(), "... %u log %7s lost (total %u)", &a, word, &b) == 3)
        {
            bool const wordOk = (a == 1U) ? (std::strcmp(word, "entry") == 0) : (std::strcmp(word, "entries") == 0);
            if (!wordOk)
            {
                return false;
            }
            lines.push_back({true, 0U, a, b});
        }
        else
        {
            return false;
        }
        pos = end + 2U;
    }
    return true;
}

/**
 * The properties SYS-12 promises, for the text after a complete drain:
 *  - entries in logging order (strictly increasing sequence numbers);
 *  - before an entry whose sequence number is not the next one there is exactly one loss line with exactly the
 *    size of the gap, and a loss line appears nowhere else;
 *  - the running total in the lines adds up, and printed + lost = logged.
 * Returns the number of violated properties (0 = all kept).
 */
int verify(std::string const& text, uint32_t const logged, uint32_t const lostTotal, uint32_t* const lossLines)
{
    std::vector<Line> lines;
    if (!parse(text, lines))
    {
        return 1;
    }
    int bad          = 0;
    uint32_t next    = 0U; // sequence number expected next
    uint32_t pending = 0U; // size of the loss line just before, 0 = none
    uint32_t total   = 0U;
    uint32_t printed = 0U;
    uint32_t losses  = 0U;
    for (Line const& line : lines)
    {
        if (line.isLoss)
        {
            if ((pending != 0U) || (line.lost == 0U))
            {
                ++bad; // two loss lines in a row, or a line about nothing
            }
            total += line.lost;
            if (line.total != total)
            {
                ++bad;
            }
            pending = line.lost;
            ++losses;
        }
        else
        {
            if (line.seq != next + pending)
            {
                ++bad; // gap without (matching) loss line, or order broken
            }
            next    = line.seq + 1U;
            pending = 0U;
            ++printed;
        }
    }
    if ((pending != 0U) || (printed + total != logged) || (total != lostTotal) || (next != logged))
    {
        ++bad;
    }
    if (lossLines != nullptr)
    {
        *lossLines = losses;
    }
    return bad;
}

/* ---------------------------------------------------------------- 1. detector */

void testDetector()
{
    std::printf("1. EntryLossDetector\n");
    {
        ::logger::EntryLossDetector<uint32_t> d;
        // entry 0, 1, 2 are delivered as 1, 2, 3 (EntryRef index after reading)
        CHECK(d.onEntry(1U) == 0U);
        CHECK(d.onEntry(2U) == 0U);
        CHECK(d.onEntry(3U) == 0U);
        CHECK(d.getTotal() == 0U);
        CHECK(d.onEntry(7U) == 3U); // entries 3, 4, 5 dropped
        CHECK(d.getTotal() == 3U);
        CHECK(d.onEntry(8U) == 0U);
        CHECK(d.getTotal() == 3U);
    }
    {
        // the board case: 9 entries dropped before the reader ever ran
        ::logger::EntryLossDetector<uint32_t> d;
        CHECK(d.onEntry(10U) == 9U);
        CHECK(d.onEntry(11U) == 0U);
        CHECK(d.getTotal() == 9U);
    }
    {
        // wrap of an 8-bit index: 254, 255, 0 (= 256), 1 ... and a gap across the wrap
        ::logger::EntryLossDetector<uint8_t> d;
        uint8_t idx = 1U;
        for (int i = 0; i < 300; ++i)
        {
            CHECK(d.onEntry(idx) == 0U);
            idx = static_cast<uint8_t>(idx + 1U);
        }
        idx = static_cast<uint8_t>(idx + 20U); // 20 dropped, crossing 255 -> 0 depending on idx
        CHECK(d.onEntry(idx) == 20U);
        CHECK(d.getTotal() == 20U);
        uint8_t const beforeWrap = 250U;
        ::logger::EntryLossDetector<uint8_t> w;
        (void)w.onEntry(beforeWrap);                  // 249 lost at the start
        CHECK(w.onEntry(static_cast<uint8_t>(3U)) == 8U); // 251..255, 0, 1, 2 -> 8 lost across the wrap
        CHECK(w.getTotal() == 257U);
    }
    {
        // the total saturates instead of wrapping
        ::logger::EntryLossDetector<uint32_t> d;
        CHECK(d.onEntry(0xFFFFFFFFU) == 0xFFFFFFFEU);
        CHECK(d.getTotal() == 0xFFFFFFFEU);
        CHECK(d.onEntry(5U) == 5U); // expected 0 after the wrap, 0..4 dropped
        CHECK(d.getTotal() == 0xFFFFFFFFU);
        CHECK(d.onEntry(6U) == 0U);
        CHECK(d.getTotal() == 0xFFFFFFFFU);
    }
}

/* ---------------------------------------------------------------- 2. text */

void testFormat()
{
    std::printf("2. formatLossLine\n");
    char buf[::logger::LOSS_LINE_MAX];
    size_t n = ::logger::formatLossLine(buf, 9U, 9U);
    CHECK(std::string(buf, n) == "... 9 log entries lost (total 9)\n");
    n = ::logger::formatLossLine(buf, 1U, 12U);
    CHECK(std::string(buf, n) == "... 1 log entry lost (total 12)\n");
    n = ::logger::formatLossLine(buf, 0xFFFFFFFFU, 0xFFFFFFFFU);
    CHECK(std::string(buf, n) == "... 4294967295 log entries lost (total 4294967295)\n");
    CHECK(n <= ::logger::LOSS_LINE_MAX);

    // through the console stream: "\r\n" like every log line
    CaptureStream console;
    ::util::stream::NormalizeLfOutputStream lf(console);
    ::logger::EntryLossReporter<uint32_t> reporter(lf);
    reporter.onEntry(1U);
    CHECK(console.text.empty()); // normal case: nothing written
    reporter.onEntry(11U);
    CHECK(console.text == "... 9 log entries lost (total 9)\r\n");
    CHECK(reporter.getTotal() == 9U);
}

/* ---------------------------------------------------------------- 3. no loss */

void testNoLoss()
{
    std::printf("3. no loss, no line\n");
    LoggerModel<uint32_t, 256U> m;
    // reader keeps up
    for (int i = 0; i < 1000; ++i)
    {
        m.log(12U);
        CHECK(m.readOne());
    }
    // bursts below the capacity (256 bytes / 25 bytes per entry = 10 entries)
    for (int burst = 0; burst < 100; ++burst)
    {
        for (int i = 0; i < 10; ++i)
        {
            m.log(24U);
        }
        m.drain();
    }
    uint32_t lossLines = 99U;
    CHECK(verify(m.text(), m.logged(), m.lostTotal(), &lossLines) == 0);
    CHECK(lossLines == 0U);
    CHECK(m.lostTotal() == 0U);
    CHECK(m.text().find("...") == std::string::npos);
    CHECK(m.logged() == 2000U);
}

/* ---------------------------------------------------------------- 4. start-up burst */

void testStartupBurst()
{
    std::printf("4. start-up burst larger than the buffer\n");
    LoggerModel<uint32_t, 256U> m;
    for (int i = 0; i < 40; ++i)
    {
        m.log(24U); // 25 bytes each: 10 fit, 30 are dropped before the reader runs
    }
    m.drain();
    std::vector<Line> lines;
    CHECK(parse(m.text(), lines));
    CHECK(!lines.empty() && lines.front().isLoss);
    CHECK(!lines.empty() && (lines.front().lost == 30U) && (lines.front().total == 30U));
    CHECK(lines.size() == 11U);
    for (size_t i = 1U; i < lines.size(); ++i)
    {
        CHECK(!lines[i].isLoss && (lines[i].seq == 29U + i)); // 30..39, in order
    }
    CHECK(m.lostTotal() == 30U);
    CHECK(verify(m.text(), m.logged(), m.lostTotal(), nullptr) == 0);
    // the report did not push anything out: logging continues without a further loss
    m.log(24U);
    m.drain();
    CHECK(m.lostTotal() == 30U);
    CHECK(verify(m.text(), m.logged(), m.lostTotal(), nullptr) == 0);
}

/* ---------------------------------------------------------------- 5. loss in between */

void testLossInBetween()
{
    std::printf("5. loss in between, line at the place of the gap\n");
    LoggerModel<uint32_t, 256U> m;
    for (int i = 0; i < 5; ++i)
    {
        m.log(12U);
    }
    m.drain(); // entry 0..4 printed
    for (int i = 0; i < 50; ++i)
    {
        m.log(24U); // 5..54, the buffer keeps the last 10
    }
    m.drain();
    std::vector<Line> lines;
    CHECK(parse(m.text(), lines));
    CHECK(lines.size() == 16U);
    if (lines.size() == 16U)
    {
        CHECK(!lines[4].isLoss && (lines[4].seq == 4U));
        CHECK(lines[5].isLoss && (lines[5].lost == 40U) && (lines[5].total == 40U));
        CHECK(!lines[6].isLoss && (lines[6].seq == 45U));
        CHECK(!lines[15].isLoss && (lines[15].seq == 54U));
    }
    CHECK(verify(m.text(), m.logged(), m.lostTotal(), nullptr) == 0);

    // a second gap later: running total in the line
    for (int i = 0; i < 12; ++i)
    {
        m.log(24U); // 55..66, 2 dropped
    }
    m.drain();
    CHECK(m.lostTotal() == 42U);
    CHECK(m.text().find("... 2 log entries lost (total 42)\r\nentry 57\r\n") != std::string::npos);
    CHECK(verify(m.text(), m.logged(), m.lostTotal(), nullptr) == 0);
}

/* ---------------------------------------------------------------- 6. random interleaving */

class Lcg
{
public:
    uint32_t next()
    {
        _state = _state * 1664525U + 1013904223U;
        return _state >> 8U;
    }

private:
    uint32_t _state = 12345U;
};

template<class E>
void randomRun(char const* const name, int const steps, uint32_t const maxBurst)
{
    LoggerModel<E, 512U> m;
    Lcg rng;
    for (int step = 0; step < steps; ++step)
    {
        uint32_t const writes = rng.next() % (maxBurst + 1U);
        for (uint32_t i = 0U; i < writes; ++i)
        {
            m.log(4U + static_cast<size_t>(rng.next() % (MAX_ENTRY - 4U + 1U)));
        }
        uint32_t const reads = rng.next() % 6U;
        for (uint32_t i = 0U; i < reads; ++i)
        {
            (void)m.readOne();
        }
    }
    m.drain();
    uint32_t lossLines = 0U;
    int const bad      = verify(m.text(), m.logged(), m.lostTotal(), &lossLines);
    std::printf(
        "   %s: %u logged, %u lost in %u gaps\n",
        name,
        static_cast<unsigned>(m.logged()),
        static_cast<unsigned>(m.lostTotal()),
        static_cast<unsigned>(lossLines));
    CHECK(bad == 0);
    CHECK(lossLines > 10U);          // the run really overflowed
    CHECK(m.lostTotal() < m.logged()); // and really printed
}

void testRandom()
{
    std::printf("6. random interleaving of writes and reads\n");
    randomRun<uint32_t>("32-bit index", 20000, 12U);
    // 8-bit index: wraps every 256 entries; the reader must not fall more than 127 entries behind the oldest one
    // (EntryBuffer compares signed) - with 512 bytes at most 102 entries are stored, bursts of 12
    randomRun<uint8_t>("8-bit index", 20000, 12U);
}

/* ---------------------------------------------------------------- 7. inserted fault */

void testWithoutReporter()
{
    std::printf("7. deliberately inserted fault: without the reporter (before SYS-12)\n");
    LoggerModel<uint32_t, 256U> m(false);
    for (int i = 0; i < 40; ++i)
    {
        m.log(24U);
    }
    m.drain();
    // exactly the finding: entries 0..29 are silently missing, the text starts with "entry 30"
    CHECK(m.text().compare(0, 10, "entry 30\r\n") == 0);
    CHECK(verify(m.text(), m.logged(), m.lostTotal(), nullptr) != 0);
}

} // namespace

int main()
{
    testDetector();
    testFormat();
    testNoLoss();
    testStartupBurst();
    testLossInBetween();
    testRandom();
    testWithoutReporter();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
