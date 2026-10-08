// NVM-07: the one-time partition of the FlexNVM of the S32K1 family (FTFC command Program Partition, PGMPART).
//
// IRREVERSIBLE: the partition codes are programmed into the data flash IFR; only a mass erase of the chip (Erase All
// Blocks, e.g. J-Link "erase") undoes it, and an interrupted command can leave the codes indeterminate ([RM13]
// 36.5.11.14). NVM-11: with a CSEc key space (FCCOB1 != 0) even the mass erase may be refused: [RM13] 36.5.11.14 (note
// "once Flash Keys are allocated (regardless of being initialized or not)") and the error tables of Erase All Blocks
// (Unsecure) (Tables 36-56/36-60: "key allocation being non-zero keys") name the allocation, the note of 36.5.11.11
// names the presence of keys. Until the board shows which holds, a key space counts as undone only by
// DBG_CHAL/DBG_AUTH (MASTER_ECU_KEY). No key ever gets WRITE_PROTECTION. Therefore:
//  - nothing calls this at start-up; the only caller is the console command "flexnvm partition" of a test build
//    (CMake option NX_FLEXNVM_PARTITION_COMMAND), used with the user at the board;
//  - the caller must hand in the confirmation word the command showed before. NVM-11: it carries the command bytes
//    FCCOB1..FCCOB5 in command order, "PGMPART-<key size>-<SFE>-<load byte>-<size code>-<partition code>" (one hex
//    digit each, NX_NVM_PARTITION_CONFIRM, derived by tools/nxconfig from nvm.partition). Key size and SFE are taken
//    from this word - Plan has no field for them yet (generator and port are outside NVM-11); the other three must
//    equal the plan. The old word "PGMPART-<size code>-<partition code>-<load byte>" (a project without
//    nvm.partition.key_slots on a derivative with a key space) is refused: it would fix the key space at 0 for good;
//  - the chip must be as delivered and no flash command may run. NVM-10: "as delivered" is SIM FCFG1 DEPART 0xF with
//    the FlexRAM as RAM (FCNFG RAMRDY 1, EEERDY 0), see flexnvm/FlexNvmChip.h; SIM FCFG1 EEERAMSIZE is no IFR code
//    (S32K144 as delivered: 0x2). The IFR EEESIZE byte cannot be read on the S32K1 (no Read Resource command);
//    PGMPART checks it itself and refuses with ACCERR before erasing anything.
//
// The command itself is OpenBSW's EepromDriver::init() (platforms/s32k1xx/bsp/bspEepromDriver, launched from RAM with
// interrupts suspended): PGMPART with FCCOB1 = key size code, FCCOB2 = 0 (SFE off), FCCOB3 = load byte, FCCOB4 =
// EEPROM size code, FCCOB5 = FlexNVM partition code, then SETRAM 0x00 (FlexRAM as EEPROM). OpenBSW takes FCCOB1 from
// EepromConfiguration::fProtectedAreaSize bits 1..0 and FCCOB2 from its bit 2 as the value 0x04 - the chip reads
// FCCOB2 bit 0 only ([RM13] 36.5.11.14), so SFE cannot be set through OpenBSW; SFE is refused here (NVM-11).
// Note the naming of OpenBSW:
// EepromConfiguration::fLoadOnReset is written unchanged into FCCOB3, where 0 means "FlexRAM LOADED during reset"
// ([RM13] Table 36-61) - so "load on reset" is fLoadOnReset = false. partition() takes the command byte (loadCode)
// and never a bool, so the inversion is in exactly one place (FlexNvmPartition.cpp).

#pragma once

#include <bsp/Bsp.h>

#include <cstdint>

namespace platform
{

class FlexNvmPartition
{
public:
    /** The partition the project wants (tools/bswgen nvm/nvmPartition.h). */
    struct Plan
    {
        uint8_t eeeSizeCode;    // FCCOB4[3:0], NX_NVM_PARTITION_EEPROM_CODE
        uint8_t partitionCode;  // FCCOB5[3:0] (DEPART), NX_NVM_PARTITION_CODE
        uint8_t loadCode;       // FCCOB3: 0 = FlexRAM loaded during reset, 1 = not; NX_NVM_PARTITION_LOAD_CODE
        uint32_t eepromBytes;   // NX_NVM_PARTITION_EEPROM_BYTES
        uintptr_t window;       // FlexRAM window (rte::NVM_WINDOW_START or the derivative's eeprom_ram region)
        char const* confirmWord; // NX_NVM_PARTITION_CONFIRM; NVM-11: also carries key size (FCCOB1) and SFE (FCCOB2)
    };

    enum class Result : uint8_t
    {
        DONE,                // PGMPART and SETRAM done, the IFR shows the codes, the emulated EEPROM is ready
        DONE_RESET_NEEDED,   // PGMPART accepted without error, the codes show after the next reset
        WRONG_CONFIRMATION,  // nothing launched
        ALREADY_PARTITIONED, // nothing launched: not NOT_PARTITIONED (only a mass erase makes it partitionable again)
        BUSY,                // nothing launched: a flash command is running (FSTAT CCIF clear)
        INVALID_PLAN,        // nothing launched: codes out of range, EEPROM size without window, load byte not 0/1
        COMMAND_FAILED,      // the module reported ACCERR, FPVIOL or MGSTAT0
        KEY_SPACE_NOT_NAMED, // nothing launched: the word names no key space (NVM-11, nvm.partition.key_slots)
        NO_CSEC,             // nothing launched: key space asked for, but SIM SDID says the part has no CSEc
    };

    /** NVM-11: the command bytes FCCOB1..FCCOB5 a confirmation word carries. */
    struct Codes
    {
        bool keyNamed;         // false: old word without key space and SFE
        uint8_t keySize;       // FCCOB1 (0 unless keyNamed)
        uint8_t sfe;           // FCCOB2 (0 unless keyNamed)
        uint8_t loadCode;      // FCCOB3
        uint8_t eeeSizeCode;   // FCCOB4
        uint8_t partitionCode; // FCCOB5
    };

    /**
     * Reads \p word: "PGMPART-K-S-L-E-D" (keyNamed) or the old "PGMPART-E-D-L", one upper-case hex digit per field.
     * False if it is neither.
     */
    static bool parseConfirmWord(char const* word, Codes& codes);

    static char const* resultText(Result result);

    /** Codes as they are today (SIM FCFG1): DEPART 0xF = not partitioned; EEERAMSIZE for information only. */
    static uint8_t departCode();
    static uint8_t eeeSizeCode();

    /**
     * True if the chip is not partitioned (as delivered or after a mass erase): DEPART erased and the FlexRAM plain
     * RAM (FlexNvmChip::Partition::NOT_PARTITIONED).
     */
    static bool isErased();

    /**
     * Writes the partition \p plan if \p confirmation equals plan.confirmWord and the chip is erased. IRREVERSIBLE.
     * Blocks for the duration of the command (data sheet tpgmpart: about 70 ms for 32 KB backup) with interrupts
     * suspended.
     */
    static Result partition(Plan const& plan, char const* confirmation);
};

} // namespace platform
