/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "safeIo/SafeIo.h"

#include <hardFaultHandler/safeOutputs.h> // SYS-30: latch of S1 and, with SYS-116, the selected safe state
#include <io/Io.h>
#include <safeIo/SafeIoRegisters.h>
#include <safeSupervisor/SafeStateReadback.h> // SYS-138: the read-back this file implements for the supervisor
#include <safeSupervisor/SafeSupervisor.h>

namespace safety
{

namespace
{
/* SYS-24: context of the first entry of the register list that deviated in a cycle, 0 while none did. Latched until
 * the next init(). A file scope variable instead of a member because SafeIo.h stays the OpenBSW header (one SafeIo
 * per ECU, SafetyManager.cpp); it lies in the MPU protected .bss of this library like the object. */
uint32_t registerFault;

/* SYS-30: context of the first entry of the safe outputs table that did not read back its safe state while S1 was
 * latched, 0 while none did; latched until the next init() like registerFault. */
uint32_t outputFault;

/* SYS-138: true once init() has run - from then on the register list has locked and verified the pin functions and
 * expects every clock gate of the read-back list on, so the read-back judges every entry and a gated clock is not a
 * safe state. Before (the hold of SYS-23 from main() before the BSP start, a fault reaction before the safety task)
 * nothing is judged: the pins are in their reset configuration, pin function disabled, driven by nothing. Never
 * cleared; a reset clears it with the .bss. */
bool readbackArmed;

/* SYS-30 (G1): true if entry `index` of the safe outputs table shows what S1 left behind - the bits of its read-back
 * register under the mask equal the generated expectation (freewheel for a PWM group, the inactive pin level for a
 * safe_off output). SYS-116: for an entry of a PWM group whose walk writes a selected short, freewheel (the
 * expectation) and the short value itself are both a safe state and pass; an entry the short leaves as it is (the
 * bound gate-driver enable, store 0) is not judged.
 * SYS-138 (review K6): an entry whose clock is gated is NOT confirmed (up to SYS-138: not judged). Its register is not
 * read (a read would bus fault), but the walk skipped the entry as well, and a timer whose clock is gated keeps its
 * outputs frozen at whatever level they had - possibly a switch on. The register list of SYS-24 expects every such
 * gate on (checked at compile time in SafeIoRegisterList.cpp), so after init() a gated clock is a fault anyway;
 * with S1 latched it now ends in the reset, which puts the pins into their reset state. */
bool readsBackSafe(size_t const index)
{
    registers::OutputReadback const& r = registers::safeOutputReadbacks[index];
    if ((registers::read(r.gate) & r.gateMask) == 0U)
    {
        return false;
    }
    uint32_t const value = registers::read(r.address);
#if defined(NX_SAFE_OUTPUT_SELECT)
    NxSafeOutputSelect const& entry = ::nxSafeOutputsSelect[index];
    return registers::readbackAccepts(
        r, value, static_cast<uintptr_t>(entry.address), entry.value, ::nxSafeOutputSelectedValue(&entry));
#else
    return registers::readbackAccepts(r, value, 0U, 0U, 0U);
#endif
}

/* SYS-138: context of the first entry of the table (every entry, or those set in `entries`) that is not confirmed, 0 if
 * every one is - and 0 as long as init() has not run (readbackArmed). Reads registers only, writes nothing. */
uint32_t firstUnconfirmed(bool const all, uint32_t const entries)
{
    if (!readbackArmed)
    {
        return 0U;
    }
    for (size_t index = 0U; index < registers::safeOutputReadbackCount; ++index)
    {
        if ((!all) && ((index >= 32U) || ((entries & (static_cast<uint32_t>(1U) << index)) == 0U)))
        {
            continue;
        }
        if (!readsBackSafe(index))
        {
            return registers::context(registers::RegisterClass::SAFE_OUTPUT, index);
        }
    }
    return 0U;
}
} // namespace

uint32_t safeIoRegisterFault() { return registerFault; }

uint32_t safeIoOutputFault() { return outputFault; }

// SYS-138: safeSupervisor/SafeStateReadback.h - the read-back of SafeIo::cyclic() for the hold of S1, the degraded
// groups and the S1 + S2 sequence of the safe supervisor
uint32_t safeStateReadback() { return firstUnconfirmed(true, 0U); }

uint32_t degradedStateReadback(uint32_t const entries) { return firstUnconfirmed(false, entries); }

void SafeIo::init()
{
    _lastCheckState = true;
    registerFault   = 0U;
    outputFault     = 0U;
    // SYS-138: from here on the read-back judges every entry (and a gated clock as not confirmed), see readbackArmed
    readbackArmed = true;

    // SYS-24 (F4, SM-21): lock every lockable register of the list generated from the configuration - the pin
    // control registers of the PWM pins, of the safe_off outputs and of the role pin safety.locked_pin (before SYS-24
    // only that one pin, the scope of the OpenBSW demo). The value found is locked as it is.
    registers::lock(registers::safeIoRegisters, registers::safeIoRegisterCount);

    // It is necessary to check the registers AFTER locking to ensure that the correct values were locked, and that
    // the lock took (SYS-42: a lock write that was lost or a lock bit stuck at 0 left the register reconfigurable).
    // Everything the list judges is read back: function, drive strength, pull and lock bit.
    size_t const deviating = registers::firstDeviation(
        registers::safeIoRegisters, registers::safeIoRegisterCount, 0U, true);
    if (deviating < registers::safeIoRegisterCount)
    {
        auto& supervisor = safety::SafeSupervisor::getInstance();
        supervisor.lockRegisterMonitor.trigger(
            registers::context(registers::safeIoRegisters[deviating].registerClass, deviating));
    }
}

void SafeIo::cyclic()
{
    // SYS-24 (F4, SM-22): every entry of the generated list, every cycle (a few ten reads, no distribution over
    // several cycles needed): locked pin functions, direction and input enable of their GPIO pins, the clock gates
    // of their peripherals and the static configuration of the PWM timer. A deviation is latched: the safe state is
    // entered once and not released when the register drifts back.
    if (registerFault == 0U)
    {
        size_t const deviating = registers::firstDeviation(
            registers::safeIoRegisters, registers::safeIoRegisterCount, 0U, false);
        if (deviating < registers::safeIoRegisterCount)
        {
            registerFault
                = registers::context(registers::safeIoRegisters[deviating].registerClass, deviating);
        }
    }

    // SYS-30 (gap G1, SM-23): while S1 is latched, confirm by read-back that the walk took effect - the software
    // output control of every PWM group and the pin level of every safe_off output (NX_SAFE_OUTPUT_READBACKS). The
    // walk ran with a barrier before S1 was latched or in the same safety context, and the drivers keep their
    // outputs off while the latch is set, so one cycle later the state must be there. An entry that is not is S1
    // that did not take effect: reported once as LOCK_REGISTER_ERROR (S1 + S2) - the reset is the one reaction left
    // that puts the pins into their reset state. Latched until the next init().
    if ((outputFault == 0U) && (::nxSafeStateIsLatched() != 0))
    {
        // SYS-138: the same read-back the supervisor uses in the hold of S1 and before a reset (firstUnconfirmed)
        uint32_t const unconfirmed = firstUnconfirmed(true, 0U);
        if (unconfirmed != 0U)
        {
            outputFault      = unconfirmed;
            auto& supervisor = safety::SafeSupervisor::getInstance();
            supervisor.lockRegisterMonitor.trigger(outputFault);
        }
    }

    // The demonstration of the OpenBSW safety demo stays: the pin safety.test_pin must still be configured as input.
    // Unlike the register list its return releases the indicator (console "safety" of the demo).
    ::bios::Io::PinConfiguration pinConfigSafetyTest;
    ::bios::Io::getConfiguration(::bios::Io::SAFETY_TEST, pinConfigSafetyTest);
    bool const checkState = (pinConfigSafetyTest.dir == ::bios::Io::_IN) && (registerFault == 0U);
    if (checkState != _lastCheckState)
    {
        auto& supervisor = safety::SafeSupervisor::getInstance();
        // SYS-169: the context of the report names the deviating entry of the list (registerFault), 0 stands for the
        // SAFETY_TEST pin; CHECK_REGISTER_CONTEXT_OK is the return to a fine check
        supervisor.checkRegisterMonitor.trigger(
            checkState ? safety::SafeSupervisor::CHECK_REGISTER_CONTEXT_OK : registerFault);
        _lastCheckState = checkState;
    }
}

} // namespace safety
