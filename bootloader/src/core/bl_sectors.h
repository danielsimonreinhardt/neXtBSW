#pragma once

/* Sector table of the code flash (PLAT-16).
 *
 * The core and the updater used to round addresses with BL_SECTOR_SIZE as a power-of-two mask. That carries only
 * flashes whose sectors are all the same size (S32K1: 2 or 4 KB, S32K3: 8 KB), not STM32F4 (F446: 4 x 16 KB,
 * 1 x 64 KB, 3 x 128 KB in one region, doc/architecture/plattformen.md 2.3). The geometry therefore has to be
 * data, not a mask.
 *
 * The data comes from the derivative database (memory[].sectors / sector_size) through the generated
 * bootloader/bl_memory.h - the same single source as every other address (ARCH-08). Nothing here is family
 * specific: a family HAL needs the sector start, its size and, where the hardware addresses sectors by number
 * instead of by address (STM32 FLASH_CR.SNB), its index.
 *
 * Header only (static inline), like bl_sosc_s32k1xx.h of BL-05: every consumer keeps its own copy of the small
 * group table, and no source list of a build or a test has to grow - tools/bswgen/test_blgen.py, which links the
 * bootloader and the updater for a second derivative, names its sources by hand and was not an allowed path for
 * PLAT-16. If the duplication ever costs too much flash, moving the bodies into a bl_sectors.c is a mechanical
 * change (proposal in doc/changelog.d/PLAT-16.md).
 */

#include "bl_config.h"

#include <stdbool.h>
#include <stdint.h>

#if !defined(BL_SECTOR_GROUPS)
/* Generated layout header without a sector table (blgen before PLAT-16, diff proposal in the changelog): one
 * group of equally sized sectors from BL_SECTOR_SIZE. Keeps the target build of every existing project working
 * and its binary comparable. */
#if BL_SECTOR_SIZE == 0UL
#error "bl_memory.h has neither BL_SECTOR_GROUPS nor a sector size (BL_SECTOR_SIZE 0 means sectors differ in size)"
#endif
#define BL_SECTOR_GROUP_COUNT   1U
#define BL_SECTOR_GROUPS        {BL_FLASH_START, BL_SECTOR_SIZE, BL_FLASH_SIZE / BL_SECTOR_SIZE}
#define BL_SECTOR_COUNT         (BL_FLASH_SIZE / BL_SECTOR_SIZE)
#define BL_SECTOR_MAX_SIZE      BL_SECTOR_SIZE
#define BL_BOOT_SECTOR_MAX_SIZE BL_SECTOR_SIZE
#endif

typedef struct
{
    uint32_t start;
    uint32_t size;
    uint32_t count;
} bl_sector_group_t;

typedef struct
{
    uint32_t start;
    uint32_t size;
    uint32_t index; /* sector number in the region, 0 = first sector (e.g. STM32 FLASH_CR.SNB) */
} bl_sector_t;

#define BL_SECTOR_TABLE_DECL static bl_sector_group_t const blSectorGroups[BL_SECTOR_GROUP_COUNT] = {BL_SECTOR_GROUPS}

static inline uint32_t bl_sector_group_end(bl_sector_group_t const* group)
{
    return group->start + (group->size * group->count);
}

/* Number of sectors of the code flash region. */
static inline uint32_t bl_sector_count(void)
{
    BL_SECTOR_TABLE_DECL;
    uint32_t count = 0U;
    for (uint32_t g = 0U; g < BL_SECTOR_GROUP_COUNT; ++g)
    {
        count += blSectorGroups[g].count;
    }
    return count;
}

/* Sector containing `address`; false if the address is outside the code flash region. */
static inline bool bl_sector_at(uint32_t address, bl_sector_t* sector)
{
    BL_SECTOR_TABLE_DECL;
    uint32_t index = 0U;
    for (uint32_t g = 0U; g < BL_SECTOR_GROUP_COUNT; ++g)
    {
        bl_sector_group_t const* const group = &blSectorGroups[g];
        if ((address >= group->start) && (address < bl_sector_group_end(group)))
        {
            uint32_t const n = (address - group->start) / group->size;
            sector->start    = group->start + (n * group->size);
            sector->size     = group->size;
            sector->index    = index + n;
            return true;
        }
        index += group->count;
    }
    return false;
}

/* Sector by number; false if there is no such sector. */
static inline bool bl_sector_by_index(uint32_t index, bl_sector_t* sector)
{
    BL_SECTOR_TABLE_DECL;
    uint32_t first = 0U;
    for (uint32_t g = 0U; g < BL_SECTOR_GROUP_COUNT; ++g)
    {
        bl_sector_group_t const* const group = &blSectorGroups[g];
        if (index < (first + group->count))
        {
            sector->start = group->start + ((index - first) * group->size);
            sector->size  = group->size;
            sector->index = index;
            return true;
        }
        first += group->count;
    }
    return false;
}

/* True if `address` is the first byte of a sector. */
static inline bool bl_sector_aligned(uint32_t address)
{
    bl_sector_t sector;
    return bl_sector_at(address, &sector) && (sector.start == address);
}

/* Smallest sector aligned range [*from, *to) that covers [start, start + size); false outside the region. */
static inline bool bl_sector_range(uint32_t start, uint32_t size, uint32_t* from, uint32_t* to)
{
    bl_sector_t first;
    bl_sector_t last;
    if ((size == 0U) || !bl_sector_at(start, &first) || !bl_sector_at(start + (size - 1U), &last))
    {
        return false;
    }
    *from = first.start;
    *to   = last.start + last.size;
    return true;
}

/* Sector holding the validity record (BL_APP_RECORD_ADDR). */
static inline bool bl_record_sector(bl_sector_t* sector) { return bl_sector_at(BL_APP_RECORD_ADDR, sector); }

/* True if [address, address + len) lies inside [from, from + size).
 *
 * Deliberately without a comparison against the lower bound: BL_FLASH_START is 0 on s32k1xx, and
 * `address >= BL_FLASH_START` is then an always-true comparison (-Werror=type-limits in the target build).
 * Subtracting first wraps an address below `from` to a very large offset, which fails the check. */
static inline bool bl_range_inside(uint32_t address, uint32_t len, uint32_t from, uint32_t size)
{
    uint32_t const offset = address - from;
    return (offset <= size) && (len <= (size - offset));
}

/* Self check of the generated table, for host tests: groups in address order without gap or overlap, covering
 * exactly the code flash region; bootloader area, application area and record on sector boundaries; record sector
 * outside the application area; BL_SECTOR_SIZE either 0 or the one sector size; no sector of the bootloader area
 * larger than the updater's buffers. */
static inline bool bl_sector_map_ok(void)
{
    BL_SECTOR_TABLE_DECL;
    uint32_t next = BL_FLASH_START;
    for (uint32_t g = 0U; g < BL_SECTOR_GROUP_COUNT; ++g)
    {
        bl_sector_group_t const* const group = &blSectorGroups[g];
        if ((group->size == 0U) || (group->count == 0U) || (group->start != next))
        {
            return false;
        }
        if ((BL_SECTOR_SIZE != 0UL) && (group->size != BL_SECTOR_SIZE))
        {
            return false;
        }
        if (group->size > BL_SECTOR_MAX_SIZE)
        {
            return false;
        }
        next = bl_sector_group_end(group);
    }
    if (next != (BL_FLASH_START + BL_FLASH_SIZE))
    {
        return false;
    }

    bl_sector_t record;
    if (!bl_sector_aligned(BL_APP_START) || !bl_record_sector(&record) || (record.start != BL_APP_RECORD_ADDR))
    {
        return false;
    }
    /* The application area must end on a sector boundary (BL_APP_END is exclusive, the region end counts). */
    if ((BL_APP_END != (BL_FLASH_START + BL_FLASH_SIZE)) && !bl_sector_aligned(BL_APP_END))
    {
        return false;
    }
    if ((BL_APP_START >= BL_APP_END) || (BL_APP_START < BL_FLASH_START))
    {
        return false;
    }
    /* The record must not sit inside the application area: erasing the application would take it with it. */
    if ((record.start < BL_APP_END) && ((record.start + record.size) > BL_APP_START))
    {
        return false;
    }
    for (uint32_t address = BL_FLASH_START; address < BL_APP_START;)
    {
        bl_sector_t sector;
        if (!bl_sector_at(address, &sector) || (sector.size > BL_BOOT_SECTOR_MAX_SIZE))
        {
            return false;
        }
        address = sector.start + sector.size;
    }
    return true;
}
