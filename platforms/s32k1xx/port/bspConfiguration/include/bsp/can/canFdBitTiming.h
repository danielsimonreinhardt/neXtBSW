// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "bsp/can/canBitTiming.h"

#include <cstdint>

namespace bsp
{
namespace can
{

/**
 * CAN FD register values of a FlexCAN controller with the ISO CAN FD feature (COM-06). Pure constexpr
 * functions like canBitTiming.h: a generated configuration checks itself with static_assert, host
 * tests check the rules. Nothing here knows an instance, a board or a transceiver.
 *
 * Source: S32K1xx Series Reference Manual Rev. 13, chapter 55 FlexCAN
 *  - CBT (55.4.2.17): EPRESDIV 10 bit, ERJW 5 bit, EPROPSEG 6 bit, EPSEG1 5 bit, EPSEG2 5 bit, all
 *    stored minus one; with MCR[FDEN] the nominal timing has to be in CBT (BTF = 1), not in CTRL1.
 *  - FDCBT (55.4.2.34): FPRESDIV 10 bit, FRJW 3 bit, FPROPSEG 5 bit (stored as is, 0..31 tq),
 *    FPSEG1 3 bit, FPSEG2 3 bit (minus one); FPROPSEG + FPSEG1 at least 2 tq.
 *  - Protocol timing (55.5.9.7, Figure 55-8): nominal 8..129 tq, data phase 5..48 tq per bit,
 *    phase segment 2 at least 2 tq (information processing time).
 *  - FDCTRL (55.1.2.4): FDRATE bit 31, MBDSR0 bits 17..16, TDCEN bit 15, TDCOFF bits 12..8 in
 *    protocol engine clock periods; Table 55-2 message buffers per RAM block by payload size.
 *  - Transceiver delay compensation (55.5.9.3): at most 3 data bit times - 2 tq loop delay.
 *
 * The bit timing structure is BitTiming of canBitTiming.h for both phases; only the limits differ.
 */

/// Nominal (arbitration phase) timing in CBT with FD enabled.
constexpr bool isValidNominalFd(BitTiming const& t)
{
    uint32_t const tq = quantaPerBit(t);
    return (t.clockHz != 0U) && (t.prescaler >= 1U) && (t.prescaler <= 1024U) && (t.propSeg >= 1U)
           && (t.propSeg <= 64U) && (t.phaseSeg1 >= 1U) && (t.phaseSeg1 <= 32U)
           && (t.phaseSeg2 >= 2U) && (t.phaseSeg2 <= 32U) && (t.resyncJumpWidth >= 1U)
           && (t.resyncJumpWidth <= 32U) && (t.resyncJumpWidth <= t.phaseSeg1)
           && (t.resyncJumpWidth <= t.phaseSeg2) && (tq >= 8U) && (tq <= 129U)
           && ((t.clockHz % (static_cast<uint32_t>(t.prescaler) * tq)) == 0U);
}

/// Data phase timing in FDCBT (frames with BRS). propSeg may be 0.
constexpr bool isValidDataFd(BitTiming const& t)
{
    uint32_t const tq = quantaPerBit(t);
    return (t.clockHz != 0U) && (t.prescaler >= 1U) && (t.prescaler <= 1024U) && (t.propSeg <= 31U)
           && (t.phaseSeg1 >= 1U) && (t.phaseSeg1 <= 8U) && (t.phaseSeg2 >= 2U)
           && (t.phaseSeg2 <= 8U) && ((static_cast<uint32_t>(t.propSeg) + t.phaseSeg1) >= 2U)
           && (t.resyncJumpWidth >= 1U) && (t.resyncJumpWidth <= 8U)
           && (t.resyncJumpWidth <= t.phaseSeg1) && (t.resyncJumpWidth <= t.phaseSeg2) && (tq >= 5U)
           && (tq <= 48U) && ((t.clockHz % (static_cast<uint32_t>(t.prescaler) * tq)) == 0U);
}

/// Sample point of the data phase in protocol engine clock periods (the recommended TDCOFF).
constexpr uint32_t dataSamplePointClockPeriods(BitTiming const& data)
{
    return static_cast<uint32_t>(data.prescaler)
           * (1U + static_cast<uint32_t>(data.propSeg) + static_cast<uint32_t>(data.phaseSeg1));
}

/// TDCOFF (5 bit) not 0 and inside one data bit.
constexpr bool isValidTdcOffset(BitTiming const& data, uint32_t const tdcOffset)
{
    return (tdcOffset >= 1U) && (tdcOffset <= 31U)
           && (tdcOffset < (static_cast<uint32_t>(data.prescaler) * quantaPerBit(data)));
}

/// Largest transceiver loop delay the delay compensation handles: 3 data bits - 2 tq, in ns (floor).
constexpr uint32_t maxCompensatedLoopDelayNs(BitTiming const& data)
{
    uint64_t const clockPeriods
        = static_cast<uint64_t>(data.prescaler) * ((3U * quantaPerBit(data)) - 2U);
    return (data.clockHz == 0U)
               ? 0U
               : static_cast<uint32_t>((clockPeriods * 1000000000ULL) / data.clockHz);
}

/**
 * Both phases together: each valid, same protocol engine clock, data bit rate not below the nominal
 * one, and the same prescaler (RM FDCBT[FPRESDIV] note: same tq in both phases minimises errors
 * when processing FD frames).
 */
constexpr bool isValidFd(BitTiming const& nominal, BitTiming const& data)
{
    return isValidNominalFd(nominal) && isValidDataFd(data) && (nominal.clockHz == data.clockHz)
           && (nominal.prescaler == data.prescaler) && (bitrate(data) >= bitrate(nominal));
}

/// CBT register value: BTF = 1 and the extended nominal timing fields.
constexpr uint32_t flexcanCbt(BitTiming const& t)
{
    return 0x80000000U | ((static_cast<uint32_t>(t.prescaler) - 1U) << 21U)
           | ((static_cast<uint32_t>(t.resyncJumpWidth) - 1U) << 16U)
           | ((static_cast<uint32_t>(t.propSeg) - 1U) << 10U)
           | ((static_cast<uint32_t>(t.phaseSeg1) - 1U) << 5U)
           | (static_cast<uint32_t>(t.phaseSeg2) - 1U);
}

/// FDCBT register value (FPROPSEG is not stored minus one).
constexpr uint32_t flexcanFdcbt(BitTiming const& t)
{
    return ((static_cast<uint32_t>(t.prescaler) - 1U) << 20U)
           | ((static_cast<uint32_t>(t.resyncJumpWidth) - 1U) << 16U)
           | (static_cast<uint32_t>(t.propSeg) << 10U)
           | ((static_cast<uint32_t>(t.phaseSeg1) - 1U) << 5U)
           | (static_cast<uint32_t>(t.phaseSeg2) - 1U);
}

uint32_t const MBDSR_INVALID = 0xFFFFFFFFU;

/// FDCTRL[MBDSR0] code of a message buffer payload size (8, 16, 32 or 64 bytes), else MBDSR_INVALID.
constexpr uint32_t mbdsrCode(uint32_t const payloadBytes)
{
    return (payloadBytes == 8U)    ? 0U
           : (payloadBytes == 16U) ? 1U
           : (payloadBytes == 32U) ? 2U
           : (payloadBytes == 64U) ? 3U
                                   : MBDSR_INVALID;
}

/// Message buffers in one 32-buffer RAM block for a payload size (RM Table 55-2), 0 if invalid.
constexpr uint32_t messageBuffersPerBlock(uint32_t const payloadBytes)
{
    return (payloadBytes == 8U)    ? 32U
           : (payloadBytes == 16U) ? 21U
           : (payloadBytes == 32U) ? 12U
           : (payloadBytes == 64U) ? 7U
                                   : 0U;
}

/**
 * FDCTRL register value: FDRATE (bit rate switch honoured), MBDSR0, TDCEN and TDCOFF. Returns 0
 * for an invalid payload size; TDCOFF is only written with TDC enabled (TDC must stay off in loop
 * back mode, RM FDCTRL[TDCEN]).
 */
constexpr uint32_t
flexcanFdctrl(bool const bitRateSwitch, uint32_t const payloadBytes, bool const tdc, uint32_t const tdcOffset)
{
    return (mbdsrCode(payloadBytes) == MBDSR_INVALID)
               ? 0U
               : ((bitRateSwitch ? 0x80000000U : 0U) | (mbdsrCode(payloadBytes) << 16U)
                  | (tdc ? (0x8000U | ((tdcOffset & 0x1FU) << 8U)) : 0U));
}

/// Payload length of a CAN FD DLC (0..15), 0 for a DLC above 15.
constexpr uint32_t fdDlcToLength(uint32_t const dlc)
{
    return (dlc <= 8U)    ? dlc
           : (dlc == 9U)  ? 12U
           : (dlc == 10U) ? 16U
           : (dlc == 11U) ? 20U
           : (dlc == 12U) ? 24U
           : (dlc == 13U) ? 32U
           : (dlc == 14U) ? 48U
           : (dlc == 15U) ? 64U
                          : 0U;
}

/// Smallest DLC whose payload holds `length` bytes (padding up), 0xFF above 64 bytes.
constexpr uint32_t fdLengthToDlc(uint32_t const length)
{
    return (length <= 8U)    ? length
           : (length <= 12U) ? 9U
           : (length <= 16U) ? 10U
           : (length <= 20U) ? 11U
           : (length <= 24U) ? 12U
           : (length <= 32U) ? 13U
           : (length <= 48U) ? 14U
           : (length <= 64U) ? 15U
                             : 0xFFU;
}

} // namespace can
} // namespace bsp
