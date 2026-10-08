// SYS-30: prints the read-back list of the safe outputs table as the target defines it
// (platforms/s32k1xx/port/safety/safeIo/src/registerList/SafeOutputReadbackList.cpp, compiled into this unit with the
// generat of a project and the real NXP register layouts) - its compile-time check runs as part of the build. Run by
// tools/bswgen/test_bswgen_safe_readback.py, which compares the lines with safe_outputs_expectation.expected_readbacks.
#include "../../../../platforms/s32k1xx/port/safety/safeIo/src/registerList/SafeOutputReadbackList.cpp" // NOLINT

#include <cstdio>

int main()
{
    using ::safety::registers::safeOutputReadbackCount;
    using ::safety::registers::safeOutputReadbacks;
    for (size_t i = 0U; i < safeOutputReadbackCount; ++i)
    {
        auto const& r = safeOutputReadbacks[i];
        std::printf(
            "READBACK 0x%08X 0x%08X 0x%08X 0x%08X 0x%08X\n",
            static_cast<unsigned>(r.address),
            static_cast<unsigned>(r.mask),
            static_cast<unsigned>(r.expected),
            static_cast<unsigned>(r.gate),
            static_cast<unsigned>(r.gateMask));
    }
    std::printf("COUNT %u %u\n", static_cast<unsigned>(safeOutputReadbackCount), static_cast<unsigned>(NX_SAFE_OUTPUT_COUNT));
    return 0;
}
