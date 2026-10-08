function swc_build(swcDir, workRoot)
%SWC_BUILD Generates AUTOSAR code and ARXML for the software component in swcDir.
%   swc_build('swc/DemoSwc') runs Embedded Coder (autosar.tlc) for <name>.slx and copies the
%   result into <swcDir>/generated:
%     src/    component C code (model sources, no RTE stubs)
%     arxml/  component, interface, data type, implementation and timing descriptions
%   Temporary build artifacts stay in <repo>/build/swc/<name>.
%
%   swc_build('swc/DemoSwc', workRoot) puts the temporary build artifacts into <workRoot>/<name> instead
%   (TOOL-107). Without it the folder lies next to this script, in <repo>/build/swc - right for a checkout of
%   neXtBSW itself, wrong for a customer repository: there <repo> is the pinned neXtBSW tree, a submodule
%   nothing may write into (ARCH-56/57). The Studio therefore passes <build directory of the project>/swc.
%   Only <workRoot>/<name> is emptied before the build, never workRoot itself, and a workRoot that holds the
%   component folder is refused - emptying it would delete the model.

narginchk(1, 2);
swcDir = char(swcDir);
if ~isfolder(swcDir)
    error('swc_build:noDir', 'folder %s not found', swcDir);
end
swcDir = char(java.io.File(swcDir).getCanonicalPath());
[~, name] = fileparts(swcDir);
repo = fileparts(fileparts(fileparts(mfilename('fullpath'))));
if nargin < 2 || isempty(workRoot)
    workRoot = fullfile(repo, 'build', 'swc');
end
workRoot = char(java.io.File(char(workRoot)).getAbsolutePath());
work = char(java.io.File(fullfile(workRoot, name)).getCanonicalPath());
if isInside(swcDir, work)
    error('swc_build:work', ['work folder %s would contain the component folder %s; it is emptied before ' ...
        'every build, so it must lie elsewhere'], work, swcDir);
end
if isfolder(work)
    rmdir(work, 's');
end
mkdir(work);

addpath(swcDir);
addpath(fullfile(repo, 'swc', 'template'));   % NxSwcLib: library blocks linked from the model (RTE-07)
cfg = Simulink.fileGenControl('getConfig');
oldCode = cfg.CodeGenFolder;
oldCache = cfg.CacheFolder;
cleanup = onCleanup(@() Simulink.fileGenControl('set', 'CodeGenFolder', oldCode, 'CacheFolder', oldCache));
Simulink.fileGenControl('set', 'CodeGenFolder', work, 'CacheFolder', work, 'createDir', true);

% Signal qualifier constants (QLF_*) a model may compare against; they come from the generated
% Rte_Qlf.m, not from a number in the model (COM-03/COM-13).
swc_load_qlf();

fprintf('nx-step build load\n');
swc_load_init(name, swcDir);                   % TOOL-135: workspace variables an imported model takes constants from
load_system(name);
% TOOL-135: only autosar.tlc writes the ARXML and the component code this function copies. A model with another
% target (e.g. grt.tlc of a model imported as it was) used to build into <name>_grt_rtw, and generated/ stayed
% empty without an error.
target = get_param(name, 'SystemTargetFile');
if ~strcmp(target, 'autosar.tlc')
    close_system(name, 0);
    error('swc_build:notAutosar', ['model %s is no AUTOSAR component (system target file %s, not autosar.tlc): ' ...
        'no ARXML and no component code. Prepare it with swc_prepare(''%s'') - the Studio does this when it ' ...
        'imports a model - or set up the AUTOSAR component in the model'], name, target, ...
        strrep(swcDir, '\', '/'));
end
% Calibration parameters (RTE-03): values of <name>_data.m, never the ones stored in the .slx; the model is closed
% without saving afterwards, so the .slx (and model.sha256) stay unchanged.
swc_load_data(name, swcDir);
checkParameters(name, swcDir);                 % TOOL-146: parameters of swc.yaml the model does not have yet
slbuild(name);
close_system(name, 0);
% TOOL-169: libraries and referenced models the code depends on - of the saved model file (the loaded model is
% dirty from the _data.m values, and the analysis refuses unsaved models)
libraries = modelDependencies(name, swcDir, repo);

buildDir = fullfile(work, [name '_autosar_rtw']);
fprintf('nx-step build copy\n');
% TOOL-135: an empty result is an error, never a success - the Studio would report "Code erzeugt" otherwise. Checked
% before generated/ is replaced, so the code of the last good build stays.
sources = dir(fullfile(buildDir, '*.c'));
arxml = dir(fullfile(buildDir, '*.arxml'));
if isempty(arxml) || isempty(sources)
    missing = {};
    if isempty(arxml)
        missing{end + 1} = 'ARXML';
    end
    if isempty(sources)
        missing{end + 1} = 'C sources';
    end
    error('swc_build:empty', ['code generation of %s gave no %s in %s; generated/ is left as it was. Check the ' ...
        'build messages above - the model must be an AUTOSAR component (autosar.tlc)'], name, ...
        strjoin(missing, ' and '), buildDir);
end
out = fullfile(swcDir, 'generated');
if isfolder(out)
    rmdir(out, 's');
end
mkdir(fullfile(out, 'src'));
mkdir(fullfile(out, 'arxml'));

% Model sources and headers; RTE stubs (stub/) and build-system files are not needed.
files = [sources; dir(fullfile(buildDir, '*.h'))];
for k = 1:numel(files)
    copyfile(fullfile(files(k).folder, files(k).name), fullfile(out, 'src'));
end
shared = fullfile(work, 'slprj', 'autosar', '_sharedutils');
if isfolder(shared)
    files = [dir(fullfile(shared, '*.c')); dir(fullfile(shared, '*.h'))];
    for k = 1:numel(files)
        copyfile(fullfile(files(k).folder, files(k).name), fullfile(out, 'src'));
    end
end
for k = 1:numel(arxml)
    copyfile(fullfile(arxml(k).folder, arxml(k).name), fullfile(out, 'arxml'));
end
% Generation timestamps would change every file on each build and hide real differences in git.
generated = [dir(fullfile(out, 'src', '*.*')); dir(fullfile(out, 'arxml', '*.arxml'))];
for k = 1:numel(generated)
    if generated(k).isdir
        continue;
    end
    f = fullfile(generated(k).folder, generated(k).name);
    content = fileread(f);
    content = regexprep(content, '[^\n]*(source code generated on|File generated on)[^\n]*\n', '');
    fid = fopen(f, 'w');
    fwrite(fid, content);
    fclose(fid);
end

% Hashes of the inputs the code was generated from (TOOL-169), one line '<sha256>  <path relative to swcDir>' each:
% the model first (older readers compare only the first hash), then <name>_init.m, init_data/** and the libraries
% and referenced models inside the repository. <name>_data.m is no input of the code - the RTE reads its values.
% The last line marks the record as complete, so an _init.m or init_data file added later counts as new.
% tools/swc/modelhash.py reads it for tools/rtegen (warning) and the Studio (model column, state dot).
inputs = [{[name '.slx']}, initInputs(name, swcDir), libraries];
fid = fopen(fullfile(out, 'model.sha256'), 'w');
for k = 1:numel(inputs)
    fprintf(fid, '%s  %s\n', fileSha256(fullfile(swcDir, inputs{k})), inputs{k});
end
fprintf(fid, '# nx-model-inputs 2\n');
fclose(fid);
fprintf('recorded %d model inputs in %s\n', numel(inputs), fullfile(out, 'model.sha256'));
fprintf('generated %d arxml files into %s\n', numel(arxml), out);
end

function checkParameters(name, swcDir)
%CHECKPARAMETERS TOOL-146: a calibration parameter swc.yaml defines but the model has not as model argument is not
%   written into the model here - the .slx is the master and the build never saves it. It is named, with the step
%   that writes it (swc_params, the Studio's "Ins Modell schreiben"); tools/rtegen stops at it otherwise.
if ~isfile(fullfile(swcDir, 'swc.yaml'))
    return;
end
text = fileread(fullfile(swcDir, 'swc.yaml'));
if isempty(regexp(text, '(?m)^parameters\s*:', 'once'))
    return;
end
try
    spec = swc_read_spec(swcDir);
catch err
    fprintf('swc.yaml of %s not checked against the model parameters: %s\n', name, err.message);
    return;
end
if ~isfield(spec, 'parameters') || isempty(spec.parameters)
    return;
end
params = spec.parameters;
if isstruct(params)
    params = num2cell(params);
end
names = cellfun(@(p) p.name, params, 'UniformOutput', false);
args = strtrim(strsplit(get_param(name, 'ParameterArgumentNames'), ','));
missing = names(~ismember(names, args));
if ~isempty(missing)
    warning('swc_build:parameters', ['swc.yaml of %s defines calibration parameter(s) the model does not have: %s. ' ...
        'Write them into the model with swc_params(''%s'') (Studio: "Ins Modell schreiben"), then build again - ' ...
        'tools/rtegen stops at them otherwise'], name, strjoin(missing, ', '), strrep(swcDir, '\', '/'));
end
end

function files = initInputs(name, swcDir)
%INITINPUTS <name>_types.m (RTE-15), <name>_init.m and every file below init_data/, relative to swcDir with '/'
%   (TOOL-169).
files = {};
if isfile(fullfile(swcDir, [name '_types.m']))
    files{end + 1} = [name '_types.m'];
end
if isfile(fullfile(swcDir, [name '_init.m']))
    files{end + 1} = [name '_init.m'];
end
data = fullfile(swcDir, 'init_data');
if isfolder(data)
    listing = dir(fullfile(data, '**', '*'));
    listing = listing(~[listing.isdir]);
    rel = cell(1, numel(listing));
    for k = 1:numel(listing)
        rel{k} = relativePath(swcDir, fullfile(listing(k).folder, listing(k).name));
    end
    files = [files, sort(rel)];
end
end

function files = modelDependencies(name, swcDir, repo)
%MODELDEPENDENCIES Libraries and referenced models of the model file inside the repository (TOOL-169).
%   MATLAB dependency analysis of the model file; kept are .slx/.mdl/.sldd files inside the repository of the
%   component (outermost folder with .git above swcDir) or inside the neXtBSW tree, never in matlabroot, never the
%   model itself or <name>_data.m. Paths are relative to swcDir with '/' and may start with '../'.
files = {};
try
    found = dependencies.fileDependencyAnalysis(fullfile(swcDir, [name '.slx']));
catch err
    warning('swc_build:dependencies', 'dependency analysis of %s failed (%s); only the model and its init files are recorded', ...
        name, err.message);
    return;
end
if ischar(found)
    found = {found};
end
roots = {repo};
top = repositoryRoot(swcDir);
if ~isempty(top)
    roots{end + 1} = top;
end
own = {fullfile(swcDir, [name '.slx']), fullfile(swcDir, [name '_data.m'])};
for k = 1:numel(found)
    f = char(java.io.File(char(found{k})).getCanonicalPath());
    [~, ~, ext] = fileparts(f);
    if ~any(strcmpi(ext, {'.slx', '.mdl', '.sldd'})) || isInside(f, matlabroot) || any(cellfun(@(o) samePath(f, o), own))
        continue;
    end
    if ~any(cellfun(@(r) isInside(f, r), roots))
        continue;
    end
    rel = relativePath(swcDir, f);
    if ~isempty(rel)
        files{end + 1} = rel; %#ok<AGROW>
    end
end
files = unique(files);
if isempty(files)
    files = {};
end
fprintf('dependency analysis of %s: %d files, %d library/model files inside the repository\n', name, ...
    numel(found), numel(files));
end

function top = repositoryRoot(folder)
%REPOSITORYROOT Outermost folder above (or equal to) folder that holds a .git entry; '' if there is none. A submodule
%   or worktree lies inside the repository that holds it, so its libraries there are inside the repository, too.
top = '';
while true
    if isfolder(fullfile(folder, '.git')) || isfile(fullfile(folder, '.git'))
        top = folder;
    end
    parent = fileparts(folder);
    if isempty(parent) || strcmp(parent, folder)
        return;
    end
    folder = parent;
end
end

function rel = relativePath(from, to)
%RELATIVEPATH Path of file `to` relative to folder `from`, '/' separated, '../' where needed; '' on another drive.
a = strsplit(strrep(char(from), '\', '/'), '/');
b = strsplit(strrep(char(to), '\', '/'), '/');
a = a(~cellfun(@isempty, a));
b = b(~cellfun(@isempty, b));
n = 0;
while n < min(numel(a), numel(b)) && samePart(a{n + 1}, b{n + 1})
    n = n + 1;
end
if n == 0
    rel = '';
    return;
end
rel = strjoin([repmat({'..'}, 1, numel(a) - n), b(n + 1:end)], '/');
end

function yes = samePart(a, b)
if ispc
    yes = strcmpi(a, b);
else
    yes = strcmp(a, b);
end
end

function yes = samePath(a, b)
yes = samePart(strrep(char(a), '\', '/'), strrep(char(b), '\', '/'));
end

function hex = fileSha256(file)
%FILESHA256 Lower-case hex SHA-256 of the file content.
md = java.security.MessageDigest.getInstance('SHA-256');
fid = fopen(file, 'r');
if fid < 0
    error('swc_build:read', 'cannot read %s for model.sha256', file);
end
md.update(fread(fid, inf, '*int8'));
fclose(fid);
digest = typecast(md.digest(), 'uint8');
hex = lower(reshape(dec2hex(digest, 2).', 1, []));
end

function yes = isInside(path, folder)
%ISINSIDE True if path is folder itself or lies below it (canonical paths, case-insensitive on Windows).
a = strrep(path, filesep, '/');
b = strrep(folder, filesep, '/');
if ispc
    a = lower(a);
    b = lower(b);
end
yes = strcmp(a, b) || startsWith(a, [regexprep(b, '/$', '') '/']);
end
