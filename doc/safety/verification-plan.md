# neXtBSW verification plan and archived reports per baseline (SYS-67)

| | |
|---|---|
| Title | Verification plan and archived reports per baseline |
| Issue | **2**, 2026-10-04 (issue 1: 2026-10-01, SYS-67, written against `integ/night11` at `baa1d837`) |
| Author | SYS-67 (issue 1), SYS-166 (update 2026-10-02), DOC-54 (issue 2) — author side |
| Approver | the safety manager (the user): issue 1 judged in the safety review of 2026-10-02 (VP-1 … VP-11, decisions in section 11) and in the confirmation review CM-04 (record `reviews/records/CR-04-1.md` (not published, available on request), the user under D-01, 2026-10-04, `accepted with conditions`); issue 2 **released** on 2026-10-04 in the re-check of the findings F-01 … F-09 in CR-04-1 (steps 6 and 7 at `dev` efc474ea: all closed, verdict `accepted`, CM-04 closed; D-01, without independence) |
| Status | issue 2 **accepted** (released 2026-10-04 in the re-check of CM-04, record CR-04-1); update DOC-59 after it, as the reviewer decided there: VP-10 open (Z7), T-VP8 = TOOL-228 (T8), finding VF-1 (RN, section 10.1) |
| Change history | section 15 |

Working state: dev at 6812a0b4 (after v0.2.0)
Readers: the user as safety manager and release authority, the coordinator who merges, runs the release CI and tags,
the agents who write tests, and the reviewer of confirmation measure CM-04 (integration and test strategy).
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**Terms.** "**Release R1**" is the first release the user wants to be releasable, tagged `v1.0.0-rc1` — a development
release without a safety claim (safety plan section 5, deviation for release R1); this plan writes it as
"Release R1 (`v1.0.0-rc1`)". The release preconditions of safety plan section 6 are always written "precondition
R1" … "precondition R6"; they are a different thing (CM-04 finding F-05).

**What this document is.** The verification plan that `iso26262-gap.md` (not published, available on request) found missing (rows 8-20
to 8-22, 6-22 to 6-36; section 6 task 9) and that the safety plan names as work product of P6-14, P6-16, P6-17, P6-20
and P8-06. It says, per verification level, which methods neXtBSW uses and how they are rated at ASIL B, what passes
and what fails, in which environment and with which tool versions, how test cases are derived, what runs on every
merge and what per baseline, and how the report of a baseline is bound to its tag and archived. It closes the gaps
G1 and G2 of [`cm-plan.md`](cm-plan.md) section 10 together with two small tool changes in `tools/ci` (section 7).

**What it is not.** Not the configuration management plan (`cm-plan.md` owns baselines, tags and the release steps;
this plan defines what "green" means for them), not the description of the tools (`dev-environment.md` section 7
lists the test flow and the pinned tools; this plan does not repeat it), not a test specification per case (the
cases live in the test code, section 5).

**Licence rule.** ISO 26262 is cited by **part and clause only** (for example "6-9.4.3" = ISO 26262-6:2018, clause
9.4.3). Methods are named in our own words; the rating at ASIL B is given as **HR** (highly recommended) or **R**
(recommended) for the methods this project uses, never as a copy of a table. The ratings are our reading and are a
review point (VP-10): **open**, checked by the user against the licensed standard before the first safety release
(CM-04 re-check Z7; not needed for Release R1 without a safety claim).

**Consistency test.** [`tools/ci/test_verification_plan.py`](../../tools/ci/test_verification_plan.py) checks this
document against the repository: every group of `tools/test/tests.yaml` has a level in section 2, every pinned
version of section 4 equals its pin file, every switch of section 3.2 equals `pipeline.toml`/`coverage.yaml`, every
`derivation` value in `tests.yaml` is one of section 5, every provenance field of section 7.1 is written by
`tools/ci`, the counts of section 3.6 equal `tests.yaml` and `traceability.md`, and the list of section 3.5 is exactly
the safety entries without `fault-injection` (both since issue 2). Each rule has a counter-check on a broken copy.
Numbers in the running text are taken from the table of section 3.6 at the working state.

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Levels | **unit** (host tests of C/C++ units, generator and tool tests, static analysis, coverage, mutation runs), **integration** (target builds of every profile, image checks of the linked ELF, SIL of the component, integration host tests), **embedded software** (board runs at the workstation, `tools/boardtest`), plus **document consistency** and **reviews** across levels (section 2) |
| Pass/fail | one rule per level, all automated levels in **one** CI verdict (`tools/ci run --strict`): every entry green, no unexpected skip, every visible profile decided, no new static-analysis finding; coverage is reported, **not yet a gate** — the gate on the safety modules comes before the first safety release (section 3.4, decision VP-1 a) |
| Environment and tools | every version pinned once (`versions.toml`, `.python-version`, `constraints*.txt`, `tools/misra/requirements.txt`); the host tests use another compiler than the image - the difference is analysed in section 4.2 with the bridging target checks |
| Test-case derivation | seven methods (section 5); recorded per safety test entry in the field `derivation` of `tests.yaml`; **filled for every safety entry** (123 at the working state, section 3.6; T-VP1, SYS-166), and the consistency test requires it on every one; the safety entries without a `fault-injection` value are listed with their counter-check in section 3.5 |
| Regression | four rings: the agent's own entries, `tools/test --since` at every merge, the full list on the integration branch, the release CI per baseline (section 6) |
| Finding faults before the release CI | the v0.2.0 release needed three CI runs of about 5 h each; a **pre-flight** of about one hour on the frozen candidate runs what only the release CI ran so far — `python -m tools.ci preflight` (TOOL-209, section 8) |
| Report per baseline (G1) | `ci-result.json` now records **commit, tree hash, describe, OpenBSW gitlink, VERSION, dirty** at the start and the state at the end; a HEAD moved during the run is red with `--strict` (section 7.1) |
| Archive per baseline (G2) | `tools.ci release` checks that the report is **bound** to the tree that is tagged (same tree hash, clean, strict, pass), copies it to the archive, zips it as `ci-out-vX.Y.Z.zip` with SHA-256 for the tag message and prints the command for the GitHub release asset; with `--strict` an unbound report stops it (section 7.2) |
| Per baseline | a matrix of what each release kind needs (section 9); a **safety release** needs all rows, a **development release** the automated rows |
| Release R1 (`v1.0.0-rc1`) | a development release **plus** (section 9.1, CM-04 Z3/Z4/Z8): the release CI as for a safety release (`--strict --clean --slow`, report bound with `tools.ci release --strict`), the pre-flight `tools.ci preflight` before it (first real run, TOOL-209), the board list of SYS-167 before the tag, and a suite run of the demo cases plus the restart-time measurement F4 on the tagged image |
| Decided | the safety review of 2026-10-02 decided VP-1 … VP-11 except VP-8 and VP-10; CM-04 (2026-10-04) decided VP-8; VP-10 is **open** - check by the user before the first safety release (the re-check of CM-04 found no record of the check, Z7) — every decision is in section 11 |
| Limits of Release R1 | no coverage gate (VP-1), architecture-level coverage argued, not measured (VP-2), HT-2/HT-3 without bridge (VP-3, SYS-168), no independence of test authoring (VP-9 b, SYS-181), no tool archives, no second-machine reproduction, no asset workflow (TOOL-210, TOOL-211, TOOL-212), the safety entries without a counter-check not sighted (TOOL-228), the HR/R ratings not checked against the standard (VP-10) — all after Release R1 and before the first safety release (sections 9.1, 11) |
| Verification of this issue | **mock**: the consistency test of this plan (`verification-plan`, `tools/ci/test_verification_plan.py`, including the counts and the counter-check list since issue 2), the host tests of `tools/ci`, the document tests of `doc/safety/test`; no hardware |

---

## 1. Scope and interfaces

The plan covers the verification of the neXtBSW element (SEooC, claim of the safety plan section 1.2) in this
repository: own code, generated code, the modified OpenBSW copies, the configuration data of the project's own
profiles, and the own tools whose output reaches the image (tool confidence in `tool-classification.md`). The
verification of a customer's configuration and integration is the integrator's; `tools/ci run --workspace` gives him
the same pipeline. The assumptions of use that hand it over (safety manual sections 1.3 and 7, `assumptions.sdoc`):

| What the integrator verifies | Assumption of use |
|---|---|
| his configuration data under change control, re-verified after every change | AoU-10, AoU-CFG-1 |
| the configuration itself on his hardware (the run-time checks detect a change, not a wrong value) | AoU-DFA-c, AoU-CFG-2 |
| the board checks of `fmea.md` section 8 that depend on a value he changed, his schedule and restart time | AoU-CFG-2, AoU-CFG-3, AoU-08 |
| his application and its generated model code | AoU-29 |
| the parts of the assumed goals the element does not cover | AoU-23 |
| the integration test of his ECU and the test in the vehicle (section 2.3, tailored) | the safety manual section 1.3 row "verification of the integration and of the item", with AoU-10 and AoU-CFG-2 |

| Interface | Document | What it takes from here | What this plan takes from it |
|---|---|---|---|
| Configuration management | `cm-plan.md` | what a green report is, the report files, the binding rule (section 7) | the baseline, the release steps 1-9, the preconditions P1-P9 |
| Safety plan | `safety-plan.md` | the work product of P6-14, P6-16, P6-17, P6-20, P8-06; evidence for precondition R2 (section 6 of the safety plan) | the activities and their status |
| Development environment | `dev-environment.md` | the host/target difference analysis it leaves open (its 7.1) | the list of pinned tools and the test flow |
| Traceability | `traceability.md` (generated) | the rule that every SSR has a verification (section 3.3) | the requirement coverage per SSR |
| Reviews | `reviews/` | the review packages of CM-04 and the inspections | the record template |
| Anomalies | `anomaly-register.md` | failures of verification that are not fixed in the same task become anomalies | the register |
| Safety case | `safety-case.md` | EW-26 (this plan), ER-02 (archived report) | the deficits D-12, D-21 |

---

## 2. Levels and methods

The levels follow part 6: unit verification (6-9), software integration and its verification (6-10), testing of the
embedded software (6-11). Every automated verification is an entry of `tools/test/tests.yaml`; its group says how
it runs, the table says which level a group serves. One group can serve two levels, because a group is a way of
running (Python, C/C++ via zig, target build), not a level.

<!-- vp-levels:start -->
| Level | tests.yaml groups | What is verified | Environment |
|---|---|---|---|
| Unit | `host` | C/C++ units of the element (safety mechanisms, drivers, BSW services) with the real unit code against register models and stubs, own test profiles | host, `zig c++`/`zig cc` |
| Unit | `python` | own tools and generators (nxconfig, bswgen, rtegen, dbcgen, …), Python wrappers that build C/C++ host tests, mutation runs, document consistency tests | host, pinned Python |
| Unit | `pytest` | neXtBSW Studio (tool, not part of the image) | host, offscreen Qt |
| Integration | `target` | every visible build profile built warning-free with the pinned ARM GCC; checks of the linked image (sections, safe outputs table, disassembly of the fault paths, timing tables, stack use, ROM check list) | host with ARM GCC, no board |
| Integration | `build` | host builds of the bootloader core as DLL and of the STM32 build test | host |
| Integration | `matlab` | model tests of the component generator (`tools/swc`), outside the CI | workstation with MATLAB |
| Embedded software | — (no entry in the list) | board runs with `tools/boardtest`, board checklists of `fmea.md` section 8 and of the task records, captures in `tools/boardtest/captures` | eval board, J-Link, VN1610, lab devices; only the user |
<!-- vp-levels:end -->

### 2.1 Unit verification (6-9.4.2)

| Method (own words) | Rating at B | How neXtBSW does it | Status |
|---|---|---|---|
| Walk-through of the code | R | the coordinator reads every agent diff before the merge; no record | done, no record |
| Inspection of the code with a record | HR | SYS-69 inspections with the template of `reviews/record-template.md` | planned (safety plan P6-14) |
| Static code analysis | HR | `tools/misra` (cppcheck with MISRA add-on, clang-tidy, NXCPP scan rules), CI stage, gate (`enforce = true`) | done |
| Control-flow and data-flow analysis | R | clang-tidy checks of the rule set, compiler warnings with `-Werror`; no separate tool | partial |
| Semi-formal verification | R | state machines of the ECU state and the supervisor tested against their tables; no model checker | partial |
| Requirement-based test | HR | `host`/`python` entries with `requirements:` (the safety entries, count in section 3.6) | done for every SSR with a test entry (section 3.3) |
| Interface test | HR | host tests drive the units through their real interfaces (RTE ports, register models with write-1-to-clear, lock bits) | done |
| Fault injection test | R | the `*-mutations` entries insert faults into the unit under test and must be found (count in section 3.6, `--slow`); register models inject hardware faults | done |
| Resource usage evaluation | R | `tools/stackuse` (worst-case stack per task, gate in `stackuse-demo`), image size per section, `elfcheck` | partial (execution time: N10 of the safety plan) |
| Back-to-back test between model and code | R | `e2e-host` (C library against the Python reference model), `demoswc-sil` (model code in SIL) | done where models exist |

### 2.2 Software integration verification (6-10.4.2)

The integration of neXtBSW is fixed by the generators: `bswgen` and `rtegen` produce the board and RTE glue from the
configuration levels, the lifecycle levels of `Integration.cpp` start the services in a fixed order. The integration
steps and what each one verifies:

| Step | What is integrated | Verified by |
|---|---|---|
| I1 Configuration | derivative + board + project + RTE into one checked model | `nxconfig*` entries, messages with YAML path |
| I2 Generation | generated glue against independent expectations | `bswgen-reference`, `rtegen-reference`, `bswgen-safe-io`, `safe-outputs-table*` |
| I3 Units with their neighbours on the host | real units together (supervisor + safety manager + checks) | `safety-cycle`, `ecustate`, `safe-supervisor*`, `rte-deadline`, `demoswc-sil` |
| I4 Build of every profile | the whole image per profile, warning-free | `target-*` entries, profile coverage check of `tools/ci` |
| I5 Linked image | placement, tables, priorities, stack, fault paths in the ELF | `elfcheck*`, `safety-context-elf`, `safe-outputs-order`, `stackuse-demo`, `freertos-config-elf`, `hold-console-elf` |
| I6 On the target | the image on the board | section 2.3 |

| Method (own words) | Rating at B | How | Status |
|---|---|---|---|
| Requirement-based test | HR | I3 and I5 entries with `requirements:` | done |
| Interface test | HR | generated interfaces checked entry by entry (I2), RTE ports in I3 | done |
| Fault injection test | R | mutation runs of I3 units, generator mutants (`bswgen-safe-io` 20 mutants), board fault commands (`safety …`, test builds) | done on host, partial on target |
| Resource usage evaluation | R | stack (I5), image sizes in every CI report, timing tables (`elfcheck-timing*`) | partial (no measured WCET, N10) |
| Back-to-back test | R | `demoswc-sil` | partial |
| Verification of control and data flow | R | `safety-context` (static rule: who may call the supervisor), `elfcheck-timing` (task order, priorities) | partial |
| Static code analysis | HR | as 2.1, over the compile database of the demo build | done |

### 2.3 Testing of the embedded software (6-11)

| Method (own words) | Rating at B | How | Status |
|---|---|---|---|
| Test in a hardware-in-the-loop setup | HR | eval board with lab supplies, electronic load, function generator and oscilloscope through LabControl; no closed-loop plant model | partial |
| Test in an ECU network environment | HR | Vector VN1610 with rest-of-bus traffic, UDS and flashing over CAN (`tools/canflash`, `tools/boardtest`) | partial |
| Test in the vehicle | HR | not applicable to an SEooC; the integrator's (section 1: safety manual 1.3, AoU-10, AoU-CFG-2) | tailored |
| Requirement-based test on the target | HR | the board test specification per SSR ([`board-test-spec.md`](board-test-spec.md), SYS-75) run with `tools/boardtest suite`, record `suite-run.json` per run; before it board checklists per task, captures with image CRC and commit | partial (organised per SSR since SYS-75; run per task image so far) |
| Fault injection on the target | R | console fault commands (`safety …`, only in test images), J-Link register writes (fmea.md B5-B16), supply faults | partial |

The board level is the weakest: it is run by the user only and so far per task image, not per baseline. Since SYS-75
the cases are specified per SSR and repeatable (`tools/boardtest suite`); the board state for Release R1 was taken from
2026-10-02 to 2026-10-04 at integration states (captures `tools/boardtest/captures/evalboard_s32k144_demo/2026-10-02_board`, `2026-10-03_board` and the suite runs `2026-10-02_suite-integ12*`),
not at a tag. Section 9 (VP-6) defines what a safety release needs, section 9.1 what Release R1 needs on its tagged
image.

### 2.4 Reviews and document checks

Inspections and confirmation reviews are planned by the safety plan (sections 4 and 5) and recorded in
`doc/safety/reviews`. Every safety document that makes a checkable statement has a consistency test in
`doc/safety/test` or next to its tool; these tests are part of the unit level of the CI and fail when a document and
the repository disagree.

---

## 3. Pass/fail criteria

### 3.1 Per level

| Level | Pass | Fail |
|---|---|---|
| Unit, entry | the runner's verdict `pass` (exit 0 and the expected summary line, `tools/test`) | `fail`, `timeout`, or a test file no entry covers ("NICHT IN tools/test/tests.yaml") |
| Unit, static analysis | no finding outside baseline and deviation list (`tools/misra`) | a new finding; with `--strict` a missing tool |
| Unit, coverage | reported per module against `coverage.yaml` | today never red (VP-1) |
| Unit, mutation run | every inserted fault is found | a surviving fault |
| Integration, build | configure and build with `-Werror` | any warning or error |
| Integration, image check | the check's own expectation, independent of the generator where stated | any deviation |
| Integration, profiles | every visible preset is built or listed with a reason under `presets.not_built` | a profile without a decision |
| Embedded software | the expected console lines and measured values of the checklist, recorded as capture with image CRC | any deviation; a deviation is an anomaly until explained |
| Whole run (`tools/ci run --strict`) | verdict `pass`: every stage ok, no `fail` finding, every skip listed in `allowed_skips` | anything else |

A run that is not `--strict` is a trial; it never counts as evidence of a baseline (section 7.2).

### 3.2 Switches that decide what is a gate

The values below are the state of the repository; the consistency test compares them with the files.

<!-- vp-switches:start -->
| Setting | File | Value | Meaning |
|---|---|---|---|
| `run.clean` | `tools/ci/pipeline.toml` | `true` | target builds from an empty build root |
| `coverage.enabled` | `tools/ci/pipeline.toml` | `true` | coverage is measured in every full run |
| `coverage.enforce` | `tools/ci/pipeline.toml` | `false` | below threshold is a warning, not red (VP-1) |
| `coverage.strict` | `tools/ci/pipeline.toml` | `false` | a failed measurement is a warning |
| `static_analysis.enabled` | `tools/ci/pipeline.toml` | `true` | the static analysis runs in every full run |
| `static_analysis.enforce` | `tools/ci/pipeline.toml` | `true` | a new finding is red |
| `static_analysis.strict_tools` | `tools/ci/pipeline.toml` | `true` | with `--strict` a missing analysis tool is red |
| `enforce` | `tools/test/coverage.yaml` | `false` | the thresholds are proposals |
<!-- vp-switches:end -->

### 3.3 Requirement coverage

Every SSR names its verification methods (`VERIFICATION` in `doc/requirements/safety/ssr.sdoc`), and every test
entry that verifies one names it in `requirements:`. `tools/trace` (entry `safety-trace`) fails on a dead reference
and regenerates `traceability.md`; the counts at the working state are in section 3.6 (59 SSR, 57 with at least one
test entry, 2 open). A test entry is evidence of a defined test, not of a passed one: the passed state comes from the
archived report of the baseline (section 7).

- **Board verification per SSR.** Every SSR whose `VERIFICATION` names "board" is covered per clause by the cases of
  [`board-test-spec.md`](board-test-spec.md) (SYS-75; section 5 there: 91 board items by 31 cases, of which 3 cases
  are for projects with features the demo does not configure, and 7 items without a board case, each with its
  reason). The board level of a baseline is the run of those cases on its image (section 9, VP-6; for Release R1
  section 9.1).
- **Open SSR.** An SSR with status `open` is not implemented; it has no test entry and carries no claim. At the
  working state these are SSR-061 (plausibility between two measurement paths, gap G2) and SSR-063 (safe state of
  bus-commanded actuators, gap G4). They are a **limit of Release R1**: the safety manual hands the missing coverage to
  the integrator (AoU-23); their verification is planned with the stage-2 work that implements them.

### 3.4 Structural coverage

| Level | Measure | Target | State |
|---|---|---|---|
| Unit | statement and branch coverage of the C/C++ host tests (`tools/test/coverage.py`, gcov counting) | safety modules 95 % statements / 70 % branches, others 80 / 50 (`coverage.yaml`) | measured in every full run, **not enforced**; the safety modules miss the branch proposal in part (gap row 6-24) |
| Unit | MC/DC | not targeted at B (recommended only); the way to it is noted in `doc/changelog.d/TOOL-40.md` | — |
| Architecture | function and call coverage | not measured (recommended at B, gap row 6-30) | argued in VP-2 |

**Decision VP-1 a (safety review 2026-10-02):** the gate is switched on for the safety modules **before the first
safety release**; the other modules stay reported. Until then `coverage.enforce` and `enforce` stay `false`
(section 3.2) and the gate is a **limit of Release R1** (section 9.1). Today `coverage.yaml` records per module the
measured value and its cause (`reason`); several safety modules are below the branch proposal of 70 %. That is a
measurement, not yet an argument: the argument per module — which branches are not reached, and why their behaviour
is shown otherwise (by a board case, an image check, or because the branch is defensive and cannot be reached) — is
part of switching the gate on and is required for the first safety release, not for Release R1, which makes no
safety claim (CM-04 finding F-08).

### 3.5 Effectiveness of the tests

A test of a safety mechanism has a counter-check: a mutation run (`*-mutations`), a built-in broken copy in the test
itself ("Gegenprobe"), or a reference against an independent expectation. A safety test entry without one of these is
a weakness. The `derivation` value `fault-injection` marks the entries that insert faults (mutation runs, register
model faults, broken copies); the safety entries **without** it are the candidates and are listed below (CM-04
finding F-09; the consistency test keeps the list equal to `tests.yaml`). The column "Marker" says whether a text
search of the entry's test files finds a counter-check marker ("Gegenprobe", "counter-check", "broken copy"); it is a
search, not a judgement. An entry with "none found" may still compare against an independent expectation (for
example a reference model or a hand-written table) — sighting each one and adding or arguing its counter-check is
T-VP8 (section 10, task TOOL-228), required before the first safety release.

<!-- vp-no-counter-check:start -->
| Entry | Marker |
|---|---|
| `nxconfig` | found |
| `nxconfig-identity` | none found |
| `nxconfig-degraded` | none found |
| `nxconfig-safestate` | none found |
| `nxconfig-watchdog` | found |
| `bswgen` | found |
| `bswgen-mpu` | none found |
| `rtegen` | found |
| `rtegen-safety` | none found |
| `ecustate` | none found |
| `application-linker` | found |
| `bl-s3-host` | none found |
| `safety-reset-console` | none found |
| `cycle-period-check` | none found |
| `cycle-period-constants` | found |
| `data-flash-system` | none found |
| `stackuse-demo` | none found |
| `bootloader-host` | none found |
| `canflash-tool` | found |
| `boardtest` | found |
| `e2e-ref` | none found |
| `e2e-host` | none found |
| `debug-halt-guard` | none found |
| `safe-off-equivalence` | none found |
| `safe-off-output-tester` | none found |
| `safe-off-hold-command` | none found |
| `can-bus-state` | none found |
| `can-phy-gate` | none found |
| `can-console` | none found |
| `hard-fault-dump` | none found |
| `bsw-providers-sbc` | none found |
| `bsw-providers-nosbc` | none found |
| `bsw-providers-nosbc-nomcu` | none found |
| `rcm-start-line` | none found |
| `safe-outputs-order` | none found |
| `safe-outputs-table-charge` | none found |
| `safe-outputs-table-bt148` | none found |
| `safety-context-elf` | none found |
| `e2e` | none found |
| `adc-access` | none found |
| `safe-state` | none found |
| `safe-state-degraded` | none found |
| `safe-supervisor` | found |
| `safe-supervisor-bare` | found |
| `nvm-flash-write-bracket` | none found |
| `demoswc-sil` | none found |
| `target-demo` | none found |
| `elfcheck` | found |
| `elfcheck-bt148` | found |
| `elfcheck-timing` | none found |
| `elfcheck-timing-bt148` | none found |
<!-- vp-no-counter-check:end -->

Of these 51 entries, 12 carry a marker; 39 are to be sighted (T-VP8). Several of them have a counter-check in a
neighbouring entry of the same unit (for example `safe-outputs-table` with `fault-injection` beside
`safe-outputs-table-charge`, `e2e-mutations` beside `e2e`), which the sighting may credit.

### 3.6 Counts used in this plan

The consistency test compares this table with `tools/test/tests.yaml` and the summary of `traceability.md`; a number
in the running text is one of these at the working state.

<!-- vp-counts:start -->
| Count | Value | Source |
|---|---|---|
| entries | 416 | `tools/test/tests.yaml` |
| safety entries | 123 | entries with `requirements:` |
| mutation entries | 32 | entries named `*-mutations` |
| safety entries without fault-injection | 51 | section 3.5 |
| SSR | 59 | `traceability.md` summary |
| SSR with a test entry | 57 | `traceability.md` summary |
| SSR open | 2 | `traceability.md` summary |
<!-- vp-counts:end -->

---

## 4. Environment and tool versions

### 4.1 Pinned versions

The versions are pinned once in the files named; every report of `tools/ci` records them (stage "Werkzeuge pruefen",
`static_analysis.tool_versions`) and, since this issue, the commit that pins them (section 7.1).

<!-- vp-tools:start -->
| Tool | Version | Pinned in | Used for |
|---|---|---|---|
| ARM GCC (xPack) | 11.3.1 | `tools/ci/versions.toml` | target builds, image |
| CMake | 4.4.3 | `tools/ci/versions.toml` | target and host builds |
| Ninja | 1.13.2 | `tools/ci/versions.toml` | target and host builds |
| Python | 3.12.10 | `tools/ci/.python-version` | all tools and Python tests |
| ziglang (zig cc/c++, clang) | 0.16.0 | `tools/ci/constraints.txt` | C/C++ host tests, coverage |
| cppcheck (pip wheel) | 1.5.1 | `tools/misra/requirements.txt` | static analysis |
| clang-tidy (pip wheel) | 22.1.8 | `tools/misra/requirements.txt` | static analysis |
<!-- vp-tools:end -->

Not pinned and therefore recorded per board record: J-Link software, Vector driver, LabControl (dev-environment.md
5.3, finding DE7). The board environment is the eval board of `boards/` with its revision; a record names board, image
CRC and commit.

### 4.2 Host and target: the differences and what bridges them (6-9.4.5, 6-10.4.7)

The unit level runs on the host with `zig c++`/`zig cc` (clang) for x86-64; the image is built by ARM GCC 11.3.1 for
Cortex-M4F (S32K14x) or Cortex-M0+ (S32K11x). A host result carries over to the target only where the difference
does not matter or a target check bridges it.

| # | Difference | Consequence for a host result | Bridge on the target | Remaining |
|---|---|---|---|---|
| HT-1 | Compiler and optimiser (clang vs GCC, other flags) | undefined or implementation-defined behaviour can differ; code generation differs | static analysis over the target compile database; target build with `-Werror`; disassembly checks of the fault paths (`safe-outputs-order`, `hold-console-elf`, `freertos-config-elf`) | units without an ELF check rely on the static analysis |
| HT-2 | Pointer and `long` width (host 64-bit pointers; `long` 32-bit on Windows, 64-bit on Linux; target 32/32) | arithmetic on `long`, casts between pointers and integers, structure layout | NXCPP/MISRA rules on fixed-width types (`uint32` = `uint32_t` since ARCH-46); layout of records checked in the ELF where it matters (`safety-context-elf`, hard-fault dump layout) | no general check that host and target layouts agree (VP-3) |
| HT-3 | Signedness of plain `char` (signed on x86-64, unsigned with ARM GCC) | comparisons and shifts on `char` can differ | MISRA essential-type rules in the static analysis | host tests are not built with `-funsigned-char` (VP-3) |
| HT-4 | Floating point (target single-precision FPU, `double` in software; host hardware double) | rounding of `double` paths, timing | safety mechanisms use integer arithmetic; FPU and ABI attributes checked by `elfcheck` | — |
| HT-5 | Hardware registers | the host sees a register model, not the silicon | register models follow the reference manual and are named per test; the HSI (`hsi-s32k1xx.md`) lists every register a mechanism uses; board checks per mechanism | a model can share an error with the code (same reader of the manual): board checks B1-B11 |
| HT-6 | Interrupts and concurrency | host tests are single-threaded with simulated interrupts and a virtual clock | MPU placement and priorities checked in the ELF (`safety-context-elf`, `elfcheck-timing*`), `safe-supervisor-mpu` with the host MMU; board runs under load (B4) | races are argued, not tested exhaustively |
| HT-7 | Start-up code, linker script, memory map | not executed on the host | `application-linker`, `elfcheck*` (sections, overlap, records), `rom-check-image`, `safe-memory-ecc-latent-elf`, `stackuse-demo` | start-up timing only on the board |
| HT-8 | Timing and clock | virtual time on the host | `elfcheck-timing*` (periods and offsets as configured), `cycle-period-constants`, board B2, B4, B7 | measured WCET open (N10) |
| HT-9 | RTOS (FreeRTOS on the target, stubs on the host) | scheduling and kernel hooks only in the image | `freertos-config-elf`, `kernel-fault-reaction` with the real hooks, board runs | FreeRTOS qualification is O2 of the safety plan |

---

## 5. Test-case derivation (6-9.4.3, 6-10.4.3, 6-11.4.3)

### 5.1 Methods

<!-- vp-derivation:start -->
| Value | Method (own words) | Rating at B | Typical evidence in a test |
|---|---|---|---|
| `requirements-analysis` | cases taken from the requirement text, one or more per statement | HR | a case per sentence of the SSR or mechanism |
| `equivalence-classes` | inputs split into classes that the unit treats alike, one case per class | HR | every register value, state or event class once |
| `boundary-values` | cases at, just inside and just outside each limit | HR | limit, limit plus one tick, zero, counter overflow |
| `error-guessing` | cases from experience and earlier findings (board findings, review findings) | R | a case named after a finding (Fn, SR-…, board session) |
| `fault-injection` | faults inserted into the unit, its inputs or its registers; the test must notice | R | a mutation run, register-model faults |
| `operational-use-cases` | cases from the way the element is used in a vehicle (start, sleep/wake, flashing, load) | HR at target level | board sessions, SIL of the demo component |
| `functional-dependencies` | cases from the dependencies between functions (order, shared resources) | R at target level | order checks in the ELF, lock and bracket tests |
<!-- vp-derivation:end -->

### 5.2 Where the derivation is recorded

- **Per safety test entry** (an entry with `requirements:`) the field `derivation:` in `tools/test/tests.yaml` lists
  the methods used, values from 5.1. The field is optional for the runner (it ignores it) and checked by the
  consistency test (only known values, only on entries with `requirements:`).
- **Per case** the test code says which class, limit or finding it covers, in the name or the comment of the case;
  that is the test specification of 8-9.4.2 (ID = entry name plus case name, version = commit, expected result =
  assertion). A separate specification document is not written (VP-8, decided in CM-04, Z5).
- **At target level** the board checklist of the task names its cases with the expected result; the capture is the
  record. Since SYS-75 the cases per SSR and operational use case are specified in
  [`board-test-spec.md`](board-test-spec.md) and run with `tools/boardtest suite`; the record of a run is its
  capture folder with `suite-run.json` (image, baseline, result and values per case).

### 5.3 State

Filled for every entry with `requirements:` (6 in SYS-67, the other 104 in SYS-166, every later one by the task that added it), each read against the entry's
comment and test; a method is named only where the comment or the test code shows it (for example `fault-injection`
only where the test inserts model faults or mutants, so the image checks of `elfcheck-bt148` and
`elfcheck-timing-bt148`, which only run the clean build, carry `requirements-analysis` alone):

<!-- vp-derivation-examples:start -->
| Entry | derivation |
|---|---|
| `nxconfig` | requirements-analysis, equivalence-classes |
| `nxconfig-identity` | requirements-analysis, equivalence-classes |
| `nxconfig-degraded` | requirements-analysis, equivalence-classes, boundary-values |
| `nxconfig-safestate` | requirements-analysis, equivalence-classes |
| `nxconfig-watchdog` | requirements-analysis, boundary-values, error-guessing |
| `bswgen` | requirements-analysis, equivalence-classes |
| `bswgen-mpu` | requirements-analysis, boundary-values |
| `bswgen-reference` | requirements-analysis, equivalence-classes, fault-injection |
| `rtegen` | requirements-analysis, equivalence-classes, functional-dependencies |
| `rtegen-reference` | requirements-analysis, equivalence-classes, fault-injection |
| `rtegen-safety` | requirements-analysis, equivalence-classes |
| `dbcgen-projects` | requirements-analysis, equivalence-classes, boundary-values, fault-injection |
| `ecustate` | requirements-analysis, equivalence-classes, operational-use-cases |
| `rte-deadline` | requirements-analysis, equivalence-classes, boundary-values, fault-injection |
| `calibration-segment` | requirements-analysis, equivalence-classes, fault-injection |
| `application-linker` | requirements-analysis, boundary-values |
| `safe-watchdog` | requirements-analysis, error-guessing, fault-injection |
| `lpit-deadman-check` | requirements-analysis, fault-injection |
| `safety-cycle` | requirements-analysis, boundary-values, functional-dependencies, fault-injection |
| `cycle-timer-off` | requirements-analysis, error-guessing, fault-injection |
| `safe-memory-ecc` | requirements-analysis, equivalence-classes, fault-injection |
| `safe-memory-ecc-latent` | requirements-analysis, equivalence-classes, fault-injection |
| `safe-memory-ecc-latent-s32k11x` | requirements-analysis, equivalence-classes, fault-injection |
| `safe-memory-ecc-latent-mutations` | fault-injection |
| `safe-memory-ecc-latent-elf` | requirements-analysis, fault-injection |
| `safe-memory-mpu-regions` | requirements-analysis, fault-injection |
| `safe-memory-mpu-regions-16` | requirements-analysis, fault-injection |
| `safe-memory-mpu-regions-mutations` | fault-injection |
| `safe-io` | requirements-analysis, equivalence-classes, fault-injection |
| `bswgen-safe-io` | requirements-analysis, equivalence-classes, fault-injection |
| `bswgen-safe-readback` | requirements-analysis, fault-injection |
| `safe-io-mutations` | fault-injection |
| `safety-gate` | requirements-analysis, functional-dependencies, fault-injection |
| `safety-gate-mutations` | fault-injection |
| `safety-shell-unlock` | requirements-analysis, fault-injection |
| `short-withdraw-order` | requirements-analysis, fault-injection |
| `bl-s3-host` | requirements-analysis, equivalence-classes, boundary-values |
| `safe-console` | requirements-analysis, error-guessing, fault-injection |
| `safety-reset-console` | requirements-analysis, boundary-values, error-guessing, functional-dependencies |
| `safety-reset-console-mutations` | fault-injection |
| `kernel-fault-reaction` | requirements-analysis, error-guessing, functional-dependencies, fault-injection |
| `kernel-fault-reaction-mutations` | fault-injection |
| `no-heap-operator-delete` | requirements-analysis, equivalence-classes, error-guessing, fault-injection |
| `freertos-config-elf` | requirements-analysis, fault-injection |
| `hold-console-elf` | requirements-analysis, fault-injection |
| `lpit-halt-deadman` | requirements-analysis, fault-injection |
| `cycle-period-check` | requirements-analysis, boundary-values, equivalence-classes |
| `lptmr-cycle-timer` | requirements-analysis, functional-dependencies, fault-injection |
| `cycle-period-constants` | requirements-analysis, boundary-values |
| `data-flash-system` | requirements-analysis |
| `stackuse-demo` | requirements-analysis, boundary-values |
| `safety-context` | requirements-analysis, error-guessing, fault-injection |
| `bl-platform-host` | requirements-analysis, equivalence-classes, fault-injection |
| `bootloader-host` | requirements-analysis, operational-use-cases |
| `canflash-tool` | requirements-analysis, equivalence-classes |
| `boardtest` | requirements-analysis, operational-use-cases |
| `bl-update-host` | requirements-analysis, fault-injection |
| `bl-update-mutations` | fault-injection |
| `e2e-ref` | requirements-analysis |
| `e2e-host` | requirements-analysis, equivalence-classes |
| `e2e-mutations` | fault-injection |
| `identity` | requirements-analysis, boundary-values, fault-injection |
| `debug-halt-guard` | requirements-analysis, operational-use-cases |
| `safe-off-equivalence` | requirements-analysis, functional-dependencies |
| `safe-off-command-hold` | requirements-analysis, equivalence-classes, boundary-values, fault-injection |
| `safe-off-output-tester` | requirements-analysis, equivalence-classes, boundary-values, error-guessing |
| `safe-off-hold-command` | requirements-analysis, equivalence-classes |
| `safe-off-write-paths` | requirements-analysis, fault-injection |
| `safe-off-hold-mutations` | fault-injection |
| `short-hold` | requirements-analysis, equivalence-classes, fault-injection |
| `short-hold-mutations` | fault-injection |
| `bswgen-safe-state` | requirements-analysis, equivalence-classes, fault-injection |
| `can-bus-state` | requirements-analysis, equivalence-classes |
| `can-phy-gate` | requirements-analysis, equivalence-classes |
| `can-console` | requirements-analysis, functional-dependencies |
| `hard-fault-dump` | requirements-analysis |
| `bsw-providers-sbc` | requirements-analysis, equivalence-classes |
| `bsw-providers-nosbc` | requirements-analysis, equivalence-classes |
| `bsw-providers-nosbc-nomcu` | requirements-analysis, equivalence-classes |
| `rcm-start-line` | requirements-analysis, equivalence-classes |
| `safe-outputs-order` | requirements-analysis, functional-dependencies |
| `safe-outputs-table` | requirements-analysis, equivalence-classes, fault-injection |
| `safe-outputs-table-charge` | requirements-analysis, equivalence-classes |
| `safe-outputs-table-bt148` | requirements-analysis, boundary-values |
| `safety-context-elf` | requirements-analysis |
| `e2e` | requirements-analysis |
| `adc-access` | requirements-analysis, boundary-values, functional-dependencies |
| `adc-access-averaging` | requirements-analysis, equivalence-classes, fault-injection |
| `safe-state` | requirements-analysis, equivalence-classes, boundary-values |
| `safe-state-degraded` | requirements-analysis, equivalence-classes |
| `safe-state-select` | requirements-analysis, equivalence-classes, fault-injection |
| `safe-state-hold` | requirements-analysis, error-guessing, functional-dependencies, fault-injection |
| `safe-supervisor` | requirements-analysis, equivalence-classes |
| `safe-supervisor-bare` | requirements-analysis, equivalence-classes |
| `safe-supervisor-mpu` | requirements-analysis, error-guessing, fault-injection |
| `safe-supervisor-degraded` | requirements-analysis, equivalence-classes, functional-dependencies, fault-injection |
| `safe-supervisor-degraded-mutations` | fault-injection |
| `safe-supervisor-bridge-fault` | requirements-analysis, equivalence-classes, fault-injection |
| `safe-supervisor-bridge-fault-mutations` | fault-injection |
| `rom-check` | requirements-analysis, equivalence-classes, boundary-values, error-guessing, fault-injection |
| `rom-check-mutations` | fault-injection |
| `nvm-flash-write-bracket` | requirements-analysis, functional-dependencies |
| `rom-check-image` | requirements-analysis, boundary-values, fault-injection |
| `safe-system` | requirements-analysis, equivalence-classes, fault-injection |
| `demoswc-sil` | requirements-analysis, operational-use-cases |
| `target-demo` | requirements-analysis |
| `target-calibration` | requirements-analysis, equivalence-classes, fault-injection |
| `elfcheck` | requirements-analysis, equivalence-classes |
| `elfcheck-fcf` | requirements-analysis, equivalence-classes, fault-injection |
| `elfcheck-isr` | requirements-analysis, fault-injection |
| `elfcheck-stm32` | requirements-analysis, fault-injection |
| `elfcheck-demo` | requirements-analysis, fault-injection |
| `elfcheck-bt148` | requirements-analysis |
| `elfcheck-s32k116` | requirements-analysis, fault-injection |
| `elfcheck-timing` | requirements-analysis, equivalence-classes |
| `elfcheck-timing-host` | requirements-analysis, equivalence-classes, fault-injection |
| `elfcheck-timing-demo` | requirements-analysis, fault-injection |
| `elfcheck-timing-bt148` | requirements-analysis |
| `bl-image-start-host` | requirements-analysis, fault-injection |
| `progress-checks` | requirements-analysis, boundary-values, fault-injection |
| `lpit-deadman-progress` | requirements-analysis, boundary-values, fault-injection |
| `safety-progress` | requirements-analysis, functional-dependencies, fault-injection |
| `rtegen-age` | requirements-analysis, equivalence-classes, fault-injection |
<!-- vp-derivation-examples:end -->

Every entry with `requirements:` carries the field; the consistency test requires it ("present on every safety
entry", since SYS-166) besides the rule "known values, only on safety entries" and the equality with this table.

---

## 6. Regression strategy

| Ring | When | What | Who | Gate |
|---|---|---|---|---|
| R-A Task | before the agent commits | the entries of the agent's own files, at most one small group (`tools/test -k`), target build of the touched profile | agent | the coordinator does not merge red |
| R-B Merge | after every merge into the integration branch | `tools/test --since <merge base> --jobs 4`: every entry whose `covers`, `watch`, imports or own entry the merge touched, plus the groups the changelog names | coordinator | red → fix-up before the next merge |
| R-C Integration | at least once per integration branch before it goes to `dev`, and after a tool pin changes | the full list `tools/test --clean` (all groups, without `--slow`) | coordinator | red → the branch does not go to `dev` |
| R-D Baseline | per release candidate | pre-flight of section 8, then `python -m tools.ci run --strict --clean [--slow]` on the frozen candidate; board regression for a safety release (section 9); for Release R1 the strict, slow, bound run and the board points of section 9.1 | coordinator, user for the board | `cm-plan.md` P2 |

A change of a tool pin (CI-06), of `pipeline.toml` or of `coverage.yaml` always goes through R-C. The mutation runs
(`--slow`) are part of R-D for a safety release and of R-B when the merge touches the unit they mutate (`--since`
selects them only with `--slow`).

---

## 7. The report of a baseline

### 7.1 Content and provenance (G1)

`python -m tools.ci run --out <dir>` writes `ci-result.json`, `summary.md`, `tests.json`, `tests.log`, the coverage
report (`coverage.json`, `coverage.md`, `coverage.log`), the static analysis under `misra/` and the artefacts with
sizes. Since this issue `ci-result.json` also records which state it judged:

<!-- vp-provenance:start -->
| Field of `provenance` | Content |
|---|---|
| `commit` | full commit of `HEAD` at the start of the run |
| `tree` | tree hash of that commit (equal for the candidate on `dev` and the release merge on `master`) |
| `describe` | `git describe --tags --always --dirty`, for the reader |
| `openbsw` | gitlink of the OpenBSW submodule in that commit |
| `version` | content of `VERSION` |
| `dirty` | whether the working tree had tracked or untracked changes (ignored files such as `build/` do not count) |
| `changes` | the first 20 changed paths, `changes_total` their number |
| `end` | `commit`, `tree`, `dirty`, `changes_total` at the end of the run |
<!-- vp-provenance:end -->

`workspace_provenance` holds the same for a customer workspace run, `slow` and `release` the flags of the run. The
summary prints the line `Stand <describe>: Commit …, Baum …`. A HEAD that moved during the run is a `Herkunft`
finding, **red with `--strict`**: a report of hours must not mix two states. Changes that appear during the run (a
test writing into the tree, someone working in it) are a warning.

### 7.2 Binding and archiving (G2)

A report is **bound** to a baseline when (`tools/ci/release.py: report_binding`):

1. `ci-result.json` exists, verdict `pass`, the run was `--strict`, a run of the tree (not of a workspace);
2. it records its provenance (reports written before this issue do not - `v0.2.0` is such a case);
3. the working tree was clean at the start and HEAD did not move;
4. its tree hash equals `HEAD^{tree}` of the checkout that is tagged, and its `VERSION` the release version.

Procedure, after step 5 of `cm-plan.md` 5.2 (merge on `master`) and before the tag (step 6):

```sh
python -m tools.ci release --report-dir build/ci-out-vX.Y.Z --strict [--profile open --archive]
```

It copies the report to `<archive>/vX.Y.Z/` (`NX_RELEASE_ARCHIVE`, else `%LOCALAPPDATA%/nextbsw/release-archive`),
writes `<archive>/ci-out-vX.Y.Z.zip` and `ci-out-vX.Y.Z.zip.sha256`, and prints in `release-plan.md` the tag message
line `CI report: sha256 <hash> (ci-out-vX.Y.Z.zip)` and the command for the GitHub release asset
(`gh release create vX.Y.Z <zip> <zip>.sha256 …`), which the integrator runs after the tag is pushed (step 9). The tool
never tags and never pushes. `--strict` stops at an unbound report and names every reason; without `--strict` the
plan says "Bound to this tree: no" with the reasons, which a development release may accept and name in its tag
message.

| Copy | Where | Retention |
|---|---|---|
| Release asset | GitHub release `vX.Y.Z` on `origin` (zip + `.sha256`) | permanent (Q-CM3) |
| Local archive | `<archive>/vX.Y.Z/` and the zip, on the workstation; a second medium is the user's backup | permanent |
| Binding | SHA-256 in the tag message; `tree` in the report | permanent with the tag |
| Other CI runs | GitHub Actions artefacts | 14 days, working records only |

For a safety release the archives of ARM GCC, CMake and Ninja go next to the report (Q-CM3); `tools.ci release` does
not copy them yet (open, section 10).

---

## 8. Finding faults before the release CI

### 8.1 What the first release run showed

The release procedure for `v0.2.0` (coordinator, 2026-10-01) needed **three** runs of `tools/ci run --strict` of
about **5 h** each. The faults that turned them red were found only there, in `calgen`, `mcudb`, `stackuse`,
`safe-outputs-table` and in the classification of the static analysis (files under a `build/` folder inside the
tree). The detailed causes are the coordinator's record of the release — `CHANGELOG.md` section "0.2.0 – 2026-10-01"
and the entry REL-020-FIX4; the duration of about 5 h per run is the coordinator's account of 2026-10-01, not a
recorded measurement. This plan uses only their pattern:

| Pattern | Why the earlier rings missed it | Pre-flight step that finds it |
|---|---|---|
| A merge changes an input of an entry that is not in the agent's group (generated reports such as the stack-use report, tables checked against an independent expectation, mcudb data) | R-A runs only the agent's group, R-B was run per group, not per touched input | PF-3 |
| A stage that runs only in the full CI (coverage, static analysis) meets something new | `tools/test` alone never runs them | PF-4 |
| The environment of the run differs (folders inside the tree, a dirty working copy, tools of the workstation) | the release run was the first strict run in that checkout | PF-1, PF-2 |
| A fix-up between two runs breaks something else | each fix-up was followed by another full run | PF-3 on the fix-up, then one full run |

### 8.2 Pre-flight (about one hour, then the release CI once)

| Step | Command | Finds | Time (order) |
|---|---|---|---|
| PF-1 Freeze | a separate clean worktree of the candidate commit: `git worktree add <dir> <commit>`; nothing else works in it; build root and output folder outside the tree | changes during the run, folders in the tree | minutes |
| PF-2 Tools and profiles | `python -m tools.ci check --strict` | pin deviations, profiles without a decision | seconds |
| PF-3 Increment | `python -m tools.test --since v<previous> --list`, then the same with `--jobs 4` (with `--slow` for a safety release) | every entry whose inputs changed since the last baseline | most of the hour |
| PF-4 CI-only stages | `python -m tools.test -k target-demo` (writes the compile database), then `python -m tools.misra run --compile-db <build root>/demo/compile_commands.json` and `python tools/test/coverage.py --jobs 4` | new static-analysis findings, classification of generated files, coverage measurement failures | 20-30 min |
| PF-5 Document state | `tools/test -k safety` (document tests, `safety-trace`) on the candidate after release step 1 (`Baseline:` lines) | state lines, tables of the safety documents | minutes |

Only when PF-1 to PF-5 are green does the release CI start. A fault found by the release CI anyway is recorded with
the pattern it belongs to, and this section gets a new row; the goal is **one** release CI run per baseline.

**Tool (TOOL-209, in review since 2026-10-02):** `python -m tools.ci preflight --since v<previous>` runs PF-2 to PF-5
in this order and writes a short report (task proposal T-VP3 of issue 1). Its first real run on a release candidate is
the one before the release CI of Release R1 (section 9.1, CM-04 Z3).

---

## 9. What is verified per baseline

<!-- vp-baseline:start -->
| Item | Every merge (R-B) | Development release | Safety release |
|---|---|---|---|
| Entries touched by the change | yes | (in the full run) | (in the full run) |
| Full list, `--strict`, `--clean` | — | yes | yes |
| Mutation runs (`--slow`) | when touched | recommended | **required** |
| Coverage report | — | reported | **gate on the safety modules** (VP-1) |
| Static analysis gate | — | yes | yes |
| Report bound to the tree (7.2) | — | recommended; reasons named in the tag message if not | **required** (`tools.ci release --strict`) |
| Report archived (asset + local) | — | yes | yes, plus tool archives |
| Requirement coverage (`safety-trace`) | when touched | yes | yes, every SSR verified or argued |
| `derivation` on every safety entry | — | — | **required** (VP-7; met since SYS-166, checked by the consistency test) |
| Board regression of the tagged image | — | — | **required** for every SSR with method "board" (VP-6) |
| Host/target bridges of 4.2 | — | in the full run | in the full run, remaining items argued (VP-3) |
| Inspections and confirmation review CM-04 | — | — | closed (P6, safety plan section 5) |
<!-- vp-baseline:end -->


### 9.1 Release R1 (`v1.0.0-rc1`)

Release R1 is the first release the user wants to be releasable (Freigabeumfang R1, user decision 2026-09-30). By the
safety plan (section 5, deviation for release R1) it is a **development release without a safety claim** (D-01,
precondition R5 not met). It is not judged against the development column of the matrix alone: the user decided in
CM-04 (2026-10-04) what it needs on top. **Release R1 = development release plus:**

1. **Release CI as for a safety release** (CM-04 Z3): `python -m tools.ci run --strict --clean --slow` on the frozen
   candidate — the mutation runs included — and the report **bound** with `python -m tools.ci release --strict`
   (section 7.2); `v1.0.0-rc1` is the first bound report. Before it the **pre-flight** `python -m tools.ci preflight`
   (section 8.2) — its first real run on a candidate, which also completes TOOL-209.
2. **Board list before the tag** (CM-04 Z4): SYS-167 delivers the board regression list (T-VP2) before the tag is set.
   The list contains **BT-S1-03** (S1 confirmed by read-back, `fmea.md` B15; decision BRD a of the re-check of CM-02,
   2026-10-04).
3. **Board run on the tagged image** (CM-04 Z4): a suite run of the demo cases of `board-test-spec.md` with
   `tools/boardtest suite` on the tagged rc1 image, plus the restart-time measurement F4 (review BL38-P1 F4 a: at least
   four runs with the oscilloscope); records with image CRC and the tag. The board state of 2026-10-02 … 2026-10-04
   (section 2.3) was taken at integration states and does not replace this run.
4. **Requirement coverage, derivation and the automated rows** as in the development column; `safety-trace` green;
   CM-04 performed (record CR-04-1).

**Limits of Release R1** — required for the first safety release, not for Release R1, and named in its release
record:

| Limit | Decision | Carrier |
|---|---|---|
| no coverage gate; shortfalls measured, not argued per module (3.4) | VP-1 a (2026-10-02), CM-04 Z2 | before the first safety release |
| architecture-level coverage argued, not measured | VP-2 a, CM-04 Z2 | before the first safety release |
| HT-2/HT-3 without a bridge on the host (4.2) | VP-3 a, CM-04 Z2 | SYS-168 |
| no independence of test authoring: the coordinator only re-runs the tests of an agent; a second agent reviewing every new or changed safety test before the merge is decided (VP-9 b) but not in place | VP-9 b (2026-10-02), CM-04 Z6 | SYS-181, after Release R1 |
| board-list run on the tagged image required only for a safety release in general (VP-6); for Release R1 the demo cases and F4 (point 3) | VP-6 a, CM-04 Z4 | SYS-167 |
| tool archives not next to the report, reproduction on a second machine not shown, no release-asset workflow; Release R1 is distributed privately and the local archive with SHA-256 suffices | CM-04 Z9 | TOOL-210, TOOL-211, TOOL-212, after Release R1 |
| safety entries without a counter-check not sighted (3.5); the list is an upper bound (finding VF-1, section 10.1) | CM-04 finding F-09, re-check T8 and RN | **TOOL-228** (T-VP8, P2), before the first safety release |
| the HR/R ratings of sections 2 and 5 not checked against the licensed standard (VP-10) | CM-04 re-check Z7 | the user, before the first safety release |
| confirmation measures CM-03, CM-08, CM-09, CM-11 after R1 | safety plan section 5 | D-22 of the safety case |

As long as no independent reviewer is named (safety plan O1), every tag is a development release in any case; the plan
is written so that a safety release needs no further plan work, only the open rows.

---

## 10. Gaps and tasks

| ID | Gap | Task | Prio | Release R1 |
|---|---|---|---|---|
| T-VP1 | ~~`derivation` on 6 of 108 safety entries~~ done | the field is filled for every safety entry, each read against its test, and the consistency test requires it — **SYS-166** (done) | P1 | done |
| T-VP2 | the board level is per task, not per baseline | a board regression list per SSR with method "board", run on the tagged image of a safety release, records with image CRC and the tag — **SYS-167** (open) | P1 | the list before the tag (9.1 point 2) |
| T-VP3 | pre-flight is a list of commands | `tools.ci preflight` (section 8.2) — **TOOL-209** (review) | P2 | first real run before the release CI (9.1 point 1) |
| T-VP4 | tool archives for a safety release (Q-CM3) are copied by hand | `tools.ci release` copies the cached archives of `versions.toml` next to the report, with their SHA-256 — **TOOL-210** (open) | P3 | limit, after R1 |
| T-VP5 | host tests are built only with the host's `char` signedness and widths | an additional host-test variant with `-funsigned-char` for the safety units, or a static check of HT-2/HT-3 (VP-3) — **SYS-168** (open) | P2 | limit, after R1 |
| T-VP6 | the reproduction of a baseline across machines is not proven (cm-plan G8, AN-092) | one reproduction on the Linux runner and on the workstation, image CRCs compared — **TOOL-211** (open) | P3 | limit, after R1 |
| T-VP7 | a release run on GitHub Actions keeps its artefacts 14 days | a workflow on tag push that attaches the report as asset (touches `.github/workflows`) — **TOOL-212** (open) | P3 | limit, after R1 |
| T-VP8 | 39 safety entries without `fault-injection` and without a counter-check marker (3.5) | sight each, add a broken copy or a mutation run or argue the independent expectation; update the list; with it the `derivation` of `data-flash-system` and the marker search (finding VF-1) — **TOOL-228** (open; CM-04 re-check T8, 2026-10-04) | P2 | limit, after R1 |
| — | independence of test authoring (VP-9 b) | a second agent reviews every new or changed safety test before the merge — **SYS-181** (open) | P2 | limit, after R1 |

### 10.1 Findings after issue 2

Findings against this plan found after its release. Each is registered in the anomaly register with the source
`verification-plan.md VF-n` (DOC-59: an open finding of the record CR-04-1 would contradict its verdict `accepted`).

| ID | Finding | Found by | Carrier | Status |
|---|---|---|---|---|
| VF-1 | (a) `tools/test/tests.yaml` gives `data-flash-system` the `derivation` `requirements-analysis` only, although its test runs a built-in mutation check (`MutationTest`, 15 mutants) - against the rule of 5.3 (`fault-injection` where the test inserts faults); section 3.5 therefore lists it as "none found", the counts would be 50 and 38 instead of 51 and 39, and 39 is an upper bound; the marker search of 3.5 does not know "Mutant" or "MutationTest". (b) The sentence "a number in the running text is one of these" (header, 3.6) does not hold for 12/39 (3.5), 91/31/3/7 (3.3), 20 mutants (2.2) and "6 … 104" (5.3) | re-check of CM-04 (record CR-04-1, RN minor, remarks R4 and R9; 2026-10-04) | TOOL-228, with the next issue of this plan | open (AN-176) |

---

## 11. Review points and decisions

Each point is a statement of issue 1 that the safety manager decided in the safety review of 2026-10-02 (artifact
of the user, transcribed into `doc/todo.yaml` SYS-67) or in the confirmation review CM-04 of 2026-10-04 (record
`reviews/records/CR-04-1.md` (not published, available on request), decisions Z1 … Z10).

| ID | Title | Statement | Options | Decision |
|---|---|---|---|---|
| VP-1 | Coverage gate | coverage is measured but not a gate; the safety modules miss the branch proposal in part | (a) gate on the safety modules before the first safety release, others reported; (b) gate on all modules; (c) stay a report, argue every shortfall | **(a)**, 2026-10-02; a limit of Release R1 (CM-04 Z2, section 3.4) |
| VP-2 | Architecture-level structural coverage | function and call coverage (recommended at B) is not measured | (a) argue by the requirement coverage of I3/I5 and the call graph of `tools/stackuse` for the first safety release, measure later; (b) measure now | **(a)**, 2026-10-02; a limit of Release R1 (CM-04 Z2) |
| VP-3 | Host/target differences without a bridge | HT-2 and HT-3 (widths, `char` signedness) rely on static analysis only | (a) T-VP5 before the first safety release; (b) accept the static analysis as the bridge, with a deviation record | **(a)**, 2026-10-02, SYS-168; a limit of Release R1 (CM-04 Z2) |
| VP-4 | Mutation runs per release | required for a safety release, recommended for a development release | (a) as stated; (b) required for every release (longer runs) | **(a)**, 2026-10-02; for Release R1 required anyway (CM-04 Z3, section 9.1) |
| VP-5 | Unbound report for a development release | `v0.2.0` is archived with a report written before the provenance existed; its tag message names that | (a) accept for development releases, never for a safety release; (b) re-run the release CI for `v0.2.0` | **(a)**, 2026-10-02; Release R1 is bound (CM-04 Z3) |
| VP-6 | Board regression per safety release | today board evidence is per task and image; a safety release needs evidence of its own image | (a) T-VP2: rerun the board list on the tagged image; (b) accept earlier images when the difference analysis of the image (CRC-relevant changes) shows no change of the mechanism | **(a)**, (b) only for documented exceptions, 2026-10-02, SYS-167; for Release R1 the list before the tag and the demo cases plus F4 on the tagged image (CM-04 Z4) |
| VP-7 | Derivation recording | the field `derivation` plus the case names in the code are the test specification | (a) as stated, T-VP1; (b) a test specification document per safety module (StrictDoc) | **(a)**, 2026-10-02; done (SYS-166) |
| VP-8 | Test specification per case | ID = entry plus case name, version = commit, expected result = assertion; no separate document | (a) accept; (b) require (b) of VP-7 | **(a)**, CM-04 Z5 (2026-10-04) |
| VP-9 | Independence of test authoring (8-9.4.3.2) | code and its tests are often written by the same agent; the coordinator re-runs them | (a) the CM-04 reviewer samples safety tests per release and records it; (b) a second agent reviews every safety test | **(b)**, 2026-10-02 (against the recommendation a) → SYS-181 after Release R1; until then only the re-run by the coordinator, named as a limit (CM-04 Z6, section 9.1) |
| VP-10 | Method ratings | the HR/R ratings of sections 2 and 5 are our reading of part 6 | the reviewer checks them against the licensed standard in CM-04 | **open** - check by the user against the licensed standard before the first safety release (CM-04 Z7: the re-check of 2026-10-04 found no record of the check, the decision "checked" rested only on "no finding raised"); not needed for Release R1 without a safety claim |
| VP-11 | Strict release run and moved HEAD | a moved HEAD is red only with `--strict`; working-tree changes during a run are a warning | (a) as stated; (b) red for any change during a strict run | **(a)**, 2026-10-02 |

---

## 12. Clauses addressed

| Gap row | Clause | Before | After this issue | Still missing |
|---|---|---|---|---|
| 8-20 | 8-9.4.1 | partial | plan per level with methods, criteria, environment, tools, regression (sections 2-6) | — (issue 2 released in the re-check of CR-04-1, 2026-10-04) |
| 8-21 | 8-9.4.2 | partial | test specification rule (5.2) | — (VP-8 decided in CM-04) |
| 8-22 | 8-9.4.3 | partial | report bound to the tree and archived per baseline (7); `tools/ci` records provenance | first bound report (Release R1, `v1.0.0-rc1`, section 9.1) |
| 6-23, 6-28, 6-35 | 6-9.4.3, 6-10.4.3, 6-11.4.3 | partial | methods and recording rule (5) | — (T-VP1 done) |
| 6-25, 6-32 | 6-9.4.5, 6-10.4.7 | partial | difference analysis HT-1 … HT-9 (4.2) | T-VP5 (SYS-168) |
| 6-26 | 6-10.4.1 | missing | integration steps I1-I6 (2.2) | — |
| 6-24, 6-30 | 6-9.4.4, 6-10.4.5 | partial / missing | targets and state (3.4) | VP-1, VP-2 |
| 6-33, 6-34 | 6-11.4.1, 6-11.4.2 | partial | methods at target level (2.3), per-baseline rule (9), Release R1 (9.1) | T-VP2 (SYS-167) |
| — | cm-plan G1, G2 | open | provenance in the report, binding, archive with SHA-256 | tool archives (T-VP4), asset workflow (T-VP7); AN-085/AN-086 are confirmed in CM-06 |

## 13. Review questions of the architecture (section 11)

SYS-67 adds one document, one consistency test and two small changes to `tools/ci` (provenance in the report,
binding and zip of the archived report). **Board without an SBC, other crystal, other derivative, several CAN buses,
several tasks:** nothing in the plan or in the tool change depends on a board or a derivative; the levels name the
build profiles generically, and the profile coverage check of `tools/ci` keeps every profile in the run. **New values
in one configuration level:** none. **Host test with its own profile:** the tool changes are tested against
synthetic git repositories in `%TEMP%`, not against this repository's state. **Qt-free core, YAML paths:** `tools/ci`
stays free of Qt; findings name the field they are about. **Device-driver granularity, platform rule:** not
applicable. Issue 2 (DOC-54) adds two rules to the consistency test (counts, list of section 3.5) and changes no
tool; the board run of Release R1 (9.1) is a run on the eval board as the first example and adds nothing that
depends on it.

## 14. Sources

- `iso26262-gap.md` (not published, available on request) rows 6-22 … 6-36, 8-20 … 8-23 and section 6 task 9; ISO 26262-6 and -8 (2018)
  cited by part and clause only.
- [`cm-plan.md`](cm-plan.md) sections 5, 7 and 10 (G1, G2, G8); [`safety-plan.md`](safety-plan.md) rows P6-14 …
  P6-20, P8-06, section 5 (deviation for release R1), release preconditions section 6;
  [`dev-environment.md`](dev-environment.md) sections 5 and 7; `reviews/checklists.md` (not published, available on request) CM-04;
  [`board-test-spec.md`](board-test-spec.md); [`safety-manual.md`](safety-manual.md) sections 1.3 and 7.
- Repository at the working state: `tools/test/tests.yaml` (counts in section 3.6), `tools/test/README.md`,
  `tools/test/coverage.yaml`, `tools/ci/{ci,release,preflight}.py`, `tools/ci/{pipeline,versions}.toml`,
  `doc/safety/traceability.md`, `doc/safety/fmea.md` section 8.
- The coordinator's account of the `v0.2.0` release runs (2026-10-01; `CHANGELOG.md` section 0.2.0, REL-020-FIX4):
  three runs, about 5 h each, the five fault areas of section 8.1.
- The decisions of the safety review of 2026-10-02 (VP-1 … VP-11, `doc/todo.yaml` SYS-67) and of CM-04
  (`reviews/records/CR-04-1.md` (not published, available on request)).

## 15. Change history

| Issue | Date | Task | Change |
|---|---|---|---|
| 1 | 2026-10-01 | SYS-67 | first issue, written against `integ/night11` at `baa1d837` while the coordinator ran the release procedure for `v0.2.0`; `tools/ci` records provenance, binds and archives the report |
| 1 (update) | 2026-10-02 | SYS-166 | the field `derivation` filled for every safety entry and required by the consistency test; VP-7 and T-VP1 closed |
| 1 (update) | 2026-10-02 | integration of the wave of 2026-10-02 (`c86513c2`, `c56e50c7`), merge of SYS-173/SYS-174 (`be29c724`) | derivation table of section 5.3 brought to the merged entries (117, then 119) |
| 1 (update) | 2026-10-02 | SYS-75 | section 5.2: the board cases per SSR in `board-test-spec.md`, run with `tools/boardtest suite`, record `suite-run.json` |
| 1 (update) | 2026-10-03 | integ/r1 (`55bdbe08`, after SYS-95) | derivation table: the entries of SYS-95 (`progress-checks`, `lpit-deadman-progress`, `safety-progress`) |
| 1 (update) | 2026-10-04 | DOC-51 (`7bec5034`) | derivation of `rtegen-age` (SSR-062 partial, CM-07 ZE) |
| **2** | 2026-10-04 | DOC-54 | corrections after the confirmation review CM-04 (record `reviews/records/CR-04-1.md`, the user under D-01, baseline `dev` at aaa22c86, `accepted with conditions`; Z1 issue 2): header table with title, issue, author, approver, status and this history, state line at the working state (F-01, F-02); decisions of the safety review of 2026-10-02 and of CM-04 in section 11, task IDs in section 10, the release record of `v0.2.0` with its place (F-03); counts in a test-bound table 3.6 instead of stale numbers in the text (F-04); "Release R1 (`v1.0.0-rc1`)" and "precondition Rn" kept apart (F-05); the AoU that hand verification to the integrator in section 1 and the vehicle and integration test in the safety manual 1.3 (F-06); board coverage per SSR and the two open SSR in 3.3 (F-07); decision VP-1 a and the missing argument per module in 3.4 (F-08); the list of safety entries without `fault-injection` in 3.5, test-bound, and proposal T-VP8 (F-09); section 9.1 "Release R1 = development release plus" with the strict, slow, bound release CI, the pre-flight, the SYS-167 list and the board run on the tagged image (Z3, Z4, Z8) and the limits of Release R1 (Z2, Z6, Z9); pre-flight tool TOOL-209 in 8.2; board suite and R1 board state in 2.3 |
| 2 (accepted) | 2026-10-04 | DOC-59 | re-check of CM-04 by the user (D-01, without independence) at `dev` efc474ea: F-01 … F-09 closed, issue 2 released (record CR-04-1 `accepted`). After it, as decided there: VP-10 **open**, checked by the user before the first safety release (Z7; header, sections 0, 9.1, 11); T-VP8 is task TOOL-228 (T8; sections 0, 3.5, 9.1, 10); finding RN as VF-1 in the new section 10.1 (AN-176), carried by TOOL-228 - the counts 51/39 and `data-flash-system` are not corrected here; 9.1 point 2 names BT-S1-03 (CM-02 re-check BRD a); section 12; state line |
| 2 (update) | 2026-10-07 | TOOL-239 | section 3.6: 414 entries (new entry `fahrplan`, no safety entry), no other count changed; not part of the review in CM-04 |
| 2 (update) | 2026-10-07 | PLAT-119 | section 3.6: 416 entries (new entries `swc-demoswc-substitute`, `target-demoswc-substitute`, no safety entry), no other count changed; not part of the review in CM-04 |
