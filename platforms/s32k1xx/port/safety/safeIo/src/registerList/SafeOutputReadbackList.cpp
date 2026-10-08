/* SYS-30: definition of the read-back list of the safe outputs table from the generated configuration (tools/bswgen,
 * bsp/safeOutputs/safeOutputsConfiguration.h, NX_SAFE_OUTPUT_READBACKS). Own library with the register list of SafeIo
 * (nxSafeIoRegisterList): initialised read-only data must not lie in a safety library, see safeIo/SafeIoRegisters.h. */

#include "safeIo/SafeIoRegisters.h"

#include <bsp/safeOutputs/safeOutputsConfiguration.h>

namespace safety
{
namespace registers
{
// X(name, read-back register, mask, expected, clock gate register, clock gate mask) of the generated list
#define NX_SAFE_OUTPUT_READBACK_ENTRY(name, address, mask, expected, gate, gateMask)                          \
    {static_cast<uintptr_t>(address), static_cast<uint32_t>(mask), static_cast<uint32_t>(expected),          \
     static_cast<uintptr_t>(gate), static_cast<uint32_t>(gateMask)},

// clang-format off
constexpr OutputReadback safeOutputReadbacks[NX_SAFE_OUTPUT_COUNT + 1U] = {
    NX_SAFE_OUTPUT_READBACKS(NX_SAFE_OUTPUT_READBACK_ENTRY)
    {0U, 0U, 0U, 0U, 0U},
};
// clang-format on
#undef NX_SAFE_OUTPUT_READBACK_ENTRY

constexpr size_t safeOutputReadbackCount = NX_SAFE_OUTPUT_COUNT;

// One entry per entry of the safe outputs table, each complete (a read-back register, a mask, the expectation inside
// the mask, a clock gate); the end entry last.
constexpr bool readbacksWellFormed()
{
    for (size_t i = 0U; i < safeOutputReadbackCount; ++i)
    {
        OutputReadback const& e = safeOutputReadbacks[i];
        if ((e.address == 0U) || ((e.address & 3U) != 0U) || (e.mask == 0U) || ((e.expected & ~e.mask) != 0U)
            || (e.gate == 0U) || (e.gateMask == 0U))
        {
            return false;
        }
    }
    return safeOutputReadbacks[safeOutputReadbackCount].address == 0U;
}

static_assert(readbacksWellFormed(), "generated read-back list of the safe outputs table is malformed");
} // namespace registers
} // namespace safety
