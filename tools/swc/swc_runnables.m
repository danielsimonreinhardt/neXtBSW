% SPDX-License-Identifier: Apache-2.0
function out = swc_runnables(mdl, action, varargin)
%SWC_RUNNABLES Runnables of a loaded component model: read them, write them into the model (TOOL-184).
%   Until TOOL-184 no tool wrote a runnable into a model - swc_create drew them once, afterwards "Runnables zieht man
%   in Simulink nach". swc_ports 'apply' now adds, removes and renames them with the same care as ports (the model is
%   the work of hours: only the blocks a runnable needs are touched). What a runnable is depends on the model:
%
%   export-function model (the swc_create layout): a runnable is a root function-call inport with the period as
%       sample time, triggering a function-call subsystem <Swc>_task<Period>ms (swc_create's convention, TOOL-180;
%       <Swc>_task<Period>ms_<Runnable> when that name is taken). swc_runnable_subsystem finds the subsystem.
%   rate-based model (the normal case of an imported one, swc_prepare): a runnable IS a rate of the model - every
%       periodic function of the multitasking model is one runnable. There is no block to add: a rate comes with a
%       block of that sample time (swc_ports adds the root port with it) and only its AUTOSAR runnable is named.
%
%   swc_runnables(mdl, 'ratebased')           true if the model has no function-call inport and is no export-
%                                              function model
%   swc_runnables(mdl, 'rates', swcDir)       rate-based model: struct array name/period_ms of its rates, fastest
%                                              first - from the AUTOSAR mapping (the names swc_prepare gave from the
%                                              swc.yaml), else from <swcDir>/swc.yaml, else empty
%   swc_runnables(mdl, 'logic', runnable)     blocks of the user in the runnable's subsystem (not the trigger, the
%                                              port blocks and the placeholders <Port>_Unused/<Port>_Default)
%   swc_runnables(mdl, 'add', name, periodMs) export-function model: inport and subsystem; rate-based: nothing
%   swc_runnables(mdl, 'remove', name)        export-function model: inport and subsystem, refused while the
%                                              subsystem holds logic of the user; rate-based: nothing
%   swc_runnables(mdl, 'rename', old, new)    export-function model: the inport (the subsystem keeps its name)
%   swc_runnables(mdl, 'map', runnables)      AUTOSAR runnables named like `runnables` (cell of struct name/
%                                              period_ms, the swc.yaml): export-function by inport, rate-based by
%                                              period (fastest first) - the rule of swc_prepare; a function without
%                                              a runnable gets one with its timing event

switch lower(char(action))
    case 'ratebased'
        out = isRateBased(mdl);
    case 'rates'
        swcDir = '';
        if ~isempty(varargin)
            swcDir = char(varargin{1});
        end
        out = ratesOf(mdl, swcDir);
    case 'logic'
        out = logicOf(mdl, char(varargin{1}));
    case 'add'
        out = [];
        if ~isRateBased(mdl)
            addRunnable(mdl, char(varargin{1}), double(varargin{2}));
        end
    case 'remove'
        out = [];
        if ~isRateBased(mdl)
            removeRunnable(mdl, char(varargin{1}));
        end
    case 'rename'
        out = [];
        if ~isRateBased(mdl)
            renameRunnable(mdl, char(varargin{1}), char(varargin{2}));
        end
    case 'map'
        swcDir = '';
        if numel(varargin) > 1
            swcDir = char(varargin{2});
        end
        out = mapRunnables(mdl, varargin{1}, swcDir);
    otherwise
        error('swc_runnables:action', 'unknown action %s', char(action));
end
end

%% ======================================================================================  reading

function yes = isRateBased(mdl)
yes = isempty(find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Inport', 'OutputFunctionCall', 'on')) && ...
    ~strcmp(get_param(mdl, 'IsExportFunctionModel'), 'on');
end

function list = ratesOf(mdl, swcDir)
list = struct('name', {}, 'period_ms', {});
if strcmp(get_param(mdl, 'SystemTargetFile'), 'autosar.tlc')
    try
        props = autosar.api.getAUTOSARProperties(mdl);
        events = find(props, [], 'TimingEvent', 'PathType', 'FullyQualified');
        for e = 1:numel(events)
            name = leaf(get(props, events{e}, 'StartOnEvent'));
            ms = round(double(get(props, events{e}, 'Period')) * 1000);
            if ~isempty(name) && ms > 0 && ~any(strcmp({list.name}, name))
                list(end + 1) = struct('name', name, 'period_ms', ms); %#ok<AGROW>
            end
        end
    catch err
        fprintf('rates of %s: no AUTOSAR runnables (%s)\n', mdl, err.message);
    end
end
if isempty(list) && ~isempty(swcDir) && isfile(fullfile(swcDir, 'swc.yaml'))
    try
        spec = swc_read_spec(swcDir);
        runnables = toCell(spec.runnables);
        for r = 1:numel(runnables)
            ms = round(double(runnables{r}.period_ms));
            list(end + 1) = struct('name', runnables{r}.name, 'period_ms', ms); %#ok<AGROW>
        end
    catch err
        fprintf('rates of %s: swc.yaml not read (%s)\n', mdl, err.message);
    end
end
if ~isempty(list)
    [~, order] = sort([list.period_ms]);
    list = list(order);
end
end

function names = logicOf(mdl, runnable)
names = {};
sys = swc_runnable_subsystem(mdl, runnable);
if isempty(sys)
    return;
end
blocks = find_system(sys, 'SearchDepth', 1, 'LookUnderMasks', 'all', 'FollowLinks', 'off', 'Type', 'Block');
for k = 1:numel(blocks)
    if strcmp(blocks{k}, sys)
        continue;
    end
    type = get_param(blocks{k}, 'BlockType');
    name = get_param(blocks{k}, 'Name');
    if any(strcmp(type, {'Inport', 'Outport', 'TriggerPort'})) || ...
            (strcmp(type, 'Terminator') && endsWith(name, '_Unused')) || ...
            (strcmp(type, 'Ground') && endsWith(name, '_Default'))
        continue;
    end
    names{end + 1} = strrep(name, sprintf('\n'), ' '); %#ok<AGROW>
end
end

%% ======================================================================================  writing

function addRunnable(mdl, name, periodMs)
%ADDRUNNABLE Function-call inport <name> and subsystem <Swc>_task<P>ms, as swc_create draws them - the subsystem
%   empty (its ports come with swc_ports 'add'), triggered periodically with the period like swc_create's.
if getSimulinkBlockHandle([mdl '/' name]) > 0
    error('swc_runnables:exists', 'model %s already has a block %s', mdl, name);
end
if ~(periodMs > 0)
    error('swc_runnables:period', 'runnable %s: the period must be > 0 ms', name);
end
sampleTime = sprintf('%g', periodMs / 1000);
top = belowRoot(mdl);
add_block('simulink/Sources/In1', [mdl '/' name], 'Position', [30 top 60 top + 14], 'BackgroundColor', 'green');
set_param([mdl '/' name], 'OutputFunctionCall', 'on', 'SampleTime', sampleTime);
sname = sprintf('%s_task%dms', mdl, round(periodMs));
if getSimulinkBlockHandle([mdl '/' sname]) > 0
    sname = [sname '_' name];                    % two runnables of the same period (swc_create's rule)
end
sys = [mdl '/' sname];
add_block('simulink/Ports & Subsystems/Function-Call Subsystem', sys, 'Position', [250 top 430 top + 80]);
lines = find_system(sys, 'SearchDepth', 1, 'FindAll', 'on', 'Type', 'line');
delete_line(lines);
delete_block([sys '/In1']);
delete_block([sys '/Out1']);
set_param([sys '/function'], 'SampleTimeType', 'periodic', 'SampleTime', sampleTime);
add_line(mdl, [name '/1'], [sname '/Trigger'], 'autorouting', 'on');
end

function removeRunnable(mdl, name)
%REMOVERUNNABLE Inport and subsystem of a runnable - only if the subsystem holds nothing of the user. The port blocks
%   in it go with it (their root ports stay; a port the swc.yaml drops is removed by swc_ports before this).
blk = [mdl '/' name];
if getSimulinkBlockHandle(blk) <= 0 || ~strcmp(get_param(blk, 'OutputFunctionCall'), 'on')
    error('swc_runnables:noRunnable', 'model %s has no runnable %s', mdl, name);
end
sys = swc_runnable_subsystem(mdl, name);
if ~isempty(sys)
    logic = logicOf(mdl, name);
    if ~isempty(logic)
        error('swc_runnables:logic', ['runnable %s: its subsystem %s holds logic (%s); removing it would take ' ...
            'that logic with it. Move or delete it in the model first.'], name, sys, strjoin(logic, ', '));
    end
end
arRunnable = '';
if strcmp(get_param(mdl, 'SystemTargetFile'), 'autosar.tlc')
    try
        arRunnable = getFunction(autosar.api.getSimulinkMapping(mdl), ['ExportedFunction:' name]);
    catch
    end
end
if ~isempty(sys)
    removeLines(sys);
    delete_block(sys);
end
removeLines(blk);
delete_block(blk);
if ~isempty(arRunnable)
    deleteArRunnable(mdl, arRunnable);
end
end

function renameRunnable(mdl, old, new)
blk = [mdl '/' old];
if getSimulinkBlockHandle(blk) <= 0 || ~strcmp(get_param(blk, 'OutputFunctionCall'), 'on')
    error('swc_runnables:noRunnable', 'model %s has no runnable %s', mdl, old);
end
if getSimulinkBlockHandle([mdl '/' new]) > 0
    error('swc_runnables:exists', 'model %s already has a block %s', mdl, new);
end
set_param(blk, 'Name', new);
end

%% ======================================================================================  AUTOSAR side

function changes = mapRunnables(mdl, runnables, swcDir)
%MAPRUNNABLES Names the AUTOSAR runnables after the swc.yaml (swc_prepare's rule) and gives a function that has
%   none its runnable and timing event. Only for a model that is an AUTOSAR component.
changes = {};
if ~strcmp(get_param(mdl, 'SystemTargetFile'), 'autosar.tlc')
    return;
end
if isstruct(runnables)
    runnables = num2cell(runnables);
end
names = cellfun(@(r) char(r.name), runnables, 'UniformOutput', false);
periods = cellfun(@(r) round(double(r.period_ms)), runnables);
slMap = autosar.api.getSimulinkMapping(mdl);
props = autosar.api.getAUTOSARProperties(mdl);
functions = cellstr(find(slMap, 'Functions'));
if isRateBased(mdl)
    periodic = functions(startsWith(functions, 'Periodic'));
    if numel(periodic) ~= numel(names) && nargin > 2 && ~isempty(swcDir)
        % a rate that came (or went) with a port is known to the mapping only after the model was compiled
        % (read only; the variables of <Swc>_init.m and the QLF_* constants the model may use first)
        compileOnce(mdl, swcDir);
        slMap = autosar.api.getSimulinkMapping(mdl);
        functions = cellstr(find(slMap, 'Functions'));
        periodic = functions(startsWith(functions, 'Periodic'));
    end
    [~, order] = sort(cellfun(@rateIndex, periodic));
    periodic = periodic(order);
    [~, order] = sort(periods);
    if numel(periodic) ~= numel(order)
        error('swc_runnables:rates', ['model %s has %d periodic rate(s) (%s), swc.yaml %d runnable(s) (%s): in a ' ...
            'rate-based model every rate is one runnable'], mdl, numel(periodic), strjoin(periodic, ', '), ...
            numel(order), strjoin(names(order), ', '));
    end
    for k = 1:numel(periodic)
        changes = [changes, mapOne(mdl, slMap, props, periodic{k}, names{order(k)}, ...
            periods(order(k)), true)]; %#ok<AGROW>
    end
else
    for k = 1:numel(functions)
        if ~startsWith(functions{k}, 'ExportedFunction:')
            continue;
        end
        inport = extractAfter(functions{k}, 'ExportedFunction:');
        hit = find(strcmp(names, inport), 1);
        if ~isempty(hit)
            changes = [changes, mapOne(mdl, slMap, props, functions{k}, names{hit}, ...
                periods(hit), false)]; %#ok<AGROW>
        end
    end
end
for k = 1:numel(changes)
    fprintf('runnables: %s\n', changes{k});
end
end

function changes = mapOne(mdl, slMap, props, fn, name, periodMs, ratePeriod)
changes = {};
current = '';
try
    current = getFunction(slMap, fn);
catch
end
comp = componentPath(props);
runnables = find(props, [], 'Runnable', 'PathType', 'FullyQualified');
if isempty(current) || ~any(cellfun(@(q) strcmp(leaf(q), current), runnables))
    % a function without a runnable: a runnable of its own, with a timing event of its period
    behavior = internalBehavior(props, comp);
    if ~any(cellfun(@(q) strcmp(leaf(q), name), runnables))
        add(props, behavior, 'Runnables', name, 'symbol', name);
    end
    mapFunction(slMap, fn, name);
    event = ['Event_' name];
    if ~any(cellfun(@(q) strcmp(leaf(q), event), find(props, [], 'TimingEvent', 'PathType', 'FullyQualified')))
        add(props, behavior, 'Events', event, 'Category', 'TimingEvent', 'Period', periodMs / 1000, ...
            'StartOnEvent', [behavior '/' name]);
    end
    changes{end + 1} = sprintf('%s: runnable %s created (%d ms)', fn, name, periodMs);
    return;
end
events = find(props, [], 'TimingEvent', 'PathType', 'FullyQualified');
for e = 1:numel(events)
    if ~strcmp(leaf(get(props, events{e}, 'StartOnEvent')), current)
        continue;
    end
    % rate-based: the timing event carries the rate - a new rate in between moves the functions (Periodic:D2 is
    % 20 ms now, not 100 ms), so the period is set along with the name
    if ratePeriod && abs(double(get(props, events{e}, 'Period')) - periodMs / 1000) > 1e-9
        set(props, events{e}, 'Period', periodMs / 1000);
        changes{end + 1} = sprintf('%s: timing event of %s now %d ms', fn, current, periodMs); %#ok<AGROW>
    end
    if ~strcmp(current, name) && strcmp(leaf(events{e}), ['Event_' current])
        set(props, events{e}, 'Name', ['Event_' name]);
    end
end
if strcmp(current, name)
    return;
end
path = runnables(cellfun(@(q) strcmp(leaf(q), current), runnables));
path = path{1};
set(props, path, 'Name', name, 'symbol', name);
changes{end + 1} = sprintf('%s: runnable %s -> %s (%d ms)', fn, current, name, periodMs);
end

function compileOnce(mdl, swcDir)
%COMPILEONCE Compiles the model and ends the compilation again - nothing is changed or saved.
swc_load_init(mdl, swcDir);
try
    swc_load_qlf();
catch err
    fprintf('qualifier constants not loaded (%s)\n', err.message);
end
feval(mdl, [], [], [], 'compile');
feval(mdl, [], [], [], 'term');
end

function deleteArRunnable(mdl, runnable)
props = autosar.api.getAUTOSARProperties(mdl);
events = find(props, [], 'TimingEvent', 'PathType', 'FullyQualified');
for e = 1:numel(events)
    if strcmp(leaf(get(props, events{e}, 'StartOnEvent')), runnable)
        try
            delete(props, events{e});
        catch err
            warning('swc_runnables:autosar', 'timing event %s not removed (%s)', events{e}, err.message);
        end
    end
end
paths = find(props, [], 'Runnable', 'PathType', 'FullyQualified');
paths = paths(cellfun(@(q) strcmp(leaf(q), runnable), paths));
for k = 1:numel(paths)
    try
        delete(props, paths{k});
    catch err
        warning('swc_runnables:autosar', 'runnable %s not removed (%s)', paths{k}, err.message);
    end
end
end

function behavior = internalBehavior(props, comp)
behavior = get(props, comp, 'Behavior', 'PathType', 'FullyQualified');
if iscell(behavior)
    behavior = behavior{1};
end
end

function comp = componentPath(props)
comp = find(props, [], 'AtomicComponent', 'PathType', 'FullyQualified');
if isempty(comp)
    error('swc_runnables:autosar', 'the model has no AUTOSAR component');
end
comp = comp{1};
end

%% ======================================================================================  helpers

function index = rateIndex(fn)
% 'Periodic' (single rate) -> 1, 'Periodic:D2' -> 2 (as swc_prepare)
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

function top = belowRoot(mdl)
top = 40;
blocks = find_system(mdl, 'SearchDepth', 1, 'Type', 'Block');
for k = 1:numel(blocks)
    pos = get_param(blocks{k}, 'Position');
    top = max(top, pos(4) + 40);
end
end

function removeLines(blk)
lh = get_param(blk, 'LineHandles');
handles = [];
for field = fieldnames(lh)'
    value = lh.(field{1});
    handles = [handles; value(:)]; %#ok<AGROW>
end
handles = unique(handles(handles > 0));
for k = 1:numel(handles)
    if ishandle(handles(k))
        delete_line(handles(k));
    end
end
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
