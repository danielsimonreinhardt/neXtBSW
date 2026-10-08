/*
 * File: TypeSink.c
 *
 * Synthetic component for the RTE-04 host test (tools/rtegen/test/types), written by hand in the style of the
 * Embedded Coder AUTOSAR export: explicit Rte_Read/Rte_Write with the argument forms of records (pointer, const
 * pointer) and arrays (pointer to the element).
 */

#include "TypeSink.h"

TypeSwc_StatBus TypeSink_Stat;
uint16 TypeSink_Scaled[4];
uint32 TypeSink_Calls10 = 0U;

void TypeSink_Init(void)
{
  (void)Rte_Write_Check_Check(1U);
}

/* 10 ms (task fast, like TypeSwc.Run10ms): largest element of Scaled */
void Sink10ms(void)
{
  uint16 scaled[4];
  uint16 peak = 0U;
  int k;
  (void)Rte_Read_Scaled_Scaled(scaled);
  for (k = 0; k < 4; k++) {
    if (scaled[k] > peak) {
      peak = scaled[k];
    }
  }
  TypeSink_Calls10++;
  (void)Rte_Write_Peak_Peak(peak);
}

/* 20 ms (task slow): checksum over the record and the array, mode taken over */
void Sink20ms(void)
{
  uint32 check;
  int k;
  (void)Rte_Read_Stat_Stat(&TypeSink_Stat);
  (void)Rte_Read_Scaled_Scaled(TypeSink_Scaled);
  check = TypeSink_Stat.Sum + TypeSink_Stat.Max + (uint32)TypeSink_Stat.Mode * 1000U
          + (uint32)(sint32)TypeSink_Stat.Echo.Speed + TypeSink_Stat.Echo.Limits[1];
  for (k = 0; k < 4; k++) {
    check += TypeSink_Scaled[k];
  }
  (void)Rte_Write_Check_Check(check);
  (void)Rte_Write_LastMode_LastMode(TypeSink_Stat.Mode);
}
