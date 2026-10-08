/* SYS-141: host test of the RTE glue of the safe state selection (systems/safeState/SafeStateRte.h) and of its binding
 * to the family's service (systems/safeState/SafeStateService.h) - on the REAL selection service of the S32K1 port
 * (platforms/s32k1xx/hardFaultHandler/src/SafeOutputSelection.cpp, SYS-116) with the two-group table of its host test
 * (hardFaultHandler/test/host/selectStubs: MOT1 = group 0, freewheel + both shorts + start test 200 us; MOT2 = group 1,
 * freewheel + short low) and a small register model.
 *
 * What it shows:
 *   - start test (runStartTests over FamilyService): both shorts of MOT1 are built, held for the duration (the clock
 *     is asked until it has passed), restored to what was found; passed, nothing blocked;
 *   - a stuck forcing register (write does not take): SAFE_STATE_TEST_FAILURE, the shorts of MOT1 are removed for the
 *     power cycle, the second short of the group is not tested again, MOT2 keeps its short;
 *   - the clock of the timer is off (the service returns ERROR_CLOCK, forces nothing): still a failure and blocked -
 *     a test that could not run is no passed test;
 *   - Selection: a refused request is reported once per change and leaves the selection; after a block the
 *     qualifier (inForce) stays false even for freewheel; a short in force reads true.
 * SYS-143 (safety review SYS-136/SYS-141, E2-E4):
 *   - E3: an enable that reads on before the test: no short is built (register untouched), ENABLE_ON, blocked; an
 *     enable that comes on during the hold: the short is taken back, ENABLE_ON, blocked;
 *   - E2: a microsecond clock that stands still ends the hold after STUCK_READS reads, the short is taken back,
 *     CLOCK_STUCK, blocked;
 *   - E4 (startTests): after a power-on the record says "none passed" while the tests run and the passed groups
 *     after them; a start without power-on builds nothing - a group keeps its shorts only with an intact record
 *     that says it passed; a failed group, a corrupted record, a low-voltage reset or an unknown reason remove the
 *     shorts of every tested group (once per group), a group without a test is never touched.
 *
 * Build and run: tools/rtegen/test_rtegen_safe_state.py (SafeStateServiceTest; zig c++, no hardware).
 */

#include <cstdint>
#include <cstdio>

namespace test_registers
{
volatile uint32_t* reg(uint32_t address);
}

#define NX_SAFE_OUTPUT_REG(address) (::test_registers::reg(address))

#include "../../../../../../platforms/s32k1xx/hardFaultHandler/src/SafeOutputSelection.cpp" // NOLINT: the service
#include "safeState/SafeStateService.h"

namespace test_registers
{
struct Register
{
    uint32_t address;
    uint32_t value;
    bool stuck;        // a write does not take: the value returns to `held` at the next access
    uint32_t held;
};

constexpr uint32_t CGC = 0x40000000U;
Register registers[] = {
    {0x40038094U, 0U, false, 0U}, // FTM0 SWOCTRL (MOT1)
    {0x40026094U, 0U, false, 0U}, // FTM3 SWOCTRL (MOT2)
    {0x400FF088U, 0U, false, 0U}, {0x400FF0C4U, 0U, false, 0U}, {0x400FF048U, 0U, false, 0U},
    {0x400FF108U, 0U, false, 0U},
    {0x40065060U, CGC, false, 0U}, // PCC FTM0
    {0x40065098U, CGC, false, 0U}, // PCC FTM3
};
uint32_t sink;

volatile uint32_t* reg(uint32_t const address)
{
    for (Register& r : registers)
    {
        if (r.address == address)
        {
            if (r.stuck)
            {
                r.value = r.held;
            }
            return &r.value;
        }
    }
    return &sink;
}

Register& at(uint32_t const address)
{
    for (Register& r : registers)
    {
        if (r.address == address)
        {
            return r;
        }
    }
    return registers[0];
}
} // namespace test_registers

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

constexpr uint32_t FTM0_SWOCTRL = 0x40038094U;
constexpr uint32_t PCC_FTM0     = 0x40065060U;

struct Test
{
    uint32_t group;
    uint32_t state;
    uint32_t durationUs;
    char const* name;
};

// the generated rte::SAFE_STATE_TESTS of the stub project: MOT1 both shorts, 200 us
constexpr Test TESTS[] = {{0U, 1U, 200U, "MOT1"}, {0U, 2U, 200U, "MOT1"}};

uint32_t now          = 0U;
uint32_t clockCalls   = 0U;
uint32_t heldValue    = 0U;     // SWOCTRL seen while the test holds the short
bool clockStuck       = false;  // SYS-143: the clock does not advance
bool enable           = false;  // SYS-143: what the gate driver bound to MOT1 reads
bool enableDuringHold = false;  // SYS-143: the enable comes on while the short is held
bool recordSeen       = false;  // SYS-143: the record of power-on as seen during a hold
uint32_t recordPassed = 0xFFU;

::safestate::StartRecord volatile record;

struct Clock
{
    static uint32_t nowUs()
    {
        ++clockCalls;
        heldValue = ::test_registers::at(FTM0_SWOCTRL).value;
        if (enableDuringHold)
        {
            enable = true;
        }
        recordSeen   = ::safestate::isValid(record);
        recordPassed = record.passed;
        if (!clockStuck)
        {
            now += 50U;
        }
        return now;
    }
};

struct Probe
{
    static bool enableOn(Test const&) { return enable; }
};

unsigned passed        = 0U;
unsigned failedReports = 0U;
int32_t lastResult     = 0;
bool lastAtEnd         = false;
unsigned refusals      = 0U;
unsigned notTestedCount = 0U;
bool lastRecordValid   = false;

struct Log
{
    static void testFailed(Test const&, int32_t const result, bool const atEnd)
    {
        ++failedReports;
        lastResult = result;
        lastAtEnd  = atEnd;
    }

    static void testPassed(Test const&) { ++passed; }

    static void refused(uint32_t, uint32_t, int32_t) { ++refusals; }

    static void notTested(Test const&, bool const recordValid)
    {
        ++notTestedCount;
        lastRecordValid = recordValid;
    }
};

using Family = ::safestate::FamilyService;

/** A reset: .bss of the service is cleared (the record in no-init RAM stays). */
void resetService()
{
    for (uint32_t g = 0U; g < NX_SAFE_STATE_GROUP_COUNT; ++g)
    {
        shortBlocked[g]         = 0U;
        nxSafeStateSelection[g] = 0U;
    }
    for (auto& r : ::test_registers::registers)
    {
        r.stuck = false;
    }
    ::test_registers::at(PCC_FTM0).value = ::test_registers::CGC;
    ::test_registers::at(FTM0_SWOCTRL).value = 0x0U; // PWM running, no output control
    passed = failedReports = notTestedCount = 0U;
    lastResult       = 0;
    lastAtEnd        = false;
    clockCalls       = 0U;
    heldValue        = 0U;
    clockStuck       = false;
    enable           = false;
    enableDuringHold = false;
    recordSeen       = false;
    recordPassed     = 0xFFU;
    lastRecordValid  = false;
}

size_t run(uint32_t& passedGroups)
{
    return ::safestate::runStartTests<Family, Clock, Log, Probe>(TESTS, 2U, passedGroups);
}

void testStartTestPasses()
{
    resetService();
    uint32_t passedGroups = 0U;
    size_t const failed   = run(passedGroups);
    CHECK(failed == 0U);
    CHECK(passed == 2U);
    CHECK(failedReports == 0U);
    CHECK(passedGroups == 0x1U);                                     // MOT1 passed, MOT2 has no test
    CHECK(clockCalls >= 2U * 5U);                                    // 200 us in steps of 50 us, per test
    CHECK(clockCalls <= 2U * 6U);                                    // and not longer
    CHECK(heldValue == 0x153FU);                                     // the last test held short high
    CHECK(::test_registers::at(FTM0_SWOCTRL).value == 0x0U);        // restored to what was found
    CHECK(nxSafeStateAllowed(0U) == 0x7U);                           // nothing removed
    CHECK(Family::select(0U, 1U) == 0);                              // a short is still accepted
}

void testStuckRegister()
{
    resetService();
    ::test_registers::Register& r = ::test_registers::at(FTM0_SWOCTRL);
    r.stuck                       = true;
    r.held                        = 0x0U;
    uint32_t passedGroups         = 0xFFU;
    size_t const failed           = run(passedGroups);
    CHECK(failed == 1U);                     // the group once, its second short not tested again
    CHECK(failedReports == 1U);
    CHECK(lastResult == NX_SAFE_STATE_ERROR_READBACK);
    CHECK(passed == 0U);
    CHECK(passedGroups == 0U);
    CHECK(nxSafeStateAllowed(0U) == 0x1U);   // freewheel only for the power cycle
    CHECK(nxSafeStateAllowed(1U) == 0x3U);   // MOT2 keeps its short
    CHECK(Family::select(0U, 1U) == NX_SAFE_STATE_ERROR_BLOCKED);
    CHECK(Family::selected(0U) == NX_SAFE_STATE_FREEWHEEL);
}

void testClockOff()
{
    resetService();
    ::test_registers::at(PCC_FTM0).value = 0U; // timer not clocked: the service forces nothing
    uint32_t passedGroups                = 0xFFU;
    size_t const failed                  = run(passedGroups);
    CHECK(failed == 1U);
    CHECK(lastResult == NX_SAFE_STATE_ERROR_CLOCK);
    CHECK(passedGroups == 0U);
    CHECK(::test_registers::at(FTM0_SWOCTRL).value == 0x0U); // nothing forced
    CHECK(nxSafeStateAllowed(0U) == 0x1U);                    // not run = not passed: blocked
}

void testEnableOnBefore()
{
    // SYS-143 (E3): the enable reads on - the short would reach the bridge, so it is not built at all
    resetService();
    enable                = true;
    uint32_t passedGroups = 0xFFU;
    size_t const failed   = run(passedGroups);
    CHECK(failed == 1U);
    CHECK(lastResult == ::safestate::ENABLE_ON);
    CHECK(!lastAtEnd);
    CHECK(clockCalls == 0U);                                  // no hold
    CHECK(::test_registers::at(FTM0_SWOCTRL).value == 0x0U); // nothing forced
    CHECK(passedGroups == 0U);
    CHECK(nxSafeStateAllowed(0U) == 0x1U);
    CHECK(nxSafeStateAllowed(1U) == 0x3U);
}

void testEnableOnDuringHold()
{
    // SYS-143 (E3): the enable comes on while the short is held - taken back, no passed test
    resetService();
    enableDuringHold      = true;
    uint32_t passedGroups = 0xFFU;
    size_t const failed   = run(passedGroups);
    CHECK(failed == 1U);
    CHECK(failedReports == 1U);
    CHECK(lastResult == ::safestate::ENABLE_ON);
    CHECK(lastAtEnd);
    CHECK(heldValue == 0x2A3FU); // the first test (short low) was built ...
    CHECK(::test_registers::at(FTM0_SWOCTRL).value == 0x0U); // ... and taken back
    CHECK(passedGroups == 0U);
    CHECK(nxSafeStateAllowed(0U) == 0x1U);
}

void testClockStuck()
{
    // SYS-143 (E2): the clock stands still - the hold ends after STUCK_READS reads instead of never
    resetService();
    clockStuck            = true;
    uint32_t passedGroups = 0xFFU;
    size_t const failed   = run(passedGroups);
    CHECK(failed == 1U);
    CHECK(lastResult == ::safestate::CLOCK_STUCK);
    CHECK(lastAtEnd);
    CHECK(clockCalls >= ::safestate::STUCK_READS);
    CHECK(clockCalls <= ::safestate::STUCK_READS + 2U);       // one test only, the second is skipped
    CHECK(::test_registers::at(FTM0_SWOCTRL).value == 0x0U); // taken back
    CHECK(passedGroups == 0U);
    CHECK(nxSafeStateAllowed(0U) == 0x1U);
}

size_t start(::safestate::Start const kind)
{
    return ::safestate::startTests<Family, Clock, Log, Probe>(TESTS, 2U, kind, record);
}

void testPowerOnThenWarm()
{
    // SYS-143 (E4): power-on - the tests run, the record says "none passed" during them and MOT1 after them
    resetService();
    record.passed       = 0x5AU; // random content of no-init RAM after power-on (never read)
    record.check        = 0x1234U;
    size_t const failed = start(::safestate::Start::POWER_ON);
    CHECK(failed == 0U);
    CHECK(passed == 2U);
    CHECK(recordSeen && (recordPassed == 0U)); // a reset during a hold leaves "none passed"
    CHECK(::safestate::isValid(record));
    CHECK(record.passed == 0x1U);
    CHECK(notTestedCount == 0U);

    // a watchdog reset: .bss cleared, the record kept - nothing is built, MOT1 keeps its shorts
    resetService();
    size_t const removed = start(::safestate::Start::WARM);
    CHECK(removed == 0U);
    CHECK(clockCalls == 0U);
    CHECK(passed == 0U);
    CHECK(heldValue == 0U);
    CHECK(::test_registers::at(FTM0_SWOCTRL).value == 0x0U);
    CHECK(nxSafeStateAllowed(0U) == 0x7U);
    CHECK(nxSafeStateAllowed(1U) == 0x3U);
    CHECK(::safestate::isValid(record) && (record.passed == 0x1U)); // a second warm reset reads the same
}

void testFailedAtPowerOnStaysAfterWarm()
{
    resetService();
    ::test_registers::Register& r = ::test_registers::at(FTM0_SWOCTRL);
    r.stuck                       = true;
    r.held                        = 0x0U;
    CHECK(start(::safestate::Start::POWER_ON) == 1U);
    CHECK(::safestate::isValid(record) && (record.passed == 0U));

    resetService(); // without the record the reset would give the short back
    size_t const removed = start(::safestate::Start::WARM);
    CHECK(removed == 1U);    // once for the group, not per short
    CHECK(notTestedCount == 1U);
    CHECK(lastRecordValid);
    CHECK(clockCalls == 0U); // no test after a reset
    CHECK(nxSafeStateAllowed(0U) == 0x1U);
    CHECK(nxSafeStateAllowed(1U) == 0x3U); // no test, not touched
}

void testCorruptedRecord()
{
    resetService();
    ::safestate::write(record, 0x1U);
    record.check ^= 0x100U; // one bit
    size_t const removed = start(::safestate::Start::WARM);
    CHECK(removed == 1U);
    CHECK(notTestedCount == 1U);
    CHECK(!lastRecordValid);
    CHECK(nxSafeStateAllowed(0U) == 0x1U);
    CHECK(::safestate::isValid(record) && (record.passed == 0U)); // "none passed" until power-on

    resetService();
    ::safestate::write(record, 0x1U);
    record.passed = 0x3U; // the passed word alone changed
    CHECK(start(::safestate::Start::WARM) == 1U);
    CHECK(nxSafeStateAllowed(0U) == 0x1U);
}

void testLostStart()
{
    // a low-voltage reset or an unknown reason: no test and no record, even an intact "passed" is not read
    resetService();
    ::safestate::write(record, 0x1U);
    size_t const removed = start(::safestate::Start::LOST);
    CHECK(removed == 1U);
    CHECK(clockCalls == 0U);
    CHECK(!lastRecordValid);
    CHECK(nxSafeStateAllowed(0U) == 0x1U);
    CHECK(::safestate::isValid(record) && (record.passed == 0U));
}

void testRecordCheck()
{
    // the check word depends on every bit of passed and is not the plain complement
    CHECK(::safestate::checkOf(0U) != ::safestate::checkOf(1U));
    CHECK(::safestate::checkOf(0U) != 0xFFFFFFFFU);
    CHECK(::safestate::checkOf(0x80000000U) != ::safestate::checkOf(0U));
    ::safestate::StartRecord volatile zero{0U, 0U};
    CHECK(!::safestate::isValid(zero)); // cleared RAM is no record
}

void testSelection()
{
    resetService();
    ::safestate::Selection<Family, Log, NX_SAFE_STATE_GROUP_COUNT> selection;
    uint32_t active = 99U;
    bool inForce    = false;
    selection.read(0U, 0x7U, active, inForce);
    CHECK((active == 0U) && inForce); // after reset: freewheel requested and in force

    selection.request(0U, 1U);
    selection.read(0U, 0x7U, active, inForce);
    CHECK(active == NX_SAFE_STATE_SHORT_LOW);
    CHECK(inForce);
    CHECK(nxSafeStateSelection[0] == NX_SAFE_STATE_WORD_SHORT_LOW);

    // MOT2 does not allow short high: refused once, selection unchanged
    selection.request(1U, 2U);
    selection.request(1U, 2U);
    CHECK(refusals == 1U);
    selection.read(1U, 0x3U, active, inForce);
    CHECK(active == NX_SAFE_STATE_FREEWHEEL);
    CHECK(!inForce);
    selection.request(1U, 0U);
    selection.read(1U, 0x3U, active, inForce);
    CHECK(inForce);

    // a bridge fault removes the shorts of MOT1: the walks apply freewheel, the qualifier says so
    nxSafeStateBlockShort(0U);
    selection.read(0U, 0x7U, active, inForce);
    CHECK(active == NX_SAFE_STATE_FREEWHEEL);
    CHECK(!inForce);
    selection.request(0U, 1U);
    CHECK(refusals == 2U);
    selection.request(0U, 0U); // freewheel accepted, but the project's states are no longer all available
    selection.read(0U, 0x7U, active, inForce);
    CHECK(active == NX_SAFE_STATE_FREEWHEEL);
    CHECK(!inForce);

    // an unknown group: never in force, no store
    selection.request(5U, 1U);
    selection.read(5U, 0x7U, active, inForce);
    CHECK(!inForce);
    CHECK(active == NX_SAFE_STATE_FREEWHEEL);
}
} // namespace

int main()
{
    testStartTestPasses();
    testStuckRegister();
    testClockOff();
    testEnableOnBefore();
    testEnableOnDuringHold();
    testClockStuck();
    testPowerOnThenWarm();
    testFailedAtPowerOnStaysAfterWarm();
    testCorruptedRecord();
    testLostStart();
    testRecordCheck();
    testSelection();
    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
