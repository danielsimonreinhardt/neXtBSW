// Console group "shutdownhang" (SYS-173 board proof, SM-53): makes a lifecycle component of level 2 hang in its next
// shutdown, so that the lifecycle never reaches the shutdown level of the ECU state management. "ecu sleep" then runs
// into the shutdown time-out, the state management requests the restart (lifecycle level 0), the lifecycle is still
// stuck - and after shutdown_timeout_ms more the state management must reset the MCU directly
// (EcuStateController::superviseRestart(), the family hook ecuState/DirectReset.h).
//
// Test builds only: compiled with the CMake option NX_SHUTDOWN_HANG_COMMAND=ON (default OFF,
// platforms/s32k1xx/port/main/CMakeLists.txt), without console the option has no effect; a product build refuses it
// (cmake/NxProductBuild.cmake, tools/elfcheck product).
//
// - "arm" also puts a reporting wrapper in front of the family's direct reset: one synchronous console line
//   (safeConsoleWrite, the logger would not be drained any more), then the family's reset.
// - Fallback: the hang ends by itself after release_ms (default 30 s, 0 = never) with a console line saying that the
//   direct reset did not come. The lifecycle then goes on - a missing reset leaves no board that needs a hand.
// - "lc reboot" bypasses the ECU state management (OpenBSW lifecycle command, nothing supervises it, SYS-173 open
//   point): with the hang armed it runs into the fallback, it is no proof of SYS-173.

#include "ShutdownHang.h"

#include "async/Config.h"

#include <async/Async.h>
#include <bsp/timer/SystemTimer.h>
#include <console/AsyncCommandWrapper.h>
#include <console/ConsoleContext.h>
#include <ecuState/DirectReset.h>
#include <lifecycle/SingleContextLifecycleComponent.h>
#include <safeSupervisor/SafeConsole.h>
#include <util/command/CommandContext.h>
#include <util/command/GroupCommand.h>
#include <util/logger/Logger.h>

#include <atomic>
#include <cstdint>
#include <cstdio>

DECLARE_LOGGER_COMPONENT(BSP)

namespace
{
constexpr uint32_t DEFAULT_RELEASE_MS = 30000U;

// written by the console context, read by the component in TASK_BSP and by the reset wrapper (ecu task); plain
// loads and stores only (no exchange: ARMv6-M, PLAT-28)
std::atomic<bool> armed{false};
std::atomic<bool> hanging{false};
std::atomic<uint32_t> releaseMs{DEFAULT_RELEASE_MS};
std::atomic<uint32_t> hangStartMs{0U};
std::atomic<uint32_t> hangCount{0U};

// the family's direct reset, replaced by the wrapper at the first "arm" (nullptr: not replaced yet)
::ecustate::DirectResetFn familyReset = nullptr;
bool wrapperInstalled                 = false;

void reportingDirectReset()
{
    char line[120];
    uint32_t const now = getSystemTimeMs32Bit();
    int const length   = snprintf(
        line,
        sizeof(line),
        "shutdownhang: DIRECT RESET by the ECU state management at %lu ms, %lu ms after the hang began\r\n",
        static_cast<unsigned long>(now),
        static_cast<unsigned long>(now - hangStartMs.load()));
    if (length > 0)
    {
        uint32_t const size = (static_cast<uint32_t>(length) < sizeof(line)) ? static_cast<uint32_t>(length)
                                                                              : (sizeof(line) - 1U);
        (void)::safety::safeConsoleWrite(line, size);
    }
    if (familyReset != nullptr)
    {
        familyReset(); // does not return on a target
    }
}

/** Level-2 component of the test build: finishes every transition at once, unless armed for its shutdown. */
class ShutdownHangSystem final
: public ::lifecycle::SingleContextLifecycleComponent
, private ::async::RunnableType
{
public:
    explicit ShutdownHangSystem(::async::ContextType const context)
    : ::lifecycle::SingleContextLifecycleComponent(context), _context(context), _timeout()
    {}

    ShutdownHangSystem(ShutdownHangSystem const&)            = delete;
    ShutdownHangSystem& operator=(ShutdownHangSystem const&) = delete;

    void init() override { transitionDone(); }

    void run() override { transitionDone(); }

    void shutdown() override
    {
        if (!armed.load())
        {
            transitionDone();
            return;
        }
        uint32_t const release = releaseMs.load();
        hangStartMs.store(getSystemTimeMs32Bit());
        hangCount.store(hangCount.load() + 1U);
        hanging.store(true);
        ::util::logger::Logger::warn(
            ::util::logger::BSP,
            "shutdownhang: shutdown of level 2 held at %u ms (SYS-173 test), fallback release in %u ms",
            hangStartMs.load(),
            release);
        if (release != 0U)
        {
            ::async::schedule(_context, *this, _timeout, release, ::async::TimeUnit::MILLISECONDS);
        }
    }

    /** From any context: ends a hang in the context of the component (console "disarm"). */
    void releaseNow() { ::async::execute(_context, *this); }

private:
    // fallback release or "disarm": the shutdown goes on
    void execute() override
    {
        _timeout.cancel();
        if (!hanging.load())
        {
            return;
        }
        hanging.store(false);
        armed.store(false);
        ::util::logger::Logger::warn(
            ::util::logger::BSP,
            "shutdownhang: released after %u ms - the direct reset did not come before",
            static_cast<uint32_t>(getSystemTimeMs32Bit() - hangStartMs.load()));
        transitionDone();
    }

    ::async::ContextType const _context;
    ::async::TimeoutType _timeout;
};

ShutdownHangSystem hangSystem(TASK_BSP);

enum Id
{
    ID_ARM,
    ID_DISARM,
    ID_SHOW
};

/** Test build only (NX_SHUTDOWN_HANG_COMMAND): arms the hang of the level-2 component for the next shutdown. */
class ShutdownHangCommand : public ::util::command::GroupCommand
{
protected:
    DECLARE_COMMAND_GROUP_GET_INFO
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;
};

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(
    ShutdownHangCommand, "shutdownhang", "SYS-173 test: level-2 component hangs in its next shutdown (test build only)")
COMMAND_GROUP_COMMAND(
    ID_ARM,
    "arm",
    "hang in the next shutdown, then \"ecu sleep\" (param: [<release_ms>], default 30000, 0 = never)")
COMMAND_GROUP_COMMAND(ID_DISARM, "disarm", "no hang; a running hang ends now and the shutdown goes on")
COMMAND_GROUP_COMMAND(ID_SHOW, "show", "armed, hanging, time since the hang began")
DEFINE_COMMAND_GROUP_GET_INFO_END

void ShutdownHangCommand::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    switch (idx)
    {
        case ID_ARM:
        {
            uint32_t const release = context.hasToken() ? context.scanIntToken<uint32_t>() : DEFAULT_RELEASE_MS;
            if (!context.checkEol())
            {
                break;
            }
            if (!wrapperInstalled)
            {
                familyReset      = ::ecustate::directReset();
                wrapperInstalled = true;
                ::ecustate::setDirectReset(&reportingDirectReset);
            }
            releaseMs.store(release);
            armed.store(true);
            (void)printf(
                "shutdownhang: armed, release after %lu ms, direct reset of the family %s - now \"ecu sleep\" "
                "(\"lc reboot\" is not supervised)\r\n",
                static_cast<unsigned long>(release),
                (familyReset != nullptr) ? "present" : "MISSING");
            break;
        }
        case ID_DISARM:
        {
            if (!context.checkEol())
            {
                break;
            }
            armed.store(false);
            if (hanging.load())
            {
                hangSystem.releaseNow();
            }
            (void)printf("shutdownhang: disarmed\r\n");
            break;
        }
        case ID_SHOW:
        {
            if (!context.checkEol())
            {
                break;
            }
            bool const isHanging = hanging.load();
            (void)printf(
                "shutdownhang: armed %u, hanging %u (%lu ms), hangs %lu, release %lu ms\r\n",
                armed.load() ? 1U : 0U,
                isHanging ? 1U : 0U,
                isHanging ? static_cast<unsigned long>(getSystemTimeMs32Bit() - hangStartMs.load()) : 0UL,
                static_cast<unsigned long>(hangCount.load()),
                static_cast<unsigned long>(releaseMs.load()));
            break;
        }
        default:
        {
            break;
        }
    }
}

// Registers itself with the console during static initialization. Console context (IO-42).
ShutdownHangCommand shutdownHangCommand;
::console::AsyncCommandWrapper asyncShutdownHangCommand(shutdownHangCommand, ::console::CONSOLE_CONTEXT);
} // namespace

namespace integration
{
::lifecycle::ILifecycleComponent& shutdownHangComponent() { return hangSystem; }
} // namespace integration
