// NVM-12: family-neutral contract of the data flash - the part of a flash module that an application may erase and
// program at run time (s32k1xx: the FlexNVM share the partition leaves as data flash; S32K3: the data flash block;
// rm4x: a bank of the F021 flash). Implemented per family under platforms/<family>/bsp (s32k1xx:
// flexnvm/FlexNvmDataFlash.h); the areas the project reserves in it and the jobs over them are dataflash::AreaService
// (dataflash/DataFlashAreas.h).
//
// Why not OpenBSW's flash::IFlashDriver: it is blocking (an erase of one data flash sector takes up to 130 ms on the
// S32K1, data sheet), it has no read and no "a command is running" answer. Here every operation returns at once:
//  - read() copies like a memory read, but only while no flash command runs on the block - a read during a command
//    (erase, program, an EEPROM store of an emulated EEPROM that shares the block) returns undefined data;
//  - startErase()/startProgram() launch ONE command and return; poll() says when it has ended and how;
//  - NVM-25 (safety review NVM12-S1 b): a reader that uses the data in place (through a pointer, without a copy)
//    first holds the block - holdForDirectRead()/releaseDirectRead(), as RAII dataflash::DirectRead. While held,
//    nothing that shares the block starts (no erase, no program, no EEPROM store of an emulated EEPROM); a hold is
//    only granted while none of them runs. The same lock keeps the commands out of a read() in progress.
// Addresses are offsets in the usable data flash (0 = geometry().start). The implementation refuses what the flash
// module would refuse (range, alignment, a command already running) before it launches anything.

#pragma once

#include <cstdint>

namespace dataflash
{

enum class Status : uint8_t
{
    OK,            // done (or accepted, for startErase/startProgram); poll(): no command pending or it succeeded
    BUSY,          // a flash command runs on the block (this one, another one, an EEPROM store): try again later
    RANGE,         // outside the usable data flash, length 0 or no buffer
    ALIGNMENT,     // erase not on a sector, program not on a program unit
    NOT_AVAILABLE, // no usable data flash: no region, or the chip is partitioned differently than the project says
    FAILED,        // the flash module reported an error (access, protection, verify), or the read-back differs
    TIMEOUT,       // the command did not end within the limit of the data sheet
    NOT_ERASED     // AreaService: the target of a program is not erased (programming over data is not allowed)
};

/** Usable data flash: memory address of offset 0, size, erase unit and program unit (bytes). */
struct Geometry
{
    uintptr_t start;
    uint32_t size;        // 0: no usable data flash
    uint32_t sectorSize;  // erase unit
    uint32_t programSize; // program unit (s32k1xx: phrase of 8 bytes)
};

class IDataFlash
{
public:
    IDataFlash(IDataFlash const&)            = delete;
    IDataFlash& operator=(IDataFlash const&) = delete;

    virtual Geometry geometry() const = 0;

    /** Copies \p length bytes at \p offset; BUSY while a flash command runs on the block (nothing copied reliably). */
    virtual Status read(uint32_t offset, uint8_t* buffer, uint32_t length) = 0;

    /** Launches the erase of the sector at \p offset (sector aligned). */
    virtual Status startErase(uint32_t offset) = 0;

    /** Launches the program of one program unit (geometry().programSize bytes of \p data) at \p offset. */
    virtual Status startProgram(uint32_t offset, uint8_t const* data) = 0;

    /**
     * BUSY while the command launched last runs; afterwards once its result (OK, FAILED, TIMEOUT), then OK. A
     * command beyond its time limit is reported as TIMEOUT and no longer waited for; the next start returns BUSY
     * until the flash module is ready again.
     */
    virtual Status poll() = 0;

    /**
     * NVM-25: holds the block for a read in place: true when nothing runs on it - then nothing starts until
     * releaseDirectRead() (stores of a shared emulated EEPROM wait, startErase/startProgram answer BUSY); false and
     * nothing held otherwise, or without usable data flash. From a task, short, never across a blocking call.
     */
    virtual bool holdForDirectRead() = 0;

    /** Ends a hold granted by holdForDirectRead() (once per granted hold). */
    virtual void releaseDirectRead() = 0;

protected:
    constexpr IDataFlash() = default;
    ~IDataFlash()          = default;
};

/**
 * NVM-25: scope of a read in place. Holds the block for its lifetime if the block is free (held()); address() gives
 * the memory address of a range of the usable data flash only while held, 0 otherwise - the caller reads through it
 * within the scope and nowhere else. Not copyable: one scope, one hold, one release.
 */
class DirectRead
{
public:
    explicit DirectRead(IDataFlash& flash) : _flash(flash), _held(flash.holdForDirectRead()) {}

    ~DirectRead()
    {
        if (_held)
        {
            _flash.releaseDirectRead();
        }
    }

    DirectRead(DirectRead const&)            = delete;
    DirectRead& operator=(DirectRead const&) = delete;

    bool held() const { return _held; }

    /** Memory address of \p length bytes at \p offset of the usable data flash; 0 if not held or outside. */
    uintptr_t address(uint32_t const offset, uint32_t const length) const
    {
        if (!_held)
        {
            return 0U;
        }
        Geometry const g = _flash.geometry();
        if ((length == 0U) || (offset >= g.size) || (length > (g.size - offset)))
        {
            return 0U;
        }
        return g.start + offset;
    }

    /** The flash this scope holds (AreaService checks that a scope belongs to its flash). */
    IDataFlash const& flash() const { return _flash; }

private:
    IDataFlash& _flash;
    bool const _held;
};

} // namespace dataflash
