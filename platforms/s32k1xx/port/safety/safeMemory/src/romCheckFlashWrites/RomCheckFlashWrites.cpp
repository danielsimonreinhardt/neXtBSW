/* SYS-44: the flash write indicator of the ROM check (safeMemory/RomCheckFlashWrites.h). Its own library
 * (romCheckFlashWrites): an object of a libsafe* archive would land in the MPU protected RAM, which the writers (NvM
 * task, console, a later updater) must not write. */

#include "safeMemory/RomCheckFlashWrites.h"

#include <interrupts/SuspendResumeAllInterruptsScopedLock.h>

namespace safety
{
// Zero at start-up (.bss, cleared by the startup code): no write open
RomCheckFlashWrites romCheckFlashWrites;

void RomCheckFlashWrites::begin()
{
    // several writers in different tasks: the read-modify-write must not lose a count, or the check would stay
    // postponed until its deadline reports a failure
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    _begun = _begun + 1U;
}

void RomCheckFlashWrites::end()
{
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    _ended = _ended + 1U;
}

RomCheckFlashWrites::Snapshot RomCheckFlashWrites::read() const
{
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    return Snapshot{_begun, _ended};
}

} // namespace safety
