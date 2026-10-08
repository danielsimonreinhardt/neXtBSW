/* SYS-36: host test of the hold of the safe state S1 - the way through SafeSupervisor and SafeState, not only through
 * the debug halt guard.
 *
 * Board finding 2026-09-21 (session d3, IO-37): after `safety dst` KL30B was off for about 10 ms, then the state
 * machine of the high-side switch wrote IN high again - S1 was entered but not held. This test puts together what
 * the target runs, with the real code everywhere except the hardware:
 * - safety::SafeSupervisor::handle() (ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp, with the
 *   stubs of its own host test for logger, interrupt lock, watchdog and reset),
 * - safety::SafeState (platforms/s32k1xx/port/safety/safeIo/src/safeIo/SafeState.cpp) with the latch and the walk
 *   of hardFaultHandler/safeOutputs.h,
 * - the drivers of libs/bsw (highside::HighSideSwitchChannel, gd::Tmc6200), their adapters
 *   (ARCH-66: the generic SafeOffOutput of ModuleBinding.h with highSideSwitch/HighSideSwitchSafeOff.h, before
 *   HighSideSwitchPowerStageOutput.h; Tmc6200PowerStageOutput.h) and integration::SafeStateHold,
 * on a register model in which the table walk really acts on the pins of the device models: a write of the GPIO
 * clear register PCOR drives IN resp. DRV_EN low, a write of FTM SWOCTRL forces the PWM inactive. The drivers run
 * in their cycle (10 ms), the hold in the poll of the integration (1 ms), and the application asks for "on" in every
 * cycle. After S1 the safe_off outputs must stay low over many cycles, and not even a single write of IN or DRV_EN
 * high may happen.
 *
 * Why the IO-37 test did not find it (DebugHaltGuardHostTest.cpp): its driver scenarios all go through the guard,
 * which calls the adapter (switchOffAfterDebugHalt() -> requestOff()), so the driver was always told. The table walk
 * itself - the only thing SafeState did - was tested on a register file of its own that was connected to no driver
 * model: the PCOR write never reached the IN of the fake switch, and no driver cycle ran after the walk. The
 * safe-state test (SYS-06) checked the registers right after enterSafeState() only, and the safe-supervisor test
 * (SYS-23) replaces SafeState by a stub that records 'S'. No test combined "S1 behind the driver's back" with "the
 * driver keeps running" - exactly the board situation. The mutants below reproduce the finding (without the lock
 * IN is high again within one driver cycle) and the old DRV_EN mismatch error of the gate driver.
 *
 * Build and run (zig c++, no hardware; tools/test -k safe-state-hold):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DPLATFORM_SUPPORT_IO -DPLATFORM_SUPPORT_WATCHDOG \
 *       -Iplatforms/s32k1xx/port/main/src/integration -Iplatforms/s32k1xx/port/safety/safeIo/include \
 *       -Iplatforms/s32k1xx/hardFaultHandler/include -Iplatforms/s32k1xx/port/safety/safeIo/test/host/stubs \
 *       -Iecu/safety/safeSupervisor/include -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -Ilibs/bsw/gdTmc6200/include -Ilibs/bsw/highSideSwitch/include \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       libs/bsw/gdTmc6200/src/gd/Tmc6200.cpp libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitch.cpp \
 *       libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitchChannel.cpp \
 *       platforms/s32k1xx/port/main/src/integration/test/host/SafeStateHoldHostTest.cpp -o build/host/s1hold.exe
 *   build/host/s1hold.exe
 * The include order matters: the real safeIo/SafeState.h comes before the stub of the supervisor test.
 */

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>
#include <string>

/* ------------------------------------------------------------------ records of the supervisor stubs */

namespace test_trace
{
std::string trace;
int lockDepth = 0;

void add(char const c) { trace.push_back(c); }
} // namespace test_trace

namespace test_hold
{
int left = 0;
std::function<void()> duringRound; // what the test looks at inside a round of the hold loop

bool holding()
{
    if (duringRound)
    {
        duringRound();
    }
    if (left <= 0)
    {
        return false;
    }
    --left;
    return true;
}
} // namespace test_hold

#define NX_SAFE_SUPERVISOR_HOLDING()                  (::test_hold::holding())
#define NX_SAFE_SUPERVISOR_CONSOLE(format, event)     ((void)(format), (void)(event))

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

/* ------------------------------------------------------------------ register model of the table */

namespace test_registers
{
volatile uint32_t* reg(uint32_t address);
}

#define NX_SAFE_OUTPUT_REG(address) (::test_registers::reg(address))

// the units under test, compiled into this translation unit so that the register access above applies
#include "../../../../../safety/safeIo/src/safeIo/SafeState.cpp" // NOLINT
#include "../../../../../../../../ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp" // NOLINT

namespace safety
{
// SYS-37: box of the deferred RTE monitor of the supervisor (on the target defined by main.cpp of the port)
SafetyEventMailbox rteTimingMailbox;
} // namespace safety

#include "ModuleBinding.h"
#include "highSideSwitch/HighSideSwitchSafeOff.h"
#include "SafeStateHold.h"
#include "Tmc6200PowerStageOutput.h"

extern "C"
{
volatile uint32_t nxSafeStateLatch;   // on the target: src/SafeStateLatch.cpp of the handler library
volatile uint32_t nxSafeStateEntries; // SYS-35, same place
}

namespace bios
{
Io::PinConfiguration Io::configuration[Io::NUMBER_OF_INPUTS_AND_OUTPUTS] = {};
int Io::setCalls                                                         = 0;
std::string* Io::trace                                                   = nullptr;
} // namespace bios

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

// Addresses of the eval board demo project (as in DebugHaltGuardHostTest.cpp): FTM0 SWOCTRL of the PWM group, DRV_EN
// of the gate driver on PTC11 and IN of the high-side switch on PTD17, both active high (PCOR).
constexpr uint32_t FTM0_SWOCTRL = 0x40038094U;
constexpr uint32_t PTC_PCOR     = 0x400FF088U;
constexpr uint32_t PTD_PCOR     = 0x400FF0C8U;
constexpr uint32_t PCC_FTM0     = 0x400650E0U;
constexpr uint32_t PCC_PORTC    = 0x4006512CU;
constexpr uint32_t PCC_PORTD    = 0x40065130U;
constexpr uint32_t CGC          = 0x40000000U;
constexpr uint32_t DRV_EN_BIT   = 1UL << 11U;
constexpr uint32_t IN_BIT       = 1UL << 17U;
constexpr uint32_t PWM_MASK     = 0x3FU;

/* ------------------------------------------------------------------ device models */

class Pwm
{
public:
    uint16_t duty  = 0U;
    bool inhibited = false; // FTM software output control

    uint16_t pins() const { return inhibited ? uint16_t{0U} : duty; }
};

/* TMC6200 register file with IOIN DRV_EN from the pin and GSTAT write one to clear (datasheet p.22-24). */
class FakeTmc6200
: public ::gd::ITmc6200Spi
, public ::gd::ITmc6200Io
{
public:
    uint32_t reg[128];
    bool drvEnPin        = false;
    int enableHighWrites = 0;
    std::function<void()> onIoinRead; // an interrupt that arrives while the driver reads IOIN

    FakeTmc6200()
    {
        std::memset(reg, 0, sizeof(reg));
        reg[TMC6200_REG_GCONF] = TMC6200_GCONF_RESET;
        reg[TMC6200_REG_IOIN]  = static_cast<uint32_t>(TMC6200_VERSION) << 24U;
    }

    bool transfer(uint64_t tx, uint64_t& rx) override;

    bool setDriverEnable(bool on) override;

    bool readFault(bool& active) override
    {
        active = false;
        return true;
    }
};

/* High-side switch pins; the load current follows IN (1 A on KL30B, as in the board test of IO-37). */
class FakeHighSideSwitchIo : public ::highside::IHighSideSwitchIo
{
public:
    bool inPin    = false;
    bool denPin   = false;
    int onWrites  = 0; // writes of IN high
    std::function<void()> onEnableWrite; // an interrupt that arrives right after IN was written high

    bool setEnable(bool on) override;

    bool setDiagEnable(bool const on) override
    {
        denPin = on;
        return true;
    }

    bool readSense(::highside::SenseSample& sample) override;
};

Pwm pwm;

/* SYS-35: the FTM software output control as the guard drives it (FtmPwmInhibit of DebugHaltGuardSystem.h): inhibit
 * writes the channel mask, release writes 0. The table walk writes the same register through the register model. */
class PwmInhibit : public ::integration::IPwmInhibit
{
public:
    int releases = 0;

    void inhibit() override { pwm.inhibited = true; }

    void release() override
    {
        pwm.inhibited = false;
        ++releases;
    }
};

FakeTmc6200* gateDriver     = nullptr;
FakeHighSideSwitchIo* hssIo = nullptr;
} // namespace

/* The register file of the walk. The walk asks for the address of an entry right before it stores to it, so a store
 * becomes visible at the next access of the model: settle() applies the pending writes - PCOR clears the pin of its
 * bit, SWOCTRL inhibits the PWM - and is called before every access of a device model and before every check. */
namespace test_registers
{
namespace
{
struct Register
{
    uint32_t address;
    uint32_t value;
};

Register file[] = {
    {FTM0_SWOCTRL, 0U},
    {PTC_PCOR, 0U},
    {PTD_PCOR, 0U},
    {PCC_FTM0, CGC},
    {PCC_PORTC, CGC},
    {PCC_PORTD, CGC},
};
int unknownAccesses = 0;
uint32_t sink       = 0U;
} // namespace

void settle()
{
    if (file[0].value != 0U)
    {
        pwm.inhibited = (file[0].value & PWM_MASK) == PWM_MASK;
        file[0].value = 0U;
    }
    if ((file[1].value & DRV_EN_BIT) != 0U)
    {
        if (gateDriver != nullptr)
        {
            gateDriver->drvEnPin = false;
        }
        file[1].value = 0U;
    }
    if ((file[2].value & IN_BIT) != 0U)
    {
        if (hssIo != nullptr)
        {
            hssIo->inPin = false;
        }
        file[2].value = 0U;
    }
}

volatile uint32_t* reg(uint32_t const address)
{
    settle(); // the store of the previous entry has happened
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
} // namespace test_registers

// The table of the eval board project (tools/bswgen): PWM group, then the safe_off outputs of its modules.
extern "C" NxSafeOutput const nxSafeOutputs[] = {
    {FTM0_SWOCTRL, PWM_MASK, PCC_FTM0, CGC},
    {PTC_PCOR, DRV_EN_BIT, PCC_PORTC, CGC},
    {PTD_PCOR, IN_BIT, PCC_PORTD, CGC},
    {0U, 0U, 0U, 0U},
};

namespace
{
bool FakeTmc6200::transfer(uint64_t const tx, uint64_t& rx)
{
    ::test_registers::settle();
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
        if (onIoinRead)
        {
            onIoinRead(); // after the sample: the level read is the one before the interrupt
            ::test_registers::settle();
        }
    }
    rx = value;
    return true;
}

bool FakeTmc6200::setDriverEnable(bool const on)
{
    ::test_registers::settle();
    if (on)
    {
        ++enableHighWrites;
    }
    drvEnPin = on;
    return true;
}

bool FakeHighSideSwitchIo::setEnable(bool const on)
{
    ::test_registers::settle();
    if (on)
    {
        ++onWrites;
    }
    inPin = on;
    if (on && onEnableWrite)
    {
        onEnableWrite();
        ::test_registers::settle();
    }
    return true;
}

bool FakeHighSideSwitchIo::readSense(::highside::SenseSample& sample)
{
    ::test_registers::settle();
    sample.loadMa = inPin ? 1000U : 0U;
    sample.raw    = sample.loadMa;
    return true;
}

// 10 ms cycle, 100 ms blanking, overcurrent 3 A (the 1 A load is fine), off <= 200 mA
constexpr ::highside::HighSideSwitchConfig HSS_CONFIG = {10U, 3000U, 50U, 6000U, 20U, 100U, 0U, 200U, 50U};
static_assert(::highside::isValidHighSideSwitchConfig(HSS_CONFIG), "test configuration invalid");
::gd::Tmc6200Config const GD_CONFIG = ::gd::keepAllConfig();

using HssResult = ::highside::HighSideSwitch::CommandResult;
using GdStatus  = ::gd::Tmc6200::Status;
using GdState   = ::gd::Tmc6200::State;
using ::safety::SafeSupervisor;

/* The latch and the walk as the target integration passes them (DebugHaltGuardSystem.h). */
bool latched() { return ::nxSafeStateIsLatched() != 0; }

void walk() { ::nxSafeOutputsSwitchOff(); }

bool noLock() { return false; }

uint32_t entries() { return ::nxSafeStateEntryCount(); }

/* SYS-35: the lock the target hands the drivers (safeStateLock() of Integration.cpp): SafeStateHold::isLocking() of the
 * hold of the running bench. */
::integration::SafeStateHold const* activeHold = nullptr;

bool holdLock() { return (activeHold != nullptr) && activeHold->isLocking(); }

/** A fresh supervisor (as main() constructs it) that has left limp home. */
SafeSupervisor& supervisor()
{
    ::safety::safeSupervisorConstructor.construct();
    auto& s = SafeSupervisor::getInstance();
    s.leaveLimpHome();
    ::test_trace::trace.clear();
    ::test_trace::lockDepth = 0;
    ::test_hold::left       = 0;
    ::test_hold::duringRound = nullptr;
    return s;
}

/**
 * The power stage of the eval board demo project as the target runs it: high-side switch KL30B and gate driver
 * MOT1_GD in their 10 ms cycle, the hold in the 1 ms poll, both in TASK_BSP; the application asks for "on" in every
 * cycle once it has switched on.
 */
struct Bench
{
    FakeHighSideSwitchIo io;
    ::highside::HighSideSwitchChannel channel{io, HSS_CONFIG};
    FakeTmc6200 dev;
    ::gd::Tmc6200 driver{dev, dev, GD_CONFIG};
    ::integration::SafeOffOutput<::highside::HighSideSwitchSafeOff<::highside::HighSideSwitchChannel>> hssOutput;
    ::integration::Tmc6200PowerStageOutput gdOutput;
    ::integration::IPowerStageOutput* outputs[2] = {&gdOutput, &hssOutput};
    PwmInhibit pwmInhibit;
    ::integration::DebugHaltGuard guard{outputs, 2U, &pwmInhibit};
    // SYS-35 mutant "before SYS-35": the hold hands the inhibit of S1 to a guard that owns neither PWM nor outputs
    ::integration::DebugHaltGuard nobody{nullptr, 0U, nullptr};
    ::integration::SafeStateHold hold;

    // configuration of the run (the mutants switch parts off)
    bool driversLocked = true; // adapters attached with the latch (SYS-36) or without a lock (before)
    bool withHold      = true;
    bool appWantsOn    = true;

    uint32_t nowMs = 0U;

    // records after S1
    bool s1            = false;
    int inHighMs       = 0; // ms with IN high at the end of the ms
    int drvEnHighMs    = 0;
    int pwmActiveMs    = 0;
    int inOnWrites     = 0; // writes of IN high after S1 (even a transient one counts)
    int drvEnOnWrites  = 0;
    int firstInHighAt  = -1;

    explicit Bench(bool const handOverPwm = true)
    : hold(outputs, 2U, &latched, &walk, &entries, handOverPwm ? guard : nobody)
    {
        ::nxSafeStateLatch   = 0U; // a reset
        ::nxSafeStateEntries = 0U;
        activeHold           = &hold;
        hssIo                = &io;
        gateDriver         = &dev;
        pwm                = Pwm();
    }

    ~Bench()
    {
        hssIo      = nullptr;
        gateDriver = nullptr;
        activeHold = nullptr;
    }

    Bench(Bench const&)            = delete;
    Bench& operator=(Bench const&) = delete;

    /** Start and switch on: KL30B on (1 A load), gate driver enabled, PWM running. */
    void start()
    {
        hssOutput.attach(channel, driversLocked ? &holdLock : &noLock);
        gdOutput.attach(driver, driversLocked ? &holdLock : &noLock);
        channel.init();
        CHECK(driver.init(0U) == GdStatus::OK);
        CHECK(channel.requestOn() == HssResult::OK);
        CHECK(driver.enable() == GdStatus::OK);
        pwm.duty = 5000U;
        run(200U); // past the blanking time, the load is supervised
        CHECK(io.inPin);
        CHECK(dev.drvEnPin);
        CHECK(pwm.pins() == 5000U);
        CHECK(driver.getLatchedFaults() == 0U);
    }

    /** One millisecond of TASK_BSP. */
    void ms()
    {
        ++nowMs;
        if ((nowMs % 10U) == 0U)
        {
            channel.cycle();
            driver.cyclic(nowMs);
            if (appWantsOn)
            {
                (void)channel.requestOn(); // the application insists (RTE, console "kl30b on")
                (void)driver.enable();
            }
        }
        if (withHold)
        {
            // the poll of DebugHaltGuardSystem::execute(): the hold first, then the guard (no debug event here)
            lastHoldEvent = hold.cyclic();
            if (lastHoldEvent == ::integration::SafeStateHold::Event::LEFT)
            {
                ++leftEvents;
            }
            (void)guard.cyclic(0U);
        }
        ::test_registers::settle();
        if (s1)
        {
            if (io.inPin)
            {
                ++inHighMs;
                if (firstInHighAt < 0)
                {
                    firstInHighAt = static_cast<int>(nowMs - s1AtMs);
                }
            }
            drvEnHighMs += dev.drvEnPin ? 1 : 0;
            pwmActiveMs += (pwm.pins() != 0U) ? 1 : 0;
        }
    }

    void run(uint32_t const count)
    {
        for (uint32_t i = 0U; i < count; ++i)
        {
            ms();
        }
    }

    /** Marks the moment of S1 (right after the event) and counts the high writes from here on. */
    void markS1()
    {
        ::test_registers::settle();
        s1        = true;
        s1AtMs    = nowMs;
        onWrites0 = io.onWrites;
        enables0  = dev.enableHighWrites;
    }

    void collect()
    {
        inOnWrites    = io.onWrites - onWrites0;
        drvEnOnWrites = dev.enableHighWrites - enables0;
    }

    uint32_t s1AtMs = 0U;
    int onWrites0   = 0;
    int enables0    = 0;
    ::integration::SafeStateHold::Event lastHoldEvent = ::integration::SafeStateHold::Event::NONE;
    int leftEvents                                     = 0;
};

/* ---------------------------------------------------------------------------------- the board finding */

/** `safety dst`: CHECK_REGISTER_ERROR enters S1 without a reset. Everything stays off for 10 s of driver cycles. */
void safetyDstHoldsEverySafeOffOutput()
{
    Bench b;
    b.start();
    auto& s = supervisor();
    b.run(3U); // somewhere between two driver cycles
    s.checkRegisterMonitor.trigger(0U); // SafeIo saw SAFETY_TEST disabled
    b.markS1();
    CHECK(::test_trace::trace.find('R') == std::string::npos); // no reset (SM-22)
    CHECK(latched());
    CHECK(!b.io.inPin); // the walk: at once
    CHECK(!b.dev.drvEnPin);
    CHECK(pwm.pins() == 0U);

    b.run(10000U); // 1000 driver cycles, "on" requested in every one of them
    b.collect();
    CHECK(b.inHighMs == 0);
    CHECK(b.drvEnHighMs == 0);
    CHECK(b.pwmActiveMs == 0);
    CHECK(b.inOnWrites == 0); // not even one write of IN high
    CHECK(b.drvEnOnWrites == 0);
    CHECK(b.channel.getSwitch().isLocked());
    CHECK(b.channel.requestOn() == HssResult::LOCKED);
    CHECK(b.driver.isLocked());
    CHECK(b.driver.enable() == GdStatus::LOCKED);
    CHECK(b.driver.getState() == GdState::DISABLED);
    // the gate driver no longer takes S1 for a wiring fault (before SYS-36: "DRV_EN mismatch", latched ERROR)
    CHECK((b.driver.getLatchedFaults() & ::gd::fault::ENABLE_MISMATCH) == 0U);
    CHECK(b.driver.getLatchedFaults() == 0U);
    CHECK(b.driver.getCounters().shutdowns == 0U); // no fault shutdown either
    // the switch has no fault latched: the load current went to 0 with IN, no off-state implausibility
    CHECK(b.channel.getSwitch().getState() == ::highside::HighSideSwitch::State::OFF);
    CHECK(b.hold.isHeld());
    CHECK(b.hold.getUnlockedOutputs() == 0U);
    CHECK(test_registers::unknownAccesses == 0);
}

/** SYS-35 (decision safe-state-rueckweg): in S1 every enable is refused with a clear message, "on" of the switch as
 * well - `gd clear` first does not help either, and the PWM stays inhibited. */
void enableInSafeStateIsRefusedClearly()
{
    Bench b;
    b.start();
    auto& s = supervisor();
    s.checkRegisterMonitor.trigger(0U); // safety dst
    b.markS1();
    b.run(50U);
    CHECK(b.driver.clearFaults() == GdStatus::OK); // gd clear
    GdStatus const status = b.driver.enable();     // gd enable
    CHECK(status == GdStatus::LOCKED);
    CHECK(std::strcmp(::gd::Tmc6200::statusName(status), "refused: safe state") == 0);
    CHECK(b.driver.getState() == GdState::DISABLED);
    CHECK(b.channel.requestOn() == HssResult::LOCKED);
    b.run(1000U);
    b.collect();
    CHECK(b.drvEnHighMs == 0);
    CHECK(b.pwmActiveMs == 0);
    CHECK(b.drvEnOnWrites == 0);
    CHECK(b.inOnWrites == 0);
    CHECK(b.guard.isInhibited()); // the guard owns the inhibit of S1
    CHECK(b.pwmInhibit.releases == 0);
    CHECK(b.guard.getHalts() == 0U); // S1 is no debug halt
}

/** SYS-35, the board finding of 2026-09-21: `safety dst`, `safety est`, `gd clear`, `gd enable`. Leaving switches
 * nothing on; the explicit enable afterwards lifts the lock of DRV_EN and releases the PWM with it, so the bridge
 * really switches (before SYS-35: enabled, IOIN 1, phases floating until a reboot). */
void safetyEstThenExplicitEnableReleasesThePowerStage()
{
    Bench b;
    b.start();
    auto& s = supervisor();
    s.checkRegisterMonitor.trigger(0U); // safety dst
    b.markS1();
    b.appWantsOn = false; // from here on only the console acts
    b.run(50U);
    s.checkRegisterMonitor.trigger(1U); // safety est: leaveSafeState()
    CHECK(!latched());
    b.run(5000U); // nothing switches on by itself
    b.collect();
    CHECK(b.inOnWrites == 0);
    CHECK(b.drvEnOnWrites == 0);
    CHECK(b.inHighMs == 0);
    CHECK(b.drvEnHighMs == 0);
    CHECK(b.pwmActiveMs == 0); // the PWM stays inhibited until the explicit enable
    CHECK(b.leftEvents == 1);  // "safe state left" once
    CHECK(!b.hold.isHeld());
    CHECK(!b.driver.isLocked());
    CHECK(!b.channel.getSwitch().isLocked());
    CHECK(b.driver.getLatchedFaults() == 0U); // no DRV_EN mismatch from S1

    CHECK(b.driver.clearFaults() == GdStatus::OK); // gd clear
    CHECK(b.driver.enable() == GdStatus::OK);      // gd enable
    CHECK(b.driver.getState() == GdState::ENABLED);
    b.run(2U); // the next poll of the guard
    CHECK(b.dev.drvEnPin);
    CHECK(pwm.pins() == 5000U); // the bridge switches again with the last duty
    CHECK(b.pwmInhibit.releases == 1);
    CHECK(!b.guard.isInhibited());
    // the high-side switch was not enabled: it stays off until its own explicit "on"
    CHECK(!b.io.inPin);
    CHECK(b.channel.requestOn() == HssResult::OK);
    ::test_registers::settle();
    CHECK(b.io.inPin);
    b.run(1000U);
    CHECK(b.dev.drvEnPin);
    CHECK(b.io.inPin);
    CHECK(pwm.pins() == 5000U);
    CHECK(b.driver.getLatchedFaults() == 0U);
    CHECK(test_registers::unknownAccesses == 0);
}

/** SYS-35: the application that keeps asking for "on" gets it with its first enable after the return, not before. */
void applicationEnableAfterTheReturn()
{
    Bench b;
    b.start();
    auto& s = supervisor();
    s.checkRegisterMonitor.trigger(0U);
    b.markS1();
    b.run(2000U); // "on" and enable requested in every driver cycle: all refused
    b.collect();
    CHECK(b.inOnWrites == 0);
    CHECK(b.drvEnOnWrites == 0);
    CHECK(b.pwmActiveMs == 0);
    s.checkRegisterMonitor.trigger(1U);
    b.run(20U); // at most two driver cycles
    CHECK(b.io.inPin);
    CHECK(b.dev.drvEnPin);
    CHECK(pwm.pins() == 5000U);
    CHECK(b.driver.getLatchedFaults() == 0U);
}

/** SYS-35: S1 entered and left again between two polls of the hold - the entry count still hands the PWM inhibit of
 * the walk to the guard, so an enable releases it (a hold that looked at the latch only would miss this entry and
 * leave the PWM inhibited for good). */
void entryAndReturnBetweenTwoPollsIsNotMissed()
{
    Bench b;
    b.appWantsOn = false;
    b.start();
    ::safety::SafeState::enterSafeState();
    ::safety::SafeState::leaveSafeState();
    ::test_registers::settle();
    CHECK(pwm.pins() == 0U); // the walk has inhibited the PWM
    CHECK(!b.dev.drvEnPin);
    b.run(1U);
    CHECK(b.lastHoldEvent == ::integration::SafeStateHold::Event::LEFT);
    CHECK(b.guard.isInhibited());
    b.run(500U);
    CHECK(pwm.pins() == 0U);
    CHECK(b.driver.getState() == GdState::DISABLED); // the switch-off of the guard reached the driver state
    CHECK(b.driver.enable() == GdStatus::OK);
    b.run(2U);
    CHECK(pwm.pins() == 5000U);
}

/** SYS-35: S1 entered and left right before a driver cycle, before the hold has polled. The drivers never saw the
 * latch; the lock of the hold (an entry not handled yet) keeps them from driving their outputs again until the hold
 * has switched them off. With the latch alone as lock, IN and DRV_EN would be high again without an enable. */
void driverCycleBetweenReturnAndPollDoesNotSwitchOn()
{
    for (int variant = 0; variant < 2; ++variant)
    {
        Bench b;
        b.start();
        b.appWantsOn = false;
        b.run(9U - (b.nowMs % 10U)); // the next ms runs the driver cycles
        CHECK((b.nowMs % 10U) == 9U);
        b.withHold = false; // the poll comes after the driver cycle of this ms
        ::safety::SafeState::enterSafeState();
        ::safety::SafeState::leaveSafeState();
        b.markS1();
        if (variant == 1)
        {
            activeHold = nullptr; // mutant: the latch alone as lock (it is 0 already)
        }
        b.run(1U); // driver cycles, no poll
        b.collect();
        if (variant == 0)
        {
            CHECK(b.inOnWrites == 0);
            CHECK(!b.io.inPin);
            CHECK(!b.dev.drvEnPin);
            CHECK(b.driver.getState() == GdState::DISABLED);
            CHECK(b.channel.requestOn() == HssResult::LOCKED); // not yet: the hold has not handled the entry
            b.withHold = true;
            b.run(1U);
            CHECK(b.guard.isInhibited());
            CHECK(!b.hold.isLocking());
            b.run(100U);
            CHECK(!b.io.inPin);
            CHECK(!b.dev.drvEnPin);
            CHECK(b.channel.requestOn() == HssResult::OK); // now the explicit "on" works
        }
        else
        {
            CHECK(b.inOnWrites > 0); // IN rewritten high by the unlocked switch - no enable in between
        }
    }
}

/** SYS-35 mutant "before SYS-35": nobody takes over the PWM inhibit of S1. After the return the driver reports
 * enabled with DRV_EN high at the device, but the PWM stays inhibited - the board finding (phases floating). */
void mutantWithoutHandOverReproducesTheFloatingPhases()
{
    Bench b(false);
    b.start();
    auto& s = supervisor();
    s.checkRegisterMonitor.trigger(0U);
    b.markS1();
    b.appWantsOn = false;
    b.run(50U);
    s.checkRegisterMonitor.trigger(1U);
    b.run(50U);
    CHECK(b.driver.clearFaults() == GdStatus::OK);
    CHECK(b.driver.enable() == GdStatus::OK);
    b.run(1000U);
    CHECK(b.driver.getState() == GdState::ENABLED); // "gd enable: ok, state enabled"
    CHECK((b.driver.getLastIoin() & TMC6200_IOIN_DRV_EN_MASK) != 0U); // IOIN DRV_EN 1
    CHECK(pwm.pins() == 0U);                                          // but the bridge does not switch
    CHECK(b.pwmInhibit.releases == 0);
}

/** A resetting event (limp home, S1, reset): until the reset has happened the outputs stay locked as well. */
void resettingEventLatchesBeforeTheReset()
{
    Bench b;
    b.start();
    auto& s = supervisor();
    s.mpuConfigMonitor.trigger(3U);
    b.markS1();
    CHECK(::test_trace::trace.find('R') != std::string::npos);
    CHECK(latched());
    // on the target the reset comes now; here the software runs on, as it may for a moment before the reset
    b.run(1000U);
    b.collect();
    CHECK(b.inHighMs == 0);
    CHECK(b.drvEnHighMs == 0);
    CHECK(b.inOnWrites == 0);
}

/** SYS-23 hold (WATCHDOG_STARTUP_CHECK_FAILURE): every round walks the table and the latch is set from the first. */
void holdOfSys23LatchesS1()
{
    Bench b;
    b.start();
    auto& s             = supervisor();
    int roundsLatched   = 0;
    int roundsOutputsOn = 0;
    ::test_hold::left   = 20;
    ::test_hold::duringRound = [&b, &roundsLatched, &roundsOutputsOn]() {
        ::test_registers::settle();
        roundsLatched += latched() ? 1 : 0;
        roundsOutputsOn += (b.io.inPin || b.dev.drvEnPin || (pwm.pins() != 0U)) ? 1 : 0;
    };
    s.watchdogStartupCheckMonitor.trigger();
    ::test_hold::duringRound = nullptr;
    CHECK(roundsLatched == 21);
    CHECK(roundsOutputsOn == 0);
    CHECK(s.holdRounds() == 20U);
    // the hold ends only with a power cycle on the target; were the drivers to run on, they would stay locked
    b.markS1();
    b.run(2000U);
    b.collect();
    CHECK(b.inOnWrites == 0);
    CHECK(b.drvEnOnWrites == 0);
    CHECK(b.inHighMs == 0);
}

/** S1 straight from an interrupt of any priority (SafeState alone, e.g. a future supervisor path in an ISR). */
void safeStateFromAnInterruptHolds()
{
    Bench b;
    b.start();
    b.run(7U);
    ::safety::SafeState::enterSafeState();
    b.markS1();
    b.run(3000U);
    b.collect();
    CHECK(b.inHighMs == 0);
    CHECK(b.drvEnHighMs == 0);
    CHECK(b.inOnWrites == 0);
    CHECK(b.drvEnOnWrites == 0);
}

/* ---------------------------------------------------------------------------------- races */

/** S1 arrives right after the switch wrote IN high (an interrupt between the lock check and the write): IN goes low
 * again before the channel returns, not one cycle later. */
void s1DuringSwitchOnTakesInBackAtOnce()
{
    Bench b;
    b.appWantsOn = false;
    b.start();
    CHECK(b.channel.requestOff() == HssResult::OK);
    b.run(20U);
    bool fired       = false;
    b.io.onEnableWrite = [&fired]() {
        if (!fired)
        {
            fired = true;
            ::safety::SafeState::enterSafeState();
        }
    };
    CHECK(b.channel.requestOn() == HssResult::OK); // accepted before S1
    ::test_registers::settle();
    CHECK(fired);
    CHECK(!b.io.inPin); // taken back inside requestOn()
    CHECK(!b.io.denPin);
    CHECK(b.channel.getSwitch().isLocked());
    b.io.onEnableWrite = nullptr;
    b.markS1();
    b.appWantsOn = true;
    b.run(1000U);
    b.collect();
    CHECK(b.inOnWrites == 0);
    CHECK(b.inHighMs == 0);
}

/** S1 arrives while the gate driver reads IOIN in its supervision (after the sample): no ENABLE_MISMATCH. */
void s1DuringGateDriverSupervisionIsNoMismatch()
{
    Bench b;
    b.start();
    bool fired       = false;
    b.dev.onIoinRead = [&fired]() {
        if (!fired)
        {
            fired = true;
            ::safety::SafeState::enterSafeState();
        }
    };
    b.run(10U); // the next driver cycle
    CHECK(fired);
    b.dev.onIoinRead = nullptr;
    b.markS1();
    b.run(1000U);
    b.collect();
    CHECK((b.driver.getLatchedFaults() & ::gd::fault::ENABLE_MISMATCH) == 0U);
    CHECK(b.driver.getState() == GdState::DISABLED);
    CHECK(b.drvEnHighMs == 0);
    CHECK(b.drvEnOnWrites == 0);
}

/** S1 arrives while enable() switches DRV_EN on: DRV_EN low again before enable() returns, LOCKED. */
void s1DuringGateDriverEnableIsTakenBack()
{
    Bench b;
    b.appWantsOn = false;
    b.start();
    CHECK(b.driver.disable() == GdStatus::OK);
    bool fired       = false;
    b.dev.onIoinRead = [&fired]() {
        if (!fired)
        {
            fired = true;
            ::safety::SafeState::enterSafeState();
        }
    };
    CHECK(b.driver.enable() == GdStatus::LOCKED);
    CHECK(fired);
    ::test_registers::settle();
    CHECK(!b.dev.drvEnPin);
    CHECK(b.driver.getState() == GdState::DISABLED);
    CHECK(b.driver.getLatchedFaults() == 0U);
}

/* ---------------------------------------------------------------------------------- the way out: today a reset */

/** Once the latch reads 0 (after a reset, or the return path of SYS-35) nothing switches on by itself: the drivers
 * unlock and wait for the next explicit "on", and the PWM comes back with the enable. */
void clearedLatchSwitchesNothingOn()
{
    Bench b;
    b.start();
    ::safety::SafeState::enterSafeState();
    b.markS1();
    b.appWantsOn = false;
    b.run(100U);
    ::safety::SafeState::leaveSafeState(); // the return path (SYS-35)
    b.run(1000U);
    b.collect();
    CHECK(!b.channel.getSwitch().isLocked());
    CHECK(!b.driver.isLocked());
    CHECK(!b.hold.isHeld());
    CHECK(b.inOnWrites == 0);
    CHECK(b.drvEnOnWrites == 0);
    CHECK(!b.io.inPin);
    CHECK(!b.dev.drvEnPin);
    CHECK(pwm.pins() == 0U);
    CHECK(b.channel.requestOn() == HssResult::OK); // an explicit "on" works again
    CHECK(b.driver.enable() == GdStatus::OK);
    ::test_registers::settle();
    CHECK(b.io.inPin);
    CHECK(b.dev.drvEnPin);
    b.run(2U);
    CHECK(pwm.pins() == 5000U);
}

/** Without S1 nothing of this changes the drivers: many cycles, no lock, outputs as the application wants. */
void withoutS1NothingChanges()
{
    Bench b;
    b.start();
    b.run(5000U);
    CHECK(b.io.inPin);
    CHECK(b.dev.drvEnPin);
    CHECK(pwm.pins() == 5000U);
    CHECK(!b.hold.isHeld());
    CHECK(!b.channel.getSwitch().isLocked());
    CHECK(!b.driver.isLocked());
    CHECK(b.driver.getLatchedFaults() == 0U);
}

/* ---------------------------------------------------------------------------------- mutants */

/** Mutant "before SYS-36": no lock reaches the drivers and no hold - the board finding. IN is high again within one
 * driver cycle, and the gate driver latches the DRV_EN mismatch as a fault. */
void mutantWithoutLockReproducesTheBoardFinding()
{
    Bench b;
    b.driversLocked = false;
    b.withHold      = false;
    b.start();
    b.run(3U);
    supervisor().checkRegisterMonitor.trigger(0U);
    b.markS1();
    CHECK(!b.io.inPin);
    b.run(1000U);
    b.collect();
    CHECK(b.firstInHighAt > 0);
    CHECK(b.firstInHighAt <= 10); // within one cycle of the switch: "about 10 ms" on the board
    CHECK(b.inHighMs > 900);
    CHECK((b.driver.getLatchedFaults() & ::gd::fault::ENABLE_MISMATCH) != 0U); // the old "DRV_EN mismatch" ERROR
}

/** Mutant "hold without driver lock": the table walk of every poll pulls IN back low, but the driver still writes it
 * high in every cycle (a pulse of up to one poll) and the hold reports both outputs as not locked. */
void mutantHoldWithoutDriverLockIsNotEnough()
{
    Bench b;
    b.driversLocked = false;
    b.start();
    supervisor().checkRegisterMonitor.trigger(0U);
    b.markS1();
    b.run(1000U);
    b.collect();
    CHECK(b.inOnWrites > 0);
    CHECK(b.hold.getUnlockedOutputs() == 2U);
}

/** The driver lock alone (without the hold) already keeps every output low: the hold is the backstop, not the
 * mechanism. */
void driverLockAloneHolds()
{
    Bench b;
    b.withHold = false;
    b.start();
    supervisor().checkRegisterMonitor.trigger(0U);
    b.markS1();
    b.run(3000U);
    b.collect();
    CHECK(b.inHighMs == 0);
    CHECK(b.drvEnHighMs == 0);
    CHECK(b.inOnWrites == 0);
    CHECK(b.drvEnOnWrites == 0);
}

/** An adapter that is not attached (no driver object yet) is reported by the hold, and the walk keeps the pin low. */
void unattachedAdapterIsReported()
{
    ::nxSafeStateLatch   = 0U;
    ::nxSafeStateEntries = 0U;
    ::integration::SafeOffOutput<::highside::HighSideSwitchSafeOff<::highside::HighSideSwitchChannel>> lonely;
    ::integration::IPowerStageOutput* outputs[] = {&lonely};
    ::integration::DebugHaltGuard guard(outputs, 1U, nullptr);
    ::integration::SafeStateHold hold(outputs, 1U, &latched, &walk, &entries, guard);
    CHECK(hold.cyclic() == ::integration::SafeStateHold::Event::NONE);
    ::nxSafeStateLatchSet();
    CHECK(hold.cyclic() == ::integration::SafeStateHold::Event::ENTERED);
    CHECK(hold.getUnlockedOutputs() == 1U);
    CHECK(hold.cyclic() == ::integration::SafeStateHold::Event::HELD);
    ::nxSafeStateLatchClear();
    CHECK(hold.cyclic() == ::integration::SafeStateHold::Event::LEFT); // SYS-35: once
    CHECK(!hold.isHeld());
    CHECK(hold.cyclic() == ::integration::SafeStateHold::Event::NONE);
}
} // namespace

int main()
{
    safetyDstHoldsEverySafeOffOutput();
    enableInSafeStateIsRefusedClearly();
    safetyEstThenExplicitEnableReleasesThePowerStage();
    applicationEnableAfterTheReturn();
    entryAndReturnBetweenTwoPollsIsNotMissed();
    driverCycleBetweenReturnAndPollDoesNotSwitchOn();
    mutantWithoutHandOverReproducesTheFloatingPhases();
    resettingEventLatchesBeforeTheReset();
    holdOfSys23LatchesS1();
    safeStateFromAnInterruptHolds();
    s1DuringSwitchOnTakesInBackAtOnce();
    s1DuringGateDriverSupervisionIsNoMismatch();
    s1DuringGateDriverEnableIsTakenBack();
    clearedLatchSwitchesNothingOn();
    withoutS1NothingChanges();
    mutantWithoutLockReproducesTheBoardFinding();
    mutantHoldWithoutDriverLockIsNotEnough();
    driverLockAloneHolds();
    unattachedAdapterIsReported();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
