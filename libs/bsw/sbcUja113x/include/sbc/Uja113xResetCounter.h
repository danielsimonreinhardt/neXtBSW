#pragma once

#include "sbc/Uja113x.h"

#include <cstdint>

namespace sbc
{

/**
 * Reset counter compensation for MCU watchdog self-test resets (SYS-07).
 *
 * The MCU watchdog fast test (OpenBSW safety::bsp::Watchdog::executeFastTest) resets the MCU twice
 * on every start. With RSTN connected, the SBC counts both resets in RCC, which leaves no reserve for
 * a failed start. Called early in main(), before the test, this takes back the count of the test reset
 * that has just happened: RCC - 1 (never below 0), all other bits of the fail-safe register kept.
 */
struct ResetCounterCompensation
{
    enum class Result : uint8_t
    {
        NOT_TEST_RESET, // last reset was no watchdog test reset, SBC not accessed
        COMPENSATED,    // RCC decremented and verified
        COUNTER_ZERO,   // RCC already 0, nothing written
        SPI_ERROR,      // SPI timeout or address echo mismatch, nothing (more) written
        WRONG_DEVICE,   // identification differs from the configuration, nothing written
        VERIFY_ERROR,   // read-back differs after the write
    };

    Result result;
    uint8_t failSafeBefore; // register 0x02 as read (valid from COMPENSATED/COUNTER_ZERO/VERIFY_ERROR)
    uint8_t failSafeAfter;  // register 0x02 read back after the write (COMPENSATED/VERIFY_ERROR)
};

/** MCU WDOG_CS.TST values while a fast test is running (S32K1xx: 2 = test low, 3 = test high). */
constexpr uint8_t MCU_WATCHDOG_TEST_LOW  = 2U;
constexpr uint8_t MCU_WATCHDOG_TEST_HIGH = 3U;

/**
 * True if the last MCU reset was caused by the watchdog fast test: watchdog reset flag set and the
 * test field (preserved across the reset) says a test was running. A watchdog reset after the test
 * (test field 1, user mode) is a real one and keeps being counted.
 */
bool isMcuWatchdogTestReset(bool lastResetWasWatchdog, uint8_t watchdogTestField);

/**
 * Decrements RCC by one if isMcuWatchdogTestReset(). Uses only the driver's register access
 * (address echo checked on reads) and writes nothing but register 0x02 after a matching ID.
 */
ResetCounterCompensation compensateWatchdogTestReset(
    Uja113x& sbc, bool lastResetWasWatchdog, uint8_t watchdogTestField);

char const* resetCounterCompensationName(ResetCounterCompensation::Result result);

} // namespace sbc
