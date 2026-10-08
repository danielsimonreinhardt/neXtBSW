/**
 * Flash writes that suspend the ROM check (SM-14, SYS-44).
 *
 * The ROM check computes a CRC over the program flash. A write to a flash module while a pass runs - an EEPROM
 * record written through the FlexRAM, a data-flash or program-flash command of a later in-application update - must
 * never end in a false ROM_CHECK_FAILURE, which holds the ECU in the safe state until a power cycle. Every writer
 * brackets its write with FlashWriteScope (or begin()/end()); RomCheck does not start a pass while a write is open
 * and discards a pass during which a write began (it runs again, it is never reported).
 *
 * The indicator is two counters (writes begun, writes ended) in ordinary RAM: the object is defined in the library
 * romCheckFlashWrites, whose name does not match the MPU protected sections of the linker script ("*libsafe*"), so
 * every task may write it without access to the protected safety RAM. A wild write into it can only postpone the
 * check (begun != ended); RomCheck bounds that postponement by its period and reports ROM_CHECK_FAILURE when it is
 * exceeded, so the indicator cannot switch SM-14 off silently.
 */

#pragma once

#include <cstdint>

namespace safety
{
class RomCheckFlashWrites
{
public:
    struct Snapshot
    {
        uint32_t begun;
        uint32_t ended;

        /** A write is open (or the indicator was corrupted). */
        bool writing() const { return begun != ended; }
    };

    /** A flash write starts. Any task, any interrupt; nests. */
    void begin();

    /** The flash write started by the matching begin() is finished (successfully or not). */
    void end();

    /** Both counters, read together. */
    Snapshot read() const;

private:
    uint32_t volatile _begun = 0U;
    uint32_t volatile _ended = 0U;
};

/** The one indicator of the image (library romCheckFlashWrites, ordinary RAM). */
extern RomCheckFlashWrites romCheckFlashWrites;

/** Brackets one flash write (RAII). */
class FlashWriteScope final
{
public:
    FlashWriteScope() { romCheckFlashWrites.begin(); }

    ~FlashWriteScope() { romCheckFlashWrites.end(); }

    FlashWriteScope(FlashWriteScope const&)            = delete;
    FlashWriteScope& operator=(FlashWriteScope const&) = delete;
};

} // namespace safety
