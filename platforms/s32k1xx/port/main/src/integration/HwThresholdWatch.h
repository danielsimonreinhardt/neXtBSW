#pragma once

/* SYS-128 (safety review SYS-86 H5): the fast check of the hardware threshold path on the S32K1 family - in the
 * 1 ms poll of the power stage (DebugHaltGuardSystem::POLL_MS, TASK_BSP), not once per safety cycle like the
 * register check SM-22 (nominal 10 ms, guaranteed 17 ms in the demo profile).
 *
 * Every poll: the registers of the path (comparator, DAC, round robin, route and lock, fault source select, fault
 * control of the timer) against the generated configuration and the consistency of the comparator output with the
 * timer's fault latch (hwThreshold/HwThresholdMonitor.h, Monitor::sample). A finding goes into the findings latch
 * of the family, next to the result of the start test that ComplementaryPwm::init() latched.
 *
 * Reaction while anything is latched: the channels of the group are forced inactive through the software output
 * control - the PWM inhibit of the debug halt guard (FtmPwmInhibit), written again in every poll, so the group stays
 * as the tripped hardware path would leave it. The poll runs after the guard, so a release of the guard's inhibit in
 * the same poll is overwritten at once. It is no decision of the safe supervisor: that is SYS-130 (the safety task
 * reports the latch, the supervisor decides - S1, which also keeps every power output off). Latched until reset.
 *
 * Only a project with a hardware threshold (NX_HW_THRESHOLD_COUNT, generated bsp/hwThreshold/
 * hwThresholdConfiguration.h) has this class; every other project's image is unchanged. */

#include "FtmPwmInhibit.h"

#include <bsp/hwThreshold/hwThresholdConfiguration.h>

#include <cstdint>

#if NX_HW_THRESHOLD_COUNT > 0
#include <bsp/io/outputPwm/ftmPwmConfiguration.h>
#include <hwThreshold/HwThresholdMonitor.h>

namespace integration
{
/** The fault control of the group's timer as generated (hwThresholdConfig, re-checked there by static_assert). */
constexpr ::bios::hwThreshold::FaultControl HW_THRESHOLD_FAULT_CONTROL{
    ::bios::hwThresholdConfig::FTM_MODE_FAULT,
    ::bios::hwThresholdConfig::FTM_COMBINE_FAULTEN,
    ::bios::hwThresholdConfig::FTM_FLTCTRL,
    ::bios::hwThresholdConfig::FTM_FLTPOL};

class HwThresholdWatch
{
public:
    HwThresholdWatch() : _monitor(), _inhibit(), _reported(0U) {}

    /**
     * One poll. \return the findings latched since the previous poll (for one log line each), 0 if nothing is new.
     */
    uint32_t cyclic()
    {
        uint32_t const found = _monitor.sample(
            ::bios::hwThresholdConfig::cmp(),
            ::bios::hwThresholdConfig::trgmux(),
            ::bios::hwThresholdConfig::sim(),
            ::bios::ftmPwm::ftm(),
            ::bios::hwThresholdConfig::CONFIG,
            HW_THRESHOLD_FAULT_CONTROL);
        if (found != 0U)
        {
            ::bios::hwThreshold::latchFindings(found);
        }
        uint32_t const latched = ::bios::hwThreshold::latchedFindings();
#if defined(NX_SAFE_OUTPUT_SELECT)
        // SYS-139 (safety review SYS-128 C2: the freewheel of the over-current path has priority): a finding or the
        // timer's fault latch removes the active short for the power cycle before the group is forced - the inhibit
        // below, every walk and the enable of the gate driver then give freewheel
        if ((latched != 0U) || ::bios::hwThreshold::faultDetected(::bios::ftmPwm::ftm()))
        {
            shortHold::removeForHardwarePath();
        }
#endif
        if (latched != 0U)
        {
            _inhibit.inhibit();
        }
        uint32_t const fresh = latched & ~_reported;
        _reported            = latched;
        return fresh;
    }

    /** Findings latched so far (start test and polls), 0 while the path is intact. */
    static uint32_t findings() { return ::bios::hwThreshold::latchedFindings(); }

private:
    ::bios::hwThreshold::Monitor _monitor;
    FtmPwmInhibit _inhibit;
    uint32_t _reported;
};

} // namespace integration
#endif
