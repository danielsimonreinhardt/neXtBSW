/* SYS-24: the only place where SafeIo touches a register of the generated register list
 * (bsp/safeIo/safeIoConfiguration.h). A 32-bit access at an address the generator derived from the device header;
 * SYS-153: an 8-bit access for a register the reference manual defines as one byte (e.g. PMC_LVDSC1). The width is
 * part of the entry (RegisterCheck.h, Entry::width), the generator chooses it.
 *
 * The host test (tools/test entry safe-io) puts its own header of the same name in front of this one
 * (test/host/ioStubs/safeIo/RegisterAccess.h) and maps the addresses onto a register model with the PCR lock of the
 * silicon, stuck bits, lost writes and the access width of every register. */
#pragma once

#include <cstdint>

namespace safety
{
namespace registers
{
inline uint32_t read(uintptr_t const address) { return *reinterpret_cast<uint32_t const volatile*>(address); }

inline void write(uintptr_t const address, uint32_t const value)
{
    *reinterpret_cast<uint32_t volatile*>(address) = value;
}

// SYS-153: a byte register (ldrb/strb)
inline uint8_t read8(uintptr_t const address) { return *reinterpret_cast<uint8_t const volatile*>(address); }

inline void write8(uintptr_t const address, uint8_t const value)
{
    *reinterpret_cast<uint8_t volatile*>(address) = value;
}
} // namespace registers
} // namespace safety
