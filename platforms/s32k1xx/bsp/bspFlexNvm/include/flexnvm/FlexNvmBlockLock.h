// NVM-25 (safety review NVM12-S1 b): the one lock of the FlexNVM block of the S32K1 family, shared by everything that
// uses the block - the data flash readers and the data flash commands (flexnvm/FlexNvmDataFlash.h) and the EEPROM
// stores of the NvM (flexnvm/FlexNvmEeprom.h). Header only, one instance per image (C++17 inline variable).
//
// The block does one thing at a time ([RM13] 36.5.7, 36.1.2): a read of the data flash during an erase, a program, an
// EEPROM store (FlexRAM write that the module turns into an EEPROM record) or a CSEc command returns undefined data.
// The hardware shows a running command (FSTAT CCIF 0) and a running store (FCNFG EEERDY 0 with the FlexRAM as EEPROM);
// what it cannot show is a reader in the middle of its reads. That is what this lock adds:
//  - beginRead()/endRead() (FlexNvmDataFlash::read()): a copy through the driver. It keeps every data flash command
//    out while it runs (a command would change the bytes between two chunks), and it copies chunk by chunk with
//    interrupts suspended and the block idle, so no store and no command can slip into a chunk. A store between two
//    chunks makes the next chunk find the block busy: read() answers BUSY, nothing undefined is handed back. Stores
//    never wait for such a read - no priority inversion is possible through read().
//  - tryHold()/release() (dataflash::DirectRead, IDataFlash::holdForDirectRead): a reader that uses the data in place
//    through a pointer (a map evaluated in the data flash, a calibration segment). While held, no EEPROM store and no
//    data flash command starts: FlexNvmEeprom waits (bounded, COMMAND_WAIT_LIMIT_US, then the write fails and the NvM
//    reports it), FlexNvmDataFlash answers BUSY. A hold is taken only with the block idle.
// Every count is changed and every decision (idle, holds, reads) is taken with interrupts suspended for a few
// instructions; a store or a launch checks the lock in the same suspended section in which it starts.
//
// Rules for the users of a hold (assumption of use, nvmDataFlash.md): from a task only (never in an interrupt
// handler), short (well below the wait of the store, COMMAND_WAIT_LIMIT_US), never across a blocking call, and from a
// task whose priority is not below that of a task that stores into the emulated EEPROM - a higher task that stores
// busy-waits for the hold and would starve a lower holder until its time limit (the write then fails, it never
// collides). Not covered by the lock: CSEc commands (bspCsec, test builds with the user only; they check CCIF and run
// with interrupts suspended, so they never overlap a chunk of read(), but they do not see a hold) and PGMPART
// (flexnvm/FlexNvmPartition.h, once, with the user).

#pragma once

#include "mcu/mcu.h"

#include <interrupts/suspendResumeAllInterrupts.h>

#include <cstdint>

namespace platform
{

class FlexNvmBlockLock final
{
public:
    static constexpr uint8_t MAX_COUNT = 0xFFU; // reads/holds at the same time (nested tasks), more are refused

    constexpr FlexNvmBlockLock() : _reads(0U), _holds(0U), _refusedHolds(0U), _unbalanced(0U) {}

    /** No command (CCIF 1) and no EEPROM store (FlexRAM plain RAM, or EEERDY 1) on the block - hardware view. */
    static bool blockIdle()
    {
        if ((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)
        {
            return false;
        }
        uint8_t const fcnfg = FTFC->FCNFG;
        return (fcnfg & (FTFC_FCNFG_RAMRDY_MASK | FTFC_FCNFG_EEERDY_MASK)) != 0U;
    }

    /** read() of the driver begins: false (nothing counted) while the block is busy. */
    bool beginRead()
    {
        uint32_t const old = getMachineStateRegisterValueAndSuspendAllInterrupts();
        bool const ok      = blockIdle() && (_reads < MAX_COUNT);
        if (ok)
        {
            ++_reads;
        }
        resumeAllInterrupts(old);
        return ok;
    }

    void endRead()
    {
        uint32_t const old = getMachineStateRegisterValueAndSuspendAllInterrupts();
        if (_reads != 0U)
        {
            --_reads;
        }
        else
        {
            ++_unbalanced;
        }
        resumeAllInterrupts(old);
    }

    /** Direct read in place: true holds off every store and command until release(); false (nothing held) if busy. */
    bool tryHold()
    {
        uint32_t const old = getMachineStateRegisterValueAndSuspendAllInterrupts();
        bool const ok      = blockIdle() && (_holds < MAX_COUNT);
        if (ok)
        {
            ++_holds;
        }
        else
        {
            ++_refusedHolds;
        }
        resumeAllInterrupts(old);
        return ok;
    }

    void release()
    {
        uint32_t const old = getMachineStateRegisterValueAndSuspendAllInterrupts();
        if (_holds != 0U)
        {
            --_holds;
        }
        else
        {
            ++_unbalanced;
        }
        resumeAllInterrupts(old);
    }

    /** EEPROM store (FlexNvmEeprom): with interrupts suspended, right before the FlexRAM write. */
    bool storeAllowed() const { return _holds == 0U; }

    /** Data flash command (FlexNvmDataFlash): with interrupts suspended, right before the launch. */
    bool launchAllowed() const { return (_holds == 0U) && (_reads == 0U); }

    uint8_t holds() const { return _holds; }

    uint8_t reads() const { return _reads; }

    /** Holds refused because the block was busy (or too many). */
    uint32_t refusedHolds() const { return _refusedHolds; }

    /** endRead()/release() without a matching begin - a programming error of a caller, kept for the console. */
    uint32_t unbalanced() const { return _unbalanced; }

private:
    uint8_t _reads;
    uint8_t _holds;
    uint32_t _refusedHolds;
    uint32_t _unbalanced;
};

/** The lock of the FlexNVM block (one block per chip, one lock per image). */
inline FlexNvmBlockLock flexNvmBlockLock;

} // namespace platform
