/* Host test of the console command "sbc" in the console context (SYS-13,
 * platforms/s32k1xx/port/main/include/systems/SbcConsole.h).
 *
 *  1. Text: "sbc status" (with and without INTN pins, with SPI error) and "sbc dump" (with one failing register)
 *     are the text before SYS-13 (golden lines); the dump reads exactly the 24 documented registers in order.
 *  2. Timing (ConsoleModel.h, own test profile): the task of the SBC system (context 4, 10 ms service cycle with
 *     the watchdog trigger, 150 us) and a CAN task above it (context 7, 1 ms). SPI costs 25 us per register.
 *     SYS-13 way: the SBC task waits at most for one snapshot (status 5 registers, dump 24 registers), the
 *     watchdog trigger keeps its cycle. Deliberately inserted fault, the old way (command in the SBC task): the
 *     service cycle stands still for the whole output.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/consoleContext/include -Iplatforms/s32k1xx/port/main/include -Ilibs/bsw/sbcUja113x/include \
 *       -Ilibs/bsp/bspSpi/include -isystem external/openbsw/libs/bsw/bsp/include \
 *       libs/bsw/consoleContext/test/host/SbcConsoleHostTest.cpp -o build/host/sbc_console_test.exe
 *   build/host/sbc_console_test.exe
 */

#include "ConsoleModel.h"
#include "systems/SbcConsole.h"

using namespace consolemodel;
namespace sc = ::systems::sbcconsole;

namespace
{
constexpr uint8_t CTX_SBC = 4U;
constexpr uint8_t CTX_CAN = 7U;

constexpr uint64_t SBC_PERIOD = 10U * NS_PER_MS;
constexpr uint64_t SBC_COST   = 150U * NS_PER_US;
constexpr uint64_t CAN_PERIOD = 1U * NS_PER_MS;
constexpr uint64_t CAN_COST   = 5U * NS_PER_US;
constexpr uint64_t SPI_READ   = 25U * NS_PER_US; // one register, generous for LPSPI at 1 MHz

sc::StatusSnapshot boardStatus()
{
    sc::StatusSnapshot s{};
    s.driverState       = "normal";
    s.resetSource       = 3U;
    s.resetSourceName   = "power-on";
    s.registersOk       = true;
    s.mode              = 0x07U;
    s.supply            = 0x02U;
    s.transceiver       = 0x81U;
    s.watchdogStatus    = 0x02U;
    s.failSafe          = 0x06U;
    s.canReady          = true;
    s.compensationName  = "not a test reset";
    s.failSafeBefore    = 0x06U;
    s.failSafeAfter     = 0x06U;
    s.resetStatus       = 0x82U;
    s.watchdogTestField = 0U;
    s.armedFailStarts   = 0U;
    s.counters.startupAttempts       = 1U;
    s.counters.watchdogTriggers      = 4711U;
    s.counters.interruptEvents       = 2U;
    s.hasInterruptPins               = true;
    s.interrupt2Active               = true;
    return s;
}

std::string const STATUS_TEXT
    = "driver: normal, reset source 3 (power-on)\r\n"
      "mode Normal, watchdog status 0x02, fail-safe 0x06 (RCC 2)\r\n"
      "supply 0x02: V1 ok, VEXT/V2 1 (0 ok, 1 UV, 2 OV, 3 off), SMPS ok\r\n"
      "CAN 0x81: transmitter on, VCAN ok, failure yes -> ready\r\n"
      "RCC compensation at start: not a test reset (0x02 0x06 -> 0x06), SRS 0x82, TST 0, failed starts armed 0\r\n"
      "counters: startups 1, SPI errors 0, read-back 0, echo 0, WD triggers 4711, irq 2, CAN lost 0, mode lost 0\r\n"
      "pins: SBC_INTERRUPT1 inactive, SBC_INTERRUPT2 active (low)\r\n";

// the 24 documented registers of the UJA113x, as "sbc dump" read them before SYS-13
uint8_t const DUMP[] = {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x0A, 0x10, 0x1B, 0x1C, 0x20, 0x22, 0x23, 0x37, 0x60, 0x61,
                        0x62, 0x63, 0x64, 0x70, 0x73, 0x74, 0x7E, 0x7F};

bool fakeRead(uint8_t const address, uint8_t& value)
{
    value = static_cast<uint8_t>(address ^ 0x5AU);
    return address != 0x7FU; // the last register fails
}

std::string dumpText()
{
    StringOut ref;
    for (uint8_t const a : DUMP)
    {
        uint8_t v = 0U;
        if (fakeRead(a, v))
        {
            (void)ref.printf("0x%02x: 0x%02x\r\n", a, v);
        }
        else
        {
            (void)ref.printf("0x%02x: SPI error\r\n", a);
        }
    }
    return ref.text;
}

void testText()
{
    std::printf("text\n");
    StringOut status;
    sc::writeStatus(status, boardStatus());
    CHECK(status.text == STATUS_TEXT);

    sc::StatusSnapshot noPins = boardStatus();
    noPins.hasInterruptPins   = false;
    noPins.registersOk        = false;
    StringOut error;
    sc::writeStatus(error, noPins);
    CHECK(countLines(error.text) == 4U);
    CHECK(error.text.find("\r\nSPI error\r\n") != std::string::npos);
    CHECK(error.text.find("pins:") == std::string::npos);

    CHECK(sc::DUMP_COUNT == sizeof(DUMP));
    for (size_t i = 0U; i < sc::DUMP_COUNT; ++i)
    {
        CHECK(sc::DUMP_ADDRESSES[i] == DUMP[i]);
    }
    sc::DumpSnapshot dump{};
    std::vector<uint8_t> order;
    sc::takeDump(dump, [&order](uint8_t const a, uint8_t& v) { order.push_back(a); return fakeRead(a, v); });
    CHECK(order == std::vector<uint8_t>(DUMP, DUMP + sizeof(DUMP)));
    StringOut dumpOut;
    sc::writeDump(dumpOut, dump);
    CHECK(dumpOut.text == dumpText());
    CHECK(sc::modeName(0x04U) == std::string("Standby"));
    CHECK(sc::modeName(0x01U) == std::string("Sleep"));
    CHECK(sc::modeName(0x00U) == std::string("invalid"));
}

enum class Cmd
{
    STATUS,
    DUMP,
};

/* "sbc status" / "sbc dump" as composed by SbcSystem::Command: one snapshot in the SBC task, then the output. */
template<class Out>
void sbcCommand(Cmd const cmd, ModelCall& call, Out& out, std::vector<uint8_t>& takenIn)
{
    if (cmd == Cmd::STATUS)
    {
        sc::StatusSnapshot s{};
        auto const take = [&s, &takenIn]()
        {
            takenIn.push_back(model->current());
            model->work(5U * SPI_READ);
            s = boardStatus();
        };
        ModelRunIn{&call}(CTX_SBC, take);
        sc::writeStatus(out, s);
    }
    else
    {
        sc::DumpSnapshot dump{};
        auto const take = [&dump, &takenIn]()
        {
            takenIn.push_back(model->current());
            sc::takeDump(dump, [](uint8_t const a, uint8_t& v) { model->work(SPI_READ); return fakeRead(a, v); });
        };
        ModelRunIn{&call}(CTX_SBC, take);
        sc::writeDump(out, dump);
    }
}

struct Run
{
    std::string text;
    TaskStats sbc;
    TaskStats can;
    std::vector<uint8_t> takenIn;
};

Run runOnce(Cmd const cmd, uint8_t const commandContext, uint64_t const phase)
{
    Model m({{"sbc", CTX_SBC, SBC_PERIOD, phase, SBC_COST}, {"can", CTX_CAN, CAN_PERIOD, 0U, CAN_COST}},
            300U * NS_PER_MS);
    model = &m;
    ModelCall call(CTX_IDLE);
    Run r{};
    r.text = runCommand(
        m, 15U * NS_PER_MS, commandContext, [cmd, &call, &r](UartOut& out) { sbcCommand(cmd, call, out, r.takenIn); });
    m.finish();
    model = nullptr;
    r.sbc = m.stats(0U);
    r.can = m.stats(1U);
    return r;
}

void testTiming()
{
    std::printf("timing (SBC task 10 ms / 150 us at context 4, CAN task 1 ms at context 7, SPI 25 us per register)\n");
    uint64_t worst[2] = {0U, 0U};
    for (uint64_t phase = 0U; phase < SBC_PERIOD; phase += 250U * NS_PER_US)
    {
        for (Cmd const cmd : {Cmd::STATUS, Cmd::DUMP})
        {
            uint64_t const snapshot = ((cmd == Cmd::STATUS) ? 5U : 24U) * SPI_READ;
            Run const now           = runOnce(cmd, CTX_IDLE, phase);
            uint64_t& w             = worst[(cmd == Cmd::STATUS) ? 0U : 1U];
            w                       = (now.sbc.maxWait > w) ? now.sbc.maxWait : w;
            CHECK(now.text == (((cmd == Cmd::STATUS) ? STATUS_TEXT : dumpText()) + "ok\r\n> "));
            CHECK(now.takenIn == std::vector<uint8_t>{CTX_SBC}); // one snapshot, in the SBC task
            CHECK(now.sbc.consoleChars == 0U);
            CHECK(now.sbc.consoleBusy == snapshot);
            // the watchdog trigger waits at most for one snapshot (plus the CAN task above it)
            CHECK(now.sbc.maxWait <= (snapshot + (2U * CAN_COST)));
            CHECK(now.sbc.maxStartDelta <= (SBC_PERIOD + snapshot + (2U * CAN_COST)));
            CHECK(now.can.maxWait == 0U);

            Run const old = runOnce(cmd, CTX_SBC, phase); // deliberately inserted fault: before SYS-13
            CHECK(old.text == now.text);
            CHECK(old.sbc.consoleChars == old.text.size());
            CHECK(old.sbc.maxWait >= (15U * NS_PER_MS)); // the service cycle stands still for the output
            if (phase == 0U)
            {
                std::printf(
                    "  sbc %s, %u characters: SYS-13 SBC task waits <= %.3f ms (snapshot %.3f ms); old way %.1f ms, "
                    "start distance %.1f ms (cycle 10 ms)\n",
                    (cmd == Cmd::STATUS) ? "status" : "dump",
                    static_cast<unsigned>(now.text.size()),
                    ms(now.sbc.maxWait),
                    ms(snapshot),
                    ms(old.sbc.maxWait),
                    ms(old.sbc.maxStartDelta));
            }
        }
    }
    std::printf(
        "  SYS-13, over all 40 phases: SBC task waits <= %.3f ms (status), <= %.3f ms (dump)\n",
        ms(worst[0]),
        ms(worst[1]));
    CHECK(worst[1] > worst[0]); // some phase really lands inside the dump snapshot
}
} // namespace

int main()
{
    testText();
    testTiming();
    return report("sbc-console");
}
