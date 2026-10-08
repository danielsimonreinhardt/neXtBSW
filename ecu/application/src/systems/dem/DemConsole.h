// NVM-03: console command "dem" - what the fault memory holds, readable without a tester. The text is built here from
// dem::IFaultMemory only (thread safe, every read copies under the lock of the memory), so the host test prints the
// same lines as the target. Writer: anything with printf(char const*, ...) (util::format::SharedStringWriter).
//
//   dem status          memory: entries used, overflows, displaced entries, enable conditions, operation cycles,
//                       ControlDTCSetting, whether the stored state was valid at the start
//   dem list            every DTC: number, status byte with its bits, fault detection counter, counters, stored
//   dem data            snapshot records and extended data records of every stored DTC, as bytes
//   dem clear [<dtc>]   clears all DTCs (0xFFFFFF) or one DTC number, like service 14
//   dem cycle           ends and restarts every running operation cycle (a power cycle without switching off:
//                       pending and aging can be checked at the desk) - a test function
//   dem setting <0|1>   ControlDTCSetting off/on, like service 85

#pragma once

#include <dem/IFaultMemory.h>

#include <cstddef>
#include <cstdint>

namespace dem
{
namespace console
{

/** Short names of the status bits (ISO 14229-1, D.2.2), bit 0 first. */
inline char const* statusBitName(uint8_t const bit)
{
    static char const* const NAMES[8] = {"TF", "TFTOC", "PDTC", "CDTC", "TNCSLC", "TFSLC", "TNCTOC", "WIR"};
    return (bit < 8U) ? NAMES[bit] : "?";
}

template<class Writer>
void printStatusBits(Writer& out, uint8_t const status)
{
    for (uint8_t bit = 0U; bit < 8U; ++bit)
    {
        if ((status & (1U << bit)) != 0U)
        {
            (void)out.printf(" %s", statusBitName(bit));
        }
    }
}

template<class Writer>
void printStatus(
    Writer& out,
    IFaultMemory const& memory,
    char const* const* const cycleNames,
    size_t const cycleCount,
    char const* const* const conditionNames,
    size_t const conditionCount)
{
    MemoryInfo const info = memory.memoryInfo();
    (void)out.printf(
        "dem: %u DTCs, entries %u of %u, overflows %u, displaced %u, DTC setting %s, stored state %s\r\n",
        static_cast<unsigned>(memory.dtcCount()),
        static_cast<unsigned>(info.entriesUsed),
        static_cast<unsigned>(info.maxEntries),
        static_cast<unsigned>(info.overflows),
        static_cast<unsigned>(info.displaced),
        info.dtcSetting ? "on" : "off (85)",
        info.loaded ? "loaded" : "not valid, started cleared");
    for (size_t c = 0U; c < cycleCount; ++c)
    {
        (void)out.printf(
            "cycle %u %s: %s\r\n",
            static_cast<unsigned>(c),
            cycleNames[c],
            ((info.cyclesRunning & (1U << c)) != 0U) ? "running" : "stopped");
    }
    for (size_t i = 0U; i < conditionCount; ++i)
    {
        (void)out.printf(
            "condition %u %s: %s\r\n",
            static_cast<unsigned>(i),
            conditionNames[i],
            ((info.conditions & (1UL << i)) != 0U) ? "fulfilled" : "not fulfilled");
    }
}

template<class Writer>
void printList(Writer& out, IFaultMemory const& memory)
{
    for (uint16_t i = 0U; i < memory.dtcCount(); ++i)
    {
        DtcInfo info{};
        if (!memory.dtcInfo(i, info))
        {
            continue;
        }
        (void)out.printf(
            "%06X %s: status 0x%02X",
            static_cast<unsigned>(info.number),
            memory.dtcConfig(i).name,
            static_cast<unsigned>(info.status));
        printStatusBits(out, info.status);
        (void)out.printf(
            ", fdc %d, occurrence %u, aging %u, failed cycles %u, since first failed %u%s\r\n",
            static_cast<int>(info.fdc),
            static_cast<unsigned>(info.occurrence),
            static_cast<unsigned>(info.aging),
            static_cast<unsigned>(info.failedCycles),
            static_cast<unsigned>(info.cyclesSinceFirstFailed),
            info.stored ? ", stored" : "");
    }
}

template<class Writer>
void printBytes(Writer& out, uint8_t const* const data, int32_t const length)
{
    for (int32_t k = 0; k < length; ++k)
    {
        (void)out.printf(" %02X", static_cast<unsigned>(data[k]));
    }
}

template<class Writer>
void printData(Writer& out, IFaultMemory const& memory)
{
    uint8_t buffer[64] = {}; // defined content even if a read reports more bytes than it wrote
    unsigned stored = 0U;
    for (uint16_t i = 0U; i < memory.dtcCount(); ++i)
    {
        DtcInfo info{};
        if (!memory.dtcInfo(i, info) || !info.stored)
        {
            continue;
        }
        ++stored;
        DtcConfig const& config = memory.dtcConfig(i);
        (void)out.printf("%06X %s\r\n", static_cast<unsigned>(info.number), config.name);
        for (uint8_t r = 0U; r < config.snapshotCount; ++r)
        {
            SnapshotRecord const& record = config.snapshots[r];
            int32_t const n              = memory.readSnapshot(i, record.number, buffer, sizeof(buffer));
            (void)out.printf("  snapshot 0x%02X:", static_cast<unsigned>(record.number));
            for (uint8_t k = 0U; k < record.count; ++k)
            {
                (void)out.printf(" %04X", static_cast<unsigned>(record.items[k].did));
            }
            (void)out.printf(" =");
            printBytes(out, buffer, n);
            (void)out.printf("\r\n");
        }
        for (uint8_t r = 0U; r < config.extendedCount; ++r)
        {
            uint8_t const number = config.extended[r].number;
            int32_t const n      = memory.readExtended(i, number, buffer, sizeof(buffer));
            (void)out.printf("  extended 0x%02X =", static_cast<unsigned>(number));
            printBytes(out, buffer, n);
            (void)out.printf("\r\n");
        }
    }
    if (stored == 0U)
    {
        (void)out.printf("no stored DTC\r\n");
    }
}

} // namespace console
} // namespace dem
