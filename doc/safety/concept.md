# neXtBSW safety concept – a Safety Element out of Context for ASIL B

Working state: dev at aaa22c86 (after v0.2.0)

Status: **issue 3 (DOC-51) of 2026-10-04, released** — the state after the corrections of the confirmation review
**CM-07** (record `reviews/records/CR-07-1.md` (not published, available on request), the user under deviation D-01, without
independence, baseline `dev` at `5cc50658`, first verdict `accepted with conditions`, decision ZI: issue 3). It was
released on 2026-10-04 with the re-check of the findings F-01 … F-07 in that record (procedure steps 6 and 7, the user
under D-01 at `dev` 898dbcd1: all findings closed, verdict `accepted`, CM-07 closed; DOC-57); the release step of
`v1.0.0-rc1` then sets the state line to `Baseline: v1.0.0-rc1`. Issue 3 takes in everything that was changed after
the release of issue 2 (2026-09-30, `c44f94ad`) — the updates SYS-116 … SYS-194 and the formal corrections of
DOC-48, which CM-07 reviewed — and the corrections of that review; its catalogue is
[`goals.sdoc`](../requirements/safety/goals.sdoc), [`assumptions.sdoc`](../requirements/safety/assumptions.sdoc) and
[`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc), issue 3 each (numbered for the first time). History:
issue 1 was **SYS-19** (`c0beb4d`), accepted with changes (`abn-sys19`); issue 2 was **SYS-27** (checked against the
code at `fa04ed1`), accepted (`abn-sys27`) and, with its updates up to DOC-38, released by the user on 2026-09-30. The
update blocks of issue 2 are kept unchanged as history in [section 14](#14-history-of-issue-2-and-its-updates); the
body of this document is the state of issue 3.
Readers: the user as architect and as reviewer of CM-07 under deviation D-01, integrators who want to build a
safety-related ECU on neXtBSW, and the agents of the tasks that change the safety argument.
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**Identifiers are stable.** `ASG-*`, `AoU-*`, `SM-*` and `F1`–`F16`, `G1`–`G6` keep their numbers from the issue that
introduced them, because the requirements, the failure analysis and the safety manual refer to them. Mechanisms that
moved keep their number; new entries get new numbers, none is reused.

### Changes in issue 3 (DOC-51, after the confirmation review CM-07)

The user reviewed issue 2 with all its updates on 2026-10-04 (CM-07, 33 answers, each as recommended on the review
page; record `CR-07-1` (not published, available on request)). Seven minor findings (F-01 … F-07, anomalies AN-136 … AN-142)
and the decisions ZA … ZM are corrected here; none of them changes the argument. The C-numbers are the proposals of
the gap analysis `iso26262-gap.md` (not published, available on request) section 5.1 (decision ZB, section 9.6 below).

| Section | Change | From |
|---|---|---|
| header | issue 3, status, state line; the update blocks of issue 2 moved to section 14 (history), their content is in the body | ZI |
| 0 | "ISO 26262 is not available" replaced: the standard is licensed to the user since 2026-09-22 and compared by the gap analysis; this document cites it by part and clause at most (C1) | ZH, F-05 |
| 1.2 | `tools/a2lgen` exists (not "later"); the consequence of a missing coexistence argument named (C4, pointer to `coexistence.md`) | ZK, ZB, F-07 |
| 2.1 | MC/DC paragraph replaced (C5): no more "the standard was not available" | ZH, F-05 |
| 2.2 | last paragraph: the second degradable case, the trip of a hardware shut-off path (`HW_SHUTOFF`, SYS-135) — never a fault of the MCU, never a finding of the path | ZA, F-01 |
| 3 | SD: the second closed list with `HW_SHUTOFF`; the selected safe state as it is in the tree (bridge-fault rule, hold of the short, RTE port C6, start test SM-27, AoU-33), **SM-26 and SM-27 not credited for release R1**; the admissibility of S3 (C18) | ZA, ZD, ZF, ZB, F-01, F-02, F-04 |
| 4 | **AoU-33** a node of 4.3; **AoU-24** (4.3), **AoU-25** (4.2) and **AoU-40** (4.4) binding (status `concept`); AoU-01 and AoU-05 with FTTI and FHTI (C7); AoU-18 extended (C18); AoU-04 and AoU-32 with the second degradable case | ZF, ZG, ZB, ZA, F-01 … F-03 |
| 6.4 | note on SM-21/SM-22 at the state of SYS-24 and SYS-153 (C11); F4 stays open until SYS-55 | ZJ, F-07 |
| 6.5 | SM-26 and SM-27 *not credited* for release R1 (`mechanisms.sdoc` `CREDITED: no`, notes brought to the tree) | ZD, F-04 |
| 7.2 | first row: **17 ms guaranteed** to de-energised (instead of "≈ 10 ms"); last column named FHTI (C7); new rows for the faults of the time bases of the supervision (stopped system time, gated or slow LPIT clock, **lost SIRC — residual risk DF-05, up to about 283 ms**); SD row with `HW_SHUTOFF` | K07-02, ZK, ZB, F-06, F-07 |
| 7.3 | FTTI of the item against FHTI of the element (C7) | ZB |
| 8.1 | the register lists since SYS-24/SYS-153 instead of "one locked pin and one checked pin"; the qualification path split into unchanged and modified OpenBSW parts (C12) | ZJ, ZB, F-07 |
| 8.2, 8.3, 8.5 | FreeRTOS route (C13); compiler **xPack GCC 11.3.1** (was "11.4"), libraries as software components, the compiler as a tool (C14) | ZK, ZB, F-07 |
| 9.1 | state of F4 and F16 in issue 3 | ZJ, ZG |
| 9.2 | the TOOL-40 figures dated, test entries of `safeLifecycle` and `safeWatchdog` since SYS-41 | ZK, F-07 |
| 9.4 | the open questions to the standard replaced by the answers of the gap analysis section 4 (C15) | ZH, F-05 |
| 9.6 | new: disposition of the proposals C1 … C18 (C6, C10, C17 to DOC-52, after release R1) | ZB |
| 10 | a state note at the head of the section (mechanism range SM-01 … SM-60, number of AoUs, TOOL-40 done) | ZK, F-07 |
| 11, 12 | issue 3 | — |
| 14 | new: history — the changes in issue 2 and the update blocks after it, unchanged | ZI |
| catalogue | `goals.sdoc`: grades unchanged, `COVERAGE_NOTE` of ASG-01, ASG-02, ASG-06, ASG-07 at the state of the tree, ASG-02 with the residual risk DF-05 (ZC); `assumptions.sdoc` and `mechanisms.sdoc` as in rows 4 and 6.5; `ssr.sdoc`: inspection INS-SSR-02 of SSR-086, SSR-089 … SSR-092, the DOC-44 clauses and SSR-024(d), the 27 atomicity warnings judged, the note of SSR-089 brought to the tree, SSR-089 … SSR-092 no longer specify SM-26/SM-27 (ZD) | ZC, ZD, F-04, F-07 |

Not changed, by decision: the coverage grades of the assumed goals (ZC); finding F4 stays open until SYS-55 (ZJ); the
re-rating of SM-21/SM-22 in [`fmea.md`](fmea.md) (proposal M7 of the gap analysis) and the stale `fmea.md` places of
AN-012 and AN-015 belong to the review of the failure analysis CM-02 (ZJ, ZL).

Related documents: the binding architecture target picture [`../architecture/README.md`](../architecture/README.md),
the relationship to Eclipse OpenBSW [`../architecture/openbsw.md`](../architecture/openbsw.md) and
[`../architecture/openbsw-naehe.md`](../architecture/openbsw-naehe.md), the module catalogue
[`../reference/modules.md`](../reference/modules.md), the signal qualifier
[`../architecture/signal-qualifier.md`](../architecture/signal-qualifier.md) and the debug-halt analysis
`../proposals/IO-32-debug-halt-detection.md` (not published, available on request). Safety documents built
on this concept (added by DOC-48): the safety plan [`safety-plan.md`](safety-plan.md) (lifecycle, roles and the
confirmation measures, among them CM-07 for this concept), the argument over the admissible configuration range
[`config-range.md`](config-range.md) (the range of configurations for which this concept holds), the safety manual
[`safety-manual.md`](safety-manual.md) (section 2: the external interfaces of the element as the integrator uses
them), the gap analysis against ISO 26262 `iso26262-gap.md` (not published, available on request), the coexistence argument
[`coexistence.md`](coexistence.md), the component qualification
[`component-qualification.md`](component-qualification.md) and the dependent failure analysis [`dfa.md`](dfa.md).

---

## 0. How to read this document, and what it deliberately does not do

**The standard and this document.** ISO 26262:2018 is licensed to the user since 2026-09-22; it is not part of the
repository. The comparison of neXtBSW with it is the gap analysis `iso26262-gap.md` (not published, available on request) (SYS-58). This
document is written in the vocabulary the field has made common (SEooC, ASIL, assumption of use, safe state, fault
tolerant time interval, fault handling time interval, diagnostic coverage, freedom from interference, tool
confidence level) and explains each term in its own words where it is first used. Where it refers to the standard it
cites **part and clause only** (for example "6-7.4.9" = ISO 26262-6:2018, clause 7.4.9) and never reproduces its text,
tables or figures; the requirements behind such a reference are in the gap analysis in its own words. The questions
issues 1 and 2 had to leave open for lack of the standard are answered there (section 9.4); the gap analysis's
proposals for this document and their disposition are listed in section 9.6. *Issue 3 (C1, decision ZH of CM-07):*
until issue 2 this paragraph said that the standard was not available and that the document contains no clause
references.

**No invented numbers.** This concept states no failure rates, no FIT values and no diagnostic coverage
percentages. Every time given here is a constant of the code (and the file it comes from is named), a configured
value of the project, a value **derived** from those (the derivation is named), or a **measurement** with its date
and record. Where a number would be needed but does not exist, the entry says so. The diagnostic coverage of each
stage-1 mechanism is rated as an ordinal class in the failure analysis [`fmea.md`](fmea.md) (SYS-20); for the device
drivers it is the later stage **SYS-28**, which additionally needs manufacturer documents that we do not have
(section 13).

**What this document is.** The concept level: what the element is, what it assumes, which mechanisms exist, what
they react to and how fast, and where the boundary to non-qualified third-party software runs. It is the input for
the failure analysis (SYS-20), the freedom-from-interference work (SYS-21), the requirements with traceability
(SYS-22) and the safety manual for integrators (DOC-19).

**What this document is not.** It is not a safety case, not a proof of compliance, and not a promise that an ECU
built on neXtBSW is ASIL B capable. neXtBSW is a hobby project; nothing here has been assessed by anyone outside
it.

---

## 1. The element

### 1.1 What "out of context" means here

neXtBSW is developed without knowing the vehicle, the ECU or the hazard it will be part of. An element built that
way makes its assumptions explicit instead: it states which safety goals it *assumes* the item has, which ASIL it
*aims* at, and which duties it hands to the integrator. The integrator then checks the assumptions against his own
item and either accepts them or replaces them — and where he replaces one, the mechanisms in section 6 have to be
re-evaluated for his case.

This is exactly the shape of the rest of the project: the architecture already refuses to let the eval board decide
anything (CLAUDE.md, guiding principle), configuration is separated from code in three levels, and every module
declares what it needs and what it provides. The safety concept is the same idea applied to the hazard side: the
element declares what it needs from its context.

### 1.2 Scope: what is part of the element

The user decided on 2026-09-21 (decision board `safety-umfang`): **everything, including the device drivers** — not
only the core. The element is therefore the whole of neXtBSW as it is built into an ECU image, plus the tools that
produce parts of that image.

With the acceptance of issue 1 (`abn-sys19`, same day) the user **staged** that scope: the safety argument for ASIL
B is built from mechanisms of the base software alone (stage 1, now); the device drivers' own safety mechanisms
follow later and encapsulated (stage 2, SYS-28). The device drivers stay in the table below, because their code
runs in the image and the rules of stage 1 apply to it (static analysis, freedom from interference, the `safe_off`
declaration of their outputs), but their diagnostics are **not credited** in stage 1. Section 1.4 draws the line.

**In scope — software that runs on the target**

| Group | Modules | Role in the concept |
|---|---|---|
| Safety supervision | `ecu/safety/safeSupervisor`, `ecu/safety/safeWatchdog` | central event handling and reaction, MCU watchdog |
| Family safety port (s32k1xx) | `platforms/s32k1xx/port/safety/{safeLifecycle,safeMemory,safeIo,safeSystem,adcSafetyConsole}` | MPU, ROM check, ECC, register locking and checking, ADC reference and internal supplies, safe state |
| Fault handlers | `platforms/s32k1xx/hardFaultHandler` (`HardFaultDump.h`, `hardFaultDumpLayout.h`, `safeOutputs.h`) | first action on a fault: switch the power outputs off; then the dump |
| Debug-halt guard | `platforms/s32k1xx/port/main/src/integration` (`DebugHaltGuardSystem.h`, `LpitHaltDeadman`), ISR `LPIT0_Ch1_IRQHandler` (`Integration.cpp`) | a core halt or a software stall longer than 10 ms de-energises the power stage (IO-32) |
| RTE and its run-time supervision | `tools/rtegen` output, `ecu/application/src/systems/RteSystem.cpp` | task and runnable deadlines as events (SYS-05) |
| ECU state and start reasons | `libs/bsw/ecuState`, `platforms/s32k1xx/port/mcuResetReason` | defined start-up, run-down and shutdown; start reason from two sources (SYS-18) |
| Data protection | `libs/bsw/e2e` (P01, P02, P11), signal qualifier of the RTE | protection of safety-related signals on CAN |
| Communication | `libs/bsw/canSystem` (transmit gate, bus state, bus-off), `libs/bsw/canPhyPassive` | controlled behaviour of a bus that is not healthy |
| Analogue input path | `libs/bsp/bspAdcAccess` and the generated sensor conversion | value plus quality qualifier per channel |
| **Device drivers** | `libs/bsw/gdTmc6200` (TMC6200), `libs/bsw/bccMc33771` (MC33771C), `libs/bsw/sbcUja113x` (UJA113x), `libs/bsw/highSideSwitch` (BTS5030), `libs/bsw/hallDecoder` | stage 1: code in the image, `safe_off` declaration of their power outputs (feeds SM-23/SM-24); stage 2: the diagnostic functions of the parts and the driver rules around them (section 13) |
| MCU drivers of the family | `platforms/s32k1xx/bsp/*`, `platforms/s32k1xx/port/bspConfiguration/*` | everything a mechanism above reads or writes |
| Bootloader | `bootloader/` (core, family HAL, updater, validity record) | image integrity before the application starts |

**In scope — off-target parts that decide what the image contains**

- The configuration data: `mcudb/<family>/<derivative>.yaml`, `boards/<board>/board.yaml`,
  `projects/<project>/{project.yaml,rte.yaml,*.dbc}`. These are **safety-related data**, not comments: the safe
  outputs table, the watchdog settings, the deadlines of SYS-05 and the ADC reference band all come from them.
- The generators that turn them into code: `tools/nxconfig`, `tools/bswgen`, `tools/rtegen`,
  `tools/dbcgen`, `blgen` (`tools/bswgen/blgen.py`) and `tools/a2lgen` (*issue 3:* issue 2 said "later
  `a2lgen`"; the tool exists). Their classification and the safeguards against a wrong generated file are
  **PLAT-75** ([`tool-classification.md`](tool-classification.md)). *Corrected by DOC-48:* issue 2 also listed
  `tools/intgen`, which does not exist (TF3 of the tool classification), and gave `blgen` a folder of its own.

**Out of scope**

- The application itself. SWCs from Simulink are the integrator's; neXtBSW carries them, protects their
  communication and supervises their timing, but says nothing about what they compute.
- The hardware. No claim is made about the failure rates, the single-point fault metric or the latent-fault metric
  of the MCU, the board or any fitted part. Those are the integrator's (AoU-09).
- The item-level safety concept: the hazard analysis, the safety goals of the actual vehicle function, the ASIL
  determination and the fault tolerant time interval (AoU-01).

The external interfaces of the element as the integrator uses them (safe supervisor, RTE, configuration files,
hardware-software interface) are listed in section 2 of the safety manual [`safety-manual.md`](safety-manual.md)
(proposal C3 of the gap analysis).

**Everything in the image is ASIL B unless argued otherwise** (proposal C4, 6-7.4.8 and 9-6 in the reading of the gap
analysis). Without a coexistence argument, all software in the image — the application and also neXtBSW's own QM
parts (console, UDS, logger, CAN stack, NvM) and the OpenBSW code — would have to be developed at ASIL B. Which module
is argued free from interference and which still counts as ASIL B is decided per module in
[`coexistence.md`](coexistence.md) (SYS-61); AoU-17 carries the consequence to the integrator.

### 1.3 Platform status

The concept is written for the **s32k1xx** family, because that is the only family with a safety port today. The
mechanisms of section 6 are split deliberately into a family-neutral part (the supervisor, its events and
reactions, the safe outputs table semantics, the RTE deadlines, E2E, ecuState) and a family part (MPU, ERM/ECC,
CRC, LPIT deadman, SIM supply monitor, watchdog registers). For `stm32`, `s32k3xx` and `rm4x` the family part does
not exist yet; an integrator on those families gets the neutral part and nothing else. That is a gap, not a
silence — see section 9.2.

### 1.4 Two stages: base-software mechanisms now, device-driver mechanisms later

The line between the two stages follows **who detects and who executes**, not which file a line of code lives in:

| Stage 1 — base software (credited now) | Stage 2 — device drivers (later, encapsulated, not credited now) |
|---|---|
| mechanisms whose detection runs on the MCU and uses only the MCU, its peripherals and the configuration: supervisor, watchdog, MPU, ECC, ROM check, supply and reference supervision, register lock/check, halt deadman, fault handlers, RTE deadlines, E2E, signal qualifier, CAN gate, ecuState, bootloader image check | diagnostics that a **part outside the MCU** performs or reports over a bus, and the driver rules that react to them: SM-40 (TMC6200), SM-41 (MC33771C), SM-42 (UJA113x), SM-43 (BTS5030), and the device-dependent parts of SM-33 (transceiver status through the SBC) and SM-52 (SBC reset counter) |
| the **safe outputs table** and its walk (SM-23 … SM-25) — including the entries a device driver *declares* with `safe_off`, because the table walk writes MCU port registers and needs no driver code | anything that needs the driver to run, a bus to work or the part to answer in order to take effect |

The consequence is deliberate and stated per goal in section 2.2: a safety goal whose detection lives in a part
outside the MCU (cell voltages, balancing) has **no stage-1 coverage**, and the gap is named (section 9.5), not
filled with driver diagnostics. The interface through which stage 2 will report into the base software — a fault
or qualifier word to the safe supervisor — is itself a base-software mechanism and is proposed for stage 1
(gap G5, section 10.9).

---

## 2. Assumed safety goals and target ASIL

### 2.1 Target ASIL

**ASIL B** (user, decision board `safety-asil`, 2026-09-21), **confirmed for the first implementation** with the
acceptance of issue 1 (`abn-sys19`). First intended applications named by the user: **motor control** and
**battery management**.

Consequences taken in this concept:

- Freedom from interference between safety-related and QM software is treated as **required** — but without the
  independence and decomposition arguments that a higher ASIL would need. The work is **SYS-21**; today all tasks
  share one address space and the MPU protects only the safety manager's data (section 6.3).
- **Structural coverage at unit level** (proposal C5): at ASIL B the standard expects statement and branch coverage
  strongly and recommends modified condition/decision coverage (MC/DC), which it expects strongly only at ASIL D; it
  sets no number, but a missing or unjustified target counts as insufficient (6-9.4.4, in the reading of
  `iso26262-gap.md` (not published, available on request) section 4 point 7). The measurement infrastructure exists (TOOL-40) and reports
  statement and branch coverage against thresholds per module (`tools/test/coverage.yaml`); the path to MC/DC is
  named there for the case of a higher ASIL. Enforcing the thresholds with a rationale per shortfall is proposal C17
  (task DOC-52, after release R1). *Issue 3:* issue 2 called MC/DC "desirable but not required" and left out a clause
  reference "because the standard was not available".
- A single ECU without a redundant channel is assumed. No lockstep core, no second MCU voting — the S32K1 has
  neither, and the multi-MCU work (ARCH-47) is about flashing, not about redundancy.

**ASIL C and D are to be considered later** (user, `abn-sys19`). The element makes no claim for them today, but
the user's decision asks the base software to implement its mechanisms "um ASIL B (oder höher) erreichen zu
können" — so no mechanism should be built in a way that closes the path upwards. What a later stage for C/D would
have to add is kept visible rather than struck out:

- **independence** of the supervising channel from the supervised one — today supervision runs in the same core
  and the same clock domain (AoU-13), which is the main single-channel limit;
- **decomposition** arguments between partitions, on top of the partitioning of SYS-21;
- the **MC/DC path** of TOOL-40, named there and not built;
- the replacement of the non-qualified RTOS, compiler and libraries (section 8) rather than their argument from use.

Which mechanisms would have to be *replaced* rather than extended for C/D is a question for the later stage; the
single-channel supervision inside one core and the non-qualified RTOS and compiler are the known candidates.
AoU-03 carries the integrator's side.

### 2.2 Assumed safety goals

These are **assumptions about the item**, not statements about a real vehicle. Each is written so that it can be
checked off or replaced by the integrator. The element does not fulfil any of them by itself; it contributes the
mechanisms listed in the column "stage 1".

**Strict separation (user, `abn-sys19`).** Only base-software mechanisms (section 1.4) are credited. The column
"later device contribution" names what the device drivers will add in stage 2 (section 13); it is **not** part of
the argument today and must not be counted by SYS-20, SYS-22 or DOC-19. Where the base software alone does not
cover a goal, the column "coverage at stage 1" says so and points to a gap in section 9.5.

**The goals** (ASG-01, ASG-02, ASG-03, ASG-04, ASG-05, ASG-06 and ASG-07, all ASIL B assumed) are the nodes of [`goals.sdoc`](../requirements/safety/goals.sdoc) (moved from this table by DOC-21). Each node holds the goal, the ASIL assumed, the base-software mechanisms credited at stage 1 (field `STAGE1_CONTRIBUTION`), the coverage at stage 1 (`COVERAGE` with the lead word covered / limited / partial / none and `COVERAGE_NOTE` with the full statement and the gaps of section 9.5) and the later device contribution, which is not credited (`LATER_CONTRIBUTION`).

What changed against issue 1: ASG-01, ASG-03 and ASG-04 were argued with SM-40 and SM-41 there; under the strict
separation they lose that support, and the honest result is one partial and two uncovered goals. ASG-07 was
argued with a run-time ROM check that — as SYS-23 found — never runs. The mechanism numbers in this table were
also corrected: issue 1 cited SM-20/21/22 for the safe outputs, the fault handlers and the halt deadman (catalogue:
SM-23, SM-17, SM-06), SM-13 for the register lock and check (catalogue: SM-21/SM-22) and SM-11 for the ROM check
(catalogue: SM-14).

**If an integrator's safety goal requires the function to keep working** (a fail-operational goal) rather than to
stop, neXtBSW supports it only within the limits of AoU-04 and AoU-32 (SYS-117): for the events the supervisor declares
degradable — faults of one attached device and, since SYS-135, the trip of a hardware shut-off path (SM-58, report
`HW_SHUTOFF`), never a fault of the MCU itself and never a finding of the shut-off path — a project may switch only
the affected safe-output groups and keep the rest running (SD, section 3). A function that must continue after a
fault of the MCU is not supported; every other reaction stays fail-safe — de-energise, or reset, or both.
*Issue 3 (CM-07 decision ZA, F-01):* the text released in review SYS-117 (R8) named the device fault only; the trip
of a hardware shut-off path is the second case, decided by the user in the safety review of SYS-128/SYS-130 (C1).

---

## 3. Safe states

neXtBSW knows **two** reactions of its own, plus "report and continue" — and, since SYS-23, a held variant of the
first (S1 held, below), decided by the user on 2026-09-21 (`sys23-halt`, option a; *corrected by DOC-48*, issue 2
called the decision still open). Since SYS-117 a project may choose a fifth,
**SD — degraded**, for the few events the supervisor declares degradable (below).

**S1 — de-energised outputs ("safe state").** The generated **safe outputs table of the project** is walked: every
PWM group of the project is forced to its inactive level through the FTM software output control, and every output
that a module manifest declares `connect.<name>.safe_off` (for example the enable of a gate driver) is driven
inactive. The walk needs no stack, no RAM and no driver object, it skips a peripheral whose clock is gated, and it
ends with a data synchronisation barrier — so it is callable from a task, from the supervisor and from an interrupt
(`platforms/s32k1xx/hardFaultHandler/include/hardFaultHandler/safeOutputs.h`, SYS-06/IO-32).

S1 is a **one-way street upwards**. `leaveSafeState()` releases only the indicator output (an LED on the eval
board). No power output is switched back on there, because the safe-state code does not know the state a driver
would have to return to. A power stage comes back only through an explicit `enable()` of its own driver.
The indicator comes back with the level the application last wrote to it, which is why it must be no power output
and should not be written by the application at all (AoU-30, SYS-87).

On a board without power outputs the table is empty and entering S1 writes no peripheral register at all. That is
the board-neutral case and it is covered by the second build profile (`buildtest_s32k148_min`,
`NX_SAFE_OUTPUT_COUNT 0`).

**Selected safe state of a PWM group (SYS-116, decisions C1 … C6 of 2026-09-29).** "Inactive level" above is
**freewheel**, and it stays the only safe state of every group whose project lists no active short. A project may
allow per group `active_short_low` and/or `active_short_high` (`pwm.<group>.safe_states`, freewheel always listed);
the application selects the state in force at run time through a service that accepts only an allowed, not withdrawn
state. Every walk of the table - S1, S1 held, SD, the fault handlers, the halt deadman - then writes, per entry of
the group, the value of the selected state: for the timer the short pattern (all channels forced, the low-side resp.
high-side ones to their active level, before dead-time insertion), for the gate-driver enable bound to the group a
store that leaves it as it is. The selection word is read once per entry from ordinary RAM and must hold **exactly**
the pattern of an allowed short; any other value (0 after a reset, a corrupted word, the pattern of a short the group
does not allow) and a gated clock of the group's timer give freewheel. A bridge fault of a device bound to the group
withdraws the short for the power cycle before any walk of the reaction (decision C4, in the safe supervisor since
SYS-140, `NX_SAFE_BRIDGE_FAULT_CLASSES`, SSR-091), the family port keeps the short in S1, in a degraded group and after
a debug halt (C2, SYS-139, `NX_SAFE_STATE_SHORT_HOLD`, SSR-092), and the hardware fault input of the timer forces
freewheel by itself (SYS-85, SM-58). Every reaction that resets ends the short: during reset and restart the bridge is
in the board's reset state (C3, AoU-33 (c)). With both code facts in the tree `nxconfig` accepts an active short
(since SYS-139/SYS-140, `7f82689e`, `b9eac59c`, 2026-09-30; before it refused every short). The application requests
the state through the RTE port `safety.safe_state.<GROUP>`, a refused request leaves the selection as it is and is
reported (C6, SYS-141). The integrator's side is **AoU-33** (section 4.3).

**Start-up test of the active short (SM-27, SYS-141/SYS-143).** `RteSystem::init()` builds every short whose project
enables the test once, before the init runnables, **only after a power-on**: the pattern is forced and read back from
the timer, the state found before is restored and read back, and the gate-driver enable bound to the group must read
off before and after (read through the driver). After any other reset the outcome of the power-on test is taken from a
record in no-init RAM; without an intact record of a passed test the group has freewheel only until the next
power-on. The hold is a busy wait whose sum over all groups `tools/rtegen` bounds to a third of the MCU watchdog
time-out. A failed test removes the shorts of the group until the next power-on; the phase voltages (IO-64) are not
measured. The integrator's side is **AoU-38**.

**Not credited for release R1.** SM-26 and SM-27 are described and host verified (`safe-state-select`,
`short-hold`, `safe-supervisor-bridge-fault`, `nxconfig-safestate`, the start-test tests of `tools/rtegen`); their
board proof is open ([`fmea.md`](fmea.md) SM-26, SM-27, B12 … B14). Decision ZD of CM-07: for R1 they are *not
credited* (`CREDITED: no` in [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc), as the safety manual already
reads them); the requirements SSR-089 … SSR-092 stay and specify SM-23 or SM-34 (section 6.5). *Issue 3 (F-02, F-04):*
issue 2 called AoU-33 "announced" and the RTE port a follow-up here, and credited SM-26/SM-27 in the catalogue.

**S2 — software system reset.** `softwareSystemReset()` after `enterLimpHome()` and after S1. A reset is a
*reaction*, not a repair: what happens afterwards depends on the board. With a system basis chip in the loop, the
SBC counts resets (RCC) and goes to forced sleep on the fourth, which removes the MCU supply until a wake-up; on
the eval board that path is armed only with JP1 closed. The ECU state management reads the start reason from two
sources since SYS-18 and can be configured to go to sleep after a watchdog reset
(`modules.ecuState.after_watchdog_reset: sleep`).

**S1 held — safe state held without reset (SYS-23).** For a fault that a reset cannot remove, S1 + S2 would only
restart into the same fault, in a loop. `SafeSupervisor::holdSafeState()`
(`ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp`) therefore enters limp home and S1 and then
**stays**: it runs with all interrupts suspended (the lock taken in `handle()`), so no task, no interrupt and in
particular no application runs any more; it walks the safe outputs table again in every round, so S1 stays asserted
even if a register is changed in between; and it services the MCU watchdog in every round, because otherwise the
watchdog would end the hold after at most 250 ms with a reset into the same loop. An external watchdog (SBC) is
deliberately not serviced — with one, its reset and reset counter decide (SM-52). Once, after the first table walk
and before the loop, it writes one synchronous console line
(`SAFE STATE HELD (<event>): outputs off, no reset - power cycle required`) so that a board in the hold is not
silent. It ends only with a power cycle or a reset from outside. Used at first by exactly two events:
`WATCHDOG_STARTUP_CHECK_FAILURE` (SM-07) and `ROM_CHECK_FAILURE` (SM-14). *State issue 3:* also the reset-loop limit
(SM-50, SYS-43), a `DEVICE_FAULT` of the class `IDENTITY` (SM-34) and a finding of a hardware shut-off path
(`HW_SHUTOFF` with findings, SYS-130; safety manual section 4).

> **Decision `sys23-halt` (user, 2026-09-21): (a) - S1 held without reset stays as implemented.** Rejected were
> S1 + S2 for the ROM check (the bootloader CRC at start covers only corruption after flashing and only projects
> with the bootloader) and a "blocked start" in no-init RAM (needs NVM-03; may come later as an improvement). The
> hold services the MCU watchdog. Accepted consequence: a debugger that halts the core during the 6 ms start-up
> self-test makes the self-test fail and leads into the hold; a power cycle ends it.

**S3 — report and continue.** The event is logged. The error memory exists since NVM-03; an S3 event is stored there
only where a DTC is defined for it (today the take-back of a foreign `safe_off` level, SYS-196), while
`RTE_TIMING_VIOLATION` stays a log entry (SYS-113 left it out of the DTC table on purpose). This
is the reaction for events where a reset would do more harm than good — today a single RTE timing violation. In
issue 1 it was also, unintentionally, the reaction for two events that should have one (finding F1); since SYS-23
those two go to S1 held.

*When S3 is admissible (proposal C18, the answer of `iso26262-gap.md` (not published, available on request) section 4 point 6):* the
standard prescribes no reaction; a fault counts as handled only if the safe state is reached within the fault
tolerant time interval (FTTI) of the item. "Report and continue" is therefore admissible only where the analysis shows
that the detected condition cannot violate a safety requirement within the FTTI, or where the reaction is allocated to
another element with its own fault handling time. For the RTE timing violation (SM-05) the reaction is allocated to
the application: **AoU-18** (extended in issue 3). A latent fault of a safety mechanism is never only reported - that
is why F1 ended in S1 held.

**SD — degraded (SYS-117, decisions B1 … B6 of 2026-09-29).** For an event on the supervisor's **closed list** of
degradable events (`NX_SAFE_DEGRADABLE_EVENTS`, `safeSupervisor/Degraded.h`; today `DEVICE_FAULT` of a well-formed
report whose class is not `IDENTITY`; the trip of a hardware shut-off path is the second case, below) that the
project maps to **safe-output groups** (`project.yaml`
`safety.output_groups`, `safety.degraded`), `handle()` drives only the entries of the safe outputs table that belong to
those groups to their safe state (the same table and the same walk as S1, restricted by an entry mask), then latches
the groups, records them in no-init RAM and reports them (log; RTE signal `safety.degraded` to the application, which
a DTC samples for the error memory). No reset, no limp home, every other group, task and mechanism keeps running.
Groups only accumulate and are walked again in every safety cycle; leaving S1 releases none; only a power cycle
ends SD — a safety reset re-applies the record before the BSP starts, and an implausible record or latch counts as
every group degraded. When the last group that still works is degraded, SD becomes **S1 without a reset**. Faults of
the MCU itself (program flow, time base, watchdog, memory, image, kernel, clock, supply, MPU) are **never** on the
list: they put the supervision of the remaining function in doubt; `nxconfig` refuses such a mapping with its YAML
path, and the supervisor refuses it once more at compile time. A device fault on a bus the supervisor regards as lost
(F5) is degraded only if every device on that bus is mapped. A project without a mapping gets not one line of SD
compiled in. Limits and residuals: [`fmea.md`](fmea.md) SM-23; the interplay with the partner supervision (SYS-115)
and the active short (SYS-116): [`partner-and-degraded.md`](partner-and-degraded.md) section 4.5.

**The second degradable case: the trip of a hardware shut-off path (SYS-135).** Decided by the user as safety
responsible in the safety review of SYS-128/SYS-130 (point C1): the latched trip of a hardware shut-off path (SM-58,
report `HW_SHUTOFF` of SYS-130) need not hold S1 for the whole ECU. A project may map it with
`safety.degraded: [{event: HW_SHUTOFF, groups: [<group>]}]` to exactly the safe-output group of the PWM group the path
switches off; a trip then degrades **only that group**, without a reset (the path stays latched until reset, SYS-86
H1), the rest of the ECU keeps running, and the last group escalates to S1 without a reset. The report is on a
**second closed list** of the supervisor (`NX_SAFE_DEGRADABLE_REPORTS` in `safeSupervisor/Degraded.h`), because it is
no event of `SafeSupervisor::Event`. Only "tripped and nothing else" is degraded: a finding of the path (switched off,
reconfigured, stuck, not latched; SYS-128), a report without context, an unmapped timer and an implausible record keep
S1 held - they may stem from a fault of the MCU, which is never degraded. Without the mapping nothing changes. SM-58
itself stays *not credited* (section 6.5), and its start test needs AoU-37. *Issue 3 (CM-07 decision ZA, F-01):* the
texts released in review SYS-117 (R8) - this paragraph on SD, the last paragraph of section 2.2, AoU-04 and AoU-32 -
now name this case; issue 2 described it only in the update block of SYS-135.

Which event leads to which reaction is decided in exactly one place:
`ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp`, function `handle()`. Every mechanism below only
raises an event; it never decides the reaction itself. That separation is the load-bearing design decision of the
whole concept, and SYS-05 and SYS-06 both re-confirmed it.

---

## 4. Assumptions of use (AoU)

These are the duties neXtBSW hands to the integrator. An integrator who cannot meet one of them either does not
get the corresponding claim, or must add his own measure. DOC-19 turns this list into the integrator-facing safety
manual with "how to fulfil it" for each entry.

**The assumptions themselves** are the nodes of [`assumptions.sdoc`](../requirements/safety/assumptions.sdoc) (moved from the tables of 4.1 … 4.5 by DOC-21,
each with its statement verbatim, a short title and the status `concept`); the subsections below name the IDs of
each group. *Issue 3 (CM-07 decisions ZF and ZG):* every assumption of the document has the status `concept` and is
binding. **AoU-33** (the active short of a PWM group, proposed in [`partner-and-degraded.md`](partner-and-degraded.md)
7.1 and in force through the safety manual since CM-10) is a node of 4.3 with the points (a) … (d); **AoU-24** and
**AoU-25**, proposed by the failure analysis and binding through the safety manual since its acceptance (review
DOC-19, D3), are nodes of 4.3 and 4.2; **AoU-40** (NVM-25) is binding in 4.4. Issue 2 called AoU-24/AoU-25 and AoU-40
"proposed" and AoU-33 "announced" here. AoU-01 and AoU-05 speak of the fault tolerant time interval (FTTI) of the
item and the maximum fault handling time interval (FHTI) of the element (C7), AoU-18 allocates the reaction to a
timing violation to the application (C18), AoU-04 and AoU-32 name the trip of a hardware shut-off path (ZA).

### 4.1 Item, goals and ASIL

AoU-01, AoU-02, AoU-03, AoU-04, AoU-05, AoU-32 and AoU-35 - section 4.1 of [`assumptions.sdoc`](../requirements/safety/assumptions.sdoc).

### 4.2 Configuration is part of the safety argument

AoU-06, AoU-07, AoU-08, AoU-09, AoU-10, AoU-11, AoU-12, AoU-30 and AoU-25 (restart or ROM re-check interval within the latent-fault interval; issue 3) - section 4.2 of [`assumptions.sdoc`](../requirements/safety/assumptions.sdoc).

### 4.3 Hardware around the MCU

AoU-13, AoU-14, AoU-15, AoU-16, AoU-34, AoU-36, AoU-37, AoU-38, AoU-39, AoU-24 (tolerance of the ADC reference against the accuracy needed; issue 3) and AoU-33 (the active short of a PWM group; issue 3) - section 4.3 of [`assumptions.sdoc`](../requirements/safety/assumptions.sdoc).

### 4.4 Software the integrator adds

AoU-17, AoU-18, AoU-19, AoU-20, AoU-21, AoU-22, AoU-23, AoU-40 (holds on the FlexNVM block, NVM-25; binding since issue 3), AoU-41 (no other bus master than the core, S171-1) and AoU-42 (all tasks run privileged, SYS-172 P1; both decided by the safety responsible 2026-10-02, review Welle-0210-Tag) - section 4.4 of [`assumptions.sdoc`](../requirements/safety/assumptions.sdoc).

### 4.5 Tools and generated code (new after issue 2, DOC-38)

Proposed by PLAT-75 in [`tool-classification.md`](tool-classification.md) section 6, carried into the safety manual by
DOC-19 and into this section by DOC-38 (review DOC-19, D2 and D3). The full wording with the tool rows it rests on is
in the tool classification; the entries below state the duty.

AoU-26, AoU-27, AoU-28 and AoU-29 - section 4.5 of [`assumptions.sdoc`](../requirements/safety/assumptions.sdoc).

---

## 5. How the mechanisms are organised

Three levels, and it matters which one a reader is looking at:

1. **Detection.** A piece of code notices something. It raises an event on a monitor of the safe supervisor
   (`trigger()`, `check()`, `hit()`), optionally with a context word.
2. **Reaction.** `SafeSupervisor::handle()` decides: log and return (S3), only the mapped safe-output groups to their
   safe state without reset (SD, SYS-117, for the degradable events a project maps), safe state without reset (S1),
   limp home + safe state held without reset (S1 held, `holdSafeState()`, SYS-23), or limp home + safe state +
   reset (S1 + S2).
3. **Recovery.** Whatever happens after the reset — start reason, ECU state, the reset counter of the SBC, the
   configured `after_watchdog_reset` behaviour.

Two mechanisms bypass level 1 and 2 on purpose, because the code that would run them cannot be trusted at that
moment: the **assembler fault handlers** and the **halt deadman interrupt** walk the safe outputs table directly,
before anything else, and only afterwards report. That is the reason the table exists as a plain array of address
/ value / clock-gate words rather than as driver calls.

---

## 6. Catalogue of safety mechanisms

**The catalogue itself** is [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc) (moved from the tables of 6.1 … 6.7 by DOC-21, fields `FAULT_MODEL`, `DETECTION`, `REACTION`, `WORST_CASE`; `STAGE 2` for the device-dependent rows, `CREDITED: no` for a stage-1 row marked *not credited*). The subsections below name the IDs and keep the notes.

**How to read the fields.** *Fault model* is what the mechanism is meant to catch, written as the failure, not as
the cause. *Detection* names the code and the period at which it runs. *Reaction* uses the safe states S1, S2, S3
of section 3. *Worst case to reaction* is the time from the fault being present to the reaction having taken
effect, derived from the periods; where a part of it is not measured, that is said. *Evidence* names the test
entry in `tools/test/tests.yaml` (run with `.venv/Scripts/python tools/test -k <name>`), or "board" where
`CHANGELOG.md` records a board run.

Everything in this catalogue is a mechanism that exists in the code today. Mechanisms that are planned are in
section 9.

### 6.1 Program flow and time

SM-01, SM-02, SM-03, SM-04, SM-05, SM-06 and SM-07 - section 6.1 of [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc).

Notes.

- **SM-01 and SM-06 overlap on purpose and cover different windows.** The watchdog catches a stall of more than
  250 ms and answers with a reset; the deadman catches a stall of more than 10 ms and answers by de-energising
  without a reset. A halt longer than 250 ms hits the watchdog warning interrupt first (IRQ 22, WDOG_EWM), whose
  handler runs the same safe outputs walk as its first action, before the dump and before any output — measured on
  the board on 2026-09-18 (EXC_RETURN `0xffffffed`, DRV_EN off afterwards).
- **SM-06's residual window.** A halt shorter than 10 ms is seen only through the `DFSR`, and only if the debug
  probe leaves it standing — the J-Link Commander clears it when it resumes the core (reproduced twice on the
  board on 2026-09-18). Halt below 10 ms **and** a probe that clears the DFSR is a gap the user accepted
  knowingly (decision `io32-halt-erkennung`, question 2). Software can never react *during* a halt; only the
  hardware change documented in `gdTmc6200.md` (DRV_EN behind an alive latch) closes that.
- **SM-06 on ARMv6-M.** Cortex-M0+ has no priority level above the RTOS syscall boundary; the handler takes the
  highest level there but can be delayed by a critical section for the length of that section. That is a property
  of the core, not of the mechanism.

### 6.2 Memory

SM-10, SM-11, SM-12, SM-13, SM-14, SM-15, SM-16, SM-55, SM-17 and SM-60 (*not credited*, proposed by NVM-25) - section 6.2 of [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc).

Notes.

- The ECC detection is a **status poll, not an exception route.** A double-bit error is found at the next safety
  cycle, not at the access that caused it. The ERM double-fault interrupt vector exists and is wrapped
  (`ERM_double_fault_IRQHandler` in `IsrWrapper.h`), but the reaction path we rely on is the poll. Whether that is
  sufficient for the fault tolerant time interval of the integrator's goal is an AoU-05 question.
- **SM-14 was not active until SYS-38** (found by SYS-23; confirmed on the board in session d3, 2026-09-21: the list
  address read 0xFF, a changed byte stopped nothing). Three things were missing against OpenBSW: the reference
  application calls `RomCheck.idle()` from its idle handler (`app.cpp`), neXtBSW's did not; OpenBSW runs
  `tools/safety/crcCheck.py` after the link (only with `PLATFORM_SUPPORT_ROM_CHECK`, a script with a fixed build path
  and `pyelftools`, which this repository does not install), neXtBSW had no such step; and neXtBSW removed `.checksum`
  from the HEX because at the end of the flash it stretched the CAN download. Had the check run, `RomCheck` would
  have taken the erased word 0xFFFFFFFF as the number of areas and walked the addresses 0xFFFFFFFF.
- **No false alarm by construction** — a false `ROM_CHECK_FAILURE` holds the ECU until a power cycle (S1 held):
  the check is only compiled into an image whose reference the build wrote (`NX_ROM_CHECK_IMAGE`, set by
  `nx_rom_check_image()`), the post-link step verifies the finished HEX (list present, result present and equal to
  the CRC over the areas as the flash will hold them, gaps as 0xFF like canflash downloads them), and every flash byte
  of the image is inside an area or is the list itself. The CRC of the tool is checked against a register model of
  the CRC unit, and the real `RomCheck.cpp` runs against the same model in the host test `rom-check`.
- **Interplay with the bootloader** (SM-51): the post-link step runs before `canflash record`, so the validity record
  covers the image including list and reference (demo: 0x8000…0x320FC, the record's length 0x2A0FC). `canflash
  flash` computes its CRC over whatever file it is given, so an image changed *after* the build passes the
  bootloader and is caught by SM-14 at run time — that is the board test of SYS-38. The bootloader protects the list
  and the reference themselves at every start; SM-14 does not cover them (they are not in any area).
- SM-14's run time is **not bounded per call**: the CRC runs over all areas in one go (demo: 172 196 bytes at
  SYS-38, 197 044 bytes at SYS-44) in the idle context. That was finding F3; SYS-20 decided that no slicing is needed
  at idle priority without a lock (fmea.md 5.1). Since SYS-44 the pass is repeated with the period of the project.
- **No false alarm from flash writes** (SYS-44): a write that begins during a pass discards the pass instead of
  reporting it, and no pass starts while one is open. The write indicator lies in ordinary RAM (library
  `romCheckFlashWrites`), so every writer can update it without access to the protected RAM; a wild write into it can
  only postpone the check, and postponement beyond one period is itself reported.

### 6.3 Freedom from interference — what exists and what does not

Today: one address space for all tasks; the MPU separates the **safety manager's data and stack** from everything
else (`FUSA_REGION`, `SAFETY_STACK_REGION`), and the fusa gate plus the ISR hooks make sure that window is as
narrow as possible. That is protection of the supervisor from the rest — it is **not** partitioning of the
application.

Missing for ASIL B, and therefore the content of **SYS-21**:

- partitions (safety-related / QM) with memory regions and tasks derived from the configuration;
- an access violation from a QM partition as an event to the safe supervisor rather than a plain fault;
- temporal freedom from interference: an execution budget per partition, building on SM-05;
- the communication between partitions: E2E or protected RTE buffers rather than shared variables.

Until then AoU-17 applies: the integrator treats all application code at the target ASIL, or partitions it
himself.

*SYS-61:* the coexistence argument per module — which modules are ASIL B (mechanisms, the separating element FreeRTOS
with task layout and interrupt lock, and the drivers the mechanisms call), which QM modules are argued free from
interference and which rows are still open — is in [`coexistence.md`](coexistence.md); its section 7 is the list of
evidence SYS-21 has to produce, and its findings CX-1 … CX-3 are interference paths into the reaction itself (logger
before the safe state, lifecycle shutdown disabling the watchdog, ETL assert reset without the safe outputs table).

### 6.4 MCU periphery, supply and registers

SM-18, SM-19, SM-20, SM-21, SM-22 and SM-59 (*not credited*) - section 6.4 of [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc).

Note on SM-21/SM-22 (*brought to the state of SYS-24 and SYS-153 in issue 3*, proposal C11, CM-07 decision ZJ;
issue 2 said they were still the OpenBSW demonstration with one locked and one checked pin): the pattern of the
OpenBSW demonstration stays - lock what must not move, check the rest cyclically - but the lists are **generated from
the configuration** since SYS-24 (`tools/bswgen`, test `bswgen-safe-io`): the lock list holds the pins of the used
PWM groups, every `safe_off` output and the role pin (class `PIN_FUNCTION`), the check list the classes
`PIN_FUNCTION`, `GPIO_DIRECTION`, `CLOCK_GATE` and `PWM_TIMER` (27 entries on the demo, board check 2026-09-22) and,
since SYS-153, `SUPERVISION` (`PMC_LVDSC1`, the LVD reset of SM-59). A deviation raises the event with class and index
as context. Still missing is the class `SYSTEM_CLOCK` (the SCG configuration, HSI H5): **finding F4 stays open until
SYS-55** (anomaly AN-004). The re-rating of the coverage class of SM-21/SM-22 in [`fmea.md`](fmea.md) (proposal M7 of
the gap analysis) belongs to the review of the failure analysis CM-02.

Note on SM-22's reaction: `CHECK_REGISTER_ERROR` is the only event that enters the safe state without a reset
*and continues running*, and the only one that can leave it again. (Since SYS-23 two further events enter S1
without a reset, but they hold it and stop all other code — S1 held, section 3.) On the eval board it is reachable from the console (`safety dst` /
`safety est`), which is how the board proof of SYS-06 is meant to be run.

Note on SM-20 and SM-59 (SYS-125): SM-20 measures the MCU's **internal** supplies (1.2 V core, 3.3 V flash and
oscillator), not VDD; on a 5 V board it sees a falling VDD only once the internal regulators drop out. SM-59 watches
VDD itself, in hardware, and resets below VLVD. Together they cover a falling supply from the regulator's drop-out
down to VLVD, SM-59 alone below that; the LVR stays as the last stage. SM-59 does not detect a supply that is **too
high**, and it does not replace an external supply supervisor where the integrator's concept needs one (AoU-09).

### 6.5 Outputs and the safe state

SM-23, SM-24, SM-25, SM-26 (*not credited* for release R1), SM-27 (*not credited* for release R1) and SM-58 (*not credited*) - section 6.5 of [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc).

Note on the credit (issue 3, CM-07 decision ZD, F-04): the catalogue states the credit of every stage-1 mechanism
explicitly. Not credited are **SM-26 and SM-27** (selectable safe state and its start test: host verified, board proof
open; section 3), **SM-58** (hardware threshold, optional; review SYS-86 H3), **SM-59** (LVD reset, section 6.4) and
**SM-60** (lock of the FlexNVM block, section 6.2); a requirement may specify none of them (the trace check refuses
it). SSR-089 and SSR-092 therefore specify SM-23, whose walk and locks they extend, SSR-091 the device report path
SM-34, and SSR-090 (the configuration check) no mechanism. Issue 2 credited SM-26/SM-27 in the catalogue while the
safety manual did not.

Note: **one list, not two.** The question "which registers switch the energy off?" has exactly one right answer
per project, and it is already derived from the module instances. A second, hand-maintained list inside the safety
code would drift apart at the next new power output — unnoticed, because the safe state is rarely entered. The
price is that the table semantics exist twice in code (once in assembler for handlers that must not touch the
stack, once in C for handlers that return); the host test of the debug-halt guard and the disassembly test
`safe-outputs-order` check the two against each other.

### 6.6 Data, communication and the analogue path

SM-30, SM-31, SM-32, SM-33 (*device-dependent: described, not credited in stage 1*) and SM-34 - section 6.6 of [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc).

Note on SM-30/SM-31: neither of them decides anything on its own. That is correct for a BSW — the meaning of a
stale wheel-speed signal is an application question — but it means the **safety argument for these two is only
complete together with the application**, and the integrator carries that part (AoU-11, AoU-12).

### 6.7 Start-up, reset and ECU state

SM-50, SM-51, SM-52 (*device-dependent: described, not credited in stage 1*) and SM-53 - section 6.7 of [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc).

### 6.8 Device drivers — moved to the later stage (section 13)

Issue 1 listed the diagnostics of the four device drivers here, as SM-40 (TMC6200), SM-41 (MC33771C), SM-42
(UJA113x) and SM-43 (BTS5030). Under the strict separation of `abn-sys19` they are **not** mechanisms of the base
software and are not credited in stage 1. Their description, unchanged in substance and with the same numbers, is
now in [section 13](#13-later-stage-encapsulated-safety-packages-of-the-device-drivers), together with the
interface through which stage 2 reports into the base software.

What stays in stage 1 from the device side is only what needs no driver code to take effect: the `safe_off`
declaration of a driver's power output in its module manifest (TMC6200 `drv_en`, BTS5030 `in`), which puts the
output into the safe outputs table (SM-23) and forces an adapter at build time (SM-24).

---

## 7. Timing: what the element guarantees, and what the integrator must check

### 7.1 Where the times come from

Only four numbers set the times, and everything else follows from them. *Corrected in issue 2:* issue 1 called
the safety task period a project value; SYS-23 found that **all of them are code constants**, none is a board or
project value. *Corrected by DOC-48 (that was the state of issue 2):* since SYS-25 (2026-09-22) the safety task
period and the watchdog time-out are **module parameters of the project** and the service interval is derived from
them by `nxconfig`, as the table shows; only the halt deadman values are still constants of the family port (AoU-08):

| Value | Where it is set | Today (demo project) |
|---|---|---|
| Safety task period | `project.yaml` `modules.safeLifecycle.task_period_ms` (since SYS-25; generated into `integration/safeLifecycleConfiguration.h`, read by `SafetySystem.cpp` and `SafetyManager.h`) | **10 ms** |
| Watchdog service interval | derived by `nxconfig` at the fastest LPO of the data sheet: `SafetyManager::WATCHDOG_CYCLIC_COUNTER` = floor(`watchdog_timeout_ms` × 1000 / (3 × period × `lpo_max_per_mille`)) = 7 safety cycles | **70 ms** |
| MCU watchdog time-out | `project.yaml` `modules.safeLifecycle.watchdog_timeout_ms` (since SYS-25, read by `SafeWatchdog::WATCHDOG_TIMEOUT_MS`) | **250 ms** |
| Halt deadman time-out / poll | constants of the family port (IO-32, decision 9: deliberately not a configuration value yet) | **10 ms / 1 ms** |

The relation that must hold is `3 × service interval ≤ time-out`: three service opportunities before the watchdog
fires (3 × 7 × 10 = 210 ≤ 230.2 ms, the 250 ms time-out at the fastest LPO, 139 kHz). **Since SYS-53** the relation is computed at the fastest LPO of the derivative (mcudb `clock.lpo`, decision sys25-lpo); before, 3 × 8 × 10 = 240 ms held only at the nominal LPO. **Since SYS-25 the interval is derived**
and the relation is checked again (`tools/nxconfig/checks.py`, `safety_timing`, with the SYS-23 check
`watchdog_timing_messages` as a cross-check); every message names the YAML path `modules.safeLifecycle.<value>`
and stops the configure run (test `nxconfig-watchdog`). The hardware limit of the S32K1 watchdog (16-bit time-out
value at 128 kHz) is checked too: at most 511 ms.

### 7.2 Worst-case time to the reaction, per class

The last column is the **maximum fault handling time interval (FHTI)** of the element: detection plus reaction until
the outputs are de-energised (proposal C7; issue 2 called it "total, worst case"). The integrator compares it with
the fault tolerant time interval (FTTI) of his goal (AoU-05). Every time says whether it is a constant, derived or
measured; the derived ones follow from the four values of section 7.1 and hold for the defaults of the demo project.

| Class | Mechanisms | Detection latency | Reaction | FHTI (worst case to de-energised) |
|---|---|---|---|---|
| Cyclic checks in the safety task | SM-02…SM-04, SM-11…SM-13, SM-15, SM-16, SM-18, SM-20, SM-22 | ≤ 1 safety cycle = **10 ms** nominal (plus whatever preemption the integrator's task set allows); *since SYS-40* guaranteed **17 ms** while the task runs — a later cycle raises SM-04 before its checks ([`fmea.md`](fmea.md) section 2; derived from the period and the configured lateness) | safe outputs walk (< 1 µs computed; board bound ≤ 21 µs, [`fmea.md`](fmea.md) 8.1 B3) and, for most, a software reset | **17 ms** guaranteed (10 ms nominal) plus the walk, to de-energised; the restart time after the reset is measured on the eval board: up to 1 077 ms through the watchdog path and up to 858 ms through an MPU fault, the stated values; with bootloader 0.3.9 MPU 699.7 ms and watchdog 883 … 933 ms ([`fmea.md`](fmea.md) 8.1 B2, [`safety-manual.md`](safety-manual.md) section 4). *Issue 3 (F-07):* issue 2 said "≈ 10 ms" here |
| Repeated-failure limits | SM-19 | 10 consecutive cycles = **100 ms** nominal, 170 ms guaranteed (derived; safety manual section 5) | as above | **170 ms** guaranteed plus the walk |
| Supply multiplexer round | SM-20 | full round = 6 cycles = **60 ms** per supply nominal, 102 ms guaranteed (derived; safety manual section 5) | as above | **102 ms** guaranteed plus the walk |
| Loss of program execution | SM-01 | up to **250 ms** from the last successful service at the nominal LPO; **230 … 283 ms** over the LPO range of the derivative (derived, [`hsi-s32k1xx.md`](hsi-s32k1xx.md) `WDOG_TOVAL`) | warning interrupt walks the table, then hardware reset | **≤ 283 ms** (derived) |
| Stopped time base of the supervision: OpenBSW system time stands still | SM-06, SM-01 (the period check SM-04 does not run: the safety task is scheduled on this time base) | the halt deadman's counter does not depend on the system time: **10 ms** (constant of the family port) | SM-06 walks the table; the reset follows through SM-01 | outputs off after about **10 ms** (SM-06); reset after about **160 … 283 ms** — **derived**: the watchdog time-out over the LPO range (230 … 283 ms) minus at most one service interval (70 ms); board 2026-10-03/04 ([`dfa.md`](dfa.md) DFA-6, safety manual section 5) |
| Stopped time base of the supervision: LPIT clock gated | SM-17 | the next access to the LPIT is a bus fault: within one poll of the halt guard, about **1 ms** (derived from the poll period) | fault handler walks the table, dump, reset | about **1 ms** plus the walk (derived; board 2026-10-03) |
| Stopped time base of the supervision: LPIT clock too slow | SM-06 progress check (DFA-2) | within a few safety cycles (two LPIT ticks of budget per cycle, derived from the clock plan) | S1 + S2 | a few safety cycles plus the walk (board 2026-10-04) |
| Stopped time base of the supervision: **SIRC lost while the core runs** — residual risk | none in time; SM-01 by its hardware reset | SM-06 runs on the same clock and stops; no log line, no walk was observed | hardware reset of the MCU watchdog (outputs go to their reset state, AoU-07) | **not shown** to be de-energised before the watchdog reset, **at most about 283 ms** (derived from the watchdog time-out; board 2026-10-03/04). Residual risk **DF-05** of [`dfa.md`](dfa.md), investigation and architecture with **SYS-195** after release R1; the integrator takes this time for his FTTI or provides an independent shut-off path (AoU-13). *Issue 3 (F-06):* issue 2 had no row for the faults of the time bases |
| Core halted or stalled | SM-06 | **10 ms** | exception entry + table walk on the first instruction after the core runs again | 10 ms + < 1 µs (**computed at 80 MHz**); on the board the walk from the timer to the gate-driver enable is bounded at **≤ 21 µs** (oscilloscope 2026-09-22, [`fmea.md`](fmea.md) 8.1 B3, AN-008); the < 1 µs is not resolved by that measurement |
| Fault exception | SM-17 | immediate | safe outputs walk as the first action, before the dump | exception entry + walk |
| Faults a reset cannot remove | SM-07, SM-14 | at start-up (SM-07); SM-14 one CRC run in the idle context after lifecycle level 2 (SYS-38), then once per `modules.safeMemory.rom_check_period_ms` (SYS-44; a corruption during operation within one period plus one pass) | S1 held: table walk, then the hold without reset (section 3) | table walk as above; the ECU stays de-energised until a power cycle |
| Device diagnostics — **stage 2, not credited** (section 13) | SM-40, SM-41, SM-43 | the part reacts in **µs** by itself; the software learns of it after **one driver cycle** (10 ms, 20 ms for the cell monitor) | latched fault, output off, no automatic retry | part: µs; software: one driver cycle |
| Timing violation of a runnable | SM-05 | end of the offending cycle | **log only** | no de-energising reaction |
| Degraded reaction (SYS-117) | the event's own detection: `DEVICE_FAULT` (SM-34) and, since SYS-135, the trip of a hardware shut-off path (`HW_SHUTOFF`, SM-58, not credited) | as the mechanism that raises the event | walk of the mapped groups' entries of the table (as SM-23, fewer entries), no reset; walked again every safety cycle | as S1 for the mapped groups; the rest keeps running. After a safety reset the groups are forced before the BSP starts; the PWM driver's initialisation releases them until the first safety cycle (**≤ 1 safety cycle**, fmea.md SM-23) |
| Selected safe state (SYS-116) | the path's own trigger (every row above that walks the table) | as that path | the same walk with one read of the selection word per entry of a group; short pattern or freewheel | as that path; every reset ends the short (board reset state, AoU-33). Accepted by `nxconfig` since SYS-139/SYS-140 (host verified); board proof open; SM-26/SM-27 not credited for release R1 (section 3) |

### 7.3 What this means for the integrator

The element does **not** define a fault tolerant time interval (FTTI) — it cannot, because the FTTI belongs to the
item it does not know (AoU-01). What it offers instead is the maximum fault handling time interval (FHTI) per class in
the table above (proposal C7). The integrator's check is:

> For every safety goal, and for every mechanism this concept lists as contributing to it: is the FTTI of that goal
> **longer** than the FHTI in section 7.2 (AoU-05)? The rows of the time bases count as well — in particular the
> residual risk of a lost SIRC (DF-05).

Two answers are easy to get wrong and are called out:

- **A reset is not instantaneous.** The de-energising happens in microseconds, but the ECU is then out of service
  for the restart time — bootloader check, clock set-up, start-up of the lifecycle levels. That time is measured
  on the eval board (section 7.2, F6; [`safety-manual.md`](safety-manual.md) section 4 states the values to use).
  If the item's safe state requires the ECU to be *back*, this time counts (finding F6, AoU-05).
- **10 ms is the floor for anything in the safety task, not a guarantee.** Preemption by higher-priority tasks and
  interrupts adds to it, and the schedule analysis (`tools/schedana`) is where that must be shown. Whether the
  safety task is actually the right priority in a given project is an integrator decision (AoU-08).

---

## 8. Demarcation: software we use and did not develop for safety

Nothing in this section is a criticism of the projects named. They are used because they are good; they were just
not built to be evidence in a safety argument.

### 8.1 Eclipse OpenBSW

- **What it is here.** An unchanged submodule (`v0.1.0-698-g3b6e7021`, 2026-09-14; issue 2 named
  `v0.1.0-677-g00e3cab1`, updated by DOC-48 from `git describe` of the pinned submodule) providing `async`, `lifecycle`,
  `cpp2can`, `docan`, `transport`, `uds`, `logger`, console, `time`, `util`, `storage`, and the safety libraries
  `libs/safety/safeMonitor` and `libs/safety/safeUtils`.
- **The honest statement.** A large part of our safety code is a **copy of OpenBSW's reference-application safety
  demonstration**, tracked as such in `tools/openbswsync/copies.yaml`: `safeSupervisor`, `safeWatchdog`,
  `safeLifecycle`, `safeMemory`, `safeIo`, `safeSystem` are all listed there as `identical` or `modified`. A
  demonstration is what it says: it shows the pattern - upstream with one locked pin and one checked pin. We have
  extended it where the work required it — the safe state is now the project's power outputs (SYS-06), the
  reference band comes from the configuration (ARCH-32), the MPU check knows the derivative's region count
  (ARCH-16), the RTE reports timing (SYS-05), and since SYS-24 and SYS-153 the register lock and check lists of
  SM-21/SM-22 are generated from the configuration (section 6.4; *issue 3*, issue 2 still said "one locked pin and
  one checked pin" here) — but the skeleton is a demo's skeleton.
- **No qualification claim.** OpenBSW is developed under Apache-2.0 as open source; it carries no safety
  qualification, and the safety demo is documented as a demo.
- **Qualification path.** Three options, in increasing cost: (a) treat the OpenBSW parts we use as third-party
  software and qualify them ourselves — argue from the field use, from our own tests and from the analysis of
  PLAT-74, which requires our test coverage on those files to be real (today the OpenBSW submodule is excluded
  from our coverage measurement, by design); (b) reduce the dependency by re-implementing the few pieces the
  safety path actually needs (`safeMonitor` is small); (c) replace it. Option (a) is the realistic one and its
  scope is a task SYS-20 should size. The `copies.yaml` mechanism already tells us exactly which files are ours
  and which are upstream's — that list is the input.
- **The path split by kind of part (proposal C12, issue 3).** The decision is taken in
  [`component-qualification.md`](component-qualification.md) (SYS-62): **unchanged** OpenBSW parts that serve a
  safety requirement are pre-existing software components and are qualified (8-12; the plan is its section 4, the
  first report QR-01 for `safeMonitor` its section 5); **modified copies** cannot be qualified as components — they are
  developed as our own code (6, in the reading of 10-9.1), and the static analysis gates them like our own code
  (its section 3, anomaly AN-031).

### 8.2 FreeRTOS

- **What it is here.** FreeRTOS **V10.6.2** from `external/openbsw/libs/3rdparty/freeRtos`, through OpenBSW's
  `asyncFreeRtos` layer, with the port selected per architecture
  (`platforms/common/3rdparty/freertosPort`, ports per family).
- **No qualification claim.** The kernel in this form carries none. The safety-relevant properties we rely on —
  that a task runs at its period, that priorities are honoured, that a critical section really masks — are
  assumed, tested by our own tests, and observed on the board. They are not proven.
- **Qualification path.**
  - **SAFERTOS** (WITTENSTEIN high integrity systems) is the obvious candidate: the same programming model, and it
    is offered with a certification package for automotive and industrial use. *The exact scope of that package
    and which of our ports and derivatives it covers must be checked with the supplier — it is not verified here.*
    The migration cost is not the API (it is close) but OpenBSW's `asyncFreeRtos` layer and the two RTOS ports we
    maintain. It is a commercial licence, which conflicts with the open-source branch (PLAT-46) — a product using
    it would be a licensed branch.
  - **Qualifying FreeRTOS ourselves** is possible in principle for ASIL B: a validation argument backed by the
    kernel's field use plus our own tests against the properties we depend on. It is a large piece of work and it
    has to be redone at each kernel update.
  - **ThreadX** is already a second RTOS in the build matrix (`target-buildtest-s32k148-threadx`) and, as Eclipse
    ThreadX, has a certification history of its own. Same caveat: the scope must be checked, not assumed.
  - **No RTOS at all.** A single-loop variant for small safety functions is a real option that the architecture
    does not forbid, and it would remove the largest single qualification item. It is not on the roadmap today.
- **Route decided (proposal C13, issue 3).** The user decided on 2026-09-22 (`rtos-u1-anfrage`) to stay on FreeRTOS
  and qualify it as a pre-existing software component (8-12: requirements-based verification including the failure
  behaviour; structural coverage of the component is expected only at ASIL D); "proven in use" (8-14) is not a
  realistic route, because it needs field data we do not have. The kernel is also the element that implements the
  partitioning of SYS-21 and is then ASIL B itself (6-7.4.9). The plan of the package, including the kernel internals
  of OpenBSW's async layer, is [`component-qualification.md`](component-qualification.md) section 4.3 (task SYS-124,
  anomaly AN-057).

### 8.3 Compiler, run-time and libraries

- **What it is here.** `arm-none-eabi-gcc` **11.3.1**, the xPack build pinned in `tools/ci/versions.toml` and set
  up by `python -m tools.ci setup` (or taken from `NX_ARM_GCC_BIN`), C++17, `-Os`/`-Werror`, with newlib (stubs in
  `platforms/common/newlib`), ETL, and the CMSIS core headers. *Issue 3 (F-07):* issues 1 and 2 named GCC 11.4 from
  the NXP S32DS installation, which is no longer installed ([`dev-environment.md`](dev-environment.md) DE1).
- **Components and tools (proposal C14, issue 3).** The libraries linked into the image (newlib, ETL, the CMSIS
  headers) are pre-existing software components (8-12), routed in
  [`component-qualification.md`](component-qualification.md) section 2; the compiler and the generators are tools
  (8-11), classified in [`tool-classification.md`](tool-classification.md).
- **No qualification claim.** GCC is not a qualified compiler, the C library is not a qualified library, and the
  standard C++ library features we use are whatever GCC 11 ships.
- **Qualification path.** Two directions.
  - **A qualified toolchain.** Commercial compilers are offered with functional-safety packages (for example
    IAR's functional-safety edition, Green Hills, TASKING). *Whether any of them covers our core profiles and what
    the package contains has not been checked; naming them is a starting point, not a recommendation.* Cost is a
    licence plus a port of the build (ours is CMake with GCC-specific flags; the linker scripts and the startup
    assembler are the awkward part).
  - **Qualifying the toolchain we have.** The usual shape is a validation suite that exercises the language
    constructs we actually use, plus the argument that our own tests would catch a mis-compilation. Commercial
    test suites for this exist (Solid Sands' SuperTest is the one usually named). *Not evaluated here.* The
    cheaper partial measure that costs us nothing and is already half in place: the **back-to-back checks against
    the target code** that this project already does in places — the disassembly test `safe-outputs-order` proves
    that the compiled watchdog vector really walks the table before it calls anything; the ELF checks of SYS-18
    prove the static constructor is in `.init_array`. Extending that pattern to the other mechanisms is a
    mitigation the integrator can see and check, and it belongs in PLAT-75.
- **Host test toolchain.** The host tests are built with `zig cc` (clang 21.1.0) and the coverage is measured
  through gcov with a small runtime of our own (TOOL-40). The **test** toolchain is a different compiler from the
  **target** toolchain. That is a strength (two front-ends see the same code) and a weakness (a host test proves
  the logic, not the emitted target code). The disassembly tests above are the bridge.

### 8.4 Tools and generated code

`nxconfig`, `bswgen` (with `blgen`), `rtegen`, `dbcgen`, `schedana` and the Studio produce or check parts of
the image, including safety-relevant parts: the safe outputs table, the MPU region count and the RAM layout (the
MPU region descriptors themselves are computed at run time from them), the ADC reference band, the interrupt
priorities, the task list and the deadlines of SM-05. *Corrected by DOC-48* after TF2 and TF3 of
[`tool-classification.md`](tool-classification.md): issue 2 named generated MPU descriptors and a tool `intgen`,
which does not exist. A wrong generator output is a fault
mode of the element.

The classification of each tool by how much it can damage the product and how likely we are to notice — and the
safeguards that follow from it — is **PLAT-75**. What already exists and should be counted there:

- generated output compared against checked-in reference output, with the generators' own test profiles rather
  than the eval board (`tools/test -k bswgen`, `-k rtegen`, `-k nxconfig`, and others); the reference outputs exist
  since PLAT-78 (`bswgen-reference`, `rtegen-reference`; issue 2 claimed them before they existed, TF1);
- validation with severities and a YAML path for every message (nxconfig), so that a wrong configuration is
  rejected rather than silently generated;
- checks that run against the **target artefact**, not the generator: `safe-outputs-order` (disassembly),
  `application-linker`, the ELF checks of SYS-18;
- deliberate fault injection: several tests are checked for meaningfulness by breaking the production code and
  confirming the test goes red (SYS-05: three mutants; SYS-06: four; IO-32: seven; SYS-18: one; SYS-23: ten for
  the supervisor, five for the watchdog relation check);
- **static analysis** (PLAT-74, [`static-analysis.md`](static-analysis.md)): cppcheck with the MISRA C:2012 add-on
  and clang-tidy over the compile database of the target build, gating in the CI (a new finding fails the run),
  OpenBSW copies reported separately via `copies.yaml`. It is a development-process measure, not a mechanism — it
  has no SM number. Its limits are part of the argument: only the demo profile is analysed, MISRA C++:2023 is not
  checked by any free tool, the deviations are approved by the user (DEV-001 … DEV-014 on 2026-09-21, decision
  board `plat74-abweichungen`, entered by PLAT-76; DEV-015 … DEV-024 on 2026-09-30 and 2026-10-01, each with its
  `approved_by` in `tools/misra/deviations.yaml`), and the three real faults it found are
  tasks of their own (RTE-19, SYS-26, IO-39 — the latter two touch SM-53 and SM-06). The integrator's side is
  AoU-22. The classification of cppcheck and clang-tidy themselves belongs to PLAT-75.

### 8.5 Summary of the demarcation

| Part | Version in use | Qualification today | Path named |
|---|---|---|---|
| Eclipse OpenBSW (incl. the safety demo we copied) | `v0.1.0-698-g3b6e7021` | none | unchanged parts qualified as components (8-12), modified copies developed as our own code; `copies.yaml` is the list ([`component-qualification.md`](component-qualification.md)) |
| FreeRTOS | V10.6.2 | none | qualification as a component (decision `rtos-u1-anfrage`, SYS-124); SAFERTOS or ThreadX with a certification package, or no RTOS, stay alternatives |
| arm-none-eabi-gcc (xPack) | 11.3.1 | none | a tool (8-11, PLAT-75): qualified toolchain, or validation suite, plus back-to-back checks against the target code |
| newlib, ETL, CMSIS headers | with the compiler; ETL 20.49.0; CMSIS-Core 6.1 | none | components (8-12), the used surface listed and qualified or replaced ([`component-qualification.md`](component-qualification.md) PC-09 … PC-11) |
| zig cc / clang (host tests only) | 21.1.0 | not applicable to the product | tool qualification question only (PLAT-75) |
| neXtBSW generators | this repository | none | PLAT-75 |

---

## 9. Findings and gaps

### 9.1 Findings in the code, from writing this concept

The findings of issue 1 are kept with their text; the last column records what happened since. The entries "State
2026-10-04 (DOC-48)" are taken from the anomaly register ([`anomaly-register.md`](anomaly-register.md), AN-001 …
AN-016), `doc/todo.yaml` and the board records named; they record evidence, not a verdict.

| ID | Finding (issue 1) | Why it matters | Suggested owner | Status (issue 2, with the states of DOC-48 and issue 3) |
|---|---|---|---|---|
| **F1** | `WATCHDOG_STARTUP_CHECK_FAILURE` and `ROM_CHECK_FAILURE` are logged and then **`return`** — no limp home, no safe state, no reset. Every other non-benign event ends in reset | These are two of the strongest statements the element can make ("the watchdog hardware does not work", "the program image is not the one that was linked") and today they change nothing | SYS-20 decides the reaction; the change is a few lines in `SafeSupervisor.cpp` | **addressed by SYS-23** (host-verified: `safe-supervisor`, `safe-supervisor-bare`): both events go to S1 held (section 3). Decided: `sys23-halt` (a), 2026-09-21. Open: board proof. Since SYS-38 `ROM_CHECK_FAILURE` is reachable (SM-14 active; also raised for an implausible checksum list) **State 2026-10-04 (DOC-48):** board-proven on 2026-09-21: the watchdog self-test failure leads into S1 held (`tools/boardtest/captures/evalboard_s32k144_demo/2026-09-21_board/27_sys23_wdg_startup_fail.txt`, `28_sys23_wdg_startup_fail.txt`, `29_sys23_after_power_cycle.console.txt`), the ROM path with SYS-38 (`46_sys38_romfail.console.txt`); anomaly register AN-001 `closed` |
| **F2** | Nothing checks `3 × watchdog service interval ≤ watchdog time-out`. The three values live in three different files, one of them a project value | Changing the safety task period in a project silently breaks the watchdog relation in either direction | SYS-22 as a requirement, `nxconfig` as the check | **check exists** since SYS-23 (`nxconfig-watchdog`). Correction: none of the three values is a project value, all are code constants (section 7.1). Moving them into a configuration level is **SYS-25**; SYS-22 still takes the relation as a requirement **State 2026-10-04 (DOC-48):** SYS-25 done: period and time-out are module parameters of the project, the cycle count is derived (section 7.1); board short check 2026-09-22 (`tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/`); AN-002 `closed` |
| **F3** | The ROM check (SM-14) runs the whole CRC in one go from the idle path; its duration has never been measured, and there is no periodic re-check | An unbounded run in idle is a timing risk, and a once-per-request check is not a run-time mechanism | SYS-20 | **partly addressed by SYS-38:** the check did not run at all (no caller, no checksum in the HEX, section 6.2); since SYS-38 it runs once per start with a checksum generated and verified in the build (host-verified: `rom-check`, `rom-check-image`; board proof open). Duration on the board not measured yet; bounding in slices and the re-check period stay with SYS-20. **Decided by SYS-20** ([`fmea.md`](fmea.md) 5.1): no slicing while the check runs at idle priority without a lock; duration computed at about 3 … 6 ms (board measurement open); re-check period, known-answer test of the CRC unit and suspension during flash writes proposed as a task (T5). **T5 done by SYS-44** (host verified with mutants: `rom-check`, `rom-check-mutations`, `nvm-flash-write-bracket`): period `modules.safeMemory.rom_check_period_ms`, known-answer test before every pass, suspension during flash writes with a deadline of one period. Board proof of the pass duration and the re-check open (fmea.md B1, B9) |
| **F4** | The register lock and register check (SM-21/SM-22) still cover **one pin each** — the OpenBSW demo scope | The registers an integrator depends on (clock, pin mux of the PWM pins, FTM configuration) are not checked at all | SYS-20 scopes it, then a task of its own: the list must come from the configuration, not from code | unchanged; task **SYS-24** exists. Weighs more now, because ASG-01 relies on base-software mechanisms only **State 2026-10-04 (DOC-48):** SYS-24 done: the register lock list and check list are generated from the configuration (`bswgen-safe-io`, `safe-io`; board check 2026-09-22, `101_sys24_p2_regs.*` … `104_sys24_p5.console.txt` in `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/`); continuation SYS-55 open; AN-004 `in progress` **State issue 3 (CM-07 decision ZJ):** since SYS-153 also the class `SUPERVISION` (`PMC_LVDSC1`); F4 stays **open** until SYS-55 adds `SYSTEM_CLOCK`; the texts of sections 6.4 and 8.1 and of the assumed goals are brought to this state; the re-rating of SM-21/SM-22 in `fmea.md` (M7) belongs to CM-02 |
| **F5** | All four devices sit on SPI; a failure of the SPI master is a **common cause** for three of them at once, and nothing treats it as one event | Each driver latches its own communication fault, which is right, but the common cause is invisible in the analysis | SYS-20 | unchanged. The SPI master is MCU periphery, so its supervision would be a stage-1 mechanism; the device diagnostics that depend on it are stage 2. **Decided by SYS-20** ([`fmea.md`](fmea.md) 5.3): no credited stage-1 mechanism uses SPI, so no separate SPI supervision in stage 1; bus instance in the device context word and correlation per bus go to G5, the dependent-failure analysis to SYS-28, the shared resource to SYS-21 |
| **F6** | The **restart time** after a safety reset has never been measured | Without it, AoU-05 cannot be checked for any goal whose safe state requires the ECU to be available | a board measurement, together with the open board proofs below | unchanged **State 2026-10-04 (DOC-48):** measured on the board (fmea.md 8.1 B2): 1 027 ms (`safety wdg`) and 852 ms (`safety mpu`) on 2026-09-22; re-measured 2026-09-30 (SYS-149) at up to 1 077 / 858 ms, the stated values of safety manual section 4; with bootloader 0.3.9 (2026-10-02/03) MPU 699.7 ms and watchdog 883 … 933 ms (`tools/boardtest/captures/evalboard_s32k144_demo/2026-10-03_board/bl33/`); AN-006 `review`, SYS-45 open |
| **F7** | `EN_SW_KL30B` is still missing from the safe outputs table | The high-side switch stays energised in the safe state | **IO-37**, already planned | **addressed by IO-37** (host-verified): `connect.in` of `libs/bsw/highSideSwitch/module.yaml` is `safe_off: true`, the adapter exists. Board proof outstanding **State 2026-10-04 (DOC-48):** board-proven on 2026-09-21 after the fix of SYS-36: 0 A on KL30B for 6 s after `safety dst`, `kl30b on` refused (`tools/boardtest/captures/evalboard_s32k144_demo/2026-09-21_board/22_sys36_dst_1A.console.txt`, `23_sys36_est_on_refused.console.txt`); AN-007 `closed` |
| **F8** | SM-23's walk duration is computed (≈ 30 instructions at 80 MHz), never measured on a board | It is the number the whole power-stage argument rests on | the IO-32 / SYS-06 board proofs | unchanged **State 2026-10-04 (DOC-48):** board bound **≤ 21 µs** from the timer to the gate-driver enable (oscilloscope 2026-09-22, fmea.md 8.1 B3); the computed < 1 µs is not resolved by it; AN-008 `review`, SYS-45 open |
| **F9** | *SYS-20:* the safety task's own period is not supervised; the watchdog fires only above 31.25 ms per cycle | every "10 ms" in section 7.2 is nominal, 31.25 ms is guaranteed | proposed task T1 ([`fmea.md`](fmea.md) section 7) | **addressed by SYS-40** (SM-04, host verified): 17 ms guaranteed while the task runs; open: board checks B4/B7 and the hardware deadline T1b |
| **F10** | *SYS-20:* RTE task activation (starvation, late start) is not supervised; SM-05 measures durations only | temporal interference goes unnoticed | SYS-21 | new **State 2026-10-04 (DOC-48):** unchanged; SYS-21 open; AN-010 `open` |
| **F11** | *SYS-20:* ECC poll incomplete (flags never cleared, `SR0` without `CR0` enables unconfirmed, one event for RAM and flash, no start-up RAM test) | SM-15/SM-16 rest on unconfirmed assumptions | proposed task T2 | new **State 2026-10-04 (DOC-48):** SYS-41 done (host: flags cleared before the report, source in the context, `safe-memory-ecc`); start-up latent-fault test of the RAM ECC through the EIM by SYS-73 (SM-55, done 2026-09-29); AN-011 `review` |
| **F12** | *SYS-20:* configuration checks of watchdog (`INT`, `WIN`, `PRES`, `UPDATE`) and deadman (`MIER`, NVIC, `TVAL`, `DBG_EN`) incomplete | one bit flip removes a mechanism silently | proposed task T2 | new **State 2026-10-04 (DOC-48):** SYS-41 (watchdog) and SYS-48 (deadman, target binding) done; both board-proven by the fault injection of SYS-51; AN-012 `closed` |
| **F13** | *SYS-20:* no limit on repeated safety resets in stage 1 (SM-52 is device-dependent) | a permanent fault restarts the ECU endlessly, the application runs at every restart | proposed task T4, decision board first | **addressed by SYS-43** (decision `sys43-resetschleife`): count of consecutive safety resets in no-init RAM, S1 held without reset from the N-th on, cleared by power-on and fault-free time (`fmea.md` section 3.6 under SM-50); a watchdog reason counts only alone, because a debugger reset reports RESET | WATCHDOG (board 2026-09-22) and must not count; board checks open **State 2026-10-04 (DOC-48):** SYS-53 made the limits module parameters; board B10/B11 passed; AN-013 `closed` |
| **F14** | *SYS-20:* SM-20 skips failed readings of the supply channel without a limit | supply supervision can vanish without an event | proposed task T2 | new **State 2026-10-04 (DOC-48):** SYS-41 done: limit of failed readings of the supply channel (`safe-system`, host with a built-in fault); no board injection recorded; AN-014 `review` |
| **F15** | *SYS-20:* `safeWatchdog`, `SafetyManager`, the ECC part of `safeMemory` and `SafeIo` lock/check have no host test | evidence for SM-02, SM-03, SM-11, SM-15, SM-16, SM-21 is review only | proposed task T3 | **closed** by SYS-41 (`safe-watchdog`, `safety-cycle`, `safe-memory-ecc`) and SYS-42 (`safety-gate` for SM-11, `safe-io` for SM-21/SM-22, each with built-in faults); SYS-42 also corrected the read-back of SM-21 (lock bit, [`fmea.md`](fmea.md) SM-21) **State 2026-10-04 (DOC-48):** board check B8 passed (`tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/02_sys42_b8_jlink.txt`); AN-015 `closed` |
| **F16** | *SYS-20:* SM-18's tolerance band is a silent gain error on every analogue value | integrator gets no warning if his accuracy need is tighter | proposed AoU-24 | new **State 2026-10-04 (DOC-48):** see AN-016 of the anomaly register; the status of AoU-24 is left to the confirmation review CM-07 **State issue 3 (CM-07 decision ZG):** AoU-24 is binding, a node of section 4.3 with the status `concept`; AN-016 stays `accepted` (residual: the gain error inside the band) |

### 9.2 Coverage gaps by platform and by module

- **Families other than s32k1xx have no safety port.** `stm32`, `s32k3xx` and `rm4x` get the neutral part only
  (supervisor, events, E2E, ecuState, RTE deadlines). Everything in sections 6.2 and 6.4 is s32k1xx today. The
  mechanism *patterns* were kept family-neutral where it was cheap (the halt deadman is documented with its
  equivalents on S32K3, STM32 and RM4x), but no second family implements them.
- **Test coverage of the safety modules is below the thresholds the project set itself.** TOOL-40 measured
  92.4 % statements / 56.5 % branches overall on 2026-09-21 and proposed 95 % / 70 % for the safety-relevant
  modules. Of those, the branch threshold is met by **none**: `e2e` 58.3 %, `canSystem` 60.0 %,
  `safeSystem` 62.5 %. `ecuState` reads 70.8 % / 50.0 % only because most of its host tests are built by a Python
  script and fall outside the measurement. Of the family safety port, `safeMemory` escapes the measurement the
  same way (its MPU host test is compiled and run by the Python test `bswgen-mpu`), and `safeLifecycle` and
  `safeWatchdog` have **no test entry of their own at all**. `safeSupervisor` gained one with SYS-23
  (`safe-supervisor`, `safe-supervisor-bare`: the real `SafeSupervisor.cpp` against recording stubs, reactions of
  all 16 events), after the TOOL-40 measurement. `safeIo` gained an entry with
  SYS-06 (`safe-state`), after the TOOL-40 measurement was taken, so it is missing from that table but will
  appear in the next run. Closing the rest is part of what makes SYS-20 provable. *State issue 3 (CM-07 decision
  ZK, F-07):* the figures above are those of the TOOL-40 measurement of **2026-09-21** and are not re-measured in
  this issue; the thresholds per module are in `tools/test/coverage.yaml` (TOOL-40 done). Since SYS-41 `safeWatchdog`
  has its test entry `safe-watchdog` and `safeLifecycle` (the `SafetyManager` of the safety cycle) the entries
  `safety-cycle` and `cycle-timer-off`, each with built-in faults (F15 closed); so the sentence "no test entry of
  their own at all" is history.
- **Board proofs** for the mechanisms merged in the night of 2026-09-21: SYS-05 (a load test really
  raises the timing event), SYS-06 (the safe state really de-energises the bridge — `safety dst` / `safety est`
  with an oscilloscope), IO-32 (halt 30 s and 60 s, no square wave after `g`; plus 10 minutes of operation without
  a false trip), SYS-18 (`wake-up sources 0x00000006` after `safety wdg`), and VER-06 (the SBC watchdog in
  operation). *State 2026-10-04 (DOC-48):* SYS-05, SYS-06, IO-32 and SYS-18 passed on the board on 2026-09-21
  (owner lines in `doc/todo.yaml`; records in `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-21_board/`:
  `02_sys18_poweron_baseline.console.txt` … `06_sys18_power_cycle.console.txt` for SYS-18,
  `07_io32_halt_log.console.txt`, `15_sys06_dst.console.txt`, `16_sys06_est.console.txt`,
  `25_sys05_loadtest_log.console.txt`, `26_sys05_rte_runnables.console.txt`, `48_sys05_loadtest.console.txt`,
  `49_sys05_runnables.console.txt`); VER-06 is still open.

### 9.3 Gaps that are already tasks

| Gap | Task |
|---|---|
| No quantified failure analysis, no diagnostic coverage, no evidence table per mechanism | **SYS-20** |
| No partitioning between safety-related and QM software | **SYS-21** |
| No safety requirements and no traceability to code and tests | **SYS-22** |
| No static analysis with a rule set in the CI | **PLAT-74** — delivered ([`static-analysis.md`](static-analysis.md)); DEV-001 … DEV-014 approved by the user on 2026-09-21 and rule 15.5 disapplied (PLAT-76, static-analysis.md section 4a); open: MISRA C++:2023, a second profile |
| Watchdog values are code constants, not a configuration level | **SYS-25** — done (section 7.1) |
| Register lock and check at demo scope (F4) | **SYS-24** — done (register list from the configuration); continued by **SYS-55** (open) |
| Device-driver safety packages and their FMEA (stage 2) | **SYS-28** |
| Base-software coverage gaps G1–G6 made visible by the strict separation | proposed in section 10.9 |
| No tool classification and no systematic safeguard of the generated code | **PLAT-75** |
| Coverage thresholds not enforced, MC/DC not available | **TOOL-40** — done (measurement and thresholds per module); enforcing them with a rationale per shortfall is proposal C17, task **DOC-52** (after release R1) |
| No integrator-facing safety manual | **DOC-19** |
| No non-volatile error memory | **NVM-01 … NVM-03** — done (error memory with DTCs and environment data, NVM-03) |
| `EN_SW_KL30B` not in the safe outputs table | **IO-37** — done; board-proven on 2026-09-21 together with SYS-36 (F7) |

### 9.4 Questions to the standard — answered by the gap analysis

Issues 1 and 2 listed seven questions here that could not be decided without the standard. Since the standard is
available to the user, the gap analysis `iso26262-gap.md` (not published, available on request) section 4 answers each of them in its
own words, citing part and clause only; the full answers are there (proposal C15, CM-07 decision ZH, finding F-05).
In short, and what this concept does with them:

| # | Question of issues 1 and 2 | Answer in short (gap analysis section 4) | Where this concept or its documents carry it |
|---|---|---|---|
| 1 | Are the assumed goals at the right level; what must an assumption of use contain? | for a software SEooC the checkable level is a set of assumed technical safety requirements allocated to the element, each with ASIL, safe state and maximum fault handling time; assumed item goals are useful context (10-9.2.4; 2-6.4.5.7) | ASG-01 … ASG-07 stay as context (section 2.2); the assumed technical safety requirements ATSR-1 … ATSR-6 are in [`safety-manual.md`](safety-manual.md) section 3.2 (C2) |
| 2 | What evidence is expected at ASIL B? | the work products of part 6, part 8 and part 9 and, from part 2, safety plan, safety case and confirmation reviews | the list with its status is the gap analysis section 2; the plan is [`safety-plan.md`](safety-plan.md) (C16) |
| 3 | Can a pure software element claim ASIL B without hardware metrics? | yes, as an ASIL capability (10-9.1); the metrics may stay with the integrator, but the element must deliver per mechanism what his metrics need, and the dependent failures between mechanisms and hardware | AoU-09, AoU-36; per mechanism in the safety manual section 5; [`dfa.md`](dfa.md) |
| 4 | Which non-qualified parts must be qualified, which argued from use? | unchanged pre-existing software that serves a safety requirement is qualified (8-12); modified copies are own development; proven in use (8-14) needs field data; compiler and generators are tools (8-11) | section 8 (C12 … C14), [`component-qualification.md`](component-qualification.md), [`tool-classification.md`](tool-classification.md) |
| 5 | What must freedom from interference show; is an MPU partition on one core enough? | timing and execution, memory, exchange of information; hardware-supported partitioning is required only at ASIL D, but the partitioning software is developed at the highest ASIL of the partitions and its effect shown in integration (6-7.4.9) | section 6.3, [`coexistence.md`](coexistence.md), SYS-21; the structure by the three kinds is C6/C10 (DOC-52, after release R1) |
| 6 | Is "log and continue" (S3) admissible? | only where the condition cannot violate a safety requirement within the FTTI, or the reaction is allocated to another element; a latent fault of a safety mechanism is not only reported | section 3 (S3), AoU-18 (C18); F1 ended in S1 held |
| 7 | Which coverage measure; are the TOOL-40 thresholds right? | statement and branch coverage strongly expected at ASIL B, MC/DC recommended; no number, but a target without rationale counts as insufficient (6-9.4.4) | section 2.1 (C5); enforcing the thresholds with a rationale per shortfall is C17 (DOC-52, after release R1) |

### 9.5 Coverage gaps of the base software made visible by the strict separation (new in issue 2)

When the device diagnostics are no longer counted (section 1.4), some assumed goals lose their support. The
entries below say which base-software mechanism is missing. Each was checked against the code at `fa04ed1`: none
of them exists today, not even in part, unless the entry says so. They are **proposals**, not decisions; section
10.9 turns them into proposed tasks. They follow the user's direction that the base software itself implements
"alle notwendigen Sicherheitsmechanismen" for ASIL B.

| ID | Missing base-software mechanism | Goals affected | What exists today (checked in the code) |
|---|---|---|---|
| **G1** | **Verification of the safe state by read-back.** After the safe outputs walk, read back what took effect: the port data/pin level of every `safe_off` output and the FTM software output control of every PWM group, and raise an event if S1 did not take effect (then the reaction can only be S1 held or a reset — the point is that the element *knows*) | ASG-01, ASG-02 | `nxSafeOutputsSwitchOff()` (`platforms/s32k1xx/hardFaultHandler/include/hardFaultHandler/safeOutputs.h`) writes and ends with `dsb`; it reads only the clock gate, never the result. The only read-back of an enable today is the TMC6200 driver's `ENABLE_MISMATCH` through `IOIN` — stage 2. **Closed for S1 on the host by SYS-30:** a read-back entry per table entry (software output control, pin level), read by SafeIo every safety cycle while S1 is latched, `LOCK_REGISTER_ERROR` (S1 + S2) if S1 did not take effect. Open after SYS-30: S1 held, SD, the fault handlers' path, board proof. **Since SYS-138 (host verified, `53dea2fc`):** read back in every round of S1 held, for the groups of SD in every safety cycle, and after the walk of every S1 + S2 sequence of the safe supervisor (`safe-io`, `safe-io-mutations`, `safe-supervisor-degraded`). Open: the hard fault and watchdog handlers and the halt deadman of the family port, board proof (fmea.md B15, B16) |
| **G2** | **Plausibility between two independent measurement paths**, as a configurable base-software service: pairs of signals declared in the project (for example commanded PWM duty or phase state against a measured phase current on an MCU ADC channel; pack voltage on an MCU ADC channel against the sum of cell voltages from a device), a tolerance and a debounce time in physical units, an event to the safe supervisor on violation | ASG-01, ASG-03 | nothing generic. The signal qualifier (SM-30) judges one channel against its own limits; there is no comparison of two sources. Individual drivers check their own inputs (the Hall decoder's sector table, the high-side switch's current levels) — stage 2 or application |
| **G3** | **Age supervision of safety-relevant signals** (freshness): a value that is not refreshed within its configured period becomes invalid, its qualifier is set, and an event can be raised — for signals from on-board devices, not only for CAN messages | ASG-03, ASG-05 | E2E counters (SM-31) cover CAN messages with a configured profile. SM-05 supervises that a *runnable* runs on time, not that the *data* it should deliver is new. A device value that stops updating is not detected by the base software. **Since SYS-32 (host verified):** the RTE sets an age qualifier `age.qlf` per port element whose value is not refreshed within its configured period (`rtegen-age`). Open: the event to the safe supervisor (SYS-99), the board proof with a device package (SYS-28) |
| **G4** | **Safe state for actuators switched by a bus command** (not by a pin): a base-software permission per such actuator, declared in the module manifest like `safe_off`, revoked by every S1 entry (including S1 held and the fault handlers' path, which needs it as a plain flag without driver code), and checked by the driver before it commands the actuator; plus a cyclic read-back through the driver interface that reports a commanded-off actuator that is on | ASG-04, ASG-01 (enable over SPI where a part has one) | nothing. The safe outputs table holds port registers only. In S1 held no task runs, so the part is not commanded any more — whether it then switches off depends on the part (for the MC33771C its communication time-out), which is stage 2 and not credited |
| **G5** | **Base-software side of the device interface** (section 13.1): an event class for device reports with a defined context word, the reaction of each class decided in `SafeSupervisor::handle()`, and the qualifier path for device values into the RTE. Without it, stage 2 has nowhere to report to, and G2–G4 have no uniform way to raise their events | ASG-01, ASG-03, ASG-04 | the supervisor has 16 events, none for devices; no driver includes `SafeSupervisor`. The gate driver's fault word reaches the RTE (`gd.<device>.faults`, IO-25), the cell controller's does not. **Closed on the base-software side by SYS-29 (SM-34, host verified):** `DEVICE_REPORT`/`DEVICE_FAULT` with the context word, reaction per class in `handle()`, correlation per bus (F5), bus verdict in `gd.<device>.qlf`. Open: no driver reports yet (SYS-28), the permission of G4. Since SYS-194 the family port delivers one box: the take-backs of foreign `safe_off` levels by its 1-ms hold (`DEVICE_REPORT`, S3) |
| **G6** | **Run-time ROM check actually active** (SM-14): checksum generated in the build for the application image, the check called from a defined context with a bounded run time per call (areas in slices), and a re-check period, with the reaction of section 3 | ASG-07, ASG-06 | **partly closed by SYS-38:** checksum generated and verified in the build for every application image of the family (build error instead of a missing list), the check called once per start from the idle context (component `romcheck`), an implausible list refused with `ROM_CHECK_FAILURE` (S1 held). Open: bounded run time per call (areas in slices) and the re-check period (F3, SYS-20). **Closed on the host by SYS-44:** slices not needed (fmea.md 5.1, idle priority without a lock); re-check period as module parameter, known-answer test, suspension during flash writes. Open: board proof (fmea.md B1, B9) |

Two things this list deliberately does **not** do. It does not relabel a driver diagnostic as a base-software
mechanism (the TMC6200 `IOIN` read-back stays SM-40, stage 2, even though G1 does something similar from the MCU
side). And it does not claim that closing G1–G6 makes the goals *fully* covered: G2 and G3 detect, but the
comparison of a cell voltage against the safe operating area remains an application function, and without the
device stage the cell values themselves have no diagnostic behind them. For ASG-03 and ASG-04 the base software can
at best provide the frame (freshness, plausibility, permission, report path); AoU-23 carries the rest until stage
2 exists.

### 9.6 Proposals C1 … C18 of the gap analysis — disposition in issue 3 (new in issue 3)

The gap analysis `iso26262-gap.md` (not published, available on request) section 5.1 proposes eighteen changes to this document. The
user decided in CM-07 (decision ZB): the consistency points and short pointers go into issue 3, what is done elsewhere
is noted as done, and three larger points are a task after release R1 (until R1 only what R1 needs).

| # | Proposal (gap analysis 5.1, in short) | Disposition |
|---|---|---|
| C1 | section 0: pointer to the gap analysis instead of "not available"; part and clause allowed | **issue 3**, section 0 |
| C2 | assumed technical safety requirements of the element below the goals | **done elsewhere**: ATSR-1 … ATSR-6 in [`safety-manual.md`](safety-manual.md) section 3.2 (DOC-19); accepted as written in CM-10 (Z1) |
| C3 | external interfaces of the element as part of the assumptions | **done elsewhere**: safety manual section 2, pointer in section 1.2 since DOC-48 |
| C4 | without a coexistence argument all software in the image is ASIL B | **done**: [`coexistence.md`](coexistence.md) per module (SYS-61), AoU-17 rewritten; pointer in section 1.2 (issue 3) |
| C5 | section 2.1: the coverage paragraph | **issue 3**, section 2.1 |
| C6 | sections 2.1, 6.3: hardware-supported partitioning only at ASIL D; the partitioning element at ASIL B | **task DOC-52**, after release R1 |
| C7 | FTTI at item level, FHTI per mechanism; AoU-05 and section 7 reworded | **issue 3**: AoU-01, AoU-05, sections 7.2, 7.3 |
| C8 | new AoUs: no QMS and no independent confirmation; the MCU safety manual; calibration | **done**: AoU-35, AoU-36, AoU-28 (DOC-38) |
| C9 | carry AoU-24 … AoU-28 into section 4 | **issue 3** for AoU-24/AoU-25 (decision ZG); AoU-26 … AoU-28 since DOC-38 |
| C10 | section 6.3: structure freedom from interference by its three kinds and map the mechanisms | **task DOC-52**, after release R1 |
| C11 | section 6.4/9.1: SM-21/SM-22 note and F4 status at the state of SYS-24 | **issue 3**: sections 6.4, 9.1 (F4 stays open until SYS-55) |
| C12 | section 8.1: unchanged OpenBSW parts qualified, modified copies own development | **issue 3**, short pointer to [`component-qualification.md`](component-qualification.md) |
| C13 | section 8.2: FreeRTOS qualification, proven in use not realistic, the kernel as partitioning element | **issue 3**, short pointer (section 8.2) |
| C14 | section 8.3: libraries are components, the compiler a tool | **issue 3**, short pointer (section 8.3) |
| C15 | section 9.4: the answers of the gap analysis | **issue 3**, section 9.4 |
| C16 | a pointer to the lifecycle tailoring of the safety plan | **done**: pointer to [`safety-plan.md`](safety-plan.md) among the related documents since DOC-48 (AN-095) |
| C17 | section 10.6: enforce the coverage thresholds, requirement coverage per unit, host/target difference | **task DOC-52**, after release R1 |
| C18 | the admissibility rule of S3; AoU-18 extended | **issue 3**: section 3 (S3), AoU-18 |

---

## 10. Task breakdown — confirmed or changed

The task cut of `doc/todo.yaml` was reviewed against this concept. **All seven tasks stay; five get additions,
none is removed, and two new tasks are proposed.** The changes are recorded here for the coordinator to carry into
`doc/todo.yaml` (agents must not edit it).

**Issue 2.** Sections 10.1 to 10.8 are the review of issue 1 and stay as the record of what was proposed; SYS-23
and SYS-24 were created from 10.8, SYS-25 and SYS-28 came later. Where the acceptance `abn-sys19` changed a point,
it is marked *Issue 2* in place. Section 10.9 proposes the tasks for the gaps G1–G6.

**State at issue 3 (CM-07 decision ZK, F-07).** Sections 10.1 … 10.9 stay the record of the task review of issues 1
and 2; their figures are of that time. Today: the catalogue runs from **SM-01 to SM-60** (10.1 names "SM-01 … SM-53";
added since: SM-26, SM-27, SM-34, SM-55, SM-58, SM-59, SM-60), section 4 has **41 assumptions** in
[`assumptions.sdoc`](../requirements/safety/assumptions.sdoc) (AoU-01 … AoU-30 and AoU-32 … AoU-42, all binding;
AoU-31 is announced for SYS-115 and not a node; 10.7 names 21 and 23) and the safety manual carries 62 rows with
those of the other analyses; SYS-20, SYS-22, PLAT-74, PLAT-75, TOOL-40 and DOC-19 are **done**, SYS-21 is open; the
state of the gap tasks of 10.9 is in section 9.5 and in the anomaly register (AN-017 … AN-022).

### 10.1 SYS-20 — failure analysis, diagnostic coverage, fault tolerance times — **confirmed, extended**

Stays as scoped: per mechanism, the faults considered, detection, reaction, detection and reaction time against
the fault tolerance time, diagnostic coverage with a justification, and the evidence.

Additions from this concept:

- **Use the mechanism IDs SM-01 … SM-53 of section 6** as the spine of the analysis, so the concept, the analysis,
  the requirements (SYS-22) and the safety manual (DOC-19) all speak about the same objects.
- **Decide findings F1 (no reaction on watchdog self-test and ROM check failure), F3 (unbounded ROM check), F4
  (register checks still at demo scope) and F5 (SPI as a common cause).** Each is either a change with its own
  task or an accepted residual risk with a reason. *Issue 2:* F1 is implemented by SYS-23 (decision `sys23-halt`
  taken: S1 held); F3 now includes that SM-14 does not run at all (G6).
- **Device drivers are blocked on the manufacturer documents.** The analysis of SM-40 … SM-43 must not start
  before the decision-board item `safety-bausteinunterlagen` is answered, and it must not invent coverage figures
  if the answer is "we have nothing". If the answer stays empty, the honest result is: "the part diagnostics are
  described, no coverage is claimed, and the integrator must obtain the documents himself" — which is AoU-21, and
  a legitimate outcome for an out-of-context element. *Issue 2 — superseded:* SYS-20 analyses the **stage-1
  mechanisms only** and is no longer blocked by `safety-bausteinunterlagen`; SM-40 … SM-43 and their documents
  belong to SYS-28 (section 13). The rule "no invented coverage" carries over to SYS-28 unchanged.
- *Issue 2:* **Analyse the goals, not only the mechanisms.** Section 2.2 now says per assumed goal whether stage 1
  covers it. SYS-20 should confirm or correct those verdicts — in particular "partial" for ASG-01 and ASG-07 and
  "not covered" for ASG-03 and ASG-04 — and weigh the gaps G1–G6 against them.
- **Coverage of the safety modules is an input, not an afterthought.** `safeLifecycle`, `safeSupervisor` and
  `safeWatchdog` have no test of their own, `safeMemory` is measured only inside a Python test, and `e2e`,
  `canSystem` and `safeSystem` miss the branch threshold. An analysis that claims a mechanism works needs a test
  that shows it (section 9.2). *Issue 2:* `safeSupervisor` has its own test since SYS-23.

### 10.2 SYS-21 — freedom from interference — **confirmed, scope sharpened**

Stays. Sharpened by section 6.3: what exists today is protection of the **safety manager's** data and stack, not
partitioning of the application. Add to the done-when list:

- the **timing** side explicitly builds on SM-05 (the deadlines already exist; what is missing is a budget per
  partition and a reaction);
- the **SPI common cause** (F5) touches this task too, because several partitions would share that resource;
- the S32K1 has no memory-protection support for more than a handful of regions — the number per derivative is
  already in `mcudb` (`NX_MPU_REGIONS`, used by SM-12), so the partition count is bounded by the derivative and
  that bound belongs in the configuration check.
- *Issue 2 (`abn-sys19`):* the **independence** and **decomposition** arguments and the **MC/DC path** that ASIL C
  and D would need are to be carried as a *later stage* of this task — named in its record, not struck out
  (section 2.1). The partitioning of stage 1 should not be built in a way that blocks them.

### 10.3 SYS-22 — safety requirements with traceability — **confirmed, extended**

Stays as decided (`safety-anforderungen`: YAML in the repository, IDs, link to code through a comment tag and to
tests through `covers` in `tools/test/tests.yaml`, matrix generated and checked in the CI).

Additions:

- The **ID spaces of this concept are the starting content**: `ASG-*` (assumed safety goals), `AoU-*` (assumptions
  of use) and `SM-*` (mechanisms). A requirement should be traceable up to an `ASG-*` or an `AoU-*` and down to a
  test.
- **Finding F2 becomes a requirement with a check**: the watchdog relation `3 × service interval ≤ time-out` has
  to be a validated property of the configuration, not an accident of three constants.
- The tool must also report the reverse direction the concept needs: a **mechanism without a test** and an **AoU
  without a place in the safety manual**.

### 10.4 PLAT-74 — MISRA and static analysis — **confirmed, no change to the scope**

Stays as decided (`safety-analyse`: cppcheck with the MISRA add-on and clang-tidy, free tools; inventory first,
then a justified deviation list, then new findings break the run; OpenBSW copies reported separately).

One addition that costs nothing and helps section 8.1: the separation the task already requires — our code versus
the OpenBSW copies — should use **`tools/openbswsync/copies.yaml`** as its source, because that file already
knows which files are upstream, which are modified copies and why. It is the same list the OpenBSW qualification
argument needs.

Delivered: rule set, tool, inventory and deviation handling in [`static-analysis.md`](static-analysis.md) (PLAT-74).

### 10.5 PLAT-75 — tool classification — **confirmed, priority questioned**

Stays. Two remarks:

- **Priority.** It is P3 today while everything else in this group is P2. Given that the safe outputs table, the
  MPU descriptors, the interrupt priorities and the SM-05 deadlines are all generated, a wrong generator output is
  a direct fault mode of the element. Suggestion to the user: **raise to P2**, after SYS-20.
- **Credit what exists.** Section 8.4 lists the safeguards already in place (reference outputs with own test
  profiles, validation messages with a YAML path, checks against the target artefact, deliberate fault injection).
  PLAT-75 should start from that inventory rather than from zero, and its result should say per tool which of the
  four safeguard kinds applies.

### 10.6 TOOL-40 — coverage — **confirmed, already delivered in part**

Status `review`; the measurement, the CI stage and the proposed thresholds exist since 2026-09-21. From this
concept:

- The threshold list should gain `platforms/s32k1xx/port/safety/safeIo`, which the `safe-state` entry of SYS-06
  now measures but which has no threshold, and it should name the safety modules that are **not measured at all**
  so that their absence is visible rather than silent: `safeLifecycle`, `safeSupervisor` and `safeWatchdog` (no
  test entry of their own) and `safeMemory` (its MPU test runs inside the Python test `bswgen-mpu`, like the
  ecuState tests inside their script). A threshold on a module nobody measures is worth nothing; so is a
  measurement nobody has a threshold for.
- The MC/DC path stays as it is: named, not built, and triggered only by an ASIL above B.

### 10.7 DOC-19 — safety manual — **confirmed, input list fixed**

Stays, and this concept fixes what it takes as input: section 2.2 (assumed goals), section 4 (all 21 AoUs, each to
be turned into "what the integrator does about it"), section 3 (the safe states), section 6 (the mechanisms the
integrator may rely on and what each requires from him), section 7 (the times he must compare with his fault
tolerance time), section 8 (the honest list of non-qualified parts) and section 9 (the gaps he inherits).
*Issue 2:* section 4 now has 23 AoUs (AoU-22, AoU-23 added); section 2.2 states per goal what stage 1 covers, and
the manual must pass that on unchanged — in particular that ASG-03 and ASG-04 are not covered by the base
software (AoU-23). The device packages of section 13 get their own chapter only once SYS-28 exists.

The dependency list `[SYS-19, SYS-20, SYS-21, SYS-22, PLAT-74, TOOL-40]` is right and should gain **PLAT-75**,
because the manual has to tell the integrator what the tool situation is.

### 10.8 Two new tasks proposed

| Proposed | Title | Why |
|---|---|---|
| **SYS-23** | Reaction for the events that have none, and the watchdog timing relation as a checked property | Findings F1 and F2 are small, concrete code and configuration changes with their own tests. Bundling them keeps SYS-20 an analysis task rather than an implementation task. Touches `ecu/safety/safeSupervisor`, `tools/nxconfig` |
| **SYS-24** | Register lock and register check from the configuration instead of one demo pin | Finding F4. The list of registers that must be locked and checked is board and project knowledge and belongs in the configuration, exactly like the safe outputs table. Depends on SYS-20 for the scope |

Not proposed as new tasks, deliberately: the ROM check (F3) and the SPI common cause (F5) — both need the analysis
of SYS-20 before anyone can say what the right change is.


### 10.9 Issue 2: tasks proposed for the coverage gaps G1–G6

For the coordinator to carry into `doc/todo.yaml`; IDs to be assigned there. All are base-software work (stage 1)
and none depends on the manufacturer documents. Suggested priority follows the project's order (safety before
OTA and multi-MCU ECU).

| Gap | Proposed title | Suggested priority, dependencies | Touches (expected) | Why |
|---|---|---|---|---|
| G5 | Device reports to the safe supervisor: event class, context word, reaction in `handle()`, qualifier path of device values into the RTE | P2; after SYS-20 | `ecu/safety/safeSupervisor`, `tools/rtegen`, `libs/bsw` interfaces (no driver logic) | the base-software half of the stage-2 interface (section 13.1); G2–G4 raise their events through it; SYS-28 uses it — **done by SYS-29** (SM-34; touched `ecu/safety/safeSupervisor` and `tools/rtegen`, no `libs/bsw` interface was needed: the context word is a dependency-free header of the supervisor) |
| G1 | Safe state verified by read-back of the `safe_off` outputs and the PWM software output control, event if S1 did not take effect | P2; with SYS-24 (same register knowledge) | `platforms/s32k1xx/hardFaultHandler`, `platforms/s32k1xx/port/safety/safeIo`, `tools/bswgen` (read-back addresses in the generated table) | ASG-01/ASG-02 rest entirely on the base software now; today nothing confirms that the walk took effect — **done for S1 by SYS-30** (host; S1 held, SD and board proof open); **S1 held, SD and the S1 + S2 sequence done by SYS-138** (host); open: fault handlers and halt deadman of the family port, board proof |
| G2 | Plausibility between two independent measurement paths as a configured service (pairs, tolerance and debounce in physical units, event to the supervisor) | P2; after G5 | `libs/bsw` (new module), `tools/rtegen` or `tools/bswgen`, `tools/nxconfig` (validation with YAML path) | the one generic detection the base software can contribute to ASG-01 (command vs. phase current) and ASG-03 (pack voltage vs. sum of cells) without relying on a device diagnostic |
| G3 | Age supervision of safety-relevant signals (freshness per signal from its configured period, qualifier set, optional event) | P2; after G5 | `tools/rtegen`, RTE runtime | closes "stale" for device values (ASG-03, ASG-05); SM-05 watches the runnable, not the data — **task SYS-32 done** (age qualifier in the RTE, host); event to the safe supervisor **SYS-99** open |
| G4 | Safe state for bus-commanded actuators: permission per actuator declared in the manifest, revoked on every S1 entry, checked by the driver; read-back reported | P3; after G5, before SYS-28's balancing part | `ecu/safety/safeSupervisor`, `platforms/s32k1xx/port/safety/safeIo`, `tools/bswgen`, manifest schema | ASG-04 has no stage-1 mechanism at all; S1 does not reach an actuator behind SPI |
| G6 | ROM check (SM-14) active: checksum generated in the build, bounded slices, re-check period, reaction S1 held (`sys23-halt`) | P2; with SYS-20 (F3) | `platforms/s32k1xx/port/safety/safeMemory`, `ecu/application` (idle or safety task, build of the checksum) | build side and activation done by SYS-38 (a missing checksum is a build error, the check is only compiled into an image with checksum); re-check period, known-answer test and suspension during flash writes done by SYS-44 (host), slices not needed (fmea.md 5.1); board proof remains |

Not proposed, deliberately: a separate task for the SPI common cause (F5) — it stays with SYS-20, because the
analysis must first say whether the answer is a supervision of the SPI master (stage 1) or a property of the
device packages (stage 2).

---

## 11. Review questions of the architecture (section 11)

This task changed no reusable code, no generator and no bootloader — it added one document. The questions are
answered for the **concept** rather than for code, i.e. does the concept force anything board-specific into the
architecture. *Issue 2 (SYS-27)* again changes no code; where its additions (stage split, gaps G1–G6) touch a
question, the answer says so. *Issue 3 (DOC-51)* changes no code, no generator and no configuration value either:
it corrects texts after the confirmation review CM-07. Its new statements are board-neutral — the second degradable
case (`HW_SHUTOFF`) exists only where a project configures a hardware threshold, the time-base rows of section 7.2
are derived from the four values of section 7.1 and marked as eval-board measurements where they are, and the credit
of SM-26/SM-27 is a statement of the catalogue, not of a board.

1. **Board without an SBC?** Yes. The concept treats the SBC as one possible provider of the roles `power_supply`
   and `watchdog_trigger` and says so (AoU-13, SM-42, SM-52). A board without one keeps the MCU watchdog (SM-01),
   loses the independent time base and the reset counter, and the concept names that as a difference rather than
   assuming the SBC. *Issue 2:* the SBC-dependent mechanisms (SM-33, SM-42, SM-52) are now explicitly not credited
   in stage 1, so the stage-1 argument is the same with and without an SBC.
2. **Different crystal and system clock?** Yes. No time in this document is written as a frequency. The four
   values that set the times (safety task period, service interval, watchdog time-out, deadman time-out) are
   times — since SYS-25 configured as times in the project, the deadman time-out a constant of the family port
   (section 7.1); the one place a frequency appears
   is the computed walk duration in section 7.2, and it is labelled "at 80 MHz".
3. **Different derivative, and S32K3 / STM32 / Cortex-R not blocked?** Yes, and the concept makes the split
   explicit (section 1.3): neutral mechanisms versus family mechanisms, with the family equivalents of the halt
   deadman named for S32K3, STM32 and RM4x. The derivative-dependent parts already read the derivative database
   (MPU region count, ERM channel count).
4. **Two or more CAN buses and different IDs?** Yes. SM-31 and SM-32 are per bus and per message by construction;
   nothing in the concept assumes one bus.
5. **Several tasks and freely chosen priorities?** Yes — and the concept turns it into an explicit duty
   (AoU-08) and an explicit residual risk (the "10 ms is a floor, not a guarantee" note in section 7.3), instead
   of assuming the demo's task set.
6. **Every new value in exactly one configuration level?** No new configuration value is introduced by this
   document. Where it says a value should become one, it names the level: the register list of F4/SYS-24 is board
   and project data, the watchdog relation of F2 is a check over existing values, not a new value.
   *Issue 2:* the gap proposals name their level too — plausibility pairs, tolerances and debounce times (G2) and
   signal periods for the age supervision (G3) are project data; whether an actuator needs a bus-level permission
   (G4) is declared by the module manifest, like `safe_off`; the read-back addresses of G1 are derived by the
   generator from the same table, not configured.
7. **Host test with its own test profile?** Not applicable — no code. The concept does, however, make the
   coverage of the safety modules a stated gap (section 9.2) and hands it to SYS-20 and TOOL-40.
8. **Tool core free of Qt, validation messages with a YAML path?** Not applicable — no tool changed. Section 8.4
   records that messages with a YAML path are an existing safeguard that PLAT-75 should count.
9. **Device driver granularity and physical units?** Not applicable — no driver changed. Section 13 (6.8 in
   issue 1) describes
   the drivers as they are cut today and states their project parameters in the physical units of the function
   (mA, ms, mV, °C), consistent with `treiber-granularitaet.md`.
10. **Placement under `platforms/`?** Not applicable — the only new file is `doc/safety/concept.md`. The concept
    respects the platform rule in how it talks about the family safety port
    (`platforms/s32k1xx/port/safety/*` today, at the OpenBSW location).

**Board peculiarity forced into the architecture?** None. One is reported instead of built in: the eval board runs
the SBC watchdog **off** (development mode), and rather than treating that as normal, the concept makes it AoU-13
with the sentence that an integrator must not copy it into a product.

---

## 12. Sources

Everything in this document comes from inside the repository. No external document was consulted for issues 1
and 2, and in particular ISO 26262 was not. *Added by DOC-48:* since then the gap analysis
`iso26262-gap.md` (not published, available on request) (SYS-58, 2026-09-22) has compared neXtBSW with the standard, which is licensed
to the user and not part of the repository; later updates of this document refer to that analysis and to other
documents of `doc/safety` and cite the standard by part and clause at most, never by its text.

*Issue 3 (DOC-51):* the record of the confirmation review `reviews/records/CR-07-1.md` (not published, available on request)
(answers, findings and decisions ZA … ZM of the user, 2026-10-04); the gap analysis sections 4 and 5.1 (questions to
the standard, proposals C1 … C18), read by part and clause only; [`dfa.md`](dfa.md) DF-05 and DFA-6 and the safety
manual section 5 (faults of the time bases, board 2026-10-03/04); [`component-qualification.md`](component-qualification.md)
sections 2, 3 and 4.3; [`coexistence.md`](coexistence.md); [`dev-environment.md`](dev-environment.md) DE1 and
`tools/ci/versions.toml` (the pinned compiler); the code of the active short at `dev` `aaa22c86`
(`ecu/application/src/systems/safeState/SafeStateRte.h`, `safeSupervisor/Degraded.h`).

- Code: `ecu/safety/**`, `platforms/s32k1xx/port/safety/**`, `platforms/s32k1xx/hardFaultHandler/**`,
  `libs/bsw/{e2e,ecuState,canSystem,gdTmc6200,bccMc33771,sbcUja113x,highSideSwitch}/**`,
  `ecu/application/src/systems/{SafetySystem.cpp,RteSystem.cpp}`.
- Module documentation: `libs/bsw/*/doc/*.md`, `platforms/s32k1xx/port/safety/safeSystem/doc/safeSystem.md`, and
  the generated catalogue [`../reference/modules.md`](../reference/modules.md).
- Architecture: [`../architecture/README.md`](../architecture/README.md) (binding target picture, decisions
  D1–D15), [`../architecture/treiber-granularitaet.md`](../architecture/treiber-granularitaet.md),
  [`../architecture/openbsw-naehe.md`](../architecture/openbsw-naehe.md),
  [`../architecture/signal-qualifier.md`](../architecture/signal-qualifier.md),
  `../proposals/IO-32-debug-halt-detection.md` (not published, available on request).
- Measurements and board findings: `CHANGELOG.md`, entries SYS-05, SYS-06, SYS-18, IO-32 (all four rounds),
  TOOL-40, ARCH-16, ARCH-32, SYS-07, IO-16, and the board sessions of 2026-09-18 and 2026-09-19.
- Configuration and tests: `tools/test/tests.yaml`, `tools/test/coverage.yaml`,
  `tools/openbswsync/copies.yaml`, `projects/evalboard_s32k144_demo/project.yaml`,
  `boards/evalboard_s32k144/board.yaml`, `mcudb/s32k1xx/*.yaml`.
- Decisions of the user: decision board items `safety-asil`, `safety-umfang`, `safety-analyse`,
  `safety-anforderungen` (2026-09-21), `io32-halt-erkennung` (2026-09-19), the acceptance `abn-sys19`
  (2026-09-21, issue 2), the decision `sys23-halt` (2026-09-21), and the entries in `doc/todo.yaml`.
- Issue 2 additionally: the `CHANGELOG.md` entries SYS-23, PLAT-74 and IO-37, checked against the code at
  `fa04ed1` — `ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp` (`holdSafeState()`),
  `platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/RomCheck.cpp` and its (missing) callers,
  `ecu/application/CMakeLists.txt` (`.checksum` removed from the HEX), the three watchdog constants,
  `platforms/s32k1xx/hardFaultHandler/include/hardFaultHandler/safeOutputs.h`,
  `libs/bsw/{gdTmc6200,highSideSwitch}/module.yaml`, `bootloader/src/core/app_image.c`, and
  [`static-analysis.md`](static-analysis.md).
- Data-sheet facts quoted here (TMC6200 turn-off time, BTS5030 sense accuracy, MC33771C `tCOM_LOSS`, UJA113x
  watchdog modes) are taken from the module documentation, which carries the page references; the data sheets
  themselves are not in the repository.

---

## 13. Later stage: encapsulated safety packages of the device drivers

**Standing.** Stage 2 of the element (section 1.4), decided by the user with `abn-sys19`: *"Sicherheitsmechanismen
der Gerätetreiber (z. B. Gate-Treiber TMC6200, MC33771C) kommen später, gekapselt."* Nothing in this section is
part of the ASIL B argument of stage 1, and the argument of stage 1 does not depend on it. The work is **SYS-28**
(per part: mechanisms, fault model, diagnostic coverage from the manufacturer documents, report to the safe
supervisor over a defined interface). The mechanism numbers SM-40 … SM-43 are kept, so that SYS-20, SYS-22 and
DOC-19 can refer to them unchanged.

### 13.1 "Encapsulated" — the interface to the base software

Encapsulated means: a device package decides nothing about the ECU's reaction, exactly like every mechanism of
stage 1 (section 3: the reaction is decided in one place). A package detects, and it reports. What it may rely on
from the base software, and what the base software expects from it:

| Direction | Content | Status today |
|---|---|---|
| package → base software | a **fault or qualifier report to the safe supervisor**: an event per device class with a context word (device instance, fault class), raised through the same monitor pattern as every stage-1 mechanism; the reaction (S3, S1, S1 + S2) is chosen in `SafeSupervisor::handle()`, not in the driver | **base-software side exists since SYS-29 (SM-34):** events `DEVICE_REPORT` (S3, from any context through the package's `DeviceReportBox` or from the safety task) and `DEVICE_FAULT` (switching off, safety task only), context word `safeSupervisor/DeviceReport.h` with the bus id (F5). No device driver uses it yet (SYS-28); faults still end in the driver's own latch, the log, and — for the gate driver only — the RTE port `gd.<device>.faults` (IO-25), whose qualifier now carries the supervisor's bus verdict |
| package → application | the device's measured values with a **qualifier** (the RTE signal qualifier, SM-30), so that a value from a faulty part is marked, not silently used | partly: `gd.<device>.faults` exists; `bcc.<device>.faults` and qualifiers for cell values do not |
| base software → package | the **safe state reaches the part**: an output the part needs to be de-energised is declared `safe_off` (stage 1, SM-23); an actuator that is switched by a *bus command* rather than a pin (balancing) needs a base-software permission that S1 revokes and that the package must hold before commanding | `safe_off` exists; the permission for bus-commanded actuators does not (gap G4) |
| base software → package | a **time budget**: the package's supervision cycle runs as a runnable with an RTE deadline (SM-05) | exists through the RTE |

The base-software side of this interface — the event, the context format, the permission and its revocation by
S1 — is a stage-1 mechanism and is proposed as a task of its own (gap G5, section 10.9). The device-side use of it
is SYS-28.

### 13.2 What the manufacturer documents are needed for

This was section 6.8 in issue 1. The content is unchanged in substance; what changed is its standing: none of it is
credited in stage 1, and SYS-20 does not analyse it — SYS-28 does.

**The open item, stated plainly.** For none of the four parts do we have the manufacturer's **safety manual or
safety application note**. The user was asked for them (decision board item `safety-bausteinunterlagen`, raised
before this task started, as CLAUDE.md requires) and **the answer is still outstanding**. Without those documents
we can describe *what the driver does with the diagnostic features the data sheet documents*, but we cannot state:

- which diagnostics the manufacturer **requires** to be enabled and evaluated for a safety-related use;
- which fault modes the part's internal diagnostics **do not** cover, and what external measure is expected
  instead;
- the **diagnostic coverage** the manufacturer claims, and under which conditions;
- the required **test intervals** of the part's own self-tests;
- the part's **assumptions of use** towards us.

Therefore each part below carries an explicit gap entry. **No diagnostic coverage is claimed for any device driver
in this concept, and none may be claimed in SYS-28 until the documents are on the table.** Everything in the
tables below is read from the data sheets already used by the drivers and from the driver code; it is the *floor*
of what the part offers, not a statement of sufficiency.

### 13.3 SM-40 — TMC6200 gate driver (`libs/bsw/gdTmc6200`)

The item table of SM-40 (supervision cycle, what the part and the driver detect, reaction, times, evidence today, the open points that need the manufacturer's safety documentation) is the field `STATEMENT` of the node SM-40 in [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc) (stage 2, moved by DOC-21).

### 13.4 SM-41 — MC33771C cell controller (`libs/bsw/bccMc33771`)

Note: the module's manifest is **parked** (`module.yaml.pending`) — no project can instantiate it yet, and its
verification is `mock`. It is in scope because the user's scope decision names it, not because it is ready.

The item table of SM-41 (supervision cycle, what the part and the driver detect, reaction, times, evidence today, the open points that need the manufacturer's safety documentation) is the field `STATEMENT` of the node SM-41 in [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc) (stage 2, moved by DOC-21).

### 13.5 SM-42 — UJA113x system basis chip (`libs/bsw/sbcUja113x`)

The item table of SM-42 (supervision cycle, what the part and the driver detect, reaction, times, evidence today, the open points that need the manufacturer's safety documentation) is the field `STATEMENT` of the node SM-42 in [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc) (stage 2, moved by DOC-21).

### 13.6 SM-43 — BTS5030 smart high-side switch (`libs/bsw/highSideSwitch`)

The item table of SM-43 (supervision cycle, what the part and the driver detect, reaction, times, evidence today, the open points that need the manufacturer's safety documentation) is the field `STATEMENT` of the node SM-43 in [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc) (stage 2, moved by DOC-21).

### 13.7 Cross-cutting for all four

| Item | Content |
|---|---|
| SPI as the common path | all four are on SPI. A failure of the SPI master or of the bus is a **common cause** for the gate-driver, cell-monitor and SBC diagnostics at once. Each driver detects its own communication failure and latches, but nothing today detects "the SPI bus is gone" as one event with one reaction. Finding F5 |
| Polling, not interrupts | none of the four uses its interrupt or FAULT line as an interrupt; all are polled at 10–20 ms. The parts themselves react in microseconds; the software knows one cycle later |
| No RTE ports for the fault words | `gd.<device>.faults` is implemented (IO-25); `bcc.<device>.faults` is proposed but not implemented. Until an application can read them, the driver diagnostics end at the console and the log |

---

## 14. History of issue 2 and its updates

Kept unchanged from issue 2 as the record of how the concept developed (decision ZI of CM-07): the changes of
issue 2 against issue 1 and every "Update after issue 2" block up to DOC-48, in the order they were written. Their
content is part of the body of issue 3; where a block says "announced", "follow-up", "not credited yet" or
"proposed", the body states what holds now. Section numbers in the blocks are those of issue 2, which issue 3 keeps.

#### Changes in issue 2 (SYS-27)

The user's decision of 2026-09-21, verbatim: *"ASIL B für die erste Umsetzung bestätigt; ASIL C und D werden später
betrachtet. Festlegung zu den angenommenen Sicherheitszielen: strikte Trennung zwischen Sicherheitsmechanismen der
Gerätetreiber und Sicherheitsmechanismen von neXtBSW. Jetzt werden alle notwendigen Sicherheitsmechanismen der
Basissoftware selbst umgesetzt, um ASIL B (oder höher) erreichen zu können. Sicherheitsmechanismen der Gerätetreiber
(z. B. Gate-Treiber TMC6200, MC33771C) kommen später, gekapselt."* The rest of issue 1 (safe states, reaction
times, the 21 AoUs, findings F1–F8) was accepted and stays; below is everything that changed, by section.

| Section | Change |
|---|---|
| 0 | Device-driver coverage now belongs to the later stage (SYS-28); SYS-20 no longer waits for the manufacturer documents |
| 1.2 | Scope stays "everything", but in two stages: stage 1 (now) argues ASIL B with mechanisms of the base software only; stage 2 (later, encapsulated) adds the device drivers' own mechanisms. New subsection 1.4 defines the split |
| 2.1 | ASIL C and D are **to be considered later** instead of "not offered"; independence, decomposition and the MC/DC path are kept as a later stage, not dropped |
| 2.2 | Every assumed safety goal is argued with base-software mechanisms only. New columns: coverage at stage 1 and the later device contribution (not credited). ASG-03 and ASG-04 are **not covered** by the base software, ASG-01 and ASG-07 only partly — stated as gaps G1–G6 (new section 9.5). The mechanism references in the table were corrected (issue 1 cited SM-20/21/22, SM-13 and SM-11 for mechanisms whose catalogue numbers are SM-23/SM-17/SM-06, SM-21/SM-22 and SM-14) |
| 3 | New reaction **S1 held** (safe state held without reset until power cycle), introduced by SYS-23 for two events; decided by the user on 2026-09-21 (`sys23-halt`, option a) |
| 4 | AoU-03 (ASIL C/D later), AoU-04 (held safe state), AoU-08 (watchdog values are code constants, not derived project values) and AoU-21 (device drivers belong to stage 2) reworded; **AoU-22** (the integrator runs the static analysis on his own code and configuration, proposed by PLAT-74) and **AoU-23** (no stage-1 claim for ASG-03/ASG-04) added |
| 5 | Reaction level lists S1 held |
| 6.1 | SM-07: reaction is now S1 held (SYS-23) |
| 6.2 | SM-14: the ROM check is **not active today** (no caller of `RomCheck::idle()`, `.checksum` removed from the HEX); reaction would be S1 held |
| 6.4 | Note on SM-22 adjusted: two more events enter S1 without reset now, but hold it |
| 6.6, 6.7 | SM-33 and SM-52 marked as device-dependent: described, not credited for stage 1 |
| 6.8 | Content moved to the new section 13 (later stage); SM-40 … SM-43 keep their numbers |
| 7.1, 7.2 | Watchdog values: all three are code constants, the relation is checked by `nxconfig` (SYS-23), SYS-25 moves them into a configuration level; the device row points to section 13; new row for S1 held |
| 8.4 | Static analysis (PLAT-74) listed as an existing safeguard |
| 9.1 | Status column: F1 and F2 addressed by SYS-23, F7 by IO-37 (both still without board proof), F3 extended by the inactive ROM check |
| 9.2 | `safeSupervisor` has its own host test since SYS-23 |
| 9.3, 9.5 | PLAT-74 delivered; SYS-24, SYS-25, SYS-28 listed; new section 9.5 lists the coverage gaps G1–G6 that the separation made visible |
| 10 | SYS-20 analyses stage 1 only and is no longer blocked by the manufacturer documents; SYS-21 carries the C/D items as a later stage; DOC-19 takes 23 AoUs and the per-goal coverage; new 10.9 proposes tasks for G1–G6 |
| 11, 12 | Review questions and sources extended for issue 2 |
| 13 | New: later stage — encapsulated safety packages of the device drivers (SM-40 … SM-43) and their interface to the base software |

#### Update after issue 2: SYS-38 (ROM check, 2026-09-21)

Board finding of session d3 (image `c9a4f31`): `__checksum_list_start` (0x7DFC0) and `__checksum_result` were not in
the flashed image (0xFF), no build step computed the reference, and nothing called `RomCheck::idle()`. SYS-38 makes
SM-14 active: the build writes the checksum list and the reference CRC into every application image of the family
(post-link step, build error instead of a missing list), `RomCheck` refuses an implausible list with
`ROM_CHECK_FAILURE` instead of walking invalid addresses, and the board port starts the check once per start. Updated:
sections 2.2 (ASG-07), 6.2 (SM-14 and its notes), 7 (reaction table), 9.1 (F1, F3) and 9.5 (G6). Still open: the
bounded run time in slices and a re-check period (F3, SYS-20), and the board proof.

#### Update after issue 2: SYS-20 (failure analysis, 2026-09-21)

The failure analysis of the stage-1 mechanisms is [`fmea.md`](fmea.md): per mechanism the faults considered,
detection, reaction, detection and reaction time, a diagnostic-coverage class with its justification (no
percentages) and the evidence. It confirms the verdicts of section 2.2, decides F3 and F5, scopes F4 for SYS-24, and
adds the findings **F9–F16** (section 9.1 below). Its most important correction to section 7.2: the "10 ms" of the
safety-task checks is **nominal**; the guaranteed bound is **31.25 ms** until the safety task's own period is
supervised (F9). Only the section 9.1 table was changed here; the rest of this issue stands.

#### Update after issue 2: SYS-22 (requirements and traceability, 2026-09-21)

The software safety requirements are [`ssr.sdoc`](../requirements/safety/ssr.sdoc) (IDs `SSR-nnn`, stable), each derived
from an `ASG-*` or `AoU-*` of this concept and linked to its mechanisms `SM-*`, findings, code (file and symbol,
later `nx-req` tags) and tests (field `requirements` in `tools/test/tests.yaml`). `python tools/trace check` checks
the chain in the CI (test `safety-trace`) and reports mechanisms without a test and AoUs without a place in the
safety manual (section 10.3); the generated matrix is `traceability.md` (not published, available on request). Finding F2 is SSR-011,
the ROM-check context of `fmea.md` section 5.1 is SSR-025. No other part of this document was changed.

#### Update after issue 2: PLAT-75 (tool classification, 2026-09-21)

The classification of the tools and the safeguards of the generated code are
[`tool-classification.md`](tool-classification.md): per tool and use case the tool impact, the error detection and
the resulting confidence level, the safety-relevant generated items with their safeguards, eight proposed tasks, the
proposed assumptions of use AoU-26 … AoU-28 and the open requirements SSR-080 … SSR-084. It corrects three
statements of section 8.4 and 10.5: there is no checked-in reference output of any generator (the tests assert
derived expectations, finding TF1), the MPU descriptors are computed at run time and only the region count and the
RAM layout are generated (TF2), and `tools/intgen` does not exist (TF3). The rest of this document was not changed.

#### Update after issue 2: SYS-40 (safety task period, 2026-09-22)

Finding F9 is addressed by a period check that extends **SM-04** (section 6.1): every safety cycle
first compares its distance to the previous cycle with the LPTMR, a free-running counter on the 1 kHz LPO clock that
neither the scheduler nor the OpenBSW system time uses, and raises `SAFETY_CYCLE_PERIOD_VIOLATION` (S1 + S2) for an
interval of 17 ms or more, for a window of 7 cycles (8 before SYS-53) that is too short, and for a deviating timer configuration. The
analysis is [`fmea.md`](fmea.md) SM-04 (period part); the SM-04 row of section 6.1, the first row of section 7.2 and
the F9 row of section 9.1 were updated. Nothing else was changed.

#### Update after issue 2: SYS-44 (ROM re-check, 2026-09-22)

Task T5 of [`fmea.md`](fmea.md) (rest of F3 and G6): **SM-14** is repeated during operation with a period set as a
module parameter of the project (`modules.safeMemory.rom_check_period_ms`, manifest
`platforms/s32k1xx/port/safety/safeMemory/module.yaml`, default 60 000 ms, 0 = once per start as before). Every pass
starts with a known-answer test of the CRC unit; no pass starts while a flash write is open (every NvM write is
bracketed) or a flash command runs, and a pass during which one began is discarded, never reported. A due pass that
could not run for longer than one period is reported as `ROM_CHECK_FAILURE`. The pass runs in the idle context without
a lock, so slicing is not needed (fmea.md 5.1). Host verified with mutants; the duration of one pass and the re-check
on the target are board checks (fmea.md B1, B9). Updated: sections 2.2 (ASG-06, ASG-07), 6.2 (SM-14 and its notes),
7.2 (row "faults a reset cannot remove"), 9.1 (F3), 9.5 (G6) and 10.9 (G6).

#### Update after issue 2: SYS-29 (device reports to the safe supervisor, gap G5, 2026-09-22)

Gap **G5** is closed on the base-software side by the new mechanism **SM-34** (proposed number; its catalogue row for section 6.6 is entered together with the revised SSR-064, because the trace check requires both at once): a context word for
device reports (bus id, device instance, fault class, detail; `safeSupervisor/DeviceReport.h`), two events of the
supervisor split by reaction, not by source — `DEVICE_REPORT` (S3 by construction, from any context through a
per-package event box or from the safety task) and `DEVICE_FAULT` (switching-off reaction per fault class, safety
context only) — the correlation of communication faults per bus (finding F5 of [`fmea.md`](fmea.md)) and the
supervisor's bus verdict in the qualifier of the device values in the RTE. The split follows the user's decision of
2026-09-21 (decision board `sys37-postfach-abschaltend`): the event box of SYS-37 only carries reports without a
switching-off reaction; a fault that must switch off is detected in the safety task itself. No driver logic was added;
whether the device packages of SYS-28 report through the box, through checks in the safety task, or both, stays
open for SYS-28 — both paths exist and use the same word. Updated: the G5 rows of sections 9.5 and 10.9
and the first row of section 13.1. Nothing else was changed.

#### Update after issue 2: SYS-87 (the three safety role pins, 2026-09-25)

TOOL-110 found that nothing stated how the three pins of the `safety` section may be used: the indicator of the
safe state (`safe_state_output`) is disabled in S1 and switched back with the application's last level when S1 is
left, so an actuator there would float and then restart without an enable — the case SSR-004 excludes. The user
decided on 2026-09-25 (decision board `tool110-anzeige-von-anwendung-geschrieben`, variant A): an assumption of use,
a warning of `nxconfig` and an explicit, reasoned confirmation in the project. New: **AoU-30** in section 4.2 (next
free number after AoU-24/25 proposed in `fmea.md` and AoU-26 … AoU-29 proposed in `tool-classification.md`); SSR-004
now names AoU-30 in `derived_from` (its text is unchanged); the note on S1 in section 3; the residual row of SM-22
in [`fmea.md`](fmea.md). Nothing else was changed.
AoU-30 accepted by the user as safety manager on 2026-09-30 (safety review SYS-87, decision board `review-sys87`,
without independence, T-11).

#### Update after issue 2: SYS-117 (degraded reaction SD, 2026-09-30)

Until now every reaction of neXtBSW ended in switching everything off or in a reset (finding PD-4 of
[`partner-and-degraded.md`](partner-and-degraded.md)); an ECU with two redundant drives, one of which must keep braking
when the other fails, could not be built on it. The user decided on 2026-09-29 (decision board `q-sys115-116-117`) all
recommendations of that concept; part B is implemented by SYS-117: a fifth reaction **SD — degraded** (B1), only for
the events on a **closed list of the supervisor** (B2; today `DEVICE_FAULT`, never a fault of the MCU itself), switching
**named safe-output groups** of the one safe outputs table (B3), latched in **no-init RAM until the power cycle, applied
again after every safety reset**, reported to the application and through it to the error memory (B4), escalating to
**S1 without a reset when the last group is degraded** (B5), and with **no return within the power cycle** (B6).
Changed: the last paragraph of section 2.2, section 3 (SD), AoU-04 and the new **AoU-32** (section 4.1), level 2 of
section 5, a row of section 7.2. Requirements SSR-001 (b), SSR-087, SSR-088. What SD does not do yet (the gate-driver
enable joins a group with IO-45; the active short of SYS-116; the partner events of SYS-115) is listed in
[`fmea.md`](fmea.md) under SM-23. Nothing else was changed.

#### Update after issue 2: IO-45 (gate-driver enable in its safe-output group, 2026-09-30)

The binding "gate driver ↔ PWM group" is the board value `pwm[].gate_driver` (IO-25). With IO-45 the `safe_off`
output of a device bound to a PWM group - the enable of its gate driver - is part of the safe-output group of that
PWM group (decision C2 (a) of [`partner-and-degraded.md`](partner-and-degraded.md), in its freewheel form - SYS-116 is
not implemented): SD pulls it low together with the channels, and the driver of that enable is locked while its group
is degraded, the same way as in S1 (enable refused, no mismatch fault). An enable of a device bound to no PWM group
keeps going off with S1 only (`nxconfig` warning). Changed: AoU-32 (last sentence), the SM-40 table in section 13.3 (the
enable rule of the driver - now one common state of the three phases - takes effect on the target), SM-23 residual
(1) in [`fmea.md`](fmea.md). Nothing else was changed.

#### Update after issue 2: SYS-123 (per-group lock in the drivers, 2026-09-30)

SYS-117 left a window: after a safety reset or through the debug halt guard a degraded group could run again for at
most one safety cycle, a deviation from [`partner-and-degraded.md`](partner-and-degraded.md) 4.1 step 1 ("the drivers
of those groups refuse on"). SYS-123 closes it in the family port: the PWM driver keeps a locked group forced (at its
initialisation before the pins reach the timer, at every duty write), the debug halt guard does not release it, and
the degraded groups are walked again right after the BSP start. The lock is the supervisor's latch of SYS-117, read per
entry of the safe outputs table; no second latch. Only projects with a degraded mapping compile it (the object code of
the demo is unchanged). Changed: SM-23 residual (2) and (3) in [`fmea.md`](fmea.md). Nothing else was changed.

#### Update after issue 2: SYS-86 (hardware threshold, 2026-09-30)

A first mechanism that acts **without software**: a project may name a threshold on a board signal at an input of
the MCU's comparator; the family port routes the comparator output to a fault input of the PWM timer, which forces
the group's outputs to their inactive level in hardware and holds them until a reset
([`hardware-mechanisms.md`](hardware-mechanisms.md) 2.4). Added: the catalogue row **SM-58** in section 6.5 and its
analysis in `fmea.md`. It is optional, used by no checked-in project, verified on the host and in a target build
only, and **not credited**: whether a hardware path counts for stage 1 stays the decision of SYS-20
(`hardware-mechanisms.md` section 3). Nothing else was changed.

#### Update after issue 2: DOC-38 (decisions of the safety responsible, 2026-09-30)

The user as safety responsible decided on 2026-09-30 in the safety reviews of SYS-117, SYS-86 and DOC-19 (decision
board `review-sys117`, `review-sys86`, `review-doc19`) and released this document (document register,
[`anomaly-register.md`](anomaly-register.md) section 6):

- **Released texts (review SYS-117, R8):** AoU-04, AoU-32, the last paragraph of section 2.2 and the paragraph on SD
  in section 3, as written by SYS-117 (and AoU-32 as completed by IO-45), stand as they are.
- **SM-58 stays not credited (review SYS-86, H3):** the row in section 6.5 keeps the mark *not credited*; the trace
  check treats such a row as described without a claim (no requirement needed, none may credit it).
- **Assumptions of use carried over from the safety manual (review DOC-19, D2):** AoU-26 … AoU-29 (proposed in
  [`tool-classification.md`](tool-classification.md) section 6) and AoU-34 … AoU-36 (numbered in
  [`safety-manual.md`](safety-manual.md) section 7) are now entries of section 4, so `tools/trace` checks them: AoU-35
  in 4.1, AoU-34 and AoU-36 in 4.3, AoU-26 … AoU-29 in the new 4.5.
- **Stale places corrected (review DOC-19, D6):** AoU-08 (the watchdog values are project values since SYS-25, the
  service interval is derived), AoU-16 (the halt deadman is SM-06, not SM-22), AoU-19 (an error memory exists since
  NVM-03; which events reach it).

Nothing else was changed.
#### Update after issue 2: SYS-116 (selectable safe state of a PWM group, 2026-09-30)

A motor inverter without a mechanical brake needs a second safe state: above the speed at which the induced voltage
exceeds the DC link, freewheel brakes uncontrolled through the body diodes and charges the DC link, while an **active
short** (every low-side or every high-side switch on) does not (finding PD-1 of
[`partner-and-degraded.md`](partner-and-degraded.md)). Part C of that concept, decisions C1-C6 (user 2026-09-29,
decision board `q-sys115-116-117`, each the recommended variant), is implemented **in the base software and the
tools**: per PWM group the project lists the allowed states (`pwm.<group>.safe_states`), the application selects the
one in force through a validating service, and **every walk of the one safe outputs table** - fault handlers, halt
deadman, S1, S1 held, SD - applies the selection with one read of an encoded selection word per entry; anything that
is not exactly an allowed pattern, and a gated timer clock, gives freewheel (C1). The gate-driver enable bound to the
group (IO-45) is left as it is in a short (C2). A register-level start-up test of the short exists as a building
block (C5). Configuration checks with YAML path (`tools/nxconfig/safestate.py`). New: catalogue rows **SM-26** and
**SM-27** (section 6.5), a paragraph in section 3, a row in section 7.2; requirements
SSR-089 and SSR-090 (review status proposed). **AoU-33** (the integrator confirms the choice of the state, the board reset state of the bridge and the standstill of the start test) stays announced in [`partner-and-degraded.md`](partner-and-degraded.md) 7.1 and takes effect with the follow-ups - an obligation for a function no project can use yet would be none.

**Not usable in a project yet.** Two parts lie outside the paths of SYS-116 and are follow-ups; until they exist
`nxconfig` refuses every active short, read as code facts of the tree: the safe supervisor's rule that a bridge fault
forces freewheel (C4, `NX_SAFE_BRIDGE_FAULT_CLASSES`), and the family port's integration that keeps a shorted
group's enable on in S1 and in the halt deadman's guard and keeps the short in the PWM group lock (C2,
`NX_SAFE_STATE_SHORT_HOLD`). Without the second, the drivers of the family port would switch the enable off within
their next cycle and the short would end in freewheel; without the first, a short could be applied onto a broken
switch. Also open: the RTE port of the selection (C6), the call of the start test with phase voltages (IO-64), the
board proof (motor, 24 V power stage, with the user). The object code of every project without a short is unchanged.
Limits and residuals: [`fmea.md`](fmea.md) SM-26, SM-27.

**SYS-140 (C4, 2026-09-30):** the safe supervisor now withdraws the active short of a group before any walk of its
reaction when a device bound to the group reports a `DEVICE_FAULT` of the classes `OUTPUT`, `SUPPLY` or `INTERNAL`
(`NX_SAFE_BRIDGE_FAULT_CLASSES` in `safeSupervisor/Degraded.h`), and of every group for a malformed report; the bound
devices are derived by `nxconfig` (requirement SSR-091, review status proposed; host verified, board open).
**SYS-139 (C2, 2026-09-30):** the S32K1 family port keeps the short - the enable of a bound, enabled gate driver stays
on in S1, in a degraded group and after a debug halt, and the PWM inhibit and the group lock write the selected output
control (`NX_SAFE_STATE_SHORT_HOLD`, SSR-092, proposed; host verified, board open). With both code facts in the tree
`nxconfig` accepts an active short; AoU-33 takes effect with the first project that uses one.

#### Update after issue 2: SYS-30 (read-back of the safe state, gap G1, 2026-09-30)

Gap **G1** (section 9.5): until now nothing confirmed that S1 took effect. The generated safe outputs table now carries
a **read-back entry per entry** (`NX_SAFE_OUTPUT_READBACKS`: for a PWM group its software output control - every
channel forced, none to its active level; for a `safe_off` output the **pin level** of its port, not the data
register, so a pin held by something else is seen), and SafeIo reads them in every safety cycle while S1 is latched.
An entry that does not show its safe state is **S1 that did not take effect**: `LOCK_REGISTER_ERROR` with the context
class `SAFE_OUTPUT` and the entry, reaction S1 + S2 - the reset is the one reaction left that puts the pins into their
reset state; the reset-loop limit (SYS-43) ends a loop in S1 held. Reported once, latched until the next `init()`.
With an active short selected (SYS-116) the short is a safe state as well. Not covered yet: S1 held (no task runs),
the groups of SD and the fault handlers' path; board proof open. Changed: SSR-060 (open → partial), the G1 rows of
sections 9.5 and 10.9, [`fmea.md`](fmea.md) SM-23 and B15.

#### Update after issue 2: SYS-135 (hardware shut-off path degrades only its group, 2026-09-30)

Decision of the user as safety responsible in the safety review of SYS-128/SYS-130 (point C1): the latched fault of a
hardware shut-off path (SM-58, report `HW_SHUTOFF` of SYS-130) need not hold S1 for the whole ECU. A project may map
it to the degraded reaction SD (section 3): `safety.degraded: [{event: HW_SHUTOFF, groups: [<group>]}]` names exactly
the safe-output group of the PWM group the path switches off, and a trip then degrades **only that group** - no reset
(the path stays latched until reset, SYS-86 H1), the rest of the ECU keeps running, the last group escalates to S1
without a reset (review SYS-117 R2). The report is on a second closed list of the supervisor
(`NX_SAFE_DEGRADABLE_REPORTS` in `safeSupervisor/Degraded.h`), because it is no event of `SafeSupervisor::Event`.
Only "tripped and nothing else" is degraded: the findings of the path (switched off, reconfigured, stuck, not latched,
SYS-128), a report without context, an unmapped timer and an implausible record keep S1 held as before - they may stem
from a fault of the MCU, which is never degraded. Without the mapping nothing changes. The released paragraphs of
sections 2.2 and 3 and AoU-04/AoU-32 (review SYS-117, R8) are not edited: where they say "faults of one attached
device", the trip of a hardware shut-off path is the second case the supervisor now allows (proposal for the review).
New: **AoU-37** in section 4.3 (point C3 of the same review: the start test of the path needs the input below VDDA by
more than offset plus hysteresis). Limits and evidence: [`fmea.md`](fmea.md) SM-58 (SYS-135),
[`hardware-mechanisms.md`](hardware-mechanisms.md) 2.6.

#### Update after issue 2: SYS-143 (start test of the active short: power-on only, bounded, enable read back, 2026-09-30)

Decisions E2-E4 of the user as safety responsible in the safety review of SYS-136/SYS-141 (2026-09-30). SYS-141 calls
the register-level start test of the short (SM-27, [`partner-and-degraded.md`](partner-and-degraded.md) 5.10) in
`RteSystem::init()`, before the init runnables; SYS-143 changes that call (`ecu/application/src/systems/safeState/
SafeStateRte.h`, `startTests`):

- **E4 - power-on only.** The shorts are built only when the MCU reports a power-on reset (`ecustate::
  mcuStartReasons()`, wake-up bit `POWER`). After any other reset (watchdog, pin, software, debugger) the machine may
  still turn, so nothing is built; the outcome of the power-on test is kept over resets in a record in no-init RAM
  (`.noinit.safeStateTest`, passed groups plus a check word, written before it is read after power-on, like the records
  of SYS-43 and SYS-117). A group keeps its shorts after such a reset only if the record is intact and says it passed;
  otherwise its shorts are removed until the next power-on (log line of the SAFETY component, qualifier
  `safety.qlf.safe_state.<GROUP>` ERROR). A low-voltage reset (RAM content lost) and a start whose reason the MCU
  cannot tell count as "no record": freewheel only until power-on. Before SYS-143 the removal of a failed test lived
  in `.bss` of the service and a warm reset gave the short back without a test.
- **E3 - enable read back.** The test stays register level (5.10 step 3 b) and **never reaches the bridge**: the
  gate-driver enable bound to the group (board `pwm[].gate_driver`, a module with RTE access `gate_driver`) must read
  **off** before the short is built and after it is taken back, read through the driver (`readGateDriver_<device>`
  of the family integration; the TMC6200 driver verifies DRV_EN against its IOIN register, `ENABLE_MISMATCH`). An
  enable that reads on is no passed test (`ENABLE_ON`), the shorts are removed. The phase voltages stay with IO-64;
  standstill and the enable of a group without a bound gate driver are **AoU-38**.
- **E2 - budget.** The hold is a busy wait in the first application task before any application task runs. The task
  that serves the MCU watchdog is above every application task (`tasks.yaml` `above_application`, RTE-02) and is not
  delayed; tasks below it (on the S32K1 port `TASK_BSP` with the SBC watchdog trigger, the device drivers' supervision
  and the halt guard's poll, and `TASK_BACKGROUND`) wait for the sum of all holds. `tools/rtegen` derives
  `rte::SAFE_STATE_TEST_BUDGET_US` = a third of `modules.safeLifecycle.watchdog_timeout_ms` (the three service
  opportunities of section 7.1; demo value 250 ms gives 83 333 us) and refuses a larger sum at
  `pwm.<group>.safe_state_test_us`; `RteSystem.cpp` asserts it again at compile time. A microsecond clock that stands
  still ends the hold after 100 000 reads (`CLOCK_STUCK`, no passed test) instead of holding the short for ever. The
  CPU time of the test is the sum of the holds plus a few register accesses per short, once per power-on.

Evidence: host only (`tools/rtegen/test_rtegen_safe_state.py`, the glue on the real selection service of the S32K1
port with 11 further mutants; generator tests of the reader and the budget); target build of the synthetic copy with a
short (`target-active-short`); the object code of every project without a short is unchanged. Board proof open with
the rest of part C (review SYS-136/SYS-141 E7). Changed: [`fmea.md`](fmea.md) SM-27, [`partner-and-degraded.md`](
partner-and-degraded.md) 5.10, **AoU-38** (section 4.3, proposed).

#### Update after issue 2: SYS-138 (read-back in S1 held, for SD and before the reset, gap G1, 2026-09-30)

Decision of the safety responsible in the safety review of SYS-116/SYS-30 (point K6): close the gaps SYS-30 left; K5:
the reaction stays the reset through `LOCK_REGISTER_ERROR`; an active short (SYS-116/SYS-139) is a safe state. The
read-back of SYS-30 is now one function of the family port (`safeSupervisor/SafeStateReadback.h`, S32K1: SafeIo) that
the safe supervisor uses in three more places: **S1 held** (SYS-23) reads back in every round of the hold loop before
its walk - the loop is the only code that runs, with every interrupt suspended, so neither the halt deadman nor the idle
context could carry it; the **groups of SD** are read back in every safety cycle before their walk; the **S1 + S2
sequence** of every resetting event and of the fault reactions in `ecu/safety` (kernel faults, `ETL_ASSERT`, ECC
error) reads back after its walk and, if S1 did not take effect, resets at once without the console line. Not
confirmed in the hold or for SD: `LOCK_REGISTER_ERROR` (class `SAFE_OUTPUT`, entry), S1 + S2. An entry whose clock is
gated is **not confirmed** any more (it was not judged): a gated timer keeps its outputs frozen, and the register list
expects every such gate on. Nothing is judged before `SafeIo::init()` (reset configuration of the pins). Open: the
hard fault and watchdog handlers and the halt deadman of the family port (outside the task's paths), board check B16.
Nothing else in this document was changed; limits and evidence: [`fmea.md`](fmea.md) SM-23 (SYS-138).

#### Update after issue 2: SYS-125 (low-voltage detect reset in the family port, 2026-09-30)

Decision of the user as safety responsible on 2026-09-30 (anomaly AN-066, finding H9 of
[`hsi-s32k1xx.md`](hsi-s32k1xx.md)): the family port s32k1xx switches the MCU's low-voltage **detect** reset on
instead of leaving the gap to the integrator (AoU-09). Before, a VDD falling below the detect threshold VLVD
(2.80 … 3.00 V) was only flagged; the MCU kept running down to the low-voltage reset VLVR (2.50 … 2.70 V), below the
specified supply range, and only SM-20 could see the drift of the internal supplies at safety-task speed. Now
`boardInit()` writes `PMC_LVDSC1[LVDRE] = 1` in every start, and the hardware resets and holds the MCU until VDD is
back above VLVD plus its hysteresis. Added: the catalogue row **SM-59** in section 6.4 (a hardware mechanism
configured by the base software; **not credited** until the safety responsible decides, like SM-58), its analysis in
`fmea.md`, the proposed **AoU-39** in section 4.3 (supply margin of a 3.3 V board), and the note under SM-20. Effect
per board: a 5 V board (the eval board) runs 2 V above the threshold and sees no difference in normal operation; a
3.3 V board keeps a margin of at least 0.3 V only (AoU-39); in the low-power modes VLPR/VLPS the LVD is off and only
the LVR acts. Not changed: the reset-loop limit still clears on POR/LVD (a brown-out loop is not counted, SM-59 in
`fmea.md`), the bootloader does not set the bit (after power-on it runs with the reset default until the
application starts), and the bit is not re-checked cyclically (rest of H9). Host verified with a register model and
the ELF of the demo; board proof open.

#### Update after issue 2: IO-67 (TMC6200 without supply at the start, 2026-10-01)

Stage-2 driver rule, not credited for stage 1: the SM-40 table in section 13.3 names the new driver fault `NO_SUPPLY`
(an all-zero `IOIN` answer: no supply or SDO held low) and when it is latched. Evidence: host test `tmc6200` and
`tmc6200-io67-mutations` (10 built-in faults, all detected); board proof B-IO67-1 … 8 open. Nothing else in this
document was changed.

#### Update after issue 2: DOC-21 (safety catalogue in StrictDoc, 2026-10-01)

User decision Q5 of DOC-21 (2026-09-22): the assumed safety goals, the assumptions of use and the safety mechanisms
are StrictDoc nodes, so that the software safety requirements name them with checked relations. The tables of
sections 2.2, 4.1 … 4.5, 6.1 … 6.7 and the item tables of 13.3 … 13.6 moved **verbatim** to
[`goals.sdoc`](../requirements/safety/goals.sdoc) (ASG), [`assumptions.sdoc`](../requirements/safety/assumptions.sdoc) (AoU, with the proposed AoU-24/25 of
[`fmea.md`](fmea.md) section 6) and [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc) (SM, stage 1 and stage 2); the software
safety requirements moved from `requirements.yaml` to [`ssr.sdoc`](../requirements/safety/ssr.sdoc). The goals and
assumptions got a short title each (new, for the reader; the statement is unchanged). Everything else stays here: the
reading guides, the notes under the tables, what changed against issue 1, and the prose of every section. The IDs
are unchanged; `python tools/trace check` (test `safety-trace`) reads the catalogue from the StrictDoc model. No
statement of this document was changed.

#### Update after issue 2: SYS-153 and NVM-25 (cyclic LVD check, lock of the FlexNVM block, 2026-10-01)

SYS-153 closes the rest of H9 of [`hsi-s32k1xx.md`](hsi-s32k1xx.md) that the SYS-125 update above left open:
`PMC_LVDSC1` is an entry of the SafeIo register list (class SUPERVISION, mask `0x30`, expected `0x10`, 8-bit
access), so SM-22 sees a cleared `LVDRE` or a set `LVDIE` within one safety cycle (S1 without reset); SM-59 stays
*not credited*, remaining: the LVD is off in VLPR/VLPS. NVM-25 adds **SM-60** (lock of the FlexNVM block: data-flash
readers and EEPROM stores exclude each other, `read()` answers `BUSY` instead of undefined data) to section 6.2 and
the assumption **AoU-40** (holds short, not below a storing task) to section 4.4 - both proposed, not credited,
open for the decision of the safety responsible. Host verified; board proofs open. Analysis: [`fmea.md`](fmea.md)
SM-59 and SM-60.

#### Changes after issue 2 without an update block of their own (recorded by DOC-48, 2026-10-04)

Compared with `git log -- doc/safety/concept.md` since the release of 2026-09-30 (`c44f94ad`): sixteen commits
changed this document up to `e2a1a638`. Eleven of them are described by an update block above (SYS-116 with SYS-139
and SYS-140, SYS-30, SYS-135, SYS-143, SYS-138, SYS-125, IO-67, DOC-21, SYS-153/NVM-25 including the AoU-40 line
of section 4.4). The other five changed single lines:

| Commit | Task, date | Change |
|---|---|---|
| `62e465fa` | integ7, 2026-09-30 | update block SYS-87: AoU-30 accepted by the user as safety manager (safety review SYS-87, `review-sys87`) |
| `ff406e3a` | SYS-133, 2026-09-30 | table of section 1.2, row "Debug-halt guard": the ISR `LPIT0_Ch1_IRQHandler` is defined in `Integration.cpp` (moved out of the header, BEF-003/BEF-004) |
| `3acefb82` | REL-020, 2026-10-01 | state line `Working state: dev at 6f868b5 (after v0.1.0)` → `Baseline: v0.2.0` (release candidate v0.2.0) |
| `0e8e49a0` | REVIEW-1002T, 2026-10-02 | section 4.4: AoU-41 (no bus master other than the core) and AoU-42 (all tasks privileged) added to the list, decided by the safety responsible on 2026-10-02 (review Welle-0210-Tag) |
| `e2a1a638` | SYS-194, 2026-10-03 | section 9.5, row G5: the family port delivers one device box since SYS-194 (take-back of a foreign `safe_off` level, `DEVICE_REPORT`, S3) |

#### Update after issue 2: DOC-48 (formal corrections before the confirmation review CM-07, 2026-10-04)

Formal corrections on the author side, each with its evidence (commit, task, board record); no statement of the
safety argument, no ID and no issue number was changed, and nothing here is a verdict of the review. Changed: the
state line (`cm-plan.md` section 8) and the status above; the table of commits above; the related documents below
(safety plan, configuration range, safety manual, gap analysis) and, in section 1.2, the pointer to the external
interfaces in the safety manual; `tools/intgen` removed and the place of `blgen` corrected in sections 1.2 and 8.4,
and the MPU sentence of 8.4 (TF3 and TF2 of [`tool-classification.md`](tool-classification.md), AN-036, AN-035);
section 3: the decision `sys23-halt` is taken, the follow-ups of the active short exist since SYS-139/SYS-140, the
error memory exists since NVM-03; section 7.1 and the answer to question 2 of section 11: the watchdog values are
project values since SYS-25; sections 7.2 and 7.3: the restart time and the bound of the table walk are measured,
the row of the selected safe state; sections 8.1 and 8.5: the OpenBSW version of the submodule; section 8.4: the
reference outputs exist (PLAT-78) and the MISRA deviations are approved; the status column of section 9.1; the
board proofs of 9.2; the task column of 9.3; G1 and G3 in sections 9.5 and 10.9 (SYS-138, SYS-32); section 12
names the gap analysis. **AoU-37, AoU-38 and AoU-39** (section 4.3, proposed by SYS-135, SYS-143 and SYS-125) are
binding: the user decided so on 2026-10-04 in the review of CM-10 (point Z4); `assumptions.sdoc` no longer calls
them "proposed, open for acceptance". Not changed, because they need a decision of the review: section 0 and 9.4
(the standard is available to the user since SYS-58), the statements on AoU-24/25 and AoU-33, the coverage verdicts
of the assumed goals, the open proposals C1 … C18 of the gap analysis beyond the pointers above.
