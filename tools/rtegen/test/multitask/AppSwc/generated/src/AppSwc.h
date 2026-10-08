/*
 * File: AppSwc.h
 *
 * Synthetic component for the RTE-02 host test (tools/rtegen/test/multitask).
 */

#ifndef AppSwc_h_
#define AppSwc_h_

#include "Rte_AppSwc.h"

extern uint32 AppSwc_Speed1;    /* first read of Speed in Run10ms */
extern uint32 AppSwc_Speed2;    /* second read of Speed in the same call */
extern float64 AppSwc_Wide;     /* Wide read in the same call */
extern uint32 AppSwc_SlowCount; /* calls of Run50ms */

/* test harness */
extern void Test_Hook(uint8 where);
extern void Test_Trace(uint8 id);

#endif /* AppSwc_h_ */
