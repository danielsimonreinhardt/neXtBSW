% SPDX-License-Identifier: Apache-2.0
function out = swc_ports(swcDir, action, file)
%SWC_PORTS Reads the ports of an existing component model and writes changed ports into it (TOOL-14).
%   The model is the master of a Simulink component: swc_create builds it once, afterwards it is the work of
%   hours. This function is the only way the Studio touches it, and it never rebuilds it - it changes exactly
%   the blocks a port needs and leaves every other block, line and setting where it is.
%
%   swc_ports('swc/NewSwc', 'report', 'report.json')
%       Reads NewSwc.slx and writes what the Studio shows in the preview: runnables with their subsystem,
%       every root port with direction, Simulink type and the runnables using it, and - the point of the
%       preview - what is attached to that port inside the model. A port that only carries the placeholder
%       swc_create puts there (<Port>_Unused Terminator, <Port>_Default Ground) is reported as `free`; a port
%       some logic of the user hangs on is not. The report also lists every block and every connection of the
%       whole model - one entry per destination of a line, so a branched line counts once per branch (TOOL-107)
%       - and the caller can compare the model before and after a change block by block. Nothing is saved.
%
%   swc_ports('swc/NewSwc', 'apply', 'plan.json')
%       Applies the plan the Studio wrote, in the order rename -> remove -> add -> retype, and saves the model
%       once at the end. The plan must name an existing backup copy of the .slx (`backup`); without it nothing
%       is touched - a model change of this kind is only allowed to happen when the old state is still on disk.
%
%   Plan (JSON, written by tools/studio/core/modelports.py):
%     name     component (= model and folder name)
%     backup   full path of the copy of the .slx made before the call; must exist
%     report   optional: where the state after the change is written (the caller's loss proof)
%     actions  list of {kind, name, ...}
%              {"kind":"rename","name":"Old","new_name":"New"}
%              {"kind":"remove","name":"Gone","detach":true|false}
%              {"kind":"add","name":"New","direction":"in"|"out","simulink_type":"uint16",
%               "runnables":["Run10ms"]}
%              {"kind":"retype","name":"Level","simulink_type":"int16"}
%              RTE-30: an "add"/"retype" of a provided port of a record or an enumeration carries "default", the Value
%              of its Constant placeholder <Port>_Default (swc_create: '0' for 'Bus: X', 'X.<literal>' for 'Enum: X')
%     types_file  RTE-30, optional: {name, text} - <Swc>_types.m generated from types: of swc.yaml (swc_yaml.types_m)
%              when a port of the plan has a named type. Written if it differs (the old one is copied next to the
%              backup), run before the model loads; a model without PreLoadFcn gets the one of swc_create
%              TOOL-184 (swc_runnables.m):
%              {"kind":"add_runnable","name":"Run20ms","period_ms":20}
%              {"kind":"remove_runnable","name":"Old"}          refused while its subsystem holds logic
%              {"kind":"rename_runnable","name":"Old","new_name":"New"}
%              an "add" of a rate-based model carries "period_ms": the sample time of the new root port
%     runnables  TOOL-184, optional: [{name, period_ms}] of the swc.yaml - the AUTOSAR runnables are named after
%              them when a runnable came, went or was renamed (rate-based: also when a port came)
%              TOOL-185 (swc_measurements.m), applied last:
%              {"kind":"measure","name":"SpeedFilt","simulink_type":"single","dimensions":1,"unit":"rpm",
%               "description":"...","signal":"tmp1"}   Simulink.Signal (ExportedGlobal) for the named signal
%              `name`, written into <Swc>_init.m (R2024a forces objects of the model workspace to Auto; the
%              script is copied next to the backup first); `signal` (optional): the named signal to take, renamed
%              to `name`
%
%   TOOL-185: 'report' also names the named signals of the model (named_signals, swc_measurements 'signals') -
%   the candidates the Studio offers for a measurement of the swc.yaml the model does not have yet.
%
%   TOOL-184: a RATE-BASED model (no function-call inport - the normal case of an imported model) is handled too.
%   Its runnables are its rates ('report': kind 'rate', names of the AUTOSAR mapping, else of the swc.yaml), its
%   ports hang at the root: a new port is a root port with the sample time of its runnable and the placeholder
%   beside it at the root, a port is renamed and removed there by the same rules as in a subsystem.
%
%   Removing a port is the dangerous case, so it is the one with the strictest rule (see the Studio for the
%   reasoning): a port some logic hangs on is removed only with `detach` - and even then **no block of the
%   user is deleted**. The logic that read the port gets a Constant <Port>_Removed of the port's type in its
%   place, the logic that wrote it ends in a Terminator <Port>_Removed. The model keeps building, and the
%   diagram shows where the port used to be. Without `detach` such a port is refused, naming what hangs on it.
%
%   The AUTOSAR side follows every change (interface, data element, port and the Simulink mapping), so
%   swc_build afterwards writes an ARXML that matches the model - otherwise tools/rtegen would keep reading
%   the old ports.

narginchk(3, 3);
swcDir = char(java.io.File(char(swcDir)).getCanonicalPath());
if ~isfolder(swcDir)
    error('swc_ports:noDir', 'folder %s not found', swcDir);
end
[~, mdl] = fileparts(swcDir);
mdlFile = fullfile(swcDir, [mdl '.slx']);
if ~isfile(mdlFile)
    error('swc_ports:noModel', ['%s not found. Ports can only be written into an existing model; ' ...
        'a new component is created with swc_create.'], mdlFile);
end
repo = fileparts(fileparts(fileparts(mfilename('fullpath'))));
addpath(fullfile(repo, 'swc', 'template'));   % NxSwcLib: library blocks the model links to (RTE-07)

switch lower(char(action))
    case 'report'
        out = doReport(mdl, mdlFile, char(file));
    case 'apply'
        out = doApply(mdl, mdlFile, char(file));
    otherwise
        error('swc_ports:action', 'unknown action %s (report or apply)', char(action));
end
end

%% ======================================================================================  report

function report = doReport(mdl, mdlFile, outFile)
closeIfLoaded(mdl);
fprintf('nx-step ports_report load\n');
swc_load_init(mdl, fileparts(mdlFile));       % TOOL-135: variables the model takes constants from
load_system(mdlFile);
cleanup = onCleanup(@() closeIfLoaded(mdl));
typeInports(mdl, fullfile(fileparts(mdlFile), 'nx_port_types.json'));
fprintf('nx-step ports_report ports\n');
report = collect(mdl, mdlFile);
fprintf('nx-step ports_report check\n');
report.codegen = codegenCheck(mdl, report.signal_objects);
try
    report.parameter_objects = swc_params(mdl, 'report');   % TOOL-146: the import proposes them for calibration
catch err
    fprintf('parameter objects of %s not reported: %s\n', mdl, err.message);
    report.parameter_objects = {};
end
report.parameter_arguments = get_param(mdl, 'ParameterArgumentNames');   % TOOL-146: "Ins Modell schreiben"
writeJson(outFile, report);
close_system(mdl, 0);
clear cleanup;
fprintf('reported %d ports of %s into %s\n', numel(report.ports), mdlFile, outFile);
end

function report = collect(mdl, mdlFile)
%COLLECT Everything the Studio needs for the preview and for the proof that nothing was lost.
report = struct();
report.name = mdl;
report.model = strrep(mdlFile, '\', '/');
report.runnables = {};
report.ports = {};
[runnableNames, subsystems] = runnablesOf(mdl);
for r = 1:numel(runnableNames)
    % TOOL-184: kind, and the blocks of the user in its subsystem (a runnable without any may be removed)
    report.runnables{end + 1} = struct('name', runnableNames{r}, 'subsystem', subsystems{r}, ...
        'sample_time', get_param([mdl '/' runnableNames{r}], 'SampleTime'), 'kind', 'function-call', ...
        'blocks', {swc_runnables(mdl, 'logic', runnableNames{r})});
end
% TOOL-184: a rate-based model (the normal case of an imported one) has no function-call inports - its runnables
% are its rates, named by the AUTOSAR mapping swc_prepare made from the swc.yaml; its ports hang at the root
report.rate_based = swc_runnables(mdl, 'ratebased');
rates = struct('name', {}, 'period_ms', {});
if report.rate_based
    rates = swc_runnables(mdl, 'rates', fileparts(mdlFile));
    for r = 1:numel(rates)
        report.runnables{end + 1} = struct('name', rates(r).name, 'subsystem', '', ...
            'sample_time', sprintf('%g', rates(r).period_ms / 1000), 'kind', 'rate', 'blocks', {{}});
    end
end
names = [find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Inport');
         find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Outport')];
for k = 1:numel(names)
    blk = names{k};
    name = get_param(blk, 'Name');
    if any(strcmp(name, runnableNames))
        continue;                                  % function-call inport of a runnable, not a data port
    end
    if report.rate_based
        report.ports{end + 1} = ratePortInfo(blk, name, rates);
        continue;
    end
    report.ports{end + 1} = portInfo(mdl, blk, name, runnableNames, subsystems);
end
report.blocks = sort(cellfun(@(b) strrep(b, sprintf('\n'), ' '), ...
    find_system(mdl, 'LookUnderMasks', 'all', 'FollowLinks', 'off', 'Type', 'Block'), 'UniformOutput', false));
report.lines = lineList(mdl, subsystems);
report.autosar = strcmp(get_param(mdl, 'SystemTargetFile'), 'autosar.tlc');
% TOOL-130: what importing a model that has no swc.yaml yet needs - the code generation settings the Studio
% checks (target, solver) and the base rate a single-rate model runs at. Block parameters only, the model is not
% compiled (a model of the user may not even compile before its libraries are on the path).
report.system_target_file = get_param(mdl, 'SystemTargetFile');
report.solver_type = get_param(mdl, 'SolverType');
report.fixed_step = get_param(mdl, 'FixedStep');
report.solver = get_param(mdl, 'Solver');                               % TOOL-135: e.g. VariableStepAuto
report.export_function = strcmp(get_param(mdl, 'IsExportFunctionModel'), 'on');
report.block_diagram_type = get_param(mdl, 'BlockDiagramType');         % 'subsystem': a subsystem reference file
[report.signal_objects, report.unresolved_signals] = signalObjects(mdl); % TOOL-143: measurement marks
report.named_signals = swc_measurements(mdl, 'signals');               % TOOL-185: measurement candidates
end

function [list, missing] = signalObjects(mdl)
%SIGNALOBJECTS TOOL-143: the Simulink.Signal objects the named signals and global data stores of the model take.
%   One entry per name: the properties of the object (effective storage class, data type, dimensions, unit,
%   description), where it lies (model or base workspace), the block that carries the name, and whether the model
%   really resolves the name to it (the port demands it, the model resolves all named signals, or it is a global
%   data store). `missing`: names whose port demands an object that does not exist - the generated init looks for
%   them in the data scripts around the model. Only read, nothing is changed.
list = {};
missing = {};
control = get_param(mdl, 'SignalResolutionControl');
resolveAll = any(strcmp(control, {'TryResolveAll', 'TryResolveAllWithWarning'}));
ports = find_system(mdl, 'FindAll', 'on', 'LookUnderMasks', 'all', 'FollowLinks', 'on', 'Type', 'port', ...
    'PortType', 'outport');
names = {};
blocks = {};
must = [];
atPort = {};
for k = 1:numel(ports)
    name = get_param(ports(k), 'Name');
    if isempty(name) || ~isvarname(name)
        continue;
    end
    demanded = strcmp(get_param(ports(k), 'MustResolveToSignalObject'), 'on');
    hit = find(strcmp(names, name), 1);
    if isempty(hit)
        names{end + 1} = name; %#ok<AGROW>
        blocks{end + 1} = strrep(get_param(ports(k), 'Parent'), sprintf('\n'), ' '); %#ok<AGROW>
        must(end + 1) = demanded; %#ok<AGROW>
        atPort{end + 1} = rootPortOf(mdl, ports(k)); %#ok<AGROW>
    elseif demanded
        must(hit) = true;
    end
end
stores = unique([get_param(find_system(mdl, 'LookUnderMasks', 'all', 'FollowLinks', 'on', 'BlockType', ...
    'DataStoreRead'), 'DataStoreName'); get_param(find_system(mdl, 'LookUnderMasks', 'all', 'FollowLinks', ...
    'on', 'BlockType', 'DataStoreWrite'), 'DataStoreName')]);
for k = 1:numel(stores)
    if ~any(strcmp(names, stores{k}))
        names{end + 1} = stores{k}; %#ok<AGROW>
        blocks{end + 1} = 'Data Store'; %#ok<AGROW>
        must(end + 1) = true; %#ok<AGROW>
        atPort{end + 1} = ''; %#ok<AGROW>
    end
end
for k = 1:numel(names)
    [obj, where] = signalObjectOf(mdl, names{k});
    if isempty(obj)
        if must(k) && ~strcmp(blocks{k}, 'Data Store')
            missing{end + 1} = names{k}; %#ok<AGROW>
        end
        continue;
    end
    entry = struct('name', names{k}, 'storage_class', storageClassOf(obj), 'data_type', char(obj.DataType), ...
        'dimensions', dimsText(obj.Dimensions), 'unit', '', 'description', char(obj.Description), ...
        'resolves', logical(must(k) || resolveAll), 'block', blocks{k}, 'workspace', where, ...
        'root_port', atPort{k});
    try
        entry.unit = char(obj.Unit);
    catch
        try
            entry.unit = char(obj.DocUnits);
        catch
        end
    end
    list{end + 1} = entry; %#ok<AGROW>
end
end

function port = rootPortOf(mdl, handle)
% the root Inport the signal comes from or the root Outport it feeds ('' if neither): with AUTOSAR, such a signal
% must keep storage class Auto (Embedded Coder refuses "non-Auto storage class ... not AUTOSAR feature compliant")
port = '';
parent = get_param(handle, 'Parent');
if strcmp(get_param(parent, 'Parent'), mdl) && strcmp(get_param(parent, 'BlockType'), 'Inport')
    port = get_param(parent, 'Name');
    return;
end
line = get_param(handle, 'Line');
if line == -1
    return;
end
destinations = get_param(line, 'DstBlockHandle');
for k = 1:numel(destinations)
    if destinations(k) ~= -1 && strcmp(get_param(destinations(k), 'BlockType'), 'Outport') && ...
            strcmp(get_param(get_param(destinations(k), 'Parent'), 'Type'), 'block_diagram')
        port = get_param(destinations(k), 'Name');
        return;
    end
end
end

function [obj, where] = signalObjectOf(mdl, name)
% the Simulink.Signal object a name resolves to: model workspace first, then the base workspace
obj = [];
where = '';
try
    mws = get_param(mdl, 'ModelWorkspace');
    if hasVariable(mws, name)
        value = getVariable(mws, name);
        if isa(value, 'Simulink.Signal')
            obj = value;
            where = 'model';
            return;
        end
    end
catch
end
if evalin('base', sprintf('exist(''%s'', ''var'') == 1 && isa(%s, ''Simulink.Signal'')', name, name))
    obj = evalin('base', name);
    where = 'base';
end
end

function sc = storageClassOf(obj)
% the storage class that counts: the custom one when StorageClass is 'Custom' (package storage classes)
sc = char(obj.CoderInfo.StorageClass);
if strcmp(sc, 'Custom')
    try
        sc = char(obj.CoderInfo.CustomStorageClass);
    catch
    end
end
end

function text = dimsText(dims)
if ischar(dims) || isstring(dims)
    text = char(dims);
else
    text = mat2str(dims);
end
end

function check = codegenCheck(mdl, signals)
%CODEGENCHECK What swc_prepare cannot switch automatically (TOOL-135): the Studio's import preview reports it as an
%   error before anything is written. Blocks with continuous states or without code generation are found by
%   their type; a model that does not run on a discrete fixed-step solver yet is also compiled (read only) to
%   count its continuous states. A model that does not compile (libraries or data missing) is reported as such.
%   TOOL-143: `signals` (signal_objects of the report) - the compiled type and size of each of them
%   (compiled_signals), and root ports with a symbolic dimension get theirs in compiled_ports (RTE-25).
if nargin < 2
    signals = {};
end
check = struct('compiled', false, 'continuous_states', -1, 'continuous_blocks', {{}}, ...
    'unsupported_blocks', {{}}, 'compile_error', '', 'unresolved_refs', {{}}, 'compiled_ports', {{}}, ...
    'compiled_signals', {{}});
continuous ={'Integrator', 'SecondOrderIntegrator', 'TransferFcn', 'StateSpace', 'ZeroPole', 'Derivative', ...
    'TransportDelay', 'VariableTransportDelay', 'VariableTimeDelay'};
unsupported = {'MATLABFcn', 'FromFile', 'ToFile', 'FromWorkspace', 'FromSpreadsheet', 'SignalEditor'};
blocks = find_system(mdl, 'LookUnderMasks', 'all', 'FollowLinks', 'on', 'Type', 'Block');
for k = 1:numel(blocks)
    type = get_param(blocks{k}, 'BlockType');
    label = strrep(blocks{k}, sprintf('\n'), ' ');
    if any(strcmp(type, continuous)) && isContinuous(blocks{k})
        check.continuous_blocks{end + 1} = sprintf('%s (%s)', label, type);
    elseif any(strcmp(type, unsupported))
        check.unsupported_blocks{end + 1} = sprintf('%s (%s)', label, type);
    end
end
check.unresolved = {};
% global data stores: read or written, but neither a Data Store Memory block of the model nor a Simulink.Signal of
% the workspace declares them - in the old project another model or a setup script did
used = unique([get_param(find_system(mdl, 'LookUnderMasks', 'all', 'FollowLinks', 'on', 'BlockType', ...
    'DataStoreRead'), 'DataStoreName'); get_param(find_system(mdl, 'LookUnderMasks', 'all', 'FollowLinks', ...
    'on', 'BlockType', 'DataStoreWrite'), 'DataStoreName')]);
declared = get_param(find_system(mdl, 'LookUnderMasks', 'all', 'FollowLinks', 'on', 'BlockType', ...
    'DataStoreMemory'), 'DataStoreName');
check.data_stores = {};
for k = 1:numel(used)
    if ~any(strcmp(used{k}, declared)) && ~evalin('base', sprintf( ...
            'exist(''%s'', ''var'') && isa(%s, ''Simulink.Signal'')', used{k}, used{k}))
        check.data_stores{end + 1} = used{k};
    end
end
subsystem = strcmp(get_param(mdl, 'BlockDiagramType'), 'subsystem');
discrete = ~subsystem && strcmp(get_param(mdl, 'SolverType'), 'Fixed-step') && ...
    strcmp(get_param(mdl, 'Solver'), 'FixedStepDiscrete');
% TOOL-138: a root port with an inherited data type gets its real type only in the compiled model - the import
% takes it from there instead of asking (HvsControl: bm_HvsErrorState is uint16, 'Inherit: auto' at the block)
inherited = inheritedTypes(mdl) || symbolicDimensions(mdl) || ~isempty(signals);
if discrete && ~inherited
    return;                                   % a discrete fixed-step model has no continuous states
end
compiled = mdl;
if subsystem
    % a subsystem reference file cannot be compiled on its own: its content goes into a scratch model in memory
    % (never saved) - the conversion swc_prepare makes on the copy
    compiled = 'nx_report_model';
    host = 'nx_report_host';
    closeIfLoaded(compiled);
    closeIfLoaded(host);
    new_system(host);
    new_system(compiled, 'Model');
    scratch = onCleanup(@() cellfun(@closeIfLoaded, {compiled, host}));
    add_block('simulink/Ports & Subsystems/Subsystem', [host '/Content']);
    Simulink.SubSystem.deleteContents([host '/Content']);
    Simulink.BlockDiagram.copyContentsToSubSystem(mdl, [host '/Content']);
    Simulink.SubSystem.copyContentsToBlockDiagram([host '/Content'], compiled);
end
try
    if ~discrete
        sizes = feval(compiled, [], [], [], 'sizes');  % compiles for the sizes and ends the compilation again
        check.continuous_states = sizes(1);
    end
    if inherited
        feval(compiled, [], [], [], 'compile');
        terminator = onCleanup(@() feval(compiled, [], [], [], 'term'));
        check.compiled_ports = compiledPorts(compiled);
        check.compiled_signals = compiledSignals(compiled, signals);
        clear terminator;
    end
    check.compiled = true;
catch err
    texts = messagesOf(err);
    check.compile_error = strrep(strjoin(texts(1:min(end, 8)), sprintf('\n')), compiled, mdl);
    [check.unresolved, check.unresolved_refs] = unresolvedNames(texts);
end
end

function typeInports(mdl, file)
% TOOL-138: the types the user gave the inherited root inports of an imported model (the Studio writes
% nx_port_types.json next to the COPY). Set in memory only - 'report' never saves - so that a model whose inputs
% come from outside compiles and its outports get their real type.
if ~isfile(file)
    return;
end
entries = jsondecode(fileread(file));
if isstruct(entries)
    entries = num2cell(entries);
end
for k = 1:numel(entries)
    blk = [mdl '/' strrep(entries{k}.name, '/', '//')];
    if getSimulinkBlockHandle(blk) ~= -1 && strcmp(get_param(blk, 'BlockType'), 'Inport') && ...
            startsWith(strtrim(get_param(blk, 'OutDataTypeStr')), 'Inherit')
        set_param(blk, 'OutDataTypeStr', entries{k}.type);
        fprintf('report: inport %s typed %s for the check (not saved)\n', entries{k}.name, entries{k}.type);
    end
end
end

function yes = inheritedTypes(mdl)
% whether a root data port has an inherited data type ('Inherit: auto', 'Inherit: via back propagation', ...)
blocks = [find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Inport');
          find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Outport')];
yes = false;
for k = 1:numel(blocks)
    if strcmp(get_param(blocks{k}, 'BlockType'), 'Inport') && strcmp(get_param(blocks{k}, 'OutputFunctionCall'), 'on')
        continue;
    end
    if startsWith(strtrim(get_param(blocks{k}, 'OutDataTypeStr')), 'Inherit')
        yes = true;
        return;
    end
end
end

function yes = symbolicDimensions(mdl)
% RTE-25: whether a root data port names its dimension by an expression (hvscmc.number_cell_voltages) - the
% compiled model gives the number the import writes next to it
blocks = [find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Inport');
          find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Outport')];
yes = false;
for k = 1:numel(blocks)
    if strcmp(get_param(blocks{k}, 'BlockType'), 'Inport') && strcmp(get_param(blocks{k}, 'OutputFunctionCall'), 'on')
        continue;
    end
    if any(isletter(get_param(blocks{k}, 'PortDimensions')))
        yes = true;
        return;
    end
end
end

function list = compiledSignals(sys, signals)
% TOOL-143: data type and dimensions of the named signals of the report in the compiled model sys (the output
% port carrying the name); a global data store has no port - its object must name type and size itself
list = {};
if isstruct(signals)
    signals = num2cell(signals);
end
for k = 1:numel(signals)
    name = signals{k}.name;
    port = find_system(sys, 'FindAll', 'on', 'LookUnderMasks', 'all', 'FollowLinks', 'on', 'Type', 'port', ...
        'PortType', 'outport', 'Name', name);
    if isempty(port)
        continue;
    end
    entry = struct('name', name, 'type', '', 'dimensions', '');
    try
        entry.type = char(get_param(port(1), 'CompiledPortDataType'));
        dims = get_param(port(1), 'CompiledPortDimensions');
        entry.dimensions = mat2str(dims(2:end));
    catch
    end
    list{end + 1} = entry; %#ok<AGROW>
end
end

function ports = compiledPorts(sys)
% TOOL-138: data type, dimensions and sample time of every root data port of the compiled model sys
ports = {};
blocks = [find_system(sys, 'SearchDepth', 1, 'BlockType', 'Inport');
          find_system(sys, 'SearchDepth', 1, 'BlockType', 'Outport')];
for k = 1:numel(blocks)
    blk = blocks{k};
    isIn = strcmp(get_param(blk, 'BlockType'), 'Inport');
    if isIn && strcmp(get_param(blk, 'OutputFunctionCall'), 'on')
        continue;
    end
    entry = struct('name', get_param(blk, 'Name'), 'type', '', 'dimensions', '', 'sample_time', '');
    handles = get_param(blk, 'PortHandles');
    if isIn
        port = handles.Outport(1);
    else
        port = handles.Inport(1);
    end
    try
        entry.type = char(get_param(port, 'CompiledPortDataType'));
    catch
    end
    try
        dims = get_param(port, 'CompiledPortDimensions');
        entry.dimensions = mat2str(dims(2:end));
    catch
    end
    try
        st = get_param(blk, 'CompiledSampleTime');
        if iscell(st)
            st = st{1};
        end
        entry.sample_time = mat2str(st);
    catch
    end
    ports{end + 1} = entry; %#ok<AGROW>
end
end

function texts = messagesOf(err)
% the message of an error and those of all its causes, each once
texts = {err.message};
for k = 1:numel(err.cause)
    texts = [texts, messagesOf(err.cause{k})]; %#ok<AGROW>
end
texts = unique(texts, 'stable');
end

function [names, refs] = unresolvedNames(texts)
% workspace variables the model uses and nobody defined (MATLAB's messages of the error and all its causes);
% refs: the whole reference as MATLAB names it ('hvsctrl.hvsopmode_on') - for the generated init (TOOL-138)
names = {};
refs = {};
for k = 1:numel(texts)
    found = regexp(texts{k}, ['(?:Unable to resolve the name|Unrecognized function or variable|' ...
        'Variable) ''([A-Za-z]\w*(?:\.[A-Za-z]\w*)*)'], 'tokens');
    for j = 1:numel(found)
        refs{end + 1} = found{j}{1}; %#ok<AGROW>
        names{end + 1} = strtok(found{j}{1}, '.'); %#ok<AGROW>
    end
end
names = unique(names);
refs = unique(refs);
end

function yes = isContinuous(blk)
% discrete variants (PID with Time domain 'Discrete-time', ...) are not continuous
yes = true;
try
    params = get_param(blk, 'DialogParameters');
    if isfield(params, 'TimeDomain')
        yes = ~contains(lower(get_param(blk, 'TimeDomain')), 'discrete');
    end
catch
end
end

function [names, subsystems] = runnablesOf(mdl)
%RUNNABLESOF The function-call inports of the root and the subsystem each one triggers (swc_runnable_subsystem:
%   <Swc>_task<Period>ms since TOOL-180, <Runnable>_Subsystem in older models - found by the trigger line).
names = {};
subsystems = {};
blocks = find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Inport');
for k = 1:numel(blocks)
    if ~strcmp(get_param(blocks{k}, 'OutputFunctionCall'), 'on')
        continue;
    end
    name = get_param(blocks{k}, 'Name');
    sys = swc_runnable_subsystem(mdl, name);
    names{end + 1} = name;             %#ok<AGROW>
    subsystems{end + 1} = sys;         %#ok<AGROW>
end
end

function info = portInfo(mdl, blk, name, runnableNames, subsystems)
info = struct();
info.name = name;
if strcmp(get_param(blk, 'BlockType'), 'Inport')
    info.direction = 'in';
else
    info.direction = 'out';
end
info.simulink_type = get_param(blk, 'OutDataTypeStr');
info.dimensions = get_param(blk, 'PortDimensions');   % TOOL-130: '-1' inherited, '1' scalar, '[2 3]' ...
info.sample_time = get_param(blk, 'SampleTime');      % TOOL-130: '-1' inherited, '0.01' ...
info.runnables = {};
info.attached = {};
info.free = true;
for r = 1:numel(subsystems)
    if isempty(subsystems{r})
        continue;
    end
    inner = [subsystems{r} '/' name];
    if ~isBlock(inner)
        continue;
    end
    info.runnables{end + 1} = runnableNames{r};
    use = attachedTo(inner, name, info.direction);
    use.runnable = runnableNames{r};
    info.attached{end + 1} = use;
    if ~use.placeholder
        info.free = false;
    end
end
end

function use = attachedTo(blk, name, direction)
%ATTACHEDTO What hangs on the port block inside a subsystem, and whether that is only the swc_create placeholder.
use = struct('runnable', '', 'blocks', {{}}, 'placeholder', true);
pc = get_param(blk, 'PortConnectivity');
if strcmp(direction, 'in')
    handles = pc(end).DstBlock;
    holder = [name '_Unused'];                     % Terminator swc_create puts behind an unused In block
else
    handles = pc(1).SrcBlock;
    holder = [name '_Default'];                    % Ground swc_create puts in front of an unconnected Out block
end
handles = handles(handles > 0);
for k = 1:numel(handles)
    other = get_param(handles(k), 'Name');
    use.blocks{end + 1} = other;                   %#ok<AGROW>
    if ~strcmp(other, holder)
        use.placeholder = false;
    end
end
end

function list = lineList(mdl, subsystems)
%LINELIST Every connection of the root and of the runnable subsystems as "<system>: <src port> -> <dst port>".
%   One entry per DESTINATION, not per line object (TOOL-107). A line that branches is one line object in
%   Simulink with several segments: the trunk carries every destination of its branches at once (DstBlockHandle
%   is then a vector - the old version passed that vector to sprintf and broke on the first branched line of a
%   real model), and each branch segment carries its own destination and inherits the source. Counting line
%   objects or segments would make the count depend on how Simulink cut the line into segments; counting
%   connections does not. So a line with two destinations is two entries, before and after a change alike -
%   never "one lost, one new" - and a branch that really went missing is exactly one entry fewer.
%   A port is named by its number, except a port of a subsystem, which is named by the In/Out block inside it:
%   removing a port renumbers the ports of a subsystem, and a number would make every later port look lost.
%   A branch that ends nowhere is "(offen)" - one entry per open end, so two open ends stay two.
list = {};
systems = [{mdl}, subsystems(~cellfun(@isempty, subsystems))];
for s = 1:numel(systems)
    segments = find_system(systems{s}, 'SearchDepth', 1, 'FindAll', 'on', 'Type', 'line');
    seen = {};
    for k = 1:numel(segments)
        seg = segments(k);
        src = sourcePort(seg);
        dst = get_param(seg, 'DstPortHandle');
        dst = dst(dst > 0);
        for d = 1:numel(dst)
            key = sprintf('%.17g>%.17g', src, dst(d));
            if any(strcmp(seen, key))
                continue;                          % the trunk and its branch both name this destination
            end
            seen{end + 1} = key;                   %#ok<AGROW>
            list{end + 1} = sprintf('%s: %s -> %s', systems{s}, portLabel(src), portLabel(dst(d))); %#ok<AGROW>
        end
        if isempty(dst) && isempty(get_param(seg, 'LineChildren'))
            list{end + 1} = sprintf('%s: %s -> (offen)', systems{s}, portLabel(src)); %#ok<AGROW>
        end
    end
end
list = sort(list);
end

function src = sourcePort(seg)
%SOURCEPORT Output port a line segment starts at; a branch segment may only know it through its parent.
src = get_param(seg, 'SrcPortHandle');
while (isempty(src) || src(1) <= 0)
    parent = get_param(seg, 'LineParent');
    if isempty(parent) || parent <= 0
        src = -1;
        return;
    end
    seg = parent;
    src = get_param(seg, 'SrcPortHandle');
end
src = src(1);
end

function name = portLabel(port)
%PORTLABEL "<block>/<port>" for one port handle; "(offen)" for a line end that is connected to nothing.
if port <= 0
    name = '(offen)';
    return;
end
blk = get_param(port, 'Parent');
block = strrep(get_param(blk, 'Name'), sprintf('\n'), ' ');
kind = get_param(port, 'PortType');
number = get_param(port, 'PortNumber');
if ~any(strcmp(kind, {'inport', 'outport'}))
    name = [block '/' kind];                        % trigger, enable, ifaction, ...
    return;
end
name = sprintf('%s/%d', block, number);
if strcmp(get_param(blk, 'BlockType'), 'SubSystem')
    if strcmp(kind, 'inport')
        inner = find_system(blk, 'SearchDepth', 1, 'LookUnderMasks', 'all', 'FollowLinks', 'on', ...
            'BlockType', 'Inport', 'Port', num2str(number));
    else
        inner = find_system(blk, 'SearchDepth', 1, 'LookUnderMasks', 'all', 'FollowLinks', 'on', ...
            'BlockType', 'Outport', 'Port', num2str(number));
    end
    if numel(inner) == 1
        name = [block '/' strrep(get_param(inner{1}, 'Name'), sprintf('\n'), ' ')];
    end
end
end

function info = ratePortInfo(blk, name, rates)
%RATEPORTINFO TOOL-184: a root port of a rate-based model. Its runnable is the rate of its sample time (the only one
%   of a single-rate model; none for an inherited rate of a multirate model), and what hangs on it is what hangs on
%   the root block - the placeholder of swc_ports 'apply' (<Port>_Unused / <Port>_Default at the root) counts as free.
info = struct();
info.name = name;
info.direction = directionOf(blk);
info.simulink_type = get_param(blk, 'OutDataTypeStr');
info.dimensions = get_param(blk, 'PortDimensions');
info.sample_time = get_param(blk, 'SampleTime');
runnable = '';
ms = round(str2double(info.sample_time) * 1000);
hit = find([rates.period_ms] == ms, 1);
if ~isempty(hit) && ms > 0
    runnable = rates(hit).name;
elseif numel(rates) == 1
    runnable = rates(1).name;
end
use = attachedTo(blk, name, info.direction);
use.runnable = runnable;
info.runnables = {};
if ~isempty(runnable)
    info.runnables = {runnable};
end
info.attached = {use};
info.free = use.placeholder;
end

%% ======================================================================================  apply

function report = doApply(mdl, mdlFile, planFile)
%DOAPPLY Applies the plan and saves once at the end.
%   An error in any action leaves save_system unreached and the cleanup closes the model without saving,
%   so a half applied plan never reaches the .slx - the file on disk is either the old state or the
%   complete new one.
plan = jsondecode(fileread(planFile));
if ~strcmp(plan.name, mdl)
    error('swc_ports:plan', 'plan is for %s, folder holds %s', plan.name, mdl);
end
backup = '';
if isfield(plan, 'backup')
    backup = char(plan.backup);
end
if isempty(backup) || ~isfile(backup)
    error('swc_ports:backup', ['the plan names no existing backup of %s. A port change is only applied ' ...
        'when a copy of the model is on disk (the Studio writes it before the call).'], mdlFile);
end
actions = toCell(plan.actions);
% TOOL-185: a measurement is written into <Swc>_init.m - its copy goes next to the one of the model
initFile = fullfile(fileparts(mdlFile), [mdl '_init.m']);
if isfile(initFile) && any(cellfun(@(a) strcmp(a.kind, 'measure'), actions))
    copyfile(initFile, fullfile(fileparts(backup), [mdl '_init.m']));
    fprintf('backup of %s: %s\n', initFile, fullfile(fileparts(backup), [mdl '_init.m']));
end

closeIfLoaded(mdl);
typesFn = writeTypes(plan, fileparts(mdlFile), fileparts(backup));      % RTE-30: before the model loads
load_system(mdlFile);
cleanup = onCleanup(@() closeIfLoaded(mdl));
before = collect(mdl, mdlFile);
useTypes(mdl, typesFn, actions);

% rename first (a name may be freed for a new port), then remove, then add, then the data types. TOOL-184: the
% runnables go around the ports - renamed first, removed after the ports that hung on them, added before the
% ports that are to hang on them
rateBased = swc_runnables(mdl, 'ratebased');
for kind = {'rename_runnable', 'rename', 'remove', 'remove_runnable', 'add_runnable', 'add', 'retype', 'measure'}
    for k = 1:numel(actions)
        a = actions{k};
        if ~strcmp(a.kind, kind{1})
            continue;
        end
        switch a.kind
            case 'rename'
                renamePort(mdl, a.name, a.new_name);
                fprintf('renamed port %s -> %s\n', a.name, a.new_name);
            case 'remove'
                removePort(mdl, a.name, isTrue(a, 'detach'));
                fprintf('removed port %s\n', a.name);
            case 'add'
                if rateBased && isfield(a, 'period_ms')          % TOOL-184: the root port carries the rate
                    addRatePort(mdl, a.name, a.direction, a.simulink_type, double(a.period_ms), textOf(a, 'default'));
                else
                    addPort(mdl, a.name, a.direction, a.simulink_type, cellstr(toCell(a.runnables)), ...
                        textOf(a, 'default'));
                end
                fprintf('added port %s (%s, %s)\n', a.name, a.direction, a.simulink_type);
            case 'retype'
                retypePort(mdl, a.name, a.simulink_type, textOf(a, 'default'));
                fprintf('port %s is now %s\n', a.name, a.simulink_type);
            case 'rename_runnable'                                % TOOL-184
                swc_runnables(mdl, 'rename', a.name, a.new_name);
                fprintf('renamed runnable %s -> %s\n', a.name, a.new_name);
            case 'remove_runnable'
                swc_runnables(mdl, 'remove', a.name);
                fprintf('removed runnable %s\n', a.name);
            case 'add_runnable'
                swc_runnables(mdl, 'add', a.name, double(a.period_ms));
                fprintf('added runnable %s (%d ms)\n', a.name, round(double(a.period_ms)));
            case 'measure'                                        % TOOL-185
                info = swc_measurements(mdl, 'write', a, fileparts(mdlFile));
                if ~isempty(info.renamed_from) && ~strcmp(info.renamed_from, info.name)
                    fprintf('signal %s is now %s\n', info.renamed_from, info.name);
                end
                fprintf('measurement %s: Simulink.Signal (%s), %d signal(s) resolve to it\n', ...
                    info.name, info.file, info.signals);
        end
    end
end
% TOOL-184: the AUTOSAR runnables named as in the swc.yaml once a runnable (or a rate) came, went or was renamed -
% the rule of swc_prepare, so swc_build writes the names tools/rtegen and the tasks of the project expect
if isfield(plan, 'runnables') && ~isempty(plan.runnables) && any(cellfun(@(a) any(strcmp(a.kind, ...
        {'add_runnable', 'remove_runnable', 'rename_runnable'})) || (rateBased && strcmp(a.kind, 'add')), actions))
    swc_runnables(mdl, 'map', toCell(plan.runnables), fileparts(mdlFile));
end

save_system(mdl, mdlFile);
report = collect(mdl, mdlFile);
close_system(mdl, 0);
clear cleanup;
fprintf('applied %d port changes to %s (%d -> %d blocks, %d -> %d lines)\n', numel(actions), mdlFile, ...
    numel(before.blocks), numel(report.blocks), numel(before.lines), numel(report.lines));
% TOOL-107: the same comparison the Studio makes (modelports.lost_content). A renamed block is named as renamed,
% not as gone - before, a rename printed "gone: <old name>" here while the Studio said nothing was lost, and the
% two statements contradicted each other in the same log. What is left over after the renames IS gone.
renames = struct('from', {}, 'to', {});
for k = 1:numel(actions)
    if strcmp(actions{k}.kind, 'rename')
        renames(end + 1) = struct('from', actions{k}.name, 'to', actions{k}.new_name); %#ok<AGROW>
    end
end
expected = cellfun(@(b) renamedText(b, renames), before.blocks, 'UniformOutput', false);
for k = 1:numel(before.blocks)
    if ~strcmp(expected{k}, before.blocks{k}) && any(strcmp(report.blocks, expected{k}))
        fprintf('  renamed: %s -> %s\n', before.blocks{k}, expected{k});
    end
end
lost = lostEntries(expected, report.blocks);
for k = 1:numel(lost)
    fprintf('  gone: %s\n', lost{k});
end
lost = lostEntries(cellfun(@(l) renamedText(l, renames), before.lines, 'UniformOutput', false), report.lines);
for k = 1:numel(lost)
    fprintf('  gone line: %s\n', lost{k});
end
if isfield(plan, 'report') && ~isempty(plan.report)
    writeJson(char(plan.report), report);
end
end

%% ------------------------------------------------------------------ rename

function renamePort(mdl, name, newName)
blk = [mdl '/' name];
if ~isBlock(blk)
    error('swc_ports:noPort', 'model %s has no port %s', mdl, name);
end
if isBlock([mdl '/' newName])
    error('swc_ports:exists', 'model %s already has a block %s', mdl, newName);
end
direction = directionOf(blk);
[arPort, arElement] = arMappingOf(mdl, name, direction);
[~, subsystems] = runnablesOf(mdl);
for r = 1:numel(subsystems)
    if isempty(subsystems{r})
        continue;
    end
    renameIfPresent([subsystems{r} '/' name], newName);
    renameIfPresent([subsystems{r} '/' name '_Unused'], [newName '_Unused']);
    renameIfPresent([subsystems{r} '/' name '_Default'], [newName '_Default']);
end
renameIfPresent([mdl '/' name '_Unused'], [newName '_Unused']);    % TOOL-184: placeholder at the root (rate-based)
renameIfPresent([mdl '/' name '_Default'], [newName '_Default']);
set_param(blk, 'Name', newName);
arRenamePort(mdl, name, newName, direction, arPort, arElement);
end

function renameIfPresent(blk, newName)
if isBlock(blk)
    set_param(blk, 'Name', newName);
end
end

%% ------------------------------------------------------------------ remove

function removePort(mdl, name, detach)
blk = [mdl '/' name];
if ~isBlock(blk)
    error('swc_ports:noPort', 'model %s has no port %s', mdl, name);
end
direction = directionOf(blk);
[arPort, ~] = arMappingOf(mdl, name, direction);
type = get_param(blk, 'OutDataTypeStr');
if swc_runnables(mdl, 'ratebased')
    removeRatePort(mdl, name, direction, type, detach);           % TOOL-184: the logic hangs at the root
    arRemovePort(mdl, arPort);
    return;
end
% every subsystem is asked first and only then is anything changed: a refusal must not leave the model half
% edited, not even in memory (on disk it could not happen anyway - an error never reaches save_system)
[~, subsystems] = runnablesOf(mdl);
places = {};
for r = 1:numel(subsystems)
    if isempty(subsystems{r}) || ~isBlock([subsystems{r} '/' name])
        continue;
    end
    use = attachedTo([subsystems{r} '/' name], name, direction);
    if ~use.placeholder && ~detach
        error('swc_ports:attached', ['port %s is used in %s (%s); removing it would take that logic with ' ...
            'it. Release it on purpose (detach) or change the model first.'], name, subsystems{r}, ...
            strjoin(use.blocks, ', '));
    end
    places{end + 1} = {subsystems{r}, use.placeholder};   %#ok<AGROW>
end
for k = 1:numel(places)
    detachInner(places{k}{1}, name, direction, type, places{k}{2});
end
removeLines(blk);
delete_block(blk);
arRemovePort(mdl, arPort);
end

function detachInner(sys, name, direction, type, placeholder)
%DETACHINNER Takes the port block out of one subsystem without deleting a block of the user.
%   The placeholder swc_create made (<name>_Unused / <name>_Default) goes with the port - it is not the work
%   of anybody. Real logic keeps its block and gets a stub in the port's place: a Constant of the port's type
%   for what read the port, a Terminator for what wrote it. Both are named <name>_Removed, so the diagram says
%   where the port used to be and the model still builds.
inner = [sys '/' name];
pc = get_param(inner, 'PortConnectivity');
pos = get_param(inner, 'Position');
if strcmp(direction, 'in')
    targets = pc(end).DstBlock;
    ports = pc(end).DstPort;
    holder = [sys '/' name '_Unused'];
else
    targets = pc(1).SrcBlock;
    ports = pc(1).SrcPort;
    holder = [sys '/' name '_Default'];
end
removeLines(inner);
delete_block(inner);
if isBlock(holder)
    % RTE-30: the placeholder may still hang on the port beside the logic of the user - it goes, it is no target
    keep = targets ~= getSimulinkBlockHandle(holder);
    targets = targets(keep);
    ports = ports(keep);
    removeLines(holder);
    delete_block(holder);
end
if placeholder
    renumber(sys, direction);
    return;
end
stub = [sys '/' name '_Removed'];
if strcmp(direction, 'in')
    add_block('simulink/Sources/Constant', stub, 'Position', pos, 'Value', stubValue(type), ...
        'OutDataTypeStr', type);
    out = get_param(stub, 'PortHandles');
    for k = 1:numel(targets)
        if targets(k) <= 0 || ~isnumeric(ports(k))
            continue;
        end
        dst = get_param(targets(k), 'PortHandles');
        add_line(sys, out.Outport(1), dst.Inport(ports(k) + 1), 'autorouting', 'on');
    end
else
    add_block('simulink/Sinks/Terminator', stub, 'Position', pos);
    dst = get_param(stub, 'PortHandles');
    if ~isempty(targets) && targets(1) > 0 && isnumeric(ports(1))
        src = get_param(targets(1), 'PortHandles');
        add_line(sys, src.Outport(ports(1) + 1), dst.Inport(1), 'autorouting', 'on');
    end
end
renumber(sys, direction);
end

function value = stubValue(type)
% RTE-30: Value of the Constant <Port>_Removed - 0 (also the ground value of every element of a record), for an
% enumeration its default literal (0 need not be a literal of it)
value = '0';
if startsWith(type, 'Enum:')
    name = strtrim(type(numel('Enum:') + 1:end));
    try
        value = sprintf('%s.%s', name, char(Simulink.data.getEnumTypeInfo(name, 'DefaultValue')));
    catch err
        warning('swc_ports:enum', 'default literal of %s unknown (%s), the stub takes 0', name, err.message);
    end
end
end

function renumber(sys, direction)
%RENUMBER Closes the gap a deleted In/Out block leaves, so the subsystem ports stay 1..n in their old order.
if strcmp(direction, 'in')
    blocks = find_system(sys, 'SearchDepth', 1, 'BlockType', 'Inport');
else
    blocks = find_system(sys, 'SearchDepth', 1, 'BlockType', 'Outport');
end
numbers = cellfun(@(b) str2double(get_param(b, 'Port')), blocks);
[~, order] = sort(numbers);
for k = 1:numel(order)
    set_param(blocks{order(k)}, 'Port', num2str(k));
end
end

function removeRatePort(mdl, name, direction, type, detach)
%REMOVERATEPORT TOOL-184: a root port of a rate-based model. The same rule as in a subsystem, one level up: the
%   placeholder goes with the port, logic of the user only with `detach` - and then keeps every block, with
%   <Port>_Removed in the port's place. A Constant stub of an inport keeps the port's sample time, so the logic
%   behind it stays in its rate (a Constant alone would be constant and could move it to another task).
blk = [mdl '/' name];
use = attachedTo(blk, name, direction);
if ~use.placeholder && ~detach
    error('swc_ports:attached', ['port %s is used at the root of %s (%s); removing it would take that logic ' ...
        'with it. Release it on purpose (detach) or change the model first.'], name, mdl, ...
        strjoin(use.blocks, ', '));
end
sampleTime = get_param(blk, 'SampleTime');
detachInner(mdl, name, direction, type, use.placeholder);
stub = [mdl '/' name '_Removed'];
if ~use.placeholder && strcmp(direction, 'in') && isBlock(stub) && ~strcmp(strtrim(sampleTime), '-1')
    set_param(stub, 'SampleTime', sampleTime);
end
end

%% ------------------------------------------------------------------ add

function addPort(mdl, name, direction, type, runnables, default)
%ADDPORT RTE-30: `default` - Value of the Constant placeholder of a provided record/enumeration port ('' = Ground).
if nargin < 6
    default = '';
end
if isBlock([mdl '/' name])
    error('swc_ports:exists', 'model %s already has a block %s', mdl, name);
end
[runnableNames, subsystems] = runnablesOf(mdl);
for r = 1:numel(runnables)
    if ~any(strcmp(runnables{r}, runnableNames))
        error('swc_ports:noRunnable', 'model %s has no runnable %s', mdl, runnables{r});
    end
end
if strcmp(direction, 'in')
    row = numel(find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Inport'));
    blk = add_block('simulink/Sources/In1', [mdl '/' name], 'Position', rootPos(30, row), ...
        'BackgroundColor', 'green');                              % TOOL-180: inports green, outports red
    set_param(blk, 'OutDataTypeStr', type, 'PortDimensions', '1');
else
    row = numel(find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Outport'));
    blk = add_block('simulink/Sinks/Out1', [mdl '/' name], 'Position', rootPos(620, row), ...
        'BackgroundColor', 'red');
    set_param(blk, 'OutDataTypeStr', type);
end
busAsStruct(blk, type);                                           % RTE-15/RTE-30: a record is a nonvirtual bus
for r = 1:numel(runnableNames)
    if ~any(strcmp(runnableNames{r}, runnables)) || isempty(subsystems{r})
        continue;
    end
    sys = subsystems{r};
    if strcmp(direction, 'in')
        index = numel(find_system(sys, 'SearchDepth', 1, 'BlockType', 'Inport')) + 1;
        add_block('simulink/Sources/In1', [sys '/' name], 'Position', rootPos(40, index - 1), ...
            'Port', num2str(index), 'BackgroundColor', 'green');
        add_block('simulink/Sinks/Terminator', [sys '/' name '_Unused'], 'Position', rootPos(160, index - 1));
        add_line(sys, [name '/1'], [name '_Unused/1'], 'autorouting', 'on');
        add_line(mdl, [name '/1'], sprintf('%s/%d', get_param(sys, 'Name'), index), 'autorouting', 'on');
    else
        index = numel(find_system(sys, 'SearchDepth', 1, 'BlockType', 'Outport')) + 1;
        add_block('simulink/Sinks/Out1', [sys '/' name], 'Position', rootPos(600, index - 1), ...
            'Port', num2str(index), 'BackgroundColor', 'red');
        addDefault([sys '/' name '_Default'], rootPos(480, index - 1), type, default);
        add_line(sys, [name '_Default/1'], [name '/1'], 'autorouting', 'on');
        add_line(mdl, sprintf('%s/%d', get_param(sys, 'Name'), index), [name '/1'], 'autorouting', 'on');
    end
end
arAddPort(mdl, name, direction, type);
end

function addRatePort(mdl, name, direction, type, periodMs, default)
%ADDRATEPORT TOOL-184: a new port of a rate-based model - there is no runnable subsystem, the root is the logic. The
%   root block gets the sample time of its runnable's period (that is what makes it belong to that runnable, and
%   a new period is a new rate of the model) and the placeholder of swc_create beside it: an inport ends in a
%   Terminator <Port>_Unused, an outport is fed by a Ground <Port>_Default. The blocks go below everything the
%   root holds, so no block of the user is covered. RTE-30: `default` as in addPort.
if nargin < 6
    default = '';
end
if isBlock([mdl '/' name])
    error('swc_ports:exists', 'model %s already has a block %s', mdl, name);
end
if ~(periodMs > 0)
    error('swc_ports:period', 'port %s: no period for the rate-based model %s', name, mdl);
end
sampleTime = sprintf('%g', periodMs / 1000);
top = belowRoot(mdl);
if strcmp(direction, 'in')
    add_block('simulink/Sources/In1', [mdl '/' name], 'Position', [30 top 60 top + 14], ...
        'BackgroundColor', 'green');
    set_param([mdl '/' name], 'OutDataTypeStr', type, 'PortDimensions', '1', 'SampleTime', sampleTime);
    add_block('simulink/Sinks/Terminator', [mdl '/' name '_Unused'], 'Position', [160 top - 3 180 top + 17]);
    add_line(mdl, [name '/1'], [name '_Unused/1'], 'autorouting', 'on');
else
    add_block('simulink/Sinks/Out1', [mdl '/' name], 'Position', [620 top 650 top + 14], ...
        'BackgroundColor', 'red');
    set_param([mdl '/' name], 'OutDataTypeStr', type, 'SampleTime', sampleTime);
    addDefault([mdl '/' name '_Default'], [500 top - 3 520 top + 17], type, default);
    add_line(mdl, [name '_Default/1'], [name '/1'], 'autorouting', 'on');
end
busAsStruct([mdl '/' name], type);                                % RTE-30
arAddPort(mdl, name, direction, type);
end

function top = belowRoot(mdl)
% y below the lowest block of the root (40 in an empty model)
top = 40;
blocks = find_system(mdl, 'SearchDepth', 1, 'Type', 'Block');
for k = 1:numel(blocks)
    pos = get_param(blocks{k}, 'Position');
    top = max(top, pos(4) + 30);
end
end

%% ------------------------------------------------------------------ data type

function retypePort(mdl, name, type, default)
%RETYPEPORT Data type of a root port. Only the root block carries it - the In/Out blocks of the subsystems
%   inherit it, so nothing of the logic is touched. Whether the logic behind it still fits is a question for
%   the user; the plan warns about it.
%   RTE-30: the placeholder <Port>_Default of a provided port follows the type - a Ground is no bus and an
%   enumeration starts at its default literal, so a record/enumeration gets the Constant of swc_create (`default`),
%   and such a Constant becomes a Ground again when the port gets a platform or fixed-point type. Only the
%   placeholder: a block of the user is never touched.
if nargin < 4
    default = '';
end
blk = [mdl '/' name];
if ~isBlock(blk)
    error('swc_ports:noPort', 'model %s has no port %s', mdl, name);
end
set_param(blk, 'OutDataTypeStr', type);
busAsStruct(blk, type);
if ~strcmp(directionOf(blk), 'out')
    return;
end
[~, subsystems] = runnablesOf(mdl);
% TOOL-184: the placeholder of a rate-based model is at the root
systems = [{mdl}, subsystems(~cellfun(@isempty, subsystems))];
for s = 1:numel(systems)
    swapDefault(systems{s}, name, type, default);
end
end

function swapDefault(sys, name, type, default)
% RTE-30: the placeholder <name>_Default in sys (if there is one) as Ground or Constant of the new type
holder = [sys '/' name '_Default'];
if ~isBlock(holder) || ~isBlock([sys '/' name])
    return;
end
kind = get_param(holder, 'BlockType');
if ~any(strcmp(kind, {'Ground', 'Constant'}))
    return;                                       % not a placeholder of swc_create / swc_ports
end
if strcmp(kind, 'Constant') && ~isempty(default)
    set_param(holder, 'OutDataTypeStr', type, 'Value', default);
    return;
end
if strcmp(kind, 'Ground') && isempty(default)
    return;
end
if strcmp(kind, 'Constant') && ~startsWith(get_param(holder, 'OutDataTypeStr'), {'Bus:', 'Enum:'})
    return;                                       % a Constant the placeholder rule did not make
end
pos = get_param(holder, 'Position');
removeLines(holder);
delete_block(holder);
addDefault(holder, pos, type, default);
add_line(sys, [name '_Default/1'], [name '/1'], 'autorouting', 'on');
end

function addDefault(path, pos, type, default)
% placeholder in front of a new provided port: a Ground, or (RTE-30, a record or an enumeration) a Constant of its
% type with `default` as Value - the rule of swc_create.addPlaceholders
if isempty(default)
    add_block('simulink/Sources/Ground', path, 'Position', pos);
else
    add_block('simulink/Sources/Constant', path, 'Position', [pos(1) - 40, pos(2), pos(3), pos(4)], ...
        'OutDataTypeStr', type, 'Value', default);
end
end

function busAsStruct(blk, type)
% RTE-15: a record of swc.yaml is a nonvirtual bus at the root port (a struct in the code and the RTE)
if startsWith(type, 'Bus:')
    set_param(blk, 'BusOutputAsStruct', 'on');
elseif strcmp(get_param(blk, 'BusOutputAsStruct'), 'on')
    set_param(blk, 'BusOutputAsStruct', 'off');
end
end

%% ------------------------------------------------------------------ named types (RTE-30)

function fn = writeTypes(plan, swcDir, backupDir)
%WRITETYPES <Swc>_types.m of the plan (types_file): written when it differs from the file next to the model (the old
%   one goes next to the backup of the model first), then run - the model and the new ports need the types before
%   load_system. '' when the plan carries none.
fn = '';
if ~isfield(plan, 'types_file') || isempty(plan.types_file)
    return;
end
file = fullfile(swcDir, char(plan.types_file.name));
text = char(plan.types_file.text);
old = '';
if isfile(file)
    old = strrep(fileread(file), sprintf('\r\n'), newline);
end
if ~strcmp(old, text)
    if isfile(file)
        copyfile(file, fullfile(backupDir, char(plan.types_file.name)));
        fprintf('backup of %s: %s\n', file, fullfile(backupDir, char(plan.types_file.name)));
    end
    fid = fopen(file, 'w', 'n', 'UTF-8');
    if fid < 0
        error('swc_ports:write', 'cannot write %s', file);
    end
    fwrite(fid, text, 'char');
    fclose(fid);
    fprintf('wrote %s from types: of swc.yaml\n', file);
end
[~, fn] = fileparts(file);
addpath(swcDir);
clear(fn);                                        % a file written again in this session
feval(fn);
end

function useTypes(mdl, fn, actions)
%USETYPES RTE-30: a model that gets its first port of a named type gets what swc_create gives a model with named
%   types: the PreLoadFcn that defines them when it loads, and SimGenImportedTypeDefs for an enumeration (RTE-04).
if isempty(fn)
    return;
end
pre = get_param(mdl, 'PreLoadFcn');
if isempty(strtrim(pre))
    set_param(mdl, 'PreLoadFcn', sprintf('if exist(''%s'', ''file'') == 2, %s(); end', fn, fn));
    fprintf('PreLoadFcn of %s runs %s\n', mdl, fn);
elseif ~contains(pre, fn)
    fprintf('note: the PreLoadFcn of %s does not run %s - swc_load_init and swc_build run it anyway\n', mdl, fn);
end
types = cellfun(@(a) textOf(a, 'simulink_type'), actions, 'UniformOutput', false);
if any(startsWith(types, 'Enum:'))
    set_param(mdl, 'SimGenImportedTypeDefs', 'on');
end
end

function text = textOf(s, field)
text = '';
if isfield(s, field) && ~isempty(s.(field))
    text = char(s.(field));
end
end

%% ------------------------------------------------------------------ AUTOSAR side

function [arPort, arElement] = arMappingOf(mdl, name, direction)
%ARMAPPINGOF AUTOSAR port and data element a root port is mapped to ('' if the model has no AUTOSAR mapping).
arPort = '';
arElement = '';
if ~isAutosar(mdl)
    return;
end
slMap = autosar.api.getSimulinkMapping(mdl);
try
    if strcmp(direction, 'in')
        [arPort, arElement] = getInport(slMap, name);
    else
        [arPort, arElement] = getOutport(slMap, name);
    end
catch err
    warning('swc_ports:mapping', 'port %s has no AUTOSAR mapping (%s)', name, err.message);
end
end

function arAddPort(mdl, name, direction, type)
if ~isAutosar(mdl)
    return;
end
arProps = autosar.api.getAUTOSARProperties(mdl);
ifPkg = get(arProps, 'XmlOptions', 'InterfacePackage');
iface = [ifPkg '/' name];
if ~any(strcmp(find(arProps, ifPkg, 'SenderReceiverInterface', 'PathType', 'FullyQualified'), iface))
    addPackageableElement(arProps, 'SenderReceiverInterface', ifPkg, name, 'IsService', false);
end
if ~any(strcmp(find(arProps, iface, 'VariableData', 'PathType', 'FullyQualified'), [iface '/' name]))
    try
        add(arProps, iface, 'DataElements', name, 'Type', type);
    catch                                    % the data types only exist after export: the mapping gives the type
        add(arProps, iface, 'DataElements', name);
    end
end
comp = componentPath(arProps);
if strcmp(direction, 'in')
    [kindFind, kindAdd] = deal('DataReceiverPort', 'ReceiverPorts');
else
    [kindFind, kindAdd] = deal('DataSenderPort', 'SenderPorts');
end
if ~any(strcmp(find(arProps, comp, kindFind, 'PathType', 'FullyQualified'), [comp '/' name]))
    add(arProps, comp, kindAdd, name, 'Interface', name);
end
slMap = autosar.api.getSimulinkMapping(mdl);
if strcmp(direction, 'in')
    mapInport(slMap, name, name, name, 'ExplicitReceive');
else
    mapOutport(slMap, name, name, name, 'ExplicitSend');
end
end

function arRenamePort(mdl, name, newName, direction, arPort, arElement)
if ~isAutosar(mdl) || isempty(arPort)
    return;
end
arProps = autosar.api.getAUTOSARProperties(mdl);
comp = componentPath(arProps);
iface = get(arProps, [comp '/' arPort], 'Interface');
if ~isempty(arElement) && ~isempty(iface)
    set(arProps, [iface '/' arElement], 'Name', newName);
end
if ~isempty(iface) && strcmp(pathLeaf(iface), name)
    set(arProps, iface, 'Name', newName);
end
set(arProps, [comp '/' arPort], 'Name', newName);
slMap = autosar.api.getSimulinkMapping(mdl);
if strcmp(direction, 'in')
    mapInport(slMap, newName, newName, newName, 'ExplicitReceive');
else
    mapOutport(slMap, newName, newName, newName, 'ExplicitSend');
end
end

function arRemovePort(mdl, arPort)
if ~isAutosar(mdl) || isempty(arPort)
    return;
end
arProps = autosar.api.getAUTOSARProperties(mdl);
comp = componentPath(arProps);
iface = get(arProps, [comp '/' arPort], 'Interface');
deleteElement(arProps, [comp '/' arPort]);
if ~isempty(iface) && isempty(usersOfInterface(arProps, comp, iface))
    deleteElement(arProps, iface);
end
end

function names = usersOfInterface(arProps, comp, iface)
names = {};
for kind = {'DataReceiverPort', 'DataSenderPort'}
    ports = find(arProps, comp, kind{1}, 'PathType', 'FullyQualified');
    for k = 1:numel(ports)
        if strcmp(get(arProps, ports{k}, 'Interface'), iface)
            names{end + 1} = ports{k};   %#ok<AGROW>
        end
    end
end
end

function deleteElement(arProps, path)
try
    delete(arProps, path);
catch err
    warning('swc_ports:autosar', 'AUTOSAR element %s could not be removed (%s)', path, err.message);
end
end

function comp = componentPath(arProps)
comp = find(arProps, [], 'AtomicComponent', 'PathType', 'FullyQualified');
if isempty(comp)
    error('swc_ports:autosar', 'the model has no AUTOSAR component');
end
comp = comp{1};
end

function yes = isAutosar(mdl)
yes = strcmp(get_param(mdl, 'SystemTargetFile'), 'autosar.tlc');
end

function leaf = pathLeaf(path)
parts = strsplit(path, '/');
leaf = parts{end};
end

%% ------------------------------------------------------------------ helpers

function text = renamedText(text, renames)
%RENAMEDTEXT A block path or line of the old model with the port renames applied (as modelports.renamed_text).
for k = 1:numel(renames)
    for suffix = {'_Unused', '_Default', ''}
        old = regexptranslate('escape', [renames(k).from suffix{1}]);
        text = regexprep(text, ['(?<![A-Za-z0-9_])' old '(?![A-Za-z0-9_])'], [renames(k).to suffix{1}]);
    end
end
end

function lost = lostEntries(before, after)
%LOSTENTRIES Entries of `before` missing in `after`, counted: an entry that was there twice and is there once is
%   lost once (two open ends of one line, TOOL-107) - setdiff would hide that.
lost = {};
remaining = after;
for k = 1:numel(before)
    hit = find(strcmp(remaining, before{k}), 1);
    if isempty(hit)
        lost{end + 1} = before{k};   %#ok<AGROW>
    else
        remaining(hit) = [];
    end
end
end

function direction = directionOf(blk)
if strcmp(get_param(blk, 'BlockType'), 'Inport')
    direction = 'in';
else
    direction = 'out';
end
end

function removeLines(blk)
%REMOVELINES Deletes every line attached to a block (delete_block refuses a connected block).
lh = get_param(blk, 'LineHandles');
handles = [];
for field = fieldnames(lh)'
    value = lh.(field{1});
    handles = [handles; value(:)];   %#ok<AGROW>
end
handles = unique(handles(handles > 0));
for k = 1:numel(handles)
    if ishandle(handles(k))
        delete_line(handles(k));
    end
end
end

function yes = isBlock(path)
yes = getSimulinkBlockHandle(path) > 0;
end

function pos = rootPos(x, row)
y = 40 + 50 * row;
pos = [x y x + 30 y + 14];
end

function c = toCell(v)
if isempty(v)
    c = {};
elseif iscell(v)
    c = reshape(v, 1, []);
elseif ischar(v)
    c = {v};
else
    c = num2cell(reshape(v, 1, []));
end
end

function yes = isTrue(s, field)
yes = isfield(s, field) && ~isempty(s.(field)) && all(logical(s.(field)));
end

function writeJson(file, data)
fid = fopen(file, 'w', 'n', 'UTF-8');
if fid < 0
    error('swc_ports:write', 'cannot write %s', file);
end
closer = onCleanup(@() fclose(fid));
fprintf(fid, '%s', jsonencode(data));
end

function closeIfLoaded(mdl)
if bdIsLoaded(mdl)
    close_system(mdl, 0);
end
end
