#pragma once

/* Register model of the family s32k1xx for the host test of the updater's LPIT cycle counter (PLAT-28).
 *
 * Register layout and bit masks come from the vendor headers of the submodule (BSD-3, S32K148 rev. 1.1a), so the test
 * cannot invent offsets. The instance pointers are redirected to access functions of the test
 * (upd_cycle_counter_host.c): every access first checks the rules of the reference manual against the state the
 * previous access left behind - PCS changed while the clock gate was on, LPIT used without clock gate, LPIT channel
 * registers used before MCR[M_CEN]. */

#include "S32K148_COMMON.h"
#include "S32K148_LPIT.h"
#include "S32K148_PCC.h"
#include "S32K148_SCG.h"

SCG_Type* upd_model_scg(void);
PCC_Type* upd_model_pcc(void);
LPIT_Type* upd_model_lpit(void);

#undef IP_SCG
#undef IP_PCC
#undef IP_LPIT0
#define IP_SCG   (upd_model_scg())
#define IP_PCC   (upd_model_pcc())
#define IP_LPIT0 (upd_model_lpit())
