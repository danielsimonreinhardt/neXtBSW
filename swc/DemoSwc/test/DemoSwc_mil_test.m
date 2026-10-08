function failures = DemoSwc_mil_test(silTrace, work)
%DEMOSWC_MIL_TEST Model-in-the-loop test of DemoSwc (same stimuli and checks as DemoSwc_sil_test.c).
%   Run via tools/swc/swc_mil_test.m:
%     matlab -batch "addpath tools/swc; swc_mil_test('swc/DemoSwc')"
%   silTrace: optional CSV of the SIL test (DemoSwc_sil_test.exe <csv>) to compare call by call.
%   work:     folder for the MIL trace (build/mil/DemoSwc).
%
%   How the runnables are called: DemoSwc.slx is simulated directly as export-function model
%   (normal mode, never saved). The root function-call inports Run10ms and Run100ms receive
%   column vectors of call times as external input, so each runnable is called exactly at
%   those times. The call sequence below mirrors the SIL test (Init, then the four test
%   cases with continuous state). Every call is placed on the next multiple of its period
%   (10 ms / 100 ms, taken from the inport sample times) after the previous call, so the
%   order is kept and each runnable only runs at its own rate: Run10ms at 0 .. 10.11 s,
%   Run100ms at 10.2 .. 20.3 s. DemoSwc_Init is empty; the simulation start state is the init
%   state. Data inports get the stimulus value valid at each call time.

if nargin < 1
    silTrace = '';
end
if nargin < 2
    work = pwd;
end
mdl = 'DemoSwc';
failures = 0;
checks = 0;

% Signal qualifier constants of the model (QLF_*) from the generated Rte_Qlf.m, never as a number
% here or in the model (COM-03/COM-13).
[~, QLF] = swc_load_qlf();

%% ---- call sequence (as in DemoSwc_sil_test.c) ----
seqRunnable = {};
seqButton = false(0, 1);
seqNtc = zeros(0, 1, 'uint32');
seqQlf = zeros(0, 1, 'uint8');
button = false;
ntc = uint32(0);
% The blink of Led2 is released by the qualifier of PC_Command; QLF_OK from the first call on keeps
% the behaviour of the three original test cases unchanged.
qlf = QLF.QLF_OK;

% buttonDrivesLed1
button = true;   iBtnHigh = call('Run10ms');
button = false;  iBtnLow = call('Run10ms');
% led2BlinksWithOneHertz
iBlink = zeros(1000, 1);
for k = 1:1000
    iBlink(k) = call('Run10ms');
end
% led2IsReleasedByTheQualifier: 10 calls inside one half period of the blink (last toggle at call
% 1001, next at 1051), so every change of Led2 comes from the qualifier
qlf = QLF.QLF_OK;                   iQlfAnchor = call('Run10ms');
qlfErrors = {'QLF_ERROR_CRC', 'QLF_ERROR_ALIVE', 'QLF_ERROR_TIMEOUT'};
iQlfError = zeros(numel(qlfErrors), 1);
iQlfHealed = zeros(numel(qlfErrors), 1);
for k = 1:numel(qlfErrors)
    qlf = QLF.(qlfErrors{k});       iQlfError(k) = call('Run10ms');
    qlf = QLF.QLF_OK;               iQlfHealed(k) = call('Run10ms');
end
qlf = QLF.QLF_DEBOUNCE;             iQlfDebounce = call('Run10ms');
qlf = QLF.QLF_ERROR; button = true; iQlfBusError = call('Run10ms');
qlf = QLF.QLF_OK;   button = false; iQlfReleased = call('Run10ms');
% ntcFilterConverges
ntc = uint32(1000);  iNtcFirst = call('Run100ms');
for k = 1:100
    iNtcSettled = call('Run100ms');
end
ntc = uint32(0);     iNtcDrop = call('Run100ms');

%% ---- simulate ----
load_system(mdl);
swc_load_data(mdl, fileparts(fileparts(mfilename('fullpath'))));   % FilterAlpha from DemoSwc_data.m (RTE-03)
inports = find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Inport');
[~, order] = sort(cellfun(@(p) str2double(get_param(p, 'Port')), inports));
inports = inports(order);

% call times: next multiple of the runnable's period after the previous call (integer ms)
nCalls = numel(seqRunnable);
tMs = zeros(nCalls, 1);
last = -1;
for k = 1:nCalls
    periodMs = round(1000 * str2double(get_param([mdl '/' seqRunnable{k}], 'SampleTime')));
    tMs(k) = (floor(last / periodMs) + 1) * periodMs;
    last = tMs(k);
end
tCall = tMs / 1000;

ds = Simulink.SimulationData.Dataset;
for p = 1:numel(inports)
    name = get_param(inports{p}, 'Name');
    if strcmp(get_param(inports{p}, 'OutputFunctionCall'), 'on')
        ds = ds.addElement(tCall(strcmp(seqRunnable, name)), name);
    else
        switch name
            case 'Button1',      values = seqButton;
            case 'MotorNtc_mV',  values = seqNtc;
            case 'PcCommandQlf', values = seqQlf;
            otherwise, error('DemoSwc_mil_test:inport', 'no stimulus for inport %s', name);
        end
        ds = ds.addElement(timeseries(values, tCall), name);
    end
end
in = Simulink.SimulationInput(mdl);
in = in.setExternalInput(ds);
in = in.setModelParameter('StopTime', sprintf('%.3f', tCall(end)), 'SaveOutput', 'on', ...
    'SaveFormat', 'Dataset', 'SaveTime', 'off', 'SaveState', 'off', 'ReturnWorkspaceOutputs', 'on');
tSim = tic;
out = sim(in);
fprintf('simulated %d calls (%d x Run10ms, %d x Run100ms) up to t = %.2f s in %.1f s\n', nCalls, ...
    sum(strcmp(seqRunnable, 'Run10ms')), sum(strcmp(seqRunnable, 'Run100ms')), tCall(end), toc(tSim));

%% ---- per-call trace: outputs after each call, held between calls ----
names = {'Led1', 'Led2', 'MotorNtcFiltered_mV'};
owner = {'Run10ms', 'Run10ms', 'Run100ms'};
outVals = zeros(nCalls, numel(names));
scheduleOk = true;
for n = 1:numel(names)
    sig = [];
    for e = 1:out.yout.numElements
        el = out.yout{e};
        if strcmp(el.BlockPath.getBlock(1), [mdl '/' names{n}])
            sig = el.Values;
        end
    end
    mine = find(strcmp(seqRunnable, owner{n}));
    if isempty(sig) || numel(sig.Time) ~= numel(mine) || any(abs(sig.Time(:) - tCall(mine)) > 1e-9)
        scheduleOk = false;
        fprintf('  FAIL output %s not logged at the %s call times\n', names{n}, owner{n});
        continue;
    end
    col = zeros(nCalls, 1);
    col(mine) = double(sig.Data(:));
    held = 0;   % RTE buffers start at 0
    for k = 1:nCalls
        if strcmp(seqRunnable{k}, owner{n})
            held = col(k);
        end
        col(k) = held;
    end
    outVals(:, n) = col;
end
if ~scheduleOk
    failures = failures + 1;
end
led1 = outVals(:, 1);
led2 = outVals(:, 2);
filt = outVals(:, 3);

%% ---- checks (as in DemoSwc_sil_test.c) ----
% buttonDrivesLed1
check(led1(iBtnHigh) == 1, 'led1(iBtnHigh) == 1');
check(led1(iBtnLow) == 0, 'led1(iBtnLow) == 0');
% led2BlinksWithOneHertz
edges = 0;
lastEdge = -1;
wrongSpacing = 0;
previous = led2(iBlink(1) - 1);
for c = 0:999
    v = led2(iBlink(c + 1));
    if v ~= previous
        if lastEdge >= 0 && (c - lastEdge) ~= 50
            wrongSpacing = wrongSpacing + 1;
        end
        lastEdge = c;
        edges = edges + 1;
        previous = v;
    end
end
check(edges == 20, 'edges == 20');
check(wrongSpacing == 0, 'wrongSpacing == 0');
% led2IsReleasedByTheQualifier
check(led2(iQlfAnchor) == 1, 'led2(iQlfAnchor) == 1');
for k = 1:numel(qlfErrors)
    check(led2(iQlfError(k)) == 0, sprintf('led2 blocked by %s', qlfErrors{k}));
    check(led2(iQlfHealed(k)) == 1, sprintf('led2 released again after %s', qlfErrors{k}));
end
check(led2(iQlfDebounce) == 0, 'led2(iQlfDebounce) == 0');
check(led2(iQlfBusError) == 0, 'led2(iQlfBusError) == 0');
check(led1(iQlfBusError) == 1, 'led1(iQlfBusError) == 1');
check(led2(iQlfReleased) == 1, 'led2(iQlfReleased) == 1');
check(led1(iQlfReleased) == 0, 'led1(iQlfReleased) == 0');
% ntcFilterConverges
check(filt(iNtcFirst) == 125, 'filt(iNtcFirst) == 125');   % 0 + (1000 - 0) / 8
check(filt(iNtcSettled) == 1000, 'filt(iNtcSettled) == 1000');
check(filt(iNtcDrop) == 875, 'filt(iNtcDrop) == 875');

%% ---- trace file and SIL comparison ----
header = {'call', 'runnable', 'Button1', 'MotorNtc_mV', 'PcCommandQlf', 'Led1', 'Led2', ...
    'MotorNtcFiltered_mV'};
milTrace = fullfile(work, 'DemoSwc_mil_trace.csv');
fid = fopen(milTrace, 'w');
fprintf(fid, '%s\n', strjoin(header, ','));
for k = 1:nCalls
    fprintf(fid, '%d,%s,%d,%d,%d,%d,%d,%d\n', k, seqRunnable{k}, seqButton(k), seqNtc(k), ...
        seqQlf(k), led1(k), led2(k), filt(k));
end
fclose(fid);
fprintf('trace: %d calls written to %s\n', nCalls, milTrace);

if ~isempty(silTrace)
    sil = readtable(silTrace, 'TextType', 'char', 'Delimiter', ',');
    mil = readtable(milTrace, 'TextType', 'char', 'Delimiter', ',');
    if height(sil) ~= height(mil) || ~isequal(sil.Properties.VariableNames, mil.Properties.VariableNames)
        failures = failures + 1;
        fprintf('  FAIL SIL trace: %d calls, MIL trace: %d calls\n', height(sil), height(mil));
    else
        differ = ~strcmp(sil.runnable, mil.runnable);
        for v = [1, 3:numel(header)]
            differ = differ | (sil.(header{v}) ~= mil.(header{v}));
        end
        if any(differ)
            failures = failures + 1;
            k = find(differ, 1);
            fprintf('  FAIL SIL and MIL traces differ in %d calls, first at call %d:\n', sum(differ), k);
            fprintf('    SIL: %s\n    MIL: %s\n', strjoin(string(table2cell(sil(k, :))), ','), ...
                strjoin(string(table2cell(mil(k, :))), ','));
        else
            fprintf('SIL comparison: %d calls identical to %s\n', height(sil), silTrace);
        end
    end
end

close_system(mdl, 0);
fprintf('%d checks, %d failures\n', checks, failures);

    function idx = call(runnable)
        seqRunnable{end + 1, 1} = runnable;
        seqButton(end + 1, 1) = button;
        seqNtc(end + 1, 1) = ntc;
        seqQlf(end + 1, 1) = qlf;
        idx = numel(seqRunnable);
    end

    function check(cond, text)
        checks = checks + 1;
        if ~cond
            failures = failures + 1;
            st = dbstack(1);
            fprintf('  FAIL %s:%d: %s\n', [st(1).name '.m'], st(1).line, text);
        end
    end
end
