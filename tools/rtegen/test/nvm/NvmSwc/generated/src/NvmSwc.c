/*
 * File: NvmSwc.c
 *
 * Synthetic component for the NVM-02 host test (tools/rtegen/test/nvm), written by hand in the style of the
 * Embedded Coder AUTOSAR export: explicit Rte_Read/Rte_Write, entry points without arguments.
 */

#include "NvmSwc.h"

uint32 NvmSwc_StoredAtInit = 0U;
uint32 NvmSwc_BootCount    = 0U;
uint16 NvmSwc_Trim         = 0U;
uint16 NvmSwc_TrimRequest  = 0U;

/* init: the stored boot count plus one goes back into the same block */
void NvmSwc_Init(void)
{
  (void)Rte_Read_StoredCount_StoredCount(&NvmSwc_StoredAtInit);
  NvmSwc_BootCount = NvmSwc_StoredAtInit + 1U;
  (void)Rte_Write_Count_Count(NvmSwc_BootCount);
}

/* 10 ms: reads the trim value, writes a new one on request */
void NvmSwc_Run10ms(void)
{
  uint32 wide = 0U;
  (void)Rte_Read_Trim_Trim(&NvmSwc_Trim);
  (void)Rte_Read_WideTrim_WideTrim(&wide);
  if (NvmSwc_TrimRequest != 0U) {
    (void)Rte_Write_TrimOut_TrimOut(NvmSwc_TrimRequest);
    NvmSwc_TrimRequest = 0U;
  }
}
