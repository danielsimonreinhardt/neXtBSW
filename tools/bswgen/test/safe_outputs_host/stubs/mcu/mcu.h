// Host stub of mcu/mcu.h for the safe outputs table (IO-32): the real register layouts and base addresses of the
// NXP headers (S32K148_FTM.h, S32K148_GPIO.h, S32K148_PCC.h; same for every S32K1 derivative), nothing else.
#pragma once

#include <cstdint>

#define __IO volatile
#define __I  volatile const
#define __O  volatile
#include "S32K148_FTM.h"
#include "S32K148_GPIO.h"
#include "S32K148_PCC.h"
