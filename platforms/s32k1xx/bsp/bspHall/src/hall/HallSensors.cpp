#include "hall/HallSensors.h"

#if NX_HALL_SENSOR_COUNT > 0

#include "interrupts/SuspendResumeAllInterruptsScopedLock.h"
#include "mcu/mcu.h"

#include <bsp/timer/SystemTimer.h>

namespace bios
{
namespace
{
// Data registers of the ports, indexed by the port index of the configuration (0 = PORTA .. 4 = PORTE). The table
// comes from the device header of the derivative (mcu/mcu.h), so nothing here names a derivative. The PORT
// registers (interrupt field and flags) belong to the pin-interrupt dispatcher (bspPinIrq, IO-52).
GPIO_Type* const GPIO_PORTS[] = GPIO_BASE_PTRS;

constexpr uint8_t PORT_INDEX_COUNT = static_cast<uint8_t>(sizeof(GPIO_PORTS) / sizeof(GPIO_PORTS[0]));
static_assert(
    hall::maxPortIndex() < PORT_INDEX_COUNT,
    "a hall signal sits on a port the derivative does not have (bsp/hall/hallSensorConfiguration.h)");

// One decoder per sensor set, configured by the generated family neutral part (bsp/hall/hallDecoderConfiguration.h).
#define NX_HALL_DECODER(name) ::hallDecoder::HallDecoder(hall::DECODERS[hall::Hi_##name]),
::hallDecoder::HallDecoder decoders[hall::_hallSensorMax] = {NX_HALL_SENSORS(NX_HALL_DECODER)};
#undef NX_HALL_DECODER
} // namespace

uint8_t HallSensors::sample(hall::SensorConfig const& cfg)
{
    uint8_t state = 0U;
    for (uint8_t i = 0U; i < 3U; ++i)
    {
        hall::PinRef const& ref = cfg.pins[i];
        if ((GPIO_PORTS[ref.port]->PDIR & (1UL << ref.pin)) != 0UL)
        {
            state = static_cast<uint8_t>(state | (4U >> i)); // U -> bit 2, V -> bit 1, W -> bit 0
        }
    }
    // A board that inverts a signal (dio active: low) declares it; without that the sector table would be
    // mirrored and every direction would be wrong.
    return static_cast<uint8_t>(state ^ cfg.invert);
}

HallEdgeConsumer hallEdgeConsumer;

void HallEdgeConsumer::onEdge(uint16_t const channel, uint32_t const timestampUs)
{
    HallSensors::edge(channel, timestampUs);
}

void HallSensors::init()
{
    // The decoders start before any interrupt can step them: the dispatcher has armed the pins and cleared their
    // flags (PinIrqDispatcher::init()) and enables the vectors afterwards (enable()).
    uint32_t const now = getSystemTimeUs32Bit();
    for (uint16_t i = 0U; i < static_cast<uint16_t>(hall::_hallSensorMax); ++i)
    {
        decoders[i].start(sample(hall::SENSORS[i]), now);
    }
}

void HallSensors::edge(uint16_t const sensor, uint32_t const nowUs)
{
    if (sensor >= static_cast<uint16_t>(hall::_hallSensorMax))
    {
        return;
    }
    // The whole state is re-read, so a sensor spread over two ports and two edges within the same interrupt are
    // handled without a special case (a second call with the same state and time does not step the decoder).
    decoders[sensor].edge(sample(hall::SENSORS[sensor]), nowUs);
}

HallSensors::Reading HallSensors::get(uint16_t const sensor)
{
    if (sensor >= static_cast<uint16_t>(hall::_hallSensorMax))
    {
        return noReading();
    }
    // State, time and the decoder under one lock: the interrupt steps the same decoder, and a time taken outside
    // the lock could be older than the last edge (an age that wraps into a false standstill).
    interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    uint32_t const now  = getSystemTimeUs32Bit();
    uint8_t const state = sample(hall::SENSORS[sensor]);
    return decoders[sensor].read(state, now);
}

HallSensors::Statistics HallSensors::getStatistics(uint16_t const sensor)
{
    if (sensor >= static_cast<uint16_t>(hall::_hallSensorMax))
    {
        return Statistics{0U, 0U, 0U, 0U, 0U, 0U};
    }
    interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    return decoders[sensor].statistics();
}

char const* HallSensors::getName(uint16_t const sensor)
{
    if (sensor >= static_cast<uint16_t>(hall::_hallSensorMax))
    {
        return "";
    }
    return hall::NAMES[sensor];
}

} // namespace bios

#endif // NX_HALL_SENSOR_COUNT > 0
