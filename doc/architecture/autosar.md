<!-- SPDX-License-Identifier: Apache-2.0 -->

# neXtBSW and AUTOSAR: what is actually here

Status: 2026-09-17 (**DOC-07**), every row below checked against the repository at this commit. Mode management
added 2026-09-19 (**RTE-08**), implicit access 2026-09-19 (**RTE-09**).

## 0. Read this first

AUTOSAR is a registered trademark of the AUTOSAR development partnership and neXtBSW has nothing to do with that
partnership. **neXtBSW is not AUTOSAR compliant, not AUTOSAR certified, and no conformance has been tested or
claimed.** It is a hobby basic software that borrows from AUTOSAR where the standard is useful and departs from it
where it is not — and this page exists so that a reader who knows AUTOSAR can tell in a few minutes which is which.

The project calls itself "AUTOSAR-like" (`CLAUDE.md`, `README.md`). That phrase is meant literally and narrowly: some
parts implement a published AUTOSAR specification, some parts use AUTOSAR vocabulary over an implementation of our
own, and large parts of AUTOSAR are simply not there. The three columns below say which.

Binding target picture: [`README.md`](README.md). Overview of the architecture: [`overview.md`](overview.md).
Where the specifications themselves live, what the document set contains and what the current release R25-11
changed: [`autosar-standard-set.md`](autosar-standard-set.md).
What the machine-readable parameter definitions of R25-11 give the configuration tools (and what they do not):
[`autosar-ecuc-evaluation.md`](autosar-ecuc-evaluation.md).

## 1. The short answer

| Area | Follows a published specification | Borrows terms and concepts only | Deliberately different, or absent |
|---|---|---|---|
| Base types | `Std_Types.h`, `Platform_Types.h`, `Compiler.h` — names and values as specified, minimal subset | — | no version-info types, no development-error interface, memory classes are empty macros |
| E2E protection | `libs/bsw/e2e`: profiles P01, P02, P11 and the CRC routines, behaviour per PRS/SWS R22-11 | — | `E2E_SMCheck` state machine and the `Forward` functions not implemented; API types are `uint8_t` instead of the specified enums; P02 `Offset` field missing |
| Software components | ARXML from Embedded Coder in the `r4.0` schema; an application SW component with sender-receiver ports, runnables and timing events; required mode ports with mode switch events (RTE-08); explicit and implicit data access points (RTE-09) | — | no client-server, no multiple instantiation (proposal: `../proposals/RTE-09-multiple-instances.md` (not published, available on request)); modes only from the one ECU mode manager |
| RTE | generated `Rte_Read_*` / `Rte_Write_*` with `Std_ReturnType`; `Rte_IRead_*` / `Rte_IWrite_*` / `Rte_IWriteRef_*` with a copy per runnable (RTE-09); `Rte_Mode_*` with `RTE_MODE_*`/`RTE_TRANSITION_*` (RTE-08) | "RTE", "runnable", "port", "task mapping" | generated from an own YAML ECU description, not from a system description or ECU extract; data consistency solved with its own copy-at-task-boundary scheme; mode switches seen per task at its cycle start, not queued |
| Diagnostics | ISO 14229 (UDS) over ISO 15765-2 (ISO-TP), both in the bootloader and in the application | — | no Dcm, no diagnostic extract, no Dem, no DTC memory |
| BSW layering | — | "BSW", "CDD", "ECU state", "lifecycle levels", layer numbering | no AUTOSAR BSW module exists by name; no `CanIf`, `PduR`, `Com`, `Dcm`, `Dem`, `NvM`, `EcuM`, `BswM` |
| MCU drivers | — | — | not MCAL: the drivers are OpenBSW's and neXtBSW's own C++ interfaces, and the union-of-all-parameters driver style is rejected on purpose |
| Operating system | — | tasks, priorities, periods | FreeRTOS, not AUTOSAR OS or OSEK |
| Configuration method | — | "derivative / board / project levels" is our own vocabulary | no AUTOSAR methodology: no ECU configuration ARXML, no BSWMD, no EcuC, no system description |
| Conformance | — | — | none. No conformance test suite has been run, and no certification exists or is sought |

## 2. Area by area, with the evidence

### 2.1 Base types — specification followed, subset

`libs/bsw/autosarBase` is an interface library with three headers and no source files:

- `Platform_Types.h` — `boolean`, `sint8`…`uint64`, `*_least` variants, `float32`/`float64`, `VoidPtr`, plus
  `CPU_TYPE = CPU_TYPE_32`, `CPU_BIT_ORDER = LSB_FIRST`, `CPU_BYTE_ORDER = LOW_BYTE_FIRST`. Names, values and
  meanings are those of `AUTOSAR_SWS_PlatformTypes`. Since ARCH-46 the integer types are typedefs of the
  `<stdint.h>` types (`uint32` is `uint32_t`, so pointers to both are interchangeable in C and C++); `boolean` stays
  `uint8_t` and is not C99 `bool`.
- `Std_Types.h` — `Std_ReturnType`, `E_OK`/`E_NOT_OK`, `STD_HIGH`/`STD_LOW`, `STD_ACTIVE`/`STD_IDLE`,
  `STD_ON`/`STD_OFF`. Its own header comment calls it a "minimal subset", and it is: there is no
  `Std_VersionInfoType`, no transformer error type and no development-error hooks.
- `Compiler.h` — the `FUNC`, `P2VAR`, `P2CONST`, `CONST`, `VAR` family for GCC and Clang. Memory classes are ignored
  by design ("a flat memory model without near/far pointers"), so every macro drops its `memclass` argument.

Used by the generated RTE and the component code (`ecu/rte/CMakeLists.txt` links `autosarBase` into `swcComponents`
and `rte`); nothing else in the repository uses these types. Note one inconsistency to be aware of: the
`Platform_Types.h` comment says "for 32-bit ARM Cortex-M", while the target picture also covers Cortex-R
([`README.md`](README.md) section 1, D11). The definitions themselves are correct for both, but the comment is
narrower than the intent.

### 2.2 E2E — specification followed, with documented deviations

`libs/bsw/e2e` implements profiles **P01**, **P02** and **P11** in C99, with the CRC routines
(`E2E_Crc8` SAE J1850, `E2E_Crc8H2F`) taken from `AUTOSAR_SWS_CRCLibrary` R22-11 including their published check
values. The header states the basis explicitly: `AUTOSAR_PRS_E2EProtocol` and `AUTOSAR_SWS_E2ELibrary` R22-11.
Function names (`E2E_PxxProtect`, `E2E_PxxCheck`, `E2E_PxxMapStatusToSM`), the status codes
(`E2E_P01STATUS_OKSOMELOST`, `E2E_P_WRONGSEQUENCE`, …) and their numeric values are those of the specification;
configuration fields use AUTOSAR bit offsets.

Deliberate deviations, all listed in `libs/bsw/e2e/doc/e2e.md`:

| AUTOSAR | Here | Why |
|---|---|---|
| `Std_ReturnType`, enum types | `uint8_t` typedefs with the same values | no dependency on `Std_Types.h`; layout independent of `-fshort-enums` |
| P01/P02: `State->NewDataAvailable`, no length | explicit `length` parameter; `data == NULL && length == 0` means "no new frame" | same shape as P11, and the length is checked for P01/P02 too |
| P02 configuration field `Offset` | absent, header fixed in bytes 0/1 | the P02 flow charts never evaluate it |
| `Forward` functions | not implemented | not needed for the RTE's CAN frames |
| `E2E_SMCheck` state machine | not implemented | the RTE debounces over time instead (`doc/architecture/signal-qualifier.md`) |

The library is real, not decorative: `tools/rtegen` derives the configuration from DBC signals
(`E2E_PROFILES`, `E2E_SYNC_COUNTER_INIT = 1`), and `ecu/rte/CMakeLists.txt` links `e2e` into the RTE when a project
configures `can.qualifiers.<message>.e2e`. Verification is host-only: 1367 checks in the C host test, a Python
reference model over the specification's own examples, a randomised C-versus-Python comparison, and 33 injected
mutations all detected (`libs/bsw/e2e/doc/e2e.md`). One line in that document is stale — it still says the library is
"not yet used in the RTE", which was true on 2026-09-15 and is no longer true.

### 2.3 Software components and RTE — AUTOSAR shape, own generator

The application model is genuinely AUTOSAR Classic in shape. A component is a Simulink model configured with the
AUTOSAR Blockset; Embedded Coder emits C and ARXML (`swc/<Swc>/generated/{src,arxml}`, checked in). The ARXML sits in
the `http://autosar.org/schema/r4.0` namespace against `AUTOSAR_00051.xsd` and contains
`APPLICATION-SW-COMPONENT-TYPE`, `SENDER-RECEIVER-INTERFACE`, `RUNNABLE-ENTITY`, `TIMING-EVENT`,
`DATA-RECEIVE-POINT-BY-ARGUMENTS` and `DATA-SEND-POINTS`. The generated component code calls
`Rte_Read_<Port>_<Element>(&value)` and `Rte_Write_<Port>_<Element>(value)` returning `Std_ReturnType` — the AUTOSAR
API for explicit sender-receiver access (`swc/DemoSwc/generated/src/DemoSwc.c`).

`tools/rtegen` reads exactly that subset of the ARXML and generates `Rte_Type.h`, `Rte_<Swc>.h`, `Rte.c`,
`RteConfig.h/.cpp`, `RteTasks.h` and the task configuration. What the generated RTE offers is
`Rte_Read`/`Rte_Write`, since RTE-08 `Rte_Mode`, and since RTE-09 `Rte_IRead`/`Rte_IWrite`/`Rte_IWriteRef` — grepping
the templates for the RTE API surface yields nothing else. There is no `Rte_Call`, no `Rte_Send`/`Rte_Receive`, no
`Rte_DRead`, no `Rte_IStatus`/`Rte_IInvalidate`, no `Rte_IrvRead`/`Rte_IrvWrite`, no `Rte_Switch`, no `Rte_Pim`, no
exclusive areas.

Explicitly unsupported (`swc/README.md`): client-server ports and multiple instantiation (a design for the latter:
`../proposals/RTE-09-multiple-instances.md` (not published, available on request)). Arrays, records and
calibration parameters have been supported since RTE-03/RTE-04. Mode management exists only in the form described
below.

#### Implicit access (RTE-09)

| Topic | As in AUTOSAR | Here, and different |
|---|---|---|
| Component side | `VARIABLE-ACCESS` in `DATA-READ-ACCESSS` / `DATA-WRITE-ACCESSS` of the `RUNNABLE-ENTITY` (Embedded Coder: `ImplicitReceive`, `ImplicitSend`, `ImplicitSendByRef`), read from the component's ARXML; no setting in `rte.yaml` | — |
| API | `Rte_IRead_<re>_<p>_<o>()` returning the value (records and arrays: `const` pointer), `Rte_IWrite_<re>_<p>_<o>(data)`, `Rte_IWriteRef_<re>_<p>_<o>()` returning a pointer into the buffer; the component header maps them to `Rte_I*_<c>_<re>_<p>_<o>` | no `Rte_IStatus`, no `Rte_IInvalidate`; a read access point on a provided port or a write access point on a required port is rejected |
| Semantics | values read stay stable during the whole execution of the runnable; values written are made available only after it terminates | the same, realised with **one copy per runnable and data element** (`Rte_Imp_<c>_<re>_<p>_<o>`): a wrapper loads every copy before the runnable and publishes the written ones after it. AUTOSAR allows an RTE to share such buffers between runnables of a task; this RTE does not, so the RAM cost is per runnable (listed by `tools/schedana`) |
| Initial content of a write buffer | — | the current value of the element, so a runnable that does not write, or writes only part of a record through `Rte_IWriteRef`, publishes what it found. Runnables of several tasks writing one element implicitly can therefore overwrite a newer value with an older one; `rtegen` warns |
| Consistency across tasks | RTE decides | the copies build on the RTE-02 scheme: loaded from the task copy or single-task buffer without a lock, or from the published value of a port used in several tasks under **one** lock per side of the runnable; the runnable itself never runs under the lock |

A component without implicit access points gets byte-identical generated code (checked for every project and every
test profile of `tools/rtegen`).

#### Modes (RTE-08)

The ECU states of `libs/bsw/ecuState` are offered as the modes of one mode manager, `ecu.mode`. What follows the
AUTOSAR Classic RTE (SWS RTE, R4.x) and what does not:

| Topic | As in AUTOSAR | Here, and different |
|---|---|---|
| Component side | required port with a `MODE-SWITCH-INTERFACE` and one `MODE-GROUP`; `MODE-DECLARATION-GROUP` with `ALPHABETIC_ORDER` (default) or `EXPLICIT_ORDER`, `VALUE`, `ON-TRANSITION-VALUE`; `MODE-ACCESS-POINT`s; `SWC-MODE-SWITCH-EVENT` with `ON-ENTRY`, `ON-EXIT`, `ON-TRANSITION` — all read from the component's ARXML | — |
| API | `Rte_Mode_<p>_<o>()` returning `Rte_ModeType_<Group>`; `RTE_MODE_<Group>_<Mode>` and `RTE_TRANSITION_<Group>` in `Rte_Type.h` (with `#ifndef` guards, so an Embedded Coder header may define them first) | only the simple form; no enhanced `Rte_Mode` with previous/next mode, no `Rte_Switch`, no `Rte_SwitchAck` |
| Mode manager | — | one fixed manager, `ecu.mode`, fed by the ECU state machine. A component cannot be a mode manager (provided mode ports are rejected), and there is no BswM/EcuM mode arbitration |
| Mode group compatibility | the groups of manager and user must match | the port's group has to name exactly the six ECU states (case ignored); its own values are what `Rte_Mode` returns, the RTE maps the state number to them |
| Execution of a switch | on-exit, then on-transition, then on-entry executables; `RTE_TRANSITION_` during the transition; on-entry of the initial mode at start | the same order and value, but **per task**: each task that runs mode switch runnables or reads a mode port in one task keeps its own view of the mode and processes a change at the start of its next cycle, before its periodic runnables. A mode port read in several tasks reads the mode manager directly (like the RTE-02 hand-over of a data port used in several tasks) |
| Mode switch queue | a queue of configurable length, every switch processed | no queue: the manager publishes only its current state, so a mode shorter than a task's cycle can pass unseen by that task |
| Mode disabling | `DISABLED-MODE-IREFS` on RTE events | rejected; a runnable reads the mode with `Rte_Mode` instead |
| Event to task mapping | ECU configuration (`RteEventToTaskMapping`) | the mode switch runnable is listed in `rte.yaml` `tasks[].runnables`, like a periodic runnable; a runnable with a mode switch event and a timing or init event is rejected |
| Mode switch runnables and the tools | — | they are not rows of `rte::RUNNABLES` but of their own table `rte::MODE_RUNNABLES`: `rte runnables` and `rte json` show one line per event with calls and run time, and `tools/schedana` adds the worst mode switch per task to the response time (RTE-17) |

The sender-receiver target `ecu.state` (the state as a number) is unchanged; an RTE without mode ports is byte-identical
to the one before RTE-08.

Two things are deliberately **not** AUTOSAR:

- **Where the integration comes from.** AUTOSAR would describe the composition, the connections and the task mapping
  in ARXML (system description, ECU extract, ECU configuration). neXtBSW describes them in its own YAML,
  `projects/<p>/rte.yaml`, and connects component ports to *board signals* by name — `dio.in.BUTTON1`,
  `adc.phys.MOTOR_NTC`, `can.qlf.PC_Command`, `ecu.state`. The generator validates direction, data type, existence in
  the board profile and use by the project, with messages carrying the YAML path.
- **How data consistency is achieved.** Instead of IRVs and exclusive areas, `rtegen` decides per buffer:
  single-task buffers are accessed directly; a buffer used by several tasks gets one copy per task, read at the
  task's start and published at its end under a lock; a port whose runnables run in several tasks gets an atomic
  hand-over under the lock (`swc/README.md`, "Datenkonsistenz zwischen Tasks"). Implicit access (RTE-09) adds a copy
  per runnable on top of that scheme where the component asks for it. This is a smaller and testable scheme, and it
  is ours.

Task priorities are also our own: OpenBSW starts each task with its context number as FreeRTOS priority, so the
project's `priority` is a sort key over BSW and application tasks rather than an OS priority
(`swc/README.md`, RTE-02).

### 2.4 Diagnostics — ISO 14229, not Dcm

Two independent UDS servers exist, both following ISO 14229 over ISO 15765-2:

- **Bootloader** (`bootloader/src/core/uds.c`, `isotp.c`): plain C, no AUTOSAR and no OpenBSW. Services 0x10, 0x11,
  0x22, 0x27, 0x2E, 0x31, 0x34, 0x36, 0x37, 0x3E with the standard negative response codes (0x11, 0x12, 0x13, 0x22,
  0x24, 0x31, 0x33, 0x35, 0x36, 0x37, 0x70, 0x72, 0x73, 0x78, 0x7F). Long operations are chunked and answer
  `7F xx 78` while they run. DIDs 0xF180, 0xF184 (fingerprint), 0xF186, 0xF189 and 0xF191 are the identifiers ISO
  14229-1 defines for these meanings; the routine identifiers 0xFF00/0xFF01/0xF1A0 (0xFF02 before bootloader 0.3.4) and DID 0xF1A0 (target
  compatibility) are project conventions.
- **Application** (`ecu/uds`, COM-04): built on OpenBSW's `uds`, `docan` and `transport` libraries. Services
  10 01/02/03, 11 01/03, 22 F180/F181/F186/FD00, 2E FD00 and 3E (`ecu/uds/include/uds/app/DiagStack.h`). Addressing
  uses OpenBSW's `TransportConfiguration::FUNCTIONAL_ALL_ISO14229`; timing, block size and separation time come from
  the project's generated `AppDiagConfig.h`, so the bootloader answers with the same values.

What is **not** there: an AUTOSAR `Dcm` with its ARXML configuration, a `Dem` with event memory and DTCs, snapshot or
extended data records, or an OBD service set. Diagnostics here is a protocol implementation, not the AUTOSAR
diagnostic stack.

### 2.5 BSW modules — vocabulary only

The layer model in [`README.md`](README.md) section 3 uses AUTOSAR words: BSW, complex device driver, services above
drivers, application on top of an RTE. The module names are not AUTOSAR's. A search across `libs/`, `ecu/`,
`platforms/`, `bootloader/` and `tools/` for `CanIf`, `PduR`, `Dem_`, `NvM_`, `EcuM`, `BswM` and `Dcm_` returns
**no hits at all** outside the submodule.

What fills those roles instead:

| AUTOSAR module | Here |
|---|---|
| `Com`, `PduR`, `CanIf`, `CanSM` | `tools/dbcgen` generates pack/unpack from the project's DBC; `libs/bsw/canSystem` owns bus state and the transmit gate; the RTE moves signals |
| `E2E` | `libs/bsw/e2e` — the one place where the specification really is implemented |
| `Dcm` | `ecu/uds` on OpenBSW's `uds` library |
| `Dem` | nothing. There is no fault memory and no DTC handling |
| `NvM`, `Fee`, `Ea` | nothing. OpenBSW's `IEepromDriver` exists but `EepromDriver::init()` is forbidden by project rule, because FlexNVM partitioning is irreversible and the decision is open (NVM-01) |
| `EcuM`, `BswM` | `libs/bsw/ecuState` plus OpenBSW's `lifecycle` manager with nine numbered levels |
| `Os` | FreeRTOS |
| `WdgM`, `Wdg` | OpenBSW's safety libraries plus the MCU watchdog and, where a board has one, the SBC's external watchdog through the `watchdog_trigger` role |

The **module manifest** `module.yaml` is a neXtBSW invention with no AUTOSAR ancestry: it declares the module's kind,
the L1 contracts it requires, the roles it provides, its configuration schema, its lifecycle level and task, and its
CMake target ([`README.md`](README.md) section 5). It exists for 11 modules today, not for all of them.

### 2.6 MCU drivers — explicitly not MCAL

The L1 layer is OpenBSW's BSP interfaces plus two of our own (`libs/bsp/bspSpi`, `libs/bsp/bspAdcAccess`). No MCAL API
appears anywhere: a search for `Dio_`, `Adc_`, `Spi_`, `Pwm_`, `Mcu_Init`, `Port_Init` and `Gpt_` returns nothing.

This is a decision, not an omission. [`treiber-granularitaet.md`](treiber-granularitaet.md) section 1 names the
failure mode it is avoiding: a generic driver whose parameter set is the union of every supported part's capabilities,
so that only some parameters apply per instance and no tool can check which combination makes sense — "das ist der
MCAL-Fehlschlag". The rule instead is that the boundary runs along the **protocol**: same behaviour and only numbers
differ → one generic behaviour module with the part in its `parts:` list; own register model or own sequence →
a hardware-near driver named after the part family; same behaviour over a different access path → a thin adapter.

### 2.7 Methodology and tooling — none of AUTOSAR's

There is no AUTOSAR methodology tool chain here. No ECU configuration ARXML, no BSWMD, no EcuC parameter definitions,
no system description, no ECU extract, no configuration editor that reads or writes any of them. The configuration is
three levels of YAML validated by pydantic models in `tools/nxconfig`, and the GUI (neXtBSW Studio) edits
those.

The only ARXML in the repository is generator **output** from Embedded Coder, consumed by `tools/rtegen` as an input
description of the component. `rtegen` parses a deliberately small set of elements and reports anything it cannot
resolve; it is not an ARXML tool in the general sense.

## 3. What is missing, stated plainly

- **No AUTOSAR methodology tooling.** See 2.7.
- **No complete BSW module list.** See 2.5. Memory (`NvM`) and diagnostic event handling (`Dem`) are entirely absent,
  not partially implemented.
- **No conformance evidence.** No AUTOSAR conformance test suite has been run against any part of this repository, no
  certification exists, and none is planned. Where a specification is implemented — the E2E profiles — the evidence
  is host tests against the specification's own example values and a mutation run, which is a statement about those
  tests and nothing more.
- **No AUTOSAR Adaptive anything.** The vocabulary used here is Classic Platform throughout.
- **Verification level.** Every statement on this page is a statement about source code read on 2026-09-17
  (verification: **mock/analysis**). None of it was re-measured on hardware for this document.

## 4. If you come from AUTOSAR, expect this

You will recognise: the layering, the idea of a component with ports and runnables, `Rte_Read`/`Rte_Write`,
`Rte_IRead`/`Rte_IWrite`/`Rte_IWriteRef`,
`Rte_Mode` with mode switch events on the ECU states, the base types, the E2E profiles, UDS, and the habit of
describing an ECU rather than programming it.

You will not find: ARXML as the configuration language, a BSW module list, a Dcm/Dem pair, an NvM stack, MCAL APIs,
AUTOSAR OS, or a conformance statement. Where AUTOSAR would have a standard module, this repository usually has a
smaller module of its own with host tests, and the reason is written down in
[`README.md`](README.md) section 10 or in [`treiber-granularitaet.md`](treiber-granularitaet.md).
