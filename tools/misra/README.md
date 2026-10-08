# tools/misra – static analysis (PLAT-74)

cppcheck with the MISRA C:2012 add-on and clang-tidy over neXtBSW's own code, with an inventory (baseline), a
justified deviation list and a list of real faults. Any finding that is in neither the baseline nor the deviation
list breaks the run. Rule set, scope and handling of deviations: [`doc/safety/static-analysis.md`](../../doc/safety/static-analysis.md).
The core (`analysis.py`) has no Qt and no CI provider; configuration errors are reported with their YAML path.

```sh
python -m tools.misra setup                  # pinned cppcheck + clang-tidy (pip wheels) into a venv of their own
python -m tools.misra run                    # analyse the demo build, compare, report (exit 1 on new findings)
python -m tools.misra baseline               # analyse and rewrite baseline.yaml (after fixing findings)
python -m tools.misra check                  # validate rules/deviations/defects/baseline.yaml only
python -m unittest tools/misra/test_misra.py # host test with a synthetic example (no tool needed)
```

`run` needs the compile database of a target build: by default that of the demo project (`tools/test/build_dirs.py`:
`NX_BUILD_DIR`, `<tools/test build root>/demo`, then the preset directory), otherwise `--compile-db <file>`. In CI the
stage "Statische Analyse" of `python -m tools.ci run` calls it after the target builds (`[static_analysis]` in
`tools/ci/pipeline.toml`). Reports go to `--out` (default `%TEMP%/nextbsw-misra`): `report.md`, `result.json` and the
raw output `cppcheck.xml`/`clang-tidy.txt`, which `--from-raw <dir>` evaluates again without running the tools.

**A customer workspace (TOOL-217, gap N19).** `run`, `baseline` and `check` take `--workspace <dir>`: the files
then come from `<dir>/ci/misra/` (`deviations.yaml` required, `baseline.yaml`, optional `defects.yaml` and
`rules.yaml` – without it the tree's `rules.yaml` applies), only files of the workspace are own code, and the
pinned tree (also as a submodule inside the workspace), the build root and `.venv` are read as context and never
judged. OpenBSW copies are not looked up – they are a matter of the tree. The compile database defaults to
`<dir>/build/nxtest/` plus `[static_analysis] compile_db` of `<dir>/ci/pipeline.toml`; `--compile-db` and the
explicit file options still win. `python -m tools.ci run --workspace` calls it the same way.

```sh
python -m tools.misra run --workspace <dir>        # analyse the customer's own code
python -m tools.misra baseline --workspace <dir>   # writes <dir>/ci/misra/baseline.yaml
python -m tools.misra check --workspace <dir>      # validates the workspace's files
```

## Files

| File | Content |
|---|---|
| `rules.yaml` | rule set: scope exclusions, cppcheck arguments, clang-tidy checks and options, tool notices, own short descriptions of every rule that occurs (no MISRA rule texts – copyright), and the guideline re-categorization plan (`revised_category`: stricter, or `disapplied` for advisory rules only – their findings are counted apart, neither new nor baseline) |
| `deviations.yaml` | justified deviations and proven false positives: rule, files, source-line pattern or symbol, optional limit, reason, who/when, approval |
| `defects.yaml` | findings that are real faults or safeguard gaps, with a proposed owner – listed separately, not suppressed |
| `baseline.yaml` | inventory of the known findings (generated, one line per entry, matched by file + rule + source line) |
| `requirements.txt` | the tool versions – the only place they are pinned |

## Where the tools come from

1. `--tool-dir <dir>`,
2. otherwise `NX_MISRA_TOOLS` (a directory holding `cppcheck` and `clang-tidy`),
3. otherwise the venv of `setup`: `NX_MISRA_VENV`, else `<CI tool cache>/misra-venv` (`NX_CI_CACHE` or
   `%LOCALAPPDATA%/nextbsw/ci-cache`) – `python -m tools.ci setup` installs the same venv,
4. otherwise `PATH`.

A tool that is not found is skipped with the list of places searched – never reported as clean. If no tool ran, `run`
ends with exit code 3.

## What is analysed

The units of the compile database, classified per file: own code (gating), OpenBSW copies from
`tools/openbswsync/copies.yaml` (reported separately), the submodule (not analysed), generated code and host tests
(read as context, findings dropped). A **modified** copy that matches `scope.gating_copies` in `rules.yaml` counts as
own code and is gating (SYS-62): it is own development in an ASIL B module of
[`doc/safety/coexistence.md`](../../doc/safety/coexistence.md), see
[`doc/safety/component-qualification.md`](../../doc/safety/component-qualification.md) section 3. Identical copies
and copies in QM modules stay separate. `test_misra.py` checks that the list hits exactly the modified copies of the
ASIL B modules. To take a newly gated file into the baseline without taking in unrelated new findings:
`python -m tools.misra baseline --only <file>` (repeatable) rewrites the entries of the matching files only. Include paths and defines come from the compile database; the target compiler's
predefined macros (`-dM`) are given to cppcheck, its system include directories to clang-tidy. cppcheck 2.17 splits
include paths of a compile database at blanks, so paths below the repository are passed relative to the unit.
