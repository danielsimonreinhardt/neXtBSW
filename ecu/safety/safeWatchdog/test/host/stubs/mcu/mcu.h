/* SYS-41 host test stub of mcu/mcu.h: a register model of the S32K1 WDOG (plain struct, the test sets any
 * control word and reads back). Field masks of external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/
 * S32K148_WDOG.h (identical on every S32K1 derivative). */

#pragma once

#include <cstdint>

struct WdogType
{
    uint32_t CS;
    uint32_t CNT;
    uint32_t TOVAL;
    uint32_t WIN;
};

extern WdogType wdogRegisters;

#define WDOG (&wdogRegisters)

#define WDOG_CS_STOP_MASK    (0x1U)
#define WDOG_CS_WAIT_MASK    (0x2U)
#define WDOG_CS_DBG_MASK     (0x4U)
#define WDOG_CS_TST_MASK     (0x18U)
#define WDOG_CS_TST_SHIFT    (3U)
#define WDOG_CS_UPDATE_MASK  (0x20U)
#define WDOG_CS_INT_MASK     (0x40U)
#define WDOG_CS_EN_MASK      (0x80U)
#define WDOG_CS_CLK_MASK     (0x300U)
#define WDOG_CS_CLK_SHIFT    (8U)
#define WDOG_CS_RCS_MASK     (0x400U)
#define WDOG_CS_ULK_MASK     (0x800U)
#define WDOG_CS_PRES_MASK    (0x1000U)
#define WDOG_CS_CMD32EN_MASK (0x2000U)
#define WDOG_CS_FLG_MASK     (0x4000U)
#define WDOG_CS_WIN_MASK     (0x8000U)
