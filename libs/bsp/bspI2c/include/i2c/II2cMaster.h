// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstddef>
#include <cstdint>

namespace bsp
{
namespace i2c
{

/**
 * Outcome of one I2C transfer. The values and their order are part of the contract: implementations that keep an
 * enumeration of their own convert by value (checked with static_assert there).
 */
enum class I2cResult : uint8_t
{
    OK,
    NOT_INITIALIZED,  // the implementation could not set up the bus (resources, clock, rate)
    INVALID_ARGUMENT, // null buffer, read of 0 bytes, more than maxBytesPerTransfer(), address above 0x7F
    BUS_BUSY,         // SDA or SCL low before START (other master, stuck device)
    ADDRESS_NACK,     // no device acknowledged the address
    DATA_NACK,        // the device refused a data byte of a write
    TIMEOUT,          // transfer not finished in time (e.g. clock stretched too long)
    BUS_ERROR         // what was sent differs from the bus (arbitration, short) or the driver fell behind
};

/**
 * One I2C bus as master with 7-bit addresses. Rate, pins, time limit and resources are fixed by the
 * configuration of the implementation, so device drivers written against it stay board and family independent.
 *
 * A call is one packet: START (or a repeated START after a call with sendStop false), the address byte, the data
 * bytes and, with sendStop, a STOP. Whether calls from several tasks are allowed is up to the implementation.
 */
class II2cMaster
{
public:
    /** Address byte and \p length data bytes; length 0 only probes the address. */
    virtual I2cResult write(uint8_t address, uint8_t const* data, size_t length, bool sendStop) = 0;
    /** Address byte, then \p length bytes (1..maxBytesPerTransfer()); the last one is not acknowledged. */
    virtual I2cResult read(uint8_t address, uint8_t* data, size_t length, bool sendStop) = 0;
    /** Data bytes one write() or read() accepts; longer sequences need several calls with repeated START. */
    virtual size_t maxBytesPerTransfer() const = 0;

protected:
    ~II2cMaster() = default;
};

} // namespace i2c
} // namespace bsp
