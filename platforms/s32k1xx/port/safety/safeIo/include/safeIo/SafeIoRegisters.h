/* SYS-24: the register list SafeIo locks and checks - generated from the configuration (tools/bswgen,
 * bsp/safeIo/safeIoConfiguration.h), defined in src/registerList/SafeIoRegisterList.cpp.
 *
 * The definition lives in its own library (nxSafeIoRegisterList): the linker script allows no initialised read-only
 * data in the safety libraries ("*libsafe*", .mpu_rodata must stay empty), and the list belongs into flash anyway,
 * where no write of a faulty task can change it - like the safe outputs table of the fault handlers. */
#pragma once

#include <safeIo/RegisterCheck.h>

#include <cstddef>
#include <cstdint>

namespace safety
{
namespace registers
{
/** The list, followed by one end entry of class NONE. */
extern Entry const safeIoRegisters[];
/** Number of entries without the end entry. */
extern size_t const safeIoRegisterCount;

/**
 * SYS-30 (gap G1 of doc/safety/concept.md 9.5): what the safe state S1 must have left behind, per entry of the safe
 * outputs table (hardFaultHandler/safeOutputs.h) and in the same order - generated from the same list
 * (tools/bswgen, NX_SAFE_OUTPUT_READBACKS of bsp/safeOutputs/safeOutputsConfiguration.h), defined in
 * src/registerList/SafeOutputReadbackList.cpp. `address` is the register that shows whether the store of the walk
 * took effect - for a PWM group its software output control itself, for a safe_off output the pin level of its port
 * (not the data register: a pin held by something else must be seen); the bits under `mask` must equal `expected`.
 * An entry whose clock gate (`gate`, `gateMask`) reads off is not read - the walk skipped it too, and a read would
 * bus fault; SYS-138: such an entry is not confirmed (after SafeIo::init() every gate of this list is expected on,
 * see SafeIoRegisterList.cpp). Family neutral: only addresses and values.
 */
struct OutputReadback
{
    uintptr_t address;
    uint32_t mask;
    uint32_t expected;
    uintptr_t gate;
    uint32_t gateMask;
};

/** The read-back list, as many entries as the safe outputs table (without its end entry). */
extern OutputReadback const safeOutputReadbacks[];
extern size_t const safeOutputReadbackCount;

/**
 * SYS-30: true if `readValue` (the content of r.address) shows a safe state for its entry of the safe outputs table.
 * The bits under the mask must equal the expectation (freewheel resp. the inactive pin level). SYS-116: when the walk
 * writes another value than freewheel for the entry (`selected` != `freewheel`, an active short selected), the short
 * is a safe state as well: an entry the short leaves as it is (store 0, the bound gate-driver enable) or whose
 * read-back register is not the one written (`writeAddress`) is not judged, and the output control of the timer may
 * hold the short value. Without an active short pass 0 for writeAddress, freewheel and selected.
 */
constexpr bool readbackAccepts(
    OutputReadback const& r,
    uint32_t const readValue,
    uintptr_t const writeAddress,
    uint32_t const freewheel,
    uint32_t const selected)
{
    uint32_t const value = readValue & r.mask;
    if (selected != freewheel)
    {
        if ((selected == 0U) || (writeAddress != r.address) || (value == (selected & r.mask)))
        {
            return true;
        }
    }
    return value == r.expected;
}
} // namespace registers

/**
 * SYS-30: context of the first entry of the safe outputs table that did not read back its safe state while S1 was
 * latched (class SAFE_OUTPUT in bits 31..24, entry index in bits 15..0), 0 while none did; latched until the next
 * init(). The same fault is reported once as LOCK_REGISTER_ERROR: S1 did not take effect, the reset (S1 + S2) is the
 * one reaction left that puts the pins into their reset state.
 */
uint32_t safeIoOutputFault();

/**
 * SYS-24: context of the first entry of the register list that deviated in a cycle of SafeIo (class in bits 31..24,
 * index in bits 15..0, registers::context()), 0 while none did. The deviation is latched until the next init(): a
 * configuration register that drifted does not release the safe state by drifting back.
 */
uint32_t safeIoRegisterFault();
} // namespace safety
