#pragma once

#include "bsp/can/canTransceiver/CanPhy.h"

namespace can
{

/**
 * Role can_phy for a CAN transceiver without software control: mode pins (STB, S, EN) are strapped
 * to normal operation by the board, there is no status to read. setMode() only records the mode,
 * the transceiver always counts as ready.
 */
class PassiveCanPhy final : public ::bios::CanPhy
{
public:
    void init(uint32_t /* id */) override {}

    bool setMode(Mode const mode, uint32_t /* id */) override
    {
        fMode = mode;
        return true;
    }

    ErrorCode getPhyErrorStatus(uint32_t /* id */) override { return CAN_PHY_ERROR_NONE; }
};

} // namespace can
