/* SYS-24 host test stub of safeIo/RegisterAccess.h (the include path puts this directory in front of the real one):
 * SafeIo reads and writes the registers of its generated list through these functions, and the host test
 * (SafeIoHostTest.cpp) maps the addresses onto the register model of ioStubs/mcu/mcu.h - the PCR lock of the
 * silicon, stuck bits, lost writes. An address the model does not know is recorded and reads as 0xDEADBEEF.
 * SYS-153: the model knows the width of every register; an access of the other width (32 bits on the byte register
 * PMC_LVDSC1, 8 bits on a word register) is recorded and acts like a bus error: it reads 0xDEADBEEF resp. 0xEF, a
 * write is lost. */
#pragma once

#include <cstdint>

namespace safety
{
namespace registers
{
uint32_t read(uintptr_t address);
void write(uintptr_t address, uint32_t value);
uint8_t read8(uintptr_t address);
void write8(uintptr_t address, uint8_t value);
} // namespace registers
} // namespace safety
