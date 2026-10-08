// Host test of the pin-interrupt dispatcher of the family s32k1xx (IO-52, pinIrq/PinIrqDispatcher.h). Every port is a
// RAM model with the real register layout (S32K148_PORT.h) and a write-1-to-clear ISFR, plus a model of the edge
// detection (a level change sets the flag only while the pin is armed for that edge) and of the NVIC. The time base
// advances with every read, so "one time per interrupt" is visible.
//
// Two tables, both independent of any board:
//   - S32K14x shape: one vector per port (PORTA, PORTC), several pins per port, two consumers, one channel spread
//     over two ports, all three edges;
//   - S32K11x shape: one vector for all ports, pins on PORTA, PORTB and PORTE.
// Checked: register picture after init() (edge per pin, nothing else of the PCR changed, the flag bit not written
// back, stale flags of own pins cleared, foreign flags kept, vectors off until enable()), enable() (NVIC level and
// request), the interrupt (every flagged pin of the vector to its consumer and channel, one time per interrupt,
// ascending pins, only own flags cleared, other vectors' ports untouched, unknown vector), the edges of the model,
// the shared vector, arming and disarming (from a task and from inside onEdge), and the compile-time table checks.
// Build, run and mutants: test_pin_irq_host.py next to this file (tools/test -k pin-irq).

#include "pinIrq/PinIrqDispatcher.h"

#include <cstdio>
#include <cstring>

NxTestPort fakePort[NX_TEST_PORTS] = {};
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

using bios::pinirq::Config;
using bios::pinirq::Edge;
using bios::pinirq::PinUse;
using bios::pinirq::PortMaps;
using bios::pinirq::TableError;
using bios::pinirq::VectorUse;

// --------------------------------------------------------------------------------------------- consumers

struct Call
{
    uint16_t channel;
    uint32_t time;
};

class Recorder final : public ::bsp::pinedge::IPinEdgeConsumer
{
public:
    constexpr Recorder() = default;

    void onEdge(uint16_t const channel, uint32_t const timestampUs) override
    {
        if (count < MAX_CALLS)
        {
            calls[count] = Call{channel, timestampUs};
        }
        ++count;
        if (disarmOnEdge != nullptr)
        {
            (void)disarmOnEdge->setArmed(*this, channel, false);
        }
    }

    void reset()
    {
        count        = 0U;
        disarmOnEdge = nullptr;
    }

    unsigned countOf(uint16_t const channel) const
    {
        unsigned n = 0U;
        for (unsigned i = 0U; (i < count) && (i < MAX_CALLS); ++i)
        {
            n += (calls[i].channel == channel) ? 1U : 0U;
        }
        return n;
    }

    static constexpr unsigned MAX_CALLS = 32U;
    Call calls[MAX_CALLS]{};
    unsigned count                                    = 0U;
    ::bsp::pinedge::IPinEdgeControl* disarmOnEdge     = nullptr;
};

Recorder consumerA;
Recorder consumerB;
Recorder unused;

// ------------------------------------------------------------------------------------------------ tables

constexpr uint8_t PA = 0U;
constexpr uint8_t PB = 1U;
constexpr uint8_t PC = 2U;
constexpr uint8_t PE = 4U;

constexpr uint32_t bit(uint8_t const pin) { return 1UL << pin; }

// S32K14x shape: vector per port
constexpr PinUse K14_PINS[] = {
    {PA, 0U, Edge::BOTH, &consumerA, 0U},
    {PA, 1U, Edge::BOTH, &consumerA, 1U},
    {PA, 5U, Edge::RISING, &consumerB, 0U},
    {PA, 7U, Edge::BOTH, &consumerB, 1U},
    {PC, 17U, Edge::FALLING, &consumerB, 1U}, // channel 1 of B on two ports
    {PA, 31U, Edge::BOTH, &consumerA, 2U},
};
constexpr VectorUse K14_VECTORS[] = {
    {PORTA_IRQn, 6U, static_cast<uint8_t>(1U << PA)},
    {PORTC_IRQn, 7U, static_cast<uint8_t>(1U << PC)},
};
static_assert(bios::pinirq::check(K14_PINS, K14_VECTORS) == TableError::OK, "K14 table");
constexpr PortMaps K14_MAPS = bios::pinirq::makePortMaps(K14_PINS);
constexpr Config K14_CONFIG{K14_PINS, 6U, K14_VECTORS, 2U, &K14_MAPS};
bios::PinIrqDispatcher k14(K14_CONFIG);

constexpr uint32_t K14_MASK_A = bit(0U) | bit(1U) | bit(5U) | bit(7U) | bit(31U);
constexpr uint32_t K14_MASK_C = bit(17U);

// S32K11x shape: one vector for all ports (the model stands in PORTB_IRQn for PORT_IRQn)
constexpr PinUse K11_PINS[] = {
    {PA, 2U, Edge::BOTH, &consumerA, 0U},
    {PB, 4U, Edge::FALLING, &consumerA, 1U},
    {PE, 31U, Edge::RISING, &consumerB, 0U},
    {PE, 0U, Edge::BOTH, &consumerB, 1U},
};
constexpr VectorUse K11_VECTORS[] = {{PORTB_IRQn, 3U, 0x1FU}};
static_assert(bios::pinirq::check(K11_PINS, K11_VECTORS) == TableError::OK, "K11 table");
constexpr PortMaps K11_MAPS = bios::pinirq::makePortMaps(K11_PINS);
constexpr Config K11_CONFIG{K11_PINS, 4U, K11_VECTORS, 1U, &K11_MAPS};
bios::PinIrqDispatcher k11(K11_CONFIG);

// Tables the check has to refuse (compile time, so a generated table cannot build with these faults)
constexpr PinUse TWICE[]         = {{PA, 3U, Edge::BOTH, &unused, 0U}, {PA, 3U, Edge::RISING, &unused, 1U}};
constexpr VectorUse ONE_A[]      = {{PORTA_IRQn, 1U, 1U}};
constexpr PinUse ON_C[]          = {{PC, 3U, Edge::BOTH, &unused, 0U}};
constexpr VectorUse A_TWICE[]    = {{PORTA_IRQn, 1U, 1U}, {PORTB_IRQn, 1U, 0x1FU}};
constexpr PinUse NOBODY[]        = {{PA, 3U, Edge::BOTH, nullptr, 0U}};
constexpr PinUse PIN_32[]        = {{PA, 32U, Edge::BOTH, &unused, 0U}};
constexpr PinUse PORT_5[]        = {{5U, 0U, Edge::BOTH, &unused, 0U}};
constexpr PinUse LEVEL[]         = {{PA, 3U, static_cast<Edge>(8U), &unused, 0U}}; // IRQC 8: level low
constexpr PinUse ON_A[]          = {{PA, 3U, Edge::BOTH, &unused, 0U}};
constexpr VectorUse A_AND_C[]    = {{PORTA_IRQn, 1U, 1U}, {PORTC_IRQn, 1U, 4U}};
static_assert(bios::pinirq::check(TWICE, ONE_A) == TableError::PIN_TWICE, "one pin, one consumer");
static_assert(bios::pinirq::check(ON_C, ONE_A) == TableError::PORT_WITHOUT_VECTOR, "port without vector");
static_assert(bios::pinirq::check(ON_A, A_TWICE) == TableError::PORT_TWICE, "port served twice");
static_assert(bios::pinirq::check(NOBODY, ONE_A) == TableError::NO_CONSUMER, "pin without consumer");
static_assert(bios::pinirq::check(PIN_32, ONE_A) == TableError::PIN_OUT_OF_RANGE, "pin 32");
static_assert(bios::pinirq::check(PORT_5, ONE_A) == TableError::PIN_OUT_OF_RANGE, "port F");
static_assert(bios::pinirq::check(LEVEL, ONE_A) == TableError::EDGE_INVALID, "level interrupt");
static_assert(bios::pinirq::check(ON_A, A_AND_C) == TableError::VECTOR_WITHOUT_PIN, "vector without pin");

// ----------------------------------------------------------------------------------------- model of the pins

uint32_t levels[NX_TEST_PORTS] = {};

uint32_t irqcOf(uint8_t const port, uint8_t const pin)
{
    return (fakePort[port].PCR[pin] & PORT_PCR_IRQC_MASK) >> PORT_PCR_IRQC_SHIFT;
}

/** One pin level; the hardware sets the flag only while the pin is armed for this edge (IRQC 9/10/11). */
void setLevel(uint8_t const port, uint8_t const pin, bool const level)
{
    bool const before = (levels[port] & bit(pin)) != 0UL;
    if (before == level)
    {
        return;
    }
    levels[port]        = level ? (levels[port] | bit(pin)) : (levels[port] & ~bit(pin));
    uint32_t const irqc = irqcOf(port, pin);
    if ((irqc == 11U) || ((irqc == 9U) && level) || ((irqc == 10U) && !level))
    {
        fakePort[port].ISFR.set(bit(pin));
    }
}

void resetModel()
{
    std::memset(static_cast<void*>(fakePort), 0, sizeof(fakePort));
    std::memset(static_cast<void*>(&fakeNvic), 0, sizeof(fakeNvic));
    std::memset(levels, 0, sizeof(levels));
    consumerA.reset();
    consumerB.reset();
    unused.reset();
}

bool enabled(IRQn_Type const irq) { return (fakeNvic.enabled & (1UL << nxTestIrqIndex(irq))) != 0UL; }

// ---------------------------------------------------------------------------------------------- test cases

void testInit()
{
    resetModel();
    uint32_t const kept          = PORT_PCR_MUX(1U) | 0x3U;              // GPIO with a pull
    fakePort[PA].PCR[0]          = kept | PORT_PCR_ISF_MASK;             // own pin with a pending flag
    uint32_t const foreign       = PORT_PCR_MUX(1U) | PORT_PCR_IRQC(10U); // another user's pin
    fakePort[PA].PCR[2]          = foreign;
    fakePort[PC].PCR[3]          = foreign;
    fakePort[PA].ISFR.set(0xFFFFFFFFU); // stale flags everywhere
    fakePort[PC].ISFR.set(bit(3U) | bit(17U));
    fakeNvic.enabled = 0xFFU;

    k14.init();
    CHECK(!enabled(PORTA_IRQn));
    CHECK(!enabled(PORTC_IRQn));
    CHECK(!fakeNvic.priorityWritten[nxTestIrqIndex(PORTA_IRQn)]); // levels only with enable()
    for (PinUse const& use : K14_PINS)
    {
        CHECK(irqcOf(use.port, use.pin) == static_cast<uint32_t>(use.edge));
    }
    CHECK((fakePort[PA].PCR[0] & ~PORT_PCR_IRQC_MASK) == kept); // mux and pull kept, ISF not written back as 1
    CHECK(fakePort[PA].PCR[2] == foreign);
    CHECK(fakePort[PC].PCR[3] == foreign);
    CHECK(fakePort[PB].PCR[5] == 0U);
    CHECK(static_cast<uint32_t>(fakePort[PA].ISFR) == ~K14_MASK_A); // own stale flags cleared, foreign kept
    CHECK(static_cast<uint32_t>(fakePort[PC].ISFR) == bit(3U));
    CHECK(fakeNvic.disableCalls == 2U);

    fakeNvic.pending = 0xFFU;
    k14.enable();
    CHECK(enabled(PORTA_IRQn) && enabled(PORTC_IRQn));
    // vectors of other users stay as they were (preset 0xFF above), init() and enable() touch only their own
    CHECK(enabled(PORTB_IRQn) && enabled(PORTD_IRQn) && enabled(PORTE_IRQn));
    CHECK(!fakeNvic.priorityWritten[nxTestIrqIndex(PORTB_IRQn)]);
    CHECK(fakeNvic.priority[nxTestIrqIndex(PORTA_IRQn)] == 6U); // level of the rank
    CHECK(fakeNvic.priority[nxTestIrqIndex(PORTC_IRQn)] == 7U);
    CHECK((fakeNvic.pending & (1UL << nxTestIrqIndex(PORTA_IRQn))) == 0UL);
    CHECK((fakeNvic.pending & (1UL << nxTestIrqIndex(PORTC_IRQn))) == 0UL);
}

void startK14()
{
    resetModel();
    k14.init();
    k14.enable();
}

void testInterruptManyPinsOfOnePort()
{
    startK14();
    fakePort[PA].ISFR.set(bit(0U) | bit(1U) | bit(5U) | bit(31U) | bit(2U)); // pin 2: nobody's
    fakePort[PC].ISFR.set(bit(17U));
    unsigned const reads = nxTestTimeReads;
    k14.isr(0U);
    CHECK(nxTestTimeReads == reads + 1U); // one time per interrupt
    CHECK(consumerA.count == 3U);
    CHECK(consumerB.count == 1U);
    // ascending pin order: A0 (A ch0), A1 (A ch1), A31 (A ch2); B only A5 (ch0) - C17 belongs to the other vector
    CHECK((consumerA.calls[0].channel == 0U) && (consumerA.calls[1].channel == 1U) && (consumerA.calls[2].channel == 2U));
    CHECK(consumerB.calls[0].channel == 0U);
    uint32_t const t = consumerA.calls[0].time;
    CHECK((consumerA.calls[1].time == t) && (consumerA.calls[2].time == t) && (consumerB.calls[0].time == t));
    CHECK(static_cast<uint32_t>(fakePort[PA].ISFR) == bit(2U)); // only own flags cleared
    CHECK(static_cast<uint32_t>(fakePort[PC].ISFR) == bit(17U)); // other vector's port untouched

    // an interrupt with a foreign flag only: nothing called, nothing cleared, no time read
    consumerA.reset();
    consumerB.reset();
    k14.isr(0U);
    CHECK((consumerA.count == 0U) && (consumerB.count == 0U));
    CHECK(nxTestTimeReads == reads + 1U);
    CHECK(static_cast<uint32_t>(fakePort[PA].ISFR) == bit(2U));

    // the vector of PORTC serves C17: channel 1 of B
    k14.isr(1U);
    CHECK((consumerB.count == 1U) && (consumerB.calls[0].channel == 1U));
    CHECK(static_cast<uint32_t>(fakePort[PC].ISFR) == 0U);

    // an unknown vector does nothing
    fakePort[PA].ISFR.set(bit(0U));
    k14.isr(2U);
    k14.isr(0xFFU);
    CHECK(consumerA.count == 0U);
    CHECK((static_cast<uint32_t>(fakePort[PA].ISFR) & bit(0U)) != 0UL);
}

void testEdges()
{
    startK14();
    setLevel(PA, 5U, true); // RISING armed
    CHECK((static_cast<uint32_t>(fakePort[PA].ISFR) & bit(5U)) != 0UL);
    k14.isr(0U);
    setLevel(PA, 5U, false); // falling edge of a RISING pin: no flag
    k14.isr(0U);
    CHECK((consumerB.count == 1U) && (consumerB.countOf(0U) == 1U));

    setLevel(PC, 17U, true); // FALLING armed: rising gives nothing
    k14.isr(1U);
    CHECK(consumerB.countOf(1U) == 0U);
    setLevel(PC, 17U, false);
    k14.isr(1U);
    CHECK(consumerB.countOf(1U) == 1U);

    setLevel(PA, 0U, true); // BOTH
    k14.isr(0U);
    setLevel(PA, 0U, false);
    k14.isr(0U);
    CHECK(consumerA.countOf(0U) == 2U);
    // two edges of the same pin before the interrupt runs are one flag: one call
    setLevel(PA, 1U, true);
    setLevel(PA, 1U, false);
    k14.isr(0U);
    CHECK(consumerA.countOf(1U) == 1U);
}

void testSharedVector()
{
    resetModel();
    k11.init();
    k11.enable();
    CHECK(enabled(PORTB_IRQn));
    CHECK(fakeNvic.priority[nxTestIrqIndex(PORTB_IRQn)] == 3U);
    CHECK(!enabled(PORTA_IRQn));
    for (PinUse const& use : K11_PINS)
    {
        CHECK(irqcOf(use.port, use.pin) == static_cast<uint32_t>(use.edge));
    }
    fakePort[PA].ISFR.set(bit(2U));
    fakePort[PB].ISFR.set(bit(4U));
    fakePort[PC].ISFR.set(bit(3U)); // no pin of this table on PORTC
    fakePort[PE].ISFR.set(bit(0U) | bit(31U) | bit(5U));
    unsigned const reads = nxTestTimeReads;
    k11.isr(0U);
    CHECK(nxTestTimeReads == reads + 1U); // one time for all ports of the vector
    CHECK((consumerA.count == 2U) && (consumerA.calls[0].channel == 0U) && (consumerA.calls[1].channel == 1U));
    CHECK((consumerB.count == 2U) && (consumerB.calls[0].channel == 1U) && (consumerB.calls[1].channel == 0U));
    CHECK((consumerA.calls[0].time == consumerB.calls[1].time) && (consumerA.calls[1].time == consumerB.calls[0].time));
    CHECK(static_cast<uint32_t>(fakePort[PA].ISFR) == 0U);
    CHECK(static_cast<uint32_t>(fakePort[PB].ISFR) == 0U);
    CHECK(static_cast<uint32_t>(fakePort[PC].ISFR) == bit(3U));
    CHECK(static_cast<uint32_t>(fakePort[PE].ISFR) == bit(5U));

    // a flag on a later port alone is found as well
    consumerB.reset();
    fakePort[PE].ISFR.set(bit(31U));
    k11.isr(0U);
    CHECK((consumerB.count == 1U) && (consumerB.calls[0].channel == 0U));
}

void testArming()
{
    startK14();
    fakePort[PA].ISFR.set(bit(7U));
    fakePort[PC].ISFR.set(bit(17U));
    CHECK(k14.setArmed(consumerB, 1U, false)); // both pins of channel 1: A7 and C17
    CHECK(irqcOf(PA, 7U) == 0U);
    CHECK(irqcOf(PC, 17U) == 0U);
    CHECK(static_cast<uint32_t>(fakePort[PA].ISFR) == 0U);
    CHECK(static_cast<uint32_t>(fakePort[PC].ISFR) == 0U);
    CHECK(irqcOf(PA, 5U) == 9U); // channel 0 of the same consumer stays armed
    CHECK(irqcOf(PA, 1U) == 11U);
    setLevel(PA, 7U, true);
    setLevel(PC, 17U, true);
    setLevel(PC, 17U, false);
    k14.isr(0U);
    k14.isr(1U);
    CHECK(consumerB.countOf(1U) == 0U);

    CHECK(!k14.setArmed(consumerB, 9U, false)); // no such channel
    CHECK(!k14.setArmed(unused, 0U, false));    // no such consumer
    CHECK(k14.setArmed(consumerA, 1U, false));  // only A1, not channel 1 of B
    CHECK((irqcOf(PA, 1U) == 0U) && (irqcOf(PA, 0U) == 11U));

    // re-arm: an edge of the disarmed time left as a flag is not reported, the edge is armed again
    fakePort[PA].ISFR.set(bit(7U));
    CHECK(k14.setArmed(consumerB, 1U, true));
    CHECK((irqcOf(PA, 7U) == 11U) && (irqcOf(PC, 17U) == 10U));
    CHECK(static_cast<uint32_t>(fakePort[PA].ISFR) == 0U);
    k14.isr(0U);
    CHECK(consumerB.countOf(1U) == 0U);
    setLevel(PA, 7U, false);
    k14.isr(0U);
    CHECK(consumerB.countOf(1U) == 1U);

    // a consumer disarming its channel inside onEdge (rate limit): no further call
    consumerA.reset();
    CHECK(k14.setArmed(consumerA, 1U, true));
    consumerA.disarmOnEdge = &k14;
    setLevel(PA, 0U, true);
    k14.isr(0U);
    CHECK((consumerA.count == 1U) && (irqcOf(PA, 0U) == 0U));
    setLevel(PA, 0U, false);
    k14.isr(0U);
    CHECK(consumerA.count == 1U);
    CHECK((irqcOf(PA, 1U) == 11U) && (irqcOf(PA, 31U) == 11U)); // the other channels stay armed
}

// IO-54: an edge chosen at run time (pin events: both edges while debouncing, else the configured one)
void testSetEdge()
{
    startK14();
    // another edge than the armed one: stale flag cleared, new edge on every pin of the channel (A7 and C17)
    fakePort[PA].ISFR.set(bit(7U));
    fakePort[PC].ISFR.set(bit(17U));
    CHECK(k14.setEdge(consumerB, 1U, Edge::RISING));
    CHECK((irqcOf(PA, 7U) == 9U) && (irqcOf(PC, 17U) == 9U));
    CHECK(static_cast<uint32_t>(fakePort[PA].ISFR) == 0U);
    CHECK(static_cast<uint32_t>(fakePort[PC].ISFR) == 0U);
    CHECK((irqcOf(PA, 5U) == 9U) && (irqcOf(PA, 0U) == 11U)); // other channels unchanged
    setLevel(PC, 17U, true);                                  // rising now counts on C17
    k14.isr(1U);
    CHECK(consumerB.countOf(1U) == 1U);

    // the armed edge again: a pending flag is an edge since the arming and stays to be reported
    setLevel(PA, 7U, true);
    CHECK(k14.setEdge(consumerB, 1U, Edge::RISING));
    CHECK((static_cast<uint32_t>(fakePort[PA].ISFR) & bit(7U)) != 0UL);
    k14.isr(0U);
    CHECK(consumerB.countOf(1U) == 2U);

    // NONE: detector off and flag cleared; no edge until armed again
    setLevel(PA, 7U, false);
    setLevel(PA, 7U, true); // flag set while still armed
    CHECK(k14.setEdge(consumerB, 1U, Edge::NONE));
    CHECK((irqcOf(PA, 7U) == 0U) && (irqcOf(PC, 17U) == 0U));
    CHECK(static_cast<uint32_t>(fakePort[PA].ISFR) == 0U);
    setLevel(PA, 7U, false);
    setLevel(PA, 7U, true);
    k14.isr(0U);
    CHECK(consumerB.countOf(1U) == 2U);
    // from NONE back to an edge: the flag of the disarmed time is not reported
    fakePort[PA].ISFR.set(bit(7U));
    CHECK(k14.setEdge(consumerB, 1U, Edge::BOTH));
    CHECK((irqcOf(PA, 7U) == 11U) && (static_cast<uint32_t>(fakePort[PA].ISFR) == 0U));

    CHECK(!k14.setEdge(consumerB, 9U, Edge::BOTH)); // no such channel
    CHECK(!k14.setEdge(unused, 0U, Edge::BOTH));    // no such consumer
    // setArmed(true) still arms the edge of the table
    CHECK(k14.setArmed(consumerB, 1U, true));
    CHECK((irqcOf(PA, 7U) == 11U) && (irqcOf(PC, 17U) == 10U));
}

void testPortMaps()
{
    CHECK(K14_MAPS.port[PA].mask == K14_MASK_A);
    CHECK(K14_MAPS.port[PC].mask == K14_MASK_C);
    CHECK((K14_MAPS.port[PB].mask == 0U) && (K14_MAPS.port[PE].mask == 0U));
    CHECK((K14_MAPS.port[PA].slot[0] == 0U) && (K14_MAPS.port[PA].slot[31] == 5U) && (K14_MAPS.port[PC].slot[17] == 4U));
    CHECK(K14_MAPS.port[PA].slot[2] == bios::pinirq::NO_SLOT);
    CHECK(K11_MAPS.port[PE].mask == (bit(0U) | bit(31U)));
}
} // namespace

int main()
{
    testPortMaps();
    testInit();
    testInterruptManyPinsOfOnePort();
    testEdges();
    testSharedVector();
    testArming();
    testSetEdge();
    std::printf("pin_irq_dispatcher_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
