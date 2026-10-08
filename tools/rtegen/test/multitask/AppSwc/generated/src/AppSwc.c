/*
 * File: AppSwc.c
 *
 * Synthetic component for the RTE-02 host test (tools/rtegen/test/multitask), written by hand in the style of
 * the Embedded Coder AUTOSAR export: explicit Rte_Read/Rte_Write, entry points without arguments.
 */

#include "AppSwc.h"

uint32 AppSwc_Speed1    = 0U;
uint32 AppSwc_Speed2    = 0U;
float64 AppSwc_Wide     = 0.0;
uint32 AppSwc_SlowCount = 0U;

/* Init runnable */
void AppSwc_Init(void)
{
  (void)Rte_Write_Setpoint_Setpoint(5U);
}

/* 10 ms: reads Speed twice around a preemption point, writes Setpoint before it */
void Run10ms(void)
{
  uint32 speed;
  float64 wide;

  Test_Trace(2U);
  (void)Rte_Read_Speed_Speed(&speed);
  AppSwc_Speed1 = speed;
  (void)Rte_Write_Setpoint_Setpoint((uint16)(speed & 0xFFFFU));
  Test_Hook(2U);                       /* preemption point: the 1 ms task may run here */
  (void)Rte_Read_Wide_Wide(&wide);
  (void)Rte_Read_Speed_Speed(&speed);
  AppSwc_Speed2 = speed;
  AppSwc_Wide = wide;
  (void)Rte_Write_Led_Led((boolean)((AppSwc_Speed1 & 1U) != 0U));
}

/* 50 ms with offset 20 ms (ECU description) */
void Run50ms(void)
{
  uint32 speed;

  Test_Trace(3U);
  (void)Rte_Read_Speed_Speed(&speed);
  AppSwc_SlowCount++;
  (void)Rte_Write_SlowCount_SlowCount(AppSwc_SlowCount);
}
