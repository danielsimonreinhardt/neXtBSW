// Host test of disabled components and runnables (RTE-23): ECU description test/disabled/rte.yaml, BSW task profile
// test/multitask/tasks.yaml. LevelSrcSwc is disabled (components[].enabled: false), AuxSrcSwc.RunAux10ms is disabled
// (tasks[].runnables[].enabled: false), SinkSwc stays enabled and reads their values. The harness plays the scheduler
// of systems::RteSystem like test/unconnected: init runnables through rte::RUNNABLES -> rte::init() -> per cycle
// readInputs(task) -> due runnables -> writeOutputs(task).
// Checked: the disabled parts are not in rte::RUNNABLES and are never called (not even the init of the disabled
// component); the dependent runnable runs and reads the value of disabled_values (1234, 77), QLF_NOT_AVAILABLE for
// the qualifier port and for the age qualifier of the disabled writer; the running source still arrives; the buffers
// of the ports fed by a disabled part are const and keep their value.
// Build and run: tools/rtegen/test_rtegen_disabled.py (RteDisabledHostTest; also compiled and linked for the
// target by RteDisabledTargetTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "RteTasks.h"
#include "Rte_Internal.h"
#include "Rte_Qlf.h"

#include <async/Config.h>

#include <cstdio>
#include <cstring>
#include <type_traits>

extern "C"
{
extern uint16 LevelSrcSwc_Calls;
extern uint16 AuxSrcSwc_AuxCalls;
}

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;

// RTE-23: the buffers of the ports fed by a disabled part are constant, the running source is not
static_assert(std::is_const<decltype(Rte_Buf_SinkSwc_Level_mV_Level_mV)>::value, "Level_mV");
static_assert(std::is_const<decltype(Rte_Buf_SinkSwc_LevelQlf_LevelQlf)>::value, "LevelQlf");
static_assert(std::is_const<decltype(Rte_Buf_SinkSwc_Aux_Aux)>::value, "Aux");
static_assert(std::is_const<decltype(Rte_Buf_Age_LevelSrcSwc_Level_mV_Level_mV)>::value, "age of a disabled writer");
static_assert(!std::is_const<decltype(Rte_Buf_AuxSrcSwc_Main_Main)>::value, "a running source");

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

uint32_t ticks[2] = {0U, 0U};

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

bool listed(char const* const name)
{
    for (size_t i = 0U; i < ::rte::RUNNABLE_COUNT; ++i)
    {
        if (std::strcmp(::rte::RUNNABLES[i].name, name) == 0)
        {
            return true;
        }
    }
    return false;
}
} // namespace

int main()
{
    CHECK(::rte::TASK_COUNT == 2U);
    size_t const fast = taskIndex("fast");
    size_t const slow = taskIndex("slow");
    CHECK((fast != 0xFFU) && (slow != 0xFFU));
    if ((fast == 0xFFU) || (slow == 0xFFU))
    {
        std::printf("%u checks, %u failures\n", checks, failures);
        return 1;
    }

    // ---- the disabled parts are not in the runnable table; the task of the fast cycle keeps no runnable ----
    CHECK(::rte::PERIODIC_RUNNABLE_COUNT == 2U);  // AuxSrcSwc.RunMain10ms, SinkSwc.Run10ms
    CHECK(::rte::INIT_RUNNABLE_COUNT == 2U);      // AuxSrcSwc and SinkSwc, not LevelSrcSwc
    CHECK(!listed("LevelSrcSwc.Run10ms"));
    CHECK(!listed("LevelSrcSwc.LevelSrcSwc_Init"));
    CHECK(!listed("AuxSrcSwc.RunAux10ms"));
    CHECK(listed("AuxSrcSwc.RunMain10ms"));
    CHECK(listed("SinkSwc.Run10ms"));
    CHECK(::rte::TASKS[fast].runnableCount == 0U);

    // ---- the constant values, before any runnable ran ----
    CHECK(Rte_Buf_SinkSwc_Level_mV_Level_mV == 1234U);  // disabled_values of LevelSrcSwc
    CHECK(Rte_Buf_SinkSwc_Aux_Aux == 77U);              // disabled_values of AuxSrcSwc (runnable disabled)
    CHECK(QLF_NOT_AVAILABLE == 64U);
    CHECK(Rte_Buf_SinkSwc_LevelQlf_LevelQlf == QLF_NOT_AVAILABLE);
    CHECK(Rte_Buf_Age_LevelSrcSwc_Level_mV_Level_mV == QLF_NOT_AVAILABLE);

    for (size_t i = ::rte::PERIODIC_RUNNABLE_COUNT; i < ::rte::RUNNABLE_COUNT; ++i)
    {
        ::rte::RUNNABLES[i].function();
    }
    ::rte::init();

    // ---- three slow cycles, ten fast cycles each ----
    for (unsigned n = 0U; n < 3U; ++n)
    {
        for (unsigned k = 0U; k < 10U; ++k)
        {
            cycle(fast);
        }
        cycle(slow);
    }

    // the disabled parts never ran, not even the init of the disabled component
    CHECK(LevelSrcSwc_Calls == 0U);
    CHECK(AuxSrcSwc_AuxCalls == 0U);
    // the dependent runnable ran with the constant values and the honest qualifiers
    CHECK(Rte_Buf_SinkSwc_Runs_Runs == 3U);
    CHECK(Rte_Buf_SinkSwc_Echo_mV_Echo_mV == 1234U);
    CHECK(Rte_Buf_SinkSwc_EchoQlf_EchoQlf == QLF_NOT_AVAILABLE);
    CHECK(Rte_Buf_SinkSwc_EchoAge_EchoAge == QLF_NOT_AVAILABLE);
    CHECK(Rte_Buf_SinkSwc_EchoAux_EchoAux == 77U);
    // the enabled runnable of the same component still delivers (written before SinkSwc in the same cycle)
    CHECK(Rte_Buf_AuxSrcSwc_Main_Main == 3U);
    CHECK(Rte_Buf_SinkSwc_EchoMain_EchoMain == 3U);
    CHECK(async::LockType::depth == 0);
    // the constant buffers are unchanged
    CHECK(Rte_Buf_SinkSwc_Level_mV_Level_mV == 1234U);
    CHECK(Rte_Buf_SinkSwc_Aux_Aux == 77U);

    std::printf("RTE-23 disabled parts: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
