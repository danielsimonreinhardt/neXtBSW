// Host stub of mcu/mcu.h: real FTM register layout and bit macros (NXP S32K148_FTM.h); every FTM instance is the
// same RAM model, which the register model of FtmPwmGroupHostTest.cpp steps like the counter would.
#pragma once

#include <cstdint>

#define __IO volatile
#include "S32K148_FTM.h"

extern FTM_Type fakeFtm;
#define FTM0 (&fakeFtm)
#define FTM1 (&fakeFtm)
#define FTM2 (&fakeFtm)
#define FTM3 (&fakeFtm)
#define FTM4 (&fakeFtm)
#define FTM5 (&fakeFtm)
#define FTM6 (&fakeFtm)
#define FTM7 (&fakeFtm)
