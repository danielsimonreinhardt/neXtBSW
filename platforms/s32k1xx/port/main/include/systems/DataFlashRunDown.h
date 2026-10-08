// NVM-23: the data flash of the project (libs/bsw/nvmDataFlash, dataflash::AreaService) in the run-down of the ECU
// state management, free of hardware (host test: src/systems/test/host/DataFlashSystemHostTest.cpp).
//
// Safety review NVM12-S2 "no erase in the run-down": an erase or program cut by the power down leaves the sector
// undefined ([RM13] CAUTION in "Flash program and erase"), and the power down follows the run-down within
// milliseconds. Therefore:
//  - DataFlashGate sits between the AreaService and the family driver. While it is locked, no flash command is
//    launched (startErase/startProgram answer NOT_AVAILABLE, the job ends after the command that runs), and it knows
//    whether a launched command has not ended yet (commandInFlight()).
//  - DataFlashRunDownStep takes the place of ShutdownStep::NVM: it locks the gate first, then runs the step of the
//    NvM (if the project has one) and answers RUNNING until that step has ended AND no data flash command is in
//    flight. The power down therefore never meets a running data flash command; a job that was cut keeps its
//    result (NOT_AVAILABLE) - its area holds what was done until then, like after a power loss (two areas and a
//    check sum are the caller's format, nvmDataFlash.md).
//  - The gate is unlocked again when the lifecycle runs the data flash system again (a run-down given up by a
//    wake-up restarts the lifecycle without an MCU reset).
// Safety review NVM12-S1 b (NVM-25) "one lock for NvM and readers": reads go through IDataFlash::read(), which the
// s32k1xx driver makes under the block lock (flexnvm/FlexNvmBlockLock.h); a reader in place takes a hold
// (dataflash::DirectRead) - the gate passes both through and hands out no address itself. In the run-down (gate
// locked) no new hold is granted: a hold would keep the EEPROM stores of the NvM step waiting.
// Safety review NVM23-S1 b (NVM-26) "no new job from STANDBY on": admitsJobs() is the admission of the AreaService
// (DataFlashSystem.cpp asks it with the state the ECU state management published, ecustate::rte::publishedState()).
// A job accepted in RUN keeps running in STANDBY (post-run) and is cut by the run-down step as above.
//
// Everything here is called from ONE task (the owner of the data flash system, the task of the power supply): the
// run-down step, AreaService::cyclic() and the console work (console::runInOwnerContext).

#pragma once

#include <dataflash/DataFlashAreas.h>
#include <dataflash/IDataFlash.h>
#include <ecuState/EcuState.h>
#include <ecuState/IShutdownStep.h>

#include <cstdint>

namespace systems
{
namespace dflash
{

/** Lockable pass-through of the family driver (see the file comment). */
class DataFlashGate final : public ::dataflash::IDataFlash
{
public:
    constexpr explicit DataFlashGate(::dataflash::IDataFlash& flash)
    : _flash(flash), _locked(false), _inFlight(false), _refused(0U)
    {}

    ::dataflash::Geometry geometry() const override { return _flash.geometry(); }

    ::dataflash::Status read(uint32_t const offset, uint8_t* const buffer, uint32_t const length) override
    {
        return _flash.read(offset, buffer, length);
    }

    ::dataflash::Status startErase(uint32_t const offset) override
    {
        if (_locked)
        {
            ++_refused;
            return ::dataflash::Status::NOT_AVAILABLE;
        }
        return launched(_flash.startErase(offset));
    }

    ::dataflash::Status startProgram(uint32_t const offset, uint8_t const* const data) override
    {
        if (_locked)
        {
            ++_refused;
            return ::dataflash::Status::NOT_AVAILABLE;
        }
        return launched(_flash.startProgram(offset, data));
    }

    ::dataflash::Status poll() override
    {
        ::dataflash::Status const s = _flash.poll();
        if (s != ::dataflash::Status::BUSY)
        {
            _inFlight = false; // ended (OK, FAILED) or given up (TIMEOUT)
        }
        return s;
    }

    /**
     * NVM-25: a hold for a read in place keeps the EEPROM stores of the NvM waiting - in the run-down (locked) no new
     * hold is granted, so the NvM step never waits for a reader.
     */
    bool holdForDirectRead() override { return !_locked && _flash.holdForDirectRead(); }

    void releaseDirectRead() override { _flash.releaseDirectRead(); }

    /** No further flash command (run-down); the command in flight runs to its end. */
    void lock() { _locked = true; }

    void unlock() { _locked = false; }

    bool locked() const { return _locked; }

    /** A command was launched and poll() has not reported its end yet. */
    bool commandInFlight() const { return _inFlight; }

    /** Launches refused because the gate was locked. */
    uint32_t refused() const { return _refused; }

private:
    ::dataflash::Status launched(::dataflash::Status const s)
    {
        if (s == ::dataflash::Status::OK)
        {
            _inFlight = true;
        }
        return s;
    }

    ::dataflash::IDataFlash& _flash;
    bool _locked;
    bool _inFlight;
    uint32_t _refused;
};

/**
 * NVM-26 (safety review NVM23-S1 b): a new data flash job is accepted only in STARTUP and RUN - from STANDBY on the ECU
 * is on its way to a power down, which must not meet an erase or program (ecustate::isOnWayToPowerDown()).
 */
inline bool admitsJobs(::ecustate::State const state) { return !::ecustate::isOnWayToPowerDown(state); }

/**
 * Advances the job of \p areas by up to \p maxSteps steps of AreaService::cyclic() in one cycle of the owner task,
 * never waiting: it stops as soon as a command is in flight (its end is polled in the next cycle) or the job has
 * ended. A write needs two steps per program unit (poll and read back, then read and launch the next one), so one
 * call per cycle with maxSteps >= 2 programs one unit per cycle instead of one per two cycles.
 * Returns the result of the job if it ended in this call, otherwise BUSY (job running) or OK (no job).
 */
inline ::dataflash::Status
advance(::dataflash::AreaService& areas, DataFlashGate const& gate, uint8_t const maxSteps)
{
    ::dataflash::Status s = ::dataflash::Status::OK;
    for (uint8_t i = 0U; i < maxSteps; ++i)
    {
        if (!areas.busy())
        {
            break;
        }
        s = areas.cyclic();
        if ((!areas.busy()) || gate.commandInFlight())
        {
            break;
        }
    }
    return s;
}

/** ShutdownStep::NVM of a project with data flash: the gate, then the NvM step (if any), see the file comment. */
class DataFlashRunDownStep final : public ::ecustate::IShutdownStep
{
public:
    constexpr explicit DataFlashRunDownStep(DataFlashGate& gate)
    : _gate(gate), _inner(nullptr), _innerDone(true), _innerResult(Result::DONE), _waits(0U), _aborts(0U)
    {}

    /** The step of the NvM of the application (integration::nvmShutdownStep()), nullptr if there is none. */
    void setInner(::ecustate::IShutdownStep* const inner) { _inner = inner; }

    // string literals only (SYS-16, tools/logcheck bounds of IShutdownStep names and reasons); the inner step is the
    // NvM of the application (ShutdownStep::NVM)
    char const* getName() const override { return (_inner != nullptr) ? "NvM" : "DFLASH"; }

    Result start(uint32_t const nowMs) override
    {
        _gate.lock(); // first: an EEPROM store of the NvM step does not wait behind a further data flash command
        _innerDone   = true;
        _innerResult = Result::DONE;
        if (_inner != nullptr)
        {
            note(_inner->start(nowMs));
        }
        return combined();
    }

    Result poll(uint32_t const nowMs) override
    {
        if (!_innerDone)
        {
            note(_inner->poll(nowMs));
        }
        return combined();
    }

    char const* getFailureReason() const override
    {
        return ((_inner != nullptr) && (_innerResult == Result::FAILED)) ? "NvM step failed" : "-";
    }

    /**
     * SYS-174 (ECS-F04): cut off by the time limit. The gate stays locked (no further data flash command is launched;
     * the one in flight runs to its end, nothing can stop it), and the NvM step of the application is told so. The
     * ECU state management never continues past this step to the power down (ecustate::isContinuableStep()); the
     * restart follows.
     */
    void abort(uint32_t const nowMs) override
    {
        _gate.lock();
        if ((_inner != nullptr) && !_innerDone)
        {
            _inner->abort(nowMs);
            _innerDone = true;
        }
        ++_aborts;
    }

    /** Time-outs of this step told through abort(). */
    uint32_t aborts() const { return _aborts; }

    /** Polls answered RUNNING only because a data flash command was in flight (board measurement). */
    uint32_t waits() const { return _waits; }

private:
    void note(Result const r)
    {
        if (r != Result::RUNNING)
        {
            _innerDone   = true;
            _innerResult = r;
        }
        else
        {
            _innerDone = false;
        }
    }

    Result combined()
    {
        if (!_innerDone)
        {
            return Result::RUNNING;
        }
        if (_gate.commandInFlight())
        {
            ++_waits;
            return Result::RUNNING; // also after a failed NvM step: the power down must not meet the command
        }
        return _innerResult;
    }

    DataFlashGate& _gate;
    ::ecustate::IShutdownStep* _inner;
    bool _innerDone;
    Result _innerResult;
    uint32_t _waits;
    uint32_t _aborts;
};

/** Short text of a data flash status for the console (string literal). */
inline char const* statusText(::dataflash::Status const s)
{
    switch (s)
    {
        case ::dataflash::Status::OK: return "ok";
        case ::dataflash::Status::BUSY: return "busy";
        case ::dataflash::Status::RANGE: return "range";
        case ::dataflash::Status::ALIGNMENT: return "alignment";
        case ::dataflash::Status::NOT_AVAILABLE: return "not available";
        case ::dataflash::Status::FAILED: return "failed";
        case ::dataflash::Status::TIMEOUT: return "timeout";
        case ::dataflash::Status::NOT_ERASED: return "not erased";
        default: return "?";
    }
}

} // namespace dflash
} // namespace systems
