#include "pinIrq/PinIrqDispatcher.h"

#include <bsp/timer/SystemTimer.h>

namespace bios
{
namespace
{
// Register blocks of the ports, indexed by the port index of the configuration (0 = PORTA .. 4 = PORTE). The table
// comes from the device header of the derivative (mcu/mcu.h), so nothing here names a derivative.
PORT_Type* const PORT_REGS[] = PORT_BASE_PTRS;

constexpr uint8_t PORT_REG_COUNT = static_cast<uint8_t>(sizeof(PORT_REGS) / sizeof(PORT_REGS[0]));
static_assert(PORT_REG_COUNT <= pinirq::MAX_PORTS, "the derivative has more ports than the dispatcher table");

// Writing PCR back: IRQC is replaced, ISF (write 1 to clear) is never written back as read - clearing a flag is
// the explicit ISFR write of the caller, so no edge is dropped as a side effect of changing the edge.
void writeIrqc(PORT_Type& regs, uint8_t const pin, uint32_t const irqc)
{
    uint32_t const keep = regs.PCR[pin] & ~static_cast<uint32_t>(PORT_PCR_IRQC_MASK | PORT_PCR_ISF_MASK);
    regs.PCR[pin]       = keep | PORT_PCR_IRQC(irqc);
}

bool portValid(uint8_t const port) { return port < PORT_REG_COUNT; }
} // namespace

void PinIrqDispatcher::init()
{
    for (uint8_t v = 0U; v < _config.vectorCount; ++v)
    {
        NVIC_DisableIRQ(_config.vectors[v].irq); // no interrupt while the pin control registers change
    }
    for (uint16_t i = 0U; i < _config.pinCount; ++i)
    {
        pinirq::PinUse const& use = _config.pins[i];
        if (portValid(use.port))
        {
            // Only the interrupt field changes; multiplexing, pull and filter stay as the board port configured
            // the pin (bios::Io from the generated bsp/io/io/ioConfiguration.h).
            writeIrqc(*PORT_REGS[use.port], use.pin, static_cast<uint32_t>(use.edge));
        }
    }
    for (uint8_t p = 0U; p < PORT_REG_COUNT; ++p)
    {
        uint32_t const mask = _config.maps->port[p].mask;
        if (mask != 0U)
        {
            PORT_REGS[p]->ISFR = mask; // write 1 to clear: only the flags of the configured pins
        }
    }
}

void PinIrqDispatcher::enable()
{
    for (uint8_t v = 0U; v < _config.vectorCount; ++v)
    {
        pinirq::VectorUse const& vec = _config.vectors[v];
        NVIC_SetPriority(vec.irq, vec.level);
        NVIC_ClearPendingIRQ(vec.irq);
        NVIC_EnableIRQ(vec.irq);
    }
}

void PinIrqDispatcher::isr(uint8_t const vector)
{
    if (vector >= _config.vectorCount)
    {
        return;
    }
    uint8_t const ports = _config.vectors[vector].ports;
    bool stamped        = false;
    uint32_t now        = 0U;
    for (uint8_t p = 0U; p < PORT_REG_COUNT; ++p) // bounded by the ports of the derivative
    {
        if ((ports & static_cast<uint8_t>(1U << p)) == 0U)
        {
            continue;
        }
        pinirq::PortMap const& map = _config.maps->port[p];
        PORT_Type& regs            = *PORT_REGS[p];
        uint32_t flags             = regs.ISFR & map.mask;
        if (flags == 0U)
        {
            continue; // an interrupt of another pin of this port: not ours, nothing to clear
        }
        regs.ISFR = flags; // write 1 to clear, before the consumers run: an edge during the call is not lost
        if (!stamped)
        {
            now     = getSystemTimeUs32Bit();
            stamped = true;
        }
        while (flags != 0U) // only the set flags: at most one pass per configured pin that has an edge
        {
            uint8_t const pin = static_cast<uint8_t>(__builtin_ctz(flags));
            flags &= flags - 1U;
            uint8_t const slot = map.slot[pin];
            if (slot < _config.pinCount)
            {
                pinirq::PinUse const& use = _config.pins[slot];
                use.consumer->onEdge(use.channel, now);
            }
        }
    }
}

bool PinIrqDispatcher::setArmed(
    ::bsp::pinedge::IPinEdgeConsumer const& consumer, uint16_t const channel, bool const armed)
{
    bool found = false;
    for (uint16_t i = 0U; i < _config.pinCount; ++i) // bounded by the configured pins
    {
        pinirq::PinUse const& use = _config.pins[i];
        if ((use.consumer != &consumer) || (use.channel != channel) || !portValid(use.port))
        {
            continue;
        }
        found           = true;
        PORT_Type& regs = *PORT_REGS[use.port];
        if (armed)
        {
            // stale flag first: an edge of the disarmed time is not reported, the first new one is
            regs.ISFR = (1UL << use.pin);
            writeIrqc(regs, use.pin, static_cast<uint32_t>(use.edge));
        }
        else
        {
            writeIrqc(regs, use.pin, 0U);
            regs.ISFR = (1UL << use.pin);
        }
    }
    return found;
}

bool PinIrqDispatcher::setEdge(
    ::bsp::pinedge::IPinEdgeConsumer const& consumer, uint16_t const channel, pinirq::Edge const edge)
{
    uint32_t const irqc = static_cast<uint32_t>(edge);
    bool found          = false;
    for (uint16_t i = 0U; i < _config.pinCount; ++i) // bounded by the configured pins
    {
        pinirq::PinUse const& use = _config.pins[i];
        if ((use.consumer != &consumer) || (use.channel != channel) || !portValid(use.port))
        {
            continue;
        }
        found           = true;
        PORT_Type& regs = *PORT_REGS[use.port];
        uint32_t const now = (regs.PCR[use.pin] & PORT_PCR_IRQC_MASK) >> PORT_PCR_IRQC_SHIFT;
        if (edge == pinirq::Edge::NONE)
        {
            writeIrqc(regs, use.pin, 0U);
            regs.ISFR = (1UL << use.pin);
        }
        else if (now != irqc)
        {
            // a flag of the detection before (or of the disarmed time) is not an edge of the new one
            regs.ISFR = (1UL << use.pin);
            writeIrqc(regs, use.pin, irqc);
        }
        else
        {
            // armed with this edge already: a pending flag is an edge since then and stays to be reported
        }
    }
    return found;
}

} // namespace bios
