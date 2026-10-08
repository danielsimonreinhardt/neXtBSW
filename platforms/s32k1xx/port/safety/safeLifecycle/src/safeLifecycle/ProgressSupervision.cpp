// SYS-95: progress checks of the safety cycle on the S32K1 - ROM-check passes, deadman counter, system time (see the
// header).

#include "safeLifecycle/ProgressSupervision.h"

#include "safeLifecycle/SafetyManager.h"

// NX_CLOCK_CORE_HZ, NX_CLOCK_LPIT_HZ: the clock plan of the project (tools/bswgen)
#include <bsp/clock/boardClock.h>
#include <bsp/timer/SystemTimer.h>
#include <safeMemory/RomCheck.h>
#include <safeSupervisionCheck/LpitDeadmanCheck.h>
#include <safeSupervisionCheck/LpitDeadmanProgress.h>
#include <safeSupervisionCheck/LptmrCycleTimer.h>
#include <safeSupervisor/SafeSupervisor.h>
#include <safeUtils/SafetyLogger.h>
#include <safeWatchdog/ProgressChecks.h>

#include <cstdint>

// State of the checks - zero initialized, so in the MPU protected .bss of this archive (like safetyCyclePeriodCheck)
::safety::ProgressDeadline safetyRomCheckProgress;
::safety::TimeBaseCheck safetySystemTimeCheck;

namespace safety
{
namespace progress
{
namespace
{
using ::util::logger::Logger;
using ::util::logger::SAFETY;

/* DFA-6 (a): the system time over the window of the period check against the LPTMR on LPO1K. The LPO range of the
 * derivative (mcudb clock.lpo, derived by tools/nxconfig as for the period check) bounds the reference; the slack is
 * one period plus the lateness of a cycle: the time a debugger exit costs the system time once (PLAT-67 restarts the
 * DWT and loses the time it stood still, at most the gap between two reads - every safety cycle reads it). A stopped
 * system time is reported at the end of the first window (demo: 7 cycles, 70 ms). */
constexpr TimeBaseLimits SYSTEM_TIME_LIMITS{
    supervision::CYCLE_TIMER_NOMINAL_HZ,
    NX_MODULE_SAFELIFECYCLE_DERIVED_LPO_MIN_PER_MILLE,
    NX_MODULE_SAFELIFECYCLE_DERIVED_LPO_MAX_PER_MILLE,
    SafetyManager::CYCLE_PERIOD_WINDOW,
    supervision::CYCLE_TIMER_MASK,
    SafetyManager::CYCLE_PERIOD_MS + SafetyManager::CYCLE_LATENESS_MS};
static_assert(SYSTEM_TIME_LIMITS.windowCycles >= 1U, "system time check: a window of at least one cycle");

/* DFA-1: re-check periods without a completed pass before the safety task reports (see the header). */
constexpr uint64_t ROM_CHECK_DEADLINE_PERIODS = 3U;

/** Deadline of the ROM-check progress in safety cycles; 0 (no check) for period 0. */
uint32_t romCheckDeadlineCycles(uint32_t const periodMs)
{
    uint64_t const deadlineMs = ROM_CHECK_DEADLINE_PERIODS * periodMs;
    uint64_t const cycles     = (deadlineMs + SafetyManager::CYCLE_PERIOD_MS - 1U) / SafetyManager::CYCLE_PERIOD_MS;
    return (cycles > 0xFFFFFFFFULL) ? 0xFFFFFFFFU : static_cast<uint32_t>(cycles);
}

void checkRomCheckProgress()
{
#ifdef NX_ROM_CHECK_IMAGE
    RomCheck::Result const result = RomCheck::result();
    if ((result != RomCheck::Result::ROMCHECK_REQUESTED) && (result != RomCheck::Result::ROMCHECK_OK))
    {
        return; // RomCheck reported itself (S1 held); nothing more to watch
    }
    uint32_t const periodMs = RomCheck::periodMs();
    if (::safetyRomCheckProgress.sample(RomCheck::statistics().passes, romCheckDeadlineCycles(periodMs)))
    {
        Logger::error(
            SAFETY,
            "ROM check: no pass for %u safety cycles (idle context starved?), period %u ms",
            static_cast<unsigned int>(::safetyRomCheckProgress.cyclesSinceProgress()),
            static_cast<unsigned int>(periodMs));
        // SM-14 is lost at run time: the reaction of every other ROM-check event (safe state S1 held, SYS-23)
        SafeSupervisor::getInstance().romCheckMonitor.trigger(ROM_CHECK_CONTEXT_NOT_CALLED);
    }
#endif
}

void checkDeadmanProgress()
{
#if defined(NX_CLOCK_LPIT_HZ) && defined(NX_CLOCK_CORE_HZ)
    auto const* const deadman = supervision::deadmanExpectedConfiguration();
    if (deadman == nullptr)
    {
        return; // no deadman in this project
    }
    // DFA-2: two LPIT ticks at the core clock of the project, a few dozen polls on the eval board (80 MHz / 8 MHz: 22)
    constexpr uint32_t POLLS = supervision::lpitProgressPolls(NX_CLOCK_CORE_HZ, NX_CLOCK_LPIT_HZ);
    static_assert(POLLS >= 4U, "deadman progress: budget of at least two LPIT ticks");
    static_assert(
        POLLS <= 20000U,
        "deadman progress: the LPIT clock is so slow against the core clock that two of its ticks cost more than "
        "20000 polls in every safety cycle - choose a faster LPIT clock (project peripheral_clocks.LPIT)");
    if (supervision::checkLpitDeadmanProgress(*deadman, POLLS) == supervision::LpitCounterProgress::STOPPED)
    {
        SafeSupervisor::getInstance().deadmanConfigMonitor.trigger(supervision::DEADMAN_FIELD_COUNTER_STOPPED);
    }
#endif
}

void checkSystemTime()
{
    if (supervision::checkCycleTimer() != 0U)
    {
        ::safetySystemTimeCheck.restart(); // the period check reports the timer (kind 3); start again once it is back
        return;
    }
    // Read one right after the other; the order only shifts both by the same few cycles
    uint32_t const nowMs   = getSystemTimeMs32Bit();
    uint32_t const ticks   = supervision::readCycleTimer();
    uint32_t const context = ::safetySystemTimeCheck.sample(nowMs, ticks, SYSTEM_TIME_LIMITS);
    if (context != 0U)
    {
        SafeSupervisor::getInstance().safetyCyclePeriodMonitor.trigger(context);
    }
}
} // namespace

void cyclic()
{
    checkDeadmanProgress();
    checkSystemTime();
    checkRomCheckProgress();
}

} // namespace progress
} // namespace safety
