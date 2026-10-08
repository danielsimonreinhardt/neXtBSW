# tools/setup – the Python environment of a workspace (TOOL-63)

A customer works in their own repository – the **workspace** – with neXtBSW as a submodule on a fixed tag
([`doc/architecture/customer-project.md`](../../doc/architecture/customer-project.md), section 5.3, gap N8). The
generators, the configuration check and the build of that workspace run with **the workspace's own** `.venv`, built
from the requirements and the constraints of exactly that pinned tree. This is the one command that creates it:

```sh
nextbsw/.venv/Scripts/python -m tools.setup venv .              # from the workspace root, core packages
python -m tools.setup venv <workdir>/acme_bcm                     # or with the folder named
python -m tools.setup venv <workdir>/acme_bcm --studio            # plus PySide6 and the Studio test tools
python -m tools.setup check <workdir>/acme_bcm                    # report only, writes nothing
```

Without a folder the command walks up from the current one and takes the first folder holding `nextbsw.yaml` – the
same rule [`tools/nxconfig/workspace.py`](../nxconfig/workspace.py) uses, so both tools always mean the same
workspace. It runs with any Python 3.12 (only the standard library), needs no Qt and does not import the Studio.

## Which packages

| File | Content | Who needs it |
|---|---|---|
| [`../requirements.txt`](../requirements.txt) | core: jinja2, jsonschema, PyYAML, pydantic, ruamel.yaml, python-can, udsoncan, can-isotp, intelhex, pyserial, ziglang, cantools | generators, `nxconfig`, host tests, CI – everything that builds and checks |
| [`../requirements-studio.txt`](../requirements-studio.txt) | PySide6, pytest, pytest-qt, pyinstaller | only `tools/studio` (GUI, its test suite, the `.exe` build) |
| [`../ci/constraints.txt`](../ci/constraints.txt) | the exact version of every package of both files | the one place a version is written down |

A workspace that only builds and checks installs the core list and stays free of Qt. `--studio` adds the second
file; it never replaces the first.

## What the command decides

The state of `<workspace>/.venv` is read first, and only then is anything run. A second run therefore changes
nothing, and a broken environment is named instead of being quietly overwritten:

| State | Meaning | What happens |
|---|---|---|
| missing | no `.venv` | create, install, write the marker |
| current | marker fits the tree, all packages at their pinned version | nothing is run at all ("Nichts zu tun") |
| stale | the tree's requirements/constraints changed, or another profile is asked for | install over it, marker rewritten |
| incomplete | marker fits, packages are missing or at the wrong version | the missing ones are named and installed |
| broken | `.venv` without an interpreter, or an unreadable marker | named, exit code 1 – `--recreate` rebuilds it |
| unmarked | an environment this command did not create | named – `--adopt` checks and takes it over, `--recreate` replaces it |
| other tree | the marker names a different neXtBSW tree | named, exit code 1 – `--recreate` |

`--adopt` never installs: it compares what is installed with `constraints.txt` and only writes the marker when
everything fits. That is how an environment somebody created by hand (`pip install -r …`) becomes a marked one.

## The marker

`<workspace>/.venv/nextbsw-venv.json` records the tree, its `VERSION`, the profile, the interpreter version and a
SHA-256 over the requirement files, `constraints.txt` and `.python-version`. It is written **last** – an
interrupted installation is never taken for a complete one (the same rule the tool cache of `tools/ci` follows).

After a pin move the hash no longer fits, and the tools say so instead of generating with the wrong packages:

- `python -m tools.setup check <workspace>` – exit code 1 and the command that repairs it,
- `python -m tools.ci check` – a finding "Umgebung" (a warning, never red; an environment without a marker stays
  silent),
- the Studio's tool page (**Werkzeuge**) names the same command.

## Own tests

```sh
python -m unittest tools/setup/test_setup.py
```

A synthetic workspace and a synthetic tree in `%TEMP%` with a recorded `pip`: nothing is downloaded and no package
is installed – what is tested is the decision, not the installation. One test creates a real, empty venv locally.
