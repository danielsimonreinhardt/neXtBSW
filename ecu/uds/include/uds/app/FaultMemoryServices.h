#pragma once

/* NVM-04: the diagnostic services of the fault memory (libs/bsw/dem) - ReadDTCInformation (19) and
 * ClearDiagnosticInformation (14). ControlDTCSetting (85) is AppControlDtcSetting (BL-21), which hands its state
 * to the same fault memory.
 *
 * Both services read and act through dem::IFaultMemory, looked up in dem::registry at every request: the fault
 * memory starts after the NvM, later than the diagnostic stack, and until it is registered 19 answers like a server
 * without DTCs and 14 with conditionsNotCorrect. Nothing here knows a DTC, a record or a board; the tables are the
 * generated ones of the diagnostic description (diag.yaml -> tools/rtegen -> rte::demtables).
 *
 * Scope of 19 for an ECU without OBD and without warning lamp (decision NVM-04, ISO 14229-1 (2026) clause 11.3,
 * only clause numbers cited):
 *   01 reportNumberOfDTCByStatusMask     02 reportDTCByStatusMask           03 reportDTCSnapshotIdentification
 *   04 reportDTCSnapshotRecordByDTCNumber 06 reportDTCExtDataRecordByDTCNumber 0A reportSupportedDTC
 *   14 reportDTCFaultDetectionCounter
 * Every other sub-function is answered with subFunctionNotSupported: 05 (DTCStoredData is not kept), 07/08/09
 * (severity is in the table, reading by severity is not asked for yet), 0B..0E (the order of first/most recent
 * failure is not kept by the fault memory), 15/16/17..19/1A/42/55/56 (permanent DTCs, user defined memories, OBD).
 *
 * Order of the negative responses (7.7, 11.2.4, 11.3.4): length of service and sub-function (0x13), sub-function
 * supported (0x12), exact length of the sub-function (0x13), parameters (0x31: DTC, record, group, memory
 * selection), conditions (0x22, 14 only). A functional request gets no 0x12/0x31 (the dispatcher suppresses them).
 */

#include <dem/IFaultMemory.h>
#include <uds/base/AbstractDiagJob.h>

#include <etl/span.h>

#include <cstdint>

namespace uds
{
namespace app
{
/**
 * A group of DTCs a tester can clear with 14 (ISO 14229-1, Annex D.1): the group identifier and the range of DTC
 * numbers it stands for. Annex D.1 leaves the groups to the vehicle manufacturer (e.g. body, chassis, network)
 * and reserves 0xFFFF00..0xFFFFFE for the functional groups (e.g. 0xFFFF33 emissions, 0xFFFFD0 safety); a project
 * names the groups its ECU belongs to. 0xFFFFFF (all DTCs) and a single DTC number need no entry.
 */
struct DtcGroup
{
    uint32_t group;
    uint32_t first;
    uint32_t last;
};

/** ReadDTCInformation (19) over the fault memory of dem::registry. */
class ReadDtcInformationService final : public AbstractDiagJob
{
public:
    /** DTCFormatIdentifier of 19 01 (Annex D.4): the description uses the ISO 14229-1 format. */
    static constexpr uint8_t DTC_FORMAT_ISO_14229_1 = 0x01U;

    explicit ReadDtcInformationService(::dem::Registry const& registry = ::dem::registry);

private:
    DiagReturnCode::Type verify(uint8_t const request[], uint16_t requestLength) override;
    DiagReturnCode::Type
    process(IncomingDiagConnection& connection, uint8_t const request[], uint16_t requestLength) override;

    ::dem::Registry const& _registry;
    uint8_t _implementedRequest[1]{};
};

/** ClearDiagnosticInformation (14): all DTCs (0xFFFFFF), one DTC or a group of \p groups. */
class ClearDiagnosticInformationService final : public AbstractDiagJob
{
public:
    explicit ClearDiagnosticInformationService(
        ::etl::span<DtcGroup const> groups = {}, ::dem::Registry const& registry = ::dem::registry);

private:
    DiagReturnCode::Type verify(uint8_t const request[], uint16_t requestLength) override;
    DiagReturnCode::Type
    process(IncomingDiagConnection& connection, uint8_t const request[], uint16_t requestLength) override;

    ::etl::span<DtcGroup const> const _groups;
    ::dem::Registry const& _registry;
    uint8_t _implementedRequest[1]{};
};

} // namespace app
} // namespace uds
