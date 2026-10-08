#include "integration/SafetyResetReport.h"

#include <dem/SafetyResetReport.h>

namespace integration
{
namespace
{
// SYS-113: constant initialized (constexpr constructor), written by main() before the scheduler starts and from then
// on only by the BSP task; QM RAM like the fault memory it feeds - nothing of the safety layer
::dem::safetyreset::Report safetyResetReport;
} // namespace

void setSafetyResetStart(bool const counted, uint32_t const count, uint32_t const lastCause)
{
    safetyResetReport.setStart(counted, count, lastCause);
}

void reportSafetyResetStart() { safetyResetReport.update(); }

} // namespace integration
