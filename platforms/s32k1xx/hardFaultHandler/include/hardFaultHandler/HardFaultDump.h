// Hard fault dump reader (PLAT-20): checks the dump written by customHardFaultHandler and prints it once
// after the reset. Format: hardFaultHandler/hardFaultDumpLayout.h, location: linker script symbols.

#pragma once

#include "hardFaultHandler/hardFaultDumpLayout.h"

#include <cstddef>
#include <cstdint>

namespace hardfault
{
size_t const DUMP_WORDS = HARD_FAULT_DUMP_SIZE / 4U;

enum class DumpState : uint8_t
{
    NONE,         ///< no magic: no fault since the dump was last cleared
    CORRUPT,      ///< magic present, checksum wrong
    VALID
};

/** Sum of all words before the checksum field (modulo 2^32). */
uint32_t computeChecksum(uint32_t const* dump);

DumpState getDumpState(uint32_t const* dump);

/**
 * SYS-148: exception number the handler was entered with (xPSR bits 8..0 at handler entry: 3 hard fault, 38 the
 * watchdog's pre-reset interrupt on S32K1), 0 unless the dump is VALID.
 */
uint32_t getDumpException(uint32_t const* dump);

using LineWriter = void (*)(char const* line);

/**
 * Writes the report of a dump in state VALID or CORRUPT line by line (without line ends).
 * Nothing is written for NONE. Returns the state.
 */
DumpState formatDump(uint32_t const* dump, LineWriter writer);

/**
 * Target: the dump in the application's no-init RAM (__hard_fault_dump_start). Nothing else needs to
 * know its address.
 */
uint32_t* getDumpRegion();

/**
 * Call first after reset, before anything reads the dump. \p ramRetained is false if the reset may have
 * lost the RAM content (power-on, low-voltage): the dump is then cleared so that its content and ECC are
 * defined before the first read. The board port decides from its reset status register.
 */
void prepareDump(bool ramRetained);

/** Prints a present dump with printf and clears its magic, so it is reported once. */
DumpState reportAndClearDump();

} // namespace hardfault
