// NVM-02/NVM-07: FlexNVM EEPROM emulation of the S32K1 family as the virtual EEPROM of the NvM (eeprom::IEepromDriver).
// Family module bspFlexNvm (platforms/s32k1xx/bsp/bspFlexNvm, moved from the port with NVM-07).
//
// Uses the emulated EEPROM only if it already exists: SIM->FCFG1 says the FlexNVM is partitioned with an EEPROM
// backup (DEPART) and an EEPROM size (EEERAMSIZE), and FTFC->FCNFG.EEERDY says the FlexRAM holds the EEPROM data.
// This driver launches no flash command at all - no PGMPART (irreversible partitioning, only a chip erase undoes it)
// and no SETRAM. init() therefore refuses: preparing the memory is a separate, explicitly confirmed action with the
// user at the board (flexnvm/FlexNvmPartition.h, console "flexnvm partition" of a test build). For the NvM to work
// without any command at every start, the partition has to be programmed with "FlexRAM loaded with EEPROM data during
// reset" (PGMPART FCCOB3 = 0, which the IFR then shows as EEERST = 1 - opposite encodings); otherwise EEERDY stays
// clear and the NvM runs on its defaults, which "nvm status" reports.
//
// Reads copy from the FlexRAM window; writes store only bytes that differ (aligned words where four bytes change
// together) and wait for EEERDY after each store. NVM-07: the wait is bounded in time, not in polls - the limit is the
// longest write of the partition from the data sheet times the factor for the first writes after a reset
// (NX_NVM_WRITE_LIMIT_US of the generated nvm/nvmPartition.h, tools/nxconfig/nvm.py), measured with the system timer,
// so it does not depend on the core clock. The longest write seen is kept for the board measurement ("flexnvm show").
// The window (start, size) is the derivative's memory region of kind eeprom_ram, handed in by the integration from
// the generated RTE (rte::NVM_WINDOW_START/SIZE); the usable size is the EEPROM size the partition reports, at most the
// window.
// NVM-12: a data flash command (flexnvm/FlexNvmDataFlash.h) may run on the same FlexNVM block, and a store into the
// emulated EEPROM during it is not possible ([RM13] 36.5.7). Every store is therefore made with interrupts suspended
// for a few instructions, only while no flash command runs (FSTAT CCIF 1); otherwise the driver waits for the end of
// the command, bounded by the longest data flash command of the family (COMMAND_WAIT_LIMIT_US).
// NVM-25 (safety review NVM12-S1 b): the same section also checks the block lock (flexnvm/FlexNvmBlockLock.h): while a
// reader holds the data flash for a read in place, no store starts - the driver waits like for a command, within the
// same limit (holdWaits() counts these waits), and fails the write after it.

#pragma once

#include "flexnvm/FlexNvmBlockLock.h"
#include "mcu/mcu.h"

#include <bsp/eeprom/IEepromDriver.h>
#include <bsp/timer/SystemTimer.h>
#include <interrupts/suspendResumeAllInterrupts.h>

#include <cstdint>

namespace platform
{

class FlexNvmEeprom final : public ::eeprom::IEepromDriver
{
public:
    enum class State : uint8_t
    {
        READY,           // partitioned with EEPROM, FlexRAM holds the EEPROM data
        NO_WINDOW,       // the derivative has no FlexRAM window (or it was not handed in)
        NOT_PARTITIONED, // FlexNVM without EEPROM partition (DEPART or EEERAMSIZE 0xF): chip as delivered
        NOT_LOADED       // partitioned, but EEERDY clear: FlexRAM not loaded with the EEPROM data
    };

    // Time limit of one store without a generated value (derivative without write times in mcudb): above the longest
    // write of every S32K1 derivative (S32K148 64 KB backup: 4500 us, x2 for the first writes after a reset).
    static constexpr uint32_t DEFAULT_WRITE_LIMIT_US = 10000U;

    // NVM-12: longest wait for the end of a data flash command before a store: Erase Flash Sector of every S32K1
    // derivative ([DS13] Tables 35/37: 130 ms) x2, as FlexNvmDataFlash::ERASE_SECTOR_LIMIT_US
    static constexpr uint32_t COMMAND_WAIT_LIMIT_US = 2U * 130000U;

    // constexpr: constant initialisation, no constructor at start-up (and nothing linked when no NvM uses it)
    constexpr explicit FlexNvmEeprom(uint32_t const writeLimitUs = DEFAULT_WRITE_LIMIT_US)
    : _window(0U)
    , _windowSize(0U)
    , _writeLimitUs(writeLimitUs)
    , _longestWriteUs(0U)
    , _timeouts(0U)
    , _commandWaits(0U)
    , _holdWaits(0U)
    {}

    FlexNvmEeprom(FlexNvmEeprom const&)            = delete;
    FlexNvmEeprom& operator=(FlexNvmEeprom const&) = delete;

    /** FlexRAM window of the derivative (rte::NVM_WINDOW_START/SIZE). */
    void setWindow(uintptr_t const start, uint32_t const size)
    {
        _window     = start;
        _windowSize = size;
    }

    /** FlexNVM partition code (SIM FCFG1 DEPART, 0xF: not partitioned). */
    static uint8_t departCode() { return static_cast<uint8_t>((SIM->FCFG1 & SIM_FCFG1_DEPART_MASK) >> SIM_FCFG1_DEPART_SHIFT); }

    /** EEPROM size code (SIM FCFG1 EEERAMSIZE, 0xF: 0 bytes). */
    static uint8_t eeeSizeCode()
    {
        return static_cast<uint8_t>((SIM->FCFG1 & SIM_FCFG1_EEERAMSIZE_MASK) >> SIM_FCFG1_EEERAMSIZE_SHIFT);
    }

    /** Bytes of the code ([RM] SIM FCFG1: 0010b 4 KB, 0011b 2 KB ... 1001b 32 B, 1111b 0 B; others reserved). */
    static uint32_t eeeSizeBytes(uint8_t const code)
    {
        return ((code >= 2U) && (code <= 9U)) ? (4096U >> (code - 2U)) : 0U;
    }

    State state() const
    {
        if ((_window == 0U) || (_windowSize == 0U))
        {
            return State::NO_WINDOW;
        }
        if ((departCode() == 0xFU) || (eeeSizeBytes(eeeSizeCode()) == 0U))
        {
            return State::NOT_PARTITIONED;
        }
        if ((FTFC->FCNFG & FTFC_FCNFG_EEERDY_MASK) == 0U)
        {
            return State::NOT_LOADED;
        }
        return State::READY;
    }

    static char const* stateText(State const state)
    {
        switch (state)
        {
            case State::READY: return "FlexNVM EEPROM emulation ready";
            case State::NO_WINDOW: return "no FlexRAM window";
            case State::NOT_PARTITIONED: return "FlexNVM not partitioned for EEPROM (no PGMPART yet)";
            case State::NOT_LOADED: return "FlexRAM not loaded with EEPROM data (EEERDY 0)";
            default: return "?";
        }
    }

    /** Usable bytes: the partitioned EEPROM size, at most the window; 0 unless READY. */
    uint32_t usableBytes() const
    {
        if (state() != State::READY)
        {
            return 0U;
        }
        uint32_t const bytes = eeeSizeBytes(eeeSizeCode());
        return (bytes < _windowSize) ? bytes : _windowSize;
    }

    /** Time limit of one store in microseconds. */
    uint32_t writeLimitUs() const { return _writeLimitUs; }

    /** Longest store seen since the start, in microseconds (board measurement of NVM-02/IO-32). */
    uint32_t longestWriteUs() const { return _longestWriteUs; }

    /** Stores that did not finish within the time limit (or found a flash command running beyond its limit). */
    uint32_t timeouts() const { return _timeouts; }

    /** NVM-12: stores that had to wait for the end of a data flash command (NVM-25: or of a hold). */
    uint32_t commandWaits() const { return _commandWaits; }

    /** NVM-25: stores whose wait began because a reader held the data flash (part of commandWaits()). */
    uint32_t holdWaits() const { return _holdWaits; }

    /** Never partitions and never launches a flash command (see above). */
    ::bsp::BspReturnCode init() override { return ::bsp::BSP_ERROR; }

    ::bsp::BspReturnCode read(uint32_t const address, uint8_t* const buffer, uint32_t const length) override
    {
        if (!allowed(address, buffer, length))
        {
            return ::bsp::BSP_ERROR;
        }
        uint8_t volatile const* const source = window(address);
        for (uint32_t i = 0U; i < length; ++i)
        {
            buffer[i] = source[i];
        }
        if ((FTFC->FSTAT & FTFC_FSTAT_RDCOLERR_MASK) != 0U)
        {
            FTFC->FSTAT = FTFC_FSTAT_RDCOLERR_MASK; // clear the read collision flag only (write 1 to clear)
            return ::bsp::BSP_ERROR;
        }
        return ::bsp::BSP_OK;
    }

    ::bsp::BspReturnCode write(uint32_t const address, uint8_t const* const buffer, uint32_t const length) override
    {
        if (!allowed(address, buffer, length))
        {
            return ::bsp::BSP_ERROR;
        }
        uint32_t i = 0U;
        while (i < length)
        {
            uint32_t const at = address + i;
            if ((((_window + at) & 3U) == 0U) && ((length - i) >= 4U))
            {
                // one EEPROM record for four bytes instead of four records (wear of the backup)
                uint32_t const value = static_cast<uint32_t>(buffer[i])
                                       | (static_cast<uint32_t>(buffer[i + 1U]) << 8U)
                                       | (static_cast<uint32_t>(buffer[i + 2U]) << 16U)
                                       | (static_cast<uint32_t>(buffer[i + 3U]) << 24U);
                // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast): FlexRAM window (MMIO)
                uint32_t volatile* const word = reinterpret_cast<uint32_t volatile*>(_window + at);
                if ((*word != value) && !store(word, value))
                {
                    return ::bsp::BSP_ERROR;
                }
                i += 4U;
                continue;
            }
            uint8_t volatile* const byte = window(at);
            if ((*byte != buffer[i]) && !store(byte, buffer[i]))
            {
                return ::bsp::BSP_ERROR;
            }
            ++i;
        }
        return ::bsp::BSP_OK;
    }

private:
    uint8_t volatile* window(uint32_t const address) const
    {
        // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast, performance-no-int-to-ptr): FlexRAM window
        return reinterpret_cast<uint8_t volatile*>(_window + address);
    }

    bool allowed(uint32_t const address, uint8_t const* const buffer, uint32_t const length) const
    {
        uint32_t const usable = usableBytes(); // 0 unless READY
        return (buffer != nullptr) && (length != 0U) && (address < usable) && (length <= (usable - address));
    }

    /**
     * NVM-12: stores \p value into the FlexRAM word or byte \p target once no flash command runs (CCIF 1, checked
     * and stored with interrupts suspended, so no data flash command of another task starts in between), then waits
     * for EEERDY. NVM-25: and no reader holds the block (flexNvmBlockLock, same section). False after the time
     * limits.
     */
    template<typename T>
    bool store(T volatile* const target, T const value)
    {
        bool waiting       = false;
        uint32_t waitStart = 0U;
        while (true)
        {
            uint32_t const old = getMachineStateRegisterValueAndSuspendAllInterrupts();
            if (((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) != 0U) && flexNvmBlockLock.storeAllowed())
            {
                uint32_t const start = getSystemTimeUs32Bit();
                *target              = value;
                resumeAllInterrupts(old);
                return waitReady(start);
            }
            bool const held = !flexNvmBlockLock.storeAllowed();
            resumeAllInterrupts(old);
            uint32_t const now = getSystemTimeUs32Bit();
            if (!waiting)
            {
                waiting   = true;
                waitStart = now;
                ++_commandWaits;
                _holdWaits += held ? 1U : 0U;
            }
            else if ((now - waitStart) > COMMAND_WAIT_LIMIT_US)
            {
                ++_timeouts;
                return false;
            }
        }
    }

    /** Waits for EEERDY of the store begun at \p start (system time in us); false after the time limit. */
    bool waitReady(uint32_t const start)
    {
        while (true)
        {
            // read the time first: a store that ends while this task was preempted still counts as finished
            uint32_t const elapsed = getSystemTimeUs32Bit() - start; // unsigned: correct across the 32-bit wrap
            if ((FTFC->FCNFG & FTFC_FCNFG_EEERDY_MASK) != 0U)
            {
                _longestWriteUs = (elapsed > _longestWriteUs) ? elapsed : _longestWriteUs;
                return true;
            }
            if (elapsed > _writeLimitUs)
            {
                ++_timeouts;
                return false;
            }
        }
    }

    uintptr_t _window;
    uint32_t _windowSize;
    uint32_t const _writeLimitUs;
    uint32_t _longestWriteUs;
    uint32_t _timeouts;
    uint32_t _commandWaits;
    uint32_t _holdWaits;
};

} // namespace platform
