// Host test of a hand-written C component against the generated RTE (RTE-20): ECU description test/c_swc/rte.yaml on
// the build test project, C component LevelMonSwc (swc.yaml implementation: c, src/LevelMonSwc.c) next to the Simulink
// component DemoSwc (Embedded Coder code) in two tasks. The harness plays the scheduler of systems::RteSystem:
// readInputs(task) -> due runnables in table order (rte::isDue) -> writeOutputs(task).
// Checked: runnable table (C functions <Swc>_<Runnable>, periods, offset, init runnables), hand-over in both directions
// between the two kinds of component (Simulink -> C over the task boundary, C -> Simulink in one task), a BSW output
// driven by the C component, the logic of LevelMonSwc through its ports (average, qualifier, hysteresis, debounce,
// range error, crossings, fixed-point gain) and calibration writes through rte::PARAMETERS.
// Build and run: tools/rtegen/test_rtegen_c_swc.py (CSwcHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_DemoSwc.h"
#include "Rte_Internal.h"
#include "Rte_LevelMonSwc.h"
#include "Rte_Qlf.h"

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
size_t fast       = 0xFFU;
size_t slow       = 0xFFU;

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

::rte::Parameter const* parameter(char const* const name)
{
    for (size_t i = 0U; i < ::rte::PARAMETER_COUNT; ++i)
    {
        if (std::strcmp(::rte::PARAMETERS[i].name, name) == 0)
        {
            return &::rte::PARAMETERS[i];
        }
    }
    return nullptr;
}

// one 10 ms step of the fast task with the level DemoSwc has published (the slow task does not run)
void fastWithLevel(uint32_t const level_mV)
{
    Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV = level_mV;
    cycle(fast);
}
} // namespace

int main()
{
    // ---- tables: C functions <Swc>_<Runnable>, Simulink functions as Embedded Coder names them ----
    fast = taskIndex("fast");
    slow = taskIndex("slow");
    CHECK(fast != 0xFFU);
    CHECK(slow != 0xFFU);
    ::rte::Runnable const* const run10   = runnable("LevelMonSwc.Run10ms");
    ::rte::Runnable const* const run100  = runnable("LevelMonSwc.Run100ms");
    ::rte::Runnable const* const init    = runnable("LevelMonSwc.LevelMonSwc_Init");
    ::rte::Runnable const* const demo10  = runnable("DemoSwc.Run10ms");
    CHECK((run10 != nullptr) && (run10->function == &LevelMonSwc_Run10ms) && (run10->periodMs == 10U));
    CHECK((run100 != nullptr) && (run100->function == &LevelMonSwc_Run100ms) && (run100->periodMs == 100U)
          && (run100->offsetMs == 50U) && (run100->task == fast));
    CHECK((init != nullptr) && (init->function == &LevelMonSwc_Init) && (init->task == ::rte::NO_TASK));
    CHECK((demo10 != nullptr) && (demo10->function == &Run10ms) && (demo10->task == fast));
    CHECK(::rte::INIT_RUNNABLE_COUNT == 2U);

    // ---- calibration parameters of the C component: values of LevelMonSwc_data.m, fixed point stored ----
    ::rte::Parameter const* const gain    = parameter("LevelMonSwc.CalPrm.Gain");
    ::rte::Parameter const* const weights = parameter("LevelMonSwc.CalPrm.Weights");
    ::rte::Parameter const* const debounceCycles = parameter("LevelMonSwc.CalPrm.DebounceCycles");
    CHECK((gain != nullptr) && (gain->denominator == 1000) && (gain->numerator[0] == 0) && (gain->numerator[1] == 1));
    CHECK(Rte_Prm_LevelMonSwc_CalPrm_Gain() == 1500U);
    CHECK((weights != nullptr) && (weights->count == 4U) && (Rte_Prm_LevelMonSwc_CalPrm_Weights()[0] == 4U));
    CHECK(Rte_Prm_LevelMonSwc_CalPrm_OnLevel_mV() == 3000U);

    // ---- init runnables, then hand-over ----
    for (size_t i = ::rte::PERIODIC_RUNNABLE_COUNT; i < ::rte::RUNNABLE_COUNT; ++i)
    {
        ::rte::RUNNABLES[i].function();
    }
    ::rte::init();
    CHECK(async::LockType::depth == 0);

    // ---- 1. both components: DemoSwc filters the ADC value (100 ms), LevelMonSwc watches it (10 ms) ----
    bios::AnalogInputScale::value = 3600U;
    bool led2Blinked              = false;
    for (uint32_t ms = 0U; ms < 4000U; ms += 10U)
    {
        if ((ms % 100U) == 0U)
        {
            cycle(slow);
        }
        cycle(fast);
        // Simulink -> C over the task boundary: what the fast task read is what slow published
        CHECK(Rte_Cpy_fast_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV
              == Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV);
        // C -> BSW: LED2 follows Above of LevelMonSwc
        CHECK(bios::Output::level[1] == Rte_Buf_LevelMonSwc_Above_Above);
        led2Blinked = led2Blinked || (Rte_Buf_DemoSwc_Led2_Led2 != 0U);
    }
    CHECK(Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV > 3500U);
    CHECK(Rte_Buf_LevelMonSwc_LevelQlf_LevelQlf == QLF_OK);
    CHECK(Rte_Buf_LevelMonSwc_Above_Above == 1U);
    CHECK(Rte_Buf_LevelMonSwc_Crossings_Crossings == 1U);
    CHECK(Rte_Buf_LevelMonSwc_Scaled_Scaled == (uint16_t)((Rte_Buf_LevelMonSwc_Filtered_mV_Filtered_mV * 3U) / 2U));
    // C -> Simulink: DemoSwc lets Led2 blink only while the qualifier of LevelMonSwc is QLF_OK
    CHECK(led2Blinked);

    // ---- 2. LevelMonSwc through its ports (fast task only, the level set as published by DemoSwc) ----
    LevelMonSwc_Init();
    for (uint32_t i = 0U; i < 3U; ++i)
    {
        fastWithLevel(1000U);
        CHECK(Rte_Buf_LevelMonSwc_LevelQlf_LevelQlf == QLF_DEBOUNCE); // average not filled yet
    }
    fastWithLevel(1000U);
    CHECK(Rte_Buf_LevelMonSwc_LevelQlf_LevelQlf == QLF_OK);
    CHECK(Rte_Buf_LevelMonSwc_Filtered_mV_Filtered_mV == 1000U);
    CHECK(Rte_Buf_LevelMonSwc_Above_Above == 0U);
    // weights 4 2 1 1 (newest first): 2000, 2500, 2750, 3000, then three cycles at OnLevel_mV (DebounceCycles 3)
    uint32_t const ramp[] = {2000U, 2500U, 2750U, 3000U, 3000U};
    for (uint32_t const expected : ramp)
    {
        fastWithLevel(3000U);
        CHECK(Rte_Buf_LevelMonSwc_Filtered_mV_Filtered_mV == expected);
        CHECK(Rte_Buf_LevelMonSwc_Above_Above == 0U);
    }
    fastWithLevel(3000U);
    CHECK(Rte_Buf_LevelMonSwc_Above_Above == 1U);
    CHECK(bios::Output::level[1] == 1U);
    // between the thresholds: stays on
    for (uint32_t i = 0U; i < 6U; ++i)
    {
        fastWithLevel(2600U);
        CHECK(Rte_Buf_LevelMonSwc_Above_Above == 1U);
    }
    // at OffLevel_mV: off at once (no debounce on the way down)
    uint32_t cyclesToOff = 0U;
    while ((Rte_Buf_LevelMonSwc_Above_Above != 0U) && (cyclesToOff < 10U))
    {
        fastWithLevel(2500U);
        ++cyclesToOff;
    }
    CHECK(Rte_Buf_LevelMonSwc_Filtered_mV_Filtered_mV == 2500U);
    CHECK(cyclesToOff == 4U); // 2550, 2525, 2512 (on) - 2500 (off)
    CHECK(bios::Output::level[1] == 0U);
    // implausible level: on the way up the average passes 3750, 4375, 4687 (a rising edge after three cycles at
    // OnLevel_mV), then 5000 > MaxLevel_mV: range error, Above off, and DemoSwc stops Led2 from the next cycle on
    bool led2DuringError = false;
    for (uint32_t i = 0U; i < 120U; ++i)
    {
        fastWithLevel(5000U);
        CHECK(Rte_Buf_LevelMonSwc_Above_Above == ((i == 2U) ? 1U : 0U));
        if (i >= 3U)
        {
            CHECK(Rte_Buf_LevelMonSwc_LevelQlf_LevelQlf == QLF_ERROR_RANGE);
        }
        if (i >= 4U)
        {
            led2DuringError = led2DuringError || (Rte_Buf_DemoSwc_Led2_Led2 != 0U);
        }
    }
    CHECK(!led2DuringError);
    // crossings and the scaled output are written by Run100ms (every 10th fast cycle, offset 50 ms)
    CHECK(Rte_Buf_LevelMonSwc_Crossings_Crossings == 2U); // since LevelMonSwc_Init: at 3000 mV and at 4687 mV
    CHECK(Rte_Buf_LevelMonSwc_Scaled_Scaled == 7500U);     // 5000 mV * 1.5

    // ---- 3. calibration writes through the table address (what XCP does, TOOL-01) ----
    *static_cast<uint16_t*>(gain->value)          = 2000U; // Gain 2.0
    *static_cast<uint8_t*>(debounceCycles->value) = 1U;
    fastWithLevel(4000U); // average 4500: plausible again, and one cycle at OnLevel_mV is enough now
    CHECK(Rte_Buf_LevelMonSwc_LevelQlf_LevelQlf == QLF_OK);
    CHECK(Rte_Buf_LevelMonSwc_Above_Above == 1U);
    for (uint32_t i = 0U; i < 20U; ++i)
    {
        fastWithLevel(4000U);
    }
    CHECK(Rte_Buf_LevelMonSwc_Filtered_mV_Filtered_mV == 4000U);
    CHECK(Rte_Buf_LevelMonSwc_Crossings_Crossings == 3U);
    CHECK(Rte_Buf_LevelMonSwc_Scaled_Scaled == 8000U);    // 4000 mV * 2.0
    // saturation of the scaled output at 0xFFFF (4800 mV * 20 would be 96000)
    *static_cast<uint16_t*>(gain->value) = 20000U;
    for (uint32_t i = 0U; i < 20U; ++i)
    {
        fastWithLevel(4800U);
    }
    CHECK(Rte_Buf_LevelMonSwc_Scaled_Scaled == 0xFFFFU);
    CHECK(async::LockType::depth == 0);

    std::printf("rte_c_swc_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
