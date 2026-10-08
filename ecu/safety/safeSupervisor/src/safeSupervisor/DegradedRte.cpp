/* SYS-117: the degraded groups for the RTE signal safety.degraded (tools/rtegen). tools/rtegen declares this reader in
 * the generated RteConfig.cpp when a required port reads safety.degraded, safety.degraded.<GROUP> or
 * safety.qlf.degraded; bit k is safety.output_groups[k] of the project. The application learns this way that a group
 * is in its safe state and can adapt the remaining function (concept section 4.1, step 3); a DTC samples the same
 * port (diag.yaml dtcs[].event.port) to put the degradation into the error memory.
 *
 * Read only - it runs in the application tasks, which have no write access to the protected RAM. Own translation
 * unit, like DeviceReportRte.cpp: linked only when the generated RTE calls it. In a project without a degraded
 * mapping it reports "nothing degraded" (there is no SD there). */

#include "safeSupervisor/SafeSupervisor.h"

#include <cstdint>

namespace safety
{
namespace degraded
{
namespace rte
{
bool readGroups(uint32_t& groups)
{
#if NX_SAFE_SUPERVISOR_DEGRADED
    return ::safety::SafeSupervisor::getInstance().degradedGroups(groups);
#else
    groups = 0U;
    return true;
#endif
}
} // namespace rte
} // namespace degraded
} // namespace safety
