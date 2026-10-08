/* SYS-39: host test of the console line of the safe supervisor's hold - the real SafeSupervisor.cpp with its default
 * console path (no replacement of NX_SAFE_SUPERVISOR_CONSOLE), the real formatter (safeSupervisor/SafeConsole.h)
 * and the real transmit loop of the S32K1 family (SafeConsoleWriter.h) on a model of the LPUART transmitter.
 *
 * What it shows (board finding 2026-09-21, SYS-38 capture 46: no line after ROM_CHECK_FAILURE):
 * - the hold writes its line completely, byte for byte, inside handle() with the interrupt lock taken - nothing but
 *   polling of the transmitter's TDRE: no interrupt, no task, no logger, no printf/charIo. The model only lets a
 *   byte out while the caller polls it; there is nothing else that could send it;
 * - the same for both callers (watchdog self-test failure from main(), ROM check from the idle context), and only
 *   after the first walk of the safe outputs table;
 * - a transmitter still busy with the last character of the logger when the hold starts costs no byte (counter-
 *   example: one bsp::Uart::write per byte without waiting first, the charIo path of printf up to SYS-39, drops it);
 * - a dead transmitter delays the hold by one time-out, not one per byte, and the hold still holds (every round:
 *   table walk and watchdog service);
 * - the formatter keeps the end of the line (CR LF) whatever the event name.
 * The glue on the target (safety::safeConsoleWrite() in SafeConsole.cpp: terminal UART of the project, the two
 * record words) is checked in the linked image: ecu/safety/safeSupervisor/test/test_hold_console_elf.py.
 *
 * Build and run (zig c++, no hardware; tools/test -k safe-console):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DPLATFORM_SUPPORT_IO -DPLATFORM_SUPPORT_WATCHDOG \
 *       -Iecu/safety/safeSupervisor/include -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -Iplatforms/s32k1xx/port/main/src/safeConsole \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       platforms/s32k1xx/port/main/src/safeConsole/test/host/SafeConsoleHostTest.cpp -o build/host/safeconsole.exe
 */

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

namespace test_trace
{
std::string trace;
int lockDepth = 0;

void add(char const c) { trace.push_back(c); }
} // namespace test_trace

namespace test_hold
{
constexpr int HOLD_ROUNDS = 3;
int left                  = 0;

bool holding()
{
    if (left <= 0)
    {
        return false;
    }
    --left;
    return true;
}
} // namespace test_hold

#define NX_SAFE_SUPERVISOR_HOLDING() (::test_hold::holding())
// NX_SAFE_SUPERVISOR_CONSOLE deliberately not replaced: the default path of the target is under test

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

#include "../../../../../../../../ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp" // NOLINT
#include "SafeConsoleWriter.h"

namespace safety
{
SafetyEventMailbox rteTimingMailbox;
} // namespace safety

/* ------------------------------------------------------------------ model of the LPUART transmitter */

namespace test_uart
{
/** Polls of STAT after which the time-out of the driver gives up (bsp::Uart: WRITE_TIMEOUT loop iterations). */
constexpr uint32_t TIMEOUT_POLLS = 1000U;

struct Transmitter
{
    bool dead            = false; // TDRE never comes back (clock gone, pin taken)
    uint32_t busyPolls   = 0U;    // polls until TDRE is set again (a character in the shift register)
    uint32_t shiftPolls  = 3U;    // polls one character takes
    int dieAfterBytes    = -1;    // >= 0: the transmitter dies after this many bytes
    uint32_t polls       = 0U;
    std::string sent;
    uint32_t sentUnlocked    = 0U;     // bytes written while the interrupt lock was not taken
    std::string traceAtFirst;          // effects of the supervisor before the first byte
    bool firstSeen           = false;

    bool tdre()
    {
        ++polls;
        if (dead)
        {
            return false;
        }
        if (busyPolls > 0U)
        {
            --busyPolls;
            return false;
        }
        return true;
    }
};

Transmitter tx;

/** The port of SafeConsoleWriter.h with the semantics of bsp::Uart (Uart.cpp of the S32K1 platform). */
struct Port
{
    bool waitForTxReady()
    {
        for (uint32_t count = 0U; count <= TIMEOUT_POLLS; ++count)
        {
            if (tx.tdre())
            {
                return true;
            }
        }
        return false;
    }

    // bsp::Uart::write of one byte: writeByte() checks TDRE once, writes DATA, then waits for TDRE
    bool put(uint8_t const byte)
    {
        if (!tx.tdre())
        {
            return false; // the byte is dropped
        }
        if (!tx.firstSeen)
        {
            tx.firstSeen    = true;
            tx.traceAtFirst = ::test_trace::trace;
        }
        tx.sent.push_back(static_cast<char>(byte));
        if (::test_trace::lockDepth <= 0)
        {
            ++tx.sentUnlocked;
        }
        tx.busyPolls = tx.shiftPolls;
        if ((tx.dieAfterBytes >= 0) && (tx.sent.size() >= static_cast<size_t>(tx.dieAfterBytes)))
        {
            tx.dead = true;
        }
        return waitForTxReady();
    }
};

Port port;
} // namespace test_uart

namespace safety
{
// The family function on the target is this loop over the terminal UART (SafeConsole.cpp)
uint32_t safeConsoleWrite(char const* const data, uint32_t const length)
{
    return ::platform::writeSynchronously(::test_uart::port, data, length);
}

// SYS-59: the flush around the SAFETY RESET line. This model has no shift register (TDRE only), so the flush waits
// for TDRE like the writer; the real flush (STAT[TC], integration/SafeConsoleFlush.h) and the order around the line
// are tested by safety-reset-console (platforms/s32k1xx/port/main/src/integration/test/host).
bool safeConsoleFlush() { return ::test_uart::port.waitForTxReady(); }
} // namespace safety

namespace
{
using ::safety::SafeSupervisor;
using Event = SafeSupervisor::Event;

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

std::string line(char const* const event)
{
    return std::string("SAFE STATE HELD (") + event + "): outputs off, no reset - power cycle required\r\n";
}

SafeSupervisor& fresh()
{
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    supervisor.leaveLimpHome();
    ::test_trace::trace.clear();
    ::test_trace::lockDepth = 0;
    ::test_hold::left       = ::test_hold::HOLD_ROUNDS;
    ::test_uart::tx         = ::test_uart::Transmitter{};
    return supervisor;
}

/** The finding of SYS-39: the hold of the ROM check (idle context) - the whole line, with the interrupts locked. */
void testRomCheckHoldWritesTheLineWithInterruptsLocked()
{
    auto& supervisor = fresh();
    supervisor.romCheckMonitor.trigger();
    auto const& tx = ::test_uart::tx;
    CHECK(tx.sent == line("ROM_CHECK_FAILURE"));
    CHECK(tx.sentUnlocked == 0U);    // every byte inside handle(), all interrupts suspended
    CHECK(tx.traceAtFirst == "ES");  // log line, then the safe outputs walk - then the line
    CHECK(supervisor.holdRounds() == static_cast<uint32_t>(::test_hold::HOLD_ROUNDS));
    CHECK(::test_trace::trace == "ESSWSWSW"); // the hold itself is unchanged
    CHECK(::test_trace::trace.find('R') == std::string::npos);
}

/** The other caller, from main() before the BSP: the same line, the same way. */
void testWatchdogStartupHoldWritesTheSameWay()
{
    auto& supervisor = fresh();
    supervisor.watchdogStartupCheckMonitor.trigger();
    CHECK(::test_uart::tx.sent == line("WATCHDOG_STARTUP_CHECK_FAILURE"));
    CHECK(::test_uart::tx.sentUnlocked == 0U);
    CHECK(::test_uart::tx.traceAtFirst == "ES");
    CHECK(supervisor.holdRounds() == static_cast<uint32_t>(::test_hold::HOLD_ROUNDS));
}

/** The logger's last character is still in the transmitter when the hold starts: no byte is lost. */
void testBusyTransmitterLosesNoByte()
{
    fresh().romCheckMonitor.trigger();
    std::string const complete = ::test_uart::tx.sent;

    auto& supervisor              = fresh();
    ::test_uart::tx.busyPolls     = 7U;
    supervisor.romCheckMonitor.trigger();
    CHECK(::test_uart::tx.sent == complete);

    // Counter-example, the path of printf up to SYS-39: bsp::Uart::write per byte without waiting first
    fresh();
    ::test_uart::tx.busyPolls = 7U;
    std::string const text    = line("ROM_CHECK_FAILURE");
    for (char const c : text)
    {
        (void)::test_uart::port.put(static_cast<uint8_t>(c));
    }
    CHECK(::test_uart::tx.sent.size() < text.size()); // every byte that met a busy TDRE is gone
}

/** A dead console: one time-out, then the hold holds as always. */
void testDeadTransmitterCostsOneTimeout()
{
    auto& supervisor      = fresh();
    ::test_uart::tx.dead  = true;
    supervisor.romCheckMonitor.trigger();
    CHECK(::test_uart::tx.sent.empty());
    CHECK(::test_uart::tx.polls <= (::test_uart::TIMEOUT_POLLS + 1U));
    CHECK(supervisor.holdRounds() == static_cast<uint32_t>(::test_hold::HOLD_ROUNDS));
    CHECK(::test_trace::trace == "ESSWSWSW");

    // dies in the middle of the line: the written part is reported, the rest given up after one time-out
    fresh();
    ::test_uart::tx.dieAfterBytes = 10;
    uint32_t const written        = ::safety::safeConsoleWrite("0123456789ABCDEF", 16U);
    CHECK(written == 9U); // the tenth byte went into DATA but never left: not counted
    CHECK(::test_uart::tx.polls <= (10U * (::test_uart::tx.shiftPolls + 3U) +::test_uart::TIMEOUT_POLLS + 1U));
}

/** The hold line only on the holds; SYS-59: a resetting event writes its SAFETY RESET line the same way (whole,
 * with the interrupts locked, before the reset), a report-only event writes nothing. */
void testNoLineOnOtherPaths()
{
    auto& supervisor = fresh();
    supervisor.mpuConfigMonitor.trigger(3U);
    CHECK(::test_uart::tx.sent == "\r\nSAFETY RESET (MPU_CONFIGURATION_ERROR, region 3): outputs off\r\n");
    CHECK(::test_uart::tx.sentUnlocked == 0U);
    CHECK(::test_trace::trace.find('R') == ::test_trace::trace.size() - 1U); // the reset comes last
    ::test_uart::tx.sent.clear();
    supervisor.rteTimingMonitor.trigger(1U);
    supervisor.deliverDeferredEvents();
    CHECK(::test_uart::tx.sent.empty());
}

void testFormatterKeepsTheEnd()
{
    char buffer[::safety::SAFE_CONSOLE_LINE_SIZE];
    char const* const format = "SAFE STATE HELD (%s): outputs off, no reset - power cycle required\r\n";

    size_t n = ::safety::formatConsoleLine(buffer, sizeof(buffer), format, "ROM_CHECK_FAILURE");
    CHECK(std::string(buffer) == line("ROM_CHECK_FAILURE"));
    CHECK(n == line("ROM_CHECK_FAILURE").size());

    std::string const longName(300U, 'X');
    n = ::safety::formatConsoleLine(buffer, sizeof(buffer), format, longName.c_str());
    std::string const shortened(buffer);
    CHECK(n == sizeof(buffer) - 1U);
    CHECK(shortened.size() == n);
    CHECK(shortened.compare(0U, 17U, "SAFE STATE HELD (") == 0);
    std::string const tail = "): outputs off, no reset - power cycle required\r\n";
    CHECK(shortened.size() > tail.size());
    CHECK(shortened.substr(shortened.size() - tail.size()) == tail); // the end, CR LF included, is whole

    n = ::safety::formatConsoleLine(buffer, sizeof(buffer), "no placeholder\r\n", "ignored");
    CHECK(std::string(buffer) == "no placeholder\r\n");
    n = ::safety::formatConsoleLine(buffer, sizeof(buffer), format, nullptr);
    CHECK(std::string(buffer) == line(""));
    n = ::safety::formatConsoleLine(buffer, 1U, format, "E");
    CHECK((n == 0U) && (buffer[0] == '\0'));
    CHECK(::safety::formatConsoleLine(buffer, 0U, format, "E") == 0U);
    (void)n;
}
} // namespace

int main()
{
    testRomCheckHoldWritesTheLineWithInterruptsLocked();
    testWatchdogStartupHoldWritesTheSameWay();
    testBusyTransmitterLosesNoByte();
    testDeadTransmitterCostsOneTimeout();
    testNoLineOnOtherPaths();
    testFormatterKeepsTheEnd();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
