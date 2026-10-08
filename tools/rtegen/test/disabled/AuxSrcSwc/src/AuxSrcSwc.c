/* AuxSrcSwc: one runnable enabled, one disabled
 *
 * RTE-23 test component (tools/rtegen/test/disabled). RunMain10ms counts Main up; RunAux10ms is disabled in the
 * ECU description and must never run (AuxSrcSwc_AuxCalls stays 0). */

#include "Rte_AuxSrcSwc.h"

static uint16 main_count;
uint16 AuxSrcSwc_AuxCalls = 0U;

void AuxSrcSwc_Init(void)
{
    main_count = 0U;
}

void AuxSrcSwc_RunMain10ms(void)
{
    ++main_count;
    (void)Rte_Write_Main_Main(main_count);
}

void AuxSrcSwc_RunAux10ms(void)
{
    ++AuxSrcSwc_AuxCalls;
    (void)Rte_Write_Aux_Aux(999U);
}
