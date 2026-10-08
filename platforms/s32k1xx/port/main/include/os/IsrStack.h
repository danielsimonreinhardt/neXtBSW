/**
 * SYS-97: interrupt stack (MSP) of the family port - fill pattern, high-water mark, and the record of an overflow
 * the hard fault saw.
 *
 * The budget of the MSP is proven by tools/stackuse (profile per image, tools/stackuse/profiles); the guard below it
 * (linker script .stack_guard, MPU descriptor MemoryProtection::STACK_GUARD_REGION) turns an overflow into a hard
 * fault. The fill pattern is the measured counter-check of the analysis on the board: main() fills the free part of
 * the MSP first, the high-water mark then covers the start-up phase and every interrupt since.
 *
 * The functions of the pattern are header-only so that the host test (os/isr/test/host) runs them on a plain array;
 * the target functions (linker symbols, stack pointer) are defined in src/os/isr/isr_sys.cpp.
 */

#pragma once

#include <cstdint>

namespace isrstack
{
/** Word written into the unused part of the MSP; any other value there means the stack reached it. */
static constexpr uint32_t FILL_WORD = 0x5AFE57ACU;

/** Writes FILL_WORD into [from, to). */
inline void fillWords(uint32_t volatile* from, uint32_t const volatile* const to)
{
    while (from < to)
    {
        *from = FILL_WORD;
        ++from;
    }
}

/**
 * Bytes at the bottom of [limit, top) that still hold FILL_WORD, counted from the limit upwards up to the first
 * other word (the stack grows down from top towards limit).
 */
inline uint32_t untouchedBytes(uint32_t const volatile* limit, uint32_t const volatile* const top)
{
    uint32_t bytes = 0U;
    while ((limit < top) && (*limit == FILL_WORD))
    {
        bytes += 4U;
        ++limit;
    }
    return bytes;
}

/** Use of the interrupt stack since fillBelowStackPointer(). */
struct Use
{
    uint32_t stackBytes;  ///< size of the MSP (__StackTop - __StackLimit)
    uint32_t usedBytes;   ///< high-water mark: bytes below the top that were written at least once
    uint32_t guardBytes;  ///< size of the no-write guard directly below the MSP
    uint32_t guardStart;  ///< address of the guard
};

#ifndef UNIT_TEST
/** main(), first statement: fills the MSP from its limit up to a little below the current stack pointer. */
void fillBelowStackPointer();

/** High-water mark of the MSP since the fill. */
Use measure();
#endif

} // namespace isrstack

extern "C"
{
/**
 * MSP the hard fault handler found below the stack limit (an overflow into the guard), 0 if none. Written by
 * HardFault_Handler_Final (after the safe outputs walk and the dump, before any C code uses the stack).
 */
extern uint32_t volatile nxIsrStackOverflowMsp;
}
