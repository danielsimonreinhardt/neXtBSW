#pragma once

/* ARCH-52: framework of the generated module binding (integration/moduleBinding.h, tools/bswgen/modules.py
 * render_binding_header). Family neutral: it only knows the adapter interface of the debug halt guard and the safe
 * state hold (DebugHaltGuard.h); it lives next to them in the family integration until both move into a neutral
 * library (step 2 of doc/architecture/treiber-anbindung-analyse.md).
 *
 * The generated block creates one SafeOffOutput per safe_off connection of a bound instance (module.yaml
 * connect.<x>.safe_off). The adapter is the same for every driver; what "off", "explicit enable" and "lock" mean
 * for a driver is the access struct SafeOff_<connection> of the module's trait:
 *
 *   struct SafeOff_<connection>
 *   {
 *       using System = ...;                             // the lifecycle component of the instance
 *       static void off(System& s);                     // output off now, driver state follows (always allowed)
 *       static uint32_t enables(System const& s);       // count of explicit enables so far
 *       static void setLock(System& s, SafeOffLock l);  // lock the driver asks before every switch-on
 *       static bool updateLock(System& s);              // evaluate the lock now; true if the driver is locked
 *   };
 *
 * This is exactly what the hand-written adapters Tmc6200PowerStageOutput.h and HighSideSwitchPowerStageOutput.h
 * do, with the driver calls taken out: a module does not have to know this integration to be switched off by it.
 * ARCH-66: the high-side switch uses it (access struct highside::HighSideSwitchSafeOff); its hand-written adapter
 * is gone, the lockstep comparison with a copy of it is test/host/SafeOffEquivalenceHostTest.cpp. The gate driver
 * keeps Tmc6200PowerStageOutput.h until the adapter can keep an enable in an active short (SYS-139).
 * Hardware independent (host tests tools/bswgen/test/binding_host, test/host/SafeOffEquivalenceHostTest.cpp,
 * DebugHaltGuardHostTest.cpp, SafeStateHoldHostTest.cpp). Call from the task context of the driver. */

#include "DebugHaltGuard.h"

#include <cstdint>

namespace integration
{
namespace binding
{
/** Lock of the safe state a safe_off driver asks before every switch-on (SYS-36). */
using SafeOffLock = bool (*)();

/**
 * The lock the family integration hands the safe_off drivers of a bound instance: the S1 latch of the safe state
 * hold, and for an instance driven by a PWM group of a degraded mapping also that group (IO-45). Defined by the
 * family integration (Integration.cpp).
 */
SafeOffLock safeOffLock(bool pwmBound);
} // namespace binding

template<class Access>
class SafeOffOutput final : public IPowerStageOutput
{
public:
    using System = typename Access::System;

    void attach(System& system, binding::SafeOffLock const lock)
    {
        _system = &system;
        Access::setLock(system, lock);
    }

    bool holdForSafeState() override { return (_system != nullptr) && Access::updateLock(*_system); }

    void switchOffAfterDebugHalt() override
    {
        if (_system == nullptr)
        {
            return; // no system yet: the output is still at the low level of the pin configuration
        }
        Access::off(*_system);
        _enablesAtSwitchOff = Access::enables(*_system);
        _switchedOff        = true;
    }

    bool enabledSinceDebugHaltSwitchOff() const override
    {
        return _switchedOff && (_system != nullptr) && (Access::enables(*_system) != _enablesAtSwitchOff);
    }

private:
    System* _system              = nullptr;
    uint32_t _enablesAtSwitchOff = 0U;
    bool _switchedOff            = false;
};

} // namespace integration
