/* Host test stub of safeSupervisor/SafeSupervisor.h for RomCheckHostTest (SYS-38): only the monitor RomCheck
 * triggers. The reaction to ROM_CHECK_FAILURE (safe state S1 held, no reset) is shown by the real supervisor in
 * ecu/safety/safeSupervisor/test/host/SafeSupervisorHostTest.cpp (testRomCheckFailureHoldsTheSafeState). */

#pragma once

#include "safeMemory/SafetyShell.h"

namespace safety
{
class SafeSupervisor
{
public:
    struct RomCheckMonitor
    {
        int triggers       = 0;
        int outsideShell   = 0; // triggers without an open safety shell (the monitor lives in safety RAM)

        void trigger()
        {
            ++triggers;
            if (::romcheck_test::shellDepth() <= 0)
            {
                ++outsideShell;
            }
        }
    };

    static SafeSupervisor& getInstance()
    {
        static SafeSupervisor instance;
        return instance;
    }

    RomCheckMonitor romCheckMonitor;
};
} // namespace safety
