// Host test of the PWM generat of a project **without** a pwm group (a board without a motor bridge, ARCH-17).
// It is the counterpart of complementary_pwm_host_test.cpp: nothing of the FTM driver is included here, only
// the generated headers, exactly as the board port sees them (lifecycle/StaticBsp.h):
//
//   #include "bsp/io/outputPwm/ftmPwmConfiguration.h"   // always includable, says how many groups there are
//   #if NX_PWM_GROUP_COUNT > 0
//   #include "bsp/io/outputPwm/ComplementaryPwm.h"      // driver and FTM configuration only with a group
//   #endif
//
// Until ARCH-17 the first include was an `#error` (ARCH-09 B4), which a board without a PWM group could not
// get past: 6 translation units with 19 errors each in the build profile buildtest_s32k148_min.
// Build and run: tools/bswgen/test_bswgen.py (BswGenPwmHostTest).

#include "bsp/io/outputPwm/ftmPwmConfiguration.h"

#include "outputPwm/OutputPwm.h" // stub with the generated channel enum (bsp/io/outputPwm/outputPwmConfiguration.h)

#include <cstdio>
#include <cstring>

#include "bsp/io/outputPwm/outputPwmConfigurationStrings.h" // char const* const outputPwmConfigurationStrings[]

// The generat of a project without a group must report exactly 0 groups (mutation guard: with the eval board
// generat, which has one group, this test must not compile).
#if NX_PWM_GROUP_COUNT != 0
#error "this test needs the generat of a project without a pwm group (NX_PWM_GROUP_COUNT 0)"
#endif

namespace
{
// The instance list of the port: without a group it must expand to nothing, so the element does not exist.
struct Empty
{
    char keep;
};

struct StaticBspLike
{
    char keep;
#define PWM_MEMBER(group) bios::OutputPwm _pwm##group;
    NX_PWM_GROUP_INSTANCES(PWM_MEMBER)
#undef PWM_MEMBER
};

static_assert(sizeof(StaticBspLike) == sizeof(Empty), "NX_PWM_GROUP_INSTANCES must expand to nothing");

// Console "pwm all" walks 0 .. outputPwmNumberAll - 1 and prints OutputPwm::getName(channel) from the
// generated string table (OpenBSW OutputPwmTester). Without a group only the static dummy channel is left,
// so the command reports one channel instead of reading a table that does not exist.
static_assert(bios::OutputPwm::_pwmStaticStart == 0, "the dummy channel stays channel 0");
static_assert(bios::OutputPwm::_pwmStaticEnd == 1, "one static channel (dummyPwm)");
static_assert(bios::OutputPwm::_pwmDynamicStart == bios::OutputPwm::_pwmDynamicMark, "no dynamic channel");
static_assert(bios::OutputPwm::_pwmChannelMax == 1, "no PWM channel besides the dummy");
static_assert(bios::OutputPwm::PORT_UNAVAILABLE == bios::OutputPwm::_pwmChannelMax, "port marker unchanged");
static_assert(sizeof(outputPwmConfigurationStrings) / sizeof(char const*) == 1, "one name for one channel");
} // namespace

int main()
{
    unsigned failures = 0U;
    if (std::strcmp(outputPwmConfigurationStrings[0], "dummyPwm") != 0)
    {
        ++failures;
        std::printf("FAIL: channel 0 is named %s, expected dummyPwm\n", outputPwmConfigurationStrings[0]);
    }
    std::printf(
        "no_pwm_group_host_test: NX_PWM_GROUP_COUNT %d, %u channel(s), %u failures\n",
        NX_PWM_GROUP_COUNT,
        static_cast<unsigned>(bios::OutputPwm::_pwmChannelMax),
        failures);
    return (failures == 0U) ? 0 : 1;
}
