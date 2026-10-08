// Host test of the generated RTE with two application tasks (RTE-02): ECU description test/multitask/rte.yaml,
// BSW task profile test/multitask/tasks.yaml, synthetic components MotorSwc (1 ms task "motor") and AppSwc
// (10 ms task "app"). The harness plays the scheduler of systems::RteSystem: readInputs(task) -> due runnables in
// table order (rte::isDue) -> writeOutputs(task), and simulates preemption at the hooks inside the runnables.
// Checked: task list and priorities, init hand-over, consistent values over the task boundary (two reads in one
// cycle, value pairs, preemption), publication only at the end of the writer's cycle, locked access of a port used
// by two tasks, offset and call order, BSW inputs/outputs in their task.
// Build and run: tools/rtegen/test_rtegen.py (RteMultiTaskHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "RteTasks.h"
#include "Rte_Internal.h"

#include <async/Config.h>

#include <cstdio>
#include <cstring>
#include <vector>

extern "C"
{
#include "AppSwc.h"
#include "MotorSwc.h"
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

struct Call
{
    uint8_t id;
    uint32_t ms;
};

std::vector<Call> trace;
uint32_t nowMs    = 0U;
uint32_t ticks[4] = {0U};
size_t motor      = 0xFFU;
size_t app        = 0xFFU;

bool preemptApp      = false; // hook 2 (inside AppSwc.Run10ms): run one motor cycle
bool probeMotor      = false; // hook 1 (inside MotorSwc.Run1ms, between the writes): look at the app side
uint32_t probeSpeed  = 0U;
double probeWide     = 0.0;
uint32_t probeGlobal = 0U;

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

extern "C" void Test_Trace(uint8 const id) { trace.push_back({id, nowMs}); }

extern "C" void Test_Hook(uint8 const where)
{
    if ((where == 2U) && preemptApp)
    {
        preemptApp = false;
        cycle(motor);
    }
    if ((where == 1U) && probeMotor)
    {
        // a reader starting now (e.g. a higher priority task) must still see the previous, consistent pair
        probeMotor = false;
        ::rte::readInputs(app);
        probeSpeed  = Rte_Cpy_app_MotorSwc_Speed_Speed;
        probeWide   = Rte_Cpy_app_MotorSwc_Wide_Wide;
        probeGlobal = Rte_Buf_MotorSwc_Speed_Speed;
    }
}

int main()
{
    // ---- generated task list (test profile: comm 20, app 25, motor 35, io 40, guard 90) ----
    CHECK(TASK_COMM == 1);
    CHECK(TASK_APP == 2);
    CHECK(TASK_MOTOR == 3);
    CHECK(TASK_IO == 4);
    CHECK(TASK_GUARD == 5);
    CHECK(ASYNC_CONFIG_TASK_COUNT == 6);
    CHECK(ASYNC_CONFIG_TICK_IN_US == 500U);
    CHECK(ISR_GROUP_ADC == 2);
    char const* const isrNames[ISR_GROUP_COUNT] = {ASYNC_CONFIG_ISR_GROUP_NAMES};
    CHECK(std::strcmp(isrNames[1], "comm") == 0);
    CHECK(RTE_APPLICATION_TASK_COUNT == 2U);
    CHECK(RTE_APPLICATION_STACK_BYTES == 3072U);

    CHECK(::rte::TASK_COUNT == 2U);
    motor = taskIndex("motor");
    app   = taskIndex("app");
    CHECK(motor == 0U);
    CHECK(app == 1U);
    CHECK(::rte::TASKS[motor].context == TASK_MOTOR);
    CHECK(::rte::TASKS[app].context == TASK_APP);
    CHECK(::rte::TASKS[motor].periodMs == 1U);
    CHECK(::rte::TASKS[app].periodMs == 10U);
    CHECK(::rte::TASKS[app].runnableCount == 3U);
    CHECK(::rte::TASKS[app].hyperTicks == 5U);
    CHECK(::rte::PERIODIC_RUNNABLE_COUNT == 4U);
    CHECK(::rte::INIT_RUNNABLE_COUNT == 2U);

    // ---- init runnables, then hand-over to all tasks ----
    for (size_t i = ::rte::PERIODIC_RUNNABLE_COUNT; i < ::rte::RUNNABLE_COUNT; ++i)
    {
        CHECK(::rte::RUNNABLES[i].task == ::rte::NO_TASK);
        ::rte::RUNNABLES[i].function();
    }
    ::rte::init();
    CHECK(async::LockType::depth == 0);
    CHECK(Rte_Buf_MotorSwc_Speed_Speed == 100U);       // written by MotorSwc_Init into the motor copy
    CHECK(Rte_Cpy_app_MotorSwc_Speed_Speed == 100U);   // already visible to the app task
    CHECK(Rte_Buf_AppSwc_Setpoint_Setpoint == 5U);     // AppSwc_Init

    // ---- 200 ms: motor every 1 ms, app every 10 ms (after the motor cycle of the same ms) ----
    uint32_t lastSetpoint     = 5U; // setpoint published by the last completed app cycle
    uint32_t slowCalls        = 0U;
    bool consistent           = true;
    bool pairs                = true;
    bool fresh                = true;
    bool setpointHandover     = true;
    bool diagSeesPublished    = true;
    bool ledFollows           = true;
    for (nowMs = 0U; nowMs < 200U; ++nowMs)
    {
        if (nowMs == 55U)
        {
            probeMotor = true;
        }
        if (nowMs == 120U)
        {
            bios::DigitalInput::value = true;
        }
        uint32_t const setpointBefore = lastSetpoint;
        cycle(motor);
        setpointHandover = setpointHandover && (MotorSwc_SetpointSeen == setpointBefore);
        if ((nowMs % 10U) == 0U)
        {
            uint32_t const motorCycles = MotorSwc_Cycles;
            preemptApp                 = (nowMs == 30U) || (nowMs == 150U);
            uint32_t const preemptedAt = MotorSwc_Cycles;
            cycle(app);
            bool const preempted = (MotorSwc_Cycles != preemptedAt);
            CHECK(preempted == ((nowMs == 30U) || (nowMs == 150U)));
            if (preempted)
            {
                // the motor cycle inside Run10ms ran before the app published its new setpoint
                CHECK(MotorSwc_SetpointSeen == setpointBefore);
            }
            consistent        = consistent && (AppSwc_Speed1 == AppSwc_Speed2);
            fresh             = fresh && (AppSwc_Speed1 == (1000U + motorCycles));
            pairs             = pairs && (AppSwc_Wide == (1.5 * static_cast<double>(AppSwc_Speed1)));
            diagSeesPublished = diagSeesPublished && (MotorSwc_DiagSetpoint == setpointBefore);
            ledFollows        = ledFollows && (bios::Output::level[0] == (AppSwc_Speed1 & 1U));
            lastSetpoint      = AppSwc_Speed1 & 0xFFFFU;
            CHECK(Rte_Buf_AppSwc_Setpoint_Setpoint == lastSetpoint); // published at the end of the app cycle
        }
        if (nowMs == 120U)
        {
            CHECK(MotorSwc_EnableSeen == 1U); // dio.in read at the start of the motor cycle
        }
    }
    CHECK(consistent);
    CHECK(fresh);
    CHECK(pairs);
    CHECK(setpointHandover);
    CHECK(diagSeesPublished);
    CHECK(ledFollows);
    CHECK(MotorSwc_Cycles == 202U); // 200 cycles + 2 preemptions

    // writer preempted between the two writes of the pair at 55 ms (motor cycle 57, one extra cycle at 30 ms):
    // the app side still sees the pair published by cycle 56
    CHECK(probeSpeed == (1000U + 56U));
    CHECK(probeWide == (1.5 * (1000.0 + 56.0)));
    CHECK(probeGlobal == (1000U + 56U));

    // ---- offset and call order in the app task ----
    std::vector<uint32_t> slow;
    bool order = true;
    unsigned appCycles = 0U;
    for (size_t i = 0U; i < trace.size(); ++i)
    {
        if (trace[i].id == 3U)
        {
            slow.push_back(trace[i].ms);
            ++slowCalls;
        }
        if (trace[i].id == 1U)
        {
            ++appCycles;
            order = order && ((i + 1U) < trace.size()) && (trace[i + 1U].id == 2U) && (trace[i + 1U].ms == trace[i].ms);
        }
    }
    CHECK(order);
    CHECK(appCycles == 20U);
    CHECK((slow.size() == 4U) && (slow[0] == 20U) && (slow[1] == 70U) && (slow[2] == 120U) && (slow[3] == 170U));
    CHECK(AppSwc_SlowCount == slowCalls);

    // ---- port used by two tasks: every access takes the lock ----
    int const before = async::LockType::count;
    uint16 value     = 0U;
    CHECK(Rte_Read_MotorSwc_Setpoint_Setpoint(&value) == RTE_E_OK);
    CHECK(async::LockType::count == (before + 1));
    CHECK(value == lastSetpoint);
    // the app task copies at its start and publishes at its end under one lock each
    int const beforeCycle = async::LockType::count;
    ::rte::readInputs(app);
    CHECK(async::LockType::count == (beforeCycle + 1));
    ::rte::writeOutputs(app);
    CHECK(async::LockType::count == (beforeCycle + 2));
    // single-task buffers have no copies: the motor task reads Enable directly, writes nothing but Speed/Wide
    int const beforeMotor = async::LockType::count;
    ::rte::readInputs(motor);
    CHECK(async::LockType::count == beforeMotor); // motor reads no copies (Setpoint is locked per access)
    CHECK(async::LockType::depth == 0);

    std::printf("rte_multitask_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
