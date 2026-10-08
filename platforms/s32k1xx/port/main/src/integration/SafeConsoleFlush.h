// SPDX-License-Identifier: Apache-2.0
#pragma once

/* SYS-59: safety::safeConsoleFlush() of the S32K1 family (contract: ecu/safety/safeSupervisor/include/safeSupervisor/
 * SafeConsole.h) - waits until the console's LPUART has sent every character handed to it, before and after the
 * SAFETY RESET line of the safe supervisor, so that the reset cuts nothing.
 *
 * Why the transmitter has to be idle, not only ready (board 2026-09-22, captures 144-148): LPUART STAT[TDRE] only says
 * that the data register took its character over; the character is then still in the shift register for one
 * character time (87 us at 115200 Bd), and a software reset in that time cuts it on the wire. STAT[TC] (transmission
 * complete) is set only when the data register, the transmit FIFO (the S32K1 platform leaves it off, Uart::init()
 * writes FIFO = 0) and the shift register are empty.
 *
 * Bounds: at most TRANSMITTER_FLUSH_POLLS reads of STAT, whatever the transmitter does. A live transmitter gets idle
 * after the characters it holds - with the FIFO off at most two (data and shift register), 174 us at 115200 Bd -
 * because nothing can refill it: the caller has all interrupts suspended. A dead one (clock stopped, transmitter
 * switched off with a character pending) ends the wait after the polls, the same bound as one character of
 * bsp::Uart (WRITE_TIMEOUT, 100 000 polls of STAT).
 *
 * Pure logic over a port (bool transmissionComplete()), so the host test runs exactly this code on a model of the
 * transmitter (test/host/SafetyResetConsoleHostTest.cpp). The port on the target reads STAT of the terminal LPUART of
 * the project (Integration.cpp). Header only: private to the board integration. */

#include <cstdint>

namespace platform
{
/** Polls of the transmitter's status after which the flush gives up: the time-out of one character of bsp::Uart. */
constexpr uint32_t TRANSMITTER_FLUSH_POLLS = 100000U;

/**
 * Polls `port` until the transmitter is idle, at most `maxPolls` times.
 * \return true once it is idle, false if it did not get idle within the polls
 */
template<class Port>
bool flushTransmitter(Port& port, uint32_t const maxPolls)
{
    for (uint32_t polls = 0U; polls < maxPolls; ++polls)
    {
        if (port.transmissionComplete())
        {
            return true;
        }
    }
    return false;
}
} // namespace platform
