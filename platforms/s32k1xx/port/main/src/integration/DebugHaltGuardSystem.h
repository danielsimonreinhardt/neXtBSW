#pragma once

/* IO-32: debug halt guard of the power stage on the S32K1 family (logic: DebugHaltGuard.h).
 *
 * Detection, two sources (user decision 2026-09-19, doc/proposals/IO-32-debug-halt-detection.md):
 *
 * 1. **Deadman timer that keeps counting while the core is halted** (primary). The LPIT continues in debug mode
 *    when MCR[DBG_EN] is set ([RM] Rev. 13 Table 48-3 p. 1529, MCR field description p. 1532-1533) - unlike the
 *    DWT cycle counter and SysTick ([v7M] C1.8, B3.3), the FTM ([RM] 47.2.2 p. 1357) and the LPTMR time counter
 *    ([RM] 49.5.4 p. 1574), which all stop, so a halt cannot be seen as lost time against them. Channel 1 runs as
 *    a 32-bit periodic down counter with TIMEOUT_MS; the guard retriggers it every poll period, so while the
 *    software runs it never expires. During a halt longer than the timeout it expires and its interrupt pends in
 *    the NVIC while the core is halted; the core takes it as soon as it runs again, **before any task code**. The
 *    handler switches the power outputs off through the safe outputs table of the project (the same table the
 *    fault handlers use) and leaves a flag for the next poll of the guard. Latency after `g`: exception entry plus
 *    the table walk, far below one PWM period - the board finding of 2026-09-18 was a burst of > 5 ms.
 *    The same mechanism catches a **software stall** longer than the timeout (interrupt lock, task starvation):
 *    the reaction is fail-safe (power stage off until the next enable, log line "halt or stall"), not a reset.
 * 2. **The DFSR of the Cortex-M core** (secondary, 0xE000ED30, ARMv6-M and ARMv7-M/ARMv8-M alike): a sticky bit per
 *    debug event - HALTED (halt or step request of the debugger), BKPT, DWTTRAP, VCATCH, EXTERNAL - until software
 *    writes it back. A reset does not clear it, so the events of the debugger's own connect and reset are discarded
 *    when the guard starts. It costs nothing and catches halts shorter than the deadman timeout, but it cannot be
 *    the mechanism: board 2026-09-18 evening, reproduced twice, DFSR is 0x01 before `g` and 0x00 300 ms after it -
 *    the J-Link Commander clears the debug events when it resumes the core.
 *
 * The watchdog case (board 2026-09-18: the watchdog interrupt runs first after the resume) stays covered by the
 * safe outputs table the fault handlers work through first (hardFaultHandler/safeOutputs.h, libs/bsw/gdTmc6200/doc,
 * "Debug halt").
 *
 * PWM inhibit: software output control of the FTM of the project's PWM group (SWOCTRL, CHnOC 1 and CHnOCV 0 for all
 * channels of the group: both channels of every pair forced to their inactive level, [RM] 47.5.15 Table 47-13;
 * software output control acts before the output mask and the polarity, Figure 47-86). With SYNCONF.SWOC 0 the
 * register takes effect at the next FTM input clock. ComplementaryPwm never writes SWOCTRL after init() and does
 * not use it, so duty requests of the application change nothing while the PWM is inhibited, and releasing it does
 * not disturb the output mask bookkeeping of the driver.
 *
 * Poll period POLL_MS in the task of the gate driver systems (TASK_BSP): it retriggers the deadman and reads the
 * DFSR. A halt seen only by the DFSR still lets the PWM run for up to one poll period after the resume; a halt
 * seen by the deadman does not, because its interrupt is taken before the task.
 *
 * Configuration levels: **no new configuration value**. The timeout, the poll period, the LPIT channel and the
 * interrupt priority are constants of the mechanism in this family port (channel 0 is the system timer of the
 * ARMv6-M port, channel 1 the deadman); nothing here is a board fact. Moving the timeout to the project level
 * (project.yaml) is only needed once a project has interrupt locks longer than TIMEOUT_MS. */

#include "DebugHaltGuard.h"
#include "FtmPwmInhibit.h"
#include "HwThresholdWatch.h" // SYS-128: the fast check of the hardware threshold path (only with a threshold)
#include "LpitHaltDeadman.h"
#include "SafeOffCommandHold.h" // SYS-151: the commanded level of every safe_off output, outside S1 as well
#include "SafeStateHold.h"
#include "hardFaultHandler/safeOutputs.h"
#include "integration/SafeOffOutputs.h" // SYS-151: output number of a safe_off output for the log line
#include "mcu/mcu.h"

#include <async/Async.h>
#include <bsp/io/outputPwm/ftmPwmConfiguration.h>
#include <lifecycle/SingleContextLifecycleComponent.h>
#include <util/logger/Logger.h>

#include <cstdint>

DECLARE_LOGGER_COMPONENT(BSP)

#if !defined(NX_CORE_ARCHITECTURE_ARMV6M) && !defined(NX_CORE_ARCHITECTURE_ARMV7M) \
    && !defined(NX_CORE_ARCHITECTURE_ARMV8M)
#error "DebugHaltGuardSystem.h reads the Cortex-M DFSR: needs NX_CORE_ARCHITECTURE_ARMV6M, ARMV7M or ARMV8M"
#endif

namespace integration
{

/** Sticky debug events of a Cortex-M core (DFSR, ARMv6-M/ARMv7-M architecture reference manuals C1.6.1). */
class CortexMDebugEvents
{
public:
    static constexpr uintptr_t DFSR_ADDRESS = 0xE000ED30U;
    // HALTED 0, BKPT 1, DWTTRAP 2, VCATCH 3, EXTERNAL 4; write one to clear
    static constexpr uint32_t DFSR_EVENTS = 0x1FU;

    /** Events since the previous call; clears exactly the events returned. */
    static uint32_t take()
    {
        volatile uint32_t* const dfsr = reinterpret_cast<volatile uint32_t*>(DFSR_ADDRESS);
        uint32_t const events         = *dfsr & DFSR_EVENTS;
        if (events != 0U)
        {
            *dfsr = events;
        }
        return events;
    }
};

/* The strong LPIT0_Ch1_IRQHandler and safety::supervision::deadmanExpectedConfiguration() are not defined here:
 * SYS-133 moved both into Integration.cpp (a definition in a header is a trap for the second #include,
 * BEF-003/BEF-004). They must not become inline - see there. */

// IO-32/SYS-123: FtmPwmInhibit (the PWM inhibit of the guard) lives in FtmPwmInhibit.h

/* SYS-36: the latch of the safe state S1 and the table walk, as the drivers' locks and SafeStateHold take them. */
inline bool safeStateLatched() { return nxSafeStateIsLatched() != 0; }

inline void safeOutputsSwitchOff() { nxSafeOutputsSwitchOff(); }

// SYS-35: entries into S1, so that the hold hands every entry's PWM inhibit to the guard
inline uint32_t safeStateEntryCount() { return nxSafeStateEntryCount(); }

/**
 * Lifecycle component polling the core's debug events for the guard, and (SYS-36) the poll of the safe state hold:
 * the one component of the power stage that runs every POLL_MS in the task of its drivers.
 */
class DebugHaltGuardSystem
: public ::lifecycle::SingleContextLifecycleComponent
, private ::async::RunnableType
{
public:
    static constexpr uint32_t POLL_MS = 1U;

    /** \param commandHold SYS-151: hold of the commanded level of the safe_off outputs (nullptr: none) */
    DebugHaltGuardSystem(
        ::async::ContextType const context,
        DebugHaltGuard& guard,
        SafeStateHold& hold,
        SafeOffCommandHold* const commandHold = nullptr)
    : ::lifecycle::SingleContextLifecycleComponent(context)
    , _context(context)
    , _timeout()
    , _guard(guard)
    , _hold(hold)
    , _commandHold(commandHold)
    , _reportedUnlocked(0U)
    {}

    DebugHaltGuardSystem(DebugHaltGuardSystem const&)            = delete;
    DebugHaltGuardSystem& operator=(DebugHaltGuardSystem const&) = delete;

    void init() override
    {
        // Events of the debugger's connect, reset and flash before this start are no halt of this run.
        uint32_t const before = CortexMDebugEvents::take();
        if (before != 0U)
        {
            ::util::logger::Logger::info(
                ::util::logger::BSP,
                "debug halt guard: debug events 0x%02x before the start discarded",
                static_cast<unsigned>(before));
        }
        // Registers only; the deadman is armed in run(), because the init of the remaining components may well
        // take longer than its timeout.
        LpitHaltDeadman::init();
        if (debuggerAttached())
        {
            // User decision 2026-09-19 (question 7): an attached probe is not refused - that would forbid every
            // motor test with the rescue path connected. Only a line in the log.
            ::util::logger::Logger::info(
                ::util::logger::BSP,
                "debug halt guard: debugger attached (DHCSR C_DEBUGEN) - a halt switches the power stage off");
        }
        transitionDone();
    }

    void run() override
    {
        /* The deadman is not armed here: it is armed at the end of the first poll. Between this run() and the
         * first poll the remaining lifecycle levels still run in this task, which may well take longer than the
         * timeout - arming here would report a halt at every start. Nothing is unprotected in that window: the
         * power stage is off until an explicit enable anyway. */
        ::async::scheduleAtFixedRate(_context, *this, _timeout, POLL_MS, ::async::TimeUnit::MILLISECONDS);
        transitionDone();
    }

    void shutdown() override
    {
        LpitHaltDeadman::stop();
        _timeout.cancel();
        transitionDone();
    }

private:
    /**
     * DHCSR bit 0 C_DEBUGEN: a debugger has enabled halting debug ([v7M] C1.6.2). Only informative - it is set
     * while the probe is attached, before, during and after a halt, so it is no halt detection.
     *
     * ARMv6-M (Cortex-M0+): whether software may read DHCSR at all is IMPLEMENTATION DEFINED in the architecture
     * and not documented for this core, so it is not read there.
     */
    static bool debuggerAttached()
    {
#if defined(NX_CORE_ARCHITECTURE_ARMV7M) || defined(NX_CORE_ARCHITECTURE_ARMV8M)
        return (*reinterpret_cast<volatile uint32_t const*>(0xE000EDF0U) & 0x1U) != 0U;
#else
        return false;
#endif
    }

    void execute() override
    {
        holdSafeState();
        holdCommandedLevels();
        uint32_t events = CortexMDebugEvents::take();
        if (LpitHaltDeadman::takeExpired())
        {
            events |= DebugHaltGuard::HALT_TIMER;
        }
        DebugHaltGuard::Event const event = _guard.cyclic(events);
        if (event == DebugHaltGuard::Event::HALT)
        {
            uint32_t const sources = _guard.getLastDebugEvents();
            if ((sources & DebugHaltGuard::HALT_TIMER) != 0U)
            {
                // The deadman cannot tell a debug halt from a software standstill longer than its timeout, and
                // reacts the same way to both (user decision 2026-09-19, question 3).
                ::util::logger::Logger::warn(
                    ::util::logger::BSP,
                    "debug halt or stall (timer, #%u): PWM inhibited and DRV_EN off until the next enable",
                    static_cast<unsigned>(_guard.getHalts()));
            }
            else
            {
                ::util::logger::Logger::warn(
                    ::util::logger::BSP,
                    "debug halt (DFSR 0x%02x, #%u): PWM inhibited and DRV_EN off until the next enable",
                    static_cast<unsigned>(sources),
                    static_cast<unsigned>(_guard.getHalts()));
            }
        }
        else if (event == DebugHaltGuard::Event::RELEASED)
        {
            // after a debug halt or (SYS-35) after the safe state was left
#ifdef NX_PWM_GROUP_LOCK
            if (::bios::ftmPwm::groupLocked())
            {
                // SYS-123: FtmPwmInhibit::release() kept the degraded group forced
                ::util::logger::Logger::info(
                    ::util::logger::BSP, "power stage: enable seen, PWM stays inhibited (group degraded)");
            }
            else
#endif
            {
                ::util::logger::Logger::info(::util::logger::BSP, "power stage: PWM released after enable");
            }
        }
        else
        {
            // nothing new
        }
#if NX_HW_THRESHOLD_COUNT > 0
        // SYS-128: after the guard, so that a release of its inhibit in this poll is overwritten at once
        checkHwThreshold();
#endif
        // Last, after the log line: the timeout starts again where the software really got to, and the deadman is
        // re-armed after an expiry. A poll that took longer than TIMEOUT_MS itself has already been reported.
        LpitHaltDeadman::retrigger();
    }

    /** SYS-36: holds S1 while it is latched (SafeStateHold.h); first in every poll, before the guard. */
    void holdSafeState()
    {
        SafeStateHold::Event const event = _hold.cyclic();
        if (event == SafeStateHold::Event::ENTERED)
        {
            // An expected consequence of S1, no fault of a driver: one line, the drivers report their lock.
            ::util::logger::Logger::warn(
                ::util::logger::BSP,
                "safe state: %u power stage output(s) locked until the safe state is left",
                static_cast<unsigned>(_hold.getOutputCount()));
        }
        else if (event == SafeStateHold::Event::LEFT)
        {
            // SYS-35: the locks are gone, nothing is switched on - every output waits for its explicit enable
            ::util::logger::Logger::info(
                ::util::logger::BSP, "safe state left: power stage off until the next explicit enable");
        }
        else
        {
            // NONE or HELD: nothing new
        }
        uint8_t const unlocked = _hold.isHeld() ? _hold.getUnlockedOutputs() : 0U;
        if (unlocked != _reportedUnlocked)
        {
            _reportedUnlocked = unlocked;
            if (unlocked != 0U)
            {
                // No lock reaches the driver of such an output; the table walk of every poll still keeps it low.
                ::util::logger::Logger::error(
                    ::util::logger::BSP,
                    "safe state: %u output(s) without lock, only the table keeps them off",
                    static_cast<unsigned>(unlocked));
            }
        }
    }

    /**
     * SYS-151: right after the hold of S1, in every poll: a safe_off output that reads active although its driver
     * does not command it is driven inactive (SafeOffCommandHold.h). One log line per new take-back, at most one per
     * second.
     */
    void holdCommandedLevels()
    {
        if (_commandHold == nullptr)
        {
            return;
        }
        (void)_commandHold->cyclic();
        if (_commandHold->takeLogDue())
        {
            // the output by its number of the console group "out" (out all lists the names) - no %s: the log line
            // stays within its entry without a bound on a name (tools/logcheck)
            uint8_t const index = _commandHold->getLastIndex();
            ::util::logger::Logger::warn(
                ::util::logger::BSP,
                "safe_off output %u: active level not commanded by its driver, taken back (%u in total)",
                static_cast<unsigned>(::integration::safeoff::outputOf(index)),
                static_cast<unsigned>(_commandHold->getTakeBacks()));
        }
    }

#if NX_HW_THRESHOLD_COUNT > 0
    /** SYS-128: the hardware threshold path in every poll (HwThresholdWatch.h); one log line per new finding. */
    void checkHwThreshold()
    {
        uint32_t const fresh = _hwThreshold.cyclic();
        if (fresh != 0U)
        {
            ::util::logger::Logger::error(
                ::util::logger::BSP,
                "hardware threshold path: findings 0x%04x (all 0x%04x), PWM group forced inactive until reset",
                static_cast<unsigned>(fresh),
                static_cast<unsigned>(HwThresholdWatch::findings()));
        }
    }

    HwThresholdWatch _hwThreshold;
#endif
    ::async::ContextType const _context;
    ::async::TimeoutType _timeout;
    DebugHaltGuard& _guard;
    SafeStateHold& _hold;
    SafeOffCommandHold* _commandHold; // SYS-151
    uint8_t _reportedUnlocked;
};

} // namespace integration
