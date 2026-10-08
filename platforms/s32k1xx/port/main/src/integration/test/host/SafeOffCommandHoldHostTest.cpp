/* SYS-151 (safety review night 2026-10-01, SR-147-3 b): host test of the hold of the commanded level of the safe_off
 * outputs - a foreign active level lasts at most one poll (1 ms), outside the safe state S1 as well.
 *
 * Real code, own test profile (safeOffStubs: invented project "irrigation_ctrl" - PWM group FAN on FTM2, safe_off
 * outputs PUMP_EN on PTB4 active high and HEATER_N on PTE9 active low, an ordinary output FAN that shares its name with
 * the PWM group, the outputs in another order than the table):
 *   - integration/SafeOffOutputs.h (the safe_off list from the generated table, the commanded level,
 *     setCommandedOutput()),
 *   - SafeOffPins.h (pin level from the read-back of the table, the store of the table walk, the clock gate),
 *   - SafeOffCommandHold.h (the hold, with the interrupt lock of the stub that counts its depth),
 *   - ModuleBinders.h (FamilyBinders::set, the write path of every module of the generated binding),
 * on a GPIO register model in which PSOR/PCOR set and clear PDOR and PDIR follows the pin (settle()).
 *
 * What it shows:
 *   - the list: exactly PUMP_EN and HEATER_N are safe_off, the output FAN (name of the PWM group) is not;
 *   - the drivers' write path records the commanded level before it writes the pin, only for safe_off outputs;
 *   - a foreign active level (active high and active low) is taken back in the next poll with the table's store;
 *     a commanded one is never touched, also not over 10 s of polls with the driver switching on and off;
 *   - pin read, commanded level and store happen inside the interrupt lock;
 *   - a port without clock is neither read nor written;
 *   - random foreign writes at random phases of 1 ms polls: every foreign level lasts at most one poll period;
 *   - the log of take-backs: the first at once, then at most one line per LOG_INTERVAL_POLLS;
 *   - SYS-161 (SR-151-2 a): the take-backs counted per safe_off output (the numbers "safeoff show" prints), a
 *     commanded level never counted, without counters only the total;
 *   - SYS-194 (safety review BL38-P1, SYS147-B1 b): every take-back reported - the first in the poll that found it,
 *     further ones at most once per REPORT_INTERVAL_POLLS and output, folded but complete; a commanded level never
 *     (counter-check); a refused report stays due; the report outside the interrupt lock; without the per-output
 *     arrays the output taken back last; without a report function nothing.
 *   - SYS-196 (decision SYS194-4 b): the record for the DTC of the fault memory (SafeOffTakeBackRecord.h) on the
 *     report of the hold - commanded levels over 3 s no occurrence (counter-check), a foreign level one occurrence in
 *     the poll that took it back with its output and count (the snapshot), at most one per second and output, a
 *     refused report counted once.
 * Meaningfulness: test_safe_off_hold_mutations.py.
 *
 * Build and run (zig c++, no hardware): tools/test -k safe-off-command-hold
 */

#include <cstdint>
#include <cstdio>
#include <map>

/* ------------------------------------------------------------------ register model */

namespace test_registers
{
volatile uint32_t* reg(uint32_t address);
} // namespace test_registers

#define NX_SAFE_OUTPUT_REG(address) (::test_registers::reg(address))

#include "integration/SafeOffOutputs.h"
#include "ModuleBinders.h"
#include "SafeOffCommandHold.h"
#include "SafeOffPins.h"
#include "SafeOffTakeBackRecord.h"

namespace test_trace
{
int lockDepth = 0;
}

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

constexpr uint32_t PTB      = 0x400FF040U;
constexpr uint32_t PTE      = 0x400FF100U;
constexpr uint32_t PDOR     = 0x00U;
constexpr uint32_t PSOR     = 0x04U;
constexpr uint32_t PCOR     = 0x08U;
constexpr uint32_t PDIR     = 0x10U;
constexpr uint32_t PCC_PTB  = 0x40065000U + 4U * 74U;
constexpr uint32_t PCC_PTE  = 0x40065000U + 4U * 77U;
constexpr uint32_t PCC_FTM2 = 0x40065000U + 4U * 58U;
constexpr uint32_t CGC      = 0x40000000U;
constexpr uint32_t PUMP_BIT   = 1U << 4U;
constexpr uint32_t HEATER_BIT = 1U << 9U;

// index of the safe_off outputs in the table order of the profile
constexpr uint8_t PUMP   = 0U;
constexpr uint8_t HEATER = 1U;

struct Model
{
    std::map<uint32_t, uint32_t> regs;
    int gatedAccesses = 0; // reads or writes of a GPIO register while its port has no clock
    int unknown       = 0;

    void reset()
    {
        regs.clear();
        for (uint32_t const port : {PTB, PTE})
        {
            for (uint32_t const off : {PDOR, PSOR, PCOR, PDIR})
            {
                regs[port + off] = 0U;
            }
        }
        regs[PCC_PTB]  = CGC;
        regs[PCC_PTE]  = CGC;
        regs[PCC_FTM2] = CGC;
        regs[PTE + PDOR] = HEATER_BIT; // HEATER_N inactive (high), as Output::init leaves it
        regs[PTE + PDIR] = HEATER_BIT;
        gatedAccesses    = 0;
        unknown          = 0;
    }

    /** GPIO semantics: PSOR/PCOR into PDOR, PDIR follows PDOR. */
    void settle()
    {
        for (uint32_t const port : {PTB, PTE})
        {
            uint32_t& pdor = regs[port + PDOR];
            pdor |= regs[port + PSOR];
            pdor &= ~regs[port + PCOR];
            regs[port + PSOR] = 0U;
            regs[port + PCOR] = 0U;
            regs[port + PDIR] = pdor;
        }
    }

    bool pumpPin() const { return (regs.at(PTB + PDIR) & PUMP_BIT) != 0U; }

    bool heaterActive() const { return (regs.at(PTE + PDIR) & HEATER_BIT) == 0U; } // active low

    void foreignPump(bool const active) // a stray store / debugger / console to the port
    {
        regs[PTB + (active ? PSOR : PCOR)] = PUMP_BIT;
        settle();
    }

    void foreignHeater(bool const active)
    {
        regs[PTE + (active ? PCOR : PSOR)] = HEATER_BIT;
        settle();
    }
} model;

int setCalls          = 0;
int notedBeforeWrite  = 0; // switch-ons of a safe_off output whose commanded level was recorded before the pin
int notedAfterWrite   = 0; // switch-ons whose commanded level was not yet recorded when the pin was written
int holdSwitchOffs    = 0;
int unlockedAccesses  = 0; // pin read, commanded level or store of the hold outside the interrupt lock
} // namespace

namespace test_registers
{
volatile uint32_t* reg(uint32_t const address)
{
    auto const it = model.regs.find(address);
    if (it == model.regs.end())
    {
        ++model.unknown;
        static uint32_t dummy = 0U;
        return &dummy;
    }
    uint32_t const port = address & ~0x3FU;
    if (((port == PTB) && ((model.regs[PCC_PTB] & CGC) == 0U))
        || ((port == PTE) && ((model.regs[PCC_PTE] & CGC) == 0U)))
    {
        ++model.gatedAccesses;
    }
    return &it->second;
}
} // namespace test_registers

/* The output manager of the stub on the model: what Output::set does for the two safe_off outputs. */
namespace bios
{
bsp::BspReturnCode Output::set(OutputId const chan, uint8_t const vol, bool)
{
    ++setCalls;
    int32_t const index = ::integration::safeoff::indexOf(static_cast<uint32_t>(chan));
    if ((index >= 0) && (vol != 0U))
    {
        if (::integration::safeoff::Commanded::isActive(static_cast<uint8_t>(index)))
        {
            ++notedBeforeWrite;
        }
        else
        {
            ++notedAfterWrite;
        }
    }
    if (chan == PUMP_EN)
    {
        model.regs[PTB + ((vol != 0U) ? PSOR : PCOR)] = PUMP_BIT;
        model.settle();
    }
    else if (chan == HEATER_N)
    {
        model.regs[PTE + ((vol != 0U) ? PCOR : PSOR)] = HEATER_BIT; // active low
        model.settle();
    }
    return bsp::BSP_OK;
}

bsp::BspReturnCode Output::get(OutputId, bool& result)
{
    result = false;
    return bsp::BSP_OK;
}

char const* Output::getName(uint16_t const channel)
{
    static char const* const names[] = {"LED_GREEN", "HEATER_N", "FAN", "PUMP_EN", "BRAKE_LIGHT"};
    return (channel < 5U) ? names[channel] : "?";
}
} // namespace bios

namespace
{
using ::integration::safeoff::Commanded;
using B = ::integration::FamilyBinders;

/* The hold as Integration.cpp builds it, with a check of the interrupt lock around each access. */
bool lockedCommanded(uint8_t const i)
{
    if (::test_trace::lockDepth != 1)
    {
        ++unlockedAccesses;
    }
    return ::integration::safeoffpins::commanded(i);
}

bool lockedActive(uint8_t const i)
{
    if (::test_trace::lockDepth != 1)
    {
        ++unlockedAccesses;
    }
    return ::integration::safeoffpins::active(i);
}

void lockedSwitchOff(uint8_t const i)
{
    if (::test_trace::lockDepth != 1)
    {
        ++unlockedAccesses;
    }
    ++holdSwitchOffs;
    ::integration::safeoffpins::switchOff(i);
}

void freshStart()
{
    model.reset();
    // every driver commands "off" after the reset (the record is zero after a reset; a test may leave it set)
    (void)B::set(::bios::Output::PUMP_EN, false);
    (void)B::set(::bios::Output::HEATER_N, false);
    holdSwitchOffs   = 0;
    unlockedAccesses = 0;
    notedBeforeWrite = 0;
    notedAfterWrite  = 0;
}

uint8_t poll(::integration::SafeOffCommandHold& hold)
{
    uint8_t const taken = hold.cyclic();
    model.settle();
    return taken;
}

void testList()
{
    using namespace ::integration::safeoff;
    CHECK(COUNT == 2U);
    CHECK(FIRST == 1);
    CHECK(outputOf(PUMP) == static_cast<int32_t>(::bios::Output::PUMP_EN));
    CHECK(outputOf(HEATER) == static_cast<int32_t>(::bios::Output::HEATER_N));
    CHECK(isSafeOff(::bios::Output::PUMP_EN));
    CHECK(isSafeOff(::bios::Output::HEATER_N));
    CHECK(!isSafeOff(::bios::Output::FAN)); // the name of the PWM group, but an ordinary output
    CHECK(!isSafeOff(::bios::Output::LED_GREEN));
    CHECK(!isSafeOff(::bios::Output::BRAKE_LIGHT));
    CHECK(!isSafeOff(::bios::Output::TOTAL_NUMBER_OF_OUTPUTS));
    CHECK(!isSafeOff(0xFFFFFFFFU));
    CHECK(indexOf(::bios::Output::HEATER_N) == 1);
    CHECK(indexOf(::bios::Output::PUMP_EN) == 0);
    static_assert(isSafeOff(::bios::Output::PUMP_EN), "usable at compile time");
}

void testCommandedRecord()
{
    freshStart();
    CHECK(!Commanded::isActive(PUMP) && !Commanded::isActive(HEATER));
    CHECK(B::set(::bios::Output::PUMP_EN, true));
    CHECK(Commanded::isActive(PUMP) && !Commanded::isActive(HEATER));
    CHECK(model.pumpPin());
    CHECK(notedBeforeWrite == 1 && notedAfterWrite == 0);
    CHECK(B::set(::bios::Output::HEATER_N, true));
    CHECK(Commanded::isActive(HEATER) && model.heaterActive());
    CHECK(notedBeforeWrite == 2 && notedAfterWrite == 0);
    // ordinary outputs are not recorded and change nothing of the record
    (void)B::set(::bios::Output::FAN, false);
    (void)B::set(::bios::Output::LED_GREEN, false);
    (void)B::set(::bios::Output::BRAKE_LIGHT, false);
    CHECK(Commanded::isActive(PUMP) && Commanded::isActive(HEATER));
    (void)B::set(::bios::Output::PUMP_EN, false);
    CHECK(!Commanded::isActive(PUMP) && Commanded::isActive(HEATER) && !model.pumpPin());
    (void)B::set(::bios::Output::HEATER_N, false);
    CHECK(!Commanded::isActive(HEATER) && !model.heaterActive());
    CHECK(!Commanded::isActive(7U)); // out of range reads "not commanded"
}

void testForeignLevelTakenBack()
{
    freshStart();
    ::integration::SafeOffCommandHold hold(
        ::integration::safeoff::COUNT, &lockedCommanded, &lockedActive, &lockedSwitchOff);
    CHECK(hold.getCount() == 2U);
    CHECK(poll(hold) == 0U);
    CHECK(holdSwitchOffs == 0);

    model.foreignPump(true);
    CHECK(model.pumpPin());
    CHECK(poll(hold) == 1U);
    CHECK(!model.pumpPin());
    CHECK(hold.getTakeBacks() == 1U && hold.getLastIndex() == PUMP);

    model.foreignHeater(true); // active low: the store is PSOR
    CHECK(model.heaterActive());
    CHECK(poll(hold) == 1U);
    CHECK(!model.heaterActive());
    CHECK(hold.getTakeBacks() == 2U && hold.getLastIndex() == HEATER);

    model.foreignPump(true);
    model.foreignHeater(true);
    CHECK(poll(hold) == 2U);
    CHECK(!model.pumpPin() && !model.heaterActive());
    CHECK(poll(hold) == 0U);
    CHECK(hold.getTakeBacks() == 4U);
    CHECK(unlockedAccesses == 0);
    CHECK(model.gatedAccesses == 0);
}

void testCommandedLevelLeftAlone()
{
    freshStart();
    ::integration::SafeOffCommandHold hold(
        ::integration::safeoff::COUNT, &lockedCommanded, &lockedActive, &lockedSwitchOff);
    (void)B::set(::bios::Output::PUMP_EN, true);
    (void)B::set(::bios::Output::HEATER_N, true);
    for (int i = 0; i < 100; ++i)
    {
        CHECK(poll(hold) == 0U);
    }
    CHECK(model.pumpPin() && model.heaterActive());
    CHECK(holdSwitchOffs == 0 && hold.getTakeBacks() == 0U);

    // the driver switches one off; a foreign write of it is taken back, the other stays on
    (void)B::set(::bios::Output::PUMP_EN, false);
    model.foreignPump(true);
    CHECK(poll(hold) == 1U);
    CHECK(!model.pumpPin() && model.heaterActive());

    // 10 s of polls with the drivers switching on and off every 10 ms: never a take-back of a commanded level
    freshStart();
    ::integration::SafeOffCommandHold hold2(
        ::integration::safeoff::COUNT, &lockedCommanded, &lockedActive, &lockedSwitchOff);
    int mismatch = 0;
    for (uint32_t ms = 0U; ms < 10000U; ++ms)
    {
        if ((ms % 10U) == 0U)
        {
            bool const pumpOn   = ((ms / 10U) % 3U) != 0U;
            bool const heaterOn = ((ms / 10U) % 5U) < 2U;
            (void)B::set(::bios::Output::PUMP_EN, pumpOn);
            (void)B::set(::bios::Output::HEATER_N, heaterOn);
        }
        (void)poll(hold2);
        if ((model.pumpPin() != Commanded::isActive(PUMP)) || (model.heaterActive() != Commanded::isActive(HEATER)))
        {
            ++mismatch;
        }
    }
    CHECK(mismatch == 0);
    CHECK(holdSwitchOffs == 0 && hold2.getTakeBacks() == 0U);
    CHECK(notedAfterWrite == 0 && notedBeforeWrite > 0);
    CHECK(unlockedAccesses == 0);
}

void testClockGated()
{
    freshStart();
    ::integration::SafeOffCommandHold hold(
        ::integration::safeoff::COUNT, &lockedCommanded, &lockedActive, &lockedSwitchOff);
    model.foreignPump(true);
    model.regs[PCC_PTB] = 0U; // port B without clock: an access would bus fault
    CHECK(!::integration::safeoffpins::active(PUMP));
    ::integration::safeoffpins::switchOff(PUMP);
    CHECK(poll(hold) == 0U);
    CHECK(model.gatedAccesses == 0);
    model.regs[PCC_PTB] = CGC;
    CHECK(::integration::safeoffpins::active(PUMP));
    CHECK(poll(hold) == 1U);
    CHECK(!model.pumpPin());
    // the PWM group's entry (FTM2 SWOCTRL) is never touched by the hold
    CHECK(model.unknown == 0);
}

/* Random foreign writes at random phases of the 1 ms poll (10 us steps): no foreign level lasts longer than one
 * poll period, the commanded ones are never taken back. */
void testForeignLevelLastsAtMostOnePoll()
{
    freshStart();
    ::integration::SafeOffCommandHold hold(
        ::integration::safeoff::COUNT, &lockedCommanded, &lockedActive, &lockedSwitchOff);
    uint32_t seed = 12345U;
    auto random   = [&seed]() {
        seed = seed * 1103515245U + 12345U;
        return (seed >> 8U) & 0xFFFFU;
    };
    uint32_t const STEP_US = 10U;
    uint32_t const POLL_US = 1000U;
    uint32_t longestUs     = 0U;
    uint32_t pumpSince     = 0U;
    uint32_t heaterSince   = 0U;
    bool pumpForeign       = false;
    bool heaterForeign     = false;
    int foreignWrites      = 0;
    int commandedLost      = 0;
    for (uint32_t t = 0U; t < 3000000U; t += STEP_US) // 3 s
    {
        uint32_t const r = random();
        if ((r % 97U) == 0U)
        {
            // the driver changes its command now and then
            bool const on = (r & 0x100U) != 0U;
            (void)B::set(((r & 0x200U) != 0U) ? ::bios::Output::PUMP_EN : ::bios::Output::HEATER_N, on);
        }
        else if ((r % 89U) == 0U)
        {
            ++foreignWrites;
            if ((r & 0x400U) != 0U)
            {
                model.foreignPump(true);
            }
            else
            {
                model.foreignHeater(true);
            }
        }
        else
        {
            // nothing this step
        }
        bool const pumpNowForeign   = model.pumpPin() && !Commanded::isActive(PUMP);
        bool const heaterNowForeign = model.heaterActive() && !Commanded::isActive(HEATER);
        if (pumpNowForeign && !pumpForeign)
        {
            pumpSince = t;
        }
        if (heaterNowForeign && !heaterForeign)
        {
            heaterSince = t;
        }
        pumpForeign   = pumpNowForeign;
        heaterForeign = heaterNowForeign;
        if ((t % POLL_US) == (POLL_US - STEP_US))
        {
            (void)poll(hold);
            if ((Commanded::isActive(PUMP) && !model.pumpPin())
                || (Commanded::isActive(HEATER) && !model.heaterActive()))
            {
                ++commandedLost;
            }
            uint32_t const end = t + STEP_US;
            if (pumpForeign && !model.pumpPin())
            {
                longestUs   = (end - pumpSince > longestUs) ? (end - pumpSince) : longestUs;
                pumpForeign = false;
            }
            if (heaterForeign && !model.heaterActive())
            {
                longestUs     = (end - heaterSince > longestUs) ? (end - heaterSince) : longestUs;
                heaterForeign = false;
            }
            CHECK(!pumpForeign && !heaterForeign); // after every poll: no foreign active level left
        }
    }
    std::printf(
        "  random: %d foreign writes, %u take-backs, longest foreign level %u us (poll %u us)\n",
        foreignWrites,
        static_cast<unsigned>(hold.getTakeBacks()),
        static_cast<unsigned>(longestUs),
        static_cast<unsigned>(POLL_US));
    CHECK(foreignWrites > 100);
    CHECK(hold.getTakeBacks() > 50U);
    CHECK(longestUs <= POLL_US);
    CHECK(longestUs > POLL_US / 2U); // the phases were spread over the poll period
    CHECK(commandedLost == 0);
    CHECK(unlockedAccesses == 0);
}

/* SYS-161 (safety review 2026-10-01, SR-151-2 a): the count per safe_off output, read by the console command. */
void testCountPerOutput()
{
    freshStart();
    uint32_t perOutput[::integration::safeoff::COUNT + 1U] = {0U, 0U, 0xA5A5A5A5U}; // last: guard word
    ::integration::SafeOffCommandHold hold(
        ::integration::safeoff::COUNT, &lockedCommanded, &lockedActive, &lockedSwitchOff, perOutput);
    CHECK(hold.getTakeBacksOf(PUMP) == 0U && hold.getTakeBacksOf(HEATER) == 0U);
    for (int i = 0; i < 3; ++i)
    {
        model.foreignPump(true);
        (void)poll(hold);
    }
    model.foreignHeater(true);
    (void)poll(hold);
    CHECK(hold.getTakeBacksOf(PUMP) == 3U);
    CHECK(hold.getTakeBacksOf(HEATER) == 1U);
    CHECK(hold.getTakeBacks() == 4U);
    // both in one poll: one each
    model.foreignPump(true);
    model.foreignHeater(true);
    CHECK(poll(hold) == 2U);
    CHECK(hold.getTakeBacksOf(PUMP) == 4U && hold.getTakeBacksOf(HEATER) == 2U && hold.getTakeBacks() == 6U);
    // a commanded level is never counted
    (void)B::set(::bios::Output::HEATER_N, true);
    for (int i = 0; i < 50; ++i)
    {
        (void)poll(hold);
    }
    CHECK(hold.getTakeBacksOf(HEATER) == 2U);
    // the counters are the caller's array, nothing written past it; an index out of range reads 0
    CHECK(perOutput[PUMP] == 4U && perOutput[HEATER] == 2U);
    CHECK(perOutput[::integration::safeoff::COUNT] == 0xA5A5A5A5U);
    CHECK(hold.getTakeBacksOf(::integration::safeoff::COUNT) == 0U);
    CHECK(hold.getTakeBacksOf(0xFFU) == 0U);
    // without counters: only the total
    freshStart();
    ::integration::SafeOffCommandHold plain(
        ::integration::safeoff::COUNT, &lockedCommanded, &lockedActive, &lockedSwitchOff);
    model.foreignPump(true);
    CHECK(poll(plain) == 1U);
    CHECK(plain.getTakeBacks() == 1U && plain.getTakeBacksOf(PUMP) == 0U);
}

void testLogRate()
{
    freshStart();
    ::integration::SafeOffCommandHold hold(
        ::integration::safeoff::COUNT, &lockedCommanded, &lockedActive, &lockedSwitchOff);
    CHECK(!hold.takeLogDue()); // nothing taken back yet
    model.foreignPump(true);
    (void)poll(hold);
    CHECK(hold.takeLogDue()); // the first take-back at once
    CHECK(!hold.takeLogDue());
    int lines = 0;
    for (uint32_t ms = 0U; ms < 3000U; ++ms) // a foreign level in every poll for 3 s
    {
        model.foreignPump(true);
        (void)poll(hold);
        if (hold.takeLogDue())
        {
            ++lines;
        }
    }
    CHECK(lines == 3); // one line per LOG_INTERVAL_POLLS
    CHECK(hold.getTakeBacks() == 3001U);
    for (uint32_t ms = 0U; ms < 5000U; ++ms) // quiet: no line without a new take-back
    {
        (void)poll(hold);
        CHECK(!hold.takeLogDue());
    }
}
/* SYS-194 (safety review BL38-P1 2026-10-03, SYS147-B1 b): every take-back reaches the safe supervisor as a report
 * (on the target a DEVICE_REPORT word into the hold's box, Integration.cpp). The report function of the test records
 * the index and the interrupt lock depth, and refuses while `refuse` is set (a full box). */
int reportCalls             = 0;
int reportsInLock           = 0;
bool refuse                 = false;
uint8_t reportedIndex[4096] = {};

bool recordReport(uint8_t const index)
{
    if (::test_trace::lockDepth != 0)
    {
        ++reportsInLock;
    }
    if (refuse)
    {
        return false;
    }
    if (reportCalls < static_cast<int>(sizeof(reportedIndex)))
    {
        reportedIndex[reportCalls] = index;
    }
    ++reportCalls;
    return true;
}

void freshReports()
{
    reportCalls   = 0;
    reportsInLock = 0;
    refuse        = false;
}

void testReports()
{
    using Hold         = ::integration::SafeOffCommandHold;
    uint32_t const gap = Hold::REPORT_INTERVAL_POLLS;
    freshStart();
    freshReports();
    uint32_t perOutput[::integration::safeoff::COUNT]           = {};
    uint32_t reported[::integration::safeoff::COUNT + 1U]       = {0U, 0U, 0x5A5A5A5AU}; // last: guard word
    Hold hold(::integration::safeoff::COUNT, &lockedCommanded, &lockedActive, &lockedSwitchOff, perOutput,
              &recordReport, reported);

    // nothing taken back, nothing reported - also over a long quiet time
    for (uint32_t ms = 0U; ms < 3U * gap; ++ms)
    {
        (void)poll(hold);
    }
    CHECK(reportCalls == 0 && hold.getReports() == 0U);

    // the board case of 2026-10-03: one foreign DRV_EN pulse - taken back and reported in the same poll
    model.foreignPump(true);
    CHECK(poll(hold) == 1U);
    CHECK(reportCalls == 1 && reportedIndex[0] == PUMP);
    CHECK(hold.getReports() == 1U && hold.getReportedTakeBacks() == 1U);
    CHECK(reported[PUMP] == 1U && reported[HEATER] == 0U);

    // a second one within the interval: counted at once, reported when the interval is over, as one report
    model.foreignPump(true);
    (void)poll(hold);
    model.foreignPump(true);
    (void)poll(hold);
    CHECK(hold.getTakeBacksOf(PUMP) == 3U && reportCalls == 1);
    uint32_t polls = 2U;
    while ((reportCalls == 1) && (polls < 2U * gap))
    {
        (void)poll(hold);
        ++polls;
    }
    CHECK(reportCalls == 2 && reportedIndex[1] == PUMP);
    CHECK(polls == gap); // the report comes exactly REPORT_INTERVAL_POLLS polls after the first
    CHECK(hold.getReports() == 2U && hold.getReportedTakeBacks() == 3U && reported[PUMP] == 3U);

    // both outputs at once after the interval: one report each, the other output's index right
    for (uint32_t ms = 0U; ms < gap; ++ms)
    {
        (void)poll(hold);
    }
    model.foreignPump(true);
    model.foreignHeater(true);
    CHECK(poll(hold) == 2U);
    CHECK(reportCalls == 4 && reportedIndex[2] == PUMP && reportedIndex[3] == HEATER);
    CHECK(hold.getReportedTakeBacks() == hold.getTakeBacks() && hold.getTakeBacks() == 5U);

    // counter-check: a commanded level is neither taken back nor reported, over several intervals
    (void)B::set(::bios::Output::HEATER_N, true);
    for (uint32_t ms = 0U; ms < 3U * gap; ++ms)
    {
        (void)poll(hold);
    }
    CHECK(reportCalls == 4 && hold.getTakeBacks() == 5U && model.heaterActive());
    (void)B::set(::bios::Output::HEATER_N, false);

    // a refused report (box full) stays due and goes out in the next poll that is accepted
    model.foreignPump(true);
    refuse = true;
    (void)poll(hold);
    (void)poll(hold);
    CHECK(reportCalls == 4 && hold.getReportedTakeBacks() == 5U && hold.getTakeBacks() == 6U);
    refuse = false;
    (void)poll(hold);
    CHECK(reportCalls == 5 && reportedIndex[4] == PUMP && hold.getReportedTakeBacks() == 6U);

    // a foreign level in every poll for 3 s: at most one report per output and interval, every take-back covered
    int const before = reportCalls;
    for (uint32_t ms = 0U; ms < 3U * gap; ++ms)
    {
        model.foreignPump(true);
        (void)poll(hold);
    }
    CHECK((reportCalls - before) >= 3 && (reportCalls - before) <= 4);
    for (uint32_t ms = 0U; ms < gap; ++ms) // quiet: the rest goes out once, then nothing more
    {
        (void)poll(hold);
    }
    CHECK(hold.getReportedTakeBacks() == hold.getTakeBacks());
    CHECK(reported[PUMP] == perOutput[PUMP] && reported[HEATER] == perOutput[HEATER]);
    CHECK(reported[::integration::safeoff::COUNT] == 0x5A5A5A5AU); // nothing written past the caller's array
    int const settled = reportCalls;
    for (uint32_t ms = 0U; ms < 3U * gap; ++ms)
    {
        (void)poll(hold);
    }
    CHECK(reportCalls == settled);
    CHECK(reportsInLock == 0); // the report runs outside the interrupt lock of the take-back
    CHECK(unlockedAccesses == 0);

    // without the per-output arrays: a report names the output taken back last and covers every take-back
    freshStart();
    freshReports();
    Hold total(::integration::safeoff::COUNT, &lockedCommanded, &lockedActive, &lockedSwitchOff, nullptr,
               &recordReport, nullptr);
    model.foreignHeater(true);
    (void)poll(total);
    CHECK(reportCalls == 1 && reportedIndex[0] == HEATER && total.getReportedTakeBacks() == 1U);
    model.foreignPump(true);
    (void)poll(total);
    CHECK(reportCalls == 1 && total.getReportedTakeBacks() == 1U);
    for (uint32_t ms = 0U; ms < gap; ++ms)
    {
        (void)poll(total);
    }
    CHECK(reportCalls == 2 && reportedIndex[1] == PUMP && total.getReportedTakeBacks() == 2U);

    // without a report function: the take-back works as before, nothing is reported
    freshStart();
    freshReports();
    Hold silent(::integration::safeoff::COUNT, &lockedCommanded, &lockedActive, &lockedSwitchOff, perOutput);
    model.foreignPump(true);
    CHECK(poll(silent) == 1U && !model.pumpPin());
    CHECK(reportCalls == 0 && silent.getReports() == 0U && silent.getReportedTakeBacks() == 0U);
}

/* SYS-196: the record for the fault memory on the report of the hold, wired as Integration.cpp wires it (the record
 * first, then the box). */
uint32_t dtcTakeBacks[::integration::safeoff::COUNT]     = {};
uint32_t dtcRecorded[::integration::safeoff::COUNT + 1U] = {};
uint32_t dtcReported[::integration::safeoff::COUNT]      = {};
int dtcOccurrences                                       = 0;
::integration::SafeOffTakeBackRecord* dtcRecord          = nullptr;

void countOccurrence() { ++dtcOccurrences; }

bool reportWithRecord(uint8_t const index)
{
    int32_t const output = ::integration::safeoff::outputOf(index);
    uint8_t const detail = ((output >= 0) && (output < 0xFF)) ? static_cast<uint8_t>(output) : uint8_t{0xFFU};
    dtcRecord->onReport(index, detail);
    return recordReport(index);
}

void testFaultMemoryRecord()
{
    using Hold         = ::integration::SafeOffCommandHold;
    using Record       = ::integration::SafeOffTakeBackRecord;
    uint32_t const gap = Hold::REPORT_INTERVAL_POLLS;
    freshStart();
    freshReports();
    for (uint32_t& w : dtcTakeBacks)
    {
        w = 0U;
    }
    for (uint32_t& w : dtcReported)
    {
        w = 0U;
    }
    dtcRecorded[0]                             = 0U;
    dtcRecorded[1]                             = 0U;
    dtcRecorded[::integration::safeoff::COUNT] = 0xA5A5A5A5U; // guard word
    dtcOccurrences                             = 0;
    Record record(::integration::safeoff::COUNT, dtcTakeBacks, dtcRecorded, &countOccurrence);
    dtcRecord = &record;
    Hold hold(::integration::safeoff::COUNT, &lockedCommanded, &lockedActive, &lockedSwitchOff, dtcTakeBacks,
              &reportWithRecord, dtcReported);
    CHECK(record.lastOutput() == Record::NO_OUTPUT && record.lastOutputTakeBacks() == 0U);

    // counter-check: the drivers command both outputs on and off for 3 s - no take-back, no occurrence, no DTC
    for (uint32_t ms = 0U; ms < 3U * gap; ++ms)
    {
        bool const on = ((ms / 100U) % 2U) == 0U;
        (void)B::set(::bios::Output::PUMP_EN, on);
        (void)B::set(::bios::Output::HEATER_N, !on);
        (void)poll(hold);
    }
    (void)B::set(::bios::Output::PUMP_EN, false);
    (void)B::set(::bios::Output::HEATER_N, false);
    (void)poll(hold);
    CHECK(hold.getTakeBacks() == 0U && dtcOccurrences == 0 && record.occurrences() == 0U);
    CHECK(record.lastOutput() == Record::NO_OUTPUT);

    // the board case: one foreign level of the pump enable - one occurrence in the poll that took it back
    model.foreignPump(true);
    (void)poll(hold);
    CHECK(dtcOccurrences == 1);
    CHECK(record.lastOutput() == static_cast<uint8_t>(::bios::Output::PUMP_EN));
    CHECK(record.lastOutputTakeBacks() == 1U);

    // a foreign level of the pump in every poll for 3 s: at most one occurrence per second (the folding of the hold)
    for (uint32_t ms = 0U; ms < 3U * gap; ++ms)
    {
        model.foreignPump(true);
        (void)poll(hold);
    }
    CHECK(dtcOccurrences >= 3 && dtcOccurrences <= 4);
    CHECK(record.lastOutputTakeBacks() == hold.getTakeBacksOf(PUMP));

    // the heater after a quiet second: its own occurrence, the snapshot names the heater
    for (uint32_t ms = 0U; ms < gap; ++ms)
    {
        (void)poll(hold);
    }
    int const beforeHeater = dtcOccurrences;
    model.foreignHeater(true);
    (void)poll(hold);
    CHECK(dtcOccurrences == beforeHeater + 1);
    CHECK(record.lastOutput() == static_cast<uint8_t>(::bios::Output::HEATER_N));
    CHECK(record.lastOutputTakeBacks() == 1U);

    // a report the box refuses: the occurrence counts once, its repetition in the next polls not again
    for (uint32_t ms = 0U; ms < gap; ++ms)
    {
        (void)poll(hold);
    }
    int const beforeRefused = dtcOccurrences;
    refuse                  = true;
    model.foreignPump(true);
    (void)poll(hold);
    (void)poll(hold);
    (void)poll(hold);
    CHECK(dtcOccurrences == beforeRefused + 1);
    refuse = false;
    (void)poll(hold);
    CHECK(dtcOccurrences == beforeRefused + 1);
    CHECK(dtcRecorded[PUMP] == dtcTakeBacks[PUMP] && dtcRecorded[HEATER] == dtcTakeBacks[HEATER]);
    CHECK(dtcRecorded[::integration::safeoff::COUNT] == 0xA5A5A5A5U); // nothing written past the caller's array

    // an index beyond the outputs records nothing
    record.onReport(::integration::safeoff::COUNT, 9U);
    CHECK(dtcOccurrences == beforeRefused + 1 && record.lastOutput() == static_cast<uint8_t>(::bios::Output::PUMP_EN));
    CHECK(unlockedAccesses == 0);
    dtcRecord = nullptr;
}
} // namespace

int main()
{
    testList();
    testCommandedRecord();
    testForeignLevelTakenBack();
    testCommandedLevelLeftAlone();
    testClockGated();
    testForeignLevelLastsAtMostOnePoll();
    testLogRate();
    testCountPerOutput();
    testReports();
    testFaultMemoryRecord(); // SYS-196
    CHECK(::test_trace::lockDepth == 0);
    std::printf("safe_off command hold (SYS-151): %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
