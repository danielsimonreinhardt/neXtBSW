/* CellAppSwc: Consumer of the cell values and their age qualifiers
 *
 * SYS-32 test component (tools/rtegen/test/age). Copies what it reads to its provided ports every 10 ms. */

#include "Rte_CellAppSwc.h"

#define CELLAPP_CELLS 4U

void CellAppSwc_Init(void)
{
}

void CellAppSwc_Run10ms(void)
{
    uint16 cells[CELLAPP_CELLS] = {0U};
    uint8 cellsAge              = 0U;
    uint16 currentAge           = 0U;
    (void)Rte_Read_Cells_mV_Cells_mV(cells);
    (void)Rte_Read_CellsAge_CellsAge(&cellsAge);
    (void)Rte_Read_CurrentAge_CurrentAge(&currentAge);
    (void)Rte_Write_SeenCell0_mV_SeenCell0_mV(cells[0]);
    (void)Rte_Write_SeenCellsAge_SeenCellsAge(cellsAge);
    (void)Rte_Write_SeenCurrentAge_SeenCurrentAge(currentAge);
}
