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

#include "busid/busIdConfiguration.h"
#include "common/busid/BusId.h"

#include <cstdint>

/**
 * Bus ids of this ECU, generated from the project (ARCH-07): the local diagnostic bus, one id per CAN bus
 * the project uses (busid::<bus> with the board name of the bus, in the order of use.can), then Ethernet.
 * busid/busIdConfiguration.h comes from tools/bswgen, BusId.cpp turns the same list into the bus names.
 */
namespace busid
{
static constexpr uint8_t SELFDIAG = NX_BUSID_SELFDIAG;

// one constant per used CAN bus, e.g. CAN0 = 2
#define NX_BUSID_CONSTANT(bus, id, index) static constexpr uint8_t bus = id;
NX_BUSID_CAN_BUSES(NX_BUSID_CONSTANT)
#undef NX_BUSID_CONSTANT

#ifdef NX_BUSID_ALIAS_CAN_0
// OpenBSW calls the first CAN bus CAN_0; its transportRouterSimple (this ECU has its own router in
// ecu/uds) is compiled with the busid of the application, so the name has to exist.
static constexpr uint8_t CAN_0 = NX_BUSID_ALIAS_CAN_0;
#endif

static constexpr uint8_t CAN_BUS_COUNT = NX_BUSID_CAN_COUNT;
static constexpr uint8_t ETH_0         = NX_BUSID_ETH_0;
static constexpr uint8_t ETH_1         = NX_BUSID_ETH_1;
static constexpr uint8_t LAST_BUS      = NX_BUSID_LAST;

} // namespace busid
