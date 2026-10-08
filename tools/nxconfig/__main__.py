"""Command line:
    python tools/nxconfig check <project.yaml> [--json]
    python tools/nxconfig cmake <project.yaml | project dir> <out.cmake> [--mcudb <dir>]
    python tools/nxconfig new <name> --board <board> [--all-signals]
                              [--safe-state-output <output> --locked-pin <output> --test-pin <pin>]
                              | --copy <project> [--dir <folder>] [--diag] [--calibration] [--dry-run]
    python tools/nxconfig new --safety-candidates --board <board>
    python tools/nxconfig new --list
    python tools/nxconfig board new <name> --mcu <derivative> [--package <package>] [--can ...] [--dry-run]
    python tools/nxconfig board list [--mcu <derivative>] [--package <package>] [--function <signal>]
    python tools/nxconfig board netlist <file.net> (--mcu <derivative> [--package <package>] | --board <board.yaml>)
    python tools/nxconfig version [<project.yaml | folder>]
    python tools/nxconfig pin [<project.yaml | folder>] [--release] [--json]
    python tools/nxconfig migrate <workspace | file> [--write] [--step <id>] [--no-diff]
    python tools/nxconfig migrate --list
    python tools/nxconfig csec-key new|where <project.yaml | project dir>
    python tools/nxconfig csec-key scan [<repository>]
    python tools/nxconfig identity <project.yaml | project dir> [--files] [--header]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nxconfig  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402
from nxconfig import i2c as nxi2c  # noqa: E402
from nxconfig import newboard, powerstage, sensed, supply  # noqa: E402
from nxconfig import pinevent  # noqa: E402  IO-51
from nxconfig import hwthreshold  # noqa: E402  SYS-86
from nxconfig import samplegroup  # noqa: E402  IO-62


def _project_file(path: Path) -> Path:
    return path / "project.yaml" if path.is_dir() else path


def newproject_effect(key: str) -> str:
    from nxconfig import newproject
    return newproject.SAFETY_EFFECTS[key].replace("%", "%%")


def _new_workspace(args):
    """TOOL-216 (gap N20): the workspace `nxconfig new` works in - `--workspace`, else the one above `--dir`, else the
    one above the current folder (the way every tool finds nextbsw.yaml). Returns (workspace or None, ok); findings
    of a pin with errors are printed."""
    from nxconfig import workspace as nxworkspace
    from nxconfig.config import Reporter

    reporter = Reporter()
    if args.workspace:
        path = Path(args.workspace)
        file = path if path.is_file() else path / nxworkspace.PIN_FILE
        if not file.is_file():
            print(f"error: {file} not found (--workspace takes the folder of {nxworkspace.PIN_FILE} or the file)",
                  file=sys.stderr)
            return None, False
        found, ws = True, nxworkspace.load(file, reporter, nxconfig.REPO)
    else:
        found, ws = nxworkspace.discover(args.dir or Path.cwd(), reporter, nxconfig.REPO)
    for m in reporter.messages:
        print(m, file=sys.stderr)
    return ws, not (found and ws is None)


def _new(args, parser) -> int:
    from nxconfig import newproject

    workspace, ok = _new_workspace(args)
    if not ok:
        return 1
    where = newproject.workspace_arguments(workspace) if workspace else {}
    if workspace and args.dir is None:
        args.dir = Path(workspace.root) / "projects" / (args.name or "")
    safety_args = [getattr(args, key) for key in newproject.SAFETY_KEYS]
    if args.safety_candidates:
        if not args.board:
            parser.error("new: --safety-candidates needs --board")
        try:
            offer = newproject.safety_candidates(args.board, **where)
        except newproject.NewProjectError as e:
            for problem in e.problems:
                print(f"error: {problem}", file=sys.stderr)
            return 1
        print(f"safety section of a project on board {offer.board} (TOOL-110: chosen by you, never derived):")
        for key in newproject.SAFETY_KEYS:
            print(f"  {key}: {newproject.SAFETY_EFFECTS[key]}")
        print("  digital outputs: " + (", ".join(f"{n} ({p})" for n, p in offer.outputs) or "none"))
        print("  test pin candidates (GPIO pins the board file does not describe): "
              + (", ".join(offer.test_pins) or "none"))
        print(f"  the family port needs the section ({offer.port})" if offer.required
              else "  the family port does not read the section")
        return 0
    if args.list:
        print("boards (empty project):")
        for b in newproject.list_boards(**{k: v for k, v in where.items() if k in ("repo", "workspace")}):
            state = "buildable" if b.buildable else (b.problem or "no board port: check only")
            print(f"  {b.name:<24} {b.mcu:<14} {state}")
        print("projects (copy):")
        for p in newproject.list_projects(**{k: v for k, v in where.items() if k in ("repo", "workspace")}):
            print(f"  {p.name:<24} board {p.board}")
        return 0
    if not args.name or not (args.board or args.copy):
        parser.error("new: NAME and --board or --copy are required (or --list)")
    if args.calibration and args.copy:
        parser.error("new: --calibration only with --board (a copy keeps the memory section of its source)")
    # TOOL-110: nothing safety related without an explicit choice - all three pins or none
    safety = None
    if any(safety_args):
        if not all(safety_args) or args.copy:
            parser.error("new: --safe-state-output, --locked-pin and --test-pin go together, and only with --board "
                         "(see --safety-candidates --board <board>)")
        safety = newproject.SafetyChoice(*safety_args)
    try:
        plan = newproject.plan(args.name, "empty" if args.board else "copy", board=args.board, source=args.copy,
                               target=args.dir, with_diag=args.diag, description=args.description,
                               signals="all" if args.all_signals else "minimal", safety=safety,
                               calibration=args.calibration, **where)
    except newproject.NewProjectError as e:
        for problem in e.problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    if plan.safety_required:
        # TOOL-126: never create a project the family port refuses - say it before anything is written
        print(f"error: the family port of board {plan.board.name} needs the safety section ({plan.safety_required}), "
              "and no pins were given - the project would be created with an error at safety. Choose them: "
              "--safe-state-output <output> --locked-pin <another output> --test-pin <unconnected pin> "
              f"(candidates and what each pin does: nxconfig new --safety-candidates --board {args.board}). "
              "Nothing written.", file=sys.stderr)
        return 1
    print(f"{plan.template} project {plan.name} in {plan.target}:")
    for rel in plan.tree():
        print(f"  {rel}")
    for note in plan.notes:
        print(f"note: {note}")
    for message in plan.expected_errors:
        # TOOL-126: what the check of the planned files found - said before writing, not only afterwards
        print(f"note: nxconfig will report after creation: {message.yaml_path or '-'}: {message.text}")
    if args.dry_run:
        return 0
    try:
        result = newproject.create(plan)
    except (newproject.NewProjectError, OSError) as e:
        print(f"error: {e} (nothing written)", file=sys.stderr)
        return 1
    for m in result.messages:
        print(m, file=sys.stderr)
    print(f"{plan.name}: created {result.project_file}, {len(result.errors)} error(s), "
          f"{len(result.messages) - len(result.errors)} warning(s)")
    return 1 if result.errors else 0


def _version(args) -> int:
    """neXtBSW version of the running tree and git describe; with a path also the workspace pin found from there."""
    from nxconfig import version as nxversion
    from nxconfig import workspace as nxworkspace

    tree = nxcfg.REPO
    try:
        version = nxversion.read_version(tree)
    except nxversion.VersionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"neXtBSW {version}")
    print(f"tree:         {tree}")
    print(f"git describe: {nxversion.git_describe(tree) or '(no git checkout)'}")
    if args.path is None:
        return 0
    reporter = nxcfg.Reporter()
    pinned, ws = nxworkspace.discover(args.path, reporter, tree)
    for m in reporter.messages:
        print(m, file=sys.stderr)
    if not pinned:
        print(f"workspace:    none ({nxworkspace.PIN_FILE} not found above {args.path}: the running tree)")
        return 0
    if ws is None:
        return 1
    tree_pin = ws.pin.nextbsw
    print(f"workspace:    {ws.root} ({ws.file.name})")
    print(f"pin:          {tree_pin.version}, {tree_pin.source} {tree_pin.path}, profile {tree_pin.profile}")
    for key in ("mcudb", "modules", "boards"):
        dirs = getattr(ws, key)
        if dirs:
            print(f"search.{key + ':':<8} {', '.join(str(d) for d in dirs)}")
    return 0


def _pin(args) -> int:
    """Release strictness of the workspace pin (TOOL-65, gap N10): annotated tag, reachable, clean, overlays.

    Exit code 1 for an error, 0 otherwise - a development pin (warning) keeps daily work green; `--release` is
    the switch that makes it red, the same switch `tools/ci run --release` sets.
    """
    from nxconfig import pincheck

    start = args.path if args.path is not None else Path(".")
    status = pincheck.check_workspace(start, release=args.release)
    if status is None:
        if args.json:
            print(json.dumps({"workspace": None, "findings": []}, indent=2))
        else:
            print(f"workspace:    none ({pincheck.PIN_FILE} not found above {start}: the running tree, "
                  "nothing to check)")
        return 0
    if args.json:
        print(json.dumps({"workspace": str(status.file.parent), "pin": str(status.file),
                          "version": status.version, "tree": str(status.tree) if status.tree else None,
                          "release": status.release, "development": status.development, "tag": status.tag,
                          "describe": status.describe, "severity": status.severity,
                          "findings": [{"severity": f.severity, "code": f.code, "text": f.text,
                                        "path": f.yaml_path, "line": f.line,
                                        "file": str(f.file) if f.file else None}
                                       for f in status.findings]}, indent=2))
        return 1 if status.errors else 0
    print(f"pin:          {status.file}")
    print(f"version:      {status.version or '?'}" + (f" (tag {status.tag})" if status.tag else ""))
    print(f"tree:         {status.tree or '?'}")
    print(f"describe:     {status.describe or '(kein git-Checkout)'}")
    print(f"strenge:      {'release' if status.release else 'taeglich'}")
    for finding in status.findings:
        print(f"{finding.severity} [{finding.code}]: {finding}",
              file=sys.stderr if finding.severity != "ok" else sys.stdout)
    return 1 if status.errors else 0


def _migrate(args, parser) -> int:
    """Raises the configuration files of a workspace to the format of this tree (TOOL-66, gap N11).

    Dry run by default: it prints per file which steps would run and what they change (diff), and writes only
    with --write - after a copy of every changed file into <workspace>/.nextbsw-backup/<timestamp>.
    """
    from nxconfig import migrate as nxmigrate

    if args.list:
        for line in nxmigrate.describe_registry():
            print(line)
        return 0
    if args.path is None:
        parser.error("migrate: a workspace, a project or a configuration file is required (or --list)")
    unknown = [s for s in args.step if s not in nxmigrate.step_ids()]
    if unknown:
        parser.error(f"migrate: unknown step(s) {', '.join(unknown)} (known: {', '.join(nxmigrate.step_ids()) or '-'})")
    if not Path(args.path).exists():
        print(f"error: {args.path} does not exist", file=sys.stderr)
        return 1
    plan = nxmigrate.plan(args.path, nxcfg.REPO, tuple(args.step))
    folder = None
    if args.write:
        try:
            folder, _ = nxmigrate.apply(plan)
        except OSError as e:
            print(f"error: {e} (nothing or only part was written)", file=sys.stderr)
            return 1
    for line in nxmigrate.report(plan, args.write, folder, diff=not args.no_diff):
        print(line)
    return 1 if plan.problems else 0


# NVM-14: MASTER_ECU_KEY of development boards in the key store of the workstation (keystore.py)
def _csec_key(args) -> int:
    from nxconfig import keystore, security

    if args.action == "scan":
        root = (args.target or nxconfig.REPO).resolve()
        keys = keystore.stored_keys()
        findings = keystore.scan(root, keys)
        for f in findings:
            print(f, file=sys.stderr)
        print(f"csec-key scan {root}: {len(keys)} key(s) of {keystore.key_dir()} looked for, "
              f"{len(findings)} finding(s)")
        return 1 if findings else 0
    if args.target is None:
        print(f"error: csec-key {args.action} needs a project", file=sys.stderr)
        return 2
    config, messages = nxconfig.check(_project_file(args.target))
    for m in messages:
        print(m, file=sys.stderr)
    if config is None:
        return 1
    name = security.master_ecu_key_name(config)
    try:
        if args.action == "new":
            path = keystore.create_master_ecu_key(name)
            print(f"created {path} - back it up outside this PC; never copy it into the repository")
        else:
            path = keystore.key_path(name)
            stored = keystore.load_master_ecu_key(name) if path.is_file() else None
            print(f"MASTER_ECU_KEY '{name}': {path} "
                  + (f"(present, fingerprint {stored.fingerprint})" if stored else "(missing: csec-key new)"))
    except keystore.KeyStoreError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _identity(args) -> int:
    """PLAT-79: configuration and generator identity of a project, the values the image carries - without building."""
    from nxconfig import identity as nxidentity

    config, messages = nxconfig.check(_project_file(args.project))
    for m in messages:
        if m.severity == "error":
            print(m, file=sys.stderr)
    if config is None or any(m.severity == "error" for m in messages):
        print("error: the configuration has errors, no identity (nxconfig check)", file=sys.stderr)
        return 1
    identity = nxidentity.of(config)
    if args.header:
        sys.stdout.write(identity.fields_header())
        return 0
    print(f"{config.project.project.name}: neXtBSW {identity.version}")
    for line in identity.lines():
        print(line)
    if args.files:
        for title, inputs in (("configuration", identity.configuration), ("generators", identity.generators)):
            print(f"{title} files:")
            for i in sorted(inputs, key=lambda i: i.label):
                print(f"  {i.sha256}  {i.label}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="nxconfig", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="load a project with board and derivative and report all findings")
    c.add_argument("project", type=Path)
    c.add_argument("--json", action="store_true", help="messages as JSON (severity, text, file, path, line)")
    m = sub.add_parser("cmake", help="check a project and write the build settings (NX_BOARD, NX_FAMILY, ...) "
                                     "as CMake include file")
    m.add_argument("project", type=Path)
    m.add_argument("out", type=Path)
    m.add_argument("--mcudb", type=Path, default=nxconfig.MCUDB, help="derivative database (tests)")
    n = sub.add_parser("new", help="create a project: empty for a board of the tree, or as a copy of a project "
                                   "(TOOL-59); checks the result")
    n.add_argument("name", nargs="?", help="project name (C identifier, also the folder name)")
    what = n.add_mutually_exclusive_group()
    what.add_argument("--board", help="empty project for this board (name or folder under boards/)")
    what.add_argument("--copy", metavar="PROJECT", help="copy of this project (name, folder or project.yaml)")
    n.add_argument("--dir", type=Path, help="target folder (default: projects/<name>; in a workspace "
                                            "<workspace>/projects/<name>)")
    n.add_argument("--workspace", type=Path, metavar="DIR",
                   help="customer workspace (folder of nextbsw.yaml or the file): boards of its search.boards and "
                        "projects of its projects/ are offered before those of the pinned tree, the new project's "
                        "board stays relative (TOOL-216); default: the workspace above --dir or the current folder")
    n.add_argument("--diag", action="store_true",
                   help="empty project: diag.yaml with the standard diagnostics of neXtBSW (sessions, security "
                        "level, identification identifiers, the DTCs of the basic software; TOOL-131) - an empty "
                        "one if the board has no diagnostic connection")
    n.add_argument("--calibration", action="store_true",
                   help="empty project: a calibration segment (project.yaml memory.calibration, one sector of the "
                        "code flash) and, with --diag, its DTCs (TOOL-131, TOOL-178)")
    n.add_argument("--all-signals", action="store_true",
                   help="empty project: use every bus, input, output, ADC and capture channel of the board "
                        "(default: first CAN bus and first UART; TOOL-106)")
    # TOOL-110 (F6): the safety section is never derived - the pins are a fact of the circuit
    n.add_argument("--safe-state-output", metavar="OUTPUT",
                   help="empty project, safety.safe_state_output: " + newproject_effect("safe_state_output"))
    n.add_argument("--locked-pin", metavar="OUTPUT",
                   help="empty project, safety.locked_pin: " + newproject_effect("locked_pin"))
    n.add_argument("--test-pin", metavar="PIN",
                   help="empty project, safety.test_pin: " + newproject_effect("test_pin") + ". Without the three "
                        "options the section stays commented out - allowed only if the family port does not read it; "
                        "otherwise nxconfig new stops before writing (TOOL-126)")
    n.add_argument("--safety-candidates", action="store_true",
                   help="with --board: list the outputs and free pins of the board for the safety section")
    n.add_argument("--description", default="", help="project.description")
    n.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    n.add_argument("--list", action="store_true", help="list boards and projects to start from")
    # TOOL-72: own board for an own ECU (a new project is almost never one of the evaluation boards of the tree)
    b = sub.add_parser("board", help="own board: create boards/<name>/board.yaml with its README and board port "
                                     "from a derivative and a package of the mcudb, or list what they offer")
    newboard.add_arguments(b)
    v = sub.add_parser("version", help="neXtBSW version (VERSION of the tree) and git describe (ARCH-55); with a "
                                       "path also the workspace pin nextbsw.yaml found from there (ARCH-56)")
    v.add_argument("path", nargs="?", type=Path, help="project.yaml or folder to search nextbsw.yaml from")
    p = sub.add_parser("pin", help="release strictness of the workspace pin (TOOL-65, gap N10): the pinned tree "
                                   "on the annotated tag v<version> reachable from master, clean, overlays of "
                                   "the same version")
    p.add_argument("path", nargs="?", type=Path, help="project.yaml or folder to search nextbsw.yaml from "
                                                      "(default: the current folder)")
    p.add_argument("--release", action="store_true",
                   help="release strictness: a development pin (no release tag) is an error instead of a warning")
    p.add_argument("--json", action="store_true", help="findings as JSON (severity, code, text, path, line)")
    g = sub.add_parser("migrate", help="raise the configuration files of a workspace to the format of this tree "
                                       "(TOOL-66); dry run with diff unless --write is given")
    g.add_argument("path", nargs="?", type=Path, help="workspace folder, nextbsw.yaml, project.yaml or a single "
                                                      "configuration file")
    g.add_argument("--write", action="store_true", help="apply the migration after writing a backup of every "
                                                        "file it changes")
    g.add_argument("--step", action="append", default=[], metavar="ID",
                   help="also run this prepared step (for example rte:2), which the tree does not read yet; "
                        "repeatable")
    g.add_argument("--no-diff", action="store_true", help="only the steps and their changes, without the diff")
    g.add_argument("--list", action="store_true", help="the formats this tree reads and every registered step")
    # NVM-14
    k = sub.add_parser("csec-key", help="MASTER_ECU_KEY of development boards in the key store of the "
                                        "workstation: create it, show where it is, or scan a repository for key "
                                        "material (NVM-14)")
    k.add_argument("action", choices=("new", "where", "scan"))
    k.add_argument("target", nargs="?", type=Path, help="new/where: project.yaml or project folder; scan: "
                                                        "repository (default: this tree)")
    # PLAT-79
    i = sub.add_parser("identity", help="configuration and generator identity of a project (SHA-256 over its "
                                        "configuration files and the generators): the value the image shows on the "
                                        "console (version, info) and answers for DID 0xF1F1, without building")
    i.add_argument("project", type=Path)
    i.add_argument("--files", action="store_true", help="also every input file with its SHA-256")
    i.add_argument("--header", action="store_true", help="print identity/nxIdentityFields.h as the build gets it")
    args = parser.parse_args(argv)

    if args.command == "identity":
        return _identity(args)
    if args.command == "csec-key":
        return _csec_key(args)

    if args.command == "new":
        return _new(args, parser)
    if args.command == "board":
        try:
            return newboard.run(args)
        except newboard.NewBoardError as e:
            for problem in e.problems:
                print(f"error: {problem}", file=sys.stderr)
            return 1
    if args.command == "version":
        return _version(args)
    if args.command == "pin":
        return _pin(args)
    if args.command == "migrate":
        return _migrate(args, parser)

    if args.command == "cmake":
        config, messages = nxconfig.check(_project_file(args.project), args.mcudb)
        if config is not None:
            settings, extra = nxcfg.build_settings(config)
            messages = messages + extra
        for msg in messages:
            print(msg, file=sys.stderr)
        if config is None:
            return 1
        nxcfg.write_if_changed(args.out, nxcfg.render_cmake(settings, config.project_file.name))
        print(f"{config.project.project.name}: board {settings['NX_BOARD']}, {settings['NX_DERIVATIVE']} "
              f"({settings['NX_FAMILY']}, {settings['NX_CORE']}), {len(messages)} warning(s)")
        return 0

    config, messages = nxconfig.check(args.project)
    if args.json:
        print(json.dumps([{"severity": m.severity, "text": m.text, "file": str(m.file) if m.file else None,
                           "path": m.yaml_path, "line": m.line} for m in messages], indent=2))
    else:
        for m in messages:
            print(m, file=sys.stderr)
        if config is not None:
            # IO-35: derived short-circuit values for the reader - no finding, not counted, not in --json
            for line in powerstage.info(config):
                print(f"info: {line}", file=sys.stderr)
            # IO-21: ADC codes of the module levels compared with an analog connection (e.g. KL30B overcurrent mA)
            for line in sensed.info(config):
                print(f"info: {line}", file=sys.stderr)
            # IO-28: rate, SCL divider and FlexIO resources of the used I2C buses
            for line in nxi2c.info(config):
                print(f"info: {line}", file=sys.stderr)
            # IO-51: one line per pin event (edge, vector, rank, rate limit)
            for line in pinevent.info(config):
                print(f"info: {line}", file=sys.stderr)
            # IO-33: battery monitor thresholds on the SBC grid, local wake-up inputs wired by the board
            for line in supply.info(config):
                print(f"info: {line}", file=sys.stderr)
            # SYS-86: DAC level, realised threshold with its band, fault input and reaction time per threshold
            for line in hwthreshold.info(config):
                print(f"info: {line}", file=sys.stderr)
            # IO-62: sample instants of every channel, end of the group, interrupt of the completing ADC
            for line in samplegroup.info(config):
                print(f"info: {line}", file=sys.stderr)
            print(f"{config.project.project.name}: ok (board {config.board.board.name}, "
                  f"{config.derivative.derivative.name}, {len(messages)} warning(s))")
    return 0 if config is not None else 1


if __name__ == "__main__":
    sys.exit(main())
