// SYS-141 (safety review SYS-116/SYS-30 K8, follow-up C6): the RTE side of the run-time selection of the safe state of
// a PWM group (doc/safety/partner-and-degraded.md part C, decisions C5 (a) and C6 (a)) and the start test of the
// active short at the start of the ECU.
//
// The selection service is the family port's (SYS-116; S32K1: hardFaultHandler/safeStateSelection.h, nxSafeState*):
// it is the only writer of the selection words the switch-off paths read, it accepts only a state the project allows
// and that is still available, and it removes the shorts of a group until the next power-on (bridge fault, failed
// start test). This file only brings the RTE ports to it and reports what it refuses - it decides nothing itself:
//
//   Selection::request   the provided port safety.safe_state.<GROUP> of the application, every cycle of its task:
//                        hands the requested state to the service (freewheel is always accepted); a refused request
//                        leaves the selection as it is and is reported once per change (S3, report only)
//   Selection::read      the required ports safety.safe_state_active.<GROUP> (the state the switch-off paths would
//                        apply now) and safety.qlf.safe_state.<GROUP>: OK only while the requested state is in force
//                        and none of the project's states of the group is removed
//   startTests           once per start, before the init runnables; after a power-on only (SYS-143, below) it runs
//   runStartTests        for every short of a group whose project enables
//                        the test (pwm.<group>.safe_state_test), begin - hold for safe_state_test_us - end; any
//                        result but OK removes the group's shorts for the power cycle (the service does it for a
//                        read-back failure itself, this code for every other result - a test that could not run is
//                        no passed test) and raises the event SAFE_STATE_TEST_FAILURE (report); the group is then
//                        freewheel only, the selection service refuses a short, the qualifier reads ERROR
//
// Why the start test runs here: RteSystem::init() is the one point of every start after the BSP (the PWM timer is
// clocked) and before the application's first runnable (the application has not enabled the group yet). Standstill is
// the application's statement (an AoU of 5.10: without phase voltages the test cannot see a turning machine); the
// phase voltages of IO-64 are not part of this step.
//
// SYS-143 (safety review SYS-136/SYS-141, decisions E2-E4 of 2026-09-30):
//   E4 power-on only   startTests(): the shorts are built only after a power-on. After any other start (watchdog,
//                      pin, software, debugger reset - the machine may still turn) nothing is built: a group keeps
//                      its shorts only if the record of the power-on test in no-init RAM (StartRecord) is intact and
//                      says it passed; otherwise the shorts are removed until the next power-on. A start whose RAM
//                      content is lost (low-voltage reset) or whose reason the MCU cannot tell counts as "no record".
//   E3 enable          Probe::enableOn(test): the gate-driver enable bound to the group (board pwm[].gate_driver,
//                      read through the driver) must read OFF before the short is built and after it is taken back -
//                      the test is register level (5.10 step 3 b) and never reaches the bridge. An enable that reads
//                      on is no passed test (ENABLE_ON). A group without a bound gate driver has no read-back (AoU).
//   E2 budget          the hold is a busy wait in the first application task; it ends after durationUs or when the
//                      microsecond clock stands still for STUCK_READS reads (CLOCK_STUCK, no passed test). The sum of
//                      all holds is bounded by tools/rtegen (rte::SAFE_STATE_TEST_BUDGET_US, a third of the MCU
//                      watchdog time-out) and checked again at compile time in RteSystem.cpp.
//
// Why SAFE_STATE_TEST_FAILURE is no SafeSupervisor::Event yet: the event list of the supervisor is read by
// tools/nxconfig/degraded.py and pinned by its tests (the last event is ETL_ASSERT), the parallel work on the
// supervisor (SYS-139/140) adds events there, and the reaction of a failed test is already taken by the service
// (short removed, freewheel forced). The event is reported here - log line of the SAFETY component, the qualifier of
// the RTE - and joins the supervisor's closed list of degradable events with the task that maps it (review point).
//
// Family and board neutral: Service, Clock and Log are policies - RteSystem binds the family's C service, the host
// test (tools/rtegen/test_rtegen_safe_state.py) a model with fault injection. No allocation, no lock; the state of
// the requests lies in ordinary RAM of the application (the switch-off paths never read it).

#pragma once

#include <cstddef>
#include <cstdint>

namespace safestate
{
/** States and results of the service (the numbers of hardFaultHandler/safeStateSelection.h NX_SAFE_STATE_*). */
constexpr uint32_t FREEWHEEL  = 0U;
constexpr uint32_t SHORT_LOW  = 1U;
constexpr uint32_t SHORT_HIGH = 2U;
constexpr int32_t OK          = 0;
/** SYS-143: results of the glue itself (outside the service's NX_SAFE_STATE_ERROR_* range -1..-6). */
constexpr int32_t CLOCK_STUCK = -100; // the microsecond clock stood still during the hold (E2)
constexpr int32_t ENABLE_ON   = -101; // the gate-driver enable bound to the group read on (E3)
/**
 * SYS-143 (E2): reads of an unchanged microsecond clock after which the hold ends as CLOCK_STUCK. A running clock
 * changes every microsecond; 100000 reads take far longer than that on any core this code runs on, so only a clock
 * that does not run ends here - and the short is not held for ever in the first application task.
 */
constexpr uint32_t STUCK_READS = 100000U;

constexpr uint32_t bitOf(uint32_t const state) { return 1UL << state; }

/**
 * The requests of the application per group. `Service`: static int32_t select(group, state),
 * static uint32_t selected(group), static uint32_t allowed(group). `Log`: static void refused(group, state, result).
 */
template<class Service, class Log, size_t Groups>
class Selection
{
public:
    /** Hands `state` of the provided port safety.safe_state.<GROUP> to the service; every cycle of its task. */
    void request(uint32_t const group, uint32_t const state)
    {
        if (group >= Groups)
        {
            return;
        }
        int32_t const result = Service::select(group, state);
        if ((result != OK) && ((result != _result[group]) || (state != _request[group])))
        {
            Log::refused(group, state, result); // S3: the selection stays as it is
        }
        _request[group] = state;
        _result[group]  = result;
    }

    /**
     * The state the switch-off paths would apply now (`active`) and whether the application's request is in force
     * with every state of the project still available (`inForce`); `projectAllowed`: the project's states of the
     * group as bit mask (bitOf), derived by tools/rtegen.
     */
    void read(uint32_t const group, uint32_t const projectAllowed, uint32_t& active, bool& inForce) const
    {
        active = Service::selected(group);
        if (group >= Groups)
        {
            inForce = false;
            return;
        }
        inForce = (_result[group] == OK) && (active == _request[group])
                  && ((Service::allowed(group) & projectAllowed) == projectAllowed);
    }

private:
    uint32_t _request[Groups] = {}; // FREEWHEEL: the state after every reset
    int32_t _result[Groups]   = {};
};

/**
 * Runs the start tests of the project once (from startTests() after a power-on). `Test`: a row of the generated
 * rte::SAFE_STATE_TESTS (group = selection word, state = SHORT_LOW/SHORT_HIGH, durationUs =
 * pwm.<group>.safe_state_test_us, name = the PWM group). `Service`: static int32_t testBegin(group, state),
 * static int32_t testEnd(group), static void blockShort(group). `Clock`: static uint32_t nowUs() (wraps). `Log`:
 * static void testFailed(Test const&, int32_t result, bool atEnd), static void testPassed(Test const&). `Probe`
 * (SYS-143, E3): static bool enableOn(Test const&) - the gate-driver enable bound to the group reads on (false for a
 * group without one). A failed group is not tested again (its shorts are gone).
 * \param passedGroups bit per group (group < 32) every test of which passed
 * \return number of failed groups
 */
template<class Service, class Clock, class Log, class Probe, class Test>
size_t runStartTests(Test const* const tests, size_t const count, uint32_t& passedGroups)
{
    size_t failed         = 0U;
    uint32_t failedGroups = 0U; // bit per group (at most 32 groups carry a test)
    uint32_t testedGroups = 0U;
    for (size_t i = 0U; i < count; ++i)
    {
        Test const& test = tests[i];
        uint32_t const bit    = (test.group < 32U) ? bitOf(test.group) : 0U;
        if ((failedGroups & bit) != 0U)
        {
            continue;
        }
        testedGroups |= bit;
        // E3: the enable must read off before the short is built - a register-level test never reaches the bridge
        int32_t result = Probe::enableOn(test) ? ENABLE_ON : Service::testBegin(test.group, test.state);
        bool atEnd     = false;
        if (result == OK)
        {
            // E2: hold for durationUs, but never longer than a running clock allows (the caller's standstill
            // statement, nothing to service here)
            uint32_t const start = Clock::nowUs();
            uint32_t last        = start;
            uint32_t still       = 0U;
            bool held            = true;
            for (;;)
            {
                uint32_t const now = Clock::nowUs();
                if ((now - start) >= test.durationUs)
                {
                    break;
                }
                if (now != last)
                {
                    last  = now;
                    still = 0U;
                }
                else if (++still >= STUCK_READS)
                {
                    held = false;
                    break;
                }
            }
            result = Service::testEnd(test.group);
            atEnd  = true;
            if ((result == OK) && !held)
            {
                result = CLOCK_STUCK;
            }
            if ((result == OK) && Probe::enableOn(test))
            {
                result = ENABLE_ON; // switched on while the short was built
            }
        }
        if (result != OK)
        {
            // a read-back failure blocked the shorts in the service already; every other result (clock off, not
            // allowed, blocked, sequence, clock stuck, enable on) is no passed test either: freewheel only for this
            // power cycle
            Service::blockShort(test.group);
            failedGroups |= bit;
            ++failed;
            Log::testFailed(test, result, atEnd);
        }
        else
        {
            Log::testPassed(test);
        }
    }
    passedGroups = testedGroups & ~failedGroups;
    return failed;
}

/** SYS-143 (E4): how this start came about, as far as the start test is concerned. */
enum class Start : uint8_t
{
    POWER_ON, // power-on reset: the machine is assumed at standstill (AoU), the tests run
    WARM,     // reset without power-on (watchdog, pin, software, debugger): no test, the record of power-on decides
    LOST,     // RAM content lost without power-on (low-voltage reset) or reason unknown: no test and no record
};

/**
 * SYS-143 (E4): the outcome of the power-on test, kept over resets in no-init RAM (RteSystem: .noinit.safeStateTest).
 * `passed`: bit per group whose tests all passed at the last power-on; `check`: checkOf(passed). Written before it is
 * read after every power-on (no-init RAM with ECC is undefined then); a record that does not check counts as "no
 * group passed".
 */
struct StartRecord
{
    uint32_t passed;
    uint32_t check;
};

constexpr uint32_t RECORD_KEY = 0x53533433UL; // "SS43"

constexpr uint32_t checkOf(uint32_t const passed) { return ~(RECORD_KEY ^ ((passed << 7U) | (passed >> 25U))); }

inline bool isValid(StartRecord const volatile& record) { return record.check == checkOf(record.passed); }

inline void write(StartRecord volatile& record, uint32_t const passed)
{
    record.passed = passed;
    record.check  = checkOf(passed);
}

/**
 * SYS-143 (E4): the start test of the active short once per start (RteSystem::init, before the init runnables).
 * POWER_ON: the record first says "none passed" (a reset during the tests keeps the shorts away), then the tests run
 * and the record takes the groups that passed. WARM and LOST: no short is built; every group with a test that is not
 * in an intact record (LOST: never) loses its shorts until the next power-on. `Log` additionally:
 * static void notTested(Test const&, bool recordValid), once per such group.
 * \return number of groups whose shorts were removed at this start
 */
template<class Service, class Clock, class Log, class Probe, class Test>
size_t startTests(Test const* const tests, size_t const count, Start const start, StartRecord volatile& record)
{
    if (start == Start::POWER_ON)
    {
        write(record, 0U);
        uint32_t passed      = 0U;
        size_t const removed = runStartTests<Service, Clock, Log, Probe>(tests, count, passed);
        write(record, passed);
        return removed;
    }
    bool const valid       = (start == Start::WARM) && isValid(record);
    uint32_t const passed  = valid ? record.passed : 0U;
    if (!valid)
    {
        write(record, 0U); // from now on "none passed" until the next power-on
    }
    size_t removed   = 0U;
    uint32_t handled = 0U;
    for (size_t i = 0U; i < count; ++i)
    {
        Test const& test = tests[i];
        uint32_t const bit    = (test.group < 32U) ? bitOf(test.group) : 0U;
        if ((bit != 0U) && (((passed | handled) & bit) != 0U))
        {
            continue; // passed at power-on, or removed already for its other short
        }
        handled |= bit;
        Service::blockShort(test.group);
        ++removed;
        Log::notTested(test, valid);
    }
    return removed;
}

} // namespace safestate
