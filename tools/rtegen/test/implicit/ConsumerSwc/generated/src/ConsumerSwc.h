/*
 * File: ConsumerSwc.h
 *
 * Synthetic component for the RTE-09 host test (tools/rtegen/test/implicit).
 */

#ifndef ConsumerSwc_h_
#define ConsumerSwc_h_

#include "Rte_ConsumerSwc.h"

/* what the runnables saw, for the harness */
extern uint32 ConsumerSwc_FastImp;       /* RunFast1ms: Rte_IRead CountImp */
extern uint32 ConsumerSwc_FastExp;       /* RunFast1ms: Rte_Read CountExp */
extern uint32 ConsumerSwc_ImpFirst;      /* RunImp10ms: Rte_IRead CountImp before the preemption */
extern uint32 ConsumerSwc_ImpSecond;     /* RunImp10ms: Rte_IRead CountImp after the preemption */
extern boolean ConsumerSwc_ImpSameArray; /* RunImp10ms: Samples identical before and after, elements equal */
extern boolean ConsumerSwc_ImpSamePair;  /* RunImp10ms: Pair identical before and after, B == 2 * A */
extern uint32 ConsumerSwc_ImpPairA;      /* RunImp10ms: Pair.A */
extern uint32 ConsumerSwc_ExpFirst;      /* RunExp10ms: Rte_Read CountExp before the preemption */
extern uint32 ConsumerSwc_ExpSecond;     /* RunExp10ms: Rte_Read CountExp after the preemption */

/* test harness */
extern void Test_Hook(uint8 where);

#endif /* ConsumerSwc_h_ */
