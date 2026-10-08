// SPDX-License-Identifier: Apache-2.0
// IO-28: console group "i2c" (I2cCommand.h). Parsing and printing only; the transfers are the contract helpers of
// libs/bsp/bspI2c (i2c/I2cTransfer.h) on the buses of I2cBuses.h.

#include "bsp/i2c/I2cCommand.h"

#include "bsp/i2c/I2cBuses.h"
#include "i2c/I2cTransfer.h"
#include "util/command/CommandContext.h"
#include "util/format/SharedStringWriter.h"

#include <cstddef>
#include <cstdint>

namespace bsp
{
namespace i2c
{
namespace
{
using ::util::command::CommandContext;

enum Id : uint8_t
{
    ID_BUSES,
    ID_PROBE,
    ID_SCAN,
    ID_READ,
    ID_WRITE
};

// More than any implementation accepts in one call (FlexIO: 13), so the transfer itself reports the limit.
constexpr size_t MAX_BYTES = 32U;

/** Bus named by the next token; nullptr (and BAD_VALUE) if there is no such bus. */
II2cMaster* scanBus(CommandContext& context, size_t& index)
{
    ::util::string::ConstString const name = context.scanIdentifierToken();
    index = busIndex(name.data(), name.length());
    II2cMaster* const master = bus(index);
    (void)context.check(master != nullptr);
    return master;
}

bool scanByte(CommandContext& context, uint32_t const max, uint8_t& value)
{
    uint32_t const v = context.scanIntToken<uint32_t>();
    value            = static_cast<uint8_t>(v);
    return context.check(v <= max);
}

#ifndef NX_PRODUCT_BUILD // SYS-119: rd and wr write any bytes to any device of a bus, not in the product image
/** Remaining tokens as bytes; false (BAD_VALUE) if one is above 0xFF or there are more than MAX_BYTES. */
bool scanBytes(CommandContext& context, uint8_t (&data)[MAX_BYTES], size_t& length)
{
    length = 0U;
    while (context.hasToken())
    {
        if (!context.check(length < MAX_BYTES) || !scanByte(context, 0xFFU, data[length]))
        {
            return false;
        }
        ++length;
    }
    return true;
}
#endif

// SharedStringWriter::printf is variadic by design.
// NOLINTBEGIN(cppcoreguidelines-pro-type-vararg)
#ifndef NX_PRODUCT_BUILD // SYS-119
void printBytes(::util::format::SharedStringWriter& out, uint8_t const* const data, size_t const length)
{
    for (size_t i = 0U; i < length; ++i)
    {
        (void)out.printf(" %02x", static_cast<unsigned>(data[i]));
    }
    (void)out.printf("\r\n");
}
#endif

void buses(CommandContext& context)
{
    ::util::format::SharedStringWriter out(context);
    for (size_t i = 0U; i < busCount(); ++i)
    {
        (void)out.printf(
            "%u: %s, at most %u Hz, SCL %u Hz%s, %u errors\r\n",
            static_cast<unsigned>(i),
            busLabel(i),
            static_cast<unsigned>(busRequestedBaudrateHz(i)),
            static_cast<unsigned>(busBaudrateHz(i)),
            (busBaudrateHz(i) == 0U) ? " (not set up yet)" : "",
            static_cast<unsigned>(busErrorCount(i)));
    }
}

void probeCommand(CommandContext& context)
{
    size_t index            = 0U;
    II2cMaster* const master = scanBus(context, index);
    uint8_t address         = 0U;
    if ((master == nullptr) || !scanByte(context, MAX_ADDRESS, address) || !context.checkEol())
    {
        return;
    }
    I2cResult const result = probe(*master, address);
    ::util::format::SharedStringWriter out(context);
    (void)out.printf("0x%02x: %s\r\n", static_cast<unsigned>(address), resultName(result));
}

void scanCommand(CommandContext& context)
{
    size_t index            = 0U;
    II2cMaster* const master = scanBus(context, index);
    if ((master == nullptr) || !context.checkEol())
    {
        return;
    }
    ScanResult const result = scan(*master);
    ::util::format::SharedStringWriter out(context);
    (void)out.printf("%s:", busLabel(index));
    for (uint32_t a = FIRST_DEVICE_ADDRESS; a <= LAST_DEVICE_ADDRESS; ++a)
    {
        if (result.contains(static_cast<uint8_t>(a)))
        {
            (void)out.printf(" 0x%02x", static_cast<unsigned>(a));
        }
    }
    (void)out.printf(" - %u device(s)\r\n", static_cast<unsigned>(result.count));
    if (result.stop != I2cResult::OK)
    {
        (void)out.printf(
            "scan stopped at 0x%02x: %s\r\n", static_cast<unsigned>(result.stopAddress), resultName(result.stop));
    }
}

#ifndef NX_PRODUCT_BUILD // SYS-119
void readCommand(CommandContext& context)
{
    size_t index            = 0U;
    II2cMaster* const master = scanBus(context, index);
    uint8_t address         = 0U;
    uint8_t count           = 0U;
    uint8_t tx[MAX_BYTES]   = {};
    size_t txLength         = 0U;
    if ((master == nullptr) || !scanByte(context, MAX_ADDRESS, address)
        || !scanByte(context, static_cast<uint32_t>(MAX_BYTES), count) || !scanBytes(context, tx, txLength))
    {
        return;
    }
    uint8_t rx[MAX_BYTES]  = {};
    I2cResult const result = writeRead(*master, address, tx, txLength, rx, count);
    ::util::format::SharedStringWriter out(context);
    (void)out.printf("0x%02x: %s", static_cast<unsigned>(address), resultName(result));
    if (result == I2cResult::OK)
    {
        (void)out.printf(",");
        printBytes(out, rx, count);
    }
    else if (result == I2cResult::INVALID_ARGUMENT)
    {
        (void)out.printf(" (1..%u bytes per call)\r\n", static_cast<unsigned>(master->maxBytesPerTransfer()));
    }
    else
    {
        (void)out.printf("\r\n");
    }
}

void writeCommand(CommandContext& context)
{
    size_t index            = 0U;
    II2cMaster* const master = scanBus(context, index);
    uint8_t address         = 0U;
    uint8_t tx[MAX_BYTES]   = {};
    size_t txLength         = 0U;
    if ((master == nullptr) || !scanByte(context, MAX_ADDRESS, address) || !scanBytes(context, tx, txLength))
    {
        return;
    }
    I2cResult const result = master->write(address, tx, txLength, true);
    ::util::format::SharedStringWriter out(context);
    (void)out.printf(
        "0x%02x: %s, %u byte(s)\r\n", static_cast<unsigned>(address), resultName(result),
        static_cast<unsigned>(txLength));
}
#endif
// NOLINTEND(cppcoreguidelines-pro-type-vararg)
} // namespace

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(I2cCommand, "i2c", "I2C buses of the project (use.i2c)")
COMMAND_GROUP_COMMAND(ID_BUSES, "buses", "buses, SCL rate, error counter")
COMMAND_GROUP_COMMAND(ID_PROBE, "probe", "address byte with STOP (param: <bus> <addr>)")
COMMAND_GROUP_COMMAND(ID_SCAN, "scan", "probe 0x08..0x77 (param: <bus>)")
#ifndef NX_PRODUCT_BUILD // SYS-119
COMMAND_GROUP_COMMAND(
    ID_READ, "rd", "write the bytes, repeated START, read n (param: <bus> <addr> <n> [<byte>...])")
COMMAND_GROUP_COMMAND(
    ID_WRITE,
    "wr",
    "write the bytes with STOP (param: <bus> <addr> [<byte>...]), only to a device whose datasheet allows it "
    "(board rule)")
#endif
DEFINE_COMMAND_GROUP_GET_INFO_END;

void I2cCommand::executeCommand(CommandContext& context, uint8_t const idx)
{
    switch (idx)
    {
        case ID_BUSES: buses(context); break;
        case ID_PROBE: probeCommand(context); break;
        case ID_SCAN: scanCommand(context); break;
#ifndef NX_PRODUCT_BUILD // SYS-119
        case ID_READ: readCommand(context); break;
        case ID_WRITE:
        default: writeCommand(context); break;
#else
        default: break;
#endif
    }
}

} // namespace i2c
} // namespace bsp
