/* Host test of the console command "ecu" in the console context (SYS-13, ecu/application/src/systems/EcuStateConsole.h).
 *
 *  1. Text: "ecu status" is the text before SYS-13 (golden lines), in RUN and with a run-down step running and an
 *     aborted step; the wake-up sources are decoded as before (fixed sources, CAN buses, local inputs).
 *  2. Timing (ConsoleModel.h, own test profile): the task of the power supply (context 4, 10 ms, the ECU state
 *     management next to the SBC watchdog trigger) and a CAN task above it. SYS-13 way: the task waits at most
 *     for one snapshot. Deliberately inserted fault, the old way (command in the task of the power supply): about
 *     1100 characters hold it for more than 80 ms.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/consoleContext/include -Iecu/application/src/systems -Ilibs/bsw/ecuState/include \
 *       libs/bsw/consoleContext/test/host/EcuConsoleHostTest.cpp -o build/host/ecu_console_test.exe
 *   build/host/ecu_console_test.exe
 */

#include "ConsoleModel.h"
#include "EcuStateConsole.h"

using namespace consolemodel;
namespace ec = ::systems::ecuconsole;

namespace
{
constexpr uint8_t CTX_SUPPLY = 4U;
constexpr uint8_t CTX_CAN    = 7U;

constexpr uint64_t SUPPLY_PERIOD = 10U * NS_PER_MS;
constexpr uint64_t SUPPLY_COST   = 150U * NS_PER_US;
constexpr uint64_t CAN_PERIOD    = 1U * NS_PER_MS;
constexpr uint64_t CAN_COST      = 5U * NS_PER_US;
constexpr uint64_t SNAPSHOT      = 30U * NS_PER_US; // ~70 member reads and a few virtual calls

ec::StatusSnapshot runStatus()
{
    using namespace ::ecustate;
    ec::StatusSnapshot s{};
    s.nowMs                  = 12345U;
    s.state                  = "Run";
    s.timeInStateMs          = 12000U;
    s.runLevel               = 9U;
    s.shutdownLevel          = 1U;
    s.startupWakeupSources   = wakeup::POWER | wakeup::canBus(0U);
    s.wakeupEvents           = 0U;
    s.selectedWakeupSources  = wakeup::canBus(0U) | wakeup::canBus(1U) | wakeup::local(2U) | wakeup::TIMER;
    s.armedWakeupSources     = wakeup::canBus(0U) | wakeup::local(2U);
    s.missingWakeupSources   = wakeup::canBus(1U) | wakeup::TIMER;
    s.requestedCyclicMs      = 1000U;
    s.usedCyclicMs           = 0U;
    s.supportedWakeupSources = wakeup::canBus(0U) | wakeup::LOCALS;
    s.steps[0]               = ec::StepSnapshot{"NvM", "pending", "no provider"};
    s.steps[1]               = ec::StepSnapshot{"CAN stop", "pending", "can"};
    s.steps[2]               = ec::StepSnapshot{"Out", "pending", "OUTPUTS"}; // SYS-174
    s.runningStep            = "-";
    s.rteSleepConnected      = true;
    s.powerSupply            = "UJA113x";
    s.powerDownSupported     = true;
    s.lastPowerDown          = "-";
    s.outputFlush            = "console";
    s.outputFlushState       = "idle";
    s.outputFlushLimitMs     = 800U;
    s.logEntriesLost         = 3U;
    s.minRunMs               = 5000U;
    s.postRunMs              = 2000U;
    s.shutdownTimeoutMs      = 3000U;
    s.sleepTimeoutMs         = 1000U;
    s.wakeupDuringShutdownRestarts = true;
    s.stepLimitName[0]             = "NvM";
    s.stepLimitMs[0]               = 500U;
    s.stepLimitName[1]             = "CAN stop";
    s.stepLimitMs[1]               = 100U;
    s.stepLimitName[2]             = "Out";
    s.stepLimitMs[2]               = 50U;
    s.stepsAborted                 = 1U;   // SYS-174
    s.restartRequested             = true; // SYS-173
    s.standbyEntered               = 2U;
    s.restartReason                = "none";
    return s;
}

std::string const RUN_TEXT
    = "state Run for 12000 ms, lifecycle run level 9, shutdown level 1\r\n"
      "wake-up sources 0x00000101 power-on CAN bus 0\r\n"
      "wake-up events since standby 0x00000000\r\n"
      "wake-up selected 0x00040310 timer CAN bus 0 CAN bus 1 local 2\r\n"
      "wake-up armed 0x00040100 CAN bus 0 local 2\r\n"
      "wake-up not on this board 0x00000210 timer CAN bus 1\r\n"
      "wake-up cyclic: requested 1000 ms, used 0 ms; supply offers 0x00ff0100 CAN bus 0 local 0 local 1 local 2 "
      "local 3 local 4 local 5 local 6 local 7\r\n"
      "run-down: NvM pending (no provider) CAN stop pending (can) Out pending (OUTPUTS)\r\n"
      "sleep request: rte no (connected), console no, forced no, refused no\r\n"
      "power supply UJA113x, power down supported, last result -\r\n"
      "output flush console: idle, limit 800 ms, took 0 ms\r\n"
      "log entries lost: 3\r\n"
      "parameters: min run 5000 ms, post-run 2000 ms, shutdown timeout 3000 ms, sleep timeout 1000 ms, wake-up "
      "during shutdown restart, after watchdog reset sleep\r\n"
      "parameters: step limits NvM 500 ms, CAN stop 100 ms, Out 50 ms, on step error continue\r\n"
      "counters: sleep refused 0, standby 2, standby cancelled 0, post-run restarted 0, step failed 0, step not "
      "present 0, restart none\r\n"
      "supervision: steps aborted 1, restart requested yes, direct reset no\r\n";

void testText()
{
    std::printf("text\n");
    StringOut run;
    ec::writeStatus(run, runStatus());
    CHECK(run.text == RUN_TEXT);
    CHECK(countLines(run.text) == 16U);

    // run-down: a step running, an earlier abort with its reason
    ec::StatusSnapshot down = runStatus();
    down.stepRunning        = true;
    down.runningStep        = "CAN stop";
    down.runningStepMs      = 40U;
    down.runningStepLimitMs = 100U;
    down.stepAborted        = true;
    down.abortStep          = "NvM";
    down.abortState         = "timeout";
    down.abortReason        = "write pending";
    StringOut rundown;
    ec::writeRunDown(rundown, down);
    CHECK(rundown.text
          == "run-down: NvM pending (no provider) CAN stop pending (can) Out pending (OUTPUTS)\r\n"
             "run-down step CAN stop running for 40 ms of 100 ms\r\n"
             "last abort: step NvM timeout, reason write pending\r\n");

    StringOut sources;
    ec::writeWakeupSources(sources, 0xFFFFFFFFU);
    CHECK(sources.text
          == "0xffffffff power-on reset watchdog supply timer unknown CAN bus 0 CAN bus 1 CAN bus 2 CAN bus 3 CAN bus 4 "
             "CAN bus 5 CAN bus 6 CAN bus 7 local 0 local 1 local 2 local 3 local 4 local 5 local 6 local 7");
}

struct Run
{
    std::string text;
    TaskStats supply;
    TaskStats can;
    std::vector<uint8_t> takenIn;
};

Run runOnce(uint8_t const commandContext, uint64_t const phase)
{
    Model m({{"supply", CTX_SUPPLY, SUPPLY_PERIOD, phase, SUPPLY_COST}, {"can", CTX_CAN, CAN_PERIOD, 0U, CAN_COST}},
            400U * NS_PER_MS);
    model = &m;
    ModelCall call(CTX_IDLE);
    Run r{};
    r.text = runCommand(
        m,
        15U * NS_PER_MS,
        commandContext,
        [&call, &r](UartOut& out)
        {
            // "ecu status" as composed by EcuStateSystem::Command: takeStatus in the task of the power supply
            ec::StatusSnapshot s{};
            auto const take = [&s, &r]()
            {
                r.takenIn.push_back(model->current());
                model->work(SNAPSHOT);
                s = runStatus();
            };
            ModelRunIn{&call}(CTX_SUPPLY, take);
            ec::writeStatus(out, s);
        });
    m.finish();
    model    = nullptr;
    r.supply = m.stats(0U);
    r.can    = m.stats(1U);
    return r;
}

void testTiming()
{
    std::printf("timing (power supply task 10 ms / 150 us at context 4, CAN task 1 ms at context 7)\n");
    uint64_t worst = 0U;
    for (uint64_t phase = 0U; phase < SUPPLY_PERIOD; phase += 250U * NS_PER_US)
    {
        Run const now = runOnce(CTX_IDLE, phase);
        worst         = (now.supply.maxWait > worst) ? now.supply.maxWait : worst;
        CHECK(now.text == (RUN_TEXT + "ok\r\n> "));
        CHECK(now.takenIn == std::vector<uint8_t>{CTX_SUPPLY});
        CHECK(now.supply.consoleChars == 0U);
        CHECK(now.supply.consoleBusy == SNAPSHOT);
        CHECK(now.supply.maxWait <= (SNAPSHOT + (2U * CAN_COST)));
        CHECK(now.can.maxWait == 0U);

        Run const old = runOnce(CTX_SUPPLY, phase); // deliberately inserted fault: before SYS-13
        CHECK(old.text == now.text);
        CHECK(old.supply.consoleChars == old.text.size());
        CHECK(old.supply.maxWait >= (80U * NS_PER_MS));
        if (phase == 0U)
        {
            std::printf(
                "  ecu status, %u characters: old way the task waits %.1f ms, start distance %.1f ms\n",
                static_cast<unsigned>(now.text.size()),
                ms(old.supply.maxWait),
                ms(old.supply.maxStartDelta));
        }
    }
    std::printf("  SYS-13, over all 40 phases: the task waits <= %.3f ms (snapshot %.3f ms)\n", ms(worst), ms(SNAPSHOT));
}
} // namespace

int main()
{
    testText();
    testTiming();
    return report("ecu-console");
}
