#pragma once

/* SYS-196 (decision SYS194-4 b): the take-backs of foreign safe_off levels (hold of SYS-151) as the DTC of the BSW
 * event safety.safe_off_take_back (libs/bsw/dem dem/OccurrenceReport.h, record: src/integration/SafeOffTakeBackRecord.h).
 *
 * The hold counts the occurrences in its 1-ms poll (TASK_BSP); the BSP task (systems::BspSystem, 10 ms, the same task)
 * hands them to the fault memory once the application registered one (dem::registry.memory). Only a DTC the
 * project's diag.yaml describes is reported - nxconfig allows it only in a project with a safe_off output; a project
 * without one has no hold and nothing to report (empty functions). Recording only: QM, never from the safety task. */

#include <cstdint>

namespace integration
{

/** Every cycle of the BSP task: hands the occurrences of the hold to the fault memory. */
void reportSafeOffTakeBacks();

} // namespace integration

namespace bswstatus
{
namespace rte
{
/**
 * The values bsw.safeoff.* of the diagnostic description (tools/rtegen BSW_STATUS "safeoff"): \p output console
 * number of the safe_off output of the last occurrence (0xFF: none since the start), \p outputTakeBacks its take-backs,
 * \p takeBacks those of every safe_off output - each since the start. Any context, single words.
 */
void readSafeOffStatus(uint8_t& output, uint32_t& outputTakeBacks, uint32_t& takeBacks);
} // namespace rte
} // namespace bswstatus
