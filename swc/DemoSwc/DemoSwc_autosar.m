function DemoSwc_autosar()
%DEMOSWC_AUTOSAR Maps DemoSwc.slx to an AUTOSAR Classic software component (one-time setup).
%   Default mapping of autosar.api.create (port = interface = data element name, runnables from the
%   function-call inports, timing events from their sample times), with explicit RTE access so the
%   generated code calls Rte_Read_<port>_<element>() / Rte_Write_<port>_<element>().

mdl  = 'DemoSwc';
here = fileparts(mfilename('fullpath'));
addpath(here);
load_system(mdl);

set_param(mdl, 'SystemTargetFile', 'autosar.tlc');
autosar.api.create(mdl, 'default');

slMap = autosar.api.getSimulinkMapping(mdl);
for p = {'Button1', 'MotorNtc_mV', 'PcCommandQlf'}
    [port, element] = getInport(slMap, p{1});
    mapInport(slMap, p{1}, port, element, 'ExplicitReceive');
end
for p = {'Led1', 'Led2', 'MotorNtcFiltered_mV'}
    [port, element] = getOutport(slMap, p{1});
    mapOutport(slMap, p{1}, port, element, 'ExplicitSend');
end
% calibration parameters (RTE-03): ParameterReceiver port CalPrm, values from DemoSwc_data.m
addpath(fullfile(fileparts(fileparts(here)), 'tools', 'swc'));
swc_map_parameters(mdl, {'FilterAlpha'}, 'CalPrm');
swc_load_data(mdl, here);

applyCodeSettings(mdl);
save_system(mdl, fullfile(here, [mdl '.slx']));
fprintf('AUTOSAR mapping saved for %s\n', mdl);
end

function applyCodeSettings(mdl)
set_param(mdl, 'GenCodeOnly', 'on');
set_param(mdl, 'GenerateMakefile', 'off');
set_param(mdl, 'GenerateReport', 'off');
set_param(mdl, 'ProdHWDeviceType', 'ARM Compatible->ARM Cortex-M');
set_param(mdl, 'ProdLongLongMode', 'on');
set_param(mdl, 'SupportNonFinite', 'off');
set_param(mdl, 'MatFileLogging', 'off');
set_param(mdl, 'GenerateComments', 'on');
set_param(mdl, 'MaxIdLength', '63');
end
