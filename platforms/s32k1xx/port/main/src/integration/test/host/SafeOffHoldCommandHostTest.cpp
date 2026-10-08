/* SYS-161 (safety review 2026-10-01, SR-151-2 a): host test of the console group "safeoff" - the take-backs of the
 * hold of the commanded level (SYS-151) per safe_off output at the console.
 *
 * Real code: integration::SafeOffHoldCommand, integration/SafeOffOutputs.h with the own test profile of safeOffStubs
 * (invented project "irrigation_ctrl": safe_off outputs PUMP_EN = output 3 and HEATER_N = output 1, in this order in
 * the safe outputs table, after the PWM group FAN), the real OpenBSW command framework (GroupCommand, CommandContext,
 * SharedStringWriter, printf formatter). The snapshot function is the test's (on the target Integration.cpp takes it
 * in TASK_BSP through console::runInOwnerContext). The command is executed as the console does:
 * GroupCommand::execute(<arguments>, stream).
 *
 * What it shows:
 *   - "show": one line per safe_off output in the order of the table, with its number and name of the group "out",
 *     the commanded level, the pin level and the count of take-backs, then the total; result OK; one snapshot;
 *   - SYS-194: the last line, the take-backs reported to the safe supervisor and the number of DEVICE_REPORTs - its
 *     own values of the snapshot, not the total again;
 *   - large counts are printed unsigned and complete;
 *   - a snapshot with more outputs than the project has is cut to the project's outputs, one without outputs says so;
 *   - extra arguments and unknown sub commands take no snapshot and print nothing.
 *
 * Build and run (zig c++, no hardware): tools/test -k safe-off-hold-command
 */

#include "SafeOffHoldCommand.h"

#include "integration/SafeOffOutputs.h"
#include "outputManager/Output.h"
#include "util/stream/SharedOutputStream.h"

#include <cstdio>
#include <string>

namespace
{
int failures = 0;
int checks   = 0;

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

using Command  = ::integration::SafeOffHoldCommand;
using Result   = ::util::command::ICommand::Result;
using Snapshot = Command::Snapshot;

class TextStream : public ::util::stream::IOutputStream
{
public:
    bool isEof() const override { return false; }

    void write(uint8_t const data) override { text.push_back(static_cast<char>(data)); }

    void write(::etl::span<uint8_t const> const& buffer) override
    {
        for (uint8_t const data : buffer)
        {
            text.push_back(static_cast<char>(data));
        }
    }

    std::string text;
};

Snapshot next     = {};
int snapshotCalls = 0;

void take(Snapshot& snapshot)
{
    ++snapshotCalls;
    snapshot = next;
}

struct Run
{
    Result result;
    std::string text;
    int snapshots;
};

Run execute(Command& command, char const* const arguments)
{
    snapshotCalls = 0;
    TextStream text;
    ::util::stream::SharedOutputStream shared(text);
    ::util::command::ICommand::ExecuteResult const result
        = command.execute(::util::string::ConstString(arguments), &shared);
    return Run{result.getResult(), text.text, snapshotCalls};
}
} // namespace

namespace bios
{
char const* Output::getName(uint16_t const channel)
{
    static char const* const names[] = {"LED_GREEN", "HEATER_N", "FAN", "PUMP_EN", "BRAKE_LIGHT"};
    return (channel < 5U) ? names[channel] : "lastStatic";
}
} // namespace bios

namespace
{
void testShow()
{
    static_assert(::integration::safeoff::COUNT == 2U, "test profile: two safe_off outputs");
    static_assert(Command::MAX_OUTPUTS == 2U, "one entry per safe_off output");
    Command command(&take);
    next                = Snapshot{};
    next.count          = 2U;
    next.total          = 7U;
    next.reported       = 6U; // SYS-194: one take-back still due
    next.reports        = 4U;
    next.takeBacks[0]   = 5U; // PUMP_EN
    next.takeBacks[1]   = 2U; // HEATER_N
    next.commanded[0]   = false;
    next.active[0]      = false;
    next.commanded[1]   = true;
    next.active[1]      = true;
    Run const run       = execute(command, "show");
    CHECK(run.result == Result::OK);
    CHECK(run.snapshots == 1);
    CHECK(
        run.text
        == "3 : PUMP_EN -> commanded 0, pin 0, taken back 5\r\n"
           "1 : HEATER_N -> commanded 1, pin 1, taken back 2\r\n"
           "taken back in total: 7\r\n"
           "reported to the safe supervisor: 6 in 4 DEVICE_REPORT(s)\r\n");
    if (run.result != Result::OK)
    {
        std::printf("    \"%s\"\n", run.text.c_str());
    }
    // the case of the sub command does not matter (OpenBSW GroupCommand), spaces neither
    Run const upper = execute(command, "SHOW  ");
    CHECK(upper.result == Result::OK && upper.text == run.text);

    // counts beyond 2^31 stay unsigned and complete
    next.takeBacks[0] = 4000000000U;
    next.total        = 4000000002U;
    next.reported     = 4000000001U;
    next.reports      = 3000000000U;
    Run const big     = execute(command, "show");
    CHECK(big.text.find("taken back 4000000000\r\n") != std::string::npos);
    CHECK(big.text.find("taken back in total: 4000000002\r\n") != std::string::npos);
    CHECK(big.text.find("reported to the safe supervisor: 4000000001 in 3000000000 DEVICE_REPORT(s)\r\n")
          != std::string::npos);

    // a pin that reads active without being commanded (between a foreign write and the next poll)
    next              = Snapshot{};
    next.count        = 2U;
    next.active[0]    = true;
    next.takeBacks[0] = 1U;
    next.total        = 1U;
    Run const foreign = execute(command, "show");
    CHECK(foreign.text.find("3 : PUMP_EN -> commanded 0, pin 1, taken back 1\r\n") == 0U);
}

void testLimits()
{
    Command command(&take);
    // more outputs reported than the project has: only the project's are printed
    next        = Snapshot{};
    next.count  = 9U;
    Run const n = execute(command, "show");
    CHECK(n.result == Result::OK);
    CHECK(n.text
          == "3 : PUMP_EN -> commanded 0, pin 0, taken back 0\r\n"
             "1 : HEATER_N -> commanded 0, pin 0, taken back 0\r\n"
             "taken back in total: 0\r\n"
             "reported to the safe supervisor: 0 in 0 DEVICE_REPORT(s)\r\n");
    // a hold without outputs
    next        = Snapshot{};
    Run const z = execute(command, "show");
    CHECK(z.result == Result::OK && z.text == "no safe_off output\r\n");
}

void testInvalid()
{
    Command command(&take);
    next = Snapshot{};
    next.count = 2U;
    char const* const invalid[] = {"show 1", "show x", "reset", "", "sho"};
    for (char const* const arguments : invalid)
    {
        Run const run = execute(command, arguments);
        CHECK(run.snapshots == 0);
        CHECK(run.result != Result::OK);
        CHECK(run.text.find("taken back") == std::string::npos);
        CHECK(run.text.find("reported") == std::string::npos);
        if ((run.snapshots != 0) || (run.result == Result::OK))
        {
            std::printf("    arguments \"%s\": %d \"%s\"\n", arguments, static_cast<int>(run.result), run.text.c_str());
        }
    }
    CHECK(std::string(command.getId()) == "safeoff");
    CHECK(Command::SHOW_COMMAND == 1U);
}
} // namespace

int main()
{
    (void)std::setvbuf(stdout, nullptr, _IONBF, 0);
    testShow();
    testLimits();
    testInvalid();
    std::printf("safe_off hold command (SYS-161): %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
