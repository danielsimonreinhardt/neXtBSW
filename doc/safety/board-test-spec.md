# neXtBSW board test specification per SSR with operational use cases (SYS-75)

Status: 2026-10-02, **issue 1 (SYS-75)**, written against `integ/night11` at `92edce0b`, draft for acceptance by the
user. No case of this issue was run at the board; every procedure was run on the host against recorded board
captures and against a simulated board (section 6).
Working state: dev at 92edce0b (after v0.1.0)
Readers: the user, who runs the cases at the board and accepts the specification as safety manager; the coordinator,
who keeps the suite current with the firmware and links the records into the verification report of a baseline;
the reviewer of confirmation measure CM-04 (integration and test strategy).
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).

**What this document is.** The target test specification that `iso26262-gap.md` (not published, available on request) section 6 no. 20
asks for and that the safety plan names as part of P6-20 (testing of the embedded software): for every software
safety requirement of [`ssr.sdoc`](../requirements/safety/ssr.sdoc) whose verification includes the board, the
board test cases that verify it, ordered by **operational use case** - start-up, S1, S1 held, S2, sleep and wake-up,
flashing, diagnostic session and normal operation - with what each case needs at the bench (oscilloscope, 24 V, a
motor, the debugger, a person at the board). The cases are **repeatable**: each one is a procedure of
[`tools/boardtest`](../../tools/boardtest/README.md) that drives the console and the CAN adapter, asks the person at
the board for what only a person can do or read, records a transcript and evaluates it with fixed checks; the
results of a run are **archived** as a record next to the board captures (section 7).

**What it is not.** Not the verification plan: [`verification-plan.md`](verification-plan.md) (SYS-67) owns the
methods, their ratings, the environments and the regression rings; this document is the specification of the cases
at its embedded-software level (its sections 2.3 and 5.2). Not the board regression list of a safety release: which
cases run on the tagged image of a safety release, and how a difference analysis may let an earlier record stand
(VP-6, task proposal T-VP2), is SYS-167; it selects from the cases here and links their records. Not the board
checklists of the analyses: `fmea.md` section 8 (B1 ... B16) stays the place where an analysis says what it needs
measured; the cases here take those checks over where they verify an SSR and name them.

**Licence rule.** The standard is cited by part and clause only; nothing of its text is reproduced.

---

## 1. Result in one page

| Question | Answer |
|---|---|
| Board items in `ssr.sdoc` | 98 clauses (or SSRs without clauses) name "board" in their verification, withdrawn ones excluded |
| Covered by a case | 91 by 31 cases; 28 cases run on the eval board project `evalboard_s32k144_demo`; 3 cases (BT-RUN-09, BT-PWM-01, BT-PWM-02) are specified for projects with features the demo does not configure (ADC qualifier ports, active short, degraded reaction) and are the only cover of 29 items (SSR-040, SSR-087, SSR-089, SSR-091, SSR-092) |
| Without a board case | 7 items, each with its reason in the suite (`no_board_case`, section 5.3): SSR-020(b)(c), SSR-024(b), SSR-042(b), SSR-052(c), SSR-082(a)(b) (open in `ssr.sdoc`) |
| Operating cases | all eight present with at least one runnable case: START 3, S1 3 (+2 other projects), S1H 4, S2 6, SLEEP 1, FLASH 2, DIAG 1, RUN 8 (+1 other project) |
| Oscilloscope / 24 V / motor | scope: BT-S1-02, BT-PWM-01; 24 V: BT-S1-02, BT-PWM-01; motor: BT-PWM-01 only (column per SSR in section 5) |
| Person at the board | 18 of 31 cases (power cycle, reset button, J-Link, short of the bus, board-rule commands such as `ecu sleep`) |
| Earlier board records | linked per case (column "Earlier records", section 5.2): 65 captures of 2026-09-16 ... 2026-09-30 and the fmea checks B2 ... B16 - this answers safety case O1 and AN-100 |
| Verified how | **mock/host**: `tools/boardtest/test_suite.py` - 18 procedures against the board captures they name (21 capture sets), every check shown to matter by mutation, five procedures end to end against a simulated board on a virtual clock, consistency with `ssr.sdoc` and with the tables of this document. **Not run at the board** (section 8) |

---

## 2. Operating cases

| Code | Operating case | How the cases enter it | Board rules (CLAUDE.md) |
|---|---|---|---|
| START | start-up: power-on, reset pin, software reset, the reset at the end of a download | power cycle or reset button by the user, `lc reboot`, canflash | power cycle and reset button need the user |
| S1 | safe state without reset | `safety dst` / `safety est`; with the debugger, falsified output control while S1 is latched | `MOT1_GD_DRV_EN`, `kl30b on` only with the user |
| S1H | safe state held without reset (S1 held) | corrupted image (ROM check), forced watchdog start-up test (J-Link), reset-loop limit | the way back from a held state is a power cycle while canflash waits (`tools/canflash` README) |
| S2 | safety reset (S1 + S2) and restart | `safety wdg`, `safety mpu`, `lc assert`, fault injection of a test build (`inject ...`), J-Link on the cycle timer | J-Link only after the licence confirmation of the day |
| SLEEP | sleep and wake-up | `ecu sleep`, then a CAN frame | `ecu sleep` switches the MCU supply off: only with the user |
| FLASH | flashing over CAN through the bootloader | `canflash flash` (and an interrupted download) | application flashing is allowed; bootloader update (`blupdate`) is not part of this suite |
| DIAG | diagnostic session | UDS sessions, TesterPresent, communication control | - |
| RUN | normal operation | listening over ROM-check periods, under load (test build), E2E qualifier phases, bus faults | short of CANH/CANL by the user |

The S2 cases start with a pause of 65 s: the reset-loop count of the safety supervisor clears after 60 000 ms
without a safety reset (project value of `modules.safeSupervisor`), so a case never inherits the count of the one
before. BT-S1H-03 relies on the count and starts from a power cycle instead.

---

## 3. Images and equipment

| Image | Meaning |
|---|---|
| `dev` | the standard development image of the tag (test console commands `safety ...`, `lc ...` present) |
| `test` | the development image built with the CMake option the case names (`NX_SAFETY_FAULT_INJECTION_COMMAND`, `NX_GD_ENABLE_COMMAND`, `NX_RTE_LOAD_TEST_US`, a stall build) |
| `special` | an image or board state the case describes (a corrupted image for the ROM check) |
| `product` | the product image (`NX_PRODUCT_BUILD`); no case needs it in this issue - it removes the test commands the S1/S2 cases use |

| Equipment | Meaning |
|---|---|
| `console` | serial console of the ECU (port and baud from the project or the command line) |
| `can` | CAN adapter (python-can, VN1610 at the workstation); bit timing from the project |
| `jlink` | debugger: halt, register read and write, breakpoint (J-Link rules of CLAUDE.md) |
| `scope` | oscilloscope |
| `24v` | 24 V supply of the power stage |
| `motor` | a motor at the power stage, at standstill or turned by hand |
| `source` | adjustable source on an analogue input |
| `user` | a person at the board: power cycle, reset button, a cable, or a command the board rules reserve to the user |

A case that needs a test or special image says so in the column "Image" of section 5.2 and in its `build` field;
the runner prints both before the case starts.

---

## 4. How a case is written, run and evaluated

A case is a **procedure** in the suite file of the project,
`tools/boardtest/suites/evalboard_s32k144_demo.yaml` (not published, available on request):
steps that act (console command, console capture for N seconds, CAN probe, UDS request, wake frame, `canflash`,
an instruction or a question to the person at the board) and **checks** on the transcript the steps leave behind.
The checks are regular expressions matched in order:

- `expect` must match after the previous match; its named groups become **values** of the run (start reason,
  reset-loop cause, restart gap on CAN, image CRC, ...);
- `absent` must not match between the previous and the next matched `expect` - "no reset while S1 is held", "no
  event while the ROM check repeats";
- `equals` / `differs` compare captured values (reset-loop cause `0x2` of a fault exception, `0x1` of a watchdog reset, `0x117` of an assertion,
  wake-up sources not `reset`);
- `delta` compares the difference of two values with a project value (the ROM-check period against
  `modules.safeMemory.rom_check_period_ms`, the project's value or the module default) - no time is written into the
  tool.

A live run writes the console traffic in the capture format of `tools/boardtest/captures` and adds lines `#> ...`
for the results of CAN steps, the answers of the person at the board and the output of external tools (`#| ...`).
The **same evaluator** judges a live transcript and a recorded capture: replaying a capture skips only the `#>`
checks. So every case with a reference capture is checked against what the board really printed, without a board,
at every run of the host tests, and a firmware change of a console line turns the case red on the host first.

Commands (no hardware for the first four):

```sh
.venv/Scripts/python tools/boardtest --project projects/evalboard_s32k144_demo suite list [--oc S2] [--ssr SSR-065]
.venv/Scripts/python tools/boardtest --project projects/evalboard_s32k144_demo suite check      # against ssr.sdoc
.venv/Scripts/python tools/boardtest --project projects/evalboard_s32k144_demo suite matrix     # tables of section 5
.venv/Scripts/python tools/boardtest --project projects/evalboard_s32k144_demo suite replay BT-S2-04 [capture ...]
.venv/Scripts/python tools/boardtest --project projects/evalboard_s32k144_demo --port COM17 suite run START S2 \
    --out tools/boardtest/captures/evalboard_s32k144_demo/<date>_suite-<tag> --image <app.hex> --baseline <tag> \
    --tester <name>
```

`suite run` takes case ids, operating-case codes or `all` (runnable cases only). A step that cannot run (adapter
missing, port busy) ends its case with an error and the run goes on with the next case.

---

## 5. Cases per SSR

### 5.1 How to read the tables

The first table has one row per SSR that has a board item in `ssr.sdoc` or a board case: the board items (clauses)
of the SSR, the cases that cover them, their operating cases, and whether **any** of these cases needs the
oscilloscope, 24 V, a motor, the debugger or a person at the board; "- (beyond the method)" marks an SSR verified by
test or review whose board case adds evidence beyond its method. The second table has one row per case with its
image, equipment, the reference capture its checks are tested against on the host, and the **earlier board
records** of the same behaviour. Both tables are generated (`suite matrix`) and compared with this document by
`test_suite.py`.

### 5.2 Tables

<!-- bts-matrix:start -->
| SSR | Title | Board items (`ssr.sdoc`) | Board cases | Operating cases | Scope | 24 V | Motor | J-Link | User |
|---|---|---|---|---|---|---|---|---|---|
| SSR-001 | Reaction decided in one place | - (beyond the method) | BT-S2-03 | S2 | - | - | - | - | - |
| SSR-002 | De-energise before every safety reset | - (beyond the method) | BT-S2-03 | S2 | - | - | - | - | - |
| SSR-003 | Safe state held without reset for faults a reset cannot remove | (a), (b), (c), (d), (e), (f) | BT-S1H-01, BT-S1H-02, BT-S1H-03 | S1H | - | - | - | yes | yes |
| SSR-004 | Leaving the safe state never re-energises a power output | - (beyond the method) | BT-S1-01 | S1 | - | - | - | - | yes |
| SSR-005 | One safe outputs table for every switch-off path | (a), (b), (c), (d), (e), (f), (g), (h), (i) | BT-S1-01, BT-S2-02, BT-S1-03, BT-S1-02, BT-RUN-10 | RUN, S1, S2 | yes | yes | - | yes | yes |
| SSR-010 | MCU watchdog serviced by the safety task only | (a), (b), (c) | BT-S2-01, BT-RUN-04, BT-RUN-01 | RUN, S2 | - | - | - | yes | yes |
| SSR-012 | Watchdog configuration re-checked at every service | - (beyond the method) | BT-S2-04, BT-RUN-04 | RUN, S2 | - | - | - | yes | yes |
| SSR-015 | RTE task and runnable deadlines reported | (a), (b), (c), (d), (e), (f) | BT-RUN-02 | RUN | - | - | - | - | - |
| SSR-016 | Core halt or stall longer than the deadman time-out de-energises | (a), (b), (c) | BT-RUN-06, BT-S1-02 | RUN, S1 | yes | yes | - | yes | yes |
| SSR-017 | Watchdog hardware tested at every start | (a), (b) | BT-START-01, BT-S2-01, BT-S1H-02 | S1H, S2, START | - | - | - | yes | yes |
| SSR-018 | Safety task period supervised against a hardware time base | (a), (b), (c), (d), (e) | BT-S2-05, BT-S2-06, BT-RUN-01, BT-RUN-02 | RUN, S2 | - | - | - | yes | yes |
| SSR-020 | Safety data writable only from the safety context | (a), (b), (c), (d), (e) | BT-S2-02, BT-RUN-02, BT-S2-07; no board case: SSR-020(b), SSR-020(c) | RUN, S2 | - | - | - | - | - |
| SSR-021 | Protection gate state checked on entry and exit of the safety cycle | - (beyond the method) | BT-RUN-01 | RUN | - | - | - | - | - |
| SSR-022 | MPU enabled with the region count of the derivative | - (beyond the method) | BT-RUN-01 | RUN | - | - | - | - | - |
| SSR-023 | MPU region descriptors unchanged | - (beyond the method) | BT-RUN-01 | RUN | - | - | - | - | - |
| SSR-024 | ROM check of the application image | (a), (b), (c), (d), (e) | BT-FLASH-01, BT-S1H-01, BT-START-01; no board case: SSR-024(b) | FLASH, S1H, START | - | - | - | - | yes |
| SSR-028 | Fault handlers de-energise first | (a), (b), (c), (d) | BT-S2-01, BT-S2-02, BT-S1-02 | S1, S2 | yes | yes | - | - | yes |
| SSR-033 | Safety-relevant pin configuration locked at start-up | (a), (b) | BT-RUN-05, BT-START-01 | RUN, START | - | - | - | yes | yes |
| SSR-034 | Safety-relevant registers checked cyclically | (a), (b) | BT-S1-03, BT-RUN-01, BT-S1-01 | RUN, S1 | - | - | - | yes | yes |
| SSR-040 | Quality qualifier for every analogue value | (a), (b), (c), (d), (e) | BT-RUN-09 (not on this project) | RUN | - | - | - | - | yes |
| SSR-041 | End-to-end protection of configured CAN signals | (a), (b), (c) | BT-RUN-03 | RUN | - | - | - | - | - |
| SSR-042 | No transmission into an unhealthy bus | (a), (b) | BT-RUN-08, BT-DIAG-01; no board case: SSR-042(b) | DIAG, RUN | - | - | - | - | yes |
| SSR-050 | Start reason from two independent sources | (a), (b), (c) | BT-START-01, BT-START-02, BT-SLEEP-01, BT-START-03, BT-S2-01 | S2, SLEEP, START | - | - | - | - | yes |
| SSR-051 | Bootloader starts only a valid, compatible image | (a), (b), (c) | BT-FLASH-01, BT-FLASH-02 | FLASH | - | - | - | - | yes |
| SSR-052 | Defined run-down with time-outs per step | (a), (b), (c) | BT-SLEEP-01; no board case: SSR-052(c) | SLEEP | - | - | - | - | yes |
| SSR-060 | Safe state verified by read-back | (a), (b) | BT-S1-03, BT-S1-01 | S1 | - | - | - | yes | yes |
| SSR-065 | Limit on repeated safety resets | - (beyond the method) | BT-START-02, BT-S2-01, BT-S2-02, BT-S2-03, BT-S2-04, BT-S1H-03, BT-S1H-04 | S1H, S2, START | - | - | - | - | yes |
| SSR-066 | Complete configuration checks of the supervision peripherals | - (beyond the method) | BT-S2-04 | S2 | - | - | - | - | - |
| SSR-067 | ROM check repeated during operation | (a), (b), (c) | BT-RUN-01, BT-RUN-02, BT-DIAG-01, BT-RUN-07 | DIAG, RUN | - | - | - | - | - |
| SSR-070 | Register lock and check list derived from the configuration | whole | BT-RUN-05 | RUN | - | - | - | yes | yes |
| SSR-082 | Configuration and generator identity recognisable in the image | (a), (b) | -; no board case: SSR-082(a), SSR-082(b) | - | - | - | - | - | - |
| SSR-086 | Latent faults of the RAM error correction tested at every start | (a), (b) | BT-START-01 | START | - | - | - | - | - |
| SSR-087 | Degraded reaction switches only the mapped safe-output groups and keeps them until the power cycle | (a), (b), (c), (d), (e), (f), (g) | BT-PWM-02 (not on this project) | S1 | - | - | - | yes | yes |
| SSR-089 | Selected safe state of a PWM group applied by every switch-off path, freewheel on any doubt | (a), (b), (c), (d), (e), (f), (g), (h) | BT-PWM-01 (not on this project) | S1 | yes | yes | yes | yes | yes |
| SSR-091 | A bridge fault withdraws the active short before the reaction walks the table | (a), (b), (c), (d) | BT-PWM-01 (not on this project) | S1 | yes | yes | yes | yes | yes |
| SSR-092 | The family port keeps an active short that a switch-off path applied | (a), (b), (c), (d), (e) | BT-PWM-01 (not on this project) | S1 | yes | yes | yes | yes | yes |

| Case | Title | OC | Covers | Image | Equipment | Reference capture | Earlier records |
|---|---|---|---|---|---|---|---|
| BT-START-01 | Software reset start and the complete start-up sequence | START | SSR-017(a), SSR-086, SSR-024(d), SSR-050(a), SSR-050(b), SSR-033(b) | dev | console | 2026-10-02_suite-integ12/BT-START-01.console.txt | 2026-09-29_board/board0929_02_session.console.txt; 2026-09-22_board/01_flash_boot.console.txt; fmea B8 |
| BT-START-02 | Power-on start - start reason from both sources, reset-loop count cleared | START | SSR-050(a), SSR-050(b), SSR-050(c), SSR-065(c) | dev | console, user | none yet | 2026-09-21_board/06_sys18_power_cycle.console.txt; 2026-09-21_board/02_sys18_poweron_baseline.console.txt; 2026-09-22_board/04_poweron.console.txt |
| BT-START-03 | Reset-pin start - start reason PIN, application starts normally | START | SSR-050(b), SSR-050(c) | dev | console, user | 2026-09-23_board/17_sys43_taster_normal.raw.txt | 2026-09-21_board/05_sys18_reset_button.console.txt; 2026-09-23_board/17_sys43_taster_normal.raw.txt |
| BT-S2-01 | Watchdog reset through an endless loop (safety wdg) and the restart time on CAN | S2 | SSR-010(a), SSR-028(a), SSR-065(a), SSR-050(b), SSR-017(a) | dev | console, can | 2026-10-02_suite-integ12/BT-S2-01.console.txt | 2026-09-23_board/14_sys43_taster_wdg1.raw.txt; 2026-09-22_board/63_sys43_b10_wdg1.console.txt; 2026-09-22_board/96_sys45_b2_wdg.console.txt; 2026-09-21_board/03_sys18_wdg_trigger.console.txt; fmea B2 |
| BT-S2-02 | MPU violation (safety mpu) - fault handler de-energises first, safety reset | S2 | SSR-020(a), SSR-028(a), SSR-028(b), SSR-028(c), SSR-028(d), SSR-005(c), SSR-065(a) | dev | console | 2026-10-02_suite-integ12/BT-S2-02.console.txt | 2026-09-22_board/96_sys45_b2_mpu.console.txt; 2026-09-22_board/68_sys43_b11_mpu.console.txt; fmea B2 |
| BT-S2-03 | Supervisor event with S1 + S2 (lc assert) - console line, reset, reset-loop cause | S2 | SSR-002, SSR-001(b), SSR-065(a) | dev | console | 2026-10-02_suite-integ12/BT-S2-03.console.txt | 2026-09-29_board/board0929_02_session.console.txt; 2026-09-23_board/25_sys80_k4_assert.raw.txt |
| BT-S2-04 | Supervision peripherals falsified at run time (deadman TVAL, WDOG_CS[INT]) - S1 + S2 | S2 | SSR-012, SSR-066(a), SSR-066(b), SSR-065(a) | test | console | 2026-10-02_suite-integ12-test/BT-S2-04.console.txt; 2026-09-22_board/160_sys59_deadman_r1.raw.txt + 2026-09-22_board/164_sys59_wdogint_r1.raw.txt | 2026-09-22_board/165_sys59_deadman_r2.raw.txt; 2026-09-22_board/169_sys59_wdogint_r2.raw.txt; 2026-09-22_board/170_sys59_deadman_r3.raw.txt; 2026-09-22_board/174_sys59_wdogint_r3.raw.txt; fmea B6 |
| BT-S2-05 | Safety task blocked once (test build) - cycle period violation kind 1, S1 + S2 | S2 | SSR-018(a), SSR-018(b), SSR-018(e) | test | console, user | 2026-09-22_board/116_sys40_b7b_20ms.console.txt; 2026-09-22_board/132_sys40_b7b_stall0x6.console.txt | 2026-09-22_board/115_sys40_b7b_5ms_repeat.console.txt; 2026-09-22_board/133_sys40_b7b_stall0x5.console.txt; fmea B7 |
| BT-S2-06 | Cycle timer stopped (J-Link clears LPTMR0_CSR[TEN]) - kind 3; debugger halt gives no report | S2 | SSR-018(a), SSR-018(d), SSR-018(e) | dev | console, jlink, user | 2026-09-22_board/177_sys59_r2_b7a.raw.txt | 2026-09-22_board/105_sys40_b7a_skip.console.txt; 2026-09-22_board/106_sys40_b7a_noskip.console.txt; fmea B7 |
| BT-S2-07 | Interrupt stack overflow onto its guard (inject mspoverflow) - fault, outputs off, safety reset | S2 | SSR-020(e) | test | console | 2026-10-03_board/bl039-suite/BT-S2-07.console.txt | fmea N3 |
| BT-S1-01 | Safe state entered and left without a reset (safety dst / safety est) | S1 | SSR-034(b), SSR-005(a), SSR-005(b), SSR-005(e), SSR-004, SSR-060(b) | dev | console, user | 2026-10-02_board/integ12/sys169_dst.txt + 2026-09-22_board/81_sys35p6_est.console.txt | 2026-09-21_board/15_sys06_dst.console.txt; 2026-09-21_board/22_sys36_dst_1A.console.txt; 2026-09-21_board/23_sys36_est_on_refused.console.txt; 2026-09-23_board/34_sys46_dst.console.txt; 2026-09-23_board/48_sys35_dst.console.txt; 2026-09-23_board/49_sys35_est.console.txt |
| BT-S1-02 | Safe outputs walk at the pins - DRV_EN and the phases with the oscilloscope (B3), fault handler first (lc dabt) | S1 | SSR-005(e), SSR-005(f), SSR-028(a), SSR-016(c) | test | console, scope, 24v, user | none yet | 2026-09-22_board/121_sys45_b3_dst.console.txt; 2026-09-22_board/127_sys45_b3_dst3.console.txt; fmea B3 |
| BT-S1-03 | S1 confirmed by read-back - falsified output control while S1 is latched (J-Link, B15/B16) | S1 | SSR-060(a), SSR-060(b), SSR-005(d), SSR-034(a) | dev | console, jlink, user | none yet | fmea B15; fmea B16 |
| BT-S1H-01 | ROM check failure at start holds S1 without reset until the power cycle | S1H | SSR-003, SSR-024(c), SSR-024(e) | special | console, can, user | 2026-09-22_board/20_sys39_romfail.console.txt | 2026-09-22_board/94_tool51_romfail.console.txt; 2026-09-22_board/74_tool51_romfail.console.txt; 2026-09-21_board/46_sys38_romfail.console.txt |
| BT-S1H-02 | Watchdog start-up test failure (result forced with a J-Link breakpoint) holds S1 | S1H | SSR-017(b), SSR-003(a), SSR-003(e) | dev | console, jlink, user | 2026-09-22_board/76_sys39_wdg_startup_fail.console.txt | 2026-09-21_board/27_sys23_wdg_startup_fail.txt; 2026-09-21_board/28_sys23_wdg_startup_fail.txt; 2026-09-21_board/29_sys23_after_power_cycle.console.txt |
| BT-S1H-03 | Reset-loop limit - three safety resets in a row hold S1; the reset button holds again; power cycle clears | S1H | SSR-065(a), SSR-065(b), SSR-065(c), SSR-003(f) | dev | console, user | 2026-09-23_board/17_sys43_taster_normal.raw.txt + 2026-09-23_board/14_sys43_taster_wdg1.raw.txt + 2026-09-23_board/14_sys43_taster_wdg2.raw.txt + 2026-09-23_board/14_sys43_taster_wdg3.raw.txt + 2026-09-23_board/15_sys43_taster_held.raw.txt + 2026-09-23_board/17_sys43_taster_normal.raw.txt | 2026-09-22_board/152_sys43_b10_wdg1.raw.txt; 2026-09-22_board/152_sys43_b10_wdg3.raw.txt; 2026-09-22_board/65_sys43_b10_powercycle.console.txt; fmea B10 |
| BT-S1H-04 | Reset-loop count cleared after the fault-free time (60 s) | S1H | SSR-065(c) | dev | console | 2026-10-02_suite-integ12/BT-S1H-04.console.txt | 2026-09-22_board/66_sys43_b11_wdg1.console.txt; 2026-09-22_board/66_sys43_b11_wdg3.console.txt; 2026-09-22_board/67_sys43_b11_record.console.txt; fmea B11 |
| BT-RUN-01 | Normal operation over two ROM-check periods - no safety event, ROM check repeated at its period | RUN | SSR-067(a), SSR-067(b), SSR-034(a), SSR-018(b), SSR-018(c), SSR-010(c), SSR-021, SSR-022, SSR-023 | dev | console, can | 2026-10-02_suite-integ12/BT-RUN-01.console.txt | 2026-09-29_board/board0929_02_session.console.txt; 2026-09-22_board/139_sys53_5min.console.txt; 2026-09-22_board/118_sys45_b4_load.console.txt; fmea B4 |
| BT-RUN-02 | Safety cycle and RTE deadlines under load (load-test build, 10 min) - deadline reported, no reset | RUN | SSR-015, SSR-018(b), SSR-020(d), SSR-067(a) | test | console | none yet | 2026-09-22_board/118_sys45_b4_load.console.txt; 2026-09-21_board/48_sys05_loadtest.console.txt; 2026-09-21_board/49_sys05_runnables.console.txt; 2026-09-22_board/33_sys40_b4_run; fmea B4 |
| BT-RUN-03 | E2E-protected reception through every qualifier state (boardtest qualifier) | RUN | SSR-041 | dev | console, can | none yet | 2026-09-16_com14_lauf1.asc; 2026-09-16_com14_lauf1.console.txt; 2026-09-16_qualifier_led2-einzelframe.asc; 2026-09-16_qualifier_led2-einzelframe.console.txt |
| BT-RUN-04 | Watchdog configuration and clock source read on the target (J-Link) | RUN | SSR-012, SSR-010(a), SSR-010(b) | dev | console, jlink, user | none yet | 2026-09-22_board/164_sys59_wdogint_r1.raw.txt; fmea B6 |
| BT-RUN-05 | Pin lock of the safety-relevant pins read back (J-Link, B8) | RUN | SSR-033(a), SSR-033(b), SSR-070 | dev | console, jlink, user | none yet | 2026-09-22_board/02_sys42_b8_jlink.txt; fmea B8 |
| BT-RUN-06 | Debug halt deadman - a halt longer than the deadman time-out inhibits the PWM and switches DRV_EN off | RUN | SSR-016 | dev | console, jlink, user | 2026-09-22_board/25_sys40_b7c.console.txt; 2026-09-22_board/44_sys35_p7_halt.console.txt | 2026-09-21_board/07_io32_halt_log.console.txt; 2026-09-22_board/44_sys35_p7_halt.console.txt |
| BT-RUN-07 | ROM check during repeated NvM writes - no failure, no overdue pass | RUN | SSR-067(c) | dev | console | 2026-10-02_suite-integ12/BT-RUN-07.console.txt | 2026-09-22_board/30_sys44_b9_nvm.console.txt; fmea B9 |
| BT-RUN-08 | CAN bus state - bus off by a long short of CANH/CANL, automatic recovery | RUN | SSR-042(a) | dev | console, can, user | 2026-09-23_board/10_com35_can_status_after.console.txt + 2026-09-22_board/90_com26_p9_recovered.console.txt | 2026-09-22_board/91_com26_p9_ack.console.txt; 2026-09-22_board/92_com26_p9_after.console.txt; 2026-09-21_board/82_com26_busoff.log; 2026-09-23_board/09_nvm03_busoff.console.txt; 2026-09-23_board/31_com35_busoff_short.raw.txt; 2026-09-23_board/32_com35_status_short.console.txt; 2026-10-02_board/com05_busoff.txt |
| BT-RUN-09 | Analogue value at its range limits - qualifier range error and error | RUN | SSR-040 | dev, not on this project | console, source, user | none yet | 2026-09-30_board/tool15_01_rte_signals.console.txt |
| BT-RUN-10 | safe_off outputs - out set refused in every spelling, a foreign level taken back, a commanded one kept | RUN | SSR-005(g), SSR-005(h), SSR-005(i) | dev | console, jlink, user | none yet | 2026-10-01_board/sys150_console.txt; 2026-10-01_board/n11demo_safeoff0.txt |
| BT-SLEEP-01 | Sleep through the console, run-down steps, wake-up by a CAN frame | SLEEP | SSR-052(a), SSR-052(b), SSR-050(a) | dev | console, can, user | none yet | CHANGELOG SYS-11; CHANGELOG COM-39 |
| BT-FLASH-01 | Application download over CAN, start of the new image, versions over UDS | FLASH | SSR-051(a), SSR-051(b), SSR-024(a), SSR-024(d) | dev | console, can, user | 2026-09-21_board/43_flash_dev.log + 2026-09-21_board/44_flash_dev.console.txt | 2026-09-21_board/45_sys38_flash.log; 2026-09-21_board/70_com26_flash.log; 2026-09-29_board/board0929_02_session.console.txt |
| BT-FLASH-02 | Download interrupted by a power loss - bootloader keeps the ECU reachable, no start of a half image | FLASH | SSR-051(a), SSR-051(c) | dev | console, can, user | none yet | 2026-09-21_board/41_ver07_powerloss_download.log; 2026-09-21_board/54_ver07a_powerloss.log; 2026-09-21_board/55_ver07a_after.log; 2026-09-21_board/52_bl26_poweron_noapp.log |
| BT-DIAG-01 | Diagnostic sessions and communication control - safety supervision unaffected | DIAG | SSR-067(a), SSR-042(a) | dev | console, can | none yet | 2026-09-21_board/24_com28_uds.txt; 2026-09-23_board/03_plat08_uds.txt |
| BT-PWM-01 | Active short and freewheel of a PWM group at standstill, fallbacks and start-up test (B12-B14) | S1 | SSR-089, SSR-091, SSR-092 | test, not on this project | console, jlink, scope, 24v, motor, user | none yet | fmea B12; fmea B13; fmea B14 |
| BT-PWM-02 | Degraded reaction - only the mapped groups switched off, latched until the power cycle | S1 | SSR-087 | test, not on this project | console, jlink, user | none yet | fmea B16 |
<!-- bts-matrix:end -->

### 5.3 Board items without a case on this project

| Item | Reason (suite field `no_board_case`) |
|---|---|
| SSR-020(b), SSR-020(c) | write access only with interrupts suspended and closed on every exit: properties of the code path, not observable at the console or the pins; host tests of the safety shell and the MPU fault of BT-S2-02 |
| SSR-024(b) | a property of the build (the link step refuses a missing or implausible list); build tests of the ROM check; the board shows the result in BT-START-01 and BT-FLASH-01 |
| SSR-042(b) | the eval board offers no console command or harmless register write that takes the CAN transceiver out of its ready state; host test `can-bus-state` |
| SSR-052(c) | a run-down step beyond its time-out needs a fault the eval board cannot produce without a code change; host test `ecustate` |
| SSR-082(a), SSR-082(b) | open in `ssr.sdoc` (the identity is not in the image yet); the case follows the implementation |
| SSR-040, SSR-087, SSR-089, SSR-091, SSR-092 | specified as BT-RUN-09, BT-PWM-01 and BT-PWM-02, but not runnable with the demo project (no `adc.qlf` port, no degraded mapping, no active short); they run on a project that configures the feature, on a board with a power stage where needed |

### 5.4 BT-RUN-08 in detail - bus off and automatic recovery (BTS-6 b)

Equipment: console, CAN adapter, a person at the board (`user`). Image: `dev`.

1. `can status` before the fault: `CAN0 open, phy ..., tx ...` (the bus is open and the ECU transmits).
2. Hand action (operator step of the suite): short CANH and CANL of CAN0 for **at least 10 s** while the ECU
   transmits, or pull the terminating resistor and every other node so that no frame is acknowledged. A short of 3 s
   (2026-09-23) or a wrong bit rate (2026-10-02, `com05_busoff`) only reaches error passive and does not satisfy the
   case. Then remove the short (or restore the termination), wait 5 s, press Enter.
3. `can status` after the fault must show, in one line: `bus off N times (now .. ms, longest .. ms, R recoveries)`
   with **N >= 1 and R >= 1**, `ever bus off yes` and `recovery automatic`. The bus state at that moment is
   `error active` (recovered) or `error passive`, but never stuck in `bus off`. A status with `bus off 0 times` fails.
4. Automatic recovery without a reset or an operator command: `canprobe` (3 s) must see the ECU's frames on the bus
   again (`#> canprobe: passed`).

Reference capture: the existing records stay (`2026-09-23_board/10_com35_can_status_after`, which shows the error-passive status after the short, no bus off yet,
and `2026-09-22_board/90_com26_p9_recovered`, which shows `bus off 9812 times ... recovery automatic` of COM-26); the
first run that reaches the bus off on CAN0 replaces them as the reference.


---

## 6. Verification of the suite itself (host)

`tools/boardtest/test_suite.py` (no board, no adapter):

| Class | Shows |
|---|---|
| `SuiteConsistencyTest` | the suite is consistent with `ssr.sdoc` (every board item covered or exempted with a reason, no unknown SSR, references and earlier records on disk, vocabulary of operating cases, images and equipment); every operating case has a runnable case; the reader of `ssr.sdoc` finds clauses, methods and withdrawn clauses; faults in a copy of the suite are found |
| `SpecDocumentTest` | the tables of section 5.2 are the generated ones; scope, 24 V and motor are marked where the cases need them |
| `EvaluatorTest` | order, windows of `absent`, optional checks, values and constraints, recorder time stamps and colour codes, `delta` against the project value and against the module default, with a second test profile (a project with its own ROM-check period) |
| `ReferenceReplayTest` | every procedure passes each board capture it names; removing the lines any `expect` finds turns it red; a forbidden line in any `absent` window turns it red; counter captures (a watchdog reset against `lc assert`, a held state against a restart, a software-reset start against the power-on case, ...) fail; the sleep lines are the format strings of the firmware |
| `RunnerTest` | complete runs against a simulated board on a virtual clock: power-on start with the documented start line (pass) and with a software-reset start (fail), `safety wdg` with the restart gap measured on CAN, the diagnostic session with communication control against UDS answers recorded on the board (pass, and fail when the ECU keeps sending), sleep and wake-up by a CAN frame, a download with the recorded canflash output and a segmented UDS answer; transcript, values and `suite-run.json` |
| `SuiteCommandLineTest` | `suite check/list/matrix/replay`, `run` refusing without `--out` and for a case of another project |

---

## 7. Records and archive

A run with `--out <folder>` leaves, per case, the transcript `<case>.console.txt` and, for the whole run,
`suite-run.json` and `summary.md`:

- **what ran on what:** project, suite file, baseline (tag or commit given with `--baseline`), `git describe` of the
  repository, the image given with `--image` with its SHA-256, the tester, start and end time;
- **per case:** result (passed, failed, error), the SSRs it covers, the values it captured (start reason, reset-loop
  cause, restart gap, image CRC from canflash, operator readings such as the walk time on the scope), and every
  failed check with its step.

The folder goes to `tools/boardtest/captures/<project>/<date>_suite-<baseline>/` and is committed like every
other board capture; the verification report of the baseline links it (SYS-167 decides which cases a safety release
needs). A failed case is a finding: it goes to the anomaly register with the record as evidence, never edited away.
A transcript of a passed run can become the reference of its case (`references:` in the suite file), so the host
checks follow the firmware the board really ran.

---

## 8. Open points and review points

**Open:** no case of this issue ran at the board. The next session at the board runs the runnable cases on the
current image of `dev` (order: START, RUN, DIAG, S1, S2, FLASH, SLEEP, S1H, the cases with a test or special image
grouped by image), keeps the records as section 7 says and turns the first passed transcript of each case without a
reference into its reference. Thirteen cases have no reference capture today (column "Reference capture" = none
yet): three are for other projects; BT-START-02, BT-SLEEP-01 and BT-DIAG-01 run on the host against the simulated
board (the start line from `RcmResetReason.h`, the sleep lines from the format strings of `EcuStateSystem.cpp`, the
UDS answers recorded on the board), BT-RUN-03 is a
tool step tested by `QualifierTest`; the others consist mostly of operator readings.

Review points for the safety manager:

| ID | Title | Statement | Options | Recommendation |
|---|---|---|---|---|
| BTS-1 | Operator readings as evidence | scope times, J-Link register values and visual checks (LED, current) enter the record as answers of the person at the board, without a file of the instrument | (a) accept the answer plus the scope screenshot or CSV stored next to the record (LabControl `csv_path`); (b) require an automatic measurement for every reading | (a) |
| BTS-2 | References from earlier firmware | decided (a) on 2026-10-02: where the first suite run of 2026-10-02 (`2026-10-02_suite-integ12`) gives a capture of the current firmware, the case's reference is that capture and its checks accept the current form only: BT-S2-01 `WATCHDOG RESET: outputs off, dump saved` and cause `0x1` (SYS-148/SYS-169), BT-S1-01 the event, KL30B and safe-state lines of SYS-169, BT-RUN-01 the log prefix of the project name (PLAT-98). Still older references: the leave step of BT-S1-01, the other cases of the earlier sessions; the fault handler cases keep `HARD FAULT RESET` | (a) replace them by the transcripts of the next board run and narrow the checks to the current form; (b) keep both forms | (a), decided |
| BTS-3 | Restart gap and AoU-05 | BT-S2-01 records the CAN gap of a watchdog reset but does not fail on it; the value of AoU-05 (fmea 8.1 B2, 1 077 ms) is the comparison | (a) report only, a larger value is a finding for AoU-05; (b) fail above the AoU-05 value | (a) |
| BTS-4 | Items without a board case | SSR-020(b)(c), SSR-024(b), SSR-042(b), SSR-052(c) rest on host tests and review (section 5.3) | (a) accept with the reasons given; (b) ask for test builds that make them observable | (a) |
| BTS-5 | Features the demo does not configure | SSR-040, SSR-087, SSR-089, SSR-091, SSR-092 have specified cases that need another project (and a board with a power stage); until such a project exists their board method is not met | (a) keep "board" as the method and run them with the first project that configures the feature; (b) change their method to test plus the existing fmea checks | (a) |
| BTS-6 | Bus off not reached | the short of CANH/CANL on 2026-09-23 reached error passive, not bus off; decided (b) on 2026-10-02: BT-RUN-08 requires the bus off (counter and recovery at least 1, `ever bus off yes`) after a short of at least 10 s or a missing termination, and fails a run that stays in error passive | (a) record the counters only; (b) require bus off with a longer short or a missing termination | (b), decided |

---

## 9. Review questions of the architecture (section 11)

The tool and the suite file are the only new parts. **Board without an SBC, other crystal, other derivative,
several CAN buses, several tasks:** the tool takes the console baud rate, the CAN bus with its bit timing, the
messages of the ECU, the diagnostic addresses and every project value from the project (`tools/boardtest/project.py`
and `project_value`); the board-specific expectations (console lines of this firmware, signal names, the expected
`WDOG_CS`) live only in the project's suite file, and another project writes its own. **Exactly one configuration
level:** the suite reads project values (ROM-check period) at run time and writes none. **Own test profile:** the
evaluator is tested with a synthetic project whose ROM-check period differs from the eval board's. **Core free of
Qt, messages with a path:** no Qt; suite errors name the file and the YAML path (`procedures.BT-X-01.steps[0]...`).

---

## 10. Change history

| Issue | Date | Change |
|---|---|---|
| 1 | 2026-10-02 | first issue (SYS-75): 31 cases in eight operating cases, suite file and runner in `tools/boardtest`, host verification against the board captures |
