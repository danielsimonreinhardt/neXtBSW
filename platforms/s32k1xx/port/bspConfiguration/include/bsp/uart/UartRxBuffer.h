#pragma once

#include "bsp/Uart.h"
#include "mcu/mcu.h"
#include <cstdint>

namespace bsp
{

/**
 * Interrupt-driven receive path of an LPUART (IO-17).
 *
 * The console reads stdin from the lowest-priority (idle) context. The LPUART receiver holds one
 * byte (a few with the FIFO enabled), and at 115200 Bd a byte arrives every ~87 us, so a
 * higher-priority task running longer than that overruns the receiver. The RX interrupt drains the
 * receiver into a single-producer (ISR) / single-consumer ring buffer instead.
 *
 * Contract: exactly one consumer context calls read() (stdin); isr() is the only producer.
 */
class UartRxBuffer
{
public:
    struct Config
    {
        LPUART_Type& uart;
        IRQn_Type irq;
        uint8_t irqPriority;
    };

    struct Statistics
    {
        uint32_t hardwareOverruns; // receiver overrun flag seen (bytes lost before the ISR ran)
        uint32_t bufferOverflows;  // bytes dropped because the ring buffer was full
        uint32_t framingErrors;    // framing, noise or parity flag seen
    };

    explicit UartRxBuffer(Config const& config) : _config(config) {}

    /**
     * (Re-)enables FIFO and receive interrupt if they are not active. OpenBSW's Uart::init()
     * rewrites CTRL and FIFO, so read() calls this on every invocation (one register read).
     */
    void ensureEnabled();

    /** Takes one byte from the ring buffer. \return true if a byte was available */
    bool read(uint8_t& byte);

    /** Receive interrupt handler: drains the receiver into the ring buffer. */
    void isr();

    Statistics const& getStatistics() const { return _statistics; }

    /** \return the bytes that can be buffered between two read() calls */
    static constexpr size_t capacity() { return RING_SIZE - 1U; }

private:
    static constexpr size_t RING_SIZE = 128U; // power of two

    Config const& _config;
    uint8_t _ring[RING_SIZE] = {};
    size_t volatile _head    = 0U; // written by isr() only
    size_t volatile _tail    = 0U; // written by read() only
    Statistics _statistics   = {};
};

/** Receive buffer of a Uart instance (instances and ISR in UartConfig.cpp). */
UartRxBuffer& getUartRxBuffer(Uart::Id id);

} // namespace bsp
