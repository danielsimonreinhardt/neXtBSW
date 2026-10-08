# neXtBSW — route per pre-existing component and qualification plan (SYS-62)

| | |
|---|---|
| Issue | 1, 2026-09-28 |
| Task | SYS-62 — gap analysis `iso26262-gap.md` (not published, available on request) section 6 no. 3; rows 6-12, 8-26, 8-27; findings SG5 (AN-031) and rtos-comparison R5 (AN-057) |
| Decisions | user 2026-09-22 **iso-q5** (FreeRTOS route decided later, after SYS-78) and **rtos-u1-anfrage** (stay on FreeRTOS and qualify it); user 2026-09-28 **q-sys62-qualifizierung**: start now **without** FreeRTOS (its route follows SYS-78), table per component, first qualification report for `safeMonitor` |
| Author | SYS-62 (cloud session of wave cloud-1001, written on the user's behalf — author side, safety plan section 2) |
| Approver | the safety manager (the user); **pending** — decision board item `cloud-1001-sys-62` |
| Status | draft for acceptance; confirmation review CM-09 (safety plan, I1) not yet done |
| Verification | **mock/host**: OpenBSW's own `safeMonitor` tests built and run on the host with coverage; static analysis with the project rule set; the gate for modified copies tested synthetically and against `coexistence.md`; target build warning-free. **Nothing on the board** |

Baseline: v0.2.0
Readers: the user as safety manager (accepts the routes and the report), the coordinator who carries the proposals
into the other safety documents, the agents of the follow-up qualifications (section 4), SYS-63 (confirmation
review CM-09) and later an integrator who wants to know on what grounds pre-existing software is in the element.
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**What this document is.** The work product that the safety plan names for activities **P6-07** (pre-existing
software) and **P8-09** (qualification of software components) and that the safety case lists as evidence **EW-07**.
It gives every pre-existing component in the demo image one **route** — qualify it per 8-12, treat it as own
development per part 6, replace it, or leave it QM under the coexistence argument — together with the plan that
carries the route out, and it contains the **first qualification report**, for OpenBSW's `safeMonitor`. It also
records the two tool changes the gap analysis asked for: the **modified OpenBSW copies in the safety path** are now
analysed as own code by the static analysis (gating) and are counted in the coverage thresholds.

**Licence rule.** ISO 26262:2018 is cited by **part and clause only** (for example "8-12" = ISO 26262-8:2018 clause 12);
requirements are restated in our own words, and no text, table or figure of the standard is reproduced.

**What it is not.** Not the FreeRTOS qualification (postponed by q-sys62-qualifizierung; its plan is in section 4.3),
not the coexistence argument (that is [`coexistence.md`](coexistence.md), SYS-61, whose module classes this document
uses unchanged), not a tool qualification (compiler and libraries as tools: [`tool-classification.md`](tool-classification.md)).

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Components classified | **16 entries** (section 2): 7 entries of unchanged OpenBSW code used by ASIL B modules, the 72 modified and 36 identical OpenBSW copies (3 entries by class), FreeRTOS with its ports, newlib/libgcc/libstdc++, ETL, CMSIS-Core, the unchanged QM libraries and what is not in the image |
| Routes | **8-12 qualification**: 12 entries (every unchanged pre-existing part an ASIL B module relies on, including the 20 identical copies in ASIL B modules); **part 6 as own development**: 1 entry — the **47 modified copies** in ASIL B modules; **QM, no route while the coexistence rows hold**: 2 entries (41 QM copies, the unchanged QM libraries); **not in the image**: 1 entry. **Replace**: chosen for no component today; it stays the fallback for newlib functions and for any component whose qualification fails |
| First report | **QR-01 `safeMonitor`** (section 5): the three monitor templates neXtBSW uses (`Trigger`, `Value`, `Sequence`, 23 instances in `SafeSupervisor`) are **suitable for ASIL B use in neXtBSW** under 6 assumptions of use; `Watchdog` and `Register` are verified to the same depth but not used. Evidence: 11 component requirements, OpenBSW's own **42 tests pass**, **100 % statements (80/80)** and **100 % branches (28/28)** of the headers, exception edges excluded (the target builds without exceptions); static analysis without a finding that needs action; **1 anomaly** (QA-1: the documentation says the handler runs outside the lock, the code calls it inside — no effect with the `DefaultMutex` neXtBSW uses). Confirmation review pending |
| Modified copies as own development | `tools/misra`: new list `scope.gating_copies` in `rules.yaml` — the **47** modified copies of the ASIL B modules are classified *own* and **gate** the run; a test checks that the list hits exactly the modified copies of the ASIL B modules of `coexistence.md` (counter-checks: one pattern missing, one QM pattern added). Result on the demo build: 23 translation units, **627 findings** — 422 under approved deviation DEV-002 (register access), 145 tool notices, **60 recorded in the baseline** with `baseline --only`, **0 new** |
| Coverage | `tools/test/coverage.yaml`: **7 ASIL B modules** that contain modified copies (or sit next to them) get the safety threshold 95 % / 70 %. Measured 2026-09-28 over the modified copies with a host test: **94.5 % statements (1301/1377), 55.0 % branches (1308/2379)**; **9 of 23** modified source files have **no host test** (section 3.3) |
| FreeRTOS | route **8-12** (decision rtos-u1-anfrage), **not started** (q-sys62-qualifizierung); plan and effort from SYS-78 in section 4.3; the async layer that uses kernel internals (AN-057) is in the same package |
| Findings | **Q-1 … Q-8** (section 6): among them the static analysis of `origin/dev` already fails with 57 new findings unrelated to this task (Q-1), the real MCU watchdog driver of OpenBSW has no host test (Q-3), and 9 modified safety sources are unmeasured (Q-4) |
| Verification | **mock/host** (section 7); nothing on the board |

---

## 1. Reading of the rules and the routes

### 1.1 What the standard asks (own words)

- **6-7.4.7 / 6-12, 8-12.** Software that was not developed for this element — a library, an operating system, a
  component taken over from another project — may be used in a safety-related element when it is **qualified**:
  there is a specification of what it must do in this use (requirements, configuration, interfaces, behaviour in
  abnormal conditions), evidence that it does that (verification suited to the ASIL, including requirements-based
  tests with a measure of structural coverage), evidence that it is suitable for the intended use (assumptions of
  use, integration evidence), the exact version and configuration it holds for, and the known anomalies. The
  qualification holds only for that version and that use; a change of either makes it void.
- **8-12, 10-9.1.** A component that is **changed** is no longer a qualification case: it is developed (or
  re-verified) under part 6 like own code, or argued by a change analysis. Hence the gap analysis (row 8-27): a
  modified OpenBSW copy in the safety path is own development and must pass the same gates as own code.
- **8-14.** Proven in use is **not** used (safety plan tailoring T-07): no field data exists.
- **9-6 / 6-7.4.8.** A QM component in the same image needs no qualification as long as the coexistence argument
  shows that it cannot violate an ASIL B requirement ([`coexistence.md`](coexistence.md)); where the argument is
  open, the component counts as ASIL B (coexistence rule 1.4) and falls back to one of the routes above.

### 1.2 Routes used here

| Route | Meaning | Evidence it produces |
|---|---|---|
| **R-Q** (8-12) | qualify the unchanged component for its use in neXtBSW | a qualification report per component (template: section 5) |
| **R-6** (part 6) | the component is own development; unit design, unit tests with coverage, static analysis as a gate, code review | the part-6 work products of the module (sw-architecture, unit-design, tests, `tools/misra`, `coverage.yaml`) |
| **R-X** (replace) | remove the component or replace it by own code developed under part 6 | the replacement's part-6 work products |
| **R-QM** | QM under the coexistence argument; no qualification while every coexistence row of its module holds | the rows of `coexistence.md` section 3 |
| **n/a** | not linked into the image (host tests, build files, other families) | — |

### 1.3 Inputs

- Module classes and the membership of every copy: [`coexistence.md`](coexistence.md) sections 2.1 and 6 (SYS-61) —
  used unchanged; the longest path prefix decides.
- Copy status per file: [`tools/openbswsync/copies.yaml`](../../tools/openbswsync/copies.yaml) (OpenBSW submodule
  `3b6e702`): **72 modified, 36 identical, 49 new** entries.
- FreeRTOS inventory, usage and effort: `rtos-comparison.md` (not published, available on request) (SYS-78).
- Compiler and libraries as tools: [`tool-classification.md`](tool-classification.md) (TN2, compiler TCL 3).

---

## 2. Table per component

"Used by" names the modules and classes of `coexistence.md`. Effort is in agent days (AD) and is an estimate of this
author unless a source is given.

<!-- components:start -->
| ID | Component | Version / where | Used by | Route | Plan (next step) | Status |
|---|---|---|---|---|---|---|
| PC-01 | OpenBSW `safeMonitor` (header-only monitor templates) | submodule `3b6e702`, `libs/safety/safeMonitor` | M-SUP (B-SM): 17 `Trigger`, 3 `Value`, 2 `Sequence` instances plus the `Trigger` inside `DeferredTrigger` | **R-Q** | report QR-01 (section 5); review CM-09 | **qualified (author side)**, review pending |
| PC-02 | OpenBSW `safeUtils` (`SafetyLogger.h`, 18 lines) | `libs/safety/safeUtils` | M-SUP, M-MEM, M-LCY (logger name of the safety part) | **R-Q** | report QR-02; small (0.5 AD): it only declares the logger component | planned |
| PC-03 | OpenBSW MCU watchdog driver `safeBspMcuWatchdog` (305 lines) | `platforms/s32k1xx/safety/safeBspMcuWatchdog` | M-WDG (B-SM) | **R-Q** | report QR-03 with a host test of the real `Watchdog.cpp` against a WDOG register model (Q-3); 2–3 AD | planned |
| PC-04 | OpenBSW interrupt lock `bspInterruptsImpl` (+ interface `bspInterrupts`) | `platforms/s32k1xx/bsp/bspInterruptsImpl`, `libs/bsp/bspInterrupts` | S-INT (B-SEP) | **R-Q** | report QR-04; the lock is part of the separating element (6-7.4.9); 1–2 AD | planned |
| PC-05 | OpenBSW MCU drivers `bspAdc`, `bspIo`, `bspMcu` (with the NXP device header), `bspCore`, `bspClock` (submodule part) | `platforms/s32k1xx/bsp/*` | D-ADC, D-IO, D-TIM, D-CLK (B-DEP) | **R-Q** | one report per driver, used functions only (the ASIL B callers are listed in `coexistence.md` 2.3); 1–2 AD each | planned |
| PC-06 | OpenBSW interface headers `common`, `bsp`, `timer`, `async` (interface) | `libs/bsw/{common,bsp,timer,async}` | every ASIL B module (types, return codes) | **R-Q** | one report for the used declarations (no code of their own beyond inline helpers); 1 AD | planned |
| PC-07 | OpenBSW async binding `asyncFreeRtos`, `asyncImpl` (about 2 250 lines, uses kernel internals — AN-057) | `libs/bsw/asyncFreeRtos`, `libs/bsw/asyncImpl` | S-OSB (B-SEP) | **R-Q** together with PC-08 | in the FreeRTOS package (section 4.3) | **deferred** (q-sys62-qualifizierung) |
| PC-08 | **FreeRTOS** kernel **V10.6.2** with the Cortex-M4F SysTick port (S32K14x); M0+ and Cortex-R4F ports for other families | `libs/3rdparty/freeRtos`, `platforms/s32k1xx/3rdparty/freertos_cm4_sysTick`; config `platforms/s32k1xx/port/freeRtosCoreConfiguration` | S-RTOS (B-SEP) | **R-Q** (rtos-u1-anfrage) | FreeRTOS package, section 4.3: 30–50 AD for the used subset (SYS-78), redone at every kernel update | **deferred** (q-sys62-qualifizierung) |
| PC-09 | **newlib nano**, libgcc, libstdc++ nano, libm, libnosys | from the pinned xPack GCC **11.3.1-1.1** (`tools/ci/versions.toml`); the newlib version is not stamped in its header (upstream Arm toolchain of the same GCC: newlib 4.2.0 — assumption) | D-RTL (B-DEP) | **R-Q** for the functions the ASIL B modules link; **R-X** as fallback per function | list the linked library functions of the ASIL B archives from the linker map (like `coexistence.md` 2.3), then qualify the few (`memcpy`, `memset`, integer helpers of libgcc) or replace them; tie to the compiler decision TN2; 3–5 AD | planned |
| PC-10 | **ETL** (Embedded Template Library) **20.49.0**, header-only, plus OpenBSW `etlImpl` (assert and platform glue) | `libs/3rdparty/etl`, `platforms/s32k1xx/etlImpl` | D-RTL (B-DEP); `safeMonitor` uses `etl/platform.h` only; the ETL assert handler is finding CX-3 | **R-Q** for the used subset | usage scan of `etl/*.h` in ASIL B modules, one report for the used templates; 2–4 AD | planned |
| PC-11 | **CMSIS-Core(M) 6.1** headers (core register structs, intrinsics) | `libs/3rdparty/cmsis` | S-INT, D-TIM, M-MEM, M-HFH | **R-Q** (light) | review of the used intrinsics and register definitions against the Armv7-M architecture reference; can be folded into the HSI review (`hsi-s32k1xx.md`); 1 AD | planned |
| PC-12 | **Modified OpenBSW copies in ASIL B modules** — 47 files (29 B-SM, 12 B-SEP, 6 B-DEP) | `ecu/`, `platforms/s32k1xx/` (`copies.yaml`, `status: modified`) | 14 ASIL B modules (section 3) | **R-6** | static analysis gating (**done**, section 3.1), coverage counted (**done**, section 3.2); host tests for the 9 unmeasured sources (Q-4); code inspection with task 13 of the gap analysis | **in progress** |
| PC-13 | Identical OpenBSW copies in ASIL B modules — 20 files (12 B-SM, 7 B-SEP, 1 B-DEP) | same roots, `status: identical` | ASIL B modules | **R-Q** with the OpenBSW module they come from | qualified in the report of the component whose file they copy (e.g. the `safeSupervisor` build files); list per report | planned |
| PC-14 | Modified OpenBSW copies in QM modules — 25 files; identical QM copies — 16 files | `copies.yaml` | Q-LOG, Q-CON, Q-LCM, Q-DIA, Q-CDR, Q-BLD, Q-BCF | **R-QM** | none while every coexistence row of the module holds; a module with an open row counts as ASIL B (coexistence 1.4) and its modified copies then join PC-12 | open rows: `coexistence.md` section 3 |
| PC-15 | Unchanged QM libraries of OpenBSW: logger, util, printf, asyncConsole, bspCharInputOutput, bspUart, lifecycle, runtime, time, uds, docan, transport, transportRouterSimple, cpp2can, bspFlexCan, canflex2Transceiver, bspInputManager, bspOutputManager, bspOutputPwm | submodule | QM-C modules | **R-QM** | as PC-14; CX-1 (logger/`printf` before the safe state) must close first | open rows |
| PC-16 | googletest, OpenBSW code of other targets (posix, ThreadX, lwIP, SOME/IP, DoIP) | submodule | not in the demo image | **n/a** | googletest is a test tool (tool classification), not a component | — |
<!-- components:end -->

Notes on the table:

- **Why unchanged OpenBSW parts are R-Q and not R-6.** They are pre-existing: neXtBSW did not write them and must not
  change them (the submodule is never changed, CLAUDE.md). A change would turn a file into a copy (PC-12).
- **Why no component is R-X today.** Replacing is always possible but costs more than qualifying the small
  components; it is the fallback when a qualification finds an anomaly that cannot be handled by an assumption of use.
- **Header-only components** (PC-01, PC-02, PC-06, PC-10, PC-11) are compiled into the ASIL B units that include them.
  Their code therefore appears in those units' objects; the qualification report is still per component, because the
  component is what is reused and what changes with a submodule update.

---

## 3. Modified copies as own development (PC-12)

### 3.1 Static analysis as a gate

Before this task `tools/misra` analysed every OpenBSW copy, but reported copies **separately and not gating**
(`static-analysis.md` section 2; gap analysis SG5 / AN-031). Now:

- `tools/misra/rules.yaml` has a list **`scope.gating_copies`** of 19 path patterns, one comment per module, taken from
  the module table of `coexistence.md`. A copy with `status: modified` that matches is classified **own** and its
  findings follow the rules of own code: deviation, baseline, or a new finding that fails the run. **Identical**
  copies stay separate even when they match (route R-Q, PC-13), and so do copies of QM modules (R-QM, PC-14).
- `tools/misra/test_misra.py` checks the list against the document: the set of modified copies the patterns hit must
  equal the set of modified copies whose module is `B-SM`, `B-SEP` or `B-DEP` in `coexistence.md` — **47** at this
  working state; every pattern must hit a copy. Counter-checks: dropping `ecu/safety/safeWatchdog/**` or adding
  `ecu/application/src/logger/**` (QM) is detected.
- `python -m tools.misra baseline --only <file>` (new) rewrites the baseline entries of the named files only. It was
  used to take the 47 files in without taking in the 57 findings that `origin/dev` already fails with (Q-1).

Result on the demo build (`evalboard-s32k144-demo`, RelWithDebInfo, cppcheck 2.17.1 and clang-tidy 22.1.8, run
2026-09-28):

<!-- gate:start -->
| Module | Class | Modified copies | of them sources | Deviated | Notice | Baseline | New |
|---|---|---:|---:|---:|---:|---:|---:|
| M-SUP | B-SM | 3 | 1 | 0 | 27 | 3 | 0 |
| M-WDG | B-SM | 3 | 1 | 0 | 2 | 0 | 0 |
| M-LCY | B-SM | 6 | 3 | 0 | 3 | 0 | 0 |
| M-MEM | B-SM | 8 | 4 | 64 | 28 | 5 | 0 |
| M-SYS | B-SM | 2 | 1 | 19 | 8 | 2 | 0 |
| M-SIO | B-SM | 4 | 2 | 0 | 2 | 3 | 0 |
| M-INT | B-SM | 1 | 1 | 11 | 0 | 1 | 0 |
| M-CAN | B-SM | 2 | 1 | 2 | 54 | 18 | 0 |
| S-RTOS | B-SEP | 2 | 0 | 0 | 0 | 0 | 0 |
| S-OSB | B-SEP | 3 | 2 | 0 | 9 | 1 | 0 |
| S-INT | B-SEP | 7 | 3 | 0 | 2 | 5 | 0 |
| D-ADC | B-DEP | 1 | 1 | 7 | 6 | 1 | 0 |
| D-TIM | B-DEP | 1 | 1 | 18 | 0 | 0 | 0 |
| D-CLK | B-DEP | 4 | 2 | 301 | 4 | 21 | 0 |
| **total** | | **47** | **23** | **422** | **145** | **60** | **0** |
<!-- gate:end -->

"Sources" counts `.cpp`/`.c` files; the others are headers, CMake files, linker scripts and start-up assembly (the
assembly and the linker script are not analysed by either tool — the linker script has its own host test, the
start-up code is in the code inspection of gap task 13). All 422 deviations are the approved deviation **DEV-002**
(register access through the vendor register blocks, `platforms/*/port/**` and `platforms/*/bsp/**`); its file
patterns already covered these paths, so no new deviation was needed. The 60 baseline entries by rule:
`hicpp-signed-bitwise` 21, `cppcoreguidelines-virtual-class-destructor` 8, `useStlAlgorithm` 7, `unreachableCode` 5,
`cppcoreguidelines-pro-type-member-init` 4, two each of `readability-implicit-bool-conversion`,
`bugprone-branch-clone`, `cppcoreguidelines-init-variables`, `clang-analyzer-security.ArrayBound`, and one each of
seven others. The two `ArrayBound` findings in `MemoryProtection.cpp` (`checkLockProtection`, `checkValidity`) were
checked: both private functions are only called from the loop in `checkMemoryProtection` that is bounded by the
number of configured regions — false positives of an analysis that looks at the function alone. The other baseline
findings are **not individually assessed** yet; like every baseline entry they are to be worked down (Q-2).

### 3.2 Coverage counted

`tools/test/coverage.py` already counted the copies — they lie in the own tree and `coverage.yaml` excludes only the
submodule, tests and generated code. What was missing was the **safety threshold** for the modules that hold them.
`coverage.yaml` now has entries at 95 % statements / 70 % branches (the class "sicherheitsrelevant" of SYS-19) for
`ecu/safety/safeSupervisor`, `ecu/safety/safeWatchdog`, `platforms/s32k1xx/port/safety/safeLifecycle`,
`…/safeMemory`, `…/safeIo`, `…/safeSupervisionCheck` and `platforms/s32k1xx/bsp/bspClock`, each with the measured
values in its reason. The thresholds are still reported, not enforced (`enforce: false`, gap task 12).

Measured 2026-09-28 (`tools/test/coverage.py`, Linux container, 72 of 78 `zig` entries; the six that do not build
there — Q-6 — cover none of these modules), over the modified copies only:

<!-- coverage:start -->
| Module | Measured copies | Statements | Branches |
|---|---:|---|---|
| M-SUP | 2 | 99.4 % (348/350) | 60.3 % (376/624) |
| M-WDG | 2 | 100 % (24/24) | 61.1 % (11/18) |
| M-LCY | 1 | 95.7 % (67/70) | 53.3 % (49/92) |
| M-MEM | 6 | 90.0 % (315/350) | 54.7 % (286/523) |
| M-SYS | 1 | 100 % (112/112) | 62.9 % (78/124) |
| M-SIO | 2 | 100 % (43/43) | 60.9 % (28/46) |
| S-INT | 1 | 85.3 % (29/34) | 32.4 % (11/34) |
| S-OSB | 1 | 40.0 % (2/5) | 25.0 % (1/4) |
| D-TIM | 1 | 100 % (70/70) | 51.9 % (54/104) |
| D-CLK | 1 | 91.2 % (291/319) | 51.1 % (414/810) |
| **total** | **18** | **94.5 % (1301/1377)** | **55.0 % (1308/2379)** |
<!-- coverage:end -->

### 3.3 Modified safety sources without a host test

Nine of the 23 modified source files are not executed by any host test (headers without code excluded):
`ecu/application/src/app/app.cpp` and `ecu/application/src/systems/SafetySystem.cpp` (S-OSB, M-LCY),
`platforms/s32k1xx/port/main/src/main.cpp` (M-INT), `…/main/src/systems/CanSystem.cpp` (M-CAN),
`…/main/src/lifecycle/StaticBsp.cpp` (D-CLK), `…/main/src/os/isr/isr_can.cpp` and `isr_wdg.cpp` (S-INT),
`…/safety/safeLifecycle/src/safeLifecycle/IsrWrapper.cpp` (M-LCY) and
`…/bspConfiguration/src/bsp/adc/AnalogInput.cpp` (D-ADC). Most of them are integration code (start order, task
table, interrupt entries) whose evidence today is the board and the linked-image checks; for part 6 each needs
either a unit test or a written rationale why the integration test is the unit-level evidence (6-9). This is
finding **Q-4**.

---

## 4. Qualification plan (8-12)

### 4.1 Procedure per component

Every R-Q component gets a report with the sections of QR-01 (section 5), in this order of work:

1. **Identify**: path, submodule commit, SHA-256 (16 hex digits) of every file, licence, configuration used.
2. **Intended use**: which parts neXtBSW uses, by which ASIL B module, how many instances, with which template
   arguments or configuration.
3. **Specify**: component requirements in our words, from the documentation *and* the code; where they differ,
   the code decides and the difference is an anomaly.
4. **Assumptions of use**: what the user of the component must ensure (lifetimes, contexts, locks, value ranges).
5. **Verify**: requirements-based tests (OpenBSW's own tests where they exist, own tests where they do not), with
   statement and branch coverage of the component; static analysis with the project rule set; a trace table
   requirement → test; integration evidence in neXtBSW (host tests with the real ASIL B callers, board records).
6. **Anomalies**: every difference and weakness found, with its effect in neXtBSW and how it is handled.
7. **Verdict and validity**: for which use the component is suitable; the report is void when a file hash or the
   use changes.

### 4.2 Order and effort

| Order | Component | Effort | Why this order |
|---|---|---|---|
| 1 | PC-01 `safeMonitor` | done | smallest, decides every safety event (q-sys62-qualifizierung) |
| 2 | PC-02 `safeUtils`, PC-06 interface headers | 1.5 AD | trivial, but every ASIL B unit includes them |
| 3 | PC-03 `safeBspMcuWatchdog` | 2–3 AD | independent watchdog of the MCU; real code has no host test (Q-3) |
| 4 | PC-04 `bspInterruptsImpl` | 1–2 AD | separating element; needed before SYS-21 can argue partitioning |
| 5 | PC-11 CMSIS-Core, PC-05 MCU drivers | 1 + 5–10 AD | with the HSI review (`hsi-s32k1xx.md`) |
| 6 | PC-10 ETL subset, PC-09 libraries | 5–9 AD | depend on the usage scans; PC-09 also on the compiler decision TN2 |
| 7 | PC-08 FreeRTOS with PC-07 async binding | 30–50 AD (SYS-78) | postponed by q-sys62-qualifizierung |
| — | PC-13 identical copies | inside 2–6 | qualified with the component they copy |

Total without FreeRTOS: about **16–30 AD**; with FreeRTOS: about **46–80 AD**.

### 4.3 FreeRTOS package (plan only, not started)

Route **R-Q** as decided on 2026-09-22 (rtos-u1-anfrage, after the comparison `rtos-comparison.md`): stay on FreeRTOS
and qualify it; the migration to a certified kernel was not chosen. Scope and steps, taken from `rtos-comparison.md`
sections 1 and 4:

- **Scope** — the kernel V10.6.2 in the configuration of `FreeRtosPlatformConfig.h` (static allocation, no MPU port,
  no timers task unless used), the Cortex-M4F SysTick port with OpenBSW's `ManualChanges.diff`, and the kernel
  services the async binding calls, **including the kernel internals it relies on** (AN-057). Other ports (M0+, R4F)
  are separate reports when those families enter a safety release.
- **Steps** — requirements of the used API subset; the port's interrupt masking and context switch reviewed against
  the architecture manual; tests of the subset on the host (posix port) and on the target (board test suite, gap task
  20); static analysis of the kernel files with the project rule set (reported, not gating, since the code is not
  ours); anomaly list from the FreeRTOS change history between V10.6.2 and the current release.
- **Effort** — 30–50 AD for the first core, redone at every kernel update (`rtos-comparison.md` section 0).
- **Open** — whether a qualified component is accepted as the **separating element** (6-7.4.9); the coexistence
  argument (SYS-21) needs the answer.

---

## 5. Qualification report QR-01 — OpenBSW `safeMonitor`

### 5.1 Identification

| Item | Value |
|---|---|
| Component | `safeMonitor` — generic monitor templates of the OpenBSW safety library |
| Source | `external/openbsw/libs/safety/safeMonitor`, OpenBSW submodule commit **`3b6e702`** (2026-09-14) |
| Licence | Apache-2.0 (header of every file); `module.spec`: `maturity: production`, `safety: True` |
| Files (SHA-256, first 16 hex digits) | `Register.h` `1e3248df946ca148`, `Sequence.h` `e7b903d0d7d174be`, `Trigger.h` `69ff3b14b06fc1ad`, `Value.h` `68dd7d4def403c7d`, `Watchdog.h` `2993bb7c545cc64b`, `common.h` `0cc4f65c4ec043ef` (581 lines of headers) |
| Build form | header-only, CMake `INTERFACE` library linked by `safeSupervisor`; uses `etl/platform.h` (`ETL_MAYBE_UNUSED`) only |
| Configuration in neXtBSW | template arguments of `ecu/safety/safeSupervisor/include/safeSupervisor/SafeSupervisor.h`: handler `SafeSupervisor`, event `SafeSupervisor::Event`; `ScopedMutex` always the default `DefaultMutex` (no lock); context `DefaultContext` for the sequence monitors, `uint32_t` for trigger and value monitors |

### 5.2 Intended use

`SafeSupervisor` (M-SUP, B-SM) owns every safety monitor and is the handler of all of them: 17 `TriggerMonitor`
(`Trigger<…, DefaultMutex, uint32_t>`), 3 `ValueMonitor` (`Value<…, bool, DefaultMutex, uint32_t>`), 2 sequence
monitors (`SafetyManagerSequenceMonitor` over 7 checkpoints of the safety cycle, `SequenceMonitor` over ENTER/LEAVE)
and one `DeferredTriggerMonitor` that wraps a `Trigger` for reporters outside the safety task
(`SafetyEventMailbox.h`, SYS-37). Every event a mechanism reports reaches the reaction of `SafeSupervisor::handle()`
through one of these monitors. `Watchdog` and `Register` are **not used** by neXtBSW.

### 5.3 Component requirements

Written for this report from the documentation (`doc/index.rst`, the header comments) and the code; the code decides
where they differ (QA-1).

<!-- smon:start -->
| ID | Requirement |
|---|---|
| SMON-01 | `Trigger::trigger(ctx)` stores `ctx` and calls the handler with the configured event exactly once per call. |
| SMON-02 | `Value::check(v, ctx)` stores `ctx`; it calls the handler with the configured event exactly once if `v` differs from the expected value, and not at all otherwise. |
| SMON-03 | `Sequence::hit(cp, ctx)` stores `ctx`; if `cp` is not the expected checkpoint it calls the handler once and leaves the expectation unchanged; otherwise it advances the expectation by one, and after the last checkpoint back to the first. The first expected checkpoint after construction is the first one. |
| SMON-04 | `Watchdog::service()` counts down from the timeout, saturating at 0, and calls the handler on every call while the counter is 0; `kick(ctx)` stores `ctx` and restarts the count at the timeout. |
| SMON-05 | `Register::check(ctx)` stores `ctx`, compares the masked value at each entry's address with the expected value in order, stops at the first mismatch and then calls the handler once; `getLastCheckedEntry()` names the last entry compared (the faulty one after a mismatch, `nullptr` before the first check). |
| SMON-06 | Every public operation that changes state holds exactly one `ScopedMutex` object for its whole duration; the handler is called **while it is held** (QA-1). |
| SMON-07 | The stored context is a copy; `getContext()` returns the last stored context, and a default-constructed context before the first store. |
| SMON-08 | The monitor calls the handler in the caller's context and returns after it; it neither blocks nor defers. |
| SMON-09 | No dynamic memory, no recursion, no exceptions thrown, no static state other than the constant default context; run time is constant per call (`Register`: linear in the number of entries). |
| SMON-10 | The monitor keeps a reference to the handler and (`Register`) to the entry array; it does not copy them. |
| SMON-11 | The event and the expected value are copied at construction and cannot change afterwards. |
<!-- smon:end -->

### 5.4 Assumptions of use

| ID | Assumption | How neXtBSW meets it |
|---|---|---|
| SMON-A1 | The handler object (and the `Register` entry array) outlives the monitor (SMON-10). | `SafeSupervisor` owns the monitors as members and is a static object |
| SMON-A2 | A monitor is changed and read from one execution context, or the `ScopedMutex` makes the operations mutually exclusive. `getContext()` itself is **not** guarded. | all monitors are driven and read in the safety task; reporters outside it post to `SafetyEventMailbox`, which hands the report over in the safety context (SYS-37, host test `safe-supervisor-mpu`) |
| SMON-A3 | With a non-default `ScopedMutex` the handler runs inside the lock (SMON-06); it must not take the same lock or block. | only `DefaultMutex` is used; a compile-time check is proposed (P-6) |
| SMON-A4 | The checkpoint type of a `Sequence` has consecutive values from the first to the last checkpoint, first ≤ last (the monitor advances by `+1`). | both enums in `SafeSupervisor.h` are consecutive from 0 with first < last; not checked at compile time (QA-3) |
| SMON-A5 | `Watchdog` with timeout 0 reports at the first `service()` call. | `Watchdog` not used |
| SMON-A6 | The monitor state lies in memory the safety part protects; a corrupted monitor can suppress or invent an event. | `SafeSupervisor` and its monitors lie in the MPU-protected safety RAM (SYS-37, `sw-architecture.md`) |

### 5.5 Verification

**Requirements-based tests.** OpenBSW ships 42 tests in `test/src` (gtest/gmock, each with a description of what it
checks). They were built unchanged with googletest from the same submodule and run on the host:

```sh
g++ -std=c++17 -O0 --coverage -fno-inline -Wall -Wextra \
    -I external/openbsw/libs/safety/safeMonitor/include -I external/openbsw/libs/safety/safeMonitor/test/include \
    -I external/openbsw/libs/3rdparty/etl/include -I ecu/etl_profile \
    -I <googletest>/googletest/include -I <googletest>/googlemock/include \
    external/openbsw/libs/safety/safeMonitor/test/src/*.cpp gtest-all.o gmock-all.o gmock_main.o -lpthread -o smtest
./smtest                                   # [  PASSED  ] 42 tests.
gcovr --filter '.*safeMonitor/include/.*' --exclude-throw-branches .
```

Result 2026-09-28 (g++ 13.3, gcovr 8.6): **42 of 42 passed**; **statements 80/80 (100 %)**, **branches 28/28
(100 %)** with exception edges excluded (with them 28/40: the 12 missing edges are the exception paths of the lock
object and the handler call, which do not exist on the target — it is built with `-fno-exceptions`).

<!-- trace:start -->
| Requirement | Tests (OpenBSW, `test/src`) |
|---|---|
| SMON-01 | `TriggerTest.TriggersHandler` |
| SMON-02 | `ValueTest.DetectsTooHighAValue`, `DetectsTooLowAValue`, `PassesWithExpectedValue` |
| SMON-03 | `SequenceTest.RunsThroughInCorrectOrder`, `WrapsBackToFirstCheckpoint`, `DetectsWrongCheckpointAtTheBeginning`, `…InTheMiddle`, `…AtTheEnd`, `FiresMultipleTimes` (three wrong hits, then the first checkpoint is still expected) |
| SMON-04 | `WatchdogTest.DetectsTimeout`, `Triggers` (reports on every call after the timeout), `KickResetsTimeout`, `KickResetsTimeoutOnlyOnce` |
| SMON-05 | `RegisterTest.DetectsBitFlipToZero`, `DetectsBitFlipToOne`, `IgnoresMaskedBits`, `ChecksAllEntries`, `LastCheckedEntryIsInitiallyNull`, `…PointsToLastEntryAfterCheck`, `…PointsToFaultyEntry` |
| SMON-06 | `UsesScopedMutex` of `Register`, `Sequence`, `Trigger`, `Value`; `WatchdogTest.ServiceUsesScopedMutex`, `KickUsesScopedMutex` — count and release of the lock; **handler inside the lock**: supplementary probe below |
| SMON-07 | `*Test.UsesDefaultConstructedContext`, `StoresAndReturnsGivenContext`, `ContextIsBeingCopied` (5 monitors each) |
| SMON-08 | every test above: the mock handler is called synchronously inside the tested call |
| SMON-09 | analysis of the code (no `new`, `throw`, recursion or static mutable data in the six headers); static analysis below |
| SMON-10, SMON-11 | analysis of the code (member types `Handler&`, `Entry const* const`, `Event const`, `Type const`) |
<!-- trace:end -->

**Supplementary probe (SMON-06).** A short program (26 lines) instantiates all five monitors with a lock type that counts
itself and a handler that records whether the lock is held; each monitor is made to report once (the `Watchdog` also
with timeout 0). Result: **6 reports, 6 inside the lock** — confirming QA-1 and SMON-A5.

**Static analysis.** One translation unit instantiating every template as neXtBSW does, analysed with the checks of
`tools/misra/rules.yaml`: clang-tidy 22.1.8 — **no finding in the six headers** (all findings of the run are in the
C++ standard library); cppcheck 2.17.1 with the MISRA add-on — 19 × rule 12.3 on template parameter lists (the C
checker reading C++; a tool notice in `rules.yaml` `notices_cpp`) and 2 × `unusedVariable` on the lock object
`ETL_MAYBE_UNUSED const ScopedMutex m;` of `Trigger` and `Value` (the object exists for its lifetime, false
positive). Nothing that needs a change or a deviation.

**Integration evidence in neXtBSW.** The real `SafeSupervisor` with the real `safeMonitor` headers runs in 12 host
test entries (`safe-supervisor`, `safe-supervisor-bare`, `safe-supervisor-mpu`, `safe-watchdog`, `safety-cycle`,
`cycle-timer-off`, `safe-io`, `safety-gate`, `safe-console`, `safety-reset-console`, `kernel-fault-reaction`,
`safe-state-hold`); they trace to SSR-001…005, 012…018, 020, 021, 033, 034, 064…066 and 070 in `tests.yaml`. On
2026-09-28 in the Linux container 10 of them passed with the normal flags and all 12 passed in the coverage build;
`safe-supervisor` and `safe-supervisor-bare` stop there at `-Werror` on a 64-bit `long` in the test file (Q-6), not
in `safeMonitor`. The board records of the safety reactions (`tools/boardtest/captures`) exercise the same code on
the target.

### 5.6 Anomalies

| ID | Anomaly | Effect in neXtBSW | Handling |
|---|---|---|---|
| QA-1 | The comments of `Register::check`, `Sequence::hit` and `Watchdog::service` say the handler is called **outside** the `ScopedMutex`; the code constructs the lock at the start of the function and calls the handler before it ends, i.e. **inside** (all five monitors). | none: neXtBSW uses `DefaultMutex` (no lock) everywhere | requirement SMON-06 states the real behaviour; assumption SMON-A3; proposed as an upstream documentation fix (P-7) |
| QA-2 | `getContext()` is not guarded by the `ScopedMutex`; a reader in another context can see a partly written context. | none today (SMON-A2) | assumption SMON-A2 |
| QA-3 | `Sequence` advances by `static_cast<Checkpoint>(expected + 1)` and needs consecutive checkpoint values; nothing checks this at compile time. | none: both enums are consecutive | assumption SMON-A4; a `static_assert` in `SafeSupervisor.h` is proposed (P-6) |
| QA-4 | `Watchdog` with timeout 0 reports at the first `service()`; a timeout of `N` gives a window of `N` calls including the one that reports. | none: not used | assumption SMON-A5 |

### 5.7 Verdict and validity

- **`Trigger`, `Value`, `Sequence` with `DefaultMutex`** — as used in neXtBSW — are **suitable for use in the ASIL B
  safety part** under the assumptions SMON-A1 … A6: the requirements SMON-01 … 03 and 06 … 11 are specified, verified
  by requirements-based tests with full statement and branch coverage of the component, the static analysis shows no
  finding that needs action, and the one documentation anomaly (QA-1) has no effect in this configuration.
- **`Watchdog` and `Register`** are verified to the same depth (SMON-04, 05) but not used; the verdict extends to
  them only after their intended use is added to 5.2 and checked against the assumptions.
- **Validity.** Bound to submodule `3b6e702` and the six file hashes of 5.1, and to the configuration of 5.1. A
  submodule update that changes one of the hashes, a new template argument (another lock, another context type) or
  a new user of the component makes the report void until it is re-checked. Proposal P-5 automates the hash check.
- **Status.** Complete on the author side; the confirmation review **CM-09** (safety plan, I1 independence, task
  SYS-63) is pending, and the acceptance by the safety manager (decision board `cloud-1001-sys-62`).

---

## 6. Findings

| ID | Finding | Proposed handling |
|---|---|---|
| Q-1 | **The static analysis of `origin/dev` already fails** (independent of this task): `python -m tools.misra run` on the demo build reports **57 new findings** at `6f868b5` — 23 in `libs/bsw/dem/include/dem/Dem.h`, 6 in `RteSystem.cpp`, 5 in `NvmManager.h`, 8 in the bootloader, the rest in UDS, FlexNVM and integration code (NvM/DEM/NVM-11 work since the baseline of 2026-09-21). The CI stage `[static_analysis]` has `enforce = true`. This task did **not** take them into the baseline (that would hide them); the run shows exactly these 57 before and after the change | coordinator: review them with their tasks (NVM-02/03/11, TOOL-136) and fix or baseline them; check whether the CI run of `dev` is red |
| Q-2 | The 60 baseline findings of the modified copies are recorded, not assessed one by one (two `ArrayBound` are shown false above) | work down with the code inspection of gap task 13 |
| Q-3 | The real MCU watchdog driver of OpenBSW (`safeBspMcuWatchdog/src/watchdog/Watchdog.cpp`) is compiled by no host test — `safe-watchdog` uses a stub `watchdog/Watchdog.h` | QR-03 adds a host test against a WDOG register model |
| Q-4 | 9 of 23 modified safety sources have no host test (section 3.3) | unit tests or a written rationale per file (part 6 unit verification) |
| Q-5 | `platforms/s32k1xx/port/bspConfiguration/include/bsp/adc/AnalogInput.h` and `AnalogInputScale.h` are modified copies classified QM (Q-BCF) in `coexistence.md`, although `AnalogInput.cpp` (D-ADC, B-DEP) includes them | coexistence owner (SYS-61/SYS-21): check whether the headers belong to D-ADC; the gate list follows the document |
| Q-6 | In a Linux container five host tests do not build with `-Werror` because `unsigned long` is 64 bits there (`uja113x`, `lpspi`, `flexcan-device`, `safe-supervisor`, `safe-supervisor-bare`), and the coverage report counts the ziglang `libcxx` below `.venv/` as a module | platform task for Linux CI (fixed-width types in the tests); `coverage.yaml` exclude of `.venv/**` if the coordinator agrees |
| Q-7 | Approved deviation DEV-002 now also covers 422 register-access findings in the gated copies — by its own file patterns, which were written for "platform code" | none needed; mentioned so the approver knows the deviation's reach grew |
| Q-8 | `doc/safety/test/test_safety_plan.py` and `test_safety_case.py` fail on `origin/dev` before this change (P2-03 status; evidence links) | coordinator (not in this task's paths) |

---

## 7. Verification of this task

All **mock/host**, 2026-09-28, Linux container, working state `6f868b5`:

| What | Command | Result |
|---|---|---|
| Target build | `cmake --preset evalboard-s32k144-demo -B <outside the repo>` and `cmake --build <dir> --config RelWithDebInfo` (xPack GCC 11.3.1) | 341/341, **0 warnings** |
| Static analysis before | `python -m tools.misra run --compile-db <dir>/compile_commands.json` on `origin/dev` | 57 new (Q-1), 699 copy findings separate |
| Static analysis after | same, with the change | 47 gated files, 627 findings in them: 422 deviated, 145 notice, 60 baseline, **0 new**; the 57 new are identical to before |
| Baseline | `python -m tools.misra baseline --only <each of the 47 files>` | 200 → 260 entries, nothing else changed |
| Configuration | `python -m tools.misra check` | OK |
| Unit tests of the tool | `python -m unittest tools/misra/test_misra.py` | 30 tests OK (1 skipped: needs cppcheck on `PATH`) |
| Mutation check of the new tests | 4 faults built into `analysis.py` one at a time (identical copies gated, `--only` ignored for new or kept entries, gating list ignored) | each detected (1–6 failing tests) |
| Coverage | `python tools/test/coverage.py --jobs 6` | 72 of 78 entries measured; values of section 3.2 |
| `safeMonitor` tests | section 5.5 | 42/42, 100 % / 100 % |

---

## 8. Proposals for other documents (not changed here)

| ID | Document | Proposal |
|---|---|---|
| P-1 | `safety-plan.md` | P6-07 and P8-09: status `in progress`, evidence this document; CM-09 can be scheduled (SYS-63) |
| P-2 | `safety-case.md` | EW-07: status `partial` with link to this document (QR-01 done, the other reports planned); EW-06 unchanged; D-10 narrowed to "FreeRTOS, newlib, ETL, CMSIS and the unchanged OpenBSW parts other than `safeMonitor`" |
| P-3 | `anomaly-register.md` | AN-031 → `closed` (modified copies in the safety path gate the static analysis, section 3.1); AN-057 → keep open, owner the FreeRTOS package (section 4.3); new entries for QA-1 … QA-4 and Q-1 … Q-8; **document register**: the row below (until it is there, `safety-anomaly-register` fails with "component-qualification.md has no row in the document register") |
| P-4 | `static-analysis.md` section 2 | replace "copies are reported separately and not gating" by the rule of section 3.1 and name `scope.gating_copies` |
| P-5 | `tools/test/tests.yaml` | two entries (block below): OpenBSW's `safeMonitor` tests as a regular host test, and a hash check that fails when a qualified component's files change without a new report |
| P-6 | `SafeSupervisor.h` | `static_assert` that the checkpoint enums are consecutive, and that every monitor uses `DefaultMutex` (QA-3, SMON-A3) |
| P-7 | upstream OpenBSW | fix the comment of `Register::check`, `Sequence::hit`, `Watchdog::service` (QA-1) |
| P-8 | `concept.md` AoU-20 | "not qualified" → "qualified per `component-qualification.md` where the report exists; the others not yet" |

Row for P-3 (document register of `anomaly-register.md` section 6):

```text
| `component-qualification.md` | Route per pre-existing component and qualification plan | SYS-62 | pending (cloud-1001-sys-62) | 1, 2026-09-28 | draft | present (Working state) | header; CHANGELOG SYS-62 |
```

Proposed entry for P-5. The `safeMonitor` tests need gtest/gmock, which no `zig` entry builds today; the entry
therefore names a small build-and-run script that does not exist yet (it would do what section 5.5 shows, with
`ziglang` instead of `g++`, and belongs to whoever may change `tools/test`):

```yaml
  - name: safemonitor-upstream
    group: host
    kind: script
    comment: >
      SYS-62 (QR-01, doc/safety/component-qualification.md 5.5): die unveraenderten Tests von OpenBSW safeMonitor
      (42 Faelle, gtest/gmock aus dem Submodul) - Nachweis der Komponentenanforderungen SMON-01..11
    args: [tools/test/scripts/safemonitor_upstream.py]
    requires: [openbsw]
```

---

## 9. Review questions of the architecture (section 11)

This task changes the static analysis tool, the coverage thresholds and a safety document; no code of the element.

1. **Board without an SBC** — not affected; the gate list names modules, not devices.
2. **Other crystal / clock** — not affected.
3. **Other derivative, S32K3/STM32/rm4x** — the gate list is written for the S32K1 port paths of the demo image, as
   `coexistence.md` is; when another family's port enters a safety release, its modified copies are added in the same
   way (the test compares with whatever `coexistence.md` classifies). Not blocked.
4. **Two or more CAN buses** — not affected.
5. **Several tasks** — not affected.
6. **One configuration level** — the gate list lives in `tools/misra/rules.yaml` only; the classes it follows live in
   `coexistence.md` only; the test keeps the two consistent.
7. **Host test with own test profile** — the gate is tested with a synthetic repository of its own and against the
   real list; four built-in faults were detected.
8. **Core logic free of Qt, messages with YAML path** — `tools/misra/analysis.py` has no Qt; a wrong
   `scope.gating_copies` is reported as `tools/misra/rules.yaml (scope.gating_copies)`.
9. **Device drivers** — not affected.
10. **Files under `platforms/`** — none added.

---

## 10. Sources

- ISO 26262:2018 parts 6, 8, 9, 10 — cited by clause number only.
- `iso26262-gap.md` (not published, available on request) section 6 no. 3, rows 6-12, 8-26, 8-27, finding SG5 (SYS-58).
- [`coexistence.md`](coexistence.md) sections 1.2, 2.1, 6 (SYS-61) — module classes.
- `rtos-comparison.md` (not published, available on request) sections 0, 1, 4 (SYS-78) — FreeRTOS inventory and effort.
- [`safety-plan.md`](safety-plan.md) P6-07, P8-09, CM-09, T-07; [`safety-case.md`](safety-case.md) EW-06, EW-07, D-10;
  [`anomaly-register.md`](anomaly-register.md) AN-031, AN-057, AN-094.
- [`static-analysis.md`](static-analysis.md), [`tools/misra/README.md`](../../tools/misra/README.md),
  [`tools/test/coverage.yaml`](../../tools/test/coverage.yaml).
- OpenBSW `libs/safety/safeMonitor` (`doc/index.rst`, headers, `test/src`) at submodule `3b6e702`.
