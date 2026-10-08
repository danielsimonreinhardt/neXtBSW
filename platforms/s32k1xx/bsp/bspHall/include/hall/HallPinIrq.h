#pragma once

#include "hall/HallSensors.h"

#if NX_HALL_SENSOR_COUNT > 0

#include <pinIrq/PinIrqTable.h>

#include <cstdint>

namespace bios
{
namespace hall
{
/**
 * The Hall sensor sets as entries of the pin-interrupt dispatcher (IO-52), derived at compile time from the
 * generated bsp/hall/hallSensorConfiguration.h - nothing is configured twice:
 *   - pins: the three signals of every sensor, both edges, consumer hallEdgeConsumer, channel = sensor index;
 *   - vectors: one per entry of hall::PORTS (same order, so NX_HALL_PORT_VECTORS index = vector index) with the
 *     NVIC level of the rank of the project and exactly that port.
 * The port configuration (bspConfiguration/src/bsp/pinIrq/PinIrqConfig.cpp) builds the dispatcher table from
 * these; once tools/bswgen writes the whole table (IO-54, Hall pins and pin events in one list) this header is
 * the reference for the Hall part of it.
 */
constexpr uint16_t PIN_IRQ_PIN_COUNT = static_cast<uint16_t>(3U * static_cast<unsigned>(_hallSensorMax));

struct PinIrqPins
{
    pinirq::PinUse pin[PIN_IRQ_PIN_COUNT];
};

struct PinIrqVectors
{
    pinirq::VectorUse vector[PORT_COUNT];
};

constexpr PinIrqPins pinIrqPins()
{
    PinIrqPins t{};
    for (uint16_t s = 0U; s < static_cast<uint16_t>(_hallSensorMax); ++s)
    {
        for (uint8_t k = 0U; k < 3U; ++k)
        {
            pinirq::PinUse& use = t.pin[(3U * s) + k];
            use.port            = SENSORS[s].pins[k].port;
            use.pin             = SENSORS[s].pins[k].pin;
            use.edge            = static_cast<pinirq::Edge>(PCR_IRQC_EITHER_EDGE); // every Hall edge is a step
            use.consumer        = &hallEdgeConsumer;
            use.channel         = s;
        }
    }
    return t;
}

constexpr PinIrqVectors pinIrqVectors()
{
    PinIrqVectors t{};
    for (uint8_t p = 0U; p < PORT_COUNT; ++p)
    {
        t.vector[p].irq   = PORTS[p].irq;
        t.vector[p].level = PORTS[p].level;
        t.vector[p].ports = static_cast<uint8_t>(1U << PORTS[p].port);
    }
    return t;
}

static_assert(
    static_cast<pinirq::Edge>(PCR_IRQC_EITHER_EDGE) == pinirq::Edge::BOTH,
    "the either-edge code of the hall configuration is the dispatcher's Edge::BOTH");

} // namespace hall
} // namespace bios

#endif // NX_HALL_SENSOR_COUNT > 0
