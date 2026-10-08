% SPDX-License-Identifier: Apache-2.0
function python = swc_python(repo)
%SWC_PYTHON Python interpreter for the tools of tools/swc (swc_yaml.py, mdata.py).
%   NX_PYTHON, then the .venv of the repo or of a parent folder (a git worktree under
%   <repo>/.claude/worktrees/<name> uses the one of the main checkout), then python on the PATH.

python = getenv('NX_PYTHON');
if ~isempty(python)
    return;
end
folder = repo;
while true
    for candidate = {fullfile(folder, '.venv', 'Scripts', 'python.exe'), fullfile(folder, '.venv', 'bin', 'python')}
        if isfile(candidate{1})
            python = candidate{1};
            return;
        end
    end
    parent = fileparts(folder);
    if strcmp(parent, folder)
        break;
    end
    folder = parent;
end
python = 'python';
end
