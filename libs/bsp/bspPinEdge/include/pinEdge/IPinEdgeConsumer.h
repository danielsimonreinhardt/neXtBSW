#pragma once

#include <cstdint>

namespace bsp
{
namespace pinedge
{

/**
 * Consumer of pin edges (doc/architecture/pin-interrupts.md, section 3.1, IO-52).
 *
 * The pin-interrupt dispatcher of a family owns the vectors of the pin interrupts. For every pin of a consumer
 * whose edge flag it finds set, it clears that flag and calls onEdge() of the consumer the configuration assigned
 * to the pin. Which pin belongs to which consumer and channel is a static table generated from the project; no
 * consumer registers itself at run time.
 *
 * Contract of the call:
 * - interrupt context, inside the ISR frame of the family (safety hooks); no RTOS call, bounded and short;
 * - \p channel is the consumer's own index (e.g. the sensor set of a Hall consumer, the event of a pin-event
 *   consumer), not a pin number; several pins may share one channel;
 * - \p timestampUs is the 32-bit microsecond system time, taken once per interrupt of a vector after the flags
 *   of that vector were cleared: all edges seen in the same interrupt carry the same time, and an edge that
 *   comes during the call raises a new interrupt instead of being lost;
 * - two flags of the same channel in one interrupt give two calls with the same time; the consumer samples the
 *   level itself if it needs it (the dispatcher does not know the meaning of a pin).
 *
 * The destructor is protected and non-virtual: consumers are static objects, never deleted through the
 * interface.
 */
class IPinEdgeConsumer
{
public:
    virtual void onEdge(uint16_t channel, uint32_t timestampUs) = 0;

protected:
    ~IPinEdgeConsumer() = default;
};

/**
 * Arming of the pins of a consumer, offered by the dispatcher of the family (IO-52) - e.g. for a consumer that
 * switches a storming input off and on again (rate limit of the pin-event module, IO-53).
 *
 * setArmed() acts on every pin the configuration assigns to (\p consumer, \p channel):
 * - false: the edge detection of those pins is switched off and their pending flags are cleared, so no further
 *   onEdge() comes for that channel (except for other pins of the channel already flagged in the interrupt that
 *   is running);
 * - true: their pending flags are cleared (an edge from the disarmed time is not reported) and the configured
 *   edge is armed again.
 * Returns false if the configuration has no pin for (\p consumer, \p channel). Callable from a task or from
 * onEdge() itself; the caller keeps the calls for one channel in one context.
 */
class IPinEdgeControl
{
public:
    virtual bool setArmed(IPinEdgeConsumer const& consumer, uint16_t channel, bool armed) = 0;

protected:
    ~IPinEdgeControl() = default;
};

} // namespace pinedge
} // namespace bsp
