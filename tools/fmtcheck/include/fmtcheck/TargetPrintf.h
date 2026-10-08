// SPDX-License-Identifier: Apache-2.0
#pragma once

/**
 * Host test helper (IO-60): runs a printf-style call through the real OpenBSW formatter (util::format::
 * PrintfFormatter, PrintfFormatScanner) with the arguments laid out as on the 32-bit target (AAPCS: Cortex-M and
 * Cortex-R, 32 bit, little endian, D11).
 *
 * Why: util::format reads "%l.." like "%ll.." as a 64-bit value (PrintfFormatScanner::scanParamLength returns the
 * same size for 'l' and 'll'). On the target an unsigned long is 32 bit, so the formatter takes two argument slots
 * (8-byte aligned) and every following argument shifts - the board printed "debounce 85899347920 us" and
 * "qlf <NULL>" (IO-54, 2026-09-29). A host test that formats with the C library, or with the real formatter and
 * the host's va_list, cannot see this: on the host the formatter and the argument agree (64-bit long or 64-bit
 * slots). TargetArgs therefore models the target instead of the host:
 *   - every argument occupies 32-bit slots, numbered from r0; a member printf has `this` in r0 and the format in
 *     r1, so its first variadic argument is slot 2 (a free function: slot 1);
 *   - integers up to 32 bit (after the default promotions) and `long`/`unsigned long` take one slot - long is
 *     32 bit on the target, whatever the host says; 64-bit integers take two slots at an even slot number
 *     (doubleword alignment in the registers r0..r3 and on the stack alike);
 *   - pointers take one slot (the host pointer is kept beside it);
 *   - a read past the last argument returns the stack fill 0xA5A5A5A5 (the board showed it in COM-39).
 * Beside the text it reports every call whose conversions do not match the arguments one by one.
 */

#include <util/format/IPrintfArgumentReader.h>
#include <util/format/PrintfFormatter.h>
#include <util/stream/IOutputStream.h>

#include <cstddef>
#include <cstdint>
#include <string>
#include <type_traits>
#include <vector>

namespace fmtcheck
{
enum class Kind : uint8_t
{
    WORD,   // integer up to 32 bit
    DWORD,  // 64-bit integer
    POINTER // any pointer
};

class TargetArgs : public ::util::format::IPrintfArgumentReader
{
public:
    static constexpr uint32_t STACK_FILL   = 0xA5A5A5A5U;
    /** First variadic slot of a member function printf(format, ...): r0 this, r1 format. */
    static constexpr size_t MEMBER_FIRST_SLOT   = 2U;
    /** First variadic slot of a free function printf(format, ...): r0 format. */
    static constexpr size_t FUNCTION_FIRST_SLOT = 1U;

    explicit TargetArgs(size_t const firstSlot) : _first(firstSlot), _next(firstSlot) {}

    template<class T>
    void push(T const value)
    {
        if constexpr (std::is_pointer<T>::value || std::is_null_pointer<T>::value)
        {
            addPointer(static_cast<void const*>(value));
        }
        else if constexpr (std::is_enum<T>::value)
        {
            push(static_cast<typename std::underlying_type<T>::type>(value));
        }
        else
        {
            static_assert(std::is_integral<T>::value, "util::format prints integers, strings and pointers only");
            // long is 32 bit on the target (ILP32), whatever its size on the host
            constexpr bool isLong = std::is_same<T, long>::value || std::is_same<T, unsigned long>::value;
            if constexpr ((sizeof(T) <= 4U) || isLong)
            {
                addWord(static_cast<uint32_t>(value));
            }
            else
            {
                addDword(static_cast<uint64_t>(value));
            }
        }
    }

    ::util::format::ParamVariant const* readArgument(::util::format::ParamDatatype const datatype) override
    {
        using ::util::format::ParamDatatype;
        switch (datatype)
        {
            case ParamDatatype::UINT8:
            case ParamDatatype::SINT8:
                _variant._uint8Value = static_cast<uint8_t>(readWord(Kind::WORD));
                break;
            case ParamDatatype::UINT16:
            case ParamDatatype::SINT16:
                _variant._uint16Value = static_cast<uint16_t>(readWord(Kind::WORD));
                break;
            case ParamDatatype::UINT32:
            case ParamDatatype::SINT32:
                _variant._uint32Value = readWord(Kind::WORD);
                break;
            case ParamDatatype::UINT64:
            case ParamDatatype::SINT64:
            {
                note(Kind::DWORD);
                _cursor += (_cursor % 2U); // doubleword alignment
                uint64_t const low  = slot(_cursor);
                uint64_t const high = slot(_cursor + 1U);
                _cursor += 2U;
                _variant._uint64Value = low | (high << 32U);
                break;
            }
            default:
            {
                note(Kind::POINTER);
                size_t const at = _cursor;
                ++_cursor;
                if (isPointerSlot(at))
                {
                    _variant._voidPtrValue = _pointers[at - _first];
                }
                else
                {
                    // a number read as pointer: null for 0 (prints "<NULL>" like the board), else a marker - the
                    // target would read whatever memory the number addresses
                    _variant._voidPtrValue = (slot(at) == 0U) ? nullptr : static_cast<void const*>(NOT_A_POINTER);
                }
                break;
            }
        }
        return &_variant;
    }

    /** Empty if the conversions read exactly the pushed arguments in order and kind, else what differs. */
    std::string problem() const
    {
        if (_read.size() != _kinds.size())
        {
            return std::to_string(_read.size()) + " conversion(s) for " + std::to_string(_kinds.size())
                   + " argument(s)";
        }
        for (size_t i = 0U; i < _read.size(); ++i)
        {
            if (_read[i] != _kinds[i])
            {
                return "conversion " + std::to_string(i + 1U) + " reads " + name(_read[i]) + ", argument "
                       + std::to_string(i + 1U) + " is " + name(_kinds[i]);
            }
        }
        return std::string();
    }

    static constexpr char const* NOT_A_POINTER = "<not a pointer>";

private:
    static char const* name(Kind const kind)
    {
        switch (kind)
        {
            case Kind::WORD:  return "32 bit";
            case Kind::DWORD: return "64 bit";
            default:          return "a pointer";
        }
    }

    void addWord(uint32_t const value)
    {
        _kinds.push_back(Kind::WORD);
        set(_next, value, nullptr, false);
        ++_next;
    }

    void addDword(uint64_t const value)
    {
        _kinds.push_back(Kind::DWORD);
        _next += (_next % 2U);
        set(_next, static_cast<uint32_t>(value), nullptr, false);
        set(_next + 1U, static_cast<uint32_t>(value >> 32U), nullptr, false);
        _next += 2U;
    }

    void addPointer(void const* const value)
    {
        _kinds.push_back(Kind::POINTER);
        // the target value of a pointer does not matter; 0 marks the null pointer, 1 any other
        set(_next, (value == nullptr) ? 0U : 1U, value, true);
        ++_next;
    }

    void set(size_t const at, uint32_t const value, void const* const pointer, bool const isPointer)
    {
        size_t const index = at - _first;
        if (_words.size() <= index)
        {
            // an alignment gap keeps the stack fill, like the unused register or stack word on the target
            _words.resize(index + 1U, STACK_FILL);
            _pointers.resize(index + 1U, nullptr);
            _isPointer.resize(index + 1U, false);
        }
        _words[index]     = value;
        _pointers[index]  = pointer;
        _isPointer[index] = isPointer;
    }

    uint32_t slot(size_t const at) const
    {
        size_t const index = at - _first;
        return (index < _words.size()) ? _words[index] : STACK_FILL;
    }

    bool isPointerSlot(size_t const at) const
    {
        size_t const index = at - _first;
        return (index < _isPointer.size()) && _isPointer[index];
    }

    uint32_t readWord(Kind const kind)
    {
        note(kind);
        uint32_t const value = slot(_cursor);
        ++_cursor;
        return value;
    }

    void note(Kind const kind) { _read.push_back(kind); }

    size_t _first;
    size_t _next;
    size_t _cursor = _first;
    std::vector<uint32_t> _words;
    std::vector<void const*> _pointers;
    std::vector<bool> _isPointer;
    std::vector<Kind> _kinds;
    std::vector<Kind> _read;
    ::util::format::ParamVariant _variant{};
};

class StringOutputStream : public ::util::stream::IOutputStream
{
public:
    explicit StringOutputStream(std::string& text) : _text(text) {}

    bool isEof() const override { return false; }

    void write(uint8_t const data) override { _text.push_back(static_cast<char>(data)); }

    void write(::etl::span<uint8_t const> const& buffer) override
    {
        for (uint8_t const c : buffer)
        {
            _text.push_back(static_cast<char>(c));
        }
    }

private:
    std::string& _text;
};

/**
 * Stand-in for util::format::StringWriter / SharedStringWriter in templates such as the console text of a
 * module: printf(format, ...) as a member function (first variadic slot 2), formatted by the real PrintfFormatter
 * with the target's argument layout. text collects the output, problems one line per mismatching call.
 */
class TargetPrintf
{
public:
    std::string text;
    std::vector<std::string> problems;

    template<class... Args>
    TargetPrintf& printf(char const* const format, Args const... args)
    {
        TargetArgs targetArgs(TargetArgs::MEMBER_FIRST_SLOT);
        (targetArgs.push(args), ...);
        StringOutputStream stream(text);
        ::util::format::PrintfFormatter formatter(stream);
        formatter.format(format, targetArgs);
        std::string const problem = targetArgs.problem();
        if (!problem.empty())
        {
            problems.push_back(std::string("\"") + format + "\": " + problem);
        }
        return *this;
    }
};

} // namespace fmtcheck
