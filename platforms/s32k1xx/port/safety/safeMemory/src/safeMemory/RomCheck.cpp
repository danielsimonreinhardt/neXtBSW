/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "safeMemory/RomCheck.h"

#include "safeMemory/RomCheckFlashWrites.h"
#include "safeMemory/RomCheckList.h"
#include "safeMemory/SafetyShell.h"

#include <bsp/timer/SystemTimer.h>
// SYS-44: period of the re-check, module parameter of the project (module.yaml of safeMemory, tools/bswgen)
#include <integration/safeMemoryConfiguration.h>
#include <mcu/mcu.h>
#include <safeSupervisor/SafeSupervisor.h>
#include <safeUtils/SafetyLogger.h>

// Linker script (port/main/linkerscript/application.dld.in): the list, its end, the reference CRC written after
// the link (tools/romCheckImage.py) and the start of the application flash, the lowest address an area may name
extern uintptr_t const __checksum_list_start[];
extern uintptr_t const __checksum_list_end[];
extern uint32_t const __checksum_result;
extern uint32_t const __FLASH_START[];

namespace safety
{
using ::util::logger::Logger;
using ::util::logger::SAFETY;

namespace
{
/** CRC of the known-answer test: CRC-32 of the five words fed by crcKnownAnswer() (same vector and value as
 * test_rom_check_image.py CrcTest.test_known_vector and the host test). */
constexpr uint32_t KNOWN_ANSWER = 0x498CFAA3U;

/** Configures the CRC unit (S32K1 CRC, configuration of the OpenBSW RomCheck) and writes the seed. */
CRC_Type& crcStart()
{
    CRC_Type& crcRegisters        = *CRC;
    constexpr uint32_t seedValue  = 0xFFFFFFFFU;
    constexpr uint32_t polynomial = 0x04C11DB7U;

    // Initialize CRC module
    crcRegisters.CTRL = CRC_CTRL_TCRC(1);                      // 32-bit CRC
    crcRegisters.CTRL = crcRegisters.CTRL | CRC_CTRL_TOT(1);   // Transpose bits for writing
    crcRegisters.CTRL = crcRegisters.CTRL | CRC_CTRL_TOTR(2);  // Transpose bits & bytes for reading
    crcRegisters.CTRL = crcRegisters.CTRL | CRC_CTRL_FXOR(1);  // Final XOR
    crcRegisters.GPOLY = polynomial;

    // Write seed value
    crcRegisters.CTRL       = crcRegisters.CTRL | CRC_CTRL_WAS(1);
    crcRegisters.DATAu.DATA = seedValue;
    // Clear CRC_CTRL[WAS] to start writing data values to the CRC.
    crcRegisters.CTRL = crcRegisters.CTRL & ~(CRC_CTRL_WAS(1));
    return crcRegisters;
}

/** CRC unit over the areas of an accepted list. */
uint32_t crcOverAreas(uintptr_t const* const list)
{
    CRC_Type& crcRegisters = crcStart();

    // Write the area data to the CRC, word by word
    auto feed = [&crcRegisters](uint32_t const word) { crcRegisters.DATAu.DATA = word; };
    romcheck::feedAreas(list, feed);

    // After all values have been written, the final result can be read from CRC_DATA.
    return crcRegisters.DATAu.DATA;
}

/**
 * SYS-44: known-answer test of the CRC unit before every pass (fmea.md SM-14, latent fault: a unit that always
 * returns the reference would hide every corruption). The words are immediates in the code, not a table: the
 * linker script allows no initialised read-only data in the safety libraries (.mpu_rodata stays empty).
 */
uint32_t crcKnownAnswer()
{
    CRC_Type& crcRegisters  = crcStart();
    crcRegisters.DATAu.DATA = 0x04030201U;
    crcRegisters.DATAu.DATA = 0x08070605U;
    crcRegisters.DATAu.DATA = 0xDEADBEEFU;
    crcRegisters.DATAu.DATA = 0x00000000U;
    crcRegisters.DATAu.DATA = 0xFFFFFFFFU;
    return crcRegisters.DATAu.DATA;
}

/**
 * SYS-44: a flash command runs (FTFC FSTAT[CCIF] clear, [RM] FTFC: set when a command has completed). Covers a
 * writer that does not use RomCheckFlashWrites, e.g. a command launched by code that runs from RAM.
 */
bool flashCommandRunning() { return (FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U; }

/** Outcome of one pass: the result and the two values its log line names. */
struct Pass
{
    RomCheck::Result result;
    uint32_t value;    ///< CRC or known answer computed; list status for an invalid list
    uint32_t expected; ///< reference or known answer; start of the list for an invalid list
};

/**
 * SYS-38: the check with the list, its length, the start of the application flash and the reference as parameters
 * (runPass() passes the linker symbols; the host test its own image). An implausible list is refused before a
 * single address of it is read: without the list the flash reads 0xFFFFFFFF areas at 0xFFFFFFFF. SYS-44: logs
 * nothing - a pass may still be discarded (flash write during the pass), only an evaluated pass is logged.
 */
Pass checkPass(uintptr_t const* const list, size_t const listWords, uintptr_t const lowest, uint32_t const reference)
{
    uintptr_t const listStart             = reinterpret_cast<uintptr_t>(list);
    romcheck::ListStatus const listStatus = romcheck::checkList(list, listWords, lowest, listStart);
    if (listStatus != romcheck::ListStatus::VALID)
    {
        return Pass{
            RomCheck::Result::ROMCHECK_INVALID_LIST,
            static_cast<uint32_t>(listStatus),
            static_cast<uint32_t>(listStart)};
    }
    uint32_t const crc = crcOverAreas(list);
    return Pass{
        (crc == reference) ? RomCheck::Result::ROMCHECK_OK : RomCheck::Result::ROMCHECK_FAILED, crc, reference};
}

/** One pass: known-answer test, then list and CRC (SYS-44), then the area with its own reference (SYS-91). */
Pass runPass(romcheck::Image const& image, RomCheck::OwnAreaCheck const ownAreaCheck)
{
    uint32_t const answer = crcKnownAnswer();
    if (answer != KNOWN_ANSWER)
    {
        return Pass{RomCheck::Result::ROMCHECK_UNIT_FAILED, answer, KNOWN_ANSWER};
    }
    Pass const pass = checkPass(image.list, image.listWords, image.lowest, image.reference);
    if ((pass.result != RomCheck::Result::ROMCHECK_OK) || (ownAreaCheck == nullptr))
    {
        return pass;
    }
    RomCheck::OwnArea const own = ownAreaCheck();
    if (!own.ok)
    {
        return Pass{RomCheck::Result::ROMCHECK_OWN_AREA_FAILED, own.detail, own.address};
    }
    return pass;
}

/** The image of the linker symbols. */
romcheck::Image linkedImage()
{
    uintptr_t const listStart = reinterpret_cast<uintptr_t>(__checksum_list_start);
    size_t const listWords
        = (reinterpret_cast<uintptr_t>(__checksum_list_end) - listStart) / sizeof(uintptr_t);
    return romcheck::Image{
        __checksum_list_start, listWords, reinterpret_cast<uintptr_t>(__FLASH_START), __checksum_result};
}

/** Log line of an evaluated pass (outside the safety shell: the logger must not run with interrupts locked). */
void logPass(Pass const& pass)
{
    switch (pass.result)
    {
        case RomCheck::Result::ROMCHECK_OK: Logger::debug(SAFETY, "ROM check passed successfully"); break;
        case RomCheck::Result::ROMCHECK_INVALID_LIST:
            Logger::error(
                SAFETY,
                "ROM check: checksum list at 0x%08x invalid (%d)",
                static_cast<unsigned int>(pass.expected),
                static_cast<int>(pass.value));
            break;
        case RomCheck::Result::ROMCHECK_UNIT_FAILED:
            Logger::error(
                SAFETY,
                "ROM check: CRC unit self-test 0x%08x, expected 0x%08x",
                static_cast<unsigned int>(pass.value),
                static_cast<unsigned int>(pass.expected));
            break;
        case RomCheck::Result::ROMCHECK_OWN_AREA_FAILED:
            Logger::error(
                SAFETY,
                "ROM check failed: area at 0x%08x deviates from its own reference (%d)",
                static_cast<unsigned int>(pass.expected),
                static_cast<int>(pass.value));
            break;
        default:
            Logger::error(
                SAFETY,
                "ROM check failed: CRC 0x%08x, expected 0x%08x",
                static_cast<unsigned int>(pass.value),
                static_cast<unsigned int>(pass.expected));
            break;
    }
}
} // namespace

RomCheck::Result RomCheck::_result                = RomCheck::Result::ROMCHECK_REQUESTED;
bool RomCheck::_armed                             = false;
bool RomCheck::_postponed                         = false;
uint32_t RomCheck::_lastPassMs                    = 0U;
uint32_t RomCheck::_postponedSinceMs              = 0U;
RomCheck::Statistics RomCheck::_statistics        = {0U, 0U, 0U};
RomCheck::OwnAreaCheck RomCheck::_ownAreaCheck    = nullptr;

uint32_t RomCheck::periodMs() { return NX_MODULE_SAFEMEMORY_PARAM_ROM_CHECK_PERIOD_MS; }

void RomCheck::setOwnAreaCheck(OwnAreaCheck const check)
{
    SafetyShell const safetyShell;
    _ownAreaCheck = check;
}

void RomCheck::init()
{
    SafetyShell const safetyShell;
    _result     = RomCheck::Result::ROMCHECK_REQUESTED;
    _armed      = true;
    _postponed  = false;
    _statistics = Statistics{0U, 0U, 0U};
}

void RomCheck::idle() { idleAt(getSystemTimeMs32Bit(), periodMs()); }

void RomCheck::idleAt(uint32_t const nowMs, uint32_t const periodMs)
{
    if (_armed)
    {
        idleOn(nowMs, periodMs, linkedImage());
    }
}

void RomCheck::idleOn(uint32_t const nowMs, uint32_t const periodMs, romcheck::Image const& image)
{
    if (!_armed)
    {
        return; // before SafetyManager::init() requested the check
    }
    if (_result != Result::ROMCHECK_REQUESTED)
    {
        // A failure holds the ECU in the safe state (SYS-23); period 0: once per start
        if ((_result != Result::ROMCHECK_OK) || (periodMs == 0U) || ((nowMs - _lastPassMs) < periodMs))
        {
            return;
        }
    }
    RomCheckFlashWrites::Snapshot const before = romCheckFlashWrites.read();
    if (before.writing() || flashCommandRunning())
    {
        postpone(nowMs, periodMs);
        return;
    }
    // The pass itself: no lock, no safety shell (SSR-025)
    Pass const pass                           = runPass(image, _ownAreaCheck);
    RomCheckFlashWrites::Snapshot const after = romCheckFlashWrites.read();
    if ((after.begun != before.begun) || flashCommandRunning())
    {
        // A flash write began during the pass: its result says nothing about the image, the pass runs again
        postpone(nowMs, periodMs, true);
        return;
    }
    logPass(pass);
    finish(pass.result, nowMs);
}

void RomCheck::postpone(uint32_t const nowMs, uint32_t const periodMs, bool const discarded)
{
    // Deadline: one period from the first time the due pass could not run (period 0: the first pass waits)
    bool const overdue = _postponed && (periodMs != 0U) && ((nowMs - _postponedSinceMs) > periodMs);
    if (overdue)
    {
        Logger::error(
            SAFETY,
            "ROM check: no pass possible for %u ms (flash writes), period %u ms",
            static_cast<unsigned int>(nowMs - _postponedSinceMs),
            static_cast<unsigned int>(periodMs));
        SafetyShell const safetyShell;
        _result    = Result::ROMCHECK_OVERDUE;
        _postponed = false;
        // No pass for more than one period: SM-14 is lost, ROM_CHECK_FAILURE (safe state S1 held, SYS-23)
        safety::SafeSupervisor::getInstance().romCheckMonitor.trigger();
        return;
    }
    if (_postponed && !discarded)
    {
        return; // read only: no safety shell in every round of the idle loop while a write lasts
    }
    SafetyShell const safetyShell;
    if (discarded)
    {
        _statistics.discarded = _statistics.discarded + 1U;
    }
    if (!_postponed)
    {
        // The deadline runs from the first time the due pass could not run, not from the latest attempt
        _postponed                = true;
        _postponedSinceMs         = nowMs;
        _statistics.postponements = _statistics.postponements + 1U;
    }
}

void RomCheck::finish(Result const result, uint32_t const nowMs)
{
    // Enter safety shell, used to grant temporary access to safety RAM
    SafetyShell const safetyShell;
    _statistics.passes = _statistics.passes + 1U;
    _lastPassMs        = nowMs;
    _postponed         = false;
    _result            = result;
    if (result != Result::ROMCHECK_OK)
    {
        // Mismatch, implausible list, failed CRC unit and (SYS-91) an area off its own reference alike:
        // ROM_CHECK_FAILURE (safe state S1 held, SYS-23)
        safety::SafeSupervisor::getInstance().romCheckMonitor.trigger();
    }
}

} // namespace safety
