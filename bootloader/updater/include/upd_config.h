#pragma once

#include "bl_config.h"

/* Bootloader updater: an application (flashed by the bootloader) that replaces the bootloader area
 * BL_FLASH_START .. BL_APP_START (generated memory layout). Family neutral since BL-08: image start, stub and
 * configuration field of a family (s32k1xx: vector table, FCF at 0x400) are in upd_family.h of
 * bootloader/platforms/<family>. */

#define UPD_BL_AREA_SIZE (BL_APP_START - BL_FLASH_START) /* bootloader area */

/* Blob header in front of the embedded bootloader image (little endian), see canflash.py blimage */
#define UPD_BLOB_MAGIC       0x50554C42UL /* "BLUP" */
#define UPD_BLOB_HEADER_SIZE 16U

/* Accepted prefixes of the embedded image's version string, the major version must match BL_VERSION_STRING:
 * the project's identification (generated bl_diag.h, COM-11/PLAT-08) and the former name of the software
 * (bl_config.h), so an image from before the rename can be installed again. */
#define UPD_VERSION_PREFIX        BL_VERSION_PREFIX " "
#define UPD_VERSION_PREFIX_FORMER BL_VERSION_PREFIX_FORMER " "

#define UPD_MAX_ATTEMPTS 3U /* erase + program + verify per sector */

/* Status frames of the updater (classic CAN, 8 bytes): see bl_update.h, canflash.py blupdate */
#define UPD_STATUS_CAN_ID 0x7EEUL
