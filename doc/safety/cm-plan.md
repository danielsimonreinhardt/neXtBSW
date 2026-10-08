# neXtBSW configuration management plan and baselines (SYS-68)

Status: 2026-09-28, **issue 2 (SYS-94)**: the user's decisions Q-CM1 … Q-CM4 (decision board `q-cm-plan`,
2026-09-28) are worked in, the state line of section 6 is set in every safety document and checked by
[`test/test_cm_plan.py`](test/test_cm_plan.py) (gaps G5, G6). Issue 1 (**SYS-68**) was written against `dev` at
`ae92e89` (after release `v0.1.0`), draft for acceptance by the user.
Update 2026-10-02 (**SYS-182**, confirmation review CR-01-1 of the safety plan, finding F-07): P9 is binding through
the safety plan's precondition R6 (no longer a proposal); section 5.1 names the safety plan's R for each P (the
mapping is kept in safety plan section 6 and checked by `test_safety_plan.py`).
Update 2026-10-08 (**PLAT-118a**): section 4.3 names the next candidate `v1.0.0-rc2` and the state of its
preconditions before the tag; no rule of this plan changed.
Update 2026-10-08 (**SYS-207**, **SYS-209**): section 4.3 states for `v1.0.0-rc2` that a public development release
without a safety claim is allowed under the restated deviation D-22 (safety plan issue 3, released in the delta
re-check of CM-01 on 2026-10-08) and where the statement "no safety claim" stands; no rule of this plan changed.
Working state: dev at c0928380 (after v1.0.0-rc1)
Readers: the user as configuration manager and release authority, the coordinator who merges and releases, the agents
of SYS-60 (safety plan), SYS-64 (safety case), SYS-67 (verification plan), TOOL-57 (change requests), DOC-19 (safety
manual) and DOC-21 (StrictDoc).
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**What this document is.** The written configuration management plan that
`iso26262-gap.md` (not published, available on request) found missing (rows 8-12, 8-13, 8-14, 2-12; section 6 task 10; finding SG7). It
states which items are under configuration management, how they are identified, what a **baseline** is, how a release
is made, and how the safety documents name the state they are about. The standard is cited by part and clause only
(for example "8-7.4.3" = ISO 26262-8:2018, 7.4.3); no text of it is reproduced.

**What it decides and what it only proposes.** Binding is the user's decision **iso-q3** of 2026-09-22: *a release is
an annotated tag `vX.Y.Z` on `master`; before it there is a green CI run with an archived report, the safety documents
refer to the tag, the qualification reports exist and the reviews are closed.* Everything else here works that
decision out. Issue 1 marked the points where the plan went beyond it as **[Proposal]** and put four of them to the
decision board (section 11); the user decided all four as recommended on 2026-09-28 (decision board `q-cm-plan`), and
issue 2 marks them **[Decided Q-CMn]**. What is still marked **[Proposal]** (the release lines of 4.4, the fields
of the tag message beyond the status line) was not part of those questions; P9 is binding since 2026-10-02 because it
is the safety plan's precondition R6 (accepted with the safety plan, SYS-182).

**What it is not.** Not the change management process (that is `doc/todo.yaml` plus TOOL-57), not the verification
plan (SYS-67), not the documentation management rules (DOC-21). It names the interfaces to them.

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Configuration items | 13 classes, from the element's source code to the verification records (section 2); build output, `doc/confidential` and keys are deliberately **not** items, with the reason |
| Identification | every item in git is identified by the **commit**; the element as a whole by `VERSION` (SemVer with the interface meaning of 3.2, ARCH-55, Q-CM4) and the release tag; the OpenBSW submodule by its gitlink; external tools by version plus SHA-256 (`tools/ci/versions.toml`) |
| Baseline | an **annotated tag `vX.Y.Z` on `master`**, never moved, never deleted (section 4). It fixes every item in git at once. What lies outside git (CI report, tool archives) is bound to the tag by SHA-256 |
| Safety status of a baseline | **[Decided Q-CM1]** each release tag states in its message whether it is a **safety release** (all preconditions of section 5.1 met) or a **development release** (reproducible, but without a safety claim). Only a safety release may be cited as evidence in a safety case |
| Existing baselines | **one**: `v0.1.0` → `ed48460` (merge commit on `master`, annotated, not signed, 2026-09-28). By section 5.1 it is a **development release**: its CI report is not archived beyond 14 days, the safety documents do not cite it, qualification reports and reviews do not exist |
| Release procedure | nine steps from the release candidate on `dev` to the archived report (section 5.2), with the checks that already exist in `tools/ci` and `tools/nxconfig pin` |
| Citation rule | every safety document carries one line `Baseline: vX.Y.Z` (or `Working state: dev at <commit> (after vX.Y.Z)`) in its header; commit hashes in the body are history, not the state the document claims (section 6) |
| Loose commits | issue 1 found 11 different hashes in 6 documents (section 6.3); all 10 neXtBSW commits among them are contained in `v0.1.0`, the eleventh is the OpenBSW submodule commit that `v0.1.0` pins. **Issue 2 (SYS-94):** 20 of the 21 safety documents carry the state line (all but the generated `traceability.md`, see 6.4); the old hashes stay as history |
| Archiving | **[Decided Q-CM3]** the release CI report as a GitHub release asset plus a local copy; for a safety release also the external tool archives (section 7) |
| Signing | **[Decided Q-CM2]** release tags are signed with the user's SSH key from the first safety release on; development releases stay annotated only (4.1 rule 5, G3) |
| Gaps found on the way | the CI report does not record commit or tree hash (G1); CI artifacts are kept 14 days (G2); tags are not signed and `pincheck` checks no signature (G3); `master` carries its merge commits only, `dev` does not merge them back (G4, harmless, rule stated); G5 and G6 are closed by SYS-94 — section 10 |
| Verification | issue 1: **analysis** (mock), the repository read at `ae92e89`, `pincheck` in release mode on a checkout of `v0.1.0` returned `ok`. Issue 2: **host test** (mock) [`test/test_cm_plan.py`](test/test_cm_plan.py) with counter-checks on broken copies; no code changed, no hardware |

---

## 1. Scope, roles and relation to other plans

### 1.1 Scope

The plan covers the **neXtBSW development repository** (private, the repository owner's GitHub account) as the
development repository of the SEooC, including the work products needed to reproduce the element and its evidence
(8-7.4.3). It covers the open source line (published as `github.com/danielsimonreinhardt/neXtBSW`, an export of
this repository without history) and the licensed lines of
`lizenz-und-release.md` (not published, available on request) (PLAT-46) only in so far as each of them is derived
from one baseline of this repository (section 4.4).

A **customer repository** (workspace with `nextbsw.yaml`, [`customer-project.md`](../architecture/customer-project.md))
is the integrator's configuration management, not this plan's. The interface is one sentence: the integrator pins a
**release tag** of this repository, and `tools/ci run --workspace <dir> --release` proves the pin is a clean,
annotated, reachable release (TOOL-65).

### 1.2 Roles

neXtBSW has one human. The roles are named so that a second person can take one of them later (iso-q1 is still open;
since 2026-09-30 the user reviews himself under deviation D-01, see the row "Reviewer").

| Role | Held by | Responsibility in this plan |
|---|---|---|
| Configuration manager, release authority | the user | accepts this plan; approves every merge to `master` and every release tag; decides the safety status of a release |
| Integrator of `dev` | the coordinator session (exactly one at a time, CLAUDE.md 2026-09-16) | merges checked agent branches to `dev`, keeps planning, changelog and `tests.yaml`, pushes `dev`, prepares release candidates, creates the tag after approval |
| Contributor | agents (`agent/<ID>` locally, `claude/<id>` in cloud sessions), the user | changes only the paths of their task; never commit to `dev`/`master`, never tag |
| Reviewer | an independent person not yet named (iso-q1, SYS-63); **for now the user himself** (decision of the safety responsible 2026-09-30 on AN-093; safety plan tailoring T-11; `reviews/README.md` section 3.1, deviation D-01) | confirmation reviews (SYS-118). A review by the user is a confirmation **without independence**: it does not meet P6, so no release can be a safety release on it, and no working state or tag gets an ASIL statement until an independent person has reviewed it (section 5.1, P6) |

Independence: the integrator re-runs tests and builds of every agent branch, and the user accepts; for the author of
code and its tests this is not independence at I1 (gap row 8-23). This plan does not change that.

### 1.3 Interfaces to other plans

| Plan / process | Task | What it takes from this plan | What this plan takes from it |
|---|---|---|---|
| Safety plan | SYS-60 | the release definition and its preconditions (section 5.1) | the list of work products per release and who checks them |
| Verification plan | SYS-67 | the baseline a report belongs to; the rule that a report is archived per release (section 7) | the report format, what "green" means per level |
| Change management | `doc/todo.yaml`, TOOL-57 | the base configuration of a change (section 8) | task IDs, safety impact per change |
| Documentation management | DOC-21 | the baseline line of a document header (section 6) | author, approver, status fields |
| Safety case | SYS-64 | "evidence is cited per safety release" | — |
| Release lines | PLAT-46, PLAT-50 | "one line = one baseline + one profile" (section 4.4) | the export and its check (`tools/distcheck`) |
| Customer projects | ARCH-54, TOOL-65 | the tag rules (annotated, on `master`, `VERSION`) | the pin check, which already enforces them |

---

## 2. Configuration items

A configuration item (CI) is anything whose exact state is needed to rebuild the element, to re-run its verification
or to understand a safety claim. The table groups them in classes; each class names how its state is identified.

| Class | Item | Where | Identified by | Changed by |
|---|---|---|---|---|
| CI-01 | Source code of the element | `libs/`, `platforms/`, `ecu/`, `bootloader/`, `swc/` (component code), root `CMakeLists.txt`, `CMakePresets.json`, `cmake/` | commit | contributors via task, merged by the integrator |
| CI-02 | Pre-existing software | `external/openbsw` (submodule, never modified; contains FreeRTOS and ETL), the modified copies of OpenBSW files listed in `tools/openbswsync/copies.yaml`, CMSIS/vendor headers in the tree, newlib from the pinned GCC | gitlink commit of the submodule; `copies.yaml` in the commit; GCC version and SHA-256 in `versions.toml` | submodule update only as its own task |
| CI-03 | Configuration data | `mcudb/**`, `boards/*/board.yaml`, `projects/*/project.yaml`, `projects/*/rte.yaml`, module manifests, `swc/*/swc.yaml` | commit | contributors via task |
| CI-04 | Models and generated code that is checked in | `swc/*/*.slx` and the component code generated from it by `tools/swc/swc_build.m` | commit; the generator input is in the same commit | model and generator only, never by hand (CLAUDE.md) |
| CI-05 | Own tools (generators, checks) | `tools/**` except `tools/studio` | commit; the tool confidence level per tool in [`tool-classification.md`](tool-classification.md) | contributors via task |
| CI-06 | External tools and packages | ARM GCC, CMake, Ninja, actionlint: `tools/ci/versions.toml`; Python: `tools/ci/.python-version`; packages: `tools/ci/constraints.txt`, `constraints-docs.txt`, `tools/misra/requirements.txt` | version **and SHA-256** of each archive, exact package pins, in the commit | only by editing the pin file, then a full CI run (versions.toml header) |
| CI-07 | Tests, test data and verification settings | `tools/test/tests.yaml`, test folders next to the code, `tools/test/coverage.yaml`, `tools/misra/{rules,baseline,deviations,defects}.yaml`, `tools/ci/pipeline.toml`, `doc/safety/test/` | commit | contributors; `tests.yaml` only by the integrator |
| CI-08 | Safety work products | `doc/safety/**` (concept, fmea, requirements, gap analysis, tool classification, static analysis, this plan, the future safety plan, manual, case) | commit; the **baseline line** of each document (section 6) | task of the document; approval by the user |
| CI-09 | Architecture and reference documentation | `doc/architecture/**`, `doc/reference/**`, `libs/*/doc/**`, READMEs | commit | contributors via task |
| CI-10 | Planning and change records | `doc/todo.yaml`, `doc/TODO.md`, `CHANGELOG.md`, `doc/changelog.d/`, `doc/briefs/` | commit | integrator only |
| CI-11 | Verification records | board captures `tools/boardtest/captures/**` (469 files in git at `ae92e89`); CI report of a release (`ci-result.json`, `summary.md`, `tests.json`, `tests.log`, `coverage.*`, `misra/`) | board captures: commit; CI report: SHA-256 in the tag message (section 7) | captures by the integrator; the CI report is written only by `tools.ci run` |
| CI-12 | Release identity | root file `VERSION`, the release tag | `VERSION` in the commit; tag object | integrator after approval |
| CI-13 | neXtBSW Studio (tool, proprietary) | `tools/studio/**`, `tools/studio/version.py` | commit plus its own version `v_x.y.z`; recorded in the tag message | contributors via task (CLAUDE.md version rules) |

**Not configuration items**, with the reason:

| Not an item | Why | How it is still controlled |
|---|---|---|
| Build output (`build/`, `%TEMP%/s32k/…`, ELF/HEX/MAP) | reproducible from a baseline; keeping it would create a second truth | the CI report lists the artefacts with sizes; the image CRC is cited in board records |
| `doc/confidential/**` (ISO 26262, NDA data sheets) | licensed or NDA material, gitignored, must not enter the repository | cited by title, edition and revision in the documents that use it (`source:` fields in `mcudb`/`board.yaml`) |
| Keys (BL-03) | secrets, outside the repository | host tests use only the insecure test key; the release names no key |
| Decision board (artifact `items`) | an external working tool, not versioned by git | every decision is transcribed into `doc/todo.yaml` (`done_when`, "Entscheidung Nutzer <date>") or `CHANGELOG.md`, which are CI-10 |
| Workstation state (`.venv`, tool cache, IDE) | derived from CI-06 | `tools.ci check` compares it with the pins |

---

## 3. Identification and naming

### 3.1 Branches

| Branch | Purpose | Who writes | Rule |
|---|---|---|---|
| `master` | released states only | integrator, after the user's approval | receives **only merges from `dev`** (`--no-ff`); no direct commit, no hotfix on `master` — a fix goes through `dev` and a new PATCH release |
| `dev` | integration branch | integrator | receives only checked task branches; pushed after every merge (after `git fsck --full --no-dangling`); never force-pushed |
| `agent/<ID>` | one task of a local agent | the agent | stays local; kept after the merge (CLAUDE.md 2026-09-25) |
| `claude/<id>` | one task of a cloud session | the cloud session | pushed to `origin`, merged by the integrator |

The task ID in the branch name is the link to the change request (section 8).

### 3.2 Versions

- The element has **one** version: the root file `VERSION`, `MAJOR.MINOR.PATCH` without suffix (ARCH-55; read by
  CMake, `nxconfig`, Studio and `tools/ci`).
- **[Decided Q-CM4]** SemVer meaning for a base software: MAJOR = an incompatible change of an interface the integrator uses
  (RTE interface, configuration schema, safe-supervisor API, AoU); MINOR = new function, compatible; PATCH = fix
  without interface change. As long as MAJOR is 0, a MINOR step may break interfaces, and the changelog says so.
- The release tag is `v` + `VERSION` (`tools/nxconfig/version.py: tag_of`). No other tag name starts with `v<digit>`.
- The Studio has its own version `v_x.y.z` (`tools/studio/version.py`, CLAUDE.md); it is **not** a tag. A release tag
  records which Studio version belongs to it.

### 3.3 Commits in records

- In records meant to survive (tag messages, safety case, archived reports) a commit is written **in full** (40 hex).
- In prose a short hash of at least 7 characters is fine; it must be unique in the repository at the time of writing.
- A short hash of the OpenBSW submodule is marked as such ("OpenBSW submodule at `3b6e702`"), because it does not
  resolve in the neXtBSW repository.

---

## 4. Baselines

### 4.1 Definition

A **baseline** is an **annotated git tag `vX.Y.Z` on a commit reachable from `master`** (iso-q3). Because git commits
are content-addressed, one tag fixes every configuration item in git at once: source, configuration, generated code,
own tools, tool pins, tests, safety documents, board captures and the OpenBSW submodule commit (its gitlink). What lies
outside git is bound to the tag through its hash in the tag message (section 5.3): the archived CI report and,
through `versions.toml`, every external tool archive.

Rules:

1. **Annotated**, never lightweight: the tag object carries tagger, date and the message of section 5.3.
   `tools/nxconfig/pincheck.py` rejects a lightweight release tag (`tag-lightweight`).
2. **On `master`**: the tagged commit is the merge commit on `master` (or reachable from it); `pincheck` rejects
   `tag-unreachable`.
3. **`VERSION` equals the tag**: `tools/ci` is red otherwise (`check_release_version`).
4. **Immutable**: a published tag is never moved or deleted, and never re-created with the same name. A wrong release
   is corrected by the next PATCH release, whose changelog names what was wrong.
5. **[Decided Q-CM2] Signed from the first safety release on**, with the user's SSH signing key
   (`git config gpg.format ssh`, `git tag -s`). Development releases stay annotated only. Signing does not change any
   other rule; `pincheck` does not (yet) check a signature (G3): until it does, step 7 of 5.2 includes
   `git tag -v vX.Y.Z` for a safety release, run against the user's `allowed_signers` file.

### 4.2 Safety status of a baseline **[Decided Q-CM1]**

The decision iso-q3 binds the preconditions to "release". `v0.1.0` already exists and does not meet them — it was
made as a reproducible version for customer pins (ARCH-54/55), before this plan. To keep both true, a release tag
states its **safety status** in its message:

| Status | Meaning | May be cited as evidence in a safety case | Preconditions |
|---|---|---|---|
| `development release` | a reproducible, versioned state; the integrator may pin it for development | **no** | P1–P3 and P7–P8 of section 5.1 |
| `safety release` | a state for which the SEooC claim (iso-q2: developed with the methods of part 6 for ASIL B, without the management part) is made | **yes** | **all** of P1–P9 |

The status never changes after tagging. A development release that later passes the reviews does not become a safety
release; the next PATCH release does.

**Alternative, not chosen:** only a state that meets all preconditions gets a `vX.Y.Z` tag at all, and
development states for customer pins use another tag form (for example `vX.Y.Z-dev.N`). That conflicts with ARCH-55
(`VERSION` without suffix, CMake takes numbers only) and with `pincheck`; the user chose the status line (Q-CM1,
2026-09-28).

### 4.3 Existing baselines

The table is checked against `git for-each-ref refs/tags/v*` by [`test/test_cm_plan.py`](test/test_cm_plan.py): every
release tag of the clone has one row with its full commit, and every row a tag.

<!-- baselines:start -->
| Tag | Commit | Tagger, date | Content of the message | Status by 4.2 |
|---|---|---|---|---|
| `v0.1.0` | `ed48460d51422ba2f5e6b76230745d53cd3484d8` (merge `dev` → `master`, `dev` at `0fe50ae9`) | Daniel Reinhardt, 2026-09-28 11:02:59 +0200 | "neXtBSW 0.1.0 – first tagged state, dev 0fe50ae9, Studio v_0.16.25, full run 217/217 green (261 839 test cases), git fsck without finding, compiler xPack GCC 11.3.1" (paraphrased; the message is German) | **development release**: annotated, on `master`, `VERSION` 0.1.0, OpenBSW submodule at `3b6e702`; the CI report is not archived with a hash (P2), the safety documents do not cite it (P4), no qualification reports (P5), no reviews (P6) |
| `v0.2.0` | `2e55d54125a8aaf0d7482baf2ee0caba4d14645f` (merge `dev` → `master`, `dev` at `a128f871`, code state `0d1aea1a`) | Daniel Reinhardt, 2026-10-01 | "neXtBSW 0.2.0 – development release, Studio v_0.22.2, CI run r3 on `73b1f142` 330/331 green (515 724 test cases), only red entry logcheck fixed in `0d1aea1a` and rerun targeted (logcheck alone 25/25), report archived with SHA-256, git fsck without finding, known issues NVM-29 and SYS-150" (paraphrased) | **development release**: annotated, on `master`, `VERSION` 0.2.0, OpenBSW submodule at `3b6e702`; P1 P2 P3 P7 met, P4 formally met (documents carried unchecked, 6.1), P5 P6 P9 not met, P8 not applicable; approved by the user (decision board release-v0-2-0) |
| `v1.0.0-rc1` | `80962d9f1a1d46e641c5181ba9bbf138e0dc6ce9` (merge of the release candidate `r1/rc1-cand` `268b41e6` → `master`, tree `f93bc886`; code freeze `bce1202a`, documentation freeze `039d8bcc`) | Daniel Reinhardt, 2026-10-05 | "neXtBSW 1.0.0-rc1 – Status: development release - no safety claim; candidate 268b41e6, tree f93bc886, OpenBSW submodule 3b6e702, compiler xPack GCC 11.3.1, CI `tools.ci run --strict --slow` on 268b41e6 413/413 entries (539 160 test cases, static analysis 0 new), report `ci-out-v1.0.0-rc1.zip` bound and archived with SHA-256, board SYS-167 on the candidate bce1202a, preconditions, release candidate tag (TOOL-232, TOOL-235), approved by the user (CM-06 record CR-06-1), git fsck without corruption" (paraphrased) | **development release** (Release R1, first release candidate of `VERSION` 1.0.0, TOOL-232): annotated, on `master`, `VERSION` 1.0.0, OpenBSW submodule at `3b6e702`; P1 P2 P3 P7 met, P4 not met (documents carried as working state, CM-06 K06-03 n.a.), P5 P6 P9 not met (D-01, D-21, D-22), P8 export after the tag; approved with conditions by the user (CM-06, record `reviews/records/CR-06-1.md`, `accepted with conditions`, decision board cm06-freigabe-1005) |
<!-- baselines:end -->

**Next candidate, not yet a baseline: `v1.0.0-rc2`** (PLAT-118, prepared by PLAT-118a against `dev` `f4055d8e`). The
user approved on 2026-10-08 the merge of `dev` into `master` and the tag `v1.0.0-rc2` as the cleaned source of the
public repository (transcribed into `doc/todo.yaml`, PLAT-118; P7 for merge and tag). Its row is entered after the
tag (step 9 of 5.2); until then the test accepts the missing row, because the tag belongs to `VERSION` 1.0.0. Planned
status by 4.2: **development release** — to be stated in the status line of the tag message. State of the
preconditions before the tag: P1 open (the changelog section of the candidate), P2 open (release CI run on the tree
that is tagged, report bound with `tools.ci release --tag v1.0.0-rc2` and archived), P3 at the merge, P4 not met
(documents carried as working state, as for `v1.0.0-rc1`), P5, P6 and P9 not met, P8 with the export after the tag.
Public distribution: allowed as a development release without a safety claim (decision of the safety responsible
2026-10-08, E1 b; safety plan section 5 issue 3, released in the delta re-check of CM-01 on 2026-10-08; safety case
D-22); CM-03, CM-08, CM-09 and CM-11 stay bound to the first release with an ASIL B claim, and every recipient receives
the statement "no safety claim" with the safety manual - it stands in the `README.md` of the repository and in every
release note (safety plan section 5, C2).
**No release decision for rc2 is recorded:** CM-06 (record `reviews/records/CR-06-1.md`) covers `v1.0.0-rc1` only;
whether CM-06 is performed for rc2 before the tag is the user's decision (pending). Release notes:
[`../release-notes/v1.0.0-rc2.md`](../release-notes/v1.0.0-rc2.md).

`git diff 0fe50ae ed48460` is empty: the merge commit on `master` carries exactly the tree of `dev` at `0fe50ae`.
The release check of `tools/nxconfig/pincheck.py` (`check_tree(release=True)`, the rule behind `tools.ci check
--release`) run on a clean checkout of `v0.1.0` on 2026-09-28 returns one finding, `ok`: *neXtBSW 0.1.0 stands on
the annotated tag v0.1.0, reachable from origin/master, tree clean*. The formal tag rules of section 4.1 (1–3) hold
for it; what makes it a development release are the preconditions of 5.1, not the tag.

### 4.4 Release lines (PLAT-46)

**[Proposal]** An open source export or a licensed line is not a baseline of its own; it is **one baseline plus one
distribution profile** (`tools/distcheck export --profile <p>`). Its identity is "`vX.Y.Z`, profile `open`", and the
export records the full commit hash of the tag it was made from. A licensed overlay carries the same version (the
`overlay-version` check of `pincheck`). How the export is published is PLAT-46's decision, not this plan's.

---

## 5. Release procedure

### 5.1 Preconditions

P1–P3 and P7–P8 are needed for every release, P4–P6 and P9 additionally for a safety release. The source of each
precondition is iso-q3 (SYS-60) unless marked otherwise. The safety plan (section 6) states the same preconditions as
R1 … R6: P1, P3 and P7 = R1, P2 = R2, P4 = R3, P5 = R4, P6 = R5, P9 = R6; P8 has no safety counterpart (SYS-182).

| # | Precondition | How it is checked | Exists today |
|---|---|---|---|
| P1 | The release candidate on `dev` is complete: all tasks meant for the release merged, `CHANGELOG.md` has a section for `vX.Y.Z`, `VERSION` is `X.Y.Z` | the integrator; `tools/nxconfig version` | yes |
| P2 | A **green CI run** on the tree that will be tagged, **with an archived report** | `python -m tools.ci run --strict --clean` (with `--slow` for a safety release, so the mutation runs count); the tree hash of the run equals the tree of the tag (section 7) | the run yes; recording the tree hash no (G1), archiving no (G2) |
| P3 | Repository integrity | `git fsck --full --no-dangling` without finding (CLAUDE.md 2026-09-16); working tree clean | yes |
| P4 | The **safety documents refer to the tag**: every file of CI-08 carries `Baseline: vX.Y.Z` (section 6) | [`test/test_cm_plan.py`](test/test_cm_plan.py) (G5): on a tagged checkout every state line must be `Baseline: v<VERSION>` | the test yes (SYS-94); the lines are `Working state` until the next release |
| P5 | The **qualification reports** exist: tool qualification per [`tool-classification.md`](tool-classification.md) for every tool with TCL > 1 used for the release, and the qualification of pre-existing software per SYS-62 | the safety plan's work-product list (SYS-60) | no |
| P6 | The **reviews are closed**: confirmation reviews of SYS-63 and the inspections of SYS-69 for the documents and units of the release, findings closed | review records under `doc/safety/reviews/`; a record under deviation D-01 (the user, without independence) does **not** count | no (no independent reviewer named, iso-q1; the user reviews under D-01 since 2026-09-30, AN-093) |
| P7 | The user approves the merge to `master` and the tag, with the safety status of section 4.2 | entry in the decision board, transcribed into `doc/todo.yaml` | practice yes, entry format no |
| P8 | For an export of a release line: `tools/distcheck check --profile <p> --references` without error (PLAT-50) | `tools/distcheck` | yes |
| P9 | The safety case (SYS-64) and the safety manual (DOC-19) of the release exist and cite the tag (safety plan R6) | SYS-64's own evidence-link test | no |

### 5.2 Steps

| Step | Action | Command / check | Who |
|---|---|---|---|
| 1 | Freeze the candidate on `dev`: set `VERSION`, close the changelog section, set every CI-08 header to `Baseline: vX.Y.Z` (the tag name is known before the tag exists, so no document has to cite a hash of itself) | `tools/nxconfig version`; test G5 | integrator |
| 2 | Full CI run on the candidate; keep the output folder | `python -m tools.ci run --strict --clean [--slow] --out build/ci-out-vX.Y.Z` | integrator (workstation) or GitHub Actions (`workflow_dispatch` / PR `dev` → `master`) |
| 3 | Integrity | `git fsck --full --no-dangling`; `git status --porcelain` empty | integrator |
| 4 | Approval of merge and tag with safety status | decision board entry | user |
| 5 | Merge to `master` without fast-forward | `git checkout master && git merge --no-ff dev -m "Merge dev in master: neXtBSW X.Y.Z (Studio v_…)"`; then `git diff dev master` must be empty | integrator |
| 6 | Create the annotated tag on the merge commit with the message of section 5.3 | `git tag -a vX.Y.Z -F msg-vX.Y.Z.txt master`; for a safety release `git tag -s` with the SSH key (Q-CM2) | integrator |
| 7 | Prove the tag is a release | `python -m tools.ci check --release` (runs `pincheck` on the tree: annotated, reachable from `master`, clean, `VERSION` equal); `python tools/nxconfig pin projects/<p>/project.yaml --release`; on the tagged checkout `python -m unittest doc/safety/test/test_cm_plan.py` (all state lines `Baseline: vX.Y.Z`); for a safety release `git tag -v vX.Y.Z` | integrator |
| 8 | Publish | `git push origin master vX.Y.Z` — the tag explicitly, never `--tags`, never `--force` | integrator |
| 9 | Archive the CI report of step 2 as a GitHub release asset and in the local archive, for a safety release with the tool archives (section 7, Q-CM3); record the release in `CHANGELOG.md`, on the roadmap page and in the decision board | — | integrator |

A failure at any step stops the release. A tag that is already pushed is not removed (rule 4 of 4.1); the next PATCH
release replaces it.

### 5.3 Tag message

The tag message is the permanent record of the baseline. The line `Status:` is **decided** (Q-CM1): every release
tag carries it, `development release` or `safety release`. The other lines are **[Proposal]**, the integrator's
working template (values as example):

```
neXtBSW 0.2.0

Status: development release        (or: safety release)
Commit: dev <40-hex commit that was merged>, tree <40-hex tree>
OpenBSW: submodule <40-hex commit>
Studio: v_0.17.0
Compiler: xPack GCC 11.3.1 (tools/ci/versions.toml)
CI: <run id or "local">, verdict pass, <n>/<n> entries, <n> test cases, with/without --slow
CI report: sha256 <hash of the report archive> (ci-out-v0.2.0.zip)
Preconditions: P1 P2 P3 P7 met; P4 P5 P6 P9 not met   (a safety release lists all as met)
Approved: user, decision board <item id>, <date>
git fsck: no finding
```

`v0.1.0` predates this template; its message carries the dev commit, the Studio version, the run result, `git fsck`
and the compiler, and lacks tree hash, report hash, status and preconditions (section 4.3).

---

## 6. How safety documents name their baseline

### 6.1 Rule

Every document of CI-08 carries **one** line in its header that says which state its statements are about:

- `Baseline: vX.Y.Z` — the document was checked against that release and is part of it. This is the only form a
  safety release accepts (P4).
- `Working state: dev at <commit> (after vX.Y.Z)` — the document was written between two releases. At the next
  release, step 1 of 5.2 replaces the line with `Baseline: v<next>` after the document has been re-checked against the
  candidate (or the release says in its message that the document was carried unchecked, which excludes a safety
  release).

The existing sentences "written against `dev` at `<hash>`" and the "Update <date> (task, against `dev` at `<hash>`)"
blocks stay as **history**: they say when and against what a part was written. They no longer define the document's
state. Commit hashes in the body (a board record at image `c9a4f31`, an inventory at `19df315`) are evidence of a
past observation and stay as they are.

### 6.2 Why the tag and not the commit

- One name for all documents of a release instead of one hash per document (finding SG7).
- A tag says the state was **released** (reachable from `master`, CI green); a `dev` hash says nothing about that.
- The tag cannot move; a hash can be unreachable after a history rewrite (the project had one, `filter-branch` in
  2026-09, `lizenz-und-release.md` (not published, available on request)).

### 6.3 Inventory of the loose commits and migration

Read at `ae92e89` (`grep` for short hashes in back-quotes). Every neXtBSW commit listed is an ancestor of `v0.1.0`
(`git merge-base --is-ancestor <hash> v0.1.0`), so none is lost and every document can take `Working state: dev at
ae92e89 (after v0.1.0)` today and `Baseline: v<next>` at the next release.

| Document | Hashes cited | Role of the hashes | Migration (by the document's next task or the release step 1; **not** done here) |
|---|---|---|---|
| `concept.md` | `c0beb4d` (issue 1), `fa04ed1` (issue 2), `c9a4f31` (board image) | header: written/checked against; body: evidence | add the baseline line; keep both hashes as history |
| `fmea.md` | `63ea900` (issue 1), `f2e2c43` (SYS-49), `1bbba28` (SYS-40) | header and "Update" blocks; sources | add the baseline line |
| `iso26262-gap.md` | `62a1b53` (analysis), `fa04ed1`, `63ea900`, `f8ee573` (quoted in SG7) | header and sources; SG7 quotes the others | add the baseline line; SG7 is closed by this plan once the lines exist |
| `tool-classification.md` | `f8ee573` (issue 1), `f2e2c43` (SYS-49), `62a1b53` (norm questions) | header and "Update" blocks | add the baseline line |
| `rtos-comparison.md` | `d9d5996`; OpenBSW submodule `3b6e702` | header | add the baseline line; the submodule hash is marked as such already |
| `static-analysis.md` | `19df315` (inventory 2026-09-21) | body: date of an inventory | add the baseline line |
| `hardware-mechanisms.md`, `traceability.md`, `requirements.yaml` | none | — | add the baseline line (`requirements.yaml`: a header comment, or the StrictDoc document field after DOC-21) |
| `cm-plan.md` (this) | `ae92e89` | header | follows the rule from issue 1 |

The documents of the running cloud wave (SYS-60 `safety-plan.md`, SYS-66 `hsi-s32k1xx.md`, SYS-70 `dfa.md`) should
start with the line; the coordinator can add it at the merge if they do not.

### 6.4 Migration done (issue 2, SYS-94)

SYS-94 set the line in every safety document, at `dev` `6f868b5` (2026-09-28). Only the header was touched: the line
is inserted as a paragraph of its own (after the title, or after the header table), nothing else in the documents
changed, and every hash of 6.3 stays in place as history. The commit named in each line is chosen by one rule:

- the commit the document says it was **written against**, when that commit lies after `v0.1.0` and no later task
  changed the document since;
- otherwise `6f868b5`, the `dev` state at which the line was set: the document was written against a commit before
  `v0.1.0` or names none (`concept.md`, `fmea.md`, `iso26262-gap.md`, `tool-classification.md`, `rtos-comparison.md`,
  `static-analysis.md`, `hardware-mechanisms.md`, `requirements.yaml`), or later tasks changed it after that commit
  (`dfa.md` by SYS-91, `safety-plan.md` by SYS-61 and SYS-92, this plan by SYS-94).

`Working state` claims no re-check (6.1): it says between which release and which `dev` state the document was
written. The re-check against the release candidate happens in step 1 of 5.2.

| Document | State line |
|---|---|
| `anomaly-register.md` | `Working state: dev at 65f3dc9 (after v0.1.0)` (set by SYS-77, unchanged) |
| `architecture-safety-analysis.md`, `config-range.md`, `security-interplay.md`, `unit-design.md` | `Working state: dev at 65f3dc9 (after v0.1.0)` |
| `hsi-s32k1xx.md` | `Working state: dev at ae92e89 (after v0.1.0)` |
| `safety-case.md` | `Working state: dev at 20b2e04 (after v0.1.0)` (set by SYS-64, unchanged; its test ties it to the increment `next`) |
| `sw-architecture.md` | `Working state: dev at 20b2e04 (after v0.1.0)`; the header table row `Baseline` (a `dev` hash, not a tag) is renamed `Built against` so the word keeps the meaning of 6.1 |
| `coexistence.md` | `Working state: dev at 65f3dc9 (after v0.1.0)`; its header table row `Baseline` is renamed `Built against` for the same reason |
| `cm-plan.md`, `concept.md`, `dfa.md`, `fmea.md`, `hardware-mechanisms.md`, `iso26262-gap.md`, `rtos-comparison.md`, `safety-plan.md`, `static-analysis.md`, `tool-classification.md` | `Working state: dev at 6f868b5 (after v0.1.0)` |
| `requirements.yaml` | `# Working state: dev at 6f868b5 (after v0.1.0)` as a header comment (a YAML file has no prose header; the StrictDoc document field replaces it after DOC-21) |
| `traceability.md` | **none**: the file is generated by `tools/trace matrix` and `safety-trace` fails on any hand edit. It is exempt in the test as long as the document register lists it as `generated`; the generator should print the line of `requirements.yaml` (proposal to the coordinator, `tools/trace` is outside SYS-94) |

### 6.5 Consistency test (G5)

[`test/test_cm_plan.py`](test/test_cm_plan.py) checks, on every run:

1. every file directly under `doc/safety` (`*.md`, `*.yaml`) except the generated ones has **exactly one** state line,
   and it lies in the header (before the first `## ` heading or `---` rule; for YAML in the leading comment block);
2. the line has one of the two forms of 6.1, nothing else on it;
3. `Working state`: the commit resolves, is an ancestor of `HEAD`, and the named release is the **newest** release
   whose content the commit contains; `Baseline`: the tag exists and is not newer than `VERSION`;
4. on a checkout that carries the release tag `v` + `VERSION` (step 7 of 5.2) every line is `Baseline: v<VERSION>`;
5. the table of 4.3 lists exactly the release tags of `git for-each-ref refs/tags/v*`, each with its full commit;
6. the exempt generated files are exactly the rows of the document register (`anomaly-register.md` section 6) with
   status `generated`.

The git checks (3 to 5) are skipped with a message when the clone is shallow or has no release tags; the text checks
always run. Counter-checks on broken copies show that each rule sees its fault.

---

## 7. Archiving of baselines and records

| What | Where | Retention | Bound to the baseline by |
|---|---|---|---|
| All items in git | `origin` (GitHub, private) and the workstation clone | permanent; tags and `master` never rewritten | the tag |
| CI report of the release run (the whole `--out` folder, zipped) | **[Decided Q-CM3]** as an asset of a GitHub release `vX.Y.Z` on `origin`, plus a copy in a local archive folder outside the repository | permanent | SHA-256 in the tag message; `tree` in the report (G1) |
| GitHub Actions artifacts of other runs | GitHub | 14 days (`.github/workflows/ci.yml`, `retention-days: 14`) | none — they are working records, not release evidence (G2) |
| External tool archives | download URLs in `versions.toml`; the tool cache | as long as the URL lives | SHA-256 in `versions.toml`. **[Decided Q-CM3]** for a safety release, the archives of ARM GCC, CMake and Ninja are copied into the local archive next to the report, because a vanished URL would make the baseline unreproducible |
| Board records | `tools/boardtest/captures/**` in git | permanent | the commit (they are CI-11); a board record cites the image CRC and the commit it was built from |
| Decisions | `doc/todo.yaml`, `CHANGELOG.md` | permanent | the commit |

Which report files a release needs and what makes it "green" per verification level is SYS-67's to define; this plan
only requires that the release report is archived and bound to the tag.

### 7.1 Reproducing a baseline

The procedure the plan promises (8-7.4.4); not executed for this issue:

```sh
git clone <origin> neXtBSW && cd neXtBSW
git checkout vX.Y.Z && git submodule update --init
git describe --exact-match   # must print vX.Y.Z
python -m tools.ci setup --python-packages
python -m tools.ci run --strict --clean --release --out build/ci-repro
```

Expected: verdict pass, the same entries and test cases as the archived report, and the same artefact sizes. Whether
the images are **byte-identical** across machines is not proven: individual tasks recorded a byte-identical demo image
before and after their change on the same workstation (for example ARCH-55), not across machines and operating
systems. Until it is proven, the image CRC of a board record is valid for the image that was flashed, and a rebuilt
image is compared by its CRC before it is used as that evidence.

---

## 8. Changes and their base configuration

Change management itself is `doc/todo.yaml` (task IDs, `done_when`, owner lines with dates, the decision board) and
will get the safety impact field of TOOL-57. This plan adds only the link to configuration management (8-8.4.2: a
change request names its base configuration):

- The **base configuration of a change** is the `dev` commit its branch started from. git records it
  (`git merge-base dev agent/<ID>`); the integrator writes it into the merge commit message or the changelog entry when
  it matters for a safety document ("against `dev` at …").
- A change that touches a CI-08 document sets that document to `Working state: dev at <commit> (after vX.Y.Z)`.
- A change of CI-06 (tool pins) requires a full CI run (versions.toml header) and is named in the next release message.
- A submodule update of `external/openbsw` is a task of its own, never a side effect.

---

## 9. Status accounting

What anyone can ask the repository, without a tool of its own:

| Question | Command |
|---|---|
| Which baselines exist? | `git for-each-ref refs/tags/v* --format='%(refname:short) %(objecttype) %(*objectname) %(taggerdate:iso)'` |
| Is this checkout a release? | `python -m tools.ci check --release`, or `python tools/nxconfig pin <project> --release` |
| What changed between two baselines? | `git log --oneline vA..vB`; `CHANGELOG.md` sections |
| Is a cited commit in a baseline? | `git merge-base --is-ancestor <hash> vX.Y.Z` |
| Which OpenBSW does a baseline use? | `git ls-tree vX.Y.Z external/openbsw` |
| Which tool versions? | `git show vX.Y.Z:tools/ci/versions.toml` |

---

## 10. Gaps found and proposed tasks

| ID | Gap | Where | Proposed fix | Task |
|---|---|---|---|---|
| G1 | The CI report records the path of the tree, not its commit or tree hash; `summary.md` names the tag only as text. An archived report cannot be proven to belong to a tag | `tools/ci/ci.py` (`finish`, payload `tree`) | add `commit`, `tree_hash`, `describe` and `dirty` to `ci-result.json` and the summary | SYS-67 (touches `tools/ci`) |
| G2 | CI artifacts are kept 14 days; no release report is archived | `.github/workflows/ci.yml` | release asset per tag (section 7); optionally a workflow on tag push that attaches the report | SYS-67 |
| G3 | Tags are not signed, `pincheck` does not verify a signature | `v0.1.0`; `tools/nxconfig/pincheck.py` | **decided (Q-CM2):** SSH signing from the first safety release on (4.1 rule 5); until the check exists, `git tag -v` in step 7. Open: a `pincheck` finding `tag-unsigned` when the tag message says `safety release` (error with `--release`) | new task (touches `tools/nxconfig`, outside SYS-94); needed before the first safety release |
| G4 | `master` carries the merge commits, `dev` does not merge them back (at `ae92e89`: `dev` has 20 commits after `0fe50ae`, `master` has 1 merge commit that is not in `dev`) | history | no fix needed: content is identical (`git diff 0fe50ae ed48460` empty); the rule "master only by merge from dev" (3.1) keeps it so. A check `git diff dev master` = empty right after each release merge is step 5 | — |
| G5 | No test that every CI-08 document carries a baseline line and that at a release all lines name `v` + `VERSION` | `doc/safety/test/` | **done (SYS-94):** [`test/test_cm_plan.py`](test/test_cm_plan.py), rules in 6.5 | SYS-94 |
| G6 | The safety documents do not carry the baseline line yet | `doc/safety/*` | **done (SYS-94):** migration of 6.4; open only `traceability.md` (generated, proposal to `tools/trace`) | SYS-94 |
| G7 | The tag message of `v0.1.0` lacks status, tree hash and report hash | `v0.1.0` | none (immutable); the next release uses the template of 5.3 | — |
| G8 | Byte-identical rebuild across machines not proven | section 7.1 | one reproduction of `v0.1.0` on the Linux runner and on the workstation, image CRCs compared | SYS-67 or new |

---

## 11. Open questions for the decision board

All four were decided by the user on 2026-09-28 as recommended (decision board `q-cm-plan`) and are worked in by
issue 2 (SYS-94).

| # | Question | Recommendation | Decision (user, 2026-09-28) | Where worked in |
|---|---|---|---|---|
| Q-CM1 | Accept the **safety status line** (development release / safety release) in the tag message, so `v0.1.0` stays a valid release without a safety claim (section 4.2)? | yes | yes: status line in every tag message | 0, 4.2, 5.3 |
| Q-CM2 | **Sign** release tags (SSH signing key of the user), and from which release on? | yes, from the first safety release; development releases annotated only | yes: SSH signature from the first safety release | 0, 4.1 rule 5, 5.2 steps 6-7, G3 |
| Q-CM3 | Archive the release CI report as a **GitHub release asset** plus a local copy, and for a safety release the external tool archives too (section 7)? | yes | yes | 0, 5.2 step 9, 7 |
| Q-CM4 | Accept the SemVer meaning for interfaces of section 3.2? | yes | yes | 0, 3.2 |

---

## 12. Clauses addressed

| Gap row | Clause | Before | After this issue | What is still missing |
|---|---|---|---|---|
| 8-12 | 8-7.4.1 | missing | plan written: items, identification, baselines, release procedure | acceptance by the user |
| 8-13 | 8-7.4.3 | partial | baseline defined; one tag exists; outside-git items bound by hash | G1, G2 (report bound and archived), G8 |
| 8-14 | 8-7.4.4, 7.4.5 | partial | when items are identified (every commit; baseline per release) and how a baseline is reproduced (7.1) | the reproduction has not been executed (G8) |
| 2-12 | 2-6.4.7.2 | partial | safety work products are CI-08; issue 2: every one carries its state line, checked by a test | `traceability.md` (generated); `Baseline` lines at the first release after this plan |
| 8-16 | 8-8.4.2 | partial | base configuration of a change defined (section 8) | TOOL-57 for the safety impact |
| 8-22 | 8-9.4.3 | partial | report archived per release (section 7) | SYS-67 |
| SG7 | — | open | rule and migration list (section 6); issue 2: migration done (6.4) and tested (6.5) | `Baseline: vX.Y.Z` at the next release |

## 13. Review questions of the architecture (section 11)

SYS-68 changed no code, no generator, no configuration: one new document. SYS-94 (issue 2) adds one host test for
documents and one header line per safety document; no code, generator, configuration or board data. **Board without an SBC, other crystal,
other derivative, several CAN buses, several tasks:** nothing in this plan depends on a board or a derivative; the
configuration items are named by path class (`boards/*`, `projects/*`, `mcudb/**`), not by the eval board. **New
values in one configuration level:** none introduced. **Host test with its own profile:** none needed for a process
document; the consistency test G5 is proposed. **Qt-free core, YAML paths:** not touched. **Device driver
granularity, platform rule:** not applicable.

## 14. Sources

- Decisions of the user of 2026-09-22 (iso-q2, iso-q3) as recorded in `doc/todo.yaml` (SYS-60, SYS-68) and
  [`customer-project.md`](../architecture/customer-project.md); decisions on branches, merges, pushes and the single
  coordinator in `CLAUDE.md`.
- `iso26262-gap.md` (not published, available on request) rows 2-12, 8-12 … 8-24, section 6 task 10, finding SG7; ISO 26262-2 and -8
  (2018) cited by part and clause only.
- Repository at `ae92e89`: `VERSION`, `tools/nxconfig/{version,pincheck}.py`, `tools/ci/{README.md,ci.py,versions.toml,
  pipeline.toml}`, `.github/workflows/ci.yml`, `tools/distcheck/README.md`, `tools/openbswsync/copies.yaml`,
  `tools/boardtest/captures/`, `doc/safety/*`, `doc/architecture/{lizenz-und-release,customer-project}.md`,
  `CONTRIBUTING.md`.
- Decision board `q-cm-plan` (user, 2026-09-28): Q-CM1 … Q-CM4 as recommended, as recorded in `doc/todo.yaml`
  (SYS-94).
- Tag `v0.1.0` and history read with `git for-each-ref`, `git cat-file -p v0.1.0`, `git merge-base --is-ancestor`,
  `git diff 0fe50ae ed48460`, `git ls-tree v0.1.0 external/openbsw`.
