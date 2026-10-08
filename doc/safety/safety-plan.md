# neXtBSW safety plan — software SEooC at ASIL B (SYS-60)

| | |
|---|---|
| Title | Safety plan - software SEooC at ASIL B |
| Issue | **3, 2026-10-08** (SYS-207, the deviation for release R1 restated in section 5 after the decision of the safety responsible of 2026-10-08); issue 2: 2026-10-02 (SYS-182, after the confirmation review CR-01-1); issue 1: 2026-09-28 (SYS-60) |
| Author | SYS-60 (issue 1), SYS-182 (issue 2), SYS-207 (issue 3) — agents writing on the user's behalf, author side; the updates in between by the tasks of section 12 |
| Approver | the safety manager (the user): issue 1 released 2026-10-02 with the review set of 15 safety documents; issue 2 released 2026-10-02 in the re-check of CM-01 (findings F-01 … F-08 of `reviews/records/CR-01-1.md` closed by the reviewer, the user under D-01, without independence); issue 3 released 2026-10-08 in the delta re-check of CM-01 (delta review of section 5, `reviews/README.md` section 4; record `reviews/records/CR-01-1.md`, points C1 … C3, the user under D-01, without independence) |
| Status | issue 3 **accepted** (released 2026-10-08 in the delta re-check of CM-01, record CR-01-1; D-01, without independence); issue 2 accepted (D-01, without independence) |
| Change history | section 12 |

Working state: dev at c0928380 (after v1.0.0-rc1)

Issue 1 was written against `dev` at `ae92e89`; issue 2 checks the statuses against `doc/todo.yaml` and the tree at
`dev` 3405bf72 (merge of `integ/night12`); issue 3 changes only the deviation for release R1 in section 5 (the
condition that lifts it), written against `dev` 9bf2d520; the activity statuses of section 4 were not re-checked. The
delta re-check of CM-01 on 2026-10-08 added the sentence on `README.md` and the release notes in section 5 (C2) and
released issue 3 (C3), recorded against `dev` c0928380.
Readers: the user as project manager and safety manager, the coordinator who plans and merges the tasks named here,
the agents of the safety tasks (SYS-61 … SYS-77, DOC-19, DOC-21, DOC-22, PLAT-83, PLAT-91, TOOL-40, TOOL-57, TOOL-58),
and later an integrator or reviewer who wants to know how neXtBSW was developed.
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**What this document is.** The safety plan that the gap analysis `iso26262-gap.md` (not published, available on request) found missing
(rows 2-09 and 2-11, proposed task 1). It states the claim neXtBSW makes, names the people responsible, tailors the
safety lifecycle for a software Safety Element out of Context (SEooC), lists every activity of parts 2, 6, 8 and 9 of
ISO 26262:2018 with objective, responsible role, work product and status, plans the confirmation measures with their
independence and defines what a release is. The consistency test
[`test/test_safety_plan.py`](test/test_safety_plan.py) checks that every work product named here exists or has a task.

**Licence rule.** The standard is cited by **part and clause only** (for example "6-9.4.4" = ISO 26262-6:2018,
clause 9.4.4). No text, table or figure of the standard is reproduced; requirements are restated in our own words.

**What it is not.** Not a safety case (SYS-64), not a safety manual (DOC-19), not a statement that neXtBSW complies.
The plan says what is done, by whom and with which result; the evidence is in the work products it names.

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Claim (user decision iso-q2, 2026-09-22) | neXtBSW is **developed following the methods of ISO 26262-6 at ASIL B, without the management part** — a software SEooC with ASIL B capability, stage 1 (base-software mechanisms, concept section 1.4) |
| Openly stated limitations | no quality management system (2-5.4.5), no evidence of competence (2-5.4.4), no confirmation review by an independent person (2-6.4.9, 2-6.4.10): since 2026-09-30 the user performs the confirmation measures himself (AN-093, T-11), without independence; they go into the safety manual (DOC-19) as assumptions of use (AoU-35) |
| Roles (section 2) | project manager and safety manager: **the repository owner** (user, GitHub `danielsimonreinhardt`), confirmed by the user on 2026-09-28 (`q-sys92-rollen`); coordinator session plans and merges; agents author; confirmation reviewer for now **the user himself** (decision 2026-09-30, AN-093, T-11 — not independent); an independent reviewer is still to be named (iso-q1) |
| Tailoring (section 3) | 11 tailoring decisions with rationale (2-6.4.5.7) — ten tailorings of the lifecycle and one **deviation** (T-11), listed in the same table so that every activity names its ID: item-level work (parts 3, 4, 5, 7, hardware metrics, item impact analysis) goes to the integrator through assumptions; QMS and competence are not provided; the development interface agreement, the evaluation of hardware elements, proven in use and ASIL decomposition are not used; the functional safety assessment is deferred; the confirmation measures are performed by the user himself without independence — a deviation, lifted when an independent person is named (T-11, 2026-09-30) |
| Activities (section 4) | **53** activities of parts 2 (16), 6 (22), 8 (11) and 9 (4), covering all **97** gap rows 2-01 … 9-11 of `iso26262-gap.md`. Status: 15 done, 24 partial, 6 in progress, 1 planned, 7 tailored (issue 1 at the review: 9 done, 21 partial, 9 in progress, 7 planned) |
| Confirmation measures (section 5) | 11 measures; 5 of them need independence I1 at ASIL B (safety plan, safety analyses, DFA, integration and test strategy, safety case), the other 6 are planned at I1 by project choice; all 11 prepared by SYS-63; since 2026-09-30 performed by the user himself (SYS-118, T-11) — a confirmation **without independence**, which gives no working state an ASIL statement; **1 performed**: CM-01 on 2026-10-02 (record `reviews/records/CR-01-1.md`, accepted with conditions, 8 minor findings corrected by SYS-182) |
| Release (section 6, user decision iso-q3) | a release is an **annotated tag `vX.Y.Z` on `master`**; before it: green CI run with archived report, safety documents citing the tag, qualification reports, closed confirmation reviews. Tags that miss a precondition are a **development release** without safety claim; R1 … R6 map one to one onto the preconditions P1 … P9 of `cm-plan.md` 5.1 (section 6) |
| New tasks proposed (section 8) | 11: interplay safety/cybersecurity (N1), unit design of watchdog and hard-fault handler (N2), configuration range argument (N3), confirmation of the roles on the decision board (N4); after the tasks SYS-62, SYS-63, SYS-74 and TOOL-57 were booked done with parts open (DOC-35): performing the confirmation measures (N5), verification inspections with record (N6), service commands in the product image (N7), further qualification reports (N8), use of the safety-impact field (N9); after SYS-65 and SYS-97 were booked done with the resource estimate open (SAFETYDOC-1001): the rest of the resource estimate (N10). **These ten are carried into tasks** (N1 … N4 SYS-92 and N7 SYS-119 done; N5 SYS-118, N6 SYS-120, N8 SYS-121, N9 SYS-122, N10 DOC-41 open); sections 4 to 6 name the task, not the proposal. Issue 2 adds N11, the unit design of the remaining safety units (U1 of `unit-design.md`), not yet carried |
| Verification | **mock/host**: the consistency test `doc/safety/test/test_safety_plan.py` (parser, cross-checks against `iso26262-gap.md`, `doc/todo.yaml`, the repository tree, `cm-plan.md` 5.1 and the review records, and counter-checks with broken copies); since issue 2 it also reports a lagging plan — a proposal still named after a task carried it out, a `planned` row whose task has started and whose work product exists, a measure whose status does not match its record (section 7). No hardware, no code change |

---

## 1. Scope and claim

### 1.1 The element

neXtBSW as described in [`concept.md`](concept.md) section 1: the base software (MCU drivers, safety mechanisms of
the base software, RTE, generators) as a **software component SEooC** (10-9.2.4) with **ASIL B capability** (10-9.1).
Stage 1 argues ASIL B with the mechanisms of the base software only (concept 1.4); the device drivers' own mechanisms
are stage 2 and not covered by this issue. The item, its hazard analysis, its safety goals, its hardware and the
application software are outside the element (concept 1.2, AoU-01, AoU-09, AoU-17).

### 1.2 The claim

<!-- claim:start -->
**neXtBSW is developed following the methods of ISO 26262-6 at ASIL B, without the management part.**
<!-- claim:end -->

Decided by the user on 2026-09-22 (decision board, iso-q2). What it means:

- **Parts 6, 8 and 9** are applied at ASIL B for the element, with the tailoring of section 3. Where a method is not
  applied the rationale is given in the work product (6-4.3 reading).
- **Part 2** is applied as far as a single-person open-source project can apply it: a safety plan (this document),
  roles, tailoring, safety case, confirmation measures and a release procedure. What cannot be provided is stated,
  not assumed:
  - **no quality management system** in the sense of 2-5.4.5;
  - **no evidence of competence** of the persons in the lifecycle in the sense of 2-5.4.4 — most work is written by
    agents on the user's behalf and reviewed by the coordinator and the user;
  - **no independent confirmation review yet** (2-6.4.9, 2-6.4.10): by the decision of the safety responsible of
    2026-09-30 (AN-093, tailoring T-11) the user performs the confirmation measures of section 5 himself for now.
    That is a confirmation **without independence**: its records find and close findings, but no development state
    gets an ASIL statement from them, and no release may claim ASIL B capability until an independent person has
    reviewed it.
- The integrator who uses neXtBSW in a safety-related item takes over these gaps under his own management of
  functional safety; the safety manual (DOC-19) states them as assumptions of use (gap analysis proposal C8).

---

## 2. Roles

Appointment of project manager and safety manager according to 2-6.4.2. The table is the reference for the column
"Responsible" of sections 4 and 5; the consistency test rejects any other value there.

<!-- plan-roles:start -->
| Role | Held by | Responsibility and authority |
|---|---|---|
| project manager | the repository owner (user, GitHub `danielsimonreinhardt`) | scope, priorities and resources of neXtBSW; decides on the decision board; approves merges to `master` and every release (section 6) |
| safety manager | the repository owner (user, GitHub `danielsimonreinhardt`) | owns this plan and the safety case; decides on tailoring, on the safety claim and on the closure of safety anomalies; confirms the ASIL-B relevance of changes |
| coordinator | the coordinator session (one at a time, CLAUDE.md "Nur eine Sitzung schreibt Planung und Merges") | turns this plan into tasks in `doc/todo.yaml`, writes the agent briefs, re-runs tests and builds, merges to `dev`, keeps the status columns of this plan up to date |
| author | the agent or person who writes a work product (branch `agent/<ID>` or `claude/<id>`) | produces the work product with its verification and changelog fragment; never reviews its own work |
| confirmation reviewer | for now **the user himself** (decision of the safety responsible 2026-09-30, AN-093, tailoring T-11); an independent person is still to be named (iso-q1, user 2026-09-22: "Reviewer spaeter klaeren") | performs the confirmation reviews of section 5; planned at independence I1 (a person other than the author and other than the user as author-side approver), performed by the user without independence until that person is named; eligibility, the deviation D-01 and signature: `doc/safety/reviews/README.md` section 3 |
| integrator | the organisation that builds an item on neXtBSW (outside the project) | item-level work handed over by the tailoring of section 3; validates the assumptions of use at integration (2-6.4.5.7, 10-9.2.4.5) |
<!-- plan-roles:end -->

**Confirmation of the appointment (N4, done).** The user confirmed the appointment of project manager and safety
manager on the decision board, entry `q-sys92-rollen` (opened by SYS-92 on 2026-09-28, answered "bestaetigen" by the
user on 2026-09-28). The table above is the user's word, not a proposal of the plan. *Issue 2 (SYS-182, finding F-04
of CR-01-1): issue 1 still called the entry open and dated it 2026-09-30.*

**One person, two roles.** Project manager and safety manager are the same person. The standard allows that; it
requires that the responsibility is assigned and that the person has the authority (2-6.4.2). The independence that
matters at ASIL B is the one of the confirmation reviews (section 5), not the one between these two roles.

**The user as approver is author side.** Work products are written by agents on the user's behalf. The user's
acceptance on the decision board (`abn-sys19`, `abn-plat75`, …) is a real review, but it is a review by the
commissioning side, not an independent one. This plan therefore counts it as **verification review** (part 8-9), not
as confirmation review. Whether the user wants to see this differently is part of iso-q1. *2026-09-30 (AN-093):* the
user decided to perform the confirmation measures himself for now; this plan records it as the deviation T-11, not as
independence — his records stay author side (first: CR-01-1, 2026-10-02).

---

## 3. Tailoring of the lifecycle for the SEooC

2-6.4.5.1 requires every tailoring to be defined in the safety plan with a rationale; 2-6.4.5.7 says for an SEooC
that the requirements are derived from assumptions on intended use and context, including the external interfaces,
and that the assumptions are validated at integration. The tailoring below applies these two clauses. A tailoring
does not remove the objective: where an activity moves to the integrator, the safety manual (DOC-19) carries the
assumption that makes the hand-over explicit.

T-01 … T-10 are tailorings of the lifecycle. **T-11 is not a tailoring but a deviation** from a requirement that
applies at ASIL B (the independence of the confirmation measures); it stands in the same table because every activity
and every document names it by this ID, and it says what lifts it.

<!-- plan-tailoring:start -->
| ID | Clause(s) | Tailoring | Rationale | Consequence |
|---|---|---|---|---|
| T-01 | 2-6.4.5.7, 10-9.2.4.2, 10-9.2.4.3 | Item definition, hazard analysis and risk assessment, functional and technical safety concept (parts 3 and 4) are **not performed**; they are replaced by assumed safety goals (ASG-01 … ASG-07) and assumptions of use (AoU-01 …) in `concept.md`, from which the software safety requirements are derived | an SEooC is developed without a known item; the standard's guideline for this case is to assume the higher-level requirements and to validate the assumptions at integration | the integrator compares ASG and AoU with his own safety concept (AoU-02); the level of assumed technical safety requirements between ASG and SSR is stated in `safety-manual.md` section 3.2 (gap proposal C2, DOC-19) |
| T-02 | parts 5 and 7, 5-8, 5-9 | Hardware development, hardware architectural metrics, evaluation of random hardware failures and production are **not performed** | the element is software; hardware and production belong to the item; the element contributes per mechanism what the integrator's metrics need (gap section 3) | AoU-09; the re-based coverage table per mechanism goes into the safety manual (DOC-19, gap proposal M2) |
| T-03 | 2-5.4.4, 2-5.4.5 | **No quality management system** and **no evidence of competence** are provided | a single-person open-source project has neither; the user decided (iso-q2) to claim the part-6 methods without the management part instead of claiming what cannot be shown | stated in the claim (section 1.2) and as assumption of use in the safety manual (gap proposal C8); the integrator's own QMS covers the integration |
| T-04 | 2-6.4.3 | The **impact analysis at item level** is not performed | there is no item; for a changed element version the integrator decides at item level | AoU-02; the element supplies a change history per release (CHANGELOG, section 6) |
| T-05 | 8-5 | **No development interface agreement** | no customer during SEooC development; the safety manual carries the interface to the integrator | an integrator who wants a development interface agreement negotiates it on the basis of DOC-19 |
| T-06 | 8-13 | **No evaluation of hardware elements** | hardware is outside the element (AoU-09); MCU, SBC and board are chosen by the integrator, so the element has no hardware element of its own to evaluate | the integrator evaluates MCU, SBC and board; the MCU's safety manual (N2 of `fmea.md`) is his input |
| T-07 | 8-14 | **Proven in use is not used** as an argument for any component | there is no field data; the route would need operating hours we cannot show | pre-existing software takes the qualification route of 8-12 or is developed per part 6 (SYS-62) |
| T-08 | 9-5 | **No ASIL decomposition** at stage 1 | all stage-1 requirements are ASIL B and implemented on one MCU; decomposition is kept for a later ASIL C/D stage (concept 2.1) | the integrator must not decompose into neXtBSW elements without his own analysis |
| T-09 | 2-6.4.12 | The **functional safety assessment** is deferred: not planned for issue 1 | it is only recommended at ASIL B and needs an independent assessor — the same blocker as the confirmation reviews (iso-q1); it adds nothing before those reviews exist | reconsidered by the safety manager before the first release that claims ASIL B capability; the integrator may assess the element within his item assessment |
| T-10 | 6-11.4.1, 6-11.4.4 | Tests of the embedded software run on the **reference eval board** and the host, not on the integrator's ECU; the admissible configuration range is argued for the configurations the project builds, not for all | the target ECU is unknown for an SEooC; the element can only be tested on representative hardware and configurations | the integrator repeats the target tests on his ECU and verifies his configuration data set (AoU-26, 6-C.4.5); the range argument is `config-range.md` (SYS-92, proposal N3) |
| T-11 | 2-6.4.9, 2-6.4.10 | **Deviation, not a tailoring:** the **confirmation measures** of section 5 are performed **for now by the user himself**, not by a person independent of the author side — a **deviation from the required independence** (I1, required at ASIL B for CM-01 … CM-05). **Lifted when** the safety manager names an independent person (iso-q1); from then on every measure performed under this deviation is reviewed again at I1 (run k+1, full scope) | decision of the safety responsible of 2026-09-30 on the escalated anomaly AN-093: "Die Prüfungen (Bestätigungsmaßnahmen) führt vorerst der Nutzer selbst durch"; no independent person is named (iso-q1) and the reviews shall not wait for one | the records of these reviews are confirmations **without independence**: they find and close findings, but they give **no development state an ASIL statement** and do not meet the release precondition R5 for a safety release; every tag stays a development release until an independent person has reviewed the work products (SYS-118, AoU-35). **Who carries it until then:** the safety manager, who keeps iso-q1 open, and the integrator, who takes over the missing independent confirmation under his own management of functional safety (AoU-35); no date is set — no release that claims ASIL B capability is planned (R5). Procedure and record rules: `doc/safety/reviews/README.md` section 3 (deviation D-01) |
<!-- plan-tailoring:end -->

What is **not** tailored: part 6 is applied at ASIL B in full; the software safety requirements, the architecture,
the unit design and verification, the integration and the target tests stay required activities of the element.
Neither are the safety case, the confirmation reviews and the release procedure — they are planned (sections 4–6);
the reviews are performed without independence until T-11 is lifted.

Decisions that shape the plan without being tailoring:

- **Coexistence** (iso-q4, user 2026-09-22): QM parts stay QM only with a freedom-from-interference argument per
  module (9-6); the separating element (RTOS, MPU code) must be ASIL B (6-7.4.9). Tasks SYS-61, SYS-21. Worked out
  in [`coexistence.md`](coexistence.md) (SYS-61): 21 of 36 modules are ASIL B (13 mechanisms, 3 separating
  elements, 5 dependencies); of the 9 QM modules with code in the image only XCP is fully argued today, the other 8
  count as ASIL B until SYS-21 and the findings CX-1 … CX-3 close their open rows.
- **FreeRTOS route** (iso-q5, user 2026-09-22): **decided** on 2026-09-22 (decision `rtos-u1-anfrage`), after the
  comparison `rtos-comparison.md` (SYS-78, done) and with the hardening of the OS boundary (SYS-80, done): neXtBSW
  stays on FreeRTOS and qualifies it. The route is carried out by SYS-62 (plan and report QR-01, done) and the
  FreeRTOS qualification package SYS-124 (rows P6-07 and P8-09); open point O2 is closed (AN-094).
- **Single exit** (iso-q6, user 2026-09-22): MISRA C:2012 rule 15.5 is re-enabled for the safety units. Task PLAT-91.

---

## 4. Activities per part

One row per activity. Columns:

- **Gap rows** — the rows of `iso26262-gap.md` sections 2.1 to 2.4 the activity answers; every gap row of parts 2,
  6, 8 and 9 appears at least once (checked).
- **Responsible** — a role of section 2.
- **Work product** — repository paths; `—` only for a tailored activity. A path that does not yet exist must have an
  open task or a proposal of section 8 in the column "Task" (checked).
- **Task** — task IDs of `doc/todo.yaml`, proposals `N<n>` of section 8, or tailoring IDs `T-nn` of section 3.
- **Status** — `done` (the work product exists and meets the objective for stage 1), `partial` (exists, a named part
  missing), `in progress` (a task is running), `planned` (a task exists, not started), `tailored` (section 3).

<!-- plan-activities:start -->
| ID | Clause | Activity | Objective | Gap rows | Responsible | Work product | Task | Status |
|---|---|---|---|---|---|---|---|---|
| P2-01 | 2-5.4.2 | Rules and processes for functional safety | written rules that name the lifecycle, the roles and the work products, and that are followed in every task | 2-01 | safety manager | `CLAUDE.md`, `CONTRIBUTING.md`, `doc/safety/safety-plan.md` | SYS-60 | done |
| P2-02 | 2-5.4.2.3 | Interplay of functional safety and cybersecurity | the attack surfaces that can reach safety functions (bootloader update over CAN, UDS, XCP) are listed with their effect on the safety argument | 2-02 | safety manager | `doc/safety/security-interplay.md` | SYS-92 | done |
| P2-03 | 2-5.4.3 | Management of safety anomalies | one register of findings with closure rationale and reviewer; open anomalies escalated to the safety manager | 2-03 | safety manager | `doc/safety/anomaly-register.md` | SYS-77, SYS-63, SYS-118 | partial |
| P2-04 | 2-5.4.4 | Competence of the persons in the lifecycle | the lack of competence evidence is stated to the integrator | 2-04 | safety manager | `doc/safety/safety-manual.md` | T-03, DOC-19 | tailored |
| P2-05 | 2-5.4.5 | Quality management | the lack of a QMS is stated to the integrator | 2-05 | safety manager | `doc/safety/safety-manual.md` | T-03, DOC-19 | tailored |
| P2-06 | 2-6.4.2 | Appointment of project manager and safety manager | both roles assigned with responsibility and authority | 2-06 | project manager | `doc/safety/safety-plan.md` | SYS-60, SYS-92 | done |
| P2-07 | 2-6.4.3 | Impact analysis at item level | handed to the integrator | 2-07 | integrator | — | T-04 | tailored |
| P2-08 | 2-6.4.4 | Impact analysis of reused elements | per pre-existing component the route (qualify, develop per part 6, replace) decided from the change status of each file | 2-08 | safety manager | `tools/openbswsync/copies.yaml`, `doc/safety/component-qualification.md` | SYS-62 | done |
| P2-09 | 2-6.4.5 | Tailoring of the lifecycle | every tailoring defined with rationale | 2-09 | safety manager | `doc/safety/safety-plan.md` | SYS-60 | done |
| P2-10 | 2-6.4.5.7 | SEooC assumptions including the external interfaces | assumed scope, assumed requirements and the external interfaces of the element (supervisor API, RTE, configuration files, HSI) stated for validation at integration | 2-10 | safety manager | `doc/safety/concept.md`, `doc/safety/safety-manual.md` | DOC-19, DOC-38, T-01, T-02 | done |
| P2-11 | 2-6.4.6 | Safety plan | the safety activities planned with objective, responsible, work product and status, updated per phase | 2-11 | safety manager | `doc/safety/safety-plan.md` | SYS-60 | done |
| P2-12 | 2-6.4.7 | Work products under configuration, change and documentation management | every work product of this plan is a configuration item and cited by baseline | 2-12 | coordinator | `doc/safety/cm-plan.md` | SYS-68, SYS-94, SYS-111 | done |
| P2-13 | 2-6.4.8 | Safety case | claim → argument → evidence, incremental per release | 2-13 | safety manager | `doc/safety/safety-case.md` | SYS-64, SYS-100 | partial |
| P2-14 | 2-6.4.9, 2-6.4.10 | Confirmation reviews | the confirmation measures of section 5 prepared, performed at the required independence, findings closed | 2-14 | safety manager | `doc/safety/reviews` | SYS-63, SYS-110, SYS-118, T-11 | partial |
| P2-15 | 2-6.4.11, 2-6.4.12 | Functional safety audit and assessment | audit of the process before the first ASIL-B release; assessment deferred | 2-15, 2-16 | safety manager | `doc/safety/reviews` | SYS-63, SYS-118, T-09 | partial |
| P2-16 | 2-6.4.13 | Release | a release is defined and made only on sufficient evidence (section 6) | 2-17 | project manager | `doc/safety/safety-plan.md`, `doc/safety/cm-plan.md` | SYS-68, SYS-67 | done |
| P6-01 | 6-5.4.1 … 6-5.4.3, 6-5.5.1 | Development environment and guidelines | languages, guidelines (incl. the C++ subset decision), tools with versions and TCL, build and test flow in one place | 6-01, 6-02 | coordinator | `doc/safety/static-analysis.md`, `tools/ci/versions.toml`, `doc/safety/dev-environment.md` | DOC-22, DOC-28, DOC-29 | done |
| P6-02 | 6-6.4.1 … 6-6.4.3, 6-6.4.5 | Software safety requirements | SSR derived from the assumed requirements, covering configuration, HSI, timing and every operating mode; non-safety requirements specified | 6-03, 6-05 | safety manager | `doc/requirements/safety/ssr.sdoc` | SYS-72, DOC-21, DOC-43, SYS-100 | partial |
| P6-03 | 6-6.4.4, 6-6.4.6 | Hardware-software interface | every register, clock, interrupt and pin the stage-1 mechanisms depend on, with expected configuration | 6-04 | author | `doc/safety/hsi-s32k1xx.md` | SYS-66, SYS-93 | in progress |
| P6-04 | 6-6.4.7 | Verification of the SSR and the HSI | structural check automated, content verified by inspection with record | 6-06 | safety manager | `doc/safety/traceability.md`, `doc/safety/reviews` | SYS-72, SYS-63, SYS-120 | partial |
| P6-05 | 6-7.4.1 … 6-7.4.5 | Software architectural design | static and dynamic view of the safety part down to the units, with bidirectional trace to the SSR | 6-07, 6-08, 6-09, 6-10 | author | `doc/architecture/README.md`, `doc/safety/sw-architecture.md` | SYS-65, SYS-97 | done |
| P6-06 | 6-7.4.6, 6-7.4.8, 6-7.4.9 | Allocation, coexistence and partitioning | SSR allocated to components with ASIL; QM modules argued free from interference, the separating element at ASIL B | 6-11, 6-13, 6-14 | safety manager | `doc/safety/sw-architecture.md`, `doc/safety/coexistence.md` | SYS-65, SYS-61, SYS-21 | partial |
| P6-07 | 6-7.4.7 | Pre-existing software | OpenBSW (unchanged), FreeRTOS, newlib, ETL, CMSIS qualified per 8-12 or replaced; modified copies developed per part 6 | 6-12 | safety manager | `doc/safety/component-qualification.md` | SYS-62, SYS-109, SYS-121, SYS-124 | in progress |
| P6-08 | 6-7.4.10, 6-7.4.12 | Safety analysis at the architectural level and error handling | failure propagation of mechanisms and functional software to the SSR analysed; measures applied | 6-15, 6-17 | author | `doc/safety/fmea.md` | SYS-71, SYS-100 | partial |
| P6-09 | 6-7.4.11 | Dependent failure analysis at the architectural level | dependent failures where freedom from interference or independence is relied on | 6-16 | author | `doc/safety/dfa.md` | SYS-70, SYS-95, SYS-195 | in progress |
| P6-10 | 6-7.4.13 | Resource estimate | execution time, memory including worst-case stack, bus load | 6-18 | author | `tools/schedana`, `tools/elfcheck`, `doc/safety/sw-architecture.md` | SYS-65, SYS-97, DOC-41 | partial |
| P6-11 | 6-7.4.14 | Verification of the architectural design | inspection of the architecture with record | 6-19 | safety manager | `doc/safety/sw-architecture.md`, `doc/safety/reviews` | SYS-65, SYS-63, SYS-120 | partial |
| P6-12 | 6-8.4.2 … 6-8.4.4 | Software unit design | every safety unit has a design description consistent with architecture and HSI | 6-20 | author | `doc/safety/unit-design.md`, `platforms/s32k1xx/port/safety/safeSupervisor/doc/safeSupervisor.md`, `platforms/s32k1xx/port/safety/safeLifecycle/doc/safeLifecycle.md`, `platforms/s32k1xx/port/safety/safeMemory/doc/safeMemory.md`, `platforms/s32k1xx/port/safety/safeSystem/doc/safeSystem.md` | SYS-92, N11 | partial |
| P6-13 | 6-8.4.5 | Design and implementation principles | the unit principles applied and gated by static analysis; single exit for the safety units | 6-21 | author | `doc/safety/static-analysis.md`, `tools/misra/rules.yaml` | PLAT-91 | in progress |
| P6-14 | 6-9.4.2, 6-9.4.3 | Unit verification and test case derivation | static analysis, requirement-based and interface tests, code inspection with record, derivation of test cases recorded | 6-22, 6-23 | author | `tools/test/tests.yaml`, `doc/safety/verification-plan.md`, `doc/safety/reviews` | SYS-67, SYS-69, SYS-166, SYS-181 | partial |
| P6-15 | 6-9.4.4 | Unit coverage | requirement coverage and statement/branch coverage measured, thresholds enforced with rationale per shortfall | 6-24 | coordinator | `tools/test/coverage.yaml`, `tools/test/coverage.py` | TOOL-40, TOOL-44 | partial |
| P6-16 | 6-9.4.5, 6-10.4.7 | Test environment differences | difference between host and target (compiler, word sizes, start-up, interrupts) analysed, extra target tests derived | 6-25, 6-32 | author | `doc/safety/verification-plan.md` | SYS-67, SYS-168 | partial |
| P6-17 | 6-10.4.1 … 6-10.4.4 | Software integration and its verification | integration steps and what is verified at each; test cases derived; requirement coverage at architectural level | 6-26, 6-27, 6-28, 6-29 | author | `doc/safety/verification-plan.md`, `doc/safety/traceability.md` | SYS-67, SYS-166, SYS-167, SYS-201 | partial |
| P6-18 | 6-10.4.5 | Structural coverage at the architectural level | function and call coverage measured (recommended at B) | 6-30 | coordinator | `tools/test/coverage.py` | TOOL-58 | planned |
| P6-19 | 6-10.4.6 | Only specified functions in the released software | a product build without test and fault-injection commands, checked in the image | 6-31 | author | `ecu/consoleCommands`, `tools/elfcheck` | SYS-74, SYS-119 | done |
| P6-20 | 6-11.4.1 … 6-11.4.4 | Testing of the embedded software | requirement-based target tests per SSR with operational use cases, repeatable and archived, evaluated against the configuration range | 6-33, 6-34, 6-35, 6-36 | author | `tools/boardtest`, `doc/safety/verification-plan.md`, `doc/safety/config-range.md` | SYS-75, SYS-67, SYS-167, SYS-92, T-10 | partial |
| P6-21 | 6-C.4.1 … 6-C.4.5 | Configuration data | valid values, units and dependencies specified; safety relevance and ASIL marked; verification route for the integrator's data set stated | 6-37, 6-38, 6-39 | author | `doc/reference/project.md`, `tools/nxconfig/checks.py`, `doc/safety/safety-manual.md`, `doc/safety/config-range.md` | DOC-19, SYS-92 | done |
| P6-22 | 6-C.4.6 … 6-C.4.11 | Calibration data | calibration data specified, protected (CRC and layout check) and plausibility-checked | 6-40 | author | `tools/calseg`, `tools/a2lgen` | SYS-90, SYS-76 | done |
| P8-01 | 8-5 | Development interface agreement | not used | 8-01 | project manager | — | T-05 | tailored |
| P8-02 | 8-6.4.1, 8-6.4.2, 8-6.4.3.1, 8-6.4.3.2 | Specification and management of safety requirements | requirements identifiable, with ASIL, allocation, quality characteristics, hierarchy and trace to design and verification | 8-02, 8-03, 8-04, 8-05, 8-06, 8-07, 8-08, 8-09 | safety manager | `doc/requirements/safety/ssr.sdoc`, `doc/safety/traceability.md` | SYS-72, DOC-21, DOC-43, SYS-100 | partial |
| P8-03 | 8-6.4.3.3, 8-6.4.3.4 | Verification of the safety requirements | inspection with record; requirements under configuration management | 8-10, 8-11 | safety manager | `doc/requirements/safety/ssr.sdoc`, `doc/safety/reviews` | SYS-72, SYS-63, SYS-120 | partial |
| P8-04 | 8-7 | Configuration management | CM planned; configuration items and baselines defined; reproducibility of every release | 8-12, 8-13, 8-14 | coordinator | `doc/safety/cm-plan.md` | SYS-68, SYS-94, SYS-67, TOOL-210, TOOL-211, TOOL-212 | partial |
| P8-05 | 8-8 | Change management | change requests with ID, base configuration and a safety impact analysis; decision by authorised role; confirmation reviews updated when safety is affected | 8-15, 8-16, 8-17, 8-18, 8-19 | coordinator | `doc/todo.yaml`, `tools/todo/todoplan.py` | TOOL-57, SYS-122 | partial |
| P8-06 | 8-9 | Verification | verification planned per phase, specified, executed and evaluated with environment and tool versions, archived per baseline | 8-20, 8-21, 8-22, 8-23 | coordinator | `doc/safety/verification-plan.md`, `tools/test/tests.yaml`, `tools/ci/pipeline.toml` | SYS-67, SYS-166, SYS-167, TOOL-209 | partial |
| P8-07 | 8-10 | Documentation management | every safety document with title, author, approver, version, change history and status | 8-24 | coordinator | `doc/safety/anomaly-register.md` | SYS-77, DOC-21, SYS-94 | done |
| P8-08 | 8-11 | Confidence in the use of software tools | use cases classified, TCL derived, qualification where needed | 8-25 | safety manager | `doc/safety/tool-classification.md` | PLAT-83, SYS-88 | partial |
| P8-09 | 8-12 | Qualification of software components | qualification plan and reports for unchanged pre-existing components; modified copies handled as own development | 8-26, 8-27 | safety manager | `doc/safety/component-qualification.md` | SYS-62, SYS-109, SYS-121, SYS-124 | in progress |
| P8-10 | 8-13 | Evaluation of hardware elements | not performed by the element | 8-28 | integrator | — | T-06 | tailored |
| P8-11 | 8-14 | Proven in use argument | not used | 8-29 | safety manager | — | T-07 | tailored |
| P9-01 | 9-5 | ASIL decomposition | not used at stage 1 | 9-01 | safety manager | — | T-08 | tailored |
| P9-02 | 9-6 | Criteria for coexistence | each QM module argued free from interference or raised to ASIL B | 9-02 | safety manager | `doc/safety/coexistence.md` | SYS-61, SYS-21 | partial |
| P9-03 | 9-7 | Dependent failure analysis | potential dependent failures identified from the topics of 9-7, evaluated, measures and rationale, verified | 9-03, 9-04, 9-05 | author | `doc/safety/dfa.md` | SYS-70, SYS-95, SYS-195 | in progress |
| P9-04 | 9-8 | Safety analyses | inductive and deductive analyses to a defined method and objective, results verified | 9-06, 9-07, 9-08, 9-09, 9-10, 9-11 | author | `doc/safety/fmea.md` | SYS-71, SYS-63, SYS-118 | partial |
<!-- plan-activities:end -->

Remarks on the status column:

- **P2-06 done**: the roles are written down here and the user confirmed them on 2026-09-28 (`q-sys92-rollen`, N4
  carried out by SYS-92).
- **P2-16 done**: the release procedure (section 6) is fixed by iso-q3 and worked out in `cm-plan.md` (SYS-68, SYS-111
  for the signature check, SYS-67 for the bound and archived report); the open tool work of a release (archives,
  reproduction, asset workflow) is carried by P8-04.
- **Issue 2 (SYS-182, CR-01-1 F-06)** re-checked every row against `doc/todo.yaml` and the tree at `dev` 3405bf72:
  the proposals N1 … N10 are replaced by the tasks that carry them (section 8); P2-02, P2-12, P2-16, P6-19, P6-21 and
  P8-07 are `done` because their tasks are done and their work products exist; P6-06, P6-11, P6-16, P8-03 and P2-15
  moved from `planned` to `partial` because their work products exist in part (coexistence argument, inspection record
  of the architecture in `sw-architecture.md` section 9, host/target analysis HT-1 … HT-9 of `verification-plan.md`,
  inspection INS-SSR-01 in `ssr.sdoc`, package and checklist of the audit CM-11); rows whose tasks were all booked
  done name the open tasks that carry the rest (for example the verification plan's gaps T-VP1 … T-VP7: SYS-166,
  SYS-167, TOOL-209 … TOOL-212, SYS-168). P6-12 names the unit design `unit-design.md` of SYS-92; its remaining units
  (proposal U1 of that document) are the new proposal N11.
- The objective of the tailored rows is kept as the objective of the integrator; the consequence column of section 3
  says how it reaches him.

---

## 5. Confirmation measures

2-6.4.9 and 2-6.4.10 require confirmation reviews of the key work products, and for ASIL B a set of them by a person
other than the author (independence I1). The standard's list is restated in our words: the safety plan, the safety
analyses and dependent failure analysis, the integration and test strategy, and the safety case need I1 at ASIL B;
the safety validation specification is item level and handed over by T-01. The rest below is planned at I1 as a
**project choice**, because the whole argument leans on those documents.

"Reviewer" is a role of section 2. SYS-63 prepared the procedure, the record template and the review packages.
*Since 2026-09-30 (AN-093, T-11):* the user performs the measures himself for now (task SYS-118, CM-03 including
AN-074 and AN-075). The column "Independence" keeps what is **required**; a measure performed by the user reaches
`performed` or `closed` with a record, but **without independence** — it does not meet R5 and gives no working state
an ASIL statement until an independent person has reviewed it.

<!-- plan-confirmation:start -->
| ID | Measure | Work product | Independence | Reviewer | When | Task | Status |
|---|---|---|---|---|---|---|---|
| CM-01 | Confirmation review of the safety plan | `doc/safety/safety-plan.md` | I1 required at B | confirmation reviewer | before the first release claiming ASIL B capability, and after every new issue of the plan | SYS-63, SYS-118 | closed |
| CM-02 | Confirmation review of the safety analyses | `doc/safety/fmea.md`, `doc/safety/architecture-safety-analysis.md` | I1 required at B | confirmation reviewer | after SYS-71; before the first release | SYS-63 | closed |
| CM-03 | Confirmation review of the dependent failure analysis | `doc/safety/dfa.md` | I1 required at B | confirmation reviewer | after SYS-70; before the first release | SYS-63 | prepared |
| CM-04 | Confirmation review of the integration and test strategy | `doc/safety/verification-plan.md` | I1 required at B | confirmation reviewer | after SYS-67; before the first release | SYS-63, SYS-67 | closed |
| CM-05 | Confirmation review of the safety case | `doc/safety/safety-case.md` | I1 required at B | confirmation reviewer | per release, after SYS-64 | SYS-63 | closed |
| CM-06 | Review of the release decision against the preconditions of section 6 | `doc/safety/cm-plan.md` | I1 by project choice | confirmation reviewer | per release | SYS-63, SYS-68 | performed |
| CM-07 | Review of the safety concept and the safety requirements | `doc/safety/concept.md`, `doc/requirements/safety/goals.sdoc`, `doc/requirements/safety/assumptions.sdoc`, `doc/requirements/safety/mechanisms.sdoc`, `doc/requirements/safety/ssr.sdoc` | I1 by project choice | confirmation reviewer | after SYS-72; before the first release | SYS-63 | closed |
| CM-08 | Review of the tool classification and qualification | `doc/safety/tool-classification.md` | I1 by project choice | confirmation reviewer | after PLAT-83; before the first release | SYS-63 | prepared |
| CM-09 | Review of the component qualification and coexistence argument | `doc/safety/component-qualification.md`, `doc/safety/coexistence.md` | I1 by project choice | confirmation reviewer | after SYS-61 and SYS-62 | SYS-63 | prepared |
| CM-10 | Review of the safety manual | `doc/safety/safety-manual.md` | I1 by project choice | confirmation reviewer | after DOC-19; before the first release | SYS-63, DOC-19 | closed |
| CM-11 | Functional safety audit of the process against this plan | `doc/safety/reviews` | I1 by project choice (recommended at B) | confirmation reviewer | once before the first release claiming ASIL B capability | SYS-63, SYS-118 | prepared |
<!-- plan-confirmation:end -->

"Prepared" means: the review package of `doc/safety/reviews/packages.md` exists (procedure, record template and
checklists by SYS-63). A measure is `performed` when its record under `doc/safety/reviews/records` carries a
verdict, and `closed` when the record's verdict is `accepted` and every finding is closed (checked against the
records by the test since issue 2). SYS-118 performs all eleven, for now by the user himself (T-11).
The functional safety assessment is not in the list (T-09).

**Deviation for release R1 (decision of the safety responsible, 2026-10-03, task DOC-46).** R1 is a **development
release without a safety claim** (D-01; R5 is not met). Before R1 the measures **CM-10, CM-05, CM-06, CM-04, CM-07 and
CM-02** are performed. The measures **CM-03, CM-08, CM-09 and CM-11 are performed after R1**; for R1 this is a
documented deviation from the "before the first release" timing of the table (their rows keep the planned timing and
the status `prepared`). Reasons:

| Measure | Reason for performing it after R1 |
|---|---|
| CM-03 | The anomaly AN-074 (completeness of the DFA topic list against part 9 clause 7 and its informative annex) is open and part of this measure (task SYS-118); it needs a reader of the licensed standard. The findings DFA-1 ... DFA-6 are implemented by SYS-95 (carried into `dev` for R1, task in review), so the review can take the DFA as it stands, but it is not done before R1 |
| CM-08 | The qualification reports of the tools with TCL 2 do not exist yet (PLAT-83, open; K08-05 of the checklist waits for them; D-11, precondition R4) |
| CM-09 | The FreeRTOS qualification package and the kernel internals of the async layer (SYS-124, AN-057) are open, and `component-qualification.md` is not yet released by the safety responsible (issue pending) |
| CM-11 | By its definition the audit takes place once, before the first release claiming ASIL B capability, and after the records of CM-01 ... CM-10 exist; R1 claims none, and CM-03, CM-08 and CM-09 have no record yet |

**Condition that lifts the deviation** (issue 3, SYS-207; decision of the safety responsible of 2026-10-08 on the
safety escalation of the public `v1.0.0-rc2`, E1 b; confirmed in the delta re-check of CM-01 on 2026-10-08, C1): CM-03,
CM-08, CM-09 and CM-11
are performed (record with verdict, see above) **before the first release with an ASIL B claim.** Until then the
deviation holds for Release R1 and every later development release: such a release may be distributed privately or
publicly, but only as a **development release without a safety claim** (D-01, R5 not met); a public release alone
neither needs the four measures nor lifts the deviation. Every recipient, of a private distribution as well as of
the public repository, receives the statement "development release – no safety claim" together with the safety
manual. **Where the recipient finds it** (decision of the safety responsible in the delta re-check of CM-01,
2026-10-08, C2): the statement stands, clearly visible and with a reference to the safety manual, in the `README.md`
of the repository and in every release note (`doc/release-notes/`); both are binding parts of every distribution,
private or public, as long as the deviation holds. The deviation is entered in the safety case as deficit D-22.

**Re-check of CM-01 for issue 3: performed 2026-10-08.** Issue 3 changes a deviation of this plan, so CM-01 was
repeated as a delta review of this paragraph (`reviews/README.md` section 4, rule "New issue" of section 7), by the
user under D-01, without independence, recorded as a delta run in `reviews/records/CR-01-1.md`: C1 the condition
restates the decision E1 b correctly; C2 the statement "development release – no safety claim" is fixed in the
`README.md` and in every release note (the sentence above); C3 issue 3 released. No finding; the record keeps the
verdict `accepted`, and the row CM-01 stays `closed`.

**Performed so far** (all by the user under deviation D-01, without independence — no ASIL statement, R5 not met):

| Measure | Record | Date | Baseline | Verdict | Findings |
|---|---|---|---|---|---|
| CM-01 | `reviews/records/CR-01-1.md` (not published, available on request) | 2026-10-02 | `dev` at `abc4ea78` | accepted (first verdict `accepted with conditions`; re-check 2026-10-02 at `dev` 620e7d44; delta re-check of issue 3 on 2026-10-08, C1 … C3: issue 3 released) | 8 minor (AN-122 … AN-129), corrected in issue 2 by SYS-182, all `closed`; the delta re-check raised none and fixed the statement "no safety claim" in `README.md` and the release notes (C2) |
| CM-10 | `reviews/records/CR-10-1.md` (not published, available on request) | 2026-10-04 | `dev` at `3297a920` | accepted (first verdict `accepted with conditions`; re-check 2026-10-04: F-01 and F-03 closed, issue 2 released; re-check 2 2026-10-04 at `dev` 2c065567: F-04 and F-05 closed, the updates DOC-51 … DOC-59 accepted as issue 2; re-check 3 2026-10-08: F-02 closed, update DOC-50 accepted) | 5 minor (AN-133 … AN-135, AN-143, AN-175): F-01 and F-03 corrected in safety manual issue 2 by DOC-49, F-04 by DOC-53, F-05 by DOC-58, F-02 by DOC-50 (clause reference and neXtBSW's rating E1 … E3 instead of the typical classes, also in gap section 3), all five `closed`; SM-21/SM-22 carry the class `medium` of `fmea.md` issue 2 (C7) |
| CM-07 | `reviews/records/CR-07-1.md` (not published, available on request) | 2026-10-04 | `dev` at `5cc50658` | accepted (first verdict `accepted with conditions`; re-check 2026-10-04 at `dev` 898dbcd1) | 7 minor (AN-136 … AN-142), corrected in concept issue 3 and its catalogue by DOC-51, all `closed`; C6, C10, C17 of the gap analysis with DOC-52 after R1; the `fmea.md` re-rating M7 and the stale places of AN-012/AN-015 with CM-02 (DOC-55); new minor points against the safety manual (RN) with DOC-58 |
| CM-04 | `reviews/records/CR-04-1.md` (not published, available on request) | 2026-10-04 | `dev` at `aaa22c86` | accepted (first verdict `accepted with conditions`; re-check 2026-10-04 at `dev` efc474ea) | 9 minor (AN-144 … AN-152), corrected in verification plan issue 2 by DOC-54, all `closed`; VP-10 open until before the first safety release, T-VP8 and the new minor point RN (AN-176) with TOOL-228; the requirements of Release R1 (strict, slow, bound release CI, pre-flight, SYS-167 list, board run on the tagged image) in its section 9.1 |
| CM-02 | `reviews/records/CR-02-1.md` (not published, available on request) | 2026-10-04 | `dev` at `aaa22c86` | accepted (first verdict `accepted with conditions`; re-check 2026-10-04 at `dev` efc474ea) | 11 minor (AN-153 … AN-163), corrected in `fmea.md` issue 2 and the architecture safety analysis issue 2 by DOC-55, all `closed`; upkeep (RN) with DOC-60, B16 and the LVD reset with SYS-198 after R1; the findings ASA-1 … ASA-12 registered (AN-164 … AN-174, ASA-3 = AN-130); M2 and the open ASA findings with DOC-56 after R1; delta update of `fmea.md` issue 2 (section 1.3: a clause reference instead of the usual figures for the classes) accepted in the re-check of 2026-10-08 (C8) |
| CM-05 | `reviews/records/CR-05-1.md` (not published, available on request) | 2026-10-04 | `dev` at `2c065567` (the candidate state of Release R1, decision Z1 a) | accepted (first verdict `accepted with conditions`; re-check 2026-10-04 at `dev` 9cd81236) | 9 minor (AN-178 … AN-186), corrected in safety case issue 2 by DOC-79 before the tag, all `closed`; the increment row of Release R1 is filled at the tag (CM-06 checks); AN-097 … AN-099 closed, AN-101 confirmed; AN-100 open until the board run (D-15); new minor points (RN) with DOC-80 and DOC-61, D-01 "see 3.1" with DOC-81 after R1 |
| CM-06 | `reviews/records/CR-06-1.md` (not published, available on request) | 2026-10-05 | `v1.0.0-rc1` (`80962d9f`, the release decision of Release R1) | accepted with conditions (R1 released with conditions as development release without a safety claim; GO1 and GO2 given) | 4 minor (AN-187 … AN-190), the conditions after the tag: tag and binding of the CI report (done, F-01), rest run on the tagged image (SYS-167, done 2026-10-05, green), manual board cases and BT-S1-01 on the tagged image (SYS-200, SYS-201), documentation after the tag (DOC-84, DOC-61); AN-085 and AN-086 closed, AN-088 and AN-091 confirmed; AN-100 closed with VP-6 (b) |

Verification reviews that are **not** confirmation measures — code inspection of the safety units (SYS-69),
inspection of the requirements (SYS-72) and of the architecture (SYS-65) — are planned in section 4 (P6-04, P6-11,
P6-14, P8-03). They should also be done by someone other than the author (8-9.4.3.2); an agent reviewing another
agent's work counts for them, but not for the confirmation reviews.

---

## 6. Release

The user decided on 2026-09-22 (iso-q3): *"Freigabe = annotierter Tag vX.Y.Z auf master, vorher gruener CI-Lauf mit
archiviertem Bericht, Sicherheitsdokumente mit Verweis auf den Tag, Qualifizierungsberichte, abgeschlossene
Reviews."* This section turns the decision into the preconditions of 2-6.4.13 for the element. The details of tag
naming, signing and configuration items are SYS-68's (`cm-plan.md`).

<!-- plan-release:start -->
| ID | Precondition before tagging `vX.Y.Z` on `master` | Evidence | Task |
|---|---|---|---|
| R1 | The release commit is on `master`, merged from `dev` with the user's approval (CLAUDE.md), and gets an **annotated tag `vX.Y.Z`** | the tag and its message | SYS-68 |
| R2 | A **green CI run** on the tree that is tagged (same tree hash, clean, strict), its report bound to that tree and archived with the tool versions | archived CI report | SYS-67 |
| R3 | Every safety document of this plan **cites the tag** instead of loose commits (gap finding SG7) | document headers | SYS-68 |
| R4 | The **qualification reports** of the tools (TCL 2) and of the pre-existing components are available for that version | qualification reports | PLAT-83, SYS-62, SYS-121 |
| R5 | The **confirmation reviews** of section 5 and the **verification inspections** (code inspection of the safety units, SYS-69; inspections of section 4) are performed and closed for that version, the confirmation reviews at I1 — a record under deviation T-11 (D-01) does not count | review and inspection records | SYS-118, SYS-69, SYS-120 |
| R6 | The **safety case** and the **safety manual** are issued for that version | `safety-case.md`, `safety-manual.md` | SYS-64, DOC-19 |
<!-- plan-release:end -->

**R and P are one list (issue 2, CR-01-1 F-07).** `cm-plan.md` section 5.1 states the same preconditions as P1 … P9,
with how each is checked; a safety release needs all of them. The mapping:

<!-- plan-release-map:start -->
| cm-plan | Safety plan | Note |
|---|---|---|
| P1 | R1 | candidate complete on `dev`, `VERSION` and changelog section |
| P2 | R2 | green CI run on the tagged tree, report bound and archived |
| P3 | R1 | repository integrity before the tag |
| P4 | R3 | state lines `Baseline: vX.Y.Z` |
| P5 | R4 | qualification reports |
| P6 | R5 | confirmation reviews at I1 and inspections closed |
| P7 | R1 | the user's approval of merge and tag |
| P8 | — | export of a release line (`tools/distcheck`); a configuration-management precondition without a safety counterpart |
| P9 | R6 | safety case and safety manual of the release — binding through R6 since issue 1 of this plan |
<!-- plan-release-map:end -->

A tag that does not meet R1–R6 is a **development release** (the term of `cm-plan.md` 4.2 and of the tag message): it
may be tagged for reproducibility (SYS-68) but carries no safety claim, and its tag message says so. As long as no
independent confirmation reviewer is named (R5, T-11), **every** tag is a development release. Releases of the
open-source branch and of licensed branches (PLAT-46) follow the same rule per branch.

---

## 7. Maintaining the plan

- **Owner:** the safety manager. **Kept up to date by:** the coordinator.
- **Updates of the current issue:** when a task named in section 4 or 5 changes its status to `done`, the coordinator
  sets the row's status in the same merge; when a new safety task is created, the coordinator adds it to the row it
  serves; when a proposal of section 8 becomes a task, the rows name the task instead of the proposal. Each update
  gets a line in the change history (section 12).
- **New issue** (rule sharpened in issue 2 after CR-01-1 F-08): the plan gets a new issue when its argument changes —
  the claim, a role, a tailoring or deviation of section 3, a confirmation measure, a release precondition — and then
  CM-01 is repeated for the change (delta review, `reviews/README.md` section 4). Issue 1 missed this twice: the
  deviation T-11 came as an update, and the release v0.2.0 carried the plan unchecked as issue 1.
- **At a release:** the plan is re-checked against the candidate and its state line becomes `Baseline: vX.Y.Z` (R3,
  `cm-plan.md` P4); if the plan was changed since its last confirmation review, the release is a development release
  or CM-01 is repeated first. Between releases the state line is `Working state: dev at <commit> (after vX.Y.Z)`
  (`cm-plan.md` 6.1).
- **Checked by:** `doc/safety/test/test_safety_plan.py`. The test fails when a named task disappears from
  `doc/todo.yaml`, when a task is `done` although its work product is still missing, when a `done` row names a
  missing work product, or when a gap row of parts 2, 6, 8, 9 is no longer served; since issue 2 also when the plan
  **lags behind**: a row still names a proposal whose task is done (or a carried proposal at all), a row is `planned`
  although one of its tasks has started and its work products exist, a measure of section 5 is `prepared` although
  a record with a verdict exists (or `performed`/`closed` without one), or the mapping R ↔ P of section 6 misses a
  precondition of `cm-plan.md` 5.1. A red test is the signal to update this plan, not to relax the test.
- **Change impact:** TOOL-57 added the optional field `safety_impact` to the tasks; until it is filled for the tasks
  that change the safety part (SYS-122, N9), the coordinator decides per merge whether a change affects a work product
  of this plan (8-8.4.3).

---

## 8. Tasks proposed by this plan

For the coordinator to carry into `doc/todo.yaml` (IDs are assigned there). A proposal that no task carries yet
(column "Task" `—`) is referenced from section 4 as `N<n>`; once a task carries it, the column names the task and the
rows of sections 4 to 6 name the task instead of the proposal (checked). The rows stay as the history of where the
work came from.

<!-- plan-proposals:start -->
| ID | Title | Done when | Prio | Task |
|---|---|---|---|---|
| N1 | Interplay of functional safety and cybersecurity | `doc/safety/security-interplay.md` lists every interface that can change code, configuration or calibration in the field (bootloader update over CAN, UDS write services, XCP) with its effect on the safety argument, the protection that exists, and an assumption of use for what the integrator must add; referenced from the safety manual | P2 | SYS-92 |
| N2 | Unit design of the safety units that have no design document | a design description, consistent with architecture and HSI, exists for the watchdog handling (`SafeWatchdog` and its service), the hard-fault handler and the safety task; the list of safety units (PLAT-91) and the design documents agree, checked by a test | P2 | SYS-92 |
| N3 | Argument over the admissible configuration range | for every configuration value the stage-1 mechanisms depend on, the range verified by the project (generator tests with own profiles, build profiles) and the verification the integrator owes for his data set (6-C.4.5); result in the safety manual | P2 | SYS-92 |
| N4 | Confirmation of the roles on the decision board | the user confirms (or changes) the appointment of project manager and safety manager of section 2 on the decision board, and the entry is referenced from section 2 | P1 | SYS-92 |
| N5 | Perform the confirmation measures of section 5 once the reviewer is named (iso-q1) | the confirmation measures CM-01 … CM-11 are performed by the named reviewer with the packages and the record template of SYS-63 (`doc/safety/reviews`), each with a record under `doc/safety/reviews/records` that carries a verdict; the closures of the anomaly register that the reviewed work products contain are confirmed in the record (anomaly register section 2.3). SYS-63 prepared them and is done; performing them is SYS-118, for now by the user himself (T-11) | P1 | SYS-118 |
| N6 | Verification inspections of the SSR, the HSI and the architecture with record | the inspections of section 4 that are not confirmation measures — SSR and HSI (P6-04), architecture (P6-11), safety requirements (P8-03) — are performed by someone other than the author (section 5: an agent reviewing another agent's work counts) and recorded with the template of `doc/safety/reviews`; SYS-72 and SYS-65 wrote the work products, SYS-63 the template, all three are done | P2 | SYS-120 |
| N7 | Assess the service console commands for the product image | every console command that stays in the product image after SYS-74 (the service commands SYS-74 left open: `sbc`/`gd` register writes, `out`/`pwm set`, `can send`, `kl30b on`, `nvm write`, `dem clear`/`dem cycle`) is either left out of the product build like the test commands or argued as a specified function, checked in the image by `tools/elfcheck product`; same work as coexistence finding CX-9 (N-CX4) | P2 | SYS-119 |
| N8 | Qualification reports of the remaining pre-existing components | the reports QR-02 ff. in the order of `component-qualification.md` section 4.2 (without FreeRTOS, which is postponed by q-sys62-qualifizierung); SYS-62 wrote the plan and QR-01 and is done, SYS-106 and SYS-109 carry parts of QR-03 and QR-01 only | P2 | SYS-121 |
| N9 | Use of the safety-impact field of TOOL-57 | TOOL-57 added the optional field `safety_impact` to `doc/todo.yaml` and its check in `tools/todo/todoplan.py`, without filling it for any task; the coordinator records it for the tasks that change the safety part (the candidates are named in the TOOL-57 changelog entry), and the confirmation measures affected by a change are named in its `reverify` list | P3 | SYS-122 |
| N10 | Rest of the resource estimate of the safety part | the two parts `sw-architecture.md` section 6 and inspection I8 name as open are estimated: the CAN bus load (no estimate so far) and a measured worst-case execution time of the safety cycle (today analysed and checked only indirectly through the period check); SYS-65 wrote the estimate and SYS-97 bounded the interrupt stack, both are done | P2 | DOC-41 |
| N11 | Unit design of the remaining safety units | proposal U1 of `doc/safety/unit-design.md` section 7: every row of its unit register with status `partial` or `missing` is `described` (supervisor reaction logic, device report box, `SafeIo`/`SafeState`/register list, `MemoryProtection`, `IntegrityEccHandler`, `SafeMemory`, `LpitDeadmanCheck`, the ADC console, the rest of `main`) in the module's `doc/` or in `unit-design.md`; `test_unit_design.py` stays green. Added by issue 2 (SYS-182) because P6-12 had no open task for the rest | P2 | — |
<!-- plan-proposals:end -->

---

## 9. Open points

| # | Open point | Owner | Consequence until resolved |
|---|---|---|---|
| O1 | Who performs the confirmation reviews (iso-q1)? *Answered for now 2026-09-30 (AN-093): the user himself, without independence (T-11); an independent person is still open* | project manager | the measures are performed without independence (SYS-118; first CR-01-1, 2026-10-02); every tag stays a development release without an ASIL statement until an independent person has reviewed it |
| O2 | FreeRTOS route (iso-q5). *Closed: decided 2026-09-22 (`rtos-u1-anfrage`, AN-094) — stay on FreeRTOS and qualify it* | project manager | none any more: P6-07 and P8-09 are `in progress` (SYS-62 done, qualification package SYS-124, reports SYS-121); the partitioning element is argued with SYS-21 (P6-06) |
| O3 | The concept does not yet point to this plan (gap proposal C16) | coordinator | DOC-21 is done without it; carried by SYS-100 (touches `concept.md`) — AN-095 |
| O4 | Formal document elements (author, approver, status) are missing on the safety documents, this one included. *Issue 2: this plan carries them in its header; the document register (`anomaly-register.md` section 6, accepted 2026-10-02) carries them for every safety document* | coordinator | AN-096 in review; the headers of the other documents follow their next issue |

---

## 10. Review questions of the architecture (section 11)

This task changes no code, no generator, no configuration and nothing under `platforms/`: one document and one
consistency test. **Board without an SBC, different crystal, derivative, CAN buses, task set:** the plan contains no
board fact; the one derivative-specific work product (`hsi-s32k1xx.md`, SYS-66) is named per family, and further
families get their own HSI work product through the same row. **New values in one configuration level:** none.
**Host test with its own test profile:** `test_safety_plan.py` checks the plan against the repository and against
deliberately broken copies of itself; it needs no board profile. **Qt-free core, YAML paths:** not affected.
**Device-driver granularity, platform rule:** not applicable. *Issue 2 (SYS-182)* changes the same document and its
test only; the answers stand.

## 11. Sources

- ISO 26262:2018 parts 2, 6, 8, 9 and 10, cited by part and clause only, through the reading in
  `iso26262-gap.md` (not published, available on request) (SYS-58); the standard itself is not in the repository.
- Decision of the safety responsible 2026-09-30 on AN-093 (confirmation measures by the user himself, T-11).
- Decisions of the user on the decision board, 2026-09-22: iso-q1 (reviewer later), iso-q2 (claim), iso-q3
  (release), iso-q4 (coexistence), iso-q5 (FreeRTOS later), iso-q6 (single exit for safety units); 2026-09-22
  `rtos-u1-anfrage` (FreeRTOS route); 2026-09-28 `q-sys92-rollen` (roles confirmed).
- Issue 1: repository at `ae92e89`: `doc/safety/{concept,fmea,iso26262-gap,tool-classification,static-analysis,rtos-comparison,traceability}.md`,
  `doc/requirements/safety/ssr.sdoc`, `doc/todo.yaml` (tasks SYS-58 … SYS-90, DOC-19, DOC-21, DOC-22, PLAT-83, PLAT-91,
  TOOL-40, TOOL-57, TOOL-58), `CLAUDE.md`, `CONTRIBUTING.md`.
- Issue 2: confirmation review record `doc/safety/reviews/records/CR-01-1.md` (findings F-01 … F-08); repository at
  `dev` 3405bf72: `doc/todo.yaml`, `doc/safety/{cm-plan,verification-plan,unit-design,config-range,security-interplay,coexistence,safety-manual,anomaly-register}.md`.

---

## 12. Change history

| Issue | Date | Task | Change |
|---|---|---|---|
| 1 | 2026-09-28 | SYS-60 | first issue against `dev` `ae92e89`: claim, roles, tailoring T-01 … T-10, 53 activities, CM-01 … CM-11, release R1 … R6, proposals N1 … N4 |
| 1 (update) | 2026-09-28 | SYS-61 | coexistence result in section 3 (21 of 36 modules ASIL B), P9-02 `partial` |
| 1 (update) | 2026-09-28 | SYS-92 | proposals N1 … N4 carried out in their own documents; paragraph "Confirmation of the appointment (N4)" in section 2 |
| 1 (update) | 2026-09-28 | SYS-94 | state line `Working state: dev at 6f868b5 (after v0.1.0)` (cm-plan 6) |
| 1 (update) | 2026-09-29 | WAVE-1001 | statuses after the cloud waves (P2-03, P2-12 … P2-14 and others), role "confirmation reviewer" points to `reviews/README.md` |
| 1 (update) | 2026-09-29 | DOC-35 | proposals N5 … N9 after SYS-62, SYS-63, SYS-74 and TOOL-57 were booked done with parts open; P2-08 and others |
| 1 (update) | 2026-09-30 | DOC-38 | decision on AN-093: confirmation measures by the user himself, tailoring T-11; claim, roles, section 5, O1; P6-22 `done` |
| 1 (update) | 2026-10-01 | SAFETYDOC-1001 | proposal N10 (rest of the resource estimate); P2-10, P6-05 statuses |
| 1 (update) | 2026-10-01 | REL-020 | release candidate v0.2.0: state line `Baseline: v0.2.0` (cm-plan 5.2 step 1); the plan was carried unchecked |
| 1 (update) | 2026-10-01 | DOC-21 | requirements in `doc/requirements/safety/ssr.sdoc` instead of `doc/safety/requirements.yaml` (P6-02, P8-02, CM-07, sources) |
| 1 (update) | 2026-10-02 | SAFETYDOC-1002 | P2-10 and P6-01 `done`, status counts |
| 1 (update) | 2026-10-02 | SYS-118 | CM-01 `performed` (record CR-01-1, the user under D-01, accepted with conditions, findings AN-122 … AN-129) |
| 2 | 2026-10-02 | SYS-182 | findings F-01 … F-08 of CR-01-1: formal elements and this history in the header, state line `Working state: dev at 3405bf72 (after v0.2.0)` (F-01, F-02); FreeRTOS route and O2 closed per AN-094 (F-03); roles confirmed 2026-09-28 (F-04); T-11 marked as deviation with the condition that lifts it, T-01 consequence (F-05); every activity re-checked against `doc/todo.yaml` — proposals replaced by their tasks, statuses raised, new proposal N11 (F-06, G-03); R ↔ P mapping with `cm-plan.md` 5.1, R2 and R5 aligned, term "development release" (F-07); maintenance rule (new issue when the argument changes) and the test that reports a lagging plan (F-08) |
| 2 (accepted) | 2026-10-02 | CM01-CLOSE | re-check of CM-01 by the user (D-01, without independence) at `dev` 620e7d44: F-01 … F-08 closed, AN-094 confirmed, issue 2 released; record verdict `accepted`; CM-01 `closed` (section 5; no independence, R5 not met, no ASIL statement); header approver and status |
| 2 (update) | 2026-10-03 | DOC-46 | deviation for release R1 (decision of the safety responsible 2026-10-03): CM-10, CM-05, CM-06, CM-04, CM-07, CM-02 before R1; CM-03, CM-08, CM-09, CM-11 after R1 with reasons and the condition that lifts it (section 5); R1 is a development release without a safety claim |
| 2 (update) | 2026-10-04 | DOC-49 | CM-10 `performed` (record CR-10-1, the user under D-01, `accepted with conditions`, findings AN-133 … AN-135; safety manual issue 2); row CM-07 names all five work products of the package (concept and the four `.sdoc` files of DOC-21) |
| 2 (update) | 2026-10-04 | DOC-51 | CM-07 `performed` (record CR-07-1, the user under D-01, baseline `dev` at 5cc50658, `accepted with conditions`, findings AN-136 … AN-142 corrected in concept issue 3 and its catalogue; `closed` after the re-check of F-01 … F-07) |
| 2 (update) | 2026-10-04 | DOC-54 | CM-04 `performed` (record CR-04-1, the user under D-01, baseline `dev` at aaa22c86, `accepted with conditions`, findings AN-144 … AN-152 corrected in verification plan issue 2; `closed` after the re-check of F-01 … F-09) |
| 2 (update) | 2026-10-04 | DOC-55 | CM-02 `performed` (record CR-02-1, the user under D-01, baseline `dev` at aaa22c86, `accepted with conditions`, findings AN-153 … AN-163 corrected in `fmea.md` issue 2 and the architecture safety analysis issue 2; `closed` after the re-check of F-01 … F-11) |
| 2 (update) | 2026-10-04 | DOC-57 | CM-07 `closed`: re-check by the user (D-01, without independence) at `dev` 898dbcd1, F-01 … F-07 closed, record verdict `accepted`, concept issue 3 and its catalogue released (section 5; no independence, R5 not met, no ASIL statement) |
| 2 (update) | 2026-10-04 | DOC-59 | CM-04 and CM-02 `closed`: re-checks by the user (D-01, without independence) at `dev` efc474ea, F-01 … F-09 (CR-04-1) and F-01 … F-11 (CR-02-1) closed, record verdicts `accepted`, verification plan issue 2 and `fmea.md`/architecture safety analysis issue 2 released (section 5; no independence, R5 not met, no ASIL statement) |
| 2 (update) | 2026-10-04 | DOC-79 | CM-10 stays `performed`: re-check 2 by the user (D-01, without independence) at `dev` 2c065567, F-04 and F-05 closed (AN-143, AN-175), the updates of safety manual issue 2 accepted; record verdict `accepted with conditions` only because of F-02 (DOC-50, before the first public release) (section 5) |
| 2 (update) | 2026-10-04 | DOC-79 | CM-05 `performed` (record CR-05-1, the user under D-01, baseline `dev` at 2c065567 as the candidate of Release R1, `accepted with conditions`, findings AN-178 … AN-186 corrected in safety case issue 2; `closed` after the re-check of F-01 … F-09) |
| 2 (update) | 2026-10-04 | DOC-80 | CM-05 `closed`: re-check by the user (D-01, without independence) at `dev` 9cd81236, F-01 … F-09 closed, record verdict `accepted`, safety case issue 2 released (section 5; no independence, R5 not met, no ASIL statement) |
| 2 (update) | 2026-10-05 | DOC-84 | CM-06 `performed` (record CR-06-1, the user under D-01, baseline `v1.0.0-rc1`, `accepted with conditions`, findings AN-187 … AN-190 = the conditions after the tag; R1 released with conditions as development release without a safety claim) (section 5; no independence, R5 not met, no ASIL statement) |
| 3 | 2026-10-08 | SYS-207 | deviation for release R1 restated after the decision of the safety responsible of 2026-10-08 (safety escalation of the public `v1.0.0-rc2`, E1 b): the condition that lifts it no longer names the first public release - CM-03, CM-08, CM-09 and CM-11 stay bound to the first release with an ASIL B claim, a public development release without a safety claim is allowed, every recipient keeps receiving the statement "no safety claim" with the safety manual (section 5); issue 2 read "before the first public release or the first release with an ASIL B claim, whichever comes first". Header (issue, author, approver, status `in review`), state line `Working state: dev at 9bf2d520 (after v1.0.0-rc1)`; the re-check of CM-01 (delta review) is open, the record CR-01-1 is not changed |
| 3 (accepted) | 2026-10-08 | SYS-209 | delta re-check of CM-01 by the user (D-01, without independence; record `reviews/records/CR-01-1.md`, delta run, re-check page of 2026-10-08): C1 the condition restates E1 b correctly, C2 the statement "development release – no safety claim" binding in `README.md` and every release note (sentence added in section 5), C3 issue 3 released; header approver and status, state line `Working state: dev at c0928380 (after v1.0.0-rc1)`; CM-01 stays `closed`. Re-check 3 of CM-10 (record CR-10-1, C4 … C7): F-02 closed, verdict `accepted`, CM-10 `closed`; row CM-02 of "Performed so far" with the delta update of `fmea.md` 1.3 (C8) (section 5; no independence, R5 not met, no ASIL statement) |
