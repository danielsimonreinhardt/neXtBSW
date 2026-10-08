/* SinkSwc: reads values of disabled parts and echoes them
 *
 * RTE-23 test component (tools/rtegen/test/disabled). Its sources: LevelSrcSwc (disabled component), AuxSrcSwc
 * RunMain10ms (runs) and RunAux10ms (disabled runnable), and the age qualifier of LevelSrcSwc.Level_mV. The runnable
 * stays enabled and must run with the constant values; Echo* show what it read, Runs that it ran. */

#include "Rte_SinkSwc.h"

static uint16 runs;

void SinkSwc_Init(void)
{
    runs = 0U;
}

void SinkSwc_Run10ms(void)
{
    uint32 level = 0xFFFFFFFFU;
    uint8 qlf = 0xFFU;
    uint8 age = 0xFFU;
    uint16 main_value = 0xFFFFU;
    uint16 aux = 0xFFFFU;
    (void)Rte_Read_Level_mV_Level_mV(&level);
    (void)Rte_Read_LevelQlf_LevelQlf(&qlf);
    (void)Rte_Read_LevelAge_LevelAge(&age);
    (void)Rte_Read_Main_Main(&main_value);
    (void)Rte_Read_Aux_Aux(&aux);
    ++runs;
    (void)Rte_Write_Echo_mV_Echo_mV(level);
    (void)Rte_Write_EchoQlf_EchoQlf(qlf);
    (void)Rte_Write_EchoAge_EchoAge(age);
    (void)Rte_Write_EchoMain_EchoMain(main_value);
    (void)Rte_Write_EchoAux_EchoAux(aux);
    (void)Rte_Write_Runs_Runs(runs);
}
