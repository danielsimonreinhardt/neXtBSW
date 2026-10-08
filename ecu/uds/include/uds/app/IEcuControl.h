#pragma once

namespace uds
{
namespace app
{
/**
 * Actions of the diagnostic stack that leave the application. Implemented by DiagSystem on the
 * target and by the host test.
 */
class IEcuControl
{
public:
    /** Sets the boot request and resets into the bootloader (does not return on the target). */
    virtual void enterBootloader() = 0;

    /** Resets the ECU (ECUReset hard/soft reset). */
    virtual void reset() = 0;

    /**
     * CommunicationControl (0x28, BL-21): whether the application still sends its non-diagnostic CAN
     * messages and whether it still evaluates the ones it receives. A client silences every server this
     * way before a programming event (ISO 14229-1 16.3.1.1 step (7)).
     */
    virtual void setApplicationMessages(bool txEnabled, bool rxEnabled) = 0;

    /**
     * ControlDTCSetting (0x85, BL-21): whether the fault memory may keep updating DTC status bits. A
     * client freezes them before a programming event (ISO 14229-1 16.3.1.1 step (5)); the fault memory
     * itself arrives with NVM-03, until then this only carries the state.
     */
    virtual void setDtcUpdatesEnabled(bool enabled) = 0;

protected:
    ~IEcuControl() = default;
};

} // namespace app
} // namespace uds
