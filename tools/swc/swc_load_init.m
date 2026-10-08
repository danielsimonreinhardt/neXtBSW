% SPDX-License-Identifier: Apache-2.0
function file = swc_load_init(mdl, swcDir)
%SWC_LOAD_INIT Runs <swcDir>/<mdl>_init.m in the base workspace, if the component has one (TOOL-135).
%   An imported model often takes constants from variables of the MATLAB workspace that a setup script of its old
%   project created (e.g. Constant 'hvsctrl.hvsopmode_on'). Without them it neither compiles nor generates code.
%   The component carries that script as <Swc>_init.m next to its model; swc_ports, swc_prepare and swc_build run
%   it before they load the model. Calibration parameters of the RTE stay in <Swc>_data.m (swc_load_data).
%   Before it, <swcDir>/<mdl>_types.m runs if it exists: the named data types of swc.yaml (RTE-15).
%   Returns the path of the script, '' when there is none.

% RTE-15: the named data types of swc.yaml (<Swc>_types.m, written by swc_create / swc_yaml.py types) come first -
% the model and an _init.m may use them
types = fullfile(char(swcDir), [char(mdl) '_types.m']);
if isfile(types)
    fprintf('running %s\n', types);
    run(types);
end
file = fullfile(char(swcDir), [char(mdl) '_init.m']);
if ~isfile(file)
    file = '';
    return;
end
fprintf('running %s\n', file);
evalin('base', sprintf('run(''%s'');', strrep(file, '''', '''''')));
end
