/* IO-32: host test of the debug halt guard of the power stage (DebugHaltGuard.h) and of its power stage outputs:
 * the TMC6200 DRV_EN (Tmc6200PowerStageOutput.h) and, since IO-37, the IN of a smart high-side switch - since
 * ARCH-66 through the generic adapter of the generated binding (integration::SafeOffOutput, ModuleBinding.h) with
 * the access struct of the module (highSideSwitch/HighSideSwitchSafeOff.h), which replaced
 * HighSideSwitchPowerStageOutput.h with the same assertions - both with the real drivers of libs/bsw on small device
 * models.
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/main/src/integration -Ilibs/bsw/gdTmc6200/include \
 *       -Ilibs/bsw/highSideSwitch/include \
 *       libs/bsw/gdTmc6200/src/gd/Tmc6200.cpp libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitch.cpp \
 *       libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitchChannel.cpp \
 *       platforms/s32k1xx/port/main/src/integration/test/host/DebugHaltGuardHostTest.cpp -o build/host/haltguard.exe
 *   build/host/haltguard.exe
 */

#include "DebugHaltGuard.h"
#include "ModuleBinding.h"
#include "highSideSwitch/HighSideSwitchSafeOff.h"
#include "Tmc6200PowerStageOutput.h"

#include <cstdio>
#include <cstring>
#include <string>

/* IO-32 (deadman): the walk over the safe outputs table that the deadman interrupt uses on the target is the real
 * code here (hardFaultHandler/safeOutputs.h, static inline). Its register access is redirected to the register
 * file of this test, so that the loop, the gate check and the order are the ones of the target. The header is
 * reached by a relative path because the test profile of tools/test only adds the include directories of the
 * integration and of the gate driver. */
namespace test_registers
{
volatile uint32_t* reg(uint32_t address);
}
#define NX_SAFE_OUTPUT_REG(address) (::test_registers::reg(address))
#include "../../../../../../hardFaultHandler/include/hardFaultHandler/safeOutputs.h"

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                                  \
    do                                                                               \
    {                                                                                \
        ++checks;                                                                    \
        if (!(cond))                                                                 \
        {                                                                            \
            ++failures;                                                              \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);            \
        }                                                                            \
    } while (false)

using ::integration::DebugHaltGuard;
using Event = ::integration::DebugHaltGuard::Event;

/* SYS-36: the adapters hand their driver the lock of the safe state when they are attached. The guard is tested
 * without S1 here; S1 and its lock have their own test (SafeStateHoldHostTest.cpp). */
bool noSafeState() { return false; }

// Cortex-M DFSR bits as the target passes them (DebugHaltGuardSystem.h)
uint32_t const DFSR_HALTED   = 0x01U;
uint32_t const DFSR_BKPT     = 0x02U;
uint32_t const DFSR_VCATCH   = 0x08U;
uint32_t const DFSR_EXTERNAL = 0x10U;

/* PWM of a power stage: duty requests of the application reach the pins only while not inhibited (the FTM software
 * output control overrides the channel outputs, whatever duty is set). */
class FakePwm : public ::integration::IPwmInhibit
{
public:
    bool inhibited    = false;
    uint16_t duty     = 0U; // last duty request of the application
    int inhibitCalls  = 0;
    int releaseCalls  = 0;
    std::string trace; // 'P' inhibit, 'R' release (order with the outputs)

    void inhibit() override
    {
        inhibited = true;
        ++inhibitCalls;
        trace += 'P';
    }

    void release() override
    {
        inhibited = false;
        ++releaseCalls;
        trace += 'R';
    }

    /** Duty at the pins. */
    uint16_t pins() const { return inhibited ? uint16_t{0U} : duty; }
};

class FakeOutput : public ::integration::IPowerStageOutput
{
public:
    explicit FakeOutput(std::string& trace, char name) : _trace(trace), _name(name) {}

    bool on             = true;
    bool enabledSince   = false;
    int switchOffCalls  = 0;

    void switchOffAfterDebugHalt() override
    {
        on           = false;
        enabledSince = false;
        ++switchOffCalls;
        _trace += _name;
    }

    bool enabledSinceDebugHaltSwitchOff() const override { return enabledSince; }

    bool holdForSafeState() override { return false; } // SYS-36: not used by the guard (SafeStateHoldHostTest)

    void enable()
    {
        on           = true;
        enabledSince = true;
    }

private:
    std::string& _trace;
    char _name;
};

/* TMC6200 as far as init(), enable(), disable() and cyclic() need it: register file with IOIN version and DRV_EN
 * from the pin, GSTAT write one to clear (datasheet p.22-24). */
class FakeTmc6200
: public ::gd::ITmc6200Spi
, public ::gd::ITmc6200Io
{
public:
    uint32_t reg[128];
    bool drvEnPin      = false;
    int enableHighWrites = 0;

    FakeTmc6200()
    {
        std::memset(reg, 0, sizeof(reg));
        reg[TMC6200_REG_GCONF] = TMC6200_GCONF_RESET;
        reg[TMC6200_REG_IOIN]  = static_cast<uint32_t>(TMC6200_VERSION) << 24U;
    }

    bool transfer(uint64_t const tx, uint64_t& rx) override
    {
        uint8_t const addressByte = static_cast<uint8_t>(tx >> 32U);
        uint8_t const address     = addressByte & TMC6200_ADDRESS_MASK;
        uint32_t const data       = static_cast<uint32_t>(tx);
        if ((addressByte & TMC6200_WRITE_FLAG) != 0U)
        {
            if (address == TMC6200_REG_GSTAT)
            {
                reg[address] &= ~data;
            }
            else
            {
                reg[address] = data;
            }
            rx = 0U;
            return true;
        }
        uint32_t value = reg[address];
        if (address == TMC6200_REG_IOIN)
        {
            value = (value & ~TMC6200_IOIN_DRV_EN_MASK) | (drvEnPin ? TMC6200_IOIN_DRV_EN_MASK : 0U);
        }
        rx = value;
        return true;
    }

    bool setDriverEnable(bool const on) override
    {
        if (on)
        {
            ++enableHighWrites;
        }
        drvEnPin = on;
        return true;
    }

    bool readFault(bool& active) override
    {
        active = false;
        return true;
    }
};

::gd::Tmc6200Config const CONFIG = ::gd::keepAllConfig();

/* IO-37: the pins of a smart high-side switch as the channel drives them (IN, DEN) and the sense sample the test
 * hands it - the load current in mA, already converted by the integration on the target. */
class FakeHighSideSwitchIo : public ::highside::IHighSideSwitchIo
{
public:
    bool inPin      = false;
    bool denPin     = false;
    uint32_t loadMa = 0U;
    bool adcOk      = true;
    int onWrites    = 0;

    bool setEnable(bool const on) override
    {
        if (on)
        {
            ++onWrites;
        }
        inPin = on;
        return true;
    }

    bool setDiagEnable(bool const on) override
    {
        denPin = on;
        return true;
    }

    bool readSense(::highside::SenseSample& sample) override
    {
        sample.loadMa = loadMa;
        sample.raw    = loadMa;
        return adcOk;
    }
};

/* Plausible levels of a load on KL30B; only the structure matters here (10 ms cycle, 100 ms blanking, 50 ms
 * overcurrent debounce), not the values of a project. */
constexpr ::highside::HighSideSwitchConfig HSS_CONFIG = {10U, 3000U, 50U, 6000U, 20U, 100U, 0U, 200U, 50U};
static_assert(::highside::isValidHighSideSwitchConfig(HSS_CONFIG), "test configuration invalid");

using HssResult = ::highside::HighSideSwitch::CommandResult;
// ARCH-66: the power stage output of a high-side switch as the generated binding creates it (on the target the owner
// is the switch's lifecycle system, HighSideSwitchBinding.h; here the channel itself)
using HssPowerStageOutput
    = ::integration::SafeOffOutput<::highside::HighSideSwitchSafeOff<::highside::HighSideSwitchChannel>>;
using HssState  = ::highside::HighSideSwitch::State;

/* ------------------------------------------------------------------------------------------ guard alone */

void noEventDoesNothing()
{
    FakePwm pwm;
    FakeOutput out(pwm.trace, 'A');
    ::integration::IPowerStageOutput* const outputs[] = {&out};
    DebugHaltGuard guard(outputs, 1U, &pwm);
    pwm.duty = 5000U;
    for (int i = 0; i < 1000; ++i)
    {
        CHECK(guard.cyclic(0U) == Event::NONE);
    }
    CHECK(!guard.isInhibited());
    CHECK(pwm.inhibitCalls == 0);
    CHECK(pwm.releaseCalls == 0);
    CHECK(out.switchOffCalls == 0);
    CHECK(out.on);
    CHECK(pwm.pins() == 5000U);
    CHECK(guard.getHalts() == 0U);
}

void haltInhibitsPwmAndSwitchesOffEveryOutput()
{
    FakePwm pwm;
    FakeOutput a(pwm.trace, 'A');
    FakeOutput b(pwm.trace, 'B');
    ::integration::IPowerStageOutput* const outputs[] = {&a, &b};
    DebugHaltGuard guard(outputs, 2U, &pwm);
    pwm.duty = 5000U;
    CHECK(guard.cyclic(DFSR_HALTED) == Event::HALT);
    CHECK(guard.isInhibited());
    CHECK(pwm.pins() == 0U);
    CHECK(!a.on);
    CHECK(!b.on);
    // PWM first, then the enables in board order
    CHECK(pwm.trace == "PAB");
    CHECK(guard.getHalts() == 1U);
    CHECK(guard.getLastDebugEvents() == DFSR_HALTED);
}

void everyDebugEventIsAHalt()
{
    uint32_t const events[] = {DFSR_HALTED, DFSR_BKPT, 0x04U, DFSR_VCATCH, DFSR_EXTERNAL, 0x1FU};
    for (uint32_t const e : events)
    {
        FakePwm pwm;
        FakeOutput a(pwm.trace, 'A');
        ::integration::IPowerStageOutput* const outputs[] = {&a};
        DebugHaltGuard guard(outputs, 1U, &pwm);
        CHECK(guard.cyclic(e) == Event::HALT);
        CHECK(pwm.inhibited);
        CHECK(!a.on);
        CHECK(guard.getLastDebugEvents() == e);
    }
}

void staysInhibitedUntilExplicitEnable()
{
    FakePwm pwm;
    FakeOutput a(pwm.trace, 'A');
    ::integration::IPowerStageOutput* const outputs[] = {&a};
    DebugHaltGuard guard(outputs, 1U, &pwm);
    pwm.duty = 5000U;
    (void)guard.cyclic(DFSR_HALTED);
    // the application keeps requesting its old duty: nothing reaches the pins, nothing switches the output on
    for (int i = 0; i < 10000; ++i)
    {
        pwm.duty = static_cast<uint16_t>(4000U + (i % 2000));
        CHECK(guard.cyclic(0U) == Event::NONE);
    }
    CHECK(pwm.pins() == 0U);
    CHECK(!a.on);
    CHECK(pwm.releaseCalls == 0);
    // explicit enable: released in the next poll, with the duty the application requests now
    a.enable();
    pwm.duty = 1234U;
    CHECK(guard.cyclic(0U) == Event::RELEASED);
    CHECK(!guard.isInhibited());
    CHECK(pwm.pins() == 1234U);
    CHECK(pwm.releaseCalls == 1);
    CHECK(guard.cyclic(0U) == Event::NONE);
    CHECK(pwm.releaseCalls == 1);
}

/* SYS-35: the safe state hands its PWM inhibit to the guard - the same wait for an explicit enable as after a halt,
 * but no halt is counted and no halt event is reported. */
void safeStateInhibitWaitsForExplicitEnable()
{
    FakePwm pwm;
    FakeOutput a(pwm.trace, 'A');
    FakeOutput b(pwm.trace, 'B');
    ::integration::IPowerStageOutput* const outputs[] = {&a, &b};
    DebugHaltGuard guard(outputs, 2U, &pwm);
    pwm.duty = 5000U;
    guard.inhibitForSafeState();
    CHECK(guard.isInhibited());
    CHECK(pwm.trace == "PAB"); // PWM first, then the outputs
    CHECK(!a.on);
    CHECK(!b.on);
    CHECK(guard.getHalts() == 0U);
    CHECK(guard.getLastDebugEvents() == 0U);
    for (int i = 0; i < 1000; ++i)
    {
        CHECK(guard.cyclic(0U) == Event::NONE);
    }
    CHECK(pwm.pins() == 0U);
    b.enable();
    CHECK(guard.cyclic(0U) == Event::RELEASED);
    CHECK(pwm.pins() == 5000U);
    CHECK(pwm.releaseCalls == 1);
    // entering again inhibits again; a project without PWM only switches the outputs off
    guard.inhibitForSafeState();
    CHECK(pwm.pins() == 0U);
    DebugHaltGuard noPwm(outputs, 2U, nullptr);
    a.enable();
    noPwm.inhibitForSafeState();
    CHECK(!a.on);
    CHECK(noPwm.isInhibited());
}

void enableOfAnyOutputReleases()
{
    FakePwm pwm;
    FakeOutput a(pwm.trace, 'A');
    FakeOutput b(pwm.trace, 'B');
    ::integration::IPowerStageOutput* const outputs[] = {&a, &b};
    DebugHaltGuard guard(outputs, 2U, &pwm);
    (void)guard.cyclic(DFSR_HALTED);
    b.enable();
    CHECK(guard.cyclic(0U) == Event::RELEASED);
    CHECK(!pwm.inhibited);
    CHECK(!a.on); // the other bridge stays off until its own enable
}

void haltDuringInhibitRestartsTheWait()
{
    FakePwm pwm;
    FakeOutput a(pwm.trace, 'A');
    ::integration::IPowerStageOutput* const outputs[] = {&a};
    DebugHaltGuard guard(outputs, 1U, &pwm);
    (void)guard.cyclic(DFSR_HALTED);
    a.enable();
    // enabled and halted again before the guard polled: the halt wins, the enable before it does not count
    CHECK(guard.cyclic(DFSR_HALTED) == Event::HALT);
    CHECK(pwm.inhibited);
    CHECK(!a.on);
    CHECK(guard.getHalts() == 2U);
    CHECK(guard.cyclic(0U) == Event::NONE);
    CHECK(pwm.inhibited);
    a.enable();
    CHECK(guard.cyclic(0U) == Event::RELEASED);
}

void haltAfterReleaseInhibitsAgain()
{
    FakePwm pwm;
    FakeOutput a(pwm.trace, 'A');
    ::integration::IPowerStageOutput* const outputs[] = {&a};
    DebugHaltGuard guard(outputs, 1U, &pwm);
    (void)guard.cyclic(DFSR_HALTED);
    a.enable();
    (void)guard.cyclic(0U);
    CHECK(guard.cyclic(DFSR_HALTED) == Event::HALT);
    CHECK(pwm.inhibited);
    CHECK(!a.on);
    CHECK(pwm.inhibitCalls == 2);
}

void withoutOutputsOnlyReleaseEndsTheInhibit()
{
    // a project with a PWM group but no gate driver: stays inhibited until release() (in practice: the next reset)
    FakePwm pwm;
    DebugHaltGuard guard(nullptr, 0U, &pwm);
    CHECK(guard.cyclic(DFSR_HALTED) == Event::HALT);
    for (int i = 0; i < 100; ++i)
    {
        CHECK(guard.cyclic(0U) == Event::NONE);
    }
    CHECK(pwm.inhibited);
    guard.release();
    CHECK(!pwm.inhibited);
    CHECK(!guard.isInhibited());
    guard.release(); // not inhibited: no second release of the PWM
    CHECK(pwm.releaseCalls == 1);
}

void withoutPwmOnlyOutputs()
{
    // a gate driver whose inputs are not a PWM group of the project
    std::string trace;
    FakeOutput a(trace, 'A');
    ::integration::IPowerStageOutput* const outputs[] = {&a};
    DebugHaltGuard guard(outputs, 1U, nullptr);
    CHECK(guard.cyclic(DFSR_HALTED) == Event::HALT);
    CHECK(!a.on);
    a.enable();
    CHECK(guard.cyclic(0U) == Event::RELEASED);
    CHECK(!guard.isInhibited());
}

/* ----------------------------------------------------------------- TMC6200 DRV_EN as power stage output */

void tmc6200OutputSwitchesOffAndWaitsForEnable()
{
    FakeTmc6200 dev;
    ::gd::Tmc6200 driver(dev, dev, CONFIG);
    CHECK(driver.init(0U) == ::gd::Tmc6200::Status::OK);
    CHECK(driver.enable() == ::gd::Tmc6200::Status::OK);
    CHECK(dev.drvEnPin);

    FakePwm pwm;
    ::integration::Tmc6200PowerStageOutput output;
    output.attach(driver, &noSafeState);
    ::integration::IPowerStageOutput* const outputs[] = {&output};
    DebugHaltGuard guard(outputs, 1U, &pwm);
    pwm.duty = 5000U;

    CHECK(guard.cyclic(0U) == Event::NONE);
    CHECK(dev.drvEnPin);
    CHECK(guard.cyclic(DFSR_HALTED) == Event::HALT);
    CHECK(!dev.drvEnPin);
    CHECK(driver.getState() == ::gd::Tmc6200::State::DISABLED);
    CHECK(!output.enabledSinceDebugHaltSwitchOff());
    CHECK(pwm.pins() == 0U);

    // the driver's own supervision never enables, and never releases the PWM
    int const highWrites = dev.enableHighWrites;
    for (uint32_t t = 10U; t < 5000U; t += 10U)
    {
        driver.cyclic(t);
        CHECK(guard.cyclic(0U) == Event::NONE);
    }
    CHECK(dev.enableHighWrites == highWrites);
    CHECK(!dev.drvEnPin);
    CHECK(pwm.pins() == 0U);

    // explicit enable (application or test build console "gd enable")
    CHECK(driver.enable() == ::gd::Tmc6200::Status::OK);
    CHECK(output.enabledSinceDebugHaltSwitchOff());
    CHECK(guard.cyclic(0U) == Event::RELEASED);
    CHECK(pwm.pins() == 5000U);
    CHECK(dev.drvEnPin);
}

void tmc6200RefusedEnableDoesNotRelease()
{
    FakeTmc6200 dev;
    ::gd::Tmc6200 driver(dev, dev, CONFIG);
    CHECK(driver.init(0U) == ::gd::Tmc6200::Status::OK);
    CHECK(driver.enable() == ::gd::Tmc6200::Status::OK);
    FakePwm pwm;
    ::integration::Tmc6200PowerStageOutput output;
    output.attach(driver, &noSafeState);
    ::integration::IPowerStageOutput* const outputs[] = {&output};
    DebugHaltGuard guard(outputs, 1U, &pwm);
    (void)guard.cyclic(DFSR_HALTED);

    // charge pump undervoltage latched: enable() is refused, the PWM stays inhibited
    dev.reg[TMC6200_REG_GSTAT] = TMC6200_GSTAT_UV_CP_MASK;
    driver.cyclic(10U);
    CHECK(driver.enable() == ::gd::Tmc6200::Status::NOT_ALLOWED);
    CHECK(!output.enabledSinceDebugHaltSwitchOff());
    CHECK(guard.cyclic(0U) == Event::NONE);
    CHECK(pwm.inhibited);
    CHECK(!dev.drvEnPin);
    // "gd clear" alone neither enables nor releases
    dev.reg[TMC6200_REG_GSTAT] = 0U;
    CHECK(driver.clearFaults() == ::gd::Tmc6200::Status::OK);
    driver.cyclic(20U);
    CHECK(guard.cyclic(0U) == Event::NONE);
    CHECK(pwm.inhibited);
    CHECK(driver.enable() == ::gd::Tmc6200::Status::OK);
    CHECK(guard.cyclic(0U) == Event::RELEASED);
}

void tmc6200HaltWhileDisabled()
{
    // PWM running at the driver inputs with DRV_EN off (as in the board test before "gd enable"): after a halt the
    // PWM stays inhibited, the first enable releases it
    FakeTmc6200 dev;
    ::gd::Tmc6200 driver(dev, dev, CONFIG);
    CHECK(driver.init(0U) == ::gd::Tmc6200::Status::OK);
    FakePwm pwm;
    ::integration::Tmc6200PowerStageOutput output;
    output.attach(driver, &noSafeState);
    ::integration::IPowerStageOutput* const outputs[] = {&output};
    DebugHaltGuard guard(outputs, 1U, &pwm);
    pwm.duty = 5000U;
    CHECK(guard.cyclic(DFSR_HALTED) == Event::HALT);
    CHECK(!dev.drvEnPin);
    CHECK(pwm.pins() == 0U);
    CHECK(driver.getCounters().enables == 0U);
    CHECK(driver.enable() == ::gd::Tmc6200::Status::OK);
    CHECK(guard.cyclic(0U) == Event::RELEASED);
    CHECK(pwm.pins() == 5000U);
}

void tmc6200EnableBeforeTheHaltDoesNotCount()
{
    // enables counted before the switch off are no release (the counter is taken at the switch off)
    FakeTmc6200 dev;
    ::gd::Tmc6200 driver(dev, dev, CONFIG);
    CHECK(driver.init(0U) == ::gd::Tmc6200::Status::OK);
    for (int i = 0; i < 3; ++i)
    {
        CHECK(driver.enable() == ::gd::Tmc6200::Status::OK);
        CHECK(driver.disable() == ::gd::Tmc6200::Status::OK);
    }
    CHECK(driver.enable() == ::gd::Tmc6200::Status::OK);
    ::integration::Tmc6200PowerStageOutput output;
    output.attach(driver, &noSafeState);
    CHECK(!output.enabledSinceDebugHaltSwitchOff()); // never switched off: nothing to report
    output.switchOffAfterDebugHalt();
    CHECK(!dev.drvEnPin);
    CHECK(!output.enabledSinceDebugHaltSwitchOff());
    // enable() while already enabled would not count - here the switch off made it DISABLED first
    CHECK(driver.enable() == ::gd::Tmc6200::Status::OK);
    CHECK(output.enabledSinceDebugHaltSwitchOff());
}

void tmc6200EnableCountsEvenIfOffAgain()
{
    // the release follows the explicit enable, not the current state: enabled and switched off again (application,
    // or a driver fault) before the guard polls still ends the inhibit - DRV_EN stays as the driver left it
    FakeTmc6200 dev;
    ::gd::Tmc6200 driver(dev, dev, CONFIG);
    CHECK(driver.init(0U) == ::gd::Tmc6200::Status::OK);
    FakePwm pwm;
    ::integration::Tmc6200PowerStageOutput output;
    output.attach(driver, &noSafeState);
    ::integration::IPowerStageOutput* const outputs[] = {&output};
    DebugHaltGuard guard(outputs, 1U, &pwm);
    (void)guard.cyclic(DFSR_HALTED);
    CHECK(driver.enable() == ::gd::Tmc6200::Status::OK);
    CHECK(driver.disable() == ::gd::Tmc6200::Status::OK);
    CHECK(output.enabledSinceDebugHaltSwitchOff());
    CHECK(guard.cyclic(0U) == Event::RELEASED);
    CHECK(!dev.drvEnPin);
}

void tmc6200NotAttached()
{
    ::integration::Tmc6200PowerStageOutput output;
    output.switchOffAfterDebugHalt(); // no driver yet: nothing to do, no crash
    CHECK(!output.enabledSinceDebugHaltSwitchOff());
}

void tmc6200UninitializedSwitchOff()
{
    // halt before the driver started: DRV_EN low (disable() is allowed in every state), state stays
    FakeTmc6200 dev;
    dev.drvEnPin = true; // as if a previous run left it on
    ::gd::Tmc6200 driver(dev, dev, CONFIG);
    ::integration::Tmc6200PowerStageOutput output;
    output.attach(driver, &noSafeState);
    output.switchOffAfterDebugHalt();
    CHECK(!dev.drvEnPin);
    CHECK(driver.getState() == ::gd::Tmc6200::State::UNINITIALIZED);
    CHECK(!output.enabledSinceDebugHaltSwitchOff());
}

/* ------------------------------------------------- IO-37: high-side switch IN as power stage output */

void highSideSwitchOutputSwitchesOffAndWaitsForOn()
{
    FakeHighSideSwitchIo io;
    ::highside::HighSideSwitchChannel channel(io, HSS_CONFIG);
    channel.init();
    CHECK(channel.requestOn() == HssResult::OK);
    CHECK(io.inPin);
    CHECK(io.denPin);

    FakePwm pwm;
    HssPowerStageOutput output;
    output.attach(channel, &noSafeState);
    ::integration::IPowerStageOutput* const outputs[] = {&output};
    DebugHaltGuard guard(outputs, 1U, &pwm);
    pwm.duty = 5000U;

    CHECK(guard.cyclic(0U) == Event::NONE);
    CHECK(io.inPin);
    CHECK(guard.cyclic(DFSR_HALTED) == Event::HALT);
    CHECK(!io.inPin); // IN low, and the state machine knows it
    CHECK(!io.denPin);
    CHECK(channel.getSwitch().getState() == HssState::OFF);
    CHECK(!output.enabledSinceDebugHaltSwitchOff());
    CHECK(pwm.pins() == 0U);

    // the supervision of the driver never switches on again, and never releases the PWM
    int const onWrites = io.onWrites;
    for (int i = 0; i < 100; ++i)
    {
        channel.cycle();
        CHECK(guard.cyclic(0U) == Event::NONE);
    }
    CHECK(io.onWrites == onWrites);
    CHECK(!io.inPin);
    CHECK(pwm.pins() == 0U);

    // explicit switch-on ("kl30b on")
    CHECK(channel.requestOn() == HssResult::OK);
    CHECK(output.enabledSinceDebugHaltSwitchOff());
    CHECK(guard.cyclic(0U) == Event::RELEASED);
    CHECK(pwm.pins() == 5000U);
    CHECK(io.inPin);
}

void highSideSwitchLatchedFaultNeedsResetBeforeItReleases()
{
    // overcurrent latched before the halt: "on" is refused, so neither the halt nor a bare reset releases the PWM
    FakeHighSideSwitchIo io;
    ::highside::HighSideSwitchChannel channel(io, HSS_CONFIG);
    channel.init();
    CHECK(channel.requestOn() == HssResult::OK);
    io.loadMa = 4000U; // above overcurrentMa
    for (int i = 0; i < 20; ++i)
    {
        channel.cycle(); // 100 ms blanking, then 50 ms debounce at a 10 ms cycle
    }
    CHECK(channel.getSwitch().getState() == HssState::OVERCURRENT);
    CHECK(!io.inPin);

    FakePwm pwm;
    HssPowerStageOutput output;
    output.attach(channel, &noSafeState);
    ::integration::IPowerStageOutput* const outputs[] = {&output};
    DebugHaltGuard guard(outputs, 1U, &pwm);
    pwm.duty = 5000U;
    CHECK(guard.cyclic(DFSR_HALTED) == Event::HALT);
    CHECK(channel.getSwitch().getState() == HssState::OVERCURRENT); // the latch survives the switch off
    CHECK(channel.requestOn() == HssResult::LATCHED);
    CHECK(!output.enabledSinceDebugHaltSwitchOff());
    CHECK(guard.cyclic(0U) == Event::NONE);
    CHECK(pwm.inhibited);

    io.loadMa = 0U;
    CHECK(channel.reset() == HssResult::OK); // "kl30b reset" alone is no switch-on
    CHECK(!output.enabledSinceDebugHaltSwitchOff());
    CHECK(guard.cyclic(0U) == Event::NONE);
    CHECK(pwm.inhibited);
    CHECK(channel.requestOn() == HssResult::OK);
    CHECK(guard.cyclic(0U) == Event::RELEASED);
    CHECK(pwm.pins() == 5000U);
}

void highSideSwitchHaltWhileOff()
{
    // halt with the switch off (the normal state on the eval board): nothing changes, the first "on" releases
    FakeHighSideSwitchIo io;
    ::highside::HighSideSwitchChannel channel(io, HSS_CONFIG);
    channel.init();
    FakePwm pwm;
    HssPowerStageOutput output;
    output.attach(channel, &noSafeState);
    ::integration::IPowerStageOutput* const outputs[] = {&output};
    DebugHaltGuard guard(outputs, 1U, &pwm);
    pwm.duty = 5000U;
    CHECK(guard.cyclic(DFSR_HALTED) == Event::HALT);
    CHECK(!io.inPin);
    CHECK(pwm.pins() == 0U);
    CHECK(channel.getSwitch().getCounters().switchOns == 0U);
    CHECK(channel.requestOn() == HssResult::OK);
    CHECK(guard.cyclic(0U) == Event::RELEASED);
    CHECK(pwm.pins() == 5000U);
}

void highSideSwitchOnBeforeTheHaltDoesNotCount()
{
    // switch-ons counted before the switch off are no release (the counter is taken at the switch off)
    FakeHighSideSwitchIo io;
    ::highside::HighSideSwitchChannel channel(io, HSS_CONFIG);
    channel.init();
    for (int i = 0; i < 3; ++i)
    {
        CHECK(channel.requestOn() == HssResult::OK);
        CHECK(channel.requestOff() == HssResult::OK);
    }
    CHECK(channel.requestOn() == HssResult::OK);
    HssPowerStageOutput output;
    output.attach(channel, &noSafeState);
    CHECK(!output.enabledSinceDebugHaltSwitchOff()); // never switched off: nothing to report
    output.switchOffAfterDebugHalt();
    CHECK(!io.inPin);
    CHECK(!output.enabledSinceDebugHaltSwitchOff());
    CHECK(channel.requestOn() == HssResult::OK);
    CHECK(output.enabledSinceDebugHaltSwitchOff());
}

void highSideSwitchOnCountsEvenIfOffAgain()
{
    // the release follows the explicit switch-on, not the current state
    FakeHighSideSwitchIo io;
    ::highside::HighSideSwitchChannel channel(io, HSS_CONFIG);
    channel.init();
    FakePwm pwm;
    HssPowerStageOutput output;
    output.attach(channel, &noSafeState);
    ::integration::IPowerStageOutput* const outputs[] = {&output};
    DebugHaltGuard guard(outputs, 1U, &pwm);
    (void)guard.cyclic(DFSR_HALTED);
    CHECK(channel.requestOn() == HssResult::OK);
    CHECK(channel.requestOff() == HssResult::OK);
    CHECK(output.enabledSinceDebugHaltSwitchOff());
    CHECK(guard.cyclic(0U) == Event::RELEASED);
    CHECK(!io.inPin);
}

void highSideSwitchNotAttached()
{
    HssPowerStageOutput output;
    output.switchOffAfterDebugHalt(); // no channel yet: nothing to do, no crash
    CHECK(!output.enabledSinceDebugHaltSwitchOff());
}

void bothSafeOffOutputsOfTheProject()
{
    /* IO-37: the eval board project has two safe_off outputs - one adapter each, as the static_assert of
     * Integration.cpp against NX_SAFE_OFF_OUTPUT_COUNT requires. One halt switches both off; because the guard
     * releases on an enable of any output, switching the gate driver on again releases the PWM while the
     * high-side switch stays off (it is switched by the application, not by the guard). */
    FakeTmc6200 dev;
    ::gd::Tmc6200 driver(dev, dev, CONFIG);
    CHECK(driver.init(0U) == ::gd::Tmc6200::Status::OK);
    CHECK(driver.enable() == ::gd::Tmc6200::Status::OK);
    FakeHighSideSwitchIo io;
    ::highside::HighSideSwitchChannel channel(io, HSS_CONFIG);
    channel.init();
    CHECK(channel.requestOn() == HssResult::OK);

    FakePwm pwm;
    ::integration::Tmc6200PowerStageOutput gdOutput;
    gdOutput.attach(driver, &noSafeState);
    HssPowerStageOutput hssOutput;
    hssOutput.attach(channel, &noSafeState);
    ::integration::IPowerStageOutput* const outputs[] = {&gdOutput, &hssOutput};
    DebugHaltGuard guard(outputs, 2U, &pwm);
    pwm.duty = 5000U;

    CHECK(guard.cyclic(DFSR_HALTED) == Event::HALT);
    CHECK(!dev.drvEnPin);
    CHECK(!io.inPin);
    CHECK(driver.getState() == ::gd::Tmc6200::State::DISABLED);
    CHECK(channel.getSwitch().getState() == HssState::OFF);
    CHECK(pwm.pins() == 0U);

    CHECK(driver.enable() == ::gd::Tmc6200::Status::OK);
    CHECK(guard.cyclic(0U) == Event::RELEASED);
    CHECK(pwm.pins() == 5000U);
    CHECK(!io.inPin); // the high-side switch stays off until the application switches it on
}

} // namespace

/* ------------------------------------------------------ deadman timer that keeps running during a debug halt */

/* Register file of the model: the four registers the safe outputs table of the eval board project touches, under
 * the addresses of the reference manual. Any address outside it is a test error, not a wild write. */
namespace test_registers
{
namespace
{
// The table the generator produces for the eval board demo project (tools/bswgen, checked against the reference
// manual by BswGenSafeOutputsHostTest): FTM0 SWOCTRL with the channel mask of the six-channel group, then the two
// safe_off outputs of its modules - DRV_EN of the gate driver on PTC11 and (IO-37) IN of the high-side switch on
// PTD17, both active high (PCOR) - each gated by the PCC register of its peripheral.
uint32_t const FTM0_SWOCTRL = 0x40038094U;
uint32_t const PTC_PCOR     = 0x400FF088U;
uint32_t const PTD_PCOR     = 0x400FF0C8U;
uint32_t const PCC_FTM0     = 0x400650E0U;
uint32_t const PCC_PORTC    = 0x4006512CU;
uint32_t const PCC_PORTD    = 0x40065130U;

struct Register
{
    uint32_t address;
    uint32_t value;
};

Register file[] = {
    {FTM0_SWOCTRL, 0U},
    {PTC_PCOR, 0U},
    {PTD_PCOR, 0U},
    {PCC_FTM0, 0x40000000U},  // CGC set: clock on
    {PCC_PORTC, 0x40000000U},
    {PCC_PORTD, 0x40000000U},
};

int unknownAccesses = 0;
uint32_t sink       = 0U;
} // namespace

volatile uint32_t* reg(uint32_t const address)
{
    for (Register& r : file)
    {
        if (r.address == address)
        {
            return &r.value;
        }
    }
    ++unknownAccesses;
    return &sink;
}

void reset()
{
    file[0].value   = 0U;
    file[1].value   = 0U;
    file[2].value   = 0U;
    file[3].value   = 0x40000000U;
    file[4].value   = 0x40000000U;
    file[5].value   = 0x40000000U;
    unknownAccesses = 0;
}

uint32_t get(uint32_t const address) { return *reg(address); }

void set(uint32_t const address, uint32_t const value) { *reg(address) = value; }
} // namespace test_registers

// Table of the eval board project as tools/bswgen generates it: first the PWM group (FTM SWOCTRL with the channel
// mask), then every safe_off output of a module instance (DRV_EN of the gate driver and, since IO-37, IN of the
// high-side switch; GPIO PCOR because both are active high).
extern "C" NxSafeOutput const nxSafeOutputs[] = {
    {test_registers::FTM0_SWOCTRL, 0x3FU, test_registers::PCC_FTM0, 0x40000000U},
    {test_registers::PTC_PCOR, 1UL << 11, test_registers::PCC_PORTC, 0x40000000U},
    {test_registers::PTD_PCOR, 1UL << 17, test_registers::PCC_PORTD, 0x40000000U},
    {0U, 0U, 0U, 0U},
};

namespace
{
uint32_t const SAFE_SWOCTRL      = 0x3FU;
uint32_t const SAFE_DRV_EN_PCOR  = 1UL << 11;
uint32_t const SAFE_IN_PCOR      = 1UL << 17;

/* The LPIT channel of the deadman and what the core does with its interrupt, as far as the mechanism needs it
 * (DebugHaltGuardSystem.h, LpitHaltDeadman). Time in microseconds.
 *
 * The point the model has to get right: while the core is halted the channel keeps counting **only** with
 * MCR[DBG_EN] set, and its interrupt then pends in the NVIC and is taken at the first instruction after the
 * resume, before any task code. */
class DeadmanModel
{
public:
    uint32_t timeoutUs = 10000U; // LpitHaltDeadman::TIMEOUT_MS

    // mutants
    bool dbgEn           = true;  // MCR[DBG_EN]: without it the channel stops with the core
    bool reloadOnEnable  = true;  // retrigger by CLRTEN/SETTEN; false = a write to TVAL alone (reload after expiry)
    bool isrSwitchesOff  = true;
    bool isrSetsFlag     = true;
    bool isrClearsFlag   = true;

    /** Registers set up, channel not armed yet - the production code arms it at the end of the first poll. */
    void init()
    {
        _armed     = false;
        _pending   = false;
        _expired   = false;
        _remaining = timeoutUs;
    }

    void retrigger()
    {
        if (reloadOnEnable)
        {
            _armed     = true;
            _remaining = timeoutUs;
        }
        else if (!_armed)
        {
            // a write to TVAL alone takes effect only after the next expiry: the channel is not re-armed either
            _armed = true;
        }
    }

    /** \p halted: the core is held by the debugger during this stretch of time. */
    void advance(uint32_t const us, bool const halted)
    {
        if (!_armed || (halted && !dbgEn))
        {
            return;
        }
        if (us >= _remaining)
        {
            _remaining = timeoutUs;
            _pending   = true;
            _armed     = false; // one interrupt per expiry (the handler stops the channel)
        }
        else
        {
            _remaining -= us;
        }
    }

    /** The core runs again (or never stopped) and takes a pending interrupt of the channel. */
    void takePendingInterrupt()
    {
        if (!_pending)
        {
            return;
        }
        _pending = false;
        if (isrSwitchesOff)
        {
            nxSafeOutputsSwitchOff(); // the real walk of the target
        }
        if (isrSetsFlag)
        {
            _expired = true;
        }
    }

    bool takeExpired()
    {
        if (!_expired)
        {
            return false;
        }
        if (isrClearsFlag)
        {
            _expired = false;
        }
        return true;
    }

    bool isPending() const { return _pending; }

private:
    bool _armed         = false;
    bool _pending       = false;
    bool _expired       = false;
    uint32_t _remaining = 0U;
};

/* The production sequence of DebugHaltGuardSystem: poll the DFSR, take the deadman flag, run the guard, retrigger
 * last. Plus the model of the probe: a J-Link that clears the DFSR when it resumes the core. */
class System
{
public:
    static uint32_t const POLL_US = 1000U; // DebugHaltGuardSystem::POLL_MS

    DeadmanModel deadman;
    FakePwm pwm;
    FakeOutput output{pwm.trace, 'A'};
    ::integration::IPowerStageOutput* outputs[1] = {&output};
    DebugHaltGuard guard{outputs, 1U, &pwm};

    bool guardReadsTheTimerBit = true; // mutant: the guard ignores the deadman
    uint32_t dfsr              = 0U;

    void start()
    {
        test_registers::reset();
        deadman.init();
    }

    /** One poll period of running software. */
    DebugHaltGuard::Event poll(uint32_t const durationUs = POLL_US)
    {
        deadman.advance(durationUs, false);
        deadman.takePendingInterrupt();
        uint32_t events = dfsr;
        dfsr            = 0U; // DFSR is cleared by the read
        if (guardReadsTheTimerBit && deadman.takeExpired())
        {
            events |= DebugHaltGuard::HALT_TIMER;
        }
        DebugHaltGuard::Event const event = guard.cyclic(events);
        deadman.retrigger();
        return event;
    }

    /** A debugger halt of \p us microseconds; \p probeClearsDfsr as the J-Link Commander does at `g`. */
    void halt(uint32_t const us, bool const probeClearsDfsr = true)
    {
        dfsr = 0x01U; // the core sets DFSR.HALTED at the halt
        deadman.advance(us, true);
        if (probeClearsDfsr)
        {
            dfsr = 0U;
        }
        // the resume: the pending interrupt of the deadman is taken before any task code
        deadman.takePendingInterrupt();
    }

    bool outputsAreOffInHardware() const
    {
        return (test_registers::get(test_registers::FTM0_SWOCTRL) == SAFE_SWOCTRL)
               && (test_registers::get(test_registers::PTC_PCOR) == SAFE_DRV_EN_PCOR)
               && (test_registers::get(test_registers::PTD_PCOR) == SAFE_IN_PCOR);
    }
};

/* ---------------------------------------------------------------------------- the walk over the safe outputs */

void safeOutputsWalkWritesEveryEntry()
{
    test_registers::reset();
    nxSafeOutputsSwitchOff();
    CHECK(test_registers::get(test_registers::FTM0_SWOCTRL) == SAFE_SWOCTRL);
    CHECK(test_registers::get(test_registers::PTC_PCOR) == SAFE_DRV_EN_PCOR);
    CHECK(test_registers::get(test_registers::PTD_PCOR) == SAFE_IN_PCOR); // IO-37: the high-side switch too
    CHECK(test_registers::unknownAccesses == 0); // no write past the end entry
}

void safeOutputsWalkSkipsAnEntryWhoseClockIsGated()
{
    test_registers::reset();
    test_registers::set(test_registers::PCC_FTM0, 0U); // FTM clock off: the access would bus fault
    nxSafeOutputsSwitchOff();
    CHECK(test_registers::get(test_registers::FTM0_SWOCTRL) == 0U);
    CHECK(test_registers::get(test_registers::PTC_PCOR) == SAFE_DRV_EN_PCOR); // the other entries are still written
    CHECK(test_registers::get(test_registers::PTD_PCOR) == SAFE_IN_PCOR);
    test_registers::reset();
    test_registers::set(test_registers::PCC_PORTC, 0U);
    nxSafeOutputsSwitchOff();
    CHECK(test_registers::get(test_registers::FTM0_SWOCTRL) == SAFE_SWOCTRL);
    CHECK(test_registers::get(test_registers::PTC_PCOR) == 0U);
    CHECK(test_registers::get(test_registers::PTD_PCOR) == SAFE_IN_PCOR);
    // IO-37: the port of the high-side switch gated - only its own entry is skipped
    test_registers::reset();
    test_registers::set(test_registers::PCC_PORTD, 0U);
    nxSafeOutputsSwitchOff();
    CHECK(test_registers::get(test_registers::FTM0_SWOCTRL) == SAFE_SWOCTRL);
    CHECK(test_registers::get(test_registers::PTC_PCOR) == SAFE_DRV_EN_PCOR);
    CHECK(test_registers::get(test_registers::PTD_PCOR) == 0U);
}

void safeOutputsWalkIsRepeatable()
{
    test_registers::reset();
    for (int i = 0; i < 5; ++i)
    {
        nxSafeOutputsSwitchOff();
    }
    CHECK(test_registers::get(test_registers::FTM0_SWOCTRL) == SAFE_SWOCTRL);
    CHECK(test_registers::get(test_registers::PTC_PCOR) == SAFE_DRV_EN_PCOR);
    CHECK(test_registers::get(test_registers::PTD_PCOR) == SAFE_IN_PCOR);
}

/* IO-37: the table of the eval board project carries **both** safe_off outputs of its modules - the DRV_EN of the
 * gate driver and the IN of the high-side switch - behind the PWM group and in that order. Before IO-37 the
 * high-side switch was missing here, so the safe state left KL30B on (finding of SYS-06). */
void safeOutputsTableCarriesEverySafeOffOutput()
{
    int entries = 0;
    for (NxSafeOutput const* e = &nxSafeOutputs[0]; e->address != 0U; ++e)
    {
        ++entries;
    }
    CHECK(entries == 3);
    CHECK(nxSafeOutputs[0].address == test_registers::FTM0_SWOCTRL); // PWM group first
    CHECK(nxSafeOutputs[1].address == test_registers::PTC_PCOR);     // gate driver DRV_EN
    CHECK(nxSafeOutputs[2].address == test_registers::PTD_PCOR);     // high-side switch IN
    CHECK(nxSafeOutputs[2].value == SAFE_IN_PCOR);
    CHECK(nxSafeOutputs[2].gateAddress == test_registers::PCC_PORTD);
}

/* ------------------------------------------------------------------------------ deadman, guard and the probe */

void deadmanSeesTheHaltTheProbeHides()
{
    // Board 2026-09-18 evening: J-Link clears the DFSR at `g`, so the DFSR poll never sees the halt.
    System s;
    s.start();
    s.pwm.duty = 5000U;
    for (int i = 0; i < 50; ++i)
    {
        CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    }
    CHECK(!s.outputsAreOffInHardware());

    s.halt(30U * 1000U * 1000U); // 30 s, DFSR cleared by the probe
    // the interrupt ran before any task code: the registers are already safe, the guard has not acted yet
    CHECK(s.outputsAreOffInHardware());
    CHECK(s.pwm.trace.empty());
    CHECK(s.dfsr == 0U);

    CHECK(s.poll() == DebugHaltGuard::Event::HALT);
    CHECK((s.guard.getLastDebugEvents() & DebugHaltGuard::HALT_TIMER) != 0U);
    CHECK(s.pwm.trace == "PA"); // PWM inhibited first, then the enables
    CHECK(s.pwm.pins() == 0U);
    CHECK(!s.output.on);

    // no restart without an explicit enable, whatever the application requests
    for (int i = 0; i < 100; ++i)
    {
        s.pwm.duty = static_cast<uint16_t>(4000U + i);
        CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    }
    CHECK(s.pwm.pins() == 0U);
    s.output.enable();
    CHECK(s.poll() == DebugHaltGuard::Event::RELEASED);
    CHECK(s.pwm.pins() != 0U);
}

void nothingIsArmedBeforeTheFirstPoll()
{
    /* Between run() of the component and its first poll the remaining lifecycle levels run in the same task,
     * which may take much longer than the timeout. The deadman is armed by the first poll, not before, so a slow
     * start does not report a halt. */
    System s;
    s.start();
    s.deadman.advance(10U * s.deadman.timeoutUs, false);
    CHECK(!s.deadman.isPending());
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    CHECK(!s.guard.isInhibited());
    CHECK(!s.outputsAreOffInHardware());
    // armed from now on
    CHECK(s.poll(s.deadman.timeoutUs + 1U) == DebugHaltGuard::Event::HALT);
}

void noTriggerWhileTheSoftwareRuns()
{
    // ten minutes of polls with jitter up to just below the timeout
    System s;
    s.start();
    uint32_t jitter = 0U;
    for (int i = 0; i < 600000; ++i)
    {
        jitter = (jitter + 997U) % (s.deadman.timeoutUs - 1000U);
        if (s.poll(1000U + jitter) != DebugHaltGuard::Event::NONE)
        {
            CHECK(false);
            break;
        }
    }
    CHECK(!s.guard.isInhibited());
    CHECK(s.guard.getHalts() == 0U);
    CHECK(!s.outputsAreOffInHardware());
}

void aStallLongerThanTheTimeoutIsFailSafe()
{
    // no debugger: the software itself stands still (interrupt lock, task starvation) - same reaction, log line
    // "halt or stall" (user decision 2026-09-19, question 3)
    System s;
    s.start();
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    CHECK(s.poll(s.deadman.timeoutUs + 1U) == DebugHaltGuard::Event::HALT);
    CHECK((s.guard.getLastDebugEvents() & DebugHaltGuard::HALT_TIMER) != 0U);
    CHECK(s.outputsAreOffInHardware());
    CHECK(s.pwm.pins() == 0U);
    // and it recovers into the inhibited state, it does not keep reporting
    for (int i = 0; i < 100; ++i)
    {
        CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    }
    CHECK(s.guard.getHalts() == 1U);
}

void aHaltShorterThanTheTimeoutIsSeenByTheDfsrOnly()
{
    System s;
    s.start();
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    s.halt(5000U, false); // 5 ms, a probe that leaves the DFSR set
    CHECK(!s.outputsAreOffInHardware());
    CHECK(s.poll() == DebugHaltGuard::Event::HALT);
    CHECK((s.guard.getLastDebugEvents() & DebugHaltGuard::HALT_TIMER) == 0U);
    CHECK(s.guard.getLastDebugEvents() == 0x01U);
    CHECK(s.pwm.pins() == 0U);
    CHECK(!s.output.on);
}

void aHaltShorterThanTheTimeoutWithAProbeThatClearsDfsrIsTheBlindSpot()
{
    // documented blind spot (user decision 2026-09-19, question 2): below the timeout and with a probe that
    // clears the DFSR nothing is seen. Manual and breakpoint halts are far longer.
    System s;
    s.start();
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    s.halt(5000U, true);
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    CHECK(!s.guard.isInhibited());
}

void bothSourcesTogetherReportOnce()
{
    System s;
    s.start();
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    s.halt(30U * 1000U * 1000U, false); // long halt, probe leaves the DFSR set
    CHECK(s.poll() == DebugHaltGuard::Event::HALT);
    CHECK((s.guard.getLastDebugEvents() & DebugHaltGuard::HALT_TIMER) != 0U);
    CHECK((s.guard.getLastDebugEvents() & 0x01U) != 0U);
    CHECK(s.guard.getHalts() == 1U);
}

/* ------------------------------------------------------------------------------------------------- mutants */

void mutantWithoutDbgEnSeesNothing()
{
    // MCR[DBG_EN] not set: the channel stops with the core, exactly the situation of the first round
    System s;
    s.deadman.dbgEn = false;
    s.start();
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    s.halt(30U * 1000U * 1000U);
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    CHECK(!s.guard.isInhibited());
    CHECK(!s.outputsAreOffInHardware());
}

void mutantWithoutRetriggerTriggersOnItsOwn()
{
    System s;
    s.start();
    CHECK(s.poll() == DebugHaltGuard::Event::NONE); // the first poll arms the deadman
    // no retrigger from here on: the timeout runs out during normal operation
    bool triggered = false;
    for (int i = 0; i < 100; ++i)
    {
        s.deadman.advance(System::POLL_US, false);
        s.deadman.takePendingInterrupt();
        uint32_t events = 0U;
        if (s.deadman.takeExpired())
        {
            events |= DebugHaltGuard::HALT_TIMER;
        }
        if (s.guard.cyclic(events) == DebugHaltGuard::Event::HALT)
        {
            triggered = true;
            break;
        }
    }
    CHECK(triggered);
}

void mutantRetriggerByTvalOnlyMissesTheHalt()
{
    // a retrigger that only writes TVAL does not reload a running channel ([RM] 48.5.6): after the first expiry
    // the deadman is no longer a deadman - it fires once and then never restarts at the right time
    System s;
    s.deadman.reloadOnEnable = false;
    s.start();
    bool falseTrigger = false;
    for (int i = 0; i < 100; ++i)
    {
        if (s.poll() != DebugHaltGuard::Event::NONE)
        {
            falseTrigger = true;
            break;
        }
    }
    CHECK(falseTrigger); // without the reload the timeout expires although the software runs
}

void mutantIsrWithoutSafeOutputsLeavesTheBridgeOn()
{
    System s;
    s.deadman.isrSwitchesOff = false;
    s.start();
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    s.halt(30U * 1000U * 1000U);
    CHECK(!s.outputsAreOffInHardware()); // the bridge switches until the guard polls: the burst after `g`
    CHECK(s.poll() == DebugHaltGuard::Event::HALT);
}

void mutantIsrWithoutFlagNeverReports()
{
    System s;
    s.deadman.isrSetsFlag = false;
    s.start();
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    s.halt(30U * 1000U * 1000U);
    CHECK(s.poll() == DebugHaltGuard::Event::NONE); // outputs off, but the driver state and the inhibit stay open
    CHECK(!s.guard.isInhibited());
}

void mutantGuardIgnoringTheTimerBitNeverInhibits()
{
    System s;
    s.guardReadsTheTimerBit = false;
    s.start();
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    s.halt(30U * 1000U * 1000U);
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    CHECK(!s.guard.isInhibited());
}

void mutantFlagNotClearedRepeatsTheHalt()
{
    System s;
    s.deadman.isrClearsFlag = false;
    s.start();
    CHECK(s.poll() == DebugHaltGuard::Event::NONE);
    s.halt(30U * 1000U * 1000U);
    CHECK(s.poll() == DebugHaltGuard::Event::HALT);
    // a flag that is never cleared reports a halt in every poll: no enable could ever release the PWM
    s.output.enable();
    CHECK(s.poll() == DebugHaltGuard::Event::HALT);
    CHECK(s.guard.getHalts() > 1U);
}

} // namespace

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"noEventDoesNothing", noEventDoesNothing},
        {"haltInhibitsPwmAndSwitchesOffEveryOutput", haltInhibitsPwmAndSwitchesOffEveryOutput},
        {"everyDebugEventIsAHalt", everyDebugEventIsAHalt},
        {"staysInhibitedUntilExplicitEnable", staysInhibitedUntilExplicitEnable},
        {"safeStateInhibitWaitsForExplicitEnable", safeStateInhibitWaitsForExplicitEnable},
        {"enableOfAnyOutputReleases", enableOfAnyOutputReleases},
        {"haltDuringInhibitRestartsTheWait", haltDuringInhibitRestartsTheWait},
        {"haltAfterReleaseInhibitsAgain", haltAfterReleaseInhibitsAgain},
        {"withoutOutputsOnlyReleaseEndsTheInhibit", withoutOutputsOnlyReleaseEndsTheInhibit},
        {"withoutPwmOnlyOutputs", withoutPwmOnlyOutputs},
        {"tmc6200OutputSwitchesOffAndWaitsForEnable", tmc6200OutputSwitchesOffAndWaitsForEnable},
        {"tmc6200RefusedEnableDoesNotRelease", tmc6200RefusedEnableDoesNotRelease},
        {"tmc6200HaltWhileDisabled", tmc6200HaltWhileDisabled},
        {"tmc6200EnableBeforeTheHaltDoesNotCount", tmc6200EnableBeforeTheHaltDoesNotCount},
        {"tmc6200EnableCountsEvenIfOffAgain", tmc6200EnableCountsEvenIfOffAgain},
        {"tmc6200NotAttached", tmc6200NotAttached},
        {"tmc6200UninitializedSwitchOff", tmc6200UninitializedSwitchOff},
        {"highSideSwitchOutputSwitchesOffAndWaitsForOn", highSideSwitchOutputSwitchesOffAndWaitsForOn},
        {"highSideSwitchLatchedFaultNeedsResetBeforeItReleases",
         highSideSwitchLatchedFaultNeedsResetBeforeItReleases},
        {"highSideSwitchHaltWhileOff", highSideSwitchHaltWhileOff},
        {"highSideSwitchOnBeforeTheHaltDoesNotCount", highSideSwitchOnBeforeTheHaltDoesNotCount},
        {"highSideSwitchOnCountsEvenIfOffAgain", highSideSwitchOnCountsEvenIfOffAgain},
        {"highSideSwitchNotAttached", highSideSwitchNotAttached},
        {"bothSafeOffOutputsOfTheProject", bothSafeOffOutputsOfTheProject},
        {"safeOutputsTableCarriesEverySafeOffOutput", safeOutputsTableCarriesEverySafeOffOutput},
        {"safeOutputsWalkWritesEveryEntry", safeOutputsWalkWritesEveryEntry},
        {"safeOutputsWalkSkipsAnEntryWhoseClockIsGated", safeOutputsWalkSkipsAnEntryWhoseClockIsGated},
        {"safeOutputsWalkIsRepeatable", safeOutputsWalkIsRepeatable},
        {"deadmanSeesTheHaltTheProbeHides", deadmanSeesTheHaltTheProbeHides},
        {"nothingIsArmedBeforeTheFirstPoll", nothingIsArmedBeforeTheFirstPoll},
        {"noTriggerWhileTheSoftwareRuns", noTriggerWhileTheSoftwareRuns},
        {"aStallLongerThanTheTimeoutIsFailSafe", aStallLongerThanTheTimeoutIsFailSafe},
        {"aHaltShorterThanTheTimeoutIsSeenByTheDfsrOnly", aHaltShorterThanTheTimeoutIsSeenByTheDfsrOnly},
        {"aHaltShorterThanTheTimeoutWithAProbeThatClearsDfsrIsTheBlindSpot",
         aHaltShorterThanTheTimeoutWithAProbeThatClearsDfsrIsTheBlindSpot},
        {"bothSourcesTogetherReportOnce", bothSourcesTogetherReportOnce},
        {"mutantWithoutDbgEnSeesNothing", mutantWithoutDbgEnSeesNothing},
        {"mutantWithoutRetriggerTriggersOnItsOwn", mutantWithoutRetriggerTriggersOnItsOwn},
        {"mutantRetriggerByTvalOnlyMissesTheHalt", mutantRetriggerByTvalOnlyMissesTheHalt},
        {"mutantIsrWithoutSafeOutputsLeavesTheBridgeOn", mutantIsrWithoutSafeOutputsLeavesTheBridgeOn},
        {"mutantIsrWithoutFlagNeverReports", mutantIsrWithoutFlagNeverReports},
        {"mutantGuardIgnoringTheTimerBitNeverInhibits", mutantGuardIgnoringTheTimerBitNeverInhibits},
        {"mutantFlagNotClearedRepeatsTheHalt", mutantFlagNotClearedRepeatsTheHalt},
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
