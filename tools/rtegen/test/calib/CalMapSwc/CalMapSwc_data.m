% SPDX-License-Identifier: Apache-2.0
% Default values of CalMapSwc (RTE-26 test profile), read by tools/swc/mdata.py - no MATLAB needed.
% A map is a matrix with one row per X breakpoint; own breakpoints stand under <Name>_X / <Name>_Y.
SpeedAxis_rpm = [0 1000 2000 4000 6000];            % [rpm] shared axis, strictly ascending
TorqueLimit_Nm_Y = [-20 25 90];                     % [degC] own Y breakpoints of TorqueLimit_Nm
TorqueLimit_Nm = [120 110 90; ...                   % [Nm] 0 rpm
                  150 140 120; ...                  %      1000 rpm
                  200 180 150; ...                  %      2000 rpm
                  180 170 140; ...                  %      4000 rpm
                  150 140 120];                     %      6000 rpm
BoostCurve = [0 400 900 1500 1800];                 % [mbar] over SpeedAxis_rpm
DerateCurve_X = [-40 60 100 150];                   % [degC] own breakpoints
DerateCurve = [100 100 80 50];                      % [%]
MaxTorque_Nm = 250;                                 % [Nm]
Mode = 1;                                           % Eco
Offsets_Nm = [-5 0 5];                              % [Nm]
Gain = 0.75;                                        % [-]
