/* Host test of the CAN console commands (COM-24): a command with a long output must not hold the CAN task,
 * and no output may get lost or change its order.
 *
 * Finding COM-22: the "can" commands ran completely in task can (AsyncCommandWrapper with the system's
 * context) and wrote to the UART synchronously, 86.8 us per character at 115200 Bd. "can status" held task
 * can - the transmit gate, reception and DoCAN/UDS - for 40-50 ms per bus. Since COM-24 the command runs in
 * the console context (TASK_IDLE); only the snapshot of a bus, a send and the log flag run in task can,
 * through ::can::ContextCall. The text comes from ::can::writeChannelStatus.
 *
 * Parts:
 *  1. writeChannelStatus: the text is the one before COM-24 (golden lines), the worst case is complete.
 *  2. ContextCall: inline in the own context, otherwise in the owner's, result visible afterwards, no
 *     nested call.
 *  3. Scheduler model (fixed priorities, preemptive, time in ns): task can with the 1 ms supervision and UDS
 *     requests (tester present every 2 ms, P2 = 50 ms), the console in TASK_IDLE, a UART that costs 86.8 us
 *     of the writing task per character. The command code is the real code (ContextCall + writeChannelStatus),
 *     composed like CanSystem::Command. Configured the old way (console context = task can) the model shows
 *     the finding of COM-22 - that is the deliberately inserted fault; configured like COM-24 the task can
 *     waits at most for one snapshot.
 * No target hardware needed.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/canSystem/include libs/bsw/canSystem/test/host/CanConsoleHostTest.cpp \
 *       -o build/host/can_console_test.exe
 *   build/host/can_console_test.exe
 */

#include "can/CanChannelStatus.h"
#include "can/ContextCall.h"

#include <cstdarg>
#include <cstdint>
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

using ::can::CanChannelStatus;

/* ---------------------------------------------------------------- writer with printf, as StringWriter */

class TextWriter
{
public:
    virtual ~TextWriter() = default;

    __attribute__((format(printf, 2, 3))) TextWriter& printf(char const* const format, ...)
    {
        char buffer[512];
        va_list ap;
        va_start(ap, format);
        int const n = std::vsnprintf(buffer, sizeof(buffer), format, ap);
        va_end(ap);
        CHECK((n >= 0) && (static_cast<size_t>(n) < sizeof(buffer))); // no line of the test is cut
        if (n > 0)
        {
            put(std::string(buffer, static_cast<size_t>(n)));
        }
        return *this;
    }

protected:
    virtual void put(std::string const& text) = 0;
};

class StringOut : public TextWriter
{
public:
    std::string text;

protected:
    void put(std::string const& t) override { text += t; }
};

/* Values as seen at the board (COM-09/COM-12: ESR1 0x00240082, TEC 30), transmit buffer busy. */
CanChannelStatus boardStatus(char const* const name)
{
    CanChannelStatus s{};
    s.name                = name;
    s.state               = "open";
    s.phyReady            = true;
    s.transmitAllowed     = true;
    s.busState            = "error active";
    s.passiveCount        = 1U;
    s.everBusOff          = false;
    s.automaticRecovery   = true;
    s.driverState         = "active";
    s.driverNotifications = 2U;
    s.bitrate             = 500000U;
    s.ctrl1               = 0x04620006U;
    s.esr1                = 0x00240082U;
    s.fltconf             = 0U;
    s.fltconfName         = "error active";
    s.tec                 = 30U;
    s.rx                  = 1234U;
    s.tx                  = 56789U;
    s.consoleOk           = 3U;
    s.txBufferBusy        = 17U;
    s.muteCount           = 0U;
    return s;
}

std::string const BOARD_TEXT
    = "CAN0 open, phy ready, tx allowed\r\n"
      "bus error active, bus off 0 times (now 0 ms, longest 0 ms, 0 recoveries), error passive 1 times, "
      "ever bus off no, recovery automatic\r\n"
      "driver state active, notifications 2 (bus off 0), phy errors 0\r\n"
      "bitrate 500000, CTRL1 0x04620006, ESR1 0x00240082 (FLTCONF 0 error active, TXWRN 0, RXWRN 0, SYNCH 0), "
      "TEC 30, REC 0\r\n"
      "rx 1234, tx 56789 (console 3 ok, 0 failed, 0 refused), tx buffer busy 17, muted 0 times\r\n"
      "  tx buffer busy: no free transmit buffer, earlier frames still wait for an acknowledge "
      "(no other node on the bus?)\r\n";

size_t countLines(std::string const& text)
{
    size_t lines = 0U;
    for (size_t i = 0U; (i + 1U) < text.size(); ++i)
    {
        if ((text[i] == '\r') && (text[i + 1U] == '\n'))
        {
            ++lines;
        }
    }
    return lines;
}

void testStatusText()
{
    std::printf("status text\n");
    StringOut out;
    ::can::writeChannelStatus(out, boardStatus("CAN0"));
    CHECK(out.text == BOARD_TEXT); // same text as the format strings before COM-24
    CHECK(countLines(out.text) == 6U);

    // Without transmit backlog the explanation line is left out.
    CanChannelStatus quiet = boardStatus("CAN0");
    quiet.txBufferBusy     = 0U;
    StringOut outQuiet;
    ::can::writeChannelStatus(outQuiet, quiet);
    CHECK(countLines(outQuiet.text) == 5U);
    CHECK(outQuiet.text.find("tx buffer busy 0, muted 0 times\r\n") != std::string::npos);

    // Muted bus, driver disagrees, bus off right now.
    CanChannelStatus muted = boardStatus("CAN1");
    muted.state            = "muted";
    muted.phyReady         = false;
    muted.transmitAllowed  = false;
    muted.driverState      = "bus off";
    muted.driverDisagrees  = true;
    StringOut outMuted;
    ::can::writeChannelStatus(outMuted, muted);
    CHECK(outMuted.text.compare(0U, 36U, "CAN1 muted, phy not ready, tx blocke") == 0);
    CHECK(outMuted.text.find("(driver disagrees, it reads FLTCONF with a setter macro: not used, COM-12)\r\n")
          != std::string::npos);

    // Worst case: every number at its maximum, every optional part present. Nothing is cut (the writer
    // checks every line against its buffer), and the length is what the timing model below uses.
    CanChannelStatus worst{};
    worst.name              = "CAN_LONGNAME";
    worst.state             = "initialized";
    worst.busState          = "error passive";
    worst.driverState       = "passive";
    worst.driverDisagrees   = true;
    worst.fltconfName       = "error passive";
    uint32_t const max      = 0xFFFFFFFFU;
    worst.busOffCount       = max;
    worst.busOffNowMs       = max;
    worst.longestBusOffMs   = max;
    worst.recoveries        = max;
    worst.passiveCount      = max;
    worst.driverNotifications = max;
    worst.driverBusOffReports = max;
    worst.driverPhyErrors     = max;
    worst.bitrate             = max;
    worst.ctrl1               = max;
    worst.esr1                = max;
    worst.fltconf             = max;
    worst.txwrn               = max;
    worst.rxwrn               = max;
    worst.synch               = max;
    worst.tec                 = max;
    worst.rec                 = max;
    worst.rx                  = max;
    worst.tx                  = max;
    worst.consoleOk           = max;
    worst.consoleFailed       = max;
    worst.consoleRefused      = max;
    worst.txBufferBusy        = max;
    worst.muteCount           = max;
    StringOut outWorst;
    ::can::writeChannelStatus(outWorst, worst);
    CHECK(countLines(outWorst.text) == 6U);
    CHECK(outWorst.text.find("muted 4294967295 times\r\n") != std::string::npos);
    std::printf(
        "  board values %u characters (%.1f ms at 115200 Bd), worst case %u characters (%.1f ms)\n",
        static_cast<unsigned>(out.text.size()),
        static_cast<double>(out.text.size()) * 0.0868,
        static_cast<unsigned>(outWorst.text.size()),
        static_cast<double>(outWorst.text.size()) * 0.0868);
    CHECK(outWorst.text.size() < 1000U);
}

/* ---------------------------------------------------------------- scheduler model */

constexpr uint8_t CTX_IDLE = 0U;  // TASK_IDLE: console input, logger output, since COM-24 the can commands
constexpr uint8_t CTX_CAN  = 4U;  // TASK_CAN in the demo project (key 40)

constexpr uint64_t NS_PER_US          = 1000U;
constexpr uint64_t CHARACTER_NS       = 86806U;          // 10 bit at 115200 Bd
constexpr uint64_t SUPERVISION_PERIOD = 1000U * NS_PER_US; // 1 ms as in COM-22: the harder case (10 ms since COM-23)
constexpr uint64_t SUPERVISION_COST   = 5U * NS_PER_US;    // gate and bus state, estimate COM-22
constexpr uint64_t UDS_PERIOD         = 2000U * NS_PER_US; // tester present loop / canflash info
constexpr uint64_t UDS_COST           = 60U * NS_PER_US;   // DoCAN reception and response, generous
constexpr uint64_t UDS_P2_NS          = 50000U * NS_PER_US;
// One call into task can from the console: two task switches and the snapshot of one bus (a few register
// and member reads). Deliberately generous for a Cortex-M4 at 80 MHz; the board checklist measures it.
constexpr uint64_t HOP_COST = 20U * NS_PER_US;

struct CanJob
{
    uint64_t release;
    uint64_t cost;
    bool uds;
};

/* Fixed priority scheduler with two contexts that matter here: task can above the console. Every other
 * task is left out; it would only add the same delay to both variants. */
class Model
{
public:
    Model(uint64_t const horizon, uint64_t const udsPhase)
    {
        // periodic supervision and UDS requests, sorted by release time
        uint64_t nextSup = SUPERVISION_PERIOD;
        uint64_t nextUds = udsPhase;
        while ((nextSup < horizon) || (nextUds < horizon))
        {
            if (nextUds <= nextSup)
            {
                _jobs.push_back(CanJob{nextUds, UDS_COST, true});
                nextUds += UDS_PERIOD;
            }
            else
            {
                _jobs.push_back(CanJob{nextSup, SUPERVISION_COST, false});
                nextSup += SUPERVISION_PERIOD;
            }
        }
    }

    uint64_t now() const { return _now; }

    uint8_t current() const { return _current; }

    void setCurrent(uint8_t const context) { _current = context; }

    /** The running context uses the CPU for \p duration; task can preempts a lower context. */
    void consume(uint64_t const duration)
    {
        if (_current == CTX_CAN)
        {
            _now += duration;
            _canBusyByConsole += duration; // only console work is consumed in task can here
            return;
        }
        uint64_t remaining = duration;
        while (remaining > 0U)
        {
            if ((_next < _jobs.size()) && (_jobs[_next].release < (_now + remaining)))
            {
                uint64_t const release = _jobs[_next].release;
                if (release > _now)
                {
                    remaining -= (release - _now);
                    _now = release;
                }
                runCanJob();
            }
            else
            {
                _now += remaining;
                remaining = 0U;
            }
        }
    }

    /** Task can runs everything released up to now (after it was busy itself, or when it preempts). */
    void drainCan()
    {
        while ((_next < _jobs.size()) && (_jobs[_next].release <= _now))
        {
            runCanJob();
        }
    }

    uint64_t maxCanWait() const { return _maxCanWait; }

    uint64_t maxUdsLatency() const { return _maxUdsLatency; }

    uint64_t canBusyByConsole() const { return _canBusyByConsole; }

    uint32_t udsResponses() const { return _udsResponses; }

private:
    void runCanJob()
    {
        CanJob const& job = _jobs[_next];
        ++_next;
        uint64_t const start = (_now > job.release) ? _now : job.release;
        uint64_t const wait  = start - job.release;
        if (wait > _maxCanWait)
        {
            _maxCanWait = wait;
        }
        _now = start + job.cost;
        if (job.uds)
        {
            ++_udsResponses;
            uint64_t const latency = _now - job.release;
            if (latency > _maxUdsLatency)
            {
                _maxUdsLatency = latency;
            }
        }
    }

    std::vector<CanJob> _jobs;
    size_t _next               = 0U;
    uint64_t _now              = 0U;
    uint8_t _current           = CTX_IDLE;
    uint64_t _maxCanWait       = 0U;
    uint64_t _maxUdsLatency    = 0U;
    uint64_t _canBusyByConsole = 0U;
    uint32_t _udsResponses     = 0U;
};

Model* model = nullptr;

struct ModelRunnable
{
    virtual void execute() = 0;

protected:
    ~ModelRunnable() = default;
};

/* ::async::execute in the model: task can has the higher priority and preempts the console right away. */
void modelExecute(uint8_t const context, ModelRunnable& runnable)
{
    CHECK(context == CTX_CAN);
    uint8_t const caller = model->current();
    if (caller != context)
    {
        model->drainCan(); // jobs already released run before the queued runnable
    }
    model->setCurrent(context);
    runnable.execute();
    model->setCurrent(caller);
    if (caller != context)
    {
        model->drainCan(); // jobs released while the runnable ran
    }
}

using ModelCall = ::can::ContextCall<ModelRunnable, uint8_t, &modelExecute>;

/* The console UART: every character costs the writing context 86.8 us (bsp::Uart::write waits for TDRE). */
class UartOut : public TextWriter
{
public:
    std::string text;
    uint64_t charactersInCan = 0U;

protected:
    void put(std::string const& t) override
    {
        for (char const c : t)
        {
            text += c;
            if (model->current() == CTX_CAN)
            {
                ++charactersInCan;
            }
            model->consume(CHARACTER_NS);
        }
    }
};

/* "can status" as composed by CanSystem::Command (ID_STATUS) and Channel::writeStatus, with inCanTask(). */
void canStatus(ModelCall& call, uint8_t const owner, UartOut& out, std::vector<CanChannelStatus> const& buses)
{
    for (CanChannelStatus const& bus : buses)
    {
        CanChannelStatus status{};
        auto const take = [&status, &bus]()
        {
            CHECK(model->current() == CTX_CAN); // the state of a bus is read in task can only
            model->consume(HOP_COST);
            status = bus;
        };
        if (!call.run(owner, take))
        {
            take();
        }
        ::can::writeChannelStatus(out, status);
    }
}

struct RunResult
{
    std::string text;
    uint64_t maxCanWait;
    uint64_t maxUdsLatency;
    uint64_t canBusyByConsole;
    uint64_t charactersInCan;
    uint32_t udsResponses;
    uint32_t queuedCalls;
};

/**
 * Executes "can status" once at 5 ms, the way AsyncConsole does: AsyncCommandWrapper runs the command in
 * \p commandContext, then "ok" in the same context (AsyncConsole::terminate). Runs the model for 200 ms.
 */
RunResult runCommand(uint8_t const commandContext, std::vector<CanChannelStatus> const& buses, uint64_t udsPhase)
{
    Model m(200000U * NS_PER_US, udsPhase);
    model = &m;
    ModelCall call(commandContext); // COM-24: TASK_IDLE; before: the system's own context
    UartOut out;

    m.consume(5000U * NS_PER_US); // console idle until the line arrives
    m.setCurrent(commandContext);
    if (commandContext == CTX_CAN)
    {
        m.drainCan(); // the wrapper's runnable queues behind what task can has to do anyway
    }
    canStatus(call, CTX_CAN, out, buses);
    (void)out.printf("ok\n");
    m.setCurrent(CTX_IDLE);
    m.drainCan();
    m.consume(200000U * NS_PER_US - m.now()); // rest of the horizon, the console waits for input
    model = nullptr;
    return RunResult{
        out.text,
        m.maxCanWait(),
        m.maxUdsLatency(),
        m.canBusyByConsole(),
        out.charactersInCan,
        m.udsResponses(),
        call.queuedCalls()};
}

std::string expectedText(std::vector<CanChannelStatus> const& buses)
{
    StringOut ref;
    for (CanChannelStatus const& bus : buses)
    {
        ::can::writeChannelStatus(ref, bus);
    }
    (void)ref.printf("ok\n");
    return ref.text;
}

double ms(uint64_t const ns) { return static_cast<double>(ns) / 1.0e6; }

void testContextCall()
{
    std::printf("context call\n");
    Model m(10000U * NS_PER_US, 1000U * NS_PER_US);
    model = &m;

    // Owner = caller: inline, nothing queued.
    ModelCall inlineCall(CTX_CAN);
    m.setCurrent(CTX_CAN);
    int ran         = 0;
    auto const work = [&ran]() { ++ran; };
    CHECK(inlineCall.run(CTX_CAN, work));
    CHECK(ran == 1);
    CHECK(inlineCall.queuedCalls() == 0U);

    // Console below the owner: the work runs in task can, its result is there when run() returns.
    ModelCall call(CTX_IDLE);
    m.setCurrent(CTX_IDLE);
    uint8_t seenIn    = 0xFFU;
    uint32_t value    = 0U;
    auto const read   = [&seenIn, &value]()
    {
        seenIn = model->current();
        value  = 42U;
    };
    CHECK(call.run(CTX_CAN, read));
    CHECK(seenIn == CTX_CAN);
    CHECK(value == 42U);
    CHECK(m.current() == CTX_IDLE);
    CHECK(call.queuedCalls() == 1U);

    // Calls run one after the other in the order of the command.
    std::string order;
    auto const a = [&order]() { order += 'a'; };
    auto const b = [&order]() { order += 'b'; };
    CHECK(call.run(CTX_CAN, a));
    CHECK(call.run(CTX_CAN, b));
    CHECK(order == "ab");

    // No second call while one is in progress (the owner would run work of a caller that went on).
    bool nested       = true;
    int innerRuns     = 0;
    auto const inner  = [&innerRuns]() { ++innerRuns; };
    auto const outer  = [&call, &nested, &inner]() { nested = call.run(CTX_CAN, inner); };
    CHECK(call.run(CTX_CAN, outer));
    CHECK(!nested);
    CHECK(innerRuns == 0);
    // ... and the call is usable again afterwards
    CHECK(call.run(CTX_CAN, inner));
    CHECK(innerRuns == 1);
    model = nullptr;
}

void testTiming()
{
    std::printf("timing model (task can 1 ms supervision, UDS every 2 ms, UART 86.8 us per character)\n");
    std::vector<CanChannelStatus> const oneBus{boardStatus("CAN0")};
    std::vector<CanChannelStatus> const twoBuses{boardStatus("CAN0"), boardStatus("CAN1")};

    for (uint64_t phase = 0U; phase < UDS_PERIOD; phase += 250U * NS_PER_US)
    {
        // COM-24: the command in the console context.
        RunResult const now = runCommand(CTX_IDLE, twoBuses, phase);
        CHECK(now.text == expectedText(twoBuses)); // complete, in order, "ok" last
        CHECK(now.charactersInCan == 0U);          // task can never waits for the UART
        CHECK(now.queuedCalls == 2U);              // one snapshot per bus
        CHECK(now.canBusyByConsole == 2U * HOP_COST);
        // Task can waits at most for one snapshot plus one of its own jobs that was released at the
        // same moment: the limit is set by HOP_COST, not by the length of the output.
        CHECK(now.maxCanWait <= (HOP_COST + UDS_COST));
        CHECK(now.maxUdsLatency <= (HOP_COST + UDS_COST + SUPERVISION_COST + UDS_COST));
        CHECK(now.maxUdsLatency < UDS_P2_NS);
        CHECK(now.udsResponses == 100U);
        if (phase == 0U)
        {
            std::printf(
                "  COM-24, two buses: %u characters, task can used %.3f ms by the command, longest wait of "
                "task can %.3f ms, UDS response <= %.3f ms\n",
                static_cast<unsigned>(now.text.size()),
                ms(now.canBusyByConsole),
                ms(now.maxCanWait),
                ms(now.maxUdsLatency));
        }

        // Before COM-24 (deliberately inserted fault: command in the context of task can).
        RunResult const before1 = runCommand(CTX_CAN, oneBus, phase);
        CHECK(before1.text == expectedText(oneBus)); // the output itself was complete before as well
        CHECK(before1.charactersInCan == before1.text.size());
        CHECK(before1.maxCanWait >= (40000U * NS_PER_US)); // finding COM-22: 40-50 ms
        RunResult const before2 = runCommand(CTX_CAN, twoBuses, phase);
        CHECK(before2.maxUdsLatency > UDS_P2_NS); // P2 missed with two buses (or a longer output)
        if (phase == 0U)
        {
            std::printf(
                "  before COM-24: one bus task can blocked %.1f ms, UDS response up to %.1f ms; two buses "
                "%.1f ms / %.1f ms (P2 50 ms)\n",
                ms(before1.maxCanWait),
                ms(before1.maxUdsLatency),
                ms(before2.maxCanWait),
                ms(before2.maxUdsLatency));
        }
    }
}

} // namespace

int main()
{
    testStatusText();
    testContextCall();
    testTiming();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
