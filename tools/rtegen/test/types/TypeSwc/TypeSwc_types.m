function TypeSwc_types()
%TYPESWC_TYPES Bus objects of the RTE-04 test component TypeSwc in the base workspace.
%   Called by the PreLoadFcn of TypeSwc.slx, so tools/swc/swc_build.m (which puts the component folder on the
%   path) finds the types. The enumeration TypeSwc_Mode is the class file next to this script.
%
%   TypeSwc_CmdBus : record with an enumeration, a fixed-point value (binary point), an array and a boolean
%   TypeSwc_StatBus: record with integers, the enumeration and a nested record (TypeSwc_CmdBus)

% Fixed point inside a record: a named numeric type. A bare fixdt(...) bus element loses its scaling in the ARXML
% of Embedded Coder R2024a (plain sint16, IDENTICAL compu method); the named type keeps the LINEAR compu method.
speed = Simulink.NumericType;
speed.DataTypeMode = 'Fixed-point: binary point scaling';
speed.Signedness = 'Signed';
speed.WordLength = 16;
speed.FractionLength = 4;
speed.IsAlias = true;
speed.DataScope = 'Imported';
speed.HeaderFile = 'Rte_Type.h';
assignin('base', 'TypeSwc_Speed', speed);

cmd = Simulink.Bus;
cmd.Description = 'command record (RTE-04 test)';
% AUTOSAR: the RTE defines the record types (tools/rtegen, Rte_Type.h), the component code includes them
cmd.DataScope = 'Imported';
cmd.HeaderFile = 'Rte_Type.h';
cmd.Elements = [ ...
    element('Mode',   'Enum: TypeSwc_Mode', 1), ...
    element('Speed',  'TypeSwc_Speed',      1, 'rpm'), ...    % 1/16 rpm steps
    element('Limits', 'uint8',              2), ...    % array inside a record
    element('Enable', 'boolean',            1)];
assignin('base', 'TypeSwc_CmdBus', cmd);

stat = Simulink.Bus;
stat.Description = 'status record (RTE-04 test)';
stat.DataScope = 'Imported';
stat.HeaderFile = 'Rte_Type.h';
stat.Elements = [ ...
    element('Sum',  'uint32',              1), ...
    element('Max',  'uint16',              1), ...
    element('Mode', 'Enum: TypeSwc_Mode',  1), ...
    element('Echo', 'Bus: TypeSwc_CmdBus', 1)];     % nested record
assignin('base', 'TypeSwc_StatBus', stat);
end

function e = element(name, type, dims, unit)
e = Simulink.BusElement;
if nargin > 3
    e.Unit = unit;
end
e.Name = name;
e.DataType = type;
e.Dimensions = dims;
end
