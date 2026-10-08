# neXtBSW — interplay of functional safety and cybersecurity (SYS-92, N1)

Baseline: v0.2.0

Status: 2026-09-30, **issue 1 (SYS-92, proposal N1 of [`safety-plan.md`](safety-plan.md))**, written against `dev`
at `65f3dc9`.
Readers: the safety manager, the author of any change to the bootloader, UDS, XCP or the console, the integrator
who runs the cybersecurity engineering of his item, and the author of the safety manual (DOC-19).
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**What this document is.** The record of the interplay between functional safety and cybersecurity that the gap
analysis found missing (`iso26262-gap.md` (not published, available on request) row 2-02, clause 2-5.4.2.3: the safety organisation
keeps communication channels to the cybersecurity side where both touch). For an element without a
cybersecurity process of its own the channel is this document: it lists **every interface through which code,
configuration, calibration data or safety-relevant state of a running ECU can be changed in the field**, says what a
change through it does to the safety argument, which protection exists, and what the integrator must add as an
**assumption of use** for the safety manual.

**What it is not.** Not a threat analysis and risk assessment and not a cybersecurity concept: neXtBSW has no
cybersecurity management and makes **no cybersecurity claim**. Attack feasibility, risk values and cybersecurity
goals belong to the integrator's item-level engineering; this document hands him the interfaces and their safety
effect as input. Cryptographic hardware and secure boot are the subject of
[`csec-concept.md`](../architecture/csec-concept.md) (SYS-56) and BL-12; they are cited, not repeated.

**Licence rule.** ISO 26262 is cited by part and clause only. No other standard is quoted.

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Field interfaces (section 2) | <!-- sec-counts -->**10**: application download and erase over CAN (IF-01), bootloader self-update (IF-02), entry into the bootloader (IF-03), UDS services of the application (IF-04), XCP calibration (IF-05), calibration segment in flash (IF-06), UART console (IF-07), debug port (IF-08), non-volatile data (IF-09), received CAN signals (IF-10) |
| What an unauthorised change can do to the safety argument | replace or corrupt the safety mechanisms themselves (IF-01, IF-02, IF-08), change a safety parameter (IF-05, IF-06), stop the safety function or force the safe state (IF-03, IF-04, IF-07), inject faults on purpose (IF-07), feed wrong values to a safety function (IF-10) |
| Protection that exists | security access with a per-device HMAC key in the bootloader (BL-03) and in the application (COM-30); CRC and compatibility check of every image (SM-51); plausibility of safety-relevant parameters before every XCP write and at start (SYS-90); CALV record with CRC (SYS-89); production profile without XCP (SYS-89); fault-injection and power-stage commands behind build options off by default |
| What does not exist | no signature of images (BL-12 open), flash security (FSEC) never set — the debug port reads and writes everything, including the device keys; no authentication of the console and of XCP; no precondition on 10 02 / 11 in the application; E2E is not a security mechanism; no SecOC |
| Effect on the safety claim | none of these gaps invalidates a stage-1 mechanism against **random hardware and systematic faults**, which is what the claim covers; each of them lets an attacker **bypass** a mechanism. The claim therefore stands only together with the integrator's cybersecurity measures, stated as assumptions of use AoU-SEC-1 … AoU-SEC-8 (section 4) |
| Rules for the interplay (section 3) | 6 rules: security measures never weaken a safety reaction; safety test paths are security hazards and leave the product; a security finding that reaches a safety function is a safety anomaly; and three more |
| New tasks proposed (section 6) | S1 flash security in the product, S2 precondition hook for session change and reset, S3 XCP resource protection or product build without XCP, S4 security section in the safety manual; existing tasks that close gaps: SYS-74, BL-12, SYS-56 |
| Verification | **mock/host**: `doc/safety/test/test_security_interplay.py` checks the interface register against the repository (every named path exists, every task exists, every interface has protection, gap and assumption, every AoU and proposal is used) with counter-checks. The facts were read from the code at `65f3dc9`; nothing was run on the board for this document |

---

## 1. Method

**Interface.** Any path by which, on an ECU in the field and without rebuilding the software, one of the
following changes: the program code (application, bootloader), the configuration or calibration data the safety
mechanisms use, the non-volatile state they read, or the run-time state of a safety function (running, safe state,
reset). An interface that only reads (UDS read services, XCP upload) is not listed; its confidentiality aspect is the
integrator's.

**Effect on the safety argument.** Which assumed safety goal (ASG), software safety requirement (SSR) or mechanism
(SM) of [`concept.md`](concept.md) an unauthorised or erroneous use of the interface can defeat, and whether a
stage-1 mechanism would still detect it. "Detected" means detected as a fault, not recognised as an attack.

**Sources.** The code at `65f3dc9` (paths in the table), the project files of the demo
(`projects/evalboard_s32k144_demo/{project,diag}.yaml`), [`csec-concept.md`](../architecture/csec-concept.md),
[`tool-classification.md`](tool-classification.md) (AoU-26 … AoU-29) and [`concept.md`](concept.md) (AoU-10,
AoU-16).

---

## 2. Interface register

<!-- sec-interfaces:start -->
| ID | Interface | Where | Can change | Effect on the safety argument | Existing protection | Gap | Assumption | Task |
|---|---|---|---|---|---|---|---|---|
| IF-01 | Application download and erase over CAN (bootloader, UDS 10 02, 27, 2E fingerprint, 31 erase/check, 34/36/37) | `bootloader/src/core/uds.c`, `bootloader/src/core/seedkey.c`, `bootloader/src/core/app_image.c`, `tools/canflash` | the whole application image, including every safety mechanism of the application and its generated configuration | an image without, or with changed, safety mechanisms runs as if it were the released one; SM-51 checks integrity and compatibility, **not origin** | programming session + security access, key = HMAC-SHA256 over a per-device key (BL-03), 3 tries then 10 s lockout; fingerprint before erase; erase limited to the application area; CRC-32 over the image, compatibility block (target, layout), entry vectors (SM-51, SSR-051) | no signature (BL-12 open); the device key is readable over the debug port while FSEC is unsecured (IF-08); lockout counter in no-init RAM, cleared by a power cycle | AoU-SEC-1, AoU-SEC-2 | BL-12, SYS-56 |
| IF-02 | Bootloader self-update (`canflash blupdate`, updater image) | `bootloader/updater`, `tools/canflash` | the bootloader, and with it SM-51 | a changed bootloader can start any image and removes the image check | the updater is loaded like an application (same security access); header CRC, image CRC and version of the blob; accepts only the unsecured reference Flash Configuration Field, so it cannot brick or lock the chip | no signature; an attacker who can do IF-01 can do IF-02 | AoU-SEC-1, AoU-SEC-2 | BL-12 |
| IF-03 | Entry into the bootloader from the running application (UDS 10 02, console `lc bootloader`, start-up window after power-on) | `ecu/uds/include/uds/app/AppSessionHandling.h`, `bootloader/include/bl_interface.h` | the run-time state: the application and its safety functions stop, the ECU resets into the bootloader | the safety function of the item is off while the bootloader runs; the power outputs are not driven by the bootloader (it has no driver for them) and stay in their pin reset state | 10 02 needs the extended session; programming itself needs security access (IF-01) | `isModeChangePossible()` is always true: no vehicle-state precondition in neXtBSW; the console path has no authentication | AoU-SEC-3 | S2 |
| IF-04 | UDS services of the application (11 ECU reset, 28/85 communication and DTC setting control, 14 clear DTC, 2E write DID, 31 routines, 27 security access) | `ecu/uds`, `projects/evalboard_s32k144_demo/diag.yaml`, `tools/diaggen` | reset, silencing of the application's transmit path and of the DTC setting, deletion of stored DTCs, writable DIDs and routines generated from `diag.yaml` | 11 resets the ECU (availability); 28/85 suppress the transmission and storage of fault information, not the detection or reaction of a mechanism; a DID or routine that writes a safety-relevant value or drives an output bypasses the safety function (AoU-28) | writable DIDs and routines per `diag.yaml` with session and security level; key = HMAC-SHA256 over a per-device key per level (COM-30); in the demo only a RAM test value is writable and one LED routine exists | 11 without precondition; 14 in every session; security attempt counter in RAM (reset clears it); what a DID or routine writes is the integrator's `diag.yaml` | AoU-SEC-3, AoU-SEC-4 | S2 |
| IF-05 | XCP calibration (DOWNLOAD, SET_CAL_PAGE) | `ecu/xcp/include/xcp/XcpSlave.h`, `ecu/xcp`, `ecu/calibration` | the RAM values of the RTE parameters (`rte::PARAMETERS`) | a safety-relevant parameter can be set to a value the design never intended | writes limited to parameters, signals and flash read-only; for parameters with `safety` other than `qm` the generated plausibility check refuses an out-of-range or non-monotonic write (`ERR_OUT_OF_RANGE`, SYS-90); `memory.calibration.profile: production` excludes XCP (SYS-89) | **no seed & key** (CONNECT reports nothing locked); XCP is on by default (`NX_XCP`) and a project without a calibration segment calibrates plain RAM; QM parameters are not plausibility-checked | AoU-SEC-5 | S3 |
| IF-06 | Calibration segment in flash (segment HEX from `tools/calseg`, flashed through the bootloader) | `tools/calseg`, `ecu/calibration` | the stored values of the calibration segment | as IF-05, but persistent | CALV record: magic, layout ID, data set ID, CRC-32; plausibility at start; `on_invalid: refuse` for safety-relevant parameters (no runnables, DEM entry); the segment is a range of the ROM check (SM-14) | the segment is loaded through IF-01 and inherits its gaps: a well-formed segment with a matching CRC is accepted | AoU-SEC-1, AoU-SEC-5 | BL-12 |
| IF-07 | UART console | `ecu/consoleCommands/src/safety/console/SafetyCommand.cpp`, `platforms/s32k1xx/port/main/CMakeLists.txt` | safe state on and off (`safety dst/est`), MPU and watchdog tests, resets and provoked faults (`lc`), SBC register writes, KL30B switch, gate-driver registers, DEM and NvM, ECU state | enters and leaves the safe state on command, provokes faults, and writes device registers behind the back of their drivers — the mechanisms detect most of it as faults (SM-22 register check, S1), but `dst/est` is a legitimate entry and exit of S1 | the dangerous commands are behind build options, off by default: `gd enable`, `hardfault`, `inject`, `flexnvm partition`; the SBC driver refuses writes to the MTPNV | no authentication; the other commands are always built (SYS-74 open) | AoU-SEC-6 | SYS-74 |
| IF-08 | Debug port (SWD) | `bootloader/platforms/s32k1xx/startup.S`, `bootloader/updater` | everything: flash, RAM, registers, device keys; the core can be halted | every mechanism can be bypassed; a halt stops the safety task and the watchdog (WDOG `CS[DBG] = 0`) | the halt deadman walks the safe outputs table after its time-out (SM-06, AoU-16); nothing else | Flash Configuration Field unsecured (FSEC 0xFE), backdoor key disabled, nothing sets FSEC; the updater enforces the unsecured field | AoU-SEC-7 | S1, SYS-56 |
| IF-09 | Non-volatile data (FlexNVM EEPROM emulation: DEM blocks, RTE NvM blocks) | `ecu/application/src/systems/RteSystem.cpp`, `platforms/s32k1xx/port/main/CMakeLists.txt` | stored DTCs and NvM blocks of the application | stage-1 mechanisms read no NvM data (AoU-19); a safety function of the application that restores a value from NvM does | NvM writes only from the application and the console (`nvm write`, `dem clear`); partitioning behind a build option; SBC MTPNV is never written | no integrity protection of NvM blocks against deliberate change | AoU-SEC-8 | SYS-74 |
| IF-10 | Received CAN signals of the RTE | `libs/bsw/e2e`, `tools/rtegen` | the values safety functions of the application compute with | wrong commands or measurements reach the application | E2E profiles P01/P02/P11 per configured signal (SM-31): counter, data ID and CRC against random and systematic faults of the channel | E2E is **not** a security mechanism: the profile is public, anybody on the bus can form a valid frame; no SecOC | AoU-SEC-8 | SYS-56 |
<!-- sec-interfaces:end -->

Remarks on the register:

- **IF-01 and IF-02 are the ones that matter most.** They replace the element itself. The security access with a
  per-device key is a real barrier against a casual tester, but as long as the debug port is open (IF-08) the key can
  be read from the key sector, and as long as the image is not signed (BL-12), anybody who has the key of one device
  can load any image with a matching CRC into it. For the safety argument this means: **the evidence of the safety
  case is about the released image; which image runs in the field is protected only by the integrator's
  measures.**
- **IF-03 is safety-relevant even when it is authorised.** Programming a vehicle while the item is active switches
  its safety function off. neXtBSW has no information about the vehicle state; the application hook
  `isModeChangePossible()` is the place where the integrator's precondition belongs (S2).
- **IF-05 is the interface where safety and security already meet in code.** The plausibility check of SYS-90 is a
  safety mechanism (it limits a legitimate but wrong calibration); it happens to limit an attacker too, but only to
  the range of the design. The production profile of SYS-89 is the actual closure.
- **IF-07:** the fault-injection commands are needed for the verification of the safety mechanisms (board tests of
  `fmea.md` section 8). They are a security hazard in a product; SYS-74 removes them from the product build.
- **IF-10:** the safety case must not count E2E as protection against manipulation. The concept states E2E against
  faults of the communication channel only (SM-31).

---

## 2.1 Security mechanisms seen from safety

A security mechanism that reacts at the wrong moment can itself create a hazard or remove a safety reaction. The
existing mechanisms were checked for that:

| Mechanism | Could it weaken a safety reaction? | Result |
|---|---|---|
| lockout after 3 failed security attempts (bootloader 10 s, application 10 s) | it blocks programming and the protected diagnostic jobs only; no safety reaction depends on them | no effect |
| refusal of an XCP write (`ERR_OUT_OF_RANGE`) | the previous value stays; the design range is kept | no effect (it is a safety mechanism) |
| `on_invalid: refuse` of the calibration segment | the application does not start its runnables; the safety task and the supervisor run, outputs stay off | safe by design (SYS-89) |
| updater refuses a secured Flash Configuration Field | prevents an accidental lock, but also prevents securing the chip by update (S1) | no safety effect; a security gap |
| session time-out of the bootloader (S3 server 5 s) | the bootloader stays in programming; the application does not run | availability only |

---

## 3. Rules for the interplay

These rules apply to every change of neXtBSW that touches one of the interfaces of section 2, and to the review of
such a change. They are the communication channel of 2-5.4.2.3 for an element without a security organisation.

<!-- sec-rules:start -->
| ID | Rule |
|---|---|
| R-1 | A security measure never delays or removes a safety reaction: detection, the safe outputs table walk, the safe state and the reset of the safe supervisor keep precedence over every lockout, authentication or session state. |
| R-2 | A path that exists to test a safety mechanism (fault injection, safe state on command, register writes behind a driver) is a security hazard: it stays behind a build option that is off in the product build, or it is removed from the product build (SYS-74). |
| R-3 | A security finding that can reach a safety function or change what runs on the ECU is a **safety anomaly**: it goes into the anomaly register (SYS-77) with its effect on the interfaces of section 2, and the safety manager decides. |
| R-4 | A change that adds, widens or removes an interface of section 2 updates this register in the same merge; the coordinator treats it as a change with safety impact (8-8.4.3). |
| R-5 | No safety requirement relies on a security mechanism: the stage-1 mechanisms are argued against faults; where a safety argument needs protection against deliberate change, it is stated as an assumption of use (section 4), not claimed. |
| R-6 | Protection that is configurable (XCP on/off, production profile, security levels in `diag.yaml`, flash security) is a configuration value in exactly one configuration level and is checked by `tools/nxconfig` with the YAML path where a combination is unsafe (as for `profile: production` with `xcp`). |
<!-- sec-rules:end -->

---

## 4. Assumptions of use proposed for the safety manual

Numbered `AoU-SEC-n` here so that they do not collide with numbers other tasks propose in parallel; DOC-19 gives
them their final numbers. They refine AoU-10, AoU-16 and AoU-28, which remain valid.

<!-- sec-aou:start -->
| ID | Assumption of use | Interfaces |
|---|---|---|
| AoU-SEC-1 | The integrator treats **every write of program or calibration data** (application, bootloader, calibration segment) as a security-relevant function of his item and protects it with his own measures (secured workshop access, signed images once BL-12 is available, key management of the per-device keys). neXtBSW verifies integrity and compatibility of an image (SM-51), **not its origin**. | IF-01, IF-02, IF-06 |
| AoU-SEC-2 | The **master key** from which the per-device keys are derived, and the provisioning step that writes them (`tools/canflash/provision.py`), are kept under the integrator's key management; a device key must be assumed known once the debug port of that device was open. | IF-01, IF-02 |
| AoU-SEC-3 | Entry into the programming session (10 02, `lc bootloader`) and the ECU reset (11) **switch the safety function of the item off** for their duration. The integrator implements the vehicle-state precondition (e.g. vehicle stationary, power stage off) in the application hook for session changes, and confirms that the pin reset state of every power output is safe while the bootloader runs. | IF-03, IF-04 |
| AoU-SEC-4 | Every **DID and routine** of the integrator's `diag.yaml` that writes a safety-relevant value or drives an output is protected by a security level and interlocked by the application (refines AoU-28). The integrator decides whether clearing DTCs (14) and suppressing their setting (85) needs a precondition in his item. | IF-04 |
| AoU-SEC-5 | In the product, **XCP is off** (`memory.calibration.profile: production` or no `xcp:` section / `NX_XCP=OFF`), or the integrator adds resource protection for calibration; the plausibility check of SYS-90 limits values to the design range but does not authenticate (refines AoU-28). | IF-05, IF-06 |
| AoU-SEC-6 | The product image is built **without the test and fault-injection console commands**: `-DNX_PRODUCT_BUILD=ON` (SYS-74), which also refuses the build options `NX_GD_ENABLE_COMMAND`, `NX_HARDFAULT_TEST_COMMAND`, `NX_SAFETY_FAULT_INJECTION_COMMAND`, `NX_FLEXNVM_PARTITION_COMMAND`, and the UART is not accessible in the vehicle (the service commands stay in the product console). | IF-07 |
| AoU-SEC-7 | The **debug port** is closed in the product (flash security set, or the port physically inaccessible) — refines AoU-16. While it is open, none of the protections of IF-01 … IF-06 holds against a person with access to it. | IF-08 |
| AoU-SEC-8 | Data a safety function of the application reads from the **bus or from NvM** is protected against deliberate manipulation by the integrator where his threat analysis requires it (e.g. SecOC); E2E protects against faults of the channel only, and NvM blocks have no protection against deliberate change. | IF-09, IF-10 |
<!-- sec-aou:end -->

**Reference from the safety manual.** The safety manual (`doc/safety/safety-manual.md`, DOC-19) does not exist yet.
Proposal S4: it gets a section "Cybersecurity interface" that states that neXtBSW makes no cybersecurity claim,
references this document and carries AoU-SEC-1 … AoU-SEC-8 with their final numbers. The consistency test checks the
reference as soon as the safety manual exists.

---

## 5. Where security measures are planned

| Task | What it changes for the register | State |
|---|---|---|
| BL-12 | signed images (Ed25519): IF-01, IF-02, IF-06 check origin, not only integrity | open |
| SYS-56 | CSEc concept: secure boot of the bootloader, keys not readable from flash, basis for SecOC (IF-08, IF-10) | review (concept only) |
| SYS-74 | product build without test and fault-injection commands (IF-07, part of IF-09) | open |
| SYS-89 / SYS-90 | production profile without XCP, plausibility before every XCP write (IF-05, IF-06) | review |

---

## 6. Tasks proposed by this document

<!-- sec-proposals:start -->
| ID | Title | Done when | Prio |
|---|---|---|---|
| S1 | Flash security in the product | a configuration value (board or project level, per architecture section 4) selects secured or unsecured Flash Configuration Field for bootloader and updater; `nxconfig` refuses `secured` without a documented rescue path; the updater accepts the configured field; the safety manual states the choice (AoU-SEC-7) | P2 |
| S2 | Precondition hook for session change and ECU reset | the application's `isModeChangePossible()` delegates to a hook the integrator provides (default: always allowed, as today), documented in the UDS module; host test with a hook that refuses 10 02 and 11 (AoU-SEC-3) | P3 |
| S3 | XCP resource protection or product build without XCP | either XCP seed & key on the per-device key (as COM-30), or `nxconfig` warns when a project has `xcp:` and a parameter with `safety` other than `qm` but no production profile; decision by the user on the decision board (AoU-SEC-5) | P3 |
| S4 | Security section in the safety manual | `doc/safety/safety-manual.md` (DOC-19) has a section on the cybersecurity interface that references `security-interplay.md` and carries AoU-SEC-1 … AoU-SEC-8 with final numbers; `test_security_interplay.py` checks the reference | P2 |
<!-- sec-proposals:end -->

---

## 7. Review questions of the architecture (section 11)

This task changes no code, generator or configuration. **Board without an SBC, other crystal, other derivative,
S32K3/STM32/Cortex-R:** the interfaces are family-neutral (bootloader, UDS, XCP, console); IF-08 names the S32K1
Flash Configuration Field — another family has its own debug-lock mechanism, and S1 makes the choice a
configuration value instead of a constant. **Several CAN buses:** the bootloader listens on one configurable bus;
XCP and UDS on the configured buses; every bus that carries IF-01, IF-04 or IF-05 is in scope. **Several tasks:** not
affected. **New values:** none; S1 and S3 would add values, each in one level. **Host test:**
`test_security_interplay.py`. **Qt, YAML paths, driver granularity, platform rule:** not applicable.

## 8. Sources

- Code at `65f3dc9`: `bootloader/src/core/{uds.c,seedkey.c,app_image.c}`, `bootloader/include/{bl_config.h,bl_interface.h}`,
  `bootloader/platforms/s32k1xx/startup.S`, `bootloader/updater`, `ecu/uds`, `ecu/xcp`, `ecu/calibration`,
  `ecu/consoleCommands`, `platforms/s32k1xx/port/main/CMakeLists.txt`, `tools/canflash`, `tools/calseg`,
  `tools/nxconfig/calibration.py`.
- [`csec-concept.md`](../architecture/csec-concept.md) (SYS-56), [`tool-classification.md`](tool-classification.md)
  (AoU-26 … AoU-29), [`concept.md`](concept.md) (AoU-10, AoU-16, AoU-19, SM-31, SM-51), `doc/todo.yaml` (BL-03,
  BL-12, COM-30, SYS-56, SYS-74, SYS-89, SYS-90).
- ISO 26262-2:2018 clause 5.4.2.3, cited by clause only through `iso26262-gap.md` (not published, available on request) row 2-02.
