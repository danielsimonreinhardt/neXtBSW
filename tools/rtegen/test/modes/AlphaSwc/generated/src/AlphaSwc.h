/*
 * File: AlphaSwc.h
 *
 * Synthetic component for the RTE-08 host test (tools/rtegen/test/modes).
 */

#ifndef AlphaSwc_h_
#define AlphaSwc_h_

#include "Rte_AlphaSwc.h"

extern Rte_ModeType_AlphaModes AlphaSwc_Mode;         /* Rte_Mode_Mode_Mode in Run10ms */
extern Rte_ModeType_AlphaModes AlphaSwc_ModeInSwitch; /* Rte_Mode_Mode_Mode in OnStandbyExit */

/* test harness */
extern void Test_Trace(uint8 id);

#endif /* AlphaSwc_h_ */
