/* Host test of the identification at run time (PLAT-98), with its own test profile - an invented project that is
 * not the eval board, so nothing here can pass because of a value of the demo.
 *
 * Board finding 2026-09-23 (PLAT-08, capture 2026-09-23_board/02): every log line started with "RefApp", the
 * application name of the OpenBSW demo, 180 times in one start-up log. Since PLAT-98 the name comes from the build
 * (project.name of project.yaml, ecu/consoleCommands/CMakeLists.txt -> identity::LOG_NAME), and the console has
 * "version" and "info".
 *
 * The profile below plays the part of ecu/consoleCommands/CMakeLists.txt: it defines the three build values and
 * compiles the real Identity.cpp with them. Parts:
 *  1. The build values reach the identification unchanged (application, neXtBSW version, board/derivative).
 *  2. The log prefix: the REAL OpenBSW ConsoleEntryFormatter (loggerIntegration, the formatter of the logger
 *     composition in ecu/application/src/logger) with identity::LOG_NAME prints "<time>: <project>: <component>..."
 *     - the invented project name, no "RefApp".
 *  3. logNameOf(): a name up to LOG_NAME_MAX_CHARS stays whole, a longer one is cut there (prefix only).
 *  4. "version": one line, application, bootloader, neXtBSW, board in this order; a missing bootloader is
 *     "unknown"; a generated field (PLAT-79 extension point) with versionLine appears in the line, one without only
 *     in "info".
 *  5. "info": one entry per line, name column, same order.
 *  6. bootloaderText(): stops at NUL and at non-printable bytes, cuts at BOOTLOADER_TEXT_MAX_CHARS.
 *  7. Deliberately inserted faults: the formatter with the old fixed name "RefApp" fails the check of part 2; a
 *     version line without the run-time field fails the order check of part 4.
 *
 * Build and run (from the repository root; tools/test -k identity does the same):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Werror -Iecu/consoleCommands/include \
 *       -isystem external/openbsw/libs/bsw/logger/include -isystem external/openbsw/libs/bsw/loggerIntegration/include \
 *       -isystem external/openbsw/libs/bsw/util/include -isystem external/openbsw/libs/3rdparty/etl/include \
 *       ecu/consoleCommands/test/host/IdentityHostTest.cpp <util sources, see tools/test/tests.yaml> -o identity.exe
 */

// Test profile: an invented customer project (ecu/consoleCommands/CMakeLists.txt defines these from nxconfig)
#define NX_IDENTITY_APPLICATION     "heatpump_ctrl_b7"
#define NX_IDENTITY_NEXTBSW_VERSION "9.8.7"
#define NX_IDENTITY_BOARD           "hp_mainboard_r3/S32K116"
// PLAT-93: what diag/DiagBuildId.h (tools/diaggen/buildid.py) defines for the same build: commit of the neXtBSW tree
// and commit of the customer workspace; Identity.cpp shows both in "info" (not in the line of "version")
#define NX_DIAG_NEXTBSW_COMMIT      "v9.8.7-3-gabc1234"
#define NX_DIAG_WORKSPACE_VERSION   "hp-v2.1-7-g00ff11a-dirty+1a2b3c4d"

#include "../../src/identity/Identity.cpp"

#include "identity/IdentityText.h"

#include <logger/ConsoleEntryFormatter.h>
#include <util/format/PrintfArgumentReader.h>
#include <util/logger/ComponentInfo.h>
#include <util/logger/LevelInfo.h>

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
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

bool startsWith(std::string const& text, std::string const& prefix) { return text.compare(0, prefix.size(), prefix) == 0; }

/* ---------------------------------------------------------------- logger model */

class CaptureStream : public ::util::stream::IOutputStream
{
public:
    bool isEof() const override { return false; }

    void write(uint8_t const data) override { text.push_back(static_cast<char>(data)); }

    void write(::etl::span<uint8_t const> const& buffer) override
    {
        text.append(reinterpret_cast<char const*>(buffer.data()), buffer.size());
    }

    std::string text;
};

/** Timestamp as the application prints it (DefaultLoggerTime "%u"), without a clock. */
class FixedTime : public ::logger::ILoggerTime<uint32_t>
{
public:
    uint32_t getTimestamp() const override { return 12345U; }

    void formatTimestamp(::util::stream::IOutputStream& stream, uint32_t const& timestamp) const override
    {
        char text[16];
        int const n = std::snprintf(text, sizeof(text), "%u", static_cast<unsigned>(timestamp));
        stream.write(::etl::span<uint8_t const>(reinterpret_cast<uint8_t const*>(text), static_cast<size_t>(n)));
    }
};

::util::logger::ComponentInfo::PlainInfo const CONSOLE_COMPONENT = {{"CONSOLE", {}}};

std::string formatLine(char const* const name, char const* const format, ...)
{
    FixedTime time;
    ::logger::ConsoleEntryFormatter<uint32_t, uint32_t> formatter(time, name);
    CaptureStream stream;
    ::util::logger::ComponentInfo const component(0U, &CONSOLE_COMPONENT);
    ::util::logger::LevelInfo const level(&::util::logger::LevelInfo::getDefaultTable()[2]);
    va_list ap;
    va_start(ap, format);
    ::util::format::PrintfArgumentReader reader(ap);
    formatter.formatEntry(stream, 0U, 12345U, component, level, format, reader);
    va_end(ap);
    return stream.text;
}

/* ---------------------------------------------------------------- text model */

struct StringOut
{
    void write(char const* const text) { value += text; }

    std::string value;
};

std::string versionLine(::etl::span<::identity::Field const> build, ::etl::span<::identity::Field const> runtime)
{
    StringOut out;
    ::identity::writeVersionLine(out, build, runtime);
    return out.value;
}

std::string info(::etl::span<::identity::Field const> build, ::etl::span<::identity::Field const> runtime)
{
    StringOut out;
    ::identity::writeInfo(out, build, runtime);
    return out.value;
}

/* ---------------------------------------------------------------- parts */

void testBuildValues()
{
    std::printf("1. build values\n");
    CHECK(std::strcmp(::identity::APPLICATION_NAME, "heatpump_ctrl_b7") == 0);
    CHECK(std::strcmp(::identity::LOG_NAME, "heatpump_ctrl_b7") == 0);
    CHECK(std::strcmp(::identity::NEXTBSW_VERSION, "9.8.7") == 0);
    CHECK(std::strcmp(::identity::BOARD, "hp_mainboard_r3/S32K116") == 0);
    auto const fields = ::identity::buildFields();
    CHECK(fields.size() == 5U);
    CHECK(std::strcmp(fields[0].name, "application") == 0);
    CHECK(fields[0].value == ::identity::APPLICATION_NAME);
    // PLAT-93: the commits come from the build header, behind the neXtBSW version, outside the version line
    CHECK(std::strcmp(fields[2].name, "commit") == 0);
    CHECK(std::strcmp(fields[2].value, "v9.8.7-3-gabc1234") == 0);
    CHECK(!fields[2].versionLine);
    CHECK(std::strcmp(fields[3].name, "workspace") == 0);
    CHECK(std::strcmp(fields[3].value, "hp-v2.1-7-g00ff11a-dirty+1a2b3c4d") == 0);
    CHECK(!fields[3].versionLine);
}

void testLogPrefix()
{
    std::printf("2. log prefix through the OpenBSW formatter\n");
    std::string const line = formatLine(::identity::LOG_NAME, "Received console command \"%s\"", "version");
    std::printf("   %s\n", line.c_str());
    CHECK(startsWith(line, "12345: heatpump_ctrl_b7: "));
    CHECK(line.find("RefApp") == std::string::npos);
    CHECK(line.find("CONSOLE") != std::string::npos);
    CHECK(line.find("Received console command \"version\"") != std::string::npos);
}

void testLogNameCut()
{
    std::printf("3. log name cut to %u characters\n", static_cast<unsigned>(::identity::LOG_NAME_MAX_CHARS));
    constexpr auto shortName = ::identity::logNameOf("abc");
    CHECK(std::strcmp(shortName.text, "abc") == 0);
    constexpr auto exact = ::identity::logNameOf("abcdefghijklmnopqrstuvwx"); // 24
    CHECK(std::strcmp(exact.text, "abcdefghijklmnopqrstuvwx") == 0);
    constexpr auto longName = ::identity::logNameOf("customer_project_with_a_long_name_0123456789");
    CHECK(std::strlen(longName.text) == ::identity::LOG_NAME_MAX_CHARS);
    CHECK(std::strcmp(longName.text, "customer_project_with_a_") == 0);
    // compile time: usable as constant initialiser of the logger composition
    static_assert(::identity::logNameOf("x").text[0] == 'x', "logNameOf is constexpr");
    static_assert(::identity::logNameOf("x").text[1] == '\0', "logNameOf terminates");
    // the demo name (22 characters) stays whole
    CHECK(std::strcmp(::identity::logNameOf("evalboard_s32k144_demo").text, "evalboard_s32k144_demo") == 0);
}

void testVersionLine()
{
    std::printf("4. version line\n");
    ::identity::Field const runtime[] = {{"bootloader", "nextbsw-bl 1.4.2", true}};
    std::string const line = versionLine(::identity::buildFields(), runtime);
    std::printf("   %s", line.c_str());
    CHECK(
        line
        == "application heatpump_ctrl_b7, bootloader nextbsw-bl 1.4.2, neXtBSW 9.8.7, board "
           "hp_mainboard_r3/S32K116\n");
    CHECK(line.find('\n') == line.size() - 1U); // one line

    char unknown[::identity::BOOTLOADER_TEXT_MAX_CHARS + 1U];
    ::identity::bootloaderText(::etl::span<uint8_t const>(), unknown);
    ::identity::Field const noBootloader[] = {{"bootloader", unknown, true}};
    CHECK(versionLine(::identity::buildFields(), noBootloader).find("bootloader unknown, neXtBSW") != std::string::npos);

    // PLAT-79 extension point: generated fields appended to the build fields
    ::identity::Field const extended[]
        = {{"application", "a", true},
           {"neXtBSW", "1.0.0", true},
           {"board", "b/d", true},
           {"configuration", "sha256:0123abcd", true},
           {"generators", "bswgen 3, rtegen 5", false}};
    std::string const extendedLine = versionLine(extended, runtime);
    CHECK(
        extendedLine
        == "application a, bootloader nextbsw-bl 1.4.2, neXtBSW 1.0.0, board b/d, configuration sha256:0123abcd\n");
    CHECK(extendedLine.find("generators") == std::string::npos);
    CHECK(info(extended, runtime).find("generators     bswgen 3, rtegen 5\n") != std::string::npos);
}

void testInfo()
{
    std::printf("5. info\n");
    ::identity::Field const runtime[] = {{"bootloader", "nextbsw-bl 1.4.2", true}};
    std::string const text = info(::identity::buildFields(), runtime);
    std::printf("%s", text.c_str());
    CHECK(
        text
        == "application    heatpump_ctrl_b7\n"
           "bootloader     nextbsw-bl 1.4.2\n"
           "neXtBSW        9.8.7\n"
           "commit         v9.8.7-3-gabc1234\n"
           "workspace      hp-v2.1-7-g00ff11a-dirty+1a2b3c4d\n"
           "board          hp_mainboard_r3/S32K116\n");
    // a name as long as the column still gets a blank
    ::identity::Field const longName[] = {{"abcdefghijklmnopq", "v", false}};
    CHECK(info(longName, ::etl::span<::identity::Field const>()) == "abcdefghijklmnopq v\n");
    // a missing value prints "?"
    ::identity::Field const noValue[] = {{"x", nullptr, true}};
    CHECK(versionLine(noValue, ::etl::span<::identity::Field const>()) == "x ?\n");
}

void testBootloaderText()
{
    std::printf("6. bootloader text from flash\n");
    char out[::identity::BOOTLOADER_TEXT_MAX_CHARS + 1U];
    uint8_t const terminated[] = {'n', 'x', '-', 'b', 'l', ' ', '1', '.', '0', 0U, 'X', 'Y'};
    ::identity::bootloaderText(terminated, out);
    CHECK(std::strcmp(out, "nx-bl 1.0") == 0);
    uint8_t const binary[] = {'v', '1', 0xFFU, 'z'};
    ::identity::bootloaderText(binary, out);
    CHECK(std::strcmp(out, "v1") == 0);
    uint8_t erased[64];
    std::memset(erased, 0xFF, sizeof(erased));
    ::identity::bootloaderText(erased, out);
    CHECK(std::strcmp(out, "unknown") == 0);
    uint8_t longText[64];
    std::memset(longText, 'a', sizeof(longText));
    ::identity::bootloaderText(longText, out);
    CHECK(std::strlen(out) == ::identity::BOOTLOADER_TEXT_MAX_CHARS);
}

void testInsertedFaults()
{
    std::printf("7. inserted faults are found\n");
    int const before = failures;
    // (a) the state before PLAT-98: fixed name "RefApp"
    std::string const old = formatLine("RefApp", "x");
    bool const oldPasses  = startsWith(old, "12345: heatpump_ctrl_b7: ") && (old.find("RefApp") == std::string::npos);
    CHECK(!oldPasses);
    // (b) version line without the run-time field: order check of part 4 fails
    std::string const noRuntime = versionLine(::identity::buildFields(), ::etl::span<::identity::Field const>());
    CHECK(noRuntime.find("bootloader") == std::string::npos);
    CHECK(failures == before);
}
} // namespace

int main()
{
    testBuildValues();
    testLogPrefix();
    testLogNameCut();
    testVersionLine();
    testInfo();
    testBootloaderText();
    testInsertedFaults();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
