/* ARCH-66: host test of the binding trait of the high-side switch (HighSideSwitchBinding.h) - the lines of the module
 * that the generated integration (tools/bswgen, integration/moduleBinding.h) instantiates per instance - against a
 * profile of its own: two synthetic instances (other consoles, labels, parts, levels and pins than any board) as the
 * generator describes them (struct with NAME, PART, COMPONENT, LABEL, Param::, Connect::) and fake family binders.
 *
 * Checked:
 * - config<I>() is field by field the configuration the macro path HIGH_SIDE_SWITCH_CONFIG (HighSideSwitchIntegration.h,
 *   the hand-written integration before ARCH-66) builds from the same generated values - so the two mappings cannot
 *   drift apart; every value of the profile differs from every other, a swapped field fails;
 * - Instance<I>: names as HIGH_SIDE_SWITCH_NAMES (console group, label, help text "<LABEL> high-side switch (<part>)"),
 *   label bound (SYS-16), configuration valid;
 * - Parts<I, B>: IN and DEN on their own output of instance I, IS read from its channel and converted with the channel's
 *   characteristic (B::physical) into mA, a negative value as 0 mA, the unconverted code kept, a failed conversion
 *   reported (no sample), a failed output write reported;
 * - SafeOff_in is the access struct of HighSideSwitchSafeOff.h on the lifecycle system (its behaviour: the lockstep
 *   test of the family integration, SafeOffEquivalenceHostTest.cpp).
 * The OpenBSW headers HighSideSwitchSystem.h names are stubbed (test/host/bindingStubs); no system is created here.
 *
 * tools/test -k high-side-switch-binding
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/highSideSwitch/test/host/bindingStubs -Ilibs/bsw/highSideSwitch/include \
 *       libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitch.cpp \
 *       libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitchChannel.cpp \
 *       libs/bsw/highSideSwitch/test/host/HighSideSwitchBindingHostTest.cpp -o hssbinding.exe
 */

#include "highSideSwitch/HighSideSwitchBinding.h"
#include "highSideSwitch/HighSideSwitchIntegration.h"

#include <cstdio>
#include <cstring>
#include <type_traits>

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

using ::highside::HighSideSwitchBinding;
using ::highside::HighSideSwitchConfig;
using ::highside::HighSideSwitchNames;

/* Fake family binders: outputs and channels are numbers, every call is recorded. */
struct FakeBinders
{
    using DigitalOutput = uint8_t;

    struct AnalogInput
    {
        uint16_t id;
        int32_t offsetMa; // the "characteristic" of the channel: 2 mA per code plus this offset
    };

    static bool outputOk;
    static uint8_t lastOutput;
    static bool lastOn;
    static int writes;
    static bool readOk;
    static uint16_t nextRaw;
    static uint16_t lastChannel;

    static bool set(DigitalOutput const output, bool const on)
    {
        lastOutput = output;
        lastOn     = on;
        ++writes;
        return outputOk;
    }

    static bool read(AnalogInput const& channel, uint16_t& raw)
    {
        lastChannel = channel.id;
        raw         = nextRaw;
        return readOk;
    }

    static int32_t physical(AnalogInput const& channel, uint16_t const raw, uint8_t& quality)
    {
        quality = 3U;
        return (2 * static_cast<int32_t>(raw)) + channel.offsetMa;
    }
};

bool FakeBinders::outputOk       = true;
uint8_t FakeBinders::lastOutput  = 0xFFU;
bool FakeBinders::lastOn         = false;
int FakeBinders::writes          = 0;
bool FakeBinders::readOk         = true;
uint16_t FakeBinders::nextRaw    = 0U;
uint16_t FakeBinders::lastChannel = 0xFFFFU;

} // namespace

/* The generated values of the profile (as integration/highSideSwitchConfiguration.h would define them): every
 * number differs from every other number of the same instance. */
#define NX_DEVICE_LAMP_SW_PART                        "BTS5030-1EJA"
#define NX_DEVICE_LAMP_SW_LABEL                       "LAMP"
#define NX_DEVICE_LAMP_SW_PARAM_CONSOLE               "lamp"
#define NX_DEVICE_LAMP_SW_PARAM_CYCLE_MS              10U
#define NX_DEVICE_LAMP_SW_PARAM_OVERCURRENT_MA        3000U
#define NX_DEVICE_LAMP_SW_PARAM_OVERCURRENT_DEBOUNCE_MS 50U
#define NX_DEVICE_LAMP_SW_PARAM_FAULT_MA              6000U
#define NX_DEVICE_LAMP_SW_PARAM_FAULT_DEBOUNCE_MS     20U
#define NX_DEVICE_LAMP_SW_PARAM_SWITCH_ON_BLANKING_MS 100U
#define NX_DEVICE_LAMP_SW_PARAM_OPEN_LOAD_MA          90U
#define NX_DEVICE_LAMP_SW_PARAM_OFF_MAX_MA            200U
#define NX_DEVICE_LAMP_SW_PARAM_OFF_DEBOUNCE_MS       30U

#define NX_DEVICE_PUMP_SW_PART                        "BTS5030-1EJA-X"
#define NX_DEVICE_PUMP_SW_LABEL                       "PUMP_SUPPLY_1234" // exactly LABEL_MAX_CHARS
#define NX_DEVICE_PUMP_SW_PARAM_CONSOLE               "pump"
#define NX_DEVICE_PUMP_SW_PARAM_CYCLE_MS              5U
#define NX_DEVICE_PUMP_SW_PARAM_OVERCURRENT_MA        1200U
#define NX_DEVICE_PUMP_SW_PARAM_OVERCURRENT_DEBOUNCE_MS 15U
#define NX_DEVICE_PUMP_SW_PARAM_FAULT_MA              2500U
#define NX_DEVICE_PUMP_SW_PARAM_FAULT_DEBOUNCE_MS     25U
#define NX_DEVICE_PUMP_SW_PARAM_SWITCH_ON_BLANKING_MS 40U
#define NX_DEVICE_PUMP_SW_PARAM_OPEN_LOAD_MA          0U
#define NX_DEVICE_PUMP_SW_PARAM_OFF_MAX_MA            150U
#define NX_DEVICE_PUMP_SW_PARAM_OFF_DEBOUNCE_MS       35U

// The instance descriptions as tools/bswgen renders them (modules.py render_binding_header)
#define DESCRIPTION(device, in, den, isId, isOffset)                                                     \
    struct device                                                                                        \
    {                                                                                                    \
        static constexpr char const NAME[]      = #device;                                               \
        static constexpr char const PART[]      = NX_DEVICE_##device##_PART;                             \
        static constexpr char const COMPONENT[] = NX_DEVICE_##device##_PARAM_CONSOLE;                    \
        static constexpr char const LABEL[]     = NX_DEVICE_##device##_LABEL;                            \
        static constexpr bool PWM_BOUND         = false;                                                 \
        struct Param                                                                                     \
        {                                                                                                \
            static constexpr uint32_t CYCLE_MS                = NX_DEVICE_##device##_PARAM_CYCLE_MS;     \
            static constexpr uint32_t OVERCURRENT_MA          = NX_DEVICE_##device##_PARAM_OVERCURRENT_MA; \
            static constexpr uint32_t OVERCURRENT_DEBOUNCE_MS                                            \
                = NX_DEVICE_##device##_PARAM_OVERCURRENT_DEBOUNCE_MS;                                    \
            static constexpr uint32_t FAULT_MA          = NX_DEVICE_##device##_PARAM_FAULT_MA;           \
            static constexpr uint32_t FAULT_DEBOUNCE_MS = NX_DEVICE_##device##_PARAM_FAULT_DEBOUNCE_MS;  \
            static constexpr uint32_t SWITCH_ON_BLANKING_MS                                              \
                = NX_DEVICE_##device##_PARAM_SWITCH_ON_BLANKING_MS;                                      \
            static constexpr uint32_t OPEN_LOAD_MA    = NX_DEVICE_##device##_PARAM_OPEN_LOAD_MA;         \
            static constexpr uint32_t OFF_MAX_MA      = NX_DEVICE_##device##_PARAM_OFF_MAX_MA;           \
            static constexpr uint32_t OFF_DEBOUNCE_MS = NX_DEVICE_##device##_PARAM_OFF_DEBOUNCE_MS;      \
            static constexpr char const CONSOLE[]     = NX_DEVICE_##device##_PARAM_CONSOLE;             \
        };                                                                                               \
        struct Connect                                                                                   \
        {                                                                                                \
            static constexpr uint8_t IN     = in;                                                        \
            static constexpr bool IN_WIRED  = true;                                                      \
            static constexpr uint8_t DEN    = den;                                                       \
            static constexpr bool DEN_WIRED = true;                                                      \
            static constexpr FakeBinders::AnalogInput IS{isId, isOffset};                                \
            static constexpr bool IS_WIRED = true;                                                       \
        };                                                                                               \
    };

// outside the anonymous namespace like the generat (integration::binding), so unused members are no warning
namespace profile
{
DESCRIPTION(LAMP_SW, 3U, 4U, 11U, 0)
DESCRIPTION(PUMP_SW, 9U, 6U, 2U, -500)
} // namespace profile

namespace
{
using ::profile::LAMP_SW;
using ::profile::PUMP_SW;

bool sameConfig(HighSideSwitchConfig const& a, HighSideSwitchConfig const& b)
{
    return (a.cycleMs == b.cycleMs) && (a.overcurrentMa == b.overcurrentMa)
           && (a.overcurrentDebounceMs == b.overcurrentDebounceMs) && (a.faultMa == b.faultMa)
           && (a.faultDebounceMs == b.faultDebounceMs) && (a.switchOnBlankingMs == b.switchOnBlankingMs)
           && (a.openLoadMa == b.openLoadMa) && (a.offMaxMa == b.offMaxMa) && (a.offDebounceMs == b.offDebounceMs);
}

bool sameText(char const* a, char const* b) { return (a != nullptr) && (b != nullptr) && (std::strcmp(a, b) == 0); }

template<class I>
void configurationAndNames(HighSideSwitchConfig const& viaMacro, HighSideSwitchNames const& namesViaMacro)
{
    using Instance = HighSideSwitchBinding::Instance<I>;
    CHECK(sameConfig(HighSideSwitchBinding::config<I>(), viaMacro));
    CHECK(sameConfig(Instance::CONFIG, viaMacro));
    CHECK(HighSideSwitchBinding::valid(Instance::CONFIG));
    CHECK(sameText(Instance::NAMES.command, namesViaMacro.command));
    CHECK(sameText(Instance::NAMES.label, namesViaMacro.label));
    CHECK(sameText(Instance::NAMES.description, namesViaMacro.description));
    CHECK(::highside::hasValidLabel(Instance::NAMES));
}

void configurationFollowsTheMacroPath()
{
    configurationAndNames<LAMP_SW>(HIGH_SIDE_SWITCH_CONFIG(LAMP_SW), HIGH_SIDE_SWITCH_NAMES(LAMP_SW));
    configurationAndNames<PUMP_SW>(HIGH_SIDE_SWITCH_CONFIG(PUMP_SW), HIGH_SIDE_SWITCH_NAMES(PUMP_SW));
    // spelled out once, independent of the macro
    CHECK(sameText(HighSideSwitchBinding::Instance<LAMP_SW>::NAMES.description, "LAMP high-side switch (BTS5030-1EJA)"));
    CHECK(HighSideSwitchBinding::config<PUMP_SW>().cycleMs == 5U);
    CHECK(HighSideSwitchBinding::config<PUMP_SW>().offDebounceMs == 35U);
    CHECK(HighSideSwitchBinding::config<LAMP_SW>().openLoadMa == 90U);
    // a configuration the driver refuses is refused by valid() as well (the generated static_assert uses it)
    HighSideSwitchConfig broken = HighSideSwitchBinding::config<LAMP_SW>();
    broken.faultMa              = broken.overcurrentMa;
    CHECK(!HighSideSwitchBinding::valid(broken));
}

template<class I>
void pinsOfTheInstance(uint8_t const in, uint8_t const den, uint16_t const is, int32_t const offsetMa)
{
    HighSideSwitchBinding::Parts<I, FakeBinders> parts;
    ::highside::IHighSideSwitchIo& io = parts;
    FakeBinders::outputOk             = true;
    CHECK(io.setEnable(true));
    CHECK((FakeBinders::lastOutput == in) && FakeBinders::lastOn);
    CHECK(io.setEnable(false));
    CHECK((FakeBinders::lastOutput == in) && !FakeBinders::lastOn);
    CHECK(io.setDiagEnable(true));
    CHECK((FakeBinders::lastOutput == den) && FakeBinders::lastOn);
    FakeBinders::outputOk = false;
    CHECK(!io.setEnable(true)); // a failed write is reported to the channel
    CHECK(!io.setDiagEnable(false));
    FakeBinders::outputOk = true;

    ::highside::SenseSample sample{0U, 0U};
    FakeBinders::readOk  = true;
    FakeBinders::nextRaw = 1000U;
    CHECK(io.readSense(sample));
    CHECK(FakeBinders::lastChannel == is);
    CHECK(sample.raw == 1000U);
    CHECK(static_cast<int32_t>(sample.loadMa) == ((2 * 1000) + offsetMa));
    FakeBinders::nextRaw = 0U;
    CHECK(io.readSense(sample));
    CHECK(sample.raw == 0U);
    CHECK(sample.loadMa == ((offsetMa > 0) ? static_cast<uint32_t>(offsetMa) : 0U)); // negative: 0 mA
    FakeBinders::readOk = false;
    sample              = {77U, 77U};
    CHECK(!io.readSense(sample)); // no valid sample
    FakeBinders::readOk = true;
}

void partsUseThePinsOfTheirInstance()
{
    pinsOfTheInstance<LAMP_SW>(3U, 4U, 11U, 0);
    pinsOfTheInstance<PUMP_SW>(9U, 6U, 2U, -500);
}

void safeOffIsTheAccessOfTheModule()
{
    static_assert(
        std::is_same<HighSideSwitchBinding::SafeOff_in, ::highside::HighSideSwitchSafeOff<::highside::HighSideSwitchSystem>>::value,
        "SafeOff_in: the access struct of HighSideSwitchSafeOff.h on the lifecycle system");
    static_assert(
        std::is_same<HighSideSwitchBinding::SafeOff_in::System, HighSideSwitchBinding::System>::value,
        "the safe_off access works on the system the trait creates");
    CHECK(true);
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
        {"configurationFollowsTheMacroPath", configurationFollowsTheMacroPath},
        {"partsUseThePinsOfTheirInstance", partsUseThePinsOfTheirInstance},
        {"safeOffIsTheAccessOfTheModule", safeOffIsTheAccessOfTheModule},
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
