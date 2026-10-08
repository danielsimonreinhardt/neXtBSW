/*
 * File: ProducerSwc.c
 *
 * Synthetic component for the RTE-09 host test (tools/rtegen/test/implicit), written by hand in the style of the
 * Embedded Coder AUTOSAR export with ImplicitSend / ImplicitSendByRef: Rte_IWrite_<Runnable>_<Port>_<Element> and
 * Rte_IWriteRef_<Runnable>_<Port>_<Element>, entry points without arguments.
 * Test_Hook is provided by the test harness (simulated preemption, probes of the published values).
 */

#include "ProducerSwc.h"

uint32 ProducerSwc_Cycles = 0U; /* calls of Run1ms */

/* Init runnable: implicit write of the start value */
void ProducerSwc_Init(void)
{
  Rte_IWrite_ProducerSwc_Init_Count_Count(100U);
}

/* 1 ms: Count by value, Samples (array) and Pair (record) through the reference of the copy; every set of values
 * belongs to one cycle: Samples[0..2] equal, Pair.B == 2 * Pair.A, Count == 1000 + Pair.A */
void Run1ms(void)
{
  uint16 *samples;
  ImpPair *pair;
  uint32 cycles;

  ProducerSwc_Cycles++;
  cycles = ProducerSwc_Cycles;
  Rte_IWrite_Run1ms_Count_Count(1000U + cycles);
  samples = Rte_IWriteRef_Run1ms_Samples_Samples();
  samples[0] = (uint16)cycles;
  Test_Hook(1U);                       /* written in part: nobody may see it yet */
  samples[1] = (uint16)cycles;
  samples[2] = (uint16)cycles;
  pair = Rte_IWriteRef_Run1ms_Pair_Pair();
  pair->A = cycles;
  Test_Hook(2U);                       /* record written in part */
  pair->B = 2U * cycles;
}
