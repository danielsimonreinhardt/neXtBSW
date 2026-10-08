/* SYS-41 host test stub of safeSupervisor/SafeSupervisor.h for the ECC poll: records every trigger with its
 * context instead of entering limp home and resetting (ecu/safety/safeSupervisor has its own test). */
#pragma once

#include <cstdint>

namespace safety
{
class SafeSupervisor
{
public:
    struct TriggerMonitor
    {
        uint32_t triggers    = 0U;
        uint32_t lastContext = 0U;

        void trigger(uint32_t const context = 0U)
        {
            ++triggers;
            lastContext = context;
        }
    };

    TriggerMonitor mpuEnableMonitor;
    TriggerMonitor mpuConfigMonitor;
    TriggerMonitor dmaEccMonitor;

    static SafeSupervisor& getInstance();
};
} // namespace safety
