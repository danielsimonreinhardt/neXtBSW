/* SYS-80 host test stub: the device header included by os/FreeRtosPlatformConfig.h and isr_sys.cpp. SYS-169: only the
 * number of the watchdog interrupt is used (isr_sys.cpp: exception number of the watchdog's dump). */
#pragma once

enum IRQn_Type
{
    WDOG_EWM_IRQn = 22
};
