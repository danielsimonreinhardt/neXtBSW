// IO-52: pin-interrupt dispatcher of the family port (doc/architecture/pin-interrupts.md, section 3.1). It owns the
// pin-detect vectors of the ports with a pin that has a consumer and hands each edge to that consumer
// (bios::PinIrqDispatcher, platforms/s32k1xx/bsp/bspPinIrq).
//
// IO-54: the whole table comes from tools/bswgen (bsp/pinIrq/pinIrqConfiguration.h): the pins of the Hall sensor
// sets (consumer bios::hallEdgeConsumer, both edges) and of the pin events of project pin_events (consumer
// bios::PinEventAdapter, the edge the module needs), and the vectors from the derivative fact pin_interrupts
// (S32K14x one per port, S32K11x one for all ports) with the NVIC level of the rank. The pin events themselves
// (libs/bsp/bspPinEvent: table, instance, task side) live here as well, because the adapter, the dispatcher and the
// module refer to each other.
//
// Pattern of the LPUART receive interrupt (IO-17, bsp/uart/UartConfig.cpp): the handler names come from the
// generated vector list, safeLifecycle/IsrWrapper.cpp declares them weak, and the safety hooks of the lifecycle
// wrap the handler exactly as they do for the CAN and UART interrupts (SYS-170: every vector through Safe_<handler>).

#include "bsp/pinIrq/PinIrqConfig.h"

#include "bsp/hall/hallSensorConfiguration.h"
#include "bsp/pinIrq/pinIrqConfiguration.h"

#if NX_PIN_IRQ_PIN_COUNT > 0

#include "bsp/hall/HallVectors.h"

#include <pinIrq/PinIrqDispatcher.h>

#if NX_HALL_SENSOR_COUNT > 0
#include <hall/HallSensors.h>
#endif
#if NX_PIN_EVENT_COUNT > 0
#include <bsp/timer/SystemTimer.h>
#include <pinIrq/PinEventAdapter.h>
#endif

extern "C"
{
void SafePreIsrHook(void);
void SafePostIsrHook(void);
#define NX_PIN_IRQ_ISR_DECL(index, irq, handler, ports, level, startup) void handler(void);
NX_PIN_IRQ_VECTORS(NX_PIN_IRQ_ISR_DECL)
#undef NX_PIN_IRQ_ISR_DECL
}

// The vector table of the port (SYS-170: generated startup/nxIrqVectors.inc on both cores) needs a vector for the
// pin detect interrupt of every port with a consumer pin, otherwise the handler below would never run and the consumer would
// never see an edge (ARCH-09 B2 for the instance itself). bsp/hall/HallVectors.h says which vectors the table has.
// `startup` is 0 for a vector the table takes from the derivative's list (ARMv6-M, startup/nxIrqVectors.inc).
#define NX_PIN_IRQ_VECTOR_ASSERT(index, irq, handler, ports, level, startup)                                    \
    static_assert(                                                                                               \
        (NX_PORT_PIN_VECTORS & (startup)) == (startup),                                                          \
        #handler ": the vector table of this family port (main/src/bsp/startUp.S) has no vector for this pin " \
                 "detect interrupt, so the handler would never run (bsp/hall/HallVectors.h)");
NX_PIN_IRQ_VECTORS(NX_PIN_IRQ_VECTOR_ASSERT)
#undef NX_PIN_IRQ_VECTOR_ASSERT

namespace bsp
{
// Named (not anonymous) so the objects below can be declared before they are defined: the adapter refers to the
// dispatcher and the pin events, the dispatcher's table to the adapter. All of them are constant initialized.
namespace pinirqconfig
{
namespace pinirq = ::bios::pinirq;

extern ::bios::PinIrqDispatcher dispatcher;

#if NX_PIN_EVENT_COUNT > 0
extern ::pinevent::PinEvents events;

#define NX_PIN_EVENT_PIN(port, pin, activeLow) {port, pin, activeLow},
constexpr pinirq::EventPin EVENT_PINS[] = {NX_PIN_EVENT_PINS(NX_PIN_EVENT_PIN)};
#undef NX_PIN_EVENT_PIN
static_assert(
    (sizeof(EVENT_PINS) / sizeof(EVENT_PINS[0])) == NX_PIN_EVENT_COUNT,
    "one pin per pin event (bsp/pinIrq/pinIrqConfiguration.h and bsp/pinEvent/pinEventConfiguration.h)");

::bios::PinEventAdapter pinEventAdapter(EVENT_PINS, NX_PIN_EVENT_COUNT, dispatcher, events);
#define NX_PIN_IRQ_CONSUMER_PIN_EVENT (&pinEventAdapter)
#endif
#if NX_HALL_SENSOR_COUNT > 0
#define NX_PIN_IRQ_CONSUMER_HALL (&::bios::hallEdgeConsumer)
#endif

#define NX_PIN_IRQ_PIN(port, pin, edge, consumer, channel) \
    {port, pin, pinirq::Edge::edge, NX_PIN_IRQ_CONSUMER_##consumer, channel},
constexpr pinirq::PinUse PINS[] = {NX_PIN_IRQ_PINS(NX_PIN_IRQ_PIN)};
#undef NX_PIN_IRQ_PIN
#define NX_PIN_IRQ_VECTOR(index, irq, handler, ports, level, startup) {irq, level, ports},
constexpr pinirq::VectorUse VECTORS[] = {NX_PIN_IRQ_VECTORS(NX_PIN_IRQ_VECTOR)};
#undef NX_PIN_IRQ_VECTOR

constexpr uint16_t PIN_COUNT   = static_cast<uint16_t>(sizeof(PINS) / sizeof(PINS[0]));
constexpr uint8_t VECTOR_COUNT = static_cast<uint8_t>(sizeof(VECTORS) / sizeof(VECTORS[0]));
static_assert(PIN_COUNT == NX_PIN_IRQ_PIN_COUNT, "NX_PIN_IRQ_PIN_COUNT does not match the list of pins");
static_assert(VECTOR_COUNT == NX_PIN_IRQ_VECTOR_COUNT, "NX_PIN_IRQ_VECTOR_COUNT does not match the list of vectors");

constexpr pinirq::TableError TABLE_ERROR = pinirq::check(PINS, VECTORS);
static_assert(TABLE_ERROR != pinirq::TableError::PIN_TWICE, "a pin has two consumers in the pin interrupt table");
static_assert(
    TABLE_ERROR != pinirq::TableError::PORT_WITHOUT_VECTOR,
    "a pin with a consumer sits on a port no pin interrupt vector serves: its edges would never be seen");
static_assert(TABLE_ERROR != pinirq::TableError::PORT_TWICE, "two pin interrupt vectors serve the same port");
static_assert(
    TABLE_ERROR == pinirq::TableError::OK,
    "the pin interrupt table is invalid (pinIrq/PinIrqTable.h, check(): pin range, consumer, edge, pin count, "
    "vector without pin)");

constexpr pinirq::PortMaps MAPS = pinirq::makePortMaps(PINS);
constexpr pinirq::Config CONFIG{PINS, PIN_COUNT, VECTORS, VECTOR_COUNT, &MAPS};

// Constant initialization: the table is in flash, the object holds only a reference to it.
::bios::PinIrqDispatcher dispatcher(CONFIG);

#if NX_PIN_EVENT_COUNT > 0
// the time base of the dispatcher's time stamps, for the task side of the module
uint32_t timeUs() { return getSystemTimeUs32Bit(); }

#define NX_PIN_EVENT_INPUT(name) ::pinevent::PinEvent(::pinevent::project::CONFIG[::pinevent::project::Pe_##name]),
::pinevent::PinEvent inputs[NX_PIN_EVENT_COUNT] = {NX_PIN_EVENTS(NX_PIN_EVENT_INPUT)};
#undef NX_PIN_EVENT_INPUT

::pinevent::PinEvents events(inputs, NX_PIN_EVENT_COUNT, pinEventAdapter, &timeUs);
#endif
} // namespace pinirqconfig
} // namespace bsp

// Vectors in nxIrqVectors.inc (weak, default DefaultISR in safeLifecycle/IsrWrapper.cpp); safety ISR hooks as for
// the CAN and UART interrupts. Note (SYS-170): the vector already runs through the SAFE_ISR wrapper Safe_<handler>,
// so these hooks are a second, nested pair (harmless); kept on purpose, removing them is a change of its own.
#define NX_PIN_IRQ_ISR(index, irq, handler, ports, level, startup) \
    void handler(void)                                             \
    {                                                              \
        SafePreIsrHook();                                          \
        ::bsp::pinirqconfig::dispatcher.isr(index);                \
        SafePostIsrHook();                                         \
    }
NX_PIN_IRQ_VECTORS(NX_PIN_IRQ_ISR)
#undef NX_PIN_IRQ_ISR

void bsp::armPinInterrupts() { pinirqconfig::dispatcher.init(); }

void bsp::enablePinInterrupts() { pinirqconfig::dispatcher.enable(); }

// The order keeps every edge: the dispatcher arms the pins and clears their stale flags with the vectors off, the
// consumers start from the levels sampled then, and only then the vectors are enabled - an edge in between is
// reported right after.
void bsp::startPinInterrupts()
{
    armPinInterrupts();
#if NX_HALL_SENSOR_COUNT > 0
    ::bios::HallSensors::init();
#endif
#if NX_PIN_EVENT_COUNT > 0
    pinirqconfig::events.start();
#endif
    enablePinInterrupts();
}

void bsp::cyclePinEvents()
{
#if NX_PIN_EVENT_COUNT > 0
    pinirqconfig::events.cycle();
#endif
}

::bsp::pinedge::IPinEdgeControl& bsp::pinEdgeControl() { return pinirqconfig::dispatcher; }

#if NX_PIN_EVENT_COUNT > 0
::pinevent::PinEvents& bsp::pinEvents() { return pinirqconfig::events; }
#endif

#else // no pin with a consumer: no vector, no table

#if NX_PIN_EVENT_COUNT > 0
#error "pin events without pins in the pin interrupt table (bsp/pinIrq/pinIrqConfiguration.h)"
#endif

namespace
{
class NoPinEdgeControl final : public ::bsp::pinedge::IPinEdgeControl
{
public:
    bool setArmed(::bsp::pinedge::IPinEdgeConsumer const& /* consumer */, uint16_t /* channel */, bool /* armed */)
        override
    {
        return false;
    }
};

NoPinEdgeControl noPinEdgeControl;
} // namespace

void bsp::armPinInterrupts() {}

void bsp::enablePinInterrupts() {}

void bsp::startPinInterrupts() {}

void bsp::cyclePinEvents() {}

::bsp::pinedge::IPinEdgeControl& bsp::pinEdgeControl() { return noPinEdgeControl; }

#endif // NX_PIN_IRQ_PIN_COUNT > 0
