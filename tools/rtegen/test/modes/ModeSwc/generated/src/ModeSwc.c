/*
 * File: ModeSwc.c
 *
 * Synthetic component for the RTE-08 host test (tools/rtegen/test/modes), written by hand in the style of the
 * Embedded Coder AUTOSAR export: Rte_Mode_<Port>_<Group>() for mode access, entry points without arguments.
 * Trace ids: 1 Run1ms, 2 Run10ms, 10 OnStartupEntry, 11 OnRunEntry, 12 OnRunExit, 13 OnStandbyToRun.
 */

#include "ModeSwc.h"

Rte_ModeType_EcuModes ModeSwc_FastMode     = 0U;
Rte_ModeType_EcuModes ModeSwc_FastAny      = 0U;
uint32 ModeSwc_FastEntryCount              = 0U;
Rte_ModeType_EcuModes ModeSwc_SlowMode     = 0U;
Rte_ModeType_EcuModes ModeSwc_SlowAny      = 0U;
Rte_ModeType_EcuModes ModeSwc_ModeInSwitch = 0U;
uint32 ModeSwc_EntryCount                  = 0U;

/* 1 ms, task fast */
void ModeSwc_Run1ms(void)
{
  Test_Trace(1U);
  ModeSwc_FastMode = Rte_Mode_EcuMode_EcuMode();
  ModeSwc_FastAny = Rte_Mode_AnyMode_EcuMode();
  (void)Rte_Read_EntryCountIn_EntryCountIn(&ModeSwc_FastEntryCount);
}

/* 10 ms, task slow */
void ModeSwc_Run10ms(void)
{
  Test_Trace(2U);
  ModeSwc_SlowMode = Rte_Mode_SlowMode_EcuMode();
  ModeSwc_SlowAny = Rte_Mode_AnyMode_EcuMode();
}

/* on entry of STARTUP (initial mode), task slow */
void ModeSwc_OnStartupEntry(void)
{
  Test_Trace(10U);
  ModeSwc_ModeInSwitch = Rte_Mode_SlowMode_EcuMode();
}

/* on entry of RUN, task slow: writes EntryCount, then a preemption point for the fast task */
void ModeSwc_OnRunEntry(void)
{
  Test_Trace(11U);
  ModeSwc_ModeInSwitch = Rte_Mode_SlowMode_EcuMode();
  ModeSwc_EntryCount++;
  (void)Rte_Write_EntryCount_EntryCount(ModeSwc_EntryCount);
  Test_Hook(1U);
}

/* on exit of RUN, task slow */
void ModeSwc_OnRunExit(void)
{
  Test_Trace(12U);
  ModeSwc_ModeInSwitch = Rte_Mode_SlowMode_EcuMode();
}

/* on transition STANDBY -> RUN, task fast */
void ModeSwc_OnStandbyToRun(void)
{
  Test_Trace(13U);
  ModeSwc_ModeInSwitch = Rte_Mode_EcuMode_EcuMode();
}
