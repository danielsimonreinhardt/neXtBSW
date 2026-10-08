#pragma once

/* SYS-161 (safety review 2026-10-01, SR-151-2 a): console group "safeoff" - the hold of the commanded level of the
 * safe_off outputs (SYS-151, SafeOffCommandHold.h) at the console:
 *
 *   safeoff show   per safe_off output: its number of the console group "out", its name, the level its driver
 *                  commands, the pin level (read-back of the safe outputs table) and the foreign active levels
 *                  taken back since the reset; then the total and (SYS-194) how many of them the hold has
 *                  reported to the safe supervisor as DEVICE_REPORT, in how many reports
 *
 *   3 : MOT1_GD_DRV_EN -> commanded 0, pin 0, taken back 2
 *   7 : EN_SW_KL30B -> commanded 1, pin 1, taken back 0
 *   taken back in total: 2
 *   reported to the safe supervisor: 2 in 2 DEVICE_REPORT(s)
 *
 * Why: a gate driver no longer sees most foreign DRV_EN levels in its own cycle (they are gone within one poll of
 * 1 ms), so its ENABLE_MISMATCH latch of SYS-147 is the rarer reaction; the log line of the hold names only the last
 * output and is rate limited. The count per output is the record of every take-back.
 *
 * Read only. The command runs in the console context; the snapshot is taken by the function the integration passes
 * (Integration.cpp: in the task of the hold, ::console::runInOwnerContext), so nothing here needs a lock and the
 * output of the command never holds the task of the hold (IO-42). Only in an image with console (NX_CONSOLE, PLAT-106:
 * Integration.cpp constructs it under that condition). No configuration value: the list is the safe_off block of the
 * generated safe outputs table (integration/SafeOffOutputs.h). Host test: test/host/SafeOffHoldCommandHostTest.cpp. */

#include "integration/SafeOffOutputs.h"

#include <util/command/GroupCommand.h>

#include <cstdint>

namespace integration
{

class SafeOffHoldCommand final : public ::util::command::GroupCommand
{
public:
    /** Sub command "show". */
    static constexpr uint8_t SHOW_COMMAND = 1U;

    static constexpr uint8_t MAX_OUTPUTS = (safeoff::COUNT > 0U) ? safeoff::COUNT : 1U;

    /** The state of the hold at one instant, taken in the task of the hold. */
    struct Snapshot
    {
        uint8_t count;                    // safe_off outputs of the hold
        uint32_t total;                   // take-backs since the reset, all outputs
        uint32_t reported;                // SYS-194: take-backs covered by a report to the safe supervisor
        uint32_t reports;                 // SYS-194: DEVICE_REPORTs handed to the hold's event box
        uint32_t takeBacks[MAX_OUTPUTS];  // per safe_off output (index as in integration::safeoff)
        bool commanded[MAX_OUTPUTS];      // its driver commands it active
        bool active[MAX_OUTPUTS];         // the pin reads active
    };

    using TakeSnapshot = void (*)(Snapshot& snapshot);

    explicit SafeOffHoldCommand(TakeSnapshot const take) : _take(take) {}

    SafeOffHoldCommand(SafeOffHoldCommand const&)            = delete;
    SafeOffHoldCommand& operator=(SafeOffHoldCommand const&) = delete;

protected:
    DECLARE_COMMAND_GROUP_GET_INFO
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;

private:
    TakeSnapshot _take;
};

} // namespace integration
