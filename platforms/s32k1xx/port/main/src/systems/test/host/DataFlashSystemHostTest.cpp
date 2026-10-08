/* NVM-23: host test of the data flash in the run-down (systems/DataFlashRunDown.h): DataFlashGate,
 * DataFlashRunDownStep and dflash::advance() together with the real dataflash::AreaService (libs/bsw/nvmDataFlash)
 * over a simulated data flash of its OWN profile (not the eval board): 8 KB, 1 KB sectors, 8-byte program unit, a
 * command ends after a given number of polls.
 *
 * What it shows (safety review NVM12-S2 "no erase in the run-down"):
 * - open gate: an erase job runs one sector per command, a write job programs one unit per cycle with
 *   STEPS_PER_CYCLE steps (two cycles per unit with one step), content and blank check as AreaService does;
 * - the run-down step locks first: a job that runs (erase or write) ends after the command in flight
 * (NOT_AVAILABLE), no further
 *   command is launched, the step answers RUNNING until that command has ended - also after a failed NvM step - and
 *   the write hooks of the ROM check are balanced;
 * - with an NvM step: RUNNING until it has ended, its result is passed on (reason "NvM step failed"); without: "DFLASH";
 * - a command given up (TIMEOUT) does not keep the run-down waiting; unlock (lifecycle run()) allows jobs again;
 * - every status has a text.
 * NVM-26 (safety review NVM23-S1 b): new jobs only in STARTUP and RUN (admitsJobs(), the admission of the
 * AreaService); a job accepted in RUN runs on in STANDBY and is cut by the run-down step; back to RUN accepts again.
 * NVM-25 (safety review NVM12-S1 b): the gate passes the hold for a read in place through, but grants no new hold in
 * the run-down.
 *
 * Build and run (zig c++, no hardware; tools/test -k data-flash-system, or test_data_flash_system.py):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/main/include -Ilibs/bsw/nvmDataFlash/include -Ilibs/bsw/ecuState/include \
 *       platforms/s32k1xx/port/main/src/systems/test/host/DataFlashSystemHostTest.cpp -o build/host/dflashsys.exe
 */

#include "systems/DataFlashRunDown.h"

#include <cstdint>
#include <cstdio>
#include <cstring>

namespace
{
using ::dataflash::Status;
using ::ecustate::IShutdownStep;
using Result = ::ecustate::IShutdownStep::Result;
using ::systems::dflash::advance;
using ::systems::dflash::DataFlashGate;
using ::systems::dflash::DataFlashRunDownStep;

int checks   = 0;
int failures = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

constexpr uint32_t SIZE   = 8192U;
constexpr uint32_t SECTOR = 1024U;
constexpr uint32_t UNIT   = 8U;
constexpr uint8_t STEPS   = 4U; // like DataFlashSystem::STEPS_PER_CYCLE

/** Simulated data flash: a command ends after `pollsPerCommand` polls; can be made to time out. */
class SimFlash final : public ::dataflash::IDataFlash
{
public:
    uint8_t mem[SIZE];
    uint32_t pollsPerCommand = 1U;
    bool timeoutNext         = false;
    uint32_t launches        = 0U;
    uint32_t erases          = 0U;
    uint32_t programs        = 0U;
    bool running             = false;

    SimFlash() { std::memset(mem, 0x5A, sizeof(mem)); }

    ::dataflash::Geometry geometry() const override { return ::dataflash::Geometry{0x10000000U, SIZE, SECTOR, UNIT}; }

    Status read(uint32_t const offset, uint8_t* const buffer, uint32_t const length) override
    {
        if (running)
        {
            return Status::BUSY;
        }
        std::memcpy(buffer, &mem[offset], length);
        return Status::OK;
    }

    Status startErase(uint32_t const offset) override
    {
        if (running)
        {
            return Status::BUSY;
        }
        ++launches;
        ++erases;
        start(offset, nullptr, SECTOR);
        return Status::OK;
    }

    Status startProgram(uint32_t const offset, uint8_t const* const data) override
    {
        if (running)
        {
            return Status::BUSY;
        }
        ++launches;
        ++programs;
        start(offset, data, UNIT);
        return Status::OK;
    }

    Status poll() override
    {
        if (!running)
        {
            return Status::OK;
        }
        if (timeoutNext)
        {
            timeoutNext = false;
            running     = false; // given up by the driver; the model forgets it
            return Status::TIMEOUT;
        }
        if (++_polls < pollsPerCommand)
        {
            return Status::BUSY;
        }
        running = false;
        if (_data == nullptr)
        {
            std::memset(&mem[_offset], 0xFF, _length);
        }
        else
        {
            for (uint32_t i = 0U; i < _length; ++i)
            {
                mem[_offset + i] = static_cast<uint8_t>(mem[_offset + i] & _data[i]);
            }
        }
        return Status::OK;
    }

    // NVM-25: the block lock of the family driver, as the gate sees it
    uint32_t holds    = 0U;
    uint32_t releases = 0U;

    bool holdForDirectRead() override
    {
        if (running)
        {
            return false;
        }
        ++holds;
        return true;
    }

    void releaseDirectRead() override { ++releases; }

private:
    void start(uint32_t const offset, uint8_t const* const data, uint32_t const length)
    {
        running = true;
        _polls  = 0U;
        _offset = offset;
        _data   = data;
        _length = length;
    }

    uint32_t _polls          = 0U;
    uint32_t _offset         = 0U;
    uint8_t const* _data     = nullptr;
    uint32_t _length         = 0U;
};

int hookBegins = 0;
int hookEnds   = 0;

void hookBegin() { ++hookBegins; }

void hookEnd() { ++hookEnds; }

// own profile: two areas of 2 sectors, one of 4
::dataflash::Area const AREAS[] = {
    {"A", 0x0000U, 0x0800U, ::dataflash::Purpose::CALIBRATION},
    {"B", 0x0800U, 0x0800U, ::dataflash::Purpose::CALIBRATION},
    {"Big", 0x1000U, 0x1000U, ::dataflash::Purpose::DATA},
};

/** NvM step of the application: ends after `polls` polls with `result`. */
class FakeNvmStep final : public IShutdownStep
{
public:
    uint32_t polls   = 0U;
    Result result    = Result::DONE;
    bool started     = false;
    uint32_t pollsDone = 0U;

    char const* getName() const override { return "NvM"; }

    Result start(uint32_t) override
    {
        started = true;
        return (polls == 0U) ? result : Result::RUNNING;
    }

    Result poll(uint32_t) override
    {
        ++pollsDone;
        return (pollsDone >= polls) ? result : Result::RUNNING;
    }

    char const* getFailureReason() const override { return (result == Result::FAILED) ? "nvm write" : "-"; }

    uint32_t aborts = 0U; // SYS-174

    void abort(uint32_t) override { ++aborts; }
};

// NVM-26: the state the ECU state management published (DataFlashSystem.cpp: ecustate::rte::publishedState())
::ecustate::State ecuState = ::ecustate::State::RUN;

bool admitJob() { return ::systems::dflash::admitsJobs(ecuState); }

struct Rig
{
    SimFlash flash;
    DataFlashGate gate{flash};
    ::dataflash::AreaService areas{gate, AREAS, 3U, ::dataflash::WriteHooks{&hookBegin, &hookEnd}, &admitJob};
    DataFlashRunDownStep step{gate};

    Rig()
    {
        hookBegins = 0;
        hookEnds   = 0;
        ecuState   = ::ecustate::State::RUN;
    }

    /** Cycles of the owner task until the job has ended (bounded); its result through `last`. */
    uint32_t runJob(Status& last, uint8_t const steps = STEPS, uint32_t const limit = 10000U)
    {
        uint32_t cycles = 0U;
        last            = Status::BUSY;
        while (areas.busy() && (cycles < limit))
        {
            Status const s = advance(areas, gate, steps);
            ++cycles;
            if (!areas.busy())
            {
                last = s;
            }
        }
        return cycles;
    }
};

void openGateErase()
{
    std::printf("open gate: erase and write jobs\n");
    Rig r;
    CHECK(advance(r.areas, r.gate, STEPS) == Status::OK); // no job: nothing called
    CHECK(r.flash.launches == 0U);
    CHECK(r.areas.erase(2U) == Status::OK);
    Status last           = Status::OK;
    uint32_t const cycles = r.runJob(last);
    CHECK(last == Status::OK);
    CHECK(r.flash.erases == 4U);
    CHECK(cycles == 5U); // launch, then per cycle: end of one sector + launch of the next, last end
    CHECK(!r.gate.commandInFlight());
    for (uint32_t i = 0x1000U; i < 0x2000U; ++i)
    {
        if (r.flash.mem[i] != 0xFFU)
        {
            CHECK(r.flash.mem[i] == 0xFFU);
            break;
        }
    }
    CHECK(r.flash.mem[0x0FFFU] == 0x5AU); // area B untouched
    CHECK(hookBegins == 4);
    CHECK(hookEnds == 4);

    // write 64 bytes = 8 units into the erased area
    uint8_t data[64];
    for (uint32_t i = 0U; i < sizeof(data); ++i)
    {
        data[i] = static_cast<uint8_t>(0x30U + i);
    }
    CHECK(r.areas.write(2U, 0x100U, data, sizeof(data)) == Status::OK);
    uint32_t const writeCycles = r.runJob(last);
    CHECK(last == Status::OK);
    CHECK(r.flash.programs == 8U);
    CHECK(writeCycles <= 9U); // one unit per cycle with STEPS steps
    CHECK(std::memcmp(&r.flash.mem[0x1100U], data, sizeof(data)) == 0);
    CHECK(hookBegins == hookEnds);

    // with one step per cycle a unit needs two cycles - the reason for STEPS
    CHECK(r.areas.write(2U, 0x200U, data, sizeof(data)) == Status::OK);
    uint32_t const slowCycles = r.runJob(last, 1U);
    CHECK(last == Status::OK);
    CHECK(slowCycles >= 16U);
    CHECK(writeCycles < slowCycles);

    // a read through the gate while a command runs: BUSY (NVM12-S1: only read(), and read() knows the block)
    CHECK(r.areas.erase(0U) == Status::OK);
    (void)advance(r.areas, r.gate, STEPS);
    CHECK(r.gate.commandInFlight());
    uint8_t buffer[8] = {};
    CHECK(r.areas.read(1U, 0U, buffer, sizeof(buffer)) == Status::BUSY);
    (void)r.runJob(last);
    CHECK(r.areas.read(1U, 0U, buffer, sizeof(buffer)) == Status::OK);
}

void runDownCutsErase()
{
    std::printf("run-down: erase in flight, no NvM step\n");
    Rig r;
    r.flash.pollsPerCommand = 3U; // a command lasts three cycles
    r.step.setInner(nullptr);
    CHECK(std::strcmp(r.step.getName(), "DFLASH") == 0);
    CHECK(r.areas.erase(2U) == Status::OK);
    (void)advance(r.areas, r.gate, STEPS); // first sector launched
    CHECK(r.gate.commandInFlight());
    CHECK(r.flash.erases == 1U);

    CHECK(r.step.start(0U) == Result::RUNNING); // locked, but the command runs
    CHECK(r.gate.locked());
    CHECK(r.step.poll(10U) == Result::RUNNING);
    uint32_t polls = 0U;
    Result rr      = Result::RUNNING;
    while ((rr == Result::RUNNING) && (polls < 100U))
    {
        (void)advance(r.areas, r.gate, STEPS); // the owner task goes on
        rr = r.step.poll(20U + polls);
        ++polls;
    }
    CHECK(rr == Result::DONE);
    CHECK(!r.gate.commandInFlight());
    CHECK(!r.flash.running);
    CHECK(r.flash.erases == 1U);   // no sector after the lock
    CHECK(!r.areas.busy());
    CHECK(r.areas.lastResult() == Status::NOT_AVAILABLE);
    CHECK(r.gate.refused() == 1U);
    CHECK(hookBegins == hookEnds); // ROM check: no write left open
    CHECK(r.step.waits() >= 1U);
    CHECK(r.flash.mem[0x1400U] == 0x5AU); // sector 2 of the area never erased

    // nothing new while locked: erase and write end at their first launch
    CHECK(r.areas.erase(0U) == Status::OK);
    Status last = Status::OK;
    (void)r.runJob(last);
    CHECK(last == Status::NOT_AVAILABLE);
    CHECK(r.flash.erases == 1U);
    CHECK(hookBegins == hookEnds);

    // lifecycle run() again (run-down given up by a wake-up): unlocked, jobs work
    r.gate.unlock();
    CHECK(r.areas.erase(0U) == Status::OK);
    (void)r.runJob(last);
    CHECK(last == Status::OK);
    CHECK(r.flash.erases == 3U);
}

void runDownCutsWrite()
{
    std::printf("run-down: write in flight\n");
    Rig r;
    Status last = Status::OK;
    CHECK(r.areas.erase(1U) == Status::OK);
    (void)r.runJob(last);
    CHECK(last == Status::OK);
    r.flash.pollsPerCommand = 2U;
    uint8_t data[32];
    std::memset(data, 0x11, sizeof(data));
    CHECK(r.areas.write(1U, 0U, data, sizeof(data)) == Status::OK);
    (void)advance(r.areas, r.gate, STEPS); // first unit launched
    CHECK(r.gate.commandInFlight());
    uint32_t const programsBefore = r.flash.programs;
    CHECK(r.step.start(0U) == Result::RUNNING);
    Result rr      = Result::RUNNING;
    uint32_t polls = 0U;
    while ((rr == Result::RUNNING) && (polls < 100U))
    {
        (void)advance(r.areas, r.gate, STEPS);
        rr = r.step.poll(polls);
        ++polls;
    }
    CHECK(rr == Result::DONE);
    CHECK(r.flash.programs == programsBefore); // the unit in flight ends, no further unit
    CHECK(!r.areas.busy());
    CHECK(r.areas.lastResult() == Status::NOT_AVAILABLE);
    CHECK(r.flash.mem[0x0800U] == 0x11U); // the unit that was in flight is complete
    CHECK(r.flash.mem[0x0808U] == 0xFFU); // the next one was never started
    CHECK(hookBegins == hookEnds);
}

void runDownWithoutCommand()
{
    std::printf("run-down: nothing in flight\n");
    Rig r;
    CHECK(r.step.start(0U) == Result::DONE);
    CHECK(r.gate.locked());
    CHECK(std::strcmp(r.step.getFailureReason(), "-") == 0);
    CHECK(r.step.waits() == 0U);
}

void runDownWithNvmStep()
{
    std::printf("run-down: with the NvM step\n");
    {
        Rig r;
        FakeNvmStep nvm;
        nvm.polls = 3U;
        r.step.setInner(&nvm);
        CHECK(std::strcmp(r.step.getName(), "NvM") == 0);
        CHECK(r.step.start(0U) == Result::RUNNING);
        CHECK(nvm.started);
        CHECK(r.gate.locked()); // before the NvM step: its EEPROM stores do not wait behind new data flash commands
        CHECK(r.step.poll(10U) == Result::RUNNING);
        CHECK(r.step.poll(20U) == Result::RUNNING);
        CHECK(r.step.poll(30U) == Result::DONE);
        CHECK(nvm.pollsDone == 3U);
    }
    {
        // NvM fails while a data flash command is in flight: RUNNING until the command ended, then FAILED
        Rig r;
        r.flash.pollsPerCommand = 4U;
        FakeNvmStep nvm;
        nvm.polls  = 0U;
        nvm.result = Result::FAILED;
        r.step.setInner(&nvm);
        CHECK(r.areas.erase(0U) == Status::OK);
        (void)advance(r.areas, r.gate, STEPS);
        CHECK(r.gate.commandInFlight());
        CHECK(r.step.start(0U) == Result::RUNNING);
        Result rr      = Result::RUNNING;
        uint32_t polls = 0U;
        while ((rr == Result::RUNNING) && (polls < 100U))
        {
            (void)advance(r.areas, r.gate, STEPS);
            rr = r.step.poll(polls);
            ++polls;
        }
        CHECK(rr == Result::FAILED);
        CHECK(polls >= 3U);
        CHECK(!r.flash.running);
        CHECK(std::strcmp(r.step.getFailureReason(), "NvM step failed") == 0);
    }
    {
        // NvM done at once, command in flight: RUNNING, then DONE
        Rig r;
        r.flash.pollsPerCommand = 2U;
        FakeNvmStep nvm;
        r.step.setInner(&nvm);
        CHECK(r.areas.erase(1U) == Status::OK);
        (void)advance(r.areas, r.gate, STEPS);
        CHECK(r.step.start(0U) == Result::RUNNING);
        (void)advance(r.areas, r.gate, STEPS);
        CHECK(r.step.poll(10U) == Result::RUNNING);
        (void)advance(r.areas, r.gate, STEPS);
        CHECK(r.step.poll(20U) == Result::DONE);
    }
}

void timeoutDoesNotHoldRunDown()
{
    std::printf("run-down: command given up (TIMEOUT)\n");
    Rig r;
    r.flash.pollsPerCommand = 50U;
    CHECK(r.areas.erase(0U) == Status::OK);
    (void)advance(r.areas, r.gate, STEPS);
    CHECK(r.step.start(0U) == Result::RUNNING);
    r.flash.timeoutNext = true;
    (void)advance(r.areas, r.gate, STEPS);
    CHECK(!r.gate.commandInFlight());
    CHECK(r.areas.lastResult() == Status::TIMEOUT);
    CHECK(r.step.poll(10U) == Result::DONE);
    CHECK(hookBegins == hookEnds);
}

/**
 * SYS-174 (safety review 2026-10-02, SYS69-S5, ECS-F04): the step is cut off by its time limit. abort() keeps the gate
 * locked (no new command after the abort either), tells the NvM step of the application once, and does not touch the
 * command in flight - it runs to its end.
 */
void abortKeepsTheGateLocked()
{
    std::printf("run-down: abort after the time limit\n");
    Rig r;
    r.flash.pollsPerCommand = 50U;
    FakeNvmStep nvm;
    nvm.polls = 1000U; // never finishes on its own
    r.step.setInner(&nvm);
    CHECK(r.areas.erase(0U) == Status::OK);
    (void)advance(r.areas, r.gate, STEPS);
    CHECK(r.gate.commandInFlight());
    CHECK(r.step.start(0U) == Result::RUNNING);
    r.step.abort(100U);
    CHECK(nvm.aborts == 1U);
    CHECK(r.step.aborts() == 1U);
    CHECK(r.gate.locked());
    CHECK(r.gate.commandInFlight()); // the command in flight is not stopped
    r.step.abort(110U);              // a second abort does not reach the NvM step again
    CHECK(nvm.aborts == 1U);
    uint32_t const launchesBefore = r.flash.launches;
    for (uint32_t i = 0U; i < 200U; ++i)
    {
        (void)advance(r.areas, r.gate, STEPS);
    }
    CHECK(!r.gate.commandInFlight());
    CHECK(r.flash.launches == launchesBefore); // nothing new launched after the abort
    CHECK(r.gate.locked());
    CHECK(hookBegins == hookEnds);
}

void admissionPerState()
{
    std::printf("NVM-26: new jobs per ECU state\n");
    using ::ecustate::State;
    CHECK(::systems::dflash::admitsJobs(State::STARTUP));
    CHECK(::systems::dflash::admitsJobs(State::RUN));
    CHECK(!::systems::dflash::admitsJobs(State::STANDBY));
    CHECK(!::systems::dflash::admitsJobs(State::RUNDOWN));
    CHECK(!::systems::dflash::admitsJobs(State::SHUTDOWN));
    CHECK(!::systems::dflash::admitsJobs(State::SLEEP));
    CHECK(!::systems::dflash::admitsJobs(static_cast<State>(9U))); // not a state: the safe side
}

void standbyRefusesNewJobs()
{
    std::printf("NVM-26: STANDBY refuses new jobs, a running one goes on, the run-down cuts it\n");
    Rig r;
    Status last = Status::OK;
    uint8_t data[64];
    std::memset(data, 0x22, sizeof(data));
    CHECK(r.areas.erase(1U) == Status::OK);
    (void)r.runJob(last);
    CHECK(last == Status::OK);
    // a write accepted in RUN
    CHECK(r.areas.write(1U, 0U, data, sizeof(data)) == Status::OK);
    (void)advance(r.areas, r.gate, STEPS);
    // sleep requested: STANDBY - no new job, from the console or the application alike
    ecuState = ::ecustate::State::STANDBY;
    CHECK(r.areas.erase(0U) == Status::NOT_AVAILABLE);
    CHECK(r.areas.write(2U, 0U, data, 8U) == Status::NOT_AVAILABLE);
    CHECK(r.areas.refusedJobs() == 2U);
    // the job accepted before runs to its end in the post-run (the gate stays open: no run-down yet)
    CHECK(!r.gate.locked());
    (void)r.runJob(last);
    CHECK(last == Status::OK);
    CHECK(r.flash.mem[0x0800U + 63U] == 0x22U);
    CHECK(r.flash.erases == 2U); // only the erase of RUN, nothing new
    // back to RUN (sleep request withdrawn): jobs again
    ecuState = ::ecustate::State::RUN;
    CHECK(r.areas.erase(0U) == Status::OK);
    (void)r.runJob(last);
    CHECK(last == Status::OK);
    CHECK(r.flash.erases == 4U);
    // a long job reaching into the run-down: refused requests in STANDBY and RUNDOWN, the step cuts the job
    CHECK(r.areas.erase(2U) == Status::OK);
    r.flash.pollsPerCommand = 3U;
    (void)advance(r.areas, r.gate, STEPS);
    ecuState = ::ecustate::State::STANDBY;
    CHECK(r.areas.write(0U, 0U, data, 8U) == Status::NOT_AVAILABLE);
    ecuState = ::ecustate::State::RUNDOWN;
    CHECK(r.step.start(0U) == Result::RUNNING);
    Result rr      = Result::RUNNING;
    uint32_t polls = 0U;
    while ((rr == Result::RUNNING) && (polls < 100U))
    {
        (void)advance(r.areas, r.gate, STEPS);
        rr = r.step.poll(polls);
        ++polls;
    }
    CHECK(rr == Result::DONE);
    CHECK(r.areas.lastResult() == Status::NOT_AVAILABLE);
    CHECK(r.flash.erases == 5U); // the sector in flight ended, no further one
    CHECK(r.areas.erase(1U) == Status::NOT_AVAILABLE);
    ecuState = ::ecustate::State::SHUTDOWN;
    CHECK(r.areas.erase(1U) == Status::NOT_AVAILABLE);
    CHECK(r.areas.refusedJobs() == 5U);
    CHECK(hookBegins == hookEnds);
}

void holdThroughTheGate()
{
    std::printf("NVM-25: hold for a read in place through the gate\n");
    Rig r;
    {
        ::dataflash::DirectRead scope = r.areas.holdForDirectRead();
        CHECK(scope.held());
        CHECK(r.flash.holds == 1U);
        CHECK(r.areas.areaAddress(scope, 1U, 8U, 8U) == (0x10000000U + 0x0808U));
    }
    CHECK(r.flash.releases == 1U);
    // a command in flight: the driver refuses, the gate passes that on
    CHECK(r.areas.erase(0U) == Status::OK);
    (void)advance(r.areas, r.gate, STEPS);
    CHECK(!r.gate.holdForDirectRead());
    Status last = Status::OK;
    (void)r.runJob(last);
    // in the run-down no new hold (the NvM step must not wait for a reader); a hold from before ends normally
    CHECK(r.gate.holdForDirectRead());
    CHECK(r.step.start(0U) == Result::DONE);
    CHECK(!r.gate.holdForDirectRead());
    CHECK(r.flash.holds == 2U);
    r.gate.releaseDirectRead();
    CHECK(r.flash.releases == 2U);
    r.gate.unlock();
    CHECK(r.gate.holdForDirectRead());
    r.gate.releaseDirectRead();
    CHECK(r.flash.releases == 3U);
}

void texts()
{
    std::printf("status texts\n");
    Status const all[] = {Status::OK,
                          Status::BUSY,
                          Status::RANGE,
                          Status::ALIGNMENT,
                          Status::NOT_AVAILABLE,
                          Status::FAILED,
                          Status::TIMEOUT,
                          Status::NOT_ERASED};
    for (Status const a : all)
    {
        CHECK(std::strcmp(::systems::dflash::statusText(a), "?") != 0);
        for (Status const b : all)
        {
            if (a != b)
            {
                CHECK(std::strcmp(::systems::dflash::statusText(a), ::systems::dflash::statusText(b)) != 0);
            }
        }
    }
}
} // namespace

int main()
{
    openGateErase();
    runDownCutsErase();
    runDownCutsWrite();
    runDownWithoutCommand();
    runDownWithNvmStep();
    timeoutDoesNotHoldRunDown();
    abortKeepsTheGateLocked();
    admissionPerState();
    standbyRefusesNewJobs();
    holdThroughTheGate();
    texts();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
