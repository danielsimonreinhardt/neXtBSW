// SYS-123: host test of the group lock of the PWM driver (bsp/io/outputPwm/ComplementaryPwm.h with NX_PWM_GROUP_LOCK),
// of the debug halt guard's PWM inhibit (FtmPwmInhibit.h) and of the fast path (ftmPwm/FtmPwmGroup.h) - no writer can
// release a locked group. Configuration: tools/bswgen for a copy of the demo project with a degraded mapping
// (safety.output_groups DRIVE = MOT1, safety.degraded DEVICE_FAULT of MOT1_GD), rendered by test_bswgen_group_lock.py;
// FTM0 is a RAM model with the real register layout, OpenBSW ftm/Ftm.h unchanged, Io and OutputPwm are stubs. The
// lock itself (ftmPwm::groupLocked(), on the target the latch of the safe supervisor through
// SafeSupervisor::isEntryDegraded) is a variable of this test.
//
// What it shows (partner-and-degraded.md 4.1 step 1, the rest of fmea.md SM-23 (2) and (3) of SYS-117):
// - a group degraded before the start is forced inactive by init() itself, before the counter runs and before a pin
//   is muxed to the FTM - Ftm::init() writes SWOCTRL 0 from the configuration, the lock writes it again at once;
// - a locked group takes no duty (BSP_ERROR, nothing unmasked) and every duty write forces SWOCTRL again, also after a
//   wild write cleared it;
// - the guard's release keeps a locked group forced and releases an unlocked one;
// - the fast path writes channel values and the software trigger only: SWOCTRL and OUTMASK of a locked group stay,
//   and the read-back for the gate driver (IO-45) sees every phase off;
// - without the lock nothing changes (the demo's driver behaviour, complementary_pwm_host_test.cpp).

#include "bsp/io/outputPwm/ComplementaryPwm.h"
#include "FtmPwmInhibit.h"

#include <cstdio>
#include <vector>

#ifndef NX_PWM_GROUP_LOCK
#error "SYS-123: the generat of a project with a degraded mapping defines NX_PWM_GROUP_LOCK"
#endif

FTM_Type fakeFtm0 = {};

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;
bool locked       = false;
unsigned lockAsks = 0U;

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

struct PinCall
{
    uint16_t pin;
    FTM_Type ftm; // register state when the pin was switched to the FTM
};

std::vector<PinCall> pinCalls;
} // namespace

namespace bios
{
namespace ftmPwm
{
bool groupLocked()
{
    ++lockAsks;
    return locked;
}
} // namespace ftmPwm

bsp::BspReturnCode Io::setDefaultConfiguration(uint16_t const io)
{
    pinCalls.push_back(PinCall{io, fakeFtm0});
    return bsp::BSP_OK;
}

bsp::BspReturnCode OutputPwm::setDynamicClient(uint16_t, uint16_t, IDynamicPwmClient*) { return bsp::BSP_OK; }

bsp::BspReturnCode OutputPwm::clrDynamicClient(uint16_t) { return bsp::BSP_OK; }
} // namespace bios

using bios::ComplementaryPwm;
namespace ftmPwm = bios::ftmPwm;

int main()
{
    FTM_Type const& f = fakeFtm0;

    // --- unlocked: the driver of the demo ----------------------------------------------------------------------
    {
        locked            = false;
        fakeFtm0.SWOCTRL  = 0x1234U; // a value from before; init() configures it
        ComplementaryPwm pwm;
        pwm.init();
        CHECK(f.SWOCTRL == 0U);
        CHECK(pwm.setDuty(0U, 5000U, false) == bsp::BSP_OK);
        CHECK((f.OUTMASK & 0x03U) == 0U);
        CHECK(f.SWOCTRL == 0U);
        ::integration::FtmPwmInhibit inhibit;
        inhibit.inhibit();
        CHECK(f.SWOCTRL == ftmPwm::CHANNEL_MASK);
        inhibit.release();
        CHECK(f.SWOCTRL == 0U);
    }

    // --- locked before the start (after a safety reset with a degraded record) ----------------------------------
    {
        fakeFtm0 = FTM_Type{};
        pinCalls.clear();
        locked   = true;
        lockAsks = 0U;
        ComplementaryPwm pwm;
        pwm.init();
        CHECK(lockAsks > 0U);
        CHECK(f.SWOCTRL == ftmPwm::CHANNEL_MASK);
        CHECK(f.OUTMASK == ftmPwm::CHANNEL_MASK);
        CHECK(pinCalls.size() == 2U * ftmPwm::PAIR_COUNT);
        for (PinCall const& call : pinCalls)
        {
            // no pin reached the FTM while the group was not forced
            CHECK(call.ftm.SWOCTRL == ftmPwm::CHANNEL_MASK);
            CHECK(call.ftm.OUTMASK == ftmPwm::CHANNEL_MASK);
        }
        // every duty writer (pwm.<pair> of the RTE, `pwm set`) through setDuty(): refused, nothing unmasked
        for (uint16_t pair = 0U; pair < ftmPwm::PAIR_COUNT; ++pair)
        {
            CHECK(pwm.setDuty(pair, 5000U, false) == bsp::BSP_ERROR);
        }
        CHECK(f.OUTMASK == ftmPwm::CHANNEL_MASK);
        CHECK(f.SWOCTRL == ftmPwm::CHANNEL_MASK);
        // a wild write (or anything else) clears the software output control: the next duty forces it again
        fakeFtm0.SWOCTRL = 0U;
        CHECK(pwm.setDuty(1U, 2500U, false) == bsp::BSP_ERROR);
        CHECK(f.SWOCTRL == ftmPwm::CHANNEL_MASK);
        CHECK(f.OUTMASK == ftmPwm::CHANNEL_MASK);
        // an invalid pair is still an invalid pair
        CHECK(pwm.setDuty(ftmPwm::PAIR_COUNT, 2500U, false) == bsp::BSP_NOT_SUPPORTED);

        // the debug halt guard: inhibit, then the explicit enable of another output releases - not a locked group
        ::integration::FtmPwmInhibit inhibit;
        inhibit.inhibit();
        inhibit.release();
        CHECK(f.SWOCTRL == ftmPwm::CHANNEL_MASK);
        ::integration::IPwmInhibit& asInterface = inhibit; // as DebugHaltGuard calls it
        asInterface.release();
        CHECK(f.SWOCTRL == ftmPwm::CHANNEL_MASK);

        // the fast path: channel values and the software trigger, never SWOCTRL or OUTMASK
        ftmPwm::FtmPwmGroup group(fakeFtm0);
        uint16_t duties[ftmPwm::PAIR_COUNT];
        for (uint8_t i = 0U; i < ftmPwm::PAIR_COUNT; ++i)
        {
            duties[i] = 16384U;
        }
        (void)group.writeDutiesQ15(duties, ftmPwm::PAIR_COUNT);
        (void)group.writePairQ15(0U, 30000U);
        CHECK(f.SWOCTRL == ftmPwm::CHANNEL_MASK);
        CHECK(f.OUTMASK == ftmPwm::CHANNEL_MASK);
        // IO-45: what the gate driver's source reads - every phase off, so a gate driver of the group sees one state
        for (uint8_t i = 0U; i < ftmPwm::PAIR_COUNT; ++i)
        {
            uint32_t ticks = 99U;
            CHECK(group.highSideOnTicks(i, ticks) && (ticks == 0U));
        }

        // the lock ends only with the power cycle (decision B6 (a)); this model shows the driver follows it
        locked = false;
        CHECK(pwm.setDuty(0U, 5000U, false) == bsp::BSP_OK);
        CHECK((f.OUTMASK & 0x03U) == 0U);
        CHECK(f.SWOCTRL == ftmPwm::CHANNEL_MASK); // the driver never releases SWOCTRL itself
        inhibit.release();
        CHECK(f.SWOCTRL == 0U);
    }

    // --- locked while running: the next duty write forces the group ----------------------------------------------
    {
        fakeFtm0 = FTM_Type{};
        locked   = false;
        ComplementaryPwm pwm;
        pwm.init();
        CHECK(pwm.setDuty(2U, 7000U, false) == bsp::BSP_OK);
        locked = true; // the safety task degrades the group (and walks the table itself)
        CHECK(pwm.setDuty(2U, 7000U, false) == bsp::BSP_ERROR);
        CHECK(f.SWOCTRL == ftmPwm::CHANNEL_MASK);
    }

    std::printf("complementary_pwm_lock_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
