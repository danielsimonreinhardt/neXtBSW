#include "sbc/Uja113x.h"
#include "sbc/Uja113xResetCounter.h"

namespace sbc
{
namespace
{
uint8_t const REGULATOR_MASK   = UJA113X_V2SC_MASK | UJA113X_V2C_MASK | UJA113X_V1RTC_MASK;
uint8_t const CAN_CONTROL_MASK = UJA113X_CFDC_MASK | UJA113X_CPNC_MASK | UJA113X_CSC_MASK | UJA113X_CMC_MASK;
uint8_t const SYSTEM_IRQ_MASK  = UJA113X_OTWIE_MASK | UJA113X_SPIFIE_MASK;
uint8_t const SUPPLY_IRQ_MASK  = 0x3FU;
uint8_t const TRX_IRQ_MASK     = UJA113X_CBSI_MASK | UJA113X_LWI2_MASK | UJA113X_LWI1_MASK
                               | UJA113X_CFI_MASK | UJA113X_CWI_MASK;
uint8_t const WATCHDOG_MASK    = UJA113X_WMC_MASK | UJA113X_NWP_MASK;

uint8_t const REG_BANK1_WAKE_IRQ_STATUS = UJA113X_REG_BANK1_WAKE_IRQ_STATUS;
uint8_t const REG_BANK1_FAIL_IRQ_STATUS = UJA113X_REG_BANK1_FAIL_IRQ_STATUS;

uint32_t const STANDARD_ID_MASK = 0x7FFUL;

/* First register of an HVIO bank block: bank 0 lives at 0x30.., bank 1 at 0x40.. with the same layout. */
uint8_t hvioRegister(uint8_t const bank, uint8_t const bank0Address)
{
    return static_cast<uint8_t>(bank0Address + (bank * 0x10U));
}

/** IOnCC out of function and slope control (data sheet table 34). */
uint8_t hvioConfigurationCode(::sbc::HvioFunction const function, bool const slopeControl)
{
    switch (function)
    {
        case ::sbc::HvioFunction::HIGH_SIDE:
            return slopeControl ? UJA113X_IOCC_HS_SLOPE : UJA113X_IOCC_HS;
        case ::sbc::HvioFunction::LOW_SIDE:
            return slopeControl ? UJA113X_IOCC_LS_SLOPE : UJA113X_IOCC_LS;
        case ::sbc::HvioFunction::WAKE_INPUT: return UJA113X_IOCC_WAKE_INPUT;
        case ::sbc::HvioFunction::HIGH_SIDE_AND_WAKE:
            return slopeControl ? UJA113X_IOCC_HS_WAKE_SLOPE : UJA113X_IOCC_HS_WAKE;
        default: return UJA113X_IOCC_OFF;
    }
}

uint8_t hvioControlValue(::sbc::HvioConfig const& pin)
{
    uint8_t const function = hvioConfigurationCode(pin.function, pin.slopeControl);
    if (function == UJA113X_IOCC_OFF)
    {
        return 0U; // not configured: the SBC ignores every other bit, so keep the register clean
    }
    return static_cast<uint8_t>((static_cast<uint8_t>(pin.shutdown) << UJA113X_IOSC_SHIFT)
                                | (static_cast<uint8_t>(pin.activation) << UJA113X_IOAC_SHIFT) | function);
}

/** True if the pin can report an input level at all (wake-up input, alone or with the HS driver). */
bool hvioIsInput(::sbc::HvioConfig const& pin)
{
    return (pin.function == ::sbc::HvioFunction::WAKE_INPUT)
           || (pin.function == ::sbc::HvioFunction::HIGH_SIDE_AND_WAKE);
}

bool hvioIsDriver(::sbc::HvioConfig const& pin)
{
    return (pin.function == ::sbc::HvioFunction::HIGH_SIDE)
           || (pin.function == ::sbc::HvioFunction::LOW_SIDE)
           || (pin.function == ::sbc::HvioFunction::HIGH_SIDE_AND_WAKE);
}

/** CDR code of a bit rate, false if the partial networking of the SBC does not offer it. */
bool dataRateCode(uint32_t const bitrate, uint8_t& code)
{
    struct Rate
    {
        uint32_t bitrate;
        uint8_t code;
    };
    static Rate const RATES[] = {
        {50000UL, UJA113X_CDR_50K},
        {100000UL, UJA113X_CDR_100K},
        {125000UL, UJA113X_CDR_125K},
        {250000UL, UJA113X_CDR_250K},
        {500000UL, UJA113X_CDR_500K},
        {1000000UL, UJA113X_CDR_1000K},
    };
    for (Rate const& rate : RATES)
    {
        if (rate.bitrate == bitrate)
        {
            code = rate.code;
            return true;
        }
    }
    return false;
}

bool isProtected(uint8_t const address)
{
    // Watchdog and mode changes have side effects (reset, Sleep); 0x70.. is the MTPNV area.
    return (address == UJA113X_REG_WATCHDOG_CONTROL) || (address == UJA113X_REG_MODE_CONTROL)
           || (address >= UJA113X_REG_MTPNV_STATUS);
}
} // namespace

Uja113x::Uja113x(::bsp::spi::ISpiDevice& spi, Uja113xConfig const& config)
: _spi(spi)
, _config(config)
, _canWakeupFrame(nullptr)
, _state(State::UNINITIALIZED)
, _startup()
, _counters()
, _events()
, _lastStartupAttemptMs(0U)
, _lastTriggerMs(0U)
, _lastTransceiverPollMs(0U)
, _lastModeCheckMs(0U)
, _transceiverStatus(0U)
, _hvioBanks(0U)
, _canReady(false)
, _watchdogActive(false)
, _limpHome(false)
, _batteryEvent(0U)
, _batteryEventSinceMs(0U)
, _canShutOff(false)
, _v2ShutOff(false)
, _lastInterruptReadMs(0U)
, _lastInterruptClearMs(0U)
, _lineMissesInRow(0U)
, _lineFault(false)
, _standbyWakeup{false, 0U, false, config.watchdogPeriod}
, _standbyWatchdog(0U)
, _standbyWatchdogService(false)
{}

/* ---- battery monitor: physical units to the register grid and back (IO-10) ---- */

uint8_t Uja113x::batteryThresholdCode(uint16_t const mv, bool const roundUp)
{
    uint32_t const full = UJA113X_BM_FULL_SCALE_MV;
    if (mv >= full)
    {
        return 0xFFU;
    }
    uint32_t const steps   = UJA113X_BM_THRESHOLD_STEPS;
    uint32_t const product = static_cast<uint32_t>(mv) * steps;
    uint32_t const code    = roundUp ? ((product + full - 1U) / full) : (product / full);
    return static_cast<uint8_t>((code > 0xFFU) ? 0xFFU : code);
}

uint16_t Uja113x::batteryThresholdMv(uint8_t const code)
{
    // Rounded to the nearest mV: what the SBC really compares against, for logs and checks.
    uint32_t const full = UJA113X_BM_FULL_SCALE_MV;
    uint32_t const mv   = ((static_cast<uint32_t>(code) * full) + (UJA113X_BM_THRESHOLD_STEPS / 2U))
                        / UJA113X_BM_THRESHOLD_STEPS;
    return static_cast<uint16_t>(mv);
}

uint8_t Uja113x::batteryHysteresisCode(uint16_t const mv)
{
    // Step is four threshold steps (313.7 mV); rounded down as an exact fraction, so the release window is
    // never wider than requested - dividing by a truncated step would sometimes give one code too many.
    uint32_t const code = (static_cast<uint32_t>(mv) * UJA113X_BM_THRESHOLD_STEPS)
                          / (4UL * UJA113X_BM_FULL_SCALE_MV);
    return static_cast<uint8_t>((code > 0x0FU) ? 0x0FU : code);
}

uint16_t Uja113x::batteryHysteresisMv(uint8_t const code)
{
    uint32_t const full = 4UL * UJA113X_BM_FULL_SCALE_MV;
    uint32_t const mv   = ((static_cast<uint32_t>(code & 0x0FU) * full) + (UJA113X_BM_THRESHOLD_STEPS / 2U))
                        / UJA113X_BM_THRESHOLD_STEPS;
    return static_cast<uint16_t>(mv);
}

uint16_t Uja113x::batteryAdcMv(uint16_t const code)
{
    uint32_t const raw = (code > UJA113X_BM_ADC_STEPS) ? UJA113X_BM_ADC_STEPS : code;
    uint32_t const mv  = ((raw * UJA113X_BM_FULL_SCALE_MV) + (UJA113X_BM_ADC_STEPS / 2U))
                        / UJA113X_BM_ADC_STEPS;
    return static_cast<uint16_t>(mv);
}

Uja113x::BatteryThresholds Uja113x::getBatteryThresholds() const
{
    BatteryThresholds t{};
    t.undervoltageMv = batteryThresholdMv(batteryThresholdCode(_config.battery.undervoltageMv, true));
    t.overvoltageMv  = batteryThresholdMv(batteryThresholdCode(_config.battery.overvoltageMv, false));
    t.undervoltageHysteresisMv
        = batteryHysteresisMv(batteryHysteresisCode(_config.battery.undervoltageHysteresisMv));
    t.overvoltageHysteresisMv
        = batteryHysteresisMv(batteryHysteresisCode(_config.battery.overvoltageHysteresisMv));
    return t;
}

/* ---- HVIO detection thresholds: load current in mA to the grid of the SBC (IO-10) ----
 * Typical values of data sheet table 90 (identical for high- and low-side drivers). The grid value at or
 * below the requested current is chosen, so the SBC never reacts later than the project asked for; below
 * the smallest value the smallest one is used. */

namespace
{
uint16_t const SHORT_CIRCUIT_MA[4] = {30U, 45U, 60U, 90U};
uint16_t const OPEN_LOAD_MA[4]     = {2U, 5U, 10U, 20U};

uint8_t thresholdCode(uint16_t const ma, uint16_t const (&grid)[4])
{
    uint8_t code = 0U;
    for (uint8_t i = 0U; i < 4U; ++i)
    {
        if (grid[i] <= ma)
        {
            code = i;
        }
    }
    return code;
}
} // namespace

uint8_t Uja113x::shortCircuitThresholdCode(uint16_t const ma) { return thresholdCode(ma, SHORT_CIRCUIT_MA); }

uint16_t Uja113x::shortCircuitThresholdMa(uint8_t const code) { return SHORT_CIRCUIT_MA[code & 0x03U]; }

uint8_t Uja113x::openLoadThresholdCode(uint16_t const ma) { return thresholdCode(ma, OPEN_LOAD_MA); }

uint16_t Uja113x::openLoadThresholdMa(uint8_t const code) { return OPEN_LOAD_MA[code & 0x03U]; }

uint32_t Uja113x::watchdogPeriodMs(WatchdogPeriod const period)
{
    switch (period)
    {
        case WatchdogPeriod::MS_8: return 8U;
        case WatchdogPeriod::MS_16: return 16U;
        case WatchdogPeriod::MS_32: return 32U;
        case WatchdogPeriod::MS_64: return 64U;
        case WatchdogPeriod::MS_128: return 128U;
        case WatchdogPeriod::MS_256: return 256U;
        case WatchdogPeriod::MS_1024: return 1024U;
        case WatchdogPeriod::MS_4096: return 4096U;
        default: return 0U;
    }
}

WatchdogPeriod Uja113x::watchdogPeriodFor(uint32_t const ms)
{
    // Ascending order of the periods the SBC offers (data sheet Table 7, NWP), longest one is the upper limit.
    static WatchdogPeriod const PERIODS[] = {
        WatchdogPeriod::MS_8,
        WatchdogPeriod::MS_16,
        WatchdogPeriod::MS_32,
        WatchdogPeriod::MS_64,
        WatchdogPeriod::MS_128,
        WatchdogPeriod::MS_256,
        WatchdogPeriod::MS_1024,
        WatchdogPeriod::MS_4096,
    };
    for (WatchdogPeriod const period : PERIODS)
    {
        if (watchdogPeriodMs(period) >= ms)
        {
            return period;
        }
    }
    return WatchdogPeriod::MS_4096;
}

char const* Uja113x::resetSourceName(uint8_t const rss)
{
    static char const* const names[] = {
        "power-on",
        "CAN wake-up",
        "LIN1 wake-up",
        "LIN2 wake-up",
        "HVIO1 wake-up",
        "HVIO2 wake-up",
        "HVIO3 wake-up",
        "HVIO4 wake-up",
        "HVIO5 wake-up",
        "HVIO6 wake-up",
        "HVIO7 wake-up",
        "HVIO8 wake-up",
        "watchdog overflow in Sleep",
        "diagnostic wake-up",
        "watchdog triggered too early",
        "watchdog overflow",
        "illegal watchdog mode change",
        "RSTN pulled down",
        "leaving overload",
        "V1 undervoltage",
        "illegal Sleep request",
        "wake-up from forced sleep preparation",
    };
    return (rss < (sizeof(names) / sizeof(names[0]))) ? names[rss] : "reserved";
}

Uja113x::Status Uja113x::read(uint8_t const address, uint8_t& value)
{
    uint32_t const tx = (static_cast<uint32_t>(address) << UJA113X_SPI_ADDRESS_SHIFT) | UJA113X_SPI_RO_BIT;
    uint32_t rx       = 0U;
    if (!_spi.transfer(tx, rx))
    {
        ++_counters.spiErrors;
        return Status::SPI_ERROR;
    }
    // AH1501 2.6: SDO returns the address during the first byte (confirmed on the eval board).
    // A mismatch means no SBC answering, e.g. SDO stuck at 0x00/0xFF or wrong chip select.
    if (((rx >> UJA113X_SPI_ADDRESS_SHIFT) & 0x7FU) != address)
    {
        ++_counters.addressEchoMismatches;
        return Status::SPI_ERROR;
    }
    value = static_cast<uint8_t>(rx & 0xFFU);
    return Status::OK;
}

Uja113x::Status Uja113x::write(uint8_t const address, uint8_t const value)
{
    uint32_t const tx = (static_cast<uint32_t>(address) << UJA113X_SPI_ADDRESS_SHIFT) | value;
    uint32_t rx       = 0U;
    if (!_spi.transfer(tx, rx))
    {
        ++_counters.spiErrors;
        return Status::SPI_ERROR;
    }
    return Status::OK;
}

Uja113x::Status Uja113x::writeVerified(uint8_t const address, uint8_t const value, uint8_t const mask)
{
    Status status = write(address, value);
    uint8_t readBack = 0U;
    if (status == Status::OK)
    {
        status = read(address, readBack);
    }
    if ((status == Status::OK) && ((readBack & mask) != (value & mask)))
    {
        ++_counters.verifyErrors;
        status = Status::VERIFY_ERROR;
    }
    return status;
}

Uja113x::Status Uja113x::readAndClearInterrupts(uint8_t const global, InterruptFlags& flags)
{
    struct Source
    {
        uint8_t globalMask;
        uint8_t address;
        uint8_t* target;
    };
    // Each bank has its own pair of registers; merging them would map an HVIO5..8 event onto HVIO1..4 (IO-10).
    Source const sources[] = {
        {UJA113X_SYSIS_MASK, UJA113X_REG_SYSTEM_IRQ_STATUS, &flags.system},
        {UJA113X_SUPIS_MASK, UJA113X_REG_SUPPLY_IRQ_STATUS, &flags.supply},
        {UJA113X_TRXIS_MASK, UJA113X_REG_TRANSCEIVER_IRQ_STATUS, &flags.transceiver},
        {UJA113X_B0WIS_MASK, UJA113X_REG_BANK0_WAKE_IRQ_STATUS, &flags.wake},
        {UJA113X_B0FIS_MASK, UJA113X_REG_BANK0_FAIL_IRQ_STATUS, &flags.failure},
        {UJA113X_B1WIS_MASK, REG_BANK1_WAKE_IRQ_STATUS, &flags.wakeBank1},
        {UJA113X_B1FIS_MASK, REG_BANK1_FAIL_IRQ_STATUS, &flags.failureBank1},
    };
    for (Source const& source : sources)
    {
        if ((global & source.globalMask) == 0U)
        {
            continue;
        }
        uint8_t pending = 0U;
        Status status   = read(source.address, pending);
        if (status == Status::OK)
        {
            status = write(source.address, pending); // write 1 to clear
        }
        if (status != Status::OK)
        {
            return status;
        }
        *source.target = static_cast<uint8_t>(*source.target | pending);
    }
    return Status::OK;
}

uint8_t Uja113x::canControlNormalValue(uint8_t const current) const
{
    // Partial networking (CPNC, PNCOK) and CAN FD tolerance off. The shutdown control CSC used to be kept:
    // its power-up default is 01, so the transceiver would go offline on a battery undervoltage as soon as a
    // project arms the monitor. With IO-10 it is a configured decision; without a configured monitor the
    // value found in the register stays, as before.
    // IO-33: with a shutdown delay the driver switches CAN itself (superviseBatteryShutdown), CSC stays 00.
    uint8_t const shutdown
        = _config.battery.configure
              ? static_cast<uint8_t>(static_cast<uint8_t>(hardwareShutdown(_config.battery.canShutdown)) << 2U)
              : static_cast<uint8_t>(current & UJA113X_CSC_MASK);
    return static_cast<uint8_t>(shutdown | static_cast<uint8_t>(_config.can));
}

uint8_t Uja113x::failSafeValue() const
{
    uint8_t value = static_cast<uint8_t>(_config.resetCounter & UJA113X_RCC_MASK);
    if (_limpHome)
    {
        value = static_cast<uint8_t>(value | UJA113X_LHC_MASK);
    }
    if (_config.enPin.configure)
    {
        value = static_cast<uint8_t>(
            value | (static_cast<uint8_t>(_config.enPin.shutdown) << UJA113X_ENSC_SHIFT)
            | (static_cast<uint8_t>(_config.enPin.driver) << UJA113X_ENDC_SHIFT)
            | (static_cast<uint8_t>(_config.enPin.mode) << UJA113X_ENC_SHIFT));
    }
    else
    {
        value = static_cast<uint8_t>(
            value | (_startup.failSafeBefore & (UJA113X_ENSC_MASK | UJA113X_ENDC_MASK | UJA113X_ENC_MASK)));
    }
    return value;
}

Uja113x::Status Uja113x::updateFailSafe(uint8_t const mask, uint8_t const value)
{
    uint8_t current = 0U;
    Status status   = read(UJA113X_REG_FAIL_SAFE_CONTROL, current);
    if (status != Status::OK)
    {
        return status;
    }
    uint8_t const wanted
        = static_cast<uint8_t>((current & static_cast<uint8_t>(~mask)) | (value & mask));
    return writeVerified(UJA113X_REG_FAIL_SAFE_CONTROL, wanted, 0xFFU);
}

Uja113x::Status Uja113x::setLimpHome(bool const active)
{
    if (_state != State::NORMAL)
    {
        return Status::NOT_READY;
    }
    Status const status
        = updateFailSafe(UJA113X_LHC_MASK, active ? UJA113X_LHC_MASK : 0U);
    if (status == Status::OK)
    {
        _limpHome = active;
    }
    return status;
}

Uja113x::Status Uja113x::setEnMode(EnMode const mode)
{
    if (_state != State::NORMAL)
    {
        return Status::NOT_READY;
    }
    return updateFailSafe(
        UJA113X_ENC_MASK, static_cast<uint8_t>(static_cast<uint8_t>(mode) << UJA113X_ENC_SHIFT));
}

/* ---- battery monitor and HVIO configuration, part of the startup sequence (IO-10) ---- */

Uja113x::Status Uja113x::configureBatteryMonitor()
{
    BatteryMonitorConfig const& b = _config.battery;
    if (!b.configure)
    {
        return Status::OK;
    }
    // Thresholds before the sources that react to them (AH1501 2.1.7 order), so no shutdown fires on a
    // half-written configuration.
    Status status = writeVerified(
        UJA113X_REG_BM_UV_THRESHOLD, batteryThresholdCode(b.undervoltageMv, true), 0xFFU);
    if (status == Status::OK)
    {
        status = writeVerified(
            UJA113X_REG_BM_OV_THRESHOLD, batteryThresholdCode(b.overvoltageMv, false), 0xFFU);
    }
    if (status == Status::OK)
    {
        uint8_t const hysteresis = static_cast<uint8_t>(
            (batteryHysteresisCode(b.overvoltageHysteresisMv) << UJA113X_BMHOC_SHIFT)
            | batteryHysteresisCode(b.undervoltageHysteresisMv));
        status = writeVerified(UJA113X_REG_BM_HYSTERESIS, hysteresis, 0xFFU);
    }
    if (status == Status::OK)
    {
        status = writeVerified(
            UJA113X_REG_BM_SOURCE_CONTROL, static_cast<uint8_t>(b.source), UJA113X_BMSC_MASK);
    }
    return status;
}

Uja113x::Status Uja113x::readBatteryVoltages(BatteryVoltages& voltages)
{
    voltages = BatteryVoltages();
    if (_state != State::NORMAL)
    {
        return Status::NOT_READY;
    }
    struct Channel
    {
        uint8_t high;
        uint8_t low;
        uint16_t* target;
    };
    Channel const channels[] = {
        {UJA113X_REG_BM_VBAT_HIGH, UJA113X_REG_BM_VBAT_LOW, &voltages.batMv},
        {UJA113X_REG_BM_VBATSENSE_HIGH, UJA113X_REG_BM_VBATSENSE_LOW, &voltages.batSenseMv},
    };
    for (Channel const& channel : channels)
    {
        uint8_t high = 0U;
        uint8_t low  = 0U;
        Status status = read(channel.high, high); // reading the MSBs first latches the pair (BMBCS/BMSCS)
        if (status == Status::OK)
        {
            status = read(channel.low, low);
        }
        if (status != Status::OK)
        {
            return status;
        }
        uint16_t const code = static_cast<uint16_t>(
            (static_cast<uint16_t>(high) << 2U) | (low & UJA113X_BM_ADC_LSB_MASK));
        *channel.target = batteryAdcMv(code);
    }
    uint8_t supply = 0U;
    Status const status = read(UJA113X_REG_SUPPLY_STATUS, supply);
    if (status == Status::OK)
    {
        voltages.undervoltage = (supply & UJA113X_BMUVS_MASK) != 0U;
        voltages.overvoltage  = (supply & UJA113X_BMOVS_MASK) != 0U;
    }
    return status;
}

/* ---- delayed battery shutdown (IO-33) ----
 * The SBC reacts to a battery event within one conversion of its monitor (tc(ADC) 6 us) and has no filter. A load
 * switched on at the same supply pulls BAT below the undervoltage threshold for a moment (IO-13), which would drop CAN
 * at once. With shutdownDelayMs > 0 CSC/V2SC stay 00 and the driver switches CAN (CMC offline) and V2 (V2C off) from
 * the monitor status: off after the event has lasted the delay without a break, on again when the SBC has ended it
 * (after its release window). The status is read in every cyclic() call, so the delay has the resolution of that
 * call. */

SupplyShutdown Uja113x::hardwareShutdown(SupplyShutdown const configured) const
{
    return (_config.battery.shutdownDelayMs > 0U) ? SupplyShutdown::NONE : configured;
}

namespace
{
/** True if \p response names one of the events in \p event (BMUVS/BMOVS bits of the supply status). */
bool respondsTo(::sbc::SupplyShutdown const response, uint8_t const event)
{
    uint8_t const r = static_cast<uint8_t>(response);
    return (((event & UJA113X_BMUVS_MASK) != 0U) && ((r & UJA113X_SHUTDOWN_UV) != 0U))
           || (((event & UJA113X_BMOVS_MASK) != 0U) && ((r & UJA113X_SHUTDOWN_OV) != 0U));
}
} // namespace

Uja113x::Status Uja113x::applyCanShutOff(bool const off)
{
    uint8_t canControl = 0U;
    Status status      = read(UJA113X_REG_CAN_CONTROL, canControl);
    if (status == Status::OK)
    {
        uint8_t const value
            = off ? static_cast<uint8_t>(
                  static_cast<uint8_t>(canControl & static_cast<uint8_t>(~UJA113X_CMC_MASK)) | UJA113X_CMC_OFFLINE)
                  : canControlNormalValue(canControl);
        status = writeVerified(UJA113X_REG_CAN_CONTROL, value, CAN_CONTROL_MASK);
        pollTransceiver(); // the CAN system sees the transmitter off (or on) at once, as with writeRegister (COM-23)
    }
    return status;
}

Uja113x::Status Uja113x::applyV2ShutOff(bool const off)
{
    uint8_t regulator = 0U;
    Status status     = read(UJA113X_REG_REGULATOR_CONTROL, regulator);
    if (status == Status::OK)
    {
        uint8_t const v2 = off ? static_cast<uint8_t>(UJA113X_V2C_OFF) : static_cast<uint8_t>(_config.v2);
        uint8_t const value = static_cast<uint8_t>(
            static_cast<uint8_t>(regulator & static_cast<uint8_t>(~UJA113X_V2C_MASK))
            | static_cast<uint8_t>(v2 << UJA113X_V2C_SHIFT));
        status = writeVerified(UJA113X_REG_REGULATOR_CONTROL, value, REGULATOR_MASK);
    }
    return status;
}

void Uja113x::superviseBatteryShutdown(uint32_t const nowMs)
{
    BatteryMonitorConfig const& b = _config.battery;
    if ((!b.configure) || (b.shutdownDelayMs == 0U)
        || ((b.canShutdown == SupplyShutdown::NONE) && (b.v2Shutdown == SupplyShutdown::NONE)))
    {
        return;
    }
    uint8_t supply = 0U;
    if (read(UJA113X_REG_SUPPLY_STATUS, supply) != Status::OK)
    {
        return; // SPI error counted by read(); decided again in the next call
    }
    uint8_t const event = static_cast<uint8_t>(supply & (UJA113X_BMUVS_MASK | UJA113X_BMOVS_MASK));
    if (event != _batteryEvent)
    {
        _batteryEvent        = event;
        _batteryEventSinceMs = nowMs;
    }
    bool const lasting = (event != 0U) && ((nowMs - _batteryEventSinceMs) >= b.shutdownDelayMs);
    // Off only after the delay, on only when no event is left: a change between under- and overvoltage restarts
    // the delay but switches nothing back on.
    bool const canOff = (event != 0U) && (lasting ? respondsTo(b.canShutdown, event) : _canShutOff);
    bool const v2Off  = (event != 0U) && (lasting ? respondsTo(b.v2Shutdown, event) : _v2ShutOff);
    if ((canOff != _canShutOff) && (applyCanShutOff(canOff) == Status::OK))
    {
        _canShutOff = canOff;
        if (canOff)
        {
            ++_counters.supplyShutdowns;
        }
    }
    if ((v2Off != _v2ShutOff) && (applyV2ShutOff(v2Off) == Status::OK))
    {
        _v2ShutOff = v2Off;
        if (v2Off)
        {
            ++_counters.supplyShutdowns;
        }
    }
}

uint8_t Uja113x::configuredWakeInputs(Uja113xConfig const& config)
{
    uint8_t inputs = 0U;
    for (uint8_t bank = 0U; bank < UJA113X_HVIO_BANKS; ++bank)
    {
        if (!config.hvio[bank].configure)
        {
            continue;
        }
        for (uint8_t i = 0U; i < UJA113X_HVIO_PER_BANK; ++i)
        {
            HvioConfig const& pin = config.hvio[bank].pin[i];
            if (hvioIsInput(pin) && (pin.activation != HvioActivation::OFF))
            {
                inputs = static_cast<uint8_t>(inputs | (1U << ((bank * UJA113X_HVIO_PER_BANK) + i)));
            }
        }
    }
    return inputs;
}

uint8_t Uja113x::normalWakeIrqEnable(uint8_t const bank) const
{
    if (!_config.hvio[bank].configure)
    {
        return 0U; // AH1501 2.2.3: nothing armed in Normal mode
    }
    uint8_t value = 0U;
    for (uint8_t i = 0U; i < UJA113X_HVIO_PER_BANK; ++i)
    {
        HvioConfig const& pin = _config.hvio[bank].pin[i];
        uint8_t const shift   = static_cast<uint8_t>(2U * i);
        if (hvioIsInput(pin) && pin.wakeOnRisingEdge)
        {
            value = static_cast<uint8_t>(value | (1U << (shift + UJA113X_HVIO_RISING_SHIFT)));
        }
        if (hvioIsInput(pin) && pin.wakeOnFallingEdge)
        {
            value = static_cast<uint8_t>(value | (1U << (shift + UJA113X_HVIO_FALLING_SHIFT)));
        }
    }
    return value;
}

Uja113x::Status Uja113x::configureHvioBank(uint8_t const bank)
{
    HvioBankConfig const& b = _config.hvio[bank];
    uint8_t shortCircuit    = 0U;
    uint8_t openLoad        = 0U;
    uint8_t failIrq         = 0U;
    for (uint8_t i = 0U; i < UJA113X_HVIO_PER_BANK; ++i)
    {
        HvioConfig const& pin = b.pin[i];
        uint8_t const shift   = static_cast<uint8_t>(2U * i);
        shortCircuit          = static_cast<uint8_t>(
            shortCircuit | (shortCircuitThresholdCode(pin.shortCircuitThresholdMa) << shift));
        openLoad = static_cast<uint8_t>(
            openLoad | (openLoadThresholdCode(pin.openLoadThresholdMa) << shift));
        if (hvioIsDriver(pin) && pin.openLoadInterrupt)
        {
            failIrq = static_cast<uint8_t>(failIrq | (1U << (shift + UJA113X_HVIO_OPEN_LOAD_SHIFT)));
        }
        if (hvioIsDriver(pin) && pin.shortCircuitInterrupt)
        {
            failIrq = static_cast<uint8_t>(failIrq | (1U << (shift + UJA113X_HVIO_SHORT_CIRCUIT_SHIFT)));
        }
    }
    uint8_t const wakeIrq = normalWakeIrqEnable(bank);
    // Thresholds and the wake-up level first, then the pins themselves: a driver must never switch on
    // with the detection thresholds of the previous configuration.
    Status status = writeVerified(
        hvioRegister(bank, UJA113X_REG_BANK0_WAKE_THRESHOLD), static_cast<uint8_t>(b.wakeThreshold),
        UJA113X_WTC_MASK);
    if (status == Status::OK)
    {
        status = writeVerified(hvioRegister(bank, UJA113X_REG_BANK0_SC_THRESHOLD), shortCircuit, 0xFFU);
    }
    if (status == Status::OK)
    {
        status = writeVerified(hvioRegister(bank, UJA113X_REG_BANK0_OL_THRESHOLD), openLoad, 0xFFU);
    }
    for (uint8_t i = 0U; (status == Status::OK) && (i < UJA113X_HVIO_PER_BANK); ++i)
    {
        status = writeVerified(
            static_cast<uint8_t>(hvioRegister(bank, UJA113X_REG_BANK0_HVIO_CONTROL) + i),
            hvioControlValue(b.pin[i]), 0xFFU);
    }
    if (status == Status::OK)
    {
        status = writeVerified(hvioRegister(bank, UJA113X_REG_BANK0_FAIL_IRQ_ENABLE), failIrq, 0xFFU);
    }
    if (status == Status::OK)
    {
        status = writeVerified(hvioRegister(bank, UJA113X_REG_BANK0_WAKE_IRQ_ENABLE), wakeIrq, 0xFFU);
    }
    return status;
}

Uja113x::Status Uja113x::configureHvio()
{
    Status status = Status::OK;
    for (uint8_t bank = 0U; (status == Status::OK) && (bank < _hvioBanks); ++bank)
    {
        if (_config.hvio[bank].configure)
        {
            status = configureHvioBank(bank);
        }
        else
        {
            // Bank not configured: leave its HVIO function alone, but make sure nothing is armed as a
            // wake-up source in Normal mode (AH1501 2.2.3); enterSleep() arms what it needs.
            status = writeVerified(hvioRegister(bank, UJA113X_REG_BANK0_WAKE_IRQ_ENABLE), 0U, 0xFFU);
        }
    }
    return status;
}

Uja113x::Status Uja113x::readHvioStatus(uint8_t const bank, HvioBankStatus& status)
{
    status = HvioBankStatus();
    if (_state != State::NORMAL)
    {
        return Status::NOT_READY;
    }
    if (bank >= _hvioBanks)
    {
        return Status::NOT_ALLOWED;
    }
    Status result = read(hvioRegister(bank, UJA113X_REG_BANK0_WAKE_LEVEL), status.wakeLevels);
    if (result == Status::OK)
    {
        result = read(hvioRegister(bank, UJA113X_REG_BANK0_DRIVER_STATUS), status.driverStatus);
    }
    if (result == Status::OK)
    {
        status.wakeLevels = static_cast<uint8_t>(status.wakeLevels & 0x0FU); // bits 7:4 reserved
    }
    return result;
}

Uja113x::Status Uja113x::setHvioActivation(uint8_t const index, HvioActivation const activation)
{
    if (_state != State::NORMAL)
    {
        return Status::NOT_READY;
    }
    uint8_t const bank = static_cast<uint8_t>(index / UJA113X_HVIO_PER_BANK);
    if ((index >= UJA113X_HVIO_COUNT) || (bank >= _hvioBanks) || !_config.hvio[bank].configure)
    {
        return Status::NOT_ALLOWED;
    }
    HvioConfig const& pin = _config.hvio[bank].pin[index % UJA113X_HVIO_PER_BANK];
    if (pin.function == HvioFunction::OFF)
    {
        return Status::NOT_ALLOWED; // an unconfigured HVIO ignores every other control bit
    }
    HvioConfig wanted = pin;
    wanted.activation = activation;
    return writeVerified(
        static_cast<uint8_t>(hvioRegister(bank, UJA113X_REG_BANK0_HVIO_CONTROL)
                             + (index % UJA113X_HVIO_PER_BANK)),
        hvioControlValue(wanted), 0xFFU);
}

Uja113x::Status Uja113x::clearHvioFailure(uint8_t const index)
{
    // Data sheet 7.10.3: the driver status only goes back to "ok" when the activation control is 000.
    Status status = setHvioActivation(index, HvioActivation::OFF);
    if (status != Status::OK)
    {
        return status;
    }
    uint8_t const bank = static_cast<uint8_t>(index / UJA113X_HVIO_PER_BANK);
    return setHvioActivation(index, _config.hvio[bank].pin[index % UJA113X_HVIO_PER_BANK].activation);
}

uint8_t Uja113x::wakeIrqEnableValue(uint8_t const bank, uint8_t const wakeInputs) const
{
    uint8_t value = 0U;
    for (uint8_t i = 0U; i < UJA113X_HVIO_PER_BANK; ++i)
    {
        uint8_t const index = static_cast<uint8_t>((bank * UJA113X_HVIO_PER_BANK) + i);
        if ((wakeInputs & (1U << index)) == 0U)
        {
            continue;
        }
        HvioConfig const& pin = _config.hvio[bank].pin[i];
        bool const rising  = pin.wakeOnRisingEdge;
        bool const falling = pin.wakeOnFallingEdge;
        // No edge configured (bank not configured at all, e.g. a plain WAKE input): arm both, a Sleep
        // source that never fires would leave the ECU asleep.
        uint8_t const shift = static_cast<uint8_t>(2U * i);
        if (rising || !falling)
        {
            value = static_cast<uint8_t>(value | (1U << (shift + UJA113X_HVIO_RISING_SHIFT)));
        }
        if (falling || !rising)
        {
            value = static_cast<uint8_t>(value | (1U << (shift + UJA113X_HVIO_FALLING_SHIFT)));
        }
    }
    return value;
}

bool Uja113x::isValidCanWakeupFrame(CanWakeupFrame const& frame)
{
    uint8_t code         = 0U;
    uint32_t const width = frame.extendedId ? UJA113X_PN_ID_FIELD_MASK : STANDARD_ID_MASK;
    bool valid           = dataRateCode(frame.bitrate, code) && ((frame.id & ~width) == 0U)
                 && ((frame.idCompareMask & ~width) == 0U) && (frame.dlc <= UJA113X_PN_DATA_MASK_COUNT);
    if (valid && frame.evaluateData && (frame.dlc > 0U))
    {
        // The SBC wakes on a pair of 1 bits in data field and data mask: an empty mask never wakes.
        uint8_t any = 0U;
        for (uint8_t i = 0U; i < frame.dlc; ++i)
        {
            any = static_cast<uint8_t>(any | frame.dataMask[i]);
        }
        valid = (any != 0U);
    }
    return valid;
}

Uja113x::Status Uja113x::setCanWakeupFrame(CanWakeupFrame const* const frame)
{
    if ((frame != nullptr) && !isValidCanWakeupFrame(*frame))
    {
        return Status::NOT_ALLOWED;
    }
    _canWakeupFrame = frame;
    return Status::OK;
}

Uja113x::Status Uja113x::armPartialNetworking()
{
    if (_state != State::NORMAL)
    {
        return Status::NOT_READY;
    }
    if ((_canWakeupFrame == nullptr) || !supportsPartialNetworking(_startup.identification))
    {
        return Status::NOT_ALLOWED;
    }
    return configurePartialNetworking(*_canWakeupFrame, false);
}

Uja113x::Status Uja113x::checkPartialNetworkingStatus()
{
    // CPNS (0x22) mirrors PNCOK: 0 means the SBC dropped the configuration and would fall back to the standard
    // wake-up (any bus activity), so the Sleep must not follow.
    uint8_t transceiverStatus = 0U;
    Status status             = read(UJA113X_REG_TRANSCEIVER_STATUS, transceiverStatus);
    if ((status == Status::OK) && ((transceiverStatus & UJA113X_CPNS_MASK) == 0U))
    {
        ++_counters.verifyErrors;
        status = Status::VERIFY_ERROR;
    }
    return status;
}

Uja113x::Status Uja113x::configurePartialNetworking(CanWakeupFrame const& frame, bool const offline)
{
    uint8_t dataRate = 0U;
    (void)dataRateCode(frame.bitrate, dataRate); // validated in setCanWakeupFrame()

    // Identifier and mask as 29 bit fields; a standard identifier sits in the upper 11 bits.
    uint32_t const width    = frame.extendedId ? UJA113X_PN_ID_FIELD_MASK : STANDARD_ID_MASK;
    uint8_t const shift     = frame.extendedId ? 0U : UJA113X_PN_ID_STD_SHIFT;
    uint32_t const id       = (frame.id & width) << shift;
    uint32_t const dontCare = (~frame.idCompareMask & width) << shift;
    uint8_t const dlc       = frame.evaluateData ? frame.dlc : 0U;

    struct Setting
    {
        uint8_t address;
        uint8_t value;
        uint8_t mask;
    };
    // Every write to these registers clears PNCOK in the SBC, so CAN control comes last.
    Setting settings[2U + (2U * 4U) + UJA113X_PN_DATA_MASK_COUNT] = {};
    uint8_t n     = 0U;
    settings[n++] = {UJA113X_REG_PN_DATA_RATE, dataRate, UJA113X_CDR_MASK};
    for (uint8_t i = 0U; i < 4U; ++i)
    {
        uint8_t const bits = (i == 3U) ? UJA113X_PN_ID_TOP_MASK : 0xFFU;
        settings[n++]      = {static_cast<uint8_t>(UJA113X_REG_PN_ID_0 + i),
                              static_cast<uint8_t>((id >> (8U * i)) & bits), bits};
        settings[n++]      = {static_cast<uint8_t>(UJA113X_REG_PN_ID_MASK_0 + i),
                              static_cast<uint8_t>((dontCare >> (8U * i)) & bits), bits};
    }
    settings[n++] = {UJA113X_REG_PN_FRAME_CONTROL,
                     static_cast<uint8_t>((frame.extendedId ? UJA113X_IDE_MASK : 0U)
                                          | (frame.evaluateData ? UJA113X_PNDM_MASK : 0U) | dlc),
                     static_cast<uint8_t>(UJA113X_IDE_MASK | UJA113X_PNDM_MASK | UJA113X_PN_DLC_MASK)};
    // Data bytes are right aligned: with DLC n, data byte 0 is compared against data mask 8 - n.
    uint8_t const first = static_cast<uint8_t>(UJA113X_PN_DATA_MASK_COUNT - dlc);
    for (uint8_t k = 0U; k < UJA113X_PN_DATA_MASK_COUNT; ++k)
    {
        uint8_t const value = (k >= first) ? frame.dataMask[k - first] : 0U;
        settings[n++]       = {static_cast<uint8_t>(UJA113X_REG_PN_DATA_MASK_0 + k), value, 0xFFU};
    }

    Status status = Status::OK;
    for (uint8_t i = 0U; (i < n) && (status == Status::OK); ++i)
    {
        status = writeVerified(settings[i].address, settings[i].value, settings[i].mask);
    }

    uint8_t canControl = 0U;
    if (status == Status::OK)
    {
        status = read(UJA113X_REG_CAN_CONTROL, canControl);
    }
    if (status == Status::OK)
    {
        // COM-39: for the Sleep the transceiver goes to CAN Offline (CMC = 00) in the same write. The data sheet
        // (7.9.1.3, last paragraph) ties the monitoring of the bus for wake-up frames to Offline/Offline Bias
        // "(CMC != 00)" - the standard wake-up worked with CMC = 01 on the board, the selective one did not
        // decode at all (no wake-up frame, no PNFDEI). CSC stays as configured.
        uint8_t const mode  = offline ? static_cast<uint8_t>(UJA113X_CMC_OFFLINE)
                                      : static_cast<uint8_t>(canControlNormalValue(canControl) & UJA113X_CMC_MASK);
        uint8_t const value = static_cast<uint8_t>(
            static_cast<uint32_t>(canControlNormalValue(canControl) & static_cast<uint8_t>(~UJA113X_CMC_MASK)) | mode
            | UJA113X_CPNC_MASK | UJA113X_PNCOK_MASK | (frame.fdPassive ? UJA113X_CFDC_MASK : 0U));
        status = writeVerified(
            UJA113X_REG_CAN_CONTROL, value, static_cast<uint8_t>(CAN_CONTROL_MASK | UJA113X_PNCOK_MASK));
    }
    // CPNS confirms that the SBC accepted the configuration; without it the SBC wakes on any bus activity.
    if (status == Status::OK)
    {
        status = checkPartialNetworkingStatus();
    }
    return status;
}

uint8_t Uja113x::watchdogControlValue() const
{
    return static_cast<uint8_t>(
        (static_cast<uint8_t>(_config.watchdogMode) << UJA113X_WMC_SHIFT)
        | static_cast<uint8_t>(_config.watchdogPeriod));
}

Uja113x::Status Uja113x::triggerWatchdog(uint32_t const nowMs, bool const inNormalMode)
{
    Status status = write(UJA113X_REG_WATCHDOG_CONTROL, watchdogControlValue());
    // Data sheet 7.2.5 case B: a timeout trigger right at 50 % of the period may act like an ignored one,
    // so send it twice. Not in Window mode, where the second one could hit the closed window.
    bool const window = inNormalMode && (_config.watchdogMode == WatchdogMode::WINDOW);
    if ((status == Status::OK) && !window)
    {
        status = write(UJA113X_REG_WATCHDOG_CONTROL, watchdogControlValue());
    }
    if (status == Status::OK)
    {
        ++_counters.watchdogTriggers;
        _lastTriggerMs = nowMs;
    }
    return status;
}

Uja113x::Status Uja113x::configureWatchdog(uint32_t const nowMs)
{
    uint8_t current = 0U;
    Status status   = read(UJA113X_REG_WATCHDOG_CONTROL, current);
    if (status != Status::OK)
    {
        return status;
    }
    uint8_t const desired = watchdogControlValue();
    bool const change     = (current & WATCHDOG_MASK) != desired;
    if (!change && !_watchdogActive)
    {
        return Status::OK;
    }

    // Changing WMC/NWP in Normal mode resets the system (data sheet 7.2, 7.3.3). Synchronizing in Standby,
    // where the watchdog acts as timeout watchdog, also avoids hitting a closed window of unknown
    // phase after an MCU-only reset.
    uint8_t mode = 0U;
    status       = read(UJA113X_REG_MODE_CONTROL, mode);
    if ((status == Status::OK) && ((mode & UJA113X_MC_MASK) != UJA113X_MC_STANDBY))
    {
        status = writeVerified(UJA113X_REG_MODE_CONTROL, UJA113X_MC_STANDBY, UJA113X_MC_MASK);
    }
    // Written twice (required when the period gets shorter), then a spurious WDI is cleared.
    if (status == Status::OK)
    {
        status = write(UJA113X_REG_WATCHDOG_CONTROL, desired);
    }
    if (status == Status::OK)
    {
        status = writeVerified(UJA113X_REG_WATCHDOG_CONTROL, desired, WATCHDOG_MASK);
    }
    if ((status == Status::OK) && change)
    {
        status = write(UJA113X_REG_SYSTEM_IRQ_STATUS, UJA113X_WDI_MASK);
    }
    if (status == Status::OK)
    {
        ++_counters.watchdogTriggers;
        _lastTriggerMs = nowMs;
    }
    return status;
}

Uja113x::Status Uja113x::enterNormal()
{
    Status status = write(UJA113X_REG_MODE_CONTROL, UJA113X_MC_NORMAL);
    uint8_t mode  = 0U;
    if (status == Status::OK)
    {
        status = read(UJA113X_REG_MODE_CONTROL, mode);
    }
    if ((status == Status::OK) && ((mode & UJA113X_MC_MASK) != UJA113X_MC_NORMAL))
    {
        ++_counters.verifyErrors;
        status = Status::VERIFY_ERROR;
    }
    return status;
}

Uja113x::Status Uja113x::runStartup(uint32_t const nowMs)
{
    StartupInfo& s = _startup;
    s              = StartupInfo();
    ++_counters.startupAttempts;
    _canReady = false;
    // IO-33: the startup writes CAN and V2 as configured, a delayed battery shutdown starts over
    _batteryEvent = 0U;
    _canShutOff   = false;
    _v2ShutOff    = false;

    // AH1501 2.1.3: only talk to the expected device, every write below depends on the register map.
    Status status = read(UJA113X_REG_IDENTIFICATION_0, s.identification);
    if (status != Status::OK)
    {
        return status;
    }
    if (s.identification != _config.identification)
    {
        return Status::WRONG_DEVICE;
    }
    _hvioBanks = hvioBankCount(s.identification);
    _limpHome  = _config.limpHomeAtStartup;

    // Reads first: MTPNV (2.1.4, check only), watchdog status, reset source (2.1.6), mode, fail-safe.
    uint8_t global = 0U;
    Status const reads[] = {
        read(UJA113X_REG_MTPNV_STATUS, s.mtpnvStatus),
        read(UJA113X_REG_STARTUP_CONTROL, s.startupControl),
        read(UJA113X_REG_SBC_CONFIG_CONTROL, s.sbcConfig),
        read(UJA113X_REG_WATCHDOG_STATUS, s.watchdogStatus),
        read(UJA113X_REG_MAIN_STATUS, s.mainStatus),
        read(UJA113X_REG_MODE_CONTROL, s.modeBefore),
        read(UJA113X_REG_FAIL_SAFE_CONTROL, s.failSafeBefore),
        read(UJA113X_REG_GLOBAL_IRQ_STATUS, global),
    };
    for (Status const r : reads)
    {
        if (r != Status::OK)
        {
            return r;
        }
    }
    s.mtpnvMismatch = ((s.mtpnvStatus & UJA113X_NVMPS_MASK) != 0U)
                      || (s.startupControl != _config.startupControl)
                      || (s.sbcConfig != _config.sbcConfig);
    s.limpHomeWasActive = (s.failSafeBefore & UJA113X_LHC_MASK) != 0U;

    // Forced Normal mode: watchdog disabled. Development mode: off as long as WMC is autonomous.
    // Otherwise autonomous still means timeout watchdog in Normal mode (data sheet 7.2.4, Table 8).
    _watchdogActive = ((s.watchdogStatus & UJA113X_FNMS_MASK) == 0U)
                      && ((_config.watchdogMode != WatchdogMode::AUTONOMOUS)
                          || ((s.watchdogStatus & UJA113X_SDMS_MASK) == 0U));

    // A running watchdog must be served long before the rest is done. Not if it may be a window
    // watchdog in Normal mode with unknown phase; configureWatchdog() synchronizes via Standby.
    uint8_t currentWatchdog = 0U;
    status                  = read(UJA113X_REG_WATCHDOG_CONTROL, currentWatchdog);
    if (status != Status::OK)
    {
        return status;
    }
    bool const unknownWindow
        = (((currentWatchdog & UJA113X_WMC_MASK) >> UJA113X_WMC_SHIFT) == UJA113X_WMC_WINDOW)
          && ((s.modeBefore & UJA113X_MC_MASK) == UJA113X_MC_NORMAL);
    if (((s.watchdogStatus & UJA113X_WDS_MASK) != 0U) && !unknownWindow)
    {
        status = write(UJA113X_REG_WATCHDOG_CONTROL, currentWatchdog);
        if (status != Status::OK)
        {
            return status;
        }
    }

    // 2.1.7: clear everything pending, otherwise INTN stays low and Sleep is impossible.
    status = readAndClearInterrupts(global, s.interrupts);
    if (status != Status::OK)
    {
        return status;
    }

    // SBC initialization. The regulator goes first so that V2 stays on if the watchdog
    // configuration below needs a detour through Standby.
    uint8_t regulator = 0U;
    uint8_t canControl = 0U;
    status             = read(UJA113X_REG_REGULATOR_CONTROL, regulator);
    if (status == Status::OK)
    {
        // V2SC only where the project armed the battery monitor; otherwise the register value stays (IO-10).
        uint8_t const keepMask = _config.battery.configure
                                     ? static_cast<uint8_t>(~(UJA113X_V2C_MASK | UJA113X_V2SC_MASK))
                                     : static_cast<uint8_t>(~UJA113X_V2C_MASK);
        uint8_t value = static_cast<uint8_t>(
            (regulator & keepMask) | (static_cast<uint8_t>(_config.v2) << UJA113X_V2C_SHIFT));
        if (_config.battery.configure)
        {
            // IO-33: with a shutdown delay the driver switches V2 itself (superviseBatteryShutdown), V2SC stays 00.
            SupplyShutdown const v2Shutdown = hardwareShutdown(_config.battery.v2Shutdown);
            value = static_cast<uint8_t>(
                value | static_cast<uint8_t>(static_cast<uint8_t>(v2Shutdown) << UJA113X_V2SC_SHIFT));
        }
        status = writeVerified(UJA113X_REG_REGULATOR_CONTROL, value, REGULATOR_MASK);
    }
    if (status == Status::OK)
    {
        status = configureBatteryMonitor(); // thresholds before anything may react to them (IO-10)
    }
    if (status == Status::OK)
    {
        status = read(UJA113X_REG_CAN_CONTROL, canControl);
    }
    if (status == Status::OK)
    {
        status = writeVerified(UJA113X_REG_CAN_CONTROL, canControlNormalValue(canControl), CAN_CONTROL_MASK);
    }
    if (status == Status::OK)
    {
        status = writeVerified(UJA113X_REG_SYSTEM_IRQ_ENABLE, _config.systemIrqEnable, SYSTEM_IRQ_MASK);
    }
    if (status == Status::OK)
    {
        status = writeVerified(UJA113X_REG_SUPPLY_IRQ_ENABLE, _config.supplyIrqEnable, SUPPLY_IRQ_MASK);
    }
    if (status == Status::OK)
    {
        // 2.2.3: no wake-up sources in Normal mode, only failure detection.
        status = writeVerified(
            UJA113X_REG_TRANSCEIVER_IRQ_ENABLE,
            static_cast<uint8_t>(_config.transceiverIrqEnable & static_cast<uint8_t>(~UJA113X_CWI_MASK)),
            TRX_IRQ_MASK);
    }
    if (status == Status::OK)
    {
        status = configureHvio(); // 2.1.7: HVIO function, thresholds and interrupts (IO-10)
    }
    if (status == Status::OK)
    {
        status = configureWatchdog(nowMs); // 2.1.5
    }
    if (status == Status::OK)
    {
        // 2.1.8: reset counter back to its start value, EN pin and LIMP as configured. A limp home left
        // over from forced sleep preparation is released unless the project asks for it (IO-10).
        status = writeVerified(UJA113X_REG_FAIL_SAFE_CONTROL, failSafeValue(), 0xFFU);
    }
    if (status == Status::OK)
    {
        status = enterNormal(); // 2.1.9
    }
    return status;
}

Uja113x::Status Uja113x::startup(uint32_t const nowMs)
{
    _lastStartupAttemptMs = nowMs;
    Status const status   = runStartup(nowMs);
    if (status == Status::OK)
    {
        _state                 = State::NORMAL;
        _lastTransceiverPollMs = nowMs;
        _lastModeCheckMs       = nowMs;
        // IO-68: the startup read 0x60 and cleared what was pending (INTN1 released)
        _lastInterruptReadMs  = nowMs;
        _lastInterruptClearMs = nowMs;
        pollTransceiver();
    }
    else
    {
        _state = State::FAILED;
    }
    return status;
}

void Uja113x::serviceWatchdog(uint32_t const nowMs)
{
    if (!_watchdogActive)
    {
        return;
    }
    uint32_t const periodMs = watchdogPeriodMs(_config.watchdogPeriod);
    // Window: open from 50 % (+10 % oscillator tolerance) to 100 % (-10 %), aim at 70 %.
    uint32_t const dueMs = (_config.watchdogMode == WatchdogMode::WINDOW) ? ((periodMs * 7U) / 10U)
                                                                           : (periodMs / 2U);
    if ((nowMs - _lastTriggerMs) >= dueMs)
    {
        (void)triggerWatchdog(nowMs, true);
    }
}

void Uja113x::pollTransceiver()
{
    uint8_t status = 0U;
    if (read(UJA113X_REG_TRANSCEIVER_STATUS, status) != Status::OK)
    {
        return;
    }
    _transceiverStatus = status;
    bool const ready   = ((status & UJA113X_CTS_MASK) != 0U)
                       && ((status & (UJA113X_VCS_MASK | UJA113X_CFS_MASK)) == 0U);
    if (_canReady && !ready)
    {
        ++_counters.canNotReady;
    }
    _canReady = ready;
}

bool Uja113x::isTransmitterRequested() const
{
    return (_config.can == CanMode::ACTIVE) || (_config.can == CanMode::ACTIVE_NO_UV);
}

bool Uja113x::interruptReadDue(uint32_t const nowMs, InterruptLine const line, bool& fallback)
{
    fallback = false;
    if ((line == InterruptLine::UNKNOWN) || _lineFault)
    {
        ++_counters.interruptPolls;
        return true;
    }
    if (line == InterruptLine::ACTIVE)
    {
        ++_counters.interruptLineReads;
        return true;
    }
    if ((nowMs - _lastInterruptReadMs) >= INTERRUPT_FALLBACK_POLL_MS)
    {
        ++_counters.interruptFallbackReads;
        fallback = true;
        return true;
    }
    ++_counters.interruptReadsSkipped;
    return false;
}

bool Uja113x::handleInterrupts(uint32_t const nowMs, InterruptLine const line)
{
    bool fallback = false;
    if (!interruptReadDue(nowMs, line, fallback))
    {
        return false;
    }
    uint8_t global = 0U;
    if (read(UJA113X_REG_GLOBAL_IRQ_STATUS, global) != Status::OK)
    {
        return false;
    }
    _lastInterruptReadMs = nowMs;
    // IO-68: compare with the line. Within tto(int) after a clear INTN1 may be high with an interrupt pending,
    // so nothing is concluded then.
    bool const settled = (nowMs - _lastInterruptClearMs) > INTERRUPT_RELEASE_MS;
    if (fallback && settled)
    {
        if (global != 0U)
        {
            ++_counters.interruptLineMisses;
            if (_lineMissesInRow < INTERRUPT_LINE_MISS_LIMIT)
            {
                ++_lineMissesInRow;
            }
            _lineFault = _lineFault || (_lineMissesInRow >= INTERRUPT_LINE_MISS_LIMIT);
        }
        else
        {
            _lineMissesInRow = 0U;
        }
    }
    else if ((line == InterruptLine::ACTIVE) && settled)
    {
        if (global == 0U)
        {
            ++_counters.interruptLineIdle; // stuck low or no pull-up: costs a read per call, misses nothing
        }
        else
        {
            _lineMissesInRow = 0U; // the line showed this one
        }
    }
    else
    {
        // polled without line information, or too soon after a clear to compare
    }
    if (global == 0U)
    {
        return false;
    }
    InterruptFlags flags{};
    (void)readAndClearInterrupts(global, flags);
    _lastInterruptClearMs = nowMs;
    if (flags.any())
    {
        ++_counters.interruptEvents;
        _events.system      = static_cast<uint8_t>(_events.system | flags.system);
        _events.supply      = static_cast<uint8_t>(_events.supply | flags.supply);
        _events.transceiver = static_cast<uint8_t>(_events.transceiver | flags.transceiver);
        _events.wake        = static_cast<uint8_t>(_events.wake | flags.wake);
        _events.failure     = static_cast<uint8_t>(_events.failure | flags.failure);
        // IO-68: bank 1 as well - a wake-up from Standby through HVIO5..8 must reach the caller
        _events.wakeBank1    = static_cast<uint8_t>(_events.wakeBank1 | flags.wakeBank1);
        _events.failureBank1 = static_cast<uint8_t>(_events.failureBank1 | flags.failureBank1);
    }
    if ((flags.transceiver != 0U) && (_state == State::NORMAL))
    {
        pollTransceiver(); // AH1501 Fig 16: CAN failure -> check VCS/CFS/CTS right away
        _lastTransceiverPollMs = nowMs;
        return true;
    }
    return false;
}

void Uja113x::superviseMode(uint32_t const nowMs, uint8_t const expected)
{
    if ((nowMs - _lastModeCheckMs) < MODE_SUPERVISION_MS)
    {
        return;
    }
    _lastModeCheckMs = nowMs;
    uint8_t mode     = 0U;
    if ((read(UJA113X_REG_MODE_CONTROL, mode) == Status::OK) && ((mode & UJA113X_MC_MASK) != expected))
    {
        // The SBC went through a reset of its own (registers at defaults, V2 possibly off) while
        // the MCU kept running, e.g. with SBC_RESET not connected to the MCU: initialize again.
        ++_counters.modeLost;
        _canReady = false;
        (void)startup(nowMs);
    }
}

void Uja113x::cyclicStandby(uint32_t const nowMs, InterruptLine const line)
{
    if (_standbyWatchdogService)
    {
        // Timeout behaviour in Standby (data sheet 7.2.3, Table 8): trigger with the value written for Standby, twice
        uint32_t const periodMs
            = watchdogPeriodMs(static_cast<WatchdogPeriod>(_standbyWatchdog & UJA113X_NWP_MASK));
        if ((nowMs - _lastTriggerMs) >= (periodMs / 2U))
        {
            Status status = write(UJA113X_REG_WATCHDOG_CONTROL, _standbyWatchdog);
            if (status == Status::OK)
            {
                status = write(UJA113X_REG_WATCHDOG_CONTROL, _standbyWatchdog);
            }
            if (status == Status::OK)
            {
                ++_counters.watchdogTriggers;
                _lastTriggerMs = nowMs;
            }
        }
    }
    (void)handleInterrupts(nowMs, line); // the wake-up interrupts, collected for takeInterruptEvents()
    if ((nowMs - _lastModeCheckMs) < MODE_SUPERVISION_MS)
    {
        return;
    }
    _lastModeCheckMs = nowMs;
    // A reset of the SBC in Standby ends in Standby again (Reset -> Standby, data sheet 7.3), so the mode alone does
    // not show it. The registers are back at their defaults then: the wake-up sources armed by enterStandby() are
    // the witness - and exactly what must not get lost while the MCU waits for INTN1.
    uint8_t const trxNormal  = static_cast<uint8_t>(_config.transceiverIrqEnable
                                                    & static_cast<uint8_t>(~UJA113X_CWI_MASK));
    uint8_t const trxStandby = static_cast<uint8_t>(
        _standbyWakeup.can ? (trxNormal | UJA113X_CWI_MASK) : trxNormal);
    uint8_t value = 0U;
    Status status = read(UJA113X_REG_MODE_CONTROL, value);
    bool intact   = (status != Status::OK) || ((value & UJA113X_MC_MASK) == UJA113X_MC_STANDBY);
    if (intact && (status == Status::OK))
    {
        status = read(UJA113X_REG_TRANSCEIVER_IRQ_ENABLE, value);
        intact = (status != Status::OK) || ((value & TRX_IRQ_MASK) == (trxStandby & TRX_IRQ_MASK));
    }
    for (uint8_t bank = 0U; intact && (status == Status::OK) && (bank < _hvioBanks); ++bank)
    {
        uint8_t const armed = wakeIrqEnableValue(bank, _standbyWakeup.wakeInputs);
        if (armed != 0U)
        {
            status = read(hvioRegister(bank, UJA113X_REG_BANK0_WAKE_IRQ_ENABLE), value);
            intact = (status != Status::OK) || (value == armed);
        }
    }
    if (intact && (status == Status::OK) && _standbyWakeup.cyclic)
    {
        status = read(UJA113X_REG_WATCHDOG_CONTROL, value);
        intact = (status != Status::OK) || ((value & WATCHDOG_MASK) == (_standbyWatchdog & WATCHDOG_MASK));
    }
    if (!intact)
    {
        ++_counters.modeLost;
        _canReady = false;
        (void)startup(nowMs); // back to Normal with the configuration; the caller sees State::NORMAL
    }
}

void Uja113x::cyclic(uint32_t const nowMs, InterruptLine const line)
{
    if (_state == State::STANDBY)
    {
        cyclicStandby(nowMs, line);
        return;
    }
    if (_state != State::NORMAL)
    {
        if ((_state == State::FAILED) && ((nowMs - _lastStartupAttemptMs) >= STARTUP_RETRY_MS))
        {
            (void)startup(nowMs);
        }
        return;
    }

    serviceWatchdog(nowMs);
    bool const polledByInterrupt = handleInterrupts(nowMs, line);
    superviseBatteryShutdown(nowMs); // IO-33, only with shutdownDelayMs > 0 and a switch-off configured

    // COM-08: with the transmitter switched on (CAN mode active) the status is read in every call, so a lost
    // VCAN (VCS) or a transmitter that went off (CTS) is seen after one cycle of the caller instead of up to
    // TRANSCEIVER_POLL_MS later. Only the CAN failure interrupt (CFI) triggers an immediate read above; AH1501
    // (Fig 16/17) does not promise that every change of VCS or CTS raises it and recommends polling the
    // transceiver status. Cost: one SPI frame per call. Offline or listen-only nobody may send, the slow poll stays.
    if (polledByInterrupt)
    {
        // status read in this call already
    }
    else if (isTransmitterRequested() || ((nowMs - _lastTransceiverPollMs) >= TRANSCEIVER_POLL_MS))
    {
        _lastTransceiverPollMs = nowMs;
        pollTransceiver();
    }

    superviseMode(nowMs, UJA113X_MC_NORMAL);
}

Uja113x::Status Uja113x::enterSleep(uint32_t const nowMs, SleepWakeup const& wakeup)
{
    if (_state != State::NORMAL)
    {
        return Status::NOT_READY;
    }
    // At least one wake-up source, otherwise the SBC resets instead of sleeping (SYS-02: the selection comes
    // from the project, the caller has already dropped what this board cannot serve).
    if (!wakeup.any())
    {
        return Status::NOT_ALLOWED;
    }
    // Selective wake-up (COM-07) only on a device with partial networking, otherwise the SBC would silently
    // wake on any bus activity.
    bool const selective = wakeup.can && (_canWakeupFrame != nullptr);
    if (selective && !supportsPartialNetworking(_startup.identification))
    {
        return Status::NOT_ALLOWED;
    }
    // An HVIO input of a bank the device does not have is refused instead of written to a reserved register -
    // before anything is written (COM-39: it used to be checked after partial networking and CWIE were armed).
    if ((wakeup.wakeInputs >> (_hvioBanks * UJA113X_HVIO_PER_BANK)) != 0U)
    {
        return Status::NOT_ALLOWED;
    }
    uint8_t const trxNormal = static_cast<uint8_t>(_config.transceiverIrqEnable
                                                   & static_cast<uint8_t>(~UJA113X_CWI_MASK));
    uint8_t const trxSleep  = static_cast<uint8_t>(
        wakeup.can ? (trxNormal | UJA113X_CWI_MASK) : trxNormal);
    // COM-39: CAN wake-up (CWIE, 0x23) first, partial networking last, so that nothing is written into the
    // transceiver control area 0x20..0x2F after PNCOK has been set (a write to 0x26..0x2F/0x68..0x6F clears it).
    Status status = writeVerified(UJA113X_REG_TRANSCEIVER_IRQ_ENABLE, trxSleep, TRX_IRQ_MASK);
    if ((status == Status::OK) && selective)
    {
        status = configurePartialNetworking(*_canWakeupFrame, true);
    }
    // HVIO wake-up inputs of both banks. `wakeInputs` is an input mask (bit i = HVIO i + 1); the two enable
    // bits per HVIO come from the edges the configuration names (IO-10).
    for (uint8_t bank = 0U; (status == Status::OK) && (bank < _hvioBanks); ++bank)
    {
        uint8_t const value = wakeIrqEnableValue(bank, wakeup.wakeInputs);
        if (value != 0U)
        {
            status = writeVerified(hvioRegister(bank, UJA113X_REG_BANK0_WAKE_IRQ_ENABLE), value, 0xFFU);
        }
    }

    uint8_t global = 0U;
    if (status == Status::OK)
    {
        status = read(UJA113X_REG_GLOBAL_IRQ_STATUS, global);
    }
    if ((status == Status::OK) && (global == 0U))
    {
        status = writeVerified(UJA113X_REG_MODE_CONTROL, UJA113X_MC_STANDBY, UJA113X_MC_MASK);
        // With a cyclic wake-up the watchdog keeps running as timeout watchdog in Sleep (data sheet 7.2.3, Table 8) and
        // its overflow wakes the SBC; without it the watchdog is switched off. Allowed now that the SBC is in
        // Standby (a WMC/NWP change in Normal mode resets the system).
        if (status == Status::OK)
        {
            status = writeLowPowerWatchdog(wakeup);
        }
        // COM-39: partial networking still confirmed after Standby and the watchdog writes, right before Sleep.
        if ((status == Status::OK) && selective)
        {
            status = checkPartialNetworkingStatus();
        }
        if (status == Status::OK)
        {
            status = read(UJA113X_REG_GLOBAL_IRQ_STATUS, global);
        }
        if ((status == Status::OK) && (global == 0U))
        {
            status = write(UJA113X_REG_MODE_CONTROL, UJA113X_MC_SLEEP);
            // Normally V1 is gone within milliseconds. If the MCU is still running a second later
            // (Sleep refused, e.g. SLPC = 1), cyclic() initializes the SBC again.
            _state                = State::FAILED;
            _lastStartupAttemptMs = nowMs;
            return status;
        }
    }

    // Abort (AH1501 Fig 37): back to Normal operation and let cyclic() handle the interrupt.
    if (status == Status::OK)
    {
        status = Status::INTERRUPT_PENDING;
    }
    (void)returnToNormal(nowMs, wakeup, selective);
    return status;
}

uint8_t Uja113x::lowPowerWatchdogValue(SleepWakeup const& wakeup) const
{
    bool const cyclic = wakeup.cyclic;
    if (!cyclic && (_config.watchdogMode == WatchdogMode::AUTONOMOUS))
    {
        return watchdogControlValue(); // nothing to change
    }
    return static_cast<uint8_t>(
        (cyclic ? (UJA113X_WMC_TIMEOUT << UJA113X_WMC_SHIFT) : (UJA113X_WMC_AUTONOMOUS << UJA113X_WMC_SHIFT))
        | static_cast<uint8_t>(cyclic ? wakeup.cyclicPeriod : _config.watchdogPeriod));
}

Uja113x::Status Uja113x::writeLowPowerWatchdog(SleepWakeup const& wakeup)
{
    Status status = Status::OK;
    if (wakeup.cyclic || (_config.watchdogMode != WatchdogMode::AUTONOMOUS))
    {
        uint8_t const value = lowPowerWatchdogValue(wakeup);
        // Written twice (required when the period gets shorter), then a spurious WDI is cleared.
        status = write(UJA113X_REG_WATCHDOG_CONTROL, value);
        if (status == Status::OK)
        {
            status = write(UJA113X_REG_WATCHDOG_CONTROL, value);
        }
        if (status == Status::OK)
        {
            status = write(UJA113X_REG_SYSTEM_IRQ_STATUS, UJA113X_WDI_MASK);
        }
    }
    return status;
}

Uja113x::Status Uja113x::returnToNormal(uint32_t const nowMs, SleepWakeup const& wakeup, bool const selective)
{
    uint8_t const trxNormal = static_cast<uint8_t>(_config.transceiverIrqEnable
                                                   & static_cast<uint8_t>(~UJA113X_CWI_MASK));
    uint8_t mode  = 0U;
    Status result = read(UJA113X_REG_MODE_CONTROL, mode);
    if ((result == Status::OK) && ((mode & UJA113X_MC_MASK) == UJA113X_MC_STANDBY))
    {
        if (wakeup.cyclic || (_config.watchdogMode != WatchdogMode::AUTONOMOUS))
        {
            (void)configureWatchdog(nowMs); // restores WMC/NWP of the configuration while still in Standby
        }
        result = enterNormal();
        if (result != Status::OK)
        {
            _state                = State::FAILED;
            _lastStartupAttemptMs = nowMs;
        }
    }
    else if ((result == Status::OK) && ((mode & UJA113X_MC_MASK) != UJA113X_MC_NORMAL))
    {
        result = Status::NOT_READY; // neither Standby nor Normal: not reached
    }
    else
    {
        // Normal already (abort before Standby), or the mode could not be read (result says so)
    }
    (void)writeVerified(UJA113X_REG_TRANSCEIVER_IRQ_ENABLE, trxNormal, TRX_IRQ_MASK);
    for (uint8_t bank = 0U; bank < _hvioBanks; ++bank)
    {
        if (wakeIrqEnableValue(bank, wakeup.wakeInputs) != 0U)
        {
            // Back to what the startup left in this bank, not blindly to zero (IO-10).
            (void)writeVerified(
                hvioRegister(bank, UJA113X_REG_BANK0_WAKE_IRQ_ENABLE), normalWakeIrqEnable(bank), 0xFFU);
        }
    }
    uint8_t canControl = 0U;
    if (selective && (read(UJA113X_REG_CAN_CONTROL, canControl) == Status::OK))
    {
        // Partial networking off again and the configured CAN mode back (COM-39: CMC was 00), as after the startup.
        (void)writeVerified(UJA113X_REG_CAN_CONTROL, canControlNormalValue(canControl),
                            static_cast<uint8_t>(CAN_CONTROL_MASK | UJA113X_PNCOK_MASK));
        // IO-33: CAN is on again; a battery event still present is delayed and switched off anew by cyclic()
        _canShutOff   = false;
        _batteryEvent = 0U;
    }
    return result;
}

Uja113x::Status Uja113x::enterStandby(uint32_t const nowMs, SleepWakeup const& wakeup)
{
    if (_state != State::NORMAL)
    {
        return Status::NOT_READY;
    }
    // A Standby nothing can end would leave an MCU in a stop mode without supply of a wake-up (the SBC itself
    // would not mind, unlike Sleep).
    if (!wakeup.any())
    {
        return Status::NOT_ALLOWED;
    }
    if ((wakeup.wakeInputs >> (_hvioBanks * UJA113X_HVIO_PER_BANK)) != 0U)
    {
        return Status::NOT_ALLOWED;
    }
    uint8_t const trxNormal  = static_cast<uint8_t>(_config.transceiverIrqEnable
                                                    & static_cast<uint8_t>(~UJA113X_CWI_MASK));
    uint8_t const trxStandby = static_cast<uint8_t>(wakeup.can ? (trxNormal | UJA113X_CWI_MASK) : trxNormal);
    Status status            = writeVerified(UJA113X_REG_TRANSCEIVER_IRQ_ENABLE, trxStandby, TRX_IRQ_MASK);
    for (uint8_t bank = 0U; (status == Status::OK) && (bank < _hvioBanks); ++bank)
    {
        uint8_t const value = wakeIrqEnableValue(bank, wakeup.wakeInputs);
        if (value != 0U)
        {
            status = writeVerified(hvioRegister(bank, UJA113X_REG_BANK0_WAKE_IRQ_ENABLE), value, 0xFFU);
        }
    }
    uint8_t global = 0U;
    if (status == Status::OK)
    {
        status = read(UJA113X_REG_GLOBAL_IRQ_STATUS, global);
    }
    if ((status == Status::OK) && (global == 0U))
    {
        status = writeVerified(UJA113X_REG_MODE_CONTROL, UJA113X_MC_STANDBY, UJA113X_MC_MASK);
        if (status == Status::OK)
        {
            status = writeLowPowerWatchdog(wakeup); // allowed in Standby only
        }
        if (status == Status::OK)
        {
            status = read(UJA113X_REG_GLOBAL_IRQ_STATUS, global);
        }
        if ((status == Status::OK) && (global == 0U))
        {
            _state                  = State::STANDBY;
            _standbyWakeup          = wakeup;
            _standbyWatchdog        = lowPowerWatchdogValue(wakeup);
            _standbyWatchdogService = _watchdogActive && !wakeup.cyclic;
            if (wakeup.cyclic || (_config.watchdogMode != WatchdogMode::AUTONOMOUS))
            {
                _lastTriggerMs = nowMs; // writeLowPowerWatchdog() restarted the watchdog
            }
            _lastModeCheckMs        = nowMs;
            _lastInterruptReadMs    = nowMs;
            _canReady               = false; // the transceiver is offline in Standby
            return Status::OK;
        }
    }
    if (status == Status::OK)
    {
        status = Status::INTERRUPT_PENDING;
    }
    (void)returnToNormal(nowMs, wakeup, false);
    return status;
}

Uja113x::Status Uja113x::leaveStandby(uint32_t const nowMs)
{
    if (_state != State::STANDBY)
    {
        return Status::NOT_READY;
    }
    _state              = State::NORMAL; // returnToNormal() sets FAILED if the mode change fails
    Status const status = returnToNormal(nowMs, _standbyWakeup, false);
    if (status != Status::OK)
    {
        // Not in Normal mode: the SBC went through a reset of its own (mode neither Standby nor Normal), or SPI
        // failed - start again
        if (status == Status::NOT_READY)
        {
            ++_counters.modeLost;
        }
        _state                = State::FAILED;
        _lastStartupAttemptMs = nowMs;
        _canReady             = false;
        return status;
    }
    // _lastTriggerMs stays: configureWatchdog() set it if it triggered; otherwise the phase of the Standby triggers
    // goes on
    _lastTransceiverPollMs = nowMs;
    _lastModeCheckMs       = nowMs;
    pollTransceiver();
    return Status::OK;
}

Uja113x::Status Uja113x::readRegister(uint8_t const address, uint8_t& value)
{
    return (address < 0x80U) ? read(address, value) : Status::NOT_ALLOWED;
}

Uja113x::Status Uja113x::writeRegister(uint8_t const address, uint8_t const value)
{
    if (isProtected(address))
    {
        return Status::NOT_ALLOWED;
    }
    Status const status = write(address, value);
    if ((address == UJA113X_REG_CAN_CONTROL) && (_state == State::NORMAL))
    {
        // COM-23: CMC decides whether the transmitter is on (listen-only/offline: CTS = 0), and nothing raises an
        // interrupt for it (only CFS has CFI); read the status now instead of in the next cycle.
        pollTransceiver();
    }
    return status;
}

Uja113x::InterruptFlags Uja113x::takeInterruptEvents()
{
    InterruptFlags const events = _events;
    _events                     = InterruptFlags();
    return events;
}

/* ---- reset counter compensation (sbc/Uja113xResetCounter.h, SYS-07) ---- */

bool isMcuWatchdogTestReset(bool const lastResetWasWatchdog, uint8_t const watchdogTestField)
{
    return lastResetWasWatchdog
           && ((watchdogTestField == MCU_WATCHDOG_TEST_LOW) || (watchdogTestField == MCU_WATCHDOG_TEST_HIGH));
}

ResetCounterCompensation compensateWatchdogTestReset(
    Uja113x& sbc, bool const lastResetWasWatchdog, uint8_t const watchdogTestField)
{
    using Result = ResetCounterCompensation::Result;
    ResetCounterCompensation c{Result::NOT_TEST_RESET, 0U, 0U};
    if (!isMcuWatchdogTestReset(lastResetWasWatchdog, watchdogTestField))
    {
        return c;
    }

    // Same rule as the startup: no write without the expected device answering.
    uint8_t id = 0U;
    if (sbc.readRegister(UJA113X_REG_IDENTIFICATION_0, id) != Uja113x::Status::OK)
    {
        c.result = Result::SPI_ERROR;
        return c;
    }
    if (id != sbc.getConfig().identification)
    {
        c.result = Result::WRONG_DEVICE;
        return c;
    }
    if (sbc.readRegister(UJA113X_REG_FAIL_SAFE_CONTROL, c.failSafeBefore) != Uja113x::Status::OK)
    {
        c.result = Result::SPI_ERROR;
        return c;
    }
    uint8_t const rcc = static_cast<uint8_t>(c.failSafeBefore & UJA113X_RCC_MASK);
    if (rcc == 0U)
    {
        c.result        = Result::COUNTER_ZERO;
        c.failSafeAfter = c.failSafeBefore;
        return c;
    }

    // Only RCC changes; ENSC/ENDC/ENC and LHC are written back as read.
    uint8_t const value = static_cast<uint8_t>(
        (c.failSafeBefore & static_cast<uint8_t>(~UJA113X_RCC_MASK)) | (rcc - 1U));
    if ((sbc.writeRegister(UJA113X_REG_FAIL_SAFE_CONTROL, value) != Uja113x::Status::OK)
        || (sbc.readRegister(UJA113X_REG_FAIL_SAFE_CONTROL, c.failSafeAfter) != Uja113x::Status::OK))
    {
        c.result = Result::SPI_ERROR;
        return c;
    }
    c.result = (c.failSafeAfter == value) ? Result::COMPENSATED : Result::VERIFY_ERROR;
    return c;
}

char const* resetCounterCompensationName(ResetCounterCompensation::Result const result)
{
    using Result = ResetCounterCompensation::Result;
    switch (result)
    {
        case Result::NOT_TEST_RESET: return "no test reset";
        case Result::COMPENSATED: return "compensated";
        case Result::COUNTER_ZERO: return "RCC already 0";
        case Result::SPI_ERROR: return "SPI error";
        case Result::WRONG_DEVICE: return "wrong device ID";
        case Result::VERIFY_ERROR: return "read-back mismatch";
        default: return "?";
    }
}

} // namespace sbc
