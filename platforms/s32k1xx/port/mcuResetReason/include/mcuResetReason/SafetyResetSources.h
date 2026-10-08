#pragma once

#include <ecuState/EcuState.h>

#include <cstdint>

/**
 * SYS-148 (follow-up of SYS-103/SYS-104): how the s32k1xx port tells the reset-loop limit (SYS-43,
 * safeSupervisor/ResetLoopLimit.h) what reset this start followed. Pure functions without register access, used by
 * resetLoopStart() in port/main/src/main.cpp and by the host test rcm-start-line.
 *
 * Why: the MCU watchdog runs with WDOG_CS[INT] = 1, and its pre-reset interrupt goes straight into the port's fault
 * handler (os/isr/isr_wdg.cpp -> customHardFaultHandler), which writes the same hard fault dump as a real fault
 * exception. Before SYS-148 every dump counted as a fault exception, and the fault exception has priority over the
 * watchdog in resetloop::onStart(): board 2026-09-30 (DOC-19 D7) - after `safety wdg` (dump exception 38, RCM_SRS
 * 0x420) and after `safety mpu` (dump exception 3, RCM_SRS 0x400) the start said "last cause 0x2" both times. The
 * dump's exception number tells them apart: the watchdog's vector is a watchdog reset (program-flow supervision),
 * every other one a fault exception.
 */
namespace rcm
{
/** What the fault-handler record of this port says about the reset before this start. */
enum class HandlerRecord : uint8_t
{
    NONE,            // no dump, no core lockup
    FAULT_EXCEPTION, // a fault exception (hard fault, MPU, bus, usage), a dump that is not intact, or a core lockup
    WATCHDOG         // a valid dump written from the watchdog's pre-reset interrupt: a real watchdog time-out
};

/**
 * \param dumpPresent        the hard fault dump has its magic (valid or corrupt)
 * \param dumpException      exception number of a valid dump, 0 otherwise (hardfault::getDumpException)
 * \param lockup             RCM_SRS[LOCKUP] of the start (a fault while the fault handler ran)
 * \param watchdogException  exception number of the watchdog's interrupt on this derivative (IRQ number + 16)
 */
constexpr HandlerRecord handlerRecordOf(
    bool const dumpPresent, uint32_t const dumpException, bool const lockup, uint32_t const watchdogException)
{
    if (lockup)
    {
        return HandlerRecord::FAULT_EXCEPTION;
    }
    if (!dumpPresent)
    {
        return HandlerRecord::NONE;
    }
    // a corrupt dump has no exception number (0): it keeps counting as a fault exception, as before SYS-148
    return ((dumpException != 0U) && (dumpException == watchdogException)) ? HandlerRecord::WATCHDOG
                                                                           : HandlerRecord::FAULT_EXCEPTION;
}

/** The four inputs of resetloop::Start (same meaning), without depending on the safety layer. */
struct SafetyResetSources
{
    bool powerOn;
    bool watchdog;
    bool faultException;
    bool otherReset;
};

/**
 * From the start reasons (ecustate::wakeup bits of startReasonsOf()) and the fault-handler record.
 * A dump from the watchdog vector is itself the proof of a watchdog time-out: it counts as watchdog whatever reset
 * bit stands next to WDOG (the handler's own SYSRESETREQ is already folded by startReasonsOf(), SYS-103) - like a
 * fault dump, which counted before regardless of the reset bits. Without a dump the rules of SYS-43/SYS-103 stay:
 * a watchdog reason counts only without another reset reason next to it (J-Link reset: RESET | WATCHDOG).
 */
constexpr SafetyResetSources safetyResetSourcesOf(uint32_t const reasons, HandlerRecord const handler)
{
    namespace wakeup = ::ecustate::wakeup;
    bool const watchdogDump = (handler == HandlerRecord::WATCHDOG);
    return SafetyResetSources{
        (reasons & (wakeup::POWER | wakeup::SUPPLY)) != 0U,
        watchdogDump || ((reasons & wakeup::WATCHDOG) != 0U),
        handler == HandlerRecord::FAULT_EXCEPTION,
        (!watchdogDump) && ((reasons & wakeup::RESET) != 0U)};
}
} // namespace rcm
