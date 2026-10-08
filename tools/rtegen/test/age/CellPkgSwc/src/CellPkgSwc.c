/* CellPkgSwc: Cell controller package stand-in (writes only new values)
 *
 * SYS-32 test component (tools/rtegen/test/age). Every 20 ms it "reads the device": while CellPkgSwc_DeviceAnswers
 * is not 0 it writes four cell voltages and the pack current (with Rte_Write, only then), and it always counts its
 * runs in Runs. The host test sets CellPkgSwc_DeviceAnswers. */

#include "Rte_CellPkgSwc.h"

#define CELLPKG_CELLS 4U

volatile uint8 CellPkgSwc_DeviceAnswers = 1U; /* set by the host test: 0 = the device does not answer */
volatile uint8 CellPkgSwc_CurrentAnswers = 1U; /* set by the host test: 0 = only the current is missing */

static uint16 runs;
static uint16 base_mV;

void CellPkgSwc_Init(void)
{
    runs    = 0U;
    base_mV = 3600U;
}

void CellPkgSwc_Run20ms(void)
{
    uint16 cells[CELLPKG_CELLS];
    uint8 i;
    ++runs;
    if (CellPkgSwc_DeviceAnswers != 0U)
    {
        for (i = 0U; i < CELLPKG_CELLS; ++i)
        {
            cells[i] = (uint16)(base_mV + (uint16)i);
        }
        ++base_mV;
        (void)Rte_Write_Cells_mV_Cells_mV(cells);
        if (CellPkgSwc_CurrentAnswers != 0U)
        {
            (void)Rte_Write_Current_mA_Current_mA(-1500);
        }
    }
    (void)Rte_Write_Runs_Runs(runs);
}
