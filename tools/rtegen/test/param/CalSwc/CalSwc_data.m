% Calibration values of the synthetic component CalSwc (tools/rtegen RTE-03 host test).
% Written in the style of the old MBDT data files: comments, struct groups, hex2dec, continuation lines.
clear;
run('SomethingElse.m');          % not an assignment: ignored

cal.gain = hex2dec('3');          % [-]
Gain = cal.gain;                  % the parameter name refers to the group value
Table = [10, 20, ...
         30 40];                  % [-] four values
Factor = 1.5;                     % physical value, stored as 1500 (lsb 0.001)
Temp = 25;                        % [degC], stored as 650 (lsb 0.1, bias -40)
Alpha = 0.125;                    % float32
Enable = true;
Unused = 'text';                  % not understood, harmless while no parameter points at it
