/* PLAT-110 (safety review 2026-10-01, SR-150-1 b): host test of the console group "pwm" of the family port -
 * "set" and "setPeriod" run only for a complete, valid line and a channel in range, everything else is the OpenBSW
 * tester.
 *
 * Real code: systems::CheckedOutputPwmTester, the real OpenBSW bios::OutputPwmTester (OutputPwmTester.cpp) and command
 * framework (GroupCommand, CommandContext, SharedStringWriter, printf formatter). The PWM output manager is the stub of
 * pwmTesterStubs (own test profile "irrigation_ctrl": static dummy channel 0, PWM channels FAN 1 and VALVE 2); its
 * setDuty() and setPeriod() record every call and, unlike ComplementaryPwm of this family port, setPeriod() is
 * supported - a PWM client with a period, as OpenBSW allows. The command is executed as the console does:
 * GroupCommand::execute(<arguments>, stream).
 *
 * What it shows:
 *   - the findings on the unwrapped OpenBSW tester: "setPeriod 65537 1000" reports "wrong channel" and still sets
 *     the period of channel 1 (65537 narrowed to uint16_t); "set 1" (no duty) writes duty 0 to channel 1;
 *     "set x 5000" writes channel 0;
 *   - with the wrapper none of these lines reaches the output manager (no call, no text, result not OK);
 *   - every valid line ("set" of one channel and of all, clamping of duty and period, "setPeriod", "all", any
 *     spelling of the numbers) gives the same text, result and calls as the OpenBSW tester - except the lines of
 *     SYS-162 below.
 *
 * SYS-162 (safety review group A 2026-10-01, SR-110-1 b): the channels of a PWM group of the safe outputs table
 * (pwmTesterStubs/bsp/safeOutputs: PWM group IRRIGATION = FAN and VALVE, then the safe_off output PUMP_EN) are refused
 * for "set" and "setPeriod" in every spelling of the number, "set 0xFF" (every channel) as well: one line
 * "<n> : <name> -> refused: ...", result BAD_VALUE, no call of OutputPwm. The static dummy channel stays as in OpenBSW.
 * The same source runs against a second profile without PWM group (pwmTesterStubsNoGroup, "dosing_unit": the table
 * holds one safe_off output, OutputPwm only the dummy channel) - there every valid line, "set 0xFF" included, is
 * exactly OpenBSW (tools/test -k checked-pwm-tester-no-group).
 *
 * Build and run (zig c++, no hardware): tools/test -k checked-pwm-tester
 */

#include "systems/CheckedOutputPwmTester.h"

#include "outputPwm/OutputPwm.h"
#include "outputPwm/OutputPwmTester.h"
#include "util/stream/SharedOutputStream.h"

#include <cstdio>
#include <initializer_list>
#include <string>
#include <vector>

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

struct Call
{
    char kind; // 'd' setDuty, 'p' setPeriod
    int channel;
    int value;
};

std::vector<Call> calls;

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

class Plain : public ::bios::OutputPwmTester
{
};

using Result = ::util::command::ICommand::Result;

struct Run
{
    Result result;
    std::string text;
    std::vector<Call> calls;
};

Run execute(::util::command::GroupCommand& command, char const* const arguments)
{
    calls.clear();
    TextStream text;
    ::util::stream::SharedOutputStream shared(text);
    ::util::command::ICommand::ExecuteResult const result
        = command.execute(::util::string::ConstString(arguments), &shared);
    return Run{result.getResult(), text.text, calls};
}

bool sameCalls(std::vector<Call> const& a, std::vector<Call> const& b)
{
    if (a.size() != b.size())
    {
        return false;
    }
    for (size_t i = 0U; i < a.size(); ++i)
    {
        if ((a[i].kind != b[i].kind) || (a[i].channel != b[i].channel) || (a[i].value != b[i].value))
        {
            return false;
        }
    }
    return true;
}

/** The test profile has generated PWM channels (a PWM group in the safe outputs table, SYS-162). */
constexpr bool WITH_GROUP = ::bios::OutputPwm::_pwmChannelMax > ::bios::OutputPwm::_pwmDynamicStart;

bool hasCall(Run const& run, char const kind, int const channel, int const value)
{
    for (Call const& c : run.calls)
    {
        if ((c.kind == kind) && (c.channel == channel) && (c.value == value))
        {
            return true;
        }
    }
    return false;
}
} // namespace

namespace bios
{
bsp::BspReturnCode OutputPwm::setDuty(uint16_t const chan, uint16_t const duty, bool)
{
    calls.push_back(Call{'d', static_cast<int>(chan), static_cast<int>(duty)});
    return (chan < outputPwmNumberAll) ? bsp::BSP_OK : bsp::BSP_ERROR;
}

bsp::BspReturnCode OutputPwm::setPeriod(uint16_t const chan, uint16_t const period)
{
    calls.push_back(Call{'p', static_cast<int>(chan), static_cast<int>(period)});
    return (chan < outputPwmNumberAll) ? bsp::BSP_OK : bsp::BSP_ERROR;
}

char const* OutputPwm::getName(uint16_t const channel)
{
    static char const* const names[] = {"dummyPwm", "FAN", "VALVE"};
    return (channel < outputPwmNumberAll) ? names[channel] : "??? ->";
}
} // namespace bios

namespace
{
/* The findings of the audit, shown on the unwrapped OpenBSW tester (P1, P2). */
void testFindingsOfOpenBsw()
{
    if (!WITH_GROUP)
    {
        return; // channel 1 does not exist in the profile without PWM group
    }
    Plain plain;
    Run const alias = execute(plain, "setPeriod 65537 1000");
    CHECK(alias.text.find("wrong channel") != std::string::npos);
    CHECK(hasCall(alias, 'p', 1, 1000)); // P1: the period of FAN set past the reported range error
    Run const noDuty = execute(plain, "set 1");
    CHECK(noDuty.result != Result::OK);
    CHECK(hasCall(noDuty, 'd', 1, 0)); // P2: duty 0 written although the line is incomplete
    Run const badChannel = execute(plain, "set x 5000");
    CHECK(badChannel.result != Result::OK);
    CHECK(hasCall(badChannel, 'd', 0, 0)); // P2: channel 0 written for an invalid channel
}

void testRefused()
{
    ::systems::CheckedOutputPwmTester tester;
    struct Case
    {
        char const* arguments;
        Result result;
    };
    Case const refused[] = {
        {"setPeriod 65537 1000", Result::BAD_VALUE},
        {"setPeriod 3 1000", Result::BAD_VALUE},
        {"setPeriod 255 1000", Result::BAD_VALUE}, // 0xFF is "all" only for set
        {"setPeriod 4294967295 1000", Result::BAD_VALUE},
        {"set 3 100", Result::BAD_VALUE},
        {"set 256 100", Result::BAD_VALUE},
        {"set 65537 100", Result::BAD_VALUE},
        {"set -1 100", Result::BAD_VALUE}, // the scanner gives 0xFFFFFFFF
        {"set 1", Result::BAD_TOKEN},
        {"set 0xFF", Result::BAD_TOKEN},
        {"set 1 x", Result::BAD_TOKEN},
        {"set x 5000", Result::BAD_TOKEN},
        {"set", Result::BAD_TOKEN},
        {"setPeriod 1", Result::BAD_TOKEN},
        {"setPeriod", Result::BAD_TOKEN},
        {"setPeriod 1 0x", Result::BAD_TOKEN},
        {"set 1 100 7", Result::UNEXPECTED_TOKEN},
        {"setPeriod 1 100 7", Result::UNEXPECTED_TOKEN},
    };
    for (Case const& c : refused)
    {
        Run const run = execute(tester, c.arguments);
        CHECK(run.calls.empty());
        CHECK(run.result == c.result);
        CHECK(run.text.empty());
        if (!run.calls.empty() || (run.result != c.result) || !run.text.empty())
        {
            std::printf(
                "    arguments \"%s\": result %d, %u calls, \"%s\"\n",
                c.arguments,
                static_cast<int>(run.result),
                static_cast<unsigned>(run.calls.size()),
                run.text.c_str());
        }
    }
}

void testEverythingValidAsOpenBsw()
{
    ::systems::CheckedOutputPwmTester tester;
    Plain plain;
    // every valid line that addresses no PWM group of the safe outputs table (SYS-162): the static dummy channel,
    // "all", and "set 0xFF" in a project without PWM group
    char const* const same[] = {
        "all",
        "set 0 100",   // the static dummy channel
        "set 0 20000", // clamped to 10000
        "set 0x0 0x10",
        "set 00 010",
        "set +0 +1",
        "SET 0 1",
        "set   0    3",
        "setPeriod 0 5",
        "setPeriod 0 70000", // clamped to 0xFFFF
        "setperiod 0 1",
        "unknown",
        "",
        WITH_GROUP ? "all" : "set 0xFF 100", // every channel - only the dummy channel without PWM group
        WITH_GROUP ? "all" : "set 255 0"};
    for (char const* const arguments : same)
    {
        Run const wrapped  = execute(tester, arguments);
        Run const original = execute(plain, arguments);
        CHECK(wrapped.result == original.result);
        CHECK(wrapped.text == original.text);
        CHECK(sameCalls(wrapped.calls, original.calls));
        if ((wrapped.result != original.result) || (wrapped.text != original.text)
            || !sameCalls(wrapped.calls, original.calls))
        {
            std::printf(
                "    arguments \"%s\": wrapped \"%s\", OpenBSW \"%s\"\n",
                arguments,
                wrapped.text.c_str(),
                original.text.c_str());
        }
    }
    // a valid line really reaches the output manager through the wrapper
    Run const one = execute(tester, "set 0 5000");
    CHECK(one.result == Result::OK && one.calls.size() == 1U && hasCall(one, 'd', 0, 5000));
    Run const period = execute(tester, "setPeriod 0 70000");
    CHECK(period.calls.size() == 1U && hasCall(period, 'p', 0, 0xFFFF));
    if (!WITH_GROUP)
    {
        Run const all = execute(tester, "set 0xFF 100");
        CHECK(all.result == Result::OK && all.calls.size() == 1U && hasCall(all, 'd', 0, 100));
    }
}

/* SYS-162: no duty or period of a PWM group of the safe outputs table from the console. */
void testSafetyPwmGroupRefused()
{
    if (!WITH_GROUP)
    {
        // the profile without PWM group: nothing is a channel of such a group
        for (uint32_t channel : {0U, 1U, 2U, 0xFFU, 65537U})
        {
            CHECK(!::systems::CheckedOutputPwmTester::isSafetyPwmChannel(channel));
        }
        return;
    }
    CHECK(!::systems::CheckedOutputPwmTester::isSafetyPwmChannel(0U)); // dummyPwm (OpenBSW, static)
    CHECK(::systems::CheckedOutputPwmTester::isSafetyPwmChannel(1U));  // FAN
    CHECK(::systems::CheckedOutputPwmTester::isSafetyPwmChannel(2U));  // VALVE
    CHECK(!::systems::CheckedOutputPwmTester::isSafetyPwmChannel(3U));
    CHECK(!::systems::CheckedOutputPwmTester::isSafetyPwmChannel(0xFFU));
    CHECK(!::systems::CheckedOutputPwmTester::isSafetyPwmChannel(65537U));

    ::systems::CheckedOutputPwmTester tester;
    Plain plain;
    struct Case
    {
        char const* arguments;
        char const* line; // the one line the wrapper answers
    };
    char const* const fan   = "1 : FAN -> refused: channel of a PWM group of the safe outputs table, "
                              "driven by its function only\r\n";
    char const* const valve = "2 : VALVE -> refused: channel of a PWM group of the safe outputs table, "
                              "driven by its function only\r\n";
    char const* const all   = "255 : all -> refused: channel of a PWM group of the safe outputs table, "
                              "driven by its function only\r\n";
    Case const refused[] = {
        {"set 1 5000", fan},
        {"set 1 0", fan}, // duty 0 too: it unmasks the pair (high side off, low side on), PLAT-110
        {"set 2 10000", valve},
        {"set 1 20000", fan},
        {"set 0x1 0x10", fan},
        {"set 0X2 1", valve},
        {"set 01 010", fan},
        {"set 002 0", valve},
        {"set +2 +1", valve},
        {"SET 1 1", fan},
        {"set   2    3", valve},
        {"set 0xFF 100", all},
        {"set 255 0", all},
        {"set 0377 0", all},
        {"setPeriod 1 1000", fan},
        {"setPeriod 2 70000", valve},
        {"setperiod 1 1", fan},
        {"setPeriod 0x2 0", valve},
    };
    for (Case const& c : refused)
    {
        Run const run      = execute(tester, c.arguments);
        Run const original = execute(plain, c.arguments);
        CHECK(!original.calls.empty()); // OpenBSW writes the group (the finding)
        CHECK(run.calls.empty());
        CHECK(run.result == Result::BAD_VALUE);
        CHECK(run.text == c.line);
        if (!run.calls.empty() || (run.result != Result::BAD_VALUE) || (run.text != c.line))
        {
            std::printf(
                "    arguments \"%s\": result %d, %u calls, \"%s\"\n",
                c.arguments,
                static_cast<int>(run.result),
                static_cast<unsigned>(run.calls.size()),
                run.text.c_str());
        }
    }
    // the order of the checks: an invalid line or a channel out of range answers as before (no text), also where
    // the number would alias a group channel in OpenBSW (65537 = 1 in uint16_t)
    Run const alias = execute(tester, "setPeriod 65537 1000");
    CHECK(alias.calls.empty() && (alias.result == Result::BAD_VALUE) && alias.text.empty());
    Run const incomplete = execute(tester, "set 1");
    CHECK(incomplete.calls.empty() && (incomplete.result == Result::BAD_TOKEN) && incomplete.text.empty());
    // the dummy channel next to the group still works
    Run const dummy = execute(tester, "set 0 1");
    CHECK((dummy.result == Result::OK) && hasCall(dummy, 'd', 0, 1) && (dummy.calls.size() == 1U));
}

void testIds()
{
    ::systems::CheckedOutputPwmTester tester;
    CHECK(std::string(tester.getId()) == "pwm");
    CHECK(::systems::CheckedOutputPwmTester::SET_COMMAND == 2U);
    CHECK(::systems::CheckedOutputPwmTester::SET_PERIOD_COMMAND == 3U);
}
} // namespace

int main()
{
    (void)std::setvbuf(stdout, nullptr, _IONBF, 0);
    testFindingsOfOpenBsw();
    testRefused();
    testEverythingValidAsOpenBsw();
    testSafetyPwmGroupRefused();
    testIds();
    std::printf(
        "checked pwm tester (PLAT-110, SYS-162, profile %s): %d checks, %d failures\n",
        WITH_GROUP ? "irrigation_ctrl" : "dosing_unit",
        checks,
        failures);
    return (failures == 0) ? 0 : 1;
}
