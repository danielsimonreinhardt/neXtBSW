#pragma once

#include "pinIrq/PinIrqTable.h"

#include <pinEdge/IPinEdgeConsumer.h>

#include <cstdint>

namespace bios
{
/**
 * Pin-interrupt dispatcher of the family s32k1xx (IO-52, doc/architecture/pin-interrupts.md section 3.1).
 *
 * It owns the pin-detect vectors: one per port on the S32K14x (PORTA..PORTE), one for all ports on the S32K11x
 * (PORT). A vector reads PORT_ISFR of each port it serves, clears only the flags of pins that have a consumer in
 * the configuration, takes the microsecond time once and calls IPinEdgeConsumer::onEdge(channel, time) of the
 * consumer of every flagged pin. Flags of other pins stay as they are.
 *
 * Bounded: per interrupt at most the ports of the vector (<= 5) and only the flags that are set (no loop over
 * unused pins). No RTOS call, no heap, no registration at run time - the table (pinIrq/PinIrqTable.h) is a
 * compile-time constant of the port configuration.
 *
 * Start-up in two steps so the consumers can start between them from the levels they sample:
 *   init()   - vectors off, the configured edge armed on every pin (PORT_PCR.IRQC, nothing else of the PCR
 *              changes), stale flags of those pins cleared;
 *   enable() - NVIC level of the rank, pending request cleared, vectors on.
 * An edge between the two steps leaves its flag set and is reported right after enable().
 */
class PinIrqDispatcher final : public ::bsp::pinedge::IPinEdgeControl
{
public:
    explicit constexpr PinIrqDispatcher(pinirq::Config const& config) : _config(config) {}

    void init();

    void enable();

    /** Interrupt of vector \p vector (index in Config::vectors); called from the handler of that vector. */
    void isr(uint8_t vector);

    bool setArmed(::bsp::pinedge::IPinEdgeConsumer const& consumer, uint16_t channel, bool armed) override;

    /**
     * Programs another edge on every pin of (\p consumer, \p channel) than the one of the table (IO-54) - for a
     * consumer that decides the edge at run time, e.g. the pin events (edge detector with both edges while it
     * debounces, pinEvent detectorEdges()). Edge::NONE switches the detection off and clears the pending flag, like
     * setArmed(false). Another edge than the one armed now clears a pending flag first (it was raised by another
     * detection) and then arms \p edge. The edge that is armed already leaves the pin as it is, flag included: that
     * flag is a real edge after the arming and is reported. Same contexts as setArmed(); returns false if the
     * configuration has no pin for (\p consumer, \p channel).
     */
    bool setEdge(::bsp::pinedge::IPinEdgeConsumer const& consumer, uint16_t channel, pinirq::Edge edge);

private:
    pinirq::Config const& _config;
};

} // namespace bios
