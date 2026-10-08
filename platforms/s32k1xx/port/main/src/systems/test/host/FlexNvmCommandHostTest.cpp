/* NVM-22: host test of the order of "flexnvm partition" (systems/FlexNvmCommand.h, flexnvmrun::runPartition()) and
 * of its journal in no-init RAM.
 *
 * Board finding 2026-09-30 (capture nvm07_pgmpart.txt): after "flexnvm partition" the safety task's period check
 * reset the ECU (SAFETY_CYCLE_PERIOD_VIOLATION, kind 1, 31 ms) and the result line never appeared - the partition was
 * written. Cause: PGMPART cannot run while the core fetches from flash, OpenBSW's EepromDriver runs it with all
 * interrupts suspended; when its resumeAllInterrupts() lets them back in, the safety task (higher priority than the
 * console context) runs first, sees the gap and resets before the console context gets the CPU again.
 *
 * The model: PRIMASK as a flag, the driver's own suspend/resume inside partition() (saved and restored like the
 * target's getMachineStateRegisterValueAndSuspendAllInterrupts()/resumeAllInterrupts()), the PGMPART block leaves a
 * period violation pending, and the "safety task" resets the model as soon as the interrupts are enabled while it is
 * pending. What it shows:
 * - counter-example (order up to NVM-22): the reset comes inside partition(), the result line is lost - the cause;
 * - runPartition(): the watchdog check first; the journal "launching" before the announcement and before PGMPART
 *   (a reset during PGMPART leaves "launching"); the interrupts suspended from before partition() until after the
 *   result line, so the driver's resume nests and the line is written with the interrupts off; the journal "done"
 *   with the result before the line and before the resume; the reset comes only at the resume - after the line;
 * - a watchdog outside the MCU that does not allow the budget: nothing launched, nothing announced, journal kept;
 * - the journal: zero or random content is invalid, the fields round-trip, a changed word is invalid.
 *
 * Build and run (zig c++, no hardware; tools/test -k flexnvm-command):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/main/src/systems/test/host/stubs -Iplatforms/s32k1xx/port/main/include \
 *       platforms/s32k1xx/port/main/src/systems/test/host/FlexNvmCommandHostTest.cpp -o build/host/flexnvmcmd.exe
 */

#include "systems/FlexNvmCommand.h"

#include <cstdint>
#include <cstdio>
#include <string>

namespace
{
using ::systems::flexnvmrun::Journal;
using ::systems::flexnvmrun::Outcome;
using ::systems::flexnvmrun::runPartition;

int checks   = 0;
int failures = 0;

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

constexpr uint8_t RESULT_DONE_RESET_NEEDED = 1U; // FlexNvmPartition::Result::DONE_RESET_NEEDED
constexpr uint8_t DEPART_ERASED            = 0xFU;

struct Snapshot
{
    bool valid;
    uint8_t state;
    uint8_t result;
    uint8_t depart;
};

Snapshot snap(Journal const volatile& j) { return Snapshot{j.valid(), j.state(), j.result(), j.departBefore()}; }

/** The ECU around the command: PRIMASK, a pending period violation, the safety task's reset, the console wire. */
struct Ecu
{
    uint32_t primask       = 0U; // 1: all interrupts suspended
    bool violationPending  = false;
    bool reset             = false;
    bool watchdogAllows    = true;
    std::string trace;   // W watchdog, D depart, A announce, S suspend, P partition, L line, R resume, X reset
    std::string console; // what got onto the wire before the reset
    uint32_t primaskAtLine = 0xFFU;
    uint32_t resumedWith   = 0xFFU;
    Snapshot atAnnounce{};
    Snapshot atPartition{};
    Snapshot atLine{};
    Journal volatile* journal = nullptr;

    // the safety task runs whenever the interrupts are enabled; a pending period violation resets the ECU
    void safetyTaskMayRun()
    {
        if ((primask == 0U) && violationPending && !reset)
        {
            reset = true;
            trace.push_back('X');
            console += "SAFETY RESET (SAFETY_CYCLE_PERIOD_VIOLATION)\n";
        }
    }
};

/** The target glue of FlexNvmCommand.cpp, modelled. After the reset nothing runs any more. */
class ModelOps
{
public:
    explicit ModelOps(Ecu& ecu) : _ecu(ecu) {}

    bool externalWatchdogAllows()
    {
        _ecu.trace.push_back('W');
        return _ecu.watchdogAllows;
    }

    uint8_t departCode()
    {
        _ecu.trace.push_back('D');
        return DEPART_ERASED;
    }

    void announce()
    {
        if (_ecu.reset)
        {
            return;
        }
        _ecu.trace.push_back('A');
        _ecu.atAnnounce = snap(*_ecu.journal);
        _ecu.console += "flexnvm: announce\n";
        _ecu.safetyTaskMayRun();
    }

    uint32_t suspendAllInterrupts()
    {
        if (_ecu.reset)
        {
            return 0U;
        }
        _ecu.trace.push_back('S');
        uint32_t const old = _ecu.primask;
        _ecu.primask       = 1U;
        return old;
    }

    // FlexNvmPartition::partition() -> OpenBSW EepromDriver::launchCommand(): its own suspend/resume around PGMPART
    uint8_t partition()
    {
        if (_ecu.reset)
        {
            return 0xEEU;
        }
        _ecu.trace.push_back('P');
        _ecu.atPartition = snap(*_ecu.journal);
        uint32_t const old = _ecu.primask; // getMachineStateRegisterValueAndSuspendAllInterrupts()
        _ecu.primask       = 1U;
        _ecu.violationPending = true; // PGMPART: the safety task misses its period
        _ecu.primask          = old;  // resumeAllInterrupts(old)
        _ecu.safetyTaskMayRun();
        return RESULT_DONE_RESET_NEEDED;
    }

    void resultLine(uint8_t const result)
    {
        if (_ecu.reset)
        {
            return;
        }
        _ecu.trace.push_back('L');
        _ecu.primaskAtLine = _ecu.primask;
        _ecu.atLine        = snap(*_ecu.journal);
        _ecu.console += "flexnvm: result " + std::to_string(result) + "\n";
    }

    void resumeAllInterrupts(uint32_t const primask)
    {
        if (_ecu.reset)
        {
            return;
        }
        _ecu.trace.push_back('R');
        _ecu.resumedWith = primask;
        _ecu.primask     = primask;
        _ecu.safetyTaskMayRun();
    }

private:
    Ecu& _ecu;
};

bool contains(std::string const& text, std::string const& part) { return text.find(part) != std::string::npos; }

/** Up to NVM-22: plan line, partition(), result line through the console - the reset swallows the result. */
void testCounterExampleOldOrderLosesTheResult()
{
    Journal volatile journal{};
    Ecu ecu;
    ecu.journal = &journal;
    ModelOps ops(ecu);
    ops.announce();
    uint8_t const result = ops.partition();
    if (!ecu.reset)
    {
        ecu.console += "flexnvm: result " + std::to_string(result) + "\n";
    }
    CHECK(ecu.reset);
    CHECK(ecu.trace == "APX");
    CHECK(!contains(ecu.console, "flexnvm: result"));
    CHECK(!journal.valid());
}

void testLaunchWritesTheResultBeforeTheReset()
{
    Journal volatile journal{};
    Ecu ecu;
    ecu.journal = &journal;
    ModelOps ops(ecu);
    Outcome const outcome = runPartition(ops, journal);

    CHECK(outcome.launched);
    CHECK(outcome.result == RESULT_DONE_RESET_NEEDED);
    // order: watchdog, depart, announce, suspend, PGMPART, line, resume - and only then the reset
    CHECK(ecu.trace == "WDASPLRX");
    CHECK(ecu.reset);
    // the line was written with the interrupts still suspended (the driver's resume nested) ...
    CHECK(ecu.primaskAtLine == 1U);
    CHECK(ecu.resumedWith == 0U);
    // ... and it is on the wire before the reset line
    std::string::size_type const line  = ecu.console.find("flexnvm: result 1");
    std::string::size_type const reset = ecu.console.find("SAFETY RESET");
    CHECK(line != std::string::npos);
    CHECK(reset != std::string::npos);
    CHECK(line < reset);
    // journal: launching before the announcement and during PGMPART (a reset there shows "interrupted") ...
    CHECK(ecu.atAnnounce.valid && (ecu.atAnnounce.state == Journal::STATE_LAUNCHING));
    CHECK(ecu.atAnnounce.result == Journal::NO_RESULT);
    CHECK(ecu.atAnnounce.depart == DEPART_ERASED);
    CHECK(ecu.atPartition.valid && (ecu.atPartition.state == Journal::STATE_LAUNCHING));
    // ... done with the result before the line, and so before the reset: "flexnvm show" after the start has it
    CHECK(ecu.atLine.valid && (ecu.atLine.state == Journal::STATE_DONE));
    CHECK(ecu.atLine.result == RESULT_DONE_RESET_NEEDED);
    CHECK(journal.valid() && (journal.state() == Journal::STATE_DONE));
    CHECK(journal.result() == RESULT_DONE_RESET_NEEDED);
    CHECK(journal.departBefore() == DEPART_ERASED);
}

/** A caller that already holds the interrupts suspended keeps them suspended (PRIMASK restored, not cleared). */
void testNestedCallerKeepsInterruptsSuspended()
{
    Journal volatile journal{};
    Ecu ecu;
    ecu.journal = &journal;
    ecu.primask = 1U;
    ModelOps ops(ecu);
    Outcome const outcome = runPartition(ops, journal);
    CHECK(outcome.launched);
    CHECK(ecu.resumedWith == 1U);
    CHECK(ecu.primask == 1U);
    CHECK(!ecu.reset);
}

void testExternalWatchdogRefusesBeforeAnything()
{
    Journal volatile journal{};
    journal.write(Journal::STATE_DONE, 0U, 0xBU); // an older journal stays
    Ecu ecu;
    ecu.journal        = &journal;
    ecu.watchdogAllows = false;
    ModelOps ops(ecu);
    Outcome const outcome = runPartition(ops, journal);
    CHECK(!outcome.launched);
    CHECK(outcome.result == Journal::NO_RESULT);
    CHECK(ecu.trace == "W");
    CHECK(ecu.console.empty());
    CHECK(!ecu.violationPending);
    CHECK(journal.valid() && (journal.state() == Journal::STATE_DONE));
    CHECK((journal.result() == 0U) && (journal.departBefore() == 0xBU));
}

void testJournalValidity()
{
    Journal volatile journal{};
    CHECK(!journal.valid()); // zero (never written)
    journal.tag   = 0xA5A5A5A5U; // random no-init content after power-on
    journal.word  = 0x12345678U;
    journal.check = 0x9ABCDEF0U;
    CHECK(!journal.valid());
    journal.tag   = Journal::TAG; // right tag, check does not fit
    CHECK(!journal.valid());

    journal.write(Journal::STATE_LAUNCHING, Journal::NO_RESULT, 0xFU);
    CHECK(journal.valid());
    CHECK(journal.state() == Journal::STATE_LAUNCHING);
    CHECK(journal.result() == Journal::NO_RESULT);
    CHECK(journal.departBefore() == 0xFU);

    journal.write(Journal::STATE_DONE, 6U, 0xBU);
    CHECK(journal.valid());
    CHECK(journal.state() == Journal::STATE_DONE);
    CHECK(journal.result() == 6U);
    CHECK(journal.departBefore() == 0xBU);

    journal.word = journal.word ^ 0x00010000U; // one bit of the result changed
    CHECK(!journal.valid());
}

void testBudgetFitsTheMcuWatchdog()
{
    // demo: MCU watchdog 250 ms nominal, 230 ms at the fastest LPO; serviced every 70 ms by the safety task, plus one
    // 10 ms cycle of lateness - what is left must hold the budget (FlexNvmCommand.h)
    CHECK(::systems::flexnvmrun::PGMPART_BUDGET_MS + 70U + 10U <= 230U);
    CHECK(::systems::flexnvmrun::PGMPART_BUDGET_MS >= 78U); // data sheet, typical, 64 KB backup
}
} // namespace

int main()
{
    testCounterExampleOldOrderLosesTheResult();
    testLaunchWritesTheResultBeforeTheReset();
    testNestedCallerKeepsInterruptsSuspended();
    testExternalWatchdogRefusesBeforeAnything();
    testJournalValidity();
    testBudgetFitsTheMcuWatchdog();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
