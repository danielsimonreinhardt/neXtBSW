#pragma once

#include "gd/ITmc6200Access.h"
#include "gd/tmc6200Registers.h"

#include <cstdint>

namespace gd
{

/** Configuration value "leave the register field as the device has it" (no write). */
constexpr int16_t KEEP = -1;

/**
 * Register settings of one TMC6200, one field per register field (datasheet terms, doc/gdTmc6200.md).
 *
 * Every field is KEEP or a value for the register field. KEEP fields are never written, so a configuration of
 * only KEEP fields makes the startup read-only. Where a value comes from:
 * - wiring of the board: interfaceMode (PWM group with individual high/low side signals or polarity/enable)
 * - board and project: amplification, amplifierOff (sense resistor, ADC range, current range; KEEP where the
 *   CURx outputs are not used)
 * - project (function decisions): faultDirect, overtemperatureSelect, shortRetries, protectParallel, and the short
 *   detection shortToSupplyLevel, shortToGroundLevel, shortFilter, shortDelay (IO-35, in mV and ns, converted with
 *   shortLevelForMv, shortFilterForNs, shortDelayForNs)
 * - power stage dependent, not configurable yet: breakBeforeMakeClocks, driveStrength (stay KEEP, reset value)
 * The protection disable bits (SHORT_CONF disable_S2G/disable_S2VS) and GCONF test_mode are not configurable.
 */
struct Tmc6200Config
{
    uint8_t expectedVersion;      // IOIN VERSION (TMC6200_VERSION, p.24)
    int16_t interfaceMode;        // GCONF singleline: 0 individual L+H, 1 H polarity / L enable (reset 1)
    int16_t faultDirect;          // GCONF faultdirect 0/1 (reset 0)
    int16_t amplification;        // GCONF amplification 0 = x5, 1 = x10, 3 = x20 (reset 0; 2 is not accepted)
    int16_t amplifierOff;         // GCONF amplifier_off 0/1 (reset 0)
    int16_t shortToSupplyLevel;   // SHORT_CONF S2VS_LEVEL 1..15 (reset OTP 6/12)
    int16_t shortToGroundLevel;   // SHORT_CONF S2G_LEVEL 2..15 (reset OTP 6/12)
    int16_t shortFilter;          // SHORT_CONF SHORTFILTER 0..3 (reset 1)
    int16_t shortDelay;           // SHORT_CONF shortdelay 0/1 (reset 0)
    int16_t shortRetries;         // SHORT_CONF RETRY 0..3 (reset 3)
    int16_t protectParallel;      // SHORT_CONF protect_parallel 0/1 (reset 1)
    int16_t breakBeforeMakeClocks; // DRV_CONF BBMCLKS 0..15 (reset OTP 1..4)
    int16_t overtemperatureSelect; // DRV_CONF OTSELECT 0..3 = 150/143/136/120 C (reset 0)
    int16_t driveStrength;        // DRV_CONF DRVSTRENGTH 0..3 (reset 2)
};

/** Configuration that writes nothing: every register field stays as the device has it. */
constexpr Tmc6200Config keepAllConfig()
{
    return {TMC6200_VERSION, KEEP, KEEP, KEEP, KEEP, KEEP, KEEP, KEEP, KEEP, KEEP, KEEP, KEEP, KEEP, KEEP};
}

namespace detail
{
constexpr bool keepOrRange(int16_t const value, int16_t const min, int16_t const max)
{
    return (value == KEEP) || ((value >= min) && (value <= max));
}
} // namespace detail

/** Ranges of every field (datasheet p.22-25); used by the integration as static_assert. */
constexpr bool isValidTmc6200Config(Tmc6200Config const& c)
{
    return detail::keepOrRange(c.interfaceMode, 0, 1) && detail::keepOrRange(c.faultDirect, 0, 1)
           && detail::keepOrRange(c.amplification, 0, 3) && (c.amplification != 2)
           && detail::keepOrRange(c.amplifierOff, 0, 1)
           && detail::keepOrRange(c.shortToSupplyLevel, TMC6200_SHORT_LEVEL_S2VS_MIN, TMC6200_SHORT_LEVEL_MAX)
           && detail::keepOrRange(c.shortToGroundLevel, TMC6200_SHORT_LEVEL_S2G_MIN, TMC6200_SHORT_LEVEL_MAX)
           && detail::keepOrRange(c.shortFilter, 0, 3) && detail::keepOrRange(c.shortDelay, 0, 1)
           && detail::keepOrRange(c.shortRetries, 0, 3) && detail::keepOrRange(c.protectParallel, 0, 1)
           && detail::keepOrRange(c.breakBeforeMakeClocks, 0, TMC6200_BBMCLKS_MAX)
           && detail::keepOrRange(c.overtemperatureSelect, 0, 3) && detail::keepOrRange(c.driveStrength, 0, 3);
}

/* ---------------------------------------------------------------------------- physical units -> field values
 * For the generator and the integration (project parameters in units of the function). KEEP if the value is
 * not representable. */

/** OTSELECT for a shutdown temperature of 150, 143, 136 or 120 degrees Celsius (p.25), otherwise KEEP. */
constexpr int16_t overtemperatureSelectForCelsius(int32_t const celsius)
{
    return (celsius == 150) ? 0 : (celsius == 143) ? 1 : (celsius == 136) ? 2 : (celsius == 120) ? 3 : KEEP;
}

/** Shutdown temperature of an OTSELECT value (p.25). */
constexpr uint16_t overtemperatureCelsius(uint32_t const otSelect)
{
    uint32_t const s = otSelect & 3U;
    return (s == 0U) ? 150U : (s == 1U) ? 143U : (s == 2U) ? 136U : 120U;
}

/** Smallest BBMCLKS whose typical time (42 ns per clock, p.14/p.25) covers \p ns; KEEP above 15 clocks. */
constexpr int16_t breakBeforeMakeClocksForNs(uint32_t const ns)
{
    uint32_t const clocks = (ns + TMC6200_BBM_NS_PER_CLOCK - 1U) / TMC6200_BBM_NS_PER_CLOCK;
    return (clocks <= TMC6200_BBMCLKS_MAX) ? static_cast<int16_t>(clocks) : KEEP;
}

/** Weakest DRVSTRENGTH (0, 2 or 3) whose typical turn-on gate current (p.36) reaches \p ma; KEEP above. */
constexpr int16_t driveStrengthForGateCurrentMa(uint32_t const ma)
{
    return (ma <= TMC6200_GATE_CURRENT_WEAK_MA)     ? 0
           : (ma <= TMC6200_GATE_CURRENT_MEDIUM_MA) ? 2
           : (ma <= TMC6200_GATE_CURRENT_STRONG_MA) ? 3
                                                    : KEEP;
}

/**
 * Typical detector voltage of a short level in mV (p.38 gives level 6 = 625 mV and level 15 = 1560 mV; the
 * straight line through both points is an interpolation, verify).
 */
constexpr int32_t shortLevelMv(int32_t const level)
{
    int32_t const span = static_cast<int32_t>(TMC6200_SHORT_LEVEL_15_MV - TMC6200_SHORT_LEVEL_6_MV);
    int32_t const num  = (level - 6) * span;
    return static_cast<int32_t>(TMC6200_SHORT_LEVEL_6_MV) + ((num >= 0) ? (num + 4) / 9 : (num - 4) / 9);
}

/**
 * Highest level in \p minLevel..15 whose typical detector voltage does not exceed \p mv, so the protection
 * trips at or below the requested drain-source voltage. KEEP if even \p minLevel is above \p mv.
 * The voltage is a project decision (module.yaml short_to_ground_mv / short_to_supply_mv, IO-35); nxconfig shows
 * the short current it corresponds to with R_DS(on) of the board.
 */
constexpr int16_t shortLevelForMv(int32_t const mv, int32_t const minLevel)
{
    int16_t result = KEEP;
    for (int32_t level = minLevel; level <= static_cast<int32_t>(TMC6200_SHORT_LEVEL_MAX); ++level)
    {
        if (shortLevelMv(level) <= mv)
        {
            result = static_cast<int16_t>(level);
        }
    }
    return result;
}

/** SHORTFILTER for a spike filter time of 100, 1000, 2000 or 3000 ns (p.24, p.32), otherwise KEEP. */
constexpr int16_t shortFilterForNs(uint32_t const ns)
{
    return (ns == 100U) ? 0 : (ns == 1000U) ? 1 : (ns == 2000U) ? 2 : (ns == 3000U) ? 3 : KEEP;
}

/** Spike filter time in ns of a SHORTFILTER value (p.24). */
constexpr uint16_t shortFilterNs(uint32_t const filter)
{
    uint32_t const f = filter & 3U;
    return static_cast<uint16_t>((f == 0U) ? 100U : (f * 1000U));
}

/** shortdelay for a short detection delay of 750 or 1500 ns (p.24, p.32), otherwise KEEP. */
constexpr int16_t shortDelayForNs(uint32_t const ns) { return (ns == 750U) ? 0 : (ns == 1500U) ? 1 : KEEP; }

/** Short detection delay in ns of a shortdelay value (p.24). */
constexpr uint16_t shortDelayNs(uint32_t const delay)
{
    return static_cast<uint16_t>(((delay & 1U) != 0U) ? 1500U : 750U);
}

/** Field value that fails isValidTmc6200Config (a requested value the device cannot realize). */
constexpr int16_t INVALID_FIELD = -2;

/**
 * Field for a project value in physical units: KEEP if the project did not set it (\p requested <= 0), the
 * converted \p field otherwise, or INVALID_FIELD if the conversion found no setting (field KEEP). A requested value
 * is never silently replaced by the device reset value.
 */
constexpr int16_t fieldForRequest(int32_t const requested, int16_t const field)
{
    return (requested <= 0) ? KEEP : ((field == KEEP) ? INVALID_FIELD : field);
}

/* ---------------------------------------------------------------------------- diagnosis */

/**
 * Fault and warning bits of a gate driver in terms of the function (RTE signal gd.<device>.faults, proposed).
 * Built from GSTAT, IOIN, the FAULT pin and the communication state.
 */
namespace fault
{
constexpr uint16_t COMMUNICATION            = 0x0001U; // SPI error, wrong device, read-back mismatch
constexpr uint16_t DEVICE_RESET             = 0x0002U; // registers lost while running (supply dropout)
constexpr uint16_t CHARGE_PUMP_UNDERVOLTAGE = 0x0004U; // GSTAT uv_cp
constexpr uint16_t OVERTEMPERATURE_WARNING  = 0x0008U; // GSTAT drv_otpw (no action by the device)
constexpr uint16_t OVERTEMPERATURE_SHUTDOWN = 0x0010U; // GSTAT drv_ot
constexpr uint16_t SHORT_TO_GROUND_U        = 0x0020U; // GSTAT s2gu (high side)
constexpr uint16_t SHORT_TO_GROUND_V        = 0x0040U;
constexpr uint16_t SHORT_TO_GROUND_W        = 0x0080U;
constexpr uint16_t SHORT_TO_SUPPLY_U        = 0x0100U; // GSTAT s2vsu (low side)
constexpr uint16_t SHORT_TO_SUPPLY_V        = 0x0200U;
constexpr uint16_t SHORT_TO_SUPPLY_W        = 0x0400U;
constexpr uint16_t SHORT_DETECTED           = 0x0800U; // GSTAT shortdet_u/v/w: a retry happened
constexpr uint16_t FAULT_PIN                = 0x1000U; // FAULT output high
constexpr uint16_t ENABLE_MISMATCH          = 0x2000U; // IOIN DRV_EN differs from the commanded level
constexpr uint16_t TEST_MODE                = 0x4000U; // GCONF test_mode set (DRV_EN is an analog output)
/**
 * IO-67: the device answers IOIN with an all-zero datagram - the signature of a device without supply VS (SDO is a
 * tristate output with pull-down, datasheet p.8 pin 16; below the VSA/5VOUT undervoltage threshold the device is
 * held in its power-on reset, p.34), or of an SDO line held low. A supplied TMC6200 never reads IOIN 0 (VERSION 0x10).
 *
 * Latched only when the device goes missing out of a running state (DISABLED or ENABLED: supply lost, an event that
 * must be acknowledged). While the driver has not brought the device up yet (UNINITIALIZED, or FAILED since the
 * start or since the last clearFaults()) it is a current fault only: a supply switched on later lets the next startup
 * attempt succeed without clearFaults() - DRV_EN still stays off until an explicit enable(). Board 2026-09-30: start
 * without the 24 V supply latched COMMUNICATION, which only `gd clear` removed after the 24 V were there.
 */
constexpr uint16_t NO_SUPPLY                = 0x8000U;

/**
 * Faults after which the device keeps the bridge off (or must not be enabled).
 *
 * IO-44: SHORT_DETECTED belongs here. The bit is not a hint but a report of an action already taken - p.23
 * "short counter has triggered at least once" and p.32 7.2: the device switches the affected bridge off on a
 * detected short and switches it on again, up to SHORT_CONF.RETRY times, before it stays off (only then s2g/s2vs
 * are set). Until the retries are used up the device reapplies the fault current on purpose, and whether it ever
 * escalates is not guaranteed: of the 15 board occurrences of "short detected" on 2026-09-23 (captures
 * 2026-09-23_board/38-41, 45-47) a shutdown fault followed in only 5 (11 ms to 1551 ms later); the other 10 ran on
 * with DRV_EN high until the operator switched off. A reported short therefore switches off here, in the software.
 *
 * Not in this list, each for a reason of the datasheet:
 * - OVERTEMPERATURE_WARNING (drv_otpw, p.22 "No action is taken", p.32 7.1 meant for power reduction): the device
 *   itself keeps driving, and taking the bridge away on a prewarning would be a reaction to a state that is
 *   designed to be tolerated.
 * - FAULT_PIN: the pin is the OR of the GSTAT bits ("ORed to STATUS output", p.22-23) and with faultdirect = 1
 *   pulses on every single protective action (p.22). It carries no information the bits above do not carry, so it
 *   is reported, not acted on - the reaction stays tied to the bit that names the cause.
 */
constexpr uint16_t SHUTDOWN = COMMUNICATION | DEVICE_RESET | CHARGE_PUMP_UNDERVOLTAGE | OVERTEMPERATURE_SHUTDOWN
                              | SHORT_TO_GROUND_U | SHORT_TO_GROUND_V | SHORT_TO_GROUND_W | SHORT_TO_SUPPLY_U
                              | SHORT_TO_SUPPLY_V | SHORT_TO_SUPPLY_W | SHORT_DETECTED | ENABLE_MISMATCH
                              | TEST_MODE | NO_SUPPLY;
} // namespace fault

/**
 * IO-44: duty of the three phases the gate driver feeds, in per mille of the PWM period (a unit of the function,
 * not of a board: 0 = switch permanently off, DUTY_FULL = permanently on).
 *
 * IO-45: this is the one unit of the driver's contract. The PWM side speaks other units - 0.01 % at the RTE target
 * pwm.<pair>, at OutputPwm and at the console command `pwm set`, Q15 on the fast path, timer ticks in the registers -
 * and the source that the integration attaches converts once, from what the timer is set to, into per mille
 * (platforms/<family>/port/main/src/integration/GateDriverDutySource.h). A value is never handed over in another unit.
 */
struct PhaseDuty
{
    uint16_t u;
    uint16_t v;
    uint16_t w;
};

/**
 * IO-45: state of one phase for the enable rule - its high side is never on (OFF, duty 0), always on (FULL, duty
 * DUTY_FULL or more) or switches (SWITCHING, strictly between).
 */
enum class PhaseState : uint8_t
{
    OFF,
    SWITCHING,
    FULL,
};

/**
 * Complex device driver of the Trinamic/ADI TMC6200 three phase MOSFET gate driver in SPI mode (SPE high).
 *
 * Safety rules (doc/gdTmc6200.md):
 * - DRV_EN is only switched on by enable(), never by init(), cyclic() or a retry.
 * - SYS-147: DRV_EN is the driver's output. Every cyclic() drives it low again while it is not commanded high, in
 *   every state: a high level written by someone else (console `out set`, debugger, stray store) lasts at most
 *   one driver cycle and, with the device supplied, is latched as ENABLE_MISMATCH before it is taken back.
 * - init() drives DRV_EN low first, then reads; it writes only fields the configuration sets (not KEEP) and
 *   reads every write back. Wrong version or SPI error: no write at all.
 * - OTP_PROG and FACTORY_CONF are never written; GCONF test_mode is never set.
 * - A shutdown fault while enabled switches DRV_EN off and latches; enable() is refused until clearFaults().
 * - IO-44: a reported short (GSTAT shortdet_u/v/w) is a shutdown fault, see fault::SHUTDOWN.
 * - IO-44/IO-45: with a phase duty source attached (setPhaseDutySource(), the PWM group this driver feeds) enable()
 *   is refused with PHASES_MIXED while the three phases are not in one common state (all off, all switching or all
 *   full, isCommonState()), instead of letting the bridge run into a fault.
 * - IO-44: clearFaults() answers NOT_CLEARED while the condition is still there after the clear attempt. p.22:
 *   "Switch off the affected MOSFET by its HS/LS input to clear a pending short condition. Just resetting the flag
 *   will not switch it on again."
 * - SYS-36: with an enable lock attached (setEnableLock(), the latch of the safe state S1 in the integration) the
 *   driver asks it at the start of every cycle, before every enable and right before it evaluates IOIN. While it
 *   reports true, DRV_EN is switched off (ENABLED -> DISABLED) and enable() is refused with LOCKED. This is no
 *   fault: the DRV_EN that S1 pulled low behind the driver's back is expected, so no ENABLE_MISMATCH is raised
 *   for it. Once the lock reports false again, enable() is allowed; nothing switches on by itself.
 * - SYS-35: the lock reports false again after the safe state was left (safety::SafeState::leaveSafeState(), the
 *   only place that clears the latch). The explicit enable() after that lifts the lock of DRV_EN; while S1 is
 *   latched it is refused with LOCKED, reported as "refused: safe state".
 * - IO-67: an all-zero IOIN datagram is NO_SUPPLY, not COMMUNICATION. It is latched when the device goes missing
 *   out of DISABLED or ENABLED; before the device was brought up (UNINITIALIZED, FAILED) it is a current fault only,
 *   so a supply switched on after the start needs no clearFaults(). Every other startup failure (SPI error, SDO
 *   stuck high, any other wrong version, read-back mismatch, test mode) still latches. Nothing switches on by itself.
 *
 * Hardware independent (ITmc6200Spi, ITmc6200Io, time as parameter), no heap, not thread safe: all calls from one
 * task context.
 */
class Tmc6200
{
public:
    enum class Status : uint8_t
    {
        OK,
        SPI_ERROR,    // transfer failed or SDO stuck high
        WRONG_DEVICE, // IOIN VERSION differs from the configuration
        VERIFY_ERROR, // read back value differs from the written one
        DEVICE_ERROR, // GCONF test_mode set
        NOT_ALLOWED,  // protected register, driver enabled, or faults pending
        NOT_READY,    // not initialized or failed
        PIN_ERROR,    // DRV_EN could not be written or does not reach the device
        LOCKED,       // enable refused: safe state lock (SYS-36), "refused: safe state" (SYS-35)
        PHASES_MIXED,  // IO-44/IO-45: enable refused, the phases are not in one common state (or not known)
        NOT_CLEARED,   // IO-44: clearFaults() wrote GSTAT, but the condition is still there
        NO_SUPPLY,     // IO-67: IOIN read as an all-zero datagram (no supply VS, or SDO held low), fault::NO_SUPPLY
    };

    /** SYS-36: true while DRV_EN must not be switched on (e.g. the latch of the safe state S1). */
    using EnableLock = bool (*)();

    /**
     * IO-44: reports the duty the PWM group of this bridge is commanded to, in per mille (DUTY_FULL = 100 %).
     * \return false if the duty is not known right now; enable() then refuses as well (it must not guess)
     */
    using PhaseDutySource = bool (*)(PhaseDuty&);

    enum class State : uint8_t
    {
        UNINITIALIZED,
        DISABLED, // configured and verified, DRV_EN low
        ENABLED,  // DRV_EN high on request of the application
        FAILED,   // communication or device error, DRV_EN low, retried every RETRY_MS
    };

    struct StartupInfo
    {
        uint32_t ioin;       // IOIN at the last startup
        uint32_t gstat;      // GSTAT at the last startup (reset flag set after power-on)
        uint32_t otpRead;    // OTP_READ
        uint32_t gconf;      // register values after the startup (read back)
        uint32_t shortConf;
        uint32_t drvConf;
        uint8_t writes;      // registers written by the startup
    };

    struct Counters
    {
        uint32_t startupAttempts;
        uint32_t spiErrors;
        uint32_t verifyErrors;
        uint32_t deviceResets;   // register loss detected while running
        uint32_t shutdowns;      // DRV_EN switched off by the driver because of a fault
        uint32_t enables;
    };

    static constexpr uint32_t RETRY_MS        = 1000U; // new startup attempt in state FAILED
    static constexpr uint32_t CONFIG_CHECK_MS = 100U;  // read back of GCONF, SHORT_CONF, DRV_CONF
    static constexpr uint16_t DUTY_FULL       = 1000U; // IO-44: duty of a phase that is permanently on, per mille

    /** IO-45: state of a phase at `duty` per mille - 0 is OFF, DUTY_FULL or more is FULL, anything between SWITCHING. */
    static constexpr PhaseState phaseState(uint16_t const duty)
    {
        return (duty == 0U) ? PhaseState::OFF : ((duty >= DUTY_FULL) ? PhaseState::FULL : PhaseState::SWITCHING);
    }

    /**
     * IO-45: the enable rule - the three phases are in one common state: all OFF, all SWITCHING or all FULL. Which
     * duty a switching phase has does not matter, and neither does which pair feeds which phase (the rule is the
     * same for every order of u, v, w).
     *
     * Measured, and the reason for this form instead of the rule "every phase switches" of IO-44 (24 V/1 A, motor
     * connected, no load; board sessions 2026-09-23 and 2026-09-24, doc/todo.yaml IO-44, B-IO44-3):
     * - all three at 100 %: 13 of 13 runs without a fault (one of them held 60 s with DRV_EN high at the pin);
     * - all three at 0 %: 11 of 12 without a fault - one outlier with "short detected" (0x0800), which the driver
     *   switches off since IO-44;
     * - all three switching (symmetric 10 to 90 %): 10 of 10 without a fault, and 50 of 50 in B-IO44-4;
     * - mixed: 50/50/0 and 0/0/50 (6 of 6) and 50/50/100 (2 of 2) all with a shutdown fault after about 9 ms.
     * So the upper end stop stays a state of its own - 50/50/100 fails measurably - but "full" is not a fault in
     * itself; what fails is the mixture. The physical reading of the board session, not measured directly: only in
     * a mixture does a direct current flow through the windings, carried by the FET of the standing phase, which is
     * what the drain-source comparison of the short detection sees. The numbers hold for that motor at 24 V only.
     */
    static constexpr bool isCommonState(PhaseDuty const& duty)
    {
        return (phaseState(duty.u) == phaseState(duty.v)) && (phaseState(duty.v) == phaseState(duty.w));
    }

    Tmc6200(ITmc6200Spi& spi, ITmc6200Io& io, Tmc6200Config const& config);

    /** DRV_EN low, read and check the device, write the configured fields. State DISABLED or FAILED. */
    Status init(uint32_t nowMs);

    /**
     * Every cycle (e.g. 10 ms): GSTAT, IOIN, FAULT; configuration check; retries. Never enables.
     * SYS-147: ends with DRV_EN driven low again whenever it is not commanded high (in every state).
     */
    void cyclic(uint32_t nowMs);

    /**
     * Application request: DRV_EN high. Only in DISABLED without pending or latched shutdown faults.
     *
     * IO-44/IO-45: with a phase duty source attached the request is also refused with PHASES_MIXED while the three
     * phases are not in one common state (isCommonState()) or while the source cannot say - the refusal comes now,
     * with its reason, instead of a device fault shortly after (about 9 ms on the board, 2026-09-24).
     */
    Status enable();

    /** DRV_EN low (always allowed, also before init). */
    Status disable();

    /** SYS-36: attaches the enable lock (nullptr: none). Takes effect at the next call. */
    void setEnableLock(EnableLock const lock) { _lock = lock; }

    /**
     * IO-44: attaches the phase duty source of the bridge this driver feeds (nullptr: none).
     *
     * Without a source the rule is not applied - a board that does not tell the driver what its PWM does keeps
     * the behaviour it had before IO-44. Attaching it is the integration's job, because only the integration knows
     * which PWM group belongs to this gate driver (IO-45: board pwm[].gate_driver; the family port attaches a source
     * that reads the timer of that group, integration/GateDriverDutySource.h). The source is called from enable(),
     * i.e. in the task context of the driver, and must be readable there.
     */
    void setPhaseDutySource(PhaseDutySource const source) { _dutySource = source; }

    /** IO-44: duty of the last enable() attempt that asked the source (0/0/0 if it was never asked). */
    PhaseDuty const& getLastPhaseDuty() const { return _lastDuty; }

    /**
     * SYS-36: asks the enable lock now; while it is set DRV_EN goes low at once (no fault).
     * \return true while the driver is locked; false without a lock attached
     */
    bool updateEnableLock();

    /** SYS-36: true while the enable lock is set (as of the last call that asked it). */
    bool isLocked() const { return _locked; }

    /**
     * Clears latched faults and the GSTAT flags (write 1). Only while DRV_EN is low.
     *
     * IO-44: the flags are read back afterwards and the fault bits are rebuilt from what the device still reports.
     * If anything is left, the answer is NOT_CLEARED - clearing a flag does not end the condition behind it
     * (p.22: the affected MOSFET has to be switched off by its HS/LS input first).
     */
    Status clearFaults();

    /** Raw access for diagnosis. Reads: documented registers only. Writes: GCONF (without test_mode),
     * GSTAT, SHORT_CONF, DRV_CONF, and only while DRV_EN is low. */
    Status readRegister(uint8_t address, uint32_t& value);
    Status writeRegister(uint8_t address, uint32_t value);

    State getState() const { return _state; }
    bool isEnabled() const { return _state == State::ENABLED; }
    /** Current fault bits (fault::*) of the last cycle. */
    uint16_t getFaults() const { return _faults; }
    /** Fault bits latched since the last clearFaults() (includes the current ones). */
    uint16_t getLatchedFaults() const { return static_cast<uint16_t>(_latchedFaults | _faults); }
    uint32_t getLastGstat() const { return _lastGstat; }
    uint32_t getLastIoin() const { return _lastIoin; }
    StartupInfo const& getStartupInfo() const { return _startup; }
    Counters const& getCounters() const { return _counters; }
    Tmc6200Config const& getConfig() const { return _config; }

    static constexpr uint64_t readDatagram(uint8_t const address)
    {
        return static_cast<uint64_t>(address & TMC6200_ADDRESS_MASK) << 32U;
    }

    static constexpr uint64_t writeDatagram(uint8_t const address, uint32_t const value)
    {
        return (static_cast<uint64_t>((address & TMC6200_ADDRESS_MASK) | TMC6200_WRITE_FLAG) << 32U) | value;
    }

    static char const* statusName(Status status);
    static char const* stateName(State state);
    /** Name of the lowest set fault bit, "none" for 0. */
    static char const* faultName(uint16_t faults);

private:
    Status read(uint8_t address, uint32_t& value);
    Status write(uint8_t address, uint32_t value);
    Status configureRegister(uint8_t address, uint32_t& value);
    Status configure();
    Status fail(Status status);
    void switchOff();
    uint16_t faultsOf(uint32_t gstat, uint32_t ioin, uint32_t gconf) const;
    void supervise(uint32_t nowMs, bool lockedBefore);
    void holdDriverEnableLow();

    ITmc6200Spi& _spi;
    ITmc6200Io& _io;
    Tmc6200Config const& _config;
    State _state;
    StartupInfo _startup;
    Counters _counters;
    uint32_t _gconf; // expected register values after the startup
    uint32_t _shortConf;
    uint32_t _drvConf;
    uint32_t _lastGstat;
    uint32_t _lastIoin;
    uint32_t _lastAttemptMs;
    uint32_t _lastConfigCheckMs;
    uint16_t _faults;
    uint16_t _latchedFaults;
    EnableLock _lock;
    PhaseDutySource _dutySource;
    PhaseDuty _lastDuty;
    bool _driverEnableCommanded;
    bool _locked;
};

} // namespace gd
