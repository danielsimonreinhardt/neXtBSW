/* SPDX-License-Identifier: Apache-2.0 */
/*
 * File: SensorSwc.c
 *
 * Synthetic component for the IO-18 host test (tools/rtegen/test/sensor_host), written by hand in the style of the
 * Embedded Coder AUTOSAR export: reads the signed physical ADC values and their qualifiers the RTE provides.
 */

#include "SensorSwc.h"

sint32 SensorSwc_TsCurrent = 0;
uint8 SensorSwc_TsCurrentQlf = 0U;
sint32 SensorSwc_Proxy = 0;
uint8 SensorSwc_ProxyQlf = 0U;
uint8 SensorSwc_SwitchQlf = 0U;

void SensorSwc_Run10ms(void)
{
  (void)Rte_Read_TsCurrent_TsCurrent(&SensorSwc_TsCurrent);
  (void)Rte_Read_TsCurrentQlf_TsCurrentQlf(&SensorSwc_TsCurrentQlf);
  (void)Rte_Read_Proxy_Proxy(&SensorSwc_Proxy);
  (void)Rte_Read_ProxyQlf_ProxyQlf(&SensorSwc_ProxyQlf);
  (void)Rte_Read_SwitchQlf_SwitchQlf(&SensorSwc_SwitchQlf);
}
