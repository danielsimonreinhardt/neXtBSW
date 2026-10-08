// Host test of array ports (RTE-25) against the generated RTE: ECU description test/arrays/rte.yaml on the build
// test project, two hand-written C components in two tasks. The harness plays the scheduler of systems::RteSystem:
// readInputs(task) -> due runnables in table order (rte::isDue) -> writeOutputs(task).
// Checked: the buffers have the full size of the array (dimension from the init data, 6 elements), the array crosses
// the task boundary as a whole (the reader sees one consistent cycle of the writer, never a mix), Rte_Read/Rte_Write of
// arrays in both components, an array written by the reader (CellEcho), and the signal table lists every element.
// Build and run: tools/rtegen/test_rtegen_c_swc.py (CArrayTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_CellSinkSwc.h"
#include "Rte_CellSrcSwc.h"
#include "Rte_Internal.h"

#include <cstdio>
#include <cstring>

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

size_t signalIndex(char const* const name)
{
    for (size_t i = 0U; i < ::rte::SIGNAL_COUNT; ++i)
    {
        if (std::strcmp(::rte::SIGNALS[i].name, name) == 0)
        {
            return i;
        }
    }
    return 0xFFFFU;
}
} // namespace

int main()
{
    size_t const fast = taskIndex("fast");
    size_t const slow = taskIndex("slow");
    CHECK(fast != 0xFFU);
    CHECK(slow != 0xFFU);

    // ---- the buffers have the full size: 6 elements of uint16 (pack.number_cells of CellSrcSwc_init.m) ----
    CHECK(sizeof(rt_Array_uint16_6) == (6U * sizeof(uint16)));
    CHECK(sizeof(Rte_Buf_CellSrcSwc_CellVoltages_CellVoltages) == (6U * sizeof(uint16)));
    CHECK(sizeof(Rte_Buf_CellSinkSwc_CellEcho_CellEcho) == (6U * sizeof(uint16)));

    // ---- signal table: element by element, each row at its element ----
    size_t const first = signalIndex("CellSrcSwc.CellVoltages.CellVoltages[0]");
    size_t const last  = signalIndex("CellSrcSwc.CellVoltages.CellVoltages[5]");
    CHECK(first != 0xFFFFU);
    CHECK(last == (first + 5U));
    CHECK(signalIndex("CellSrcSwc.CellVoltages.CellVoltages[6]") == 0xFFFFU);
    CHECK((last != 0xFFFFU) && (::rte::SIGNALS[last].value == &Rte_Buf_CellSrcSwc_CellVoltages_CellVoltages[5]));

    for (size_t i = ::rte::PERIODIC_RUNNABLE_COUNT; i < ::rte::RUNNABLE_COUNT; ++i)
    {
        ::rte::RUNNABLES[i].function();
    }
    ::rte::init();
    CHECK(async::LockType::depth == 0);

    // ---- 1. one run of the writer, then the reader: the whole array arrives, the reader writes arrays too ----
    Rte_Buf_CellSrcSwc_BaseVoltage_mV_BaseVoltage_mV = 3600U;
    cycle(fast);    // run 1: cells 3601, 3611, ... 3651
    cycle(slow);
    for (unsigned i = 0U; i < 6U; ++i)
    {
        CHECK(Rte_Buf_CellSrcSwc_CellVoltages_CellVoltages[i] == (3601U + (10U * i)));
        CHECK(Rte_Buf_CellSinkSwc_CellEcho_CellEcho[i] == (3601U + (10U * i)));
    }
    CHECK(Rte_Buf_CellSinkSwc_MaxCell_mV_MaxCell_mV == 3651U);
    CHECK(Rte_Buf_CellSinkSwc_MaxCellIndex_MaxCellIndex == 5U);

    // ---- 2. consistency across the task boundary: the reader works on the copy it took at its start ----
    // The writer runs twice while the reader's copy is taken once: every element the reader echoes belongs to the
    // same run of the writer (a mix of two runs would show as a difference other than 10 between neighbours).
    cycle(fast);    // run 2
    ::rte::readInputs(slow);           // the reader takes run 2 ...
    cycle(fast);    // run 3 publishes while the reader has not run yet
    for (size_t i = ::rte::TASKS[slow].firstRunnable;
         i < (::rte::TASKS[slow].firstRunnable + ::rte::TASKS[slow].runnableCount);
         ++i)
    {
        ::rte::RUNNABLES[i].function();
    }
    ::rte::writeOutputs(slow);
    for (unsigned i = 0U; i < 6U; ++i)
    {
        CHECK(Rte_Buf_CellSinkSwc_CellEcho_CellEcho[i] == (3602U + (10U * i)));    // ... and sees only run 2
        CHECK(Rte_Buf_CellSrcSwc_CellVoltages_CellVoltages[i] == (3603U + (10U * i)));
    }
    CHECK(Rte_Buf_CellSinkSwc_MaxCell_mV_MaxCell_mV == 3652U);

    // ---- 3. Rte_Read of a reader and Rte_Write of a writer directly: whole arrays, no element left behind ----
    uint16 got[6] = {0U, 0U, 0U, 0U, 0U, 0U};
    CHECK(Rte_Read_CellSinkSwc_CellVoltages_CellVoltages(got) == RTE_E_OK);
    CHECK((got[0] == 3602U) && (got[5] == 3652U));
    uint16 const echo[6] = {1U, 2U, 3U, 4U, 5U, 6U};
    CHECK(Rte_Write_CellSinkSwc_CellEcho_CellEcho(echo) == RTE_E_OK);
    ::rte::writeOutputs(slow);
    CHECK((Rte_Buf_CellSinkSwc_CellEcho_CellEcho[0] == 1U) && (Rte_Buf_CellSinkSwc_CellEcho_CellEcho[5] == 6U));
    CHECK(async::LockType::depth == 0);

    std::printf("rte_arrays_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
