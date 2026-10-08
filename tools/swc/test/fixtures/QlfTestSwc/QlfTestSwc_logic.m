% SPDX-License-Identifier: Apache-2.0
function QlfTestSwc_logic(mdl, ~)
%QLFTESTSWC_LOGIC Logic of the SensorQualifier test component for swc_create (RTE-07 test data).
%   Run10ms:  VoltageQlf = SensorQualifier(Voltage_mV, VoltageSrcQlf), range 1000..4000 mV, 30 ms
%   Run20ms:  TempQlf    = SensorQualifier(Temp_dC, QLF_OK), range -400..1250 (0.1 degC), 40 ms

s10 = swc_runnable_subsystem(mdl, 'Run10ms');   % <Swc>_task10ms (TOOL-180)
add_block('NxSwcLib/SensorQualifier', [s10 '/VoltageQualifier'], 'Position', [200 30 320 110], ...
    'min_value', '1000', 'max_value', '4000', 'debounce_ms', '30');
add_line(s10, 'Voltage_mV/1', 'VoltageQualifier/1', 'autorouting', 'on');
add_line(s10, 'VoltageSrcQlf/1', 'VoltageQualifier/2', 'autorouting', 'on');
add_line(s10, 'VoltageQualifier/1', 'VoltageQlf/1', 'autorouting', 'on');

s20 = swc_runnable_subsystem(mdl, 'Run20ms');
add_block('NxSwcLib/SensorQualifier', [s20 '/TempQualifier'], 'Position', [200 30 320 110], ...
    'min_value', '-400', 'max_value', '1250', 'debounce_ms', '40');
add_block('simulink/Sources/Constant', [s20 '/NoSourceQlf'], 'Position', [80 90 140 110], ...
    'Value', 'QLF_OK', 'OutDataTypeStr', 'uint8');
add_line(s20, 'Temp_dC/1', 'TempQualifier/1', 'autorouting', 'on');
add_line(s20, 'NoSourceQlf/1', 'TempQualifier/2', 'autorouting', 'on');
add_line(s20, 'TempQualifier/1', 'TempQlf/1', 'autorouting', 'on');
end
