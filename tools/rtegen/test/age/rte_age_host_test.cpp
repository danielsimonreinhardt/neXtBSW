// Host test of the age supervision of the generated RTE (SYS-32, gap G3 of doc/safety/concept.md): ECU description
// test/age/rte.yaml on the build test project, BSW task profile test/multitask/tasks.yaml. CellPkgSwc (task dev,
// 10 ms) stands for a device package: its runnable Run20ms runs on time but writes the cell values and the current
// only while its "device" answers. CellAppSwc (task app, 10 ms) reads the cells and both age qualifiers
// (age.qlf.*) in the other task. The harness plays the scheduler of systems::RteSystem: readInputs(task) -> due
// runnables in table order (rte::isDue) -> writeOutputs(task); one step is a cycle of dev, then one of app.
// Checked: start (QLF_DEBOUNCE until the first write, QLF_ERROR_TIMEOUT when it never comes within one period),
// fresh values (QLF_OK also in the cycle between two writes), detection of a missing write at the end of the cycle
// it was due, the value held meanwhile, recovery with the next write, two signals of one runnable independent,
// a wider reading port, saturation over a long silence, the row in rte::SIGNALS.
// Build and run: tools/rtegen/test_rtegen_age.py (RteAgeHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"
#include "Rte_Qlf.h"

#include <cstdio>
#include <cstring>

extern "C"
{
#include "Rte_CellAppSwc.h"
#include "Rte_CellPkgSwc.h"

extern volatile uint8 CellPkgSwc_DeviceAnswers;
extern volatile uint8 CellPkgSwc_CurrentAnswers;
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
size_t app        = 0xFFU;
size_t dev        = 0xFFU;
bool devDue       = false; // CellPkgSwc.Run20ms ran in the last dev cycle

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

void cycle(size_t const task)
{
    ::rte::Task const& t = ::rte::TASKS[task];
    ::rte::readInputs(task);
    for (size_t i = t.firstRunnable; i < (t.firstRunnable + t.runnableCount); ++i)
    {
        bool const due = ::rte::isDue(::rte::RUNNABLES[i], ticks[task]);
        if (task == dev)
        {
            devDue = due;
        }
        if (due)
        {
            ::rte::RUNNABLES[i].function();
        }
    }
    ::rte::writeOutputs(task);
    ticks[task] = ((ticks[task] + 1U) < t.hyperTicks) ? (ticks[task] + 1U) : 0U;
}

// one 10 ms step: the writer's task, then the reader's task
void step()
{
    cycle(dev);
    cycle(app);
}

// what CellAppSwc saw in its last cycle
uint8_t cellsAge() { return Rte_Buf_CellAppSwc_SeenCellsAge_SeenCellsAge; }

uint16_t currentAge() { return Rte_Buf_CellAppSwc_SeenCurrentAge_SeenCurrentAge; }

uint16_t cell0() { return Rte_Buf_CellAppSwc_SeenCell0_mV_SeenCell0_mV; }

uint16_t runs() { return Rte_Buf_CellPkgSwc_Runs_Runs; }

bool signalRow(char const* const label, void const* const address)
{
    for (size_t i = 0U; i < ::rte::SIGNAL_COUNT; ++i)
    {
        if (std::strcmp(::rte::SIGNALS[i].name, label) == 0)
        {
            return ::rte::SIGNALS[i].value == address;
        }
    }
    return false;
}
} // namespace

int main()
{
    app = taskIndex("app");
    dev = taskIndex("dev");
    CHECK(app != 0xFFU);
    CHECK(dev != 0xFFU);
    CHECK(::rte::TASKS[dev].hyperTicks == 2U); // Run20ms in a 10 ms task: due every second cycle

    // ---- the qualifier buffers are signals like any other (console, XCP) and start as QLF_DEBOUNCE ----
    CHECK(signalRow("CellPkgSwc.Cells_mV.Cells_mV.Age", &Rte_Buf_Age_CellPkgSwc_Cells_mV_Cells_mV));
    CHECK(signalRow("CellPkgSwc.Current_mA.Current_mA.Age", &Rte_Buf_Age_CellPkgSwc_Current_mA_Current_mA));
    CHECK(Rte_Buf_Age_CellPkgSwc_Cells_mV_Cells_mV == QLF_DEBOUNCE);

    for (size_t i = ::rte::PERIODIC_RUNNABLE_COUNT; i < ::rte::RUNNABLE_COUNT; ++i)
    {
        ::rte::RUNNABLES[i].function();
    }
    ::rte::init();
    CHECK(async::LockType::depth == 0);

    // ---- 1. start without an answer: not valid yet, then stale after one period although never written ----
    CellPkgSwc_DeviceAnswers = 0U;
    step(); // dev tick 0: Run20ms runs, writes nothing
    CHECK(devDue);
    CHECK(runs() == 1U);
    CHECK(cellsAge() == QLF_DEBOUNCE);
    CHECK(currentAge() == QLF_DEBOUNCE);
    step(); // dev tick 1: not due; one period without a value has passed
    CHECK(!devDue);
    CHECK(cellsAge() == QLF_ERROR_TIMEOUT);
    CHECK(currentAge() == QLF_ERROR_TIMEOUT);
    CHECK(cell0() == 0U); // init value of the port, nothing was ever written

    // ---- 2. the device answers: fresh with the first write, and stays fresh between two writes ----
    CellPkgSwc_DeviceAnswers = 1U;
    step(); // tick 0: written
    CHECK(devDue);
    CHECK(cellsAge() == QLF_OK);
    CHECK(currentAge() == QLF_OK);
    CHECK(cell0() == 3600U);
    for (unsigned i = 0U; i < 20U; ++i)
    {
        step();
        CHECK(cellsAge() == QLF_OK);
        CHECK(currentAge() == QLF_OK);
    }
    CHECK(runs() == 12U);
    uint16_t const last = cell0();
    CHECK(last == 3610U); // 11 writes: 3600 .. 3610
    CHECK(ticks[dev] == 1U); // the last write was in the previous dev cycle

    // ---- 3. the device stops answering: the runnable runs on (SM-05 sees nothing), the value ages ----
    CellPkgSwc_DeviceAnswers = 0U;
    step(); // not due: one cycle since the last write - within the deadline
    CHECK(!devDue);
    CHECK(cellsAge() == QLF_OK);
    step(); // due, no write: one period of the writer without a value -> stale at the end of this cycle
    CHECK(devDue);
    CHECK(cellsAge() == QLF_ERROR_TIMEOUT);
    CHECK(currentAge() == QLF_ERROR_TIMEOUT);
    CHECK(cell0() == last); // the value is held (on_error hold)
    uint16_t const runsBefore = runs();
    for (unsigned i = 0U; i < 1001U; ++i) // long silence: the counter saturates, no wrap back to QLF_OK
    {
        step();
        CHECK(cellsAge() == QLF_ERROR_TIMEOUT);
    }
    CHECK(runs() == static_cast<uint16_t>(runsBefore + 500U));
    CHECK(cell0() == last);

    // ---- 4. recovery with the next write (Q7 of the signal qualifier) ----
    CellPkgSwc_DeviceAnswers = 1U;
    step();
    CHECK(devDue);
    CHECK(cellsAge() == QLF_OK);
    CHECK(currentAge() == QLF_OK);
    CHECK(cell0() == static_cast<uint16_t>(last + 1U));

    // ---- 5. two values of one runnable are supervised each for itself ----
    CellPkgSwc_CurrentAnswers = 0U;
    step();
    step();
    step();
    CHECK(cellsAge() == QLF_OK);
    CHECK(currentAge() == QLF_ERROR_TIMEOUT); // wider port (uint16) carries the same value
    CellPkgSwc_CurrentAnswers = 1U;
    step();
    CHECK(devDue);
    CHECK(currentAge() == QLF_OK);

    // ---- 6. an extra write in between is no harm: the next missing one is still found ----
    // (the age counts from the last write, whoever made it in the writer's task)
    uint16 const extra[4] = {1U, 2U, 3U, 4U};
    (void)Rte_Write_CellPkgSwc_Cells_mV_Cells_mV(extra);
    CellPkgSwc_DeviceAnswers = 0U;
    step(); // not due; the extra write counts as a write in this cycle
    CHECK(!devDue);
    CHECK(cellsAge() == QLF_OK);
    step(); // due, no write: one cycle since the extra write
    CHECK(devDue);
    CHECK(cellsAge() == QLF_OK);
    step(); // two cycles without a write
    CHECK(cellsAge() == QLF_ERROR_TIMEOUT);
    CHECK(cell0() == 1U); // the extra value reached the reader and is held

    CHECK(async::LockType::depth == 0);
    std::printf("rte age host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
