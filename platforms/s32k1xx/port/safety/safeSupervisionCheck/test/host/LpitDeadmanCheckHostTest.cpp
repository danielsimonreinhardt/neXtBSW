/* SYS-41 (finding F12 of doc/safety/fmea.md, SM-06): host test of the deadman configuration check
 * (safeSupervisionCheck/LpitDeadmanCheck.cpp, the real code) against a register model of the LPIT and the NVIC.
 *
 * What it shows:
 * - the configuration LpitHaltDeadman::init() writes (MCR |= DBG_EN on a running module, channel 1 as 32-bit
 *   periodic counter, TVAL = ticks - 1, MIER bit, NVIC level and enable) passes, with the channel enabled or not
 *   (the retrigger and the interrupt handler switch T_EN legitimately);
 * - every single corrupted field is reported with its own bit: M_CEN, DBG_EN, MIER, TCTRL mode/trigger, TVAL, NVIC
 *   enable, NVIC priority - before SYS-41 none of them was re-read;
 * - a channel number outside the register block is refused without reading the registers;
 * - a project without a deadman: the weak default of deadmanExpectedConfiguration() returns nullptr.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeSupervisionCheck/test/host/stubs \
 *       -Iplatforms/s32k1xx/port/safety/safeSupervisionCheck/include \
 *       platforms/s32k1xx/port/safety/safeSupervisionCheck/test/host/LpitDeadmanCheckHostTest.cpp \
 *       platforms/s32k1xx/port/safety/safeSupervisionCheck/src/safeSupervisionCheck/LpitDeadmanCheck.cpp \
 *       -o build/host/lpitdeadmancheck.exe
 */

#include <safeSupervisionCheck/LpitDeadmanCheck.h>

#include <cstdio>

LpitType lpitRegisters{};
NvicModel nvicModel{};

namespace
{
namespace sv = ::safety::supervision;

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

// An LPIT clock of 8 MHz and 10 ms, the level one below a syscall boundary of 5 - values of a test profile, not of
// the eval board; the check takes them from the expected configuration only.
constexpr uint32_t CHANNEL = 1U;
constexpr uint32_t TICKS   = 80000U;
constexpr uint32_t LEVEL   = 4U;

constexpr sv::LpitDeadmanConfiguration EXPECTED{CHANNEL, TICKS - 1U, LPIT0_Ch1_IRQn, LEVEL};

/** Registers as initSystemTimer() and LpitHaltDeadman::init() leave them. */
void setUpAsInit()
{
    lpitRegisters                = LpitType{};
    nvicModel                    = NvicModel{};
    lpitRegisters.MCR            = LPIT_MCR_M_CEN_MASK;                          // initSystemTimer()
    lpitRegisters.MCR            = lpitRegisters.MCR | LPIT_MCR_DBG_EN_MASK;     // deadman init
    lpitRegisters.TMR[0].TCTRL   = LPIT_TMR_TCTRL_T_EN_MASK;                     // system time, another owner
    lpitRegisters.MIER           = 0x1U;                                         // channel 0 interrupt, not ours
    lpitRegisters.TMR[CHANNEL].TCTRL = 0U;
    lpitRegisters.TMR[CHANNEL].TVAL  = TICKS - 1U;
    lpitRegisters.MIER           = lpitRegisters.MIER | (1U << CHANNEL);
    nvicModel.level[LPIT0_Ch1_IRQn]   = LEVEL;
    nvicModel.enabled[LPIT0_Ch1_IRQn] = true;
}

void testConfigurationOfInitPasses()
{
    setUpAsInit();
    CHECK(sv::checkLpitDeadman(EXPECTED) == 0U); // before the first poll: channel not armed
    lpitRegisters.TMR[CHANNEL].TCTRL = LPIT_TMR_TCTRL_T_EN_MASK; // armed by the retrigger
    lpitRegisters.TMR[CHANNEL].CVAL  = 1234U;
    lpitRegisters.MSR                = 1U << CHANNEL; // expired flag, the handler clears it
    CHECK(sv::checkLpitDeadman(EXPECTED) == 0U);
}

void testEveryCorruptedFieldIsReported()
{
    struct Case
    {
        char const* name;
        void (*corrupt)();
        uint32_t expected;
    };

    Case const cases[] = {
        {"MCR[M_CEN] cleared", [] { lpitRegisters.MCR &= ~LPIT_MCR_M_CEN_MASK; }, sv::DEADMAN_FIELD_MODULE_ENABLE},
        {"MCR[DBG_EN] cleared", [] { lpitRegisters.MCR &= ~LPIT_MCR_DBG_EN_MASK; },
         sv::DEADMAN_FIELD_DEBUG_ENABLE},
        {"MIER bit cleared", [] { lpitRegisters.MIER &= ~(1U << CHANNEL); }, sv::DEADMAN_FIELD_IRQ_ENABLE},
        {"TCTRL mode dual 16 bit", [] { lpitRegisters.TMR[CHANNEL].TCTRL |= 0x4U; },
         sv::DEADMAN_FIELD_TIMER_CONTROL},
        {"TCTRL trigger start", [] { lpitRegisters.TMR[CHANNEL].TCTRL |= LPIT_TMR_TCTRL_TSOT_MASK; },
         sv::DEADMAN_FIELD_TIMER_CONTROL},
        {"TVAL stretched", [] { lpitRegisters.TMR[CHANNEL].TVAL = 0xFFFFFFFFU; }, sv::DEADMAN_FIELD_TIMER_VALUE},
        {"TVAL one bit", [] { lpitRegisters.TMR[CHANNEL].TVAL ^= 0x100U; }, sv::DEADMAN_FIELD_TIMER_VALUE},
        {"NVIC disabled", [] { nvicModel.enabled[LPIT0_Ch1_IRQn] = false; }, sv::DEADMAN_FIELD_NVIC_ENABLE},
        {"NVIC priority", [] { nvicModel.level[LPIT0_Ch1_IRQn] = LEVEL + 1U; }, sv::DEADMAN_FIELD_NVIC_PRIORITY},
    };
    for (auto const& c : cases)
    {
        setUpAsInit();
        c.corrupt();
        uint32_t const fields = sv::checkLpitDeadman(EXPECTED);
        CHECK(fields == c.expected);
        if (fields != c.expected)
        {
            std::printf("  case '%s': fields 0x%x\n", c.name, static_cast<unsigned>(fields));
        }
    }
    // another channel's interrupt or configuration is not the deadman's
    setUpAsInit();
    lpitRegisters.MIER &= ~0x1U;
    lpitRegisters.TMR[0].TVAL = 5U;
    nvicModel.enabled[LPIT0_Ch0_IRQn] = false;
    CHECK(sv::checkLpitDeadman(EXPECTED) == 0U);
}

void testImplausibleChannelIsRefused()
{
    setUpAsInit();
    sv::LpitDeadmanConfiguration const wrong{LPIT_TMR_COUNT, TICKS - 1U, LPIT0_Ch1_IRQn, LEVEL};
    CHECK(sv::checkLpitDeadman(wrong) == sv::DEADMAN_FIELD_CONFIGURATION);
}

void testNoDeadmanByDefault() { CHECK(sv::deadmanExpectedConfiguration() == nullptr); }
} // namespace

int main()
{
    testConfigurationOfInitPasses();
    testEveryCorruptedFieldIsReported();
    testImplausibleChannelIsRefused();
    testNoDeadmanByDefault();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
