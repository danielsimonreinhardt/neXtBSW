// Target part of the hard fault dump reader: the dump region from the linker script.

#include "hardFaultHandler/HardFaultDump.h"

#include <cstdio>

extern "C"
{
extern uint32_t __hard_fault_dump_start[];
}

namespace
{
void printLine(char const* const line) { printf("%s\r\n", line); }
} // namespace

namespace hardfault
{
uint32_t* getDumpRegion() { return __hard_fault_dump_start; }

void prepareDump(bool const ramRetained)
{
    if (ramRetained)
    {
        return;
    }
    uint32_t* const dump = getDumpRegion();
    for (size_t i = 0U; i < DUMP_WORDS; ++i)
    {
        dump[i] = 0U;
    }
}

DumpState reportAndClearDump()
{
    uint32_t* const dump  = getDumpRegion();
    DumpState const state = formatDump(dump, &printLine);
    if (state != DumpState::NONE)
    {
        dump[DUMP_MAGIC / 4U] = 0U;
    }
    return state;
}

} // namespace hardfault
