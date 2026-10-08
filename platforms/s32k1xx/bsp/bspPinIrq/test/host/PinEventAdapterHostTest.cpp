// Host test of the family end of the pin events (IO-54, pinIrq/PinEventAdapter.h): the adapter between the
// pin-interrupt dispatcher of the s32k1xx and the family neutral module libs/bsp/bspPinEvent, end to end through the
// real dispatcher, the real module and the register models of the dispatcher test (PORT with write-1-to-clear ISFR
// and edge detection per IRQC, GPIO PDIR, NVIC).
//
// Table (independent of any board): two pin events and a foreign consumer on the same vector
//   E0  PTA6, active low,  edge activate, no debounce      -> detector FALLING (the physical activate edge)
//   E1  PTC3, active low,  edge activate, debounce 1000 us -> detector BOTH (level confirmation)
//   PTA1  another consumer (e.g. a Hall signal), both edges, on the vector of PORTA as well
// Checked: the physical edge of every logical request, the start-up order of the port (dispatcher init -> start of
// the module from the sampled levels -> enable) with an edge between init and start that is still counted, counting
// with the inversion of the board, debounce with the level sampled after the flag was cleared, the rate limit
// switching the detector off from inside the interrupt and cycle() switching it on again after the hold-off, the
// foreign consumer unaffected, unknown inputs. Build, run and mutants: test_pin_irq_host.py (tools/test -k pin-irq).

#include "pinIrq/PinEventAdapter.h"

#include <cstdio>
#include <cstring>

NxTestPort fakePort[NX_TEST_PORTS] = {};
GPIO_Type fakeGpio[NX_TEST_PORTS]  = {};
NvicModel fakeNvic                 = {};
uint32_t nxTestNowUs               = 1000U;
unsigned nxTestTimeReads           = 0U;

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

using bios::pinirq::Edge;
using bios::pinirq::EventPin;
using bios::pinirq::PinUse;
using bios::pinirq::VectorUse;
using ::pinevent::Detect;

// the logical request -> physical edge of the family, for both levels of the board
static_assert(bios::pinirq::physicalEdge(Detect::NONE, false) == Edge::NONE, "none");
static_assert(bios::pinirq::physicalEdge(Detect::NONE, true) == Edge::NONE, "none, low");
static_assert(bios::pinirq::physicalEdge(Detect::BOTH, true) == Edge::BOTH, "both");
static_assert(bios::pinirq::physicalEdge(Detect::ACTIVATE, false) == Edge::RISING, "activate, high");
static_assert(bios::pinirq::physicalEdge(Detect::ACTIVATE, true) == Edge::FALLING, "activate, low");
static_assert(bios::pinirq::physicalEdge(Detect::DEACTIVATE, false) == Edge::FALLING, "deactivate, high");
static_assert(bios::pinirq::physicalEdge(Detect::DEACTIVATE, true) == Edge::RISING, "deactivate, low");

class Recorder final : public ::bsp::pinedge::IPinEdgeConsumer
{
public:
    void onEdge(uint16_t /* channel */, uint32_t /* timestampUs */) override { ++count; }

    unsigned count = 0U;
};

constexpr uint8_t PA = 0U;
constexpr uint8_t PC = 2U;

constexpr uint32_t bit(uint8_t const pin) { return 1UL << pin; }

uint32_t taskNowUs() { return nxTestNowUs; } // the task side reads without advancing the time

constexpr ::pinevent::PinEventConfig CONFIG[] = {
    {"E0", ::pinevent::Edge::ACTIVATE, 0U, 3U, 100000U, 5000U},
    {"E1", ::pinevent::Edge::ACTIVATE, 1000U, 10U, 100000U, 5000U},
};
static_assert(::pinevent::isValidConfig(CONFIG[0]) && ::pinevent::isValidConfig(CONFIG[1]), "valid");

extern bios::PinIrqDispatcher dispatcher;
extern ::pinevent::PinEvents events;

constexpr EventPin EVENT_PINS[] = {{PA, 6U, true}, {PC, 3U, true}};
bios::PinEventAdapter adapter(EVENT_PINS, 2U, dispatcher, events);
Recorder other;

constexpr PinUse PINS[] = {
    {PA, 6U, Edge::FALLING, &adapter, 0U},
    {PC, 3U, Edge::BOTH, &adapter, 1U},
    {PA, 1U, Edge::BOTH, &other, 0U},
};
constexpr VectorUse VECTORS[] = {
    {PORTA_IRQn, 7U, static_cast<uint8_t>(1U << PA)},
    {PORTC_IRQn, 7U, static_cast<uint8_t>(1U << PC)},
};
static_assert(bios::pinirq::check(PINS, VECTORS) == bios::pinirq::TableError::OK, "table");
constexpr bios::pinirq::PortMaps MAPS = bios::pinirq::makePortMaps(PINS);
constexpr bios::pinirq::Config CONFIG_TABLE{PINS, 3U, VECTORS, 2U, &MAPS};
bios::PinIrqDispatcher dispatcher(CONFIG_TABLE);

::pinevent::PinEvent inputs[] = {::pinevent::PinEvent(CONFIG[0]), ::pinevent::PinEvent(CONFIG[1])};
::pinevent::PinEvents events(inputs, 2U, adapter, &taskNowUs);

// ----------------------------------------------------------------------------------------- model of the pins

uint32_t irqcOf(uint8_t const port, uint8_t const pin)
{
    return (fakePort[port].PCR[pin] & PORT_PCR_IRQC_MASK) >> PORT_PCR_IRQC_SHIFT;
}

/** One physical pin level; the hardware sets the flag only while the pin is armed for this edge. */
void setPin(uint8_t const port, uint8_t const pin, bool const high)
{
    bool const before = (fakeGpio[port].PDIR & bit(pin)) != 0UL;
    if (before == high)
    {
        return;
    }
    fakeGpio[port].PDIR = high ? (fakeGpio[port].PDIR | bit(pin)) : (fakeGpio[port].PDIR & ~bit(pin));
    uint32_t const irqc = irqcOf(port, pin);
    if ((irqc == 11U) || ((irqc == 9U) && high) || ((irqc == 10U) && !high))
    {
        fakePort[port].ISFR.set(bit(pin));
    }
}

/** The NVIC: the vector of a port runs while one of its flags is set (and the vector is enabled). */
void runInterrupts()
{
    for (uint8_t v = 0U; v < 2U; ++v)
    {
        uint8_t const port = (v == 0U) ? PA : PC;
        if ((static_cast<uint32_t>(fakePort[port].ISFR) != 0U)
            && ((fakeNvic.enabled & (1UL << nxTestIrqIndex(VECTORS[v].irq))) != 0UL))
        {
            dispatcher.isr(v);
        }
    }
}

/** A logical press or release of an active-low input: the pin goes low when the input becomes active. */
void setInput(uint8_t const port, uint8_t const pin, bool const active)
{
    setPin(port, pin, !active);
    runInterrupts();
}

::pinevent::Reading read(uint8_t const input)
{
    ::pinevent::Reading r{0xDEADU, 0xDEADU, 0xFFU};
    CHECK(events.read(input, r));
    return r;
}

void resetModel()
{
    std::memset(static_cast<void*>(fakePort), 0, sizeof(fakePort));
    std::memset(static_cast<void*>(fakeGpio), 0, sizeof(fakeGpio));
    std::memset(static_cast<void*>(&fakeNvic), 0, sizeof(fakeNvic));
    other.count = 0U;
}

/** Start-up as bsp::startPinInterrupts() of the port: init, the module from the sampled levels, enable. */
void startAll(bool const pressBetween)
{
    resetModel();
    fakeGpio[PA].PDIR = bit(6U) | bit(1U); // both inputs inactive (high, active low)
    fakeGpio[PC].PDIR = bit(3U);
    dispatcher.init();
    if (pressBetween)
    {
        setPin(PA, 6U, false); // E0 pressed after the arming, before the module samples its level
    }
    events.start();
    dispatcher.enable();
    runInterrupts();
}

// ---------------------------------------------------------------------------------------------- test cases

void testStartAndLevels()
{
    startAll(false);
    // the detector each input needs: E0 the physical activate edge of an active-low input, E1 both (debounce)
    CHECK(irqcOf(PA, 6U) == 10U);
    CHECK(irqcOf(PC, 3U) == 11U);
    CHECK(irqcOf(PA, 1U) == 11U); // the foreign consumer keeps the edge of the table
    CHECK(!adapter.level(0U) && !adapter.level(1U));
    fakeGpio[PA].PDIR &= ~bit(6U);
    CHECK(adapter.level(0U)); // low = active
    fakeGpio[PA].PDIR |= bit(6U);
    CHECK(!adapter.level(7U)); // unknown input
    ::pinevent::Reading r = read(0U);
    CHECK((r.count == 0U) && (r.quality == ::pinevent::QUALITY_DEBOUNCE));

    // an edge between the arming and the start of the module is an edge after the sampled level: counted
    startAll(true);
    r = read(0U);
    CHECK((r.count == 1U) && (r.quality == ::pinevent::QUALITY_OK));
}

void testCountingWithoutDebounce()
{
    startAll(false);
    nxTestNowUs = 20000U;
    setInput(PA, 6U, true); // press: falling edge
    ::pinevent::Reading r = read(0U);
    CHECK((r.count == 1U) && (r.quality == ::pinevent::QUALITY_OK));
    CHECK(r.timeUs == 20000U); // the time of the interrupt (the dispatcher reads it once)
    nxTestNowUs += 30000U;
    setInput(PA, 6U, false); // release: rising edge, not armed
    r = read(0U);
    CHECK(r.count == 1U);
    CHECK(static_cast<uint32_t>(fakePort[PA].ISFR) == 0U);
}

void testDebounceUsesTheSampledLevel()
{
    startAll(false);
    nxTestNowUs = 50000U;
    setInput(PC, 3U, true); // press opens the lock-out; level sampled after the flag was cleared: active
    nxTestNowUs += 200U;
    setInput(PC, 3U, false); // bounce
    nxTestNowUs += 200U;
    setInput(PC, 3U, true); // bounce, ends active
    ::pinevent::Reading r = read(1U);
    CHECK(r.count == 0U); // lock-out still running
    nxTestNowUs += 2000U;
    r = read(1U);
    CHECK((r.count == 1U) && (r.quality == ::pinevent::QUALITY_OK) && (r.timeUs == 50000U));
    // a short spike towards inactive and back: a glitch, not counted as activate
    nxTestNowUs += 10000U;
    setInput(PC, 3U, false);
    nxTestNowUs += 100U;
    setInput(PC, 3U, true);
    nxTestNowUs += 5000U;
    r = read(1U);
    CHECK(r.count == 1U);
    ::pinevent::Statistics s{};
    CHECK(events.statistics(1U, s) && (s.glitches == 1U));
}

void testRateLimitDisarmsAndCycleRearms()
{
    startAll(false);
    nxTestNowUs = 100000U;
    for (unsigned i = 0U; i < 3U; ++i)
    {
        setInput(PA, 6U, true);
        setInput(PA, 6U, false);
        nxTestNowUs += 1000U;
    }
    CHECK(read(0U).count == 3U);
    CHECK(irqcOf(PA, 6U) == 10U);
    // the foreign pin of the same vector flags in the same interrupt as the tripping edge
    setPin(PA, 1U, false);
    setInput(PA, 6U, true); // 4th candidate within the window: tripped inside onEdge -> detector off
    CHECK(irqcOf(PA, 6U) == 0U);
    CHECK(static_cast<uint32_t>(fakePort[PA].ISFR) == 0U);
    CHECK(other.count == 1U);
    CHECK(irqcOf(PA, 1U) == 11U); // the other consumer stays armed
    ::pinevent::Reading r = read(0U);
    CHECK((r.count == 3U) && (r.quality == ::pinevent::QUALITY_ERROR));
    setInput(PA, 6U, false);
    setInput(PA, 6U, true); // nothing while off
    CHECK(read(0U).count == 3U);

    nxTestNowUs += 1000U;
    events.cycle(); // hold-off not over
    CHECK(irqcOf(PA, 6U) == 0U);
    nxTestNowUs += 5000U;
    events.cycle(); // re-armed from the level sampled now (active), the activate edge again
    CHECK(irqcOf(PA, 6U) == 10U);
    r = read(0U);
    CHECK((r.count == 3U) && (r.quality == ::pinevent::QUALITY_OK));
    setInput(PA, 6U, false);
    setInput(PA, 6U, true);
    CHECK(read(0U).count == 4U);
}

void testArmRequests()
{
    startAll(false);
    adapter.arm(0U, Detect::NONE);
    CHECK(irqcOf(PA, 6U) == 0U);
    adapter.arm(0U, Detect::DEACTIVATE);
    CHECK(irqcOf(PA, 6U) == 9U); // deactivate of an active-low input: rising
    adapter.arm(1U, Detect::ACTIVATE);
    CHECK(irqcOf(PC, 3U) == 10U);
    adapter.arm(9U, Detect::BOTH); // unknown input: nothing
    CHECK((irqcOf(PA, 6U) == 9U) && (irqcOf(PC, 3U) == 10U) && (irqcOf(PA, 1U) == 11U));
    // an edge for a channel the module does not know switches that channel off (it has no pin), and nothing else
    adapter.onEdge(200U, nxTestNowUs);
    CHECK((irqcOf(PA, 6U) == 9U) && (irqcOf(PC, 3U) == 10U) && (irqcOf(PA, 1U) == 11U));
}
} // namespace

int main()
{
    testStartAndLevels();
    testCountingWithoutDebounce();
    testDebounceUsesTheSampledLevel();
    testRateLimitDisarmsAndCycleRearms();
    testArmRequests();
    std::printf("pin_event_adapter_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
