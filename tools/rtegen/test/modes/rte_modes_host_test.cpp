// Host test of mode ports and mode switch runnables (RTE-08): ECU description test/modes/rte.yaml, BSW task profile
// test/multitask/tasks.yaml, synthetic components ModeSwc (EXPLICIT_ORDER group, values != ecustate::State numbers)
// and AlphaSwc (ALPHABETIC_ORDER group, mixed case, no access points) in the tasks fast (1 ms) and slow (10 ms).
// The ECU state comes from the real hand-over libs/bsw/ecuState EcuStateRte.cpp (ecustate::rte::publish). The
// harness plays the scheduler of systems::RteSystem: readInputs(task) -> due runnables -> writeOutputs(task).
// Checked: RTE_MODE_/RTE_TRANSITION_ values of both groups, mapping of every ECU state, the task view (changes only
// at the cycle start of its task), the direct reading of a port used in two tasks, entry of the initial mode,
// on-entry/on-exit/on-transition once per switch and in the order exit -> transition -> entry before the periodic
// runnables, RTE_TRANSITION_ while they run, the data hand-over of a value written by a mode switch runnable to
// another task (published at the end of the cycle), a skipped mode, an unknown state number.
// RTE-17: the table rte::MODE_RUNNABLES (one row per event: name, task, activation, mode), the plain call without a
// wrapper and the call of every row through the wrapper of the runner (setModeRunnableCall, RteSystem statistics).
// Build and run: tools/rtegen/test_rtegen.py (RteModeHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "RteConsole.h" // ecu/application/src/systems: the lines of "rte runnables|json" (RTE-17)
#include "Rte_Internal.h"
#include "ecuState/EcuStateRte.h"

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

extern "C"
{
#include "AlphaSwc.h"
#include "ModeSwc.h"
}

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

using ::ecustate::State;

std::vector<uint8_t> trace;
uint32_t ticks[2] = {0U, 0U};
size_t fast       = 0xFFU;
size_t slow       = 0xFFU;

bool preemptSlow         = false;   // hook 1 (inside OnRunEntry, after its write): run one fast cycle
uint32_t seenInPreemption = 0xFFFFU; // EntryCountIn as the preempting fast cycle read it

void cycle(size_t const task)
{
    ::rte::Task const& t = ::rte::TASKS[task];
    ::rte::readInputs(task);
    for (size_t i = t.firstRunnable; i < (t.firstRunnable + t.runnableCount); ++i)
    {
        if (::rte::isDue(::rte::RUNNABLES[i], ticks[task]))
        {
            ::rte::RUNNABLES[i].function();
        }
    }
    ::rte::writeOutputs(task);
    ticks[task] = ((ticks[task] + 1U) < t.hyperTicks) ? (ticks[task] + 1U) : 0U;
}

// RTE-17: rows of rte::MODE_RUNNABLES the RTE ran through the runner's wrapper (setModeRunnableCall)
std::vector<size_t> modeRows;
uint32_t modeCalls[::rte::MODE_RUNNABLE_COUNT] = {};

void wrapModeRunnable(size_t const index)
{
    modeRows.push_back(index);
    ++modeCalls[index];
    ::rte::MODE_RUNNABLES[index].function();
}

/** printf target of RteConsole.h (util::format::SharedStringWriter on the target). */
struct Out
{
    std::string text;

    int printf(char const* const format, ...)
    {
        char line[256];
        va_list args;
        va_start(args, format);
        int const n = std::vsnprintf(line, sizeof(line), format, args);
        va_end(args);
        text += line;
        return n;
    }
};

/**
 * Source of RteConsole.h over the generated table, as RteSystem.cpp builds it (takeModes): the counted calls of
 * the wrapper and made-up runtimes (10 us per row index + 5), which only have to arrive in the right line.
 */
struct ModeSource
{
    size_t taskCount() const { return ::rte::TASK_COUNT; }

    uint32_t taskContext(size_t const t) const { return ::rte::TASKS[t].context; }

    size_t modeCount(size_t const t) const
    {
        size_t count = 0U;
        for (size_t i = 0U; i < ::rte::MODE_RUNNABLE_COUNT; ++i)
        {
            count += (static_cast<size_t>(::rte::MODE_RUNNABLES[i].task) == t) ? 1U : 0U;
        }
        return count;
    }

    void takeModes(size_t const t, ::systems::rteconsole::ModeSnapshot* const modes) const
    {
        size_t n = 0U;
        for (size_t i = 0U; i < ::rte::MODE_RUNNABLE_COUNT; ++i)
        {
            ::rte::ModeRunnable const& config = ::rte::MODE_RUNNABLES[i];
            if (static_cast<size_t>(config.task) != t)
            {
                continue;
            }
            ::systems::rteconsole::ModeSnapshot& m = modes[n++];
            m.runnable                             = ::systems::rteconsole::RunnableSnapshot();
            m.runnable.name                        = config.name;
            m.runnable.calls                       = modeCalls[i];
            m.runnable.lastUs                      = static_cast<uint32_t>(10U * i + 5U);
            m.runnable.minUs                       = static_cast<uint32_t>(10U * i + 4U);
            m.runnable.meanUs                      = static_cast<uint32_t>(10U * i + 5U);
            m.runnable.maxUs                       = static_cast<uint32_t>(10U * i + 6U);
            m.task                                 = ::rte::TASKS[t].name;
            m.event = (config.activation == ::rte::ModeActivation::ON_ENTRY)  ? "entry"
                      : (config.activation == ::rte::ModeActivation::ON_EXIT) ? "exit"
                                                                              : "transition";
            m.mode     = config.mode;
            m.fromMode = (config.fromMode != nullptr) ? config.fromMode : "";
        }
    }
};

std::vector<uint32_t> contextsTaken;

/** One cycle of a task; returns the trace ids it produced. */
std::vector<uint8_t> run(size_t const task)
{
    trace.clear();
    modeRows.clear();
    cycle(task);
    return trace;
}

bool rowsAre(std::vector<size_t> const& expected)
{
    if (modeRows != expected)
    {
        std::printf("  mode rows:");
        for (size_t const row : modeRows)
        {
            std::printf(" %u", static_cast<unsigned>(row));
        }
        std::printf("\n");
        return false;
    }
    return true;
}

bool row(
    size_t const index,
    char const* const name,
    char const* const task,
    ::rte::ModeActivation const activation,
    char const* const mode,
    char const* const fromMode)
{
    ::rte::ModeRunnable const& r = ::rte::MODE_RUNNABLES[index];
    bool const from = (fromMode == nullptr) ? (r.fromMode == nullptr)
                                            : ((r.fromMode != nullptr) && (std::strcmp(r.fromMode, fromMode) == 0));
    return (std::strcmp(r.name, name) == 0) && (std::strcmp(::rte::TASKS[r.task].name, task) == 0)
           && (r.activation == activation) && (std::strcmp(r.mode, mode) == 0) && from && (r.function != nullptr);
}

bool is(std::vector<uint8_t> const& got, std::vector<uint8_t> const& expected)
{
    if (got != expected)
    {
        std::printf("  trace:");
        for (uint8_t const id : got)
        {
            std::printf(" %u", static_cast<unsigned>(id));
        }
        std::printf("\n");
        return false;
    }
    return true;
}

void publish(State const state) { ::ecustate::rte::publish(state, 0U); }

size_t taskIndex(char const* const name)
{
    for (size_t i = 0U; i < ::rte::TASK_COUNT; ++i)
    {
        if (std::strcmp(::rte::TASKS[i].name, name) == 0)
        {
            return i;
        }
    }
    return 0xFFU;
}
} // namespace

extern "C" void Test_Trace(uint8 const id) { trace.push_back(id); }

extern "C" void Test_Hook(uint8 const where)
{
    if ((where == 1U) && preemptSlow)
    {
        preemptSlow = false;
        cycle(fast);
        seenInPreemption = ModeSwc_FastEntryCount;
    }
}

int main()
{
    // ---- generated values: EXPLICIT_ORDER takes VALUE, ALPHABETIC_ORDER numbers the sorted names ----
    CHECK(RTE_MODE_EcuModes_STARTUP == 5U);
    CHECK(RTE_MODE_EcuModes_RUN == 10U);
    CHECK(RTE_MODE_EcuModes_STANDBY == 20U);
    CHECK(RTE_MODE_EcuModes_RUNDOWN == 30U);
    CHECK(RTE_MODE_EcuModes_SHUTDOWN == 40U);
    CHECK(RTE_MODE_EcuModes_SLEEP == 50U);
    CHECK(RTE_TRANSITION_EcuModes == 255U);
    CHECK(RTE_MODE_AlphaModes_Run == 0U);
    CHECK(RTE_MODE_AlphaModes_Rundown == 1U);
    CHECK(RTE_MODE_AlphaModes_Shutdown == 2U);
    CHECK(RTE_MODE_AlphaModes_Sleep == 3U);
    CHECK(RTE_MODE_AlphaModes_Standby == 4U);
    CHECK(RTE_MODE_AlphaModes_Startup == 5U);
    CHECK(RTE_TRANSITION_AlphaModes == 6U);
    CHECK(sizeof(Rte_ModeType_EcuModes) == 1U);

    // ---- tasks: the mode switch runnables are no periodic and no init runnables ----
    CHECK(::rte::TASK_COUNT == 2U);
    fast = taskIndex("fast");
    slow = taskIndex("slow");
    CHECK((fast == 0U) && (slow == 1U));
    CHECK(::rte::PERIODIC_RUNNABLE_COUNT == 3U);
    CHECK(::rte::INIT_RUNNABLE_COUNT == 0U);

    // ---- RTE-17: own table of the mode switch runnables, one row per event, grouped by task ----
    using ::rte::ModeActivation;
    CHECK(::rte::MODE_RUNNABLE_COUNT == 5U);
    CHECK(row(0U, "ModeSwc.OnStandbyToRun", "fast", ModeActivation::ON_TRANSITION, "RUN", "STANDBY"));
    CHECK(row(1U, "ModeSwc.OnStartupEntry", "slow", ModeActivation::ON_ENTRY, "STARTUP", nullptr));
    CHECK(row(2U, "ModeSwc.OnRunEntry", "slow", ModeActivation::ON_ENTRY, "RUN", nullptr));
    CHECK(row(3U, "ModeSwc.OnRunExit", "slow", ModeActivation::ON_EXIT, "RUN", nullptr));
    CHECK(row(4U, "AlphaSwc.OnStandbyExit", "slow", ModeActivation::ON_EXIT, "STANDBY", nullptr));
    CHECK(::rte::MODE_RUNNABLES[2].function == &ModeSwc_OnRunEntry);
    ::rte::init();

    // ---- before the first cycle: initial mode STARTUP everywhere ----
    ::ecustate::rte::reset();
    CHECK(Rte_Mode_ModeSwc_EcuMode_EcuMode() == RTE_MODE_EcuModes_STARTUP);
    CHECK(Rte_Mode_ModeSwc_SlowMode_EcuMode() == RTE_MODE_EcuModes_STARTUP);
    CHECK(Rte_Mode_ModeSwc_AnyMode_EcuMode() == RTE_MODE_EcuModes_STARTUP);
    CHECK(Rte_Mode_AlphaSwc_Mode_Mode() == RTE_MODE_AlphaModes_Startup);

    // ---- first cycles in STARTUP: on entry of the initial mode once, in the task it is mapped to ----
    // (without a wrapper: the RTE calls the function of the row itself, RTE-17)
    CHECK(is(run(slow), {10U, 2U, 20U}));
    CHECK(rowsAre({}));
    CHECK(ModeSwc_ModeInSwitch == RTE_TRANSITION_EcuModes); // the runnable ran inside the switch
    CHECK(ModeSwc_SlowMode == RTE_MODE_EcuModes_STARTUP);
    CHECK(AlphaSwc_Mode == RTE_MODE_AlphaModes_Startup);
    CHECK(is(run(slow), {2U, 20U}));
    CHECK(is(run(fast), {1U}));
    CHECK(ModeSwc_FastMode == RTE_MODE_EcuModes_STARTUP);

    // ---- from here on the runner wraps every mode switch runnable (RteSystem: statistics, RTE-17) ----
    ::rte::setModeRunnableCall(&wrapModeRunnable);

    // ---- STARTUP -> RUN ----
    publish(State::RUN);
    CHECK(Rte_Mode_ModeSwc_AnyMode_EcuMode() == RTE_MODE_EcuModes_RUN);        // port in two tasks: at once
    CHECK(Rte_Mode_ModeSwc_EcuMode_EcuMode() == RTE_MODE_EcuModes_STARTUP);    // task view: next cycle start
    CHECK(Rte_Mode_ModeSwc_SlowMode_EcuMode() == RTE_MODE_EcuModes_STARTUP);
    CHECK(is(run(fast), {1U}));                     // no STANDBY -> RUN transition
    CHECK(ModeSwc_FastMode == RTE_MODE_EcuModes_RUN);
    CHECK(ModeSwc_FastAny == RTE_MODE_EcuModes_RUN);
    CHECK(Rte_Mode_ModeSwc_SlowMode_EcuMode() == RTE_MODE_EcuModes_STARTUP);   // slow has not started a cycle
    int const locksBefore = async::LockType::count;
    preemptSlow           = true;
    CHECK(is(run(slow), {11U, 1U, 2U, 20U}));       // entry of RUN, the preempting fast cycle, periodic runnables
    CHECK(rowsAre({2U}));                           // through the wrapper, with its row
    CHECK(async::LockType::count == (locksBefore + 2)); // fast copy-in and slow publish; the mode switch takes none
    CHECK(async::LockType::depth == 0);
    CHECK(ModeSwc_ModeInSwitch == RTE_TRANSITION_EcuModes);
    CHECK(seenInPreemption == 0U);                  // written in the switch, published only at the cycle end
    CHECK(ModeSwc_SlowMode == RTE_MODE_EcuModes_RUN);
    CHECK(ModeSwc_SlowAny == RTE_MODE_EcuModes_RUN);
    CHECK(AlphaSwc_Mode == RTE_MODE_AlphaModes_Run);
    CHECK(Rte_Buf_ModeSwc_EntryCount_EntryCount == 1U);
    CHECK(is(run(fast), {1U}));
    CHECK(ModeSwc_FastEntryCount == 1U);
    CHECK(is(run(slow), {2U, 20U}));                // entered once, not per cycle
    CHECK(ModeSwc_EntryCount == 1U);

    // ---- RUN -> STANDBY: on exit of RUN before the periodic runnables ----
    publish(State::STANDBY);
    CHECK(is(run(fast), {1U}));
    CHECK(ModeSwc_FastMode == RTE_MODE_EcuModes_STANDBY);
    CHECK(is(run(slow), {12U, 2U, 20U}));
    CHECK(rowsAre({3U}));
    CHECK(ModeSwc_ModeInSwitch == RTE_TRANSITION_EcuModes);
    CHECK(AlphaSwc_Mode == RTE_MODE_AlphaModes_Standby);

    // ---- STANDBY -> RUN: every exit, then every transition, then every entry (not the rte.yaml list order) ----
    publish(State::RUN);
    CHECK(is(run(slow), {21U, 11U, 2U, 20U}));
    CHECK(rowsAre({4U, 2U}));
    CHECK(AlphaSwc_ModeInSwitch == RTE_TRANSITION_AlphaModes);
    CHECK(ModeSwc_EntryCount == 2U);
    CHECK(is(run(fast), {13U, 1U}));                // transition runs in its task, before the periodic runnable
    CHECK(rowsAre({0U}));
    CHECK(ModeSwc_ModeInSwitch == RTE_TRANSITION_EcuModes);
    CHECK(ModeSwc_FastMode == RTE_MODE_EcuModes_RUN);
    CHECK(ModeSwc_FastEntryCount == 2U);
    CHECK(is(run(fast), {1U}));

    // ---- a mode shorter than a task's cycle is not seen by that task (documented deviation) ----
    publish(State::STANDBY);
    publish(State::RUN);
    CHECK(is(run(fast), {1U}));
    CHECK(is(run(slow), {2U, 20U}));
    CHECK(ModeSwc_EntryCount == 2U);

    // ---- RUN -> RUNDOWN -> SHUTDOWN -> SLEEP: mapping of the remaining states ----
    publish(State::RUNDOWN);
    CHECK(Rte_Mode_ModeSwc_AnyMode_EcuMode() == RTE_MODE_EcuModes_RUNDOWN);
    CHECK(is(run(slow), {12U, 2U, 20U}));
    CHECK(ModeSwc_SlowMode == RTE_MODE_EcuModes_RUNDOWN);
    CHECK(AlphaSwc_Mode == RTE_MODE_AlphaModes_Rundown);
    publish(State::SHUTDOWN);
    CHECK(Rte_Mode_ModeSwc_AnyMode_EcuMode() == RTE_MODE_EcuModes_SHUTDOWN);
    CHECK(is(run(slow), {2U, 20U}));
    CHECK(AlphaSwc_Mode == RTE_MODE_AlphaModes_Shutdown);
    publish(State::SLEEP);
    CHECK(Rte_Mode_ModeSwc_AnyMode_EcuMode() == RTE_MODE_EcuModes_SLEEP);
    CHECK(is(run(slow), {2U, 20U}));
    CHECK(ModeSwc_SlowMode == RTE_MODE_EcuModes_SLEEP);
    CHECK(AlphaSwc_Mode == RTE_MODE_AlphaModes_Sleep);
    CHECK(is(run(fast), {1U}));
    CHECK(ModeSwc_FastMode == RTE_MODE_EcuModes_SLEEP);

    // ---- a state number the group does not know: the transition value, no runnable ----
    publish(static_cast<State>(9U));
    CHECK(Rte_Mode_ModeSwc_AnyMode_EcuMode() == RTE_TRANSITION_EcuModes);
    CHECK(is(run(slow), {2U, 20U}));
    CHECK(ModeSwc_SlowMode == RTE_TRANSITION_EcuModes);
    CHECK(AlphaSwc_Mode == RTE_TRANSITION_AlphaModes);
    // and back: the task leaves the unknown number without an exit runnable, enters RUN
    publish(State::RUN);
    CHECK(is(run(slow), {11U, 2U, 20U}));
    CHECK(ModeSwc_SlowMode == RTE_MODE_EcuModes_RUN);
    CHECK(async::LockType::depth == 0);

    // ---- RTE-17: console lines of the mode switch runnables (RteConsole.h), per task in its own context ----
    CHECK(modeCalls[0] == 1U); // STANDBY -> RUN in fast
    CHECK(modeCalls[1] == 0U); // STARTUP entry ran before the wrapper was set
    CHECK(modeCalls[2] == 3U); // RUN entered three times with the wrapper
    ModeSource source;
    ::systems::rteconsole::ModeSnapshot buffer[::rte::MODE_RUNNABLE_COUNT];
    auto const runIn = [](uint32_t const context, auto& work)
    {
        contextsTaken.push_back(context);
        work();
    };
    Out text;
    ::systems::rteconsole::writeModeStatistics(text, source, runIn, buffer, ::systems::rteconsole::Format::TEXT);
    CHECK((contextsTaken.size() == 2U) && (contextsTaken[0] == ::rte::TASKS[fast].context)
          && (contextsTaken[1] == ::rte::TASKS[slow].context));
    CHECK(text.text.find("mode ModeSwc.OnStandbyToRun (task fast): on transition STANDBY -> RUN, calls 1, last 5 us, "
                         "max 6 us\r\n")
          == 0U);
    CHECK(text.text.find("mode ModeSwc.OnRunExit (task slow): on exit RUN, calls ") != std::string::npos);
    Out json;
    ::systems::rteconsole::writeModeStatistics(json, source, runIn, buffer, ::systems::rteconsole::Format::JSON);
    CHECK(json.text.find("{\"t\":\"mode\",\"task\":\"slow\",\"name\":\"ModeSwc.OnRunEntry\",\"event\":\"entry\","
                         "\"mode\":\"RUN\",\"from\":\"\",\"calls\":3,")
          != std::string::npos);
    size_t longest = 0U;
    for (size_t start = 0U; start < text.text.size();)
    {
        size_t const end = text.text.find('\n', start) + 1U;
        longest          = ((end - start) > longest) ? (end - start) : longest;
        start            = end;
    }
    CHECK(longest <= 128U); // the lines of this profile fit a 128 byte line
    // for tools/rtegen/test_rtegen.py: the lines go on to the parser of tools/schedana
    std::printf("--- rte runnables (modes) ---\n%s--- rte json (modes) ---\n%s--- end ---\n", text.text.c_str(),
        json.text.c_str());

    std::printf("rte_modes_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
