#pragma once

/* IO-32: the PWM inhibit of the debug halt guard on the S32K1 family - software output control of the FTM of the
 * project's PWM group (SWOCTRL, CHnOC 1 and CHnOCV 0 for all channels of the group, [RM] 47.5.15 Table 47-13; see
 * DebugHaltGuardSystem.h). Its own header since SYS-123, so that the host test of the group lock
 * (tools/bswgen/test/pwm_host/complementary_pwm_lock_host_test.cpp) runs the real class on a register model. */

#include "DebugHaltGuard.h"

#include <bsp/io/outputPwm/ftmPwmConfiguration.h>

#include <cstdint>

#if defined(NX_SAFE_OUTPUT_SELECT)
#include "ShortHold.h" // SYS-139: a project with an active short
#endif

namespace integration
{

#if NX_PWM_GROUP_COUNT > 0
/** All channels of the project's PWM group forced inactive through the FTM software output control. */
class FtmPwmInhibit final : public IPwmInhibit
{
public:
    static_assert(
        (::bios::ftmPwm::CHANNEL_MASK & ~0xFFU) == 0U, "SWOCTRL: CHnOC are bits 0..7, CHnOCV bits 8..15 stay 0");

#if defined(NX_SAFE_OUTPUT_SELECT)
    /* SYS-139 (decision C2 (a), ShortHold.h rule 2): the group is forced to what the walks write for it - its active
     * short while one is selected and not removed, freewheel otherwise. Never freewheel over a selected short: that
     * would undo the short of S1 and of the halt deadman in the first poll. */
    static uint32_t forcedValue()
    {
        return shortHold::outputControl(&::bios::ftmPwm::ftm().SWOCTRL, ::bios::ftmPwm::CHANNEL_MASK);
    }

    void inhibit() override { ::bios::ftmPwm::ftm().SWOCTRL = forcedValue(); }
#else
    void inhibit() override { ::bios::ftmPwm::ftm().SWOCTRL = ::bios::ftmPwm::CHANNEL_MASK; }
#endif

    // SWOCTRL belongs to this class alone (ComplementaryPwm writes 0 once in init()).
#if defined(NX_PWM_GROUP_LOCK) && defined(NX_SAFE_OUTPUT_SELECT)
    // SYS-123/SYS-139: a degraded group is not released and stays in its selected safe state
    void release() override { ::bios::ftmPwm::ftm().SWOCTRL = ::bios::ftmPwm::groupLocked() ? forcedValue() : 0U; }
#elif defined(NX_PWM_GROUP_LOCK)
    // SYS-123: a degraded group is not released - the explicit enable of another output (a high-side switch) must not
    // let the channels of a degraded group run until the next safety cycle forces them again (fmea.md SM-23 (3))
    void release() override
    {
        ::bios::ftmPwm::ftm().SWOCTRL = ::bios::ftmPwm::groupLocked() ? ::bios::ftmPwm::CHANNEL_MASK : 0U;
    }
#else
    void release() override { ::bios::ftmPwm::ftm().SWOCTRL = 0U; }
#endif
};
#endif

} // namespace integration
