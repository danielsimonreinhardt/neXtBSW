#include "uds/app/DiagStack.h"

#include <busid/BusId.h>
#include <can/canframes/CanId.h>
#include <diag/DiagPreprogramming.h>
#include <docan/common/DoCanLogger.h>
#include <docan/datalink/DoCanFrameCodecConfig.h>
#include <time/TimestampProvider.h>
#include <transport/TransportConfiguration.h>
#include <uds/session/DiagSession.h>

#include <cstring>

// COM-43: the tables of tools/diaggen belong to the build they are compiled into - a development build must not serve
// 28/85 with the security of a product build, and a product build must never serve them without it
#ifdef NX_PRODUCT_BUILD
static_assert(NX_DIAG_PRODUCT_BUILD == 1, "diag/DiagPreprogramming.h generated without --product (NX_PRODUCT_BUILD)");
#else
static_assert(NX_DIAG_PRODUCT_BUILD == 0, "diag/DiagPreprogramming.h generated with --product, NX_PRODUCT_BUILD off");
#endif

namespace uds
{
namespace app
{
namespace
{
// DoCAN timing, block size and separation time of the project (AppDiagConfig.h, generated): the bootloader
// answers with the same values.
uint16_t const ALLOCATE_TIMEOUT_MS      = 1000U;
uint16_t const RX_TIMEOUT_MS            = static_cast<uint16_t>(ISOTP_N_CR_MS);
uint16_t const TX_CALLBACK_TIMEOUT_MS   = 1000U;
uint16_t const FLOW_CONTROL_TIMEOUT_MS  = static_cast<uint16_t>(ISOTP_N_BS_MS);
uint8_t const ALLOCATE_RETRY_COUNT      = 15U;
uint8_t const FLOW_CONTROL_WAIT_COUNT   = 15U;
uint32_t const MIN_SEPARATION_TIME_US   = ISOTP_STMIN_US;
uint8_t const BLOCK_SIZE                = ISOTP_BLOCK_SIZE;
uint32_t const TICK_DELTA_US            = 200U;

using DataLinkLayerType = DiagStack::DataLinkLayerType;
using FilterType        = ::docan::DoCanNormalAddressingFilter<DataLinkLayerType>;

// Padded classic CAN with the filler byte of the project; apart from the filler this is
// DoCanFrameCodecConfigPresets::PADDED_CLASSIC (single, first, consecutive and flow control frames of 8 bytes).
::docan::DoCanFrameCodecConfig<DataLinkLayerType::FrameSizeType> const CODEC_CONFIG
    = {{8U, 8U}, {8U, 8U}, {8U, 8U}, {8U, 8U}, ISOTP_PADDING, 0U};

// Ordered by reception CAN ID. Multi-frame requests to the functional ID are rejected by
// DoCanReceiver because of the invalid transmission address; the response always uses the physical
// entry.
FilterType::AddressEntryType const ADDRESSES[] = {
    {::can::CanId::Base<CAN_ID_REQUEST_FUNC>::value,
     DataLinkLayerType::INVALID_ADDRESS,
     TESTER_ADDRESS,
     ::transport::TransportConfiguration::FUNCTIONAL_ALL_ISO14229,
     0U,
     0U},
    {::can::CanId::Base<CAN_ID_REQUEST_PHYS>::value,
     ::can::CanId::Base<CAN_ID_RESPONSE>::value,
     TESTER_ADDRESS,
     ECU_ADDRESS,
     0U,
     0U}};

uint32_t systemUs() { return ::bsw::time::TimestampProvider::getTimestampUs32Bit(); }

::etl::span<uint8_t const> asBytes(char const* const text)
{
    return ::etl::span<uint8_t const>(
        static_cast<uint8_t const*>(static_cast<void const*>(text)), ::strlen(text));
}
} // namespace

DiagStack::DiagStack(
    ::can::ICanTransceiver& transceiver,
    ::async::ContextType const context,
    IEcuControl& ecuControl,
    char const* const applicationSoftwareId,
    ISecurityKeyAlgorithm* const securityAlgorithm,
    ::etl::span<uint8_t const> const chipUid,
    ::etl::span<DtcGroup const> const dtcGroups,
    DiagIdentification const& identification)
: _context(context)
, _tickTimeout()
, _shutdownDone()
, _router()
, _frameSizeMapper()
, _codec(CODEC_CONFIG, _frameSizeMapper)
, _codecs{&_codec}
, _addressing()
, _filter()
, _parameters(
      ::etl::delegate<uint32_t()>::create<&systemUs>(),
      ALLOCATE_TIMEOUT_MS,
      RX_TIMEOUT_MS,
      TX_CALLBACK_TIMEOUT_MS,
      FLOW_CONTROL_TIMEOUT_MS,
      ALLOCATE_RETRY_COUNT,
      FLOW_CONTROL_WAIT_COUNT,
      MIN_SEPARATION_TIME_US,
      BLOCK_SIZE)
, _layerConfig(_parameters)
, _docanTransceiver(transceiver, _filter, _filter, _addressing)
, _docanLayer(
      // bus of the diagnostic stack: the one DiagSystem picked (uds.bus of the ECU description, ARCH-07)
      transceiver.getBusId(),
      context,
      _filter,
      _docanTransceiver,
      *this,
      _layerConfig,
      ::util::logger::DOCAN)
, _values{
      {},
      asBytes(applicationSoftwareId),
      DiagSession::DEFAULT,
      chipUid,
      // COM-44: texts of the identification, empty (NRC 0x22) when not known
      (identification.hardwareNumber != nullptr) ? asBytes(identification.hardwareNumber)
                                                 : ::etl::span<uint8_t const>(),
      (identification.nextbswVersion != nullptr) ? asBytes(identification.nextbswVersion)
                                                 : ::etl::span<uint8_t const>(),
      compatibilityRecord({}),
      // PLAT-79: configuration identity, empty (NRC 0x22) when not known
      (identification.configurationIdentity != nullptr) ? asBytes(identification.configurationIdentity)
                                                        : ::etl::span<uint8_t const>(),
      // PLAT-93: commit of the customer workspace, empty (NRC 0x22) when not known
      (identification.workspaceVersion != nullptr) ? asBytes(identification.workspaceVersion)
                                                   : ::etl::span<uint8_t const>()}
, _accessState()
, _applicationRecord(identification.applicationRecord)
, _lifecycleConnector(ecuControl)
, _sessionPersistence(ecuControl)
, _jobRoot()
, _diagnosticSessionControl(_lifecycleConnector, context, _sessionPersistence)
, _didReader(diagDescription(), _values, _diagnosticSessionControl)
, _udsConfiguration{
      ECU_ADDRESS,
      ::transport::TransportConfiguration::FUNCTIONAL_ALL_ISO14229,
      ::transport::TransportConfiguration::DIAG_PAYLOAD_SIZE,
      ::busid::SELFDIAG,
      true, /* activate outgoing pending */
      true, /* accept all requests: DoCAN only accepts the tester's CAN IDs anyway */
      true, /* copy functional requests */
      context}
, _connectionPool()
, _sendJobQueue()
, _udsDispatcher(
      _connectionPool, _sendJobQueue, _udsConfiguration, _diagnosticSessionControl, _jobRoot)
, _ecuReset()
, _hardReset(_lifecycleConnector, _udsDispatcher)
, _softReset(_lifecycleConnector, _udsDispatcher)
, _testerPresent()
, _readDataByIdentifier(diagDescription(), _accessState, _values)
, _writeDataByIdentifier(diagDescription(), _accessState)
, _routineControl(diagDescription(), _accessState)
, _securityAccess(diagDescription(), _accessState, securityAlgorithm)
, _readDtcInformation()
, _clearDiagnosticInformation(dtcGroups)
, _preprogrammingAccess(_accessState, DIAG_PREPROGRAMMING_SECURITY)
, _communicationControl(_preprogrammingAccess)
, _communicationState(ecuControl)
, _controlDtcSetting(ecuControl, _preprogrammingAccess)
, _sessionReset(_communicationControl, _controlDtcSetting)
{
    // a new stack starts with a cleared RAM test value, as the value did when it was a member of the stack
    ::etl::span<uint8_t> const ramTest = diagRamTest();
    if (!ramTest.empty())
    {
        (void)::memset(ramTest.data(), 0, ramTest.size());
    }
}

void DiagStack::init(::etl::span<uint8_t const> const bootloaderArea)
{
    _values.bootSoftwareId = findBootloaderVersion(bootloaderArea);
    // COM-44: the record of 0xF1A0, as the bootloader answers it, with the state of this application's validity record
    _values.compatibility = compatibilityRecord(_applicationRecord);

    // UDS server (as the OpenBSW reference UdsSystem)
    (void)_udsDispatcher.init();
    AbstractDiagJob::setDefaultDiagSessionManager(_diagnosticSessionControl);
    _diagnosticSessionControl.setDiagDispatcher(&_udsDispatcher);

    (void)_jobRoot.addAbstractDiagJob(_diagnosticSessionControl);
    (void)_jobRoot.addAbstractDiagJob(_ecuReset);
    (void)_jobRoot.addAbstractDiagJob(_hardReset);
    (void)_jobRoot.addAbstractDiagJob(_softReset);
    // COM-28: data identifiers, routines and security levels of the diagnostic description
    (void)_jobRoot.addAbstractDiagJob(_readDataByIdentifier);
    (void)_jobRoot.addAbstractDiagJob(_writeDataByIdentifier);
    (void)_jobRoot.addAbstractDiagJob(_routineControl);
    (void)_jobRoot.addAbstractDiagJob(_securityAccess);
    // NVM-04: 19 and 14 over the fault memory (it registers itself in dem::registry after the NvM)
    (void)_jobRoot.addAbstractDiagJob(_readDtcInformation);
    (void)_jobRoot.addAbstractDiagJob(_clearDiagnosticInformation);
    (void)_jobRoot.addAbstractDiagJob(_testerPresent);
    // BL-21: preprogramming step of a programming event (ISO 14229-1 Table 501). Both services keep a
    // state that belongs to the non-defaultSession they were set in, so the session listener restores
    // normal operation when the server falls back to the defaultSession.
    (void)_jobRoot.addAbstractDiagJob(_communicationControl);
    (void)_jobRoot.addAbstractDiagJob(_controlDtcSetting);
    _communicationControl.addCommunicationStateListener(_communicationState);
    _diagnosticSessionControl.addDiagSessionListener(_sessionReset);
    // a change of the session locks the security level and ends running routines (ISO 14229-1, 9.2)
    _diagnosticSessionControl.addDiagSessionListener(_securityAccess);
    _diagnosticSessionControl.addDiagSessionListener(_routineControl);

    // routing and DoCAN
    _router.init();
    _router.addTransportLayer(_udsDispatcher);
    _filter.init(::etl::make_span(ADDRESSES), ::etl::make_span(_codecs));
    _router.addTransportLayer(_docanLayer);
    (void)_docanLayer.init();

    // NVM-03: the fault memory stores the identifiers of its snapshots through the read functions of 22
    ::dem::registry.didReader = &_didReader;
}

void DiagStack::shutdown(::etl::delegate<void()> const done)
{
    _shutdownDone = done;
    if (::dem::registry.didReader == &_didReader)
    {
        ::dem::registry.didReader = nullptr; // the fault memory stores 0xFF from now on
    }
    _communicationControl.removeCommunicationStateListener(_communicationState);
    _diagnosticSessionControl.removeDiagSessionListener(_sessionReset);
    _diagnosticSessionControl.removeDiagSessionListener(_securityAccess);
    _diagnosticSessionControl.removeDiagSessionListener(_routineControl);
    _diagnosticSessionControl.setDiagDispatcher(nullptr);
    _diagnosticSessionControl.shutdown();
    (void)_udsDispatcher.shutdown(::transport::AbstractTransportLayer::ShutdownDelegate::
                                      create<DiagStack, &DiagStack::dispatcherShutdownDone>(*this));
}

void DiagStack::dispatcherShutdownDone(::transport::AbstractTransportLayer& /* layer */)
{
    _router.removeTransportLayer(_udsDispatcher);
    (void)_docanLayer.shutdown(::transport::AbstractTransportLayer::ShutdownDelegate::
                                   create<DiagStack, &DiagStack::docanShutdownDone>(*this));
}

void DiagStack::docanShutdownDone(::transport::AbstractTransportLayer& /* layer */)
{
    _tickTimeout.cancel();
    _router.removeTransportLayer(_docanLayer);
    _router.shutdown();
    if (_shutdownDone.is_valid())
    {
        _shutdownDone();
    }
}

void DiagStack::cyclicTask() { _docanLayer.cyclicTask(systemUs()); }

DiagSession const& DiagStack::getActiveSession() const
{
    return _diagnosticSessionControl.getActiveSession();
}

void DiagStack::tickNeeded()
{
    ::async::schedule(_context, *this, _tickTimeout, TICK_DELTA_US, ::async::TimeUnit::MICROSECONDS);
}

void DiagStack::execute()
{
    if (_docanLayer.tick(systemUs()))
    {
        tickNeeded();
    }
}

} // namespace app
} // namespace uds
