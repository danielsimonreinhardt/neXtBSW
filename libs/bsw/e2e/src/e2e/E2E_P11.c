/**
 * \file E2E_P11.c
 * AUTOSAR E2E Profile 11 (PRS_E2EProtocol R22-11, 6.12; SWS_E2ELibrary R22-11, 8.3.11).
 */
#include "e2e/E2E_P11.h"

#include "E2E_Internal.h"

#include <stddef.h>

#define P11_COUNTER_MODULO 15U

E2E_ReturnType E2E_P11CheckConfig(E2E_P11ConfigType const* config)
{
    if (config == NULL)
    {
        return E2E_E_INPUTERR_NULL;
    }
    bool const nibble = config->DataIDMode == E2E_P11_DATAID_NIBBLE;
    bool const valid  = (nibble || (config->DataIDMode == E2E_P11_DATAID_BOTH))
                       && e2e_layoutValid(
                           config->DataLength, config->CRCOffset, config->CounterOffset, nibble, config->DataIDNibbleOffset)
                       && (!nibble || ((config->DataID & 0xF000U) == 0U));
    return valid ? E2E_E_OK : E2E_E_INPUTERR_WRONG;
}

/* Figure 6.98 */
static uint8_t p11Crc(E2E_P11ConfigType const* config, uint8_t const* data, uint16_t length)
{
    uint8_t const id[2] = {(uint8_t)(config->DataID & 0xFFU),
                           (config->DataIDMode == E2E_P11_DATAID_NIBBLE) ? 0U : (uint8_t)(config->DataID >> 8U)};
    return e2e_crcIdAndData(id, 2U, data, length, config->CRCOffset);
}

E2E_ReturnType E2E_P11ProtectInit(E2E_P11ProtectStateType* state)
{
    if (state == NULL)
    {
        return E2E_E_INPUTERR_NULL;
    }
    state->Counter = 0U;
    return E2E_E_OK;
}

E2E_ReturnType
E2E_P11Protect(E2E_P11ConfigType const* config, E2E_P11ProtectStateType* state, uint8_t* data, uint16_t length)
{
    if ((config == NULL) || (state == NULL) || (data == NULL))
    {
        return E2E_E_INPUTERR_NULL;
    }
    if ((E2E_P11CheckConfig(config) != E2E_E_OK) || (length != (config->DataLength / 8U))
        || (state->Counter >= P11_COUNTER_MODULO))
    {
        return E2E_E_INPUTERR_WRONG;
    }
    if (config->DataIDMode == E2E_P11_DATAID_NIBBLE)
    {
        e2e_writeNibble(data, config->DataIDNibbleOffset, (uint8_t)(config->DataID >> 8U));
    }
    e2e_writeNibble(data, config->CounterOffset, state->Counter);
    data[config->CRCOffset / 8U] = p11Crc(config, data, length);
    state->Counter               = (uint8_t)((state->Counter + 1U) % P11_COUNTER_MODULO);
    return E2E_E_OK;
}

E2E_ReturnType E2E_P11CheckInit(E2E_P11CheckStateType* state)
{
    if (state == NULL)
    {
        return E2E_E_INPUTERR_NULL;
    }
    state->Counter = 0x0EU; /* SWS_E2E_00556 */
    state->Status  = E2E_P11STATUS_ERROR;
    return E2E_E_OK;
}

E2E_ReturnType E2E_P11Check(
    E2E_P11ConfigType const* config, E2E_P11CheckStateType* state, uint8_t const* data, uint16_t length)
{
    if ((config == NULL) || (state == NULL))
    {
        return E2E_E_INPUTERR_NULL;
    }
    bool const newData = data != NULL;
    if ((E2E_P11CheckConfig(config) != E2E_E_OK) || (newData != (length != 0U))
        || (newData && (length != (config->DataLength / 8U))))
    {
        return E2E_E_INPUTERR_WRONG;
    }
    if (!newData)
    {
        state->Status = E2E_P11STATUS_NONEWDATA;
        return E2E_E_OK;
    }

    uint8_t const receivedCounter = e2e_readNibble(data, config->CounterOffset);
    bool dataValid                = data[config->CRCOffset / 8U] == p11Crc(config, data, length);
    if (config->DataIDMode == E2E_P11_DATAID_NIBBLE)
    {
        dataValid = dataValid
                    && (e2e_readNibble(data, config->DataIDNibbleOffset) == ((config->DataID >> 8U) & 0x0FU));
    }
    if (!dataValid)
    {
        state->Status = E2E_P11STATUS_ERROR;
        return E2E_E_OK;
    }

    /* Figure 6.111: counter difference with wrap-around after 0x0E */
    uint8_t const delta = (receivedCounter >= state->Counter)
                              ? (uint8_t)(receivedCounter - state->Counter)
                              : (uint8_t)(P11_COUNTER_MODULO + receivedCounter - state->Counter);
    if (delta > config->MaxDeltaCounter)
    {
        state->Status = E2E_P11STATUS_WRONGSEQUENCE;
    }
    else if (delta == 0U)
    {
        state->Status = E2E_P11STATUS_REPEATED;
    }
    else
    {
        state->Status = (delta == 1U) ? E2E_P11STATUS_OK : E2E_P11STATUS_OKSOMELOST;
    }
    state->Counter = receivedCounter;
    return E2E_E_OK;
}

E2E_PCheckStatusType E2E_P11MapStatusToSM(E2E_ReturnType checkReturn, E2E_P11CheckStatusType status)
{
    if (checkReturn != E2E_E_OK)
    {
        return E2E_P_ERROR;
    }
    switch (status)
    {
        case E2E_P11STATUS_OK:
        case E2E_P11STATUS_OKSOMELOST: return E2E_P_OK;
        case E2E_P11STATUS_REPEATED: return E2E_P_REPEATED;
        case E2E_P11STATUS_NONEWDATA: return E2E_P_NONEWDATA;
        case E2E_P11STATUS_WRONGSEQUENCE: return E2E_P_WRONGSEQUENCE;
        default: return E2E_P_ERROR; /* E2E_P11STATUS_ERROR and invalid values */
    }
}
