// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "i2c/II2cMaster.h"

#include <cstddef>
#include <cstdint>

namespace bsp
{
namespace i2c
{

/** Lowest and highest 7-bit address of an ordinary device; 0x00-0x07 and 0x78-0x7F are reserved by the I2C bus
 *  specification (general call, START byte, CBUS, other bus formats, 10-bit addressing, future purposes). */
constexpr uint8_t FIRST_DEVICE_ADDRESS = 0x08U;
constexpr uint8_t LAST_DEVICE_ADDRESS  = 0x77U;
constexpr uint8_t MAX_ADDRESS          = 0x7FU;

inline char const* resultName(I2cResult const result)
{
    switch (result)
    {
        case I2cResult::OK: return "ok";
        case I2cResult::NOT_INITIALIZED: return "not initialized";
        case I2cResult::INVALID_ARGUMENT: return "invalid argument";
        case I2cResult::BUS_BUSY: return "bus busy";
        case I2cResult::ADDRESS_NACK: return "address nack";
        case I2cResult::DATA_NACK: return "data nack";
        case I2cResult::TIMEOUT: return "timeout";
        case I2cResult::BUS_ERROR:
        default: return "bus error";
    }
}

/** Address byte only, with STOP: OK if a device acknowledged \p address. */
inline I2cResult probe(II2cMaster& bus, uint8_t const address) { return bus.write(address, nullptr, 0U, true); }

/**
 * The usual register read: \p txLength bytes (register address) without STOP, repeated START, \p rxLength bytes
 * with STOP. txLength 0 reads without writing first. If the write part fails the read is not started; the bus is
 * left with a STOP by the implementation (a failed call ends its packet).
 */
inline I2cResult writeRead(
    II2cMaster& bus,
    uint8_t const address,
    uint8_t const* const txData,
    size_t const txLength,
    uint8_t* const rxData,
    size_t const rxLength)
{
    if (txLength > 0U)
    {
        I2cResult const written = bus.write(address, txData, txLength, false);
        if (written != I2cResult::OK)
        {
            return written;
        }
    }
    return bus.read(address, rxData, rxLength, true);
}

/** Addresses that acknowledged a probe, one bit per 7-bit address, and how the scan ended. */
struct ScanResult
{
    uint32_t found[4];   // bit (address % 32) of word (address / 32)
    uint8_t count;       // number of acknowledged addresses
    I2cResult stop;      // OK: every address probed; otherwise the bus fault that ended the scan
    uint8_t stopAddress; // address whose probe returned \p stop (valid if stop is not OK)

    bool contains(uint8_t const address) const
    {
        return (address <= MAX_ADDRESS) && ((found[address / 32U] & (1U << (address % 32U))) != 0U);
    }
};

/**
 * Probes \p first..\p last (clamped to the device addresses FIRST_DEVICE_ADDRESS..LAST_DEVICE_ADDRESS) one by one.
 * ADDRESS_NACK means "no device" and the scan goes on; any other failure (bus busy, timeout, bus error, not
 * initialized) is a bus fault and ends the scan, because every further probe would only repeat it.
 */
inline ScanResult scan(
    II2cMaster& bus, uint8_t first = FIRST_DEVICE_ADDRESS, uint8_t last = LAST_DEVICE_ADDRESS)
{
    ScanResult result = {{0U, 0U, 0U, 0U}, 0U, I2cResult::OK, 0U};
    if (first < FIRST_DEVICE_ADDRESS)
    {
        first = FIRST_DEVICE_ADDRESS;
    }
    if (last > LAST_DEVICE_ADDRESS)
    {
        last = LAST_DEVICE_ADDRESS;
    }
    for (uint32_t address = first; address <= last; ++address)
    {
        uint8_t const a       = static_cast<uint8_t>(address);
        I2cResult const probed = probe(bus, a);
        if (probed == I2cResult::OK)
        {
            result.found[a / 32U] |= (1U << (a % 32U));
            ++result.count;
        }
        else if (probed != I2cResult::ADDRESS_NACK)
        {
            result.stop        = probed;
            result.stopAddress = a;
            break;
        }
    }
    return result;
}

} // namespace i2c
} // namespace bsp
