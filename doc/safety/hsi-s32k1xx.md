# neXtBSW hardware-software interface of the stage-1 safety mechanisms — s32k1xx (SYS-66)

Working state: dev at 6812a0b4 (after v0.2.0)

Status: 2026-09-28, **issue 1 (SYS-66)**, written against `dev` at `ae92e89`. Task no. 8 of
`iso26262-gap.md` (not published, available on request) section 6 (requirement 6-04 of its section 2.2: the hardware-software interface
is refined and every safety-related dependency described).
Readers: the integrator (safety manual DOC-19), the agents of SYS-55 (register list), SYS-73 (ECC injection),
SYS-85/SYS-86 (hardware paths) and SYS-21 (freedom from interference), and whoever writes the HSI of the next family.
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**Update SYS-125 (2026-09-30, finding H9, anomaly AN-066).** Decision of the safety responsible 2026-09-30: the family
port switches the PMC low-voltage **detect** reset on instead of leaving the gap to AoU-09. `boardInit()` writes
`PMC_LVDSC1 = LVDRE` (reset on, interrupt off, no acknowledge) in every start
(`platforms/s32k1xx/port/main/include/pmc/LowVoltageDetectReset.h`); a VDD falling below VLVD (2.80 … 3.00 V) now
resets the MCU instead of only a VDD below VLVR (2.50 … 2.70 V). Rows changed: section 2 (PMC), 4.8 (`LVDSC1`),
5 (LVD/LVW vector), 8 (H9, now partly closed) and 9; the mechanism is catalogued as **SM-59** in `concept.md` 6.4
(not credited) and analysed in `fmea.md`. What remains of H9: the bit is written at each start but **not re-checked
cyclically** (it is not in the SafeIo list, 4.9). Host verified with a register model; board proof open.

**Update SYS-153 (2026-10-01, rest of H9).** `PMC_LVDSC1` is now an entry of the SafeIo list (4.9, class SUPERVISION,
mask `0x30`, expected `0x10`, 8-bit access; the engine got an access width per entry). A cleared `LVDRE` or a set
`LVDIE` is seen within one safety cycle (S1). H9 closed except VLPR/VLPS; the bootloader sets LVDRE itself since
BL-34. Host verified; board proof open.

**Update SYS-171 (2026-10-02, safety review check point SYS69-S2, findings SFP-F02 … SFP-F04 of
`reviews/code-inspection/INS-SFP-1.md` (not published, available on request)).** Section 4.4: `RGD0_WORD2` is now
really compared every cycle (it was claimed but never read), `WORD2` of every descriptor is compared in all fields of
the bus masters 0 … 3 (before only M0 … M2), the rows name descriptors 1 … 7 (SYS-97 guard, part of SFP-F12), and the
statement that the descriptors keep M2/M3 out of the protected RAM is corrected: descriptor 0 grants them rwx (H10,
section 8, section 0). Host verified with a register model and 15 mutants; board check B17 (fmea section 8) open.

**Update DOC-59 (2026-10-04, re-check of CM-02, decision BRD a).** Board check B17 (2026-10-02, `fmea.md` 8.2)
reads `RGD0_WORD3` = 0: the valid bit of descriptor 0 is **not** set after `init()`. Section 4.4 and H10 carry this
as a caveat: the M2/M3 rights of descriptor 0 may not be in effect; until the reference manual settles it (S171-3,
SYS-171) this document keeps the unfavourable reading, and the restriction of descriptor 0 stays SYS-21.

This document lists, for the family **s32k1xx**, every peripheral, register class, clock, interrupt and pin that a
**stage-1** mechanism of [`concept.md`](concept.md) / [`fmea.md`](fmea.md) depends on, with the configuration the
mechanism expects, who writes that configuration, what re-checks it, and where each value comes from (derivative
database `mcudb`, board, project, or a constant of the family port). It is derived from `mcudb/s32k1xx/*.yaml`, from
fmea section 5.2 (register classes of SM-21/SM-22) and from the code of `platforms/s32k1xx`; register facts that the
code relies on were read again in the S32K1xx Reference Manual Rev. 13 in the repository ([RM13],
`doc/hardware/s32k1xx/S32K-RM.pdf`) where section 11 says so.

**Scope.** Stage 1 only, as in fmea.md (user, `abn-sys19`): mechanisms whose detection runs on the MCU and uses only
the MCU, its peripherals and the configuration. Device-dependent mechanisms (SM-33, SM-40 … SM-43, SM-52) have their
HSI in the device packages (SYS-28). The hardware paths of [`hardware-mechanisms.md`](hardware-mechanisms.md) are not
used today; section 9 lists them only so that their registers are not mistaken for unused.

**Not a copy of the reference manual.** The tables name registers and fields and the value neXtBSW expects; for the
meaning of a field see [RM13]. Where a field is written by the OpenBSW driver in `external/openbsw` (never changed in
the submodule), the table says so.

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Peripherals the stage-1 mechanisms depend on | **20**: WDOG, LPTMR0, LPIT0, SIM, PCC, SCG, PMC (by its reset default), RCM, MPU, ERM, FTFC, CRC, ADC0, PORT, GPIO, FTM (of the PWM group), FlexCAN (per bus), NVIC, SCB (SHCSR, AIRCR, DFSR, DEMCR), SysTick / DWT (time bases of the OS and the system time) — section 2 |
| Clocks | **LPO128K** (WDOG), **LPO1K** (LPTMR0), **LPIT functional clock** (PCC_LPIT, demo SIRCDIV2 8 MHz), **system/core clock** (SCG → FTM counter, SysTick, DWT, CRC and the safety task itself), **ADC clock** (PCC_ADC0) — section 3 |
| Interrupts and exceptions | HardFault (exception 3, all faults escalate to it), WDOG warning (IRQ 22), LPIT0 channel 1 (IRQ 49 on S32K14x, the shared LPIT IRQ 20 on S32K11x), SysTick/PendSV/SVC of the RTOS; **expected disabled**: BusFault/UsageFault/MemManage enables, ERM and FTFC fault IRQs, LPTMR IRQ — section 5 |
| Pins | none of their own for the supervision peripherals; the **pins of the safe outputs** (PWM group, `safe_off` outputs), the role pins `safety.locked_pin`, `safety.test_pin` and `safety.safe_state_output`, all from the board and project — section 6 |
| Where the values come from | derivative (`mcudb`: MPU regions, LPO range, NVIC bits, ERM channels, ADC internal channels), board (pins, active level, `adc_reference_mv`, oscillator), project (clock plan, PWM group, safety roles, `modules.safe*` parameters), family constants (register masks, deadman channel, watchdog clock) — no board value lives in reusable code |
| Re-checked at run time | WDOG control, LPTMR0, LPIT channel 1 + its NVIC entry, MPU descriptors and enable, the register list of SafeIo (pin control, GPIO direction, clock gates, FTM configuration, `PMC_LVDSC1` since SYS-153), CRC unit (known-answer test) |
| **Not re-checked, and relied on** | findings **H1 … H10** (section 8): the NVIC entry of the watchdog warning IRQ (H1), the fault enables in SCB_SHCSR (H2), disabled ERM/FTFC fault interrupts (H3), the LPIT functional clock (H4), the system clock and with it the FTM dead time in nanoseconds (H5), FTM OUTMASK/SWOCTRL and GPIO PDOR (H6, known: SYS-30), SIM_PLATCGC (H7), SIM_CHIPCTL (H8), PMC low-voltage detect reset (H9: since SYS-125 switched **on** at every start and re-checked cyclically since SYS-153; remaining: LVD off in VLPR/VLPS), descriptor 0 of the MPU grants the DMA/ENET bus masters full access, so the MPU does not keep them out of the protected RAM (H10, open point N3; no such user today) |
| Other derivatives of the family | the table holds for S32K116 … S32K148; differences (core, NVIC bits, MPU regions, ERM channels, IRQ numbers, time base of the system time) are in section 7 and all come from `mcudb` or the device header, not from a board |
| Review | author review against code and [RM13] recorded in section 10; **independent review (I1) open** — it needs a second person (gap analysis Q1) |

---

## 1. Reading rules

### 1.1 Columns

Each table in section 4 has one row per register or field group:

| Column | Meaning |
|---|---|
| **Register / field** | peripheral register and the fields that matter; `x` = instance or index from the configuration |
| **Expected** | the value or relation the mechanism assumes while the ECU runs |
| **Written by** | the code that sets it (file under `platforms/s32k1xx` unless stated), or "reset value" if nobody writes it |
| **Re-checked by** | the mechanism or check that detects a deviation at run time, and how often; "—" = nothing |
| **Value from** | `family` = constant of the family port (register facts, fixed choices of the mechanism); `mcudb` = derivative database; `board` = `boards/<board>/board.yaml`; `project` = `projects/<project>/project.yaml` (including module parameters); `derived` = computed by `tools/bswgen` or `tools/nxconfig` from those |

### 1.2 Status words for the check

| Word | Meaning |
|---|---|
| **cyclic** | compared with the expected value in every safety cycle (nominal 10 ms, guaranteed 17 ms while the task runs, fmea section 2) or at the stated period |
| **start** | compared once after it was written |
| **self-healing** | rewritten before each use, so a drifted value does not survive the next use |
| **fail-safe by access** | a drift makes the next access of the mechanism fault (bus fault → HardFault → SM-17), which is a safe reaction |
| **not checked** | a drift is not detected; the row names the consequence — these rows are the findings of section 8 |

---

## 2. Overview: which mechanism depends on which peripheral

`●` = the mechanism's detection or reaction needs the peripheral; `○` = it needs it only as a time base, a clock or a
report path. Mechanism names as in concept section 6.

| Peripheral | SM-01/02/03/07 watchdog | SM-04 sequence + period | SM-05 RTE timing | SM-06 deadman | SM-10…13 MPU | SM-14 ROM check | SM-15/16 ECC | SM-17 fault handlers | SM-18/19/20 ADC, supplies | SM-21/22 register lock/check | SM-23/24/25 safe outputs | SM-30 qualifier | SM-31/32 E2E, CAN | SM-50 start reason, reset loop | SM-51 image check |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WDOG | ● | | | | | | | ○ (warning IRQ) | | | | | | ● (fast test) | |
| LPTMR0 | | ● | | | | | | | | | | | | | |
| LPIT0 | | | ○ (ARMv6-M system time, ch 0) | ● (ch 1) | | | | | | | | | | | |
| SIM | ○ (LPO) | ● (LPOCLKS) | | | ○ (PLATCGC) | | ○ (PLATCGC) | | ● (CHIPCTL) | | | | | | |
| PCC | | ● (LPTMR0) | | ● (LPIT) | | ○ (CRC) | | | ○ (ADC0) | ● (PORT, FTM) | ● (gate of each entry) | ○ | ○ (FlexCAN) | | |
| SCG | ○ | ○ (the task's clock) | ○ | ○ (SIRC) | | ○ | | | | | ○ (FTM counts the system clock) | | ○ | ○ (LOC/LOL resets) | |
| PMC | | | | | | | | | ○ (LVD reset on, SYS-125) | | | | | ○ (LVD in RCM_SRS) | |
| RCM | | | | | | | | | | | | | | ● | |
| MPU | | | | | ● | | | ○ (bus fault) | | | | | | | |
| ERM | | | | | | | ● | | | | | | | | |
| FTFC | | | | | | ● (CCIF) | ● (FERSTAT) | | | | | | | | ○ (flash reads) |
| CRC | | | | | | ● | | | | | | | | | |
| ADC0 | | | | | | | | | ● | | | ● (board channels, any ADC) | | | |
| PORT | | | | | | | | | | ● | ● | | | | |
| GPIO | | | | | | | | | | ● | ● | | | | |
| FTM (PWM group) | | | | ● (inhibit) | | | | ● (walk) | | ● | ● | | | | |
| FlexCAN | | | | | | | | | | | | | ● | | |
| NVIC | ● (IRQ 22) | | | ● (LPIT vector) | | | ○ (IRQs off) | ● | | | | | | | |
| SCB (SHCSR, AIRCR, DFSR, DEMCR) | | | ○ (DEMCR on ARMv7-M) | ● (DFSR) | | | | ● (SHCSR, AIRCR) | | | ● (AIRCR reset after S1) | | | | |
| SysTick / DWT | | ○ (supervised) | ● | | | | | | | | | | | | |
| SRAM (no-init) | | | | | | | ○ (ECC) | ● (dump) | | | | | | ● (record) | |

---

## 3. Clocks

| Clock | Source and path | Used by | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|---|---|
| **LPO128K** | PMC low-power oscillator, always on, no divider in SCG | WDOG counter (`CS[CLK] = 1`) | running; frequency within the data-sheet range 113 … 139 kHz | nobody (always on) | a stopped or drifted LPO is seen by the SM-04 period window against the core-clocked safety task (fmea SM-01 "SM-04 also covers a stopped LPO") | `mcudb` `clock.lpo` |
| **LPO1K** | LPO128K ÷ 128, gated by `SIM_LPOCLKS[LPO1KCLKEN]` | LPTMR0 time counter (`PSR[PCS] = 01`, prescaler bypassed) | `LPO1KCLKEN = 1` | reset value (not written) | **cyclic**, `checkCycleTimer()` (field LPO1K, kind 3 of `SAFETY_CYCLE_PERIOD_VIOLATION`) | `family` |
| **LPIT functional clock** | `PCC_LPIT[PCS]` → one of SOSCDIV2/SIRCDIV2/FIRCDIV2/SPLLDIV2 (SCG `xxxDIV` dividers); `NX_CLOCK_LPIT_HZ` | LPIT channel 1 (deadman time-out = `TIMEOUT_TICKS` = `NX_CLOCK_LPIT_HZ / 1000 × 10`); on ARMv6-M also channel 0 (system time) | the frequency the build assumed; demo SIRCDIV2 = 8 MHz → `TVAL1 = 79 999` (board 2026-09-22: `0x1387F`) | `clockConfig.cpp` / generated clock plan | **not checked** (H4): `TVAL` is checked in ticks, not the tick length | `project` (clock plan), default `mcudb` `clock.pcc.LPIT` |
| **System / core clock** | SCG `RCCR` → SOSC or SPLL or FIRC; `DIVCORE`, `DIVBUS`, `DIVSLOW` | the safety task and the OS tick (SysTick), DWT system time (ARMv7-M), FTM counter of the PWM group (`SC[CLKS] = 01`, system clock), CRC, all bus accesses | the clock plan of the project (demo: SOSC 40 MHz → SPLL, core 80 MHz) | `configurPll()` from `boardInit()` | **not checked as a register** (H5); its **effect** on the safety task's period is SM-04 (period part, against LPO1K); loss of SOSC or SPLL lock is a hardware reset (LOC/LOL, below) | `board` (oscillator `clock.sosc_hz`), `project` (`clock.system_hz`), limits `mcudb` `clock.*` |
| **SOSC / SPLL clock monitors** | `SCG_SOSCCSR[SOSCCM, SOSCCMRE]`, `SCG_SPLLCSR[SPLLCM, SPLLCMRE]` | loss-of-clock / loss-of-lock **reset** ([RM13] 25.2.2.4, 25.2.2.5); `RCM_SRS[LOC]` / `[LOL]` afterwards | `CM = 1`, `CMRE = 1` for every oscillator and PLL the clock plan uses | `soscMon()`, `spllMon()` in `platforms/s32k1xx/bsp/bspClock/src/clockConfig.cpp` | **not checked** (H5). A hardware mechanism of the MCU, **not credited** in stage 1 | `family` (always on when the source is used) |
| **ADC clock** | `PCC_ADC0[PCS]` + divider | bandgap and supply conversions (SM-18…20), board channels (SM-30) | conversion clock ≤ 50 MHz (mcudb `max_hz`) | clock plan | **fail-safe by access** for a gated clock (read failures → SM-19/SM-20 limit); frequency not checked (no effect on a ratio measurement) | `project`, `mcudb` `instances.ADC0.clock` |
| **Peripheral clock gates** | `PCC_x[CGC]` | every peripheral above | 1 for every peripheral a mechanism uses | `configurPll()` → `enablePeripheralClocks()` (OpenBSW) and `nxEnablePeripheralClocks()` | FTM and PORT of the safe outputs: **cyclic** (class CLOCK_GATE of SafeIo); LPTMR0: **cyclic** (`checkCycleTimer`); others: **fail-safe by access** (a gated peripheral bus-faults on access, SM-17) | `derived` (bswgen: every used peripheral; `S32K1_INFRASTRUCTURE_GATES` for FTFC, CRC, PORTx, …) |

The two time bases that do not depend on the clock plan are LPO128K (watchdog) and LPO1K (period check). The deadman
(SM-06) runs on an SCG-derived clock chosen by the project: it is independent of the **core** being halted, not of the
clock plan (H4).

---

## 4. Per mechanism

### 4.1 SM-01, SM-02, SM-03, SM-07 — MCU watchdog (WDOG)

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| `WDOG_CS[EN]` | 1 | OpenBSW `Watchdog::enableWatchdog()` (`safeBspMcuWatchdog`), called from `SafeWatchdog::enableMcuWatchdog()` (`ecu/safety/safeWatchdog`) after the fast test | **cyclic** every 7 safety cycles, `checkWatchdogConfiguration()` (SM-02) | `family` |
| `WDOG_CS[CLK]` | 1 = LPO clock | same | SM-02 | `family` |
| `WDOG_TOVAL` | `128 000 / 1000 × watchdog_timeout_ms` = 32 000 for 250 ms (the driver computes with the **nominal** LPO 128 kHz; real time-out 230 … 283 ms over the LPO range) | same | SM-02 (low 16 bits) | `project` `modules.safeLifecycle.watchdog_timeout_ms`; relation to the service interval checked by `nxconfig` (`safety_timing`) |
| `WDOG_CS[INT]` | 1 (warning interrupt before the reset) | same, with `interruptActive = true` | SM-02 via `checkWatchdogControl()` (context bit 2) | `family` |
| `WDOG_CS[WIN]`, `WDOG_WIN` | 0 (no window) | same | SM-02 (`WIN`, bit 4); window register not read | `family` |
| `WDOG_CS[PRES]` | 0 (no ÷256 prescaler) | same | SM-02 (bit 8) | `family` |
| `WDOG_CS[UPDATE]` | 1 (reconfigurable — required by the service path of the driver) | same | SM-02 (bit 16): a change is **detected**, not prevented (fmea SM-02) | `family` |
| `WDOG_CS[CMD32EN]`, `[STOP]`, `[WAIT]` | 1, 1, 1 (32-bit command words; counts in STOP and WAIT modes) | same | not checked (no effect on detection in RUN) | `family` |
| `WDOG_CS[DBG]` | 0 (the watchdog **stops** in a debugger halt — by design, the halt is SM-06's) | same (not set) | not checked | `family` |
| `WDOG_CS[TST]` | 2 or 3 during the two fast-test resets, then kept as written by the driver | `executeFastTest()` from `main()` (SM-07) | SM-07 at every start; the start-up code reads `TST` to tell a fast-test reset from a real one (`boardInit()`, SM-50) | `family` |
| `WDOG_CNT` | refresh sequence only from `Watchdog::serviceWatchdog()` | `SafeWatchdog::cyclic()` every 7 cycles | SM-03 counts calls of the service function; a direct write to `CNT` is not seen (fmea SM-03) | `family` / `derived` (service interval from `task_period_ms` and the fastest LPO, SYS-53) |
| NVIC `ISER` bit of `WDOG_EWM_IRQn` (22) | enabled | OpenBSW `enableWatchdog()` (direct `NVIC->ISER` write) | **not checked** (H1) | `family` |
| NVIC priority of `WDOG_EWM_IRQn` | reset value 0 (most urgent) | nobody | **not checked** (H1) | reset value |
| vector 38 (`Safe_WDOG_EWM_IRQHandler`, `os/isr/isr_wdg.cpp`) | naked handler: safe outputs walk first, R0–R3 only | startup `startUp.S`, bswgen `FAMILY_IRQ_HANDLERS` (S32K11x: `WDOG_IRQn`, same number) | `safe-outputs-order` (disassembly) at build/test time | `family` |

### 4.2 SM-04 (period part) — LPTMR0 against LPO1K

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| `PCC_LPTMR0[CGC]` | 1 | clock plan | **cyclic** `checkCycleTimer()` (checked first: without it an LPTMR access would bus-fault) | `family` |
| `LPTMR0_CSR[TEN]` | 1 | `startCycleTimer()` (`safeSupervisionCheck/LptmrCycleTimer.cpp`, start sequence [RM13] 49.5.1) | **cyclic** (kind 3, field ENABLE) | `family` |
| `LPTMR0_CSR[TMS, TFC, TIE, TDRE]` | 0, 1, 0, 0 (time counter, free running, **no interrupt**, no DMA) | same | **cyclic** (field MODE) | `family` |
| `LPTMR0_PSR[PCS, PBYP, PRESCALE]` | 01 (LPO1K), 1, 0 | same | **cyclic** (field PRESCALER) | `family` |
| `LPTMR0_CMR` | 0 (compare unused) | same | not checked (no effect with `TIE = 0` and `TFC = 1`) | `family` |
| `LPTMR0_CNR` | free-running 16-bit counter, read by writing CNR first (latch) | `readCycleTimer()` | a stopped counter: the 7-cycle window (kind 2) | `family`; bounds `derived` from `task_period_ms`, `cycle_lateness_ms`, `mcudb` `clock.lpo` |
| `SIM_LPOCLKS[LPO1KCLKEN]` | 1 | reset value | **cyclic** (field LPO1K) | `family` |
| NVIC `LPTMR0_IRQn` | disabled (no hardware deadline yet — proposed T1b) | nobody | not needed while `TIE = 0` (checked) | — |

### 4.3 SM-06 — halt / stall deadman (LPIT0 channel 1) and DFSR

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| `PCC_LPIT[CGC]`, `[PCS]` | 1, the clock source of `NX_CLOCK_LPIT_HZ` | clock plan | CGC: **fail-safe by access**; PCS: **not checked** (H4) | `project`, default `mcudb` |
| `LPIT0_MCR[M_CEN]` | 1 | `initSystemTimer()` (keeps it), `LpitHaltDeadman::init()` | **cyclic** `checkLpitDeadman()` from the safety task (field 0x01) | `family` |
| `LPIT0_MCR[DBG_EN]` | 1 (keeps counting while the core is halted — the point of the mechanism) | `LpitHaltDeadman::init()` (`port/main/src/integration/LpitHaltDeadman.h`) | **cyclic** (0x02) | `family` |
| `LPIT0_MIER` bit 1 | 1 | same | **cyclic** (0x04) | `family` |
| `LPIT0_TCTRL1` | 0 except `T_EN` (32-bit periodic counter, no trigger, no chaining) | same | **cyclic** (0x08); `T_EN` excluded on purpose (retrigger toggles it) and healed by `SETTEN` every 1 ms | `family` |
| `LPIT0_TVAL1` | `TIMEOUT_TICKS − 1` (10 ms; demo 79 999) | same | **cyclic** (0x10) | `family` (10 ms) × `project` (LPIT clock) |
| NVIC enable of `LPIT0_Ch1_IRQn` (49; S32K11x: shared `LPIT_IRQn` 20) | enabled | `LpitHaltDeadman::init()` | **cyclic** (0x20) | `family`; IRQ alias bswgen `FAMILY_IRQ_ALIASES` |
| NVIC priority of the same | `NX_IRQ_MAX_SYSCALL_LEVEL − 1` (more urgent than every RTOS critical section; 0 on ARMv6-M) | `SYS_SetPriority()` in `init()` | **cyclic** (0x40) | `derived` from the RTOS boundary and `mcudb` `nvic_priority_bits` |
| `LPIT0_MSR` bit 1 | cleared at init; set by an expiry, taken by the handler | `init()`, handler | — | — |
| `SCB_DFSR` (0xE000ED30) bits 0–4 | read and cleared (write 1) by the guard every poll; debug events of connect/reset discarded at start | `CortexMDebugEvents::take()` (`DebugHaltGuardSystem.h`) | secondary source only (the J-Link clears it, IO-32) | architecture |
| FTM `SWOCTRL` of the PWM group | written as inhibit (`CHnOC` of the group, `CHnOCV = 0`) by the deadman reaction | `DebugHaltGuardSystem.h` `inhibit()` | see 4.10 | `derived` (PWM group) |

The expected configuration of the re-check comes from the owner of the deadman (`EXPECTED_CONFIGURATION` in
`LpitHaltDeadman.h`, strong `deadmanExpectedConfiguration()` since SYS-48, defined out of line in `integration/Integration.cpp` since SYS-133 - it must never become inline, otherwise the weak default wins and SM-06 no longer checks the deadman); a project without a power stage has no
deadman and nothing to check.

### 4.4 SM-10 … SM-13 — system MPU

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| `MPU_CESR[VLD]` | 1 | `MemoryProtection::init()` (`safeMemory/MemoryProtection.cpp`) | **cyclic** `mpu::isGlobalEnabled()` (SM-12, context = CESR) | `family` |
| `MPU_CESR[NRGD]` | encodes `NX_MPU_REGIONS` (0 = 8, 2 = 16 regions) | hardware | **cyclic** `hasRegionsOfDerivative()` (SM-12) | `mcudb` `mpu.regions` → `NX_MPU_CESR_NRGD` (bswgen, `static_assert` in `mpu.h`) |
| `MPU_RGD0_WORD2` | debugger region over the whole address space, `REGION0_ACCESS` (fields of the bus masters 0 … 3: `0x007C77D8`): core (M0) **no access** (M0SM 11b, M0UM 000b), debugger (M1) its reset rights, M2 (DMA) **rwx**, M3 (ENET, S32K148 only) **rwx** (M3SM 11b, M3UM 111b); M4 … M7 not implemented on the S32K1 ([RM13] 15.1.2) | `init()`; `platform::StaticBsp::init()` afterwards ORs M3SM 11b / M3UM 111b in through the alias `RGDAAC0`, which `REGION0_ACCESS` already contains | **cyclic** `areRegionsConfiguredCorrectly()` (SM-13), every implemented field, reported as region 0 — **since SYS-171**; before, descriptor 0 was never read (finding SFP-F02) | `family` |
| `MPU_RGD0_WORD3[VLD]` | a write to `RGDn_WORD2` clears the valid bit ([RM13] 15.4.7), so after `init()` descriptor 0 is expected **not valid** (the comment in `init()`: "implicitly deactivated"); the debug chapter of [RM13] says the debugger keeps its access through RGD0 regardless. *Board check B17 (2026-10-02):* reads 0 - not valid after `init()` | `init()` (implicitly) | **not checked**; read once on the board by B17 (`fmea.md` 8.2, SYS-171): 0 | `family` |
| `MPU_RGD1…7_WORD0/1` | start/end from the linker symbols: 0 … protected RAM − 1, protected RAM (`__MPU_BSS_START` … `__MPU_BSS_END` − 1, `FUSA_REGION` = 2), gap, safety stack (`SAFETY_STACK_REGION` = 4), rest … guard − 1, guard below the MSP (`__StackGuardStart` … `__StackGuardEnd` − 1, `STACK_GUARD_REGION` = 6, SYS-97), guard end … 0xFFFFFFFF (7) | `init()` | **cyclic** (SM-13) | `derived` (linker script `application.dld.in`) |
| `MPU_RGD1…7_WORD2` | `REGION_FULL_ACCESS` (M0 … M3 rwx) for 1, 3, 4 (while the safety task runs), 5, 7; `REGION_LOCKED_READ_ACCESS` for 6 (guard); 2 see the gate row | `init()`; descriptor 4 also `fusaTaskStackCloseIsr()` / `fusaTaskStackOpenIsr()` | **cyclic** (SM-13), every field of the bus masters 0 … 3 (`WORD2_FIELDS` = `0x007DFFFF`); before SYS-171 only M0 … M2 (`0x0001FFFF`, finding SFP-F04). The reserved bits 17, 23 and the M4 … M7 enables (not implemented, [RM13] 15.1.2) are not compared | `family` |
| `MPU_RGD2_WORD2` (gate) | outside the safety shell `REGION_LOCKED_READ_ACCESS` (M0/M1 read only, M2/M3 no access in this descriptor, M4–M7 bits 0); inside the shell full access. The "no access" of M2/M3 has **no effect** while descriptor 0 is valid, which grants them rwx everywhere (finding SFP-F03, see below) | `fusaGateClose()` / `fusaGateOpen()` | **cyclic** on entry and exit of the safety cycle (SM-11, whole word), every implemented field in the region check (SM-13) | `family` |
| `MPU_RGDn_WORD3[VLD]` | 1 for regions 1 … 7; the unused descriptors up to `NX_MPU_REGIONS − 1` not valid | `init()` | **cyclic** (SM-13, also RGD[8..15] on a 16-region derivative, ARCH-16) | `family`, count `mcudb` |
| `SIM_PLATCGC[CGCMPU]` | 1 (reset value 0x1F) | nobody | **not checked** (H7) | reset value |

**Other bus masters (SYS-171, finding SFP-F03, open point N3 answered).** The MPU master numbers of the S32K1 are
M0 core, M1 debugger, M2 DMA, M3 ENET (S32K148 only); M4 … M7 are not implemented ([RM13] 15.1.2 Table 15-3). The
system MPU grants an access when **any** valid descriptor grants it. Descriptor 0 covers the whole address space and
grants M2 and M3 rwx, so the "no access" for M2/M3 in the locked gate word and in the guard has no effect as long
as descriptor 0 is valid: **the MPU does not protect the protected RAM, the safety stack or the guard against the DMA
(or the ENET)**. Whether descriptor 0 is still valid after `init()` (its `WORD2` write clears `VLD` per [RM13]
15.4.7, while the debug chapter says the debugger keeps its RGD0 access) read 0 on the board (B17, 2026-10-02:
not valid), but the effect on the M2/M3 rights is not settled (S171-3, SYS-171); this document assumes the
unfavourable case. No neXtBSW driver enables the DMA or the ENET today (dfa DF-44, the ADC
driver refuses `DMAEN`), so the gap has no coupling factor in the code; before the first such user, descriptor 0 must
be restricted for M2/M3 and the master be granted only the regions it needs (H10, SYS-21).

### 4.5 SM-14 — ROM check (CRC unit)

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| `PCC_CRC[CGC]` | 1 | `nxEnablePeripheralClocks()` (infrastructure gate) | **fail-safe by access** | `family` |
| `CRC_CTRL[TCRC, TOT, TOTR, FXOR, WAS]` | 32-bit, transpose bits on write, bits and bytes on read, final XOR; `WAS` 1 only while the seed is written | `crcStart()` (`safeMemory/RomCheck.cpp`) before **every** pass and every known-answer test | **self-healing** + known-answer test before every pass (`0x498CFAA3`, `ROMCHECK_UNIT_FAILED`) | `family` |
| `CRC_GPOLY` | `0x04C11DB7` | same | same | `family` (same polynomial as the post-link step `tools/romCheckImage.py`) |
| `CRC_DATA` | seed `0xFFFFFFFF`, then the image words | same | the result against `__checksum_result` | `derived` (post-link step) |
| exclusive use of the CRC unit | no other user in the image | — | by review (fmea SM-14: "reserved for SM-14") | — |
| `FTFC_FSTAT[CCIF]` | 1 while no flash command runs; a pass does not start with 0 and is discarded if one started | FTFC hardware | read before and after each pass (SYS-44) | `family` |
| program flash (`PFLASH`, `mcudb` `memory`) | the image areas of the checksum list | bootloader / flashing tool | SM-14 itself; ECC on read (SM-16) | `mcudb`, linker script |

### 4.6 SM-15, SM-16 — ECC of RAM and flash (ERM, FTFC)

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| `ERM_SR0[NCE0, NCE1]` | 0; a set bit is taken and cleared (write 1) **before** the report | `takeEccErrors()` (`safeMemory/IntegrityEccHandler.cpp`) | the mechanism itself, **cyclic** | `NCE1` only where the device header defines it (`mcudb`: two RAM channels on S32K14x, one on S32K11x) |
| `ERM_SR0[SBC0, SBC1]` | counted (`eccCorrectionCount()`), cleared, no reaction | same | diagnostic only | same |
| `ERM_EARn[0..1]` | address of the last non-correctable RAM error, logged | same | — | `ERM_EARn_COUNT` of the device header |
| `ERM_CR0[ENCIEn, ESCIEn]` | **0** (no interrupt): the ERM records in `SR0` independently of `CR0` ([RM13] 22.4.1/22.4.2, fmea SM-15) | reset value (nobody writes it) | **not checked** (H3) | reset value |
| `FTFC_FERSTAT[DFDIF]` | 0; taken and cleared like the ERM flags | `takeEccErrors()` | the mechanism itself, **cyclic** (context bit 2) | `family` |
| `FTFC_FERCNFG[DFDIE]` | **0** (no interrupt) | reset value | **not checked** (H3) | reset value |
| `FTFC_FERCNFG[FDFD]` | **0** in operation (1 forces `DFDIF` on every flash read, [RM13] 36.4.4.1.12) — also the natural latent-fault test of the SM-16 path (SYS-73) | reset value | a set `FDFD` makes SM-16 fire in the next cycle: safe direction | reset value |
| `EIM_EIMCR`, `EIM_EICHEN`, `EIM_EICHDn_WORD0/1` (SM-55, SYS-73) | **0** outside the start-up test; during it one channel with one or two data bits for one register-only read, then 0 again (checked after every read, switched off if not) | `eccLatentTestAtStart()` (`safeMemory/EccLatentTest.cpp`, `EccLatentTestTarget.cpp`), once per start in `main()` | the test itself; **not checked** in operation (H3) | reset value; channel count `ERM_EARn_COUNT`/`EIM_EICHDn_COUNT` of the device header |
| `ERM_CR0` during SM-55 | the channel's `ESCIEn`/`ENCIEn` cleared for the test, the previous value restored after the test's record was cleared | `testEccChannel()` | host test `safe-memory-ecc-latent` | reset value (0) |
| NVIC `ERM_double_fault_IRQn` (45), `FTFM_Fault_IRQn` (21) (S32K11x: `ERM_IRQn` 5, `FTFC_IRQn` 18) | **disabled** — their handlers (`os/isr/isr_erm.cpp`, `isr_ftfc.cpp`) print and loop **without** walking the safe outputs table | nobody | **not checked** (H3) | reset value |
| `SIM_PLATCGC[CGCERM]` | 1 (reset value) | nobody | **not checked** (H7) | reset value |
| EIM (error injection module) | unused, `SIM_PLATCGC[CGCEIM]` at reset value | nobody | — | latent-fault test is SYS-73 / gap task 18 |
| core bus fault on a read with a double-bit error | taken as HardFault (4.7) | hardware | SM-17 | — |

### 4.7 SM-17 — fault handlers

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| vector 3 `HardFault_Handler` | naked branch to `customHardFaultHandler` (`hardFaultHandler/src/<architecture>/hardFaultHandler.S`): walk of `nxSafeOutputs` first (R0–R3, no stack, no RAM), then dump, then reset | `os/isr/isr_sys.cpp`, startup vector table | `safe-outputs-order` (disassembly of both architectures and of the demo ELF) | `family` |
| `SCB_SHCSR[MEMFAULTENA, BUSFAULTENA, USGFAULTENA]` (ARMv7-M) | **0** (reset value): every MPU violation (the S32K1 system MPU answers with a bus error), ECC read error, usage fault escalates to HardFault. `BusFault_Handler` and `UsageFault_Handler` print and reset **without** the walk; `MemManage` is `DefaultISR` | nobody | **not checked** (H2) | reset value |
| `SCB_CCR[DIV_0_TRP, UNALIGN_TRP]` | reset value (division by zero yields 0; `SafeSystem` guards its division itself) | nobody | not checked; no mechanism relies on the trap | reset value |
| `SCB_AIRCR[SYSRESETREQ]` | the reset of every S2 reaction (`softwareSystemReset()`) | the reaction | the reset itself; `RCM_SRS[SW]` after it | architecture |
| no-init RAM of the dump (`hardFaultDumpLayout.h`) | outside the RAM the start-up initializes; cleared after POR/LVD before its first read (`prepareDump()` in `boardInit()`) | start-up, handler | read after the reset | `derived` (linker script) |
| on ARMv6-M (S32K11x) | only HardFault exists; same walk (`armv6m/hardFaultHandler.S`) | — | `safe-outputs-order` | `mcudb` `core` |

### 4.8 SM-18, SM-19, SM-20 — ADC reference and internal supplies

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| ADC instance of the internal channels | `ADC0` (the only instance with `INTERNAL0` on every S32K1 derivative) | generated ADC configuration | `bswgen` refuses an internal channel an instance does not have | `project` `adc_internal`, allowed channels `mcudb` `instances.ADCx.internal_channels` |
| `ADC0_SC1n[ADCH]` of slot 13 / 14 | `BANDGAP` / `INTERNAL0` (slots just below the last) | bspAdc from `bsp/adc/…Configuration.h` | a wrong channel gives a wrong value: out of band → SM-18/SM-20; in band → not detected | `derived` (bswgen slot rule) |
| `PCC_ADC0[CGC]` | 1 | clock plan | **fail-safe by access** / read failures → SM-19, SM-20 limit | `derived` |
| VREFH pin | the board reference, nominal `adc_reference_mv` | board hardware | SM-18 every cycle (± `adc_reference_tolerance_mv`) | `board` `adc_reference_mv` (demo 4096 mV), `project` tolerance |
| `SIM_CHIPCTL[ADC_SUPPLYEN]` | 1 | `SafeSystem::init()` (`safeSystem/SafeSystem.cpp`) | **not checked** (H8): cleared, the supply channel reads a value the band rejects (SM-20 fires, safe direction) — assumption, not verified on the board | `family` |
| `SIM_CHIPCTL[ADC_SUPPLY]` | rotated by the safety task over 3.3 V flash, 3.3 V oscillator, 1.2 V core, one step per cycle group | `SafeSystem::checkInternalSupplies()` | **self-healing** (written every round); the value read back decides the nominal 1200 or 3300 mV | `family` |
| PMC `LVDSC1[LVDRE, LVDIE]` | *changed by SYS-125:* `LVDRE = 1`, `LVDIE = 0` (whole register written as `0x10`, `LVDACK` not written). 0 after POR and unaffected by other resets ([RM13] 41.7.1.2), so it is written in **every** start; a VDD below VLVD (2.80 … 3.00 V, [DS13] table 8) resets (`RCM_SRS[LVD]`) and holds the MCU in reset until VDD is above VLVD + 50 mV; the LVR (2.50 … 2.70 V) stays on in all modes (25.2.2.2); in VLPR/VLPS the LVD is off (41.6), SM-59 | `boardInit()` (`main.cpp`, `pmc/LowVoltageDetectReset.h`) before the RAM initialization | checked every safety cycle by SafeIo (4.9, SYS-153); host test `low-voltage-detect-reset` | `family` |

### 4.9 SM-21, SM-22 — register lock and register check (SafeIo list)

The list is generated per project by `tools/bswgen` (`collect_safe_io()`, `bsp/safeIo/safeIoConfiguration.h`) from
the PWM group, the `safe_off` outputs and the role pin; `SafeIo::init()` locks and reads back, `SafeIo::cyclic()`
compares every entry every safety cycle (context: class in bits 31..24, index in 15..0). Nothing is a demo constant
since SYS-24.

| Class | Register / field | Mask | Expected | Lock | Value from |
|---|---|---|---|---|---|
| PIN_FUNCTION | `PORTx_PCRn` of every pin of the used PWM group, of every `safe_off` output and of `safety.locked_pin` | `0x8743` (PS, PE, DSE, MUX, LK; `PFE` not judged) | MUX and attributes of the pin table + `LK` | `LK` (bit 15, until reset); `ISF` (w1c, bit 24) kept out of the lock write | `board` (pin, function), `project` (roles) → `derived` |
| GPIO_DIRECTION | `GPIOx_PDDR` of the GPIO pins of that list, one word per port | pins of the port | 1 for outputs | — | `derived` |
| GPIO_DIRECTION | `GPIOx_PIDR` of the same | same | 0 (input not disabled) | — | `derived` |
| CLOCK_GATE | `PCC_FTMx[CGC]` of the PWM group, `PCC_PORTx[CGC]` of every port in the list | `CGC` | 1 | — | `derived` |
| PWM_TIMER | `FTMx_SC` | `0x00FF003F` | `CLKS = 01` (system clock), `PS`, `PWMEN` of the group's channels | — | `project` PWM group → `derived` |
| PWM_TIMER | `FTMx_MOD`, `FTMx_CNTIN` | `0xFFFF` | period − 1, 0 | — | `derived` (frequency, clock plan) |
| PWM_TIMER | `FTMx_COMBINE` | `0x77777777` | `COMBINE, COMP, DTEN, SYNCEN` of the used pairs; `FAULTEN = 0` | — | `derived` |
| PWM_TIMER | `FTMx_POL` | `0xFF` | active-low channels of the group | — | `board` (active level) |
| PWM_TIMER | `FTMx_INVCTRL` | `0x0F` | 0 | — | `family` |
| PWM_TIMER | `FTMx_CONF[BDMMODE]` | `0xC0` | 01 (outputs at their safe value in a debug halt) | — | `family` |
| PWM_TIMER | `FTMx_PAIRnDEADTIME` of the used pairs | `0x000F00FF` | `DTPS`, `DTVAL` of the configured dead time **in ticks** (see H5) | — | `project` dead time (ns) → `derived` with the system clock |
| SYSTEM_CLOCK | `SCG` | — | **reserved, not in the list** (SYS-55). `SCG_xCSR[LK]` is a guard against accidental writes only: it can be cleared at any time ([RM13] 28.3.8) | — | — |
| SUPERVISION | `PMC_LVDSC1[LVDRE, LVDIE]` (8-bit access) | `0x30` | `0x10` | — | `family` |
| SUPERVISION | WDOG, LPIT, LPTMR | — | covered by 4.1–4.3 | — | — |
| (demo pin of SM-22) | direction of `safety.test_pin` through `Io::getConfiguration()` | — | input | — | `project` `safety.test_pin` (demo PTE14, unconnected, AoU-30) |

Not in the list on purpose: `FTMx_OUTMASK`, `FTMx_SWOCTRL`, `FTMx_CnV`/`SYNC` and `GPIOx_PDOR` — they change at run
time (duty, enable, safe state); their read-back is SYS-30 (H6). FTM write protection (`MODE[WPDIS]`, `FMS[WPEN]`,
[RM13] 47) is not used: the safe outputs walk must still be able to write `SWOCTRL` (SYS-55).

### 4.10 SM-23, SM-24, SM-25 — safe outputs table and the safe state

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| `nxSafeOutputs[]` in flash | one entry per PWM group and per `safe_off` output: address, value, clock gate register, gate mask; end entry 0 | bswgen `collect_safe_outputs()` → `bsp/safeOutputs/safeOutputsConfiguration.h`, `os/isr/isr_wdg.cpp` | build: `static_assert` (aligned address, value, gate); SM-24 (`NX_SAFE_OFF_OUTPUT_COUNT` against the adapters); PLAT-77 (entry by entry in the linked ELF against an independent expectation, board TB1 2026-09-22); SM-14/SM-16 (flash) | `derived` |
| `FTMx_SWOCTRL` of every PWM group | `CHnOC` of the group's channels, `CHnOCV = 0`: every channel forced to its inactive level before dead-time insertion and polarity ([RM13] 47.5.15); takes effect at the next FTM input clock with `SYNCONF[SWOC] = 0` | the walk (fault handlers, watchdog warning, deadman, `enterSafeState()`) | **not read back** (G1, H6) | `derived` (PWM group) |
| `FTMx_POL` | the polarity that makes "inactive" safe for the circuit | driver init | cyclic (4.9) | `board` (active level, AoU-07) |
| `GPIOx_PCOR` / `GPIOx_PSOR` of every `safe_off` output | `1 << n` to PCOR for an active-high output, PSOR for an active-low one | the walk | **not read back** (G1; `PDOR` not in the list, H6) | `board` `active` |
| gate of each entry | `PCC_FTMx` / `PCC_PORTx` `CGC`; an entry whose gate reads 0 is **skipped** (the access would bus-fault inside the fault handler) | — | the gates of the listed peripherals: cyclic (CLOCK_GATE) | `derived` |
| final `DSB` | after the last store, before anything else runs | the walk | `safe-outputs-order` | `family` |
| pin state during and after reset | the reset state of each pin (input, no pull, unless the port says otherwise) must be safe for the circuit | hardware | — | **AoU-07** (integrator, external pulls) |

Demo (`evalboard_s32k144_demo`, reference output of bswgen): `FTM0_SWOCTRL = 0x3F` (MOT1 on channels 0–5, gate
`PCC_FTM0`), `PTC_PCOR = 1 << 11` (`MOT1_GD_DRV_EN`, gate `PCC_PORTC`), `PTD_PCOR = 1 << 17` (`EN_SW_KL30B`, gate
`PCC_PORTD`).

### 4.11 SM-30, SM-31, SM-32 — measurement qualifier and CAN

These mechanisms are software judgements over values a peripheral delivers; their HSI is the peripheral's normal
function.

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| ADC channels of the board (any instance) | conversion per `SC1n[ADCH]` of the generated slot, 12 bit | bspAdc | qualifier (SM-30) for out-of-range values only; in-range faults not detected (G2) | `board` `adc`, `project` |
| `FlexCANx_ESR1[FLTCONF, BOFFINT]` per bus | read by `CanBusMonitor` / `BusStateTransceiver`; `BOFFINT` cleared by the caller after reading | FlexCAN driver (`port/bspConfiguration/bspFlexCan`) | SM-32 (per bus) | `board` (CAN instances), `project` |
| `FlexCANx_ECR[TXERRCNT, RXERRCNT]` | read for the bus state | same | SM-32 | — |
| `FlexCANx_CTRL1[BOFFREC]` | 1 while the recovery holds the controller in bus off (`CanBusOffRecovery.h`) | recovery | — | `project` |
| `PCC_FlexCANx[CGC]` | 1 | clock plan | **fail-safe by access** | `derived` |
| CAN controller CRC and error confinement | hardware function | — | E2E (SM-31) adds a CRC on top for configured signals | — |

### 4.12 SM-50 — start reason and reset-loop limit (RCM, no-init RAM)

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| `RCM_SRS[POR, LVD]` | read in `boardInit()` before the RAM initialization: POR or LVD clears the dump and the reset-loop record | hardware | — | `family` |
| `RCM_SRS[WDOG]` together with `WDOG_CS[TST]` | a watchdog reset with `TST` 2 or 3 is the fast test (SM-07), otherwise a real watchdog reset; counted only without another reset reason in the same start (a J-Link reset starts with the reasons RESET and WATCHDOG together, board finding 2026-09-22, fmea SM-50) | hardware | `ecustate` / `safe-supervisor` host tests; board SYS-18 | `family` |
| `RCM_SRS[LOCKUP]` | kept across the fast-test resets (`resetLoopLockup`), counts as a fault exception | hardware | same | `family` |
| `RCM_SRS[LOC, LOL]` | loss of clock / lock from the SCG monitors (section 3) — reported as a start reason, not counted | hardware | — | `family` |
| reset-loop record (4 words, section `.noinit`) | above the RAM the start-up initializes, outside the MPU region, below the boot request; cleared on POR/LVD | `main.cpp`, `SafeSupervisor::handle()` | check word; ELF placement `safety-context-elf` | `derived` (linker script), `family` defaults `reset_loop_limit` 3, `reset_loop_clear_ms` 60 000 |
| SRAM ECC of the no-init words | valid after POR only once written; the clear on POR/LVD writes them before the first read (PLAT-20) | `boardInit()` | SM-15 on a later read | — |

### 4.13 SM-51 — image check by the bootloader

The bootloader uses a software CRC-32 (`bootloader/src/core/crc32`) and reads the program flash; the CRC unit is not
used there. Its HSI is the flash read path (ECC, SM-16 of the application does not run yet) and the FTFC command
interface for flashing, which belongs to the bootloader's own documentation. The integrity of the bootloader itself
is open point **N4**.

What the bootloader configures and leaves behind (BL-36, safety review 2026-10-02 SYS69-S11, finding BLI-F03 of
INS-BLI-1):

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| `WDOG_CS`, `WDOG_TOVAL` | watchdog **disabled** (`TOVAL` 0xFFFF, `EN` 0, unlock sequence before the interrupts are on), `WDOG_CS[TST]` kept for the application's fast test (BL-33) | `bl_system_init()` in the reset handler (`bootloader/platforms/s32k1xx/hal_s32k1xx.c`) | not re-checked in the bootloader; the application configures the watchdog again in its own start-up and checks it (SM-07, section 4.1) | `family` |
| fault vectors | every fault vector of the bootloader resets unconditionally (`Fault_Handler`: `NVIC_SystemReset()`), no output is touched | bootloader vector table | — | `family` |
| `PMC_LVDSC1[LVDRE]` | 1 from the reset handler on (BL-34, SM-59) | `bl_lvd_reset_enable()` | the application (SYS-153) | `family` |
| SBC watchdog (board device UJA113x, register 0x00 `WMC`/`NWP` over SPI; BL-28, BL-41, BL-42) | **SDMS 0** (no software development mode; with `serve` or `leave`) or `serve` with a watchdog that runs in Normal mode: set in Standby to autonomous, 1 024 ms (written twice, read back; an SBC found in Normal mode is put into Standby first), then Normal mode; triggered every 256 ms by `bl_device_service()` and once more right before the jump. Not taken (read back differs) → no Normal mode, stage `0xE5`. **SDMS 1 with `leave`:** not touched, Normal mode as before. Forced Normal mode: not touched (off). **Read only** (`bootloader.init` without the SBC, `sbc_init(false)`; BL-42): with **SDMS 0** only the watchdog is set (same fixed setting, in Standby; an SBC found in Normal mode is put into Standby first) and triggered as above - the SBC stays in Standby, regulator (V2) and CAN control are not written, no Normal mode (autonomous runs in Standby as timeout while an interrupt is pending, INTN1 low, otherwise it is off; Table 8); not taken → stage `0xE5`; with SDMS 1 read only, nothing written. Reason in `bl_sbc_watchdog.reason` (3 = read only with SDMS 0) | `sbc_init()`, `bl_device_service()` (`bootloader/devices/sbcUja113x/sbc_uja113x.c`) | read back after the setting; the application's SBC driver takes the running watchdog over (AoU-34) | `project` (`bootloader.sbc_watchdog`), device state (SDMS, data sheet UJA113X_SER Table 7, 7.2.1, Table 8) |

**The bootloader phase is not supervised by the MCU watchdog.** From the reset until the application has enabled the
watchdog in its start-up, a hang (program flow, a peripheral that never answers) is not detected by any neXtBSW
mechanism; it ends only by a reset from outside (power cycle, the SBC watchdog the bootloader serves — always with an
SBC without software development mode (SDMS 0, BL-41; since BL-42 also if the SBC is only read), with `bootloader.sbc_watchdog: serve` also with it; AoU-34 —
or a tester's ECU reset). The SBC watchdog then supervises only the bootloader's main loop, not its program flow.
**Why SDMS 0 is always served (BL-41, safety review SBC watchdog 2026-10-06, SDMS0-1 c):** after an SBC reset
without development mode the UJA113x watchdog is a timeout watchdog with 128 ms that already runs in Standby
(public data sheet UJA113X_SER Rev. 2, Table 7 with 7.2.1 and Table 8). A bootloader that left it alone would be
reset over and over without a valid application and would hand the application only the rest of 128 ms since the
SBC reset; `leave` therefore takes effect only with SDMS 1. **The same holds for an SBC that is only read (BL-42,
safety review bootloader 0.3.10 2026-10-07, BL41-2 b):** the watchdog runs whether or not the bootloader needs the
SBC as its transceiver, so the hook sets up the watchdog alone; it needs Standby for the setting (data sheet 7.2,
7.3.3) but not Normal mode for serving, so V2 and CAN stay as found. The updater is not changed: it still refuses to run
unless the SBC watchdog is off (SDMS 1). This is acceptable because the bootloader configures no pin of a power output: all
outputs keep their reset state while it runs (AoU-07, AoU-SEC-3), and a bootloader that stays is a state with
de-energised outputs. The decision to start the application does not rest on RAM state of the past: since BL-36 the
start is checked again immediately before `hal_jump_to_app()`, and a record without compatibility block is not
started. Since BL-38 (bootloader 0.3.8) that second check reads record, compatibility block and entry again from flash
and requires the record's size and CRC field to be the ones the single CRC-32 over the image of `bl_init()` was
computed for and matched; it does not compute a second CRC over the image (0.3.7 did, about 160 ms per start after
the start-up window, BL36-P2). This is sufficient because the bootloader issues no flash command between the two
checks: erase, download and the record write need the programming session, which keeps the bootloader from jumping.
An image body changed without such a command (debugger, bit error) is not seen again before the jump; it is covered by
the flash ECC and by the application's ROM check SM-14 at its start (bootloader README, "One CRC per start"). The flash
read path of the checks is unchanged (ECC of the program flash; a non-correctable error during the check is a bus
fault and resets, BLI-F02).

### 4.14 SM-04 (sequence), SM-05 — the time bases being supervised

| Register / field | Expected | Written by | Re-checked by | Value from |
|---|---|---|---|---|
| `SysTick_LOAD`, `CTRL` | one OS tick per `ASYNC_CONFIG_TICK_IN_US` at the core clock (e.g. 80 000 − 1 at 80 MHz) | `clockConfig.cpp` / RTOS port | **effect** checked by SM-04 (the safety task's period against LPO1K), not the register | `derived` (clock plan) |
| `DEMCR[TRCENA]`, `DWT_CYCCNT` (ARMv7-M system time) | TRCENA 1 (a J-Link session can clear it: re-armed, PLAT-67) | `SystemTimer.cpp` | a stopped system time stops the safety task → SM-01 | `family` |
| `LPIT0` channel 0 (ARMv6-M system time) | 32-bit periodic, `M_CEN`, with `DBG_EN = 1` when the deadman is present | `initSystemTimer()` | not checked by itself; its effect by SM-04 | `family` |
| RTE deadlines (SM-05) | measured with the OpenBSW system time above | generated RTE | S3 only | `project` `rte.yaml` |

### 4.15 SM-34, SM-53 — no MCU hardware of their own

SM-34 (device reports to the safe supervisor) is a software report path in RAM; its hardware interface is that of
the reporting device package (SYS-28). SM-53 (defined run-down) drives the supply role through its driver; since
SYS-174 its last run-down step walks the safe outputs table (the registers of 4.10, the same walk as the fault
handlers), and since SYS-173 an overdue restart ends in the software reset of 4.7 after the same walk. The rest is
device-dependent.

---

## 5. Interrupts and exceptions

| Exception / IRQ (S32K14x; S32K11x) | Handler | Expected state | Priority expected | Set by | Re-checked by | Mechanism |
|---|---|---|---|---|---|---|
| exception 3 HardFault | `HardFault_Handler` → `customHardFaultHandler` | always enabled | fixed −1 | architecture | disassembly test | SM-17, SM-10, SM-15/16 on access |
| exceptions 4/5/6 MemManage, BusFault, UsageFault (ARMv7-M only) | `DefaultISR` / print + reset, **no walk** | **disabled** (`SHCSR` enables 0) → escalate to HardFault | — | reset value | **not checked** (H2) | SM-17 |
| IRQ 22 `WDOG_EWM_IRQn` (22 `WDOG_IRQn`) | `Safe_WDOG_EWM_IRQHandler` (naked, walk first) | enabled | reset value 0 (most urgent) | OpenBSW `enableWatchdog()` | **not checked** (H1); `CS[INT]` checked (SM-02) | SM-01, SM-17 |
| IRQ 49 `LPIT0_Ch1_IRQn` (20 `LPIT_IRQn`, shared) | `LPIT0_Ch1_IRQHandler` (defined in `integration/Integration.cpp`, SYS-133) → `LpitHaltDeadman::onInterrupt()` (walk, flag) | enabled when the project has a power stage | `NX_IRQ_MAX_SYSCALL_LEVEL − 1` (0 on ARMv6-M) | `LpitHaltDeadman::init()` | **cyclic** (`checkLpitDeadman`) | SM-06 |
| IRQ 45 `ERM_double_fault_IRQn` (5 `ERM_IRQn`); IRQ 44 single-bit | 45: `Safe_ERM_double_fault_IRQHandler` (SAFE_ISR wrapper) → print + endless loop, no walk; 44: `DefaultISR` | **disabled** (`ERM_CR0` = 0, NVIC off) | — | reset value | **not checked** (H3) | SM-15 (polling instead) |
| IRQ 21 `FTFM_Fault_IRQn` (18 `FTFC_IRQn`) | `Safe_FTFC_Fault_IRQHandler` (SAFE_ISR wrapper) → print + endless loop, no walk | **disabled** (`FERCNFG[DFDIE]` = 0, NVIC off) | — | reset value | **not checked** (H3) | SM-16 (polling instead) |
| IRQ 58 `LPTMR0_IRQn` (8) | none | **disabled** (`LPTMR0_CSR[TIE] = 0`) | — | `startCycleTimer()` | `TIE` cyclic (4.2) | SM-04; T1b would enable it |
| IRQ 20 `LVD_LVW_IRQn` (21 `PMC_SCG_CMU_IRQn`, shared) | `DefaultISR` | **disabled** (`LVDIE`, `LVWIE` 0) — the LVD acts by its reset (SM-59), not by the interrupt | — | `LVDIE`: `boardInit()` (SYS-125); `LVWIE`: reset value | not checked | none |
| SysTick, PendSV, SVC | RTOS port | enabled | RTOS port; SysTick below `NX_IRQ_MAX_SYSCALL_LEVEL` | RTOS | effect: SM-04 | the safety task itself |
| FlexCAN IRQs per bus | CAN driver | enabled per used bus | project | `main.cpp` `NX_ENABLE_CAN_MB_IRQ` | — | SM-32 (report path) |

Priority rule that the safety argument relies on: the vectors that walk the safe outputs table (WDOG, LPIT channel 1)
must not be delayed by an RTOS critical section. The deadman's level is chosen above the RTOS boundary and checked;
the watchdog vector stays at the reset level 0, which is the most urgent — correct, but unchecked (H1).

---

## 6. Pins

The supervision peripherals (WDOG, LPIT, LPTMR, MPU, ERM, CRC, ADC internal channels) use **no pin**. Every pin a
stage-1 mechanism touches comes from the board and the project; none is a constant of reusable code.

| Pin role | Mechanism | Expected configuration | Written by | Re-checked by | Value from |
|---|---|---|---|---|---|
| channels of the used PWM group | SM-23 (reaction), SM-21/22 | FTM function (MUX of the pinmux table), `PCR[LK] = 1` | `Io` init from `ioConfiguration.h`, lock in `SafeIo::init()` | cyclic (PIN_FUNCTION, start read-back) | `board` `pwm[]`, pinmux `mcudb/<derivative>_<package>.pinmux.yaml` |
| every `safe_off` output (module connections such as `DRV_EN`, KL30B enable) | SM-23 (reaction), SM-21/22 | GPIO output, `LK = 1`, `PDDR` bit 1, `PIDR` bit 0 | same | cyclic (PIN_FUNCTION, GPIO_DIRECTION) | `board` (pin, `active`), module manifest `connect.<name>.safe_off` |
| `safety.locked_pin` | SM-21 | its pin-table function, `LK = 1` | same | cyclic | `project` |
| `safety.test_pin` | SM-22 (demo part) | input, unconnected | `Io` init | cyclic (direction) | `project`; AoU-30 |
| `safety.safe_state_output` | S1 indicator | output; **not** locked (the safe state writes it) — `bswgen` refuses a locked indicator | `Io` init | — | `project`; AoU-30 |
| VREFH | SM-18 | board reference | hardware | SM-18 | `board` `adc_reference_mv` |
| RESET_b | SM-50 | reset input, filtered ([RM13] 25.2.2) | hardware | `RCM_SRS[PIN]` | board circuit |
| FTM fault inputs, TRGMUX, CMP0 inputs | — | **unused** (section 9) | — | — | SYS-85, SYS-86 |

Demo (`evalboard_s32k144_demo`, bswgen reference `tools/bswgen/test/reference/evalboard_s32k144_demo`, board 2026-09-22
SYS-24 point P2): PTB12 … PTB17 ALT2 = FTM0 CH0 … CH5, `PCR = 0x8200`; PTC11 `MOT1_GD_DRV_EN`, PTD17 `EN_SW_KL30B`,
PTC8 `LED2` (locked pin), each GPIO `PCR = 0x8100`; test pin PTE14; indicator LED1 (PTC9). These are board data and
appear here only as an example.

---

## 7. Derivatives of the family

Everything that differs between the derivatives comes from `mcudb/s32k1xx/<derivative>.yaml` or the device header;
the code adapts through generated macros. No row of sections 3–6 changes its **meaning**.

| Fact | S32K116 | S32K142 / S32K144 / S32K146 | S32K148 | Source |
|---|---|---|---|---|
| core | Cortex-M0+ (ARMv6-M) | Cortex-M4F (ARMv7-M) | Cortex-M4F | `mcudb` `derivative.core` |
| NVIC priority bits | 2 | 4 | 4 | `mcudb` `nvic_priority_bits` |
| fault exceptions | HardFault only | HardFault + (disabled) MemManage/BusFault/UsageFault | same | architecture |
| system time of OpenBSW | LPIT0 channel 0 | DWT `CYCCNT` | DWT | family port |
| MPU region descriptors | 8 | 8 | 16 | `mcudb` `mpu.regions` |
| ERM RAM channels (`NCE0/NCE1`) | 1 | 2 | 2 | device header (`ERM_SR0_NCE1_MASK`) |
| WDOG / LPIT / ERM / FTFC IRQ | 22 / 20 (shared) / 5 / 18 | 22 / 48–51 / 44–45 / 21 | same | device header, bswgen `FAMILY_IRQ_ALIASES` |
| `INTERNAL0` supply channel | ADC0 | ADC0 (not ADC1) | ADC0 | `mcudb` `internal_channels` |
| FTM instances for a PWM group | FTM0, FTM1 | FTM0–3 (K142/K144), FTM0–5 (K146) | FTM0–7 | `mcudb` `instances` |
| code cache (LMEM) | none | 4 KB | 4 KB | `mcudb` `features` |
| LPO range | 113 … 139 kHz | 113 … 139 kHz | 113 … 139 kHz | `mcudb` `clock.lpo` |

**Other families.** S32K3xx, STM32 and RM4x need their own HSI document (`hsi-<family>.md`) because the peripherals
differ (S32K3: SWT, XRDC/MPU, eMCEM/ERM, STCU; STM32: IWDG/WWDG, ARM MPU, ECC in flash controller; RM4x: RTI/DWD,
ESM, CCM-R4 lockstep). What carries over is the structure: sections 1, 2 (with the family's peripherals), 5 and 6
and the column "value from". The register list of SafeIo (4.9) is already family-neutral in its runtime part
(`RegisterCheck.h`); only the generator knows s32k1xx registers.

---

## 8. Findings — configuration that is relied on but not re-checked

IDs H1 … H10 are local to this document. Proposals, not decisions; the coordinator assigns task IDs.

| # | Finding | Consequence if the value drifts | Proposal |
|---|---|---|---|
| **H1** | NVIC enable and priority of the watchdog warning IRQ (`WDOG_EWM_IRQn`) are written once (enable by the OpenBSW driver, priority left at its reset value) and never read back. SM-02 checks `WDOG_CS[INT]` only | with the IRQ disabled or delayed, the watchdog still resets, but the de-energising **before** the reset (SM-01 reaction, SM-17 walk) is lost; the pins go to their reset state (AoU-07) | add NVIC enable and priority of the vector to `checkWatchdogControl()` (same pattern as `checkLpitDeadman`), expected values from the family port; or class SUPERVISION of the SafeIo list (SYS-55) |
| **H2** | `SCB_SHCSR` fault enables must stay 0: `BusFault_Handler` and `UsageFault_Handler` print and reset without the walk, `MemManage` is `DefaultISR` | a set enable bit turns an MPU violation or an ECC read error into a handler that does **not** switch the outputs off before the reset | either a start-up + cyclic check `SHCSR & (MEMFAULTENA|BUSFAULTENA|USGFAULTENA) == 0` in the family safety port, or point all three vectors at `customHardFaultHandler`; the second removes the dependency |
| **H3** | ERM (`CR0`) and FTFC (`FERCNFG[DFDIE]`) fault interrupts must stay disabled: their handlers (`isr_erm.cpp`, `isr_ftfc.cpp`) print and loop forever without the walk. Nobody sets their priority, so an enabled one would run at the reset level 0 like the watchdog vector, which then cannot preempt the loop | an enabled ERM/FTFC IRQ turns SM-15/SM-16 into "stuck in an ISR until the watchdog resets" — 250 ms without de-energising, then the reset | make the two handlers walk the table and reset (as HardFault_Handler_Final does), and add `ERM_CR0 == 0`, `DFDIE == 0` to a cyclic check; alternatively state them as configuration rules in the safety manual (DOC-19) |
| **H4** | the LPIT functional clock (`PCC_LPIT[PCS]`, SCG divider `SIRCDIV2` in the demo) is not checked; `TVAL1` is checked in ticks | a slower LPIT clock stretches the deadman time-out beyond 10 ms (a faster one gives false trips, safe direction) | include `PCC_LPIT` (CGC, PCS) and the source divider in the deadman check or in class SYSTEM_CLOCK (SYS-55) |
| **H5** | the system clock (SCG `RCCR`, dividers, source status) is not in the register list (class SYSTEM_CLOCK reserved, SYS-55). The FTM of a PWM group counts the system clock, so `PAIRnDEADTIME` (checked in **ticks**) means a different **time** at a different clock: a faster clock **shortens** the dead time — the hazard of ASG-01 that no other mechanism sees. SM-04 notices a changed clock only through the OS tick, i.e. only if SysTick runs on the same wrong clock and the change exceeds the period tolerance (12 … 17 ms band) | shoot-through risk on a bridge with a clock that is too fast but inside SM-04's band | SYS-55: SCG class with `RCCR`/`CSR` (source and dividers), `SOSCCSR`/`SPLLCSR` (`CM`, `CMRE`, `VLD`, `LK` — the lock is only a write guard, [RM13] 28.3.8); expected values derived from the clock plan |
| **H6** | `FTMx_OUTMASK`, `FTMx_SWOCTRL` and `GPIOx_PDOR` are not read back (known, G1, SYS-30) | the safe state is written but not confirmed | SYS-30 (unchanged) |
| **H7** | `SIM_PLATCGC` (clock gates of MPU, ERM, EIM, DMA, MSCM; reset value `0x1F`) is not written and not checked; what a gated MPU or ERM does to SM-10…13 and SM-15 was not analysed here | possibly a silently inactive MPU or ERM | analyse against [RM13] ch. 11/15/22 and add `CGCMPU`, `CGCERM` to the cyclic check (SYS-55, class SUPERVISION) |
| **H8** | `SIM_CHIPCTL[ADC_SUPPLYEN]` is written once at init and not checked | assumed safe direction (the supply reading fails the band → SM-20) — not verified | host test of that case in `safe-system`, or include the bit in the rotation write (self-healing) |
| **H9** | PMC low-voltage **detect** reset is **off**: `PMC_LVDSC1[LVDRE] = 0` after POR and nobody sets it ([RM13] 41.7.1.2); only the low-voltage **reset** (LVR) and POR act. *Partly closed by SYS-125 (decision of the safety responsible 2026-09-30, AN-066):* `boardInit()` sets `LVDRE` in every start (SM-59). *Closed by SYS-153* (cyclic check, class SUPERVISION) and BL-34 (bootloader sets LVDRE); remaining: LVD off in VLPR/VLPS | before SYS-125: a supply between LVD and LVR threshold was detected by SM-20 (1.2 V / 3.3 V band) only, at safety-task speed. Until SYS-153: a bit flip that clears `LVDRE` went unnoticed until the next start (the LVR still acts); since SYS-153 it is CHECK_REGISTER_ERROR within one safety cycle (S1 without reset) | add `PMC_LVDSC1` (mask `0x30`, expected `0x10`, 8-bit access) to the cyclic check — class SUPERVISION of the SafeIo list (generator `collect_safe_io()`, SYS-55) or a check next to `checkWatchdogControl()` - done by SYS-153 in the SafeIo list; board proof open |
| **H10** | descriptor 0 (whole address space) grants the bus masters M2 (DMA) and M3 (ENET, S32K148) rwx, so the locked gate word and the guard do not keep them out of the protected RAM, the safety stack and the guard; master assignment per [RM13] 15.1.2 Table 15-3 (M4 … M7 not implemented). Corrected by SYS-171 (finding SFP-F03): this row said before that the descriptors give M2/M3 no access. *Caveat of B17 (DOC-59):* the board reads `RGD0_WORD3` = 0, the valid bit of descriptor 0 is not set after `init()`, so the M2/M3 rights may not be in effect; the unfavourable reading stays until S171-3 (SYS-171) settles it | a DMA or ENET user could write the safety manager's data without detection (SM-10 void for it) — open point N3 of fmea SM-10 | before the first DMA/ENET user: restrict descriptor 0 for M2/M3, grant the master only the regions it needs, and refuse such a configuration until then (SYS-21; dfa DF-44); until then **AoU-41** (safety manual section 7.4) forbids any bus master other than the core, review Welle-0210-Tag S171-1 |

Observation without a finding: `WDOG_CS[DBG] = 0` (the watchdog stops in a debugger halt) and `FTMx_CONF[BDMMODE] = 01`
(outputs safe in a halt) are consistent with the deadman design of SM-06; `CS[STOP] = CS[WAIT] = 1` keep the watchdog
running in the low-power modes neXtBSW does not use today.

---

## 9. Hardware functions that are deliberately not used in stage 1

So that their registers are not mistaken for configured: FTM fault inputs (`FLTCTRL`, `FLTPOL` generated as 0,
`SIM_FTMOPT0`, `TRGMUX_FTMx` — SYS-85), comparator CMP0 (SYS-86), EWM (external watchdog monitor; `PCC_EWM` gate is
enabled as infrastructure on derivatives that have it, the module itself is not configured), EIM (error injection,
SYS-73), CMU0/CMU1 (clock monitor units where the derivative has them; gate enabled as infrastructure only), LPTMR compare
interrupt (T1b), PMC LVD/LVW interrupts (H9; the LVD **reset** is used since SYS-125, SM-59), CSEc key storage (only with the user, CLAUDE.md).

---

## 10. Review record

| Item | Content |
|---|---|
| Method | every row traced to the code that writes the value and the code that re-checks it (file named in the row); masks and expected values of 4.9 compared with `tools/bswgen/bswgen.py` `collect_safe_io()` and the reference output of `evalboard_s32k144_demo`; derivative facts compared with `mcudb/s32k1xx/*.yaml` and the device headers; the rows marked with [RM13] re-read in the reference manual in the repository: PMC `LVDSC1` reset value (41.7.1.2), LVD/LVR and LOC/LOL resets (25.2.2.2–25.2.2.5), `SIM_PLATCGC` reset value (11.1.1.3), `FTFC_FERCNFG` fields (36.4.4.1.12), `SCG_SOSCCSR[LK]` semantics (28.3.8), `PCC[PCS]` only writable with `CGC = 0` (29) |
| Result | consistent with fmea section 3 and section 5.2; the ten findings of section 8 are new; no contradiction to concept.md found |
| Not reviewed | register details of the OpenBSW drivers beyond the fields quoted; the S32K1 safety manual (not available, gap 11-01). The MPU master mapping (H10) was read in [RM13] 15.1.2 by SYS-171 |
| Reviewer | author (SYS-66, agent). **Independent review open** — needs a person other than the author (gap analysis section 6 no. 4, question Q1) |
| Verification | **mock** (desk review against code and manual); no board access in this task |

---

## 11. Review questions of the architecture (section 11)

This task changes no code, generator or bootloader; the answers concern the document.

1. **Without an SBC:** nothing in the table depends on an SBC; the SBC's watchdog and reset counter are stage 2.
2. **Different crystal / clock:** the clock facts are project and derivative values (section 3); H4 and H5 name the two
   places where a changed clock is not seen.
3. **Other derivative / family:** section 7; the way to S32K3, STM32 and RM4x is a separate HSI per family with the
   same structure.
4. **Several CAN buses:** 4.11 is per bus instance.
5. **Several tasks:** priorities are listed where a mechanism relies on them (section 5); nothing assumes a single task.
6. **One configuration level per value:** column "value from"; no new value is introduced.
7. **Host test with own profile:** not applicable (document only); proposed follow-up: a consistency test that every
   mechanism of fmea section 3 has a row here and that every expected value of 4.9 matches the bswgen reference
   outputs of **several** profiles (demo, `testproject_safe_outputs`, `s32k116evb_minimal`).
8. **Qt-free core:** not applicable.
9. **Device driver granularity:** not applicable.
10. **Platform rule:** no file under `platforms/` added.

---

## 12. Sources

- [RM13] S32K1xx Series Reference Manual, Rev. 13, 04/2020 (`doc/hardware/s32k1xx/S32K-RM.pdf`): ch. 11 (SIM,
  `PLATCGC`), 15 (MPU), 22 (ERM), 25 (reset sources), 27 (clocking), 29 (PCC), 28 (SCG), 36 (FTFC), 41 (PMC),
  47 (FTM), 48 (LPIT), 49 (LPTMR).
- [DS13] S32K1xx Data Sheet Rev. 13 — LPO range (via `mcudb` `clock.lpo`).
- UJA113X_SER, public data sheet Rev. 2 (`doc/hardware/uja113x/UJA113X_SER.pdf`): Table 7 with 7.2.1 (watchdog
  after an SBC reset with and without software development mode), Table 8 (watchdog per mode), 7.2.5 — the SBC
  watchdog row of 4.13 (BL-41).
- `mcudb/s32k1xx/S32K116.yaml` … `S32K148.yaml`; device headers under `platforms/s32k1xx/bsp/bspMcu/include/3rdparty`
  and `external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/S32K148_COMMON.h`.
- `doc/safety/fmea.md` sections 2, 3 and 5.2; `doc/safety/concept.md` sections 4 (AoU) and 6; `doc/safety/iso26262-gap.md`
  sections 2.2 (6-04), 2.6 and 6 (task 8); `doc/safety/hardware-mechanisms.md`.
- Code: `platforms/s32k1xx/port/safety/*` (safeIo, safeMemory, safeSupervisionCheck, safeSystem, safeLifecycle),
  `platforms/s32k1xx/port/main/src/main.cpp`, `…/integration/LpitHaltDeadman.h`, `…/integration/DebugHaltGuardSystem.h`, `…/integration/Integration.cpp` (strong `LPIT0_Ch1_IRQHandler` and `deadmanExpectedConfiguration()`, out of line since SYS-133),
  `…/os/isr/isr_sys.cpp`, `isr_wdg.cpp`, `isr_erm.cpp`, `isr_ftfc.cpp`, `…/bsp/startUp.S`,
  `platforms/s32k1xx/hardFaultHandler`, `platforms/s32k1xx/bsp/bspClock/src/clockConfig.cpp`,
  `ecu/safety/safeWatchdog`, `external/openbsw/platforms/s32k1xx/safety/safeBspMcuWatchdog`,
  `tools/bswgen/bswgen.py` (`collect_safe_io`, `collect_safe_outputs`, `FAMILY_IRQ_ALIASES`) and its reference output
  `tools/bswgen/test/reference/evalboard_s32k144_demo`.
