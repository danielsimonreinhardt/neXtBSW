/*
 * File: ConsumerSwc.c
 *
 * Synthetic component for the RTE-09 host test (tools/rtegen/test/implicit), written by hand in the style of the
 * Embedded Coder AUTOSAR export: ImplicitReceive (Rte_IRead_<Runnable>_<Port>_<Element>) next to ExplicitReceive
 * (Rte_Read_<Port>_<Element>) as the counter-check. Test_Hook is provided by the test harness: at hooks 3 and 5 it
 * runs cycles of the higher priority task fast, which writes Count, Samples and Pair.
 */

#include "ConsumerSwc.h"

uint32 ConsumerSwc_FastImp = 0U;
uint32 ConsumerSwc_FastExp = 0U;
uint32 ConsumerSwc_ImpFirst = 0U;
uint32 ConsumerSwc_ImpSecond = 0U;
boolean ConsumerSwc_ImpSameArray = 0U;
boolean ConsumerSwc_ImpSamePair = 0U;
uint32 ConsumerSwc_ImpPairA = 0U;
uint32 ConsumerSwc_ExpFirst = 0U;
uint32 ConsumerSwc_ExpSecond = 0U;

/* 1 ms, task fast: the same element implicitly and explicitly */
void RunFast1ms(void)
{
  (void)Rte_Read_CountExp_Count(&ConsumerSwc_FastExp);
  ConsumerSwc_FastImp = Rte_IRead_RunFast1ms_CountImp_Count();
}

/* 10 ms, task slow: implicit reads before and after a preemption by task fast */
void RunImp10ms(void)
{
  const uint16 *samples;
  const ImpPair *pair;
  uint16 before[3];
  uint32 pairA;
  uint32 pairB;

  ConsumerSwc_ImpFirst = Rte_IRead_RunImp10ms_CountImp_Count();
  samples = Rte_IRead_RunImp10ms_Samples_Samples();
  pair = Rte_IRead_RunImp10ms_Pair_Pair();
  before[0] = samples[0];
  before[1] = samples[1];
  before[2] = samples[2];
  pairA = pair->A;
  pairB = pair->B;
  Rte_IWrite_RunImp10ms_Seen_Seen(ConsumerSwc_ImpFirst);
  Test_Hook(3U);                       /* task fast runs: Count, Samples and Pair change */
  ConsumerSwc_ImpSecond = Rte_IRead_RunImp10ms_CountImp_Count();
  samples = Rte_IRead_RunImp10ms_Samples_Samples();
  pair = Rte_IRead_RunImp10ms_Pair_Pair();
  ConsumerSwc_ImpSameArray = (boolean)((samples[0] == before[0]) && (samples[1] == before[1]) &&
    (samples[2] == before[2]) && (samples[0] == samples[1]) && (samples[1] == samples[2]));
  ConsumerSwc_ImpSamePair = (boolean)((pair->A == pairA) && (pair->B == pairB) && (pair->B == (2U * pair->A)));
  ConsumerSwc_ImpPairA = pair->A;
  Test_Hook(4U);                       /* Seen written, not yet published */
}

/* 10 ms, task slow: the counter-check with explicit reads */
void RunExp10ms(void)
{
  (void)Rte_Read_CountExp_Count(&ConsumerSwc_ExpFirst);
  Test_Hook(5U);                       /* task fast runs */
  (void)Rte_Read_CountExp_Count(&ConsumerSwc_ExpSecond);
}
