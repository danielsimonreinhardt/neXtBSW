% SPDX-License-Identifier: Apache-2.0
classdef SwcPortsTest < matlab.unittest.TestCase
    %SWCPORTSTEST Tests of swc_ports: ports of an existing model added, renamed and removed (TOOL-14).
    %   matlab -batch "results = runtests('tools/swc/test/SwcPortsTest.m'); assertSuccess(results)"
    %
    %   Everything happens in a temporary folder on the fixture PortTestSwc; nothing in swc/ is written. The
    %   point of every test is the same: the model keeps its content. The tests therefore compare the block
    %   and line list of the whole model before and after the change, not only the return value.
    %
    %   TOOL-107: the fixture has BRANCHED lines (Mode at the root, Offset_mm inside Fast5ms). The TOOL-14 fixture
    %   had none, and swc_ports broke on the first branched line of a real model. fixtureKeepsItsBranches holds
    %   them in place; every case that compares line lists therefore compares them with branches in the model.

    properties (Constant)
        Repo = fileparts(fileparts(fileparts(fileparts(mfilename('fullpath')))))
        Fixtures = fullfile(fileparts(mfilename('fullpath')), 'fixtures')
        Name = 'PortTestSwc'
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
            tc.addTeardown(@() SwcPortsTest.restore(oldPath, oldDir, oldEnv, oldCode, oldCache, tc.Work));
            addpath(fullfile(tc.Repo, 'tools', 'swc'));
            addpath(fullfile(tc.Repo, 'swc', 'template'));
            addpath(fullfile(tc.Fixtures, tc.Name));
            % TOOL-182: Rte_Qlf.m from the fixtures, like SwcCreateTest - without it swc_load_qlf searched the
            % build folders and found the %TEMP%/s32k folder of another run (deleted during this one)
            setenv('NX_RTE_GENERATED', tc.Fixtures);
            cd(tc.Work);
            Simulink.fileGenControl('set', 'CodeGenFolder', tc.Work, 'CacheFolder', tc.Work, 'createDir', true);
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

        function data = readJson(file)
            data = jsondecode(fileread(file));
        end

        function port = portOf(report, name)
            port = [];
            ports = SwcPortsTest.cellOf(report.ports);
            for k = 1:numel(ports)
                if strcmp(ports{k}.name, name)
                    port = ports{k};
                    return;
                end
            end
        end

        function stamp = folderStamp(folder)
            % '' if the folder does not exist, else name and date of every entry - what changes when it is written
            stamp = '';
            if isfolder(folder)
                entries = dir(fullfile(folder, '**'));
                entries = entries(~ismember({entries.name}, {'.', '..'}));   % their date is the parent's
                stamp = strjoin(sort(strcat({entries.folder}, '/', {entries.name}, '@', {entries.date})), newline);
            end
        end

        function c = cellOf(v)
            if isempty(v)
                c = {};
            elseif iscell(v)
                c = reshape(v, 1, []);
            else
                c = num2cell(reshape(v, 1, []));
            end
        end
    end

    methods (Access = private)
        function d = component(tc, tag)
            % A fresh copy of the fixture with its model; every test works on its own folder.
            d = fullfile(tc.Work, tag, tc.Name);
            if isfolder(d)
                rmdir(d, 's');
            end
            mkdir(d);
            copyfile(fullfile(tc.Fixtures, tc.Name, 'swc.yaml'), fullfile(d, 'swc.yaml'));
            swc_create(d, 'Logic', [tc.Name '_logic']);
        end

        function r = report(tc, d)
            file = fullfile(tc.Work, 'report.json');
            swc_ports(d, 'report', file);
            r = SwcPortsTest.readJson(file);
        end

        function file = plan(tc, d, actions, withBackup)
            if nargin < 4
                withBackup = true;
            end
            backup = fullfile(tc.Work, [tc.Name '_backup.slx']);
            if withBackup
                copyfile(fullfile(d, [tc.Name '.slx']), backup);
            elseif isfile(backup)
                delete(backup);
            end
            p = struct('name', tc.Name, 'backup', strrep(backup, '\', '/'));
            p.actions = actions;
            file = fullfile(tc.Work, 'plan.json');
            fid = fopen(file, 'w', 'n', 'UTF-8');
            fprintf(fid, '%s', jsonencode(p));
            fclose(fid);
        end
    end

    methods (Test)

        function reportTellsWhatHangsOnEveryPort(tc)
            % The preview stands or falls with this: a port that carries logic must not look free.
            d = tc.component('report');
            r = tc.report(d);
            tc.verifyEqual(r.name, tc.Name);
            tc.verifyTrue(r.autosar);
            tc.verifyEqual(numel(SwcPortsTest.cellOf(r.ports)), 6);
            tc.verifyEqual(numel(SwcPortsTest.cellOf(r.runnables)), 2);
            fast = SwcPortsTest.cellOf(r.runnables);
            fast = fast{cellfun(@(q) strcmp(q.name, 'Fast5ms'), fast)};
            tc.verifyEqual(fast.subsystem, [tc.Name '/PortTestSwc_task5ms']);     % TOOL-180: named after the rate

            raw = SwcPortsTest.portOf(r, 'RawLevel_mm');
            tc.verifyEqual(raw.direction, 'in');
            tc.verifyEqual(raw.simulink_type, 'uint16');
            tc.verifyEqual(cellstr(raw.runnables), {'Fast5ms'});
            tc.verifyFalse(logical(raw.free), 'RawLevel_mm carries the logic of the user');
            attached = SwcPortsTest.cellOf(raw.attached);
            tc.verifyEqual(cellstr(attached{1}.blocks), {'RawToSigned'});

            level = SwcPortsTest.portOf(r, 'Level_mm');
            tc.verifyEqual(level.direction, 'out');
            tc.verifyFalse(logical(level.free));

            spareIn = SwcPortsTest.portOf(r, 'SpareIn');
            tc.verifyTrue(logical(spareIn.free), 'SpareIn only carries the placeholder of swc_create');
            spareOut = SwcPortsTest.portOf(r, 'SpareOut');
            tc.verifyTrue(logical(spareOut.free));

            % a port read by two runnables carries the placeholder in both, and is free
            mode = SwcPortsTest.portOf(r, 'Mode');
            tc.verifyEqual(sort(cellstr(mode.runnables)), {'Fast5ms'; 'Slow50ms'});
            tc.verifyTrue(logical(mode.free));
            % a port that branches to two blocks of the user names both
            offset = SwcPortsTest.portOf(r, 'Offset_mm');
            attached = SwcPortsTest.cellOf(offset.attached);
            tc.verifyEqual(sort(cellstr(attached{1}.blocks)), {'LevelSum'; 'OffsetProbe'});
        end

        function fixtureKeepsItsBranches(tc)
            % TOOL-107 (F4): without a branched line this test class cannot see the error it is here for. If the
            % fixture is ever simplified, this fails instead of the gap slipping through again.
            d = tc.component('branches');
            load_system(fullfile(d, [tc.Name '.slx']));
            closer = onCleanup(@() close_system(tc.Name, 0));   %#ok<NASGU>
            branched = {};
            for sys = {tc.Name, [tc.Name '/PortTestSwc_task5ms']}
                segments = find_system(sys{1}, 'SearchDepth', 1, 'FindAll', 'on', 'Type', 'line');
                for k = 1:numel(segments)
                    if ~isempty(get_param(segments(k), 'LineChildren'))
                        branched{end + 1} = sys{1};   %#ok<AGROW>
                    end
                end
            end
            tc.verifyEqual(sort(branched), sort({tc.Name, [tc.Name '/PortTestSwc_task5ms']}), ...
                'one branched line at the root (Mode) and one inside Fast5ms (Offset_mm)');
        end

        function aBranchedLineCountsOncePerDestination(tc)
            % The report used to break on a branched line ("sprintf ... not defined for cell inputs"). Now every
            % destination is one entry - so a line with two destinations is two entries, and a subsystem port is
            % named by its block inside, not by a number a removal would shift.
            d = tc.component('count');
            r = tc.report(d);
            lines = cellstr(r.lines);
            for expected = {[tc.Name ': Mode/1 -> PortTestSwc_task5ms/Mode'], ...
                            [tc.Name ': Mode/1 -> PortTestSwc_task50ms/Mode'], ...
                            [tc.Name '/PortTestSwc_task5ms: Offset_mm/1 -> LevelSum/2'], ...
                            [tc.Name '/PortTestSwc_task5ms: Offset_mm/1 -> OffsetProbe/1'], ...
                            [tc.Name ': Fast5ms/1 -> PortTestSwc_task5ms/trigger']}
                tc.verifyEqual(sum(strcmp(lines, expected{1})), 1, expected{1});
            end
            tc.verifyEqual(numel(unique(lines)), numel(lines), 'no connection is counted twice');
        end

        function aLostBranchIsExactlyOneEntryFewer(tc)
            % The other side of counting per destination: if ONE branch of a line disappears, the line list says
            % exactly that one - it must not hide behind the branch that stayed.
            d = tc.component('lostbranch');
            before = tc.report(d);
            load_system(fullfile(d, [tc.Name '.slx']));
            sys = [tc.Name '/PortTestSwc_task5ms'];
            probe = get_param([sys '/OffsetProbe'], 'Handle');
            segments = find_system(sys, 'SearchDepth', 1, 'FindAll', 'on', 'Type', 'line');
            branch = [];
            for k = 1:numel(segments)
                if isempty(get_param(segments(k), 'LineChildren')) && ...
                        isequal(get_param(segments(k), 'DstBlockHandle'), probe)
                    branch = segments(k);
                end
            end
            tc.assertNotEmpty(branch, 'the branch to OffsetProbe');
            delete_line(branch);          % Simulink joins the rest of the line; the other handles are stale now
            save_system(tc.Name);
            close_system(tc.Name, 0);
            after = tc.report(d);
            tc.verifyEqual(setdiff(before.lines, after.lines), ...
                {[tc.Name '/PortTestSwc_task5ms: Offset_mm/1 -> OffsetProbe/1']});
            tc.verifyEmpty(setdiff(after.lines, before.lines));
            tc.verifyEqual(numel(after.lines), numel(before.lines) - 1);
        end

        function renamingABranchedPortKeepsEveryBranch(tc)
            % Mode branches at the root to two subsystems. After the rename every line of the old model is in the
            % new one with the new name - the proof of the Studio (renames applied, then compared) finds no loss,
            % and a line with two destinations is neither "one lost, one new" nor two lost.
            d = tc.component('renamebranch');
            before = tc.report(d);
            file = tc.plan(d, {struct('kind', 'rename', 'name', 'Mode', 'new_name', 'OpMode')});
            out = evalc('swc_ports(d, ''apply'', file)');
            after = tc.report(d);
            renamed = regexprep(before.lines, '(?<![A-Za-z0-9_])Mode(?![A-Za-z0-9_])', 'OpMode');
            renamed = regexprep(renamed, '(?<![A-Za-z0-9_])Mode_Unused(?![A-Za-z0-9_])', 'OpMode_Unused');
            tc.verifyEqual(sort(after.lines), sort(renamed));
            tc.verifySubstring(out, sprintf('renamed: %s/Mode -> %s/OpMode', tc.Name, tc.Name));
            tc.verifyEmpty(strfind(out, 'gone'), 'a rename is not reported as gone');
        end

        function removingAPortWhoseRootLineBranches(tc)
            % Mode carries only placeholders, in two subsystems; its root line branches to both.
            d = tc.component('removebranch');
            before = tc.report(d);
            file = tc.plan(d, {struct('kind', 'remove', 'name', 'Mode', 'detach', false)});
            out = evalc('swc_ports(d, ''apply'', file)');
            after = tc.report(d);
            tc.verifyEmpty(SwcPortsTest.portOf(after, 'Mode'));
            tc.verifyEqual(sort(setdiff(before.blocks, after.blocks)), ...
                sort({[tc.Name '/Mode']; [tc.Name '/PortTestSwc_task5ms/Mode']; ...
                      [tc.Name '/PortTestSwc_task5ms/Mode_Unused']; [tc.Name '/PortTestSwc_task50ms/Mode']; ...
                      [tc.Name '/PortTestSwc_task50ms/Mode_Unused']}));
            tc.verifyEmpty(setdiff(after.blocks, before.blocks), 'no new block');
            % exactly the lines of Mode went - both branches at the root, the placeholders inside
            gone = setdiff(before.lines, after.lines);
            tc.verifyTrue(all(contains(gone, 'Mode')), strjoin(gone, newline));
            tc.verifyEqual(numel(gone), 4);
            tc.verifyEmpty(setdiff(after.lines, before.lines), 'no new line');
            tc.verifySubstring(out, sprintf('gone: %s/Mode', tc.Name));
        end

        function renameKeepsEveryBlockAndLine(tc)
            d = tc.component('rename');
            before = tc.report(d);
            file = tc.plan(d, {struct('kind', 'rename', 'name', 'Offset_mm', 'new_name', 'Bias_mm')});
            out = evalc('swc_ports(d, ''apply'', file)');
            after = tc.report(d);
            % TOOL-107 (F11): MATLAB names a rename as a rename, as the Studio does - no "gone" for a block that
            % only changed its name
            tc.verifySubstring(out, sprintf('renamed: %s/Offset_mm -> %s/Bias_mm', tc.Name, tc.Name));
            tc.verifyEmpty(strfind(out, 'gone'));

            tc.verifyEmpty(SwcPortsTest.portOf(after, 'Offset_mm'));
            bias = SwcPortsTest.portOf(after, 'Bias_mm');
            tc.verifyEqual(bias.direction, 'in');
            tc.verifyEqual(bias.simulink_type, 'int16');
            tc.verifyFalse(logical(bias.free), 'the logic stays on the port');
            attached = SwcPortsTest.cellOf(bias.attached);
            tc.verifyEqual(sort(cellstr(attached{1}.blocks)), {'LevelSum'; 'OffsetProbe'});

            % nothing but the two blocks of that port changed its name; the count of blocks and lines is equal
            tc.verifyEqual(numel(before.blocks), numel(after.blocks));
            tc.verifyEqual(numel(before.lines), numel(after.lines));
            moved = setdiff(before.blocks, after.blocks);
            tc.verifyEqual(sort(moved), sort({[tc.Name '/Offset_mm']; [tc.Name '/PortTestSwc_task5ms/Offset_mm']}));
            fresh = setdiff(after.blocks, before.blocks);
            tc.verifyEqual(sort(fresh), sort({[tc.Name '/Bias_mm']; [tc.Name '/PortTestSwc_task5ms/Bias_mm']}));

            % AUTOSAR follows, otherwise swc_build would write the old port into the ARXML
            load_system(fullfile(d, [tc.Name '.slx']));
            props = autosar.api.getAUTOSARProperties(tc.Name);
            comp = find(props, [], 'AtomicComponent', 'PathType', 'FullyQualified');
            ports = find(props, comp{1}, 'DataReceiverPort', 'PathType', 'FullyQualified');
            tc.verifyTrue(any(endsWith(ports, '/Bias_mm')), 'AUTOSAR receiver port renamed');
            tc.verifyFalse(any(endsWith(ports, '/Offset_mm')));
            slMap = autosar.api.getSimulinkMapping(tc.Name);
            [arPort, arElement] = getInport(slMap, 'Bias_mm');
            tc.verifyEqual(arPort, 'Bias_mm');
            tc.verifyEqual(arElement, 'Bias_mm');
            close_system(tc.Name, 0);
        end

        function addPutsThePortIntoRootAndSubsystem(tc)
            d = tc.component('add');
            before = tc.report(d);
            tc.plan(d, {struct('kind', 'add', 'name', 'Temp_dC', 'direction', 'in', ...
                               'simulink_type', 'single', 'runnables', {{'Slow50ms'}})});
            swc_ports(d, 'apply', fullfile(tc.Work, 'plan.json'));
            after = tc.report(d);

            temp = SwcPortsTest.portOf(after, 'Temp_dC');
            tc.verifyNotEmpty(temp);
            tc.verifyEqual(temp.direction, 'in');
            tc.verifyEqual(temp.simulink_type, 'single');
            tc.verifyEqual(cellstr(temp.runnables), {'Slow50ms'});
            tc.verifyTrue(logical(temp.free), 'a new port starts on its placeholder');
            % every block that was there is still there
            tc.verifyEmpty(setdiff(before.blocks, after.blocks));
            tc.verifyEqual(sort(setdiff(after.blocks, before.blocks)), ...
                sort({[tc.Name '/Temp_dC']; [tc.Name '/PortTestSwc_task50ms/Temp_dC']; ...
                      [tc.Name '/PortTestSwc_task50ms/Temp_dC_Unused']}));
            tc.verifyEmpty(setdiff(before.lines, after.lines));
            % TOOL-180: a new inport is green, at the root and in the subsystem
            load_system(fullfile(d, [tc.Name '.slx']));
            closer = onCleanup(@() close_system(tc.Name, 0));   %#ok<NASGU>
            tc.verifyEqual(get_param([tc.Name '/Temp_dC'], 'BackgroundColor'), 'green');
            tc.verifyEqual(get_param([tc.Name '/PortTestSwc_task50ms/Temp_dC'], 'BackgroundColor'), 'green');
        end

        function modelWithSubsystemNamesOfBeforeIsStillRead(tc)
            % TOOL-180: models created before are not renamed - their <Runnable>_Subsystem is found through the
            % trigger line, reported, and a new port goes into it (outports red)
            d = tc.component('oldnames');
            load_system(fullfile(d, [tc.Name '.slx']));
            set_param([tc.Name '/PortTestSwc_task5ms'], 'Name', 'Fast5ms_Subsystem');
            set_param([tc.Name '/PortTestSwc_task50ms'], 'Name', 'Slow50ms_Subsystem');
            save_system(tc.Name);
            close_system(tc.Name, 0);
            before = tc.report(d);
            runnables = SwcPortsTest.cellOf(before.runnables);
            tc.verifyEqual(sort(cellfun(@(q) q.subsystem, runnables, 'UniformOutput', false)), ...
                sort({[tc.Name '/Fast5ms_Subsystem'], [tc.Name '/Slow50ms_Subsystem']}));
            tc.verifyFalse(logical(SwcPortsTest.portOf(before, 'RawLevel_mm').free));
            tc.plan(d, {struct('kind', 'add', 'name', 'Warn', 'direction', 'out', ...
                               'simulink_type', 'boolean', 'runnables', {{'Fast5ms'}})});
            swc_ports(d, 'apply', fullfile(tc.Work, 'plan.json'));
            after = tc.report(d);
            tc.verifyEqual(sort(setdiff(after.blocks, before.blocks)), ...
                sort({[tc.Name '/Warn']; [tc.Name '/Fast5ms_Subsystem/Warn']; ...
                      [tc.Name '/Fast5ms_Subsystem/Warn_Default']}));
            tc.verifyEmpty(setdiff(before.blocks, after.blocks));
            load_system(fullfile(d, [tc.Name '.slx']));
            closer = onCleanup(@() close_system(tc.Name, 0));   %#ok<NASGU>
            tc.verifyEqual(get_param([tc.Name '/Warn'], 'BackgroundColor'), 'red');
            tc.verifyEqual(get_param([tc.Name '/Fast5ms_Subsystem/Warn'], 'BackgroundColor'), 'red');
        end

        function removingAFreePortNeedsNoPermission(tc)
            % SpareIn/SpareOut carry only the placeholders swc_create made - no work of the user is at stake.
            d = tc.component('free');
            before = tc.report(d);
            tc.plan(d, {struct('kind', 'remove', 'name', 'SpareIn', 'detach', false), ...
                        struct('kind', 'remove', 'name', 'SpareOut', 'detach', false)});
            swc_ports(d, 'apply', fullfile(tc.Work, 'plan.json'));
            after = tc.report(d);

            tc.verifyEmpty(SwcPortsTest.portOf(after, 'SpareIn'));
            tc.verifyEmpty(SwcPortsTest.portOf(after, 'SpareOut'));
            tc.verifyEqual(sort(setdiff(before.blocks, after.blocks)), ...
                sort({[tc.Name '/SpareIn']; [tc.Name '/SpareOut']; ...
                      [tc.Name '/PortTestSwc_task50ms/SpareIn']; [tc.Name '/PortTestSwc_task50ms/SpareIn_Unused']; ...
                      [tc.Name '/PortTestSwc_task50ms/SpareOut']; [tc.Name '/PortTestSwc_task50ms/SpareOut_Default']}));
            tc.verifyEmpty(setdiff(after.blocks, before.blocks), 'no new block');
            % the logic of the other runnable is untouched
            tc.verifyNotEmpty(SwcPortsTest.portOf(after, 'Level_mm'));
            tc.verifyTrue(any(strcmp(after.blocks, [tc.Name '/PortTestSwc_task5ms/LevelSum'])));
        end

        function removingAUsedPortIsRefused(tc)
            d = tc.component('refuse');
            before = tc.report(d);
            tc.plan(d, {struct('kind', 'remove', 'name', 'Offset_mm', 'detach', false)});
            tc.verifyError(@() swc_ports(d, 'apply', fullfile(tc.Work, 'plan.json')), 'swc_ports:attached');
            after = tc.report(d);
            tc.verifyEqual(after.blocks, before.blocks, 'the model is unchanged after a refused plan');
            tc.verifyEqual(after.lines, before.lines);
        end

        function detachKeepsTheLogicAndMarksThePlace(tc)
            d = tc.component('detach');
            before = tc.report(d);
            tc.plan(d, {struct('kind', 'remove', 'name', 'Offset_mm', 'detach', true)});
            swc_ports(d, 'apply', fullfile(tc.Work, 'plan.json'));
            after = tc.report(d);

            tc.verifyEmpty(SwcPortsTest.portOf(after, 'Offset_mm'));
            gone = setdiff(before.blocks, after.blocks);
            tc.verifyEqual(sort(gone), sort({[tc.Name '/Offset_mm']; [tc.Name '/PortTestSwc_task5ms/Offset_mm']}));
            tc.verifyEqual(setdiff(after.blocks, before.blocks), ...
                {[tc.Name '/PortTestSwc_task5ms/Offset_mm_Removed']}, 'the place is marked, not emptied');
            % every block of the user is still there
            for block = {'LevelSum', 'RawToSigned', 'LevelLimit', 'OffsetProbe'}
                tc.verifyTrue(any(strcmp(after.blocks, [tc.Name '/PortTestSwc_task5ms/' block{1}])), block{1});
            end
            % and it is still fed: the stub took the place of the port
            % ... and on BOTH branches the port had (TOOL-107): the stub feeds every destination
            tc.verifyTrue(any(strcmp(after.lines, ...
                [tc.Name '/PortTestSwc_task5ms: Offset_mm_Removed/1 -> LevelSum/2'])));
            tc.verifyTrue(any(strcmp(after.lines, ...
                [tc.Name '/PortTestSwc_task5ms: Offset_mm_Removed/1 -> OffsetProbe/1'])));
            % the loss proof sees exactly the lines of the port: Offset_mm was port 2 of PortTestSwc_task5ms and
            % Mode, port 3 before, is port 2 now - named by its block, the root line of Mode did not change
            tc.verifyEqual(sort(setdiff(before.lines, after.lines)), ...
                sort({[tc.Name ': Offset_mm/1 -> PortTestSwc_task5ms/Offset_mm']; ...
                      [tc.Name '/PortTestSwc_task5ms: Offset_mm/1 -> LevelSum/2']; ...
                      [tc.Name '/PortTestSwc_task5ms: Offset_mm/1 -> OffsetProbe/1']}));
            tc.verifyEqual(sort(setdiff(after.lines, before.lines)), ...
                sort({[tc.Name '/PortTestSwc_task5ms: Offset_mm_Removed/1 -> LevelSum/2']; ...
                      [tc.Name '/PortTestSwc_task5ms: Offset_mm_Removed/1 -> OffsetProbe/1']}));
        end

        function backupIsRequiredBeforeAnythingIsWritten(tc)
            d = tc.component('backup');
            before = tc.report(d);
            tc.plan(d, {struct('kind', 'remove', 'name', 'SpareIn', 'detach', false)}, false);
            tc.verifyError(@() swc_ports(d, 'apply', fullfile(tc.Work, 'plan.json')), 'swc_ports:backup');
            after = tc.report(d);
            tc.verifyEqual(after.blocks, before.blocks);
        end

        function aWorkFolderThatHoldsTheComponentIsRefused(tc)
            % swc_build empties <workRoot>/<name> before every build; if that were the component folder, it would
            % delete the model. Refused before anything happens (no code generation needed to show it).
            d = tc.component('workguard');
            tc.verifyError(@() swc_build(d, fileparts(d)), 'swc_build:work');
            tc.verifyTrue(isfile(fullfile(d, [tc.Name '.slx'])), 'the model is still there');
        end

        function changedPortsReachTheArxml(tc)
            % The whole point of writing into the model: swc_build afterwards describes the new ports, so
            % tools/rtegen sees them. One build covers all three cases at once.
            d = tc.component('build');
            tc.plan(d, {struct('kind', 'rename', 'name', 'Offset_mm', 'new_name', 'Bias_mm'), ...
                        struct('kind', 'remove', 'name', 'SpareOut', 'detach', false), ...
                        struct('kind', 'add', 'name', 'Temp_dC', 'direction', 'in', ...
                               'simulink_type', 'single', 'runnables', {{'Slow50ms'}})});
            swc_ports(d, 'apply', fullfile(tc.Work, 'plan.json'));
            % TOOL-107 (gap of TOOL-105): the work folder is an argument - the Studio passes <build dir>/swc so
            % nothing lands next to the script (in a customer repository: the pinned neXtBSW tree)
            workRoot = fullfile(tc.Work, 'build', 'swc');
            defaultWork = fullfile(tc.Repo, 'build', 'swc', tc.Name);
            stamp = SwcPortsTest.folderStamp(defaultWork);
            swc_build(d, workRoot);
            tc.verifyTrue(isfolder(fullfile(workRoot, tc.Name, [tc.Name '_autosar_rtw'])), ...
                'the code generation ran in the work folder that was passed');
            tc.verifyEqual(SwcPortsTest.folderStamp(defaultWork), stamp, ...
                'nothing was written into <repo>/build/swc');

            component = fileread(fullfile(d, 'generated', 'arxml', [tc.Name '_component.arxml']));
            tc.verifySubstring(component, '<SHORT-NAME>Bias_mm</SHORT-NAME>');
            tc.verifySubstring(component, '<SHORT-NAME>Temp_dC</SHORT-NAME>');
            tc.verifyEmpty(strfind(component, 'Offset_mm'), 'the old name is gone');
            tc.verifyEmpty(strfind(component, 'SpareOut'), 'the removed port is gone');
            interfaces = fileread(fullfile(d, 'generated', 'arxml', [tc.Name '_interface.arxml']));
            tc.verifySubstring(interfaces, '<SHORT-NAME>Temp_dC</SHORT-NAME>');
            tc.verifySubstring(interfaces, 'float32');       % the type of the added port reached the interface
            tc.verifyEmpty(strfind(interfaces, 'SpareOut'));

            code = fileread(fullfile(d, 'generated', 'src', [tc.Name '.c']));
            tc.verifySubstring(code, 'Rte_Read_Bias_mm_Bias_mm(');
            tc.verifyEmpty(strfind(code, 'SpareOut'));
            % A new port starts on the placeholder swc_ports puts there, and Embedded Coder does not generate a
            % read for a value nothing uses. It is in the ARXML - tools/rtegen sees the port and can connect it -
            % and gets its Rte_Read as soon as the logic in the model uses it. Measured on 2026-09-24.
            tc.verifyEmpty(strfind(code, 'Temp_dC'), 'a port on its placeholder is not read yet');
        end
    end
end
