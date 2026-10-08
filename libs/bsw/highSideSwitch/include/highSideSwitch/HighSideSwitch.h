#pragma once

#include <cstdint>

namespace highside
{

/**
 * Supervision parameters of one smart high-side switch (PROFET-like: IN, DEN, current sense IS).
 *
 * All levels are load currents in mA, the physical quantity of the function (IO-21,
 * doc/architecture/treiber-granularitaet.md section 5). The conversion from the sense signal (IS voltage,
 * sense ratio kILIS, sense resistor) is board knowledge: the integration converts every sample before it
 * reaches this class (IHighSideSwitchIo::readSense), so no part fact is used here. The driver holds no
 * default values: every instance gets its configuration from the integration.
 */
struct HighSideSwitchConfig
{
    /** Period of step() in ms. */
    uint32_t cycleMs;
    /** Load current in mA at or above which it counts as overcurrent. */
    uint32_t overcurrentMa;
    /** Overcurrent must persist this long before the switch is turned off. */
    uint32_t overcurrentDebounceMs;
    /** Load current in mA at or above which the fault current of the switch is assumed (IS saturated). */
    uint32_t faultMa;
    /** Fault level must persist this long before the switch is turned off. */
    uint32_t faultDebounceMs;
    /** After switching on, current thresholds are ignored this long (inrush, IS settling). */
    uint32_t switchOnBlankingMs;
    /** While on and after blanking, a load current below this (mA) is reported as open load (warning only). */
    uint32_t openLoadMa;
    /** While off (DEN low) the reading must stay at or below this (mA), otherwise the sense path is implausible. */
    uint32_t offMaxMa;
    /** Implausible off-state reading must persist this long before a diagnosis fault is latched. */
    uint32_t offDebounceMs;
};

/**
 * Structural checks of a configuration (usable in static_assert). The parameter schema in module.yaml
 * (ranges, constraints) states the same rules, so tools/nxconfig reports them before a build.
 */
constexpr bool isValidHighSideSwitchConfig(HighSideSwitchConfig const& c)
{
    return (c.cycleMs > 0U) && (c.cycleMs <= 1000U) && (c.overcurrentMa > 0U)
           && (c.faultMa > c.overcurrentMa) && (c.openLoadMa < c.overcurrentMa) && (c.offMaxMa > 0U)
           && (c.offMaxMa < c.faultMa) && (c.overcurrentDebounceMs >= c.cycleMs)
           && (c.faultDebounceMs >= c.cycleMs) && (c.offDebounceMs >= c.cycleMs)
           && (c.overcurrentDebounceMs <= 1000U) && (c.faultDebounceMs <= 1000U)
           && (c.offDebounceMs <= 1000U) && (c.switchOnBlankingMs <= 1000U);
}

/**
 * Hardware independent state machine of a smart high-side switch.
 *
 * - OFF: IN and DEN low. Default at construction and after every fault.
 * - ON: IN and DEN high. After the blanking time the load current is checked every cycle.
 * - OVERCURRENT: load current at or above overcurrentMa (debounced) -> IN/DEN low, latched.
 * - DIAG_FAULT: fault current level (debounced), ADC read error or implausible reading while off
 *   -> IN/DEN low, latched.
 *
 * Latched states are left only by reset() (-> OFF); switching on again needs a separate on
 * command. There is no automatic retry: smart high-side switches limit the current and restart
 * thermally on their own, so repeated software retries would only add stress to a faulty load.
 *
 * SYS-36, safe state lock: lock() switches off (ON -> OFF) and refuses every requestOn() with
 * LOCKED until unlock(); it is orthogonal to the states, so a latched fault stays latched and
 * reset() still clears it (the switch stays off). The supervision keeps running while locked (off
 * plausibility). Who locks is the integration's business (HighSideSwitchChannel::setSwitchOnLock()).
 *
 * Usage per cycle: sample IS (while the outputs of the previous cycle are applied), call step(),
 * then drive the pins from isEnableOutput()/isDiagEnableOutput() (HighSideSwitchChannel does this).
 * Commands take effect on the outputs immediately.
 */
class HighSideSwitch
{
public:
    enum class State : uint8_t
    {
        OFF,
        ON,
        OVERCURRENT,
        DIAG_FAULT,
    };

    enum class Fault : uint8_t
    {
        NONE,
        OVERCURRENT,
        IS_FAULT_LEVEL,
        ADC_ERROR,
        OFF_IMPLAUSIBLE,
    };

    enum class CommandResult : uint8_t
    {
        OK,
        ALREADY,
        LATCHED, // on refused: reset first
        LOCKED,  // on refused: safe state lock (SYS-36)
    };

    struct Counters
    {
        uint32_t switchOns;
        uint32_t overcurrentTrips;
        uint32_t diagFaults;
        uint32_t adcErrors;
        uint32_t openLoadCycles;
    };

    explicit HighSideSwitch(HighSideSwitchConfig const& config);

    CommandResult requestOn();
    CommandResult requestOff();
    /** Clears a latched fault (-> OFF). ALREADY if nothing was latched. */
    CommandResult reset();

    /** SYS-36: off (ON -> OFF) and every requestOn() refused until unlock(). Idempotent. */
    void lock();

    /** SYS-36: requestOn() allowed again; switches nothing on. */
    void unlock() { _locked = false; }

    bool isLocked() const { return _locked; }

    /**
     * One supervision cycle.
     * \param adcOk   false if the sense sample could not be read
     * \param loadMa  load current in mA converted by the integration (ignored if adcOk is false)
     */
    void step(bool adcOk, uint32_t loadMa);

    State getState() const { return _state; }

    Fault getLastFault() const { return _lastFault; }

    // SYS-36: the lock is part of the output decision as well, not only of requestOn()
    bool isEnableOutput() const { return (_state == State::ON) && !_locked; }

    bool isDiagEnableOutput() const { return (_state == State::ON) && !_locked; }

    /** True while on, after blanking, with a load current below openLoadMa. */
    bool isOpenLoad() const { return _openLoad; }

    /** True while the blanking time after switching on is running. */
    bool isBlanking() const;

    /** Load current of the last valid sample in mA. */
    uint32_t getLastMa() const { return _lastMa; }

    /** Highest load current in mA since the last switch-on (after blanking). */
    uint32_t getMaxOnMa() const { return _maxOnMa; }

    Counters const& getCounters() const { return _counters; }

    HighSideSwitchConfig const& getConfig() const { return _config; }

    static char const* stateName(State state);
    static char const* faultName(Fault fault);
    static char const* resultName(CommandResult result);

private:
    static uint32_t toCycles(uint32_t ms, uint32_t cycleMs);
    static void increment(uint32_t& value);
    void latch(State state, Fault fault);
    void clearDebounce();

    HighSideSwitchConfig const& _config;
    uint32_t const _blankingCycles;
    uint32_t const _overcurrentCycles;
    uint32_t const _faultCycles;
    uint32_t const _offCycles;
    State _state;
    Fault _lastFault;
    uint32_t _onCycles;
    uint32_t _overcurrentCount;
    uint32_t _faultCount;
    uint32_t _offCount;
    uint32_t _lastMa;
    uint32_t _maxOnMa;
    bool _openLoad;
    bool _locked;
    Counters _counters;
};

} // namespace highside
