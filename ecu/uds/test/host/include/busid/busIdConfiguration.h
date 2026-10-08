/* Test profile of the host build (ARCH-07): what tools/bswgen generates for a project with two CAN
 * buses. The diagnostic stack must work on the second one (uds/udsBusConfiguration.h: NX_UDS_BUS DIAG),
 * so nothing in ecu/uds may assume the first bus or the bus id 2. */

#pragma once

#define NX_BUSID_SELFDIAG 1

#define NX_BUSID_CAN_BUSES(X) \
    X(VEH, 2, 0)              \
    X(DIAG, 3, 1)
#define NX_BUSID_CAN_COUNT 2
#define NX_BUSID_VEH       2
#define NX_BUSID_DIAG      3
#define NX_BUSID_FIRST_CAN   VEH
#define NX_BUSID_ALIAS_CAN_0 VEH
#define NX_BUSID_ETH_0     4
#define NX_BUSID_ETH_1     5
#define NX_BUSID_LAST      5
