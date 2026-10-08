// NVM-07: one-time partition of the FlexNVM (see flexnvm/FlexNvmPartition.h). IRREVERSIBLE - only reachable through
// the console command of a test build, behind the confirmation word. NVM-11: the CSEc key space (FCCOB1) is the
// project's (confirmation word), no longer a fixed 0.

#include "flexnvm/FlexNvmPartition.h"

#include "flexnvm/FlexNvmChip.h"
#include "mcu/mcu.h"

#include <eeprom/EepromDriver.h>

namespace platform
{
namespace
{
constexpr uint8_t FAILURE_FLAGS
    = static_cast<uint8_t>(FTFC_FSTAT_ACCERR_MASK | FTFC_FSTAT_FPVIOL_MASK | FTFC_FSTAT_MGSTAT0_MASK);

bool sameText(char const* a, char const* b)
{
    if ((a == nullptr) || (b == nullptr))
    {
        return false;
    }
    while ((*a != '\0') && (*a == *b))
    {
        ++a;
        ++b;
    }
    return *a == *b;
}

// one upper-case hex digit, as tools/nxconfig writes it
bool hexDigit(char const c, uint8_t& value)
{
    if ((c >= '0') && (c <= '9'))
    {
        value = static_cast<uint8_t>(c - '0');
        return true;
    }
    if ((c >= 'A') && (c <= 'F'))
    {
        value = static_cast<uint8_t>((c - 'A') + 10);
        return true;
    }
    return false;
}

constexpr uint8_t KEY_SIZE_MAX = 3U; // [RM13] Table 36-62: FCCOB1[1:0], 20 keys
} // namespace

bool FlexNvmPartition::parseConfirmWord(char const* const word, Codes& codes)
{
    constexpr char PREFIX[] = "PGMPART";
    if (word == nullptr)
    {
        return false;
    }
    char const* w = word;
    for (char const* p = PREFIX; *p != '\0'; ++p, ++w)
    {
        if (*w != *p)
        {
            return false;
        }
    }
    uint8_t field[5] = {};
    uint8_t count    = 0U;
    while ((*w == '-') && (count < 5U))
    {
        if (!hexDigit(w[1], field[count]))
        {
            return false;
        }
        ++count;
        w += 2;
    }
    if (*w != '\0')
    {
        return false;
    }
    if (count == 5U)
    {
        codes = Codes{true, field[0], field[1], field[2], field[3], field[4]};
        return true;
    }
    if (count == 3U)
    {
        codes = Codes{false, 0U, 0U, field[2], field[0], field[1]};
        return true;
    }
    return false;
}

char const* FlexNvmPartition::resultText(Result const result)
{
    switch (result)
    {
        case Result::DONE: return "partition written, emulated EEPROM ready - reset the ECU";
        case Result::DONE_RESET_NEEDED: return "partition written, codes show after the next reset - reset the ECU";
        case Result::WRONG_CONFIRMATION: return "wrong confirmation word, nothing written";
        case Result::ALREADY_PARTITIONED: return "already partitioned, nothing written (only a mass erase undoes it)";
        case Result::BUSY: return "a flash command is running, nothing written";
        case Result::INVALID_PLAN: return "invalid partition values, nothing written";
        case Result::COMMAND_FAILED:
            return "the flash module refused the command (ACCERR/FPVIOL/MGSTAT0); ACCERR on a chip shown as not "
                   "partitioned: IFR EEESIZE byte not erased, only a mass erase clears it";
        case Result::KEY_SPACE_NOT_NAMED:
            return "the project names no CSEc key space (nvm.partition.key_slots), nothing written - the key space is "
                   "fixed with the partition";
        case Result::NO_CSEC: return "key space asked for, but this part has no CSEc (SIM SDID), nothing written";
        default: return "?";
    }
}

uint8_t FlexNvmPartition::departCode() { return FlexNvmChip::departCode(); }

uint8_t FlexNvmPartition::eeeSizeCode() { return FlexNvmChip::eeeRamSizeCode(); }

// NVM-10: not "DEPART and EEERAMSIZE 0xF" - an S32K144 as delivered shows EEERAMSIZE 0x2 (flexnvm/FlexNvmChip.h)
bool FlexNvmPartition::isErased() { return FlexNvmChip::partition() == FlexNvmChip::Partition::NOT_PARTITIONED; }

FlexNvmPartition::Result FlexNvmPartition::partition(Plan const& plan, char const* const confirmation)
{
    if ((plan.confirmWord == nullptr) || !sameText(confirmation, plan.confirmWord))
    {
        return Result::WRONG_CONFIRMATION;
    }
    if ((plan.eeeSizeCode > 0xFU) || (plan.partitionCode > 0xFU) || (plan.loadCode > 1U)
        || ((plan.eepromBytes != 0U) && (plan.window == 0U)))
    {
        return Result::INVALID_PLAN;
    }
    // NVM-11: FCCOB1/2 come from the confirmed word; the codes it repeats must be the plan's
    Codes codes{};
    if (!parseConfirmWord(plan.confirmWord, codes) || (codes.loadCode != plan.loadCode)
        || (codes.eeeSizeCode != plan.eeeSizeCode) || (codes.partitionCode != plan.partitionCode))
    {
        return Result::INVALID_PLAN;
    }
    if (!codes.keyNamed)
    {
        return Result::KEY_SPACE_NOT_NAMED;
    }
    // SFE: not through OpenBSW (see header); key space needs the FlexRAM loaded during reset ([RM13] Table 36-69)
    if ((codes.keySize > KEY_SIZE_MAX) || (codes.sfe != 0U) || ((codes.keySize != 0U) && (codes.loadCode != 0U)))
    {
        return Result::INVALID_PLAN;
    }
    if ((codes.keySize != 0U) && !FlexNvmChip::deviceId().security)
    {
        return Result::NO_CSEC;
    }
    if (!isErased())
    {
        return Result::ALREADY_PARTITIONED;
    }
    if ((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)
    {
        return Result::BUSY;
    }
    // OpenBSW writes fLoadOnReset unchanged into FCCOB3, where 0 means "loaded during reset": pass the command byte
    ::eeprom::EepromConfiguration const configuration{
        static_cast<uint32_t>(plan.window),
        plan.eepromBytes,
        codes.keySize, // NVM-11: FCCOB1 = CSEc key size of the project (bits 1..0), FCCOB2 = 0 (SFE off, bit 2 clear)
        plan.loadCode != 0U,
        plan.eeeSizeCode,
        plan.partitionCode};
    ::eeprom::EepromDriver driver(configuration);
    ::bsp::BspReturnCode const result = driver.init();
    // FSTAT still holds the outcome of the last command (PGMPART, or SETRAM after it)
    if ((FTFC->FSTAT & FAILURE_FLAGS) != 0U)
    {
        return Result::COMMAND_FAILED;
    }
    // OpenBSW's init() compares SIM FCFG1 with the codes right after PGMPART; the reference manual loads SIM FCFG1
    // from the IFR during reset, so a successful command may still read as "not initialized" until the reset
    return (result == ::bsp::BSP_OK) ? Result::DONE : Result::DONE_RESET_NEEDED;
}

} // namespace platform
