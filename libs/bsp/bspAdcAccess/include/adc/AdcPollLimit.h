/**
 * Bound for the polling loop of a synchronous (polled) conversion, derived from the conversion
 * time instead of an arbitrary large number (IO-16).
 *
 * A polled conversion inside an exclusive section must give up after a bounded number of loop
 * iterations, otherwise a stopped converter clock or a converter that never sets its
 * completion flag suspends the interrupts until the watchdog fires. The bound must be
 *   - large enough that a healthy conversion never hits it (no spurious timeout) and
 *   - small enough that the failure case is detected well inside the watchdog window.
 *
 * Only the loop count is portable, not a time: the loop has no clock of its own. The bound is
 * therefore expressed in iterations of the polling core and computed from the conversion time as
 * seen by that core:
 *
 *   clocks    = sarConversionClocks(...)                        converter clock domain
 *   cycles    = pollCyclesForClocks(clocks, convHz, pollHz)     core clock domain, rounded up
 *             + pollCyclesForClocks(busClocks, busHz, pollHz)   further domains, if any
 *   limit     = pollLimit(cycles, safetyFactor)
 *
 * `cycles` assumes the fastest conceivable loop of one iteration per core clock, which no
 * implementation reaches: one iteration reads a peripheral register and needs several core
 * clocks. `limit` is therefore a guaranteed lower bound of safetyFactor times the conversion
 * time and in practice a multiple of that. Choose safetyFactor so that the guaranteed minimum
 * covers every variation of the conversion time (calibration, hardware averaging, a conversion
 * of another caller that was started just before) with room to spare.
 *
 * MCU- and RTOS-neutral: the register bits of a family (divider, resolution, sample time,
 * averaging) are turned into the clock counts by the family, this header only does the
 * arithmetic. Everything is constexpr, nothing is computed at run time.
 */

#pragma once

#include <cstdint>

namespace adc
{

/**
 * Clocks of one complete conversion of a SAR converter, in its own clock domain.
 * The parts are the ones the S32K1, S32K3 and STM32 manuals use: sample (acquisition), hold,
 * compare (one clock per bit plus overhead) and a fixed adder of the first conversion. Parts in
 * another clock domain (S32K1: 5 bus clocks of the adder) are added separately, see
 * pollCyclesForClocks.
 *
 * \param averages hardware averaging: number of conversions per result (0 and 1 both mean one)
 */
constexpr uint32_t sarConversionClocks(
    uint32_t const sampleClocks,
    uint32_t const holdClocks,
    uint32_t const compareClocks,
    uint32_t const adderClocks,
    uint32_t const averages)
{
    return (sampleClocks + holdClocks + compareClocks + adderClocks)
           * ((averages == 0U) ? 1U : averages);
}

/**
 * Number of polling iterations that certainly last at least as long as `clocks` clocks of a
 * domain running at clockHz, if every iteration took a single clock of the polling core (pollHz).
 * Rounded up, saturated, at least 1. A clockHz of 0 (unknown converter clock) yields the largest
 * value: an unknown clock must not cause a spurious timeout.
 */
constexpr uint32_t
pollCyclesForClocks(uint32_t const clocks, uint32_t const clockHz, uint32_t const pollHz)
{
    if (clockHz == 0U)
    {
        return UINT32_MAX;
    }
    uint64_t const ticks = ((static_cast<uint64_t>(clocks) * static_cast<uint64_t>(pollHz))
                            + static_cast<uint64_t>(clockHz) - 1U)
                           / static_cast<uint64_t>(clockHz);
    if (ticks == 0U)
    {
        return 1U;
    }
    if (ticks >= UINT32_MAX)
    {
        return UINT32_MAX;
    }
    return static_cast<uint32_t>(ticks);
}

/** pollCycles times safetyFactor, saturated, at least 1. */
constexpr uint32_t pollLimit(uint32_t const pollCycles, uint32_t const safetyFactor)
{
    uint32_t const factor = (safetyFactor == 0U) ? 1U : safetyFactor;
    uint64_t const limit  = static_cast<uint64_t>(pollCycles) * static_cast<uint64_t>(factor);
    if (limit == 0U)
    {
        return 1U;
    }
    if (limit >= UINT32_MAX)
    {
        return UINT32_MAX;
    }
    return static_cast<uint32_t>(limit);
}

/** Duration of `clocks` clocks of a domain running at clockHz, in ns (rounded up, for logs). */
constexpr uint32_t clocksToNs(uint32_t const clocks, uint32_t const clockHz)
{
    if (clockHz == 0U)
    {
        return UINT32_MAX;
    }
    uint64_t const ns = ((static_cast<uint64_t>(clocks) * 1000000000ULL)
                         + static_cast<uint64_t>(clockHz) - 1U)
                        / static_cast<uint64_t>(clockHz);
    if (ns >= UINT32_MAX)
    {
        return UINT32_MAX;
    }
    return static_cast<uint32_t>(ns);
}

} // namespace adc
