// Host test of the s32k1xx access hall/HallSensors.h/.cpp with the configuration bswgen generates for a project
// with hall sensor sets (IO-07). Every port is a RAM model with the real register layout (S32K148_GPIO.h,
// S32K148_PORT.h) plus a model of the pin detect hardware: a level change sets the interrupt flag only while the
// pin is armed for an edge interrupt, the flag stays until the driver clears it, and the model calls the vector of
// that port like the NVIC would. The microsecond time base is the test's own variable.
//
// The evaluation itself (sector, direction, debouncing with confirmation, period, standstill, quality) is the
// family neutral libs/bsw/hallDecoder and has its own host test (HallDecoderHostTest.cpp). Here only what the
// access adds is checked, end to end through the pins: register picture after init, the sampled state with the
// inversion of the board, a revolution forwards and backwards through the pin interrupts of all ports of a sensor
// (so pins, ports, decoder configuration and pole pairs reach the decoder), standstill from the time base, no
// sensor at all, independence of two sensors, foreign interrupt flags, names, and that get() takes state and time
// under the interrupt lock.
// IO-52: the pin interrupt runs through the family dispatcher (platforms/s32k1xx/bsp/bspPinIrq) with the table the
// port builds from the Hall configuration (hall/HallPinIrq.h) - the same start-up order as the port
// (bsp::startPinInterrupts(): dispatcher init, HallSensors::init(), dispatcher enable), so this test checks the Hall
// path end to end through the dispatcher.
// Build and run: tools/bswgen/test_bswgen.py (BswGenHallHostTest).

#include "hall/HallPinIrq.h"
#include "hall/HallSensors.h"
#include "pinIrq/PinIrqDispatcher.h"

#include <cstdio>
#include <cstring>

GPIO_Type fakeGpio[NX_TEST_PORTS]  = {};
NxTestPort fakePort[NX_TEST_PORTS] = {};
NvicModel fakeNvic                 = {};
uint32_t nxTestNowUs               = 0U;
unsigned nxTestLockCount           = 0U;
unsigned nxTestLockDepth           = 0U;
unsigned nxTestLockMaxDepth        = 0U;
unsigned nxTestTimeReadsUnlocked   = 0U;

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

using bios::HallSensors;
namespace h = bios::hall;

constexpr uint16_t SENSORS = static_cast<uint16_t>(h::_hallSensorMax);
constexpr uint8_t STEPS    = HallSensors::SECTORS;
constexpr unsigned EDGES_TO_OK = STEPS + 1U;

// The dispatcher table exactly as bspConfiguration/src/bsp/pinIrq/PinIrqConfig.cpp builds it.
constexpr h::PinIrqPins PIN_IRQ_PINS       = h::pinIrqPins();
constexpr h::PinIrqVectors PIN_IRQ_VECTORS = h::pinIrqVectors();
constexpr bios::pinirq::PortMaps PIN_IRQ_MAPS = bios::pinirq::makePortMaps(PIN_IRQ_PINS.pin);
static_assert(
    bios::pinirq::check(PIN_IRQ_PINS.pin, PIN_IRQ_VECTORS.vector) == bios::pinirq::TableError::OK,
    "the table derived from the hall configuration is valid");
constexpr uint8_t VECTOR_COUNT = h::PORT_COUNT;
constexpr bios::pinirq::Config PIN_IRQ_CONFIG{
    PIN_IRQ_PINS.pin, h::PIN_IRQ_PIN_COUNT, PIN_IRQ_VECTORS.vector, VECTOR_COUNT, &PIN_IRQ_MAPS};
bios::PinIrqDispatcher dispatcher(PIN_IRQ_CONFIG);

/** Start-up as bsp::startPinInterrupts() of the port does it. */
void initAll()
{
    dispatcher.init();
    HallSensors::init();
    dispatcher.enable();
}

// ------------------------------------------------------------------------ model of the pins and the interrupt

void advance(uint32_t const us) { nxTestNowUs += us; }

uint8_t irqcOf(h::PinRef const& ref)
{
    return static_cast<uint8_t>((fakePort[ref.port].PCR[ref.pin] & PORT_PCR_IRQC_MASK) >> PORT_PCR_IRQC_SHIFT);
}

/** One pin level; the hardware sets the interrupt flag only while the pin is armed for an edge interrupt. */
void setPin(h::PinRef const& ref, bool const level)
{
    uint32_t const bit = 1UL << ref.pin;
    bool const before  = (fakeGpio[ref.port].PDIR & bit) != 0UL;
    if (before == level)
    {
        return;
    }
    fakeGpio[ref.port].PDIR = level ? (fakeGpio[ref.port].PDIR | bit) : (fakeGpio[ref.port].PDIR & ~bit);
    if (irqcOf(ref) == h::PCR_IRQC_EITHER_EDGE)
    {
        fakePort[ref.port].ISFR.set(bit);
    }
}

/** Puts the three lines of a sensor to the levels that produce `state` (the inversion of the board applied). */
void applyState(uint16_t const sensor, uint8_t const state)
{
    h::SensorConfig const& cfg = h::SENSORS[sensor];
    uint8_t const raw          = static_cast<uint8_t>(state ^ cfg.invert);
    for (uint8_t i = 0U; i < 3U; ++i)
    {
        setPin(cfg.pins[i], ((raw >> (2U - i)) & 1U) != 0U);
    }
}

/** The NVIC calls the vector of every enabled port interrupt whose flag is set. */
void servicePendingInterrupts()
{
    for (uint8_t p = 0U; p < h::PORT_COUNT; ++p)
    {
        h::PortConfig const& port = h::PORTS[p];
        if ((fakeNvic.enabled & (1UL << nxTestIrqIndex(port.irq))) == 0UL)
        {
            continue;
        }
        if ((fakePort[port.port].ISFR & port.mask) != 0UL)
        {
            dispatcher.isr(p); // vector p of the table = entry p of hall::PORTS (hall/HallPinIrq.h)
        }
    }
}

void driveState(uint16_t const sensor, uint8_t const state)
{
    applyState(sensor, state);
    servicePendingInterrupts();
}

// ------------------------------------------------------------------------------- expectations from the config

uint8_t stateOf(uint16_t const sensor, uint8_t const sector)
{
    for (uint8_t s = 0U; s < 8U; ++s)
    {
        if (h::DECODERS[sensor].sectors[s] == sector)
        {
            return s;
        }
    }
    return 0xFFU;
}

uint8_t nextSector(uint8_t const sector) { return (sector >= STEPS) ? 1U : static_cast<uint8_t>(sector + 1U); }

uint8_t previousSector(uint8_t const sector) { return (sector <= 1U) ? STEPS : static_cast<uint8_t>(sector - 1U); }

uint32_t expectedSpeedRpm(uint32_t const period, uint16_t const polePairs)
{
    uint64_t const divisor = static_cast<uint64_t>(period) * polePairs;
    return static_cast<uint32_t>((60ULL * 1000ULL * 1000ULL + (divisor / 2U)) / divisor);
}

uint32_t stepTimeOf(uint16_t const sensor)
{
    ::hallDecoder::HallDecoderConfig const& cfg = h::DECODERS[sensor];
    uint32_t step                               = cfg.standstillUs / 10U;
    if (step <= (2U * cfg.debounceUs))
    {
        step = 2U * cfg.debounceUs + 1U;
    }
    return step;
}

/** Puts every sensor into a valid position, `sensor` into `sector`, and restarts the driver. */
void restart(uint16_t const sensor, uint8_t const sector)
{
    for (uint16_t i = 0U; i < SENSORS; ++i)
    {
        applyState(i, stateOf(i, 1U));
    }
    applyState(sensor, stateOf(sensor, sector));
    for (unsigned p = 0U; p < NX_TEST_PORTS; ++p)
    {
        fakePort[p].ISFR.clear();
    }
    for (uint8_t p = 0U; p < h::PORT_COUNT; ++p)
    {
        // stale flags from before the start: init() has to clear the flags of its own pins
        fakePort[h::PORTS[p].port].ISFR.set(h::PORTS[p].mask);
    }
    advance(1U);
    initAll();
}

// ------------------------------------------------------------------------------------------------- test cases

void testInitialRegisters()
{
    h::PortConfig const& first = h::PORTS[0];
    uint8_t foreign            = 0xFFU;
    for (uint8_t pin = 0U; pin < 32U; ++pin)
    {
        if ((first.mask & (1UL << pin)) == 0UL)
        {
            foreign = pin;
            break;
        }
    }
    CHECK(foreign != 0xFFU);
    uint32_t const marker             = PORT_PCR_MUX(1U) | PORT_PCR_IRQC(0U) | 0x3U; // GPIO with a pull
    fakePort[first.port].PCR[foreign] = marker;
    uint8_t const hallPin             = static_cast<uint8_t>(h::SENSORS[0].pins[0].pin);
    uint8_t const hallPort            = static_cast<uint8_t>(h::SENSORS[0].pins[0].port);
    fakePort[hallPort].PCR[hallPin]   = PORT_PCR_MUX(1U) | 0x2U;
    restart(0U, 1U);
    CHECK(fakePort[first.port].PCR[foreign] == marker);
    CHECK((fakePort[hallPort].PCR[hallPin] & PORT_PCR_MUX_MASK) == PORT_PCR_MUX(1U));
    CHECK((fakePort[hallPort].PCR[hallPin] & 0x3U) == 0x2U);

    for (uint8_t p = 0U; p < h::PORT_COUNT; ++p)
    {
        h::PortConfig const& port = h::PORTS[p];
        CHECK((fakePort[port.port].ISFR & port.mask) == 0UL);
        CHECK((fakeNvic.enabled & (1UL << nxTestIrqIndex(port.irq))) != 0UL);
        CHECK(fakeNvic.priorityWritten[nxTestIrqIndex(port.irq)]);
        CHECK(fakeNvic.priority[nxTestIrqIndex(port.irq)] == port.level); // rank of the project (PLAT-21)
        CHECK(fakePort[port.port].DFER == 0U);
        for (uint8_t pin = 0U; pin < 32U; ++pin)
        {
            if ((port.mask & (1UL << pin)) != 0UL)
            {
                uint32_t const irqc = (fakePort[port.port].PCR[pin] & PORT_PCR_IRQC_MASK) >> PORT_PCR_IRQC_SHIFT;
                CHECK(irqc == h::PCR_IRQC_EITHER_EDGE);
            }
        }
    }
    for (uint16_t i = 0U; i < SENSORS; ++i)
    {
        HallSensors::Reading const r = HallSensors::get(i);
        CHECK(r.quality == HallSensors::QUALITY_DEBOUNCE);
        CHECK(r.sector == 1U); // the position is read from the pins (inversion applied), not invented
        CHECK(r.state == stateOf(i, 1U));
        CHECK((r.period_us == 0U) && (r.speed_rpm == 0U) && (r.mech_period_us == 0U));
        HallSensors::Statistics const s = HallSensors::getStatistics(i);
        CHECK((s.edges == 0U) && (s.bounces == 0U) && (s.invalidStates == 0U) && (s.sectorJumps == 0U));
    }
    // an interrupt of a hall pin without a change of the levels: the decoders started from the pins, so no step
    for (uint8_t p = 0U; p < h::PORT_COUNT; ++p)
    {
        fakePort[h::PORTS[p].port].ISFR.set(h::PORTS[p].mask);
    }
    advance(1000U);
    servicePendingInterrupts();
    for (uint16_t i = 0U; i < SENSORS; ++i)
    {
        CHECK(HallSensors::getStatistics(i).edges == 0U);
    }
}

/** A full electrical revolution through the pin interrupts: every step reaches the decoder of this sensor. */
void testRevolution(uint16_t const sensor, bool const forward)
{
    ::hallDecoder::HallDecoderConfig const& cfg = h::DECODERS[sensor];
    uint32_t const step                         = stepTimeOf(sensor);
    restart(sensor, 1U);
    uint8_t sector      = 1U;
    int8_t const expect = forward ? cfg.forwardSign : static_cast<int8_t>(-cfg.forwardSign);
    for (unsigned edge = 1U; edge <= (2U * EDGES_TO_OK); ++edge)
    {
        advance(step);
        sector = forward ? nextSector(sector) : previousSector(sector);
        driveState(sensor, stateOf(sensor, sector));
        HallSensors::Reading const r = HallSensors::get(sensor);
        CHECK(r.sector == sector);
        CHECK(r.state == stateOf(sensor, sector));
        if (edge < EDGES_TO_OK)
        {
            CHECK(r.quality == HallSensors::QUALITY_DEBOUNCE);
        }
        else
        {
            CHECK(r.quality == HallSensors::QUALITY_OK);
            CHECK(r.period_us == STEPS * step);
            CHECK(r.mech_period_us == STEPS * step * cfg.polePairs);
            CHECK(r.speed_rpm == expectedSpeedRpm(STEPS * step, cfg.polePairs));
            CHECK(r.direction == expect);
        }
    }
    HallSensors::Statistics const s = HallSensors::getStatistics(sensor);
    CHECK(s.edges == (2U * EDGES_TO_OK));
    CHECK((s.bounces == 0U) && (s.invalidStates == 0U) && (s.sectorJumps == 0U) && (s.glitches == 0U));
}

/** No sensor connected: no edge, so standstill from the time base - never a sector of an invalid state. */
void testWithoutSensor(uint16_t const sensor)
{
    ::hallDecoder::HallDecoderConfig const& cfg = h::DECODERS[sensor];
    for (unsigned level = 0U; level < 2U; ++level)
    {
        restart(sensor, 1U);
        uint8_t const pins  = static_cast<uint8_t>((level == 0U) ? 0U : 7U);
        uint8_t const state = static_cast<uint8_t>(pins ^ h::SENSORS[sensor].invert);
        applyState(sensor, state);
        servicePendingInterrupts();
        advance(cfg.standstillUs + 1U);
        HallSensors::Reading const r = HallSensors::get(sensor);
        CHECK(r.quality == HallSensors::QUALITY_TIMEOUT);
        CHECK(r.state == state);
        CHECK((r.period_us == 0U) && (r.speed_rpm == 0U));
        CHECK(r.sector == cfg.sectors[state]);
    }
}

void testIndependence()
{
    if (SENSORS < 2U)
    {
        return;
    }
    restart(0U, 1U);
    uint32_t const step = stepTimeOf(0U);
    uint8_t sector      = 1U;
    for (unsigned edge = 0U; edge < (2U * EDGES_TO_OK); ++edge)
    {
        advance(step);
        sector = nextSector(sector);
        driveState(0U, stateOf(0U, sector));
    }
    CHECK(HallSensors::get(0U).quality == HallSensors::QUALITY_OK);
    advance(h::DECODERS[1].standstillUs + 1U);
    CHECK(HallSensors::get(1U).quality == HallSensors::QUALITY_TIMEOUT);
    CHECK(HallSensors::getStatistics(1U).edges == 0U);
    CHECK(HallSensors::getStatistics(1U).sectorJumps == 0U);
}

void testForeignInterrupt()
{
    restart(0U, 1U);
    h::PortConfig const& port = h::PORTS[0];
    uint32_t foreign          = 0U;
    for (uint8_t pin = 0U; pin < 32U; ++pin)
    {
        if ((port.mask & (1UL << pin)) == 0UL)
        {
            foreign = 1UL << pin;
            break;
        }
    }
    CHECK(foreign != 0U);
    fakePort[port.port].ISFR.set(foreign);
    dispatcher.isr(0U);
    CHECK((fakePort[port.port].ISFR & foreign) != 0UL);
    CHECK(HallSensors::getStatistics(0U).edges == 0U);
    dispatcher.isr(VECTOR_COUNT);
    CHECK(HallSensors::getStatistics(0U).edges == 0U);
    HallSensors::edge(SENSORS, nxTestNowUs); // an unknown sensor is ignored
    CHECK(HallSensors::getStatistics(0U).edges == 0U);

    uint8_t hallPin = 0U;
    while ((port.mask & (1UL << hallPin)) == 0UL)
    {
        ++hallPin;
    }
    h::PinRef const ref{port.port, hallPin};
    bool const level = (fakeGpio[port.port].PDIR & (1UL << hallPin)) != 0UL;
    advance(1U + h::DECODERS[0].debounceUs);
    setPin(ref, !level);
    servicePendingInterrupts();
    CHECK((fakePort[port.port].ISFR & foreign) != 0UL);
    CHECK((fakePort[port.port].ISFR & port.mask) == 0UL);
    fakePort[port.port].ISFR.clear();
}

void testNames()
{
    for (uint16_t i = 0U; i < SENSORS; ++i)
    {
        CHECK(std::strcmp(HallSensors::getName(i), h::NAMES[i]) == 0);
        CHECK(HallSensors::getName(i)[0] != '\0');
    }
    CHECK(HallSensors::getName(SENSORS)[0] == '\0');
    CHECK(HallSensors::get(SENSORS).quality == HallSensors::QUALITY_DEBOUNCE);
    CHECK(HallSensors::get(SENSORS).sector == 0U);
    CHECK(HallSensors::getStatistics(SENSORS).edges == 0U);
}

/** get() samples state and time and reads the decoder under the lock (see HallSensors.h). */
void testReadIsLocked()
{
    restart(0U, 1U);
    unsigned const before         = nxTestLockCount;
    unsigned const unlockedBefore = nxTestTimeReadsUnlocked;
    (void)HallSensors::get(0U);
    CHECK(nxTestLockCount > before);
    CHECK(nxTestLockMaxDepth == 1U);
    CHECK(nxTestTimeReadsUnlocked == unlockedBefore); // the time was taken inside the lock
}
} // namespace

int main()
{
    testInitialRegisters();
    testNames();
    testReadIsLocked();
    for (uint16_t sensor = 0U; sensor < SENSORS; ++sensor)
    {
        testRevolution(sensor, true);
        testRevolution(sensor, false);
        testWithoutSensor(sensor);
    }
    testIndependence();
    testForeignInterrupt();
    std::printf(
        "hall_sensors_host_test: %u sensor(s), %u port(s), %u checks, %u failures\n",
        static_cast<unsigned>(SENSORS),
        static_cast<unsigned>(h::PORT_COUNT),
        checks,
        failures);
    return (failures == 0U) ? 0 : 1;
}
