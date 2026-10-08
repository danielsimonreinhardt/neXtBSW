#include "pinIrq/PinEventAdapter.h"

#include "mcu/mcu.h"

namespace bios
{
namespace
{
// Data input registers of the ports, indexed like the dispatcher table (0 = PORTA); from the device header.
GPIO_Type* const GPIO_REGS[] = GPIO_BASE_PTRS;

constexpr uint8_t GPIO_REG_COUNT = static_cast<uint8_t>(sizeof(GPIO_REGS) / sizeof(GPIO_REGS[0]));
} // namespace

bool PinEventAdapter::level(uint8_t const input) const
{
    if ((input >= _count) || (_pins[input].port >= GPIO_REG_COUNT))
    {
        return false;
    }
    pinirq::EventPin const& p = _pins[input];
    bool const high           = (GPIO_REGS[p.port]->PDIR & (1UL << p.pin)) != 0UL;
    return high != p.activeLow;
}

void PinEventAdapter::arm(uint8_t const input, ::pinevent::Detect const edges)
{
    if (input < _count)
    {
        (void)_dispatcher.setEdge(*this, input, pinirq::physicalEdge(edges, _pins[input].activeLow));
    }
}

void PinEventAdapter::onEdge(uint16_t const channel, uint32_t const timestampUs)
{
    // the dispatcher cleared the flag before this call: the level sampled now is the level after the edge, and a
    // change after the sample raises a new interrupt
    uint8_t const input = static_cast<uint8_t>(channel);
    bool const valid    = channel < _count;
    if (!valid || !_events.onEdge(input, timestampUs, level(input)))
    {
        // rate limit tripped (or an input the module does not know): detector off until cycle() re-arms it
        (void)_dispatcher.setEdge(*this, channel, pinirq::Edge::NONE);
    }
}

} // namespace bios
