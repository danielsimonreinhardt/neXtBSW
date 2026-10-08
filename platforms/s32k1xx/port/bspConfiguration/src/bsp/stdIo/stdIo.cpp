/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "bsp/Uart.h"
#include "bsp/uart/UartRxBuffer.h"
#include "charInputOutput/charIoSerial.h"

#include <cstdint>

namespace bsp
{
namespace
{
uint32_t const RX_ERROR_FLAGS = LPUART_STAT_FE_MASK | LPUART_STAT_NF_MASK | LPUART_STAT_PF_MASK;
uint32_t const RE_OFF_TIMEOUT = 10000U;
} // namespace

// IO-17: LPUART receive path with FIFO and RX interrupt (family code, S32K1xx LPUART). Candidate for
// platforms/s32k1xx/bsp together with the Uart instance generation (ARCH-05).
void UartRxBuffer::ensureEnabled()
{
    LPUART_Type& uart = _config.uart;
    uint32_t const enabled
        = LPUART_CTRL_RE_MASK | LPUART_CTRL_RIE_MASK | LPUART_CTRL_ORIE_MASK;
    if (((uart.CTRL & enabled) == enabled) && ((uart.FIFO & LPUART_FIFO_RXFE_MASK) != 0U))
    {
        return;
    }
    NVIC_DisableIRQ(_config.irq);
    // RXFE may only change while the receiver is disabled; the transmitter stays on.
    uart.CTRL = uart.CTRL & ~(enabled);
    for (uint32_t count = 0U; ((uart.CTRL & LPUART_CTRL_RE_MASK) != 0U) && (count < RE_OFF_TIMEOUT);
         ++count)
    {}
    // Keep TX FIFO settings; write-1-to-clear flags (RXUF, TXOF) are cleared on the way.
    uart.FIFO  = (uart.FIFO & LPUART_FIFO_TXFE_MASK) | LPUART_FIFO_RXFE_MASK | LPUART_FIFO_RXUF_MASK
                | LPUART_FIFO_TXOF_MASK;
    uart.FIFO  = uart.FIFO | LPUART_FIFO_RXFLUSH_MASK;
    uart.WATER = uart.WATER & ~LPUART_WATER_RXWATER_MASK; // interrupt from the first byte
    uart.STAT  = LPUART_STAT_OR_MASK | RX_ERROR_FLAGS;    // RW bits of STAT are 0 after Uart::init()
    NVIC_SetPriority(_config.irq, _config.irqPriority);
    NVIC_ClearPendingIRQ(_config.irq);
    NVIC_EnableIRQ(_config.irq);
    uart.CTRL = uart.CTRL | enabled;
}

bool UartRxBuffer::read(uint8_t& byte)
{
    ensureEnabled();
    size_t const tail = _tail;
    if (tail == _head)
    {
        return false;
    }
    byte  = _ring[tail];
    _tail = (tail + 1U) & (RING_SIZE - 1U);
    return true;
}

void UartRxBuffer::isr()
{
    LPUART_Type& uart = _config.uart;
    uint32_t stat     = uart.STAT;
    while ((stat & LPUART_STAT_RDRF_MASK) != 0U)
    {
        uint8_t const byte = static_cast<uint8_t>(uart.DATA & 0xFFU);
        size_t const head  = _head;
        size_t const next  = (head + 1U) & (RING_SIZE - 1U);
        if (next != _tail)
        {
            _ring[head] = byte;
            _head       = next;
        }
        else
        {
            ++_statistics.bufferOverflows; // the newest byte is dropped
        }
        stat = uart.STAT;
    }
    // A set overrun flag blocks further reception until it is cleared.
    uint32_t const flags = stat & (LPUART_STAT_OR_MASK | RX_ERROR_FLAGS);
    if (flags != 0U)
    {
        if ((flags & LPUART_STAT_OR_MASK) != 0U)
        {
            ++_statistics.hardwareOverruns;
        }
        if ((flags & RX_ERROR_FLAGS) != 0U)
        {
            ++_statistics.framingErrors;
        }
        uart.STAT = flags; // write 1 to clear; RW bits of STAT are 0 after Uart::init()
    }
}

} // namespace bsp

extern "C" void putByteToStdout(uint8_t const byte)
{
    static bsp::Uart& uart = bsp::Uart::getInstance(bsp::Uart::Id::TERMINAL);
    uart.write(etl::span<uint8_t const>(&byte, 1U));
}

// Single consumer of the terminal receive buffer (console input, idle task).
extern "C" int32_t getByteFromStdin()
{
    static bsp::UartRxBuffer& rxBuffer = bsp::getUartRxBuffer(bsp::Uart::Id::TERMINAL);
    uint8_t byte                       = 0U;
    if (!rxBuffer.read(byte))
    {
        return -1;
    }
    return byte;
}
