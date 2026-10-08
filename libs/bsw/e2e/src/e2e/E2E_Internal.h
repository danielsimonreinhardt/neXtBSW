/**
 * \file E2E_Internal.h
 * Helpers shared by the profiles, not part of the public API.
 */
#ifndef E2E_E2E_INTERNAL_H
#define E2E_E2E_INTERNAL_H

#include "e2e/E2E.h"

/** Reads the nibble at a bit offset (offset % 8 == 0: low nibble of byte offset / 8, otherwise high nibble). */
uint8_t e2e_readNibble(uint8_t const* data, uint16_t bitOffset);

/** Writes the low 4 bits of value to the nibble at a bit offset, the other nibble of the byte is kept. */
void e2e_writeNibble(uint8_t* data, uint16_t bitOffset, uint8_t value);

/** Layout check shared by profiles 1 and 11: offsets inside the data, CRC byte, counter and nibble not overlapping. */
bool e2e_layoutValid(
    uint16_t dataLength, uint16_t crcOffset, uint16_t counterOffset, bool nibbleUsed, uint16_t nibbleOffset);

/**
 * CRC of profiles 1 and 11 (PRS Figures 6.55 and 6.98): CRC8 over the Data ID bytes (idBytes[0..idCount-1]), then
 * over data[0..length-1] without the CRC byte, with the compensation XOR so that start and XOR value are 0x00.
 */
uint8_t e2e_crcIdAndData(
    uint8_t const* idBytes, uint8_t idCount, uint8_t const* data, uint16_t length, uint16_t crcOffset);

/* Receiver state of the legacy profiles 1 and 2 (same members in E2E_P01CheckStateType and E2E_P02CheckStateType) */
typedef struct
{
    uint8_t* lastValidCounter;
    uint8_t* maxDeltaCounter;
    uint8_t* lostData;
    uint8_t* syncCounter;
    uint8_t* noNewOrRepeatedDataCounter;
} e2e_LegacyState;

typedef struct
{
    uint8_t maxDeltaCounterInit;
    uint8_t maxNoNewOrRepeatedData;
    uint8_t syncCounterInit;
} e2e_LegacyConfig;

/**
 * Counter evaluation of profiles 1 and 2 after a valid CRC and not waiting for first data (P01 Figures 6.60-6.63,
 * P02 Figures 6.72-6.76, PRS_E2E_00145-00151, 00298-00301). counterModulo is 15 (P01) or 16 (P02). Returns the
 * detailed status with the (identical) values of E2E_P01CheckStatusType / E2E_P02CheckStatusType.
 */
uint8_t e2e_legacyProcessCounter(
    e2e_LegacyState const* state, e2e_LegacyConfig const* config, uint8_t receivedCounter, uint8_t counterModulo);

/** Increments a legacy no-new-or-repeated counter up to counterModulo - 1. */
void e2e_legacyCountNoNewOrRepeated(uint8_t* counter, uint8_t counterModulo);

#endif /* E2E_E2E_INTERNAL_H */
