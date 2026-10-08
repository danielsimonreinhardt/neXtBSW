#pragma once

/**
 * SYS-173 (safety review 2026-10-02, SYS69-S4, finding ECS-F01): the direct reset of the ECU state management.
 *
 * Every reaction of SM-53 (run-down step failed or timed out, shutdown time-out, sleep time-out, refused power down,
 * wake-up during the shutdown) is a request to the lifecycle to go to the restart level 0. The lifecycle carries a new
 * target level out only after the component it waits for has finished - and the shutdown time-out exists exactly
 * because a component did not finish. So the restart itself is supervised: the controller (EcuStateController) gives
 * the lifecycle EcuStateConfig::shutdownTimeoutMs after its request, and if the ECU is still running then, it resets
 * the MCU directly through this hook instead of waiting for the lifecycle (EcuStateController::isDirectResetIssued()).
 *
 * Family neutral: the ECU state management only knows this hook. The family port registers one function that resets
 * the MCU at once and does not return (s32k1xx: safe outputs table, then the software system reset of OpenBSW;
 * platforms/s32k1xx/port/main/src/integration/Integration.cpp). A family that registers nothing keeps the old
 * behaviour - the request to the lifecycle only - and "ecu status" shows the overdue restart; the MCU watchdog is then
 * the only mechanism that can end the hang.
 */
namespace ecustate
{
/** Resets the MCU now; must not return on a target. Called from the task of the power supply. */
using DirectResetFn = void (*)();

/**
 * The family port registers its function here, before the run level is reached (a static constructor is fine: the
 * pointer is constant initialised). nullptr removes it again (host tests).
 */
void setDirectReset(DirectResetFn reset);

/** The registered function, nullptr without one. */
DirectResetFn directReset();

} // namespace ecustate
