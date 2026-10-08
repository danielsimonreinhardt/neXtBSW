"""Target build of a project with a hardware threshold (SYS-86): a copy of the demo project, never the demo itself.

The eval board has no meaningful signal at a comparator input (CMP0_IN0 PTA0 = MOTOR_NTC, not fitted; IN1 PTA1 = Hall
signal; IN2 PTC4 = debug clock; IN3 PTE8 = PILOT_DUTY; IN4/IN5 PTC3/PTC2 = CAN0; IN6/IN7 PTD7/PTD6 = UART - board.yaml,
doc/safety/hardware-mechanisms.md section 4). The copy therefore gets a **synthetic** board fact that no board has:
the DC link current amplifier of SENSE_TSCURRENT also at PTA0 = CMP0_IN0, and the project a threshold on it - the
consumption above 20 A (the signal counts consumption negative: trip below -20000 mA) switches MOT1 (FTM0) off, on
the supply reference, with a 100 ns filter. It proves that the generat and the family code build for the target;
it says nothing about the board. The board and the project copy lie in the system temp folder; the build is the
Studio's (base preset plus -DNX_PROJECT, nxconfig/projectbuild.py).

Checked: nxconfig finds no error in the copy and plans the threshold, configure and build pass, the build log has no
warning (-Werror), the image contains the generated comparator word CMP_C1 and the TRGMUX and CMP0 base
addresses as literals (the inline code of hwThreshold/HwThreshold.h reached the PWM driver's init()).

    python tools/bswgen/test/hw_threshold_build.py <build directory>

Needs cmake, ninja and arm-none-eabi-gcc (PATH or NX_ARM_GCC_BIN), like the target group of tools/test.
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1]
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

# PLAT-114: the open build helper, not the Studio's classes (the Studio is not distributed, F12)
from nxconfig.projectbuild import ProjectBuild, run, tool  # noqa: E402

DEMO = REPO / "projects" / "evalboard_s32k144_demo"
EVAL_BOARD = REPO / "boards" / "evalboard_s32k144" / "board.yaml"
COMPARATORS = {"supply": {"rail": "SUPPLY_5V"},
               "inputs": [{"name": "TSCURRENT_CMP", "instance": "CMP0", "input": 0, "pin": "PTA0",
                           "signal": "SENSE_TSCURRENT",
                           "note": "SYNTHETIC build test SYS-86 - the eval board has no such wiring"}]}
THRESHOLD = {"MOT1_OVERCURRENT": {"input": "TSCURRENT_CMP", "trip": "below", "threshold": -20000,
                                  "reference": "supply", "pwm": "MOT1", "filter_ns": 100}}
TRGMUX_BASE = 0x40063000                         # TRGMUX base address ([RM13] 19.4.1.1); no other user in the tree
CMP0_BASE = 0x40073000                           # CMP0 base address (device header IP_CMP0_BASE); no other user


def make_project(folder: Path, name: str) -> Path:
    """The copy of the demo project with the synthetic comparator input and the threshold."""
    board_dir = folder.parent / "boards" / "evalboard_s32k144"
    board_dir.mkdir(parents=True)
    board = yaml.safe_load(EVAL_BOARD.read_text(encoding="utf-8"))
    board["comparators"] = COMPARATORS
    (board_dir / "board.yaml").write_text(yaml.safe_dump(board, sort_keys=False, allow_unicode=True),
                                          encoding="utf-8")
    folder.mkdir(parents=True)
    for file in DEMO.iterdir():
        if file.is_file():
            shutil.copy(file, folder / file.name)
    doc = yaml.safe_load((folder / "project.yaml").read_text(encoding="utf-8"))
    doc["project"]["name"] = name
    doc["project"]["board"] = board_dir.as_posix()
    doc["hw_thresholds"] = THRESHOLD
    (folder / "project.yaml").write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    rte = yaml.safe_load((folder / "rte.yaml").read_text(encoding="utf-8"))
    for component in rte["components"]:
        component["path"] = (DEMO / component["path"]).resolve().as_posix()
    (folder / "rte.yaml").write_text(yaml.safe_dump(rte, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return folder / "project.yaml"


def words_in_image(elf: Path) -> set[int]:
    dump = subprocess.run([tool("arm-none-eabi-objdump"), "-d", str(elf)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return {int(m.group(1), 16) for m in re.finditer(r"\.word\s+0x([0-9a-fA-F]+)", dump.stdout)}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    if not (DEMO / "project.yaml").is_file():
        print("demo project missing (open export): nothing to build")
        print("target build with a hardware threshold: 0 checks, 0 failures")
        return 0
    build_dir = Path(sys.argv[1]).resolve()
    name = build_dir.name
    folder = Path(tempfile.gettempdir()) / "s32k" / f"{build_dir.parent.name}_{name}_project" / name
    for old in (build_dir, folder.parent):
        shutil.rmtree(old, ignore_errors=True)
    failures: list[str] = []

    project_file = make_project(folder, name)
    project = ProjectBuild(project_file, build_dir, repo=REPO)
    for m in project.messages:
        print(" ", m)
    plans = [] if project.errors else project.config.hw_thresholds
    if project.errors:
        failures.append(f"nxconfig reports {len(project.errors)} error(s) in the copy")
    elif [t.name for t in plans] != list(THRESHOLD):
        failures.append(f"hardware thresholds of the copy: {[t.name for t in plans]}")

    log: list[str] = []
    built = False
    if not failures:
        built = all(run(c, log) for c in project.steps())
        if not built:
            failures.append("configure or build failed")
    if built:
        text = "\n".join(log[1:])               # the build log only; configure prints no compiler output
        warnings = [line for line in text.splitlines() if re.search(r"\bwarning:", line, re.I)]
        if warnings:
            failures.append(f"{len(warnings)} warning(s) in the build log:\n" + "\n".join(warnings[:40]))
        generat = project.build_dir / "generated" / "board" / "bsp" / "hwThreshold" / "hwThresholdConfiguration.h"
        found = list(project.build_dir.glob("**/bsp/hwThreshold/hwThresholdConfiguration.h"))
        generat = found[0] if found else generat
        match = re.search(r"constexpr uint32_t CMP_C1 = (0x[0-9A-F]+)U;",
                          generat.read_text(encoding="utf-8") if generat.is_file() else "")
        elfs = list(project.build_dir.glob("**/app.ecu.elf"))
        if match is None:
            failures.append(f"generated {generat} without CMP_C1")
        elif not elfs:
            failures.append(f"app.ecu.elf missing in {project.build_dir}")
        else:
            words = words_in_image(elfs[0])
            for what, value in (("CMP_C1", int(match.group(1), 16)), ("TRGMUX base address", TRGMUX_BASE),
                                ("CMP0 base address", CMP0_BASE)):
                print(f"  {what} 0x{value:08X}: {'in the image' if value in words else 'MISSING'}")
                if value not in words:
                    failures.append(f"{what} 0x{value:08X} is no literal of the image")
            size = subprocess.run([tool("arm-none-eabi-size"), str(elfs[0])], capture_output=True, text=True)
            print(size.stdout.strip())

    for f in failures:
        print("FAIL", f)
    print(f"target build with a hardware threshold: 5 checks, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
