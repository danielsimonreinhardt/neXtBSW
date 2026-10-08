/**
 * \file E2E_P01.c
 * AUTOSAR E2E Profile 1 (PRS_E2EProtocol R22-11, 6.5; SWS_E2ELibrary R22-11, 8.3.1).
 */
#include "e2e/E2E_P01.h"

#include "E2E_Internal.h"

#include <stddef.h>

#define P01_COUNTER_MODULO     15U
#define P01_MAX_DELTA_CAP      14U

E2E_ReturnType E2E_P01CheckConfig(E2E_P01ConfigType const* config)
{
    if (config == NULL)
    {
        return E2E_E_INPUTERR_NULL;
    }
    bool const nibble = config->DataIDMode == E2E_P01_DATAID_NIBBLE;
    bool valid        = (config->DataIDMode <= E2E_P01_DATAID_NIBBLE)
                 && e2e_layoutValid(
                     config->DataLength, config->CRCOffset, config->CounterOffset, nibble, config->DataIDNibbleOffset);
    if (valid && nibble)
    {
        valid = (config->DataID & 0xF000U) == 0U; /* PRS_E2E_00306 */
    }
    return valid ? E2E_E_OK : E2E_E_INPUTERR_WRONG;
}

/* Data ID part of the CRC (Figure 6.56); counter selects the byte in mode ALT */
static uint8_t p01Crc(E2E_P01ConfigType const* config, uint8_t const* data, uint16_t length, uint8_t counter)
{
    uint8_t id[2];
    uint8_t count = 2U;
    uint8_t const low  = (uint8_t)(config->DataID & 0xFFU);
    uint8_t const high = (uint8_t)(config->DataID >> 8U);
    switch (config->DataIDMode)
    {
        case E2E_P01_DATAID_BOTH:
            id[0] = low;
            id[1] = high;
            break;
        case E2E_P01_DATAID_ALT:
            id[0] = ((counter % 2U) == 0U) ? low : high;
            count = 1U;
            break;
        case E2E_P01_DATAID_LOW:
            id[0] = low;
            count = 1U;
            break;
        default: /* E2E_P01_DATAID_NIBBLE */
            id[0] = low;
            id[1] = 0U;
            break;
    }
    return e2e_crcIdAndData(id, count, data, length, config->CRCOffset);
}

E2E_ReturnType E2E_P01ProtectInit(E2E_P01ProtectStateType* state)
{
    if (state == NULL)
    {
        return E2E_E_INPUTERR_NULL;
    }
    state->Counter = 0U;
    return E2E_E_OK;
}

E2E_ReturnType
E2E_P01Protect(E2E_P01ConfigType const* config, E2E_P01ProtectStateType* state, uint8_t* data, uint16_t length)
{
    if ((config == NULL) || (state == NULL) || (data == NULL))
    {
        return E2E_E_INPUTERR_NULL;
    }
    if ((E2E_P01CheckConfig(config) != E2E_E_OK) || (length != (config->DataLength / 8U))
        || (state->Counter >= P01_COUNTER_MODULO))
    {
        return E2E_E_INPUTERR_WRONG;
    }
    e2e_writeNibble(data, config->CounterOffset, state->Counter);
    if (config->DataIDMode == E2E_P01_DATAID_NIBBLE)
    {
        e2e_writeNibble(data, config->DataIDNibbleOffset, (uint8_t)(config->DataID >> 8U));
    }
    data[config->CRCOffset / 8U] = p01Crc(config, data, length, state->Counter);
    state->Counter               = (uint8_t)((state->Counter + 1U) % P01_COUNTER_MODULO);
    return E2E_E_OK;
}

E2E_ReturnType E2E_P01CheckInit(E2E_P01CheckStateType* state)
{
    if (state == NULL)
    {
        return E2E_E_INPUTERR_NULL;
    }
    state->LastValidCounter           = 0U;
    state->MaxDeltaCounter            = 0U;
    state->WaitForFirstData           = true;
    state->LostData                   = 0U;
    state->Status                     = E2E_P01STATUS_NONEWDATA;
    state->NoNewOrRepeatedDataCounter = 0U;
    state->SyncCounter                = 0U;
    return E2E_E_OK;
}

E2E_ReturnType E2E_P01Check(
    E2E_P01ConfigType const* config, E2E_P01CheckStateType* state, uint8_t const* data, uint16_t length)
{
    if ((config == NULL) || (state == NULL))
    {
        return E2E_E_INPUTERR_NULL;
    }
    bool const newData = data != NULL;
    if ((E2E_P01CheckConfig(config) != E2E_E_OK) || (newData != (length != 0U))
        || (newData && (length != (config->DataLength / 8U))))
    {
        return E2E_E_INPUTERR_WRONG;
    }

    if (state->MaxDeltaCounter < P01_MAX_DELTA_CAP)
    {
        ++state->MaxDeltaCounter;
    }
    if (!newData)
    {
        e2e_legacyCountNoNewOrRepeated(&state->NoNewOrRepeatedDataCounter, P01_COUNTER_MODULO);
        state->Status = E2E_P01STATUS_NONEWDATA;
        return E2E_E_OK;
    }

    uint8_t const receivedCounter = e2e_readNibble(data, config->CounterOffset);
    if (receivedCounter >= P01_COUNTER_MODULO)
    {
        return E2E_E_INPUTERR_WRONG;
    }
    bool dataValid = data[config->CRCOffset / 8U] == p01Crc(config, data, length, receivedCounter);
    if (config->DataIDMode == E2E_P01_DATAID_NIBBLE)
    {
        dataValid = dataValid
                    && (e2e_readNibble(data, config->DataIDNibbleOffset) == ((config->DataID >> 8U) & 0x0FU));
    }

    if (!dataValid)
    {
        state->Status = E2E_P01STATUS_WRONGCRC;
    }
    else if (state->WaitForFirstData)
    {
        state->WaitForFirstData = false;
        state->MaxDeltaCounter  = config->MaxDeltaCounterInit;
        state->LastValidCounter = receivedCounter;
        state->Status           = E2E_P01STATUS_INITIAL;
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
        state->Status = e2e_legacyProcessCounter(&legacyState, &legacyConfig, receivedCounter, P01_COUNTER_MODULO);
    }
    return E2E_E_OK;
}

E2E_PCheckStatusType
E2E_P01MapStatusToSM(E2E_ReturnType checkReturn, E2E_P01CheckStatusType status, bool profileBehavior)
{
    if (checkReturn != E2E_E_OK)
    {
        return E2E_P_ERROR;
    }
    switch (status)
    {
        case E2E_P01STATUS_OK:
        case E2E_P01STATUS_OKSOMELOST: return E2E_P_OK;
        case E2E_P01STATUS_WRONGCRC: return E2E_P_ERROR;
        case E2E_P01STATUS_REPEATED: return E2E_P_REPEATED;
        case E2E_P01STATUS_NONEWDATA: return E2E_P_NONEWDATA;
        case E2E_P01STATUS_WRONGSEQUENCE: return E2E_P_WRONGSEQUENCE;
        case E2E_P01STATUS_SYNC: return profileBehavior ? E2E_P_OK : E2E_P_WRONGSEQUENCE;
        case E2E_P01STATUS_INITIAL: return profileBehavior ? E2E_P_WRONGSEQUENCE : E2E_P_OK;
        default: return E2E_P_ERROR;
    }
}
