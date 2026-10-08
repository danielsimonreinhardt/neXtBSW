#pragma once

#include <cstddef>
#include <cstdint>

/**
 * Console command "rte" in the console context (SYS-13): what RteSystem prints, split into the snapshot that is
 * taken in the task that owns the values and the text that is printed afterwards in the console context.
 *
 * Until SYS-13 "rte signals|runnables|json" ran completely in the first RTE application task (demo: TASK_SWC,
 * 10 ms) and wrote to the UART there, 86.8 us per character: the runnables of that task stood still for several
 * cycles, and the statistics the command printed - overruns, max times, the activation jitter tools/schedana
 * reads through "rte json" - measured the command itself. Now the command runs in TASK_IDLE; per task one
 * snapshot of its cycle statistics and of the statistics of its runnables is taken in that task's own context
 * (TASKS[t].context), so each task waits for its own snapshot only, and the values of one task are consistent.
 *
 * Independent of the generated RTE and of OpenBSW: the text is written through any object with a printf member
 * (util::format::SharedStringWriter on the target), the values come from a Source (RteSystem.cpp on the target,
 * a test profile in the host test libs/bsw/consoleContext/test/host/RteConsoleHostTest.cpp), and the call into
 * the owner is a callable runIn(context, work) (::console::runInOwnerContext on the target).
 * The text is the one before SYS-13, character by character.
 */
namespace systems
{
namespace rteconsole
{

/** Cycle statistics of one application task, copied in its own context. */
struct TaskSnapshot
{
    char const* name;
    uint32_t context;
    uint32_t periodMs;
    uint32_t cycles;
    uint32_t overruns;
    uint32_t minUs;
    uint32_t meanUs;
    uint32_t maxUs;
    uint32_t minDeltaUs;
    uint32_t maxDeltaUs;
};

/** Statistics of one runnable, copied in the context of its task (init runnables: the first task). */
struct RunnableSnapshot
{
    char const* name;
    uint32_t periodMs;
    uint32_t offsetMs;
    uint32_t calls;
    uint32_t lastUs;
    uint32_t minUs;
    uint32_t meanUs;
    uint32_t maxUs;
};

/**
 * Statistics of one mode switch runnable (RTE-17, one row of rte::MODE_RUNNABLES: one event of the runnable),
 * copied in the context of its task. runnable.periodMs/offsetMs are not used (0).
 */
struct ModeSnapshot
{
    RunnableSnapshot runnable;
    char const* task;     // name of the task whose cycle start runs it
    char const* event;    // "entry", "exit" or "transition"
    char const* mode;     // ECU mode of the event (transition: the mode it enters)
    char const* fromMode; // transition: the mode it leaves, else ""
};

/** Value of one RTE signal, copied in the owner's context and printed as before (printf without float). */
struct ValueSnapshot
{
    enum class Kind : uint8_t
    {
        UNSIGNED,
        SIGNED,
        MILLI, // FLOAT32 as thousandths
        UNKNOWN,
    };

    Kind kind;
    uint32_t u;
    int32_t s;
};

#ifdef NX_RTE_HAS_SIGNAL_SCALING
/**
 * RTE-29: conversion of a fixed point signal (a row of rte::SIGNAL_SCALINGS): physical = (offset + factor * stored)
 * / denominator, printed with `decimals` decimals and the unit. denominator 0: not scaled, printed as before.
 * Only with NX_RTE_HAS_SIGNAL_SCALING (generated RteConfig.h): an ECU without fixed point signals builds as before.
 */
struct Scaling
{
    char const* unit    = nullptr;
    int32_t offset      = 0;
    int32_t factor      = 1;
    int32_t denominator = 0;
    uint8_t decimals    = 0U;
};
#endif

struct SignalSnapshot
{
    char const* name;
    char const* bsw; // connected BSW signals or nullptr
    ValueSnapshot value;
#ifdef NX_RTE_HAS_SIGNAL_SCALING
    Scaling scaling{}; // RTE-29: set by the source for every row (the snapshot buffer is reused)
#endif
};

/** Signals copied per call into the owner: bounds the time the owner spends in one snapshot. */
constexpr size_t SIGNALS_PER_SNAPSHOT = 16U;

/**
 * Copies the value of an RTE signal (rte::Signal: type, value pointer) - call it in the owner's context. A
 * template, so that the host test can use its own signal table with the enumerators of rte::SignalType.
 */
template<class Signal>
ValueSnapshot takeValue(Signal const& signal)
{
    using Type = decltype(signal.type);
    ValueSnapshot v{ValueSnapshot::Kind::UNKNOWN, 0U, 0};
    switch (signal.type)
    {
        case Type::BOOLEAN:
        case Type::UINT8:
            v.kind = ValueSnapshot::Kind::UNSIGNED;
            v.u    = *static_cast<uint8_t const*>(signal.value);
            break;
        case Type::SINT8:
            v.kind = ValueSnapshot::Kind::SIGNED;
            v.s    = *static_cast<int8_t const*>(signal.value);
            break;
        case Type::UINT16:
            v.kind = ValueSnapshot::Kind::UNSIGNED;
            v.u    = *static_cast<uint16_t const*>(signal.value);
            break;
        case Type::SINT16:
            v.kind = ValueSnapshot::Kind::SIGNED;
            v.s    = *static_cast<int16_t const*>(signal.value);
            break;
        case Type::UINT32:
            v.kind = ValueSnapshot::Kind::UNSIGNED;
            v.u    = *static_cast<uint32_t const*>(signal.value);
            break;
        case Type::SINT32:
            v.kind = ValueSnapshot::Kind::SIGNED;
            v.s    = *static_cast<int32_t const*>(signal.value);
            break;
        case Type::FLOAT32:
            // printf without float support: print thousandths as integer
            v.kind = ValueSnapshot::Kind::MILLI;
            v.s    = static_cast<int32_t>(*static_cast<float const*>(signal.value) * 1000.0F);
            break;
        default: break;
    }
    return v;
}

// Note: the OpenBSW printf treats the 'l' length modifier as 64 bit, so 32 bit values are printed with
// %u/%d (int is 32 bit on the target).
template<class Out>
void writeValue(Out& out, ValueSnapshot const& v)
{
    switch (v.kind)
    {
        case ValueSnapshot::Kind::UNSIGNED: (void)out.printf("%u", static_cast<unsigned>(v.u)); break;
        case ValueSnapshot::Kind::SIGNED: (void)out.printf("%d", static_cast<int>(v.s)); break;
        case ValueSnapshot::Kind::MILLI: (void)out.printf("%d e-3", static_cast<int>(v.s)); break;
        default: (void)out.printf("?"); break;
    }
}

#ifdef NX_RTE_HAS_SIGNAL_SCALING
/**
 * RTE-29: decimal text of (offset + factor * stored) / denominator with `decimals` decimals, rounded half away from
 * zero, without float and without 64 bit printf. tools/rtegen (signal_scalings) guarantees that the product fits
 * int64_t over the whole stored range; text needs PHYSICAL_TEXT bytes.
 */
constexpr size_t PHYSICAL_TEXT = 24U; // sign, 19 digits, point, terminator

inline char const* physicalText(char (&text)[PHYSICAL_TEXT], int64_t const stored, Scaling const& s)
{
    int64_t pow10 = 1;
    for (uint8_t i = 0U; i < s.decimals; ++i)
    {
        pow10 *= 10;
    }
    int64_t const n = (static_cast<int64_t>(s.offset) + (static_cast<int64_t>(s.factor) * stored)) * pow10;
    int64_t const d = static_cast<int64_t>(s.denominator);
    int64_t const q = (n >= 0) ? ((n + (d / 2)) / d) : -(((-n) + (d / 2)) / d);
    uint64_t magnitude = (q < 0) ? (static_cast<uint64_t>(-(q + 1)) + 1U) : static_cast<uint64_t>(q);
    size_t pos         = PHYSICAL_TEXT - 1U;
    text[pos]          = '\0';
    unsigned digits    = 0U;
    do
    {
        text[--pos] = static_cast<char>('0' + static_cast<int>(magnitude % 10U));
        magnitude /= 10U;
        ++digits;
        if ((s.decimals != 0U) && (digits == s.decimals))
        {
            text[--pos] = '.';
        }
    } while ((magnitude != 0U) || (digits <= s.decimals));
    if (q < 0)
    {
        text[--pos] = '-';
    }
    return &text[pos];
}
#endif

template<class Out>
void writeSignalLine(Out& out, SignalSnapshot const& s)
{
    (void)out.printf("%s = ", s.name);
#ifdef NX_RTE_HAS_SIGNAL_SCALING
    if ((s.scaling.denominator > 0)
        && ((s.value.kind == ValueSnapshot::Kind::UNSIGNED) || (s.value.kind == ValueSnapshot::Kind::SIGNED)))
    {
        // RTE-29: "<physical>[ <unit>] [<stored>]"
        bool const isUnsigned = (s.value.kind == ValueSnapshot::Kind::UNSIGNED);
        int64_t const stored  = isUnsigned ? static_cast<int64_t>(s.value.u) : static_cast<int64_t>(s.value.s);
        char text[PHYSICAL_TEXT];
        bool const hasUnit = (s.scaling.unit != nullptr) && (s.scaling.unit[0] != '\0');
        (void)out.printf("%s%s%s [", physicalText(text, stored, s.scaling), hasUnit ? " " : "",
                         hasUnit ? s.scaling.unit : "");
        writeValue(out, s.value);
        (void)out.printf("]");
    }
    else
#endif
    {
        writeValue(out, s.value);
    }
    (void)out.printf("%s%s\r\n", (s.bsw != nullptr) ? "  <-> " : "", (s.bsw != nullptr) ? s.bsw : "");
}

template<class Out>
void writeTaskLine(Out& out, TaskSnapshot const& t)
{
    (void)out.printf(
        "task %s (context %u): every %u ms, cycles %u, overruns %u, max %u us\r\n",
        t.name,
        static_cast<unsigned>(t.context),
        static_cast<unsigned>(t.periodMs),
        static_cast<unsigned>(t.cycles),
        static_cast<unsigned>(t.overruns),
        static_cast<unsigned>(t.maxUs));
}

template<class Out>
void writeRunnableLine(Out& out, RunnableSnapshot const& r)
{
    (void)out.printf(
        "  %s: every %u ms, offset %u ms, calls %u, last %u us, max %u us\r\n",
        r.name,
        static_cast<unsigned>(r.periodMs),
        static_cast<unsigned>(r.offsetMs),
        static_cast<unsigned>(r.calls),
        static_cast<unsigned>(r.lastUs),
        static_cast<unsigned>(r.maxUs));
}

template<class Out>
void writeInitLine(Out& out, RunnableSnapshot const& r)
{
    (void)out.printf(
        "init %s: calls %u, last %u us\r\n",
        r.name,
        static_cast<unsigned>(r.calls),
        static_cast<unsigned>(r.lastUs));
}

// One JSON object per line: a lost line (IO-17) only costs that one value, and the header plus the end marker
// tell the reader whether lines are missing. Parser: tools/schedana/measure.py.
template<class Out>
void writeJsonTask(Out& out, TaskSnapshot const& t)
{
    (void)out.printf(
        "{\"t\":\"task\",\"name\":\"%s\",\"context\":%u,\"period_ms\":%u,\"cycles\":%u",
        t.name,
        static_cast<unsigned>(t.context),
        static_cast<unsigned>(t.periodMs),
        static_cast<unsigned>(t.cycles));
    (void)out.printf(
        ",\"overruns\":%u,\"min_us\":%u,\"mean_us\":%u,\"max_us\":%u",
        static_cast<unsigned>(t.overruns),
        static_cast<unsigned>(t.minUs),
        static_cast<unsigned>(t.meanUs),
        static_cast<unsigned>(t.maxUs));
    (void)out.printf(
        ",\"min_delta_us\":%u,\"max_delta_us\":%u}\r\n",
        static_cast<unsigned>(t.minDeltaUs),
        static_cast<unsigned>(t.maxDeltaUs));
}

template<class Out>
void writeJsonRunnable(Out& out, char const* const taskName, RunnableSnapshot const& r)
{
    (void)out.printf(
        "{\"t\":\"runnable\",\"task\":\"%s\",\"name\":\"%s\",\"period_ms\":%u,\"offset_ms\":%u",
        taskName,
        r.name,
        static_cast<unsigned>(r.periodMs),
        static_cast<unsigned>(r.offsetMs));
    (void)out.printf(
        ",\"calls\":%u,\"last_us\":%u,\"min_us\":%u,\"mean_us\":%u,\"max_us\":%u}\r\n",
        static_cast<unsigned>(r.calls),
        static_cast<unsigned>(r.lastUs),
        static_cast<unsigned>(r.minUs),
        static_cast<unsigned>(r.meanUs),
        static_cast<unsigned>(r.maxUs));
}

template<class Out>
void writeJsonInit(Out& out, RunnableSnapshot const& r)
{
    (void)out.printf(
        "{\"t\":\"init\",\"name\":\"%s\",\"calls\":%u,\"last_us\":%u,\"min_us\":%u,\"mean_us\":%u,"
        "\"max_us\":%u}\r\n",
        r.name,
        static_cast<unsigned>(r.calls),
        static_cast<unsigned>(r.lastUs),
        static_cast<unsigned>(r.minUs),
        static_cast<unsigned>(r.meanUs),
        static_cast<unsigned>(r.maxUs));
}

/**
 * Source of the values (RteSystem.cpp, host test):
 *   size_t taskCount() const;
 *   uint32_t taskContext(size_t task) const;           context that owns the statistics of the task
 *   size_t runnableCount(size_t task) const;
 *   void takeTask(size_t task, TaskSnapshot&, RunnableSnapshot* runnables);   runs in taskContext(task)
 *   size_t initCount() const;
 *   void takeInit(RunnableSnapshot* runnables);        init runnables, runs in taskContext(0)
 * \p runIn(context, work) runs work in that context and returns when it is done. \p buffer holds the runnables of the largest task and all init runnables.
 */
enum class Format : uint8_t
{
    TEXT, // "rte runnables"
    JSON, // "rte json", after the header line
};

template<class Out, class Source, class RunIn>
void writeStatistics(Out& out, Source& source, RunIn&& runIn, RunnableSnapshot* const buffer, Format const format)
{
    for (size_t t = 0U; t < source.taskCount(); ++t)
    {
        TaskSnapshot task{};
        auto const take = [&source, t, &task, buffer]() { source.takeTask(t, task, buffer); };
        runIn(source.taskContext(t), take);
        if (format == Format::TEXT)
        {
            writeTaskLine(out, task);
        }
        else
        {
            writeJsonTask(out, task);
        }
        for (size_t i = 0U; i < source.runnableCount(t); ++i)
        {
            if (format == Format::TEXT)
            {
                writeRunnableLine(out, buffer[i]);
            }
            else
            {
                writeJsonRunnable(out, task.name, buffer[i]);
            }
        }
    }
    if ((source.initCount() != 0U) && (source.taskCount() != 0U))
    {
        auto const take = [&source, buffer]() { source.takeInit(buffer); };
        runIn(source.taskContext(0U), take);
        for (size_t i = 0U; i < source.initCount(); ++i)
        {
            if (format == Format::TEXT)
            {
                writeInitLine(out, buffer[i]);
            }
            else
            {
                writeJsonInit(out, buffer[i]);
            }
        }
    }
}

// RTE-17: one line per mode switch event. "mode" at column 0 (not indented like a periodic runnable: it belongs to
// no period), the event as the RTE runs it: "on entry RUN", "on exit RUN", "on transition STANDBY -> RUN".
// Up to 77 characters plus runnable name, task name and the numbers (tools/schedana/measure.py parses it).
template<class Out>
void writeModeLine(Out& out, ModeSnapshot const& m)
{
    bool const transition = (m.fromMode != nullptr) && (m.fromMode[0] != '\0');
    (void)out.printf(
        "mode %s (task %s): on %s %s%s%s, calls %u, last %u us, max %u us\r\n",
        m.runnable.name,
        m.task,
        m.event,
        transition ? m.fromMode : "",
        transition ? " -> " : "",
        m.mode,
        static_cast<unsigned>(m.runnable.calls),
        static_cast<unsigned>(m.runnable.lastUs),
        static_cast<unsigned>(m.runnable.maxUs));
}

template<class Out>
void writeJsonMode(Out& out, ModeSnapshot const& m)
{
    (void)out.printf(
        "{\"t\":\"mode\",\"task\":\"%s\",\"name\":\"%s\",\"event\":\"%s\",\"mode\":\"%s\",\"from\":\"%s\"",
        m.task,
        m.runnable.name,
        m.event,
        m.mode,
        (m.fromMode != nullptr) ? m.fromMode : "");
    (void)out.printf(
        ",\"calls\":%u,\"last_us\":%u,\"min_us\":%u,\"mean_us\":%u,\"max_us\":%u}\r\n",
        static_cast<unsigned>(m.runnable.calls),
        static_cast<unsigned>(m.runnable.lastUs),
        static_cast<unsigned>(m.runnable.minUs),
        static_cast<unsigned>(m.runnable.meanUs),
        static_cast<unsigned>(m.runnable.maxUs));
}

/**
 * Mode switch runnables (RTE-17) after writeStatistics, task by task, each task's rows copied in its own context:
 *   size_t taskCount() const;  uint32_t taskContext(size_t task) const;   as for writeStatistics
 *   size_t modeCount(size_t task) const;                   rows of rte::MODE_RUNNABLES of the task
 *   void takeModes(size_t task, ModeSnapshot* modes);      runs in taskContext(task)
 * \p buffer holds the rows of the task with the most rows. A separate function (not part of writeStatistics), so
 * that a Source without mode switch runnables needs nothing new.
 */
template<class Out, class Source, class RunIn>
void writeModeStatistics(Out& out, Source& source, RunIn&& runIn, ModeSnapshot* const buffer, Format const format)
{
    for (size_t t = 0U; t < source.taskCount(); ++t)
    {
        size_t const count = source.modeCount(t);
        if (count == 0U)
        {
            continue;
        }
        auto const take = [&source, t, buffer]() { source.takeModes(t, buffer); };
        runIn(source.taskContext(t), take);
        for (size_t i = 0U; i < count; ++i)
        {
            if (format == Format::TEXT)
            {
                writeModeLine(out, buffer[i]);
            }
            else
            {
                writeJsonMode(out, buffer[i]);
            }
        }
    }
}

/**
 * Signal source:
 *   size_t signalCount() const;
 *   uint32_t signalContext() const;                        context the signal buffers are read in
 *   void takeSignals(size_t first, size_t count, SignalSnapshot*);   runs in signalContext()
 */
template<class Out, class Source, class RunIn>
void writeSignals(Out& out, Source& source, RunIn&& runIn)
{
    SignalSnapshot chunk[SIGNALS_PER_SNAPSHOT];
    for (size_t first = 0U; first < source.signalCount(); first += SIGNALS_PER_SNAPSHOT)
    {
        size_t const left  = source.signalCount() - first;
        size_t const count = (left < SIGNALS_PER_SNAPSHOT) ? left : SIGNALS_PER_SNAPSHOT;
        auto const take    = [&source, first, count, &chunk]() { source.takeSignals(first, count, chunk); };
        runIn(source.signalContext(), take);
        for (size_t i = 0U; i < count; ++i)
        {
            writeSignalLine(out, chunk[i]);
        }
    }
}

} // namespace rteconsole
} // namespace systems
