% SPDX-License-Identifier: Apache-2.0
function spec = swc_read_spec(swcDir, python)
%SWC_READ_SPEC Reads and checks <swcDir>/swc.yaml through tools/swc/swc_yaml.py (json) and returns the decoded struct.
%   spec = swc_read_spec('swc/NewSwc') is the reader of swc_create and swc_prepare (TOOL-135): both need the same
%   description, checked by the same rules, so there is one reader. Messages of swc_yaml.py (warnings too, with
%   YAML path and line) are printed; an invalid file is an error.
%
%   python: interpreter for swc_yaml.py (default: NX_PYTHON, then the .venv of the repo or of a parent folder,
%   then python on the PATH).

if nargin < 2
    python = '';
end
repo = fileparts(fileparts(fileparts(mfilename('fullpath'))));
yamlFile = fullfile(char(swcDir), 'swc.yaml');
if ~isfile(yamlFile)
    error('swc_create:noYaml', ['%s not found. Start from swc/template/swc.yaml ' ...
        '(copy it into the component folder and set name, runnables and ports).'], yamlFile);
end
if isempty(python)
    python = swc_python(repo);
end
jsonFile = [tempname '.json'];
cleanup = onCleanup(@() deleteIfExists(jsonFile));
tool = fullfile(repo, 'tools', 'swc', 'swc_yaml.py');
setenv('PYTHONUTF8', '1');
cmd = sprintf('"%s" "%s" json "%s" -o "%s" 2>&1', python, tool, yamlFile, jsonFile);
[status, output] = system(cmd);
if ~isempty(strtrim(output))
    fprintf('%s', output);   % messages with YAML path and line (warnings too)
end
if status ~= 0
    error('swc_create:yaml', '%s is invalid:\n%s', yamlFile, strtrim(output));
end
spec = jsondecode(fileread(jsonFile));
end

function deleteIfExists(f)
if isfile(f)
    delete(f);
end
end
