/* Host test of the run-down step SAFE_OUTPUTS of the s32k1xx family port (SYS-174, safety review 2026-10-02, SYS69-S5,
 * finding ECS-F02; platforms/s32k1xx/port/main/src/integration/SafeOutputsShutdownStep.h) inside the run-down of the
 * ECU state management: the lock first, then the table walk, then the PWM inhibit handed to the debug halt guard; the
 * outputs stay off and every explicit enable is refused until the end of the run-down; it runs last, after NvM and
 * CAN, before the lifecycle shutdown. Table walk, outputs and PWM are fakes; no hardware.
 * Build and run: libs/bsw/ecuState/test/test_ecustate_host.py (zig c++). */

#include "DebugHaltGuard.h"
#include "SafeOutputsShutdownStep.h"
#include "ecuState/EcuStateController.h"
#include "ecuState/EcuStateMachine.h"
#include "ecuState/EcuStateRte.h"

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

using namespace ::ecustate;
using ::integration::DebugHaltGuard;
using ::integration::SafeOutputsShutdownStep;

constexpr uint8_t RUN_LEVEL      = 9U;
constexpr uint8_t SHUTDOWN_LEVEL = 1U;

std::vector<std::string> events; // order of everything that touches an output

/** The safe outputs table as the walk sees it: one PWM group and one safe_off output. */
struct Table
{
    bool pwmSafe     = false;
    bool safeOffOn   = true;
    uint32_t walks   = 0U;
} table;

void walk()
{
    ++table.walks;
    table.pwmSafe   = true;
    table.safeOffOn = false;
    events.push_back("walk");
}

/** Driver of the safe_off output: enables only while its lock (the integration's safeStateLock()) is open. */
struct Driver
{
    bool (*lock)()  = nullptr;
    bool enabled    = true;
    uint32_t refused = 0U;

    bool enable()
    {
        if ((lock != nullptr) && lock())
        {
            ++refused;
            return false;
        }
        enabled         = true;
        table.safeOffOn = true;
        events.push_back("enable");
        return true;
    }
};

Driver driver;

class Output final : public ::integration::IPowerStageOutput
{
public:
    void switchOffAfterDebugHalt() override
    {
        driver.enabled  = false;
        table.safeOffOn = false;
        _enabledSince   = false;
        events.push_back("output off");
    }

    bool enabledSinceDebugHaltSwitchOff() const override { return driver.enabled && !_enabledSince; }

    bool holdForSafeState() override { return true; }

private:
    bool _enabledSince = false;
};

class Pwm final : public ::integration::IPwmInhibit
{
public:
    bool inhibited    = false;
    uint32_t releases = 0U;

    void inhibit() override
    {
        inhibited = true;
        events.push_back("pwm inhibit");
    }

    void release() override
    {
        inhibited = false;
        ++releases;
    }
};

class FakeSupply final : public IPowerSupply
{
public:
    uint32_t powerDowns = 0U;

    char const* getName() const override { return "fake"; }

    bool isPowerDownSupported() const override { return true; }

    uint32_t getStartupWakeupSources() const override { return wakeup::POWER; }

    uint32_t takeWakeupEvents() override { return 0U; }

    uint32_t getSupportedWakeupSources() const override { return wakeup::CAN_BUSES; }

    uint32_t getCyclicWakeupMs(uint32_t const requestedMs) const override { return requestedMs; }

    PowerDownResult powerDown(uint32_t, WakeupSelection const&) override
    {
        ++powerDowns;
        events.push_back("power down");
        return PowerDownResult::STARTED;
    }
};

class Lifecycle final : public ILifecycleControl
{
public:
    std::vector<uint8_t> requests;
    uint8_t level                = RUN_LEVEL;
    uint8_t target               = RUN_LEVEL;
    EcuStateController* listener = nullptr;

    void transitionToLevel(uint8_t const l) override
    {
        requests.push_back(l);
        target = l;
        events.push_back("lifecycle " + std::to_string(l));
    }

    void step()
    {
        if (level != target)
        {
            bool const down = level > target;
            level           = down ? static_cast<uint8_t>(level - 1U) : static_cast<uint8_t>(level + 1U);
            listener->lifecycleLevelReached(level, down);
        }
    }
};

class CanStep final : public IShutdownStep
{
public:
    char const* getName() const override { return "CAN"; }

    Result start(uint32_t) override
    {
        events.push_back("CAN");
        return Result::DONE;
    }

    Result poll(uint32_t) override { return Result::DONE; }

    char const* getFailureReason() const override { return "-"; }
};

constexpr EcuStateConfig PROFILE{
    0U, 0U, 800U, 500U, {100U, 50U, SAFE_OUTPUTS_STEP_TIMEOUT_MS}, WakeupSelection{wakeup::canBus(0U), 0U},
    WakeupDuringShutdown::SLEEP, AfterWatchdogReset::RUN, ShutdownStepError::RESTART};

Pwm pwm;
Output output;
::integration::IPowerStageOutput* outputs[] = {&output};
DebugHaltGuard guard(outputs, 1U, &pwm);
SafeOutputsShutdownStep step(&walk, &guard);

// the integration's lock of the safe_off drivers (Integration.cpp safeStateLock(), S1 latch left out here)
bool safeStateLock() { return step.isLocking(); }

/** The step alone: lock first, walk, PWM inhibit to the guard, DONE at once; it reports its name as a literal. */
void stepAlone()
{
    events.clear();
    driver.lock = &safeStateLock;
    CHECK(!step.isLocking());
    CHECK(driver.enable()); // before the run-down: enabling works
    CHECK(std::strcmp(step.getName(), "OUTPUTS") == 0);
    CHECK(std::strcmp(step.getFailureReason(), "-") == 0);
    CHECK(step.start(0U) == IShutdownStep::Result::DONE);
    CHECK(step.isLocking());
    CHECK(step.getWalks() == 1U);
    CHECK(table.walks == 1U);
    CHECK(table.pwmSafe);
    CHECK(!table.safeOffOn);
    CHECK(pwm.inhibited);
    CHECK(guard.isInhibited());
    CHECK(!driver.enabled);
    // the application keeps running in RUNDOWN: its enable is refused, the guard keeps the PWM inhibited
    CHECK(!driver.enable());
    CHECK(driver.refused == 1U);
    CHECK(guard.cyclic(0U) == DebugHaltGuard::Event::NONE);
    CHECK(pwm.inhibited);
    CHECK(pwm.releases == 0U);
    CHECK(!table.safeOffOn);
    // abort() (default) changes nothing: the lock stays
    step.abort(10U);
    CHECK(step.isLocking());
}

/** A step without the table walk (integration error) fails and still locks. */
void stepWithoutWalkFails()
{
    Pwm p;
    Output o;
    ::integration::IPowerStageOutput* os[] = {&o};
    DebugHaltGuard g(os, 1U, &p);
    SafeOutputsShutdownStep broken(nullptr, &g);
    CHECK(broken.start(0U) == IShutdownStep::Result::FAILED);
    CHECK(broken.isLocking());
    CHECK(std::strcmp(broken.getFailureReason(), "no safe outputs walk") == 0);
    // a board without power stage outputs: no guard, the walk alone
    uint32_t const before = table.walks;
    SafeOutputsShutdownStep noGuard(&walk, nullptr);
    CHECK(noGuard.start(0U) == IShutdownStep::Result::DONE);
    CHECK(table.walks == before + 1U);
}

/** In the run-down of the ECU state management: after CAN, before the lifecycle shutdown and the power down. */
void inTheRunDown()
{
    events.clear();
    table           = Table{};
    driver          = Driver{};
    driver.lock     = &safeStateLock;
    FakeSupply supply;
    Lifecycle lifecycle;
    CanStep can;
    SafeOutputsShutdownStep runDownStep(&walk, nullptr);
    ShutdownSequence sequence;
    sequence.set(ShutdownStep::CAN_STOP, can);
    sequence.set(ShutdownStep::SAFE_OUTPUTS, runDownStep);
    ::ecustate::rte::reset();
    EcuStateController controller(PROFILE, supply, lifecycle, sequence, RUN_LEVEL, SHUTDOWN_LEVEL);
    lifecycle.listener = &controller;
    controller.lifecycleLevelReached(RUN_LEVEL, false);
    uint32_t now = 0U;
    for (; now < 100U; now += 10U)
    {
        controller.cyclic(now);
    }
    controller.setConsoleSleepRequest(true);
    for (; now < 2000U; now += 10U)
    {
        lifecycle.step();
        controller.cyclic(now);
    }
    CHECK(controller.getStateMachine().getStepState(ShutdownStep::SAFE_OUTPUTS) == StepState::DONE);
    CHECK(supply.powerDowns == 1U);
    std::string order;
    for (std::string const& e : events)
    {
        order += e + ";";
    }
    // (the fake MCU keeps running after the power down, so the sleep time-out restarts it afterwards - not shown here)
    std::string const expected = "CAN;walk;lifecycle 1;power down;";
    CHECK(order.compare(0U, expected.size(), expected) == 0);
    if (order.compare(0U, expected.size(), expected) != 0)
    {
        std::printf("  order: %s\n", order.c_str());
    }
    CHECK(!table.safeOffOn);
    CHECK(table.pwmSafe);
}

} // namespace

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"stepAlone", stepAlone},
        {"stepWithoutWalkFails", stepWithoutWalkFails},
        {"inTheRunDown", inTheRunDown},
    };
    for (auto const& test : tests)
    {
        int const before = failures;
        test.fn();
        std::printf("%s %s\n", (failures == before) ? "ok  " : "FAIL", test.name);
    }
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
