# neXtBSW — software unit design: watchdog handling, fault handlers, safety task (SYS-92, N2)

Baseline: v0.2.0

Status: 2026-09-30, **issue 1 (SYS-92, proposal N2 of [`safety-plan.md`](safety-plan.md))**, written against `dev`
at `65f3dc9`.
Readers: the author of a change to one of the units below, the code inspector (SYS-69), the confirmation reviewer,
and an integrator who ports the safety part to another family.
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**What this document is.** The software unit design (activity P6-12 of the safety plan, 6-8.4.2 … 6-8.4.4 read
through `iso26262-gap.md` (not published, available on request) row 6-20) of the three parts of the safety software that had no design
description: the **watchdog handling** (`SafeWatchdog`, its configuration check and its service), the **fault
handlers** (hard fault, bus and usage fault, watchdog warning interrupt) and the **safety task** (`SafetySystem`,
`SafetyManager`, the period check and the safe ISR hooks). It sits one level below the architecture
[`sw-architecture.md`](sw-architecture.md) (components C-WDG, C-LCY, C-HFH, C-SPC, C-INT) and uses the register view of
[`hsi-s32k1xx.md`](hsi-s32k1xx.md); it does not repeat what those say, it says **how each unit does it**.

Section 1 is the **unit register**: every translation unit of the safety part with the document that describes its
design. The register is checked against the source tree and against the list of safety units of PLAT-91 by
[`test/test_unit_design.py`](test/test_unit_design.py).

**Licence rule.** ISO 26262 is cited by part and clause only, as in the safety plan.

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Units described here | **15** translation units in three designs: UD-WDG (4: `SafeWatchdog`, `WatchdogControlCheck`, `CyclePeriodCheck`, `LptmrCycleTimer`), UD-HFH (7: `hardFaultHandler.S` for armv7m and armv6m, `isr_sys`, `isr_wdg`, `HardFaultDump`, `HardFaultDumpRegion`, `SafeStateLatch`), UD-STK (4: `SafetySystem`, `SafetyManager`, `IsrHooks`, `IsrWrapper`) |
| Unit register (section 1) | <!-- unit-counts -->**42** translation units: 33 under the PLAT-91 paths (`libs/bsw/e2e`, `ecu/safety`, `platforms/s32k1xx/port/safety`) and 9 outside them that the architecture counts as safety part (fault handlers, their vector glue, `SafetySystem`, the start-up in `main`). Design status: 24 described, 5 partial, 13 missing |
| Finding for PLAT-91 | the PLAT-91 path list (MISRA 15.5 for the safety units) does **not** contain the fault handlers (`platforms/s32k1xx/hardFaultHandler`, `os/isr/isr_sys.cpp`, `os/isr/isr_wdg.cpp`) nor `SafetySystem.cpp`, although the architecture places them in the safety part (C-HFH, C-INT); proposal U2 |
| Consistency with architecture and HSI (section 5) | 14 statements of `sw-architecture.md` and `hsi-s32k1xx.md` compared with the code: 14 agree; 4 design observations D1 … D4 for the inspection (none contradicts a document) |
| New tasks proposed (section 7) | U1 unit design of the remaining units, U2 extend the PLAT-91 list, U3 start-up time budget of the watchdog as a checked property |
| Verification | **mock/host**: the consistency test `doc/safety/test/test_unit_design.py` (register against the tree and PLAT-91, described units named in their document, the three designs complete, counter-checks with broken copies). The designs were read against the code at `65f3dc9`; nothing was run on the board for this document |

---

## 1. Unit register

A **unit** is a translation unit (`.c`, `.cpp`, `.S`), the level at which the host tests build and the coverage
measures (definition of `sw-architecture.md` section 1). Headers belong to the unit that implements them; a
header-only unit (e.g. the table walk of `safeOutputs.h`) is described with the unit that owns it.

**Scope.** Every translation unit below the source paths of the safety units decided for PLAT-91 (user 2026-09-28:
E2E + `ecu/safety` + `platforms/*/port/safety`, bootloader later), plus the units outside those paths that the
architecture counts as the safety part and that N2 names (fault handlers, their vectors, the scheduling of the safety
task). Test sources (`test/`) are not units of the product.

Columns: **Design description** — the document(s) that describe the unit's design; **Status** — `described` (the
document describes interface, behaviour, data, error handling and configuration of the unit), `partial` (a document
exists but covers only part of it, e.g. configuration only), `missing`; **Follow-up** — for `partial` and `missing`
the task or proposal (section 7) that closes it, `—` otherwise.

<!-- unit-register:start -->
| Unit | Source | Component | Design description | Status | Follow-up |
|---|---|---|---|---|---|
| E2E | `libs/bsw/e2e/src/e2e/E2E.c` | C-E2E | `libs/bsw/e2e/doc/e2e.md` | described | — |
| E2E_P01 | `libs/bsw/e2e/src/e2e/E2E_P01.c` | C-E2E | `libs/bsw/e2e/doc/e2e.md` | described | — |
| E2E_P02 | `libs/bsw/e2e/src/e2e/E2E_P02.c` | C-E2E | `libs/bsw/e2e/doc/e2e.md` | described | — |
| E2E_P11 | `libs/bsw/e2e/src/e2e/E2E_P11.c` | C-E2E | `libs/bsw/e2e/doc/e2e.md` | described | — |
| SafeSupervisor | `ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp` | C-SUP | `platforms/s32k1xx/port/safety/safeSupervisor/doc/safeSupervisor.md`, `doc/safety/concept.md` | partial | U1 |
| DeviceReportRte | `ecu/safety/safeSupervisor/src/safeSupervisor/DeviceReportRte.cpp` | C-SUP | — | missing | U1 |
| DegradedRte | `ecu/safety/safeSupervisor/src/safeSupervisor/DegradedRte.cpp` | C-SUP | — | missing | U1 |
| HwShutoff | `ecu/safety/safeSupervisor/src/safeSupervisor/HwShutoff.cpp` | C-SUP | `doc/safety/hardware-mechanisms.md` | partial | U1 |
| SafeWatchdog | `ecu/safety/safeWatchdog/src/safeWatchdog/SafeWatchdog.cpp` | C-WDG | `doc/safety/unit-design.md` | described | — |
| CyclePeriodCheck | `ecu/safety/safeWatchdog/src/safeWatchdog/CyclePeriodCheck.cpp` | C-WDG | `doc/safety/unit-design.md` | described | — |
| ProgressChecks | `ecu/safety/safeWatchdog/src/safeWatchdog/ProgressChecks.cpp` | C-WDG | — | missing | U1 |
| AdcSafetyConsole | `platforms/s32k1xx/port/safety/adcSafetyConsole/src/adcSafetyConsole/AdcSafetyConsole.cpp` | C-SYS | `platforms/s32k1xx/port/safety/safeSystem/doc/safeSystem.md` | partial | U1 |
| SafeIoRegisterList | `platforms/s32k1xx/port/safety/safeIo/src/registerList/SafeIoRegisterList.cpp` | C-SIO | — | missing | U1 |
| SafeOutputReadbackList | `platforms/s32k1xx/port/safety/safeIo/src/registerList/SafeOutputReadbackList.cpp` | C-SIO | — | missing | U1 |
| SafeIo | `platforms/s32k1xx/port/safety/safeIo/src/safeIo/SafeIo.cpp` | C-SIO | — | missing | U1 |
| SafeState | `platforms/s32k1xx/port/safety/safeIo/src/safeIo/SafeState.cpp` | C-SIO | — | missing | U1 |
| SafeStateDegraded | `platforms/s32k1xx/port/safety/safeIo/src/safeIo/SafeStateDegraded.cpp` | C-SIO | — | missing | U1 |
| IsrHooks | `platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/IsrHooks.cpp` | C-LCY | `doc/safety/unit-design.md` | described | — |
| IsrWrapper | `platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/IsrWrapper.cpp` | C-LCY | `doc/safety/unit-design.md` | described | — |
| ProgressSupervision | `platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/ProgressSupervision.cpp` | C-LCY | — | missing | U1 |
| SafetyManager | `platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/SafetyManager.cpp` | C-LCY | `doc/safety/unit-design.md`, `platforms/s32k1xx/port/safety/safeLifecycle/doc/safeLifecycle.md` | described | — |
| RomCheckFlashWrites | `platforms/s32k1xx/port/safety/safeMemory/src/romCheckFlashWrites/RomCheckFlashWrites.cpp` | C-MEM | `platforms/s32k1xx/port/safety/safeMemory/doc/safeMemory.md` | described | — |
| IntegrityEccHandler | `platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/IntegrityEccHandler.cpp` | C-MEM | — | missing | U1 |
| MemoryProtection | `platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/MemoryProtection.cpp` | C-MEM | — | missing | U1 |
| EccLatentTest | `platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/EccLatentTest.cpp` | C-MEM | `platforms/s32k1xx/port/safety/safeMemory/doc/safeMemory.md` | described | — |
| EccLatentTestTarget | `platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/EccLatentTestTarget.cpp` | C-MEM | `platforms/s32k1xx/port/safety/safeMemory/doc/safeMemory.md` | described | — |
| RomCheck | `platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/RomCheck.cpp` | C-MEM | `platforms/s32k1xx/port/safety/safeMemory/doc/safeMemory.md` | described | — |
| SafeMemory | `platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/SafeMemory.cpp` | C-MEM | `platforms/s32k1xx/port/safety/safeMemory/doc/safeMemory.md` | partial | U1 |
| LpitDeadmanCheck | `platforms/s32k1xx/port/safety/safeSupervisionCheck/src/safeSupervisionCheck/LpitDeadmanCheck.cpp` | C-SPC | — | missing | U1 |
| LpitDeadmanProgress | `platforms/s32k1xx/port/safety/safeSupervisionCheck/src/safeSupervisionCheck/LpitDeadmanProgress.cpp` | C-SPC | — | missing | U1 |
| LptmrCycleTimer | `platforms/s32k1xx/port/safety/safeSupervisionCheck/src/safeSupervisionCheck/LptmrCycleTimer.cpp` | C-SPC | `doc/safety/unit-design.md` | described | — |
| WatchdogControlCheck | `platforms/s32k1xx/port/safety/safeSupervisionCheck/src/safeSupervisionCheck/WatchdogControlCheck.cpp` | C-SPC | `doc/safety/unit-design.md` | described | — |
| SafeSystem | `platforms/s32k1xx/port/safety/safeSystem/src/safeSystem/SafeSystem.cpp` | C-SYS | `platforms/s32k1xx/port/safety/safeSystem/doc/safeSystem.md` | described | — |
| hardFaultHandler | `platforms/s32k1xx/hardFaultHandler/src/armv7m/hardFaultHandler.S` | C-HFH | `doc/safety/unit-design.md` | described | — |
| hardFaultHandler | `platforms/s32k1xx/hardFaultHandler/src/armv6m/hardFaultHandler.S` | C-HFH | `doc/safety/unit-design.md` | described | — |
| HardFaultDump | `platforms/s32k1xx/hardFaultHandler/src/HardFaultDump.cpp` | C-HFH | `doc/safety/unit-design.md` | described | — |
| HardFaultDumpRegion | `platforms/s32k1xx/hardFaultHandler/src/HardFaultDumpRegion.cpp` | C-HFH | `doc/safety/unit-design.md` | described | — |
| SafeStateLatch | `platforms/s32k1xx/hardFaultHandler/src/SafeStateLatch.cpp` | C-HFH | `doc/safety/unit-design.md` | described | — |
| isr_sys | `platforms/s32k1xx/port/main/src/os/isr/isr_sys.cpp` | C-HFH | `doc/safety/unit-design.md` | described | — |
| isr_wdg | `platforms/s32k1xx/port/main/src/os/isr/isr_wdg.cpp` | C-HFH | `doc/safety/unit-design.md` | described | — |
| SafetySystem | `ecu/application/src/systems/SafetySystem.cpp` | C-INT | `doc/safety/unit-design.md` | described | — |
| main | `platforms/s32k1xx/port/main/src/main.cpp` | C-INT | `doc/safety/unit-design.md`, `doc/safety/sw-architecture.md` | partial | U1 |
<!-- unit-register:end -->

Remarks:

- `DeviceReportRte`, `DegradedRte` (SYS-117), the `safeIo` units (with `SafeStateDegraded` of SYS-117), `IntegrityEccHandler`, `MemoryProtection`, `LpitDeadmanCheck` and the progress checks of SYS-95 (`ProgressChecks`, `ProgressSupervision`,
  `LpitDeadmanProgress`; what they do is in [`dfa.md`](dfa.md) section 8.1 and in their header comments) have
  good header comments, but no design description in a document; the concept (section 5) and the architecture
  (sections 1.1, 2.3, 2.4) describe what they do for the safety argument, not how. That is the gap U1 closes.
- `safeSupervisor.md` of the port is a configuration description (project values of the supervisor); the reaction
  logic of `SafeSupervisor::handle()` is described by the concept section 5 and the architecture section 2.4 at the
  level of reactions, not as a unit design. Hence `partial`.
- `main.cpp` (start-up order of the family port) is described here only for its watchdog part (fast test, enable,
  section 3.4); the rest of the start-up — reset-loop limit, MPU init, ROM check registration — is architecture
  section 2.2 and the reset-loop header, not a unit design. Hence `partial`.
- `e2e.md` is German (written before the language rule of 2026-09-17); it describes API, configuration and
  deviations from the AUTOSAR API and counts as described. Its translation is DOC-05.

The counts of section 0 are recomputed from this table and from the tree by the test.

---

## 2. Design rules common to the three designs

These rules follow from the architecture (sections 1.1, 5) and hold for every unit of sections 3 to 5; a unit
section only names a deviation.

| Rule | How the units keep it |
|---|---|
| **Detection raises, the supervisor reacts** (SSR-001) | a unit never resets, never switches an output and never logs a fault as reaction; it calls `trigger()`, `check()` or `hit()` of a monitor of `SafeSupervisor::getInstance()`, whose `handle()` decides. **Exception by design:** the fault handlers (UD-HFH) act before the supervisor can run — they walk the safe outputs table and reset themselves (bypass reaction, architecture section 2.4) |
| **Static memory only** | no heap, no RTOS object created at run time; every state variable is a namespace-scope object or member, zero initialised (`.bss`) where it lies in the MPU protected RAM of a `libsafe*` archive (architecture section 1.2) |
| **Constants from the configuration** | times, counts and table contents come from generated macros (`NX_MODULE_SAFELIFECYCLE_*`, `bsp/safeOutputs/safeOutputsConfiguration.h`); relations between them are checked twice — by `tools/nxconfig` with the YAML path and by `static_assert` on the generated values |
| **Family interface by header** | a family-neutral unit (`ecu/safety/safeWatchdog`) reaches the hardware only through `safety::bsp::Watchdog` (OpenBSW driver) and the family headers of `safeSupervisionCheck`; another family provides the same headers (see section 8) |
| **Single exit** | MISRA C:2012 rule 15.5 is re-enabled for the safety units by PLAT-91 (user decision iso-q6). The units of this document are written with one return per function except `CyclePeriodCheck::sample()` (two early returns) and `SafetyManager::checkSupervisionPeripherals()` (none); PLAT-91 decides the rework |

---

## 3. UD-WDG — watchdog handling

### 3.1 Units and what they implement

| Unit | Responsibility | Mechanisms (concept) | Requirements |
|---|---|---|---|
| `SafeWatchdog` (`ecu/safety/safeWatchdog`) | enable the MCU watchdog with the project time-out; at every service: re-check its configuration, detect over-service, service it, close the service bracket | SM-01, SM-02, SM-03 | SSR-010, SSR-012, SSR-013 |
| `WatchdogControlCheck` (`safeSupervisionCheck`, family S32K1) | compare the WDOG control fields that the OpenBSW driver's own check leaves out | SM-02 | SSR-012, SSR-066 |
| `CyclePeriodCheck` (`ecu/safety/safeWatchdog`) | compare the safety task's cycle distance with an independent counter: one interval too long, a window too short | SM-04 (period part) | SSR-018 |
| `LptmrCycleTimer` (`safeSupervisionCheck`, family S32K1) | provide that counter (LPTMR0 on LPO1K) and re-check its configuration | SM-04 (period part) | SSR-018, SSR-066 |
| `main` (watchdog part only, `platforms/s32k1xx/port/main/src/main.cpp`) | run the watchdog fast test once per start and enable the watchdog before the BSP and the application | SM-07 | SSR-017 |

The driver `safety::bsp::Watchdog` (OpenBSW `safeBspMcuWatchdog`, submodule, unchanged) is **pre-existing
software**: its qualification is SYS-62 (P6-07, P8-09), not part of this design.

### 3.2 Interfaces

| Function | Caller and context | Precondition | Effect |
|---|---|---|---|
| `SafeWatchdog::enableMcuWatchdog()` (static) | `main()` after the fast test, inside a `SafetyShell` (interrupts suspended, gate open); again from `SafeWatchdog::init()` | WDOG `CS[UPDATE] = 1` (left so by the driver) | `Watchdog::enableWatchdog(WATCHDOG_TIMEOUT_MS, true)`: enabled, LPO clock, warning interrupt on, no window, no prescaler |
| `SafeWatchdog::init()` | `SafetyManager::init()` in the safety task's lifecycle transition, inside a `SafetyShell` | none | resets the service counter to its initial value (`0xFFFFFFFF`), re-enables the watchdog (same configuration) |
| `SafeWatchdog::cyclic()` | `SafetyManager::cyclic()` every `WATCHDOG_CYCLIC_COUNTER` cycles, between the `ENTER` hit and its own `LEAVE` hit of `safeWatchdogSequenceMonitor`, gate open | the `ENTER` hit was made by the caller | configuration check → over-service check → service → `LEAVE` hit (section 3.4) |
| `supervision::checkWatchdogControl(bool)` | `SafeWatchdog::cyclic()` | clock of the WDOG always on | pure read of `WDOG->CS`; returns the mask of deviating fields (`WATCHDOG_FIELD_INT/WIN/PRES/UPDATE`) |
| `CyclePeriodCheck::restart()` / `sample(counter, limits)` | `SafetyManager::init()` / `SafetyManager::checkCyclePeriod()` | `limits.windowCycles > 0` | see section 3.5; returns 0 or a context word |
| `supervision::startCycleTimer()`, `readCycleTimer()`, `checkCycleTimer()` | `SafetyManager::init()`, `checkCyclePeriod()` | `PCC_LPTMR0[CGC]` set by the clock start-up | start LPTMR0 free running on LPO1K; latch-and-read CNR; return the mask of deviating fields |

### 3.3 Data

| Data | Type, size | Where | Written by | Initial value | Protection |
|---|---|---|---|---|---|
| `safeWatchdog` (object, member `_serviceCounter`) | `uint32_t` | `.bss` of `libsafeLifecycle` (defined in `SafetyManager.cpp`) → MPU protected RAM | `init()`, `cyclic()` | `0xFFFFFFFF` (constructor and `init()`) | MPU gate (SM-10 … SM-13) |
| `safetyCyclePeriodCheck` (`_last`, `_windowSum`, `_windowCount`, `_started`) | 4 × `uint32_t` | `.bss`, MPU protected RAM | `restart()`, `sample()` | all 0 | MPU gate; the limits are passed per call, not stored, so no initialised data lies outside the region |
| `CYCLE_PERIOD_LIMITS` | `constexpr CyclePeriodLimits` | flash | compiler | derived at compile time (section 3.6) | ROM check (SM-14) |
| `WATCHDOG_TIMEOUT_MS` | `constexpr uint32_t` | flash / immediate | generator | `modules.safeLifecycle.watchdog_timeout_ms` | ROM check |

### 3.4 Behaviour — start-up and service

**Start-up (`main()`, before the scheduler).** Inside one `SafetyShell`: the supervisor is constructed and set to
limp home; `executeFastTest(6000 µs)` runs the WDOG fast test (two test resets with `CS[TST]` 2 and 3; the reset
reason code of the start tells them from real watchdog resets). On success limp home is left; on failure the
monitor `watchdogStartupCheckMonitor` raises `WATCHDOG_STARTUP_CHECK_FAILURE`, whose reaction is **S1 held without
reset** (`holdSafeState()`). In both cases `enableMcuWatchdog()` follows, so from here on the watchdog runs until the
next reset — including through a held safe state, which services it from its hold loop. The time from this enable
to the first service of the safety task must stay below the shortest time-out (observation D1, proposal U3).

**Service (`SafeWatchdog::cyclic()`), in this order:**

1. `fields = checkWatchdogControl(true)`; if `Watchdog::checkWatchdogConfiguration(WATCHDOG_TIMEOUT_MS)` fails,
   `fields |= WATCHDOG_FIELD_DRIVER_CHECK`. `safeWatchdogConfigMonitor.check(fields == 0, fields)` — a deviation is
   `SAFE_WATCHDOG_CONFIGURATION_ERROR` with the fields as context (S1 + S2).
2. `current = Watchdog::getWatchdogServiceCounter()`. If `current > _serviceCounter + 1` (unsigned), somebody else
   serviced the watchdog since the last service of the safety task: `serviceWatchdogMonitor.trigger()` →
   `SAFE_WATCHDOG_SERVICE_DEVIATION` (S1 + S2). The initial value `0xFFFFFFFF` makes `_serviceCounter + 1` wrap to
   0, so the **first** service after `init()` already requires that nobody serviced the watchdog since it was
   enabled. `_serviceCounter = current`.
3. `Watchdog::serviceWatchdog()` (refresh sequence of the driver).
4. `safeWatchdogSequenceMonitor.hit(LEAVE)`. A service path that returned early or was entered without `ENTER`
   (made by the caller) is `SAFE_WATCHDOG_SEQUENCE_DEVIATION` (S1 + S2).

Without `PLATFORM_SUPPORT_WATCHDOG` (a family without the driver) steps 1–3 are compiled out and only the bracket
remains; such a family has no MCU watchdog supervision from this unit and must say so in its port (section 8).

**Detection of a missing service** is the watchdog hardware itself (SM-01): the warning interrupt
`WDOG_EWM_IRQHandler` enters the fault handler (UD-HFH), which switches the outputs off; the watchdog reset
follows. Over-service by the safety task itself (too many services) is not possible by construction (one call site,
every `WATCHDOG_CYCLIC_COUNTER` cycles); a safety task that runs too fast is the window part of SM-04 (section 3.5).

### 3.5 Behaviour — period check (`CyclePeriodCheck::sample`)

State machine with two states: **unstarted** (`_started == 0`) and **running**.

- *unstarted*: the sample becomes the reference (`_last`), window sum and count are cleared, state → running,
  return 0. Entered after construction and after `restart()`.
- *running*: `interval = (now − _last) & counterMask` (correct across one wrap-around of the counter);
  `_last = now`; the interval is added to the window sum (saturating at `0xFFFFFFFF`) and the window count
  incremented. When the count reaches `windowCycles`, the window is evaluated — a sum below `minWindowTicks` gives
  `WINDOW_TOO_SHORT` with the sum — and cleared. Then, independent of the window: an interval above
  `maxIntervalTicks` returns `INTERVAL_TOO_LONG` with the interval; otherwise the window result (0 or
  `WINDOW_TOO_SHORT`) is returned. A too-long interval is therefore reported by the first late cycle, before any
  other check of that cycle.

The context word packs the kind into bits 31..24 and the value (saturated to 24 bit) into bits 23..0; the kind
`TIMER_CONFIGURATION` (3) carries the deviating fields of `checkCycleTimer()` instead of a time. All three kinds are
one event, `SAFETY_CYCLE_PERIOD_VIOLATION` (S1 + S2).

**Why a window and not a per-interval lower bound:** the task is scheduled at a fixed rate; after one late cycle the
scheduler catches up with a short interval. One short interval is legitimate, a whole window of them is not — the
task runs too fast, or the counter runs too slow or stopped (which would otherwise switch the check off silently).

### 3.6 Timing and derived values

| Quantity | Formula (source) | Demo value |
|---|---|---|
| service interval in cycles `WATCHDOG_CYCLIC_COUNTER` | floor(`watchdog_timeout_ms` × 1000 / (3 × `task_period_ms` × LPO_max‰)) (`tools/nxconfig`, generated `NX_MODULE_SAFELIFECYCLE_DERIVED_WATCHDOG_SERVICE_CYCLES`) | 7 → service every 70 ms |
| shortest real time-out | `watchdog_timeout_ms` × 1000 / LPO_max‰ | 250 ms → 230.2 ms |
| three services before that time-out | 3 × 7 × 10 ms = 210 ms ≤ 230.2 ms (`static_assert` in `SafetyManager.cpp`, `checks.py safety_timing`) | holds |
| `maxIntervalTicks` | floor((P + D) × 1 kHz × LPO_max‰ / 10⁶) + 1 | 14 ticks: 17 ms always reported, 12 ms never |
| `minWindowTicks` | floor((W × P − D) × 1 kHz × LPO_min‰ / 10⁶), W = service interval | 59 ticks: a mean period below about 7.6 ms is reported |
| wrap-around of the counter | 16 bit at 1 kHz | 65.5 s ≫ one window (`static_assert`) |

P = `task_period_ms`, D = `cycle_lateness_ms`; LPO‰ are the per-mille limits derived from `mcudb` `clock.lpo`.

### 3.7 Configuration

Project values (`modules.safeLifecycle`): `task_period_ms`, `watchdog_timeout_ms`, `cycle_lateness_ms`. Derivative
value: `clock.lpo` (min/max/nominal). Everything else is derived (section 3.6). Ranges and checks are in the module
manifest and in [`safeLifecycle.md`](../../platforms/s32k1xx/port/safety/safeLifecycle/doc/safeLifecycle.md); the
admissible configuration range of these values is argued in [`config-range.md`](config-range.md) (N3).

### 3.8 Hardware-software interface

WDOG fields, expected values and the re-check per field: HSI section 4.1. LPTMR0, LPO1K and the clock gate: HSI
sections 3 and 4.2. The unit design adds no register the HSI does not list.

### 3.9 Verification

| Test (`tools/test/tests.yaml`) | What it shows for these units |
|---|---|
| `safe-watchdog` | real `SafeWatchdog.cpp` + `WatchdogControlCheck.cpp` + real `SafeSupervisor` on a WDOG register model: every single corrupted field (`INT`, `WIN`, `PRES`, `UPDATE`, `EN`, `CLK`, `TOVAL`) is `SAFE_WATCHDOG_CONFIGURATION_ERROR` with its field, over-service is `SAFE_WATCHDOG_SERVICE_DEVIATION`, the bracket closes |
| `cycle-period-check` | `CyclePeriodCheck` alone: reference, interval bound, window bound, wrap-around, saturation |
| `cycle-period-constants` | the code takes every value from the generated macros; bounds recomputed with fractions from the LPO range of `mcudb` |
| `cycle-timer-off` | `LptmrCycleTimer` checks on a register model: clock gate, enable, mode, prescaler, LPO1K; no counter access with the gate off |
| `nxconfig-watchdog` | derivation and every check of `safety_timing` with own test profiles (other LPO range, two SPI buses) |
| board (CHANGELOG, fmea B4) | demo values: no watchdog reset in every board run; safety cycle 9 982 … 10 019 µs under load (B4) |

---

## 4. UD-HFH — fault handlers

### 4.1 Units and what they implement

| Unit | Responsibility | Mechanisms | Requirements |
|---|---|---|---|
| `isr_sys` (`HardFault_Handler`, `HardFault_Handler_Final`, `BusFault_Handler`, `UsageFault_Handler`, kernel fault hooks) | vector entry of the hard fault, final line and reset; bus/usage fault fall-backs; kernel stack overflow and assertion to the supervisor | SM-17 | SSR-028, SSR-002 |
| `isr_wdg` (`WDOG_EWM_IRQHandler`, `Safe_WDOG_EWM_IRQHandler`, strong `nxSafeOutputs`) | the watchdog warning vector into the same handler; the project's safe outputs table in flash with compile-time checks | SM-01 (pre-reset part), SM-17, SM-23 | SSR-005, SSR-028, SSR-080 |
| `hardFaultHandler` (`customHardFaultHandler`, `src/armv7m` and `src/armv6m`) | walk the safe outputs table first, then write the dump, then branch to `HardFault_Handler_Final` | SM-17, SM-23 | SSR-005, SSR-006, SSR-028 |
| `HardFaultDump` | reader of the dump: state (none, corrupt, valid), checksum, formatted report | diagnostic only | — |
| `HardFaultDumpRegion` | target part of the reader: location from the linker script, clearing after power-on, report once and clear | diagnostic; input of the reset-loop limit | SSR-065 (input) |
| `SafeStateLatch` | the two words of the latch of S1 (`nxSafeStateLatch`, `nxSafeStateEntries`); the functions are inline in `safeOutputs.h` | SM-24, SM-25 | SSR-004, SSR-005 |

### 4.2 Interfaces

| Entry | Entered from | Contract |
|---|---|---|
| `HardFault_Handler` (naked) | exception 3 — every fault escalates to it, because `SHCSR` leaves MemManage, BusFault and UsageFault disabled (HSI 4.7) | one branch `b customHardFaultHandler`: no prologue, LR still holds EXC_RETURN, the stack is untouched |
| `WDOG_EWM_IRQHandler`, `Safe_WDOG_EWM_IRQHandler` (naked) | WDOG warning interrupt (IRQ 22), a fixed time before the watchdog reset | same branch; on ARMv6-M `ldr r0, =customHardFaultHandler; bx r0` because `b` reaches only ±2 KB. **No `SAFE_ISR` wrapper** (it would run C code on a possibly broken stack and overwrite EXC_RETURN) |
| `customHardFaultHandler` | the two vectors above only | uses R0–R3 and R12 until the dump holds R4–R11; no stack, no RAM except the dump; never returns |
| `HardFault_Handler_Final` | end of `customHardFaultHandler` | ordinary C function: one console line (poll, bounded), `checkEccErrors()`, `softwareSystemReset()`, endless loop. **SYS-97:** naked, no stack: MSP below `__StackLimit` (overflow into the guard) → record `nxIsrStackOverflowMsp`, MSP = `__StackTop`, branch to `nxIsrStackOverflowFinal` (own line, reset); otherwise branch to `nxHardFaultFinal`, the former C body (line, reset; `checkEccErrors()` removed by SYS-98) |
| `BusFault_Handler`, `UsageFault_Handler` | only if a later change enabled them in `SHCSR` | line and reset **without** the table walk (HSI finding H2) |
| `nxKernelStackOverflow`, `nxKernelAssertFailed` (with FreeRTOS) | kernel context switch / `configASSERT` | `SafetyShell`, report to the supervisor (S1 + S2), reset if it ever returns |
| `nxSafeOutputs[]` | read by the handler, by `nxSafeOutputsSwitchOff()` (deadman ISR) and by `SafeState` | entries {address, value, gate address, gate mask}, ended by address 0; `static_assert`: every entry aligned, non-zero value and gate |
| `prepareDump(ramRetained)`, `reportAndClearDump()`, `getDumpState()` | `main()`: before anything reads the dump / after the start-up / reset-loop input | the dump is cleared after power-on or low voltage only; the report clears the magic |

### 4.3 Behaviour — `customHardFaultHandler`

Both architecture variants follow the same sequence; the ARMv6-M variant uses only low registers and 8-bit
immediates, the ARMv7-M variant adds the fault status registers and the FP extended frame.

1. `R12 = xPSR` (exception number for the dump).
2. **Safe outputs first (IO-32):** for every entry of `nxSafeOutputs` until address 0: if a gate address is given
   and `(*gate & mask) == 0`, skip (the peripheral's clock is off — an access would fault again); else store
   `value` to `address`. Then `dsb`. Only R0–R3 are used; they were stacked by the exception entry.
3. Clear the whole dump region including the magic word (a partly written dump is never valid).
4. Store handler xPSR and EXC_RETURN, then R4–R11 (not stacked by the hardware).
5. ARMv7-M: store CFSR, HFSR, MMFAR, BFAR (lost on the reset).
6. Select MSP or PSP by EXC_RETURN bit 2. If the stack pointer is not inside `[__hard_fault_ram_start,
   __hard_fault_ram_end − frame size]`, store it raw and skip 7–8 (a corrupt stack pointer is a frequent fault
   cause; reading through it would fault inside the handler → lockup).
7. Set `DUMP_FLAG_FRAME_VALID`; copy the stacked R0–R3, R12, LR, PC, xPSR; compute the SP before the exception
   (frame size, FP extended frame by EXC_RETURN bit 4, alignment padding by stacked xPSR bit 9).
8. Copy stack words while they lie inside the RAM, up to the dump's stack area.
9. Checksum over all words before the checksum field; store it; store the **magic last**.
10. Branch to `HardFault_Handler_Final`.

**Why this order.** De-energising is the safety reaction; everything after step 2 is diagnosis. A handler that
printed first would leave a bridge switching for milliseconds (board finding 2026-09-18, IO-32); a handler that
called a function first would lose EXC_RETURN and read the wrong frame (PLAT-20).

### 4.4 Behaviour — after the handler

`HardFault_Handler_Final` writes `HARD FAULT RESET: outputs off, dump saved` (SYS-169: `WATCHDOG RESET: outputs off, dump saved` when the valid dump carries the watchdog's vector, WDOG_EWM_IRQn + 16) between two flushes of the safe
console (polling, bounded — the fault may have hit the UART driver, the logger or the RTOS), checks the ECC error
flags once more (`checkEccErrors()`: an ECC error that caused the fault is reported, not lost) and requests the
system reset. At the next start `main()` counts a present dump (or a lockup) as a fault exception for the reset-loop
limit (SSR-065) and prints the dump once (`reportAndClearDump()`).

### 4.5 Data

| Data | Where | Written by | Notes |
|---|---|---|---|
| dump, `HARD_FAULT_DUMP_SIZE` = 0x144 bytes | `.noinit`, `__hard_fault_dump_start` (linker script) | handler; cleared by `prepareDump()` after POR/LVD, magic cleared by the report | layout version `HFD1` in `hardFaultDumpLayout.h`, the single source for assembler, linker script and reader |
| `nxSafeOutputs[]` | flash `.rodata` (strong symbol in `isr_wdg.cpp`, weak empty default in the handler library) | generator (`tools/bswgen`) | walked without RAM; checked in the linked image (`safe-outputs-table*`) |
| `nxSafeStateLatch`, `nxSafeStateEntries` | ordinary `.bss` (deliberately **not** the protected RAM, see `safeOutputs.h`) | `nxSafeStateLatchSet()` from every entry into S1; cleared only by `SafeState::leaveSafeState()` | any non-zero value reads as latched (errs to the safe side) |

### 4.6 Hardware-software interface

HSI section 4.7 (vectors, `SHCSR`, `CCR`, `AIRCR`, no-init RAM) and section 5 (exceptions and priorities). The
handler relies on `SHCSR` keeping the reset value (all configurable faults escalate) — **not re-checked** (HSI H2).

### 4.7 Verification

| Test | What it shows |
|---|---|
| `safe-outputs-order` | disassembly of both architecture variants and of the demo ELF: the table walk comes before the first store to the dump and before any call; the vectors are branches |
| `safe-outputs-table`, `-charge`, `-bt148` | the generated table in the linked image against an independent expectation (SSR-080) |
| `hard-fault-dump` | reader: states none/corrupt/valid, checksum, formatting |
| `kernel-fault-reaction` (+ mutations) | stack overflow and assertion hooks reach the supervisor; HardFault/BusFault/UsageFault write the line between two flushes, a dead transmitter costs only the time-out |
| `safe-state`, `safe-state-hold` | latch set on every entry, drivers refuse while latched, only `leaveSafeState()` clears |
| board | fault injection by console (`safety` commands, B-series of `fmea.md`): outputs off, dump printed after the reset |

---

## 5. UD-STK — safety task

### 5.1 Units and what they implement

| Unit | Responsibility | Mechanisms | Requirements |
|---|---|---|---|
| `SafetySystem` (`ecu/application/src/systems`) | lifecycle component "safety": `init()` → `SafetyManager::init()`; `run()` schedules the cycle at a fixed rate of `task_period_ms` in the safety task; `shutdown()` cancels it and disables the watchdog | SM-04 (the schedule the check supervises) | SSR-010, SSR-018 |
| `SafetyManager` (`safeLifecycle`) | one safety cycle: gate state, period, watchdog service, memory, supplies, I/O, supervision peripherals, deferred events — each followed by a checkpoint | SM-04, SM-06, SM-11, and the call of every cyclic mechanism | SSR-010, SSR-014, SSR-018, SSR-021, SSR-066 |
| `IsrHooks` (`SafePreIsrHook`, `SafePostIsrHook`, `resetCounterSafeIsrHook`) | close the MPU gate and the safety stack for an interrupt that preempts the safety cycle, reopen them on return; nesting counter | SM-10 … SM-12 (gate part) | SSR-020 |
| `IsrWrapper` (`SAFE_ISR(...)`) | generate `Safe_<vector>` wrappers (pre hook, handler, post hook) for FTFC fault, ERM double fault, SysTick and (SYS-170) every other external vector of the derivative from the generated list `interrupts/nxSafeIsrVectors.h`, CAN message-buffer vectors included; weak default `DefaultISR` for every handler the integration does not define | as above | SSR-020 |

### 5.2 Interfaces

| Function | Context | Contract |
|---|---|---|
| `SafetySystem::init()` | lifecycle run level 1, transition context = safety task | calls `SafetyManager::init()` then `transitionDone()` |
| `SafetySystem::run()` | same | `scheduleAtFixedRate(context, *this, _timeout, task_period_ms)`; `static_assert` that it equals `SafetyManager::CYCLE_PERIOD_MS` |
| `SafetySystem::shutdown()` | run-down (SM-53) | cancels the cycle, then `Watchdog::disableWatchdog()` (observation D2) |
| `SafetyManager::init()` | from `SafetySystem::init()` | inside one `SafetyShell`: reset the ISR-hook counter, `safeWatchdog.init()`, `safeSystem.init()`, `safeIo.init()` (pin lock SM-21), `startCycleTimer()`, `safetyCyclePeriodCheck.restart()`; then `RomCheck::init()` |
| `SafetyManager::cyclic()` | every period, safety task (highest application priority, `above_application: true`) | section 5.3; never blocks, never waits for another task |
| `SafePreIsrHook()` / `SafePostIsrHook()` | first/last action of a `Safe_<vector>` wrapper, every external vector and SysTick (SYS-170), any interrupt level | executed with all interrupts suspended; balanced by construction of the wrapper |

### 5.3 Behaviour — one cycle (`SafetyManager::cyclic`)

1. Read the gate state (`fusaGateIsLocked()`), then open it for the scope of the cycle
   (`ProtectedRamScopedUnlock`, restored at scope exit). `mpuStatusCheckOnEnterMonitor.check(locked)`: the gate
   must have been **closed** on entry (`MPU_UNLOCKED_ON_SAFETY_MANAGER_ENTRY` otherwise).
2. `hit(SAFETY_MANAGER_ENTER)`.
3. `checkCyclePeriod()`: `checkCycleTimer()`; if the timer deviates, context = `TIMER_CONFIGURATION` + fields (the
   counter is **not** read — with the clock gate off the read would bus fault); else
   `sample(readCycleTimer(), CYCLE_PERIOD_LIMITS)`. A non-zero context → `safetyCyclePeriodMonitor.trigger()`.
   First, so that a late cycle reports itself before the results of its checks are used.
4. `hit(SAFETY_MANAGER_PERIOD_CHECKED)`.
5. `++_counter`; when it reaches `WATCHDOG_CYCLIC_COUNTER`: `safeWatchdogSequenceMonitor.hit(ENTER)`,
   `safeWatchdog.cyclic()` (section 3.4, ends with `hit(LEAVE)`), `_counter = 0`.
6. `safe_memory::cyclic()` (MPU descriptors, ECC status) → `hit(MEMORY_CHECKED)`.
7. `safeSystem.cyclic()` (ADC reference, supplies) → `hit(SYSTEM_CHECKED)`.
8. `safeIo.cyclic()` (register check) → `hit(IO_CHECKED)`.
9. `checkSupervisionPeripherals()`: if the project has a halt deadman, `checkLpitDeadman(expected)`; deviations →
   `deadmanConfigMonitor.trigger(fields)` → `hit(PERIPHERALS_CHECKED)`.
10. `deliverDeferredEvents()` (reports of tasks without write access, e.g. RTE deadlines); with I/O support the
    pending `safeadc reset` request of the console task is carried out here.
11. `hit(SAFETY_MANAGER_LEAVE)`; read the gate state again and `mpuStatusCheckOnLeaveMonitor.check(locked)`: it must
    still be **open** here (closed would mean something inside the cycle closed it); the scope then restores it.

Every checkpoint is a `hit()` of one sequence monitor: a cycle that returns early, skips or reorders a step is
`SAFETY_MANAGER_SEQUENCE_DEVIATION` at the next hit, one that never reaches `LEAVE` is seen by the next `ENTER`.
The reactions of every event raised in the cycle are S1 + S2 (architecture section 2.4); the supervisor's `handle()`
runs synchronously in the cycle, so a fault found in step *n* stops the cycle there.

### 5.4 Behaviour — safe ISR hooks

`counterSafeIsrHook` (protected `.bss`) counts nesting. **Pre hook:** with all interrupts suspended; if the counter is
above 0 (a safe ISR is already active) it is incremented under a short unlock; else, if the gate is **open** (the
interrupt preempted the safety cycle), counter = 1, the safety stack region is closed for the ISR and the gate
closed. If the gate was closed (the interrupt preempted a normal task) nothing is done. **Post hook:** mirror image;
at counter 1 the stack region and the gate are reopened and the counter cleared. The safety cycle continues with the
gate state it had.

**Which vectors run through the hooks (SYS-170, SYS69-S1, finding SFP-F01).** Up to SYS-170 only FTFC fault, ERM
double fault and the CAN message-buffer vectors had a wrapper, so UART, pin-event, LPIT and SysTick handlers that
preempted the cycle ran with write access to the protected RAM. Since SYS-170 the vector table of both startups is
the generated list of the derivative (`startup/nxIrqVectors.inc`, tools/bswgen) and every entry points at
`Safe_<handler>`: for every external vector except the safety handlers, and for SysTick, `Safe_<handler>` is a
`SAFE_ISR` wrapper (`IsrWrapper.cpp`, list `interrupts/nxSafeIsrVectors.h`), whether the integration uses the
vector or not (an unused one reaches `DefaultISR` with the gate closed). So an interrupt handler that returns to the
interrupted code never runs with write access to the safety RAM.

Not wrapped, each on purpose:

| Vector | Why |
|---|---|
| watchdog/EWM (`Safe_WDOG_EWM_IRQHandler`) | naked branch into the fault handler, outputs off first, never returns (section 4.2, K10) |
| halt deadman, LPIT0 channel 1 (`Safe_LPIT0_Ch1_IRQHandler` = alias of `LPIT0_Ch1_IRQHandler`, IO-32) | a safety path itself: its first action is the safe outputs walk with no call before it (checked by disassembly, `test_safe_outputs_order.py`); it stores only LPIT registers and a flag outside the protected RAM. Without the debug halt guard the vector is `DefaultISR` (weak). The other LPIT channels are wrapped |
| HardFault, BusFault, UsageFault | never return; the fault path must reach the safe outputs without C code first (section 4) |
| PendSV, SVC | RTOS context switch: PendSV stacks the outgoing task's registers onto its process stack, which the pre hook makes read-only for the safety task, and a post hook after a switch would open the gate for the incoming task. The safety task has the highest priority and never blocks inside the cycle (section 5.2), so no switch away from it happens while the gate is open |
| NMI, MemManage, DebugMon, reserved numbers | `DefaultISR` (unused, the S32K1 NMI pin is not configured) |

**Assumption (SYS-170, review point SYS170-S4 a): interrupt handlers use no floating point.** The core stacks the
FP context of an interrupted task lazily: the exception entry only reserves the space on that task's stack, the
first FP instruction of the handler writes it there. If the interrupted task is the safety task with an active FP
context, the pre hook has just made that stack read-only, and the deferred write would be a bus fault (fault
reaction, reset). So nothing a wrapped handler or the hooks reach may contain a VFP instruction; `tools/elfcheck`
checks it on the image (code `isr-fpu`, every function reached through `bl`/`b.w`; calls through a register and
`DefaultISR` - an unexpected interrupt that never returns - are not followed). Eval board image: 63 functions,
none with FP.

The rule is checked on the linked image by `tools/elfcheck` (code `isr-unwrapped`): every vector from SysTick on
points at `DefaultISR`, the watchdog path, the halt deadman or a function whose first call is `SafePreIsrHook` and which calls
`SafePostIsrHook` — recognised by its code, not by its name.

### 5.5 Data

| Data | Where | Notes |
|---|---|---|
| `safetyManager` (member `_counter`, `uint16_t`) | `.bss` of the application (defined in `SafetySystem.cpp`) — **outside** the protected RAM | observation D3 |
| `safeWatchdog`, `safeSystem`, `safeIo`, `safetyCyclePeriodCheck` | `.bss` of `libsafeLifecycle` → protected RAM | written only with the gate open |
| `counterSafeIsrHook` | `.bss` of `libsafeLifecycle` → protected RAM | written under a short unlock |
| safety task stack `safetyStack` | 2048 B, own MPU region | worst case 1576 B (SYS-65, section 7.2 of the architecture) |

### 5.6 Timing

Period `task_period_ms` (demo 10 ms), fixed rate. Guaranteed detection time of a check in the cycle while the task
runs: P + D + one period = 17 ms for the demo (fmea F9). A task that stops is detected by the watchdog (SM-01) at the
latest after the shortest time-out. Worst-case execution time of one cycle: not measured separately; the cycle
distance under load was measured (B4). Worst-case stack: SYS-65.

### 5.7 Verification

| Test | What it shows |
|---|---|
| `safety-cycle` | `SafetyManager` with its own test profile (`test/host/stubs/integration`: 270 ms, 8 cycles): order of the checkpoints, service every n-th cycle, period check first |
| `safety-gate` (+ `safety-gate-mutations`, 9 built-in faults) | gate state on entry and exit, the hooks' nesting, the unlock scopes |
| `bswgen` (`DerivativeInterruptsTest`), `elfcheck` (`IsrWrapperUnitTest`), `elfcheck-demo` | SYS-170: every non-safety vector of the derivative in the wrapper list, both startups route through `Safe_<handler>`; in the image every vector from SysTick on is wrapped (mutant: a vector around its wrapper turns it red) |
| `cycle-timer-off` | timer configuration fault reported as kind 3 without a counter read, in every phase of the watchdog window |
| `safety-fault-injection` | the injected faults of the console commands reach the right events |
| board | demo: no watchdog reset in any board run; B4 cycle distance under load |

---

## 6. Consistency with the architecture and the HSI

Each statement of the architecture or the HSI about these units, compared with the code at `65f3dc9`.

<!-- unit-consistency:start -->
| # | Statement | Source | Code | Result |
|---|---|---|---|---|
| K1 | the watchdog is serviced from the safety task only | `sw-architecture.md` 1.1, SSR-010 | one call site of `serviceWatchdog()` in the running system (`SafeWatchdog::cyclic`), plus the hold loop of the supervisor, which never returns | agrees |
| K2 | service every 7th cycle, three services before the shortest time-out | `sw-architecture.md` 2.1, 2.3 | `WATCHDOG_CYCLIC_COUNTER` from the generated macro, `static_assert` on the relation | agrees |
| K3 | configuration re-check at every service covers EN, CLK, TOVAL, INT, WIN, PRES, UPDATE | `hsi-s32k1xx.md` 4.1 | driver check + `checkWatchdogControl()` | agrees; `CMD32EN`, `STOP`, `WAIT`, `DBG` not checked, as the HSI says |
| K4 | the period check comes first in the cycle | `sw-architecture.md` 2.3 | step 3 of section 5.3 | agrees |
| K5 | the period check is measured against LPTMR0 on LPO1K, independent of the scheduler | `hsi-s32k1xx.md` 4.2 | `startCycleTimer()`: `PSR[PCS] = 1`, prescaler bypassed | agrees |
| K6 | a timer with gated clock is reported without a counter read | `hsi-s32k1xx.md` 3 | `checkCycleTimer()` returns at the gate before any LPTMR access | agrees |
| K7 | every checkpoint of the cycle is a hit of the sequence monitor | `sw-architecture.md` 2.3 | seven hits, `ENTER` … `LEAVE` | agrees |
| K8 | gate closed on entry, open on exit of the cycle | `sw-architecture.md` 1.1 (SSR-021) | `mpuStatusCheckOnEnter/OnLeaveMonitor` | agrees |
| K9 | the fault handlers walk the table before anything else | `sw-architecture.md` 2.4, `hsi-s32k1xx.md` 4.7 | step 2 of section 4.3, checked by disassembly | agrees |
| K10 | the watchdog warning vector branches into the fault handler without a wrapper | `sw-architecture.md` 2.1 | `isr_wdg.cpp`, `IsrWrapper.cpp` comment | agrees |
| K11 | BusFault and UsageFault print and reset without the walk | `hsi-s32k1xx.md` 4.7 | `isr_sys.cpp` | agrees (finding H2 stands) |
| K12 | the dump lies in no-init RAM and is cleared after POR/LVD before its first read | `hsi-s32k1xx.md` 4.7 | `prepareDump()` in `main()` | agrees |
| K13 | `SafetySystem::shutdown` cancels the cycle and disables the watchdog | `sw-architecture.md` 2.2 | `SafetySystem.cpp` | agrees (observation D2) |
| K14 | state of the safety components lies in the protected `.bss` of the `libsafe*` archives | `sw-architecture.md` 1.2 | watchdog, period check, hook counter: yes; `safetyManager._counter`: no | agrees for the components the architecture lists; see D3 |
<!-- unit-consistency:end -->

**Design observations for the code inspection (SYS-69).** None contradicts a document; each is a property the
design relies on without a check of its own.

- **D1 — start-up budget of the watchdog.** The watchdog is enabled in `main()` before the BSP, the scheduler and
  lifecycle run level 1; its first service is `WATCHDOG_CYCLIC_COUNTER` cycles after `SafetySystem::run()`. The time
  in between must stay below the shortest time-out (230 ms for the demo). It holds on the eval board (no watchdog
  reset in any run), but no tool checks it; a project with more or slower run-level-1 components (e.g. SBC
  initialisation over SPI) moves it. Proposal U3.
- **D2 — watchdog off during the run-down.** `SafetySystem::shutdown()` disables the MCU watchdog. During the
  remaining run-down the steps are supervised by their own time-outs (SM-53), not by the watchdog. That is the
  inherited OpenBSW behaviour; whether the run-down should keep the watchdog running (and service it from the
  run-down) is a question for SYS-69 and the FMEA (SYS-71). Update SYS-173 (2026-10-02): `SafetySystem::shutdown()` runs
  only on the way to level 0 (restart); the sleep path stays at level 1 with the watchdog on. The restart itself is now
  supervised by the ECU state management (direct reset after `shutdown_timeout_ms`) as long as level 1 is not taken
  down. Replacing the disable by "stop servicing" (finding WDG-F03) was examined and not done: after the last service at
  most about 160 ms of the watchdog remain (250 ms nominal, 230 ms at the fastest LPO, service every 7 cycles = 70 ms),
  while the orderly reset of the IdleHandler (lowest priority) first drains the logger - up to 8 KiB at 115 200 Bd,
  about 5.7 s. Every orderly restart would turn into a watchdog reset (start reason WATCHDOG, counted by the reset-loop
  limit). Open as check point S173-1 of the safety review.
- **D3 — cycle counter outside the protected RAM.** `safetyManager` is defined in `SafetySystem.cpp`
  (`ecu/application`), so `_counter` lies in the general `.bss`. A wild write can at worst delay the watchdog service
  by up to 65 535 cycles — which the watchdog itself then detects (reset) — or bring it forward, which the
  over-service check does not see because the service stays in the safety task. Effect: availability, not a missed
  detection. Moving the object into `libsafeLifecycle` is a small change for PLAT-91 or SYS-69.
- **D4 — first service after `init()`.** The over-service check of the first service requires that nobody serviced
  the watchdog between `enableMcuWatchdog()` and it (section 3.4, step 2). No code does so today; a future service
  during a long start-up (a remedy for D1) must go through `SafeWatchdog` or re-initialise its counter.

---

## 7. Tasks proposed by this document

For the coordinator to carry into `doc/todo.yaml`.

<!-- unit-proposals:start -->
| ID | Title | Done when | Prio |
|---|---|---|---|
| U1 | Unit design of the remaining safety units | every row of the unit register with status `partial` or `missing` is `described` (supervisor reaction logic, device report box, `SafeIo`/`SafeState`/register list, `MemoryProtection`, `IntegrityEccHandler`, `SafeMemory`, `LpitDeadmanCheck`, the ADC console, the rest of `main`) in the module's `doc/` or in this document; `test_unit_design.py` stays green | P2 |
| U2 | Extend the PLAT-91 list of safety units by the fault handlers and the safety-task glue | the path list of PLAT-91 (MISRA 15.5 re-enabled) contains `platforms/s32k1xx/hardFaultHandler`, `platforms/s32k1xx/port/main/src/os/isr/isr_sys.cpp`, `.../isr_wdg.cpp` and `ecu/application/src/systems/SafetySystem.cpp`, or the safety plan states why not; the assembler units are argued separately (MISRA C does not apply to them) | P2 |
| U3 | Start-up time budget of the watchdog as a checked property | the time from the watchdog enable in `main()` to the first service of the safety task is measured on the board (demo) and bounded by a check or an assumption of use with its value (observation D1) | P3 |
<!-- unit-proposals:end -->

---

## 8. Review questions of the architecture (section 11)

This task changes no code, no generator and no configuration: one document and one consistency test.
**Board without an SBC:** the designs use the MCU watchdog only; the SBC watchdog is a device driver (stage 2).
**Different crystal / system clock:** the watchdog and the period check run on the LPO, not on the system clock;
the only clock dependency is the safety task itself, which is what the period check measures. **Other derivative
of the family:** the LPO range comes from `mcudb`; ARMv6-M (S32K11x) has its own handler variant. **S32K3, STM32,
Cortex-R:** `SafeWatchdog` and `CyclePeriodCheck` are family-neutral; a new family provides
`safety::bsp::Watchdog`, the headers of `safeSupervisionCheck` (watchdog control fields, a free-running counter on
an independent clock) and a fault handler with the same contract (walk first, dump, reset). On Cortex-R the
exception model differs (no EXC_RETURN, separate abort modes) — the contract stays, the handler is new code; nothing
in this design blocks that. **Several CAN buses:** not affected (the CAN message-buffer vectors are wrapped per
generated bus). **Several tasks, free priorities:** the design assumes the safety task is the highest application
task (`above_application: true`), which `tasks.yaml` enforces. **New values:** none. **Host test with own
profile:** `test_unit_design.py` checks the document against the tree; the designs cite the unit tests with their
own profiles. **Qt, YAML paths:** not affected. **Driver granularity, platform rule:** not applicable.

## 9. Sources

- Code at `65f3dc9`: `ecu/safety/safeWatchdog`, `platforms/s32k1xx/port/safety/{safeLifecycle,safeSupervisionCheck}`,
  `platforms/s32k1xx/hardFaultHandler`, `platforms/s32k1xx/port/main/src/{main.cpp,os/isr/isr_sys.cpp,os/isr/isr_wdg.cpp}`,
  `ecu/application/src/systems/SafetySystem.cpp`, `ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp`.
- [`sw-architecture.md`](sw-architecture.md) (SYS-65), [`hsi-s32k1xx.md`](hsi-s32k1xx.md) (SYS-66),
  [`fmea.md`](fmea.md) (F9, B4), [`concept.md`](concept.md) sections 5 and 7, [`safety-plan.md`](safety-plan.md)
  (P6-12, N2), `doc/todo.yaml` (PLAT-91 decision of 2026-09-28), `tools/test/tests.yaml`.
- ISO 26262-6:2018 clause 8 (unit design), cited by clause only through `iso26262-gap.md` (not published, available on request) row 6-20.
