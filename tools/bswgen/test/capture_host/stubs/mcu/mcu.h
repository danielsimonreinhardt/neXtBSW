// Host stub of mcu/mcu.h: real FTM register layout and bit macros (NXP S32K148_FTM.h), every FTM is a RAM
// model, so the host test works with any input_capture profile (see input_capture_host_test.cpp).
#pragma once

#include <cstdint>

#define __IO volatile
#include "S32K148_FTM.h"

extern FTM_Type fakeFtm[8];
#define FTM0 (&fakeFtm[0])
#define FTM1 (&fakeFtm[1])
#define FTM2 (&fakeFtm[2])
#define FTM3 (&fakeFtm[3])
#define FTM4 (&fakeFtm[4])
#define FTM5 (&fakeFtm[5])
#define FTM6 (&fakeFtm[6])
#define FTM7 (&fakeFtm[7])
