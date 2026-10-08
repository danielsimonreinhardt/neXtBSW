/* ARCH-66 (safety review ARCH-52 B2/B5, 2026-09-30): the move of the high-side switch to the generated binding
 * must behave exactly like the hand-written adapter it replaces. This test runs both side by side in lockstep:
 *
 *   reference  HighSideSwitchPowerStageOutput.h as it was until ARCH-66 (verbatim copy below, commit ef544944)
 *   generic    integration::SafeOffOutput (ModuleBinding.h) with the access struct of the module's trait
 *              (highside::HighSideSwitchSafeOff, libs/bsw/highSideSwitch/include/highSideSwitch/HighSideSwitchSafeOff.h)
 *
 * Each of the two drives its own real highside::HighSideSwitchChannel (libs/bsw) on its own pin model; both get the
 * same sequence of events - driver cycles, "on"/"off"/"reset" of the application, load currents (overcurrent and
 * fault latches), the lock of the safe state S1 set and cleared, the debug halt guard's switch off, the hold's
 * holdForSafeState() - from fixed scenarios and from 200 pseudo-random sequences of 400 steps. After every step
 * every observable must be equal: the return values of the adapter calls, the pins IN and DEN, the number of writes
 * of IN high, the state and counters of the state machine. Also an unattached adapter (no system yet) on both.
 *
 * tools/test -k safe-off-equivalence; mutants of the generic side: test_safe_off_equivalence_mutations.py.
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/main/src/integration -Ilibs/bsw/highSideSwitch/include \
 *       libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitch.cpp \
 *       libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitchChannel.cpp \
 *       platforms/s32k1xx/port/main/src/integration/test/host/SafeOffEquivalenceHostTest.cpp -o safeoff.exe
 */

#include "DebugHaltGuard.h"
#include "ModuleBinding.h"
#include "highSideSwitch/HighSideSwitchChannel.h"
#include "highSideSwitch/HighSideSwitchSafeOff.h"

#include <cstdint>
#include <cstdio>

namespace reference
{
// ---- verbatim copy of platforms/s32k1xx/port/main/src/integration/HighSideSwitchPowerStageOutput.h (removed by
// ARCH-66), namespace changed from integration to reference, comments shortened ----
using ::integration::IPowerStageOutput;

class HighSideSwitchPowerStageOutput : public IPowerStageOutput
{
public:
    void attach(
        ::highside::HighSideSwitchChannel& channel, ::highside::HighSideSwitchChannel::SwitchOnLock const lock)
    {
        _channel = &channel;
        channel.setSwitchOnLock(lock);
    }

    bool holdForSafeState() override { return (_channel != nullptr) && _channel->updateSwitchOnLock(); }

    void switchOffAfterDebugHalt() override
    {
        if (_channel == nullptr)
        {
            return; // no channel yet: IN is still the low level of the pin configuration
        }
        (void)_channel->requestOff();
        _switchOnsAtSwitchOff = _channel->getSwitch().getCounters().switchOns;
        _switchedOff          = true;
    }

    bool enabledSinceDebugHaltSwitchOff() const override
    {
        return _switchedOff && (_channel != nullptr)
               && (_channel->getSwitch().getCounters().switchOns != _switchOnsAtSwitchOff);
    }

private:
    ::highside::HighSideSwitchChannel* _channel = nullptr;
    uint32_t _switchOnsAtSwitchOff              = 0U;
    bool _switchedOff                           = false;
};
// ---- end of the copy ----
} // namespace reference

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

using Generic = ::integration::SafeOffOutput<::highside::HighSideSwitchSafeOff<::highside::HighSideSwitchChannel>>;
using HssResult = ::highside::HighSideSwitch::CommandResult;

class PinModel : public ::highside::IHighSideSwitchIo
{
public:
    bool inPin      = false;
    bool denPin     = false;
    uint32_t loadMa = 0U;
    bool adcOk      = true;
    uint32_t inHigh = 0U;
    uint32_t writes = 0U;

    bool setEnable(bool const on) override
    {
        ++writes;
        inHigh += on ? 1U : 0U;
        inPin = on;
        return true;
    }

    bool setDiagEnable(bool const on) override
    {
        ++writes;
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

// 10 ms cycle, 100 ms blanking, 50 ms overcurrent debounce, 20 ms fault debounce, open load below 100 mA
constexpr ::highside::HighSideSwitchConfig CONFIG = {10U, 3000U, 50U, 6000U, 20U, 100U, 100U, 200U, 50U};
static_assert(::highside::isValidHighSideSwitchConfig(CONFIG), "test configuration invalid");

// the lock of the safe state both channels ask (on the target the S1 latch, SafeStateHold)
bool s1 = false;

bool safeStateLock() { return s1; }

/** One side: a channel on its pin model and its adapter. */
template<class Adapter>
struct Side
{
    PinModel pins;
    ::highside::HighSideSwitchChannel channel{pins, CONFIG};
    Adapter adapter;
};

struct Bench
{
    Side<::reference::HighSideSwitchPowerStageOutput> ref;
    Side<Generic> gen;
    uint32_t step = 0U;

    Bench()
    {
        s1 = false;
        ref.channel.init();
        gen.channel.init();
    }

    void attach()
    {
        ref.adapter.attach(ref.channel, &safeStateLock);
        gen.adapter.attach(gen.channel, &safeStateLock);
    }

    /** Every observable of both sides equal. */
    void compare(char const* what)
    {
        ++step;
        auto const& a = ref.channel.getSwitch();
        auto const& b = gen.channel.getSwitch();
        bool const same
            = (ref.pins.inPin == gen.pins.inPin) && (ref.pins.denPin == gen.pins.denPin)
              && (ref.pins.inHigh == gen.pins.inHigh) && (ref.pins.writes == gen.pins.writes)
              && (a.getState() == b.getState()) && (a.getCounters().switchOns == b.getCounters().switchOns)
              && (ref.adapter.enabledSinceDebugHaltSwitchOff() == gen.adapter.enabledSinceDebugHaltSwitchOff());
        ++checks;
        if (!same)
        {
            ++failures;
            std::printf(
                "  FAIL step %u (%s): IN %d/%d DEN %d/%d IN-high %u/%u state %d/%d switchOns %u/%u enabled %d/%d\n",
                step,
                what,
                ref.pins.inPin,
                gen.pins.inPin,
                ref.pins.denPin,
                gen.pins.denPin,
                ref.pins.inHigh,
                gen.pins.inHigh,
                static_cast<int>(a.getState()),
                static_cast<int>(b.getState()),
                a.getCounters().switchOns,
                b.getCounters().switchOns,
                ref.adapter.enabledSinceDebugHaltSwitchOff(),
                gen.adapter.enabledSinceDebugHaltSwitchOff());
        }
    }

    void cycle()
    {
        ref.channel.cycle();
        gen.channel.cycle();
        compare("cycle");
    }

    void on()
    {
        HssResult const r = ref.channel.requestOn();
        HssResult const g = gen.channel.requestOn();
        CHECK(r == g);
        compare("on");
    }

    void off()
    {
        CHECK(ref.channel.requestOff() == gen.channel.requestOff());
        compare("off");
    }

    void reset()
    {
        CHECK(ref.channel.reset() == gen.channel.reset());
        compare("reset");
    }

    void load(uint32_t const ma)
    {
        ref.pins.loadMa = ma;
        gen.pins.loadMa = ma;
    }

    void halt()
    {
        ref.adapter.switchOffAfterDebugHalt();
        gen.adapter.switchOffAfterDebugHalt();
        compare("switchOffAfterDebugHalt");
    }

    /** holdForSafeState() of both (it evaluates the lock and may write IN): the common answer. */
    bool hold()
    {
        bool const r = ref.adapter.holdForSafeState();
        bool const g = gen.adapter.holdForSafeState();
        CHECK(r == g);
        compare("holdForSafeState");
        return g;
    }
};

/* ------------------------------------------------------------------------------------------ scenarios */

void notAttached()
{
    // no system yet: nothing switched, nothing reported, no crash - on both
    Bench b;
    b.halt();
    CHECK(!b.hold());
    CHECK(!b.gen.adapter.enabledSinceDebugHaltSwitchOff());
    b.on();
    b.cycle();
    CHECK(!b.gen.adapter.enabledSinceDebugHaltSwitchOff()); // never switched off: no release
}

void haltSwitchesOffAndWaitsForOn()
{
    Bench b;
    b.attach();
    b.on();
    CHECK(b.gen.pins.inPin);
    b.halt();
    CHECK(!b.gen.pins.inPin);
    CHECK(!b.gen.adapter.enabledSinceDebugHaltSwitchOff());
    for (int i = 0; i < 50; ++i)
    {
        b.cycle();
    }
    CHECK(!b.gen.pins.inPin);
    b.on();
    CHECK(b.gen.adapter.enabledSinceDebugHaltSwitchOff());
    b.off();
    CHECK(b.gen.adapter.enabledSinceDebugHaltSwitchOff()); // the release follows the switch-on, not the state
}

void latchedFaultNeedsReset()
{
    Bench b;
    b.attach();
    b.on();
    b.load(4000U); // overcurrent after blanking and debounce
    for (int i = 0; i < 20; ++i)
    {
        b.cycle();
    }
    b.halt();
    b.on(); // refused (LATCHED): no release
    CHECK(!b.gen.adapter.enabledSinceDebugHaltSwitchOff());
    b.load(0U);
    b.reset();
    CHECK(!b.gen.adapter.enabledSinceDebugHaltSwitchOff());
    b.on();
    CHECK(b.gen.adapter.enabledSinceDebugHaltSwitchOff());
}

void safeStateLockHoldsInLow()
{
    // SYS-36: while S1 is latched IN is never written high, "on" is refused, holdForSafeState reports held
    Bench b;
    b.attach();
    b.on();
    s1 = true;
    CHECK(b.hold());
    CHECK(!b.gen.pins.inPin);
    uint32_t const inHigh = b.gen.pins.inHigh;
    for (int i = 0; i < 100; ++i)
    {
        b.on();
        b.cycle();
    }
    CHECK(b.gen.pins.inHigh == inHigh);
    s1 = false;
    CHECK(!b.hold());
    b.cycle();
    CHECK(!b.gen.pins.inPin); // stays off until the next "on"
    b.on();
    CHECK(b.gen.pins.inPin);
}

/** 200 sequences of 400 random events (fixed seed: reproducible). */
void randomSequences()
{
    uint32_t seed = 0x2A660930U;
    auto next     = [&seed]() {
        seed ^= seed << 13U;
        seed ^= seed >> 17U;
        seed ^= seed << 5U;
        return seed;
    };
    uint32_t const loads[] = {0U, 50U, 1500U, 4000U, 7000U};
    for (int run = 0; run < 200; ++run)
    {
        Bench b;
        bool attached = false;
        for (int i = 0; i < 400; ++i)
        {
            switch (next() % 12U)
            {
                case 0U:
                case 1U:
                case 2U: b.cycle(); break;
                case 3U: b.on(); break;
                case 4U: b.off(); break;
                case 5U: b.reset(); break;
                case 6U: b.load(loads[next() % 5U]); break;
                case 7U: s1 = !s1; break;
                case 8U: b.halt(); break;
                case 9U: (void)b.hold(); break;
                case 10U:
                    if (!attached && ((next() % 4U) == 0U))
                    {
                        b.attach(); // the system exists from some point on, as after platformLifecycleAdd
                        attached = true;
                    }
                    break;
                default:
                    b.ref.pins.adcOk = b.gen.pins.adcOk = ((next() % 8U) != 0U);
                    break;
            }
        }
        b.compare("end of sequence");
    }
}

} // namespace

int main()
{
    struct Test
    {
        char const* name;
        void (*fn)();
    };

    Test const tests[] = {
        {"notAttached", notAttached},
        {"haltSwitchesOffAndWaitsForOn", haltSwitchesOffAndWaitsForOn},
        {"latchedFaultNeedsReset", latchedFaultNeedsReset},
        {"safeStateLockHoldsInLow", safeStateLockHoldsInLow},
        {"randomSequences", randomSequences},
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
