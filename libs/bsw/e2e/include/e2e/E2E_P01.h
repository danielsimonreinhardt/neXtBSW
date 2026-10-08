/**
 * \file E2E_P01.h
 * AUTOSAR E2E Profile 1 (legacy CAN profile): CRC-8 SAE J1850 with start/XOR 0x00 over Data ID and data,
 * 4-bit counter 0..14, 16-bit Data ID in mode BOTH, ALT, LOW or NIBBLE (PRS_E2EProtocol 6.5).
 */
#ifndef E2E_E2E_P01_H
#define E2E_E2E_P01_H

#include "e2e/E2E.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Inclusion of the Data ID into the CRC (PRS_E2E_00163) */
typedef uint8_t E2E_P01DataIDMode;
#define E2E_P01_DATAID_BOTH   ((E2E_P01DataIDMode)0U) /* low byte, then high byte */
#define E2E_P01_DATAID_ALT    ((E2E_P01DataIDMode)1U) /* even counter: low byte, odd counter: high byte */
#define E2E_P01_DATAID_LOW    ((E2E_P01DataIDMode)2U) /* low byte only */
#define E2E_P01_DATAID_NIBBLE ((E2E_P01DataIDMode)3U) /* low byte + 0x00 in CRC, low nibble of high byte sent */

/* Offsets are bit offsets from the start of the data: byte = offset / 8, offset % 8 == 0 addresses the low nibble,
 * offset % 8 == 4 the high nibble (variants 1A/1C: CRCOffset 0, CounterOffset 8, DataIDNibbleOffset 12). */
typedef struct
{
    uint16_t CounterOffset;         /* multiple of 4 */
    uint16_t CRCOffset;             /* multiple of 8 */
    uint16_t DataID;                /* NIBBLE: high nibble must be 0 (12-bit ID) */
    uint16_t DataIDNibbleOffset;    /* multiple of 4, only used in mode NIBBLE */
    E2E_P01DataIDMode DataIDMode;
    uint16_t DataLength;            /* bits, multiple of 8, 16..256 */
    uint8_t MaxDeltaCounterInit;    /* allowed counter gap after a valid message */
    uint8_t MaxNoNewOrRepeatedData; /* no-new/repeated receptions tolerated before SYNC */
    uint8_t SyncCounterInit;        /* valid messages needed to resynchronise after an unexpected counter */
} E2E_P01ConfigType;

typedef struct
{
    uint8_t Counter; /* counter of the next Protect call, 0..14 */
} E2E_P01ProtectStateType;

/* Detailed check result (values of SWS_E2ELibrary 8.2.1 E2E_P01CheckStatusType) */
typedef uint8_t E2E_P01CheckStatusType;
#define E2E_P01STATUS_OK            ((E2E_P01CheckStatusType)0x00U)
#define E2E_P01STATUS_NONEWDATA     ((E2E_P01CheckStatusType)0x01U)
#define E2E_P01STATUS_WRONGCRC      ((E2E_P01CheckStatusType)0x02U) /* CRC or Data ID nibble wrong */
#define E2E_P01STATUS_SYNC          ((E2E_P01CheckStatusType)0x03U)
#define E2E_P01STATUS_INITIAL       ((E2E_P01CheckStatusType)0x04U)
#define E2E_P01STATUS_REPEATED      ((E2E_P01CheckStatusType)0x08U)
#define E2E_P01STATUS_OKSOMELOST    ((E2E_P01CheckStatusType)0x20U)
#define E2E_P01STATUS_WRONGSEQUENCE ((E2E_P01CheckStatusType)0x40U)

typedef struct
{
    uint8_t LastValidCounter;
    uint8_t MaxDeltaCounter;
    bool WaitForFirstData;
    uint8_t LostData; /* valid only for status OK and OKSOMELOST */
    E2E_P01CheckStatusType Status;
    uint8_t SyncCounter;
    uint8_t NoNewOrRepeatedDataCounter;
} E2E_P01CheckStateType;

/** Checks the configuration: E2E_E_OK, E2E_E_INPUTERR_NULL or E2E_E_INPUTERR_WRONG. */
E2E_ReturnType E2E_P01CheckConfig(E2E_P01ConfigType const* config);

E2E_ReturnType E2E_P01ProtectInit(E2E_P01ProtectStateType* state);

/**
 * Writes counter, Data ID nibble (mode NIBBLE) and CRC into data and increments the counter (PRS_E2E_00195).
 * length is the buffer size in bytes and must equal DataLength / 8.
 */
E2E_ReturnType
E2E_P01Protect(E2E_P01ConfigType const* config, E2E_P01ProtectStateType* state, uint8_t* data, uint16_t length);

E2E_ReturnType E2E_P01CheckInit(E2E_P01CheckStateType* state);

/**
 * Checks received data (PRS_E2E_00196), result in state->Status. data == NULL with length == 0 means no new data
 * (replaces the AUTOSAR state member NewDataAvailable); otherwise length must equal DataLength / 8.
 * A received counter of 15 returns E2E_E_INPUTERR_WRONG as specified (map it with E2E_P01MapStatusToSM).
 */
E2E_ReturnType E2E_P01Check(
    E2E_P01ConfigType const* config, E2E_P01CheckStateType* state, uint8_t const* data, uint16_t length);

/**
 * Maps the detailed status to E2E_PCheckStatusType (SWS_E2E_00383/00476/00384). profileBehavior true is the R4.2
 * behaviour (INITIAL -> WRONGSEQUENCE, SYNC -> OK), false the legacy one (INITIAL -> OK, SYNC -> WRONGSEQUENCE).
 * checkReturn != E2E_E_OK gives E2E_P_ERROR.
 */
E2E_PCheckStatusType
E2E_P01MapStatusToSM(E2E_ReturnType checkReturn, E2E_P01CheckStatusType status, bool profileBehavior);

#ifdef __cplusplus
}
#endif

#endif /* E2E_E2E_P01_H */
