#pragma once

#include "ecuState/EcuState.h"

#include <cstdint>

namespace ecustate
{

/**
 * Role power_supply (doc/architecture/README.md, section 5): what the ECU state management needs from the
 * component that powers the MCU. A system basis chip is one implementation (sbc::Uja113xPowerSupply), a board
 * with a permanent supply uses PermanentPowerSupply, an MCU low-power mode would be another one.
 *
 * All calls come from one task context, the one the implementation requires (the integration passes it to the
 * ECU state system). Non-blocking.
 */
class IPowerSupply
{
public:
    enum class PowerDownResult : uint8_t
    {
        STARTED,        // power down running, the MCU loses its supply or stops shortly
        NOT_SUPPORTED,  // this supply cannot power down (now)
        WAKEUP_PENDING, // refused because a wake-up event is pending
        FAILED,         // communication or verification error
    };

    /**
     * Short name for console and log: a string literal of at most 16 characters - the log lines of the ECU state
     * system are sized for it (SYS-16, checked by tools/logcheck).
     */
    virtual char const* getName() const = 0;

    /** True if powerDown() can succeed in the current state. */
    virtual bool isPowerDownSupported() const = 0;

    /** Reasons of the current start (ecustate::wakeup bits), wakeup::UNKNOWN if they cannot be determined. */
    virtual uint32_t getStartupWakeupSources() const = 0;

    /**
     * NVM-34: true if the supply itself went through a start for the current start of the MCU (power-on, wake-up or
     * its own reset); false if the MCU started alone and getStartupWakeupSources() only says so (an SBC whose reset
     * line does not reach the MCU sees no MCU software reset). Used by the reset statistics of the application, whose
     * supply row counts only the starts of the supply. A supply that cannot tell keeps the default.
     */
    virtual bool restartedForThisStart() const { return true; }

    /** Wake-up events seen since the previous call (ecustate::wakeup bits), 0 if the supply does not report any. */
    virtual uint32_t takeWakeupEvents() = 0;

    /**
     * Wake-up sources this supply can arm on this board (ecustate::wakeup bits: CAN buses of its own
     * transceivers, its local wake-up inputs, TIMER for a cyclic wake-up), 0 if it cannot arm any. The ECU
     * state management arms only the intersection with the project selection and reports the difference
     * (SYS-02); is the intersection empty, a sleep request is refused, because the ECU would not come back.
     */
    virtual uint32_t getSupportedWakeupSources() const = 0;

    /**
     * Period this supply would really use for a cyclic wake-up of \p requestedMs (its own grid, e.g. the
     * watchdog periods of a SBC), 0 if it has no cyclic wake-up or \p requestedMs is 0.
     */
    virtual uint32_t getCyclicWakeupMs(uint32_t requestedMs) const = 0;

    /**
     * Arms \p selection (already reduced to getSupportedWakeupSources()) and removes the MCU supply (or
     * enters the low-power mode). A selection without any source is refused with NOT_SUPPORTED.
     */
    virtual PowerDownResult powerDown(uint32_t nowMs, WakeupSelection const& selection) = 0;

protected:
    IPowerSupply()                               = default;
    IPowerSupply(IPowerSupply const&)            = default;
    IPowerSupply& operator=(IPowerSupply const&) = default;
    ~IPowerSupply()                              = default;
};

} // namespace ecustate
