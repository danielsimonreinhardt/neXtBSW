/* SYS-48: host test of the halt deadman of the debug halt guard (LpitHaltDeadman.h, the real code) together with the
 * configuration check of the safety task (safeSupervisionCheck/LpitDeadmanCheck.cpp, SYS-41, the real code) on a
 * register model of the LPIT and the NVIC.
 *
 * What it shows:
 * - EXPECTED_CONFIGURATION, the configuration DebugHaltGuardSystem.h hands to the check on the target
 *   (deadmanExpectedConfiguration()), follows the LPIT clock and the syscall boundary of the project - here a test
 *   profile of 4 MHz and level 3, not the eval board: channel 1, TVAL = 10 ms - 1 tick, vector LPIT0_Ch1, one
 *   level above the boundary;
 * - what init() writes passes the check, and so does every legitimate state after it: armed by the retrigger,
 *   expired and handled by the interrupt (T_EN cleared, flag cleared, safe outputs written), re-armed, stopped;
 * - the check does look: before init() it reports DBG_EN, MIER, TVAL, NVIC enable and priority (why the check must
 *   start only after the init of "haltguard" - it does, "safety" runs after it in the same level), and every
 *   single field corrupted after init() is reported with its own bit.
 * The strong deadmanExpectedConfiguration() itself (under #ifndef UNIT_TEST next to the vector) is checked on the
 * linked image: test_deadman_expected_elf.py.
 *
 * Build and run (zig c++, no hardware; tools/test -k lpit-halt-deadman):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DNX_SAFE_SUPERVISION_CHECK_NO_WEAK_DEFAULT \
 *       -Iplatforms/s32k1xx/port/main/src/integration/test/host/deadmanStubs \
 *       -Iplatforms/s32k1xx/port/main/src/integration -Iplatforms/s32k1xx/hardFaultHandler/include \
 *       -Iplatforms/s32k1xx/port/safety/safeSupervisionCheck/include \
 *       platforms/s32k1xx/port/main/src/integration/test/host/LpitHaltDeadmanHostTest.cpp \
 *       platforms/s32k1xx/port/safety/safeSupervisionCheck/src/safeSupervisionCheck/LpitDeadmanCheck.cpp \
 *       -o build/host/lpithaltdeadman.exe
 */

#include <cstdint>
#include <cstdio>

/* ------------------------------------------------------------------ register model of the safe outputs table */

namespace test_outputs
{
uint32_t pin    = 0xFFFFFFFFU; // a power output, high
uint32_t writes = 0U;

volatile uint32_t* reg(uint32_t const address)
{
    ++writes;
    (void)address;
    return &pin;
}
} // namespace test_outputs

#define NX_SAFE_OUTPUT_REG(address) (::test_outputs::reg(address))

#include "LpitHaltDeadman.h" // the unit under test (with safeSupervisionCheck/LpitDeadmanCheck.h)

extern "C" NxSafeOutput const nxSafeOutputs[] = {{0x400FF000U, 0U, 0U, 0U}, {0U, 0U, 0U, 0U}};

LpitType lpitRegisters;
NvicModel nvicModel{};

namespace
{
namespace sv = ::safety::supervision;
using ::integration::LpitHaltDeadman;

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

constexpr uint32_t CH1 = 1U << 1U;

void setEnable(LpitRegister&, uint32_t const channels)
{
    for (uint32_t ch = 0U; ch < LPIT_TMR_COUNT; ++ch)
    {
        if ((channels & (1U << ch)) != 0U)
        {
            lpitRegisters.TMR[ch].TCTRL.value |= LPIT_TMR_TCTRL_T_EN_MASK;
        }
    }
}

void clearEnable(LpitRegister&, uint32_t const channels)
{
    for (uint32_t ch = 0U; ch < LPIT_TMR_COUNT; ++ch)
    {
        if ((channels & (1U << ch)) != 0U)
        {
            lpitRegisters.TMR[ch].TCTRL.value &= ~LPIT_TMR_TCTRL_T_EN_MASK;
        }
    }
}

void writeOneToClear(LpitRegister& reg, uint32_t const flags) { reg.value &= ~flags; }

/** Reset values, then what initSystemTimer() leaves: module on, channel 0 running with its interrupt. */
void setUpAfterSystemTimer()
{
    lpitRegisters.MCR.value = 0U;
    lpitRegisters.MSR.value = 0U;
    lpitRegisters.MIER.value = 0U;
    for (auto& tmr : lpitRegisters.TMR)
    {
        tmr.TVAL.value  = 0U;
        tmr.CVAL.value  = 0U;
        tmr.TCTRL.value = 0U;
    }
    lpitRegisters.SETTEN.write = &setEnable;
    lpitRegisters.CLRTEN.write = &clearEnable;
    lpitRegisters.MSR.write    = &writeOneToClear;
    nvicModel                  = NvicModel{};

    lpitRegisters.MCR                = LPIT_MCR_M_CEN_MASK;
    lpitRegisters.TMR[0].TVAL        = 0xFFFFFFFFU;
    lpitRegisters.TMR[0].TCTRL.value = LPIT_TMR_TCTRL_T_EN_MASK;
    lpitRegisters.MIER               = 0x1U;
    ::test_outputs::pin              = 0xFFFFFFFFU;
    ::test_outputs::writes           = 0U;
    (void)LpitHaltDeadman::takeExpired();
}

uint32_t check() { return sv::checkLpitDeadman(LpitHaltDeadman::EXPECTED_CONFIGURATION); }

void testExpectedConfigurationFollowsTheProject()
{
    auto const& expected = LpitHaltDeadman::EXPECTED_CONFIGURATION;
    CHECK(expected.channel == 1U);
    CHECK(expected.timerValue == 39999U); // 4 MHz x 10 ms - 1: the period of a down counter is TVAL + 1
    CHECK(expected.irq == LPIT0_Ch1_IRQn);
    CHECK(expected.irqPriority == 2U); // one level above the syscall boundary 3
}

void testInitPassesTheCheck()
{
    setUpAfterSystemTimer();
    LpitHaltDeadman::init();
    CHECK(check() == 0U);
    CHECK((lpitRegisters.MCR & (LPIT_MCR_M_CEN_MASK | LPIT_MCR_DBG_EN_MASK))
          == (LPIT_MCR_M_CEN_MASK | LPIT_MCR_DBG_EN_MASK));
    CHECK(lpitRegisters.MIER == (0x1U | CH1));      // channel 0 of the system time untouched
    CHECK(lpitRegisters.TMR[0].TCTRL == LPIT_TMR_TCTRL_T_EN_MASK);
    CHECK(lpitRegisters.TMR[1].TCTRL == 0U);        // not armed before the first poll
}

void testArmExpireStopPass()
{
    setUpAfterSystemTimer();
    LpitHaltDeadman::init();
    LpitHaltDeadman::retrigger();
    CHECK(lpitRegisters.TMR[1].TCTRL == LPIT_TMR_TCTRL_T_EN_MASK);
    CHECK(check() == 0U);

    lpitRegisters.MSR.value |= CH1; // the deadman expired (a halt or a stall)
    LpitHaltDeadman::onInterrupt();
    CHECK(::test_outputs::pin == 0U); // outputs off
    CHECK(lpitRegisters.TMR[1].TCTRL == 0U);
    CHECK((lpitRegisters.MSR & CH1) == 0U);
    CHECK(LpitHaltDeadman::takeExpired());
    CHECK(check() == 0U); // an expired, stopped channel is no configuration error

    LpitHaltDeadman::retrigger();
    CHECK(check() == 0U);
    LpitHaltDeadman::stop();
    CHECK(check() == 0U);
}

void testInterruptOfAnotherChannelWritesNothing()
{
    setUpAfterSystemTimer();
    LpitHaltDeadman::init();
    LpitHaltDeadman::retrigger();
    lpitRegisters.MSR.value |= 0x1U; // channel 0 (shared vector on derivatives with one LPIT vector)
    LpitHaltDeadman::onInterrupt();
    CHECK(::test_outputs::writes == 0U);
    CHECK(!LpitHaltDeadman::takeExpired());
    CHECK(check() == 0U);
}

void testBeforeInitIsReported()
{
    setUpAfterSystemTimer();
    CHECK(check()
          == (sv::DEADMAN_FIELD_DEBUG_ENABLE | sv::DEADMAN_FIELD_IRQ_ENABLE | sv::DEADMAN_FIELD_TIMER_VALUE
              | sv::DEADMAN_FIELD_NVIC_ENABLE | sv::DEADMAN_FIELD_NVIC_PRIORITY));
}

void testCorruptionAfterInitIsReported()
{
    setUpAfterSystemTimer();
    LpitHaltDeadman::init();
    lpitRegisters.MCR = lpitRegisters.MCR & ~LPIT_MCR_DBG_EN_MASK;
    CHECK(check() == sv::DEADMAN_FIELD_DEBUG_ENABLE);

    setUpAfterSystemTimer();
    LpitHaltDeadman::init();
    lpitRegisters.TMR[1].TVAL = 2U * 39999U; // a stretched time-out
    CHECK(check() == sv::DEADMAN_FIELD_TIMER_VALUE);

    setUpAfterSystemTimer();
    LpitHaltDeadman::init();
    lpitRegisters.MIER = lpitRegisters.MIER & ~CH1;
    CHECK(check() == sv::DEADMAN_FIELD_IRQ_ENABLE);

    setUpAfterSystemTimer();
    LpitHaltDeadman::init();
    lpitRegisters.TMR[1].TCTRL = 0x4U; // dual 16-bit mode
    CHECK(check() == sv::DEADMAN_FIELD_TIMER_CONTROL);

    setUpAfterSystemTimer();
    LpitHaltDeadman::init();
    nvicModel.level[LPIT0_Ch1_IRQn] = 3U; // below the syscall boundary: an RTOS critical section delays it
    CHECK(check() == sv::DEADMAN_FIELD_NVIC_PRIORITY);

    setUpAfterSystemTimer();
    LpitHaltDeadman::init();
    nvicModel.enabled[LPIT0_Ch1_IRQn] = false;
    CHECK(check() == sv::DEADMAN_FIELD_NVIC_ENABLE);

    setUpAfterSystemTimer();
    LpitHaltDeadman::init();
    lpitRegisters.MCR = 0U;
    CHECK(check() == (sv::DEADMAN_FIELD_MODULE_ENABLE | sv::DEADMAN_FIELD_DEBUG_ENABLE));
}
} // namespace

int main()
{
    testExpectedConfigurationFollowsTheProject();
    testInitPassesTheCheck();
    testArmExpireStopPass();
    testInterruptOfAnotherChannelWritesNothing();
    testBeforeInitIsReported();
    testCorruptionAfterInitIsReported();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
