/**
 * \file E2E.c
 * CRC routines (AUTOSAR_SWS_CRCLibrary 7.2.1) and helpers shared by the profiles.
 */
#include "e2e/E2E.h"

#include "E2E_Internal.h"

#define CRC8_INITIAL_VALUE ((uint8_t)0xFFU)
#define CRC8_XOR_VALUE     ((uint8_t)0xFFU)

static uint8_t crc8Bitwise(uint8_t const* data, uint32_t length, uint8_t startValue, bool isFirstCall, uint8_t poly)
{
    /* Crc_CalculateCRC8 semantics: a subsequent call continues from the previous (already XORed) result */
    uint8_t reg = isFirstCall ? CRC8_INITIAL_VALUE : (uint8_t)(startValue ^ CRC8_XOR_VALUE);
    for (uint32_t i = 0U; i < length; ++i)
    {
        reg = (uint8_t)(reg ^ data[i]);
        for (uint8_t bit = 0U; bit < 8U; ++bit)
        {
            if ((reg & 0x80U) != 0U)
            {
                reg = (uint8_t)((uint8_t)(reg << 1U) ^ poly);
            }
            else
            {
                reg = (uint8_t)(reg << 1U);
            }
        }
    }
    return (uint8_t)(reg ^ CRC8_XOR_VALUE);
}

uint8_t E2E_Crc8(uint8_t const* data, uint32_t length, uint8_t startValue, bool isFirstCall)
{
    return crc8Bitwise(data, length, startValue, isFirstCall, 0x1DU);
}

uint8_t E2E_Crc8H2F(uint8_t const* data, uint32_t length, uint8_t startValue, bool isFirstCall)
{
    return crc8Bitwise(data, length, startValue, isFirstCall, 0x2FU);
}

uint8_t e2e_readNibble(uint8_t const* data, uint16_t bitOffset)
{
    uint8_t const byte = data[bitOffset / 8U];
    return ((bitOffset % 8U) == 0U) ? (uint8_t)(byte & 0x0FU) : (uint8_t)((byte >> 4U) & 0x0FU);
}

void e2e_writeNibble(uint8_t* data, uint16_t bitOffset, uint8_t value)
{
    uint16_t const index = (uint16_t)(bitOffset / 8U);
    if ((bitOffset % 8U) == 0U)
    {
        data[index] = (uint8_t)((data[index] & 0xF0U) | (value & 0x0FU));
    }
    else
    {
        data[index] = (uint8_t)((data[index] & 0x0FU) | ((uint8_t)(value << 4U) & 0xF0U));
    }
}

bool e2e_layoutValid(
    uint16_t dataLength, uint16_t crcOffset, uint16_t counterOffset, bool nibbleUsed, uint16_t nibbleOffset)
{
    uint16_t const bytes = (uint16_t)(dataLength / 8U);
    bool valid = ((dataLength % 8U) == 0U) && (dataLength >= 16U) && (dataLength <= 256U)
                 && ((crcOffset % 8U) == 0U) && ((crcOffset / 8U) < bytes) && ((counterOffset % 4U) == 0U)
                 && ((counterOffset / 8U) < bytes) && ((counterOffset / 8U) != (crcOffset / 8U));
    if (valid && nibbleUsed)
    {
        valid = ((nibbleOffset % 4U) == 0U) && ((nibbleOffset / 8U) < bytes) && ((nibbleOffset / 8U) != (crcOffset / 8U))
                && (nibbleOffset != counterOffset);
    }
    return valid;
}

uint8_t e2e_crcIdAndData(
    uint8_t const* idBytes, uint8_t idCount, uint8_t const* data, uint16_t length, uint16_t crcOffset)
{
    uint16_t const crcByte = (uint16_t)(crcOffset / 8U);
    /* start value 0xFF with isFirstCall = FALSE: register starts at 0x00 (PRS_E2E_00070 note) */
    uint8_t crc = E2E_Crc8(idBytes, idCount, 0xFFU, false);
    if (crcByte > 0U)
    {
        crc = E2E_Crc8(data, crcByte, crc, false);
    }
    if ((uint16_t)(crcByte + 1U) < length)
    {
        crc = E2E_Crc8(&data[crcByte + 1U], (uint32_t)length - crcByte - 1U, crc, false);
    }
    return (uint8_t)(crc ^ 0xFFU);
}

/* status values shared by E2E_P01CheckStatusType and E2E_P02CheckStatusType */
#define LEGACY_STATUS_OK            0x00U
#define LEGACY_STATUS_SYNC          0x03U
#define LEGACY_STATUS_REPEATED      0x08U
#define LEGACY_STATUS_OKSOMELOST    0x20U
#define LEGACY_STATUS_WRONGSEQUENCE 0x40U

void e2e_legacyCountNoNewOrRepeated(uint8_t* counter, uint8_t counterModulo)
{
    if (*counter < (uint8_t)(counterModulo - 1U))
    {
        ++(*counter);
    }
}

uint8_t e2e_legacyProcessCounter(
    e2e_LegacyState const* state, e2e_LegacyConfig const* config, uint8_t receivedCounter, uint8_t counterModulo)
{
    uint8_t const delta = (receivedCounter >= *state->lastValidCounter)
                              ? (uint8_t)(receivedCounter - *state->lastValidCounter)
                              : (uint8_t)(counterModulo + receivedCounter - *state->lastValidCounter);
    uint8_t status;

    if (delta == 0U)
    {
        e2e_legacyCountNoNewOrRepeated(state->noNewOrRepeatedDataCounter, counterModulo);
        status = LEGACY_STATUS_REPEATED;
    }
    else if (delta > *state->maxDeltaCounter)
    {
        *state->noNewOrRepeatedDataCounter = 0U;
        *state->syncCounter                = config->syncCounterInit;
        if (*state->syncCounter > 0U)
        {
            *state->maxDeltaCounter  = config->maxDeltaCounterInit;
            *state->lastValidCounter = receivedCounter;
        }
        status = LEGACY_STATUS_WRONGSEQUENCE;
    }
    else
    {
        *state->maxDeltaCounter  = config->maxDeltaCounterInit;
        *state->lastValidCounter = receivedCounter;
        *state->lostData         = (uint8_t)(delta - 1U);
        if (*state->noNewOrRepeatedDataCounter <= config->maxNoNewOrRepeatedData)
        {
            if (*state->syncCounter > 0U)
            {
                --(*state->syncCounter);
                status = LEGACY_STATUS_SYNC;
            }
            else
            {
                status = (delta == 1U) ? LEGACY_STATUS_OK : LEGACY_STATUS_OKSOMELOST;
            }
        }
        else
        {
            *state->syncCounter = config->syncCounterInit;
            status              = LEGACY_STATUS_SYNC;
        }
        *state->noNewOrRepeatedDataCounter = 0U;
    }
    return status;
}
