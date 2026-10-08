#include "systems/SbcSystem.h"

#include "bsp/sbc/sbcConfiguration.h"
#include "bsp/timer/SystemTimer.h"
#include "console/ConsoleContext.h"
#include "inputManager/DigitalInput.h"
#include "integration/moduleConfiguration.h"
#include "reset/softwareSystemReset.h"
#include "sbc/Uja113xPowerSupply.h"
#include "systems/SbcConsole.h"
#include "systems/SbcPowerReport.h"

#include <util/format/SharedStringWriter.h>
#include <util/logger/Logger.h>

#ifdef PLATFORM_SUPPORT_CAN
// COM-23: role can_phy reports changes of the transceiver readiness (libs/bsw/canSystem); a project without CAN
// builds without it
#include "can/CanPhyReadiness.h"
#endif

// IO-68: INTN1 of the SBC. Board fact: the module connection intn1 (board.yaml devices[].connect, optional), generated
// as NX_DEVICE_<sbc>_IO_INTN1_WIRED / _IO_INTN1 in integration/sbcUja113xConfiguration.h. Project decision: a pin
// event on that input (pin_events.<input>), which brings its pin interrupt - looked up by the input name in the
// generated table, so nothing here names a pin or an input.
#include "bsp/pinEvent/pinEventConfiguration.h"
#if NX_PIN_EVENT_COUNT > 0
#include "bsp/pinIrq/PinIrqConfig.h"
#endif

#define SBC_INTN1_WIRED BSP_SBC_CONCAT(BSP_SBC_CONCAT(NX_DEVICE_, BSP_SBC_DEVICE), _IO_INTN1_WIRED)
#define SBC_INTN1_INPUT BSP_SBC_CONCAT(BSP_SBC_CONCAT(NX_DEVICE_, BSP_SBC_DEVICE), _IO_INTN1)
#define SBC_STRING_(x)  #x
#define SBC_STRING(x)   SBC_STRING_(x)

DECLARE_LOGGER_COMPONENT(BSP)

namespace
{
/* IO-68: index of the pin event on the INTN1 input, NO_PIN_EVENT without one (or without INTN1 wired). */
constexpr uint8_t NO_PIN_EVENT = 0xFFU;

#if SBC_INTN1_WIRED
constexpr char const* INTN1_INPUT_NAME = SBC_STRING(SBC_INTN1_INPUT);

constexpr bool sameName(char const* const a, char const* const b)
{
    return (*a == *b) && ((*a == '\0') || sameName(a + 1, b + 1));
}

constexpr uint8_t intn1PinEvent()
{
#if NX_PIN_EVENT_COUNT > 0
    for (uint8_t i = 0U; i < NX_PIN_EVENT_COUNT; ++i)
    {
        if (sameName(::pinevent::project::CONFIG[i].name, INTN1_INPUT_NAME))
        {
            return i;
        }
    }
#endif
    return NO_PIN_EVENT;
}
#else
constexpr char const* INTN1_INPUT_NAME = "";

constexpr uint8_t intn1PinEvent() { return NO_PIN_EVENT; }
#endif

constexpr uint8_t INTN1_PIN_EVENT = intn1PinEvent();

// SYS-16 (tools/logcheck/bounds.yaml): the input name goes into a log line as "%s", one entry holds 128 bytes
constexpr size_t INTN1_INPUT_NAME_MAX_CHARS = 32U;

constexpr size_t nameLength(char const* const s) { return (*s == '\0') ? 0U : (1U + nameLength(s + 1)); }

static_assert(
    nameLength(INTN1_INPUT_NAME) <= INTN1_INPUT_NAME_MAX_CHARS,
    "the board input wired to INTN1 of the SBC has a name longer than INTN1_INPUT_NAME_MAX_CHARS (log line)");

#if NX_PIN_EVENT_COUNT > 0
static_assert(
    (INTN1_PIN_EVENT == NO_PIN_EVENT)
        || (::pinevent::project::CONFIG[INTN1_PIN_EVENT].edge != ::pinevent::Edge::DEACTIVATE),
    "the pin event on the INTN1 input of the SBC counts only the release of the line: give it edge activate (or "
    "both) in project.yaml pin_events, the activating edge is the interrupt and the wake-up of the MCU");
#endif

constexpr uint32_t SBC_CYCLE_MS       = 10U;
constexpr uint32_t SLEEP_DELAY_MS     = 300U; // lets the console print the confirmation first
// RTE-16: bsw.power every 100 ms (5 SPI reads, about 0.2 ms at 500 kHz); undervoltage events reach the application
// through the SBC's own comparator status, the period only limits how fresh the voltage is.
constexpr uint8_t POWER_REPORT_CYCLES = 10U;

enum Id
{
    ID_STATUS,
    ID_DUMP,
    ID_READ,
    ID_WRITE,
    ID_SLEEP,
    ID_FAILSTART,
    ID_PN,
    ID_STANDBY,
};

// IO-68: limits of the board test "sbc standby <ms>"
constexpr uint32_t STANDBY_MIN_MS = 100U;
constexpr uint32_t STANDBY_MAX_MS = 600000U;

char const* statusName(::sbc::Uja113x::Status const status)
{
    using Status = ::sbc::Uja113x::Status;
    switch (status)
    {
        case Status::OK: return "ok";
        case Status::SPI_ERROR: return "SPI error";
        case Status::WRONG_DEVICE: return "wrong device ID";
        case Status::VERIFY_ERROR: return "read-back mismatch";
        case Status::NOT_ALLOWED: return "not allowed";
        case Status::INTERRUPT_PENDING: return "interrupt pending";
        case Status::NOT_READY: return "not initialized";
        default: return "?";
    }
}

using ::systems::sbcconsole::modeName;

/* COM-07/COM-21: registers of partial networking, printed by "sbc dump" after the registers of SbcConsole.h: data
 * rate, identifier, identifier mask and frame control (0x26..0x2F), data masks (0x68..0x6F). Only the /FD variants
 * implement them. */
constexpr uint8_t PN_DUMP_FIRST[]  = {UJA113X_REG_PN_DATA_RATE, UJA113X_REG_PN_DATA_MASK_0};
constexpr uint8_t PN_DUMP_COUNTS[] = {
    static_cast<uint8_t>(UJA113X_REG_PN_FRAME_CONTROL - UJA113X_REG_PN_DATA_RATE + 1U), UJA113X_PN_DATA_MASK_COUNT};
constexpr size_t PN_DUMP_COUNT    = static_cast<size_t>(PN_DUMP_COUNTS[0]) + PN_DUMP_COUNTS[1];

struct PnDump
{
    uint8_t address[PN_DUMP_COUNT];
    uint8_t value[PN_DUMP_COUNT];
    bool ok[PN_DUMP_COUNT];
};

#if defined(NX_SBC_CAN_WAKEUP_FRAME)
/* COM-21: the one frame that wakes the ECU from Sleep, from project.yaml modules.<sbc>.can_wakeup_frame (bit rate
 * from can.<bus>.bitrate), generated in integration/sbcUja113xConfiguration.h. Kept by the driver by reference. */
::sbc::CanWakeupFrame const SBC_CAN_WAKEUP_FRAME = NX_SBC_CAN_WAKEUP_FRAME_INIT;
#endif
} // namespace

namespace systems
{
using ::util::logger::BSP;
using ::util::logger::Logger;

SbcSystem::SbcSystem(::async::ContextType const context)
: ::lifecycle::SingleContextLifecycleComponent(context)
, _context(context)
, _timeout()
, _spi(::bsp::sbc::SPI_CONFIG)
, _sbc(_spi, ::bsp::sbc::SBC_CONFIG)
, _canPhy(_sbc)
#if !defined(NX_CONSOLE) || NX_CONSOLE
, _command(*this)
// SYS-13: the command runs in the console context, only the SBC access runs in this system's task
, _asyncCommand(_command, ::console::CONSOLE_CONTEXT)
#endif
, _sleepAtMs(0U)
, _sleepRequested(false)
, _resetRequested(false)
, _canReady(false)
, _state(::sbc::Uja113x::State::UNINITIALIZED)
, _startupAttempts(0U)
, _powerCycle(0U)
, _intnCount(0U)
, _intnEdgeUs(0U)
, _standbyForMs(0U)
, _standbySinceMs(0U)
, _standbyIntnCount(0U)
, _standbyRequested(false)
, _standbyTest(false)
, _lineFaultLogged(false)
{}

bool SbcSystem::isMcuWakeLineArmed() const
{
#if NX_PIN_EVENT_COUNT > 0
    if constexpr (INTN1_PIN_EVENT != NO_PIN_EVENT)
    {
        ::pinevent::Statistics statistics{};
        return ::bsp::pinEvents().statistics(INTN1_PIN_EVENT, statistics) && statistics.armed;
    }
#endif
    return false;
}

::sbc::Uja113x::InterruptLine SbcSystem::sampleInterruptLine()
{
    using Line = ::sbc::Uja113x::InterruptLine;
#if SBC_INTN1_WIRED
    bool active = false;
    // INTN1 is a level output: low as long as an enabled interrupt is pending, so a sample per cycle misses none
    if (::bios::DigitalInput::get(::bios::DigitalInput::SBC_INTN1_INPUT, active) != ::bsp::BSP_OK)
    {
        return Line::UNKNOWN;
    }
#if NX_PIN_EVENT_COUNT > 0
    if constexpr (INTN1_PIN_EVENT != NO_PIN_EVENT)
    {
        // an edge since the previous cycle counts too, even if the line is high again (e.g. cleared by "sbc write")
        ::pinevent::Reading reading{};
        if (::bsp::pinEvents().read(INTN1_PIN_EVENT, reading))
        {
            active      = active || (reading.count != _intnCount);
            _intnCount  = reading.count;
            _intnEdgeUs = reading.timeUs;
        }
    }
#endif
    return active ? Line::ACTIVE : Line::INACTIVE;
#else
    return Line::UNKNOWN; // not wired on this board: 0x60 is read in every cycle
#endif
}

void SbcSystem::init()
{
    logResetCounterCompensation();
    _spi.init();
    ::sbc::Uja113x::Status const status = _sbc.startup(getSystemTimeMs32Bit());
    logStartup(status);
#if defined(NX_SBC_CAN_WAKEUP_FRAME)
    // Only kept here; enterSleep() programs and verifies the registers (and refuses on a device without /FD).
    if (_sbc.setCanWakeupFrame(&SBC_CAN_WAKEUP_FRAME) != ::sbc::Uja113x::Status::OK)
    {
        Logger::error(BSP, "SBC: CAN wake-up frame rejected");
    }
#endif
    _state           = _sbc.getState();
    _canReady        = _sbc.isCanTransceiverReady();
    _startupAttempts = _sbc.getCounters().startupAttempts;
    // IO-68: how the service learns of SBC interrupts on this board
    if constexpr (SBC_INTN1_WIRED == 0)
    {
        Logger::info(BSP, "SBC INTN1 not wired: interrupt register read every %u ms", static_cast<unsigned>(SBC_CYCLE_MS));
    }
    else
    {
        Logger::info(
            BSP,
            "SBC INTN1 on %s, %s",
            INTN1_INPUT_NAME,
            (INTN1_PIN_EVENT != NO_PIN_EVENT) ? "pin interrupt (pin event)" : "level only, no pin event");
    }
    reportPower(); // bsw.power from the lifecycle init on, not only after the first 100 ms
    transitionDone();
}

void SbcSystem::run()
{
    ::async::scheduleAtFixedRate(_context, *this, _timeout, SBC_CYCLE_MS, ::async::TimeUnit::MILLISECONDS);
    transitionDone();
}

void SbcSystem::shutdown()
{
    _timeout.cancel();
    transitionDone();
}

#ifdef NX_FLEXNVM_PARTITION_COMMAND
uint32_t SbcSystem::watchdogAllowanceMs() const
{
    if (_sbc.getState() != ::sbc::Uja113x::State::NORMAL)
    {
        return 0U; // no successful startup: watchdog state unknown
    }
    uint8_t const status = _sbc.getStartupInfo().watchdogStatus;
    ::sbc::Uja113xConfig const& config = _sbc.getConfig();
    // Forced Normal mode: watchdog disabled; software development mode: off while WMC is autonomous (data sheet
    // 7.2.1, 7.2.4, Table 8; the same decision as Uja113x::startup())
    bool const off = ((status & UJA113X_FNMS_MASK) != 0U)
                     || ((config.watchdogMode == ::sbc::WatchdogMode::AUTONOMOUS)
                         && ((status & UJA113X_SDMS_MASK) != 0U));
    if (off)
    {
        return 0xFFFFFFFFU;
    }
    uint32_t const period = ::sbc::Uja113x::watchdogPeriodMs(config.watchdogPeriod);
    return (period > SBC_CYCLE_MS) ? (period - SBC_CYCLE_MS) : 0U;
}
#endif

void SbcSystem::logResetCounterCompensation()
{
    using Result  = ::sbc::ResetCounterCompensation::Result;
    auto const& c = SbcEarlyStart::getCompensation();
    unsigned const srs = static_cast<unsigned>(SbcEarlyStart::getResetStatus());
    unsigned const tst = SbcEarlyStart::getWatchdogTestField();
    switch (c.result)
    {
        case Result::COMPENSATED:
            Logger::info(
                BSP,
                "SBC RCC compensated: was %u, now %u, SRS 0x%x, TST %u",
                static_cast<unsigned>(c.failSafeBefore & UJA113X_RCC_MASK),
                static_cast<unsigned>(c.failSafeAfter & UJA113X_RCC_MASK),
                srs,
                tst);
            break;
        case Result::NOT_TEST_RESET:
        case Result::COUNTER_ZERO:
            Logger::info(
                BSP,
                "SBC RCC compensation: %s, SRS 0x%x, TST %u",
                ::sbc::resetCounterCompensationName(c.result),
                srs,
                tst);
            break;
        default:
            // SYS-16: two entries - as one line (137 bytes worst case) TST would print as "<?>"
            Logger::warn(
                BSP,
                "SBC RCC compensation failed: %s, SRS 0x%x, TST %u",
                ::sbc::resetCounterCompensationName(c.result),
                srs,
                tst);
            Logger::warn(
                BSP,
                "SBC RCC compensation failed: 0x02 was 0x%02x, read back 0x%02x",
                c.failSafeBefore,
                c.failSafeAfter);
            break;
    }
#ifndef NX_PRODUCT_BUILD // SYS-74: board test "sbc failstart" not in the product image
    uint8_t const armed = SbcEarlyStart::getArmedFailStarts();
    if (armed != 0U)
    {
        Logger::warn(BSP, "SBC failstart: %u failed starts still armed", static_cast<unsigned>(armed));
    }
#endif
}

void SbcSystem::logStartup(::sbc::Uja113x::Status const status)
{
    auto const& s = _sbc.getStartupInfo();
    if (status != ::sbc::Uja113x::Status::OK)
    {
        Logger::error(
            BSP,
            "SBC startup failed: %s (ID 0x%02x, SPI errors %u)",
            statusName(status),
            s.identification,
            static_cast<unsigned>(_sbc.getCounters().spiErrors));
        return;
    }
    uint8_t const rss = _sbc.getResetSource();
    // SYS-16: two entries - with the longest reset source name (37 characters) one line needs 146 bytes, the end
    // would print as "<?>"
    Logger::info(
        BSP,
        "SBC UJA113x ready: ID 0x%02x, was %s, SPI %u Hz",
        s.identification,
        modeName(s.modeBefore),
        static_cast<unsigned>(_spi.getBaudrateHz()));
    Logger::info(
        BSP, "SBC reset source %u (%s)", static_cast<unsigned>(rss), ::sbc::Uja113x::resetSourceName(rss));
    if (!::sbc::uja113xRestartedForThisStart(s, _sbc.getState(), _sbc.getConfig().resetCounter))
    {
        // NVM-34: RCC at its start value and no start interrupt - RSS is the one of an earlier SBC reset
        Logger::info(BSP, "SBC reset source is old: no SBC reset, MCU-only start");
    }
    Logger::info(
        BSP,
        "SBC startup irq: sys 0x%02x supply 0x%02x trx 0x%02x wake 0x%02x fail 0x%02x, RCC was %u",
        s.interrupts.system,
        s.interrupts.supply,
        s.interrupts.transceiver,
        s.interrupts.wake,
        s.interrupts.failure,
        static_cast<unsigned>(s.failSafeBefore & UJA113X_RCC_MASK));
    if (s.mtpnvMismatch)
    {
        Logger::warn(
            BSP,
            "SBC MTPNV differs from configuration: status 0x%02x, 0x73 = 0x%02x, 0x74 = 0x%02x",
            s.mtpnvStatus,
            s.startupControl,
            s.sbcConfig);
    }
    if (s.limpHomeWasActive)
    {
        Logger::warn(BSP, "SBC limp home was active (forced sleep preparation), released");
    }
    auto const& c = _sbc.getCounters();
    if ((c.verifyErrors != 0U) || (c.addressEchoMismatches != 0U))
    {
        Logger::warn(
            BSP,
            "SBC SPI: %u read-back mismatches, %u address echo mismatches",
            static_cast<unsigned>(c.verifyErrors),
            static_cast<unsigned>(c.addressEchoMismatches));
    }
}

void SbcSystem::reportChanges(uint32_t const nowMs)
{
    // Re-initialization after an SBC reset or a failed startup; retries that keep failing stay quiet.
    ::sbc::Uja113x::State const state = _sbc.getState();
    if ((_sbc.getCounters().startupAttempts != _startupAttempts)
        && ((state != _state) || (state == ::sbc::Uja113x::State::NORMAL)))
    {
        logStartup(
            (state == ::sbc::Uja113x::State::NORMAL) ? ::sbc::Uja113x::Status::OK
                                                     : ::sbc::Uja113x::Status::NOT_READY);
    }
    _startupAttempts = _sbc.getCounters().startupAttempts;
    _state           = state;

    auto const events = _sbc.takeInterruptEvents();
    if (events.any())
    {
        Logger::warn(
            BSP,
            "SBC interrupt: sys 0x%02x supply 0x%02x trx 0x%02x wake 0x%02x fail 0x%02x",
            events.system,
            events.supply,
            events.transceiver,
            events.wake,
            events.failure);
    }
    // IO-68: a broken INTN1 line is reported once; the driver reads 0x60 in every cycle from then on
    if (_sbc.isInterruptLineFaulty() && !_lineFaultLogged)
    {
        _lineFaultLogged = true;
        Logger::error(
            BSP,
            "SBC INTN1 faulty: %u interrupts it did not show, interrupt register read every cycle",
            static_cast<unsigned>(_sbc.getCounters().interruptLineMisses));
    }
    serviceStandbyTest(nowMs, events);

    reportCanReadiness();
}

void SbcSystem::serviceStandbyTest(uint32_t const nowMs, ::sbc::Uja113x::InterruptFlags const& events)
{
    if (!_standbyTest)
    {
        return;
    }
    if (_sbc.getState() != ::sbc::Uja113x::State::STANDBY)
    {
        // the driver left Standby itself (SBC reset in Standby, initialized again)
        _standbyTest = false;
        Logger::error(BSP, "SBC Standby ended by the driver after %u ms", static_cast<unsigned>(nowMs - _standbySinceMs));
        return;
    }
    bool const wakeup  = events.any();
    bool const timeout = (nowMs - _standbySinceMs) >= _standbyForMs;
    if (!wakeup && !timeout)
    {
        return;
    }
    _standbyTest = false;
    // Time from the INTN1 edge (pin interrupt) to this service: what a stop mode of the MCU adds is the wake-up of
    // the core and the clock, this is the rest of the path.
    uint32_t const edgeAgeUs = getSystemTimeUs32Bit() - _intnEdgeUs;
    bool const edgeSeen      = (_intnCount != _standbyIntnCount);
    ::sbc::Uja113x::Status const status = _sbc.leaveStandby(nowMs);
    Logger::info(
        BSP,
        "SBC Standby left after %u ms by %s: %s",
        static_cast<unsigned>(nowMs - _standbySinceMs),
        wakeup ? "wake-up" : "timeout",
        statusName(status));
    if (edgeSeen)
    {
        Logger::info(BSP, "SBC INTN1 edge %u us before the service", static_cast<unsigned>(edgeAgeUs));
    }
}

void SbcSystem::reportCanReadiness()
{
    bool const canReady = _sbc.isCanTransceiverReady();
    if (canReady != _canReady)
    {
        _canReady = canReady;
        Logger::info(
            BSP,
            "SBC CAN transceiver %s (status 0x%02x)",
            canReady ? "ready" : "not ready",
            _sbc.getTransceiverStatus());
#ifdef PLATFORM_SUPPORT_CAN
        // COM-23: the CAN system mutes or unmutes right away (gate runnable in task can, above this task and the
        // RTE's CAN task) instead of sampling the readiness every millisecond (COM-22). Without a CAN system
        // (not started yet, stopped) nothing happens; its start reads the readiness itself.
        ::can::CanPhyReadiness::notify(_canPhy);
#endif
    }
}

void SbcSystem::reportPower()
{
    ::sbc::Uja113x::BatteryVoltages voltages{};
    ::sbc::Uja113x::Status const status = _sbc.readBatteryVoltages(voltages);
    sbcpower::publish(sbcpower::reportOf(status, voltages, ::bsp::sbc::SBC_CONFIG.battery));
}

void SbcSystem::execute()
{
    uint32_t const nowMs = getSystemTimeMs32Bit();
    if (_resetRequested && ((nowMs - _sleepAtMs) >= SLEEP_DELAY_MS))
    {
        softwareSystemReset();
    }
    if (_sleepRequested && ((nowMs - _sleepAtMs) >= SLEEP_DELAY_MS))
    {
        _sleepRequested                     = false;
        ::sbc::Uja113x::Status const status = _sbc.enterSleep(nowMs);
        if (status != ::sbc::Uja113x::Status::OK)
        {
            Logger::error(BSP, "SBC Sleep refused: %s", statusName(status));
        }
    }
    if (_standbyRequested)
    {
        // IO-68, board test: Standby with V1 on, woken by CAN bus activity or a wake-up input of the board; the MCU
        // keeps running here (its stop mode is PLAT-108), so the wake-up shows as INTN1 going low
        _standbyRequested = false;
        ::sbc::SleepWakeup const wakeup{
            true, ::sbc::Uja113x::configuredWakeInputs(_sbc.getConfig()), false, _sbc.getConfig().watchdogPeriod};
        ::sbc::Uja113x::Status const status = _sbc.enterStandby(nowMs, wakeup);
        if (status == ::sbc::Uja113x::Status::OK)
        {
            _standbyTest      = true;
            _standbySinceMs   = nowMs;
            _standbyIntnCount = _intnCount;
            Logger::info(
                BSP,
                "SBC Standby for at most %u ms, wake inputs 0x%02x, MCU wake-up line %s",
                static_cast<unsigned>(_standbyForMs),
                static_cast<unsigned>(wakeup.wakeInputs),
                isMcuWakeLineArmed() ? "armed" : "not armed");
        }
        else
        {
            Logger::error(BSP, "SBC Standby refused: %s", statusName(status));
        }
        reportCanReadiness(); // the transceiver is offline in Standby: the CAN system mutes now
    }
    _sbc.cyclic(nowMs, sampleInterruptLine());
    reportChanges(nowMs);
    if (++_powerCycle >= POWER_REPORT_CYCLES)
    {
        _powerCycle = 0U;
        reportPower();
    }
}

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(SbcSystem::Command, "sbc", "UJA113x system basis chip")
COMMAND_GROUP_COMMAND(ID_STATUS, "status", "state, supply, transceiver, counters, INTN pins")
COMMAND_GROUP_COMMAND(ID_DUMP, "dump", "read all documented registers")
COMMAND_GROUP_COMMAND(ID_READ, "read", "read register (param: <address>)")
#ifndef NX_PRODUCT_BUILD // SYS-119: register write past the driver (fail-safe, regulators, CAN), not in the product
COMMAND_GROUP_COMMAND(ID_WRITE, "write", "write register (param: <address> <value>), not 0x00/0x01/MTPNV")
#endif
// SYS-17: "(board rule)" at the end of the help text marks the commands that may only be used with a person at
// the board (the board's README says why). tools/docgen reads the marker for doc/reference/console.md.
#ifndef NX_PRODUCT_BUILD // SYS-119 (review point S4): the product sleeps through "ecu sleep" and its run-down only
COMMAND_GROUP_COMMAND(ID_SLEEP, "sleep", "enter SBC Sleep, MCU supply off, wake-up by CAN (board rule)")
#endif
#ifndef NX_PRODUCT_BUILD // SYS-74: fault injection (failed starts), not in the product image
COMMAND_GROUP_COMMAND(ID_FAILSTART, "failstart", "reset now and in the next <n> (0..3) starts before the SBC start, JP1 test (board rule)")
#endif
// COM-39: writes the partial networking registers of the CAN wake-up frame without Sleep, "sbc dump" shows them
#ifndef NX_PRODUCT_BUILD // SYS-119: board test of COM-39, not in the product image (the sleep arms PN itself)
COMMAND_GROUP_COMMAND(ID_PN, "pn", "arm: write CAN wake-up frame registers (0x20, 0x26..0x2F, 0x68..0x6F) without Sleep")
#endif
#ifndef NX_PRODUCT_BUILD // IO-68 board test, like sbc sleep not in the product image (SYS-119 S4)
// IO-68: Standby keeps V1 (the MCU stays supplied), but CAN is offline until the wake-up or the time is over
COMMAND_GROUP_COMMAND(ID_STANDBY, "standby", "SBC Standby, V1 on, CAN offline, until a wake-up or <ms> (100..600000)")
#endif
DEFINE_COMMAND_GROUP_GET_INFO_END

void SbcSystem::Command::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    // SYS-13: this runs in the console context (TASK_IDLE). Every access to the SBC - SPI, driver state, the
    // requests of sleep/failstart - runs in the task of the SBC system (inSbcTask), the output here.
    SbcSystem& system   = _system;
    ::sbc::Uja113x& sbc = system._sbc;
    auto const inSbcTask = [&system](auto& work) { ::console::runInOwnerContext(system._context, work); };
    switch (idx)
    {
        case ID_STATUS:
        {
            sbcconsole::StatusSnapshot s{};
            bool lineFaulty = false; // IO-68
            bool wakeArmed  = false;
            auto const take = [&system, &sbc, &s, &lineFaulty, &wakeArmed]()
            {
                lineFaulty = sbc.isInterruptLineFaulty();
                wakeArmed  = system.isMcuWakeLineArmed();
                using Status   = ::sbc::Uja113x::Status;
                s.registersOk  = (sbc.readRegister(UJA113X_REG_MODE_CONTROL, s.mode) == Status::OK)
                                && (sbc.readRegister(UJA113X_REG_SUPPLY_STATUS, s.supply) == Status::OK)
                                && (sbc.readRegister(UJA113X_REG_TRANSCEIVER_STATUS, s.transceiver) == Status::OK)
                                && (sbc.readRegister(UJA113X_REG_WATCHDOG_STATUS, s.watchdogStatus) == Status::OK)
                                && (sbc.readRegister(UJA113X_REG_FAIL_SAFE_CONTROL, s.failSafe) == Status::OK);
                char const* const states[] = {"uninitialized", "normal", "failed", "standby"};
                s.driverState              = states[static_cast<uint8_t>(sbc.getState())];
                s.resetSource              = sbc.getResetSource();
                s.resetSourceName          = ::sbc::Uja113x::resetSourceName(s.resetSource);
                s.canReady                 = sbc.isCanTransceiverReady();
                auto const& comp           = SbcEarlyStart::getCompensation();
                s.compensationName         = ::sbc::resetCounterCompensationName(comp.result);
                s.failSafeBefore           = comp.failSafeBefore;
                s.failSafeAfter            = comp.failSafeAfter;
                s.resetStatus              = SbcEarlyStart::getResetStatus();
                s.watchdogTestField        = SbcEarlyStart::getWatchdogTestField();
                s.armedFailStarts          = SbcEarlyStart::getArmedFailStarts();
                s.counters                 = sbc.getCounters();
#if defined(NX_USE_DIO_SBC_INTERRUPT1) && defined(NX_USE_DIO_SBC_INTERRUPT2)
                s.hasInterruptPins = true;
                (void)::bios::DigitalInput::get(::bios::DigitalInput::SBC_INTERRUPT1, s.interrupt1Active);
                (void)::bios::DigitalInput::get(::bios::DigitalInput::SBC_INTERRUPT2, s.interrupt2Active);
#endif
            };
            inSbcTask(take);
            ::util::format::SharedStringWriter out(context);
            sbcconsole::writeStatus(out, s);
            // IO-68: how the interrupt register was read since the start, and the state of the INTN1 line
            ::sbc::Uja113x::Counters const& c = s.counters;
            (void)out.printf(
                "INTN1 %s: %s, line %s, MCU wake-up %s\r\n",
                (SBC_INTN1_WIRED != 0) ? INTN1_INPUT_NAME : "-",
                (SBC_INTN1_WIRED == 0)              ? "not wired"
                : (INTN1_PIN_EVENT != NO_PIN_EVENT) ? "pin event"
                                                    : "level only",
                lineFaulty ? "FAULTY" : "ok",
                wakeArmed ? "armed" : "not armed");
            (void)out.printf(
                "irq reads: line %u, fallback %u, polled %u, skipped %u, misses %u, idle low %u\r\n",
                static_cast<unsigned>(c.interruptLineReads),
                static_cast<unsigned>(c.interruptFallbackReads),
                static_cast<unsigned>(c.interruptPolls),
                static_cast<unsigned>(c.interruptReadsSkipped),
                static_cast<unsigned>(c.interruptLineMisses),
                static_cast<unsigned>(c.interruptLineIdle));
            break;
        }
        case ID_DUMP:
        {
            // One snapshot of all registers (24 + 18 SPI transfers in the SBC task), then 42 lines here.
            sbcconsole::DumpSnapshot dump{};
            PnDump pn{};
            auto const take = [&sbc, &dump, &pn]()
            {
                auto const read = [&sbc](uint8_t const address, uint8_t& value)
                { return sbc.readRegister(address, value) == ::sbc::Uja113x::Status::OK; };
                sbcconsole::takeDump(dump, read);
                size_t n = 0U;
                for (size_t r = 0U; r < (sizeof(PN_DUMP_FIRST) / sizeof(PN_DUMP_FIRST[0])); ++r)
                {
                    for (uint8_t i = 0U; i < PN_DUMP_COUNTS[r]; ++i)
                    {
                        pn.address[n] = static_cast<uint8_t>(PN_DUMP_FIRST[r] + i);
                        pn.ok[n]      = read(pn.address[n], pn.value[n]);
                        ++n;
                    }
                }
            };
            inSbcTask(take);
            ::util::format::SharedStringWriter out(context);
            sbcconsole::writeDump(out, dump);
            for (size_t i = 0U; i < PN_DUMP_COUNT; ++i)
            {
                if (pn.ok[i])
                {
                    (void)out.printf("0x%02x: 0x%02x\r\n", pn.address[i], pn.value[i]);
                }
                else
                {
                    (void)out.printf("0x%02x: SPI error\r\n", pn.address[i]);
                }
            }
            break;
        }
        case ID_READ:
        {
            uint32_t const address = context.scanIntToken<uint32_t>();
            if (context.check(address < 0x80U) && context.checkEol())
            {
                uint8_t value           = 0U;
                ::sbc::Uja113x::Status status = ::sbc::Uja113x::Status::NOT_READY;
                auto const read         = [&sbc, address, &value, &status]()
                { status = sbc.readRegister(static_cast<uint8_t>(address), value); };
                inSbcTask(read);
                ::util::format::SharedStringWriter out(context);
                (void)out.printf("0x%02x: 0x%02x (%s)\r\n", static_cast<unsigned>(address), value, statusName(status));
            }
            break;
        }
#ifndef NX_PRODUCT_BUILD // SYS-119
        case ID_WRITE:
        {
            uint32_t const address = context.scanIntToken<uint32_t>();
            uint32_t const value   = context.scanIntToken<uint32_t>();
            if (context.check((address < 0x80U) && (value <= 0xFFU)) && context.checkEol())
            {
                ::sbc::Uja113x::Status status = ::sbc::Uja113x::Status::NOT_READY;
                auto const write              = [&system, &sbc, address, value, &status]()
                {
                    status = sbc.writeRegister(static_cast<uint8_t>(address), static_cast<uint8_t>(value));
                    // COM-23: a write to the CAN control register (e.g. listen-only for the board test of COM-22)
                    // makes the driver read the transceiver status right away; report it now, not in the next
                    // cycle, so the CAN system mutes before the RTE can write into a transmitter that is off.
                    system.reportCanReadiness();
                };
                inSbcTask(write);
                ::util::format::SharedStringWriter out(context);
                (void)out.printf("0x%02x <- 0x%02x: %s\r\n", static_cast<unsigned>(address), static_cast<unsigned>(value), statusName(status));
            }
            break;
        }
#endif
#ifndef NX_PRODUCT_BUILD // SYS-119 (S4)
        case ID_SLEEP:
        {
            bool accepted                       = false;
            ::sbc::CanWakeupFrame const* filter = nullptr;
            auto const request                  = [&system, &sbc, &accepted, &filter]()
            {
                accepted = (sbc.getState() == ::sbc::Uja113x::State::NORMAL);
                filter   = sbc.getCanWakeupFrame();
                if (accepted)
                {
                    system._sleepAtMs      = getSystemTimeMs32Bit();
                    system._sleepRequested = true;
                }
            };
            inSbcTask(request);
            ::util::format::SharedStringWriter out(context);
            if (!accepted)
            {
                (void)out.printf("SBC not initialized\r\n");
                break;
            }
            // The SBC task waits SLEEP_DELAY_MS before the sleep, so this line leaves the UART first.
            if (filter != nullptr)
            {
                // COM-21: selective wake-up, only the configured frame wakes the SBC
                // COM-39: %x with uint32_t - the OpenBSW formatter reads %lx as 64 bit value (printed
                // 0xa5a5a5a5000007ff on the board)
                (void)out.printf(
                    "SBC Sleep in %u ms, wake-up by CAN frame 0x%x mask 0x%x\r\n",
                    static_cast<unsigned>(SLEEP_DELAY_MS),
                    static_cast<uint32_t>(filter->id),
                    static_cast<uint32_t>(filter->idCompareMask));
                break;
            }
            (void)out.printf("SBC Sleep in %u ms, wake-up by CAN bus activity\r\n", static_cast<unsigned>(SLEEP_DELAY_MS));
            break;
        }
#endif
#ifndef NX_PRODUCT_BUILD
        case ID_FAILSTART:
        {
            uint32_t const count = context.scanIntToken<uint32_t>();
            if (context.check(count <= 3U) && context.checkEol())
            {
                bool armed         = false;
                auto const request = [&system, count, &armed]()
                {
                    armed = SbcEarlyStart::armFailStarts(static_cast<uint8_t>(count));
                    if (armed && (count != 0U))
                    {
                        system._sleepAtMs      = getSystemTimeMs32Bit();
                        system._resetRequested = true;
                    }
                };
                inSbcTask(request);
                ::util::format::SharedStringWriter out(context);
                if (!armed)
                {
                    (void)out.printf("failed starts not armed: no-init RAM not usable\r\n");
                }
                else if (count == 0U)
                {
                    (void)out.printf("failed starts disarmed\r\n");
                }
                else
                {
                    (void)out.printf(
                        "reset in %u ms, then %u failed start(s) before the SBC start\r\n",
                        static_cast<unsigned>(SLEEP_DELAY_MS),
                        static_cast<unsigned>(count));
                }
            }
            break;
        }
#endif
#ifndef NX_PRODUCT_BUILD // SYS-119
        case ID_PN:
        {
            ::util::string::ConstString const token = context.scanIdentifierToken();
            if (!context.check(token.compareIgnoreCase(::util::string::ConstString("arm")) == 0)
                || !context.checkEol())
            {
                break;
            }
            ::sbc::Uja113x::Status status = ::sbc::Uja113x::Status::NOT_READY;
            uint8_t canControl            = 0U;
            uint8_t transceiverStatus     = 0U;
            bool hasFrame                 = false;
            auto const arm = [&sbc, &status, &canControl, &transceiverStatus, &hasFrame]()
            {
                hasFrame = (sbc.getCanWakeupFrame() != nullptr);
                status   = sbc.armPartialNetworking();
                (void)sbc.readRegister(UJA113X_REG_CAN_CONTROL, canControl);
                (void)sbc.readRegister(UJA113X_REG_TRANSCEIVER_STATUS, transceiverStatus);
            };
            inSbcTask(arm);
            ::util::format::SharedStringWriter out(context);
            if (!hasFrame)
            {
                (void)out.printf("no CAN wake-up frame configured\r\n");
                break;
            }
            (void)out.printf(
                "SBC PN armed: %s, 0x20: 0x%02x, 0x22: 0x%02x (CPNERR %u CPNS %u COSCS %u)\r\n",
                statusName(status),
                static_cast<unsigned>(canControl),
                static_cast<unsigned>(transceiverStatus),
                static_cast<unsigned>(((transceiverStatus & UJA113X_CPNERR_MASK) != 0U) ? 1U : 0U),
                static_cast<unsigned>(((transceiverStatus & UJA113X_CPNS_MASK) != 0U) ? 1U : 0U),
                static_cast<unsigned>(((transceiverStatus & UJA113X_COSCS_MASK) != 0U) ? 1U : 0U));
            break;
        }
#endif
#ifndef NX_PRODUCT_BUILD // IO-68 board test, like sbc sleep not in the product image (SYS-119 S4)
        case ID_STANDBY:
        {
            uint32_t const ms = context.scanIntToken<uint32_t>();
            if (!context.check((ms >= STANDBY_MIN_MS) && (ms <= STANDBY_MAX_MS)) || !context.checkEol())
            {
                break;
            }
            bool accepted      = false;
            auto const request = [&system, &sbc, &accepted, ms]()
            {
                accepted = (sbc.getState() == ::sbc::Uja113x::State::NORMAL) && !system._standbyTest;
                if (accepted)
                {
                    system._standbyForMs     = ms;
                    system._standbyRequested = true;
                }
            };
            inSbcTask(request);
            ::util::format::SharedStringWriter out(context);
            if (!accepted)
            {
                (void)out.printf("SBC not in Normal mode\r\n");
                break;
            }
            (void)out.printf(
                "SBC Standby for at most %u ms, wake-up by CAN bus activity or a wake input, see log\r\n",
                static_cast<unsigned>(ms));
            break;
        }
#endif
        default:
        {
            break;
        }
    }
}

} // namespace systems
