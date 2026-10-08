#pragma once

/* ARCH-66: what the safe_off connection `in` means for this driver (module.yaml connect.in.safe_off) - the access
 * struct SafeOff_in of the binding trait (HighSideSwitchBinding.h) for the generic adapter of the family integration
 * (integration::SafeOffOutput, the power stage output of the debug halt guard and the hold of the safe state S1).
 *
 *   off        HighSideSwitchChannel::requestOff(): IN and DEN low at once, the state machine stays in OFF, so the
 *              next supervision cycle does not rewrite IN high (IO-37)
 *   enables    Counters::switchOns of the state machine: an explicit, accepted "on" - neither a refused "on" (a
 *              latched fault needs a reset first) nor the driver's own supervision counts
 *   setLock    HighSideSwitchChannel::setSwitchOnLock(): the lock of the safe state the channel asks in every cycle
 *              and before every "on" (SYS-36)
 *   updateLock HighSideSwitchChannel::updateSwitchOnLock(): evaluate the lock now, IN low at once while it is set
 *
 * Exactly the driver calls of the hand-written adapter HighSideSwitchPowerStageOutput.h it replaces. The owner is the
 * lifecycle system of an instance on the target (HighSideSwitchBinding.h) or the bare channel in the host tests of
 * the family integration; channelOf() picks the channel of either. Header only, no OpenBSW dependency. */

#include "highSideSwitch/HighSideSwitchChannel.h"

#include <cstdint>

namespace highside
{
inline HighSideSwitchChannel& channelOf(HighSideSwitchChannel& channel) { return channel; }

inline HighSideSwitchChannel const& channelOf(HighSideSwitchChannel const& channel) { return channel; }

// the channel of a temporary would dangle: only a channel that outlives the call has one
HighSideSwitchChannel const& channelOf(HighSideSwitchChannel const&& channel) = delete;

template<class Owner>
struct HighSideSwitchSafeOff
{
    using System = Owner;

    static void off(Owner& owner) { (void)channelOf(owner).requestOff(); }

    static uint32_t enables(Owner const& owner) { return channelOf(owner).getSwitch().getCounters().switchOns; }

    static void setLock(Owner& owner, HighSideSwitchChannel::SwitchOnLock const lock)
    {
        channelOf(owner).setSwitchOnLock(lock);
    }

    static bool updateLock(Owner& owner) { return channelOf(owner).updateSwitchOnLock(); }
};

} // namespace highside
