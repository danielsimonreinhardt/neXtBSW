/* Host test stub of safeSupervisor/SafeSupervisor.h (SYS-05), same idea as the stub of the SafeSystem host
 * test (IO-16): it records every trigger with its context instead of entering limp home and resetting the MCU.
 * Only the monitor RteSystem.cpp uses is present.
 *
 * The real header needs the matching entry: an event RTE_TIMING_VIOLATION and a TriggerMonitor
 * rteTimingMonitor (ecu/safety/safeSupervisor). Until that is merged, ecu/application is not linked against
 * safeSupervisor and the report is compiled out (NX_RTE_SAFE_SUPERVISOR); this test defines the macro, so the
 * whole chain from the slow runnable to the event is under test. */

#pragma once

#include <cstddef>
#include <cstdint>

namespace safety
{

class SafeSupervisor
{
public:
    enum class Event : uint8_t
    {
        RTE_TIMING_VIOLATION,
    };

    static constexpr size_t MAX_RECORDED = 16U;

    struct TriggerMonitor
    {
        Event event;
        uint32_t triggers                   = 0U;
        uint32_t lastContext                = 0U;
        uint32_t recorded[MAX_RECORDED]     = {};

        explicit TriggerMonitor(Event const e) : event(e) {}

        void trigger(uint32_t const context = 0U)
        {
            if (triggers < MAX_RECORDED)
            {
                recorded[triggers] = context;
            }
            ++triggers;
            lastContext = context;
        }

        uint32_t getContext() const { return lastContext; }
    };

    TriggerMonitor rteTimingMonitor{Event::RTE_TIMING_VIOLATION};

    static SafeSupervisor& getInstance();
};

} // namespace safety
