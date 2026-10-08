#pragma once

#include "mcu/mcu.h"

#include <pinEdge/IPinEdgeConsumer.h>

#include <cstddef>
#include <cstdint>

namespace bios
{
namespace pinirq
{
/**
 * Static configuration of the pin-interrupt dispatcher of the family s32k1xx (IO-52, doc/architecture/
 * pin-interrupts.md section 3.1): which pin raises which edge, which consumer and channel it belongs to, and which
 * vector serves which ports. The table is built at compile time from the generated project configuration
 * (bsp/pinIrq/pinIrqConfiguration.h of tools/bswgen, IO-54: Hall sensor sets and pin events, vectors from the
 * derivative fact pin_interrupts) - nothing is registered at run time.
 */

/// Ports of the family (index of GPIO_BASE_PTRS/PORT_BASE_PTRS: 0 = PORTA .. 4 = PORTE) and pins per port.
constexpr uint8_t MAX_PORTS     = 5U;
constexpr uint8_t PINS_PER_PORT = 32U;
/// Marker of a pin without consumer in PortMap::slot; also bounds the number of pins of one table.
constexpr uint8_t NO_SLOT = 0xFFU;

/**
 * PORT_PCR.IRQC of the edge ([RM] PORT chapter: 1001b rising, 1010b falling, 1011b either). Only edges: a level
 * interrupt on a stuck input would never end (section 3.1). The project writes the edge relative to the active
 * level of the board; nxconfig turns it into the physical edge (PinEventUse.physical_edge), the generator into
 * this code. NONE (IRQC 0) is only a request of PinIrqDispatcher::setEdge() (detector off); a table entry with it
 * is refused by check().
 */
enum class Edge : uint8_t
{
    NONE    = 0U,
    RISING  = 9U,
    FALLING = 10U,
    BOTH    = 11U,
};

/** One pin with a consumer. */
struct PinUse
{
    uint8_t port;                                // index of the port (0 = PORTA)
    uint8_t pin;                                 // pin number 0..31
    Edge edge;                                   // armed edge
    ::bsp::pinedge::IPinEdgeConsumer* consumer;  // receives onEdge(channel, time)
    uint16_t channel;                            // consumer's own index
};

/** One pin-interrupt vector: its NVIC request, level and the ports it serves (S32K14x one, S32K11x all). */
struct VectorUse
{
    IRQn_Type irq;
    uint8_t level; // NVIC level of the interrupt rank of the project (PLAT-21)
    uint8_t ports; // bit per port index served by this vector
};

/** Derived per port: the pins with a consumer and, per pin, the index of its PinUse (NO_SLOT: none). */
struct PortMap
{
    uint32_t mask;
    uint8_t slot[PINS_PER_PORT];
};

struct PortMaps
{
    PortMap port[MAX_PORTS];
};

/** What the dispatcher works on. All arrays are static constants of the configuration. */
struct Config
{
    PinUse const* pins;
    uint16_t pinCount;
    VectorUse const* vectors;
    uint8_t vectorCount;
    PortMaps const* maps;
};

/** Port maps of a pin table (compile time); a table that fails check() gives no meaningful maps. */
template<size_t N>
constexpr PortMaps makePortMaps(PinUse const (&pins)[N])
{
    PortMaps maps{};
    for (uint8_t p = 0U; p < MAX_PORTS; ++p)
    {
        maps.port[p].mask = 0U;
        for (uint8_t n = 0U; n < PINS_PER_PORT; ++n)
        {
            maps.port[p].slot[n] = NO_SLOT;
        }
    }
    for (size_t i = 0U; (i < N) && (i < NO_SLOT); ++i)
    {
        if ((pins[i].port < MAX_PORTS) && (pins[i].pin < PINS_PER_PORT))
        {
            maps.port[pins[i].port].mask |= (1UL << pins[i].pin);
            maps.port[pins[i].port].slot[pins[i].pin] = static_cast<uint8_t>(i);
        }
    }
    return maps;
}

/** Result of check(); the port configuration turns everything but OK into a static_assert with the reason. */
enum class TableError : uint8_t
{
    OK,
    TOO_MANY_PINS,       // more pins than PortMap::slot can index
    PIN_OUT_OF_RANGE,    // port or pin number beyond the family
    NO_CONSUMER,         // a pin without consumer
    EDGE_INVALID,        // not one of the three edge codes
    PIN_TWICE,           // the same pin twice: one pin has exactly one consumer
    PORT_WITHOUT_VECTOR, // no vector serves the port of a pin: its edges would never be seen
    PORT_TWICE,          // two vectors serve the same port: both would clear its flags
    VECTOR_WITHOUT_PIN,  // a vector that serves no pin with a consumer
};

template<size_t N, size_t V>
constexpr TableError check(PinUse const (&pins)[N], VectorUse const (&vectors)[V])
{
    if (N >= NO_SLOT)
    {
        return TableError::TOO_MANY_PINS;
    }
    uint8_t served = 0U;
    for (size_t v = 0U; v < V; ++v)
    {
        if ((served & vectors[v].ports) != 0U)
        {
            return TableError::PORT_TWICE;
        }
        served = static_cast<uint8_t>(served | vectors[v].ports);
    }
    uint8_t used = 0U;
    for (size_t i = 0U; i < N; ++i)
    {
        PinUse const& p = pins[i];
        if ((p.port >= MAX_PORTS) || (p.pin >= PINS_PER_PORT))
        {
            return TableError::PIN_OUT_OF_RANGE;
        }
        if (p.consumer == nullptr)
        {
            return TableError::NO_CONSUMER;
        }
        if ((p.edge != Edge::RISING) && (p.edge != Edge::FALLING) && (p.edge != Edge::BOTH))
        {
            return TableError::EDGE_INVALID;
        }
        for (size_t k = 0U; k < i; ++k)
        {
            if ((pins[k].port == p.port) && (pins[k].pin == p.pin))
            {
                return TableError::PIN_TWICE;
            }
        }
        if ((served & (1U << p.port)) == 0U)
        {
            return TableError::PORT_WITHOUT_VECTOR;
        }
        used = static_cast<uint8_t>(used | (1U << p.port));
    }
    for (size_t v = 0U; v < V; ++v)
    {
        if ((vectors[v].ports & used) == 0U)
        {
            return TableError::VECTOR_WITHOUT_PIN;
        }
    }
    return TableError::OK;
}

} // namespace pinirq
} // namespace bios
