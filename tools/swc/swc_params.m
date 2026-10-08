% SPDX-License-Identifier: Apache-2.0
function out = swc_params(target, varargin)
%SWC_PARAMS Writes the calibration parameters of swc.yaml into an existing model, and lists the ones it could have
%   (TOOL-146, stage K5 of doc/architecture/kalibrierung.md). The parameter counterpart of swc_ports: until now only
%   swc_create gave a model its parameters (swc_map_parameters); an imported model and a parameter defined later in
%   swc.yaml (Studio or by hand) never reached the model.
%
%   swc_params('swc/Imported')
%       Loads swc/Imported/Imported.slx (after <Swc>_init.m and the QLF_* constants), reads swc/Imported/swc.yaml
%       (swc_read_spec) and writes every entry of `parameters:` into the model, then saves and closes it. The
%       model must be an AUTOSAR component already (swc_prepare / swc_create).
%
%   changes = swc_params(mdl, spec, swcDir)
%       The same on the loaded model mdl with the decoded swc.yaml (swc_prepare, import of a model); saving is the
%       caller's. Returns one text per change.
%
%   list = swc_params(mdl, 'report')
%       Simulink.Parameter objects (and Simulink.LookupTable/Breakpoint objects) the loaded model uses, from the
%       model or the base workspace (swc_ports 'report' adds them to its report: the import proposes them as
%       calibration parameters). Nothing is changed.
%
%   What writing a parameter does (only what differs is changed, each change printed as "params: ..."):
%     - the model workspace gets a Simulink.Parameter of that name: the existing one of the model workspace, a copy
%       of the one in the base workspace (an imported model takes it from its init script - the model workspace
%       then shadows it, the init stays untouched), a plain number of either workspace turned into one, or a new
%       one with zeros (a parameter defined in swc.yaml first). The value keeps its numbers; its number of
%       elements must be dim of swc.yaml.
%     - data type as swc.yaml (simulink_type, fixdt(...) for lsb/offset), storage class Auto (a model argument
%       has no storage class of its own)
%     - model argument, ParameterReceiver port CalPrm and mapping PortParameter (swc_map_parameters)
%     - the values of <Swc>_data.m are put in (swc_load_data), so a missing or wrong value stops here and not only
%       in swc_build. Embedded Coder then generates Rte_Prm_CalPrm_<Name>() and tools/rtegen keeps the value.
%   TOOL-193 (stage K5 rest):
%     - a map (kind map, nx X and ny Y breakpoints) is a 2-D model argument of ny x nx: rows over Y = first input
%       of the n-D Lookup Table (BreakpointsForDimension1), columns over X = second input. With the array layout
%       Column-major (required) its memory order is that of <Swc>_data.m (one row per X), the RTE and the A2L,
%       and Embedded Coder writes a flat array of nx*ny values into the ARXML. A map an older swc_params wrote as
%       vector becomes that matrix.
%     - a Simulink.LookupTable object (explicit breakpoints) that swc.yaml gives as curve/map with own axes is
%       dissolved: Simulink.Parameter <Name> (table) and <Name>_X/_Y (breakpoints), its lookup blocks switched to
%       "Table and breakpoints". The object itself would become a record in the ARXML (not read by tools/rtegen).
%     - a fixed-point data type (fixdt) comes from lsb/offset of swc.yaml; the ARXML carries it as COMPU-METHOD.
%     - the report gives the resolved fixed-point scaling, the roles of an object in its lookup blocks and the
%       table and breakpoints of a lookup object.

narginchk(1, 3);
if nargin == 2 && (ischar(varargin{1}) || isstring(varargin{1})) && strcmpi(varargin{1}, 'report')
    out = reportParameters(char(target));
    return;
end
if nargin == 3
    out = writeParameters(char(target), varargin{1}, char(varargin{2}));
    return;
end
out = folderMode(char(target));
end

%% ======================================================================================  folder mode

function changes = folderMode(swcDir)
repo = fileparts(fileparts(fileparts(mfilename('fullpath'))));
if ~java.io.File(swcDir).isAbsolute()
    swcDir = fullfile(pwd, swcDir);
end
swcDir = char(java.io.File(swcDir).getCanonicalPath());
[~, mdl] = fileparts(swcDir);
mdlFile = fullfile(swcDir, [mdl '.slx']);
if ~isfile(mdlFile)
    error('swc_params:noModel', ['%s not found. Parameters are written into an existing model; a new ' ...
        'component gets them from swc_create.'], mdlFile);
end
step('yaml');
spec = swc_read_spec(swcDir);
if ~strcmp(spec.name, mdl)
    error('swc_params:name', 'swc.yaml names component %s, the model is %s', spec.name, mdl);
end
addpath(fullfile(repo, 'swc', 'template'));
addpath(swcDir);
step('load');
closeIfLoaded(mdl);
try
    swc_load_qlf();
catch err
    fprintf('qualifier constants not loaded (%s)\n', err.message);
end
swc_load_init(mdl, swcDir);
load_system(mdlFile);
closer = onCleanup(@() closeIfLoaded(mdl));
if ~strcmp(get_param(mdl, 'SystemTargetFile'), 'autosar.tlc')
    error('swc_params:notAutosar', ['model %s is no AUTOSAR component (system target file %s): prepare it ' ...
        'first (swc_prepare, the Studio''s model import)'], mdl, get_param(mdl, 'SystemTargetFile'));
end
step('write');
changes = writeParameters(mdl, spec, swcDir);
step('save');
save_system(mdl, mdlFile);
close_system(mdl, 0);
clear closer;
fprintf('wrote %d calibration parameters into %s (%d changes)\n', numel(toCell(fieldOr(spec, 'parameters'))), ...
    mdlFile, numel(changes));
end

%% ======================================================================================  write

function changes = writeParameters(mdl, spec, swcDir)
changes = {};
params = toCell(fieldOr(spec, 'parameters'));
if isempty(params)
    return;
end
port = 'CalPrm';
if isfield(spec, 'parameter_port') && ~isempty(spec.parameter_port)
    port = spec.parameter_port;
end
mw = get_param(mdl, 'ModelWorkspace');
if ~any(strcmp(mw.DataSource, {'Model File', 'Model file'}))
    error('swc_params:source', ['the model workspace of %s is loaded from %s (%s): parameters written into it ' ...
        'would be lost on the next load - set its data source to "Model File" in the Model Explorer'], ...
        mdl, mw.DataSource, char(mw.FileName));
end
dissolveLookupObjects(mdl, mw, params, @record);   % TOOL-193: Simulink.LookupTable -> table + breakpoints
names = cell(1, numel(params));
hasMap = false;
for k = 1:numel(params)
    p = params{k};
    name = p.name;
    names{k} = name;
    wanted = double(p.dim);
    shape = [1 wanted];
    if isfield(p, 'kind') && strcmp(p.kind, 'map') && isfield(p, 'shape') && numel(p.shape) == 2
        % TOOL-193: a map is a 2-D model argument ny x nx - rows over Y (first input of the n-D Lookup Table,
        % BreakpointsForDimension1), columns over X (second input). Simulink keeps a matrix column by column, so
        % its memory order is X-major with Y running fastest: exactly the order of <Swc>_data.m (one row per X),
        % of the RTE's initialiser and of the A2L (mapLayout).
        shape = fliplr(reshape(double(p.shape), 1, 2));
        hasMap = true;
    end
    [obj, origin] = parameterOf(mdl, mw, name);
    if isempty(obj)
        obj = Simulink.Parameter(zeros(shape));
        origin = 'new';
    end
    value = obj.Value;
    if ~(isnumeric(value) || islogical(value)) || isempty(value)
        error('swc_params:value', '%s: parameter %s has no numeric value (%s) - give it one in the model', ...
            mdl, name, class(value));
    end
    if ~ismatrix(value)
        error('swc_params:matrix', '%s: parameter %s has %d dimensions; a calibration parameter has at most two', ...
            mdl, name, ndims(value));
    end
    if numel(value) ~= wanted
        if strcmp(origin, 'new')
            value = zeros(shape);
        else
            error('swc_params:dim', ['%s: parameter %s has %d value(s) in the model (%s), swc.yaml gives dim %d - ' ...
                'align the model or swc.yaml'], mdl, name, numel(value), origin, wanted);
        end
    end
    if shape(1) > 1
        if isvector(value)
            % a map written as a vector of nx*ny values (TOOL-146): in the order of <Swc>_data.m, so the column
            % by column reshape gives the ny x nx matrix
            record(sprintf('%s: vector of %d values -> %dx%d matrix (map: rows Y, columns X)', name, ...
                numel(value), shape(1), shape(2)));
            value = reshape(value, shape);
        elseif ~isequal(size(value), shape)
            error('swc_params:shape', ['%s: parameter %s is a %s matrix in the model (%s); swc.yaml gives the map ' ...
                'with %d X and %d Y breakpoints, which the model holds as %d x %d matrix (rows: Y = first input ' ...
                'of the lookup block, columns: X = second input) - swap the axes x and y of swc.yaml or ' ...
                'align the model'], mdl, name, mat2str(size(value)), origin, shape(2), shape(1), shape(1), shape(2));
        end
        value = double(value);
    else
        if ~isvector(value) && ~isscalar(value)
            error('swc_params:matrix', ['%s: parameter %s is a %s matrix in the model; swc.yaml gives it as %s ' ...
                '(only a map is a matrix: kind map with axes x and y)'], mdl, name, mat2str(size(value)), ...
                kindOf(p));
        end
        value = reshape(double(value), 1, []);
    end
    changed = ~strcmp(origin, 'model');
    if ~isequal(obj.Value, value) || ~strcmp(class(obj.Value), 'double')
        obj.Value = value;
        changed = true;
    end
    if ~strcmp(obj.DataType, p.simulink_type)
        record(sprintf('%s: data type %s -> %s', name, obj.DataType, p.simulink_type));
        obj.DataType = p.simulink_type;
        changed = true;
    end
    if ~strcmp(obj.StorageClass, 'Auto')
        record(sprintf('%s: storage class %s -> Auto (model argument)', name, obj.StorageClass));
        obj.StorageClass = 'Auto';
        changed = true;
    end
    if changed || ~hasVariable(mw, name)
        assignin(mw, name, obj);
    end
    switch origin
        case 'new'
            record(sprintf('%s: new Simulink.Parameter (%s, %d value(s)) in the model workspace', name, ...
                p.simulink_type, wanted));
        case 'base'
            record(sprintf(['%s: Simulink.Parameter of the base workspace (init script) copied into the model ' ...
                'workspace'], name));
        case 'base value'
            record(sprintf('%s: number of the base workspace (init script) as Simulink.Parameter into the model workspace', name));
        case 'model value'
            record(sprintf('%s: number of the model workspace turned into a Simulink.Parameter', name));
    end
end
if hasMap
    mapLayout(mdl);
end
args = strtrim(strsplit(get_param(mdl, 'ParameterArgumentNames'), ','));
args = args(~cellfun(@isempty, args));
missing = names(~ismember(names, args));
swc_map_parameters(mdl, names, port);
if ~isempty(missing)
    record(sprintf('model arguments mapped to %s (PortParameter): %s', port, strjoin(missing, ', ')));
end
if isfile(fullfile(swcDir, [mdl '_data.m']))
    swc_load_data(mdl, swcDir);           % the values of <Swc>_data.m; a missing one is an error here already
else
    fprintf(['params: %s_data.m missing next to the model - the values of the calibration parameters stand only ' ...
        'there (swc_build and tools/rtegen need it)\n'], mdl);
end

    function record(text)
        changes{end + 1} = text;
        fprintf('params: %s\n', text);
    end
end

function text = kindOf(p)
text = 'value/array';
if isfield(p, 'kind') && ~isempty(p.kind)
    text = char(p.kind);
end
end

function dissolveLookupObjects(mdl, mw, params, record)
% TOOL-193: a calibration parameter whose object is a Simulink.LookupTable (explicit breakpoints) becomes its parts:
% the table as Simulink.Parameter <Name> (nx x ny for a map), the breakpoints as <Name>_X / <Name>_Y - the own axes
% of swc.yaml, elements of the port CalPrm like every other parameter (RTE-26). The lookup blocks that took the
% object ("Lookup table object") take table and breakpoints by these names instead ("Table and breakpoints").
% Why not the object itself as model argument: Embedded Coder makes a record of it (struct type, header Rte_Type.h),
% which tools/rtegen does not read as a parameter (scalars and arrays only), and the storage class of a lookup object
% in the model workspace is reset to Auto on load (measured R2024a, TOOL-193 - like TOOL-185 for Simulink.Signal).
byName = containers.Map();
for k = 1:numel(params)
    byName(params{k}.name) = params{k};
end
for k = 1:numel(params)
    p = params{k};
    if ~isfield(p, 'kind') || ~any(strcmp(p.kind, {'curve', 'map'}))
        continue;
    end
    [lut, where] = objectOf(mw, p.name);
    if ~isa(lut, 'Simulink.LookupTable')
        continue;
    end
    if ~strcmp(lut.BreakpointsSpecification, 'Explicit values')
        error('swc_params:lookup', ['%s: Simulink.LookupTable %s has breakpoints "%s" - only explicit values are ' ...
            'dissolved into table and breakpoints (TOOL-193); give the object explicit breakpoints'], mdl, p.name, ...
            lut.BreakpointsSpecification);
    end
    axes = toCell(fieldOr(p, 'axes'));
    if numel(axes) ~= numel(lut.Breakpoints)
        error('swc_params:lookup', ['%s: Simulink.LookupTable %s has %d breakpoint set(s), swc.yaml gives %d axis ' ...
            '(axes)'], mdl, p.name, numel(lut.Breakpoints), numel(axes));
    end
    % breakpoint set d of the object belongs to dimension d of the lookup block; a map's X axis is dimension 2
    % (columns), its Y axis dimension 1 (rows) - see mapLayout
    order = 1:numel(axes);
    if numel(axes) == 2
        order = [2 1];
    end
    axisOf = axes(order);                  % axisOf{d}: swc.yaml axis of breakpoint set d
    for a = 1:numel(axes)
        axisName = char(axisOf{a});
        if ~isKey(byName, axisName)
            error('swc_params:lookup', '%s: axis %s of %s is no parameter of swc.yaml', mdl, axisName, p.name);
        end
        bp = lut.Breakpoints(a);
        obj = Simulink.Parameter(reshape(double(bp.Value), 1, []));
        obj.DataType = byName(axisName).simulink_type;
        obj.Unit = bp.Unit;
        obj.Description = propOr(bp, 'Description');
        assignin(mw, axisName, obj);
    end
    t = lut.Table;
    value = double(t.Value);
    if isvector(value) && numel(axes) == 1
        value = reshape(value, 1, []);
    end
    obj = Simulink.Parameter(value);
    obj.DataType = p.simulink_type;
    obj.Unit = t.Unit;
    obj.Description = propOr(lut.Table, 'Description');
    assignin(mw, p.name, obj);            % in the model workspace it shadows a lookup object of the init script
    blocks = find_system(mdl, 'LookUnderMasks', 'all', 'FollowLinks', 'on', 'BlockType', 'Lookup_n-D');
    switched = {};
    for b = 1:numel(blocks)
        blk = blocks{b};
        if ~strcmp(get_param(blk, 'DataSpecification'), 'Lookup table object') || ...
                ~strcmp(strtrim(get_param(blk, 'LookupTableObject')), p.name)
            continue;
        end
        if ~strcmp(get_param(blk, 'LinkStatus'), 'none') && ~strcmp(get_param(blk, 'LinkStatus'), 'inactive')
            error('swc_params:lookup', ['%s: block %s takes the lookup object %s but lies in a library link - ' ...
                'change it in the library'], mdl, blk, p.name);
        end
        set_param(blk, 'DataSpecification', 'Table and breakpoints', 'Table', p.name);
        for a = 1:numel(axes)
            set_param(blk, sprintf('BreakpointsForDimension%d', a), char(axisOf{a}));
        end
        switched{end + 1} = strrep(blk, newline, ' '); %#ok<AGROW>
    end
    record(sprintf(['%s: Simulink.LookupTable (%s workspace) dissolved into the parameters %s, %s; lookup ' ...
        'block(s) %s take table and breakpoints'], p.name, where, p.name, strjoin(cellfun(@char, axes, ...
        'UniformOutput', false), ', '), strjoin(switched, ', ')));
end
end

function [value, where] = objectOf(mw, name)
value = [];
where = '';
if hasVariable(mw, name)
    value = getVariable(mw, name);
    where = 'model';
elseif evalin('base', sprintf('exist(''%s'', ''var'')', name)) == 1
    value = evalin('base', name);
    where = 'base';
end
end

function mapLayout(mdl)
% TOOL-193: the order of a map in memory. <Swc>_data.m, tools/rtegen (the RTE's initialiser) and the A2L
% (FNC_VALUES COLUMN_DIR, a2lgen, TOOL-196) keep a map as one block per X breakpoint, the Y index running fastest.
% The model holds the map as ny x nx matrix (rows Y, columns X); with the default array layout Column-major its
% memory order is the same, and the 2-D lookup code indexes table[ix * ny + iy] (measured R2024a: look2_* with
% stride = rows). Row-major would need the transposed matrix and makes Embedded Coder write the parameter type as an
% array of arrays, which tools/rtegen refuses ("arrays of arrays (matrices) are not supported", measured R2024a).
if ~strcmp(get_param(mdl, 'ArrayLayout'), 'Column-major')
    error('swc_params:layout', ['%s: array layout %s - a map as model argument needs the array layout ' ...
        'Column-major (Configuration Parameters, Math and Data Types): its memory order must be that of the RTE ' ...
        'and the A2L, and tools/rtegen reads no array of arrays'], mdl, get_param(mdl, 'ArrayLayout'));
end
end

function [obj, origin] = parameterOf(mdl, mw, name)
% The parameter of that name the model sees: model workspace first (it shadows the base workspace), then base.
obj = [];
origin = '';
if hasVariable(mw, name)
    value = getVariable(mw, name);
    where = 'model';
elseif evalin('base', sprintf('exist(''%s'', ''var'')', name)) == 1
    value = evalin('base', name);
    where = 'base';
else
    return;
end
if isa(value, 'Simulink.Parameter') && ~isa(value, 'Simulink.LookupTable') && ~isa(value, 'Simulink.Breakpoint')
    obj = value;
    if strcmp(where, 'base')
        obj = copy(value);                % never change the object of the init script
    end
    origin = where;
elseif isnumeric(value) || islogical(value)
    obj = Simulink.Parameter(double(value));
    origin = [where ' value'];
else
    error('swc_params:type', ['%s: %s of the %s workspace is a %s, no Simulink.Parameter - a calibration ' ...
        'parameter needs a Simulink.Parameter or a number of that name'], mdl, name, where, class(value));
end
end

%% ======================================================================================  report

function list = reportParameters(mdl)
% Parameter objects the model uses: Simulink.findVars on the compiled model; when it does not compile, the
% objects of the model and base workspace whose name stands in a block parameter of the model.
list = {};
args = strtrim(strsplit(get_param(mdl, 'ParameterArgumentNames'), ','));
entries = struct('name', {}, 'workspace', {}, 'users', {});
try
    vars = Simulink.findVars(mdl);
    for k = 1:numel(vars)
        v = vars(k);
        switch lower(v.SourceType)
            case 'model workspace'
                where = 'model';
            case 'base workspace'
                where = 'base';
            otherwise
                continue;                 % data dictionaries: not written by swc_params (open, TOOL-146)
        end
        entries(end + 1) = struct('name', v.Name, 'workspace', where, 'users', {cellstr(v.Users)}); %#ok<AGROW>
    end
catch err
    fprintf('params: Simulink.findVars on %s failed (%s) - parameter objects taken from the block parameters\n', ...
        mdl, firstLine(err.message));
    entries = textualUse(mdl);
end
for k = 1:numel(entries)
    e = entries(k);
    if strcmp(e.workspace, 'model')
        value = getVariable(get_param(mdl, 'ModelWorkspace'), e.name);
    else
        value = evalin('base', e.name);
    end
    if isa(value, 'Simulink.LookupTable')
        kind = 'lookup_table';
    elseif isa(value, 'Simulink.Breakpoint')
        kind = 'breakpoint';
    elseif isa(value, 'Simulink.Parameter')
        kind = 'parameter';
    else
        continue;                         % plain numbers and other objects are constants of the model
    end
    info = struct('name', e.name, 'workspace', e.workspace, 'kind', kind, 'data_type', '', 'value_class', '', ...
        'dimensions', [], 'values', [], 'min', [], 'max', [], 'unit', '', 'description', '', ...
        'storage_class', '', 'argument', any(strcmp(args, e.name)), 'users', {usersOf(e.users)}, ...
        'scaling', [], 'roles', {rolesOf(e.users, e.name)}, 'breakpoints', {{}}, 'breakpoints_spec', '');
    switch kind
        case 'parameter'
            info = describeValue(info, value.Value, value.DataType, value.Min, value.Max, value.Unit, ...
                value.Description);
            info.storage_class = char(value.StorageClass);
        case 'lookup_table'
            % TOOL-193: the table as the values, the breakpoints (explicit values or names of Simulink.Breakpoint
            % objects) as a list - the import proposes it as a curve/map with own or shared axes
            t = value.Table;
            info = describeValue(info, t.Value, t.DataType, t.Min, t.Max, t.Unit, propOr(t, 'Description'));
            info.storage_class = char(value.CoderInfo.StorageClass);
            info.breakpoints_spec = char(value.BreakpointsSpecification);
            bps = {};
            if strcmp(info.breakpoints_spec, 'Reference')
                for b = 1:numel(value.Breakpoints)
                    bps{end + 1} = struct('name', char(value.Breakpoints{b}), 'data_type', '', 'value_class', '', ...
                        'values', [], 'unit', '', 'description', '', 'scaling', [], 'min', [], 'max', []); %#ok<AGROW>
                end
            else
                for b = 1:numel(value.Breakpoints)
                    bp = value.Breakpoints(b);
                    one = describeValue(struct(), bp.Value, bp.DataType, bp.Min, bp.Max, bp.Unit, ...
                        propOr(bp, 'Description'));
                    bps{end + 1} = struct('name', '', 'data_type', one.data_type, 'value_class', one.value_class, ...
                        'values', one.values, ...
                        'unit', one.unit, 'description', one.description, 'scaling', one.scaling, ...
                        'min', one.min, 'max', one.max); %#ok<AGROW>
                end
            end
            info.breakpoints = bps;
        case 'breakpoint'
            bp = value.Breakpoints;
            info = describeValue(info, bp.Value, bp.DataType, bp.Min, bp.Max, bp.Unit, propOr(bp, 'Description'));
            info.storage_class = char(value.CoderInfo.StorageClass);
    end
    list{end + 1} = info; %#ok<AGROW>
end
fprintf('reported %d parameter objects of %s\n', numel(list), mdl);
end

function info = describeValue(info, v, dataType, low, high, unit, description)
% Value, type and limits of a parameter, table or breakpoint object for the report (TOOL-146/193)
info.data_type = char(dataType);
info.value_class = class(v);
info.dimensions = size(v);
info.values = [];
if (isnumeric(v) || islogical(v)) && ismatrix(v) && numel(v) <= 4096
    info.values = reshape(double(v).', 1, []);   % row by row (a map: one row per X breakpoint)
end
info.min = double(low);
info.max = double(high);
info.unit = char(unit);
info.description = char(description);
info.scaling = fixedScaling(info.data_type);
end

function scaling = fixedScaling(dataType)
% TOOL-193: [signed bits slope bias] of a fixed-point data type (fixdt(...), sfix16_En4, a Simulink.NumericType
% of that name), [] for any other type. MATLAB resolves the expression, so fixdt(1,16,2^-4,0) needs no parser.
scaling = [];
dataType = strtrim(char(dataType));
if isempty(dataType) || ~(startsWith(dataType, 'fixdt') || startsWith(dataType, 'sfix') || ...
        startsWith(dataType, 'ufix'))
    return;
end
try
    if startsWith(dataType, 'fixdt')
        nt = eval(dataType);
    else
        nt = fixdt(dataType);
    end
    if ~isa(nt, 'Simulink.NumericType') || ~strcmp(nt.DataTypeMode(1:min(end, 11)), 'Fixed-point')
        return;
    end
    scaling = [double(strcmp(nt.Signedness, 'Signed')), double(nt.WordLength), double(nt.Slope), ...
        double(nt.Bias)];
catch
    scaling = [];
end
end

function roles = rolesOf(users, name)
% TOOL-193: what the object is in the lookup blocks that use it - 'table', 'bp1', 'bp2', ... ('object': the block
% takes a Simulink.LookupTable object). The import pairs a map with its X and Y axis by these roles, not by guess.
roles = {};
for k = 1:numel(users)
    blk = char(users{k});
    try
        if ~any(strcmp(get_param(blk, 'BlockType'), {'Lookup_n-D', 'Interpolation_n-D'}))
            continue;
        end
        if strcmp(get_param(blk, 'BlockType'), 'Lookup_n-D') && ...
                strcmp(get_param(blk, 'DataSpecification'), 'Lookup table object')
            if strcmp(strtrim(get_param(blk, 'LookupTableObject')), name)
                roles{end + 1} = 'object'; %#ok<AGROW>
            end
            continue;
        end
        if strcmp(strtrim(get_param(blk, 'Table')), name)
            roles{end + 1} = 'table'; %#ok<AGROW>
        end
        n = str2double(get_param(blk, 'NumberOfTableDimensions'));
        for d = 1:n
            if strcmp(strtrim(get_param(blk, sprintf('BreakpointsForDimension%d', d))), name)
                roles{end + 1} = sprintf('bp%d', d); %#ok<AGROW>
            end
        end
    catch
        continue;
    end
end
roles = unique(roles, 'stable');
end

function entries = textualUse(mdl)
entries = struct('name', {}, 'workspace', {}, 'users', {});
candidates = {};
wheres = {};
mw = get_param(mdl, 'ModelWorkspace');
for w = reshape(whos(mw), 1, [])
    candidates{end + 1} = w.name; %#ok<AGROW>
    wheres{end + 1} = 'model'; %#ok<AGROW>
end
for w = reshape(evalin('base', 'whos'), 1, [])
    if ~any(strcmp(candidates, w.name)) && any(strcmp(w.class, {'Simulink.Parameter', 'Simulink.LookupTable', ...
            'Simulink.Breakpoint'}))
        candidates{end + 1} = w.name; %#ok<AGROW>
        wheres{end + 1} = 'base'; %#ok<AGROW>
    end
end
if isempty(candidates)
    return;
end
users = repmat({{}}, 1, numel(candidates));
blocks = find_system(mdl, 'LookUnderMasks', 'all', 'FollowLinks', 'on', 'Type', 'Block');
for b = 1:numel(blocks)
    dialog = get_param(blocks{b}, 'DialogParameters');
    if ~isstruct(dialog)
        continue;
    end
    fields = fieldnames(dialog);
    texts = {};
    for f = 1:numel(fields)
        try
            value = get_param(blocks{b}, fields{f});
        catch
            continue;
        end
        if ischar(value)
            texts{end + 1} = value; %#ok<AGROW>
        end
    end
    text = strjoin(texts, ' ');
    for c = 1:numel(candidates)
        if ~isempty(regexp(text, ['(?<![\w.])' candidates{c} '(?![\w])'], 'once'))
            users{c}{end + 1} = blocks{b};
        end
    end
end
for c = 1:numel(candidates)
    if ~isempty(users{c})
        entries(end + 1) = struct('name', candidates{c}, 'workspace', wheres{c}, 'users', {users{c}}); %#ok<AGROW>
    end
end
end

function users = usersOf(list)
users = cellfun(@(u) strrep(char(u), sprintf('\n'), ' '), list(1:min(end, 5)), 'UniformOutput', false);
users = reshape(users, 1, []);
if isempty(users)
    users = {};
end
end

%% ======================================================================================  helpers

function step(key)
fprintf('nx-step params %s\n', key);
end

function value = propOr(obj, name)
% A text property of a Simulink data object, '' when its class has none (Simulink.LookupTable has no Description)
value = '';
if isprop(obj, name) || (isstruct(obj) && isfield(obj, name))
    value = char(obj.(name));
end
end

function value = fieldOr(s, name)
value = [];
if isfield(s, name)
    value = s.(name);
end
end

function text = firstLine(text)
lines = splitlines(char(text));
text = lines{1};
end

function c = toCell(v)
if isempty(v)
    c = {};
elseif iscell(v)
    c = reshape(v, 1, []);
else
    c = num2cell(reshape(v, 1, []));
end
end

function closeIfLoaded(mdl)
if bdIsLoaded(mdl)
    close_system(mdl, 0);
end
end
