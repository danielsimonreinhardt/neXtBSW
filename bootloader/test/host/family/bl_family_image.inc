/* Family hook of the bootloader core for host builds of a synthetic test family without a bootloader HAL
 * (tools/nxconfig/test/mcudb/testfam, BL-08): the host tests build their application images with a Cortex-M vector
 * table (tools/canflash/test_bootloader_host.py make_app_image), so the Cortex-M rule applies. Selected by
 * tools/canflash/hostbuild.py family_sources() only when bootloader/platforms/<family> does not exist. */

#include "arch/cortex_m_app_entry.inc"
