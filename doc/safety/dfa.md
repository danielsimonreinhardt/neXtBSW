# neXtBSW dependent failure analysis of the stage-1 mechanisms (SYS-70)

Baseline: v0.2.0

Status: 2026-09-28, **issue 1 (SYS-70)**, written against `dev` at `ae92e89`.
Update 2026-10-03 (**SYS-95**, implementation of the findings): DFA-1, DFA-2, DFA-3, DFA-4 (a) and DFA-6 are
implemented in the family port and host verified, DFA-5 and AoU-DFA-a … AoU-DFA-c are in the safety manual (DOC-19);
the rows DF-05, DF-14, DF-36, DF-46 and DF-47 changed their status, section 0 counts them, section 8.1 says what was
done and what stays open, and the consistency script of section 10.2 is checked in as
[`test/test_dfa.py`](test/test_dfa.py).
Update 2026-10-04 (**DOC-49**, board results of SYS-95 of 2026-10-03/04 and their safety review of 2026-10-04, all
points as recommended): DF-05 from covered (host) to **partial** - a gated LPIT clock is a bus fault caught by SM-17,
a slow LPIT clock is caught by DFA-2 on the board, a lost SIRC is a residual risk carried by SYS-195; DFA-6 and
section 8.1 corrected: a stopped system time is not kind 4 (SM-06 and SM-01 react), kind 4/5 see a slow or fast one.
Readers: the user as architect and safety owner, the coordinator, the agents of **SYS-21** (freedom from
interference), SYS-28 (device packages), SYS-66 (hardware-software interface), SYS-73 (ECC latent test and MCU
safety manual) and DOC-19 (safety manual).
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**What this document is.** The dependent failure analysis (DFA) that the gap analysis
`iso26262-gap.md` (not published, available on request) section 6 no. 15 asks for (rows 9-03, 9-04, 9-05, 9-11, 6-16, 10-07 and 11-02
there): the stage-1 safety mechanisms of neXtBSW run on **one MCU, one core, one supply and one die** together with
the software they supervise. This document asks, systematically and per topic, which single cause can make a
mechanism fail **together with** the element it supervises or together with another mechanism that is supposed to
catch the same fault (common cause), and which failure of one element can **propagate** into a mechanism (cascading
failure). For every potential dependent failure it states whether it is plausible, which measure exists or is
proposed, and why that measure is adequate. The SPI common cause (finding F5 of [`fmea.md`](fmea.md)) is analysed
explicitly (section 6).

**Licence rules followed here.** ISO 26262 is not in the repository and was **not available** to this analysis
(cloud session, `doc/confidential/` does not exist in the remote repository). The standard is cited by part and
clause only, as the gap analysis does, and no text or table of it is reproduced. The **topic list** of section 3 is
the grouping the gap analysis gives for 9-7 (row 9-03), completed with the software topics that 6-7.4.11 and the
freedom-from-interference annex of part 6 name, all in our own words. Whether the list is complete against the
clause and its informative annex is **open point O1** (section 10): it needs a reader of the licensed copy.

**What it is not.** Not a quantitative analysis (no beta factors, no failure rates — those are the integrator's,
AoU-09), not the DFA of the device packages (SYS-28), and not the freedom-from-interference design itself (SYS-21).
It is the list that tells SYS-21, SYS-28, SYS-66 and SYS-73 which dependent failures they have to close or to
accept, and why the ones that are already closed are.

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Scope | the 29 stage-1 mechanisms of `fmea.md` section 3 plus the period part of SM-04, the reset-loop limit of SM-50 and the kernel-fault reactions of SYS-80; the SPI common cause (F5); one MCU (s32k1xx) as the only family with a safety port (concept 1.3) |
| Independence the argument relies on | **14 pairs** (section 2): who supervises whom, who covers whose latent faults, and which reaction path must survive the fault it reacts to |
| Shared resources found | **22** (section 4), from the supply to the developer; every stage-1 mechanism of `fmea.md` section 3 is assigned to at least one |
| Potential dependent failures analysed | **47** (section 5): 11 random hardware, 8 systematic, 7 installation and maintenance, 4 environmental, 5 shared external resources, 5 stress, 7 cascading inside the element |
| Result | <!-- dfa-counts -->**18 covered** by an existing measure (5 of them covered (host) only, board proof open), **14 partial**, **8 open**, **3 AoU**, **1 residual**, **3 not plausible** (with the reason). *Since SYS-95:* the 5 open rows that led to the findings **DFA-1 … DFA-5** are closed or narrowed (DF-36 covered (host), DF-05, DF-46 and DF-47 partial, DF-14 AoU; section 8.1; DF-05 partial since the board results of 2026-10-03/04: a lost SIRC stays a residual risk, SYS-195); a sixth finding, DFA-6, comes from the time-out analysis of DF-32 and SPI-3 and is implemented too. The 8 open ones are carried by existing tasks (SYS-21 twice, SYS-73 twice, SYS-30, SYS-63, SYS-74, N4). Section 7 adds the DFA view to SYS-21, SYS-50/SYS-55, SYS-66, SYS-73, SYS-28, SYS-74 and DOC-19; section 9 proposes 3 assumptions of use |
| The load-bearing measure | **diverse time bases.** The core runs on the system clock (SOSC/SPLL), the watchdog SM-01 and the period check SM-04 on the LPO, the halt deadman SM-06 on the LPIT's own functional clock. A fault of the system clock or of the LPO is seen by a mechanism on another clock (DF-02, DF-03) — with one exception: a stopped LPIT clock stops SM-06 unseen (DF-05, DFA-2). *Since SYS-95 and the board of 2026-10-03/04:* a slow LPIT clock is seen by DFA-2, a gated one is a bus fault (SM-17); a lost SIRC stops SM-06 and leaves only the hardware reset of SM-01 (residual risk, SYS-195). This is the one place where the stage-1 argument has real independence on one die |
| The load-bearing limitation | **one core, one supply, one die.** A core logic fault that keeps servicing the watchdog but computes wrongly (DF-06) and a supply fault outside what the MCU's own low-voltage reset catches (DF-01) have no stage-1 measure; both need the MCU manufacturer's safety manual (N2, SYS-73) or external hardware (AoU-13) |
| New findings | **DFA-1** the run-time ROM re-check (SM-14) stops silently when the idle context is starved — nothing outside the idle context watches it · **DFA-2** a stopped LPIT functional clock stops the halt deadman (SM-06) silently; the configuration re-check sees registers, not a counter that stands still · **DFA-3** a stuck ADC converter that returns a plausible code blinds SM-18, SM-19, SM-20 and SM-30 together · **DFA-4** a clock-gate fault of a PWM timer is detected (CLOCK_GATE class of SM-22) but also disables the PWM part of the reaction it triggers · **DFA-5** expected values and configured values come from the same generated data, so a design fault in the configuration or a generator fault is masked by every run-time comparison · **DFA-6** the SPI time-out and the CAN time-outs count on the OpenBSW system time — a stopped system time makes them unbounded (section 8) |
| SPI common cause (F5) | **confirmed: no stage-1 mechanism depends on SPI**, and no SPI failure can switch an output on that S1 switched off (the enable is a GPIO in the safe outputs table). SPI is a common cause for the **stage-2** device diagnostics (SYS-28) and a shared resource between partitions (SYS-21); a bus arbitration is missing if a board puts two devices on one SPI master (section 6) |
| Coordination with SYS-21 | SYS-21 is `open`; its agent has not started. Section 7 is the hand-over: 9 potential dependent failures whose measure belongs to SYS-21, each with what SYS-21 must show. The DFA is **verified** against the repository and the other safety documents (section 10), but **not reviewed** by a second person (I1, SYS-63) and not yet acknowledged by SYS-21 |
| Verification | **analysis only (mock).** Code and documents read at `ae92e89`; the consistency script [`test/test_dfa.py`](test/test_dfa.py) (section 10.2, checked in by SYS-95) checks every mechanism, task and test ID cited here and the counts of this page. The measures of SYS-95 are host verified (mock); on the board (2026-10-03/04, eval board) SYS95-B1, B3 and B4 passed, DFA-2 is proven with a slowed LPIT clock, and B2/B5 showed safe reactions through SM-17, SM-06 and SM-01 instead of the new checks (section 8.1; safety review SYS-95 board 2026-10-04) |

---

## 1. Method

### 1.1 Terms

In our own words:

- **Dependent failure** — two or more failures whose probability of occurring together is higher than the product
  of their single probabilities. Two kinds:
  - **Common cause failure (CCF)** — one root cause makes several elements fail, directly, through a **coupling
    factor** they share (the same clock, the same supply, the same code origin, the same person);
  - **Cascading failure (CF)** — the failure of one element causes the failure of another (a runaway task corrupts
    the supervisor's data; a stuck SPI transfer stalls a task).
- **Initiator** — the event or fault that starts the dependent failure.
- **Coupling factor** — the shared property or resource through which it spreads.
- **Independence vs. freedom from interference.** Freedom from interference (FFI, SYS-21) is the absence of
  *cascading* failures from a lower-rated element into a higher-rated one. Independence additionally excludes
  *common causes*. At ASIL B neXtBSW does not use decomposition (gap 9-01), so it needs independence only where the
  **argument relies on it**: where a mechanism is claimed to detect a fault of the element it supervises, or to
  cover the latent faults of another mechanism. That is the list of section 2.

### 1.2 Steps

1. **What must be independent** (section 2): from `fmea.md` section 3 and `concept.md` section 6, every pair
   "supervised element ↔ mechanism", "mechanism ↔ mechanism that covers its latent faults" and "detection ↔
   reaction path" that the stage-1 argument relies on.
2. **Shared resources** (section 4): for every mechanism, what it needs to work — hardware on the die, execution
   context, code and data, tools, people. A resource used by both sides of a pair of section 2 is a coupling factor.
3. **Topics** (section 3): each topic is walked against the shared resources to find initiators.
4. **Per potential dependent failure** (section 5): kind (CCF/CF), plausibility with reason, measure, rationale,
   status, evidence. A measure is one of three types, in the order of preference the gap analysis gives for 9-7:
   - **P** — prevents the root cause (for example the CRC unit reserved for one user);
   - **D** — detects or controls the effect (for example a check on an independent clock);
   - **R** — reduces the coupling (for example a separate time base, a separate execution context).
5. **Findings and hand-over** (sections 7 and 8): what is not closed becomes a finding, an addition to an existing
   task, or an assumption of use.

### 1.3 Status words

| Status | Meaning |
|---|---|
| **covered** | a measure exists in the code and is verified (host tests with built-in faults and/or board, as named) |
| **covered (host)** | the measure exists and is host verified; the board proof is open (named) |
| **partial** | a measure exists but leaves a named gap |
| **open** | no measure yet; a finding DFA-n (section 8) or an addition to a named task |
| **AoU** | the element cannot close it; the integrator's duty, as a proposed assumption of use (section 9) |
| **residual** | accepted, with the reason and who accepted it |
| **not plausible** | the dependent failure cannot occur in the stage-1 design, with the reason |

### 1.4 Plausibility

A potential dependent failure is **plausible** if the initiator can occur in the target environment of the element
(an S32K1 ECU in a vehicle, concept 1.2) **and** the coupling factor exists in the code at `ae92e89`. It is not
plausible if the coupling factor does not exist (for example no second user of a resource) or if the initiator is
excluded by construction (for example a build error). Rates are not estimated (concept section 0, AoU-09).

---

## 2. What the stage-1 argument needs to be independent

| ID | Supervised element / covered mechanism | Supervising mechanism | Why independence is needed | Main coupling factors (section 4) |
|---|---|---|---|---|
| I-01 | execution of the safety task | SM-01 MCU watchdog | the watchdog must still count when the task, the scheduler or the core clock has stopped | R-02, R-03, R-05, R-13 |
| I-02 | period of the safety task | SM-04 period part (LPTMR) | the period is set by the OS tick on the system clock; the check must not use that clock | R-02, R-03, R-08 |
| I-03 | execution of all software (≥ 10 ms stall, debug halt) | SM-06 halt deadman (LPIT) | the deadman must fire while no software runs | R-04, R-05, R-20 |
| I-04 | supervisor data and stack | SM-10 … SM-13 MPU | QM code and the supervisor share the core and the address space | R-07, R-14, R-17 |
| I-05 | detection of a fault (any SM) | reaction path: `SafeSupervisor::handle()`, `enterSafeState()`, the safe outputs table | the reaction must survive the fault it reacts to | R-06, R-07, R-10, R-16 |
| I-06 | reaction path in C | direct walks of the table in assembler (SM-17, SM-06) | the path for faults that make the C path untrustworthy | R-06, R-10 |
| I-07 | SM-01 (latent: cannot reset) | SM-07 start-up self-test | the latent fault must be found by a test of the real reset path | R-13, R-15 |
| I-08 | program flash content | SM-14 ROM check (CRC unit) and SM-16 (ECC) | the check must not rely on the content it checks, nor share its failure mode | R-06, R-09, R-12 |
| I-09 | SM-15/SM-16 (latent: ECC logic blind) | — (SYS-73: error injection) | nothing covers a blind ECC today; named here so the gap is visible | R-06, R-07 |
| I-10 | analogue values (SM-30) and supply check (SM-20) | SM-18/SM-19 reference supervision | reference and measured values go through the same converter | R-11 |
| I-11 | safety-related CAN data | SM-31 E2E, SM-32 bus state | black channel: E2E must not depend on the CAN stack it protects | R-17, R-18 |
| I-12 | application image | SM-51 bootloader check | the check runs before the checked image | R-06, R-22 |
| I-13 | a permanent fault answered by S1 + S2 | reset-loop limit (SM-50 extension) | the limit must count resets the fault causes, even if the fault destroyed the run-time state | R-07, R-15 |
| I-14 | registers that steer the power stage | SM-21/SM-22 register lock and check (SYS-24 list) | the check must see a register that a driver or a fault changed | R-16, R-21 |

---

## 3. Topics walked

Own words; the grouping follows the gap analysis row 9-03 and the software topics of 6-7.4.11.

| # | Topic | What it means for software mechanisms on one MCU | Section 5 |
|---|---|---|---|
| T1 | Random hardware faults of **shared on-chip resources** | supply, clocks, core, memories, interrupt and reset logic used by the supervised element and the mechanism | 5.1 |
| T2 | **Systematic development faults** with a common origin | same specification, same code origin (OpenBSW demonstration), same compiler, same generator and configuration data, same people | 5.2 |
| T3 | **Production** faults | end-of-line programming, MCU manufacturing faults (the latter AoU-09) | 5.3 |
| T4 | **Installation, repair, maintenance** | flashing, bootloader update, debug access, calibration, test commands left in the image | 5.3 |
| T5 | **Environmental** conditions | temperature, electromagnetic disturbance, humidity and vibration acting on the whole die at once | 5.4 |
| T6 | Failure of **shared external resources** | external supply, crystal, CAN bus, SPI buses and the devices on them, debug probe | 5.5 and 6 |
| T7 | **Stress** from specific situations | CPU and interrupt load, long operation without restart, repeated resets, flash writes | 5.6 |
| T8 | **Software shared resources** (6-7.4.11, FFI annex of part 6) | memory, execution and timing, exchange of information between elements of one image — the cascading failures inside the element | 5.7 |

---

## 4. Shared resources and who relies on them

| ID | Resource | Stage-1 mechanisms relying on it | Supervised elements relying on it |
|---|---|---|---|
| R-01 | Supply of the MCU (external VDD, internal 1.2 V core, 3.3 V flash and oscillator regulators) | all | all |
| R-02 | System clock (SOSC/SPLL through SCG): core, bus, SysTick (OS tick), FTM counters, CAN bit timing, UART | every software mechanism; SM-05 measures with it; E2E and CAN time-outs count with it | all tasks |
| R-03 | LPO (128 kHz, divided to 1 kHz) | SM-01 (WDOG), SM-04 period part (LPTMR0 on LPO1K) | — |
| R-04 | LPIT0 and its functional clock (mcudb `clock.pcc.LPIT`, SIRCDIV2 by default) | SM-06 (channel 1) | on ARMv6-M the OpenBSW system time (channel 0) |
| R-05 | CPU core, NVIC, interrupt mask (PRIMASK), bus matrix | every software mechanism; SM-17 through the exceptions | all |
| R-06 | Program flash and flash controller | code of every mechanism; safe outputs table; SYS-24 register list; MPU descriptors expected; SM-14 reference and area list | all code |
| R-07 | SRAM | supervisor state and safety stack (MPU region); event mailboxes, ROM-check write indicator, reset-loop record (ordinary / no-init RAM) | all data |
| R-08 | Safety task (one FreeRTOS task, highest application-relevant priority, `above_application`) | SM-02, SM-03, SM-04, SM-11 … SM-13, SM-15, SM-16, SM-18 … SM-20, SM-22; delivery of SM-05 and SM-34 reports | — |
| R-09 | Idle context | SM-14 passes (`RomCheck::idle()`), console and log drain | — |
| R-10 | Reaction path: `SafeSupervisor::handle()`, `enterSafeState()`, `holdSafeState()` | reaction of every mechanism that raises an event; SM-25 (de-energise before the reset) | — |
| R-11 | ADC instance, VREFH, bandgap, the shared `AnalogInput` driver (IO-14 lock) | SM-18, SM-19, SM-20 | SM-30 and all analogue application values |
| R-12 | CRC unit | SM-14 only (reserved, `fmea.md` SM-14) | — |
| R-13 | WDOG | SM-01, SM-02, SM-03, SM-07; serviced inside S1 held | — |
| R-14 | MPU | SM-10 … SM-13 | — |
| R-15 | Reset controller (RCM) and the no-init RAM record | SM-50 and the reset-loop limit | — |
| R-16 | FTM, PCC clock gates, PORT and GPIO of the power stage | SM-23 walk, SM-21/SM-22 (SYS-24 classes PIN_FUNCTION, GPIO_DIRECTION, CLOCK_GATE, PWM_TIMER) | the application's PWM and outputs |
| R-17 | FreeRTOS kernel (scheduler, TCBs, tick) | every task-based mechanism; SM-53 (run-down steps with time-outs); SYS-80 reactions | all tasks |
| R-18 | CAN controller, CAN task, transceiver | SM-31, SM-32 | safety-related CAN data |
| R-19 | SPI masters (LPSPI) and the devices on them | **none of stage 1** (F5) | stage-2 device diagnostics |
| R-20 | Debug port (SWD) and probe | SM-06 must work while it halts the core | all |
| R-21 | Development resources: compiler, OpenBSW origin of the safety code, generators and configuration data, the people | all; SM-24 is a build check of the generated data | all |
| R-22 | Bootloader | SM-51; the start of everything | the application image |

---

## 5. Potential dependent failures

Columns: **Kind** CCF / CF. **P?** plausible (yes / no). **Measure** with its type P / D / R (section 1.2).

### 5.1 T1 — random hardware faults of shared on-chip resources

| ID | Initiator → coupling | Fails together | Kind | P? | Measure | Rationale | Status / evidence |
|---|---|---|---|---|---|---|---|
| DF-01 | Supply of the MCU out of range (R-01) | every mechanism and everything it supervises; SM-20 measures the supply with the same die it supervises | CCF | yes | **D** hardware power-on and low-voltage reset of the MCU (PMC); since SYS-125 also the low-voltage **detect** reset, switched on by the family port (SM-59, not credited); **D** SM-20 for a drift inside the working range; **R** an external supply supervisor / SBC (stage 2, AoU-09) | software on the die cannot be credited for an under-voltage that makes the die itself unreliable; it is credited only for a drift while the die still works correctly. The hardware reset path is the MCU manufacturer's claim, and its configuration (PMC reset enables) belongs into the HSI | **partial** — the PMC configuration is recorded (SYS-66, `hsi-s32k1xx.md` H9) and the LVD reset is on since SYS-125 (SM-59, AoU-39), not re-checked cyclically; the manufacturer's claim needs the safety manual (N2, SYS-73); proposed **AoU-DFA-a** (section 9) |
| DF-02 | System clock wrong, lost or fallen back to FIRC (R-02; PLAT-72: factor 6) | OS tick, safety task period, RTE deadlines (SM-05 measures with the faulty clock and is blind), E2E and CAN time-outs, CAN bit rate | CCF | yes | **R/D** SM-04 period part counts on the LPO: a period ≥ 17 ms is always reported, a mean period < 7.6 ms at the end of a window; **D** SM-01 on the LPO for a stop; **D** clock monitors of SOSC and SPLL with reset (`SOSCCMRE`, `SPLLCMRE`, enabled by the family clock driver when the clock is valid, `bspClock/src/clockConfig.cpp`, PLAT-85); **D** start-up clock check `nxClockCheck()` (report only) | all tasks share the one tick, so a check of the safety task's period against an independent oscillator detects a clock fault for every task at once. A fallback by factor 6 stretches the period to 60 ms: reported at the first late cycle (kind 1) | **partial** — covered for loss and large errors (SM-04 board B4/B7 2026-09-22, `safety-cycle`, `cycle-period-check`); open for an error inside the SM-04 band: a clock up to about 17 % slow (period ≤ 12 ms) is never reported, one up to about 32 % fast (mean period ≥ 7.6 ms) may not be → the register-level check of the clock plan (SYS-55 class `SYSTEM_CLOCK`) and the reaction at start (SYS-50) close it; addition to both (section 7.2) |
| DF-03 | LPO stopped or drifted (R-03) | SM-01 and SM-04 period part lose their time base together | CCF | yes | **D** SM-04 detects a stopped LPTMR counter (window kind 2, value 0) and a reconfigured timer (kind 3) from the **core-clock domain** → S1 + S2; after the reset the stopped LPO is found again and the reset-loop limit holds S1 at the N-th start; **P** the bounds of SM-04 and the watchdog relation are derived at both ends of the data-sheet LPO range (mcudb `clock.lpo`, SYS-53) | the two LPO users fail together, but the comparison runs on the other clock, so the common failure is seen. A drift inside the data-sheet range is not a fault by construction of the bounds | **covered** (`cycle-period-check`, `lptmr-cycle-timer`, `safety-cycle`, `safe-supervisor` for the limit; board B7(a) 2026-09-22; reset-loop limit board B10/B11 open) |
| DF-04 | LPO and system clock drift in the same direction (temperature, supply; R-02 + R-03) | SM-04 compares two clocks that moved together | CCF | yes | **residual**: the SPLL is locked to the crystal and the LPO is an RC oscillator; a common drift is bounded by the LPO's data-sheet range, which the bounds already include | the SM-04 blind spot named in `fmea.md` (SM-04 period part, blind spots); a drift beyond the data sheet is a hardware failure for the integrator's metrics (AoU-09) | **residual** (accepted in `fmea.md` SYS-40) |
| DF-05 | LPIT functional clock stopped (SIRC off, PCC gate or source changed; R-04) | SM-06 stops silently; on ARMv6-M also the OpenBSW system time (channel 0) | CCF | yes | **D** the configuration re-check `checkLpitDeadman()` (SYS-41/SYS-48) sees `MCR[M_CEN]`, the channel enable, `TVAL`, NVIC — **not** a counter that stands still because its clock stopped; on ARMv6-M a stopped system time stops the safety task and SM-01 fires (250 ms) | on ARMv7-M (S32K14x) the system time runs on the DWT, so nothing sees the deadman's clock stop: SM-06 becomes latent until the next stall, which it then misses | **partial** since SYS-95 (DFA-2): the safety cycle polls the deadman's counter until it changes, within a budget of two LPIT ticks derived from the clock plan; a counter that stands still **or moves too slowly** with the channel enabled is `DEADMAN_CONFIGURATION_ERROR` field 0x100 (`lpit-deadman-progress`, `safety-progress`; board 2026-10-04: `SIRCDIV2` /64, deadman 640 ms instead of 10 ms, gave `SAFETY RESET (DEADMAN_CONFIGURATION_ERROR, fields 0x100)`). **PCC gate** of the LPIT switched off: every LPIT access is a bus fault, the fault handlers SM-17 walk the table and reset within about 1 ms (board 2026-10-03, SYS95-B2: `HARD FAULT RESET: outputs off`); DFA-2 cannot see a gated clock and need not. **SIRC lost** while the core runs: **residual risk** - observed no log line, no walk, a hardware reset of the MCU watchdog (`RCM_SRS` 0x20) after at most about 283 ms, the outputs until then not shown to be de-energised (SM-06 runs on the same clock); the cause of the hang is open (with the core halted the debugger reads `CVAL1` and `TCTRL1` without error, board 2026-10-04, so a blocked bus is not confirmed); investigation and architecture (for example the deadman on another clock, or an external watchdog on the enable line) with SYS-195, after release R1 (safety review SYS-95 board 2026-10-04, B2-2 a) |
| DF-06 | Core logic fault (ALU, register file, pipeline, bus matrix) that leaves the program flow intact (R-05) | every software mechanism computes wrongly while the safety task still services the watchdog | CCF | yes | **D** SM-01, SM-04, SM-17 catch a fault that stops or diverts the program flow or raises an exception; **none** for a fault that computes wrongly on a correct flow | single core without lockstep (concept 2.1); the independence the stage-1 argument has is in **time**, not in **computation**. Whether the S32K1 needs a software core self-test is a statement of its safety manual | **open → addition to SYS-73** (section 7.3); proposed **AoU-DFA-b** |
| DF-07 | Interrupt mask stuck set (PRIMASK) or NVIC fault (R-05) | SM-06 interrupt, watchdog warning interrupt (walk before the reset), OS tick, every task | CCF | yes | **D** SM-01 hardware reset on the LPO does not need an interrupt; the pins go to their reset state | only the watchdog reset remains, so de-energising depends on the reset state of the pins (AoU-07). S1 held runs with interrupts suspended **by design** and walks the table itself in every round | **covered** by SM-01 with **AoU-07** (board 2026-09-21 SYS-18, watchdog reset) |
| DF-08 | Uncorrectable program-flash fault in the code of a mechanism **and** of the reaction path (R-06) | the mechanism and `handle()` / the table | CCF | yes | **D** SM-16 (ECC flag, polled), bus fault on the access → SM-17 (walk from assembler, table in flash); **D** SM-14 CRC over the whole image at start and once per period (known-answer test of the CRC unit before each pass); **D** SM-51 at start | two diverse detections (ECC per word on read, CRC over the image) with different failure modes; the table read by SM-17 is inside the CRC areas | **covered** (`safe-memory-ecc`, `rom-check`, `rom-check-mutations`, board 2026-09-21 SYS-38); run-time part depends on DF-36 |
| DF-09 | Uncorrectable SRAM fault in the supervisor state (R-07) | monitors, counters and the event handling | CCF | yes | **D** SM-15 (ERM poll), bus fault on read → SM-17; **R** supervisor state in its own MPU region | a double-bit error is flagged on read; the reaction for a read by the core is the exception path, which needs no RAM | **covered** (`safe-memory-ecc`, `safe-supervisor-mpu`) |
| DF-10 | ECC logic latent fault: ERM or flash ECC does not flag (R-06, R-07) | SM-15 and SM-16 blind together | CCF | yes | **D** for flash: SM-14 CRC (diverse); **none** for RAM | SM-15/SM-16 share the ECC principle; the CRC of SM-14 is the only diverse check, and only for the program flash | **open → SYS-73** (error injection module start-up test, already planned); named here as the dependent side |
| DF-11 | Reset controller records a wrong cause, or the no-init record is disturbed (R-15) | SM-50 and the reset-loop limit | CCF | yes | **D** the reset-loop limit uses three sources (supervisor mark, `RCM_SRS`, the fault dump) and a check word; a disturbed record restarts the count from 0 and never holds by itself; **R** the record is cleared on POR/LVD before the first read | a wrong cause can only delay the limit, not trigger a hold; the direction is documented in `fmea.md` SM-50 | **covered (host)** (`safe-supervisor`, 17 built-in faults; board B10/B11 open) |

### 5.2 T2 — systematic development faults with a common origin

| ID | Initiator → coupling | Fails together | Kind | P? | Measure | Rationale | Status / evidence |
|---|---|---|---|---|---|---|---|
| DF-12 | Flaw in the OpenBSW safety demonstration that several copied modules share (R-21; `tools/openbswsync/copies.yaml`) | `safeSupervisor`, `safeWatchdog`, `safeLifecycle`, `safeMemory`, `safeIo`, `safeSystem` | CCF | yes | **D** a host test with built-in faults **per mechanism** (T3 of `fmea.md`, closed by SYS-41/SYS-42); static analysis; **P** code inspection (SYS-69); qualification route per component (SYS-62) | the history shows it is real: SM-14 never ran (SYS-23), the SM-21 read-back ignored the lock bit (SYS-42) — both found by tests written against the fault model, not against the copied code | **partial**: tests exist; inspection and qualification open (SYS-69, SYS-62) |
| DF-13 | Compiler or library fault (R-21) | every mechanism compiled with it | CCF | yes | **D** target-level checks of the ELF (`safe-outputs-order` disassembly, `safety-context-elf`, `freertos-config-elf`), board runs; **R** host tests compile the same sources with the host compiler (diverse compilation for the logic, not for the target code); **P** pinned toolchain (xPack GCC 11.3.1) | a miscompilation that the host build does not reproduce is caught only by target-level checks and board runs, which exist for the reaction paths | **partial** (tool classification PLAT-75; AoU-20) |
| DF-14 | Generator fault or wrong configuration data (R-21; `tools/bswgen`, `mcudb`, `board.yaml`, `project.yaml`) | the configured value **and** the expected value the run-time check compares with | CCF | yes | **D** `nxconfig` checks with YAML paths, generator tests with own profiles and generator mutants (SYS-24: 13 × 2), SM-24 build check; **P** tool classification (PLAT-75); **D** board tests of the generated result | every run-time check of stage 1 (SM-13 descriptors, SM-21/SM-22 register list, SM-23 table) compares the hardware with values from the **same** generated source that configured it. It detects a **corruption after start**, never a **wrong design**: a wrong inactive level, a missing `safe_off` output or a wrong dead time is configured and expected alike | **AoU** since SYS-95 (DFA-5): the claim limit is stated in the safety manual (10.2) and the integrator verifies the configuration itself, **AoU-DFA-c**; AoU-06, AoU-07, AoU-10 |
| DF-15 | Wrong derivative fact shared by several derivations (R-21; mcudb `clock.lpo`, `NX_MPU_REGIONS`) | SM-01 relation and SM-04 bounds (LPO range); SM-12 (region count) | CCF | yes | **P** `source:` per mcudb value, `verify` flags; **D** `cycle-period-constants` recomputes the bounds independently; `nxconfig-watchdog` | a wrong LPO range biases the watchdog relation and the period bounds in the same direction; the data-sheet source is the only independent reference | **partial**: review of the mcudb values against the data sheet belongs into the HSI (SYS-66, addition section 7.4) |
| DF-16 | Common misunderstanding of one author across specification, implementation and test (R-21; one human, agent-written code and tests) | a requirement, its mechanism and its test | CCF | yes | **D** built-in faults (mutants) test the tests; board runs against physical behaviour; **P** confirmation reviews with independence (SYS-63) | mutants show a test catches a code change; they cannot show the requirement is right. Only a second person can | **open** → SYS-63 (decision iso-q1: reviewer later) |
| DF-17 | The table semantics exist twice (C `enterSafeState()`, assembler walk) and could diverge (R-10) | S1 from a task vs. S1 from a fault handler | CF | yes | **D** `safe-outputs-order` and the debug-halt guard host test check both against the same table | deliberate diversity of the two paths needs a test that they mean the same thing; it exists | **covered** (`safe-outputs-order`, `debug-halt-guard`) |
| DF-18 | One reaction function for all events (R-10: `handle()`) | reaction of every mechanism that raises an event | CCF | yes | **R** SM-17 and SM-06 bypass `handle()` and walk the table directly; **D** SM-01 as the last backstop; **P** `safe-supervisor` (reaction per event, built-in faults), static rule and ELF check who may call it | the single decision point is the load-bearing design decision of the concept (section 3); its systematic faults are covered by tests, its random faults by the two bypass paths and the watchdog | **covered** (`safe-supervisor`, `safety-context`, `safety-context-elf`, `safe-outputs-order`) |
| DF-19 | The safe outputs table itself is incomplete or wrong (R-10, R-16; AoU-06) | all three de-energising paths at once | CCF | yes | **D** SM-24 build check (one adapter per `safe_off` output); **none** at run time for "the walk wrote but nothing changed" (G1) | one table for three paths is chosen on purpose (concept 6.5: two lists would drift); the price is this common cause, and the answer is the read-back of G1 | **open** → SYS-30 (G1), unchanged; AoU-06/AoU-07 |

### 5.3 T3/T4 — production, installation, repair and maintenance

| ID | Initiator → coupling | Fails together | Kind | P? | Measure | Rationale | Status / evidence |
|---|---|---|---|---|---|---|---|
| DF-20 | Interrupted or wrong download (R-06, R-22) | image, ROM-check list and reference | CCF | yes | **D** SM-51 CRC over the whole image including list and reference at every start; **D** SM-14 at run time for a change after the bootloader check | two checks at two times; power loss during download is tested | **covered** (`bootloader-host`, `bl-update-host`, `bl-update-mutations`, board 2026-09-21 VER-07) |
| DF-21 | Bootloader itself corrupted (R-22) | SM-51 and the start of everything | CCF | yes | **none** in stage 1 (open point N4) | the bootloader checks the application, nothing checks the bootloader | **open** (N4, unchanged; not a new finding) |
| DF-22 | Debugger halts the core (R-20) | every software mechanism stops together | CCF | yes | **D** SM-06: LPIT counts through the halt (`MCR[DBG_EN] = 1`), walk on the first instruction; **P** FTM `CONF.BDMMODE = 01` (outputs safe in debug halt), checked by the PWM_TIMER class; **R** the LPTMR stops in debug (no false period report of SM-04); the WDOG keeps counting, so a halt longer than 250 ms ends in the watchdog warning walk and the reset (board 2026-09-18) | the only coupling software cannot break; the power stage is made safe by the FTM's own debug behaviour and the deadman | **covered** (board IO-32 2026-09-21: halts of 30 s and 60 s) with the accepted gap < 10 ms (decision `io32-halt-erkennung`); **AoU-16** |
| DF-23 | Debugger or console writes registers or triggers faults (R-20; fault-injection commands in the image, gap SG6) | several mechanisms can be disabled or triggered from one interface | CCF | yes | **P** product build without test commands (SYS-74); debug port closed in the product (AoU-16) | test commands are a single interface into several mechanisms; in a product they are a common cause of operator error | **open** → SYS-74 (unchanged); **AoU-16** |
| DF-24 | Calibration changes a parameter several mechanisms use (XCP; R-21) | e.g. the ADC reference band for SM-18 and SM-20 | CCF | yes | **P** production profile without XCP, CRC of the calibration segment at start (SYS-89) and in every ROM check pass (SYS-91); plausibility (SYS-90) | same data feeds several checks; the protection is that the data cannot change in the product | **partial** → SYS-76/SYS-89/SYS-90 (unchanged) |
| DF-25 | NvM flash writes (maintenance of data) suspend SM-14 (R-06, R-09) | ROM check postponed | CF | yes | **D** `ROMCHECK_OVERDUE` after one period of postponement; **P** a pass during which a write began is discarded, never reported | the coupling is deliberate (no false alarm); its effect is bounded and reported — except with period 0 | **covered (host)** (`rom-check`, `nvm-flash-write-bracket`; board B9 open) |
| DF-26 | MCU manufacturing fault common to mechanism and supervised element (T3) | any | CCF | yes | the MCU manufacturer's production test and safety manual | not a software matter | **AoU** (AoU-09) |

### 5.4 T5 — environmental conditions

| ID | Initiator → coupling | Fails together | Kind | P? | Measure | Rationale | Status / evidence |
|---|---|---|---|---|---|---|---|
| DF-27 | Temperature at the edges of the range (whole die) | LPO frequency (SM-01, SM-04), bandgap (SM-18), flash retention | CCF | yes | **P** SM-04 bounds and watchdog relation over the full data-sheet LPO range; **D** SM-16/SM-14 for retention; **residual** for the bandgap's temperature error (SM-18 reference shares the die) | the numeric design already uses worst-case data-sheet ranges; what remains is F16/AoU-24 | **partial** — covered for timing; AoU-24 (proposed in `fmea.md`) for the reference |
| DF-28 | Electromagnetic disturbance: several bit flips at once in RAM and registers | supervisor state and a checked register at the same time | CCF | yes | **D** ECC (SM-15), cyclic register checks (SM-02, SM-06 re-check, SM-11 … SM-13, SM-22 list), SM-04; **D** SM-01 as backstop if the reaction path is hit too | many independent checks each cycle make it unlikely that one burst silences all of them; the watchdog does not depend on RAM or registers the burst can reach in the core domain | **covered** by design; immunity of the hardware **AoU-09** |
| DF-29 | Humidity, vibration, ageing of the board | hardware | CCF | no (for the software element) | — | acts on the hardware the integrator qualifies; no software coupling factor | **not plausible** for the element; **AoU-09** |
| DF-30 | Supply transients (cranking, load dump) | as DF-01 | CCF | yes | as DF-01 | — | **partial** (as DF-01) |

### 5.5 T6 — failure of shared external resources

| ID | Initiator → coupling | Fails together | Kind | P? | Measure | Rationale | Status / evidence |
|---|---|---|---|---|---|---|---|
| DF-31 | External crystal missing or broken | SOSC, SPLL → system clock falls back | CCF | yes | **P** bounded waits in the clock driver (PLAT-85: start-up continues instead of hanging with the watchdog off); **D** clock monitor with reset; SM-04 (DF-02) | a board without a working crystal no longer hangs silently in `boardInit()`; the rest is DF-02 | **covered (host)** (`clock-config-s32k-*`, PLAT-85 board open); reaction at start **SYS-50** |
| DF-32 | CAN bus disturbed or controller faulty | SM-31 and SM-32 on one bus; all messages of that bus | CCF | yes | **R** E2E end to end (black channel: CRC, data ID, counter independent of the stack); **D** SM-32 bus state per bus; several buses supervised separately | E2E does not depend on the stack it protects; a bus-wide fault is a loss of messages, which the application sees through the qualifier and its time-out (AoU-11/AoU-12) | **covered** (`e2e`, `e2e-mutations`, `can-bus-state`, board COM-12) — time-outs see DFA-6 |
| DF-33 | SPI master or SPI bus failure | **no stage-1 mechanism**; stage-2 diagnostics of every device on it | CCF | no (stage 1) | none needed in stage 1 (section 6) | no stage-1 mechanism uses SPI; the stage-2 side goes to SYS-28 | **not plausible** for stage 1 (section 6) |
| DF-34 | Debug probe attached in the field | as DF-22/DF-23 | CCF | yes | as DF-22/DF-23 | — | **AoU** (AoU-16) |
| DF-35 | External watchdog / SBC missing (board without SBC) | no second time base outside the MCU | CCF | yes | **R** stage 1 needs no SBC: SM-01, SM-04, SM-06 use three on-chip clocks; the reset-loop limit works without SBC (SYS-43) | a board without an SBC keeps every stage-1 claim; what it loses is independence from the die (AoU-13) | **covered** by design; **AoU-13** |

### 5.6 T7 — stress from specific situations

| ID | Initiator → coupling | Fails together | Kind | P? | Measure | Rationale | Status / evidence |
|---|---|---|---|---|---|---|---|
| DF-36 | CPU overload: the idle context never runs (R-09) | SM-14 run-time re-check stops; console and log drain stop | CF | yes | **none** today: `ROMCHECK_OVERDUE` is computed inside `RomCheck::idle()` (only for postponement by flash writes), so a pass that is never *called* is never overdue; SM-05 reports the overload as S3 only | the safety task keeps running above the load, SM-04 and SM-01 stay quiet, and the run-time protection of the program flash rests on SM-16 alone without any report (ASG-07) | **covered (host)** since SYS-95 (DFA-1): the safety task watches the pass counter of the ROM check against three re-check periods in safety cycles and raises `ROM_CHECK_FAILURE` context 7 (`safety-progress`, `progress-checks`; board SYS95-B1 open) |
| DF-37 | Interrupt storm or long critical sections (e.g. CAN flood) | safety task and all RTE tasks delayed | CCF | yes | **D** SM-04 period part for the safety task (≥ 17 ms); SM-06 for a whole-software stall ≥ 10 ms on boards with a power stage; SM-01; **R** safety task `above_application` | the delay of the safety task is caught on an independent clock; the delay of application tasks is not (F10) | **partial** — covered for the safety task (board B4 2026-09-22: 10 min at load, 9 982 … 10 019 µs); open for application activation → SYS-21 (F10) |
| DF-38 | Safety task blocked for a time that ends before the watchdog fires | every check of the safety task paused | CCF | yes | **D** SM-04 reports at the end of the block; SM-06 if ≥ 10 ms on boards with a power stage | the report comes when the block ends, not at the bound | **partial** → SYS-57 (hardware deadline on the LPTMR compare), unchanged |
| DF-39 | Long operation without restart | latent faults of SM-01 (SM-07 only at start), SM-14 with period 0 | CCF | yes | **D** SM-14 re-check period (SYS-44); proposed AoU-25 | latent-fault intervals of start-up tests equal the operating time | **partial** — covered (host) for SM-14; AoU-25 (proposed in `fmea.md`) for SM-07 |
| DF-40 | A permanent fault answered by S1 + S2 restarts the ECU in a loop | the safe state is left at every restart until the fault is found again | CF | yes | **D** reset-loop limit: S1 held from the N-th safety reset in a row (SYS-43) | — | **covered (host)** (`safe-supervisor`; board B10/B11 open) |

### 5.7 T8 — cascading failures inside the image (software shared resources)

| ID | Initiator → coupling | Fails together | Kind | P? | Measure | Rationale | Status / evidence |
|---|---|---|---|---|---|---|---|
| DF-41 | A QM task or a QM interrupt handler writes the supervisor state or the safety stack (R-07) | all monitors | CF | yes | **D** SM-10 MPU (bus fault → SM-17); SM-11 … SM-13 for the gate and the descriptors; static rule and ELF check (SYS-37); SYS-170 (SYS69-S1, SFP-F01): the gate is open only in the safety context - every interrupt that preempts the cycle runs through a generated `SAFE_ISR` wrapper that closes it first (ELF check `isr-unwrapped`) | spatial FFI **for the supervisor** exists and is board-proven for tasks; for interrupt handlers since SYS-170 (until then only FTFC, ERM and CAN message buffers were wrapped) | **covered** (`safe-supervisor-mpu`, `safety-gate`, board 2026-09-21); interrupt part: host (`bswgen`, `elfcheck`, `elfcheck-demo`), board proof open (SYS-170) |
| DF-42 | A QM task writes application data a safety-related SWC uses, or a kernel structure (R-07, R-17) | the safety function of the application; the scheduler | CF | yes | **D** stack overflow → `STACK_OVERFLOW` (SYS-80, pattern check at the context switch), `configASSERT` → `RTOS_ASSERT`; **none** for a wild write outside a stack | one address space for all tasks (AoU-17) | **open** → SYS-21 (section 7.1) |
| DF-43 | A QM task delays or starves a safety-related task (R-17) | the application's safety function | CF | yes | **D** SM-05 for overruns (S3); **none** for activation (F10) | temporal FFI for application partitions is missing | **open** → SYS-21 (F10) |
| DF-44 | A DMA master writes the protected RAM (R-07, R-14) | supervisor state | CF | no today | **P** no neXtBSW driver enables DMA (the ADC driver refuses a configuration with `DMAEN`); MPU master rights: descriptor 0 grants the DMA (M2) rwx over the whole address space, so the MPU does not keep it out (N3 answered by SYS-171, HSI H10) | the coupling factor does not exist in the code at `ae92e89`; it appears with the first DMA user | **not plausible** today; **open** for the first DMA user → SYS-21 (N3) |
| DF-45 | The application and the safety task use the ADC at the same time (R-11) | a conversion of one aborted or read by the other | CF | yes | **P** serialized access with nested-preemption handling (IO-14, `AnalogInput`, `adc-access`) | the shared driver prevents the interference at its root | **covered** (`adc-access`, `adc-access-averaging`) |
| DF-46 | A stuck ADC converter returns a plausible, constant code (R-11) | SM-18 computes a plausible VREFH, SM-19 sees no read failure, SM-20 scales with it, SM-30 sees in-range values | CCF | yes | **D** SM-19 only for failed conversions; **none** for a stuck result | reference and measured values share the converter; the reference check cannot see a converter that answers with the same code for every channel | **partial** since SYS-95 (DFA-3): the converter that carries the bandgap compares it with the internal supply channel in the same cycle - a common code or a supply result that does not follow the multiplexer is `ADC_REFERENCE_ERROR` context 3 (`safe-system`); a second converter with safety inputs (ADC1) has no internal check yet, and the core-supply step cannot tell one code from the other |
| DF-47 | Clock gate of a PWM timer switched off (R-16) | the FTM stops, its pins freeze; the SM-23 walk **skips** a gated peripheral | CF | yes | **D** SYS-24 class `CLOCK_GATE` (SM-22) detects it within one safety cycle; the reaction S1 then de-energises only through the `safe_off` outputs of other peripherals (for example the gate-driver enable on a GPIO port) | the fault disables exactly the part of the reaction it triggers: a PWM group without an enable on another peripheral stays where it froze | **partial** since SYS-95 (DFA-4 a): `nxconfig` warns with the YAML path for a used PWM group without a bound `safe_off` output (`nxconfig-pwmgate`); part (b), whether the walk opens the clock gate first, stays with **SYS-30** |

Not plausible, checked and recorded so they are not searched again:

- **CRC unit shared (R-12):** reserved for SM-14; no other user in the repository (`fmea.md` SM-14). A second user
  would have to be serialised with the pass.
- **PCR lock (SM-21) blocks the S1 walk:** the walk writes `SWOCTRL` and the GPIO data registers, never a PCR; the
  lock cannot stand in its way.
- **Humidity, vibration, ageing (DF-29)** and **DMA (DF-44)** as above.

---

## 6. SPI as a common cause (F5)

`fmea.md` section 5.3 decided that the SPI master is **not a stage-1 problem**. This analysis confirms the decision
with the dependent-failure view and names what stays open for the other tasks.

**Stage 1 does not depend on SPI.** None of the mechanisms of section 4 uses R-19. The safe outputs walk writes port
and FTM registers; the gate-driver enable is a GPIO declared `safe_off` (TMC6200 `drv_en`), so S1 takes effect
without any SPI transfer. The only SPI-dependent function near a stage-1 mechanism is the SBC watchdog trigger, and
its failure ends in an SBC reset — a safe direction (stage 2, SM-42).

**No SPI failure can undo S1.** A wrong command to the gate driver (wrong chip select, corrupted frame, a QM task
writing the device) cannot energise the bridge while `DRV_EN` is held inactive by the table walk, because the enable
does not go over SPI. That is the property that makes the SPI a stage-2 common cause and not a stage-1 one; it holds
as long as every power stage has a `safe_off` enable outside the bus (AoU-06, and DFA-4 for the PWM side).

**What SPI couples in stage 2 (for SYS-28).**

| ID | Initiator | Fails together | Measure today | For whom |
|---|---|---|---|---|
| SPI-1 | one LPSPI instance or its bus fails | the diagnostics of every device on that bus | SM-34 correlates communication faults of two devices on one bus within 20 safety cycles into one "bus lost" (SYS-29) and sets `QLF_ERROR` on their values | SYS-28 per package: the reaction to a lost bus (a package that needs a switching-off reaction detects it in the safety task, decision `sys37-postfach-abschaltend`) |
| SPI-2 | all LPSPI instances fail together | device diagnostics on every bus | none beyond SPI-1 per bus | SYS-28: the common causes of the instances — **same driver code** (`platforms/s32k1xx/bsp/bspLpspi`), **same functional clock source**, **same system time for the transfer time-out**, supply of the pin ports |
| SPI-3 | the OpenBSW system time stops | every SPI transfer waits without bound (`Lpspi.cpp` times out on `getSystemTimeUs32Bit()`) | the calling task stalls → SM-06 (BSP task) or SM-01 | DFA-6 |
| SPI-4 | two tasks use one SPI master | frames interleave | no bus arbitration exists; the board model names **one device per `spi[]` entry** (eval board: three instances, one device each), so it does not occur today | SYS-21/SYS-28: an arbitration (or a rule in `nxconfig` that refuses two users of one master from different tasks) before a board puts two devices on one bus |
| SPI-5 | the SPI master is a resource of a QM partition and of a safety-related one | a QM driver reconfigures or blocks the master | none (one address space, AoU-17) | SYS-21: shared resource between partitions |

**Result.** No measure is needed in stage 1. The proposed additions go to SYS-28 (SPI-1, SPI-2) and SYS-21 (SPI-4,
SPI-5); SPI-3 is part of DFA-6.

---

## 7. Hand-over to other tasks

### 7.1 SYS-21 — freedom from interference (coordination point of this task)

SYS-21 is `open` and has not started; this section is what it should take over. The DFA is considered consistent
with SYS-21 once the SYS-21 agent confirms each row (either "carried" with its measure, or "rejected" with a reason
this document then records).

| DF | What SYS-21 must show | Kind of FFI (6-Annex D, own words) |
|---|---|---|
| DF-42 | partitions with MPU regions from the configuration; a write from a QM partition into a safety-related one is a fault reported to the supervisor, not only a hard fault | spatial |
| DF-43 | activation supervision of the RTE tasks (F10) and an execution budget per partition, building on SM-05 | temporal |
| DF-44 | MPU master rights of the DMA (N3) before the first DMA user; a configuration check that refuses a DMA user without them | spatial |
| DF-37 | the priority of the safety task above every application partition stays a checked rule (`above_application`) | temporal |
| SPI-4, SPI-5 | the SPI master as a shared resource: arbitration, ownership per partition | exchange of information / shared resource |
| DF-45 | the ADC lock of IO-14 as the model for every shared peripheral (the same pattern for SPI, CAN) | shared resource |
| DF-14 | the partition table is generated from the configuration: the DFA-5 claim limit applies to it as well (the MPU check compares with the generated table) | — |
| DF-18 | the partitioning element (FreeRTOS, MPU code) is itself ASIL B (decision iso-q4) — the single reaction function stays in the safety partition | — |
| DFA-1 | if SYS-21 introduces execution budgets, the idle context's share (ROM re-check) must not be budgeted away silently | temporal |

### 7.2 SYS-50 and SYS-55 — clock plan

DF-02: SM-04 detects large clock errors on the independent LPO; the band it cannot see (about −17 % … +32 % of the
system clock) needs the register-level comparison of the clock plan. Addition: **SYS-55** class `SYSTEM_CLOCK`
(SCG `CSR` against the plan, valid flags of the planned sources every safety cycle) and **SYS-50** (reaction at start
instead of a report). With DFA-2 the class should also carry `SIRCVLD` (or the LPIT's source) — the clock of SM-06.

### 7.3 SYS-73 — ECC latent test and the MCU safety manual

DF-10 (ECC blind) is already its scope. Addition from DF-01 and DF-06: when the S32K1 safety manual is available,
its list of required software measures is to be checked in particular for **(a)** a software test of the core
(DF-06) and **(b)** the configuration of the low-voltage detection and reset (DF-01). Both are the only dependent
failures of stage 1 for which the element has no measure of its own.

### 7.4 SYS-66 — hardware-software interface

Entries the HSI should carry because the DFA relies on them: the clock source of every supervision peripheral (LPO
for WDOG and LPTMR0, the LPIT functional clock of mcudb `clock.pcc.LPIT`), the clock monitors with reset
(`SOSCCMRE`, `SPLLCMRE`), the PMC low-voltage configuration (reset values, not written by neXtBSW), FTM
`CONF.BDMMODE`, the debug behaviour of WDOG, LPTMR and LPIT, and the data-sheet LPO range with its source (DF-15).

### 7.5 SYS-28, SYS-74, DOC-19

- **SYS-28:** SPI-1 and SPI-2 (section 6); the DFA of each device package continues this document's method.
- **SYS-74:** DF-23 is one more reason for the product build without test commands.
- **DOC-19:** the claim limit of DFA-5 and the proposed AoUs of section 9 belong into the safety manual.

---

## 8. New findings of this analysis

| ID | Finding | Why it matters | Proposed measure | Proposed task (for the coordinator) |
|---|---|---|---|---|
| **DFA-1** | The run-time re-check of the ROM check (SM-14) is only watched from inside the idle context: `RomCheck::idle()` computes `ROMCHECK_OVERDUE` only for a pass postponed by flash writes. If the idle context does not run (CPU overload, a task that never blocks), no pass is started and nothing reports it (DF-36) | ASG-07 at run time silently falls back to SM-16 alone; the overload itself is only an S3 log (SM-05) | the **safety task** checks that the pass counter advanced within a deadline derived from the period (for example two periods plus the longest pass), and raises `ROM_CHECK_FAILURE` with context "overdue, not called" (S1 held like the other ROM-check events, or S1 + S2 — reaction to decide); period 0 excluded. Host test with built-in faults (idle never called) | P2 — `platforms/s32k1xx/port/safety/safeMemory`, `platforms/s32k1xx/port/safety/safeLifecycle`, `ecu/safety/safeSupervisor` (context) |
| **DFA-2** | A stopped LPIT functional clock (source switched off, PCC gate) stops the halt deadman SM-06 without any event: the configuration re-check (SYS-41/SYS-48) compares registers, not whether the counter moves. On ARMv7-M nothing else notices (DF-05) | SM-06 becomes latent until the next stall, which it then misses (ASG-01 termination path, halt case) | the safety cycle reads `CVAL1` twice with the retrigger in between, or compares it with the previous cycle's value, and raises `DEADMAN_CONFIGURATION_ERROR` with a new field "counter stands still"; alternatively the valid flag of the LPIT's clock source in the `SYSTEM_CLOCK` class (SYS-55) | P2 — `platforms/s32k1xx/port/safety/safeSupervisionCheck`, `platforms/s32k1xx/port/main/src/integration` |
| **DFA-3** | A stuck ADC converter that returns one plausible code for every channel blinds SM-18, SM-19, SM-20 and SM-30 together (DF-46); the bandgap reading alone cannot tell a stuck converter from a good one | the reference and supply supervision and every analogue safety input fail together, silently (ASG-03 frame, 11-05 of the gap analysis) | read a **second internal channel with a clearly different expected code** in the same safety cycle (for example the high or low reference of the converter — which internal channels the S32K1 ADC offers is to be confirmed in the reference manual by the HSI, SYS-66) and require both codes in their bands; a result that does not change between two different channels is a converter fault (context 3 of `ADC_REFERENCE_ERROR`). Host test with a converter model that sticks | P2 — `platforms/s32k1xx/port/safety/safeSystem`; with SYS-66 |
| **DFA-4** | The SM-23 walk skips a peripheral whose clock is gated. The SYS-24 class `CLOCK_GATE` detects a gated PWM timer and raises S1 — whose PWM part then does nothing for that timer, because the same fault gates it (DF-47) | a PWM group without an enable on another peripheral stays at whatever level the timer froze; ASG-01 | two parts: **(a)** a `nxconfig` warning with the YAML path of the PWM group when no `safe_off` output on another peripheral de-energises its power stage (the stage-1 argument then rests on AoU-07 alone for that group); **(b)** for SYS-30: decide whether the walk re-enables the clock gate before writing `SWOCTRL` (needs the question whether an FTM output follows `SWOCTRL` immediately after the gate is opened, reference manual) or switches the pins to GPIO inactive — the PCR lock of SM-21 would forbid the latter, so (b) is the gate | (a) P2 — `tools/nxconfig/checks.py`; (b) addition to **SYS-30** |
| **DFA-5** | Every run-time comparison of stage 1 (SM-13 MPU descriptors, SM-21/SM-22 register list, the safe outputs table of SM-23) takes its expected values from the **same generated data** that configured the hardware (DF-14). A wrong design value, a generator fault or a wrong derivative fact is configured and expected alike | the claim of these mechanisms is "detects a change after start", not "detects a wrong configuration"; without saying so, a reader could credit them for the second | no code change. State the claim limit in the safety manual (DOC-19) and in `fmea.md` (with DOC-21 step 3); the design side is covered by tool confidence (PLAT-75), generator tests with own profiles and mutants, and board tests of the generated result per project (SYS-75); proposed **AoU-DFA-c** (section 9) | DOC-19 / DOC-21 addition |
| **DFA-6** | The time-outs of the SPI transfers (`Lpspi.cpp`) and of the CAN reception supervision count on the OpenBSW system time. If the system time stops (for example `DEMCR.TRCENA` cleared, PLAT-67, or DFA-2 on ARMv6-M), every such wait is unbounded and every reception time-out stays silent (SPI-3) | a single initiator removes all time-outs of the communication layer at once; the stall that follows is caught only by SM-06/SM-01 if the waiting task is one they supervise | **(a)** a supervision of the system time from the safety cycle: the system time must advance by about one period per cycle, measured against the SM-04 LPTMR (the check would reuse the SM-04 time base, no new peripheral); **(b)** SPI waits additionally bounded by a poll budget like the clock driver of PLAT-85 | P2 — `platforms/s32k1xx/port/safety/safeLifecycle` (a), `platforms/s32k1xx/bsp/bspLpspi` (b) |

---

### 8.1 Implementation status (SYS-95, 2026-10-03)

<!-- dfa-status:start -->
| Finding | Status | What was done | Evidence (mock/host) | Open |
|---|---|---|---|---|
| DFA-1 | implemented | `platforms/s32k1xx/port/safety/safeLifecycle` `ProgressSupervision.cpp`: the safety cycle samples the pass counter of the ROM check (`RomCheck::statistics().passes`) in every cycle; when it stood still for more than three re-check periods in safety cycles (one until the pass is due, one that RomCheck itself grants flash writes before `ROMCHECK_OVERDUE`, one for the pass and the idle context) it raises `ROM_CHECK_FAILURE` with context 7 "not called" - S1 held like every other ROM-check event. Only in an image that runs the re-check (`NX_ROM_CHECK_IMAGE`) and with a period other than 0; not after the ROM check reported itself. The counting is hardware independent (`ecu/safety/safeWatchdog` `ProgressChecks.h`, `ProgressDeadline`) | `progress-checks`, `safety-progress` | board SYS95-B1; with period 0 the first pass is not watched (AoU-25 unchanged) |
| DFA-2 | implemented | `safeSupervisionCheck` `LpitDeadmanProgress.cpp`: with the channel enabled the counter of the deadman must change within a budget of polls that spans two LPIT ticks at the core clock of the project (derived from the clock plan, 22 polls on the eval board); a counter that stands still is `DEADMAN_CONFIGURATION_ERROR` with the new field 0x100 (S1 + S2). A channel disabled by the retrigger or by an expiry gives no verdict in that cycle | `lpit-deadman-progress`, `safety-progress` | board 2026-10-04: proven with a slowed LPIT clock (`SIRCDIV2` /64, field 0x100) - the check also sees an LPIT clock that is too slow; a gated clock is a bus fault caught by SM-17 (board 2026-10-03); a lost SIRC is a residual risk (DF-05, SYS-195); O4 answered: `CVAL` is readable with the clock stopped (core halted). The alternative of the finding (`SIRCVLD` in the `SYSTEM_CLOCK` class) stays with SYS-55 |
| DFA-3 | implemented, partial | `safeSystem` `SafeSystem.cpp` `checkConverterSeparation()`: the second internal channel is the internal supply channel the cycle reads anyway (`INTERNAL0`), not `VREFSH`/`VREFSL` - those would need a new `adc_internal` entry in every project and in the port manifest (`port.yaml`), outside this task. The two raw codes of one cycle must stand on the expected side of twice the bandgap code: above it for the 3.3 V supplies, below it for the 1.2 V core; otherwise `ADC_REFERENCE_ERROR` context 3 (S1 + S2). A converter that answers both channels alike is reported within three cycles, one that stopped converting at the core step | `safe-system` | board SYS95-B3 (no false alarm over the supply steps); the core step cannot tell one code from the other; a second converter with safety inputs (ADC1) is unchecked - proposal SYS95-P3 |
| DFA-4 | (a) implemented, (b) open | `tools/nxconfig/pwmgate.py`: a used PWM group without a bound `safe_off` output (board `pwm[].gate_driver` and a `safe_off` connection of its module, IO-45) is a warning with the YAML path `pwm.<group>` that names AoU-07. Six test profiles of the tree get the warning, the eval board demo does not | `nxconfig-pwmgate` | (b) whether the walk opens the clock gate before it writes `SWOCTRL` - decision for **SYS-30** (proposal SYS95-P4) |
| DFA-5 | done (no code) | the claim limit is stated in the safety manual 10.2; **AoU-DFA-a … AoU-DFA-c** are rows of its section 7 (status proposed) and `test_safety_manual.py` requires them from section 9 of this document | `safety-manual`, `safety-dfa` | acceptance of the three AoUs (safety review); `fmea.md` keeps its wording (DOC-21 step 3 owns the catalogue) |
| DFA-6 | implemented | (a) `ProgressSupervision.cpp`: over the window of the period check the OpenBSW system time must advance like the LPTMR on LPO1K within the LPO range of the derivative, with a slack of one period plus the lateness for the one-off loss after a debugger exit (PLAT-67); a slow system time is `SAFETY_CYCLE_PERIOD_VIOLATION` kind 4, a fast one kind 5 (S1 + S2), at the end of a window (eval board: 7 cycles, 70 ms). *Corrected after the board (DOC-49, safety review SYS-95 board 2026-10-04, B5-1 a):* a **stopped** system time is **not** kind 4 - the safety task is scheduled through exactly this time base and does not run any more; the halt deadman SM-06 walks the table after about 10 ms and the watchdog SM-01 resets after about 160 … 283 ms, or, once the system time runs again (debugger halt and resume, PLAT-67), the period check resets. (b) `bspLpspi` `Lpspi.cpp`: every wait is bounded by a poll budget as well (112 polls per µs of the time-out, the fastest core of the family), so a transfer ends also when the system time stands still | `progress-checks`, `safety-progress`, `lpspi` | board SYS95-B4 passed (no false alarm, also across a J-Link exit); SYS95-B5 (DWT `CYCCNT` stopped): safe reaction, not by kind 4 - 2026-10-03 `WATCHDOG RESET: outputs off`, 2026-10-04 SM-06 expired (`_expired` = 1, `T_EN` = 0, `CYCCNT` standing), the reset then came as kind 1 because the debugger's halt and resume restarted the system time (PLAT-67); a wake-up of the safety task independent of the system time is for after release R1. The CAN reception time-outs stay on the system time and are supervised through (a) for a slow or fast one |
<!-- dfa-status:end -->

## 9. Proposed assumptions of use

Numbers are assigned by the coordinator (AoU-24/25 are proposed in `fmea.md`, AoU-26 … AoU-29 in
`tool-classification.md`, AoU-30 is in the concept; other tasks of the same wave may propose more).

| ID | Assumption |
|---|---|
| AoU-DFA-a | The integrator confirms that the MCU's own power-on and low-voltage reset (or an external supply supervisor) brings the MCU into reset **before** its supply leaves the range in which the MCU manufacturer guarantees correct operation. neXtBSW's supply check (SM-20) runs on the same die and is credited only for a drift while the die still works correctly (DF-01). |
| AoU-DFA-b | The stage-1 mechanisms detect faults that stop, divert or delay the program flow, or raise an exception, on an independent time base. They do **not** detect a core fault that computes wrong results on a correct program flow (DF-06). If the integrator's hardware metrics need that coverage, he provides it — a core self-test required by the MCU manufacturer's safety manual, an external challenge-response watchdog (AoU-13) or a second channel. |
| AoU-DFA-c | The run-time register, MPU and safe-outputs checks compare with the configured values: they detect a **change after start**, not a **wrong configuration** (DFA-5). The integrator verifies the configuration itself — inactive levels, `safe_off` declarations, dead times, MPU regions — against his design and on his hardware (AoU-06, AoU-07, AoU-10). |

---

## 10. Verification of this analysis

### 10.1 What was checked

| Check | Result |
|---|---|
| Every stage-1 mechanism of `fmea.md` section 3 appears in section 4 as relying on at least one resource | yes — script, section 10.2 |
| Every pair of section 2 has its coupling factors in section 4 and at least one row in section 5 | yes, by reading: I-01 DF-02/DF-03/DF-07; I-02 DF-02/DF-04; I-03 DF-05/DF-22; I-04 DF-41; I-05 DF-18/DF-19/DF-47; I-06 DF-17; I-07 DF-39; I-08 DF-08/DF-25/DF-36; I-09 DF-10; I-10 DF-46; I-11 DF-32; I-12 DF-20/DF-21; I-13 DF-11/DF-40; I-14 DF-14/DF-47 |
| Every plausible row names a measure or a status that says why there is none | yes — script counts the status words |
| Every SM, task, test and finding ID cited exists (concept, fmea, `doc/todo.yaml`, `tools/test/tests.yaml`) | yes — script |
| No ISO text, no percentages as coverage | yes — script checks for coverage percentages; the only percentages are the clock-error band of DF-02 and section 7.2, which are timing tolerances, not coverage |
| The claims about the code were read at `ae92e89` | `RomCheck.cpp` (overdue only inside `idle()`), `SafetyManager.cpp` (no ROM-check progress check), `clockConfig.cpp` (clock monitors with reset), `Lpspi.cpp` (time-out on the system time), `AnalogInput.cpp` (IO-14 lock, `DMAEN` refused), `SafeSystem.cpp` (bandgap only), `LpitHaltDeadman.h`/`SystemTimer.cpp` (LPIT clock from mcudb, channel 0 as ARMv6-M system time), `ecu/asyncCoreConfiguration/tasks.yaml` (`above_application`), `boards/evalboard_s32k144/board.yaml` (one device per SPI master) |
| Cross-check with the gap analysis | rows 9-03, 9-04, 9-05, 9-11, 6-16, 10-07 and 11-02 of `iso26262-gap.md` are each answered by sections 2–8; their "partial"/"missing" would become "partial" (independent review and SYS-21 confirmation still missing) |

### 10.2 Consistency script

The checks marked "script" were run with a small Python script (no dependencies beyond PyYAML) that reads this file,
`concept.md`, `fmea.md`, `doc/todo.yaml` and `tools/test/tests.yaml`. *Since SYS-95* it is checked in as
[`test/test_dfa.py`](test/test_dfa.py) (test `safety-dfa`): every mechanism, task and test ID cited here exists, every
row of section 5 has a status word of section 1.3 and the counts of section 0 match them, every finding DFA-1 …
DFA-6 has its row in section 8.1, every proposed AoU of section 9 is in the safety manual, no coverage percentage,
the licence rule - with counter-checks on broken copies.

### 10.3 What is not verified

| ID | Open point |
|---|---|
| O1 | Completeness of the topic list (section 3) against 9-7 and its informative annex — needs a reader of the licensed standard |
| O2 | Independent review (I1) of this analysis — SYS-63 (decision iso-q1: reviewer later) |
| O3 | Confirmation of section 7.1 by the SYS-21 agent |
| O4 | Register facts named in DFA-2 … DFA-4 (LPIT counter read, internal ADC channels, FTM output behaviour after re-enabling the clock gate) — to be confirmed in the reference manual by SYS-66 or by the implementing task. *SYS-95:* the internal channels are taken from `mcudb` (`internal_channels`, [RM13] 44.4.2); the LPIT counter read with its clock off: `CVAL1` and `TCTRL1` are readable with SIRC off and the core halted (board 2026-10-04, DOC-49); the FTM behaviour after the gate stays with SYS-30 |
| O5 | The PMC low-voltage configuration at reset (DF-01) — HSI, SYS-66 |
| O6 | *Found by the script of SYS-95:* mechanisms that `fmea.md` gained after the baseline `ae92e89` of this analysis are not yet assigned to a shared resource of section 4 - SM-26, SM-27 (selectable safe state and its start test), SM-55 (ECC latent test), SM-58 (hardware threshold), SM-59 (LVD reset), SM-60 (FlexNVM lock); for the next issue of this analysis |

---

## 11. Review questions of the architecture (section 11)

This task changed no code, no generator, no configuration and no bootloader: one new document. The questions are
answered for the analysis and for the proposed measures.

1. **Board without an SBC?** Yes. No stage-1 measure relies on the SBC (DF-35); the independence the analysis credits
   comes from three on-chip clocks. The loss of independence from the die without an external watchdog is stated
   (AoU-13, AoU-DFA-b).
2. **Different crystal and system clock?** Yes. The clock-error band of DF-02 is stated relative to the nominal
   period, not to 80 MHz; DFA-6 (a) and DFA-2 compare against the LPO, not against a frequency constant.
3. **Different derivative, S32K3 / STM32 / Cortex-R?** Yes. The analysis is by resource class (supply, clocks,
   memories, timers); the S32K1 registers are named as the instance of the family port. Another family repeats the
   resource list of section 4 with its own peripherals; DFA-2 and DFA-3 are family-port measures.
4. **Two or more CAN buses?** Yes. DF-32 and SPI-1 are per bus; SM-32 and SM-34 are per bus instance.
5. **Several tasks and free priorities?** Yes; DF-36, DF-37, DF-43 and DFA-1 are exactly about what a task set can
   do to the mechanisms. The only fixed rule used is `above_application` for the safety task, which the
   configuration already checks.
6. **Every new value in one configuration level?** No new value. DFA-1's deadline is derived from the existing module
   parameter `modules.safeMemory.rom_check_period_ms`; DFA-6 (a) derives its bound from the safety task period and
   the LPO range already in the configuration.
7. **Host test with its own test profile?** Each proposed measure names its host test with built-in faults. The
   document itself is checked by the consistency script (section 10.2).
8. **Tool core free of Qt, messages with a YAML path?** DFA-4 (a) is an `nxconfig` warning with the YAML path of the
   PWM group.
9. **Device driver granularity?** Not applicable — no driver changed; the SPI part only hands SPI-1/SPI-2 to SYS-28.
10. **Placement under `platforms/`?** Not applicable — no new file there.

---

## 12. Sources

- Repository at `ae92e89`: [`concept.md`](concept.md) issue 2 with its updates, [`fmea.md`](fmea.md) with the
  SYS-40 … SYS-53 updates and the board results of section 8.1, `iso26262-gap.md` (not published, available on request) (sections 2.4,
  2.6, 6), `rtos-comparison.md` (not published, available on request) (SYS-78/SYS-80), [`hardware-mechanisms.md`](hardware-mechanisms.md),
  `doc/todo.yaml` (SYS-21, SYS-24, SYS-28, SYS-30, SYS-48, SYS-50, SYS-55, SYS-57, SYS-62, SYS-63, SYS-66, SYS-69,
  SYS-73, SYS-74, SYS-76), `CHANGELOG.md` (SYS-24, PLAT-85), `tools/test/tests.yaml`.
- Code: `platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/RomCheck.cpp`,
  `platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/SafetyManager.cpp`,
  `platforms/s32k1xx/port/safety/safeSystem/src/safeSystem/SafeSystem.cpp`,
  `platforms/s32k1xx/bsp/bspClock/src/clockConfig.cpp`, `platforms/s32k1xx/bsp/bspLpspi/src/spi/Lpspi.cpp`,
  `platforms/s32k1xx/port/bspConfiguration/src/bsp/adc/AnalogInput.cpp`,
  `platforms/s32k1xx/port/bspConfiguration/src/bsp/SystemTimer/SystemTimer.cpp`,
  `platforms/s32k1xx/port/main/src/integration/{LpitHaltDeadman.h,Integration.cpp}`,
  `platforms/s32k1xx/port/main/src/main.cpp`, `ecu/asyncCoreConfiguration/tasks.yaml`,
  `boards/evalboard_s32k144/board.yaml`, `mcudb/s32k1xx/*.yaml` (`clock.pcc.LPIT`, `clock.lpo`).
- ISO 26262:2018 parts 6 and 9 by part and clause only, through the gap analysis; the standard itself was not read
  for this document (O1).
