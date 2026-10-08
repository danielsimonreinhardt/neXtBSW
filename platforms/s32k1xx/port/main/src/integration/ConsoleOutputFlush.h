// SPDX-License-Identifier: Apache-2.0
#pragma once

/* Output flush before the power down of the ECU state management (SYS-09, ecuState/IOutputFlush.h): the log lines
 * of the run-down and the change to Sleep have to reach the console before the power supply removes the MCU
 * supply (board test SYS-02 (b), 2026-09-16: the log stopped in the middle of a line).
 *
 * Nothing new is buffered or written here: the OpenBSW logger (LoggerComposition, ring buffer) keeps writing its
 * entries through stdIo and bspUart in its own output context. start() posts a probe runnable into that context
 * (IdleOutputDrain.h), which recognises when the handler of that context stops writing, then waits until the last
 * character has left the UART (bsp::Uart::waitForTxReady() plus one character time). isFlushed() only reads the
 * result, so the ECU state management never blocks.
 *
 * The limit is derived, not configured (consoleFlushLimitMs(), IdleOutputDrain.h) from the logger buffer and the
 * baud rate of the console (project uart.<name>.baudrate, generated). Board neutral: no SBC, no UART instance, only
 * the console of the project (bsp::Uart::Id::TERMINAL).
 *
 * Header only: private to the board integration (Integration.cpp creates the one instance). */

#include "IdleOutputDrain.h"

#include <async/Async.h>
#include <bsp/Uart.h>
#include <bsp/timer/SystemTimer.h>
#include <bsp/uart/UartConfig.h>
#include <ecuState/IOutputFlush.h>
#include <lifecycle/LifecycleLogger.h>
#include <util/logger/Logger.h>

#include <atomic>
#include <cstdint>

namespace integration
{

class ConsoleOutputFlush final
: public ::ecustate::IOutputFlush
, private ::async::RunnableType
{
public:
    /**
     * \param outputContext context in which the application outputs the logger and the console (OpenBSW
     *                      application: TASK_IDLE, IdleHandler)
     * \param baudRate      baud rate of the console UART in Bd
     */
    ConsoleOutputFlush(::async::ContextType const outputContext, uint32_t const baudRate)
    : _context(outputContext)
    , _limitMs(consoleFlushLimitMs(baudRate))
    , _characterUs(::ecustate::serialTransmitTimeUs(1U, baudRate))
    , _drain(::ecustate::serialTransmitTimeUs(IdleOutputDrain::QUIET_GAP_CHARACTERS, baudRate))
    , _requested(false)
    , _flushed(false)
    {}

    char const* getName() const override { return "console"; }

    uint32_t getLimitMs() const override { return _limitMs; }

    void start(uint32_t const /* nowMs */) override
    {
        // Last line of the ECU before the power down; it is written before the probe runs, so it is flushed too.
        ::util::logger::Logger::info(
            ::util::logger::LIFECYCLE,
            "ECU power down after the console output is flushed (limit %u ms)",
            static_cast<unsigned>(_limitMs));
        _flushed.store(false);
        _requested.store(true);
        ::async::execute(_context, *this); // ignored if the probe is still queued
    }

    bool isFlushed(uint32_t const /* nowMs */) override { return _flushed.load(); }

private:
    // Output context: runs alternately with the handler that writes the log and the console.
    void execute() override
    {
        if (_requested.load())
        {
            _requested.store(false);
            _drain.restart();
        }
        else if (_flushed.load())
        {
            return;
        }
        else
        {
            // measuring
        }
        if (!_drain.sample(getSystemTimeUs32Bit()))
        {
            ::async::execute(_context, *this);
            return;
        }
        ::bsp::Uart& uart = ::bsp::Uart::getInstance(::bsp::Uart::Id::TERMINAL);
        (void)uart.waitForTxReady(); // the last character moved into the shift register
        uint32_t const shiftStartUs = getSystemTimeUs32Bit();
        while ((getSystemTimeUs32Bit() - shiftStartUs) <= _characterUs)
        {
            // ... and out of it (one character time, well below the 10-ms cycle of the ECU state management)
        }
        _flushed.store(true);
    }

    ::async::ContextType const _context;
    uint32_t const _limitMs;
    uint32_t const _characterUs;
    IdleOutputDrain _drain;
    std::atomic<bool> _requested;
    std::atomic<bool> _flushed;
};

} // namespace integration
