/* SYS-24: definition of the register list of SafeIo from the generated configuration (tools/bswgen,
 * bsp/safeIo/safeIoConfiguration.h). Own library, see safeIo/SafeIoRegisters.h. */

#include "safeIo/SafeIoRegisters.h"

#include <bsp/safeIo/safeIoConfiguration.h>
#include <bsp/safeOutputs/safeOutputsConfiguration.h> // SYS-138: the clock gates of the read-back list

namespace safety
{
namespace registers
{
// X(class, name, register address, mask, expected, lock bits, lock clear, access width in bits) of the generated
// list (SYS-153: the width, 32 or 8)
#define NX_SAFE_IO_ENTRY(registerClass, name, address, mask, expected, lockBits, lockClear, width) \
    {static_cast<uintptr_t>(address), (mask), (expected), (lockBits), (lockClear), RegisterClass::registerClass, \
     static_cast<uint8_t>(width)},

// clang-format off
constexpr Entry safeIoRegisters[NX_SAFE_IO_REGISTER_COUNT + 1U] = {
    NX_SAFE_IO_REGISTERS(NX_SAFE_IO_ENTRY)
    {0U, 0U, 0U, 0U, 0U, RegisterClass::NONE, WIDTH_32},
};
// clang-format on
#undef NX_SAFE_IO_ENTRY

constexpr size_t safeIoRegisterCount = NX_SAFE_IO_REGISTER_COUNT;

// A list shorter than its count leaves entries of class NONE behind and fails here; a longer one does not compile.
static_assert(wellFormed(safeIoRegisters, safeIoRegisterCount), "generated register list of SafeIo is malformed");
static_assert(
    safeIoRegisters[safeIoRegisterCount].registerClass == RegisterClass::NONE, "end entry of the register list");

/* SYS-138 (review K6): the read-back of the safe state judges an entry whose clock is gated as not confirmed (SafeIo.cpp).
 * That is only right if this list expects the gate on - then a gated clock after SafeIo::init() is a register fault in
 * any case, never a normal state. Checked here against the read-back list of the same generat (class CLOCK_GATE, same
 * word, the gate bits judged and expected set): a generator that dropped a gate from this list does not build. */
struct ReadbackGate
{
    uintptr_t address;
    uint32_t mask;
};

#define NX_SAFE_IO_READBACK_GATE(name, address, mask, expected, gate, gateMask) \
    {static_cast<uintptr_t>(gate), static_cast<uint32_t>(gateMask)},

// clang-format off
constexpr ReadbackGate readbackGates[NX_SAFE_OUTPUT_COUNT + 1U] = {
    NX_SAFE_OUTPUT_READBACKS(NX_SAFE_IO_READBACK_GATE)
    {0U, 0U},
};
// clang-format on
#undef NX_SAFE_IO_READBACK_GATE

// Named constant like safeIoRegisterCount: the macro compared directly is "unsigned < 0" without safe outputs
// (-Werror=type-limits, e.g. buildtest_s32k148_min); same bound, same loop.
constexpr size_t readbackGateCount = NX_SAFE_OUTPUT_COUNT;

constexpr bool gateExpectedOn(ReadbackGate const& gate)
{
    for (size_t i = 0U; i < safeIoRegisterCount; ++i)
    {
        Entry const& e = safeIoRegisters[i];
        if ((e.registerClass == RegisterClass::CLOCK_GATE) && (e.address == gate.address)
            && ((e.mask & gate.mask) == gate.mask) && ((e.expected & gate.mask) == gate.mask))
        {
            return true;
        }
    }
    return false;
}

constexpr bool readbackGatesExpectedOn()
{
    for (size_t i = 0U; i < readbackGateCount; ++i)
    {
        if (!gateExpectedOn(readbackGates[i]))
        {
            return false;
        }
    }
    return true;
}

static_assert(readbackGatesExpectedOn(), "SYS-138: a clock gate of the read-back list is not checked on by SafeIo");
} // namespace registers
} // namespace safety
