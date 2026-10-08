/* IO-28: host test of the I2C master contract helpers (i2c/I2cTransfer.h) against a fake bus - no hardware.
 *
 * The fake implements II2cMaster with a set of present device addresses, a register pointer per device and a
 * scripted fault (bus busy/timeout/bus error from a given address on, or a failing write). Every call is logged,
 * so the tests check what goes onto the bus (STOP or repeated START, order of write and read), not only results.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Werror \
 *       -Ilibs/bsp/bspI2c/include libs/bsp/bspI2c/test/host/I2cTransferHostTest.cpp -o build/host/i2c_transfer.exe
 *   build/host/i2c_transfer.exe
 */

#include "i2c/I2cTransfer.h"

#include <cstdio>
#include <cstring>
#include <vector>

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

using ::bsp::i2c::I2cResult;

struct Call
{
    bool isRead;
    uint8_t address;
    size_t length;
    bool sendStop;
};

/** Devices with 16 registers each; a write sets the register pointer (first byte) and stores the rest. */
class FakeBus : public ::bsp::i2c::II2cMaster
{
public:
    bool present[128] = {};
    uint8_t registers[128][16] = {};
    uint8_t pointer[128] = {};
    I2cResult faultFrom      = I2cResult::OK; // returned for every address >= faultAddress
    uint8_t faultAddress     = 0xFFU;
    I2cResult writeFault     = I2cResult::OK; // returned by every write with data
    std::vector<Call> calls;

    I2cResult write(uint8_t const address, uint8_t const* const data, size_t const length, bool const sendStop)
        override
    {
        calls.push_back({false, address, length, sendStop});
        if (address > 0x7FU || (length > 0U && data == nullptr) || length > maxBytesPerTransfer())
        {
            return I2cResult::INVALID_ARGUMENT;
        }
        if ((faultFrom != I2cResult::OK) && (address >= faultAddress))
        {
            return faultFrom;
        }
        if (!present[address])
        {
            return I2cResult::ADDRESS_NACK;
        }
        if ((length > 0U) && (writeFault != I2cResult::OK))
        {
            return writeFault;
        }
        if (length > 0U)
        {
            pointer[address] = static_cast<uint8_t>(data[0] & 0x0FU);
            for (size_t i = 1U; i < length; ++i)
            {
                registers[address][pointer[address]] = data[i];
                pointer[address] = static_cast<uint8_t>((pointer[address] + 1U) & 0x0FU);
            }
        }
        return I2cResult::OK;
    }

    I2cResult read(uint8_t const address, uint8_t* const data, size_t const length, bool const sendStop) override
    {
        calls.push_back({true, address, length, sendStop});
        if (address > 0x7FU || data == nullptr || length == 0U || length > maxBytesPerTransfer())
        {
            return I2cResult::INVALID_ARGUMENT;
        }
        if ((faultFrom != I2cResult::OK) && (address >= faultAddress))
        {
            return faultFrom;
        }
        if (!present[address])
        {
            return I2cResult::ADDRESS_NACK;
        }
        for (size_t i = 0U; i < length; ++i)
        {
            data[i]          = registers[address][pointer[address]];
            pointer[address] = static_cast<uint8_t>((pointer[address] + 1U) & 0x0FU);
        }
        return I2cResult::OK;
    }

    size_t maxBytesPerTransfer() const override { return 13U; }
};

void testResultNames()
{
    I2cResult const all[] = {I2cResult::OK, I2cResult::NOT_INITIALIZED, I2cResult::INVALID_ARGUMENT,
                             I2cResult::BUS_BUSY, I2cResult::ADDRESS_NACK, I2cResult::DATA_NACK,
                             I2cResult::TIMEOUT, I2cResult::BUS_ERROR};
    // numbering is part of the contract (implementations convert by value)
    for (size_t i = 0U; i < sizeof(all) / sizeof(all[0]); ++i)
    {
        CHECK(static_cast<size_t>(all[i]) == i);
        for (size_t j = 0U; j < i; ++j)
        {
            CHECK(std::strcmp(::bsp::i2c::resultName(all[i]), ::bsp::i2c::resultName(all[j])) != 0);
        }
    }
    CHECK(std::strcmp(::bsp::i2c::resultName(I2cResult::ADDRESS_NACK), "address nack") == 0);
    CHECK(std::strcmp(::bsp::i2c::resultName(static_cast<I2cResult>(200)), "bus error") == 0);
}

void testProbe()
{
    FakeBus bus;
    bus.present[0x50] = true;
    CHECK(::bsp::i2c::probe(bus, 0x50U) == I2cResult::OK);
    CHECK(::bsp::i2c::probe(bus, 0x51U) == I2cResult::ADDRESS_NACK);
    // a probe is the address byte alone, with STOP
    CHECK(bus.calls.size() == 2U);
    CHECK(!bus.calls[0].isRead && bus.calls[0].address == 0x50U && bus.calls[0].length == 0U && bus.calls[0].sendStop);
}

void testWriteRead()
{
    FakeBus bus;
    bus.present[0x48] = true;
    for (uint8_t r = 0U; r < 16U; ++r)
    {
        bus.registers[0x48][r] = static_cast<uint8_t>(0xA0U + r);
    }
    uint8_t const reg[] = {0x03U};
    uint8_t rx[4]       = {};
    CHECK(::bsp::i2c::writeRead(bus, 0x48U, reg, 1U, rx, 4U) == I2cResult::OK);
    CHECK(rx[0] == 0xA3U && rx[1] == 0xA4U && rx[2] == 0xA5U && rx[3] == 0xA6U);
    // write without STOP (the next call starts with a repeated START), then read with STOP
    CHECK(bus.calls.size() == 2U);
    CHECK(!bus.calls[0].isRead && bus.calls[0].length == 1U && !bus.calls[0].sendStop);
    CHECK(bus.calls[1].isRead && bus.calls[1].length == 4U && bus.calls[1].sendStop);

    // txLength 0: read only, from the current register pointer
    bus.calls.clear();
    uint8_t one = 0U;
    CHECK(::bsp::i2c::writeRead(bus, 0x48U, nullptr, 0U, &one, 1U) == I2cResult::OK);
    CHECK(one == 0xA7U);
    CHECK(bus.calls.size() == 1U && bus.calls[0].isRead && bus.calls[0].sendStop);

    // a failed write part: no read is started, its result is returned
    bus.calls.clear();
    bus.writeFault = I2cResult::DATA_NACK;
    rx[0]          = 0x55U;
    CHECK(::bsp::i2c::writeRead(bus, 0x48U, reg, 1U, rx, 4U) == I2cResult::DATA_NACK);
    CHECK(bus.calls.size() == 1U && rx[0] == 0x55U);
    bus.calls.clear();
    CHECK(::bsp::i2c::writeRead(bus, 0x49U, reg, 1U, rx, 4U) == I2cResult::ADDRESS_NACK);
    CHECK(bus.calls.size() == 1U);
    // the read part's own limits come from the implementation
    bus.writeFault = I2cResult::OK;
    uint8_t big[14] = {};
    CHECK(::bsp::i2c::writeRead(bus, 0x48U, reg, 1U, big, 14U) == I2cResult::INVALID_ARGUMENT);
}

void testScan()
{
    FakeBus bus;
    uint8_t const devices[] = {0x08U, 0x1FU, 0x20U, 0x50U, 0x77U};
    for (uint8_t a : devices)
    {
        bus.present[a] = true;
    }
    bus.present[0x03] = true; // reserved addresses are never probed
    bus.present[0x78] = true;
    ::bsp::i2c::ScanResult const result = ::bsp::i2c::scan(bus);
    CHECK(result.stop == I2cResult::OK);
    CHECK(result.count == 5U);
    for (uint32_t a = 0U; a < 128U; ++a)
    {
        bool const expected = (a == 0x08U) || (a == 0x1FU) || (a == 0x20U) || (a == 0x50U) || (a == 0x77U);
        CHECK(result.contains(static_cast<uint8_t>(a)) == expected);
    }
    CHECK(!result.contains(0x80U));
    // 0x08..0x77, each once, in order, address byte with STOP
    CHECK(bus.calls.size() == 0x70U);
    bool ordered = true;
    for (size_t i = 0U; i < bus.calls.size(); ++i)
    {
        ordered = ordered && (bus.calls[i].address == 0x08U + i) && !bus.calls[i].isRead
                  && (bus.calls[i].length == 0U) && bus.calls[i].sendStop;
    }
    CHECK(ordered);

    // a window, clamped to the device addresses
    bus.calls.clear();
    ::bsp::i2c::ScanResult const window = ::bsp::i2c::scan(bus, 0x00U, 0x1FU);
    CHECK(window.count == 2U && window.contains(0x08U) && window.contains(0x1FU) && !window.contains(0x03U));
    CHECK(bus.calls.size() == 0x18U && bus.calls.front().address == 0x08U);
    bus.calls.clear();
    CHECK(::bsp::i2c::scan(bus, 0x70U, 0x7FU).count == 1U);
    CHECK(bus.calls.size() == 8U && bus.calls.back().address == 0x77U);
}

void testScanStopsAtBusFaults()
{
    I2cResult const faults[] = {I2cResult::BUS_BUSY, I2cResult::TIMEOUT, I2cResult::BUS_ERROR,
                                I2cResult::NOT_INITIALIZED, I2cResult::DATA_NACK};
    for (I2cResult fault : faults)
    {
        FakeBus bus;
        bus.present[0x10] = true;
        bus.present[0x40] = true;
        bus.faultFrom     = fault;
        bus.faultAddress  = 0x30U;
        ::bsp::i2c::ScanResult const result = ::bsp::i2c::scan(bus);
        CHECK(result.stop == fault);
        CHECK(result.stopAddress == 0x30U);
        CHECK(result.count == 1U && result.contains(0x10U) && !result.contains(0x40U));
        CHECK(bus.calls.size() == (0x30U - 0x08U + 1U)); // nothing probed after the fault
    }
    // an empty bus is no fault
    FakeBus empty;
    ::bsp::i2c::ScanResult const none = ::bsp::i2c::scan(empty);
    CHECK(none.stop == I2cResult::OK && none.count == 0U);
}
} // namespace

int main()
{
    testResultNames();
    testProbe();
    testWriteRead();
    testScan();
    testScanStopsAtBusFaults();
    std::printf("I2cTransferHostTest: %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
