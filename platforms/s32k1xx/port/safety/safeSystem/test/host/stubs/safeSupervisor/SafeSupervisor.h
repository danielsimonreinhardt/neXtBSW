/* Host test stub of safeSupervisor/SafeSupervisor.h (IO-16): records every trigger with its
 * context instead of entering limp home and resetting the MCU. Only the two monitors SafeSystem
 * uses and the events of the real header are present. */

#pragma once

#include <cstdint>

namespace safety
{

class SafeSupervisor
{
public:
    enum class Event : uint8_t
    {
        ADC_REFERENCE_ERROR,
        INTERNAL_SUPPLIES_ERROR,
    };

    struct TriggerMonitor
    {
        Event event;
        uint32_t triggers   = 0U;
        uint32_t lastContext = 0U;

        explicit TriggerMonitor(Event const e) : event(e) {}

        void trigger(uint32_t const context = 0U)
        {
            ++triggers;
            lastContext = context;
        }

        uint32_t getContext() const { return lastContext; }
    };

    TriggerMonitor adcReferenceMonitor{Event::ADC_REFERENCE_ERROR};
    TriggerMonitor internalSuppliesMonitor{Event::INTERNAL_SUPPLIES_ERROR};

    static SafeSupervisor& getInstance();
    static void resetInstance();
};

} // namespace safety
