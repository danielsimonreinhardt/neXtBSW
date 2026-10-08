// SPDX-License-Identifier: Apache-2.0
// Host test for bsp/can/canFdBitTiming.h (COM-06, no MCU headers needed). Profiles: 40 MHz, 16 MHz
// oscillator and 80 MHz bus clock, not only the eval board.
// Build: zig c++ -std=c++17 -Wall -Wextra -Wconversion -Werror
//        -Iplatforms/s32k1xx/port/bspConfiguration/include <this file>

#include "bsp/can/canFdBitTiming.h"

#include <cstdio>

using namespace ::bsp::can;

namespace
{
int failures = 0;
int checks   = 0;

void expect(bool const condition, char const* const what)
{
    ++checks;
    if (!condition)
    {
        ++failures;
        std::printf("FAIL: %s\n", what);
    }
}

// Register macros of S32K148_FLEXCAN.h (external/openbsw), copied to cross-check the field layout.
constexpr uint32_t CBT_BTF = 0x80000000U;
constexpr uint32_t CBT_EPRESDIV(uint32_t x) { return (x << 21U) & 0x7FE00000U; }
constexpr uint32_t CBT_ERJW(uint32_t x) { return (x << 16U) & 0x001F0000U; }
constexpr uint32_t CBT_EPROPSEG(uint32_t x) { return (x << 10U) & 0x0000FC00U; }
constexpr uint32_t CBT_EPSEG1(uint32_t x) { return (x << 5U) & 0x000003E0U; }
constexpr uint32_t CBT_EPSEG2(uint32_t x) { return x & 0x0000001FU; }
constexpr uint32_t FDCBT_FPRESDIV(uint32_t x) { return (x << 20U) & 0x3FF00000U; }
constexpr uint32_t FDCBT_FRJW(uint32_t x) { return (x << 16U) & 0x00070000U; }
constexpr uint32_t FDCBT_FPROPSEG(uint32_t x) { return (x << 10U) & 0x00007C00U; }
constexpr uint32_t FDCBT_FPSEG1(uint32_t x) { return (x << 5U) & 0x000000E0U; }
constexpr uint32_t FDCBT_FPSEG2(uint32_t x) { return x & 0x00000007U; }
constexpr uint32_t FDCTRL_FDRATE = 0x80000000U;
constexpr uint32_t FDCTRL_MBDSR0(uint32_t x) { return (x << 16U) & 0x00030000U; }
constexpr uint32_t FDCTRL_TDCEN = 0x00008000U;
constexpr uint32_t FDCTRL_TDCOFF(uint32_t x) { return (x << 8U) & 0x00001F00U; }

// {clockHz, prescaler, propSeg, phaseSeg1, phaseSeg2, resyncJumpWidth}
// 40 MHz: 500 kbit/s nominal (80 tq, 80 %), 2 Mbit/s data (20 tq, 75 %), same prescaler.
constexpr BitTiming NOM_40M_500K = {40000000U, 1U, 47U, 16U, 16U, 16U};
constexpr BitTiming DAT_40M_2M   = {40000000U, 1U, 9U, 5U, 5U, 5U};
// Generated headers can check themselves at compile time.
static_assert(isValidFd(NOM_40M_500K, DAT_40M_2M), "constexpr use");
static_assert(flexcanFdcbt(DAT_40M_2M) == 0x00042484U, "constexpr FDCBT");
} // namespace

int main()
{
    // --- Profile 40 MHz oscillator
    expect(quantaPerBit(NOM_40M_500K) == 80U, "40M nominal 80 tq");
    expect(bitrate(NOM_40M_500K) == 500000U, "40M nominal 500 kbit/s");
    expect(samplePointBasisPoints(NOM_40M_500K) == 8000U, "40M nominal sample point 80 %");
    expect(!isValid(NOM_40M_500K), "80 tq is beyond classic CTRL1 (8..25 tq)");
    expect(isValidNominalFd(NOM_40M_500K), "40M nominal valid in CBT");
    expect(bitrate(DAT_40M_2M) == 2000000U, "40M data 2 Mbit/s");
    expect(samplePointBasisPoints(DAT_40M_2M) == 7500U, "40M data sample point 75 %");
    expect(isValidDataFd(DAT_40M_2M), "40M data valid");
    expect(isValidFd(NOM_40M_500K, DAT_40M_2M), "40M pair valid");
    expect(
        flexcanCbt(NOM_40M_500K)
            == (CBT_BTF | CBT_EPRESDIV(0U) | CBT_ERJW(15U) | CBT_EPROPSEG(46U) | CBT_EPSEG1(15U)
                | CBT_EPSEG2(15U)),
        "CBT fields");
    expect(flexcanCbt(NOM_40M_500K) == 0x800FB9EFU, "CBT value 0x800FB9EF");
    expect(
        flexcanFdcbt(DAT_40M_2M)
            == (FDCBT_FPRESDIV(0U) | FDCBT_FRJW(4U) | FDCBT_FPROPSEG(9U) | FDCBT_FPSEG1(4U)
                | FDCBT_FPSEG2(4U)),
        "FDCBT fields, FPROPSEG not minus one");
    expect(dataSamplePointClockPeriods(DAT_40M_2M) == 15U, "40M data sample point = 15 clock periods");
    expect(isValidTdcOffset(DAT_40M_2M, 15U), "TDCOFF 15 valid");
    expect(maxCompensatedLoopDelayNs(DAT_40M_2M) == 1450U, "loop delay limit 58 periods = 1450 ns");
    expect(
        flexcanFdctrl(true, 64U, true, 15U)
            == (FDCTRL_FDRATE | FDCTRL_MBDSR0(3U) | FDCTRL_TDCEN | FDCTRL_TDCOFF(15U)),
        "FDCTRL fields");
    expect(flexcanFdctrl(true, 64U, true, 15U) == 0x80038F00U, "FDCTRL value 0x80038F00");

    // Classic eval board CAN0 timing written to CBT instead of CTRL1 keeps its fields.
    BitTiming const can0 = {40000000U, 5U, 7U, 5U, 3U, 2U};
    expect(isValidNominalFd(can0), "classic 16 tq timing also valid in CBT");
    expect(flexcanCbt(can0) == 0x80811882U, "CBT of the classic CAN0 timing");

    // --- Profile 16 MHz oscillator (other quartz)
    BitTiming const nom16 = {16000000U, 1U, 19U, 6U, 6U, 4U};
    BitTiming const dat16 = {16000000U, 1U, 7U, 4U, 4U, 4U};
    expect(bitrate(nom16) == 500000U && samplePointBasisPoints(nom16) == 8125U, "16M nominal");
    expect(bitrate(dat16) == 1000000U && samplePointBasisPoints(dat16) == 7500U, "16M data 1 Mbit/s");
    expect(isValidFd(nom16, dat16), "16M pair valid");
    BitTiming const dat16fast = {16000000U, 1U, 3U, 2U, 2U, 2U};
    expect(bitrate(dat16fast) == 2000000U, "16M 2 Mbit/s = 8 tq");
    expect(isValidFd(nom16, dat16fast), "16M 2 Mbit/s valid");
    expect(dataSamplePointClockPeriods(dat16fast) == 6U, "16M TDCOFF 6");
    expect(maxCompensatedLoopDelayNs(dat16fast) == 1375U, "16M loop delay limit 22 periods");

    // --- Profile 80 MHz bus clock (CLKSRC = 1): prescaler 2 in both phases
    BitTiming const nom80 = {80000000U, 2U, 47U, 16U, 16U, 16U};
    BitTiming const dat80 = {80000000U, 2U, 3U, 2U, 2U, 2U};
    expect(bitrate(dat80) == 5000000U, "80M data 5 Mbit/s");
    expect(isValidFd(nom80, dat80), "80M pair valid");
    expect(!isValidNominalFd(BitTiming{80000000U, 1U, 64U, 30U, 32U, 16U}), "127 tq rounding error rejected");
    expect(isValidNominalFd(BitTiming{64500000U, 1U, 64U, 32U, 32U, 16U}), "129 tq accepted");

    // --- Pair rules
    expect(!isValidFd(NOM_40M_500K, BitTiming{40000000U, 2U, 4U, 3U, 2U, 2U}), "different prescaler rejected");
    expect(!isValidFd(NOM_40M_500K, BitTiming{20000000U, 1U, 4U, 3U, 2U, 2U}), "different clock rejected");
    expect(
        !isValidFd(BitTiming{40000000U, 1U, 15U, 2U, 2U, 2U}, BitTiming{40000000U, 1U, 31U, 4U, 4U, 4U}),
        "data bit rate below nominal rejected");
    expect(
        isValidFd(BitTiming{40000000U, 1U, 15U, 2U, 2U, 2U}, BitTiming{40000000U, 1U, 15U, 2U, 2U, 2U}),
        "data bit rate equal to nominal accepted");

    // --- Data phase limits
    expect(isValidDataFd(BitTiming{40000000U, 1U, 0U, 2U, 2U, 2U}), "propSeg 0 accepted");
    expect(!isValidDataFd(BitTiming{40000000U, 1U, 0U, 1U, 3U, 1U}), "propSeg + phaseSeg1 < 2 rejected");
    expect(!isValidDataFd(BitTiming{40000000U, 1U, 32U, 4U, 3U, 3U}), "FPROPSEG > 31 rejected");
    expect(!isValidDataFd(BitTiming{40000000U, 1U, 4U, 9U, 6U, 2U}), "FPSEG1 > 8 rejected");
    expect(!isValidDataFd(BitTiming{40000000U, 1U, 6U, 4U, 9U, 2U}), "FPSEG2 > 8 rejected");
    expect(!isValidDataFd(BitTiming{40000000U, 1U, 10U, 8U, 1U, 1U}), "FPSEG2 < 2 rejected");
    expect(!isValidDataFd(BitTiming{40000000U, 1U, 1U, 8U, 8U, 9U}), "FRJW 9 rejected");
    expect(!isValidDataFd(BitTiming{40000000U, 1U, 9U, 5U, 5U, 6U}), "FRJW > phase segment rejected");
    expect(!isValidDataFd(BitTiming{40000000U, 0U, 9U, 5U, 5U, 5U}), "prescaler 0 rejected");
    expect(!isValidDataFd(BitTiming{18450000U, 1025U, 1U, 8U, 8U, 5U}), "prescaler 1025 rejected");
    expect(isValidDataFd(BitTiming{12288000U, 1024U, 1U, 8U, 2U, 2U}), "prescaler 1024 accepted");
    expect(!isValidDataFd(BitTiming{40000000U, 1U, 31U, 8U, 8U, 8U}), "48 tq rounding error rejected");
    expect(isValidDataFd(BitTiming{48000000U, 1U, 31U, 8U, 8U, 8U}), "48 tq accepted");
    expect(!isValidDataFd(BitTiming{40000000U, 1U, 9U, 6U, 5U, 5U}), "rounding error (21 tq) rejected");

    // --- Nominal limits in CBT
    expect(!isValidNominalFd(BitTiming{40000000U, 1U, 65U, 8U, 6U, 4U}), "EPROPSEG > 64 rejected");
    expect(!isValidNominalFd(BitTiming{40000000U, 1U, 30U, 33U, 16U, 4U}), "EPSEG1 > 32 rejected");
    expect(!isValidNominalFd(BitTiming{40000000U, 1U, 30U, 16U, 33U, 4U}), "EPSEG2 > 32 rejected");
    expect(!isValidNominalFd(BitTiming{40000000U, 1U, 32U, 16U, 1U, 1U}), "EPSEG2 < 2 rejected");
    expect(!isValidNominalFd(BitTiming{40000000U, 1U, 47U, 16U, 16U, 17U}), "ERJW > phase segment rejected");
    expect(!isValidNominalFd(BitTiming{8200000U, 1025U, 3U, 2U, 2U, 1U}), "EPRESDIV > 1024 rejected");
    expect(isValidNominalFd(BitTiming{8192000U, 1024U, 3U, 2U, 2U, 1U}), "EPRESDIV 1024 accepted");
    expect(!isValidNominalFd(BitTiming{40000000U, 5U, 1U, 1U, 2U, 1U}), "fewer than 8 tq rejected");

    // --- Transceiver delay compensation
    BitTiming const dat8tq = {40000000U, 1U, 3U, 2U, 2U, 2U};
    expect(!isValidTdcOffset(dat8tq, 0U), "TDCOFF 0 rejected");
    expect(isValidTdcOffset(dat8tq, 7U), "TDCOFF inside the bit accepted");
    expect(!isValidTdcOffset(dat8tq, 8U), "TDCOFF of a whole bit rejected");
    expect(!isValidTdcOffset(BitTiming{40000000U, 2U, 9U, 5U, 5U, 5U}, 32U), "TDCOFF > 31 rejected");
    expect(maxCompensatedLoopDelayNs(BitTiming{0U, 1U, 3U, 2U, 2U, 2U}) == 0U, "no clock -> 0");

    // --- FDCTRL and message buffers
    expect(mbdsrCode(8U) == 0U && mbdsrCode(16U) == 1U && mbdsrCode(32U) == 2U && mbdsrCode(64U) == 3U,
           "MBDSR0 codes");
    expect(mbdsrCode(12U) == MBDSR_INVALID, "12 bytes is no message buffer size");
    expect(messageBuffersPerBlock(8U) == 32U && messageBuffersPerBlock(16U) == 21U
               && messageBuffersPerBlock(32U) == 12U && messageBuffersPerBlock(64U) == 7U,
           "RM Table 55-2");
    expect(messageBuffersPerBlock(20U) == 0U, "invalid payload -> 0 buffers");
    expect(flexcanFdctrl(true, 12U, true, 15U) == 0U, "invalid payload -> FDCTRL 0");
    expect(flexcanFdctrl(false, 8U, false, 15U) == 0U, "no BRS, 8 bytes, no TDC, offset ignored");
    expect(flexcanFdctrl(false, 32U, false, 0U) == FDCTRL_MBDSR0(2U), "32 bytes only");

    // --- DLC
    uint32_t const lengths[16] = {0U, 1U, 2U, 3U, 4U, 5U, 6U, 7U, 8U, 12U, 16U, 20U, 24U, 32U, 48U, 64U};
    bool roundTrip             = true;
    for (uint32_t dlc = 0U; dlc < 16U; ++dlc)
    {
        roundTrip = roundTrip && (fdDlcToLength(dlc) == lengths[dlc])
                    && (fdLengthToDlc(lengths[dlc]) == dlc);
    }
    expect(roundTrip, "DLC <-> length for all 16 codes");
    expect(fdDlcToLength(16U) == 0U, "DLC 16 -> 0");
    expect(fdLengthToDlc(9U) == 9U && fdLengthToDlc(13U) == 10U && fdLengthToDlc(49U) == 15U,
           "length pads up to the next DLC");
    expect(fdLengthToDlc(65U) == 0xFFU, "65 bytes -> 0xFF");

    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
