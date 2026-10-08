// SYS-40: independent time base of the safety task's period check on the S32K1 (see the header).

#include "safeSupervisionCheck/LptmrCycleTimer.h"

#include <mcu/mcu.h>

namespace safety
{
namespace supervision
{
namespace
{
// Time counter mode (TMS 0), free running (TFC 1: reset on overflow only), no interrupt, no DMA request
constexpr uint32_t CSR_CONFIG = LPTMR_CSR_TFC_MASK;
// Fields of CSR that the check compares (TEN separately; TCF is a status flag and TPP/TPS only matter for pulses)
constexpr uint32_t CSR_CHECKED = LPTMR_CSR_TMS_MASK | LPTMR_CSR_TFC_MASK | LPTMR_CSR_TIE_MASK | LPTMR_CSR_TDRE_MASK;
// Prescaler clock 1 = LPO1K_CLK ([RM13] Table 27-9 p. 574), prescaler bypassed: one count per LPO1K cycle
constexpr uint32_t PSR_CONFIG = LPTMR_PSR_PCS(1U) | LPTMR_PSR_PBYP_MASK;
constexpr uint32_t PSR_CHECKED = LPTMR_PSR_PCS_MASK | LPTMR_PSR_PBYP_MASK | LPTMR_PSR_PRESCALE_MASK;
} // namespace

void startCycleTimer()
{
    // [RM13] 49.5.1: CSR with the timer disabled first (resets CNR and TCF), then PSR and CMR, then enable. TEN is
    // set with a second write that leaves CSR[5:1] unchanged ([RM13] 49.4.1.2, TEN).
    IP_LPTMR0->CSR = 0U;
    IP_LPTMR0->PSR = PSR_CONFIG;
    IP_LPTMR0->CMR = 0U; // compare unused (no interrupt); TCF at every wrap is ignored
    IP_LPTMR0->CSR = CSR_CONFIG;
    IP_LPTMR0->CSR = CSR_CONFIG | LPTMR_CSR_TEN_MASK;
}

uint32_t readCycleTimer()
{
    IP_LPTMR0->CNR = 0U; // any value: latches the counter into the readable register
    return IP_LPTMR0->CNR & CYCLE_TIMER_MASK;
}

uint32_t checkCycleTimer()
{
    if ((IP_PCC->PCCn[PCC_LPTMR0_INDEX] & PCC_PCCn_CGC_MASK) == 0U)
    {
        return CYCLE_TIMER_FIELD_CLOCK_GATE; // an LPTMR access would be a bus fault
    }
    uint32_t deviations = 0U;
    uint32_t const csr  = IP_LPTMR0->CSR;
    if ((csr & LPTMR_CSR_TEN_MASK) == 0U)
    {
        deviations |= CYCLE_TIMER_FIELD_ENABLE;
    }
    if ((csr & CSR_CHECKED) != CSR_CONFIG)
    {
        deviations |= CYCLE_TIMER_FIELD_MODE;
    }
    if ((IP_LPTMR0->PSR & PSR_CHECKED) != PSR_CONFIG)
    {
        deviations |= CYCLE_TIMER_FIELD_PRESCALER;
    }
    if ((IP_SIM->LPOCLKS & SIM_LPOCLKS_LPO1KCLKEN_MASK) == 0U)
    {
        deviations |= CYCLE_TIMER_FIELD_LPO1K;
    }
    return deviations;
}

} // namespace supervision
} // namespace safety
