% SPDX-License-Identifier: Apache-2.0
function PortTestSwc_logic(mdl, ~)
%PORTTESTSWC_LOGIC Logic of the port editing test component for swc_create (TOOL-14 test data).
%   Fast5ms:  Level_mm = int16(RawLevel_mm) - Offset_mm, limited to 0 .. 30000
%             Offset_mm also feeds OffsetProbe - a BRANCHED line inside a subsystem (TOOL-107)
%   Slow50ms: nothing - SpareIn and SpareOut keep the placeholders swc_create puts there, so the test has
%             both a port that carries work of the user and a port that carries none.
%   Mode is read by both runnables, so swc_create already draws a BRANCHED line at the root: one line from the
%   root inport to two subsystems. Real models branch everywhere; the TOOL-14 fixture had no branch at all, and
%   swc_ports broke on the first branched line of a real model (TOOL-107, F4). SwcPortsTest checks that both
%   branches are still here, so the fixture cannot lose them unnoticed.

s = swc_runnable_subsystem(mdl, 'Fast5ms');   % PortTestSwc_task5ms (TOOL-180)
add_block('simulink/Signal Attributes/Data Type Conversion', [s '/RawToSigned'], ...
    'Position', [180 30 240 60], 'OutDataTypeStr', 'int16');
add_block('simulink/Math Operations/Sum', [s '/LevelSum'], 'Position', [300 25 330 75], ...
    'Inputs', '+-', 'OutDataTypeStr', 'int16', 'SaturateOnIntegerOverflow', 'on');
add_block('simulink/Discontinuities/Saturation', [s '/LevelLimit'], 'Position', [390 30 440 70], ...
    'UpperLimit', '30000', 'LowerLimit', '0');
add_line(s, 'RawLevel_mm/1', 'RawToSigned/1', 'autorouting', 'on');
add_line(s, 'RawToSigned/1', 'LevelSum/1', 'autorouting', 'on');
add_line(s, 'Offset_mm/1', 'LevelSum/2', 'autorouting', 'on');
add_block('simulink/Sinks/Terminator', [s '/OffsetProbe'], 'Position', [300 110 320 130]);
add_line(s, 'Offset_mm/1', 'OffsetProbe/1', 'autorouting', 'on');     % second destination: a branch
add_line(s, 'LevelSum/1', 'LevelLimit/1', 'autorouting', 'on');
add_line(s, 'LevelLimit/1', 'Level_mm/1', 'autorouting', 'on');
end
