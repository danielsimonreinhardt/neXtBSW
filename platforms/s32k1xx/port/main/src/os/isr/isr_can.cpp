/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

// neXtBSW (COM-08): copy of executables/referenceApp/platforms/s32k148evb/main/src/os/isr/isr_can.cpp. Deviation:
// the handlers come from the generated vector list NX_CAN_MB_VECTORS (every used bus, the message buffer vectors
// of the derivative, PLAT-12/ARCH-07) instead of the two fixed CAN0 handlers, so call_can_isr_RX/TX take the
// device (FlexCAN instance) as parameter. A combined vector (S32K11x) serves transmit first, then receive.
#include "bsp/can/canDeviceConfiguration.h"

#include <cstdint>

extern "C"
{
extern void call_can_isr_RX(uint8_t device);
extern void call_can_isr_TX(uint8_t device);

// clang-format off
#define NX_CAN_ISR_RX(bus, device, isr, irq)    void isr() { call_can_isr_RX(device); }
#define NX_CAN_ISR_TX(bus, device, isr, irq)    void isr() { call_can_isr_TX(device); }
#define NX_CAN_ISR_RX_TX(bus, device, isr, irq) void isr() { call_can_isr_TX(device); call_can_isr_RX(device); }
// clang-format on
NX_CAN_MB_VECTORS(NX_CAN_ISR)
#undef NX_CAN_ISR_RX
#undef NX_CAN_ISR_TX
#undef NX_CAN_ISR_RX_TX

} /* extern "C" */
