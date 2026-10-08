/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#pragma once

// ARCH-15: one identifier per used board UART, generated from the configuration (board uart[].name,
// project use.uart) instead of a hand-written list. The instance, the baud rate, the receive interrupt and its
// priority follow from the same entry (UartConfig.cpp).
#include <bsp/uart/uartConfiguration.h>

#if NX_UART_COUNT == 0
#error "the project uses no UART (use.uart), the board port needs one for the console (bsp/stdIo)"
#endif

namespace bsp
{

enum class Uart::Id : size_t
{
#define NX_UART_ID(uart, index, registers, label, baud, irq, isr, level) uart = index,
    NX_UART_INSTANCES(NX_UART_ID)
#undef NX_UART_ID
    INVALID = NX_UART_COUNT,
    // Console of the ECU: stdin and stdout use this identifier (bsp/stdIo, ecu/application app.cpp). The
    // project selects the UART behind it (NX_UART_CONSOLE, generated); a board UART must not be named
    // TERMINAL or INVALID (checked by tools/nxconfig/uart.py).
    TERMINAL = NX_UART_CONSOLE,
};

static constexpr size_t NUMBER_OF_UARTS = static_cast<size_t>(Uart::Id::INVALID);

} // namespace bsp
