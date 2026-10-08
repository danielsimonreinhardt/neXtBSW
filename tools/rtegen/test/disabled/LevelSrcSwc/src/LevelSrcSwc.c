/* LevelSrcSwc: level source that the ECU description disables
 *
 * RTE-23 test component (tools/rtegen/test/disabled). components[].enabled is false: neither LevelSrcSwc_Init nor
 * LevelSrcSwc_Run10ms may be called. LevelSrcSwc_Calls counts every call, the host test expects 0. */

#include "Rte_LevelSrcSwc.h"

uint16 LevelSrcSwc_Calls = 0U;

void LevelSrcSwc_Init(void)
{
    ++LevelSrcSwc_Calls;
}

void LevelSrcSwc_Run10ms(void)
{
    ++LevelSrcSwc_Calls;
    (void)Rte_Write_Level_mV_Level_mV(4321U);
    (void)Rte_Write_LevelQlf_LevelQlf(1U); /* QLF_OK: must never reach the reader */
}
