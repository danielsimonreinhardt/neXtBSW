#pragma once

#include "console/AsyncCommandWrapper.h"
#include "gd/Tmc6200.h"
#include "inputManager/DigitalInput.h"
#include "integration/SafeOffOutputs.h" // SYS-151: DRV_EN with its commanded level
#include "lifecycle/SingleContextLifecycleComponent.h"
#include "outputManager/Output.h"

#include <util/command/GroupCommand.h>

namespace systems
{

/**
 * DRV_EN and FAULT of a TMC6200 on the OpenBSW output and input manager (board.yaml devices[].connect).
 */
class BiosTmc6200Io : public ::gd::ITmc6200Io
{
public:
    BiosTmc6200Io(::bios::Output::OutputId const drvEn, ::bios::DigitalInput::DigitalInputId const fault)
    : _drvEn(drvEn), _fault(fault)
    {}

    /** SYS-151: the commanded level of DRV_EN first, then the pin (integration/SafeOffOutputs.h). */
    bool setDriverEnable(bool const on) override { return ::integration::safeoff::setCommandedOutput(_drvEn, on); }

    bool readFault(bool& active) override
    {
        return ::bios::DigitalInput::get(_fault, active) == ::bsp::BSP_OK;
    }

private:
    ::bios::Output::OutputId const _drvEn;
    ::bios::DigitalInput::DigitalInputId const _fault;
};

/** Names of one gate driver instance, chosen by the integration (static strings). */
struct GateDriverNames
{
    char const* command;     // console command group, e.g. "gd"
    char const* label;       // name in log messages, e.g. "MOT1_GD"
    char const* description; // help text of the command group
};

/**
 * IO-05: lifecycle component of one TMC6200 gate driver (libs/bsw/gdTmc6200).
 *
 * init: DRV_EN low, device check, configured register fields (gd::Tmc6200::init). Every cycleMs: supervision
 * (GSTAT, IOIN, FAULT, configuration), log of changes. shutdown: DRV_EN low.
 *
 * DRV_EN is never switched on here by itself: neither at start nor after a retry. Enabling the bridge is a request
 * of the application (getDriver().enable()) and on the eval board only allowed with the user on site
 * (MOT1_GD_DRV_EN, CLAUDE.md).
 *
 * Console "<command>": status, dump, read, write, clear, off. IO-34: only in a test build with the CMake option
 * NX_GD_ENABLE_COMMAND (default OFF) also "enable", which does nothing but getDriver().enable() (refused outside
 * DISABLED or with shutdown faults, DRV_EN checked through IOIN). The standard build has no console command that
 * switches DRV_EN on.
 */
class GateDriverSystem
: public ::lifecycle::SingleContextLifecycleComponent
, private ::async::RunnableType
{
public:
    GateDriverSystem(
        ::async::ContextType context,
        ::gd::ITmc6200Spi& spi,
        ::gd::ITmc6200Io& io,
        ::gd::Tmc6200Config const& config,
        uint32_t cycleMs,
        GateDriverNames const& names);
    GateDriverSystem(GateDriverSystem const&)            = delete;
    GateDriverSystem& operator=(GateDriverSystem const&) = delete;

    void init() override;
    void run() override;
    void shutdown() override;

    ::gd::Tmc6200& getDriver() { return _driver; }

    /** Latched fault bits (gd::fault) for the RTE signal gd.<device>.faults (proposed). */
    uint16_t getFaults() const { return _driver.getLatchedFaults(); }

private:
    class Command : public ::util::command::GroupCommand
    {
    public:
        explicit Command(GateDriverSystem& system);

    protected:
        PlainCommandInfo const* getInfo() const override { return _info; }

        void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;

    private:
        GateDriverSystem& _system;
#ifdef NX_GD_ENABLE_COMMAND
        PlainCommandInfo _info[9]; // IO-34: "enable", test builds only
#else
        PlainCommandInfo _info[8];
#endif
    };

    void execute() override;
    void logStartup(::gd::Tmc6200::Status status);
    void reportChanges();

    ::async::ContextType const _context;
    ::async::TimeoutType _timeout;
    GateDriverNames const _names;
    uint32_t const _cycleMs;
    ::gd::Tmc6200 _driver;
#if !defined(NX_CONSOLE) || NX_CONSOLE
    // PLAT-106: the console command only in an image with console (project.yaml console.enabled, NX_CONSOLE)
    Command _command;
    ::console::AsyncCommandWrapper _asyncCommand;
#endif
    ::gd::Tmc6200::State _reportedState;
    uint16_t _reportedFaults;
    uint32_t _reportedAttempts;
};

} // namespace systems
