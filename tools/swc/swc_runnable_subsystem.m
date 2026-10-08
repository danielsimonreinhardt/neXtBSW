% SPDX-License-Identifier: Apache-2.0
function sys = swc_runnable_subsystem(mdl, runnable)
%SWC_RUNNABLE_SUBSYSTEM Full path of the function-call subsystem a runnable of a component model triggers (TOOL-180).
%   sys = swc_runnable_subsystem('NewSwc', 'task10ms') follows the line of the root function-call inport named
%   after the runnable to the subsystem it triggers, so the subsystem may carry any name: swc_create names it
%   after the rate (<Swc>_task<Period>ms, TOOL-180), models created before that have <Runnable>_Subsystem, and
%   the user may have renamed it. If the inport is not wired to a subsystem, the old name <Runnable>_Subsystem
%   is tried. '' if there is none. The model must be loaded.

sys = '';
blk = [mdl '/' runnable];
if getSimulinkBlockHandle(blk) > 0
    pc = get_param(blk, 'PortConnectivity');
    handles = [pc.DstBlock];
    handles = handles(handles > 0);
    for k = 1:numel(handles)
        if strcmp(get_param(handles(k), 'BlockType'), 'SubSystem') && ...
                strcmp(get_param(handles(k), 'Parent'), mdl)
            sys = getfullname(handles(k));
            return;
        end
    end
end
if getSimulinkBlockHandle([mdl '/' runnable '_Subsystem']) > 0
    sys = [mdl '/' runnable '_Subsystem'];
end
end
