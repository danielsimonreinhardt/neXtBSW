// NVM-13: the CSEc parameter RAM (CSE_PRAM) of the S32K1 family - register view only.
//
// 128 bytes, eight 128-bit pages of four 32-bit words ([RM13] 36.5.13.4). NVM-21: its address depends on the
// sub-series (0x1400_1000 on the S32K14x, 0x1400_0800 on the S32K11x) and is handed in by the caller, see
// csec/CsecPramAddress.h. Byte 0 of a message is bits 31:24 of its word (SVD field BYTE_0; NXP SDK csec driver
// DATA_8HU), so a word is written as the big-endian value of four message bytes. Page 0 word 0 is the command header:
// FuncID bits 31:24, FuncFormat 23:16, CallSeq 15:8, KeyID 7:0; writing it LOCKS the interface and STARTS the command
// ([RM13] 36.5.13.3), so it is written last. Page 0 word 1 bits 31:16 return the error bits. Only aligned 32-bit
// writes: 8- and 16-bit writes end in a transfer error ([RM13] 37.5.11).
//
// Always inlined: the launch runs from RAM and must not call into the flash (CsecRecovery.cpp).
// The host test replaces this header by test/host/stubs/csec/CsecPram.h (a model of the CSEc).

#pragma once

#include "csec/CsecPramAddress.h"

#include <cstdint>

namespace platform
{
namespace csec
{
__attribute__((always_inline)) inline uint32_t volatile* pram(uintptr_t const base)
{
    return reinterpret_cast<uint32_t volatile*>(base);
}

__attribute__((always_inline)) inline void pramWrite(uintptr_t const base, uint32_t const word, uint32_t const value)
{
    pram(base)[word] = value;
}

__attribute__((always_inline)) inline uint32_t pramRead(uintptr_t const base, uint32_t const word)
{
    return pram(base)[word];
}
} // namespace csec
} // namespace platform
