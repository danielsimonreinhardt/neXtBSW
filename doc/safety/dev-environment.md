# neXtBSW development environment: languages, guidelines, tools, build and test flow (DOC-22)

Status: 2026-09-28, **issue 1 (DOC-22)**, draft for acceptance by the user.
Baseline: v0.2.0
Readers: the user as safety manager, the coordinator, every agent that writes C, C++ or a generator, and an integrator
or reviewer who wants to know with what and under which rules neXtBSW is made.
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**What this document is.** The one place that names the software development environment of neXtBSW, as asked by
6-5.5.1 and found missing by `iso26262-gap.md` (not published, available on request) (rows 6-01 and 6-02, section 6 task 21; safety plan
row P6-01, safety case row EW-14). It states which languages are used and why (6-5.4.2), which coding guidelines
cover the topics the standard lists for them (6-5.4.3), **the neXtBSW C++ subset `NXCPP`** (section 4), the tools
with their pinned versions and tool confidence levels, and the build and test flow per lifecycle phase. It does not
repeat what other documents own; it points to them.

**Decision behind section 4.** User decision **q-doc22-cpp** (2026-09-28): *neXtBSW defines its own C++ subset and
enforces it with the tools it already has (cppcheck, clang-tidy, the compiler, the test runner, PLAT-74); deviations
are justified; no commercial MISRA C++ tool is bought.* MISRA C++:2023 therefore stays **not claimed** (it is named
only as the reference the subset was compared against, section 4.5).

**Licence rule.** ISO 26262 is cited by **part and clause only** (for example "6-5.4.3" = ISO 26262-6:2018, clause
5.4.3); no text, table or figure of it is reproduced. MISRA guidelines are named by number with a short description
in our own words, never with their text.

**Consistency test.** [`test/test_dev_environment.py`](test/test_dev_environment.py) checks this document against the
repository: every version of section 5 against the file that pins it, every tool-enforced NXCPP rule against the
active compiler options and analysis checks, and the code-scan rules of section 4.3 against the code itself.

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Languages | **C99** (bootloader, E2E library, startup, newlib hooks), **C++17** (application frame, drivers, platform port, safety mechanisms), ARM assembly (startup, context switch), GNU linker scripts (generated from templates), **Python 3.12** (configuration check, generators, verifiers, test runner), YAML (configuration), Jinja2 (generator templates), CMake. Simulink/Embedded Coder only for the application example (out of the element's scope) |
| Guidelines | C: **MISRA C:2012** with the PLAT-74/PLAT-76 re-categorisation and deviations ([`static-analysis.md`](static-analysis.md)). C++: **NXCPP** — 27 rules (section 4), of them **20 enforced by a tool** (compiler and build settings, clang-tidy, cppcheck add-on, the code scan of the consistency test or `elfcheck`; 3 of them with a review part), **7 by review** with a named tool proposal each. Naming and layout: the OpenBSW conventions, adopted by reference |
| Guideline topics of 6-5.4.3 | 9 topics; covered for C and C++: language subset, strong typing, defensive implementation, well-trusted principles, naming, concurrency (C++ by review); **not covered by any tool**: complexity limit (both languages) and style/format check (section 3.2) |
| Tools | 22 rows with version, where it is pinned and the TCL of [`tool-classification.md`](tool-classification.md) (section 5). Everything that builds or verifies the image is pinned once (`tools/ci/versions.toml`, `tools/ci/constraints.txt`, `tools/misra/requirements.txt`, the submodule gitlink); **not pinned**: MATLAB, J-Link software, Vector driver (bench and application tools, section 5.3) |
| Build flow | 7 steps from `project.yaml` to the checked image (section 6), one command per step, all run by `cmake --build` and `tools/test` |
| Test flow | per lifecycle phase (section 7): host unit tests (`zig`), generator tests with own profiles, target builds warning-free with `-Werror`, static analysis gating, coverage measured (not enforced), mutation runs, image checks (`elfcheck`), board runs outside CI |
| Findings | **DE1–DE7** (section 8). Most important: **DE5** (closed by DOC-28, 2026-10-01) — the demo image contained newlib's allocator (`malloc`, `free`, `_sbrk`, 488 bytes), pulled in by the sized `operator delete` that a virtual deleting destructor references; our code never calls it (scan: 0 heap uses), but nothing prevents it. Further: the tool classification names GCC 11.4 although 11.3.1 is pinned (DE1), GNU extensions on by default (DE2, closed by DOC-29), no complexity limit (DE3), no format check of our own code (DE4), NXCPP review rules have no record (DE6), MATLAB and bench software not pinned (DE7) |
| Verification | **mock/host**: the document test runs against the repository at `6f868b5`, with counter-checks on broken copies; the demo target build was repeated warning-free with the pinned GCC 11.3.1 in a Linux container and its image read with `arm-none-eabi-nm` and the linker map (DE5). No hardware |

---

## 1. Scope and phases

The environment covers the software lifecycle of part 6 as tailored by [`safety-plan.md`](safety-plan.md) for a
software SEooC, and the supporting processes of part 8 that need a tool (configuration management
[`cm-plan.md`](cm-plan.md), verification, tool confidence). One environment serves all phases (6-5.4.1 asks for
consistency across them): the same pinned toolchain builds, analyses and tests, on the workstation and in CI.

| Phase (clause of part 6) | Environment element | Where |
|---|---|---|
| Software safety requirements (6-6) | StrictDoc in `doc/requirements/safety/ssr.sdoc` (DOC-21; YAML in `doc/safety/requirements.yaml` until step 3), `tools/trace` | `traceability.md` (not published, available on request) |
| Architectural design (6-7) | natural language and informal notations, module manifests, `tools/schedana` | [`doc/architecture/README.md`](../architecture/README.md) |
| Unit design and implementation (6-8) | C99, C++17 with NXCPP, generators with Jinja2 templates | sections 2–4 of this document |
| Unit verification (6-9) | host tests (`zig c++`/`zig cc`), static analysis, coverage, mutation runs | section 7 |
| Integration and verification (6-10) | target build (`arm-none-eabi-gcc`), image checks (`elfcheck`, disassembly tests) | sections 6, 7 |
| Testing of the embedded software (6-11) | eval board, J-Link, Vector VN1610, `tools/boardtest` | section 7.3 |
| Configuration data (6-C) | three-level YAML, `tools/nxconfig` checks | [`config-range.md`](config-range.md) |

The workstation is Windows (repository `.venv`, `PYTHONUTF8=1`); the CI runs the same pipeline (`python -m tools.ci
run --strict`) on `windows-latest` by default (`.github/workflows/ci.yml`); `tools/ci` fetches the same archives for
Linux, which the cloud sessions use. A difference between the three is a tool change in the sense of section 5.4.

---

## 2. Languages

6-5.4.2 asks that the language is chosen by criteria such as an unambiguous definition, support for embedded real-time
software and for modularity, abstraction and structured constructs. The choice below was made by OpenBSW, which
neXtBSW builds on; the table records the reasons as they apply to neXtBSW.

| Language | Standard / dialect | Used for | Compiler | Why this language |
|---|---|---|---|---|
| C | ISO C99 (`CMAKE_C_STANDARD 99`), ISO mode `-std=c99` (`CMAKE_C_EXTENSIONS OFF`, DOC-29; DE2 closed) | bootloader and updater, E2E library, startup and newlib hooks, C parts of the platform, the C component interface of the RTE | `arm-none-eabi-gcc` | small runtime, direct register access, the language of the vendor headers; MISRA C:2012 exists as a mature subset with a free checker |
| C++ | ISO C++17 (`CMAKE_CXX_STANDARD 17`), ISO mode `-std=c++17` (`CMAKE_CXX_EXTENSIONS OFF`, DOC-29; DE2 closed) | application frame, RTE runtime, drivers, platform port, safety mechanisms (supervisor, watchdog, safe I/O, MPU, ROM check) | `arm-none-eabi-g++` | the language of OpenBSW; classes and interfaces carry the layer and role model of the architecture, templates and `constexpr` move checks to compile time (`static_assert` on generated tables); used as the subset of section 4 |
| ARM assembly | GNU as, unified syntax | startup, FreeRTOS port, context switch of the fault handler | `arm-none-eabi-gcc` | the few places C cannot express (stack switch, exception entry) |
| Linker script | GNU ld | memory layout, generated from `bl_memory.ld.j2` by blgen | `arm-none-eabi-ld` | layout is data of the derivative (`mcudb`), not code |
| Python | 3.12 (`tools/ci/.python-version`) | configuration check, generators, verifiers, test runner, Studio | CPython | the tools run on the host only; their output is checked (tool confidence, section 5), not their source against a coding standard |
| YAML, Jinja2, CMake | — | configuration levels, generator templates, build description | — | declarative; checked by `nxconfig` with YAML paths, templates by reference outputs (PLAT-78) |
| Simulink | MATLAB R2024a, Embedded Coder, AUTOSAR Blockset | the example application `swc/DemoSwc` | — | out of the element's scope (concept 1.2); the integrator's language and code generator |

What a language leaves undefined or weak is covered by the guidelines of section 3: undefined and implementation-defined
behaviour of C by MISRA C:2012, of C++ by NXCPP; the target's implementation-defined choices (`int` 32 bit, little
endian, `wchar_t` 4 byte, plain `char` unsigned, bit-fields unsigned) are fixed by the toolchain file and given to
cppcheck as `--platform=arm32-wchar_t4` (`tools/misra/rules.yaml`).

---

## 3. Coding guidelines

### 3.1 Per language

| Code | Guideline | Checked by | Owner of the rule set |
|---|---|---|---|
| C (own code) | MISRA C:2012, rule 15.5 disapplied (PLAT-76), deviations DEV-001 … DEV-014 | cppcheck MISRA add-on, cppcheck core, clang-tidy — gating in CI | [`static-analysis.md`](static-analysis.md), `tools/misra/rules.yaml` |
| C++ (own code) | **NXCPP** (section 4) | compiler, clang-tidy, cppcheck core and add-on rule 17.2, code scan of `dev-environment` test, review | this document; the tool part lives in `tools/misra/rules.yaml` |
| C and C++ (OpenBSW copies) | the same checks, **reported separately, not gating** | as above | [`static-analysis.md`](static-analysis.md) section 2; qualification is concept 8.1 |
| Naming and layout (C++ and C) | OpenBSW conventions `CPP-001` … `CPP-025`, `FILE-000` …, and OpenBSW's `.clang-format`, adopted by reference | review (DE4) | `external/openbsw/doc/dev/guidelines/conventions` |
| Comments | English, short, in the style of the surrounding code | review | [`CONTRIBUTING.md`](../../CONTRIBUTING.md) |
| Python tools | core free of Qt, validation messages with a YAML path (architecture section 11, questions 8), versions pinned | review question 8 in every task, host tests of each tool | [`tool-classification.md`](tool-classification.md) |
| Generated C/C++ | not hand-edited; the generator and its templates are the unit; output read as context by the analysis | reference outputs (PLAT-78), generator tests with own profiles | [`tool-classification.md`](tool-classification.md) section 4 |
| Simulink model code | the integrator's (MAB/MISRA settings of his code generator) | out of scope | concept 1.2, AoU-17 |

### 3.2 Topics of 6-5.4.3

6-5.4.3 lists topics that coding guidelines are to address, with a recommendation level per ASIL. The table maps each
topic, in our own words, to what neXtBSW does.

| Topic | C | C++ | Gap |
|---|---|---|---|
| Low complexity | none by tool | none by tool (NXCPP-26, review) | **DE3**: no limit; proposal P1 |
| Language subset | MISRA C:2012 | NXCPP | MISRA C++:2023 not claimed (decision q-doc22-cpp) |
| Strong typing | MISRA C essential type rules (10.x, 11.x) | NXCPP-08 … NXCPP-11 | — |
| Defensive implementation | MISRA C 17.x, 21.x; clang-tidy `bugprone-*`, `cert-*` | NXCPP-07, NXCPP-14, NXCPP-22 | — |
| Well-trusted design principles | architecture rules (layers, roles, D1–D14) | same, plus NXCPP-03 … NXCPP-06 (OpenBSW "disallowed features") | — |
| Unambiguous graphical representation | only for models; the Simulink application is out of scope | — | — |
| Style guide | OpenBSW `.clang-format` | same | **DE4**: not run on neXtBSW code; proposal P4 |
| Naming conventions | OpenBSW conventions | same (NXCPP-25) | **DE6**: review only |
| Concurrency aspects | MISRA C 8.x, add-on | NXCPP-21, NXCPP-23 (review) | **DE6** |

---

## 4. The neXtBSW C++ subset (NXCPP)

### 4.1 Basis

NXCPP is a **positive list of restrictions** on C++17 for neXtBSW's own C++ code (`ecu`, `libs`, `platforms`
outside `3rdparty`, the C++ parts of `bootloader`), in the order of the enforcement strength. It starts from the
OpenBSW "disallowed features" (no dynamic memory, no RTTI, no exceptions), adds the checks that PLAT-74 already runs,
and names each rule's enforcement. It is **not** a MISRA C++:2023 compliance statement (section 4.5).

Scope rules:

- **Host tests and mocks** (`**/test/**`) are not product code; NXCPP-05, NXCPP-19 and NXCPP-20 do not apply there.
- **OpenBSW copies** (`tools/openbswsync/copies.yaml`) are checked by the tools like our code but reported separately
  (static-analysis.md section 2); the code scan of section 4.3 includes them, because an OpenBSW construct in the
  image is in the image.
- **Generated code** (board headers, RTE, CAN database, UDS tables) follows NXCPP through its templates; a finding in
  it is a generator finding.

### 4.2 Rules

Enforcement: **tool** — a tool rejects a violation (build fails with `-Werror`, or the static analysis or the
consistency test turns red); **tool + review** — a tool catches part of the rule, the rest is reviewed; **review** —
only a review catches it, and the rule names a proposal (section 4.6) that would make it a tool rule.
"State" is the measurement at `6f868b5`.

<!-- nxcpp:start -->
| ID | Rule (own words) | Topic | Enforcement | Enforced by | State 2026-09-28 |
|---|---|---|---|---|---|
| NXCPP-01 | C++17 is the language level of all product code | subset | tool | `cmake: CMAKE_CXX_STANDARD 17` | set in the root `CMakeLists.txt` |
| NXCPP-02 | No GNU language extension outside platform code; in platform code only attributes (`section`, `used`, `naked`, `always_inline`) and inline assembly | subset | tool + review | `cmake: CMAKE_CXX_EXTENSIONS OFF`, `cmake: CMAKE_C_EXTENSIONS OFF` (P2, DOC-29) | ISO mode since DOC-29: an extension that conflicts with ISO (`typeof`, `asm` without underscores) does not compile; extensions that ISO mode still accepts without `-pedantic` and the platform-code exception stay with the review; every profile builds unchanged (no source change needed) |
| NXCPP-03 | No exceptions: no `throw`, `try`, `catch` | well-trusted principles | tool | `compiler: -fno-exceptions`, `scan: exceptions` | 0 uses |
| NXCPP-04 | No run-time type information: no `dynamic_cast`, no `typeid` | well-trusted principles | tool | `compiler: -fno-rtti`, `scan: rtti` | 0 uses |
| NXCPP-05 | No dynamic memory: no `malloc`/`free`, no `new`/`delete` expression; placement `new` only into storage of static size | well-trusted principles | tool | `scan: heap`, `elfcheck` code `heap-symbol` (P5, DOC-28) | 0 heap uses in the source; 1 placement `new` (`safeSupervisor/utils/explicitly_constructible.h`, static buffer); since DOC-28 the platform defines both `operator delete` forms and `elfcheck` rejects an image that links the allocator (DE5 closed) |
| NXCPP-06 | No recursion, direct or indirect | well-trusted principles | tool | `cppcheck-addon: misra-c2012-17.2` | 0 findings since SYS-26 (BEF-002 closed) |
| NXCPP-07 | Every local variable and every member is initialised before use | defensive | tool | `clang-tidy: cppcoreguidelines-init-variables`, `clang-tidy: cppcoreguidelines-pro-type-member-init`, `warning: -Wall` | findings in the baseline, none new |
| NXCPP-08 | No implicit conversion to or from `bool` | strong typing | tool | `clang-tidy: readability-implicit-bool-conversion` | 55 in the baseline (C and C++), none new |
| NXCPP-09 | No implicit narrowing conversion | strong typing | tool | `clang-tidy: bugprone-narrowing-conversions` | baseline, none new |
| NXCPP-10 | No bit operation on a signed operand | strong typing | tool | `clang-tidy: hicpp-signed-bitwise` | 43 in the baseline, none new |
| NXCPP-11 | No C-style cast between unrelated types; `reinterpret_cast` only to reach a memory-mapped register or a fixed location of the memory layout, or to view an object as bytes; `const_cast` never to write an object defined `const` | strong typing | tool + review | `clang-tidy: cppcoreguidelines-pro-type-cstyle-cast`, review for the named forms | 46 `reinterpret_cast`, 3 `const_cast` in product code; the register forms are DEV-002/DEV-003 |
| NXCPP-12 | Comparisons of signed with unsigned values are not mixed | strong typing | tool | `warning: -Wall` | build warning-free with `-Werror` |
| NXCPP-13 | A class with virtual functions has a virtual or a protected non-virtual destructor; no object slicing | well-trusted principles | tool | `clang-tidy: cppcoreguidelines-virtual-class-destructor`, `clang-tidy: cppcoreguidelines-slicing` | 37 in the baseline, none new |
| NXCPP-14 | Every `switch` handles each case or has a `default`; no silent fall-through | defensive | tool | `clang-tidy: hicpp-multiway-paths-covered`, `warning: -Wextra` | baseline, none new |
| NXCPP-15 | Bodies of `if`, `else`, loops are compound statements; indentation matches the structure | style | tool | `clang-tidy: readability-braces-around-statements`, `clang-tidy: readability-misleading-indentation` | baseline, none new |
| NXCPP-16 | No variable-length arrays | subset | tool | `warning: -Wvla` | build warning-free with `-Werror` |
| NXCPP-17 | No definition of a function or variable with external linkage in a header | subset | tool | `clang-tidy: misc-definitions-in-headers` | 0 findings since SYS-133 (BEF-003, BEF-004 closed) |
| NXCPP-18 | No C-style variadic function, except a formatting sink whose format string the compiler checks | subset | tool + review | `clang-tidy: cert-dcl50-cpp` | 1 in the baseline (`hardfault::writeLine`) |
| NXCPP-19 | No `goto` | well-trusted principles | tool | `scan: goto` | 0 uses |
| NXCPP-20 | Standard library only from the allow-list of section 4.3 (freestanding, no allocation); containers from ETL/`estd` with static capacity | well-trusted principles | tool | `scan: headers` | 13 standard headers in use, all on the list |
| NXCPP-21 | No function-local `static` object with dynamic initialisation that more than one task or an interrupt can reach first (the code is built with `-fno-threadsafe-statics`) | concurrency | review | P6 | not measured |
| NXCPP-22 | `assert` is never a safety mechanism: release builds define `NDEBUG`; a safety check is explicit code with a reaction | defensive | review | P6 | 6 files use an assert macro; none named as a mechanism in concept 6 |
| NXCPP-23 | Data shared between tasks or with an interrupt only through `std::atomic`, the OpenBSW locks or the RTE copies; `volatile` only for hardware registers and memory that hardware or a debugger writes | concurrency | review | P6 | not measured |
| NXCPP-24 | A `union` only as a register overlay (word and bit-field views of the same peripheral register), never to reinterpret data | strong typing | review | P6 | 6 unions, all register overlays (`safeMemory/mpu.h`, `can/FlexCANDevice.h`) |
| NXCPP-25 | Names and file layout follow the OpenBSW conventions (`CPP-001` … `CPP-025`) | naming | review | P4 | reviewed per task, no record (DE6) |
| NXCPP-26 | Functions stay small and flat: a function whose cognitive complexity exceeds the limit is split or justified | complexity | review | P1 | no limit and no measurement (DE3) |
| NXCPP-27 | The code is formatted with OpenBSW's `.clang-format` | style | review | P4 | not run on neXtBSW code (DE4) |
<!-- nxcpp:end -->

Counted: 27 rules; **20 by tool** — 17 fully (01, 03, 04, 05, 06, 07, 08, 09, 10, 12, 13, 14, 15, 16, 17, 19, 20) and 3
as tool + review (02, 11, 18) — and **7 by review** (21, 22, 23, 24, 25, 26, 27). The consistency test recounts
the table.

### 4.3 The code scan (`scan:` rules)

Four NXCPP rules have no clang-tidy or cppcheck check among the active ones, but can be checked with certainty by
reading the source: the constructs are keywords or `#include` lines. The consistency test
[`test/test_dev_environment.py`](test/test_dev_environment.py) scans `bootloader`, `ecu`, `libs` and `platforms`
(C++ files `.cpp`, `.h`, `.hpp`; without `test`, `tests`, `3rdparty`, `generated`; 357 files at `6f868b5`), after removing comments and
string literals:

| Scan | Rejects |
|---|---|
| `exceptions` | the keywords `throw`, `try`, `catch` |
| `rtti` | `dynamic_cast`, `typeid` |
| `heap` | a call of `malloc`, `calloc`, `realloc`, `free`; a `new` expression that is not a placement `new` (`new (` …`)`); a `delete` expression (`= delete` of a special member is allowed) |
| `goto` | the keyword `goto` |
| `headers` | an `#include <…>` of a standard C++ header that is not on the allow-list |

Allow-list of standard headers (freestanding or without allocation; each one used today):

<!-- nxcpp-headers:start -->
`algorithm`, `array`, `atomic`, `cinttypes`, `cstdarg`, `cstddef`, `cstdint`, `cstdio`, `cstring`,
`initializer_list`, `iterator`, `new`, `numeric`, `tuple`
<!-- nxcpp-headers:end -->

A header outside the list (for example `vector`, `string`, `map`, `memory`, `functional`, `iostream`, `thread`,
`mutex`) needs either an entry here with the reason (it allocates nothing and needs no OS support) or a deviation
(section 4.4). `cstdio` is on the list for `snprintf`-style formatting only; file and stream functions are covered by
NXCPP-05 (they allocate) and by the newlib stubs, which return errors. `new` (DOC-28) only declares the platform's
own `operator delete` and placement `new`; it allocates nothing, and `elfcheck` rejects an image that links
`operator new` or the allocator. Headers of C (`stdint.h` …) and of the vendor,
ETL, `estd` and OpenBSW are not standard C++ headers and are not scanned.

The scan is a heuristic reader, not a compiler. It is exact for the keywords and includes it looks for; it cannot see
a heap call hidden behind a macro or a library function. That is why P5 adds the check that closes it: the linked image must not contain the allocator. Since DOC-28
(2026-10-01) `elfcheck` rejects every image that defines it (code `heap-symbol`), so NXCPP-05 is a **tool** rule (DE5 closed).

### 4.4 Deviations

A deviation from an NXCPP rule is written, justified and approved like a MISRA deviation, in the **same** process
([`static-analysis.md`](static-analysis.md) section 4):

1. **Tool rule, finding of clang-tidy or cppcheck** → an entry in `tools/misra/deviations.yaml` (`DEV-nnn`, rule,
   files, source-line pattern or symbol, reason in own words, raised by, `approved_by`). This exists today; the
   entries DEV-001 … DEV-014 already cover the register access of NXCPP-11.
2. **Tool rule, finding of the code scan** → a comment on the same line, `// NXCPP-nn deviation: <DEV-nnn>`, and an
   entry `DEV-nnn` in `tools/misra/deviations.yaml` with rule `NXCPP-nn` (**[Proposal]** — the `misra` checker
   accepts only the rule names of its tools today; until it accepts `NXCPP-nn`, the test honours the comment only
   when the `DEV-nnn` it names exists in the file).
3. **Review rule** → the reviewer records the deviation in the task's changelog fragment ("Abweichung NXCPP-nn:
   Grund") and, if it stays in the code, the comment of item 2.

Approval is the user's, as for every deviation; an open approval belongs on the decision board. No NXCPP deviation
exists today beyond the approved DEV-002/DEV-003 (register and fixed-location access, NXCPP-11) and the baseline
entry of `cert-dcl50-cpp` (NXCPP-18), which is a baseline finding, not a deviation; it needs a decision (DE6).

### 4.5 What NXCPP does not cover, compared with MISRA C++:2023

The subset was compared topic by topic with the rule groups of MISRA C++:2023 (by group, not by rule text). It covers
the groups on exceptions, dynamic memory, RTTI, recursion, initialisation, casts, virtual functions and destructors,
`switch` and statement structure, and part of the integral-conversion and concurrency groups. Not covered, and
therefore **not claimed**:

- the full essential-type model of integral and floating arithmetic (only narrowing, signed bit operations and signed/
  unsigned comparison are checked);
- rules on templates, lambdas and `constexpr` beyond what the compiler rejects;
- lifetime rules on references and pointers to temporaries beyond `clang-analyzer-cplusplus.*` and `core.*`;
- the rules on the preprocessor for C++ units (the add-on's C view of C++ gives notices only, static-analysis.md 1);
- the resource and library rules outside the allow-list of section 4.3.

An integrator whose process requires MISRA C++:2023 runs a qualified tool on neXtBSW's C++ code himself; this is a
proposed assumption of use for DOC-19 (next to the one of static-analysis.md section 6).

### 4.6 Proposals that turn review rules into tool rules

Each proposal changes a file outside this task's path and is therefore **not done here**; it is listed for the
coordinator (section 9).

| Proposal | Rules | Change | File |
|---|---|---|---|
| P1 | NXCPP-26 | enable `readability-function-cognitive-complexity` (threshold 25 as a start), first as a report, then gating with the baseline mechanism | `tools/misra/rules.yaml` |
| P2 | NXCPP-02 | set `CMAKE_CXX_EXTENSIONS OFF` and `CMAKE_C_EXTENSIONS OFF`, build every profile, keep the attributes that need GNU mode behind the OpenBSW compiler macros | root `CMakeLists.txt`. **Done by DOC-29 (2026-10-01):** both set OFF, `-ffp-contract=fast` kept explicit (ISO mode would switch it off and change the object code), no source change, test `cmake-iso-mode` |
| P3 | NXCPP-19 | replace the scan by the clang-tidy check `cppcoreguidelines-avoid-goto` (same result, one mechanism) | `tools/misra/rules.yaml` |
| P4 | NXCPP-25, NXCPP-27 | a check `clang-format --dry-run` with OpenBSW's `.clang-format` over the changed own files (the clang-format wheel is available like clang-tidy), and `readability-identifier-naming` with the OpenBSW options (their `.clang-tidy` carries them commented out) | `tools/misra/rules.yaml`, `tools/misra/requirements.txt` |
| P5 | NXCPP-05 | (a) the platform defines `operator delete(void*)` and `operator delete(void*, unsigned int)` itself, as a call that never returns into the fault reaction (a deleting destructor is never run, because no `delete` expression exists), so libstdc++'s version and with it newlib's allocator are no longer linked (DE5); (b) `elfcheck` then requires that the linked image contains none of `malloc`, `_malloc_r`, `free`, `_sbrk`, `operator new` | `platforms/*/port` (or `libs/bsp`), `tools/elfcheck` |
| P6 | NXCPP-21 … NXCPP-24 | a review checklist with these four rules in the task template (`tools/todo/todoplan.py brief`), answered in the changelog fragment | `tools/todo` |

---

## 5. Tools, versions and tool confidence

### 5.1 Pinned tools

The version is the one pinned in the named file (the only place it is set, cm-plan.md section 3); the TCL is that of
[`tool-classification.md`](tool-classification.md) section 3 today. "Phase" uses the rows of section 1.

<!-- tools:start -->
| Tool | Version | Pinned in | Phase | TCL today |
|---|---|---|---|---|
| arm-none-eabi-gcc and binutils (xPack) | 11.3.1 | `tools/ci/versions.toml` | build, integration | TCL 3 |
| CMake | 4.4.3 | `tools/ci/versions.toml` | build | TCL 2 |
| Ninja | 1.13.2 | `tools/ci/versions.toml` | build | TCL 2 |
| Python | 3.12.10 | `tools/ci/.python-version` | generators, verifiers | with the generators |
| Jinja2 | 3.1.6 | `tools/ci/constraints.txt` | generators | with the generators |
| PyYAML | 6.0.3 | `tools/ci/constraints.txt` | generators, configuration | with the generators |
| ruamel.yaml | 0.19.1 | `tools/ci/constraints.txt` | configuration, Studio | with the generators |
| pydantic | 2.13.5 | `tools/ci/constraints.txt` | configuration | with the generators |
| ziglang (zig cc, clang for host tests) | 0.16.0 | `tools/ci/constraints.txt` | unit verification | TCL 2 |
| pytest | 9.1.1 | `tools/ci/constraints.txt` | unit verification (Studio) | TCL 1 |
| cppcheck (wheel 1.5.1) | 2.17.1 | `tools/misra/requirements.txt` | unit verification | TCL 2 |
| clang-tidy | 22.1.8 | `tools/misra/requirements.txt` | unit verification | TCL 2 |
| StrictDoc | 0.30.1 | `tools/ci/constraints-docs.txt` | requirements | TCL 1 |
| python-can | 4.6.1 | `tools/ci/constraints.txt` | embedded testing, flashing | with canflash (TCL 1) |
| PySide6 | 6.11.2 | `tools/ci/constraints.txt` | Studio | TCL 1 |
| actionlint | 1.7.12 | `tools/ci/versions.toml` | CI definition only | not classified (TI1) |
| Eclipse OpenBSW | `3b6e702` | submodule gitlink `external/openbsw` | platform code | pre-existing software (concept 8.1) |
| FreeRTOS (in OpenBSW) | 10.6.2 | submodule gitlink `external/openbsw` | platform code | pre-existing software (concept 8.2) |
| ETL (in OpenBSW) | 20.49.0 | submodule gitlink `external/openbsw` | platform code | pre-existing software (concept 8.2) |
<!-- tools:end -->

### 5.2 Own tools

The generators and verifiers of the repository (`nxconfig`, `bswgen`, `blgen`, `rtegen`, `dbcgen`, `diaggen`,
`a2lgen`, `calseg`, `canflash`, `romCheckImage`, `schedana`, `elfcheck`, `tools/test`, `tools/misra`, `tools/trace`,
`tools/boardtest`, Studio) are identified by the repository commit and, at a release, by the tag (cm-plan.md section
3). Their TCL is in the summary table of [`tool-classification.md`](tool-classification.md) section 3; it is not
repeated here, so that one table stays the truth.

### 5.3 Tools not pinned

| Tool | Used for | Why not pinned | Effect |
|---|---|---|---|
| MATLAB R2024a with Simulink, Embedded Coder, AUTOSAR Blockset | the example application `swc/DemoSwc`; its code is checked in | licensed installation, out of the element's scope | none for the element; the integrator's tool (DE7) |
| SEGGER J-Link software (EDU) | flashing and reading at the bench | installed on the workstation | board evidence names no J-Link version (DE7) |
| Vector driver for VN1610 | CAN at the bench | installed on the workstation | as J-Link |
| Git | configuration management | any recent version | the repository format is stable; no effect on the image |

### 5.4 Changing a tool

A new version of a pinned tool is a change under [`cm-plan.md`](cm-plan.md): version, URL and SHA-256 are changed
together in the one file of the table, then `python -m tools.ci setup` and a full `python -m tools.ci run --clean`;
a new ARM GCC must build every target profile warning-free with `-Werror`; a new cppcheck or clang-tidy needs a new
baseline, reviewed finding by finding (`tools/misra/requirements.txt` header). The TCL argument of the tool is
re-read for the new version (tool-classification.md section 3). This document's table is then updated; its
consistency test fails until it is.

---

## 6. Build flow

One command (`cmake --preset <profile>` and `cmake --build --preset <profile>`) runs all steps; the preset base is
`cmake/NxPresets.json` (Ninja Multi-Config, default `RelWithDebInfo` with `-O2 -g3 -DNDEBUG`,
`CMAKE_COMPILE_WARNING_AS_ERROR ON`, C `-Wall -Wextra`, C++ `-Wall -Wextra -Wvla`). The toolchain file
`cmake/toolchains/NxArmNoneEabi-gcc.cmake` adds the core options from the derivative and the C++ options of NXCPP-03,
NXCPP-04 and NXCPP-21 (`-fno-exceptions -fno-rtti -fno-threadsafe-statics`), plus `-fstack-usage`,
`-ffunction-sections -fdata-sections`, `-fno-common`, `-fshort-enums`, `-mno-unaligned-access`.

| Step | What | Tool | Check that follows |
|---|---|---|---|
| 1 | read and check the three configuration levels, derive values (clock plan, CAN timing, memory layout) | `nxconfig` | messages with YAML path; `nxconfig check` |
| 2 | generate board headers, memory layout and linker script, bootloader configuration | `bswgen`, `blgen` | reference outputs (`bswgen-reference`), `static_assert` in generated headers |
| 3 | generate RTE, CAN database, UDS tables | `rtegen`, `dbcgen`, `diaggen` | reference outputs (`rtegen-reference`), `static_assert` |
| 4 | compile and link bootloader, updater and application | `arm-none-eabi-gcc`, CMake, Ninja | warning-free with `-Werror` |
| 5 | post-link: calibration header, ROM check references, validity record | `calseg`, `romCheckImage`, `canflash record` | CRC over the real image (`rom-check-image`) |
| 6 | A2L for calibration | `a2lgen` | `a2lgen`, `xcp-host` |
| 7 | check the linked image against the configuration | `elfcheck` (sections, core attributes, timing tables, priorities) | `elfcheck*` entries of `tools/test/tests.yaml` |

A build directory belongs to one compiler; a moved compiler needs `--fresh` (`cmake/NxBuildDirCheck.cmake`).

---

## 7. Test and verification flow

### 7.1 Host and CI

All automated verification is listed in **one** file, `tools/test/tests.yaml` (252 entries at `6f868b5`), and run by
`tools/test`; the CI runs the same list (`python -m tools.ci run --strict`) with the pinned tools. A test file that no
entry covers makes the run red; a missing tool is reported as skipped, never as passed, and is red under `--strict`.

| Phase (part 6) | Verification | Entries / tool | Gating |
|---|---|---|---|
| Unit verification (6-9) | requirement-based host tests of C and C++ units, compiled with `zig c++`/`zig cc` (`kind: zig`, 78 entries) | `tools/test` | yes |
| Unit verification | static analysis (MISRA C, NXCPP tool rules) | `tools/misra`, CI stage "Statische Analyse" | yes (`enforce = true`) |
| Unit verification | statement and branch coverage of the host tests | `tools/test/coverage.py`, `coverage.yaml` | **no** (`enforce = false`, TOOL-40) |
| Unit verification | fault injection by mutants into the unit under test | the `*-mutations` entries (`--slow`) | yes, when run |
| Unit verification | NXCPP code scan and this document's consistency | `dev-environment` (proposed entry, section 9) | yes, once registered |
| Integration (6-10) | target builds of every profile, warning-free | `kind: cmake_preset` | yes |
| Integration | image checks: sections, timing tables, priorities, disassembly of the fault paths | `elfcheck*`, `safe-outputs-order`, `safety-context-elf`, `application-linker` | yes |
| Generators (tool confidence) | own test profiles, reference outputs, `nxconfig` checks | `nxconfig*`, `bswgen*`, `rtegen*`, … | yes |
| Requirements and trace | structure, dead references, missing tests | `tools/trace`, `safety-trace` | yes |

The host tests run with a different compiler than the image; the analysis of that difference is open (gap row 6-25,
tool-classification.md 3.14 proposal I) and is not repeated here.

### 7.2 Outside the CI

`tools/ci/pipeline.toml` names what the CI deliberately does not run, and each report lists it: MATLAB/Simulink (the
model tests run at the workstation, `--slow`), hardware (flashing, J-Link, CAN, console), `doc/confidential`, keys.

### 7.3 Embedded software tests at the board

Board runs (fmea.md section 8, B1 …; `tools/boardtest` with recorded captures) run at the workstation with the eval
board, a J-Link and a Vector VN1610, under the hardware safety rules of `CLAUDE.md`. Their records name the image CRC
and commit (cm-plan.md section 7); the bench software versions are not recorded (DE7).

---

## 8. Findings

Findings of this document, proposed for the anomaly register ([`anomaly-register.md`](anomaly-register.md), not
entered here, section 9).

| ID | Finding | Effect | Proposal |
|---|---|---|---|
| DE1 | `tool-classification.md` names the compiler **GCC 11.4** (inventory row, heading 3.14, TCL argument); the pinned and used compiler is **xPack GCC 11.3.1** (`tools/ci/versions.toml`, TOOL-03; S32DS with 11.4 is uninstalled) | the TCL 3 argument and proposal L (known wrong-code faults) refer to a version that no longer builds the image | tool-classification.md: 11.4 → 11.3.1 in section 2 and 3.14, proposal L against 11.3.1 |
| DE2 | `CMAKE_C_EXTENSIONS`/`CMAKE_CXX_EXTENSIONS` are not set, so C and C++ are compiled as `gnu99`/`gnu++17` | a GNU extension is accepted silently; the language of section 2 is not exactly ISO | P2. **Closed by DOC-29 (2026-10-01):** the root build compiles C and C++ in ISO mode (`CMAKE_C_EXTENSIONS OFF`, `CMAKE_CXX_EXTENSIONS OFF`, `-ffp-contract=fast` explicit), test `cmake-iso-mode` |
| DE3 | no complexity limit for C or C++ | 6-5.4.3 topic "low complexity" not covered by a tool | P1 |
| DE4 | OpenBSW's `.clang-format` is not applied to neXtBSW code; no format check | style guide only by review | P4 |
| DE5 | **the demo image contains newlib's allocator.** Measured on the demo build (xPack GCC 11.3.1, `RelWithDebInfo`, `arm-none-eabi-nm` and the linker map `application.map`): `app.cpp.obj` references the sized `operator delete(void*, unsigned int)` (the deleting destructors of classes with a virtual destructor, which their vtables name — for example that of the OpenBSW class `lifecycle::LifecycleManager::ComponentTransitionExecutor`, emitted in `app.cpp`); libstdc++ `del_ops.o` → `del_op.o` → `free` → newlib nano `malloc.o`, `mallocr.o`, `freer.o`, `sbrkr.o` → libnosys `sbrk.o`. Linked: `malloc` 16, `_malloc_r` 236, `free` 16, `_free_r` 152, `_sbrk_r` 32, `_sbrk` 28 and the two `operator delete` 4 + 4 bytes = **488 bytes**. The bootloader image contains none of them. No code of ours calls it (scan `heap`: 0), so it is dead at run time; but a future `delete`, a library function that allocates, or a call through `strdup`/`printf` paths would link and run silently, and libnosys `_sbrk` grows from the end of `.bss` without a limit | NXCPP-05 is only true in the source; the image carries an unbounded allocator; SM-10 (MPU) would catch a heap growing into a protected region, not one growing within the task's RAM | P5 (a) and (b). **Closed by DOC-28 (2026-10-01):** `NoHeapOperatorDelete.cpp` of the platform defines both forms (ending in the safe supervisor's fault reaction), `elfcheck` code `heap-symbol` rejects the allocator in every image; demo image −624 B flash, −416 B RAM; tests `no-heap-operator-delete`, `elfcheck-demo` |
| DE6 | the review rules of NXCPP (9) have no record per change; the `cert-dcl50-cpp` finding (`hardfault::writeLine`) sits in the baseline instead of a deviation | review evidence missing, as gap rows 6-19/6-22 | P6; decide the `writeLine` finding as a deviation (formatting sink with checked format) |
| DE7 | MATLAB, J-Link software and the Vector driver are not pinned and their versions not recorded in board evidence | a board record cannot be reproduced with the same bench software | record the bench versions in `tools/boardtest` captures |

---

## 9. Proposals to the coordinator (not done here)

This task may change only this document, its test and the changelog fragment. Everything else is a proposal:

1. **`tools/test/tests.yaml`** — new entry for the consistency test and the NXCPP scan:

   ```yaml
     - name: dev-environment
       group: python
       kind: unittest
       comment: >
         DOC-22: Entwicklungsumgebung doc/safety/dev-environment.md - Werkzeugversionen gegen versions.toml,
         constraints.txt, misra/requirements.txt, .python-version und Submodul; NXCPP-Werkzeugregeln gegen
         Compiler-Optionen, Presets und tools/misra/rules.yaml; Code-Scan (Ausnahmen, RTTI, Heap, goto,
         Standard-Header-Positivliste) ueber den eigenen C++-Code; Lizenzregel; Gegenproben mit beschaedigten Kopien
       args: [doc/safety/test/test_dev_environment.py]
       covers: [doc/safety/test/test_dev_environment.py]
   ```

2. **`doc/safety/anomaly-register.md`** — register DE1–DE7 with owner and status `open`, and add the document row
   (the register's test `safety-anomaly-register` is red until it exists, because every safety document needs one):

   ```text
   | `dev-environment.md` | Development environment: languages, guidelines, tools, build and test flow | DOC-22 | pending (acceptance of the DOC-22 report, board item cloud-1001-doc-22) | 1, 2026-09-28 | draft | present (Working state) | header; CHANGELOG DOC-22 |
   ```

   and in its section 0 "**21** safety documents: 2 draft" → "**22** safety documents: 3 draft". With both changes
   applied to a scratch copy, `safety-anomaly-register` was green.
3. **`doc/safety/safety-case.md`** — row EW-14: status from `missing` to `present (draft)`, reference this document.
4. **`doc/safety/iso26262-gap.md`** — rows 6-01 and 6-02: evidence `dev-environment.md`; 6-02 "C++ subset missing"
   → "NXCPP defined (DOC-22), 18 of 27 rules by tool"; section 6 task 21 done.
5. **`doc/safety/safety-plan.md`** — row P6-01: work product present.
6. **`doc/safety/static-analysis.md`** section 1 — the C++ row: "not checked" → "NXCPP (dev-environment.md section 4);
   MISRA C++:2023 not claimed (q-doc22-cpp)".
7. **`doc/safety/tool-classification.md`** — DE1.
8. **Follow-up tasks** — P1 (complexity), P2 (extensions off), P4 (format and naming), P5 (allocator in the image),
   P6 (review checklist); P3 is a small clean-up that can ride with P1. Suggested priority: P5 and P2 before a safety
   release (they close a rule of the subset), P1/P4/P6 with the confirmation reviews (SYS-60).
9. **DOC-19 (safety manual)** — assumption of use: an integrator who needs MISRA C++:2023 runs a qualified checker on
   neXtBSW's C++ code (section 4.5).

---

## 10. Review questions of the architecture (section 11)

This task changes a document and adds a test; no reusable code, generator or bootloader is changed.

1. Board without SBC — not affected; NXCPP is independent of board parts.
2. Other crystal and clock — not affected.
3. Other derivative, S32K3/STM32, Cortex-R — the tool table names the toolchain and options per core as derived by
   `nxconfig`; NXCPP applies to all families alike. The scan covers `platforms/*` outside `3rdparty`, including
   `stm32` and `rm4x`.
4. Several CAN buses — not affected.
5. Several tasks — NXCPP-21 and NXCPP-23 are written for any number of tasks and interrupts.
6. One configuration level per value — no value added; tool versions stay in their one file each, this document only
   reads them (and the test checks that it reads them right).
7. Host test with own profile — the consistency test checks its rules on synthetic broken copies of the document and
   on synthetic source snippets, not only on the repository.
8. Tools free of Qt — the test imports only `yaml` and the standard library.
9. Device drivers — not affected.
10. `platforms/` — no file added there.

---

## 11. Sources

- ISO 26262-6:2018, clauses 5.4.1–5.4.3, 5.5.1 (cited by clause only).
- `doc/safety/iso26262-gap.md` rows 6-01, 6-02, 6-21, 6-25, section 2.7, section 6 task 21.
- `doc/safety/static-analysis.md`, `tools/misra/rules.yaml`, `tools/misra/deviations.yaml`, `tools/misra/baseline.yaml`.
- `doc/safety/tool-classification.md` sections 2, 3; `doc/safety/cm-plan.md` sections 3, 6, 7.
- `tools/ci/versions.toml`, `tools/ci/.python-version`, `tools/ci/constraints.txt`, `tools/ci/constraints-docs.txt`,
  `tools/misra/requirements.txt`, `tools/ci/pipeline.toml`, `.github/workflows/ci.yml`.
- `CMakeLists.txt`, `cmake/NxPresets.json`, `cmake/toolchains/NxArmNoneEabi-gcc.cmake`.
- `external/openbsw/doc/dev/guidelines/conventions/*.rst` (naming, disallowed features), `external/openbsw/.clang-format`,
  `external/openbsw/.clang-tidy`.
- MISRA C:2012, MISRA C++:2023 (compared by rule group; no text reproduced).
