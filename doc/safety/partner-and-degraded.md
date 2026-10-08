# Partner MCU supervision, degraded reaction and active short circuit — concept

Baseline: v0.2.0

Status: 2026-09-29, **issue 1, draft**, checked against the night state `integ/night2` at `7af5b7f` — concept for **SYS-115**, **SYS-117** and **SYS-116** together, written in the
night without the user; every choice that is his is written as a numbered decision with options and a recommendation
(section 6). **No code, no schema and no other document was changed.** The changes this concept proposes to
[`concept.md`](concept.md), [`fmea.md`](fmea.md) and [`ssr.sdoc`](../requirements/safety/ssr.sdoc) are listed in
section 7, not made.
**Decisions and implementation (2026-09-30).** The user took all 18 recommendations on 2026-09-29 (decision board
`q-sys115-116-117`: A1-A6, B1-B6, C1-C6 each in the recommended variant).

*Implementation notes of parts B and C (SYS-117, SYS-123, IO-45, SYS-116, SYS-140, SYS-139) (not published, available on request)*

Readers: the user as architect and safety owner, the coordinator, the agents who implement SYS-115/116/117 after the
decisions, and whoever works on SYS-85, SYS-86, IO-45, IO-64, BL-17 and ARCH-65's follow-up tasks.
Language: English, like the rest of `doc/`.

Occasion (user, session a5, 2026-09-29): an ECU of **two MCUs** (master and slave; in the real motor hardware the
slave has no system basis chip and no external watchdog, and the link is SPI), **two redundant drives** (one motor
keeps braking when the other fails) and a **motor inverter without a mechanical brake**. neXtBSW today is fail-safe
only (concept section 3, AoU-04), supervises exactly one MCU, and knows one safe state of a PWM group — all
switches off (freewheel).

**Rules kept.** Board neutrality: the hardware of the user's power unit stays outside neXtBSW; no value of it
appears here, and the eval board is at most an example. No invented numbers (concept section 0): every time is a
symbol, a configured value or a derived one; the one worked example in section 3.7 is labelled as hypothetical. No
text or clause of a standard is cited. Stage separation (concept section 1.4): what is credited in stage 1 must
detect and react with the MCU and its configuration; the partner MCU is an MCU running neXtBSW, not a device.

---

## 0. Result in one page

- **Partner supervision (SYS-115).** The master asks, the slave answers: a question-and-answer watchdog over the
  inter-MCU link. The answer is a function of the question **and** of the slave's own program-flow evidence (its
  safety cycle completed, its deadman armed and not expired, its fast loop alive), so a slave that is stuck, runs
  wild or skips its checks cannot produce it. Late, early, wrong or missing answers raise an error counter; at its
  threshold the master **cuts a hardware shut-off line** that disables the slave's power stage outside the slave
  (board fact), and raises an event with a project-chosen reaction. The slave supervises the master by the arrival
  of the questions and enters its own safe state when they stop. The shut-off line is a `safe_off` output of the
  master, so every switch-off path of the master (safe state, fault handlers, halt deadman, its reset) cuts it too.
- **Link neutral and bootloader compatible.** The supervision is one short message class on whatever transport
  carries messages between the MCUs (the SPI link `mcuLink` today, CAN possible). A slave in its bootloader answers
  a question with the bootloader's ordinary "service not supported" — **no byte of bootloader code is needed**, and
  a bootloader can never produce a valid answer, so "partner in bootloader" is seen, and the line stays cut.
- **Proof against the fault tolerance time.** Question period, answer window, error counter and the integrator's
  fault tolerance time are project values in milliseconds; `nxconfig` derives the worst-case detection and reaction
  time and refuses a configuration that does not fit (section 3.7).
- **Degraded reaction (SYS-117).** A new reaction class **SD — degraded**: the affected **safe-output group** goes to
  its safe state, the event goes to the error memory and to the application as an RTE signal, the rest keeps
  running, no reset. Which events may be degraded is a **closed list in the supervisor** — faults of the MCU itself
  (ROM, RAM, ECC, MPU, watchdog, program flow, kernel, clock, supply) stay fail-safe; device faults, partner faults
  and fast-loop faults may be degraded. The mapping event → group is a project value; `nxconfig` refuses a mapping of
  a fail-safe event. The degraded state is latched until the power cycle and survives a safety reset.
- **Active short circuit (SYS-116).** Per PWM group a project value lists the allowed safe states
  (`freewheel`, `active_short_low`, `active_short_high`); the application selects the currently valid one at run time
  (for example by speed); every switch-off path applies the selection. Anything that makes the selection
  untrustworthy or unavailable falls back to **freewheel**: an invalid selection word, a gated timer clock, a bridge
  fault, the hardware fault input (which forces freewheel in hardware), and the reset. The gate-driver enable of a
  group in active short stays on — the one real conflict with today's `safe_off` rule, resolved by binding enables to
  their PWM group. A start-up test builds the short at standstill and reads it back.
- **18 decisions** for the user (section 6), each with a recommendation; **proposed changes** to concept, FMEA,
  requirements, DFA and the DTC plan (section 7); the implementation tasks (section 8).

---

## 1. Starting point

What exists, checked in the code at `7af5b7f`:

| Topic | Today | Where |
|---|---|---|
| Reactions | S3, S1 (only `CHECK_REGISTER_ERROR`), S1 held, S1 + S2; decided in exactly one place | `SafeSupervisor::handle()`, concept section 3 |
| Safe state of a PWM group | one: every channel of the group forced to its inactive level by the timer's software output control, one store per group | `tools/bswgen/bswgen.py` `collect_safe_outputs`, `nxSafeOutputsSwitchOff()` |
| Safe state of other outputs | driven inactive when a module manifest declares `connect.<name>.safe_off` (a gate-driver enable, for example) | same table, second block |
| Latch of S1 | one word in ordinary RAM; every `safe_off` driver refuses "on" while it is set | `nxSafeStateLatch`, `safeOutputs.h` |
| Several MCUs | flashing only: link protocol `mcuLink` (framing, CRC, go-back-N, session counter), slave bootloader over the link, gateway in the master's bootloader | `multi-mcu-flash.md` (not published, available on request), `libs/bsw/mcuLink`, `bootloader/src/link` |
| Error memory | `libs/bsw/dem`, BSW DTC range with a numbering plan | `tools/nxconfig/bswdtc.py` (TOOL-178) |
| Hardware switch-off | none used; the FTM fault input is catalogued, not configured | [`hardware-mechanisms.md`](hardware-mechanisms.md), SYS-85 |
| Fast loop | concept only; names SYS-115 as the consumer of its liveness counter | `fast-control-loop.md` (not published, available on request) section 10 |

What the three tasks change in the reaction model, in one table (section 2 explains each row):

| Reaction | Outputs | Rest of the ECU | Reset | Ends with |
|---|---|---|---|---|
| S3 report and continue | unchanged | runs | no | — |
| **SD degraded** (new, SYS-117) | the event's safe-output groups to their **selected** safe state | runs, informed | no | power cycle |
| S1 | all groups to their selected safe state | runs (only `CHECK_REGISTER_ERROR`) | no | leave S1 / power cycle |
| S1 held | all groups to their selected safe state, every round | stopped | no | power cycle |
| S1 + S2 | all groups to their selected safe state, then **reset** — the board's reset state afterwards | restarts | yes | restart |

"Selected safe state" is freewheel unless SYS-116 is used; with SYS-116 it is freewheel or an active short per
group (section 5).

---

## 2. Terms

- **Partner.** The other MCU of a two-MCU (or N-MCU) ECU, reached over a link. **Master** asks, **slave** answers;
  the roles are the ones of ARCH-47 (the master has the diagnostic bus), so a project has exactly one place for them.
- **Shut-off line.** A wire from one MCU that disables the power stage of the other MCU **without** that MCU's
  software — for example ANDed into the gate driver's enable, or switching the gate-driver supply. It is a board
  fact of both boards.
- **Safe-output group.** A named set of entries of the safe outputs table that can go to its safe state alone: a PWM
  group plus the `safe_off` outputs bound to it (its gate-driver enable, its high-side supply switch). Today every
  project has exactly one implicit group, "all".
- **Selected safe state.** For a PWM group, the safe state currently chosen out of those the project allows
  (freewheel, active short low, active short high).
- **Fault tolerance time (FTT).** The time the item tolerates a fault before it becomes hazardous. It is the
  integrator's (AoU-01); here it becomes a project value **only as an input to a check**, never as a claim.

---

## 3. Part A — supervision of a partner MCU (SYS-115)

### 3.1 Topology

```
         master MCU                                       slave MCU
 ┌────────────────────────────┐   link (SPI, CAN …)   ┌────────────────────────────┐
 │ safety task:               │  question q(k) ─────▶ │ supervision answer:        │
 │  question, window, counter │  ◀───── answer a(k)   │  a = f(q, program-flow sig)│
 │                            │                       │  master-lost timer         │
 │ shut-off line (safe_off) ──┼──────────────────────▶│ AND gate / gate supply ──▶ │ power stage
 └────────────────────────────┘                       └────────────────────────────┘
```

- **Master supervises slave**: question-and-answer, error counter, shut-off line.
- **Slave supervises master**: no valid question for `master_lost_ms` → event on the slave, own safe state.
- **Several slaves**: one supervision instance per `slaves[]` entry, each with its own line. A slave with its own
  slaves (cascading, ARCH-47 section 13) is out of scope, like there.
- **Mixed families**: nothing below depends on the family; the family appears only in the link driver and in the
  safe outputs table of each side.

The role split is deliberate: only the master clocks the SPI link, so only the master can meter time on it; the
slave sees time as "questions arriving". On a symmetric link (CAN) the same split is kept, so that one project
never has two masters of one question sequence.

### 3.2 Question and answer

One message class on the link, fixed length, one link frame (so it never waits behind itself):

| Field | Question (master → slave) | Answer (slave → master) |
|---|---|---|
| class byte | a reserved value that is **no UDS service identifier** (section 3.8) | the same value + a fixed offset |
| sequence | question number k (8 bit, wraps) | k of the question it answers |
| payload | the question q(k) | the answer a(k) and the slave's **state word** |
| protection | the link's own CRC and session counter; additionally a 16-bit check value over the fields, so that a transport without CRC (or a CRC shared with UDS traffic) does not carry the argument alone | same |

- **The question sequence** is generated by the master from a seed that changes per start (the master's own
  session value of `mcuLink`, section 5 of `mcuLink.md`), so a recorded answer sequence of an earlier run is worth
  nothing.
- **The answer** is `a(k) = F(q(k), S(k))`, where `S(k)` is the slave's **program-flow signature** of the interval
  since question k−1 (section 3.3) and `F` is a fixed mixing function (a CRC over both is sufficient: the point is
  that the answer cannot be produced without `S(k)` having its expected value, not secrecy). The master knows the
  expected `S` because it is a constant of the slave's configuration, generated for both sides from the slave's
  project (one source, like the link parameters in ARCH-47 section 7.5).
- **The state word** carries the slave's reaction state (running, SD degraded with its groups, S1, S1 held is never
  seen because S1 held stops all tasks) and the slave's view of the master (questions arriving or lost). It is
  information for the master's reaction and for the error memory, not part of the check.

### 3.3 What goes into the slave's program-flow signature

The signature is built from checkpoints that only the intended program flow passes, each contributing a fixed value
when passed; a missed checkpoint leaves a wrong signature, and the answer is wrong.

| Checkpoint | Evidence it gives | Existing mechanism it reuses |
|---|---|---|
| every safety cycle since the last question completed its sequence (ENTER … LEAVE) | the safety task runs and runs all its checks | SM-04 sequence monitor |
| the safety cycle period check passed | the safety task runs at its period | SM-04 period part (SYS-40) |
| the halt/stall deadman is armed and has not expired since the last question | the core is not stalled; the deadman's configuration re-check passed | SM-06, its re-check (SYS-41/SYS-48) |
| the fast loop's liveness counter advanced by the expected amount (only if the project has a fast loop) | the PWM-rate loop of ARCH-65 runs | `fast-control-loop.md` (not published, available on request) section 6.4 |
| the answer is computed in the safety context of the slave | the evaluation is not done by a runaway QM task | static rule `safety-context` (SM-10) |

What the signature does **not** prove: that the slave computes its **function** correctly (a core that follows the
right program flow and computes wrong values, DF-06 of [`dfa.md`](dfa.md)). An answer that additionally requires the
slave to compute a small fixed test task (arithmetic over the question) would detect part of that; it is decision
A2 (c) and not the recommendation, because what it proves is hard to state without the MCU manufacturer's safety
manual.

### 3.4 Timing on the master

| Value (project, physical unit) | Meaning |
|---|---|
| `question_period_ms` (T_Q) | one question per T_Q, from the master's safety task or a task the safety task checks |
| `answer_window_ms: [min, max]` (W_min, W_max) | an answer counts only if it arrives after W_min and before W_max, measured from sending the question; W_max ≤ T_Q |
| `error_increment`, `error_threshold` (E_inc, E_max) | every bad answer (missing, late, early, wrong, wrong sequence) adds E_inc, every good answer subtracts 1 (not below 0); reaching E_max is the partner fault |
| `start_answers` (N_start) | good answers in a row before the master enables the shut-off line at start-up (the line starts cut) |

W_min > 0 detects a slave whose answer comes from a path that runs too fast (a loop that answers every question at
once); a window of `[0, max]` turns it off and is allowed.

The counter (rather than "n bad in a row") tolerates a single disturbed exchange without letting a slave that
fails every second answer run forever: with E_inc = 2 and a decrement of 1, a 50 % failure rate reaches any
threshold. Its worst-case number of failures to detection for a permanent fault is `n = ceil(E_max / E_inc)`.

### 3.5 The reaction that does not depend on the hanging MCU

**Board fact, both sides** (ARCH-48 decides the names):

- master board: the output that drives the line (`dio.outputs`), its active level, **and the level the pin has
  while the master is in reset** (pull resistor fitted, or the pin state of the derivative in reset);
- slave board: what the line acts on — `gate_enable` (ANDed into the gate driver's enable), `gate_supply` (switches
  the gate-driver supply), or `pwm_fault_input` (a fault input of the slave's PWM timer, SYS-85) — and optionally an
  input on the slave that reads the line back.

**Rules** that `nxconfig` checks (section 3.9):

1. **Fail-safe polarity.** The line's reset level on the master must be "cut". A master in reset, in its
   bootloader or without power must disable the slave's power stage.
2. **The line is a `safe_off` output of the master.** The supervision module's manifest declares it
   `connect.shutoff.safe_off: true`, so it lands in the master's safe outputs table (SM-23) and gets its adapter
   (SM-24). Every switch-off path of the master — S1, S1 held, the fault handlers, the halt deadman — cuts the line.
   A hanging master therefore also disables the slave's power stage, before its own watchdog reset.
3. **The line is enabled only by the supervision**, and only after N_start good answers; it is cut at the partner
   fault and stays cut until the recovery rule allows otherwise (decision A4).
4. **`pwm_fault_input` is weaker** than the other two, because it relies on the slave's own timer configuration,
   which is set up by the slave's software — the supervised party. It is allowed with a warning and a reasoned
   confirmation in the project (decision A6).

Why a line and not a message: a slave that hangs with its PWM running cannot be told anything. Only hardware
outside the slave's software can switch its bridge off; the line is the smallest such hardware.

**The master's event.** At the partner fault the master raises `PARTNER_FAULT` (context: slave index, cause —
missing, late, early, wrong, sequence, peer restart, in bootloader — and the last state word). Its reaction on the
master is a project value (decision A3): SD degraded (the master's own function continues; needs SYS-117), S1, or
S1 + S2. The shut-off line is cut **before** the event is handled, by the supervision itself, so the reaction
choice never delays it.

### 3.6 The slave's side

- **Master lost.** No valid question (right class, next sequence number, check value correct) for `master_lost_ms`
  → event `PARTNER_LOST` on the slave. Reaction per the slave's project: S1 (recommended default), S1 held, or SD
  degraded if the slave's function may continue alone (redundant drives: decision B2 lists it as degradable).
- **Line read-back (optional).** If the slave board wires the line to an input as well, the slave raises
  `PARTNER_LOST` with cause "line cut" when it reads the line cut while its own supervision is fine — the master has
  decided, and the slave's software should agree with its hardware instead of commanding a bridge that cannot
  switch.
- **The slave's watchdog.** A slave without an external watchdog keeps SM-01 (same clock domain, AoU-13). The
  master's questions come from another MCU with its own clock, so the partner supervision gives the slave the
  **independent time base** that AoU-13 asks the integrator for — but only for the power stage (via the line), not
  as a reset source. This is worth saying in AoU-13 (section 7.1).

### 3.7 Proof against the fault tolerance time

Worst case for a permanent fault of the slave that starts right after a good answer:

```
detection  D = n · T_Q + W_max + T_eval          n = ceil(E_max / E_inc)
                                                 T_eval = period of the master task that evaluates (safety task)
reaction   R = T_line                            T_line = board fact: line change -> power stage off
check      D + R + T_margin  <  FTT_partner      T_margin = project value for jitter the element cannot see
```

On the slave's side for a master that stops: `D_s = master_lost_ms + T_eval_s`, and in parallel the hardware path:
the master's own switch-off paths cut the line (section 3.5 rule 2) within the master's reaction times of concept
section 7.2.

`nxconfig` computes D + R from the project values and refuses the configuration when it reaches `FTT_partner`
(error with YAML path `slaves[<n>].supervision.fault_tolerance_time_ms`). `T_line` is a board value in
microseconds; without it the check uses 0 and warns that the board does not state it.

*Worked example, hypothetical values only, not a recommendation:* T_Q = 10 ms, W_max = 8 ms, E_inc = 2,
E_max = 4 → n = 2; T_eval = 10 ms; D = 20 + 8 + 10 = 38 ms. With T_line in the microsecond range the item would need
an FTT above about 40 ms plus its margin.

This is the same form as AoU-05 for the single-MCU mechanisms; the difference is that here the element can check it,
because the integrator's FTT enters as a value.

### 3.8 Link neutrality and the SPI bootloader transport

**Neutral contract.** The supervision needs from the transport: send a message of a few bytes, receive one, and be
told of a peer restart. `mcuLink` gives all three (`mcuLink_send`, `mcuLink_receive`, `MCULINK_EVT_PEER_RESTART`);
a CAN link gives the first two with a pair of identifiers and the third through the sequence number of the
question. Nothing above the contract names SPI.

**Sharing the link with UDS.** `mcuLink` carries whole messages, one at a time per direction. The class byte
separates supervision from UDS:

- The class value is chosen **outside the UDS service identifiers**. A slave in its **bootloader** passes the
  message to its UDS server, which answers every unknown identifier with a negative response "service not
  supported" (`bootloader/src/core/uds.c`, default branch). The master recognises exactly that reply as **"partner in
  bootloader"**: line stays cut, `PARTNER_FAULT` with cause "in bootloader". No bootloader code is added (ARCH-47
  requirement R7 stays byte-identical), and a bootloader can never produce a valid answer, because it has no
  program-flow signature — the rule "only the running application answers" holds by construction.
- **During flashing** the forwarding lives in the master's **bootloader** (decision `fm-master-ort`), so the
  master application — and with it the supervision — does not run, the line is at its reset level (cut), and the
  slave is in its bootloader. Nothing to suspend, nothing to arbitrate.
- **If the master application forwards** UDS to a running slave (ARCH-47 section 6.3, optional), a supervision
  question may wait behind one UDS message. `nxconfig` derives the worst-case occupancy from the link values
  (`frameLen`, `window`, `retries`, `ackTimeoutMs`, link clock, longest UDS message) and requires
  `W_max ≥ occupancy + slave answer latency`; otherwise an error at `answer_window_ms` (decision A5 (a)).
- **Flow control is no excuse.** A `BUSY` slave that does not answer in the window is a bad answer; the link's own
  rule ("a slow peer is flow control, not an error") stays true for UDS and does not apply to the supervision.
- **Peer restart** (`MCULINK_EVT_PEER_RESTART`) during supervision is a bad answer with cause "peer restart"; the
  sequence starts again and needs N_start good answers before the line is enabled again, if the recovery rule
  allows it at all (A4).

### 3.9 Configuration sketch and checks

Illustrative only; ARCH-48 and the implementing task decide names.

```yaml
# boards/<master board>/board.yaml     - wiring, the line's reset level is a board fact
devices:
  - name: MOT_MCU
    module: mcuLink
    connect: { spi: LINK_SPI, reset: MOT_MCU_RST, shutoff: MOT_MCU_SHUTOFF }
dio:
  outputs:
    - { name: MOT_MCU_SHUTOFF, pin: …, active: high, reset_level: low }   # low = cut (pull-down fitted)

# boards/<slave board>/board.yaml
partner_shutoff: { acts_on: gate_enable, readback: SHUTOFF_IN, switch_off_us: … }   # T_line

# projects/<master project>/project.yaml  - decisions of this ECU
slaves:
  - name: MOT
    supervision:
      question_period_ms: …
      answer_window_ms: [ …, … ]
      error_increment: 2
      error_threshold: …
      start_answers: …
      fault_tolerance_time_ms: …      # the integrator's FTT for this partner (AoU-01), input to the check only
      margin_ms: …
      reaction: degraded              # degraded | safe_state | safe_state_reset   (decision A3)
      recovery: none                  # none | restart_partner (decision A4)

# projects/<slave project>/project.yaml
partner_supervision:
  master_lost_ms: …
  reaction: safe_state                # safe_state | safe_state_held | degraded
```

| Check (`nxconfig`, error unless marked) | Path |
|---|---|
| the shut-off output exists, is a used `dio` output and has a stated `reset_level`, and that level means "cut" | `devices[<n>].connect.shutoff` |
| the slave board states `partner_shutoff.acts_on`; `pwm_fault_input` only with a reasoned confirmation (**warning** otherwise) | `partner_shutoff.acts_on` |
| W_min < W_max ≤ T_Q; E_inc ≥ 1; E_max ≥ E_inc; N_start ≥ 1 | `slaves[<n>].supervision.*` |
| D + R + margin < FTT (section 3.7); **warning** when `switch_off_us` is missing | `slaves[<n>].supervision.fault_tolerance_time_ms` |
| W_max ≥ worst-case link occupancy + answer latency (section 3.8), when the master application forwards | `slaves[<n>].supervision.answer_window_ms` |
| slave: `master_lost_ms` > T_Q of the master + W_max (a single late question is no loss) | `partner_supervision.master_lost_ms` |
| `reaction: degraded` only if the event is degradable (section 4.2) and a group is mapped (section 4.3) | `slaves[<n>].supervision.reaction` |
| both sides generated from one source: the expected signature S and the class values | — (generator, like ARCH-47 section 7.5) |

### 3.10 Events, error memory, placement, evidence

- **Events** (supervisor, safety context only — decision `sys37-postfach-abschaltend`): `PARTNER_FAULT` (master),
  `PARTNER_LOST` (slave). The link driver runs in its own task; question and answer cross into the safety task
  through a checked mailbox in ordinary RAM (the SYS-37 pattern), and the evaluation, the counter and the line run
  in the safety task.
- **DTC.** New source block in the numbering plan `tools/nxconfig/bswdtc.py`: `+0x09nn partner.<NAME>`, nn = index
  of the slave in `slaves[]`, `0xFF` = master lost (on the slave). Proposed, not entered (section 7.4).
- **Code placement** (proposal): the neutral protocol (question sequence, F, counter, window) as plain C next to
  `mcuLink` in `libs/bsw/partnerSupervision`, usable by any family; the event wiring in the safety port of each
  family; the generator part in `tools/bswgen` (expected signature, table entry of the line).
- **Host test**: two simulated partners on the simulated `mcuLink` bus of BL-15 — good sequence, each kind of bad
  answer, a slave that skips one checkpoint, a slave that replays an old answer, a master that stops, a slave that
  restarts, a slave in its bootloader (a real `uds.c` answering "service not supported"), counter and window
  arithmetic against the formula of 3.7; mutants for every check.
- **Board proof later**: eval board as master and Nucleo F446RE as slave (`fm-testaufbau`), line on a GPIO,
  measured: D against the formula, T_line with an oscilloscope, line level with the master in reset and in its
  bootloader.

### 3.11 What this does not cover

- **Common cause of both MCUs** — one supply, one clock source if shared, one ground, one connector. The
  supervision sees a partner that stops; it does not see both drifting the same way. A DFA row is proposed
  (section 7.5).
- **A master that asks correctly and computes wrongly** — the slave checks only that questions arrive in order.
  Symmetric question-and-answer (decision A1 (b)) would add it at the cost of a second protocol direction.
- **The slave's function** — see 3.3; the answer proves program flow, not results.
- **The line itself.** A line that is stuck "enabled" is a latent fault. A start-up test (cut the line, see the
  slave report the read-back, enable again) is possible only with the read-back input; it is part of the
  implementation task and only with the read-back wired.

---

## 4. Part B — the degraded reaction (SYS-117)

*Sections 4.1 to 4.7 (not published, available on request)*

---

## 5. Part C — active short circuit as a second safe state (SYS-116)

*Sections 5.1 to 5.11 (not published, available on request)*

---

## 6. Decisions for the user

Proposed board keys in brackets; the coordinator enters them. Each item: options, recommendation, reason.

**A — Partner supervision (SYS-115)**

- **A1 Who supervises whom** (`sys115-richtung`). (a) master asks, slave answers; slave supervises the arrival of the
  questions · (b) question-and-answer in both directions · (c) master supervises only. **Recommendation: (a)** — the
  master owns the time on the link, the slave still reacts to a lost master, and the master's own hang is covered by
  its `safe_off` line anyway; (b) is a later extension if a master that computes wrongly must be caught.
- **A2 Content of the answer** (`sys115-antwort`). (a) CRC over question and the slave's program-flow signature
  (section 3.3) plus state word · (b) alive counter / echo only · (c) (a) plus a computed test task. **Recommendation:
  (a)** — detects stuck, runaway and check-skipping slaves with existing checkpoints; (b) proves only that something
  answers, (c) claims a coverage we cannot argue without the MCU safety manual.
- **A3 Reaction on the master** (`sys115-reaktion`). (a) project value per slave: degraded | safe state | safe state +
  reset, default degraded if B is accepted, else safe state · (b) always S1 of the master · (c) line only, report
  (S3). **Recommendation: (a)** — the line already secures the slave's power stage; what the master does with its own
  outputs is an item decision (redundant drives want degraded).
- **A4 Recovery after a partner fault** (`sys115-rueckkehr`). (a) latched until power cycle · (b) automatic after
  the partner restarts and gives N_start good answers, at most `recovery_limit` times per power cycle · (c) master
  resets the partner over the reset line and then (b). **Recommendation: (a) as default, (b) as opt-in** with a limit
  — like every other latch of neXtBSW; availability is a project choice with a stated limit.
- **A5 Sharing the link with UDS** (`sys115-link`). (a) own message class, answer window derived with the
  worst-case UDS occupancy, "partner in bootloader" from the bootloader's "service not supported" · (b) a separate
  wire for the supervision (second link or toggle line) · (c) forbid forwarding by the master application while
  supervision runs. **Recommendation: (a)** — no bootloader byte, no extra wire, the check makes the cost visible.
- **A6 What the shut-off line may act on** (`sys115-leitung`). (a) `gate_enable`, `gate_supply` freely,
  `pwm_fault_input` with warning and reasoned confirmation · (b) all three equally · (c) only `gate_enable` and
  `gate_supply`. **Recommendation: (a)** — the fault input depends on the supervised MCU's own configuration.

*Decisions B1 to B6 (degraded reaction, SYS-117) and C1 to C6 (active short circuit, SYS-116) (not published, available on request)*

---

## 7. Proposed changes to other documents (not made)

### 7.1 `concept.md`

1. Header: "Update after issue 2: SYS-115/116/117 (concept, 2026-09-29)" paragraph pointing here.
2. Section 2.1: the bullet "A single ECU without a redundant channel is assumed … the multi-MCU work (ARCH-47) is
   about flashing" → the partner supervision exists as a mechanism for multi-MCU ECUs; still no redundancy claim of
   the element.
3. Section 2.2: ASG-01 stage-1 column gains the partner supervision and the selectable safe state; the paragraph
   "If an integrator's safety goal requires the function to keep working … not supported" is replaced (section 4.7).
4. Section 3: SD degraded as a reaction; S1 "every PWM group forced to its inactive level" → "to its selected safe
   state (freewheel by default)"; a paragraph on the active short and its fallback; the list of events in S1 held
   unchanged.
5. Section 4: AoU-04 new text (4.7); AoU-13 note that a partner MCU's supervision gives an independent time base for
   the power stage (3.6); new AoUs (numbers by the coordinator, next free after AoU-30):
   - *AoU-31* the integrator states the FTT per partner and the board states `T_line`; he confirms the common-cause
     assumptions of 3.11 (supply, clock, connector) for his board;
   - *AoU-32* for every degraded mapping, the remaining function is safe with the mapped groups in their safe state,
     and the groups are independent in his hardware (supply, sensors, connectors);
   - *AoU-33* the choice of the safe state of a PWM group (freewheel or short) is part of his safety function; the
     writing software runs at the ASIL of that goal; he confirms that the board's reset state of the bridge is
     tolerable during reset and restart, and that the start test runs only at standstill where no phase voltage is
     measured.
6. Section 5: level 2 lists SD.
7. Section 6: new catalogue rows (numbers proposed, the coordinator assigns): **SM-56** partner question-and-answer
   supervision (6.1, program flow and time), **SM-57** partner shut-off line (6.5, outputs — the reaction half, a
   board path credited only with SM-56 as its trigger), **SM-26** selectable safe state of a PWM group with fallback
   to freewheel (6.5), **SM-27** start-up test of the short (6.5). SD itself is a reaction, not a mechanism.
8. Section 6.5 note "one list, not two": the table stays one list, now with value sets per group and a group index.
9. Section 7.2: rows for SM-56 (D + R of 3.7, derived by `nxconfig`), SD (as S1 for the walk), the short (as S1;
   plus dead-time spin where needed).
10. Section 9: new findings (IDs by the coordinator; here PD-1 … PD-5):
    - **PD-1** the safe outputs table can express only one safe state per PWM group (freewheel); motor inverters
      without a mechanical brake need a second (SYS-116);
    - **PD-2** `safe_off` enables go inactive unconditionally, which makes an active short impossible (5.4);
    - **PD-3** no supervision of a second MCU and no hardware path to its power stage exists (SYS-115);
    - **PD-4** every reaction ends in a switch-off of everything or a reset (SYS-117);
    - **PD-5** the forced pattern of the software output control for a complementary pair has never been checked
      against the reference manual for anything but all-off, and never on the board with the gate driver enabled
      beyond the SYS-35 observation (5.3).
11. Section 9.5/10.9: G1 (read-back of S1) gains the short as a second pattern to read back.

### 7.2 `fmea.md`

- New per-mechanism sections SM-56, SM-57, SM-26, SM-27 in the usual fields (faults considered, detection, reaction,
  times with "minimum FTTI served", diagnostic-coverage class, latent faults, evidence, residual). Key contents:
  SM-56 latent faults — a checkpoint that always contributes its value (the signature must include the counts, not
  only flags); SM-57 latent fault — a line stuck "enabled" (start test only with read-back); SM-26 residual — a
  wild write of exactly one encoded pattern, ECC error in the selection word (lockup, then reset state); SM-27
  residual — no standstill proof without phase voltages.
- SM-23: the walk reads one word of RAM per group (C1 (a)); SM-06: the guard keeps the enable of a shorted group.
- Section 3.7 summary rows and section 4 (goals checked): ASG-01 gains SM-56/57 (multi-MCU) and SM-26; ASG-02 the
  statement that S1 may be a short.
- Section 7 (proposed tasks) and section 8 (board checklist): B-items for D and T_line of SM-56/57 (eval board +
  Nucleo), the short pattern with an oscilloscope on each leg at standstill, the start test.

### 7.3 `requirements.yaml` (IDs by the coordinator; SSR-085 appears unused, SSR-087 ff. are free)

- **SSR-001 (b)**: the list of reactions gains "degraded (SD)".
- **SSR-003**: unchanged in content; (c) "every entry … to its inactive level" → "to its selected safe state".
- **SSR-004**: add clause: "(c) An output that is left active because its group is in an active short shall not be
  counted as switched on again."
- **SSR-005 (c)/(e)**: "(c) … no stack, no driver object, no RAM write, and at most one read of RAM per PWM group";
  "(e) … drive every other entry to the safe state selected for its group, freewheel if the selection is invalid or
  unavailable".
- New, open (status `open`, verification test + board):
  - partner questions and answer evaluation (window, counter, signature, sequence) — SM-56;
  - shut-off line: fail-safe reset level, `safe_off`, enabled only after N_start good answers, cut before the event
    is handled — SM-57;
  - master lost on the slave — SM-56;
  - `nxconfig` check of D + R + margin against the stated FTT — SM-56 (verification test);
  - degraded reaction: only degradable events, only mapped groups, no reset, record surviving a safety reset,
    escalation, no return in the power cycle;
  - `nxconfig` refuses a degraded mapping of a fail-safe event;
  - selectable safe state: allowed list, encoded selection, fallback to freewheel, bridge-fault events force
    freewheel — SM-26;
  - enable bound to a group follows its selected state — SM-26;
  - start test of the short — SM-27.
- `derived_from`: ASG-01, ASG-02, AoU-04 and the new AoU-31 … AoU-33.

### 7.4 Other

- `tools/nxconfig/bswdtc.py` numbering plan: `+0x09nn partner.<NAME>` (nn = slave index, 0xFF master lost),
  `+0x0Ann degraded.<GROUP>` (nn = index of the safe-output group), `+0x0Bnn safe_state_test.<GROUP>`.
- [`hardware-mechanisms.md`](hardware-mechanisms.md) section 5: point 4 gains "and with a selected short, which
  pattern the fault input and the walk each expect"; a new section for the partner shut-off line as a
  board-level hardware path (reaction independent of the supervised MCU).
- `multi-mcu-flash.md` (not published, available on request) section 13: "a transport other than SPI" stays; new
  bullet: supervision uses the link but is not part of the flash path, and the reserved class value is not a UDS
  service identifier.

### 7.5 `dfa.md` (proposed rows)

- partner MCUs share supply, clock source (if one oscillator feeds both), ground and connector — common cause of
  master and slave; measure: the line's reset level is "cut", the slave's own supervision; residual: an AoU;
- the PWM timer as common cause of a group's actuation and of its degraded reaction (SD forces the same timer that
  failed) — measure: fast-loop events on a group whose timer is shared are not degradable (4.3);
- the selection word in ordinary RAM as common cause of all walks — measure: encoding, fallback, the latch analogy.

### 7.6 Anomaly register

This new file needs a row in the document register of [`anomaly-register.md`](anomaly-register.md) (the test
`anomaly-register` checks that every document of `doc/safety` has one); proposed row, to be entered by the
coordinator together with the summary counts of its section 0:

```
| `partner-and-degraded.md` | Partner MCU supervision, degraded reaction and active short circuit (concept) | SYS-115 | pending (decision items of SYS-115, SYS-116, SYS-117) | 1, 2026-09-29 | draft | present (Working state) | header; CHANGELOG SYS-115 |
```

---

## 8. Proposed implementation cut (after the decisions)

| Step | Content | Paths | Verification |
|---|---|---|---|
| 1 (SYS-117) | reaction SD in `handle()`, degradable list in the manifest, group index in the table, subset walk, per-group latch, degraded record in no-init RAM, RTE signal, DEM source; `nxconfig` groups and mapping checks | `ecu/safety/safeSupervisor`, `tools/nxconfig`, `tools/bswgen`, family safety port | host (supervisor with recording stubs, mutants) |
| 2 (IO-45) | gate driver ↔ PWM group in the board schema — prerequisite for groups with enables and for C2 | `boards`, `tools/nxconfig` | host |
| 3 (SYS-116) | allowed states, selection service and word, value sets in the table, both walks, enable binding, deadman guard, bridge-fault list, start test, checks | `platforms/<family>/hardFaultHandler`, `port/safety`, `tools/bswgen`, `tools/nxconfig`, mcudb | host with register model; disassembly test `safe-outputs-order` extended; board with the user (motor, power stage) |
| 4 (SYS-115) | `libs/bsw/partnerSupervision` (protocol, window, counter, signature), events, line as `safe_off`, schema of ARCH-48 extended, FTT check, DTC source | `libs/bsw`, `tools/nxconfig`, family port | host with two simulated partners on the BL-15 bus; board: eval board + Nucleo |
| 5 | documents of section 7 | `doc/safety`, `doc/architecture` | `safety-trace`, doc tests |

Order: 1 and 2 are independent and small; 3 needs 2 and SYS-85's recovery decision; 4 needs the slave side of the
link in an application (BL-16 slave port) and profits from 1 for its degraded reaction.

---

## 9. Review questions of the architecture ([`../architecture/README.md`](../architecture/README.md) section 11)

For the design proposed here (no code changed):

1. **Board without an SBC.** Yes — the partner supervision is designed for exactly that slave; it uses the MCU
   watchdog and the master's clock, never an SBC. SD and the short need no SBC either.
2. **Other crystal / clock.** Yes — every time is a project value in milliseconds or microseconds; the dead-time spin
   and the link occupancy are derived from the configured clock by the generator.
3. **Other derivative, S32K3/STM32/Cortex-R.** Yes — the protocol is plain C over a message contract; the short needs
   a family fact (can the timer force it with single stores), stated in mcudb, and a family without it simply does
   not offer the short. The walk's per-group read is architecture-neutral; the assembler handler of each family has
   to implement it (R0–R3 on Cortex-M; the Cortex-R port has its own register rule).
4. **Two or more CAN buses.** Unaffected; on a CAN link the supervision uses its own identifier pair, checked against
   the DBC like the diagnostic identifiers.
5. **Several tasks, free priorities.** The evaluation runs in the safety task; the link task hands over through a
   mailbox; `T_eval` is the safety task's period, so another task layout changes only the formula's input.
6. **One configuration level per value.** Line wiring, reset level, `T_line`, bridge reset state and high-side hold:
   board. Periods, window, counter, FTT, reactions, groups, mappings, allowed states, start test: project. Which
   events are degradable and which force freewheel: code facts of the supervisor, published in its manifest. The
   short's feasibility: derivative (mcudb). Derived by the generator: D + R, link occupancy, dead-time spin, the
   expected signature for both sides.
7. **Host test with its own profile.** Planned per step (section 8), with synthetic two-MCU and two-drive profiles,
   not the eval board.
8. **Tools free of Qt, messages with YAML path.** All checks of 3.9, 4.3 and 5.11 name their path.
9. **Device drivers.** No driver logic; the gate driver appears only as a `safe_off` enable bound to a group and as
   the source of device fault classes (SYS-29).
10. **`platforms/` placement.** The walk and the start test's register part live in the family's
    `hardFaultHandler` and `port/safety`, as today; nothing in `platforms/common`.

---

## 10. Sources

- [`concept.md`](concept.md) sections 1.4, 2, 3, 4, 6, 7, 9 — reactions, AoUs, mechanisms, times.
- [`fmea.md`](fmea.md) sections 1.2 (time terms), 3 (SM-04, SM-06, SM-23 … SM-25, SM-34).
- [`ssr.sdoc`](../requirements/safety/ssr.sdoc) SSR-001 … SSR-007.
- [`dfa.md`](dfa.md) DF-06, DF-35, AoU-DFA-b.
- [`hardware-mechanisms.md`](hardware-mechanisms.md) sections 2.1, 3, 5.
- `../architecture/multi-mcu-flash.md` (not published, available on request) sections 2, 7, 8, 9.4, 10, 12, 13.
- `../architecture/fast-control-loop.md` (not published, available on request) sections 6, 8.3, 10.
- `libs/bsw/mcuLink/doc/mcuLink.md` (frame, session counter, flow control, configuration).
- Code at `7af5b7f`: `ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp` (`handle()`,
  `holdSafeState()`), `platforms/s32k1xx/hardFaultHandler/include/hardFaultHandler/safeOutputs.h`,
  `tools/bswgen/bswgen.py` (`collect_safe_outputs`), `tools/nxconfig/bswdtc.py`, `bootloader/src/core/uds.c`
  (negative response for an unknown service), `libs/bsw/gdTmc6200/module.yaml` (`drv_en` `safe_off`).
- `doc/todo.yaml`: SYS-115, SYS-116, SYS-117, SYS-85, SYS-86, IO-45, IO-64, SYS-31, SYS-32, BL-15 … BL-18, ARCH-48.
