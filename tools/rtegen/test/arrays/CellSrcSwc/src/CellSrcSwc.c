/* CellSrcSwc: Cell voltage source (array port with symbolic dimension)
 *
 * RTE-25 test component (tools/rtegen/test/arrays). Writes CellVoltages, whose dimension swc.yaml names as
 * pack.number_cells of CellSrcSwc_init.m; the generated RTE has the number (rt_Array_uint16_6). Cell i gets
 * BaseVoltage_mV + 10 * i + the number of runs so far, so the reader can tell every element and every cycle apart. */

#include "Rte_CellSrcSwc.h"

#define CELLSRC_CELLS 6U              /* = pack.number_cells */

static uint16 runs;

void CellSrcSwc_Init(void)
{
    runs = 0U;
}

void CellSrcSwc_Run10ms(void)
{
    uint16 base = 0U;
    rt_Array_uint16_6 cells;
    uint8 i;
    (void)Rte_Read_BaseVoltage_mV_BaseVoltage_mV(&base);
    ++runs;
    for (i = 0U; i < CELLSRC_CELLS; ++i)
    {
        cells[i] = (uint16)(base + (uint16)(10U * i) + runs);
    }
    (void)Rte_Write_CellVoltages_CellVoltages(cells);
}
