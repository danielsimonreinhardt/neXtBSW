#include "highSideSwitch/HighSideSwitchChannel.h"

namespace highside
{

HighSideSwitchChannel::HighSideSwitchChannel(IHighSideSwitchIo& io, HighSideSwitchConfig const& config)
: _io(io), _switch(config), _lock(nullptr), _outputErrors(0U), _lastSenseRaw(0U)
{}

void HighSideSwitchChannel::init() { applyOutputs(); }

void HighSideSwitchChannel::cycle()
{
    SenseSample sample{0U, 0U};
    bool const adcOk = _io.readSense(sample);
    if (adcOk)
    {
        _lastSenseRaw = sample.raw;
    }
    _switch.step(adcOk, sample.loadMa);
    applyOutputs(); // asks the lock first (SYS-36)
}

HighSideSwitch::CommandResult HighSideSwitchChannel::requestOn()
{
    syncLock(); // SYS-36: a lock set since the last cycle refuses this "on"
    HighSideSwitch::CommandResult const result = _switch.requestOn();
    applyOutputs();
    return result;
}

HighSideSwitch::CommandResult HighSideSwitchChannel::requestOff()
{
    HighSideSwitch::CommandResult const result = _switch.requestOff();
    applyOutputs();
    return result;
}

HighSideSwitch::CommandResult HighSideSwitchChannel::reset()
{
    HighSideSwitch::CommandResult const result = _switch.reset();
    applyOutputs();
    return result;
}

bool HighSideSwitchChannel::updateSwitchOnLock()
{
    applyOutputs();
    return _switch.isLocked();
}

void HighSideSwitchChannel::syncLock()
{
    bool const locked = (_lock != nullptr) && _lock();
    if (locked)
    {
        _switch.lock();
    }
    else if (_switch.isLocked())
    {
        // Only the explicit return path clears the lock source (SYS-35); the switch stays off.
        _switch.unlock();
    }
    else
    {
        // not locked, nothing to do
    }
}

void HighSideSwitchChannel::applyOutputs()
{
    // SYS-36: the lock decides before any output is written - a locked switch never writes IN high.
    syncLock();
    bool const enable     = _switch.isEnableOutput();
    bool const diagEnable = _switch.isDiagEnableOutput();
    bool ok               = true;
    // IN before DEN when turning off, DEN before IN when turning on.
    if (!enable)
    {
        ok = _io.setEnable(false);
    }
    ok = _io.setDiagEnable(diagEnable) && ok;
    if (enable)
    {
        ok = _io.setEnable(true) && ok;
        // SYS-36: the safe state may have been entered (by an interrupt) between the check above and this
        // write. Ask again and take IN back at once instead of leaving it high for one cycle.
        syncLock();
        if (_switch.isLocked())
        {
            (void)_io.setEnable(false);
            (void)_io.setDiagEnable(false);
            return;
        }
    }
    if (!ok)
    {
        if (_outputErrors != UINT32_MAX)
        {
            ++_outputErrors;
        }
        if (enable)
        {
            (void)_switch.requestOff();
            (void)_io.setEnable(false);
            (void)_io.setDiagEnable(false);
        }
    }
}

} // namespace highside
