// Host test for bsp/can/canBitTiming.h (no MCU headers needed).
// Build: see doc/changelog.d/COM-01.md (zig c++ -std=c++17 -Werror).

#include "bsp/can/canBitTiming.h"

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

// Register macros of S32K148_FLEXCAN.h, copied to cross-check the field layout.
constexpr uint32_t PRESDIV(uint32_t x) { return (x << 24U) & 0xFF000000U; }
constexpr uint32_t RJW(uint32_t x) { return (x << 22U) & 0x00C00000U; }
constexpr uint32_t PSEG1(uint32_t x) { return (x << 19U) & 0x00380000U; }
constexpr uint32_t PSEG2(uint32_t x) { return (x << 16U) & 0x00070000U; }
constexpr uint32_t PROPSEG(uint32_t x) { return x & 0x00000007U; }
} // namespace

int main()
{
    // Eval board CAN0 = bootloader timing: SOSCDIV2 (40 MHz / 1), PRESDIV 4, PROPSEG 6, PSEG1 4,
    // PSEG2 2, RJW 1.
    uint32_t const _SOSCDIV2 = 1U; // boardClock.h
    BitTiming const can0     = {40000000U / scgDivider(_SOSCDIV2), 5U, 7U, 5U, 3U, 2U};
    expect(scgDivider(0U) == 0U, "SCG divider 0 = disabled");
    expect(scgDivider(1U) == 1U, "SCG divider 1 = /1");
    expect(scgDivider(2U) == 2U, "SCG divider 2 = /2");
    expect(scgDivider(7U) == 64U, "SCG divider 7 = /64");
    expect(quantaPerBit(can0) == 16U, "16 tq");
    expect(bitrate(can0) == 500000U, "500 kbit/s");
    expect(samplePointBasisPoints(can0) == 8125U, "sample point 81.25 %");
    expect(isValid(can0), "CAN0 timing valid");
    expect(
        flexcanCtrl1(can0) == (PRESDIV(4U) | RJW(1U) | PSEG1(4U) | PSEG2(2U) | PROPSEG(6U)),
        "CTRL1 equals BOARD_CAN_CTRL1 of the bootloader");
    expect(flexcanCtrl1(can0) == 0x04620006U, "CTRL1 value 0x04620006");
    expect((flexcanCtrl1(can0) & 0x2000U) == 0U, "CLKSRC = oscillator");

    // Wrong clock assumptions are detected.
    BitTiming const soscDiv2By2 = {40000000U / scgDivider(2U), 5U, 7U, 5U, 3U, 2U};
    expect(bitrate(soscDiv2By2) == 250000U, "SOSCDIV2 = /2 halves the bit rate");
    BitTiming const busClock = {40000000U / 2U * 1U, 5U, 7U, 5U, 3U, 2U};
    expect(bitrate(busClock) != 500000U, "20 MHz clock is not 500 kbit/s with PRESDIV 4");
    BitTiming const fircClock = {48000000U, 5U, 7U, 5U, 3U, 2U};
    expect(bitrate(fircClock) == 600000U, "48 MHz (FIRC) with PRESDIV 4 = 600 kbit/s");
    expect(!isValid(BitTiming{48000000U, 7U, 7U, 5U, 3U, 2U}), "48 MHz / (7 * 16) rounding error rejected");

    // OpenBSW S32K148 EVB value (PRESDIV 0) on this board would be 2.5 Mbit/s.
    BitTiming const s32k148 = {40000000U, 1U, 7U, 5U, 3U, 2U};
    expect(bitrate(s32k148) == 2500000U, "PRESDIV 0 on 40 MHz = 2.5 Mbit/s");

    // Rule checks.
    expect(!isValid(BitTiming{40000000U, 5U, 7U, 5U, 1U, 1U}), "PSEG2 < 2 tq rejected");
    expect(!isValid(BitTiming{40000000U, 5U, 7U, 5U, 3U, 4U}), "RJW > PSEG2 rejected");
    expect(!isValid(BitTiming{40000000U, 5U, 9U, 5U, 3U, 2U}), "PROPSEG > 8 rejected");
    expect(!isValid(BitTiming{40000000U, 0U, 7U, 5U, 3U, 2U}), "prescaler 0 rejected");
    expect(!isValid(BitTiming{40000000U, 5U, 1U, 1U, 2U, 1U}), "fewer than 8 tq rejected");
    expect(bitrate(BitTiming{40000000U, 0U, 0U, 0U, 0U, 0U}) == 0U, "degenerate timing -> 0");
    expect(isValid(BitTiming{40000000U, 10U, 7U, 5U, 3U, 2U}), "250 kbit/s timing valid");
    expect(bitrate(BitTiming{40000000U, 10U, 7U, 5U, 3U, 2U}) == 250000U, "250 kbit/s");

    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
