/* Scheduler model for the host tests of the console context (SYS-13), shared by every *ConsoleHostTest.cpp here.
 *
 * Fixed priorities, preemptive, time in ns. A context number is its priority (as in the async binding: the
 * FreeRTOS priority equals the context, TASK_IDLE = 0 is the lowest). Periodic tasks release one job per period;
 * a job of a higher context preempts whatever runs below it. The console runs in CTX_IDLE. A UART writer charges
 * the writing context 86.8 us per character (bsp::Uart::write waits for TDRE at 115200 Bd), so a command that
 * prints in the task of its system holds that task for the whole output.
 *
 * The command code in the tests is the real code of the targets (the snapshot/print split of RteConsole.h,
 * SbcConsole.h, EcuStateConsole.h, LifecycleConsole.h and ::console::ContextCall) - only the execution of a
 * runnable in another context (::async::execute) and the UART are modelled. A command can be run two ways:
 *  - the SYS-13 way: registered in CTX_IDLE, the snapshots go through ModelCall into the owner
 *  - the old way (the deliberately inserted fault): the whole command, output included, in the owner's context
 * No target hardware needed.
 */
#pragma once

#include "console/ContextCall.h"

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace consolemodel
{

inline int failures = 0;
inline int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++::consolemodel::checks;                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++::consolemodel::failures;                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

constexpr uint8_t CTX_IDLE = 0U;

constexpr uint64_t NS_PER_US    = 1000U;
constexpr uint64_t NS_PER_MS    = 1000000U;
constexpr uint64_t CHARACTER_NS = 86806U; // 10 bit at 115200 Bd

inline double ms(uint64_t const ns) { return static_cast<double>(ns) / 1.0e6; }

/* ---------------------------------------------------------------- text output with printf */

class TextWriter
{
public:
    virtual ~TextWriter() = default;

    __attribute__((format(printf, 2, 3))) TextWriter& printf(char const* const format, ...)
    {
        char buffer[1024];
        va_list ap;
        va_start(ap, format);
        int const n = std::vsnprintf(buffer, sizeof(buffer), format, ap);
        va_end(ap);
        CHECK((n >= 0) && (static_cast<size_t>(n) < sizeof(buffer))); // no line of a test is cut
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

inline size_t countLines(std::string const& text)
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

/* ---------------------------------------------------------------- scheduler */

struct TaskSpec
{
    char const* name;
    uint8_t context; // priority
    uint64_t period;
    uint64_t phase; // first release
    uint64_t cost;  // execution time of one job (without preemption)
};

struct TaskStats
{
    uint32_t jobs            = 0U;
    uint64_t maxWait         = 0U; // release -> start
    uint64_t maxResponse     = 0U; // release -> end
    uint64_t maxStartDelta   = 0U; // start -> next start (activation jitter, tools/schedana max_delta_us)
    uint64_t lastStart       = 0U;
    uint64_t consoleBusy     = 0U; // time this context spent on console work (snapshots or, the old way, output)
    uint64_t consoleChars    = 0U; // characters this context wrote to the UART
};

class Model
{
public:
    Model(std::vector<TaskSpec> const& tasks, uint64_t const horizon) : _tasks(tasks), _horizon(horizon)
    {
        for (TaskSpec const& t : _tasks)
        {
            _next.push_back(t.phase);
            _stats.push_back(TaskStats());
        }
    }

    uint64_t now() const { return _now; }

    uint8_t current() const { return _current; }

    void setCurrent(uint8_t const context) { _current = context; }

    TaskStats const& stats(size_t const task) const { return _stats[task]; }

    TaskStats const* statsOf(uint8_t const context) const
    {
        for (size_t i = 0U; i < _tasks.size(); ++i)
        {
            if (_tasks[i].context == context)
            {
                return &_stats[i];
            }
        }
        return nullptr;
    }

    /** The running context uses the CPU for \p duration; released jobs of higher contexts preempt it. */
    void consume(uint64_t const duration)
    {
        uint64_t remaining = duration;
        while (true)
        {
            size_t const ready = readyAbove(_current);
            if (ready < _tasks.size())
            {
                runJob(ready);
                continue;
            }
            if (remaining == 0U)
            {
                break;
            }
            uint64_t const release = nextReleaseAbove(_current);
            if (release < (_now + remaining))
            {
                remaining -= (release - _now);
                _now = release;
            }
            else
            {
                _now += remaining;
                remaining = 0U;
            }
        }
    }

    /** Console work that is not the UART, charged to the current context (e.g. one snapshot). */
    void work(uint64_t const duration)
    {
        addConsoleBusy(duration);
        consume(duration);
    }

    /** One character to the UART from the current context. */
    void character()
    {
        TaskStats* const s = mutableStatsOf(_current);
        if (s != nullptr)
        {
            ++s->consoleChars;
        }
        addConsoleBusy(CHARACTER_NS);
        consume(CHARACTER_NS);
    }

    /** Idles until \p until (jobs keep running). */
    void idleUntil(uint64_t const until)
    {
        uint8_t const saved = _current;
        _current            = CTX_IDLE;
        if (until > _now)
        {
            consume(until - _now);
        }
        _current = saved;
    }

    /** Runs the rest of the horizon. */
    void finish() { idleUntil(_horizon); }

private:
    size_t readyAbove(uint8_t const context) const
    {
        size_t best = _tasks.size();
        for (size_t i = 0U; i < _tasks.size(); ++i)
        {
            if ((_tasks[i].context > context) && (_next[i] <= _now) && (_next[i] < _horizon)
                && ((best == _tasks.size()) || (_tasks[i].context > _tasks[best].context)))
            {
                best = i;
            }
        }
        return best;
    }

    uint64_t nextReleaseAbove(uint8_t const context) const
    {
        uint64_t release = UINT64_MAX;
        for (size_t i = 0U; i < _tasks.size(); ++i)
        {
            if ((_tasks[i].context > context) && (_next[i] < release) && (_next[i] < _horizon))
            {
                release = _next[i];
            }
        }
        return release;
    }

    void runJob(size_t const i)
    {
        uint64_t const release = _next[i];
        _next[i] += _tasks[i].period;
        TaskStats& s         = _stats[i];
        uint64_t const start = _now;
        if ((start - release) > s.maxWait)
        {
            s.maxWait = start - release;
        }
        if ((s.jobs != 0U) && ((start - s.lastStart) > s.maxStartDelta))
        {
            s.maxStartDelta = start - s.lastStart;
        }
        s.lastStart         = start;
        uint8_t const saved = _current;
        _current            = _tasks[i].context;
        consume(_tasks[i].cost);
        _current = saved;
        if ((_now - release) > s.maxResponse)
        {
            s.maxResponse = _now - release;
        }
        ++s.jobs;
    }

    TaskStats* mutableStatsOf(uint8_t const context)
    {
        for (size_t i = 0U; i < _tasks.size(); ++i)
        {
            if (_tasks[i].context == context)
            {
                return &_stats[i];
            }
        }
        return nullptr;
    }

    void addConsoleBusy(uint64_t const duration)
    {
        TaskStats* const s = mutableStatsOf(_current);
        if (s != nullptr)
        {
            s->consoleBusy += duration;
        }
    }

    std::vector<TaskSpec> _tasks;
    std::vector<uint64_t> _next;
    std::vector<TaskStats> _stats;
    uint64_t _horizon;
    uint64_t _now     = 0U;
    uint8_t _current  = CTX_IDLE;
};

inline Model* model = nullptr;

struct ModelRunnable
{
    virtual void execute() = 0;

protected:
    ~ModelRunnable() = default;
};

/**
 * ::async::execute in the model: the queued runnable runs in \p context. The owner is above the console, so it
 * preempts the console right away (jobs that were released before already ran: the console is the lowest
 * context); jobs of the owner released while the runnable runs come after it, as in the owner's queue.
 */
inline void modelExecute(uint8_t const context, ModelRunnable& runnable)
{
    uint8_t const caller = model->current();
    CHECK(context > caller); // the precondition of ContextCall: the owner is above the caller
    model->setCurrent(context);
    runnable.execute();
    model->setCurrent(caller);
    model->consume(0U); // jobs released meanwhile run before the console continues
}

using ModelCall = ::console::ContextCall<ModelRunnable, uint8_t, &modelExecute>;

/** runIn(context, work) of the console headers, through ModelCall like ::console::runInOwnerContext. */
struct ModelRunIn
{
    ModelCall* call;

    template<class Work>
    void operator()(uint32_t const context, Work& work) const
    {
        if (model->current() != call->callerContext())
        {
            work(); // not from the console (the old way: the command runs in the owner): inline, as on the target
            return;
        }
        CHECK(call->run(static_cast<uint8_t>(context), work));
    }
};

/* The console UART: every character costs the writing context 86.8 us. */
class UartOut : public TextWriter
{
public:
    std::string text;

protected:
    void put(std::string const& t) override
    {
        for (char const c : t)
        {
            text += c;
            model->character();
        }
    }
};

/**
 * Runs a command at \p at: \p commandContext is the context the AsyncCommandWrapper runs it in (SYS-13: CTX_IDLE,
 * before: the system's own context), then "ok" and the prompt in the same context (AsyncConsole::terminate).
 */
template<class Command>
std::string runCommand(Model& m, uint64_t const at, uint8_t const commandContext, Command&& command)
{
    m.idleUntil(at);
    m.setCurrent(commandContext);
    m.consume(0U); // the wrapper's runnable queues behind what that context has to do anyway
    UartOut out;
    command(out);
    (void)out.printf("ok\r\n> ");
    m.setCurrent(CTX_IDLE);
    m.consume(0U);
    return out.text;
}

inline int report(char const* const name)
{
    std::printf("%s: %d checks, %d failures\n", name, checks, failures);
    return (failures == 0) ? 0 : 1;
}

} // namespace consolemodel
