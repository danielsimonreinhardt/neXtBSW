% SPDX-License-Identifier: Apache-2.0
classdef SwcLoadQlfTest < matlab.unittest.TestCase
    %SWCLOADQLFTEST Search order and time bound of swc_load_qlf (TOOL-117, TOOL-184).
    %   matlab -batch "results = runtests('tools/swc/test/SwcLoadQlfTest.m'); assertSuccess(results)"
    %
    %   TOOL-184: without NX_RTE_GENERATED, swc_load_qlf used to search %TEMP%/s32k/** recursively - on a machine
    %   with many agent build directories that took more than ten minutes and left swc_prepare/swc_build hanging.
    %   TOOL-117 replaced that with a fixed, short search order (NX_RTE_GENERATED, then one level below this
    %   tree's own build/, then a fallback that renders the fixed QLF_* table straight from tools/rtegen); these
    %   tests exercise every step and bound the time of the case without NX_RTE_GENERATED.

    properties (Constant)
        Repo = fileparts(fileparts(fileparts(fileparts(mfilename('fullpath')))))
        Fixtures = fullfile(fileparts(mfilename('fullpath')), 'fixtures')
        % Generous but firm: TOOL-184 measured over ten MINUTES for the recursive search this replaces; the
        % fallback is one MATLAB "system" call to Python (import tools/rtegen, no build, no Simulink model).
        TimeLimitSeconds = 60
    end

    methods (TestClassSetup)
        function addPath(tc)
            oldPath = path;
            tc.addTeardown(@() path(oldPath));
            addpath(fullfile(tc.Repo, 'tools', 'swc'));
        end
    end

    methods (Static, Access = private)
        function removeIfExists(folder)
            if isfolder(folder)
                rmdir(folder, 's');
            end
        end
    end

    methods (Test)
        function nxRteGeneratedIsUsedDirectlyWithoutASearch(tc)
            oldEnv = getenv('NX_RTE_GENERATED');
            tc.addTeardown(@() setenv('NX_RTE_GENERATED', oldEnv));
            setenv('NX_RTE_GENERATED', tc.Fixtures);

            [file, qlf] = swc_load_qlf();

            expected = char(java.io.File(fullfile(tc.Fixtures, 'Rte_Qlf.m')).getCanonicalPath());
            tc.verifyEqual(file, expected);
            tc.verifyEqual(qlf.QLF_OK, uint8(1));
        end

        function buildDirectoryOneLevelBelowBuildIsFound(tc)
            % Step 2 of the search order: <repo>/build/<preset>/generated/rte/Rte_Qlf.m, one directory level -
            % never a recursive walk into the preset's other build output (TOOL-184).
            oldEnv = getenv('NX_RTE_GENERATED');
            tc.addTeardown(@() setenv('NX_RTE_GENERATED', oldEnv));
            setenv('NX_RTE_GENERATED', '');

            presetDir = fullfile(tc.Repo, 'build', 'swc-load-qlf-test-preset');
            tc.addTeardown(@() SwcLoadQlfTest.removeIfExists(presetDir));
            target = fullfile(presetDir, 'generated', 'rte');
            mkdir(target);
            copyfile(fullfile(tc.Fixtures, 'Rte_Qlf.m'), fullfile(target, 'Rte_Qlf.m'));

            [file, qlf] = swc_load_qlf();

            expected = char(java.io.File(fullfile(target, 'Rte_Qlf.m')).getCanonicalPath());
            tc.verifyEqual(file, expected);
            tc.verifyEqual(qlf.QLF_OK, uint8(1));
        end

        function withoutNxRteGeneratedOrABuildEndsQuicklyWithTheRtegenFallback(tc)
            % TOOL-184: the scenario that used to hang - no NX_RTE_GENERATED, no build/ of this tree. The QLF_*
            % values are a fixed table (tools/rtegen.QLF_VALUES), so swc_load_qlf renders them on the fly
            % (tools/rtegen render_qlf_matlab) instead of searching further (TOOL-117).
            oldEnv = getenv('NX_RTE_GENERATED');
            tc.addTeardown(@() setenv('NX_RTE_GENERATED', oldEnv));
            setenv('NX_RTE_GENERATED', '');

            started = tic;
            [file, qlf] = swc_load_qlf();
            elapsed = toc(started);
            if isfile(file)
                tc.addTeardown(@() delete(file));
            end

            msg = sprintf(['swc_load_qlf took %.1f s without NX_RTE_GENERATED - TOOL-184 found a recursive ' ...
                'search that took minutes here'], elapsed);
            tc.verifyLessThan(elapsed, tc.TimeLimitSeconds, msg);
            tc.verifyTrue(isfile(file), 'the rendered Rte_Qlf.m must exist');
            tc.verifyEqual(qlf.QLF_OK, uint8(1));
            tc.verifyEqual(qlf.QLF_DEBOUNCE, uint8(0));
            tc.verifyEqual(qlf.QLF_NOT_AVAILABLE, uint8(64));
        end

        function anExplicitFileIsUsedAsBefore(tc)
            file = fullfile(tc.Fixtures, 'Rte_Qlf.m');
            [returned, qlf] = swc_load_qlf(file);

            tc.verifyEqual(returned, char(java.io.File(file).getCanonicalPath()));
            tc.verifyEqual(qlf.QLF_OK, uint8(1));
        end
    end
end
