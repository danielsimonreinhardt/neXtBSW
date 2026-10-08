// Host test of the one-time FlexNVM partition (NVM-07, include/flexnvm/FlexNvmPartition.h, src/FlexNvmPartition.cpp)
// together with OpenBSW's unchanged EepromDriver.cpp (external/openbsw/platforms/s32k1xx/bsp/bspEepromDriver), against
// a model of the flash module: FSTAT (write 1 to clear, CCIF launches), FCCOB, the data flash IFR with the partition
// codes and SIM FCFG1. The model follows [RM13] 36.5.11.14/36.5.11.15 for what the test needs: PGMPART only on an erased
// IFR and with codes of the S32K144 tables (Table 36-65/36-67), otherwise ACCERR; SETRAM 0x00 makes the FlexRAM the
// EEPROM (EEERDY). Whether SIM FCFG1 shows new codes before the next reset is a switch of the model (the reference
// manual says it is loaded during reset; not measured at the board).
//
// Checked: nothing is launched with a wrong or missing confirmation, on a partitioned chip, while a command runs or
// with invalid values; the FCCOB bytes of PGMPART (FCCOB0 0x80, FCCOB1/2 0, FCCOB3 load byte, FCCOB4 EEESIZE, FCCOB5
// DEPART) at the register offsets of the S32K1 (FCCOB0 at FCCOB[3], AN11983 3.1.1.2) for the decision nvm01-eeesize
// (EEESIZE 0010b, DEPART 1011b, FCCOB3 = 0: loaded during reset) and for the opposite load byte; SETRAM afterwards;
// cache off and interrupts suspended during the command and restored after; a refused command reported; the result
// with and without the codes visible before the reset.
// NVM-10 (flexnvm/FlexNvmChip.h): the mixed state of the board 2026-09-22 (SIM DEPART 0xF with EEERAMSIZE 0x2, the
// model switch simEeeRamSize) counts as a chip as delivered and takes PGMPART; FCNFG RAMRDY/EEERDY as the model sets
// them ([RM13] 36.4.4.1.3); DEPART erased with FlexRAM flags that do not fit is refused; an IFR EEESIZE byte that is
// programmed although DEPART is erased is refused by the module (ACCERR) and reported; SIM SDID part name and CSEc.
// NVM-11: FCCOB1 (CSEc key size) and FCCOB2 (SFE) from the confirmation word "PGMPART-K-S-L-E-D"; the decision of
// 2026-09-23/28 (FCCOB1..5 = 03 00 00 02 0B) reaches the module; refused before launch: the old word without key
// space, key size > 3, SFE, key space without load on reset, codes of the word other than the plan's, key space on a
// part without CSEc. The model refuses key space like [RM13] Table 36-69.
// Build and run: platforms/s32k1xx/bsp/bspFlexNvm/test/test_flexnvm.py.

#include "flexnvm/FlexNvmPartition.h"

#include "flexnvm/FlexNvmChip.h"

#include "cache/cache.h"
#include "mcu/mcu.h"

#include <cstdio>
#include <cstring>

FakeFtfc fakeFtfc;
FakeSim fakeSim;
FakeCore fakeCore;

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

using platform::FlexNvmChip;
using platform::FlexNvmPartition;
using Result = FlexNvmPartition::Result;

// ------------------------------------------------------------------------------------------ flash module model
struct Command
{
    uint8_t fccob[FTFC_FCCOB_COUNT];
    bool cacheOn;
    bool interruptsOn;
};

struct Module
{
    uint8_t ifrEeeSize    = 0xFFU; // data flash IFR 0x03FE (EEERST, EEESIZE), erased
    uint8_t ifrDepart     = 0xFFU; // data flash IFR 0x03FC (DEPART), erased
    bool fcfg1Immediately = true;  // SIM FCFG1 shows the new codes before the next reset
    uint8_t simEeeRamSize = 0xFFU; // SIM FCFG1 EEERAMSIZE: 0xFF = from the IFR, else this code (board: always 0x2)
    bool refuseAll        = false; // e.g. command not available in the current mode
    uint8_t keySize       = 0xFFU; // NVM-11: CSEc key size the last accepted PGMPART programmed (0xFF: none)
    Command commands[4]   = {};
    unsigned count        = 0U;
} module;

// FCCOBn of the reference manual at its register offset (FCCOB0 = FCCOB[3], ..., FCCOB4 = FCCOB[7])
uint8_t fccob(Command const& c, unsigned const n) { return c.fccob[(n & ~3U) + (3U - (n & 3U))]; }

bool validEeeSize(uint8_t const code) { return (code == 0x2U) || (code == 0xFU); }

bool validDepart(uint8_t const code)
{
    return (code == 0x0U) || (code == 0x3U) || (code == 0x4U) || (code == 0x8U) || (code == 0xAU) || (code == 0xBU)
           || (code == 0xCU);
}

void updateFcfg1()
{
    fakeSim.FCFG1 = (fakeSim.FCFG1 & ~(SIM_FCFG1_DEPART_MASK | SIM_FCFG1_EEERAMSIZE_MASK))
                    | (static_cast<uint32_t>(module.ifrDepart & 0xFU) << SIM_FCFG1_DEPART_SHIFT)
                    | (static_cast<uint32_t>(((module.simEeeRamSize == 0xFFU) ? module.ifrEeeSize : module.simEeeRamSize)
                                             & 0xFU)
                       << SIM_FCFG1_EEERAMSIZE_SHIFT);
}

void launch(FakeReg8& fstat, uint8_t const written)
{
    if ((written & FTFC_FSTAT_CCIF_MASK) == 0U)
    {
        return;
    }
    Command& c = module.commands[(module.count < 4U) ? module.count : 3U];
    for (unsigned i = 0U; i < FTFC_FCCOB_COUNT; ++i)
    {
        c.fccob[i] = fakeFtfc.FCCOB[i];
    }
    c.cacheOn      = fakeCore.cacheOn;
    c.interruptsOn = fakeCore.interruptsOn;
    ++module.count;
    uint8_t status = FTFC_FSTAT_CCIF_MASK;
    uint8_t const cmd = fccob(c, 0U);
    if (module.refuseAll)
    {
        status |= FTFC_FSTAT_ACCERR_MASK;
    }
    else if (cmd == 0x80U)
    {
        uint8_t const eee = fccob(c, 4U), depart = fccob(c, 5U), load = fccob(c, 3U);
        bool const erased = (module.ifrEeeSize == 0xFFU) && (module.ifrDepart == 0xFFU);
        bool const noEeprom = (depart == 0x0U) || (depart == 0xCU);
        // NVM-11: key size rules of [RM13] Table 36-69 (CSEc from SIM SDID FEATURES bit 7)
        uint8_t const keys = fccob(c, 1U);
        bool const csec    = (fakeSim.SDID & 0x80U) != 0U;
        bool const badKeys = (keys > 3U) || ((keys != 0U) && (!csec || noEeprom || ((load & 1U) != 0U)
                                                              || ((fccob(c, 2U) & 1U) != 0U)));
        if (!erased || !validEeeSize(eee) || !validDepart(depart) || badKeys || (noEeprom != (eee == 0xFU)))
        {
            status |= FTFC_FSTAT_ACCERR_MASK;
        }
        else
        {
            // IFR: bit 7 and 5..4 reserved as 1, EEERST = 1 when loaded during reset (FCCOB3 bit 0 = 0)
            module.ifrEeeSize = static_cast<uint8_t>(0xB0U | (((load & 1U) == 0U) ? 0x40U : 0U) | eee);
            module.ifrDepart  = static_cast<uint8_t>(0xF0U | depart);
            module.keySize    = keys;
            if (!noEeprom) // [RM13] FCNFG RAMRDY: cleared by a PGMPART with emulated EEPROM
            {
                fakeFtfc.FCNFG.value = static_cast<uint8_t>(fakeFtfc.FCNFG.value & ~FTFC_FCNFG_RAMRDY_MASK);
            }
            if (module.fcfg1Immediately)
            {
                updateFcfg1();
            }
        }
    }
    else if (cmd == 0x81U)
    {
        if ((fccob(c, 1U) == 0x00U) && (module.ifrEeeSize != 0xFFU))
        {
            fakeFtfc.FCNFG.value = static_cast<uint8_t>(
                (fakeFtfc.FCNFG.value | FTFC_FCNFG_EEERDY_MASK) & ~FTFC_FCNFG_RAMRDY_MASK);
        }
        else
        {
            status |= FTFC_FSTAT_ACCERR_MASK;
        }
    }
    else
    {
        status |= FTFC_FSTAT_ACCERR_MASK;
    }
    fstat.value = static_cast<uint8_t>(fstat.value | status);
}

void reset()
{
    fakeFtfc              = FakeFtfc();
    fakeFtfc.FSTAT.value  = FTFC_FSTAT_CCIF_MASK; // idle
    fakeFtfc.FSTAT.onWrite = &launch;
    fakeFtfc.FCNFG.value  = FTFC_FCNFG_RAMRDY_MASK; // not partitioned: FlexRAM is RAM ([RM13] FCNFG RAMRDY)
    fakeSim.FCFG1         = 0xFFFFFFFFU;
    fakeSim.SDID          = 0U;
    fakeCore              = FakeCore();
    module                = Module();
}

// decision nvm01-eeesize (S32K144): 4 KB EEPROM, 32 KB data flash, DEPART 1011b, loaded during reset; no key space
// (NVM-11 word: FCCOB1..5 = key size 0, SFE 0, load 0, EEESIZE 2, DEPART B)
FlexNvmPartition::Plan const DECISION{0x2U, 0xBU, 0U, 4096U, 0x14000000U, "PGMPART-0-0-0-2-B"};
// NVM-11 decision of 2026-09-23/28: FCCOB1 0x03 (20 key slots), FCCOB2 0x00, FCCOB3 0x00, FCCOB4 0x02, FCCOB5 0x0B
FlexNvmPartition::Plan const CSEC{0x2U, 0xBU, 0U, 4096U, 0x14000000U, "PGMPART-3-0-0-2-B"};

void testNothingWithoutConfirmation()
{
    reset();
    CHECK(FlexNvmPartition::isErased());
    CHECK(FlexNvmPartition::partition(DECISION, nullptr) == Result::WRONG_CONFIRMATION);
    CHECK(FlexNvmPartition::partition(DECISION, "") == Result::WRONG_CONFIRMATION);
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-2-B") == Result::WRONG_CONFIRMATION);
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-2-B-00") == Result::WRONG_CONFIRMATION);
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-2-3-0") == Result::WRONG_CONFIRMATION);
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-2-B-0") == Result::WRONG_CONFIRMATION); // old word
    FlexNvmPartition::Plan noWord = DECISION;
    noWord.confirmWord            = nullptr;
    CHECK(FlexNvmPartition::partition(noWord, "PGMPART-0-0-0-2-B") == Result::WRONG_CONFIRMATION);
    CHECK(module.count == 0U);
    CHECK(fakeFtfc.FSTAT.writes == 0U);
    for (unsigned i = 0U; i < FTFC_FCCOB_COUNT; ++i)
    {
        CHECK(fakeFtfc.FCCOB[i] == 0U);
    }
}

void testNothingOnAPartitionedOrBusyChip()
{
    reset();
    module.ifrDepart  = 0xF3U; // partitioned before (e.g. 0011b)
    module.ifrEeeSize = 0xF2U;
    updateFcfg1();
    CHECK(!FlexNvmPartition::isErased());
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::ALREADY_PARTITIONED);
    // same codes as wanted: still nothing (no second PGMPART, no SETRAM)
    module.ifrDepart = 0xFBU;
    updateFcfg1();
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::ALREADY_PARTITIONED);
    // DEPART erased with a reserved EEERAMSIZE code (0x0) is no chip as delivered either
    fakeSim.FCFG1 = 0xFFFFFFFFU & ~SIM_FCFG1_EEERAMSIZE_MASK; // DEPART 0xF, EEERAMSIZE 0x0
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::ALREADY_PARTITIONED);
    reset();
    fakeFtfc.FSTAT.value = 0U; // CCIF clear: a command is running
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::BUSY);
    CHECK(module.count == 0U);
    CHECK(fakeFtfc.FSTAT.writes == 0U);
}

void testInvalidPlan()
{
    reset();
    FlexNvmPartition::Plan bad = DECISION;
    bad.loadCode               = 2U;
    CHECK(FlexNvmPartition::partition(bad, "PGMPART-0-0-0-2-B") == Result::INVALID_PLAN);
    bad.confirmWord = "PGMPART-0-0-2-2-B"; // word and plan agree on the load byte 2: still invalid
    CHECK(FlexNvmPartition::partition(bad, "PGMPART-0-0-2-2-B") == Result::INVALID_PLAN);
    bad          = DECISION;
    bad.window   = 0U;
    CHECK(FlexNvmPartition::partition(bad, "PGMPART-0-0-0-2-B") == Result::INVALID_PLAN);
    bad               = DECISION;
    bad.partitionCode = 0x1BU;
    CHECK(FlexNvmPartition::partition(bad, "PGMPART-0-0-0-2-B") == Result::INVALID_PLAN);
    CHECK(module.count == 0U);
}

void testDecision(bool const fcfg1Immediately)
{
    reset();
    module.fcfg1Immediately = fcfg1Immediately;
    Result const r          = FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B");
    CHECK(r == (fcfg1Immediately ? Result::DONE : Result::DONE_RESET_NEEDED));
    CHECK(module.count == (fcfg1Immediately ? 2U : 1U)); // SETRAM only when OpenBSW sees the codes
    Command const& pgmpart = module.commands[0];
    CHECK(fccob(pgmpart, 0U) == 0x80U); // PGMPART
    CHECK(fccob(pgmpart, 1U) == 0x00U); // no CSEc keys
    CHECK(fccob(pgmpart, 2U) == 0x00U); // SFE off
    CHECK(fccob(pgmpart, 3U) == 0x00U); // FlexRAM LOADED during reset (0!)
    CHECK(fccob(pgmpart, 4U) == 0x02U); // EEESIZE 0010b: 4 KB
    CHECK(fccob(pgmpart, 5U) == 0x0BU); // DEPART 1011b: 32 KB data flash, 32 KB backup
    // register offsets of AN11983 3.1.1.2: FCCOB[3] = FCCOB0, FCCOB[0] = FCCOB3, FCCOB[7] = FCCOB4, FCCOB[6] = FCCOB5
    CHECK((pgmpart.fccob[3] == 0x80U) && (pgmpart.fccob[0] == 0x00U) && (pgmpart.fccob[7] == 0x02U)
          && (pgmpart.fccob[6] == 0x0BU));
    CHECK(!pgmpart.cacheOn && !pgmpart.interruptsOn);
    CHECK(fakeCore.cacheOn && fakeCore.interruptsOn); // restored
    CHECK(module.ifrDepart == 0xFBU);
    CHECK(module.ifrEeeSize == 0xF2U); // EEERST = 1: loaded during reset
    if (fcfg1Immediately)
    {
        Command const& setram = module.commands[1];
        CHECK(fccob(setram, 0U) == 0x81U);
        CHECK(fccob(setram, 1U) == 0x00U); // FlexRAM as emulated EEPROM
        CHECK((fakeFtfc.FCNFG.value & FTFC_FCNFG_EEERDY_MASK) != 0U);
    }
    // a second call is refused: the IFR is no longer erased (after the reset at the latest)
    updateFcfg1();
    unsigned const before = module.count;
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::ALREADY_PARTITIONED);
    CHECK(module.count == before);
}

void testLoadByteIsHandedThrough()
{
    reset();
    FlexNvmPartition::Plan plan = DECISION;
    plan.loadCode               = 1U;
    plan.confirmWord            = "PGMPART-0-0-1-2-B";
    CHECK(FlexNvmPartition::partition(plan, "PGMPART-0-0-1-2-B") == Result::DONE);
    CHECK(fccob(module.commands[0], 3U) == 0x01U);
    CHECK(module.ifrEeeSize == 0xB2U); // EEERST = 0: not loaded during reset
}

void testRefusedCommand()
{
    reset();
    module.refuseAll = true;
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::COMMAND_FAILED);
    CHECK(fakeCore.cacheOn && fakeCore.interruptsOn);
    reset();
    FlexNvmPartition::Plan bad = DECISION;
    bad.partitionCode          = 0x5U; // reserved code: the module refuses it
    bad.confirmWord            = "PGMPART-0-0-0-2-5";
    CHECK(FlexNvmPartition::partition(bad, "PGMPART-0-0-0-2-5") == Result::COMMAND_FAILED);
    CHECK(module.ifrDepart == 0xFFU);
}

// NVM-10: board 2026-09-22 (capture 93): DEPART 0xF with EEERAMSIZE 0x2, EEERDY 0 on an S32K144 as delivered
void testMixedStateOfTheBoard()
{
    reset();
    module.simEeeRamSize = 0x2U; // SIM shows the FlexRAM size, not the IFR EEESIZE byte
    updateFcfg1();
    CHECK(FlexNvmChip::departCode() == 0xFU);
    CHECK(FlexNvmChip::eeeRamSizeCode() == 0x2U);
    CHECK(FlexNvmChip::partition() == FlexNvmChip::Partition::NOT_PARTITIONED);
    CHECK(std::strncmp(FlexNvmChip::partitionText(FlexNvmChip::partition()), "not partitioned", 15U) == 0);
    CHECK(FlexNvmPartition::isErased());
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::DONE);
    CHECK(module.count == 2U);
    CHECK((fccob(module.commands[0], 0U) == 0x80U) && (fccob(module.commands[0], 4U) == 0x02U)
          && (fccob(module.commands[0], 5U) == 0x0BU));
    CHECK(module.ifrDepart == 0xFBU);
    CHECK(FlexNvmChip::partition() == FlexNvmChip::Partition::EEPROM_READY);
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::ALREADY_PARTITIONED);
    CHECK(module.count == 2U);
}

// the IFR EEESIZE byte programmed but DEPART erased (e.g. an interrupted PGMPART): software cannot see it, the module
// refuses PGMPART with ACCERR and nothing changes
void testIfrEeeSizeByteProgrammed()
{
    reset();
    module.simEeeRamSize = 0x2U;
    module.ifrEeeSize    = 0xF2U;
    updateFcfg1();
    CHECK(FlexNvmPartition::isErased());
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::COMMAND_FAILED);
    CHECK(module.count == 1U); // no SETRAM after the refused PGMPART
    CHECK(module.ifrDepart == 0xFFU);
    CHECK(fakeCore.cacheOn && fakeCore.interruptsOn);
    CHECK(std::strstr(FlexNvmPartition::resultText(Result::COMMAND_FAILED), "mass erase") != nullptr);
}

// DEPART erased, but the FlexRAM flags do not fit a chip as delivered: refused, nothing launched
void testInconsistentFlexRam()
{
    reset();
    module.simEeeRamSize = 0x2U;
    updateFcfg1();
    fakeFtfc.FCNFG.value = 0U; // RAMRDY 0
    CHECK(FlexNvmChip::partition() == FlexNvmChip::Partition::INCONSISTENT);
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::ALREADY_PARTITIONED);
    fakeFtfc.FCNFG.value = static_cast<uint8_t>(FTFC_FCNFG_RAMRDY_MASK | FTFC_FCNFG_EEERDY_MASK);
    CHECK(FlexNvmChip::partition() == FlexNvmChip::Partition::INCONSISTENT);
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::ALREADY_PARTITIONED);
    fakeFtfc.FCNFG.value = FTFC_FCNFG_EEERDY_MASK;
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::ALREADY_PARTITIONED);
    CHECK(module.count == 0U);
    CHECK(std::strstr(FlexNvmChip::partitionText(FlexNvmChip::Partition::INCONSISTENT), "do not partition") != nullptr);
}

void testPartitionStates()
{
    reset();
    fakeSim.FCFG1 = 0xFFFFFFFFU & ~SIM_FCFG1_DEPART_MASK & ~SIM_FCFG1_EEERAMSIZE_MASK;
    fakeSim.FCFG1 |= (0xBU << SIM_FCFG1_DEPART_SHIFT) | (0x2U << SIM_FCFG1_EEERAMSIZE_SHIFT);
    fakeFtfc.FCNFG.value = FTFC_FCNFG_EEERDY_MASK;
    CHECK(FlexNvmChip::partition() == FlexNvmChip::Partition::EEPROM_READY);
    fakeFtfc.FCNFG.value = FTFC_FCNFG_RAMRDY_MASK;
    CHECK(FlexNvmChip::partition() == FlexNvmChip::Partition::FLEXRAM_AS_RAM);
    fakeFtfc.FCNFG.value = 0U;
    CHECK(FlexNvmChip::partition() == FlexNvmChip::Partition::EEPROM_NOT_LOADED);
    // every state reads as "partitioned" except the chip as delivered
    CHECK(std::strncmp(FlexNvmChip::partitionText(FlexNvmChip::Partition::EEPROM_READY), "partitioned", 11U) == 0);
    CHECK(std::strncmp(FlexNvmChip::partitionText(FlexNvmChip::Partition::FLEXRAM_AS_RAM), "partitioned", 11U) == 0);
    CHECK(std::strncmp(FlexNvmChip::partitionText(FlexNvmChip::Partition::EEPROM_NOT_LOADED), "partitioned", 11U)
          == 0);
    // EEERAMSIZE codes of the SIM table
    CHECK(FlexNvmChip::definedEeeRamSizeCode(0x2U) && FlexNvmChip::definedEeeRamSizeCode(0x9U)
          && FlexNvmChip::definedEeeRamSizeCode(0xFU));
    CHECK(!FlexNvmChip::definedEeeRamSizeCode(0x0U) && !FlexNvmChip::definedEeeRamSizeCode(0x1U)
          && !FlexNvmChip::definedEeeRamSizeCode(0xAU) && !FlexNvmChip::definedEeeRamSizeCode(0xEU));
}

uint32_t sdid(unsigned sub, unsigned der, unsigned ram, unsigned rev, unsigned pkg, unsigned features)
{
    return (1UL << 28U) | (static_cast<uint32_t>(sub) << 24U) | (static_cast<uint32_t>(der) << 20U)
           | (static_cast<uint32_t>(ram) << 16U) | (static_cast<uint32_t>(rev) << 12U)
           | (static_cast<uint32_t>(pkg) << 8U) | static_cast<uint32_t>(features);
}

void testDeviceId()
{
    reset();
    fakeSim.SDID               = sdid(4U, 4U, 0xBU, 1U, 0x4U, 0x80U); // S32K144 rev 1, 100 LQFP, CSEc
    FlexNvmChip::DeviceId id   = FlexNvmChip::deviceId();
    CHECK(id.raw == fakeSim.SDID);
    CHECK((id.subseries == 4U) && (id.derivate == 4U) && (id.ramSizeCode == 0xBU) && (id.revision == 1U)
          && (id.package == 0x4U) && id.security);
    CHECK(std::strcmp(FlexNvmChip::deviceName(id), "S32K144") == 0);
    CHECK(std::strcmp(FlexNvmChip::packageText(id.package), "100 LQFP") == 0);
    fakeSim.SDID = sdid(4U, 4U, 0xBU, 1U, 0x3U, 0x7FU); // no CSEc: only bit 7 counts
    id           = FlexNvmChip::deviceId();
    CHECK(!id.security && (id.package == 0x3U));
    fakeSim.SDID = sdid(4U, 5U, 0U, 0U, 0x2U, 0U);
    CHECK(std::strcmp(FlexNvmChip::deviceName(FlexNvmChip::deviceId()), "S32K144W") == 0);
    fakeSim.SDID = sdid(4U, 3U, 0U, 0U, 0x2U, 0U);
    CHECK(std::strcmp(FlexNvmChip::deviceName(FlexNvmChip::deviceId()), "S32K142W") == 0);
    fakeSim.SDID = sdid(4U, 8U, 0U, 0U, 0x7U, 0U);
    CHECK(std::strcmp(FlexNvmChip::deviceName(FlexNvmChip::deviceId()), "S32K148") == 0);
    fakeSim.SDID = sdid(1U, 6U, 0U, 0U, 0x2U, 0U);
    CHECK(std::strcmp(FlexNvmChip::deviceName(FlexNvmChip::deviceId()), "S32K116") == 0);
    fakeSim.SDID = sdid(1U, 4U, 0U, 0U, 0x2U, 0U);
    CHECK(std::strcmp(FlexNvmChip::deviceName(FlexNvmChip::deviceId()), "S32K1?") == 0);
    CHECK(std::strcmp(FlexNvmChip::packageText(0x5U), "reserved") == 0);
}

// NVM-11: the confirmation word carries FCCOB1..5; the old three-code word is read, but not written
void testConfirmWordParsing()
{
    FlexNvmPartition::Codes c{};
    CHECK(FlexNvmPartition::parseConfirmWord("PGMPART-3-0-0-2-B", c));
    CHECK(c.keyNamed && (c.keySize == 3U) && (c.sfe == 0U) && (c.loadCode == 0U) && (c.eeeSizeCode == 2U)
          && (c.partitionCode == 0xBU));
    CHECK(FlexNvmPartition::parseConfirmWord("PGMPART-2-B-0", c));
    CHECK(!c.keyNamed && (c.keySize == 0U) && (c.loadCode == 0U) && (c.eeeSizeCode == 2U) && (c.partitionCode == 0xBU));
    char const* const bad[] = {nullptr,
                               "",
                               "PGMPART",
                               "PGMPART-",
                               "PGMPART-3-0-0-2",
                               "PGMPART-3-0-0-2-B-",
                               "PGMPART-3-0-0-2-B-1",
                               "PGMPART-3-0-0-2-b",
                               "PGMPART-30-0-2-B",
                               "PGMPART-2-B",
                               "pgmpart-3-0-0-2-B",
                               "PGMPART_3-0-0-2-B"};
    for (char const* const w : bad)
    {
        CHECK(!FlexNvmPartition::parseConfirmWord(w, c));
    }
}

// NVM-11 decision: 20 key slots on an S32K144 with CSEc - FCCOB1 0x03 goes to the module, nothing else changes
void testCsecDecision()
{
    reset();
    fakeSim.SDID = sdid(4U, 4U, 0xBU, 1U, 0x4U, 0x80U); // S32K144 with CSEc
    CHECK(FlexNvmPartition::partition(CSEC, "PGMPART-3-0-0-2-B") == Result::DONE);
    CHECK(module.count == 2U);
    Command const& pgmpart = module.commands[0];
    CHECK(fccob(pgmpart, 0U) == 0x80U);
    CHECK(fccob(pgmpart, 1U) == 0x03U); // 20 key slots, 512 bytes off the end of the 4 KB
    CHECK(fccob(pgmpart, 2U) == 0x00U); // SFE off
    CHECK(fccob(pgmpart, 3U) == 0x00U); // loaded during reset
    CHECK(fccob(pgmpart, 4U) == 0x02U);
    CHECK(fccob(pgmpart, 5U) == 0x0BU);
    CHECK(module.keySize == 0x03U);
    CHECK(!pgmpart.cacheOn && !pgmpart.interruptsOn);
    // the same word without the key space as the plan's word: the confirmation does not match
    reset();
    fakeSim.SDID = sdid(4U, 4U, 0xBU, 1U, 0x4U, 0x80U);
    CHECK(FlexNvmPartition::partition(CSEC, "PGMPART-0-0-0-2-B") == Result::WRONG_CONFIRMATION);
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-3-0-0-2-B") == Result::WRONG_CONFIRMATION);
    CHECK(module.count == 0U);
}

// NVM-11: combinations refused before anything is launched
void testKeySpaceRefused()
{
    reset();
    fakeSim.SDID = sdid(4U, 4U, 0xBU, 1U, 0x4U, 0x80U);
    // old word (project without key_slots on a derivative with key space): would fix the key space at 0
    FlexNvmPartition::Plan plan = DECISION;
    plan.confirmWord            = "PGMPART-2-B-0";
    CHECK(FlexNvmPartition::partition(plan, "PGMPART-2-B-0") == Result::KEY_SPACE_NOT_NAMED);
    // key size code beyond 20 slots
    plan.confirmWord = "PGMPART-4-0-0-2-B";
    CHECK(FlexNvmPartition::partition(plan, "PGMPART-4-0-0-2-B") == Result::INVALID_PLAN);
    // SFE: not through OpenBSW's driver
    plan.confirmWord = "PGMPART-3-1-0-2-B";
    CHECK(FlexNvmPartition::partition(plan, "PGMPART-3-1-0-2-B") == Result::INVALID_PLAN);
    plan.confirmWord = "PGMPART-0-1-0-2-B";
    CHECK(FlexNvmPartition::partition(plan, "PGMPART-0-1-0-2-B") == Result::INVALID_PLAN);
    // key space without load on reset ([RM13] Table 36-69)
    plan             = DECISION;
    plan.loadCode    = 1U;
    plan.confirmWord = "PGMPART-3-0-1-2-B";
    CHECK(FlexNvmPartition::partition(plan, "PGMPART-3-0-1-2-B") == Result::INVALID_PLAN);
    // word that repeats other codes than the plan's
    plan             = DECISION;
    plan.confirmWord = "PGMPART-3-0-0-2-4";
    CHECK(FlexNvmPartition::partition(plan, "PGMPART-3-0-0-2-4") == Result::INVALID_PLAN);
    plan.confirmWord = "PGMPART-3-0-1-2-B";
    CHECK(FlexNvmPartition::partition(plan, "PGMPART-3-0-1-2-B") == Result::INVALID_PLAN);
    plan.confirmWord = "PGMPART-3-0-0-F-B";
    CHECK(FlexNvmPartition::partition(plan, "PGMPART-3-0-0-F-B") == Result::INVALID_PLAN);
    plan.confirmWord = "PGMPART-3-0-0-2-B-";
    CHECK(FlexNvmPartition::partition(plan, "PGMPART-3-0-0-2-B-") == Result::INVALID_PLAN);
    CHECK(module.count == 0U);
    // part without CSEc (SIM SDID FEATURES bit 7 clear): key space refused, no key space still possible
    fakeSim.SDID = sdid(4U, 4U, 0xBU, 1U, 0x4U, 0x00U);
    CHECK(FlexNvmPartition::partition(CSEC, "PGMPART-3-0-0-2-B") == Result::NO_CSEC);
    CHECK(module.count == 0U);
    CHECK(FlexNvmPartition::partition(DECISION, "PGMPART-0-0-0-2-B") == Result::DONE);
    CHECK((fccob(module.commands[0], 1U) == 0x00U) && (module.keySize == 0x00U));
}

void testTexts()
{
    CHECK(std::strstr(FlexNvmPartition::resultText(Result::KEY_SPACE_NOT_NAMED), "key_slots") != nullptr);
    CHECK(std::strstr(FlexNvmPartition::resultText(Result::NO_CSEC), "CSEc") != nullptr);
    CHECK(std::strstr(FlexNvmPartition::resultText(Result::ALREADY_PARTITIONED), "mass erase") != nullptr);
    CHECK(std::strstr(FlexNvmPartition::resultText(Result::DONE), "reset") != nullptr);
}
} // namespace

int main()
{
    testNothingWithoutConfirmation();
    testNothingOnAPartitionedOrBusyChip();
    testInvalidPlan();
    testDecision(true);
    testDecision(false);
    testLoadByteIsHandedThrough();
    testRefusedCommand();
    testMixedStateOfTheBoard();
    testIfrEeeSizeByteProgrammed();
    testInconsistentFlexRam();
    testPartitionStates();
    testDeviceId();
    testConfirmWordParsing();
    testCsecDecision();
    testKeySpaceRefused();
    testTexts();
    std::printf("FlexNvmPartitionHostTest: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
