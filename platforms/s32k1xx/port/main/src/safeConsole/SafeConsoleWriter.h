#pragma once

/* SYS-39: the loop of safety::safeConsoleWrite() (safeSupervisor/SafeConsole.h) on the S32K1 family, pure logic
 * over a transmitter port so that the host test runs exactly this code (test/host/SafeConsoleHostTest.cpp).
 *
 * Port: bool waitForTxReady() - polls until the transmitter takes a byte (LPUART STAT[TDRE]) or its time-out ran
 * out; bool put(uint8_t) - hands one byte over and waits for it to move on. No interrupt, no task, no buffer.
 *
 * Every byte is waited for **before** it is written: the transmitter may still be busy with the last character of
 * the logger or the console when the supervisor takes over (bsp::Uart::write alone drops a byte in that case,
 * writeByte() only checks TDRE once). The first byte the transmitter does not take ends the line: a dead console
 * costs one time-out, not one per byte - the hold loop services the watchdog only after the line. */

#include <cstdint>

namespace platform
{
template<class Port>
uint32_t writeSynchronously(Port& port, char const* const data, uint32_t const length)
{
    uint32_t written = 0U;
    while (written < length)
    {
        if (!port.waitForTxReady())
        {
            break;
        }
        if (!port.put(static_cast<uint8_t>(data[written])))
        {
            break;
        }
        ++written;
    }
    return written;
}
} // namespace platform
