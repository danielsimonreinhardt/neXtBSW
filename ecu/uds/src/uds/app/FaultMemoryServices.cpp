#include "uds/app/FaultMemoryServices.h"

#include <uds/connection/IncomingDiagConnection.h>
#include <uds/connection/PositiveResponse.h>

#include <algorithm>
#include <cstring>

namespace uds
{
namespace app
{
namespace
{
uint8_t const SID_CLEAR_DIAGNOSTIC_INFORMATION = 0x14U;
uint8_t const SID_READ_DTC_INFORMATION         = 0x19U;
uint8_t const SUPPRESS_POSITIVE_RESPONSE       = 0x80U;
uint8_t const SUBFUNCTION_MASK                 = 0x7FU;

// ReadDTCInformation sub-functions this server supports (11.3.2.2)
uint8_t const REPORT_NUMBER_OF_DTC_BY_STATUS_MASK   = 0x01U;
uint8_t const REPORT_DTC_BY_STATUS_MASK             = 0x02U;
uint8_t const REPORT_DTC_SNAPSHOT_IDENTIFICATION    = 0x03U;
uint8_t const REPORT_DTC_SNAPSHOT_RECORD_BY_DTC     = 0x04U;
uint8_t const REPORT_DTC_EXT_DATA_RECORD_BY_DTC     = 0x06U;
uint8_t const REPORT_SUPPORTED_DTC                  = 0x0AU;
uint8_t const REPORT_DTC_FAULT_DETECTION_COUNTER    = 0x14U;

// 11.3.2.3 and Annex D.8: FF all records of the DTC; FE all OBD extended data records (none without OBD)
uint8_t const ALL_RECORDS                   = 0xFFU;
uint8_t const ALL_OBD_EXTENDED_DATA_RECORDS = 0xFEU;

// Annex D.6: 19 14 reports the DTCs whose fault detection counter is prefailed, 0 < FDC < 0x7F
int8_t const FDC_FAILED = 127;

uint32_t readUint24(uint8_t const data[])
{
    return (static_cast<uint32_t>(data[0]) << 16U) | (static_cast<uint32_t>(data[1]) << 8U)
           | static_cast<uint32_t>(data[2]);
}

/** Bytes behind the service id a request of sub-function \p subfunction has; 0: not supported here. */
uint16_t requestLengthOf(uint8_t const subfunction)
{
    switch (subfunction)
    {
        case REPORT_NUMBER_OF_DTC_BY_STATUS_MASK:
        case REPORT_DTC_BY_STATUS_MASK: return 2U; // DTCStatusMask
        case REPORT_DTC_SNAPSHOT_IDENTIFICATION:
        case REPORT_SUPPORTED_DTC:
        case REPORT_DTC_FAULT_DETECTION_COUNTER: return 1U;
        case REPORT_DTC_SNAPSHOT_RECORD_BY_DTC:
        case REPORT_DTC_EXT_DATA_RECORD_BY_DTC: return 5U; // DTCMaskRecord, record number
        default: return 0U;
    }
}

/**
 * Index of the record with the smallest number above \p after (-1: start), -1 if there is none. The table keeps the
 * order of the description; 11.3.1.5 wants all records of a DTC in ascending order.
 */
template<class Record>
int32_t nextRecord(Record const* const records, uint8_t const count, int32_t const after)
{
    int32_t found = -1;
    for (uint8_t r = 0U; r < count; ++r)
    {
        int32_t const number = static_cast<int32_t>(records[r].number);
        if ((number > after) && ((found < 0) || (number < static_cast<int32_t>(records[found].number))))
        {
            found = static_cast<int32_t>(r);
        }
    }
    return found;
}

template<class Record>
int32_t findRecord(Record const* const records, uint8_t const count, uint8_t const number)
{
    for (uint8_t r = 0U; r < count; ++r)
    {
        if (records[r].number == number)
        {
            return static_cast<int32_t>(r);
        }
    }
    return -1;
}

/**
 * Appends one DTCSnapshotRecord: record number, number of identifiers, then each identifier with its stored data.
 * The fault memory keeps the data of a record back to back; it is read behind the place of the identifiers and
 * moved forward item by item (never over data that is still to be moved). A record that is not stored appends
 * nothing; an overflow is left to response.isOverflow().
 */
void appendSnapshotRecord(
    ::dem::IFaultMemory const& memory,
    uint16_t const dtc,
    ::dem::SnapshotRecord const& record,
    PositiveResponse& response)
{
    size_t data = 0U;
    for (uint8_t i = 0U; i < record.count; ++i)
    {
        data += record.items[i].bytes;
    }
    size_t const header = 2U + (2U * static_cast<size_t>(record.count));
    if (response.getAvailableDataLength() < (header + data))
    {
        (void)response.appendData(nullptr, header + data); // too long: only marks the overflow
        return;
    }
    uint8_t* const out = response.getData();
    // 0: not stored, -1: no such record; a record has at least one identifier, so data > 0
    int32_t const read = memory.readSnapshot(dtc, record.number, &out[header], data);
    if ((read <= 0) || (static_cast<size_t>(read) != data))
    {
        return;
    }
    out[0]     = record.number;
    out[1]     = record.count; // DTCSnapshotRecordNumberOfIdentifiers
    size_t pos = 2U;
    size_t src = header;
    for (uint8_t i = 0U; i < record.count; ++i)
    {
        uint16_t const did   = record.items[i].did;
        uint16_t const bytes = record.items[i].bytes;
        out[pos]             = static_cast<uint8_t>(did >> 8U);
        out[pos + 1U]        = static_cast<uint8_t>(did & 0xFFU);
        pos += 2U;
        (void)::memmove(&out[pos], &out[src], bytes);
        pos += bytes;
        src += bytes;
    }
    (void)response.increaseDataLength(header + data);
}

/**
 * Appends one DTCExtDataRecord: record number and its values. A record of counters only is always available (the
 * counters are values of the fault memory itself); a record with a data identifier only while the DTC is stored,
 * because the identifier was read when it was stored (11.3.1.7: a supported record without data is left out).
 */
void appendExtendedRecord(
    ::dem::IFaultMemory const& memory,
    uint16_t const dtc,
    bool const stored,
    ::dem::ExtendedRecord const& record,
    PositiveResponse& response)
{
    size_t data         = 0U;
    bool hasIdentifiers = false;
    for (uint8_t i = 0U; i < record.count; ++i)
    {
        data += record.items[i].bytes;
        hasIdentifiers = hasIdentifiers || (record.items[i].content == ::dem::Counter::DID);
    }
    if (hasIdentifiers && !stored)
    {
        return;
    }
    if (response.getAvailableDataLength() < (1U + data))
    {
        (void)response.appendData(nullptr, 1U + data); // too long: only marks the overflow
        return;
    }
    uint8_t* const out = response.getData();
    int32_t const read = memory.readExtended(dtc, record.number, &out[1], data);
    if ((read < 0) || (static_cast<size_t>(read) != data))
    {
        return;
    }
    out[0] = record.number;
    (void)response.increaseDataLength(1U + data);
}

} // namespace

// ------------------------------------------------------------------------------------------- 19 (11.3)

ReadDtcInformationService::ReadDtcInformationService(::dem::Registry const& registry)
: AbstractDiagJob(_implementedRequest, 1U, 0U), _registry(registry)
{
    _implementedRequest[0] = SID_READ_DTC_INFORMATION;
    setDefaultDiagReturnCode(DiagReturnCode::ISO_SUBFUNCTION_NOT_SUPPORTED);
}

DiagReturnCode::Type
ReadDtcInformationService::verify(uint8_t const request[], uint16_t const /* requestLength */)
{
    return (request[0] == SID_READ_DTC_INFORMATION) ? DiagReturnCode::OK : DiagReturnCode::NOT_RESPONSIBLE;
}

DiagReturnCode::Type ReadDtcInformationService::process(
    IncomingDiagConnection& connection, uint8_t const request[], uint16_t const requestLength)
{
    // 7.7: service and sub-function present, sub-function supported, then its exact length
    if (requestLength < 1U)
    {
        return DiagReturnCode::ISO_INVALID_FORMAT;
    }
    uint8_t const subfunction = request[0] & SUBFUNCTION_MASK;
    bool const suppress       = (request[0] & SUPPRESS_POSITIVE_RESPONSE) != 0U;
    uint16_t const expected   = requestLengthOf(subfunction);
    if (expected == 0U)
    {
        return DiagReturnCode::ISO_SUBFUNCTION_NOT_SUPPORTED;
    }
    if (requestLength != expected)
    {
        return DiagReturnCode::ISO_INVALID_FORMAT;
    }
    // The response is written into the request buffer: keep the parameters before it is released.
    ::dem::IFaultMemory const* const memory = _registry.memory;
    uint16_t const dtcCount                 = (memory != nullptr) ? memory->dtcCount() : 0U;
    uint8_t const statusMask                = (expected == 2U) ? request[1] : 0U;
    uint32_t const number                   = (expected == 5U) ? readUint24(&request[1]) : 0U;
    uint8_t const record                    = (expected == 5U) ? request[4] : 0U;
    uint16_t dtc                            = 0U;
    if (expected == 5U)
    {
        // 11.3.4: a DTCMaskRecord the server does not know (a group value included) or a record number the DTC
        // does not have is out of range; a known record without data is answered positively
        int32_t const index = (memory != nullptr) ? memory->findDtc(number) : -1;
        if (index < 0)
        {
            return DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE;
        }
        dtc                         = static_cast<uint16_t>(index);
        ::dem::DtcConfig const& cfg = memory->dtcConfig(dtc);
        bool const known            = (subfunction == REPORT_DTC_SNAPSHOT_RECORD_BY_DTC)
                                          ? ((record == ALL_RECORDS)
                                  || (findRecord(cfg.snapshots, cfg.snapshotCount, record) >= 0))
                                          : ((record == ALL_RECORDS)
                                  || ((record != ALL_OBD_EXTENDED_DATA_RECORDS)
                                      && (findRecord(cfg.extended, cfg.extendedCount, record) >= 0)));
        if (!known)
        {
            return DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE;
        }
    }

    PositiveResponse& response = connection.releaseRequestGetResponse();
    (void)response.appendUint8(subfunction);
    ::dem::DtcInfo info{};
    switch (subfunction)
    {
        case REPORT_NUMBER_OF_DTC_BY_STATUS_MASK:
        {
            (void)response.appendUint8(::dem::status::AVAILABILITY_MASK);
            (void)response.appendUint8(DTC_FORMAT_ISO_14229_1);
            (void)response.appendUint16((memory != nullptr) ? memory->countByStatusMask(statusMask) : 0U);
            break;
        }
        case REPORT_DTC_BY_STATUS_MASK:
        case REPORT_SUPPORTED_DTC:
        {
            (void)response.appendUint8(::dem::status::AVAILABILITY_MASK);
            for (uint16_t i = 0U; i < dtcCount; ++i)
            {
                // 11.3.1.3: bits the server does not support are ignored (dtcInfo masks them)
                if (memory->dtcInfo(i, info)
                    && ((subfunction == REPORT_SUPPORTED_DTC)
                        || ((info.status & static_cast<uint8_t>(statusMask & ::dem::status::AVAILABILITY_MASK))
                            != 0U)))
                {
                    (void)response.appendUint24(info.number);
                    (void)response.appendUint8(info.status);
                }
            }
            break;
        }
        case REPORT_DTC_SNAPSHOT_IDENTIFICATION:
        {
            for (uint16_t i = 0U; i < dtcCount; ++i)
            {
                if (!memory->dtcInfo(i, info) || !info.stored)
                {
                    continue;
                }
                ::dem::DtcConfig const& cfg = memory->dtcConfig(i);
                for (int32_t r = nextRecord(cfg.snapshots, cfg.snapshotCount, -1); r >= 0;
                     r = nextRecord(cfg.snapshots, cfg.snapshotCount, cfg.snapshots[r].number))
                {
                    (void)response.appendUint24(info.number);
                    (void)response.appendUint8(cfg.snapshots[r].number);
                }
            }
            break;
        }
        case REPORT_DTC_SNAPSHOT_RECORD_BY_DTC:
        {
            (void)memory->dtcInfo(dtc, info);
            (void)response.appendUint24(info.number);
            (void)response.appendUint8(info.status);
            ::dem::DtcConfig const& cfg = memory->dtcConfig(dtc);
            if (info.stored)
            {
                for (int32_t r = nextRecord(cfg.snapshots, cfg.snapshotCount, -1); r >= 0;
                     r = nextRecord(cfg.snapshots, cfg.snapshotCount, cfg.snapshots[r].number))
                {
                    if ((record == ALL_RECORDS) || (cfg.snapshots[r].number == record))
                    {
                        appendSnapshotRecord(*memory, dtc, cfg.snapshots[r], response);
                    }
                }
            }
            break;
        }
        case REPORT_DTC_EXT_DATA_RECORD_BY_DTC:
        {
            (void)memory->dtcInfo(dtc, info);
            (void)response.appendUint24(info.number);
            (void)response.appendUint8(info.status);
            ::dem::DtcConfig const& cfg = memory->dtcConfig(dtc);
            for (int32_t r = nextRecord(cfg.extended, cfg.extendedCount, -1); r >= 0;
                 r = nextRecord(cfg.extended, cfg.extendedCount, cfg.extended[r].number))
            {
                if ((record == ALL_RECORDS) || (cfg.extended[r].number == record))
                {
                    appendExtendedRecord(*memory, dtc, info.stored, cfg.extended[r], response);
                }
            }
            break;
        }
        case REPORT_DTC_FAULT_DETECTION_COUNTER:
        default:
        {
            for (uint16_t i = 0U; i < dtcCount; ++i)
            {
                if (memory->dtcInfo(i, info) && (info.fdc > 0) && (info.fdc < FDC_FAILED))
                {
                    (void)response.appendUint24(info.number);
                    (void)response.appendUint8(static_cast<uint8_t>(info.fdc));
                }
            }
            break;
        }
    }
    if (response.isOverflow())
    {
        return DiagReturnCode::ISO_RESPONSE_TOO_LONG;
    }
    if (suppress)
    {
        connection.suppressPositiveResponse();
    }
    (void)connection.sendPositiveResponseInternal(static_cast<uint16_t>(response.getLength()), *this);
    return DiagReturnCode::OK;
}

// ------------------------------------------------------------------------------------------- 14 (11.2)

ClearDiagnosticInformationService::ClearDiagnosticInformationService(
    ::etl::span<DtcGroup const> const groups, ::dem::Registry const& registry)
: AbstractDiagJob(_implementedRequest, 1U, 0U), _groups(groups), _registry(registry)
{
    _implementedRequest[0] = SID_CLEAR_DIAGNOSTIC_INFORMATION;
    setDefaultDiagReturnCode(DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE);
}

DiagReturnCode::Type
ClearDiagnosticInformationService::verify(uint8_t const request[], uint16_t const /* requestLength */)
{
    return (request[0] == SID_CLEAR_DIAGNOSTIC_INFORMATION) ? DiagReturnCode::OK
                                                             : DiagReturnCode::NOT_RESPONSIBLE;
}

DiagReturnCode::Type ClearDiagnosticInformationService::process(
    IncomingDiagConnection& connection, uint8_t const request[], uint16_t const requestLength)
{
    // groupOfDTC, optionally MemorySelection (11.2.2)
    if ((requestLength != 3U) && (requestLength != 4U))
    {
        return DiagReturnCode::ISO_INVALID_FORMAT;
    }
    // this server has one fault memory and no user defined memories: a MemorySelection is not supported
    if (requestLength == 4U)
    {
        return DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE;
    }
    uint32_t const group             = readUint24(request);
    ::dem::IFaultMemory* const memory = _registry.memory;
    if (memory == nullptr)
    {
        return DiagReturnCode::ISO_CONDITIONS_NOT_CORRECT; // the fault memory has not started (yet)
    }
    // Annex D.1: all DTCs, one DTC, or a group the project names
    if ((group == ::dem::GROUP_ALL_DTCS) || (memory->findDtc(group) >= 0))
    {
        (void)memory->clear(group);
    }
    else
    {
        auto const found = std::find_if(
            _groups.begin(), _groups.end(), [group](DtcGroup const& entry) { return entry.group == group; });
        if (found == _groups.end())
        {
            return DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE;
        }
        // a group without a DTC of this server is cleared as well (11.2.1: positive even if nothing is stored)
        uint16_t const count = memory->dtcCount();
        for (uint16_t i = 0U; i < count; ++i)
        {
            uint32_t const number = memory->dtcConfig(i).number;
            if ((number >= found->first) && (number <= found->last))
            {
                (void)memory->clear(number);
            }
        }
    }
    PositiveResponse const& response = connection.releaseRequestGetResponse();
    (void)connection.sendPositiveResponseInternal(static_cast<uint16_t>(response.getLength()), *this);
    return DiagReturnCode::OK;
}

} // namespace app
} // namespace uds
