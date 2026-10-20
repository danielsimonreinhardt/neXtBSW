// Console group "shortlock" (SYS-172 board proof, open item P3 a): duration of the interrupt lock of the safe state
// selection (hardFaultHandler/safeStateLockMeasure.h). Test builds only: compiled with the CMake option
// NX_SHORT_LOCK_MEASURE_COMMAND=ON (default OFF, platforms/s32k1xx/port/main/CMakeLists.txt) in a project with an
// active short (NX_SAFE_OUTPUT_SELECT); a product build refuses the option (cmake/NxProductBuild.cmake, tools/elfcheck
// product). The start test measures Begin/End at power-on by itself; "select" and "block" call the service from the
// console so that Select and Block are measured on a board where no function selects or blocks.

#include <bsp/clock/boardClock.h>
#include <console/AsyncCommandWrapper.h>
#include <console/ConsoleContext.h>
#include <hardFaultHandler/safeStateLockMeasure.h>
#include <hardFaultHandler/safeStateSelection.h>
#include <util/command/CommandContext.h>
#include <util/command/GroupCommand.h>

#include <cstdint>
#include <cstdio>

namespace
{
enum Id
{
    ID_SHOW,
    ID_RESET,
    ID_SELECT,
    ID_BLOCK
};

// at most this many selects per command: the console context must not be held for long
constexpr uint32_t MAX_SELECTS = 1000U;

char const* const SITE_NAMES[NX_SAFE_STATE_LOCK_SITE_COUNT] = {"select", "block", "test begin", "test end"};

// cycles to nanoseconds at the core clock of the project (bsp/clock/boardClock.h)
unsigned long nanoseconds(uint32_t const cycles)
{
    return static_cast<unsigned long>((static_cast<uint64_t>(cycles) * 1000000000ULL) / NX_CLOCK_CORE_HZ);
}

/** Test build only (NX_SHORT_LOCK_MEASURE_COMMAND): duration of the lock of the safe state selection. */
class ShortLockMeasureCommand : public ::util::command::GroupCommand
{
protected:
    DECLARE_COMMAND_GROUP_GET_INFO
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;

private:
    static void show();
    static void select(::util::command::CommandContext& context);
    static void block(::util::command::CommandContext& context);
};

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(
    ShortLockMeasureCommand, "shortlock", "SYS-172 lock time of the safe state selection (test build only)")
COMMAND_GROUP_COMMAND(ID_SHOW, "show", "count, last and longest lock per call site in cycles and ns")
COMMAND_GROUP_COMMAND(ID_RESET, "reset", "clear the figures")
COMMAND_GROUP_COMMAND(
    ID_SELECT, "select", "call nxSafeStateSelect (param: <state 0|1|2> [<count 1..1000>] [<group>], default 1 0)")
COMMAND_GROUP_COMMAND(
    ID_BLOCK, "block", "call nxSafeStateBlockShort (param: [<group>], default 0) - short removed until power-on")
DEFINE_COMMAND_GROUP_GET_INFO_END

void ShortLockMeasureCommand::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    switch (idx)
    {
        case ID_SHOW:
        {
            if (context.checkEol())
            {
                show();
            }
            break;
        }
        case ID_RESET:
        {
            if (context.checkEol())
            {
                nxSafeStateLockStatReset();
                (void)printf("shortlock: figures cleared\r\n");
            }
            break;
        }
        case ID_SELECT:
        {
            select(context);
            break;
        }
        case ID_BLOCK:
        {
            block(context);
            break;
        }
        default:
        {
            break;
        }
    }
}

void ShortLockMeasureCommand::show()
{
    (void)printf(
        "shortlock: core %lu Hz, cycle counter %s\r\n",
        static_cast<unsigned long>(NX_CLOCK_CORE_HZ),
        (nxSafeStateLockCycleCounter() != 0U) ? "on" : "none (ARMv6-M: probe pin only)");
    for (uint32_t site = 0U; site < NX_SAFE_STATE_LOCK_SITE_COUNT; ++site)
    {
        NxSafeStateLockStat stat{0U, 0U, 0U};
        (void)nxSafeStateLockStatRead(site, &stat);
        (void)printf(
            "shortlock: %s: count %lu last %lu cyc (%lu ns) max %lu cyc (%lu ns)\r\n",
            SITE_NAMES[site],
            static_cast<unsigned long>(stat.count),
            static_cast<unsigned long>(stat.lastCycles),
            nanoseconds(stat.lastCycles),
            static_cast<unsigned long>(stat.maxCycles),
            nanoseconds(stat.maxCycles));
    }
    for (uint32_t group = 0U; group < nxSafeStateGroupCount(); ++group)
    {
        (void)printf(
            "shortlock: group %lu allowed 0x%lx selected %lu\r\n",
            static_cast<unsigned long>(group),
            static_cast<unsigned long>(nxSafeStateAllowed(group)),
            static_cast<unsigned long>(nxSafeStateSelected(group)));
    }
}

void ShortLockMeasureCommand::select(::util::command::CommandContext& context)
{
    uint32_t const state = context.scanIntToken<uint32_t>();
    uint32_t const count = context.hasToken() ? context.scanIntToken<uint32_t>() : 1U;
    uint32_t const group = context.hasToken() ? context.scanIntToken<uint32_t>() : 0U;
    if (!context.checkEol() || !context.check((count >= 1U) && (count <= MAX_SELECTS)))
    {
        return;
    }
    int32_t result = NX_SAFE_STATE_OK;
    for (uint32_t i = 0U; i < count; ++i)
    {
        result = nxSafeStateSelect(group, state);
    }
    (void)printf(
        "shortlock: select group %lu state %lu x%lu: %ld, selected %lu\r\n",
        static_cast<unsigned long>(group),
        static_cast<unsigned long>(state),
        static_cast<unsigned long>(count),
        static_cast<long>(result),
        static_cast<unsigned long>(nxSafeStateSelected(group)));
}

void ShortLockMeasureCommand::block(::util::command::CommandContext& context)
{
    uint32_t const group = context.hasToken() ? context.scanIntToken<uint32_t>() : 0U;
    if (!context.checkEol())
    {
        return;
    }
    nxSafeStateBlockShort(group);
    (void)printf(
        "shortlock: group %lu short blocked until power-on, allowed 0x%lx selected %lu\r\n",
        static_cast<unsigned long>(group),
        static_cast<unsigned long>(nxSafeStateAllowed(group)),
        static_cast<unsigned long>(nxSafeStateSelected(group)));
}

// Registers itself with the console during static initialization (like "inject"). Console context (IO-42).
ShortLockMeasureCommand shortLockMeasureCommand;
::console::AsyncCommandWrapper asyncShortLockMeasureCommand(shortLockMeasureCommand, ::console::CONSOLE_CONTEXT);
} // namespace
