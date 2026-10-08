#pragma once

#include <cstdint>

namespace bsp
{
namespace spi
{

struct LpspiDivider
{
    uint8_t prescale; // TCR[PRESCALE]: functional clock divided by 2^prescale (0..7)
    uint8_t sckdiv;   // CCR[SCKDIV]: prescaled clock divided by (sckdiv + 2)
    uint32_t baudrateHz;
};

/**
 * Selects the fastest SCK frequency that does not exceed \p requestedHz.
 * If even the slowest setting is too fast, the slowest setting is returned.
 */
inline LpspiDivider calculateLpspiDivider(uint32_t const functionalClockHz, uint32_t const requestedHz)
{
    LpspiDivider best{7U, 255U, (functionalClockHz >> 7U) / 257UL};
    if (requestedHz == 0U)
    {
        return best;
    }
    bool found = false;
    for (uint8_t prescale = 0U; prescale <= 7U; ++prescale)
    {
        uint32_t const prescaledHz = functionalClockHz >> prescale;
        // Rounding the divisor up guarantees prescaledHz / divisor <= requestedHz.
        uint32_t divisor = (prescaledHz / requestedHz) + (((prescaledHz % requestedHz) != 0U) ? 1UL : 0UL);
        if (divisor < 2UL)
        {
            divisor = 2UL;
        }
        if (divisor > 257UL)
        {
            continue;
        }
        uint32_t const actualHz = prescaledHz / divisor;
        if ((!found) || (actualHz > best.baudrateHz))
        {
            best  = LpspiDivider{prescale, static_cast<uint8_t>(divisor - 2UL), actualHz};
            found = true;
        }
    }
    return best;
}

} // namespace spi
} // namespace bsp
