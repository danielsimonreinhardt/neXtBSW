#pragma once

/* Maps the generated macros of one instance (integration/highSideSwitchConfiguration.h, tools/bswgen/modules.py
 * from module.yaml parameters and board.yaml devices[].connect) to the driver structures. The parameter names
 * here are the ones declared in module.yaml; host test: test/host/HighSideSwitchIntegrationHostTest.cpp.
 *
 *   NX_MODULE_HIGHSIDESWITCH_INSTANCES(X)       X(<device>) per instance
 *   HIGH_SIDE_SWITCH_CONFIG(<device>)           constant expression of type HighSideSwitchConfig
 *   HIGH_SIDE_SWITCH_NAMES(<device>)            constant expression of type HighSideSwitchNames
 *   NX_DEVICE_<device>_IO_IN/_DEN/_IS           board entry names of the connections (used by the board port)
 *
 * The current thresholds are load currents in mA (IO-21). The board port converts the sense channel NX_DEVICE_<device>_IO_IS
 * with its generated characteristic nxAdcPhys_<channel> (HighSideSwitchSense.h); tools/nxconfig checks that this
 * channel delivers mA and that every threshold lies in its range. */

#include "highSideSwitch/HighSideSwitch.h"
#include "highSideSwitch/HighSideSwitchNames.h"

// clang-format off
#define HIGH_SIDE_SWITCH_CONFIG(device)                                                                \
    ::highside::HighSideSwitchConfig{                                                                   \
        NX_DEVICE_##device##_PARAM_CYCLE_MS,                                                            \
        NX_DEVICE_##device##_PARAM_OVERCURRENT_MA,                                                      \
        NX_DEVICE_##device##_PARAM_OVERCURRENT_DEBOUNCE_MS,                                             \
        NX_DEVICE_##device##_PARAM_FAULT_MA,                                                            \
        NX_DEVICE_##device##_PARAM_FAULT_DEBOUNCE_MS,                                                   \
        NX_DEVICE_##device##_PARAM_SWITCH_ON_BLANKING_MS,                                               \
        NX_DEVICE_##device##_PARAM_OPEN_LOAD_MA,                                                        \
        NX_DEVICE_##device##_PARAM_OFF_MAX_MA,                                                          \
        NX_DEVICE_##device##_PARAM_OFF_DEBOUNCE_MS}

#define HIGH_SIDE_SWITCH_NAMES(device)                                                                 \
    ::highside::HighSideSwitchNames{                                                                    \
        NX_DEVICE_##device##_PARAM_CONSOLE,                                                             \
        NX_DEVICE_##device##_LABEL,                                                                     \
        NX_DEVICE_##device##_LABEL " high-side switch (" NX_DEVICE_##device##_PART ")"}
// clang-format on
