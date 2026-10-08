/* SYS-150 (safety review night 2026-10-01, SR-147-2 a): host test of the console group "out" of the family port -
 * `out set` refuses every safe_off output, everything else is the OpenBSW tester.
 *
 * Real code: systems::SafeOffOutputTester, the real OpenBSW bios::OutputTester (OutputTester.cpp) and command
 * framework (GroupCommand, CommandContext, SharedStringWriter, printf formatter), integration/SafeOffOutputs.h with
 * the own test profile of safeOffStubs (invented project "irrigation_ctrl": safe_off outputs PUMP_EN and HEATER_N, an
 * ordinary output FAN that shares its name with the PWM group of the table). The output manager is the stub; its set()
 * records every call. The command is executed as the console does: GroupCommand::execute(<arguments>, stream).
 *
 * What it shows:
 *   - `set` of a safe_off output is refused for 1 and 0, for every spelling of the id the OpenBSW scanner accepts
 *     (decimal, 0x hex, leading-0 octal, '+'), with a line that names the output and the result BAD_VALUE ("error"
 *     on the console) - Output::set is never called;
 *   - `set` of every other output (also FAN), `get` and `all` behave exactly as the unwrapped OpenBSW tester: same
 *     text, same result, same calls - compared output by output against a bios::OutputTester;
 *   - invalid arguments (no id, garbage, an id out of range) end as in the OpenBSW tester;
 *   - an id at or above TOTAL_NUMBER_OF_OUTPUTS is refused on the number itself: OpenBSW casts it to the one-byte
 *     OutputId first (target ABI, built here with -fshort-enums), so `set 259 1` wrote output 3 (shown with the
 *     unwrapped tester) - with the wrapper no id reaches Output::set or Output::get that way.
 * Meaningfulness: integration/test/host/test_safe_off_hold_mutations.py.
 *
 * Build and run (zig c++, no hardware): tools/test -k safe-off-output-tester
 */

#include "systems/SafeOffOutputTester.h"

#include "integration/SafeOffOutputs.h"
#include "outputManager/Output.h"
#include "outputManager/OutputTester.h"
#include "util/stream/SharedOutputStream.h"

#include <cstdio>
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

struct SetCall
{
    int output;
    int value;
};

std::vector<SetCall> setCalls;
bool levels[::bios::Output::NUMBER_OF_INTERNAL_OUTPUTS] = {};

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

class Exposed : public ::bios::OutputTester
{
};

struct Run
{
    ::util::command::ICommand::Result result;
    std::string text;
    std::vector<SetCall> calls;
};

Run execute(::util::command::GroupCommand& command, char const* const arguments)
{
    setCalls.clear();
    TextStream text;
    ::util::stream::SharedOutputStream shared(text);
    ::util::command::ICommand::ExecuteResult const result
        = command.execute(::util::string::ConstString(arguments), &shared);
    return Run{result.getResult(), text.text, setCalls};
}

bool sameCalls(std::vector<SetCall> const& a, std::vector<SetCall> const& b)
{
    if (a.size() != b.size())
    {
        return false;
    }
    for (size_t i = 0U; i < a.size(); ++i)
    {
        if ((a[i].output != b[i].output) || (a[i].value != b[i].value))
        {
            return false;
        }
    }
    return true;
}
} // namespace

namespace bios
{
bsp::BspReturnCode Output::set(OutputId const chan, uint8_t const vol, bool)
{
    setCalls.push_back(SetCall{static_cast<int>(chan), static_cast<int>(vol)});
    if (chan < NUMBER_OF_INTERNAL_OUTPUTS)
    {
        levels[chan] = (vol != 0U);
        return bsp::BSP_OK;
    }
    return bsp::BSP_ERROR;
}

bsp::BspReturnCode Output::get(OutputId const chan, bool& result)
{
    if (chan < NUMBER_OF_INTERNAL_OUTPUTS)
    {
        result = levels[chan];
        return bsp::BSP_OK;
    }
    return bsp::BSP_ERROR;
}

char const* Output::getName(uint16_t const channel)
{
    static char const* const names[] = {"LED_GREEN", "HEATER_N", "FAN", "PUMP_EN", "BRAKE_LIGHT"};
    return (channel < 5U) ? names[channel] : "lastStatic";
}
} // namespace bios

namespace
{
using Result = ::util::command::ICommand::Result;

void testRefused()
{
    ::systems::SafeOffOutputTester tester;
    // PUMP_EN is 3, HEATER_N is 1 in the output order of the profile; every spelling OpenBSW's scanner reads
    char const* const refused[] = {
        "set 3 1",
        "set 3 0",
        "set 1 1",
        "set 1 0",
        "set 0x3 1",
        "set 0X01 1",
        "set 03 1",
        "set +3 1",
        "set 3 7",
        "SET 3 1",
        "set   1    1",
        "set 3",   // no value: refused before the value is read
        "set 3 1 2"}; // extra token: refused all the same, nothing is written
    for (char const* const arguments : refused)
    {
        Run const run = execute(tester, arguments);
        CHECK(run.calls.empty());
        CHECK(run.result == Result::BAD_VALUE);
        CHECK(run.text.find("refused: safe_off output") != std::string::npos);
        bool const names = (run.text.find("PUMP_EN") != std::string::npos)
                           || (run.text.find("HEATER_N") != std::string::npos);
        CHECK(names);
        if (!run.calls.empty() || (run.result != Result::BAD_VALUE))
        {
            std::printf("    arguments \"%s\": %s", arguments, run.text.c_str());
        }
    }
    Run const run = execute(tester, "set 3 1");
    CHECK(run.text == "3 : PUMP_EN -> refused: safe_off output, switched by its driver only\r\n");
}

void testEverythingElseAsOpenBsw()
{
    ::systems::SafeOffOutputTester tester;
    Exposed plain;
    char const* const same[] = {
        "set 0 1",     "set 0 0",     "set 2 1", // FAN: the name of the PWM group, an ordinary output
        "set 2 0",     "set 4 1",     "set 0x4 0", "set 04 1", "get 0", "get 1", "get 3", "get 4", "all",
        "set 5 1", // out of range
        "set 9 1",     "set",         "set x 1",   "set -1 1", "get",   "unknown", "set 0 1 2"};
    for (char const* const arguments : same)
    {
        for (bool& level : levels)
        {
            level = false;
        }
        Run const wrapped = execute(tester, arguments);
        for (bool& level : levels)
        {
            level = false;
        }
        Run const original = execute(plain, arguments);
        CHECK(wrapped.result == original.result);
        CHECK(wrapped.text == original.text);
        CHECK(sameCalls(wrapped.calls, original.calls));
        if ((wrapped.result != original.result) || (wrapped.text != original.text))
        {
            std::printf(
                "    arguments \"%s\": wrapped \"%s\", OpenBSW \"%s\"\n",
                arguments,
                wrapped.text.c_str(),
                original.text.c_str());
        }
    }
    // a normal output really is written through the wrapper
    Run const run = execute(tester, "set 2 1");
    CHECK(run.calls.size() == 1U && run.calls[0].output == 2 && run.calls[0].value == 1);
    CHECK(run.result == Result::OK);
    // reading a safe_off output stays possible
    levels[::bios::Output::PUMP_EN] = true;
    Run const get = execute(tester, "get 3");
    CHECK(get.text == "3 : PUMP_EN -> 1\r\n");
    CHECK(get.calls.empty());
}

/* Found with this test: OpenBSW casts the id to Output::OutputId before its range check, and the enum is one byte on
 * the target (AAPCS short enums; this test is built with -fshort-enums like arm-none-eabi-gcc). 256 + n reached
 * output n. The wrapper refuses every id at or above TOTAL_NUMBER_OF_OUTPUTS on the number itself. */
void testIdsOutOfRange()
{
    ::systems::SafeOffOutputTester tester;
    Exposed plain;
    static_assert(sizeof(::bios::Output::OutputId) == 1U, "built with the target's enum size (-fshort-enums)");
    Run const alias = execute(plain, "set 259 1");
    CHECK(alias.calls.size() == 1U && alias.calls[0].output == ::bios::Output::PUMP_EN); // the finding
    char const* const outOfRange[] = {
        "set 259 1",
        "set 257 0",
        "set 0x103 1",
        "set 0403 1",
        "set 512 1",
        "set 65539 1",
        "set 4294967295 1",
        "set 6 1",
        "set 255 1",
        "get 259",
        "get 9",
        "get 6"};
    for (char const* const arguments : outOfRange)
    {
        Run const run = execute(tester, arguments);
        CHECK(run.calls.empty());
        CHECK(run.result == Result::BAD_VALUE);
        CHECK(run.text.empty());
        if (!run.calls.empty() || !run.text.empty())
        {
            std::printf("    arguments \"%s\": %s\n", arguments, run.text.c_str());
        }
    }
    // the last valid id still works
    Run const last = execute(tester, "set 4 1");
    CHECK(last.result == Result::OK && last.calls.size() == 1U && last.calls[0].output == 4);
}

void testHelpUnchanged()
{
    ::systems::SafeOffOutputTester tester;
    CHECK(std::string(tester.getId()) == "out");
    CHECK(::systems::SafeOffOutputTester::SET_COMMAND == 2U);
    CHECK(::systems::SafeOffOutputTester::GET_COMMAND == 3U);
}
} // namespace

int main()
{
    (void)std::setvbuf(stdout, nullptr, _IONBF, 0); // a crash keeps every line printed before it
    testRefused();
    testEverythingElseAsOpenBsw();
    testIdsOutOfRange();
    testHelpUnchanged();
    std::printf("safe_off output tester (SYS-150): %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
