/* IO-28: host test of the used I2C buses as contract objects (src/bsp/i2c/I2cBuses.cpp) with a scripted
 * FlexIO bridge (src/bsp/i2c/FlexioI2cBridge.h) and a generated configuration in the form of tools/bswgen
 * (i2cStubs/bsp/i2c/i2cConfiguration.h: two buses, not the eval board).
 *
 * Checked: one object per generated bus in board order, lookup by board name (case-insensitive, exact length),
 * labels and requested rates from the configuration, setup at the first transfer only - and again after a failed
 * setup (NOT_INITIALIZED without a transfer), every result value passed through unchanged and an unknown value as
 * BUS_ERROR, arguments forwarded to the right bus.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Werror \
 *       -Iplatforms/s32k1xx/port/bspConfiguration/test/host/i2cStubs \
 *       -Iplatforms/s32k1xx/port/bspConfiguration/include -Iplatforms/s32k1xx/port/bspConfiguration/src/bsp/i2c \
 *       -Ilibs/bsp/bspI2c/include platforms/s32k1xx/port/bspConfiguration/src/bsp/i2c/I2cBuses.cpp \
 *       platforms/s32k1xx/port/bspConfiguration/test/host/I2cBusesHostTest.cpp -o build/host/i2c_buses.exe
 *   build/host/i2c_buses.exe
 */

#include "FlexioI2cBridge.h"
#include "bsp/i2c/I2cBuses.h"

#include <cstdio>
#include <cstring>

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

struct BridgeBus
{
    int inits        = 0;
    bool initResult  = true;
    uint8_t result   = 0U;
    int writes       = 0;
    int reads        = 0;
    uint8_t address  = 0U;
    size_t length    = 0U;
    bool sendStop    = false;
    void const* data = nullptr;
};

BridgeBus fake[2];
} // namespace

namespace bsp
{
namespace i2c
{
namespace flexiobridge
{
bool init(size_t const index)
{
    ++fake[index].inits;
    return fake[index].initResult;
}

uint8_t write(size_t const index, uint8_t const address, uint8_t const* const data, size_t const length,
              bool const sendStop)
{
    BridgeBus& b = fake[index];
    ++b.writes;
    b.address  = address;
    b.data     = data;
    b.length   = length;
    b.sendStop = sendStop;
    return b.result;
}

uint8_t read(size_t const index, uint8_t const address, uint8_t* const data, size_t const length,
             bool const sendStop)
{
    BridgeBus& b = fake[index];
    ++b.reads;
    b.address  = address;
    b.data     = data;
    b.length   = length;
    b.sendStop = sendStop;
    return b.result;
}

size_t maxBytesPerTransfer() { return 13U; }

uint32_t baudrateHz(size_t const index)
{
    return (fake[index].inits > 0) ? 400000U + static_cast<uint32_t>(index) : 0U;
}

uint32_t errorCount(size_t const index) { return 7U + static_cast<uint32_t>(index); }
} // namespace flexiobridge
} // namespace i2c
} // namespace bsp

namespace
{
using ::bsp::i2c::I2cResult;
using ::bsp::i2c::II2cMaster;

void testTable()
{
    CHECK(::bsp::i2c::busCount() == 2U);
    CHECK(::bsp::i2c::bus(0U) != nullptr && ::bsp::i2c::bus(1U) != nullptr);
    CHECK(::bsp::i2c::bus(0U) != ::bsp::i2c::bus(1U));
    CHECK(::bsp::i2c::bus(2U) == nullptr);
    CHECK(std::strcmp(::bsp::i2c::busLabel(0U), "SENSOR on FlexIO") == 0);
    CHECK(std::strcmp(::bsp::i2c::busLabel(1U), "Eeprom on FlexIO") == 0);
    CHECK(std::strcmp(::bsp::i2c::busLabel(2U), "") == 0);
    CHECK(::bsp::i2c::busRequestedBaudrateHz(0U) == 400000U);
    CHECK(::bsp::i2c::busRequestedBaudrateHz(1U) == 100000U);
    CHECK(::bsp::i2c::busRequestedBaudrateHz(2U) == 0U);
    CHECK(::bsp::i2c::busErrorCount(1U) == 8U);
    CHECK(::bsp::i2c::bus(0U)->maxBytesPerTransfer() == 13U);
}

void testLookup()
{
    CHECK(::bsp::i2c::busIndex("SENSOR", 6U) == 0U);
    CHECK(::bsp::i2c::busIndex("sensor", 6U) == 0U);
    CHECK(::bsp::i2c::busIndex("EEPROM", 6U) == 1U);
    CHECK(::bsp::i2c::busIndex("eeprom x", 6U) == 1U); // only the token length counts
    CHECK(::bsp::i2c::busIndex("SENS", 4U) == 2U);     // no prefix match
    CHECK(::bsp::i2c::busIndex("SENSORS", 7U) == 2U);
    CHECK(::bsp::i2c::busIndex("", 0U) == 2U);
}

void testSetupAtFirstTransfer()
{
    fake[0] = BridgeBus{};
    fake[1] = BridgeBus{};
    II2cMaster& sensor = *::bsp::i2c::bus(0U);
    CHECK(fake[0].inits == 0);                          // nothing touched before the first transfer
    CHECK(::bsp::i2c::busBaudrateHz(0U) == 0U);
    uint8_t const tx[] = {1U, 2U, 3U};
    CHECK(sensor.write(0x50U, tx, 3U, false) == I2cResult::OK);
    CHECK(fake[0].inits == 1 && fake[0].writes == 1);
    CHECK(fake[0].address == 0x50U && fake[0].data == tx && fake[0].length == 3U && !fake[0].sendStop);
    CHECK(fake[1].inits == 0 && fake[1].writes == 0);   // the other bus is untouched
    uint8_t rx[2] = {};
    CHECK(sensor.read(0x51U, rx, 2U, true) == I2cResult::OK);
    CHECK(fake[0].inits == 1 && fake[0].reads == 1);    // set up once
    CHECK(fake[0].address == 0x51U && fake[0].data == rx && fake[0].length == 2U && fake[0].sendStop);
    CHECK(sensor.read(0x52U, rx, 1U, false) == I2cResult::OK); // chained read: no STOP
    CHECK(fake[0].reads == 2 && fake[0].address == 0x52U && fake[0].length == 1U && !fake[0].sendStop);
    CHECK(sensor.write(0x53U, tx, 1U, true) == I2cResult::OK);
    CHECK(fake[0].writes == 2 && fake[0].address == 0x53U && fake[0].sendStop);
    CHECK(::bsp::i2c::busBaudrateHz(0U) == 400000U);
}

void testFailedSetupIsRetried()
{
    fake[1]            = BridgeBus{};
    fake[1].initResult = false;
    II2cMaster& eeprom = *::bsp::i2c::bus(1U);
    uint8_t rx         = 0U;
    CHECK(eeprom.read(0x50U, &rx, 1U, true) == I2cResult::NOT_INITIALIZED);
    CHECK(eeprom.write(0x50U, nullptr, 0U, true) == I2cResult::NOT_INITIALIZED);
    CHECK(fake[1].inits == 2 && fake[1].reads == 0 && fake[1].writes == 0);
    fake[1].initResult = true;
    CHECK(eeprom.write(0x50U, nullptr, 0U, true) == I2cResult::OK);
    CHECK(fake[1].inits == 3 && fake[1].writes == 1);
    CHECK(eeprom.write(0x50U, nullptr, 0U, true) == I2cResult::OK);
    CHECK(fake[1].inits == 3);
}

void testResultsPassThrough()
{
    II2cMaster& sensor = *::bsp::i2c::bus(0U);
    uint8_t rx         = 0U;
    for (uint8_t v = 0U; v <= 7U; ++v)
    {
        fake[0].result = v;
        CHECK(static_cast<uint8_t>(sensor.write(0x10U, nullptr, 0U, true)) == v);
        CHECK(static_cast<uint8_t>(sensor.read(0x10U, &rx, 1U, true)) == v);
    }
    fake[0].result = 8U; // not a value of the contract
    CHECK(sensor.write(0x10U, nullptr, 0U, true) == I2cResult::BUS_ERROR);
    fake[0].result = 0xFFU;
    CHECK(sensor.read(0x10U, &rx, 1U, true) == I2cResult::BUS_ERROR);
    fake[0].result = 0U;
}
} // namespace

int main()
{
    testTable();
    testLookup();
    testSetupAtFirstTransfer();
    testFailedSetupIsRetried();
    testResultsPassThrough();
    std::printf("I2cBusesHostTest: %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
