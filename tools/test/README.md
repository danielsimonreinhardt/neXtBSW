# tools/test – one command for all host tests (TOOL-04)

Before this tool, the test commands were spread over five places (`CLAUDE.md`, `README.md`, `swc/README.md`, the
file headers of the host tests, `CHANGELOG.md`) and had to be collected by hand. Whoever forgot a line did not notice:
the second build profile `buildtest_s32k148_min` had been unbuildable unnoticed since the IO-19 merge (ARCH-15).

`tools/test` runs all host tests from **one list** and returns one exit code for CI.

```sh
.venv/Scripts/python tools/test                          # all groups, without the slow mutation runs
.venv/Scripts/python tools/test --group host --jobs 6    # only the C/C++ host tests, in parallel
.venv/Scripts/python tools/test --group target           # only the target builds (demo + build profile S32K148)
.venv/Scripts/python tools/test --list                   # what would run
.venv/Scripts/python tools/test -k e2e --slow            # a selection, with mutation runs
.venv/Scripts/python tools/test --json lauf.json         # result per test, for tools
```

Prerequisites: the repository's `.venv` (`PYTHONUTF8=1`); for the groups `build`/`target` also `cmake`, `ninja` and
`arm-none-eabi-gcc` from the `PATH` or `NX_ARM_GCC_BIN`, and the submodule `external/openbsw`. If something is
missing, the affected group is **skipped** and named – it does not turn red. No hardware is accessed: no J-Link, no
CAN, no serial port, no MATLAB.

## Groups

| Group | Content |
|---|---|
| `python` | Python unit tests: nxconfig, bswgen (including interrupts, blgen and the module host tests), rtegen, dbcgen, todo, schedana, mcudb tools, e2e, canflash and bootloader host tests, board measurement scripts, platforms/common, ecuState, application UDS, linker template, self-test of this runner |
| `pytest` | neXtBSW Studio (`pytest tools/studio`, offscreen) |
| `host` | C/C++ host tests via `ziglang` (list from `README.md`, `swc/README.md` and the file headers): SBC, high-side switch, CAN gate, hard fault dump, E2E, CAN bit timing, system time in four clock profiles, ADC access, ADC lock, SafeSystem, DemoSwc SIL |
| `build` | host builds: bootloader DLL via `tools/canflash/hostbuild.py` (demo project and build profile S32K148), STM32 build test |
| `target` | configure and build `cmake --preset evalboard-s32k144-demo` and `buildtest-s32k148-min`, expected warning-free. If the second profile fails, **board neutrality** is broken (ARCH-09/ARCH-15), not the board to blame. |
| `matlab` | MATLAB/Simulink model tests of `tools/swc` (TOOL-97): `SwcCreateTest`, `SwcPortsTest`. `slow` and `requires: [matlab]`, so an ordinary run does not select them and a machine without MATLAB **skips** them instead of turning red. `parallel: false` on both – that is the only thing that keeps at most one MATLAB running (see below). |

The two mutation runs (`e2e-mutations` about 7 min, `bl-update-mutations` about 4 min) only run with `--slow`,
and so do the two MATLAB entries (`swc-matlab-create`, `swc-matlab-ports`, together far over 15 min).
The target builds are incremental; `--clean` builds them from scratch (`--clean-first`) – only then does
"warning-free" say something about the whole tree.

### Memory report per build profile (PLAT-29)

`memory-report-profiles` (group `target`) builds the application of the four build profiles (eval board demo and
charge project, S32K148, S32K116), links it once more next to the real image with `--noinhibit-exec
--print-memory-usage` (`tools/test/memreport.py`, the measuring link of PLAT-12) and holds the usage of every linker
region against its size and the reserve of `tools/test/memory_budget.yaml`. A profile that overflows therefore still
gets a complete report; the entry is red as soon as a region overflows or falls below its reserve. The report lists
the biggest items per library/object and symbol (stderr, `NX_MEMORY_REPORT_JSON=<file>` writes the numbers).
`memory-report-logic` (group `python`) tests the parsing and the budget logic on synthetic text without a toolchain.

### At most one MATLAB process

MATLAB may not run twice at once on the same tree (shared code generation folder `build/swc/<name>`, licence). The runner
has **no resource lock**; `parallel: false` is the only lever (the scheduler runs all non-parallel entries of a wave one
after another), so every entry with `requires: [matlab]` must also set `parallel: false`. Against a MATLAB started
elsewhere it does not protect; the Studio's lock file (`nextbsw-studio-matlab.lock`) is a Studio mechanism the runner
neither takes nor looks at.

## The list is the truth: `tests.yaml`

Each entry names group, kind (`unittest`, `pytest`, `script`, `command`, `zig`, `cmake_preset`), the command, a
comment and, under `covers`, the test files it runs. The runner guesses nothing.

To keep the list complete, the runner searches the repository after every run for `test_*.py`, `*HostTest.cpp`,
`*HostTest.c`, `*_host_test.cpp` and `*_sil_test.c`. A file that is in no `covers` and in no `inventory.ignore` line
turns the run **red** ("NICHT IN tools/test/tests.yaml", German for "not in tools/test/tests.yaml"). A new host test
therefore has to go into the list – or be excluded deliberately, with a reason.

### `covers` must be what really runs (TOOL-100)

`covers` feeds the completeness check, but what runs is `args` (`sources` for `zig`). A test file named in
`covers` and missing from `args` would count as listed and never run – the inventory would confirm itself. So
before every run (also with `--list`) the runner checks, for the kinds that hand files over themselves
(`pytest`, `unittest`, `script`: `args`; `zig`: `sources`), that every test file of `covers` (a name the inventory
patterns match) is handed over; otherwise the list is wrong (exit code 2, "Testliste fehlerhaft (covers gegen
args, TOOL-100)"). A pytest node id (`file.py::Case`), a root placeholder (`{repo}/`) and options such as
`-k Class` or `-q` do not matter: an entry that runs only part of a file with `-k` still names the file.

`args` is deliberately **not** derived from `covers`: `args` also carries options (`-q`, `-k`, several entries split
one file by `-k` – `elfcheck-*`, `clock-check-*`), and `covers` names more than the runner hands over (see below);
`tools/docgen` and `tools/distcheck` read `covers` as it is.

One named exception: a `pytest`/`unittest` entry can only hand over Python files. A C/C++ host test in its
`covers` (`bswgen`, `rtegen`, `ecustate`, `dem`, ...) is compiled and started by the Python test itself; it counts
as run only if a Python file of the entry's `args` names it by its file name. Files in `covers` that are no test
files (the module under test such as `pincheck.py`, a scenario, a header) are not inventoried and not checked.

Not checked: `command` (an arbitrary command line; the runner cannot know what it runs – `swc-matlab-*` name their
`.m` file inside the MATLAB call) and `cmake_preset` (builds, runs no test file). Also not checked: whether the
`-k` selections of all entries sharing a file together select every test class of it.

Further fields: `in_workspace` (TOOL-67, see below), `watch` (TOOL-79, see `--since` below), `requires` (tool missing → skipped), `needs_files`
(precondition from another group), `after`
(order, also applies with `--jobs N`, but only among entries that are selected anyway), `needs` (TOOL-52: entries
that build what this entry uses, e.g. `needs: [bl-host-dll]` - they are always run first, also with `-k` or
`--group`, so a single `-k canflash-info-host` does not use a stale DLL of the build folder; if one of them is not
green, the entry is skipped), `slow`, `parallel: false` (runs alone), `timeout`, `env`, `modules`
(below). Placeholders in paths: `{python}`, `{repo}`, `{tmp}` (a working directory of its own per test), `{build}`.

### Field `modules`: which module an entry tests

`modules` (optional, DOC-18) is a list of modules with a manifest that the entry demonstrably tests although none
of its `covers` lies in the module directory – for example `modules: [capture, hall]` on `bswgen`, which compiles
and runs the drivers of those port modules from `tools/bswgen/test` and `platforms/s32k1xx/bsp/bspHall/test`. A
module is named by its directory name (as in the catalogue); if two manifests share the name, by its directory
relative to the repository (`platforms/<family>/port/.../capture`). Only name a module whose code the test really
runs – compiling against a header is not enough.

The runner checks the field before every run (also with `--list`): a name that is not the directory of a
`module.yaml` or `module.yaml.pending` under `libs/*/`, `platforms/*/port/` or `ecu/platforms/*/`, or a name that
is not unique, is an error of the list (exit code 2, "Testliste fehlerhaft (Feld modules)").
[`tools/docgen`](../docgen/README.md) reads the field for the column *Host tests* of
[`doc/reference/modules.md`](../../doc/reference/modules.md): a test belongs to a module if one of its `covers` lies
in the module directory or if it names the module under `modules`. Without such an entry the module is listed
under *Gaps*.

## What a change touches: `--since` (TOOL-79)

```sh
.venv/Scripts/python tools/test --since --list                 # which entries the changes since origin/dev touch
.venv/Scripts/python tools/test --since --jobs 4               # ... and run them
.venv/Scripts/python tools/test --since agent/IO-48 --list     # against another base
.venv/Scripts/python tools/test --since 2950431e^1..2950431e --list   # replay what a past merge touched
.venv/Scripts/python tools/test -k nxconfig -k rtegen          # -k is repeatable: an entry matching any of them
```

**Why.** A mapping file → entry names the group that a change breaks (twice a test group landed red on `dev` because the
selection was made by hand).

**Which files.** `git diff --name-only` of the working tree against the merge base of `<ref>` (default
`origin/dev`) and `HEAD` - committed, staged, unstaged and untracked files alike, renames as deletion plus
addition. `A..B` compares two commits instead (no working tree).

**Which entries.** An entry is chosen if a changed file equals a path it names, or lies below one (a directory):

| Source | What it claims |
|---|---|
| `covers`, `watch` | always paths (`watch` also takes glob patterns, `*` crosses `/`) |
| `args`, `argv`, `sources`, `include` | only values that name an existing file or directory; `-m tools.x` becomes `tools/x` |
| import | derived: every repository module the entry's Python files import, transitively (parsed with `ast`, never run), plus the `conftest.py` files pytest loads. Resolved against the importing file's folder, the repository root and `tools/` |
| list | derived: an entry whose own lines in `tests.yaml` changed (or whose kind's `defaults`) is triggered by the list |

An entry **without any claim** (today only the `cmake_preset` target builds - their source is the whole tree) is
chosen whenever anything changed at all: nothing proves it unaffected. `--group`, `-k`, `--slow` and `--skip` still
narrow the selection; what they leave out of the triggered entries is named.

**Output.** Before the run (and with `--list`): per chosen entry the file that triggered it and why
(`covers: ...`, `watch: ...`, `import ueber <test file>`, `eigener Eintrag geaendert`), then the changed files **no
entry covers** - code and configuration apart from documentation. That list is the actual finding: code without a
test, or a test that depends on a file without saying so. It is repeated after the run. With `--json` the mapping
is written under `since` (in the run result and in the `--list` file). `--since` is refused with `--workspace`.

**Field `watch`.** For what a test depends on without running or importing it: a document it checks, test profiles it copies,
every `CMakeLists.txt` it scans (example: `watch: ["*CMakeLists.txt", "*.cmake"]` on `safety-tools`, which reads the build
files through `git ls-files`). `tools/test/test_run_tests.py` (`SinceMapping`, `SinceRealList`) covers it.

## A customer workspace: `--workspace` (TOOL-67)

A customer works in his own repository (the **workspace**: the folder with `nextbsw.yaml`, ARCH-56) with neXtBSW
as a submodule on a fixed tag. He checks, builds and tests his projects with the tool of the pinned tree, and his
tests never appear in the test list of the neXtBSW tree:

```sh
<tree>/.venv/Scripts/python <tree>/tools/test --workspace .            # his list plus the inherited entries
<tree>/.venv/Scripts/python <tree>/tools/test --workspace . --list     # what would run
<tree>/.venv/Scripts/python <tree>/tools/test --workspace . --list --json liste.json   # the same as data
<tree>/.venv/Scripts/python <tree>/tools/test --workspace . --skip target-main          # all but this entry
```

The step **Host-Tests** of neXtBSW Studio runs exactly this workspace run (TOOL-109): it reads the offer with
`--list --json` (name, group, kind, origin `workspace`/`tree`, comment of each entry) and hands the entries its user
switched off over as `--skip <name>` - an exact name, repeatable; an entry another one `needs` still runs, and a name
the list does not know is reported and ignored. Both options work in a tree run as well.

| | run in the tree | run in a workspace |
|---|---|---|
| list | `tools/test/tests.yaml` | `<workspace>/ci/tests.yaml` plus the tree entries marked `in_workspace` |
| working directory of an entry | the tree | the workspace (an inherited entry: the tree) |
| inventory | the tree, rules of `tests.yaml` | **the workspace**, rules of its own `ci/tests.yaml` |
| build root | `build/`, in a worktree `%TEMP%/s32k/…` | `<workspace>/build/nxtest`; one inside the tree is refused |
| placeholders | `{python} {repo} {tree} {workspace} {tmp} {build}`, `{tree}` = `{repo}` = `{workspace}` = the tree | `{tree}` = `{repo}` = the tree, `{workspace}` = the customer repository |

**Why `ci/tests.yaml`.** One folder `ci/` holds everything the customer's pipeline decides (`ci/tests.yaml`,
`ci/pipeline.toml`), so the workspace root stays the thin `CMakeLists.txt`, `CMakePresets.json` and `nextbsw.yaml`
(ARCH-57, gap N12). The format is the tree's format (version 1); `python -m tools.ci template tests --out <workspace>`
writes a commented starting point.

**Which entries of the tree carry over.** By default none. A tree entry runs in a workspace only with
`in_workspace: true`, and that mark belongs on entries that check a mechanism the workspace itself stands on,
run inside the tree and write nothing but their own `{tmp}`. Today those are `nxconfig-workspace` (pin, version,
search paths – ARCH-55/56) and `setup` (the environment the customer's `.venv` is made with). Everything else
builds or checks the tree's own projects and boards: re-running it in a customer's pipeline costs half an hour
and can only fail for reasons he must not be asked to fix – the release CI of the tree ran it before the tag.
A workspace that wants more names it itself, by entry name:

```yaml
inherit: [rtegen, dbcgen]     # top level of <workspace>/ci/tests.yaml; an unknown name is an error of the list
```

**The inventory never blames the customer:** in a workspace run the completeness check walks the **workspace** with the
patterns and `ignore` lines of `ci/tests.yaml`; the tree's test files are never reported, and without a `ci/tests.yaml` the
check is named as skipped. **The tree is only read:** the build root has to lie outside the tree, and
`tools/test/test_workspace.py` compares a snapshot of a synthetic tree before and after a complete run. A run without
`--workspace` is unchanged. What a workspace is and how its pin is checked: [`workspace.py`](workspace.py).

## Build directory

`{build}` is `build/` in the repository, but in an agent worktree under `.claude/worktrees/…` it is
`%TEMP%/s32k/<worktree name>`, because the object paths would otherwise exceed the Windows limit of 260 characters
(PLAT-06). The target builds are located in it under `demo` and `bt148`, the bootloader DLL under `host`.
`--build-root <path>` overrides this.

Host tests that read the demo target build (`test_flash_real_application_hex` in `bootloader-host`, the real
`bootloader.hex` in `bl-update-host` and `app-uds-host`) have no fixed build folder (PLAT-07). The rule lives in
`tools/test/build_dirs.py`: `NX_BUILD_DIR` (environment) first, then `{build}/demo`, then the binaryDir of the preset
`evalboard-s32k144-demo` (in a worktree `%TEMP%/s32k/<worktree name>`). The runner passes `NX_BUILD_DIR={build}/demo`
to every test once that directory is configured, also with `--build-root`; an `NX_BUILD_DIR` of your own wins. So
`tools/test -k target-demo` followed by `-k app-uds-host`, `-k bl-update-host` or `-k bootloader-host` runs these
tests without skips, in the repository and in a worktree. `BOOTLOADER_HEX` and `BL_HEX` still override the
bootloader image of the two tests.

## Coverage of the C/C++ host tests (TOOL-40)

`tools/test/coverage.py` measures **statement and branch coverage** of every `zig` entry and reports it per
module. It is a report, not a gate: the thresholds in `tools/test/coverage.yaml` are a proposal and only turn a
run red with `--enforce`.

```sh
.venv/Scripts/python tools/test/coverage.py --jobs 4                      # every zig entry
.venv/Scripts/python tools/test/coverage.py -k e2e --markdown cov.md      # a selection
.venv/Scripts/python tools/test/coverage.py --json cov.json --enforce     # below a threshold = exit code 1
```

**Why not `llvm-cov`.** The host tests are built with the `ziglang` package, which ships neither the compiler-rt profile
runtime nor `llvm-cov`; the gcov route carries:
| Step | Tool |
|---|---|
| instrument | `zig cc --coverage`, notes and data file placed with `-Xclang -coverage-notes-file/-coverage-data-file` |
| count at runtime | `tools/test/gcov_runtime.c` – the missing `llvm_gcda_*` symbols, writes the `.gcda` |
| read | `arm-none-eabi-gcov --json-format`, the ARM GCC the CI already pins (gcov reads notes of the host build; the compiler's target does not matter). `--gcov` or `NX_GCOV` name another one. |

The coverage build is a **second** build of the same entry: `-O0` (line mapping) and without `-Werror` (warnings
are the job of the normal run). Each run uses a define of its own, because ziglang caches compilations and on a
cache hit writes no notes file. Test code (`**/test/**`), the submodule (`external/**`) and generated code are
not counted – only the code under test.

A module is the directory of its `module.yaml` (as in `doc/reference/modules.md`); code without a manifest is
reported under its component directory, so nothing measured disappears. `tools/test/coverage.yaml` carries the
threshold per module together with the **reason** why it is that high – a threshold without a reason is an error
of the list. Safety-relevant modules (SYS-19) are higher than the default.

**A customer workspace (TOOL-217, gap N19):** `coverage.py --workspace <dir>` measures the `zig` entries of
`<dir>/ci/tests.yaml` only (the inherited tree entries test the tree), instruments and counts only files of the
workspace (never the pinned tree, also not as a submodule inside it, nor the build root or `.venv`), takes the
modules from the manifests below `search.modules` of `nextbsw.yaml` and the thresholds from `<dir>/ci/coverage.yaml`
(same format; starting point `python -m tools.ci template coverage`). Without that file the run ends with exit code
2 and names the template.

**Not measured:** the C/C++ host tests that a Python wrapper builds (`ecustate`, `safe-memory` and the mutation
runs, `kind: script`/`unittest`), and MC/DC – see `doc/changelog.d/TOOL-40.md` for the way there.

## Own tests

```sh
.venv/Scripts/python -m unittest tools/test/test_run_tests.py    # runner with invented groups, selection, list checks, --since
.venv/Scripts/python -m unittest tools/test/test_workspace.py    # workspace run against a synthetic tree and customer repository
```

`tools/test/test_coverage.py` measures `fixtures/coverage_probe.c` for real: its never called function and its untaken
condition must appear as uncovered.
