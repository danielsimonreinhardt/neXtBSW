/* SYS-139: host stub of the generated bsp/io/outputPwm/ftmPwmConfiguration.h for ShortHoldHostTest.cpp - one PWM
 * group on FTM0 (channels 0..5, the entry MOT1 of hardFaultHandler/test/host/selectStubs) with the group lock of
 * SYS-123. The output control register of the stub FTM is the register of the test's model at the address of the
 * table entry, so the real FtmPwmInhibit writes exactly where the walks write. */
#pragma once

#include <cstdint>

namespace test_registers
{
volatile uint32_t* reg(uint32_t address);
}

struct FTM_Type
{
    volatile uint32_t& SWOCTRL;
};

#define NX_PWM_GROUP_COUNT 1
#define NX_PWM_GROUP_LOCK  1

namespace bios
{
namespace ftmPwm
{
constexpr uint32_t CHANNEL_MASK = 0x3FU;
constexpr uint32_t FTM0_SWOCTRL = 0x40038094U;

inline FTM_Type& ftm()
{
    static FTM_Type instance{*::test_registers::reg(FTM0_SWOCTRL)};
    return instance;
}

bool groupLocked(); // defined by the test (on the target: the integration, from the safe supervisor's latch)
} // namespace ftmPwm
} // namespace bios
