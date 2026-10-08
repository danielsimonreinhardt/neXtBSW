function swc_mil_test(swcDir, silTrace)
%SWC_MIL_TEST Runs the model-in-the-loop test of the software component in swcDir.
%   swc_mil_test('swc/DemoSwc') calls swc/DemoSwc/test/DemoSwc_mil_test.m, which simulates
%   DemoSwc.slx in Simulink (normal mode) and checks the outputs.
%   swc_mil_test('swc/DemoSwc', 'build/host/demoswc_sil_trace.csv') additionally compares the
%   per-call trace with the one written by the SIL test.
%   Throws an error when a check fails, so "matlab -batch" returns exit code 1.
%   Simulation artifacts (slprj, caches, trace CSV) stay in <repo>/build/mil/<name>; the model
%   is loaded read-only in the sense that it is never saved.

if nargin < 2
    silTrace = '';
end
swcDir = char(java.io.File(char(swcDir)).getCanonicalPath());
if ~isfolder(swcDir)
    error('swc_mil_test:noDir', 'folder %s not found', swcDir);
end
if ~isempty(silTrace)
    silTrace = char(java.io.File(char(silTrace)).getCanonicalPath());
    if ~isfile(silTrace)
        error('swc_mil_test:noTrace', 'SIL trace %s not found', silTrace);
    end
end
[~, name] = fileparts(swcDir);
repo = fileparts(fileparts(fileparts(mfilename('fullpath'))));
work = fullfile(repo, 'build', 'mil', name);
if ~isfolder(work)
    mkdir(work);
end

cfg = Simulink.fileGenControl('getConfig');
oldCode = cfg.CodeGenFolder;
oldCache = cfg.CacheFolder;
oldDir = pwd;
oldPath = path;
cleanup = onCleanup(@() restore(oldCode, oldCache, oldDir, oldPath, name));
Simulink.fileGenControl('set', 'CodeGenFolder', work, 'CacheFolder', work, 'createDir', true);
addpath(swcDir, fullfile(swcDir, 'test'));
addpath(fullfile(repo, 'swc', 'template'));   % NxSwcLib: library blocks linked from the model (RTE-07)
cd(work);   % anything Simulink writes relative to pwd lands in build/
swc_load_qlf();   % QLF_* constants of the model from the generated Rte_Qlf.m (COM-03/COM-13)

tStart = tic;
failures = feval([name '_mil_test'], silTrace, work);
fprintf('MIL test %s finished in %.1f s\n', name, toc(tStart));
if failures > 0
    error('swc_mil_test:failed', 'MIL test %s: %d failures', name, failures);
end
end

function restore(oldCode, oldCache, oldDir, oldPath, name)
if bdIsLoaded(name)
    close_system(name, 0);   % never save the model under test
end
cd(oldDir);
path(oldPath);
Simulink.fileGenControl('set', 'CodeGenFolder', oldCode, 'CacheFolder', oldCache);
end
