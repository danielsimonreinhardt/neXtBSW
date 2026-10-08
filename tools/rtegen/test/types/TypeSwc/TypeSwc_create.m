function TypeSwc_create()
%TYPESWC_CREATE Creates TypeSwc.slx, the test model of RTE-04 (composite and scaled data types at ports).
%   One-time bootstrap (like swc/DemoSwc/DemoSwc_create.m + DemoSwc_autosar.m): creates the export-function
%   model, maps it to an AUTOSAR Classic component with explicit sender-receiver access and saves it. Code and
%   ARXML come from tools/swc/swc_build.m afterwards:
%       addpath tools/swc; addpath tools/rtegen/test/types/TypeSwc
%       TypeSwc_create(); swc_build('tools/rtegen/test/types/TypeSwc')
%
%   Run10ms (10 ms), all ports explicit:
%     required  Samples  uint16[4]                 array
%               Cmd      TypeSwc_CmdBus            record: enum, fixed point, array, boolean
%               ModeIn   TypeSwc_Mode              enumeration
%               Temp     fixdt(0,16,0.1,-40) degC  fixed point with slope and bias
%     provided  Scaled   uint16[4]  = 2 * Samples (saturating)
%               Stat     TypeSwc_StatBus: Sum = sum(Samples), Max = max(Samples),
%                        Mode = Cmd.Enable ? Cmd.Mode : ModeIn, Echo = Cmd (nested record)
%               ModeOut  TypeSwc_Mode = Off while ModeIn is Fault, else ModeIn
%               TempOut  = Temp + 1 degC (same fixed-point type, saturating)

mdl  = 'TypeSwc';
here = fileparts(mfilename('fullpath'));
addpath(here);
TypeSwc_types();
if bdIsLoaded(mdl)
    close_system(mdl, 0);
end
slxFile = fullfile(here, [mdl '.slx']);
if isfile(slxFile)
    delete(slxFile);
end

tempType = 'fixdt(0,16,0.1,-40)';

new_system(mdl);
set_param(mdl, 'PreLoadFcn', 'TypeSwc_types();');
set_param(mdl, 'SolverType', 'Fixed-step', 'Solver', 'FixedStepDiscrete', 'FixedStep', '0.01', ...
    'StopTime', '10', 'IsExportFunctionModel', 'off');
set_param(mdl, 'ProdHWDeviceType', 'ARM Compatible->ARM Cortex-M');
% the MATLAB Function block simulates with the uint8 enumeration only if the simulation target defines the
% imported type itself (in the generated code Rte_Type.h of tools/rtegen defines it)
set_param(mdl, 'SimGenImportedTypeDefs', 'on');

%% ---- root ports ----
fc = add_block('simulink/Sources/In1', [mdl '/Run10ms'], 'Position', [30 20 60 34]);
set_param(fc, 'OutputFunctionCall', 'on', 'SampleTime', '0.01');
inport(mdl, 'Samples', 60,  'uint16', '4');
inport(mdl, 'Cmd',     120, 'Bus: TypeSwc_CmdBus', '1');
inport(mdl, 'ModeIn',  180, 'Enum: TypeSwc_Mode', '1');
t = inport(mdl, 'Temp', 240, tempType, '1');
set_param(t, 'Unit', 'degC');
outport(mdl, 'Scaled',  60,  'uint16');
outport(mdl, 'Stat',    120, 'Bus: TypeSwc_StatBus');
outport(mdl, 'ModeOut', 180, 'Enum: TypeSwc_Mode');
t = outport(mdl, 'TempOut', 240, tempType);
set_param(t, 'Unit', 'degC');

%% ---- Run10ms ----
s = [mdl '/Run10ms_Subsystem'];
add_block('simulink/Ports & Subsystems/Function-Call Subsystem', s, 'Position', [200 40 420 280]);
lines = find_system(s, 'SearchDepth', 1, 'FindAll', 'on', 'Type', 'line');
delete_line(lines);
delete_block([s '/In1']);
delete_block([s '/Out1']);
names = {'Samples', 'Cmd', 'ModeIn', 'Temp'};
for k = 1:numel(names)
    add_block('simulink/Sources/In1', [s '/' names{k}], 'Position', [30 40+60*k 60 54+60*k], ...
        'Port', num2str(k));
end
names = {'Scaled', 'Stat', 'ModeOut', 'TempOut'};
for k = 1:numel(names)
    add_block('simulink/Sinks/Out1', [s '/' names{k}], 'Position', [520 40+60*k 550 54+60*k], ...
        'Port', num2str(k));
end

fcn = [s '/Logic'];
add_block('simulink/User-Defined Functions/MATLAB Function', fcn, 'Position', [200 80 400 220]);
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
    'if ModeIn == TypeSwc_Mode.Fault'
    '    ModeOut = TypeSwc_Mode.Off;'
    'else'
    '    ModeOut = ModeIn;'
    'end'
    }, newline);
setData(chart, 'Scaled', 'uint16', '4');
setData(chart, 'Stat', 'Bus: TypeSwc_StatBus', '-1');
setData(chart, 'ModeOut', 'Enum: TypeSwc_Mode', '-1');

add_block('simulink/Sources/Constant', [s '/OneDegree'], 'Position', [200 300 260 330], ...
    'Value', '1', 'OutDataTypeStr', tempType);
add_block('simulink/Math Operations/Sum', [s '/AddOne'], 'Position', [320 280 350 320], 'Inputs', '++', ...
    'OutDataTypeStr', tempType, 'SaturateOnIntegerOverflow', 'on', 'RndMeth', 'Nearest');

add_line(s, 'Samples/1', 'Logic/1');
add_line(s, 'Cmd/1', 'Logic/2');
add_line(s, 'ModeIn/1', 'Logic/3');
add_line(s, 'Logic/1', 'Scaled/1');
add_line(s, 'Logic/2', 'Stat/1');
add_line(s, 'Logic/3', 'ModeOut/1');
add_line(s, 'Temp/1', 'AddOne/1', 'autorouting', 'on');
add_line(s, 'OneDegree/1', 'AddOne/2', 'autorouting', 'on');
add_line(s, 'AddOne/1', 'TempOut/1', 'autorouting', 'on');

%% ---- root wiring ----
add_line(mdl, 'Run10ms/1', 'Run10ms_Subsystem/Trigger', 'autorouting', 'on');
for k = 1:4
    add_line(mdl, [inportName(k) '/1'], sprintf('Run10ms_Subsystem/%d', k), 'autorouting', 'on');
    add_line(mdl, sprintf('Run10ms_Subsystem/%d', k), sprintf('%s/1', outportName(k)), 'autorouting', 'on');
end
set_param(mdl, 'IsExportFunctionModel', 'on');

%% ---- AUTOSAR mapping ----
set_param(mdl, 'SystemTargetFile', 'autosar.tlc');
autosar.api.create(mdl, 'default');
slMap = autosar.api.getSimulinkMapping(mdl);
for k = 1:4
    [port, element] = getInport(slMap, inportName(k));
    mapInport(slMap, inportName(k), port, element, 'ExplicitReceive');
    [port, element] = getOutport(slMap, outportName(k));
    mapOutport(slMap, outportName(k), port, element, 'ExplicitSend');
end
set_param(mdl, 'GenCodeOnly', 'on');
set_param(mdl, 'GenerateMakefile', 'off');
set_param(mdl, 'GenerateReport', 'off');
set_param(mdl, 'ProdLongLongMode', 'on');
set_param(mdl, 'SupportNonFinite', 'off');
set_param(mdl, 'MatFileLogging', 'off');
set_param(mdl, 'GenerateComments', 'on');
set_param(mdl, 'MaxIdLength', '63');
save_system(mdl, slxFile);
fprintf('created %s\n', slxFile);
end

function name = inportName(k)
names = {'Samples', 'Cmd', 'ModeIn', 'Temp'};
name = names{k};
end

function name = outportName(k)
names = {'Scaled', 'Stat', 'ModeOut', 'TempOut'};
name = names{k};
end

function h = inport(mdl, name, y, type, dims)
h = add_block('simulink/Sources/In1', [mdl '/' name], 'Position', [30 y 60 y+14]);
set_param(h, 'OutDataTypeStr', type, 'PortDimensions', dims);
if startsWith(type, 'Bus:')
    set_param(h, 'BusOutputAsStruct', 'on');
end
end

function h = outport(mdl, name, y, type)
h = add_block('simulink/Sinks/Out1', [mdl '/' name], 'Position', [520 y 550 y+14]);
set_param(h, 'OutDataTypeStr', type);
if startsWith(type, 'Bus:')
    set_param(h, 'BusOutputAsStruct', 'on');
end
end

function setData(chart, name, type, size)
d = chart.find('-isa', 'Stateflow.Data', 'Name', name);
d.DataType = type;
d.Props.Array.Size = size;
end
