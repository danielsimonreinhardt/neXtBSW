#pragma once

/**
 * Pin detect interrupt vectors of this family port (IO-07), same principle as the LPUART vectors of ARCH-15
 * (bsp/uart/UartVectors.h) and the further CAN message buffer vectors of ARCH-07.
 *
 * Bit n of NX_PORT_PIN_VECTORS stands for the pin detect interrupt of port n (0 = PORTA .. 4 = PORTE): the bit
 * is set when the vector table (SYS-170: the generated startup/nxIrqVectors.inc in main/src/bsp/startUp.S, through
 * the SAFE_ISR wrapper Safe_PORT<x>_IRQHandler) routes that interrupt to a weak PORT<x>_IRQHandler,
 * which the pin-interrupt dispatcher of the port (bspConfiguration/src/bsp/pinIrq/PinIrqConfig.cpp, IO-52) then
 * defines for every port a consumer pin of the project (today: a hall sensor) sits on. Without the vector the handler would never run: the pin interrupt would
 * land in DefaultISR (vIllegalISR) or be lost, and the measurement would report standstill forever - exactly
 * the kind of silent error ARCH-09 found for a missing instance (B2).
 *
 * Since the merge of IO-07 the vector table routes PORTA..PORTE to weak PORT<x>_IRQHandler symbols (since SYS-170
 * weak in safeLifecycle/IsrWrapper.cpp) that fall back to DefaultISR unless the pin-interrupt dispatcher defines them, so the value is 0x1F. Clearing a bit makes a
 * project with a hall sensor on that port stop the build with a message naming the port instead of measuring
 * nothing at run time.
 *
 * Keep in sync with the generated vector list of the eval board derivative; the host test
 * tools/bswgen/test_bswgen.py::BswGenHallTest::test_port_vector_mask_matches_the_vector_table compares both.
 */
#define NX_PORT_PIN_VECTORS 0x1FU // PORTA..PORTE routed to weak PORT<x>_IRQHandler (startup/nxIrqVectors.inc)
