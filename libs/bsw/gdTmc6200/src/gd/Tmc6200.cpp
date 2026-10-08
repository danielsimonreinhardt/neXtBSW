#include "gd/Tmc6200.h"

namespace gd
{
namespace
{
constexpr uint64_t FRAME_MASK = (static_cast<uint64_t>(1U) << TMC6200_FRAME_BITS) - 1U;

uint8_t versionOf(uint32_t const ioin)
{
    return static_cast<uint8_t>((ioin & TMC6200_IOIN_VERSION_MASK) >> TMC6200_IOIN_VERSION_SHIFT);
}

/** Sets a register field unless the configuration keeps it. */
void setField(uint32_t& reg, int16_t const value, uint32_t const mask, uint32_t const shift)
{
    if (value != KEEP)
    {
        reg = (reg & ~mask) | ((static_cast<uint32_t>(value) << shift) & mask);
    }
}
} // namespace

Tmc6200::Tmc6200(ITmc6200Spi& spi, ITmc6200Io& io, Tmc6200Config const& config)
: _spi(spi)
, _io(io)
, _config(config)
, _state(State::UNINITIALIZED)
, _startup()
, _counters()
, _gconf(0U)
, _shortConf(0U)
, _drvConf(0U)
, _lastGstat(0U)
, _lastIoin(0U)
, _lastAttemptMs(0U)
, _lastConfigCheckMs(0U)
, _faults(0U)
, _latchedFaults(0U)
, _lock(nullptr)
, _dutySource(nullptr)
, _lastDuty()
, _driverEnableCommanded(false)
, _locked(false)
{}

bool Tmc6200::updateEnableLock()
{
    _locked = (_lock != nullptr) && _lock();
    if (_locked && (_driverEnableCommanded || (_state == State::ENABLED)))
    {
        // SYS-36: the safe state wants DRV_EN low (it may already be low - S1 walked the safe outputs table).
        // Not a fault and not a shutdown of the driver: DISABLED without a latched fault.
        switchOff();
        if (_state == State::ENABLED)
        {
            _state = State::DISABLED;
        }
    }
    return _locked;
}

Tmc6200::Status Tmc6200::read(uint8_t const address, uint32_t& value)
{
    uint64_t rx = 0U;
    // All ones: SDO not driven (no supply, SPE low, wrong chip select) - never a valid register content here.
    if (!_spi.transfer(readDatagram(address), rx) || ((rx & FRAME_MASK) == FRAME_MASK))
    {
        ++_counters.spiErrors;
        return Status::SPI_ERROR;
    }
    value = static_cast<uint32_t>(rx & TMC6200_DATA_MASK);
    /* IO-67: all zeros for IOIN - status byte and data - is what a device without supply VS answers (SDO tristate
     * with pull-down, p.8 pin 16; power-on reset below the VSA/5VOUT threshold, p.34; eval board 2026-09-18 and
     * 2026-09-30: IOIN 0x00000000). A supplied device reads VERSION 0x10 here, so this never hides a valid answer.
     * Any other wrong IOIN stays a wrong device (COMMUNICATION). */
    if ((address == TMC6200_REG_IOIN) && ((rx & FRAME_MASK) == 0U))
    {
        _lastIoin = 0U; // what `gd status` shows, also when the device goes missing while supervised
        return Status::NO_SUPPLY;
    }
    return Status::OK;
}

Tmc6200::Status Tmc6200::write(uint8_t const address, uint32_t const value)
{
    uint64_t rx = 0U; // read data of a write access is ignored (p.18 4.1.1)
    if (!_spi.transfer(writeDatagram(address, value), rx))
    {
        ++_counters.spiErrors;
        return Status::SPI_ERROR;
    }
    return Status::OK;
}

void Tmc6200::switchOff()
{
    // p.34: after a controller reboot disable the driver before (re)initializing the registers.
    (void)_io.setDriverEnable(false);
    _driverEnableCommanded = false;
}

Tmc6200::Status Tmc6200::fail(Status const status)
{
    if (_state == State::ENABLED)
    {
        ++_counters.shutdowns;
    }
    // IO-67: the device was up (configured and supervised) - losing it now is an event, not a start-up condition
    bool const wasRunning = (_state == State::DISABLED) || (_state == State::ENABLED);
    switchOff();
    _state           = State::FAILED;
    uint16_t const f = (status == Status::DEVICE_ERROR) ? fault::TEST_MODE
                       : (status == Status::NO_SUPPLY)  ? fault::NO_SUPPLY
                                                        : fault::COMMUNICATION;
    // the answer class of this attempt replaces the one of the previous attempt (supply first missing, then there)
    uint16_t const answer = static_cast<uint16_t>(fault::COMMUNICATION | fault::NO_SUPPLY);
    _faults = static_cast<uint16_t>((static_cast<uint32_t>(_faults) & ~static_cast<uint32_t>(answer)) | f);
    /* IO-67: a device that has never been brought up since the start (or since the last clearFaults()) and answers
     * like a device without supply is not latched: nothing was configured or enabled on it, enable() is refused in
     * FAILED anyway, and the next attempt must find the device once the supply is there (board 2026-09-30). Every
     * other failure latches as before, and so does a supply lost out of DISABLED or ENABLED. */
    if ((status != Status::NO_SUPPLY) || wasRunning)
    {
        _latchedFaults = static_cast<uint16_t>(_latchedFaults | f);
    }
    return status;
}

Tmc6200::Status Tmc6200::init(uint32_t const nowMs)
{
    switchOff();
    _lastAttemptMs     = nowMs;
    _lastConfigCheckMs = nowMs;
    ++_counters.startupAttempts;
    _startup = StartupInfo();
    if (!isValidTmc6200Config(_config))
    {
        return fail(Status::NOT_ALLOWED);
    }
    uint32_t ioin = 0U;
    Status status = read(TMC6200_REG_IOIN, ioin);
    if (status != Status::OK)
    {
        return fail(status);
    }
    _startup.ioin = ioin;
    _lastIoin     = ioin;
    // Every write below relies on this register map: nothing is written to another device.
    if (versionOf(ioin) != _config.expectedVersion)
    {
        return fail(Status::WRONG_DEVICE);
    }
    uint32_t gstat = 0U;
    status         = read(TMC6200_REG_GSTAT, gstat);
    if (status == Status::OK)
    {
        _startup.gstat = gstat;
        _lastGstat     = gstat;
        status         = read(TMC6200_REG_OTP_READ, _startup.otpRead);
    }
    if (status == Status::OK)
    {
        status = configure();
    }
    if (status != Status::OK)
    {
        return fail(status);
    }
    _startup.gconf     = _gconf;
    _startup.shortConf = _shortConf;
    _startup.drvConf   = _drvConf;
    _faults            = faultsOf(gstat, ioin, _gconf);
    _latchedFaults     = static_cast<uint16_t>(_latchedFaults | _faults);
    _state             = State::DISABLED;
    return Status::OK;
}

Tmc6200::Status Tmc6200::configureRegister(uint8_t const address, uint32_t& value)
{
    uint32_t current = 0U;
    Status status    = read(address, current);
    if (status != Status::OK)
    {
        return status;
    }
    uint32_t target = current;
    Tmc6200Config const& c = _config;
    switch (address)
    {
        case TMC6200_REG_GCONF:
        {
            if ((current & TMC6200_GCONF_TEST_MODE_MASK) != 0U)
            {
                return Status::DEVICE_ERROR; // DRV_EN is an analog output now: nothing is written, no enable
            }
            setField(target, c.interfaceMode, TMC6200_GCONF_SINGLELINE_MASK, 1U);
            setField(target, c.faultDirect, TMC6200_GCONF_FAULTDIRECT_MASK, 2U);
            setField(target, c.amplification, TMC6200_GCONF_AMPLIFICATION_MASK, TMC6200_GCONF_AMPLIFICATION_SHIFT);
            setField(target, c.amplifierOff, TMC6200_GCONF_AMPLIFIER_OFF_MASK, 6U);
            break;
        }
        case TMC6200_REG_SHORT_CONF:
        {
            setField(
                target, c.shortToSupplyLevel, TMC6200_SHORT_CONF_S2VS_LEVEL_MASK, TMC6200_SHORT_CONF_S2VS_LEVEL_SHIFT);
            setField(
                target, c.shortToGroundLevel, TMC6200_SHORT_CONF_S2G_LEVEL_MASK, TMC6200_SHORT_CONF_S2G_LEVEL_SHIFT);
            setField(
                target, c.shortFilter, TMC6200_SHORT_CONF_SHORTFILTER_MASK, TMC6200_SHORT_CONF_SHORTFILTER_SHIFT);
            setField(target, c.shortDelay, TMC6200_SHORT_CONF_SHORTDELAY_MASK, TMC6200_SHORT_CONF_SHORTDELAY_SHIFT);
            setField(target, c.shortRetries, TMC6200_SHORT_CONF_RETRY_MASK, TMC6200_SHORT_CONF_RETRY_SHIFT);
            setField(target, c.protectParallel, TMC6200_SHORT_CONF_PROTECT_PARALLEL_MASK, 28U);
            break;
        }
        default: // TMC6200_REG_DRV_CONF
        {
            setField(
                target, c.breakBeforeMakeClocks, TMC6200_DRV_CONF_BBMCLKS_MASK, TMC6200_DRV_CONF_BBMCLKS_SHIFT);
            setField(
                target, c.overtemperatureSelect, TMC6200_DRV_CONF_OTSELECT_MASK, TMC6200_DRV_CONF_OTSELECT_SHIFT);
            setField(target, c.driveStrength, TMC6200_DRV_CONF_DRVSTRENGTH_MASK, TMC6200_DRV_CONF_DRVSTRENGTH_SHIFT);
            break;
        }
    }
    if (target != current)
    {
        status = write(address, target);
        uint32_t readBack = 0U;
        if (status == Status::OK)
        {
            ++_startup.writes;
            status = read(address, readBack);
        }
        if ((status == Status::OK) && (readBack != target))
        {
            ++_counters.verifyErrors;
            status = Status::VERIFY_ERROR;
        }
    }
    value = target;
    return status;
}

Tmc6200::Status Tmc6200::configure()
{
    // p.6: set the drive mode first (GCONF), then the protection and driver settings.
    Status status = configureRegister(TMC6200_REG_GCONF, _gconf);
    if (status == Status::OK)
    {
        status = configureRegister(TMC6200_REG_SHORT_CONF, _shortConf);
    }
    if (status == Status::OK)
    {
        status = configureRegister(TMC6200_REG_DRV_CONF, _drvConf);
    }
    return status;
}

uint16_t Tmc6200::faultsOf(uint32_t const gstat, uint32_t const ioin, uint32_t const gconf) const
{
    struct Map
    {
        uint32_t gstat;
        uint16_t fault;
    };
    static constexpr Map const map[] = {
        {TMC6200_GSTAT_UV_CP_MASK, fault::CHARGE_PUMP_UNDERVOLTAGE},
        {TMC6200_GSTAT_DRV_OTPW_MASK, fault::OVERTEMPERATURE_WARNING},
        {TMC6200_GSTAT_DRV_OT_MASK, fault::OVERTEMPERATURE_SHUTDOWN},
        {TMC6200_GSTAT_S2GU_MASK, fault::SHORT_TO_GROUND_U},
        {TMC6200_GSTAT_S2GV_MASK, fault::SHORT_TO_GROUND_V},
        {TMC6200_GSTAT_S2GW_MASK, fault::SHORT_TO_GROUND_W},
        {TMC6200_GSTAT_S2VSU_MASK, fault::SHORT_TO_SUPPLY_U},
        {TMC6200_GSTAT_S2VSV_MASK, fault::SHORT_TO_SUPPLY_V},
        {TMC6200_GSTAT_S2VSW_MASK, fault::SHORT_TO_SUPPLY_W},
        {TMC6200_GSTAT_SHORTDET_U_MASK | TMC6200_GSTAT_SHORTDET_V_MASK | TMC6200_GSTAT_SHORTDET_W_MASK,
         fault::SHORT_DETECTED},
    };
    uint16_t faults = 0U;
    for (Map const& m : map)
    {
        if ((gstat & m.gstat) != 0U)
        {
            faults = static_cast<uint16_t>(faults | m.fault);
        }
    }
    if (((ioin & TMC6200_IOIN_DRV_EN_MASK) != 0U) != _driverEnableCommanded)
    {
        faults = static_cast<uint16_t>(faults | fault::ENABLE_MISMATCH);
    }
    if ((gconf & TMC6200_GCONF_TEST_MODE_MASK) != 0U)
    {
        faults = static_cast<uint16_t>(faults | fault::TEST_MODE);
    }
    return faults;
}

void Tmc6200::cyclic(uint32_t const nowMs)
{
    bool const lockedBefore = _locked;
    (void)updateEnableLock(); // SYS-36: before anything reads DRV_EN back
    if ((_state == State::UNINITIALIZED) || (_state == State::FAILED))
    {
        if ((_counters.startupAttempts == 0U) || ((nowMs - _lastAttemptMs) >= RETRY_MS))
        {
            (void)init(nowMs); // leaves DRV_EN low in any case
        }
    }
    else
    {
        supervise(nowMs, lockedBefore);
    }
    holdDriverEnableLow();
}

void Tmc6200::holdDriverEnableLow()
{
    /* SYS-147: DRV_EN is the driver's output. Every cycle in which it is not commanded high, the driver drives it
     * low again - after the supervision, so an uncommanded high level the device reports in IOIN is still latched
     * as ENABLE_MISMATCH first. Before SYS-147 a high level written by someone else (the OpenBSW console `out set`,
     * a debugger, a stray store to the port) stayed on outside the safe state: DISABLED only latched the mismatch
     * (and only with the device supplied, IOIN readable), FAILED retried once per RETRY_MS. Now it lasts at most
     * one driver cycle in every state; in S1 the hold of the integration takes it back within its own poll.
     * A kept enable (SYS-139 active short) is commanded and therefore not touched. */
    if (!_driverEnableCommanded)
    {
        (void)_io.setDriverEnable(false);
    }
}

void Tmc6200::supervise(uint32_t const nowMs, bool const lockedBefore)
{
    uint32_t gstat = 0U;
    uint32_t ioin  = 0U;
    Status status  = read(TMC6200_REG_GSTAT, gstat);
    if (status == Status::OK)
    {
        status = read(TMC6200_REG_IOIN, ioin);
    }
    if ((status == Status::OK) && (versionOf(ioin) != _config.expectedVersion))
    {
        status = Status::WRONG_DEVICE;
    }
    bool deviceReset = false;
    uint32_t gconf   = _gconf;
    if ((status == Status::OK) && ((nowMs - _lastConfigCheckMs) >= CONFIG_CHECK_MS))
    {
        _lastConfigCheckMs = nowMs;
        uint32_t shortConf = 0U;
        uint32_t drvConf   = 0U;
        status             = read(TMC6200_REG_GCONF, gconf);
        if (status == Status::OK)
        {
            status = read(TMC6200_REG_SHORT_CONF, shortConf);
        }
        if (status == Status::OK)
        {
            status = read(TMC6200_REG_DRV_CONF, drvConf);
        }
        // A power-on reset of the device (VSA, 5VOUT or VCC_IO undervoltage, p.34) restores the reset values.
        deviceReset = (status == Status::OK) && ((gconf != _gconf) || (shortConf != _shortConf) || (drvConf != _drvConf));
    }
    if (status != Status::OK)
    {
        _lastAttemptMs = nowMs;
        (void)fail(status);
        return;
    }
    _lastGstat = gstat;
    _lastIoin  = ioin;
    // The reset flag was cleared when the driver was enabled (enable()): set again means a device reset.
    if ((_state == State::ENABLED) && ((gstat & TMC6200_GSTAT_RESET_MASK) != 0U))
    {
        deviceReset = true;
    }
    if (deviceReset)
    {
        ++_counters.deviceResets;
        if (_state == State::ENABLED)
        {
            ++_counters.shutdowns;
        }
        _latchedFaults = static_cast<uint16_t>(_latchedFaults | fault::DEVICE_RESET);
        _state         = State::DISABLED;
        (void)init(nowMs); // DRV_EN off, configuration written again; enabling needs clearFaults() + enable()
        return;
    }
    /* SYS-36: ask the lock again right before the comparison of IOIN DRV_EN with the commanded level. In the cycle in
     * which the lock arrives, S1 may have pulled DRV_EN low (or not yet) at any point between the reads above and
     * here, so the level read is no evidence of a wiring fault in that cycle - an expected consequence of S1, not
     * an ENABLE_MISMATCH. From the next cycle on DRV_EN is commanded low and checked as always. */
    bool const lockArrived = updateEnableLock() && !lockedBefore;
    uint16_t faults        = faultsOf(gstat, ioin, gconf);
    if (lockArrived)
    {
        faults = static_cast<uint16_t>(static_cast<uint32_t>(faults) & ~static_cast<uint32_t>(fault::ENABLE_MISMATCH));
    }
    bool faultPin = false;
    if (_io.readFault(faultPin) && faultPin)
    {
        faults = static_cast<uint16_t>(faults | fault::FAULT_PIN);
    }
    _faults        = faults;
    _latchedFaults = static_cast<uint16_t>(_latchedFaults | faults);
    if ((_state == State::ENABLED) && ((faults & fault::SHUTDOWN) != 0U))
    {
        ++_counters.shutdowns;
        switchOff();
        _state = State::DISABLED;
    }
}

Tmc6200::Status Tmc6200::enable()
{
    if (updateEnableLock())
    {
        return Status::LOCKED; // SYS-36: DRV_EN is low (switched off right now if it was on)
    }
    if (_state == State::ENABLED)
    {
        return Status::OK;
    }
    if (_state != State::DISABLED)
    {
        return Status::NOT_READY;
    }
    if ((getLatchedFaults() & fault::SHUTDOWN) != 0U)
    {
        return Status::NOT_ALLOWED;
    }
    if (_dutySource != nullptr)
    {
        // IO-44: refuse now, with the reason, instead of switching on into a fault the device reports later.
        // IO-45: the rule is over all three phases together - one common state (isCommonState()).
        PhaseDuty duty{};
        bool const known = _dutySource(duty);
        _lastDuty        = duty;
        if (!known || !isCommonState(duty))
        {
            return Status::PHASES_MIXED;
        }
    }
    if (!_io.setDriverEnable(true))
    {
        switchOff();
        return Status::PIN_ERROR;
    }
    _driverEnableCommanded = true;
    uint32_t ioin          = 0U;
    uint32_t gstat         = 0U;
    Status status          = read(TMC6200_REG_IOIN, ioin);
    if (status == Status::OK)
    {
        _lastIoin = ioin;
        if ((ioin & TMC6200_IOIN_DRV_EN_MASK) == 0U)
        {
            // DRV_EN does not reach the device (wiring, pin configuration): stay off.
            switchOff();
            _faults        = static_cast<uint16_t>(_faults | fault::ENABLE_MISMATCH);
            _latchedFaults = static_cast<uint16_t>(_latchedFaults | fault::ENABLE_MISMATCH);
            return Status::PIN_ERROR;
        }
        status = read(TMC6200_REG_GSTAT, gstat);
    }
    if ((status == Status::OK) && ((gstat & TMC6200_GSTAT_RESET_MASK) != 0U))
    {
        // p.22: the reset flag can only be cleared with DRV_EN high. Cleared, it detects a later device reset.
        status = write(TMC6200_REG_GSTAT, TMC6200_GSTAT_RESET_MASK);
        if (status == Status::OK)
        {
            status = read(TMC6200_REG_GSTAT, gstat);
        }
        if ((status == Status::OK) && ((gstat & TMC6200_GSTAT_RESET_MASK) != 0U))
        {
            ++_counters.verifyErrors;
            switchOff();
            return Status::VERIFY_ERROR;
        }
    }
    if (status != Status::OK)
    {
        return fail(status);
    }
    _lastGstat = gstat;
    if (updateEnableLock())
    {
        // SYS-36: S1 arrived while DRV_EN was being switched on - take it back at once, not one cycle later.
        switchOff();
        return Status::LOCKED;
    }
    ++_counters.enables;
    _state = State::ENABLED;
    return Status::OK;
}

Tmc6200::Status Tmc6200::disable()
{
    bool const ok = _io.setDriverEnable(false);
    _driverEnableCommanded = false;
    if (_state == State::ENABLED)
    {
        _state = State::DISABLED;
    }
    return ok ? Status::OK : Status::PIN_ERROR;
}

Tmc6200::Status Tmc6200::clearFaults()
{
    if (_driverEnableCommanded || (_state == State::ENABLED))
    {
        return Status::NOT_ALLOWED;
    }
    _latchedFaults = 0U;
    if (_state != State::DISABLED)
    {
        _faults = 0U;
        return Status::OK;
    }
    // Write 1 clears the flags (p.22); reset stays set while DRV_EN is low, drv_ot while the device is hot.
    uint32_t gstat = 0U;
    uint32_t ioin  = 0U;
    Status status  = write(TMC6200_REG_GSTAT, TMC6200_GSTAT_ALL_MASK & ~TMC6200_GSTAT_RESET_MASK);
    if (status == Status::OK)
    {
        status = read(TMC6200_REG_GSTAT, gstat);
    }
    if (status == Status::OK)
    {
        status = read(TMC6200_REG_IOIN, ioin); // DRV_EN level now, not the one before switching off
    }
    if (status != Status::OK)
    {
        return fail(status);
    }
    _lastGstat     = gstat;
    _lastIoin      = ioin;
    _faults        = faultsOf(gstat, ioin, _gconf);
    _latchedFaults = _faults;
    // IO-44: say it when nothing was cleared. Board 2026-09-23 (captures 45/46): with the bridge still enabled and
    // a standing phase, "gd clear" answered ok while faults and latched stayed 0x0800 - only switching DRV_EN and
    // the PWM off ended it (p.22: the affected MOSFET must be switched off by its HS/LS input first).
    return (_faults == 0U) ? Status::OK : Status::NOT_CLEARED;
}

Tmc6200::Status Tmc6200::readRegister(uint8_t const address, uint32_t& value)
{
    switch (address)
    {
        case TMC6200_REG_GCONF:
        case TMC6200_REG_GSTAT:
        case TMC6200_REG_IOIN:
        case TMC6200_REG_OTP_READ:
        case TMC6200_REG_FACTORY_CONF:
        case TMC6200_REG_SHORT_CONF:
        case TMC6200_REG_DRV_CONF: return read(address, value);
        default: return Status::NOT_ALLOWED; // OTP_PROG (write only) and undocumented addresses
    }
}

Tmc6200::Status Tmc6200::writeRegister(uint8_t const address, uint32_t const value)
{
    if (_driverEnableCommanded || (_state == State::ENABLED))
    {
        return Status::NOT_ALLOWED;
    }
    if (_state != State::DISABLED)
    {
        return Status::NOT_READY;
    }
    uint32_t* expected = nullptr;
    bool allowed       = false;
    switch (address)
    {
        case TMC6200_REG_GCONF:
            allowed  = ((value & ~TMC6200_GCONF_WRITABLE_MASK) == 0U) && ((value & TMC6200_GCONF_TEST_MODE_MASK) == 0U);
            expected = &_gconf;
            break;
        case TMC6200_REG_GSTAT: allowed = (value & ~TMC6200_GSTAT_ALL_MASK) == 0U; break;
        case TMC6200_REG_SHORT_CONF:
            // The short protection stays on: disable_S2G / disable_S2VS are not accepted.
            allowed = ((value & ~TMC6200_SHORT_CONF_WRITABLE_MASK) == 0U)
                      && ((value & (TMC6200_SHORT_CONF_DISABLE_S2G_MASK | TMC6200_SHORT_CONF_DISABLE_S2VS_MASK)) == 0U);
            expected = &_shortConf;
            break;
        case TMC6200_REG_DRV_CONF:
            allowed  = (value & ~TMC6200_DRV_CONF_WRITABLE_MASK) == 0U;
            expected = &_drvConf;
            break;
        default: break; // IOIN, OTP_PROG, OTP_READ, FACTORY_CONF and undocumented addresses
    }
    if (!allowed)
    {
        return Status::NOT_ALLOWED;
    }
    Status status = write(address, value);
    if ((status != Status::OK) || (expected == nullptr))
    {
        return status;
    }
    uint32_t readBack = 0U;
    status            = read(address, readBack);
    if (status == Status::OK)
    {
        *expected = readBack; // new reference for the device reset detection
        if (readBack != value)
        {
            ++_counters.verifyErrors;
            status = Status::VERIFY_ERROR;
        }
    }
    return status;
}

char const* Tmc6200::statusName(Status const status)
{
    switch (status)
    {
        case Status::OK: return "ok";
        case Status::SPI_ERROR: return "SPI error";
        case Status::WRONG_DEVICE: return "wrong device version";
        case Status::VERIFY_ERROR: return "read-back mismatch";
        case Status::DEVICE_ERROR: return "test mode active";
        case Status::NOT_ALLOWED: return "not allowed";
        case Status::NOT_READY: return "not ready";
        case Status::PIN_ERROR: return "DRV_EN pin error";
        // SYS-35: the clear refusal of the user decision safe-state-rueckweg; at most 20 characters (log entries,
        // tools/logcheck/bounds.yaml)
        case Status::LOCKED: return "refused: safe state";
        // IO-44: the reason of the refusal (IO-45: the phases are not in one common state), and the honest answer
        // of "gd clear" (20 characters at most, as above)
        case Status::PHASES_MIXED: return "phases mixed";
        case Status::NOT_CLEARED: return "fault still present";
        // IO-67: IOIN all zeros - both causes named, the driver cannot tell them apart (20 characters, as above)
        case Status::NO_SUPPLY: return "no supply or SDO low";
        default: return "?";
    }
}

char const* Tmc6200::stateName(State const state)
{
    switch (state)
    {
        case State::UNINITIALIZED: return "uninitialized";
        case State::DISABLED: return "disabled";
        case State::ENABLED: return "enabled";
        case State::FAILED: return "failed";
        default: return "?";
    }
}

char const* Tmc6200::faultName(uint16_t const faults)
{
    static constexpr char const* const names[] = {
        "communication",
        "device reset",
        "charge pump undervoltage",
        "overtemperature warning",
        "overtemperature shutdown",
        "short to GND U",
        "short to GND V",
        "short to GND W",
        "short to VS U",
        "short to VS V",
        "short to VS W",
        "short detected",
        "FAULT pin",
        "DRV_EN mismatch",
        "test mode",
        "no supply", // IO-67
    };
    for (uint32_t bit = 0U; bit < (sizeof(names) / sizeof(names[0])); ++bit)
    {
        if ((faults & (1U << bit)) != 0U)
        {
            return names[bit];
        }
    }
    return "none";
}

} // namespace gd
