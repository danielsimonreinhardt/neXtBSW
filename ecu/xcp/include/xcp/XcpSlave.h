#pragma once

#include <cstddef>
#include <cstdint>

/**
 * XCP slave protocol engine (TOOL-01): command processor for measurement by polling and calibration of RAM
 * parameters, independent of the transport, the RTOS and the target (host tests build it unchanged).
 *
 * Scope: CONNECT, DISCONNECT, GET_STATUS, SYNCH, GET_COMM_MODE_INFO, GET_ID (empty), SET_MTA, UPLOAD,
 * SHORT_UPLOAD, DOWNLOAD (with master block mode: DOWNLOAD_NEXT), DOWNLOAD_MAX, GET_CAL_PAGE and SET_CAL_PAGE
 * for the one page that exists. No DAQ, no STIM, no programming, no seed & key. Address granularity byte,
 * address extension 0, MAX_CTO = MAX_DTO = 8 (CAN).
 *
 * Memory access is limited to the regions an IMemoryMap names: reading (UPLOAD) needs a region, writing
 * (DOWNLOAD) a writable one, and a write must cover whole elements of exactly one region. Nothing else of the
 * address space is reachable - the engine never turns an address into a pointer itself.
 *
 * Multi-byte protocol fields (addresses, MAX_DTO, ...) use the byte order given at construction, which is also
 * reported in CONNECT; the data bytes of UPLOAD/DOWNLOAD are copied as they are in memory.
 */
namespace xcp
{
enum class ByteOrder : uint8_t
{
    LITTLE_ENDIAN_ORDER, // Intel, "MSB_LAST"
    BIG_ENDIAN_ORDER     // Motorola, "MSB_FIRST"
};

/** A piece of memory the master may access. */
struct Region
{
    uint32_t address;    // XCP address (ECU_ADDRESS of the A2L)
    uint8_t* data;       // the same memory on this machine
    uint32_t size;       // bytes
    uint8_t elementSize; // bytes of one element (1, 2, 4, 8); writes cover whole elements
    bool writable;       // calibration parameter
};

/** The accessible memory. */
class IMemoryMap
{
public:
    /** The region containing address; false if there is none. */
    virtual bool find(uint32_t address, Region& region) const = 0;

protected:
    ~IMemoryMap() = default;
};

/**
 * TOOL-151: region for constant memory the master may only read (the EPK of the build, a reference page), placed at
 * XCP address base with its bytes at data. True if it contains address; region is set in either case. The region
 * is never writable: DOWNLOAD there is refused with ERR_WRITE_PROTECTED, the data is never written through it.
 */
bool findReadOnly(uint32_t address, uint32_t base, uint8_t const* data, uint32_t size, Region& region);

/** Copies the data; this is where consistency with the users of the memory is decided. */
class IMemoryAccess
{
public:
    /** Copies n bytes for an upload, as one consistent snapshot. */
    virtual void read(uint8_t* destination, uint8_t const* source, uint16_t n) = 0;

    /**
     * Writes n bytes as one consistent change. The engine waits for XcpSlave::writeDone() before it answers
     * (may be called from inside this function). source stays valid until then.
     */
    virtual void write(uint8_t* destination, uint8_t const* source, uint16_t n) = 0;

protected:
    ~IMemoryAccess() = default;
};

/** Sends one response packet (at most MAX_DTO bytes). */
class ITransmitter
{
public:
    virtual void send(uint8_t const* data, uint8_t length) = 0;

protected:
    ~ITransmitter() = default;
};

class XcpSlave
{
public:
    static constexpr uint8_t MAX_CTO = 8U;
    static constexpr uint16_t MAX_DTO = 8U;
    /** Command packets of one DOWNLOAD block (first DOWNLOAD and the DOWNLOAD_NEXT packets). */
    static constexpr uint8_t MAX_BS = 16U;
    /** Largest write that is applied as one: a full DOWNLOAD block. */
    static constexpr uint16_t MAX_DOWNLOAD = MAX_BS * (MAX_CTO - 2U);

    // command codes (PID of the command packet)
    static constexpr uint8_t CMD_CONNECT            = 0xFFU;
    static constexpr uint8_t CMD_DISCONNECT         = 0xFEU;
    static constexpr uint8_t CMD_GET_STATUS         = 0xFDU;
    static constexpr uint8_t CMD_SYNCH              = 0xFCU;
    static constexpr uint8_t CMD_GET_COMM_MODE_INFO = 0xFBU;
    static constexpr uint8_t CMD_GET_ID             = 0xFAU;
    static constexpr uint8_t CMD_SET_MTA            = 0xF6U;
    static constexpr uint8_t CMD_UPLOAD             = 0xF5U;
    static constexpr uint8_t CMD_SHORT_UPLOAD       = 0xF4U;
    static constexpr uint8_t CMD_DOWNLOAD           = 0xF0U;
    static constexpr uint8_t CMD_DOWNLOAD_NEXT      = 0xEFU;
    static constexpr uint8_t CMD_DOWNLOAD_MAX       = 0xEEU;
    static constexpr uint8_t CMD_SET_CAL_PAGE       = 0xEBU;
    static constexpr uint8_t CMD_GET_CAL_PAGE       = 0xEAU;

    // response PIDs and error codes
    static constexpr uint8_t PID_RES              = 0xFFU;
    static constexpr uint8_t PID_ERR              = 0xFEU;
    static constexpr uint8_t ERR_CMD_SYNCH        = 0x00U;
    static constexpr uint8_t ERR_CMD_BUSY         = 0x10U;
    static constexpr uint8_t ERR_CMD_UNKNOWN      = 0x20U;
    static constexpr uint8_t ERR_CMD_SYNTAX       = 0x21U;
    static constexpr uint8_t ERR_OUT_OF_RANGE     = 0x22U;
    static constexpr uint8_t ERR_WRITE_PROTECTED  = 0x23U;
    static constexpr uint8_t ERR_ACCESS_DENIED    = 0x24U;
    static constexpr uint8_t ERR_PAGE_NOT_VALID   = 0x26U;
    static constexpr uint8_t ERR_MODE_NOT_VALID   = 0x27U;
    static constexpr uint8_t ERR_SEGMENT_NOT_VALID = 0x28U;
    static constexpr uint8_t ERR_SEQUENCE         = 0x29U;

    struct Counters
    {
        uint32_t commands;  // command packets while connected (and CONNECT)
        uint32_t errors;    // error responses
        uint32_t uploads;   // UPLOAD / SHORT_UPLOAD answered positively
        uint32_t downloads; // writes applied
        uint32_t denied;    // accesses outside the regions, to read-only memory or not whole elements
        uint32_t busy;      // commands refused while a write was pending, writes not applied in time
        uint32_t refused;   // writes refused by the memory access before they took effect (writeRefused)
    };

    XcpSlave(IMemoryMap const& map, IMemoryAccess& memory, ITransmitter& transmitter, ByteOrder byteOrder);
    XcpSlave(XcpSlave const&)            = delete;
    XcpSlave& operator=(XcpSlave const&) = delete;

    /** One command packet from the master (length: bytes received, may include padding). */
    void receive(uint8_t const* packet, uint8_t length);

    /**
     * Completion of IMemoryAccess::write(). applied false: the write did not happen (not taken over in time);
     * the master gets ERR_CMD_BUSY and may repeat the command.
     */
    void writeDone(bool applied);

    /**
     * Completion of IMemoryAccess::write() without writing: the memory access refuses the new value before it takes
     * effect (SYS-90: plausibility of a safety-relevant calibration parameter). The master gets \p errorCode (an
     * ERR_* of XCP, e.g. ERR_OUT_OF_RANGE), the MTA stays where it was, and the memory is unchanged.
     */
    void writeRefused(uint8_t errorCode);

    /** Back to the disconnected state (transport lost, shutdown). A pending write is left to writeDone(). */
    void reset();

    bool isConnected() const { return _connected; }

    bool isWritePending() const { return _writePending; }

    uint32_t getMta() const { return _mta; }

    Counters const& getCounters() const { return _counters; }

private:
    void process(uint8_t const* packet, uint8_t length);
    void connect(uint8_t const* packet, uint8_t length);
    void upload(uint32_t address, uint8_t n);
    void download(uint8_t const* packet, uint8_t length, uint8_t dataOffset, uint8_t n, bool block);
    void downloadNext(uint8_t const* packet, uint8_t length);
    void startWrite();
    void sendPositive(uint8_t const* data, uint8_t length);
    void sendError(uint8_t code);
    void sendError(uint8_t code, uint8_t parameter);
    /** Error code if [address, address + n) is not readable (0: fine). */
    uint8_t checkRead(uint32_t address, uint32_t n, Region& region) const;
    /** Error code if [address, address + n) is not a write of whole elements of one writable region. */
    uint8_t checkWrite(uint32_t address, uint32_t n, Region& region) const;
    uint32_t getDword(uint8_t const* bytes) const;
    void putWord(uint8_t* bytes, uint16_t value) const;
    void putDword(uint8_t* bytes, uint32_t value) const;

    IMemoryMap const& _map;
    IMemoryAccess& _memory;
    ITransmitter& _transmitter;
    ByteOrder const _byteOrder;
    uint32_t _mta;
    bool _connected;
    // DOWNLOAD staging: a block is collected here and written as one
    uint8_t _staging[MAX_DOWNLOAD];
    uint8_t* _writeTarget;
    uint16_t _writeLength;
    uint16_t _blockReceived;
    bool _blockActive;
    bool _writePending;
    Counters _counters;
};

} // namespace xcp
