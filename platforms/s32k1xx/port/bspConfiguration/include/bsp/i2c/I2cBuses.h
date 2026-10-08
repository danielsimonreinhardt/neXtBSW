// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "i2c/II2cMaster.h"

#include <cstddef>
#include <cstdint>

namespace bsp
{
namespace i2c
{

/**
 * IO-28: the I2C buses the project uses (use.i2c, generated bsp/i2c/i2cConfiguration.h), in board order, each as
 * the contract II2cMaster. On the s32k1xx family every bus is a FlexIO I2C master (platforms/s32k1xx/bsp/
 * bspFlexioI2c); it is set up at its first transfer, in the calling task. Not thread safe: all transfers of one bus
 * must run in one task context (today the console, command "i2c").
 */
size_t busCount();
/** Bus \p index, nullptr if there is none. */
II2cMaster* bus(size_t index);
/** Index of the bus with the board name \p name (case-insensitive), busCount() if there is none. */
size_t busIndex(char const* name, size_t length);
/** "<bus> on <instance>". */
char const* busLabel(size_t index);
/** Requested upper limit of SCL (project i2c.<bus>.baudrate_hz). */
uint32_t busRequestedBaudrateHz(size_t index);
/** SCL frequency the driver selected, 0 until the bus was set up. */
uint32_t busBaudrateHz(size_t index);
/** Transfers that ended in an error since start (driver counter). */
uint32_t busErrorCount(size_t index);

} // namespace i2c
} // namespace bsp
