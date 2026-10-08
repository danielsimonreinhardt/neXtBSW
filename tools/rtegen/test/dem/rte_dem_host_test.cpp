// SIL test of NVM-03: the fault memory generated from the diagnostic description (test/dem/diag.yaml) through the
// generated RTE (RteDem.h, rte::demSample/demCondition/demCycleState, NvM blocks DemEvents/DemEntries), the fault
// memory integration (ecu/application/src/systems/dem/DemRte.h) and the NvM core on a virtual EEPROM kept in a file
// between the runs - every run is one power cycle. Project buildtest_s32k148_min (no SBC), BSW task profile
// test/multitask/tasks.yaml. The harness plays systems::RteSystem: NvM start -> fault memory start -> cycles of the
// fault memory -> end of the operation cycles and NvM writeAll at the run-down. The buffers are written directly.
//
//   run 1 <file>   erased memory: started cleared; SpeedTooHigh (limit on MotorSwc.Speed, conditions MotorEnabled and
//                  Running) matures after 4 samples and is stored with its snapshot; CountStatus (status source, cycle
//                  Drive driven by AppSwc.Led) and BusOff (reported by the BSW, priority 1) overflow the two entries
//   run 2 <file>   the stored state is back: statuses of the new cycle, entries, snapshot; SpeedTooHigh passes
//   run 3 <file>   passes again: aged after 2 cycles (confirmed bit and entry gone)
//   run none       no memory in the port (the board today): started cleared, works in RAM, nothing written
// Build and run: tools/rtegen/test_rtegen_dem.py (RteDemHostTest).

#include "FakeEeprom.h"
#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"
#include "dem/DemRte.h"
#include "nvm/NvmRteBlocks.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;

// runnables of the test components (test/multitask): not called here, the harness writes the buffers
extern "C" void Test_Trace(uint8 const /* id */) {}

extern "C" void Test_Hook(uint8 const /* where */) {}

namespace
{
uint8_t ecuState = 1U; // RUN
} // namespace

namespace ecustate
{
namespace rte
{
void readState(uint8_t& state) { state = ecuState; }
} // namespace rte
} // namespace ecustate

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

using Memory = FakeEeprom<4096U>;
Memory memory;

/** Plays the diagnostic stack (dem::IDidReader): MotorSpeed from the RTE buffer, Marker a constant. */
class Reader : public ::dem::IDidReader
{
public:
    bool read(uint16_t const did, uint8_t* const data, uint16_t const bytes) override
    {
        uint32_t value = 0U;
        if ((did == 0x0300U) && (bytes == 2U))
        {
            value = Rte_Buf_MotorSwc_Speed_Speed;
        }
        else if ((did == 0xFD10U) && (bytes == 4U))
        {
            value = 0x12345678U;
        }
        else
        {
            return false;
        }
        for (uint16_t i = 0U; i < bytes; ++i)
        {
            data[i] = static_cast<uint8_t>(value >> (8U * (bytes - 1U - i))); // big endian, as 22 answers
        }
        return true;
    }
};

Reader reader;

bool load(char const* const path)
{
    FILE* const f = std::fopen(path, "rb");
    if (f == nullptr)
    {
        return false;
    }
    size_t const n = std::fread(memory.bytes, 1U, sizeof(memory.bytes), f);
    std::fclose(f);
    return n == sizeof(memory.bytes);
}

void save(char const* const path)
{
    FILE* const f = std::fopen(path, "wb");
    if (f != nullptr)
    {
        (void)std::fwrite(memory.bytes, 1U, sizeof(memory.bytes), f);
        std::fclose(f);
    }
}

::dem::DtcInfo info(::dem::IFaultMemory const& m, char const* const name)
{
    ::dem::DtcInfo i{};
    for (uint16_t d = 0U; d < m.dtcCount(); ++d)
    {
        if (std::strcmp(m.dtcConfig(d).name, name) == 0)
        {
            (void)m.dtcInfo(d, i);
        }
    }
    return i;
}

void checkTables()
{
    // tables and NvM rows as the generator derives them from test/dem/diag.yaml
    CHECK(::rte::DEM_DTC_COUNT == 4U);
    CHECK(::rte::DEM_CYCLE_COUNT == 2U);
    CHECK(::rte::DEM_CONDITION_COUNT == 2U);
    CHECK(::rte::DEM_ENTRY_DATA_BYTES == 10U); // snapshot 2 + 4, extended identifier 4
    ::rte::NvmBlock const& events  = ::rte::NVM_BLOCKS[::rte::DEM_NVM_EVENTS];
    ::rte::NvmBlock const& entries = ::rte::NVM_BLOCKS[::rte::DEM_NVM_ENTRIES];
    CHECK(std::strcmp(events.name, "DemEvents") == 0);
    CHECK(std::strcmp(entries.name, "DemEntries") == 0);
    CHECK(events.size == ::dem::eventsBlockBytes(4U));
    CHECK(entries.size == ::dem::entriesBlockBytes(2U, 10U));
    CHECK(events.write == ::rte::NvmWrite::CHANGE);
    CHECK(events.minIntervalMs == 500U);
    CHECK(::rte::demtables::CONFIG.autostartCycles == 0x01U);
    CHECK(::rte::demtables::CONFIG.dtcs[0].conditions == 0x3U);
}

void cycles(::dem::DemRte& dem, unsigned const n)
{
    for (unsigned i = 0U; i < n; ++i)
    {
        dem.cycle();
    }
}

void powerCycle(char const* const run, ::eeprom::IEepromDriver* const eeprom)
{
    ::nvm::NvmRteBlocks nvm;
    ::dem::DemRte dem;
    nvm.start(eeprom, (eeprom != nullptr) ? 4096U : 0U);
    dem.manager().setDidReader(&reader);
    bool const loaded = dem.start();
    ::dem::IFaultMemory const& m = dem.manager();
    CHECK(::dem::registry.memory == &dem.manager());
    CHECK(std::strcmp(::rte::demtables::CYCLE_NAMES[1], "Drive") == 0);
    uint8_t out[16] = {};

    if (std::strcmp(run, "1") == 0)
    {
        CHECK(!loaded);
        CHECK(info(m, "SpeedTooHigh").status == 0x50U);
        // condition MotorEnabled (AppSwc.Led) off: samples discarded
        Rte_Buf_AppSwc_Led_Led        = 0U;
        Rte_Buf_MotorSwc_Speed_Speed  = 4000U;
        cycles(dem, 10U);
        CHECK(info(m, "SpeedTooHigh").status == 0x50U);
        CHECK(info(m, "SpeedTooHigh").fdc == 0);
        CHECK(m.memoryInfo().cyclesRunning == 0x1U); // Drive follows AppSwc.Led
        // condition on, ECU not in RUN: still discarded
        Rte_Buf_AppSwc_Led_Led = 1U;
        ecuState               = 2U;
        cycles(dem, 5U);
        CHECK(info(m, "SpeedTooHigh").fdc == 0);
        CHECK(m.memoryInfo().cyclesRunning == 0x3U);
        ecuState = 1U;
        cycles(dem, 3U);
        CHECK(info(m, "SpeedTooHigh").fdc == 95); // 3 of 4
        CHECK(info(m, "SpeedTooHigh").status == 0x50U);
        cycles(dem, 1U);
        CHECK(info(m, "SpeedTooHigh").status == 0x2FU);
        CHECK(info(m, "SpeedTooHigh").stored);
        CHECK(nvm.manager().isPending(::rte::DEM_NVM_EVENTS)); // handed over to the NvM image
        CHECK(nvm.manager().isPending(::rte::DEM_NVM_ENTRIES));
        CHECK(m.readSnapshot(0U, 0x01U, out, sizeof(out)) == 6);
        CHECK((out[0] == 0x0FU) && (out[1] == 0xA0U)); // 4000
        CHECK((out[2] == 0x12U) && (out[5] == 0x78U));
        // CountStatus: status source 1 = FAILED, matures at once
        Rte_Buf_AppSwc_SlowCount_SlowCount = 1U;
        cycles(dem, 1U);
        CHECK(info(m, "CountStatus").status == 0x2FU);
        CHECK(m.memoryInfo().entriesUsed == 2U);
        // BusOff (priority 1) reported by the BSW: displaces CountStatus (priority 3)
        int32_t const busOff = ::dem::registry.memory->findReportedEvent("can.busoff.CAN1");
        CHECK(busOff == 2);
        ::dem::registry.memory->report(static_cast<uint16_t>(busOff), ::dem::Result::FAILED);
        cycles(dem, 1U);
        CHECK(info(m, "BusOff").stored);
        CHECK(!info(m, "CountStatus").stored);
        CHECK(m.memoryInfo().displaced == 1U);
        // values outside the status range give no result
        Rte_Buf_AppSwc_SlowCount_SlowCount = 7U;
        cycles(dem, 3U);
        CHECK(info(m, "CountStatus").fdc == 127);
        // one NvM cycle writes a changed block (policy CHANGE); the run-down writes the rest
        CHECK(nvm.manager().mainFunction(0U) == 1U);
    }
    else if (std::strcmp(run, "2") == 0)
    {
        CHECK(loaded);
        // run 1 ended its cycles (tested and failed: pending stays); the new cycle cleared TFTOC and set TNCTOC
        CHECK(info(m, "SpeedTooHigh").status == 0x6DU);
        CHECK(info(m, "SpeedTooHigh").occurrence == 1U);
        CHECK(info(m, "SpeedTooHigh").cyclesSinceFirstFailed == 1U);
        CHECK(info(m, "SpeedTooHigh").stored);
        CHECK(info(m, "BusOff").stored);
        CHECK(m.readSnapshot(0U, 0x01U, out, sizeof(out)) == 6);
        CHECK((out[0] == 0x0FU) && (out[1] == 0xA0U)); // the snapshot of run 1
        CHECK(m.readExtended(0U, 0x01U, out, sizeof(out)) == 6);
        CHECK((out[0] == 1U) && (out[1] == 0x12U) && (out[5] == 0U));
        Rte_Buf_AppSwc_Led_Led       = 1U;
        Rte_Buf_MotorSwc_Speed_Speed = 100U;
        cycles(dem, 5U);
        CHECK(info(m, "SpeedTooHigh").status == 0x2CU); // passed: TF, TNCTOC cleared
    }
    else if (std::strcmp(run, "3") == 0)
    {
        CHECK(loaded);
        CHECK(info(m, "SpeedTooHigh").status == 0x68U); // pending cleared at the end of run 2
        CHECK(info(m, "SpeedTooHigh").aging == 1U);
        Rte_Buf_AppSwc_Led_Led       = 1U;
        Rte_Buf_MotorSwc_Speed_Speed = 100U;
        cycles(dem, 5U);
    }
    else
    {
        CHECK(!loaded);
        CHECK(nvm.manager().status(::rte::DEM_NVM_EVENTS).state == ::nvm::BlockState::NO_MEMORY);
        Rte_Buf_AppSwc_Led_Led       = 1U;
        Rte_Buf_MotorSwc_Speed_Speed = 4000U;
        cycles(dem, 4U);
        CHECK(info(m, "SpeedTooHigh").status == 0x2FU); // works in RAM
    }

    // ---- run-down: end of the cycles, then the NvM writes ----
    dem.stop();
    size_t const written = nvm.manager().writeAll(1000U);
    if (std::strcmp(run, "3") == 0)
    {
        CHECK(info(m, "SpeedTooHigh").aging == 2U);
        CHECK((info(m, "SpeedTooHigh").status & 0x08U) == 0U); // aged: confirmed cleared
        CHECK(!info(m, "SpeedTooHigh").stored);
    }
    if (eeprom == nullptr)
    {
        CHECK(written == 0U);
    }
    CHECK(memory.initCalls == 0U);
    ::dem::registry.memory = nullptr;
    std::printf("run %s: loaded %u, written %u, entries %u\n", run, loaded ? 1U : 0U, static_cast<unsigned>(written),
                static_cast<unsigned>(m.memoryInfo().entriesUsed));
}

} // namespace

int main(int const argc, char** const argv)
{
    if (argc < 3)
    {
        std::printf("usage: rte_dem_host_test <run> <file|->\n");
        return 2;
    }
    checkTables();
    bool const withMemory = std::strcmp(argv[2], "-") != 0;
    if (withMemory)
    {
        (void)load(argv[2]); // missing file: erased memory
    }
    powerCycle(argv[1], withMemory ? &memory : nullptr);
    if (withMemory)
    {
        save(argv[2]);
    }
    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
