#pragma once

#include "ecuState/EcuState.h"
#include "ecuState/IPowerSupply.h"

namespace ecustate
{

/**
 * Role power_supply of a board whose MCU supply cannot be switched by software and without MCU low-power
 * module: no power down, so sleep requests are refused and the ECU stays in RUN.
 */
class PermanentPowerSupply final : public IPowerSupply
{
public:
    explicit PermanentPowerSupply(uint32_t const startupWakeupSources = wakeup::UNKNOWN)
    : _startupWakeupSources(startupWakeupSources)
    {}

    char const* getName() const override { return "permanent"; }

    bool isPowerDownSupported() const override { return false; }

    uint32_t getStartupWakeupSources() const override { return _startupWakeupSources; }

    uint32_t takeWakeupEvents() override { return 0U; }

    /** No source: the ECU state management refuses sleep requests for this reason alone (SYS-02). */
    uint32_t getSupportedWakeupSources() const override { return wakeup::NONE; }

    uint32_t getCyclicWakeupMs(uint32_t /* requestedMs */) const override { return 0U; }

    PowerDownResult powerDown(uint32_t /* nowMs */, WakeupSelection const& /* selection */) override
    {
        return PowerDownResult::NOT_SUPPORTED;
    }

private:
    uint32_t _startupWakeupSources;
};

} // namespace ecustate
