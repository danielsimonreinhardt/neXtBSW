// Host test of required ports without connection (RTE-27, RTE-28): ECU description test/unconnected/rte.yaml, BSW task
// profile test/multitask/tasks.yaml. ConsumerSwc (synthetic component of test/implicit, here without its producer,
// CountImp/CountExp with the ARXML init value 7 and Samples with 5, set by the test on a copy) and the C component
// InitSinkSwc read only unconnected inputs. The harness plays the scheduler of systems::RteSystem like
// test/implicit: readInputs(task) -> due runnables through rte::RUNNABLES -> writeOutputs(task).
// Checked: the runnables run and read the init value of the port - explicitly, implicitly (RTE-09 copies) and
// under the lock of a port read in two tasks; an unconnected qualifier reads QLF_NOT_AVAILABLE (64, RTE-28); the buffers of the
// unconnected ports are const (nobody writes them) and keep their value.
// Build and run: tools/rtegen/test_rtegen.py (RteUnconnectedHostTest).

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
#include "ConsumerSwc.h"
}

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;

// RTE-27: the buffers of unconnected ports are constant
static_assert(std::is_const<decltype(Rte_Buf_ConsumerSwc_CountImp_Count)>::value, "CountImp");
static_assert(std::is_const<decltype(Rte_Buf_ConsumerSwc_CountExp_Count)>::value, "CountExp");
static_assert(std::is_const<decltype(Rte_Buf_ConsumerSwc_Samples_Samples)>::value, "Samples");
static_assert(std::is_const<decltype(Rte_Buf_ConsumerSwc_Pair_Pair)>::value, "Pair");
static_assert(std::is_const<decltype(Rte_Buf_InitSinkSwc_Level_mV_Level_mV)>::value, "Level_mV");
static_assert(std::is_const<decltype(Rte_Buf_InitSinkSwc_LevelQlf_LevelQlf)>::value, "LevelQlf");
static_assert(!std::is_const<decltype(Rte_Buf_InitSinkSwc_Echo_mV_Echo_mV)>::value, "a written port");

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
size_t fast       = 0xFFU;
size_t slow       = 0xFFU;
unsigned hooks    = 0U;

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
} // namespace

extern "C" void Test_Hook(uint8 const where)
{
    // hooks 3 and 5 of ConsumerSwc: the higher priority task preempts - there is no writer, nothing changes
    if ((where == 3U) || (where == 5U))
    {
        ++hooks;
        cycle(fast);
    }
}

int main()
{
    CHECK(::rte::TASK_COUNT == 2U);
    fast = taskIndex("fast");
    slow = taskIndex("slow");
    CHECK((fast != 0xFFU) && (slow != 0xFFU));
    if ((fast == 0xFFU) || (slow == 0xFFU))
    {
        std::printf("%u checks, %u failures\n", checks, failures);
        return 1;
    }

    // ---- the init values, before any runnable ran ----
    CHECK(Rte_Buf_ConsumerSwc_CountImp_Count == 7U);     // ARXML init value of the port
    CHECK(Rte_Buf_ConsumerSwc_CountExp_Count == 7U);
    CHECK((Rte_Buf_ConsumerSwc_Samples_Samples[0] == 5U) && (Rte_Buf_ConsumerSwc_Samples_Samples[2] == 5U));
    CHECK(Rte_Buf_InitSinkSwc_Level_mV_Level_mV == 0U);  // swc.yaml has no init value: 0
    CHECK(QLF_NOT_AVAILABLE == 64U);                     // RTE-28: fixed value (NVM-19 relies on it)
    CHECK(Rte_Buf_InitSinkSwc_LevelQlf_LevelQlf == QLF_NOT_AVAILABLE);

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

    // the runnable with the unconnected inputs ran, with the init value and the honest qualifier
    CHECK(Rte_Buf_InitSinkSwc_Runs_Runs == 3U);
    CHECK(Rte_Buf_InitSinkSwc_Echo_mV_Echo_mV == 0U);
    CHECK(Rte_Buf_InitSinkSwc_EchoQlf_EchoQlf == QLF_NOT_AVAILABLE);
    // ConsumerSwc: explicit, implicit and locked reads all see the init value of the port
    CHECK(ConsumerSwc_FastImp == 7U);
    CHECK(ConsumerSwc_FastExp == 7U);
    CHECK(ConsumerSwc_ImpFirst == 7U);
    CHECK(ConsumerSwc_ImpSecond == 7U);
    CHECK(ConsumerSwc_ImpSameArray != 0U);                 // 5, 5, 5 before and after the preemption
    CHECK(ConsumerSwc_ImpSamePair != 0U);                  // {0, 0}
    CHECK(ConsumerSwc_ImpPairA == 0U);
    CHECK((ConsumerSwc_ExpFirst == 7U) && (ConsumerSwc_ExpSecond == 7U));
    CHECK(Rte_Buf_ConsumerSwc_Seen_Seen == 7U);            // written implicitly from what RunImp10ms read
    CHECK(hooks == 6U);
    CHECK(async::LockType::depth == 0);
    // the constant buffers are unchanged
    CHECK(Rte_Buf_ConsumerSwc_CountImp_Count == 7U);
    CHECK(Rte_Buf_InitSinkSwc_LevelQlf_LevelQlf == QLF_NOT_AVAILABLE);

    std::printf("RTE-27 unconnected ports: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
