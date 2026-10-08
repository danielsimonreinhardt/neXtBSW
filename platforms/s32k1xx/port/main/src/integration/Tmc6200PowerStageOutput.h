#pragma once

/* IO-32: the DRV_EN of a TMC6200 gate driver as power stage output of the debug halt guard (DebugHaltGuard.h).
 *
 * Switching off is Tmc6200::disable() (DRV_EN low, always allowed). An explicit enable is a successful
 * Tmc6200::enable() after that - counted by the driver (Counters::enables), so neither a refused enable nor the
 * driver's own supervision releases the PWM. The driver object lives in its lifecycle system and is attached when
 * that system is created. Hardware independent (host test test/host/DebugHaltGuardHostTest.cpp); call from the
 * task context of the driver.
 *
 * SYS-36: attach() also hands the driver the enable lock of the safe state (on the target the S1 latch,
 * SafeStateHold.h): enable() is refused while it is set, and the DRV_EN that S1 pulled low is no ENABLE_MISMATCH
 * fault any more (before SYS-36 the driver latched "DRV_EN mismatch" as an error after every safe state).
 * holdForSafeState() makes the driver evaluate the lock at once.
 *
 * SYS-139 (decision C2 (a)): for a driver bound to a PWM group with an active short attach() also takes `keep`
 * (ShortHold.h rule 1: the group is in a short and the driver is enabled). While it answers true the lock of the
 * driver reads "not locked" (the integration builds the lock with it), the adapter reports the driver as held to the
 * hold of S1, and switchOffAfterDebugHalt() leaves DRV_EN on - the guard still waits for an explicit enable before it
 * releases the PWM. nullptr (every other driver): exactly the behaviour before. */

#include "DebugHaltGuard.h"
#include "gd/Tmc6200.h"

#include <cstdint>

namespace integration
{

class Tmc6200PowerStageOutput : public IPowerStageOutput
{
public:
#if defined(NX_SAFE_OUTPUT_SELECT)
    /** SYS-139: true while the enable of the attached driver is kept on in an active short. */
    using KeepEnable = bool (*)();

    void attach(::gd::Tmc6200& driver, ::gd::Tmc6200::EnableLock const lock, KeepEnable const keep)
    {
        _driver = &driver;
        _keep   = keep;
        driver.setEnableLock(lock);
    }

    bool holdForSafeState() override
    {
        // SYS-139: the lock of a driver whose enable is kept reads "not locked" - held by the short, not unlocked
        return (_driver != nullptr) && (_driver->updateEnableLock() || kept());
    }

    void switchOffAfterDebugHalt() override
    {
        if (_driver == nullptr)
        {
            return; // no driver yet: DRV_EN is still the low level of the pin configuration
        }
        if (!kept()) // SYS-139: DRV_EN of a group in an active short stays on
        {
            (void)_driver->disable();
        }
        _enablesAtSwitchOff = _driver->getCounters().enables;
        _switchedOff        = true;
    }
#else
    // a project without an active short: exactly the adapter before SYS-139
    void attach(::gd::Tmc6200& driver, ::gd::Tmc6200::EnableLock const lock)
    {
        _driver = &driver;
        driver.setEnableLock(lock);
    }

    bool holdForSafeState() override { return (_driver != nullptr) && _driver->updateEnableLock(); }

    void switchOffAfterDebugHalt() override
    {
        if (_driver == nullptr)
        {
            return; // no driver yet: DRV_EN is still the low level of the pin configuration
        }
        (void)_driver->disable();
        _enablesAtSwitchOff = _driver->getCounters().enables;
        _switchedOff        = true;
    }
#endif

    bool enabledSinceDebugHaltSwitchOff() const override
    {
        return _switchedOff && (_driver != nullptr) && (_driver->getCounters().enables != _enablesAtSwitchOff);
    }

private:
#if defined(NX_SAFE_OUTPUT_SELECT)
    bool kept() const { return (_keep != nullptr) && _keep(); }

    KeepEnable _keep = nullptr;
#endif
    ::gd::Tmc6200* _driver       = nullptr;
    uint32_t _enablesAtSwitchOff = 0U;
    bool _switchedOff            = false;
};

} // namespace integration
