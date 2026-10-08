/* Host test of the console command "lc" in the console context (SYS-13, ecu/application/src/systems/LifecycleConsole.h).
 *
 * The command itself (LifecycleControlCommand) is not ours to split; SysAdminSystem registers it in the console
 * context and hands it LifecycleConsole as its lifecycle manager.
 *  1. The proxy: transitionToLevel() runs in the task of the lifecycle manager (context of the sysadmin task),
 *     getLevelCount() and the listener calls are passed through directly, every call reaches the manager once.
 *  2. Timing (ConsoleModel.h, own test profile): the sysadmin task (context 8) runs above the CAN task (context 7,
 *     1 ms, UDS). SYS-13 way: "lc level 3" costs the sysadmin task one short call, the CAN task is not held.
 *     Deliberately inserted fault, the old way (command in the sysadmin task): its "ok" and prompt hold the
 *     sysadmin task and with it the CAN task below it for about 0.5 ms per command.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/consoleContext/include -Iecu/application/src/systems \
 *       libs/bsw/consoleContext/test/host/LcConsoleHostTest.cpp -o build/host/lc_console_test.exe
 *   build/host/lc_console_test.exe
 */

#include "ConsoleModel.h"
#include "LifecycleConsole.h"

using namespace consolemodel;

namespace
{
constexpr uint8_t CTX_CAN      = 7U;
constexpr uint8_t CTX_SYSADMIN = 8U;

constexpr uint64_t CAN_PERIOD      = 1U * NS_PER_MS;
constexpr uint64_t CAN_COST        = 60U * NS_PER_US;
constexpr uint64_t SYSADMIN_PERIOD = 10U * NS_PER_MS;
constexpr uint64_t SYSADMIN_COST   = 5U * NS_PER_US;
constexpr uint64_t TRANSITION      = 5U * NS_PER_US; // _nextLevel and ::async::execute of the manager

/* Same shape as ::lifecycle::ILifecycleManager / ILifecycleListener (the target types need OpenBSW's async). */
class Listener
{
public:
    virtual ~Listener() = default;
};

class Manager
{
public:
    virtual uint8_t getLevelCount() const                 = 0;
    virtual void transitionToLevel(uint8_t level)         = 0;
    virtual void addLifecycleListener(Listener& l)        = 0;
    virtual void removeLifecycleListener(Listener& l)     = 0;
    virtual ~Manager()                                    = default;
};

class FakeManager final : public Manager
{
public:
    uint8_t getLevelCount() const override
    {
        ++levelCountCalls;
        return 9U;
    }

    void transitionToLevel(uint8_t const level) override
    {
        levels.push_back(level);
        contexts.push_back(model->current());
        model->work(TRANSITION);
    }

    void addLifecycleListener(Listener& l) override { added = &l; }

    void removeLifecycleListener(Listener& l) override { removed = &l; }

    mutable int levelCountCalls = 0;
    std::vector<uint8_t> levels;
    std::vector<uint8_t> contexts;
    Listener* added   = nullptr;
    Listener* removed = nullptr;
};

ModelCall* currentCall = nullptr;

struct RunIn
{
    template<class Work>
    void operator()(uint8_t const context, Work& work) const
    {
        ModelRunIn{currentCall}(context, work);
    }
};

using Proxy = ::systems::LifecycleConsole<Manager, Listener, uint8_t, RunIn>;

/* "lc level <n>" as LifecycleControlCommand runs it: range check with getLevelCount(), then the transition. */
template<class Out>
void lcLevel(Manager& manager, uint8_t const level, Out& out)
{
    if (level <= manager.getLevelCount())
    {
        manager.transitionToLevel(level);
    }
    else
    {
        (void)out.printf("error\r\n");
    }
}

void testProxy()
{
    std::printf("proxy\n");
    Model m({{"sysadmin", CTX_SYSADMIN, SYSADMIN_PERIOD, 0U, SYSADMIN_COST}}, 20U * NS_PER_MS);
    model = &m;
    ModelCall call(CTX_IDLE);
    currentCall = &call;
    FakeManager target;
    Proxy proxy(target, CTX_SYSADMIN);
    Manager& manager = proxy;

    CHECK(manager.getLevelCount() == 9U);
    CHECK(target.levelCountCalls == 1);
    manager.transitionToLevel(3U);
    manager.transitionToLevel(0U);
    CHECK(target.levels == (std::vector<uint8_t>{3U, 0U}));
    CHECK(target.contexts == (std::vector<uint8_t>{CTX_SYSADMIN, CTX_SYSADMIN})); // in the manager's task
    CHECK(call.queuedCalls() == 2U);
    Listener l;
    manager.addLifecycleListener(l);
    manager.removeLifecycleListener(l);
    CHECK(target.added == &l);
    CHECK(target.removed == &l);

    // called from the sysadmin task itself (not from the console): inline, as runInOwnerContext on the target
    m.setCurrent(CTX_SYSADMIN);
    manager.transitionToLevel(9U);
    CHECK(target.contexts.back() == CTX_SYSADMIN);
    CHECK(call.queuedCalls() == 2U);
    m.setCurrent(CTX_IDLE);
    currentCall = nullptr;
    model       = nullptr;
}

struct Run
{
    std::string text;
    TaskStats can;
    TaskStats sysadmin;
    std::vector<uint8_t> contexts;
};

Run runOnce(uint8_t const commandContext, uint64_t const phase)
{
    Model m({{"can", CTX_CAN, CAN_PERIOD, phase, CAN_COST}, {"sysadmin", CTX_SYSADMIN, SYSADMIN_PERIOD, 0U, SYSADMIN_COST}},
            50U * NS_PER_MS);
    model = &m;
    ModelCall call(CTX_IDLE);
    currentCall = &call;
    FakeManager target;
    Proxy proxy(target, CTX_SYSADMIN);
    Run r{};
    r.text = runCommand(m, 15U * NS_PER_MS, commandContext, [&proxy](UartOut& out) { lcLevel(proxy, 3U, out); });
    m.finish();
    currentCall = nullptr;
    model       = nullptr;
    r.can       = m.stats(0U);
    r.sysadmin  = m.stats(1U);
    r.contexts  = target.contexts;
    return r;
}

void testTiming()
{
    std::printf("timing (sysadmin at context 8 above the CAN task at context 7, 1 ms / 60 us)\n");
    uint64_t worstNow = 0U;
    uint64_t worstOld = 0U;
    for (uint64_t phase = 0U; phase < CAN_PERIOD; phase += 50U * NS_PER_US)
    {
        Run const now = runOnce(CTX_IDLE, phase);
        CHECK(now.text == "ok\r\n> ");
        CHECK(now.contexts == std::vector<uint8_t>{CTX_SYSADMIN});
        CHECK(now.sysadmin.consoleChars == 0U);
        CHECK(now.can.consoleChars == 0U);
        CHECK(now.sysadmin.consoleBusy == TRANSITION);
        CHECK(now.can.maxWait <= (TRANSITION + SYSADMIN_COST));
        worstNow = (now.can.maxWait > worstNow) ? now.can.maxWait : worstNow;

        Run const old = runOnce(CTX_SYSADMIN, phase); // deliberately inserted fault: before SYS-13
        CHECK(old.text == now.text);
        CHECK(old.sysadmin.consoleChars == 6U); // "ok" and the prompt in the sysadmin task
        worstOld = (old.can.maxWait > worstOld) ? old.can.maxWait : worstOld;
    }
    CHECK(worstOld >= (6U * CHARACTER_NS - CAN_COST)); // the CAN task waited for the output of the sysadmin task
    CHECK(worstNow < (worstOld / 10U));
    std::printf("  CAN task waits <= %.3f ms (SYS-13) instead of %.3f ms (old way)\n", ms(worstNow), ms(worstOld));
}
} // namespace

int main()
{
    testProxy();
    testTiming();
    return report("lc-console");
}
