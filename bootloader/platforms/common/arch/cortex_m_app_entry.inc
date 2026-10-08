/* Application start rule of an Arm Cortex-M image (ARMv6-M, ARMv7-M, ARMv8-M), shared by the families whose
 * derivatives are Cortex-M cores (BL-08). Not used by the core directly: a family's bl_family_image.inc includes it
 * when its applications start with a Cortex-M vector table. A core with another start - Cortex-R4F: ARM
 * instructions at the exception vectors, no VTOR, forwarding table of decision E2
 * (doc/architecture/non-cortex-m-audit.md 3.9) - defines hal_app_entry_plausible() in its own family file.
 *
 * Definition file, compiled once inside bootloader/src/core/app_image.c (see there). Cortex-M image start: word 0
 * initial main stack pointer, word 1 reset handler with the Thumb bit set. */

static uint32_t cortex_m_read_u32(uint32_t address)
{
    uint8_t const* p = hal_flash_ptr(address);
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

bool hal_app_entry_plausible(uint32_t start, uint32_t size)
{
    if (size < 8U)
    {
        return false;
    }
    uint32_t const sp    = cortex_m_read_u32(start);
    uint32_t const reset = cortex_m_read_u32(start + 4U);
    /* initial stack pointer in the application RAM (full descending: the end is allowed), Thumb reset handler
     * inside the image */
    return (sp > BL_RAM_START) && (sp <= BL_RAM_END) && ((reset & 1U) != 0U)
           && ((reset & ~1UL) >= start) && ((reset & ~1UL) < (start + size));
}
