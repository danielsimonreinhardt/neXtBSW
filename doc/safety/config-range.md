# neXtBSW — argument over the admissible configuration range (SYS-92, N3)

Baseline: v0.2.0

Status: 2026-09-30, **issue 1 (SYS-92, proposal N3 of [`safety-plan.md`](safety-plan.md))**, written against `dev`
at `65f3dc9`.
Readers: the safety manager, the author of a module manifest or of a check in `tools/nxconfig`, the integrator who
configures neXtBSW for his ECU, and the author of the safety manual (DOC-19).
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**What this document is.** neXtBSW is configurable software: the stage-1 safety mechanisms take times, counts,
tables and memory layouts from the three configuration levels (derivative, board, project). For such software the
standard leaves two routes (6-C.4.5, read through `iso26262-gap.md` (not published, available on request) rows 6-36 and 6-38): verify
the configured software **for the data set actually used**, or verify it **for the admissible range** of the
configuration data. This document states, for every configuration value the stage-1 mechanisms depend on, which
route neXtBSW takes, what range the project has verified and with which evidence, and which verification the
integrator owes for his own data set. It is the argument the tailoring T-10 of the safety plan promises and the
input for the configuration chapter of the safety manual.

**Licence rule.** ISO 26262 is cited by part and clause only.

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Values the stage-1 mechanisms depend on (section 2) | <!-- cfg-counts -->**19** configuration values or value groups: 2 derivative, 2 board, 15 project level (module parameters, `safety`, `interrupts`, `memory`, task set, RTE periods, E2E); plus one code constant that behaves like one (CV-20, deadman time-out) |
| Route chosen (section 1) | **combined**: the *relations* the mechanisms need are verified for the **whole declared range** by checks that run on every configuration (`tools/nxconfig` with YAML path, `static_assert` on the generated values, generator refusals); the *behaviour* of the built software is verified **per data set** — for the project's own profiles by the project, for the integrator's data set by the integrator |
| Range argument holds for | 11 of 19 values: every value whose safety property is a relation that a check enforces (e.g. three watchdog services before the shortest time-out, bounds of the period check, interrupt level of the deadman, safe outputs of every `safe_off` connection) |
| Data-set argument only | 8 of 19 values: the property depends on the integrator's hardware or application (ADC tolerance against the needed accuracy, ROM-check period against the longest flash write, lateness of the safety task under his load, E2E choice per signal, task set) |
| What the project verified beyond the demo | configuration-level tests with own profiles for every timing relation (`nxconfig-watchdog`: other periods, time-outs, LPO range, supervisor values); host tests with own stub profiles (`safety-cycle` 270 ms / 8 cycles, ROM check 2500 ms); target builds of five projects on three derivatives — **all with the default safety values**. On the board only the demo values run |
| Findings (section 4) | G1 no target build with non-default safety values; G2 ROM-check period not checked against the longest flash write; G3 deadman time-out is a code constant; G4 device-bus window only warned; G5 no checked-in project uses a calibration segment |
| Integrator's verification (section 5) | 7 assumptions of use AoU-CFG-1 … AoU-CFG-7 proposed for the safety manual: run the checks and the reference outputs on his data set, repeat the board checks that depend on his values, measure the lateness under his load, choose data-set-dependent values with a rationale, keep the ROM check active behind the neXtBSW bootloader (AoU-CFG-7, DOC-45) |
| New tasks proposed (section 7) | C1 target test profile with non-default safety values, C2 check of the ROM-check period against the flash write time, C3 deadman time-out into the configuration, C4 configuration chapter of the safety manual |
| Verification | **mock/host**: `doc/safety/test/test_config_range.py` checks the value register against the module manifests (every module parameter of the stage-1 safety modules is listed, declared ranges agree), every named check, test and path exists, counts agree; counter-checks with broken copies. No board run for this document |

---

## 1. The route and why

**Range where a check can carry it.** A safety mechanism of stage 1 rarely depends on a single value; it depends on
a **relation** between values (time-out ≥ three service intervals at the fastest LPO; one interval shorter than a
window; deadman level above the RTOS boundary). Such a relation is decidable from the configuration alone. neXtBSW
decides it in two independent places — `tools/nxconfig` (with the YAML path of the value, the configure step stops
on an error) and a `static_assert` on the generated macros in the code that uses them — so **every** configuration
that builds satisfies it. For these values the verification is valid for the admissible range, and the admissible
range is defined as: *declared range of the manifest ∩ every relation the checks enforce*.

**Data set where the property is outside the configuration.** Some properties depend on facts the configuration
does not contain: the accuracy the integrator's function needs, the longest flash write of his application, the
interrupt load of his software. For these the checks can only enforce the declared range; whether a value in that
range is right is verified for the data set — by the project for its own profiles, by the integrator for his.

**Why not the range route for everything.** Target tests over the full range would need hardware per value
combination, and the properties of the second kind cannot be judged without the integrator's facts. Why not the
data-set route for everything: then every change of a value would invalidate the element's evidence; the checks
make most of it re-usable.

**Precondition of the argument.** The checks run on the integrator's configuration: the image is built only
through the build, which runs `nxconfig` and the generators (AoU-26 of
[`tool-classification.md`](tool-classification.md)), and `nxconfig` and the generators are classified and qualified
as there. The configuration files are safety-related data under change control (AoU-10).

---

## 2. Value register

Columns: **Level** — `derivative`, `board` or `project` (architecture section 4); **Declared** — the range of the
manifest or the model; **Relation checked** — what a check enforces over the whole range, and where; **Project
verified** — values other than the demo's exercised by tests of the repository; **Board** — values run on the eval
board; **Route** — `range` (section 1, first kind) or `data set`.

<!-- cfg-values:start -->
| ID | Value | YAML path | Level | Declared | Mechanisms | Relation checked | Project verified | Board | Route |
|---|---|---|---|---|---|---|---|---|---|
| CV-01 | safety task period | `modules.safeLifecycle.task_period_ms` | project | 1 … 1000 ms, default 10 | SM-01, SM-04 | `safety_timing` in `tools/nxconfig/checks.py`: service interval derived; conditions of the period check (window ≥ 1 tick, interval < window, window < wrap-around); `static_assert` in `SafetyManager.cpp`; task and check use the same value (`static_assert` in `SafetySystem.cpp`) | `nxconfig-watchdog`: 1, 3, 5, 20 ms and period 0 refused; `safety-cycle`: 20 ms task detected | 10 ms | range |
| CV-02 | MCU watchdog time-out | `modules.safeLifecycle.watchdog_timeout_ms` | project | 1 … 65535 ms, default 250 | SM-01, SM-02 | `safety_timing`: 3 × service interval ≤ time-out at the fastest LPO; family counter limit (S32K1 511 ms); `static_assert` in `SafetyManager.cpp` | `nxconfig-watchdog`: 4, 40, 50, 66, 228, 229, 400, 511, 512 ms; `safety-cycle` stub profile 270 ms | 250 ms | range |
| CV-03 | tolerated lateness of a cycle start | `modules.safeLifecycle.cycle_lateness_ms` | project | 0 … 999 ms, default 2 | SM-04 | manifest constraint lateness < period; `static_assert` in `SafetyManager.cpp` | `nxconfig-watchdog`: 0, 3, 10 ms | 2 ms, confirmed by fmea B4 (cycle 9 982 … 10 019 µs under load) | data set |
| CV-04 | LPO frequency range | `clock.lpo` | derivative | min ≤ nominal ≤ max (model) | SM-01, SM-04 | missing `clock.lpo` refused at the derivative's `clock` entry; per-mille limits derived and rounded outwards | `nxconfig-watchdog` with its own `mcudb` copy (141 kHz variant) | 113 … 139 kHz (S32K1xx data sheet) | range |
| CV-05 | consecutive safety resets before the held safe state | `modules.safeSupervisor.reset_loop_limit` | project | 1 … 255, default 3 | SM-50 (reset-loop limit) | declared range; `static_assert` ≥ 1 in `platforms/s32k1xx/port/main/src/main.cpp` | `nxconfig-watchdog`: 5; schema limits 0 and 256 refused | 3 | range |
| CV-06 | fault-free time that clears the reset-loop count | `modules.safeSupervisor.reset_loop_clear_ms` | project | 10 … 86 400 000 ms, default 60 000 | SM-50 | `_safe_supervisor` in `checks.py`: not shorter than one safety period; `static_assert` in `main.cpp` | `nxconfig-watchdog`: 10 000 ms | 60 000 ms | range |
| CV-07 | window of the device-bus correlation | `modules.safeSupervisor.device_bus_window_ms` | project | 1 … 60 000 ms, default 200 | SSR-064 (device reports) | `_safe_supervisor`: window in cycles derived; **warning only** below two supervision cycles of the slowest device and above 8 device buses | `nxconfig-watchdog`: 150 and 500 ms | none (host only) | data set |
| CV-08 | read-error limit of the internal supply channel | `modules.safeSystem.adc_read_error_limit` | project | 1 … 100 000 cycles, default 10 | SM-20 | declared range | `bswgen-reference` profile `testproject_adc_reference`: 6 | 10 | data set |
| CV-09 | tolerance band of the ADC reference | `modules.safeSystem.adc_reference_tolerance_mv` | project | 1 … 1000 mV, default 200 | SM-18, SM-19 | declared range; `static_assert` tolerance < nominal in `SafeSystem.cpp` | `testproject_adc_reference`: 350 mV at 5000 mV | 200 mV at 4096 mV | data set |
| CV-10 | nominal ADC reference | `adc_reference_mv` | board | positive integer | SM-18 | required when the ADC or `adc_internal` is used; `#error` in `SafeSystem.cpp` without it | boards with 3300, 4096, 5000 mV | 4096 mV | range |
| CV-11 | ROM-check period | `modules.safeMemory.rom_check_period_ms` | project | 0 … 86 400 000 ms, default 60 000 | SM-14 | declared range only; **no check** against the longest flash write the manifest requires (G2) | host stub 2500 ms, period 0 (`rom-check`) | first pass only | data set |
| CV-12 | outputs switched off in the safe state | `connect.<name>.safe_off` (module manifest) with the board's `devices[].connect` | board | boolean per `digital_output` connection | SM-17, SM-23, SM-24 | bswgen refuses a `safe_off` output that is not a used dio output; the table is checked in the linked image against an independent expectation (SSR-080); one power-stage adapter per output (`static_assert` in `Integration.cpp`) | `safe-outputs-table`, `-charge`, `-bt148`; reference profiles with 0, 1 and 2 outputs | demo table | range |
| CV-13 | pins of the safety roles | `safety.safe_state_output`, `safety.locked_pin`, `safety.test_pin` | project | pin names | SM-21, SM-22, SM-25 | `_safety_roles` in `checks.py` (errors at each path, warning for an application writer, AoU-30); bswgen refuses a locked pin used otherwise; `static_assert` in `SafeIoRegisterList.cpp` | `bswgen-safe-io`, `safe-io` | demo pins | range |
| CV-14 | highest interrupt level that may call the RTOS | `interrupts.max_syscall_level` | project | optional, default per core | SM-06 (deadman one level above), SM-04 | `_interrupts` in `checks.py`: 0 on ARMv6-M, 1 … 2^bits − 1 otherwise, rank fit; `static_assert`s in `main.cpp`; `elfcheck` compares the value in the image | projects with 5 and with 0 (S32K116) | 5 | range |
| CV-15 | reaction to an invalid calibration segment, calibration profile | `memory.calibration.on_invalid`, `memory.calibration.profile` | project | `refuse`/`defaults`/`warn`; `development`/`production` | SM-14 (segment as ROM range), SYS-89/SYS-90 | `tools/nxconfig/calibration.py`: `refuse` required when a parameter has `safety` other than `qm`; no XCP in `production`; `defaults` only in development | `target-calibration` (derived profiles on S32K144 and S32K148), `nxconfig-calibration` | derived demo project, refuse/development (SYS-89 board test) | range |
| CV-16 | memory layout (flash, RAM, regions of the image) | `memory` | derivative | per derivative and project | SM-10 … SM-14 | `_memory` in `checks.py` (derivative) and `tools/nxconfig/memory.py` (project); MPU `static_assert`s in `mpu.h`; linked-image layout check (SSR-083) | five derivatives in `mcudb`, target builds on S32K144, S32K148, S32K116 | S32K144 | range |
| CV-17 | E2E profile and data ID per safety-relevant signal | `can.qualifiers.<message>.e2e` (`rte.yaml`) | project | P01/P02/P11, `data_id`, `max_delta_counter` | SM-31 | rtegen: width of `data_id`, nibble mode 12 bit, P02 list, `max_delta_counter` against the counter range | `e2e-ref`, `e2e-host`, `e2e-mutations` | demo: P01 0x0210, P11 0x0110 | data set |
| CV-18 | task set, periods and priorities | `ecu/asyncCoreConfiguration/tasks.yaml`, `rte.yaml` | project | ordering keys, periods | SM-04, SM-05 | safety task highest (`above_application`), schedule analysis `tools/schedana` (TD1 check) | `schedana` tests | demo task set | data set |
| CV-19 | RTE periods as deadlines | `rte.yaml` runnables | project | per runnable | SM-05 | the deadline **is** the configured period; rtegen checks the age deadlines | `rtegen-reference`, `rtegen-age` | demo | data set |
| CV-20 | halt-deadman time-out (code constant) | — (`LpitHaltDeadman::TIMEOUT_MS` = 10 ms) | code | fixed | SM-06 | `static_assert`: more than one tick, LPIT clock a whole kHz | host test `lpit-halt-deadman` | 10 ms | range |
<!-- cfg-values:end -->

Remarks:

- CV-20 is not a configuration value; it is listed because the deadman's reaction time depends on it and on the
  clock plan (`NX_CLOCK_LPIT_HZ`, derived), and a project might need another value (finding G3). It is not counted
  among the 19 values of section 0.
- The **ROM-check areas** are not a configuration value: they come from the linker template and the image check;
  the calibration region is excluded and checked by its own record (SYS-91).
- `safeLifecycle`, `safeSupervisor`, `safeSystem` and `safeMemory` are the stage-1 modules with project parameters;
  the test checks that every one of their parameters is a row here with the declared range of its manifest.

---

## 3. The argument per route

**Range (11 values: CV-01, CV-02, CV-04, CV-05, CV-06, CV-10, CV-12 … CV-16, and CV-20 as code constant).** For each
of them: the safety property is a relation; a check enforces it on every configuration with the YAML path, and a
second check in the code (`static_assert`, `#error`, image check) enforces it on the generated values; the
configuration-level tests exercise values across the declared range including both sides of every limit
(`nxconfig-watchdog` tests 228/229 ms against the shortest time-out, 511/512 ms against the counter). Conclusion: a
configuration inside the admissible range keeps the property; one outside it does not build.
**What the range argument does not cover:** that the software *behaves* as designed at every value — the host tests
exercise the mechanisms at the demo values and at one or two stub profiles (G1), and the board only at the demo
values. Behaviour that scales with a value (a longer period means a longer detection time) is computed in
[`concept.md`](concept.md) section 7 as a formula of the values, not measured per value.

**Data set (8 values: CV-03, CV-07, CV-08, CV-09, CV-11, CV-17, CV-18, CV-19).** The project verifies its own data
sets (demo, charge, buildtest, S32K116 minimal) and states the rationale of the defaults in the manifests. For the
integrator's data set the verification is his (section 5); the checks only guarantee the declared range and, where
it exists, a warning (CV-07).

---

## 4. Findings

<!-- cfg-findings:start -->
| ID | Finding | Consequence | Proposal |
|---|---|---|---|
| G1 | No target build and no generator reference profile uses safety values other than the defaults (10/250/2 ms, 3/60 000/200 ms, 60 000 ms); non-default values exist only in configuration-level tests and host stubs | the range argument rests on the checks; that the generated code builds and runs at other values is shown only for the stubs | C1 |
| G2 | `rom_check_period_ms` must be longer than the longest flash write of the ECU (manifest text), but no check enforces it | a too-short period turns a long EEPROM write into `ROM_CHECK_FAILURE` (S1 held) — availability, not safety; the integrator must know it | C2, AoU-CFG-4 |
| G3 | the halt-deadman time-out is a code constant (10 ms) | a project whose fault tolerance time needs a shorter or allows a longer standstill cannot configure it | C3 |
| G4 | the device-bus window is only warned about when it is shorter than two supervision cycles | a configuration with a too-short window builds; the correlation then misses faults | AoU-CFG-5 |
| G5 | no checked-in project has a calibration segment; the production profile runs only in `target-calibration` | the calibration route is verified by derived profiles, not by a maintained project | C1 |
<!-- cfg-findings:end -->

---

## 5. Verification the integrator owes for his data set

Proposed for the safety manual; numbered `AoU-CFG-n` so that they do not collide with numbers proposed in parallel.
They refine AoU-10 and AoU-26.

<!-- cfg-aou:start -->
| ID | Assumption of use | Values |
|---|---|---|
| AoU-CFG-1 | The integrator builds his image only through the build (so every check of section 2 runs on his configuration), treats every warning of `nxconfig` on a value of section 2 as a finding he resolves or justifies in writing, and keeps his configuration files under change control (AoU-10, AoU-26). | all |
| AoU-CFG-2 | For every value of section 2 that differs from the defaults he repeats, on his ECU, the board checks of [`fmea.md`](fmea.md) section 8 that depend on it (watchdog reset, period violation, reset-loop limit, ROM check, safe outputs) and records the result with the configuration it was run with. | CV-01 … CV-06, CV-11, CV-12 |
| AoU-CFG-3 | He measures the start distance of the safety cycle under his worst-case load (as board check B4) and sets `cycle_lateness_ms` from that measurement with margin; the default 2 ms is the demo's measured value, not a property of neXtBSW. | CV-03, CV-18 |
| AoU-CFG-4 | He sets `rom_check_period_ms` longer than the longest flash write of his application (EEPROM emulation, NvM), and not longer than the latent-fault interval his analysis allows (AoU-25). | CV-11 |
| AoU-CFG-5 | He chooses the data-set values from the needs of his function and records the rationale: ADC reference tolerance against the needed accuracy (AoU-24), read-error limit and device-bus window against his fault tolerance time, E2E profile and data ID per signal (AoU-11). | CV-07, CV-08, CV-09, CV-17 |
| AoU-CFG-6 | He confirms that the halt-deadman time-out of 10 ms and the other fixed times of stage 1 fit his fault tolerance times (AoU-05), or requests a change (C3). | CV-20 |
| AoU-CFG-7 | If the neXtBSW bootloader starts his application image, he keeps the ROM check (SM-14) active in that image: built only through the build, so that the reference CRC is written and the check is compiled in (AoU-CFG-1), and with a re-check period (CV-11, AoU-CFG-4) or a restart interval (AoU-25) that covers his latent-fault interval. Reason (safety review BL38-P1, 2026-10-03, F5): since BL-36/BL-38 the bootloader checks the image again immediately before the jump without a second full CRC, so an image byte that changes between the CRC at start and the jump without a flash command (debugger, bit fault; with a held extended session the window can last minutes) is left to the flash ECC and SM-14. | CV-11 |
<!-- cfg-aou:end -->

---

## 6. Result for the safety manual

The safety manual does not exist yet (DOC-19). Proposal C4: its configuration chapter states the route of
section 1 in two sentences, carries the value register of section 2 (or references it), and carries AoU-CFG-1 …
AoU-CFG-6 with their final numbers. The consistency test checks the reference as soon as the safety manual exists.

---

## 7. Tasks proposed by this document

<!-- cfg-proposals:start -->
| ID | Title | Done when | Prio |
|---|---|---|---|
| C1 | Target test profile with non-default safety values | a test project (or a derived profile as in `target-calibration`) builds for the target with values other than the defaults for every range value of section 2 (e.g. 20 ms period, 400 ms time-out, reset-loop limit 5, ROM-check period 2 s, a calibration segment), the reference outputs are checked in, and the host tests of the safety cycle run with its generated configuration | P2 |
| C2 | ROM-check period against the flash write time | `tools/nxconfig` warns or refuses when `rom_check_period_ms` (other than 0) is shorter than the longest flash write the configuration implies (EEPROM emulation in use: backup-sector wait), with the YAML path; test with an own profile | P3 |
| C3 | Halt-deadman time-out into the configuration | the time-out is a project value of the module that owns the deadman, with range, derived ticks and the existing `static_assert`s; default 10 ms; reference outputs unchanged for the defaults | P3 |
| C4 | Configuration chapter of the safety manual | `doc/safety/safety-manual.md` (DOC-19) states the route, references `config-range.md` and carries AoU-CFG-1 … AoU-CFG-6 with final numbers; `test_config_range.py` checks the reference | P2 |
<!-- cfg-proposals:end -->

---

## 8. Review questions of the architecture (section 11)

This task changes no code, generator or configuration. **Board without an SBC:** no value of section 2 assumes an
SBC. **Different crystal:** the timing values run on the LPO or are relations to the safety task, whose own clock is
what the period check measures; CV-20 depends on the LPIT clock through a derived value. **Other derivative,
S32K3/STM32/Cortex-R:** CV-04 and CV-16 are derivative level; CV-02's upper bound is the family's watchdog counter,
which `nxconfig` takes per family; the register is the list another family has to fill. **Several CAN buses:**
CV-17 per signal and bus. **Several tasks:** CV-18. **Exactly one level per value:** every row names one level;
none is set in two. **Host test with own profile:** `test_config_range.py` checks this document against the
manifests. **Qt, YAML paths:** every check named reports with the YAML path. **Driver granularity, platform rule:**
not applicable.

## 9. Sources

- Module manifests `platforms/s32k1xx/port/safety/{safeLifecycle,safeSupervisor,safeSystem,safeMemory}/module.yaml`,
  `tools/nxconfig/{checks.py,calibration.py,memory.py,model.py}`, `tools/bswgen`, `tools/rtegen`,
  `tools/test/tests.yaml` (`nxconfig-watchdog`, `safety-cycle`, `rom-check`, `bswgen-reference`, `target-*`), code at
  `65f3dc9`.
- [`concept.md`](concept.md) (AoU-05, AoU-10, AoU-11, section 7), [`fmea.md`](fmea.md) (B4, AoU-24, AoU-25),
  [`tool-classification.md`](tool-classification.md) (AoU-26), [`safety-plan.md`](safety-plan.md) (T-10, P6-20,
  P6-21, N3).
- ISO 26262-6:2018 annex C (C.4.1 … C.4.5) and clause 11.4.4, cited by clause only through
  `iso26262-gap.md` (not published, available on request) rows 6-36 … 6-39.
