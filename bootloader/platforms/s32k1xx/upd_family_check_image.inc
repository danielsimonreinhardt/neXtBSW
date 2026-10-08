/* Family part of upd_check_blob() (bootloader/updater/src/core/bl_update.c, BL-08), family s32k1xx: statements
 * included into the function body after the CRC check. In scope: `uint8_t const* const img` (image from the start
 * of the code flash), `uint32_t const size` (>= UPD_IMAGE_HEADER_SIZE, <= bootloader area), le32() of the core and
 * upd_family.h. They return UPD_ERR_CONFIG_FIELD or UPD_ERR_VECTORS and fall through when the start of the image is
 * what the family needs.
 *
 * Statements instead of a function: GCC orders the comparisons of an inlined function differently, and the
 * updater image of the eval board had to stay byte-identical (BL-08, PLAT-08 b). A family without this constraint
 * (Cortex-R4F, decision E2) may call a function of its own here and return its error.
 *
 * s32k1xx: reference Flash Configuration Field, Cortex-M vector table (initial stack pointer in the application
 * RAM, reset/NMI/HardFault Thumb addresses inside the image behind the FCF). */

    if (memcmp(&img[UPD_FCF_ADDR], fcfReference, UPD_FCF_SIZE) != 0)
    {
        return UPD_ERR_CONFIG_FIELD;
    }
    uint32_t const sp = le32(img);
    if ((sp <= BL_RAM_START) || (sp > BL_RAM_END))
    {
        return UPD_ERR_VECTORS;
    }
    for (uint32_t i = 1U; i <= 3U; ++i) /* reset, NMI, HardFault */
    {
        uint32_t const v = le32(&img[4U * i]);
        if (((v & 1U) == 0U) || ((v & ~1UL) < UPD_IMAGE_HEADER_SIZE) || ((v & ~1UL) >= size))
        {
            return UPD_ERR_VECTORS;
        }
    }
