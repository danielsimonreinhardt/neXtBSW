#pragma once

#include <stdint.h>

/* CRC-32 (IEEE 802.3, identical to zlib.crc32). Start with crc = 0 for a new calculation. */
uint32_t crc32_update(uint32_t crc, uint8_t const* data, uint32_t len);
