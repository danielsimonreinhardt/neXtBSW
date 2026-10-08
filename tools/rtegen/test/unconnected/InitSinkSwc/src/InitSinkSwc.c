/* InitSinkSwc: reads two unconnected inputs and echoes them
 *
 * RTE-27 test component (tools/rtegen/test/unconnected). Level_mV and LevelQlf have no connection in the ECU
 * description; the RTE hands the runnable the init value of Level_mV (0) and QLF_NOT_AVAILABLE for the qualifier (RTE-28).
 * Echo_mV and EchoQlf show what the runnable read, Runs that it ran. */

#include "Rte_InitSinkSwc.h"

static uint16 runs;

void InitSinkSwc_Init(void)
{
    runs = 0U;
}

void InitSinkSwc_Run10ms(void)
{
    uint32 level = 0xFFFFFFFFU;
    uint8 qlf = 0xFFU;
    (void)Rte_Read_Level_mV_Level_mV(&level);
    (void)Rte_Read_LevelQlf_LevelQlf(&qlf);
    ++runs;
    (void)Rte_Write_Echo_mV_Echo_mV(level);
    (void)Rte_Write_EchoQlf_EchoQlf(qlf);
    (void)Rte_Write_Runs_Runs(runs);
}
