/* Host test profile in place of the generated RteConfig.h (SYS-05, tools/rtegen).
 *
 * Deliberately not the eval board: two application tasks with freely chosen priorities and periods - "swc"
 * (context 5, 10 ms, two runnables of 10 ms and 100 ms) and "fast" (context 6, 1 ms, runnables of 1 ms and
 * 5 ms) - plus one init runnable. Same shape as the generated header, so the real RteSystem.cpp compiles
 * against it unchanged. No CAN, no mode switch runnables (NX_RTE_HAS_CAN / NX_RTE_HAS_MODE_RUNNABLES unset).
 *
 * The tables are defined in RteDeadlineHostTest.cpp, exactly as the generated RteConfig.cpp defines them. */

#pragma once

#include <cstddef>
#include <cstdint>

namespace rte
{

enum class SignalType : uint8_t
{
    BOOLEAN,
    UINT8,
    SINT8,
    UINT16,
    SINT16,
    UINT32,
    SINT32,
    FLOAT32,
    FLOAT64,
};

struct Signal
{
    char const* name;
    char const* bsw;
    SignalType type;
    void const* value;
};

constexpr uint8_t NO_TASK = 0xFFU;

struct Runnable
{
    char const* name;
    void (*function)();
    uint32_t periodMs; // 0: init runnable
    uint32_t offsetMs;
    uint8_t task;
    uint32_t periodTicks;
    uint32_t offsetTicks;
};

struct Task
{
    char const* name;
    uint8_t context;
    uint32_t periodMs;
    size_t firstRunnable;
    size_t runnableCount;
    uint32_t hyperTicks;
};

constexpr size_t TASK_COUNT               = 2U;
constexpr size_t PERIODIC_RUNNABLE_COUNT  = 4U;
constexpr size_t INIT_RUNNABLE_COUNT      = 1U;
constexpr size_t RUNNABLE_TABLE_SIZE      = PERIODIC_RUNNABLE_COUNT + INIT_RUNNABLE_COUNT;

extern Signal const SIGNALS[];
extern size_t const SIGNAL_COUNT;
extern Runnable const RUNNABLES[];
// RTE-19: compile-time constant as generated. NX_TEST_RUNNABLE_COUNT_EXCESS simulates a generator error (more
// rows than RUNNABLE_TABLE_SIZE): RteSystem.cpp must then refuse to compile (tools/rtegen/test_rtegen.py).
#ifndef NX_TEST_RUNNABLE_COUNT_EXCESS
#define NX_TEST_RUNNABLE_COUNT_EXCESS 0U
#endif
constexpr size_t RUNNABLE_COUNT = RUNNABLE_TABLE_SIZE + NX_TEST_RUNNABLE_COUNT_EXCESS;
extern Task const TASKS[];

/** true if the runnable is due in the task cycle with the given counter (0 .. hyperTicks - 1). */
inline bool isDue(Runnable const& runnable, uint32_t const tick)
{
    return (runnable.periodTicks != 0U) && ((tick % runnable.periodTicks) == runnable.offsetTicks);
}

void init();
void readInputs(size_t task);
void writeOutputs(size_t task);

} // namespace rte
