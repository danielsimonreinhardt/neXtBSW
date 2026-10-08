/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "busid/BusId.h"

namespace common
{
namespace busid
{
// clang-format off
#define BUS_ID_NAME(BUS) \
    case ::busid::BUS: return #BUS

// The CAN buses of the project (ARCH-07): board name of the bus, same list as in busid/BusId.h.
#define BUS_ID_CAN_NAME(BUS, ID, INDEX) \
    case ID: return #BUS;

// clang-format on

char const* BusIdTraits::getName(uint8_t index)
{
    switch (index)
    {
        BUS_ID_NAME(SELFDIAG);
        NX_BUSID_CAN_BUSES(BUS_ID_CAN_NAME)
        BUS_ID_NAME(ETH_0);
        default: return "INVALID";
    }
}

} // namespace busid
} // namespace common
