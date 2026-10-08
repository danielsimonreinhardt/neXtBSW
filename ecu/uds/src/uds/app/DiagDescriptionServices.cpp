#include "uds/app/DiagDescriptionServices.h"

#include <time/TimestampProvider.h>
#include <uds/connection/IncomingDiagConnection.h>
#include <uds/session/DiagSession.h>
#include <uds/session/IDiagSessionManager.h>

#include <algorithm>
#include <cstring>
#include <iterator>

namespace uds
{
namespace app
{
namespace
{
uint8_t const SID_READ_DATA_BY_IDENTIFIER  = 0x22U;
uint8_t const SID_WRITE_DATA_BY_IDENTIFIER = 0x2EU;
uint8_t const SID_ROUTINE_CONTROL          = 0x31U;
uint8_t const SID_SECURITY_ACCESS          = 0x27U;
uint8_t const SUPPRESS_POSITIVE_RESPONSE   = 0x80U;
uint8_t const SUBFUNCTION_MASK             = 0x7FU;

uint16_t readUint16(uint8_t const data[])
{
    return static_cast<uint16_t>((static_cast<uint32_t>(data[0]) << 8U) | static_cast<uint32_t>(data[1]));
}

DidEntry const* findDid(DiagDescriptionTables const& tables, uint16_t const identifier)
{
    auto const it = std::find_if(
        tables.dids.begin(),
        tables.dids.end(),
        [identifier](DidEntry const& entry) { return entry.identifier == identifier; });
    return (it == tables.dids.end()) ? nullptr : &*it;
}
} // namespace

uint8_t sessionBit(DiagDescriptionTables const& tables, uint8_t const session)
{
    for (size_t i = 0U; i < tables.sessions.size(); ++i)
    {
        if (tables.sessions[i] == session)
        {
            return static_cast<uint8_t>(1U << i);
        }
    }
    return 0U;
}

// ------------------------------------------------------------------------------------------- 22 (10.2)

DidReadService::DidReadService(
    DiagDescriptionTables const& tables, DiagAccessState const& access, DiagValues const& values)
: AbstractDiagJob(_implementedRequest, 1U, 0U), _tables(tables), _access(access), _values(values)
{
    _implementedRequest[0] = SID_READ_DATA_BY_IDENTIFIER;
    setDefaultDiagReturnCode(DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE);
}

DiagReturnCode::Type DidReadService::verify(uint8_t const request[], uint16_t const /* requestLength */)
{
    return (request[0] == SID_READ_DATA_BY_IDENTIFIER) ? DiagReturnCode::OK
                                                       : DiagReturnCode::NOT_RESPONSIBLE;
}

DiagReturnCode::Type DidReadService::process(
    IncomingDiagConnection& connection, uint8_t const request[], uint16_t const requestLength)
{
    // request: the identifiers behind the service id, at least one, two bytes each
    if ((requestLength < 2U) || ((requestLength % 2U) != 0U))
    {
        return DiagReturnCode::ISO_INVALID_FORMAT;
    }
    size_t const count = requestLength / 2U;
    if (count > _tables.maxDidsPerRequest)
    {
        return DiagReturnCode::ISO_INVALID_FORMAT;
    }
    // The response is written into the request buffer: keep what is needed before it is released.
    DidEntry const* entries[DIAG_MAX_DIDS_PER_REQUEST];
    DiagValues values     = _values;
    values.activeSession  = getDiagSessionManager().getActiveSession().getSessionByte();
    uint8_t const session = sessionBit(_tables, values.activeSession);
    size_t supported      = 0U;
    for (size_t i = 0U; i < count; ++i)
    {
        DidEntry const* const entry = findDid(_tables, readUint16(&request[2U * i]));
        // not supported in the active session: left out of the response
        if ((entry == nullptr) || (entry->read == nullptr) || ((entry->readSessions & session) == 0U))
        {
            entries[i] = nullptr;
            continue;
        }
        if (!_access.allows(entry->readSecurity))
        {
            return DiagReturnCode::ISO_SECURITY_ACCESS_DENIED;
        }
        entries[i] = entry;
        ++supported;
    }
    if (supported == 0U)
    {
        return DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE;
    }
    PositiveResponse& response = connection.releaseRequestGetResponse();
    for (size_t i = 0U; i < count; ++i)
    {
        DidEntry const* const entry = entries[i];
        if (entry == nullptr)
        {
            continue;
        }
        // COM-33: a record has exactly its described length (texts filled up), which is how a tester splits a
        // response with several identifiers - so the identifier goes in only together with its whole record
        if (response.getAvailableDataLength() < (2U + static_cast<size_t>(entry->readLength)))
        {
            return DiagReturnCode::ISO_RESPONSE_TOO_LONG;
        }
        if (!response.appendUint16(entry->identifier))
        {
            return DiagReturnCode::ISO_RESPONSE_TOO_LONG;
        }
        DiagReturnCode::Type const result = entry->read(values, response);
        if (result != DiagReturnCode::OK)
        {
            return result;
        }
    }
    (void)connection.sendPositiveResponseInternal(static_cast<uint16_t>(response.getLength()), *this);
    return DiagReturnCode::OK;
}

// ------------------------------------------------------------------------------------------- 2E (10.7)

DidWriteService::DidWriteService(DiagDescriptionTables const& tables, DiagAccessState const& access)
: AbstractDiagJob(_implementedRequest, 1U, 0U), _tables(tables), _access(access)
{
    _implementedRequest[0] = SID_WRITE_DATA_BY_IDENTIFIER;
    setDefaultDiagReturnCode(DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE);
}

DiagReturnCode::Type DidWriteService::verify(uint8_t const request[], uint16_t const /* requestLength */)
{
    return (request[0] == SID_WRITE_DATA_BY_IDENTIFIER) ? DiagReturnCode::OK
                                                        : DiagReturnCode::NOT_RESPONSIBLE;
}

DiagReturnCode::Type DidWriteService::process(
    IncomingDiagConnection& connection, uint8_t const request[], uint16_t const requestLength)
{
    // identifier and at least one byte of data
    if (requestLength < 3U)
    {
        return DiagReturnCode::ISO_INVALID_FORMAT;
    }
    uint16_t const identifier   = readUint16(request);
    DidEntry const* const entry = findDid(_tables, identifier);
    uint8_t const session
        = sessionBit(_tables, getDiagSessionManager().getActiveSession().getSessionByte());
    if ((entry == nullptr) || (entry->write == nullptr) || ((entry->writeSessions & session) == 0U))
    {
        return DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE;
    }
    if ((requestLength - 2U) != entry->writeLength)
    {
        return DiagReturnCode::ISO_INVALID_FORMAT;
    }
    if (!_access.allows(entry->writeSecurity))
    {
        return DiagReturnCode::ISO_SECURITY_ACCESS_DENIED;
    }
    DiagReturnCode::Type const result = entry->write(&request[2], entry->writeLength);
    if (result != DiagReturnCode::OK)
    {
        return result;
    }
    PositiveResponse& response = connection.releaseRequestGetResponse();
    (void)response.appendUint16(identifier);
    (void)connection.sendPositiveResponseInternal(static_cast<uint16_t>(response.getLength()), *this);
    return DiagReturnCode::OK;
}

// ------------------------------------------------------------------------------------------- 31 (13.2)

RoutineControlService::RoutineControlService(
    DiagDescriptionTables const& tables, DiagAccessState const& access)
: AbstractDiagJob(_implementedRequest, 1U, 0U), _tables(tables), _access(access), _state()
{
    _implementedRequest[0] = SID_ROUTINE_CONTROL;
    setDefaultDiagReturnCode(DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE);
    std::fill(std::begin(_state), std::end(_state), State::NEVER_STARTED);
}

DiagReturnCode::Type RoutineControlService::verify(uint8_t const request[], uint16_t const /* requestLength */)
{
    return (request[0] == SID_ROUTINE_CONTROL) ? DiagReturnCode::OK : DiagReturnCode::NOT_RESPONSIBLE;
}

bool RoutineControlService::isRunning(size_t const index) const
{
    return (index < _tables.routines.size()) && (_state[index] == State::RUNNING);
}

DiagReturnCode::Type RoutineControlService::process(
    IncomingDiagConnection& connection, uint8_t const request[], uint16_t const requestLength)
{
    // routineControlType and routineIdentifier
    if (requestLength < 3U)
    {
        return DiagReturnCode::ISO_INVALID_FORMAT;
    }
    uint8_t const subfunction = request[0] & SUBFUNCTION_MASK;
    uint16_t const identifier = readUint16(&request[1]);
    uint8_t const session
        = sessionBit(_tables, getDiagSessionManager().getActiveSession().getSessionByte());
    size_t index = 0U;
    while ((index < _tables.routines.size())
           && ((_tables.routines[index].identifier != identifier)
               || ((_tables.routines[index].sessions & session) == 0U)))
    {
        ++index;
    }
    if (index == _tables.routines.size())
    {
        return DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE;
    }
    RoutineEntry const& routine = _tables.routines[index];
    if (!_access.allows(routine.security))
    {
        return DiagReturnCode::ISO_SECURITY_ACCESS_DENIED;
    }
    if ((subfunction < 1U) || (subfunction > 3U) || (routine.steps[subfunction - 1U].run == nullptr))
    {
        return DiagReturnCode::ISO_SUBFUNCTION_NOT_SUPPORTED;
    }
    RoutineStep const& step = routine.steps[subfunction - 1U];
    if ((requestLength - 3U) != step.requestLength)
    {
        return DiagReturnCode::ISO_INVALID_FORMAT;
    }
    if (step.check != nullptr)
    {
        DiagReturnCode::Type const valid = step.check(&request[3], step.requestLength);
        if (valid != DiagReturnCode::OK)
        {
            return valid;
        }
    }
    bool const stoppable = (routine.steps[1].run != nullptr);
    State const state    = _state[index];
    if (((subfunction == 1U) && stoppable && (state == State::RUNNING))
        || ((subfunction == 2U) && (state != State::RUNNING))
        || ((subfunction == 3U) && (state == State::NEVER_STARTED)))
    {
        return DiagReturnCode::ISO_REQUEST_SEQUENCE_ERROR;
    }
    // the response is written into the request buffer
    uint8_t options[DIAG_ROUTINE_REQUEST_BYTES_ARRAY];
    (void)::memcpy(options, &request[3], step.requestLength);
    bool const suppress        = (request[0] & SUPPRESS_POSITIVE_RESPONSE) != 0U;
    PositiveResponse& response = connection.releaseRequestGetResponse();
    (void)response.appendUint8(subfunction);
    (void)response.appendUint16(identifier);
    DiagReturnCode::Type const result = step.run(options, step.requestLength, response);
    if (result != DiagReturnCode::OK)
    {
        return result;
    }
    if (subfunction == 1U)
    {
        _state[index] = stoppable ? State::RUNNING : State::DONE;
    }
    else if (subfunction == 2U)
    {
        _state[index] = State::DONE;
    }
    else
    {
        // requestResults does not change the state
    }
    if (suppress)
    {
        connection.suppressPositiveResponse();
    }
    (void)connection.sendPositiveResponseInternal(static_cast<uint16_t>(response.getLength()), *this);
    return DiagReturnCode::OK;
}

void RoutineControlService::diagSessionChanged(DiagSession const& /* session */)
{
    uint8_t zeros[DIAG_ROUTINE_REQUEST_BYTES_ARRAY];
    uint8_t scratch[DIAG_ROUTINE_RESPONSE_BYTES_ARRAY + 3U];
    (void)::memset(zeros, 0, sizeof(zeros));
    for (size_t i = 0U; i < _tables.routines.size(); ++i)
    {
        RoutineStep const& stop = _tables.routines[i].steps[1];
        if ((_state[i] == State::RUNNING) && (stop.run != nullptr))
        {
            PositiveResponse response;
            response.init(scratch, sizeof(scratch));
            (void)stop.run(zeros, stop.requestLength, response);
        }
        _state[i] = State::NEVER_STARTED;
    }
}

// ------------------------------------------------------------------------------------------- 27 (9.4)

SecurityAccessService::SecurityAccessService(
    DiagDescriptionTables const& tables, DiagAccessState& access, ISecurityKeyAlgorithm* const algorithm)
: AbstractDiagJob(_implementedRequest, 1U, 0U)
, _tables(tables)
, _access(access)
, _algorithm(algorithm)
, _pendingLevel(NO_LEVEL)
, _seed()
, _failedAttempts()
, _delay()
, _delayStartMs()
{
    _implementedRequest[0] = SID_SECURITY_ACCESS;
    setDefaultDiagReturnCode(DiagReturnCode::ISO_SUBFUNCTION_NOT_SUPPORTED);
}

DiagReturnCode::Type SecurityAccessService::verify(uint8_t const request[], uint16_t const /* requestLength */)
{
    if (request[0] != SID_SECURITY_ACCESS)
    {
        return DiagReturnCode::NOT_RESPONSIBLE;
    }
    if (_tables.securityLevels.empty())
    {
        return DiagReturnCode::ISO_SERVICE_NOT_SUPPORTED;
    }
    // the service exists in a session if one of its levels may be unlocked there (7.7, figure 5)
    uint8_t const session
        = sessionBit(_tables, getDiagSessionManager().getActiveSession().getSessionByte());
    bool const available = std::any_of(
        _tables.securityLevels.begin(),
        _tables.securityLevels.end(),
        [session](SecurityLevel const& level) { return (level.sessions & session) != 0U; });
    return available ? DiagReturnCode::OK : DiagReturnCode::ISO_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION;
}

DiagReturnCode::Type SecurityAccessService::process(
    IncomingDiagConnection& connection, uint8_t const request[], uint16_t const requestLength)
{
    if (requestLength < 1U)
    {
        return DiagReturnCode::ISO_INVALID_FORMAT;
    }
    uint8_t const subfunction = request[0] & SUBFUNCTION_MASK;
    bool const isKey          = (subfunction % 2U) == 0U;
    uint8_t const seedFunction
        = isKey ? static_cast<uint8_t>(subfunction - 1U) : subfunction; // 0 - 1 matches no level
    uint8_t index = 0U;
    while ((index < _tables.securityLevels.size())
           && (_tables.securityLevels[index].requestSeed != seedFunction))
    {
        ++index;
    }
    if ((subfunction == 0U) || (index == _tables.securityLevels.size()))
    {
        return DiagReturnCode::ISO_SUBFUNCTION_NOT_SUPPORTED;
    }
    SecurityLevel const& level = _tables.securityLevels[index];
    uint8_t const session
        = sessionBit(_tables, getDiagSessionManager().getActiveSession().getSessionByte());
    if ((level.sessions & session) == 0U)
    {
        return DiagReturnCode::ISO_SUBFUNCTION_NOT_SUPPORTED_IN_ACTIVE_SESSION;
    }
    if (isKey && (_pendingLevel != index))
    {
        return DiagReturnCode::ISO_REQUEST_SEQUENCE_ERROR;
    }
    uint16_t const expected = isKey ? static_cast<uint16_t>(1U + level.keyBytes) : 1U;
    if (requestLength != expected)
    {
        return DiagReturnCode::ISO_INVALID_FORMAT;
    }
    if ((request[0] & SUPPRESS_POSITIVE_RESPONSE) != 0U)
    {
        connection.suppressPositiveResponse();
    }
    if (isKey)
    {
        uint8_t key[DIAG_KEY_BYTES_ARRAY];
        (void)::memcpy(key, &request[1], level.keyBytes);
        return sendKey(connection, index, subfunction, key);
    }
    return requestSeed(connection, index, subfunction);
}

bool SecurityAccessService::delayActive(uint8_t const index)
{
    if (_delay[index]
        && ((::bsw::time::TimestampProvider::getTimestampMs32Bit() - _delayStartMs[index])
            >= _tables.securityLevels[index].delayMs))
    {
        _delay[index] = false;
    }
    return _delay[index];
}

DiagReturnCode::Type SecurityAccessService::requestSeed(
    IncomingDiagConnection& connection, uint8_t const index, uint8_t const subfunction)
{
    SecurityLevel const& level = _tables.securityLevels[index];
    ::etl::span<uint8_t> const seed(_seed, level.seedBytes);
    if (_access.isUnlocked(index))
    {
        // already unlocked: an all zero seed, and no sendKey is expected (9.4)
        (void)::memset(_seed, 0, sizeof(_seed));
        _pendingLevel = NO_LEVEL;
    }
    else
    {
        if (delayActive(index))
        {
            return DiagReturnCode::ISO_REQUIRED_TIME_DELAY_NOT_EXPIRED;
        }
        bool zero = true;
        if ((_algorithm == nullptr) || !_algorithm->makeSeed(level, seed))
        {
            return DiagReturnCode::ISO_CONDITIONS_NOT_CORRECT;
        }
        for (uint8_t const byte : seed)
        {
            zero = zero && (byte == 0U);
        }
        if (zero)
        {
            return DiagReturnCode::ISO_CONDITIONS_NOT_CORRECT; // would read as "unlocked" to the tester
        }
        _pendingLevel = index;
    }
    PositiveResponse& response = connection.releaseRequestGetResponse();
    (void)response.appendUint8(subfunction);
    (void)response.appendData(_seed, level.seedBytes);
    (void)connection.sendPositiveResponseInternal(static_cast<uint16_t>(response.getLength()), *this);
    return DiagReturnCode::OK;
}

DiagReturnCode::Type SecurityAccessService::sendKey(
    IncomingDiagConnection& connection,
    uint8_t const index,
    uint8_t const subfunction,
    uint8_t const key[])
{
    SecurityLevel const& level = _tables.securityLevels[index];
    _pendingLevel              = NO_LEVEL; // a seed answers one key only
    if ((_algorithm == nullptr)
        || !_algorithm->checkKey(
            level,
            ::etl::span<uint8_t const>(_seed, level.seedBytes),
            ::etl::span<uint8_t const>(key, level.keyBytes)))
    {
        ++_failedAttempts[index];
        if (_failedAttempts[index] >= level.attempts)
        {
            _failedAttempts[index] = 0U;
            _delay[index]          = true;
            _delayStartMs[index]   = ::bsw::time::TimestampProvider::getTimestampMs32Bit();
            return DiagReturnCode::ISO_EXCEEDED_NUMS_OF_ATTEMPTS;
        }
        return DiagReturnCode::ISO_INVALID_KEY;
    }
    _failedAttempts[index] = 0U;
    _access.unlock(index);
    PositiveResponse& response = connection.releaseRequestGetResponse();
    (void)response.appendUint8(subfunction);
    (void)connection.sendPositiveResponseInternal(static_cast<uint16_t>(response.getLength()), *this);
    return DiagReturnCode::OK;
}

void SecurityAccessService::diagSessionChanged(DiagSession const& /* session */)
{
    // every change of the session locks the server again (9.2); running delays keep running
    _access.lock();
    _pendingLevel = NO_LEVEL;
}

} // namespace app
} // namespace uds
