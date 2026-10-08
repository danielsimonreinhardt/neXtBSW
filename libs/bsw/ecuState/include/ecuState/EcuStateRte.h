#pragma once

#include "ecuState/EcuState.h"

#include <cstdint>

/**
 * Hand-over between the ECU state management and the RTE (tools/rtegen targets ecu.state, ecu.wakeup,
 * ecu.wakeup.<source>, ecu.sleep_request). The RTE runs in its own task, every value is a single atomic word.
 */
namespace ecustate
{
namespace rte
{
// ---- RTE side: called by the generated RteConfig.cpp, which declares these functions itself (the RTE library
// does not see this include directory). The parameter types are part of the linker names, so a changed type
// does not link instead of reading garbage.

/** Current state (ecustate::State as number). */
void readState(uint8_t& state);
/** Reasons of this start (ecustate::wakeup bits), 0 until the run level is reached. */
void readWakeupSources(uint32_t& sources);
/** Combined sleep request of all components connected to ecu.sleep_request (true: sleep wanted). */
void writeSleepRequest(bool request);

// ---- ECU state side

void publish(State state, uint32_t startupWakeupSources);
/**
 * NVM-26: the state published last (the controller publishes at the end of every cycle), readable from any task -
 * for BSW parts that act on the state without being part of the ECU state management (s32k1xx data flash: no new
 * job from STANDBY on). STARTUP before the first publish.
 */
State publishedState();
/** True once writeSleepRequest() was called: a component is connected to ecu.sleep_request. */
bool isSleepRequestConnected();
bool isSleepRequested();
/** Back to the initial values (host tests). */
void reset();
} // namespace rte
} // namespace ecustate
