/* Host build of the updater's ARMv6-M cycle counter (PLAT-28), driven from Python
 * (bootloader/updater/test/test_upd_cycle_counter_host.py):
 *
 *   bootloader/updater/platforms/s32k1xx/cycle_counter/lpit/upd_cycle_counter.h
 *     -> SCG/PCC/LPIT register model (test/host/model/mcu/mcu.h, vendor register layout)
 *
 * The register memory is plain; the access functions apply the order rules of the reference manual to the state the
 * previous access left behind:
 *   - PCC PCS may only change while CGC = 0 ([RM] 29.4): a PCS write while the gate was on is dropped, the old
 *     clock source stays (only the final value shows it)
 *   - LPIT registers need the clock gate ([RM] 48.1 "transfer error")
 *   - LPIT registers other than MCR need MCR[M_CEN] = 1 ([RM] 48.4.1.2): one access (the MCR write) is allowed
 *     while M_CEN = 0 */

#include "mcu/mcu.h"
#include "upd_cycle_counter.h"

#include <stdint.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

static uint32_t scgMem[(sizeof(SCG_Type) / 4U) + 1U];
static uint32_t pccMem[(sizeof(PCC_Type) / 4U) + 1U];
static uint32_t lpitMem[(sizeof(LPIT_Type) / 4U) + 1U];
static uint32_t pccSnapshot[(sizeof(PCC_Type) / 4U) + 1U];
static uint32_t lpitAccessesWithoutMcen;
static uint32_t lpitAccessesWithoutGate;

#define PCC_WORDS (sizeof(PCC_Type) / 4U)

static PCC_Type* pcc(void) { return (PCC_Type*)(void*)pccMem; }

static LPIT_Type* lpit(void) { return (LPIT_Type*)(void*)lpitMem; }

/* Applies the PCS lock to what the previous access wrote: a PCS change while the gate was on does not happen. */
static void settle_pcc(void)
{
    for (uint32_t i = 0U; i < PCC_WORDS; ++i)
    {
        uint32_t const before = pccSnapshot[i];
        uint32_t const now    = pccMem[i];
        if (((before & PCC_PCCn_CGC_MASK) != 0U) && ((before & PCC_PCCn_PCS_MASK) != (now & PCC_PCCn_PCS_MASK)))
        {
            pccMem[i] = (now & ~PCC_PCCn_PCS_MASK) | (before & PCC_PCCn_PCS_MASK); /* locked: PCS write ignored */
        }
        pccSnapshot[i] = pccMem[i];
    }
}

SCG_Type* upd_model_scg(void)
{
    settle_pcc();
    return (SCG_Type*)(void*)scgMem;
}

PCC_Type* upd_model_pcc(void)
{
    settle_pcc();
    return pcc();
}

LPIT_Type* upd_model_lpit(void)
{
    settle_pcc();
    if ((pccMem[PCC_LPIT_INDEX] & PCC_PCCn_CGC_MASK) == 0U)
    {
        ++lpitAccessesWithoutGate;
    }
    if ((lpit()->MCR & LPIT_MCR_M_CEN_MASK) == 0U)
    {
        ++lpitAccessesWithoutMcen;
    }
    return lpit();
}

EXPORT void upd_test_reset(uint32_t fircdiv, uint32_t pccLpit)
{
    memset(scgMem, 0, sizeof(scgMem));
    memset(pccMem, 0, sizeof(pccMem));
    memset(lpitMem, 0, sizeof(lpitMem));
    ((SCG_Type*)(void*)scgMem)->FIRCDIV = fircdiv;
    pccMem[PCC_LPIT_INDEX]              = pccLpit;
    memcpy(pccSnapshot, pccMem, sizeof(pccMem));
    lpitAccessesWithoutMcen = 0U;
    lpitAccessesWithoutGate = 0U;
}

EXPORT void upd_test_start(void)
{
    upd_cycle_counter_start();
    settle_pcc();
}

EXPORT uint32_t upd_test_count(void) { return upd_cycle_count(); }

/* CVAL is read-only for the code under test; the test plays the counting hardware. */
EXPORT void upd_test_set_cval(uint32_t channel, uint32_t value)
{
    uint32_t* const cval = (uint32_t*)(void*)&lpit()->TMR[channel].CVAL;
    *cval                = value;
}

EXPORT uint32_t upd_test_fircdiv(void) { return ((SCG_Type*)(void*)scgMem)->FIRCDIV; }

EXPORT uint32_t upd_test_scg_word(uint32_t index) { return scgMem[index]; }

EXPORT uint32_t upd_test_scg_words(void) { return (uint32_t)(sizeof(SCG_Type) / 4U); }

EXPORT uint32_t upd_test_pcc(uint32_t index) { return pccMem[index]; }

EXPORT uint32_t upd_test_pcc_words(void) { return (uint32_t)PCC_WORDS; }

EXPORT uint32_t upd_test_pcc_lpit_index(void) { return (uint32_t)PCC_LPIT_INDEX; }

EXPORT uint32_t upd_test_lpit_mcr(void) { return lpit()->MCR; }

EXPORT uint32_t upd_test_lpit_tval(uint32_t channel) { return lpit()->TMR[channel].TVAL; }

EXPORT uint32_t upd_test_lpit_tctrl(uint32_t channel) { return lpit()->TMR[channel].TCTRL; }

EXPORT uint32_t upd_test_lpit_channels(void) { return (uint32_t)LPIT_TMR_COUNT; }

EXPORT uint32_t upd_test_lpit_without_mcen(void) { return lpitAccessesWithoutMcen; }

EXPORT uint32_t upd_test_lpit_without_gate(void) { return lpitAccessesWithoutGate; }
