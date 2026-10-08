#pragma once

/* Services that serve the diagnostic description (COM-28): ReadDataByIdentifier (22), WriteDataByIdentifier
 * (2E), RoutineControl (31) and SecurityAccess (27) over the tables of DiagDescription.h.
 *
 * The order of the negative response checks follows ISO 14229-1 (2026): the general order of 7.7 (service,
 * session of the service, length, sub-function, session of the sub-function, sequence) and the NRC figures of
 * the services in 10.2 (22), 10.7 (2E), 13.2 (31) and 9.4 (27). Only clause numbers are cited here; the
 * wording is ours.
 *
 * All four run in the context of the diagnostic stack (DiagStack). Session checks use the session types of the
 * description, security checks the level unlocked with 27; a change of the session locks it again (9.2). */

#include "uds/app/DiagDescription.h"

#include <diag/DiagConfig.h>
#include <uds/base/AbstractDiagJob.h>
#include <uds/session/IDiagSessionChangedListener.h>

#include <etl/span.h>

#include <cstdint>

namespace uds
{
namespace app
{
/**
 * Seed and key of the security levels (27). The description names the method of every level
 * (SecurityLevel::method, COM-30); an algorithm serves the levels of its method and refuses the others. Without
 * one every requestSeed is answered with NRC 0x22 and nothing can be unlocked. The product algorithm is
 * DeviceKeySecurity (method hmac-sha256).
 */
class ISecurityKeyAlgorithm
{
public:
    /** Fills \p seed (the level's seed_bytes) for \p level. The seed must not be all zero - an all zero seed
     * tells the tester that the level is already unlocked (9.4). false: no seed now (NRC 0x22). */
    virtual bool makeSeed(SecurityLevel const& level, ::etl::span<uint8_t> seed) = 0;

    /** true if \p key answers \p seed for \p level. */
    virtual bool
    checkKey(SecurityLevel const& level, ::etl::span<uint8_t const> seed, ::etl::span<uint8_t const> key)
        = 0;

protected:
    ~ISecurityKeyAlgorithm() = default;
};

/** Security level unlocked with 27, shared by the services. Only one level is unlocked at a time (9.4). */
class DiagAccessState
{
public:
    /** \p mask: security mask of an entry (bit i: securityLevels[i]); 0 means no security access needed. */
    bool allows(uint8_t const mask) const { return (mask == 0U) || ((_unlocked & mask) != 0U); }

    void unlock(uint8_t const index) { _unlocked = static_cast<uint8_t>(1U << index); }

    void lock() { _unlocked = 0U; }

    bool isUnlocked(uint8_t const index) const { return (_unlocked & (1U << index)) != 0U; }

private:
    uint8_t _unlocked = 0U;
};

/** ReadDataByIdentifier (22) with several identifiers per request (10.2). */
class DidReadService final : public AbstractDiagJob
{
public:
    DidReadService(
        DiagDescriptionTables const& tables, DiagAccessState const& access, DiagValues const& values);

private:
    DiagReturnCode::Type verify(uint8_t const request[], uint16_t requestLength) override;
    DiagReturnCode::Type
    process(IncomingDiagConnection& connection, uint8_t const request[], uint16_t requestLength) override;

    DiagDescriptionTables const& _tables;
    DiagAccessState const& _access;
    DiagValues const& _values;
    uint8_t _implementedRequest[1]{};
};

/** WriteDataByIdentifier (2E, 10.7). */
class DidWriteService final : public AbstractDiagJob
{
public:
    DidWriteService(DiagDescriptionTables const& tables, DiagAccessState const& access);

private:
    DiagReturnCode::Type verify(uint8_t const request[], uint16_t requestLength) override;
    DiagReturnCode::Type
    process(IncomingDiagConnection& connection, uint8_t const request[], uint16_t requestLength) override;

    DiagDescriptionTables const& _tables;
    DiagAccessState const& _access;
    uint8_t _implementedRequest[1]{};
};

/**
 * RoutineControl (31, 13.2): start, stop and requestResults with the sequence check of NRC 0x24.
 *
 * A routine with a stop step runs from start until stop: a second start before the stop is a sequence error.
 * A routine without a stop step may be started again at any time. Results exist after the first start. A
 * change of the session stops every running routine (its stop step is run with an all zero request) and
 * forgets the results, because the security level they were started under is locked again.
 */
class RoutineControlService final
: public AbstractDiagJob
, public IDiagSessionChangedListener
{
public:
    RoutineControlService(DiagDescriptionTables const& tables, DiagAccessState const& access);

    void diagSessionChanged(DiagSession const& session) override;

    void diagSessionResponseSent(uint8_t /* responseCode */) override {}

    /** true while the routine with index \p index (routines[index]) is running (host test). */
    bool isRunning(size_t index) const;

private:
    enum class State : uint8_t
    {
        NEVER_STARTED,
        RUNNING,
        DONE,
    };

    DiagReturnCode::Type verify(uint8_t const request[], uint16_t requestLength) override;
    DiagReturnCode::Type
    process(IncomingDiagConnection& connection, uint8_t const request[], uint16_t requestLength) override;

    DiagDescriptionTables const& _tables;
    DiagAccessState const& _access;
    State _state[DIAG_ROUTINE_COUNT_ARRAY];
    uint8_t _implementedRequest[1]{};
};

/**
 * SecurityAccess (27, 9.4): requestSeed / sendKey per level of the description, attempts and delay.
 *
 * After `attempts` wrong keys in a row the level answers requestSeed with NRC 0x37 for `delay_ms`. A seed is
 * valid for one sendKey; a wrong key needs a new seed. A requestSeed for the unlocked level is answered with
 * an all zero seed. Not implemented (optional in 9.4): the delay after power up.
 */
class SecurityAccessService final
: public AbstractDiagJob
, public IDiagSessionChangedListener
{
public:
    SecurityAccessService(
        DiagDescriptionTables const& tables, DiagAccessState& access, ISecurityKeyAlgorithm* algorithm);

    void diagSessionChanged(DiagSession const& session) override;

    void diagSessionResponseSent(uint8_t /* responseCode */) override {}

private:
    static constexpr uint8_t NO_LEVEL = 0xFFU;

    DiagReturnCode::Type verify(uint8_t const request[], uint16_t requestLength) override;
    DiagReturnCode::Type
    process(IncomingDiagConnection& connection, uint8_t const request[], uint16_t requestLength) override;

    DiagReturnCode::Type requestSeed(IncomingDiagConnection& connection, uint8_t index, uint8_t subfunction);
    DiagReturnCode::Type sendKey(
        IncomingDiagConnection& connection, uint8_t index, uint8_t subfunction, uint8_t const key[]);
    bool delayActive(uint8_t index);

    DiagDescriptionTables const& _tables;
    DiagAccessState& _access;
    ISecurityKeyAlgorithm* const _algorithm;
    uint8_t _pendingLevel;
    uint8_t _seed[DIAG_SEED_BYTES_ARRAY];
    uint8_t _failedAttempts[DIAG_SECURITY_LEVEL_COUNT_ARRAY];
    bool _delay[DIAG_SECURITY_LEVEL_COUNT_ARRAY];
    uint32_t _delayStartMs[DIAG_SECURITY_LEVEL_COUNT_ARRAY];
    uint8_t _implementedRequest[1]{};
};

} // namespace app
} // namespace uds
