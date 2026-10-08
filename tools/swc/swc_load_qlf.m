function [qlfFile, qlf] = swc_load_qlf(qlfFile)
%SWC_LOAD_QLF Puts the RTE signal qualifier constants QLF_* into the base workspace.
%   A model that compares a qualifier port (Qlf == QLF_OK, COM-03/COM-13) must take the values from
%   the generated Rte_Qlf.m, never as a number in the model. swc_build and swc_mil_test call this
%   before loading a model; in the MATLAB desktop call it once after "addpath tools/swc".
%
%   swc_load_qlf()           searches Rte_Qlf.m, runs it in the base workspace and returns its path
%   swc_load_qlf(file)       uses that file
%   [file, qlf] = ...        qlf is a struct with the same constants, for test scripts
%
%   Search order (first match wins), fixed and short - never a recursive search of a whole tree any more
%   (TOOL-107/TOOL-184: a recursive dir('**') under the pinned tree's build/ or, without NX_RTE_GENERATED, under
%   %TEMP%/s32k/** took minutes once that folder had grown with agent build directories, and swc_prepare/
%   swc_build hung on it):
%     1. NX_RTE_GENERATED (environment): <folder>/Rte_Qlf.m. The Studio sets it for every MATLAB call from the
%        BUILD step's work folder (tools/studio/core/matlab.py, TOOL-117); a desktop session or test sets it once
%        with setenv('NX_RTE_GENERATED', ...).
%     2. <repo>/build/*/generated/rte/Rte_Qlf.m - one directory level below the build root of a CMake preset
%        configured against THIS tree (cmake --preset evalboard-s32k144-demo), never a recursive search below it
%        and never outside this tree (a customer repository's project build lies elsewhere; that case needs
%        NX_RTE_GENERATED).
%     3. Fallback: the QLF_* values are a fixed table of tools/rtegen (rtegen.QLF_VALUES), the same for every
%        project - never generated from project-specific input. swc_load_qlf renders Rte_Qlf.m on the fly through
%        tools/rtegen (render_qlf_matlab) into a temporary file, so a MATLAB session that never configured a
%        project (or a worktree without a build yet) still gets working constants instead of a long search or an
%        error. Errors only if Python or tools/rtegen itself is unusable.

if nargin < 1 || isempty(qlfFile)
    qlfFile = findQlfFile();
end
qlfFile = char(java.io.File(char(qlfFile)).getCanonicalPath());
if ~isfile(qlfFile)
    error('swc_load_qlf:noFile', 'qualifier constants %s not found', qlfFile);
end
evalin('base', sprintf('run(''%s'');', qlfFile));

if nargout > 1
    qlf = struct();
    names = evalin('base', 'who(''QLF_*'')');
    for k = 1:numel(names)
        qlf.(names{k}) = evalin('base', names{k});
    end
    if ~isfield(qlf, 'QLF_OK')
        error('swc_load_qlf:noConstants', '%s did not define QLF_OK', qlfFile);
    end
end
end

function qlfFile = findQlfFile()
repo = fileparts(fileparts(fileparts(mfilename('fullpath'))));

% 1. NX_RTE_GENERATED: a direct file check, never a search.
fromEnv = getenv('NX_RTE_GENERATED');
if ~isempty(fromEnv)
    candidate = fullfile(fromEnv, 'Rte_Qlf.m');
    if isfile(candidate)
        qlfFile = candidate;
        return;
    end
end

% 2. Build directory of this tree's own presets, one level below build/ - bounded by the number of configured
% presets, never a recursive walk into their contents (TOOL-184).
hits = dir(fullfile(repo, 'build', '*', 'generated', 'rte', 'Rte_Qlf.m'));
if ~isempty(hits)
    [~, newest] = max([hits.datenum]);
    qlfFile = fullfile(hits(newest).folder, hits(newest).name);
    return;
end

% 3. Fallback: render the fixed QLF_* table from tools/rtegen instead of searching further.
qlfFile = generateQlfFallback(repo);
end

function qlfFile = generateQlfFallback(repo)
%GENERATEQLFFALLBACK Renders Rte_Qlf.m through tools/rtegen (TOOL-117): the QLF_* values (rtegen.QLF_VALUES) are
%   a fixed table, the same for every project, so they need no build - only Python and this tree's own
%   tools/rtegen. Replaces the recursive filesystem search TOOL-184 found hanging for minutes; a project that HAS
%   been built still prefers its own generated Rte_Qlf.m (steps 1 and 2 above), this is only the last resort.
python = swc_python(repo);
outFile = [tempname, '_Rte_Qlf.m'];
script = [tempname, '_qlf_render.py'];
lines = {
    'import sys'
    sprintf('sys.path.insert(0, %s)', pyLiteral(fullfile(repo, 'tools', 'rtegen')))
    'import rtegen'
    sprintf('open(%s, "w", encoding="utf-8").write(rtegen.render_qlf_matlab())', pyLiteral(outFile))
};
fid = fopen(script, 'w');
if fid < 0
    error('swc_load_qlf:notFound', 'cannot write temporary script %s to render Rte_Qlf.m from tools/rtegen', script);
end
cleanupScript = onCleanup(@() deleteIfExists(script));
fprintf(fid, '%s\n', lines{:});
fclose(fid);
setenv('PYTHONUTF8', '1');
cmd = sprintf('"%s" "%s" 2>&1', python, script);
[status, output] = system(cmd);
if status ~= 0 || ~isfile(outFile)
    error('swc_load_qlf:notFound', ['Rte_Qlf.m not found: NX_RTE_GENERATED not set (or without the file) and no ' ...
        'build under %s. Generating it from tools/rtegen failed too (%s):\n%s\nConfigure a project once ' ...
        '(cmake --preset evalboard-s32k144-demo) or set NX_RTE_GENERATED to the folder that holds Rte_Qlf.m.'], ...
        fullfile(repo, 'build'), cmd, strtrim(output));
end
qlfFile = outFile;
end

function text = pyLiteral(path)
%PYLITERAL Python single-quoted string literal of an absolute path (forward slashes, escaped quotes).
escaped = strrep(strrep(char(path), '\', '/'), '''', '\''');
text = ['''' escaped ''''];
end

function deleteIfExists(f)
if isfile(f)
    delete(f);
end
end
