/* Host test of the smart high-side switch driver (HighSideSwitch, HighSideSwitchChannel, HighSideSwitchSense),
 * no target hardware needed. The configurations are test data (round numbers, and a second profile with another
 * cycle time and other levels), not the values of any board. Levels and samples are load currents in mA (IO-21);
 * the conversion of a real sense channel is tested with generated characteristics in
 * HighSideSwitchIntegrationHostTest.cpp.
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/highSideSwitch/include \
 *       libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitch.cpp \
 *       libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitchChannel.cpp \
 *       libs/bsw/highSideSwitch/test/host/HighSideSwitchHostTest.cpp \
 *       -o build/host/high_side_switch_test.exe
 *   build/host/high_side_switch_test.exe
 */

#include "highSideSwitch/HighSideSwitchChannel.h"
#include "highSideSwitch/HighSideSwitchSense.h"

#include <cstdio>

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                                  \
    do                                                                               \
    {                                                                                \
        ++checks;                                                                    \
        if (!(cond))                                                                 \
        {                                                                            \
            ++failures;                                                              \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);            \
        }                                                                            \
    } while (false)

using ::highside::HighSideSwitch;
using ::highside::HighSideSwitchConfig;
using State         = HighSideSwitch::State;
using Fault         = HighSideSwitch::Fault;
using CommandResult = HighSideSwitch::CommandResult;

/* Test configuration with round numbers:
 * blanking 2 cycles, overcurrent 3 cycles, fault 2 cycles, off 5 cycles. */
constexpr HighSideSwitchConfig TEST_CONFIG = {
    10U,   // cycleMs
    2000U, // overcurrentMa
    30U,   // overcurrentDebounceMs
    3500U, // faultMa
    20U,   // faultDebounceMs
    20U,   // switchOnBlankingMs
    20U,   // openLoadMa
    500U,  // offMaxMa
    50U,   // offDebounceMs
};

static_assert(::highside::isValidHighSideSwitchConfig(TEST_CONFIG), "test config");

bool outputsOff(HighSideSwitch const& sw) { return !sw.isEnableOutput() && !sw.isDiagEnableOutput(); }

bool outputsOn(HighSideSwitch const& sw) { return sw.isEnableOutput() && sw.isDiagEnableOutput(); }

void steps(HighSideSwitch& sw, uint32_t count, uint32_t ma)
{
    for (uint32_t i = 0U; i < count; ++i)
    {
        sw.step(true, ma);
    }
}

/* Switch on and run through the blanking time with a normal load current. */
void switchOn(HighSideSwitch& sw)
{
    (void)sw.requestOn();
    steps(sw, 2U, 800U);
}

void testDefaultOff()
{
    std::printf("default off\n");
    HighSideSwitch sw(TEST_CONFIG);
    CHECK(sw.getState() == State::OFF);
    CHECK(outputsOff(sw));
    CHECK(sw.getLastFault() == Fault::NONE);
    steps(sw, 100U, 0U);
    CHECK(sw.getState() == State::OFF);
    CHECK(outputsOff(sw));
    CHECK(sw.requestOff() == CommandResult::ALREADY);
    CHECK(sw.reset() == CommandResult::ALREADY);
}

void testOnOff()
{
    std::printf("on/off\n");
    HighSideSwitch sw(TEST_CONFIG);
    CHECK(sw.requestOn() == CommandResult::OK);
    CHECK(sw.getState() == State::ON);
    CHECK(outputsOn(sw)); // outputs follow the command immediately
    CHECK(sw.isBlanking());
    CHECK(sw.requestOn() == CommandResult::ALREADY);
    steps(sw, 1000U, 1500U); // below overcurrent
    CHECK(sw.getState() == State::ON);
    CHECK(!sw.isBlanking());
    CHECK(sw.getMaxOnMa() == 1500U);
    CHECK(!sw.isOpenLoad());
    CHECK(sw.requestOff() == CommandResult::OK);
    CHECK(sw.getState() == State::OFF);
    CHECK(outputsOff(sw));
    CHECK(sw.getCounters().switchOns == 1U);
}

void testBlanking()
{
    std::printf("blanking\n");
    HighSideSwitch sw(TEST_CONFIG);
    (void)sw.requestOn();
    steps(sw, 2U, 4000U); // inrush during blanking is ignored
    CHECK(sw.getState() == State::ON);
    CHECK(sw.getMaxOnMa() == 0U);
    sw.step(true, 800U);
    CHECK(sw.getState() == State::ON);
    CHECK(!sw.isBlanking());
    // Overcurrent still present right after blanking -> trips after the debounce time.
    HighSideSwitch sw2(TEST_CONFIG);
    (void)sw2.requestOn();
    steps(sw2, 2U, 2500U);
    steps(sw2, 2U, 2500U);
    CHECK(sw2.getState() == State::ON);
    sw2.step(true, 2500U);
    CHECK(sw2.getState() == State::OVERCURRENT);
}

void testOvercurrentTrip()
{
    std::printf("overcurrent trip, latch, reset\n");
    HighSideSwitch sw(TEST_CONFIG);
    switchOn(sw);
    steps(sw, 2U, 2000U); // exactly at the threshold counts as overcurrent
    CHECK(sw.getState() == State::ON);
    CHECK(outputsOn(sw));
    sw.step(true, 2000U);
    CHECK(sw.getState() == State::OVERCURRENT);
    CHECK(sw.getLastFault() == Fault::OVERCURRENT);
    CHECK(outputsOff(sw));
    CHECK(sw.getCounters().overcurrentTrips == 1U);
    CHECK(sw.getMaxOnMa() == 2000U);

    // Latched: no automatic restart, on refused, off does not clear the latch.
    steps(sw, 1000U, 0U);
    CHECK(sw.getState() == State::OVERCURRENT);
    CHECK(outputsOff(sw));
    CHECK(sw.requestOn() == CommandResult::LATCHED);
    CHECK(outputsOff(sw));
    CHECK(sw.requestOff() == CommandResult::ALREADY);
    CHECK(sw.getState() == State::OVERCURRENT);

    // Reset -> OFF, still off until a new on command.
    CHECK(sw.reset() == CommandResult::OK);
    CHECK(sw.getState() == State::OFF);
    CHECK(outputsOff(sw));
    CHECK(sw.getLastFault() == Fault::OVERCURRENT); // kept for the status output
    steps(sw, 100U, 0U);
    CHECK(sw.getState() == State::OFF);
    CHECK(sw.requestOn() == CommandResult::OK);
    CHECK(outputsOn(sw));
    CHECK(sw.getCounters().switchOns == 2U);
}

void testOvercurrentDebounce()
{
    std::printf("overcurrent debounce\n");
    HighSideSwitch sw(TEST_CONFIG);
    switchOn(sw);
    for (int i = 0; i < 100; ++i)
    {
        steps(sw, 2U, 2600U); // two cycles above, one below: never 3 in a row
        sw.step(true, 1999U);
    }
    CHECK(sw.getState() == State::ON);
    CHECK(sw.getCounters().overcurrentTrips == 0U);
    steps(sw, 3U, 2600U);
    CHECK(sw.getState() == State::OVERCURRENT);
}

void testFaultLevel()
{
    std::printf("IS fault level\n");
    HighSideSwitch sw(TEST_CONFIG);
    switchOn(sw);
    sw.step(true, 4096U);
    CHECK(sw.getState() == State::ON);
    sw.step(true, 4096U);
    CHECK(sw.getState() == State::DIAG_FAULT);
    CHECK(sw.getLastFault() == Fault::IS_FAULT_LEVEL); // fault wins over overcurrent
    CHECK(outputsOff(sw));
    CHECK(sw.getCounters().diagFaults == 1U);
    CHECK(sw.getCounters().overcurrentTrips == 0U);
    CHECK(sw.requestOn() == CommandResult::LATCHED);
    CHECK(sw.reset() == CommandResult::OK);
    CHECK(sw.getState() == State::OFF);

    // Fault level interrupted: debounce restarts; an overcurrent level in between still counts.
    HighSideSwitch sw2(TEST_CONFIG);
    switchOn(sw2);
    sw2.step(true, 3500U);
    sw2.step(true, 3000U);
    CHECK(sw2.getState() == State::ON);
    sw2.step(true, 3500U);
    CHECK(sw2.getState() == State::OVERCURRENT); // 3 cycles >= overcurrent, fault never 2 in a row
    CHECK(sw2.getLastFault() == Fault::OVERCURRENT);

    // Exactly at the fault threshold counts as fault level; one below does not.
    HighSideSwitch sw3(TEST_CONFIG);
    switchOn(sw3);
    steps(sw3, 2U, 3500U);
    CHECK(sw3.getState() == State::DIAG_FAULT);
    CHECK(sw3.getLastFault() == Fault::IS_FAULT_LEVEL);
    HighSideSwitch sw4(TEST_CONFIG);
    switchOn(sw4);
    steps(sw4, 2U, 3499U);
    CHECK(sw4.getState() == State::ON);
}

void testAdcError()
{
    std::printf("ADC error\n");
    HighSideSwitch sw(TEST_CONFIG);
    (void)sw.requestOn();
    sw.step(false, 0U); // also during blanking
    CHECK(sw.getState() == State::DIAG_FAULT);
    CHECK(sw.getLastFault() == Fault::ADC_ERROR);
    CHECK(outputsOff(sw));
    CHECK(sw.getCounters().adcErrors == 1U);

    HighSideSwitch off(TEST_CONFIG);
    off.step(false, 0U);
    CHECK(off.getState() == State::DIAG_FAULT); // on must not be possible without sense signal
    CHECK(off.requestOn() == CommandResult::LATCHED);
    off.step(false, 0U);
    CHECK(off.getCounters().adcErrors == 2U);
    CHECK(off.getCounters().diagFaults == 1U); // no second latch
}

void testOffPlausibility()
{
    std::printf("off-state plausibility\n");
    HighSideSwitch sw(TEST_CONFIG);
    steps(sw, 4U, 600U);
    CHECK(sw.getState() == State::OFF);
    sw.step(true, 500U); // at the limit is plausible, restarts debounce
    steps(sw, 4U, 600U);
    CHECK(sw.getState() == State::OFF);
    sw.step(true, 600U);
    CHECK(sw.getState() == State::DIAG_FAULT);
    CHECK(sw.getLastFault() == Fault::OFF_IMPLAUSIBLE);
    CHECK(outputsOff(sw));

    // Current decaying after "off" within the off debounce time is no fault.
    HighSideSwitch sw2(TEST_CONFIG);
    switchOn(sw2);
    (void)sw2.requestOff();
    steps(sw2, 4U, 1800U);
    steps(sw2, 100U, 0U);
    CHECK(sw2.getState() == State::OFF);
}

void testOpenLoad()
{
    std::printf("open load warning\n");
    HighSideSwitch sw(TEST_CONFIG);
    (void)sw.requestOn();
    steps(sw, 2U, 0U);
    CHECK(!sw.isOpenLoad()); // not during blanking
    sw.step(true, 19U);
    CHECK(sw.isOpenLoad());
    CHECK(sw.getState() == State::ON); // warning only
    CHECK(outputsOn(sw));
    sw.step(true, 20U);
    CHECK(!sw.isOpenLoad());
    sw.step(true, 0U);
    CHECK(sw.getCounters().openLoadCycles == 2U);
    (void)sw.requestOff();
    CHECK(!sw.isOpenLoad());
}

void testConfigValidation()
{
    std::printf("config validation and debounce rounding\n");
    HighSideSwitchConfig c = TEST_CONFIG;
    CHECK(::highside::isValidHighSideSwitchConfig(c));
    c.faultMa = c.overcurrentMa;
    CHECK(!::highside::isValidHighSideSwitchConfig(c));
    c           = TEST_CONFIG;
    c.cycleMs   = 0U;
    CHECK(!::highside::isValidHighSideSwitchConfig(c));
    c                       = TEST_CONFIG;
    c.overcurrentDebounceMs = 5U;
    CHECK(!::highside::isValidHighSideSwitchConfig(c));
    c            = TEST_CONFIG;
    c.openLoadMa = c.overcurrentMa;
    CHECK(!::highside::isValidHighSideSwitchConfig(c));
    c          = TEST_CONFIG;
    c.offMaxMa = c.faultMa;
    CHECK(!::highside::isValidHighSideSwitchConfig(c));

    // 25 ms with a 10 ms cycle rounds up to 3 cycles (never shorter than configured).
    c                       = TEST_CONFIG;
    c.overcurrentDebounceMs = 25U;
    HighSideSwitch sw(c);
    switchOn(sw);
    steps(sw, 2U, 2500U);
    CHECK(sw.getState() == State::ON);
    sw.step(true, 2500U);
    CHECK(sw.getState() == State::OVERCURRENT);
}

/* Second profile: 5 ms cycle, other levels. Debounce times are given in ms, so the cycle counts follow
 * from the cycle time: overcurrent 30 ms = 6 cycles, fault 10 ms = 2, blanking 15 ms = 3, off 25 ms = 5. */
constexpr HighSideSwitchConfig FAST_CONFIG = {
    5U,    // cycleMs
    1200U, // overcurrentMa
    30U,   // overcurrentDebounceMs
    3000U, // faultMa
    10U,   // faultDebounceMs
    15U,   // switchOnBlankingMs
    50U,   // openLoadMa
    200U,  // offMaxMa
    25U,   // offDebounceMs
};

static_assert(::highside::isValidHighSideSwitchConfig(FAST_CONFIG), "fast config");

void testOtherProfile()
{
    std::printf("other cycle time and levels\n");
    HighSideSwitch sw(FAST_CONFIG);
    (void)sw.requestOn();
    steps(sw, 3U, 5000U); // blanking: 3 cycles
    CHECK(sw.getState() == State::ON);
    CHECK(sw.isBlanking());
    steps(sw, 5U, 1200U); // 5 of 6 overcurrent cycles
    CHECK(sw.getState() == State::ON);
    CHECK(!sw.isBlanking());
    sw.step(true, 1200U);
    CHECK(sw.getState() == State::OVERCURRENT);
    CHECK(sw.reset() == CommandResult::OK);

    (void)sw.requestOn();
    steps(sw, 3U, 0U);
    sw.step(true, 1199U); // below this profile's overcurrent level
    CHECK(sw.getState() == State::ON);
    CHECK(!sw.isOpenLoad());
    sw.step(true, 49U);
    CHECK(sw.isOpenLoad());
    sw.step(true, 3000U);
    CHECK(sw.getState() == State::ON);
    sw.step(true, 3000U); // fault after 2 cycles (10 ms)
    CHECK(sw.getState() == State::DIAG_FAULT);
    CHECK(sw.getLastFault() == Fault::IS_FAULT_LEVEL);

    HighSideSwitch off(FAST_CONFIG);
    steps(off, 4U, 201U);
    CHECK(off.getState() == State::OFF);
    off.step(true, 201U);
    CHECK(off.getState() == State::DIAG_FAULT);
    CHECK(off.getLastFault() == Fault::OFF_IMPLAUSIBLE);
}

/* Records the pin writes of a channel; each pin can be made to fail. */
class MockIo : public ::highside::IHighSideSwitchIo
{
public:
    static constexpr int MAX_WRITES = 16;

    bool setEnable(bool const on) override { return record('E', on, failEnable); }

    bool setDiagEnable(bool const on) override { return record('D', on, failDiag); }

    bool readSense(::highside::SenseSample& sample) override
    {
        ++reads;
        if (senseOk)
        {
            sample.loadMa = senseMa;
            sample.raw    = senseRaw;
        }
        return senseOk;
    }

    /* Writes since the last clear() as text, e.g. "D1E1" = DEN high, then IN high. */
    char const* sequence() const { return _log; }

    void clear()
    {
        _count  = 0;
        _log[0] = '\0';
    }

    bool enable      = false;
    bool diag        = false;
    bool failEnable  = false;
    bool failDiag    = false;
    bool senseOk     = true;
    uint32_t senseMa  = 0U;
    uint32_t senseRaw = 0U;
    int reads         = 0;

private:
    bool record(char const pin, bool const on, bool const fail)
    {
        if (_count < MAX_WRITES)
        {
            _log[2 * _count]     = pin;
            _log[2 * _count + 1] = on ? '1' : '0';
            ++_count;
            _log[2 * _count] = '\0';
        }
        if (fail)
        {
            return false;
        }
        if (pin == 'E')
        {
            enable = on;
        }
        else
        {
            diag = on;
        }
        return true;
    }

    char _log[2 * MAX_WRITES + 1] = {'\0'};
    int _count                    = 0;
};

bool same(char const* a, char const* b)
{
    while ((*a != '\0') && (*a == *b))
    {
        ++a;
        ++b;
    }
    return *a == *b;
}

void testChannelWriteOrder()
{
    std::printf("channel: write order and cycle\n");
    MockIo io;
    io.enable = true; // unknown pin state before init
    io.diag   = true;
    ::highside::HighSideSwitchChannel ch(io, TEST_CONFIG);
    ch.init();
    CHECK(same(io.sequence(), "E0D0")); // IN off before DEN
    CHECK(!io.enable && !io.diag);

    io.clear();
    CHECK(ch.requestOn() == CommandResult::OK);
    CHECK(same(io.sequence(), "D1E1")); // DEN on before IN, right with the command
    CHECK(io.enable && io.diag);

    io.clear();
    io.senseMa  = 800U;
    io.senseRaw = 440U;
    ch.cycle();
    CHECK(io.reads == 1);
    CHECK(same(io.sequence(), "D1E1")); // rewritten every cycle
    ch.cycle();
    ch.cycle();
    CHECK(ch.getSwitch().getMaxOnMa() == 800U); // sample reaches the state machine after blanking
    CHECK(ch.getSwitch().getLastMa() == 800U);
    CHECK(ch.getLastSenseRaw() == 440U);        // raw value kept for the diagnosis output

    // Overcurrent trips after 3 cycles and switches the pins off.
    io.senseMa = 2500U;
    ch.cycle();
    ch.cycle();
    CHECK(io.enable);
    io.clear();
    ch.cycle();
    CHECK(ch.getSwitch().getState() == State::OVERCURRENT);
    CHECK(same(io.sequence(), "E0D0"));
    CHECK(!io.enable && !io.diag);
    CHECK(ch.requestOn() == CommandResult::LATCHED);
    CHECK(!io.enable && !io.diag);

    io.clear();
    CHECK(ch.requestOff() == CommandResult::ALREADY);
    CHECK(same(io.sequence(), "E0D0"));
    CHECK(ch.reset() == CommandResult::OK);
    CHECK(!io.enable && !io.diag);
    CHECK(ch.getOutputErrors() == 0U);
}

void testChannelSenseError()
{
    std::printf("channel: sense read error\n");
    MockIo io;
    ::highside::HighSideSwitchChannel ch(io, TEST_CONFIG);
    ch.init();
    (void)ch.requestOn();
    io.senseMa  = 700U;
    io.senseRaw = 385U;
    ch.cycle();
    io.senseOk = false;
    ch.cycle();
    CHECK(ch.getSwitch().getState() == State::DIAG_FAULT);
    CHECK(ch.getSwitch().getLastFault() == Fault::ADC_ERROR);
    CHECK(!io.enable && !io.diag);
    // a failed read changes neither the last load current nor the last raw value
    CHECK(ch.getSwitch().getLastMa() == 700U);
    CHECK(ch.getLastSenseRaw() == 385U);
}

/* Characteristics as the integration passes them (generated nxAdcPhys_<channel>): a linear one with an end of the
 * converter marked as out of range, and one that goes negative (offset). */
uint8_t lastQuality = 0U;

int32_t linearPhys(uint16_t const raw, uint8_t* const quality)
{
    *quality = ((raw == 0U) || (raw >= 4095U)) ? 32U : 1U;
    return static_cast<int32_t>(raw) * 2;
}

int32_t offsetPhys(uint16_t const raw, uint8_t* const quality)
{
    *quality    = 1U;
    lastQuality = *quality;
    return static_cast<int32_t>(raw) - 100;
}

void testSenseConversion()
{
    std::printf("sense conversion: generated characteristic -> load current in mA\n");
    CHECK(::highside::loadCurrentMa(&linearPhys, 1000U) == 2000U);
    // both ends of the converter are regular readings (off: 0, fault current: full scale), quality not used
    CHECK(::highside::loadCurrentMa(&linearPhys, 0U) == 0U);
    CHECK(::highside::loadCurrentMa(&linearPhys, 4095U) == 8190U);
    // a negative value counts as no current
    CHECK(::highside::loadCurrentMa(&offsetPhys, 99U) == 0U);
    CHECK(::highside::loadCurrentMa(&offsetPhys, 100U) == 0U);
    CHECK(::highside::loadCurrentMa(&offsetPhys, 101U) == 1U);
    CHECK(lastQuality == 1U);
}

void testChannelOutputError()
{
    std::printf("channel: output write error\n");
    MockIo io;
    ::highside::HighSideSwitchChannel ch(io, TEST_CONFIG);
    ch.init();
    io.failDiag = true; // DEN cannot be written: the switch must not stay on
    io.clear();
    CHECK(ch.requestOn() == CommandResult::OK);
    CHECK(ch.getSwitch().getState() == State::OFF);
    CHECK(!io.enable);
    CHECK(same(io.sequence(), "D1E1E0D0")); // off written again after the failed on
    CHECK(ch.getOutputErrors() == 1U);

    // Failing write while off: counted, stays off, no extra writes.
    io.clear();
    io.failDiag   = false;
    io.failEnable = true;
    ch.cycle();
    CHECK(ch.getSwitch().getState() == State::OFF);
    CHECK(ch.getOutputErrors() == 2U);
    CHECK(same(io.sequence(), "E0D0"));

    // IN write fails while on (after a successful on): switched off, DEN low.
    io.failEnable = false;
    (void)ch.requestOn();
    CHECK(io.enable && io.diag);
    io.failEnable = true;
    ch.cycle();
    CHECK(ch.getSwitch().getState() == State::OFF);
    CHECK(ch.getOutputErrors() == 3U);
    CHECK(!io.diag);
}

/* SYS-36: the safe state lock of the state machine - off, "on" refused, orthogonal to the latched faults. */
void testLock()
{
    std::printf("lock (safe state)\n");
    HighSideSwitch sw(TEST_CONFIG);
    switchOn(sw);
    CHECK(outputsOn(sw));
    sw.lock();
    CHECK(sw.isLocked());
    CHECK(sw.getState() == State::OFF);
    CHECK(outputsOff(sw));
    CHECK(sw.requestOn() == CommandResult::LOCKED);
    CHECK(sw.getCounters().switchOns == 1U); // a refused "on" is no switch-on
    steps(sw, 100U, 0U); // supervision keeps running while locked, the switch stays off
    CHECK(outputsOff(sw));
    sw.lock(); // idempotent
    CHECK(sw.isLocked());
    sw.unlock();
    CHECK(!sw.isLocked());
    CHECK(sw.getState() == State::OFF); // unlocking switches nothing on
    CHECK(sw.requestOn() == CommandResult::OK);
    CHECK(outputsOn(sw));

    // a latched fault stays latched through lock and unlock; reset clears it, the lock still refuses "on"
    HighSideSwitch f(TEST_CONFIG);
    switchOn(f);
    steps(f, 3U, 2500U);
    CHECK(f.getState() == State::OVERCURRENT);
    f.lock();
    CHECK(f.getState() == State::OVERCURRENT);
    CHECK(f.requestOn() == CommandResult::LOCKED);
    CHECK(f.reset() == CommandResult::OK);
    CHECK(f.getState() == State::OFF);
    CHECK(f.requestOn() == CommandResult::LOCKED);
    f.unlock();
    CHECK(f.requestOn() == CommandResult::OK);
    CHECK(same(HighSideSwitch::resultName(CommandResult::LOCKED), "refused, safe state (locked until reset)"));
}

bool lockSet = false;

bool lockQuery() { return lockSet; }

/* SYS-36: the channel asks the lock in every cycle and before every "on" and never writes IN high while it is set. */
void testChannelSwitchOnLock()
{
    std::printf("channel: switch-on lock\n");
    MockIo io;
    ::highside::HighSideSwitchChannel ch(io, TEST_CONFIG);
    CHECK(!ch.updateSwitchOnLock()); // no lock attached: never locked
    ch.setSwitchOnLock(&lockQuery);
    lockSet = false;
    ch.init();
    CHECK(ch.requestOn() == CommandResult::OK);
    io.senseMa = 800U;
    ch.cycle();
    CHECK(io.enable && io.diag);

    // S1 pulls IN low behind the driver's back and sets the lock: the next cycle does not rewrite it high
    lockSet    = true;
    io.enable  = false;
    io.senseMa = 0U; // no load current with IN low (the off supervision keeps running while locked)
    io.clear();
    ch.cycle();
    CHECK(same(io.sequence(), "E0D0"));
    CHECK(ch.getSwitch().isLocked());
    CHECK(ch.getSwitch().getState() == State::OFF);
    for (int i = 0; i < 100; ++i)
    {
        io.clear();
        CHECK(ch.requestOn() == CommandResult::LOCKED);
        CHECK(same(io.sequence(), "E0D0"));
        ch.cycle();
        CHECK(!io.enable && !io.diag);
    }
    CHECK(ch.updateSwitchOnLock());
    CHECK(ch.getOutputErrors() == 0U); // the lock is no output error

    // updateSwitchOnLock() acts at once, without waiting for the cycle
    lockSet = false;
    CHECK(!ch.updateSwitchOnLock());
    CHECK(ch.requestOn() == CommandResult::OK);
    CHECK(io.enable);
    lockSet = true;
    io.clear();
    CHECK(ch.updateSwitchOnLock());
    CHECK(same(io.sequence(), "E0D0"));

    // lock released: stays off until the next "on"
    lockSet = false;
    ch.cycle();
    CHECK(!ch.getSwitch().isLocked());
    CHECK(!io.enable);
    CHECK(ch.requestOn() == CommandResult::OK);
    CHECK(io.enable);
    ch.setSwitchOnLock(nullptr);
}
} // namespace

int main()
{
    testDefaultOff();
    testOnOff();
    testBlanking();
    testOvercurrentTrip();
    testOvercurrentDebounce();
    testFaultLevel();
    testAdcError();
    testOffPlausibility();
    testOpenLoad();
    testConfigValidation();
    testOtherProfile();
    testChannelWriteOrder();
    testChannelSenseError();
    testChannelOutputError();
    testSenseConversion();
    testLock();
    testChannelSwitchOnLock();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
