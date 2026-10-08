/* SYS-40 (finding F9 of doc/safety/fmea.md, SM-04): host test of the S32K1 time base of the period check
 * (safeSupervisionCheck/LptmrCycleTimer.cpp) against a register model of LPTMR0, its PCC entry and SIM_LPOCLKS
 * (lptmrStubs/mcu/mcu.h).
 *
 * What it shows:
 * - startCycleTimer() follows [RM13] 49.5.1: CSR with the timer disabled first, then PSR and CMR, then the mode, then
 *   TEN in a write that leaves CSR[5:1] unchanged; PSR selects LPO1K_CLK with the prescaler bypassed, CSR is time
 *   counter, free running, no interrupt, no DMA;
 * - readCycleTimer() writes CNR before it reads (latch, [RM13] 49.5.4) and returns 16 bits;
 * - checkCycleTimer() passes on the started configuration, also with TCF set (the flag of every wrap-around), and
 *   reports each single deviating field with its bit: TEN, TMS, TFC, TIE, TDRE, PCS, PBYP, PRESCALE, LPO1KCLKEN;
 * - with PCC_LPTMR0[CGC] cleared it reports the gate and does not touch the LPTMR at all (the access would be a bus
 *   fault on the target).
 *
 * Built-in faults (run by hand for SYS-40): CNR not written before the read, TEN in the same write as the mode,
 * prescaler not bypassed, clock gate not checked first, LPO1K enable not checked - each makes this test red.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeSupervisionCheck/test/host/lptmrStubs \
 *       -Iplatforms/s32k1xx/port/safety/safeSupervisionCheck/include \
 *       platforms/s32k1xx/port/safety/safeSupervisionCheck/test/host/LptmrCycleTimerHostTest.cpp \
 *       platforms/s32k1xx/port/safety/safeSupervisionCheck/src/safeSupervisionCheck/LptmrCycleTimer.cpp \
 *       -o build/host/lptmrcycletimer.exe
 */

#include <mcu/mcu.h>
#include <safeSupervisionCheck/LptmrCycleTimer.h>

#include <cstdint>
#include <cstdio>
#include <string>

namespace lptmr_model
{
std::string log;
uint32_t running = 0U;
} // namespace lptmr_model

Reg& Reg::operator=(uint32_t const v)
{
    char text[32];
    (void)std::snprintf(text, sizeof(text), "w%c=%x ", name, static_cast<unsigned>(v));
    ::lptmr_model::log += text;
    if (name == 'N')
    {
        value = ::lptmr_model::running & 0xFFFFU; // write latches the counter
    }
    else
    {
        value = v;
    }
    return *this;
}

Reg::operator uint32_t() const
{
    ::lptmr_model::log += 'r';
    ::lptmr_model::log += name;
    ::lptmr_model::log += ' ';
    return value;
}

LptmrType lptmrRegisters{{'C', 0U}, {'P', 0U}, {'M', 0U}, {'N', 0U}};
PccType pccRegisters{};
SimType simRegisters{};

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

/** Reset state of a warm reset: the LPTMR still runs with an old configuration (reset only by POR/LVD). */
void freshStarted()
{
    lptmrRegisters.CSR.value            = LPTMR_CSR_TEN_MASK | LPTMR_CSR_TIE_MASK; // old configuration
    lptmrRegisters.PSR.value            = 0x2BU;
    lptmrRegisters.CMR.value            = 0x1234U;
    pccRegisters.PCCn[PCC_LPTMR0_INDEX] = PCC_PCCn_CGC_MASK;
    simRegisters.LPOCLKS                = SIM_LPOCLKS_LPO1KCLKEN_MASK | 0x2U; // reset value
    sv::startCycleTimer();
    ::lptmr_model::log.clear();
}

void testStartSequence()
{
    lptmrRegisters.CSR.value            = LPTMR_CSR_TEN_MASK | LPTMR_CSR_TIE_MASK;
    pccRegisters.PCCn[PCC_LPTMR0_INDEX] = PCC_PCCn_CGC_MASK;
    ::lptmr_model::log.clear();
    sv::startCycleTimer();
    // disabled first, PSR = LPO1K (PCS 1) with PBYP, CMR, mode (TFC), then TEN with the mode unchanged
    CHECK(::lptmr_model::log == "wC=0 wP=5 wM=0 wC=4 wC=5 ");
    if (::lptmr_model::log != "wC=0 wP=5 wM=0 wC=4 wC=5 ")
    {
        std::printf("  log '%s'\n", ::lptmr_model::log.c_str());
    }
}

void testReadLatchesTheCounter()
{
    freshStarted();
    ::lptmr_model::running = 0x1ABCDU; // 17 bits inside: only 16 are the counter
    uint32_t const value   = sv::readCycleTimer();
    CHECK(value == 0xABCDU);
    CHECK(::lptmr_model::log.substr(0, 5) == "wN=0 "); // written before the read
    ::lptmr_model::running = 0x0042U;
    CHECK(sv::readCycleTimer() == 0x42U);
}

void testStartedConfigurationPasses()
{
    freshStarted();
    CHECK(sv::checkCycleTimer() == 0U);
    lptmrRegisters.CSR.value = lptmrRegisters.CSR.value | LPTMR_CSR_TCF_MASK; // flag of a wrap-around
    CHECK(sv::checkCycleTimer() == 0U);
}

void testEachDeviatingFieldIsReported()
{
    struct Case
    {
        char const* what;
        Reg* reg;
        uint32_t flip;
        uint32_t expected;
    };

    Case const cases[] = {
        {"CSR[TEN] cleared", &lptmrRegisters.CSR, LPTMR_CSR_TEN_MASK, sv::CYCLE_TIMER_FIELD_ENABLE},
        {"CSR[TMS] pulse counter", &lptmrRegisters.CSR, LPTMR_CSR_TMS_MASK, sv::CYCLE_TIMER_FIELD_MODE},
        {"CSR[TFC] cleared", &lptmrRegisters.CSR, LPTMR_CSR_TFC_MASK, sv::CYCLE_TIMER_FIELD_MODE},
        {"CSR[TIE] set", &lptmrRegisters.CSR, LPTMR_CSR_TIE_MASK, sv::CYCLE_TIMER_FIELD_MODE},
        {"CSR[TDRE] set", &lptmrRegisters.CSR, LPTMR_CSR_TDRE_MASK, sv::CYCLE_TIMER_FIELD_MODE},
        {"PSR[PCS] other clock", &lptmrRegisters.PSR, 0x3U, sv::CYCLE_TIMER_FIELD_PRESCALER},
        {"PSR[PBYP] cleared", &lptmrRegisters.PSR, LPTMR_PSR_PBYP_MASK, sv::CYCLE_TIMER_FIELD_PRESCALER},
        {"PSR[PRESCALE] changed", &lptmrRegisters.PSR, 0x08U, sv::CYCLE_TIMER_FIELD_PRESCALER},
    };
    for (Case const& c : cases)
    {
        freshStarted();
        c.reg->value         = c.reg->value ^ c.flip;
        uint32_t const found = sv::checkCycleTimer();
        CHECK(found == c.expected);
        if (found != c.expected)
        {
            std::printf("  %s: 0x%x\n", c.what, static_cast<unsigned>(found));
        }
    }
    freshStarted();
    simRegisters.LPOCLKS = 0x2U; // LPO1KCLKEN cleared
    CHECK(sv::checkCycleTimer() == sv::CYCLE_TIMER_FIELD_LPO1K);
}

void testClockGateOffMeansNoAccess()
{
    freshStarted();
    pccRegisters.PCCn[PCC_LPTMR0_INDEX] = 0U;
    lptmrRegisters.CSR.value            = 0U; // would also be reported, but must not even be read
    CHECK(sv::checkCycleTimer() == sv::CYCLE_TIMER_FIELD_CLOCK_GATE);
    CHECK(::lptmr_model::log.empty());
}
} // namespace

int main()
{
    testStartSequence();
    testReadLatchesTheCounter();
    testStartedConfigurationPasses();
    testEachDeviatingFieldIsReported();
    testClockGateOffMeansNoAccess();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
