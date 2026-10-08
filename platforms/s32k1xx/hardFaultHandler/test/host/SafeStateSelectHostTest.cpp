/* SYS-116: host test of the safe state selection - the real code of the target on a register model.
 *
 * Units under test, compiled into this translation unit with the register access redirected to the model
 * (NX_SAFE_OUTPUT_REG) and NX_SAFE_OUTPUT_SELECT set, as a project with an active short builds them:
 *   - the C walk nxSafeOutputsSwitchOff() of hardFaultHandler/safeOutputs.h (halt deadman, S1 through SafeState),
 *   - safety::SafeState::enterSafeState() and enterDegradedState() of safeIo (S1 and SD),
 *   - the extended table, the selection words, the selection service and the start test of
 *     hardFaultHandler/src/SafeOutputSelection.cpp, with the table of selectStubs (two groups, see there).
 *
 * What it shows (doc/safety/partner-and-degraded.md part C, decisions C1, C2, C4, C5, C6):
 *   - after a reset (word 0) every walk writes the freewheel values - the table of a project without a short;
 *   - a selected short writes the short pattern of its group's timer and leaves the bound enable as it is (store 0),
 *     the enable of no group goes off, the other group stays in freewheel;
 *   - any word that is not exactly a pattern (bit flips, 0xFFFFFFFF, the other group's pattern for a state the group
 *     does not allow) writes freewheel, and so does a gated clock of the group's timer (the enable goes off too);
 *   - the service refuses a state the project does not allow, a blocked short, an unknown group - the word stays;
 *   - blocking selects freewheel at once and for the power cycle;
 *   - the degraded walk applies the selection to its entries only; S1 applies it to all;
 *   - the start test forces and reads back the pattern, restores what it found, and a register that does not take
 *     the pattern (stuck) blocks the short and leaves freewheel; sequence and clock errors force nothing;
 *   - SYS-172 (HFH-F06): a block that becomes pending between the check and the store of a select or a start test
 *     begin, and a select pending between the two stores of a block, run only after the lock is released - a removed
 *     short is never selected or forced again; a block ends a running start test of its group in freewheel.
 *
 * Build and run (zig c++, no hardware) - tools/test group safe-state-select, or
 * platforms/s32k1xx/hardFaultHandler/test/test_safe_state_select.py (also runs the mutants).
 */

#include <cstdint>
#include <cstdio>
#include <string>

namespace test_registers
{
volatile uint32_t* reg(uint32_t address);
}

#define NX_SAFE_OUTPUT_REG(address) (::test_registers::reg(address))

/* SYS-172: lock model of the service's interrupt lock (test seam of SafeOutputSelection.cpp). One interrupt can be
 * made pending; it runs where the real one would be taken - at a preemption point of the service while no lock is
 * held, or when the outermost lock is released (PRIMASK restored). Without the lock it runs at the point, i.e.
 * between the check of the block and the store. */
namespace test_lock
{
int depth = 0;
int taken = 0;
void (*pending)() = nullptr;

inline void take()
{
    void (*const isr)() = pending;
    pending             = nullptr;
    if (isr != nullptr)
    {
        isr();
    }
}

inline void preempt()
{
    if (depth == 0)
    {
        take();
    }
}

struct Lock
{
    Lock()
    {
        ++depth;
        ++taken;
    }

    ~Lock()
    {
        if (--depth == 0)
        {
            take();
        }
    }
};
} // namespace test_lock

#define NX_SAFE_STATE_LOCK                 ::test_lock::Lock
#define NX_SAFE_STATE_PREEMPTION_POINT()   ::test_lock::preempt()

#include "../../src/SafeOutputSelection.cpp"                                 // NOLINT: unit under test
#include "../../src/SafeStateLatch.cpp"                                      // NOLINT: latch of S1
#include "../../../port/safety/safeIo/src/safeIo/SafeState.cpp"             // NOLINT: S1
#include "../../../port/safety/safeIo/src/safeIo/SafeStateDegraded.cpp"     // NOLINT: SD

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

constexpr uint32_t FTM0_SWOCTRL = 0x40038094U;
constexpr uint32_t FTM3_SWOCTRL = 0x40026094U;
constexpr uint32_t PTC_PCOR     = 0x400FF088U;
constexpr uint32_t PTD_PSOR     = 0x400FF0C4U;
constexpr uint32_t PTB_PCOR     = 0x400FF048U;
constexpr uint32_t PTE_PCOR     = 0x400FF108U;
constexpr uint32_t PCC_FTM0     = 0x40065060U;
constexpr uint32_t PCC_FTM3     = 0x40065098U;
constexpr uint32_t PCC_PORTB    = 0x40065120U;
constexpr uint32_t PCC_PORTC    = 0x40065124U;
constexpr uint32_t PCC_PORTD    = 0x40065128U;
constexpr uint32_t PCC_PORTE    = 0x4006512CU;
constexpr uint32_t CGC          = 0x40000000U;
constexpr uint32_t UNWRITTEN    = 0xDEADBEEFU;

constexpr uint32_t LOW  = NX_SAFE_STATE_WORD_SHORT_LOW;
constexpr uint32_t HIGH = NX_SAFE_STATE_WORD_SHORT_HIGH;
} // namespace

namespace test_registers
{
struct Register
{
    uint32_t address;
    uint32_t value;
    bool isGate;
    bool stuck; // a write does not take (a broken register or a lost write)
    int writes;
    uint32_t last; // last value stored (also for a stuck register)
};

Register registers[] = {
    {FTM0_SWOCTRL, UNWRITTEN, false, false, 0, UNWRITTEN}, {FTM3_SWOCTRL, UNWRITTEN, false, false, 0, UNWRITTEN},
    {PTC_PCOR, UNWRITTEN, false, false, 0, UNWRITTEN},     {PTD_PSOR, UNWRITTEN, false, false, 0, UNWRITTEN},
    {PTB_PCOR, UNWRITTEN, false, false, 0, UNWRITTEN},     {PTE_PCOR, UNWRITTEN, false, false, 0, UNWRITTEN},
    {PCC_FTM0, CGC, true, false, 0, 0U},                   {PCC_FTM3, CGC, true, false, 0, 0U},
    {PCC_PORTB, CGC, true, false, 0, 0U},                  {PCC_PORTC, CGC, true, false, 0, 0U},
    {PCC_PORTD, CGC, true, false, 0, 0U},                  {PCC_PORTE, 0U, true, false, 0, 0U},
};

int unknownAccess = 0;

Register& find(uint32_t const address)
{
    for (Register& r : registers)
    {
        if (r.address == address)
        {
            return r;
        }
    }
    ++unknownAccess;
    static Register sink{0U, 0U, false, false, 0, 0U};
    return sink;
}

/* The unit reads and writes through the pointer handed out here. A stuck register (a write that does not take) is
 * modelled by restoring its previous content (`last`) at the next access - the start test reads back right after
 * its store, so it sees the old value. */
volatile uint32_t* reg(uint32_t const address)
{
    Register& r = find(address);
    if (r.isGate)
    {
        return &r.value;
    }
    if (r.stuck && (r.last != r.value))
    {
        r.value = r.last; // what was stored before a stuck register "took" nothing: previous content
    }
    return &r.value;
}

void reset()
{
    for (Register& r : registers)
    {
        if (!r.isGate)
        {
            r.value  = UNWRITTEN;
            r.writes = 0;
            r.last   = UNWRITTEN;
            r.stuck  = false;
        }
    }
}

uint32_t valueOf(uint32_t const address) { return find(address).value; }

void setGate(uint32_t const address, uint32_t const value) { find(address).value = value; }
} // namespace test_registers

using test_registers::valueOf;

namespace bios
{
Io::PinConfiguration Io::configuration[Io::NUMBER_OF_INPUTS_AND_OUTPUTS] = {};
int Io::setCalls                                                         = 0;
std::string* Io::trace                                                   = nullptr;
} // namespace bios

namespace
{
void clearSelection()
{
    for (uint32_t g = 0U; g < NX_SAFE_STATE_GROUP_COUNT; ++g)
    {
        ::nxSafeStateSelection[g] = 0U;
        shortBlocked[g]           = 0U;
    }
    testGroup = 0U;
}

void expectFreewheel()
{
    CHECK(valueOf(FTM0_SWOCTRL) == 0x3FU);
    CHECK(valueOf(FTM3_SWOCTRL) == 0x0FU);
    CHECK(valueOf(PTC_PCOR) == (1UL << 11U));
    CHECK(valueOf(PTD_PSOR) == (1UL << 17U));
    CHECK(valueOf(PTB_PCOR) == (1UL << 4U));
    CHECK(valueOf(PTE_PCOR) == UNWRITTEN);
}

void walk()
{
    test_registers::reset();
    ::nxSafeOutputsSwitchOff();
}

void testResetIsFreewheel()
{
    std::printf("reset: word 0 is freewheel for every walk\n");
    clearSelection();
    walk();
    expectFreewheel();
    test_registers::reset();
    safety::SafeState::enterSafeState();
    expectFreewheel();
    CHECK(::nxSafeStateIsLatched() == 1);
    ::nxSafeStateLatchClear();
}

void testShortLow()
{
    std::printf("short low of group 1: timer pattern, bound enable left, other group and loose enable freewheel\n");
    clearSelection();
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK);
    CHECK(::nxSafeStateSelection[0] == LOW);
    CHECK(nxSafeStateSelected(0U) == NX_SAFE_STATE_SHORT_LOW);
    walk();
    CHECK(valueOf(FTM0_SWOCTRL) == 0x2A3FU);  // CHnOC 0..5, CHnOCV of the low sides 1, 3, 5
    CHECK(valueOf(PTC_PCOR) == 0U);           // store 0: the enable stays as it is
    CHECK(valueOf(FTM3_SWOCTRL) == 0x0FU);    // group 2 not selected
    CHECK(valueOf(PTD_PSOR) == (1UL << 17U)); // its enable off
    CHECK(valueOf(PTB_PCOR) == (1UL << 4U));  // enable of no group: off
    CHECK(valueOf(PTE_PCOR) == UNWRITTEN);    // gated
    test_registers::reset();
    safety::SafeState::enterSafeState(); // S1 applies the selection too
    CHECK(valueOf(FTM0_SWOCTRL) == 0x2A3FU);
    CHECK(valueOf(PTC_PCOR) == 0U);
    ::nxSafeStateLatchClear();
}

void testShortHigh()
{
    std::printf("short high of group 1, short low of group 2\n");
    clearSelection();
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_HIGH) == NX_SAFE_STATE_OK);
    CHECK(nxSafeStateSelect(1U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK);
    walk();
    CHECK(valueOf(FTM0_SWOCTRL) == 0x153FU);
    CHECK(valueOf(PTC_PCOR) == 0U);
    CHECK(valueOf(FTM3_SWOCTRL) == 0x0A0FU);
    CHECK(valueOf(PTD_PSOR) == 0U);
    CHECK(valueOf(PTB_PCOR) == (1UL << 4U));
}

void testRefusals()
{
    std::printf("service refuses: state not allowed, unknown group and state, the word stays\n");
    clearSelection();
    CHECK(nxSafeStateSelect(1U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK);
    CHECK(nxSafeStateSelect(1U, NX_SAFE_STATE_SHORT_HIGH) == NX_SAFE_STATE_ERROR_NOT_ALLOWED);
    CHECK(::nxSafeStateSelection[1] == LOW);
    CHECK(nxSafeStateSelect(2U, NX_SAFE_STATE_FREEWHEEL) == NX_SAFE_STATE_ERROR_GROUP);
    CHECK(nxSafeStateSelect(0U, 3U) == NX_SAFE_STATE_ERROR_NOT_ALLOWED);
    CHECK(nxSafeStateSelect(0U, 0xFFFFFFFFU) == NX_SAFE_STATE_ERROR_NOT_ALLOWED);
    CHECK(::nxSafeStateSelection[0] == 0U);
    CHECK(nxSafeStateSelect(1U, NX_SAFE_STATE_FREEWHEEL) == NX_SAFE_STATE_OK);
    CHECK(::nxSafeStateSelection[1] != LOW);
    CHECK(nxSafeStateSelected(1U) == NX_SAFE_STATE_FREEWHEEL);
    CHECK(nxSafeStateSelected(7U) == NX_SAFE_STATE_FREEWHEEL);
    CHECK(nxSafeStateGroupCount() == 2U);
    CHECK(nxSafeStateAllowed(0U) == 0x7U);
    CHECK(nxSafeStateAllowed(1U) == 0x3U);
    CHECK(nxSafeStateAllowed(2U) == 0U);
}

void testCorruptedWords()
{
    std::printf("corrupted selection words end in freewheel\n");
    uint32_t const words[] = {LOW ^ 1U, LOW ^ 0x80000000U, HIGH ^ 0x00010000U, 0xFFFFFFFFU, 0x5AFE0051U, 1U, 2U};
    for (uint32_t const w : words)
    {
        clearSelection();
        ::nxSafeStateSelection[0] = w;
        ::nxSafeStateSelection[1] = w;
        walk();
        expectFreewheel();
    }
    // the pattern of a state group 2 does not allow (short high): its slot holds the freewheel values
    clearSelection();
    ::nxSafeStateSelection[1] = HIGH;
    walk();
    CHECK(valueOf(FTM3_SWOCTRL) == 0x0FU);
    CHECK(valueOf(PTD_PSOR) == (1UL << 17U));
}

void testTimerClockOff()
{
    std::printf("timer clock gated: no store to the timer, the bound enable goes off\n");
    clearSelection();
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK);
    test_registers::setGate(PCC_FTM0, 0U);
    walk();
    CHECK(valueOf(FTM0_SWOCTRL) == UNWRITTEN);
    CHECK(valueOf(PTC_PCOR) == (1UL << 11U));
    test_registers::setGate(PCC_FTM0, CGC);
    // a gate with other bits set but CGC clear reads as off as well
    test_registers::setGate(PCC_FTM0, 0x3F000000U);
    walk();
    CHECK(valueOf(PTC_PCOR) == (1UL << 11U));
    test_registers::setGate(PCC_FTM0, CGC);
}

void testBlock()
{
    std::printf("block: freewheel at once, shorts refused for the power cycle\n");
    clearSelection();
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK);
    nxSafeStateBlockShort(0U);
    CHECK(::nxSafeStateSelection[0] == 0U);
    walk();
    expectFreewheel();
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_ERROR_BLOCKED);
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_HIGH) == NX_SAFE_STATE_ERROR_BLOCKED);
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_FREEWHEEL) == NX_SAFE_STATE_OK);
    CHECK(nxSafeStateAllowed(0U) == 0x1U);
    CHECK(nxSafeStateSelect(1U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK); // the other group is untouched
    nxSafeStateBlockShort(9U); // unknown group: nothing
    CHECK(::nxSafeStateSelection[1] == LOW);
}

void testDegraded()
{
    std::printf("degraded walk: selection applied to its entries only\n");
    clearSelection();
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK);
    CHECK(nxSafeStateSelect(1U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK);
    test_registers::reset();
    safety::SafeState::enterDegradedState((1U << 0U) | (1U << 2U)); // group 1: its timer and its enable
    CHECK(valueOf(FTM0_SWOCTRL) == 0x2A3FU);
    CHECK(valueOf(PTC_PCOR) == 0U);
    CHECK(valueOf(FTM3_SWOCTRL) == UNWRITTEN);
    CHECK(valueOf(PTD_PSOR) == UNWRITTEN);
    CHECK(valueOf(PTB_PCOR) == UNWRITTEN);
    CHECK(::nxSafeStateIsLatched() == 0);
    // all bits equal the full walk, entry by entry
    test_registers::reset();
    safety::SafeState::enterDegradedState(0xFFFFFFFFU);
    uint32_t const degraded[] = {valueOf(FTM0_SWOCTRL), valueOf(FTM3_SWOCTRL), valueOf(PTC_PCOR),
                                 valueOf(PTD_PSOR),     valueOf(PTB_PCOR),     valueOf(PTE_PCOR)};
    walk();
    uint32_t const full[] = {valueOf(FTM0_SWOCTRL), valueOf(FTM3_SWOCTRL), valueOf(PTC_PCOR),
                             valueOf(PTD_PSOR),     valueOf(PTB_PCOR),     valueOf(PTE_PCOR)};
    for (int i = 0; i < 6; ++i)
    {
        CHECK(degraded[i] == full[i]);
    }
}

void testStartTest()
{
    std::printf("start test: force, read back, restore; stuck register blocks; sequence and clock\n");
    clearSelection();
    test_registers::reset();
    *test_registers::reg(FTM0_SWOCTRL) = 0U; // PWM running before the test (SWOCTRL released)
    *test_registers::reg(PTC_PCOR)     = UNWRITTEN;
    CHECK(nxSafeStateShortTestBegin(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK);
    CHECK(valueOf(FTM0_SWOCTRL) == 0x2A3FU);
    CHECK(valueOf(PTC_PCOR) == UNWRITTEN); // the enable is not the test's to touch
    CHECK(valueOf(FTM3_SWOCTRL) == UNWRITTEN);
    CHECK(nxSafeStateShortTestBegin(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_ERROR_SEQUENCE);
    CHECK(nxSafeStateShortTestEnd(1U) == NX_SAFE_STATE_ERROR_SEQUENCE);
    CHECK(nxSafeStateShortTestEnd(0U) == NX_SAFE_STATE_OK);
    CHECK(valueOf(FTM0_SWOCTRL) == 0U); // what it found
    CHECK(nxSafeStateShortTestEnd(0U) == NX_SAFE_STATE_ERROR_SEQUENCE);
    CHECK(nxSafeStateAllowed(0U) == 0x7U);
    CHECK(::nxSafeStateSelection[0] == 0U); // the test selects nothing

    CHECK(nxSafeStateShortTestBegin(0U, NX_SAFE_STATE_SHORT_HIGH) == NX_SAFE_STATE_OK);
    CHECK(valueOf(FTM0_SWOCTRL) == 0x153FU);
    CHECK(nxSafeStateShortTestEnd(0U) == NX_SAFE_STATE_OK);

    CHECK(nxSafeStateShortTestBegin(1U, NX_SAFE_STATE_SHORT_HIGH) == NX_SAFE_STATE_ERROR_NOT_ALLOWED);
    CHECK(nxSafeStateShortTestBegin(0U, NX_SAFE_STATE_FREEWHEEL) == NX_SAFE_STATE_ERROR_NOT_ALLOWED);
    CHECK(nxSafeStateShortTestBegin(5U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_ERROR_GROUP);

    // clock of the timer off: nothing forced, nothing blocked
    test_registers::reset();
    test_registers::setGate(PCC_FTM0, 0U);
    CHECK(nxSafeStateShortTestBegin(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_ERROR_CLOCK);
    CHECK(valueOf(FTM0_SWOCTRL) == UNWRITTEN);
    CHECK(nxSafeStateAllowed(0U) == 0x7U);
    test_registers::setGate(PCC_FTM0, CGC);

    // stuck register: the pattern does not take - blocked, freewheel selected and written
    test_registers::reset();
    *test_registers::reg(FTM0_SWOCTRL)        = 0U;
    test_registers::find(FTM0_SWOCTRL).last  = 0U;
    test_registers::find(FTM0_SWOCTRL).stuck = true;
    ::nxSafeStateSelection[0]                 = LOW;
    CHECK(nxSafeStateShortTestBegin(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_ERROR_READBACK);
    CHECK(nxSafeStateAllowed(0U) == 0x1U);
    CHECK(::nxSafeStateSelection[0] == 0U);
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_ERROR_BLOCKED);
    CHECK(nxSafeStateShortTestEnd(0U) == NX_SAFE_STATE_ERROR_SEQUENCE); // the failed test ended itself
    CHECK(nxSafeStateShortTestBegin(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_ERROR_BLOCKED);
    test_registers::find(FTM0_SWOCTRL).stuck = false;
}

// SYS-172 (HFH-F06): the interrupts of the race tests
uint32_t seenWord     = 0U;
int32_t isrResult     = 0;
int isrRuns           = 0;

void isrBlockGroup1()
{
    ++isrRuns;
    seenWord = ::nxSafeStateSelection[0];
    nxSafeStateBlockShort(0U);
}

void isrSelectShortGroup1()
{
    ++isrRuns;
    isrResult = nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW);
}

void testRaceSelectThenBlock()
{
    std::printf("SYS-172 race: block pending between the check and the store of a select - the short stays removed\n");
    clearSelection();
    isrRuns            = 0;
    test_lock::pending = &isrBlockGroup1;
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK); // it came first
    CHECK(isrRuns == 1);
    CHECK(seenWord == LOW);                       // the interrupt ran after the store, not between check and store
    CHECK(::nxSafeStateSelection[0] == 0U);       // the block wins
    CHECK(nxSafeStateSelected(0U) == NX_SAFE_STATE_FREEWHEEL);
    CHECK(nxSafeStateAllowed(0U) == 0x1U);
    walk();
    expectFreewheel();
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_ERROR_BLOCKED);
    CHECK(test_lock::depth == 0);
}

void testRaceBlockThenSelect()
{
    std::printf("SYS-172 race: select pending between the two stores of a block - refused\n");
    clearSelection();
    isrRuns            = 0;
    isrResult          = 0;
    test_lock::pending = &isrSelectShortGroup1;
    nxSafeStateBlockShort(0U);
    CHECK(isrRuns == 1);
    CHECK(isrResult == NX_SAFE_STATE_ERROR_BLOCKED);
    CHECK(::nxSafeStateSelection[0] == 0U);
    walk();
    expectFreewheel();
    CHECK(test_lock::depth == 0);
}

void testRaceTestBeginThenBlock()
{
    std::printf("SYS-172 race: block pending in the start test begin - the forced short ends in freewheel\n");
    clearSelection();
    test_registers::reset();
    *test_registers::reg(FTM0_SWOCTRL) = 0U;
    isrRuns            = 0;
    test_lock::pending = &isrBlockGroup1;
    CHECK(nxSafeStateShortTestBegin(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK); // it came first
    CHECK(isrRuns == 1);
    CHECK(valueOf(FTM0_SWOCTRL) == 0x3FU); // not the short pattern 0x2A3F: freewheel at once
    CHECK(nxSafeStateAllowed(0U) == 0x1U);
    CHECK(nxSafeStateShortTestEnd(0U) == NX_SAFE_STATE_ERROR_SEQUENCE); // ended by the block
    CHECK(valueOf(FTM0_SWOCTRL) == 0x3FU);                              // the end restores nothing
    CHECK(nxSafeStateShortTestBegin(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_ERROR_BLOCKED);
    CHECK(test_lock::depth == 0);
}

void testBlockEndsRunningTest()
{
    std::printf("SYS-172: a block during the start test ends it in freewheel; the other group's test runs on\n");
    clearSelection();
    test_registers::reset();
    *test_registers::reg(FTM0_SWOCTRL) = 0U;
    *test_registers::reg(FTM3_SWOCTRL) = 0U;
    CHECK(nxSafeStateShortTestBegin(0U, NX_SAFE_STATE_SHORT_HIGH) == NX_SAFE_STATE_OK);
    CHECK(valueOf(FTM0_SWOCTRL) == 0x153FU);
    nxSafeStateBlockShort(1U); // another group: the test runs on
    CHECK(valueOf(FTM0_SWOCTRL) == 0x153FU);
    CHECK(valueOf(FTM3_SWOCTRL) == 0U);
    nxSafeStateBlockShort(0U);
    CHECK(valueOf(FTM0_SWOCTRL) == 0x3FU);
    CHECK(nxSafeStateShortTestEnd(0U) == NX_SAFE_STATE_ERROR_SEQUENCE);
    CHECK(valueOf(FTM0_SWOCTRL) == 0x3FU);
    nxSafeStateBlockShort(0U); // idempotent, no test running: nothing written
    CHECK(valueOf(FTM0_SWOCTRL) == 0x3FU);
}

void testEveryWriterLocks()
{
    std::printf("SYS-172: select, block, begin and end take the lock; the lock is released on every path\n");
    clearSelection();
    test_registers::reset();
    *test_registers::reg(FTM0_SWOCTRL) = 0U;
    int before = test_lock::taken;
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK);
    CHECK(test_lock::taken == before + 1);
    before = test_lock::taken;
    CHECK(nxSafeStateShortTestBegin(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK);
    CHECK(test_lock::taken == before + 1);
    before = test_lock::taken;
    CHECK(nxSafeStateShortTestEnd(0U) == NX_SAFE_STATE_OK);
    CHECK(test_lock::taken == before + 1);
    before = test_lock::taken;
    nxSafeStateBlockShort(0U);
    CHECK(test_lock::taken == before + 1);
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_ERROR_BLOCKED); // early return
    CHECK(nxSafeStateShortTestBegin(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_ERROR_BLOCKED);
    CHECK(test_lock::depth == 0);
    CHECK(test_lock::pending == nullptr);
}
} // namespace


// SYS-30: the acceptance rule of the read-back of S1 (safeIo/SafeIoRegisters.h readbackAccepts) with a selected short
#include <safeIo/SafeIoRegisters.h>

namespace
{
void testReadbackWithSelectedShort()
{
    std::printf("SYS-30 read-back: freewheel always safe, the selected short too, a bound enable not judged\n");
    using ::safety::registers::OutputReadback;
    using ::safety::registers::readbackAccepts;
    clearSelection();
    OutputReadback const timer{FTM0_SWOCTRL, 0x3F3FU, 0x3FU, PCC_FTM0, CGC};
    OutputReadback const enable{0x400FF090U, 1UL << 11U, 0U, PCC_PORTC, CGC}; // PDIR of PTC, bit 11 low
    NxSafeOutputSelect const& t = ::nxSafeOutputsSelect[0];
    NxSafeOutputSelect const& e = ::nxSafeOutputsSelect[2];
    auto const acceptsTimer = [&](uint32_t const read) {
        return readbackAccepts(timer, read, t.address, t.value, ::nxSafeOutputSelectedValue(&t));
    };
    auto const acceptsEnable = [&](uint32_t const read) {
        return readbackAccepts(enable, read, 0x400FF088U, e.value, ::nxSafeOutputSelectedValue(&e));
    };
    // freewheel selected: only freewheel passes, the enable must be low
    CHECK(acceptsTimer(0x3FU));
    CHECK(acceptsTimer(0x3FU | 0xC0C0U)); // channels of the timer outside the group
    CHECK(!acceptsTimer(0x2A3FU));
    CHECK(!acceptsTimer(0U));
    CHECK(acceptsEnable(0U));
    CHECK(!acceptsEnable(1UL << 11U));
    // short low selected: freewheel and the short pass, the other short and a half pattern do not
    CHECK(nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_OK);
    CHECK(acceptsTimer(0x2A3FU));
    CHECK(acceptsTimer(0x3FU));
    CHECK(!acceptsTimer(0x153FU));
    CHECK(!acceptsTimer(0x0A3FU));
    CHECK(!acceptsTimer(0U));
    CHECK(acceptsEnable(1UL << 11U)); // left on in the short: not judged
    // timer clock off: the walk writes freewheel - so does the expectation
    test_registers::setGate(PCC_FTM0, 0U);
    CHECK(!acceptsTimer(0x2A3FU));
    CHECK(!acceptsEnable(1UL << 11U));
    test_registers::setGate(PCC_FTM0, CGC);
    clearSelection();
}
} // namespace

int main()
{
    testReadbackWithSelectedShort();
    testResetIsFreewheel();
    testShortLow();
    testShortHigh();
    testRefusals();
    testCorruptedWords();
    testTimerClockOff();
    testBlock();
    testDegraded();
    testStartTest();
    testRaceSelectThenBlock();
    testRaceBlockThenSelect();
    testRaceTestBeginThenBlock();
    testBlockEndsRunningTest();
    testEveryWriterLocks();
    CHECK(test_lock::depth == 0);
    CHECK(test_registers::unknownAccess == 0);
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
