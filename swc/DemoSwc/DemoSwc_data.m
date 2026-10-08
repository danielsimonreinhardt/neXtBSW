% Calibration values of DemoSwc (RTE-03). The only place of these numbers: the model describes the parameters as
% elements of the ParameterReceiver port CalPrm (type, dimension), swc_build/swc_mil_test load this file into the
% model workspace (tools/swc/swc_load_data.m), and tools/rtegen puts the same values into the RTE parameter
% storage in RAM (Rte_Prm_DemoSwc_CalPrm_<name>_data), where a calibration tool may change them (XCP, TOOL-01).
% Read without MATLAB by tools/swc/mdata.py: plain assignments, see there for the forms it understands.

FilterAlpha = 0.125;   % [-] weight of the NTC low-pass in Run100ms: y = y + (u - y) * FilterAlpha (float32)
