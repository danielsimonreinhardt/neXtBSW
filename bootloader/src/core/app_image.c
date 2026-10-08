#include "app_image.h"

#include "bl_config.h"
#include "bl_hal.h"
#include "crc32.h"

#include <string.h>

/* The compatibility block must stay where a record written before BL-02 ends, the fingerprint block where the
 * compatibility block ends (no padding: 32-bit fields around a byte array of a multiple of 4 bytes). */
typedef char app_record_layout_check
    [((sizeof(app_record_t) == (APP_RECORD_V1_SIZE + APP_COMPAT_SIZE + APP_FINGERPRINT_SIZE))
      && ((BL_FINGERPRINT_LEN % 4U) == 0U)) ? 1 : -1];
/* The record is programmed in one piece: whole flash phrases. */
typedef char app_record_phrase_check[((sizeof(app_record_t) % BL_PHRASE_SIZE) == 0U) ? 1 : -1];

#define LAYOUT_VALUE_COUNT 10U

/* In flash, not on the stack: the values are constants of the generated memory layout.
 *
 * PLAT-16: BL_SECTOR_SIZE stays one of the ten values so that the identity of every layout with equally sized
 * sectors is unchanged (the record of the eval board application stays byte-identical). For a flash with sectors
 * of different sizes it is 0; two such layouts with the same addresses but a different sector table would get the
 * same identity - the nine addresses distinguish the realistic cases, see doc/changelog.d/PLAT-16.md. */
static uint32_t const layoutValues[LAYOUT_VALUE_COUNT] = {
    BL_FLASH_START, BL_FLASH_SIZE, BL_SECTOR_SIZE, BL_PHRASE_SIZE,      BL_APP_START,
    BL_APP_END,     BL_APP_RECORD_ADDR, BL_RAM_START,   BL_RAM_END, BL_BOOT_REQUEST_ADDR};

uint32_t app_layout_id(void)
{
    return crc32_update(0U, (uint8_t const*)layoutValues, (uint32_t)sizeof(layoutValues));
}

bool app_range_valid(uint32_t start, uint32_t size)
{
    return (start >= BL_APP_START) && (start < BL_APP_END) && (size > 0U)
           && (size <= (BL_APP_END - start));
}

void app_record_build(app_record_t* record, uint32_t start, uint32_t size, uint32_t crc, uint8_t const* fingerprint)
{
    (void)memset(&record->fpMagic, 0xFF, APP_FINGERPRINT_SIZE);
    if (fingerprint != NULL)
    {
        record->fpMagic = APP_FINGERPRINT_MAGIC;
        (void)memcpy(record->fingerprint, fingerprint, BL_FINGERPRINT_LEN);
        record->fpCrc = crc32_update(0U, (uint8_t const*)&record->fpMagic, APP_FINGERPRINT_SIZE - 4U);
    }

    record->magic     = APP_RECORD_MAGIC;
    record->start     = start;
    record->size      = size;
    record->crc       = crc;
    record->recordCrc = crc32_update(0U, (uint8_t const*)record, 16U);
    record->reserved  = 0xFFFFFFFFUL;

    record->compatMagic   = APP_COMPAT_MAGIC;
    record->compatVersion = APP_COMPAT_VERSION;
    record->compatSize    = APP_COMPAT_SIZE;
    record->targetId      = BL_TARGET_ID;
    record->layoutId      = app_layout_id();
    record->appEnd        = BL_APP_END;
    record->recordAddr    = BL_APP_RECORD_ADDR;
    record->compatCrc     = crc32_update(0U, (uint8_t const*)&record->compatMagic, APP_COMPAT_SIZE - 4U);
}

bool app_record_valid(app_record_t const* record)
{
    return (record->magic == APP_RECORD_MAGIC)
           && (record->recordCrc == crc32_update(0U, (uint8_t const*)record, 16U))
           && app_range_valid(record->start, record->size);
}

app_compat_result_t app_compat_match(uint32_t version, uint32_t targetId, uint32_t layoutId, uint32_t appStart,
                                     uint32_t appEnd, uint32_t recordAddr)
{
    if (version != APP_COMPAT_VERSION)
    {
        return APP_COMPAT_FORMAT;
    }
    /* A target identity of 0 means "not known": either side may be built without it (BL_TARGET_ID), then only the
     * memory layout is checked. */
    if ((targetId != 0U) && (BL_TARGET_ID != 0UL) && (targetId != (uint32_t)BL_TARGET_ID))
    {
        return APP_COMPAT_TARGET;
    }
    if ((appStart != BL_APP_START) || (appEnd != BL_APP_END) || (recordAddr != BL_APP_RECORD_ADDR))
    {
        return APP_COMPAT_AREA;
    }
    if (layoutId != app_layout_id())
    {
        return APP_COMPAT_LAYOUT;
    }
    return APP_COMPAT_OK;
}

app_compat_result_t app_record_compat(app_record_t const* record)
{
    if (record->compatMagic != APP_COMPAT_MAGIC)
    {
        return APP_COMPAT_ABSENT;
    }
    if ((record->compatSize != APP_COMPAT_SIZE)
        || (record->compatCrc != crc32_update(0U, (uint8_t const*)&record->compatMagic, APP_COMPAT_SIZE - 4U)))
    {
        return APP_COMPAT_FORMAT;
    }
    return app_compat_match(record->compatVersion, record->targetId, record->layoutId, record->start, record->appEnd,
                            record->recordAddr);
}

/* Family hook hal_app_entry_plausible() (bl_hal.h, BL-08): what an application has at its start is a convention of
 * the family (Cortex-M vector table, Cortex-R forwarding table of decision E2), so its definition comes from
 * bootloader/platforms/<family>/bl_family_image.inc. It is compiled here, at the place of the former Cortex-M check
 * of the core, not as a file of its own: the build lists of bootloader, updater and host tests stay as they are,
 * and the Cortex-M bootloader images stay byte-identical to before BL-08 (GCC emits the functions of a translation
 * unit in definition order). */
#include "bl_family_image.inc"


static bool read_record(app_record_t* record)
{
    (void)memcpy(record, hal_flash_ptr(BL_APP_RECORD_ADDR), sizeof(*record));
    return app_record_valid(record) && (record->start == BL_APP_START);
}

app_compat_result_t app_installed_compat(void)
{
    app_record_t record;
    if (!read_record(&record))
    {
        return APP_COMPAT_NO_RECORD;
    }
    return app_record_compat(&record);
}

bool app_installed_fingerprint(uint8_t fingerprint[BL_FINGERPRINT_LEN])
{
    app_record_t record;
    if (!read_record(&record) || (record.fpMagic != APP_FINGERPRINT_MAGIC)
        || (record.fpCrc != crc32_update(0U, (uint8_t const*)&record.fpMagic, APP_FINGERPRINT_SIZE - 4U)))
    {
        return false;
    }
    (void)memcpy(fingerprint, record.fingerprint, BL_FINGERPRINT_LEN);
    return true;
}

/* Everything of the image check except the CRC over the image: valid record at the application start, checked
 * compatibility block, an application start the family accepts. Cheap (record, compatibility and layout CRC over
 * some 100 bytes, two words of the image), so it runs at bl_init() and again right before the jump (BL-38). */
static bool record_startable(app_record_t* record)
{
    if (!read_record(record))
    {
        return false;
    }
    /* BL-36 (SYS69-S10, BLI-F01): only a checked compatibility block starts. A record without it
     * (APP_COMPAT_ABSENT, written before BL-02) is not startable any more: the block is not covered by recordCrc,
     * so one corrupted word compatMagic would otherwise skip the target and layout check of SM-51. */
    if (app_record_compat(record) != APP_COMPAT_OK)
    {
        return false;
    }
    return hal_app_entry_plausible(record->start, record->size);
}

bool app_image_valid(app_image_check_t* checked)
{
    app_record_t record;
    checked->size = 0U;
    checked->crc  = 0U;
    if (!record_startable(&record))
    {
        return false;
    }
    /* the one CRC-32 over the application per start (BL-38); its result goes to the jump, not only a bool */
    checked->size = record.size;
    checked->crc  = crc32_update(0U, hal_flash_ptr(record.start), record.size);
    return checked->crc == record.crc;
}

bool app_image_confirm(app_image_check_t const* checked)
{
    app_record_t record;
    /* BL-38 (SYS69-S11): the record is read again from flash and checked like at the start; its CRC field and size
     * must still be the ones the image CRC of app_image_valid() was computed for and matched. A changed record,
     * compatibility block or entry, a record of another image, and a start decision corrupted in RAM (a CRC result
     * that never matched cannot turn into the record's value by a bit error) all stop the jump. */
    return record_startable(&record) && (record.size == checked->size) && (record.crc == checked->crc);
}
