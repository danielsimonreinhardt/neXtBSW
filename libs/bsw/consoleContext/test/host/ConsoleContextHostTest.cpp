/* Host test of ::console::ContextCall (SYS-13, general form of COM-24's ::can::ContextCall).
 *
 *  1. Inline when the owner is the caller's own context, otherwise in the owner's context; the result is there
 *     when run() returns, calls keep their order, a nested call is refused, the type erased run() behaves the
 *     same, one call serves owners of any priority above the console.
 *  2. Timing (scheduler model ConsoleModel.h): an owner task with a 1 ms job and a console command with a long
 *     output. SYS-13 way: the owner waits at most for one snapshot. Old way (deliberately inserted fault: the
 *     command in the owner's context): the owner waits for the whole output.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/consoleContext/include libs/bsw/consoleContext/test/host/ConsoleContextHostTest.cpp \
 *       -o build/host/console_context_test.exe
 *   build/host/console_context_test.exe
 */

#include "ConsoleModel.h"

using namespace consolemodel;

namespace
{
constexpr uint8_t CTX_LOW  = 3U;
constexpr uint8_t CTX_HIGH = 7U;

void testContextCall()
{
    std::printf("context call\n");
    Model m({{"low", CTX_LOW, 1U * NS_PER_MS, 500U * NS_PER_US, 10U * NS_PER_US},
             {"high", CTX_HIGH, 1U * NS_PER_MS, 0U, 10U * NS_PER_US}},
            10U * NS_PER_MS);
    model = &m;

    // Owner = caller: inline, nothing queued.
    ModelCall inlineCall(CTX_LOW);
    m.setCurrent(CTX_LOW);
    int ran         = 0;
    auto const work = [&ran]() { ++ran; };
    CHECK(inlineCall.run(CTX_LOW, work));
    CHECK(ran == 1);
    CHECK(inlineCall.queuedCalls() == 0U);

    // Console below the owner: the work runs in the owner's context, its result is there when run() returns.
    ModelCall call(CTX_IDLE);
    CHECK(call.callerContext() == CTX_IDLE);
    m.setCurrent(CTX_IDLE);
    uint8_t seenIn  = 0xFFU;
    uint32_t value  = 0U;
    auto const read = [&seenIn, &value]()
    {
        seenIn = model->current();
        value  = 42U;
    };
    CHECK(call.run(CTX_LOW, read));
    CHECK(seenIn == CTX_LOW);
    CHECK(value == 42U);
    CHECK(m.current() == CTX_IDLE);
    CHECK(call.queuedCalls() == 1U);

    // The same call serves an owner of any priority above the console.
    CHECK(call.run(CTX_HIGH, read));
    CHECK(seenIn == CTX_HIGH);
    CHECK(call.queuedCalls() == 2U);

    // Calls run one after the other in the order of the command.
    std::string order;
    auto const a = [&order]() { order += 'a'; };
    auto const b = [&order]() { order += 'b'; };
    CHECK(call.run(CTX_LOW, a));
    CHECK(call.run(CTX_HIGH, b));
    CHECK(call.run(CTX_LOW, a));
    CHECK(order == "aba");

    // Type erased form, the one ::console::runInOwnerContext uses on the target.
    int erased         = 0;
    auto const count   = [&erased]() { erased += (model->current() == CTX_HIGH) ? 1 : 100; };
    CHECK(call.run(CTX_HIGH, ModelCall::erase(count), &ModelCall::invoke<decltype(count)>));
    CHECK(erased == 1);

    // No second call while one is in progress (the owner would run work of a caller that went on).
    bool nested      = true;
    int innerRuns    = 0;
    auto const inner = [&innerRuns]() { ++innerRuns; };
    auto const outer = [&call, &nested, &inner]() { nested = call.run(CTX_LOW, inner); };
    CHECK(call.run(CTX_LOW, outer));
    CHECK(!nested);
    CHECK(innerRuns == 0);
    // ... and the call is usable again afterwards
    CHECK(call.run(CTX_LOW, inner));
    CHECK(innerRuns == 1);
    model = nullptr;
}

constexpr uint64_t OWNER_PERIOD = 1U * NS_PER_MS;
constexpr uint64_t OWNER_COST   = 50U * NS_PER_US;
constexpr uint64_t SNAPSHOT     = 20U * NS_PER_US;
constexpr size_t LINES          = 20U; // 20 lines of 60 characters: 104 ms at 115200 Bd

/* A command of the new kind: one snapshot in the owner, then the output. */
template<class Out>
void command(ModelCall& call, Out& out)
{
    uint32_t snapshot = 0U;
    auto const take   = [&snapshot]()
    {
        model->work(SNAPSHOT);
        snapshot = 7U;
    };
    ModelRunIn{&call}(CTX_LOW, take);
    for (size_t i = 0U; i < LINES; ++i)
    {
        (void)out.printf("line %2u of the command, value %u, padded to sixty characters\r\n",
            static_cast<unsigned>(i), static_cast<unsigned>(snapshot));
    }
}

struct Run
{
    std::string text;
    TaskStats low;
    TaskStats high;
};

Run runOnce(uint8_t const commandContext, uint64_t const phase)
{
    Model m({{"low", CTX_LOW, OWNER_PERIOD, phase, OWNER_COST}, {"high", CTX_HIGH, 250U * NS_PER_US, 0U, 5U * NS_PER_US}},
            300U * NS_PER_MS);
    model = &m;
    ModelCall call(CTX_IDLE);
    std::string const text
        = runCommand(m, 5U * NS_PER_MS, commandContext, [&call](UartOut& out) { command(call, out); });
    m.finish();
    model = nullptr;
    return Run{text, m.stats(0U), m.stats(1U)};
}

void testTiming()
{
    std::printf("timing (owner 1 ms, 50 us per job; console output 20 lines)\n");
    for (uint64_t phase = 0U; phase < OWNER_PERIOD; phase += 125U * NS_PER_US)
    {
        Run const quiet = runOnce(CTX_IDLE, phase);
        CHECK(countLines(quiet.text) == (LINES + 1U));
        CHECK(quiet.text.find("value 7") != std::string::npos);
        CHECK(quiet.low.consoleChars == 0U);   // the owner never waits for the UART
        CHECK(quiet.low.consoleBusy == SNAPSHOT); // it spends one snapshot on the command
        // The owner waits at most for one snapshot plus the higher task, independent of the output length.
        CHECK(quiet.low.maxWait <= (SNAPSHOT + (2U * 5U * NS_PER_US)));
        CHECK(quiet.low.maxStartDelta <= (OWNER_PERIOD + SNAPSHOT + (2U * 5U * NS_PER_US)));
        CHECK(quiet.high.maxWait == 0U);

        // Deliberately inserted fault: the command in the owner's context (before SYS-13).
        Run const old = runOnce(CTX_LOW, phase);
        CHECK(old.text == quiet.text); // same output
        CHECK(old.low.consoleChars == old.text.size());
        CHECK(old.low.maxWait >= (100U * NS_PER_MS)); // held for the whole output
        CHECK(old.high.maxWait == 0U);                // a higher task is not affected either way
        if (phase == 0U)
        {
            std::printf(
                "  SYS-13: %u characters, owner waits <= %.3f ms (spent %.3f ms on the command); old way: owner "
                "waits %.1f ms\n",
                static_cast<unsigned>(quiet.text.size()),
                ms(quiet.low.maxWait),
                ms(quiet.low.consoleBusy),
                ms(old.low.maxWait));
        }
    }
}
} // namespace

int main()
{
    testContextCall();
    testTiming();
    return report("console-context");
}
