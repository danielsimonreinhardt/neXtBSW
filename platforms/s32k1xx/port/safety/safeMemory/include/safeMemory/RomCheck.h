/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#pragma once

#include "safeMemory/RomCheckList.h"

#include <cstdint>

namespace safety
{

/**
 * ROM check (SM-14): CRC-32 through the CRC unit over the flash areas of the checksum list, compared with the
 * reference the post-link step wrote (SYS-38).
 *
 * SYS-44: one pass is the known-answer test of the CRC unit, the plausibility of the list and the CRC over the
 * areas. The first pass runs once per start after init(); further passes follow with the period of the project
 * (module parameter modules.safeMemory.rom_check_period_ms, 0 = once per start), measured from the start of one pass
 * to the start of the next. idle() is called from the idle context only (lowest priority, no lock around the pass:
 * every supervised task preempts it, doc/safety/fmea.md section 5.1, SSR-025); only the bookkeeping at the end of a
 * pass and the report enter the safety shell. While a flash write is open (RomCheckFlashWrites) or a flash command
 * runs, no pass starts, and a pass during which one began is discarded and repeated - never reported. A pass that
 * could not run for longer than one period is reported as ROM_CHECK_FAILURE (result ROMCHECK_OVERDUE).
 *
 * SYS-91: an area with its own reference. Flash content that may be written without a build - the calibration
 * segment of memory.calibration (ecu/calibration), whose dataset can be flashed on its own - cannot be covered by the
 * reference of the build: a different dataset would hold the ECU in the safe state before the owner's start-up check
 * could apply its own reaction. The linker template leaves such an area out of the list (symbols
 * __rom_check_own_area_start/_end; romCheckImage.py accepts image content outside the list only there), and its owner
 * registers a check against the area's own reference (setOwnAreaCheck). Every pass calls it after the areas of the
 * list: same period, same flash-write rules, and a deviation is ROMCHECK_OWN_AREA_FAILED, reported as
 * ROM_CHECK_FAILURE like any other.
 */
class RomCheck
{
public:
    enum class Result : uint8_t
    {
        ROMCHECK_REQUESTED = 0,
        ROMCHECK_OK        = 1,
        ROMCHECK_FAILED    = 2,
        // SYS-38: checksum list implausible (image without the post-link step?), reported as ROM_CHECK_FAILURE
        ROMCHECK_INVALID_LIST = 3,
        // SYS-44: the CRC unit failed its known-answer test, reported as ROM_CHECK_FAILURE
        ROMCHECK_UNIT_FAILED = 4,
        // SYS-44: flash writes kept every pass off for longer than one period, reported as ROM_CHECK_FAILURE
        ROMCHECK_OVERDUE = 5,
        // SYS-91: the area with its own reference deviates from it, reported as ROM_CHECK_FAILURE
        ROMCHECK_OWN_AREA_FAILED = 6
    };

    /** SYS-91: outcome of the owner's check of the area with its own reference. */
    struct OwnArea
    {
        bool ok;
        uint32_t address; ///< start of the area (for the log line)
        uint32_t detail;  ///< the owner's code of the deviation (calibration::Status for the calibration segment)
    };

    /** SYS-91: the owner's check. Called inside a pass (idle context, no lock); must not log - the pass may still be
     * discarded. */
    using OwnAreaCheck = OwnArea (*)();

    /** Counters since init() (for the board check and a later console command). */
    struct Statistics
    {
        uint32_t passes;        ///< passes that ran to the end and were evaluated
        uint32_t discarded;     ///< passes thrown away because a flash write began during them
        uint32_t postponements; ///< times a due pass had to wait for a flash write
    };

    /** Period of the project (modules.safeMemory.rom_check_period_ms), 0 = once per start. */
    static uint32_t periodMs();

    /** Requests the first pass (SafetyManager::init, inside its safety shell). */
    static void init();

    /** Runs a pass if one is due (system time and period of the project). Idle context only. */
    static void idle();

    /** idle() with the time and the period as arguments, on the image of the linker symbols. */
    static void idleAt(uint32_t nowMs, uint32_t periodMs);

    /** idleAt() on another image (the host test). */
    static void idleOn(uint32_t nowMs, uint32_t periodMs, romcheck::Image const& image);

    /**
     * SYS-91: registers the check of the area with its own reference (nullptr: none). Once per start, before or after
     * init(); enters the safety shell (the pointer lives in the safety RAM, so a wild write cannot bend it).
     */
    static void setOwnAreaCheck(OwnAreaCheck check);

    static Result result() { return _result; }

    static Statistics statistics() { return _statistics; }

private:
    static void postpone(uint32_t nowMs, uint32_t periodMs, bool discarded = false);
    static void finish(Result result, uint32_t nowMs);

    static Result _result;
    static bool _armed;
    static bool _postponed;
    static uint32_t _lastPassMs;
    static uint32_t _postponedSinceMs;
    static Statistics _statistics;
    static OwnAreaCheck _ownAreaCheck;
};

} // namespace safety
