% RTE-25 test data: the init script of an imported model, as tools/swc/initdata.py reads it without MATLAB.
% Only plain top-level assignments count; pack.number_cells is the dimension of the port CellVoltages.
pack.modules = 2;
pack.cells_per_module = 3;   % two modules of three cells
pack.number_cells = pack.modules * pack.cells_per_module;
pack.name = 'test pack';
