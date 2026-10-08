// Test profile "firc" of the s32k1xx clock driver and check (PLAT-72, driver values since PLAT-85), not an existing
// board: S32K14x without crystal, core on FIRC 48 MHz / 1, bus / 2, slow / 2. The driver starts neither SOSC nor
// SPLL, so only SIRC/FIRC, the source and the dividers can deviate. The driver part follows what tools/bswgen writes
// without a crystal (test_bswgen.py test_without_crystal: no CPU_XTAL_CLK_HZ, PLL_CONFIG 0, every peripheral on FIRC
// or SIRC); the PCC sources cover the instances of the S32K148 device headers the host test builds with.

#pragma once

#ifdef __cplusplus
extern "C"
{
#endif

#define NX_CLOCK_SYSTEM_SOURCE_FIRC 1
#define NX_CLOCK_CORE_HZ 48000000U
#define NX_CLOCK_BUS_HZ  24000000U
#define NX_CLOCK_FIRC_HZ 48000000U
#define NX_CLOCK_SIRC_HZ 8000000U

#define CLOCKSET_PCC(sel, frac, pcd) (((sel) << 24) | ((frac) << 3) | (pcd))

#define SYSTEMCLICK_SCS_OSC_CLK  1UL
#define SYSTEMCLICK_SCS_SIRC_CLK 2UL
#define SYSTEMCLICK_SCS_FIRC_CLK 3UL
#define SYSTEMCLICK_SCS_SPLL_CLK 6UL

#define _SCG_RCCR_SCS     (3 << 24)
#define _SCG_RCCR_DIVCORE (0 << 16)
#define _SCG_RCCR_DIVBUS  (1 << 4)
#define _SCG_RCCR_DIVSLOW (1)

#define _SPLLDIV1 (1) // SPLL not started
#define _SPLLDIV2 (1) // SPLL not started
#define _FIRCDIV1 (1)
#define _FIRCDIV2 (1)
#define _SIRCDIV1 (1)
#define _SIRCDIV2 (1)
#define _SOSCDIV1 (1) // SOSC not started
#define _SOSCDIV2 (1) // SOSC not started

#define PLL_CONFIG (0UL) // no PLL started (no oscillator or no PLL)

#define CLOCKoUT_SELECT (0)

#define CLOCK_SOURCE_FTM0   CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_FTM1   CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_FTM2   CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_FTM3   CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_FTM4   CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_FTM5   CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_ADC0   CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_ADC1   CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_LPSPI0 CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_LPSPI1 CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_LPSPI2 CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_LPIT   CLOCKSET_PCC(SYSTEMCLICK_SCS_SIRC_CLK, 0, 0)
#define CLOCK_SOURCE_LPTMR0 CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_FLEXIO CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_LPI2C0 CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_LPUART0 CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_LPUART1 CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_LPUART2 CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)
#define CLOCK_SOURCE_ENET   CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)

#define _RCM_SRIE_ ((3UL) | (1UL << 7) | (1UL << 5))

#define _SCG_RCCR_ ((_SCG_RCCR_SCS) | (_SCG_RCCR_DIVCORE) | (_SCG_RCCR_DIVBUS) | (_SCG_RCCR_DIVSLOW))

#ifdef __cplusplus
}
#endif
