/* Host test of the "can log" lines (COM-25): every line fits one entry of the buffered logger.
 *
 * Finding COM-24 (board, 2026-09-18 evening): "can log 1" with 0x210 [8] printed
 * "CAN0 rx 0x210 [8] 00 00 00 00 00 00 00 <?>" - the 8th data byte was missing. The receive log was one
 * Logger::info() with twelve arguments; OpenBSW's EntrySerializer copies the format string and every argument into
 * one entry of at most MaxEntrySize bytes and cuts the rest, the formatter prints a missing argument as "<?>". Same
 * pattern as the "TMC6200 ready" line of IO-25. Since COM-25 the board port formats the text with
 * ::can::CanFrameLog (libs/bsw/canSystem/include/can/CanFrameLog.h) and logs it line by line with the format "%s".
 *
 * The test runs the REAL OpenBSW path of an entry: logger::EntrySerializer::serialize() into a buffer of
 * MaxEntrySize bytes (as BufferedLoggerOutput::logOutput()), logger::EntryBuffer::addEntry() (caps the entry at
 * MaxEntrySize) and getNextEntry(), EntrySerializer::deserialize() and util::format::PrintfFormatter (as the
 * console output of the entry). Only the lock, the component mapping and the timestamp source are left out.
 *
 * Maximum payload of a logger entry (found in the code): the generated logger/Config.h (root CMakeLists.txt,
 * PLAT-04) declares BufferedLoggerOutput<..., MaxEntrySize = 128, T = uint16_t, E = uint32_t, Timestamp =
 * uint32_t>; the default SectionPredicate (no section) copies every string into the entry. An entry is
 * timestamp (4) + component (1) + level (1) + the format string as char array (type 1 + length 2 + characters + 0)
 * + every argument (type byte + value, strings like the format). MAX_ENTRY_BYTES below repeats 128; the board port
 * checks CanFrameLog::entryBytes() against the MaxEntrySize, T and Timestamp of the real
 * logger::BufferedLoggerOutputType at compile time (static_assert in platforms/s32k1xx/port/main/src/systems/
 * CanSystem.cpp), so the host value cannot hide a smaller target entry.
 *
 * Parts:
 *  1. Entry limit: a "%s" entry carries at most 112 characters of text (16 bytes overhead + 112 = 128); 112 print
 *     complete, 113 are cut and end with "<?>" - the payload limit, measured through the real serializer.
 *  2. Deliberately inserted fault / finding: the former single call (twelve arguments) needs 129 bytes for 8 data
 *     bytes and prints exactly the board line "... 00 00 00 00 00 00 00 <?>".
 *  3. Classic CAN, 8 bytes: one line with all 8 bytes, the entry fits; 2 bytes print 2 bytes (no zero padding).
 *  4. CAN FD, 64 bytes, extended id, bus name of BUS_NAME_CHARS characters: four lines with offsets, each complete.
 *  5. Every length 0..64 (standard and extended id, several names): every line fits, no "<?>", the bytes read back
 *     from the printed lines equal the payload, line count = lineCount().
 *  6. entryBytes() is an upper bound of every measured entry and fits the 128 bytes; the longest possible line
 *     (LINE_CAPACITY - 1 characters) prints complete.
 * No target hardware needed.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/canSystem/include -isystem external/openbsw/libs/bsw/logger/include \
 *       -isystem external/openbsw/libs/bsw/util/include -isystem external/openbsw/libs/3rdparty/etl/include \
 *       libs/bsw/canSystem/test/host/CanFrameLogHostTest.cpp \
 *       external/openbsw/libs/bsw/logger/src/logger/EntrySerializer.cpp \
 *       external/openbsw/libs/bsw/util/src/util/format/PrintfFormatter.cpp \
 *       external/openbsw/libs/bsw/util/src/util/format/PrintfFormatScanner.cpp \
 *       external/openbsw/libs/bsw/util/src/util/format/PrintfArgumentReader.cpp -o build/host/can_frame_log_test.exe
 *   build/host/can_frame_log_test.exe
 */

#include "can/CanFrameLog.h"

#include <logger/EntryBuffer.h>
#include <logger/EntrySerializer.h>
#include <util/format/PrintfFormatter.h>
#include <util/stream/IOutputStream.h>

#include <cstdarg>
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

using ::can::CanFrameLog;

/** MaxEntrySize of logger::BufferedLoggerOutputType (generated logger/Config.h, see header comment). */
constexpr uint8_t MAX_ENTRY_BYTES = 128U;
using LengthType                  = uint16_t; // T
using TimestampType               = uint32_t; // Timestamp
using Serializer                  = ::logger::EntrySerializer<LengthType, TimestampType>;

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

class Printer : public ::logger::IEntrySerializerCallback<TimestampType>
{
public:
    void onEntry(
        TimestampType /* timestamp */,
        uint8_t /* componentIndex */,
        ::util::logger::Level /* level */,
        char const* const str,
        ::util::format::IPrintfArgumentReader& argReader) override
    {
        ::util::format::PrintfFormatter formatter(stream, false);
        formatter.format(str, argReader);
    }

    CaptureStream stream;
};

/** Result of one log call through the real entry path. */
struct Logged
{
    size_t serializedBytes; // what EntrySerializer::serialize() returned
    size_t storedBytes;     // what the entry buffer kept
    std::string text;       // what the console prints (message part)
};

Logged vlogThrough(char const* const format, va_list ap)
{
    Serializer const serializer;
    uint8_t entry[MAX_ENTRY_BYTES];
    LengthType const size = serializer.serialize(
        ::etl::span<uint8_t>(entry), 0x12345678U, 3U, ::util::logger::LEVEL_INFO, format, ap);

    // BufferedLoggerOutput passes first(size) of its MaxEntrySize array and EntryBuffer::addEntry() keeps at most
    // MaxEntrySize bytes of it; the test caps before building the span, with the same result.
    size_t const offered = (size > MAX_ENTRY_BYTES) ? MAX_ENTRY_BYTES : size;
    uint8_t storage[1024];
    ::logger::EntryBuffer<MAX_ENTRY_BYTES, uint32_t> buffer{::etl::span<uint8_t>(storage)};
    buffer.addEntry(::etl::span<uint8_t const>(entry, offered));
    ::logger::EntryBuffer<MAX_ENTRY_BYTES, uint32_t>::EntryRef ref(0U);
    uint8_t read[MAX_ENTRY_BYTES];
    uint8_t const stored = buffer.getNextEntry(::etl::span<uint8_t>(read), ref);

    Printer printer;
    Serializer::deserialize(::etl::span<uint8_t const>(read, stored), printer);
    return Logged{size, stored, printer.stream.text};
}

Logged logThrough(char const* const format, ...)
{
    va_list ap;
    va_start(ap, format);
    Logged const result = vlogThrough(format, ap);
    va_end(ap);
    return result;
}

/** The board port's receive log (CanSystem::Channel::frameReceived since COM-25). */
std::vector<Logged> logFrame(
    char const* const name, uint32_t const id, bool const extended, std::vector<uint8_t> const& payload)
{
    std::vector<Logged> lines;
    for (size_t line = 0U; line < CanFrameLog::lineCount(payload.size()); ++line)
    {
        char text[CanFrameLog::LINE_CAPACITY];
        (void)CanFrameLog::formatLine(text, name, id, extended, payload.data(), payload.size(), line);
        lines.push_back(logThrough(CanFrameLog::FORMAT, text));
    }
    return lines;
}

/** The receive log before COM-25: one call, eight separate byte arguments, zero padded. */
Logged logFrameOld(char const* const name, uint32_t const id, bool const extended, std::vector<uint8_t> const& p)
{
    uint8_t d[8] = {0U};
    for (size_t i = 0U; (i < p.size()) && (i < sizeof(d)); ++i)
    {
        d[i] = p[i];
    }
    return logThrough(
        "%s rx 0x%x%s [%u] %02x %02x %02x %02x %02x %02x %02x %02x",
        name,
        static_cast<unsigned>(id),
        extended ? "x" : "",
        static_cast<unsigned>(p.size()),
        d[0],
        d[1],
        d[2],
        d[3],
        d[4],
        d[5],
        d[6],
        d[7]);
}

std::vector<uint8_t> counting(size_t const n, uint8_t const start = 0U)
{
    std::vector<uint8_t> p(n);
    for (size_t i = 0U; i < n; ++i)
    {
        p[i] = static_cast<uint8_t>(start + i);
    }
    return p;
}

bool contains(std::string const& s, char const* const part) { return s.find(part) != std::string::npos; }

/** Reads the hex bytes after the "[n]" or "+offset" field of a printed line. */
std::vector<uint8_t> bytesOf(std::string const& line)
{
    std::vector<uint8_t> bytes;
    size_t pos = line.find(" rx 0x");
    pos        = line.find(' ', pos + 6U); // after the id
    pos        = line.find(' ', pos + 1U); // after "[n]" / "+offset"
    while ((pos != std::string::npos) && ((pos + 3U) <= line.size()))
    {
        unsigned value = 0U;
        if (std::sscanf(line.c_str() + pos + 1U, "%2x", &value) != 1)
        {
            break;
        }
        bytes.push_back(static_cast<uint8_t>(value));
        pos += 3U;
    }
    return bytes;
}

void partEntryLimit()
{
    std::printf("part 1: payload limit of one logger entry\n");
    CHECK(CanFrameLog::entryBytes(sizeof(LengthType), sizeof(TimestampType)) <= MAX_ENTRY_BYTES);

    std::string const fits(112U, 'a');
    Logged const a = logThrough("%s", fits.c_str());
    CHECK(a.serializedBytes == 128U);
    CHECK(a.storedBytes == 128U);
    CHECK(a.text == fits);

    std::string const tooLong(113U, 'b');
    Logged const b = logThrough("%s", tooLong.c_str());
    CHECK(b.storedBytes == 128U);
    CHECK(b.text.size() >= 3U);
    CHECK(b.text.compare(b.text.size() - 3U, 3U, "<?>") == 0);
    CHECK(b.text != tooLong);
    std::printf("  \"%%s\" entry: 112 characters -> %zu bytes, complete; 113 -> cut: \"...%s\"\n",
                a.serializedBytes, b.text.substr(b.text.size() - 8U).c_str());
}

void partOldFinding()
{
    std::printf("part 2: former single call (deliberately inserted fault, board finding COM-24)\n");
    Logged const old = logFrameOld("CAN0", 0x210U, false, std::vector<uint8_t>(8U, 0U));
    std::printf("  old: %zu bytes, \"%s\"\n", old.serializedBytes, old.text.c_str());
    CHECK(old.serializedBytes == 129U);
    CHECK(old.text == "CAN0 rx 0x210 [8] 00 00 00 00 00 00 00 <?>");
    // the check the new code passes must fail for the old one
    CHECK(!(old.serializedBytes <= MAX_ENTRY_BYTES && !contains(old.text, "<?>")));
    // extended ids lose even more
    Logged const oldX = logFrameOld("CAN0", 0x18fedf00U, true, counting(8U));
    CHECK(contains(oldX.text, "<?>"));
}

void partClassic()
{
    std::printf("part 3: classic CAN\n");
    std::vector<Logged> const zero = logFrame("CAN0", 0x210U, false, std::vector<uint8_t>(8U, 0U));
    CHECK(zero.size() == 1U);
    CHECK(zero[0].text == "CAN0 rx 0x210 [8] 00 00 00 00 00 00 00 00");
    CHECK(zero[0].serializedBytes <= MAX_ENTRY_BYTES);
    std::printf("  8 bytes: %zu bytes, \"%s\"\n", zero[0].serializedBytes, zero[0].text.c_str());

    std::vector<Logged> const full = logFrame("CAN0", 0x210U, false, {0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef});
    CHECK(full.size() == 1U);
    CHECK(full[0].text == "CAN0 rx 0x210 [8] 01 23 45 67 89 ab cd ef");

    std::vector<Logged> const two = logFrame("CAN2", 0x7e0U, false, {0x3e, 0x00});
    CHECK(two.size() == 1U);
    CHECK(two[0].text == "CAN2 rx 0x7e0 [2] 3e 00");

    std::vector<Logged> const none = logFrame("CAN0", 0x0U, false, {});
    CHECK(none.size() == 1U);
    CHECK(none[0].text == "CAN0 rx 0x0 [0]");

    std::vector<Logged> const ext = logFrame("CAN0", 0x18fedf00U, true, counting(8U, 0xf8U));
    CHECK(ext.size() == 1U);
    CHECK(ext[0].text == "CAN0 rx 0x18fedf00x [8] f8 f9 fa fb fc fd fe ff");
}

void partFd()
{
    std::printf("part 4: CAN FD, 64 bytes\n");
    char const* const longName = "BUS_NAME_16_CHAR";
    CHECK(std::strlen(longName) == CanFrameLog::BUS_NAME_CHARS);
    std::vector<Logged> const fd = logFrame(longName, 0x1fffffffU, true, counting(64U));
    CHECK(fd.size() == 4U);
    char const* const expected[] = {
        "BUS_NAME_16_CHAR rx 0x1fffffffx [64] 00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f",
        "BUS_NAME_16_CHAR rx 0x1fffffffx +16 10 11 12 13 14 15 16 17 18 19 1a 1b 1c 1d 1e 1f",
        "BUS_NAME_16_CHAR rx 0x1fffffffx +32 20 21 22 23 24 25 26 27 28 29 2a 2b 2c 2d 2e 2f",
        "BUS_NAME_16_CHAR rx 0x1fffffffx +48 30 31 32 33 34 35 36 37 38 39 3a 3b 3c 3d 3e 3f",
    };
    for (size_t i = 0U; (i < fd.size()) && (i < 4U); ++i)
    {
        CHECK(fd[i].text == expected[i]);
        CHECK(fd[i].serializedBytes <= MAX_ENTRY_BYTES);
        std::printf("  %zu bytes: \"%s\"\n", fd[i].serializedBytes, fd[i].text.c_str());
    }

    // a name longer than BUS_NAME_CHARS is cut, the data stays complete
    std::vector<Logged> const cut = logFrame("A_VERY_LONG_BUS_NAME_OF_40_CHARACTERS_XX", 0x123U, false, counting(64U));
    CHECK(cut.size() == 4U);
    CHECK(cut[0].text.compare(0U, 21U, "A_VERY_LONG_BUS_ rx 0") == 0);
    CHECK(bytesOf(cut[3].text) == counting(16U, 48U));

    // 12 bytes (CAN FD length code 9) stay one line; 20 bytes need two
    CHECK(logFrame("CAN1", 0x100U, false, counting(12U)).size() == 1U);
    std::vector<Logged> const twenty = logFrame("CAN1", 0x100U, false, counting(20U));
    CHECK(twenty.size() == 2U);
    CHECK(twenty[1].text == "CAN1 rx 0x100 +16 10 11 12 13");
}

void partAllLengths()
{
    std::printf("part 5: every length 0..64\n");
    char const* const names[] = {"CAN0", "CAN2", "", "BUS_NAME_16_CHAR"};
    size_t largest            = 0U;
    for (char const* const name : names)
    {
        for (int extended = 0; extended < 2; ++extended)
        {
            for (size_t n = 0U; n <= 64U; ++n)
            {
                std::vector<uint8_t> const payload = counting(n, static_cast<uint8_t>(0xa0U + n));
                uint32_t const id = (extended != 0) ? 0x1fffffffU : 0x7ffU;
                std::vector<Logged> const lines = logFrame(name, id, extended != 0, payload);
                CHECK(lines.size() == CanFrameLog::lineCount(n));
                std::vector<uint8_t> back;
                bool complete = true;
                for (Logged const& l : lines)
                {
                    complete = complete && (l.serializedBytes <= MAX_ENTRY_BYTES) && !contains(l.text, "<?>")
                               && (l.text.size() <= CanFrameLog::LINE_CHARS);
                    largest                         = (l.serializedBytes > largest) ? l.serializedBytes : largest;
                    std::vector<uint8_t> const part = bytesOf(l.text);
                    back.insert(back.end(), part.begin(), part.end());
                }
                CHECK(complete);
                CHECK(back == payload);
            }
        }
    }
    std::printf("  largest entry: %zu of %u bytes\n", largest, static_cast<unsigned>(MAX_ENTRY_BYTES));
    CHECK(largest <= CanFrameLog::entryBytes(sizeof(LengthType), sizeof(TimestampType)));
}

void partBound()
{
    std::printf("part 6: bound of entryBytes()\n");
    size_t const bound = CanFrameLog::entryBytes(sizeof(LengthType), sizeof(TimestampType));
    std::string const longest(CanFrameLog::LINE_CAPACITY - 1U, 'z');
    Logged const l = logThrough(CanFrameLog::FORMAT, longest.c_str());
    std::printf("  longest line buffer: %zu bytes (bound %zu, entry %u)\n", l.serializedBytes, bound,
                static_cast<unsigned>(MAX_ENTRY_BYTES));
    CHECK(l.serializedBytes <= bound);
    CHECK(l.text == longest);
    CHECK(CanFrameLog::LINE_CHARS < CanFrameLog::LINE_CAPACITY);

    // formatLine never writes past the buffer and terminates it, also for a line index past the end
    char text[CanFrameLog::LINE_CAPACITY];
    std::memset(text, 'q', sizeof(text));
    std::vector<uint8_t> const p = counting(64U);
    CHECK(CanFrameLog::formatLine(text, "CAN0", 1U, false, p.data(), p.size(), 4U) == 0U);
    CHECK(text[0] == '\0');
}
} // namespace

int main()
{
    partEntryLimit();
    partOldFinding();
    partClassic();
    partFd();
    partAllLengths();
    partBound();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
