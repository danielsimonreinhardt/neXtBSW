/* SYS-117: host test of the degraded walk (safeIo/SafeStateDegraded.cpp) - the real code of the target on the register
 * model of SafeStateHostTest.cpp.
 *
 * What it shows: SafeState::enterDegradedState(entries) writes exactly the entries of the safe outputs table whose bit
 * is set, with the value, the gate check and the order of the full walk (nxSafeOutputsSwitchOff of
 * hardFaultHandler/safeOutputs.h, compared entry by entry); all bits set equals the full walk; bits behind the
 * terminator and above bit 31 reach nothing; it touches neither the latch of S1, nor its entry count, nor the indicator
 * - the rest of the ECU keeps running (partner-and-degraded.md section 4.1).
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeIo/include \
 *       -Iplatforms/s32k1xx/hardFaultHandler/include \
 *       -Iplatforms/s32k1xx/port/safety/safeIo/test/host/stubs \
 *       platforms/s32k1xx/port/safety/safeIo/test/host/SafeStateDegradedHostTest.cpp -o build/host/safestate_sd.exe
 *   build/host/safestate_sd.exe
 */

#include <cstdint>
#include <cstdio>
#include <string>

namespace test_registers
{
volatile uint32_t* reg(uint32_t address);
}

#define NX_SAFE_OUTPUT_REG(address) (::test_registers::reg(address))

#include "../../src/safeIo/SafeState.cpp"         // NOLINT: the full walk and the latch, for the comparison
#include "../../src/safeIo/SafeStateDegraded.cpp" // NOLINT: the unit under test

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

// Registers of the model (addresses as an S32K1 table names them): two PWM groups, two enables, one gated output
constexpr uint32_t FTM0_SWOCTRL = 0x40038094U;
constexpr uint32_t FTM3_SWOCTRL = 0x40026094U;
constexpr uint32_t PTC_PCOR     = 0x400FF088U;
constexpr uint32_t PTD_PSOR     = 0x400FF0C0U;
constexpr uint32_t PTE_PCOR     = 0x400FF108U;
constexpr uint32_t NEVER        = 0x400FF148U;
constexpr uint32_t PCC_FTM0     = 0x40065040U;
constexpr uint32_t PCC_FTM3     = 0x40065098U;
constexpr uint32_t PCC_PORTC    = 0x40065124U;
constexpr uint32_t PCC_PORTD    = 0x40065128U;
constexpr uint32_t PCC_PORTE    = 0x4006512CU;
constexpr uint32_t CGC          = 0x40000000U;
constexpr uint32_t WRITTEN      = 0xFFFFFFFFU; // marker in `value` of a register not written yet
} // namespace

namespace test_registers
{
struct Register
{
    uint32_t address;
    uint32_t value;
    bool isGate;
};

Register registers[] = {
    {FTM0_SWOCTRL, WRITTEN, false}, {FTM3_SWOCTRL, WRITTEN, false}, {PTC_PCOR, WRITTEN, false},
    {PTD_PSOR, WRITTEN, false},     {PTE_PCOR, WRITTEN, false},     {NEVER, WRITTEN, false},
    {PCC_FTM0, CGC, true},          {PCC_FTM3, CGC, true},          {PCC_PORTC, CGC, true},
    {PCC_PORTD, CGC, true},         {PCC_PORTE, 0U, true}, // clock gated: PTE_PCOR must stay untouched
};

std::string trace; // per access of the walk: the index of the register written ('0'..), 'I' for the indicator
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
    static Register sink{0U, 0U, false};
    return sink;
}

volatile uint32_t* reg(uint32_t const address)
{
    Register& r = find(address);
    if (!r.isGate)
    {
        trace += static_cast<char>('0' + (&r - &registers[0]));
    }
    return &r.value;
}
} // namespace test_registers

/* Table in the order of tools/bswgen: the PWM groups first, then the safe_off outputs; one entry behind the terminator
 * that no walk may reach. */
extern "C" NxSafeOutput const nxSafeOutputs[] = {
    {FTM0_SWOCTRL, 0x3FU, PCC_FTM0, CGC},
    {FTM3_SWOCTRL, 0x0FU, PCC_FTM3, CGC},
    {PTC_PCOR, 1U << 11U, PCC_PORTC, CGC},
    {PTD_PSOR, 1U << 17U, PCC_PORTD, CGC},
    {PTE_PCOR, 1U << 3U, PCC_PORTE, CGC},
    {0U, 0U, 0U, 0U},
    {NEVER, 0xFFFFFFFFU, 0U, 0U},
};

extern "C"
{
volatile uint32_t nxSafeStateLatch;
volatile uint32_t nxSafeStateEntries;
}

namespace bios
{
Io::PinConfiguration Io::configuration[Io::NUMBER_OF_INPUTS_AND_OUTPUTS] = {};
int Io::setCalls                                                         = 0;
std::string* Io::trace                                                   = nullptr;
} // namespace bios

namespace
{
void reset()
{
    for (auto& r : test_registers::registers)
    {
        if (!r.isGate)
        {
            r.value = WRITTEN;
        }
    }
    test_registers::trace.clear();
    ::bios::Io::setCalls = 0;
    ::nxSafeStateLatch   = 0U;
    ::nxSafeStateEntries = 0U;
}

std::string degradedTrace(uint32_t const entries)
{
    reset();
    ::safety::SafeState::enterDegradedState(entries);
    return test_registers::trace;
}

void testOnlyTheEntriesOfTheMask()
{
    CHECK(degradedTrace(0x1U) == "0");
    CHECK(test_registers::registers[0].value == 0x3FU);
    CHECK(test_registers::registers[1].value == WRITTEN); // the other PWM group keeps running
    CHECK(degradedTrace(0x2U) == "1");
    CHECK(test_registers::registers[1].value == 0x0FU);
    CHECK(degradedTrace(0x6U) == "12"); // a group of a PWM group and its enable (IO-45)
    CHECK(test_registers::registers[2].value == (1U << 11U));
    CHECK(degradedTrace(0x8U) == "3");
    CHECK(degradedTrace(0x0U).empty());
}

void testGatedPeripheralIsSkipped()
{
    CHECK(degradedTrace(0x10U).empty()); // entry 4: its clock is gated, the access would bus fault
    CHECK(test_registers::registers[4].value == WRITTEN);
}

void testBitsBehindTheTerminatorReachNothing()
{
    CHECK(degradedTrace(0xFFFFFFE0U).empty());
    CHECK(test_registers::registers[5].value == WRITTEN);
    CHECK(test_registers::unknownAccess == 0);
}

void testAllBitsEqualTheFullWalk()
{
    reset();
    ::nxSafeOutputsSwitchOff();
    std::string const full = test_registers::trace;
    uint32_t values[6];
    for (int i = 0; i < 6; ++i)
    {
        values[i] = test_registers::registers[i].value;
    }
    CHECK(degradedTrace(0xFFFFFFFFU) == full);
    for (int i = 0; i < 6; ++i)
    {
        CHECK(test_registers::registers[i].value == values[i]);
    }
    // and entry by entry: the single-bit walks together are the full walk
    std::string pieces;
    for (uint32_t bit = 0U; bit < 32U; ++bit)
    {
        pieces += degradedTrace(1U << bit);
    }
    CHECK(pieces == full);
}

void testNoLatchNoIndicator()
{
    reset();
    ::safety::SafeState::enterDegradedState(0x3U);
    CHECK(::nxSafeStateLatch == 0U);
    CHECK(::nxSafeStateEntries == 0U);
    CHECK(::bios::Io::setCalls == 0);
    CHECK(test_registers::trace.find('I') == std::string::npos);
}
} // namespace

int main()
{
    testOnlyTheEntriesOfTheMask();
    testGatedPeripheralIsSkipped();
    testBitsBehindTheTerminatorReachNothing();
    testAllBitsEqualTheFullWalk();
    testNoLatchNoIndicator();
    std::printf("SafeStateDegradedHostTest: %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
