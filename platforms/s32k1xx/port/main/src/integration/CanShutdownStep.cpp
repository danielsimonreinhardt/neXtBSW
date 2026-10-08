// Run-down step CAN_STOP of the ECU state management (SYS-02), see CanShutdownStep.h.

#include "CanShutdownStep.h"

#include <busid/busIdConfiguration.h> // generated: NX_BUSID_CAN_BUSES(X), NX_BUSID_CAN_COUNT

namespace systems
{

CanShutdownStep::CanShutdownStep(
    ::can::ICanSystem& (*canSystem)(), ::bios::CanPhy* const* phys, uint8_t const phyCount)
: _canSystem(canSystem), _phys(phys), _phyCount(phyCount)
{}

char const* CanShutdownStep::getName() const { return "CAN"; }

CanShutdownStep::Result CanShutdownStep::start(uint32_t const nowMs)
{
    _reason          = "-";
    _bus             = "-";
    _muted           = 0U;
    _drainUntilMs    = nowMs;
    uint16_t drainMs = 0U;
#define NX_CAN_STOP_BUS(bus, busId, index) \
    if (!muteBus(busId, #bus, drainMs))    \
    {                                      \
        return Result::FAILED;             \
    }
    NX_BUSID_CAN_BUSES(NX_CAN_STOP_BUS)
#undef NX_CAN_STOP_BUS
    _drainUntilMs = nowMs + drainMs;
    return poll(nowMs);
}

CanShutdownStep::Result CanShutdownStep::poll(uint32_t const nowMs)
{
    if (static_cast<int32_t>(_drainUntilMs - nowMs) > 0)
    {
        return Result::RUNNING; // frames already in the hardware queue may still leave
    }
    for (uint8_t i = 0U; i < _phyCount; ++i)
    {
        if (_phys[i] != nullptr)
        {
            (void)_phys[i]->setMode(::bios::CanPhy::CAN_PHY_MODE_STANDBY, i);
        }
    }
    return Result::DONE;
}

char const* CanShutdownStep::getFailureReason() const { return _reason; }

bool CanShutdownStep::muteBus(uint8_t const busId, char const* const name, uint16_t& drainMs)
{
    ::can::ICanTransceiver* const transceiver = _canSystem().getCanTransceiver(busId);
    if (transceiver == nullptr)
    {
        // Bus of the configuration without a transceiver object: board integration and generated
        // configuration do not match.
        _reason = "no transceiver object";
        _bus    = name;
        return false;
    }
    if (transceiver->getHwQueueTimeout() > drainMs)
    {
        drainMs = transceiver->getHwQueueTimeout();
    }
    if (transceiver->getState() == ::can::ICanTransceiver::State::OPEN)
    {
        if (transceiver->mute() != ::can::ICanTransceiver::ErrorCode::CAN_ERR_OK)
        {
            _reason = "transceiver refused mute";
            _bus    = name;
            return false;
        }
        ++_muted;
    }
    // Already muted (transceiver not ready) or closed: nothing to stop on this bus.
    return true;
}

} // namespace systems
