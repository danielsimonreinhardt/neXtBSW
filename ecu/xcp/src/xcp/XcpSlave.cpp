#include "xcp/XcpSlave.h"

namespace xcp
{
namespace
{
constexpr uint8_t RESOURCE_CAL_PAG             = 0x01U; // calibration and paging, no DAQ/STIM/PGM
constexpr uint8_t COMM_MODE_BASIC_BIG_ENDIAN   = 0x01U; // bit 0 BYTE_ORDER; bits 1..2 granularity 0 = byte
constexpr uint8_t COMM_MODE_BASIC_OPTIONAL     = 0x80U; // GET_COMM_MODE_INFO is available
constexpr uint8_t COMM_MODE_OPTIONAL_MASTER_BM = 0x01U; // master block mode (DOWNLOAD_NEXT)
constexpr uint8_t PROTOCOL_LAYER_VERSION       = 0x01U;
constexpr uint8_t TRANSPORT_LAYER_VERSION      = 0x01U;
constexpr uint8_t DRIVER_VERSION               = 0x10U; // 1.0 of this implementation
constexpr uint8_t CAL_PAGE_MODE_ECU            = 0x01U;
constexpr uint8_t CAL_PAGE_MODE_XCP            = 0x02U;
constexpr uint8_t CAL_PAGE_MODE_ALL            = 0x80U;
} // namespace

bool findReadOnly(
    uint32_t const address, uint32_t const base, uint8_t const* const data, uint32_t const size, Region& region)
{
    // not writable: the engine never writes through data, so dropping const here is safe
    region = {base, const_cast<uint8_t*>(data), size, 0U, false};
    return (size != 0U) && (address >= base) && ((address - base) < size);
}

XcpSlave::XcpSlave(
    IMemoryMap const& map, IMemoryAccess& memory, ITransmitter& transmitter, ByteOrder const byteOrder)
: _map(map)
, _memory(memory)
, _transmitter(transmitter)
, _byteOrder(byteOrder)
, _mta(0U)
, _connected(false)
, _staging()
, _writeTarget(nullptr)
, _writeLength(0U)
, _blockReceived(0U)
, _blockActive(false)
, _writePending(false)
, _counters()
{}

void XcpSlave::reset()
{
    _connected   = false;
    _blockActive = false;
    _mta         = 0U;
}

void XcpSlave::receive(uint8_t const* const packet, uint8_t const length)
{
    if ((packet == nullptr) || (length == 0U))
    {
        return;
    }
    // Disconnected: only CONNECT is answered, everything else stays silent.
    if (!_connected && (packet[0] != CMD_CONNECT))
    {
        return;
    }
    ++_counters.commands;
    if (_writePending)
    {
        // one command at a time: the answer to the pending write is still to come
        ++_counters.busy;
        sendError(ERR_CMD_BUSY);
        return;
    }
    if (_blockActive && (packet[0] != CMD_DOWNLOAD_NEXT))
    {
        _blockActive = false; // block abandoned by the master (e.g. SYNCH after a timeout)
    }
    process(packet, length);
}

void XcpSlave::process(uint8_t const* const packet, uint8_t const length)
{
    switch (packet[0])
    {
        case CMD_CONNECT: connect(packet, length); break;
        case CMD_DISCONNECT:
        {
            reset();
            sendPositive(nullptr, 0U);
            break;
        }
        case CMD_GET_STATUS:
        {
            // session status, resource protection (nothing locked), reserved, session configuration id
            uint8_t response[5] = {0U, 0U, 0U, 0U, 0U};
            putWord(&response[3], 0U);
            sendPositive(response, sizeof(response));
            break;
        }
        case CMD_SYNCH: sendError(ERR_CMD_SYNCH); break;
        case CMD_GET_COMM_MODE_INFO:
        {
            uint8_t const response[7]
                = {0U, COMM_MODE_OPTIONAL_MASTER_BM, 0U, MAX_BS, 0U /* MIN_ST */, 0U /* QUEUE_SIZE */, DRIVER_VERSION};
            sendPositive(response, sizeof(response));
            break;
        }
        case CMD_GET_ID:
        {
            if (length < 2U)
            {
                sendError(ERR_CMD_SYNTAX);
                break;
            }
            // no identification is offered: mode 0, length 0
            uint8_t response[7] = {0U, 0U, 0U, 0U, 0U, 0U, 0U};
            putDword(&response[3], 0U);
            sendPositive(response, sizeof(response));
            break;
        }
        case CMD_SET_MTA:
        {
            if (length < 8U)
            {
                sendError(ERR_CMD_SYNTAX);
            }
            else if (packet[3] != 0U)
            {
                sendError(ERR_OUT_OF_RANGE); // only address extension 0
            }
            else
            {
                _mta = getDword(&packet[4]);
                sendPositive(nullptr, 0U);
            }
            break;
        }
        case CMD_UPLOAD:
        {
            if (length < 2U)
            {
                sendError(ERR_CMD_SYNTAX);
            }
            else
            {
                upload(_mta, packet[1]);
            }
            break;
        }
        case CMD_SHORT_UPLOAD:
        {
            if (length < 8U)
            {
                sendError(ERR_CMD_SYNTAX);
            }
            else if (packet[3] != 0U)
            {
                sendError(ERR_OUT_OF_RANGE);
            }
            else
            {
                upload(getDword(&packet[4]), packet[1]);
            }
            break;
        }
        case CMD_DOWNLOAD:
        {
            if (length < 2U)
            {
                sendError(ERR_CMD_SYNTAX);
            }
            else
            {
                download(packet, length, 2U, packet[1], true);
            }
            break;
        }
        case CMD_DOWNLOAD_NEXT: downloadNext(packet, length); break;
        case CMD_DOWNLOAD_MAX: download(packet, length, 1U, MAX_CTO - 1U, false); break;
        case CMD_SET_CAL_PAGE:
        {
            if (length < 4U)
            {
                sendError(ERR_CMD_SYNTAX);
            }
            else if ((packet[1] & (CAL_PAGE_MODE_ECU | CAL_PAGE_MODE_XCP | CAL_PAGE_MODE_ALL)) == 0U)
            {
                sendError(ERR_MODE_NOT_VALID);
            }
            else if ((packet[2] != 0U) && ((packet[1] & CAL_PAGE_MODE_ALL) == 0U))
            {
                sendError(ERR_SEGMENT_NOT_VALID);
            }
            else if (packet[3] != 0U)
            {
                sendError(ERR_PAGE_NOT_VALID); // one page: the RAM parameters are the working page
            }
            else
            {
                sendPositive(nullptr, 0U);
            }
            break;
        }
        case CMD_GET_CAL_PAGE:
        {
            if (length < 3U)
            {
                sendError(ERR_CMD_SYNTAX);
            }
            else if ((packet[1] != CAL_PAGE_MODE_ECU) && (packet[1] != CAL_PAGE_MODE_XCP))
            {
                sendError(ERR_MODE_NOT_VALID);
            }
            else if (packet[2] != 0U)
            {
                sendError(ERR_SEGMENT_NOT_VALID);
            }
            else
            {
                uint8_t const response[3] = {0U, 0U, 0U /* page */};
                sendPositive(response, sizeof(response));
            }
            break;
        }
        default: sendError(ERR_CMD_UNKNOWN); break;
    }
}

void XcpSlave::connect(uint8_t const* const packet, uint8_t const length)
{
    if (length < 2U)
    {
        sendError(ERR_CMD_SYNTAX);
        return;
    }
    // mode 0 normal, 1 user defined: both simply connect
    (void)packet;
    _connected   = true;
    _blockActive = false;
    _mta         = 0U;
    uint8_t response[7];
    response[0] = RESOURCE_CAL_PAG;
    response[1] = static_cast<uint8_t>(
        COMM_MODE_BASIC_OPTIONAL
        | ((_byteOrder == ByteOrder::BIG_ENDIAN_ORDER) ? COMM_MODE_BASIC_BIG_ENDIAN : 0U));
    response[2] = MAX_CTO;
    putWord(&response[3], MAX_DTO);
    response[5] = PROTOCOL_LAYER_VERSION;
    response[6] = TRANSPORT_LAYER_VERSION;
    sendPositive(response, sizeof(response));
}

void XcpSlave::upload(uint32_t const address, uint8_t const n)
{
    if ((n == 0U) || (n > (MAX_DTO - 1U)))
    {
        sendError(ERR_OUT_OF_RANGE); // no slave block mode: one response packet
        return;
    }
    Region region{};
    uint8_t const error = checkRead(address, n, region);
    if (error != 0U)
    {
        ++_counters.denied;
        sendError(error);
        return;
    }
    uint8_t data[MAX_DTO - 1U];
    _memory.read(data, region.data + (address - region.address), n);
    _mta = address + n;
    ++_counters.uploads;
    sendPositive(data, n);
}

void XcpSlave::download(
    uint8_t const* const packet,
    uint8_t const length,
    uint8_t const dataOffset,
    uint8_t const n,
    bool const block)
{
    if ((n == 0U) || (n > MAX_DOWNLOAD))
    {
        sendError(ERR_OUT_OF_RANGE);
        return;
    }
    uint8_t const inPacket = static_cast<uint8_t>(MAX_CTO - dataOffset);
    if (!block && (n > inPacket))
    {
        sendError(ERR_OUT_OF_RANGE);
        return;
    }
    uint8_t const first = (n < inPacket) ? n : inPacket;
    if (length < (dataOffset + first))
    {
        sendError(ERR_CMD_SYNTAX);
        return;
    }
    Region region{};
    uint8_t const error = checkWrite(_mta, n, region);
    if (error != 0U)
    {
        ++_counters.denied;
        sendError(error);
        return;
    }
    for (uint8_t i = 0U; i < first; ++i)
    {
        _staging[i] = packet[dataOffset + i];
    }
    _writeTarget   = region.data + (_mta - region.address);
    _writeLength   = n;
    _blockReceived = first;
    if (first == n)
    {
        startWrite();
    }
    else
    {
        _blockActive = true; // no response until the block is complete
    }
}

void XcpSlave::downloadNext(uint8_t const* const packet, uint8_t const length)
{
    if (!_blockActive)
    {
        sendError(ERR_SEQUENCE, 0U);
        return;
    }
    uint16_t const remaining = static_cast<uint16_t>(_writeLength - _blockReceived);
    if ((length < 2U) || (packet[1] != remaining))
    {
        _blockActive = false;
        sendError(ERR_SEQUENCE, static_cast<uint8_t>(remaining));
        return;
    }
    uint8_t const inPacket = MAX_CTO - 2U;
    uint8_t const count    = (remaining < inPacket) ? static_cast<uint8_t>(remaining) : inPacket;
    if (length < (2U + count))
    {
        _blockActive = false;
        sendError(ERR_CMD_SYNTAX);
        return;
    }
    for (uint8_t i = 0U; i < count; ++i)
    {
        _staging[_blockReceived + i] = packet[2U + i];
    }
    _blockReceived = static_cast<uint16_t>(_blockReceived + count);
    if (_blockReceived == _writeLength)
    {
        _blockActive = false;
        startWrite();
    }
}

void XcpSlave::startWrite()
{
    _writePending = true;
    _memory.write(_writeTarget, _staging, _writeLength);
}

void XcpSlave::writeDone(bool const applied)
{
    if (!_writePending)
    {
        return;
    }
    _writePending = false;
    if (applied)
    {
        _mta += _writeLength;
        ++_counters.downloads;
        sendPositive(nullptr, 0U);
    }
    else
    {
        ++_counters.busy;
        sendError(ERR_CMD_BUSY);
    }
}

void XcpSlave::writeRefused(uint8_t const errorCode)
{
    if (!_writePending)
    {
        return;
    }
    _writePending = false;
    ++_counters.refused;
    sendError(errorCode);
}

uint8_t XcpSlave::checkRead(uint32_t const address, uint32_t const n, Region& region) const
{
    if (!_map.find(address, region))
    {
        return ERR_ACCESS_DENIED;
    }
    uint64_t const end       = static_cast<uint64_t>(address) + n;
    uint64_t const regionEnd = static_cast<uint64_t>(region.address) + region.size;
    if ((address < region.address) || (end > regionEnd))
    {
        return ERR_ACCESS_DENIED;
    }
    return 0U;
}

uint8_t XcpSlave::checkWrite(uint32_t const address, uint32_t const n, Region& region) const
{
    uint8_t const error = checkRead(address, n, region);
    if (error != 0U)
    {
        return error;
    }
    if (!region.writable)
    {
        return ERR_WRITE_PROTECTED;
    }
    uint32_t const element = (region.elementSize == 0U) ? 1U : region.elementSize;
    if ((((address - region.address) % element) != 0U) || ((n % element) != 0U))
    {
        return ERR_OUT_OF_RANGE; // part of an element: would leave a mixed value behind
    }
    return 0U;
}

void XcpSlave::sendPositive(uint8_t const* const data, uint8_t const length)
{
    uint8_t packet[MAX_DTO];
    packet[0] = PID_RES;
    for (uint8_t i = 0U; (i < length) && (i < (MAX_DTO - 1U)); ++i)
    {
        packet[1U + i] = data[i];
    }
    _transmitter.send(packet, static_cast<uint8_t>(1U + ((length < MAX_DTO) ? length : (MAX_DTO - 1U))));
}

void XcpSlave::sendError(uint8_t const code)
{
    ++_counters.errors;
    uint8_t const packet[2] = {PID_ERR, code};
    _transmitter.send(packet, sizeof(packet));
}

void XcpSlave::sendError(uint8_t const code, uint8_t const parameter)
{
    ++_counters.errors;
    uint8_t const packet[3] = {PID_ERR, code, parameter};
    _transmitter.send(packet, sizeof(packet));
}

uint32_t XcpSlave::getDword(uint8_t const* const bytes) const
{
    if (_byteOrder == ByteOrder::BIG_ENDIAN_ORDER)
    {
        return (static_cast<uint32_t>(bytes[0]) << 24U) | (static_cast<uint32_t>(bytes[1]) << 16U)
               | (static_cast<uint32_t>(bytes[2]) << 8U) | static_cast<uint32_t>(bytes[3]);
    }
    return (static_cast<uint32_t>(bytes[3]) << 24U) | (static_cast<uint32_t>(bytes[2]) << 16U)
           | (static_cast<uint32_t>(bytes[1]) << 8U) | static_cast<uint32_t>(bytes[0]);
}

void XcpSlave::putWord(uint8_t* const bytes, uint16_t const value) const
{
    uint8_t const high = static_cast<uint8_t>(value >> 8U);
    uint8_t const low  = static_cast<uint8_t>(value);
    bytes[0]           = (_byteOrder == ByteOrder::BIG_ENDIAN_ORDER) ? high : low;
    bytes[1]           = (_byteOrder == ByteOrder::BIG_ENDIAN_ORDER) ? low : high;
}

void XcpSlave::putDword(uint8_t* const bytes, uint32_t const value) const
{
    for (uint8_t i = 0U; i < 4U; ++i)
    {
        uint8_t const shift = static_cast<uint8_t>(
            (_byteOrder == ByteOrder::BIG_ENDIAN_ORDER) ? (8U * (3U - i)) : (8U * i));
        bytes[i] = static_cast<uint8_t>(value >> shift);
    }
}

} // namespace xcp
