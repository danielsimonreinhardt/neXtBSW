/* SYS-41 host test stub of safeWatchdog/SafeWatchdog.h: records 'W' and closes the service bracket like the real
 * SafeWatchdog::cyclic (ecu/safety/safeWatchdog has its own host test). */
#pragma once

#include "../cycleStubs.h"

#include <safeSupervisor/SafeSupervisor.h>

namespace safety
{
class SafeWatchdog
{
public:
    void init() {}

    void cyclic()
    {
        ::test_trace::add('W');
        SafeSupervisor::getInstance().safeWatchdogSequenceMonitor.hit(SafeSupervisor::EnterLeaveSequence::LEAVE);
    }
};
} // namespace safety
