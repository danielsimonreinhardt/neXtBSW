/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#pragma once

#include <cstdint>

namespace safety
{

/**
 * SafeState puts the outputs of the project into their safe state.
 *
 * SYS-06: the safe state is the **safe outputs table of the project** (generated
 * bsp/safeOutputs/safeOutputsConfiguration.h, walked by nxSafeOutputsSwitchOff() of
 * hardFaultHandler/safeOutputs.h) - the very same table the hard fault and watchdog handlers and the halt
 * deadman of the debug halt guard work through (IO-32). One list, one order, one behaviour: every PWM group of
 * the project is forced to its inactive level (FTM software output control) and every output a module connection
 * declares safe_off (module.yaml connect.<name>.safe_off, e.g. the enable of a gate driver) is driven inactive.
 * The walk needs no stack, no RAM and no driver object, skips a peripheral whose clock is gated and ends with a
 * data synchronization barrier, so it is callable from any context - task, supervisor or interrupt.
 *
 * Board neutral: a board without power outputs has an empty table, and entering the safe state then writes no
 * register at all. Whether a board has such outputs is a fact of the board and of the modules it uses, never of
 * this code.
 *
 * Besides the table an optional **indicator** output of the project is switched (project.yaml
 * safety.safe_state_output, generated as ::bios::Io::SAFETY_SAFE_STATE_PIN). It only shows the safe state - on
 * the eval board it is an LED. Before SYS-06 that LED *was* the whole safe state (OpenBSW safety demo).
 *
 * No automatic way back for the power outputs: nothing here switches a power output back on, because this class
 * does not know the state a driver would have to return to. A power stage comes back only with an explicit enable
 * of its own driver - the same rule the debug halt guard follows after its deadman has switched the outputs off
 * behind the drivers' backs.
 *
 * SYS-36: S1 is **held**, not only entered. Board finding 2026-09-21: after `safety dst` KL30B was off for about
 * 10 ms only - the table walk had driven IN low, and the next cycle of the high-side switch driver wrote it high
 * again. enterSafeState() therefore latches S1 (nxSafeStateLatchSet(), hardFaultHandler/safeOutputs.h) before it
 * walks the table. Every driver of a safe_off output reads the latch in its cycle and before every switch-on and
 * keeps that output off while it is set, and the integration walks the table again in every poll while it is
 * set (integration/SafeStateHold.h).
 *
 * SYS-35 (user decision safe-state-rueckweg, 2026-09-21): leaveSafeState() is the return path and the only place
 * that clears the latch. While S1 is latched every enable of a safe_off driver is refused ("not allowed: safe
 * state"); after leaving, an explicit enable of the driver lifts the lock of its output, and the PWM inhibit of
 * the table walk is released with it (integration/SafeStateHold.h, DebugHaltGuard.h). Board finding 2026-09-21
 * before SYS-35: `gd enable` reported enabled, IOIN 1, but the FTM software output control of the walk stayed
 * set - the phases floated until a reboot.
 */
class SafeState
{
public:
    // [PUBLIC_API_START]

    /**
     * Puts the outputs into their safe state and latches it: the latch of S1 (SYS-36), the safe outputs table of
     * the project (PWM groups inactive, safe_off outputs off), then the indicator output. Callable from any
     * context: no blocking, no allocation, no driver call.
     */
    static void enterSafeState();

    /**
     * SYS-35: leaves S1 - clears the latch of SYS-36, then releases the indicator output.
     *
     * Switches no power output on: the outputs of the table stay off until the explicit enable of their driver,
     * which the drivers accept again from now on.
     */
    static void leaveSafeState();

    /**
     * SYS-117: the degraded reaction SD of the safe supervisor - drives only the entries of the safe outputs table
     * whose bit is set in `entries` (bit i = entry i, as tools/nxconfig derives the safe-output groups) to their safe
     * state, with the walk of nxSafeOutputsSwitchOff() (gate check, order, barrier). Touches neither the latch of S1
     * nor the indicator: the rest of the ECU keeps running. Callable from any context like enterSafeState(); own
     * translation unit (SafeStateDegraded.cpp), so a project without a degraded mapping does not link it.
     */
    static void enterDegradedState(uint32_t entries);

    // [PUBLIC_API_END]
};

} // namespace safety
