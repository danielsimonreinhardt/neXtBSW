% SPDX-License-Identifier: Apache-2.0
classdef SwcCreateTest < matlab.unittest.TestCase
    %SWCCREATETEST Tests of swc_create, swc/template and the NxSwcLib SensorQualifier block (RTE-07).
    %   matlab -batch "results = runtests('tools/swc/test'); assertSuccess(results)"
    %
    %   Everything is created in a temporary folder; nothing in swc/ is written. Code generation runs
    %   through swc_build (temporary files in <repo>/build/swc/<name>). The QLF_* constants come from
    %   tools/swc/test/fixtures/Rte_Qlf.m: the rtegen output plus the proposed QLF_ERROR_RANGE
    %   (test_swc_yaml.py checks that the fixture matches tools/rtegen).

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
            tc.addTeardown(@() SwcCreateTest.restore(oldPath, oldDir, oldEnv, oldCode, oldCache, tc.Work));

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
    end

    methods (Access = private)
        function d = componentDir(tc, name, yamlSource)
            d = fullfile(tc.Work, name);
            if isfolder(d)
                rmdir(d, 's');
            end
            mkdir(d);
            copyfile(yamlSource, fullfile(d, 'swc.yaml'));
            data = fullfile(fileparts(yamlSource), [name '_data.m']);   % calibration values (RTE-03)
            if isfile(data)
                copyfile(data, d);
            end
        end

        function d = createFixture(tc, name, varargin)
            d = tc.componentDir(name, fullfile(tc.Fixtures, name, 'swc.yaml'));
            addpath(fullfile(tc.Fixtures, name));
            addpath(d);
            swc_create(d, 'Logic', [name '_logic'], varargin{:});
        end
    end

    methods (Test)
        function demoSwcIsReproduced(tc)
            % swc.yaml + logic hook give the code of swc/DemoSwc (DemoSwc_create.m + DemoSwc_autosar.m)
            d = tc.createFixture('DemoSwc');
            swc_build(d);
            reference = fullfile(tc.Repo, 'swc', 'DemoSwc', 'generated');
            created = fullfile(d, 'generated');

            files = dir(fullfile(reference, 'src', '*.*'));
            files = files(~[files.isdir]);
            tc.verifyNotEmpty(files);
            actual = dir(fullfile(created, 'src', '*.*'));
            tc.verifyEqual(sort({actual(~[actual.isdir]).name}), sort({files.name}), 'source file set');
            for k = 1:numel(files)
                tc.verifyEqual(fileread(fullfile(created, 'src', files(k).name)), ...
                    fileread(fullfile(reference, 'src', files(k).name)), ['byte-identical ' files(k).name]);
            end

            % ARXML: identical except the UUIDs (new per autosar.api.create) and the model checksum
            files = dir(fullfile(reference, 'arxml', '*.arxml'));
            tc.verifyNotEmpty(files);
            actual = dir(fullfile(created, 'arxml', '*.arxml'));
            tc.verifyEqual(sort({actual.name}), sort({files.name}), 'ARXML file set');
            for k = 1:numel(files)
                tc.verifyEqual(SwcCreateTest.maskArxml(fileread(fullfile(created, 'arxml', files(k).name))), ...
                    SwcCreateTest.maskArxml(fileread(fullfile(reference, 'arxml', files(k).name))), ...
                    ['ARXML without UUID/checksum ' files(k).name]);
            end
        end

        function sensorQualifierPrecedenceAndDebounce(tc)
            tc.createFixture('QlfTestSwc', 'Autosar', false);
            Q = SwcCreateTest.qlf();
            % Run10ms: range 1000..4000, debounce 30 ms at 10 ms -> error in the 4th call outside
            steps = {
                2500, 'QLF_OK',            'QLF_OK'             % in range
                1000, 'QLF_OK',            'QLF_OK'             % lower limit inclusive
                4000, 'QLF_OK',            'QLF_OK'             % upper limit inclusive
                 999, 'QLF_OK',            'QLF_DEBOUNCE'       % 0 ms
                 999, 'QLF_OK',            'QLF_DEBOUNCE'       % 10 ms
                 999, 'QLF_OK',            'QLF_DEBOUNCE'       % 20 ms
                 999, 'QLF_OK',            'QLF_ERROR_RANGE'    % 30 ms >= debounce_ms
                   0, 'QLF_OK',            'QLF_ERROR_RANGE'
                2500, 'QLF_OK',            'QLF_OK'             % healing at once
                4001, 'QLF_OK',            'QLF_DEBOUNCE'       % debounce starts again at 0
                4001, 'QLF_ERROR_TIMEOUT', 'QLF_ERROR_TIMEOUT'  % source error has precedence, restarts
                4001, 'QLF_OK',            'QLF_DEBOUNCE'       % 0 ms
                4001, 'QLF_OK',            'QLF_DEBOUNCE'       % 10 ms
                4001, 'QLF_OK',            'QLF_DEBOUNCE'       % 20 ms
                4001, 'QLF_OK',            'QLF_ERROR_RANGE'    % 30 ms
                4001, 'QLF_ERROR_CRC',     'QLF_ERROR_CRC'      % source error over a confirmed range error
                4001, 'QLF_ERROR_ALIVE',   'QLF_ERROR_ALIVE'
                4001, 'QLF_ERROR',         'QLF_ERROR'
                4001, 'QLF_DEBOUNCE',      'QLF_DEBOUNCE'       % source not valid yet
                2500, 'QLF_ERROR',         'QLF_ERROR'
                2500, 'QLF_OK',            'QLF_OK'
                };
            % Run20ms: range -400..1250, no source qualifier, debounce 40 ms at 20 ms -> 3rd call
            tempSteps = {
                  20.5, 'QLF_OK'
                -400,   'QLF_OK'
                1250,   'QLF_OK'
                1250.5, 'QLF_DEBOUNCE'
                -401,   'QLF_DEBOUNCE'
                2000,   'QLF_ERROR_RANGE'
                  20,   'QLF_OK'
                };
            [volt, temp] = SwcCreateTest.simulateQlfTestSwc(steps, tempSteps, {});
            tc.verifyEqual(volt, SwcCreateTest.toQlf(Q, steps(:, 3)), 'VoltageQlf (Run10ms, 30 ms)');
            tc.verifyEqual(temp, SwcCreateTest.toQlf(Q, tempSteps(:, 2)), 'TempQlf (Run20ms, 40 ms)');
        end

        function sensorQualifierWithoutDebounceReportsAtOnce(tc)
            tc.createFixture('QlfTestSwc', 'Autosar', false);
            Q = SwcCreateTest.qlf();
            steps = {
                2500, 'QLF_OK', 'QLF_OK'
                 999, 'QLF_OK', 'QLF_ERROR_RANGE'
                2500, 'QLF_OK', 'QLF_OK'
                4001, 'QLF_OK', 'QLF_ERROR_RANGE'
                4001, 'QLF_ERROR_TIMEOUT', 'QLF_ERROR_TIMEOUT'
                };
            tempSteps = {0, 'QLF_OK'; 5000, 'QLF_ERROR_RANGE'; 5000, 'QLF_ERROR_RANGE'};
            params = {'QlfTestSwc/QlfTestSwc_task10ms/VoltageQualifier', 'debounce_ms', '0'; ...
                      'QlfTestSwc/QlfTestSwc_task20ms/TempQualifier', 'debounce_ms', '0'};
            [volt, temp] = SwcCreateTest.simulateQlfTestSwc(steps, tempSteps, params);
            tc.verifyEqual(volt, SwcCreateTest.toQlf(Q, steps(:, 3)));
            tc.verifyEqual(temp, SwcCreateTest.toQlf(Q, tempSteps(:, 2)));
        end

        function sensorQualifierRejectsInvalidParameters(tc)
            tc.createFixture('QlfTestSwc', 'Autosar', false);
            steps = {2500, 'QLF_OK', 'QLF_OK'};
            tempSteps = {0, 'QLF_OK'};
            bad = {{'debounce_ms', '-10'}, {'debounce_ms', '2.5'}, {'min_value', '5000'}};
            for k = 1:numel(bad)
                params = {'QlfTestSwc/QlfTestSwc_task10ms/VoltageQualifier', bad{k}{1}, bad{k}{2}};
                try
                    SwcCreateTest.simulateQlfTestSwc(steps, tempSteps, params);
                    tc.verifyFail(sprintf('%s = %s was accepted', bad{k}{1}, bad{k}{2}));
                catch err
                    tc.verifySubstring(getReport(err, 'extended', 'hyperlinks', 'off'), 'SensorQualifier:');
                end
            end
        end

        function sensorQualifierCodeIsGenerated(tc)
            % AUTOSAR code of two instances in runnables with different periods: the period is folded
            % into a constant, no 64-bit arithmetic, QLF_ERROR_RANGE as its value
            d = tc.createFixture('QlfTestSwc');
            swc_build(d);
            code = fileread(fullfile(d, 'generated', 'src', 'QlfTestSwc.c'));
            tc.verifySubstring(code, 'Rte_Write_VoltageQlf_VoltageQlf(');
            tc.verifySubstring(code, 'Rte_Write_TempQlf_TempQlf(');
            tc.verifyMatches(code, '\+ 10U\>');
            tc.verifyMatches(code, '\+ 20U\>');
            tc.verifyMatches(code, '>= 30U');
            tc.verifyMatches(code, '>= 40U');
            tc.verifySubstring(code, sprintf('%dU', SwcCreateTest.qlf().QLF_ERROR_RANGE));
            tc.verifyEmpty(regexp(code, 'uint64|ULL', 'once'), 'no 64-bit arithmetic');
            tc.verifyEqual(numel(dir(fullfile(d, 'generated', 'arxml', '*.arxml'))), 5);
        end

        function templateCreatesBuildableSkeleton(tc)
            d = tc.componentDir('NewSwc', fullfile(tc.Repo, 'swc', 'template', 'swc.yaml'));
            addpath(d);
            swc_create(d);
            load_system(fullfile(d, 'NewSwc.slx'));
            closer = onCleanup(@() close_system('NewSwc', 0));
            tc.verifyEqual(get_param('NewSwc', 'IsExportFunctionModel'), 'on');
            tc.verifyEqual(get_param('NewSwc', 'SystemTargetFile'), 'autosar.tlc');
            tc.verifyEqual(get_param('NewSwc', 'FixedStep'), '0.01');
            tc.verifyEqual(get_param('NewSwc/Run100ms', 'SampleTime'), '0.1');
            tc.verifyEqual(get_param('NewSwc/Speed_rpm', 'OutDataTypeStr'), 'uint16');
            tc.verifyEqual(get_param('NewSwc/Temperature_dC', 'OutDataTypeStr'), 'int16');
            tc.verifyEqual(get_param('NewSwc/NewSwc_task10ms/function', 'SampleTimeType'), 'periodic');
            % TOOL-180: subsystems named after the rate, inports green, outports red (root and subsystems)
            tc.verifyEqual(swc_runnable_subsystem('NewSwc', 'Run100ms'), 'NewSwc/NewSwc_task100ms');
            tc.verifyEmpty(find_system('NewSwc', 'SearchDepth', 1, 'Regexp', 'on', 'Name', '_Subsystem$'));
            for blk = {'NewSwc/Run10ms', 'NewSwc/Speed_rpm', 'NewSwc/NewSwc_task10ms/Speed_rpm'}
                tc.verifyEqual(get_param(blk{1}, 'BackgroundColor'), 'green', blk{1});
            end
            for blk = {'NewSwc/Heating', 'NewSwc/NewSwc_task100ms/Heating'}
                tc.verifyEqual(get_param(blk{1}, 'BackgroundColor'), 'red', blk{1});
            end
            % placeholders keep the new model compilable
            tc.verifyNotEmpty(find_system('NewSwc/NewSwc_task10ms', 'SearchDepth', 1, 'Name', 'SpeedQlf_Unused'));
            tc.verifyNotEmpty(find_system('NewSwc/NewSwc_task100ms', 'SearchDepth', 1, 'Name', 'Heating_Default'));
            slMap = autosar.api.getSimulinkMapping('NewSwc');
            [~, ~, access] = getInport(slMap, 'Speed_rpm');
            tc.verifyEqual(access, 'ExplicitReceive');
            [~, ~, access] = getOutport(slMap, 'Heating');
            tc.verifyEqual(access, 'ExplicitSend');
            set_param('NewSwc', 'SimulationCommand', 'update');
        end

        function sameRateRunnablesGetUniqueSubsystems(tc)
            % TOOL-180: two runnables of one period get <Swc>_task<Period>ms_<Runnable>; a subsystem with the name
            % of before TOOL-180 (<Runnable>_Subsystem) or any other name is still found through its trigger line
            d = fullfile(tc.Work, 'RateSwc');
            if isfolder(d)
                rmdir(d, 's');
            end
            mkdir(d);
            fid = fopen(fullfile(d, 'swc.yaml'), 'w');
            fprintf(fid, '%s\n', 'version: 1', 'name: RateSwc', 'runnables:', ...
                '  - { name: task10ms, period_ms: 10 }', '  - { name: Diag, period_ms: 10 }', ...
                '  - { name: task20ms, period_ms: 20 }', 'ports:', ...
                '  - { name: Level, direction: in, type: uint8, runnables: [task10ms, Diag, task20ms] }', ...
                '  - { name: Alarm, direction: out, type: boolean, runnables: [Diag] }');
            fclose(fid);
            swc_create(d, 'Autosar', false);
            load_system(fullfile(d, 'RateSwc.slx'));
            closer = onCleanup(@() close_system('RateSwc', 0));
            tc.verifyEqual(swc_runnable_subsystem('RateSwc', 'task10ms'), 'RateSwc/RateSwc_task10ms_task10ms');
            tc.verifyEqual(swc_runnable_subsystem('RateSwc', 'Diag'), 'RateSwc/RateSwc_task10ms_Diag');
            tc.verifyEqual(swc_runnable_subsystem('RateSwc', 'task20ms'), 'RateSwc/RateSwc_task20ms');
            tc.verifyEqual(get_param('RateSwc/RateSwc_task20ms/function', 'SampleTime'), '0.02');
            tc.verifyEqual(get_param('RateSwc/RateSwc_task10ms_Diag/Alarm', 'BackgroundColor'), 'red');
            tc.verifyEqual(get_param('RateSwc/RateSwc_task10ms_Diag/Level', 'BackgroundColor'), 'green');
            set_param('RateSwc/RateSwc_task10ms_Diag', 'Name', 'Diag_Subsystem');       % a model of before
            tc.verifyEqual(swc_runnable_subsystem('RateSwc', 'Diag'), 'RateSwc/Diag_Subsystem');
            set_param('RateSwc/RateSwc_task20ms', 'Name', 'Slow');                     % renamed by the user
            tc.verifyEqual(swc_runnable_subsystem('RateSwc', 'task20ms'), 'RateSwc/Slow');
            tc.verifyEqual(swc_runnable_subsystem('RateSwc', 'NoSuchRunnable'), '');
        end

        function existingModelIsKept(tc)
            d = tc.componentDir('NewSwc', fullfile(tc.Repo, 'swc', 'template', 'swc.yaml'));
            swc_create(d, 'Autosar', false);
            tc.verifyError(@() swc_create(d, 'Autosar', false), 'swc_create:exists');
            swc_create(d, 'Autosar', false, 'Overwrite', true);
        end

        function invalidYamlIsReportedWithPath(tc)
            d = fullfile(tc.Work, 'BadSwc');
            mkdir(d);
            fid = fopen(fullfile(d, 'swc.yaml'), 'w');
            fprintf(fid, ['name: BadSwc\nrunnables:\n  - name: Run10ms\n    period_ms: 0\n' ...
                'ports:\n  - { name: Out1, direction: out, type: uint8, runnables: [Run10ms, Run5ms] }\n']);
            fclose(fid);
            try
                swc_create(d);
                tc.verifyFail('invalid swc.yaml was accepted');
            catch err
                tc.verifyEqual(err.identifier, 'swc_create:yaml');
                tc.verifySubstring(err.message, 'runnables[0].period_ms');
                tc.verifySubstring(err.message, 'ports[0].runnables[1]');
            end
            tc.verifyFalse(isfile(fullfile(d, 'BadSwc.slx')));
        end

        function libraryMatchesItsScript(tc)
            % the checked-in NxSwcLib.slx is what swc_lib_create.m produces
            fresh = fullfile(tc.Work, 'lib', 'NxSwcLib.slx');
            mkdir(fileparts(fresh));
            swc_lib_create(fresh);
            expected = SwcCreateTest.librarySignature(fresh);
            actual = SwcCreateTest.librarySignature(fullfile(tc.Repo, 'swc', 'template', 'NxSwcLib.slx'));
            tc.verifyEqual(actual, expected);
        end
    end

    methods (Static)
        function text = maskArxml(text)
            text = regexprep(text, 'UUID="[0-9a-f-]+"', 'UUID="*"');
            text = regexprep(text, 'Model Checksum[^\n]*', 'Model Checksum *');
        end

        function Q = qlf()
            [~, Q] = swc_load_qlf();
        end

        function v = toQlf(Q, names)
            v = cellfun(@(n) Q.(n), names(:));
        end

        function [volt, temp] = simulateQlfTestSwc(steps, tempSteps, params)
            % Calls Run10ms for each row of steps (every 10 ms) and Run20ms for each row of tempSteps
            % (every 20 ms); returns the qualifier written by each call. The model is never saved.
            mdl = 'QlfTestSwc';
            Q = SwcCreateTest.qlf();
            load_system(mdl);
            t10 = (0:size(steps, 1) - 1)' * 10 / 1000;
            t20 = (0:size(tempSteps, 1) - 1)' * 20 / 1000;
            ds = Simulink.SimulationData.Dataset;
            % periodic runnable subsystems (swc_create): the function-call inports call every period by
            % themselves, explicit call times are not allowed; extra calls after the last step are ignored
            ds = ds.addElement([], 'Run10ms');
            ds = ds.addElement([], 'Run20ms');
            ds = ds.addElement(timeseries(uint16(cell2mat(steps(:, 1))), t10), 'Voltage_mV');
            ds = ds.addElement(timeseries(SwcCreateTest.toQlf(Q, steps(:, 2)), t10), 'VoltageSrcQlf');
            ds = ds.addElement(timeseries(single(cell2mat(tempSteps(:, 1))), t20), 'Temp_dC');
            in = Simulink.SimulationInput(mdl);
            in = in.setExternalInput(ds);
            for k = 1:size(params, 1)
                in = in.setBlockParameter(params{k, 1}, params{k, 2}, params{k, 3});
            end
            in = in.setModelParameter('StopTime', sprintf('%.3f', max([t10; t20])), 'SaveOutput', 'on', ...
                'SaveFormat', 'Dataset', 'SaveTime', 'off', 'SaveState', 'off', 'ReturnWorkspaceOutputs', 'on');
            out = sim(in);
            volt = SwcCreateTest.logged(out, [mdl '/VoltageQlf'], t10);
            temp = SwcCreateTest.logged(out, [mdl '/TempQlf'], t20);
        end

        function v = logged(out, block, times)
            for e = 1:out.yout.numElements
                el = out.yout{e};
                if strcmp(el.BlockPath.getBlock(1), block)
                    n = numel(times);
                    assert(numel(el.Values.Time) >= n && all(abs(el.Values.Time(1:n) - times) < 1e-9), ...
                        'SwcCreateTest:schedule', '%s not logged once per call', block);
                    v = el.Values.Data(:);
                    v = v(1:n);
                    return;
                end
            end
            error('SwcCreateTest:output', 'output %s not logged', block);
        end

        function sig = librarySignature(file)
            % blocks with type and dialog parameters, connections and mask of a library, as text
            [~, name] = fileparts(file);
            if bdIsLoaded(name)
                close_system(name, 0);
            end
            load_system(file);
            closer = onCleanup(@() close_system(name, 0));
            blocks = sort(find_system(name, 'LookUnderMasks', 'all', 'FollowLinks', 'on', 'Type', 'block'));
            sig = {};
            skip = {'Position', 'ZOrder'};
            for k = 1:numel(blocks)
                b = blocks{k};
                entry = sprintf('%s [%s]', b, get_param(b, 'BlockType'));
                dp = get_param(b, 'DialogParameters');
                if isstruct(dp)
                    names = sort(fieldnames(dp));
                    for n = 1:numel(names)
                        if ~any(strcmp(names{n}, skip))
                            try
                                value = get_param(b, names{n});
                                if ischar(value)
                                    entry = sprintf('%s %s=%s', entry, names{n}, value);
                                end
                            catch
                            end
                        end
                    end
                end
                pc = get_param(b, 'PortConnectivity');
                for p = 1:numel(pc)
                    src = pc(p).SrcBlock;
                    if ~isempty(src) && src ~= -1
                        entry = sprintf('%s in%s<-%s:%d', entry, pc(p).Type, getfullname(src), pc(p).SrcPort);
                    end
                end
                if strcmp(get_param(b, 'Mask'), 'on')
                    m = Simulink.Mask.get(b);
                    entry = sprintf('%s mask=%s|%s|%s|%s', entry, m.Type, m.Description, m.Initialization, m.Display);
                    for p = 1:numel(m.Parameters)
                        entry = sprintf('%s %s=%s(%s)', entry, m.Parameters(p).Name, m.Parameters(p).Value, ...
                            m.Parameters(p).Prompt);
                    end
                end
                sig{end + 1, 1} = entry; %#ok<AGROW>
            end
        end
    end
end
