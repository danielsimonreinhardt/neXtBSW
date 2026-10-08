#pragma once

/* SYS-151: the pins of the safe_off outputs for the hold of the commanded level (SafeOffCommandHold.h), straight from
 * the generated safe outputs table (bsp/safeOutputs/safeOutputsConfiguration.h) - the second block, the outputs a
 * module declares safe_off:
 *
 *   active     the read-back of SYS-30 (NX_SAFE_OUTPUT_READBACKS: PDIR of the port under the pin's mask, `expected`
 *              is the inactive level) - the same signal SafeIo judges in S1 and Io::getPin() reads
 *   switchOff  the store of the table walk (NX_SAFE_OUTPUTS: PCOR bit of an active-high, PSOR bit of an active-low
 *              output) - one store to a set/clear register, no read-modify-write of PDOR
 *   commanded  integration::safeoff::Commanded, written by the drivers' write path
 *
 * Both registers are skipped while the clock of the port is gated (PCC CGC 0), as in every walk of the table. No new
 * configuration value: addresses, masks and levels are the generated ones. Register access through
 * NX_SAFE_OUTPUT_REG (hardFaultHandler/safeOutputs.h), which a host test points at its register model. */

#include "bsp/safeOutputs/safeOutputsConfiguration.h"
#include "hardFaultHandler/safeOutputs.h"
#include "integration/SafeOffOutputs.h"

#include <cstdint>

namespace integration
{
namespace safeoffpins
{
struct Store
{
    uint32_t address;
    uint32_t value;
    uint32_t gateAddress;
    uint32_t gateMask;
};

struct Level
{
    uint32_t address;
    uint32_t mask;
    uint32_t inactive;
    uint32_t gateAddress;
    uint32_t gateMask;
};

// clang-format off
#define NX_SAFE_OFF_STORE_(name, address, value, gate, gateMask) {(address), (value), (gate), (gateMask)},
#define NX_SAFE_OFF_LEVEL_(name, address, mask, expected, gate, gateMask) {(address), (mask), (expected), (gate), (gateMask)},
constexpr Store STORES[NX_SAFE_OUTPUT_COUNT + 1] = {NX_SAFE_OUTPUTS(NX_SAFE_OFF_STORE_){0U, 0U, 0U, 0U}};
constexpr Level LEVELS[NX_SAFE_OUTPUT_COUNT + 1] = {NX_SAFE_OUTPUT_READBACKS(NX_SAFE_OFF_LEVEL_){0U, 0U, 0U, 0U, 0U}};
#undef NX_SAFE_OFF_STORE_
#undef NX_SAFE_OFF_LEVEL_
// clang-format on

/** Table entry of safe_off output \p index. */
inline uint32_t entryOf(uint8_t const index) { return static_cast<uint32_t>(::integration::safeoff::FIRST) + index; }

inline bool clocked(uint32_t const gateAddress, uint32_t const gateMask)
{
    return (gateAddress == 0U) || ((*NX_SAFE_OUTPUT_REG(gateAddress) & gateMask) != 0U);
}

/** The pin of safe_off output \p index reads active (false while its port is not clocked). */
inline bool active(uint8_t const index)
{
    Level const& level = LEVELS[entryOf(index)];
    if (!clocked(level.gateAddress, level.gateMask))
    {
        return false;
    }
    uint32_t const value = *NX_SAFE_OUTPUT_REG(level.address);
    return (value & level.mask) != level.inactive;
}

/** Drives safe_off output \p index inactive with the store of the table walk. */
inline void switchOff(uint8_t const index)
{
    Store const& store = STORES[entryOf(index)];
    if (clocked(store.gateAddress, store.gateMask))
    {
        *NX_SAFE_OUTPUT_REG(store.address) = store.value;
    }
}

/** The driver of safe_off output \p index commands it active. */
inline bool commanded(uint8_t const index) { return ::integration::safeoff::Commanded::isActive(index); }

} // namespace safeoffpins
} // namespace integration
