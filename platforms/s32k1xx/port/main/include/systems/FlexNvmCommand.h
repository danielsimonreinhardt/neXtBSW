// Console command "flexnvm" (NVM-07): FlexNVM partition of the S32K1 family.
//
//   flexnvm show               partition as it is (SIM FCFG1, EEERDY), EEPROM driver (state, time limit, longest write,
//                              time-outs), the partition the project wants (generated nvm/nvmPartition.h) and, in test
//                              builds, the journal of the last "flexnvm partition" (NVM-22, survives the reset)
//   flexnvm partition <word>   test builds only (CMake option NX_FLEXNVM_PARTITION_COMMAND): writes the partition of
//                              the project - IRREVERSIBLE - if <word> is the confirmation word "show" prints and the
//                              chip is as delivered. Never called at start-up.
//
// NVM-22 (board 2026-09-30, capture nvm07_pgmpart.txt): PGMPART cannot run beside the safety task. The flash is not
// accessible while the command runs ([RM13] 36.5.11.14: it must not be launched from flash, "flash memory resources
// are not accessible"), so OpenBSW's EepromDriver runs it from RAM with all interrupts suspended - for the data sheet's
// 71..78 ms (typical, no maximum given), on the board 2026-09-30 a gap of 31 ms between two safety cycles. The safety
// task's period check (SYS-40) sees that gap and resets the ECU (SAFETY_CYCLE_PERIOD_VIOLATION, S1 + S2) as soon as
// the interrupts come back - before the console context printed the result. The supervision is not touched: the
// reset is the expected end of the command (the ECU must be reset after PGMPART anyway). Instead the command
//  1. refuses if a watchdog outside the MCU would not allow PGMPART_BUDGET_MS (NVM-17, as "csec"),
//  2. writes the journal (no-init RAM) "launching" and announces the expected reset (console line, flushed),
//  3. suspends all interrupts itself around FlexNvmPartition::partition() - the driver's own suspend/resume nests
//     inside (PRIMASK saved and restored), so the interrupts stay off when it returns,
//  4. writes the journal "done" with the result and the result line synchronously (safety::safeConsoleWrite(),
//     polling, no interrupt), waits until the transmitter is idle,
//  5. resumes the interrupts - now the period check may reset the ECU; the result is already on the wire and in the
//     journal, which "flexnvm show" prints after the start.
// runPartition() below is that order, free of hardware for the host test (test/host/FlexNvmCommandHostTest.cpp).

#pragma once

#include <util/command/GroupCommand.h>

#include <cstdint>

namespace systems
{

class FlexNvmCommand final : public ::util::command::GroupCommand
{
protected:
    DECLARE_COMMAND_GROUP_GET_INFO
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;
};

namespace flexnvmrun
{
/**
 * NVM-22: how long "flexnvm partition" may keep the interrupts suspended, ms - PGMPART (data sheet: typical 71..78 ms
 * for 32/64 KB EEPROM backup, no maximum; board 2026-09-30: under 31 ms) plus SETRAM and the result line. The MCU
 * watchdog is not serviced in that time: 150 ms leave at least the service interval of the safety task (70 ms) plus a
 * cycle of lateness to its shortest time-out (demo 230 ms at the fastest LPO). A watchdog outside the MCU must allow
 * it (integration::externalWatchdogAllowanceMs()), otherwise nothing is launched.
 */
constexpr uint32_t PGMPART_BUDGET_MS = 150U;

/** NVM-22: journal of the last "flexnvm partition" in no-init RAM (FlexNvmCommand.cpp); read by "flexnvm show". */
struct Journal
{
    static constexpr uint32_t TAG            = 0x464E564DU; // "FNVM"
    static constexpr uint8_t STATE_LAUNCHING = 1U; // written before the launch; still there after a reset: PGMPART
                                                   // was interrupted (watchdog, power) - check the chip state
    static constexpr uint8_t STATE_DONE      = 2U; // partition() returned, result valid
    static constexpr uint8_t NO_RESULT       = 0xFFU;

    uint32_t tag;
    uint32_t word;  // state 31:24, result 23:16 (FlexNvmPartition::Result), SIM FCFG1 DEPART before 15:8
    uint32_t check; // ~(tag ^ word), written last

    bool valid() const volatile { return (tag == TAG) && (check == ~(tag ^ word)); }

    uint8_t state() const volatile { return static_cast<uint8_t>(word >> 24U); }

    uint8_t result() const volatile { return static_cast<uint8_t>(word >> 16U); }

    uint8_t departBefore() const volatile { return static_cast<uint8_t>(word >> 8U); }

    void write(uint8_t const newState, uint8_t const newResult, uint8_t const depart) volatile
    {
        uint32_t const w = (static_cast<uint32_t>(newState) << 24U) | (static_cast<uint32_t>(newResult) << 16U)
                           | (static_cast<uint32_t>(depart) << 8U);
        check = 0U; // invalid while it changes
        tag   = TAG;
        word  = w;
        check = ~(TAG ^ w);
    }
};

/** Outcome of runPartition(). */
struct Outcome
{
    bool launched;  // false: the watchdog outside the MCU does not allow the budget, nothing called, journal as it was
    uint8_t result; // FlexNvmPartition::Result of partition() (only if launched)
};

/**
 * NVM-22: the order of "flexnvm partition" (see the file comment). Ops (the target glue in FlexNvmCommand.cpp, a
 * recorder in the host test) provides:
 *   bool externalWatchdogAllows()     the watchdog outside the MCU allows PGMPART_BUDGET_MS
 *   uint8_t departCode()              SIM FCFG1 DEPART now
 *   void announce()                   console lines before the launch, then the transmitter flushed
 *   uint32_t suspendAllInterrupts()   PRIMASK before, all interrupts suspended
 *   uint8_t partition()               FlexNvmPartition::partition() of the plan with the confirmation word
 *   void resultLine(uint8_t result)   the result, synchronously on the console (interrupts suspended), flushed
 *   void resumeAllInterrupts(uint32_t)
 */
template<class Ops>
Outcome runPartition(Ops& ops, Journal volatile& journal)
{
    if (!ops.externalWatchdogAllows())
    {
        return Outcome{false, Journal::NO_RESULT};
    }
    uint8_t const depart = ops.departCode();
    journal.write(Journal::STATE_LAUNCHING, Journal::NO_RESULT, depart);
    ops.announce();
    uint32_t const primask = ops.suspendAllInterrupts();
    uint8_t const result   = ops.partition();
    journal.write(Journal::STATE_DONE, result, depart);
    ops.resultLine(result);
    ops.resumeAllInterrupts(primask);
    return Outcome{true, result};
}

} // namespace flexnvmrun

} // namespace systems
