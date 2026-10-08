# neXtBSW — software architectural design specification of the safety part (SYS-65)

| | |
|---|---|
| Issue | 1, 2026-09-28 |
| Task | SYS-65 — gap analysis `iso26262-gap.md` (not published, available on request) section 6 no. 7; rows 6-07 … 6-19 |
| Status | draft for review; written by an agent on the user's behalf (author side, see section 9) |
| Built against | branch `dev` at `20b2e04`, demo project `projects/evalboard_s32k144_demo`, build preset `evalboard-s32k144-demo` (RelWithDebInfo), xPack GCC 11.3.1 |
| Verification | mock/host: stack tool with host tests and against the target build of the baseline; **nothing on the board** |

Baseline: v0.2.0

## 0. What this document is

The software architecture of neXtBSW as a whole is described in [`doc/architecture/README.md`](../architecture/README.md)
(layers, roles, module manifests, configuration levels) and in the module manifests. The safety documents describe
the mechanisms ([`concept.md`](concept.md) section 6), their analysis ([`fmea.md`](fmea.md), [`dfa.md`](dfa.md)),
the requirements ([`ssr.sdoc`](../requirements/safety/ssr.sdoc)) and the hardware-software interface
([`hsi-s32k1xx.md`](hsi-s32k1xx.md)). What the gap analysis found missing (rows 6-10, 6-11, 6-18, 6-19) is **one
view of the safety part** that says which units implement which mechanism, in which task or interrupt, with which
data, which requirement is allocated to which component at which ASIL, how much of the resources it needs, and how
the design was inspected. This document is that view. It adds no mechanism and changes no requirement.

References to ISO 26262:2018 are given as `<part>-<clause>` (for example 6-7.4.13), in the project's own words; the
standard is not quoted.

Scope: stage 1 of the concept (base-software mechanisms, [`concept.md`](concept.md) section 1.4) on the only family
with a safety port, **s32k1xx**. Numbers are those of the demo image (S32K144, Cortex-M4F). What changes for other
families is named where it matters (section 10).

---

## 1. Components of the safety part

A *component* here is a group of units with one responsibility and one place in the repository; a *unit* is a class
or a translation unit (the level at which the host tests and the coverage measure). IDs are used by the allocation in
section 4.

| ID | Component | Units (main) | Place | Layer (architecture section 2) |
|---|---|---|---|---|
| C-SUP | Safe supervisor | `SafeSupervisor` (monitors, `handle()`, `holdSafeState()`, deferred event box), `ResetLoopLimit`, `SafeConsole` | `ecu/safety/safeSupervisor` | family-neutral safety |
| C-WDG | Safe watchdog | `SafeWatchdog`, `CyclePeriodCheck` | `ecu/safety/safeWatchdog` | family-neutral safety |
| C-LCY | Safety lifecycle | `SafetyManager` (safety cycle), `IsrHooks` (MPU gate for safe ISRs), `IsrWrapper` (`SAFE_ISR`) | `platforms/s32k1xx/port/safety/safeLifecycle` | family safety port |
| C-MEM | Safe memory | `MemoryProtection` (MPU, gate), `SafeMemory`, `RomCheck`, `IntegrityEccHandler`, `RomCheckFlashWrites` | `platforms/s32k1xx/port/safety/safeMemory` | family safety port |
| C-SYS | Safe system | `SafeSystem` (ADC reference, internal supplies), `AdcSafetyConsole` | `platforms/s32k1xx/port/safety/safeSystem`, `adcSafetyConsole` | family safety port |
| C-SIO | Safe I/O | `SafeIo` (pin lock and register check), `SafeState` (enter/leave S1) | `platforms/s32k1xx/port/safety/safeIo` | family safety port |
| C-SPC | Supervision-peripheral checks | `LpitDeadmanCheck`, `WatchdogControlCheck`, `LptmrCycleTimer` | `platforms/s32k1xx/port/safety/safeSupervisionCheck` | family safety port |
| C-HFH | Fault handlers and safe outputs table | `hardFaultHandler.S` (armv6m, armv7m), `HardFaultDump`, `safeOutputs.h` (table walk) | `platforms/s32k1xx/hardFaultHandler` | family port |
| C-INT | Integration of the safety part | `main.cpp` (start-up order, watchdog self-test, reset-loop start), `Integration.cpp` (power-stage adapters), `DebugHaltGuardSystem`, `LpitHaltDeadman`, `RomCheckSystem`, `SafetySystem`, `FlashWriteBracket` | `platforms/s32k1xx/port/main`, `ecu/application/src/systems` | integration |
| C-RTE | RTE run-time supervision and signal qualifier | `RteSystem` (deadlines, SYS-05), generated `RteConfig.cpp` (qualifier) | `ecu/application/src/systems`, `tools/rtegen/templates` | RTE |
| C-E2E | End-to-end protection | `E2E_P01`, `E2E_P02`, `E2E_P11` | `libs/bsw/e2e` | device-independent BSW |
| C-CAN | CAN transmit gate and bus state | `CanBusState`, `CanPhyGate` | `libs/bsw/canSystem` | device-independent BSW |
| C-ECU | ECU state and start reason | `EcuStateController`, `EcuStateMachine`, `McuResetReason` | `libs/bsw/ecuState`, `platforms/s32k1xx/port/mcuResetReason` | BSW / family port |
| C-BLD | Bootloader image check | `app_image.c` (validity record, CRC, compatibility) | `bootloader/src/core` | bootloader |
| C-GEN | Generators and image checks (off-target) | `tools/bswgen` (safe outputs table, lock list), `tools/nxconfig/checks.py` (timing relations), `tools/elfcheck`, `tools/dbcgen` tests | `tools/` | tools (classification: [`tool-classification.md`](tool-classification.md)) |

Components in the image **without** an allocated safety requirement (QM by intent): console and its commands, the
logger, UDS and DoCAN, XCP, the CAN stack above the gate, NvM, DEM, the device drivers (stage 2), the OpenBSW
lifecycle and `async` framework, FreeRTOS, newlib, ETL. Until the coexistence decision (gap analysis Q4, rows 6-13
and 6-14, task SYS-21) they count as **ASIL B by default** — they run in the same address space and on the same
stacks as the safety part (section 3.4 shows that the console and the ETL assert handler are on the deepest stack
paths of the safety task).

### 1.1 Interfaces inside the safety part

| Interface | Provider → user | Form | Rule |
|---|---|---|---|
| Monitor API `trigger(context)`, `check(bool)`, `hit(checkpoint)` | C-SUP → every mechanism | member monitors of `SafeSupervisor::getInstance()` | detection only raises an event; the reaction is decided in `SafeSupervisor::handle()` alone (SSR-001, concept section 5) |
| Deferred event box | C-SUP ← C-RTE (tasks without write access) | plain struct in general `.bss`, delivered by `deliverDeferredEvents()` in the safety cycle | the reporting task never opens the MPU gate (SYS-37) |
| Safe state `enterSafeState()` / `leaveSafeState()` | C-SIO → C-SUP | functions | S1 is one-way upwards: leaving releases only the indicator (SSR-004) |
| Safe outputs table `nxSafeOutputs[]` | C-GEN (generated) → C-HFH, C-SIO, deadman ISR | const array of address/value/clock-gate words in flash | walked without stack, RAM or driver objects; the same table for every switch-off path (SSR-005) |
| MPU gate `fusaGateGetLockAndUnlock()`, `ProtectedRamScopedUnlock` | C-MEM → C-LCY | functions + RAII scope | the protected RAM is writable only inside the safety cycle and the safe ISR hooks (SSR-020, SSR-021) |
| Safe ISR hook `SafePreIsrHook`/`SafePostIsrHook` | C-LCY → safe handlers (`SAFE_ISR`) | wrapper per vector | opens and closes the gate around a safety interrupt |
| Watchdog BSP `safety::bsp::Watchdog` | family BSP → C-WDG | static class | serviced from the safety task only (SSR-010) |
| Supervision-peripheral expectations | C-INT (`LpitHaltDeadman::EXPECTED_CONFIGURATION`) → C-SPC | constexpr struct | one definition for init and check (SYS-48) |
| Generated configuration | C-GEN → all | headers from `project.yaml` / `mcudb` (`NX_MODULE_SAFELIFECYCLE_*`, `safeIoConfiguration.h`, `safeOutputsConfiguration.h`, `interruptPriorities.h`) | values in exactly one configuration level, relations checked by `nxconfig` and by `static_assert` (SSR-011) |

### 1.2 Global data of the safety part

| Data | Where (demo image) | Written by | Protection |
|---|---|---|---|
| state of the safety components (every `.bss` of a `libsafe*` archive: supervisor monitors, watchdog state, period check, ROM check progress, …) | `.mpu_bss`, 576 B at 0x1FFF9000 | safety cycle, safe ISR hooks | MPU region, gate closed outside the safety context (SM-10 … SM-13) |
| safety task stack `safetyStack` | 2048 B, MPU region of its own | safety task | MPU (opened for the safety ISR hooks, `fusaTaskStackOpenIsr`) |
| deferred event box of the RTE deadline monitor | general `.bss` | RTE tasks | none by design; content checked on delivery |
| safe outputs table `nxSafeOutputs` | flash (const) | generator | ROM check (SM-14), linked-image check (SSR-080) |
| hard-fault dump | fixed RAM area of the dump layout (`hardFaultDumpLayout.h`) | `customHardFaultHandler` | written only on the way to a reset |
| reset-loop record `resetLoopRecord` | `.noinit` (0x20006D44, 16 B) | `startResetLoopLimit` in `main()`, supervisor before a safety reset | survives a reset; plausibility record (SSR-065) |

---

## 2. Dynamic view

### 2.1 Execution contexts

Priorities are the ordering keys of `ecu/asyncCoreConfiguration/tasks.yaml`; FreeRTOS priorities follow from their
order. Stacks from `ecu/application/src/app/app.cpp` and `ecu/configuration/include/app/appConfig.h`.

| Context | Priority / level | Stack | Safety content |
|---|---|---|---|
| task `safety` | 60, highest (`above_application: true`) | 2048 B (`safetyStack`, MPU) | safety cycle every `task_period_ms` = 10 ms (project value), watchdog service every 7th cycle, delivery of deferred events; lifecycle transitions of the component "safety"; console command `safety` |
| task `sysadmin` | 50 | 2048 B | lifecycle manager (run levels) |
| task `can` | 40 | 2048 B | CAN transceivers, transmit gate (SM-32), UDS, XCP |
| application task `swc` (RTE) | 30 | 2048 B | runnables of the SWCs; deadline supervision raises into the deferred box (SM-05) |
| task `bsp` | 20 | 2048 B | BSP, SBC, ECU state (SM-50, SM-53), halt deadman retrigger (SM-06) |
| task `background` | 10 | 2048 B | run-time statistics |
| idle | 0 | 2048 B | console input, logger output, **ROM check pass** (`RomCheck::idle`, lowest priority, preempted by every supervised task: SSR-025) |
| FreeRTOS timer task | highest + 1 | 1024 B | — |
| HardFault / BusFault / UsageFault | fixed above all levels | MSP 4096 B (SYS-97; 1024 B before), 1 KB no-write guard below | `customHardFaultHandler`: safe outputs table first (SM-17), dump, reset; SYS-97: an MSP below the stack limit (overflow into the guard) is moved back to the top before any C code, own line |
| ERM double-bit, FTFC fault, WDOG pre-reset | NVIC level 0 (reset value, not set by the image) | MSP | ECC (SM-15, SM-16) via `SAFE_ISR`; WDOG_EWM branches to the fault handler |
| LPIT0 ch 1 — halt deadman | level 4 = `NX_IRQ_MAX_SYSCALL_LEVEL − 1`, above the RTOS boundary | MSP | walks the safe outputs table when not retriggered within 10 ms (SM-06) |
| FlexCAN0 message buffers | level 8 (rank 3) | MSP | receive/transmit; `SAFE_ISR` wrapper |
| LPUART2 console | level 9 (rank 4) | MSP | — |
| SysTick, PendSV, SVC | level 15 (kernel) | MSP | scheduler; `configASSERT` → RTOS_ASSERT (S1 + S2) |

### 2.2 Start-up and lifecycle levels

1. `Reset_Handler` → `main()` of the family port: clocks, watchdog configuration, **watchdog self-test**
   (SM-07, SSR-017; failure → S1 held), **reset-loop limit** (`startResetLoopLimit`, SSR-065; limit reached → S1
   held), MPU init (SM-12), ROM check registration, then the scheduler.
2. `app::startApp()` adds the lifecycle components; run level 1: platform, `runtime`, **`safety`**
   (`SafetySystem::init` → `SafetyManager::init`: watchdog, supplies, pin lock (SM-21), cycle timer, period check),
   `ecu`; run level 2: platform part two; level 3: UDS, XCP; level 8: `sysadmin`; level 9: RTE.
3. `SafetySystem::run` schedules the safety cycle at a fixed rate of 10 ms in the safety task.
4. Shutdown (run-down, SM-53): components in reverse order with a time-out per step; `SafetySystem::shutdown`
   cancels the cycle and disables the watchdog.

### 2.3 One safety cycle (`SafetyManager::cyclic`)

`ENTER` → MPU gate state on entry (SM-11) → **period check** against LPTMR (SM-04) → `PERIOD_CHECKED` → every 7th
cycle the watchdog service with its own enter/leave sequence and configuration re-check (SM-01 … SM-03) →
`safe_memory::cyclic` (MPU descriptors SM-13, ECC status) → `MEMORY_CHECKED` → `SafeSystem::cyclic` (ADC reference
SM-18/19, internal supplies SM-20) → `SYSTEM_CHECKED` → `SafeIo::cyclic` (register check SM-22) → `IO_CHECKED` →
supervision-peripheral checks (deadman SM-06, watchdog control) → `PERIPHERALS_CHECKED` → delivery of deferred
events (RTE deadlines) → `LEAVE` → MPU gate state on exit. Every checkpoint is a `hit()` of the sequence monitor
(SM-04): a skipped or reordered check is `SAFETY_MANAGER_SEQUENCE_DEVIATION`.

### 2.4 Reaction sequences

| Reaction | Sequence (all in `SafeSupervisor::handle()` under the interrupt lock) | Events (demo) |
|---|---|---|
| S3 report and continue | log line, return | `RTE_TIMING_VIOLATION`, `DEVICE_REPORT`, `CHECK_REGISTER_ERROR` without I/O support |
| S1 | `enterSafeState()`: walk the safe outputs table, indicator on; `leaveSafeState()` when the register is correct again (indicator only, SSR-004) | `CHECK_REGISTER_ERROR` (with `PLATFORM_SUPPORT_IO`); SYS-169: its context names the cause - 1 check fine again, 0 the SAFETY_TEST pin, else class (bits 31..24) and entry (bits 15..0) of the register list, in the log line |
| S1 + S2 | `enterLimpHome()` → `enterSafeState()` → console line → reset-loop record → `softwareSystemReset()` | sequence deviations, watchdog configuration/service, MPU, ECC, ADC reference, supplies, lock register, deadman configuration, period violation, `STACK_OVERFLOW`, `RTOS_ASSERT` |
| S1 held | `holdSafeState()`: limp home, S1, one console line, then loop: walk the table again, service the MCU watchdog; interrupts stay locked; ends only with power cycle or external reset | `WATCHDOG_STARTUP_CHECK_FAILURE`, `ROM_CHECK_FAILURE`, `DEVICE_FAULT` (well-formed report of a device configured for it; otherwise S1 + S2), `RESET_LOOP_LIMIT` |
| bypass | fault handlers and the halt deadman walk the table **before** anything else, then dump/report (concept section 5) | HardFault, BusFault, UsageFault, WDOG_EWM, deadman time-out |

The reaction times per class are in [`concept.md`](concept.md) section 7; they are not repeated here.

---

## 3. Freedom from interference — what the architecture provides

Summary of what exists (details: concept 6.3, [`dfa.md`](dfa.md), task SYS-21 for the rest), sorted by the three
kinds of interference of 6-Annex D:

| Kind | Provided | Not provided |
|---|---|---|
| memory | MPU region for the safety data and the safety stack, gate open only in the safety context (SM-10 … SM-13); runtime stack check of every **task** at the context switch (`configCHECK_FOR_STACK_OVERFLOW 2` → `STACK_OVERFLOW`, S1 + S2); **since SYS-97** a guard of 1 KB directly below the interrupt stack (MSP) that the core may read but not write (MPU descriptor 6, checked every cycle by SM-13): an overflow ends in the hard fault reaction (SM-17) | no partitioning of the application; before SYS-97 the MSP had no guard (section 7.3, finding STK-2) |
| timing and execution | safety task above every application task; period check against an independent timer (SM-04); program-flow checkpoints (SM-04); watchdog with window relation (SM-01 … SM-03); halt deadman above the RTOS boundary (SM-06); RTE deadlines (SM-05, report only) | the watchdog follows one task (AoU-14) |
| exchange of information | E2E for configured signals (SM-31), qualifier for analogue values (SM-30), deferred event box instead of opening the gate (SYS-37) | no protection of the internal RTE buffers against a faulty writer in the same address space |

---

## 4. Allocation of the software safety requirements to components

Every SSR of stage 1 is ASIL B ([`ssr.sdoc`](../requirements/safety/ssr.sdoc)). A component is developed to the highest
ASIL allocated to it (6-7.4.6), so **every component C-SUP … C-BLD is ASIL B**; C-GEN is off-target and falls
under tool classification and the image checks ([`tool-classification.md`](tool-classification.md)). Derived from
the `code:` links of each requirement (component = the place of the linked file, table of section 1).

| SSR | Title | Component(s) | ASIL | Status |
|---|---|---|---|---|
| SSR-001 | Reaction decided in one place | C-SUP | B | implemented |
| SSR-002 | De-energise before every safety reset | C-SUP | B | implemented |
| SSR-003 | Safe state held without reset for faults a reset cannot remove | C-SUP | B | implemented |
| SSR-004 | Leaving the safe state never re-energises a power output | C-SIO | B | implemented |
| SSR-005 | One safe outputs table for every switch-off path | C-HFH, C-SIO, C-GEN | B | partial |
| SSR-006 | Safe state on a board without power outputs | C-HFH | B | implemented |
| SSR-007 | One power-stage adapter per safe_off output, enforced by the build | C-INT, C-GEN | B | implemented |
| SSR-010 | MCU watchdog serviced by the safety task only | C-WDG, C-LCY | B | partial |
| SSR-011 | Watchdog timing relation is a checked property | C-GEN, C-LCY | B | implemented |
| SSR-012 | Watchdog configuration re-checked at every service | C-WDG, C-SPC | B | implemented |
| SSR-013 | Watchdog over-service detected | C-WDG | B | implemented |
| SSR-014 | Control flow through the safety cycle supervised | C-LCY | B | implemented |
| SSR-015 | RTE task and runnable deadlines reported | C-RTE, C-SUP | B | partial |
| SSR-016 | Core halt or stall longer than the deadman time-out de-energises | C-INT | B | implemented |
| SSR-017 | Watchdog hardware tested at every start | C-INT | B | implemented |
| SSR-018 | Safety task period supervised against a hardware time base | C-LCY, C-WDG, C-SPC | B | partial |
| SSR-020 | Safety data writable only from the safety context | C-MEM, C-LCY | B | implemented |
| SSR-021 | Protection gate state checked on entry and exit of the safety cycle | C-LCY, C-MEM | B | implemented |
| SSR-022 | MPU enabled with the region count of the derivative | C-MEM | B | implemented |
| SSR-023 | MPU region descriptors unchanged | C-MEM | B | implemented |
| SSR-024 | ROM check of the application image | C-MEM | B | implemented |
| SSR-025 | Context in which the ROM check may run | C-INT, C-MEM | B | implemented |
| SSR-026 | Uncorrectable RAM ECC error detected | C-MEM | B | implemented |
| SSR-027 | Uncorrectable flash ECC error detected | C-MEM | B | implemented |
| SSR-028 | Fault handlers de-energise first | C-HFH | B | implemented |
| SSR-030 | ADC reference supervised against a configured band | C-SYS | B | implemented |
| SSR-031 | Loss of the ADC reference supervision detected | C-SYS | B | implemented |
| SSR-032 | Internal supplies of the MCU supervised | C-SYS | B | implemented |
| SSR-033 | Safety-relevant pin configuration locked at start-up | C-SIO | B | partial |
| SSR-034 | Safety-relevant registers checked cyclically | C-SIO | B | partial |
| SSR-040 | Quality qualifier for every analogue value | C-RTE | B | implemented |
| SSR-041 | End-to-end protection of configured CAN signals | C-E2E | B | implemented |
| SSR-042 | No transmission into an unhealthy bus | C-CAN | B | implemented |
| SSR-050 | Start reason from two independent sources | C-ECU | B | implemented |
| SSR-051 | Bootloader starts only a valid, compatible image | C-BLD | B | implemented |
| SSR-052 | Defined run-down with time-outs per step | C-ECU | B | implemented |
| SSR-060 | Safe state verified by read-back | planned: C-SIO | B | open |
| SSR-061 | Plausibility between two independent measurement paths | planned: C-RTE (signal layer) | B | open |
| SSR-062 | Age supervision of safety-relevant signals | planned: C-RTE / C-E2E | B | open |
| SSR-063 | Safe state reaches bus-commanded actuators | planned: C-SUP + C-CAN | B | open |
| SSR-064 | Device reports to the safe supervisor | C-SUP | B | implemented |
| SSR-065 | Limit on repeated safety resets | C-SUP, C-INT | B | implemented |
| SSR-066 | Complete configuration checks of the supervision peripherals | C-SPC, C-LCY | B | partial |
| SSR-067 | ROM check repeated during operation | C-MEM, C-INT | B | implemented |
| SSR-068 | ECC supervision complete | C-MEM | B | implemented |
| SSR-069 | Read-failure limit for the internal supply channel | C-SYS | B | implemented |
| SSR-070 | Register lock and check list derived from the configuration | C-GEN, C-SIO | B | partial |
| SSR-080 | Safe outputs table checked in the linked image against an independent expectation | C-GEN, C-HFH | B | implemented |
| SSR-081 | Change of a safety-relevant generated file detected | C-GEN | B | implemented |
| SSR-082 | Configuration and generator identity recognisable in the image | C-GEN + C-BLD | B | implemented |
| SSR-083 | Memory layout of the linked images checked against the derivative | C-GEN | B | implemented |
| SSR-084 | CAN signal layout of every project checked against an independent decoder | C-GEN | B | implemented |

The allocation "planned" of the open requirements is a proposal of this document, not a decision.

Proposal (not done here, other paths): a field `COMPONENT` per SSR in `ssr.sdoc` and a check in
`tools/trace` that the component exists in this table would make the allocation bidirectional and checked
(row 6-08: SSR ↔ architectural element).

---

## 5. Design principles applied (6-7.4.3)

| Principle (own words) | How the safety part meets it | Gap |
|---|---|---|
| hierarchical structure | detection → reaction → recovery (concept section 5); family-neutral C-SUP/C-WDG over the family port | — |
| restricted size and complexity of components | one mechanism per unit in the family port; the supervisor's `handle()` is the one large switch by design | `handle()` has 23 cases; its cyclomatic complexity is not measured |
| restricted size of interfaces | monitor API with three calls and one context word | — |
| strong cohesion | one component per protected resource (memory, system, I/O, supervision peripherals) | — |
| loose coupling | mechanisms know only the monitor API, not each other | the safety task hosts the console command `safety` (test functions; gap analysis task 19) |
| appropriate scheduling | safety task above every application task, fixed rate; `tools/schedana` for the application tasks | the safety cycle's execution time is measured on the board only indirectly (period check) |
| restricted use of interrupts | two bypass paths (fault handlers, deadman) with no RTOS call and no stack beyond their frames | the level-0 fault interrupts reach the logger and the ETL assert handler (section 7.3) |
| appropriate management of shared resources | MPU gate, deferred event box | SPI shared with the SBC (fmea F5), DMA rights (N3) — SYS-21 |

---

## 6. Resource estimate (6-7.4.13)

| Resource | Estimate | Source | Status |
|---|---|---|---|
| execution time | safety task period and lateness bound, watchdog relation (`static_assert` + `nxconfig`); application tasks by `tools/schedana` | [`concept.md`](concept.md) 7.1, `tools/schedana` | analysed; measured WCET of the safety cycle open |
| program memory | 208 464 B `.text` + 1 024 B vectors + small sections of the demo image; layout against the derivative | `arm-none-eabi-size`, `tools/elfcheck` (SSR-083) | checked |
| data memory | `.mpu_bss` 576 B, `.hot_bss` 22 464 B, `.bss` 16 692 B, `.data_general` 496 B, `.noinit` 352 B, `.heap` 516 B, `.stack` (MSP) 1 024 B; **SYS-97:** `.stack` 4 096 B and `.stack_guard` 1 024 B (+4 096 B RAM; demo image: guard 0x200045E0 … 0x200049E0, stack up to 0x200059E0, 4 640 B free up to the no-init area at 0x20006C00) | `arm-none-eabi-size -A`, `nm` (SYS-97) | measured |
| **stack** | per context below (section 7) | `tools/stackuse` | **new** |
| communication | CAN bus load | — | **open** (no estimate; outside this task) |

---

## 7. Worst-case stack

### 7.1 Method

`tools/stackuse` ([README](../../tools/stackuse/README.md)) combines the `-fstack-usage` output of the build with
the call graph of the linked image. Frame of a function = the larger of its `.su` value and the sum of its stack
decrements in the disassembly; call graph on the exact symbols; indirect calls only as far as the profile resolves
them, otherwise the context is marked incomplete; recursion only as bounded where the profile says so, with the
number of times a function can be on the stack. The facts the image does not contain (contexts, stacks, targets of
indirect calls, interrupt levels) are in the profile
[`profiles/evalboard_s32k144_demo.yaml`](../../tools/stackuse/profiles/evalboard_s32k144_demo.yaml), each with its
source. The full report of the baseline build is
`reports/evalboard_s32k144_demo.md` (not published, available on request).

Cross-check of the two frame sources in the demo image: 1 457 functions have both; 1 308 agree exactly, 14 have a
larger sum in the disassembly (taken), the rest are template instances merged under one key (the larger `.su` value
taken). 895 indirect call sites exist in the image (310 virtual); the profile resolves 51 callers.

Task stacks carry, in addition to the call tree, the exception frame of a preemption and the context FreeRTOS saves
on a switch: 108 B + 100 B = 208 B (`context_bytes`). Interrupt handlers run on the MSP; per nesting level one
exception frame of 108 B (Cortex-M4F, extended frame with lazy FP stacking, plus alignment).

### 7.2 Safety task

| Context | Stack | Worst case | Margin | Complete |
|---|---:|---:|---:|---|
| safety task, all entries | 2 048 B | **1 200 B** (59 %) | 848 B | yes |
| — cyclic supervision only (`SafetySystem::execute`) | 2 048 B | 1 112 B (54 %) | 936 B | yes |
| — console command `safety` only | 2 048 B | 1 200 B (59 %) | 848 B | yes |

**Update SYS-98 (wave cloud-1001, 2026-09-29).** The table above is measured again after SYS-98 (lean ETL assert
handler `safety::etlAssertFailed`, kernel faults without `handle()` and logger, ECC interrupts without `printf`); the
report `reports/evalboard_s32k144_demo.md` (not published, available on request) is regenerated
from the same build. The deepest console path now ends in `Uart::getInstance` → `safety::etlAssertFailed` →
`resetWithLine` → `safeConsoleWrite` → `Uart::write` → `Uart::init` instead of formatting in `etl_assert_function`;
the cyclic part grew by 16 B (frame of `resetWithLine`, no longer inlined). The paths described below are those of
issue 1 (SYS-65) and are kept for the history.

Deepest path (console): dispatch 176 B incl. context → `AsyncCommandWrapper::execute` → `AsyncConsole::terminate`
→ `StringWriter::printf` → `PrintfFormatter` → the stream chain (Tagged → NormalizeLf → Stdout) →
`Uart::getInstance` → **`etl_assert_function`** (frame 200 B, counted twice as bounded recursion) → `putByteToStdout`
→ `Uart::write`. Deepest path (cyclic): `SafetyManager::cyclic` → `SafeSystem::checkAdcReference` →
`SafeSupervisor::handle` (200 B) → `handleDeviceReport` → `Logger::warn` → `BufferedLoggerOutput::logOutput`
(128 B entry buffer) → entry serializer. The graph merges all branches of `handle()`, so these are upper bounds:
the device-report branch is not reachable from the ADC check in the real control flow.

Sensitivity: without the ETL assert handler on the paths (profile variant with `ignore`), the safety task needs
1 192 B.

The task stack overflow check of FreeRTOS (`STACK_OVERFLOW`, S1 + S2) catches an overflow of this stack at the next
context switch; the 472 B margin makes it a second line, not the first.

### 7.3 Interrupt stack (MSP)

| Level | Deepest handler | Worst case | Complete |
|---|---|---:|---|
| fault (−1) | HardFault/BusFault/UsageFault → `HardFault_Handler_Final` → `safeConsoleWrite` (SYS-98: no `checkEccErrors`) | 96 B | yes |
| 0 | `Safe_ERM_double_fault_IRQHandler` → `reportEccError` → `resetWithLine` → `safeConsoleWrite` (SYS-98: no `printf`) | 288 B | yes |
| 4 | LPIT0 ch 1 (deadman) | 0 B | yes |
| 8 | FlexCAN0 → receive/transmit → DoCAN transmitter callbacks | ≥ 768 B | **no** (DoCAN transmit path not resolved) |
| 9 | LPUART2 | 28 B | yes |
| 15 | SysTick → `xTaskIncrementTick` → `configASSERT` → `KernelFaultReaction` → `resetOnKernelFault` → `safeConsoleWrite` (SYS-98) | 392 B | yes |
| **all levels nested** | per level deepest + 108 B | **≥ 2 220 B** of 1 024 B (issue 1: ≥ 3 980 B) | no |

Without the ETL assert handler (sensitivity): level 0 needs 456 B, nested ≥ 3 404 B.

**Update SYS-97 (2026-09-30, host).** Budget and overflow detection of the MSP (findings STK-1, STK-2, STK-6; user
decision q-stk-msp: shorten the deep paths first - SYS-98 - then size the MSP to the tool value plus reserve, MPU
guard below it, `stackuse --strict` in the CI). Report of the same build:
`reports/evalboard_s32k144_demo.md` (not published, available on request).

| Level / context | Deepest path | Worst case | Complete |
|---|---|---:|---|
| fault (−1) | HardFault → `customHardFaultHandler` → `HardFault_Handler_Final` (naked MSP check) → `nxHardFaultFinal` / `nxIsrStackOverflowFinal` → `safeConsoleWrite` | 104 B | yes |
| 0 | ERM/FTFC/WDOG_EWM, unchanged | 288 B | yes |
| 4 | LPIT0 ch 1 (deadman) | 0 B | yes |
| 8 | FlexCAN0 → `transmitISR` → `canFrameSentCallback` → `async::execute` (queues the completion for the CAN task) → `xTaskGenericNotify` → `configASSERT` → kernel fault reaction | **400 B** | **yes** (AN-084) |
| 9 | LPUART2 | 28 B | yes |
| 15 | SysTick/PendSV/SVC, unchanged | 392 B | yes |
| all levels nested | per level deepest + 108 B | **1 860 B** | yes |
| start-up on the MSP (`main-before-scheduler`) | reset handler, static constructors (`.init_array`), `main()`, `app::startApp` in `vTaskStartScheduler`; interrupts are enabled, every level nests on top | ≥ 928 B | lower bound, **accepted** in the profile (reason there) |
| **MSP need** | start-up + nested | **≥ 2 788 B of 4 096 B**, margin 1 308 B | levels complete |

- **CAN level (AN-084, STK-6):** SYS-65 had resolved the frame-sent delegate of `FlexCANDevice::transmitISR` to both
  `canFrameSentCallback` and `canFrameSentAsyncCallback`. The only construction of the device in the image
  (`CanFlex2Transceiver`) passes `canFrameSentCallback`, which only queues `canFrameSentAsyncCallback` for the CAN task
  (`async::execute` → `RunnableExecutor::enqueue`). The listeners and the DoCAN transmit path
  (`DoCanPhysicalCanTransceiver::canFrameSent` → `DoCanTransmitter::dataFramesSent` → `sendNextFrames` → ...
  → `CanFlex2Transceiver::write`) therefore run in the CAN task, not on the MSP. Their indirect calls are resolved in
  the profile all the same (tick generator, `nowUs` delegate, frame transmitter, transport listeners, frame size
  mapper), so a context that reaches them is not a lower bound; such a context would report the cycle
  write → notifySentListeners → canFrameSent → ... → write, bounded by the send lock of `DoCanTransmitter` (argument
  not made, no context reaches it today).
- **New interrupt paths of the night (IO-62 sample group "group complete", ARCH-65 fast loop, pin events):** none is
  linked into the demo image - their vectors are weak stubs to `DefaultISR`. The profile now checks the vector table:
  a handler that is linked later is UNCOVERED until it gets a context, the nested bound is then incomplete and
  `stackuse --strict` (CI) fails. A fast loop above the RTOS boundary adds one level (its depth + 108 B); the reserve of
  1 308 B covers a few such levels, the tool says when it does not.
- **Guard:** 1 KB below `__StackLimit` (linker section `.stack_guard`, `__StackGuardStart/End`, 32-byte aligned),
  own MPU descriptor (core read, no write), cut out of the full-access region that contains it. Size check of the
  tool: largest frame on the MSP plus the larger of that frame and an exception frame; because the start-up is only a
  lower bound, the largest frame of the whole image counts (432 B) → 864 B needed.

### 7.4 Findings of the stack analysis

| ID | Finding | Consequence | Proposal |
|---|---|---|---|
| STK-1 | **The interrupt stack (1 024 B) is not shown to be sufficient.** The level-0 memory-fault handler alone reaches 1 032 B + 108 B frame on its deepest path; nested, the bound is ≥ 3 980 B. | An overflow of the MSP is not detected (STK-2) and would overwrite `.heap` and then `.bss`. The bound is conservative (every level at its deepest at once, all branches of `handle()` merged), but even the single-handler case exceeds the stack. | decide a stack budget: either enlarge `STACK_SIZE` to a value the tool proves, or cut the deep paths (STK-3, STK-4); then run `stackuse --strict` in the CI (proposed test entry below). **Done by SYS-97 (host):** MSP 4 096 B against a need of ≥ 2 788 B (levels complete 1 860 B, start-up an accepted lower bound 928 B), `stackuse --strict` green in `stackuse-demo`; board counter-check with the fill pattern (`inject mspuse`) open |
| STK-2 | No overflow detection for the MSP (no MPU guard below `__StackLimit`, no stack limit register on ARMv7-M). | a silent overwrite of general RAM; outside the MPU-protected safety data, but the RTE and the deferred event box live there | MPU guard region below the MSP (SM-10 code, family port) or a watermark check in the safety cycle. **Done by SYS-97 (host):** 1 KB guard, read-only for the core (`MemoryProtection::STACK_GUARD_REGION`, covered by the descriptor check SM-13); the hard fault moves an MSP below the limit back to the top before any C code and writes `HARD FAULT RESET: interrupt stack overflow (guard), outputs off`. Board proof (`inject mspoverflow`) open |
| STK-3 | **`etl_assert_function` (frame 200 B) is reachable from every context** through `etl::singleton_base::instance()` and `Uart::getInstance`; it formats with `etl::print` on the stack. | +400 B on the deepest paths of the safety task and of the level-0 fault interrupt | a lean assert handler (safe state + reset, a fixed string without formatting), as `holdSafeState` does with `safeConsoleWrite`. **Done by SYS-98 (host):** `safety::etlAssertFailed`, event `ETL_ASSERT` (0x117); board proof `lc assert` open |
| STK-4 | Fault handlers and the kernel tick log through the buffered logger (128 B entry buffer) and `printf`: `HardFault_Handler_Final` → `checkEccErrors` → `Logger::debug`; `ERM_double_fault_IRQHandler` → `debug_printf`; `SafeSupervisor::handle` → `Logger::warn` from `configASSERT` in SysTick. | QM code (logger, printf) on the stack of safety reactions; the logger takes a lock | move the logging of fault paths behind the reaction or to the synchronous `safeConsoleWrite`. **Done by SYS-98 (host):** kernel faults, ERM/FTFC and the hard fault reach no logger or `printf` (rule 5 of `hold-console-elf`); nested MSP ≥ 2 220 B, the rest is SYS-97 (CAN level, `safeConsoleWrite` → `Uart::init`) |
| STK-5 | The console command `safety` (test and fault-injection functions) runs in the safety task and determines its worst case (1 576 B against 1 096 B for the cyclic part). | test code on the safety stack in the product | covered by gap analysis task 19 (product build without test commands) |
| STK-6 | The CAN interrupt reaches DoCAN transmit callbacks from the ISR; the tool does not resolve them yet. | level 8 is a lower bound | resolve in the profile (outside the safety part). **Done by SYS-97 (host):** the transmit interrupt only queues the completion for the CAN task; the DoCAN callbacks are not on the MSP, level 8 complete with 400 B (section 7.3, update SYS-97) |

### 7.5 Limits of the estimate

- The profile's resolutions of indirect calls are a review item: a wrong resolution hides a path. Each resolution
  names its source in the profile.
- Merged branches over-estimate (see 7.2); nothing under-estimates as long as the resolutions are complete, no
  function uses `alloca` or a VLA (the image has none: all `.su` qualifiers are `static`) and the bounded cycles are
  bounded as stated.
- Measured values are not available: a stack watermark on the board (FreeRTOS `uxTaskGetStackHighWaterMark`, MSP
  fill pattern) would give a lower bound to compare against — open, needs the board. **SYS-97:** `main()` fills the
  free MSP with a pattern as its first statement; `inject mspuse` (test build with
  `NX_SAFETY_FAULT_INJECTION_COMMAND`) prints the high-water mark since then, which includes the start-up phase the
  tool only bounds from below. Board run open.

---

## 8. Architectural analysis hooks

The safety-oriented analysis of the architecture (6-7.4.10) and the dependent-failure analysis (6-7.4.11) are
separate work products ([`fmea.md`](fmea.md), [`dfa.md`](dfa.md); gap analysis task 16 for the failure propagation
of RTE, ecuState, CAN, NvM and bootloader). This document provides their input: the components and interfaces of
section 1, the contexts of section 2 and the shared stacks of section 7 (a shared stack is a coupling between
components of different intent: STK-3 … STK-5).

---

## 9. Inspection record (6-7.4.14)

**Object:** this document, issue 1, against the code of the baseline. **Method:** inspection with a checklist
derived from the verification methods the standard lists for the architectural design at ASIL B (in own words:
walk-through or inspection of the design, control-flow and data-flow analysis, scheduling analysis; simulation and
prototyping where useful), plus the architecture review questions (section 11 of the architecture).
**Inspector:** the author (agent, cloud session 2026-09-28). **Independence: not met** — the gap analysis
(row 2-14, question Q1) requires a person other than the author for a confirmation; this record is the author's
self-inspection and the input for that review.

| # | Check | Result | Evidence |
|---|---|---|---|
| I1 | every stage-1 SSR allocated to a component with ASIL | pass (52 SSR; the 5 open ones with a proposed component) | section 4, generated from `requirements.yaml` |
| I2 | every component of the safety part has a place in the repository and named units | pass | section 1 |
| I3 | interfaces between safety components named with their rule | pass | section 1.1 |
| I4 | global data of the safety part with writer and protection | pass | section 1.2, `arm-none-eabi-size -A`, `nm` |
| I5 | every safety mechanism assigned to a task or interrupt (control flow) | pass | section 2.1, 2.3 |
| I6 | data flow of events from detection to reaction without a bypass of `handle()` except the two named ones | pass (code reading of `SafeSupervisor.cpp` `handle()`, 23 cases; the bypasses are the fault handlers and the deadman) | section 2.4 |
| I7 | scheduling: safety task above application tasks; watchdog relation checked | pass | `tasks.yaml`, `SafetyManager.cpp` `static_assert`s |
| I8 | resource estimate complete (time, memory, stack, communication) | **fail in part**: communication (bus load) not estimated; safety-cycle WCET not measured | section 6 |
| I9 | stack of every safety context bounded and below its size | **fail**: MSP (STK-1); safety task pass | section 7 |
| I10 | freedom from interference: the three kinds addressed | partial (memory: MSP unguarded; application unpartitioned) | section 3 |
| I11 | QM code on safety paths identified | pass (identified: console, logger, printf, ETL assert handler) | STK-3 … STK-5 |
| I12 | review questions of the architecture answered | pass | section 11 |

Findings of the inspection: STK-1 … STK-6 (section 7.4), I8. None changes a requirement; STK-1 and STK-2 need a
decision (budget, guard) before a release claim.

---

## 10. Other families

The family-neutral components (C-SUP, C-WDG, C-RTE, C-E2E, C-CAN, C-ECU) carry over. The family port (C-LCY,
C-MEM, C-SYS, C-SIO, C-SPC, C-HFH) exists only for s32k1xx ([`concept.md`](concept.md) 1.3). The stack tool is
family-neutral for Arm Thumb-2 images (Cortex-M and the Thumb state of Cortex-R); a new family needs its own profile
with its exception frame size (Cortex-M0+: 32 B, no FP; Cortex-R4F: the port's own interrupt frame, no hardware
stacking) and, on Cortex-R in ARM state, an extension of the instruction patterns in `sp_decrement` (`stmfd`,
`sub sp, sp, #` are covered, `push` in ARM state is the same mnemonic).

---

## 11. Review questions of the architecture (section 11)

Changes: a new document and a new tool under `tools/stackuse` (host tool, not in the image).

1. **Without an SBC:** the tool and the document are independent of the SBC; the MCU watchdog path is described.
2. **Other crystal / clock:** no clock value in the tool; frames do not depend on the clock.
3. **Other derivative / S32K3, STM32, Cortex-R:** the tool reads any Arm Thumb image; derivative facts (MSP size,
   exception frame, levels) are in the profile. Section 10 names what a Cortex-R profile needs.
4. **Two or more CAN buses:** CAN handlers are contexts of the profile; a second bus is a second entry.
5. **Several tasks, free priorities:** every task is a context of its own with its own stack; ISR levels come from
   the profile.
6. **One configuration level per value:** the tool introduces no configuration value of the image; the profile only
   records image facts with their source (stack sizes, levels), it does not feed the build.
7. **Host test with its own profile:** `tools/stackuse/test/profile_test.yaml` with a made-up image; the demo profile
   is tested against the real build only when one exists.
8. **Tools free of Qt, messages with YAML path:** no Qt; profile errors name the YAML path (`contexts[0].entries`).
9. **Device drivers:** not affected.
10. **Files under `platforms/`:** none.

---

## 12. Proposals for the coordinator (paths outside this task)

- Test entries for `tools/test/tests.yaml` (the inventory check of `tools/test` reports
  `tools/stackuse/test/test_stackuse.py` as unregistered until they exist):

  ```yaml
  - name: stackuse
    group: python
    kind: unittest
    comment: >
      SYS-65: Worst-Case-Stack aus .su und Aufrufgraph mit eigenem Testprofil (zwei Tasks, vier Handler auf drei
      Ebenen, Dispatch-Kette, offene/aufgeloeste indirekte Aufrufe, begrenzte/offene Rekursion); 8 Mutanten rot
    args: [tools/stackuse/test/test_stackuse.py, -k, NameKeyTest, -k, ParseTest, -k, AnalysisTest, -k, ProfileTest, -k, CliTest]
    covers: [tools/stackuse/test/test_stackuse.py]
  - name: stackuse-demo
    group: target
    kind: unittest
    comment: >
      SYS-65: Profil evalboard_s32k144_demo gegen den Demo-Build - Sicherheits-Task vollstaendig und mit Reserve,
      jede Profil-Referenz ein Symbol des Abbilds, Rahmenquellen zu mehr als 85 % gleich
    args: [tools/stackuse/test/test_stackuse.py, -k, DemoImageTest]
    covers: [tools/stackuse/test/test_stackuse.py]
    requires: [arm_gcc, openbsw]
  ```
- After STK-1 is decided: a CI step `python tools/stackuse --build {build}/demo --elf … --profile
  tools/stackuse/profiles/evalboard_s32k144_demo.yaml --strict` next to `elfcheck`.
- `ssr.sdoc`: field `COMPONENT` per SSR, checked by `tools/trace` against section 1 (row 6-08).
- New tasks from STK-1 … STK-4 (MSP budget and guard, lean ETL assert handler, logging out of fault paths).

## 13. Sources

- Code of the baseline: `ecu/safety/*`, `platforms/s32k1xx/port/safety/*`, `platforms/s32k1xx/hardFaultHandler`,
  `platforms/s32k1xx/port/main/src/{main.cpp,integration/*,os/isr/*}`, `ecu/application/src/{app/app.cpp,systems/SafetySystem.cpp}`,
  `ecu/asyncCoreConfiguration/tasks.yaml`, `platforms/s32k1xx/port/main/linkerscript/application.dld.in`.
- Safety documents: [`concept.md`](concept.md), [`fmea.md`](fmea.md), [`dfa.md`](dfa.md),
  [`ssr.sdoc`](../requirements/safety/ssr.sdoc), `iso26262-gap.md` (not published, available on request), [`hsi-s32k1xx.md`](hsi-s32k1xx.md).
- Arm: exception entry and the extended stack frame with lazy FP state preservation (ARMv7-M Architecture Reference
  Manual, exception model), FreeRTOS Cortex-M4F port (`port.c`, PendSV context save).
- ISO 26262:2018 part 6 clause 7 and part 9; cited by clause number only.
