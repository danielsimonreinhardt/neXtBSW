"""SYS-139/SYS-140: target build of a project with an active short - a copy of the demo project, never the demo itself.

No project uses the active short yet, so this copy is the only proof that the code of C2 (family port: ShortHold.h,
FtmPwmInhibit, the TMC6200 lock and adapter, the group lock of ComplementaryPwm, HwThresholdWatch) and of C4 (safe
supervisor: blockShortOnBridgeFault) builds for the target, warning-free, together with the value sets of SYS-116.
The copy gets **synthetic** values that no real project has decided:
  - project pwm.MOT1.safe_states [freewheel, active_short_low] with the start test (register level) and the board fact
    bridge_reset_state: freewheel (a warning of nxconfig, expected);
  - a degraded mapping (safety.output_groups DRIVE = MOT1, DEVICE_FAULT of MOT1_GD -> DRIVE), so the group lock of
    SYS-123 is compiled with the selected output control;
  - the hardware threshold of tools/bswgen/test/hw_threshold_build.py (SENSE_TSCURRENT also at PTA0 = CMP0_IN0), so
    HwThresholdWatch is compiled with the withdrawal of the short.
It says nothing about the board. The board and the project copy lie in the system temp folder; the build is the
Studio's (base preset plus -DNX_PROJECT, nxconfig/projectbuild.py), like hw_threshold_build.py.

Checked: nxconfig accepts the short (no error; before SYS-139/SYS-140 it refused it), the plan selects the short and
derives the supervisor's bridge device; configure and build pass; the build log has no warning (-Werror); the image
has the symbols of C2 and C4 (nm).

    python platforms/s32k1xx/port/main/src/integration/test/short_hold_build.py <build directory>

SYS-172/SYS-173 board image (test build, never a product): the same copy built with the test options
NX_SHORT_LOCK_MEASURE_COMMAND (console "shortlock", probe pin) and NX_SHUTDOWN_HANG_COMMAND (console "shutdownhang"),
flashable on the eval board. Without --threshold the synthetic hardware threshold is left out: PTA0 floats on the eval
board (1.3 .. 1.9 V, board.yaml MOTOR_NTC) and would trip the comparator at random, which removes the short at once.
The image additionally has to contain the symbols of both commands.

    python .../short_hold_build.py <build directory> --board-image [--threshold] [--probe-pin PTC8]
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[6]
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))

# PLAT-114: the open build helper, not the Studio's classes (the Studio is not distributed, F12)
from nxconfig.projectbuild import ProjectBuild, run, tool  # noqa: E402

DEMO = REPO / "projects" / "evalboard_s32k144_demo"
EVAL_BOARD = REPO / "boards" / "evalboard_s32k144" / "board.yaml"
COMPARATORS = {"supply": {"rail": "SUPPLY_5V"},
               "inputs": [{"name": "TSCURRENT_CMP", "instance": "CMP0", "input": 0, "pin": "PTA0",
                           "signal": "SENSE_TSCURRENT",
                           "note": "SYNTHETIC build test SYS-139 - the eval board has no such wiring"}]}
THRESHOLD = {"MOT1_OVERCURRENT": {"input": "TSCURRENT_CMP", "trip": "below", "threshold": -20000,
                                  "reference": "supply", "pwm": "MOT1", "filter_ns": 100}}
# symbols of C2 and C4 the image must contain (demangled names from nm -C)
SYMBOLS = ("nxSafeStateBlockShort", "nxSafeOutputsSelect", "safety::SafeSupervisor::blockShortOnBridgeFault",
           "bios::ftmPwm::lockedOutputControl",  # bridgeGroupsOf is inlined into blockShortOnBridgeFault
           "platform::MOT1_GD_GD_SHORT_LOCK", "platform::MOT1_GD_GD_KEEP_ENABLE")
# SYS-172/SYS-173 board image: the symbols of the commands of its test options
BOARD_IMAGE_SYMBOLS = ("nxSafeStateLockStatRead", "integration::shutdownHangComponent")
DEFAULT_PROBE_PIN = "PTC8"  # LED2 of the eval board (board.yaml dio.outputs), a GPIO output of the demo project


def make_project(folder: Path, name: str, threshold: bool = True) -> Path:
    board_dir = folder.parent / "boards" / "evalboard_s32k144"
    board_dir.mkdir(parents=True)
    board = yaml.safe_load(EVAL_BOARD.read_text(encoding="utf-8"))
    if threshold:
        board["comparators"] = COMPARATORS
    for group in board["pwm"]:
        if group["name"] == "MOT1":
            group["bridge_reset_state"] = "freewheel"
            group["high_side_hold"] = False
    (board_dir / "board.yaml").write_text(yaml.safe_dump(board, sort_keys=False, allow_unicode=True),
                                          encoding="utf-8")
    folder.mkdir(parents=True)
    for file in DEMO.iterdir():
        if file.is_file():
            shutil.copy(file, folder / file.name)
    doc = yaml.safe_load((folder / "project.yaml").read_text(encoding="utf-8"))
    doc["project"]["name"] = name
    doc["project"]["board"] = board_dir.as_posix()
    if threshold:
        doc["hw_thresholds"] = THRESHOLD
    doc["pwm"]["MOT1"]["safe_states"] = ["freewheel", "active_short_low"]
    doc["pwm"]["MOT1"]["safe_state_test"] = True
    doc["pwm"]["MOT1"]["safe_state_test_us"] = 200
    doc["safety"]["output_groups"] = [{"name": "DRIVE", "pwm": "MOT1"}]
    doc["safety"]["degraded"] = [{"event": "DEVICE_FAULT", "device": "MOT1_GD", "groups": ["DRIVE"]}]
    (folder / "project.yaml").write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    rte = yaml.safe_load((folder / "rte.yaml").read_text(encoding="utf-8"))
    for component in rte["components"]:
        component["path"] = (DEMO / component["path"]).resolve().as_posix()
    (folder / "rte.yaml").write_text(yaml.safe_dump(rte, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return folder / "project.yaml"


def parse(argv: list[str]) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("build_dir", type=Path)
    parser.add_argument("--board-image", action="store_true")
    parser.add_argument("--threshold", action="store_true")
    parser.add_argument("--probe-pin", default=DEFAULT_PROBE_PIN)
    try:
        return parser.parse_args(argv)
    except SystemExit:
        return None


def main() -> int:
    args = parse(sys.argv[1:])
    if args is None:
        print(__doc__)
        return 2
    if not (DEMO / "project.yaml").is_file():
        print("demo project missing (open export): nothing to build")
        print("target build with an active short: 0 checks, 0 failures")
        return 0
    build_dir = args.build_dir.resolve()
    name = build_dir.name
    folder = Path(tempfile.gettempdir()) / "s32k" / f"{build_dir.parent.name}_{name}_project" / name
    for old in (build_dir, folder.parent):
        shutil.rmtree(old, ignore_errors=True)
    failures: list[str] = []
    symbols = SYMBOLS + (BOARD_IMAGE_SYMBOLS if args.board_image else ())

    project_file = make_project(folder, name, threshold=args.threshold or not args.board_image)
    project = ProjectBuild(project_file, build_dir, repo=REPO)
    for m in project.messages:
        print(" ", m)
    if project.errors:
        failures.append(f"nxconfig reports {len(project.errors)} error(s) in the copy")
    else:
        plan = project.config.safe_states
        if not plan.select or plan.groups[0].allowed != ("freewheel", "active_short_low"):
            failures.append(f"safe states of the copy: {[(g.name, g.allowed) for g in plan.groups]}")
        derived = project.config.integration.modules["safeSupervisor"].derived
        if derived.get("bridge_fault_devices", (0,))[0] != 1:
            failures.append(f"bridge devices of the supervisor: {derived.get('bridge_fault_devices')}")

    log: list[str] = []
    built = False
    if not failures:
        steps = project.steps()
        if args.board_image:
            steps[0].argv += ["-DNX_SHORT_LOCK_MEASURE_COMMAND=ON", f"-DNX_SHORT_LOCK_PROBE_PIN={args.probe_pin}",
                              "-DNX_SHUTDOWN_HANG_COMMAND=ON"]
        built = all(run(c, log) for c in steps)
        if not built:
            failures.append("configure or build failed")
    if built:
        text = "\n".join(log[1:])
        warnings = [line for line in text.splitlines() if re.search(r"\bwarning:", line, re.I)]
        if warnings:
            failures.append(f"{len(warnings)} warning(s) in the build log:\n" + "\n".join(warnings[:40]))
        elfs = list(project.build_dir.glob("**/app.ecu.elf"))
        if not elfs:
            failures.append(f"app.ecu.elf missing in {project.build_dir}")
        else:
            names = subprocess.run([tool("arm-none-eabi-nm"), "-C", str(elfs[0])], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace").stdout
            for symbol in symbols:
                present = re.search(rf"\s{re.escape(symbol)}(\(|$)", names, re.M) is not None
                print(f"  {symbol}: {'in the image' if present else 'MISSING'}")
                if not present:
                    failures.append(f"{symbol} is not in the image")
            size = subprocess.run([tool("arm-none-eabi-size"), str(elfs[0])], capture_output=True, text=True)
            print(size.stdout.strip())
            if args.board_image:
                for image in (elfs[0], elfs[0].with_suffix(".hex")):
                    print(f"  board image: {image}")

    for f in failures:
        print("FAIL", f)
    print(f"target build with an active short: {3 + len(symbols)} checks, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
