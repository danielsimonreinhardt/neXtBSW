/* FTFC access of the updater: only the bootloader area and the record sector, never the updater itself. */

#include "bl_config.h"
#include "bl_sectors.h"
#include "mcu/mcu.h"
#include "upd_hal.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FTFC_CMD_PROGRAM_PHRASE 0x07U
#define FTFC_CMD_ERASE_SECTOR   0x09U

/* Bootloader area, or exactly the sector of the validity record (which may lie below the application, PLAT-16). */
static bool address_allowed(uint32_t address, uint32_t len)
{
    if (bl_range_inside(address, len, BL_FLASH_START, BL_APP_START - BL_FLASH_START))
    {
        return true;
    }
    bl_sector_t record;
    return bl_record_sector(&record) && bl_range_inside(address, len, record.start, record.size);
}

/* Runs from RAM with interrupts disabled: the program flash is not readable while a command executes.
 * No calls into flash (checked in the disassembly). */
__attribute__((section(".ramfunc"), long_call, noinline)) static uint8_t upd_ftfc_launch(void)
{
    IP_FTFC->FSTAT = FTFC_FSTAT_CCIF_MASK;
    while ((IP_FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)
    {
    }
    return IP_FTFC->FSTAT;
}

static bool upd_ftfc_command(uint8_t cmd, uint32_t address, uint8_t const* data)
{
    while ((IP_FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)
    {
    }
    IP_FTFC->FSTAT    = FTFC_FSTAT_ACCERR_MASK | FTFC_FSTAT_FPVIOL_MASK;
    IP_FTFC->FCCOB[3] = cmd;
    IP_FTFC->FCCOB[2] = (uint8_t)(address >> 16);
    IP_FTFC->FCCOB[1] = (uint8_t)(address >> 8);
    IP_FTFC->FCCOB[0] = (uint8_t)address;
    if (data != NULL)
    {
        for (uint8_t i = 0U; i < BL_PHRASE_SIZE; ++i)
        {
            IP_FTFC->FCCOB[4U + i] = data[i];
        }
    }
    uint32_t const primask = __get_PRIMASK();
    __disable_irq();
    uint8_t const status = upd_ftfc_launch();
    __set_PRIMASK(primask);
    return (status & (FTFC_FSTAT_ACCERR_MASK | FTFC_FSTAT_FPVIOL_MASK | FTFC_FSTAT_MGSTAT0_MASK)) == 0U;
}

uint8_t const* upd_flash_ptr(uint32_t address) { return (uint8_t const*)address; }

bool upd_flash_erase_sector(uint32_t address)
{
    bl_sector_t sector;
    return bl_sector_at(address, &sector) && (sector.start == address) && address_allowed(address, sector.size)
           && upd_ftfc_command(FTFC_CMD_ERASE_SECTOR, address, NULL);
}

bool upd_flash_program_phrase(uint32_t address, uint8_t const* data)
{
    return ((address % BL_PHRASE_SIZE) == 0U) && address_allowed(address, BL_PHRASE_SIZE)
           && upd_ftfc_command(FTFC_CMD_PROGRAM_PHRASE, address, data);
}
