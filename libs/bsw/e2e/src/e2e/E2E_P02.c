/**
 * \file E2E_P02.c
 * AUTOSAR E2E Profile 2 (PRS_E2EProtocol R22-11, 6.6; SWS_E2ELibrary R22-11, 8.3.2).
 */
#include "e2e/E2E_P02.h"

#include "E2E_Internal.h"

#include <stddef.h>

#define P02_COUNTER_MODULO 16U
#define P02_MAX_DELTA_CAP  15U
#define P02_MAX_DATALENGTH (256U * 8U) /* PRS_E2E_00121 */

E2E_ReturnType E2E_P02CheckConfig(E2E_P02ConfigType const* config)
{
    if (config == NULL)
    {
        return E2E_E_INPUTERR_NULL;
    }
    bool const valid = ((config->DataLength % 8U) == 0U) && (config->DataLength >= 16U)
                       && (config->DataLength <= P02_MAX_DATALENGTH);
    return valid ? E2E_E_OK : E2E_E_INPUTERR_WRONG;
}

/* Figure 6.70: CRC8H2F over Data[1..DataLength/8-1], then the Data ID of the counter */
static uint8_t p02Crc(uint8_t const* data, uint16_t length, uint8_t dataId)
{
    uint8_t const crc = E2E_Crc8H2F(&data[1], (uint32_t)length - 1U, 0xFFU, true);
    return E2E_Crc8H2F(&dataId, 1U, crc, false);
}

E2E_ReturnType E2E_P02ProtectInit(E2E_P02ProtectStateType* state)
{
    if (state == NULL)
    {
        return E2E_E_INPUTERR_NULL;
    }
    state->Counter = 0U;
    return E2E_E_OK;
}

E2E_ReturnType
E2E_P02Protect(E2E_P02ConfigType const* config, E2E_P02ProtectStateType* state, uint8_t* data, uint16_t length)
{
    if ((config == NULL) || (state == NULL) || (data == NULL))
    {
        return E2E_E_INPUTERR_NULL;
    }
    if ((E2E_P02CheckConfig(config) != E2E_E_OK) || (length != (config->DataLength / 8U)))
    {
        return E2E_E_INPUTERR_WRONG;
    }
    state->Counter = (state->Counter < 15U) ? (uint8_t)(state->Counter + 1U) : 0U;
    data[1]        = (uint8_t)((data[1] & 0xF0U) | (state->Counter & 0x0FU));
    data[0]        = p02Crc(data, length, config->DataIDList[state->Counter]);
    return E2E_E_OK;
}

E2E_ReturnType E2E_P02CheckInit(E2E_P02CheckStateType* state)
{
    if (state == NULL)
    {
        return E2E_E_INPUTERR_NULL;
    }
    state->LastValidCounter           = 0U;
    state->MaxDeltaCounter            = 0U;
    state->WaitForFirstData           = true;
    state->LostData                   = 0U;
    state->Status                     = E2E_P02STATUS_NONEWDATA;
    state->NoNewOrRepeatedDataCounter = 0U;
    state->SyncCounter                = 0U;
    return E2E_E_OK;
}

E2E_ReturnType E2E_P02Check(
    E2E_P02ConfigType const* config, E2E_P02CheckStateType* state, uint8_t const* data, uint16_t length)
{
    if ((config == NULL) || (state == NULL))
    {
        return E2E_E_INPUTERR_NULL;
    }
    bool const newData = data != NULL;
    if ((E2E_P02CheckConfig(config) != E2E_E_OK) || (newData != (length != 0U))
        || (newData && (length != (config->DataLength / 8U))))
    {
        return E2E_E_INPUTERR_WRONG;
    }

    if (state->MaxDeltaCounter < P02_MAX_DELTA_CAP)
    {
        ++state->MaxDeltaCounter;
    }
    if (!newData)
    {
        e2e_legacyCountNoNewOrRepeated(&state->NoNewOrRepeatedDataCounter, P02_COUNTER_MODULO);
        state->Status = E2E_P02STATUS_NONEWDATA;
        return E2E_E_OK;
    }

    uint8_t const receivedCounter = (uint8_t)(data[1] & 0x0FU);
    if (data[0] != p02Crc(data, length, config->DataIDList[receivedCounter]))
    {
        state->Status = E2E_P02STATUS_WRONGCRC;
    }
    else if (state->WaitForFirstData)
    {
        state->WaitForFirstData = false;
        state->MaxDeltaCounter  = config->MaxDeltaCounterInit;
        state->LastValidCounter = receivedCounter;
        state->Status           = E2E_P02STATUS_INITIAL;
    }
    else
    {
        e2e_LegacyState const legacyState = {&state->LastValidCounter,
                                             &state->MaxDeltaCounter,
                                             &state->LostData,
                                             &state->SyncCounter,
                                             &state->NoNewOrRepeatedDataCounter};
        e2e_LegacyConfig const legacyConfig
            = {config->MaxDeltaCounterInit, config->MaxNoNewOrRepeatedData, config->SyncCounterInit};
        state->Status = e2e_legacyProcessCounter(&legacyState, &legacyConfig, receivedCounter, P02_COUNTER_MODULO);
    }
    return E2E_E_OK;
}

E2E_PCheckStatusType
E2E_P02MapStatusToSM(E2E_ReturnType checkReturn, E2E_P02CheckStatusType status, bool profileBehavior)
{
    if (checkReturn != E2E_E_OK)
    {
        return E2E_P_ERROR;
    }
    switch (status)
    {
        case E2E_P02STATUS_OK:
        case E2E_P02STATUS_OKSOMELOST: return E2E_P_OK;
        case E2E_P02STATUS_WRONGCRC: return E2E_P_ERROR;
        case E2E_P02STATUS_REPEATED: return E2E_P_REPEATED;
        case E2E_P02STATUS_NONEWDATA: return E2E_P_NONEWDATA;
        case E2E_P02STATUS_WRONGSEQUENCE: return E2E_P_WRONGSEQUENCE;
        case E2E_P02STATUS_SYNC: return profileBehavior ? E2E_P_OK : E2E_P_WRONGSEQUENCE;
        case E2E_P02STATUS_INITIAL: return profileBehavior ? E2E_P_WRONGSEQUENCE : E2E_P_OK;
        default: return E2E_P_ERROR;
    }
}
