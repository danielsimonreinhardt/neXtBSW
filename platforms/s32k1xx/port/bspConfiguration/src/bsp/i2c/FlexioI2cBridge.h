// SPDX-License-Identifier: Apache-2.0
#pragma once

// IO-28, transitional: the FlexIO I2C master (platforms/s32k1xx/bsp/bspFlexioI2c) still declares its own
// bsp::i2c::I2cResult with the same enumerators as the contract (libs/bsp/bspI2c, i2c/II2cMaster.h), so no
// translation unit can include both headers. Until FlexioI2cMaster implements II2cMaster itself (proposal in
// doc/changelog.d/IO-28.md), FlexioI2cBuses.cpp owns the masters and I2cBuses.cpp the contract objects; they meet
// only through these functions, with the result as its numeric value (both sides check the numbering).

#include <cstddef>
#include <cstdint>

namespace bsp
{
namespace i2c
{
namespace flexiobridge
{

/** Numeric values of the enumerators, the same on both sides of the bridge. */
constexpr uint8_t RESULT_OK               = 0U;
constexpr uint8_t RESULT_NOT_INITIALIZED  = 1U;
constexpr uint8_t RESULT_INVALID_ARGUMENT = 2U;
constexpr uint8_t RESULT_BUS_BUSY         = 3U;
constexpr uint8_t RESULT_ADDRESS_NACK     = 4U;
constexpr uint8_t RESULT_DATA_NACK        = 5U;
constexpr uint8_t RESULT_TIMEOUT          = 6U;
constexpr uint8_t RESULT_BUS_ERROR        = 7U;

/** FlexioI2cMaster::init() of bus \p index; false if it failed or \p index is no bus. */
bool init(size_t index);
uint8_t write(size_t index, uint8_t address, uint8_t const* data, size_t length, bool sendStop);
uint8_t read(size_t index, uint8_t address, uint8_t* data, size_t length, bool sendStop);
size_t maxBytesPerTransfer();
/** SCL frequency selected by init(), 0 before. */
uint32_t baudrateHz(size_t index);
uint32_t errorCount(size_t index);

} // namespace flexiobridge
} // namespace i2c
} // namespace bsp
