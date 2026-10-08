% SPDX-License-Identifier: Apache-2.0
function DemoSwc_logic(mdl, ~)
%DEMOSWC_LOGIC Logic of DemoSwc for swc_create (RTE-07 test data).
%   Same blocks as swc/DemoSwc/DemoSwc_create.m, drawn into the runnable subsystems that swc_create
%   has already created with their In/Out blocks (named after the ports) and the root wiring.

%% ---- Run10ms: button -> LED1, blinker (released by the qualifier) -> LED2 ----
% swc/DemoSwc (DemoSwc_create.m) keeps the subsystem names of before TOOL-180, and its generated code names them
% in comments: the reproduction gives the subsystems these names back.
set_param(swc_runnable_subsystem(mdl, 'Run10ms'), 'Name', 'Run10ms_Subsystem');
set_param(swc_runnable_subsystem(mdl, 'Run100ms'), 'Name', 'Run100ms_Subsystem');
s10 = [mdl '/Run10ms_Subsystem'];
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
add_block('simulink/Signal Attributes/Data Type Conversion', [s100 '/ToSingle'], ...
    'Position', [90 55 150 80], 'OutDataTypeStr', 'single');
add_block('simulink/Math Operations/Sum', [s100 '/Error'], 'Position', [190 55 210 80], 'Inputs', '+-');
add_block('simulink/Math Operations/Gain', [s100 '/Alpha'], 'Position', [240 55 280 80], 'Gain', 'FilterAlpha');
add_block('simulink/Math Operations/Sum', [s100 '/Update'], 'Position', [320 55 340 80], 'Inputs', '++');
add_block('simulink/Discrete/Unit Delay', [s100 '/FilterState'], 'Position', [320 130 350 160], ...
    'InitialCondition', '0', 'SampleTime', '-1');
set_param([s100 '/FilterState'], 'Orientation', 'left');
add_block('simulink/Signal Attributes/Data Type Conversion', [s100 '/ToUint32'], ...
    'Position', [420 55 480 80], 'OutDataTypeStr', 'uint32', 'RndMeth', 'Nearest', ...
    'SaturateOnIntegerOverflow', 'on');
add_line(s100, 'MotorNtc_mV/1', 'ToSingle/1');
add_line(s100, 'ToSingle/1', 'Error/1');
add_line(s100, 'Error/1', 'Alpha/1');
add_line(s100, 'Alpha/1', 'Update/1');
add_line(s100, 'Update/1', 'ToUint32/1');
add_line(s100, 'ToUint32/1', 'MotorNtcFiltered_mV/1');
add_line(s100, 'Update/1', 'FilterState/1', 'autorouting', 'on');
add_line(s100, 'FilterState/1', 'Error/2', 'autorouting', 'on');
add_line(s100, 'FilterState/1', 'Update/2', 'autorouting', 'on');
end
