// BL-34: low-voltage detect reset of the bootloader (bl_lvd_s32k1xx.h). The only C++ unit of the bootloader: it
// exists to use the family port's header unchanged, the code is one byte store (no exceptions, no RTTI, no global
// data, nothing from the C++ runtime - the bootloader links as C).

#include "bl_lvd_s32k1xx.h"

#include "mcu/mcu.h" // device header first: LowVoltageDetectReset.h then checks its constants against it

#include "pmc/LowVoltageDetectReset.h"

extern "C" void bl_lvd_reset_enable(void) { ::platform::pmc::enableLowVoltageDetectReset(IP_PMC->LVDSC1); }
