/* NVM-05: host test of the operating time and reset statistics of the application (stats/EcuStatistics.h,
 * stats/EcuStatisticsNvm.h, stats/EcuStatisticsConsole.h) - the core alone and on the NvM core of libs/bsw/nvm with a
 * virtual EEPROM in RAM, over several power cycles. Own test profile, not the eval board: the block EcuStatistics
 * (52 bytes, qm, write CHANGE, min interval 1 h as the project value) next to a block Other; the MCU and supply shares
 * of the start reasons are given by the test (an MCU without reset status and a supply without SBC included).
 *
 * What it shows:
 * - every cause bit of a source counts once, a source that cannot tell counts "unknown", counters saturate;
 * - a start is counted once, with the supply share when it comes within START_SETTLE_MS (one write per start), else
 *   without it and the supply share later;
 * - the operating time runs in 100 ms cycles, also over the wrap of the 32-bit millisecond time, goes to the image at
 *   most every IMAGE_PERIOD_MS, and the NvM writes it at most once per min interval; the run-down (flush + writeAll)
 *   stores it to the second; a power cut loses at most the min interval;
 * - write budget: 24 h of operation with one start = 1 + 24 writes at most; 1000 short drive cycles = 2 per cycle;
 * - a record of another format restarts at zero, a block of another size or no block is not used (RAM only);
 * - NVM-34: the supply row counts only starts of the supply itself - an MCU-only start (eval board: "lc reboot" with
 *   JP1 open, the SBC reports an old reset source) counts in the MCU row only, an MCU reset the SBC sees over RSTN
 *   (JP1 closed) in both rows;
 * - the console text.
 * Deliberate faults of the headers must fail it: test/test_ecu_statistics.py.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iecu/application/src/systems -Iecu/application/src/systems/test/host -Ilibs/bsw/nvm/include \
 *       -Ilibs/bsw/ecuState/include -isystem external/openbsw/libs/bsw/bsp/include \
 *       ecu/application/src/systems/test/host/EcuStatisticsHostTest.cpp -o build/host/ecustatistics.exe
 */

#include "FakeEeprom.h"
#include "stats/EcuStatisticsConsole.h"
#include "stats/StartReasonHandover.h"

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

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

namespace wakeup = ::ecustate::wakeup;
using ::ecustats::Cause;
using ::ecustats::Record;
using ::ecustats::Statistics;

size_t idx(Cause const c) { return static_cast<size_t>(c); }

struct TestLock
{
    TestLock() { ++depth; }

    ~TestLock() { --depth; }

    TestLock(TestLock const&)            = delete;
    TestLock& operator=(TestLock const&) = delete;

    static int depth;
};

int TestLock::depth = 0;

using Manager = ::nvm::Manager<TestLock>;
using Stats   = ::ecustats::NvmStatistics<Manager, TestLock>;

constexpr uint32_t HOUR_MS = 3600000U;
constexpr uint32_t CYCLE   = 100U;

// ------------------------------------------------------------------------------------------------- core alone

void testCountReasons()
{
    uint16_t c[::ecustats::CAUSE_COUNT] = {};
    ::ecustats::countReasons(c, wakeup::POWER);
    CHECK(c[idx(Cause::POWER)] == 1U);
    ::ecustats::countReasons(c, wakeup::WATCHDOG | wakeup::RESET);
    CHECK((c[idx(Cause::WATCHDOG)] == 1U) && (c[idx(Cause::RESET)] == 1U));
    ::ecustats::countReasons(c, wakeup::SUPPLY);
    ::ecustats::countReasons(c, wakeup::TIMER);
    ::ecustats::countReasons(c, wakeup::canBus(3U) | wakeup::canBus(0U)); // two buses: one CAN start
    ::ecustats::countReasons(c, wakeup::local(5U));
    CHECK(c[idx(Cause::SUPPLY)] == 1U);
    CHECK(c[idx(Cause::TIMER)] == 1U);
    CHECK(c[idx(Cause::CAN)] == 1U);
    CHECK(c[idx(Cause::LOCAL)] == 1U);
    CHECK(c[idx(Cause::UNKNOWN)] == 0U);
    ::ecustats::countReasons(c, wakeup::NONE);    // MCU without reset status
    ::ecustats::countReasons(c, wakeup::UNKNOWN); // supply that cannot tell
    CHECK(c[idx(Cause::UNKNOWN)] == 2U);
    ::ecustats::countReasons(c, wakeup::UNKNOWN | wakeup::POWER);
    CHECK((c[idx(Cause::UNKNOWN)] == 3U) && (c[idx(Cause::POWER)] == 2U));
    // saturation
    uint16_t s[::ecustats::CAUSE_COUNT] = {};
    s[idx(Cause::WATCHDOG)]             = 0xFFFEU;
    ::ecustats::countReasons(s, wakeup::WATCHDOG);
    ::ecustats::countReasons(s, wakeup::WATCHDOG);
    CHECK(s[idx(Cause::WATCHDOG)] == 0xFFFFU);
    CHECK(s[idx(Cause::POWER)] == 0U);
    uint32_t big = 0xFFFFFFFFUL;
    ::ecustats::increment(big);
    CHECK(big == 0xFFFFFFFFUL);
    CHECK(std::strcmp(::ecustats::causeName(idx(Cause::CAN)), "can") == 0);
    CHECK(std::strcmp(::ecustats::causeName(99U), "?") == 0);
}

void testLoad()
{
    Statistics st;
    Record stored{};
    CHECK(st.load(stored) == ::ecustats::Origin::FRESH);
    CHECK(st.record().format == ::ecustats::FORMAT);
    stored.format           = ::ecustats::FORMAT;
    stored.operatingSeconds = 1234U;
    stored.starts           = 7U;
    stored.mcu[2]           = 3U;
    Statistics loaded;
    CHECK(loaded.load(stored) == ::ecustats::Origin::LOADED);
    CHECK((loaded.record().operatingSeconds == 1234U) && (loaded.record().starts == 7U));
    CHECK(loaded.record().mcu[2] == 3U);
    stored.format = 0x4E580400UL; // another record version
    Statistics other;
    CHECK(other.load(stored) == ::ecustats::Origin::FORMAT);
    CHECK((other.record().operatingSeconds == 0U) && (other.record().starts == 0U));
    CHECK(other.record().format == ::ecustats::FORMAT);
}

void testStartWithSupply()
{
    Statistics st;
    (void)st.load(Record{});
    st.start(1000U, wakeup::POWER);
    CHECK(!st.cycle(1100U)); // supply share not known yet, settle time not over
    CHECK(!st.startCounted());
    st.supplyReasons(wakeup::POWER | wakeup::canBus(1U));
    CHECK(st.cycle(1200U)); // one hand-over with both shares
    CHECK(st.startCounted() && st.supplyCounted());
    CHECK(st.record().starts == 1U);
    CHECK(st.record().mcu[idx(Cause::POWER)] == 1U);
    CHECK((st.record().supply[idx(Cause::POWER)] == 1U) && (st.record().supply[idx(Cause::CAN)] == 1U));
    CHECK(st.record().lastMcuReasons == wakeup::POWER);
    CHECK(st.record().lastSupplyReasons == (wakeup::POWER | wakeup::canBus(1U)));
    CHECK(!st.cycle(1300U)); // nothing new
    st.supplyReasons(wakeup::WATCHDOG); // a second report changes nothing
    CHECK(!st.cycle(1400U));
    CHECK(st.record().supply[idx(Cause::WATCHDOG)] == 0U);
    CHECK(st.record().starts == 1U);
}

void testStartWithoutSupply()
{
    Statistics st;
    (void)st.load(Record{});
    st.start(0U, wakeup::NONE); // MCU cannot tell
    uint32_t now = 0U;
    unsigned handovers = 0U;
    for (; now < ::ecustats::START_SETTLE_MS - CYCLE; now += CYCLE)
    {
        handovers += st.cycle(now) ? 1U : 0U;
    }
    CHECK(handovers == 0U);
    CHECK(!st.startCounted());
    now = ::ecustats::START_SETTLE_MS;
    CHECK(st.cycle(now)); // counted without the supply share
    CHECK(st.startCounted() && (!st.supplyCounted()));
    CHECK(st.record().mcu[idx(Cause::UNKNOWN)] == 1U);
    CHECK(st.record().lastSupplyReasons == 0U);
    st.supplyReasons(wakeup::UNKNOWN); // supply without SBC that cannot tell, late
    CHECK(st.cycle(now + CYCLE));
    CHECK(st.supplyCounted());
    CHECK(st.record().supply[idx(Cause::UNKNOWN)] == 1U);
    CHECK(st.record().starts == 1U);
}

void testMcuOnlyStart()
{
    // NVM-34: the supply did not restart for this start - counted at once, MCU row only, supply row as it is
    Statistics st;
    (void)st.load(Record{});
    st.start(0U, wakeup::RESET);
    st.supplyReasons(::ecustats::SUPPLY_NOT_RESTARTED);
    CHECK(st.cycle(CYCLE)); // one hand-over, no wait for the settle time
    CHECK(st.startCounted() && st.supplyCounted() && (!st.supplyRestarted()));
    CHECK(st.record().starts == 1U);
    CHECK(st.record().mcu[idx(Cause::RESET)] == 1U);
    uint32_t supplyTotal = 0U;
    for (size_t c = 0U; c < ::ecustats::CAUSE_COUNT; ++c)
    {
        supplyTotal += st.record().supply[c];
    }
    CHECK(supplyTotal == 0U); // not "reset", not "power" (old RSS), not "unknown"
    CHECK(st.record().lastSupplyReasons == 0U);
    st.supplyReasons(wakeup::POWER); // a later report changes nothing
    CHECK(!st.cycle(2U * CYCLE));
    CHECK(st.record().supply[idx(Cause::POWER)] == 0U);
    // the hand-over word of the ECU state system: restarted false -> the marker, true -> the reasons (0 -> UNKNOWN)
    namespace handover = ::ecustats::handover;
    handover::publishSupplyStartReasons(wakeup::RESET, false);
    CHECK(handover::supplyStartReasonsOrZero() == ::ecustats::SUPPLY_NOT_RESTARTED);
    handover::publishSupplyStartReasons(wakeup::RESET, true);
    CHECK(handover::supplyStartReasonsOrZero() == wakeup::RESET);
    handover::publishSupplyStartReasons(wakeup::NONE, true);
    CHECK(handover::supplyStartReasonsOrZero() == wakeup::UNKNOWN);
    handover::publishSupplyStartReasons(wakeup::POWER);
    CHECK(handover::supplyStartReasonsOrZero() == wakeup::POWER); // default: the supply restarted
    handover::supplyStartReasons.store(0U);
}

void testOperatingTime()
{
    Statistics st;
    (void)st.load(Record{});
    // start close to the wrap of the 32-bit millisecond time
    uint32_t now = 0xFFFFFFFFUL - 30000U;
    st.start(now, wakeup::POWER);
    st.supplyReasons(wakeup::POWER);
    unsigned handovers = 0U;
    for (unsigned i = 0U; i < 6000U; ++i) // 10 minutes in 100 ms cycles
    {
        now += CYCLE;
        handovers += st.cycle(now) ? 1U : 0U;
    }
    CHECK(st.record().operatingSeconds == 600U);
    CHECK(handovers == 10U); // the start, then every 60 s: 9 more within 600 s (the last one at 540..599 s)
    CHECK(st.unsavedSeconds() == 60U); // last hand-over at 540.1 s, the next one is due at 600.1 s
    // run-down: the rest up to now, then nothing more
    now += 450U;
    CHECK(st.flush(now));
    CHECK(st.unsavedSeconds() == 0U);
    CHECK(!st.flush(now + 500U)); // 950 ms, still the same second
    CHECK(st.flush(now + 600U));  // 1050 ms: one more second
    CHECK(st.record().operatingSeconds == 601U);
    // saturation of the operating time
    Record full{};
    full.format           = ::ecustats::FORMAT;
    full.operatingSeconds = 0xFFFFFFFFUL - 1U;
    Statistics sat;
    (void)sat.load(full);
    sat.start(0U, wakeup::POWER);
    (void)sat.flush(10000U);
    CHECK(sat.record().operatingSeconds == 0xFFFFFFFFUL);
    // nothing before start()
    Statistics idle;
    CHECK(!idle.cycle(100U));
    CHECK(!idle.flush(100U));
}

// --------------------------------------------------------------------------------------- on the NvM core

constexpr size_t STATS = 0U, OTHER = 1U, COUNT = 2U;
constexpr uint32_t OTHER_AT = 2U * ::nvm::slotBytes(::ecustats::RECORD_BYTES); // 120

/** The "generated" RTE side of the profile: images, defaults, changed flags, block table. */
struct Profile
{
    Record image{};
    Record const defaults{};
    uint32_t otherImage         = 0U;
    uint32_t const otherDefault = 0U;
    bool changed[COUNT]         = {false, false};
    ::nvm::BlockConfig configs[COUNT];
    ::nvm::BlockStatus status[COUNT];
    uint8_t scratch[64];
    Manager manager;

    explicit Profile(uint16_t const statsSize = ::ecustats::RECORD_BYTES, char const* const name = "EcuStatistics")
    : configs{
        ::nvm::BlockConfig{name, 0x5151U, statsSize, 0U, &image, &defaults, &changed[STATS], ::nvm::WritePolicy::CHANGE,
                           HOUR_MS, ::nvm::Integrity::QM, nullptr},
        ::nvm::BlockConfig{"Other", 0x7272U, 4U, OTHER_AT, &otherImage, &otherDefault, &changed[OTHER],
                           ::nvm::WritePolicy::SHUTDOWN, 0U, ::nvm::Integrity::QM, nullptr}}
    , status()
    , scratch()
    , manager(configs, status, COUNT, scratch, sizeof(scratch))
    {}
};

using Eeprom = FakeEeprom<256U>;

/** One power cycle: read the blocks, start the statistics. */
struct Ecu
{
    Profile profile;
    Stats stats;
    uint32_t now;

    Ecu(Eeprom* const memory, uint32_t const startMs, uint32_t const mcu) : profile(), stats(), now(startMs)
    {
        profile.manager.attach(memory, 256U);
        profile.manager.readAll();
        stats.start(profile.manager, COUNT, now, mcu);
    }

    /** \p ms in 100 ms cycles; \p supply from \p supplyAtMs on. */
    void run(uint32_t const ms, uint32_t const supply = wakeup::POWER, uint32_t const supplyAfterMs = 0U)
    {
        uint32_t const begin = now;
        for (uint32_t t = 0U; t < ms; t += CYCLE)
        {
            now += CYCLE;
            stats.cycle(now, ((now - begin) >= supplyAfterMs) ? supply : 0U);
            (void)profile.manager.mainFunction(now);
        }
    }

    void rundown()
    {
        stats.flush(now);
        (void)profile.manager.writeAll(now);
    }

    uint32_t writes() const { return profile.manager.status(STATS).writes; }
};

void testPowerCycles()
{
    Eeprom memory;
    // 1st start: erased memory, power-on of MCU and supply; 2 h and the run-down
    {
        Ecu ecu(&memory, 0U, wakeup::POWER);
        CHECK(ecu.stats.binding() == ::ecustats::Binding::BOUND);
        CHECK(ecu.stats.statistics().origin() == ::ecustats::Origin::FRESH);
        ecu.run(1000U, wakeup::POWER, 300U);
        CHECK(ecu.writes() == 1U); // the start, written at once (first CHANGE write)
        ecu.run(2U * HOUR_MS - 1000U);
        CHECK(ecu.writes() == 2U); // + after 1 h (the next one would be due at 2 h + 0.3 s)
        ecu.rundown();
        CHECK(ecu.writes() == 3U); // the run-down stores the rest
        CHECK(ecu.stats.statistics().record().operatingSeconds == 7200U);
        CHECK(TestLock::depth == 0);
    }
    // 2nd start: watchdog reset seen by both; the record is back; 30 min, then a power cut without run-down
    {
        Ecu ecu(&memory, 5000U, wakeup::WATCHDOG | wakeup::RESET);
        CHECK(ecu.stats.statistics().origin() == ::ecustats::Origin::LOADED);
        CHECK(ecu.stats.statistics().record().operatingSeconds == 7200U);
        CHECK(ecu.stats.statistics().record().starts == 1U);
        ecu.run(30U * 60000U, wakeup::WATCHDOG, 200U);
        CHECK(ecu.writes() == 1U); // only the start: the min interval of 1 h is not over
        Record const& r = ecu.stats.statistics().record();
        CHECK(r.starts == 2U);
        CHECK((r.mcu[idx(Cause::WATCHDOG)] == 1U) && (r.mcu[idx(Cause::RESET)] == 1U));
        CHECK(r.supply[idx(Cause::WATCHDOG)] == 1U);
        CHECK(r.operatingSeconds == 7200U + 1800U);
    }
    // 3rd start: the 30 min are lost (power cut within the min interval), the start counts are not
    {
        Ecu ecu(&memory, 0U, wakeup::POWER | wakeup::SUPPLY);
        Record const& r = ecu.stats.statistics().record();
        CHECK(r.starts == 2U);
        CHECK(r.operatingSeconds == 7200U); // at most the min interval lost
        CHECK(r.mcu[idx(Cause::WATCHDOG)] == 1U);
        ecu.run(2000U, wakeup::POWER, 0U);
        CHECK(r.starts == 3U);
        CHECK((r.mcu[idx(Cause::POWER)] == 2U) && (r.mcu[idx(Cause::SUPPLY)] == 1U));
        CHECK(r.supply[idx(Cause::POWER)] == 2U);
        ecu.rundown();
    }
    {
        Ecu ecu(&memory, 0U, wakeup::POWER);
        CHECK(ecu.stats.statistics().record().starts == 3U);
        CHECK(ecu.stats.statistics().record().operatingSeconds == 7202U);
        // the console text of this state
        ::ecustats::Snapshot s{};
        ecu.stats.snapshot(s);
        CHECK(s.binding == ::ecustats::Binding::BOUND);
        CHECK(std::strcmp(s.blockState, "stored") == 0 || std::strcmp(s.blockState, "valid") == 0);
        CHECK(std::strcmp(s.policy, "change") == 0);
        CHECK(s.minIntervalMs == HOUR_MS);
    }
}

/**
 * NVM-34, board finding 2026-10-02 (NVM-05 step 5): "lc reboot" on the eval board. The MCU share is RESET (RCM_SRS
 * software reset) in both cases; the supply share is what the role power_supply hands over at the run level
 * (ecustats::handover, filled from IPowerSupply::getStartupWakeupSources() and restartedForThisStart() - the
 * UJA113x side of both cases is in libs/bsw/sbcUja113x/test/host/Uja113xPowerSupplyHostTest.cpp jp1OpenAndClosed).
 */
void testJp1OpenAndClosed()
{
    namespace handover = ::ecustats::handover;
    Eeprom memory;
    // power-on: MCU and SBC (POSI)
    {
        Ecu ecu(&memory, 0U, wakeup::POWER);
        handover::publishSupplyStartReasons(wakeup::POWER, true);
        ecu.run(1000U, handover::supplyStartReasonsOrZero(), 300U);
        ecu.rundown();
        Record const& r = ecu.stats.statistics().record();
        CHECK((r.starts == 1U) && (r.mcu[idx(Cause::POWER)] == 1U) && (r.supply[idx(Cause::POWER)] == 1U));
    }
    // "lc reboot" with JP1 closed: SBC reset source 17 (RSTN pulled down), RCC 1 -> supply "reset" + 1
    {
        Ecu ecu(&memory, 0U, wakeup::RESET);
        handover::publishSupplyStartReasons(wakeup::RESET, true);
        ecu.run(1000U, handover::supplyStartReasonsOrZero(), 300U);
        ecu.rundown();
        Record const& r = ecu.stats.statistics().record();
        CHECK(r.starts == 2U);
        CHECK(r.mcu[idx(Cause::RESET)] == 1U);
        CHECK(r.supply[idx(Cause::RESET)] == 1U);
        CHECK(r.supply[idx(Cause::POWER)] == 1U);
        CHECK(r.lastSupplyReasons == wakeup::RESET);
    }
    // "lc reboot" with JP1 open: SBC reset source 0 (power-on of long ago), RCC 0 -> supply row unchanged
    {
        Ecu ecu(&memory, 0U, wakeup::RESET);
        handover::publishSupplyStartReasons(wakeup::RESET, false);
        ecu.run(1000U, handover::supplyStartReasonsOrZero(), 300U);
        CHECK(ecu.writes() == 1U); // still one write for the start
        ::ecustats::Snapshot s{};
        ecu.stats.snapshot(s);
        CHECK(s.supplyCounted && (!s.supplyRestarted));
        ecu.rundown();
        Record const& r = ecu.stats.statistics().record();
        CHECK(r.starts == 3U);
        CHECK(r.mcu[idx(Cause::RESET)] == 2U);  // the MCU reset is counted as such
        CHECK(r.supply[idx(Cause::RESET)] == 1U); // board 2026-10-02: was 2 (+1 although the SBC saw nothing)
        CHECK(r.supply[idx(Cause::POWER)] == 1U); // and not the old power-on again
        CHECK(r.supply[idx(Cause::UNKNOWN)] == 0U);
        CHECK(r.lastSupplyReasons == 0U);
    }
    // persistent: the next power cycle reads the same rows
    {
        Ecu ecu(&memory, 0U, wakeup::POWER);
        Record const& r = ecu.stats.statistics().record();
        CHECK((r.starts == 3U) && (r.mcu[idx(Cause::RESET)] == 2U) && (r.supply[idx(Cause::RESET)] == 1U));
    }
    handover::supplyStartReasons.store(0U);
}

void testWriteBudget()
{
    // continuous operation: 24 h with one start -> the start + one write per hour
    {
        Eeprom memory;
        Ecu ecu(&memory, 0U, wakeup::POWER);
        ecu.run(24U * HOUR_MS, wakeup::POWER, 500U);
        CHECK(ecu.writes() <= 25U);
        CHECK(ecu.writes() >= 24U);
        ecu.rundown();
        CHECK(ecu.writes() <= 26U);
    }
    // 1000 short drive cycles of 20 min with run-down: two writes per cycle (start and run-down)
    {
        Eeprom memory;
        uint32_t total = 0U;
        for (unsigned cycle = 0U; cycle < 1000U; ++cycle)
        {
            Ecu ecu(&memory, cycle * 7U, wakeup::POWER);
            ecu.run(20U * 60000U, wakeup::POWER | wakeup::canBus(0U), 400U);
            ecu.rundown();
            total += ecu.writes();
        }
        CHECK(total == 2000U);
        Ecu last(&memory, 0U, wakeup::POWER);
        CHECK(last.stats.statistics().record().starts == 1000U);
        CHECK(last.stats.statistics().record().operatingSeconds == 1000U * 1200U);
        CHECK(last.stats.statistics().record().supply[idx(Cause::CAN)] == 1000U);
    }
}

void testBinding()
{
    // no block of that name: counted in RAM only, nothing written, nothing marked
    {
        Eeprom memory;
        Profile p(::ecustats::RECORD_BYTES, "Another");
        p.manager.attach(&memory, 256U);
        p.manager.readAll();
        Stats stats;
        stats.start(p.manager, COUNT, 0U, wakeup::POWER);
        stats.cycle(100U, wakeup::POWER);
        CHECK(stats.binding() == ::ecustats::Binding::NONE);
        CHECK(stats.statistics().record().starts == 1U);
        CHECK(!p.changed[STATS]);
        CHECK(p.image.format == 0U);
        ::ecustats::Snapshot s{};
        stats.snapshot(s);
        CHECK(s.handovers == 1U);
        CHECK(std::strcmp(s.blockState, "-") == 0);
    }
    // a block of that name with another size: not used
    {
        Eeprom memory;
        Profile p(48U);
        p.manager.attach(&memory, 256U);
        p.manager.readAll();
        Stats stats;
        stats.start(p.manager, COUNT, 0U, wakeup::POWER);
        stats.cycle(100U, wakeup::POWER);
        CHECK(stats.binding() == ::ecustats::Binding::SIZE);
        CHECK(!p.changed[STATS]);
    }
    // a stored record of another format restarts at zero (and is overwritten with the new format)
    {
        Eeprom memory;
        {
            Ecu ecu(&memory, 0U, wakeup::POWER);
            ecu.run(1000U);
            ecu.rundown();
        }
        {
            Profile p;
            p.manager.attach(&memory, 256U);
            p.manager.readAll();
            {
                TestLock const lock;
                p.image.format = 0x12345678UL;
                p.changed[STATS] = true;
            }
            (void)p.manager.writeAll(0U);
        }
        Ecu ecu(&memory, 0U, wakeup::POWER);
        CHECK(ecu.stats.statistics().origin() == ::ecustats::Origin::FORMAT);
        CHECK(ecu.stats.statistics().record().starts == 0U);
        ecu.run(1000U);
        CHECK(ecu.profile.image.format == ::ecustats::FORMAT);
        CHECK(ecu.profile.image.starts == 1U);
    }
    // no memory: counted, handed over, the NvM reports it cannot write
    {
        Ecu ecu(nullptr, 0U, wakeup::POWER);
        ecu.run(1000U);
        CHECK(ecu.stats.binding() == ::ecustats::Binding::BOUND);
        CHECK(ecu.stats.statistics().record().starts == 1U);
        CHECK(ecu.writes() == 0U);
    }
}

// ---------------------------------------------------------------------------------------------- console

struct Text
{
    std::string text;

    Text& printf(char const* const format, ...)
    {
        char line[256];
        va_list args;
        va_start(args, format);
        int const n = std::vsnprintf(line, sizeof(line), format, args);
        va_end(args);
        if (n > 0)
        {
            text += line;
        }
        return *this;
    }
};

void testConsole()
{
    Eeprom memory;
    Ecu ecu(&memory, 0U, wakeup::WATCHDOG);
    ecu.run(3U * HOUR_MS + 61000U, wakeup::canBus(2U), 300U);
    ::ecustats::Snapshot s{};
    ecu.stats.snapshot(s);
    Text out;
    ::ecustats::writeStatistics(out, s);
    std::printf("%s", out.text.c_str());
    CHECK(out.text.find("operating time 3 h 01 min (10861 s), starts 1\r\n") != std::string::npos);
    CHECK(out.text.find("record: block EcuStatistics, fresh,") != std::string::npos);
    CHECK(out.text.find("block: stored, write change min interval 3600000 ms, writes 4, errors 0") != std::string::npos
          || out.text.find("block: valid, write change min interval 3600000 ms, writes 4, errors 0")
                 != std::string::npos);
    CHECK(out.text.find("this start: counted, MCU 0x00000004, supply 0x00000400\r\n") != std::string::npos);
    CHECK(out.text.find("cause       power    reset watchdog   supply    timer      can    local  unknown")
          != std::string::npos);
    CHECK(out.text.find("MCU             0        0        1        0        0        0        0        0")
          != std::string::npos);
    CHECK(out.text.find("supply          0        0        0        0        0        1        0        0")
          != std::string::npos);
    // every line below 128 bytes (SYS-16)
    size_t start = 0U;
    while (start < out.text.size())
    {
        size_t const end = out.text.find('\n', start);
        size_t const len = ((end == std::string::npos) ? out.text.size() : end) - start;
        CHECK(len < 128U);
        start = (end == std::string::npos) ? out.text.size() : (end + 1U);
    }
    // NVM-34: an MCU-only start says so instead of a bare supply 0
    {
        Eeprom memory2;
        Ecu mcuOnly(&memory2, 0U, wakeup::RESET);
        mcuOnly.run(1000U, ::ecustats::SUPPLY_NOT_RESTARTED, 300U);
        ::ecustats::Snapshot m{};
        mcuOnly.stats.snapshot(m);
        Text out3;
        ::ecustats::writeStatistics(out3, m);
        CHECK(out3.text.find("this start: counted, MCU 0x00000002, supply 0x00000000 (supply did not restart: "
                             "MCU-only start)\r\n")
              != std::string::npos);
        CHECK(out3.text.find("supply          0        0        0        0        0        0        0        0")
              != std::string::npos);
    }
    // without a block
    ::ecustats::Snapshot none{};
    none.binding    = ::ecustats::Binding::NONE;
    none.blockState = "-";
    none.policy     = "-";
    Text out2;
    ::ecustats::writeStatistics(out2, none);
    CHECK(out2.text.find("no block EcuStatistics in this project, not persistent") != std::string::npos);
    CHECK(out2.text.find("block: ") == std::string::npos);
    CHECK(out2.text.find("not counted yet") != std::string::npos);
}

} // namespace

int main()
{
    testCountReasons();
    testLoad();
    testStartWithSupply();
    testStartWithoutSupply();
    testMcuOnlyStart();
    testOperatingTime();
    testPowerCycles();
    testJp1OpenAndClosed();
    testWriteBudget();
    testBinding();
    testConsole();
    std::printf("EcuStatisticsHostTest: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
