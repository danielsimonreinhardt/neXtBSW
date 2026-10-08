#pragma once

#include "highSideSwitch/HighSideSwitch.h"
#include "highSideSwitch/IHighSideSwitchIo.h"

namespace highside
{

/**
 * One high-side switch with its pins: runs the state machine and writes IN/DEN after every cycle
 * and every command. Hardware independent (IHighSideSwitchIo), single context.
 *
 * Write order: IN before DEN when switching off, DEN before IN when switching on. The outputs are
 * rewritten every cycle, so a different writer is overridden within one cycle. If a write fails
 * while the switch is on, it is switched off and both outputs are written low again.
 *
 * SYS-36, switch-on lock: the integration can attach a lock (setSwitchOnLock(), e.g. the latch of
 * the safe state S1). The channel asks it at the start of every cycle, before every command and
 * before and after every write of IN high; while it reports true the switch is locked
 * (HighSideSwitch::lock()), IN is never written high and "on" is refused with LOCKED. Once it
 * reports false again the switch is unlocked and stays off until the next "on". The query must be
 * cheap and must not block (it is a read of a latch).
 */
class HighSideSwitchChannel
{
public:
    /** SYS-36: true while switching on is not allowed. */
    using SwitchOnLock = bool (*)();

    HighSideSwitchChannel(IHighSideSwitchIo& io, HighSideSwitchConfig const& config);
    HighSideSwitchChannel(HighSideSwitchChannel const&)            = delete;
    HighSideSwitchChannel& operator=(HighSideSwitchChannel const&) = delete;

    /** Writes the safe default (off) to the outputs. */
    void init();

    /** Samples the current sense, runs one supervision cycle and writes the outputs. */
    void cycle();

    HighSideSwitch::CommandResult requestOn();
    HighSideSwitch::CommandResult requestOff();
    HighSideSwitch::CommandResult reset();

    /** SYS-36: attaches the switch-on lock (nullptr: none). Takes effect at the next call. */
    void setSwitchOnLock(SwitchOnLock const lock) { _lock = lock; }

    /**
     * SYS-36: asks the lock now and drives the outputs accordingly (IN low at once when it is set).
     * \return true while the switch is locked; false without a lock attached
     */
    bool updateSwitchOnLock();

    HighSideSwitch const& getSwitch() const { return _switch; }

    /** Number of output updates with at least one failed write. */
    uint32_t getOutputErrors() const { return _outputErrors; }

    /** Unconverted converter code of the last valid sense sample (diagnosis). */
    uint32_t getLastSenseRaw() const { return _lastSenseRaw; }

private:
    void applyOutputs();
    void syncLock();

    IHighSideSwitchIo& _io;
    HighSideSwitch _switch;
    SwitchOnLock _lock;
    uint32_t _outputErrors;
    uint32_t _lastSenseRaw;
};

} // namespace highside
