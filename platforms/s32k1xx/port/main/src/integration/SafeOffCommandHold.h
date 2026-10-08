#pragma once

/* SYS-151 (safety review night 2026-10-01, SR-147-3 b): every safe_off output stays at the level its driver commands,
 * also outside the safe state S1 - a foreign active level lasts at most one poll of the integration.
 *
 * Finding SYS-147: outside S1 nothing took back an active level that the driver had not written. `out set` held
 * DRV_EN high until the gate driver's own cycle (SYS-147 added holdDriverEnableLow(): at most cycle_ms, demo 10 ms,
 * and only for the TMC6200); the IN of the high-side switch had no such rule. In S1 the hold of SYS-36
 * (SafeStateHold.h) walks the table in every poll. This is the counterpart for the time outside S1, for every
 * safe_off output of the project, independent of the driver:
 *
 *   per safe_off output, in every poll (DebugHaltGuardSystem::POLL_MS = 1 ms, TASK_BSP): if the pin reads active and
 *   its driver does not command it active (integration/SafeOffOutputs.h, Commanded), drive it inactive with the
 *   store of the safe outputs table (PCOR of an active-high, PSOR of an active-low output) and count it.
 *
 * - It never drives an output active and never touches one its driver commands active (a gate driver that is
 *   enabled, an enable kept in an active short of SYS-139, a high-side switch that is on). A commanded level that the
 *   pin does not show is the driver's own business (its supervision: DRV_EN mismatch, the switch's diagnosis).
 * - Read of the pin, read of the commanded level and the store run with all interrupts suspended (a few loads and one
 *   store per output): a driver in a task of higher priority cannot command the output active between the two reads
 *   and the store, so a commanded switch-on is never taken back. The drivers record the level before they write the
 *   pin (setCommandedOutput()), so a pin that reads active while the record says inactive was written by someone
 *   else.
 * - An entry whose peripheral clock is gated is not touched (the access would bus fault), as in every table walk.
 * - In S1 it runs as well, after the hold of S1: the table walk has pulled every output low already, so it finds
 *   nothing - except an enable kept in an active short, which is commanded and left alone.
 *
 * Diagnosis: every take-back is counted (per hold, per output - SYS-161 - and the last output); the system logs it,
 * at most one line per LOG_INTERVAL_POLLS, and the console command "safeoff show" prints the count per output
 * (SYS-161, safety review 2026-10-01 SR-151-2 a; only in an image with console, SafeOffHoldCommand.h). A gate driver no longer sees most of these levels in its own cycle (they are gone within
 * 1 ms, its cycle is 10 ms), so its ENABLE_MISMATCH latch of SYS-147 is now the rarer reaction - the counter is the
 * record (safety review point SR-151-2).
 *
 * SYS-194 (safety review BL38-P1 2026-10-03, SYS147-B1 b): every take-back is also reported to the safe supervisor,
 * through the function the integration passes (Report; Integration.cpp: a DEVICE_REPORT word into the hold's own
 * DeviceReportBox in ordinary RAM, delivered by the safety task - S3, it never switches anything off). Board
 * 2026-10-03: a foreign DRV_EN high (J-Link PSOR) was taken back after 0.753 ms and nothing was stored, because the
 * 10-ms supervision of the gate driver (ENABLE_MISMATCH) no longer saw the level. Rate: the first take-back is
 * reported in the poll that found it; while further ones keep coming, every output with take-backs not reported yet
 * is reported again at most once per REPORT_INTERVAL_POLLS (one report then stands for all its take-backs since the
 * last one - the counters stay the complete record). A report the box refuses (full) stays due and is handed over
 * again in the next poll. The report runs after the take-back, outside the interrupt lock.
 *
 * Hardware independent: the pin read, the store and the commanded level are passed in (target: SafeOffPins.h; host
 * test test/host/SafeOffCommandHoldHostTest.cpp with a GPIO register model). Call from one task only. */

#include "interrupts/SuspendResumeAllInterruptsScopedLock.h"

#include <cstdint>

namespace integration
{

class SafeOffCommandHold
{
public:
    using Commanded = bool (*)(uint8_t index); // the driver commands safe_off output `index` active
    using Active    = bool (*)(uint8_t index); // the pin of safe_off output `index` reads active
    using SwitchOff = void (*)(uint8_t index); // drive safe_off output `index` inactive (the table's store)
    using Report    = bool (*)(uint8_t index); // SYS-194: report take-backs of output `index`; false: not accepted

    /** At most one log line per this many polls (1 s at 1 ms) while foreign levels keep coming. */
    static constexpr uint32_t LOG_INTERVAL_POLLS = 1000U;

    /** SYS-194: after a report, the next one at the earliest this many polls later (1 s at 1 ms). */
    static constexpr uint32_t REPORT_INTERVAL_POLLS = 1000U;

    /**
     * \param perOutput SYS-161: \p count counters, one per safe_off output (take-backs of that output since the
     *                  reset), owned by the caller; nullptr: no count per output
     * \param report    SYS-194: called for an output with take-backs not reported yet; nullptr: nothing reported
     * \param reported  SYS-194: \p count counters owned by the caller, the take-backs of each output covered by a
     *                  report. With \p perOutput one report per output; without either, a report names the output
     *                  taken back last and covers every take-back not reported yet.
     */
    SafeOffCommandHold(
        uint8_t const count,
        Commanded const commanded,
        Active const active,
        SwitchOff const switchOff,
        uint32_t* const perOutput = nullptr,
        Report const report       = nullptr,
        uint32_t* const reported  = nullptr)
    : _commanded(commanded)
    , _active(active)
    , _switchOff(switchOff)
    , _report(report)
    , _perOutput(perOutput)
    , _reported(reported)
    , _count(count)
    {}

    SafeOffCommandHold(SafeOffCommandHold const&)            = delete;
    SafeOffCommandHold& operator=(SafeOffCommandHold const&) = delete;

    /** One poll; \return the number of outputs taken back in this poll. */
    uint8_t cyclic()
    {
        uint8_t taken = 0U;
        for (uint8_t i = 0U; i < _count; ++i)
        {
            bool tookBack = false;
            {
                ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
                if (_active(i) && !_commanded(i))
                {
                    _switchOff(i);
                    tookBack = true;
                }
            }
            if (tookBack)
            {
                if (_perOutput != nullptr)
                {
                    ++_perOutput[i]; // SYS-161
                }
                ++_takeBacks;
                _lastIndex = i;
                ++taken;
            }
        }
        if (_quietPolls < LOG_INTERVAL_POLLS)
        {
            ++_quietPolls;
        }
        if (_reportQuietPolls < REPORT_INTERVAL_POLLS)
        {
            ++_reportQuietPolls;
        }
        reportDue(); // SYS-194: after the take-backs, outside the interrupt lock
        return taken;
    }

    /**
     * true once per new take-back, at most every LOG_INTERVAL_POLLS polls (the first one at once): the caller logs
     * getTakeBacks() and getLastIndex() then.
     */
    bool takeLogDue()
    {
        if ((_takeBacks == _loggedTakeBacks) || (_quietPolls < LOG_INTERVAL_POLLS))
        {
            return false;
        }
        _loggedTakeBacks = _takeBacks;
        _quietPolls      = 0U;
        return true;
    }

    /** Foreign active levels taken back since the reset. */
    uint32_t getTakeBacks() const { return _takeBacks; }

    /** SYS-161: foreign active levels of safe_off output \p index taken back since the reset (0 without counters). */
    uint32_t getTakeBacksOf(uint8_t const index) const
    {
        return ((_perOutput != nullptr) && (index < _count)) ? _perOutput[index] : 0U;
    }

    /** Index of the safe_off output taken back last (valid once getTakeBacks() is not 0). */
    uint8_t getLastIndex() const { return _lastIndex; }

    uint8_t getCount() const { return _count; }

    /** SYS-194: reports handed over (accepted by Report) since the reset. */
    uint32_t getReports() const { return _reports; }

    /** SYS-194: take-backs covered by the reports handed over; the rest of getTakeBacks() is still due. */
    uint32_t getReportedTakeBacks() const { return _reportedTotal; }

private:
    /** SYS-194: hands the take-backs not reported yet to Report (see the header comment for the rate). */
    void reportDue()
    {
        if ((_report == nullptr) || (_takeBacks == _reportedTotal) || (_reportQuietPolls < REPORT_INTERVAL_POLLS))
        {
            return;
        }
        bool handed = false;
        if ((_perOutput != nullptr) && (_reported != nullptr))
        {
            for (uint8_t out = 0U; out < _count; ++out)
            {
                uint32_t const due = _perOutput[out] - _reported[out];
                if ((due != 0U) && _report(out))
                {
                    _reported[out] = _perOutput[out];
                    _reportedTotal = _reportedTotal + due;
                    ++_reports;
                    handed = true;
                }
            }
        }
        else if (_report(_lastIndex))
        {
            _reportedTotal = _takeBacks;
            ++_reports;
            handed = true;
        }
        else
        {
            // refused (box full): due again in the next poll
        }
        if (handed)
        {
            _reportQuietPolls = 0U;
        }
    }

    Commanded _commanded;
    Active _active;
    SwitchOff _switchOff;
    Report _report;       // SYS-194, or nullptr
    uint32_t* _perOutput; // SYS-161: one counter per safe_off output, or nullptr
    uint32_t* _reported;  // SYS-194: per output the take-backs covered by a report, or nullptr
    uint32_t _takeBacks        = 0U;
    uint32_t _loggedTakeBacks  = 0U;
    uint32_t _quietPolls       = LOG_INTERVAL_POLLS;    // the first take-back is logged at once
    uint32_t _reportedTotal    = 0U;                    // SYS-194
    uint32_t _reports          = 0U;                    // SYS-194
    uint32_t _reportQuietPolls = REPORT_INTERVAL_POLLS; // SYS-194: the first take-back is reported at once
    uint8_t _count;
    uint8_t _lastIndex = 0U;
};

} // namespace integration
