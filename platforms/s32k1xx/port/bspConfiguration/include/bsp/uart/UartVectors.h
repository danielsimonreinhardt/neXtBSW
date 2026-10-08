#pragma once

/**
 * LPUART interrupt vectors of this board port (ARCH-15).
 *
 * The vector table (SYS-170: generated from the derivative's list startup/nxIrqVectors.inc, included by
 * main/src/bsp/startUp.S) routes the LPUART transmit/receive interrupts through the SAFE_ISR wrapper
 * Safe_LPUART<n>_RxTx_IRQHandler to LPUART<n>_RxTx_IRQHandler; safeLifecycle/IsrWrapper.cpp defines those names weak
 * (DefaultISR) so that unused instances need no handler. The UART configuration (bspConfiguration/src/bsp/uart/UartConfig.cpp) defines the handler of every
 * UART the project uses; without a vector for its instance the handler would never run and the receive
 * interrupt would fail silently - exactly the kind of silent error ARCH-09 found for the instance itself (B2).
 *
 * Bit n of NX_PORT_LPUART_VECTORS stands for LPUART<n>. The generated configuration is checked against it at
 * compile time (static_assert in UartConfig.cpp), so a used UART without a vector stops the build with a
 * message naming that UART instead of losing its receive path at run time.
 *
 * Keep in sync with the generated vector list of the eval board derivative; the host test
 * tools/bswgen/test_bswgen.py::UartTest::test_port_vector_mask_matches_the_vector_table compares both.
 */
#define NX_PORT_LPUART_VECTORS 0x07U // LPUART0, LPUART1, LPUART2 (S32K14x)
