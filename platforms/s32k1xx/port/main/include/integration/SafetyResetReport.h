#pragma once

/* SYS-113: the cause of the safety reset before this start as a DTC (libs/bsw/dem dem/SafetyResetReport.h).
 *
 * main() hands over what the reset-loop limit of SYS-43 found in its no-init record once per start; the BSP task
 * (systems::BspSystem, 10 ms) passes it on to the fault memory as soon as the application registered one
 * (dem::registry.memory) - FAILED for the cause of the safety reset (and safety.reset_loop after several in a row)
 * until the fault memory took it over, PASSED for the other causes. Only the DTCs the project's diag.yaml describes
 * are reported; a project without them does nothing but one look-up. Recording only: the record and the reaction stay
 * with the safe supervisor, nothing here writes either. */

#include <cstdint>

namespace integration
{

/** Once per start in main(), after startResetLoopLimit() and before the tasks run (resetloop::StartResult). */
void setSafetyResetStart(bool counted, uint32_t count, uint32_t lastCause);

/** Every cycle of the BSP task: hands the result to the fault memory once one is registered. */
void reportSafetyResetStart();

} // namespace integration
