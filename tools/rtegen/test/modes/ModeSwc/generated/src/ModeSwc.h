/*
 * File: ModeSwc.h
 *
 * Synthetic component for the RTE-08 host test (tools/rtegen/test/modes).
 */

#ifndef ModeSwc_h_
#define ModeSwc_h_

#include "Rte_ModeSwc.h"

extern Rte_ModeType_EcuModes ModeSwc_FastMode;     /* Rte_Mode_EcuMode_EcuMode in Run1ms (view of task fast) */
extern Rte_ModeType_EcuModes ModeSwc_FastAny;      /* Rte_Mode_AnyMode_EcuMode in Run1ms (mode manager) */
extern uint32 ModeSwc_FastEntryCount;              /* EntryCountIn read by Run1ms */
extern Rte_ModeType_EcuModes ModeSwc_SlowMode;     /* Rte_Mode_SlowMode_EcuMode in Run10ms (view of task slow) */
extern Rte_ModeType_EcuModes ModeSwc_SlowAny;      /* Rte_Mode_AnyMode_EcuMode in Run10ms */
extern Rte_ModeType_EcuModes ModeSwc_ModeInSwitch; /* Rte_Mode of the port of the last mode switch runnable */
extern uint32 ModeSwc_EntryCount;                  /* calls of OnRunEntry */

/* test harness */
extern void Test_Hook(uint8 where);
extern void Test_Trace(uint8 id);

#endif /* ModeSwc_h_ */
