// EPK of the application (RTE-26, calibration stage K1): identity of this build as a string in the image.
// tools/a2lgen reads it from the ELF (symbol xcp::EPK) and writes EPK and ADDR_EPK into the A2L, so a calibration
// tool can tell whether an A2L belongs to the software on the ECU. The text comes from tools/a2lgen/epk.py, which
// the build runs every time (ecu/xcp/CMakeLists.txt, target xcpEpk).

#include <xcp/xcpEpk.h>

#include <cstdint>

// the start line "XCP EPK %s" (XcpSystem.cpp) fits one logger entry (SYS-16, tools/logcheck/bounds.yaml)
static_assert(sizeof(NX_XCP_EPK) - 1U <= 96U, "EPK longer than tools/a2lgen/epk.py MAX_LENGTH");

namespace xcp
{
extern char const EPK[];
char const EPK[] = NX_XCP_EPK;
// TOOL-151: bytes of the text (without the terminating zero) - the read-only XCP region at ADDR_EPK (XcpSystem.cpp)
extern uint32_t const EPK_LENGTH;
uint32_t const EPK_LENGTH = sizeof(NX_XCP_EPK) - 1U;
} // namespace xcp
