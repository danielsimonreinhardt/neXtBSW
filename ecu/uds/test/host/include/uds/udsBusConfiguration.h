/* Test profile of the host build (ARCH-07): the generated diagnostic bus of tools/rtegen (uds.bus).
 * Here the UDS stack runs on the second CAN bus (DIAG, bus id 3), not on the first one. */

#pragma once

#define NX_UDS_BUS      DIAG // ::busid::DIAG
#define NX_UDS_BUS_NAME "DIAG"
