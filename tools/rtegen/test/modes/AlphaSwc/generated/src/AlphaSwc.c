/*
 * File: AlphaSwc.c
 *
 * Synthetic component for the RTE-08 host test (tools/rtegen/test/modes). Trace ids: 20 Run10ms, 21 OnStandbyExit.
 */

#include "AlphaSwc.h"

Rte_ModeType_AlphaModes AlphaSwc_Mode         = 0U;
Rte_ModeType_AlphaModes AlphaSwc_ModeInSwitch = 0U;

/* 10 ms, task slow */
void AlphaSwc_Run10ms(void)
{
  Test_Trace(20U);
  AlphaSwc_Mode = Rte_Mode_Mode_Mode();
}

/* on exit of Standby, task slow */
void AlphaSwc_OnStandbyExit(void)
{
  Test_Trace(21U);
  AlphaSwc_ModeInSwitch = Rte_Mode_Mode_Mode();
}
