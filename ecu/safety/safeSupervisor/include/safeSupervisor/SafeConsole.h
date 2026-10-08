/* SYS-39: the last words of the safe supervisor - one console line written synchronously, with all interrupts
 * suspended, from any context (main() before the BSP, a task, the idle context), before a hold that never ends.
 * SYS-52: also before the reset of SAFETY_CYCLE_PERIOD_VIOLATION (safety task), whose asynchronous log line the reset
 * would swallow; SYS-59: before the reset of every S1 + S2 reaction (below).
 *
 * Board finding 2026-09-21 (SYS-38, capture 46): the hold after ROM_CHECK_FAILURE (idle context) left no line on
 * the console, while the hold after the watchdog self-test (main()) did. Up to SYS-39 the hold printed through
 * printf, i.e. the OpenBSW charIo layer (bspCharInputOutput: vfctprintf -> __outchar -> the charIo API table ->
 * Uart::write). In this build that path is synchronous as well, so it does not explain the finding by itself (in
 * that capture main(RCM...) and "hello" are missing too, see doc/changelog.d/SYS-39.md). But the layer is not made
 * for a last line: its table can be switched to an asynchronous buffer that only the idle context drains
 * (charIoSetAsynchronous, SerialLogger_Idle), and Uart::write gives a byte up without a trace when the transmitter
 * is still busy on entry (writeByte() does not wait before it writes). None of that may decide whether the one line
 * of a held ECU gets out. The supervisor therefore formats the line itself (no printf) and hands it to
 * safeConsoleWrite(), which the family port of the target defines and which records what it wrote.
 *
 * Contract of safeConsoleWrite() for a family port:
 * - synchronous: returns when every byte was handed to the transmitter or the transmitter did not get ready in
 *   time; polling only - no interrupt, no RTOS service, no buffer, no task, no heap, no logger;
 * - callable with all interrupts suspended, before the BSP initialization (main()) and from every task;
 * - bounded: gives up at the first byte the transmitter does not take within its time-out, so a dead console
 *   delays the hold by one time-out at most (the watchdog is serviced only in the hold loop);
 * - returns the number of bytes handed to the transmitter.
 * On the S32K1 family: platforms/s32k1xx/port/main/src/safeConsole (terminal LPUART of the project).
 *
 * SYS-59: every reset of an S1 + S2 reaction writes one such line, "SAFETY RESET (<event>[, <context>]): outputs
 * off" (board finding 2026-09-22, captures 144-148: the deadman and watchdog configuration errors reset without one,
 * and the reset cut the character on the wire). The order in SafeSupervisor::handle() is S1, mark of the reset-loop
 * record, safeConsoleFlush(), the line, safeConsoleFlush(), reset. The line starts with CR LF, so it begins a line
 * of its own even if the reset interrupted another writer in the middle of one. The log entries still waiting in the
 * buffer of the (asynchronous) logger are not written: the line does not wait behind them (it bypasses the logger),
 * and printing them would run the logger and the formatter - neither bounded nor part of the safety code - between
 * S1 and S2 (doc/changelog.d/SYS-59.md).
 *
 * Contract of safeConsoleFlush() for a family port:
 * - returns when every character already handed to the console's transmitter has left it (the transmitter is idle:
 *   data register, FIFO and shift register empty), or when its time-out ran out;
 * - polling only, like safeConsoleWrite(), callable with all interrupts suspended from every context;
 * - bounded: at most one time-out, whatever the transmitter does (dead, disabled flow control, a writer that keeps
 *   writing cannot: all interrupts are suspended);
 * - returns false if the time-out ran out (the transmitter did not get idle) - the supervisor then skips the line,
 *   so a dead console costs one time-out, not three.
 */

#pragma once

#include <cstddef>
#include <cstdint>

namespace safety
{
/** Writes `length` bytes of `data` to the console, synchronously (see above). Defined by the family port. */
uint32_t safeConsoleWrite(char const* data, uint32_t length);

/** SYS-59: waits until the console's transmitter is idle (see above). Defined by the family port. */
bool safeConsoleFlush();

/** Longest console line of the supervisor including CR LF; a longer event name is shortened, the end is kept. */
constexpr size_t SAFE_CONSOLE_LINE_SIZE = 128U;

/**
 * Formats `format` with its single "%s" replaced by `text` into `line` (at most `size` - 1 characters, 0
 * terminated) - no printf, no locale, no heap, so it is safe wherever safeConsoleWrite() is. The part after "%s"
 * is always kept whole (it ends with CR LF); if the line is too long, `text` is shortened. A format without "%s"
 * is copied as it is. \return length of the line without the terminating 0
 */
inline size_t formatConsoleLine(char* const line, size_t const size, char const* const format, char const* text)
{
    if (size == 0U)
    {
        return 0U;
    }
    size_t prefix = 0U;
    while ((format[prefix] != '\0') && !((format[prefix] == '%') && (format[prefix + 1U] == 's')))
    {
        ++prefix;
    }
    bool const hasText   = (format[prefix] != '\0');
    char const* const tail = hasText ? &format[prefix + 2U] : &format[prefix];
    size_t tailLength      = 0U;
    while (tail[tailLength] != '\0')
    {
        ++tailLength;
    }
    size_t const room = size - 1U;
    size_t length     = 0U;
    for (size_t i = 0U; (i < prefix) && (length < room); ++i)
    {
        line[length] = format[i];
        ++length;
    }
    if (hasText && (text != nullptr))
    {
        size_t const textRoom = (room > (length + tailLength)) ? (room - length - tailLength) : 0U;
        for (size_t i = 0U; (i < textRoom) && (text[i] != '\0'); ++i)
        {
            line[length] = text[i];
            ++length;
        }
    }
    for (size_t i = 0U; (i < tailLength) && (length < room); ++i)
    {
        line[length] = tail[i];
        ++length;
    }
    line[length] = '\0';
    return length;
}
} // namespace safety
