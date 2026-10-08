/*
 * File: CalSwc.c
 *
 * Synthetic component for the RTE-03 host test (tools/rtegen/test/param).
 *   Out  = Gain * (Table[0] + .. + Table[3]) + Factor(stored) + Enable
 *   FOut = Alpha * 8
 * Temp (stored value) is only recorded, the test compares it with the conversion of the generator.
 */

#include "CalSwc.h"

sint16 CalSwc_TempSeen = 0;

void CalSwc_Init(void)
{
    CalSwc_TempSeen = 0;
}

void Run10ms(void)
{
    uint32 sum = 0U;
    uint8 i;
    for (i = 0U; i < 4U; i++)
    {
        sum += (Rte_Prm_CalPrm_Table())[i];
    }
    CalSwc_TempSeen = Rte_Prm_CalPrm_Temp();
    (void)Rte_Write_Out_Out((uint32)Rte_Prm_CalPrm_Gain() * sum + (uint32)Rte_Prm_CalPrm_Factor()
                           + (Rte_Prm_CalPrm_Enable() ? 1U : 0U));
    (void)Rte_Write_FOut_FOut(Rte_Prm_CalPrm_Alpha() * 8.0F);
}
