#pragma once

#include "pinIrq/PinIrqDispatcher.h"

#include <pinEdge/IPinEdgeConsumer.h>
#include <pinEvent/PinEvent.h>

#include <cstdint>

namespace bios
{
namespace pinirq
{
/** The pin of one pin event (index = input of pinevent::PinEvents), generated per project (IO-54). */
struct EventPin
{
    uint8_t port;   // index of the port (0 = PORTA), as PinUse::port
    uint8_t pin;    // pin number 0..31
    bool activeLow; // the board inverts the input (dio.inputs[].active: low)
};

/**
 * Physical edge (PORT_PCR.IRQC) of the logical edges the pin-event module asks for: ACTIVATE is the rising edge of
 * an active-high input and the falling edge of an active-low one; BOTH stays BOTH, NONE switches the detector off.
 */
constexpr Edge physicalEdge(::pinevent::Detect const edges, bool const activeLow)
{
    return (edges == ::pinevent::Detect::NONE)       ? Edge::NONE
           : (edges == ::pinevent::Detect::BOTH)     ? Edge::BOTH
           : (edges == ::pinevent::Detect::ACTIVATE) ? (activeLow ? Edge::FALLING : Edge::RISING)
                                                     : (activeLow ? Edge::RISING : Edge::FALLING);
}
} // namespace pinirq

/**
 * Family end of the pin events (IO-54, doc/architecture/pin-interrupts.md section 3.1): connects the family neutral
 * module libs/bsp/bspPinEvent to the pin-interrupt dispatcher of the s32k1xx.
 *
 *  - consumer of the dispatcher (IPinEdgeConsumer): channel = input of the pin events. For every edge it samples the
 *    logical level of the pin (GPIO PDIR, inversion of the board applied) - after the dispatcher cleared the flag, so
 *    a later edge raises a new interrupt - and calls PinEvents::onEdge(input, time, level). false (rate limit
 *    tripped, unknown input) switches the detector of that pin off (setEdge NONE) until the task side re-arms it.
 *  - pin access of the module (IPinEventHardware): level() the same sample, arm() the logical edges of the module
 *    as physical edge through PinIrqDispatcher::setEdge(). An edge that is armed already keeps a flag raised since
 *    the dispatcher's init(): that edge came after the level the module started from and is reported.
 *
 * Nothing about a board: pins, inversion and the order of the inputs come from the generated configuration; the
 * dispatcher table has one pin per input with this adapter as consumer. Static objects of the port configuration
 * (bspConfiguration/src/bsp/pinIrq/PinIrqConfig.cpp); the adapter, the dispatcher and the pin events refer to each
 * other by reference, all of them constant initialized.
 */
class PinEventAdapter final
: public ::bsp::pinedge::IPinEdgeConsumer
, public ::pinevent::IPinEventHardware
{
public:
    constexpr PinEventAdapter(
        pinirq::EventPin const* pins,
        uint8_t count,
        PinIrqDispatcher& dispatcher,
        ::pinevent::PinEvents& events)
    : _pins(pins), _count(count), _dispatcher(dispatcher), _events(events)
    {}

    void onEdge(uint16_t channel, uint32_t timestampUs) override;

    bool level(uint8_t input) const override;

    void arm(uint8_t input, ::pinevent::Detect edges) override;

private:
    pinirq::EventPin const* _pins;
    uint8_t _count;
    PinIrqDispatcher& _dispatcher;
    ::pinevent::PinEvents& _events;
};

} // namespace bios
