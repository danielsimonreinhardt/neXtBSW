/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "logger/logger.h"

#if NX_CONSOLE
#include <identity/Identity.h> // PLAT-98: application name of the log prefix
#endif

/* start: adding logger includes */

#include <lifecycle/LifecycleLogger.h>
#include <logger/ConsoleLogger.h>
#include <safeUtils/SafetyLogger.h>
#ifdef PLATFORM_SUPPORT_TRANSPORT
#include <transport/TpRouterLogger.h>
#endif // PLATFORM_SUPPORT_TRANSPORT
#ifdef PLATFORM_SUPPORT_CAN
#include <can/CanLogger.h>
#ifdef PLATFORM_SUPPORT_TRANSPORT
#include <docan/common/DoCanLogger.h>
#endif // PLATFORM_SUPPORT_TRANSPORT
#endif // PLATFORM_SUPPORT_CAN
#if defined(PLATFORM_SUPPORT_TRANSPORT) && defined(PLATFORM_SUPPORT_UDS)
#include <uds/UdsLogger.h>
#endif // PLATFORM_SUPPORT_UDS && TRANSPORT
#ifdef PLATFORM_SUPPORT_ETHERNET
#include <ethernet/EthernetLogger.h>
#include <lwipSocket/utils/LwipLogger.h>
#include <someip/defaultlogger.h>
#include <tcp/TcpLogger.h>
#include <udp/UdpLogger.h>
#ifdef PLATFORM_SUPPORT_TRANSPORT
#include <doip/server/DoIpServerLogger.h>
#endif // PLATFORM_SUPPORT_TRANSPORT
#endif // PLATFORM_SUPPORT_ETHERNET

/* end: adding logger includes */

DEFINE_LOGGER_COMPONENT(BSP);
DEFINE_LOGGER_COMPONENT(COMMON);
DEFINE_LOGGER_COMPONENT(DEMO);
DEFINE_LOGGER_COMPONENT(GLOBAL);
DEFINE_LOGGER_COMPONENT(SAFETY);
DEFINE_LOGGER_COMPONENT(ETHERNET);
DEFINE_LOGGER_COMPONENT(DOIP);
DEFINE_LOGGER_COMPONENT(UDS);
DEFINE_LOGGER_COMPONENT(LWIP);
DEFINE_LOGGER_COMPONENT(RUST);
DEFINE_LOGGER_COMPONENT(ROUTING);
DEFINE_LOGGER_COMPONENT(SOMEIP);

/* PLAT-27: console and logger are a function of the project (project.yaml console.enabled -> NX_CONSOLE). The logger
 * components above are defined in every image - the BSW libraries name them in their log calls. Without a logger
 * nobody calls ::util::logger::Logger::init(): Logger::isEnabled() is false for every component, a log call does
 * nothing, and neither ring buffer nor formatter nor the command "logger" is in the image. */
#if NX_CONSOLE

#include <async/AsyncBinding.h>
#include <console/AsyncCommandWrapper.h>
#include <logger/ComponentConfig.h>
#include <logger/ComponentMapping.h>
#include <logger/DefaultLoggerCommand.h>
#include <util/command/SimpleCommand.h>
#include <util/format/SharedStringWriter.h>

// SYS-12: instead of OpenBSW logger::LoggerComposition - same parts, plus the report of lost entries
#include "LossReportingLoggerComposition.h"

START_LOGGER_COMPONENT_MAPPING_INFO_TABLE(loggerComponentInfoTable)
/* start: adding logger components */
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, BSP, ::util::format::Color::DEFAULT_COLOR)
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, COMMON, ::util::format::Color::DEFAULT_COLOR)
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, DEMO, ::util::format::Color::DEFAULT_COLOR)
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, GLOBAL, ::util::format::Color::DEFAULT_COLOR)
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, LIFECYCLE, ::util::format::Color::DARK_GRAY)
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, CONSOLE, ::util::format::Color::DEFAULT_COLOR)
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, SAFETY, ::util::format::Color::DEFAULT_COLOR)
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, RUST, ::util::format::Color::GREEN)
#ifdef PLATFORM_SUPPORT_CAN
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, CAN, ::util::format::Color::LIGHT_BLUE)
#ifdef PLATFORM_SUPPORT_TRANSPORT
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, DOCAN, ::util::format::Color::LIGHT_GRAY)
#endif // PLATFORM_SUPPORT_TRANSPORT
#endif // PLATFORM_SUPPORT_CAN
#ifdef PLATFORM_SUPPORT_TRANSPORT
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, TPROUTER, ::util::format::Color::LIGHT_YELLOW)
#ifdef PLATFORM_SUPPORT_UDS
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, UDS, ::util::format::Color::LIGHT_YELLOW)
#endif // PLATFORM_SUPPORT_UDS
#endif // PLATFORM_SUPPORT_TRANSPORT
#ifdef PLATFORM_SUPPORT_ETHERNET
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, ETHERNET, ::util::format::Color::MAGENTA)
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, TCP, ::util::format::Color::CYAN)
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, UDP, ::util::format::Color::LIGHT_GRAY)
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, LWIP, ::util::format::Color::LIGHT_YELLOW)
LOGGER_COMPONENT_MAPPING_INFO(_NOLOGGING, SOMEIP, ::util::format::Color::LIGHT_CYAN)
#ifdef PLATFORM_SUPPORT_TRANSPORT
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, DOIP, ::util::format::Color::LIGHT_GREEN)
#endif // PLATFORM_SUPPORT_TRANSPORT
#endif // PLATFORM_SUPPORT_ETHERNET
#if defined(PLATFORM_SUPPORT_ETHERNET) && defined(PLATFORM_SUPPORT_CAN)
LOGGER_COMPONENT_MAPPING_INFO(_DEBUG, ROUTING, ::util::format::Color::LIGHT_CYAN)
#endif // defined(PLATFORM_SUPPORT_ETHERNET) && defined(PLATFORM_SUPPORT_CAN)

/* end: adding logger components */
END_LOGGER_COMPONENT_MAPPING_INFO_TABLE();

DEFINE_LOGGER_COMPONENT_MAPPING(
    LoggerComponentMappingType,
    loggerComponentMapping,
    loggerComponentInfoTable,
    ::util::logger::LevelInfo::getDefaultTable(),
    GLOBAL);

namespace logger
{
using ComponentConfigType = ComponentConfig<LoggerComponentMappingType::MappingSize>;
ComponentConfigType loggerComponentConfig(loggerComponentMapping);
// PLAT-98: the prefix of every log line is the project name (project.name, identity/Identity.h), not "RefApp"
LossReportingLoggerComposition loggerComposition(loggerComponentMapping, ::identity::LOG_NAME);

DefaultLoggerCommand loggerCommand(loggerComponentConfig);

uint32_t lostEntries() { return loggerComposition.getLostEntries(); }

namespace
{
/** SYS-12: "logger lost" - log entries the ring buffer dropped before the console printed them, since start. */
class LostEntriesCommand
{
public:
    explicit LostEntriesCommand(::util::command::ParentCommand& root)
    : _command(
        "lost",
        "log entries lost in the ring buffer since start. Usage: logger lost",
        ::util::command::SimpleCommand::ExecuteFunction::create<LostEntriesCommand, &LostEntriesCommand::execute>(
            *this))
    {
        root.addCommand(_command);
    }

private:
    void execute(::util::command::CommandContext& context)
    {
        if (context.checkEol())
        {
            // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg): SharedStringWriter::printf is printf-style
            (void)::util::format::SharedStringWriter(context).printf(
                "log entries lost: %u (buffer %u bytes)\n",
                static_cast<unsigned>(lostEntries()),
                static_cast<unsigned>(BufferBytesOf<BufferedLoggerOutputType>::VALUE));
        }
    }

    ::util::command::SimpleCommand _command;
};

// Constructed after loggerCommand (same translation unit, order of definition).
LostEntriesCommand lostEntriesCommand(loggerCommand.root());
} // namespace

::console::AsyncCommandWrapper
    asyncCommandWrapper(loggerCommand.root(), ::async::AsyncBinding::AdapterType::TASK_IDLE);

void init()
{
    // SYS-15: log lines and the console input line share the terminal (same context, TASK_IDLE)
    loggerComposition.setOutputInterleave(::console::consoleOutputInterleave());
    loggerComposition.start(
        LossReportingLoggerComposition::ConfigStart::create<ComponentConfigType, &ComponentConfigType::start>(
            loggerComponentConfig));
}

void run() { loggerComposition.run(); }

void flush()
{
    loggerComposition.stop(
        LossReportingLoggerComposition::ConfigStop::create<ComponentConfigType, &ComponentConfigType::shutdown>(
            loggerComponentConfig));
}

#else // NX_CONSOLE

namespace logger
{
// PLAT-27: no ring buffer, nothing lost ("ecu status" of EcuStateSystem). init/run/flush are not called (app.cpp).
uint32_t lostEntries() { return 0U; }

#endif // NX_CONSOLE

using ::util::logger::Logger;
using ::util::logger::LWIP;

// lwIP requires a C-style variadic callback; delegate to Logger's va_list overload.
// NOLINTBEGIN(cppcoreguidelines-pro-type-vararg)
extern "C" void log_lwipInfo(char const* message, ...)
{
    va_list ap;
    va_start(ap, message);
    Logger::log(LWIP, ::util::logger::LEVEL_INFO, message, ap);
    va_end(ap);
}

// NOLINTEND(cppcoreguidelines-pro-type-vararg)

extern "C" void log_lwipError(char const* message) { Logger::error(LWIP, message); }

} // namespace logger
