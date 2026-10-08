#pragma once

#include "bsp/can/canTransceiver/CanPhy.h"
#include "sbc/Uja113x.h"

namespace sbc
{

/**
 * Role can_phy of the UJA113x: the SBC driver owns the transceiver mode (CAN control register), so
 * setMode() only records the requested mode. The error status reflects the transceiver readiness the
 * driver polls in its cycle (CTS set, no VCAN undervoltage, no CAN failure).
 */
class Uja113xCanPhy : public ::bios::CanPhy
{
public:
    explicit Uja113xCanPhy(Uja113x const& sbc) : _sbc(sbc) {}

    void init(uint32_t /* id */) override {}

    bool setMode(Mode const mode, uint32_t /* id */) override
    {
        fMode = mode;
        return true;
    }

    ErrorCode getPhyErrorStatus(uint32_t /* id */) override
    {
        return _sbc.isCanTransceiverReady() ? CAN_PHY_ERROR_NONE : CAN_PHY_ERROR;
    }

private:
    Uja113x const& _sbc;
};

} // namespace sbc
