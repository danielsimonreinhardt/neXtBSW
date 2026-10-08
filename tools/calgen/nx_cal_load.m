% SPDX-License-Identifier: Apache-2.0
function values = nx_cal_load(target, varargin)
%NX_CAL_LOAD Loads a calibration dataset (nextbsw-cal 1, tools/calgen) into MATLAB (TOOL-145, stage K2).
%   values = nx_cal_load('projects/p/project.yaml') reads the base dataset of the project (projects/p/calibration)
%   with the same reader and the same checks as the build (tools/calgen/calgen.py values, via Python - the dataset is
%   YAML, MATLAB has no reader for it) and assigns every calibration parameter as a variable of the base workspace,
%   named and shaped as in <Swc>_data.m: a scalar, a row vector, a map as a matrix with one row per X breakpoint.
%   Values are physical; a value with a value table is its stored number.
%
%   Options (name/value):
%     'Variant'    name of a variant (base + delta); default: build_variant of dataset.yaml, else base
%     'Component'  only this component (needed when two components have a parameter of the same name)
%     'Model'      name of a loaded model: sets its model arguments (Simulink.Parameter in the model workspace) like
%                  tools/swc/swc_load_data.m instead of the base workspace; the component is the model name. A
%                  model argument keeps its form: a map is the matrix ny x nx of the model (rows over Y = 1st
%                  lookup input, columns over X = 2nd input, TOOL-193), filled column by column from the dataset
%                  order (one row per X) - reshape(flat, size(p.Value)); a map of another form is refused (TOOL-196)
%     'Workspace'  'base' (default) or 'none' (only return the values)
%     'Dataset'    another dataset folder than calibration/ next to the target
%   target may also be an ECU description rte.yaml (dataset calibration/ next to it, e.g. a test profile).
%
%   Returns a struct: component -> parameter -> value.
%
%   Status (TOOL-145): the base-workspace path was run in MATLAB R2024a (matlab -batch, 2026-09-28, profile
%   tools/rtegen/test/calib, variant sport: map 5x3, scalar, value table, float, 'Workspace' 'none'). The 'Model'
%   option was run in MATLAB R2024a on a probe model (TOOL-196, 2026-09-30, same profile, base dataset): map as
%   matrix 3 x 5 = ny x nx (column 3 = X 2000 rpm), row and column vectors keep their form, a map argument 5 x 3 is
%   refused (nx_cal_load:shape); not yet from the init file section nx:calibration of a real component (the model
%   arguments are only there after the model is loaded, TOOL-193). The JSON it reads is tested by
%   tools/calgen/test_calgen.py.

opts = struct('Variant', '', 'Component', '', 'Model', '', 'Workspace', 'base', 'Dataset', '');
for k = 1:2:numel(varargin)
    name = varargin{k};
    if ~isfield(opts, name)
        error('nx_cal_load:option', 'unknown option %s', name);
    end
    opts.(name) = varargin{k + 1};
end
if ~isempty(opts.Model) && isempty(opts.Component)
    opts.Component = opts.Model;
end

here = fileparts(mfilename('fullpath'));
repo = fileparts(fileparts(here));
if exist('swc_python', 'file') ~= 2
    addpath(fullfile(repo, 'tools', 'swc'));
end
target = char(java.io.File(char(target)).getCanonicalPath());
jsonFile = [tempname '.json'];
cleanup = onCleanup(@() deleteIfExists(jsonFile));
setenv('PYTHONUTF8', '1');
cmd = sprintf('"%s" "%s" values "%s" --json -o "%s"', swc_python(repo), fullfile(here, 'calgen.py'), target, jsonFile);
if ~isempty(opts.Variant)
    cmd = sprintf('%s --variant "%s"', cmd, opts.Variant);
end
if ~isempty(opts.Dataset)
    cmd = sprintf('%s --dataset "%s"', cmd, char(opts.Dataset));
end
[status, output] = system([cmd ' 2>&1']);
if status ~= 0
    error('nx_cal_load:read', 'cannot load the calibration dataset of %s:\n%s', target, strtrim(output));
end
data = jsondecode(fileread(jsonFile));

values = struct();
components = fieldnames(data.components);
if ~isempty(opts.Component)
    if ~ismember(opts.Component, components)
        error('nx_cal_load:component', 'component %s is not in the dataset (%s)', opts.Component, ...
            strjoin(components', ', '));
    end
    components = {opts.Component};
end
assigned = {};
for c = 1:numel(components)
    comp = components{c};
    params = data.components.(comp);
    names = fieldnames(params);
    for k = 1:numel(names)
        p = params.(names{k});
        flat = reshape(double(p.value), 1, []);          % row by row (a map: one row per X breakpoint)
        shape = reshape(double(p.shape), 1, []);
        if numel(shape) == 2
            value = reshape(flat, shape(2), shape(1)).';  % nx rows of ny values, as in <Swc>_data.m
        else
            value = flat;
        end
        values.(comp).(names{k}) = value;
        if ~isempty(opts.Model)
            setModelArgument(opts.Model, names{k}, flat, shape);
        elseif strcmp(opts.Workspace, 'base')
            if ismember(names{k}, assigned)
                error('nx_cal_load:clash', ['%s is a parameter of two components; give ''Component'' to load ' ...
                    'one of them'], names{k});
            end
            assignin('base', names{k}, value);
            assigned{end + 1} = names{k}; %#ok<AGROW>
        end
    end
end
end

function setModelArgument(mdl, name, flat, shape)
% like tools/swc/swc_load_data.m: the value of the model argument, its data type and its form stay. flat is the
% dataset order (a map: one row per X, Y running fastest); MATLAB keeps a matrix column by column, so a map (model
% matrix ny x nx, tools/swc/swc_params.m mapLayout) is reshape(flat, [ny nx]) - reshape(flat, size(p.Value)) keeps
% every form the model gives (row or column vector, matrix) (TOOL-196).
mw = get_param(mdl, 'ModelWorkspace');
if ~hasVariable(mw, name)
    return;                                  % a parameter the model does not use as model argument
end
p = getVariable(mw, name);
if ~isa(p, 'Simulink.Parameter')
    error('nx_cal_load:type', '%s: model argument %s is no Simulink.Parameter', mdl, name);
end
if numel(p.Value) ~= numel(flat)
    error('nx_cal_load:dim', '%s: %s has %d value(s) in the dataset, the model expects %d', mdl, name, ...
        numel(flat), numel(p.Value));
end
if numel(shape) == 2 && ~isequal(size(p.Value), [shape(2) shape(1)])
    error('nx_cal_load:shape', ['%s: model argument %s is %s, the map of the dataset needs the matrix %d x %d ' ...
        '(ny x nx: rows over Y = 1st lookup input, columns over X = 2nd input, swc/README.md)'], mdl, name, ...
        mat2str(size(p.Value)), shape(2), shape(1));
end
p.Value = reshape(flat, size(p.Value));
assignin(mw, name, p);
end

function deleteIfExists(f)
if isfile(f)
    delete(f);
end
end
