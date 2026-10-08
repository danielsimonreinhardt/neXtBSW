// Host test of the implicit access of the generated RTE (RTE-09): ECU description test/implicit/rte.yaml, BSW task
// profile test/multitask/tasks.yaml, synthetic components ProducerSwc (Rte_IWrite/Rte_IWriteRef in the 1 ms task
// "fast") and ConsumerSwc (Rte_IRead in "fast" and in the 10 ms task "slow", Rte_Read as the counter-check). The
// harness plays the scheduler of systems::RteSystem: readInputs(task) -> due runnables through rte::RUNNABLES ->
// writeOutputs(task), and simulates the preemption of "slow" by "fast" at hooks inside the runnables.
// Checked: the RTE calls a runnable with implicit access through its wrapper; an implicitly read value stays the
// same for the whole run of the runnable although the higher priority task publishes new values meanwhile, while
// two explicit reads of the same element in one run differ; implicitly written values (scalar, array through
// Rte_IWriteRef, record) become visible only when the runnable ends and are published as one set; a write copy
// starts with the current value; the runnable never runs under the lock, and the copies take at most one lock
// section per runnable (none if the source is a task copy).
// Build and run: tools/rtegen/test_rtegen.py (RteImplicitHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "RteTasks.h"
#include "Rte_Internal.h"

#include <async/Config.h>

#include <cstdio>
#include <cstring>

extern "C"
{
#include "ConsumerSwc.h"
#include "ProducerSwc.h"
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

uint32_t ticks[2] = {0U, 0U};
size_t fast       = 0xFFU;
size_t slow       = 0xFFU;

unsigned preemptAt3 = 0U;  // fast cycles to run at hook 3 (inside ConsumerSwc.RunImp10ms)
unsigned preemptAt5 = 0U;  // fast cycles to run at hook 5 (inside ConsumerSwc.RunExp10ms)
bool probeProducer  = false;

// probes
uint32_t publishedBeforePreemption = 0U;
uint32_t publishedAfterPreemption  = 0U;
bool seenUnpublishedAtHook4        = false;
bool samplesHiddenAtHook1          = false;
bool pairHiddenAtHook2             = false;
uint32_t pairStartB                = 0xFFFFFFFFU; // Pair.B in the write copy while Run1ms runs (before it writes B)
int lockDepthInRunnables           = 0; // highest lock depth seen inside a runnable (must stay 0)

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

::rte::Runnable const* runnable(char const* const name)
{
    for (size_t i = 0U; i < ::rte::RUNNABLE_COUNT; ++i)
    {
        if (std::strcmp(::rte::RUNNABLES[i].name, name) == 0)
        {
            return &::rte::RUNNABLES[i];
        }
    }
    return nullptr;
}

bool samplesAll(uint16 const* const samples, uint16 const value)
{
    return (samples[0] == value) && (samples[1] == value) && (samples[2] == value);
}
} // namespace

extern "C" void Test_Hook(uint8 const where)
{
    if (async::LockType::depth > lockDepthInRunnables)
    {
        lockDepthInRunnables = async::LockType::depth;
    }
    uint16 const previous = static_cast<uint16>(ProducerSwc_Cycles - 1U);
    if ((where == 1U) && probeProducer)
    {
        // Samples[0] is written in the copy, neither the task copy of fast nor the published value may show it
        samplesHiddenAtHook1 = (Rte_Imp_ProducerSwc_Run1ms_Samples_Samples[0] == static_cast<uint16>(ProducerSwc_Cycles))
                               && samplesAll(Rte_Cpy_fast_ProducerSwc_Samples_Samples, previous)
                               && samplesAll(Rte_Buf_ProducerSwc_Samples_Samples, previous)
                               && (Rte_Imp_ProducerSwc_Run1ms_Count_Count == (1000U + ProducerSwc_Cycles))
                               && (Rte_Cpy_fast_ProducerSwc_Count_Count == Rte_Buf_ProducerSwc_Count_Count);
    }
    if ((where == 2U) && probeProducer)
    {
        pairHiddenAtHook2 = (Rte_Imp_ProducerSwc_Run1ms_Pair_Pair.A == ProducerSwc_Cycles)
                            && (Rte_Cpy_fast_ProducerSwc_Pair_Pair.A == previous)
                            && (Rte_Buf_ProducerSwc_Pair_Pair.A == previous);
        // the write copy was loaded with the current value at the start: B still holds what the destination held
        pairStartB = Rte_Imp_ProducerSwc_Run1ms_Pair_Pair.B;
    }
    if (where == 3U)
    {
        publishedBeforePreemption = Rte_Buf_ProducerSwc_Count_Count;
        for (; preemptAt3 > 0U; --preemptAt3)
        {
            cycle(fast);
        }
        publishedAfterPreemption = Rte_Buf_ProducerSwc_Count_Count;
    }
    if (where == 4U)
    {
        seenUnpublishedAtHook4 = (Rte_Imp_ConsumerSwc_RunImp10ms_Seen_Seen == ConsumerSwc_ImpFirst)
                                 && (Rte_Buf_ConsumerSwc_Seen_Seen != ConsumerSwc_ImpFirst);
    }
    if (where == 5U)
    {
        for (; preemptAt5 > 0U; --preemptAt5)
        {
            cycle(fast);
        }
    }
}

int main()
{
    // ---- task list and the runnable table: runnables with implicit access are called through their wrapper ----
    CHECK(::rte::TASK_COUNT == 2U);
    fast = taskIndex("fast");
    slow = taskIndex("slow");
    CHECK(fast == 0U);
    CHECK(slow == 1U);
    CHECK(::rte::TASKS[fast].context == TASK_FAST);
    CHECK(::rte::TASKS[slow].periodMs == 10U);
    CHECK(::rte::PERIODIC_RUNNABLE_COUNT == 4U);
    CHECK(::rte::INIT_RUNNABLE_COUNT == 1U);
    ::rte::Runnable const* const run1ms = runnable("ProducerSwc.Run1ms");
    ::rte::Runnable const* const runImp = runnable("ConsumerSwc.RunImp10ms");
    ::rte::Runnable const* const runExp = runnable("ConsumerSwc.RunExp10ms");
    ::rte::Runnable const* const init   = runnable("ProducerSwc.ProducerSwc_Init");
    CHECK((run1ms != nullptr) && (runImp != nullptr) && (runExp != nullptr) && (init != nullptr));
    if ((run1ms == nullptr) || (runImp == nullptr) || (runExp == nullptr) || (init == nullptr))
    {
        std::printf("%u checks, %u failures\n", checks, failures);
        return 1;
    }
    CHECK(run1ms->function != &Run1ms);             // wrapper with the copies
    CHECK(runImp->function != &RunImp10ms);
    CHECK(init->function != &ProducerSwc_Init);
    CHECK(runExp->function == &RunExp10ms);         // explicit access only: the entry point itself

    // ---- init runnables (implicit write in the init runnable), then the hand-over to all tasks ----
    for (size_t i = ::rte::PERIODIC_RUNNABLE_COUNT; i < ::rte::RUNNABLE_COUNT; ++i)
    {
        ::rte::RUNNABLES[i].function();
    }
    ::rte::init();
    CHECK(Rte_Buf_ProducerSwc_Count_Count == 100U);
    CHECK(Rte_Cpy_fast_ProducerSwc_Count_Count == 100U); // the writer's task copy; slow has none (atomic ports)
    CHECK(Rte_Imp_ProducerSwc_ProducerSwc_Init_Count_Count == 100U);

    // ---- task fast, first cycle: writes become visible at the end of Run1ms, published at the end of the cycle ----
    probeProducer   = true;
    int const locks = async::LockType::count;
    cycle(fast);
    probeProducer = false;
    // no double locking: Run1ms copies from/to the task copy of fast (no lock), RunFast1ms loads CountImp from the
    // published value (one lock), Rte_Read CountExp (one lock), writeOutputs(fast) publishes (one lock)
    CHECK((async::LockType::count - locks) == 3);
    CHECK(samplesHiddenAtHook1);
    CHECK(pairHiddenAtHook2);
    CHECK(pairStartB == 0U);                        // the init value of the port
    CHECK(Rte_Buf_ProducerSwc_Count_Count == 1001U);
    CHECK(samplesAll(Rte_Buf_ProducerSwc_Samples_Samples, 1U));
    CHECK((Rte_Buf_ProducerSwc_Pair_Pair.A == 1U) && (Rte_Buf_ProducerSwc_Pair_Pair.B == 2U));
    // RunFast1ms runs after Run1ms in the same cycle, but fast publishes Count only at its end: both reads see the
    // value of before the cycle
    CHECK(ConsumerSwc_FastImp == 100U);
    CHECK(ConsumerSwc_FastExp == 100U);
    // second cycle: another writer of the task (here the harness) changed Pair.B in the task copy meanwhile; the
    // write copy of Run1ms starts with that value, not with what Run1ms wrote last time
    Rte_Cpy_fast_ProducerSwc_Pair_Pair.B = 777U;
    samplesHiddenAtHook1                 = false;
    pairHiddenAtHook2                    = false;
    probeProducer                        = true;
    cycle(fast);
    probeProducer = false;
    CHECK(pairStartB == 777U);
    CHECK(samplesHiddenAtHook1);
    CHECK(pairHiddenAtHook2);
    CHECK(Rte_Buf_ProducerSwc_Count_Count == 1002U);
    CHECK((Rte_Buf_ProducerSwc_Pair_Pair.A == 2U) && (Rte_Buf_ProducerSwc_Pair_Pair.B == 4U));
    CHECK(ConsumerSwc_FastImp == 1001U);

    // ---- task slow, preempted by fast inside both runnables ----
    preemptAt3 = 3U;
    preemptAt5 = 1U;
    cycle(slow);
    CHECK(publishedBeforePreemption == 1002U);
    CHECK(publishedAfterPreemption == 1005U);       // fast really published new values meanwhile
    CHECK(ConsumerSwc_ImpFirst == 1002U);
    CHECK(ConsumerSwc_ImpSecond == ConsumerSwc_ImpFirst); // implicit: stable for the whole run
    CHECK(ConsumerSwc_ExpFirst == 1005U);
    CHECK(ConsumerSwc_ExpSecond == 1006U);          // counter-check: explicit reads in one run differ
    CHECK(ConsumerSwc_ExpSecond != ConsumerSwc_ExpFirst);
    CHECK(ConsumerSwc_ImpSameArray);
    CHECK(ConsumerSwc_ImpSamePair);
    CHECK(ConsumerSwc_ImpPairA == 2U);              // task copy of slow, taken at its cycle start
    CHECK(seenUnpublishedAtHook4);                  // implicit write: not visible while the runnable runs
    CHECK(Rte_Buf_ConsumerSwc_Seen_Seen == 1002U);  // visible after it
    CHECK(lockDepthInRunnables == 0);               // no runnable runs under the lock
    CHECK(async::LockType::depth == 0);

    // ---- a second slow cycle without preemption: the copies are loaded anew ----
    cycle(slow);
    CHECK(ConsumerSwc_ImpFirst == 1006U);
    CHECK(ConsumerSwc_ImpSecond == 1006U);
    CHECK(ConsumerSwc_ExpFirst == ConsumerSwc_ExpSecond);
    CHECK(ConsumerSwc_ImpPairA == 6U);
    CHECK(Rte_Buf_ConsumerSwc_Seen_Seen == 1006U);

    std::printf("rte_implicit_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
