#pragma once

#include "bl_config.h"

#include <stdbool.h>
#include <stdint.h>

/* State of the CAN transport (ISO 15765-2, normal addressing, classic CAN), private to isotp.c. Since BL-14 the
 * core reaches it only through bl_transport.h; nothing outside isotp.c declares an isotp_t of its own. */

typedef enum
{
    ISOTP_RX_IDLE,
    ISOTP_RX_RECEIVING,
    ISOTP_RX_COMPLETE
} isotp_rx_state_t;

typedef enum
{
    ISOTP_TX_IDLE,
    ISOTP_TX_WAIT_FC,
    ISOTP_TX_SENDING
} isotp_tx_state_t;

typedef struct
{
    isotp_rx_state_t rxState;
    uint8_t rxBuf[BL_UDS_BUFFER_SIZE];
    uint16_t rxLen;
    uint16_t rxExpected;
    uint8_t rxSn;
    uint8_t rxBlockCount;
    uint32_t rxLastMs;
    bool rxFunctional;

    isotp_tx_state_t txState;
    uint8_t txBuf[BL_UDS_BUFFER_SIZE];
    uint16_t txLen;
    uint16_t txPos;
    uint8_t txSn;
    uint8_t txBlockSize;
    uint8_t txBlockCount;
    uint32_t txStminMs;
    uint32_t txLastMs;
} isotp_t;
