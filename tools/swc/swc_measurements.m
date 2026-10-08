% SPDX-License-Identifier: Apache-2.0
function out = swc_measurements(mdl, action, varargin)
%SWC_MEASUREMENTS Measurements of a loaded component model: its named signals, and writing one into it (TOOL-185).
%   A measurement of a Simulink component is a Simulink.Signal object with an exporting storage class at a named
%   signal of the model (swc_yaml.EXPORTING_STORAGE_CLASSES): Embedded Coder then defines a global variable of that
%   name, the RTE declares it and XCP reads it. Until TOOL-185 only Simulink made such an object - a measurement
%   entered in the Studio (swc.yaml measurements:) never reached the model, and rtegen reported it missing in the
%   generated code (model_measurements). swc_ports 'apply' now writes it, for an imported model and for one the
%   Studio created alike; the model is the work of hours, so only the one line and the one object are touched.
%
%   swc_measurements(mdl, 'signals')   struct cell of the named signals of the model (the candidates the Studio
%                                      offers for a measurement): name, block (the source block), object (a
%                                      Simulink.Signal of that name exists), must_resolve, port_signal (the signal
%                                      of a port block - a root port must keep storage class Auto for AUTOSAR, and a
%                                      port is measurable anyway), linked (inside a library link: not writable)
%   swc_measurements(mdl, 'write', a, swcDir)
%                                      a: struct name, simulink_type, dimensions (number, 0 = leave), unit,
%                                      description, optional signal (the named signal to take: it is renamed to
%                                      `name`). Writes the Simulink.Signal (storage class ExportedGlobal, type,
%                                      size) as a marked block into <swcDir>/<mdl>_init.m - the script swc_ports,
%                                      swc_prepare and swc_build run before they load the model (swc_load_init) -
%                                      creates it in the base workspace right away, and sets "Signal name must
%                                      resolve to Simulink signal object" at the signal, so the name really takes
%                                      the object.
%                                      Why not the model workspace: measured with R2024a (TOOL-185), Simulink sets
%                                      every Simulink.Signal of the MODEL workspace back to storage class Auto
%                                      ("must be set to 'Auto' ... Change the storage class ... in the Code Mappings
%                                      Editor"), and the code then defines no variable. An object of that name in
%                                      the model workspace is removed for that reason (it would shadow the script's).
%                                      An object of the script that is not the Studio's block is not rewritten:
%                                      exporting already, it is only resolved; otherwise refused with the script
%                                      to change.

switch lower(char(action))
    case 'signals'
        out = namedSignals(mdl);
    case 'write'
        out = writeMeasurement(mdl, varargin{1}, char(varargin{2}));
    otherwise
        error('swc_measurements:action', 'unknown action %s (signals or write)', char(action));
end
end

%% ======================================================================================  reading

function list = namedSignals(mdl)
list = {};
ports = signalPorts(mdl, '');
names = {};
for k = 1:numel(ports)
    name = get_param(ports(k), 'Name');
    if isempty(name) || ~isvarname(name)
        continue;
    end
    parent = get_param(ports(k), 'Parent');
    hit = find(strcmp(names, name), 1);
    portSignal = isPortSignal(ports(k));
    if ~isempty(hit)
        list{hit}.port_signal = list{hit}.port_signal || portSignal;
        list{hit}.count = list{hit}.count + 1;
        continue;
    end
    names{end + 1} = name; %#ok<AGROW>
    list{end + 1} = struct('name', name, 'block', strrep(parent, sprintf('\n'), ' '), ...
        'object', hasObject(mdl, name), ...
        'must_resolve', strcmp(get_param(ports(k), 'MustResolveToSignalObject'), 'on'), ...
        'port_signal', portSignal, 'linked', isLinked(parent), 'count', 1); %#ok<AGROW>
end
end

function ports = signalPorts(mdl, name)
% the output ports of the whole model (in masks too, not inside library links) - those carrying `name` if given
args = {'FindAll', 'on', 'LookUnderMasks', 'all', 'FollowLinks', 'on', 'Type', 'port', 'PortType', 'outport'};
if ~isempty(name)
    args = [args, {'Name', name}];
end
ports = find_system(mdl, args{:});
end

function yes = isPortSignal(port)
% the signal of an Inport block (at any level) or one that ends in an Outport block: in an export-function model a
% subsystem port continues at the root port, so its signal IS the port's (AUTOSAR: storage class Auto)
parent = get_param(port, 'Parent');
yes = strcmp(get_param(parent, 'BlockType'), 'Inport');
if yes
    return;
end
line = get_param(port, 'Line');
if line == -1
    return;
end
destinations = get_param(line, 'DstBlockHandle');
for k = 1:numel(destinations)
    if destinations(k) ~= -1 && strcmp(get_param(destinations(k), 'BlockType'), 'Outport')
        yes = true;
        return;
    end
end
end

function yes = isLinked(block)
% whether the block lies in (or is) a library link - its signals belong to the library, not to the model
yes = false;
while ~isempty(block) && ~strcmp(get_param(block, 'Type'), 'block_diagram')
    if ~strcmp(get_param(block, 'LinkStatus'), 'none') && ~strcmp(get_param(block, 'LinkStatus'), 'inactive')
        yes = true;
        return;
    end
    block = get_param(block, 'Parent');
end
end

function yes = hasObject(mdl, name)
[obj, ~] = objectOf(mdl, name);
yes = ~isempty(obj);
end

function [obj, where] = objectOf(mdl, name)
% the Simulink.Signal a name resolves to: model workspace first, then the base workspace (as swc_ports reads it)
obj = [];
where = '';
mws = get_param(mdl, 'ModelWorkspace');
if hasVariable(mws, name)
    value = getVariable(mws, name);
    if isa(value, 'Simulink.Signal')
        obj = value;
        where = 'model';
    end
    return;
end
if evalin('base', sprintf('exist(''%s'', ''var'') == 1 && isa(%s, ''Simulink.Signal'')', name, name))
    obj = evalin('base', name);
    where = 'base';
end
end

%% ======================================================================================  writing

function info = writeMeasurement(mdl, a, swcDir)
name = char(a.name);
if ~isvarname(name)
    error('swc_measurements:name', 'measurement %s: not a valid signal name', name);
end
signal = '';
if isfield(a, 'signal') && ~isempty(a.signal)
    signal = char(a.signal);
end
if ~isempty(signal) && ~strcmp(signal, name)
    renameSignal(mdl, signal, name);
end
ports = signalPorts(mdl, name);
if isempty(ports)
    error('swc_measurements:noSignal', ['measurement %s: the model has no signal of that name - name a line %s ' ...
        'in Simulink or choose a named signal of the model'], name, name);
end
for k = 1:numel(ports)
    parent = get_param(ports(k), 'Parent');
    if isPortSignal(ports(k))
        error('swc_measurements:port', ['measurement %s: the signal belongs to a port (%s) - for AUTOSAR it keeps ' ...
            'storage class Auto, and the port is measurable anyway'], name, parent);
    end
    if isLinked(parent)
        error('swc_measurements:linked', 'measurement %s: the signal lies in a library link (%s)', name, parent);
    end
end
% an object of the model workspace is always Auto in R2024a and would shadow the script's: its unit and description
% are kept, the object goes
old = [];
mws = get_param(mdl, 'ModelWorkspace');
if hasVariable(mws, name)
    old = getVariable(mws, name);
    if ~strcmp(mws.DataSource, 'Model File')
        error('swc_measurements:workspace', ['measurement %s: the model workspace of %s is loaded from %s (%s) ' ...
            '- remove the object %s there, the measurement lives in %s_init.m'], name, mdl, mws.DataSource, ...
            char(mws.FileName), name, mdl);
    end
    clear(mws, name);
end
initFile = fullfile(swcDir, [mdl '_init.m']);
text = '';
if isfile(initFile)
    text = fileread(initFile);
end
[from, to] = blockOf(text, name);
inBase = evalin('base', sprintf('exist(''%s'', ''var'') == 1 && isa(%s, ''Simulink.Signal'')', name, name));
if inBase && isempty(from)
    obj = evalin('base', name);
    if ~any(strcmp(storageClassOf(obj), {'ExportedGlobal', 'ExportToFile'}))
        error('swc_measurements:script', ['measurement %s: its Simulink.Signal comes from a script of the base ' ...
            'workspace (%s_init.m) with storage class %s - set ExportedGlobal there'], name, mdl, storageClassOf(obj));
    end
    where = 'base';                                    % the user's own object exports already: only resolved
else
    block = initBlock(name, a, old);
    if isempty(from)
        if ~isempty(text) && ~endsWith(text, newline)
            text = [text newline];
        end
        text = [text block];
    else
        text = [text(1:from - 1) block text(to + 1:end)];
    end
    fid = fopen(initFile, 'w', 'n', 'UTF-8');
    if fid < 0
        error('swc_measurements:write', 'measurement %s: cannot write %s', name, initFile);
    end
    fprintf(fid, '%s', text);
    fclose(fid);
    statements = strsplit(block, newline);             % as swc_load_init will do before the next load
    for k = 1:numel(statements)
        if ~isempty(strtrim(statements{k})) && ~startsWith(strtrim(statements{k}), '%')
            evalin('base', statements{k});
        end
    end
    where = 'init';
end
for k = 1:numel(ports)
    set_param(ports(k), 'MustResolveToSignalObject', 'on');
end
info = struct('name', name, 'workspace', where, 'signals', numel(ports), 'renamed_from', signal, ...
    'file', initFile);
end

function [from, to] = blockOf(text, name)
% start and end (characters) of the Studio's block of measurement `name` in the init script, [] if there is none
from = [];
to = [];
head = regexp(text, ['^% nx-measurement ' name ' \(TOOL-185\)'], 'start', 'once', 'lineanchors');
if isempty(head)
    return;
end
tail = regexp(text(head:end), ['^% nx-measurement ' name ' end[^\n]*(\n|$)'], 'end', 'once', 'lineanchors');
if isempty(tail)
    return;
end
from = head;
to = head + tail - 1;
end

function block = initBlock(name, a, old)
% the Simulink.Signal of a measurement as plain top-level assignments (the syntax tools/swc/initdata.py reads)
lines = {sprintf(['%% nx-measurement %s (TOOL-185): written by neXtBSW Studio ("Ins Modell schreiben"); ' ...
    'changed there, not here'], name), ...
    sprintf('%s = Simulink.Signal;', name), ...
    sprintf('%s.CoderInfo.StorageClass = ''ExportedGlobal'';', name)};
if isfield(a, 'simulink_type') && ~isempty(a.simulink_type)
    lines{end + 1} = sprintf('%s.DataType = ''%s'';', name, char(a.simulink_type));
end
if isfield(a, 'dimensions') && ~isempty(a.dimensions) && double(a.dimensions) > 0
    lines{end + 1} = sprintf('%s.Dimensions = %d;', name, round(double(a.dimensions)));
end
unit = textOf(a, 'unit', old, 'Unit');
if ~isempty(unit)
    lines{end + 1} = sprintf('%s.Unit = ''%s'';', name, strrep(unit, '''', ''''''));
end
description = textOf(a, 'description', old, 'Description');
if ~isempty(description)
    lines{end + 1} = sprintf('%s.Description = ''%s'';', name, strrep(description, '''', ''''''));
end
lines{end + 1} = sprintf('%% nx-measurement %s end', name);
block = [strjoin(lines, newline) newline];
end

function text = textOf(a, field, old, property)
text = '';
if isfield(a, field) && ~isempty(a.(field))
    text = char(a.(field));
elseif isa(old, 'Simulink.Signal')
    text = char(old.(property));
end
text = strrep(strrep(text, sprintf('\r'), ' '), newline, ' ');
end

function renameSignal(mdl, signal, name)
% the named signal the user chose for the measurement takes its name; a signal of that name must not exist yet
if ~isempty(signalPorts(mdl, name))
    error('swc_measurements:taken', 'measurement %s: the model already has a signal %s - choose no other signal', ...
        name, name);
end
ports = signalPorts(mdl, signal);
if isempty(ports)
    error('swc_measurements:noSignal', 'measurement %s: the model has no named signal %s', name, signal);
end
if hasObject(mdl, signal)
    error('swc_measurements:object', ['measurement %s: signal %s already takes a Simulink.Signal %s - renaming ' ...
        'it would leave that object behind'], name, signal, signal);
end
for k = 1:numel(ports)
    set_param(ports(k), 'Name', name);
end
end

function sc = storageClassOf(obj)
sc = char(obj.CoderInfo.StorageClass);
if strcmp(sc, 'Custom')
    try
        sc = char(obj.CoderInfo.CustomStorageClass);
    catch
    end
end
end
