/**
 * \file E2E.h
 * AUTOSAR E2E library, common part: return codes, profile independent check status, CRC routines (COM-10).
 *
 * C99, no dependency on OpenBSW, no dynamic memory, reentrant: all state is passed in per message (one
 * configuration and one state structure for each protected or checked message on any bus).
 *
 * Behaviour follows AUTOSAR_PRS_E2EProtocol and AUTOSAR_SWS_E2ELibrary R22-11 (profiles 1, 2, 11), CRCs follow
 * AUTOSAR_SWS_CRCLibrary R22-11. Deviations from the AUTOSAR API are listed in libs/bsw/e2e/doc/e2e.md.
 */
#ifndef E2E_E2E_H
#define E2E_E2E_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Return codes of the E2E functions (SWS_E2E_00047). Plain uint8_t, independent of -fshort-enums. */
typedef uint8_t E2E_ReturnType;
#define E2E_E_OK             ((E2E_ReturnType)0x00U)
#define E2E_E_INPUTERR_NULL  ((E2E_ReturnType)0x13U) /* at least one pointer parameter is NULL */
#define E2E_E_INPUTERR_WRONG ((E2E_ReturnType)0x17U) /* parameter or configuration out of range */

/* Profile independent status of one check (SWS_E2E_00347), result of E2E_PxxMapStatusToSM(). */
typedef uint8_t E2E_PCheckStatusType;
#define E2E_P_OK            ((E2E_PCheckStatusType)0x00U) /* all checks passed (incl. counter) */
#define E2E_P_REPEATED      ((E2E_PCheckStatusType)0x01U) /* counter repeated */
#define E2E_P_WRONGSEQUENCE ((E2E_PCheckStatusType)0x02U) /* counter jumped by more than allowed */
#define E2E_P_ERROR         ((E2E_PCheckStatusType)0x03U) /* CRC, Data ID, length or input error */
#define E2E_P_NOTAVAILABLE  ((E2E_PCheckStatusType)0x04U) /* nothing checked yet: init value, never returned */
#define E2E_P_NONEWDATA     ((E2E_PCheckStatusType)0x05U) /* no new data */

/**
 * CRC-8 SAE J1850 (polynomial 0x1D, initial value 0xFF, XOR value 0xFF), semantics of Crc_CalculateCRC8
 * (SWS_Crc_00030): with isFirstCall the start value is ignored, otherwise startValue is the result of the previous
 * call of a call sequence. Check value over "123456789" = 0x4B.
 */
uint8_t E2E_Crc8(uint8_t const* data, uint32_t length, uint8_t startValue, bool isFirstCall);

/** CRC-8 polynomial 0x2F (initial value 0xFF, XOR value 0xFF), semantics of Crc_CalculateCRC8H2F. Check = 0xDF. */
uint8_t E2E_Crc8H2F(uint8_t const* data, uint32_t length, uint8_t startValue, bool isFirstCall);

#ifdef __cplusplus
}
#endif

#endif /* E2E_E2E_H */
