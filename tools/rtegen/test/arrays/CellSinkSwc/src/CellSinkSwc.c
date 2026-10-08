/* CellSinkSwc: Cell voltage monitor (reads and writes array ports)
 *
 * RTE-25 test component (tools/rtegen/test/arrays). Reads CellVoltages (written by CellSrcSwc in another task),
 * writes the highest cell, its index and the array it read back as CellEcho. */

#include "Rte_CellSinkSwc.h"

#define CELLSINK_CELLS 6U

void CellSinkSwc_Init(void)
{
}

void CellSinkSwc_Run20ms(void)
{
    uint16 cells[CELLSINK_CELLS] = {0U};
    uint16 highest               = 0U;
    uint8 index                  = 0U;
    uint8 i;
    (void)Rte_Read_CellVoltages_CellVoltages(cells);
    for (i = 0U; i < CELLSINK_CELLS; ++i)
    {
        if (cells[i] > highest)
        {
            highest = cells[i];
            index   = i;
        }
    }
    (void)Rte_Write_MaxCell_mV_MaxCell_mV(highest);
    (void)Rte_Write_MaxCellIndex_MaxCellIndex(index);
    (void)Rte_Write_CellEcho_CellEcho(cells);
}
