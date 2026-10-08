#pragma once

#include "bsp/hall/hallSensorConfiguration.h"

#include <hallDecoder/HallDecoder.h>
#include <pinEdge/IPinEdgeConsumer.h>

#include <cstdint>

namespace bios
{
/**
 * Access of the s32k1xx to the three digital Hall signals of a brushless motor (IO-07): pin interrupt of the
 * PORT, time stamp per edge. The evaluation - sector, direction, debouncing, period, speed, standstill and
 * quality - is the family neutral libs/bsw/hallDecoder; this class only does what is family knowledge
 * (doc/architecture/treiber-granularitaet.md):
 *   - sample the three levels (GPIO PDIR, inversion of the board applied) and hand state and microsecond time
 *     to the decoder when the pin-interrupt dispatcher reports an edge of the sensor (edge(), IO-52),
 *   - in get() sample state and time and read the decoder, both under one interrupt lock.
 *
 * The pin interrupt itself - arming both edges (PORT_PCR.IRQC), clearing the flags of the Hall pins only, the
 * vector with the NVIC level of the rank and the time stamp - belongs to the family dispatcher
 * (platforms/s32k1xx/bsp/bspPinIrq, bios::PinIrqDispatcher) since IO-52; the Hall pins are one consumer of it
 * (hall/HallPinIrq.h: channel = sensor index, consumer = hallEdgeConsumer).
 *
 * Everything about a board or a motor comes from the generated configuration: bsp/hall/hallSensorConfiguration.h
 * (pins, ports, vectors, levels) and bsp/hall/hallDecoderConfiguration.h (decoder configuration per sensor).
 *
 * Time base: getSystemTimeUs32Bit() of the OpenBSW system timer, whose microsecond comes from the generated clock
 * model (ARCH-13). get() takes the time *inside* the lock: taken before it, an edge between the two could stamp a
 * later time than the one read() gets, and the unsigned age would look like a standstill.
 *
 * Reading: get() may be called from any task and needs no cycle (no single task rule as for the polled input
 * capture of IO-03).
 */
class HallSensors
{
public:
    using Reading    = ::hallDecoder::Reading;
    using Statistics = ::hallDecoder::Statistics;

    // the constants of the decoder, so a user of the access needs no second include
    static constexpr uint8_t QUALITY_DEBOUNCE  = ::hallDecoder::QUALITY_DEBOUNCE;
    static constexpr uint8_t QUALITY_OK        = ::hallDecoder::QUALITY_OK;
    static constexpr uint8_t QUALITY_TIMEOUT   = ::hallDecoder::QUALITY_TIMEOUT;
    static constexpr uint8_t QUALITY_ERROR     = ::hallDecoder::QUALITY_ERROR;
    static constexpr int8_t DIRECTION_UNKNOWN  = ::hallDecoder::DIRECTION_UNKNOWN;
    static constexpr int8_t DIRECTION_FORWARD  = ::hallDecoder::DIRECTION_FORWARD;
    static constexpr int8_t DIRECTION_BACKWARD = ::hallDecoder::DIRECTION_BACKWARD;
    static constexpr uint8_t SECTORS           = ::hallDecoder::SECTORS;

    static constexpr Reading noReading() { return ::hallDecoder::noReading(); }

#if NX_HALL_SENSOR_COUNT > 0
    /**
     * Starts every decoder from the position sampled now. Call it after the pin configuration of the board port
     * (bios::Io) and between PinIrqDispatcher::init() and enable() (bsp::startPinInterrupts() of the port does
     * that): an edge after the sampling keeps its flag and is reported once the vectors are enabled.
     */
    static void init();

    /**
     * An edge on a pin of \p sensor (hall::SensorId), called in interrupt context by the dispatcher through
     * hallEdgeConsumer with the time of that interrupt. The whole state is re-read, so a sensor spread over two
     * ports and two edges within the same interrupt are handled without a special case; an unknown sensor is
     * ignored.
     */
    static void edge(uint16_t sensor, uint32_t nowUs);

    /** Current values of a sensor (hall::SensorId); an unknown sensor returns noReading(). */
    static Reading get(uint16_t sensor);

    /** Counters of a sensor; an unknown sensor returns zeros. */
    static Statistics getStatistics(uint16_t sensor);

    /** Board name of a sensor, "" for an unknown one. */
    static char const* getName(uint16_t sensor);

    static uint16_t const SensorCountAll = static_cast<uint16_t>(hall::_hallSensorMax);

private:
    static uint8_t sample(hall::SensorConfig const& cfg);
#else
    // The project has no hall device: nothing is measured, every sensor reports "no reading".
    static void init() {}

    static void edge(uint16_t /* sensor */, uint32_t /* nowUs */) {}

    static Reading get(uint16_t /* sensor */) { return noReading(); }

    static Statistics getStatistics(uint16_t /* sensor */) { return Statistics{0U, 0U, 0U, 0U, 0U, 0U}; }

    static char const* getName(uint16_t /* sensor */) { return ""; }

    static uint16_t const SensorCountAll = 0U;
#endif
};

#if NX_HALL_SENSOR_COUNT > 0
/**
 * The Hall pins as consumer of the pin-interrupt dispatcher (IO-52): channel = sensor index (hall::SensorId).
 * One static object, referenced by the constant pin table of the port (hall/HallPinIrq.h).
 */
class HallEdgeConsumer final : public ::bsp::pinedge::IPinEdgeConsumer
{
public:
    constexpr HallEdgeConsumer() = default;

    void onEdge(uint16_t channel, uint32_t timestampUs) override;
};

extern HallEdgeConsumer hallEdgeConsumer;
#endif

} // namespace bios
