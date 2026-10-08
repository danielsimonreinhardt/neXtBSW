#pragma once

#include "bl_config.h"

#include <stdbool.h>
#include <stdint.h>

#define APP_RECORD_MAGIC      0x56505041UL /* "APPV" */
#define APP_COMPAT_MAGIC      0x43505041UL /* "APPC", compatibility block (BL-02) */
#define APP_FINGERPRINT_MAGIC 0x46505041UL /* "APPF", fingerprint block (BL-10) */

/* Version of the compatibility block. Its fields never move; anything new is appended as a further block with its own
 * magic, size and CRC behind it, so a reader of version 1 keeps working. A raised version means the opposite: older
 * readers must not trust the block any more (they answer APP_COMPAT_FORMAT). */
#define APP_COMPAT_VERSION 1UL

/* Result of the compatibility check: image (or installed record) against this bootloader's board, derivative and
 * memory layout. Also the status byte of routine 0xF1A0 (0xFF02 before BL-24) and the last byte
 * of DID 0xF1A0. */
typedef enum
{
    APP_COMPAT_OK        = 0x00U, /* target and image match */
    APP_COMPAT_FORMAT    = 0x01U, /* block present but damaged or of an unusable version */
    APP_COMPAT_TARGET    = 0x02U, /* built for another board or derivative */
    APP_COMPAT_LAYOUT    = 0x03U, /* other memory partitioning (flash, sectors, RAM, boot request) */
    APP_COMPAT_AREA      = 0x04U, /* other application area (start, end, record address) */
    APP_COMPAT_ABSENT    = 0x10U, /* no compatibility block: record written before BL-02, not started (BL-36) */
    APP_COMPAT_NO_RECORD = 0xFFU  /* no valid validity record at all */
} app_compat_result_t;

/* Stored at BL_APP_RECORD_ADDR. Size is a multiple of the flash phrase size.
 *
 * The first 24 bytes are the record of milestone 2 and must stay exactly as they are: a bootloader flashed before
 * BL-02 copies only those, checks recordCrc over the first 16 bytes and ignores everything behind them. The
 * compatibility block is therefore additional information for newer bootloaders, not a format change. The
 * fingerprint block (BL-10) follows the same rule behind the compatibility block: a bootloader before BL-10 copies
 * 56 bytes and never sees it, a record written before BL-10 has it erased (no fingerprint). */
typedef struct
{
    uint32_t magic;
    uint32_t start;
    uint32_t size;
    uint32_t crc;
    uint32_t recordCrc; /* CRC-32 over the four fields above */
    uint32_t reserved;  /* 0xFFFFFFFF */
    /* compatibility block (BL-02), 0xFFFFFFFF in an erased sector = not present */
    uint32_t compatMagic;
    uint32_t compatVersion;
    uint32_t compatSize; /* bytes from compatMagic to compatCrc inclusive */
    uint32_t targetId;   /* board/derivative identity, 0 = unknown (then not checked) */
    uint32_t layoutId;   /* identity of the generated memory layout, see app_layout_id() */
    uint32_t appEnd;
    uint32_t recordAddr;
    uint32_t compatCrc; /* CRC-32 over compatSize - 4 bytes from compatMagic on */
    /* fingerprint block (BL-10): DID 0xF184 of the session that programmed this application (tester and date,
     * bl_config.h), so it survives the reset after the download; erased = not present */
    uint32_t fpMagic;
    uint8_t fingerprint[BL_FINGERPRINT_LEN];
    uint32_t fpCrc; /* CRC-32 over fpMagic and fingerprint */
} app_record_t;

#define APP_RECORD_V1_SIZE   24U
#define APP_COMPAT_SIZE      32U                         /* compatMagic .. compatCrc */
#define APP_FINGERPRINT_SIZE (8U + BL_FINGERPRINT_LEN)  /* fpMagic .. fpCrc */

/* CRC-32 over the ten generated memory layout values in the order of tools/canflash LAYOUT_KEYS, each as a
 * little-endian 32-bit word: flash start and size, sector and phrase size, application start and end, record
 * address, RAM start and end, boot request address. tools/canflash computes the same value for an image. */
uint32_t app_layout_id(void);

bool app_range_valid(uint32_t start, uint32_t size);
/* fingerprint: BL_FINGERPRINT_LEN bytes for the fingerprint block, NULL leaves the block erased (BL-10) */
void app_record_build(app_record_t* record, uint32_t start, uint32_t size, uint32_t crc, uint8_t const* fingerprint);
bool app_record_valid(app_record_t const* record);
/* Compatibility of the values an image (or a record) claims against this bootloader's target. */
app_compat_result_t app_compat_match(uint32_t version, uint32_t targetId, uint32_t layoutId, uint32_t appStart,
                                     uint32_t appEnd, uint32_t recordAddr);
app_compat_result_t app_record_compat(app_record_t const* record);
/* Compatibility of the installed application's record; APP_COMPAT_NO_RECORD without a valid record. */
app_compat_result_t app_installed_compat(void);
/* Fingerprint of the installed application (BL-10): false without a valid record or without a valid fingerprint
 * block (record written before BL-10, or in a session without fingerprint). */
bool app_installed_fingerprint(uint8_t fingerprint[BL_FINGERPRINT_LEN]);
/* What the full image check of a start computed (BL-38): the image size of the record and the CRC-32 computed over
 * that many bytes of flash, carried from bl_init() to the jump instead of a bare bool. */
typedef struct
{
    uint32_t size;
    uint32_t crc;
} app_image_check_t;

/* Full check, once per start: valid record, compatibility block present and APP_COMPAT_OK, an application start the
 * family accepts (hal_app_entry_plausible()) and the CRC-32 over the image equal to the record's. Fills `checked`
 * (size and computed CRC; zero without a startable record). */
bool app_image_valid(app_image_check_t* checked);
/* Check right before the jump (BL-36 SYS69-S11, BL-38): record, compatibility block and entry again from flash, and
 * the record's size and CRC field equal to what app_image_valid() computed - without a second CRC over the image.
 * Sufficient because the bootloader has no flash write path outside the programming session, which keeps it from
 * jumping (bl_main.c); an image changed by anything else is the application's ROM check (SM-14) and the flash ECC. */
bool app_image_confirm(app_image_check_t const* checked);
