// Register model of SCG and RCM for the host test of the s32k1xx clock check (PLAT-72).
//
// Layout, field positions and bit masks are those of the real device headers, included unchanged: S32K148_SCG.h and
// S32K148_RCM.h of the OpenBSW submodule (the header the S32K14x derivatives build with, SPLL and LOL present) or,
// with NX_TEST_S32K116, S32K116_SCG.h and S32K116_RCM.h of platforms/s32k1xx/bsp/bspMcu (no SPLL, no LOL). Only
// the peripheral pointers are bent onto plain structures in host memory, under the names the family header
// mcu/mcu.h gives them (SCG, RCM instead of IP_SCG, IP_RCM).

#pragma once

#include <cstdint>

#if defined(NX_TEST_S32K116)
#include "S32K116_RCM.h"
#include "S32K116_SCG.h"
#else
#include "S32K148_RCM.h"
#include "S32K148_SCG.h"
#endif

extern SCG_Type scgModel;
extern RCM_Type rcmModel;

#define SCG (&scgModel)
#define RCM (&rcmModel)
