/**
 * \file E2E_P02.h
 * AUTOSAR E2E Profile 2 (legacy CAN profile): CRC8H2F in Data[0] over Data[1..] and a Data ID from a list of 16,
 * selected by the 4-bit counter 0..15 in the low nibble of Data[1] (PRS_E2EProtocol 6.6).
 */
#ifndef E2E_E2E_P02_H
#define E2E_E2E_P02_H

#include "e2e/E2E.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The header position is fixed (CRC = Data[0], counter = low nibble of Data[1]); the R22-11 member Offset is not
 * used by the profile 2 flow charts and therefore not provided (header offsets: profile 22). */
typedef struct
{
    uint16_t DataLength;         /* bits, multiple of 8, 16..2048 */
    uint8_t DataIDList[16];      /* Data ID for each counter value */
    uint8_t MaxDeltaCounterInit;
    uint8_t MaxNoNewOrRepeatedData;
    uint8_t SyncCounterInit;
} E2E_P02ConfigType;

typedef struct
{
    uint8_t Counter; /* counter of the previous Protect call; incremented before use, so the first message has 1 */
} E2E_P02ProtectStateType;

typedef uint8_t E2E_P02CheckStatusType;
#define E2E_P02STATUS_OK            ((E2E_P02CheckStatusType)0x00U)
#define E2E_P02STATUS_NONEWDATA     ((E2E_P02CheckStatusType)0x01U)
#define E2E_P02STATUS_WRONGCRC      ((E2E_P02CheckStatusType)0x02U)
#define E2E_P02STATUS_SYNC          ((E2E_P02CheckStatusType)0x03U)
#define E2E_P02STATUS_INITIAL       ((E2E_P02CheckStatusType)0x04U)
#define E2E_P02STATUS_REPEATED      ((E2E_P02CheckStatusType)0x08U)
#define E2E_P02STATUS_OKSOMELOST    ((E2E_P02CheckStatusType)0x20U)
#define E2E_P02STATUS_WRONGSEQUENCE ((E2E_P02CheckStatusType)0x40U)

typedef struct
{
    uint8_t LastValidCounter;
    uint8_t MaxDeltaCounter;
    bool WaitForFirstData;
    uint8_t LostData;
    E2E_P02CheckStatusType Status;
    uint8_t SyncCounter;
    uint8_t NoNewOrRepeatedDataCounter;
} E2E_P02CheckStateType;

E2E_ReturnType E2E_P02CheckConfig(E2E_P02ConfigType const* config);

E2E_ReturnType E2E_P02ProtectInit(E2E_P02ProtectStateType* state);

/** Increments the counter, writes it and the CRC (Figures 6.65/6.66). length must equal DataLength / 8. */
E2E_ReturnType
E2E_P02Protect(E2E_P02ConfigType const* config, E2E_P02ProtectStateType* state, uint8_t* data, uint16_t length);

E2E_ReturnType E2E_P02CheckInit(E2E_P02CheckStateType* state);

/** Checks received data (Figures 6.69-6.76). data == NULL with length == 0 means no new data. */
E2E_ReturnType E2E_P02Check(
    E2E_P02ConfigType const* config, E2E_P02CheckStateType* state, uint8_t const* data, uint16_t length);

/** Same mapping as E2E_P01MapStatusToSM (SWS_E2E_00380/00477/00381). */
E2E_PCheckStatusType
E2E_P02MapStatusToSM(E2E_ReturnType checkReturn, E2E_P02CheckStatusType status, bool profileBehavior);

#ifdef __cplusplus
}
#endif

#endif /* E2E_E2E_P02_H */
