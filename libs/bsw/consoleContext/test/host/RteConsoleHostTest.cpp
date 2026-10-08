/* Host test of the console command "rte" in the console context (SYS-13, ecu/application/src/systems/RteConsole.h).
 *
 * Own test profile (not the eval board): two application tasks with free priorities - "swc" (context 5, 10 ms,
 * three runnables) and "fast" (context 6, 1 ms, one runnable) - one init runnable and 40 signals of every type.
 *  1. Text: "rte runnables", "rte json" and "rte signals" are the text before SYS-13 (golden lines).
 *  2. Ownership: the statistics of a task are copied in that task's context, the init runnables and the
 *     signals in the first task's; the signals in chunks of SIGNALS_PER_SNAPSHOT.
 *  3. Timing (ConsoleModel.h): "rte json" in the console context - each task waits at most for its own snapshot,
 *     the activation jitter the command prints (max_delta_us) stays at the period. Deliberately inserted fault,
 *     the old way (command in the first task, TASKS[0].context): task swc stands still for the whole output, its
 *     jitter grows by the output time - the statistics measure the command itself.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/consoleContext/include -Iecu/application/src/systems \
 *       libs/bsw/consoleContext/test/host/RteConsoleHostTest.cpp -o build/host/rte_console_test.exe
 *   build/host/rte_console_test.exe
 */

// RTE-29: as a generated RteConfig.h with fixed point signals (rte::SIGNAL_SCALINGS); the profile below has none, so
// its text stays the golden text, testScaling() prints scaled rows directly
#define NX_RTE_HAS_SIGNAL_SCALING 1

#include "ConsoleModel.h"
#include "RteConsole.h"

using namespace consolemodel;
namespace rc = ::systems::rteconsole;

namespace
{
constexpr uint8_t CTX_SWC  = 5U;
constexpr uint8_t CTX_FAST = 6U;

constexpr uint64_t SWC_PERIOD  = 10U * NS_PER_MS;
constexpr uint64_t SWC_COST    = 400U * NS_PER_US;
constexpr uint64_t FAST_PERIOD = 1U * NS_PER_MS;
constexpr uint64_t FAST_COST   = 30U * NS_PER_US;
constexpr uint64_t SNAPSHOT    = 20U * NS_PER_US; // one task with its runnables, generous for 80 MHz

/* ---------------------------------------------------------------- test profile */

enum class SignalType : uint8_t
{
    BOOLEAN,
    UINT8,
    SINT8,
    UINT16,
    SINT16,
    UINT32,
    SINT32,
    FLOAT32,
    FLOAT64,
};

struct Signal
{
    char const* name;
    char const* bsw;
    SignalType type;
    void const* value;
};

bool vBool       = true;
uint8_t vU8      = 200U;
int8_t vS8       = -100;
uint16_t vU16    = 65535U;
int16_t vS16     = -32768;
uint32_t vU32    = 4294967295U;
int32_t vS32     = -2147483647;
float vF32       = 1.5F;
double vF64      = 2.0;
float vF32Neg    = -0.25F;

constexpr size_t SIGNALS = 40U;

struct Profile
{
    Signal signals[SIGNALS];
    std::string names[SIGNALS];

    Profile()
    {
        void const* const values[] = {&vBool, &vU8, &vS8, &vU16, &vS16, &vU32, &vS32, &vF32, &vF64, &vF32Neg};
        SignalType const types[]   = {SignalType::BOOLEAN,
                                      SignalType::UINT8,
                                      SignalType::SINT8,
                                      SignalType::UINT16,
                                      SignalType::SINT16,
                                      SignalType::UINT32,
                                      SignalType::SINT32,
                                      SignalType::FLOAT32,
                                      SignalType::FLOAT64,
                                      SignalType::FLOAT32};
        for (size_t i = 0U; i < SIGNALS; ++i)
        {
            names[i]   = "Swc.Port" + std::to_string(i) + ".Element";
            signals[i] = Signal{names[i].c_str(), ((i % 3U) == 0U) ? "DIO_X" : nullptr, types[i % 10U], values[i % 10U]};
        }
    }
};

Profile profile;

char const* const VALUE_TEXT[] = {
    "1", "200", "-100", "65535", "-32768", "4294967295", "-2147483647", "1500 e-3", "?", "-250 e-3"};

struct TaskData
{
    char const* name;
    uint8_t context;
    uint32_t periodMs;
    size_t first;
    size_t count;
};

TaskData const TASKS[] = {{"swc", CTX_SWC, 10U, 0U, 3U}, {"fast", CTX_FAST, 1U, 3U, 1U}};
rc::RunnableSnapshot const RUNNABLES[] = {
    {"Swc.Run10ms", 10U, 0U, 1234U, 120U, 90U, 110U, 300U},
    {"Swc.Run100ms", 100U, 20U, 123U, 250U, 240U, 245U, 260U},
    {"Swc.Run50ms", 50U, 0U, 247U, 5U, 4U, 5U, 4294967295U},
    {"Fast.Run1ms", 1U, 0U, 12345U, 12U, 10U, 11U, 17U},
    {"Swc.Init", 0U, 0U, 1U, 55U, 55U, 55U, 55U},
};
rc::TaskSnapshot const TASK_VALUES[] = {
    {"swc", CTX_SWC, 10U, 1234U, 0U, 380U, 395U, 812U, 9990U, 10020U},
    {"fast", CTX_FAST, 1U, 12345U, 2U, 25U, 28U, 1100U, 998U, 1003U},
};

/** Source of RteConsole.h over the test profile; records in which context each part was taken. */
class Source
{
public:
    size_t taskCount() const { return 2U; }

    uint32_t taskContext(size_t const t) const { return TASKS[t].context; }

    size_t runnableCount(size_t const t) const { return TASKS[t].count; }

    size_t initCount() const { return 1U; }

    void takeTask(size_t const t, rc::TaskSnapshot& task, rc::RunnableSnapshot* const runnables)
    {
        takenIn.push_back(model->current());
        expected.push_back(TASKS[t].context);
        if (model != nullptr)
        {
            model->work(SNAPSHOT);
        }
        task = TASK_VALUES[t];
        for (size_t i = 0U; i < TASKS[t].count; ++i)
        {
            runnables[i] = RUNNABLES[TASKS[t].first + i];
        }
    }

    void takeInit(rc::RunnableSnapshot* const runnables)
    {
        takenIn.push_back(model->current());
        expected.push_back(TASKS[0].context);
        model->work(SNAPSHOT);
        runnables[0] = RUNNABLES[4];
    }

    size_t signalCount() const { return SIGNALS; }

    uint32_t signalContext() const { return TASKS[0].context; }

    void takeSignals(size_t const first, size_t const count, rc::SignalSnapshot* const signals)
    {
        takenIn.push_back(model->current());
        expected.push_back(TASKS[0].context);
        chunks.push_back(count);
        model->work(SNAPSHOT);
        for (size_t i = 0U; i < count; ++i)
        {
            Signal const& s  = profile.signals[first + i];
            signals[i].name  = s.name;
            signals[i].bsw   = s.bsw;
            signals[i].value = rc::takeValue(s);
        }
    }

    std::vector<uint8_t> takenIn;
    std::vector<uint8_t> expected;
    std::vector<size_t> chunks;
};

/* ---------------------------------------------------------------- golden text (format strings before SYS-13) */

std::string const RUNNABLES_TEXT
    = "task swc (context 5): every 10 ms, cycles 1234, overruns 0, max 812 us\r\n"
      "  Swc.Run10ms: every 10 ms, offset 0 ms, calls 1234, last 120 us, max 300 us\r\n"
      "  Swc.Run100ms: every 100 ms, offset 20 ms, calls 123, last 250 us, max 260 us\r\n"
      "  Swc.Run50ms: every 50 ms, offset 0 ms, calls 247, last 5 us, max 4294967295 us\r\n"
      "task fast (context 6): every 1 ms, cycles 12345, overruns 2, max 1100 us\r\n"
      "  Fast.Run1ms: every 1 ms, offset 0 ms, calls 12345, last 12 us, max 17 us\r\n"
      "init Swc.Init: calls 1, last 55 us\r\n";

std::string const JSON_TEXT
    = "{\"t\":\"task\",\"name\":\"swc\",\"context\":5,\"period_ms\":10,\"cycles\":1234,\"overruns\":0,\"min_us\":380,"
      "\"mean_us\":395,\"max_us\":812,\"min_delta_us\":9990,\"max_delta_us\":10020}\r\n"
      "{\"t\":\"runnable\",\"task\":\"swc\",\"name\":\"Swc.Run10ms\",\"period_ms\":10,\"offset_ms\":0,\"calls\":1234,"
      "\"last_us\":120,\"min_us\":90,\"mean_us\":110,\"max_us\":300}\r\n"
      "{\"t\":\"runnable\",\"task\":\"swc\",\"name\":\"Swc.Run100ms\",\"period_ms\":100,\"offset_ms\":20,\"calls\":123,"
      "\"last_us\":250,\"min_us\":240,\"mean_us\":245,\"max_us\":260}\r\n"
      "{\"t\":\"runnable\",\"task\":\"swc\",\"name\":\"Swc.Run50ms\",\"period_ms\":50,\"offset_ms\":0,\"calls\":247,"
      "\"last_us\":5,\"min_us\":4,\"mean_us\":5,\"max_us\":4294967295}\r\n"
      "{\"t\":\"task\",\"name\":\"fast\",\"context\":6,\"period_ms\":1,\"cycles\":12345,\"overruns\":2,\"min_us\":25,"
      "\"mean_us\":28,\"max_us\":1100,\"min_delta_us\":998,\"max_delta_us\":1003}\r\n"
      "{\"t\":\"runnable\",\"task\":\"fast\",\"name\":\"Fast.Run1ms\",\"period_ms\":1,\"offset_ms\":0,\"calls\":12345,"
      "\"last_us\":12,\"min_us\":10,\"mean_us\":11,\"max_us\":17}\r\n"
      "{\"t\":\"init\",\"name\":\"Swc.Init\",\"calls\":1,\"last_us\":55,\"min_us\":55,\"mean_us\":55,\"max_us\":55}\r\n";

std::string signalsText()
{
    std::string t;
    for (size_t i = 0U; i < SIGNALS; ++i)
    {
        t += profile.names[i] + " = " + VALUE_TEXT[i % 10U];
        t += ((i % 3U) == 0U) ? "  <-> DIO_X\r\n" : "\r\n";
    }
    return t;
}

/* ---------------------------------------------------------------- commands as composed by RteSystem */

enum class Cmd
{
    RUNNABLES,
    JSON,
    SIGNALS,
};

template<class Out>
void rteCommand(Cmd const cmd, Source& source, ModelCall& call, Out& out)
{
    rc::RunnableSnapshot buffer[4];
    switch (cmd)
    {
        case Cmd::RUNNABLES: rc::writeStatistics(out, source, ModelRunIn{&call}, buffer, rc::Format::TEXT); break;
        case Cmd::JSON: rc::writeStatistics(out, source, ModelRunIn{&call}, buffer, rc::Format::JSON); break;
        default: rc::writeSignals(out, source, ModelRunIn{&call}); break;
    }
}

struct Run
{
    std::string text;
    TaskStats swc;
    TaskStats fast;
    Source source;
};

Run runOnce(Cmd const cmd, uint8_t const commandContext, uint64_t const phase)
{
    Model m({{"swc", CTX_SWC, SWC_PERIOD, phase, SWC_COST}, {"fast", CTX_FAST, FAST_PERIOD, 0U, FAST_COST}},
            300U * NS_PER_MS);
    model = &m;
    ModelCall call(CTX_IDLE);
    Run r{};
    r.text = runCommand(
        m, 15U * NS_PER_MS, commandContext, [cmd, &r, &call](UartOut& out) { rteCommand(cmd, r.source, call, out); });
    m.finish();
    model  = nullptr;
    r.swc  = m.stats(0U);
    r.fast = m.stats(1U);
    return r;
}

void testText()
{
    std::printf("text and ownership\n");
    Run const runnables = runOnce(Cmd::RUNNABLES, CTX_IDLE, 0U);
    CHECK(runnables.text == (RUNNABLES_TEXT + "ok\r\n> "));
    // task swc in its context, task fast in its own, the init runnables in the first task's
    CHECK(runnables.source.takenIn == (std::vector<uint8_t>{CTX_SWC, CTX_FAST, CTX_SWC}));
    CHECK(runnables.source.takenIn == runnables.source.expected);

    Run const json = runOnce(Cmd::JSON, CTX_IDLE, 0U);
    CHECK(json.text == (JSON_TEXT + "ok\r\n> "));
    CHECK(json.source.takenIn == runnables.source.takenIn);

    Run const signals = runOnce(Cmd::SIGNALS, CTX_IDLE, 0U);
    CHECK(signals.text == (signalsText() + "ok\r\n> "));
    CHECK(signals.source.chunks == (std::vector<size_t>{16U, 16U, 8U})); // SIGNALS_PER_SNAPSHOT per call
    CHECK(signals.source.takenIn == signals.source.expected);
    CHECK(rc::SIGNALS_PER_SNAPSHOT == 16U);
}

void testTiming()
{
    std::printf("timing (swc 10 ms / 400 us at context 5, fast 1 ms / 30 us at context 6)\n");
    // Reference without a command: the waits and the jitter the tasks have anyway.
    Model idle({{"swc", CTX_SWC, SWC_PERIOD, 0U, SWC_COST}, {"fast", CTX_FAST, FAST_PERIOD, 0U, FAST_COST}},
               300U * NS_PER_MS);
    idle.finish();
    TaskStats const base = idle.stats(0U);

    for (uint64_t phase = 0U; phase < SWC_PERIOD; phase += 1250U * NS_PER_US)
    {
        for (Cmd const cmd : {Cmd::RUNNABLES, Cmd::JSON, Cmd::SIGNALS})
        {
            Run const now = runOnce(cmd, CTX_IDLE, phase);
            CHECK(countLines(now.text) >= 7U);
            CHECK(now.swc.consoleChars == 0U);  // no task of the RTE writes to the UART
            CHECK(now.fast.consoleChars == 0U);
            // each task waits at most for its own snapshots (one per call), independent of the output length
            CHECK(now.swc.maxWait <= (base.maxWait + SNAPSHOT + FAST_COST));
            CHECK(now.fast.maxWait <= SNAPSHOT);
            CHECK(now.swc.maxStartDelta <= (SWC_PERIOD + SNAPSHOT + (2U * FAST_COST)));
            CHECK(now.swc.jobs == 30U);

            Run const old = runOnce(cmd, CTX_SWC, phase); // deliberately inserted fault: before SYS-13
            CHECK(old.text == now.text);
            CHECK(old.swc.consoleChars == old.text.size());
            CHECK(old.swc.maxWait >= (2U * SWC_PERIOD)); // several cycles of the runnables stand still
            CHECK(old.swc.maxStartDelta >= (3U * SWC_PERIOD));
            if ((phase == 0U) && (cmd == Cmd::JSON))
            {
                std::printf(
                    "  rte json, %u characters: SYS-13 swc waits <= %.3f ms (without command %.3f ms), jitter %.3f "
                    "ms; old way swc waits %.1f ms, start distance %.1f ms (period 10 ms)\n",
                    static_cast<unsigned>(now.text.size()),
                    ms(now.swc.maxWait),
                    ms(base.maxWait),
                    ms(now.swc.maxStartDelta),
                    ms(old.swc.maxWait),
                    ms(old.swc.maxStartDelta));
            }
        }
    }
}
/* RTE-29: a fixed point row is printed "<physical>[ <unit>] [<stored>]", exactly, rounded half away from zero. */
std::string scaledLine(rc::ValueSnapshot const value, rc::Scaling const& scaling, char const* const bsw = nullptr)
{
    StringOut out;
    rc::SignalSnapshot s{"A.P.P", bsw, value};
    s.scaling = scaling;
    rc::writeSignalLine(out, s);
    return out.text;
}

void testScaling()
{
    std::printf("fixed point rows (RTE-29)\n");
    using K = rc::ValueSnapshot::Kind;
    rc::Scaling const temp{"degC", -400, 1, 10, 1U};   // (-400 + stored) / 10
    rc::Scaling const speed{"", 0, 1, 16, 4U};        // stored / 16, no unit
    CHECK(scaledLine({K::UNSIGNED, 610U, 0}, temp) == "A.P.P = 21.0 degC [610]\r\n");
    CHECK(scaledLine({K::UNSIGNED, 400U, 0}, temp) == "A.P.P = 0.0 degC [400]\r\n");
    CHECK(scaledLine({K::UNSIGNED, 395U, 0}, temp) == "A.P.P = -0.5 degC [395]\r\n");
    CHECK(scaledLine({K::UNSIGNED, 0U, 0}, temp, "rte.init") == "A.P.P = -40.0 degC [0]  <-> rte.init\r\n");
    CHECK(scaledLine({K::UNSIGNED, 65535U, 0}, temp) == "A.P.P = 6513.5 degC [65535]\r\n");
    CHECK(scaledLine({K::SIGNED, 0U, -16}, speed) == "A.P.P = -1.0000 [-16]\r\n");
    CHECK(scaledLine({K::SIGNED, 0U, 1}, speed) == "A.P.P = 0.0625 [1]\r\n");
    CHECK(scaledLine({K::SIGNED, 0U, -32768}, speed) == "A.P.P = -2048.0000 [-32768]\r\n");
    // not a finite decimal: 1/3 with 2 decimals, rounded half away from zero
    rc::Scaling const third{"V", 0, 1, 3, 2U};
    CHECK(scaledLine({K::SIGNED, 0U, 2}, third) == "A.P.P = 0.67 V [2]\r\n");
    CHECK(scaledLine({K::SIGNED, 0U, -2}, third) == "A.P.P = -0.67 V [-2]\r\n");
    // the extremes rtegen admits: uint32 * (2^31 - 1) without decimals, beyond 32 bit
    rc::Scaling const big{"", 0, 2147483647, 1, 0U};
    CHECK(scaledLine({K::UNSIGNED, 4294967295U, 0}, big) == "A.P.P = 9223372030412324865 [4294967295]\r\n");
    // a row without conversion (denominator 0) and a float row stay as before
    CHECK(scaledLine({K::UNSIGNED, 7U, 0}, rc::Scaling()) == "A.P.P = 7\r\n");
    CHECK(scaledLine({K::MILLI, 0U, 1500}, temp) == "A.P.P = 1500 e-3\r\n");
}
} // namespace

int main()
{
    testText();
    testScaling();
    testTiming();
    return report("rte-console");
}
