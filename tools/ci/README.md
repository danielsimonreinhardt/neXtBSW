# tools/ci – provider-neutral CI (TOOL-03)

All the logic of a CI pipeline lives here, not with the CI provider. A provider (today GitHub Actions,
`.github/workflows/ci.yml`) only checks out, provides Python and calls two commands. The same two commands run
locally:

```sh
python -m tools.ci setup --python-packages   # pinned tools into the cache, pinned Python packages
python -m tools.ci run --strict              # check tools, tools/test (all groups), artefacts, report
```

On the workstation with the `.venv` (the packages are already installed there): `.venv/Scripts/python -m tools.ci setup`
and `.venv/Scripts/python -m tools.ci run --jobs 4`. Further commands:

| Command | Purpose |
|---|---|
| `check [--toolchain local]` | tool, Python and package versions against the pinning, coverage of the build profiles |
| `run --toolchain local` | pipeline with the tools of the workstation (for example the GCC of a vendor IDE); deviations as warnings |
| `run -k <name>` / `--group <g>` | trial run with a selection (not with `--strict`) |
| `run --no-coverage` | pipeline without the coverage measurement (TOOL-40) |
| `run --no-static-analysis` | pipeline without the static analysis (PLAT-74) |
| `setup --python-packages --core-only` | core packages only, without PySide6 (TOOL-63); the Studio tests are then skipped |
| `setup --tool misra` | only the static analysis tools (pip wheels) into the cache |
| `setup --tool strictdoc` | only StrictDoc (DOC-21) into `<cache>/strictdoc-venv`; `run` announces it to tools/test as `NX_STRICTDOC_VENV`, `check` compares it with `constraints-docs.txt` |
| `lint` | `actionlint` (pinned version) over `.github/workflows` |
| `summary <out>` | print the Markdown report of a run (job summary) |
| `preflight [--since v<previous>]` | pre-flight before the release CI (TOOL-209, `doc/safety/verification-plan.md` 8.2): PF-1 freeze check, PF-2 `check --strict`, PF-3 increment since the previous release, PF-4 target build + static analysis + coverage, PF-5 safety documents; report `preflight.md/json`, step timeouts (`--timeout-factor`), a timed-out test is repeated once alone |
| `run --workspace <dir>` | the pipeline of a customer repository (TOOL-67, see below) |
| `template <name> [--out <workspace>]` | the CI files a workspace starts from (`github`, `gitlab`, `tests`, `pipeline`, `coverage`, `misra-deviations`) |
| `release [--version X.Y.Z] [--tag vX.Y.Z-rcN]` | release publication of a tagged baseline (TOOL-69, see below); a release candidate by its own tag (TOOL-232) |

## Pinning – every value exactly once

| File | Content |
|---|---|
| `versions.toml` | ARM GCC, CMake, Ninja (and actionlint): version, download URL and SHA-256 for Windows/Linux each |
| `../misra/requirements.txt` | cppcheck and clang-tidy (pip wheels) of the static analysis |
| `../requirements-docs.txt`, `constraints-docs.txt` | StrictDoc 0.30.1 and its 88 dependencies, exact pins, own venv (not the `.venv`, not the Studio build; the workstation `.venv` pins a different pypdf) |
| `.python-version` | Python interpreter; also read by `actions/setup-python` |
| `constraints.txt` | exact versions of all Python packages of `tools/requirements.txt` **and** `tools/requirements-studio.txt` |
| `pipeline.toml` | allowed skips, presets deliberately not built, steps outside CI, artefact patterns |
| `templates/` | the CI files a customer workspace starts from (TOOL-67); they call the same commands, they do not repeat their logic |

Which tests and target builds exist is stated only in `tools/test/tests.yaml` (TOOL-04).

`tools/requirements.txt` holds the **core** packages (generators, checks, host tests, CI); PySide6, pytest,
pytest-qt and pyinstaller are only needed by `tools/studio` and live in `tools/requirements-studio.txt`
(TOOL-63). `setup --python-packages` installs both, because the CI runs the Studio tests as well, and also
`tools/requirements-site.txt` (MkDocs of the documentation website, DOC-71; exact pins in the file itself, TOOL-232),
also with `--core-only` - without it `docgen-site` is skipped, and under `run --strict` a skipped entry is red. `check`
reports them separately: the core list must fit, the Studio list counts as "skipped" when it is absent and as a
finding when it is half installed or wrongly pinned. The environment of a customer workspace is built from the
same two files by [`tools/setup`](../setup/README.md); `check` names a stale one under "Umgebung".

The tool cache is located under `NX_CI_CACHE`, otherwise `%LOCALAPPDATA%/nextbsw/ci-cache` or
`~/.cache/nextbsw/ci-cache` (ARM GCC unpacked about 1 GB). A tool only counts as installed once the marker file with
the checksum has been written after unpacking.

## What `run` decides

| # | Stage | Decision |
|---|---|---|
| 1 | Provide the tools | pinned versions from the cache (missing ones are downloaded) at the front of the `PATH`, ARM GCC also as `NX_ARM_GCC_BIN` |
| 2 | Check | versions (`--strict`: every deviation is red); release identity (ARCH-55, SYS-68): a commit with tag `vX.Y.Z` or `vX.Y.Z-rcN` (release candidate, TOOL-232) must equal the root file `VERSION`, and any other tag starting with `v` + digit on it (`v1.0.0-rc`, `v1.0.0-beta1`) is red; every visible CMake preset is built by a target entry in `tests.yaml` or listed with a reason under `presets.not_built`, otherwise red („Profil ohne CI-Entscheidung“). A preset with a cache variable of its own besides `NX_PROJECT` is a profile of its own (`<project> [VAR=value]`, PLAT-71) |
| 3 | Tests and target builds | one call `python -m tools.test --group all --clean --json …`; with `--strict` every skipped test not listed under `allowed_skips` is red |
| 4 | Coverage (TOOL-40) | `tools/test/coverage.py` per module (`coverage.json`, `coverage.md`, table in the summary); only over the whole list (not with `-k`/`--group`/`--no-coverage`). `[coverage]` in `pipeline.toml`: `enabled`, `enforce` (below threshold red or warning, today a warning, SYS-19 stage 2), `strict` (failed measurement red or warning) |
| 5 | Static analysis (PLAT-74) | `python -m tools.misra run` over the compile database of the demo build ([`tools/misra`](../misra/README.md)); `[static_analysis]`: `enforce` (a finding in neither baseline nor deviation list is red), `strict_tools` (missing tool red with `--strict`, otherwise skipped); only over the whole list, `--no-static-analysis` skips it |
| 6 | Artefacts | ELF/HEX/MAP of the target builds with sizes |
| 7 | Report | `ci-result.json`, `summary.md`, `tests.json`, `tests.log` in `--out` (default `build/ci-out`); what lies outside CI (MATLAB, hardware, `doc/confidential`, keys) is listed as skipped |

**Provenance (SYS-67, cm-plan G1):** `ci-result.json` records under `provenance` the full commit, tree hash, `git describe`,
the OpenBSW gitlink, `VERSION` and whether the working tree was dirty, before the first stage and at the end
(`provenance.end`); the summary prints the line `Stand …`. A HEAD that moved during the run is red with `--strict`.

## A customer workspace (TOOL-67)

A customer has his own repository – the **workspace**, the folder with `nextbsw.yaml` (ARCH-56) – with neXtBSW as
a submodule on a fixed tag. He gets one run that judges *his* repository:

```sh
python -m tools.ci run --workspace <dir> --strict    # from the tree; <dir> is the customer repository
python -m tools.ci run --workspace <dir> --release   # plus: the pinned tree must be a released baseline
python -m tools.ci check --workspace <dir>           # pin, tools, environment, profile coverage only
python -m tools.ci template gitlab --out <dir>       # the CI file of his provider
```

What changes against a run in the tree, and nothing else:

| | tree | workspace |
|---|---|---|
| pipeline | `tools/ci/pipeline.toml` | `<workspace>/ci/pipeline.toml` (missing: the tree's, as a fallback) |
| tests | `tools/test/tests.yaml` | `tools/test --workspace <dir>`: `<workspace>/ci/tests.yaml` plus the tree entries marked `in_workspace` ([`tools/test`](../test/README.md)) |
| profiles | `CMakePresets.json` of the tree | `CMakePresets.json` of the workspace; a profile without a `target` entry and without a reason under `presets.not_built` is red, exactly as in the tree |
| build root, report | `build/…` of the tree | `<workspace>/build/nxtest`, `<workspace>/build/ci-out`; a build root inside the tree is refused |
| first stage | versions and profiles | **the pin** on top of it |

**The pin stage (ARCH-55/56).** `nextbsw.yaml` states which neXtBSW version the workspace is built with; the stage compares
it with the `VERSION` of the tree the run uses and checks that `nextbsw.path` points at that tree. A deviation is **red**: an
image built with another tree than the pin names cannot be reproduced. The `.venv` of the workspace is reported against
the requirements of the pinned tree (TOOL-63) as a warning.

**`--release`: the provenance of the pinned tree (TOOL-65, gap N10).** Version and path say *which* tree; they do
not say whether it is a **released** one. `tools/nxconfig/pincheck.py` answers that, and the stage reports it in
every run:

| finding | meaning | without `--release` | with `--release` |
|---|---|---|---|
| `no-tag` | the pinned commit carries no release tag – a development pin | warning | **error** |
| `dirty` | the pinned tree has local changes (tracked or untracked) | warning | **error** |
| `tag-lightweight` | `v<version>` exists, but as a bare pointer without author, date and message | warning | **error** |
| `tag-mismatch`, `tag-elsewhere` | the commit carries another release tag, or `v<version>` sits elsewhere in the history | warning | **error** |
| `tag-unreachable`, `master-unknown` | `v<version>` hangs off a side branch, or there is no `master` to measure against | warning | **error** |
| `overlay-version`, `overlay-missing` | an overlay (`search.overlays`) names another version, or is not fetched | warning | **error** |
| `no-git` with `source: archive` | no git checkout, on purpose – the identity is `url` plus `sha256` | note | note |

The findings are printed in every run; the flag decides whether they stop it, because a development pin is the state every
customer starts in and a pipeline that is red from day one teaches everybody to ignore it. `--release` is the point at which
an image leaves the house and has to be reproducible from a tag (customer-project.md 4.4 and 5.7). Every message names file,
line and YAML path of `nextbsw.yaml` and the command that fixes it. Without `--workspace`, `--release` asks the same question
of the tree the run builds. `nxconfig pin` prints the rule's own severities and exits 1 on an error.

The same rule read on its own, without a pipeline:

```sh
python tools/nxconfig pin <project.yaml | folder> [--release] [--json]
```

**Coverage and static analysis measure the customer's own code (TOOL-217, gap N19).** The thresholds in
`tools/test/coverage.yaml` and the baseline in `tools/misra` belong to the tree, so a workspace run never uses them; the
workspace keeps its own files next to its list:

| File in the workspace | Content | Start from |
|---|---|---|
| `ci/coverage.yaml` | thresholds per module of the workspace | `template coverage` |
| `ci/misra/deviations.yaml` | justified deviations (required once the stage is on) | `template misra-deviations` |
| `ci/misra/baseline.yaml` | inventory of known findings | `python -m tools.misra baseline --workspace <dir>` |
| `ci/misra/defects.yaml`, `ci/misra/rules.yaml` | optional; without `rules.yaml` the tree's rule set applies | - |

`[coverage]` and `[static_analysis]` of `ci/pipeline.toml` switch the stages on (`static_analysis.compile_db` names the
compile database relative to the build root). Only files of the workspace are own code; the pinned tree, the build root and
`.venv` are context. A stage switched off is named as not run; a stage switched on without its file is red with the command
that writes the starting point. **The tree is only read:** build directory and report lie in the workspace
(`tools/test/test_workspace.py` proves it).

### CI files for a workspace

`python -m tools.ci template <name> [--out <workspace>]` prints the file, or writes it where it belongs (never
over an existing one without `--force`):

| Name | Place in the workspace | Content |
|---|---|---|
| `github` | `.github/workflows/ci.yml` | checkout with the submodule and `fetch-depth: 0` (the release tag has to come along), Python from `<tree>/tools/ci/.python-version`, `tools/setup venv .`, `tools.ci setup`, `tools.ci run --strict --workspace`, job summary, artefacts |
| `gitlab` | `.gitlab-ci.yml` | `GIT_SUBMODULE_STRATEGY: recursive`, tool cache, the same four commands |
| `tests` | `ci/tests.yaml` | commented starting point of the customer's own test list (format of `tools/test/tests.yaml`) |
| `pipeline` | `ci/pipeline.toml` | commented starting point of the customer's own pipeline (format of this folder's `pipeline.toml`), coverage and static analysis of his own code switched on |
| `coverage` | `ci/coverage.yaml` | coverage thresholds for his own code (format of `tools/test/coverage.yaml`, TOOL-217) |
| `misra-deviations` | `ci/misra/deviations.yaml` | empty, commented deviation list of the static analysis of his own code (format of `tools/misra`, TOOL-217) |

Both provider files fetch a **private** neXtBSW repository with a token in the secret `NEXTBSW_TOKEN` (for a public tree the
line is deleted); the templates stay thin hulls around the same two commands anyone runs locally.

## Release publication (TOOL-69, gap N15)

A release is more than the tag: `python -m tools.ci release` gathers what `doc/architecture/customer-project.md`
gap N15 asks for into one local run - it never pushes and never tags the real tree by itself (`tools/ci/release.py`
has the reasons, `tools/ci/test_release.py` proves it with temporary repositories):

```sh
python -m tools.ci release                                              # VERSION, profile open, build/release/<tag>
python -m tools.ci release --version 0.2.0 --profile open,licensed --archive --report-dir build/ci-out
python -m tools.ci release --tag v1.0.0-rc1 --report-dir build/ci-out   # a release candidate (TOOL-232)
```

**Release candidates (TOOL-232).** `VERSION` stays `X.Y.Z`; a candidate of it is tagged `vX.Y.Z-rcN` (N = 1, 2, ...
without leading zero; Release R1 is `v1.0.0-rc1`). The tag of a `release` run is `--tag`, else the release tag of
`VERSION` that HEAD already carries (the release itself before a candidate, the newest candidate otherwise), else
`v<VERSION>`. Tag plan, archive folder, `ci-out-<tag>.zip`, `RELEASE_NOTES-<tag>.md` and the default `--out` are
named after it, and the `gh release create` line marks a candidate `--prerelease`. A tag of another form
(`v1.0.0-rc`, `v1.0.0-beta1`) or of another version (`v1.0.1-rc1` on `VERSION` 1.0.0) is refused before anything is
written. `check --release` / `run --release` judge a candidate on HEAD with the same rule as `v<VERSION>` (annotated,
reachable from `master`, tree clean); the rule (`tools/ci/rctag.py`) lives in `tools/ci` until it moves to
`tools/nxconfig/version.py` after R1, so `nxconfig pin --release` of a workspace does not know candidates yet.

| Piece | What it does |
|---|---|
| **Tag (SYS-68)** | `plan_tag` checks the annotated tag `v<version>` is ready (clean, not already there, `VERSION` matches, on `master`) and prints the exact `git tag -a` command - it never runs it. `create_tag` runs it for real; run it only against a temporary test repository, never the tree you release, that command is the user's own deliberate step. |
| **CI report (SYS-67)** | `--report-dir` (default `build/ci-out`) is first checked by `report_binding`: verdict pass, a `--strict` run of the tree, clean at the start, HEAD not moved, its tree hash equal to `HEAD^{tree}` of the checkout to be tagged, same `VERSION`. Then it is copied under the release tag into the archive - `NX_RELEASE_ARCHIVE`, else a per-user cache directory (`default_archive_root`) - and zipped as `ci-out-<tag>.zip` with a `.sha256` file next to it; the plan prints the tag-message line `CI report: sha256 …` and the `gh release create` command for the release asset (never run). With `--strict` an unbound report stops the command and names every reason. Rules and retention: [`doc/safety/verification-plan.md`](../../doc/safety/verification-plan.md) section 7. A missing report is only noted. |
| **Release notes** | the lines `CHANGELOG.md` gained since the previous release tag, wherever in the file they landed - this changelog's `## ` headings name tasks, not versions, so there is no per-release section to cut out instead. The first release takes the whole file. Written to `RELEASE_NOTES-v<version>.md` in the output folder. |
| **Export per profile** | one `tools/distcheck export` per requested profile (default `open`); a profile blocked by findings is reported, not silently forced (`--allow-findings` is for trial runs, exactly as in `tools/distcheck` itself). |
| **Target repository** | `tools/distcheck/release-targets.yaml` names where an exported profile would go, once decided; without an entry the plan carries the profile as undecided (arch54-hosting, `customer-project.md` Q2) instead of a guessed address. |
| **Archive + SHA-256 (`--archive`, variant 3)** | zips each exported profile as `nextbsw-<version>-<profile>.zip` next to a `.sha256` file in `sha256sum` format. |

Everything lands under `--out` (default `build/release/v<version>`) as `release-plan.json`/`release-plan.md` plus
the release notes, the exports and, with `--archive`, the zips - the plan for what would be pushed where, never
the push itself.

## Another provider

Without any change to `tools/ci`: check out with submodules, Python from `tools/ci/.python-version`, then
`python -m tools.ci setup --python-packages` and `python -m tools.ci run --strict --out ci-out`, and keep `ci-out` as
artefact. Linux is pinned in `versions.toml`, but so far only proven under Windows.

## Own tests

```sh
python -m unittest tools/ci/test_ci.py
python -m unittest tools/ci/test_release.py
python -m unittest tools/ci/test_verification_plan.py   # doc/safety/verification-plan.md against the repository
```
