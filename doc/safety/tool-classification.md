# neXtBSW tool classification and safeguards of the generated code (PLAT-75)

Baseline: v0.2.0

Status: 2026-09-21, **issue 1 (PLAT-75)**, written against `dev` at `f8ee573` (after SYS-20, SYS-22, SYS-38, COM-30).
Update 2026-09-22 (**SYS-49**, against `dev` at `f2e2c43`): proposals **A** (PLAT-77) and **E** (PLAT-81) are done;
findings TF5 and TF8 are closed, SSR-080 and SSR-084 are implemented, and dbcgen moves from TD2 to **TD1** (TCL 1).
Update 2026-09-22 (**PLAT-82**): proposals **B** (PLAT-78, reference output) and **F** (PLAT-82, RTE timing and
interrupt priorities of the linked image against the configuration, `tools/elfcheck/timing.py`) are done; findings
TF1 and TF7 are closed and SSR-081 is implemented. D (PLAT-80) was done before; C (SSR-082) was the one proposal of the
generated items still open and is implemented by PLAT-79 since 2026-10-01 (all of A to F are done). The image check `elfcheck` has its own row in section 3.
Update 2026-09-22 (**PLAT-75, norm questions**, against `dev` at `62a1b53`): the open points TN1–TN5 are checked
against ISO 26262 (mainly ISO 26262-8, clause 11) and answered in section 9.2; the tool impact classes are renamed to
the standard's names (**TI1** = no possibility of impact, **TI2** = possibility), the planning data of the tool usage
(section 1.4) and the norm-based content of the qualification record (section 5) are added, the host-test compiler
moves from TD1 to **TD2** (conservative estimation), StrictDoc gets its row, and proposals I–L are new.
Readers: the user as architect, the integrator who builds a safety-related ECU with the neXtBSW tools, and the agents
of DOC-19 (safety manual), SYS-22 (requirements) and the tasks proposed in section 7.
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

This document is the tool part that the safety concept [`concept.md`](concept.md) hands to PLAT-75 (concept sections
1.2, 8.3, 8.4 and 10.5). It classifies every tool that produces, transforms, transfers or checks a part of the ECU
image, says how an error of the tool would be noticed, and names the safeguards that exist and the ones that are
missing. It uses the concept's identifiers unchanged (`ASG-*`, `AoU-*`, `SM-*`, `F*`, `G*`) and those of the failure
analysis [`fmea.md`](fmea.md) (`F9` …, `T*`, `N*`).

**ISO 26262 and this document.** Issue 1 was written without the standard. Since 2026-09-22 the user holds a licensed
copy of ISO 26262 parts 1–12 (outside the repository). This document now follows ISO 26262-8, clause 11 (confidence
in the use of software tools) and is meant as the **software tool criteria evaluation report** of neXtBSW's tools in
the sense of ISO 26262-8, clause 11.5.1; the qualification reports of clause 11.5.2 are proposal G. **Licence rule:**
the standard is cited only by part and clause ("ISO 26262-8, clause 11.4.5"); no sentence, table or figure of it is
reproduced, everything is said in this document's own words, and the consistency test `safety-tools` refuses
references to tables or figures of the standard. Where a decision is the user's and not the standard's, it is
recorded as such (section 9.2).

**No invented numbers.** No failure rates, no probabilities of tool errors, no coverage percentages. The detection
levels are ordinal classes with the reasoning written out, like the diagnostic-coverage classes of `fmea.md`.

---

## 0. Result in one page

| Question of the task | Answer |
|---|---|
| Tools classified | 8 named in the task (nxconfig, bswgen, rtegen, dbcgen, blgen, a2lgen, schedana, Studio) plus 14 more that also produce, transfer or check part of the image or its evidence (section 2) |
| Result today | **At issue 1 no generator reached TCL 1 on its own.** All generators whose output reaches the image have tool impact (TI2) and a **medium** error detection (TD2) → **TCL 2**. Since proposal E (PLAT-81, SYS-49 update) **dbcgen** is TD1 → **TCL 1**; the others stay TCL 2 until their proposals are done. The strongest safeguards exist for the items the concept calls safety-relevant (safe outputs table, ROM-check reference, bootloader validity record), but only on the host or only in part in the linked image (section 3, 4) |
| Chosen route | **Detect rather than qualify** for the safety-relevant generated items: checks of the *output* that do not use the generator's own logic (independent expectation, check in the linked image). They survive refactoring of the generators and let those items reach TCL 1. The rest of each generator's output stays TCL 2 and is covered by **qualification through validation** — the existing test suites with own profiles and fault injection, written up as a qualification record per tool (section 5, proposal G). The standard supports route 1 as the preferred alternative to qualification (ISO 26262-8, clause 11.4.1; ISO 26262-10, clause 13); for route 2 it lists validation only as *recommended* at ASIL B, so the record must carry a rationale for choosing it (TN3) |
| Norm questions TN1–TN5 | **answered** from ISO 26262-8, clause 11 (section 9.2): classification per use case confirmed, run-time mechanisms credited only where the error shows deterministically in the integrator's own tests (TN1); compiler: TCL 3 today, the standard allows qualification or a lower TD through target-environment tests — the choice is the user's (TN2, decision open); validation alone is admissible for TCL 2 at ASIL B only with a written rationale, and the record content is fixed (TN3); the host compiler's TD1 does not rest on the mutation runs alone → **TD2** (TN4); the integrator gets the Embedded Coder configuration neXtBSW applies and a statement of what neXtBSW does **not** verify (TN5, AoU-29) |
| Target after the proposals | nxconfig, bswgen, blgen, rtegen, dbcgen, romCheckImage, canflash, Studio: **TCL 1** for the safety-relevant items; a2lgen, diaggen, schedana: TCL 1 under a proposed assumption of use, else TCL 2; host-test compiler: TCL 1 after I and K; compiler and linker: **TCL 3** until the user's decision (TN2), TCL 2 reachable with proposal I |
| Corrections to the concept | (1) there was **no checked-in reference output** of any generator — tests asserted derived expectations and properties (finding **TF1**, closed by PLAT-78); (2) the MPU descriptors are **not generated** — only the region count and the RAM layout they are computed from (**TF2**); (3) `intgen` does not exist (**TF3**) |
| Missing safeguards | twelve proposed tasks A–L (section 7; A–H from issue 1, I–L from the norm questions), highest value: the safe outputs table checked entry by entry in the linked image (A, **done**: PLAT-77), reference output of the safety-relevant generated files (B, **done**: PLAT-78), a configuration identity in the image (C, open); D (linked images against mcudb, PLAT-80), E (dbcgen over every project DBC, PLAT-81) and F (RTE timing and interrupt priorities against the configuration, PLAT-82) are **done** as well. From the norm questions: I (host tests of the safety modules in the target environment), J (compiler identification and options checked in the image), K (mutation run for every safety module), L (known malfunctions of the external tools); G is raised to P2, because the qualification report is a required work product for every TCL 2 tool |
| Assumptions of use | four proposed for DOC-19: AoU-26 (tool checks run on the integrator's own project; since the norm check also: he verifies the validity of this predetermined classification for his item and keeps the tool versions and configuration of the record), AoU-27 (Studio display is not evidence), AoU-28 (calibration and diagnostic write access to safety-relevant values), AoU-29 (Embedded Coder classification and verification of the model code are the integrator's) — section 6 |
| Requirements | the missing safeguards are entered as requirements SSR-080 … SSR-084 in [`ssr.sdoc`](../requirements/safety/ssr.sdoc) (section 8); SSR-080, SSR-081, SSR-083 and SSR-084 are **implemented** (PLAT-77, PLAT-78 with PLAT-82, PLAT-80, PLAT-81), SSR-082 is **implemented** since PLAT-79 (2026-10-01); the matrix was regenerated |
| Verification | **mock/analysis only**: code and test review against `f8ee573`, norm check against `62a1b53` with parts 6, 8 and 10 of ISO 26262, and the consistency test `safety-tools` (`doc/safety/test/test_tool_classification.py`). No board, no hardware |

---

## 1. Method

### 1.1 The three questions per tool and use case

A tool is classified **per use case**, not once for all: the same program can be harmless as a planning aid and
relevant when its verdict is used as evidence. The standard asks for exactly this: the usage plan names the use
cases, and impact and detection are determined from the intended usage (ISO 26262-8, clauses 11.4.4.1 and 11.4.5;
ISO 26262-10, clause 13). A classification is valid only for the use cases it names; a **new use case** of a tool
needs a new classification (impact analysis) before it is relied on.

1. **Tool impact (TI).** Can a malfunction of the tool, in this use case, put an error into the product or let an
   error in the product go unnoticed? The class names are those of ISO 26262-8, clause 11.4.5.2 (issue 1 called
   them TI0 and TI1; the meaning is unchanged):
   - **TI1** — no: there is an argument that the output does not reach the image, and no verification result that
     the safety argument relies on depends on it.
   - **TI2** — yes, in all other cases. Every generator whose output is compiled into the image is TI2, and so is
     every verifier whose "pass" is used as evidence.
2. **Tool error detection (TD).** If the tool does malfunction, how confident are we that the erroneous output is
   prevented or detected **before it is used** — by a check that does not trust the tool?
   - **TD1** — high: an independent check looks at exactly the erroneous item (an expectation derived without the
     generator's logic, a check in the linked image, a run-time mechanism that detects this very error class),
     and the check has been shown to fail on a broken generator or broken output (fault injection).
   - **TD2** — medium: the output is exercised and many errors would show (compiled and executed on the host,
     validated input, consistency between consumers), but an error that is consistent with itself could pass.
   - **TD3** — low or none.

   Where the choice of TI or TD is doubtful, the more conservative class is taken (ISO 26262-8, clause 11.4.5.3).
   Our TD1 is deliberately stricter than the standard's wording: it also asks for fault injection that shows the
   check fires.
3. **Tool confidence level (TCL)**, derived — never chosen (ISO 26262-8, clause 11.4.5.4). In this document's
   words: a use case without impact (TI1) or with high detection (TD1) is **TCL 1**; with impact (TI2), medium
   detection gives **TCL 2** and low detection **TCL 3**. The consistency test applies exactly this rule to every
   row of section 3.

   TCL 1 needs no qualification method; TCL 2 and TCL 3 need one (ISO 26262-8, clause 11.4.6.1). The standard
   offers four alternative methods — increased confidence from use (clause 11.4.7), evaluation of the tool's
   development process (clause 11.4.8), **validation of the tool** (clause 11.4.9) and development according to a
   safety standard — and grades them by ASIL. **At ASIL B**, for TCL 2 as for TCL 3, the first two are the highly
   recommended ones; validation and development according to a safety standard are only recommended. Because the
   methods are alternatives, a combination fitting the ASIL may be chosen, the higher-graded ones should be
   preferred, and the choice needs a written rationale (ISO 26262-8, clause 4.3). For a project whose generators
   change every week, increased confidence from use is not available (it needs an unchanged specification and holds
   only for the evaluated version, clauses 11.4.7.2 and 11.4.7.4), and no assessment of our development process
   exists; validation is therefore the realistic method, **with a rationale** (section 5, TN3). The standard itself
   points to the other way out: add checks of the tool output to the development process until TD1, so that no
   qualification is needed (ISO 26262-10, clause 13) — that is route 1.

A **run-time mechanism** (`SM-*`, K7) counts towards TD only under two conditions (TN1). First, it must detect the
specific error class of the tool — SM-51 detects a wrong validity record because the bootloader recomputes it; a
mechanism whose own configuration comes from the same generator does **not** count (a wrong safe outputs table is
walked faithfully by SM-23; the standard asks for such interdependencies to be considered, ISO 26262-8, clause
11.4.5.2, note 3). Second, the error must show **deterministically before release**: the standard names measures
of the development process — tests, reviews, checks in the tool — and detection only in the field is not among
them (ISO 26262-8, clause 11.2). SM-51 and SM-14 qualify, because a wrong record or reference CRC stops **every** start of the image, so the
integrator's first test on the target sees it (AoU-26). The watchdog behind schedana does not: a starved task shows
only under the load that causes it, possibly first in the field — no TD credit.

### 1.2 Kinds of safeguard

Concept section 8.4 lists four safeguard kinds that already exist. They are refined here into eight, so that each
tool can be credited precisely:

| Kind | Safeguard | Independent of the generator? |
|---|---|---|
| **K1** | **Independent expectation:** the expected content is derived from the configuration by another path — reference-manual register addresses, `cantools` for the bit layout, a Python reference model, the example values of a specification | yes |
| **K2** | Generated code **compiled and executed on the host** against the generators' own test profiles (not only the eval board) | partly: it tests behaviour, but the expectation is often written by the same author as the generator |
| **K3** | **Validation before generation**: a wrong or incomplete configuration is refused with a message carrying the YAML path, instead of being generated | no (same tool) — it prevents configuration errors, not tool errors |
| **K4** | **Build-time enforcement in the target code**: `static_assert`, `#error`, counts that must match (e.g. `NX_SAFE_OFF_OUTPUT_COUNT`, SM-24), derivative facts checked against the device header | yes, if the checked fact comes from a different source than the generated value |
| **K5** | **Check in the target artefact**: the linked ELF or HEX is inspected (disassembly, symbols, section placement, checksums) | yes |
| **K6** | **Fault injection**: a broken generator, generated file or production code must make the test fail (mutants) | shows that K1/K2/K5 detect |
| **K7** | **Run-time mechanism** that detects the error class of the tool in the product (e.g. SM-51, SM-14) | yes, if not configured by the same generator; credited for TD only if the error shows at every start (section 1.1) |
| **K8** | **Controlled tool environment**: pinned versions of Python packages (`tools/ci/constraints.txt`, checked by `python -m tools.ci check`), one interpreter per repository (`.venv`), generators run by the build only | prevents, does not detect |

### 1.3 What was looked at

For every tool: where the build or the user runs it (`CMakeLists.txt`, `bootloader/CMakeLists.txt`,
`bootloader/updater/CMakeLists.txt`, `ecu/application/CMakeLists.txt`, `ecu/uds/CMakeLists.txt`,
`ecu/xcp/CMakeLists.txt`, `cmake/NxRomCheck.cmake`, `tools/studio/core/commands.py`), which generated files it
writes and which mechanism consumes them, and which entries of `tools/test/tests.yaml` test it and how (read in the
test sources: expectation derived how, mutants yes or no, own profiles yes or no).

### 1.4 Planning data of the tool usage

The standard asks the usage of every tool to be planned with its identification and version, its configuration,
its use cases, its execution environment, the maximum ASIL it can violate, and the qualification method where one
is needed (ISO 26262-8, clause 11.4.4.1); it also asks for a description of the tool, its user guide, its
behaviour under anomalous conditions, its known malfunctions and the measures against them (clause 11.4.4.2). The
use cases and measures are section 3; the rest is here, per group of tools. What is marked *missing* is part of
proposal G (neXtBSW's tools) or L (external tools).

| Group | Identification and version | Configuration | Environment | Description, user guide | Anomalous conditions, known malfunctions |
|---|---|---|---|---|---|
| neXtBSW generators and checks (nxconfig, bswgen, blgen, rtegen, dbcgen, diaggen, a2lgen, schedana, romCheckImage, canflash, elfcheck, mcudb tools, swc tools, trace, test runner, boardtest) | the repository commit; one version for all, no separate tool releases | the project, board and derivative files are the tool **input**, not its configuration; command-line options as the build passes them (`CMakeLists.txt`, `cmake/*.cmake`) | `.venv` with the packages of `tools/ci/constraints.txt` (checked by `python -m tools.ci check`), workstation and CI (`.github/workflows/ci.yml`) | README per tool, `doc/reference` pages | refusal of wrong input with YAML path (K3) is tested; malfunction list *missing* (the fixes are in `CHANGELOG.md`, not collected) |
| Studio | version in `tools/studio/version.py`, packaged `.exe` | user settings | packaged interpreter with **frozen copies** of the tool packages (TF9) | `tools/studio/README.md` | a packaged Studio older than the repository (AoU-27) |
| arm-none-eabi-gcc, binutils | 11.4, NXP S32DS build (`NX_ARM_GCC_BIN`) | compile and link options of the toolchain files in `cmake/toolchains` and the family ports; `-Werror` | Windows workstation; the version is not checked by the build (*missing*: proposal J) | vendor manuals | *missing*: no review of the known wrong-code faults of this version (proposal L) |
| zig cc (host tests) | `ziglang` package, clang 21.1.0, pinned in `constraints.txt` | options of `tools/test` | `.venv` | vendor documentation | *missing* (proposal L) |
| cppcheck, clang-tidy | cppcheck 2.17.1 (wheel 1.5.1) and clang-tidy 22.1.8, pinned in `tools/misra/requirements.txt` | `tools/misra/rules.yaml`, `baseline.yaml`, `deviations.yaml` | CI and workstation | `doc/safety/static-analysis.md` | partly: `misra` runs synthetic violations (proposal H) |
| StrictDoc | pinned in `tools/ci/constraints-docs.txt` | `doc/requirements/strictdoc_config.py`, grammar `nextbsw.sgra` | own venv (`NX_STRICTDOC_VENV`, `python -m tools.ci setup --tool strictdoc`) | `doc/requirements/migration-plan.md` | UID cross-check in `tools/trace` |
| CMake, Ninja | versions of the workstation, **not pinned** | `CMakePresets.json` | workstation, CI | vendor documentation | build-attribute check of the image (proposal D, done) |

**Maximum ASIL.** Every TI2 use case of this document is classified for **ASIL B**, the target of the element
(concept section 2.1). For an element out of context this is a *predetermined* ASIL, which the standard allows but
asks to be verified for the actual item (ISO 26262-8, clause 11.4.4.1, note 3, and clause 11.4.2) — that is the
integrator's step in AoU-26. For ASIL C or D the classification stays valid, but the qualification methods of the
TCL 2 and TCL 3 tools would have to be chosen again, because the grading of the methods shifts towards validation
at those levels (clause 11.4.6.1).

---

## 2. Inventory

| Tool | Location | Run by | Role | Output that reaches the image or the evidence |
|---|---|---|---|---|
| nxconfig | `tools/nxconfig` | CMake before `project()` (`nxconfig cmake`), imported by every generator, Studio | validator and generator | build settings (`NX_*`: family, core flags, derivative, flash tools), derived values used by all generators (clock plan, CAN bit timing, memory partitioning and target ID in `memory.py`, sensor conversion, watchdog relation check) |
| bswgen | `tools/bswgen/bswgen.py`, `modules.py`, `stm32gen.py`, 39 templates | CMake at configure time | generator | board headers: pins and pin functions, **safe outputs table** (`safeOutputsConfiguration.h`), **derivative facts** (`derivativeFacts.h`: MPU region count, NVIC priority bits), **interrupt priorities** (`interruptPriorities.h`), **ADC reference band** (`adcReferenceConfiguration.h`), clocks, CAN, SPI, UART, PWM, ADC, module integration (`modules.cmake`) |
| blgen | `tools/bswgen/blgen.py` | CMake (bootloader, updater) | generator | `bl_memory.h`, `bl_memory.ld`, `memoryLayout.h` (also read by the application linker script, the updater and `canflash`), `bl_board_*.h`, `bl_diag.h`, `bl_security.h` |
| rtegen | `tools/rtegen` | CMake at configure time | generator | RTE: **task list, priorities and periods** (`RteTasks.h`, `Config.h`), the **SM-05 deadlines** (the periods), **signal qualifier** computation (SM-30), E2E configuration per signal (SM-31), sensor conversion, ports and copies |
| dbcgen | `tools/dbcgen` | imported by rtegen | generator | `CanDb.c/h`: pack and unpack of every CAN signal used |
| diaggen | `tools/diaggen` | CMake (`ecu/uds`) | generator | UDS job tables (0x22/0x2E/0x31/0x27), session and security masks, `UdsConfig.h` |
| a2lgen | `tools/a2lgen` | CMake after link (`ecu/xcp`, target `a2l`) | generator (off-target) | `app.ecu.a2l`: addresses and conversions a calibration tool uses to write calibration parameters over XCP |
| schedana | `tools/schedana` | user, Studio | verifier | verdict whether the task set meets its deadlines (argument for AoU-08) |
| Studio | `tools/studio` | user | editor, launcher, display | writes project, board, derivative, `rte.yaml`, `swc.yaml`, DBC; runs the same generator commands as CMake; shows validation and test state |
| romCheckImage | `platforms/s32k1xx/port/safety/safeMemory/tools/romCheckImage.py` | CMake post-link (`nx_rom_check_image`) | generator | the checksum list and the **reference CRC of SM-14** in the application image |
| calseg | `tools/calseg/calseg.py` | CMake post-link (`seal`, before romCheckImage and the record), user (`hex`, `merge`) | generator | the **CALV header of the calibration segment** (magic, layout id, dataset id, length, CRC of the values) checked at start-up by the calibration check (SYS-89, AoU-28) and, since SYS-91, in every ROM check pass as the segment's own reference (the segment is no longer in the ROM check list); the segment HEX of a dataset (`app.ecu.cal.hex`); `merge` (SYS-91) is a test aid that puts another or a damaged segment into a download image |
| canflash | `tools/canflash/canflash.py` | CMake post-build (`record`), user | generator and transfer | the **validity record** checked by SM-51; download of the image; `compat` compares the tool's layout with the target |
| mcudb tools | `mcudb/tools` (`svd2header.py`, `pinmux_hal_nxp.py`, `pinmux_stm32_open_pin_data.py`, `rtd_adapter.py`) | user, when a derivative is added | generator | derivative data and device headers: pin functions, peripheral instances, flash and RAM map, MPU region count |
| swc tools | `tools/swc` (`swc_yaml.py` and MATLAB scripts) | user, Studio | generator | the component description that rtegen reads (ports, qualifier constants) |
| arm-none-eabi-gcc 11.4 and binutils | `NX_ARM_GCC_BIN` | CMake | compiler, assembler, linker, preprocessor of the linker script | the image itself (concept 8.3) |
| CMake, Ninja | workstation | user, CI | build | composition of the image (which files, which flags) |
| Python, Jinja2, PyYAML, ruamel.yaml | `.venv` | every generator | run-time of the generators | as the generators |
| zig cc (clang 21.1.0) | `ziglang` package | host tests | test compiler | host-test verdicts |
| cppcheck, clang-tidy | `tools/misra` | CI | verifier | static-analysis verdict (PLAT-74) |
| test runner, coverage | `tools/test` | user, CI | verifier | which tests ran and passed, coverage figures |
| trace | `tools/trace` | CI | verifier of the argument | traceability matrix |
| e2e reference model | `tools/e2e/e2e_ref.py` | tests | verifier | expectation for the E2E library |
| boardtest | `tools/boardtest` | user at the board | verifier | board evidence named for SSR-040 |

Not classified, because their output neither reaches the image nor serves as safety evidence (**TI1, TCL 1**):
`tools/docgen`, `tools/distcheck`, `tools/openbswsync`, `tools/todo`, `tools/logcheck` (logger line lengths),
`tools/ci` (it runs the others; its verdict is the test runner's), `tools/a2lgen/xcp_master.py` (board helper).
MATLAB/Simulink with Embedded Coder generates the **application** code; the application is the integrator's (concept
section 1.2, out of scope), and so is the classification of his code generator.

---

## 3. Classification per tool

The summary table is the result; the subsections below give the reasoning. "TCL target" is the level after the
proposals of section 7 named in the last column. The consistency test `safety-tools` checks that every row applies
the table of section 1.1, that every test named exists in `tools/test/tests.yaml` and that every tool the build runs
is in this table.

<!-- tool-summary:start -->
| Tool | Path | TI | TD today | TCL today | TCL target | Safeguards today | Tests | Proposals |
|---|---|---|---|---|---|---|---|---|
| nxconfig | `tools/nxconfig` | TI2 | TD2 | TCL 2 | TCL 1 | K2 K3 K4 K6 K8 | `nxconfig`, `nxconfig-watchdog`, `nxconfig-build`, `nxconfig-diag`, `nxconfig-xcp`, `flash-targets`, `system-timer-80mhz`, `can-bit-timing` | C, D, G |
| bswgen | `tools/bswgen` | TI2 | TD2 | TCL 2 | TCL 1 | K1 K2 K3 K4 K5 K6 | `bswgen`, `bswgen-mpu`, `bswgen-interrupts`, `bswgen-reference`, `safe-outputs-order`, `safe-outputs-table`, `elfcheck-timing-host`, `elfcheck-timing-demo`, `target-demo` | A, B, C, F, G |
| blgen | `tools/bswgen/blgen.py` | TI2 | TD2 | TCL 2 | TCL 1 | K2 K3 K5 K6 K7 | `blgen`, `blgen-stm32`, `application-linker`, `bootloader-host`, `bl-update-host`, `bl-update-mutations` | B, C, D, G |
| rtegen | `tools/rtegen` | TI2 | TD2 | TCL 2 | TCL 1 | K1 K2 K3 K5 K6 | `rtegen`, `rtegen-diag`, `rtegen-reference`, `rte-deadline`, `e2e-ref`, `demoswc-sil`, `safety-context-elf`, `elfcheck-timing`, `elfcheck-timing-host`, `elfcheck-timing-demo`, `elfcheck-timing-bt148` | B, C, F, G |
| dbcgen | `tools/dbcgen` | TI2 | TD1 | TCL 1 | TCL 1 | K1 K2 K6 | `dbcgen`, `dbcgen-projects`, `rtegen` | B, E |
| diaggen | `tools/diaggen` | TI2 | TD2 | TCL 2 | TCL 2 | K2 K3 K6 | `diaggen`, `nxconfig-diag`, `rtegen-diag` | G |
| a2lgen | `tools/a2lgen` | TI2 | TD2 | TCL 2 | TCL 2 | K2 K5 | `a2lgen`, `xcp-host` | G |
| schedana | `tools/schedana` | TI2 | TD2 | TCL 2 | TCL 2 | K2 K6 K7 | `schedana` | G |
| Studio | `tools/studio` | TI2 | TD1 | TCL 1 | TCL 1 | K3 K8 | `studio-core`, `studio-gui-model`, `studio-gui-window`, `studio-gui-project` | - |
| romCheckImage | `platforms/s32k1xx/port/safety/safeMemory/tools/romCheckImage.py` | TI2 | TD1 | TCL 1 | TCL 1 | K1 K5 K6 K7 | `rom-check-image`, `rom-check` | - |
| calseg | `tools/calseg/calseg.py` | TI2 | TD1 | TCL 1 | TCL 1 | K5 K6 K7 | `calseg`, `calibration-segment` | - |
| canflash | `tools/canflash/canflash.py` | TI2 | TD1 | TCL 1 | TCL 1 | K2 K6 K7 | `canflash-tool`, `bootloader-host`, `bl-update-host`, `bl-update-mutations` | - |
| mcudb tools | `mcudb/tools` | TI2 | TD2 | TCL 2 | TCL 2 | K1 K4 | `mcudb`, `mcudb-rm4x`, `mcudb-pinmux-nxp`, `mcudb-pinmux-stm32`, `mcudb-svd2header`, `device-header` | G |
| swc tools | `tools/swc` | TI2 | TD2 | TCL 2 | TCL 2 | K2 K3 | `swc-yaml`, `demoswc-sil` | G |
| arm-none-eabi-gcc and binutils | `cmake/toolchains` | TI2 | TD3 | TCL 3 | TCL 2 | K5 | `safe-outputs-order`, `safety-context-elf`, `application-linker`, `rom-check-image` | D, I, J, L |
| CMake and Ninja | `CMakeLists.txt` | TI2 | TD2 | TCL 2 | TCL 1 | K4 K5 | `nxconfig-build`, `target-demo` | D |
| zig cc (host tests) | `tools/test` | TI2 | TD2 | TCL 2 | TCL 1 | K2 K6 | `run-tests-selbst` | I, K, L |
| cppcheck and clang-tidy | `tools/misra` | TI2 | TD2 | TCL 2 | TCL 2 | K6 K8 | `misra` | G, H, L |
| test runner and coverage | `tools/test` | TI2 | TD1 | TCL 1 | TCL 1 | K6 | `run-tests-selbst`, `coverage-selbst` | - |
| trace | `tools/trace` | TI2 | TD1 | TCL 1 | TCL 1 | K6 | `trace-tool`, `safety-trace` | - |
| StrictDoc | `doc/requirements` | TI2 | TD1 | TCL 1 | TCL 1 | K1 K6 K8 | `trace-tool`, `trace-sdoc`, `safety-trace` | - |
| elfcheck | `tools/elfcheck` | TI2 | TD1 | TCL 1 | TCL 1 | K1 K5 K6 | `elfcheck`, `elfcheck-demo`, `elfcheck-bt148`, `elfcheck-timing`, `elfcheck-timing-host`, `elfcheck-timing-demo` | - |
| e2e reference model | `tools/e2e/e2e_ref.py` | TI2 | TD1 | TCL 1 | TCL 1 | K1 | `e2e-ref`, `e2e-host`, `e2e-mutations` | - |
| boardtest | `tools/boardtest` | TI2 | TD2 | TCL 2 | TCL 2 | K2 | `boardtest` | G |
<!-- tool-summary:end -->

Python, Jinja2, PyYAML and ruamel.yaml are classified **with the generators** they run: an error of the template
engine shows in the generated file, and every check of the generated file is also a check of them. K8 (pinned
versions) applies to all of them.

### 3.1 nxconfig

| | |
|---|---|
| Use cases | (a) validator of project, board and derivative before every build; (b) writer of the build settings (`nxconfig cmake`: family, core, `-mcpu`/`-mfpu`, derivative, debug probe); (c) library of derived values used by bswgen, blgen, rtegen and diaggen (clock plan, CAN bit timing, memory partitioning, target ID, sensor conversion, watchdog timing relation) |
| Impact | TI2 in all three. (a) a check that does not fire lets a wrong configuration through; (b) wrong core flags compile the image for the wrong core; (c) a wrong derived value is wrong in every generated file that uses it — the clock plan sets every time in the image |
| Detection today | 236 unit tests with synthetic profiles in `nxconfig`, each check with a deliberately broken input and the YAML path compared (K3, K6); the watchdog relation with counter-examples (`nxconfig-watchdog`); generated build settings and generated files of every project configured (`nxconfig-build`); clock and bit timing compiled and run at four system clocks (`system-timer-80mhz` and siblings, `can-bit-timing`, K2); versions pinned (K8) |
| Why TD2 | the expectations of the derived values are written by the same author and mostly from the same formulas; a consistent error in the clock plan or the memory model would pass the host tests. The board runs of the changelog are the only independent check of the clock plan, and they exist for the eval board only |
| To reach TCL 1 | derived values that set safety-relevant times and addresses are checked in the image or by a second path: the memory layout (proposal D), the configuration identity so a reviewed configuration can be recognised in the image (C). The rest stays TCL 2 with the validation record (G) |

### 3.2 bswgen

| | |
|---|---|
| Use cases | generator of the board headers at configure time (also `stm32gen.py` for the stm32 family, `modules.py` for the module composition) |
| Impact | TI2. Safety-relevant outputs: the safe outputs table (SM-23, used by three switch-off paths), the `safe_off` count (SM-24), the MPU region count (SM-12, SM-13), the interrupt priorities, the ADC reference band (SM-18), the pin functions of safe outputs and PWM pins (SM-21/SM-22 lock and check them only at demo scope, F4) |
| Detection today | **safe outputs table:** the strongest safeguard in the repository — `BswGenSafeOutputsHostTest` (in `bswgen`) compiles the real table definition of the port on the host and compares it with an expectation read from `board.yaml`, the project's `use` and the module manifests, with register addresses taken **from the reference manual, not from the generator** (K1), over five profiles including a board without power outputs, with mutants (PCOR/PSOR swapped and others, K6); the `safe_off` count is enforced by the build (K4, SM-24, `target-demo`); `safe-outputs-order` checks in the **linked** image that the table is read-only, is the project's strong table, has as many entries as `NX_SAFE_OUTPUT_COUNT` and ends with the end entry (K5). **Since PLAT-77 (proposal A):** `safe-outputs-table` reads every entry (address, value, gate register, gate mask, order) from the **linked** image and compares it with the bswgen-independent expectation `tools/bswgen/test/safe_outputs_expectation.py` (reference-manual addresses, cross-checked against the NXP device headers), for two own profiles (one of them S32K116, Cortex-M0+), the demo, charge, S32K148 and S32K116 profiles with the target compiler, and for the application ELFs of the demo, charge and S32K148 builds (`safe-outputs-table-charge`, `safe-outputs-table-bt148`); generator, generat and image mutants turn it red (K1, K5, K6). Since SYS-49 `BswGenSafeOutputsHostTest` uses the same expectation instead of its own copy of the rules. **MPU region count:** encoded for 8/12/16 regions, a derivative with an unknown count is refused, the MPU header does not compile without the facts and a device header with too few descriptors does not compile (K4, `bswgen-mpu`). **Interrupt priorities:** ranks in order, a rank beyond the derivative's priority bits does not compile (K4, `bswgen-interrupts`); **since PLAT-82 (proposal F)** the ranks, the syscall level and the NVIC priority bits the application image was compiled with are read from its debugging information (`.debug_macro`) and compared with the project's ranks and `mcudb`, and the NVIC level of every rank is evaluated by the target compiler from the generated headers (probe) and compared with `max_syscall_level + rank` — by `tools/elfcheck/timing.py`, which imports no generator; own profiles (two UART ranks, S32K116 with 2 bits and syscall level 0), the S32K148 build test and the demo; generat, template and configuration mutants turn it red (K1, K5, K6; `elfcheck-timing-host`, `elfcheck-timing-demo`). **Reference output** of the safety-relevant headers per profile since PLAT-78 (proposal B, `bswgen-reference`). **ADC reference band:** against the headers of an own profile and the demo, with mutants of `SafeSystem.cpp` (K2, K6). 194 unit tests in `bswgen` over own profiles in `tools/bswgen/test/profiles` |
| Why TD2 | the safe outputs table is TD1 since PLAT-77 (finding **TF5** closed: its content is checked in the linked image) and the interrupt priorities since PLAT-82 (finding **TF7** closed: rank, syscall level and bits of the image and the level of every rank against the configuration); a changed header is noticed since PLAT-78 (finding **TF1** closed). Still TD2: the pin functions of the safe outputs have no independent check beyond the generator's own validation against the pinmux data, and nothing ties an image to the configuration it was generated from (C) |
| To reach TCL 1 | A (**done**, PLAT-77), B (**done**, PLAT-78), F (**done**, PLAT-82), C (configuration identity, open); the pin functions of the safe outputs with SYS-24. Everything else stays TCL 2 with G |

### 3.3 blgen

| | |
|---|---|
| Use cases | generator of the bootloader's board part and of the **memory layout** that bootloader, application linker script, updater and `canflash` all read |
| Impact | TI2. A wrong layout places the application, the validity record or the checksum list wrongly; the bootloader could erase or start the wrong area. SM-51 checks the image against the record — but at the addresses the same layout names |
| Detection today | 22 unit tests in `blgen` (own profiles, target ID as CRC-32 of the identifying string), `blgen-stm32` links a stm32 bootloader; `application-linker` runs the real `arm-none-eabi-ld` over the template with one to four RAM regions (K5); `bootloader-host`, `bl-update-host` and the mutation run `bl-update-mutations` exercise the bootloader with the generated layout (K2, K6); `canflash compat` compares the tool's layout with the running target (K7, board). nxconfig validates the partitioning against the derivative's flash map (K3) |
| Why TD2 | **one generated source feeds all consumers** (finding **TF6**): they are consistent by construction, so a layout that is wrong for the derivative is wrong in the bootloader, the application and the tool alike, and every consumer test passes. The only independent source is the derivative's flash map, and it is checked by nxconfig, which is part of the same chain |
| To reach TCL 1 | D (linked bootloader, application and updater ELFs checked against the derivative map from `mcudb` and against each other), B, C |

### 3.4 rtegen

| | |
|---|---|
| Use cases | generator of the RTE from `rte.yaml`, the component descriptions and the DBC (through dbcgen) |
| Impact | TI2. Safety-relevant outputs: the task list with priorities and periods; the SM-05 deadlines, which **are** the configured periods; the qualifier computation of SM-30 (converter limits, amplifier range, breakpoints); the E2E configuration of each protected signal (SM-31); the copies at task boundaries |
| Detection today | 206 unit tests in `rtegen` that compile and run the generated RTE on the host in 14 host-test programs over own profiles (K2) — PWM, CAN, qualifier and qualifier stall, types, parameters, sensors, ECU state, multitask, BSW services, modes, implicit access, communication control; the sensor values the host test expects are those of the independent Python reference `tools/nxconfig/sensor.py`, with mutants of the generated `RteConfig.cpp` (K1, K6); the E2E library is checked against the example values of the specification through the reference model (`e2e-ref`, `e2e-host`, `e2e-mutations`); the demo task list (order, hence priorities) is pinned by a test (`test_demo_task_list_is_unchanged`); `rte-deadline` runs the real `RteSystem.cpp` against a virtual clock with an own two-task profile and mutants; `demoswc-sil` runs model code and RTE together; `safety-context-elf` checks the event mailbox placement in the linked image (K5). **Since PLAT-78 (proposal B)** `RteTasks.h`, `Config.h`, `RteConfig.cpp` and `CanDb.c/h` of four profiles are compared with a reviewed reference (`rtegen-reference`). **Since PLAT-82 (proposal F)** `tools/elfcheck/timing.py` reads `rte::TASKS`, `rte::RUNNABLES` and `rte::MODE_RUNNABLES` from the linked image (names, called functions, periods = SM-05 deadlines, offsets, task index, tick counts) and the context number (FreeRTOS priority) of **every** task, BSW tasks included, from the `TASK_*` enumerators of its debugging information, and compares them with an expectation derived without rtegen from `rte.yaml`, `tasks.yaml`, the component ARXML and, since PLAT-102, the `swc.yaml` of hand-written C components (own reader, not rtegen's or `swc_yaml.py`); the expectation itself agrees with the reviewed reference output of six profiles (two with C components); demo and S32K148 images, the firmware of `test/c_swc` (C component next to a Simulink one) and the own profiles `test/multitask` and `test/c_swc` built with the target compiler for Cortex-M4 and M0+; image, generat, template and configuration mutants turn it red (K1, K5, K6; `elfcheck-timing*`) |
| Why TD2 | the task list, the priorities, the periods and the SM-05 deadlines are TD1 since PLAT-82 (finding **TF7** closed for the RTE part), and a change of any generated RTE file is noticed since PLAT-78 (**TF1** closed). Still TD2: the E2E *configuration* (which signal, which data ID, which profile) and the qualifier limits are exercised and guarded against change by the reference, but not compared with an independent source; nothing ties an image to its configuration (C) |
| To reach TCL 1 | B (**done**, PLAT-78), F (**done**, PLAT-82), C (open); an independent check of the E2E configuration — today it is checked at run time by the partner ECU (a mismatched data ID or counter makes the E2E check fail there), which is independent but only at integration |

### 3.5 dbcgen

| | |
|---|---|
| Use cases | generator of pack and unpack code for every CAN signal a project uses |
| Impact | TI2. A wrong bit position, byte order, sign or scaling delivers a wrong value with a good qualifier; E2E (SM-31) protects the bytes, not their interpretation, so it does **not** detect this error class |
| Detection today | the generated code is compiled and run with ziglang and compared with the exact Python model, and the model is compared with `cantools` as **independent reference** for the bit layout (K1, K2, `dbcgen`); rtegen's CAN host tests use it (`rtegen`). **Since PLAT-81 (proposal E):** `dbcgen-projects` finds every DBC under `projects/` and `tools/` and every ECU description with `can: dbc:`, and packs and unpacks every signal of the messages the node sends or receives with the generated code and with `cantools` (limits of the bit field, DBC minimum and maximum, raw and physical zero, random values with a fixed seed per DBC; raw bytes and physical values, E2E signals and the bit offset rtegen hands to the E2E library included); eight mutants of the generat of the demo DBC turn it red (K1, K2, K6) |
| Why TD1 | an independent decoder checks exactly the erroneous item (the bit layout and scaling of every used signal) for the DBC of every project and test profile, and the check is shown to fail on broken output (finding **TF8** closed). Remaining limits: messages dbcgen cannot generate (multiplexed, IEEE float) are skipped and listed, and a description that uses one turns the test red; a new project DBC is covered automatically, but only once it is in the repository (AoU-26) |
| To reach TCL 1 | reached with E (PLAT-81); B (reference output of `CanDb.c/h`) remains as a guard against unasserted changes |

### 3.6 diaggen

| | |
|---|---|
| Use cases | generator of the UDS job tables of the application (read and write data identifiers, routines, security access masks) |
| Impact | TI2 **if** a routine or a write reaches a safety-relevant output or value: a wrong session or security mask would allow it where it should not be allowed |
| Detection today | 22 unit tests with an own profile, every check with a deliberately broken input and its YAML path (K3, K6, `diaggen`); the description itself validated by `nxconfig-diag`; the RTE side of the jobs compiled and called on the host (`rtegen-diag`, K2) |
| Why TD2 | the masks are exercised with the author's expectation; nothing independent says which job may run in which session |
| Target | stays TCL 2 (validation record, G). Proposed **AoU-28**: a diagnostic job must not be the only barrier in front of a safety-relevant output — the application or the safe supervisor interlocks it. With AoU-28 met, the use case becomes TI1 |

### 3.7 a2lgen

| | |
|---|---|
| Use cases | writer of the A2L after link; a calibration tool uses it to write calibration parameters over XCP |
| Impact | TI2 when a **safety-relevant parameter** is calibrated with it: a wrong conversion (`COMPU_METHOD`) makes the tool write a wrong stored value while showing the right physical one |
| Detection today | addresses and tables come from the linked ELF itself (K5), for little and big endian images of an own profile (`a2lgen`); since RTE-26 also the calibration objects (curves, maps, axes, limits, value tables) of the profile `test/calib`, every A2L through the formal check `tools/a2lgen/a2lcheck.py` and read back from the image; the XCP slave refuses writes outside its writable regions and writes of partial elements (`xcp-host`, K7 for wrong addresses) |
| Why TD2 | a wrong conversion is consistent with itself and is not detected by the slave |
| Target | TCL 2, or **TI1** under the proposed AoU-28: XCP is disabled in the product, or a safety-relevant parameter is verified after calibration by reading it back through a path that does not use the A2L conversion (since SYS-89 the console command `cal values`, which prints every stored value as it is in memory; and the production profile of `memory.calibration` has no XCP at all) |

### 3.8 schedana

| | |
|---|---|
| Use cases | (a) planning aid while the task set is designed; (b) **evidence** that the safety task and the other tasks meet their periods under load (AoU-08) |
| Impact | (a) TI1. (b) TI2: a "schedulable" verdict that is wrong lets a task set pass whose safety task can starve |
| Detection today | 63 tests with mutants of the analysis itself (K6, `schedana`), a measurement path that compares computed with measured run times (K2); in the product the watchdog (SM-01) detects a starved safety task and SM-05 reports overruns — detection in the field, fail-safe, but after release, and only under the load that causes it; per section 1.1 (TN1) this is **no** TD credit, it only limits the consequence |
| Target | TCL 2 for use (b) with the validation record (G); **TCL 1** when the integrator's AoU-08 evidence is a measurement under worst-case load on his board and schedana only plans it. The measurement path of schedana makes that easy; DOC-19 should say so |

### 3.9 Studio

| | |
|---|---|
| Use cases | (a) editor of the configuration files; (b) launcher of the same generator, build and test commands as CMake and the command line (`tools/studio/core/commands.py` builds the same `nxconfig`, `bswgen`, `rtegen` calls); (c) display of validation messages, step state and test results |
| Impact | (a) TI2: a wrong write (wrong key, wrong element of a list) is a configuration error. (b) TI1 beyond the generators themselves: the build re-runs every generator at configure time, so what the Studio generated is overwritten. (c) TI1 **if** its display is not used as evidence |
| Detection today | (a) every write is validated live with the same functions as the command line (K3) and, independently, again by the build's nxconfig run; AoU-10 requires the change control of source code for the configuration files, and the Studio's own git page shows each change by role — a reviewed diff of the YAML is an independent check of what the Studio wrote. (c) The packaged application imports **frozen copies** of `nxconfig`, `rtegen`, `bswgen` and the other tool packages for its live validation (`tools/studio/build_exe.py`, `TOOL_PACKAGES`), while generators and builds run with the repository's interpreter: its live messages can differ from the build's (finding **TF9**) |
| Classification | TD1 for (a) **under AoU-10** (reviewed diff of every configuration change) → TCL 1; (c) excluded from the evidence by the proposed **AoU-27** |

### 3.10 romCheckImage (post-link step of SM-14)

TI2: it writes the reference CRC and the list of areas into the image. TD1: the CRC is checked against a known
vector and a register model of the CRC unit, the rules (gaps, content outside the areas, missing or implausible list →
build error, changed byte → mismatch) are tested on synthetic images and on the real linker template
(`rom-check-image`, K1, K5, K6); on the target, SM-14 recomputes the CRC at every start and a wrong reference leads
to `ROM_CHECK_FAILURE` and S1 held (`rom-check`, K7) — fail-safe. An area missing from the list would weaken SM-14
silently, and exactly that is a build error ("does not cover every flash byte"). The K7 credit meets both conditions
of section 1.1: SM-14 checks exactly the reference the tool wrote, with its own CRC computation, and a wrong
reference stops every start, so it cannot survive the integrator's first run on the target. SYS-91: the only
content the tool accepts outside the list is the area with its own reference, which the linker template defines
around the calibration segment; the tool refuses it if an area of the list overlaps it, and SM-14 checks it at run
time against its own header, not against the tool's reference. **TCL 1.**

### 3.11 canflash (validity record and transfer)

TI2: `canflash record` computes the validity record that SM-51 checks, and the download writes the image. TD1: the
bootloader recomputes the CRC over the image and compares the identifier and version field at every start (SM-51,
K7), which a wrong record or an interrupted download cannot pass; the bootloader and updater host tests and their
mutation run show that a broken check is detected (`bootloader-host`, `bl-update-host`, `bl-update-mutations`, K6).
`canflash` certifies whatever image it is given — a wrong image is the compiler's or a generator's error, not
canflash's. The K7 credit meets both conditions of section 1.1 (a wrong record stops every start, so the first run
on the target shows it). **TCL 1.** Key provisioning (`provision.py`, `keygen`, `unlock`) is security, not safety, and is not
classified here.

### 3.12 mcudb tools

TI2: the derivative data is safety-related configuration data (AoU-10) — flash and RAM map, pin functions, MPU region
count, peripheral instances. TD2: the derivative data carries a source per value for RM46L852 (`mcudb-rm4x`), the
generated device header is compared with the vendor header where the SVD collection is available
(`mcudb-svd2header`, K1 — skipped without it), and the pinmux importers are tested against their sources. The
derivative facts are also checked at compile time against the device header (K4, `device-header`, `bswgen-mpu`).
Stays **TCL 2**: a new derivative's data is reviewed value by value with its source (validation record, G).

### 3.13 swc tools

TI2 through rtegen: the component description sets ports and the qualifier constants. TD2: schema and messages with
YAML path, the demo component against its ARXML, the QLF constants against rtegen (`swc-yaml`), model code and RTE
run together (`demoswc-sil`). **TCL 2.** The MATLAB/Simulink code generation of the component itself is the
integrator's (section 2).

A second use case came up with TN5: `tools/swc/swc_create.m` **sets the Embedded Coder configuration** of every
component it creates (system target file, production hardware type, fixed-step discrete solver, export-function
model, non-finite numbers off, file logging off, identifier length, code generation only). That is part of the
configuration of the integrator's code generator, which the standard asks him to fix in his tool usage plan
(ISO 26262-8, clause 11.4.4.1). A wrong setting written by the script is an error in his tool configuration, which
his own classification of Embedded Coder has to cover — neXtBSW's part is to state which settings the script applies
and that he owns them from then on (AoU-29). No change to the classification of the swc tools.

### 3.14 Compiler, assembler, linker (arm-none-eabi-gcc 11.4, binutils)

The options of qualification are described in concept section 8.3; the norm answer is TN2 (section 9.2).
TI2. TD3 for the image in general: the host tests are built with a **different** compiler (zig cc / clang), so they
prove the logic, not the emitted target code; the checks that do look at the target code cover selected mechanisms
only — the disassembly of the fault handlers and the halt deadman (`safe-outputs-order`), section placement
(`safety-context-elf`), the real linker over the template (`application-linker`), the CRC over the real hex
(`rom-check-image`), the safe outputs table, the RTE tables and the priorities in the image (`safe-outputs-table`,
`elfcheck-timing*`) — plus the board runs. The standard asks that tests which do not run in the target environment
have their differences to it analysed and covered by later tests in the target environment (ISO 26262-6, clauses
9.4.5 and 10.4.7); today no such analysis exists, which is a gap of the element, not only of the tool argument.

**TCL 3** today. At ASIL B a TCL 3 tool needs one or a combination of the qualification methods, and the highly
recommended ones are increased confidence from use and evaluation of the tool's development process; validation is
only recommended and then needs a rationale (ISO 26262-8, clauses 11.4.6.1 and 4.3). For GCC 11.4 in our
configuration neither highly recommended method is within reach of the project today: confidence from use needs
recorded use of this version with this configuration and a systematic record of its malfunctions (clauses 11.4.7.2
and 11.4.7.3), and a process evaluation of GCC would have to be done against a recognised standard (clause 11.4.8).
**Target TCL 2**, not TCL 1: proposal I runs the host tests of the safety modules also as target code (compiled by
this compiler with the product options, executed on the target core or its simulator) and compares the results with
the host run; proposal J makes the compiler version and options that built an image checkable; proposal L collects
the known wrong-code faults of GCC 11.4 for the used options. Together they give **TD2** — many miscompilations of
safety code would show, but not every construct of the whole image is exercised — and the remaining TCL 2 needs a
qualification method with rationale. The decision between (1) a commercial toolchain with a qualification kit,
whose predetermined classification must then be checked against our use cases (clause 11.4.2), (2) validation of
GCC 11.4 with a test suite for the language subset and options we use, with rationale, and (3) the TD2 route with
I, J, L plus validation is the **user's** (TN2).

### 3.15 CMake and Ninja

TI2: they decide which files and flags make the image. TD2 today: a missing generated file is found per project
(`nxconfig-build`), a missing module adapter breaks the build (SM-24, K4), and the demo build is warning-free with
`-Werror` (`target-demo`). Proposal D (**done**, PLAT-80) checks the build attributes of the linked ELF
(`Tag_CPU_arch`, FP ABI) against the derivative's core; the other options (optimisation, language standard, warnings)
are still not checked in the image, so TD2 stays until proposal J compares the options recorded in the image with
the pinned configuration. **TCL 1** after J.

### 3.16 Verifiers

- **zig cc for the host tests** — TI2 (a miscompilation could make a test pass). Issue 1 claimed **TD1** on the
  argument that a miscompilation hiding exactly the fault under test while the tests still fail on their mutants is
  implausible. The norm check (TN4) keeps the argument but not its reach: a mutation run shows that *this* test,
  compiled by *this* compiler, fails on *these* mutants — it says nothing about modules without a mutation run, and
  the mutation runs exist per task (SYS-05, SYS-06, IO-32, SYS-18, SYS-23, `safe-io-mutations`,
  `safety-gate-mutations`, `rom-check-mutations`, `e2e-mutations`, `bl-update-mutations` and others), not
  systematically for every safety module. Where the choice is doubtful the conservative class applies (ISO 26262-8,
  clause 11.4.5.3), and the interdependency between the compiler and the verdicts it produces has to be considered
  (clause 11.4.5.2, note 3). Hence two use cases: (a) verdict of a test of a module **with** its own mutation run —
  TD1; (b) any other host-test verdict — **TD2**. The row takes the worse, **TCL 2**. Target **TCL 1** with proposal K
  (a mutation run for every safety module, checked for completeness) and proposal I (the same tests as target code:
  a second, diverse compiler on the same test).
- **cppcheck and clang-tidy** — TI2 when the static analysis is credited (concept 8.4, AoU-22). TD2: `misra` runs
  the real add-on over synthetic code with known violations, which is a partial validation of the tool; nothing
  independent notices a violation the tool misses in the real code. **TCL 2**, qualification by validation: proposal
  H, with the rationale and record of proposal G; the standard's own example rates a static checker at medium
  detection only when testing also covers the same property (ISO 26262-8, clause 11.4.5.2, example 3), which fits
  the combination with the test suites here.
- **Test runner and coverage** — TI2 (a false "green"). TD1: inventory check (a test file that no entry covers turns
  the run red), skips reported as skips, own tests of the runner and of the coverage measurement with invented
  groups and a never-called probe function (`run-tests-selbst`, `coverage-selbst`). **TCL 1.**
- **elfcheck** (`tools/elfcheck`, PLAT-80 and PLAT-82) — TI2 where its "pass" is evidence for SSR-081 and SSR-083.
  TD1: its expectations come from `mcudb`, the project, `rte.yaml`, `tasks.yaml`, the ARXML and the `swc.yaml` of C
  components (own reader, PLAT-102), not from the generators; its ELF, HEX and DWARF readers are its own and are
  compared with `arm-none-eabi-readelf` on the demo images (`elfcheck-demo`, `elfcheck-timing-demo`); its timing expectation agrees with the reviewed reference output
  of rtegen (`elfcheck-timing`); image, derivative, generat, template and configuration mutants show that every check
  fires. **TCL 1.**
- **trace** — TI2 for the argument (a missing test would be hidden). TD1: a synthetic repository with one broken input
  per rule (`trace-tool`), and the generated matrix is reviewed. **TCL 1.**
- **StrictDoc** (`strictdoc`, pinned in `tools/ci/constraints-docs.txt`, DOC-21; row taken over from
  `doc/requirements/migration-plan.md` section 9) — two use cases. (a) Parser and validator whose JSON export
  `tools/trace` checks — TI2: a requirement or field lost in the export would hide a missing test or a dead
  reference. TD1: `tools/trace` counts the `UID:` lines of every `.sdoc` file with its own expression and compares
  the set with the export, and every rule has a fault injection in `trace-tool`, including an export with a missing
  node and a missing field; the version is pinned (K8). Because `tools/trace` checks StrictDoc's output, the
  interdependency of the two (ISO 26262-8, clause 11.4.5.2, note 3) is covered by that independent count. (b) HTML
  export for reading — TI1, not evidence (like the Studio display, AoU-27). **TCL 1.** An update of StrictDoc is a
  tool change: full `trace-tool` run and equality of the export before and after.
- **E2E reference model** — TI2 (it is the expectation of the E2E library). TD1: checked against the example values
  of the specification (`e2e-ref`); the library is checked against it with mutants (`e2e-host`, `e2e-mutations`).
  **TCL 1.**
- **boardtest** — TI2 where it is named as evidence (SSR-040). TD2: parsers and checks against recorded console and
  CAN captures and the format strings of the firmware; the expectations are the author's. **TCL 2** (G).

---

## 4. Safety-relevant generated items

Concept sections 8.4 and 10.5 name the items whose generation is a direct fault mode of the element. Per item: who
generates it, which mechanism consumes it, which safeguard kinds apply today, and what is missing.

| Item | Generated by, file | Consumed by | Safeguards today | Missing |
|---|---|---|---|---|
| Safe outputs table | bswgen, `safeOutputsConfiguration.h` | SM-23 (three paths), SM-25 | K1 host and image (reference-manual addresses, one shared expectation), K4 count (SM-24), K5 image: read-only, strong table, count, end entry, handler order and, since PLAT-77, the content of every entry (A done); K6; reference output (B done) | pin function of the safe_off outputs (GPIO mux, direction) is checked by the generator only |
| `safe_off` output count | bswgen, `NX_SAFE_OFF_OUTPUT_COUNT` | SM-24 | K4 build error, K2 | - |
| MPU region count | bswgen from mcudb, `derivativeFacts.h` | SM-12, SM-13 | K4 against the device header, encoding tested for 8/12/16 regions | - |
| MPU descriptors | **not generated**: computed at run time in `MemoryProtection::init()` from linker symbols (**TF2**) | SM-10, SM-13 | K5 placement of the protected data between `__MPU_BSS_START` and `__MPU_BSS_END` (`safety-context-elf`) | the RAM layout they derive from is generated (see memory layout) |
| Interrupt priorities | bswgen, `interruptPriorities.h` | every ISR; timing of the safety task | K4 range against the derivative's priority bits, K2 compiled for the cores; since PLAT-82 (F done) K1/K5: ranks, syscall level and bits as compiled into the image, and the level of every rank through the target compiler, against the project and `mcudb`; K6; reference output (B done) | the NVIC registers in a running device (board check TB4) |
| ADC reference band | bswgen, `adcReferenceConfiguration.h` | SM-18 | K2 with own profile, K6 mutants of `SafeSystem.cpp`; reference output (B done) | - |
| Task list, priorities, periods | rtegen, `RteTasks.h`, `Config.h` | scheduling; SM-01 relation via `nxconfig-watchdog` | K2 own profiles, demo list pinned by a test; reference output (B done); since PLAT-82 (F done) K1/K5: `rte::TASKS` and the context number of every task in the linked image against `rte.yaml` and `tasks.yaml`, K6 | the running task list (board check TB3) |
| SM-05 deadlines | rtegen (= the periods) | SM-05 | K2 `rte-deadline` with an own profile and mutants; since PLAT-82 (F done) K1/K5: periods and offsets of `rte::RUNNABLES` in the linked image against the ARXML (C components: `swc.yaml`, own reader, PLAT-102) and `rte.yaml`, K6 | as above (TB3) |
| Signal qualifier limits | rtegen, `RteConfig.cpp` | SM-30 | K1 Python reference for sensor values, K6 mutants of the generat; reference output (B done) | - |
| E2E configuration | rtegen with dbcgen | SM-31 | K2; K1 for the library; partner ECU at integration; reference output (B done) | an independent source for data ID and profile per signal |
| CAN pack/unpack | dbcgen, `CanDb.c/h` | every CAN value | K1 `cantools` on the test DBC and, since PLAT-81, on every project and profile DBC (E done); K6 mutants; reference output (B done) | - |
| Memory layout, target ID | blgen / nxconfig `memory.py`, `memoryLayout.h`, `bl_memory.*` | SM-51, SM-14, both linker scripts, canflash | K3 against the derivative map, K5 real `ld` run, K6 bootloader mutants, K7 `canflash compat` at the board | independent check of the linked images against the derivative map (D) |
| ROM-check list and reference CRC | romCheckImage | SM-14 | K1, K5, K6, K7 | - |
| Validity record | canflash `record` | SM-51 | K7, K6 | - |
| Clock plan | nxconfig `timing.py`, bswgen `boardClock.h` | every time in the image, watchdog relation | K2 at four system clocks, board runs (eval board only) | configuration identity (C); a run-time clock check is SYS-24's register class "system clock" (fmea 5.2) |
| Watchdog times | **code constants**, not generated (concept 7.1, AoU-08) | SM-01 | relation checked by nxconfig (`nxconfig-watchdog`) | moves into a configuration level with SYS-25; then B applies |

---

## 5. Two routes, and which one is taken where

**Route 1 — raise detection until TD1.** A check of the output that does not use the generator's logic. Its value is
independent of how the generator is written, it keeps working when the generator is refactored, and it is visible to
the integrator: he can run it on his own project (AoU-26). It is the route for the items of section 4, because there
a wrong output is a direct fault mode of the element (concept 10.5). The standard backs this route explicitly: its
tool clause applies only where a tool's relevant output is **not** examined or verified (ISO 26262-8, clause
11.4.1); a code generator whose generated code is verified is its own example of high detection (clause 11.4.5.2,
example 1); and the guidance calls adding checks of the tool output to the development process the alternative to
qualification, valid independently of the tool version as long as the use cases stay the same (ISO 26262-10,
clause 13). Its advantage here is decisive: the generators change every week, and a qualification would have to be
repeated for each version.

**Route 2 — qualify the tool (TCL 2) by validation.** For everything else a generator writes (UART, pin names,
console strings, most of the RTE plumbing) a check per item would be out of proportion. The existing test suites
already are a validation in substance: own profiles, a deliberately broken input per check, mutants of the output.
What the standard asks of it (TN3):

- **Admissible, with rationale.** At ASIL B validation is only a recommended method for TCL 2; the highly
  recommended ones (increased confidence from use, evaluation of the tool development process) are not available
  for tools that change every week (section 1.1). Choosing validation alone is allowed because the methods are
  alternatives, but the record must say **why** it is sufficient (ISO 26262-8, clause 4.3). The rationale proposed
  here: the tools are in-house and fully visible, their requirements are written, the validation runs on every
  commit (so it always matches the version used, which confidence from use never could), it includes fault
  injection, and the safety-relevant items are additionally covered by route 1.
- **What the validation must show** (ISO 26262-8, clause 11.4.9.2): that the tool meets the requirements of its
  classified purpose; that every malfunction found during validation is analysed with its possible consequence and
  a measure against it; and how the tool reacts to anomalous conditions — wrong or incomplete input (K3 covers this
  for the configuration), forbidden option combinations, and an **incomplete update of the tool** (for us: a
  package version differing from `constraints.txt`, a stale generated file, a packaged Studio with older tool
  copies, TF9).
- **What the record must contain** (ISO 26262-8, clause 11.4.6.2, in our words): the tool's identification and exact
  version; its TCL with a pointer to this document; the maximum ASIL of the requirements it could violate, per use
  case; the configuration and environment the qualification holds for; who qualified it; the method chosen and its
  rationale; the results; and the usage constraints and malfunctions found. The planning data of section 1.4 and
  the information of ISO 26262-8, clause 11.4.4.2 (description, user guide, behaviour under anomalous conditions,
  known malfunctions with work-arounds) go with it. The work product is the software tool qualification report
  (ISO 26262-8, clause 11.5.2). That is proposal G.
- **Per version.** A qualification holds for the qualified version and environment only (ISO 26262-10, clause 13);
  a change of the tool or of its environment (Python, a package) needs the validation again. For our tools this is
  cheap if the record is produced by the CI run of the released commit — proposal G asks for exactly that.

**Common to both routes:** the checks run on **our** test profiles and on the demo. An integrator's project is a
different configuration; the safeguards only protect him if they run on **his** project too. That is the reason for
the proposed AoU-26 and for proposal C, which makes the configuration that was checked recognisable in the image.
The standard says the same from two sides: a classification made independently of the item must be checked for
validity before the tool is used for that item, and the tool must then be used in the version, configuration and
conditions the classification assumed (ISO 26262-8, clauses 11.4.2 and 11.4.3); and configurable software must be
verified for the configuration data set actually used (ISO 26262-6, Annex C, clause C.4.4).

---

## 6. Proposed assumptions of use (for DOC-19)

Numbered after AoU-24 and AoU-25 proposed in `fmea.md`; to be taken into concept section 4 and the safety manual by
the coordinator or DOC-19.

| ID | Assumption (proposed) |
|---|---|
| AoU-26 | The integrator generates the image **only through the build** (CMake runs nxconfig, bswgen, blgen, rtegen, diaggen, the post-link steps), with the tool versions pinned by `tools/ci/constraints.txt`, never edits a generated file, and runs the tool checks named in section 4 on **his own project configuration** — not only on the neXtBSW test profiles — after every change of configuration or tool version. Without that, the TD1 classifications of section 3 do not apply to his project. Added after the norm check: he **verifies the validity** of this predetermined classification (use cases, maximum ASIL, tool versions and environment of section 1.4) for his item before relying on it, uses the tools only in the versions and configuration it names, and runs every image at least once on the target before release, because the K7 credit of romCheckImage and canflash rests on that first start (ISO 26262-8, clauses 11.4.2 and 11.4.3; section 1.1). |
| AoU-27 | The **Studio's display** (validation messages, step state, test results, schedule analysis) is not evidence. Evidence is the output of the command-line tools and of `tools/test` from the repository's interpreter, as recorded by the CI. The packaged Studio may carry older copies of the tool packages. |
| AoU-28 | **Calibration and diagnostic write access** to safety-relevant values: either XCP calibration and the diagnostic write jobs and routines that reach a safety-relevant output or parameter are disabled in the product, or each such write is interlocked by the application or the safe supervisor, and a calibrated safety-relevant parameter is verified by reading back its stored value through a path that does not use the A2L conversion. neXtBSW offers both since SYS-89: `memory.calibration.profile: production` (no XCP, values read from the flash segment) and the console command `cal values` (stored values without conversion); the integrator applies them. Since SYS-90 an XCP write to a safety-relevant parameter is also interlocked in the ECU itself: the generated plausibility check (limits, ascending axes) refuses it before it takes effect, and `cal check` shows the result; this does not replace the read-back, because a plausible value can still be the wrong one. Since SYS-91 a dataset flashed on its own reaches this start-up check (the segment is no longer covered by the build reference of the ROM check, but re-checked against its own header in every ROM check pass), so the integrator's dataset release is decided by `on_invalid` and the plausibility checks, not by a safe-state hold. |
| AoU-29 | **Code generator of the application (Embedded Coder).** Its classification and qualification are the integrator's, for his use cases and his maximum ASIL. neXtBSW supplies for it: (1) the interface description of the RTE (ports, types, runnables and their calling contract); (2) the list of Embedded Coder settings that `tools/swc/swc_create.m` applies to every component it creates, which become part of his tool configuration and which he owns from then on; (3) the statement that **no neXtBSW check verifies the behaviour of the generated model code** — rtegen checks only the interface against the ARXML, and `demoswc-sil` runs the demo component only — so his tool error detection for Embedded Coder cannot rest on neXtBSW's checks; he verifies the generated code himself, for example by back-to-back tests of model and object code (ISO 26262-6, clause 9.4.5). |

---

## 7. Proposed tasks

For the coordinator to carry into `doc/todo.yaml`; **IDs are deliberately not assigned** (the letters are local to
this document). Priority follows the project's order (safety before OTA and multi-MCU ECU); none needs hardware
except where a board check is named as a checklist item.

| # | Title | Done when | Priority, dependencies | Touches (expected) | Closes |
|---|---|---|---|---|---|
| **A** | Safe outputs table checked entry by entry in the linked image | `safe-outputs-order` (or a sibling test) reads every entry of `nxSafeOutputs` from the application ELF — register address, value, gate register and gate mask, order — and compares it with an expectation derived from `board.yaml`, the project and the module manifests with reference-manual addresses, **shared with `BswGenSafeOutputsHostTest` in a module that does not import bswgen**; runs for the demo and at least two own profiles (with PWM group and active-low output, without power outputs); mutants of the generator (PCOR/PSOR swapped, entry dropped, gate missing, order changed) turn it red | P2; none — **done: PLAT-77** (2026-09-21), `safe-outputs-table` | `platforms/s32k1xx/hardFaultHandler/test`, `tools/bswgen/test`, `tools/test/tests.yaml` | TF5, SSR-080 |
| **B** | Reference output of the safety-relevant generated files | for every test profile of bswgen, blgen, rtegen and dbcgen the files of section 4 (`safeOutputsConfiguration.h`, `derivativeFacts.h`, `interruptPriorities.h`, `adcReferenceConfiguration.h`, `memoryLayout.h`, `bl_memory.h/.ld`, `RteTasks.h`, `Config.h`, `RteConfig.cpp`, `CanDb.c/h`) are checked in as expected output, normalised (no absolute paths); a difference fails the test with a diff; an update command rewrites them and the diff is reviewed in the commit; a deliberately changed template turns the test red | P2; none — **done: PLAT-78** (2026-09-22), `bswgen-reference`, `rtegen-reference` | `tools/bswgen/test`, `tools/rtegen/test`, `tools/dbcgen/test`, `tools/test/tests.yaml` | TF1, SSR-081 |
| **C** | Configuration identity in the image | every generated file carries the generator's version and a SHA-256 over its inputs (project, board, derivative, `rte.yaml`, DBC, `diag.yaml`, the generator's own sources and templates); the image carries one combined identity as a constant, readable on the console and through a UDS data identifier, and `nxconfig` prints it for a project without building; generating twice gives identical files (reproducibility test); host test with own profile; board check as checklist | P2; none — **done: PLAT-79** (2026-10-01), `nxconfig-identity`, `identity` | `tools/nxconfig`, `tools/bswgen`, `tools/rtegen`, `tools/diaggen`, `ecu/application` (constant and console), `tools/test/tests.yaml` | TF4, SSR-082 |
| **D** | Linked images checked against the derivative map and the core | a test reads the linked bootloader, application and updater ELFs and checks, against `mcudb` (not against `memoryLayout.h`): every loadable section inside the derivative's flash or RAM, no overlap between bootloader and application, validity record and checksum list where the bootloader and SM-14 expect them, sector alignment of the application start; and the ELF build attributes (`Tag_CPU_arch`, FP ABI) against the derivative's core; runs for the demo and for S32K116, S32K148 and a stm32 profile; mutants of the layout turn it red | P2; none — **done: PLAT-80** (2026-09-22), `elfcheck*` | new test under `bootloader/test` or `tools/bswgen/test`, `tools/test/tests.yaml` | TF6, SSR-083 |
| **E** | dbcgen back-to-back over every project DBC | for every DBC under `projects/` every used signal is packed and unpacked by the generated code and by `cantools` at minimum, maximum, zero and random values, with a comparison of the raw bytes and the physical value; runs in `tools/test` per project; E2E-protected signals included | P2; none — **done: PLAT-81** (2026-09-21), `dbcgen-projects` | `tools/dbcgen`, `tools/test/tests.yaml` | TF8, SSR-084 |
| **F** | RTE timing and interrupt priorities checked against the configuration | a test reads from the linked ELF (or from a host build of the generated tables) the task list with priorities and periods, the runnable periods that SM-05 uses as deadlines, and the NVIC priority assigned to each configured interrupt, and compares them with `rte.yaml`, `ecu/asyncCoreConfiguration/tasks.yaml` and the project's interrupt ranks by a parser that does not import rtegen or bswgen; mutants turn it red; board check with the console commands `rte runnables` and `rte json` as checklist | P2; after B — **done: PLAT-82** (2026-09-22), `elfcheck-timing*` | `tools/elfcheck` (`timing.py`, `dwarf.py`), `tools/test/tests.yaml` | TF7, SSR-081 (timing part) |
| **G** | Qualification records of the TCL 2 tools | per TCL 2 tool of neXtBSW (nxconfig, bswgen, blgen, rtegen, diaggen, a2lgen, schedana, mcudb tools, swc tools, boardtest) and for cppcheck/clang-tidy a software tool qualification report with the content of ISO 26262-8, clause 11.4.6.2 as listed in section 5: identification and version (commit, `constraints.txt`), TCL with pointer to section 3, maximum ASIL per use case, configuration and environment, who qualified, method with the **rationale** for validation at ASIL B, results (which test entry validates which written requirement of the tool), usage constraints and malfunctions; the validation additionally shows the analysis of malfunctions found and the reaction to anomalous conditions including an incomplete tool update (clause 11.4.9.2); known malfunctions collected from `CHANGELOG.md` with work-around; the record of a release is produced from the CI run of that commit; a consistency test checks that every TCL 2 row of section 3 has its record with every field filled; input for DOC-19 | **P2** (raised: the report is a required work product for every TCL 2 tool; A, B, D, E, F are done, C does not change the table) | `doc/safety`, `tools/ci`, `tools/test/tests.yaml` | section 5 route 2, TN3 |
| **H** | Validation suite of the static analysis | for every MISRA rule enabled in `tools/misra/rules.yaml` and every clang-tidy check, a synthetic snippet with a known violation and one without; `misra` checks that each is found exactly as expected with the pinned tool versions, and a rule without a snippet is reported | P3; none | `tools/misra`, `tools/test/tests.yaml` | cppcheck/clang-tidy TCL 2 |
| **I** | Host tests of the safety modules also in the target environment | the host tests of every module under `platforms/*/port/safety` and `ecu/safety` are additionally compiled with `arm-none-eabi-gcc` and the **product options** of the toolchain file, and executed on the target core — an instruction-set simulator of Cortex-M4 and Cortex-M0+ in the CI, the board as checklist; the verdict of every test case is compared with the host run and a difference fails; a written analysis of the remaining differences between host and target environment (word size, alignment, library, start-up) names the tests that cover them (ISO 26262-6, clauses 9.4.5 and 10.4.7); a deliberately miscompiled function (patched object) turns it red | P2; none | `tools/test`, `cmake/toolchains`, `tools/test/tests.yaml`, `doc/safety` | TN2, TN4: compiler TD3 → TD2, host compiler TD1 |
| **J** | Compiler identification and options checked in the image | `elfcheck` reads from the linked bootloader, application and updater ELFs the compiler identification (`.comment`) and the options recorded per compilation unit (`DW_AT_producer`, `-grecord-gcc-switches`), and compares them with a pinned expectation (compiler version string, the option set of the toolchain file and the family port); an unexpected version, a missing or additional option (optimisation level, language standard, CPU and FPU options) fails; runs for the demo, S32K116 and S32K148 builds; mutants (other version string, changed option) turn it red; the build prints the compiler version it found | P2; none | `tools/elfcheck`, `cmake/toolchains`, `tools/test/tests.yaml` | TN2, section 1.4 (tool identification and configuration), CMake TD1 |
| **K** | A mutation run for every safety module | a list in `tools/test` names every safety module (the same set as I) and its mutation entry; a consistency test fails for a safety module without a mutation run of at least three mutants of its detection logic; missing runs are written for the modules not covered today | P3; none | `tools/test`, module test folders, `tools/test/tests.yaml` | TN4: host compiler TD1 for every safety module |
| **L** | Known malfunctions of the external tools | for arm-none-eabi-gcc 11.4 (NXP build), binutils, zig cc (clang 21.1.0), cppcheck 2.17.1 and clang-tidy 22.1.8: the published wrong-code and false-negative faults fixed after the used version are collected, each judged against the options and language subset we use (affected / not affected / work-around), with the source of the list; the list is part of the tool record (G) and is re-checked on every version change; the user's decision on the compiler route (TN2) is recorded there | P3; after the decision on TN2 for the compiler part | `doc/safety` | ISO 26262-8, clause 11.4.4.2 information on known malfunctions; TN2 |

Additions to existing tasks, not new tasks:

- **DOC-19:** AoU-26 … AoU-29 (section 6); a chapter "Tools" with the summary table of section 3, the planning data
  of section 1.4 and, for AoU-29, the list of Embedded Coder settings applied by `tools/swc/swc_create.m`.
- **SYS-25** (watchdog values into a configuration level): once they are generated, they join the items of
  section 4 and proposal B.
- **SYS-24** (register lock and check from the configuration): the pin functions of the safe outputs and PWM pins are
  a bswgen output with no independent check today (section 3.2); the register list of SYS-24 is derived by bswgen
  and belongs in proposal B's reference output when it exists.

---

## 8. Requirements added to the SSR (`ssr.sdoc`, was `requirements.yaml`)

The missing safeguards of section 4 that protect a mechanism of stage 1 are entered as **open** requirements, derived
from AoU-10 (configuration is safety-related data) and the goals whose mechanisms consume the generated item. They
carry no code link and no test until the proposed task is done; `tools/trace` reports them as open. When a proposal
is done, its requirement becomes **implemented** with code links and test entries (SYS-49 update: A and E; PLAT-80:
D; PLAT-82: B and F). The column
"Status" must equal the status in `ssr.sdoc`; `safety-tools` checks it, and that every implemented one is
named by at least one entry of `tools/test/tests.yaml`.

| ID | Title | Derived from | Mechanisms | Proposal | Status |
|---|---|---|---|---|---|
| SSR-080 | Safe outputs table checked in the linked image against an independent expectation | ASG-01, ASG-02, AoU-06, AoU-10 | SM-23 | A | implemented (PLAT-77) |
| SSR-081 | Change of a safety-relevant generated file detected | ASG-06, AoU-10 | SM-05, SM-18, SM-23, SM-30 | B, F | implemented (PLAT-78, PLAT-82) |
| SSR-082 | Configuration and generator identity recognisable in the image | ASG-07, AoU-10 | SM-51 | C | implemented (PLAT-79) |
| SSR-083 | Memory layout of the linked images checked against the derivative | ASG-07, AoU-10 | SM-14, SM-51 | D | implemented (PLAT-80) |
| SSR-084 | CAN signal layout of every project checked against an independent decoder | ASG-05, AoU-10 | SM-30 | E | implemented (PLAT-81) |

The existing safeguards are already traced through their mechanisms (SSR-005, SSR-007, SSR-022, SSR-024, SSR-040,
SSR-041, SSR-051 with the test entries `bswgen`, `bswgen-mpu`, `safe-outputs-order`, `rom-check-image`, `rtegen`,
`e2e-ref`, `bootloader-host`); this document adds no second requirement for them.

---

## 9. Findings and norm questions

### 9.1 Findings

| ID | Finding | Consequence |
|---|---|---|
| **TF1** | Concept 8.4 counts "generated output compared against checked-in reference output". There is **no** checked-in expected output of any generator (no expected, golden or reference files under `tools/*/test`); the tests assert expectations and properties, and compile and run the generated code | parts of a generated file that no test asserts can change unnoticed when a generator is refactored. Proposal B — **closed** by PLAT-78 (`bswgen-reference`, `rtegen-reference`: the safety-relevant generated files of every profile against a reviewed reference) |
| **TF2** | Concept 8.4 and 10.5 say the MPU descriptors are generated. Only the **region count** (`derivativeFacts.h`) and the **RAM layout** they are computed from (`memoryLayout.h` → application linker script) are; the descriptors are computed at run time in `MemoryProtection::init()` from `__MPU_BSS_START`, `__MPU_BSS_END` and the safety stack | the descriptor contents are code, covered by SM-13 and `safety-context-elf`; the generated part is covered by `bswgen-mpu` and proposal D |
| **TF3** | Concept 1.2 and 8.4 list `tools/intgen`. It does not exist; the module composition is `Integration.cpp` (hand-written, X-macros) plus `modules.cmake` from bswgen | nothing to classify; when intgen is written it joins section 3 and proposal B |
| **TF4** | No generated file carries the generator's version or a fingerprint of its inputs, and the image carries no configuration identity | AoU-10's change control cannot tell from an image which configuration and which generator produced it. Proposal C |
| **TF5** | `safe-outputs-order` checks in the linked image the count, the end entry, the read-only placement and the handler order, **not the entries' content**; the content is checked independently only in a host build of the definition | a difference between host build and target build of the table (compiler, preprocessor, linker) would not be noticed. Proposal A — **closed** by PLAT-77 (`safe-outputs-table`, every entry of the linked image against the independent expectation) |
| **TF6** | One generated memory layout feeds the bootloader, the application linker script, the updater and `canflash` | consistent by construction, wrong everywhere at once; SM-51 checks at the addresses the same layout names. Proposal D — **closed** by PLAT-80 (`elfcheck`, images against mcudb and the core) |
| **TF7** | Interrupt priorities are checked for range at compile time, not for the assignment to the configured rank in the image or at run time | a swapped priority would pass every test. Proposal F — **closed** by PLAT-82 (`elfcheck-timing*`: task contexts, periods, deadlines and the interrupt ranks, syscall level and NVIC bits of the linked image, and the level of every rank through the target compiler, against the configuration; the NVIC registers of a running device stay board check TB4) |
| **TF8** | dbcgen's independent reference (`cantools`) runs on the test DBC only | a layout error that appears only with a project's DBC passes. Proposal E — **closed** by PLAT-81 (`dbcgen-projects`, every DBC of the repository against `cantools`, eight mutants) |
| **TF9** | The packaged Studio imports frozen copies of the tool packages for its live validation, while the generators run with the repository's interpreter | its messages can differ from the build's; AoU-27 |

### 9.2 Norm questions TN1–TN5 (record)

Raised in issue 1 because the standard was not available; answered on 2026-09-22 from ISO 26262-8, clause 11, with
ISO 26262-6 and ISO 26262-10 where they add to it. The table is kept as the record: status, answer in this
document's own words, where the answer changed the document. "Answered" means the standard gives the answer; "decision
open" means the standard leaves a choice that belongs to the user.

<!-- tn-record:start -->
| ID | Question | Status | Answer (own words, with clause) | Changed in this document |
|---|---|---|---|---|
| TN1 | Is the classification per use case, with credit for run-time mechanisms (K7), what the standard intends? | answered | **Per use case: yes.** Use cases are part of the usage plan and impact and detection are determined from the intended usage (ISO 26262-8, clauses 11.4.4.1 and 11.4.5.2; ISO 26262-10, clause 13); a classification holds only as long as the use cases do. **Run-time mechanisms: only with two conditions.** Detection is credited to measures of the development process — tests, reviews, checks inside the tool (ISO 26262-8, clause 11.2 and clause 11.4.5.2, note 1) — and interdependencies between a tool and whatever checks it must be considered (clause 11.4.5.2, note 3). A product mechanism therefore counts only when it checks exactly the tool's error class independently of that tool **and** the error shows deterministically in the integrator's own tests before release (SM-51, SM-14: every start fails). Detection that depends on the operating situation (watchdog behind schedana) is not credited. The class names are the standard's: TI1 = no impact, TI2 = impact (clause 11.4.5.2); doubtful cases are estimated conservatively (clause 11.4.5.3) | section 1.1 (names, conditions for K7, conservative rule), 3.8, 3.10, 3.11, summary table (TI0/TI1 → TI1/TI2), AoU-26 (first start on target) |
| TN2 | Which qualification does the compiler need at ASIL B? | answered, decision open | With TD3 the compiler is **TCL 3**; at ASIL B the highly recommended qualification methods are increased confidence from use and evaluation of the tool's development process, validation and development according to a safety standard are only recommended; the methods are alternatives, and a combination with rationale is required (ISO 26262-8, clauses 11.4.6.1 and 4.3). Neither highly recommended method is within reach for GCC 11.4 in our configuration (clauses 11.4.7 and 11.4.8). The standard also allows lowering TD by process measures instead of qualifying (ISO 26262-10, clause 13), and independently asks that host tests be complemented by tests in the target environment (ISO 26262-6, clauses 9.4.5 and 10.4.7). Route: proposals I, J, L give **TD2 → TCL 2**, which still needs a qualification method with rationale. The choice between a commercial toolchain with a qualification kit (its classification then checked against our use cases, ISO 26262-8, clause 11.4.2), validation of GCC 11.4 for our language subset and options, and the TD2 route with validation is the **user's** — decision board | section 3.14, summary table (target TCL 2, proposals D, I, J, L), section 1.4 (compiler identification and configuration), proposals I, J, L |
| TN3 | Is validation (route 2) enough for TCL 2 generators at ASIL B, and what must the record contain? | answered | **Admissible, but not the preferred method, so only with a written rationale**: at ASIL B validation is a recommended, not a highly recommended method for TCL 2 (ISO 26262-8, clauses 11.4.6.1 and 4.3); the highly recommended ones are not available for tools that change every week (clauses 11.4.7.2 and 11.4.7.4). The validation must show conformance to the tool's written requirements, analyse every malfunction found, and examine the reaction to anomalous conditions including an incomplete tool update (clause 11.4.9.2). The record must hold identification and version, TCL with its analysis, maximum ASIL per use case, configuration and environment, who qualified, method, results, and usage constraints and malfunctions found (clause 11.4.6.2), together with the tool information of clause 11.4.4.2; it is the qualification report of clause 11.5.2 and holds per version (ISO 26262-10, clause 13). Route 1 remains the preferred way for the safety-relevant items (ISO 26262-8, clause 11.4.1) | section 1.1 (methods), 1.4 (planning data), section 5 (rationale, validation criteria, record content, per version), proposal G (content, P2) |
| TN4 | May the TD1 of the host compiler rest on the mutation runs? | answered | **Not for all host tests.** A mutation run shows that one test, compiled by this compiler, fails on its mutants; it covers the modules that have one. For the rest the choice is doubtful, and doubtful cases are classified conservatively (ISO 26262-8, clause 11.4.5.3), with the interdependency between compiler and verdict considered (clause 11.4.5.2, note 3). zig cc is therefore **TD2 → TCL 2** today, TD1 only for tests with their own mutation run; TCL 1 for all after proposals K (a mutation run per safety module) and I (the same tests as target code by a second compiler) | section 3.16, summary table (zig cc TD1 → TD2, TCL 1 → TCL 2, target TCL 1, proposals I, K, L), proposal K |
| TN5 | What must the integrator get for his Embedded Coder classification? | answered | His code generator is his tool: he classifies it for his use cases and maximum ASIL (ISO 26262-8, clause 11.4.4.1), and as integrator of an element out of context he checks every assumption the element makes (ISO 26262-10, clause 9.2.4.5). neXtBSW must give him what his classification depends on: the RTE interface description; the Embedded Coder settings that `tools/swc/swc_create.m` applies, because they are part of his tool configuration (ISO 26262-8, clause 11.4.4.1); and the statement that no neXtBSW check verifies the behaviour of the generated model code, so his detection must come from his own verification of that code (ISO 26262-6, clause 9.4.5). Nothing beyond that is needed from neXtBSW; the swc tools themselves stay classified here | section 3.13 (second use case of the swc tools), AoU-29 (new), DOC-19 addition |
<!-- tn-record:end -->

Consequences for the concept (to be taken over by the coordinator; this task does not edit `concept.md`): section 0
and 9.4 no longer need to say that the standard is unavailable for the tool part; concept 9.4 point 2 (evidence
expected for the tool confidence level) and point 4 (compiler) are answered by TN3 and TN2; concept 8.3 should name
the target-environment tests (proposal I) as part of the compiler path and the decision of TN2 as the user's; concept
8.4 should say that the tool classification is the software tool criteria evaluation report and that the TCL 2 tools
need qualification reports (G).

---

## 10. Board checklist (not run for this task)

No hardware was used. These read-only checks would confirm on the board what the host checks and the proposals claim:

| # | Check | How | Expected |
|---|---|---|---|
| TB1 | Safe outputs table in the flashed image | J-Link: read `nxSafeOutputs` from flash and compare with the ELF and with the expectation `safe_outputs_expectation.expected` (used by `safe-outputs-table` and `BswGenSafeOutputsHostTest`) for the demo | identical, ends with address 0 |
| TB2 | Memory layout seen by the tool and the target | `canflash compat` against the running demo | board, derivative, layout and addresses agree |
| TB3 | Task list and deadlines in the running image | console commands `rte runnables` and `rte json`, compared with `rte.yaml` and `tasks.yaml` | identical periods and priorities |
| TB4 | Interrupt priorities in the running image | J-Link: read the NVIC IPR registers of the configured interrupts after start-up | the levels of `interruptPriorities.h` for the demo (CAN 8, UART-RX 9 as recorded) |

---

## 11. Review questions of the architecture (section 11)

This task changed no reusable code, no generator and no bootloader; it added this document, a consistency test under
`doc/safety/test/`, a test entry, five open requirements with the regenerated matrix, and a pointer in `concept.md`.

1. **Board without an SBC?** Yes. No tool classification depends on the SBC; the safe outputs table and its checks
   include a board without power outputs (S32K116 profile), and proposal A keeps that profile.
2. **Different crystal and system clock?** Yes. The clock plan is classified as a derived value of nxconfig (section
   3.1); its check today runs at four system clocks, and proposal C makes the configured clock plan recognisable in
   the image.
3. **Different derivative, S32K3 / STM32 / Cortex-R?** Yes. The classification is per tool, not per family; the
   stm32 generator is part of bswgen's row, and proposal D is explicitly required to run for S32K116, S32K148 and a
   stm32 profile, and to take its expectation from `mcudb`, which also holds RM46L852.
4. **Two or more CAN buses and different IDs?** Yes. dbcgen's proposal E covers every signal of every DBC of every
   project; nothing assumes one bus.
5. **Several tasks and freely chosen priorities?** Yes. Proposal F compares the task list and priorities of the
   project with its configuration, whatever they are; the schedana classification distinguishes planning from
   evidence (AoU-08).
6. **Every new value in exactly one configuration level?** No new configuration value. Proposal C adds a derived
   value (the configuration identity), computed by the generator, not configured.
7. **Host test with its own test profile?** The new test `doc/safety/test/test_tool_classification.py` checks the
   document against the repository (every build-run tool classified, every test named exists, TCL derived by the
   table, every proposal has a "done when") with deliberately broken copies of the document; it needs no board
   profile. Every proposal of section 7 requires own profiles besides the demo.
8. **Tool core free of Qt, messages with a YAML path?** Not applicable — no tool changed. K3 (messages with a YAML
   path) is credited where it exists; AoU-27 records that the Studio's display is not evidence.
9. **Device driver granularity?** Not applicable — no driver changed.
10. **Placement under `platforms/`?** Not applicable — no new file there.

**Board peculiarity forced into the architecture?** None.

**Norm follow-up (2026-09-22).** Again no code, generator or bootloader changed; only this document and its
consistency test. The answers are per tool and use case, not per board: proposal I runs for Cortex-M4 and Cortex-M0+,
proposal J for the demo, S32K116 and S32K148 builds, and nothing in TN1–TN5 depends on the eval board, the SBC, the
crystal or the number of CAN buses.

---

## 12. Sources

Issue 1: from inside the repository at `f8ee573`. Norm follow-up: repository at `62a1b53` and the licensed copy of
ISO 26262 (second edition), which is not part of the repository and is cited by part and clause only —
ISO 26262-8, clauses 4.3 and 11 (in particular 11.2, 11.4.1 to 11.4.9 and 11.5); ISO 26262-6, clauses 4.3, 9.4.5
and 10.4.7 and Annex C, clause C.4.4; ISO 26262-10, clauses 9.2.4 and 13.

- Concept and analysis: [`concept.md`](concept.md) (sections 1.2, 8, 9.4, 10.5), [`fmea.md`](fmea.md),
  [`ssr.sdoc`](../requirements/safety/ssr.sdoc), [`static-analysis.md`](static-analysis.md).
- Build: `CMakeLists.txt`, `bootloader/CMakeLists.txt`, `bootloader/updater/CMakeLists.txt`,
  `ecu/application/CMakeLists.txt`, `ecu/uds/CMakeLists.txt`, `ecu/xcp/CMakeLists.txt`, `cmake/NxRomCheck.cmake`,
  `platforms/s32k1xx/port/main/CMakeLists.txt` (linker script from `memoryLayout.h`).
- Tools: `tools/nxconfig`, `tools/bswgen` (`bswgen.py`, `blgen.py`, `stm32gen.py`, templates), `tools/rtegen`,
  `tools/dbcgen`, `tools/diaggen`, `tools/a2lgen` (README), `tools/schedana`, `tools/studio` (README,
  `core/commands.py`, `build_exe.py`), `tools/canflash/canflash.py`, `tools/ci/constraints.txt`, `mcudb/tools`,
  `platforms/s32k1xx/port/safety/safeMemory/tools/romCheckImage.py`.
- Tests: `tools/test/tests.yaml` and the test sources named there, in particular `tools/bswgen/test_bswgen.py`
  (`BswGenSafeOutputsHostTest`), `tools/bswgen/test_bswgen_mpu.py`, `tools/bswgen/test_bswgen_interrupts.py`,
  `tools/rtegen/test_rtegen.py`, `tools/dbcgen/test_dbcgen.py`,
  `platforms/s32k1xx/hardFaultHandler/test/test_safe_outputs_order.py`.
- Code: `platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/MemoryProtection.cpp`,
  `ecu/xcp/src/xcp/XcpSlave.cpp`.
