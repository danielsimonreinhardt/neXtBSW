% SPDX-License-Identifier: Apache-2.0
function TypeYamlSwc_logic(mdl, ~)
%TYPEYAMLSWC_LOGIC Logic of the RTE-15 test component, the same as TypeSwc (tools/rtegen/test/types/TypeSwc_create.m).
%   Called by swc_create(..., 'Logic', 'TypeYamlSwc_logic') after the ports and the runnable subsystem exist and the
%   named types of swc.yaml are defined (TypeYamlSwc_types.m). Run10ms:
%     Scaled  = 2 * Samples (saturating)
%     Stat    = Sum/Max of Samples, Mode = Cmd.Enable ? Cmd.Mode : ModeIn, Echo = Cmd (nested record)
%     ModeOut = Off while ModeIn is Fault, else ModeIn
%     TempOut = Temp + 1 degC (same fixed-point type, saturating)

s = swc_runnable_subsystem(mdl, 'Run10ms');
fcn = [s '/Logic'];
add_block('simulink/User-Defined Functions/MATLAB Function', fcn, 'Position', [220 60 420 200]);
chart = sfroot().find('-isa', 'Stateflow.EMChart', 'Path', fcn);
chart.Script = strjoin({
    'function [Scaled, Stat, ModeOut] = Logic(Samples, Cmd, ModeIn)'
    '%#codegen'
    'Scaled = zeros(4, 1, ''uint16'');'
    'total = uint32(0);'
    'largest = uint16(0);'
    'for k = 1:4'
    '    Scaled(k) = Samples(k) * uint16(2);   % integer arithmetic saturates'
    '    total = total + uint32(Samples(k));'
    '    largest = max(largest, Samples(k));'
    'end'
    'Stat.Sum = total;'
    'Stat.Max = largest;'
    'if Cmd.Enable'
    '    Stat.Mode = Cmd.Mode;'
    'else'
    '    Stat.Mode = ModeIn;'
    'end'
    'Stat.Echo = Cmd;'
    'if ModeIn == TypeYamlSwc_Mode.Fault'
    '    ModeOut = TypeYamlSwc_Mode.Off;'
    'else'
    '    ModeOut = ModeIn;'
    'end'
    }, newline);
setData(chart, 'Scaled', 'uint16', '4');
setData(chart, 'Stat', 'Bus: TypeYamlSwc_StatBus', '-1');
setData(chart, 'ModeOut', 'Enum: TypeYamlSwc_Mode', '-1');

add_block('simulink/Sources/Constant', [s '/OneDegree'], 'Position', [220 300 280 330], ...
    'Value', '1', 'OutDataTypeStr', 'TypeYamlSwc_Temp');
add_block('simulink/Math Operations/Sum', [s '/AddOne'], 'Position', [340 280 370 320], 'Inputs', '++', ...
    'OutDataTypeStr', 'TypeYamlSwc_Temp', 'SaturateOnIntegerOverflow', 'on', 'RndMeth', 'Nearest');

add_line(s, 'Samples/1', 'Logic/1', 'autorouting', 'on');
add_line(s, 'Cmd/1', 'Logic/2', 'autorouting', 'on');
add_line(s, 'ModeIn/1', 'Logic/3', 'autorouting', 'on');
add_line(s, 'Logic/1', 'Scaled/1', 'autorouting', 'on');
add_line(s, 'Logic/2', 'Stat/1', 'autorouting', 'on');
add_line(s, 'Logic/3', 'ModeOut/1', 'autorouting', 'on');
add_line(s, 'Temp/1', 'AddOne/1', 'autorouting', 'on');
add_line(s, 'OneDegree/1', 'AddOne/2', 'autorouting', 'on');
add_line(s, 'AddOne/1', 'TempOut/1', 'autorouting', 'on');
end

function setData(chart, name, type, size)
d = chart.find('-isa', 'Stateflow.Data', 'Name', name);
d.DataType = type;
d.Props.Array.Size = size;
end
