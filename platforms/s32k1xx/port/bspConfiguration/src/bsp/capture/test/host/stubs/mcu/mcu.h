// Host stub of mcu/mcu.h: real FTM register layout and bit macros (NXP S32K148_FTM.h, same stub as
// tools/bswgen/test/pwm_host/stubs/mcu/mcu.h), FTM0 is a RAM model - the charge project's only capture channel
// (PILOT_DUTY) sits on FTM0 (IO-48).
#pragma once

#include <cstdint>

#define __IO volatile
#include "S32K148_FTM.h"

extern FTM_Type fakeFtm0;
#define FTM0 (&fakeFtm0)
