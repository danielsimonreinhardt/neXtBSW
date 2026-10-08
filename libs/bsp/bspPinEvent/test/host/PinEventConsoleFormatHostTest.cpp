// SPDX-License-Identifier: Apache-2.0
// Console text of "pinevent show/config" through the real OpenBSW formatter with the target's argument layout
// (IO-60). PinEventHostTest.cpp checks the same text with the C library, which reads "%lu" as a long of the
// platform - that could not see the board fault of IO-54 (2026-09-29): util::format reads 'l' as 64 bit, the
// 32-bit target passes 32 bit, every following argument shifted ("debounce 85899347920 us", "qlf <NULL>").
// Parts:
//   1. the target argument model (tools/fmtcheck/include/fmtcheck/TargetPrintf.h) against known cases: %u/%x/%s,
//      %llu with a real 64-bit value, a null string, a read past the arguments (stack fill);
//   2. the former format lines reproduce the board text exactly and are reported as mismatching;
//   3. writeShow/writeConfig of the module: complete, exact text, every conversion matches its argument.

#include "pinEvent/PinEventConsole.h"

#include <fmtcheck/TargetPrintf.h>

#include <cstdio>
#include <string>

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

#define CHECK_TEXT(actual, expected)                                                                     \
    do                                                                                                   \
    {                                                                                                    \
        ++checks;                                                                                        \
        if ((actual) != std::string(expected))                                                           \
        {                                                                                                \
            ++failures;                                                                                  \
            std::printf("FAIL %s:%d:\n  got  \"%s\"\n  want \"%s\"\n", __FILE__, __LINE__, (actual).c_str(), \
                        std::string(expected).c_str());                                                 \
        }                                                                                                \
    } while (0)

using namespace pinevent;
using ::fmtcheck::TargetPrintf;

uint32_t nowUs = 0U;

uint32_t clockUs() { return nowUs; }

class FakeHardware : public IPinEventHardware
{
public:
    bool level(uint8_t const) const override { return false; }

    void arm(uint8_t const, Detect const) override {}
};

void printProblems(TargetPrintf const& out)
{
    for (std::string const& p : out.problems)
    {
        std::printf("  mismatch: %s\n", p.c_str());
    }
}

// ------------------------------------------------------------------------------------------ 1. argument model
void testModel()
{
    TargetPrintf out;
    (void)out.printf("%u %x %s|", static_cast<uint32_t>(4000000000U), static_cast<uint32_t>(0xBEEFU), "txt");
    (void)out.printf("%llu %u|", static_cast<unsigned long long>(0x123456789ULL), static_cast<uint32_t>(7U));
    // first variadic slot 2 (member printf): a 64-bit value after one word is aligned to slot 4
    (void)out.printf("%u %llx %u|", static_cast<uint32_t>(1U), static_cast<uint64_t>(0xA00000000BULL),
                     static_cast<uint32_t>(2U));
    (void)out.printf("%s|", static_cast<char const*>(nullptr));
    (void)out.printf("%u %d|", static_cast<uint8_t>(200U), static_cast<int16_t>(-5));
    CHECK_TEXT(out.text, "4000000000 beef txt|4886718345 7|1 a00000000b 2|<NULL>|200 -5|");
    CHECK(out.problems.empty());
    printProblems(out);

    TargetPrintf past;
    (void)past.printf("%u %x", static_cast<uint32_t>(3U));
    CHECK_TEXT(past.text, "3 a5a5a5a5");
    CHECK(past.problems.size() == 1U);
}

// ------------------------------------------------------------------ 2. the former lines give the board text
void testFormerLinesReproduceBoard()
{
    // "pinevent config" before IO-60, DOOR-like input: debounce 2000 us, 20 events per 1 s, re-arm after 5 s
    TargetPrintf config;
    (void)config.printf(
        "%s: edge %s, debounce %lu us, limit %lu per %lu us, re-arm after %lu us, detector %s\r\n",
        "IN",
        "activate",
        static_cast<unsigned long>(2000U),
        static_cast<unsigned long>(20U),
        static_cast<unsigned long>(1000000U),
        static_cast<unsigned long>(5000000U),
        "both");
    // board 2026-09-29 (IO-54): "debounce 85899347920 us" = 20 << 32 | 2000
    CHECK(config.text.find("debounce 85899347920 us") != std::string::npos);
    CHECK(config.problems.size() == 1U);
    std::printf("  former config line: %s", config.text.c_str());

    // "pinevent show" before IO-60: the qualifier string lands on the bounces slot (0) - "qlf <NULL>"
    TargetPrintf show;
    (void)show.printf(
        "%s: count %lu, last %lu us, qlf %s (%u), trips %lu, bounces %lu, glitches %lu, ",
        "IN",
        static_cast<unsigned long>(3U),
        static_cast<unsigned long>(5000U),
        "OK",
        static_cast<unsigned>(1U),
        static_cast<unsigned long>(0U),
        static_cast<unsigned long>(0U),
        static_cast<unsigned long>(0U));
    CHECK(show.text.find("qlf <NULL>") != std::string::npos);
    CHECK(show.problems.size() == 1U);
    std::printf("  former show line:   %s\n", show.text.c_str());
}

// -------------------------------------------------------------------------------- 3. the module's console text
void testConsoleText()
{
    nowUs = 1000U;
    PinEventConfig const configs[] = {
        {"DOOR", Edge::ACTIVATE, 2000U, 20U, 1000000U, 5000000U},
        {"PULSE", Edge::BOTH, 0U, 2U, 10000U, 250000U},
    };
    PinEvent inputs[] = {PinEvent(configs[0]), PinEvent(configs[1])};
    FakeHardware hw;
    PinEvents table(inputs, 2U, hw, &clockUs);
    table.start();
    nowUs = 5000U;
    CHECK(table.onEdge(0U, nowUs, true));
    CHECK(table.onEdge(1U, nowUs, true));
    CHECK(table.onEdge(1U, nowUs + 1U, false));
    CHECK(!table.onEdge(1U, nowUs + 2U, true));
    nowUs = 10000U;

    TargetPrintf show;
    console::writeShow(show, table);
    CHECK_TEXT(show.text,
               "pin events: 2\r\n"
               "DOOR: count 1, last 5000 us, qlf OK (1), trips 0, bounces 0, glitches 0, armed\r\n"
               "PULSE: count 2, last 5001 us, qlf ERROR (16), trips 1, bounces 0, glitches 0, off, re-arm in 246 ms\r\n");
    CHECK(show.problems.empty());
    printProblems(show);

    TargetPrintf config;
    console::writeConfig(config, table);
    CHECK_TEXT(config.text,
               "DOOR: edge activate, debounce 2000 us, limit 20 per 1000000 us, re-arm after 5000000 us, "
               "detector both\r\n"
               "PULSE: edge both, debounce 0 us, limit 2 per 10000 us, re-arm after 250000 us, detector both\r\n");
    CHECK(config.problems.empty());
    printProblems(config);

    // the largest values of the configuration rule and counters at their end fit and print unchanged
    PinEventConfig const big[] = {{"MAX", Edge::DEACTIVATE, MAX_DEBOUNCE_US, 59U, MAX_WINDOW_US, MAX_REARM_US}};
    PinEvent bigInputs[] = {PinEvent(big[0])};
    PinEvents bigTable(bigInputs, 1U, hw, &clockUs);
    TargetPrintf bigConfig;
    console::writeConfig(bigConfig, bigTable);
    CHECK_TEXT(bigConfig.text,
               "MAX: edge deactivate, debounce 1000000 us, limit 59 per 60000000 us, re-arm after 600000000 us, "
               "detector both\r\n");
    CHECK(bigConfig.problems.empty());
    std::printf("  show:   %s", show.text.c_str());
    std::printf("  config: %s", config.text.c_str());
}

} // namespace

int main()
{
    testModel();
    testFormerLinesReproduceBoard();
    testConsoleText();
    std::printf("PinEventConsoleFormatHostTest: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
