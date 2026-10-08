#include "systems/DiagSystem.h"

#include <bl_interface.h>
#include <busid/BusId.h>
#include <can/CanFrameLog.h>
#include <diag/DiagBuildId.h>
#include <diag/DiagDtcGroups.h>
#if defined(__has_include)
#if __has_include(<identity/nxIdentityFields.h>)
#include <identity/nxIdentityFields.h> // PLAT-79: NX_IDENTITY_CONFIGURATION (tools/bswgen)
#endif
#endif
#include <lifecycle/ILifecycleManager.h>
#include <uds/udsBusConfiguration.h>
#include <reset/softwareSystemReset.h>
#include <systems/ICanSystem.h>
#include <uds/UdsLogger.h>
#include <uds/app/DeviceKeySecurity.h>

#include <etl/optional.h>
#include <etl/span.h>

#ifdef NX_RTE_APPLICATION_TX_GATE
// COM-29: the communication layer owns the state, the diagnostic stack writes it (Dcm -> Com, not the other
// way round). The setter exists only if the ECU description sends application messages; ecu/uds/CMakeLists.txt
// defines this macro from the generator's own answer (RTE_APPLICATION_TX_GATE, rte_components.cmake).
#include <RteConfig.h>
#endif

#ifdef NX_UDS_BUS_MISSING
#error "UDS is built (PLATFORM_SUPPORT_UDS), but the project has no CAN bus for it (use.can, uds.bus)"
#endif

// ARCH-43: the bus name is part of the start lines below, which are sized for the bus names of the CAN log lines
// (SYS-16: one logger entry of 128 bytes); tools/nxconfig reports a longer name in the project before the build.
static_assert(
    sizeof(NX_UDS_BUS_NAME) - 1U <= ::can::CanFrameLog::BUS_NAME_CHARS,
    "UDS bus name " NX_UDS_BUS_NAME " longer than CanFrameLog::BUS_NAME_CHARS (log lines)");

namespace
{
uint32_t const CYCLE_TIME_MS = 10U;

/* BL-21: what the preprogramming step of a programming event asked this server to switch off
 * (ISO 14229-1 16.3.1.1 steps (5) and (7)). Written in the diagnostic context, read by the COM of the
 * CAN task; a single bool is written and read atomically on every target of this project. */
bool applicationTxEnabled = true;
bool applicationRxEnabled = true;
bool dtcUpdates           = true;

// Version string of the application for DID F181, APP_SOFTWARE_VERSION comes from CMake
char const APPLICATION_SOFTWARE_ID[] = "nextbsw-app " APP_SOFTWARE_VERSION;

// COM-44: neXtBSW version of the tree and commit of this build (diag/DiagBuildId.h, written at every build by
// tools/diaggen/buildid.py) and the hardware number "<board>/<derivative>" of the generated memory layout (BL-10)
char const NEXTBSW_VERSION[] = NX_DIAG_NEXTBSW_VERSION;
// PLAT-93: commit of the customer workspace (same header; "-" without a workspace of its own)
char const WORKSPACE_VERSION[] = NX_DIAG_WORKSPACE_VERSION;
char const HARDWARE_NUMBER[] = BL_HARDWARE_ID;

// PLAT-79: identity of configuration and generators of this image (identity/nxIdentityFields.h, written by
// tools/bswgen at configure time from tools/nxconfig/identity.py) - the same text the console shows with "info"
#ifdef NX_IDENTITY_CONFIGURATION
char const CONFIGURATION_IDENTITY[] = NX_IDENTITY_CONFIGURATION;
#else
char const* const CONFIGURATION_IDENTITY = nullptr;
#endif

// COM-30: chip UID (derivative uid, bl_security.h) in the byte order of the bootloader's DID 0xF18C: 32-bit words in
// address order, each most significant byte first. The tester derives the device key from it.
uint8_t chipUid[BL_UID_LEN];

void readChipUid()
{
    for (uint32_t i = 0U; i < (BL_UID_LEN / 4U); ++i)
    {
        uint32_t const word = *reinterpret_cast<uint32_t const volatile*>(BL_UID_ADDR + (4UL * i));
        chipUid[4U * i]        = static_cast<uint8_t>(word >> 24U);
        chipUid[(4U * i) + 1U] = static_cast<uint8_t>(word >> 16U);
        chipUid[(4U * i) + 2U] = static_cast<uint8_t>(word >> 8U);
        chipUid[(4U * i) + 3U] = static_cast<uint8_t>(word);
    }
}

// COM-30: seed and key of the security levels (method hmac-sha256) with the device key the bootloader's security
// access uses (BL-03), from the key record of the key sector
::etl::optional< ::uds::app::DeviceKeySecurity> deviceKeySecurity;
} // namespace

namespace systems
{
using ::util::logger::Logger;
using ::util::logger::UDS;

DiagSystem::DiagSystem(
    ::async::ContextType const context,
    ::can::ICanSystem& canSystem,
    ::lifecycle::ILifecycleManager& lifecycleManager)
: _context(context)
, _canSystem(canSystem)
, _lifecycleManager(lifecycleManager)
, _cyclicTimeout()
, _stack()
{
    setTransitionContext(context);
}

void DiagSystem::init()
{
    // Diagnostic bus from the ECU description (uds.bus, generated uds/udsBusConfiguration.h)
    ::can::ICanTransceiver* const transceiver = _canSystem.getCanTransceiver(::busid::NX_UDS_BUS);
    if (transceiver != nullptr)
    {
        readChipUid();
        auto& security = deviceKeySecurity.emplace(
            ::etl::span<uint8_t const>(reinterpret_cast<uint8_t const*>(BL_KEY_ADDR), BL_KEY_RECORD_LEN),
            ::etl::span<uint8_t const>(chipUid));
        auto& stack = _stack.emplace(
            *transceiver,
            _context,
            static_cast< ::uds::app::IEcuControl&>(*this),
            APPLICATION_SOFTWARE_ID,
            &security,
            ::etl::span<uint8_t const>(chipUid),
            // NVM-09: groups of DTCs 14 clears (dtc_groups of the description, generated by tools/diaggen)
            ::uds::app::diagDtcGroups(),
            // COM-44: identification (F191, F195 and F1A0 of the standard description); the validity record of
            // this application lies at BL_APP_RECORD_ADDR, written by the bootloader after the download
            ::uds::app::DiagIdentification{
                HARDWARE_NUMBER,
                NEXTBSW_VERSION,
                ::etl::span<uint8_t const>(
                    reinterpret_cast<uint8_t const*>(BL_APP_RECORD_ADDR), ::uds::app::applicationRecordBytes()),
                CONFIGURATION_IDENTITY,
                WORKSPACE_VERSION});
        if (security.keyState() != ::uds::app::DeviceKeySecurity::KeyState::PROVISIONED)
        {
            Logger::warn(
                UDS,
                "no valid device key in the key sector (state %d): security access (27) refused",
                static_cast<int>(security.keyState()));
        }
        // The bootloader occupies the flash below the application (see bl_config.h).
        stack.init(::etl::span<uint8_t const>(
            reinterpret_cast<uint8_t const*>(::uds::app::BOOTLOADER_SCAN_START),
            ::uds::app::BOOTLOADER_SCAN_END - ::uds::app::BOOTLOADER_SCAN_START));
        auto const bootId = stack.getBootSoftwareId();
        // the version string found in flash is NUL terminated (see findBootloaderVersion)
        Logger::info(
            UDS,
            "UDS on " NX_UDS_BUS_NAME " (0x%x/0x%x), bootloader: %s",
            static_cast<unsigned>(::uds::app::CAN_ID_REQUEST_PHYS),
            static_cast<unsigned>(::uds::app::CAN_ID_RESPONSE),
            bootId.empty() ? "not found"
                           : static_cast<char const*>(static_cast<void const*>(bootId.data())));
    }
    else
    {
        Logger::error(UDS, "no " NX_UDS_BUS_NAME " transceiver, UDS disabled");
    }
    transitionDone();
}

void DiagSystem::run()
{
    if (_stack.has_value())
    {
        ::async::scheduleAtFixedRate(
            _context, *this, _cyclicTimeout, CYCLE_TIME_MS, ::async::TimeUnit::MILLISECONDS);
    }
    transitionDone();
}

void DiagSystem::shutdown()
{
    _cyclicTimeout.cancel();
    if (_stack.has_value())
    {
        _stack->shutdown(::etl::delegate<void()>::create<DiagSystem, &DiagSystem::shutdownDone>(*this));
    }
    else
    {
        transitionDone();
    }
}

void DiagSystem::shutdownDone() { transitionDone(); }

void DiagSystem::execute() { _stack->cyclicTask(); }

void DiagSystem::enterBootloader()
{
    // Called after the positive response to 10 02 has been sent. Same mechanism as
    // "lc bootloader": the bootloader stays in its programming session after a software reset.
    bl_set_boot_request();
    softwareSystemReset();
}

void DiagSystem::reset() { _lifecycleManager.transitionToLevel(0U); }

void DiagSystem::setApplicationMessages(bool const txEnabled, bool const rxEnabled)
{
    applicationTxEnabled = txEnabled;
    applicationRxEnabled = rxEnabled;
#ifdef NX_RTE_APPLICATION_TX_GATE
    // COM-29: the generated COM stops sending the cyclic application messages. Called for every change,
    // including the one AppCommunicationState makes when the diagnostic session ends.
    ::rte::setApplicationTxEnabled(txEnabled);
#endif
    Logger::info(
        UDS, "CommunicationControl: application messages tx %s, rx %s",
        txEnabled ? "on" : "off", rxEnabled ? "on" : "off");
}

void DiagSystem::setDtcUpdatesEnabled(bool const enabled)
{
    dtcUpdates = enabled;
    // NVM-03: the fault memory stops every status change except clearing (ISO 14229-1, D.2.1)
    if (::dem::registry.memory != nullptr)
    {
        ::dem::registry.memory->setDtcSetting(enabled);
    }
    Logger::info(UDS, "ControlDTCSetting: DTC status updates %s", enabled ? "on" : "off");
}

bool DiagSystem::applicationMessagesTxEnabled() { return applicationTxEnabled; }

bool DiagSystem::applicationMessagesRxEnabled() { return applicationRxEnabled; }

bool DiagSystem::dtcUpdatesEnabled() { return dtcUpdates; }

} // namespace systems
