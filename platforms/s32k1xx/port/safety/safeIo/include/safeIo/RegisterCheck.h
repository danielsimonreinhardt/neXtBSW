/* SYS-24 (finding F4 of doc/safety/fmea.md, section 5.2): lock and check of a register list, family neutral.
 *
 * The list is generated from the configuration (tools/bswgen: bsp/safeIo/safeIoConfiguration.h, X-macro
 * NX_SAFE_IO_REGISTERS). One entry is one register: its address, the bits that are judged (mask), the value those
 * bits must have (expected), for a register the hardware can lock the bits that lock it and (SYS-153) the access
 * width - 32 bits for a register word, 8 bits for a byte register such as PMC_LVDSC1. Nothing
 * here knows a register of a family: which words are listed, what they must contain and how they are locked is the
 * generator's knowledge of the family (for s32k1xx: PORT_PCR[LK], GPIO PDDR/PIDR, PCC CGC, FTM configuration).
 *
 * Classes (bits 31..24 of the event context, so that a report says which kind of register drifted):
 *   PIN_FUNCTION    pin control register of a safe output, a PWM pin or the locked role pin - locked at start
 *   GPIO_DIRECTION  direction and input enable of the GPIO pins of that list
 *   CLOCK_GATE      clock gate of every peripheral of that list (a gated clock makes the safe outputs walk skip it)
 *   PWM_TIMER       static configuration of the timer of a PWM group (polarity, dead time, combine, ...)
 *   SYSTEM_CLOCK    reserved: system clock generator (follow-up of SYS-24)
 *   SUPERVISION     supervision peripherals: since SYS-153 the low-voltage detect reset (s32k1xx: PMC_LVDSC1, 8 bits);
 *                   watchdog control and LPIT deadman are checked by SYS-41
 *   SAFE_OUTPUT     SYS-30: read-back of an entry of the safe outputs table while S1 is latched - the index is the
 *                   entry of the table (SafeIoRegisters.h safeOutputReadbacks), not of this list
 */
#pragma once

#include <safeIo/RegisterAccess.h>

#include <cstddef>
#include <cstdint>

namespace safety
{
namespace registers
{
enum class RegisterClass : uint8_t
{
    NONE           = 0U,
    PIN_FUNCTION   = 1U,
    GPIO_DIRECTION = 2U,
    CLOCK_GATE     = 3U,
    PWM_TIMER      = 4U,
    SYSTEM_CLOCK   = 5U,
    SUPERVISION    = 6U,
    SAFE_OUTPUT    = 7U,
};

struct Entry
{
    uintptr_t address;
    uint32_t mask;      // bits of the word that are judged
    uint32_t expected;  // value of those bits (with the lock bits once locked)
    uint32_t lockBits;  // written as 1 at start to lock the word; 0: the word is only checked
    uint32_t lockClear; // bits written as 0 by the lock write (e.g. a write-1-to-clear flag of the word)
    RegisterClass registerClass;
    uint8_t width; // SYS-153: access width in bits, WIDTH_32 or WIDTH_8
};

constexpr uint8_t WIDTH_32 = 32U;
constexpr uint8_t WIDTH_8  = 8U;

/** SYS-153: the register of an entry, read with the width of the entry (a byte register zero-extended). */
inline uint32_t readEntry(Entry const& entry)
{
    if (entry.width == WIDTH_8)
    {
        return read8(entry.address);
    }
    return read(entry.address);
}

/** SYS-153: writes the register of an entry with the width of the entry (a byte register gets the low byte). */
inline void writeEntry(Entry const& entry, uint32_t const value)
{
    if (entry.width == WIDTH_8)
    {
        write8(entry.address, static_cast<uint8_t>(value));
        return;
    }
    write(entry.address, value);
}

constexpr uint32_t CONTEXT_CLASS_SHIFT = 24U;
constexpr uint32_t CONTEXT_INDEX_MASK  = 0xFFFFU;

/** Event context of a deviating entry: class in bits 31..24, index of the entry in the list in bits 15..0. */
constexpr uint32_t context(RegisterClass const registerClass, size_t const index)
{
    return (static_cast<uint32_t>(registerClass) << CONTEXT_CLASS_SHIFT)
           | (static_cast<uint32_t>(index) & CONTEXT_INDEX_MASK);
}

constexpr bool deviates(Entry const& entry, uint32_t const value)
{
    return (value & entry.mask) != entry.expected;
}

/** Sets the lock bits of every lockable entry with a read-modify-write. The value found is locked as it is: a word
 * that is wrong before the lock stays wrong and is found by the read-back (readBack()), it is never repaired here. */
inline void lock(Entry const* const entries, size_t const count)
{
    for (size_t i = 0U; i < count; ++i)
    {
        Entry const& e = entries[i];
        if (e.lockBits != 0U)
        {
            writeEntry(e, (readEntry(e) & ~e.lockClear) | e.lockBits);
        }
    }
}

/**
 * Index of the first entry at or after `from` whose judged bits deviate, `count` if none does.
 * lockedOnly: only the lockable entries (the read-back after the lock).
 */
inline size_t firstDeviation(Entry const* const entries, size_t const count, size_t const from, bool const lockedOnly)
{
    for (size_t i = from; i < count; ++i)
    {
        Entry const& e = entries[i];
        if ((!lockedOnly || (e.lockBits != 0U)) && deviates(e, readEntry(e)))
        {
            return i;
        }
    }
    return count;
}

/** Static checks of a list, for a static_assert over the generated table: every expected bit and every lock bit
 * inside the mask, a lock bit expected as 1, a class set; SYS-153: a width of 32 or 8 bits, the address aligned to
 * it, a byte register judged in its 8 bits only. */
constexpr bool wellFormed(Entry const* const entries, size_t const count)
{
    for (size_t i = 0U; i < count; ++i)
    {
        Entry const& e = entries[i];
        if (((e.expected & ~e.mask) != 0U) || ((e.lockBits & ~e.mask) != 0U)
            || ((e.expected & e.lockBits) != e.lockBits) || ((e.lockClear & e.lockBits) != 0U)
            || (e.registerClass == RegisterClass::NONE) || (e.mask == 0U))
        {
            return false;
        }
        if ((e.width != WIDTH_32) && (e.width != WIDTH_8))
        {
            return false;
        }
        if (((e.width == WIDTH_32) && ((e.address % 4U) != 0U))
            || ((e.width == WIDTH_8) && (((e.mask | e.lockClear) & ~static_cast<uint32_t>(0xFFU)) != 0U)))
        {
            return false;
        }
    }
    return true;
}
} // namespace registers
} // namespace safety
