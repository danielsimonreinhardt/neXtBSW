% SPDX-License-Identifier: Apache-2.0
function file = RateImportSwc_model(folder)
%RATEIMPORTSWC_MODEL Writes the rate-based test model <folder>/RateImportSwc.slx (TOOL-184 test data).
%   An ordinary Simulink model as it comes from an old project - no swc.yaml, no AUTOSAR component, target grt.tlc,
%   Variable-step solver, no function-call inports: the rates of its ports are its runnables (the normal case of a
%   model the Studio imports, studio/core/slximport.py). Two rates, so the import makes two runnables:
%     10 ms:  Speed_rpm (uint16) -> Gain 2 -> SpeedOut_rpm
%     100 ms: Temp_dC (int16) -> Unit Delay -> TempOut_dC
%   Invented names, no board signal. The model is built by this script instead of being checked in as .slx, so a
%   review can read what it contains.

mdl = 'RateImportSwc';
if bdIsLoaded(mdl)
    close_system(mdl, 0);
end
file = fullfile(folder, [mdl '.slx']);
if isfile(file)
    delete(file);
end
new_system(mdl);
closer = onCleanup(@() close_system(mdl, 0));
set_param(mdl, 'SolverType', 'Variable-step', 'SystemTargetFile', 'grt.tlc', 'StopTime', '10');
add_block('simulink/Sources/In1', [mdl '/Speed_rpm'], 'Position', [30 40 60 54], ...
    'OutDataTypeStr', 'uint16', 'SampleTime', '0.01', 'PortDimensions', '1');
add_block('simulink/Math Operations/Gain', [mdl '/SpeedGain'], 'Position', [150 35 190 60], 'Gain', '2', ...
    'OutDataTypeStr', 'uint16', 'SaturateOnIntegerOverflow', 'on');
add_block('simulink/Sinks/Out1', [mdl '/SpeedOut_rpm'], 'Position', [300 40 330 54], ...
    'OutDataTypeStr', 'uint16', 'PortDimensions', '1');
add_line(mdl, 'Speed_rpm/1', 'SpeedGain/1', 'autorouting', 'on');
add_line(mdl, 'SpeedGain/1', 'SpeedOut_rpm/1', 'autorouting', 'on');
add_block('simulink/Sources/In1', [mdl '/Temp_dC'], 'Position', [30 120 60 134], ...
    'OutDataTypeStr', 'int16', 'SampleTime', '0.1', 'PortDimensions', '1');
add_block('simulink/Discrete/Unit Delay', [mdl '/TempDelay'], 'Position', [150 115 190 140]);
add_block('simulink/Sinks/Out1', [mdl '/TempOut_dC'], 'Position', [300 120 330 134], ...
    'OutDataTypeStr', 'int16', 'PortDimensions', '1');
add_line(mdl, 'Temp_dC/1', 'TempDelay/1', 'autorouting', 'on');
add_line(mdl, 'TempDelay/1', 'TempOut_dC/1', 'autorouting', 'on');
save_system(mdl, file);
end
