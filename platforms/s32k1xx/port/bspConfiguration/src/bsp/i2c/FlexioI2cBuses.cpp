// SPDX-License-Identifier: Apache-2.0
// IO-28: one FlexIO I2C master per used board I2C bus, configured from the generated bsp/i2c/i2cConfiguration.h
// (board pins, project rate and time limit, shifters and timers from tools/nxconfig/i2c.py). Pins are muxed by
// staticBsp (ioConfiguration.h), the FlexIO clock gate by the clock driver (boardClock.h); init() runs at the
// first use of a bus, in the task that uses it. Reached only through FlexioI2cBridge.h (see there why).

#include "FlexioI2cBridge.h"

#include "bsp/clock/boardClock.h"
#include "bsp/i2c/i2cConfiguration.h"
#include "i2c/FlexioI2cMaster.h"
#include "mcu/mcu.h"

namespace bsp
{
namespace i2c
{
namespace flexiobridge
{
namespace
{
static_assert(static_cast<uint8_t>(I2cResult::OK) == RESULT_OK, "numbering of I2cResult");
static_assert(static_cast<uint8_t>(I2cResult::NOT_INITIALIZED) == RESULT_NOT_INITIALIZED, "numbering of I2cResult");
static_assert(static_cast<uint8_t>(I2cResult::INVALID_ARGUMENT) == RESULT_INVALID_ARGUMENT, "numbering of I2cResult");
static_assert(static_cast<uint8_t>(I2cResult::BUS_BUSY) == RESULT_BUS_BUSY, "numbering of I2cResult");
static_assert(static_cast<uint8_t>(I2cResult::ADDRESS_NACK) == RESULT_ADDRESS_NACK, "numbering of I2cResult");
static_assert(static_cast<uint8_t>(I2cResult::DATA_NACK) == RESULT_DATA_NACK, "numbering of I2cResult");
static_assert(static_cast<uint8_t>(I2cResult::TIMEOUT) == RESULT_TIMEOUT, "numbering of I2cResult");
static_assert(static_cast<uint8_t>(I2cResult::BUS_ERROR) == RESULT_BUS_ERROR, "numbering of I2cResult");

// The generator planned the divider with the clock of the clock plan; the image must run the FlexIO at that clock.
#define NX_I2C_CLOCK_CHECK(bus, index, regs, clockHz, baud, timeoutUs, sda, scl, tx, rx, timer, label) \
    static_assert(NX_I2C_##bus##_CLOCK_HZ == (clockHz), "FlexIO clock of I2C " #bus " differs from the plan");
NX_I2C_BUSES(NX_I2C_CLOCK_CHECK)
#undef NX_I2C_CLOCK_CHECK

#define NX_I2C_CONFIG(bus, index, regs, clockHz, baud, timeoutUs, sda, scl, tx, rx, timer, label) \
    {(regs), (clockHz), (baud), (timeoutUs), (sda), (scl), (tx), (rx), (timer)},
// <registers> is IP_<instance> of the device header (FLEXIO_Type* at a fixed address): the cast sits in the vendor
// macro, approved deviation DEV-002 (tools/misra/deviations.yaml) - IP_ named on the line for its pattern
FlexioI2cConfig const configs[] = {NX_I2C_BUSES(NX_I2C_CONFIG)}; // IP_<instance>
#undef NX_I2C_CONFIG

#define NX_I2C_MASTER(bus, index, regs, clockHz, baud, timeoutUs, sda, scl, tx, rx, timer, label) \
    FlexioI2cMaster(configs[(index)]),
FlexioI2cMaster masters[] = {NX_I2C_BUSES(NX_I2C_MASTER)};
#undef NX_I2C_MASTER

constexpr size_t BUS_COUNT = sizeof(configs) / sizeof(configs[0]);
static_assert(BUS_COUNT == NX_I2C_BUS_COUNT, "one master per generated I2C bus");
} // namespace

bool init(size_t const index) { return (index < BUS_COUNT) && masters[index].init(); }

uint8_t write(size_t const index, uint8_t const address, uint8_t const* const data, size_t const length,
              bool const sendStop)
{
    return (index < BUS_COUNT) ? static_cast<uint8_t>(masters[index].write(address, data, length, sendStop))
                               : RESULT_NOT_INITIALIZED;
}

uint8_t read(size_t const index, uint8_t const address, uint8_t* const data, size_t const length,
             bool const sendStop)
{
    return (index < BUS_COUNT) ? static_cast<uint8_t>(masters[index].read(address, data, length, sendStop))
                               : RESULT_NOT_INITIALIZED;
}

size_t maxBytesPerTransfer() { return flexio::MAX_BYTES_PER_TRANSFER; }

uint32_t baudrateHz(size_t const index) { return (index < BUS_COUNT) ? masters[index].getBaudrateHz() : 0U; }

uint32_t errorCount(size_t const index) { return (index < BUS_COUNT) ? masters[index].getErrorCount() : 0U; }

} // namespace flexiobridge
} // namespace i2c
} // namespace bsp
