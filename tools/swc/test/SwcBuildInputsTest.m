% SPDX-License-Identifier: Apache-2.0
classdef SwcBuildInputsTest < matlab.unittest.TestCase
    %SWCBUILDINPUTSTEST generated/model.sha256 lists every input of the code generation (TOOL-169).
    %   matlab -batch "results = runtests('tools/swc/test/SwcBuildInputsTest.m'); assertSuccess(results)"
    %
    %   QlfTestSwc (two SensorQualifier blocks of swc/template/NxSwcLib.slx) is created in a temporary folder with
    %   an <Swc>_init.m and init_data/, built with swc_build, and the record is checked: model, init script,
    %   init data and the library, no <Swc>_data.m. Then the init script changes and tools/swc/modelhash.py (the
    %   reader of the Studio and tools/rtegen) must name exactly that file. Nothing in swc/ is written.

    properties (Constant)
        Repo = fileparts(fileparts(fileparts(fileparts(mfilename('fullpath')))))
        Fixtures = fullfile(fileparts(mfilename('fullpath')), 'fixtures')
    end

    properties
        Work
    end

    methods (TestClassSetup)
        function setupEnvironment(tc)
            oldPath = path;
            oldDir = pwd;
            oldEnv = getenv('NX_RTE_GENERATED');
            cfg = Simulink.fileGenControl('getConfig');
            oldCode = cfg.CodeGenFolder;
            oldCache = cfg.CacheFolder;
            tc.Work = tempname;
            mkdir(tc.Work);
            tc.addTeardown(@() SwcBuildInputsTest.restore(oldPath, oldDir, oldEnv, oldCode, oldCache, tc.Work));

            addpath(fullfile(tc.Repo, 'tools', 'swc'));
            addpath(fullfile(tc.Repo, 'swc', 'template'));
            setenv('NX_RTE_GENERATED', tc.Fixtures);
            cd(tc.Work);
            Simulink.fileGenControl('set', 'CodeGenFolder', tc.Work, 'CacheFolder', tc.Work, 'createDir', true);
            swc_load_qlf();
        end
    end

    methods (Static, Access = private)
        function restore(oldPath, oldDir, oldEnv, oldCode, oldCache, work)
            bdclose('all');
            cd(oldDir);
            path(oldPath);
            setenv('NX_RTE_GENERATED', oldEnv);
            Simulink.fileGenControl('set', 'CodeGenFolder', oldCode, 'CacheFolder', oldCache);
            if isfolder(work)
                rmdir(work, 's');
            end
        end

        function write(file, text)
            fid = fopen(file, 'w');
            fwrite(fid, text);
            fclose(fid);
        end
    end

    methods (Test)
        function recordListsInitDataAndLibrary(tc)
            name = 'QlfTestSwc';
            d = fullfile(tc.Work, 'swc', name);
            mkdir(d);
            copyfile(fullfile(tc.Fixtures, name, 'swc.yaml'), fullfile(d, 'swc.yaml'));
            addpath(fullfile(tc.Fixtures, name));
            addpath(d);
            swc_create(d, 'Logic', [name '_logic']);
            mkdir(fullfile(d, 'init_data', 'sub'));
            SwcBuildInputsTest.write(fullfile(d, [name '_init.m']), sprintf('t169_gain = 2;\n'));
            SwcBuildInputsTest.write(fullfile(d, 'init_data', 'a.m'), sprintf('t169_a = 1;\n'));
            SwcBuildInputsTest.write(fullfile(d, 'init_data', 'sub', 'b.m'), sprintf('t169_b = 1;\n'));

            swc_build(d, fullfile(tc.Work, 'build'));

            lines = splitlines(strtrim(fileread(fullfile(d, 'generated', 'model.sha256'))));
            disp(lines);
            paths = regexprep(lines(1:end - 1), '^[0-9a-f]{64}  ', '');
            tc.verifyTrue(all(~cellfun(@isempty, regexp(lines(1:end - 1), '^[0-9a-f]{64}  \S'))), 'hash format');
            tc.verifyEqual(paths{1}, [name '.slx'], 'model first');
            tc.verifyEqual(paths(2:4)', {[name '_init.m'], 'init_data/a.m', 'init_data/sub/b.m'}, 'init inputs');
            library = paths(endsWith(paths, 'swc/template/NxSwcLib.slx'));
            tc.verifyNumElements(library, 1, 'library of the SensorQualifier blocks');
            tc.verifyTrue(startsWith(library{1}, '../'), 'library relative to the component folder');
            tc.verifyTrue(isfile(fullfile(d, library{1})), 'library path resolves');
            tc.verifyEmpty(paths(endsWith(paths, '_data.m')), '_data.m is no input');
            tc.verifyEqual(lines{end}, '# nx-model-inputs 2', 'complete record');

            reader = fullfile(tc.Repo, 'tools', 'swc', 'modelhash.py');
            python = swc_python(tc.Repo);
            setenv('PYTHONUTF8', '1');
            [status, out] = system(sprintf('"%s" "%s" "%s" %s 2>&1', python, reader, d, name));
            fprintf('modelhash after the build: %s', out);
            tc.verifyEqual(status, 0, out);
            tc.verifySubstring(out, 'aktuell');

            SwcBuildInputsTest.write(fullfile(d, [name '_init.m']), sprintf('t169_gain = 3;\n'));
            [status, out] = system(sprintf('"%s" "%s" "%s" %s 2>&1', python, reader, d, name));
            fprintf('modelhash after changing %s_init.m: %s', name, out);
            tc.verifyEqual(status, 1, out);
            tc.verifySubstring(out, [': ' name '_init.m']);
            tc.verifyEmpty(strfind(out, 'NxSwcLib'), out);
            tc.verifyEmpty(strfind(out, [name '.slx']), out);
        end
    end
end
