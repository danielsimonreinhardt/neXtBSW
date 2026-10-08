"""Command line of the board tests (see README.md). Opens the serial port and the CAN adapter - the only place
that does; checks.py and the host tests work on transports handed in."""

import argparse
import contextlib
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import can  # noqa: E402

import checks  # noqa: E402
import console as con  # noqa: E402
import project as proj  # noqa: E402

INTERRUPTED = 130       # exit code of a capture ended with Ctrl+C (what it read is printed and recorded)


def open_console(args, setup):
    baudrate = args.baud or setup.baudrate
    if not args.port:
        raise proj.SetupError("--port is needed (serial port of the ECU console on this PC, e.g. COM9)")
    if not baudrate:
        raise proj.SetupError(f"{setup.project}: the project has not exactly one UART, pass --baud")
    transport = con.SerialTransport(args.port, baudrate)
    if args.record:
        transport = con.Recorder(transport, Path(args.record).with_suffix(".console.txt"))
    return transport


def _canflash():
    sys.path.insert(0, str(HERE.parent / "canflash"))
    import canflash

    return canflash


def resolve_can_timing(args, setup):
    """Bit timing for the adapter: command line first, then the project's bus (rte.yaml can.bus -> project
    can.<bus>), one source for every subcommand (COM-40) - never a constant in the test."""
    canflash = _canflash()
    if setup.timing is None:
        raise proj.SetupError(f"{setup.project}: the ECU description has no CAN bus (rte.yaml can)")
    return canflash.BusTiming(args.bitrate or setup.timing.bitrate,
                              args.sample_point if args.sample_point is not None else setup.timing.sample_point_percent,
                              args.sjw if args.sjw is not None else setup.timing.sjw, args.quanta)


def open_can(args, setup):
    canflash = _canflash()
    timing = resolve_can_timing(args, setup)
    bus = canflash.bus_for(args.interface, args.channel, timing, args.app_name)
    if args.record:
        # a Notifier would take the frames away from the test; the wrapper logs what the test sends and reads
        return checks.RecordingBus(bus, can.Logger(str(Path(args.record).with_suffix(".asc"))))
    return bus


def can_opener(args, setup, stack):
    """Factory that opens a fresh CAN bus with the project's bit timing on every call (COM-40, finding COM-39
    2026-09-28): the VN1610 keeps a frame nobody acknowledged in its own send mailbox - exactly the case a
    partial-networking wake probe tests - and that blocks every later send on a bus kept open. The channel is
    therefore closed and reopened before each attempt instead of reused. With --record every attempt is logged
    into the same trace file (the logger is closed once, at the end, via `stack`)."""
    canflash = _canflash()
    timing = resolve_can_timing(args, setup)
    logger = None
    if args.record:
        logger = can.Logger(str(Path(args.record).with_suffix(".asc")))
        stack.callback(logger.stop)

    def opener():
        bus = canflash.bus_for(args.interface, args.channel, timing, args.app_name)
        return checks.RecordingBus(bus, logger, owns_logger=False) if logger is not None else bus

    return opener


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="boardtest", description=__doc__)
    parser.add_argument("--project", required=True, help="project.yaml (or its directory) of the flashed firmware")
    parser.add_argument("--port", help="serial port of the console (test PC), e.g. COM9")
    parser.add_argument("--baud", type=int, help="console baud rate (default: the project's only UART)")
    parser.add_argument("--interface", default="vector", help="python-can interface (default vector)")
    parser.add_argument("--channel", default=0, help="adapter channel (default 0)")
    parser.add_argument("--app-name", default=None, help="Vector application name (default: global channel index)")
    parser.add_argument("--bitrate", type=int, help="default: project can.<bus> of rte.yaml can.bus")
    parser.add_argument("--sample-point", type=float, help="sample point in %% (default: project)")
    parser.add_argument("--sjw", type=int, help="SJW in tq (default: project)")
    parser.add_argument("--quanta", type=int, default=16, help="time quanta per bit of the adapter (default 16)")
    parser.add_argument("--record", help="file stem: console transcript <stem>.console.txt and CAN trace <stem>.asc "
                                         "in the capture format of the host tests")
    sub = parser.add_subparsers(dest="test", required=True)

    p = sub.add_parser("console", help="send commands, print the answers (transcript format)")
    p.add_argument("commands", nargs="+")
    p.add_argument("--timeout", type=float, default=1.5)

    p = sub.add_parser("capture", help="send one command (or none) and print everything for N seconds")
    p.add_argument("seconds", type=float)
    p.add_argument("command", nargs="?", default="")

    p = sub.add_parser("echo", help="N console commands, every answer must parse (IO-17)")
    p.add_argument("--count", type=int, default=200)
    p.add_argument("--command", default="rte runnables")

    p = sub.add_parser("drift", help="system time of the ECU against the PC over N seconds (ARCH-14)")
    p.add_argument("seconds", type=float)
    p.add_argument("--task", help="RTE task whose cycle counter is used (default: the first of rte.yaml)")
    p.add_argument("--limit-ppm", type=float, help="fail above this drift")

    p = sub.add_parser("canprobe", help="cyclic messages of the ECU and a TesterPresent answer")
    p.add_argument("--seconds", type=float, default=3.0)

    p = sub.add_parser("qualifier", help="qualifier phases of a received E2E message (COM-03/COM-13)")
    p.add_argument("--message", help="received message with can.qualifiers e2e (default: the first one)")
    p.add_argument("--payload", help="frame bytes as hex before E2E protection (default: DBC start values)")
    p.add_argument("--observe", help="Message.Signal of a sent message to record per phase, e.g. EVB_Status.Led2")
    p.add_argument("--expect-gated", action="store_true",
                   help="the observed signal must change in QLF_OK phases and stay 0 in all others")
    p.add_argument("--window", type=float, default=4.0, help="seconds per phase after settling (default 4)")

    p = sub.add_parser("wake", help="send a candidate wake frame with the project's bit timing, reopening the "
                                    "channel before every attempt (COM-40, selective/partial-networking wake-up)")
    p.add_argument("id", type=lambda s: int(s, 0), help="arbitration id, decimal or 0x-hex, e.g. 0x123")
    p.add_argument("--dlc", type=int, default=0, help="data length code if --data is not given (default 0: "
                                                       "COM-39 found DLC 0 woke, DLC 1 for 40 s did not)")
    p.add_argument("--data", help="frame data as hex bytes, e.g. 0011 (default: --dlc zero bytes)")
    p.add_argument("--extended", action="store_true", help="29-bit identifier")
    p.add_argument("--count", type=int, default=1, help="attempts, channel reopened before each (default 1)")
    p.add_argument("--gap", type=float, default=0.2, help="seconds between attempts (default 0.2)")

    p = sub.add_parser("suite", help="board test suite per SSR (SYS-75, doc/safety/board-test-spec.md): list, "
                                     "check, matrix, replay recorded captures, run at the board")
    p.add_argument("action", choices=("list", "check", "matrix", "replay", "run"))
    p.add_argument("cases", nargs="*", help="run: case ids, operating cases (START, S2 ...) or 'all'; replay: one "
                                            "case id, then capture files (default: the case's references)")
    p.add_argument("--suite", help="suite file (default: tools/boardtest/suites/<project>.yaml)")
    p.add_argument("--oc", help="list: only this operating case")
    p.add_argument("--ssr", help="list: only cases covering this SSR or clause")
    p.add_argument("--out", help="run: folder for transcripts and suite-run.json (e.g. captures/<project>/"
                                 "<date>_suite-<tag>)")
    p.add_argument("--image", help="run: the flashed image (hex); its SHA-256 goes into the record, {image} of steps")
    p.add_argument("--baseline", default="", help="run: tag or commit the image was built from")
    p.add_argument("--tester", default="", help="run: who ran it")
    return parser


def run_suite(args, setup) -> int:
    """`boardtest suite ...` - only `run` opens the console and the CAN adapter."""
    import suite as st

    the_suite = st.load_suite(Path(args.suite) if args.suite else st.suite_for(setup.project.parent))
    if args.action == "check":
        problems = st.validate(the_suite)
        for line in problems:
            print(line)
        print(f"suite {the_suite.path.name}: {len(the_suite.procedures)} case(s), "
              f"{'consistent' if not problems else f'{len(problems)} problem(s)'}")
        return 1 if problems else 0
    if args.action == "matrix":
        print(st.matrix(the_suite), end="")
        return 0
    if args.action == "list":
        for proc in the_suite.procedures.values():
            if args.oc and proc.oc != args.oc:
                continue
            if args.ssr and proc not in the_suite.covering(args.ssr):
                continue
            flags = "" if proc.runnable else "  [not on this project]"
            print(f"{proc.pid:13} {proc.oc:6} {proc.image:8} {','.join(proc.equipment):28} {proc.title}{flags}")
        return 0
    if args.action == "replay":
        if not args.cases:
            raise proj.SetupError("replay needs a case id (and optionally capture files)")
        proc = the_suite.procedure(args.cases[0])
        sets = [[Path(p) for p in args.cases[1:]]] if args.cases[1:] else \
            [[the_suite.captures / name for name in ref] for ref in proc.references]
        if not sets:
            raise proj.SetupError(f"{proc.pid} has no reference capture: {proc.reference_note}")
        ok = True
        for files in sets:
            text = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in files)
            ev = st.evaluate(proc, text, tool_checks=False, project_file=setup.project)
            ok &= ev.passed
            print(f"{proc.pid} against {' + '.join(f.name for f in files)}: {'passed' if ev.passed else 'FAILED'}")
            print("\n".join(ev.lines()))
            if ev.values:
                print("  values: " + ", ".join(f"{k}={v}" for k, v in ev.values.items()))
        return 0 if ok else 1
    # run
    if not args.out:
        raise proj.SetupError("suite run needs --out <folder> for the transcripts and the record")
    selected = []
    for name in args.cases or ["all"]:
        if name == "all":
            selected += [p for p in the_suite.procedures.values() if p.runnable]
        elif name in st.OPERATING_CASES:
            selected += [p for p in the_suite.procedures.values() if p.oc == name and p.runnable]
        else:
            selected.append(the_suite.procedure(name))
    selected = list({p.pid: p for p in selected}.values())
    for proc in selected:
        if not proc.runnable:
            raise proj.SetupError(f"{proc.pid} does not run on this project: {proc.not_runnable}")

    def open_console():
        return open_console_transport(args, setup)

    def open_bus():
        return canflash_bus(args, setup)

    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    runner = st.Runner(the_suite, setup, open_console, open_bus, st.Operator(), Path(args.out),
                       image=Path(args.image) if args.image else None)
    runs = []
    for proc in selected:
        print(f"\n=== {proc.pid}: {proc.title} (needs {', '.join(proc.equipment)}; image {proc.image}"
              + (f": {proc.build}" if proc.build else "") + ")")
        result = runner.run(proc)
        runs.append(result)
        if result.evaluation is not None:
            print("\n".join(result.evaluation.lines()))
        print(f"{proc.pid}: {'passed' if result.passed else 'FAILED'}" + (f" ({result.error})" if result.error else ""))
    record = st.write_record(Path(args.out), the_suite, runs, args.baseline,
                             Path(args.image) if args.image else None, args.tester, started)
    print(f"\nrecord: {record}")
    return 0 if all(r.passed for r in runs) else 1


def open_console_transport(args, setup):
    """Console transport without the --record wrapper (the suite records itself)."""
    baudrate = args.baud or setup.baudrate
    if not args.port:
        raise proj.SetupError("--port is needed (serial port of the ECU console on this PC, e.g. COM9)")
    if not baudrate:
        raise proj.SetupError(f"{setup.project}: the project has not exactly one UART, pass --baud")
    return con.SerialTransport(args.port, baudrate)


def canflash_bus(args, setup):
    canflash = _canflash()
    return canflash.bus_for(args.interface, args.channel, resolve_can_timing(args, setup), args.app_name)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # console output may carry bytes that are no text (power down, reset); a redirected stdout (cp1252 on
    # Windows) must not end a capture with UnicodeEncodeError (TOOL-25). Forcing the encoding to UTF-8 - the
    # same encoding Recorder uses for the capture file - additionally fixes umlauts turning into mojibake when
    # stdout is redirected: without it, a redirected stdout falls back to the OS locale's codepage (cp1252 on a
    # German Windows), which the file or terminal reading it back does not necessarily share (board finding
    # 2026-09-16, "Konsole nicht UTF-8"; a live console already gets UTF-8 from Python's own console I/O on
    # Windows - PEP 528 - and is unaffected).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        setup = proj.load(Path(args.project), with_diag=args.test in ("canprobe", "suite"))
    except proj.SetupError as e:
        print(f"boardtest: {e}", file=sys.stderr)
        return 2
    try:
        channel = int(args.channel)
    except ValueError:
        channel = args.channel
    args.channel = channel
    if args.test == "suite":
        import suite as st

        try:
            return run_suite(args, setup)
        except (proj.SetupError, st.SuiteError) as e:
            print(f"boardtest: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
    try:
        with contextlib.ExitStack() as stack:
            result = run(args, setup, stack)
    except (proj.SetupError, con.FormatError) as e:
        print(f"boardtest: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if result is None:
        return 0
    if result == INTERRUPTED:
        return INTERRUPTED
    for line in result.lines:
        print(line)
    print(f"{result.name}: {'passed' if result.passed else 'FAILED'}")
    return 0 if result.passed else 1


def run(args, setup, stack):
    needs_console = args.test in ("console", "capture", "echo", "drift", "qualifier")
    needs_can = args.test in ("canprobe", "qualifier")
    console = None
    if needs_console:
        transport = open_console(args, setup)
        stack.callback(transport.close)
        console = con.Console(transport)
    bus = None
    if needs_can:
        bus = open_can(args, setup)
        stack.callback(bus.shutdown)

    if args.test == "wake":
        data = bytes.fromhex(args.data) if args.data else bytes(args.dlc)
        frame = can.Message(arbitration_id=args.id, is_extended_id=args.extended, data=data)
        opener = can_opener(args, setup, stack)
        return checks.send_wake_frames(opener, frame, args.count, args.gap)

    if args.test == "console":
        for command in args.commands:
            answer = console.command(command, args.timeout)
            print(f"### {command}\n{answer.text.replace(chr(13), '').strip()}")
        return None
    if args.test == "capture":
        # printed while it arrives; with --record every read is on disk at once (TOOL-25)
        result = console.capture(args.seconds, args.command, out=sys.stdout)
        if not result.text.endswith("\n"):
            print()
        if result.interrupted:
            print(f"boardtest: capture interrupted after {result.elapsed_s:.1f} s of {args.seconds:g} s, "
                  f"{len(result.text)} characters kept", file=sys.stderr)
            return INTERRUPTED
        return None
    if args.test == "echo":
        return checks.echo(console, setup, args.count, args.command)
    if args.test == "drift":
        return checks.drift(console, setup, args.seconds, task=args.task, limit_ppm=args.limit_ppm)
    if args.test == "canprobe":
        return checks.can_probe(bus, setup, args.seconds)
    if args.test == "qualifier":
        payload = bytes.fromhex(args.payload) if args.payload else None
        return checks.qualifier_phases(console, bus, setup, args.message, payload, args.observe, args.expect_gated,
                                       args.window)
    raise AssertionError(args.test)


if __name__ == "__main__":
    sys.exit(main())
