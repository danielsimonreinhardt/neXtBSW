/* SYS-06: host test of the safe state (safeIo/SafeState.cpp) - the real code of the target on a register model.
 *
 * What it shows: entering the safe state walks the **safe outputs table of the project** (the same table and the
 * same walk the fault handlers and the halt deadman of the debug halt guard use, IO-32
 * hardFaultHandler/safeOutputs.h), and only then touches the indicator output; a peripheral whose clock is gated
 * is skipped; the walk stops at the terminator, so a board without power outputs (empty table) writes no
 * register at all; and leaving the safe state releases the indicator without switching a power output back on.
 *
 * The register access of the walk is redirected to the register file of this test, so loop, gate check and order
 * are the ones of the target. SafeState.cpp is compiled into this translation unit (as the OpenBSW tests include
 * the unit under test) because the macro has to be in place before the header of the walk is read.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeIo/include \
 *       -Iplatforms/s32k1xx/hardFaultHandler/include \
 *       -Iplatforms/s32k1xx/port/safety/safeIo/test/host/stubs \
 *       platforms/s32k1xx/port/safety/safeIo/test/host/SafeStateHostTest.cpp -o build/host/safestate.exe
 *   build/host/safestate.exe
 */

#include <cstdint>
#include <cstdio>
#include <string>

namespace test_registers
{
volatile uint32_t* reg(uint32_t address);
}

#define NX_SAFE_OUTPUT_REG(address) (::test_registers::reg(address))

#include "../../src/safeIo/SafeState.cpp" // NOLINT: the unit under test, with the register access above

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

// Registers of the model, addresses as the generated table of an S32K1 project names them.
constexpr uint32_t FTM0_SWOCTRL = 0x40038094U; // PWM group of the power stage
constexpr uint32_t PTC_PCOR     = 0x400FF088U; // clear register: enable of the gate driver, active high
constexpr uint32_t PTD_PSOR     = 0x400FF0C0U; // set register: an output that is active low
constexpr uint32_t PTE_PCOR     = 0x400FF108U; // output of a peripheral whose clock is off
constexpr uint32_t NEVER        = 0x400FF148U; // behind the terminator: must stay untouched
constexpr uint32_t PCC_FTM0     = 0x40065040U;
constexpr uint32_t PCC_PORTC    = 0x40065124U;
constexpr uint32_t PCC_PORTD    = 0x40065128U;
constexpr uint32_t PCC_PORTE    = 0x4006512CU;

constexpr uint32_t CGC = 0x40000000U; // PCC_PCCn_CGC_MASK: the clock of the peripheral is on

// Channels 0..5 of the PWM group forced to their inactive level (three complementary pairs)
constexpr uint32_t SWOCTRL_VALUE  = 0x3FU;
constexpr uint32_t DRV_EN_BIT     = static_cast<uint32_t>(1U) << 11U; // MOT1_GD_DRV_EN on PTC11 of the eval board
constexpr uint32_t LOW_ACTIVE_BIT = static_cast<uint32_t>(1U) << 17U;
constexpr uint32_t GATED_BIT      = static_cast<uint32_t>(1U) << 3U;
} // namespace

namespace test_registers
{
/** One register of the model. A gate register is only read by the walk, every other one is written by it. */
struct Register
{
    uint32_t address;
    uint32_t value;
    bool isGate;
    int writes;
};

Register registers[] = {
    {FTM0_SWOCTRL, 0U, false, 0},
    {PTC_PCOR, 0U, false, 0},
    {PTD_PSOR, 0U, false, 0},
    {PTE_PCOR, 0U, false, 0},
    {NEVER, 0U, false, 0},
    {PCC_FTM0, CGC, true, 0},
    {PCC_PORTC, CGC, true, 0},
    {PCC_PORTD, CGC, true, 0},
    {PCC_PORTE, 0U, true, 0}, // clock gated: the walk must not touch PTE_PCOR
};

std::string trace; // 'W' per register write of the walk, 'I' per configuration write of the indicator
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
    ++unknownAccess; // an address no entry of the project declares - on the target a wild access
    static Register sink{0U, 0U, false, 0};
    return sink;
}

/* The walk asks for the address of an entry right before it stores to it, and for a gate address to read it.
 * Counting the access to a non-gate register is therefore counting the writes of the table walk. */
volatile uint32_t* reg(uint32_t const address)
{
    Register& r = find(address);
    if (!r.isGate)
    {
        ++r.writes;
        // SYS-36: 'W' with the latch of S1 already set, 'w' without - the latch comes before the first write
        trace += (::nxSafeStateLatch != 0U) ? 'W' : 'w';
    }
    return &r.value;
}

int writesOf(uint32_t const address) { return find(address).writes; }

uint32_t valueOf(uint32_t const address) { return find(address).value; }

void setGate(uint32_t const address, uint32_t const value) { find(address).value = value; }
} // namespace test_registers

/* The safe outputs table of a project with a power stage, in the order tools/bswgen generates it: first the PWM
 * groups (FTM software output control), then every output a module connection declares safe_off. The entry
 * behind the terminator stands for the rest of a shorter table: the walk must never reach it, the same property
 * that makes an empty table (terminator at index 0) write nothing at all. */
extern "C" NxSafeOutput const nxSafeOutputs[] = {
    {FTM0_SWOCTRL, SWOCTRL_VALUE, PCC_FTM0, CGC},
    {PTC_PCOR, DRV_EN_BIT, PCC_PORTC, CGC},
    {PTD_PSOR, LOW_ACTIVE_BIT, PCC_PORTD, CGC},
    {PTE_PCOR, GATED_BIT, PCC_PORTE, CGC},
    {0U, 0U, 0U, 0U},
    {NEVER, 0xFFFFFFFFU, 0U, 0U},
};

// SYS-36: the latch of S1 (on the target defined once in the handler library, src/SafeStateLatch.cpp)
extern "C"
{
volatile uint32_t nxSafeStateLatch;
volatile uint32_t nxSafeStateEntries; // SYS-35
}

namespace bios
{
Io::PinConfiguration Io::configuration[Io::NUMBER_OF_INPUTS_AND_OUTPUTS] = {};
int Io::setCalls                                                         = 0;
std::string* Io::trace                                                   = nullptr;
} // namespace bios

namespace
{
uint32_t indicator() { return ::bios::Io::configuration[::bios::Io::SAFETY_SAFE_STATE_PIN].pinCfg; }

uint32_t indicatorOn()
{
    return static_cast<uint32_t>(::bios::Io::GPIO) | static_cast<uint32_t>(::bios::Io::STRENGTH_ON);
}

void reset()
{
    for (auto& r : test_registers::registers)
    {
        r.writes = 0;
    }
    test_registers::trace.clear();
    ::bios::Io::setCalls = 0;
    ::nxSafeStateLatch   = 0U; // as after a reset (.bss)
    ::nxSafeStateEntries = 0U;
}

/** SYS-36: entering latches S1, and the latch is set before the first register write of the walk. */
void testEnterLatchesTheSafeStateFirst()
{
    reset();
    CHECK(::nxSafeStateIsLatched() == 0);

    ::safety::SafeState::enterSafeState();

    CHECK(::nxSafeStateIsLatched() == 1);
    CHECK(::nxSafeStateLatch == NX_SAFE_STATE_LATCHED);
    CHECK(test_registers::trace == "WWWI"); // no 'w': every write of the walk saw the latch already set
}

/** SYS-35 (decision safe-state-rueckweg): leaving is the return path - it clears the latch (so the safe_off drivers
 * accept an explicit enable again) and releases the indicator, and it writes no register of the table. Before
 * SYS-35 the latch survived leaveSafeState() and only a reset cleared it. */
void testLeaveClearsTheLatch()
{
    reset();
    ::safety::SafeState::enterSafeState();
    CHECK(::nxSafeStateIsLatched() == 1);
    test_registers::trace.clear();
    ::safety::SafeState::leaveSafeState();
    CHECK(::nxSafeStateIsLatched() == 0);
    CHECK(::nxSafeStateLatch == 0U);
    CHECK(indicator() == indicatorOn());
    CHECK(test_registers::trace == "I"); // no write of the table: nothing is switched on
    // entering again latches again, leaving again clears again
    ::safety::SafeState::enterSafeState();
    CHECK(::nxSafeStateLatch == NX_SAFE_STATE_LATCHED);
    ::safety::SafeState::leaveSafeState();
    CHECK(::nxSafeStateIsLatched() == 0);
    // leaving without having entered is harmless
    ::safety::SafeState::leaveSafeState();
    CHECK(::nxSafeStateIsLatched() == 0);
}

/** SYS-35: every entry is counted (the integration hands the PWM inhibit of each entry to the debug halt guard),
 * leaving is not, and the counter only has to change - it wraps. */
void testEveryEntryIsCounted()
{
    reset();
    CHECK(::nxSafeStateEntryCount() == 0U);
    ::safety::SafeState::enterSafeState();
    CHECK(::nxSafeStateEntryCount() == 1U);
    ::safety::SafeState::leaveSafeState();
    CHECK(::nxSafeStateEntryCount() == 1U);
    ::safety::SafeState::enterSafeState();
    ::safety::SafeState::enterSafeState();
    CHECK(::nxSafeStateEntryCount() == 3U);
    ::nxSafeStateEntries = 0xFFFFFFFFU;
    ::safety::SafeState::enterSafeState();
    CHECK(::nxSafeStateEntryCount() == 0U); // wrapped - still a change
    CHECK(::nxSafeStateIsLatched() == 1);
}

/** SYS-36: any value but 0 reads as latched - a corrupted latch errs on the safe side. */
void testLatchErrsOnTheSafeSide()
{
    uint32_t const values[] = {1U, 0x80000000U, 0xFFFFFFFFU, NX_SAFE_STATE_LATCHED ^ 1U};
    for (uint32_t const v : values)
    {
        ::nxSafeStateLatch = v;
        CHECK(::nxSafeStateIsLatched() == 1);
    }
    ::nxSafeStateLatch = 0U;
    CHECK(::nxSafeStateIsLatched() == 0);
}

/** The safe state switches the power outputs of the table off, and only then shows it on the indicator. */
void testEnterSwitchesThePowerOutputsOff()
{
    reset();
    ::bios::Io::configuration[::bios::Io::SAFETY_SAFE_STATE_PIN].pinCfg = indicatorOn();

    ::safety::SafeState::enterSafeState();

    // PWM group of the power stage: all channels forced to their inactive level
    CHECK(test_registers::valueOf(FTM0_SWOCTRL) == SWOCTRL_VALUE);
    // enable of the gate driver (active high): cleared
    CHECK(test_registers::valueOf(PTC_PCOR) == DRV_EN_BIT);
    // an active low output: set
    CHECK(test_registers::valueOf(PTD_PSOR) == LOW_ACTIVE_BIT);
    // the indicator of the project follows
    CHECK(indicator() == static_cast<uint32_t>(::bios::Io::PINDISABLE));
    CHECK(::bios::Io::setCalls == 1);
    // order: the energy is off before anything else happens
    CHECK(test_registers::trace == "WWWI");
    CHECK(test_registers::unknownAccess == 0);
}

/** A peripheral whose clock is gated is skipped: the access would bus fault, in a fault handler as well. */
void testGatedPeripheralIsSkipped()
{
    reset();
    CHECK(test_registers::valueOf(PCC_PORTE) == 0U);

    ::safety::SafeState::enterSafeState();

    CHECK(test_registers::valueOf(PTE_PCOR) == 0U);
    CHECK(test_registers::writesOf(PTE_PCOR) == 0);

    // with the clock on, the same entry is written
    reset();
    test_registers::setGate(PCC_PORTE, CGC);
    ::safety::SafeState::enterSafeState();
    CHECK(test_registers::valueOf(PTE_PCOR) == GATED_BIT);
    CHECK(test_registers::writesOf(PTE_PCOR) == 1);
    test_registers::setGate(PCC_PORTE, 0U);
}

/** The walk ends at the terminator - a board without power outputs has an empty table and writes nothing. */
void testTableEndsAtTheTerminator()
{
    reset();
    ::safety::SafeState::enterSafeState();
    CHECK(test_registers::writesOf(NEVER) == 0);
    CHECK(test_registers::valueOf(NEVER) == 0U);
}

/** Leaving the safe state releases the indicator only: a power output comes back with its own driver. */
void testLeaveDoesNotSwitchThePowerOutputsOn()
{
    reset();
    ::safety::SafeState::enterSafeState();
    reset();

    ::safety::SafeState::leaveSafeState();

    CHECK(indicator() == indicatorOn());
    CHECK(::bios::Io::setCalls == 1);
    CHECK(test_registers::writesOf(FTM0_SWOCTRL) == 0);
    CHECK(test_registers::writesOf(PTC_PCOR) == 0);
    CHECK(test_registers::writesOf(PTD_PSOR) == 0);
    CHECK(test_registers::trace == "I");
}

/** Entering twice writes the same registers again: the safe state keeps no state of its own. */
void testEnterIsRepeatable()
{
    reset();
    ::safety::SafeState::enterSafeState();
    ::safety::SafeState::enterSafeState();
    CHECK(test_registers::writesOf(FTM0_SWOCTRL) == 2);
    CHECK(test_registers::writesOf(PTC_PCOR) == 2);
    CHECK(test_registers::valueOf(PTC_PCOR) == DRV_EN_BIT);
    CHECK(indicator() == static_cast<uint32_t>(::bios::Io::PINDISABLE));
}
} // namespace

int main()
{
    ::bios::Io::trace = &test_registers::trace;

    testEnterSwitchesThePowerOutputsOff();
    testGatedPeripheralIsSkipped();
    testTableEndsAtTheTerminator();
    testLeaveDoesNotSwitchThePowerOutputsOn();
    testEnterIsRepeatable();
    testEnterLatchesTheSafeStateFirst();
    testLeaveClearsTheLatch();
    testEveryEntryIsCounted();
    testLatchErrsOnTheSafeSide();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
