#pragma once

/* SYS-36: the safe state S1 is held, not only entered.
 *
 * Board finding 2026-09-21 (session d3, IO-37): after `safety dst` KL30B was off for about 10 ms only, then the state
 * machine of the high-side switch wrote IN high again. safety::SafeState::enterSafeState() had walked the safe
 * outputs table (the pins go low behind the drivers' backs) and told no driver; the debug halt guard (IO-32) does
 * tell them, the safe state did not. The gate driver hid the gap because it saw DRV_EN low and latched
 * "DRV_EN mismatch" as a fault of its own.
 *
 * The mechanism, three parts, none of them per driver by hand:
 * 1. **Latch** (hardFaultHandler/safeOutputs.h, nxSafeStateLatchSet()): set by every entry into S1 - SafeState, and
 *    through it every reaction of the safe supervisor including the hold of SYS-23. One store: no call into a
 *    driver, no blocking, no allocation, so it works from an interrupt and with all interrupts suspended.
 * 2. **Drivers read it in their own cycle.** Every output a module declares safe_off (module.yaml
 *    connect.<name>.safe_off) has one IPowerStageOutput adapter - the static_assert of Integration.cpp checks their
 *    number against NX_SAFE_OFF_OUTPUT_COUNT, which tools/bswgen derives from the manifests - and attach() hands
 *    that driver the latch as its switch-on lock. The driver asks it at the start of every cycle and before every
 *    switch-on and never drives the output active while it is set (no window of one driver cycle).
 * 3. **This hold** (every poll of the integration, DebugHaltGuardSystem::POLL_MS, in the task of the drivers):
 *    while the latch is set it walks the safe outputs table again - the same thing holdSafeState() of SYS-23 does in
 *    every round, and a backstop for anything that wrote a pin of the table - and asks every adapter to evaluate
 *    the lock now, so a driver whose own cycle is slower (the high-side switch: 10 ms) shows the lock within one
 *    poll. An adapter that reports "not locked" is counted: no lock reaches that driver, an integration error.
 *
 * Way out (SYS-35, user decision safe-state-rueckweg 2026-09-21): safety::SafeState::leaveSafeState() - `safety est`
 * or the return path of the safe supervisor - is the only place that clears the latch; a reset clears it as well.
 * Once the latch reads 0, cyclic() reports LEFT once and every driver unlocks on its next query - staying off until
 * its next explicit enable. While the latch is set, every enable is refused ("refused: safe state").
 *
 * 4. **The PWM inhibit of S1 gets an owner** (SYS-35). The table walk forces the PWM group inactive (FTM software
 *    output control) behind everybody's back, and before SYS-35 only the debug halt guard released that - and only
 *    if it had inhibited itself. Board finding 2026-09-21 (session d3): after `safety dst`, `safety est`, `gd clear`
 *    and `gd enable` the driver answered ok, state enabled, IOIN 1, but the phases floated until `lc reboot`. On
 *    every entry into S1 this hold hands the inhibit to the guard (DebugHaltGuard::inhibitForSafeState()), which
 *    releases it at the next explicit enable of an output - possible only after the latch was cleared. Entries are
 *    counted by the latch itself (nxSafeStateEntryCount()), so an S1 that is entered and left between two polls is
 *    not missed either.
 *
 * Hardware independent: the latch, the entry count, the table walk and the guard are passed in (host test
 * test/host/SafeStateHoldHostTest.cpp runs it with the real SafeState, the real SafeSupervisor, the real guard and
 * the real drivers). Not thread safe: all calls from the task context of the drivers. */

#include "DebugHaltGuard.h"

#include <cstdint>

/* SYS-139 (decision C2 (a), partner-and-degraded.md 5.4/5.7): the modules whose safe_off enable, bound to a PWM group
 * (board pwm[].gate_driver), this family port keeps on while the group is in an active short - in S1, in a degraded
 * group and after a debug halt - with the PWM inhibit and the group lock writing the selected output control instead
 * of freewheel (ShortHold.h, Tmc6200PowerStageOutput.h, FtmPwmInhibit.h, ComplementaryPwm.h, Integration.cpp).
 * A code fact read by tools/nxconfig/safestate.py: without it every active short is refused, and an enable of a
 * module not listed here bound to a group with a short is refused (its adapter would switch it off in S1). The
 * generic adapter of the generated binding (ModuleBinding.h, SafeOffOutput) does not keep an enable yet. */
#define NX_SAFE_STATE_SHORT_HOLD(X) X(gdTmc6200)

namespace integration
{

class SafeStateHold
{
public:
    using Latched    = bool (*)();
    using SwitchOff  = void (*)();
    using EntryCount = uint32_t (*)();

    enum class Event : uint8_t
    {
        NONE,    // S1 not latched
        ENTERED, // first poll that sees the latch
        HELD,    // S1 still latched
        LEFT,    // SYS-35: first poll after the latch was cleared (or S1 entered and left between two polls)
    };

    /**
     * \param outputs adapters of every safe_off output (\p outputCount entries)
     * \param latched query of the S1 latch
     * \param switchOff walk of the safe outputs table
     * \param entryCount entries into S1 since the reset (SYS-35, 0 after a reset, changes on every entry)
     * \param guard takes over the PWM inhibit of S1 until the next explicit enable (SYS-35)
     */
    SafeStateHold(
        IPowerStageOutput* const* outputs,
        uint8_t outputCount,
        Latched latched,
        SwitchOff switchOff,
        EntryCount entryCount,
        DebugHaltGuard& guard)
    : _outputs(outputs)
    , _latched(latched)
    , _switchOff(switchOff)
    , _entryCount(entryCount)
    , _guard(guard)
    , _entries(0U) // the count after a reset: an entry before the first poll is seen by the first poll
    , _outputCount(outputCount)
    {}

    Event cyclic()
    {
        // SYS-35: every entry, also one that was left again before this poll, hands the PWM inhibit of the table
        // walk to the guard - before the guard's own poll can see an enable.
        uint32_t const entries = _entryCount();
        bool const entered     = (entries != _entries);
        _entries               = entries;
        if (entered)
        {
            _guard.inhibitForSafeState();
        }
        if (!_latched())
        {
            bool const wasHeld = _held;
            _held              = false;
            return (wasHeld || entered) ? Event::LEFT : Event::NONE;
        }
        _switchOff(); // the table again first: the energy is off before any driver object is touched
        uint8_t unlocked = 0U;
        for (uint8_t i = 0U; i < _outputCount; ++i)
        {
            if (!_outputs[i]->holdForSafeState())
            {
                ++unlocked;
            }
        }
        _unlockedOutputs = unlocked;
        if (!_held)
        {
            _held = true;
            return Event::ENTERED;
        }
        return Event::HELD;
    }

    /**
     * SYS-35: the lock every safe_off driver gets (attach() of its adapter): S1 latched, or an entry into S1 this
     * hold has not handled yet. The second part matters when S1 is entered and left again before the next poll:
     * the drivers never saw the latch, and without it a driver whose state is still "on" would drive its output
     * again before the hold has switched it off and taken over the PWM inhibit - an output back on without an
     * explicit enable.
     */
    bool isLocking() const { return _latched() || (_entryCount() != _entries); }

    bool isHeld() const { return _held; }

    /** Adapters that reported "not locked" in the last poll while held (0: every safe_off output is locked). */
    uint8_t getUnlockedOutputs() const { return _unlockedOutputs; }

    uint8_t getOutputCount() const { return _outputCount; }

private:
    IPowerStageOutput* const* _outputs;
    Latched _latched;
    SwitchOff _switchOff;
    EntryCount _entryCount;
    DebugHaltGuard& _guard;
    uint32_t _entries; // entry count seen by the previous poll
    uint8_t _outputCount;
    uint8_t _unlockedOutputs = 0U;
    bool _held               = false;
};

} // namespace integration
