#pragma once

#include "uds/app/DiagStack.h"
#include "uds/app/IEcuControl.h"

#include <async/Async.h>
#include <etl/optional.h>
#include <lifecycle/AsyncLifecycleComponent.h>

namespace can
{
class ICanSystem;
}

namespace lifecycle
{
class ILifecycleManager;
}

namespace systems
{
/**
 * UDS in the application (DoCAN on the diagnostics CAN bus, tester 0x7E0/0x7E8, functional 0x7DF). 10 02 hands
 * over to the CAN bootloader with the same boot request as the console command "lc bootloader".
 */
class DiagSystem
: public ::lifecycle::AsyncLifecycleComponent
, private ::uds::app::IEcuControl
, private ::async::RunnableType
{
public:
    DiagSystem(
        ::async::ContextType context,
        ::can::ICanSystem& canSystem,
        ::lifecycle::ILifecycleManager& lifecycleManager);
    DiagSystem(DiagSystem const&)            = delete;
    DiagSystem& operator=(DiagSystem const&) = delete;

    void init() override;
    void run() override;
    void shutdown() override;

    /**
     * CommunicationControl (0x28, BL-21): whether the application's non-diagnostic CAN messages are sent
     * and evaluated. The state is kept here for the diagnostic services that report it; acting on it is
     * the job of the communication layer. Since COM-29 the send side really switches: every change is
     * handed to the generated COM through rte::setApplicationTxEnabled(), which stops writing the cyclic
     * application messages (tools/rtegen, RteCan.cpp.j2). The receive side is still only carried.
     */
    static bool applicationMessagesTxEnabled();
    static bool applicationMessagesRxEnabled();

    /** ControlDTCSetting (0x85, BL-21): DTC status bit updating; since NVM-03 handed to the fault memory
     * (dem::registry.memory->setDtcSetting). */
    static bool dtcUpdatesEnabled();

private:
    void enterBootloader() override;
    void reset() override;
    void setApplicationMessages(bool txEnabled, bool rxEnabled) override;
    void setDtcUpdatesEnabled(bool enabled) override;
    void execute() override;
    void shutdownDone();

    ::async::ContextType const _context;
    ::can::ICanSystem& _canSystem;
    ::lifecycle::ILifecycleManager& _lifecycleManager;
    ::async::TimeoutType _cyclicTimeout;
    ::etl::optional< ::uds::app::DiagStack> _stack;
};

} // namespace systems
