# tools/boardtest – board measurement scripts with host tests (TOOL-05)

The coordinator's measurement scripts used to live in the scratchpad; when `rte runnables` switched from `ticks` to `cycles`
they silently counted every answer as lost. Here they live next to the firmware, with three rules:

1. **Every value from the console goes through a parser** (`console.py`) that raises `FormatError` on an unknown format
   instead of returning nothing.
2. **Every test reads once before anything long begins**, so a format error aborts after seconds.
3. **No fixed board values**: console baud rate, CAN bus with bit timing, messages and cycles (DBC), E2E configuration,
   qualifier times, diagnostic addresses and RTE tasks come from the project (`project.py`); serial port, CAN adapter and
   channel come from the command line.

`test_boardtest.py` checks this without hardware against **recorded console answers**, a **CAN capture** (`captures/`) and
the **format strings of the firmware** (`ecu/application/src/systems/RteConsole.h`, `rtegen`).

## Invocation

```sh
.venv/Scripts/python tools/boardtest --project projects/evalboard_s32k144_demo --port COM9 <test> [options]
```

Common options: `--project` (mandatory, the flashed project), `--port` (console), `--baud` (default: the only UART
of the project), `--interface`/`--channel`/`--app-name` (python-can, default `vector`, channel 0),
`--bitrate`/`--sample-point`/`--sjw`/`--quanta` (default: project `can.<bus>` of the bus from `rte.yaml can.bus`,
16 tq; for Vector this gives SJW 2, TSEG1 12, TSEG2 3 with the demo project), `--record <stem>` (writes
`<stem>.console.txt` and `<stem>.asc` in the format of `captures/` – this is how a board run becomes a new capture).

Exit code: 0 passed, 1 measurement not passed, 2 format error or missing configuration.

| Test | Invocation | What it proves | Resource |
|---|---|---|---|
| `console` | `console "ecu status" "rte runnables"` | answers in the capture format (`### <command>`), replaces `console.py` | console |
| `capture` | `capture 20` or `capture 5 "rte json"` | everything that arrives within N seconds (start-up, log), replaces `longcmd.py`; output as it arrives, Ctrl+C ends early (see below) | console |
| `echo` | `echo --count 200` | N commands `rte runnables`, every answer fully readable, all tasks from `rte.yaml` present (IO-17: characters lost in the UART RX path) | console |
| `drift` | `drift 540 --limit-ppm 100` | system time against the PC: Δcycles × task period against `time.monotonic()` on arrival of the task line, resolution ≈ one period (ARCH-14: 10 ms / 9 min ≈ 20 ppm) | console |
| `canprobe` | `canprobe --seconds 3` | the cyclic messages the node sends according to the DBC, with measured cycle (±20 %), and UDS `3E 00` to the diagnostic address of the project with answer `7E 00` | CAN |
| `qualifier` | `qualifier --observe EVB_Status.Led2 --expect-gated` | qualifier of an E2E-protected receive message through all states (COM-03/COM-13): before the first frame DEBOUNCE or TIMEOUT, valid OK, wrong CRC ERROR_CRC, stuck counter ERROR_ALIVE, sending out of ERROR_TIMEOUT, recovery OK; optionally one signal of a transmit message per phase (DemoSwc: Led2 only blinks on OK) | console and CAN |
| `wake` | `wake 0x123 --dlc 0 --count 3` | sends a candidate wake frame (e.g. a partial-networking probe, COM-40) with the project's bit timing, reopening the CAN channel before every attempt | CAN |
| `suite` | `suite run START S2 --out <folder>` | the board test cases per SSR (SYS-75, below): `list`, `check`, `matrix`, `replay` without hardware, `run` at the board | per case |

`qualifier`: message `--message` (default: the first receive message with `can.qualifiers.<Msg>.e2e`), payload `--payload <hex>`
(default: initial values of the DBC; CRC and counter by `tools/e2e/e2e_ref.py`), `--window` seconds per phase after the
settling time.

`canprobe` listens 0.3 s before its window and drops every frame whose **adapter time stamp** lies before the window's start
(TOOL-46: right after opening, the VN1610 delivers a burst of older frames that would shorten the measured cycle); the offset
between adapter clock and `time.monotonic()` is learnt from the frames (`AdapterTime` in `checks.py`) and the output names
both counts. `qualifier` filters its observed signal the same way.

`capture` prints every line as it arrives and `--record` writes every block read into the file immediately (TOOL-25).
**Ctrl+C** ends the recording early (`capture interrupted after … s`, exit code 130); even a killed process leaves
everything up to the last read in the file. Without a command the file has no `###` line: a plain log, not an answer capture
for `load_capture`.

`wake` (COM-40): the partial-networking decoder of the SBC is stricter about the bus bit timing than FlexCAN, so `wake` takes
`--sample-point`/`--sjw`/`--quanta`/`--bitrate` from the project like every other subcommand and opens the CAN channel
**again before every attempt** (an unacknowledged frame stays in the VN1610's transmit mailbox and would block the next
send). `--dlc`/`--data` build the payload, `--extended` a 29-bit id, `--count`/`--gap` repeat it. The SBC requirement is in
`libs/bsw/sbcUja113x/doc/sbcUja113x.md` ("Bitzeit des Weckrahmens"); whether the board wakes is read at the board, not by this
tool.

## Board test suite per SSR (`suite`, SYS-75)

The board test cases of a project live in `suites/<project>.yaml`; the specification that explains them per software
safety requirement and operating case is [`doc/safety/board-test-spec.md`](../../doc/safety/board-test-spec.md),
whose tables `suite matrix` generates. A case is a procedure: steps that act (console command, console capture,
`canprobe`, CAN silence, UDS request, wake frame, `qualifier`, an external command such as `canflash`, an instruction
or a question to the person at the board) and checks on the transcript (`expect`, `absent`, `optional`, `equals`,
`differs`, `delta` against a project value). The runner (`suite.py`) records the console in the capture format and
adds `#> ...` lines for CAN results, operator answers and tool output; the same evaluator judges a live transcript and
a recorded capture.

```sh
.venv/Scripts/python tools/boardtest --project projects/evalboard_s32k144_demo suite list [--oc S2] [--ssr SSR-065]
.venv/Scripts/python tools/boardtest --project projects/evalboard_s32k144_demo suite check
.venv/Scripts/python tools/boardtest --project projects/evalboard_s32k144_demo suite replay BT-S2-04 [capture ...]
.venv/Scripts/python tools/boardtest --project projects/evalboard_s32k144_demo --port COM17 suite run all \
    --out tools/boardtest/captures/evalboard_s32k144_demo/<date>_suite-<tag> --image <app.hex> --baseline <tag>
```

`run` writes `<case>.console.txt` per case and `suite-run.json` / `summary.md` (image SHA-256, `git describe`,
baseline, tester, result, values and failed checks per case). Cases marked "not on this project" are refused. The
board rules below and in CLAUDE.md hold for every case: a case that needs a board-rule command (`ecu sleep`,
`MOT1_GD_DRV_EN`, a power cycle) lists `user` in its equipment and is run by the user only.

## Safety rules

The tests only read and only send diagnostic and application messages of the DBC: no flashing, no `sbc` command.
Exception: the `suite` cases of the operating case FLASH run `canflash flash` after the operator pressed Enter.
`console`/`capture` send whatever they are given – for `sbc sleep`, `sbc failstart`, `kl30b on` the rules from
`CLAUDE.md` apply (only with the user on site).

## Captures (`captures/<project>/`)

| File | Origin |
|---|---|
| `2026-09-16_status.console.txt` | board, demo project after RTE-02, the coordinator's `console.py` (ecu/sbc/safeadc/rte runnables/adcaccess); line endings normalised |
| `2026-09-16_measure.console.txt` | board, same state, measurement commands of the Studio analysis (`rte json`, `rte runnables`, `stats cpu`, `stats stack`) |
| `2026-09-30_board/tool15_01_rte_signals.console.txt` | **board** (remote session "Board tests heute", `v0.1.0-262-g1c329e0d`, board item `board-0930-remote`): one console session `rte signals`/`stats cpu`/`rte runnables` - replaces the reconstructed `rte signals` reference of `test_boardtest.py` (TOOL-201, TOOL-15) |
| `2026-09-30_board/tool15_02_canprobe.asc` | **board** (same session): `EVB_Status`/`EVB_Analog` at their real cadence with the recorded start-up burst (TOOL-46), UDS `3E 00`/`7E 00` as measured on the board - replaces `canprobe.synthetic.asc` (TOOL-201, TOOL-15) |

`rte_signals.reconstructed.console.txt` is unused by `test_boardtest.py` since TOOL-201 but `tools/studio/tests/test_livedata.py`
(`test_several_answers_split_at_ok`) needs its three synthetic qualifier states (8, 1, 4) verbatim; change both together.
Format of the console captures: comment lines `#` at the start, then per command `### <command>` and the answer.

## Host tests

```sh
.venv/Scripts/python -m unittest tools/boardtest/test_boardtest.py      # about 70 s, no hardware
```

The classes cover the capture parser (`ticks` instead of `cycles` is a `FormatError`), the firmware format strings against
`RteConsole.h` and `rtegen`, `echo`, `drift`, `canprobe`, `qualifier` and `capture` against recorded answers and simulated
boards (each with a counter-check that fails), the project setup (demo and S32K148 profile), and `wake` against a fake
adapter and a mocked `canflash.bus_for`. `test_suite.py` (SYS-75, entry `boardtest-suite`, about 60 s) checks the suite:
consistency with `doc/requirements/safety/ssr.sdoc` and the tables of `doc/safety/board-test-spec.md`, every procedure against
the board captures it names (with a mutation per check) and five procedures end to end against a simulated board on a virtual
clock.
