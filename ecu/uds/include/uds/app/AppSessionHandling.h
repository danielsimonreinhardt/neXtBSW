#pragma once

#include "uds/app/IEcuControl.h"

#include <uds/lifecycle/IUdsLifecycleConnector.h>
#include <uds/services/sessioncontrol/DiagnosticSessionControl.h>
#include <uds/services/sessioncontrol/ISessionPersistence.h>
#include <uds/session/DiagSession.h>

namespace uds
{
namespace app
{
/**
 * DiagnosticSessionControl calls writeSession(PROGRAMMING) after the positive response to 10 02
 * has been sent. Instead of persisting the session, the application hands over to the bootloader,
 * which starts in its programming session when it finds the boot request.
 */
class BootloaderSessionPersistence : public ISessionPersistence
{
public:
    explicit BootloaderSessionPersistence(IEcuControl& ecuControl) : _ecuControl(ecuControl) {}

    void readSession(DiagnosticSessionControl& /* sessionControl */) override {}

    void writeSession(DiagnosticSessionControl& sessionControl, uint8_t const session) override
    {
        if (session == static_cast<uint8_t>(DiagSession::PROGRAMMING))
        {
            _ecuControl.enterBootloader();
        }
        else
        {
            sessionControl.sessionWritten(false);
        }
    }

private:
    IEcuControl& _ecuControl;
};

/** ECUReset (11 01, 11 03): reset after the positive response. Power down is not supported. */
class AppUdsLifecycleConnector : public IUdsLifecycleConnector
{
public:
    explicit AppUdsLifecycleConnector(IEcuControl& ecuControl) : _ecuControl(ecuControl) {}

    bool isModeChangePossible() const override { return true; }

    bool requestPowerdown(bool /* rapid */, uint8_t& /* time */) override { return false; }

    bool requestShutdown(ShutdownType const type, uint32_t /* timeout */) override
    {
        if ((type == HARD_RESET) || (type == SOFT_RESET))
        {
            _ecuControl.reset();
            return true;
        }
        return false;
    }

private:
    IEcuControl& _ecuControl;
};

} // namespace app
} // namespace uds
