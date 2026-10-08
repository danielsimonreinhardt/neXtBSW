#include "systems/GateDriverSystem.h"

#include "bsp/timer/SystemTimer.h"
#include "console/ConsoleContext.h"

#include <util/format/SharedStringWriter.h>
#include <util/logger/Logger.h>

DECLARE_LOGGER_COMPONENT(BSP)

namespace
{
enum Id : uint8_t
{
    ID_STATUS = 1U,
    ID_DUMP,
    ID_READ,
    ID_WRITE,
    ID_CLEAR,
    ID_OFF,
#ifdef NX_GD_ENABLE_COMMAND
    ID_ENABLE, // IO-34: test builds only
#endif
};

using Status = ::gd::Tmc6200::Status;
} // namespace

namespace systems
{
using ::util::logger::BSP;
using ::util::logger::Logger;

GateDriverSystem::GateDriverSystem(
    ::async::ContextType const context,
    ::gd::ITmc6200Spi& spi,
    ::gd::ITmc6200Io& io,
    ::gd::Tmc6200Config const& config,
    uint32_t const cycleMs,
    GateDriverNames const& names)
: ::lifecycle::SingleContextLifecycleComponent(context)
, _context(context)
, _timeout()
, _names(names)
, _cycleMs(cycleMs)
, _driver(spi, io, config)
#if !defined(NX_CONSOLE) || NX_CONSOLE
, _command(*this)
// SYS-13: the command runs in the console context, only the driver access runs in this system's task
, _asyncCommand(_command, ::console::CONSOLE_CONTEXT)
#endif
, _reportedState(::gd::Tmc6200::State::UNINITIALIZED)
, _reportedFaults(0U)
, _reportedAttempts(0U)
{}

void GateDriverSystem::init()
{
    Status const status = _driver.init(getSystemTimeMs32Bit());
    logStartup(status);
    _reportedState    = _driver.getState();
    _reportedFaults   = _driver.getLatchedFaults();
    _reportedAttempts = _driver.getCounters().startupAttempts;
    transitionDone();
}

void GateDriverSystem::run()
{
    ::async::scheduleAtFixedRate(_context, *this, _timeout, _cycleMs, ::async::TimeUnit::MILLISECONDS);
    transitionDone();
}

void GateDriverSystem::shutdown()
{
    _timeout.cancel();
    if (_driver.isEnabled())
    {
        Logger::info(BSP, "%s DRV_EN off (shutdown)", _names.label);
    }
    (void)_driver.disable();
    _reportedState = _driver.getState();
    transitionDone();
}

void GateDriverSystem::execute()
{
    _driver.cyclic(getSystemTimeMs32Bit());
    reportChanges();
}

void GateDriverSystem::logStartup(Status const status)
{
    auto const& s = _driver.getStartupInfo();
    if (status != Status::OK)
    {
        Logger::error(
            BSP,
            "%s TMC6200 startup failed: %s (IOIN 0x%08x), DRV_EN stays off",
            _names.label,
            ::gd::Tmc6200::statusName(status),
            static_cast<unsigned>(s.ioin));
        return;
    }
    // Two entries: as one line the arguments overflow a buffered logger entry (EntrySerializer "<?>", label
    // "<NULL>", board test 2026-09-18).
    Logger::info(
        BSP,
        "%s TMC6200 ready: version 0x%02x, GSTAT 0x%04x, %u register(s) written, DRV_EN off",
        _names.label,
        static_cast<unsigned>((s.ioin & TMC6200_IOIN_VERSION_MASK) >> TMC6200_IOIN_VERSION_SHIFT),
        static_cast<unsigned>(s.gstat),
        static_cast<unsigned>(s.writes));
    Logger::info(
        BSP,
        "%s TMC6200 GCONF 0x%02x, SHORT_CONF 0x%08x, DRV_CONF 0x%05x",
        _names.label,
        static_cast<unsigned>(s.gconf),
        static_cast<unsigned>(s.shortConf),
        static_cast<unsigned>(s.drvConf));
    uint16_t const faults = _driver.getFaults();
    if (faults != 0U)
    {
        Logger::warn(
            BSP, "%s startup faults 0x%04x (first: %s)", _names.label, faults, ::gd::Tmc6200::faultName(faults));
    }
}

void GateDriverSystem::reportChanges()
{
    uint32_t const attempts = _driver.getCounters().startupAttempts;
    ::gd::Tmc6200::State const state = _driver.getState();
    if ((attempts != _reportedAttempts) && ((state != _reportedState) || (state != ::gd::Tmc6200::State::FAILED)))
    {
        // new startup: retry after a failure or re-initialization after a device reset; repeated failures stay quiet
        logStartup((state == ::gd::Tmc6200::State::FAILED) ? Status::NOT_READY : Status::OK);
    }
    _reportedAttempts = attempts;
    if (state != _reportedState)
    {
        Logger::info(
            BSP,
            "%s %s -> %s",
            _names.label,
            ::gd::Tmc6200::stateName(_reportedState),
            ::gd::Tmc6200::stateName(state));
        _reportedState = state;
    }
    uint16_t const faults = _driver.getLatchedFaults();
    uint16_t const added  = static_cast<uint16_t>(faults & ~_reportedFaults);
    if (added != 0U)
    {
        Logger::error(
            BSP,
            "%s fault 0x%04x (new: %s), GSTAT 0x%04x, DRV_EN %s",
            _names.label,
            faults,
            ::gd::Tmc6200::faultName(added),
            static_cast<unsigned>(_driver.getLastGstat()),
            _driver.isEnabled() ? "on" : "off");
    }
    _reportedFaults = faults;
}

GateDriverSystem::Command::Command(GateDriverSystem& system)
: _system(system)
, _info{
      {system._names.command, system._names.description, 0U},
      {"status", "state, faults, GSTAT/IOIN, counters", ID_STATUS},
      {"dump", "read all documented registers", ID_DUMP},
      {"read", "read register (param: <address>)", ID_READ},
#ifndef NX_PRODUCT_BUILD // SYS-119: register write past the driver, not in the product image
      {"write", "write GCONF/GSTAT/SHORT_CONF/DRV_CONF (param: <address> <value>), only with DRV_EN off", ID_WRITE},
#endif
#ifndef NX_PRODUCT_BUILD // SYS-119 (review point S2): releases latched faults without a reset, not in the product
      {"clear", "clear latched faults and GSTAT flags (DRV_EN stays off)", ID_CLEAR},
#endif
      {"off", "DRV_EN off", ID_OFF},
#ifdef NX_GD_ENABLE_COMMAND
      // IO-34: test build only (NX_GD_ENABLE_COMMAND). SYS-17: the marker "(board rule)" at the end of the help
      // text says the command needs a person at the board; tools/docgen reads it for the console reference.
      {"enable",
       "TEST BUILD: DRV_EN on through the driver, only disabled, no faults (board rule)",
       ID_ENABLE},
#endif
      {nullptr, nullptr, 0U},
  }
{}

void GateDriverSystem::Command::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    // SYS-13: this runs in the console context (TASK_IDLE). The driver - SPI, state, the switching commands - is
    // used in the task of this system (inDriverTask), the output is printed here.
    GateDriverSystem& s     = _system;
    ::gd::Tmc6200& drv      = s._driver;
    char const* const label = s._names.label;
    auto const inDriverTask = [&s](auto& work) { ::console::runInOwnerContext(s._context, work); };
    switch (idx)
    {
        case ID_STATUS:
        {
            if (!context.checkEol())
            {
                break;
            }
            char const* state       = "";
            bool enabled            = false;
            uint32_t ioin           = 0U;
            uint32_t gstat          = 0U;
            uint16_t faults         = 0U;
            uint16_t latched        = 0U;
            ::gd::Tmc6200::Counters c{};
            auto const take = [&drv, &state, &enabled, &ioin, &gstat, &faults, &latched, &c]()
            {
                state   = ::gd::Tmc6200::stateName(drv.getState());
                enabled = drv.isEnabled();
                ioin    = drv.getLastIoin();
                gstat   = drv.getLastGstat();
                faults  = drv.getFaults();
                latched = drv.getLatchedFaults();
                c       = drv.getCounters();
            };
            inDriverTask(take);
            ::util::format::SharedStringWriter out(context);
            (void)out.printf(
                "%s %s, DRV_EN %s (IOIN %u), faults 0x%04x (first: %s), latched 0x%04x\r\n",
                label,
                state,
                enabled ? "on" : "off",
                ((ioin & TMC6200_IOIN_DRV_EN_MASK) != 0U) ? 1U : 0U,
                faults,
                ::gd::Tmc6200::faultName(faults),
                latched);
            (void)out.printf(
                "GSTAT 0x%04x, IOIN 0x%08x (inputs UL..WH 0x%02x, OT levels 0x%x)\r\n",
                static_cast<unsigned>(gstat),
                static_cast<unsigned>(ioin),
                static_cast<unsigned>(ioin & 0x3FU),
                static_cast<unsigned>((ioin >> 8U) & 0xFU));
            (void)out.printf(
                "counters: startups %u, SPI errors %u, read-back %u, device resets %u, shutdowns %u, enables %u\r\n",
                static_cast<unsigned>(c.startupAttempts),
                static_cast<unsigned>(c.spiErrors),
                static_cast<unsigned>(c.verifyErrors),
                static_cast<unsigned>(c.deviceResets),
                static_cast<unsigned>(c.shutdowns),
                static_cast<unsigned>(c.enables));
            break;
        }
        case ID_DUMP:
        {
            if (!context.checkEol())
            {
                break;
            }
            static uint8_t const addresses[] = {
                TMC6200_REG_GCONF,
                TMC6200_REG_GSTAT,
                TMC6200_REG_IOIN,
                TMC6200_REG_OTP_READ,
                TMC6200_REG_FACTORY_CONF,
                TMC6200_REG_SHORT_CONF,
                TMC6200_REG_DRV_CONF,
            };
            constexpr size_t COUNT = sizeof(addresses) / sizeof(addresses[0]);
            // one snapshot of all registers in the task of this system, then the lines here
            uint32_t values[COUNT]   = {};
            Status statuses[COUNT]   = {};
            auto const take          = [&drv, &values, &statuses]()
            {
                for (size_t i = 0U; i < COUNT; ++i)
                {
                    statuses[i] = drv.readRegister(addresses[i], values[i]);
                }
            };
            inDriverTask(take);
            ::util::format::SharedStringWriter out(context);
            for (size_t i = 0U; i < COUNT; ++i)
            {
                (void)out.printf(
                    "0x%02x: 0x%08x (%s)\r\n",
                    static_cast<unsigned>(addresses[i]),
                    static_cast<unsigned>(values[i]),
                    ::gd::Tmc6200::statusName(statuses[i]));
            }
            break;
        }
        case ID_READ:
        {
            uint32_t const address = context.scanIntToken<uint32_t>();
            if (context.check(address < 0x80U) && context.checkEol())
            {
                uint32_t value   = 0U;
                Status status    = Status::NOT_READY;
                auto const read  = [&drv, address, &value, &status]()
                { status = drv.readRegister(static_cast<uint8_t>(address), value); };
                inDriverTask(read);
                ::util::format::SharedStringWriter out(context);
                (void)out.printf(
                    "0x%02x: 0x%08x (%s)\r\n",
                    static_cast<unsigned>(address),
                    static_cast<unsigned>(value),
                    ::gd::Tmc6200::statusName(status));
            }
            break;
        }
#ifndef NX_PRODUCT_BUILD // SYS-119
        case ID_WRITE:
        {
            uint32_t const address = context.scanIntToken<uint32_t>();
            uint32_t const value   = context.scanIntToken<uint32_t>();
            if (context.check(address < 0x80U) && context.checkEol())
            {
                Status status    = Status::NOT_READY;
                auto const write = [&drv, address, value, &status]()
                { status = drv.writeRegister(static_cast<uint8_t>(address), value); };
                inDriverTask(write);
                ::util::format::SharedStringWriter out(context);
                (void)out.printf(
                    "0x%02x <- 0x%08x: %s\r\n",
                    static_cast<unsigned>(address),
                    static_cast<unsigned>(value),
                    ::gd::Tmc6200::statusName(status));
            }
            break;
        }
#endif
#ifndef NX_PRODUCT_BUILD // SYS-119 (S2)
        case ID_CLEAR:
#endif
        case ID_OFF:
#ifdef NX_GD_ENABLE_COMMAND
        case ID_ENABLE:
#endif
        {
            if (!context.checkEol())
            {
                break;
            }
            Status status      = Status::NOT_READY;
            char const* state  = "";
            uint16_t latched   = 0U;
            auto const command = [&s, &drv, idx, &status, &state, &latched]()
            {
#ifdef NX_GD_ENABLE_COMMAND
                // IO-34: enable() refuses outside DISABLED or with shutdown faults and checks DRV_EN through IOIN;
                // same call and reply as in the host test (libs/bsw/gdTmc6200/test/host, consoleEnableCommand)
                status = (idx == ID_CLEAR) ? drv.clearFaults() : (idx == ID_OFF) ? drv.disable() : drv.enable();
#elif !defined(NX_PRODUCT_BUILD)
                status = (idx == ID_CLEAR) ? drv.clearFaults() : drv.disable();
#else
                status = drv.disable(); // SYS-119 (S2): "off" only in the product image
#endif
                s.reportChanges();
                state   = ::gd::Tmc6200::stateName(drv.getState());
                latched = drv.getLatchedFaults();
            };
            inDriverTask(command);
            ::util::format::SharedStringWriter out(context);
            (void)out.printf(
                "%s %s: %s, state %s, faults 0x%04x\r\n",
                s._names.command,
#ifdef NX_GD_ENABLE_COMMAND
                (idx == ID_CLEAR) ? "clear" : ((idx == ID_OFF) ? "off" : "enable"),
#elif !defined(NX_PRODUCT_BUILD)
                (idx == ID_CLEAR) ? "clear" : "off",
#else
                "off", // SYS-119 (S2)
#endif
                ::gd::Tmc6200::statusName(status),
                state,
                latched);
            break;
        }
        default:
        {
            break;
        }
    }
}

} // namespace systems
