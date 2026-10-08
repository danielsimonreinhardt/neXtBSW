#include "uds/app/ApplicationIdentification.h"

#include <bl_config.h>

// COM-44: the record layout, its constants and the CRC-32 of the bootloader core, compiled into the application as
// they are (like DeviceKeySecurity, COM-30) - one definition of the validity record, not a second one
extern "C"
{
#include <app_image.h>
#include <crc32.h>
}

#include <cstring>

namespace uds
{
namespace app
{
namespace
{
/* The ten values of the memory layout in the order of app_image.c layoutValues (tools/canflash LAYOUT_KEYS), each a
 * little-endian 32-bit word in the CRC - the identity the bootloader writes into the record and compares. */
uint32_t const LAYOUT_VALUES[10] = {
    BL_FLASH_START, BL_FLASH_SIZE, BL_SECTOR_SIZE, BL_PHRASE_SIZE,      BL_APP_START,
    BL_APP_END,     BL_APP_RECORD_ADDR, BL_RAM_START,   BL_RAM_END, BL_BOOT_REQUEST_ADDR};

uint32_t layoutId()
{
    uint8_t bytes[sizeof(LAYOUT_VALUES)];
    for (size_t i = 0U; i < (sizeof(LAYOUT_VALUES) / sizeof(LAYOUT_VALUES[0])); ++i)
    {
        for (size_t b = 0U; b < 4U; ++b)
        {
            bytes[(4U * i) + b] = static_cast<uint8_t>(LAYOUT_VALUES[i] >> (8U * b));
        }
    }
    return crc32_update(0U, bytes, static_cast<uint32_t>(sizeof(bytes)));
}

/* app_compat_match() of app_image.c */
uint8_t compatMatch(app_record_t const& r)
{
    if (r.compatVersion != APP_COMPAT_VERSION)
    {
        return static_cast<uint8_t>(APP_COMPAT_FORMAT);
    }
    // a target identity of 0 means "not known" on either side: then only the memory layout is checked
    if ((r.targetId != 0U) && (BL_TARGET_ID != 0UL) && (r.targetId != static_cast<uint32_t>(BL_TARGET_ID)))
    {
        return static_cast<uint8_t>(APP_COMPAT_TARGET);
    }
    if ((r.start != BL_APP_START) || (r.appEnd != BL_APP_END) || (r.recordAddr != BL_APP_RECORD_ADDR))
    {
        return static_cast<uint8_t>(APP_COMPAT_AREA);
    }
    if (r.layoutId != layoutId())
    {
        return static_cast<uint8_t>(APP_COMPAT_LAYOUT);
    }
    return static_cast<uint8_t>(APP_COMPAT_OK);
}

/* app_installed_compat() of app_image.c: read_record(), app_record_valid() and app_record_compat() */
uint8_t installedCompat(::etl::span<uint8_t const> const installed)
{
    if (installed.size() < sizeof(app_record_t))
    {
        return static_cast<uint8_t>(APP_COMPAT_NO_RECORD);
    }
    app_record_t r;
    (void)::memcpy(&r, installed.data(), sizeof(r));
    bool const rangeValid = (r.start >= BL_APP_START) && (r.start < BL_APP_END) && (r.size > 0U)
                            && (r.size <= (BL_APP_END - r.start));
    if ((r.magic != APP_RECORD_MAGIC)
        || (r.recordCrc != crc32_update(0U, reinterpret_cast<uint8_t const*>(&r), 16U))
        || !rangeValid || (r.start != BL_APP_START))
    {
        return static_cast<uint8_t>(APP_COMPAT_NO_RECORD);
    }
    if (r.compatMagic != APP_COMPAT_MAGIC)
    {
        return static_cast<uint8_t>(APP_COMPAT_ABSENT);
    }
    uint8_t const* const block = reinterpret_cast<uint8_t const*>(&r.compatMagic);
    if ((r.compatSize != APP_COMPAT_SIZE) || (r.compatCrc != crc32_update(0U, block, APP_COMPAT_SIZE - 4U)))
    {
        return static_cast<uint8_t>(APP_COMPAT_FORMAT);
    }
    return compatMatch(r);
}
} // namespace

size_t applicationRecordBytes() { return sizeof(app_record_t); }

CompatibilityRecord compatibilityRecord(::etl::span<uint8_t const> const installedRecord)
{
    // the fields of DID 0xF1A0 in bootloader/src/core/uds.c
    return CompatibilityRecord{
        static_cast<uint8_t>(APP_COMPAT_VERSION),
        static_cast<uint32_t>(BL_TARGET_ID),
        layoutId(),
        static_cast<uint32_t>(BL_APP_START),
        static_cast<uint32_t>(BL_APP_END),
        static_cast<uint32_t>(BL_APP_RECORD_ADDR),
        installedCompat(installedRecord)};
}

} // namespace app
} // namespace uds
