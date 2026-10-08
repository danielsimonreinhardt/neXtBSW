#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum
{
    UPD_OK                   = 0x00,
    UPD_OK_ALREADY_INSTALLED = 0x01,
    /* checks before any bootloader write (record gets invalidated, old bootloader stays) */
    UPD_ERR_HEADER       = 0x10,
    UPD_ERR_SIZE         = 0x11,
    UPD_ERR_CRC          = 0x12,
    UPD_ERR_CONFIG_FIELD = 0x13, /* configuration field of the family (s32k1xx: FCF), BL-08 */
    UPD_ERR_VECTORS      = 0x14, /* start of the image (s32k1xx: vector table) */
    UPD_ERR_COMPAT       = 0x15,
    UPD_ERR_STUB         = 0x16,
    UPD_ERR_ENVIRONMENT  = 0x17,
    /* flash errors while writing */
    UPD_ERR_FLASH_STUB   = 0x20,
    UPD_ERR_FLASH_BODY   = 0x21,
    UPD_ERR_FLASH_FINAL  = 0x22,
    UPD_ERR_FLASH_RECORD = 0x23,
    UPD_ERR_VERIFY       = 0x24
} upd_result_t;

typedef enum
{
    UPD_STEP_START   = 0x01, /* detail: start state */
    UPD_STEP_CHECKED = 0x02, /* image checks passed */
    UPD_STEP_STUB    = 0x03, /* sector 0 points to the updater */
    UPD_STEP_BODY    = 0x04, /* detail: sector index 1..7 written and verified */
    UPD_STEP_FINAL   = 0x05, /* sector 0 of the new bootloader written and verified */
    UPD_STEP_RECORD  = 0x06, /* validity record invalidated */
    UPD_STEP_DONE    = 0x07, /* detail: result */
    UPD_STEP_ERROR   = 0x0E  /* detail: result */
} upd_step_t;

typedef enum
{
    UPD_START_FRESH     = 0, /* started by the old bootloader */
    UPD_START_RESUME    = 1, /* started from the stub in sector 0 after an interruption */
    UPD_START_INSTALLED = 2  /* image already in place */
} upd_start_t;

typedef struct
{
    uint8_t start;      /* upd_start_t */
    uint8_t retries;    /* additional sector attempts */
    uint32_t stubUs;    /* sector 0 unbootable while writing the stub: erase until reset vector written */
    uint32_t finalUs;   /* sector 0 unbootable while writing the new bootloader's sector 0 */
    uint32_t securedUs; /* longest time with the Flash Configuration Field erased (chip secured on reset) */
} upd_stats_t;

/* Checks header, size, CRC, the start of the image by the family (upd_family_check_image.inc; s32k1xx: Flash
 * Configuration Field and vectors) and the version of the embedded image. */
upd_result_t upd_check_blob(uint8_t const* blob, uint32_t blobLen);

/* Runs or resumes the update. environmentOk: no watchdog can reset the MCU while writing.
 * forceRewrite: write even if the image is already installed (board test). */
upd_result_t upd_run(uint8_t const* blob, uint32_t blobLen, bool environmentOk, bool forceRewrite,
                     upd_stats_t* stats);
