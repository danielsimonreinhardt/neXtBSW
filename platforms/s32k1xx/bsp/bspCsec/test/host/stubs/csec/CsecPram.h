// Host stub of csec/CsecPram.h (NVM-13): the CSEc parameter RAM goes to the CSEc model of CsecRecoveryHostTest.cpp,
// which starts a command when word 0 (the command header) is written.
// NVM-21: every access carries the address the module chose; the model answers only at the address of the part it
// plays (csec/CsecPramAddress.h, the real table) - anything else is the FlexRAM, as on the board 2026-09-30.

#pragma once

#include "csec/CsecPramAddress.h"

#include <cstdint>

namespace platform
{
namespace csec
{
void pramWrite(uintptr_t base, uint32_t word, uint32_t value);
uint32_t pramRead(uintptr_t base, uint32_t word);
} // namespace csec
} // namespace platform
