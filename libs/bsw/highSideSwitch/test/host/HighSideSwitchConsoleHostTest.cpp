/* IO-42: host test of the console command of a high-side switch in the console context
 * (highSideSwitch/HighSideSwitchConsole.h, the snapshot/print split of HighSideSwitchSystem).
 *
 * Board finding 2026-09-21 (session d3): "kl30b status" triggered the halt deadman every time (4 of 4, "debug halt
 * or stall (timer)"), then KL30B and DRV_EN were off. The whole command ran in TASK_BSP and wrote its four lines to
 * the UART from there; the deadman poll of the debug halt guard runs in the same task every 1 ms and the deadman
 * fires after 10 ms without a poll.
 *
 *  1. Text: "status" (off, on with load, blanking, locked, open load) and "on/off/reset" are the text before IO-42;
 *     the measured length of "kl30b status" on the eval board values is printed (characters and UART time).
 *  2. Timing (ConsoleModel.h, own test profile, scheduler with fixed priorities, 86.8 us per character): TASK_BSP
 *     (context 2) carries the deadman poll (1 ms), the switch (10 ms) and the gate driver (10 ms, SPI); a CAN task
 *     above it. The IO-42 way (command in the console context, snapshot through ContextCall into TASK_BSP): the
 *     gap between two deadman polls stays far below the deadman timeout for every command and every phase. The old
 *     way (the deliberately inserted fault, = before IO-42): the gap exceeds the timeout for "status" - the board
 *     finding - by the measured amount.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/highSideSwitch/include -Ilibs/bsw/consoleContext/include -Ilibs/bsw/consoleContext/test/host \
 *       libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitch.cpp \
 *       libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitchChannel.cpp \
 *       libs/bsw/highSideSwitch/test/host/HighSideSwitchConsoleHostTest.cpp -o build/host/hss_console_test.exe
 *   build/host/hss_console_test.exe
 */

#include "ConsoleModel.h"
#include "highSideSwitch/HighSideSwitchConsole.h"

using namespace consolemodel;
namespace hc = ::highside::console;
using ::highside::HighSideSwitch;

namespace
{
constexpr uint8_t CTX_BSP = 2U; // TASK_BSP (TASK_IDLE 0, TASK_BACKGROUND 1)
constexpr uint8_t CTX_CAN = 5U;

// Deadman of the debug halt guard (platforms/s32k1xx/port/main/src/integration/DebugHaltGuardSystem.h):
// LpitHaltDeadman::TIMEOUT_MS, DebugHaltGuardSystem::POLL_MS. Mirrored here; the budget below is what counts.
constexpr uint64_t DEADMAN_TIMEOUT = 10U * NS_PER_MS;
constexpr uint64_t POLL_PERIOD     = 1U * NS_PER_MS;
constexpr uint64_t POLL_COST       = 5U * NS_PER_US;
constexpr uint64_t HSS_PERIOD      = 10U * NS_PER_MS;
constexpr uint64_t HSS_COST        = 30U * NS_PER_US; // one ADC conversion, the state machine, two pin writes
constexpr uint64_t GD_PERIOD       = 10U * NS_PER_MS;
constexpr uint64_t GD_COST         = 250U * NS_PER_US; // GSTAT, IOIN and now and then three config reads over SPI
constexpr uint64_t CAN_PERIOD      = 1U * NS_PER_MS;
constexpr uint64_t CAN_COST        = 20U * NS_PER_US;
constexpr uint64_t SNAPSHOT_COST   = 5U * NS_PER_US;  // copy of the switch state resp. one command

/* General budget (IO-42): what a console command may cost a task that carries the deadman poll. Half the deadman
 * timeout leaves the other half to the task's own jobs and to the tasks above it. */
constexpr uint64_t CONSOLE_BUDGET_IN_OWNER = DEADMAN_TIMEOUT / 2U;

// Values of the eval board demo project (projects/evalboard_s32k144_demo, KL30B_SW) as test data: they give the
// longest "limits" line the board prints, which is what the finding was about.
constexpr ::highside::HighSideSwitchConfig KL30B = {10U, 3633U, 30U, 6359U, 20U, 20U, 36U, 909U, 50U};
static_assert(::highside::isValidHighSideSwitchConfig(KL30B), "test data");
// A second profile with other widths of the numbers (own test profile, no board)
constexpr ::highside::HighSideSwitchConfig OTHER = {5U, 12000U, 100U, 25000U, 50U, 250U, 0U, 1500U, 200U};
static_assert(::highside::isValidHighSideSwitchConfig(OTHER), "test data");

class FakeIo : public ::highside::IHighSideSwitchIo
{
public:
    bool in           = false;
    bool den          = false;
    uint32_t loadMa   = 0U;
    uint32_t raw      = 0U;

    bool setEnable(bool const on) override
    {
        in = on;
        return true;
    }

    bool setDiagEnable(bool const on) override
    {
        den = on;
        return true;
    }

    bool readSense(::highside::SenseSample& sample) override
    {
        sample.loadMa = in ? loadMa : 0U;
        sample.raw    = in ? raw : 0U;
        return true;
    }
};

bool lockSet = false;

bool lockQuery() { return lockSet; }

/* ------------------------------------------------------------------------------------------ text */

std::string status(::highside::HighSideSwitchChannel const& ch, char const* const label = "KL30B")
{
    StringOut out;
    hc::writeStatus(out, label, hc::takeStatus(ch));
    return out.text;
}

void testText()
{
    std::printf("text\n");
    FakeIo io;
    ::highside::HighSideSwitchChannel ch(io, KL30B);
    ch.setSwitchOnLock(&lockQuery);
    lockSet = false;
    ch.init();

    std::string const off
        = "KL30B off, last fault none, EN 0, DEN 0\r\n"
          "load 0 mA, max since on 0 mA (IS raw 0)\r\n"
          "limits: overcurrent >= 3633 mA for 30 ms, fault >= 6359 mA for 20 ms, blanking 20 ms, open load < 36 mA, "
          "off <= 909 mA (50 ms), cycle 10 ms\r\n"
          "switch-ons 0, overcurrent trips 0, diagnosis faults 0, ADC errors 0, open-load cycles 0, output errors 0\r\n";
    CHECK(status(ch) == off);
    CHECK(countLines(off) == 4U);

    // on with the 1 A load of the board test (IS raw as on the eval board: 1.8 mA per code)
    StringOut cmd;
    hc::writeCommandResult(cmd, "kl30b", hc::Command::ON, hc::execute(ch, hc::Command::ON));
    CHECK(cmd.text == "kl30b on: ok, state on\r\n");
    io.loadMa = 1003U;
    io.raw    = 551U;
    std::string const blanking = status(ch);
    CHECK(blanking.find("KL30B on (blanking), last fault none, EN 1, DEN 1\r\n") == 0U);
    for (int i = 0; i < 5; ++i)
    {
        ch.cycle();
    }
    std::string const on
        = "KL30B on, last fault none, EN 1, DEN 1\r\n"
          "load 1003 mA, max since on 1003 mA (IS raw 551)\r\n"
          "limits: overcurrent >= 3633 mA for 30 ms, fault >= 6359 mA for 20 ms, blanking 20 ms, open load < 36 mA, "
          "off <= 909 mA (50 ms), cycle 10 ms\r\n"
          "switch-ons 1, overcurrent trips 0, diagnosis faults 0, ADC errors 0, open-load cycles 0, output errors 0\r\n";
    CHECK(status(ch) == on);
    std::printf(
        "  \"kl30b status\" with the load on: %u characters + \"ok\\r\\n> \" = %.1f ms at 115200 Bd synchronous\n",
        static_cast<unsigned>(on.size()),
        ms((on.size() + 6U) * CHARACTER_NS));
    CHECK(((on.size() + 6U) * CHARACTER_NS) > DEADMAN_TIMEOUT); // the board finding: longer than the deadman

    // open load
    io.loadMa = 10U;
    io.raw    = 5U;
    ch.cycle();
    CHECK(status(ch).find("KL30B on, last fault none, EN 1, DEN 1, open load\r\n") == 0U);

    // SYS-36: locked by the safe state
    lockSet = true;
    ch.cycle();
    CHECK(status(ch).find("KL30B off (locked by the safe state), last fault none, EN 0, DEN 0\r\n") == 0U);
    StringOut refused;
    hc::writeCommandResult(refused, "kl30b", hc::Command::ON, hc::execute(ch, hc::Command::ON));
    CHECK(refused.text == "kl30b on: refused, safe state (locked until reset), state off\r\n");
    lockSet = false;
    ch.cycle();

    StringOut offText;
    hc::writeCommandResult(offText, "kl30b", hc::Command::OFF, hc::execute(ch, hc::Command::OFF));
    CHECK(offText.text == "kl30b off: no change, state off\r\n");
    StringOut resetText;
    hc::writeCommandResult(resetText, "kl30b", hc::Command::RESET, hc::execute(ch, hc::Command::RESET));
    CHECK(resetText.text == "kl30b reset: no change, state off\r\n");

    // other profile, other label: every value comes from the snapshot
    FakeIo io2;
    ::highside::HighSideSwitchChannel other(io2, OTHER);
    other.init();
    std::string const t = status(other, "PUMP");
    CHECK(t.find("PUMP off,") == 0U);
    CHECK(t.find("overcurrent >= 12000 mA for 100 ms, fault >= 25000 mA for 50 ms, blanking 250 ms") != std::string::npos);
    CHECK(t.find("cycle 5 ms\r\n") != std::string::npos);
}

/* ------------------------------------------------------------------------------------------ timing */

enum class Cmd
{
    STATUS,
    ON,
    OFF,
    RESET,
};

struct Run
{
    std::string text;
    TaskStats poll;
    TaskStats hss;
    TaskStats can;
    std::vector<uint8_t> takenIn;
};

/* The command as HighSideSwitchSystem::Command composes it: the snapshot (or the command) in the task of the
 * switch, the output afterwards in the context the command runs in. */
template<class Out>
void hssCommand(
    Cmd const cmd, ::highside::HighSideSwitchChannel& ch, ModelCall& call, Out& out, std::vector<uint8_t>& takenIn)
{
    if (cmd == Cmd::STATUS)
    {
        hc::StatusSnapshot s{};
        auto const take = [&s, &ch, &takenIn]()
        {
            takenIn.push_back(model->current());
            model->work(SNAPSHOT_COST);
            s = hc::takeStatus(ch);
        };
        ModelRunIn{&call}(CTX_BSP, take);
        hc::writeStatus(out, "KL30B", s);
    }
    else
    {
        hc::Command const c = (cmd == Cmd::ON) ? hc::Command::ON : ((cmd == Cmd::OFF) ? hc::Command::OFF : hc::Command::RESET);
        hc::CommandSnapshot r{};
        auto const run = [&r, &ch, c, &takenIn]()
        {
            takenIn.push_back(model->current());
            model->work(SNAPSHOT_COST);
            r = hc::execute(ch, c);
        };
        ModelRunIn{&call}(CTX_BSP, run);
        hc::writeCommandResult(out, "kl30b", c, r);
    }
}

Run runOnce(Cmd const cmd, uint8_t const commandContext, uint64_t const phase)
{
    Model m(
        {{"haltguard", CTX_BSP, POLL_PERIOD, 0U, POLL_COST},
         {"kl30b", CTX_BSP, HSS_PERIOD, phase, HSS_COST},
         {"gd", CTX_BSP, GD_PERIOD, (phase + 3U * NS_PER_MS) % GD_PERIOD, GD_COST},
         {"can", CTX_CAN, CAN_PERIOD, 0U, CAN_COST}},
        200U * NS_PER_MS);
    model = &m;
    FakeIo io;
    io.loadMa = 1003U;
    io.raw    = 551U;
    ::highside::HighSideSwitchChannel ch(io, KL30B);
    ch.init();
    (void)ch.requestOn();
    for (int i = 0; i < 5; ++i)
    {
        ch.cycle();
    }
    ModelCall call(CTX_IDLE);
    Run r{};
    r.text = runCommand(
        m, 15U * NS_PER_MS, commandContext, [cmd, &ch, &call, &r](UartOut& out) { hssCommand(cmd, ch, call, out, r.takenIn); });
    m.finish();
    model = nullptr;
    r.poll = m.stats(0U);
    r.hss  = m.stats(1U);
    r.can  = m.stats(3U);
    return r;
}

char const* name(Cmd const cmd)
{
    return (cmd == Cmd::STATUS) ? "status" : ((cmd == Cmd::ON) ? "on" : ((cmd == Cmd::OFF) ? "off" : "reset"));
}

void testTiming()
{
    std::printf(
        "timing (TASK_BSP context 2: deadman poll 1 ms, switch 10 ms, gate driver 10 ms; CAN 1 ms at context 5)\n");
    uint64_t worstGap = 0U;
    for (uint64_t phase = 0U; phase < HSS_PERIOD; phase += 250U * NS_PER_US)
    {
        for (Cmd const cmd : {Cmd::STATUS, Cmd::ON, Cmd::OFF, Cmd::RESET})
        {
            Run const now = runOnce(cmd, CTX_IDLE, phase);
            CHECK(now.takenIn == std::vector<uint8_t>{CTX_BSP}); // one snapshot resp. command, in TASK_BSP
            CHECK(now.poll.consoleChars == 0U);                 // TASK_BSP writes no character
            CHECK(now.hss.consoleChars == 0U);
            CHECK(now.poll.consoleBusy + now.hss.consoleBusy <= SNAPSHOT_COST);
            // the deadman poll never waits longer than the jobs of its own task and the snapshot
            CHECK(now.poll.maxStartDelta < DEADMAN_TIMEOUT);
            CHECK((now.poll.maxStartDelta - POLL_PERIOD) <= CONSOLE_BUDGET_IN_OWNER);
            CHECK(now.can.maxWait == 0U);
            worstGap = (now.poll.maxStartDelta > worstGap) ? now.poll.maxStartDelta : worstGap;

            Run const old = runOnce(cmd, CTX_BSP, phase); // deliberately inserted fault: before IO-42
            CHECK(old.text == now.text);
            // every character charged to TASK_BSP (the model books a context's console work on its first task)
            CHECK(old.poll.consoleChars == old.text.size());
            CHECK(old.can.consoleChars == 0U);
            uint64_t const uart = static_cast<uint64_t>(old.text.size()) * CHARACTER_NS;
            CHECK(old.poll.maxStartDelta >= uart); // the deadman poll stands still for the whole output
            if (cmd == Cmd::STATUS)
            {
                CHECK(old.poll.maxStartDelta > DEADMAN_TIMEOUT); // the board finding: the deadman fires
            }
            if (phase == 0U)
            {
                std::printf(
                    "  kl30b %-6s %3u characters: IO-42 deadman poll gap <= %.3f ms; old way %.1f ms (timeout %.0f ms)"
                    "%s\n",
                    name(cmd),
                    static_cast<unsigned>(now.text.size()),
                    ms(now.poll.maxStartDelta),
                    ms(old.poll.maxStartDelta),
                    ms(DEADMAN_TIMEOUT),
                    (old.poll.maxStartDelta > DEADMAN_TIMEOUT) ? "  -> deadman fired" : "");
            }
        }
    }
    std::printf("  IO-42, over all 40 phases and 4 commands: deadman poll gap <= %.3f ms\n", ms(worstGap));
    CHECK(worstGap < (POLL_PERIOD + GD_COST + HSS_COST + SNAPSHOT_COST + (2U * POLL_COST) + (2U * CAN_COST)));
}
} // namespace

int main()
{
    testText();
    testTiming();
    return report("high-side-switch console");
}
