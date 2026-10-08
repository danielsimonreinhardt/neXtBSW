# neXtBSW safety manual for integrators — software SEooC at ASIL B (DOC-19)

| | |
|---|---|
| Title | Safety manual of neXtBSW for integrators: claim, assumptions of use, mechanisms, configuration rules, limitations |
| Issue | 2, 2026-10-04 (issue 1, 2026-09-30); update DOC-50 of 2026-10-08 (section 5, column "Clause; neXtBSW rating") accepted 2026-10-08 in re-check 3 of CR-10-1 |
| Author | DOC-19 (agent session of wave 2026-09-30, written on the user's behalf — author side); issue 2: DOC-49 (updates DOC-51, DOC-53, DOC-58, DOC-59, DOC-79, DOC-50, section 16) |
| Approver | the safety manager (the user): issue 1 **accepted** 2026-09-30 in the safety review of DOC-19 (decision board `review-doc19`, D1 … D6, D8, D9; D7 — the times — to be re-measured on the board: *done*, section 4 since SYS-149, bootloader 0.3.9 and the oscilloscope breakdown of 2026-10-03); issue 2 is the state after the corrections of the CM-10 findings (decision Z3 of the confirmation review CM-10, record `reviews/records/CR-10-1.md` (not published, available on request), 2026-10-04, the user under D-01, verdict `accepted with conditions`), released on 2026-10-04 with the re-check of F-01 and F-03 in that record (N1, N2; D-01, without independence); the updates of issue 2 after its release (DOC-51, DOC-53, DOC-54, DOC-58, DOC-59) accepted on 2026-10-04 in re-check 2 of that record (`dev` 2c065567, N6 … N10; D-01, without independence); the update DOC-50 (section 5, column "Clause; neXtBSW rating") accepted on 2026-10-08 in re-check 3 of that record (`dev` c0928380, C4 … C6, F-02 closed; C7 the class of SM-21/SM-22 as `fmea.md` issue 2; D-01, without independence) |
| Status | issue 2 **accepted** (released 2026-10-04 in the re-check of CM-10, record `reviews/records/CR-10-1.md` (not published, available on request): F-01 and F-03 closed; D-01, without independence); the updates after the release - F-04 (DOC-53, AN-143) and F-05 (DOC-58, AN-175) - closed and accepted as updates of issue 2 in re-check 2 of CR-10-1 (2026-10-04); F-02 (the typical classes of the former re-based column, section 5; Z2) corrected by DOC-50 (2026-10-08: per mechanism a clause reference and neXtBSW's own rating instead of the typical classes) and closed in re-check 3 of CR-10-1 (2026-10-08), the update DOC-50 accepted; CM-10 **closed** with the verdict `accepted` (D-01, without independence) |
| Change history | section 16 |

Working state: dev at c0928380 (after v1.0.0-rc1)
Readers: the integrator who builds a safety-related ECU on neXtBSW, his safety manager and assessor; the user as
reviewer of CM-10 under deviation D-01 (record CR-10-1), and an independent confirmation reviewer once named
(`reviews/packages.md` (not published, available on request)).
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**What this document is.** The integrator-facing safety manual of neXtBSW as a **Safety Element out of Context
(SEooC)**, the work product of confirmation measure **CM-10** of the safety plan
([`safety-plan.md`](safety-plan.md) section 5). It says what neXtBSW assures and what it does not, which assumptions
of use (AoU) the integrator must meet and how, which configuration rules keep the claim valid, which limitations he
inherits, and where the evidence is. It **adds no new safety statement**: every sentence restates an existing
statement of the repository and names its source. Where the sources disagree or are out of date, this manual says so
(section 15) instead of deciding.

**Licence rule.** ISO 26262:2018 is cited by **part and clause only** (for example "10-9.2.4" = ISO 26262-10:2018,
clause 9.2.4). No text, table or figure of the standard is reproduced.

**What it is not.** Not a safety case ([`safety-case.md`](safety-case.md)), not an assessment, not a statement that an
ECU built on neXtBSW is ASIL B capable. neXtBSW is a hobby project; nothing here has been assessed by anyone outside it
(concept section 0).

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Claim | **Methods of ISO 26262-6 at ASIL B, without the management part** (user decision iso-q2, 2026-09-22; safety plan 1.2) — a software component SEooC with ASIL B capability, **stage 1**: only base-software mechanisms are credited (section 1) |
| Not claimed | ASIL C and D; hardware metrics and failure rates; the device drivers' own diagnostics (stage 2); detection for ASG-03 and ASG-04; a quality management system, competence evidence, independent confirmation reviews, a functional safety assessment (section 1.3) |
| Assumed safety goals | 7 (ASG-01 … ASG-07): none fully covered; ASG-02 and ASG-06 covered with limits, ASG-05 covered for CAN data, ASG-01 and ASG-07 partial, ASG-03 and ASG-04 not covered — passed on unchanged from concept 2.2 (section 3); six assumed technical safety requirements group the SSRs (section 3.2) |
| Safe states | S1, S1 held, S1 + S2, S3 and, per project, SD degraded (section 4) |
| Mechanisms | <!-- manual-sm-counts -->**31** credited stage-1 mechanisms (detection or reaction; SM-34 a reaction whose coverage belongs to the reporting package - as `mechanisms.sdoc` and `fmea.md` issue 2), and the not credited SM-26, SM-27 (selectable safe state and its start test, SYS-116; not credited for release R1, CM-07 ZD), SM-33, SM-52 (device-dependent), SM-58 (optional hardware path), SM-59 (hardware LVD reset, SYS-125), SM-60 (lock of the FlexNVM block, NVM-25, proposed) and SM-40 … SM-43 (stage 2) — each with guaranteed detection, reaction, fault handling time, coverage class, and the clause reference with neXtBSW's own rating of the gap analysis (section 5; since DOC-50 instead of the typical classes) |
| Assumptions of use | <!-- manual-aou-counts -->**62**: AoU-01 … AoU-30 and AoU-32 (concept, fmea, tool classification), AoU-33 (partner-and-degraded, in force for every project with an active short since CM-10, F-03; a node of concept 4.3 since CM-07, ZF), AoU-34 … AoU-36 (numbered here from existing statements, in the concept since DOC-38), AoU-37 (SYS-135), AoU-38 (SYS-143) and AoU-39 (SYS-125) (concept 4.3, binding since CM-10, Z4), AoU-40 (NVM-25), AoU-41 and AoU-42 (concept, review Welle-0210-Tag; AoU-40 binding since CM-07, ZG), AoU-DFA-a … AoU-DFA-c (proposed, dependent failure analysis, SYS-95), AoU-ASA-a, AoU-ASA-b (a row of its own since CM-10, F-03) and AoU-ASA-d (architecture safety analysis, binding since the re-check of CM-02, Z7I b; AoU-ASA-c covered by existing rows, section 7.7), AoU-CFG-1 … AoU-CFG-7, AoU-SEC-1 … AoU-SEC-8; plus AoU-31 announced for SYS-115 and not in force (section 7.6); every row added after the acceptance of issue 1 says so in its source column |
| Configuration | combined route: relations checked over the whole declared range, behaviour verified per data set; 19 values in [`config-range.md`](config-range.md) (section 8) |
| Tools | <!-- manual-tool-counts -->**24** rows of the tool classification; compiler TCL 3, most generators TCL 2 today; the integrator's duties are AoU-26 … AoU-29 (section 9) |
| Known limitations | non-qualified FreeRTOS, GCC 11.3.1 (xPack arm-none-eabi-gcc), newlib, ETL; OpenBSW demo skeleton; single channel on one core; no partitioning of the application yet (SYS-21); family port for s32k1xx only; open board proofs (section 10) |
| Verification | **mock/host**: [`test/test_safety_manual.py`](test/test_safety_manual.py) checks this document against its sources (every AoU of concept, fmea, tool classification, config range and security interplay present with a source, every stage-1 mechanism of the concept catalogue in section 5, the tool table against `tool-classification.md`, tailoring T-01 … T-11, links, licence rule; the AoU proposals of `dfa.md` section 9 and `architecture-safety-analysis.md` section 7.1 present or marked as covered; in the git repository also the state line against the last tag, an entry of section 16 for every commit that changed this manual since its acceptance, and the note on every AoU row added after it) with counter-checks; `tools/trace check` sees every AoU here. No code, no hardware |

---

## 1. What neXtBSW assures, and what it does not

### 1.1 The element

neXtBSW as it is built into an ECU image, plus the tools that produce parts of that image (concept 1.2): safety
supervision (`ecu/safety/safeSupervisor`, `ecu/safety/safeWatchdog`), the family safety port
(`platforms/s32k1xx/port/safety/*`), the fault handlers, the debug-halt guard, the RTE and its run-time supervision,
ECU state and start reasons, E2E and the signal qualifier, the CAN system, the analogue input path, the MCU drivers of
the family, the bootloader, the device drivers (stage 1: only their `safe_off` declaration) and the configuration
data with its generators. Out of scope: the application (SWCs), the hardware, the item-level safety concept
(concept 1.2, AoU-01, AoU-09, AoU-17).

### 1.2 The claim

**neXtBSW is developed following the methods of ISO 26262-6 at ASIL B, without the management part** (safety plan
1.2, user decision iso-q2). For the element this means: parts 6, 8 and 9 applied at ASIL B with the tailoring of
section 12; part 2 applied as far as a single-person project can (safety plan, roles, tailoring, safety case,
confirmation measures, release procedure). The capability is **ASIL B** (concept 2.1, confirmed in `abn-sys19`), as
**ASIL capability** of an element out of context (10-9.1; gap analysis section 4 point 3).

**Stage 1** (concept 1.4): the safety argument is built from mechanisms whose detection runs on the MCU and uses only
the MCU, its peripherals and the configuration. Diagnostics of parts outside the MCU (gate driver, cell controller,
SBC, high-side switch) are **stage 2** (SYS-28) and **not credited**; their `safe_off` declaration is stage 1.

### 1.3 What neXtBSW does not assure

| Not assured | Source | Consequence for the integrator |
|---|---|---|
| ASIL C or D | concept 2.1, AoU-03 | an item with a C or D goal cannot rely on the element |
| failure rates, single-point and latent-fault metrics, hardware measures | concept 1.2, AoU-09, safety plan T-02 | the integrator provides them; section 5 gives what his metrics need per mechanism |
| detection for ASG-03 (cell values) and ASG-04 (balancing); full coverage of ASG-01 | concept 2.2, AoU-23 | he provides that detection himself (section 3) |
| the device drivers' diagnostics | concept 1.4, 13; AoU-21 | he argues them from the manufacturer documents or adds his own measures |
| a function that continues after a fault of the MCU (fail-operational) | concept 2.2, AoU-04 | only the degraded reaction SD for device faults exists (section 4) |
| freedom from interference for the application | concept 6.3, AoU-17 | he develops his safety-related code at the target ASIL or partitions it himself |
| qualification of RTOS, compiler, libraries | concept 8, AoU-20 | section 10 |
| QMS, competence evidence, independent confirmation reviews, functional safety assessment | safety plan 1.2, T-03, T-09 | AoU-35 |
| any cybersecurity claim | [`security-interplay.md`](security-interplay.md) section 0 | section 11, AoU-SEC-1 … AoU-SEC-8 |
| the behaviour of the integrator's application or its generated model code | concept 1.2, tool classification AoU-29 | he verifies his application himself |
| verification of the integration and of the item: the integration test of his ECU with his configuration and the test in the vehicle | [`verification-plan.md`](verification-plan.md) sections 1 and 2.3 (vehicle test tailored for the SEooC); AoU-10, AoU-CFG-1, AoU-CFG-2 | he runs the same pipeline on his workspace (`python -m tools.ci run --workspace`), repeats the board checks his values depend on and tests the integrated ECU and the item himself |

### 1.4 Platform status

The family safety port exists for **s32k1xx** only (concept 1.3, 9.2). On `stm32`, `s32k3xx` and `rm4x` an integrator
gets the family-neutral part (supervisor, events and reactions, safe outputs table semantics, RTE deadlines, E2E,
ecuState) and nothing of sections 6.2 and 6.4 of the concept. That is a gap, not a silence.

---

## 2. External interfaces of the element

The interfaces through which the integrator's item, hardware and software meet the element (2-6.4.5.7; gap proposal
C3). Each is described in the named document; this list is the index.

| Interface | What crosses it | Where described | Integrator's side |
|---|---|---|---|
| Safe supervisor | events of the mechanisms (`trigger()`, `check()`, `hit()`), context words; event mailboxes in general RAM for events from outside the safety context (SYS-37); the device report word and `DeviceReportBox` (SM-34) | concept 3, 5, 6.2 (SM-10), 6.6 (SM-34) | he raises no switching-off event from outside the safety context (concept 6.2 SM-10, decision `sys37-postfach-abschaltend`) |
| RTE | values with the quality qualifier (`QLF_OK`, `QLF_ERROR`, `QLF_ERROR_RANGE`), E2E check status, deadlines = configured periods (SM-05), the signal `safety.degraded` (SD), `gd.<device>.qlf` | concept 3, 6.1, 6.6; [`../architecture/signal-qualifier.md`](../architecture/signal-qualifier.md) | AoU-11, AoU-12, AoU-18, AoU-32 |
| Power outputs | the safe outputs table (PWM groups, `safe_off` connections), one `IPowerStageOutput` adapter per `safe_off` output, re-enable only through the driver's own `enable()` | concept 3, 6.5 | AoU-06, AoU-07, AoU-30 |
| Configuration data | `mcudb/<family>/<derivative>.yaml`, `boards/<board>/board.yaml`, `projects/<project>/{project.yaml,rte.yaml,*.dbc}`, module manifests | concept 1.2, [`config-range.md`](config-range.md) | AoU-10, AoU-CFG-1 … AoU-CFG-7 |
| Hardware-software interface | peripherals, registers, clocks, interrupts and pins the stage-1 mechanisms use on s32k1xx | [`hsi-s32k1xx.md`](hsi-s32k1xx.md) | AoU-07, AoU-09, AoU-13, AoU-30 |
| Bootloader and image | validity record, image CRC, memory layout generated from one source, `bootloader.sbc_watchdog` | concept 6.7 (SM-51), `doc/reference/project.md` | AoU-34, AoU-SEC-1 |
| Field interfaces | download, bootloader update, UDS, XCP, calibration segment, console, debug port, NvM, received CAN signals (IF-01 … IF-10) | [`security-interplay.md`](security-interplay.md) section 2 | AoU-SEC-1 … AoU-SEC-8 |
| Tools | the build (nxconfig, generators, post-link steps), the Studio, the application's code generator | [`tool-classification.md`](tool-classification.md) | AoU-26 … AoU-29 |

---

## 3. Assumed safety goals and assumed technical safety requirements

### 3.1 Assumed safety goals (passed on unchanged from concept 2.2)

These are **assumptions about the item**, each to be checked off or replaced by the integrator (AoU-02). Only
base-software mechanisms are credited; the "later device contribution" of the concept is not part of the argument.
The per-goal verdicts were confirmed by the failure analysis ([`fmea.md`](fmea.md) section 4). The column "Coverage at stage 1" restates the `COVERAGE_NOTE` of [`goals.sdoc`](../requirements/safety/goals.sdoc) issue 3 (concept issue 3, CM-07 decision ZC: grades unchanged, notes at the state of the tree; DOC-58).

| ID | Assumed safety goal (of the item) | ASIL | Stage-1 mechanisms credited | Coverage at stage 1 |
|---|---|---|---|---|
| ASG-01 | Unintended torque of the electric drive shall be prevented, or terminated within the fault tolerance time | B | SM-23 … SM-25, SM-17, SM-06, sections 6.1/6.2 of the concept as triggers of S1 | **partial** — the termination path works for every fault the MCU detects; nothing compares commanded with actual behaviour (G2); register lock and check from the configuration (SM-21/SM-22, SYS-24, SYS-153), the class `SYSTEM_CLOCK` open (F4, SYS-55); the read-back of the safe state (G1) closed on the host for S1, S1 held, SD and S1 + S2 (SYS-30/SYS-138), open for the fault handlers, the watchdog handler and the halt deadman and on the board (B15/B16) |
| ASG-02 | The path that switches the power stage off shall itself be available when needed | B | SM-23, SM-24, SM-25, SM-21/SM-22, SM-17, SM-06 | **covered with limits** — register lock and check from the configuration, the class `SYSTEM_CLOCK` open (F4, SYS-55); the walk bounded on the board at ≤ 21 µs (F8, fmea B3); read-back of the safe state on the host (G1, SYS-30/SYS-138), open for the handlers of the family port and on the board (B15/B16); **residual risk DF-05**: after a loss of the SIRC the outputs are not shown to be de-energised until the watchdog reset after at most about 283 ms (section 5, SYS-195) |
| ASG-03 | Cell voltage, cell temperature and pack current outside the safe operating area shall be detected and made known within the fault tolerance time | B | SM-18 … SM-20 and SM-30 for MCU-measured values, SM-31 on the reporting path | **not covered** (G2, G3, G5; AoU-23) |
| ASG-04 | Cell balancing shall not run unintentionally | B | — | **not covered** (G4; AoU-23) |
| ASG-05 | Corrupted, stale or repeated safety-related data shall not be used as if it were valid | B | SM-31, SM-30, SM-32 | **covered for CAN data**, together with the application (AoU-11, AoU-12); no age supervision of device values (G3) |
| ASG-06 | A failure of the control software shall lead to a defined safe state | B | concept 6.1 … 6.5 | **covered with limits** — SM-05 reports only (S3; the reaction within the FTTI is the application's, AoU-18); task activation unsupervised (F10, SYS-21); register configuration from the configuration, the class `SYSTEM_CLOCK` open (F4, SYS-55); one ROM pass at most 5 574 µs on the board (fmea B1), the repetition observed (B4), the suspension during flash writes (B9) not recorded on the board |
| ASG-07 | The ECU shall not start, or not run, with an image that is not the intended one | B | SM-51, SM-14 | **partial** — start covered for projects with the neXtBSW bootloader; run time by SM-14 with its period (one pass at most 5 574 µs on the board, the repetition observed); the suspension during flash writes (B9) not recorded on the board; the bootloader's own integrity is not checked (fmea N4, SYS-127) |

### 3.2 Assumed technical safety requirements allocated to the element

The gap analysis (row 10-02, proposal C2) and the safety plan (T-01) ask for the level between the assumed goals and
the software safety requirements (SSR): what the element is assumed to do, with ASIL, safe state and maximum fault
handling time, in a form the integrator can check against his own technical safety concept. **This level is written
here for the first time.** It invents no requirement: every row groups existing SSRs of
[`ssr.sdoc`](../requirements/safety/ssr.sdoc) under the goals they are derived from, and takes the times from section 5.
It is a review point (section 15).

| ID | Assumed technical safety requirement of the element | From | ASIL | Safe state | Maximum fault handling time | Realised by |
|---|---|---|---|---|---|---|
| ATSR-1 | On every fault the element detects, it shall de-energise the declared power outputs before any other action, including before a reset | ASG-01, ASG-02, ASG-06 | B | S1 (and S2, S1 held or SD as section 4) | per mechanism, section 5; outputs off within the walk after detection | SSR-001, SSR-002, SSR-004, SSR-005, SSR-006, SSR-007, SSR-016, SSR-028, SSR-080, SSR-089, SSR-090, SSR-091, SSR-092, SSR-060 |
| ATSR-2 | The element shall detect failures of its own control software — program flow, timing, memory, register configuration, supply of the MCU — and react by ATSR-1 | ASG-06 | B | S1 + S2, S1 held | from 17 ms (checks in the safety task) to 250 ms (watchdog), section 5 | SSR-003, SSR-010, SSR-011, SSR-012, SSR-013, SSR-014, SSR-015, SSR-017, SSR-018, SSR-020, SSR-021, SSR-022, SSR-023, SSR-026, SSR-027, SSR-030, SSR-031, SSR-032, SSR-033, SSR-034, SSR-050, SSR-052, SSR-065, SSR-066, SSR-068, SSR-069, SSR-070, SSR-081, SSR-086 |
| ATSR-3 | The element shall not start an image that is not the intended one, and shall detect a change of the running image | ASG-07 | B | image not started; S1 held | at start; during operation one period plus one pass | SSR-024, SSR-025, SSR-051, SSR-067, SSR-082, SSR-083 |
| ATSR-4 | The element shall mark safety-related data that is corrupted, repeated, out of sequence, from the wrong sender or out of its valid range, and shall not transmit into an unhealthy bus; the reaction is the application's | ASG-05, ASG-03 (MCU-measured values only) | B | application's reaction (AoU-11, AoU-12) | one reception or task period | SSR-040, SSR-041, SSR-042, SSR-062, SSR-084 |
| ATSR-5 | On a device fault, or on the trip of a hardware shut-off path (`HW_SHUTOFF`, never a finding of the path; since SYS-135, concept 3, CM-07 ZA), that the project maps to the degraded reaction, the element shall switch only the mapped safe-output groups and keep them off until the power cycle | ASG-01, ASG-06, AoU-32 | B | SD; S1 without reset for the last group | as S1 for the mapped groups | SSR-087, SSR-088 |
| ATSR-6 | The element shall carry device reports to one reaction decision and mark the values of a device on a lost bus | ASG-01, ASG-03, ASG-04 | B | S3 for reports, S1 + S2 or S1 held for device faults | one safety cycle plus the RTE task period | SSR-064 |

Not allocated to the element: detection for ASG-03 and ASG-04 (AoU-23), comparison of commanded and actual behaviour
(SSR-061 and SSR-063 are **open**, gaps G2 and G4; SSR-062 (gap G3) is partial since CM-07 ZE - the age qualifier of a value not refreshed within its period, allocated to ATSR-4; the event of its clause (b) is open with SYS-99; G1 is closed on the host for S1 by SYS-30 and for S1 held, the groups of SD and the S1 + S2 sequence of `ecu/safety` by SYS-138; SSR-060 stays partial: the hard fault and watchdog handlers and the halt deadman of the family port, and the board proof B15/B16).

---

## 4. Safe states and reactions

The reaction to an event is decided in exactly one place, `SafeSupervisor::handle()`; a mechanism only raises an event
(concept 3, SSR-001).

| Reaction | What happens | Leaves it | Used for (today) | Source |
|---|---|---|---|---|
| **S1** de-energised outputs | the project's safe outputs table is walked: every PWM group forced inactive through the FTM software output control, every `safe_off` output driven inactive; no stack, no RAM, callable from task, supervisor and interrupt | only through an explicit `enable()` of each power stage's driver; `leaveSafeState()` releases the indicator only | SM-22 (without reset); first step of every resetting reaction; fault handlers and halt deadman walk it directly | concept 3, 5, 6.5 |
| **S1 held** | limp home and S1, then a loop with interrupts suspended that re-walks the table and services the MCU watchdog; one console line; no application runs | power cycle or a reset from outside | `WATCHDOG_STARTUP_CHECK_FAILURE` (SM-07), `ROM_CHECK_FAILURE` (SM-14), the reset-loop limit (SM-50, SYS-43), `DEVICE_FAULT` class `IDENTITY` (SM-34) | concept 3, 6.6; fmea SM-50 |
| **S1 + S2** | S1, then limp home and a software reset; what happens after the reset depends on the board (SBC reset counter) and on `modules.ecuState.after_watchdog_reset` | restart; after N safety resets in a row S1 held (reset-loop limit) | most events of sections 6.1 … 6.4 of the concept | concept 3, 6 |
| **S3** report and continue | the event is logged | — | `RTE_TIMING_VIOLATION` (SM-05), `DEVICE_REPORT` (SM-34) | concept 3 |
| **SD** degraded (per project) | only the mapped safe-output groups go to their safe state (same table, masked walk), latched and recorded in no-init RAM, walked again every safety cycle, reported to the application (`safety.degraded`) and through a DTC to the error memory; no reset, the rest keeps running | power cycle only; a safety reset re-applies the record before the BSP starts; the last working group degraded means S1 without reset | `DEVICE_FAULT` of a well-formed report not of class `IDENTITY`, and the trip of a hardware shut-off path (`HW_SHUTOFF` without findings, SM-58, SYS-135; the group the path switches off), if the project maps it (`safety.output_groups`, `safety.degraded`); faults of the MCU itself and findings of the shut-off path never | concept 3 (both cases since issue 3, CM-07 ZA); [`partner-and-degraded.md`](partner-and-degraded.md) 4 |

What the integrator must confirm about these states is AoU-04 (and AoU-32 for SD). A reset is not instantaneous: the
restart after a safety reset (last to first application frame) was measured on the eval board at up to **1 077 ms**
through the watchdog path (985 … 1 077 ms, including the 250 ms watchdog time-out) and up to **858 ms** through an
MPU fault (817 … 858 ms) ([`fmea.md`](fmea.md) 8.1, B2, re-measured 2026-09-30 with a bootloader before 0.3.6;
2026-09-22: 1 027 ms and 852 ms). Use the maximum. **These two values stay the stated restart times** (safety review
BL38-P1, 2026-10-03, F2 a): the restart time enters AoU-04, the restart cycle as safe state, not the fault reaction
time, because the outputs are switched off before the reset; the fault tolerance times are not changed.

*Bootloader 0.3.9* (BL-38/BL-39; largest gap of the application frames on CAN, board 2026-10-02/03): `safety mpu`
699.7 ms in each of three runs, `safety wdg` 883 … 933 ms in four runs (883.0, 913.2, 923.3, 933.2 ms; the spread is
the phase of the 250 ms watchdog time-out against the frame cycle) - within the stated values (fmea 8.1 B2;
records `tools/boardtest/captures/evalboard_s32k144_demo/2026-10-03_board/bl33/`, `m1_*_gap.txt` and
`m2_*_gap.txt`).

*Where the time goes with bootloader 0.3.9* (oscilloscope on RESET_b and on the chip select of the SBC's SPI, board
2026-10-03, same folder, `c-20261003-*.csv`; BL-33 and BL38-P1 F1). The fault reset is followed by the two resets
of the MCU watchdog fast test (SM-07): the reset pulses lie at 0, +229.0 and +173.0 ms (after power-on +251.7 and
+173.0 ms). Since bootloader 0.3.6 (BL-33) only the first start after the fault reset passes the bootloader's
start-up window of 200 ms:

- start 1 = bootloader initialisation ≈ 20.6 ms + start-up window 200 ms (inside it the CRC over the application
  image, ≈ 144 ms) + application up to its fast test ≈ 8 ms;
- start 2 = bootloader initialisation ≈ 20.6 ms + CRC ≈ 144 ms + application up to its fast test ≈ 8 ms;
- start 3 = bootloader and CRC as start 2, then the application up to its first frame.

BL-33 therefore saves ≈ 112 ms (two windows of 200 ms minus the CRC that was computed inside them, 2 × 56 ms), not
the 400 ms of two whole windows. The MPU path is 36 ms longer than with bootloader 0.3.6 (664 ms): ≈ 11 ms come from
the larger application image (longer CRC in each of the three starts), the rest lies on the application side (fault
to reset and start 3 to the first frame); splitting that rest up is task SYS-197, not part of release R1. For his
board, bootloader and start-up the integrator measures the restart time himself (AoU-CFG-2).

**The bootloader phase is not supervised.** On s32k1xx the bootloader disables the MCU watchdog as its first action
(`bootloader/platforms/s32k1xx/hal_s32k1xx.c`, `bl_system_init()`) and never enables it; on stm32 the independent
watchdog is not running after power-on or software reset and the bootloader does not start it. The application
enables its watchdog in its own start-up. From every reset until then, no neXtBSW mechanism supervises the program
flow. This is safe under three conditions, which the integrator confirms: (1) every power output is in its reset
state while the bootloader runs - the bootloader configures no pin of a power output, only CAN and, on boards with
an SBC, its SPI (AoU-07, AoU-SEC-3); (2) the phase is bounded: without a tester it ends after the start-up window
with the jump, or the bootloader stays with outputs in reset state; a hang in the bootloader is left only by a reset
from outside (power cycle, SBC watchdog served by the bootloader - with `bootloader.sbc_watchdog: serve`, and since
BL-41 always with an SBC without software development mode, since BL-42 also with an SBC that is only read (AoU-34) -
or the tester's ECU reset);
(3) a bootloader that stays (no valid image, programming session) is a state with de-energised outputs, not a
functional state. The start decision itself is not a RAM value of the past: since BL-36 the bootloader checks the
start again immediately before the jump, and a record without compatibility block (written before BL-02) is never
started (SM-51; SYS69-S10/S11 of the safety review 2026-10-02). Since BL-38 (bootloader 0.3.8) that check reads record,
compatibility block and entry again and compares the record's CRC field and size with the one CRC-32 over the image
computed at the start, instead of a second CRC over the image; the bootloader has no flash write path between the two
checks outside the programming session, which keeps it from jumping, and an image body changed by anything else is
the application's ROM check SM-14 at its start.

---

## 5. Safety mechanisms the integrator may rely on

**How to read the table.** *Guaranteed detection (nominal)* is the time from the fault to the event, as bounded by
the failure analysis ([`fmea.md`](fmea.md) section 2 and 3.7): checks in the safety task are guaranteed at **17 ms**
while the task runs (nominal 10 ms, SM-04 since SYS-40); a task that stops is SM-01's. *FHTI* is the maximum fault
handling time interval of the element = guaranteed detection + reaction until the outputs are de-energised (the
naming of gap proposals C7/M1); the walk itself is computed below 1 µs at 80 MHz and bounded at **≤ 21 µs** by a board
measurement (fmea 8.1, B3). The integrator compares the FHTI with the fault tolerant time interval of his goal
(AoU-05). *Class* is the ordinal coverage class of `fmea.md` 1.3 — a judgement of neXtBSW's **use** of a feature, no
percentage. *Clause; neXtBSW rating* is the answer of the gap analysis to open point N1
(`iso26262-gap.md` (not published, available on request) section 3), since DOC-50 in this form: first the **clause** of the standard
against which the integrator rates the mechanism in his hardware metrics (part and clause only; the standard's own
ratings are not carried here, the integrator reads them in his copy), then **neXtBSW's own rating** with what the
mechanism sees and what not, in the words of [`fmea.md`](fmea.md) section 3; "—" where section 3 has no row (proposal
M2 open). *Latent faults* names how a failure of the mechanism itself is found (fmea section 3).

**neXtBSW's rating scale (DOC-50).** neXtBSW has no failure rates and no safety manual of the MCU (AoU-09, fmea N2),
so it cannot say which share of a hardware element's failures a mechanism detects. What it can show from the
repository is **how deep the detection of the faults in its own fault model is demonstrated**. The rating says that,
on a scale of its own that is deliberately not "low / medium / high", so that it cannot be read as the standard's
classes:

| Rating | Meaning | Where the evidence is |
|---|---|---|
| **E3** | the fault, or its register-level equivalent, was injected on the target and the reaction was recorded, in addition to host tests | board run named in `fmea.md` section 3 or 8, capture or changelog entry |
| **E2** | the detection is exercised on the host against the fault model, and built-in faults (mutants) of the detecting code are all found; on the target at most the absence of false alarms or the reaction alone | host test with a mutation profile (`*-mutations`, mutants named in `fmea.md`) |
| **E1** | host tests without mutation testing, or analysis only | host test or review |

After the level the cell says what the mechanism **sees** and what it does **not see**; "reaction" there means that the
hardware detects and neXtBSW contributes the reaction and its time; "not a hardware diagnostic" marks a
freedom-from-interference measure. The rating does not replace the class: the class judges how completely the
property is checked, the rating how well that is shown. For his metrics the integrator takes neither as a coverage
figure without his own rationale against the clause named (AoU-09).

<!-- manual-mechanisms:start -->
| ID | Mechanism | Credited | Guaranteed detection (nominal) | Reaction | FHTI | Class | Clause; neXtBSW rating (gap section 3) | Latent faults of the mechanism |
|---|---|---|---|---|---|---|---|---|
| SM-01 | MCU watchdog | yes | 250 ms after the last service | warning interrupt walks the table, then hardware reset (S2) | 250 ms + walk | medium | 5-D.8. **E3**: sees a stop of the safety task (endless loop, deadlock, dead tick, lost core clock) by a time-out on the LPO; does not see a service that comes too early (no window) or a wrong flow that still services (SM-04, SM-03) - credited together with SM-04 as one mechanism against a clock independent of the core, limited by the shared die (AoU-13). Host `safe-watchdog`; board 2026-09-18 (halt > 250 ms, warning handler walked) and 2026-09-21 (`safety wdg` loop → watchdog reset) | SM-07 at every start; configuration by SM-02 |
| SM-02 | Watchdog configuration check | yes | 119 ms (70 ms) | S1 + S2 | 119 ms + walk | medium | — | a skipped check is seen by SM-04 only if the service is skipped with it |
| SM-03 | Watchdog over-service check | yes | 119 ms (70 ms) | S1 + S2 | 119 ms + walk | low | — | none |
| SM-04 | Safety-cycle sequence monitor with period check | yes | 17 ms (10 ms); period violation at the late cycle, window of 7 cycles | S1 + S2 | 17 ms + walk | medium | 5-D.8 (with SM-01). **E3**: sees a wrong order or a skipped sub-check of the safety cycle, a period too long or too short against the LPTMR on the LPO, a slow or fast system time; does not see a stopped system time (SM-06, SM-01). Host `safety-cycle`, `cycle-period-check` with mutants; board B7 (task blocked 5 ms → kind 1), B4 without false alarm | timer configuration re-checked every cycle; a stopped counter seen by the window; since SYS-95 also the OpenBSW system time against the LPTMR (DFA-6): a **slow** system time is kind 4, a **fast** one kind 5 - a **stopped** one is not seen by this check (the safety task is scheduled on that time base and does not run), it is SM-06's and SM-01's (below the table) |
| SM-05 | RTE run-time supervision | yes (report only) | end of the offending cycle | S3 | no de-energising reaction | medium; none for task activation (F10) | — | mailbox overflow and corruption reported |
| SM-06 | Debug-halt / stall deadman | yes | 10 ms (hardware counter) | S1 walk from the interrupt; PWM inhibited and enable withdrawn until the next explicit enable | 10 ms + walk; for a debugger halt when the core runs again | high (stalls and halts ≥ 10 ms) | 5-D.8. **E3** for its own fault model: sees a halt or stall of the core ≥ 10 ms on the LPIT counter; does not see a wrong flow that keeps running, nor a lost SIRC (DF-05) - as a diagnostic of the processing unit it covers only faults that stop the core, the class holds for stalls only. Host `debug-halt-guard`, `lpit-deadman-check`, `lpit-deadman-progress` with mutants; board halts of 30 s and 60 s (IO-32), falsified LPIT registers (B6), `SIRCDIV2` /64 (SYS-95) | configuration re-check of the LPIT channel (fmea SM-06); since SYS-95 also that its counter moves in every safety cycle (DFA-2: a counter that stands still or moves too slowly is `DEADMAN_CONFIGURATION_ERROR`, field 0x100; board 2026-10-04 with a slowed LPIT clock); a gated or lost LPIT clock: below the table |
| SM-07 | Start-up watchdog self-test | yes | at start | S1 held | at start | high | — | none needed; the latent-fault interval of SM-01 is the time between two starts (AoU-25) |
| SM-10 | MPU: protected RAM (fusa gate) | yes | on the access | fault exception → walk, dump, reset | exception latency + walk | high | 6-7.4.9; not a hardware diagnostic (freedom from interference), kept out of the hardware metrics. **E3**: a write into the protected RAM from outside the gate trapped on the target (board 2026-09-21), the MSP guard (SYS-97, board 2026-09-30 and 2026-10-03) | SM-11, SM-12, SM-13 |
| SM-11 | MPU state check on entry and exit | yes | 17 ms (10 ms) | S1 + S2 | 17 ms + walk | medium | 6-7.4.9, as SM-10. **E2**: gate left open, lost writes and every flipped bit of the gate word on the host, 9 built-in faults found (`safety-gate-mutations`); no injection on the target | none |
| SM-12 | MPU enabled and complete | yes | 17 ms (10 ms) | S1 + S2 | 17 ms + walk | high | 6-7.4.9, as SM-10. **E1**: MPU off or incomplete checked on the host (`bswgen-mpu`), no mutants, no injection on the target | none needed |
| SM-13 | MPU region descriptors unchanged | yes | 17 ms (10 ms) | S1 + S2 | 17 ms + walk | high | 6-7.4.9, as SM-10. **E2**: every field bit of the descriptors on the host, 15 built-in faults found (`safe-memory-mpu-regions-mutations`); board B17 without false alarm only | none needed (expected values of descriptors 1 … 7 in the protected RAM, SM-14, SM-16; descriptor 0 is a constant of the code compared in flash) |
| SM-14 | ROM check (CRC-32 over the flash areas) | yes | at start plus one pass (computed 3 … 6 ms, measured ≤ 5 574 µs, fmea B1); then one period + one pass (`rom_check_period_ms`, default 60 s) | S1 held | as detection + walk | high at start; at run time high with a period, low with period 0 | 11-5.1.12. **E3**: sees any change of the checked flash areas (every single-bit flip on the host, 21 mutants; on the board an image with one changed character held in the safe state, 2026-09-21); at run time only with a period, which must fit the integrator's multiple-point fault detection interval; does not see the bootloader's own code (fmea N4) | known-answer test of the CRC unit before every pass; overdue pass reported; since SYS-95 the safety task reports a re-check that completes no pass within three periods (DFA-1, idle context starved), context 7 |
| SM-15 | ECC double-bit error, RAM | yes | 17 ms (10 ms) after the faulty read | S1 + S2 | 17 ms + walk after the read | medium | 11-5.1.12. **E2**, reaction: the RAM ECC detects in hardware, neXtBSW contributes the reaction and its time; reaction on the host with mutants (`safe-memory-ecc`), on the board only that no flag survives a reset (B5, weak); the ECC's own latent faults are SM-55's | SM-55 at every start |
| SM-16 | ECC double-bit error, flash | yes | 17 ms (10 ms) after the faulty read | S1 + S2 | as SM-15 | medium | 11-5.1.12. **E2**, reaction: as SM-15 for the flash ECC; host with mutants only, no injection test for the flash ECC | flash ECC decoder not tested (fmea SM-55) |
| SM-55 | Latent-fault test of the RAM ECC | yes | at start | S1 + S2, then S1 held by the reset-loop limit | at start | high for the RAM paths exercised; flash not covered | the latent-fault test the gap analysis asked for (task 18). **E3** for the RAM paths: every modelled hardware fault on the host, 25 mutants; on the board an injected fault gives cause `0x107` (2026-09-29/30); the flash ECC not covered | runs once per start (AoU-25 applies as for SM-07) |
| SM-17 | Fault handlers with dump | yes | on the exception | walk first (assembler, no stack), dump, reset | exception latency + walk | high | 5-D.4. **E3** for illegal exceptions only: hard fault and bus fault reach walk, dump and reset on the target (2026-09-21; gated LPIT clock, SYS-95; MSP overflow, SYS-97); does not see a wrong computation that raises no exception | handler and table in flash (SM-14, SM-16) |
| SM-60 | Lock of the FlexNVM block (NVM-25, proposed) | **no** | not a detector: `read()` answers `BUSY` instead of undefined data; a store under a hold waits at most 260 ms | `BUSY` to the reader; the store fails after 260 ms and the NvM reports it | — | not claimed | — | unbalanced releases counted; readers by pointer outside the family port and CSEc commands do not take the hold yet ([`fmea.md`](fmea.md) SM-60, AoU-40) |
| SM-18 | ADC reference supervision | yes | 17 ms (10 ms) | S1 + S2 | 17 ms + walk | medium | 11-5.2.4. **E1**: sees a VREFH drift beyond the band against the on-chip bandgap; does not see a drift inside the band or a bandgap error (fmea SM-18); host decision logic only, no board injection | SM-19; since SYS-95 a converter that answers the bandgap and the supply channel alike is context 3 (DFA-3) |
| SM-19 | ADC read failure limit | yes | 170 ms (100 ms) | S1 + S2 | 170 ms + walk | high | 11-5.2.4, as SM-18. **E1**: sees a reference reading that fails `adc_read_error_limit` times in a row; host only, no mutants | none needed |
| SM-20 | Internal supply supervision | yes | 102 ms (60 ms); read failures 170 ms (100 ms) | S1 + S2 | 102 ms + walk | low to medium | 5-D.7. **E2**: sees the 3.3 V flash, 3.3 V oscillator and 1.2 V core supplies outside ± 200 mV (narrow for 3.3 V, wide for 1.2 V) and a channel that stops reading; shares VREFH with SM-18; host with mutants, no board injection | read-failure limit |
| SM-21 | Safety register lock | yes | at start-up | S1 + S2 | at start-up | medium | 5-D.4. **E2**: sees at start a pin of the lock lists with another function and a lock that did not take - the configuration registers of the lists only; host with mutants, board B8 shows the lock set; re-rate after SSR-070 | none |
| SM-22 | Safety register check | yes | 17 ms (10 ms) | S1 without reset | 17 ms + walk | medium | 5-D.4, as SM-21. **E3**: sees a changed register of the lists once per cycle (sampled); host with mutants; board 2026-10-01 (`PMC_LVDSC1` written gives `CHECK_REGISTER_ERROR`) | none |
| SM-23 | Safe outputs table, one list for three paths | yes (reaction) | — | the walk | < 1 µs computed, ≤ 21 µs measured bound | reaction | — | read-back of S1 since SYS-30, in S1 held, for the groups of SD and before the reset of the S1 + S2 sequence since SYS-138 (host only); open: the hard fault and watchdog handlers and the halt deadman of the family port, board proof B15/B16 (G1 rest) |
| SM-24 | One adapter per `safe_off` output, enforced at build time | yes | build time | build fails | build time | high (build) | — | — |
| SM-25 | De-energise before the reset | yes (reaction) | — | S1 before S2 | as SM-23 | reaction | — | — |
| SM-30 | Signal qualifier of the RTE | yes (with the application) | one period of the reading task | the application decides (AoU-12) | the application's | medium; none for in-range faults | 5-D.9. **E1**: sees values driven out of their valid range (open, short to a rail, failed conversion); does not see in-range faults (drift, stuck value, gain) - a second source (G2) would; host only | none |
| SM-31 | E2E protection P01 / P02 / P11 | yes (configured signals) | one reception period | the application decides (AoU-11) | the application's | high for configured signals | 5-D.6. **E3** for configured signals: sees a corrupted, repeated, lost or late frame only with CRC, counter and a configured timeout together, stated per profile; host with mutants (`e2e-mutations`), board 2026-09-16 (seven steps with injected errors) | none |
| SM-32 | CAN bus state and transmit gate; the transceiver (phy) half of the gate exists only with a transceiver that reports its status - a passive one counts as ready and the gate follows bus-off only (ASA-10, AoU-ASA-a) | yes | one supervision cycle | transmission blocked | one supervision cycle | medium | 5-D.6. **E3**: sees the controller's error states per bus (error passive, bus-off) and blocks transmission; does not see a transceiver fault without a status-reporting transceiver; board error passive by a wrong bit rate (COM-12, 2026-09-16) and bus-off with a shorted bus (COM-35, 2026-09-22); no mutants | none |
| SM-34 | Device reports to the safe supervisor | yes (reaction) | report: one safety cycle after posting; fault: within the detecting cycle | S3 for reports; S1 + S2, S1 held for `IDENTITY`, or SD if mapped | as the reporting package | reaction; coverage belongs to the reporting package (SYS-28) | — | a lost box delivery shows only as a missing report |
| SM-50 | Start reason from two sources, reset-loop limit | yes | at start | configured start behaviour; S1 held after N safety resets in a row | at start | high | — | record in ordinary RAM; an invalid record restarts the count, never holds by itself |
| SM-51 | Image check by the bootloader | yes (projects with the neXtBSW bootloader) | at start | image not started | at start | high | — | the bootloader's own code is not checked (fmea N4); the bootloader phase runs without MCU watchdog (section 4); since BL-36 checked again right before the jump (since BL-38 record, compatibility block, entry and the record's CRC field against the one image CRC of the start - no second CRC over the image), a record without compatibility block is not started |
| SM-53 | Defined run-down | yes (reaction) | per step time-out | restart | per step time-out | reaction | — | — |
| SM-33, SM-52 | CAN transceiver readiness through the SBC; reset counter of the SBC | **no** (device-dependent, stage 2) | — | — | — | — | — | — |
| SM-26 | Selectable safe state of a PWM group with fallback to freewheel (optional, SYS-116; accepted by `nxconfig` since SYS-139/SYS-140 - the bridge-fault rule `NX_SAFE_BRIDGE_FAULT_CLASSES` and the family port's hold of the short `NX_SAFE_STATE_SHORT_HOLD` are in the tree; host verified, board proof open; AoU-33) | **no** | — (reaction of the path that walks the table) | the group to the selected short or to freewheel; freewheel on any doubt; the short withdrawn before any walk when a bound device reports a bridge fault (SYS-140) | as SM-23 plus one word read per entry, computed | not claimed | — | a wild write of exactly one pattern; ECC error in the selection word ([`fmea.md`](fmea.md) SM-26) |
| SM-27 | Start-up test of the active short (SYS-116; called from `RteSystem::init()` before the init runnables since SYS-141, power-on only, bounded and with the gate-driver enable read back since SYS-143; host verified, board proof open; AoU-38) | **no** | at power-on, register level | short withdrawn until the next power-on, freewheel | at start | not claimed | — | proves the forcing register only, not gate driver or switches ([`fmea.md`](fmea.md) SM-27) |
| SM-58 | Hardware threshold without the core (optional, SYS-86; host verified, board proof not part of release R1) | **no** | comparator ≤ 500 ns plus 3 (4 + filter) timer clocks, computed | FTM forces the group's channels inactive, latched until reset | computed, not measured | not claimed | — | the path's registers are checked by SM-22; a stuck comparator output is not detected ([`hardware-mechanisms.md`](hardware-mechanisms.md) 5) |
| SM-59 | Low-voltage detect reset (hardware, switched on by the family port s32k1xx, SYS-125) | **no** | VDD below VLVD (2.80 … 3.00 V falling), continuous, in hardware | hardware reset, MCU held in reset until VDD is above VLVD plus hysteresis; the start counts as power-on | comparator response, no software time | not claimed | — | `LVDRE` written in every start and re-checked every safety cycle by SM-22 since SYS-153 (S1 without reset); off in VLPR/VLPS ([`fmea.md`](fmea.md) SM-59, [`hsi-s32k1xx.md`](hsi-s32k1xx.md) H9) |
| SM-40 … SM-43 | Device diagnostics of TMC6200, MC33771C, UJA113x, BTS5030 | **no** (stage 2, SYS-28) | the part in µs, the software after one driver cycle | driver latch | — | none claimed | — | concept 13 |
<!-- manual-mechanisms:end -->

**SM-34 and the hold of the `safe_off` outputs (SYS-151, SYS-194, SYS-196).** The family port s32k1xx holds every
`safe_off` output at its commanded level and takes a foreign active level back (SYS-151); since SYS-194 every
take-back is reported as `DEVICE_REPORT` (S3, class `ACTUATOR_STATE`, detail = output number; [`fmea.md`](fmea.md)
SM-34). Foreign active levels of `safe_off` outputs taken back by the hold are recorded as DTC
`safety.safe_off_take_back` (+0x05FA, QM, recording only; snapshot: output, its take-backs, total; SYS-196,
[`dem.md`](../../libs/bsw/dem/doc/dem.md)). Only projects with a `safe_off` output may describe it (`nxconfig`
error otherwise). The integrator shall evaluate a stored 0x1605FA as an indication of a foreign write to a
power-stage enable (debugger, stray software write). Verified on the host and on the board 2026-10-03
(`tools/boardtest/captures/evalboard_s32k144_demo/2026-10-03_board/sys194_196/`). *Credit (DOC-58, finding RN of
the re-check of CM-07):* SM-34 is credited as a reaction, as in `mechanisms.sdoc` and `fmea.md` issue 2 (31
credited blocks); its coverage belongs to the reporting package (SYS-28). Since concept issue 3 SSR-091 (the
withdrawal of the active short on a bridge fault) specifies SM-34.

**Faults of the time bases of the supervision (SYS-95; board 2026-10-03/04, safety review SYS-95 board 2026-10-04).**
What reacts, and when, if a clock of the supervision itself fails (eval board, `dfa.md` DF-05 and DFA-6;
records `tools/boardtest/captures/evalboard_s32k144_demo/2026-10-03_board/sys95/`):

| Fault | What reacts | Time to de-energised outputs / reset | Evidence |
|---|---|---|---|
| OpenBSW system time (DWT `CYCCNT`) too slow or too fast | SM-04 period part (DFA-6): `SAFETY_CYCLE_PERIOD_VIOLATION` kind 4 (slow) or kind 5 (fast), S1 + S2 | at the end of a window of the period check (eval board 7 cycles, 70 ms) + walk | host (`progress-checks`, `safety-progress`); board B4: no false alarm |
| OpenBSW system time **stands still** | **not** kind 4: the safety task is scheduled through exactly this time base and does not run any more. The halt deadman SM-06 walks the table (its LPIT counter does not depend on the system time); the reset follows through the watchdog SM-01 (warning interrupt walks, then reset) or, as soon as the system time runs again, through the period check | outputs off after about **10 ms** (SM-06; the deadman time-out, a constant of the family port); reset after about **160 … 283 ms** (SM-01) - **derived**: the watchdog time-out over the LPO range of the derivative (230 … 283 ms, [`hsi-s32k1xx.md`](hsi-s32k1xx.md) `WDOG_TOVAL`) minus at most one service interval (70 ms) | board 2026-10-04: SM-06 expired with `CYCCNT` standing (`_expired` = 1, `T_EN` = 0); board 2026-10-03: `WATCHDOG RESET: outputs off` (B5); with a debugger halt and resume the system time ran again and the reset came as `SAFETY_CYCLE_PERIOD_VIOLATION` kind 1 (PLAT-67) |
| LPIT clock gate (`PCC_LPIT[CGC]`) switched off | every LPIT access is a bus fault: the fault handlers SM-17 walk the table, dump and reset; DFA-2 cannot see a gated clock and does not need to | reset within about **1 ms** - **derived**: the next access to the LPIT comes with the next poll of the halt guard (1 ms, constant of the family port) | board 2026-10-03 (B2: `HARD FAULT RESET: outputs off`) |
| LPIT clock too slow (for example `SIRCDIV2` changed) | DFA-2 (SM-06 progress check): `DEADMAN_CONFIGURATION_ERROR` field 0x100, S1 + S2 | within a few safety cycles | board 2026-10-04 (`SIRCDIV2` /64: `SAFETY RESET (DEADMAN_CONFIGURATION_ERROR, fields 0x100)`) |
| SIRC (the LPIT's clock source) fails while the core runs | **residual risk**: no log line, no walk; a hardware reset of the MCU watchdog (`RCM_SRS` 0x20) follows after at most about **283 ms** (**derived**: the watchdog time-out at the slowest LPO); until then the outputs are **not shown** to be de-energised (SM-06 depends on the same clock). The cause of the hang is open (with the core halted the debugger reads the LPIT registers without error, so a blocked bus is not confirmed) | outputs in their reset state with the watchdog reset, at most about 283 ms (derived, as left; AoU-07) | board 2026-10-03 (B2b), 2026-10-04 (D1); investigation and architecture (for example the deadman on another clock, or an external watchdog on the enable line) with **SYS-195**, after release R1 (`dfa.md` DF-05) |

For his fault tolerant time intervals the integrator takes the last row as the time of a lost SIRC (AoU-05), or
provides an independent shut-off path (AoU-13). *Derived* marks a time computed from the values of section 6 (and the
data sheet range of the LPO); the board records in the last column show the reaction, not the bound (since the
re-check of CM-10, point N4). The concept carries the same rows in its section 7.2 since issue 3 (CM-07, F-06).

What the integrator does with this table: he takes, per goal he relies on, the mechanisms named in section 3.1,
compares their FHTI with his fault tolerant time interval (AoU-05), rates each mechanism against the clause named,
with the class and neXtBSW's rating as input and his own rationale, in his hardware metrics with his failure rates
(AoU-09), and sets the latent-fault intervals (AoU-25, AoU-CFG-4).

---

## 6. Timing: what the element guarantees, and what the integrator compares

The times come from four values (concept 7.1; defaults of the demo project):

| Value | Where set | Default |
|---|---|---|
| safety task period | `modules.safeLifecycle.task_period_ms` (project, since SYS-25) | 10 ms |
| watchdog service interval | derived by `nxconfig`: floor(time-out × 1000 / (3 × period × LPO maximum per mille)) cycles | 7 cycles = 70 ms |
| MCU watchdog time-out | `modules.safeLifecycle.watchdog_timeout_ms` (project, since SYS-25) | 250 ms |
| halt deadman time-out / poll | code constants of the family port (IO-32; config-range CV-20, finding G3) | 10 ms / 1 ms |

The relation three services before the time-out at the fastest LPO of the derivative is checked by `nxconfig`
(`safety_timing`, test `nxconfig-watchdog`) and by `static_assert`s; the S32K1 counter limits the time-out to 511 ms
(concept 7.1, config-range CV-02). Other values scale the times of section 5 as formulas of the values
([`config-range.md`](config-range.md) section 3); the tables above hold for the defaults.

Two traps the concept calls out (7.3): **a reset is not instantaneous** (restart times in section 4), and **10 ms is
the floor for anything in the safety task, not a guarantee** — preemption by higher-priority tasks and interrupts adds
to it; `tools/schedana` is where the integrator shows his schedule (AoU-08, AoU-CFG-3). A later fast control loop at
PWM rate (ARCH-65, `../architecture/fast-control-loop.md` (not published, available on request), not implemented)
would be an interrupt above the safety task; its credited backstops stay SM-04, SM-06 and SM-01 (section 8.1 there).

---

## 7. Assumptions of use and how to fulfil them

An integrator who cannot meet an assumption either does not get the corresponding claim, or adds his own measure
(concept 4). He validates every assumption against his item before integration and handles a mismatch as a change
with an impact analysis (10-9.2.4.5). **Status** says where the assumption stands: `concept` (in concept section 4),
`proposed` (proposed in the named analysis and carried into this manual; confirmed with the acceptance of this
manual), `new here` (numbered here from an existing
statement that had no number yet). The note *added after acceptance* at the end of the source column marks a row
that was not part of the accepted issue 1 (section 16); these rows were reviewed in CM-10 (record CR-10-1,
2026-10-04, D-01) and are part of issue 2. *Since CM-10 (Z4):* AoU-37, AoU-38 and AoU-39 read `concept` and are
binding. *Since CM-10 (F-03):* AoU-33 is in force for every project with an active short, and AoU-ASA-b is a row of
its own. *Since CM-07 (concept issue 3, decisions ZF and ZG; DOC-51):* AoU-33 is a node of concept section 4.3 and
AoU-24, AoU-25 and AoU-40 are binding there; all four read `concept`. *Since the re-check of CM-02 (decision Z7I b,
2026-10-04; DOC-59):* AoU-ASA-a, AoU-ASA-b and AoU-ASA-d read `concept` and are binding, because the residual risks
accepted in CM-02 (AN-172, AN-170, AN-165) rest on them; they are made binding by that decision, not yet nodes of
concept section 4. *Since DOC-38 (review DOC-19,
D2):* AoU-26 … AoU-29 and AoU-34 … AoU-36 are in concept section 4 and read `concept`; the source column keeps where they
came from. With the acceptance of this issue (2026-09-30, D3) every `proposed` row of that issue is binding; a row *added after acceptance* is not covered by that acceptance. *How to fulfil* restates the
source; where the source names no method, the column says so.

### 7.1 Item, goals, ASIL and safe states

<!-- manual-aou:start -->
| ID | The integrator's obligation | How to fulfil it | Source | Status |
|---|---|---|---|---|
| AoU-01 | Perform the hazard analysis of his item, derive safety goals with ASIL and determine the fault tolerance time of each goal | his own item-level work; neXtBSW provides none of it | [concept 4.1](concept.md), safety plan T-01 | concept |
| AoU-02 | Check the assumed safety goals ASG-01 … ASG-07 against his own; re-evaluate the mechanisms for every goal he replaces or adds | compare section 3 with his safety concept; for a changed element version decide the impact at item level (safety plan T-04) | [concept 4.1](concept.md) | concept |
| AoU-03 | Rely on the element for ASIL B only; ASIL C and D are a later stage | an item with a C or D goal does not rely on neXtBSW for it | [concept 4.1](concept.md), 2.1 | concept |
| AoU-04 | Confirm that S1, S1 held, the reset and restart cycle, and an ECU that stays de-energised and silent until the next power cycle are safe states of his item; for every degraded mapping AoU-32 - the element degrades a device fault and, since SYS-135, the trip of a hardware shut-off path (`HW_SHUTOFF`), never a fault of the MCU or a finding of the path; no fail-operational use after an MCU fault | confirm (a) … (d) of the concept for his item, using the restart times of section 4 | [concept 4.1](concept.md), 3 | concept |
| AoU-05 | Confirm that the fault tolerant time interval (FTTI) of every goal of his item is longer than the maximum fault handling time interval (FHTI) of the mechanisms he relies on, including the faults of the time bases of the supervision | compare with the FHTI column and the table of the time bases of section 5 (and concept 7.2) | [concept 4.1](concept.md), 7.3 | concept |
| AoU-32 | For every degraded mapping - of a device fault or of the trip of a hardware shut-off path (`HW_SHUTOFF`, exactly the group the path switches off) - confirm that the remaining function is safe with the mapped groups off for as long as the item may run so, and that the groups are independent in his hardware (no shared gate-driver supply, current sensor, connector); for an enable bound to no PWM group, that forcing the channels is enough | `nxconfig` refuses a shared timer and handles a shared device bus; a shared supply or sensor he checks in his schematic | [concept 4.1](concept.md), [partner-and-degraded 4.6](partner-and-degraded.md) | concept |

### 7.2 Configuration

| ID | The integrator's obligation | How to fulfil it | Source | Status |
|---|---|---|---|---|
| AoU-06 | Declare every output whose energy matters: PWM groups in the project, every other output with `safe_off: true` at `connect.<name>` of its driver's manifest; an undeclared output is not switched off by any path | review his outputs against the safe outputs table; the build enforces one adapter per declared output (SM-24), not the declaration itself | [concept 4.2](concept.md) | concept |
| AoU-07 | Make the inactive level of every PWM group and `safe_off` output right for his circuit, including the pin state while the MCU is in reset; fit external pull resistors | check against his schematic | [concept 4.2](concept.md), [hsi-s32k1xx.md](hsi-s32k1xx.md) | concept |
| AoU-08 | Choose task set, periods and priorities so that the safety task runs at its period under worst-case load | schedule analysis (`tools/schedana`); measure the lateness as AoU-CFG-3; period and time-out are project values, the service interval is derived (concept AoU-08 corrected by DOC-38) | [concept 4.2](concept.md), 7.1 | concept |
| AoU-10 | Treat derivative, board and project YAML files as safety-related configuration data under the same change control as source code, and re-run the verification after a change | his configuration management; AoU-CFG-1 | [concept 4.2](concept.md) | concept |
| AoU-DFA-c | Verify the configuration itself - inactive levels, `safe_off` declarations, dead times, MPU regions - against his design and on his hardware: the run-time register, MPU and safe-outputs checks (SM-13, SM-21, SM-22, SM-23) compare with the configured values and detect a **change after start**, not a **wrong configuration** (claim limit DFA-5) | review of the configuration against his schematic and design; board tests of the generated result per project (AoU-06, AoU-07, AoU-10, AoU-CFG-2) | [dfa 9](dfa.md), DFA-5; added after acceptance | proposed |
| AoU-11 | Choose the safety-related signals and configure an E2E profile for each | `rte.yaml` `can.qualifiers.<message>.e2e` (config-range CV-17) | [concept 4.2](concept.md) | concept |
| AoU-12 | In the application, read the quality qualifier with every ADC value and do not use a value marked `QLF_ERROR` or `QLF_ERROR_RANGE` | application design | [concept 4.2](concept.md) | concept |
| AoU-24 | Check that the ADC reference tolerance relative to the board's reference is smaller than the accuracy his safety-related analogue values need (a drift inside the band is a silent gain error, F16) | compare `modules.safeSystem.adc_reference_tolerance_mv` / `board.adc_reference_mv` with his accuracy need; record the rationale (AoU-CFG-5) | [concept 4.3](concept.md) since issue 3 (CM-07 ZG); before: [fmea 6](fmea.md) | concept |
| AoU-25 | Restart the ECU at least as often as his latent-fault interval requires for the once-per-start tests (SM-07, SM-55, SM-14 with period 0), or set the ROM re-check period to at most that interval and longer than the longest flash write | `modules.safeMemory.rom_check_period_ms` (AoU-CFG-4) | [concept 4.2](concept.md) since issue 3 (CM-07 ZG); before: [fmea 6](fmea.md) | concept |
| AoU-30 | Use the three pins of the `safety` section only in their role: `safe_state_output` is an indicator, not a power output and not written by the application; `locked_pin` is not reconfigured by any driver; `test_pin` is unconnected | check against his schematic; `nxconfig` warns on an application writer (confirmation key `safety.safe_state_output_shared_with_application` with his reason) and refuses a test pin the board names as wired | [concept 4.2](concept.md) | concept |
| AoU-CFG-1 | Build the image only through the build, treat every `nxconfig` warning on a value of the register as a finding to resolve or justify in writing, keep the configuration under change control | build as delivered; see section 8 | [config-range 5](config-range.md) | proposed |
| AoU-CFG-2 | For every value that differs from the defaults, repeat on his ECU the board checks of `fmea.md` section 8 that depend on it and record them with the configuration | fmea section 8 checklist | [config-range 5](config-range.md) | proposed |
| AoU-CFG-3 | Measure the start distance of the safety cycle under his worst-case load and set `cycle_lateness_ms` from it with margin | as board check B4 of `fmea.md` | [config-range 5](config-range.md) | proposed |
| AoU-CFG-4 | Set `rom_check_period_ms` longer than the longest flash write of his application and not longer than his latent-fault interval | no check enforces the lower bound today (config-range G2, proposal C2) | [config-range 5](config-range.md) | proposed |
| AoU-CFG-5 | Choose the data-set values from the needs of his function and record the rationale: ADC tolerance, read-error limit, device-bus window, E2E profile and data ID | config-range CV-07 … CV-09, CV-17 | [config-range 5](config-range.md) | proposed |
| AoU-CFG-6 | Confirm that the 10 ms halt-deadman time-out and the other fixed times fit his fault tolerance times, or request a change | compare with AoU-05; the time-out is a code constant (config-range G3, proposal C3) | [config-range 5](config-range.md) | proposed |
| AoU-CFG-7 | If the neXtBSW bootloader starts his image, keep the ROM check (SM-14) active in it - built through the build, re-check period or restart interval within his latent-fault interval - because the check before the jump has no second full CRC since BL-36/BL-38 (review BL38-P1, F5) | AoU-CFG-1, AoU-CFG-4, AoU-25; fmea SM-51 | [config-range 5](config-range.md); added after acceptance | proposed |

### 7.3 Hardware around the MCU

| ID | The integrator's obligation | How to fulfil it | Source | Status |
|---|---|---|---|---|
| AoU-09 | Supply the hardware side: failure rates of MCU and parts, single-point and latent-fault metrics, the hardware measures the part manufacturers require | his hardware analysis; section 5 gives per mechanism what the metrics need | [concept 4.2](concept.md), safety plan T-02 | concept |
| AoU-13 | Fit and configure an external watchdog if his concept needs a time base independent of the MCU (`watchdog_trigger` role); never copy the eval board's switched-off SBC watchdog into a product | board design; SM-42 is stage 2 and not credited | [concept 4.3](concept.md) | concept |
| AoU-14 | Couple the watchdog trigger to alive signs of other tasks if he needs program-flow supervision over all tasks; today it follows one task | his application design | [concept 4.3](concept.md) | concept |
| AoU-15 | Confirm that a hardware reset counter (SBC) survives his start-up budget, including self-test resets | board design; the eval board leaves room for one failed start | [concept 4.3](concept.md) | concept |
| AoU-16 | Decide whether the debug port is disabled in the product; no software mechanism closes it | AoU-SEC-7 | [concept 4.3](concept.md) | concept |
| AoU-34 | If he selects `bootloader.sbc_watchdog: serve`, **or his SBC runs without software development mode** (UJA113x SDMS 0 — then the bootloader serves the watchdog with `leave` too, BL-41, and since BL-42 also if the SBC is only read, not in `bootloader.init`: watchdog setting alone, SBC stays in Standby), the application takes the SBC watchdog over at the latest 1 024 ms after the jump, also on a slow start; while the bootloader runs, the SBC watchdog supervises only the bootloader's main loop, not its program flow; the updater stays locked with SDMS 0. `leave` takes effect only with development mode (SDMS 1): the bootloader does not touch the watchdog. *Added by BL-41 (to be confirmed by the safety responsible):* with SDMS 0 his board reaches the bootloader's watchdog setting within the first watchdog period after an SBC reset (UJA113x: timeout, 128 ms after an SBC reset, running in Standby already; public data sheet UJA113X_SER Rev. 2, see HSI 4.13), including the SBC start-up the bootloader waits for (at most 100 ms) | project value `bootloader.sbc_watchdog` (checked by `nxconfig` with its YAML path) and the SDMS bit of his SBC (`bl_sbc_watchdog.reason`, `bl_sbc_diag.readyMs` over J-Link); the application's SBC driver triggers a running watchdog with the value it finds at `startup()` | [concept 4.3](concept.md); before: BL-28 in `CHANGELOG.md`, `doc/reference/project.md` (`bootloader.sbc_watchdog`); BL-41 (`doc/changelog.d/BL-41.md`, `doc/safety/hsi-s32k1xx.md` 4.13); BL-42 (`doc/changelog.d/BL-42.md`) | concept |
| AoU-36 | Obtain the MCU manufacturer's safety manual and apply the software measures and configuration it requires; neXtBSW does not have it and every hardware coverage statement is about its use of a feature | his supplier; compare with section 5 | [concept 4.3](concept.md); before: [fmea 9](fmea.md) N2, gap proposal C8, safety plan T-06 | concept |
| AoU-33 | For every PWM group with an active short (`pwm.<group>.safe_states`, SM-26): the choice of the safe state (freewheel or short) is part of his safety function, and the software that selects it runs at the ASIL of that goal; the board's reset state of the bridge is tolerable during reset and restart; the start test of the short (SM-27) runs only at standstill, where no phase voltage is measured (AoU-38 refines this for the start test) | his safety concept for the choice and its writer; his schematic for the reset state of the bridge; AoU-38 for the start test | [concept 4.3](concept.md) (node of [`assumptions.sdoc`](../requirements/safety/assumptions.sdoc) since concept issue 3, CM-07 ZF, points (a) … (d)); before: [partner-and-degraded 7.1](partner-and-degraded.md); announced in issue 1 (section 7.6), in force for every project with an active short since CM-10 (CR-10-1 F-03) | concept |
| AoU-37 | For every hardware threshold (`hw_thresholds`), keep the signal at the comparator input at every start further below the comparator supply (VDDA) than the comparator's input offset plus its hysteresis (data sheet: offset at most 25 mV); a signal at or near the rail fails the start test of the path, which then holds the group off and S1 (fail-safe, the ECU does not start its function) | check his circuit (divider, amplifier output range, clamp) against VDDA at start-up, including a supply that ramps; proposed by SYS-135 (review SYS-128/SYS-130, C3), binding since CM-10 (Z4); the hardware threshold path is host verified, its board proof is not part of release R1 | [concept 4.3](concept.md); [hardware-mechanisms 2.5](hardware-mechanisms.md); added after acceptance | concept |
| AoU-38 | For every PWM group with the start test of the active short (`pwm.<group>.safe_state_test`): the machine does not turn at power-on (the test runs only then) or a short of `safe_state_test_us` at a turning machine is harmless; the group's gate-driver enable is off until the application enables it (read back through a bound gate driver; without one, his circuit keeps the forced pattern from the bridge); the tasks below the first application task, among them an external watchdog's trigger, tolerate the sum of all holds (at most a third of the MCU watchdog time-out) once per power-on; after a low-voltage reset or an unknown reset reason the group has freewheel only until power-on | check his start-up and circuit against the four points; proposed by SYS-143 (review SYS-136/SYS-141, E2-E4), binding since CM-10 (Z4) | [concept 4.3](concept.md); [partner-and-degraded 5.10](partner-and-degraded.md); added after acceptance | concept |
| AoU-39 | Keep VDD, including tolerance, ripple, load steps and the ramp at start, above 3.05 V (the highest low-voltage detect threshold plus its hysteresis): the family port s32k1xx switches the MCU's LVD reset on (SM-59), a VDD below it resets the ECU or keeps it from starting; do not rely on the LVD in VLPR/VLPS | a 5 V supply holds it by a wide margin; on a 3.3 V supply show the margin (at most 0.3 V) in the supply design; proposed by SYS-125 (AN-066), binding since CM-10 (Z4) | [concept 4.3](concept.md); [fmea SM-59](fmea.md); added after acceptance | concept |
| AoU-DFA-a | Confirm that the MCU's own power-on and low-voltage reset (or an external supply supervisor) brings the MCU into reset **before** its supply leaves the range in which the MCU manufacturer guarantees correct operation; the supply check SM-20 runs on the same die and is credited only for a drift while the die still works correctly (DF-01) | his supply design against the MCU data sheet and safety manual (AoU-36); AoU-39 for the LVD reset neXtBSW switches on | [dfa 9](dfa.md), DF-01; added after acceptance | proposed |
| AoU-DFA-b | Accept that the stage-1 mechanisms detect faults that stop, divert or delay the program flow, or raise an exception, on an independent time base, but **not** a core fault that computes wrong results on a correct program flow (DF-06); where his hardware metrics need that coverage, provide it - a core self-test required by the MCU manufacturer's safety manual, an external challenge-response watchdog (AoU-13) or a second channel | his hardware analysis (AoU-09) and the MCU safety manual (AoU-36) | [dfa 9](dfa.md), DF-06; added after acceptance | proposed |
| AoU-ASA-a | On a board whose CAN transceiver reports no status (passive transceiver, `canPhyPassive`), accept that the transmit gate of SM-32 follows the bus-off state only: confirm that a transceiver fault that does not lead to bus-off is acceptable for his item, or add his own supervision of the transceiver | check the transceiver of his board; with a passive one, argue the acceptance or supervise it himself | [architecture-safety-analysis 7.1](architecture-safety-analysis.md), ASA-10; binding since the re-check of CM-02 (CR-02-1 Z7I b, 2026-10-04: the accepted residual risk AN-172 rests on it); added after acceptance | concept |

### 7.4 Software, tools and process

| ID | The integrator's obligation | How to fulfil it | Source | Status |
|---|---|---|---|---|
| AoU-17 | His application is QM unless he allocates safety requirements to it; a safety-related SWC is developed together with every QM module that still has an open coexistence row at the target ASIL, or he provides his own partitioning with the evidence of `coexistence.md` section 7; safety-relevant received signals with E2E; no diagnostic routine or I/O control drives a power output without his own argument | [`coexistence.md`](coexistence.md) per module; SYS-21 brings the ASIL B partition | [concept 4.4](concept.md) | concept |
| AoU-18 | His application respects its configured periods; SM-05 only logs a violation, the reaction within his FTTI is the application's | configure the periods realistically; react in the application (gap proposal C18) | [concept 4.4](concept.md) | concept |
| AoU-19 | Know which events reach the error memory: the degraded reaction SD, which keeps the ECU running, is sampled by a DTC (`safety.device_fault`); the cause of a resetting event is reported as a DTC at the next start (`safety.<event>`, `safety.mcu_watchdog`, `safety.fault_exception`, `safety.reset_other`, `safety.reset_loop` after two or more safety resets in a row; SYS-113), but only if the project has the DTC or the collective DTC; S1 held without a reset, `CHECK_REGISTER_ERROR`, `RTE_TIMING_VIOLATION` and `DEVICE_REPORT` store no DTC | his own storage where he needs more than the DTC of the next start (for example the cause of an event that stores none) | [concept 4.4](concept.md) | concept |
| AoU-20 | Accept, qualify or replace the non-qualified RTOS, compiler and libraries | section 10; [`component-qualification.md`](component-qualification.md) | [concept 4.4](concept.md) | concept |
| AoU-21 | For every safety-related device obtain the manufacturer's safety manual or application note and check the driver's use of the part; until SYS-28 whatever his argument needs from a part's diagnostics is his | his supplier documents; concept 13 lists the open questions per part | [concept 4.4](concept.md) | concept |
| AoU-22 | Run the same static analysis (or a qualified one) on his own code in his project configuration and handle findings with the same rules; MISRA C++:2023 is checked by no free tool | `tools/misra` with his compile database; [`static-analysis.md`](static-analysis.md) | [concept 4.4](concept.md) | concept |
| AoU-23 | Provide the detection for ASG-03 and ASG-04, and the missing part of ASG-01, himself — in the application, in hardware, or by arguing the device diagnostics under AoU-21 — until G1 … G5 are closed and stage 2 exists | his technical safety concept | [concept 4.4](concept.md) | concept |
| AoU-ASA-d | Until ASA-2 exists, switch the actuators off in the application when the ECU mode view reports `RUNDOWN`: the RTE does not drive the outputs to their init value at run-down; the safe outputs table is locked off only by the last run-down step (SYS-174, fmea SM-53), so until then an output keeps its last command | application design: react to `RUNDOWN` of the ECU mode view | [architecture-safety-analysis 7.1](architecture-safety-analysis.md), ASA-2; binding since the re-check of CM-02 (CR-02-1 Z7I b, 2026-10-04: the accepted residual risk AN-165 rests on it); added after acceptance | concept |
| AoU-40 | Keep a hold on the FlexNVM block (read in place, `dataflash::DirectRead`) short and do not take it in a task of lower priority than a task that stores into the EEPROM emulation: under a hold a store waits at most 260 ms and then fails with a reported write error (SM-60) | the task layout of his project; or show that a failed NvM store is acceptable for his item | [concept 4.4](concept.md); NVM-25; binding since concept issue 3 (CM-07 ZG); added after acceptance | concept |
| AoU-41 | Do not enable a bus master other than the core (DMA/eDMA = M2, Ethernet = M3) and let none access RAM until SYS-21 restricts MPU descriptor 0 for M2 and M3: the descriptor grants them read, write and execute access everywhere, so the protected RAM is not protected against them | no neXtBSW driver enables such a master today; with one, the integrator argues the protection himself (hsi-s32k1xx.md H10, fmea.md N3) | [concept 4.4](concept.md); review Welle-0210-Tag S171-1; added after acceptance | concept |
| AoU-42 | Run every task privileged: the short interrupt lock (`cpsid`) of SYS-172 around the check and the write of the active short takes effect only in privileged code and is ignored silently in the unprivileged thread mode | all tasks of the image privileged; with unprivileged tasks first add a lock that works for them (supervisor call) | [concept 4.4](concept.md); review Welle-0210-Tag SYS-172 P1; added after acceptance | concept |
| AoU-26 | Generate the image only through the build with the pinned tool versions, never edit a generated file, run the tool checks of `tool-classification.md` section 4 on his own configuration after every change; verify this predetermined classification for his item; run every image at least once on the target before release | section 9 | [concept 4.5](concept.md); before: [tool-classification 6](tool-classification.md) | concept |
| AoU-27 | Do not use the Studio's display as evidence; evidence is the output of the command-line tools and `tools/test` from the repository's interpreter as recorded by the CI | section 9 | [concept 4.5](concept.md); before: [tool-classification 6](tool-classification.md) | concept |
| AoU-28 | Disable XCP calibration and diagnostic writes that reach a safety-relevant output or parameter in the product, or interlock each such write, and verify a calibrated safety-relevant parameter by reading it back without the A2L conversion | `memory.calibration.profile: production`; console `cal values`; plausibility of SYS-90 limits but does not replace the read-back | [concept 4.5](concept.md); before: [tool-classification 6](tool-classification.md) | concept |
| AoU-ASA-b | In the product, build XCP not in or protect it; protect every UDS service that writes a component port (DIDs, routines, I/O control) **and the ECU reset (service 11)** by security access (27) **and** by a precondition he defines (for example vehicle stationary, power stage off), until ASA-1/ASA-8 provide one; the ECU reset of the application has no precondition and no security level today (security-interplay IF-04) | security levels in his `diag.yaml` for 11 and for every writing DID and routine; the precondition in the application hook for session change and ECU reset (AoU-SEC-3); XCP as AoU-28 and AoU-SEC-5; DIDs and routines as AoU-SEC-4 | [architecture-safety-analysis 7.1](architecture-safety-analysis.md), ASA-1, ASA-8; a row of its own since CM-10 (CR-10-1 F-03, Z5: security access for the ECU reset was missing in AoU-SEC-3/AoU-SEC-4); binding since the re-check of CM-02 (CR-02-1 Z7I b, 2026-10-04: the accepted residual risk AN-170 rests on it); added after acceptance | concept |
| AoU-29 | Classify and qualify his code generator (Embedded Coder) himself and verify the generated model code himself; neXtBSW checks only the interface | section 9.3 lists what neXtBSW supplies | [concept 4.5](concept.md); before: [tool-classification 6](tool-classification.md) | concept |
| AoU-35 | Take over under his own management of functional safety that neXtBSW has no quality management system, no evidence of competence of the persons in its lifecycle, no independent confirmation review yet and no functional safety assessment; every tag is a development release until the reviews are closed | his QMS covers the integration; he may assess the element within his item assessment (safety plan T-09) | [concept 4.1](concept.md); before: safety plan 1.2, T-03, T-09, 6; gap proposal C8 | concept |
<!-- manual-aou:end -->

### 7.5 Cybersecurity

AoU-SEC-1 … AoU-SEC-8 are in section 11.

### 7.6 Announced, not in force

Proposed by [`partner-and-degraded.md`](partner-and-degraded.md) 7.1 for a mechanism that does **not exist yet**; it
becomes an obligation when SYS-115 (partner supervision) is implemented. *AoU-33* (active short, SYS-116) was announced
here in issue 1; since SYS-139/SYS-140 `nxconfig` accepts the short, and with CM-10 (CR-10-1 F-03) AoU-33 is an
obligation for every project with an active short (section 7.3; a node of concept 4.3 since CM-07 ZF):

| ID | Announced obligation | Source |
|---|---|---|
| AoU-31 | state the fault tolerance time per partner MCU and the board's shut-off line time; confirm the common-cause assumptions (supply, clock, connector) for his board | partner-and-degraded 3.11, 7.1 |

### 7.7 Proposals of the analyses covered by existing rows

Proposed assumptions of the analyses that are not carried as rows of their own, because existing rows say the same
(added after acceptance; CM-10 judged the cover of AoU-ASA-c complete and that of AoU-ASA-b incomplete - security
access for the ECU reset was missing -, so AoU-ASA-b is a row of section 7.4 since CR-10-1 F-03):

<!-- manual-aou-covered:start -->
| ID | Proposed by | Covered by |
|---|---|---|
| AoU-ASA-c | [architecture-safety-analysis 7.1](architecture-safety-analysis.md) (FM-BL-04): de-energised pins of every power output in reset and while the bootloader runs | AoU-07 (pin state in reset), AoU-SEC-3 (reset state safe while the bootloader runs), section 4 condition (1) of the unsupervised bootloader phase and section 10.2 |
<!-- manual-aou-covered:end -->

---

## 8. Configuration rules for safe operation

**The route in two sentences** ([`config-range.md`](config-range.md) section 1). The *relations* the stage-1
mechanisms need are verified for the **whole declared range** by checks that run on every configuration
(`tools/nxconfig` with the YAML path, `static_assert` on the generated values, generator refusals), so a configuration
outside the admissible range does not build. The *behaviour* of the built software is verified **per data set** —
for the project's own profiles by the project, for his data set by the integrator (AoU-CFG-1 … AoU-CFG-7).

**The value register** is `config-range.md` section 2 (CV-01 … CV-20, 19 values plus the deadman constant): level,
declared range, relation checked, what the project verified, route. 11 values are argued for the range, 8 per data set.
All target builds of the project use the **default** safety values (finding G1); on the board only the demo values
ran.

**Rules**, each from its source:

1. Build the image only through the build; never edit a generated file; tool versions pinned (AoU-26, AoU-CFG-1).
2. An error of `nxconfig` stops the configure run; a warning on a safety value is a finding the integrator resolves
   or justifies in writing (AoU-CFG-1).
3. Every power output in the safe outputs table: PWM groups in the project, `safe_off: true` in the manifest
   (AoU-06); inactive levels right for the circuit (AoU-07).
4. The three pins of the `safety` section only in their role (AoU-30).
5. The safety task stays above every application task (`above_application`, checked; config-range CV-18); the
   watchdog service interval is derived, not set (concept 7.1).
6. `rom_check_period_ms` between the longest flash write and the latent-fault interval; 0 means once per start
   (AoU-25, AoU-CFG-4).
7. A calibration segment with a parameter whose `safety` is not `qm` needs `on_invalid: refuse`; no XCP in the
   `production` profile (config-range CV-15; AoU-28, AoU-SEC-5).
8. One E2E profile and data ID per safety-relevant signal (AoU-11, config-range CV-17).
9. Degraded mappings only for degradable events, in groups that share no timer; the integrator confirms independence
   beyond timer and bus (AoU-32; SSR-088).
10. The product image is built with `-DNX_PRODUCT_BUILD=ON` (no test and fault-injection commands; AoU-SEC-6).
11. `bootloader.sbc_watchdog` chosen deliberately (AoU-34).
12. A hardware threshold (`hw_thresholds`, SM-58) may be configured but carries no credit (section 5); host verified,
    board proof not part of release R1; the input stays away from VDDA at start (AoU-37).
13. On s32k1xx the LVD reset is always on (SM-59, not credited): the supply keeps VDD above 3.05 V (AoU-39).
14. A PWM group with the start test of the active short (`pwm.<group>.safe_state_test`) only under the four
    conditions of AoU-38.
15. Holds on the FlexNVM block short and never in a task below one that stores into the EEPROM emulation (AoU-40).
16. No bus master other than the core enabled until SYS-21 (AoU-41); every task runs privileged (AoU-42).
17. With the neXtBSW bootloader the ROM check SM-14 stays active in the image (AoU-CFG-7).
18. An active short (`pwm.<group>.safe_states`, SM-26) only under AoU-33; the ECU reset (UDS 11) and every UDS
    service that writes a component port protected by security access and a precondition (AoU-ASA-b).

---

## 9. Tools

### 9.1 Classification

From [`tool-classification.md`](tool-classification.md) section 3 (the software tool criteria evaluation report in the
sense of 8-11.5.1). TI = tool impact, TD = tool error detection, TCL = tool confidence level; *target* is the level
after the proposals named there.

<!-- manual-tools:start -->
| Tool | TCL today | TCL target |
|---|---|---|
| nxconfig | TCL 2 | TCL 1 |
| bswgen | TCL 2 | TCL 1 |
| blgen | TCL 2 | TCL 1 |
| rtegen | TCL 2 | TCL 1 |
| dbcgen | TCL 1 | TCL 1 |
| diaggen | TCL 2 | TCL 2 |
| a2lgen | TCL 2 | TCL 2 |
| schedana | TCL 2 | TCL 2 |
| Studio | TCL 1 | TCL 1 |
| romCheckImage | TCL 1 | TCL 1 |
| calseg | TCL 1 | TCL 1 |
| canflash | TCL 1 | TCL 1 |
| mcudb tools | TCL 2 | TCL 2 |
| swc tools | TCL 2 | TCL 2 |
| arm-none-eabi-gcc and binutils | TCL 3 | TCL 2 |
| CMake and Ninja | TCL 2 | TCL 1 |
| zig cc (host tests) | TCL 2 | TCL 1 |
| cppcheck and clang-tidy | TCL 2 | TCL 2 |
| test runner and coverage | TCL 1 | TCL 1 |
| trace | TCL 1 | TCL 1 |
| StrictDoc | TCL 1 | TCL 1 |
| elfcheck | TCL 1 | TCL 1 |
| e2e reference model | TCL 1 | TCL 1 |
| boardtest | TCL 2 | TCL 2 |
<!-- manual-tools:end -->

Route: **detect rather than qualify** for the safety-relevant generated items (checks of the output that do not use
the generator's logic: safe outputs table, reference outputs, RTE timing and priorities in the linked image, memory
layout, DBC layout) and **qualification through validation** for the rest (proposal G, the qualification reports, not
written yet). The compiler route (TN2) is the user's decision and open. Every TI2 use case is classified for ASIL B,
a predetermined ASIL the integrator verifies for his item (AoU-26; 8-11.4.2).

### 9.2 Planning data

Identification, version, configuration, environment, user guide and known malfunctions per tool group:
`tool-classification.md` section 1.4. Missing today there: the malfunction list of neXtBSW's tools (proposal G) and of
the external tools (proposal L); the compiler version is not checked by the build (proposal J).

### 9.3 What neXtBSW supplies for the integrator's code generator (AoU-29)

1. The interface description of the RTE: ports, types, runnables and their calling contract (ARXML, `rte.yaml`).
2. The Embedded Coder settings neXtBSW applies to every component, which become part of his tool configuration and
   which he owns from then on — set by `tools/swc/swc_prepare.m` (and at creation by `tools/swc/swc_create.m`):
   `SolverType` Fixed-step, `Solver` FixedStepDiscrete, `FixedStep` = the base period of the runnables,
   `EnableMultiTasking` on for several rates without export functions, `SystemTargetFile` `autosar.tlc`,
   `GenCodeOnly` on, `GenerateMakefile` off, `GenerateReport` off, `ProdHWDeviceType` ARM Compatible → ARM Cortex-M,
   `ProdLongLongMode` on, `SupportNonFinite` off, `MatFileLogging` off, `GenerateComments` on, `MaxIdLength` 63;
   new components are export-function models (`IsExportFunctionModel` on). The list is read from the scripts at the
   working state of this issue; the scripts are the reference.
3. The statement that **no neXtBSW check verifies the behaviour of the generated model code**: rtegen checks only the
   interface against the ARXML, `demoswc-sil` runs the demo component only. He verifies the generated code himself,
   for example back-to-back of model and object code (6-9.4.5).

---

## 10. Known limitations

### 10.1 Software neXtBSW uses and did not develop for safety

From concept 8.5 and [`component-qualification.md`](component-qualification.md):

| Part | Version | Qualification today | Route |
|---|---|---|---|
| Eclipse OpenBSW, including the copied safety demonstration | `v0.1.0-677-g00e3cab1` | none; the safety code is a demonstration's skeleton (concept 8.1) | unchanged parts: qualification (8-12); the 47 modified copies in ASIL B modules: own development per part 6; first report QR-01 `safeMonitor` |
| FreeRTOS | V10.6.2 | **not qualified** | qualification per 8-12 decided (rtos-u1-anfrage), not started; SAFERTOS and ThreadX named as alternatives, scope unchecked (concept 8.2, `rtos-comparison.md` (not published, available on request)) |
| arm-none-eabi-gcc | 11.3.1 (xPack) | **not qualified**, TCL 3 | qualified toolchain, validation suite, or the TD2 route with proposals I, J, L — user decision TN2 open |
| newlib, ETL, CMSIS headers | as shipped | none | as the compiler; pre-existing components per 8-12 |
| zig cc / clang (host tests only) | 21.1.0 | not applicable to the product | tool question only |

The integrator's side is AoU-20.

### 10.2 Architectural and product limitations

- **Single channel on one core**: supervisor and supervised share core, clock domain, RAM, flash and supply; no
  lockstep, no second MCU voting (concept 2.1, AoU-13; [`dfa.md`](dfa.md)).
- **No partitioning of the application**: the MPU protects the safety manager's data and stack only; SYS-21 is open
  (concept 6.3, AoU-17). Task activation is not supervised (F10).
- **Register lock and check** cover the classes derived by SYS-24; the remaining classes are SSR-070 (partial).
- **Run-time comparisons detect a change, not a wrong design** (DFA-5 of [`dfa.md`](dfa.md)): the MPU descriptor
  check, the register check and the safe outputs table take their expected values from the same generated data that
  configured the hardware; a wrong design value, a generator fault or a wrong derivative fact is configured and
  expected alike. The design side is covered by tool confidence (section 9), generator tests with own profiles and
  mutants, and board tests of the generated result per project (AoU-DFA-c).
- **A PWM timer whose clock gate is switched off** freezes its pins and does not take the walk of the safe outputs
  table (DFA-4): the group is de-energised only through a `safe_off` enable on another peripheral bound to it;
  `nxconfig` warns for a used group without one (SYS-95), and whether the walk opens the gate first is open for
  SYS-30.
- **Read-back that S1 took effect** (G1) closed on the host since SYS-30 (S1) and SYS-138 (S1 held, the groups of SD,
  the S1 + S2 sequence of `ecu/safety`); SSR-060 partial: the hard fault and watchdog handlers and the halt deadman
  of the family port, board proof B15/B16 open; no plausibility between two sources (G2), no age supervision of device values
  (G3), no safe state for bus-commanded actuators (G4) — SSR-061 … SSR-063 open.
- **Family port for s32k1xx only** (section 1.4); SM-55 and the hardware threshold are not described for every
  derivative (fmea SM-55, hardware-mechanisms 2.4: S32K116).
- **The bootloader's own integrity** is not checked by any stage-1 mechanism (fmea N4).
- **The bootloader phase runs without MCU watchdog** (section 4, HSI 4.13): safe only with all outputs in their reset
  state while it runs (AoU-07, AoU-SEC-3); a hang there is ended by a reset from outside, not by neXtBSW.
- **A lost SIRC** (the clock of the halt deadman's LPIT) is a residual risk (section 5, faults of the time bases;
  `dfa.md` DF-05): the halt deadman stops with it, nothing walks the table, and only the hardware reset of the MCU
  watchdog de-energises the outputs, after at most about 283 ms on the eval board; the cause of the hang is open,
  investigation and architecture with SYS-195 after release R1. A **stopped system time** is not seen by the period
  check (kind 4/5 see only a slow or fast one); the halt deadman SM-06 and the watchdog SM-01 react (section 5).

### 10.3 Evidence limitations

- **Open board proofs** named by the sources: of `fmea.md` 8, B8 and B10 ran (`fmea.md` 8.2; safety case EB-11,
  EB-09), B9 and B11 have records not yet linked on one image and B5 has none (safety case D-15); **B16 (a), (c),
  (d) and the reset at falling supply (LVD, SM-59)** are a known gap of Release R1, recorded after R1 with SYS-198
  (safety case D-15); the degraded reaction SD (no board run,
  fmea SM-23), the gate-driver binding of IO-45, the hardware threshold of SYS-86 (host verified, board proof not part of release
  R1), the SBC watchdog `serve` with
  SDMS 0 (mock only, BL-28).
- **Coverage thresholds** of the safety modules are proposed, not all met (concept 9.2; component-qualification:
  modified copies 94.5 % statements, 55.0 % branches, 9 of 23 files without a host test).
- **Host tests use a different compiler** from the target; the host/target difference analysis is missing
  (tool-classification 3.14, proposal I).
- **Tool qualification reports** (proposal G) and the qualification of pre-existing software are not written for a
  release (safety plan R4).

---

## 11. Cybersecurity interface

neXtBSW has no cybersecurity management and makes **no cybersecurity claim** ([`security-interplay.md`](security-interplay.md)
section 0). The safety claim covers random hardware and systematic faults; each security gap listed there lets an
attacker **bypass** a mechanism, so the claim stands only together with the integrator's cybersecurity measures.
The field interfaces IF-01 … IF-10, the protection that exists and the rules of the interplay are in
`security-interplay.md` sections 2 and 3. The assumptions below keep their identifiers as final numbers.

<!-- manual-aou-sec:start -->
| ID | The integrator's obligation | Source |
|---|---|---|
| AoU-SEC-1 | Treat every write of program or calibration data as a security-relevant function of his item and protect it with his own measures; neXtBSW verifies integrity and compatibility of an image (SM-51), not its origin | [security-interplay 4](security-interplay.md) |
| AoU-SEC-2 | Keep the master key of the per-device keys and the provisioning step under his key management; a device key is known once the debug port of that device was open | [security-interplay 4](security-interplay.md) |
| AoU-SEC-3 | Programming session and ECU reset switch the safety function off for their duration: implement the vehicle-state precondition in the application hook, confirm that the pin reset state of every power output is safe while the bootloader runs | [security-interplay 4](security-interplay.md) |
| AoU-SEC-4 | Protect every DID and routine that writes a safety-relevant value or drives an output with a security level and an application interlock; decide on preconditions for clearing and suppressing DTCs | [security-interplay 4](security-interplay.md) |
| AoU-SEC-5 | XCP off in the product, or add resource protection for calibration; the plausibility check limits, it does not authenticate | [security-interplay 4](security-interplay.md) |
| AoU-SEC-6 | Build the product image without the test and fault-injection console commands (`-DNX_PRODUCT_BUILD=ON`); UART not accessible in the vehicle | [security-interplay 4](security-interplay.md) |
| AoU-SEC-7 | Close the debug port in the product (flash security or physically inaccessible); while open, none of the protections of IF-01 … IF-06 holds | [security-interplay 4](security-interplay.md) |
| AoU-SEC-8 | Protect data a safety function reads from the bus or NvM against deliberate manipulation where his threat analysis requires it; E2E protects against channel faults only | [security-interplay 4](security-interplay.md) |
<!-- manual-aou-sec:end -->

---

## 12. Tailoring and process

**Tailoring** of the lifecycle for the SEooC (safety plan section 3; 2-6.4.5.7). Where an activity moves to the
integrator, the assumption named in the last column makes the hand-over explicit.

<!-- manual-tailoring:start -->
| ID | Tailoring | The integrator's side |
|---|---|---|
| T-01 | item definition, hazard analysis, functional and technical safety concept not performed; replaced by ASG and AoU | AoU-01, AoU-02; section 3.2 |
| T-02 | hardware development, hardware metrics, random hardware failures, production not performed | AoU-09; section 5 |
| T-03 | no quality management system, no evidence of competence | AoU-35 |
| T-04 | no impact analysis at item level | AoU-02; the change history per release (`CHANGELOG.md`) |
| T-05 | no development interface agreement | negotiated on the basis of this manual |
| T-06 | no evaluation of hardware elements | AoU-36; he evaluates MCU, SBC and board |
| T-07 | proven in use not used | pre-existing software per 8-12 or part 6 (section 10.1) |
| T-08 | no ASIL decomposition at stage 1 | he does not decompose into neXtBSW elements without his own analysis |
| T-09 | functional safety assessment deferred | AoU-35 |
| T-10 | embedded tests on the reference eval board and the host; configuration range argued for the project's configurations | AoU-26, AoU-CFG-1 … AoU-CFG-7 |
| T-11 | **Deviation, not a tailoring:** confirmation measures performed for now by the user himself, without the independence I1 (decision 2026-09-30, AN-093); **lifted when** the safety manager names an independent person (iso-q1), and from then on every measure performed under it is reviewed again at I1 (safety plan issue 2, T-11) | AoU-35: no independent confirmation; a working state carries no ASIL statement until an independent review; for release R1 also the deviation of CM-03, CM-08, CM-09 and CM-11 (safety plan section 5, safety case D-22) |
<!-- manual-tailoring:end -->

**Confirmation measures** (safety plan section 5): 11 measures CM-01 … CM-11 planned at independence I1; the
procedure, checklists and packages exist (`reviews/README.md` (not published, available on request)). *Since 2026-09-30 (decision of
the safety responsible, AN-093, tailoring T-11):* the user performs them himself for now (task SYS-118). That is **not
independent**: a record of such a review is a confirmation without independence, and the working states it looks at
keep **no ASIL statement** until an independent person has reviewed them. This manual is the work product of CM-10.
**Deviation for release R1** (decision of the safety responsible 2026-10-03, [`safety-plan.md`](safety-plan.md)
section 5, deficit D-22 of [`safety-case.md`](safety-case.md)): CM-10, CM-05, CM-06, CM-04, CM-07 and CM-02 are
performed before release R1, CM-03, CM-08, CM-09 and CM-11 after it; the deviation is lifted before the first release
with an ASIL B claim (restated 2026-10-08, safety plan issue 3, released in the delta re-check of CM-01 the same
day). Until then every release, private or public, is a development release without a safety claim, and every
recipient receives that statement together with this manual; it stands in the `README.md` of the repository and in
every release note (safety plan section 5, C2).

**Release** (safety plan section 6): a safety release is an annotated tag `vX.Y.Z` on `master` with a green archived
CI run, safety documents citing the tag, qualification reports, closed confirmation reviews, and safety case and
safety manual issued for it (preconditions R1 … R6, the same list as P1 … P9 of [`cm-plan.md`](cm-plan.md)
section 5.1; mapping in safety plan section 6). **No tag meets R1 … R6 today**; every tag, `v0.1.0` and `v0.2.0`
alike, is a development release without a safety claim.

---

## 13. Work products

The work products an integrator may request, with their state (status words of the document register,
[`anomaly-register.md`](anomaly-register.md) section 6, read at the working state of this manual). None belongs to a
safety release yet; each carries the working state it was checked against in its header (`cm-plan.md` section 6).
"Accepted" names the issue or state the user accepted; several documents were changed after that by later tasks
(register section 6, "Accepted, then changed"), and a later change waits for acceptance again. An integrator
requests them **at a tag** and checks that the tag meets R1 … R6.

**Issue and tag.** The state of a document is identified by its issue together with its state line: issue 1 of this
manual was accepted on 2026-09-30 at a working state after `v0.1.0` (`dev` at 46d7f04); `v0.2.0` (REL-020) set its
state line to `Baseline: v0.2.0`; the changes since then are a working state after `v0.2.0` (section 16). CM-10
decided (Z3, record CR-10-1): **issue 2** is the state after the corrections of the CM-10 findings and goes with the
next tag `v1.0.0-rc1` (release R1); the release step sets the state line to `Baseline: v1.0.0-rc1`.

| Work product | Content | Status |
|---|---|---|
| [`safety-manual.md`](safety-manual.md) | this manual | issue 2 accepted: CM-10 performed 2026-10-04 (CR-10-1, first verdict accepted with conditions, D-01), issue 2 released in the re-check of F-01 and F-03 the same day; the updates F-04 and F-05 closed and accepted as updates of issue 2 in re-check 2 (2026-10-04); F-02 closed and the update DOC-50 accepted in re-check 3 (2026-10-08), CM-10 closed (verdict accepted); issue 1 accepted 2026-09-30 |
| [`concept.md`](concept.md) | safety concept: element, ASG, safe states, AoU, mechanisms, timing, demarcation, gaps | accepted: issue 3 with its catalogue (`goals.sdoc`, `assumptions.sdoc`, `mechanisms.sdoc`) released 2026-10-04 in the re-check of CM-07 (CR-07-1, verdict accepted, D-01); issue 2 released 2026-09-30 |
| [`fmea.md`](fmea.md) | failure analysis of the stage-1 mechanisms, coverage classes, times, board results | accepted: issue 2 (DOC-55, after CM-02) released 2026-10-04 in the re-check of CM-02 (CR-02-1, verdict accepted, D-01); its update of section 1.3 (SYS-209) accepted 2026-10-08 (C8); issue 1 released 2026-09-30 |
| [`ssr.sdoc`](../requirements/safety/ssr.sdoc), `traceability.md` (not published, available on request) | software safety requirements, generated trace matrix | accepted (release 2026-10-02; DOC-44 clauses and INS-SSR-02 confirmed in the re-check of CM-07, 2026-10-04) / generated |
| [`architecture-safety-analysis.md`](architecture-safety-analysis.md) | safety analysis at architecture level | accepted: issue 2 (DOC-55, after CM-02) released 2026-10-04 in the re-check of CM-02 (CR-02-1, verdict accepted, D-01); issue 1 accepted (release 2026-10-02) |
| [`dfa.md`](dfa.md) | dependent failure analysis | accepted (release 2026-10-02) |
| [`coexistence.md`](coexistence.md) | coexistence of ASIL B and QM software | accepted (release 2026-10-02) |
| [`sw-architecture.md`](sw-architecture.md), [`unit-design.md`](unit-design.md) | software architecture and unit design of the safety part | accepted (release 2026-10-02) |
| [`hsi-s32k1xx.md`](hsi-s32k1xx.md) | hardware-software interface s32k1xx | accepted (released 2026-09-30) |
| [`hardware-mechanisms.md`](hardware-mechanisms.md) | hardware safety mechanisms (catalogue) | accepted (release 2026-10-02) |
| [`partner-and-degraded.md`](partner-and-degraded.md) | partner supervision, degraded reaction, active short (concept) | accepted (release 2026-10-02) |
| [`config-range.md`](config-range.md) | configuration range argument | accepted (release 2026-10-02) |
| [`tool-classification.md`](tool-classification.md) | tool classification and safeguards | accepted (released 2026-09-30) |
| [`static-analysis.md`](static-analysis.md) | static analysis and coding guidelines | accepted (released 2026-09-30) |
| [`component-qualification.md`](component-qualification.md), `rtos-comparison.md` (not published, available on request) | pre-existing components and RTOS comparison | draft / accepted |
| [`security-interplay.md`](security-interplay.md) | interplay of safety and cybersecurity | accepted (release 2026-10-02) |
| `iso26262-gap.md` (not published, available on request) | gap analysis against ISO 26262:2018 | accepted (release 2026-10-02); section 3 restated by DOC-50 and SYS-209 (C7), checked in re-check 3 of CR-10-1 (2026-10-08, D-01) |
| [`safety-plan.md`](safety-plan.md), [`cm-plan.md`](cm-plan.md), [`dev-environment.md`](dev-environment.md) | safety plan, configuration management plan, development environment | accepted (safety plan issue 3, released 2026-10-08 in the delta re-check of CM-01, CR-01-1; issue 2 after CR-01-1, 2026-10-02) / accepted (issue 2, release 2026-10-02) / draft |
| [`safety-case.md`](safety-case.md) | safety case | issue 2 accepted (CM-05 closed 2026-10-04 after the re-check of CR-05-1, D-01); issue 1 accepted (release 2026-10-02) |
| [`anomaly-register.md`](anomaly-register.md) | safety anomalies and document register | accepted (release 2026-10-02) |
| [`board-test-spec.md`](board-test-spec.md) | board test specification per SSR with operational use cases | draft |
| `reviews/README.md` (not published, available on request) | confirmation review procedure, checklists, packages, record template | draft |
| `reviews/records/CR-01-1.md` (not published, available on request) | record of the confirmation review CM-01 (safety plan), 2026-10-02, without independence (T-11) | record |
| `reviews/records/CR-10-1.md` (not published, available on request) | record of the confirmation review CM-10 (this manual), 2026-10-04, without independence (T-11) | record |
| [`verification-plan.md`](verification-plan.md) | verification plan and archived reports per baseline (SYS-67) | accepted: issue 2 (DOC-54, after CM-04) released 2026-10-04 in the re-check of CM-04 (CR-04-1, verdict accepted, D-01) |

---

## 14. Checklist of the safety manual content (11-5.1.11 by analogy)

The gap analysis (section 4, point 1) lists what the safety manual of an element out of context typically contains,
for the MCU case of 11-5.1.11, applicable by analogy. Where each item is in this manual:

| Item (own words) | Section |
|---|---|
| assumed requirements the element fulfils, with ASIL | 3.2 |
| assumptions on the design outside the element | 2, 7 |
| safe states | 4 |
| maximum fault handling times | 5, 6 |
| configuration of the element for safe use | 8 |
| applicable work products | 13 |
| tailoring of the lifecycle | 12 |
| confirmation measures and their independence | 12 |
| safety mechanisms and their coverage as input for the hardware metrics | 5 |
| known limitations, including non-qualified parts | 10 |

---

## 15. Open points and stale places in the sources

Nothing below was decided here; each is a point for the review of this manual (CM-10) or for the owner of the source.
Points 1, 8, 11 and 12 were decided in CM-10 on 2026-10-04 (record CR-10-1, the user under D-01).

| # | Point | Source |
|---|---|---|
| 1 | *Decided in CM-10 (Z1, CR-10-1):* section 3.2 (assumed technical safety requirements ATSR-1 … ATSR-6) is accepted as written, level and cut included. Remark for after release R1: `tools/trace` does not see the ATSR (they are no nodes of the `.sdoc` files) | gap C2, safety plan T-01 |
| 2 | *Done by DOC-38 (review DOC-19, D2):* AoU-34, AoU-35, AoU-36 are in `concept.md` section 4 (4.3, 4.1) and checked by `tools/trace` | BL-28, gap C8 |
| 3 | *Decided in review DOC-19 (D2, D3):* AoU-24, AoU-25 and AoU-CFG-1 … AoU-CFG-6 stay *proposed* in their sources and are binding with the acceptance of this manual (2026-09-30); AoU-26 … AoU-29 are in `concept.md` section 4.5 since DOC-38 and checked by `tools/trace`. *Decided in CM-07 (ZG, CR-07-1):* AoU-24 and AoU-25 are nodes of concept 4.3 and 4.2 with the status `concept` since concept issue 3 (DOC-51), AoU-40 as well (4.4); this manual reads them `concept` | fmea 6, tool-classification 6, config-range 5 |
| 4 | *Corrected by DOC-38 (review DOC-19, D6):* AoU-08 in the concept now says that period and time-out are project values since SYS-25 and the service interval is derived, as concept 7.1 and config-range CV-01/CV-02 | concept 4.2 |
| 5 | *Corrected by DOC-38 (review DOC-19, D6):* AoU-16 in the concept names the halt deadman SM-06 | concept 4.3 |
| 6 | *Corrected by DOC-38 (review DOC-19, D6):* AoU-19 in the concept says that the error memory exists (NVM-03), that only SD keeps the ECU running and reaches it through a DTC (`safety.device_fault`, NVM-08), and that the cause of a resetting event is stored after the next start only with SYS-113 | concept 4.4 |
| 7 | The clause reference and rating (section 5) exist for the mechanisms of gap section 3 only; the rest waits for proposal M2 in `fmea.md` | gap 3, M2 |
| 8 | *Decided in CM-10 (Z2, CR-10-1 F-02):* the typical classes in the re-based column (taken from the gap analysis, which read them from the standard) stay for the privately distributed release R1; before the first public release they are replaced by a reference to the clause and neXtBSW's own rating (task DOC-50). *Done:* implemented by DOC-50 (2026-10-08) - section 5 and gap section 3 carry per mechanism the clause and neXtBSW's rating E1 … E3 instead of the typical classes; F-02 closed in re-check 3 of CM-10 (record CR-10-1, 2026-10-08, C4 … C6) | gap 3 |
| 9 | FreeRTOS qualification not started; compiler route TN2 undecided; qualification reports (proposal G) missing — preconditions R4 of every safety release | safety plan R4, tool-classification TN2 |
| 10 | *Decided 2026-09-30 (AN-093, safety plan T-11):* CM-10 is performed by the user himself for now, without independence; an independent review of this manual stays open | iso-q1, AN-093 |
| 11 | *Decided in CM-10 (Z3, CR-10-1):* the changes after the acceptance of issue 1 (section 16, from SYS-116 on; AoU rows marked *added after acceptance*) were reviewed and make **issue 2** = the state after the corrections of F-01 and F-03, released with their re-check; the state line becomes `Baseline: v1.0.0-rc1` in the release step | section 16 |
| 12 | *Decided in CM-10 (Z4, CR-10-1):* AoU-37, AoU-38 and AoU-39 are binding (`concept`), as their STATUS in `assumptions.sdoc` says; this manual reads them `concept`. The words "open for acceptance" in their text are removed on the side of the source (DOC-48, CM-07) | assumptions.sdoc |

---

## 16. Change history

| Issue | Date | Task | Change |
|---|---|---|---|
| 1 | 2026-09-30 | DOC-19 | first issue from concept (issue 2 with updates up to SYS-123 and SYS-86), fmea, requirements, tool classification, config range, security interplay, hardware mechanisms, partner-and-degraded, fast control loop, safety plan and gap analysis at `integ/night5` 46d7f04 |
| 1 (accepted) | 2026-09-30 | DOC-38 | issue 1 accepted by the user in the safety review of DOC-19 (`review-doc19`: D1 … D6, D8, D9; D7 re-measure the times on the board); AoU-26 … AoU-29 and AoU-34 … AoU-36 read `concept` (carried into concept section 4); section 15 points 2 … 6 and 10 answered; tailoring T-11 (confirmation measures by the user, AN-093) in section 12 |
| 1 (update) | 2026-09-30 | SYS-116 (`81ad254c`) | SM-26 (selectable safe state of a PWM group) and SM-27 (start-up test of the active short) added as not credited; ATSR-1 names the selection |
| 1 (update) | 2026-09-30 | SYS-30 (`3adf9f2b`) | read-back of S1 (gap G1 closed for S1, SSR-060 partial) in section 3.2, SM-23 and 10.2 |
| 1 (update) | 2026-09-30 | SYS-135 (`9bfd7826`) | AoU-37 (hardware threshold input away from VDDA at start), count 49 |
| 1 (update) | 2026-09-30 | SYS-143 (`12edc358`) | AoU-38 (conditions of the start test of the active short), count 50 |
| 1 (update) | 2026-10-01 | SYS-148, SYS-149 (`a2899db8`, merge `dc7a62ca`) | section 4: restart times re-measured, 1 077 ms (watchdog path) and 858 ms (MPU path), with the breakdown of that time |
| 1 (update) | 2026-10-01 | SYS-125 (`92145912`, merge `f4a6b62a`) | SM-59 (hardware LVD reset) as not credited, the supply AoU (AoU-39 since SAFETYDOC-1001), rule 13 of section 8 |
| 1 (update) | 2026-10-01 | SAFETYDOC-1001 (`aab5b17d`) | consistency after the night merges: ATSR-1 |
| 1 (update) | 2026-10-01 | REL-020 (`3acefb82`) | state line set to `Baseline: v0.2.0` for the release candidate v0.2.0 (cm-plan 5.2 step 1); the content was not re-checked |
| 1 (update) | 2026-10-01 | DOC-21 (`9b918153`) | links from `requirements.yaml` to `ssr.sdoc` (SSR, goals, AoU and SM as StrictDoc nodes) |
| 1 (update) | 2026-10-01 | NVM-25, SYS-153 (`89ed5357`, FIXUP-1001B) | SM-60 (lock of the FlexNVM block) and AoU-40 added as proposed, not credited; SM-59: cyclic re-check by SM-22 since SYS-153 |
| 1 (update) | 2026-10-01 | SYS-67 (`318fcef0`) | section 13: verification plan as a work product |
| 1 (update) | 2026-10-02 | SAFETYDOC-1002 (`3dc79178`) | consistency after PLAT-79 and SYS-105: ATSR-3, SSR-082 no longer named as open |
| 1 (update) | 2026-10-02 | BL-36 (`f1a56bfa`) | section 4 and 10.2: the bootloader phase runs without MCU watchdog and under which conditions that is safe (SYS69-S11, BLI-F03); SM-51: image check again right before the jump, no start of a record without compatibility block (SYS69-S10, BLI-F01, BLI-F06) |
| 1 (update) | 2026-10-02 | REVIEW-1002T (`0e8e49a0`) | correction after the safety review 2026-10-02, S171-4, release unchanged: SM-13 names descriptors 1 … 7 in the protected RAM instead of "expected values in flash"; AoU-41 (no bus master other than the core until SYS-21 restricts descriptor 0, S171-1) and AoU-42 (all tasks run privileged, SYS-172 P1) added, status concept, count 54 |
| 1 (update) | 2026-10-02 | BL-38 (`b764e7b7`) | section 4 and SM-51: the check right before the jump compares the record with the one image CRC of the start instead of computing a second one (restart times of 0.3.7 about 490 ms above section 4); section 4 restart times marked to be re-measured with the first bootloader image that carries BL-38 - that is 0.3.9 (`0e53fdc6`), measured with `880e70e7` |
| 1 (update) | 2026-10-02 | BL-38, BL-39 (`0e53fdc6`) | section 4: the re-measurement named for bootloader 0.3.9, the first image with BL-38 (together with BL-39) |
| 1 (update) | 2026-10-03 | board 2026-10-02/03 (`880e70e7`) | section 4: restart times with bootloader 0.3.9 (safety wdg 883/893 ms, safety mpu 700 ms) within the stated values |
| 1 (update) | 2026-10-03 | DOC-45 (`c6359ff7`) | safety review BL38-P1: AoU-CFG-7 (SM-14 active with the neXtBSW bootloader, F5), count 55; section 4 breakdown marked as the state before bootloader 0.3.6 (F3), +36 ms of the MPU path marked as not explained (F1) |
| 1 (update) | 2026-10-03 | SYS-95 (`b104b5e1`) | dependent failure analysis carried in: AoU-DFA-a … AoU-DFA-c as proposed (count 58 with AoU-CFG-7 of DOC-45), claim limit DFA-5 and the clock gate of a PWM timer (DFA-4) in 10.2, the progress checks of DFA-1 and DFA-2 and the converter check of DFA-3 in the latent-fault column of SM-14, SM-06 and SM-18 |
| 1 (update) | 2026-10-04 | SYS-194, SYS-196 (carried in by DOC-47) | SM-34: take-backs of foreign `safe_off` levels reported as `DEVICE_REPORT` (SYS-194) and recorded as DTC `safety.safe_off_take_back` 0x1605FA (SYS-196) |
| 1 (update) | 2026-10-04 | DOC-47 | author-side clean-up before CM-10, issue and acceptance unchanged: state line `Working state`; status line and D7 marked; history from SYS-116 on completed; section 4 restart time with bootloader 0.3.9 and the oscilloscope breakdown of 2026-10-03 (BL-33, BL38-P1 F1; replaces "not explained" and the breakdown before 0.3.6); AoU-ASA-a and AoU-ASA-d added, AoU-ASA-b/c as covered (7.7), SM-32 phy half only with a status-reporting transceiver (ASA-10), count 60; AoU-37 … AoU-39 read `proposed` as their text says; rows added after the acceptance marked; section 8 rules 14 … 17; SM-58 and SYS-86 as not part of release R1; T-11 as deviation with the condition that lifts it and the deviation for release R1; "development release"; section 13 against the document register; section 15 points 1, 8, 11, 12 for CM-10 |
| 2 | 2026-10-04 | DOC-49 | corrections after the confirmation review CM-10 (record `reviews/records/CR-10-1.md` (not published, available on request), the user under D-01, baseline `dev` at 3297a920, verdict `accepted with conditions`; decision Z3: issue 2): SM-26 and SM-27 brought to the state of the concept - `nxconfig` accepts the active short since SYS-139/SYS-140, the start test is called from `RteSystem::init()` since SYS-141/SYS-143 (F-01); AoU-33 in force for projects with an active short, a row of section 7.3 instead of 7.6, and AoU-ASA-b a row of its own with security access for the ECU reset (UDS 11) (F-03, Z5), count 62; AoU-37 … AoU-39 `concept` (Z4); section 15 points 1, 8, 11, 12 decided; section 8 rule 18; header issue 2 in review, state line; SYS-95 board results (safety review SYS-95 board 2026-10-04, all a): section 5 faults of the time bases (stopped system time not kind 4: SM-06 after about 10 ms, SM-01 after about 160 … 283 ms; gated LPIT clock: bus fault, SM-17; slow LPIT clock: DFA-2 on the board; lost SIRC: residual risk, SYS-195), SM-04 and SM-06 latent-fault column, section 10.2 |
| 2 (update) | 2026-10-04 | DOC-51 | after the confirmation review of the concept CM-07 (record `reviews/records/CR-07-1.md` (not published, available on request), the user under D-01, `accepted with conditions`; concept issue 3): ATSR-5 and the SD row of section 4 name the second degradable case, the trip of a hardware shut-off path (`HW_SHUTOFF`, SYS-135; decision ZA, F-01); AoU-04, AoU-05 (FTTI and FHTI, C7) and AoU-32 restated as the concept; AoU-24, AoU-25, AoU-33 and AoU-40 read `concept` (ZF, ZG; AoU-33 from `assumptions.sdoc` with its points (a) … (d)); section 7 note, 7.6 and 15 point 3; section 0 count line; the times of the table of the time bases in section 5 marked as **derived** with their derivation (re-check of CM-10, point N4); state line. No count changed (62 rows, 30 credited mechanisms; SM-26/SM-27 stay not credited, now also in the catalogue, ZD); SSR-062 `partial` (ZE) allocated to ATSR-4, its clause (b) open with SYS-99 |
| 2 (update) | 2026-10-04 | DOC-53 | finding F-04 of the re-check of CM-10 (N5; AN-143): gap G1 (read-back of S1) stated as closed on the host by SYS-30 and SYS-138 with its open rest (hard fault and watchdog handlers and halt deadman of the family port, board proof B15/B16) in section 3 (ASG-01, ASG-02, the note of section 3.2), the SM-23 row of section 5 and section 10; the compiler named as the xPack arm-none-eabi-gcc 11.3.1 in sections 0 and 9 (tool classification, SYS-88/PLAT-88); AoU-19 (section 7) restated after SYS-113 (the cause of a resetting event is a DTC at the next start; what stores none); `HW_SHUTOFF` (section 4, ATSR-5) and the "derived" times of section 5 checked, already corrected by DOC-51; no count changed |
| 2 (update) | 2026-10-04 | DOC-54 | after the confirmation review of the verification plan CM-04 (record `reviews/records/CR-04-1.md` (not published, available on request), finding F-06): section 1.3 names the verification of the integration and of the item (integration test of the ECU, test in the vehicle, `tools.ci run --workspace`) as the integrator's, with AoU-10, AoU-CFG-1 and AoU-CFG-2; no count changed |
| 2 (update) | 2026-10-04 | DOC-58 | finding RN of the re-check of CM-07 (record `reviews/records/CR-07-1.md` (not published, available on request); registered as AN-175, CR-10-1 F-05): the column "Coverage at stage 1" of section 3.1 restates `goals.sdoc` issue 3 (ASG-01, ASG-02 with the residual risk DF-05, ASG-06, ASG-07; CM-07 ZC); SM-34 credited as a reaction as in `mechanisms.sdoc` and `fmea.md` issue 2 - section 0 count **31** (was 30 plus SM-34 "report path only"), SM-26/SM-27 named among the not credited; header status and approver after the re-check of CR-10-1 (issue 2 accepted); section 13 statuses as the document register (concept issue 3 accepted; `fmea.md` and the architecture safety analysis issue 2 in review); state line `dev at c8bf73bc`. GCC 11.3.1 was already corrected by DOC-53 (sections 0 and 9) |
| 2 (update) | 2026-10-04 | DOC-59 | re-check of CM-02 (record `reviews/records/CR-02-1.md` (not published, available on request), decision Z7I b, 2026-10-04): AoU-ASA-a (section 7.3), AoU-ASA-b and AoU-ASA-d (section 7.4) read `concept` and are binding, because the accepted residual risks AN-172, AN-170 and AN-165 rest on them (section 7 note, section 0 count line; count unchanged, 62); section 13: `fmea.md`, the architecture safety analysis and the verification plan issue 2 accepted after the re-checks of CM-02 and CM-04; state line `dev at 6812a0b4` |
| 2 (update) | 2026-10-04 | DOC-79 | re-check 2 of CM-10 (record `reviews/records/CR-10-1.md` (not published, available on request), the user under D-01, `dev` 2c065567): F-04 (AN-143) and F-05 (AN-175) closed with a remark, the updates DOC-51 … DOC-59 accepted as updates of issue 2 (N10); header approver and status, section 13 row of this manual; CM-10 stays `accepted with conditions` only because of F-02 (DOC-50); state line `dev at 2c065567`; section 13 row of the safety case: issue 2 in review after CM-05 (record CR-05-1); no content change |
| 2 (update) | 2026-10-04 | DOC-80 | editorial update after the re-check of CM-05 (record `reviews/records/CR-05-1.md` (not published, available on request), finding RN (1), the user under D-01): section 10.3 "Open board proofs" at the state of the safety case issue 2 - B8 and B10 ran (`fmea.md` 8.2; safety case EB-11, EB-09), B9 and B11 have records not yet linked on one image, B5 none (D-15), B16 (a), (c), (d) and the LVD reset (SM-59) a known gap of Release R1 with SYS-198; no claim, assumption, mechanism, count or rule changed; issue and acceptance unchanged |
| 2 (update) | 2026-10-04 | DOC-80 | editorial update requested by the release session for CM-06: section 13, row of the safety case, at the state after the re-check of CM-05 (record `reviews/records/CR-05-1.md` (not published, available on request)) - issue 2 accepted; no claim, assumption, mechanism, count or rule changed; issue and acceptance unchanged |
| 2 (update) | 2026-10-05 | DOC-84 | state line after the tag `v1.0.0-rc1` (Release R1, CM-06 record `reviews/records/CR-06-1.md`, condition (4)): `Working state: dev at c50e240f (after v1.0.0-rc1)`; the manual is unchanged since `v1.0.0-rc1`, development release, P4 not required (K06-03); no claim, assumption, mechanism, count or rule changed; issue and acceptance unchanged |
| 2 (update) | 2026-10-07 | BL-41 | decision SDMS0-1 c of the safety review SBC watchdog (2026-10-06): the bootloader (0.3.10) serves the UJA113x watchdog whenever the SBC is not in software development mode (SDMS 0), also with `bootloader.sbc_watchdog: leave`; `leave` takes effect only with SDMS 1. AoU-34 restated (the "programmable only with a debugger" case of `leave` with SDMS 0 is gone) and extended by the start-up time to the bootloader's watchdog setting (within the first period of 128 ms after an SBC reset) - **proposed by the author, to be confirmed by the safety responsible**; section 4 (who resets a hung bootloader) adapted; no count changed |
| 2 (update) | 2026-10-07 | BL-42 | decision BL41-2 b of the safety review bootloader 0.3.10 (2026-10-07): the bootloader (0.3.11) serves the UJA113x watchdog with SDMS 0 also when the SBC is only read (not in `bootloader.init`) - only the watchdog is set up, the SBC stays in Standby, V2 and CAN are not written. AoU-34 and section 4 extended accordingly; no count changed |
| 2 (update) | 2026-10-08 | SYS-207 | section 12 (confirmation measures) after safety plan issue 3 (decision of the safety responsible 2026-10-08, E1 b; pending the re-check of CM-01): the deviation for release R1 is lifted before the first release with an ASIL B claim instead of "before the first public release or …, whichever comes first"; until then every release, private or public, is a development release without a safety claim and every recipient receives the statement with this manual; section 13 names safety plan issue 3 in review. Point 8 of section 15 and the status line (F-02, DOC-50) not changed; no claim, assumption, mechanism, count or rule changed |
| 2 (update) | 2026-10-08 | DOC-50 | finding F-02 of CM-10 (record `reviews/records/CR-10-1.md` (not published, available on request), decision Z2, AN-134; safety escalation 2026-10-08, E2): section 5, column "Re-based" renamed "Clause; neXtBSW rating" - the typical classes taken from the standard removed; per mechanism the clause (part and clause only) and neXtBSW's own rating E1 … E3 with what the mechanism sees and its evidence (scale and its reason in the reading notes of section 5); closing paragraph of section 5, section 0 mechanisms row, section 15 points 7 and 8; header issue and status; state line `dev at 9bf2d520`. No claim, assumption, mechanism, class, time, count or rule changed. **Re-check CM-10 F-02 pending** (the user); until then this update is in review and F-02 stays open |
| 2 (update) | 2026-10-08 | SYS-209 | re-checks of the user of 2026-10-08 (D-01, without independence): re-check 3 of CM-10 (record `reviews/records/CR-10-1.md` (not published, available on request), C4 … C6) closed F-02 and accepted the update DOC-50, verdict `accepted`, CM-10 closed - header issue, approver and status, section 13 row of this manual, section 15 point 8; C7: the class of SM-21 and SM-22 in section 5 is `medium` as in `fmea.md` issue 2 (was `low`); the delta re-check of CM-01 (record CR-01-1, C1 … C3) released safety plan issue 3 - section 12 without "pending" and with the places of the statement (README, release notes; C2), section 13 rows of the safety plan, `fmea.md` (update of 1.3, C8) and the gap analysis; state line `dev at c0928380`. No claim, assumption, mechanism, time, count or rule changed |

---

## 17. Review questions of the architecture (section 11)

This task changes no code, generator, configuration or bootloader; it adds this document and its consistency test.
**Board without an SBC:** the stage-1 argument is the same without an SBC (SM-33, SM-42, SM-52 not credited);
AoU-13 and AoU-34 name what an SBC changes. **Different crystal:** every time is stated as a time; the one computed
walk duration names its 80 MHz. **Other derivative, S32K3/STM32/Cortex-R:** section 1.4 states that the family port
exists for s32k1xx only. **Several CAN buses, several tasks:** SM-31/SM-32 per bus, task set as AoU-08 and CV-18.
**Exactly one level per value:** no new value; section 8 points to the register. **Host test:**
`test/test_safety_manual.py`. **Qt, YAML paths, driver granularity, platform rule:** not applicable.

## 18. Sources

Issue 1 from inside the repository at `integ/night5` 46d7f04 (the updates after it name their sources in section
16 and in place): [`concept.md`](concept.md) (sections 0 … 13 and the update
blocks up to SYS-86), [`fmea.md`](fmea.md) (sections 1 … 9), [`ssr.sdoc`](../requirements/safety/ssr.sdoc),
[`tool-classification.md`](tool-classification.md) (sections 0, 1.4, 3, 5, 6), [`config-range.md`](config-range.md),
[`hardware-mechanisms.md`](hardware-mechanisms.md), [`partner-and-degraded.md`](partner-and-degraded.md) (sections 4,
7.1), `../architecture/fast-control-loop.md` (not published, available on request) (sections 6, 8),
[`security-interplay.md`](security-interplay.md) (sections 0, 4), [`safety-plan.md`](safety-plan.md) (sections 1, 3,
5, 6), `iso26262-gap.md` (not published, available on request) (sections 3, 4, 5, 6),
[`component-qualification.md`](component-qualification.md) (section 0), [`anomaly-register.md`](anomaly-register.md)
(section 6), `CHANGELOG.md` (BL-28), `doc/reference/project.md` (`bootloader.sbc_watchdog`),
`tools/swc/swc_prepare.m` and `tools/swc/swc_create.m` (Embedded Coder settings). ISO 26262:2018 is cited by part
and clause only, through the gap analysis.
