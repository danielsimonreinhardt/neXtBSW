#include "uds.h"

#include "app_image.h"
#include "bl_hal.h"
#include "bl_sectors.h"
#include "bl_transport.h"
#include "crc32.h"
#include "seedkey.h"

#include <string.h>

#define SID_DIAGNOSTIC_SESSION_CONTROL 0x10U
#define SID_ECU_RESET                  0x11U
#define SID_READ_DATA_BY_ID            0x22U
#define SID_COMMUNICATION_CONTROL      0x28U /* BL-21: preprogramming step, ISO 14229-1 Table 501 step (7) */
#define SID_SECURITY_ACCESS            0x27U
#define SID_WRITE_DATA_BY_ID           0x2EU
#define SID_ROUTINE_CONTROL            0x31U
#define SID_REQUEST_DOWNLOAD           0x34U
#define SID_TRANSFER_DATA              0x36U
#define SID_REQUEST_TRANSFER_EXIT      0x37U
#define SID_TESTER_PRESENT             0x3EU
#define SID_NEGATIVE_RESPONSE          0x7FU
#define SID_CONTROL_DTC_SETTING        0x85U /* BL-21: preprogramming step, ISO 14229-1 Table 501 step (5) */

#define NRC_SERVICE_NOT_SUPPORTED        0x11U
#define NRC_SUBFUNCTION_NOT_SUPPORTED    0x12U
#define NRC_INCORRECT_LENGTH             0x13U
#define NRC_CONDITIONS_NOT_CORRECT       0x22U
#define NRC_REQUEST_SEQUENCE_ERROR       0x24U
#define NRC_REQUEST_OUT_OF_RANGE         0x31U
#define NRC_SECURITY_ACCESS_DENIED       0x33U
#define NRC_INVALID_KEY                  0x35U
#define NRC_EXCEEDED_NUMBER_OF_ATTEMPTS  0x36U
#define NRC_REQUIRED_TIME_DELAY          0x37U
#define NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED 0x70U
#define NRC_GENERAL_PROGRAMMING_FAILURE  0x72U
#define NRC_WRONG_BLOCK_SEQUENCE_COUNTER 0x73U
#define NRC_RESPONSE_PENDING             0x78U
#define NRC_SERVICE_NOT_IN_SESSION       0x7FU

/* CommunicationControl (BL-21, ISO 14229-1 9.5): controlTypes the boot software answers. 0x04 and 0x05 carry a
 * nodeIdentificationNumber and address the master of a sub-network, which the boot software is not. */
#define CC_ENABLE_RX_AND_TX  0x00U
#define CC_DISABLE_RX_AND_TX 0x03U
/* communicationType (Annex B.1): low nibble names the message classes (0 reserved, bits 2 and 3 reserved),
 * high nibble the sub-network - 0x0 is "this node", 0xF "the network the request arrived on". */
#define CC_MESSAGES_MASK  0x0FU
#define CC_SUBNET_MASK    0xF0U
#define CC_SUBNET_THIS    0x00U
#define CC_SUBNET_RECEIVE 0xF0U
#define CC_MESSAGES_MAX   0x03U /* networkManagement and normal communication messages */

/* ControlDTCSetting (BL-21, ISO 14229-1 9.8): DTCSettingType */
#define DTC_SETTING_ON  0x01U
#define DTC_SETTING_OFF 0x02U

#define RID_ERASE_MEMORY  0xFF00U
#define RID_CHECK_MEMORY  0xFF01U
/* BL-02: the tester declares the image's target, 25 bytes. BL-24: in the system supplier range of ISO 14229-1
 * Annex F.1 (0xF000..0xFEFF); the same number as DID 0xF1A0, which reads back what this routine checks. */
#define RID_CHECK_COMPAT 0xF1A0U
/* Number of the routine until bootloader 0.3.3, reserved by Annex F.1. Still answered so that a tester from before
 * BL-24 keeps flashing (transition, like the identification form of BL-22); canflash sends it only to bootloaders
 * older than 0.3.4. */
#define RID_CHECK_COMPAT_FORMER 0xFF02U

#define DID_BOOT_SOFTWARE_ID 0xF180U
#define DID_FINGERPRINT      0xF184U /* BL-04: applicationSoftwareFingerprint, written by the tester */
#define DID_ACTIVE_SESSION   0xF186U
#define DID_ECU_SERIAL       0xF18CU /* BL-03: ECUSerialNumber = chip UID, the tester derives the device key */
#define DID_SECURITY_INFO    0xF1A1U /* BL-03: format, method, key state, seed and key length */
#define DID_ECU_SW_VERSION   0xF189U /* BL-04: vehicleManufacturerECUSoftwareVersionNumber, ASCII */
#define DID_ECU_HW_NUMBER    0xF191U /* BL-04: vehicleManufacturerECUHardwareNumber, BL_HARDWARE_ID (BL-10) */
#define DID_TARGET_COMPAT    0xF1A0U /* BL-02: record version, target and layout identity, application area, record state */

/* DID 0xF1A0 payload: version, targetId, layoutId, appStart, appEnd, recordAddr, state of the installed record */
#define DID_TARGET_COMPAT_LEN 22U
/* DID 0xF1A1 payload (BL-03): format version, method, key state (seedkey_state_t), seed length, key length */
#define DID_SECURITY_INFO_LEN     5U
#define DID_SECURITY_INFO_VERSION 1U

/* The parts of the fingerprint and its total length belong together (bl_config.h, BL-04). */
typedef char bl_fingerprint_len_check
    [((BL_FINGERPRINT_DATE_LEN + BL_FINGERPRINT_TESTER_LEN) == BL_FINGERPRINT_LEN) ? 1 : -1];

/* Hardware number of DID 0xF191: board and derivative in plain text from the generated memory layout (BL-10). */
#define DID_ECU_HW_NUMBER_LEN ((uint16_t)(sizeof(BL_HARDWARE_ID) - 1U))

/* BL-22: an identification identifier of ISO 14229-1 Annex C.1 reports how many modules it describes before it
 * describes them. This boot software is one module - its own software - so the byte is 1 and the identification
 * text follows it. The text itself is unchanged, and it is unchanged in flash as well: the byte is put in front
 * of it when the response is built, never stored. That matters, because the application (ecu/uds), the updater
 * (upd_check_blob()) and "canflash blimage" find this bootloader by searching its flash for exactly that text.
 * Readers written before BL-22 expect the bare text; tools/canflash reads both forms during the transition. */
#define DID_NUMBER_OF_MODULES    1U
#define DID_BOOT_SOFTWARE_ID_LEN ((uint16_t)(1U + (sizeof(BL_VERSION_STRING) - 1U)))

/* Longest payload ReadDataByIdentifier answers with, so the response fits on the stack without a buffer per DID. */
#define DID_LONGER(a, b) (((a) > (b)) ? (a) : (b))
#define DID_MAX_PAYLOAD                                                                                      \
    DID_LONGER(DID_LONGER(DID_LONGER(DID_BOOT_SOFTWARE_ID_LEN, (uint16_t)DID_TARGET_COMPAT_LEN),             \
                          DID_LONGER((uint16_t)BL_FINGERPRINT_LEN, DID_ECU_HW_NUMBER_LEN)),                  \
               (uint16_t)BL_UID_LEN)

#define SUPPRESS_POS_RSP 0x80U
#define CRC_CHUNK        0x2000UL
#define PROGRAM_CHUNK    64U

/* BL-25: what a job step may cost at most - P2* less the margin for the pending response in front of it and for
 * the frame behind it. A step that has not been measured yet is assumed to cost this much. The check below keeps
 * a step that waits for the gap between two pending responses inside P2*. */
#define STEP_WORST_MS (BL_P2STAR_SERVER_MS - (2UL * BL_PENDING_MARGIN_MS))
typedef char bl_pending_gap_check[((BL_PENDING_MIN_GAP_MS + (2UL * BL_PENDING_MARGIN_MS)) < BL_P2STAR_SERVER_MS) ? 1
                                                                                                                 : -1];

static uint32_t be32(uint8_t const* p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static void put_be32(uint8_t* p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

/* What RequestDownload promises the tester as maxNumberOfBlockLength: the core's own block buffer, but never
 * more than the transport carries in one message (BL-14). Today both are BL_UDS_BUFFER_SIZE; a transport with
 * shorter messages (the inter-MCU link, BL-16) lowers the promise instead of failing on the first block. The
 * length check of TransferData below stays at BL_MAX_BLOCK_LENGTH: a message longer than that cannot arrive
 * through any transport, and what it has to fit into there is jobData. */
static uint16_t max_block_length(void)
{
    uint16_t const carried = bl_transport_max_message_len();
    return (carried < BL_MAX_BLOCK_LENGTH) ? carried : (uint16_t)BL_MAX_BLOCK_LENGTH;
}

static void send_response(uds_t* uds, uint8_t const* data, uint16_t len, uint32_t nowMs)
{
    if (bl_transport_send(data, len, nowMs))
    {
        uds->job = UDS_JOB_NONE;
        return;
    }
    (void)memcpy(uds->response, data, len);
    uds->responseLen = len;
    uds->job         = UDS_JOB_RESPOND;
}

static void send_nrc(uds_t* uds, uint8_t sid, uint8_t nrc, bool functional, uint32_t nowMs)
{
    if (functional
        && ((nrc == NRC_SERVICE_NOT_SUPPORTED) || (nrc == NRC_SUBFUNCTION_NOT_SUPPORTED)
            || (nrc == NRC_REQUEST_OUT_OF_RANGE) || (nrc == NRC_SERVICE_NOT_IN_SESSION)))
    {
        return;
    }
    uint8_t const rsp[3] = {SID_NEGATIVE_RESPONSE, sid, nrc};
    send_response(uds, rsp, sizeof(rsp), nowMs);
}

static void abort_download(uds_t* uds) { uds->downloadActive = false; }

static void forget_compat(uds_t* uds)
{
    uds->compatDeclared = false;
    uds->compatResult   = APP_COMPAT_OK;
}

static void lock_security(uds_t* uds)
{
    uds->unlocked   = false;
    uds->seedIssued = false;
}

/* BL-04: a fingerprint belongs to the programming session it was written in, like the security access.
 * Leaving the session means the next tester has to say again who it is before anything is erased. */
static void forget_fingerprint(uds_t* uds)
{
    uds->fingerprintValid = false;
    (void)memset(uds->fingerprint, 0, sizeof(uds->fingerprint));
}

static void set_session(uds_t* uds, uint8_t session)
{
    if (session != uds->session)
    {
        lock_security(uds);
        abort_download(uds);
        forget_compat(uds);
        forget_fingerprint(uds);
    }
    uds->session = session;
}

/* BL-02: a declared mismatch blocks erasing and downloading, so nothing is written before the tester reacts. */
static bool compat_allows_programming(uds_t* uds, uint8_t sid, uint32_t nowMs)
{
    if (uds->compatDeclared && (uds->compatResult != APP_COMPAT_OK))
    {
        send_nrc(uds, sid, NRC_CONDITIONS_NOT_CORRECT, false, nowMs);
        return false;
    }
    return true;
}

/* BL-04: every erase and every download is documented. The tester writes DID 0xF184 (tester identification and
 * date) first; without it the bootloader refuses with conditionsNotCorrect, the negative response ISO 14229-1
 * lists for 0x34 when a precondition is missing (0x24 is not among its response codes). The check sits behind the
 * range checks, so a wrong address keeps answering with its own code. */
static bool fingerprint_written(uds_t* uds, uint8_t sid, uint32_t nowMs)
{
    if (!uds->fingerprintValid)
    {
        send_nrc(uds, sid, NRC_CONDITIONS_NOT_CORRECT, false, nowMs);
        return false;
    }
    return true;
}

static bool programming_allowed(uds_t* uds, uint8_t sid, uint32_t nowMs)
{
    if (uds->session != UDS_SESSION_PROGRAMMING)
    {
        send_nrc(uds, sid, NRC_SERVICE_NOT_IN_SESSION, false, nowMs);
        return false;
    }
    if (!uds->unlocked)
    {
        send_nrc(uds, sid, NRC_SECURITY_ACCESS_DENIED, false, nowMs);
        return false;
    }
    return true;
}

static void start_job(uds_t* uds, uds_job_t job, uint8_t sid, uint32_t nowMs)
{
    uds->job              = job;
    uds->jobSid           = sid;
    uds->jobLastPendingMs = nowMs;
    uds->jobPendingDue    = true;
    uds->jobStepKnown     = false;
    uds->jobStepMaxMs     = 0U;
}

static void handle_session_control(uds_t* uds, uint8_t const* req, uint16_t len, bool functional, uint32_t nowMs)
{
    if (len != 2U)
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    uint8_t const sub = req[1] & (uint8_t)~SUPPRESS_POS_RSP;
    /* BL-21: the extendedDiagnosticSession is mandatory for the boot software too (ISO 14229-1 Table 501
     * step (2)). A tester runs the preprogramming step against whatever answers - the application when it
     * runs, this bootloader when there is no valid application or a boot request kept us here. It carries
     * no rights of its own: everything that writes flash stays bound to the programming session below. */
    if ((sub != UDS_SESSION_DEFAULT) && (sub != UDS_SESSION_PROGRAMMING) && (sub != UDS_SESSION_EXTENDED))
    {
        send_nrc(uds, req[0], NRC_SUBFUNCTION_NOT_SUPPORTED, functional, nowMs);
        return;
    }
    set_session(uds, sub);
    if ((req[1] & SUPPRESS_POS_RSP) == 0U)
    {
        uint8_t const rsp[6] = {
            0x50U,
            sub,
            (uint8_t)(BL_P2_SERVER_MS >> 8),
            (uint8_t)BL_P2_SERVER_MS,
            (uint8_t)((BL_P2STAR_SERVER_MS / 10UL) >> 8),
            (uint8_t)(BL_P2STAR_SERVER_MS / 10UL)};
        send_response(uds, rsp, sizeof(rsp), nowMs);
    }
}

static void handle_ecu_reset(uds_t* uds, uint8_t const* req, uint16_t len, bool functional, uint32_t nowMs)
{
    if (len != 2U)
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    uint8_t const sub = req[1] & (uint8_t)~SUPPRESS_POS_RSP;
    if ((sub != 0x01U) && (sub != 0x03U))
    {
        send_nrc(uds, req[0], NRC_SUBFUNCTION_NOT_SUPPORTED, functional, nowMs);
        return;
    }
    if ((req[1] & SUPPRESS_POS_RSP) == 0U)
    {
        uint8_t const rsp[2] = {0x51U, sub};
        (void)bl_transport_send(rsp, sizeof(rsp), nowMs);
    }
    start_job(uds, UDS_JOB_RESET, req[0], nowMs);
}

static void handle_read_data(uds_t* uds, uint8_t const* req, uint16_t len, bool functional, uint32_t nowMs)
{
    if (len != 3U)
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    uint16_t const did = (uint16_t)(((uint16_t)req[1] << 8) | req[2]);
    uint8_t rsp[3U + DID_MAX_PAYLOAD];
    rsp[0] = 0x62U;
    rsp[1] = req[1];
    rsp[2] = req[2];
    if (did == DID_BOOT_SOFTWARE_ID)
    {
        /* BL-22: numberOfModules of Annex C.1, then the identification text of that one module */
        uint16_t const n = (uint16_t)(sizeof(BL_VERSION_STRING) - 1U);
        rsp[3]           = (uint8_t)DID_NUMBER_OF_MODULES;
        (void)memcpy(&rsp[4], BL_VERSION_STRING, n);
        send_response(uds, rsp, (uint16_t)(3U + DID_BOOT_SOFTWARE_ID_LEN), nowMs);
    }
    else if (did == DID_ACTIVE_SESSION)
    {
        rsp[3] = uds->session;
        send_response(uds, rsp, 4U, nowMs);
    }
    else if (did == DID_ECU_SW_VERSION)
    {
        /* Version number of the bootloader software without the project's prefix (BL-04): the machine readable
         * counterpart of DID 0xF180, from the one place the version is written down (bl_config.h). */
        uint16_t const n = (uint16_t)(sizeof(BL_VERSION_NUMBER) - 1U);
        (void)memcpy(&rsp[3], BL_VERSION_NUMBER, n);
        send_response(uds, rsp, (uint16_t)(3U + n), nowMs);
    }
    else if (did == DID_ECU_HW_NUMBER)
    {
        /* Hardware number = "<board>/<derivative>" of the generated memory layout (BL-10); its CRC-32 is
         * BL_TARGET_ID, so a tester can compare it with the project it flashes from without a name table. */
        (void)memcpy(&rsp[3], BL_HARDWARE_ID, DID_ECU_HW_NUMBER_LEN);
        send_response(uds, rsp, (uint16_t)(3U + DID_ECU_HW_NUMBER_LEN), nowMs);
    }
    else if (did == DID_ECU_SERIAL)
    {
        /* BL-03: the chip UID, readable in every session - the tester needs it before the security access */
        seedkey_uid(&rsp[3]);
        send_response(uds, rsp, (uint16_t)(3U + BL_UID_LEN), nowMs);
    }
    else if (did == DID_SECURITY_INFO)
    {
        /* BL-03: which method this bootloader expects and whether a device key is provisioned, so a tester can
         * say why the security access is refused instead of reporting a bare negative response */
        rsp[3] = (uint8_t)DID_SECURITY_INFO_VERSION;
        rsp[4] = (uint8_t)BL_SECURITY_METHOD;
        rsp[5] = (uint8_t)seedkey_key_state();
        rsp[6] = (uint8_t)BL_SECURITY_SEED_LEN;
        rsp[7] = (uint8_t)BL_SECURITY_KEY_LEN;
        send_response(uds, rsp, (uint16_t)(3U + DID_SECURITY_INFO_LEN), nowMs);
    }
    else if (did == DID_FINGERPRINT)
    {
        /* Programming data, not identification: readable in the programming session only (BL-04). The one
         * written in this session, else the one of the installed application's record (BL-10). */
        if (uds->session != UDS_SESSION_PROGRAMMING)
        {
            send_nrc(uds, req[0], NRC_SERVICE_NOT_IN_SESSION, functional, nowMs);
            return;
        }
        if (uds->fingerprintValid)
        {
            (void)memcpy(&rsp[3], uds->fingerprint, BL_FINGERPRINT_LEN);
        }
        else if (!app_installed_fingerprint(&rsp[3]))
        {
            send_nrc(uds, req[0], NRC_CONDITIONS_NOT_CORRECT, functional, nowMs);
            return;
        }
        send_response(uds, rsp, (uint16_t)(3U + BL_FINGERPRINT_LEN), nowMs);
    }
    else if (did == DID_TARGET_COMPAT)
    {
        rsp[3] = (uint8_t)APP_COMPAT_VERSION;
        put_be32(&rsp[4], (uint32_t)BL_TARGET_ID);
        put_be32(&rsp[8], app_layout_id());
        put_be32(&rsp[12], BL_APP_START);
        put_be32(&rsp[16], BL_APP_END);
        put_be32(&rsp[20], BL_APP_RECORD_ADDR);
        rsp[24] = (uint8_t)app_installed_compat();
        send_response(uds, rsp, (uint16_t)(3U + DID_TARGET_COMPAT_LEN), nowMs);
    }
    else
    {
        send_nrc(uds, req[0], NRC_REQUEST_OUT_OF_RANGE, functional, nowMs);
    }
}

/* BL-04: the tester says who programs and when. Only DID 0xF184 can be written, only in the programming session
 * and only after security access, and only in full: a short or long fingerprint is a length error, never a
 * silently truncated entry. */
static void handle_write_data(uds_t* uds, uint8_t const* req, uint16_t len, bool functional, uint32_t nowMs)
{
    if (len < 3U)
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    uint16_t const did = (uint16_t)(((uint16_t)req[1] << 8) | req[2]);
    if (did != DID_FINGERPRINT)
    {
        send_nrc(uds, req[0], NRC_REQUEST_OUT_OF_RANGE, functional, nowMs);
        return;
    }
    if (!programming_allowed(uds, req[0], nowMs))
    {
        return;
    }
    if (len != (uint16_t)(3U + BL_FINGERPRINT_LEN))
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    (void)memcpy(uds->fingerprint, &req[3], BL_FINGERPRINT_LEN);
    uds->fingerprintValid = true;
    uint8_t const rsp[3]  = {0x6EU, req[1], req[2]};
    send_response(uds, rsp, sizeof(rsp), nowMs);
}

static void handle_security_access(uds_t* uds, uint8_t const* req, uint16_t len, bool functional, uint32_t nowMs)
{
    if (uds->session != UDS_SESSION_PROGRAMMING)
    {
        send_nrc(uds, req[0], NRC_SERVICE_NOT_IN_SESSION, functional, nowMs);
        return;
    }
    if (len < 2U)
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    if (uds->lockout && ((nowMs - uds->lockoutStartMs) < BL_SECURITY_DELAY_MS))
    {
        send_nrc(uds, req[0], NRC_REQUIRED_TIME_DELAY, functional, nowMs);
        return;
    }
    if (uds->lockout)
    {
        uds->lockout = false;
        seedkey_store_attempts(uds->failedAttempts, false);
    }

    if (req[1] == 0x01U)
    {
        if (len != 2U)
        {
            send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
            return;
        }
        /* BL-03: 16-byte seed; all zero while unlocked (ISO 14229-1). Without a provisioned device key there is
         * no security access at all: conditionsNotCorrect, DID 0xF1A1 tells the tester why. */
        uint8_t rsp[2U + BL_SECURITY_SEED_LEN];
        (void)memset(rsp, 0, sizeof(rsp));
        rsp[0] = 0x67U;
        rsp[1] = 0x01U;
        if (!uds->unlocked)
        {
            if (!seedkey_seed(uds->seed))
            {
                uds->seedIssued = false;
                send_nrc(uds, req[0], NRC_CONDITIONS_NOT_CORRECT, functional, nowMs);
                return;
            }
            uds->seedIssued = true;
            (void)memcpy(&rsp[2], uds->seed, BL_SECURITY_SEED_LEN);
        }
        send_response(uds, rsp, sizeof(rsp), nowMs);
    }
    else if (req[1] == 0x02U)
    {
        if (len != (2U + BL_SECURITY_KEY_LEN))
        {
            send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
            return;
        }
        if (!uds->seedIssued)
        {
            send_nrc(uds, req[0], NRC_REQUEST_SEQUENCE_ERROR, functional, nowMs);
            return;
        }
        uds->seedIssued = false; /* one key per seed: a repeated or late key needs a new seed */
        if (seedkey_key_matches(uds->seed, &req[2]))
        {
            uds->unlocked       = true;
            uds->failedAttempts = 0U;
            seedkey_store_attempts(0U, false);
            uint8_t const rsp[2] = {0x67U, 0x02U};
            send_response(uds, rsp, sizeof(rsp), nowMs);
        }
        else
        {
            uds->failedAttempts++;
            if (uds->failedAttempts >= BL_SECURITY_MAX_TRIES)
            {
                uds->failedAttempts = 0U;
                uds->lockout        = true;
                uds->lockoutStartMs = nowMs;
                send_nrc(uds, req[0], NRC_EXCEEDED_NUMBER_OF_ATTEMPTS, functional, nowMs);
            }
            else
            {
                send_nrc(uds, req[0], NRC_INVALID_KEY, functional, nowMs);
            }
            /* kept across a reset: an ECU reset must not hand out three new attempts (BL-03) */
            seedkey_store_attempts(uds->failedAttempts, uds->lockout);
        }
        (void)memset(uds->seed, 0, sizeof(uds->seed));
    }
    else
    {
        send_nrc(uds, req[0], NRC_SUBFUNCTION_NOT_SUPPORTED, functional, nowMs);
    }
}

static void handle_routine_control(uds_t* uds, uint8_t const* req, uint16_t len, bool functional, uint32_t nowMs)
{
    if (len < 4U)
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    if (req[1] != 0x01U)
    {
        send_nrc(uds, req[0], NRC_SUBFUNCTION_NOT_SUPPORTED, functional, nowMs);
        return;
    }
    uint16_t const rid = (uint16_t)(((uint16_t)req[2] << 8) | req[3]);
    bool checkCompat = false;
    if ((rid == RID_CHECK_COMPAT) || (rid == RID_CHECK_COMPAT_FORMER))
    {
        checkCompat = true;
    }
    if ((rid != RID_ERASE_MEMORY) && (rid != RID_CHECK_MEMORY) && !checkCompat)
    {
        send_nrc(uds, req[0], NRC_REQUEST_OUT_OF_RANGE, functional, nowMs);
        return;
    }
    if (!programming_allowed(uds, req[0], nowMs))
    {
        return;
    }
    if (checkCompat)
    {
        if (len != 25U)
        {
            send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, false, nowMs);
            return;
        }
        uds->compatResult   = app_compat_match((uint32_t)req[4], be32(&req[5]), be32(&req[9]), be32(&req[13]),
                                               be32(&req[17]), be32(&req[21]));
        uds->compatDeclared = true;
        /* the response repeats the identifier of the request, whichever of the two it was */
        uint8_t const rsp[5] = {0x71U, 0x01U, req[2], req[3], (uint8_t)uds->compatResult};
        send_response(uds, rsp, sizeof(rsp), nowMs);
    }
    else if (rid == RID_ERASE_MEMORY)
    {
        if ((len != 13U) || (req[4] != 0x44U))
        {
            send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, false, nowMs);
            return;
        }
        if (!compat_allows_programming(uds, req[0], nowMs))
        {
            return;
        }
        uint32_t const start = be32(&req[5]);
        uint32_t const size  = be32(&req[9]);
        if (!app_range_valid(start, size))
        {
            send_nrc(uds, req[0], NRC_REQUEST_OUT_OF_RANGE, false, nowMs);
            return;
        }
        /* Sector table instead of a power-of-two mask (PLAT-16): the sectors of a region may differ in size. */
        uint32_t from = 0U;
        uint32_t to   = 0U;
        if (!bl_sector_range(start, size, &from, &to) || (from < BL_APP_START) || (to > BL_APP_END))
        {
            send_nrc(uds, req[0], NRC_REQUEST_OUT_OF_RANGE, false, nowMs);
            return;
        }
        if (!fingerprint_written(uds, req[0], nowMs))
        {
            return; /* BL-04: nothing is erased before the tester has said who it is */
        }
        abort_download(uds);
        uds->jobPos          = from;
        uds->jobEnd          = to;
        uds->jobRecordErased = false;
        start_job(uds, UDS_JOB_ERASE, req[0], nowMs);
    }
    else
    {
        if (len != 16U)
        {
            send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, false, nowMs);
            return;
        }
        uint32_t const start = be32(&req[4]);
        uint32_t const size  = be32(&req[8]);
        if ((start != BL_APP_START) || !app_range_valid(start, size))
        {
            send_nrc(uds, req[0], NRC_REQUEST_OUT_OF_RANGE, false, nowMs);
            return;
        }
        uds->jobStart          = start;
        uds->jobSize           = size;
        uds->jobCrc            = be32(&req[12]);
        uds->jobPos            = start;
        uds->jobEnd            = start + size;
        uds->jobCrcAcc         = 0U;
        start_job(uds, UDS_JOB_CHECK, req[0], nowMs);
    }
}

static void handle_request_download(uds_t* uds, uint8_t const* req, uint16_t len, bool functional, uint32_t nowMs)
{
    if (!programming_allowed(uds, req[0], nowMs))
    {
        return;
    }
    if ((len != 11U) || (req[1] != 0x00U) || (req[2] != 0x44U))
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    if (!compat_allows_programming(uds, req[0], nowMs))
    {
        return;
    }
    uint32_t const address = be32(&req[3]);
    uint32_t const size    = be32(&req[7]);
    if (!app_range_valid(address, size) || ((address % BL_PHRASE_SIZE) != 0U))
    {
        send_nrc(uds, req[0], NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED, functional, nowMs);
        return;
    }
    if (!fingerprint_written(uds, req[0], nowMs))
    {
        return; /* BL-04: no download without a fingerprint */
    }
    uds->downloadActive  = true;
    uds->dlAddress       = address;
    uds->dlRemaining     = size;
    uds->dlBsc           = 1U;
    uint16_t const maxBlock = max_block_length();
    uint8_t const rsp[4]    = {0x74U, 0x20U, (uint8_t)(maxBlock >> 8), (uint8_t)maxBlock};
    send_response(uds, rsp, sizeof(rsp), nowMs);
}

static void handle_transfer_data(uds_t* uds, uint8_t const* req, uint16_t len, bool functional, uint32_t nowMs)
{
    if (!uds->downloadActive)
    {
        send_nrc(uds, req[0], NRC_REQUEST_SEQUENCE_ERROR, functional, nowMs);
        return;
    }
    if ((len < 2U) || (len > BL_MAX_BLOCK_LENGTH))
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    if (req[1] == (uint8_t)(uds->dlBsc - 1U))
    {
        uint8_t const rsp[2] = {0x76U, req[1]}; /* repeated block, already programmed */
        send_response(uds, rsp, sizeof(rsp), nowMs);
        return;
    }
    if (req[1] != uds->dlBsc)
    {
        send_nrc(uds, req[0], NRC_WRONG_BLOCK_SEQUENCE_COUNTER, functional, nowMs);
        return;
    }
    uint16_t const dataLen = (uint16_t)(len - 2U);
    bool const lastBlock   = (dataLen == uds->dlRemaining);
    if ((dataLen == 0U) || (dataLen > uds->dlRemaining)
        || (!lastBlock && ((dataLen % BL_PHRASE_SIZE) != 0U)))
    {
        send_nrc(uds, req[0], NRC_GENERAL_PROGRAMMING_FAILURE, functional, nowMs);
        abort_download(uds);
        return;
    }
    (void)memcpy(uds->jobData, &req[2], dataLen);
    uint16_t padded = dataLen;
    while ((padded % BL_PHRASE_SIZE) != 0U)
    {
        uds->jobData[padded] = 0xFFU;
        padded++;
    }
    uds->jobDataLen    = dataLen;
    uds->jobDataPadded = padded;
    uds->jobPos        = 0U;
    start_job(uds, UDS_JOB_PROGRAM, req[0], nowMs);
}

static void handle_transfer_exit(uds_t* uds, uint8_t const* req, uint16_t len, bool functional, uint32_t nowMs)
{
    if (len != 1U)
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    if (!uds->downloadActive || (uds->dlRemaining != 0U))
    {
        send_nrc(uds, req[0], NRC_REQUEST_SEQUENCE_ERROR, functional, nowMs);
        return;
    }
    abort_download(uds);
    uint8_t const rsp[1] = {0x77U};
    send_response(uds, rsp, sizeof(rsp), nowMs);
}

static void handle_tester_present(uds_t* uds, uint8_t const* req, uint16_t len, bool functional, uint32_t nowMs)
{
    if (len != 2U)
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    if ((req[1] & (uint8_t)~SUPPRESS_POS_RSP) != 0U)
    {
        send_nrc(uds, req[0], NRC_SUBFUNCTION_NOT_SUPPORTED, functional, nowMs);
        return;
    }
    if ((req[1] & SUPPRESS_POS_RSP) == 0U)
    {
        uint8_t const rsp[2] = {0x7EU, 0x00U};
        send_response(uds, rsp, sizeof(rsp), nowMs);
    }
}

/* BL-21, ISO 14229-1 9.5 and Table 501 step (7): the client switches off the non-diagnostic communication of
 * every server before it starts programming. The boot software sends and evaluates no application messages at
 * all, so the standard asks for nothing but the positive response - but it has to arrive, otherwise a conforming
 * tester stops before the programming step. Nothing is stored: there is no state to restore later. */
static void handle_communication_control(uds_t* uds, uint8_t const* req, uint16_t len, bool functional,
                                         uint32_t nowMs)
{
    if (len < 2U)
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    uint8_t const sub = req[1] & (uint8_t)~SUPPRESS_POS_RSP;
    /* Checked before the length, because the length that is correct depends on the controlType: 0x04 and 0x05
     * carry two more bytes. Neither is supported, so their length never has to be judged. */
    if (sub > CC_DISABLE_RX_AND_TX)
    {
        send_nrc(uds, req[0], NRC_SUBFUNCTION_NOT_SUPPORTED, functional, nowMs);
        return;
    }
    if (len != 3U)
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    uint8_t const messages = req[2] & CC_MESSAGES_MASK;
    uint8_t const subnet   = req[2] & CC_SUBNET_MASK;
    if ((messages == 0U) || (messages > CC_MESSAGES_MAX)
        || ((subnet != CC_SUBNET_THIS) && (subnet != CC_SUBNET_RECEIVE)))
    {
        /* reserved message class or a sub-network this node does not have (9.5.4: requestOutOfRange) */
        send_nrc(uds, req[0], NRC_REQUEST_OUT_OF_RANGE, functional, nowMs);
        return;
    }
    if ((req[1] & SUPPRESS_POS_RSP) == 0U)
    {
        uint8_t const rsp[2] = {0x68U, sub};
        send_response(uds, rsp, sizeof(rsp), nowMs);
    }
}

/* BL-21, ISO 14229-1 9.8 and Table 501 step (5): the client stops the DTC status bit updates before programming.
 * The boot software has no fault memory, so there are no status bits to freeze and no action is required - only
 * the positive response, without which a conforming tester stops. */
static void handle_control_dtc_setting(uds_t* uds, uint8_t const* req, uint16_t len, bool functional,
                                       uint32_t nowMs)
{
    if (len < 2U)
    {
        send_nrc(uds, req[0], NRC_INCORRECT_LENGTH, functional, nowMs);
        return;
    }
    uint8_t const sub = req[1] & (uint8_t)~SUPPRESS_POS_RSP;
    if ((sub != DTC_SETTING_ON) && (sub != DTC_SETTING_OFF))
    {
        send_nrc(uds, req[0], NRC_SUBFUNCTION_NOT_SUPPORTED, functional, nowMs);
        return;
    }
    if (len > 2U)
    {
        /* A DTCSettingControlOptionRecord names the DTCs to control; without a fault memory there is nothing
         * the record could refer to (9.8.4: requestOutOfRange for an error in that record). */
        send_nrc(uds, req[0], NRC_REQUEST_OUT_OF_RANGE, functional, nowMs);
        return;
    }
    if ((req[1] & SUPPRESS_POS_RSP) == 0U)
    {
        uint8_t const rsp[2] = {0xC5U, sub};
        send_response(uds, rsp, sizeof(rsp), nowMs);
    }
}

void uds_init(uds_t* uds, uint32_t nowMs)
{
    (void)memset(uds, 0, sizeof(*uds));
    uds->session       = UDS_SESSION_DEFAULT;
    uds->lastRequestMs = nowMs;
    uds->job           = UDS_JOB_NONE;
    /* BL-03: failed attempts and a running lockout survive a reset; the delay starts again from here */
    seedkey_init();
    uds->failedAttempts = seedkey_failed_attempts();
    uds->lockout        = seedkey_lockout_pending();
    uds->lockoutStartMs = nowMs;
}

void uds_enter_programming_session(uds_t* uds, uint32_t nowMs)
{
    set_session(uds, UDS_SESSION_PROGRAMMING);
    uds->lastRequestMs = nowMs;
}

bool uds_busy(uds_t const* uds) { return uds->job != UDS_JOB_NONE; }

void uds_handle(uds_t* uds, uint8_t const* req, uint16_t len, bool functional, uint32_t nowMs)
{
    uds->lastRequestMs = nowMs;
    if (len == 0U)
    {
        return;
    }
    switch (req[0])
    {
        case SID_DIAGNOSTIC_SESSION_CONTROL: handle_session_control(uds, req, len, functional, nowMs); break;
        case SID_ECU_RESET: handle_ecu_reset(uds, req, len, functional, nowMs); break;
        case SID_READ_DATA_BY_ID: handle_read_data(uds, req, len, functional, nowMs); break;
        case SID_COMMUNICATION_CONTROL: handle_communication_control(uds, req, len, functional, nowMs); break;
        case SID_CONTROL_DTC_SETTING: handle_control_dtc_setting(uds, req, len, functional, nowMs); break;
        case SID_WRITE_DATA_BY_ID: handle_write_data(uds, req, len, functional, nowMs); break;
        case SID_SECURITY_ACCESS: handle_security_access(uds, req, len, functional, nowMs); break;
        case SID_ROUTINE_CONTROL: handle_routine_control(uds, req, len, functional, nowMs); break;
        case SID_REQUEST_DOWNLOAD: handle_request_download(uds, req, len, functional, nowMs); break;
        case SID_TRANSFER_DATA: handle_transfer_data(uds, req, len, functional, nowMs); break;
        case SID_REQUEST_TRANSFER_EXIT: handle_transfer_exit(uds, req, len, functional, nowMs); break;
        case SID_TESTER_PRESENT: handle_tester_present(uds, req, len, functional, nowMs); break;
        default: send_nrc(uds, req[0], NRC_SERVICE_NOT_SUPPORTED, functional, nowMs); break;
    }
}

static bool record_sector_erased(void)
{
    uint8_t const* p = hal_flash_ptr(BL_APP_RECORD_ADDR);
    for (uint32_t i = 0U; i < sizeof(app_record_t); ++i)
    {
        if (p[i] != 0xFFU)
        {
            return false;
        }
    }
    return true;
}

static void finish_job_error(uds_t* uds, uint8_t nrc, uint32_t nowMs)
{
    uint8_t const rsp[3] = {SID_NEGATIVE_RESPONSE, uds->jobSid, nrc};
    send_response(uds, rsp, sizeof(rsp), nowMs);
}


static void job_step(uds_t* uds, uint32_t nowMs)
{
    switch (uds->job)
    {
        case UDS_JOB_ERASE:
        {
            if (!uds->jobRecordErased)
            {
                if (!hal_flash_erase_sector(BL_APP_RECORD_ADDR))
                {
                    finish_job_error(uds, NRC_GENERAL_PROGRAMMING_FAILURE, nowMs);
                    return;
                }
                uds->jobRecordErased = true;
                return;
            }
            if (uds->jobPos < uds->jobEnd)
            {
                bl_sector_t sector;
                if (!bl_sector_at(uds->jobPos, &sector) || !hal_flash_erase_sector(uds->jobPos))
                {
                    finish_job_error(uds, NRC_GENERAL_PROGRAMMING_FAILURE, nowMs);
                    return;
                }
                uds->jobPos        = sector.start + sector.size;
                return;
            }
            uint8_t const rsp[5] = {0x71U, 0x01U, (uint8_t)(RID_ERASE_MEMORY >> 8), (uint8_t)RID_ERASE_MEMORY, 0x00U};
            send_response(uds, rsp, sizeof(rsp), nowMs);
            return;
        }
        case UDS_JOB_CHECK:
        {
            if (uds->jobPos < uds->jobEnd)
            {
                uint32_t n = uds->jobEnd - uds->jobPos;
                n          = (n > CRC_CHUNK) ? CRC_CHUNK : n;
                uds->jobCrcAcc = crc32_update(uds->jobCrcAcc, hal_flash_ptr(uds->jobPos), n);
                uds->jobPos += n;
                return;
            }
            uint8_t status = 0x01U;
            if ((uds->jobCrcAcc == uds->jobCrc) && hal_app_entry_plausible(uds->jobStart, uds->jobSize))
            {
                app_record_t record;
                /* BL-10: the fingerprint of this session goes into the record and survives the reset */
                app_record_build(&record, uds->jobStart, uds->jobSize, uds->jobCrc,
                                 uds->fingerprintValid ? uds->fingerprint : NULL);
                bool ok = record_sector_erased() || hal_flash_erase_sector(BL_APP_RECORD_ADDR);
                ok      = ok && hal_flash_program(BL_APP_RECORD_ADDR, (uint8_t const*)&record, sizeof(record));
                if (!ok)
                {
                    finish_job_error(uds, NRC_GENERAL_PROGRAMMING_FAILURE, nowMs);
                    return;
                }
                status = 0x00U;
            }
            uint8_t const rsp[5] = {0x71U, 0x01U, (uint8_t)(RID_CHECK_MEMORY >> 8), (uint8_t)RID_CHECK_MEMORY, status};
            send_response(uds, rsp, sizeof(rsp), nowMs);
            return;
        }
        case UDS_JOB_PROGRAM:
        {
            if (uds->jobPos < uds->jobDataPadded)
            {
                uint16_t n = (uint16_t)(uds->jobDataPadded - uds->jobPos);
                n          = (n > PROGRAM_CHUNK) ? PROGRAM_CHUNK : n;
                if (!hal_flash_program(uds->dlAddress + uds->jobPos, &uds->jobData[uds->jobPos], n))
                {
                    abort_download(uds);
                    finish_job_error(uds, NRC_GENERAL_PROGRAMMING_FAILURE, nowMs);
                    return;
                }
                uds->jobPos = (uint16_t)(uds->jobPos + n);
                return;
            }
            uds->dlAddress += uds->jobDataLen;
            uds->dlRemaining -= uds->jobDataLen;
            uint8_t const rsp[2] = {0x76U, uds->dlBsc};
            uds->dlBsc++;
            send_response(uds, rsp, sizeof(rsp), nowMs);
            return;
        }
        default: break;
    }
}

/* BL-25: size in KB of the sector the next job step erases, 0 if it erases none. The last step of the check job
 * erases the record sector (unless it is still erased) and writes the record; it counts as an erase. */
static uint32_t next_erase_kb(uds_t const* uds)
{
    uint32_t address = BL_APP_RECORD_ADDR;
    bool erases      = false;
    if (uds->job == UDS_JOB_ERASE)
    {
        if (!uds->jobRecordErased)
        {
            erases = true;
        }
        else if (uds->jobPos < uds->jobEnd)
        {
            address = uds->jobPos;
            erases  = true;
        }
        else
        {
            /* only the final response is left */
        }
    }
    else if ((uds->job == UDS_JOB_CHECK) && (uds->jobPos >= uds->jobEnd))
    {
        erases = true;
    }
    else
    {
        /* no erase in this step */
    }
    uint32_t kb = 0U;
    if (erases)
    {
        bl_sector_t sector;
        kb = 1U;
        if (bl_sector_at(address, &sector))
        {
            kb = (sector.size + 1023UL) >> 10;
        }
    }
    return kb;
}

/* BL-25: whether the next step could end beyond P2* counted from the last pending response. Erasing is predicted
 * from the largest cost per KB measured since power-on times the size of the next sector - so a 128-KB sector
 * behind a 16-KB one is not underestimated - the other steps from the longest step of this job. An unmeasured
 * step counts as STEP_WORST_MS, and no prediction exceeds it: behind a fresh pending response every step fits.
 * Cross multiplied instead of divided (no division routine on Cortex-M0+, S32K116 bootloader area of 10 KB). */
static bool next_step_overruns(uds_t const* uds, uint32_t eraseKb, uint32_t sincePendingMs)
{
    bool overruns = true;
    if ((sincePendingMs + BL_PENDING_MARGIN_MS) < BL_P2STAR_SERVER_MS)
    {
        uint32_t const budgetMs = BL_P2STAR_SERVER_MS - BL_PENDING_MARGIN_MS - sincePendingMs;
        /* +1 ms: a measured n ms may have been almost n + 1 ms (tick granularity) */
        if ((budgetMs > STEP_WORST_MS)
            || ((eraseKb != 0U) && (uds->eraseKb != 0U)
                && (((uds->eraseMs + 1UL) * eraseKb) < (budgetMs * uds->eraseKb)))
            || ((eraseKb == 0U) && uds->jobStepKnown && ((uds->jobStepMaxMs + 1UL) < budgetMs)))
        {
            overruns = false;
        }
    }
    return overruns;
}

void uds_poll(uds_t* uds, uint32_t nowMs)
{
    switch (uds->job)
    {
        case UDS_JOB_NONE: break;
        case UDS_JOB_RESPOND:
            if (bl_transport_send(uds->response, uds->responseLen, nowMs))
            {
                uds->job = UDS_JOB_NONE;
            }
            break;
        case UDS_JOB_RESET:
            if (bl_transport_tx_done())
            {
                hal_reset();
            }
            break;
        default:
        {
            /* Flash jobs can exceed P2 at any step: announce them right away, then keep the tester waiting with as
             * few "response pending" as ISO 14229-2 allows (BL-25).
             *
             * A single sector erase can use up a large part of P2* on its own (STM32F4: 128 KB sectors take
             * seconds, doc/architecture/plattformen.md 2.3), and a refresh that looked only at the time since the
             * last one would notice that afterwards - the tester would already have given up. PLAT-16 therefore
             * announced every sector, which on an S32K144 (4 KB in about 4 ms) meant one 0x78 every 4 ms, far
             * below the 0.3 x P2* the standard asks for between two of them. Now a pending response is sent only
             * when the next step could end beyond P2* (next_step_overruns()), and not sooner than
             * BL_PENDING_MIN_GAP_MS after the previous one. If it would come too soon, the step waits for it. */
            uint32_t const sincePending = nowMs - uds->jobLastPendingMs;
            uint32_t const eraseKb      = next_erase_kb(uds);
            if (uds->jobPendingDue || next_step_overruns(uds, eraseKb, sincePending))
            {
                if (uds->jobPendingDue || (sincePending >= BL_PENDING_MIN_GAP_MS))
                {
                    uint8_t const rsp[3] = {SID_NEGATIVE_RESPONSE, uds->jobSid, NRC_RESPONSE_PENDING};
                    if (bl_transport_send(rsp, sizeof(rsp), nowMs))
                    {
                        uds->jobPendingDue    = false;
                        uds->jobLastPendingMs = nowMs;
                    }
                }
                break;
            }
            job_step(uds, nowMs);
            /* measured with the core's own readings, whatever the family HAL does inside the step */
            uint32_t const tookMs = hal_millis() - nowMs;
            if (eraseKb != 0U)
            {
                if ((uds->eraseKb == 0U) || ((tookMs * uds->eraseKb) > (uds->eraseMs * eraseKb)))
                {
                    uds->eraseMs = tookMs;
                    uds->eraseKb = eraseKb;
                }
            }
            else if (!uds->jobStepKnown || (tookMs > uds->jobStepMaxMs))
            {
                uds->jobStepMaxMs = tookMs;
                uds->jobStepKnown = true;
            }
            else
            {
                /* shorter than the longest step so far */
            }
            break;
        }
    }

    /* S3 (ISO 14229-2, session layer timing): stopped from the start of a request - its first frame - until its
     * final response has been transmitted (every frame handed to the controller), or until the action is complete
     * when no response is sent;
     * a "response pending" does not count. A reception that ends in an error restarts it as well. Held here by
     * moving the start along while stopped, so it starts from the last moment of the stop (BL-29). Before, S3
     * ran from the moment the previous request was complete: a block that took longer than S3 to arrive (STmin
     * 1 ms, Windows tester, VER-07) found the session back in default and got NRC 0x24, and a flash job longer
     * than S3 (erasing 128-KB sectors, or the whole S32K148 application area) lost the session with its positive
     * response. */
    if (bl_transport_rx_pending() || (uds->job != UDS_JOB_NONE) || !bl_transport_tx_idle())
    {
        uds->lastRequestMs = nowMs;
    }
    /* Every non-default session falls back, not just the programming one (BL-21: the extended session of the
     * preprogramming step must not keep the bootloader awake after the tester is gone). */
    else if ((uds->session != UDS_SESSION_DEFAULT) && ((nowMs - uds->lastRequestMs) > BL_S3_SERVER_MS))
    {
        set_session(uds, UDS_SESSION_DEFAULT);
    }
    else
    {
        /* default session, or within S3: nothing to do */
    }
}
