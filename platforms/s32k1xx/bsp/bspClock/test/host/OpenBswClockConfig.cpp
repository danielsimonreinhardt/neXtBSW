// The unchanged OpenBSW clock driver (external/openbsw/platforms/s32k1xx/bsp/bspClock/src/clockConfig.cpp) under
// other names, linked next to the copy with bounded waits (src/clockConfig.cpp) - PLAT-85.
//
// ClockConfigS32kHostTest.cpp runs both against the same register model and compares the writes of a normal
// start-up: the copy must do exactly what the original does when every clock comes. The original is only run in
// scenarios where every clock comes; otherwise it waits for ever, which is the finding of PLAT-72.

#define configurPll      openbsw_configurPll
#define fircOff          openbsw_fircOff
#define extOscOff        openbsw_extOscOff
#define pllOff           openbsw_pllOff
#define systemClock2Sirc openbsw_systemClock2Sirc
#define systemClock2Firc openbsw_systemClock2Firc
#define systemClock2Pll  openbsw_systemClock2Pll

// Without SPLL the original leaves isSoscValid() unused (finding PLAT-11); platforms/s32k1xx/CMakeLists.txt switches
// the warning off for this translation unit in the target build as well
#pragma GCC diagnostic ignored "-Wunused-function"

#include "src/clockConfig.cpp" // include path: external/openbsw/platforms/s32k1xx/bsp/bspClock
