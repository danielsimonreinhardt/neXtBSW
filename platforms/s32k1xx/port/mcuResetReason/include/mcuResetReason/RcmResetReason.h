#pragma once

#include <ecuState/EcuState.h>

#include <cstddef>
#include <cstdint>

/**
 * RTE-16: reset reason of an S32K1 MCU (RCM_SRS) as ecustate::wakeup bits, for bswservice::reset::mcuStartReasons()
 * (bswService/McuResetReason.h). Pure functions without register access, so the host test runs them; the register
 * access and the capture before main() are in src/mcuResetReason/McuResetReason.cpp.
 *
 * Bit positions from the S32K1xx reference manual (RCM_SRS), the same for S32K11x and S32K14x except bit 3 (LOL,
 * S32K14x only) and bit 4 (CMU_LOC, S32K11x only); McuResetReason.cpp checks them against the device header.
 *
 * Why a capture: RCM_SRS only holds the reason of the most recent reset. Every start of the application runs the
 * MCU watchdog fast test (OpenBSW safeBspMcuWatchdog, main()), which resets the MCU twice by the watchdog - after a
 * completed start RCM_SRS therefore always says WDOG. The reason of the start is the RCM_SRS of the pass that was not
 * such a test reset; it is kept across the test resets in no-init RAM (Record).
 */
namespace rcm
{
constexpr uint32_t SRS_LVD     = 1UL << 1U;  // low-voltage detect reset (also set with POR)
constexpr uint32_t SRS_LOC     = 1UL << 2U;  // loss of external clock
constexpr uint32_t SRS_LOL     = 1UL << 3U;  // loss of PLL lock (S32K14x)
constexpr uint32_t SRS_CMU_LOC = 1UL << 4U;  // CMU loss of clock (S32K11x)
constexpr uint32_t SRS_WDOG    = 1UL << 5U;  // watchdog
constexpr uint32_t SRS_PIN     = 1UL << 6U;  // external reset pin (also a system basis chip pulling RESET_b)
constexpr uint32_t SRS_POR     = 1UL << 7U;  // power-on reset
constexpr uint32_t SRS_JTAG    = 1UL << 8U;  // JTAG
constexpr uint32_t SRS_LOCKUP  = 1UL << 9U;  // core lockup
constexpr uint32_t SRS_SW      = 1UL << 10U; // software (SYSRESETREQ)
constexpr uint32_t SRS_MDM_AP  = 1UL << 11U; // debugger (MDM-AP system reset request)
constexpr uint32_t SRS_SACKERR = 1UL << 13U; // stop acknowledge error

/** Resets without power or watchdog event: reported as wakeup::RESET. */
constexpr uint32_t SRS_RESETS
    = SRS_LOC | SRS_LOL | SRS_CMU_LOC | SRS_PIN | SRS_JTAG | SRS_LOCKUP | SRS_SW | SRS_MDM_AP | SRS_SACKERR;

/**
 * SYS-103: reset bits that are a consequence of the watchdog reset itself when they come together with WDOG.
 *
 * The watchdog runs with WDOG_CS[INT] = 1: its pre-reset interrupt (vector 38) enters the fault handler of the port
 * (customHardFaultHandler: safe outputs, dump; HardFault_Handler_Final: console line, softwareSystemReset()), and
 * RCM_SRIE (DELAY 514 LPO cycles, WDOG, GIE) holds the watchdog reset back for about 4 ms. The handler's
 * AIRCR.SYSRESETREQ comes about as late as the delay ends (the line takes milliseconds on the console): inside the
 * window a real watchdog reset reports WDOG + SW (0x420), after it WDOG alone (0x20). Board 2026-09-29 (SYS-103,
 * captures sys103_*): without a breakpoint the whole line "HARD FAULT RESET: outputs off, dump saved" came and
 * RCM_SRS was 0x420; with a breakpoint on HardFault_Handler_Final or softwareSystemReset (the core stops before
 * SYSRESETREQ, the delayed watchdog reset comes anyway) it was 0x20. A debugger or software reset does not set WDOG
 * (J-Link reset: 0x400; the core is halted and the watchdog stopped, WDOG_CS[DBG] = 0), so next to WDOG the SW bit is
 * the watchdog's own follow-up, not another reset reason. Other bits next to WDOG (pin, MDM-AP, JTAG, lockup, loss of
 * clock, ...) stay a reset reason of their own.
 */
constexpr uint32_t SRS_WATCHDOG_FOLLOW_UP = SRS_SW;

/** WDOG_CS.TST during the fast test of the MCU watchdog (2 = test of the low byte, 3 = high byte). */
constexpr uint8_t WDOG_TST_FAST_LOW  = 2U;
constexpr uint8_t WDOG_TST_FAST_HIGH = 3U;

/**
 * RCM_SRS -> ecustate::wakeup bits. A power-on also sets LVD: then only POWER, SUPPLY stands for a low-voltage reset
 * without power-on (brown-out). Several bits are passed on together; bswservice::reset::reasonOf() ranks them.
 * SYS-103: next to WDOG, SW is the watchdog's own follow-up (SRS_WATCHDOG_FOLLOW_UP) - WATCHDOG only, no RESET, so a
 * real watchdog reset is the same start reason whichever of the two resets came first. The reset-loop limit (SYS-43)
 * counts a watchdog reason only without RESET next to it.
 */
constexpr uint32_t startReasonsOf(uint32_t const srs)
{
    namespace wakeup = ::ecustate::wakeup;
    uint32_t reasons = wakeup::NONE;
    uint32_t resets  = srs & SRS_RESETS;
    if ((srs & SRS_POR) != 0U)
    {
        reasons |= wakeup::POWER;
    }
    else if ((srs & SRS_LVD) != 0U)
    {
        reasons |= wakeup::SUPPLY;
    }
    if ((srs & SRS_WDOG) != 0U)
    {
        reasons |= wakeup::WATCHDOG;
        resets &= ~SRS_WATCHDOG_FOLLOW_UP;
    }
    if (resets != 0U)
    {
        reasons |= wakeup::RESET;
    }
    return reasons;
}

/** True if this reset came from the watchdog fast test of the start, not from a real watchdog timeout. */
constexpr bool isWatchdogTestReset(uint32_t const srs, uint8_t const watchdogTestField)
{
    return ((srs & SRS_WDOG) != 0U)
           && ((watchdogTestField == WDOG_TST_FAST_LOW) || (watchdogTestField == WDOG_TST_FAST_HIGH));
}

/** RCM_SRS of the start, kept in no-init RAM across the resets of the watchdog fast test. */
struct Record
{
    uint32_t srs;
    uint32_t check; // ~srs ^ RECORD_KEY: content after power-on is random
};

constexpr uint32_t RECORD_KEY = 0x52164E00UL;

/**
 * One pass through the start-up (called before main() on every reset): a pass that is not a fast-test reset is a
 * new start and writes its RCM_SRS into \p record; a fast-test reset keeps it. Returns the start reasons of the
 * start this pass belongs to, 0 if the record is not valid (fast-test reset without a recorded start).
 */
inline uint32_t capture(Record volatile& record, uint32_t const srs, uint8_t const watchdogTestField)
{
    if (!isWatchdogTestReset(srs, watchdogTestField))
    {
        record.srs   = srs;
        record.check = ~srs ^ RECORD_KEY;
        return startReasonsOf(srs);
    }
    uint32_t const recorded = record.srs;
    if (record.check != (~recorded ^ RECORD_KEY))
    {
        return ::ecustate::wakeup::NONE;
    }
    return startReasonsOf(recorded);
}

/**
 * SYS-84: what the start-up line reports. RCM_SRS read in main() is the status of the most recent reset, and in a
 * start with the watchdog fast test that is always the test's own watchdog reset (0x20) - also after a power-on or
 * the reset pin (board 2026-09-23). The line therefore reports the RCM_SRS of the start (the record of capture())
 * and names the last reset only where it differs.
 */
struct StartStatus
{
    uint32_t startSrs; // RCM_SRS of the pass that was not a fast-test reset (valid if known)
    uint32_t lastSrs;  // RCM_SRS of the most recent reset (the pass that reaches main())
    bool known;        // false: fast-test reset without an intact record (no-init RAM lost)
};

/** Status of the start after capture() of this pass with the RCM_SRS \p lastSrs of this pass. */
inline StartStatus startStatusOf(Record const volatile& record, uint32_t const lastSrs)
{
    uint32_t const recorded = record.srs;
    bool const known        = (record.check == (~recorded ^ RECORD_KEY));
    return StartStatus{known ? recorded : 0U, lastSrs, known};
}

/** Names of the RCM_SRS bits for the start-up line; bit 3 exists on S32K14x only, bit 4 on S32K11x only. */
struct SrsName
{
    uint32_t bit;
    char const* name;
};

constexpr SrsName SRS_NAMES[] = {
    {SRS_POR, "POR"},
    {SRS_LVD, "LVD"},
    {SRS_WDOG, "WDOG"},
    {SRS_PIN, "PIN"},
    {SRS_SW, "SW"},
    {SRS_MDM_AP, "MDM_AP"},
    {SRS_LOCKUP, "LOCKUP"},
    {SRS_JTAG, "JTAG"},
    {SRS_LOC, "LOC"},
    {SRS_LOL, "LOL"},
    {SRS_CMU_LOC, "CMU_LOC"},
    {SRS_SACKERR, "SACKERR"},
};

namespace detail
{
/** Appends \p text at \p pos of \p buf (size \p size), always terminated; returns the new position. */
inline size_t append(char* const buf, size_t const size, size_t pos, char const* text)
{
    while ((*text != '\0') && ((pos + 1U) < size))
    {
        buf[pos] = *text;
        ++pos;
        ++text;
    }
    if (size != 0U)
    {
        buf[(pos < size) ? pos : (size - 1U)] = '\0';
    }
    return pos;
}

inline size_t appendHex(char* const buf, size_t const size, size_t const pos, uint32_t const value)
{
    char digits[11] = {'0', 'x'};
    size_t n        = 2U;
    bool started    = false;
    for (int shift = 28; shift >= 0; shift -= 4)
    {
        uint32_t const nibble = (value >> static_cast<uint32_t>(shift)) & 0xFU;
        if ((nibble != 0U) || started || (shift == 0))
        {
            digits[n] = "0123456789abcdef"[nibble];
            ++n;
            started = true;
        }
    }
    digits[n] = '\0';
    return append(buf, size, pos, digits);
}

inline size_t appendNames(char* const buf, size_t const size, size_t pos, uint32_t const srs)
{
    bool first = true;
    for (SrsName const& n : SRS_NAMES)
    {
        if ((srs & n.bit) != 0U)
        {
            pos   = append(buf, size, pos, first ? " " : "+");
            pos   = append(buf, size, pos, n.name);
            first = false;
        }
    }
    return pos;
}
} // namespace detail

/** Buffer for formatStartLine() that holds every combination of bits in full (host test rcm-start-line). */
constexpr size_t START_LINE_SIZE = 192U;

/**
 * Start-up line of main() (without line end), e.g.
 *   "main(RCM::SRS 0x82 POR+LVD, last reset 0x20 WDOG fast test)"  power-on, then the two fast-test resets
 *   "main(RCM::SRS 0x40 PIN)"                                        reset pin without a fast-test reset after it
 *   "main(RCM::SRS unknown, last reset 0x20 WDOG fast test)"         record lost
 * The prefix "main(RCM::SRS " is the one of the OpenBSW reference, so older board captures stay comparable; the
 * number after it is now the reason of the start. Returns the length written (without the terminator).
 */
inline size_t formatStartLine(char* const buf, size_t const size, StartStatus const& s)
{
    size_t pos = detail::append(buf, size, 0U, "main(RCM::SRS ");
    if (s.known)
    {
        pos = detail::appendHex(buf, size, pos, s.startSrs);
        pos = detail::appendNames(buf, size, pos, s.startSrs);
    }
    else
    {
        pos = detail::append(buf, size, pos, "unknown");
    }
    if ((!s.known) || (s.lastSrs != s.startSrs))
    {
        pos = detail::append(buf, size, pos, ", last reset ");
        pos = detail::appendHex(buf, size, pos, s.lastSrs);
        pos = detail::appendNames(buf, size, pos, s.lastSrs);
        if ((s.lastSrs & SRS_WDOG) != 0U)
        {
            pos = detail::append(buf, size, pos, " fast test");
        }
    }
    return detail::append(buf, size, pos, ")");
}
} // namespace rcm
