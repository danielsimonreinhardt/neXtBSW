#pragma once

/* IO-45: the phase duty source of a gate driver - what the PWM group bound to it (board pwm[].gate_driver) is set to,
 * in the one unit of the driver's contract, per mille of the period (gd::PhaseDuty, gd::Tmc6200::DUTY_FULL).
 *
 * Finding IO-44 (2026-09-23): the enable rule sat in the driver and was host tested, but on the target nobody attached
 * a source. Finding of the coordinator (IO-45): the PWM side speaks 0.01 % (RTE target pwm.<pair>, OutputPwm, the
 * console command `pwm set`: 0..10000), the driver per mille (0..1000) - a factor of ten for the same quantity; a
 * source that passed the console value on would make the driver see 5000 where it expects at most 1000 and take every
 * phase for full. Decision: the driver's unit stays per mille, and the source does not take a duty value of any writer
 * at all. It reads what the timer is set to - the on time of each high side in counter ticks
 * (ftmPwm::FtmPwmGroup::highSideOnTicks(), including the output mask and the software output control of the safe
 * state and the debug halt guard) - and converts that once, here, into per mille:
 * - 0 exactly when the high side is never on (OFF), DUTY_FULL exactly when it is on for the whole period (FULL);
 * - anything between is SWITCHING and becomes 1 .. DUTY_FULL - 1 (rounded down, kept off both ends), so the state the
 *   enable rule compares (gd::Tmc6200::phaseState()) is the state of the timer, whatever the value was rounded to.
 * A duty of 0.01 % that the counter cannot represent (on time 0 ticks) is therefore OFF - which it is at the pin - and
 * not "switching" as a conversion of the requested 1 would say.
 *
 * Where it is read: from Tmc6200::enable(), i.e. in the task context of the driver (TASK_BSP) - IO-44's condition.
 * The source only reads timer registers (no write, no lock, no RTE, no allocation), which that task may do: the
 * debug halt guard writes the same timer from the same task.
 *
 * Family neutral: the group is a template parameter with highSideOnTicks(pair, ticks); the s32k1xx integration passes
 * ftmPwm::FtmPwmGroup (Integration.cpp). Host test: test/host/GateDriverDutySourceHostTest.cpp. */

#include "gd/Tmc6200.h"

#include <cstdint>

namespace integration
{

/**
 * Per mille of a period of `periodTicks` for a high side that is on for `onTicks` of them: 0 only for no on time,
 * gd::Tmc6200::DUTY_FULL only for the whole period, otherwise 1 .. DUTY_FULL - 1 (the phase state is kept exactly).
 * A period of 0 ticks gives 0 (the caller checks it before, see readPhaseDuty()).
 */
constexpr uint16_t perMilleOfPeriod(uint32_t const onTicks, uint32_t const periodTicks)
{
    if ((onTicks == 0U) || (periodTicks == 0U))
    {
        return 0U;
    }
    if (onTicks >= periodTicks)
    {
        return ::gd::Tmc6200::DUTY_FULL;
    }
    // rounded down: onTicks < periodTicks keeps the value below DUTY_FULL, so only the lower end needs a stop
    uint64_t const value = (static_cast<uint64_t>(onTicks) * ::gd::Tmc6200::DUTY_FULL) / periodTicks;
    if (value < 1U)
    {
        return 1U;
    }
    return static_cast<uint16_t>(value);
}

/**
 * The three phases of a gate driver from the PWM group bound to it: pairs 0, 1, 2 of the group are u, v, w. Which pair
 * feeds which phase is a wiring fact the rule does not need - it is the same for every order (Tmc6200::isCommonState).
 * \return false (the driver refuses then) if the group does not have exactly three pairs, has no period, or a pair's
 *         state cannot be told from the registers
 */
template<typename Group>
bool readPhaseDuty(Group const& group, uint8_t const pairCount, uint32_t const periodTicks, ::gd::PhaseDuty& duty)
{
    if ((pairCount != 3U) || (periodTicks == 0U))
    {
        return false;
    }
    uint32_t ticks[3] = {0U, 0U, 0U};
    for (uint8_t i = 0U; i < 3U; ++i)
    {
        if (!group.highSideOnTicks(i, ticks[i]))
        {
            return false;
        }
    }
    duty.u = perMilleOfPeriod(ticks[0], periodTicks);
    duty.v = perMilleOfPeriod(ticks[1], periodTicks);
    duty.w = perMilleOfPeriod(ticks[2], periodTicks);
    return true;
}

} // namespace integration
