// Host test of the data flash of the S32K1 family (NVM-12, include/flexnvm/FlexNvmDataFlash.h) and of the lock
// between it and the EEPROM stores (include/flexnvm/FlexNvmEeprom.h), against the register model stubs/mcu/mcu.h and a
// model of the flash module: writing CCIF launches the command in FCCOB (FSTAT write 1 to clear), the module stays
// busy for a number of FSTAT reads, then erases the sector or programs the phrase in a host array standing in for the
// FlexNVM, or reports the error flags the test sets. No board.
// Checked: states (no region, chip not partitioned, the project's partition, another partition), range and alignment,
// FCCOB bytes (command, data flash address with bit 23, phrase bytes in memory order), launch only with the module
// idle and no EEPROM store running, interrupts suspended around the launch only, poll (busy, result once, errors,
// time limit in microseconds), cache invalidate hook after every command, read refused while busy and on a read
// collision; an EEPROM store waits for the end of a data flash command (bounded) and is never made during one.
// NVM-25 (safety review NVM12-S1 b, block lock flexnvm/FlexNvmBlockLock.h): read() copies in chunks with interrupts
// suspended and the block idle, a store that another task started between two chunks ends the read with BUSY, a data
// flash launch of another task during a read is refused; a hold for a read in place keeps EEPROM stores (bounded wait,
// then the write fails - it never collides) and launches out, is granted only with the block idle, counts what it
// refused, and a release without a hold is counted, not wrapped. "Another task" is played at the points where the
// code enables interrupts again (stubs/cache/cache.h onResume).
// Build and run: platforms/s32k1xx/bsp/bspFlexNvm/test/test_flexnvm.py.

#include "flexnvm/FlexNvmDataFlash.h"
#include "flexnvm/FlexNvmEeprom.h"

#include <dataflash/DataFlashAreas.h>

#include <cstdio>
#include <cstring>

FakeFtfc fakeFtfc;
FakeSim fakeSim;
FakeCore fakeCore;
uint32_t fakeTimeUs     = 0U;
uint32_t fakeTimeStepUs = 0U;

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

using ::dataflash::Status;
using platform::FlexNvmDataFlash;
using platform::FlexNvmEeprom;

constexpr uint32_t SECTOR = 2048U;
constexpr uint32_t SIZE   = 32768U; // decision nvm01-eeesize: 32 KB data flash, DEPART 0xB

alignas(8) uint8_t flexNvm[SIZE];
alignas(4) uint8_t flexRam[4096];

// flash module model
struct Module
{
    uint32_t busyReads   = 2U;  // FSTAT reads while a command runs
    uint32_t left        = 0U;  // reads left of the command running
    bool running         = false;
    uint8_t errorFlags   = 0U;  // ACCERR/FPVIOL/MGSTAT0 the next command ends with
    uint32_t launches    = 0U;
    uint8_t cmd[12]      = {};
    bool launchedWithInterruptsOn = false;
    bool storedDuringCommand      = false;
    bool never                    = false; // the command never ends
};

Module module;
uint32_t invalidates = 0U;

// NVM-29: model of the FMC buffers of bank 1 ([RM13] chapter 35) - a single-entry buffer (last word read) and a
// speculation buffer (the next word, fetched on a miss while speculation for data is on, OCMDR1 bit 4 = 0). Neither
// learns of a command (board 2026-10-01). Writing OCMDR1 with OCM1 = 11b clears the speculation buffer (the reference
// manual says so); the single-entry buffer is never cleared by a register (worst case: the manual offers none). Every
// hit is compared with the flash array: a hit on a word that differs is counted as a stale read.
constexpr uint32_t WORD = 8U; // S32K144: 64-bit words of the data flash
constexpr uint32_t OCMDR1_RESET = 0xCA08B000U; // [RM13] 34.4.2.19: data flash, OCMT 101b, speculation on

struct Fmc
{
    struct Entry
    {
        bool valid      = false;
        uint32_t offset = 0U;
        uint8_t data[WORD] = {};
    };
    Entry single;
    Entry speculation;
    uint32_t misses       = 0U;
    uint32_t staleReads   = 0U;  // bytes served from a buffer that differ from the flash
    uint32_t staleReadsOfFlush = 0U; // the same while speculation is off (the reads of the flush, value unused)
    uint32_t readsSpeculationOff = 0U;
    uint32_t ocmdrWritesInterruptsOn = 0U;
};

Fmc fmc;

void fill(Fmc::Entry& e, uint32_t const offset)
{
    e.valid  = true;
    e.offset = offset;
    std::memcpy(e.data, &flexNvm[offset], WORD);
}

uint8_t fmcRead(uintptr_t const address)
{
    uintptr_t const base = reinterpret_cast<uintptr_t>(flexNvm);
    if ((address < base) || (address >= (base + SIZE)))
    {
        return *reinterpret_cast<uint8_t const volatile*>(address);
    }
    uint32_t const offset = static_cast<uint32_t>(address - base);
    uint32_t const word   = offset - (offset % WORD);
    bool const speculate  = (fakeMscm.OCMDR[1].value & 0x10U) == 0U;
    fmc.readsSpeculationOff += speculate ? 0U : 1U;
    Fmc::Entry* hit = nullptr;
    if (fmc.single.valid && (fmc.single.offset == word))
    {
        hit = &fmc.single;
    }
    else if (fmc.speculation.valid && (fmc.speculation.offset == word))
    {
        fmc.single = fmc.speculation; // the word moves into the single-entry buffer
        hit        = &fmc.single;
    }
    if (hit != nullptr)
    {
        uint8_t const value = hit->data[offset % WORD];
        // a stale word read while speculation is off is a read of the flush, whose value is discarded
        uint32_t& stale = speculate ? fmc.staleReads : fmc.staleReadsOfFlush;
        stale += (value != flexNvm[offset]) ? 1U : 0U;
        return value;
    }
    ++fmc.misses;
    fill(fmc.single, word);
    if (speculate && ((word + WORD) < SIZE))
    {
        fill(fmc.speculation, word + WORD);
    }
    return flexNvm[offset];
}

void ocmdrWritten(FakeReg32& reg)
{
    fmc.ocmdrWritesInterruptsOn += fakeCore.interruptsOn ? 1U : 0U;
    if ((reg.value & 0x30U) == 0x30U)
    {
        fmc.speculation.valid = false; // all speculation off: the speculation buffer is cleared
    }
}

void invalidate() { ++invalidates; }

void finishCommand()
{
    module.running = false;
    uint32_t const address = (static_cast<uint32_t>(module.cmd[1]) << 16U) | (static_cast<uint32_t>(module.cmd[2]) << 8U)
                             | module.cmd[3];
    uint32_t const offset = address & 0x7FFFFFU;
    if (module.errorFlags != 0U)
    {
        fakeFtfc.FSTAT.value = static_cast<uint8_t>(fakeFtfc.FSTAT.value | module.errorFlags);
    }
    else if (((address & 0x800000U) != 0U) && (module.cmd[0] == 0x09U) && (offset < SIZE))
    {
        std::memset(&flexNvm[offset - (offset % SECTOR)], 0xFF, SECTOR);
    }
    else if (((address & 0x800000U) != 0U) && (module.cmd[0] == 0x07U) && ((offset + 8U) <= SIZE))
    {
        for (uint32_t i = 0U; i < 8U; ++i)
        {
            flexNvm[offset + i] = static_cast<uint8_t>(flexNvm[offset + i] & module.cmd[4U + i]); // 1 -> 0 only
        }
    }
    else
    {
        fakeFtfc.FSTAT.value = static_cast<uint8_t>(fakeFtfc.FSTAT.value | FTFC_FSTAT_ACCERR_MASK);
    }
    fakeFtfc.FSTAT.value = static_cast<uint8_t>(fakeFtfc.FSTAT.value | FTFC_FSTAT_CCIF_MASK);
}

uint8_t fstatRead(FakeReg8 const& reg)
{
    if (module.running && !module.never)
    {
        if (module.left == 0U)
        {
            finishCommand();
        }
        else
        {
            --module.left;
        }
    }
    return reg.value;
}

void fstatWrite(FakeReg8& reg, uint8_t const v)
{
    if ((v & FTFC_FSTAT_CCIF_MASK) != 0U)
    {
        // launch: FCCOB in register order FCCOB3..0, FCCOB7..4, FCCOBB..8
        ++module.launches;
        module.launchedWithInterruptsOn = fakeCore.interruptsOn;
        module.cmd[0] = fakeFtfc.FCCOB[3];
        module.cmd[1] = fakeFtfc.FCCOB[2];
        module.cmd[2] = fakeFtfc.FCCOB[1];
        module.cmd[3] = fakeFtfc.FCCOB[0];
        for (uint32_t i = 0U; i < 8U; ++i)
        {
            module.cmd[4U + i] = fakeFtfc.FCCOB[4U + i];
        }
        module.running = true;
        module.left    = module.busyReads;
        reg.value      = static_cast<uint8_t>(reg.value & static_cast<uint8_t>(~FTFC_FSTAT_CCIF_MASK));
    }
}

void reset()
{
    fakeFtfc = FakeFtfc();
    fakeFtfc.FSTAT.value   = FTFC_FSTAT_CCIF_MASK;
    fakeFtfc.FSTAT.onRead  = &fstatRead;
    fakeFtfc.FSTAT.onWrite = &fstatWrite;
    fakeFtfc.FCNFG.value   = FTFC_FCNFG_EEERDY_MASK; // partitioned with EEPROM, FlexRAM loaded
    fakeSim.FCFG1          = 0xFF020FFFU | (0xBU << 12U);
    fakeCore               = FakeCore();
    module                 = Module();
    invalidates            = 0U;
    fakeTimeUs             = 0U;
    fakeTimeStepUs         = 0U;
    platform::flexNvmBlockLock = platform::FlexNvmBlockLock(); // NVM-25: one lock per image, fresh per test
    std::memset(flexNvm, 0x5A, sizeof(flexNvm));
    std::memset(flexRam, 0x00, sizeof(flexRam));
    fmc                           = Fmc(); // NVM-29: buffers empty, as after reset
    fakeMscm                      = FakeMscm();
    fakeMscm.OCMDR[0].value       = 0xDC089000U;
    fakeMscm.OCMDR[1].value       = OCMDR1_RESET;
    fakeMscm.OCMDR[2].value       = 0xC304D000U;
    fakeMscm.OCMDR[1].onWrite     = &ocmdrWritten;
    fakeFlexNvmRead               = &fmcRead;
}

FlexNvmDataFlash::Region region()
{
    return FlexNvmDataFlash::Region{reinterpret_cast<uintptr_t>(flexNvm), SIZE, SECTOR, 0xBU};
}

Status runToEnd(FlexNvmDataFlash& flash)
{
    Status s = Status::BUSY;
    for (uint32_t i = 0U; (i < 100U) && (s == Status::BUSY); ++i)
    {
        s = flash.poll();
    }
    return s;
}

void testStates()
{
    reset();
    FlexNvmDataFlash flash(&invalidate);
    uint8_t buffer[8] = {};
    CHECK(flash.state() == FlexNvmDataFlash::State::NO_REGION);
    CHECK(flash.geometry().size == 0U);
    CHECK(flash.read(0U, buffer, 8U) == Status::NOT_AVAILABLE);
    CHECK(flash.startErase(0U) == Status::NOT_AVAILABLE);
    flash.setRegion(region());
    CHECK(flash.state() == FlexNvmDataFlash::State::READY);
    CHECK(flash.geometry().size == SIZE);
    CHECK(flash.geometry().sectorSize == SECTOR);
    CHECK(flash.geometry().programSize == 8U);
    CHECK(flash.geometry().start == reinterpret_cast<uintptr_t>(flexNvm));
    fakeSim.FCFG1 = 0xFFFFFFFFU; // not partitioned: the whole FlexNVM is data flash, the areas lie in it
    CHECK(flash.state() == FlexNvmDataFlash::State::READY);
    fakeSim.FCFG1 = 0xFF020FFFU | (0xAU << 12U); // 16 KB data flash: another partition
    CHECK(flash.state() == FlexNvmDataFlash::State::WRONG_PARTITION);
    CHECK(flash.geometry().size == 0U);
    CHECK(flash.read(0U, buffer, 8U) == Status::NOT_AVAILABLE);
    CHECK(flash.startProgram(0U, buffer) == Status::NOT_AVAILABLE);
    CHECK(module.launches == 0U);
    CHECK(std::strcmp(FlexNvmDataFlash::stateText(FlexNvmDataFlash::State::WRONG_PARTITION),
                      "chip partitioned differently than the project (DEPART)")
          == 0);
}

void testRangeAndAlignment()
{
    reset();
    FlexNvmDataFlash flash(&invalidate);
    flash.setRegion(region());
    uint8_t buffer[8] = {};
    CHECK(flash.read(SIZE - 8U, buffer, 8U) == Status::OK);
    CHECK(flash.read(SIZE - 7U, buffer, 8U) == Status::RANGE);
    CHECK(flash.read(0U, buffer, 0U) == Status::RANGE);
    CHECK(flash.read(0U, nullptr, 1U) == Status::RANGE);
    CHECK(flash.startErase(SIZE) == Status::RANGE);
    CHECK(flash.startErase(SECTOR + 8U) == Status::ALIGNMENT);
    CHECK(flash.startProgram(4U, buffer) == Status::ALIGNMENT);
    CHECK(flash.startProgram(SIZE - 4U, buffer) == Status::RANGE);
    CHECK(flash.startProgram(0U, nullptr) == Status::RANGE);
    CHECK(module.launches == 0U);
}

void testEraseAndProgram()
{
    reset();
    FlexNvmDataFlash flash(&invalidate);
    flash.setRegion(region());
    CHECK(flash.poll() == Status::OK); // nothing pending
    CHECK(flash.startErase(3U * SECTOR) == Status::OK);
    CHECK(module.launches == 1U);
    CHECK(module.cmd[0] == 0x09U);
    CHECK(module.cmd[1] == 0x80U); // bit 23: data flash
    CHECK((module.cmd[2] == 0x18U) && (module.cmd[3] == 0x00U));
    CHECK(!module.launchedWithInterruptsOn);
    CHECK(fakeCore.interruptsOn);
    CHECK(fakeCore.suspends == fakeCore.resumes);
    CHECK(flash.startErase(0U) == Status::BUSY); // result of the last command not fetched
    CHECK(flash.poll() == Status::BUSY);
    CHECK(runToEnd(flash) == Status::OK);
    CHECK(flash.poll() == Status::OK);
    CHECK(invalidates == 1U);
    CHECK((flexNvm[3U * SECTOR] == 0xFFU) && (flexNvm[4U * SECTOR - 1U] == 0xFFU));
    CHECK((flexNvm[3U * SECTOR - 1U] == 0x5AU) && (flexNvm[4U * SECTOR] == 0x5AU));
    uint8_t const phrase[8] = {0x01U, 0x23U, 0x45U, 0x67U, 0x89U, 0xABU, 0xCDU, 0xEFU};
    CHECK(flash.startProgram(3U * SECTOR + 16U, phrase) == Status::OK);
    CHECK(module.cmd[0] == 0x07U);
    CHECK((module.cmd[1] == 0x80U) && (module.cmd[2] == 0x18U) && (module.cmd[3] == 0x10U));
    CHECK(runToEnd(flash) == Status::OK);
    CHECK(std::memcmp(&flexNvm[3U * SECTOR + 16U], phrase, 8U) == 0); // memory order
    uint8_t back[8] = {};
    CHECK(flash.read(3U * SECTOR + 16U, back, 8U) == Status::OK);
    CHECK(std::memcmp(back, phrase, 8U) == 0);
    CHECK(invalidates == 2U);
}

void testLocks()
{
    reset();
    FlexNvmDataFlash flash(&invalidate);
    flash.setRegion(region());
    uint8_t buffer[8] = {};
    // an EEPROM store runs (EEERDY 0, FlexRAM not RAM): no read, no command
    fakeFtfc.FCNFG.value = 0U;
    CHECK(flash.read(0U, buffer, 8U) == Status::BUSY);
    CHECK(flash.startErase(0U) == Status::BUSY);
    CHECK(module.launches == 0U);
    CHECK(fakeCore.suspends == fakeCore.resumes);
    // FlexRAM as RAM (data flash only partition, or not partitioned): no store possible, commands allowed
    fakeFtfc.FCNFG.value = FTFC_FCNFG_RAMRDY_MASK;
    CHECK(flash.startErase(0U) == Status::OK);
    CHECK(runToEnd(flash) == Status::OK);
    fakeFtfc.FCNFG.value = FTFC_FCNFG_EEERDY_MASK;
    // another command runs (CSEc, a program flash write): refused
    module.never = true;
    fakeFtfc.FSTAT.value = 0U;
    CHECK(flash.startErase(0U) == Status::BUSY);
    CHECK(flash.read(0U, buffer, 8U) == Status::BUSY);
    CHECK(module.launches == 1U);
    module.never = false;
    fakeFtfc.FSTAT.value = FTFC_FSTAT_CCIF_MASK;
    // read collision: reported as busy, flag cleared, counted
    fakeFtfc.FSTAT.value = static_cast<uint8_t>(FTFC_FSTAT_CCIF_MASK | FTFC_FSTAT_RDCOLERR_MASK);
    CHECK(flash.read(0U, buffer, 8U) == Status::BUSY);
    CHECK(fakeFtfc.FSTAT.lastWritten == FTFC_FSTAT_RDCOLERR_MASK);
    CHECK(flash.collisions() == 1U);
    CHECK(flash.read(0U, buffer, 8U) == Status::OK);
}

void testErrorsAndTimeout()
{
    reset();
    FlexNvmDataFlash flash(&invalidate);
    flash.setRegion(region());
    uint8_t const phrase[8] = {};
    module.errorFlags = FTFC_FSTAT_MGSTAT0_MASK;
    CHECK(flash.startProgram(0U, phrase) == Status::OK);
    CHECK(runToEnd(flash) == Status::FAILED);
    CHECK(flash.poll() == Status::OK); // reported once
    // the error flags of the last command are cleared before the next launch
    module.errorFlags = 0U;
    fakeFtfc.FSTAT.value = static_cast<uint8_t>(FTFC_FSTAT_CCIF_MASK | FTFC_FSTAT_ACCERR_MASK);
    CHECK(flash.startErase(0U) == Status::OK);
    CHECK(runToEnd(flash) == Status::OK);
    module.errorFlags = FTFC_FSTAT_FPVIOL_MASK;
    CHECK(flash.startErase(0U) == Status::OK);
    CHECK(runToEnd(flash) == Status::FAILED);
    module.errorFlags = FTFC_FSTAT_ACCERR_MASK;
    CHECK(flash.startErase(0U) == Status::OK);
    CHECK(runToEnd(flash) == Status::FAILED);
    module.errorFlags = 0U;
    // time limit in microseconds: an erase that never ends is given up after 260 ms, a program after 450 us
    module.never   = true;
    fakeTimeUs     = 0xFFFFFF00U; // across the wrap
    fakeTimeStepUs = 1000U;
    CHECK(flash.startErase(0U) == Status::OK);
    uint32_t polls = 0U;
    Status s       = Status::BUSY;
    while ((s == Status::BUSY) && (polls < 1000U))
    {
        s = flash.poll();
        ++polls;
    }
    CHECK(s == Status::TIMEOUT);
    CHECK(polls == 261U); // time read at launch, then 1000 us per poll: the 261st is the first above 260000 us
    CHECK(flash.timeouts() == 1U);
    CHECK(flash.startErase(0U) == Status::BUSY); // the module still runs the command
    module.never = false;
    for (uint32_t i = 0U; (i < 10U) && ((fakeFtfc.FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U); ++i)
    {
        // the module ends the command late; nobody waits for it any more
    }
    CHECK(flash.poll() == Status::OK);
    fakeTimeStepUs = 100U;
    module.never   = true;
    CHECK(flash.startProgram(0U, phrase) == Status::OK);
    polls = 0U;
    s     = Status::BUSY;
    while ((s == Status::BUSY) && (polls < 1000U))
    {
        s = flash.poll();
        ++polls;
    }
    CHECK(s == Status::TIMEOUT);
    CHECK(polls == 5U);
    CHECK(flash.timeouts() == 2U);
    fakeTimeStepUs = 0U;
}

void testLongest()
{
    reset();
    FlexNvmDataFlash flash;
    flash.setRegion(region());
    fakeTimeStepUs   = 500U;
    module.busyReads = 3U;
    CHECK(flash.startErase(0U) == Status::OK);
    CHECK(runToEnd(flash) == Status::OK);
    CHECK(flash.longestEraseUs() == 2000U);
    CHECK(flash.longestProgramUs() == 0U);
    fakeTimeStepUs = 0U;
}

void testEepromWaitsForCommand()
{
    reset();
    FlexNvmDataFlash flash(&invalidate);
    flash.setRegion(region());
    FlexNvmEeprom eeprom(4000U);
    eeprom.setWindow(reinterpret_cast<uintptr_t>(flexRam), sizeof(flexRam));
    // a data flash command runs for 5 FSTAT reads: the store waits for its end, then stores once
    module.busyReads = 5U;
    CHECK(flash.startErase(0U) == Status::OK);
    uint8_t const value = 0x42U;
    fakeTimeStepUs      = 10U;
    CHECK(eeprom.write(7U, &value, 1U) == ::bsp::BSP_OK);
    CHECK(flexRam[7] == 0x42U);
    CHECK(eeprom.commandWaits() == 1U);
    CHECK(!module.running);
    CHECK(fakeCore.suspends == fakeCore.resumes);
    CHECK(fakeCore.interruptsOn);
    CHECK(flash.poll() == Status::OK);
    // a command that never ends: the store gives up after COMMAND_WAIT_LIMIT_US and stores nothing
    module.never = true;
    CHECK(flash.startErase(0U) == Status::OK);
    fakeTimeStepUs = 10000U;
    uint8_t const other = 0x43U;
    uint32_t const before = fakeTimeUs;
    CHECK(eeprom.write(8U, &other, 1U) == ::bsp::BSP_ERROR);
    CHECK((fakeTimeUs - before) <= (FlexNvmEeprom::COMMAND_WAIT_LIMIT_US + 30000U)); // given up at the limit
    CHECK(flexRam[8] == 0x00U);
    CHECK(eeprom.timeouts() == 1U);
    CHECK(eeprom.commandWaits() == 2U);
    fakeTimeStepUs = 0U;
}

// ---- NVM-25: the block lock ------------------------------------------------------------------------------------

using platform::flexNvmBlockLock;
using platform::FlexNvmBlockLock;

// "another task": runs at the n-th point where interrupts are enabled again (stubs onResume)
uint32_t otherCountdown = 0U;
void (*otherWork)()     = nullptr;

void otherTask()
{
    if (otherCountdown > 1U)
    {
        --otherCountdown;
        fakeCore.onResume = &otherTask; // not yet: wait for the next resume
        return;
    }
    otherCountdown = 0U;
    otherWork();
}

void armOther(uint32_t const nthResume, void (*work)())
{
    otherCountdown    = nthResume;
    otherWork         = work;
    fakeCore.onResume = &otherTask;
}

FlexNvmDataFlash* otherFlash = nullptr;
Status otherStatus           = Status::OK;
uint32_t fcnfgReadsInterruptsOn = 0U;

uint8_t fcnfgWatched(FakeReg8 const& reg)
{
    fcnfgReadsInterruptsOn += fakeCore.interruptsOn ? 1U : 0U;
    return reg.value;
}

void otherStartsStore() { fakeFtfc.FCNFG.value = 0U; } // an EEPROM store of another task, still running

void otherLaunchesErase() { otherStatus = otherFlash->startErase(SECTOR); }

void otherReleasesHold() { otherFlash->releaseDirectRead(); }

void testReadUnderLock()
{
    reset();
    FlexNvmDataFlash flash(&invalidate);
    flash.setRegion(region());
    otherFlash = &flash;
    for (uint32_t i = 0U; i < 64U; ++i)
    {
        flexNvm[100U + i] = static_cast<uint8_t>(i);
    }
    uint8_t buffer[64] = {};
    // two chunks of READ_CHUNK_BYTES, each with interrupts suspended and the block checked idle inside
    CHECK(FlexNvmDataFlash::READ_CHUNK_BYTES == 32U);
    fakeFtfc.FCNFG.onRead      = &fcnfgWatched;
    fcnfgReadsInterruptsOn     = 0U;
    uint32_t const suspends    = fakeCore.suspends;
    uint32_t const fcnfgReads  = fakeFtfc.FCNFG.reads;
    CHECK(flash.read(100U, buffer, 64U) == Status::OK);
    CHECK(fakeCore.suspends - suspends == 4U); // begin, chunk, chunk, end
    CHECK(fakeFtfc.FCNFG.reads - fcnfgReads == 3U); // begin + one idle check per chunk
    CHECK(fcnfgReadsInterruptsOn == 0U);
    CHECK(buffer[0] == 0U);
    CHECK(buffer[63] == 63U);
    CHECK(flexNvmBlockLock.reads() == 0U);
    CHECK(fakeCore.interruptsOn);
    // a store another task starts between the chunks and that still runs when the read goes on: BUSY, nothing counted
    armOther(2U, &otherStartsStore); // resume 1: after begin, resume 2: after chunk 1
    CHECK(flash.read(100U, buffer, 64U) == Status::BUSY);
    CHECK(flexNvmBlockLock.reads() == 0U);
    CHECK(fakeCore.suspends == fakeCore.resumes);
    fakeFtfc.FCNFG.value = FTFC_FCNFG_EEERDY_MASK; // the store has ended
    // a store that starts right after the begin: the first chunk sees it
    armOther(1U, &otherStartsStore);
    CHECK(flash.read(100U, buffer, 8U) == Status::BUSY);
    CHECK(flexNvmBlockLock.reads() == 0U);
    fakeFtfc.FCNFG.value = FTFC_FCNFG_EEERDY_MASK;
    // a data flash launch of another task while the read runs: refused, nothing launched, the read is complete
    std::memset(buffer, 0, sizeof(buffer));
    armOther(2U, &otherLaunchesErase);
    CHECK(flash.read(100U, buffer, 64U) == Status::OK);
    CHECK(otherStatus == Status::BUSY);
    CHECK(module.launches == 0U);
    CHECK(flash.heldOff() == 1U);
    CHECK(buffer[63] == 63U);
    // after the read the same launch works
    CHECK(flash.startErase(SECTOR) == Status::OK);
    CHECK(runToEnd(flash) == Status::OK);
    fakeFtfc.FCNFG.onRead = nullptr;
    CHECK(flexNvmBlockLock.unbalanced() == 0U);
}

void testHoldForDirectRead()
{
    reset();
    FlexNvmDataFlash flash(&invalidate);
    otherFlash = &flash;
    // no usable data flash: no hold, the lock untouched
    CHECK(!flash.holdForDirectRead());
    CHECK(flexNvmBlockLock.holds() == 0U);
    flash.setRegion(region());
    FlexNvmEeprom eeprom(4000U);
    eeprom.setWindow(reinterpret_cast<uintptr_t>(flexRam), sizeof(flexRam));

    CHECK(flash.holdForDirectRead());
    CHECK(flexNvmBlockLock.holds() == 1U);
    CHECK(fakeCore.suspends == fakeCore.resumes);
    // a hold is a read: read() works, a launch does not
    uint8_t buffer[8] = {};
    CHECK(flash.read(0U, buffer, 8U) == Status::OK);
    CHECK(flash.startErase(0U) == Status::BUSY);
    uint8_t const phrase[8] = {};
    CHECK(flash.startProgram(0U, phrase) == Status::BUSY);
    CHECK(module.launches == 0U);
    CHECK(flash.heldOff() == 2U);
    // an EEPROM store waits for the hold; never released: the write fails at the limit and stores nothing
    uint8_t const value = 0x42U;
    fakeTimeStepUs      = 10000U;
    CHECK(eeprom.write(5U, &value, 1U) == ::bsp::BSP_ERROR);
    CHECK(flexRam[5] == 0x00U);
    CHECK(eeprom.timeouts() == 1U);
    CHECK(eeprom.commandWaits() == 1U);
    CHECK(eeprom.holdWaits() == 1U);
    // the holder releases while the store waits (another task, third resume of the waiting loop): stored once
    fakeTimeStepUs = 10U;
    armOther(3U, &otherReleasesHold);
    CHECK(eeprom.write(5U, &value, 1U) == ::bsp::BSP_OK);
    CHECK(flexRam[5] == 0x42U);
    CHECK(flexNvmBlockLock.holds() == 0U);
    CHECK(eeprom.holdWaits() == 2U);
    CHECK(eeprom.timeouts() == 1U);
    CHECK(fakeCore.interruptsOn);
    fakeTimeStepUs = 0U;
    // released: launches work again; no hold while the command runs
    CHECK(flash.startErase(0U) == Status::OK);
    CHECK(!flash.holdForDirectRead());
    CHECK(flexNvmBlockLock.refusedHolds() == 1U);
    CHECK(flexNvmBlockLock.holds() == 0U);
    CHECK(runToEnd(flash) == Status::OK);
    // no hold while an EEPROM store runs
    fakeFtfc.FCNFG.value = 0U;
    CHECK(!flash.holdForDirectRead());
    CHECK(flexNvmBlockLock.refusedHolds() == 2U);
    fakeFtfc.FCNFG.value = FTFC_FCNFG_EEERDY_MASK;
    // FlexRAM as RAM (no EEPROM): a hold is possible
    fakeFtfc.FCNFG.value = FTFC_FCNFG_RAMRDY_MASK;
    CHECK(flash.holdForDirectRead());
    flash.releaseDirectRead();
    fakeFtfc.FCNFG.value = FTFC_FCNFG_EEERDY_MASK;
    // a release without a hold is counted, the count does not wrap
    flash.releaseDirectRead();
    CHECK(flexNvmBlockLock.holds() == 0U);
    CHECK(flexNvmBlockLock.unbalanced() == 1U);
    CHECK(flash.startErase(0U) == Status::OK);
    CHECK(runToEnd(flash) == Status::OK);
    // nested holds (several readers) up to MAX_COUNT, then refused; all released: free again
    uint32_t granted = 0U;
    for (uint32_t i = 0U; i < 300U; ++i)
    {
        granted += flash.holdForDirectRead() ? 1U : 0U;
    }
    CHECK(granted == FlexNvmBlockLock::MAX_COUNT);
    for (uint32_t i = 0U; i < granted; ++i)
    {
        flash.releaseDirectRead();
    }
    CHECK(flexNvmBlockLock.holds() == 0U);
    CHECK(flash.startErase(0U) == Status::OK);
    CHECK(runToEnd(flash) == Status::OK);
    CHECK(fakeCore.suspends == fakeCore.resumes);
}

void testLockAlone()
{
    reset();
    FlexNvmBlockLock lock;
    CHECK(lock.storeAllowed());
    CHECK(lock.launchAllowed());
    CHECK(lock.beginRead());
    CHECK(lock.storeAllowed()); // a read through read() keeps stores out by its chunks, not by the count
    CHECK(!lock.launchAllowed());
    lock.endRead();
    CHECK(lock.launchAllowed());
    CHECK(lock.tryHold());
    CHECK(!lock.storeAllowed());
    CHECK(!lock.launchAllowed());
    lock.release();
    CHECK(lock.storeAllowed());
    lock.endRead();
    CHECK(lock.unbalanced() == 1U);
    CHECK(lock.reads() == 0U);
    // busy block: neither a read nor a hold begins
    fakeFtfc.FCNFG.value = 0U;
    CHECK(!lock.beginRead());
    CHECK(!lock.tryHold());
    CHECK(lock.reads() == 0U);
    CHECK(lock.holds() == 0U);
    fakeFtfc.FCNFG.value = FTFC_FCNFG_EEERDY_MASK;
    module.never         = true;
    fakeFtfc.FSTAT.value = 0U; // a command runs (CSEc, another driver)
    CHECK(!lock.beginRead());
    CHECK(!lock.tryHold());
    module.never         = false;
    fakeFtfc.FSTAT.value = FTFC_FSTAT_CCIF_MASK;
    CHECK(fakeCore.suspends == fakeCore.resumes);
}

// ---- NVM-29: FMC buffers after a command -------------------------------------------------------------------------

Status runJob(::dataflash::AreaService& service)
{
    Status s = Status::BUSY;
    for (uint32_t i = 0U; (i < 1000U) && (s == Status::BUSY); ++i)
    {
        s = service.cyclic();
    }
    return s;
}

::dataflash::Area const boardAreas[] = {
    {"CalBackup", 0x0000U, 0x2000U, ::dataflash::Purpose::CALIBRATION},
    {"CalMain", 0x2000U, 0x2000U, ::dataflash::Purpose::CALIBRATION},
    {"Test", 0x4000U, 0x1000U, ::dataflash::Purpose::DATA},
};

void testBoardSequence20261001()
{
    // the console sequence of the board 2026-10-01 (dflash_1.txt), through the AreaService as in the port
    reset();
    FlexNvmDataFlash flash(&invalidate);
    flash.setRegion(region());
    ::dataflash::AreaService service(flash, boardAreas, 3U);
    uint32_t const test = service.find("Test");
    CHECK(service.erase(test) == Status::OK);
    CHECK(runJob(service) == Status::OK);
    CHECK(service.commands() == 2U);
    uint8_t buffer[64] = {};
    CHECK(service.read(test, 0U, buffer, 64U) == Status::OK); // dflash read Test 0 64
    CHECK((buffer[0] == 0xFFU) && (buffer[63] == 0xFFU));
    uint8_t data[256] = {};
    for (uint32_t i = 0U; i < 256U; ++i)
    {
        data[i] = static_cast<uint8_t>(0x10U + i);
    }
    CHECK(service.write(test, 0U, data, 256U) == Status::OK); // dflash write Test 0 256 0x10
    CHECK(runJob(service) == Status::OK); // board: "failed" after the first Program Phrase
    CHECK(service.commands() == 34U);
    CHECK(std::memcmp(&flexNvm[0x4000U], data, 256U) == 0);
    CHECK(service.read(test, 0U, buffer, 64U) == Status::OK);
    CHECK((buffer[0] == 0x10U) && (buffer[7] == 0x17U) && (buffer[63] == 0x4FU)); // board: ff
    CHECK(service.write(test, 0U, data, 8U) == Status::OK); // same bytes: skipped, no command
    CHECK(runJob(service) == Status::OK);
    CHECK(service.commands() == 34U);
    uint8_t other[8] = {0x20U, 0x21U, 0x22U, 0x23U, 0x24U, 0x25U, 0x26U, 0x27U};
    CHECK(service.write(test, 0U, other, 8U) == Status::OK);
    CHECK(runJob(service) == Status::NOT_ERASED);
    CHECK(fmc.staleReads == 0U);
    CHECK(flash.bufferFlushes() == 34U); // one per command
    CHECK(flash.speculationNotCleared() == 0U);
    CHECK(fmc.ocmdrWritesInterruptsOn == 0U);
    CHECK(fakeMscm.OCMDR[1].value == OCMDR1_RESET); // speculation as before
    CHECK(fakeMscm.OCMDR[0].writes == 0U);
    CHECK(fakeMscm.OCMDR[2].writes == 0U);
    CHECK(fakeCore.suspends == fakeCore.resumes);
}

void testEraseThenSameDataIsProgrammed()
{
    // the silent case: an erased unit must not be skipped because a buffer still shows the bytes of before the erase
    reset();
    FlexNvmDataFlash flash(&invalidate);
    flash.setRegion(region());
    ::dataflash::AreaService service(flash, boardAreas, 3U);
    uint32_t const calMain = service.find("CalMain");
    uint8_t set[16] = {};
    for (uint32_t i = 0U; i < 16U; ++i)
    {
        set[i] = static_cast<uint8_t>(0xA0U + i);
    }
    std::memcpy(&flexNvm[0x2000U], set, sizeof(set)); // the data set as it was programmed before
    uint8_t buffer[8] = {};
    // read before the erase: word 0 in the single-entry buffer, word 1 in the speculation buffer
    CHECK(service.read(calMain, 0U, buffer, 8U) == Status::OK);
    CHECK(std::memcmp(buffer, set, 8U) == 0);
    CHECK(service.erase(calMain) == Status::OK);
    CHECK(runJob(service) == Status::OK);
    uint32_t const before = service.commands();
    CHECK(service.write(calMain, 0U, set, 16U) == Status::OK);
    CHECK(runJob(service) == Status::OK);
    CHECK(service.commands() == before + 2U); // both units programmed, none skipped
    CHECK(std::memcmp(&flexNvm[0x2000U], set, 16U) == 0);
    CHECK(fmc.staleReads == 0U);
}

void testProgramNextWordAfterSpeculation()
{
    // the word behind the one read is in the speculation buffer; it is programmed and read back
    reset();
    FlexNvmDataFlash flash(&invalidate);
    flash.setRegion(region());
    std::memset(&flexNvm[3U * SECTOR], 0xFF, SECTOR);
    uint8_t buffer[8] = {};
    CHECK(flash.read(3U * SECTOR + 64U, buffer, 8U) == Status::OK);
    CHECK(fmc.speculation.valid && (fmc.speculation.offset == 3U * SECTOR + 72U));
    uint8_t const phrase[8] = {1U, 2U, 3U, 4U, 5U, 6U, 7U, 8U};
    CHECK(flash.startProgram(3U * SECTOR + 72U, phrase) == Status::OK);
    CHECK(runToEnd(flash) == Status::OK);
    CHECK(!fmc.speculation.valid);
    CHECK(fmc.readsSpeculationOff == 2U); // the two reads of the flush, speculation off
    CHECK(flash.read(3U * SECTOR + 72U, buffer, 8U) == Status::OK);
    CHECK(std::memcmp(buffer, phrase, 8U) == 0);
    CHECK(fmc.staleReads == 0U);
}

void otherStoreEnds() { fakeFtfc.FCNFG.value = FTFC_FCNFG_EEERDY_MASK; }

void testFlushDeferredWhileStoreRuns()
{
    reset();
    FlexNvmDataFlash flash(&invalidate);
    flash.setRegion(region());
    std::memset(&flexNvm[0], 0xFF, SECTOR);
    uint8_t buffer[8] = {};
    CHECK(flash.read(0U, buffer, 8U) == Status::OK); // word 0 in the single-entry buffer
    uint8_t const phrase[8] = {9U, 8U, 7U, 6U, 5U, 4U, 3U, 2U};
    CHECK(flash.startProgram(0U, phrase) == Status::OK);
    // the command ends, then an EEPROM store of another task starts before the poll: no flush in poll()
    while ((fakeFtfc.FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)
    {
    }
    fakeFtfc.FCNFG.value = 0U;
    CHECK(flash.poll() == Status::OK);
    CHECK(flash.bufferFlushes() == 0U);
    CHECK(fakeMscm.OCMDR[1].writes == 0U); // the controller is not idle: OCMDR1 untouched
    CHECK(flash.read(0U, buffer, 8U) == Status::BUSY);
    otherStoreEnds();
    CHECK(flash.read(0U, buffer, 8U) == Status::OK); // flushed in the first chunk, then read
    CHECK(std::memcmp(buffer, phrase, 8U) == 0);
    CHECK(flash.bufferFlushes() == 1U);
    CHECK(fmc.staleReads == 0U);
    CHECK(fmc.staleReadsOfFlush == 1U); // word 0 was stale: the first read of the flush met it, the second evicted it
    // the same for a hold: a reader in place sees the new data
    CHECK(flash.startErase(0U) == Status::OK);
    while ((fakeFtfc.FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)
    {
    }
    fakeFtfc.FCNFG.value = 0U;
    CHECK(flash.poll() == Status::OK);
    CHECK(!flash.holdForDirectRead()); // the store runs: no hold, nothing flushed
    CHECK(flash.bufferFlushes() == 1U);
    otherStoreEnds();
    CHECK(flash.holdForDirectRead());
    CHECK(flash.bufferFlushes() == 2U);
    uintptr_t const base = reinterpret_cast<uintptr_t>(flexNvm);
    for (uint32_t i = 0U; i < 8U; ++i)
    {
        CHECK(fmcRead(base + i) == 0xFFU); // read in place (the AreaService gives the address)
    }
    flash.releaseDirectRead();
    CHECK(fmc.staleReads == 0U);
    CHECK(fmc.ocmdrWritesInterruptsOn == 0U);
    CHECK(fakeCore.suspends == fakeCore.resumes);
}

void testFlushInPollForReadersInPlace()
{
    // a reader in place that holds the block right after the poll finds no stale word either
    reset();
    FlexNvmDataFlash flash(&invalidate);
    flash.setRegion(region());
    std::memset(&flexNvm[SECTOR], 0xFF, SECTOR);
    uint8_t buffer[8] = {};
    CHECK(flash.read(SECTOR, buffer, 8U) == Status::OK);
    uint8_t const phrase[8] = {0x11U, 0x22U, 0x33U, 0x44U, 0x55U, 0x66U, 0x77U, 0x88U};
    CHECK(flash.startProgram(SECTOR, phrase) == Status::OK);
    CHECK(runToEnd(flash) == Status::OK);
    CHECK(flash.bufferFlushes() == 1U); // at the end of the command, not only at the next read
    uintptr_t const base = reinterpret_cast<uintptr_t>(flexNvm);
    CHECK(fmcRead(base + SECTOR) == 0x11U);
    CHECK(fmc.staleReads == 0U);
    CHECK(flash.poll() == Status::OK);
    CHECK(flash.bufferFlushes() == 1U); // once per command
}

void testOcmdrLockedOrOtherDescriptor()
{
    // OCMDR1 locked (RO): the speculation buffer cannot be cleared - counted, the reads still evict the single entry
    reset();
    FlexNvmDataFlash flash(&invalidate);
    flash.setRegion(region());
    fakeMscm.OCMDR[1].value = OCMDR1_RESET | MSCM_OCMDR_RO_MASK;
    std::memset(&flexNvm[0], 0xFF, SECTOR);
    uint8_t buffer[8] = {};
    CHECK(flash.read(16U, buffer, 8U) == Status::OK);
    uint8_t const phrase[8] = {1U, 1U, 1U, 1U, 1U, 1U, 1U, 1U};
    CHECK(flash.startProgram(16U, phrase) == Status::OK);
    CHECK(runToEnd(flash) == Status::OK);
    CHECK(flash.speculationNotCleared() == 1U);
    CHECK(fakeMscm.OCMDR[1].value == (OCMDR1_RESET | MSCM_OCMDR_RO_MASK));
    CHECK(flash.read(16U, buffer, 8U) == Status::OK);
    CHECK(std::memcmp(buffer, phrase, 8U) == 0);
    // a descriptor that is not the data flash (OCMT 110b, EEE): never written
    reset();
    FlexNvmDataFlash other(&invalidate);
    other.setRegion(region());
    fakeMscm.OCMDR[1].value = 0xC304D000U;
    CHECK(other.startErase(0U) == Status::OK);
    CHECK(runToEnd(other) == Status::OK);
    CHECK(fakeMscm.OCMDR[1].writes == 0U);
    CHECK(other.speculationNotCleared() == 1U);
    CHECK(other.bufferFlushes() == 1U);
}
} // namespace

int main()
{
    testStates();
    testRangeAndAlignment();
    testEraseAndProgram();
    testLocks();
    testErrorsAndTimeout();
    testLongest();
    testEepromWaitsForCommand();
    testReadUnderLock();
    testHoldForDirectRead();
    testLockAlone();
    testBoardSequence20261001();
    testEraseThenSameDataIsProgrammed();
    testProgramNextWordAfterSpeculation();
    testFlushDeferredWhileStoreRuns();
    testFlushInPollForReadersInPlace();
    testOcmdrLockedOrOtherDescriptor();
    std::printf("FlexNvmDataFlashHostTest: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
