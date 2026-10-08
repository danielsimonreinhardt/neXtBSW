// SYS-41: configuration re-check of the LPIT deadman (see the header).

#include "safeSupervisionCheck/LpitDeadmanCheck.h"

namespace safety
{
namespace supervision
{

#ifndef NX_SAFE_SUPERVISION_CHECK_NO_WEAK_DEFAULT
// A project without a deadman (no power stage): nothing to check. The owner of the deadman replaces this with a
// strong definition. Host tests that provide their own definition set NX_SAFE_SUPERVISION_CHECK_NO_WEAK_DEFAULT
// (weak symbols are not portable to every host linker).
__attribute__((weak)) LpitDeadmanConfiguration const* deadmanExpectedConfiguration() { return nullptr; }
#endif

uint32_t checkLpitDeadman(LpitDeadmanConfiguration const& expected)
{
    if (expected.channel >= LPIT_TMR_COUNT)
    {
        // an index outside the register block would read another register (or fault) - report, do not read
        return DEADMAN_FIELD_CONFIGURATION;
    }
    uint32_t const channelMask = static_cast<uint32_t>(1U) << expected.channel;
    uint32_t deviations        = 0U;
    uint32_t const mcr         = LPIT0->MCR;
    if ((mcr & LPIT_MCR_M_CEN_MASK) == 0U)
    {
        deviations |= DEADMAN_FIELD_MODULE_ENABLE;
    }
    if ((mcr & LPIT_MCR_DBG_EN_MASK) == 0U)
    {
        deviations |= DEADMAN_FIELD_DEBUG_ENABLE;
    }
    if ((LPIT0->MIER & channelMask) == 0U)
    {
        deviations |= DEADMAN_FIELD_IRQ_ENABLE;
    }
    if ((LPIT0->TMR[expected.channel].TCTRL & ~LPIT_TMR_TCTRL_T_EN_MASK) != 0U)
    {
        deviations |= DEADMAN_FIELD_TIMER_CONTROL;
    }
    if (LPIT0->TMR[expected.channel].TVAL != expected.timerValue)
    {
        deviations |= DEADMAN_FIELD_TIMER_VALUE;
    }
    if (NVIC_GetEnableIRQ(expected.irq) == 0U)
    {
        deviations |= DEADMAN_FIELD_NVIC_ENABLE;
    }
    if (NVIC_GetPriority(expected.irq) != expected.irqPriority)
    {
        deviations |= DEADMAN_FIELD_NVIC_PRIORITY;
    }
    return deviations;
}

} // namespace supervision
} // namespace safety
