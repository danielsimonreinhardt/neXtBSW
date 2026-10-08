// NVM-12: the data flash of the S32K1 family (the FlexNVM share the partition leaves as data flash) as
// dataflash::IDataFlash (libs/bsw/nvmDataFlash). Header only, like flexnvm/FlexNvmEeprom.h.
//
// Read like memory; erase one sector (FTFC Erase Flash Sector 0x09) and program one phrase of 8 bytes (Program Phrase
// 0x07), each launched and left running - poll() reports the end. The locks that hold while the flash module works
// ([RM13] 36.5.7 "Read while write", 36.1.2):
//  - code: the MCU may fetch instructions from the program flash while a data flash command runs, so the command is
//    launched from the program flash and interrupts stay enabled during it (no RAM function, unlike PGMPART or a
//    program flash write); no code may run from the FlexNVM (nothing is linked there);
//  - the FlexNVM block does one thing at a time: read, program, erase, EEPROM store or CSEc command. A command is
//    therefore launched only with the module idle (FSTAT CCIF 1) and no EEPROM store running (FlexRAM plain RAM, or
//    FCNFG EEERDY 1), checked and launched with interrupts suspended for a few instructions so no store of another
//    task slips in between; FlexNvmEeprom waits for the end of a data flash command before it stores (it checks CCIF
//    the same way). read() returns BUSY in the same cases and when the module flags a read collision (RDCOLERR);
//  - NVM-25 (safety review NVM12-S1 b): the block lock shared with the EEPROM driver (flexnvm/FlexNvmBlockLock.h).
//    read() copies chunk by chunk (READ_CHUNK_BYTES) with interrupts suspended and the block idle, and keeps every
//    data flash command out while it runs; holdForDirectRead() lets a reader use the data in place through a pointer
//    (dataflash::DirectRead) while no EEPROM store and no command can start. A launch is refused (BUSY) while a read
//    or a hold is open;
//  - interrupts: no interrupt handler may read the data flash or store into the emulated EEPROM (both would
//    collide); the data flash is accessed from tasks only;
//  - SYS-44: the ROM check sees a running command itself (CCIF 0); dataflash::AreaService brackets every command
//    with the write hooks of the integration (safety::romCheckFlashWrites).
// The code cache (LMEM, S32K14x) caches the FlexNVM region write-through by default ([RM13] 33.3.1.6, PCCRMR R2
// reset 10b): after every command the driver calls the invalidate hook the integration hands in (cacheInvalidate()
// on derivatives with LMEM, nothing on S32K11x) so a read after an erase or program does not see old lines.
// NVM-29: the flash memory controller (FMC, [RM13] chapter 35) has buffers of its own for the FlexNVM (bank 1), on
// from reset and independent of the LMEM: a single-entry buffer that keeps the last flash word read (64 bit; 128 bit
// on the S32K148) and a speculation buffer that prefetches the next word. Neither learns of an FTFC command. Board
// 2026-10-01: the erased check of a phrase left it in the single-entry buffer, the read-back after Program Phrase was
// served from there (still ff, "failed"), and so was every later read of that phrase until another word of the
// FlexNVM was read (nothing else reads bank 1; code runs from bank 0). The same stale word can also make a write skip
// a unit that the command before has erased (it still shows the old bytes) - a silent wrong result.
// After every command the driver therefore flushes these buffers before the FlexNVM is read again, with interrupts
// suspended and the block idle (the reference manual asks for an idle controller while OCMDRn of its memory changes):
//  - speculation off for bank 1 (MSCM OCMDR1 OCM1 = 11b: all speculation disabled and the speculation buffer
//    cleared, [RM13] 34.4.2.19), read back (the write has taken effect before the flash is read);
//  - two reads of different words of the FlexNVM (its start and BUFFER_ENTRY_MAX_BYTES further): the single-entry
//    buffer then holds a word read after the command, whatever it held before (the reference manual offers no other
//    way to empty it);
//  - OCMDR1 back to the value before (speculation as configured).
// The flush runs at the end of the command in poll() when the block is idle, otherwise (an EEPROM store started in
// between) before the next read() chunk or direct-read hold: the FlexNVM is never read through the driver while a
// word of before the last command may be in a buffer.
//
// Usable data flash: offset 0 is the start of the FlexNVM (the data flash is its low part, the EEPROM backup the high
// part), the size is what the project's partition leaves (generated NX_DFLASH_SIZE), never more. The chip must show
// that partition (SIM FCFG1 DEPART = the generated code) or be not partitioned at all (DEPART 0xF: the whole FlexNVM
// is data flash); any other partition refuses everything (WRONG_PARTITION) - its data flash may be smaller.
// Command time limits: the longest time of the data sheet for every S32K1 derivative ([DS13] Tables 35/37: Erase
// Flash Sector 130 ms, Program Phrase 225 us) times two; a command beyond it is reported as TIMEOUT. NVM-29: the limit
// is checked at poll(): a command that ends before the next poll is never a time-out, however long it took - with a
// poll period above the limit (10 ms job step against 450 us for Program Phrase) only a command still running at
// that poll is caught.

#pragma once

#include "flexnvm/FlexNvmBlockLock.h"
#include "mcu/mcu.h"

#include <bsp/timer/SystemTimer.h>
#include <dataflash/IDataFlash.h>
#include <interrupts/suspendResumeAllInterrupts.h>

#include <cstdint>

namespace platform
{

#ifndef NX_FLEXNVM_READ_MODEL
/** One byte of the FlexNVM (the host test replaces it with its model of the FMC buffers, stubs/mcu/mcu.h). */
inline uint8_t readFlexNvm(uintptr_t const address)
{
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast, performance-no-int-to-ptr): data flash
    return *reinterpret_cast<uint8_t const volatile*>(address);
}
#endif

class FlexNvmDataFlash final : public ::dataflash::IDataFlash
{
public:
    enum class State : uint8_t
    {
        READY,          // the chip shows the project's partition (or none): the data flash can be used
        NO_REGION,      // no data flash was handed in (project without nvm.data_flash, or size 0)
        WRONG_PARTITION // the chip is partitioned differently than the project says
    };

    static constexpr uint8_t CMD_PROGRAM_PHRASE = 0x07U;   // [RM13] 36.5.11.4
    static constexpr uint8_t CMD_ERASE_SECTOR   = 0x09U;   // [RM13] 36.5.11.6
    static constexpr uint32_t PHRASE_BYTES      = 8U;      // Program Phrase, 64-bit aligned
    static constexpr uint32_t DATA_FLASH_SELECT = 0x800000U; // FCCOB address bit 23: data flash ([RM13] 36.5.11)
    static constexpr uint8_t NOT_PARTITIONED    = 0xFU;    // SIM FCFG1 DEPART erased
    static constexpr uint32_t ERASE_SECTOR_LIMIT_US   = 2U * 130000U; // [DS13] Tables 35/37 max x2
    static constexpr uint32_t PROGRAM_PHRASE_LIMIT_US = 2U * 225U;    // [DS13] Tables 35/37 max x2
    // NVM-25: bytes copied per section with interrupts suspended (a few microseconds of interrupt latency)
    static constexpr uint32_t READ_CHUNK_BYTES = 32U;
    // NVM-29: FMC buffers of the FlexNVM - descriptor of the data flash in the MSCM ([RM13] 34.4.2.19: OCMDR1, type
    // OCMT 101b) and the largest buffer entry of the family (64 bit, 128 bit on the S32K148, [RM13] 36.1.2 table)
    static constexpr uint32_t MSCM_DATA_FLASH_DESCRIPTOR = 1U;
    static constexpr uint32_t OCMT_DATA_FLASH            = 5U;
    static constexpr uint32_t BUFFER_ENTRY_MAX_BYTES     = 16U;

    using Hook = void (*)();

    struct Region
    {
        uintptr_t start;       // FlexNVM start (NX_DFLASH_START)
        uint32_t size;         // usable data flash (NX_DFLASH_SIZE), 0: none
        uint32_t sectorSize;   // NX_DFLASH_SECTOR_SIZE
        uint8_t partitionCode; // DEPART the project writes (NX_DFLASH_PARTITION_CODE)
    };

    constexpr explicit FlexNvmDataFlash(Hook const invalidateCache = nullptr)
    : _region{0U, 0U, 0U, NOT_PARTITIONED}
    , _invalidateCache(invalidateCache)
    , _pending(false)
    , _started(0U)
    , _limitUs(0U)
    , _longestEraseUs(0U)
    , _longestProgramUs(0U)
    , _timeouts(0U)
    , _collisions(0U)
    , _heldOff(0U)
    , _bufferFlushes(0U)
    , _speculationNotCleared(0U)
    , _buffersStale(false)
    , _lastCommand(0U)
    {}

    /** Usable data flash of the project (generated nvm/nvmDataFlash.h). */
    void setRegion(Region const& region) { _region = region; }

    /** SIM FCFG1 DEPART (0xF: not partitioned). */
    static uint8_t departCode()
    {
        return static_cast<uint8_t>((SIM->FCFG1 & SIM_FCFG1_DEPART_MASK) >> SIM_FCFG1_DEPART_SHIFT);
    }

    State state() const
    {
        if ((_region.size == 0U) || (_region.sectorSize == 0U) || (_region.start == 0U))
        {
            return State::NO_REGION;
        }
        uint8_t const depart = departCode();
        if ((depart != NOT_PARTITIONED) && (depart != _region.partitionCode))
        {
            return State::WRONG_PARTITION;
        }
        return State::READY;
    }

    static char const* stateText(State const state)
    {
        switch (state)
        {
            case State::READY: return "data flash ready";
            case State::NO_REGION: return "no data flash in the project (nvm.data_flash)";
            case State::WRONG_PARTITION: return "chip partitioned differently than the project (DEPART)";
            default: return "?";
        }
    }

    ::dataflash::Geometry geometry() const override
    {
        if (state() != State::READY)
        {
            return ::dataflash::Geometry{_region.start, 0U, _region.sectorSize, PHRASE_BYTES};
        }
        return ::dataflash::Geometry{_region.start, _region.size, _region.sectorSize, PHRASE_BYTES};
    }

    ::dataflash::Status read(uint32_t const offset, uint8_t* const buffer, uint32_t const length) override
    {
        ::dataflash::Status const s = allowed(offset, length);
        if (s != ::dataflash::Status::OK)
        {
            return s;
        }
        if (buffer == nullptr)
        {
            return ::dataflash::Status::RANGE;
        }
        if (!flexNvmBlockLock.beginRead())
        {
            return ::dataflash::Status::BUSY; // a command or an EEPROM store runs
        }
        uintptr_t const source = _region.start + offset;
        uint32_t done          = 0U;
        while (done < length)
        {
            uint32_t const left = length - done;
            uint32_t const n    = (left < READ_CHUNK_BYTES) ? left : READ_CHUNK_BYTES;
            // NVM-25: no store and no command can start inside a chunk; one that started between two chunks
            // (another task) is seen here and ends the read
            uint32_t const old = getMachineStateRegisterValueAndSuspendAllInterrupts();
            if (!idle())
            {
                resumeAllInterrupts(old);
                flexNvmBlockLock.endRead();
                return ::dataflash::Status::BUSY;
            }
            if (_buffersStale)
            {
                flushReadBuffers(); // NVM-29: the end of the last command was polled while an EEPROM store ran
            }
            for (uint32_t i = done; i < (done + n); ++i)
            {
                buffer[i] = readFlexNvm(source + i);
            }
            resumeAllInterrupts(old);
            done += n;
        }
        flexNvmBlockLock.endRead();
        if ((FTFC->FSTAT & FTFC_FSTAT_RDCOLERR_MASK) != 0U)
        {
            FTFC->FSTAT = FTFC_FSTAT_RDCOLERR_MASK; // write 1 to clear the collision flag only
            ++_collisions;
            return ::dataflash::Status::BUSY;
        }
        return ::dataflash::Status::OK;
    }

    ::dataflash::Status startErase(uint32_t const offset) override
    {
        ::dataflash::Status const s = allowed(offset, _region.sectorSize);
        if (s != ::dataflash::Status::OK)
        {
            return s;
        }
        if ((offset % _region.sectorSize) != 0U)
        {
            return ::dataflash::Status::ALIGNMENT;
        }
        return launch(CMD_ERASE_SECTOR, offset, nullptr, ERASE_SECTOR_LIMIT_US);
    }

    ::dataflash::Status startProgram(uint32_t const offset, uint8_t const* const data) override
    {
        ::dataflash::Status const s = allowed(offset, PHRASE_BYTES);
        if (s != ::dataflash::Status::OK)
        {
            return s;
        }
        if (data == nullptr)
        {
            return ::dataflash::Status::RANGE;
        }
        if ((offset % PHRASE_BYTES) != 0U)
        {
            return ::dataflash::Status::ALIGNMENT;
        }
        return launch(CMD_PROGRAM_PHRASE, offset, data, PROGRAM_PHRASE_LIMIT_US);
    }

    ::dataflash::Status poll() override
    {
        if (!_pending)
        {
            return ::dataflash::Status::OK;
        }
        // read the time first: a command that ends while this task was preempted still counts as finished
        uint32_t const elapsed = getSystemTimeUs32Bit() - _started; // unsigned: correct across the 32-bit wrap
        uint8_t const fstat    = FTFC->FSTAT;
        if ((fstat & FTFC_FSTAT_CCIF_MASK) == 0U)
        {
            if (elapsed > _limitUs)
            {
                _pending = false;
                ++_timeouts;
                return ::dataflash::Status::TIMEOUT;
            }
            return ::dataflash::Status::BUSY;
        }
        _pending = false;
        if (_invalidateCache != nullptr)
        {
            _invalidateCache(); // the cache may hold lines of the sector as it was before the command
        }
        flushAfterCommand();
        uint32_t& longest = (_lastCommand == CMD_ERASE_SECTOR) ? _longestEraseUs : _longestProgramUs;
        longest           = (elapsed > longest) ? elapsed : longest;
        if ((fstat & (FTFC_FSTAT_ACCERR_MASK | FTFC_FSTAT_FPVIOL_MASK | FTFC_FSTAT_MGSTAT0_MASK)) != 0U)
        {
            return ::dataflash::Status::FAILED;
        }
        return ::dataflash::Status::OK;
    }

    /**
     * NVM-25: holds the block for a direct read in place (dataflash::DirectRead): true while no command and no EEPROM
     * store runs - then none starts until releaseDirectRead(); false (nothing held) otherwise or without usable data
     * flash.
     */
    bool holdForDirectRead() override
    {
        if (state() != State::READY)
        {
            return false;
        }
        // NVM-29: the reader uses the data in place - no word of before the last command may be left in a buffer
        uint32_t const old = getMachineStateRegisterValueAndSuspendAllInterrupts();
        bool const held    = flexNvmBlockLock.tryHold();
        if (held && _buffersStale)
        {
            flushReadBuffers(); // the hold was granted: the block is idle
        }
        resumeAllInterrupts(old);
        return held;
    }

    void releaseDirectRead() override { flexNvmBlockLock.release(); }

    /**
     * Longest sector erase / phrase program seen, in microseconds (board measurement). NVM-29: an upper bound - the
     * end is seen at the first poll() with CCIF 1, so the value is the caller's poll period when the command is
     * shorter (board 2026-10-01: about 10 ms for both with the 10 ms job step of the port).
     */
    uint32_t longestEraseUs() const { return _longestEraseUs; }

    uint32_t longestProgramUs() const { return _longestProgramUs; }

    /** Commands that did not end within their limit. */
    uint32_t timeouts() const { return _timeouts; }

    /** Reads refused because the module flagged a read collision. */
    uint32_t collisions() const { return _collisions; }

    /** NVM-25: launches refused because a read or a direct read held the block. */
    uint32_t heldOff() const { return _heldOff; }

    /** NVM-29: flushes of the FMC buffers after a command. */
    uint32_t bufferFlushes() const { return _bufferFlushes; }

    /**
     * NVM-29: flushes in which the speculation buffer could not be cleared (OCMDR1 locked by its RO bit, or the
     * descriptor is not the data flash) - only the two reads were made; expected 0.
     */
    uint32_t speculationNotCleared() const { return _speculationNotCleared; }

private:
    /** Module idle for the FlexNVM block: no command (CCIF 1) and no EEPROM store (FlexRAM RAM, or EEERDY 1). */
    static bool idle() { return FlexNvmBlockLock::blockIdle(); }

    /** NVM-29: at the end of a command - flushes now if the block is idle, else the next read or hold does it. */
    void flushAfterCommand()
    {
        uint32_t const old = getMachineStateRegisterValueAndSuspendAllInterrupts();
        if (_buffersStale && idle())
        {
            flushReadBuffers();
        }
        resumeAllInterrupts(old);
    }

    /**
     * NVM-29: empties the FMC buffers of the FlexNVM of words read before the last command (see the head of the file).
     * Only with interrupts suspended and the block idle: no read of the FlexNVM, no command and no EEPROM store runs.
     */
    void flushReadBuffers()
    {
        uint32_t const saved = IP_MSCM->OCMDR[MSCM_DATA_FLASH_DESCRIPTOR];
        bool cleared         = false;
        if (((saved & MSCM_OCMDR_OCMT_MASK) >> MSCM_OCMDR_OCMT_SHIFT) == OCMT_DATA_FLASH)
        {
            IP_MSCM->OCMDR[MSCM_DATA_FLASH_DESCRIPTOR] = saved | MSCM_OCMDR_OCM1_MASK;
            // read back: the write has taken effect (not locked) before the flash is read
            cleared = (IP_MSCM->OCMDR[MSCM_DATA_FLASH_DESCRIPTOR] & MSCM_OCMDR_OCM1_MASK) == MSCM_OCMDR_OCM1_MASK;
        }
        static_cast<void>(readFlexNvm(_region.start));
        static_cast<void>(readFlexNvm(_region.start + BUFFER_ENTRY_MAX_BYTES));
        if (cleared)
        {
            IP_MSCM->OCMDR[MSCM_DATA_FLASH_DESCRIPTOR] = saved;
        }
        else
        {
            ++_speculationNotCleared;
        }
        _buffersStale = false;
        ++_bufferFlushes;
    }

    ::dataflash::Status allowed(uint32_t const offset, uint32_t const length) const
    {
        if (state() != State::READY)
        {
            return ::dataflash::Status::NOT_AVAILABLE;
        }
        if ((length == 0U) || (offset >= _region.size) || (length > (_region.size - offset)))
        {
            return ::dataflash::Status::RANGE;
        }
        return ::dataflash::Status::OK;
    }

    ::dataflash::Status
    launch(uint8_t const command, uint32_t const offset, uint8_t const* const data, uint32_t const limitUs)
    {
        if (_pending)
        {
            return ::dataflash::Status::BUSY; // the result of the last command has not been fetched
        }
        uint32_t const address = DATA_FLASH_SELECT | offset;
        uint32_t const old     = getMachineStateRegisterValueAndSuspendAllInterrupts();
        if (!idle())
        {
            resumeAllInterrupts(old);
            return ::dataflash::Status::BUSY;
        }
        if (!flexNvmBlockLock.launchAllowed())
        {
            resumeAllInterrupts(old);
            ++_heldOff; // NVM-25: a reader is in the middle of its reads or holds the block
            return ::dataflash::Status::BUSY;
        }
        FTFC->FSTAT = static_cast<uint8_t>(FTFC_FSTAT_ACCERR_MASK | FTFC_FSTAT_FPVIOL_MASK); // write 1 to clear
        // register order of FCCOB0..3 as in the bootloader (bootloader/platforms/s32k1xx/hal_s32k1xx.c) and OpenBSW
        FTFC->FCCOB[3] = command;
        FTFC->FCCOB[2] = static_cast<uint8_t>(address >> 16U);
        FTFC->FCCOB[1] = static_cast<uint8_t>(address >> 8U);
        FTFC->FCCOB[0] = static_cast<uint8_t>(address);
        if (data != nullptr)
        {
            for (uint32_t i = 0U; i < PHRASE_BYTES; ++i)
            {
                FTFC->FCCOB[4U + i] = data[i]; // bytes in memory order, like the bootloader's Program Phrase
            }
        }
        _lastCommand  = command;
        _limitUs      = limitUs;
        _started      = getSystemTimeUs32Bit();
        _pending      = true;
        _buffersStale = true; // NVM-29: until flushed, the FMC buffers may hold words of before this command
        FTFC->FSTAT  = static_cast<uint8_t>(FTFC_FSTAT_CCIF_MASK); // launch
        resumeAllInterrupts(old);
        return ::dataflash::Status::OK;
    }

    Region _region;
    Hook const _invalidateCache;
    bool _pending;
    uint32_t _started;
    uint32_t _limitUs;
    uint32_t _longestEraseUs;
    uint32_t _longestProgramUs;
    uint32_t _timeouts;
    uint32_t _collisions;
    uint32_t _heldOff;
    uint32_t _bufferFlushes;
    uint32_t _speculationNotCleared;
    bool _buffersStale;
    uint8_t _lastCommand;
};

} // namespace platform
