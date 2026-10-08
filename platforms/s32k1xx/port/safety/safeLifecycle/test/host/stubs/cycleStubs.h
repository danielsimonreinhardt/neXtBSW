/* SYS-41 host test stubs of the sub-checks of the safety cycle (SafetyCycleHostTest.cpp). Every sub-check records
 * one character in the trace: m safe_memory::cyclic, s SafeSystem::cyclic, o SafeIo::cyclic, W SafeWatchdog::cyclic
 * (service), d deadman check. The supervisor's own stubs (ecu/safety/safeSupervisor/test/host/stubs) add E (log
 * error) and R (reset). */

#pragma once

#include <cstdint>

namespace test_trace
{
void add(char c);
}

namespace test_cycle
{
// MPU fusa gate: locked outside the safety task, opened by ProtectedRamScopedUnlock
extern bool gateLocked;
} // namespace test_cycle
