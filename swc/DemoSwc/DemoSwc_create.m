function DemoSwc_create()
%DEMOSWC_CREATE Creates the AUTOSAR demo software component model DemoSwc.slx.
%   One-time bootstrap: after creation DemoSwc.slx is the master and is edited in Simulink.
%   Board demo logic (formerly DemoSystem):
%     Run10ms : Led1 follows Button1, Led2 toggles every 50 calls (1 Hz blink) while the signal
%               qualifier PcCommandQlf is QLF_OK, otherwise Led2 stays off (release, COM-13)
%     Run100ms: first-order low-pass of MotorNtc_mV -> MotorNtcFiltered_mV
%
%   QLF_OK comes from the generated Rte_Qlf.m (tools/rtegen, COM-03), never as a number in the
%   model: run tools/swc/swc_load_qlf before creating, building or simulating the model.

mdl  = 'DemoSwc';
here = fileparts(mfilename('fullpath'));
if bdIsLoaded(mdl)
    close_system(mdl, 0);
end
slxFile = fullfile(here, [mdl '.slx']);
if isfile(slxFile)
    delete(slxFile);
end

new_system(mdl);
set_param(mdl, 'SolverType', 'Fixed-step', 'Solver', 'FixedStepDiscrete', 'FixedStep', '0.01', ...
    'StopTime', '10', 'IsExportFunctionModel', 'off');
% Target: S32K144 (Cortex-M4F, 32 bit, little endian)
set_param(mdl, 'ProdHWDeviceType', 'ARM Compatible->ARM Cortex-M');

%% ---- root ports ----
fc10  = addPort(mdl, 'Run10ms',  'In',  [30 40 60 54]);
set_param(fc10,  'OutputFunctionCall', 'on', 'SampleTime', '0.01');
fc100 = addPort(mdl, 'Run100ms', 'In',  [30 240 60 254]);
set_param(fc100, 'OutputFunctionCall', 'on', 'SampleTime', '0.1');

btn = addPort(mdl, 'Button1',     'In',  [30 100 60 114]);
set_param(btn, 'OutDataTypeStr', 'boolean', 'PortDimensions', '1');
ntc = addPort(mdl, 'MotorNtc_mV', 'In',  [30 300 60 314]);
set_param(ntc, 'OutDataTypeStr', 'uint32', 'PortDimensions', '1');
% Signal qualifier of the received CAN message PC_Command (RTE target can.qlf.PC_Command, COM-03).
% Added after the existing inports so their port numbers stay 1 .. 4.
qlf = addPort(mdl, 'PcCommandQlf', 'In', [30 150 60 164]);
set_param(qlf, 'OutDataTypeStr', 'uint8', 'PortDimensions', '1');

led1 = addPort(mdl, 'Led1', 'Out', [520 100 550 114]);
set_param(led1, 'OutDataTypeStr', 'boolean');
led2 = addPort(mdl, 'Led2', 'Out', [520 150 550 164]);
set_param(led2, 'OutDataTypeStr', 'boolean');
ntcf = addPort(mdl, 'MotorNtcFiltered_mV', 'Out', [520 300 550 314]);
set_param(ntcf, 'OutDataTypeStr', 'uint32');

%% ---- Run10ms: button -> LED1, blinker (released by the qualifier) -> LED2 ----
s10 = [mdl '/Run10ms_Subsystem'];
add_block('simulink/Ports & Subsystems/Function-Call Subsystem', s10, 'Position', [200 60 380 200]);
clearSubsystem(s10);
in = add_block('simulink/Sources/In1', [s10 '/Button1'], 'Position', [40 60 70 74]);
o1 = add_block('simulink/Sinks/Out1',  [s10 '/Led1'],    'Position', [400 60 430 74]);
o2 = add_block('simulink/Sinks/Out1',  [s10 '/Led2'],    'Position', [400 160 430 174]);
set_param(o2, 'Port', '2');
add_line(s10, 'Button1/1', 'Led1/1');

add_block('simulink/Sources/Counter Limited', [s10 '/BlinkCounter'], 'Position', [40 150 90 190], ...
    'uplimit', '49', 'tsamp', '-1');
add_block('simulink/Logic and Bit Operations/Compare To Zero', [s10 '/IsWrap'], ...
    'Position', [130 155 170 185], 'relop', '==');
add_block('simulink/Logic and Bit Operations/Logical Operator', [s10 '/Toggle'], ...
    'Position', [230 150 260 190], 'Operator', 'XOR');
add_block('simulink/Discrete/Unit Delay', [s10 '/Led2State'], 'Position', [230 220 260 250], ...
    'InitialCondition', 'false', 'SampleTime', '-1');
set_param([s10 '/Led2State'], 'Orientation', 'left');
add_line(s10, 'BlinkCounter/1', 'IsWrap/1');
add_line(s10, 'IsWrap/1', 'Toggle/1');
add_line(s10, 'Led2State/1', 'Toggle/2', 'autorouting', 'on');
add_line(s10, 'Toggle/1', 'Led2State/1', 'autorouting', 'on');

% Release of Led2 by the signal qualifier (COM-13): Led2 = blink AND (PcCommandQlf == QLF_OK).
% The blink state machine keeps running while the qualifier reports an error, so the phase of the
% 1 Hz blink is kept and the release is reversible within one call.
add_block('simulink/Sources/In1', [s10 '/PcCommandQlf'], 'Position', [40 250 70 264], 'Port', '2');
add_block('simulink/Sources/Constant', [s10 '/QlfOk'], 'Position', [40 300 90 330], ...
    'Value', 'QLF_OK', 'OutDataTypeStr', 'uint8');
add_block('simulink/Logic and Bit Operations/Relational Operator', [s10 '/QlfIsOk'], ...
    'Position', [130 255 170 300], 'Operator', '==');
add_block('simulink/Logic and Bit Operations/Logical Operator', [s10 '/Led2Release'], ...
    'Position', [320 155 350 195], 'Operator', 'AND', 'Inputs', '2');
add_line(s10, 'PcCommandQlf/1', 'QlfIsOk/1');
add_line(s10, 'QlfOk/1', 'QlfIsOk/2');
add_line(s10, 'Toggle/1', 'Led2Release/1', 'autorouting', 'on');
add_line(s10, 'QlfIsOk/1', 'Led2Release/2', 'autorouting', 'on');
add_line(s10, 'Led2Release/1', 'Led2/1');

%% ---- Run100ms: NTC low-pass y = y + (u - y) / 8 ----
s100 = [mdl '/Run100ms_Subsystem'];
add_block('simulink/Ports & Subsystems/Function-Call Subsystem', s100, 'Position', [200 260 380 360]);
clearSubsystem(s100);
add_block('simulink/Sources/In1',  [s100 '/MotorNtc_mV'], 'Position', [30 60 60 74]);
add_block('simulink/Sinks/Out1',   [s100 '/MotorNtcFiltered_mV'], 'Position', [520 60 550 74]);
add_block('simulink/Signal Attributes/Data Type Conversion', [s100 '/ToSingle'], ...
    'Position', [90 55 150 80], 'OutDataTypeStr', 'single');
add_block('simulink/Math Operations/Sum', [s100 '/Error'], 'Position', [190 55 210 80], 'Inputs', '+-');
% Filter weight: calibration parameter FilterAlpha (RTE-03), value from DemoSwc_data.m, mapped to the
% ParameterReceiver port CalPrm by DemoSwc_autosar.m
add_block('simulink/Math Operations/Gain', [s100 '/Alpha'], 'Position', [240 55 280 80], 'Gain', 'FilterAlpha');
alpha = Simulink.Parameter(0);   % no number in the model: DemoSwc_autosar/swc_build load DemoSwc_data.m
alpha.DataType = 'single';
assignin(get_param(mdl, 'ModelWorkspace'), 'FilterAlpha', alpha);
add_block('simulink/Math Operations/Sum', [s100 '/Update'], 'Position', [320 55 340 80], 'Inputs', '++');
add_block('simulink/Discrete/Unit Delay', [s100 '/FilterState'], 'Position', [320 130 350 160], ...
    'InitialCondition', '0', 'SampleTime', '-1');
set_param([s100 '/FilterState'], 'Orientation', 'left');
add_block('simulink/Signal Attributes/Data Type Conversion', [s100 '/ToUint32'], ...
    'Position', [420 55 480 80], 'OutDataTypeStr', 'uint32', 'RndMeth', 'Nearest', 'SaturateOnIntegerOverflow', 'on');
add_line(s100, 'MotorNtc_mV/1', 'ToSingle/1');
add_line(s100, 'ToSingle/1', 'Error/1');
add_line(s100, 'Error/1', 'Alpha/1');
add_line(s100, 'Alpha/1', 'Update/1');
add_line(s100, 'Update/1', 'ToUint32/1');
add_line(s100, 'ToUint32/1', 'MotorNtcFiltered_mV/1');
add_line(s100, 'Update/1', 'FilterState/1', 'autorouting', 'on');
add_line(s100, 'FilterState/1', 'Error/2', 'autorouting', 'on');
add_line(s100, 'FilterState/1', 'Update/2', 'autorouting', 'on');

%% ---- root wiring ----
add_line(mdl, 'Run10ms/1',  'Run10ms_Subsystem/Trigger');
add_line(mdl, 'Button1/1',  'Run10ms_Subsystem/1');
add_line(mdl, 'PcCommandQlf/1', 'Run10ms_Subsystem/2', 'autorouting', 'on');
add_line(mdl, 'Run10ms_Subsystem/1', 'Led1/1');
add_line(mdl, 'Run10ms_Subsystem/2', 'Led2/1');
add_line(mdl, 'Run100ms/1', 'Run100ms_Subsystem/Trigger');
add_line(mdl, 'MotorNtc_mV/1', 'Run100ms_Subsystem/1');
add_line(mdl, 'Run100ms_Subsystem/1', 'MotorNtcFiltered_mV/1');

set_param(mdl, 'IsExportFunctionModel', 'on');
save_system(mdl, slxFile);
fprintf('created %s\n', slxFile);
end

function h = addPort(mdl, name, kind, pos)
if strcmp(kind, 'In')
    h = add_block('simulink/Sources/In1', [mdl '/' name], 'Position', pos);
else
    h = add_block('simulink/Sinks/Out1', [mdl '/' name], 'Position', pos);
end
end

function clearSubsystem(sys)
% Removes the default In1 -> Out1 connection of a new function-call subsystem.
lines = find_system(sys, 'SearchDepth', 1, 'FindAll', 'on', 'Type', 'line');
delete_line(lines);
delete_block([sys '/In1']);
delete_block([sys '/Out1']);
end
