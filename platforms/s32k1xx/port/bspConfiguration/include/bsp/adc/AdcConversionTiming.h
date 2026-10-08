/**
 * Conversion time and polling bound of the S32K1 SAR ADC, derived from the ADC registers of
 * Adc12BitConfiguration and from the generated clock model (IO-16).
 *
 * Nothing here is a magic number: the conversion time follows the formula of the reference
 * manual (also quoted in OpenBSW adc/Adc.h), every frequency comes from bsp/clock/boardClock.h
 * (NX_CLOCK_*, ARCH-06) and the converter settings and the safety factor come from the project
 * (modules.bspAdcAccess -> bsp/adc/adcAccessConfiguration.h, IO-19), so another crystal, another
 * system clock, another sample time or another safety factor changes the bound without a code
 * change (question 2 and 6).
 *
 * Belongs to the family, not to this board: as soon as the ADC contract moves to
 * platforms/s32k1xx/bsp (ARCH-03/05) this header moves with the converter. The arithmetic is
 * board- and MCU-neutral and lives in libs/bsp/bspAdcAccess (adc/AdcPollLimit.h).
 */

#pragma once

#include "bsp/adc/AnalogInput.h"

#include <adc/AdcPollLimit.h>
#include <bsp/clock/boardClock.h>

#include <cstdint>

namespace bios
{
namespace adcTiming
{

/** ADC clock = functional clock of the instance (PCC) divided by CFG1[ADIV] (1, 2, 4, 8). */
constexpr uint32_t clockHz(uint32_t const peripheralHz, uint32_t const cfg1)
{
    return peripheralHz >> ((cfg1 & ADC_CFG1_ADIV_MASK) >> ADC_CFG1_ADIV_SHIFT);
}

/**
 * Compare phase of CFG1[MODE]: 8 bit (00) 20 clocks, 10 bit (10) 24, 12 bit (01) 28. MODE 11 is
 * reserved on S32K1xx; it uses the longest defined phase, so the bound stays on the safe side.
 */
constexpr uint32_t compareClocks(uint32_t const cfg1)
{
    uint32_t const mode = (cfg1 & ADC_CFG1_MODE_MASK) >> ADC_CFG1_MODE_SHIFT;
    if (mode == 0U)
    {
        return 20U;
    }
    if (mode == 2U)
    {
        return 24U;
    }
    return 28U;
}

/** Hardware averaging of SC3: AVGE with AVGS 0..3 means 4, 8, 16 or 32 conversions per result. */
constexpr uint32_t averages(uint32_t const sc3)
{
    if ((sc3 & ADC_SC3_AVGE_MASK) == 0U)
    {
        return 1U;
    }
    return 4U << ((sc3 & ADC_SC3_AVGS_MASK) >> ADC_SC3_AVGS_SHIFT);
}

/**
 * Clocks of one conversion in the ADC clock domain: sample phase CFG2[SMPLTS] + 1, hold 1,
 * compare (resolution) and 5 clocks of the single/first-conversion adder, times the averaging
 * count. The remaining 5 bus clocks of the adder are in the bus clock domain, see pollLimit().
 */
constexpr uint32_t conversionClocks(uint32_t const cfg1, uint32_t const cfg2, uint32_t const sc3)
{
    return ::adc::sarConversionClocks(
        ((cfg2 & ADC_CFG2_SMPLTS_MASK) >> ADC_CFG2_SMPLTS_SHIFT) + 1U,
        1U,
        compareClocks(cfg1),
        5U,
        averages(sc3));
}

/**
 * Bound of the COCO polling loop of a synchronous conversion, in loop iterations of the core.
 * See adc/AdcPollLimit.h: the bound is at least safetyFactor times the conversion time even for
 * a loop of one iteration per core clock, which no real loop reaches (one iteration reads
 * SC1[0] over the peripheral bridge, several core clocks).
 */
constexpr uint32_t pollLimit(
    uint32_t const cfg1,
    uint32_t const cfg2,
    uint32_t const sc3,
    uint32_t const peripheralHz,
    uint32_t const busHz,
    uint32_t const coreHz,
    uint32_t const safetyFactor)
{
    return ::adc::pollLimit(
        ::adc::pollCyclesForClocks(conversionClocks(cfg1, cfg2, sc3), clockHz(peripheralHz, cfg1), coreHz)
            + ::adc::pollCyclesForClocks(5U * averages(sc3), busHz, coreHz),
        safetyFactor);
}

/**
 * Safety factor of the bound, from the project (modules.bspAdcAccess.poll_safety_factor, IO-19).
 * The eval board demo uses 16, chosen so that
 *   - a healthy conversion cannot time out: the guaranteed minimum is 16 conversion times, the
 *     realistic one 8 to 15 times that (iteration cost of the SC1[0] read), i.e. 130 to 240
 *     conversions. It covers calibration spread, a hardware averaging setting and the worst
 *     nesting case of adc::AdcAccess (an exclusive conversion plus its restart on the same
 *     instance) several times over;
 *   - a dead converter (stopped PCC clock, COCO never set) is detected inside a few hundred
 *     microseconds: with the eval board numbers the loop gives up after at least 27 us and, with
 *     a realistic iteration cost, after roughly 220 to 410 us. That is two orders of magnitude
 *     below the watchdog window and, unlike the 0xFFFFFF00 iterations of OpenBSW getValueSync
 *     (about 9 minutes at 80 MHz), it cannot suspend the interrupts past the next task tick.
 * A single bound serves both the task path and the exclusive path: with InterruptLockAdcLock
 * every conversion runs with the interrupts suspended anyway.
 */
constexpr uint32_t POLL_SAFETY_FACTOR = NX_ADC_POLL_SAFETY_FACTOR;

constexpr uint32_t CFG1 = static_cast<uint32_t>(Adc12BitConfiguration::CFG1);
constexpr uint32_t CFG2 = static_cast<uint32_t>(Adc12BitConfiguration::CFG2);
constexpr uint32_t SC3  = static_cast<uint32_t>(Adc12BitConfiguration::SC3);

/** ADC0 and ADC1 share the configuration; both run on FIRCDIV2, so one bound covers both. */
constexpr uint32_t ADC_CLOCK_HZ       = clockHz(NX_CLOCK_ADC0_HZ, CFG1);
constexpr uint32_t CONVERSION_CLOCKS  = conversionClocks(CFG1, CFG2, SC3);
constexpr uint32_t CONVERSION_NS      = ::adc::clocksToNs(CONVERSION_CLOCKS, ADC_CLOCK_HZ)
                                   + ::adc::clocksToNs(5U * averages(SC3), NX_CLOCK_BUS_HZ);
constexpr uint32_t POLL_LIMIT
    = pollLimit(CFG1, CFG2, SC3, NX_CLOCK_ADC0_HZ, NX_CLOCK_BUS_HZ, NX_CLOCK_CORE_HZ, POLL_SAFETY_FACTOR);

// PLAT-12: only a derivative that has a second converter can compare it (mcu/derivativeFacts.h)
#if NX_ADC_INSTANCE_COUNT > 1
static_assert(
    clockHz(NX_CLOCK_ADC1_HZ, CFG1) == ADC_CLOCK_HZ,
    "ADC0 and ADC1 run at different clocks: derive one bound per instance");
#endif
// The same arithmetic runs in the configuration (tools/nxconfig/adc.py) to check the conversion time against the
// interrupt suspension the project accepts, and its result is generated into
// bsp/adc/adcAccessConfiguration.h. Comparing both here is the guard against a silent drift between the
// generator and the code on the target: a changed formula, a changed register encoding or a changed clock model
// breaks the build instead of the polling bound (IO-19).
static_assert(ADC_CLOCK_HZ == NX_ADC_CLOCK_HZ, "generated and derived ADC clock differ");
static_assert(
    CONVERSION_CLOCKS == NX_ADC_CONVERSION_CLOCKS, "generated and derived conversion clocks differ");
static_assert(CONVERSION_NS == NX_ADC_CONVERSION_NS, "generated and derived conversion time differ");
static_assert(POLL_LIMIT == NX_ADC_POLL_LIMIT, "generated and derived poll limit differ");
// Plausibility of the derivation, not of this board: a SAR conversion of an automotive MCU is
// neither faster than 100 ns nor slower than 1 ms, and a bound below 64 iterations would be
// within the noise of the loop itself.
static_assert(
    (CONVERSION_NS >= 100U) && (CONVERSION_NS <= 1000000U),
    "derived ADC conversion time implausible: check NX_CLOCK_ADC0_HZ and Adc12BitConfiguration");
static_assert(POLL_LIMIT >= 64U, "derived ADC poll limit too small");

} // namespace adcTiming
} // namespace bios
