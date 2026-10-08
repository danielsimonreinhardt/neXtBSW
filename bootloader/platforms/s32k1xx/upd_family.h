#pragma once

/* Family part of the bootloader updater core (BL-08, bootloader/updater/src/core/bl_update.c), family s32k1xx:
 * what a bootloader image of the family has at its start and what the stub in sector 0 looks like. The core keeps
 * the write order (stub -> remaining sectors -> sector 0 -> record) and knows no image layout of its own.
 *
 * Contract of bootloader/platforms/<family>/upd_family.h (on the include path of the updater):
 *  UPD_IMAGE_HEADER_SIZE        fixed start of an image the family checks (vectors, configuration field):
 *                               smallest image size; the version string is searched behind it
 *  UPD_STUB_EARLY_PHRASES(X)    X(offset) for every phrase of sector 0 that is programmed right after its erase,
 *                               before the entry phrase at offset 0 and before everything else (empty: none)
 *  upd_family_build_stub()      sector 0 content that starts the running updater (buffer preset with 0xFF)
 *  upd_family_stub_plausible()  the stub really starts an updater at the application start
 * and upd_family_check_image.inc next to it: the image start checks inside upd_check_blob() (see there).
 * A family with another image start (Cortex-R4F: ARM branch instructions at the exception vectors, no
 * configuration field; doc/architecture/non-cortex-m-audit.md 3.9, decision E2) brings its own files.
 *
 * s32k1xx: Cortex-M0+/M4F vector table at 0, Flash Configuration Field at 0x400..0x40F. */

#include "bl_config.h"
#include "upd_hal.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define UPD_FCF_ADDR     0x00000400UL
#define UPD_FCF_SIZE     16U
#define UPD_VECTORS_SIZE 0x00000400UL /* the stub keeps this much of the updater's vector table */

#define UPD_IMAGE_HEADER_SIZE (UPD_FCF_ADDR + UPD_FCF_SIZE)

/* The FCF is programmed first so the chip is secured on reset (mass erase still enabled) only between the erase
 * of sector 0 and these two phrases. */
#define UPD_STUB_EARLY_PHRASES(X) X(UPD_FCF_ADDR) X(UPD_FCF_ADDR + BL_PHRASE_SIZE)

/* The only Flash Configuration Field the updater accepts: unsecured, mass erase enabled, backdoor key
 * disabled, no P-Flash protection, FOPT as in bootloader/platforms/s32k1xx/startup.S. The same reference is
 * in tools/canflash/canflash.py (image rule of the family s32k1xx). */
static uint8_t const fcfReference[UPD_FCF_SIZE] = {
    0xFFU, 0xFFU, 0xFFU, 0xFFU, 0xFFU, 0xFFU, 0xFFU, 0xFFU, /* backdoor comparison key */
    0xFFU, 0xFFU, 0xFFU, 0xFFU,                             /* FPROT3..0 */
    0xFEU,                                                  /* FSEC */
    0x7FU,                                                  /* FOPT */
    0xFFU, 0xFFU                                            /* FEPROT, FDPROT */
};

static inline uint32_t upd_family_le32(uint8_t const* p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* Stub: the updater's own vector table (its reset handler starts the updater again after an interruption) and the
 * reference FCF, so that the chip stays unsecured while the stub is sector 0. */
static inline void upd_family_build_stub(uint8_t* stub)
{
    (void)memcpy(stub, upd_flash_ptr(BL_APP_START), UPD_VECTORS_SIZE);
    (void)memcpy(&stub[UPD_FCF_ADDR], fcfReference, UPD_FCF_SIZE);
}

static inline bool upd_family_stub_plausible(uint8_t const* stub)
{
    uint32_t const sp    = upd_family_le32(stub);
    uint32_t const reset = upd_family_le32(&stub[4]);
    /* The stub keeps the updater's own vector table: its reset handler points into the updater code, i.e. behind
     * the vector table and inside the application area (BL_APP_END, not the record - the record may lie below the
     * application area, PLAT-16). */
    return (sp > BL_RAM_START) && (sp <= BL_RAM_END) && ((reset & 1U) != 0U)
           && ((reset & ~1UL) >= (BL_APP_START + UPD_VECTORS_SIZE)) && ((reset & ~1UL) < BL_APP_END);
}
