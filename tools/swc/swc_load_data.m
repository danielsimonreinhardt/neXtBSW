% SPDX-License-Identifier: Apache-2.0
function values = swc_load_data(mdl, swcDir)
%SWC_LOAD_DATA Puts the calibration values of <Swc>_data.m into the model workspace (RTE-03).
%   swc_load_data('DemoSwc', 'swc/DemoSwc') reads swc/DemoSwc/DemoSwc_data.m with the same reader as the RTE
%   generator (tools/swc/mdata.py, via Python) and sets the value of every model argument of the loaded model
%   (the parameters of the ParameterReceiver port, swc_map_parameters) to it; the data type of the parameter stays.
%   So simulation (MIL), code generation (swc_build) and the RTE (tools/rtegen) use the one data file and never a
%   number stored in the .slx. A model without model arguments needs no data file.
%   Returns the decoded values (struct: name -> row vector) or [] when there is nothing to load.

values = [];
swcDir = char(java.io.File(char(swcDir)).getCanonicalPath());
args = strtrim(strsplit(get_param(mdl, 'ParameterArgumentNames'), ','));
args = args(~cellfun(@isempty, args));
if isempty(args)
    return;
end
dataFile = fullfile(swcDir, [mdl '_data.m']);
if ~isfile(dataFile)
    error('swc_load_data:noFile', '%s has calibration parameters (%s) but no data file %s', ...
        mdl, strjoin(args, ', '), dataFile);
end
repo = fileparts(fileparts(fileparts(mfilename('fullpath'))));
jsonFile = [tempname '.json'];
cleanup = onCleanup(@() deleteIfExists(jsonFile));
setenv('PYTHONUTF8', '1');
cmd = sprintf('"%s" "%s" "%s" --json -o "%s" 2>&1', swc_python(repo), ...
    fullfile(repo, 'tools', 'swc', 'mdata.py'), dataFile, jsonFile);
[status, output] = system(cmd);
if status ~= 0
    error('swc_load_data:read', 'cannot read %s:\n%s', dataFile, strtrim(output));
end
values = jsondecode(fileread(jsonFile));
mw = get_param(mdl, 'ModelWorkspace');
for k = 1:numel(args)
    name = args{k};
    if ~isfield(values, name)
        error('swc_load_data:missing', '%s: %s is not set in %s', mdl, name, dataFile);
    end
    value = reshape(double(values.(name)), 1, []);
    p = getVariable(mw, name);
    if ~isa(p, 'Simulink.Parameter')
        error('swc_load_data:type', '%s: model argument %s is no Simulink.Parameter', mdl, name);
    end
    if numel(p.Value) ~= numel(value)
        error('swc_load_data:dim', '%s: %s has %d value(s) in %s, the model expects %d', mdl, name, ...
            numel(value), dataFile, numel(p.Value));
    end
    if ~isvector(p.Value) && ~isscalar(p.Value)
        % TOOL-193: a map is a 2-D model argument ny x nx (rows Y, columns X, swc_params.m); <Swc>_data.m (and
        % mdata.py) give it row by row, one row per X breakpoint - which is the column by column order of the model
        % matrix, so a plain reshape puts every value where the generated code and the RTE expect it
        value = reshape(value, size(p.Value));
    end
    p.Value = value;
    assignin(mw, name, p);
end
end

function deleteIfExists(f)
if isfile(f)
    delete(f);
end
end
