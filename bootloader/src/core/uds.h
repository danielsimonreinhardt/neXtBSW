#pragma once

#include "app_image.h"
#include "bl_config.h"

#include <stdbool.h>
#include <stdint.h>

#define UDS_SESSION_DEFAULT     0x01U
#define UDS_SESSION_PROGRAMMING 0x02U
/* BL-21: mandatory for the boot software as well (ISO 14229-1 Table 501 step (2)) - the preprogramming step
 * of a conforming tester starts here. It grants no rights; flash access stays in the programming session. */
#define UDS_SESSION_EXTENDED    0x03U

typedef enum
{
    UDS_JOB_NONE,
    UDS_JOB_ERASE,
    UDS_JOB_CHECK,
    UDS_JOB_PROGRAM,
    UDS_JOB_RESPOND, /* final response could not be sent yet */
    UDS_JOB_RESET
} uds_job_t;

typedef struct
{
    uint8_t session;
    uint32_t lastRequestMs; /* start of S3; BL-29: moved along while S3 is stopped (request, job, response) */

    bool unlocked;
    bool seedIssued;
    uint8_t seed[BL_SECURITY_SEED_LEN]; /* BL-03: challenge of the running security access */
    uint8_t failedAttempts;
    bool lockout;
    uint32_t lockoutStartMs;

    /* Compatibility declared by the tester with routine 0xF1A0 (BL-02; 0xFF02 before BL-24, still answered); without
     * a declaration nothing is blocked, so a tester from before BL-02 keeps working. */
    bool compatDeclared;
    app_compat_result_t compatResult;

    /* Fingerprint written in this programming session (BL-04, DID 0xF184): required before erasing and downloading,
     * written into the validity record by routine 0xFF01 (BL-10), dropped with the session like the security access. */
    bool fingerprintValid;
    uint8_t fingerprint[BL_FINGERPRINT_LEN];

    bool downloadActive;
    uint32_t dlAddress;
    uint32_t dlRemaining;
    uint8_t dlBsc;

    uds_job_t job;
    uint8_t jobSid;
    uint32_t jobLastPendingMs;
    /* A "response pending" (0x78) is owed at the start of a job. After that one is sent only when the next step
     * could end beyond P2*, and never sooner than BL_PENDING_MIN_GAP_MS after the previous one (BL-25). */
    bool jobPendingDue;
    /* BL-25: measured cost of the steps, the basis of that prediction. Erasing scales with the sector size
     * (PLAT-16: one sector may take a large part of P2* on its own), so the erase with the largest cost per KB
     * since power-on is kept as the pair eraseMs / eraseKb (eraseKb 0: none measured yet) - across jobs, because
     * the check job erases the record sector as its last step. The other steps: the longest of this job. */
    uint32_t eraseMs;
    uint32_t eraseKb;
    bool jobStepKnown;
    uint32_t jobStepMaxMs;
    uint32_t jobPos;
    uint32_t jobEnd;
    uint32_t jobStart;
    uint32_t jobSize;
    uint32_t jobCrc;
    uint32_t jobCrcAcc;
    bool jobRecordErased;
    uint8_t jobData[BL_MAX_BLOCK_LENGTH];
    uint16_t jobDataLen;
    uint16_t jobDataPadded;

    uint8_t response[BL_UDS_BUFFER_SIZE];
    uint16_t responseLen;
} uds_t;

void uds_init(uds_t* uds, uint32_t nowMs);
void uds_enter_programming_session(uds_t* uds, uint32_t nowMs);
void uds_handle(uds_t* uds, uint8_t const* req, uint16_t len, bool functional, uint32_t nowMs);
void uds_poll(uds_t* uds, uint32_t nowMs);
bool uds_busy(uds_t const* uds);
