#include "uds/app/AppProgrammingPreconditions.h"

#include <uds/connection/IncomingDiagConnection.h>

namespace uds
{
namespace app
{
AppCommunicationState::AppCommunicationState(IEcuControl& ecuControl)
: _ecuControl(ecuControl), _tx(true), _rx(true)
{}

void AppCommunicationState::publish(bool const tx, bool const rx)
{
    if ((tx != _tx) || (rx != _rx))
    {
        _tx = tx;
        _rx = rx;
        _ecuControl.setApplicationMessages(_tx, _rx);
    }
}

void AppCommunicationState::communicationStateChanged(CommunicationState const newState)
{
    switch (newState)
    {
        // controlType 0x00 enableRxAndTx, for normal or for all message classes
        case ENABLE_NORMAL_MESSAGE_TRANSMISSION:
        case ENABLE_ALL_MESSAGE_TRANSMISSION: publish(true, true); break;
        // controlType 0x03 disableRxAndTx
        case DISABLE_NORMAL_MESSAGE_TRANSMISSION:
        case DISABLE_ALL_MESSAGE_TRANSMISSION: publish(false, false); break;
        // controlType 0x01 enableRxAndDisableTx
        case ENABLE_REC_DISABLE_NORMAL_MESSAGE_SEND_TRANSMISSION:
        case ENABLE_REC_DISABLE_ALL_SEND_TRANSMISSION: publish(false, true); break;
        // controlType 0x02 disableRxAndEnableTx
        case DISABLE_REC_ENABLE_NORMAL_MESSAGE_SEND_TRANSMISSION:
        case DISABLE_REC_ENABLE_ALL_SEND_TRANSMISSION: publish(true, false); break;
        // networkManagementCommunicationMessages only: this ECU has none, so nothing changes
        case ENABLE_NN_MESSAGE_TRANSMISSION:
        case DISABLE_NM_MESSAGE_TRANSMISSION:
        case ENABLE_REC_DISABLE_NM_SEND_TRANSMISSION:
        case DISABLE_REC_ENABLE_NM_SEND_TRANSMISSION:
        default: break;
    }
}

AppControlDtcSetting::AppControlDtcSetting(IEcuControl& ecuControl, PreprogrammingAccess const& access)
: ControlDTCSetting(), _ecuControl(ecuControl), _access(access), _enabled(true)
{}

void AppControlDtcSetting::publish(bool const enabled)
{
    if (enabled != _enabled)
    {
        _enabled = enabled;
        _ecuControl.setDtcUpdatesEnabled(_enabled);
    }
}

void AppControlDtcSetting::restoreDefault() { publish(true); }

DiagReturnCode::Type AppControlDtcSetting::process(
    IncomingDiagConnection& connection, uint8_t const* const request, uint16_t const /* requestLength */)
{
    // The base class checked service, session and length; COM-43: security access in a product build, then
    // only the SubFunction is left (9.8.2.2).
    DiagReturnCode::Type const access = _access.check();
    if (access != DiagReturnCode::OK)
    {
        return access;
    }
    uint8_t const dtcSettingType = request[0];
    if ((dtcSettingType != static_cast<uint8_t>(CONTROL_DTC_SETTING_ON))
        && (dtcSettingType != static_cast<uint8_t>(CONTROL_DTC_SETTING_OFF)))
    {
        return DiagReturnCode::ISO_SUBFUNCTION_NOT_SUPPORTED;
    }
    publish(dtcSettingType == static_cast<uint8_t>(CONTROL_DTC_SETTING_ON));
    // 9.8.1: a positive response is due even when the requested state is already active
    PositiveResponse& response = connection.releaseRequestGetResponse();
    (void)response.appendUint8(dtcSettingType);
    (void)connection.sendPositiveResponseInternal(response.getLength(), *this);
    return DiagReturnCode::OK;
}

AppSessionReset::AppSessionReset(
    ::uds::ICommunicationStateManager& communication, AppControlDtcSetting& dtcSetting)
: _communication(communication), _dtcSetting(dtcSetting)
{}

void AppSessionReset::diagSessionChanged(DiagSession const& session)
{
    if (session.getType() == DiagSession::DEFAULT)
    {
        _communication.setCommunicationState(
            ICommunicationStateListener::ENABLE_NORMAL_MESSAGE_TRANSMISSION);
        _dtcSetting.restoreDefault();
    }
}

} // namespace app
} // namespace uds
