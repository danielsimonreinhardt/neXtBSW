/* Fail-safe replacement of the bootloader area (BL_FLASH_START..BL_APP_START), see doc in the BL-01 changelog.
 *
 * Order of writes (the MCU always boots from sector 0, whose first phrase is the entry: reset vector):
 *  1. sector 0 := stub of the family (s32k1xx: updater vector table + Flash Configuration Field). From here on
 *     every reset starts the updater directly, which resumes the update. Phrase order: early phrases of the
 *     family (s32k1xx: FCF), entry phrase, rest.
 *  2. the remaining sectors of the bootloader area := new bootloader, each erased, programmed and verified.
 *     Sector starts and sizes come from the generated sector table (bl_sectors.h, PLAT-16), not from a mask:
 *     a region may have sectors of different sizes (STM32F4).
 *  3. sector 0 := new bootloader. Phrase order: early phrases, rest, entry phrase last.
 *  4. whole area verified, then the validity record is erased: the new bootloader stays active.
 * Only steps 1 and 3 have a window in which a power loss leaves an unbootable chip (erase of sector 0
 * until its entry phrase is programmed). On s32k1xx the FCF is programmed first so the chip is secured on reset
 * (mass erase still enabled) only between erase and the first two phrases.
 *
 * What an image of the family has at its start, how the stub looks and which phrases come early is the family's
 * (upd_family.h in bootloader/platforms/<family>, BL-08); this core knows no image layout of its own. */

#include "bl_update.h"

#include "bl_config.h"
#include "bl_sectors.h"
#include "crc32.h"
#include "upd_config.h"
#include "upd_family.h" /* image start, stub and early phrases of the family (BL-08) */
#include "upd_hal.h"

#include <stddef.h>
#include <string.h>

#define CODE_START UPD_IMAGE_HEADER_SIZE

/* X-macro expansions of UPD_STUB_EARLY_PHRASES (upd_family.h), inside program_sector() */
#define PROGRAM_EARLY_PHRASE(offset) &&program_phrase(base, buf, (offset))
#define IS_EARLY_PHRASE(offset)      || (off == (offset))

typedef enum
{
    ORDER_STUB,
    ORDER_BODY,
    ORDER_FINAL
} order_t;

/* Largest sector of the bootloader area (generated, PLAT-16): on s32k1xx one sector size, on STM32F4 the first
 * sectors of the region (F446: 16 KB) - not the 128 KB of the flash end. */
static uint8_t stubBuf[BL_BOOT_SECTOR_MAX_SIZE];
static uint8_t sectorBuf[BL_BOOT_SECTOR_MAX_SIZE];

static uint32_t le32(uint8_t const* p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static bool version_compatible(uint8_t const* img, uint32_t size)
{
    /* "<prefix> <major>." with the project's prefix or the former one (PLAT-08). BL_VERSION_STRING starts with
     * UPD_VERSION_PREFIX by construction; the two prefixes may differ in length. */
    static char const own[]             = BL_VERSION_STRING;
    static char const* const accepted[] = {UPD_VERSION_PREFIX, UPD_VERSION_PREFIX_FORMER};
    static uint32_t const acceptedLen[] = {
        (uint32_t)(sizeof(UPD_VERSION_PREFIX) - 1U), (uint32_t)(sizeof(UPD_VERSION_PREFIX_FORMER) - 1U)};
    uint32_t const prefixLen = acceptedLen[0];
    uint32_t majorEnd        = prefixLen;
    while ((own[majorEnd] != '\0') && (own[majorEnd] != '.'))
    {
        ++majorEnd;
    }
    if ((own[majorEnd] != '.') || (majorEnd == prefixLen))
    {
        return false;
    }
    char const* const major = &own[prefixLen];
    uint32_t const majorLen = majorEnd + 1U - prefixLen; /* "<major>." */
    for (uint32_t addr = CODE_START; addr < size; ++addr)
    {
        for (uint32_t i = 0U; i < 2U; ++i)
        {
            uint32_t const n = acceptedLen[i];
            if (((size - addr) >= (n + majorLen)) && (memcmp(&img[addr], accepted[i], n) == 0)
                && (memcmp(&img[addr + n], major, majorLen) == 0))
            {
                return true;
            }
        }
    }
    return false;
}

upd_result_t upd_check_blob(uint8_t const* blob, uint32_t blobLen)
{
    if ((blob == NULL) || (blobLen < UPD_BLOB_HEADER_SIZE) || (le32(blob) != UPD_BLOB_MAGIC)
        || (le32(&blob[12]) != crc32_update(0U, blob, 12U)))
    {
        return UPD_ERR_HEADER;
    }
    uint32_t const size = le32(&blob[4]);
    if ((size < CODE_START) || (size > UPD_BL_AREA_SIZE) || (size > (blobLen - UPD_BLOB_HEADER_SIZE)))
    {
        return UPD_ERR_SIZE;
    }
    uint8_t const* const img = &blob[UPD_BLOB_HEADER_SIZE];
    if (crc32_update(0U, img, size) != le32(&blob[8]))
    {
        return UPD_ERR_CRC;
    }
    /* Start of the image as the family expects it (BL-08): statements of upd_family_check_image.inc in
     * bootloader/platforms/<family>, returning UPD_ERR_CONFIG_FIELD or UPD_ERR_VECTORS for img[0..size). Included
     * as statements, not called, so that the s32k1xx updater stays byte-identical to before BL-08. */
#include "upd_family_check_image.inc"
    if (!version_compatible(img, size))
    {
        return UPD_ERR_COMPAT;
    }
    return UPD_OK;
}

static bool stub_plausible(void) { return upd_family_stub_plausible(stubBuf); }

static void fill_sector(uint8_t const* img, uint32_t size, uint32_t base, uint32_t sectorSize)
{
    (void)memset(sectorBuf, 0xFF, sizeof(sectorBuf));
    if (base < size)
    {
        uint32_t const n = ((size - base) < sectorSize) ? (size - base) : sectorSize;
        (void)memcpy(sectorBuf, &img[base], n);
    }
}

static bool range_erased(uint32_t address, uint32_t len)
{
    uint8_t const* const p = upd_flash_ptr(address);
    for (uint32_t i = 0U; i < len; ++i)
    {
        if (p[i] != 0xFFU)
        {
            return false;
        }
    }
    return true;
}

/* Image offsets are relative to the start of the code flash (BL_FLASH_START; 0 on s32k1xx, 0x08000000 on STM32). */
static bool area_matches(uint8_t const* img, uint32_t size)
{
    return (memcmp(upd_flash_ptr(BL_FLASH_START), img, size) == 0)
           && range_erased(BL_FLASH_START + size, UPD_BL_AREA_SIZE - size);
}

static bool program_phrase(uint32_t base, uint8_t const* buf, uint32_t off)
{
    if (memcmp(upd_flash_ptr(base + off), &buf[off], BL_PHRASE_SIZE) == 0)
    {
        return true; /* already there (erased phrases need no programming) */
    }
    return upd_flash_program_phrase(base + off, &buf[off]);
}

static uint32_t elapsed(uint32_t t0) { return upd_micros() - t0; }

static bool program_sector(uint32_t base, uint8_t const* buf, uint32_t sectorSize, order_t order, bool measure,
                           uint32_t t0, uint32_t* windowUs, upd_stats_t* stats)
{
    if (order != ORDER_BODY)
    {
        /* early phrases of the family (s32k1xx: FCF) right after the erase */
        if (!(true UPD_STUB_EARLY_PHRASES(PROGRAM_EARLY_PHRASE)))
        {
            return false;
        }
        if (measure && (elapsed(t0) > stats->securedUs))
        {
            stats->securedUs = elapsed(t0);
        }
        if (order == ORDER_STUB)
        {
            if (!program_phrase(base, buf, 0U))
            {
                return false;
            }
            if (measure)
            {
                *windowUs = elapsed(t0);
            }
        }
    }
    for (uint32_t off = 0U; off < sectorSize; off += BL_PHRASE_SIZE)
    {
        bool const special = (order != ORDER_BODY) && ((off == 0U) UPD_STUB_EARLY_PHRASES(IS_EARLY_PHRASE));
        if (!special && !program_phrase(base, buf, off))
        {
            return false;
        }
    }
    if (order == ORDER_FINAL)
    {
        if (!program_phrase(base, buf, 0U))
        {
            return false;
        }
        if (measure)
        {
            *windowUs = elapsed(t0);
        }
    }
    return true;
}

/* A sector is completable if every phrase is either already correct or still erased. */
static bool sector_completable(uint32_t base, uint8_t const* buf, uint32_t sectorSize)
{
    uint8_t const* const p = upd_flash_ptr(base);
    for (uint32_t off = 0U; off < sectorSize; off += BL_PHRASE_SIZE)
    {
        if ((memcmp(&p[off], &buf[off], BL_PHRASE_SIZE) != 0) && !range_erased(base + off, BL_PHRASE_SIZE))
        {
            return false;
        }
    }
    return true;
}

static bool write_sector(uint32_t base, uint8_t const* buf, uint32_t sectorSize, order_t order, uint32_t* windowUs,
                         upd_stats_t* stats)
{
    for (uint32_t attempt = 0U; attempt < UPD_MAX_ATTEMPTS; ++attempt)
    {
        if (attempt > 0U)
        {
            stats->retries++;
        }
        bool ok;
        /* Resume of an interrupted stub: complete it without opening another erase window. */
        if ((order == ORDER_STUB) && (attempt == 0U) && sector_completable(base, buf, sectorSize))
        {
            ok = program_sector(base, buf, sectorSize, order, false, 0U, windowUs, stats);
        }
        else
        {
            uint32_t const t0 = upd_micros();
            ok = upd_flash_erase_sector(base)
                 && program_sector(base, buf, sectorSize, order, true, t0, windowUs, stats);
        }
        if (ok && (memcmp(upd_flash_ptr(base), buf, sectorSize) == 0))
        {
            return true;
        }
    }
    return false;
}

static bool invalidate_record(void)
{
    bl_sector_t record;
    if (!bl_record_sector(&record))
    {
        return false;
    }
    for (uint32_t attempt = 0U; attempt < UPD_MAX_ATTEMPTS; ++attempt)
    {
        if (range_erased(record.start, record.size))
        {
            return true;
        }
        (void)upd_flash_erase_sector(record.start);
    }
    return range_erased(record.start, record.size);
}

static upd_result_t fail(upd_result_t result, upd_stats_t const* stats)
{
    upd_report(UPD_STEP_ERROR, (uint8_t)result, stats);
    return result;
}

upd_result_t upd_run(uint8_t const* blob, uint32_t blobLen, bool environmentOk, bool forceRewrite,
                     upd_stats_t* stats)
{
    (void)memset(stats, 0, sizeof(*stats));

    (void)memset(stubBuf, 0xFF, sizeof(stubBuf));
    upd_family_build_stub(stubBuf);

    /* Reset vector of the stub in sector 0: the updater was started directly after an interruption. */
    bool const resumed = memcmp(upd_flash_ptr(BL_FLASH_START), stubBuf, BL_PHRASE_SIZE) == 0;
    stats->start       = resumed ? (uint8_t)UPD_START_RESUME : (uint8_t)UPD_START_FRESH;
    upd_report(UPD_STEP_START, stats->start, stats);

    upd_result_t result = upd_check_blob(blob, blobLen);
    if ((result == UPD_OK) && !stub_plausible())
    {
        result = UPD_ERR_STUB;
    }
    if ((result == UPD_OK) && !environmentOk)
    {
        result = UPD_ERR_ENVIRONMENT;
    }
    if (result != UPD_OK)
    {
        /* Nothing written: the old bootloader must not start the updater again. */
        if (!resumed)
        {
            (void)invalidate_record();
        }
        return fail(result, stats);
    }
    upd_report(UPD_STEP_CHECKED, 0U, stats);

    uint32_t const size      = le32(&blob[4]);
    uint8_t const* const img = &blob[UPD_BLOB_HEADER_SIZE];

    if (!resumed && !forceRewrite && area_matches(img, size))
    {
        stats->start = (uint8_t)UPD_START_INSTALLED;
    }
    else
    {
        bl_sector_t first;
        /* The buffers hold the largest sector of the bootloader area (BL_BOOT_SECTOR_MAX_SIZE); a generated
         * table that says otherwise must stop the update, not overrun them. */
        if (!bl_sector_at(BL_FLASH_START, &first) || (first.start != BL_FLASH_START)
            || (first.size > sizeof(stubBuf)))
        {
            return fail(UPD_ERR_STUB, stats);
        }
        if (!write_sector(first.start, stubBuf, first.size, ORDER_STUB, &stats->stubUs, stats))
        {
            return fail(UPD_ERR_FLASH_STUB, stats);
        }
        upd_report(UPD_STEP_STUB, 0U, stats);

        /* Remaining sectors of the bootloader area, from the sector table (sizes may differ, PLAT-16) */
        uint32_t index = 1U;
        for (uint32_t base = first.start + first.size; base < (BL_FLASH_START + UPD_BL_AREA_SIZE); ++index)
        {
            bl_sector_t sector;
            if (!bl_sector_at(base, &sector) || (sector.start != base) || (sector.size > sizeof(sectorBuf)))
            {
                return fail(UPD_ERR_FLASH_BODY, stats);
            }
            fill_sector(img, size, base - BL_FLASH_START, sector.size);
            if (!write_sector(base, sectorBuf, sector.size, ORDER_BODY, NULL, stats))
            {
                return fail(UPD_ERR_FLASH_BODY, stats);
            }
            upd_report(UPD_STEP_BODY, (uint8_t)index, stats);
            base += sector.size;
        }

        fill_sector(img, size, 0U, first.size);
        if (!write_sector(first.start, sectorBuf, first.size, ORDER_FINAL, &stats->finalUs, stats))
        {
            return fail(UPD_ERR_FLASH_FINAL, stats);
        }
        upd_report(UPD_STEP_FINAL, 0U, stats);

        if (!area_matches(img, size))
        {
            return fail(UPD_ERR_VERIFY, stats);
        }
    }

    if (!invalidate_record())
    {
        return fail(UPD_ERR_FLASH_RECORD, stats);
    }
    upd_report(UPD_STEP_RECORD, 0U, stats);

    result = (stats->start == (uint8_t)UPD_START_INSTALLED) ? UPD_OK_ALREADY_INSTALLED : UPD_OK;
    upd_report(UPD_STEP_DONE, (uint8_t)result, stats);
    return result;
}
