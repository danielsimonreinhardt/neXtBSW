/* SYS-40 host test stub of mcu/mcu.h: register models of the S32K1 LPTMR, of the PCC entry of LPTMR0 and of
 * SIM_LPOCLKS for safeSupervisionCheck/LptmrCycleTimer.cpp. Names and masks of
 * external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/S32K148_{LPTMR,PCC,SIM}.h.
 *
 * Every access to an LPTMR register goes through Reg, which logs it (register, read/write, value), so the test can
 * see the order of the writes and that no LPTMR access happens while the clock gate is off. CNR behaves like the
 * hardware ([RM13] 49.5.4): a write latches the running counter (`running`) into the readable register; a read
 * without a write before it returns the old latched value. */

#pragma once

#include <cstdint>
#include <string>

namespace lptmr_model
{
extern std::string log;       // "w<reg>=<hex> r<reg> ..." of the LPTMR accesses
extern uint32_t running;      // the counter inside the timer
} // namespace lptmr_model

struct Reg
{
    char name;
    uint32_t value;

    Reg& operator=(uint32_t const v);
    operator uint32_t() const; // NOLINT: register read like the hardware header
};

struct LptmrType
{
    Reg CSR;
    Reg PSR;
    Reg CMR;
    Reg CNR;
};

struct PccType
{
    uint32_t PCCn[128];
};

struct SimType
{
    uint32_t LPOCLKS;
};

extern LptmrType lptmrRegisters;
extern PccType pccRegisters;
extern SimType simRegisters;

#define IP_LPTMR0 (&lptmrRegisters)
#define IP_PCC    (&pccRegisters)
#define IP_SIM    (&simRegisters)

#define PCC_LPTMR0_INDEX  64
#define PCC_PCCn_CGC_MASK (0x40000000U)

#define LPTMR_CSR_TEN_MASK  (0x1U)
#define LPTMR_CSR_TMS_MASK  (0x2U)
#define LPTMR_CSR_TFC_MASK  (0x4U)
#define LPTMR_CSR_TPP_MASK  (0x8U)
#define LPTMR_CSR_TPS_MASK  (0x30U)
#define LPTMR_CSR_TIE_MASK  (0x40U)
#define LPTMR_CSR_TCF_MASK  (0x80U)
#define LPTMR_CSR_TDRE_MASK (0x100U)

#define LPTMR_PSR_PCS_MASK      (0x3U)
#define LPTMR_PSR_PCS(x)        (((uint32_t)(x)) & LPTMR_PSR_PCS_MASK)
#define LPTMR_PSR_PBYP_MASK     (0x4U)
#define LPTMR_PSR_PRESCALE_MASK (0x78U)

#define SIM_LPOCLKS_LPO1KCLKEN_MASK (0x1U)
