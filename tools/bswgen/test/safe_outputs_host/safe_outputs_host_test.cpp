// IO-32: prints the safe outputs table as the family port defines it (platforms/s32k1xx/port/main/src/os/isr/
// isr_wdg.cpp, compiled with UNIT_TEST, i.e. without the naked handlers) from the generat of a project.
// Run by tools/bswgen/test_bswgen.py (BswGenSafeOutputsHostTest), which compares the lines with board.yaml.
#include "hardFaultHandler/safeOutputs.h"

#include <cstdio>

int main()
{
    unsigned count = 0U;
    for (NxSafeOutput const* e = nxSafeOutputs; e->address != 0U; ++e)
    {
        std::printf(
            "ENTRY 0x%08X 0x%08X 0x%08X 0x%08X\n",
            static_cast<unsigned>(e->address),
            static_cast<unsigned>(e->value),
            static_cast<unsigned>(e->gateAddress),
            static_cast<unsigned>(e->gateMask));
        ++count;
    }
    std::printf("COUNT %u\n", count);
    return 0;
}
