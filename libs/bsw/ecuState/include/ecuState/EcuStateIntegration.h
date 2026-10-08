#pragma once

/* Maps the generated project parameters (EcuStateConfig.h, tools/rtegen from the ECU description section
 * ecu_state:, defaults from libs/bsw/ecuState/module.yaml) to the configuration structure. Include the generated
 * header before this one. The parameter names of the manifest are known here and nowhere else in C++. */

#include "ecuState/EcuState.h"

#ifndef NX_ECU_STATE_PARAM_MIN_RUN_MS
#error "include the generated EcuStateConfig.h (tools/rtegen) before ecuState/EcuStateIntegration.h"
#endif
#ifndef NX_ECU_STATE_WAKEUP_SOURCES
#error "EcuStateConfig.h is older than SYS-02 (NX_ECU_STATE_WAKEUP_SOURCES missing): regenerate it"
#endif

// clang-format off
#define ECU_STATE_CONFIG()                                                                             \
    ::ecustate::EcuStateConfig{                                                                        \
        NX_ECU_STATE_PARAM_MIN_RUN_MS,                                                                 \
        NX_ECU_STATE_PARAM_POST_RUN_MS,                                                                \
        NX_ECU_STATE_PARAM_SHUTDOWN_TIMEOUT_MS,                                                        \
        NX_ECU_STATE_PARAM_SLEEP_TIMEOUT_MS,                                                           \
        {NX_ECU_STATE_PARAM_NVM_TIMEOUT_MS, NX_ECU_STATE_PARAM_CAN_STOP_TIMEOUT_MS,                             ::ecustate::SAFE_OUTPUTS_STEP_TIMEOUT_MS},                                                    \
        ::ecustate::WakeupSelection{                                                                   \
            NX_ECU_STATE_WAKEUP_SOURCES, NX_ECU_STATE_PARAM_WAKEUP_CYCLIC_MS},                         \
        ::ecustate::WakeupDuringShutdown::NX_ECU_STATE_PARAM_WAKEUP_DURING_SHUTDOWN,                   \
        ::ecustate::AfterWatchdogReset::NX_ECU_STATE_PARAM_AFTER_WATCHDOG_RESET,                       \
        ::ecustate::ShutdownStepError::NX_ECU_STATE_PARAM_STEP_ERROR}
// clang-format on
