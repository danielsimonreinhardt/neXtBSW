// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "i2c/FlexioI2cTiming.h"
#include "mcu/mcu.h"

#include <cstddef>
#include <cstdint>

namespace bsp
{
namespace i2c
{

enum class I2cResult : uint8_t
{
    OK,
    NOT_INITIALIZED,
    INVALID_ARGUMENT, // null buffer, read of 0 bytes, more than MAX_BYTES_PER_TRANSFER, address above 0x7F
    BUS_BUSY,         // SDA or SCL low before START (other master, stuck slave)
    ADDRESS_NACK,     // no device acknowledged the address
    DATA_NACK,        // the device refused a data byte of a write
    TIMEOUT,          // transfer not finished within timeoutUs (e.g. clock stretched too long)
    BUS_ERROR         // SDA read back differs from what was sent (arbitration, short) or the driver fell behind
};

/**
 * Resources of one FlexIO I2C master. Every value comes from configuration: the FlexIO pins from the board (pin
 * mux of SDA/SCL), shifters and timers from the integration (several buses can share one FlexIO as long as their
 * resources differ), the functional clock from the clock model (NX_CLOCK_FLEXIO_HZ), the rate from the project.
 */
struct FlexioI2cConfig
{
    FLEXIO_Type* base;
    uint32_t functionalClockHz; // PCC FlexIO source after its DIV2 divider
    uint32_t baudrateHz;        // upper limit of SCL, the next slower achievable rate is used
    uint32_t timeoutUs;         // per call, includes clock stretching; at least (bytes + 2) * 9 SCL periods
    uint8_t sdaPin;             // FXIO_D<n> of SDA
    uint8_t sclPin;             // FXIO_D<n> of SCL
    uint8_t txShifter;          // transmit shifter, its status flag starts the packet
    uint8_t rxShifter;          // receive shifter (data and ACK/NACK)
    uint8_t sclTimer;           // generates SCL; timer sclTimer + 1 clocks the shifters
};

/**
 * Polling I2C master (7-bit addresses) emulated with FlexIO, S32K1xx Reference Manual Rev. 13, 54.5.5 "I2C Master"
 * (two timers, two shifters, two open-drain pins) and AN12174 chapter 5.
 *
 * - START, repeated START (sendStop false, the next call starts with a repeated START) and STOP
 * - ACK/NACK: each address and write byte is checked, a read acknowledges every byte except the last one
 * - clock stretching: SCL timer reset while the SCL pin differs from its output (TIMRST, 54.5.5), bounded by the
 *   timeout
 * - every written bit is read back through the receive shifter; a difference is reported as BUS_ERROR
 * - at most flexio::MAX_BYTES_PER_TRANSFER data bytes per call (8-bit edge counter of the SCL timer)
 *
 * Not thread safe: all calls must run in one task context. Pin muxing and the FlexIO clock gate are configured
 * elsewhere (IO and clock configuration).
 */
class FlexioI2cMaster
{
public:
    explicit FlexioI2cMaster(FlexioI2cConfig const& config);

    /** Checks the resources against FLEXIO_PARAM, selects the baud rate, enables FlexIO; false if not usable. */
    bool init();

    /** Address byte and \p length data bytes (0 probes the address only). */
    I2cResult write(uint8_t address, uint8_t const* data, size_t length, bool sendStop = true);
    /** Address byte, then \p length (1..MAX_BYTES_PER_TRANSFER) bytes into \p data; the last one is NACKed. */
    I2cResult read(uint8_t address, uint8_t* data, size_t length, bool sendStop = true);
    /** write() without STOP, repeated START, read() with STOP - the usual register read. */
    I2cResult writeRead(
        uint8_t address, uint8_t const* txData, size_t txLength, uint8_t* rxData, size_t rxLength);

    uint32_t getBaudrateHz() const { return _baudrateHz; }
    uint32_t getErrorCount() const { return _errorCount; }

private:
    I2cResult transfer(uint8_t address, bool isRead, uint8_t const* txData, uint8_t* rxData, size_t length,
                       bool sendStop);
    void configurePacket(uint32_t words) const;
    void setTxStopBit(bool high) const;
    void releaseResources() const;
    void clearFlags() const;
    bool isPinHigh(uint8_t pin) const;
    void waitUs(uint32_t durationUs) const;
    I2cResult finishPacket(uint32_t startUs, bool sendStop);
    I2cResult abortPacket(I2cResult reason);

    FlexioI2cConfig const& _config;
    uint32_t _baudrateHz;
    uint32_t _errorCount;
    uint8_t _baudCompare;
    bool _initialized;
};

} // namespace i2c
} // namespace bsp
