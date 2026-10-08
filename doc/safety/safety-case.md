# neXtBSW safety case — software SEooC at ASIL B (SYS-64)

| | |
|---|---|
| Title | Safety case of neXtBSW - software SEooC at ASIL B: claims, argument, evidence, deficits, increments per release |
| Issue | 2, 2026-10-04 (issue 1, 2026-09-28) |
| Author | SYS-64 (issue 1; agents writing on the user's behalf - author side), the updates in between by the tasks of section 12; issue 2: DOC-79 |
| Approver | the safety manager (the user): issue 1 **accepted** 2026-10-02 with the review set of 15 safety documents (`c7c9398c`); issue 2 is the state after the corrections of the CM-05 findings (decision Z4 a of the confirmation review CM-05, record `doc/safety/reviews/records/CR-05-1.md`, 2026-10-04, the user under D-01, verdict `accepted with conditions`); issue 2 **accepted** 2026-10-04 in the re-check of F-01 … F-09 in that record (steps 6 and 7 at `dev` 9cd81236, verdict `accepted`, CM-05 closed; D-01, without independence); its increment row of Release R1 (I-1) is checked in CM-06 (record `doc/safety/reviews/records/CR-06-1.md`, 2026-10-05, the user under D-01, `accepted with conditions`, decision Z9 a) and filled in the documentation commit after the tag `v1.0.0-rc1` (DOC-84) |
| Status | issue 2 **accepted** 2026-10-04 (re-check of CR-05-1; D-01, without independence); issue 1 accepted 2026-10-02 |
| Change history | section 12 |

Working state: dev at f4055d8e (after v1.0.0-rc1)
Readers: the user as safety manager (owner of this case, safety plan section 2), the coordinator who keeps it up to
date at every merge and release, the user as confirmation reviewer of CM-05 under deviation D-01 (record CR-05-1) and
an independent confirmation reviewer once named, and an integrator who wants to see what neXtBSW claims, on which
argument, and with which evidence.
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**What this document is.** The safety case that the gap analysis `iso26262-gap.md` (not published, available on request) found missing
(row 2-13, proposed task 6) and that the safety plan [`safety-plan.md`](safety-plan.md) plans as activity P2-13 and
release precondition R6. It states the **claims** (the assumed requirements of the SEooC), the **argument** that
connects each claim with what was done — for the product and for the process — and the **evidence** the argument
rests on: work products, tests, board records, decisions. It is **incremental per release**: every evidence item
says since which baseline it exists, and section 7 adds one increment per release. The consistency test
[`test/test_safety_case.py`](test/test_safety_case.py) resolves **every evidence link** against the repository.

**Licence rule.** The standard is cited by **part and clause only** (for example "2-6.4.8" = ISO 26262-2:2018,
clause 6.4.8). No text, table or figure of the standard is reproduced; requirements are restated in our own words.

**What it is not.** Not a statement that neXtBSW complies with anything: section 0 says plainly that the top claim is
**not supported** today. Not the safety plan (who does what), not the safety manual (DOC-19, what the integrator must
do), not the traceability matrix (`traceability.md`, generated) — the case cites them as evidence.

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Top claim (C-00) | neXtBSW stage 1 can be integrated as a software SEooC with ASIL B capability for the assumed safety goals it covers (ASG-01, ASG-02, ASG-05, ASG-06, ASG-07) within its stated assumptions; for ASG-03 and ASG-04 it provides no stage-1 mechanism, carried by the integrator under AoU-23. It was developed following the methods of ISO 26262-6 at ASIL B without the management part |
| Release R1 (`v1.0.0-rc1`) | **development release, no safety claim**; not citable for a safety statement - reasons D-01 (confirmation only by the user, without independence), D-21 (no baseline is a safety release) and D-22 (CM-03, CM-08, CM-09 and CM-11 after R1) |
| Verdict today | **not supported**. The confirmation reviews are performed by the user himself without independence (D-01; row "Confirmation reviews"), so no working state gets an ASIL statement; CM-03, CM-08, CM-09 and CM-11 come after R1 (D-22); the confirmation review of the safety manual is closed, but without independence (D-02); no baseline is a safety release (D-21); C-03 and C-04 stay in the basis for completeness (concept.md 2.2 lists all seven assumed safety goals) and stay not supported on their own, which is what keeps the top verdict at its floor — narrowed wording (AN-101, decision cloud-0930-sys-77) does not by itself raise it |
| Claims | 10 claims: 1 top claim, 7 product claims (the assumed safety goals ASG-01 … ASG-07 of `concept.md`), 1 context claim (the assumptions of use), 1 process claim (the claim of the safety plan, iso-q2) |
| Product argument | per assumed safety goal over the software safety requirements derived from it (`ssr.sdoc`) to tests and board records. Verdicts: 0 supported, 3 supported with limits, 2 partly supported, 2 not supported — identical to the stage-1 coverage of `concept.md` section 2.2 (checked) |
| Process argument | per part of the standard (2, 6, 8, 9) over the 53 activities of the safety plan. Verdicts: 4 partly supported |
| Evidence | **118** items: 59 test, 31 work product, 13 board record, 8 decision, 4 analysis, 3 record. Status: 102 available, 14 partial, 2 missing (each missing item has an open task); review records, the trace matrix, board captures and the task list are not published (section 1.3) |
| Deficits | **20** assurance deficits, each with its task or tailoring (section 6) |
| Confirmation reviews | performed by the user under D-01, without independence (records under `doc/safety/reviews/records`, not published, section 1.3): CM-01 safety plan (CR-01-1; issue 3 released in its delta re-check of 2026-10-08), CM-07 concept and requirements (CR-07-1), CM-04 verification plan (CR-04-1), CM-02 failure analysis and architecture safety analysis (CR-02-1) and CM-10 safety manual (CR-10-1; F-02 closed in re-check 3 of 2026-10-08, DOC-50) `closed`; CM-05 this case (CR-05-1, `dev` 2c065567) `closed`, this issue 2 is its correction, accepted in the re-check of 2026-10-04; CM-06 release decision of Release R1 (CR-06-1, tag `v1.0.0-rc1`, 2026-10-05) `performed`, `accepted with conditions`; CM-03, CM-08, CM-09, CM-11 after R1 (D-22) |
| Increments | 3: `v0.1.0` (development release, no safety case, 84 of today's evidence items already present), Release R1 `v1.0.0-rc1` (development release, this issue, 32 further items; tag commit, bound CI report and board run in section 7) and the working state after it (`v0.2.0` is a development release without an increment of its own, section 7) |
| Verification | **mock/host**: `doc/safety/test/test_safety_case.py` resolves every evidence link (paths, test entries with their SSR, text anchors, board captures, decision IDs, task IDs, the tag `v0.1.0` when it is present) and runs counter-checks with deliberately broken copies. No hardware, no code change |

---

## 1. Structure of the case

### 1.1 Notation

The case is written as tables so that a test can check it; the structure follows the usual claim–argument–evidence
pattern:

- **Claim** (`C-nn`, section 2): a statement that the element is meant to meet. For an SEooC the claims are the
  **assumed** requirements (2-6.4.5.7, 10-9.2.4): the assumed safety goals of `concept.md` section 2.2 and the claim
  of the safety plan section 1.2.
- **Argument** (`A-nn`, `A-P<part>`, sections 3 and 4): the strategy that says why the claim holds, the basis it
  decomposes into (requirements, assumptions of use, activities, sub-claims), the evidence it cites, the deficits it
  admits and a **verdict**.
- **Evidence** (`E?-nn`, section 5): one row per item, with the reference the test resolves, since which baseline it
  exists and its status.
- **Deficit** (`D-nn`, section 6): something the argument needs and does not have — the counter-evidence of the case.
  Every deficit names the task that removes it, or the tailoring of the safety plan that hands it on.

Verdicts, from strong to weak: `supported`, `supported with limits`, `partly supported`, `not supported`. An argument
is `supported` only without deficits and with all its evidence available; a claim is never stronger than what
`concept.md` section 2.2 says about its coverage; the top claim is never stronger than its weakest sub-claim (all
checked).

### 1.2 How the claims decompose

```
C-00 top claim ─┬─ C-01 … C-07  product: assumed safety goals ASG-01 … ASG-07  (A-01 … A-07)
                │     └─ SSR-nnn (ssr.sdoc) ─ tests (tools/test/tests.yaml) ─ board records (captures)
                ├─ C-08  context: the assumptions of use are stated for the integrator  (A-08)
                │     └─ AoU-nn cited by the SSR ─ concept.md section 4, safety manual (DOC-19)
                └─ C-09  process: developed following part 6 at ASIL B without management part  (A-P2, A-P6, A-P8, A-P9)
                      └─ activities P2-01 … P9-04 (safety-plan.md section 4) ─ their work products
```

### 1.3 Reference forms that the test resolves

| Form in the "Reference" column | Meaning | Resolved by the test |
|---|---|---|
| `` `path` `` | a file or folder of the repository | the path exists |
| `` `path::text` `` | a place in a file, named by a text it contains (a heading, a decision ID, a capture line) | the file exists and contains the text |
| `` `test:<name>` `` | an entry of `tools/test/tests.yaml` | the entry exists **and** names the requirement of the evidence row in its field `requirements` |
| `—` | no reference yet | allowed only for status `missing` |

A test entry is evidence that a test is **defined** for the requirement; whether it passes is the job of the test
run of the baseline (archived CI report, D-12).

**Evidence that is not published** (decision Z2 b of CM-05, record CR-05-1; export classes of DOC-73). Part of the
evidence stays in the private repository and is not exported with a release: the **review records** and code
inspections under `doc/safety/reviews` (EW-11, the records named in section 0 and in D-01, D-02, D-12, D-17, D-20),
the generated **trace matrix** `doc/safety/traceability.md` (EW-17), the **board captures** under
`tools/boardtest/captures` (EB-01 … EB-13), the **task list** `doc/todo.yaml` (ED-01 … ED-06; exported only in a
cleaned form) with its checker `tools/todo` (EW-32, not published), the requirements under `doc/requirements` other
than the safety requirements (the safety requirements and catalogues under `doc/requirements/safety` - `ssr.sdoc`,
EW-15, and `goals.sdoc`, `assumptions.sdoc`, `mechanisms.sdoc` - are published since the re-check of CM-05,
decision SDOC), and the written project rules `CLAUDE.md` (EW-01; exported only in a cleaned form, without the
sections on the internal way of working). They are **available on request**. Their status in section 5 is the
status in the repository; that
they are not published **raises no verdict** of this case and lowers none - a reader of the export takes them as
named, not as seen. **Paths of private files** (decision Z10 a): where the published text names the path of a
private file, the path stays as a reference; the file itself is not published and is available on request.

---

## 2. Claims

<!-- case-claims:start -->
| ID | Kind | Claim | Source |
|---|---|---|---|
| C-00 | top | neXtBSW stage 1 can be integrated as a software SEooC with ASIL B capability for the assumed safety goals it covers (ASG-01, ASG-02, ASG-05, ASG-06, ASG-07) within its stated assumptions; for ASG-03 and ASG-04 it provides no stage-1 mechanism and the integrator carries them under AoU-23. It was developed following the methods of ISO 26262-6 at ASIL B without the management part | safety-plan 1.1, 1.2 |
| C-01 | product | Unintended torque of the electric drive shall be prevented, or terminated within the fault tolerance time | ASG-01 |
| C-02 | product | The path that switches the power stage off shall itself be available when it is needed | ASG-02 |
| C-03 | product | Cell voltage, cell temperature and pack current outside the safe operating area shall be detected and made known within the fault tolerance time | ASG-03 |
| C-04 | product | Cell balancing shall not run unintentionally | ASG-04 |
| C-05 | product | Corrupted, stale or repeated safety-related data shall not be used as if it were valid | ASG-05 |
| C-06 | product | A failure of the control software (program flow, timing, memory, register configuration) shall lead to a defined safe state and not to arbitrary output behaviour | ASG-06 |
| C-07 | product | The ECU shall not start, or shall not run, with an image that is not the intended one | ASG-07 |
| C-08 | context | The assumptions of use on which the product argument rests are stated, each with its reason, for validation by the integrator | 2-6.4.5.7, 10-9.2.4 |
| C-09 | process | neXtBSW is developed following the methods of ISO 26262-6 at ASIL B, without the management part. | iso-q2 |
<!-- case-claims:end -->

The product claims are the assumed safety goals **word for word** (checked against `concept.md`): they are
assumptions about an item, and the element contributes mechanisms to them — it does not fulfil them alone
(concept 2.2). C-09 is the claim of the safety plan word for word (checked). What stage 1 means — base-software
mechanisms only, device diagnostics not credited — is the user's decision `abn-sys19` (evidence ED-07).

---

## 3. Product and context argument

The first row argues the top claim over the other nine claims. Columns: **Basis** — the sub-claims (top claim), the
requirements (`SSR-nnn`) derived from the goal, or the assumptions of use (`AoU-nn`); the
test requires the basis to be exactly the set that `ssr.sdoc` derives from the goal. **Evidence** — every
test and board record of those requirements is cited (checked). **Deficits** — section 6.

<!-- case-product:start -->
| ID | Claim | Strategy | Basis | Evidence | Deficits | Verdict |
|---|---|---|---|---|---|---|
| A-00 | C-00 | Split into what the element does (product claims per assumed safety goal, but the top claim's own wording is narrowed — AN-101, decision cloud-0930-sys-77, 2026-09-30 — to the goals stage 1 covers; ASG-03/ASG-04 are cited only through AoU-23 to the integrator), what it assumes (context claim) and how it was made (process claim); the top claim holds only if all three hold for one baseline that is a safety release. C-03 and C-04 stay part of the basis (concept.md 2.2 lists all seven assumed safety goals) so the case keeps arguing them for completeness, but they no longer raise what C-00 asserts | C-01, C-02, C-03, C-04, C-05, C-06, C-07, C-08, C-09 | ED-01, ED-02, ED-07, ER-01, EW-02, EW-09, EW-10 | D-01, D-02, D-21, D-22 | not supported |
| A-01 | C-01 | Argue over the *termination* path: every fault the MCU detects leads, through the one safe outputs table, to de-energised PWM groups and `safe_off` outputs — from the supervisor, the fault handlers and the halt deadman alike; the pins and registers the path depends on are locked and checked. Detection of torque whose cause the MCU does not see (wrong command, enable that does not reach the part) is not argued: nothing compares command with effect | SSR-002, SSR-004, SSR-005, SSR-016, SSR-028, SSR-033, SSR-034, SSR-060, SSR-061, SSR-063, SSR-064, SSR-070, SSR-080, SSR-087, SSR-089, SSR-090, SSR-091, SSR-092 | EW-08, EW-15, EW-17, EW-21, ET-002, ET-004, ET-005, ET-016, ET-028, ET-033, ET-034, ET-060, ET-061, ET-063, ET-064, ET-070, ET-080, ET-087, ET-089, ET-090, ET-091, ET-092, EB-03, EB-10, EB-11, EB-12 | D-03, D-04, D-06, D-07, D-08, D-15 | partly supported |
| A-02 | C-02 | Argue over independence of the switch-off path: one table used by every caller, one power-stage adapter per `safe_off` output enforced by the build and re-checked in the linked image, de-energise before every reset, fault handlers and deadman as callers that need no running task, register lock and cyclic check of the path's pins, a reset-loop limit that ends in a held safe state | SSR-002, SSR-004, SSR-005, SSR-006, SSR-007, SSR-016, SSR-028, SSR-033, SSR-034, SSR-060, SSR-065, SSR-066, SSR-070, SSR-080, SSR-089, SSR-091, SSR-092 | EW-08, EW-15, EW-17, EW-21, ET-002, ET-004, ET-005, ET-006, ET-007, ET-016, ET-028, ET-033, ET-034, ET-060, ET-065, ET-066, ET-070, ET-080, ET-089, ET-091, ET-092, EB-03, EB-06, EB-09, EB-10, EB-11, EB-12 | D-03, D-08, D-15 | supported with limits |
| A-03 | C-03 | The base software supervises only what the MCU measures itself (ADC reference, qualifier per analogue value) and offers the report path for devices; it has no measurement of cells, no age supervision and no second source. The detection part is **not argued** at stage 1 and is handed to the integrator by AoU-23 | SSR-030, SSR-031, SSR-040, SSR-061, SSR-062, SSR-064 | EW-08, EW-15, EW-17, EW-21, ET-030, ET-031, ET-040, ET-061, ET-062, ET-064 | D-04, D-05, D-07, D-15 | not supported |
| A-04 | C-04 | Balancing is commanded over SPI, not by an MCU output; the safe state does not reach it. The base software offers the device report path only. **Not argued** at stage 1; AoU-23 | SSR-063, SSR-064 | EW-08, EW-15, EW-17, EW-21, ET-063, ET-064 | D-06, D-07 | not supported |
| A-05 | C-05 | Argue over the CAN path: E2E profiles on the signals the integrator declares (AoU-11), a quality qualifier the application reads (AoU-12), no transmission into an unhealthy bus, and a signal layout checked against an independent decoder. On-board device values have no age supervision | SSR-040, SSR-041, SSR-042, SSR-062, SSR-084 | EW-08, EW-15, EW-17, EW-21, ET-040, ET-041, ET-042, ET-062, ET-084 | D-05, D-15 | supported with limits |
| A-06 | C-06 | Argue over the supervision classes of `concept.md` section 6: program flow and time (watchdog serviced by the safety task only and re-checked, cycle period against an independent time base, deadman), memory (MPU with checked descriptors, protection gate, ROM check, ECC), MCU periphery and supplies, start reason and run-down — every event with one reaction decided in one place, S1 held where a reset does not help. Accepted limit: RTE deadline violations (SM-05) are reported, not reacted to, by decision | SSR-001, SSR-003, SSR-010, SSR-011, SSR-012, SSR-013, SSR-014, SSR-015, SSR-017, SSR-018, SSR-020, SSR-021, SSR-022, SSR-023, SSR-024, SSR-025, SSR-026, SSR-027, SSR-028, SSR-030, SSR-031, SSR-032, SSR-050, SSR-052, SSR-065, SSR-066, SSR-068, SSR-069, SSR-081, SSR-086, SSR-087, SSR-088 | EW-08, EW-15, EW-17, EW-21, ED-08, ET-001, ET-003, ET-010, ET-011, ET-012, ET-013, ET-014, ET-015, ET-017, ET-018, ET-020, ET-021, ET-022, ET-023, ET-024, ET-025, ET-026, ET-027, ET-028, ET-030, ET-031, ET-032, ET-050, ET-052, ET-065, ET-066, ET-068, ET-069, ET-081, ET-086, ET-087, ET-088, EB-01, EB-04, EB-05, EB-06, EB-07, EB-08, EB-09 | D-08, D-09, D-15, D-16, D-17, D-18 | supported with limits |
| A-07 | C-07 | *Start:* the bootloader starts only a valid, compatible image (CRC and validity record) and the memory layout of every linked image is checked against the derivative. *Run:* ROM check once per start and repeated with a period, flash ECC, S1 held on failure. The identity of configuration and generator is readable in the image (SSR-082, implemented by PLAT-79: console `version`/`info`, UDS DID 0xF1F1; host and board 2026-10-01) | SSR-003, SSR-024, SSR-025, SSR-027, SSR-051, SSR-067, SSR-082, SSR-083 | EW-08, EW-15, EW-17, EW-21, ET-003, ET-024, ET-025, ET-027, ET-051, ET-067, ET-082, ET-083, EB-01, EB-04, EB-07, EB-08 | D-15, D-17 | partly supported |
| A-08 | C-08 | Every assumption of use that a requirement is derived from is written down with its reason in `concept.md` section 4 (proposed ones in `fmea.md` and `tool-classification.md`), and the integrator gets them through the safety manual (issue 2, sections 7 and 8; section 3.1 below). The restart time measured on the board gives AoU-05 its number: at most 1 077 ms through the watchdog path and 858 ms through the fault handler (`fmea.md` 8.1, re-measured 2026-09-30), confirmed with bootloader 0.3.9 (8.2, 2026-10-03) | AoU-04, AoU-05, AoU-06, AoU-07, AoU-08, AoU-10, AoU-11, AoU-12, AoU-13, AoU-14, AoU-16, AoU-17, AoU-18, AoU-24, AoU-25, AoU-30, AoU-32 | EW-08, EW-21, EW-34, EW-05, ED-08, EB-02, EB-13 | D-02 | partly supported |
<!-- case-product:end -->

Notes on the verdicts:

- **A-02 and A-06 "supported with limits"**, not "supported": the limits are the deficits named, first of all the
  demo scope of the register lock and check (F4, D-08) and the open rest of the read-back of the safe state (G1,
  D-03: closed on the host, open for the fault handlers' path and on the board).
- **A-03 and A-04 "not supported"** is the honest stage-1 result of the strict separation (concept 2.2, AoU-23). They
  do not make the element unusable: an integrator whose item has such goals provides the detection himself. **Open
  point O2 resolved 2026-09-30** (AN-101, decision cloud-0930-sys-77, variant 'einengen'): C-00's own wording is
  narrowed to the goals stage 1 covers; ASG-03 and ASG-04 stay cited in the basis (concept.md 2.2 names all seven
  assumed safety goals, and the test ties the basis to that list) but only through AoU-23 to the integrator. This
  changes what C-00 asserts, not the arithmetic of section 1.1: the top verdict is still bounded by the weakest of
  *all* nine sub-claims, so A-03/A-04 being `not supported` still keeps C-00 at its floor until D-01, D-02 and D-21
  are removed.
- **A-08 "partly supported"**: the assumptions are stated and reach the integrator through the safety manual
  (`safety-manual.md` issue 2, section 7 with the assumptions of use and section 8 with the configuration rules); its
  confirmation review CM-10 is closed by the user without independence (D-02).

### 3.1 What the integrator supplies himself

An integrator reads his duties in the **safety manual** (`safety-manual.md` issue 2): the assumptions of use with how
to fulfil them (section 7), the configuration rules (section 8) and the tailoring and process (section 12). Two
consequences of this case belong there and are named here as well:

- **The independent confirmation.** No confirmation measure of neXtBSW is performed with independence (D-01): the
  integrator takes over under his own management of functional safety that there is no independent confirmation
  review yet, as well as no quality management system, no competence evidence and no assessment - **AoU-35** (safety
  manual section 7, safety plan T-03, T-09, T-11).
- **The tailored activities.** What the project does not perform - item definition and hazard analysis (T-01),
  hardware development (T-02), QMS and competence evidence (T-03), the impact analysis at item level (T-04), the
  development interface agreement (T-05), the evaluation of hardware elements (T-06), proven in use (T-07), ASIL
  decomposition (T-08), the assessment (T-09), tests on his ECU (T-10) and the independence of the confirmation
  measures (T-11, a deviation) - is listed with the integrator's side in the safety plan section 3 and in the safety
  manual section 12; D-14 and D-21 name the consequences that stay with the element.

Board records are evidence of the reference configuration (T-10): the integrator repeats the target tests on his ECU
(section 5.2).

---

## 4. Process argument

Basis: the activities of the safety plan section 4; together the four rows cite every activity exactly once
(checked). The evidence cites every work product the plan names for these activities (checked). A row can be
`supported` only when every activity of its basis is `done` or `tailored` in the plan.

<!-- case-process:start -->
| ID | Claim | Strategy | Basis | Evidence | Deficits | Verdict |
|---|---|---|---|---|---|---|
| A-P2 | C-09 | Part 2 as far as a one-person open-source project can apply it: written rules, a safety plan with roles and tailoring, configuration management with release rules, this case; what cannot be provided (QMS, competence, independent confirmation) is stated and handed on, not assumed | P2-01, P2-02, P2-03, P2-04, P2-05, P2-06, P2-07, P2-08, P2-09, P2-10, P2-11, P2-12, P2-13, P2-14, P2-15, P2-16 | EW-01, EW-02, EW-03, EW-04, EW-05, EW-06, EW-07, EW-08, EW-09, EW-10, EW-11, ED-01, ED-02, ED-03 | D-01, D-14, D-22 | partly supported |
| A-P6 | C-09 | Part 6 at ASIL B in full for the element: SSR from the assumed goals, HSI, architecture, unit design and verification, integration and target tests, configuration and calibration data — each activity with its work product; the open ones are tasks | P6-01, P6-02, P6-03, P6-04, P6-05, P6-06, P6-07, P6-08, P6-09, P6-10, P6-11, P6-12, P6-13, P6-14, P6-15, P6-16, P6-17, P6-18, P6-19, P6-20, P6-21, P6-22 | EW-05, EW-07, EW-11, EW-12, EW-13, EW-14, EW-15, EW-16, EW-17, EW-18, EW-19, EW-20, EW-21, EW-22, EW-23, EW-24, EW-25, EW-26, EW-27, EW-28, EW-29, EW-30, EW-31, EW-35, EW-36, ED-04, ED-05, ED-06 | D-09, D-10, D-12, D-17, D-18, D-20 | partly supported |
| A-P8 | C-09 | Supporting processes of part 8: requirements management with generated trace, configuration and change management, verification recorded per baseline, tool confidence, qualification of components; development interface agreement, hardware evaluation and proven in use tailored | P8-01, P8-02, P8-03, P8-04, P8-05, P8-06, P8-07, P8-08, P8-09, P8-10, P8-11 | EW-04, EW-07, EW-09, EW-11, EW-15, EW-17, EW-25, EW-26, EW-32, EW-33, EW-34, ER-01, ER-02 | D-10, D-11, D-12 | partly supported |
| A-P9 | C-09 | Part 9: no decomposition at stage 1; coexistence per QM module or ASIL B for all; dependent failure analysis and safety analyses of the mechanisms | P9-01, P9-02, P9-03, P9-04 | EW-20, EW-21, EW-22 | D-09, D-17 | partly supported |
<!-- case-process:end -->

---

## 5. Evidence

Columns: **Kind** — `work product`, `analysis`, `test`, `board record`, `decision`, `record`. **Requirements** — for
tests and board records the SSR or AoU the item supports (`—` otherwise). **Since** — the first baseline that
contains the item: `v0.1.0`, or `next` for the working state after it; `—` for a missing item. **Status** —
`available` (every reference resolves), `partial` (references resolve, the item is not complete; the task completes
it), `missing` (not there yet; the task produces it). **Task** — an open task of `doc/todo.yaml` for `partial` and
`missing`, `—` otherwise.

**Not published, available on request** (section 1.3, Z2 b and Z10 a of CM-05): the review records and inspections
(EW-11), the trace matrix (EW-17), the board captures (EB-01 … EB-13), the task list (ED-01 … ED-06) and its checker
(EW-32) are evidence in the private repository, `CLAUDE.md` (EW-01) is exported only in a cleaned form; the status
column gives their status there. That they are not published raises no verdict.

### 5.1 Work products, analyses, decisions and records

<!-- case-evidence-work:start -->
| ID | Evidence | Kind | Reference | Requirements | Since | Status | Task |
|---|---|---|---|---|---|---|---|
| EW-01 | Written rules of the project: lifecycle, roles, work products, contribution rules | work product | `CLAUDE.md`, `CONTRIBUTING.md` | — | v0.1.0 | available | — |
| EW-02 | Safety plan, issue 2 (released 2026-10-02 in the re-check of CM-01, record CR-01-1): claim, roles, tailoring T-01 … T-11, 53 activities, confirmation measures, release R1–R6 | work product | `doc/safety/safety-plan.md` | — | v1.0.0-rc1 | available | — |
| EW-03 | Interplay of functional safety and cybersecurity: field interfaces, rules, assumptions of use AoU-SEC-1 … AoU-SEC-8 | work product | `doc/safety/security-interplay.md` | — | v1.0.0-rc1 | available | — |
| EW-04 | Register of safety anomalies and formal document elements | work product | `doc/safety/anomaly-register.md` | — | v1.0.0-rc1 | available | — |
| EW-05 | Safety manual with assumptions of use, coverage per mechanism, configuration rules (issue 2, released 2026-10-04 in the re-check of CM-10, record CR-10-1, its updates accepted in re-check 2; F-02 closed with DOC-50 in re-check 3, 2026-10-08; issue 1 accepted by the user 2026-09-30) | work product | `doc/safety/safety-manual.md` | — | v1.0.0-rc1 | available | — |
| EW-06 | Classification of the OpenBSW copies (unchanged, modified) as input to the impact analysis of reused elements | work product | `tools/openbswsync/copies.yaml` | — | v0.1.0 | available | — |
| EW-07 | Route and qualification per pre-existing component | work product | `doc/safety/component-qualification.md` | — | v1.0.0-rc1 | partial | SYS-106, SYS-109 |
| EW-08 | Safety concept, issue 3 (released 2026-10-04 in the re-check of CM-07, record CR-07-1): element, assumed safety goals with stage-1 coverage, safe states, assumptions of use, mechanisms, timing, demarcation, gaps | analysis | `doc/safety/concept.md::### 2.2 Assumed safety goals`, `doc/safety/concept.md::## 4. Assumptions of use (AoU)` | — | v0.1.0 | available | — |
| EW-09 | Configuration management plan and baselines | work product | `doc/safety/cm-plan.md` | — | v1.0.0-rc1 | available | — |
| EW-10 | This safety case, issue 2 (after CM-05, record CR-05-1, accepted 2026-10-04 in its re-check; issue 1 accepted 2026-10-02) | work product | `doc/safety/safety-case.md` | — | v1.0.0-rc1 | partial | SYS-118 |
| EW-11 | Review procedure and records of confirmation reviews and inspections - the records CR-01-1, CR-10-1, CR-07-1, CR-04-1, CR-02-1 and CR-05-1 under `doc/safety/reviews/records`, the code inspections INS-*-1 (not published, available on request; section 1.3) | record | `doc/safety/reviews` | — | v1.0.0-rc1 | available | — |
| EW-12 | Static analysis: guidelines, rule set, deviations, gating | work product | `doc/safety/static-analysis.md`, `tools/misra/rules.yaml` | — | v0.1.0 | partial | PLAT-91 |
| EW-13 | Pinned tool versions with checksums | work product | `tools/ci/versions.toml` | — | v0.1.0 | available | — |
| EW-14 | Development environment document | work product | `doc/safety/dev-environment.md` | — | v1.0.0-rc1 | available | — |
| EW-15 | Software safety requirements, 59 SSR with ASIL, derivation, code and verification method (`doc/safety/requirements.yaml` until DOC-21 step 3 moved it to StrictDoc) | work product | `doc/requirements/safety/ssr.sdoc` | — | v0.1.0 | partial | SYS-100 |
| EW-16 | Hardware-software interface of the s32k1xx family for the stage-1 mechanisms | work product | `doc/safety/hsi-s32k1xx.md` | — | v1.0.0-rc1 | partial | SYS-93 |
| EW-17 | Traceability matrix goal → SSR → mechanism → code → test, generated and checked in CI (not published, available on request; section 1.3) | work product | `doc/safety/traceability.md` | — | v0.1.0 | available | — |
| EW-18 | Architecture of neXtBSW (layers, roles, configuration levels) | work product | `doc/architecture/README.md` | — | v0.1.0 | available | — |
| EW-19 | Software architecture specification of the safety part with worst-case stack | work product | `doc/safety/sw-architecture.md` | — | v1.0.0-rc1 | available | — |
| EW-20 | Coexistence argument per QM module: classification of every module of the image, arguments per kind of interference, findings CX-1 … CX-9; FFI evidence open | work product | `doc/safety/coexistence.md` | — | v1.0.0-rc1 | partial | SYS-21 |
| EW-21 | Failure analysis of the mechanisms (FMEA, issue 2, released 2026-10-04 in the re-check of CM-02, record CR-02-1) with coverage classes, fault tolerance times and board results | analysis | `doc/safety/fmea.md`, `doc/safety/fmea.md::### 8.1 Board results` | — | v0.1.0 | partial | SYS-100 |
| EW-22 | Dependent failure analysis of the stage-1 mechanisms | analysis | `doc/safety/dfa.md` | — | v1.0.0-rc1 | partial | SYS-195 |
| EW-23 | Resource checks: schedulability analysis and image checks | work product | `tools/schedana`, `tools/elfcheck` | — | v0.1.0 | available | — |
| EW-24 | Unit design of the safety units that have one | work product | `platforms/s32k1xx/port/safety/safeSupervisor/doc/safeSupervisor.md`, `platforms/s32k1xx/port/safety/safeLifecycle/doc/safeLifecycle.md`, `platforms/s32k1xx/port/safety/safeMemory/doc/safeMemory.md`, `platforms/s32k1xx/port/safety/safeSystem/doc/safeSystem.md` | — | v0.1.0 | available | — |
| EW-25 | Test list with the requirement each entry verifies | work product | `tools/test/tests.yaml` | — | v0.1.0 | available | — |
| EW-26 | Verification plan (issue 2, released 2026-10-04 in the re-check of CM-04, record CR-04-1) per level with test-case derivation and host/target difference analysis | work product | `doc/safety/verification-plan.md` | — | v1.0.0-rc1 | available | — |
| EW-27 | Statement and branch coverage of the host tests | work product | `tools/test/coverage.yaml`, `tools/test/coverage.py` | — | v0.1.0 | partial | TOOL-44 |
| EW-28 | Console commands, among them the test and fault-injection commands a product build must not contain; SYS-74: product build without them (`NX_PRODUCT_BUILD`), checked in the image (`target-product`); the service commands that stay in the product image assessed and the rest removed by SYS-119 (coexistence CX-9) | work product | `ecu/consoleCommands` | — | v0.1.0 | available | — |
| EW-29 | Board test tool and its captures | work product | `tools/boardtest` | — | v0.1.0 | partial | SYS-75 |
| EW-30 | Configuration data: reference of the project file and the checks over it | work product | `doc/reference/project.md`, `tools/nxconfig/checks.py` | — | v0.1.0 | available | — |
| EW-31 | Calibration data: segment with CRC and layout check, generated plausibility checks | work product | `tools/calseg`, `tools/a2lgen` | — | v1.0.0-rc1 | available | — |
| EW-32 | Change management: task list and its checker | work product | `doc/todo.yaml`, `tools/todo/todoplan.py` | — | v0.1.0 | available | — |
| EW-33 | CI pipeline definition | work product | `tools/ci/pipeline.toml` | — | v0.1.0 | available | — |
| EW-34 | Tool classification with TCL per use case and proposed assumptions of use | analysis | `doc/safety/tool-classification.md` | — | v0.1.0 | partial | PLAT-83, SYS-88 |
| EW-36 | Configuration range of the SEooC: what the element is argued for and what the integrator must keep inside (accepted by the user 2026-10-02) | work product | `doc/safety/config-range.md` | — | v1.0.0-rc1 | available | — |
| EW-35 | Unit design of the safety units (watchdog, hard-fault handler and the units described so far; the remaining units are proposal U1) | work product | `doc/safety/unit-design.md` | — | v1.0.0-rc1 | partial | SYS-183 |
| ED-01 | Decision iso-q2: the claim (part 6 at ASIL B, without the management part) | decision | `doc/todo.yaml::iso-q2`, `doc/safety/safety-plan.md::iso-q2` | — | v1.0.0-rc1 | available | — |
| ED-02 | Decision iso-q3: a release is an annotated tag `vX.Y.Z` on `master` with its preconditions | decision | `doc/todo.yaml::iso-q3`, `doc/safety/safety-plan.md::iso-q3` | — | v1.0.0-rc1 | available | — |
| ED-03 | Decision iso-q1: the independent reviewer is named later; until then the user performs the confirmation reviews himself, without independence (AN-093, safety plan T-11, D-01) | decision | `doc/todo.yaml::iso-q1` | — | v0.1.0 | available | — |
| ED-04 | Decision iso-q4: QM parts only with a freedom-from-interference argument per module | decision | `doc/todo.yaml::iso-q4` | — | v0.1.0 | available | — |
| ED-05 | Decision iso-q5: FreeRTOS route decided after the comparison | decision | `doc/todo.yaml::iso-q5` | — | v0.1.0 | available | — |
| ED-06 | Decision iso-q6: single exit rule re-enabled for the safety units | decision | `doc/todo.yaml::iso-q6` | — | v0.1.0 | available | — |
| ED-07 | Decision abn-sys19: stage 1 credits base-software mechanisms only | decision | `doc/safety/concept.md::abn-sys19` | — | v0.1.0 | available | — |
| ED-08 | Decision sys23-halt: S1 held without reset for faults a reset cannot remove | decision | `doc/safety/concept.md::sys23-halt` | — | v0.1.0 | available | — |
| ER-01 | Baseline `v0.1.0` recorded as a development release without safety claim | record | `doc/safety/cm-plan.md::development release` | — | v1.0.0-rc1 | available | — |
| ER-02 | Archived CI report of a baseline, bound to its tag (`tools.ci release`, SYS-67; v0.2.0 carries the SHA-256 of its report in the tag message; `v1.0.0-rc1` is the first tag whose report is bound with `tools.ci release`, `ci-out-v1.0.0-rc1.zip`, SHA-256 `523063d2…517da7` in the tag message, increment I-1); the report as release asset is open | record | `tools/ci/ci.py` | — | v0.1.0 | partial | TOOL-212 |
<!-- case-evidence-work:end -->

### 5.2 Board records

All on the reference eval board (`evalboard_s32k144_demo`, S32K144 at 80 MHz), captures committed under
`tools/boardtest/captures/evalboard_s32k144_demo/` (configuration item CI-11 of `cm-plan.md`). The paths below are
relative to the repository root. Board records are evidence of the **reference configuration** (tailoring T-10); the
integrator repeats the target tests on his ECU.

<!-- case-evidence-board:start -->
| ID | Evidence | Kind | Reference | Requirements | Since | Status | Task |
|---|---|---|---|---|---|---|---|
| EB-01 | fmea B1: one ROM-check pass at idle priority took at most 5 574 µs without load (2026-09-22) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/112_sys45_b1_probe_boot.console.txt`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/113_sys45_b1_read.jlink.log` | SSR-024, SSR-025 | v0.1.0 | available | — |
| EB-02 | fmea B2: restart time last to first application frame; the value of AoU-05 after `fmea.md` issue 2 8.1/8.2 is at most **1 077 ms** through the watchdog path and **858 ms** through the fault handler (re-measured 2026-09-30, EB-13), confirmed with bootloader 0.3.9 (2026-10-03: 883 … 933 ms and 699.7 ms, EB-13); the captures of this row are the first run of 2026-09-22 (1 027 ms and 852 ms) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/96_sys45_b2_wdg.console.txt::reset loop: 1 of 3`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/96_sys45_b2_mpu.console.txt` | AoU-05 | v0.1.0 | available | — |
| EB-03 | fmea B3: safe outputs walk to DRV_EN bounded by 21 µs at 24 V, order of the switch-off on the console (2026-09-22) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/119_sys45_b3_enable.console.txt`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/121_sys45_b3_dst.console.txt` | SSR-005 | v0.1.0 | available | — |
| EB-04 | fmea B4: 10 min under load, safety cycle 9 982 … 10 019 µs over 63 798 cycles, no period violation, ROM check passed every 60 s (2026-09-22) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/118_sys45_b4_load.console.txt`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/118_sys45_b4_read.jlink.log` | SSR-014, SSR-018, SSR-067 | v0.1.0 | available | — |
| EB-05 | fmea B7: cycle period supervision on the target — timer stopped, safety task blocked 5 ms and more, 30 s debugger halt without report (2026-09-22) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/28_sys40_b7a.console.txt`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/116_sys40_b7b_20ms.console.txt`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/25_sys40_b7c.console.txt` | SSR-018 | v0.1.0 | available | — |
| EB-06 | fmea B6: watchdog configuration fault injected while running → reset with last cause 0x103; deadman configuration faults → last cause 0x110; 15 of 15 injections with the console line before the reset (2026-09-22) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/148_sys51_p3_wdogint.raw.txt::last cause 0x103`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/144_sys51_deadman_tval.raw.txt`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/164_sys59_wdogint_r1.raw.txt` | SSR-012, SSR-066 | v0.1.0 | available | — |
| EB-07 | Watchdog start-up test failure → S1 held until the power cycle, normal start afterwards (SYS-23, 2026-09-21) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-21_board/28_sys23_wdg_startup_fail.txt::SAFE STATE HELD (WATCHDOG_STARTUP_CHECK_FAILURE)`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-21_board/29_sys23_after_power_cycle.console.txt` | SSR-003, SSR-017 | v0.1.0 | available | — |
| EB-08 | ROM check failure → S1 held, repeated at every start (SYS-39, 2026-09-22) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/20_sys39_romfail.console.txt::SAFE STATE HELD (ROM_CHECK_FAILURE)` | SSR-003, SSR-024 | v0.1.0 | available | — |
| EB-09 | fmea B10: third safety reset in a row → S1 held with RESET_LOOP_LIMIT, also after the reset button (SYS-43, 2026-09-22 and 2026-09-23) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/63_sys43_b10_wdg3.console.txt::SAFE STATE HELD (RESET_LOOP_LIMIT`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-23_board/15_sys43_taster_held.raw.txt::SAFE STATE HELD (RESET_LOOP_LIMIT` | SSR-065 | v0.1.0 | available | — |
| EB-10 | Register check: the SAFETY_TEST pin reconfigured → CHECK_REGISTER_ERROR, safe state, gate driver disabled (SYS-06, 2026-09-21) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-21_board/15_sys06_dst.console.txt::CHECK_REGISTER_ERROR (enter safe state)` | SSR-034 | v0.1.0 | available | — |
| EB-11 | fmea B8: pin control register of the locked role pin read back with the lock bit set and MUX 001 (`0x00008100`, SYS-42, 2026-09-22) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/02_sys42_b8_jlink.txt::4004B020 = 00008100` | SSR-033 | v0.1.0 | available | — |
| EB-12 | fmea B15: read-back of S1 on the board, run ad hoc (SYS-30, 2026-09-30 evening): a J-Link write to the enable of the gate driver in S1 ends in `LOCK_REGISTER_ERROR` (register 117440513), a single KL30B write is taken back by the hold, a loop of 20 writes resets (register 117440514); repeated with records as suite case BT-S1-03 on the board list of Release R1, on the release candidate `bce1202a` on 2026-10-04 (increment I-1, D-15) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-30_board/abend/sys30_b15_pin_drven.console.txt::LOCK_REGISTER_ERROR, register 117440513`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-30_board/abend/sys30_b15_pin_kl30b_loop.console.txt::register 117440514`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-30_board/abend/sys30_b15a.console.txt` | SSR-060 | v1.0.0-rc1 | available | — |
| EB-13 | fmea B2 re-measured (DOC-19 D7, SYS-149, 2026-09-30, CAN trace and oscilloscope): `safety wdg` up to 1 077 ms, `safety mpu` up to 858 ms - the values of AoU-05; with bootloader 0.3.9 (BL-33, 2026-10-03) `safety wdg` 883 … 933 ms in 4 runs, `safety mpu` 699.7 ms in 3 runs (`fmea.md` 8.1, 8.2) | board record | `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-30_board/abend/d7_wdg_3_con.console.txt::reset loop: 1 of 3`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-30_board/abend/d7_mpu_2_con.console.txt`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-10-03_board/bl33/m1_wdg1_gap.txt`, `tools/boardtest/captures/evalboard_s32k144_demo/2026-10-03_board/bl33/m1_mpu1_gap.txt` | AoU-05 | v1.0.0-rc1 | available | — |
<!-- case-evidence-board:end -->

EB-11 was a finding of issue 1 (proposal S2, AN-098): the capture showed the expected register value while `fmea.md`
8.1 listed B8 as "not run in this round". `fmea.md` issue 2 section 8.2 records B8 with this capture (DOC-55); AN-098
is closed with the confirmation review CM-05 (record CR-05-1, Z8 a).

**Board results not linked yet** (decision Z6 a of CM-05): the captures since 2026-09-29 beyond EB-12 and EB-13 - among
them the board part of SYS-95 (dependent failure analysis, 2026-10-03/04, D-16), the lock times of SYS-172 and the
suite run of 2026-10-02 - are linked after Release R1. B16 (a), (c), (d) and the reset at falling supply (LVD, SM-59)
at a lab supply were not run; they are a known gap of Release R1 (D-15, SYS-198). The board run of Release R1 on
the release candidate (SYS-167, `2026-10-04_rc1-candidate`, 2026-10-04) is recorded in increment I-1 and in D-15
(booking after the tag, DOC-84); its captures are linked as EB rows after Release R1 with the others.

### 5.3 Tests

One row per software safety requirement (`ET-nnn` belongs to `SSR-nnn`). The reference lists test entries of
`tools/test/tests.yaml` that name the requirement; the test checks each of them. The full list per requirement is in
the generated `traceability.md` (not published, available on request) (not published, available on request; section 1.3).

<!-- case-evidence-tests:start -->
| ID | Evidence | Kind | Reference | Requirements | Since | Status | Task |
|---|---|---|---|---|---|---|---|
| ET-001 | tests that name SSR-001 (Reaction decided in one place) | test | `test:safe-console`, `test:safety-reset-console`, `test:safety-reset-console-mutations`, `test:kernel-fault-reaction`, `test:kernel-fault-reaction-mutations`, `test:safe-supervisor`, `test:safe-supervisor-bare` | SSR-001 | v0.1.0 | available | — |
| ET-002 | tests that name SSR-002 (De-energise before every safety reset) | test | `test:safety-reset-console`, `test:safety-reset-console-mutations`, `test:kernel-fault-reaction`, `test:kernel-fault-reaction-mutations`, `test:safe-supervisor` | SSR-002 | v0.1.0 | available | — |
| ET-003 | tests that name SSR-003 (Safe state held without reset for faults a reset cannot remove) | test | `test:safe-console`, `test:safe-state-hold`, `test:safe-supervisor`, `test:safe-supervisor-bare` | SSR-003 | v0.1.0 | available | — |
| ET-004 | tests that name SSR-004 (Leaving the safe state never re-energises a power output) | test | `test:safe-state`, `test:safe-state-hold` | SSR-004 | v0.1.0 | available | — |
| ET-005 | tests that name SSR-005 (One safe outputs table for every switch-off path) | test | `test:bswgen`, `test:debug-halt-guard`, `test:safe-outputs-order`, `test:safe-state`, `test:safe-state-hold` | SSR-005 | v0.1.0 | available | — |
| ET-006 | tests that name SSR-006 (Safe state on a board without power outputs) | test | `test:safe-state` | SSR-006 | v0.1.0 | available | — |
| ET-007 | tests that name SSR-007 (One power-stage adapter per safe_off output, enforced by the build) | test | `test:bswgen`, `test:target-demo` | SSR-007 | v0.1.0 | available | — |
| ET-010 | tests that name SSR-010 (MCU watchdog serviced by the safety task only) | test | `test:nxconfig-watchdog` | SSR-010 | v0.1.0 | available | — |
| ET-011 | tests that name SSR-011 (Watchdog timing relation is a checked property) | test | `test:nxconfig-watchdog` | SSR-011 | v0.1.0 | available | — |
| ET-012 | tests that name SSR-012 (Watchdog configuration re-checked at every service) | test | `test:safe-watchdog` | SSR-012 | v0.1.0 | available | — |
| ET-013 | tests that name SSR-013 (Watchdog over-service detected) | test | `test:safe-watchdog` | SSR-013 | v0.1.0 | available | — |
| ET-014 | tests that name SSR-014 (Control flow through the safety cycle supervised) | test | `test:safety-cycle` | SSR-014 | v0.1.0 | available | — |
| ET-015 | tests that name SSR-015 (RTE task and runnable deadlines reported) | test | `test:rte-deadline`, `test:safety-context`, `test:safety-context-elf`, `test:safe-supervisor-mpu` | SSR-015 | v0.1.0 | available | — |
| ET-016 | tests that name SSR-016 (Core halt or stall longer than the deadman time-out de-energises) | test | `test:lpit-deadman-check`, `test:safety-cycle`, `test:lpit-halt-deadman`, `test:debug-halt-guard`, `test:safe-outputs-order` | SSR-016 | v0.1.0 | available | — |
| ET-017 | tests that name SSR-017 (Watchdog hardware tested at every start) | test | `test:safe-supervisor` | SSR-017 | v0.1.0 | available | — |
| ET-018 | tests that name SSR-018 (Safety task period supervised against a hardware time base) | test | `test:safety-cycle`, `test:cycle-timer-off`, `test:cycle-period-check`, `test:lptmr-cycle-timer`, `test:cycle-period-constants` | SSR-018 | v0.1.0 | available | — |
| ET-020 | tests that name SSR-020 (Safety data writable only from the safety context) | test | `test:bswgen-mpu`, `test:safety-context`, `test:safety-context-elf`, `test:safe-supervisor-mpu` | SSR-020 | v0.1.0 | available | — |
| ET-021 | tests that name SSR-021 (Protection gate state checked on entry and exit of the safety cycle) | test | `test:safety-gate`, `test:safety-gate-mutations` | SSR-021 | v0.1.0 | available | — |
| ET-022 | tests that name SSR-022 (MPU enabled with the region count of the derivative) | test | `test:bswgen-mpu` | SSR-022 | v0.1.0 | available | — |
| ET-023 | tests that name SSR-023 (MPU region descriptors unchanged) | test | `test:bswgen-mpu` | SSR-023 | v0.1.0 | available | — |
| ET-024 | tests that name SSR-024 (ROM check of the application image) | test | `test:rom-check`, `test:rom-check-image` | SSR-024 | v0.1.0 | available | — |
| ET-025 | tests that name SSR-025 (Context in which the ROM check may run) | test | `test:rom-check` | SSR-025 | v0.1.0 | available | — |
| ET-026 | tests that name SSR-026 (Uncorrectable RAM ECC error detected) | test | `test:safe-memory-ecc` | SSR-026 | v0.1.0 | available | — |
| ET-027 | tests that name SSR-027 (Uncorrectable flash ECC error detected) | test | `test:safe-memory-ecc` | SSR-027 | v0.1.0 | available | — |
| ET-028 | tests that name SSR-028 (Fault handlers de-energise first) | test | `test:hard-fault-dump`, `test:safe-outputs-order` | SSR-028 | v0.1.0 | available | — |
| ET-030 | tests that name SSR-030 (ADC reference supervised against a configured band) | test | `test:nxconfig`, `test:bswgen`, `test:safe-system` | SSR-030 | v0.1.0 | available | — |
| ET-031 | tests that name SSR-031 (Loss of the ADC reference supervision detected) | test | `test:safe-system` | SSR-031 | v0.1.0 | available | — |
| ET-032 | tests that name SSR-032 (Internal supplies of the MCU supervised) | test | `test:safe-system` | SSR-032 | v0.1.0 | available | — |
| ET-033 | tests that name SSR-033 (Safety-relevant pin configuration locked at start-up) | test | `test:safe-io`, `test:bswgen-safe-io`, `test:safe-io-mutations` | SSR-033 | v0.1.0 | available | — |
| ET-034 | tests that name SSR-034 (Safety-relevant registers checked cyclically) | test | `test:safe-io`, `test:bswgen-safe-io`, `test:safe-io-mutations`, `test:safe-state-hold`, `test:safe-supervisor` | SSR-034 | v0.1.0 | available | — |
| ET-040 | tests that name SSR-040 (Quality qualifier for every analogue value) | test | `test:bswgen`, `test:rtegen`, `test:boardtest`, `test:adc-access`, `test:adc-access-averaging`, `test:demoswc-sil` | SSR-040 | v0.1.0 | available | — |
| ET-041 | tests that name SSR-041 (End-to-end protection of configured CAN signals) | test | `test:rtegen`, `test:e2e-ref`, `test:e2e-host`, `test:e2e-mutations`, `test:can-bus-state`, `test:e2e` | SSR-041 | v0.1.0 | available | — |
| ET-042 | tests that name SSR-042 (No transmission into an unhealthy bus) | test | `test:can-bus-state`, `test:can-phy-gate`, `test:can-console` | SSR-042 | v0.1.0 | available | — |
| ET-050 | tests that name SSR-050 (Start reason from two independent sources) | test | `test:ecustate`, `test:bsw-providers-sbc`, `test:bsw-providers-nosbc`, `test:bsw-providers-nosbc-nomcu` | SSR-050 | v0.1.0 | available | — |
| ET-051 | tests that name SSR-051 (Bootloader starts only a valid, compatible image) | test | `test:bl-s3-host`, `test:bl-platform-host`, `test:bootloader-host`, `test:canflash-tool`, `test:bl-update-host`, `test:bl-update-mutations` | SSR-051 | v0.1.0 | available | — |
| ET-052 | tests that name SSR-052 (Defined run-down with time-outs per step) | test | `test:ecustate` | SSR-052 | v0.1.0 | available | — |
| ET-060 | tests that name SSR-060 (Safe state verified by read-back) | test | `test:safe-io`, `test:bswgen-safe-readback` | SSR-060 | v1.0.0-rc1 | available | — |
| ET-061 | tests that name SSR-061 (Plausibility between two independent measurement paths) | test | — | SSR-061 | — | missing | SYS-31 |
| ET-062 | tests that name SSR-062 (Age supervision of safety-relevant signals) | test | `test:rtegen-age` | SSR-062 | v1.0.0-rc1 | partial | SYS-99 |
| ET-063 | tests that name SSR-063 (Safe state reaches bus-commanded actuators) | test | — | SSR-063 | — | missing | SYS-33 |
| ET-064 | tests that name SSR-064 (Device reports to the safe supervisor) | test | `test:safe-supervisor` | SSR-064 | v0.1.0 | available | — |
| ET-065 | tests that name SSR-065 (Limit on repeated safety resets) | test | `test:safe-supervisor` | SSR-065 | v0.1.0 | available | — |
| ET-066 | tests that name SSR-066 (Complete configuration checks of the supervision peripherals) | test | `test:safe-watchdog`, `test:lpit-deadman-check`, `test:safety-cycle` | SSR-066 | v0.1.0 | available | — |
| ET-067 | tests that name SSR-067 (ROM check repeated during operation) | test | `test:rom-check`, `test:rom-check-mutations`, `test:nvm-flash-write-bracket` | SSR-067 | v0.1.0 | available | — |
| ET-068 | tests that name SSR-068 (ECC supervision complete) | test | `test:safe-memory-ecc` | SSR-068 | v0.1.0 | available | — |
| ET-069 | tests that name SSR-069 (Read-failure limit for the internal supply channel) | test | `test:safe-system` | SSR-069 | v0.1.0 | available | — |
| ET-070 | tests that name SSR-070 (Register lock and check list derived from the configuration) | test | `test:safe-io`, `test:bswgen-safe-io` | SSR-070 | v0.1.0 | available | — |
| ET-080 | tests that name SSR-080 (Safe outputs table checked in the linked image against an independent expectation) | test | `test:safe-outputs-table`, `test:safe-outputs-table-charge`, `test:safe-outputs-table-bt148` | SSR-080 | v0.1.0 | available | — |
| ET-081 | tests that name SSR-081 (Change of a safety-relevant generated file detected) | test | `test:bswgen-reference`, `test:rtegen-reference`, `test:elfcheck-timing`, `test:elfcheck-timing-host`, `test:elfcheck-timing-demo`, `test:elfcheck-timing-bt148` | SSR-081 | v0.1.0 | available | — |
| ET-082 | tests that name SSR-082 (Configuration and generator identity recognisable in the image) | test | `test:nxconfig-identity`, `test:identity` | SSR-082 | v1.0.0-rc1 | available | — |
| ET-083 | tests that name SSR-083 (Memory layout of the linked images checked against the derivative) | test | `test:elfcheck`, `test:elfcheck-stm32`, `test:elfcheck-demo`, `test:elfcheck-bt148`, `test:elfcheck-s32k116` | SSR-083 | v0.1.0 | available | — |
| ET-084 | tests that name SSR-084 (CAN signal layout of every project checked against an independent decoder) | test | `test:dbcgen-projects` | SSR-084 | v0.1.0 | available | — |
| ET-086 | tests that name SSR-086 (Latent faults of the RAM error correction tested at every start) | test | `test:safe-memory-ecc-latent`, `test:safe-memory-ecc-latent-s32k11x`, `test:safe-memory-ecc-latent-mutations`, `test:safe-memory-ecc-latent-elf` | SSR-086 | v1.0.0-rc1 | available | — |
| ET-087 | tests that name SSR-087 (Degraded reaction switches only the mapped safe-output groups and keeps them until the power cycle) | test | `test:safe-supervisor-degraded`, `test:safe-supervisor-degraded-mutations`, `test:safe-state-degraded`, `test:rtegen-safety` | SSR-087 | v1.0.0-rc1 | available | — |
| ET-088 | tests that name SSR-088 (Only degradable events and independent groups can be mapped to the degraded reaction) | test | `test:nxconfig-degraded`, `test:safe-supervisor-degraded`, `test:safe-supervisor-degraded-mutations` | SSR-088 | v1.0.0-rc1 | available | — |
| ET-089 | tests that name SSR-089 (Selected safe state of a PWM group applied by every switch-off path, freewheel on any doubt) | test | `test:safe-state-select`, `test:bswgen-safe-state` | SSR-089 | v1.0.0-rc1 | available | — |
| ET-090 | tests that name SSR-090 (Configuration check of the safe states of a PWM group) | test | `test:nxconfig-safestate` | SSR-090 | v1.0.0-rc1 | available | — |
| ET-091 | tests that name SSR-091 (A bridge fault withdraws the active short before the reaction walks the table) | test | `test:safe-supervisor-bridge-fault`, `test:safe-supervisor-bridge-fault-mutations`, `test:nxconfig-safestate` | SSR-091 | v1.0.0-rc1 | available | — |
| ET-092 | tests that name SSR-092 (The family port keeps an active short that a switch-off path applied) | test | `test:short-hold`, `test:short-hold-mutations` | SSR-092 | v1.0.0-rc1 | available | — |
<!-- case-evidence-tests:end -->

---

## 6. Deficits

What the argument needs and does not have. **Affects** — the claims whose arguments must cite the deficit (checked
both ways). **Task** — the task that removes it (open in `doc/todo.yaml`), or the tailoring of the safety plan that
hands it on for good.

<!-- case-deficits:start -->
| ID | Deficit | Affects | Task | Consequence until removed |
|---|---|---|---|---|
| D-01 | No independent confirmation review: no independent reviewer is named (iso-q1); since 2026-09-30 the user performs the confirmation measures himself (AN-093, safety plan T-11, deviation D-01 of `doc/safety/reviews/README.md`), without independence | C-00, C-09 | SYS-118, T-11 | no baseline can be a safety release (R5); no working state gets an ASIL statement until an independent review; every verdict of this case is the author side's |
| D-02 | The confirmation review of the safety manual, CM-10, is closed (D-01) - by the user without independence (record `doc/safety/reviews/records/CR-10-1.md`, `accepted`; issue 2 released 2026-10-04 in its re-check, F-01 and F-03 closed; F-04 and F-05 closed and the updates of issue 2 accepted in re-check 2 of 2026-10-04; F-02 (AN-134), the typical coverage classes of the former re-based column of section 5, replaced by DOC-50 with a reference to the clause and neXtBSW's own rating E1 … E3 and closed in re-check 3 of 2026-10-08); the times of D7 are re-measured on the board (manual section 4) | C-00, C-08 | SYS-118 | the integrator relies on a manual confirmed without independence (D-01) |
| D-03 | Gap G1: the read-back of the safe state is closed on the host for S1, S1 held, the groups of SD and the S1 + S2 sequence (SYS-30, SYS-138); open for the hard fault and watchdog handlers and the halt deadman of the family port, and on the board (B15 run ad hoc, EB-12; B16 not run, D-15) | C-01, C-02 | SYS-30 | a switch-off on the path of the fault handlers or the deadman that does not act is not detected by the element; the board proof is open |
| D-04 | Gap G2: no plausibility between two independent measurement paths | C-01, C-03 | SYS-31 | commanded and measured behaviour are not compared by the base software |
| D-05 | Gap G3: the age supervision of values from on-board devices (SYS-32) reports stale values in the RTE, but no event reaches the safe supervisor | C-03, C-05 | SYS-99 | a device value that stops updating is only flagged to the application, the base software does not react |
| D-06 | Gap G4: the safe state does not reach actuators switched by a bus command | C-01, C-04 | SYS-33 | balancing and SPI enables are outside S1 |
| D-07 | Device diagnostics are stage 2 and not credited (AoU-21, AoU-23) | C-01, C-03, C-04 | SYS-28 | the integrator argues device diagnostics himself |
| D-08 | Register lock and cyclic register check still at demo scope (F4); SSR-033, SSR-034, SSR-070 partial | C-01, C-02, C-06 | SYS-55 | registers outside the list (clock, PWM pin mux, FTM) are not checked |
| D-09 | Safety and QM software share one address space; no freedom-from-interference argument (AoU-17) | C-06, C-09 | SYS-61, SYS-21 | the integrator treats all application code at ASIL B or partitions it himself |
| D-10 | Pre-existing software (FreeRTOS, newlib, ETL, CMSIS and the unchanged OpenBSW parts other than `safeMonitor`) is neither qualified nor developed per part 6; `safeMonitor` has its first report (QR-01, SYS-62); of the further reports of the plan (`component-qualification.md` 4.2) only parts of QR-03 and QR-01 have a task (SYS-106, SYS-109), the rest is proposed (safety plan N8) and the FreeRTOS package is postponed (anomaly register AN-057) | C-09 | SYS-106, SYS-109 | AoU-20 applies |
| D-11 | Qualification reports of the tools with TCL 2 are missing | C-09 | PLAT-83, SYS-88 | release precondition R4 cannot be met |
| D-12 | The verification plan and the CI report archived per baseline with commit, tree hash and `git describe` exist (SYS-67); its confirmation review CM-04 is performed (D-01) — by the user without independence (record `reviews/records/CR-04-1.md`, `accepted` after the re-check of 2026-10-04, CM-04 closed, issue 2 by DOC-54 released; VP-10 and TOOL-228 open until before the first safety release), and no report is bound to a tag yet (the first is Release R1, `v1.0.0-rc1`, verification plan 9.1) | C-09 | SYS-118, DOC-54 | a test entry is evidence of a defined test, not of a passed one for a given baseline (R2) |
| D-14 | No quality management system and no evidence of competence (tailoring T-03) | C-09 | T-03 | stated to the integrator as assumption of use through the safety manual; permanent for the element |
| D-15 | Board verification named in `ssr.sdoc` has board records on one image only for the cases of the demo project: the board list of Release R1 (SYS-167, verification plan 9.1) ran on the release candidate `bce1202a` (2026-10-04, with the user, folder `2026-10-04_rc1-candidate`) without a firmware finding - 21 of 25 suite cases passed, among them BT-SLEEP-01 (SSR-052 (a), (b)); **BT-S1-03** (SSR-060, read-back of S1 with the pin cases, the repeat of B15/EB-12) passed with records; BT-S1-03 and BT-RUN-06 repeated and passed after the suite correction SYS-199; BT-S1-01 judged passed with a tool finding (CM-06 Z8 a), repeated with the corrected tool on the tagged image by SYS-200 and SYS-201 together with BT-RUN-08 and the pin cases of BT-S1-03; F4 in six runs. The run counts for the tagged image `v1.0.0-rc1` under the exception VP-6 (b) (CM-06 Z7 a: the images differ in the repository identification only); the rest run on the tagged image (2026-10-05, image SHA-256 `1583be99…c7ba45`) passed 9 of 9 automated cases and F4 in six runs (702.7 ms MPU path, 876 … 926 ms watchdog path) without a firmware finding. The cases are not yet linked as EB rows (after Release R1, section 5.2). The earlier records stay linked per case in `doc/safety/board-test-spec.md` (SYS-75, column "Earlier records"; records of fmea B9 and B11 exist in `tools/boardtest/captures`); SSR-040 (no qualifier port in the demo), SSR-052 (c) and the cases for other projects have none; fmea B5 not run; **B16 (a), (c), (d) and the reset at falling supply (LVD, SM-59)** at a lab supply are a known gap of Release R1, recorded after R1 with **SYS-198** (CM-02 re-check BRD a) | C-01, C-02, C-03, C-05, C-06, C-07 | SYS-75, SYS-167, SYS-198, SYS-201 | those requirements rest on host and build tests and on the board list of the release candidate in this issue; the LVD reset and B16 (a), (c), (d) are not shown for Release R1, BT-S1-01 rests on the tester's confirmation until SYS-201 |
| D-16 | Findings of the dependent failure analysis implemented (SYS-95, board 2026-10-03/04) except the residuals: SIRC failure without walk, stopped system time not detected by the safety task, mechanisms after the DFA baseline not assigned (O6) | C-06 | SYS-195 | the independence the mechanisms rely on is argued with these residuals open |
| D-17 | The safety analysis beyond the mechanisms (RTE, ecuState, CAN, NvM, bootloader; SYS-71) and the failure analysis exist; their confirmation review CM-02 is performed (D-01) — by the user without independence (record `reviews/records/CR-02-1.md`, `accepted` after the re-check of 2026-10-04, CM-02 closed, issue 2 of both by DOC-55 released); open ASA findings and the re-basing M2 with DOC-56 after release R1 | C-06, C-07, C-09 | SYS-118, DOC-56 | eight failure modes without SSR (P-SSR-1 … P-SSR-7) and open ASA findings until DOC-56 (CM-02 re-check D17 a) |
| D-18 | The product image keeps the service console commands that SYS-119 assessed as needed in the field (`nvm write`, `kl30b off`, `gd off` and the read commands); SYS-74 and SYS-119 removed the test, fault-injection and register-write commands from `NX_PRODUCT_BUILD`, checked in the image and on the board. The assessment has had no confirmation review | C-06, C-09 | SYS-118 | the released software contains functions that are not specified for it |
| D-20 | The code inspection of the safety units is performed (SYS-69, records `doc/safety/reviews/code-inspection/INS-*-1.md`, accepted in the safety review of 2026-10-02); its major findings are corrected by SYS-170, SYS-172, SYS-173, SYS-174 and BL-36 (done), the last one, the cyclic check of MPU descriptor 0 and of the full WORD2 (S171-3), waits for SYS-171 (in review) | C-09 | SYS-171 | until SYS-171 is merged, the MPU unit carries that open finding |
| D-21 | No baseline is a safety release; `v0.1.0` and `v0.2.0` are development releases (release preconditions R4 and R5 open) | C-00 | PLAT-83, SYS-118 | the case cannot be cited for a release yet (R1–R6) |
| D-22 | Deviation for release R1 (decision of the safety responsible 2026-10-03, safety plan section 5): the confirmation measures CM-03, CM-08, CM-09 and CM-11 are performed after R1. CM-03 waits for AN-074 (completeness of the DFA topic list, needs a reader of the licensed standard; the DFA findings are implemented by SYS-95), CM-08 for the qualification reports of PLAT-83, CM-09 for SYS-124 (FreeRTOS package) and the release of `component-qualification.md`, CM-11 because the audit comes once before the first ASIL B release and needs the records of CM-01 ... CM-10. Lifted before the first release with an ASIL B claim (condition restated by the decision of the safety responsible 2026-10-08, E1 b, safety plan issue 3, SYS-207, released in the delta re-check of CM-01 on 2026-10-08; before: "before the first public release or the first release with an ASIL B claim, whichever comes first") | C-00, C-09 | SYS-118, SYS-124, PLAT-83 | R1 and every later release until the four measures are performed are development releases without a safety claim; they may be distributed privately or publicly (the public repository from `v1.0.0-rc2` on); each recipient gets this statement with the safety manual - it stands in the `README.md` of the repository and in every release note (safety plan section 5, C2); no safety release until the four measures are performed |
<!-- case-deficits:end -->

---

## 7. Increments per release

The case grows with the releases: each increment names the baseline, its safety status (cm-plan section 4.2) and
what changed in the case. The column "Since" of section 5 refers to this table; the test checks, when the tag is
present in the clone, that every item marked `v0.1.0` resolves in the tree of that tag and every item marked `next`
does not.

<!-- case-increments:start -->
| Increment | Baseline | Commit | Safety status | Change to the case |
|---|---|---|---|---|
| I-0 | v0.1.0 | ed48460 | development release | no safety case existed; 84 of the evidence items were already present (the CI report tool of ER-02 counted since 2026-10-02) in this baseline |
| I-1 | v1.0.0-rc1 | 80962d9f | development release (**Release R1**, no safety claim; CM-06 record CR-06-1 `accepted with conditions`, released with conditions 2026-10-05) | **Increment of Release R1 `v1.0.0-rc1`** (changes since `v0.1.0`; `v0.2.0` is a development release without an increment of its own, decision Z5 a of CM-05). **Tag** `v1.0.0-rc1` (annotated, 2026-10-05) on the merge `80962d9f` on `master` of the release candidate `268b41e6` (tree `f93bc886`; code freeze `bce1202a`, documentation freeze `039d8bcc`); the tagged tree carries this row still as `next` with the placeholders, the row is entered in the documentation commit after the tag (DOC-84, CM-06 Z9 a). **CI report** (ER-02, verification plan 9.1): `tools.ci run --strict --slow` on `268b41e6` green, 413 of 413 entries, static analysis 0 new; bound to the tag with `tools.ci release --tag v1.0.0-rc1` and archived as `ci-out-v1.0.0-rc1.zip`, SHA-256 `523063d28d52d89e0c2dd0e6d11cc8048f647c09cf543dba9477d22d24517da7` (also in the tag message). **Board run** (SYS-167 list, verification plan 9.1, VP-6 (b) accepted in CM-06 Z7 a): on the release candidate `bce1202a` (image SHA-256 `93e9796e…730afd`, 2026-10-04, with the user, folder `2026-10-04_rc1-candidate`) no firmware finding, 21 of 25 suite cases passed, BT-S1-03 with the pin cases, F4 in six runs; BT-S1-03 and BT-RUN-06 repeated and passed after SYS-199 (2026-10-04), BT-S1-01 judged passed with a tool finding (CM-06 Z8 a; SYS-200, SYS-201); the difference candidate → tag is the repository identification only (DiagBuildId, XCP EPK; bootloader identical). **Rest run on the tagged image** (SYS-167, 2026-10-05, R1 session, condition (2) of CM-06): green, no firmware finding - image `app.ecu.hex` SHA-256 `1583be99…c7ba45`, CRC-32 `0x44C0A3A6`, bootloader 0.3.9, EPK on the board "… neXtBSW 1.0.0 v1.0.0-rc1"; 9 of 9 automated suite cases passed; F4 in six runs: `safety mpu` 702.7 ms, `safety wdg` 876 … 926 ms (limits 858 / 1 077 ms, AoU-05), the restart time taken from the gap in the CAN trace (oscilloscope channel B without contact, F4 not affected); captures `2026-10-05_v1.0.0-rc1` (`dev` e364f2da); the cases with a manual step count from the candidate (VP-6 (b)), their repeat on the tagged image is SYS-201. Issue 2 (DOC-79, after CM-05): 10 claims, 13 arguments, 118 evidence items, 20 deficits; the confirmation reviews CR-01-1, CR-10-1, CR-07-1, CR-04-1 and CR-02-1 of the other work products and CR-05-1 of this case (section 0), D-02 at CM-10 performed, D-15 with SYS-198 and BT-S1-03, D-20 with SYS-171, EB-12 (B15) and EB-13 (B2 with bootloader 0.3.9) new, EB-02 restated; further since `v0.2.0`: SYS-95 (DFA findings, D-16), SYS-138 (read-back of the safe state in S1 held, SD and S1 + S2, D-03), SYS-172 … SYS-174 and BL-36 (inspection findings, D-20), BL-38/BL-39 (bootloader 0.3.9), D-22 (DOC-46: deviation of CM-03, CM-08, CM-09, CM-11 for R1). Issue 1 (SYS-64) and its updates, new since `v0.1.0`: safety plan, CM plan, HSI, DFA, calibration segment, the decisions iso-q2/q3 in the plan, this case; SYS-117: degraded reaction SD (ET-087, ET-088); SYS-116: selectable safe state of a PWM group (ET-089, ET-090); SYS-140 and SYS-139: withdrawal of the active short on a bridge fault and its hold in the family port (ET-091, ET-092); SYS-97 done (host and board 2026-09-30): D-19 removed, EW-23 available; EW-08 available after DOC-19 and DOC-38; PLAT-79 done (host, board 2026-10-01): D-13 removed, ET-082 available, SSR-082 implemented |
| I-2 | next | f4055d8e | working state after Release R1 `v1.0.0-rc1`: candidate of **`v1.0.0-rc2`** (development release, no safety claim; **no release decision recorded** - CM-06, record CR-06-1, covers `v1.0.0-rc1` only) | 10 claims, 13 arguments, 118 evidence items, 20 deficits - unchanged from I-1; booking after the tag (DOC-84): increment I-1 filled, the evidence items new since `v0.1.0` marked `v1.0.0-rc1` after the re-check against the tag (rule 1), D-15 after the board run of Release R1, ER-02 with the first report bound to a tag. **Candidate `v1.0.0-rc2`** (PLAT-118; the user 2026-10-08: merge `dev` → `master` and tag `v1.0.0-rc2` as the cleaned source of the public repository; release notes `doc/release-notes/v1.0.0-rc2.md`): since `v1.0.0-rc1` the SBC sources rest on the public data sheet only (PLAT-117, code unchanged), bootloader 0.3.10 and 0.3.11 serve the SBC watchdog without software development mode (BL-41, BL-42; AoU-34, AN-191 open until the board proof with SDMS 0), distribution changes PLAT-119, PLAT-120, TOOL-240 … TOOL-245. **TODO at the tag** (rule 1): baseline `v1.0.0-rc2` and the commit of the tag in this row and a new row `next`, the evidence items marked `next` re-checked against the tag, the CI report bound to the tag (ER-02), the board run for rc2 or the statement that none was made, the release decision (CM-06) for rc2 or the statement that none was made - not yet entered (PLAT-118a) |
<!-- case-increments:end -->

**Rule for the next increment** (to be carried out by the coordinator, owner the safety manager):

1. On a release candidate, the header line becomes `Baseline: vX.Y.Z` (cm-plan section 6.1), the row `next` of the
   table above gets that tag and its commit, and every evidence item marked `next` is re-checked against the
   candidate. A new row `next` opens for the following working state.
2. The verdicts are re-assessed: a deficit whose task is done is removed together with its citations, the evidence
   status is raised, and the verdicts follow the rules of section 1.1. The test fails when a task named as the
   carrier of a `partial` or `missing` item or of a deficit is `done` — that is the signal to update the case, not to
   relax the test.
3. The confirmation review CM-05 of the safety plan is performed on the case of that release - until an independent
   reviewer is named by the user under D-01, without independence (for Release R1 on the candidate state `dev`
   2c065567, record CR-05-1, decision Z1 a). Only when the top claim C-00 is at least `supported with limits`, D-01 and D-21 are removed and the
   preconditions R1–R6 are met, may the release be a **safety release**.

---

## 8. Maintaining the case

- **Owner:** the safety manager. **Kept up to date by:** the coordinator, in the same merge that changes a task
  named here (status `done`) or a requirement of `ssr.sdoc`.
- **Checked by:** `doc/safety/test/test_safety_case.py`. It fails when an evidence link does not resolve, when a
  requirement derived from an assumed safety goal is missing in its argument, when a test named here no longer names
  its requirement, when an activity of the safety plan is not argued, when a claim's text drifts from `concept.md` or
  from the safety plan, when a verdict is stronger than the coverage of `concept.md` 2.2 allows, when a carrier task
  is done, when the numbers of section 0 do not match the tables, and when the standard is cited beyond part and
  clause.
- **Change impact:** a change that touches a requirement, a mechanism or a work product named here is a change of
  the case (8-8.4.3); TOOL-57 added the optional field `safety_impact` to the tasks, and until it is filled for the
  tasks that change the safety part (safety plan N9) the coordinator decides per merge.

---

## 9. Proposals and open points

For the coordinator to carry into `doc/todo.yaml` or the decision board.

| # | Proposal or open point | Owner |
|---|---|---|
| S1 | **Done** (AN-097, closed in CM-05, record CR-05-1, Z8 a): the entry `safety-case` is in `tools/test/tests.yaml` (group `python`, kind `unittest`, `doc/safety/test/test_safety_case.py`) | coordinator |
| S2 | **Done** (AN-098, closed in CM-05, Z8 a): `fmea.md` issue 2 section 8.2 records B8 with the capture `2026-09-22_board/02_sys42_b8_jlink.txt` (`0x00008100`, LK = 1, MUX = 001; EB-11) | coordinator (DOC-55) |
| S3 | **Done** (AN-099, closed in CM-05, Z8 a): the safety plan's activity P2-13 stands `partial` | coordinator |
| O1 | Board records that exist but are not linked here (for example CAN bus-off, E2E, bootloader update runs) should be linked when SYS-75 writes the target test specification per SSR, so D-15 shrinks with evidence instead of new runs. SYS-75: linked per case in `doc/safety/board-test-spec.md` section 5.2; the cases ran on one image with the board list of Release R1 (candidate `bce1202a`, counted for the tag under VP-6 (b)); AN-100 closed in CM-06 (record CR-06-1, Z9 a; D-15) | SYS-75 |
| O2 | **Resolved 2026-09-30** (AN-101, decision cloud-0930-sys-77, variant 'einengen', carried out by SYS-126): the top claim C-00 is narrowed to the assumed safety goals stage 1 covers (ASG-01, ASG-02, ASG-05, ASG-06, ASG-07); ASG-03 and ASG-04 stay in the basis for completeness (concept.md 2.2 lists all seven) but are covered only through AoU-23 for the integrator, not by C-00's own wording (sections 0, 2, 3, "Notes on the verdicts") | coordinator (SYS-126) |

---

## 10. Review questions of the architecture (section 11)

This task changes no code, no generator, no configuration and nothing under `platforms/`: one document and one
consistency test. **Board without an SBC, different crystal, derivative, CAN buses, task set:** the case contains
no board fact of its own; the board records are marked as evidence of the reference configuration (T-10), and the
product argument rests on requirements that are written per family and configuration. **New values in one
configuration level:** none. **Host test with its own test profile:** `test_safety_case.py` checks the case against
the repository and against deliberately broken copies of itself; it needs no board profile. **Qt-free core, YAML
paths:** not affected. **Device-driver granularity, platform rule:** not applicable.

## 11. Sources

- ISO 26262:2018 parts 2, 6, 8, 9 and 10, cited by part and clause only, through the reading in
  `iso26262-gap.md` (not published, available on request) (SYS-58, section 6 task 6); the standard itself is not in the repository.
- [`safety-plan.md`](safety-plan.md) (SYS-60): claim, activities, confirmation measure CM-05, release R6.
- [`cm-plan.md`](cm-plan.md) (SYS-68): baselines, citation rule, safety status of a release.
- [`concept.md`](concept.md) issue 3, [`ssr.sdoc`](../requirements/safety/ssr.sdoc), `traceability.md`,
  [`fmea.md`](fmea.md) issue 2, [`dfa.md`](dfa.md), [`tool-classification.md`](tool-classification.md),
  [`safety-manual.md`](safety-manual.md) issue 2, [`verification-plan.md`](verification-plan.md) issue 2,
  `tools/test/tests.yaml`, `tools/boardtest/captures/`, `doc/todo.yaml` at `dev` 2c065567 (issue 1: at `20b2e04`); the
  tag `v0.1.0` (`ed48460`); the records of the confirmation reviews under `doc/safety/reviews/records` (not published,
  section 1.3).

---

## 12. Change history

| Issue | Date | Task | Change |
|---|---|---|---|
| 1 | 2026-09-28 | SYS-64 (`7874daf7`) | first issue: 10 claims, 13 arguments, evidence per item with its baseline, deficits, increments, consistency test |
| 1 (update) | 2026-09-28 … 2026-09-29 | wave 0929/0930, SYS-61, SYS-77, SYS-92, WAVE-1001, SYS-74, SYS-73, DOC-35 | carrier tasks and counts after the merges; EW-20 coexistence, EW-28 product build, ET-086 |
| 1 (update) | 2026-09-30 | SYS-126 (`bddcb885`) | top claim C-00 narrowed to the covered safety goals (AN-101, decision cloud-0930-sys-77, variant 'einengen') |
| 1 (update) | 2026-09-30 | SYS-117, DOC-19, DOC-38, SYS-116, SYS-30 | ET-087 … ET-090, EW-05 the safety manual, decisions of 2026-09-30, ET-060 |
| 1 (update) | 2026-10-01 | SAFETYDOC-1001, REL-020, DOC-21, SYS-67 | consistency after the night merges; header `Baseline: v0.2.0` for the release candidate v0.2.0 (content not re-checked); `ssr.sdoc` instead of `requirements.yaml`; EW-26 verification plan |
| 1 (update) | 2026-10-02 | SAFETYDOC-1002, SYS-75 | D-13 removed after PLAT-79, ET-082; D-15 narrowed after the board test specification |
| 1 (accepted) | 2026-10-02 | release `c7c9398c` | issue 1 accepted by the user with the review set of 15 safety documents |
| 1 (update) | 2026-10-02 | merge `integ/night12` (`0ed6b366`) | carriers of done tasks (D-12, D-18, D-20, D-21, EW-04/09/25/26/28/33, ER-02), EW-35, EW-36 |
| 1 (update) | 2026-10-03 | DOC-46 (`1fadffa6`) | deficit D-22: deviation of CM-03, CM-08, CM-09, CM-11 for release R1 |
| 1 (update) | 2026-10-04 | `integ/r1` (`3b923530`), DOC-51, DOC-53, DOC-54, DOC-55, DOC-57, DOC-59 | D-16 and EW-22 after SYS-95; EW-08 concept issue 3; D-12 after CM-04, D-17 after CM-02 |
| 2 | 2026-10-04 | DOC-79 | corrections after the confirmation review CM-05 (record `doc/safety/reviews/records/CR-05-1.md`, the user under D-01, baseline `dev` at 2c065567, `accepted with conditions`; decision Z4 a: issue 2): header table, this history and state line `Working state: dev at 2c065567 (after v0.2.0)` (F-01, F-02); EW-15 59 SSR, EB-02 after `fmea.md` 8.1/8.2, A-08 with 1 077/858 ms (F-03); the stale places of sections 0, 3, 5, 6, 7, 9 and 11 (F-04); D-03 and D-15 with SYS-198, BT-S1-03 and SSR-060, EB-12 (F-05, Z6 a); EB-12, EB-13, the unlinked board results named (F-06, Z6 a); D-02 at CM-10 performed (Z7 a), D-20 with SYS-171, the consequence of D-01 for the integrator in section 3.1 (F-07); increment of Release R1 with TODO for the tag (Z5 a), section 0 rows "Release R1" (Z9 a) and "Confirmation reviews" (F-08); section 3.1 "What the integrator supplies himself" with AoU-35 and T-01 … T-11 (F-09); not published evidence and paths of private files in sections 1.3 and 5, EW-11, EW-17 (Z2 b, Z10 a); S1 … S3 done (AN-097 … AN-099 closed, Z8 a). Unchanged: the top claim C-00 and its verdict `not supported`, D-01, D-12, D-17 and D-22. The consistency test accepts `v0.2.0` as a development release without an increment and a release candidate tag `vX.Y.Z-rcN` |
| 2 (accepted) | 2026-10-04 | DOC-80 | issue 2 accepted by the user in the re-check of CM-05 (record `doc/safety/reviews/records/CR-05-1.md`, steps 6 and 7 at `dev` 9cd81236, D-01, without independence): F-01 … F-09 closed, verdict `accepted`, CM-05 closed; header status and approver, EW-10 and section 0 row "Confirmation reviews"; editorial completion of the list of not published evidence in sections 1.3 and 5 (remark Z2: `tools/todo` EW-32, `CLAUDE.md` cleaned EW-01, the requirements outside `doc/requirements/safety`; the safety requirements are published by decision SDOC). No claim, argument, evidence status, deficit or verdict changed; the increment row of Release R1 is filled at the tag (CM-06) |
| 2 (update) | 2026-10-05 | DOC-84 | booking after the tag `v1.0.0-rc1` (confirmation review CM-06, record `doc/safety/reviews/records/CR-06-1.md`, the user under D-01, `accepted with conditions`, decision Z9 a): increment I-1 with the tag commit `80962d9f`, the bound CI report (ER-02, SHA-256 `523063d2…517da7`) and the board run of the release candidate and the rest run on the tagged image (2026-10-05, green), new row I-2 `next` and state line `Working state: dev at c50e240f (after v1.0.0-rc1)` (section 7 rule 1); the 32 evidence items marked `next` re-checked against the tag and marked `v1.0.0-rc1`; D-15 after the board run (BT-S1-03, BT-SLEEP-01, VP-6 (b), SYS-201), EB-12 and ER-02 restated; O1: AN-100 closed; section 0 rows "Increments" and "Confirmation reviews". No claim, argument, evidence status or verdict changed |
| 2 (update) | 2026-10-08 | PLAT-118a | increment I-2 (`next`, `dev` f4055d8e) marked as the candidate of `v1.0.0-rc2` with the changes since `v1.0.0-rc1` and the TODO at the tag; no release decision for rc2 recorded (CM-06 covers rc1 only). No claim, argument, evidence item, status or verdict changed |
| 2 (update) | 2026-10-08 | SYS-207 | D-22 after safety plan issue 3 (decision of the safety responsible 2026-10-08, E1 b; pending the re-check of CM-01): lifted before the first release with an ASIL B claim instead of "before the first public release or the first release with an ASIL B claim, whichever comes first"; consequence: a public development release without a safety claim is allowed, each recipient keeps receiving the statement with the safety manual, no safety release until CM-03, CM-08, CM-09 and CM-11 are performed. No claim, argument, evidence item, status or verdict changed; 20 deficits |
| 2 (update) | 2026-10-08 | SYS-209 | re-checks of the user of 2026-10-08 (D-01, without independence): D-02 after re-check 3 of CM-10 (record CR-10-1, F-02 closed, verdict `accepted`, CM-10 `closed`) - the deficit stays for the missing independence, DOC-50 no longer in its task column; D-22 without "pending" (safety plan issue 3 released in the delta re-check of CM-01, record CR-01-1) and with the places of the statement (README, release notes; C2); section 0 rows "Verdict today" and "Confirmation reviews", section 3 note, EW-05; state line unchanged (increment I-2 names it). No claim, argument, evidence status or verdict changed; 20 deficits |
