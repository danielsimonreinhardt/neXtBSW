/* SYS-163 (safety review group A 2026-10-01, SR-110-2 a): host test of the number argument without wrap-around
 * (consoleCommands/CheckedNumber.h) and of "lc level" (LifecycleControlCommand.cpp).
 *
 * Real code: consolecommands::scanUnsigned, lifecycle::LifecycleControlCommand (development build, with "level") and
 * the OpenBSW command framework (GroupCommand, CommandContext). The lifecycle manager is a recorder with 8 levels
 * (own test profile: no level count of a real project), "lc bootloader" is stubbed (lcStubs).
 *
 * What it shows:
 *   - the finding of PLAT-110 on the code before SYS-163 (scanIntToken<uint8_t>, result not looked at): "lc level",
 *     "lc level x" and "lc level 256" run a transition to level 0 although the console answers "error"; the same
 *     scanner reads "dem setting 256" as 0 (DTC setting off);
 *   - scanUnsigned accepts exactly the spellings of OpenBSW's scanIntToken (decimal, 0x/0X hex, leading 0 octal,
 *     '+'/'-' with decimal digits) and gives the same value for every number in range;
 *   - a missing token or no number: BAD_TOKEN; a number above the limit with any number of digits, or a negative one:
 *     BAD_VALUE; the value stays unchanged;
 *   - "lc level" runs a transition only for a complete line with a level from 0 to the level count, every other line
 *     answers "error" without a transition; the limit 0/1 of "dem setting" behaves the same.
 *
 * Build and run (zig c++, no hardware): tools/test -k console-number
 */

#include "consoleCommands/CheckedNumber.h"
#include "lifecycle/console/LifecycleControlCommand.h"

#include "lifecycle/ILifecycleManager.h"
#include "util/command/CommandContext.h"
#include "util/stream/SharedOutputStream.h"
#include "util/string/ConstString.h"

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

using Result = ::util::command::ICommand::Result;
using ::util::command::CommandContext;
using ::util::string::ConstString;

class Recorder : public ::lifecycle::ILifecycleManager
{
public:
    uint8_t getLevelCount() const override { return 8U; }

    void transitionToLevel(uint8_t const level) override { transitions.push_back(static_cast<int>(level)); }

    void addLifecycleListener(::lifecycle::ILifecycleListener&) override {}

    void removeLifecycleListener(::lifecycle::ILifecycleListener&) override {}

    std::vector<int> transitions;
};

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

struct Run
{
    Result result;
    std::vector<int> transitions;
};

Run execute(Recorder& manager, char const* const arguments)
{
    manager.transitions.clear();
    ::lifecycle::LifecycleControlCommand command(manager);
    TextStream text;
    ::util::stream::SharedOutputStream shared(text);
    ::util::command::ICommand::ExecuteResult const result = command.execute(ConstString(arguments), &shared);
    return Run{result.getResult(), manager.transitions};
}

/* "lc level" before SYS-163, as it was in LifecycleControlCommand.cpp (the finding of PLAT-110). */
void oldLevel(CommandContext& context, ::lifecycle::ILifecycleManager& manager)
{
    uint8_t level = context.scanIntToken<uint8_t>();
    if (level <= manager.getLevelCount())
    {
        manager.transitionToLevel(level);
    }
    else
    {
        (void)context.check(false);
    }
}

struct Scan
{
    bool ok;
    uint32_t value;
    Result result;
    std::string rest; // what the context has left after the token
};

Scan checked(char const* const line, uint32_t const max)
{
    CommandContext context{ConstString(line)};
    uint32_t value = 0xA5A5A5A5U;
    bool const ok  = ::consolecommands::scanUnsigned(context, max, value);
    ConstString const rest = context.getSuffix();
    return Scan{ok, value, context.getResult(), std::string(rest.data(), rest.length())};
}

Scan openbsw(char const* const line)
{
    CommandContext context{ConstString(line)};
    uint32_t const value   = context.scanIntToken<uint32_t>();
    ConstString const rest = context.getSuffix();
    return Scan{context.getResult() == Result::OK, value, context.getResult(), std::string(rest.data(), rest.length())};
}
} // namespace

extern "C" void softwareSystemReset(void) {}

void bl_set_boot_request(void) {}

namespace
{
void testFindingBeforeSys163()
{
    Recorder manager;
    for (char const* const line : {"", "x", "256", "0x100"})
    {
        manager.transitions.clear();
        CommandContext context{ConstString(line)};
        oldLevel(context, manager);
        // "error" for the empty and the invalid line, and still the transition to level 0
        CHECK((manager.transitions.size() == 1U) && (manager.transitions[0] == 0));
    }
    CommandContext dem{ConstString("256")};
    CHECK(dem.scanIntToken<uint8_t>() == 0U); // dem setting 256 = off
    CHECK(dem.getResult() == Result::OK);
}

void testSameSpellingsAsOpenBsw()
{
    char const* const lines[] = {
        "0",     "1",     "7",          "8",          "9",     "10",         "255",        "00",        "007",
        "010",   "0377",  "0x0",        "0x1",        "0X1f",  "0xFF",       "0xff",       "0x00000001", "+0",
        "+1",    "+255",  "-0",         "4294967295", "0xFFFFFFFF", "037777777777", "1 rest", "0x10 rest", "3\t",
        "42\r\n"};
    for (char const* const line : lines)
    {
        Scan const ours = checked(line, 0xFFFFFFFFU);
        Scan const ref  = openbsw(line);
        CHECK(ours.ok && ref.ok);
        CHECK(ours.value == ref.value);
        CHECK(ours.rest == ref.rest);
        if (!ours.ok || (ours.value != ref.value) || (ours.rest != ref.rest))
        {
            std::printf(
                "    \"%s\": ours %d %u \"%s\", OpenBSW %d %u \"%s\"\n",
                line,
                static_cast<int>(ours.result),
                static_cast<unsigned>(ours.value),
                ours.rest.c_str(),
                static_cast<int>(ref.result),
                static_cast<unsigned>(ref.value),
                ref.rest.c_str());
        }
    }
    // every number 0..1000 in every spelling, against a limit that cuts at 300
    char line[32];
    for (unsigned n = 0U; n <= 1000U; ++n)
    {
        for (char const* const format : {"%u", "0x%x", "0X%X", "0%o", "+%u"})
        {
            (void)std::snprintf(line, sizeof(line), format, n);
            Scan const ours = checked(line, 300U);
            Scan const ref  = openbsw(line);
            CHECK(ref.ok && (ref.value == n));
            if (n <= 300U)
            {
                CHECK(ours.ok && (ours.value == n) && (ours.result == Result::OK));
            }
            else
            {
                CHECK(!ours.ok && (ours.result == Result::BAD_VALUE) && (ours.value == 0xA5A5A5A5U));
            }
        }
    }
}

void testRefused()
{
    struct Case
    {
        char const* line;
        uint32_t max;
        Result result;
    };
    Case const cases[] = {
        {"", 8U, Result::BAD_TOKEN},
        {"   ", 8U, Result::BAD_TOKEN},
        {"x", 8U, Result::BAD_TOKEN},
        {"1x", 8U, Result::BAD_TOKEN},
        {"0x", 8U, Result::BAD_TOKEN},
        {"0xg", 8U, Result::BAD_TOKEN},
        {"08", 8U, Result::BAD_TOKEN}, // 8 is no octal digit (OpenBSW: BAD_TOKEN as well)
        {"-", 8U, Result::BAD_TOKEN},
        {"+", 8U, Result::BAD_TOKEN},
        {"-0x1", 8U, Result::BAD_TOKEN},
        {"1.5", 8U, Result::BAD_TOKEN},
        {"9", 8U, Result::BAD_VALUE},
        {"256", 8U, Result::BAD_VALUE},
        {"256", 255U, Result::BAD_VALUE},
        {"0x100", 255U, Result::BAD_VALUE},
        {"0400", 255U, Result::BAD_VALUE},
        {"-1", 8U, Result::BAD_VALUE},
        {"-256", 255U, Result::BAD_VALUE}, // OpenBSW uint8_t: 0
        {"-255", 255U, Result::BAD_VALUE}, // OpenBSW uint8_t: 1
        {"4294967296", 0xFFFFFFFFU, Result::BAD_VALUE}, // OpenBSW uint32_t: 0
        {"4294967297", 8U, Result::BAD_VALUE},           // OpenBSW uint32_t: 1
        {"0x100000000", 0xFFFFFFFFU, Result::BAD_VALUE},
        {"040000000000", 0xFFFFFFFFU, Result::BAD_VALUE},
        {"99999999999999999999999999999999", 0xFFFFFFFFU, Result::BAD_VALUE},
        {"18446744073709551616", 8U, Result::BAD_VALUE}, // 2^64: 0 in any integer type of OpenBSW
        {"2", 1U, Result::BAD_VALUE},                     // dem setting
        {"256", 1U, Result::BAD_VALUE},
        {"257", 1U, Result::BAD_VALUE},
        {"1", 0U, Result::BAD_VALUE},
    };
    for (Case const& c : cases)
    {
        Scan const s = checked(c.line, c.max);
        CHECK(!s.ok);
        CHECK(s.result == c.result);
        CHECK(s.value == 0xA5A5A5A5U); // unchanged
        if (s.ok || (s.result != c.result))
        {
            std::printf("    \"%s\" (max %u): ok %d, result %d\n", c.line, c.max, s.ok ? 1 : 0, static_cast<int>(s.result));
        }
    }
    // the limits themselves
    CHECK(checked("0", 0U).ok && (checked("0", 0U).value == 0U));
    CHECK(checked("1", 1U).ok && (checked("1", 1U).value == 1U));
    CHECK(checked("0", 1U).ok && (checked("0", 1U).value == 0U));
    CHECK(checked("4294967295", 0xFFFFFFFFU).ok);
    CHECK(checked("8", 8U).ok && (checked("8", 8U).value == 8U));
    // an earlier error stays and nothing is read
    CommandContext context{ConstString("x 1")};
    (void)context.scanIntToken<uint32_t>();
    uint32_t value = 7U;
    CHECK(!::consolecommands::scanUnsigned(context, 8U, value) && (value == 7U));
    CHECK(context.getResult() == Result::BAD_TOKEN);
}

void testLcLevel()
{
    Recorder manager;
    char const* const refused[] = {
        "level",
        "level x",
        "level 256",
        "level 0x100",
        "level 9",
        "level 264", // 256 + 8
        "level 4294967296",
        "level 4294967297",
        "level -1",
        "level -256",
        "level 1.5",
        "level 0x",
        "level 3 x",
        "level 3 4",
        "level 08",
    };
    for (char const* const line : refused)
    {
        Run const run = execute(manager, line);
        CHECK(run.transitions.empty());
        CHECK(run.result != Result::OK);
        if (!run.transitions.empty() || (run.result == Result::OK))
        {
            std::printf(
                "    \"%s\": result %d, %u transitions\n",
                line,
                static_cast<int>(run.result),
                static_cast<unsigned>(run.transitions.size()));
        }
    }
    struct Valid
    {
        char const* line;
        int level;
    };
    Valid const valid[] = {
        {"level 0", 0},
        {"level 8", 8},
        {"level 3", 3},
        {"level 0x3", 3},
        {"level 03", 3},
        {"level +3", 3},
        {"level -0", 0},
        {"LEVEL 5", 5},
        {"level   7  ", 7},
    };
    for (Valid const& v : valid)
    {
        Run const run = execute(manager, v.line);
        CHECK(run.result == Result::OK);
        CHECK((run.transitions.size() == 1U) && (run.transitions[0] == v.level));
        if ((run.result != Result::OK) || (run.transitions.size() != 1U) || (run.transitions[0] != v.level))
        {
            std::printf("    \"%s\": result %d\n", v.line, static_cast<int>(run.result));
        }
    }
    // the other sub commands are unchanged
    Run const reboot = execute(manager, "reboot");
    CHECK((reboot.result == Result::OK) && (reboot.transitions.size() == 1U) && (reboot.transitions[0] == 0));
    Run const assertion = execute(manager, "udef");
    CHECK((assertion.result == Result::OK) && assertion.transitions.empty());
}
} // namespace

int main()
{
    (void)std::setvbuf(stdout, nullptr, _IONBF, 0);
    testFindingBeforeSys163();
    testSameSpellingsAsOpenBsw();
    testRefused();
    testLcLevel();
    std::printf("console number (SYS-163): %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
