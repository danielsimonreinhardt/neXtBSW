/* SYS-59: host test of the SAFETY RESET line before every safety reset - the real SafeSupervisor.cpp with its default
 * console path (no replacement of NX_SAFE_SUPERVISOR_CONSOLE / _RESET_CONSOLE / _RESET_FLUSH), the real transmit loop
 * of the S32K1 family (safeConsole/SafeConsoleWriter.h) and the real flush (integration/SafeConsoleFlush.h) on a model
 * of the LPUART transmitter with data register, shift register, STAT[TDRE] and STAT[TC], and a model of the other
 * writers of the console (the logger and the console command in the idle context, preempted by the safety task).
 *
 * Board finding 2026-09-22 (captures 115, 116, 132, 133, 142-148): after DEADMAN_CONFIGURATION_ERROR and
 * SAFE_WATCHDOG_CONFIGURATION_ERROR (test build of SYS-51, "inject deadman", "inject wdogint") no SAFETY RESET line
 * came; the output broke off in the middle of an older line ("inject deadman: now " + one cut character + restart).
 * After SAFETY_CYCLE_PERIOD_VIOLATION (SYS-52) the line came in 116, 132, 133, not in 115.
 *
 * What the test shows:
 * - the cause (testReproducesTheBoardFinding): the reset path of SYS-52 on the model - a reset without a line cuts
 *   the character that the preempted writer had in the shift register and leaves its line open (the capture of 145
 *   exactly), and the line of SYS-52 lost its last character to the reset when it had not left the shift register.
 *   The pending entries of the logger never reach the wire, with or without the line: the line bypasses the logger,
 *   it does not wait behind it;
 * - since SYS-59 every S1 + S2 reaction writes its line (the deadman fields 0x10/0x4/0x40/0x2 and the watchdog
 *   fields 0x2 of the board checks P1-P3), whole, on a line of its own after an interrupted writer, with nothing cut
 *   by the reset: the transmitter is idle when softwareSystemReset() comes;
 * - the order: safe outputs (S1), the mark of the reset-loop record, flush, line, flush, reset - no byte of the line
 *   before S1, all of it with the interrupts locked;
 * - bounded: a transmitter that never gets idle costs one flush time-out (TRANSMITTER_FLUSH_POLLS polls) and no
 *   line, the reset still comes; the time from S1 to the reset stays within the stated bound (characters left in the
 *   transmitter + the line, in character times).
 * SYS-137 (board 2026-09-30, SYS-73 (1): no SAFETY RESET line in the captures after the start-up ECC fault in
 * main(), before the BSP; partly no output at all):
 * - the early path as main() runs it: the synchronous start-up line of printf (charIo is synchronous, each byte waits
 *   for TDRE; the last one is still in the shift register) and then INTEGRITY_EVENT_DMA_ECC_ERROR from the latent
 *   test - the start-up line and the SAFETY RESET line both whole, nothing cut: the flush before the line empties
 *   what printf left, nothing of printf is buffered behind it (testEarlyEccResetFromMain);
 * - the line as the very first output of a start (transmitter not yet initialized, bsp::Uart initializes it in its
 *   first waitForTxReady()): STAT[TC] has its reset value 1, the line is whole (testLineAsFirstOutputOfAStart);
 * - the record of the safe console in the no-init RAM (safeConsole/SafeConsoleRecord.h), with the glue of the target
 *   (safeConsoleWrite/safeConsoleFlush note every line and flush): the next start reads one line with all its bytes
 *   and two idle flushes, or the flush time-out of a dead transmitter; a record the run overwrote is recognized
 *   (testRecordOfTheStartBefore, testRecordIntegrity).
 * Mutants: platforms/s32k1xx/port/main/src/integration/test/host/test_safety_reset_console_mutations.py.
 *
 * Build and run (zig c++, no hardware; tools/test -k safety-reset-console):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DPLATFORM_SUPPORT_IO -DPLATFORM_SUPPORT_WATCHDOG \
 *       -Iecu/safety/safeSupervisor/include -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -Iplatforms/s32k1xx/port/main/src/safeConsole -Iplatforms/s32k1xx/port/main/src/integration \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       platforms/s32k1xx/port/main/src/integration/test/host/SafetyResetConsoleHostTest.cpp -o build/host/src.exe
 */

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace test_trace
{
std::string trace;
int lockDepth = 0;
void add(char c); // below: records the reset in the transmitter model too
} // namespace test_trace

#define NX_SAFE_SUPERVISOR_HOLDING() (false)
// NX_SAFE_SUPERVISOR_CONSOLE, _RESET_CONSOLE and _RESET_FLUSH deliberately not replaced: the target path is under test

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

#include "../../../../../../../../ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp" // NOLINT
#include "SafeConsoleFlush.h"
#include "SafeConsoleRecord.h"
#include "SafeConsoleWriter.h"

extern "C"
{
// SYS-137: the record of the target (SafeConsole.cpp places it in .noinit); here ordinary memory the model keeps
// across its "reset"
::platform::safeconsole::Record volatile nxSafeConsoleRecord;
}

namespace safety
{
SafetyEventMailbox rteTimingMailbox;
} // namespace safety

/* ------------------------------------------------------------------ model of the LPUART transmitter */

namespace test_uart
{
/** Model time per character on the wire, in reads/writes of the transmitter (one tick each). */
constexpr uint32_t CHARACTER_TICKS = 10U;
/** bsp::Uart: polls of STAT after which one character is given up (WRITE_TIMEOUT, scaled down for the model). */
constexpr uint32_t WRITE_TIMEOUT_POLLS = 1000U;
/** Polls after which the model stops a flush that would never end (a mutant without bound) and reports it. */
constexpr uint32_t HANG_POLLS = 3U * ::platform::TRANSMITTER_FLUSH_POLLS;
/** What the wire shows for a character the reset cut in the shift register (the board shows a garbage byte). */
constexpr char CUT = '~';

struct Transmitter
{
    bool dataFull  = false; // data register holds a character (STAT[TDRE] = 0)
    char data      = 0;
    bool shifting  = false; // shift register busy
    char shift     = 0;
    uint32_t left  = 0U;    // ticks until the character in the shift register is out
    bool stuck     = false; // the transmitter stopped (clock gone, TE cleared with a character pending)
    bool enabled   = true;  // SYS-137: false = not initialized yet (CTRL[TE] 0, STAT at its reset value)
    bool preamble  = false; // the shift register sends the idle character queued by setting TE
    uint64_t now   = 0U;
    std::string wire;       // what the other end received
    bool cutByReset = false;
    bool resetDone  = false;
    // the flush
    uint32_t flushPolls  = 0U; // polls of STAT[TC] in the current flush
    uint32_t maxFlush    = 0U; // most polls of one flush
    bool hung            = false;
    // the line of the supervisor
    bool lineStarted       = false;
    std::string traceAtLine; // trace when the first character of the supervisor went to the transmitter
    uint32_t unlocked      = 0U; // characters of the supervisor written without the interrupt lock
    uint64_t tSafeState    = 0U;
    uint64_t tReset        = 0U;
    bool markedAtFlush     = false;
    ::safety::resetloop::Record volatile* record = nullptr;

    void tick()
    {
        ++now;
        if (stuck)
        {
            return;
        }
        if (shifting)
        {
            --left;
            if (left == 0U)
            {
                if (!preamble)
                {
                    wire.push_back(shift);
                }
                preamble = false;
                shifting = false;
            }
        }
        if ((!shifting) && dataFull)
        {
            shift    = data;
            dataFull = false;
            shifting = true;
            left     = CHARACTER_TICKS;
        }
    }

    bool tdre()
    {
        tick();
        return !dataFull;
    }

    bool tc()
    {
        tick();
        return (!dataFull) && (!shifting);
    }

    void writeData(char const c)
    {
        tick();
        if (!dataFull)
        {
            data     = c;
            dataFull = true;
        }
    }

    // bsp::Uart::init() (first waitForTxReady() of a start): TE set, which queues one idle character (preamble)
    void init()
    {
        enabled  = true;
        shifting = true;
        preamble = true;
        left     = CHARACTER_TICKS;
    }

    // SYSRESETREQ resets the LPUART: a character in the shift register is cut, the data register is lost
    void reset()
    {
        resetDone  = true;
        cutByReset = (shifting && !preamble) || dataFull;
        if (shifting && !preamble)
        {
            wire.push_back(CUT);
        }
        shifting = false;
        dataFull = false;
    }
};

Transmitter tx;

/** Port of SafeConsoleWriter.h with the semantics of bsp::Uart (Uart.cpp of the S32K1 platform). */
struct UartPort
{
    bool supervisor = true; // false: another writer (logger, console) - not recorded as the supervisor's line

    bool waitForTxReady()
    {
        if (!tx.enabled)
        {
            tx.init(); // bsp::Uart::waitForTxReady(): "initializes the UART first if nobody did"
        }
        for (uint32_t count = 0U; count <= WRITE_TIMEOUT_POLLS; ++count)
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
        if (supervisor)
        {
            if (!tx.lineStarted)
            {
                tx.lineStarted = true;
                tx.traceAtLine = ::test_trace::trace;
            }
            if (::test_trace::lockDepth <= 0)
            {
                ++tx.unlocked;
            }
        }
        tx.writeData(static_cast<char>(byte));
        return waitForTxReady();
    }
};

/** Port of SafeConsoleFlush.h: STAT[TC] of the terminal LPUART, as Integration.cpp reads it. */
struct FlushPort
{
    bool transmissionComplete()
    {
        if (tx.flushPolls == 0U)
        {
            tx.markedAtFlush = (tx.record == nullptr)
                               || (tx.record->pending != ::safety::resetloop::CAUSE_NONE);
        }
        ++tx.flushPolls;
        if (tx.flushPolls > tx.maxFlush)
        {
            tx.maxFlush = tx.flushPolls;
        }
        if (tx.flushPolls >= HANG_POLLS)
        {
            tx.hung = true; // a flush without bound: end it here so that the test reports instead of hanging
            return true;
        }
        return tx.tc();
    }
};

/** The idle context (logger output, console command): writes `count` characters of `text` through bsp::Uart, the
 * last one without waiting for it - the safety task preempts right after the store to DATA. */
void otherWriterIsPreempted(std::string const& text, size_t const count)
{
    UartPort other;
    other.supervisor = false;
    for (size_t i = 0U; i < count; ++i)
    {
        if (i + 1U == count)
        {
            if (tx.tdre())
            {
                tx.writeData(text[i]); // preempted before its wait for TDRE
            }
        }
        else
        {
            (void)other.put(static_cast<uint8_t>(text[i]));
        }
    }
}
} // namespace test_uart

namespace test_trace
{
void add(char const c)
{
    trace.push_back(c);
    if ((c == 'S') && (::test_uart::tx.tSafeState == 0U))
    {
        ::test_uart::tx.tSafeState = ::test_uart::tx.now + 1U;
    }
    if (c == 'R')
    {
        ::test_uart::tx.tReset = ::test_uart::tx.now;
        ::test_uart::tx.reset();
    }
}
} // namespace test_trace

namespace safety
{
// The family functions on the target: the loop over the terminal UART (SafeConsole.cpp) and the flush over its STAT
// register (Integration.cpp)
uint32_t safeConsoleWrite(char const* const data, uint32_t const length)
{
    ::test_uart::UartPort port;
    uint32_t const written = ::platform::writeSynchronously(port, data, length);
    ::platform::safeconsole::noteLine(nxSafeConsoleRecord, length, written); // SYS-137, as SafeConsole.cpp
    return written;
}

bool safeConsoleFlush()
{
    ::test_uart::FlushPort port;
    ::test_uart::tx.flushPolls = 0U;
    bool const idle = ::platform::flushTransmitter(port, ::platform::TRANSMITTER_FLUSH_POLLS);
    ::platform::safeconsole::noteFlush(nxSafeConsoleRecord, idle); // SYS-137, as Integration.cpp
    return idle;
}
} // namespace safety

namespace
{
using ::safety::SafeSupervisor;
using Event = SafeSupervisor::Event;
namespace rl = ::safety::resetloop;

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

rl::Record volatile record;

/** A supervisor as main() leaves it (reset-loop record registered, limp home left), an idle transmitter. */
SafeSupervisor& fresh()
{
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    rl::clear(record);
    (void)supervisor.startResetLoopLimit(record, rl::Start{}, 3U, 100U);
    supervisor.leaveLimpHome();
    ::test_trace::trace.clear();
    ::test_trace::lockDepth = 0;
    ::test_uart::tx         = ::test_uart::Transmitter{};
    ::test_uart::tx.record  = &record;
    ::platform::safeconsole::clear(nxSafeConsoleRecord); // SYS-137: as boardInit() after power-on
    return supervisor;
}

std::string resetLine(char const* const text)
{
    return std::string("\r\nSAFETY RESET (") + text + "): outputs off\r\n";
}

// The line of the console command that was on the wire when the deadman was falsified (capture 145), and the
// entries the logger still had in its buffer (the log line of handle() itself among them).
std::string const COMMAND_LINE = "inject deadman: now 0x00000000\r\n";
std::vector<std::string> const PENDING_LOG
    = {"102205: RefApp: CONSOLE: INFO: Received console command \"inject deadman mier\"\r\n",
       "102215: RefApp: SAFETY: ERROR: Event: DEADMAN_CONFIGURATION_ERROR, fields: 0x4\r\n"};

bool containsPendingLog(std::string const& wire)
{
    for (std::string const& entry : PENDING_LOG)
    {
        if (wire.find(entry.substr(8U, 20U)) != std::string::npos)
        {
            return true;
        }
    }
    return false;
}

/** The cause on the model. SYS-52's reset path, done by hand with the real writer: for the deadman no line at all,
 * for the period violation the line and then the reset at once. The logger's pending entries play no part. */
void testReproducesTheBoardFinding()
{
    // DEADMAN_CONFIGURATION_ERROR before SYS-59: safe outputs, reset - no line
    (void)fresh();
    ::test_uart::otherWriterIsPreempted(COMMAND_LINE, 21U); // "inject deadman: now 0" - '0' still shifting
    ::test_trace::add('S');
    ::test_trace::add('R');
    std::string const& wire = ::test_uart::tx.wire;
    // capture 145: "inject deadman: now M-0" and the restart - the open line, a character cut by the reset
    CHECK(wire.compare(0U, 19U, "inject deadman: now") == 0);
    CHECK((wire.size() <= 21U) && (wire.back() == ::test_uart::CUT));
    CHECK(wire.find("SAFETY RESET") == std::string::npos);
    CHECK(::test_uart::tx.cutByReset);
    CHECK(!containsPendingLog(wire)); // the logger's entries are lost, they are not in front of anything

    // SAFETY_CYCLE_PERIOD_VIOLATION before SYS-59: the line of SYS-52 (without CR LF in front), then the reset
    (void)fresh();
    ::test_uart::otherWriterIsPreempted(COMMAND_LINE, 21U);
    ::test_trace::add('S');
    std::string const old = "SAFETY RESET (SAFETY_CYCLE_PERIOD_VIOLATION, kind 1, value 15): outputs off\r\n";
    CHECK(::safety::safeConsoleWrite(old.c_str(), static_cast<uint32_t>(old.size())) == old.size());
    ::test_trace::add('R');
    CHECK(::test_uart::tx.cutByReset); // its last character was still in the shift register
    CHECK(::test_uart::tx.wire == "inject deadman: now 0" + old.substr(0U, old.size() - 1U) + "~");
    CHECK(::test_uart::tx.wire.find("\r\nSAFETY RESET") == std::string::npos); // not on a line of its own
}

/** The fix: the deadman after an interrupted console line and with entries waiting in the logger. */
void testDeadmanAfterAnInterruptedWriter()
{
    auto& supervisor = fresh();
    ::test_uart::otherWriterIsPreempted(COMMAND_LINE, 21U);
    supervisor.deadmanConfigMonitor.trigger(0x4U); // "inject deadman mier"
    auto const& tx = ::test_uart::tx;
    std::string const line = resetLine("DEADMAN_CONFIGURATION_ERROR, fields 0x4");
    CHECK(tx.wire == "inject deadman: now 0" + line); // the interrupted character whole, the line on its own
    CHECK(!tx.cutByReset);                            // the reset cut nothing
    CHECK(!containsPendingLog(tx.wire));              // the line did not wait behind the logger
    CHECK(tx.traceAtLine == "ES");                    // log line (asynchronous), safe outputs - then the line
    CHECK(::test_trace::trace == "ESR");
    CHECK(tx.unlocked == 0U);
    CHECK(tx.markedAtFlush); // the mark first: a reset while the line is written still counts this cause
    CHECK(record.pending == (rl::CAUSE_EVENT | ::etl::to_underlying(Event::DEADMAN_CONFIGURATION_ERROR)));
    CHECK(!tx.hung);
}

/** Board checks P1-P3 of SYS-51 and B7 of SYS-40: every field of the deadman, the watchdog interrupt, the period
 * violation - each line whole and nothing cut, whether the transmitter was idle or busy at the event. */
void testBoardChecksP1ToP3()
{
    struct Case
    {
        char const* name;
        uint32_t value;
        char const* text;
    };
    Case const cases[] = {
        {"tval", 0x10U, "DEADMAN_CONFIGURATION_ERROR, fields 0x10"},
        {"mier", 0x4U, "DEADMAN_CONFIGURATION_ERROR, fields 0x4"},
        {"prio", 0x40U, "DEADMAN_CONFIGURATION_ERROR, fields 0x40"},
        {"dbgen", 0x2U, "DEADMAN_CONFIGURATION_ERROR, fields 0x2"},
        {"wdogint", 0x2U, "SAFE_WATCHDOG_CONFIGURATION_ERROR, fields 0x2"},
        {"period", 0x0100000FU, "SAFETY_CYCLE_PERIOD_VIOLATION, kind 1, value 15"},
    };
    for (Case const& c : cases)
    {
        for (size_t preempted : {size_t{0U}, size_t{1U}, size_t{5U}, COMMAND_LINE.size()})
        {
            auto& supervisor = fresh();
            ::test_uart::otherWriterIsPreempted(COMMAND_LINE, preempted);
            std::string const name(c.name);
            if (name == "wdogint")
            {
                supervisor.safeWatchdogConfigMonitor.check(false, c.value);
            }
            else if (name == "period")
            {
                supervisor.safetyCyclePeriodMonitor.trigger(c.value);
            }
            else
            {
                supervisor.deadmanConfigMonitor.trigger(c.value);
            }
            std::string const expected = COMMAND_LINE.substr(0U, preempted) + resetLine(c.text);
            bool const ok = (::test_uart::tx.wire == expected) && !::test_uart::tx.cutByReset;
            CHECK(ok);
            if (!ok)
            {
                std::printf("  %s after %u characters: wire '%s'\n", c.name, static_cast<unsigned>(preempted),
                            ::test_uart::tx.wire.c_str());
            }
        }
    }
}

/** Every S1 + S2 event of handle() writes its line whole before its reset (no event resets silently any more). */
void testEveryResettingEventLeavesItsLineWhole()
{
    auto const last = static_cast<uint8_t>(Event::RTOS_ASSERT); // SYS-80: the last event
    uint32_t resets = 0U;
    for (uint8_t id = 0U; id <= last; ++id)
    {
        auto& supervisor = fresh();
        supervisor.handle(static_cast<Event>(id));
        if (::test_trace::trace.find('R') == std::string::npos)
        {
            continue; // hold or report only
        }
        ++resets;
        std::string const& wire = ::test_uart::tx.wire;
        bool const ok = (wire.compare(0U, 16U, "\r\nSAFETY RESET (") == 0) && (wire.size() > 18U)
                        && (wire.substr(wire.size() - 16U) == "): outputs off\r\n") && !::test_uart::tx.cutByReset
                        && (::test_trace::trace.back() == 'R') && ::test_uart::tx.markedAtFlush;
        CHECK(ok);
        if (!ok)
        {
            std::printf("  event %u: wire '%s', trace '%s'\n", static_cast<unsigned>(id), wire.c_str(),
                        ::test_trace::trace.c_str());
        }
    }
    CHECK(resets == 17U); // the S1 + S2 events of the list (as testResetLoopMarkOnlyBeforeAReset of safe-supervisor)
}

/** Bounded: the time from the safe outputs to the reset stays within the stated bound - the characters another
 * writer left in the transmitter (data and shift register: two) plus the line, in character times; the polls of
 * the model's clock add at most one tick per character. */
void testTimeFromSafeStateToResetIsBounded()
{
    auto& supervisor = fresh();
    ::test_uart::otherWriterIsPreempted(COMMAND_LINE, 3U);
    supervisor.deadmanConfigMonitor.trigger(0x40U);
    auto const& tx         = ::test_uart::tx;
    uint64_t const line    = resetLine("DEADMAN_CONFIGURATION_ERROR, fields 0x40").size();
    uint64_t const elapsed = tx.tReset - tx.tSafeState;
    uint64_t const bound   = (2U + line) * (::test_uart::CHARACTER_TICKS + 1U) + 4U;
    CHECK(elapsed <= bound);
    CHECK(elapsed >= line * ::test_uart::CHARACTER_TICKS); // the line was really sent before the reset
    if (elapsed > bound)
    {
        std::printf("  %u ticks from S1 to the reset, bound %u\n", static_cast<unsigned>(elapsed),
                    static_cast<unsigned>(bound));
    }
    CHECK(tx.maxFlush <= (2U * (::test_uart::CHARACTER_TICKS + 1U)));
}

/** A transmitter that never gets idle: one flush time-out, no line, and the reset still comes - the reset never
 * waits for the console. */
void testDeadTransmitterCostsOneFlush()
{
    auto& supervisor = fresh();
    ::test_uart::otherWriterIsPreempted(COMMAND_LINE, 4U);
    ::test_uart::tx.stuck = true;
    supervisor.deadmanConfigMonitor.trigger(0x10U);
    auto const& tx = ::test_uart::tx;
    CHECK(!tx.hung);
    CHECK(tx.maxFlush == ::platform::TRANSMITTER_FLUSH_POLLS); // exactly the bound, then given up
    CHECK(!tx.lineStarted);                                    // no line on a dead console
    CHECK(::test_trace::trace == "ESR");                       // the reset comes all the same
    // one time-out in all, not one per step: from S1 to the reset nothing but the one flush polled the transmitter
    CHECK((tx.tReset - tx.tSafeState) <= (::platform::TRANSMITTER_FLUSH_POLLS + 2U));
    CHECK(record.pending == (rl::CAUSE_EVENT | ::etl::to_underlying(Event::DEADMAN_CONFIGURATION_ERROR)));

    // gets idle again within the bound (a long character, slow flow control): the line is written
    auto& again = fresh();
    ::test_uart::otherWriterIsPreempted(COMMAND_LINE, 4U);
    ::test_uart::tx.left = 50000U; // the shift register takes half the flush time-out
    again.deadmanConfigMonitor.trigger(0x10U);
    CHECK(::test_uart::tx.wire == "inje" + resetLine("DEADMAN_CONFIGURATION_ERROR, fields 0x10"));
    CHECK(!::test_uart::tx.cutByReset);
}

/** The flush alone: returns at once on an idle transmitter, waits for both registers of a busy one, and polls at
 * most the bound on a stuck one. */
void testFlushOnItsOwn()
{
    (void)fresh();
    CHECK(::safety::safeConsoleFlush());
    CHECK(::test_uart::tx.flushPolls == 1U);

    (void)fresh();
    ::test_uart::tx.writeData('a');
    ::test_uart::tx.writeData('b'); // 'a' in the shift register, 'b' in the data register
    CHECK(::safety::safeConsoleFlush());
    CHECK(::test_uart::tx.wire == "ab");

    (void)fresh();
    ::test_uart::tx.writeData('a');
    ::test_uart::tx.stuck = true;
    CHECK(!::safety::safeConsoleFlush());
    CHECK(::test_uart::tx.flushPolls == ::platform::TRANSMITTER_FLUSH_POLLS);
}

namespace sc = ::platform::safeconsole;

/** printf of the start-up code in main() (charIo, synchronous): bsp::Uart::write of each byte - write DATA, wait for
 * TDRE - with the "\n" -> "\r\n" of __outedit; returns with its last character still in the shift register. */
void printfFromMain(std::string const& text)
{
    ::test_uart::UartPort out;
    out.supervisor = false;
    for (char const c : text)
    {
        if (c == '\n')
        {
            (void)out.put(static_cast<uint8_t>('\r'));
        }
        (void)out.put(static_cast<uint8_t>(c));
    }
}

/** SYS-137: the start-up ECC fault of SYS-73 (1) as main() meets it - the watchdog line of printf, then the latent
 * test fails (context 0x00000E08 of the board's counter-probe) and main() triggers the ECC monitor inside its
 * SafetyShell. Nothing printf wrote is left behind the line or cut: charIo does not buffer, and the flush before
 * the line waits for its last character. */
void testEarlyEccResetFromMain()
{
    auto& supervisor = fresh();
    ::test_uart::tx.enabled = false; // after the reset of the fast test: printf initializes the transmitter
    printfFromMain("MCU watchdog fast tests successful\r\n");
    CHECK(::test_uart::tx.shifting); // the last character still on its way when the test starts
    supervisor.dmaEccMonitor.trigger(0x00000E08U);
    auto const& tx         = ::test_uart::tx;
    std::string const line = resetLine("INTEGRITY_EVENT_DMA_ECC_ERROR, sources 0xe08");
    CHECK(tx.wire == "MCU watchdog fast tests successful\r\r\n" + line);
    CHECK(!tx.cutByReset);
    CHECK(::test_trace::trace == "ESR"); // log line (no logger yet: dropped), S1, reset - the line in between
    CHECK(tx.traceAtLine == "ES");
    CHECK(tx.unlocked == 0U);
    CHECK(tx.markedAtFlush);
    CHECK(record.pending == (rl::CAUSE_EVENT | ::etl::to_underlying(Event::INTEGRITY_EVENT_DMA_ECC_ERROR)));
    CHECK(record.pending == 0x107U); // "last cause 0x107" of the next start, as on the board

    // the next start: the record of the safe console says the line went out whole, both flushes idle
    sc::Snapshot const before = sc::take(nxSafeConsoleRecord);
    CHECK(before.present && before.intact);
    CHECK(before.lines == 1U);
    CHECK(before.requested == line.size());
    CHECK(before.written == line.size());
    CHECK(before.flushesOk == 2U);
    CHECK(before.flushTimeouts == 0U);
    // taken once: the start after it reports nothing
    CHECK(!sc::take(nxSafeConsoleRecord).present);
}

/** SYS-137: the line as the first output of a start (no printf before it): the transmitter is initialized by the
 * first byte's wait (bsp::Uart), the flush before it sees the reset value of STAT[TC] - the line is whole. */
void testLineAsFirstOutputOfAStart()
{
    auto& supervisor = fresh();
    ::test_uart::tx.enabled = false;
    supervisor.dmaEccMonitor.trigger(0x00000E08U);
    std::string const line = resetLine("INTEGRITY_EVENT_DMA_ECC_ERROR, sources 0xe08");
    CHECK(::test_uart::tx.wire == line);
    CHECK(!::test_uart::tx.cutByReset);
    CHECK(::test_trace::trace == "ESR");
    sc::Snapshot const before = sc::take(nxSafeConsoleRecord);
    CHECK(before.present && before.intact && (before.lines == 1U) && (before.written == line.size())
          && (before.flushesOk == 2U) && (before.flushTimeouts == 0U));
}

/** SYS-137: what the next start reads after the other endings of a start - a dead transmitter (flush time-out, no
 * line), a hold (line, no flush), no safe console output at all (nothing to report). */
void testRecordOfTheStartBefore()
{
    auto& dead = fresh();
    ::test_uart::otherWriterIsPreempted(COMMAND_LINE, 4U);
    ::test_uart::tx.stuck = true;
    dead.dmaEccMonitor.trigger(0x1U);
    sc::Snapshot before = sc::take(nxSafeConsoleRecord);
    CHECK(before.present && before.intact);
    CHECK((before.lines == 0U) && (before.requested == 0U) && (before.written == 0U));
    CHECK((before.flushesOk == 0U) && (before.flushTimeouts == 1U));

    auto& hold = fresh();
    hold.romCheckMonitor.trigger();
    before = sc::take(nxSafeConsoleRecord);
    CHECK(before.present && before.intact && (before.lines == 1U) && (before.requested == before.written)
          && (before.written > 0U) && (before.flushesOk == 0U) && (before.flushTimeouts == 0U));

    (void)fresh();
    before = sc::take(nxSafeConsoleRecord);
    CHECK(!before.present && before.intact);

    // a transmitter that takes only part of the line (it stops in the middle): written < requested
    auto& partial = fresh();
    ::test_uart::tx.writeData('x'); // busy, then stuck with a character in both registers
    ::test_uart::tx.writeData('y');
    ::test_uart::tx.stuck = true;
    CHECK(::safety::safeConsoleWrite("abc", 3U) == 0U);
    (void)partial;
    before = sc::take(nxSafeConsoleRecord);
    CHECK(before.present && (before.lines == 1U) && (before.requested == 3U) && (before.written == 0U));
}

/** SYS-137: the record in the no-init RAM - garbage after a new image is no record, an overwritten record is
 * recognized, the counters saturate, a take empties it. */
void testRecordIntegrity()
{
    sc::Record volatile r{};
    r.magic = 0x12345678U; // RAM content that never was a record
    r.lines = 7U;
    sc::Snapshot snapshot = sc::take(r);
    CHECK(!snapshot.present && !snapshot.intact);
    CHECK(sc::isIntact(r) && (r.lines == 0U)); // emptied

    sc::noteLine(r, 10U, 10U);
    sc::noteFlush(r, true);
    r.written = 3U; // overwritten by the run
    snapshot = sc::take(r);
    CHECK(snapshot.present && !snapshot.intact);

    r.magic = 0U; // a line into a record that is not intact starts it again
    sc::noteLine(r, 5U, 4U);
    CHECK(sc::isIntact(r) && (r.lines == 1U) && (r.requested == 5U) && (r.written == 4U) && (r.flushesOk == 0U));
    r.magic = 0U;
    sc::noteFlush(r, false);
    CHECK(sc::isIntact(r) && (r.lines == 0U) && (r.flushTimeouts == 1U));

    sc::clear(r);
    for (uint32_t i = 0U; i < (sc::COUNT_MAX + 5U); ++i)
    {
        sc::noteFlush(r, true);
    }
    CHECK(r.flushesOk == sc::COUNT_MAX);
    snapshot = sc::take(r);
    CHECK(snapshot.present && snapshot.intact && (snapshot.flushesOk == sc::COUNT_MAX));
    CHECK(!sc::take(r).present);
}

/** The holds are unchanged: their line, no reset, no flush needed (they never reset). */
void testHoldIsUnchanged()
{
    auto& supervisor = fresh();
    supervisor.romCheckMonitor.trigger();
    CHECK(::test_uart::tx.lineStarted);
    CHECK(::test_trace::trace.find('R') == std::string::npos);
    CHECK(::test_uart::tx.maxFlush == 0U);
}
} // namespace

int main()
{
    testReproducesTheBoardFinding();
    testDeadmanAfterAnInterruptedWriter();
    testBoardChecksP1ToP3();
    testEveryResettingEventLeavesItsLineWhole();
    testTimeFromSafeStateToResetIsBounded();
    testDeadTransmitterCostsOneFlush();
    testFlushOnItsOwn();
    testHoldIsUnchanged();
    testEarlyEccResetFromMain();
    testLineAsFirstOutputOfAStart();
    testRecordOfTheStartBefore();
    testRecordIntegrity();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
