/**
 * \file E2E_P11.h
 * AUTOSAR E2E Profile 11 (successor of profile 1 for CAN, bus compatible to variants 1A/1C): CRC-8 SAE J1850 over
 * Data ID and data, 4-bit counter 0..14, Data ID mode BOTH or NIBBLE, check behaviour like profiles 4..7
 * (PRS_E2EProtocol 6.12).
 */
#ifndef E2E_E2E_P11_H
#define E2E_E2E_P11_H

#include "e2e/E2E.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef uint8_t E2E_P11DataIDMode;
#define E2E_P11_DATAID_BOTH   ((E2E_P11DataIDMode)0U)
#define E2E_P11_DATAID_NIBBLE ((E2E_P11DataIDMode)3U)

/* Bit offsets as in E2E_P01ConfigType (variants 11A/11C: CRCOffset 0, CounterOffset 8, DataIDNibbleOffset 12) */
typedef struct
{
    uint16_t CounterOffset;      /* multiple of 4 */
    uint16_t CRCOffset;          /* multiple of 8 */
    uint16_t DataID;             /* NIBBLE: high nibble must be 0 (12-bit ID) */
    uint16_t DataIDNibbleOffset; /* multiple of 4, only used in mode NIBBLE */
    E2E_P11DataIDMode DataIDMode;
    uint16_t DataLength;         /* bits, multiple of 8, 16..256 */
    uint8_t MaxDeltaCounter;     /* allowed counter step, e.g. 1: only the next counter value is OK */
} E2E_P11ConfigType;

typedef struct
{
    uint8_t Counter; /* counter of the next Protect call, 0..14 */
} E2E_P11ProtectStateType;

typedef uint8_t E2E_P11CheckStatusType;
#define E2E_P11STATUS_OK            ((E2E_P11CheckStatusType)0x00U)
#define E2E_P11STATUS_NONEWDATA     ((E2E_P11CheckStatusType)0x01U)
#define E2E_P11STATUS_ERROR         ((E2E_P11CheckStatusType)0x07U) /* CRC or Data ID nibble wrong */
#define E2E_P11STATUS_REPEATED      ((E2E_P11CheckStatusType)0x08U)
#define E2E_P11STATUS_OKSOMELOST    ((E2E_P11CheckStatusType)0x20U)
#define E2E_P11STATUS_WRONGSEQUENCE ((E2E_P11CheckStatusType)0x40U)

typedef struct
{
    uint8_t Counter; /* counter of the previous valid (CRC OK) data; 0x0E after init */
    E2E_P11CheckStatusType Status;
} E2E_P11CheckStateType;

E2E_ReturnType E2E_P11CheckConfig(E2E_P11ConfigType const* config);

E2E_ReturnType E2E_P11ProtectInit(E2E_P11ProtectStateType* state);

/** Writes Data ID nibble (NIBBLE), counter and CRC, increments the counter (Figure 6.94). length == DataLength/8. */
E2E_ReturnType
E2E_P11Protect(E2E_P11ConfigType const* config, E2E_P11ProtectStateType* state, uint8_t* data, uint16_t length);

E2E_ReturnType E2E_P11CheckInit(E2E_P11CheckStateType* state);

/** Checks received data (Figure 6.106). data == NULL with length == 0 means no new data (as in AUTOSAR). */
E2E_ReturnType E2E_P11Check(
    E2E_P11ConfigType const* config, E2E_P11CheckStateType* state, uint8_t const* data, uint16_t length);

/** SWS_E2E_00557/00558: OK/OKSOMELOST -> OK, ERROR -> ERROR, ..., checkReturn != E2E_E_OK -> ERROR. */
E2E_PCheckStatusType E2E_P11MapStatusToSM(E2E_ReturnType checkReturn, E2E_P11CheckStatusType status);

#ifdef __cplusplus
}
#endif

#endif /* E2E_E2E_P11_H */
