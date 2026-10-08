#pragma once

#include "bl_update.h"

#include <stdbool.h>
#include <stdint.h>

/* Implemented by the target (S32K144) and by the host test build. */

uint8_t const* upd_flash_ptr(uint32_t address);
/* Only the bootloader area and the record sector may be erased or programmed. */
bool upd_flash_erase_sector(uint32_t address);
/* Programs one 8 byte phrase; the phrase must be erased. */
bool upd_flash_program_phrase(uint32_t address, uint8_t const* data);
uint32_t upd_micros(void);
void upd_report(uint8_t step, uint8_t detail, upd_stats_t const* stats);
