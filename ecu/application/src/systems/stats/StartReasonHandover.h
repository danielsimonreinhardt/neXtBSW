// NVM-05: hand-over of the start reasons of the role power_supply from the ECU state management (EcuStateSystem,
// task of the power supply) to the ECU statistics (NvM context). The ECU state management knows the share of the
// supply only when the run level is reached; the MCU share is read by the statistics directly
// (ecustate::mcuStartReasons(), valid from before main). One word, only load and store (no libatomic, PLAT-28).

#pragma once

#include "EcuStatistics.h"

#include <ecuState/EcuState.h>

#include <atomic>
#include <cstdint>

namespace ecustats
{
namespace handover
{
/** 0: not reported yet; SUPPLY_NOT_RESTARTED: the supply did not start for this start (NVM-34). */
inline std::atomic<uint32_t> supplyStartReasons{0U};

/**
 * Called once when the run level is reached with IPowerSupply::getStartupWakeupSources() and
 * IPowerSupply::restartedForThisStart(); a supply that reports nothing counts as UNKNOWN. NVM-34: after an MCU-only
 * start (the supply did not restart, e.g. MCU software reset with the reset line of the SBC not connected) the supply
 * row of the statistics counts nothing - its reset source is the one of an earlier start.
 */
inline void publishSupplyStartReasons(uint32_t const reasons, bool const restarted = true)
{
    if (!restarted)
    {
        supplyStartReasons.store(SUPPLY_NOT_RESTARTED);
        return;
    }
    supplyStartReasons.store((reasons != 0U) ? reasons : ::ecustate::wakeup::UNKNOWN);
}

inline uint32_t supplyStartReasonsOrZero() { return supplyStartReasons.load(); }
} // namespace handover
} // namespace ecustats
