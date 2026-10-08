/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "safeIo/SafeState.h"

/* SYS-06: the safe state is the safe outputs table of the project - the same one the fault handlers and the halt
 * deadman of the debug halt guard use (IO-32, one list instead of a second one that could drift apart). The walk
 * is the static inline nxSafeOutputsSwitchOff(); the table itself comes from the generated
 * bsp/safeOutputs/safeOutputsConfiguration.h through the board port (weak empty default in the handler library,
 * so a project without power outputs links and writes nothing). */
#include <hardFaultHandler/safeOutputs.h> // SYS-36: also the latch of S1 (nxSafeStateLatchSet)
#include <io/Io.h>

#include <cstdint>

namespace safety
{

namespace
{
/* Indicator of the safe state (project.yaml safety.safe_state_output): it shows the state, it is not the state.
 * On the eval board it is an LED; before SYS-06 it was the whole safe state of the OpenBSW demo. */
constexpr auto SAFETY_PIN_ID          = ::bios::Io::SAFETY_SAFE_STATE_PIN;
constexpr uint32_t SAFETY_PIN_DISABLE = static_cast<uint32_t>(::bios::Io::PINDISABLE);
constexpr uint32_t SAFETY_PIN_ENABLE
    = static_cast<uint32_t>(::bios::Io::GPIO) | static_cast<uint32_t>(::bios::Io::STRENGTH_ON);

void setIndicator(uint32_t const pinCfg)
{
    ::bios::Io::PinConfiguration pinConfig;
    ::bios::Io::getConfiguration(SAFETY_PIN_ID, pinConfig);
    pinConfig.pinCfg = pinCfg;
    ::bios::Io::setConfiguration(SAFETY_PIN_ID, pinConfig);
}
} // namespace

void SafeState::enterSafeState()
{
    /* SYS-36: latch S1 first, then the table. The latch is one store; set before the walk, a driver that runs
     * between the two (only possible if a caller has interrupts enabled) already sees it and keeps its safe_off
     * output off instead of rewriting it high. The drivers read the latch in their cycle, so they do not need to
     * be called here - nothing in this function blocks, allocates or needs a driver object (interrupt, suspended
     * interrupts, hold loop of SYS-23). */
    ::nxSafeStateLatchSet();
    // The power outputs next: whatever else fails afterwards, the energy is off.
    ::nxSafeOutputsSwitchOff();
    setIndicator(SAFETY_PIN_DISABLE);
}

void SafeState::leaveSafeState()
{
    /* SYS-35 (user decision safe-state-rueckweg, 2026-09-21): the return path of the power outputs. This is the only
     * place that clears the latch of SYS-36. Clearing it switches nothing on: every safe_off driver unlocks and
     * stays off until its next explicit enable, and the PWM inhibit the table walk left behind is released by the
     * integration only after such an enable (SafeStateHold.h hands it to the debug halt guard). Before this
     * point every enable was refused ("refused: safe state"). */
    ::nxSafeStateLatchClear();
    setIndicator(SAFETY_PIN_ENABLE);
}

} // namespace safety
