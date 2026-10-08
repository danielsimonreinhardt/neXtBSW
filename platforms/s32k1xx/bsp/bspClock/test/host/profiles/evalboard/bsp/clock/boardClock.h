// Test profile "evalboard" of the s32k1xx clock check (PLAT-72): the clock plan values that tools/bswgen writes for
// projects/evalboard_s32k144_demo (40 MHz SOSC, SPLL 160 MHz, core SPLL / 2 = 80 MHz, bus / 2, slow / 3). Copied
// on 2026-09-22; the peripheral clock and PCC lines are left out.

#pragma once

#define NX_CLOCK_SYSTEM_SOURCE_SPLL 1
#define NX_CLOCK_CORE_HZ 80000000U
#define NX_CLOCK_BUS_HZ  40000000U
#define NX_CLOCK_FIRC_HZ 48000000U
#define NX_CLOCK_SIRC_HZ 8000000U
#define NX_CLOCK_SOSC_HZ 40000000U
#define NX_CLOCK_SPLL_HZ 160000000U

#define CPU_XTAL_CLK_HZ 40000000U

#define _SCG_RCCR_SCS     (6 << 24)
#define _SCG_RCCR_DIVCORE (1 << 16)
#define _SCG_RCCR_DIVBUS  (1 << 4)
#define _SCG_RCCR_DIVSLOW (2)

#define _SCG_RCCR_ ((_SCG_RCCR_SCS) | (_SCG_RCCR_DIVCORE) | (_SCG_RCCR_DIVBUS) | (_SCG_RCCR_DIVSLOW))
