# tools/trace — traceability of the safety requirements

Checks and documents the chain **assumed safety goal / assumption of use → requirement → mechanism → code → test**
for the neXtBSW safety requirements (SYS-22). Core without Qt, only PyYAML; every message carries the file and the
YAML path (or file and line of a code tag).

```sh
.venv/Scripts/python tools/trace check            # messages; exit 1 on an error (--strict: also on a warning)
.venv/Scripts/python tools/trace check --info     # plus the suggested code tags
.venv/Scripts/python tools/trace matrix           # regenerate doc/safety/traceability.md
.venv/Scripts/python tools/trace tags             # suggested tags: file, symbol, comment to insert
.venv/Scripts/python tools/test -k safety-trace   # the CI entry (check against the repository)
.venv/Scripts/python tools/test -k trace-tool     # host test of the tool (synthetic repository, faults per rule)
```

## Where each link lives

| Link | Source of truth |
|---|---|
| goal / AoU → requirement | relation `Parent`, role `DerivedFrom` of the SSR node in [`doc/requirements/safety/ssr.sdoc`](../../doc/requirements/safety/ssr.sdoc) (DOC-21 step 3; until then `derived_from` in `doc/safety/requirements.yaml`); the goals, assumptions and mechanisms are the nodes of [`goals.sdoc`](../../doc/requirements/safety/goals.sdoc), [`assumptions.sdoc`](../../doc/requirements/safety/assumptions.sdoc) (with the proposed AoU-24/25) and [`mechanisms.sdoc`](../../doc/requirements/safety/mechanisms.sdoc); the findings F, G stay in [`concept.md`](../../doc/safety/concept.md), F9 …, T, N in [`fmea.md`](../../doc/safety/fmea.md) |
| requirement → mechanism, finding | relation `Parent`, role `Specifies` (SM), tag field `FINDINGS` |
| requirement → code | field `CODE` (one `file :: symbol` per line; file must exist, symbol must occur as a whole word), and `nx-req: SSR-nnn` comment tags in the code (`// nx-req: SSR-001`, `# nx-req: ...` in Python/CMake, `{# nx-req: ... #}` in Jinja); the `@relation` markers replace both in step 4 |
| requirement → test | field `requirements: [SSR-nnn, ...]` of a test entry in [`tools/test/tests.yaml`](../test/tests.yaml) |
| clause → test (DOC-43) | tag field `TESTS` of the `CLAUSE` node `SSR-nnn(a)`: the entries of tests.yaml that name the requirement and verify this clause (a refinement of the link above, checked against it) |
| AoU → safety manual | the AoU ID appears in `doc/safety/safety-manual.md` (DOC-19) |

## Rules

| Severity | Rule |
|---|---|
| error | dead reference (unknown ID, missing file, symbol not found, unknown requirement in tests.yaml or in a tag, evidence test missing from tests.yaml) |
| error | schema (required keys, unknown keys, duplicate ID, `SSR-nnn` format, status, stage, verification methods, implemented requirement without code link) |
| error | a stage-1 requirement names a device-dependent (stage-2) mechanism — strict separation, concept section 1.4 |
| error | a stage-1 mechanism or an assumed goal without any requirement |
| error | an implemented requirement whose `verification` says `test` but that no test entry names |
| error | a test cited as evidence in fmea.md that names no requirement |
| error | an AoU missing from the safety manual once the manual exists |
| error | `doc/safety/traceability.md` not up to date |
| error | clauses (DOC-43): clause UID not `SSR-nnn(x)` of its requirement or out of letter order, not exactly one parent SSR, a requirement with clauses and its own `STATEMENT`, a clause method that its requirement does not name, a method of the requirement that no clause in force names, a clause test entry that is unknown or does not name the requirement, a withdrawn clause with tests; `REVIEW_STATUS` not one of proposed, rework, inspected, accepted |
| error | RST of the field texts that StrictDoc's HTML export would refuse (`check` on the command line, `rst=True`; the interpreter of the StrictDoc venv runs the same docutils components as StrictDoc's HTML writer over all ~1 300 field texts, about 11 s on the loaded workstation instead of the minutes of `requirements-html`) |
| warning | requirement without test (review / board only), mechanism without test, open requirement, goal with open requirements only, AoU without manual while the manual does not exist |
| warning | clause whose verification names `test` without a test entry (`clause-without-test`, finding R7 of INS-SSR-01), test entry of a requirement that no clause claims (`test-not-allocated`) |
| info | code link without an `nx-req` tag in that file (suggested tag) |

The matrix depends only on the sources, never on the time or on a test run: a test named in the matrix is a test
*entry* that claims the requirement; whether it passes is decided by the test run.

## StrictDoc model (DOC-21)

The requirements are in StrictDoc ([`doc/requirements`](../../doc/requirements), plan in
`migration-plan.md` (not published, available on request)). **Since step 3 (2026-10-01) the StrictDoc project
`doc/requirements` is the default and only source** of the SSR and the safety catalogue; `doc/safety/requirements.yaml`
was removed (user decision Q2). `--requirements FILE` still reads a file in the old format (the synthetic repository of
`trace-tool` and the equivalence check of the migration use it). [`migrate_sdoc.py`](migrate_sdoc.py) is the record
of the one-off conversion (`--verify --source-rev <commit>`: same records, catalogue and messages as before).

```sh
.venv/Scripts/python tools/trace check                                         # exports JSON with StrictDoc, then all rules
.venv/Scripts/python tools/trace check --sdoc DIR --sdoc-json index.json       # on an existing export
.venv/Scripts/python tools/trace export --out %TEMP%/sd                        # JSON export only
.venv/Scripts/python tools/trace html --out %TEMP%/sd                          # HTML with the scan of the @relation markers (slow)
.venv/Scripts/python tools/test -k trace-sdoc                                  # host test with the real StrictDoc
```

- **StrictDoc is a separate program**, not imported: [`sdocmodel.py`](sdocmodel.py) runs `strictdoc export
  --formats json` and reads the documented JSON export. StrictDoc lives in its own venv
  (`python -m tools.ci setup --tool strictdoc`, pins in `tools/requirements-docs.txt` and
  `tools/ci/constraints-docs.txt`); it is found through `NX_STRICTDOC_VENV`, the tool cache, next to the interpreter
  or on `PATH`. A missing StrictDoc is an error, never a silent pass.
- **Safety catalogue as nodes (user decision Q5):** ASG, AoU and SM are StrictDoc nodes (`ASG`, `AOU`, `SM` in
  [`nextbsw.sgra`](../../doc/requirements/nextbsw.sgra)). An SSR names them with relations: `Parent` with the role
  `DerivedFrom` (ASG, AoU) and `Specifies` (SM); a plain `Parent` names the BSR it constrains. Findings (F, G, T, N)
  stay a tag field checked against concept.md / fmea.md, and the evidence tests stay in fmea.md.
- **Extra rules on the model:** every `UID:` line of the `.sdoc` files is counted with a regular expression of its own
  and compared with the export (`uid-lost` in both directions, duplicate UIDs across documents), the UID format per
  node type (`STK-nnn`, `BSR-<AREA>-nnn`, `MOD-<MODULE>-nnn`, `SSR-nnn`, `ASG-nn`, `AoU-nn`, `SM-nn`), and the node
  type at the end of every parent relation. Withdrawn nodes (status `withdrawn`) are not checked any more; a
  reference to one is an error.
- **Rules of step 3:** an SM node with `CREDITED: no` (a stage-1 mechanism described but not credited, SM-58,
  SM-59) needs no requirement and may not be credited by one; a stage-1 SM node needs its `FAULT_MODEL`; every
  `#### SM-xx` section of fmea.md names an SM node.
- **Step 3b (DOC-43):** every lettered clause of an SSR is a `CLAUSE` node `SSR-nnn(a)` with `STATEMENT`,
  `VERIFICATION`, `TESTS`, optional `STATUS` (`withdrawn` keeps the UID) and `REVIEW_STATUS`; its requirement has no
  `STATEMENT` and gets its text back from the clauses in force (`(a) … (b) …`, so the records and the doc/safety
  tests read the same text as before). `REVIEW_STATUS` is a required field of every SSR. The matrix shows the review
  status per requirement and a table of the clauses. [`migrate_clauses.py`](migrate_clauses.py) is the record of
  the conversion (`--verify --source-rev <commit>`).
- **Documents:** the consistency tests of doc/safety read the SSR and the catalogue through
  `tracecheck.safety_model()` (StrictDoc export, cached in the temp folder per content, `sdocmodel.cached_export`);
  the CI check `safety-trace` exports afresh.
- **Tests:** `trace-tool` runs every rule on the checked-in export [`fixtures/sdoc/index.json`](fixtures/sdoc) without
  StrictDoc; `trace-sdoc` (needs StrictDoc) proves that the export of the fixture `.sdoc` files still equals that
  JSON, runs the faults StrictDoc refuses and the ones only tools/trace refuses through the real export.
  After a deliberate change of the grammar or the fixtures: `python tools/trace/test_trace_sdoc.py --regenerate`.
