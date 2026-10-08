// Host stub of mcu/mcu.h: real FTM register layout and bit macros (NXP S32K148_FTM.h), FTM0 is a RAM model.
#pragma once

#include <cstdint>

#define __IO volatile
#include "S32K148_FTM.h"

extern FTM_Type fakeFtm0;
#define FTM0 (&fakeFtm0)
