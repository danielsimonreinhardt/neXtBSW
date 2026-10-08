/* SYS-125 (anomaly AN-066, doc/safety/hsi-s32k1xx.md H9, concept.md SM-59): low-voltage detect reset of the PMC.
 *
 * Every S32K1xx derivative has three supply thresholds on VDD (S32K1xx data sheet Rev. 13, table 8): the power-on
 * reset VPOR, the low-voltage reset VLVR (2.50 ... 2.70 V falling, always on) and the low-voltage detect VLVD
 * (2.80 ... 3.00 V falling, 50 mV hysteresis). Rising from power-on the LVD circuit holds the MCU in reset until VDD
 * is above VLVD; falling, it resets only if PMC_LVDSC1[LVDRE] = 1 ([RM13] 25.2.2.2, 41.6.1). LVDRE is 0 after
 * power-on and unaffected by every other reset ([RM13] 41.7.1.2), so without this write the MCU keeps running
 * between VLVD and VLVR - below the specified supply range, where the data sheet degrades ADC, comparator, pins and
 * communication (data sheet 4.2). With LVDRE = 1 a falling VDD below VLVD resets the MCU (RCM_SRS[LVD]) and holds it
 * in reset until VDD is above VLVD plus the hysteresis again.
 *
 * boardInit() writes the register once per start, before the RAM initialization (supervisor mode, no global data).
 * Writing it in every start makes the configuration independent of what survived the reset: a power-on clears it,
 * and whether an LVD reset (a "chip POR" in [RM13] 26.1) clears it as well does not matter.
 *
 * The whole register is written, not read-modify-written: LVDRE on; LVDIE off (the reset is the reaction - the shared
 * vector LVD_LVW / PMC_SCG_CMU stays on DefaultISR, hsi-s32k1xx.md section 5); LVDACK not written (0, it only
 * acknowledges the flag, which a reset clears anyway). The LVD is off in the low-power modes VLPR/VLPS of the
 * regulator ([RM13] 41.6); only the LVR acts there.
 *
 * Family constants: the field layout is the same on S32K116 ... S32K148 (device headers PMC_LVDSC1_*). The S32K14xW
 * derivatives specify no VLVD (data sheet table 9); they are not in the derivative database, and adding one needs a
 * decision whether this write stays (SYS-125, open point). Header only, no device header needed, so the host test
 * (platforms/s32k1xx/port/main/test/host/LowVoltageDetectResetHostTest.cpp) runs it on a register model.
 */
#pragma once

#include <cstdint>

namespace platform
{
namespace pmc
{
// PMC_LVDSC1 fields ([RM13] 41.7.1.2)
constexpr uint8_t LVDSC1_LVDF   = 0x80U; // read-only: VDD below VLVD (level sensitive, cleared by a reset)
constexpr uint8_t LVDSC1_LVDACK = 0x40U; // write-only: acknowledge LVDF, reads 0
constexpr uint8_t LVDSC1_LVDIE  = 0x20U; // interrupt on LVDF
constexpr uint8_t LVDSC1_LVDRE  = 0x10U; // reset on LVDF; 0 after power-on, unaffected by other resets

/** The value boardInit() writes: reset on, interrupt off, no acknowledge. */
constexpr uint8_t LVDSC1_START_VALUE = LVDSC1_LVDRE;

/** True if a read of LVDSC1 shows the configuration of the family port: reset on, interrupt off (LVDF ignored). */
constexpr bool lowVoltageDetectResetOn(uint8_t const lvdsc1)
{
    // unsigned operands: uint8_t alone would be promoted to int for the bit operations
    return (static_cast<uint32_t>(lvdsc1)
            & (static_cast<uint32_t>(LVDSC1_LVDRE) | static_cast<uint32_t>(LVDSC1_LVDIE)))
           == static_cast<uint32_t>(LVDSC1_LVDRE);
}

/**
 * Switches the low-voltage detect reset on. `lvdsc1` is PMC_LVDSC1 (IP_PMC->LVDSC1 on the target, a register model in
 * the host test). Idempotent: every start writes the same value.
 */
template<typename Register>
inline void enableLowVoltageDetectReset(Register& lvdsc1)
{
    lvdsc1 = LVDSC1_START_VALUE;
}
} // namespace pmc
} // namespace platform

// With the device header included before this one, the family constants are checked against it.
#if defined(PMC_LVDSC1_LVDRE_MASK)
static_assert(::platform::pmc::LVDSC1_LVDRE == PMC_LVDSC1_LVDRE_MASK, "PMC_LVDSC1[LVDRE] of the device header");
static_assert(::platform::pmc::LVDSC1_LVDIE == PMC_LVDSC1_LVDIE_MASK, "PMC_LVDSC1[LVDIE] of the device header");
static_assert(::platform::pmc::LVDSC1_LVDACK == PMC_LVDSC1_LVDACK_MASK, "PMC_LVDSC1[LVDACK] of the device header");
static_assert(::platform::pmc::LVDSC1_LVDF == PMC_LVDSC1_LVDF_MASK, "PMC_LVDSC1[LVDF] of the device header");
#endif
