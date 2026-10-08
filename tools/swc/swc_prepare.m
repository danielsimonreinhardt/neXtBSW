% SPDX-License-Identifier: Apache-2.0
function changes = swc_prepare(target, spec)
%SWC_PREPARE Sets a Simulink model up for the AUTOSAR code generation of swc_build, matching its swc.yaml (TOOL-135).
%   The one place that turns a model into an AUTOSAR software component - a new one (swc_create) and an imported
%   one (the Studio's "Modell importieren", always on its COPY) use the same settings:
%
%   swc_prepare('swc/Imported')   loads swc/Imported/Imported.slx, reads swc/Imported/swc.yaml (swc_read_spec),
%                                 prepares the model, checks it by compiling it, saves and closes it
%   swc_prepare(mdl, spec)        prepares the loaded model mdl with the decoded swc.yaml (swc_create);
%                                 saving is the caller's
%
%   What it sets (only what differs is changed, and every change is printed as "prepare: ..."):
%     - root ports of swc.yaml: data type, dimension of swc.yaml (RTE-25; an expression of the model giving the
%       same number stays); in a rate-based model an inport with inherited sample
%       time gets the period of its runnable. A port of swc.yaml the model does not have is an error.
%     - solver Fixed-step (FixedStepDiscrete), step = greatest common divisor of the runnable periods; a
%       rate-based model with several runnables runs multitasking (one periodic function per rate)
%     - system target file autosar.tlc and the code settings of the component template (code only, Cortex-M, ...)
%     - AUTOSAR component (autosar.api.create 'default') unless the model has one; every port of swc.yaml with
%       explicit RTE access (ExplicitReceive/ExplicitSend: Rte_Read_<port>_<element>() / Rte_Write_...)
%     - calibration parameters of swc.yaml (import only, TOOL-146): Simulink.Parameter model arguments mapped to
%       the ParameterReceiver port CalPrm (swc_params; swc_create maps its own)
%     - runnables named as in swc.yaml: export-function model - the runnable of each function-call inport is
%       named after the inport; rate-based model - the periodic function of each rate (fastest first) is the
%       runnable of swc.yaml with that period (a different number of rates or another period is an error)
%   What cannot be switched automatically (continuous states with a discrete solver, blocks without code
%   generation) makes the compile check fail with MATLAB's message; the Studio checks the same before (swc_ports
%   'report') and says so in its preview.

narginchk(1, 2);
repo = fileparts(fileparts(fileparts(mfilename('fullpath'))));
target = char(target);
% A loaded model wins over a folder of the same name in the current folder (swc_create runs next to the
% component folder DemoSwc/ and passes the loaded model DemoSwc, VERIFY-0928)
folderMode = isfolder(target) && ~(isvarname(target) && bdIsLoaded(target));
changes = {};
if folderMode
    % relative to MATLAB's current folder, not to the working directory of the JVM (they differ after cd)
    swcDir = target;
    if ~java.io.File(swcDir).isAbsolute()
        swcDir = fullfile(pwd, swcDir);
    end
    swcDir = char(java.io.File(swcDir).getCanonicalPath());
    [~, mdl] = fileparts(swcDir);
    mdlFile = fullfile(swcDir, [mdl '.slx']);
    if ~isfile(mdlFile)
        error('swc_prepare:noModel', '%s not found', mdlFile);
    end
    addpath(fullfile(repo, 'swc', 'template'));   % NxSwcLib: library blocks the model may link to
    addpath(swcDir);
    step('yaml');
    if nargin < 2 || isempty(spec)
        spec = swc_read_spec(swcDir);
    end
    if ~strcmp(spec.name, mdl)
        error('swc_prepare:name', 'swc.yaml names component %s, the model is %s', spec.name, mdl);
    end
    step('load');
    closeIfLoaded(mdl);
    try
        swc_load_qlf();                        % QLF_* of the RTE, for a model that compares qualifiers (COM-03)
    catch err
        fprintf('qualifier constants not loaded (%s)\n', err.message);
    end
    swc_load_init(mdl, swcDir);
    load_system(mdlFile);
    closer = onCleanup(@() closeIfLoaded(mdl));
    if strcmp(get_param(mdl, 'BlockDiagramType'), 'subsystem')
        % a subsystem reference file has no configuration and no code mapping of its own: its content becomes
        % the content of a model of the same name (the file is the COPY of the import, never the user's file)
        step('convert');
        subsystemToModel(mdl, mdlFile);
        record(sprintf('subsystem reference file %s.slx converted into a model of the same name', mdl));
    end
else
    mdl = target;
end

runnables = toCell(spec.runnables);
ports = toCell(spec.ports);
if isempty(runnables)
    error('swc_prepare:runnables', 'swc.yaml of %s has no runnable', mdl);
end
periods = cellfun(@(r) round(double(r.period_ms)), runnables);
baseMs = 0;
for k = 1:numel(periods)
    baseMs = gcd(baseMs, periods(k));
end
if baseMs <= 0
    error('swc_prepare:period', 'swc.yaml of %s: runnable periods must be whole milliseconds > 0', mdl);
end
periodOf = containers.Map(cellfun(@(r) r.name, runnables, 'UniformOutput', false), num2cell(periods));
fcnInports = find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Inport', 'OutputFunctionCall', 'on');
exportFcn = strcmp(get_param(mdl, 'IsExportFunctionModel'), 'on') || ~isempty(fcnInports);

%% ---- root ports of swc.yaml
step('ports');
for p = 1:numel(ports)
    port = ports{p};
    if strcmp(port.direction, 'in')
        kind = 'Inport';
    else
        kind = 'Outport';
    end
    blk = [mdl '/' strrep(port.name, '/', '//')];
    if getSimulinkBlockHandle(blk) == -1 || ~strcmp(get_param(blk, 'BlockType'), kind)
        error('swc_prepare:port', ['swc.yaml port %s (%s) is no root %s of model %s - the ports of swc.yaml ' ...
            'must be the root ports of the model'], port.name, port.direction, kind, mdl);
    end
    setIf(blk, 'OutDataTypeStr', port.simulink_type, sprintf('port %s: data type', port.name));
    if startsWith(port.simulink_type, 'Bus:')
        % RTE-15: a record of swc.yaml is a nonvirtual bus at the root port (a struct in the code and the RTE)
        setIf(blk, 'BusOutputAsStruct', 'on', sprintf('port %s: nonvirtual bus', port.name));
    end
    % RTE-25: the dimension of swc.yaml (always the number); an expression of the model that gives the same number
    % (hvscmc.number_cell_voltages) stays, so the model keeps reading it from its data
    dims = 1;
    if isfield(port, 'dimension') && ~isempty(port.dimension)
        dims = double(port.dimension);
    end
    if ~dimensionsMatch(blk, get_param(blk, 'PortDimensions'), dims)
        setIf(blk, 'PortDimensions', num2str(dims), sprintf('port %s: dimension', port.name));
    end
    if ~exportFcn && strcmp(kind, 'Inport') && strcmp(strtrim(get_param(blk, 'SampleTime')), '-1')
        users = toCellstr(port.runnables);
        if ~isempty(users) && isKey(periodOf, users{1})
            setIf(blk, 'SampleTime', sprintf('%g', periodOf(users{1}) / 1000), ...
                sprintf('port %s: sample time', port.name));
        end
    end
end

%% ---- solver, target, code settings
step('settings');
setIf(mdl, 'SolverType', 'Fixed-step', 'solver type');
setIf(mdl, 'Solver', 'FixedStepDiscrete', 'solver');
step_s = baseMs / 1000;
if abs(str2double(get_param(mdl, 'FixedStep')) - step_s) > 1e-12 || isnan(str2double(get_param(mdl, 'FixedStep')))
    setIf(mdl, 'FixedStep', sprintf('%g', step_s), 'fixed step');
end
if ~exportFcn && numel(runnables) > 1
    setIf(mdl, 'EnableMultiTasking', 'on', 'multitasking (one periodic function per rate)');
end
setIf(mdl, 'SystemTargetFile', 'autosar.tlc', 'system target file');
settings = {'GenCodeOnly', 'on'; 'GenerateMakefile', 'off'; 'GenerateReport', 'off';
            'ProdHWDeviceType', 'ARM Compatible->ARM Cortex-M'; 'ProdLongLongMode', 'on';
            'SupportNonFinite', 'off'; 'MatFileLogging', 'off'; 'GenerateComments', 'on'; 'MaxIdLength', '63'};
for k = 1:size(settings, 1)
    setIf(mdl, settings{k, 1}, settings{k, 2}, settings{k, 1});
end

%% ---- check (import): the model compiles with the new settings - before the AUTOSAR component is made from it
% (autosar.api.create maps what the compiled model has: a model that does not compile gets no periodic runnable)
if folderMode
    step('check');
    swc_load_data(mdl, swcDir);
    try
        feval(mdl, [], [], [], 'compile');
        feval(mdl, [], [], [], 'term');
    catch err
        try
            feval(mdl, [], [], [], 'term');
        catch
        end
        error('swc_prepare:compile', ['model %s does not compile with the settings for code generation: %s\n' ...
            'The user''s model is unchanged. Variables the model needs (constants of a setup script) belong ' ...
            'in %s_init.m next to the model; blocks with continuous states or without code generation must be ' ...
            'replaced in the model. Then import it again.'], mdl, messagesOf(err), mdl);
    end
end

%% ---- AUTOSAR component and port mapping
step('autosar');
if ~hasComponent(mdl)
    autosar.api.create(mdl, 'default');
    record(sprintf('AUTOSAR component %s created (default mapping)', mdl));
end
slMap = autosar.api.getSimulinkMapping(mdl);
arProps = autosar.api.getAUTOSARProperties(mdl);
for p = 1:numel(ports)
    name = ports{p}.name;
    if strcmp(ports{p}.direction, 'in')
        [arPort, element, mode] = getInport(slMap, name);
        if ~strcmp(mode, 'ExplicitReceive')
            mapInport(slMap, name, arPort, element, 'ExplicitReceive');
            record(sprintf('port %s: RTE access %s -> ExplicitReceive', name, mode));
        end
    else
        [arPort, element, mode] = getOutport(slMap, name);
        if ~strcmp(mode, 'ExplicitSend')
            mapOutport(slMap, name, arPort, element, 'ExplicitSend');
            record(sprintf('port %s: RTE access %s -> ExplicitSend', name, mode));
        end
    end
end

%% ---- runnables named as in swc.yaml
step('runnables');
functions = cellstr(find(slMap, 'Functions'));
if exportFcn
    for k = 1:numel(functions)
        if startsWith(functions{k}, 'ExportedFunction:')
            inport = extractAfter(functions{k}, 'ExportedFunction:');
            if isKey(periodOf, inport)
                renameRunnable(arProps, getFunction(slMap, functions{k}), inport, periodOf(inport));
            end
        end
    end
else
    periodic = functions(startsWith(functions, 'Periodic'));
    [~, order] = sort(cellfun(@rateIndex, periodic));
    periodic = periodic(order);
    [~, order] = sort(periods);
    wanted = runnables(order);
    if numel(periodic) ~= numel(wanted)
        error('swc_prepare:rates', ['model %s has %d periodic rate(s) (functions of the mapping: %s), swc.yaml ' ...
            '%d runnable(s) (%s): every rate of the model is one runnable - give swc.yaml one runnable per rate ' ...
            'or align the sample times of the model'], mdl, numel(periodic), strjoin(functions, ', '), numel(wanted), ...
            strjoin(cellfun(@(r) sprintf('%s %d ms', r.name, round(double(r.period_ms))), wanted, ...
            'UniformOutput', false), ', '));
    end
    for k = 1:numel(periodic)
        renameRunnable(arProps, getFunction(slMap, periodic{k}), wanted{k}.name, round(double(wanted{k}.period_ms)));
    end
end

%% ---- calibration parameters of swc.yaml (TOOL-146): model arguments of the ParameterReceiver port CalPrm
% The import proposes the Simulink.Parameter objects of the model as calibration parameters; the ones the user
% chose stand in swc.yaml. swc_create maps its own parameters itself, so only the import (folder mode) does it here.
if folderMode && isfield(spec, 'parameters') && ~isempty(spec.parameters)
    step('params');
    changes = [changes, swc_params(mdl, spec, swcDir)];
end

%% ---- save (import)
if folderMode
    step('save');
    save_system(mdl, mdlFile);
    close_system(mdl, 0);
    clear closer;
    fprintf('prepared %s (%d changes)\n', mdlFile, numel(changes));
end

    function record(text)
        changes{end + 1} = text;
        fprintf('prepare: %s\n', text);
    end

    function setIf(obj, param, value, what)
        old = get_param(obj, param);
        if ~ischar(old)
            old = num2str(old);
        end
        if strcmp(strtrim(old), value)
            return;
        end
        set_param(obj, param, value);
        record(sprintf('%s: %s -> %s', what, old, value));
    end

    function renameRunnable(props, old, new, periodMs)
        paths = find(props, [], 'Runnable', 'PathType', 'FullyQualified');
        path = paths(cellfun(@(q) strcmp(leaf(q), old), paths));
        if isempty(path)
            error('swc_prepare:runnable', 'AUTOSAR runnable %s of model %s not found', old, mdl);
        end
        path = path{1};
        events = find(props, [], 'TimingEvent', 'PathType', 'FullyQualified');
        for e = 1:numel(events)
            if strcmp(leaf(get(props, events{e}, 'StartOnEvent')), old)
                period = get(props, events{e}, 'Period');
                % only a rate-based model's timing event carries its rate here; the one of an export-function
                % model is a placeholder (1 s) that the build replaces by the sample time of the inport
                if ~exportFcn && abs(double(period) - periodMs / 1000) > 1e-9
                    error('swc_prepare:period', ['runnable %s of model %s runs every %g s, swc.yaml gives %s ' ...
                        '%d ms - align the sample times of the model or the period in swc.yaml'], old, mdl, ...
                        double(period), new, periodMs);
                end
                if strcmp(leaf(events{e}), ['Event_' old]) && ~strcmp(old, new)
                    set(props, events{e}, 'Name', ['Event_' new]);
                end
            end
        end
        if ~strcmp(old, new)
            set(props, path, 'Name', new, 'symbol', new);
            record(sprintf('runnable %s -> %s (%d ms)', old, new, periodMs));
        end
    end
end

%% ======================================================================================

function step(key)
% progress of the Studio (studio.core.matlab): the key names the part of the preparation that starts now
fprintf('nx-step prepare %s\n', key);
end

function subsystemToModel(mdl, mdlFile)
% Content of the subsystem reference file mdl -> a new model mdl saved over mdlFile (via a host subsystem in a
% scratch model, the two copy functions Simulink has for this).
host = 'nx_prepare_host';
closeIfLoaded(host);
new_system(host);
hostCleanup = onCleanup(@() closeIfLoaded(host));
add_block('simulink/Ports & Subsystems/Subsystem', [host '/Content']);
Simulink.SubSystem.deleteContents([host '/Content']);
Simulink.BlockDiagram.copyContentsToSubSystem(mdl, [host '/Content']);
close_system(mdl, 0);
new_system(mdl, 'Model');
Simulink.SubSystem.copyContentsToBlockDiagram([host '/Content'], mdl);
clear hostCleanup;                             % the model is saved over mdlFile only when the preparation succeeds
end

function text = messagesOf(err)
% the message of an error with those of its causes ("Error due to multiple causes." alone says nothing)
texts = {err.message};
for k = 1:numel(err.cause)
    texts{end + 1} = messagesOf(err.cause{k}); %#ok<AGROW>
end
texts = unique(texts, 'stable');
text = strjoin(texts(1:min(end, 8)), '\n  ');
end

function yes = hasComponent(mdl)
yes = false;
if ~strcmp(get_param(mdl, 'SystemTargetFile'), 'autosar.tlc')
    return;
end
try
    props = autosar.api.getAUTOSARProperties(mdl);
    yes = ~isempty(find(props, [], 'AtomicComponent', 'PathType', 'FullyQualified'));
catch
    yes = false;
end
end

function index = rateIndex(fn)
% 'Periodic' (single rate) -> 1, 'Periodic:D2' -> 2
token = regexp(fn, 'D(\d+)$', 'tokens', 'once');
if isempty(token)
    index = 1;
else
    index = str2double(token{1});
end
end

function name = leaf(path)
path = char(path);
slash = find(path == '/', 1, 'last');
if isempty(slash)
    name = path;
else
    name = path(slash + 1:end);
end
end

function c = toCell(v)
% jsondecode gives a struct array for uniform objects, a cell array otherwise, [] for an empty list
if isempty(v)
    c = {};
elseif iscell(v)
    c = reshape(v, 1, []);
else
    c = num2cell(reshape(v, 1, []));
end
end

function c = toCellstr(v)
if ischar(v)
    c = {v};
else
    c = cellstr(v);
end
end

function closeIfLoaded(mdl)
if bdIsLoaded(mdl)
    close_system(mdl, 0);
end
end

function yes = dimensionsMatch(blk, text, dims)
% RTE-25: whether the PortDimensions text of blk already gives `dims` elements as a vector ('1' for a scalar,
% '8', '[1 8]', or an expression of the workspace that evaluates to it)
text = strtrim(char(text));
if dims == 1
    yes = any(strcmp(text, {'1', '[1]', '[1 1]'}));
    return;
end
yes = false;
try
    value = slResolve(text, blk);
catch
    try
        value = evalin('base', text);
    catch
        return;
    end
end
if isa(value, 'Simulink.Parameter')
    value = value.Value;
end
value = double(value);
yes = (isscalar(value) && value == dims) || (numel(value) == 2 && min(value) == 1 && max(value) == dims);
end
