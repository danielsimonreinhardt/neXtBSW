// SYS-116: prints the extended safe outputs table and the safe state groups as the target defines them
// (platforms/s32k1xx/hardFaultHandler/src/SafeOutputSelection.cpp, compiled into this unit with the generat of a
// project) - its compile-time checks against NX_SAFE_OUTPUTS run as part of the build. Run by
// tools/bswgen/test_bswgen_safe_state.py, which compares the lines with test/safe_state_expectation.py.
#include "../../../../platforms/s32k1xx/hardFaultHandler/src/SafeOutputSelection.cpp" // NOLINT: unit under test

#include <cstdio>

namespace
{
#define NX_PRINT_GROUP(name, index, allowed, test, testUs)                                              \
    std::printf("GROUP %s 0x%X %u %u\n", #name, static_cast<unsigned>(allowed), static_cast<unsigned>(test), \
                static_cast<unsigned>(testUs));

void printGroups() { NX_SAFE_STATE_GROUPS(NX_PRINT_GROUP) }
} // namespace

int main()
{
    unsigned count = 0U;
    for (NxSafeOutputSelect const* e = nxSafeOutputsSelect; e->address != 0U; ++e)
    {
        std::printf(
            "ENTRY 0x%08X 0x%08X 0x%08X 0x%08X 0x%08X 0x%08X 0x%08X 0x%08X 0x%08X\n",
            static_cast<unsigned>(e->address),
            static_cast<unsigned>(e->value),
            static_cast<unsigned>(e->gateAddress),
            static_cast<unsigned>(e->gateMask),
            static_cast<unsigned>(e->group),
            static_cast<unsigned>(e->groupGateAddress),
            static_cast<unsigned>(e->groupGateMask),
            static_cast<unsigned>(e->shortLow),
            static_cast<unsigned>(e->shortHigh));
        ++count;
    }
    std::printf("COUNT %u\n", count);
    printGroups();
    for (uint32_t g = 0U; g < nxSafeStateGroupCount(); ++g)
    {
        std::printf("ALLOWED %u 0x%X\n", static_cast<unsigned>(g), static_cast<unsigned>(nxSafeStateAllowed(g)));
    }
    return 0;
}
