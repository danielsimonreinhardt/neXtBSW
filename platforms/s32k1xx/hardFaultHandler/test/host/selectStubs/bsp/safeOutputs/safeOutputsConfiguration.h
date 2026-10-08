// SYS-116: host stub of the generated bsp/safeOutputs/safeOutputsConfiguration.h (tools/bswgen) for
// SafeStateSelectHostTest.cpp - a project with two PWM groups and register addresses of an S32K1 table. What the real
// generator writes is checked by tools/bswgen/test_bswgen_safe_state.py; this table covers what one generated
// project alone cannot: two selection words, a group that allows only one short, an enable of each kind.
//
//   entry 0  FTM0 SWOCTRL, group 1 (freewheel, short low, short high, start test), channels 0..5, low sides 1/3/5
//   entry 1  FTM3 SWOCTRL, group 2 (freewheel, short low), channels 0..3, low sides 1/3
//   entry 2  PTC PCOR bit 11, enable bound to group 1: left as it is (0) in both shorts
//   entry 3  PTD PSOR bit 17, enable bound to group 2: left as it is in short low, short high not allowed
//   entry 4  PTB PCOR bit 4, enable of no group: off in every state
//   entry 5  PTE PCOR bit 3, no group, clock of PORTE gated in the model
#pragma once

#include <cstdint>

#define NX_SAFE_OUTPUT_COUNT     6
#define NX_SAFE_OFF_OUTPUT_COUNT 4

#define NX_SAFE_OUTPUTS(X)                                                  \
    X(MOT1, 0x40038094U, 0x3FU, 0x40065060U, 0x40000000U)                  \
    X(MOT2, 0x40026094U, 0x0FU, 0x40065098U, 0x40000000U)                  \
    X(EN1, 0x400FF088U, (1UL << 11U), 0x40065124U, 0x40000000U)            \
    X(EN2, 0x400FF0C4U, (1UL << 17U), 0x40065128U, 0x40000000U)            \
    X(OTHER, 0x400FF048U, (1UL << 4U), 0x40065120U, 0x40000000U)           \
    X(GATED, 0x400FF108U, (1UL << 3U), 0x4006512CU, 0x40000000U)

#define NX_SAFE_OUTPUT_SELECT_GENERATED 1
#define NX_SAFE_STATE_GROUP_COUNT       2

#define NX_SAFE_STATE_GROUPS(X)     \
    X(MOT1, 0U, 0x7U, 1U, 200U)     \
    X(MOT2, 1U, 0x3U, 0U, 0U)

// clang-format off
#define NX_SAFE_OUTPUTS_SELECT(X) \
    X(MOT1, 0x40038094U, 0x3FU, 0x40065060U, 0x40000000U, 1U, 0x40065060U, 0x40000000U, 0x2A3FU, 0x153FU) \
    X(MOT2, 0x40026094U, 0x0FU, 0x40065098U, 0x40000000U, 2U, 0x40065098U, 0x40000000U, 0x0A0FU, 0x0FU) \
    X(EN1, 0x400FF088U, (1UL << 11U), 0x40065124U, 0x40000000U, 1U, 0x40065060U, 0x40000000U, 0U, 0U) \
    X(EN2, 0x400FF0C4U, (1UL << 17U), 0x40065128U, 0x40000000U, 2U, 0x40065098U, 0x40000000U, 0U, (1UL << 17U)) \
    X(OTHER, 0x400FF048U, (1UL << 4U), 0x40065120U, 0x40000000U, 0U, 0U, 0U, (1UL << 4U), (1UL << 4U)) \
    X(GATED, 0x400FF108U, (1UL << 3U), 0x4006512CU, 0x40000000U, 0U, 0U, 0U, (1UL << 3U), (1UL << 3U))
// clang-format on
