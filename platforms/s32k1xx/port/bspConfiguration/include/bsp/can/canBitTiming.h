#pragma once

#include <cstdint>

namespace bsp
{
namespace can
{

/**
 * Classic CAN bit timing of a FlexCAN controller, all segments in time quanta (not the register
 * values, which are stored minus one). Pure constexpr functions so that the configuration can be
 * checked at compile time and in host tests.
 */
struct BitTiming
{
    uint32_t clockHz;       ///< FlexCAN protocol engine clock (CTRL1.CLKSRC selects it)
    uint16_t prescaler;     ///< 1..256, CTRL1.PRESDIV + 1
    uint8_t propSeg;        ///< 1..8 tq, CTRL1.PROPSEG + 1
    uint8_t phaseSeg1;      ///< 1..8 tq, CTRL1.PSEG1 + 1
    uint8_t phaseSeg2;      ///< 2..8 tq, CTRL1.PSEG2 + 1
    uint8_t resyncJumpWidth; ///< 1..4 tq, CTRL1.RJW + 1
};

/// Divider of an SCG SOSCDIVx/FIRCDIVx field value (0 = output disabled -> 0).
constexpr uint32_t scgDivider(uint32_t const fieldValue)
{
    return ((fieldValue == 0U) || (fieldValue > 7U)) ? 0U : (1UL << (fieldValue - 1U));
}

/// Quanta per bit including the sync segment.
constexpr uint32_t quantaPerBit(BitTiming const& t)
{
    return 1U + static_cast<uint32_t>(t.propSeg) + static_cast<uint32_t>(t.phaseSeg1)
           + static_cast<uint32_t>(t.phaseSeg2);
}

/// Resulting bit rate in bit/s (rounded down), 0 if the timing is degenerate.
constexpr uint32_t bitrate(BitTiming const& t)
{
    uint32_t const divisor = static_cast<uint32_t>(t.prescaler) * quantaPerBit(t);
    return (divisor == 0U) ? 0U : (t.clockHz / divisor);
}

/// Sample point in 1/100 percent (8125 = 81.25 %).
constexpr uint32_t samplePointBasisPoints(BitTiming const& t)
{
    return (10000U * (1U + static_cast<uint32_t>(t.propSeg) + static_cast<uint32_t>(t.phaseSeg1)))
           / quantaPerBit(t);
}

/**
 * Checks the FlexCAN register ranges and the CAN rules: 8..25 tq per bit, phase segment 2 at
 * least the information processing time (2 tq), resync jump width not larger than either phase
 * segment, and a bit rate without rounding error.
 */
constexpr bool isValid(BitTiming const& t)
{
    uint32_t const tq = quantaPerBit(t);
    return (t.clockHz != 0U) && (t.prescaler >= 1U) && (t.prescaler <= 256U) && (t.propSeg >= 1U)
           && (t.propSeg <= 8U) && (t.phaseSeg1 >= 1U) && (t.phaseSeg1 <= 8U)
           && (t.phaseSeg2 >= 2U) && (t.phaseSeg2 <= 8U) && (t.resyncJumpWidth >= 1U)
           && (t.resyncJumpWidth <= 4U) && (t.resyncJumpWidth <= t.phaseSeg1)
           && (t.resyncJumpWidth <= t.phaseSeg2) && (tq >= 8U) && (tq <= 25U)
           && ((t.clockHz % (static_cast<uint32_t>(t.prescaler) * tq)) == 0U);
}

/// FlexCAN CTRL1 timing fields (PRESDIV, RJW, PSEG1, PSEG2, PROPSEG); CLKSRC and all other bits 0.
constexpr uint32_t flexcanCtrl1(BitTiming const& t)
{
    return ((static_cast<uint32_t>(t.prescaler) - 1U) << 24U)
           | ((static_cast<uint32_t>(t.resyncJumpWidth) - 1U) << 22U)
           | ((static_cast<uint32_t>(t.phaseSeg1) - 1U) << 19U)
           | ((static_cast<uint32_t>(t.phaseSeg2) - 1U) << 16U)
           | (static_cast<uint32_t>(t.propSeg) - 1U);
}

} // namespace can
} // namespace bsp
