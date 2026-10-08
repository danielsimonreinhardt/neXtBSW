// Host test of bsp/io/outputPwm/ComplementaryPwm.h with the configuration bswgen generates for
// boards/evalboard_s32k144/board.yaml (MOT1: FTM0, 20 kHz, 1000 ns, active high). FTM0 is a RAM model with
// the real register layout (S32K148_FTM.h), OpenBSW ftm/Ftm.h is used unchanged; Io and OutputPwm are stubs.
// Expected register values are calculated by hand from the S32K1xx reference manual bit layout:
//   period 80 MHz / 20 kHz = 4000 ticks -> MOD 3999, PS 0
//   dead time 1000 ns = 80 clock ticks -> DTPS /4 (0b10), DTVAL 20 -> DEADTIME 0x80 | 0x14 = 0x94
//   COMBINE per pair: COMBINE 0x01 | COMP 0x02 | DTEN 0x10 | SYNCEN 0x20 = 0x33 -> 0x333333
//   SC: PWMEN0..5 0x3F0000 | CLKS 01 0x08 = 0x3F0008
// Build and run: tools/bswgen/test_bswgen.py (BswGenPwmHostTest).

#include "bsp/io/outputPwm/ComplementaryPwm.h"

#include <cstdio>
#include <vector>

FTM_Type fakeFtm0 = {};

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

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

struct ClientCall
{
    uint16_t output;
    uint16_t clientChannel;
    bios::OutputPwm::IDynamicPwmClient* client;
};

std::vector<PinCall> pinCalls;
std::vector<ClientCall> clientCalls;
std::vector<uint16_t> clearedClients;

// high time of the combined high side output in counter ticks for one period (C0 <= CNT < C1)
uint32_t highTicks(FTM_Type const& ftm, uint8_t ch)
{
    uint32_t n = 0U;
    for (uint32_t cnt = ftm.CNTIN; cnt <= ftm.MOD; ++cnt)
    {
        if ((ftm.CONTROLS[ch].CnV <= cnt) && (cnt < ftm.CONTROLS[ch + 1].CnV))
        {
            ++n;
        }
    }
    return n;
}
} // namespace

namespace bios
{
bsp::BspReturnCode Io::setDefaultConfiguration(uint16_t const io)
{
    pinCalls.push_back(PinCall{io, fakeFtm0});
    return bsp::BSP_OK;
}

bsp::BspReturnCode
OutputPwm::setDynamicClient(uint16_t const out, uint16_t const clientChannel, IDynamicPwmClient* const client)
{
    clientCalls.push_back(ClientCall{out, clientChannel, client});
    return bsp::BSP_OK;
}

bsp::BspReturnCode OutputPwm::clrDynamicClient(uint16_t const out)
{
    clearedClients.push_back(out);
    return bsp::BSP_OK;
}
} // namespace bios

using bios::ComplementaryPwm;

// constexpr duty -> edges, period 4000 ticks
static_assert(ComplementaryPwm::edges(4000U, 0U).c0 == 2000U && ComplementaryPwm::edges(4000U, 0U).c1 == 2000U, "0 %");
static_assert(ComplementaryPwm::edges(4000U, 10000U).c0 == 0U && ComplementaryPwm::edges(4000U, 10000U).c1 == 4000U, "100 %");

int main()
{
    // generated constants against the hand calculation
    CHECK(bios::ftmPwm::PERIOD_TICKS == 4000U);
    CHECK(bios::ftmPwm::PAIR_COUNT == 3U);
    CHECK(bios::OutputPwm::MOT1_U == 1 && bios::OutputPwm::MOT1_W == 3);

    // reset state: registers 0, pins untouched
    fakeFtm0.SC = 0x12345678U; // garbage, init() must end with the configured value
    ComplementaryPwm pwm;
    CHECK(pinCalls.empty());
    pwm.init();

    FTM_Type const& f = fakeFtm0;
    CHECK(f.SC == 0x003F0008U);
    CHECK(f.MOD == 3999U);
    CHECK(f.CNTIN == 0U);
    CHECK((f.MODE & FTM_MODE_FTMEN_MASK) != 0U);
    CHECK(f.COMBINE == 0x00333333U);
    CHECK(f.DEADTIME == 0x94U);
    CHECK(f.POL == 0U);
    CHECK(f.OUTMASK == 0x3FU);
    CHECK(f.SYNC == FTM_SYNC_CNTMAX_MASK);
    CHECK(f.SYNCONF == (FTM_SYNCONF_SYNCMODE_MASK | FTM_SYNCONF_SWWRBUF_MASK));
    CHECK(f.CONF == 0x40U); // BDMMODE 01: outputs to safe value in debug halt
    CHECK(f.PWMLOAD == 0U && f.INVCTRL == 0U && f.SWOCTRL == 0U && f.FLTCTRL == 0U);
    for (uint8_t ch = 0U; ch < 6U; ++ch)
    {
        CHECK(f.CONTROLS[ch].CnSC == FTM_CnSC_ELSB_MASK);
    }
    CHECK(f.CONTROLS[6].CnSC == 0U && f.CONTROLS[7].CnSC == 0U); // PILOT_DUTY input capture untouched
    for (uint8_t ch = 0U; ch < 6U; ch += 2U)
    {
        CHECK(highTicks(f, ch) == 0U); // 0 % until the first duty
    }

    // pins: all six, each once, only after the FTM is running with every output masked
    std::vector<uint16_t> const expectedPins = {bios::Io::MOT1_U_PWM_HS, bios::Io::MOT1_U_PWM_LS,
                                                bios::Io::MOT1_V_PWM_HS, bios::Io::MOT1_V_PWM_LS,
                                                bios::Io::MOT1_W_PWM_HS, bios::Io::MOT1_W_PWM_LS};
    CHECK(pinCalls.size() == expectedPins.size());
    for (size_t i = 0U; (i < pinCalls.size()) && (i < expectedPins.size()); ++i)
    {
        FTM_Type const& s = pinCalls[i].ftm;
        CHECK(pinCalls[i].pin == expectedPins[i]);
        CHECK(s.OUTMASK == 0x3FU);
        CHECK((s.SC & FTM_SC_CLKS_MASK) == FTM_SC_CLKS(1U));
        CHECK(s.COMBINE == 0x00333333U && s.DEADTIME == 0x94U);
        CHECK(s.CONTROLS[0].CnSC == FTM_CnSC_ELSB_MASK);
    }

    // OutputPwm registration: channel MOT1_U..W -> client channel 0..2
    CHECK(clientCalls.size() == 3U);
    for (size_t i = 0U; i < clientCalls.size(); ++i)
    {
        CHECK(clientCalls[i].output == bios::OutputPwm::MOT1_U + i);
        CHECK(clientCalls[i].clientChannel == i);
        CHECK(clientCalls[i].client == &pwm);
    }

    // duty 25 % on V: centered pulse, software sync, only V unmasked
    fakeFtm0.SYNC = FTM_SYNC_CNTMAX_MASK;
    CHECK(pwm.setDuty(1U, 2500U, false) == bsp::BSP_OK);
    CHECK(f.CONTROLS[2].CnV == 1500U && f.CONTROLS[3].CnV == 2500U);
    CHECK(highTicks(f, 2U) == 1000U);
    CHECK((f.SYNC & FTM_SYNC_SWSYNC_MASK) != 0U && (f.SYNC & FTM_SYNC_CNTMAX_MASK) != 0U);
    CHECK(f.OUTMASK == 0x33U);

    // duty table: exact high time and symmetric center
    struct Case
    {
        uint16_t duty;
        uint32_t high;
    };
    Case const cases[] = {{0U, 0U}, {1U, 0U}, {3U, 1U}, {5000U, 2000U}, {7777U, 3110U}, {9999U, 3999U},
                          {10000U, 4000U}, {10001U, 4000U}, {65535U, 4000U}};
    for (Case const& c : cases)
    {
        CHECK(pwm.setDuty(0U, c.duty, false) == bsp::BSP_OK);
        uint32_t const high = highTicks(f, 0U);
        CHECK(high == c.high);
        if ((high > 0U) && (high < 4000U))
        {
            uint32_t const center2 = f.CONTROLS[0].CnV + f.CONTROLS[1].CnV; // 2 x center
            CHECK((center2 >= 3999U) && (center2 <= 4001U));
        }
        if (c.high == 0U)
        {
            CHECK(f.CONTROLS[0].CnV == f.CONTROLS[1].CnV); // RM: C(n)V == C(n+1)V -> 0 %
        }
        if (c.high == 4000U)
        {
            CHECK(f.CONTROLS[0].CnV == 0U && f.CONTROLS[1].CnV == f.MOD + 1U); // C(n+1)V > MOD -> 100 %
        }
    }
    CHECK(f.OUTMASK == 0x30U);

    // dead time: 20 x 4 clock ticks = 80 counter ticks (PS 1) = 1000 ns at 80 MHz, less than half a period
    uint32_t const dtTicks = ((f.DEADTIME & FTM_DEADTIME_DTVAL_MASK) >> FTM_DEADTIME_DTVAL_SHIFT) * 4U;
    CHECK(dtTicks == 80U);
    CHECK(static_cast<uint64_t>(dtTicks) * 1000000000ULL / bios::ftmPwm::CLOCK_HZ == 1000U);
    CHECK(2U * dtTicks < f.MOD + 1U);

    // invalid channel and period
    FTM_Type const before = fakeFtm0;
    CHECK(pwm.setDuty(3U, 5000U, false) == bsp::BSP_NOT_SUPPORTED);
    CHECK(fakeFtm0.OUTMASK == before.OUTMASK && fakeFtm0.CONTROLS[6].CnV == before.CONTROLS[6].CnV);
    CHECK(pwm.setPeriod(0U, 50U) == bsp::BSP_NOT_SUPPORTED);
    CHECK(f.MOD == 3999U);

    // disableOutputs masks everything, the next duty unmasks only its pair
    pwm.disableOutputs();
    CHECK(f.OUTMASK == 0x3FU);
    CHECK((f.SC & FTM_SC_CLKS_MASK) == FTM_SC_CLKS(1U)); // counter keeps running
    CHECK(pwm.setDuty(2U, 100U, false) == bsp::BSP_OK);
    CHECK(f.OUTMASK == 0x0FU);

    // shutdown: masked and unregistered
    pwm.shutdown();
    CHECK(f.OUTMASK == 0x3FU);
    CHECK(clearedClients.size() == 3U);
    CHECK(pinCalls.size() == 6U); // no pin reconfiguration outside init

    std::printf("complementary_pwm_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
