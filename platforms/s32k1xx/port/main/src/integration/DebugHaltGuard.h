#pragma once

/* IO-32: power stage after a debug halt - no restart with the old duty.
 *
 * Board finding 2026-09-18 (CHANGELOG "IO-15 Fortsetzung"): a debugger halt stops the FTM (CONF.BDMMODE 01 forces
 * the PWM outputs inactive), but when the core runs on, the PWM continues with the old duty and the gate driver
 * enable is a GPIO that stayed on - the bridge switches again although the application was stopped in between,
 * until the MCU watchdog resets. Decision of the user 2026-09-18: after a debug halt the PWM and the gate driver
 * enable stay off until the next explicit enable. The safer solution is hardware (enable through an FTM output or
 * the FTM fault path, libs/bsw/gdTmc6200/doc/gdTmc6200.md); this is the software part.
 *
 * Detection (board findings 2026-09-18, doc/proposals/IO-32-debug-halt-detection.md, user decision 2026-09-19):
 * two sources, both delivered to cyclic() as event bits.
 * - **Primary, HALT_TIMER:** a deadman timer that keeps counting while the core is halted (S32K1: an LPIT channel
 *   with MCR[DBG_EN] = 1), retriggered by the guard every poll period. Its interrupt pends in the NVIC during the
 *   halt and is taken before any task code after the resume; the handler switches the power outputs off there and
 *   leaves this bit for the next poll. Independent of the debugger, and it also catches a software stall longer
 *   than the timeout (fail-safe, log text "halt or stall").
 * - **Secondary, the DFSR bits of the core:** free, catches halts shorter than the deadman timeout, but only with
 *   a probe that leaves DFSR set - the J-Link Commander clears it at `g` (board 2026-09-18 evening), which is why
 *   it is no longer the mechanism.
 *
 * Hardware independent (host test test/host/DebugHaltGuardHostTest.cpp). The caller passes the sticky debug events
 * of the core since the previous poll (Cortex-M DFSR, DebugHaltGuardSystem.h). On an event the guard
 * - forces the PWM outputs of the power stage inactive (IPwmInhibit), independent of later duty requests, and
 * - switches every power stage output off (IPowerStageOutput, e.g. the gate driver enable),
 * and keeps the PWM inhibited until one of these outputs was enabled explicitly again (the application or a test
 * build console, never the guard), or release() is called. A project with PWM but without such an output stays
 * inhibited until the next reset.
 *
 * Software can only react after the core runs again: the PWM is back for up to one poll period of the caller. Only
 * the hardware solution avoids that window.
 *
 * Not thread safe: all calls from the task context of the power stage outputs. */

#include <cstdint>

namespace integration
{

/**
 * A power stage output the guard switches off after a debug halt, e.g. the enable of a gate driver. The output
 * itself decides what an explicit enable is.
 *
 * SYS-36: the same adapter carries the safe state lock of its driver. There is one adapter per safe_off output of
 * the project (static_assert against NX_SAFE_OFF_OUTPUT_COUNT in Integration.cpp, derived from the module
 * manifests), so every such output is covered by both mechanisms; the pure virtual holdForSafeState() makes a new
 * adapter decide how its driver is locked.
 */
class IPowerStageOutput
{
public:
    /** Off now; nothing switches it on again except an explicit enable. After a debug halt and (SYS-35) on entry
     * into the safe state S1. */
    virtual void switchOffAfterDebugHalt() = 0;
    /** true once the output was enabled explicitly after the last switchOffAfterDebugHalt(). */
    virtual bool enabledSinceDebugHaltSwitchOff() const = 0;
    /**
     * SYS-36: called in every poll while the safe state S1 is latched (SafeStateHold.h). The driver evaluates its
     * lock now: output off at once, switch-on refused. \return true if the driver is locked; false means no lock
     * reaches the driver (not attached) - an integration error that the hold reports.
     */
    virtual bool holdForSafeState() = 0;

protected:
    ~IPowerStageOutput() = default;
};

/** PWM outputs of a power stage forced to their inactive level, independent of later duty changes. */
class IPwmInhibit
{
public:
    virtual void inhibit() = 0;
    virtual void release() = 0;

protected:
    ~IPwmInhibit() = default;
};

class DebugHaltGuard
{
public:
    /**
     * Event bit of the deadman timer, next to the debug event bits of the core (Cortex-M DFSR uses bits 0..4).
     * Outside the DFSR bits so that the log line can name the source that saw the halt.
     */
    static constexpr uint32_t HALT_TIMER = 1UL << 8;

    enum class Event : uint8_t
    {
        NONE,
        HALT,     // halt detected: PWM inhibited, outputs switched off
        RELEASED, // PWM released after an explicit enable of an output (or release())
    };

    /**
     * \param outputs power stage outputs (\p outputCount entries, none on a board without gate driver)
     * \param pwm PWM of the power stage, nullptr if the project has none
     */
    DebugHaltGuard(IPowerStageOutput* const* outputs, uint8_t outputCount, IPwmInhibit* pwm)
    : _outputs(outputs), _pwm(pwm), _outputCount(outputCount)
    {}

    /**
     * Every poll period, in the task context of the outputs.
     * \param debugEvents sticky debug events of the core since the previous call (0: none), already cleared
     */
    Event cyclic(uint32_t const debugEvents)
    {
        if (debugEvents != 0U)
        {
            // PWM first (stops the switching at once), then the enables. A halt while inhibited switches off again
            // and restarts the wait for an explicit enable.
            if (_pwm != nullptr)
            {
                _pwm->inhibit();
            }
            for (uint8_t i = 0U; i < _outputCount; ++i)
            {
                _outputs[i]->switchOffAfterDebugHalt();
            }
            _inhibited        = true;
            _lastDebugEvents  = debugEvents;
            ++_halts;
            return Event::HALT;
        }
        if (_inhibited)
        {
            for (uint8_t i = 0U; i < _outputCount; ++i)
            {
                if (_outputs[i]->enabledSinceDebugHaltSwitchOff())
                {
                    release();
                    return Event::RELEASED;
                }
            }
        }
        return Event::NONE;
    }

    /**
     * SYS-35: the safe state S1 was entered (SafeStateHold.h). Its table walk has forced the PWM inactive behind
     * the back of everybody (FTM software output control); from here on the guard owns that inhibit exactly as
     * after a halt: PWM inhibited, every output switched off and the enable counts taken, released at the next
     * explicit enable of an output. The drivers refuse every enable while S1 is latched, so the release can only
     * come after SafeState::leaveSafeState(). Board finding 2026-09-21 before SYS-35: nobody owned the inhibit of
     * the walk, `gd enable` reported enabled and the phases floated until a reboot. Counts no halt.
     */
    void inhibitForSafeState()
    {
        if (_pwm != nullptr)
        {
            _pwm->inhibit();
        }
        for (uint8_t i = 0U; i < _outputCount; ++i)
        {
            _outputs[i]->switchOffAfterDebugHalt();
        }
        _inhibited = true;
    }

    /** Explicit release of the PWM (a power stage without an output that is enabled explicitly). */
    void release()
    {
        if (_inhibited && (_pwm != nullptr))
        {
            _pwm->release();
        }
        _inhibited = false;
    }

    bool isInhibited() const { return _inhibited; }

    uint32_t getHalts() const { return _halts; }

    uint32_t getLastDebugEvents() const { return _lastDebugEvents; }

private:
    IPowerStageOutput* const* _outputs;
    IPwmInhibit* _pwm;
    uint32_t _halts           = 0U;
    uint32_t _lastDebugEvents = 0U;
    uint8_t _outputCount;
    bool _inhibited = false;
};

} // namespace integration
