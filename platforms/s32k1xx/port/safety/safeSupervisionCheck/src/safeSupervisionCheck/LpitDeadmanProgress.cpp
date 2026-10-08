// SYS-95 (DFA-2): progress of the halt deadman's counter (see the header).

#include "safeSupervisionCheck/LpitDeadmanProgress.h"

namespace safety
{
namespace supervision
{

LpitCounterProgress checkLpitDeadmanProgress(LpitDeadmanConfiguration const& expected, uint32_t const maxPolls)
{
    if (expected.channel >= LPIT_TMR_COUNT)
    {
        return LpitCounterProgress::NOT_RUNNING; // the configuration check reports DEADMAN_FIELD_CONFIGURATION
    }
    uint32_t const channel = expected.channel;
    if ((LPIT0->TMR[channel].TCTRL & LPIT_TMR_TCTRL_T_EN_MASK) == 0U)
    {
        return LpitCounterProgress::NOT_RUNNING;
    }
    uint32_t const first = LPIT0->TMR[channel].CVAL;
    uint32_t const polls = (maxPolls == 0U) ? 1U : maxPolls;
    for (uint32_t i = 0U; i < polls; ++i)
    {
        uint32_t const now = LPIT0->TMR[channel].CVAL;
        if (now != first)
        {
            return LpitCounterProgress::MOVING;
        }
    }
    // An expiry during the polls (the deadman's interrupt stops the channel) is no clock fault
    if ((LPIT0->TMR[channel].TCTRL & LPIT_TMR_TCTRL_T_EN_MASK) == 0U)
    {
        return LpitCounterProgress::NOT_RUNNING;
    }
    return LpitCounterProgress::STOPPED;
}

} // namespace supervision
} // namespace safety
