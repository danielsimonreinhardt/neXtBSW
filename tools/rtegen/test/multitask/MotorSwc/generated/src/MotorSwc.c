/*
 * File: MotorSwc.c
 *
 * Synthetic component for the RTE-02 host test (tools/rtegen/test/multitask), written by hand in the style of
 * the Embedded Coder AUTOSAR export: explicit Rte_Read/Rte_Write, entry points without arguments.
 * Test_Hook/Test_Trace are provided by the test harness (simulated preemption, call trace).
 */

#include "MotorSwc.h"

uint32 MotorSwc_Cycles        = 0U; /* calls of Run1ms */
uint16 MotorSwc_SetpointSeen  = 0U; /* Setpoint read by Run1ms */
boolean MotorSwc_EnableSeen   = 0U; /* Enable read by Run1ms */
uint16 MotorSwc_DiagSetpoint  = 0U; /* Setpoint read by RunDiag10ms (other task) */

/* Init runnable */
void MotorSwc_Init(void)
{
  (void)Rte_Write_Speed_Speed(100U);
}

/* 1 ms: speed and its double precision companion are written as a pair */
void Run1ms(void)
{
  uint16 setpoint;
  boolean enable;
  uint32 speed;

  (void)Rte_Read_Setpoint_Setpoint(&setpoint);
  (void)Rte_Read_Enable_Enable(&enable);
  MotorSwc_SetpointSeen = setpoint;
  MotorSwc_EnableSeen = enable;
  MotorSwc_Cycles++;
  speed = 1000U + MotorSwc_Cycles;
  (void)Rte_Write_Speed_Speed(speed);
  Test_Hook(1U);                       /* preemption point between the two writes */
  (void)Rte_Write_Wide_Wide(1.5 * (float64)speed);
}

/* 10 ms diagnosis, runs in another task than Run1ms but reads the same port */
void RunDiag10ms(void)
{
  uint16 setpoint;

  Test_Trace(1U);
  (void)Rte_Read_Setpoint_Setpoint(&setpoint);
  MotorSwc_DiagSetpoint = setpoint;
}
