#pragma once

/* Interface between application and bootloader. */

#include "bl_config.h"

#include <stdint.h>

/* Marks a boot request in no-init RAM; the caller must trigger a software reset afterwards. */
static inline void bl_set_boot_request(void)
{
    uint32_t* const flag = (uint32_t*)BL_BOOT_REQUEST_ADDR;
    flag[0]              = BL_BOOT_REQUEST_MAGIC;
    flag[1]              = ~BL_BOOT_REQUEST_MAGIC;
}
