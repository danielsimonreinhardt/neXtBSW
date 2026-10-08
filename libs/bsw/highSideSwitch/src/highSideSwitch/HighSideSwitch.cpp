#include "highSideSwitch/HighSideSwitch.h"

namespace highside
{

HighSideSwitch::HighSideSwitch(HighSideSwitchConfig const& config)
: _config(config)
, _blankingCycles(toCycles(config.switchOnBlankingMs, config.cycleMs))
, _overcurrentCycles(toCycles(config.overcurrentDebounceMs, config.cycleMs))
, _faultCycles(toCycles(config.faultDebounceMs, config.cycleMs))
, _offCycles(toCycles(config.offDebounceMs, config.cycleMs))
, _state(State::OFF)
, _lastFault(Fault::NONE)
, _onCycles(0U)
, _overcurrentCount(0U)
, _faultCount(0U)
, _offCount(0U)
, _lastMa(0U)
, _maxOnMa(0U)
, _openLoad(false)
, _locked(false)
, _counters{0U, 0U, 0U, 0U, 0U}
{}

uint32_t HighSideSwitch::toCycles(uint32_t const ms, uint32_t const cycleMs)
{
    if (cycleMs == 0U)
    {
        return ms;
    }
    // Round up: a debounce time is never shortened by the cycle raster.
    return (ms / cycleMs) + (((ms % cycleMs) != 0U) ? 1U : 0U);
}

void HighSideSwitch::increment(uint32_t& value)
{
    if (value != UINT32_MAX)
    {
        ++value;
    }
}

bool HighSideSwitch::isBlanking() const { return (_state == State::ON) && (_onCycles <= _blankingCycles); }

void HighSideSwitch::clearDebounce()
{
    _overcurrentCount = 0U;
    _faultCount       = 0U;
    _offCount         = 0U;
    _openLoad         = false;
}

void HighSideSwitch::latch(State const state, Fault const fault)
{
    _state     = state;
    _lastFault = fault;
    clearDebounce();
    if (state == State::OVERCURRENT)
    {
        increment(_counters.overcurrentTrips);
    }
    else
    {
        increment(_counters.diagFaults);
    }
}

HighSideSwitch::CommandResult HighSideSwitch::requestOn()
{
    if (_locked)
    {
        return CommandResult::LOCKED;
    }
    switch (_state)
    {
        case State::OFF:
        {
            _state    = State::ON;
            _onCycles = 0U;
            _maxOnMa  = 0U;
            clearDebounce();
            increment(_counters.switchOns);
            return CommandResult::OK;
        }
        case State::ON:
        {
            return CommandResult::ALREADY;
        }
        default:
        {
            return CommandResult::LATCHED;
        }
    }
}

HighSideSwitch::CommandResult HighSideSwitch::requestOff()
{
    if (_state == State::ON)
    {
        _state = State::OFF;
        clearDebounce();
        return CommandResult::OK;
    }
    // OFF or latched: outputs are already off, a latched fault stays latched.
    return CommandResult::ALREADY;
}

void HighSideSwitch::lock()
{
    _locked = true;
    // same as requestOff(): a latched fault stays latched, OFF stays OFF
    (void)requestOff();
}

HighSideSwitch::CommandResult HighSideSwitch::reset()
{
    if ((_state == State::OVERCURRENT) || (_state == State::DIAG_FAULT))
    {
        _state = State::OFF;
        clearDebounce();
        return CommandResult::OK;
    }
    return CommandResult::ALREADY;
}

void HighSideSwitch::step(bool const adcOk, uint32_t const loadMa)
{
    if (!adcOk)
    {
        increment(_counters.adcErrors);
        _openLoad = false;
        // Without the sense signal neither overcurrent nor a sense path fault can be detected.
        if ((_state == State::ON) || (_state == State::OFF))
        {
            latch(State::DIAG_FAULT, Fault::ADC_ERROR);
        }
        return;
    }
    _lastMa = loadMa;

    switch (_state)
    {
        case State::OFF:
        {
            _offCount = (loadMa > _config.offMaxMa) ? (_offCount + 1U) : 0U;
            if (_offCount >= _offCycles)
            {
                latch(State::DIAG_FAULT, Fault::OFF_IMPLAUSIBLE);
            }
            break;
        }
        case State::ON:
        {
            increment(_onCycles);
            if (_onCycles <= _blankingCycles)
            {
                break;
            }
            if (loadMa > _maxOnMa)
            {
                _maxOnMa = loadMa;
            }
            _faultCount       = (loadMa >= _config.faultMa) ? (_faultCount + 1U) : 0U;
            _overcurrentCount = (loadMa >= _config.overcurrentMa) ? (_overcurrentCount + 1U) : 0U;
            if (_faultCount >= _faultCycles)
            {
                latch(State::DIAG_FAULT, Fault::IS_FAULT_LEVEL);
            }
            else if (_overcurrentCount >= _overcurrentCycles)
            {
                latch(State::OVERCURRENT, Fault::OVERCURRENT);
            }
            else
            {
                _openLoad = (loadMa < _config.openLoadMa);
                if (_openLoad)
                {
                    increment(_counters.openLoadCycles);
                }
            }
            break;
        }
        default:
        {
            // Latched: outputs stay off until reset().
            break;
        }
    }
}

char const* HighSideSwitch::stateName(State const state)
{
    switch (state)
    {
        case State::OFF: return "off";
        case State::ON: return "on";
        case State::OVERCURRENT: return "overcurrent (latched)";
        case State::DIAG_FAULT: return "diagnosis fault (latched)";
        default: return "?";
    }
}

char const* HighSideSwitch::faultName(Fault const fault)
{
    switch (fault)
    {
        case Fault::NONE: return "none";
        case Fault::OVERCURRENT: return "overcurrent";
        case Fault::IS_FAULT_LEVEL: return "IS fault level";
        case Fault::ADC_ERROR: return "ADC read error";
        case Fault::OFF_IMPLAUSIBLE: return "IS implausible while off";
        default: return "?";
    }
}

char const* HighSideSwitch::resultName(CommandResult const result)
{
    switch (result)
    {
        case CommandResult::OK: return "ok";
        case CommandResult::ALREADY: return "no change";
        case CommandResult::LATCHED: return "refused, fault latched (reset first)";
        case CommandResult::LOCKED: return "refused, safe state (locked until reset)";
        default: return "?";
    }
}

} // namespace highside
