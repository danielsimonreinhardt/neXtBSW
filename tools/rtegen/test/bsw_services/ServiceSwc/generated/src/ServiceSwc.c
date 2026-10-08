/*
 * File: ServiceSwc.c
 *
 * Synthetic component for the RTE-05 host test (tools/rtegen/test/bsw_services), written by hand in the style of
 * the Embedded Coder AUTOSAR export: explicit Rte_Read/Rte_Write, entry points without arguments.
 */

#include "ServiceSwc.h"

uint8 ServiceSwc_PowerState     = 0U;
boolean ServiceSwc_PowerOk      = 0U;
uint16 ServiceSwc_SupplyMv      = 0U;
uint8 ServiceSwc_PowerQlf       = 0U;
uint16 ServiceSwc_ResetReason   = 0U;
boolean ServiceSwc_ResetWatchdog = 0U;
boolean ServiceSwc_ResetPowerOn = 0U;
uint32 ServiceSwc_ResetQlf      = 0U;
uint32 ServiceSwc_TimeMs        = 0U;
uint32 ServiceSwc_TimeUs        = 0U;
uint32 ServiceSwc_TimeUsAgain   = 0U;
uint32 ServiceSwc_FastUs        = 0U;
uint8 ServiceSwc_FastReason     = 0U;

static uint32 ServiceSwc_Count = 0U;

/* 10 ms: every value of the three services */
void ServiceSwc_Run10ms(void)
{
  (void)Rte_Read_PowerState_PowerState(&ServiceSwc_PowerState);
  (void)Rte_Read_PowerOk_PowerOk(&ServiceSwc_PowerOk);
  (void)Rte_Read_SupplyMv_SupplyMv(&ServiceSwc_SupplyMv);
  (void)Rte_Read_PowerQlf_PowerQlf(&ServiceSwc_PowerQlf);
  (void)Rte_Read_ResetReason_ResetReason(&ServiceSwc_ResetReason);
  (void)Rte_Read_ResetWatchdog_ResetWatchdog(&ServiceSwc_ResetWatchdog);
  (void)Rte_Read_ResetPowerOn_ResetPowerOn(&ServiceSwc_ResetPowerOn);
  (void)Rte_Read_ResetQlf_ResetQlf(&ServiceSwc_ResetQlf);
  (void)Rte_Read_TimeMs_TimeMs(&ServiceSwc_TimeMs);
  (void)Rte_Read_TimeUs_TimeUs(&ServiceSwc_TimeUs);
  Test_Hook(1U);                       /* the timer advances here: the port keeps the value of this cycle */
  (void)Rte_Read_TimeUs_TimeUs(&ServiceSwc_TimeUsAgain);
  ServiceSwc_Count++;
  (void)Rte_Write_Count_Count(ServiceSwc_Count);
}

/* 1 ms (task motor): the time and the reset reason read in a second task */
void ServiceSwc_Run1ms(void)
{
  (void)Rte_Read_FastUs_FastUs(&ServiceSwc_FastUs);
  (void)Rte_Read_FastReason_FastReason(&ServiceSwc_FastReason);
}
