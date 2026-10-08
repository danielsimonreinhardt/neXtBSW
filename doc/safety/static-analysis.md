# Static analysis and coding guidelines (PLAT-74)

Baseline: v0.2.0

Status: 2026-09-21, **PLAT-74**, first issue; **PLAT-76**: deviations approved, rule 15.5 disapplied (section 4a). Part of stage 2 of functional safety (user 2026-09-19): neXtBSW as a
Safety Element out of Context, see [`concept.md`](concept.md) sections 9.3 and 10.4.
Tool decision `safety-analyse` (user 2026-09-21): free tools — **cppcheck with the MISRA add-on and clang-tidy** in
the CI; a commercial tool only with a concrete series project.

The tool lives in [`tools/misra`](../../tools/misra/README.md); the CI stage is described in
[`tools/ci/README.md`](../../tools/ci/README.md).

MISRA rule texts are copyrighted. This document, the rule set and the reports name **rule numbers and short
descriptions in our own words only**. Whoever needs the text reads it in the licensed standard.

---

## 1. Rule set

| Code | Guideline | Checked by | Status |
|---|---|---|---|
| C (bootloader, E2E library, newlib hooks, C parts of the platform) | **MISRA C:2012** (with the amendments as far as the add-on implements them) | cppcheck MISRA add-on | checked, gating |
| C and C++ | cppcheck core checks (`warning`, `style`, `performance`, `portability`) | cppcheck | checked, gating |
| C and C++ | selected clang-tidy checks: `bugprone-*`, `cert-*`, clang static analyzer (`core`, `cplusplus`, `deadcode`, array bounds), initialisation, casts, signed bit operations, implicit bool conversion, virtual destructors, definitions in headers | clang-tidy | checked, gating |
| C++ (application, drivers, platform port) | **MISRA C++:2023** | — | **not checked**: no free tool implements it. The clang-tidy selection covers several of its topics (casts, initialisation, recursion via the add-on, narrowing), but it is not a MISRA C++ compliance statement. Open until a commercial tool is chosen for a series project |

Which checks are active, with the reason for each exclusion, is stated in
[`tools/misra/rules.yaml`](../../tools/misra/rules.yaml) — the single place. Two exclusions are worth naming here:
`bugprone-throwing-static-initialization`/`cert-err58-cpp` (every C++ unit is compiled with `-fno-exceptions`, nothing
can throw) and `bugprone-easily-swappable-parameters` (style, not a fault class).

The MISRA add-on is a C checker. On a C++ unit it reads C++ syntax as C — template and base-class lists as comma
operators (12.3), using-declarations as typedefs (5.6, 2.3), C++ types as unknown (`misra-config`). Its findings in
C++ units are therefore counted as **tool notices** and do not gate, except rule 17.2 (recursion), whose meaning is
the same in C++. A header seen from a C and a C++ unit is judged in the C view.

## 2. Scope — what is "our code"

The only source of translation units, include paths and defines is the **compile database of the target build**
(`compile_commands.json` of the demo project; in CI `<build root>/demo`). Every file is classified:

| Class | Meaning | Treatment |
|---|---|---|
| own | neXtBSW code | analysed, gating |
| copy | OpenBSW copy — `identical` or `modified` in [`tools/openbswsync/copies.yaml`](../../tools/openbswsync/copies.yaml) | analysed, **reported separately**, not gating |
| external | the submodule `external/openbsw` | not analysed |
| excluded | Simulink output (`swc/*/generated`), host tests, third-party code under `platforms/*/3rdparty` | read as context, findings dropped |
| outside | not in the repository: generated board and RTE code in the build directory | read as context, findings dropped |

The separation between our code and the OpenBSW copies comes from `copies.yaml` and nowhere else (SYS-19 finding in
concept.md 10.4) — the same list that the OpenBSW qualification path (concept.md 8.1, option a) starts from.
Generated code is read as context so that cross-unit rules see the callers (without it the E2E library looks
unused, rule 8.7); its own findings belong to the generators (PLAT-75), not to this list.

Because the compile database drives the analysis, **only code that the demo project compiles is analysed**. On
2026-09-21 that is 55 own translation units (19 C, 36 C++) and 38 OpenBSW copies. The report names every own source
file that no unit compiles; today 11 files in 6 modules (the STM32 bootloader and BSP, the bootloader's link transport,
`mcuLink`, `CellMonitorSystem`, `HardFaultTestCommand`, `cacheUnavailable`). A second analysed profile is an open point
(section 7).

## 3. Verdicts and what breaks the run

Every finding of our code gets exactly one verdict:

| Verdict | Source | Effect |
|---|---|---|
| disapplied | rule with `revised_category: disapplied` in `rules.yaml` (re-categorization plan, section 4a) | neither new nor baseline; counted and reported separately |
| notice | `notices`/`notices_cpp` in `rules.yaml` (tool limits) | counted, reported |
| deviated | [`tools/misra/deviations.yaml`](../../tools/misra/deviations.yaml) | counted per deviation, reported |
| baseline | [`tools/misra/baseline.yaml`](../../tools/misra/baseline.yaml) (inventory) | counted, reported |
| **new** | none of the above | **the run fails** (`python -m tools.misra run` exit code 1, CI stage red) |

Findings are matched **without line numbers**: file + rule + the stripped source line, counted as a multiset. Moving
code does not create a new finding; a second copy of the same violation does. A baseline entry that is no longer found
is reported as resolved; the baseline is then rewritten and only ever shrinks. A deviation matches by rule, file
pattern and a source-line pattern or symbol — never by file or line number alone — and may carry a `limit`.

A missing tool is **never green**: it is reported as skipped with the place that was searched, and in a strict CI run
(`python -m tools.ci run --strict`) it is red. `python -m tools.ci setup` installs both tools as pinned pip wheels
into the tool cache, without an installer.

## 4. Handling deviations and findings

1. **A new finding** is fixed in the code by the task that owns the code. If the code is right and the rule does not
   fit, a deviation is written.
2. **A deviation** (`deviations.yaml`, MISRA Compliance:2020 "deviation record") names: `id` (DEV-nnn, never reused),
   `kind` (`deviation` or `false-positive` for a proven tool error), rule(s), files, `pattern`/`symbol`, optional
   `limit`, the reason in our own words, who raised it and when, and **who approved it** (`approved_by`; empty = open).
   Approval is the user's; open approvals are listed in every run as a warning and belong on the decision board.
   `python -m tools.misra check` validates the file and reports every error with its YAML path.
3. **A real fault** found by the analysis is not deviated away and not repaired by PLAT-74. It stays in the baseline
   (so that the run is green until it is fixed) and is recorded in [`tools/misra/defects.yaml`](../../tools/misra/defects.yaml)
   with a note and a proposed owner; the report lists these separately.
4. **Rule-set changes** (a check switched off, an advisory rule disapplied) are changes to `rules.yaml` with a reason
   and need the user's decision, like a deviation.

## 4a. Guideline re-categorization plan (PLAT-76)

MISRA Compliance:2020 lets a project re-categorize guidelines: any guideline may be made stricter (advisory →
required → mandatory), and an **advisory** guideline may be **disapplied** — not applied at all. Mandatory and
required guidelines can never be disapplied; a deviation record is the only way to depart from them.

The plan lives in [`tools/misra/rules.yaml`](../../tools/misra/rules.yaml), at the rule itself: `category` stays the
category of the standard, `revised_category` is the one of this project, with `reason` (own words, no rule text),
`date` and `approved_by`. `python -m tools.misra check` (and every run) rejects, each with its YAML path
(`rules.<rule>.revised_category`): disapplying a rule that is not advisory, weakening a rule, re-categorizing a tool
check that has no MISRA category, an unknown category, and a missing reason. A missing approval is a warning.

Findings of a disapplied rule get the verdict `disapplied`: they are **not new** (the run stays green), **not part of
the baseline** (`python -m tools.misra baseline` leaves them out; a baseline that still holds them is reported as a
warning) and are listed in a section of their own in the report, per module. They stay visible, so the decision can
be revisited with numbers.

| Rule | Category | Project | Findings 2026-09-21 | Approval |
|---|---|---|---:|---|
| MISRA C:2012 15.5 (single exit point) | advisory | **disapplied** | 214 (bootloader/src 113, libs/bsw/e2e 50, bootloader/updater 25, bootloader/platforms 20, bootloader/devices 6) | user 2026-09-21, decision board `plat74-abweichungen` |

Reason for 15.5, in short (full text in `rules.yaml`): early return after a failed check is the prevailing pattern of
our C code and keeps the good path flat; a forced single exit needs status variables and deep nesting and makes the
code harder to read and review without making it safer. What the rule protects — no skipped clean-up, a traceable
control flow — is covered by the other control-flow rules, the clang-tidy checks and the review of the function.

## 5. Inventory 2026-09-21 (dev `19df315`, demo project, cppcheck 2.17.1, clang-tidy 22.1.8)

| | cppcheck | clang-tidy | total |
|---|---:|---:|---:|
| baseline (known findings) | 263 | 166 | **429** |
| deviated (14 deviations) | 93 | 49 | **142** |
| tool notices (not gating) | 765 | — | 765 |
| new | 0 | 0 | **0** |
| OpenBSW copies (separate) | 191 | 118 | 309 |

**Update PLAT-76 (2026-09-21, same day, demo project, same tool versions):** rule 15.5 disapplied (section 4a), the
baseline rewritten: **414 → 200** findings (cppcheck 258 → 44, clang-tidy 156 unchanged; only the 214 findings of
15.5 left). The run now reports: new 0, baseline 200, deviated 142 (all 14 deviations approved), tool notices 793,
disapplied 214 (separate), OpenBSW copies 309 (separate); 56 own translation units. (The 414 before PLAT-76 are
smaller than the 429 above because intermediate tasks fixed findings.)

Our code by rule (baseline + deviated, the ten largest; the full table is in every report):

| Rule / check | Short description (own words) | Findings | Largest modules |
|---|---|---:|---|
| MISRA C 15.5 (advisory) | single exit point | 214 | bootloader/src 113, libs/bsw/e2e 50, bootloader/updater 25 — **disapplied since PLAT-76**, no longer in the baseline |
| clang-tidy readability-implicit-bool-conversion | implicit conversion to/from bool | 55 | libs/bsw/e2e 18, bootloader/src 14 |
| clang-tidy hicpp-signed-bitwise | bit operation on a signed operand (also after promotion) | 43 | sbcUja113x 15, bccMc33771 12 |
| clang-tidy cppcoreguidelines-virtual-class-destructor | class with virtual functions, destructor neither virtual nor protected | 37 | platforms/s32k1xx/port 14, ecu/uds 10 |
| register access (11.4, fixed-address dereference, C casts) | peripheral registers through the vendor header | 92 | all deviated (DEV-002/003) |
| MISRA C 8.7 (advisory) | external although used in one unit only | 22 | all deviated (DEV-005/006) |
| clang-tidy cppcoreguidelines-init-variables | local without initial value (out-parameters in C) | 10 | bootloader/src 8 |
| MISRA C 20.7 | macro parameter not parenthesised | 10 | autosarBase 9 (DEV-001) |
| clang-tidy clang-analyzer-security.ArrayBound | possible access past the end | 0 (was 5) | ecu/application 5, fixed by RTE-19 (**BEF-001**, closed) |
| MISRA C 17.2 | recursion | 0 (was 5) | ecuState 5, fixed by SYS-26 (**BEF-002**, closed) |

By module (baseline + deviated): bootloader/src 165, libs/bsw/e2e 90, bootloader/platforms 68,
platforms/s32k1xx/port 60, bootloader/updater 52, sbcUja113x 22, ecu/uds 16, bccMc33771 14, ecu/application 13,
bootloader/devices 12, autosarBase 9, ecuState 9, all others ≤ 6.

Deviations (all **approved** by the user on 2026-09-21, decision board `plat74-abweichungen`, entered by PLAT-76 as
`approved_by` of each entry): DEV-001 AUTOSAR compiler abstraction macros (20.7); DEV-002 peripheral
registers through the vendor header; DEV-003 fixed memory locations of the layout (flash read, boot request, UID,
fail-start record); DEV-004 jump into the application (11.6); DEV-005 public E2E API (8.7); DEV-006 newlib stubs,
vector-table handlers, startup hook (8.7); DEV-007 cppcheck ignores `volatile` (false positive); DEV-008
derivative-dependent layout check; DEV-009 sign extension of an sint8 signal (false positive); DEV-010 C99
compile-time assertions (2.3); DEV-011 family `.inc` include (20.1); DEV-012 CMSIS `__MPU_PRESENT` (20.5, 21.1);
DEV-013 generic lock strategy (false positive); DEV-014 two programs with one `main` each (false positive).

Real faults and safeguard gaps (`defects.yaml`), not repaired here:

| ID | Finding | Proposed owner |
|---|---|---|
| BEF-001 | *closed by RTE-19 (2026-09-21):* RTE loops run to `RUNNABLE_COUNT`, the arrays have `MAX_RUNNABLES`; now `RUNNABLE_COUNT` is a generated constexpr checked by `static_assert` | RTE (rtegen / RteSystem) |
| BEF-002 | *closed by SYS-26 (2026-09-21):* indirect recursion `execute()` → `driveShutdownStep()` → `execute()` in `EcuStateController`, now a bounded loop | ecuState |
| BEF-003 | *closed by SYS-133 (2026-09-30):* interrupt handler `LPIT0_Ch1_IRQHandler` stood in `DebugHaltGuardSystem.h`, now defined out of line in `Integration.cpp` | follow-up of IO-32 (SM-06) |
| BEF-004 | *closed by SYS-133 (2026-09-30):* `deadmanExpectedConfiguration()` (SYS-48) stood in the same header; now out of line in `Integration.cpp`, never inline (the weak default would win and SM-06 would not check the deadman; `test_deadman_expected_elf.py` checks the image) | follow-up of SYS-105 (SM-06) |

In the OpenBSW copies the analysis reports, among others, `unreachableCode` (3), `badBitmaskCheck` (1),
`clang-analyzer-core.CallAndMessage` (1) and uninitialised members (3). They are input for the qualification of
the copied files (concept.md 8.1), not for this list.

## 6. How this fits the safety concept

Static analysis is a **development-process measure**, not a safety mechanism: it gets no SM number and has no
reaction time. It supports the concept in three ways:

- **Evidence for the code of the mechanisms.** The mechanisms of concept.md section 6 are implemented in the analysed
  modules: SM-05 in the RTE (`ecu/application`, BEF-001, closed), SM-06 in `DebugHaltGuardSystem` (BEF-003, BEF-004, closed), SM-23/24 in
  `hardFaultHandler` and the port, SM-31 in `libs/bsw/e2e`, SM-50/51 in the bootloader and the port, SM-53 in
  `ecuState` (BEF-002, closed). A finding in one of these modules is weighed against the mechanism it touches.
- **The OpenBSW part of the argument.** The copies are reported separately from `copies.yaml`; this is the
  "analysis of PLAT-74" that option (a) of concept.md 8.1 refers to.
- **Assumptions of use.** The analysis covers neXtBSW's own code in the configuration of the demo project. It does
  not cover the integrator's application code, his generated code or other derivatives' code paths. This belongs
  next to AoU-17 (application code at the target ASIL) and AoU-20 (unqualified compiler and libraries): an integrator
  who relies on MISRA compliance runs the same (or a qualified) analysis on his own code and his project
  configuration. Proposal for the concept owner: an AoU of its own for this — concept.md is not changed by PLAT-74.
- **Tool classification.** cppcheck and clang-tidy are verification tools whose failure can hide a fault; their
  classification belongs to PLAT-75. The versions are pinned once, in `tools/misra/requirements.txt`.

## 7. Limits and open points

- MISRA C++:2023 is not checked (section 1).
- Only the demo profile is analysed; derivative- and family-specific code paths of other profiles and the 11 own
  files outside the demo build are not. A second compile database (for example `buildtest-s32k148`) is the next
  step once the run time allows it (today about 2.5 minutes for both tools on the workstation, 6 parallel jobs).
- Whole-program rules of the add-on (2.5, 8.7) see one image at a time; bootloader, updater and application are in
  one compile database (DEV-014 exists for that reason).
- Advisory rule 15.5 (single exit) is disapplied project-wide (user 2026-09-21, section 4a); its 214 findings are
  reported separately. An integrator whose own process applies 15.5 has to re-enable it (remove `revised_category`)
  and rewrite the baseline.
- The deviations DEV-001 to DEV-014 are approved (user 2026-09-21); a new deviation needs a new approval.
