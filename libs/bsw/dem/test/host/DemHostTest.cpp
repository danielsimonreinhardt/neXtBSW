// Host test of the fault memory core (NVM-03, libs/bsw/dem/include/dem/Dem.h). Own test profile, not the eval board:
// four DTCs (counter debouncing, time debouncing, a BSW reported event, a DTC with confirmation over two cycles),
// two enable conditions, two operation cycles (one autostart, one driven by the source), a memory with two entries.
// Checked: status byte transitions (ISO 14229-1 D.2.2) including clearing, fault detection counter with the jump over
// zero (D.6), confirmation per operation cycle (D.2.1), aging (D.7), enable conditions, ControlDTCSetting off (with the
// reports of BSW monitors discarded while it is off, COM-43), snapshots and extended data, overflow with priority,
// keep_oldest, persistence round trip and a damaged image.
// Build and run: libs/bsw/dem/test/test_dem.py, also with deliberate faults (mutations).

#include "dem/Dem.h"

#include <cstdio>
#include <cstring>

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

#define CHECK_EQ(a, b)                                                                                     \
    do                                                                                                     \
    {                                                                                                      \
        ++checks;                                                                                          \
        long const va_ = static_cast<long>(a);                                                             \
        long const vb_ = static_cast<long>(b);                                                             \
        if (va_ != vb_)                                                                                    \
        {                                                                                                  \
            ++failures;                                                                                    \
            std::printf("FAIL %s:%d: %s == %s (%ld != %ld)\n", __FILE__, __LINE__, #a, #b, va_, vb_);       \
        }                                                                                                  \
    } while (0)

struct TestLock
{
    TestLock() { ++depth; }

    ~TestLock() { --depth; }

    static int depth;
};

int TestLock::depth = 0;

using Manager = ::dem::Manager<TestLock>;
using ::dem::Result;
namespace st = ::dem::status;

// ---- test profile
constexpr uint16_t DID_A = 0x0110U; // 2 bytes
constexpr uint16_t DID_B = 0xF186U; // 1 byte
constexpr uint16_t DID_C = 0xFD00U; // 4 bytes

::dem::DataItem const SNAP_COUNTER[] = {{DID_A, 2U}, {DID_B, 1U}};
::dem::SnapshotRecord const SNAPSHOTS_COUNTER[] = {{0x01U, SNAP_COUNTER, 2U}};
::dem::ExtendedItem const EXT_COUNTER[]
    = {{::dem::Counter::OCCURRENCE, 0U, 1U},
       {::dem::Counter::AGING, 0U, 1U},
       {::dem::Counter::FAULT_DETECTION, 0U, 1U},
       {::dem::Counter::DID, DID_C, 4U},
       {::dem::Counter::FAILED_CYCLES, 0U, 1U},
       {::dem::Counter::CYCLES_SINCE_FIRST_FAILED, 0U, 1U}};
::dem::ExtendedRecord const EXTENDED_COUNTER[] = {{0x01U, EXT_COUNTER, 6U}};

::dem::DataItem const SNAP_TIME[] = {{DID_B, 1U}};
::dem::SnapshotRecord const SNAPSHOTS_TIME[] = {{0x02U, SNAP_TIME, 1U}};

enum : uint16_t
{
    COUNTER_DTC  = 0U, // cycle 0, condition 0, aging 3, priority 2
    TIME_DTC     = 1U, // cycle 0, no condition, priority 1
    REPORTED_DTC = 2U, // cycle 1 (driven by the source), priority 3, reported by the BSW
    TWO_CYCLES   = 3U  // cycle 0, confirmation after 2 cycles, condition 1, priority 3
};

//                     name        number     sev   prio conf aging cyc aging cond  kind      counter                  time
::dem::DtcConfig const DTCS[] = {
    {"Counter", 0x904001U, 0x41U, 2U, 1U, 3U, 0U, 0U, 0x1U, ::dem::DebounceKind::COUNTER, {10, -10, 1U, 1U, 0, 0}, {0U, 0U},
     SNAPSHOTS_COUNTER, 1U, EXTENDED_COUNTER, 1U, nullptr},
    {"Time", 0xC10016U, 0x01U, 1U, 1U, 0U, 0U, 0U, 0x0U, ::dem::DebounceKind::TIME, {0, 0, 0U, 0U, 0, 0}, {50U, 30U},
     SNAPSHOTS_TIME, 1U, nullptr, 0U, nullptr},
    {"Reported", 0x123456U, 0x81U, 3U, 1U, 0U, 1U, 1U, 0x0U, ::dem::DebounceKind::COUNTER, {3, -3, 1U, 1U, 0, 0},
     {0U, 0U}, nullptr, 0U, nullptr, 0U, "can.busoff.CAN1"},
    {"TwoCycles", 0x654321U, 0x21U, 3U, 2U, 0U, 0U, 0U, 0x2U, ::dem::DebounceKind::COUNTER, {2, -2, 1U, 1U, 0, 0},
     {0U, 0U}, nullptr, 0U, nullptr, 0U, nullptr},
};
constexpr uint16_t DTC_COUNT   = 4U;
constexpr uint8_t MAX_ENTRIES  = 2U;
constexpr uint16_t DATA_BYTES  = 7U; // Counter: 2 + 1 + 4

::dem::Config const CONFIG
    = {DTCS, DTC_COUNT, MAX_ENTRIES, true, 2U, 0x1U, 2U, 0x3U, 10U, DATA_BYTES};
::dem::Config const CONFIG_KEEP
    = {DTCS, DTC_COUNT, MAX_ENTRIES, false, 2U, 0x1U, 2U, 0x3U, 10U, DATA_BYTES};

/** Sampled events, conditions and the driven cycle as a table. */
class Source : public ::dem::ISource
{
public:
    Result results[DTC_COUNT] = {Result::NONE, Result::NONE, Result::NONE, Result::NONE};
    bool conditions[2]        = {true, true};
    uint8_t cycle1            = 1U;
    unsigned samples          = 0U;

    Result sample(uint16_t const dtc) override
    {
        ++samples;
        return results[dtc];
    }

    bool condition(uint8_t const c) override { return conditions[c]; }

    uint8_t cycleState(uint8_t const c) override { return (c == 1U) ? cycle1 : CYCLE_AUTOMATIC; }
};

class Reader : public ::dem::IDidReader
{
public:
    bool available   = true;
    unsigned reads   = 0U;
    uint8_t base     = 0x10U;
    int lockDepthMax = 0;

    bool read(uint16_t const did, uint8_t* const data, uint16_t const bytes) override
    {
        ++reads;
        lockDepthMax = (TestLock::depth > lockDepthMax) ? TestLock::depth : lockDepthMax;
        if (!available)
        {
            return false;
        }
        for (uint16_t i = 0U; i < bytes; ++i)
        {
            data[i] = static_cast<uint8_t>(base + (did & 0xFU) + i);
        }
        return true;
    }
};

struct Fixture
{
    ::dem::DtcState states[DTC_COUNT] = {};
    ::dem::Entry entries[MAX_ENTRIES] = {};
    uint8_t data[MAX_ENTRIES * DATA_BYTES] = {};
    uint8_t scratch[DATA_BYTES]            = {};
    Source source;
    Reader reader;
    Manager dem;

    explicit Fixture(::dem::Config const& config = CONFIG) : dem(config, states, entries, data, scratch)
    {
        dem.setDidReader(&reader);
    }

    void cycles(unsigned const n)
    {
        for (unsigned i = 0U; i < n; ++i)
        {
            dem.mainFunction(source);
        }
    }

    uint8_t status(uint16_t const dtc) const
    {
        ::dem::DtcInfo info{};
        (void)dem.dtcInfo(dtc, info);
        return info.status;
    }

    ::dem::DtcInfo info(uint16_t const dtc) const
    {
        ::dem::DtcInfo info{};
        (void)dem.dtcInfo(dtc, info);
        return info;
    }
};

void testInitialAndLoadOfEmptyImages()
{
    Fixture f;
    uint8_t events[::dem::eventsBlockBytes(DTC_COUNT)]                   = {};
    uint8_t entries[::dem::entriesBlockBytes(MAX_ENTRIES, DATA_BYTES)] = {};
    CHECK(!f.dem.load(events, sizeof(events), entries, sizeof(entries))); // NvM default: zeros
    f.dem.start();
    for (uint16_t i = 0U; i < DTC_COUNT; ++i)
    {
        CHECK_EQ(f.status(i), st::INITIAL); // 0x50
    }
    CHECK_EQ(sizeof(events), 24U);   // 4 + 4 * 5, the sizes tools/rtegen computes as well
    CHECK_EQ(sizeof(entries), 30U);  // 4 + 2 * (6 + 7)
    CHECK(!f.dem.memoryInfo().loaded);
    CHECK_EQ(f.dem.memoryInfo().cyclesRunning, 0x1U); // cycle 1 is driven by the source
    CHECK_EQ(f.dem.findDtc(0x654321U), 3);
    CHECK_EQ(f.dem.findDtc(0x111111U), -1);
    CHECK_EQ(f.dem.findReportedEvent("can.busoff.CAN1"), REPORTED_DTC);
    CHECK_EQ(f.dem.findReportedEvent("can.busoff.CAN0"), -1);
}

void testCounterDebounceAndStatus()
{
    Fixture f;
    f.dem.start();
    f.source.results[COUNTER_DTC] = Result::PREPASSED;
    f.cycles(3);
    CHECK_EQ(f.info(COUNTER_DTC).fdc, -38); // -3 of -10 -> -3 * 128 / 10
    CHECK_EQ(f.status(COUNTER_DTC), st::INITIAL);
    // D.6: the first failed sample after passed ones jumps over zero - prefailed at once
    f.source.results[COUNTER_DTC] = Result::PREFAILED;
    f.cycles(1);
    CHECK_EQ(f.info(COUNTER_DTC).fdc, 12); // 1 of 10 -> 127 / 10
    CHECK(f.info(COUNTER_DTC).fdc > 0);
    CHECK_EQ(f.status(COUNTER_DTC), st::INITIAL);
    f.cycles(8);
    CHECK_EQ(f.info(COUNTER_DTC).fdc, 114);
    CHECK_EQ(f.status(COUNTER_DTC), st::INITIAL); // not matured yet
    f.cycles(1);
    CHECK_EQ(f.info(COUNTER_DTC).fdc, 127);
    // matured: TF, TFTOC, PDTC, CDTC (threshold 1), TFSLC; TNCSLC and TNCTOC cleared
    CHECK_EQ(f.status(COUNTER_DTC), 0x2FU);
    CHECK_EQ(f.info(COUNTER_DTC).occurrence, 1U);
    CHECK(f.info(COUNTER_DTC).stored);
    f.cycles(5); // the failure stays: nothing changes, the occurrence does not count again
    CHECK_EQ(f.info(COUNTER_DTC).occurrence, 1U);
    CHECK_EQ(f.dem.memoryInfo().entriesUsed, 1U);
    // passed again: first sample jumps to zero minus one
    f.source.results[COUNTER_DTC] = Result::PREPASSED;
    f.cycles(1);
    CHECK_EQ(f.info(COUNTER_DTC).fdc, -12);
    CHECK_EQ(f.status(COUNTER_DTC), 0x2FU);
    f.cycles(9);
    CHECK_EQ(f.status(COUNTER_DTC), 0x2EU); // TF cleared
    // failing again: occurrence 2, still one entry
    f.source.results[COUNTER_DTC] = Result::FAILED;
    f.cycles(1);
    CHECK_EQ(f.status(COUNTER_DTC), 0x2FU);
    CHECK_EQ(f.info(COUNTER_DTC).occurrence, 2U);
    CHECK_EQ(f.info(COUNTER_DTC).failedCycles, 1U); // same cycle
    CHECK_EQ(f.dem.countByStatusMask(st::CONFIRMED_DTC), 1U);
    CHECK_EQ(f.dem.countByStatusMask(st::TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE), 3U);
}

void testCycleEndPendingAndAging()
{
    Fixture f;
    f.dem.start();
    f.source.results[COUNTER_DTC] = Result::FAILED;
    f.cycles(1);
    f.source.results[COUNTER_DTC] = Result::PASSED;
    f.cycles(1);
    CHECK_EQ(f.status(COUNTER_DTC), 0x2EU);
    f.dem.setCycle(0U, true); // restart: ends the cycle (failed in it: pending stays), starts a new one
    CHECK_EQ(f.status(COUNTER_DTC), 0x6CU); // TFTOC cleared, TNCTOC set
    CHECK_EQ(f.info(COUNTER_DTC).cyclesSinceFirstFailed, 1U);
    CHECK_EQ(f.info(COUNTER_DTC).aging, 0U);
    // a cycle that is not tested at all: no pending change, no aging
    f.dem.setCycle(0U, true);
    CHECK_EQ(f.status(COUNTER_DTC), 0x6CU);
    CHECK_EQ(f.info(COUNTER_DTC).aging, 0U);
    // tested and passed: pending cleared, aging 1
    f.cycles(1);
    CHECK_EQ(f.status(COUNTER_DTC), 0x2CU);
    f.dem.setCycle(0U, true);
    CHECK_EQ(f.status(COUNTER_DTC), 0x68U);
    CHECK_EQ(f.info(COUNTER_DTC).aging, 1U);
    f.cycles(1);
    f.dem.setCycle(0U, true);
    CHECK_EQ(f.info(COUNTER_DTC).aging, 2U);
    CHECK(f.info(COUNTER_DTC).stored);
    // a failure in between resets the aging counter
    f.source.results[COUNTER_DTC] = Result::FAILED;
    f.cycles(1);
    f.source.results[COUNTER_DTC] = Result::PASSED;
    f.cycles(1);
    f.dem.setCycle(0U, true);
    CHECK_EQ(f.info(COUNTER_DTC).aging, 0U);
    for (unsigned n = 0U; n < 3U; ++n)
    {
        f.cycles(1);
        f.dem.setCycle(0U, true);
    }
    // aged (threshold 3): confirmed cleared, entry released
    CHECK_EQ(f.info(COUNTER_DTC).aging, 3U);
    CHECK_EQ(f.status(COUNTER_DTC) & st::CONFIRMED_DTC, 0U);
    CHECK(!f.info(COUNTER_DTC).stored);
    CHECK_EQ(f.status(COUNTER_DTC), 0x60U); // TFSLC and TNCTOC remain
    CHECK_EQ(f.dem.memoryInfo().entriesUsed, 0U);
}

void testConfirmationOverTwoCycles()
{
    Fixture f;
    f.dem.start();
    f.source.results[TWO_CYCLES] = Result::FAILED;
    f.cycles(1);
    CHECK_EQ(f.status(TWO_CYCLES), 0x27U); // pending, not confirmed
    CHECK(!f.info(TWO_CYCLES).stored);
    f.cycles(3);
    CHECK_EQ(f.info(TWO_CYCLES).failedCycles, 1U);
    f.dem.setCycle(0U, true);
    f.cycles(1);
    CHECK_EQ(f.info(TWO_CYCLES).failedCycles, 2U);
    CHECK_EQ(f.status(TWO_CYCLES), 0x2FU); // confirmed in the second cycle with a failure
    CHECK(f.info(TWO_CYCLES).stored);
    // a pending DTC that passes a whole cycle starts counting again
    Fixture g;
    g.dem.start();
    g.source.results[TWO_CYCLES] = Result::FAILED;
    g.cycles(1);
    g.source.results[TWO_CYCLES] = Result::PASSED;
    g.dem.setCycle(0U, true);
    g.cycles(1);
    g.dem.setCycle(0U, true);
    CHECK_EQ(g.status(TWO_CYCLES) & st::PENDING_DTC, 0U);
    CHECK_EQ(g.info(TWO_CYCLES).failedCycles, 0U);
    g.source.results[TWO_CYCLES] = Result::FAILED;
    g.cycles(1);
    CHECK_EQ(g.status(TWO_CYCLES) & st::CONFIRMED_DTC, 0U);
}

void testEnableConditionsAndDtcSetting()
{
    Fixture f;
    f.dem.start();
    f.source.conditions[0]        = false;
    f.source.results[COUNTER_DTC] = Result::PREFAILED;
    f.cycles(20);
    CHECK_EQ(f.info(COUNTER_DTC).fdc, 0); // discarded, the counter stands
    CHECK_EQ(f.status(COUNTER_DTC), st::INITIAL);
    CHECK_EQ(f.dem.memoryInfo().conditions, 0x2U);
    f.source.conditions[0] = true;
    f.cycles(3);
    CHECK_EQ(f.info(COUNTER_DTC).fdc, 38);
    f.source.conditions[0] = false;
    f.cycles(3);
    CHECK_EQ(f.info(COUNTER_DTC).fdc, 38);
    f.source.conditions[0] = true;
    // ControlDTCSetting off: no transition at all, not even the start of a cycle, but clearing works
    f.dem.setDtcSetting(false);
    f.source.results[COUNTER_DTC] = Result::FAILED;
    f.source.results[TIME_DTC]    = Result::FAILED;
    f.cycles(5);
    CHECK_EQ(f.status(COUNTER_DTC), st::INITIAL);
    CHECK_EQ(f.status(TIME_DTC), st::INITIAL);
    CHECK(!f.dem.memoryInfo().dtcSetting);
    f.dem.setDtcSetting(true);
    f.cycles(1);
    CHECK_EQ(f.status(COUNTER_DTC), 0x2FU);
    f.dem.setDtcSetting(false);
    f.source.results[COUNTER_DTC] = Result::PASSED;
    f.cycles(2);
    f.dem.setCycle(0U, true);
    CHECK_EQ(f.status(COUNTER_DTC), 0x2FU);
    CHECK(f.dem.clear(0x904001U));
    CHECK_EQ(f.status(COUNTER_DTC), st::INITIAL);
    CHECK(!f.info(COUNTER_DTC).stored);
    CHECK_EQ(f.info(COUNTER_DTC).occurrence, 0U);
    CHECK(!f.dem.clear(0x904002U)); // no such DTC
}

// COM-43: while ControlDTCSetting is off a report of a BSW monitor is not taken over - a failure reported during the
// preprogramming step of a flash sequence is no DTC after 85 on; a report before 85 off keeps its level.
void testReportsWhileDtcSettingOff()
{
    Fixture f;
    f.source.cycle1 = 1U; // the cycle of the reported event runs
    f.dem.start();
    f.cycles(1);
    CHECK_EQ(f.dem.memoryInfo().cyclesRunning, 0x3U);
    CHECK_EQ(f.status(REPORTED_DTC), st::INITIAL);
    f.dem.setDtcSetting(false);
    f.dem.report(REPORTED_DTC, Result::FAILED);
    f.cycles(3);
    f.dem.report(REPORTED_DTC, Result::FAILED); // reported again, still off
    f.dem.setDtcSetting(true);
    f.cycles(3);
    CHECK_EQ(f.info(REPORTED_DTC).fdc, 0);
    CHECK_EQ(f.status(REPORTED_DTC) & st::TEST_FAILED, 0U);
    CHECK_EQ(f.status(REPORTED_DTC) & st::CONFIRMED_DTC, 0U);
    CHECK(!f.info(REPORTED_DTC).stored);
    // a failure reported with the setting on matures as before
    f.dem.report(REPORTED_DTC, Result::FAILED);
    f.cycles(1);
    CHECK((f.status(REPORTED_DTC) & st::TEST_FAILED) != 0U);
    // its level stays over 85 off and on; a pass reported while off is not taken over either
    f.dem.setDtcSetting(false);
    f.dem.report(REPORTED_DTC, Result::PASSED);
    f.cycles(2);
    f.dem.setDtcSetting(true);
    f.cycles(1);
    CHECK((f.status(REPORTED_DTC) & st::TEST_FAILED) != 0U);
    f.dem.report(REPORTED_DTC, Result::PASSED);
    f.cycles(1);
    CHECK_EQ(f.status(REPORTED_DTC) & st::TEST_FAILED, 0U);
}

void testTimeDebounce()
{
    Fixture f;
    f.dem.start();
    f.source.results[TIME_DTC] = Result::PREFAILED;
    f.cycles(1);
    CHECK_EQ(f.info(TIME_DTC).fdc, 0); // the direction starts: 0 ms
    f.cycles(4);
    CHECK_EQ(f.info(TIME_DTC).fdc, 101); // 40 of 50 ms
    CHECK_EQ(f.status(TIME_DTC), st::INITIAL);
    f.cycles(1);
    CHECK_EQ(f.info(TIME_DTC).fdc, 127);
    CHECK_EQ(f.status(TIME_DTC), 0x2FU);
    f.source.results[TIME_DTC] = Result::PREPASSED;
    f.cycles(3);
    CHECK_EQ(f.info(TIME_DTC).fdc, -85); // 20 of 30 ms
    CHECK_EQ(f.status(TIME_DTC), 0x2FU);
    f.cycles(1);
    CHECK_EQ(f.status(TIME_DTC), 0x2EU);
    // no result: nothing moves
    f.source.results[TIME_DTC] = Result::NONE;
    f.cycles(3);
    CHECK_EQ(f.info(TIME_DTC).fdc, -128);
}

void testReportedEventAndDrivenCycle()
{
    Fixture f;
    f.source.cycle1 = 0U;
    f.dem.start();
    f.cycles(1);
    CHECK_EQ(f.dem.memoryInfo().cyclesRunning, 0x1U);
    f.dem.report(REPORTED_DTC, Result::FAILED);
    f.cycles(2);
    CHECK_EQ(f.status(REPORTED_DTC), st::INITIAL); // its cycle does not run: discarded
    f.source.cycle1 = 1U;
    f.dem.report(REPORTED_DTC, Result::PREFAILED);
    f.cycles(2);
    CHECK_EQ(f.dem.memoryInfo().cyclesRunning, 0x3U);
    CHECK_EQ(f.info(REPORTED_DTC).fdc, 84); // level PREFAILED sampled twice (2 of 3)
    f.cycles(1);
    CHECK_EQ(f.status(REPORTED_DTC), 0x2FU);
    // a failure between two cycles is sampled once even if passed is reported before the cycle
    f.dem.report(REPORTED_DTC, Result::PASSED);
    f.cycles(1);
    CHECK_EQ(f.status(REPORTED_DTC), 0x2EU);
    f.dem.report(REPORTED_DTC, Result::PREFAILED);
    f.dem.report(REPORTED_DTC, Result::PREPASSED);
    f.cycles(1);
    CHECK_EQ(f.info(REPORTED_DTC).fdc, 42); // jumped over zero: prefailed
    f.cycles(1);
    CHECK_EQ(f.info(REPORTED_DTC).fdc, -42);
    // the source ends the cycle: tested and failed in it, pending stays
    f.source.cycle1 = 0U;
    f.cycles(1);
    CHECK_EQ(f.dem.memoryInfo().cyclesRunning, 0x1U);
    CHECK_EQ(f.status(REPORTED_DTC) & st::PENDING_DTC, st::PENDING_DTC);
    CHECK_EQ(f.source.samples % 3U, 0U); // the reported event is never sampled from the source
    f.dem.report(99U, Result::FAILED);   // outside: ignored
    f.dem.report(COUNTER_DTC, Result::FAILED); // not a reported event: ignored
    f.cycles(1);
    CHECK_EQ(f.status(COUNTER_DTC), st::INITIAL);
}

void testSnapshotsAndExtendedData()
{
    Fixture f;
    f.dem.start();
    uint8_t out[16] = {};
    CHECK_EQ(f.dem.readSnapshot(COUNTER_DTC, 0x01U, out, sizeof(out)), 0);  // nothing stored
    CHECK_EQ(f.dem.readSnapshot(COUNTER_DTC, 0x02U, out, sizeof(out)), -1); // no such record
    CHECK_EQ(f.dem.readExtended(COUNTER_DTC, 0x02U, out, sizeof(out)), -1);
    CHECK_EQ(f.dem.readExtended(COUNTER_DTC, 0x01U, out, sizeof(out)), 9);  // counters without an entry
    CHECK_EQ(out[3], 0xFFU);                                                // identifier not stored
    f.source.results[COUNTER_DTC] = Result::FAILED;
    f.cycles(1);
    CHECK_EQ(f.reader.reads, 3U); // A, B, C once, at the confirmation
    CHECK_EQ(f.reader.lockDepthMax, 0); // read without the lock of the memory
    CHECK_EQ(f.dem.readSnapshot(COUNTER_DTC, 0x01U, out, sizeof(out)), 3);
    CHECK_EQ(out[0], 0x10U); // DID_A 0x0110: base + 0
    CHECK_EQ(out[1], 0x11U);
    CHECK_EQ(out[2], 0x16U); // DID_B 0xF186: base + 6
    f.reader.base = 0x80U;   // later values do not change the stored snapshot
    f.cycles(3);
    CHECK_EQ(f.dem.readSnapshot(COUNTER_DTC, 0x01U, out, sizeof(out)), 3);
    CHECK_EQ(out[0], 0x10U);
    CHECK_EQ(f.dem.readExtended(COUNTER_DTC, 0x01U, out, sizeof(out)), 9);
    CHECK_EQ(out[0], 1U);    // occurrence
    CHECK_EQ(out[1], 0U);    // aging
    CHECK_EQ(out[2], 127U);  // fault detection counter
    CHECK_EQ(out[3], 0x10U); // DID_C 0xFD00: base + 0, 4 bytes
    CHECK_EQ(out[6], 0x13U);
    CHECK_EQ(out[7], 1U);    // failed cycles
    CHECK_EQ(out[8], 0U);    // cycles since first failed
    CHECK_EQ(f.dem.readExtended(COUNTER_DTC, 0x01U, out, 4U), 3); // a value that does not fit is left out
    // reader without data: 0xFF
    Fixture g;
    g.reader.available = false;
    g.dem.start();
    g.source.results[TIME_DTC] = Result::FAILED;
    g.cycles(1);
    CHECK_EQ(g.dem.readSnapshot(TIME_DTC, 0x02U, out, sizeof(out)), 1);
    CHECK_EQ(out[0], 0xFFU);
}

void testOverflow()
{
    // replace_oldest: two entries, three DTCs; the least important (largest priority number), oldest one goes
    Fixture f;
    f.dem.start();
    f.source.results[TWO_CYCLES] = Result::FAILED; // priority 3
    f.cycles(1);
    f.dem.setCycle(0U, true);
    f.cycles(1);                                   // confirmed in its second cycle
    CHECK(f.info(TWO_CYCLES).stored);
    f.source.results[COUNTER_DTC] = Result::FAILED; // priority 2
    f.cycles(1);
    CHECK_EQ(f.dem.memoryInfo().entriesUsed, 2U);
    f.source.results[TIME_DTC] = Result::FAILED; // priority 1: displaces TwoCycles (priority 3)
    f.cycles(1);
    CHECK(f.info(TIME_DTC).stored);
    CHECK(!f.info(TWO_CYCLES).stored);
    CHECK_EQ(f.status(TWO_CYCLES) & st::CONFIRMED_DTC, 0U);
    CHECK_EQ(f.dem.memoryInfo().displaced, 1U);
    // TwoCycles fails again in a new cycle: priority 3 cannot displace priority 1 or 2
    f.dem.setCycle(0U, true);
    f.cycles(1);
    CHECK(!f.info(TWO_CYCLES).stored);
    CHECK_EQ(f.dem.memoryInfo().overflows, 1U);
    CHECK_EQ(f.status(TWO_CYCLES) & st::CONFIRMED_DTC, 0U);
    f.cycles(5); // a failure that stays does not retry every cycle
    CHECK_EQ(f.dem.memoryInfo().overflows, 1U);
    // equal priority: the oldest entry goes (Reported stored before TwoCycles, both priority 3)
    Fixture o;
    o.dem.start();
    o.dem.report(REPORTED_DTC, Result::FAILED);
    o.cycles(1);
    o.source.results[TWO_CYCLES] = Result::FAILED;
    o.cycles(1);
    o.dem.setCycle(0U, true);
    o.cycles(1);
    CHECK(o.info(REPORTED_DTC).stored);
    CHECK(o.info(TWO_CYCLES).stored);
    o.source.results[COUNTER_DTC] = Result::FAILED;
    o.cycles(1);
    CHECK(o.info(COUNTER_DTC).stored);
    CHECK(!o.info(REPORTED_DTC).stored);
    CHECK(o.info(TWO_CYCLES).stored);
    // keep_oldest: the new one is not stored
    Fixture k(CONFIG_KEEP);
    k.dem.start();
    k.source.results[COUNTER_DTC] = Result::FAILED;
    k.source.results[TWO_CYCLES]  = Result::FAILED;
    k.cycles(1);
    k.dem.setCycle(0U, true);
    k.cycles(1);
    k.source.results[TIME_DTC] = Result::FAILED;
    k.cycles(1);
    CHECK(!k.info(TIME_DTC).stored);
    CHECK_EQ(k.status(TIME_DTC) & st::CONFIRMED_DTC, 0U);
    CHECK_EQ(k.dem.memoryInfo().overflows, 1U);
    CHECK_EQ(k.dem.memoryInfo().displaced, 0U);
}

void testPersistence()
{
    uint8_t events[::dem::eventsBlockBytes(DTC_COUNT)]                   = {};
    uint8_t entries[::dem::entriesBlockBytes(MAX_ENTRIES, DATA_BYTES)] = {};
    {
        Fixture f;
        f.dem.start();
        (void)f.dem.takeEventsDirty();
        f.source.results[COUNTER_DTC] = Result::FAILED;
        f.source.results[TIME_DTC]    = Result::FAILED;
        f.cycles(1);
        CHECK(f.dem.takeEventsDirty());
        CHECK(f.dem.takeEntriesDirty());
        CHECK(!f.dem.takeEntriesDirty()); // once
        f.dem.stop();                      // run-down: the cycle ends
        f.dem.serializeEvents(events, sizeof(events));
        f.dem.serializeEntries(entries, sizeof(entries));
    }
    CHECK_EQ(events[0], 0xDEU);
    CHECK_EQ(events[1], 1U);
    CHECK_EQ(events[2], DTC_COUNT);
    CHECK_EQ(events[4], 0x2FU);
    CHECK_EQ(entries[1], 2U);
    {
        Fixture f;
        CHECK(f.dem.load(events, sizeof(events), entries, sizeof(entries)));
        f.dem.start();
        CHECK(f.dem.memoryInfo().loaded);
        CHECK_EQ(f.status(COUNTER_DTC), 0x6DU); // new cycle: TFTOC cleared, TNCTOC set, TF kept
        CHECK_EQ(f.info(COUNTER_DTC).occurrence, 1U);
        CHECK_EQ(f.info(COUNTER_DTC).cyclesSinceFirstFailed, 1U);
        CHECK(f.info(COUNTER_DTC).stored);
        CHECK(f.info(TIME_DTC).stored);
        uint8_t out[4] = {};
        CHECK_EQ(f.dem.readSnapshot(COUNTER_DTC, 0x01U, out, sizeof(out)), 3);
        CHECK_EQ(out[2], 0x16U);
        // the order of storage survives: a displacement still takes the oldest
        CHECK_EQ(f.dem.memoryInfo().entriesUsed, 2U);
    }
    {
        // damaged header or other layout: starts cleared
        uint8_t bad[sizeof(events)];
        (void)std::memcpy(bad, events, sizeof(bad));
        bad[1] = 7U;
        Fixture f;
        CHECK(!f.dem.load(bad, sizeof(bad), entries, sizeof(entries)));
        f.dem.start();
        CHECK_EQ(f.status(COUNTER_DTC), st::INITIAL);
        CHECK(!f.info(COUNTER_DTC).stored); // entries only with their events
        CHECK(!f.dem.load(events, sizeof(events) - 1U, entries, sizeof(entries)));
    }
    {
        // an entry of an unknown DTC is dropped
        uint8_t bad[sizeof(entries)];
        (void)std::memcpy(bad, entries, sizeof(bad));
        bad[4] = 9U;
        bad[5] = 0U;
        Fixture f;
        (void)f.dem.load(events, sizeof(events), bad, sizeof(bad));
        CHECK_EQ(f.dem.memoryInfo().entriesUsed, 1U);
    }
}

void testClearAll()
{
    Fixture f;
    f.dem.start();
    f.source.results[COUNTER_DTC] = Result::FAILED;
    f.source.results[TIME_DTC]    = Result::FAILED;
    f.cycles(1);
    (void)f.dem.takeEventsDirty();
    (void)f.dem.takeEntriesDirty();
    CHECK(f.dem.clear(::dem::GROUP_ALL_DTCS));
    CHECK(f.dem.takeEventsDirty());
    CHECK(f.dem.takeEntriesDirty());
    for (uint16_t i = 0U; i < DTC_COUNT; ++i)
    {
        CHECK_EQ(f.status(i), st::INITIAL);
        CHECK_EQ(f.info(i).fdc, 0);
    }
    CHECK_EQ(f.dem.memoryInfo().entriesUsed, 0U);
    // the failure is still there: it matures again after the clear
    f.cycles(1);
    CHECK_EQ(f.status(COUNTER_DTC), 0x2FU);
    CHECK_EQ(f.info(COUNTER_DTC).occurrence, 1U);
}

} // namespace

int main()
{
    testInitialAndLoadOfEmptyImages();
    testCounterDebounceAndStatus();
    testCycleEndPendingAndAging();
    testConfirmationOverTwoCycles();
    testEnableConditionsAndDtcSetting();
    testReportsWhileDtcSettingOff();
    testTimeDebounce();
    testReportedEventAndDrivenCycle();
    testSnapshotsAndExtendedData();
    testOverflow();
    testPersistence();
    testClearAll();
    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
