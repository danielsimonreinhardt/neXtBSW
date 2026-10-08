#pragma once

#include <cstddef>
#include <cstdint>

namespace can
{

/**
 * Text of "can log" for one received frame, split into lines that each fit one logger entry (COM-25).
 *
 * Finding COM-24 (board, 2026-09-18): the receive log was one Logger::info() with the format string and
 * twelve arguments - bus name, id, extended flag, length and eight bytes as separate "%02x". OpenBSW's
 * buffered logger (logger::EntrySerializer) copies the format string and every argument into one entry of at
 * most MaxEntrySize bytes (128 in logger/Config.h) and cuts what does not fit; the last argument of a frame
 * with 8 bytes on "CAN0" ended one byte past the entry and printed as "<?>". CAN FD frames (up to 64 bytes)
 * could not be shown at all.
 *
 * Now the board port formats the text itself (this header, no OpenBSW dependency) and logs each line with the
 * format "%s": the entry holds the short format and one string of at most LINE_CAPACITY - 1 characters, so
 * its size has a fixed upper bound (entryBytes()) independent of the frame, checked against the real logger
 * type at compile time in the board port. A frame with up to BYTES_PER_LINE bytes is one line with exactly its
 * payload length of bytes (no zero padding up to 8 any more):
 *
 *   CAN0 rx 0x210 [8] 00 11 22 33 44 55 66 77
 *
 * longer frames continue with the byte offset in place of the length, so every line stands alone even if
 * other log lines come in between:
 *
 *   CAN1 rx 0x18fedf00x [64] 00 01 ... 0f
 *   CAN1 rx 0x18fedf00x +16 10 11 ... 1f
 *   CAN1 rx 0x18fedf00x +32 ...
 *   CAN1 rx 0x18fedf00x +48 ...
 *
 * Host test: libs/bsw/canSystem/test/host/CanFrameLogHostTest.cpp (real OpenBSW EntrySerializer).
 */
struct CanFrameLog
{
    /** Payload bytes per line: two lines for the largest CAN FD frame would need about 110 characters. */
    static constexpr size_t BYTES_PER_LINE = 16U;
    /** Longer bus names are cut; the names come from the board port (CAN0, CAN2, ...). */
    static constexpr size_t BUS_NAME_CHARS = 16U;
    /** Line buffer including the terminating zero. */
    static constexpr size_t LINE_CAPACITY = 96U;
    /** Format string of every log call; the line is its only argument. */
    static constexpr char const* FORMAT = "%s";

    /**
     * Longest line in characters (without the zero): name, " rx 0x", 8 hex digits, "x", " [64]" or " +48",
     * BYTES_PER_LINE times " hh".
     */
    static constexpr size_t LINE_CHARS = BUS_NAME_CHARS + 6U + 8U + 1U + 5U + (3U * BYTES_PER_LINE);
    static_assert(LINE_CHARS < LINE_CAPACITY, "a line must fit the line buffer");

    /**
     * Upper bound of the logger entry of one line, following logger::EntrySerializer::serialize(): timestamp,
     * component index and level, the format string (copied as type byte, length of type T, characters and zero,
     * or stored as a pointer if the serializer's read-only predicate accepts it) and the line (copied the same
     * way, it lives on the stack). lengthBytes = sizeof(T), timestampBytes = sizeof(Timestamp) of the logger.
     */
    static constexpr size_t entryBytes(size_t const lengthBytes, size_t const timestampBytes)
    {
        return timestampBytes + 1U + 1U + larger(1U + lengthBytes + sizeof("%s"), 1U + sizeof(char const*)) + 1U
               + lengthBytes + LINE_CAPACITY;
    }

    /** Number of lines of a frame with payloadLength bytes (at least one, also for no payload). */
    static constexpr size_t lineCount(size_t const payloadLength)
    {
        return (payloadLength == 0U) ? 1U : ((payloadLength + BYTES_PER_LINE - 1U) / BYTES_PER_LINE);
    }

    /**
     * Writes line `line` (0 .. lineCount() - 1) of a frame into `out` (zero terminated) and returns its length.
     * A line index past the end gives an empty line.
     */
    static size_t formatLine(
        char (&out)[LINE_CAPACITY],
        char const* const name,
        uint32_t const rawId,
        bool const extended,
        uint8_t const* const payload,
        size_t const payloadLength,
        size_t const line)
    {
        Writer w(out);
        if (line < lineCount(payloadLength))
        {
            w.name(name);
            w.text(" rx 0x");
            w.hex(rawId);
            if (extended)
            {
                w.put('x');
            }
            size_t const first = line * BYTES_PER_LINE;
            if (line == 0U)
            {
                w.text(" [");
                w.decimal(payloadLength);
                w.put(']');
            }
            else
            {
                w.text(" +");
                w.decimal(first);
            }
            for (size_t i = first; (i < payloadLength) && (i < (first + BYTES_PER_LINE)); ++i)
            {
                w.put(' ');
                w.byte(payload[i]);
            }
        }
        return w.finish();
    }

private:
    static constexpr size_t larger(size_t const a, size_t const b) { return (a > b) ? a : b; }

    class Writer
    {
    public:
        explicit Writer(char (&out)[LINE_CAPACITY]) : _out(out), _length(0U) {}

        void put(char const c)
        {
            if (_length < (LINE_CAPACITY - 1U))
            {
                _out[_length] = c;
                ++_length;
            }
        }

        void text(char const* s)
        {
            while (*s != '\0')
            {
                put(*s);
                ++s;
            }
        }

        void name(char const* s)
        {
            for (size_t i = 0U; (s != nullptr) && (s[i] != '\0') && (i < BUS_NAME_CHARS); ++i)
            {
                put(s[i]);
            }
        }

        void hex(uint32_t const value)
        {
            char digits[8];
            size_t n   = 0U;
            uint32_t v = value;
            do
            {
                digits[n] = nibble(v & 0xFU);
                ++n;
                v >>= 4U;
            } while (v != 0U);
            while (n > 0U)
            {
                --n;
                put(digits[n]);
            }
        }

        void byte(uint8_t const value)
        {
            put(nibble(static_cast<uint32_t>(value) >> 4U));
            put(nibble(static_cast<uint32_t>(value) & 0xFU));
        }

        void decimal(size_t const value)
        {
            char digits[20];
            size_t n = 0U;
            size_t v = value;
            do
            {
                digits[n] = static_cast<char>('0' + static_cast<char>(v % 10U));
                ++n;
                v /= 10U;
            } while (v != 0U);
            while (n > 0U)
            {
                --n;
                put(digits[n]);
            }
        }

        size_t finish()
        {
            _out[_length] = '\0';
            return _length;
        }

    private:
        static char nibble(uint32_t const v)
        {
            return (v < 10U) ? static_cast<char>('0' + static_cast<char>(v))
                             : static_cast<char>('a' + static_cast<char>(v - 10U));
        }

        char (&_out)[LINE_CAPACITY];
        size_t _length;
    };
};

} // namespace can
