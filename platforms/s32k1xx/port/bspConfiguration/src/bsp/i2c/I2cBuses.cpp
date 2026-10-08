// SPDX-License-Identifier: Apache-2.0
// IO-28: the used I2C buses as contract objects (libs/bsp/bspI2c). Transitional adapter over FlexioI2cBridge.h
// until FlexioI2cMaster implements II2cMaster itself; then the objects of FlexioI2cBuses.cpp take this place.

#include "bsp/i2c/I2cBuses.h"

#include "FlexioI2cBridge.h"
#include "bsp/i2c/i2cConfiguration.h"

namespace bsp
{
namespace i2c
{
namespace
{
namespace bridge = flexiobridge;

static_assert(static_cast<uint8_t>(I2cResult::OK) == bridge::RESULT_OK, "numbering of I2cResult");
static_assert(static_cast<uint8_t>(I2cResult::NOT_INITIALIZED) == bridge::RESULT_NOT_INITIALIZED, "numbering");
static_assert(static_cast<uint8_t>(I2cResult::INVALID_ARGUMENT) == bridge::RESULT_INVALID_ARGUMENT, "numbering");
static_assert(static_cast<uint8_t>(I2cResult::BUS_BUSY) == bridge::RESULT_BUS_BUSY, "numbering of I2cResult");
static_assert(static_cast<uint8_t>(I2cResult::ADDRESS_NACK) == bridge::RESULT_ADDRESS_NACK, "numbering");
static_assert(static_cast<uint8_t>(I2cResult::DATA_NACK) == bridge::RESULT_DATA_NACK, "numbering of I2cResult");
static_assert(static_cast<uint8_t>(I2cResult::TIMEOUT) == bridge::RESULT_TIMEOUT, "numbering of I2cResult");
static_assert(static_cast<uint8_t>(I2cResult::BUS_ERROR) == bridge::RESULT_BUS_ERROR, "numbering of I2cResult");

I2cResult toResult(uint8_t const value)
{
    return (value <= bridge::RESULT_BUS_ERROR) ? static_cast<I2cResult>(value) : I2cResult::BUS_ERROR;
}

/** Bus \p index of the bridge; sets the master up at its first transfer and again after a failed setup. */
class FlexioI2cBus final : public II2cMaster
{
public:
    explicit FlexioI2cBus(size_t const index) : _index(index), _started(false) {}

    I2cResult write(uint8_t const address, uint8_t const* const data, size_t const length, bool const sendStop)
        override
    {
        return start() ? toResult(bridge::write(_index, address, data, length, sendStop))
                       : I2cResult::NOT_INITIALIZED;
    }

    I2cResult read(uint8_t const address, uint8_t* const data, size_t const length, bool const sendStop) override
    {
        return start() ? toResult(bridge::read(_index, address, data, length, sendStop))
                       : I2cResult::NOT_INITIALIZED;
    }

    size_t maxBytesPerTransfer() const override { return bridge::maxBytesPerTransfer(); }

private:
    bool start()
    {
        if (!_started)
        {
            _started = bridge::init(_index);
        }
        return _started;
    }

    size_t _index;
    bool _started;
};

#define NX_I2C_BUS(bus, index, regs, clockHz, baud, timeoutUs, sda, scl, tx, rx, timer, label) FlexioI2cBus(index),
FlexioI2cBus buses[] = {NX_I2C_BUSES(NX_I2C_BUS)};
#undef NX_I2C_BUS

#define NX_I2C_LABEL(bus, index, regs, clockHz, baud, timeoutUs, sda, scl, tx, rx, timer, label) label,
char const* const labels[] = {NX_I2C_BUSES(NX_I2C_LABEL)};
#undef NX_I2C_LABEL

#define NX_I2C_NAME(bus, index, regs, clockHz, baud, timeoutUs, sda, scl, tx, rx, timer, label) #bus,
char const* const names[] = {NX_I2C_BUSES(NX_I2C_NAME)};
#undef NX_I2C_NAME

#define NX_I2C_RATE(bus, index, regs, clockHz, baud, timeoutUs, sda, scl, tx, rx, timer, label) (baud),
uint32_t const requestedRates[] = {NX_I2C_BUSES(NX_I2C_RATE)};
#undef NX_I2C_RATE

constexpr size_t BUS_COUNT = sizeof(buses) / sizeof(buses[0]);
static_assert(BUS_COUNT == NX_I2C_BUS_COUNT, "one contract object per generated I2C bus");

char lower(char const c) { return ((c >= 'A') && (c <= 'Z')) ? static_cast<char>(c - 'A' + 'a') : c; }
} // namespace

size_t busCount() { return BUS_COUNT; }

II2cMaster* bus(size_t const index) { return (index < BUS_COUNT) ? &buses[index] : nullptr; }

size_t busIndex(char const* const name, size_t const length)
{
    for (size_t i = 0U; i < BUS_COUNT; ++i)
    {
        char const* const candidate = names[i];
        size_t n                    = 0U;
        while ((n < length) && (candidate[n] != '\0') && (lower(candidate[n]) == lower(name[n])))
        {
            ++n;
        }
        if ((n == length) && (candidate[n] == '\0'))
        {
            return i;
        }
    }
    return BUS_COUNT;
}

char const* busLabel(size_t const index) { return (index < BUS_COUNT) ? labels[index] : ""; }

uint32_t busRequestedBaudrateHz(size_t const index) { return (index < BUS_COUNT) ? requestedRates[index] : 0U; }

uint32_t busBaudrateHz(size_t const index) { return bridge::baudrateHz(index); }

uint32_t busErrorCount(size_t const index) { return bridge::errorCount(index); }

} // namespace i2c
} // namespace bsp
