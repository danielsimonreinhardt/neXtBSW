/* NVM-19: host test of the NvM integration of the application - nvm::NvmRteBlocks (the block table of libs/bsw/nvm
 * built from rte::NVM_BLOCKS with integrity and quality) and nvm/NvmEvents.h (the events of the start on their way to
 * the fault memory) - on a virtual EEPROM in RAM, over several power cycles. Own test profile (nvmstubs/RteConfig.h):
 * Trim uint16 qm, Counter uint32 asil_b, Table uint8[6] asil_d; the fault memory is a fake dem::IFaultMemory that
 * knows DTCs by their BSW event name "<event>.<Block>" (TOOL-178: one DTC per event and block).
 *
 * What it shows:
 * - an asil_* block of the RTE is an ASIL block of the core (two copies with the same record, write counter), a qm
 *   block stays in the QM format; the quality of every block reaches its RTE buffer through rte::nvmRestore();
 * - erased memory: defaults, quality DEFAULT, no event, every DTC of the NvM PASSED;
 * - copy A of an ASIL block damaged (nvm inject): value kept, copy repaired, COPY_REPAIRED -> the DTC of this event
 *   and this block FAILED, every other one PASSED;
 * - the repair refused by the memory: value kept, COPY_REPAIR_FAILED -> its DTC FAILED;
 * - both copies of two blocks damaged: defaults, quality UNSECURED, DEFAULT_LOADED -> the DTC of each block FAILED;
 * - NVM-30: the newest place of the QM block damaged: the previous value, quality STORED, PLACE_DEFECTIVE -> the DTC
 *   nvm.place_defective.Trim FAILED; after the next write of the block PASSED again;
 * - no memory, or a block beyond it: no result for that block (it was not checked); a fault memory without these
 *   DTCs gets nothing; every image and quality access runs under the lock of the RTE.
 * Deliberate faults of NvmRteBlocks.h/NvmEvents.h must fail it: test/test_nvm_integration.py.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DNX_NVM_FAULT_INJECTION=1 -Iecu/application/src/systems/test/host/nvmstubs -Iecu/application/src/systems \
 *       -Iecu/application/src/systems/test/host -Ilibs/bsw/nvm/include -Ilibs/bsw/dem/include \
 *       -isystem external/openbsw/libs/bsw/bsp/include \
 *       ecu/application/src/systems/test/host/NvmIntegrationHostTest.cpp -o build/host/nvmintegration.exe
 */

#include "FakeEeprom.h"
#include "nvm/NvmEvents.h"
#include "nvm/NvmRteBlocks.h"

#include <cstdint>
#include <cstdio>
#include <cstring>

int async::LockType::depth = 0;

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(cond)                                                     \
    do                                                                  \
    {                                                                   \
        ++checks;                                                       \
        if (!(cond))                                                    \
        {                                                               \
            ++failures;                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                               \
    } while (false)

// ------------------------------------------------------------------------------ the "generated" RTE of the profile

constexpr size_t TRIM = 0U, COUNTER = 1U, TABLE = 2U;

uint16_t trimImage = 0U, trimBuffer = 0U;
uint16_t const trimDefault = 500U;
uint32_t counterImage = 0U, counterBuffer = 0U;
uint32_t const counterDefault = 0U;
uint8_t tableImage[6] = {}, tableBuffer[6] = {};
uint8_t const tableDefault[6] = {1U, 2U, 3U, 4U, 5U, 6U};
bool changed[3]      = {false, false, false};
uint8_t quality[3]   = {0xEEU, 0xEEU, 0xEEU}; // written by the NvM (rte::nvmQuality[])
uint8_t qualifier[3] = {0xEEU, 0xEEU, 0xEEU}; // buffers of nvm.qlf.<Block> after nvmRestore()

// addresses as rtegen lays them out: QM 2 x 12, ASIL 2 x 16, ASIL 2 x 20
constexpr uint32_t TRIM_AT = 0U, COUNTER_AT = 24U, TABLE_AT = 56U;
} // namespace

namespace rte
{
NvmBlock const NVM_BLOCKS[] = {
    {"Trim", 0x1111U, 2U, TRIM_AT, &trimImage, &trimDefault, &changed[TRIM], NvmWrite::CHANGE, 100U,
     NvmIntegrity::QM, &quality[TRIM]},
    {"Counter", 0x2222U, 4U, COUNTER_AT, &counterImage, &counterDefault, &changed[COUNTER], NvmWrite::CHANGE, 100U,
     NvmIntegrity::ASIL, &quality[COUNTER]},
    {"Table", 0x3333U, 6U, TABLE_AT, tableImage, tableDefault, &changed[TABLE], NvmWrite::SHUTDOWN, 0U,
     NvmIntegrity::ASIL, &quality[TABLE]},
};

void nvmRestore()
{
    ::async::LockType const lock;
    trimBuffer    = trimImage;
    counterBuffer = counterImage;
    std::memcpy(tableBuffer, tableImage, sizeof(tableBuffer));
    for (size_t i = 0U; i < NVM_BLOCK_COUNT; ++i)
    {
        qualifier[i] = quality[i]; // rtegen maps nvm::Quality to QLF_* here (NVM_QUALITY); the number is enough
    }
}
} // namespace rte

namespace
{
// ------------------------------------------------------------------------------------------ fake fault memory

constexpr size_t MAX_REPORTS = 16U;

struct Report
{
    uint16_t dtc;
    ::dem::Result result;
};

/** Knows DTCs by their BSW event name only and records what is reported. */
class FakeFaultMemory final : public ::dem::IFaultMemory
{
public:
    explicit FakeFaultMemory(char const* const* names, uint16_t count) : _names(names), _count(count) {}

    uint16_t dtcCount() const override { return _count; }

    ::dem::DtcConfig const& dtcConfig(uint16_t) const override { return _config; }

    bool dtcInfo(uint16_t, ::dem::DtcInfo&) const override { return false; }

    int32_t findDtc(uint32_t) const override { return -1; }

    uint16_t countByStatusMask(uint8_t) const override { return 0U; }

    ::dem::MemoryInfo memoryInfo() const override { return ::dem::MemoryInfo{}; }

    int32_t readSnapshot(uint16_t, uint8_t, uint8_t*, size_t) const override { return -1; }

    int32_t readExtended(uint16_t, uint8_t, uint8_t*, size_t) const override { return -1; }

    bool clear(uint32_t) override { return false; }

    void setDtcSetting(bool) override {}

    int32_t findReportedEvent(char const* const name) const override
    {
        for (uint16_t i = 0U; i < _count; ++i)
        {
            if (std::strcmp(_names[i], name) == 0)
            {
                return static_cast<int32_t>(i);
            }
        }
        return -1;
    }

    void report(uint16_t const dtc, ::dem::Result const result) override
    {
        if (reports < MAX_REPORTS)
        {
            log[reports] = Report{dtc, result};
        }
        ++reports;
    }

    /** Result reported for the DTC of \p name (NONE: none); fails if it was reported more than once. */
    ::dem::Result resultOf(char const* const name) const
    {
        int32_t const dtc   = findReportedEvent(name);
        ::dem::Result found = ::dem::Result::NONE;
        unsigned times      = 0U;
        for (size_t i = 0U; (i < reports) && (i < MAX_REPORTS); ++i)
        {
            if (static_cast<int32_t>(log[i].dtc) == dtc)
            {
                found = log[i].result;
                ++times;
            }
        }
        CHECK(times <= 1U);
        return found;
    }

    Report log[MAX_REPORTS] = {};
    size_t reports          = 0U;

private:
    char const* const* _names;
    uint16_t _count;
    ::dem::DtcConfig _config{};
};

// DTCs of a project (TOOL-178: one per event and block, here not for every pair), with another BSW event in between
char const* const PROJECT_DTCS[] = {
    "nvm.default_loaded.Trim",
    "rte.deadline",
    "nvm.default_loaded.Counter",
    "nvm.copy_repair_failed.Counter",
    "nvm.copy_repaired.Counter",
    "nvm.default_loaded.Table",
    "nvm.copy_repaired.Table",
    "nvm.copy_repaired", // the event without a block names no DTC of the NvM
    "nvm.place_defective.Trim", // NVM-30: QM block, one place defective
};
constexpr uint16_t PROJECT_DTC_COUNT = 9U;
constexpr size_t NVM_DTCS            = 7U; // DTCs above that a start with every block read reports

using Memory = FakeEeprom<256U>;

uint16_t get16(Memory const& m, uint32_t const a)
{
    return static_cast<uint16_t>(m.bytes[a] | (m.bytes[a + 1U] << 8U));
}

uint32_t get32(Memory const& m, uint32_t const a)
{
    return static_cast<uint32_t>(get16(m, a)) | (static_cast<uint32_t>(get16(m, a + 2U)) << 16U);
}

/** One power cycle up to the fault memory: a new NvM (fresh status) over the same memory, read, restore, report. */
struct Start
{
    explicit Start(
        ::eeprom::IEepromDriver* const memory,
        char const* const* names = PROJECT_DTCS,
        uint16_t count           = PROJECT_DTC_COUNT,
        uint32_t usable          = 256U)
    : faultMemory(names, count)
    {
        for (uint8_t& q : quality)
        {
            q = 0xEEU;
        }
        nvm.manager().setEventSink(&events);
        nvm.start(memory, (memory != nullptr) ? usable : 0U);
        reported = ::nvm::reportToFaultMemory(events, nvm.manager(), faultMemory);
    }

    ::nvm::BlockState state(size_t const i) const { return nvm.manager().status(i).state; }

    ::nvm::NvmRteBlocks nvm;
    ::nvm::EventLog<::nvm::NvmRteBlocks::COUNT> events;
    FakeFaultMemory faultMemory;
    size_t reported = 0U;
};

uint8_t q(::nvm::Quality const quality) { return static_cast<uint8_t>(quality); }

void setValues(uint16_t const trim, uint32_t const counter, uint8_t const first)
{
    ::async::LockType const lock;
    trimImage    = trim;
    counterImage = counter;
    for (uint8_t i = 0U; i < 6U; ++i)
    {
        tableImage[i] = static_cast<uint8_t>(first + i);
    }
    changed[TRIM] = changed[COUNTER] = changed[TABLE] = true;
}

/** Every DTC of the NvM in PROJECT_DTCS PASSED, except \p failed (nullptr: none). */
void expectOnlyFailed(FakeFaultMemory const& fm, char const* const failed, char const* const failed2 = nullptr)
{
    for (uint16_t i = 0U; i < PROJECT_DTC_COUNT; ++i)
    {
        char const* const name = PROJECT_DTCS[i];
        if ((std::strcmp(name, "rte.deadline") == 0) || (std::strcmp(name, "nvm.copy_repaired") == 0))
        {
            CHECK(fm.resultOf(name) == ::dem::Result::NONE);
            continue;
        }
        bool const isFailed = ((failed != nullptr) && (std::strcmp(name, failed) == 0))
                              || ((failed2 != nullptr) && (std::strcmp(name, failed2) == 0));
        CHECK(fm.resultOf(name) == (isFailed ? ::dem::Result::FAILED : ::dem::Result::PASSED));
    }
}

void testFormatAndQuality(Memory& memory)
{
    {
        Start s(&memory); // erased memory
        CHECK(s.state(TRIM) == ::nvm::BlockState::EMPTY);
        CHECK(s.state(COUNTER) == ::nvm::BlockState::EMPTY);
        CHECK(trimBuffer == trimDefault);
        CHECK(std::memcmp(tableBuffer, tableDefault, 6U) == 0);
        for (size_t i = 0U; i < 3U; ++i)
        {
            CHECK(quality[i] == q(::nvm::Quality::DEFAULT));
            CHECK(qualifier[i] == q(::nvm::Quality::DEFAULT));
        }
        CHECK(s.events.count(::nvm::BlockEvent::COPY_REPAIRED) == 0U);
        CHECK(s.reported == NVM_DTCS); // every block read: each DTC of the NvM gets PASSED
        CHECK(s.faultMemory.reports == NVM_DTCS);
        expectOnlyFailed(s.faultMemory, nullptr);
        setValues(700U, 41U, 10U);
        CHECK(s.nvm.manager().writeAll(1000U) == 3U);
    }
    // integrity of the RTE table: ASIL blocks as two copies of the same record (id, size, counter 1), QM as one record
    CHECK(get16(memory, COUNTER_AT) == 0x2222U);
    CHECK(get16(memory, COUNTER_AT + 2U) == 4U);
    CHECK(get32(memory, COUNTER_AT + 4U) == 1U);
    CHECK(std::memcmp(&memory.bytes[COUNTER_AT], &memory.bytes[COUNTER_AT + 16U], 16U) == 0);
    CHECK(get32(memory, COUNTER_AT + 8U) == 41U);
    CHECK(std::memcmp(&memory.bytes[TABLE_AT], &memory.bytes[TABLE_AT + 20U], 18U) == 0);
    CHECK(get16(memory, TRIM_AT) == 0x1111U);
    CHECK(get16(memory, TRIM_AT + 4U) == 1U); // QM sequence (16 bit) right after id and size
    CHECK(get16(memory, TRIM_AT + 6U) == 700U);
    CHECK(get16(memory, TRIM_AT + 12U) == 0xFFFFU); // QM: the second place untouched
    {
        Start s(&memory);
        CHECK(trimBuffer == 700U);
        CHECK(counterBuffer == 41U);
        CHECK(tableBuffer[5] == 15U);
        for (size_t i = 0U; i < 3U; ++i)
        {
            CHECK(qualifier[i] == q(::nvm::Quality::STORED));
        }
        expectOnlyFailed(s.faultMemory, nullptr);
    }
}

void testOneCopyRepaired(Memory& memory)
{
    {
        Start s(&memory);
        CHECK(s.nvm.manager().injectCopyFault(COUNTER, 0U)); // nvm inject Counter a
    }
    Start s(&memory);
    CHECK(s.state(COUNTER) == ::nvm::BlockState::REPAIRED);
    CHECK(counterBuffer == 41U);
    CHECK(qualifier[COUNTER] == q(::nvm::Quality::STORED));
    CHECK(s.events.count(::nvm::BlockEvent::COPY_REPAIRED) == 1U);
    CHECK(s.events.firstBlock(::nvm::BlockEvent::COPY_REPAIRED) == COUNTER);
    CHECK(s.events.raised(COUNTER, ::nvm::BlockEvent::COPY_REPAIRED));
    CHECK(!s.events.raised(TABLE, ::nvm::BlockEvent::COPY_REPAIRED));
    expectOnlyFailed(s.faultMemory, "nvm.copy_repaired.Counter"); // not the one of Table
    CHECK(std::memcmp(&memory.bytes[COUNTER_AT], &memory.bytes[COUNTER_AT + 16U], 16U) == 0); // repaired
}

void testRepairRefused(Memory& memory)
{
    {
        Start s(&memory);
        CHECK(s.state(COUNTER) == ::nvm::BlockState::VALID); // repaired at the last start
        expectOnlyFailed(s.faultMemory, nullptr);
        CHECK(s.nvm.manager().injectCopyFault(COUNTER, 1U)); // nvm inject Counter b
    }
    memory.refuseWrites = true;
    {
        Start s(&memory);
        CHECK(s.state(COUNTER) == ::nvm::BlockState::DEGRADED);
        CHECK(counterBuffer == 41U);
        CHECK(qualifier[COUNTER] == q(::nvm::Quality::STORED));
        expectOnlyFailed(s.faultMemory, "nvm.copy_repair_failed.Counter");
    }
    memory.refuseWrites = false;
    Start s(&memory); // the repair goes through now
    CHECK(s.state(COUNTER) == ::nvm::BlockState::REPAIRED);
    expectOnlyFailed(s.faultMemory, "nvm.copy_repaired.Counter");
}

void testBothCopiesLost(Memory& memory)
{
    {
        Start s(&memory);
        CHECK(s.nvm.manager().injectCopyFault(TABLE, 0U));
        CHECK(s.nvm.manager().injectCopyFault(TABLE, 1U));
        CHECK(s.nvm.manager().injectCopyFault(COUNTER, 1U));
        CHECK(s.nvm.manager().injectCopyFault(COUNTER, 0U));
    }
    Start s(&memory);
    CHECK(s.state(TABLE) == ::nvm::BlockState::CORRUPT);
    CHECK(s.state(COUNTER) == ::nvm::BlockState::CORRUPT);
    CHECK(std::memcmp(tableBuffer, tableDefault, 6U) == 0);
    CHECK(counterBuffer == counterDefault);
    CHECK(qualifier[TABLE] == q(::nvm::Quality::UNSECURED));
    CHECK(qualifier[COUNTER] == q(::nvm::Quality::UNSECURED));
    CHECK(qualifier[TRIM] == q(::nvm::Quality::STORED));
    CHECK(s.events.count(::nvm::BlockEvent::DEFAULT_LOADED) == 2U);
    CHECK(s.events.firstBlock(::nvm::BlockEvent::DEFAULT_LOADED) == COUNTER); // the first of the two, not the last
    expectOnlyFailed(s.faultMemory, "nvm.default_loaded.Counter", "nvm.default_loaded.Table");
}

void testNotCheckedAndNoDtcs(Memory& memory)
{
    {
        Start s(nullptr);
        for (size_t i = 0U; i < 3U; ++i)
        {
            CHECK(qualifier[i] == q(::nvm::Quality::NO_MEMORY));
        }
        CHECK(s.reported == 0U); // nothing was checked, nothing reported
        CHECK(s.faultMemory.reports == 0U);
    }
    {
        // the memory ends inside Table: Trim and Counter are read and get results, Table (not checked) none
        Start s(&memory, PROJECT_DTCS, PROJECT_DTC_COUNT, 60U);
        CHECK(s.state(TABLE) == ::nvm::BlockState::OUTSIDE);
        CHECK(s.faultMemory.resultOf("nvm.default_loaded.Table") == ::dem::Result::NONE);
        CHECK(s.faultMemory.resultOf("nvm.copy_repaired.Table") == ::dem::Result::NONE);
        CHECK(s.faultMemory.resultOf("nvm.default_loaded.Trim") == ::dem::Result::PASSED);
        CHECK(s.reported == 5U); // Trim: default loaded, place defective; Counter: its three
    }
    char const* const others[] = {"rte.deadline", "calibration.segment"};
    Start s(&memory, others, 2U);
    CHECK(s.reported == 0U);
    CHECK(s.faultMemory.reports == 0U);
    CHECK(::async::LockType::depth == 0);
}

/** NVM-30: the newest place of the QM block Trim damaged - the previous value is loaded and reported, not silently. */
void testQmPlaceDefective(Memory& memory)
{
    {
        Start s(&memory);
        CHECK(trimBuffer == 700U);
        setValues(800U, 42U, 20U);
        CHECK(s.nvm.manager().writeAll(2000U) == 3U);
        CHECK(s.nvm.manager().status(TRIM).slot == 1U); // Trim: 700 in A (seq 1), 800 in B (seq 2)
        CHECK(s.nvm.manager().injectCopyFault(TRIM, 1U)); // nvm inject Trim b: the newest record
    }
    {
        Start s(&memory);
        CHECK(s.state(TRIM) == ::nvm::BlockState::VALID);
        CHECK(trimBuffer == 700U); // the previous value
        CHECK(qualifier[TRIM] == q(::nvm::Quality::STORED));
        CHECK(s.events.count(::nvm::BlockEvent::PLACE_DEFECTIVE) == 1U);
        CHECK(s.events.raised(TRIM, ::nvm::BlockEvent::PLACE_DEFECTIVE));
        CHECK(s.events.firstBlock(::nvm::BlockEvent::PLACE_DEFECTIVE) == TRIM);
        expectOnlyFailed(s.faultMemory, "nvm.place_defective.Trim");
        setValues(810U, 42U, 20U);
        CHECK(s.nvm.manager().writeAll(3000U) >= 1U); // goes to the defective place B
    }
    Start s(&memory);
    CHECK(trimBuffer == 810U);
    CHECK(s.events.count(::nvm::BlockEvent::PLACE_DEFECTIVE) == 0U);
    expectOnlyFailed(s.faultMemory, nullptr);
}

void testNames()
{
    char name[::nvm::DEM_EVENT_NAME_MAX + 1U];
    CHECK(::nvm::demEventName("nvm.default_loaded", "Trim", name, sizeof(name)));
    CHECK(std::strcmp(name, "nvm.default_loaded.Trim") == 0);
    CHECK(::nvm::demEventName("ab", "cd", name, 6U)); // "ab.cd" and the terminator fit exactly
    CHECK(!::nvm::demEventName("ab", "cde", name, 6U));
    CHECK(std::strcmp(::nvm::eventName(::nvm::BlockEvent::COPY_REPAIRED), "copy repaired") == 0);
    CHECK(std::strcmp(::nvm::qualityName(::nvm::Quality::UNSECURED), "unsecured") == 0);
    // the prefixes of TOOL-178 (tools/nxconfig/bswdtc.py PLAN), one per kind, each kind once
    CHECK(std::strcmp(::nvm::DEM_EVENTS[0].prefix, "nvm.default_loaded") == 0);
    CHECK(std::strcmp(::nvm::DEM_EVENTS[1].prefix, "nvm.copy_repair_failed") == 0);
    CHECK(std::strcmp(::nvm::DEM_EVENTS[2].prefix, "nvm.copy_repaired") == 0);
    CHECK(std::strcmp(::nvm::DEM_EVENTS[3].prefix, "nvm.place_defective") == 0); // NVM-30, tools/nxconfig +0x09nn
    CHECK(std::strcmp(::nvm::eventName(::nvm::BlockEvent::PLACE_DEFECTIVE), "place defective") == 0);
    for (size_t k = 0U; k < ::nvm::EVENT_KINDS; ++k)
    {
        unsigned found = 0U;
        for (::nvm::DemEvent const& e : ::nvm::DEM_EVENTS)
        {
            found += (static_cast<size_t>(e.event) == k) ? 1U : 0U;
        }
        CHECK(found == 1U);
    }
}
} // namespace

int main()
{
    Memory memory;
    testFormatAndQuality(memory);
    testOneCopyRepaired(memory);
    testRepairRefused(memory);
    testBothCopiesLost(memory);
    testNotCheckedAndNoDtcs(memory);
    testQmPlaceDefective(memory);
    testNames();
    CHECK(memory.initCalls == 0U); // the NvM never prepares the memory

    std::printf("NvmIntegrationHostTest: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
