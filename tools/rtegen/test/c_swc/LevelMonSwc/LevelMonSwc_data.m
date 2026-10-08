% SPDX-License-Identifier: Apache-2.0
% Calibration values of LevelMonSwc (RTE-20 test profile), read by tools/swc/mdata.py - no MATLAB needed.
OnLevel_mV = 3000;        % [mV] Above goes on at or above this filtered level (after DebounceCycles cycles)
OffLevel_mV = 2500;       % [mV] Above goes off at or below this level (hysteresis)
MaxLevel_mV = 4800;       % [mV] above it the level is implausible: LevelQlf = QLF_ERROR_RANGE
DebounceCycles = 3;       % [-] 10 ms cycles at or above OnLevel_mV before Above goes on
Weights = [4 2 1 1];      % [-] moving average weights, newest sample first
Gain = 1.5;               % [-] Scaled = Filtered_mV * Gain (fixed point, stored 1500)
