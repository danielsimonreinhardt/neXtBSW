#pragma once

#include "bsp/can/canTransceiver/CanPhy.h"

#include <cstdint>

namespace can
{

/**
 * Transmit permission of one CAN bus from the readiness of its transceiver (role can_phy).
 *
 * The CAN system asks the transceiver through ::bios::CanPhy only; whether a SBC, a transceiver with
 * STB pin or a passive part answers is decided by the integration. Hardware independent.
 */
class CanPhyGate
{
public:
    enum class Action : uint8_t
    {
        NONE,
        MUTE,   // transceiver open but phy not ready: stop transmitting, keep receiving
        UNMUTE, // transceiver muted and phy ready again
    };

    /** CAN_PHY_ERROR means not ready; CAN_PHY_ERROR_UNSUPPORTED (no status available) counts as ready. */
    static bool isReady(::bios::CanPhy::ErrorCode const status)
    {
        return status != ::bios::CanPhy::CAN_PHY_ERROR;
    }

    static bool isReady(::bios::CanPhy& phy, uint32_t const busId)
    {
        return isReady(phy.getPhyErrorStatus(busId));
    }

    /**
     * \param open   transceiver state OPEN
     * \param muted  transceiver state MUTED
     * \param ready  phy readiness (isReady)
     */
    static Action decide(bool const open, bool const muted, bool const ready)
    {
        if (open && !ready)
        {
            return Action::MUTE;
        }
        if (muted && ready)
        {
            return Action::UNMUTE;
        }
        return Action::NONE;
    }

    /** Frames may be written only while the transceiver is open (not muted) and the phy is ready. */
    static bool isTransmitAllowed(bool const open, bool const ready) { return open && ready; }
};

} // namespace can
