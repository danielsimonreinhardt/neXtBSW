// Host build of the XCP slave engine (TOOL-01) with a simulated CAN channel on stdin/stdout.
//
// Own test profile (not the eval board): four signals and four parameters at made-up XCP addresses, among them
// a 64 bit value (written in two words on a 32 bit target) and an array larger than one DOWNLOAD block. The 64 bit
// value Gain stands for a safety-relevant parameter (SYS-90): a write outside -100 .. 100 is refused before it takes
// effect (IMemoryAccess::write -> XcpSlave::writeRefused, ERR_OUT_OF_RANGE). TOOL-151: an EPK text in constant
// memory is reachable read only through xcp::findReadOnly, as XcpSystem::find exposes xcp::EPK at ADDR_EPK.
//
// Lines on stdin (one command each, every line is answered by "OK" after its output):
//   RX <hex bytes>   command packet of the master (one CAN frame, DLC as given)
//   STEP             the application runs one cycle (signal values change)
//   TICK             the writer task runs (--deferred): a pending write is applied, the answer follows
//   EXPIRE           the write timeout expires (--deferred): a pending write is dropped
//   DUMP <addr> <n>  prints "MEM <hex>" straight from the simulated memory (ground truth for the test)
// Output: "TX <hex bytes>" per response packet (padded to 8 bytes like XcpSystem does).

#include "xcp/XcpSlave.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace
{
struct Memory
{
    // signals (read only)
    uint32_t counter    = 0U;
    float temperature   = 21.5F;
    uint8_t flag        = 0U;
    double energy       = 0.0;
    // parameters (writable)
    float alpha         = 0.125F;
    uint16_t table[4]   = {10U, 20U, 30U, 40U};
    double gain         = 1.0;
    uint8_t curve[100]  = {};
};

Memory memory;

xcp::Region const REGIONS[] = {
    {0x20000000U, reinterpret_cast<uint8_t*>(&memory.counter), 4U, 4U, false},
    {0x20000004U, reinterpret_cast<uint8_t*>(&memory.temperature), 4U, 4U, false},
    {0x20000008U, &memory.flag, 1U, 1U, false},
    {0x20000010U, reinterpret_cast<uint8_t*>(&memory.energy), 8U, 8U, false},
    {0x20000100U, reinterpret_cast<uint8_t*>(&memory.alpha), 4U, 4U, true},
    {0x20000104U, reinterpret_cast<uint8_t*>(memory.table), 8U, 2U, true},
    // 0x2000010C..0x2000010F: nothing (gap)
    {0x20000110U, reinterpret_cast<uint8_t*>(&memory.gain), 8U, 8U, true},
    {0x20000118U, memory.curve, 100U, 1U, true},
};

// TOOL-151: the EPK of the test profile (the A2L of test_xcp_host.py names the same text and address)
constexpr uint32_t EPK_ADDRESS = 0x00038BC0U;
char const EPK[]               = "xcp_host_test neXtBSW 0.0.0 host";

class Map : public xcp::IMemoryMap
{
public:
    bool find(uint32_t const address, xcp::Region& region) const override
    {
        for (auto const& r : REGIONS)
        {
            if ((address >= r.address) && ((address - r.address) < r.size))
            {
                region = r;
                return true;
            }
        }
        return xcp::findReadOnly(
            address, EPK_ADDRESS, reinterpret_cast<uint8_t const*>(EPK), sizeof(EPK) - 1U, region);
    }
};

class Transmitter : public xcp::ITransmitter
{
public:
    void send(uint8_t const* const data, uint8_t const length) override
    {
        std::printf("TX");
        for (uint8_t i = 0U; i < 8U; ++i)
        {
            std::printf(" %02x", (i < length) ? data[i] : 0U);
        }
        std::printf("\n");
    }
};

class Access : public xcp::IMemoryAccess
{
public:
    explicit Access(bool deferred) : _deferred(deferred) {}

    void attach(xcp::XcpSlave& slave) { _slave = &slave; }

    void read(uint8_t* const destination, uint8_t const* const source, uint16_t const n) override
    {
        std::memcpy(destination, source, n);
    }

    void write(uint8_t* const destination, uint8_t const* const source, uint16_t const n) override
    {
        // SYS-90: stand-in for the plausibility check of a safety-relevant parameter - Gain must stay inside
        // -100 .. 100 (NaN too is outside); XcpSystem asks calibration::plausibility::checkWrite() here
        if ((destination == reinterpret_cast<uint8_t*>(&memory.gain)) && (n == sizeof(double)))
        {
            double gain = 0.0;
            std::memcpy(&gain, source, sizeof(gain));
            if (!((gain >= -100.0) && (gain <= 100.0)))
            {
                _slave->writeRefused(xcp::XcpSlave::ERR_OUT_OF_RANGE);
                return;
            }
        }
        _destination = destination;
        _source      = source;
        _length      = n;
        _pending     = true;
        if (!_deferred)
        {
            tick();
        }
    }

    void tick()
    {
        if (_pending)
        {
            std::memcpy(_destination, _source, _length); // the writer task copies between its cycles
            _pending = false;
            _slave->writeDone(true);
        }
    }

    void expire()
    {
        if (_pending)
        {
            _pending = false;
            _slave->writeDone(false);
        }
    }

private:
    bool const _deferred;
    xcp::XcpSlave* _slave     = nullptr;
    uint8_t* _destination     = nullptr;
    uint8_t const* _source    = nullptr;
    uint16_t _length          = 0U;
    bool _pending             = false;
};

uint8_t const* hostAddress(uint32_t const address)
{
    for (auto const& r : REGIONS)
    {
        if ((address >= r.address) && ((address - r.address) < r.size))
        {
            return r.data + (address - r.address);
        }
    }
    if ((address >= EPK_ADDRESS) && ((address - EPK_ADDRESS) < sizeof(EPK)))
    {
        return reinterpret_cast<uint8_t const*>(EPK) + (address - EPK_ADDRESS);
    }
    return nullptr;
}
} // namespace

int main(int argc, char** argv)
{
    bool const deferred = (argc > 1) && (std::strcmp(argv[1], "--deferred") == 0);
    Map map;
    Transmitter transmitter;
    Access access(deferred);
    xcp::XcpSlave slave(map, access, transmitter, xcp::ByteOrder::LITTLE_ENDIAN_ORDER);
    access.attach(slave);
    std::setvbuf(stdout, nullptr, _IOLBF, 0);

    char line[256];
    while (std::fgets(line, sizeof(line), stdin) != nullptr)
    {
        std::string const text(line);
        if (text.rfind("RX", 0) == 0)
        {
            uint8_t packet[8];
            uint8_t length = 0U;
            char* cursor   = line + 2;
            while (length < 8U)
            {
                char* end               = nullptr;
                unsigned long const val = std::strtoul(cursor, &end, 16);
                if (end == cursor)
                {
                    break;
                }
                packet[length++] = static_cast<uint8_t>(val);
                cursor           = end;
            }
            slave.receive(packet, length);
        }
        else if (text.rfind("STEP", 0) == 0)
        {
            memory.counter += 1U;
            memory.temperature += 0.5F;
            memory.energy += 0.25;
            memory.flag = static_cast<uint8_t>(memory.flag ^ 1U);
        }
        else if (text.rfind("TICK", 0) == 0)
        {
            access.tick();
        }
        else if (text.rfind("EXPIRE", 0) == 0)
        {
            access.expire();
        }
        else if (text.rfind("DUMP", 0) == 0)
        {
            char* end              = nullptr;
            uint32_t const address = static_cast<uint32_t>(std::strtoul(line + 4, &end, 16));
            unsigned const n       = static_cast<unsigned>(std::strtoul(end, nullptr, 10));
            uint8_t const* data    = hostAddress(address);
            std::printf("MEM");
            for (unsigned i = 0U; (data != nullptr) && (i < n); ++i)
            {
                std::printf(" %02x", data[i]);
            }
            std::printf("\n");
        }
        else if (text.rfind("QUIT", 0) == 0)
        {
            break;
        }
        std::printf("OK\n");
        std::fflush(stdout);
    }
    return 0;
}
