#pragma once

/* Run-down step SAFE_OUTPUTS of the ECU state management (SYS-174, safety review 2026-10-02, SYS69-S5, finding ECS-F02;
 * ecuState/IShutdownStep.h): the power outputs reach their safe state before the lifecycle shutdown and the power down,
 * and stay there.
 *
 * What it does, in start(), synchronously (the step never answers RUNNING):
 *   1. sets the run-down lock - the integration ORs it into the lock it hands every safe_off driver (safeStateLock() in
 *      Integration.cpp), so from now on every explicit enable is refused and no driver drives its output again;
 *   2. walks the safe outputs table of the project (the walk of the fault handlers and of S1, injected as \p walk:
 *      nxSafeOutputsSwitchOff() of hardFaultHandler/safeOutputs.h - PWM groups to their safe state, safe_off outputs
 *      inactive, gate checks and barrier included);
 *   3. hands the PWM inhibit of that walk to the debug halt guard exactly like an entry into S1
 *      (DebugHaltGuard::inhibitForSafeState()): the guard switches every power stage output off through its adapter,
 *      so the drivers' state follows the pins, and releases the PWM only at an explicit enable - which the lock refuses.
 * Nothing releases the lock: the run-down has no way back (State::RUNDOWN), it ends in the power down or in the restart
 * (lifecycle level 0 or the direct reset, SYS-173), i.e. in an MCU reset either way.
 *
 * The step runs while the application still runs (State::RUNDOWN, before the lifecycle shutdown): what the application
 * commands from now on is refused or has no effect, which is what the run-down wants. It runs last, after NvM and CAN.
 * The S1 latch of the safe supervisor is not touched (no safety event, no S1 entry count): the run-down is an orderly
 * operation, not a fault reaction.
 *
 * Context: the task of the power supply (TASK_BSP on this family port), the same task as the debug halt guard and its
 * hold, so the guard needs no lock. The lock word is a single byte written here and read by the drivers' tasks.
 * Family port only: the table walk and the guard are the s32k1xx ones; the class itself knows neither (host-testable,
 * libs/bsw/ecuState/test/host/EcuSafeOutputsStepHostTest.cpp). */

#include "DebugHaltGuard.h"

#include <ecuState/IShutdownStep.h>

#include <cstdint>

namespace integration
{

class SafeOutputsShutdownStep final : public ::ecustate::IShutdownStep
{
public:
    using Walk = void (*)();

    /**
     * \param walk  walk of the safe outputs table (nxSafeOutputsSwitchOff); nullptr is an integration error the step
     *              reports as FAILED
     * \param guard the debug halt guard that owns the PWM inhibit; nullptr on a board without power stage outputs
     */
    SafeOutputsShutdownStep(Walk const walk, DebugHaltGuard* const guard) : _walk(walk), _guard(guard) {}

    SafeOutputsShutdownStep(SafeOutputsShutdownStep const&)            = delete;
    SafeOutputsShutdownStep& operator=(SafeOutputsShutdownStep const&) = delete;

    // string literals only (SYS-16, tools/logcheck bounds of IShutdownStep names and reasons)
    char const* getName() const override { return "OUTPUTS"; }

    Result start(uint32_t const /* nowMs */) override
    {
        _locked = true; // first: no driver enables its output again between the walk and the guard
        if (_walk == nullptr)
        {
            return Result::FAILED;
        }
        _walk();
        if (_guard != nullptr)
        {
            _guard->inhibitForSafeState();
        }
        ++_walks;
        return Result::DONE;
    }

    Result poll(uint32_t const nowMs) override { return start(nowMs); } // never RUNNING; for completeness

    // a literal of at most 24 characters (SYS-16, tools/logcheck)
    char const* getFailureReason() const override { return (_walk == nullptr) ? "no safe outputs walk" : "-"; }

    // abort(): the default - the step never answers RUNNING, and the lock stays set

    /** true from the start of the step on (OR it into the lock of the safe_off drivers). */
    bool isLocking() const { return _locked; }

    /** Table walks done by this step (1 after a run-down). */
    uint32_t getWalks() const { return _walks; }

private:
    Walk _walk;
    DebugHaltGuard* _guard;
    uint32_t _walks = 0U;
    bool volatile _locked = false;
};

} // namespace integration
