/**
 * Checksum list of the ROM check (SM-14, SYS-38): plausibility and walk over the areas, without hardware access.
 *
 * Layout written by the linker script (port/main/linkerscript/application.dld.in, section .checksum):
 *     word 0            number of areas n
 *     word 1 + 2 i      start address of area i (inclusive, word aligned)
 *     word 2 + 2 i      end address of area i (exclusive, word aligned)
 * followed by the reference CRC (__checksum_result), written after the link by tools/romCheckImage.py.
 *
 * Before SYS-38 RomCheck took word 0 as it came: in an image without the list the flash reads 0xFFFFFFFF there and the
 * loop would have run over 0x7FFFFFFF areas at the addresses 0xFFFFFFFF. checkList() refuses such a list; RomCheck
 * then reports ROM_CHECK_FAILURE instead of reading anything. The rules are the same as in romCheckImage.py, which
 * refuses to build such an image in the first place.
 */

#pragma once

#include <cstddef>
#include <cstdint>

namespace safety
{
namespace romcheck
{
/** Largest number of areas a list may name (same limit as MAX_AREAS in tools/romCheckImage.py). */
constexpr uint32_t MAX_AREAS = 16U;

/** An image to check (SYS-44): the list, its length in words, the lowest address an area may name and the reference.
 * RomCheck::idle() takes the linker symbols, the host test its own image. */
struct Image
{
    uintptr_t const* list;
    size_t listWords;
    uintptr_t lowest;
    uint32_t reference;
};

enum class ListStatus : uint8_t
{
    VALID       = 0,
    BAD_COUNT   = 1, ///< number of areas 0 or above MAX_AREAS (erased flash: 0xFFFFFFFF)
    BAD_LENGTH  = 2, ///< list length of the linker symbols does not fit the number of areas
    BAD_AREA    = 3  ///< an area not word aligned, reversed or outside [lowest, highest)
};

/**
 * \param list      the list (word 0: number of areas)
 * \param listWords number of words between the start and the end symbol of the list
 * \param lowest    lowest address an area may start at (start of the application flash)
 * \param highest   highest address an area may end at (the list itself: it is not part of any area)
 */
inline ListStatus
checkList(uintptr_t const* const list, size_t const listWords, uintptr_t const lowest, uintptr_t const highest)
{
    if (listWords == 0U)
    {
        return ListStatus::BAD_LENGTH;
    }
    uintptr_t const count = list[0];
    if ((count == 0U) || (count > MAX_AREAS))
    {
        return ListStatus::BAD_COUNT;
    }
    if (listWords != ((2U * static_cast<size_t>(count)) + 1U))
    {
        return ListStatus::BAD_LENGTH;
    }
    for (size_t i = 0U; i < static_cast<size_t>(count); ++i)
    {
        uintptr_t const start = list[(2U * i) + 1U];
        uintptr_t const end   = list[(2U * i) + 2U];
        bool const aligned    = ((start % sizeof(uint32_t)) == 0U) && ((end % sizeof(uint32_t)) == 0U);
        if ((!aligned) || (start > end) || (start < lowest) || (end > highest))
        {
            return ListStatus::BAD_AREA;
        }
    }
    return ListStatus::VALID;
}

/**
 * Feeds every word of every area to \p feed (a callable taking uint32_t), in list order. Only for a list that
 * checkList() accepted.
 */
template<typename Feed>
void feedAreas(uintptr_t const* const list, Feed& feed)
{
    size_t const count = static_cast<size_t>(list[0]);
    for (size_t i = 0U; i < count; ++i)
    {
        auto const* word      = reinterpret_cast<uint32_t const*>(list[(2U * i) + 1U]);
        auto const* const end = reinterpret_cast<uint32_t const*>(list[(2U * i) + 2U]);
        for (; word < end; ++word)
        {
            feed(*word);
        }
    }
}

} // namespace romcheck
} // namespace safety
