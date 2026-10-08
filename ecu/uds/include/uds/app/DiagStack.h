#pragma once

#include "uds/app/ApplicationIdentification.h"
#include "uds/app/BootloaderIdentification.h"
#include "uds/app/DiagDescription.h"
#include "uds/app/DiagDescriptionServices.h"
#include "uds/app/AppProgrammingPreconditions.h"
#include "uds/app/AppDiagConfig.h"
#include "uds/app/AppSessionHandling.h"
#include "uds/app/DiagDidReader.h"
#include "uds/app/FaultMemoryServices.h"
#include "uds/app/IEcuControl.h"

#include <async/Async.h>
#include <docan/addressing/DoCanNormalAddressing.h>
#include <docan/addressing/DoCanNormalAddressingFilter.h>
#include <docan/can/DoCanPhysicalCanTransceiver.h>
#include <docan/common/DoCanParameters.h>
#include <docan/datalink/DoCanDefaultFrameSizeMapper.h>
#include <docan/datalink/DoCanFrameCodec.h>
#include <docan/transmitter/IDoCanTickGenerator.h>
#include <docan/transport/DoCanTransportLayer.h>
#include <docan/transport/DoCanTransportLayerConfig.h>
#include <transport/routing/DiagTransportRouter.h>
#include <uds/DiagDispatcher.h>
#include <uds/base/DiagJobRoot.h>
#include <uds/services/communicationcontrol/CommunicationControl.h>
#include <uds/services/ecureset/ECUReset.h>
#include <uds/services/ecureset/HardReset.h>
#include <uds/services/ecureset/SoftReset.h>
#include <uds/services/sessioncontrol/DiagnosticSessionControl.h>
#include <uds/services/testerpresent/TesterPresent.h>

#include <etl/delegate.h>
#include <etl/pool.h>
#include <etl/queue.h>
#include <etl/span.h>

namespace can
{
class ICanTransceiver;
}

namespace uds
{
namespace app
{
/**
 * UDS server of the application on DoCAN (ISO 15765-2 normal addressing, classic CAN):
 * CAN transceiver -> DoCanTransportLayer -> DiagTransportRouter -> DiagDispatcher -> jobs.
 *
 * Services: 10 01/02/03, 11 01/03, 28, 3E, 85, and from the diagnostic description of the project (COM-28,
 * diag.yaml -> tools/diaggen -> DiagDescription.cpp): 22 and 2E for its data identifiers, 31 for its routines,
 * 27 for its security levels; 19 and 14 over the fault memory of dem::registry (NVM-04, FaultMemoryServices.h).
 * Everything runs in one async context (on the target the CAN task).
 * Independent of the lifecycle, so that the host test drives exactly this wiring.
 */
class DiagStack final
: private ::docan::IDoCanTickGenerator
, private ::async::RunnableType
{
public:
    using AddressingType    = ::docan::DoCanNormalAddressing<>;
    using DataLinkLayerType = AddressingType::DataLinkLayerType;

    static constexpr size_t NUM_INCOMING_CONNECTIONS = 2U;
    static constexpr size_t NUM_SEND_JOBS            = 4U;
    static constexpr uint32_t NUM_DOCAN_RECEIVERS    = 2U;
    static constexpr uint32_t NUM_DOCAN_TRANSMITTERS = 2U;

    /** \p securityAlgorithm: seed and key of the security levels of the description (27); nullptr: no level
     * can be unlocked, requestSeed is answered with NRC 0x22. \p chipUid: the value `chip.uid` of the
     * description (COM-30; the tester derives the device key from it), empty: not known (NRC 0x22).
     * \p dtcGroups: the groups of DTCs 14 clears besides all DTCs and single DTCs (NVM-04, Annex D.1).
     * \p identification: COM-44, hardware number, neXtBSW version and the validity record of the application for
     * the values hardware.number, nextbsw.version (not known: NRC 0x22) and compatibility.*; PLAT-79 the
     * configuration identity for configuration.identity (not known: NRC 0x22). */
    DiagStack(
        ::can::ICanTransceiver& transceiver,
        ::async::ContextType context,
        IEcuControl& ecuControl,
        char const* applicationSoftwareId,
        ISecurityKeyAlgorithm* securityAlgorithm   = nullptr,
        ::etl::span<uint8_t const> chipUid         = {},
        ::etl::span<DtcGroup const> dtcGroups      = {},
        DiagIdentification const& identification = DiagIdentification{nullptr, nullptr, {}, nullptr, nullptr});
    DiagStack(DiagStack const&)            = delete;
    DiagStack& operator=(DiagStack const&) = delete;

    /** Wires and starts the stack. \p bootloaderArea is searched for the F180 string; the record of 0xF1A0 is built
     * from the validity record of the identification (COM-44). */
    void init(::etl::span<uint8_t const> bootloaderArea);

    /** Stops the stack, \p done is called in the stack context when finished. */
    void shutdown(::etl::delegate<void()> done);

    /** Timeout supervision of DoCAN, to be called every 10 ms in the stack context. */
    void cyclicTask();

    DiagSession const& getActiveSession() const;

    ::transport::DiagTransportRouter const& getRouter() const { return _router; }

    /** Bus the DoCAN layer is registered for: the bus id of the transceiver it was built with (ARCH-07). */
    uint8_t getBusId() const { return _docanLayer.getBusId(); }

    ::etl::span<uint8_t const> getBootSoftwareId() const { return _values.bootSoftwareId; }

    /** Storage of `uds.ram_test` of the description (empty if it has none). */
    ::etl::span<uint8_t const> getRamTestData() const { return diagRamTest(); }

    /** Security level unlocked with 27 (host test). */
    DiagAccessState const& getAccessState() const { return _accessState; }

    RoutineControlService const& getRoutineControl() const { return _routineControl; }

    /** NVM-03: the identifiers of the description for the snapshots of the fault memory (registered by init()). */
    DiagDidReader& getDidReader() { return _didReader; }

private:
    using FilterType      = ::docan::DoCanNormalAddressingFilter<DataLinkLayerType>;
    using CodecType       = ::docan::DoCanFrameCodec<DataLinkLayerType>;
    using TransceiverType = ::docan::DoCanPhysicalCanTransceiver<AddressingType>;
    using LayerType       = ::docan::DoCanTransportLayer<DataLinkLayerType>;
    using LayerConfigType = ::docan::declare::DoCanTransportLayerConfig<
        DataLinkLayerType, NUM_DOCAN_RECEIVERS, NUM_DOCAN_TRANSMITTERS, 8U>;

    void tickNeeded() override;
    void execute() override;
    void dispatcherShutdownDone(::transport::AbstractTransportLayer& layer);
    void docanShutdownDone(::transport::AbstractTransportLayer& layer);

    ::async::ContextType const _context;
    ::async::TimeoutType _tickTimeout;
    ::etl::delegate<void()> _shutdownDone;

    ::transport::DiagTransportRouter _router;

    ::docan::DoCanDefaultFrameSizeMapper<DataLinkLayerType::FrameSizeType> _frameSizeMapper;
    CodecType _codec;
    CodecType const* _codecs[1];
    AddressingType _addressing;
    FilterType _filter;
    ::docan::DoCanParameters _parameters;
    LayerConfigType _layerConfig;
    TransceiverType _docanTransceiver;
    LayerType _docanLayer;

    DiagValues _values;
    DiagAccessState _accessState;
    // COM-44: validity record of the application, read at init for the record of 0xF1A0
    ::etl::span<uint8_t const> _applicationRecord;

    AppUdsLifecycleConnector _lifecycleConnector;
    BootloaderSessionPersistence _sessionPersistence;
    DiagJobRoot _jobRoot;
    DiagnosticSessionControl _diagnosticSessionControl;
    DiagDidReader _didReader; // NVM-03, after the session control it reads
    DiagnosisConfiguration _udsConfiguration;
    ::etl::pool<IncomingDiagConnection, NUM_INCOMING_CONNECTIONS> _connectionPool;
    ::etl::queue<TransportJob, NUM_SEND_JOBS> _sendJobQueue;
    DiagDispatcher _udsDispatcher;

    ECUReset _ecuReset;
    HardReset _hardReset;
    SoftReset _softReset;
    TesterPresent _testerPresent;
    // COM-28: the jobs of the diagnostic description
    DidReadService _readDataByIdentifier;
    DidWriteService _writeDataByIdentifier;
    RoutineControlService _routineControl;
    SecurityAccessService _securityAccess;
    // NVM-04: the services of the fault memory (libs/bsw/dem)
    ReadDtcInformationService _readDtcInformation;
    ClearDiagnosticInformationService _clearDiagnosticInformation;
    // BL-21: the preprogramming step of ISO 14229-1 Table 501 (services 0x28 and 0x85); COM-43: with security
    // access in a product build
    PreprogrammingAccess _preprogrammingAccess;
    AppCommunicationControl _communicationControl;
    AppCommunicationState _communicationState;
    AppControlDtcSetting _controlDtcSetting;
    AppSessionReset _sessionReset;
};

} // namespace app
} // namespace uds
