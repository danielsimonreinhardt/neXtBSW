// SPDX-License-Identifier: Apache-2.0
// Host stub of mcu/mcu.h for the FlexCAN host test (COM-26): the register layout and bit macros of the vendor header
// (NXP S32K148_FLEXCAN.h, the same layout for every S32K1xx FlexCAN instance). The registers themselves are real
// memory at the real base addresses (0x4002_4000, 0x4002_5000, 0x4002_B000), guarded page by page by the model in
// FlexCanDeviceHostTest.cpp: every access of the driver is trapped, checked against the message buffers the
// instance has and given the register semantics the driver relies on (write 1 to clear, soft reset, freeze).
#pragma once

#include <cstdint>

#define __IO volatile
#define __O volatile
#define __I volatile const

typedef uint8_t volatile vuint8_t;
typedef uint16_t volatile vuint16_t;
typedef uint32_t volatile vuint32_t;

#include "S32K148_FLEXCAN.h"

// the access qualifiers of the vendor header collide with compiler intrinsics headers included later (windows.h)
#undef __IO
#undef __O
#undef __I
