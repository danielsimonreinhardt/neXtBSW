"""IO-68: target build with INTN1 of the SBC wired and a pin event on it - a copy of board and demo project, never the
demo itself.

The eval board (boards/evalboard_s32k144) and the demo project are copied into a temporary folder outside the tree.
The board copy gets the module connection `intn1: SBC_INTERRUPT1` at its SBC (board.yaml devices[].connect, the
optional connection of libs/bsw/sbcUja113x/module.yaml), the project copy a pin event on that input (edge activate,
no debounce) and an interrupt rank for it. The tree itself stays unchanged: the eval board wires the line, the demo
has no pin event on it and samples its level only (the target build of the demo is that variant).

Checked: nxconfig finds no error in the copy; the generated integration/sbcUja113xConfiguration.h says the line is
wired and names the input; configure and build pass and the build log has no warning (-Werror); the image has the
pin-detect handler of the INTN1 port (PORTE on the eval board, the others stay weak), the pin event adapter and the
standby functions of the driver; tools/elfcheck timing finds no error.

    python libs/bsw/sbcUja113x/test/intn_build.py <build directory>

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
REPO = HERE.parents[3]
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))

# PLAT-114: the open build helper, not the Studio's classes (the Studio is not distributed, F12)
from nxconfig.projectbuild import ProjectBuild, run, tool  # noqa: E402

DEMO = REPO / "projects" / "evalboard_s32k144_demo"
BOARD = REPO / "boards" / "evalboard_s32k144"
SBC = "SBC"                                 # board device of the UJA1131
INPUT = "SBC_INTERRUPT1"                    # board input wired to INTN1 (PTE12 on the eval board)
EVENT = {"edge": "activate", "debounce_us": 0, "max_events": 20, "window_ms": 100, "rearm_ms": 200}
RANK = 4
SYMBOLS = [
    ("PORTE_IRQHandler", "T"), ("PORTA_IRQHandler", "W"), ("PORTB_IRQHandler", "W"), ("PORTC_IRQHandler", "W"),
    ("PORTD_IRQHandler", "W"),
    ("bios::PinEventAdapter::onEdge(unsigned short, unsigned long)", "T"),
    ("sbc::Uja113x::enterStandby(unsigned long, sbc::SleepWakeup const&)", "T"),
    ("sbc::Uja113x::leaveStandby(unsigned long)", "T"),
    ("systems::SbcSystem::sampleInterruptLine()", "T"),
    ("systems::SbcSystem::isMcuWakeLineArmed() const", "T"),
]
HEADER_LINES = [f"#define NX_DEVICE_{SBC}_IO_INTN1_WIRED 1", f"#define NX_DEVICE_{SBC}_IO_INTN1 {INPUT}"]


def make_board(folder: Path) -> Path:
    """The copy of the eval board with INTN1 wired to the SBC."""
    folder.mkdir(parents=True)
    shutil.copy(BOARD / "board.yaml", folder / "board.yaml")
    doc = yaml.safe_load((folder / "board.yaml").read_text(encoding="utf-8"))
    device = next(d for d in doc["devices"] if d["name"] == SBC)
    device.setdefault("connect", {})["intn1"] = INPUT
    (folder / "board.yaml").write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return folder


def make_project(folder: Path, name: str, board: Path) -> Path:
    """The copy of the demo project on the board copy, with a pin event on the INTN1 input."""
    folder.mkdir(parents=True)
    for file in DEMO.iterdir():
        if file.is_file():
            shutil.copy(file, folder / file.name)
    doc = yaml.safe_load((folder / "project.yaml").read_text(encoding="utf-8"))
    doc["project"]["name"] = name
    doc["project"]["board"] = board.as_posix()
    doc["pin_events"] = {INPUT: EVENT}
    doc["interrupts"]["ranks"].append({"source": "dio", "name": INPUT, "rank": RANK})
    (folder / "project.yaml").write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    rte = yaml.safe_load((folder / "rte.yaml").read_text(encoding="utf-8"))
    for component in rte["components"]:
        component["path"] = (DEMO / component["path"]).resolve().as_posix()
    (folder / "rte.yaml").write_text(yaml.safe_dump(rte, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return folder / "project.yaml"


def check_image(elf: Path, failures: list[str]):
    nm = subprocess.run([tool("arm-none-eabi-nm"), "-C", str(elf)], capture_output=True, text=True)
    if nm.returncode != 0:
        failures.append(f"nm failed: {nm.stderr[-500:]}")
        return
    types: dict[str, str] = {}
    for line in nm.stdout.splitlines():
        match = re.match(r"^(?:[0-9a-fA-F]+)?\s+(\w)\s+(.+)$", line)
        if match:
            types.setdefault(match.group(2), match.group(1))
    for symbol, allowed in SYMBOLS:
        kind = types.get(symbol)
        print(f"  {kind or '-'} {symbol}")
        if kind is None or kind not in allowed:
            failures.append(f"symbol {symbol}: {kind or 'missing'}, expected one of {allowed}")
    size = subprocess.run([tool("arm-none-eabi-size"), str(elf)], capture_output=True, text=True)
    print(size.stdout.strip())


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    if not (DEMO / "project.yaml").is_file() or not (BOARD / "board.yaml").is_file():
        print("demo project or eval board missing (open export): nothing to build")
        print("target build with INTN1: 0 checks, 0 failures")
        return 0
    build_dir = Path(sys.argv[1]).resolve()
    name = build_dir.name
    root = Path(tempfile.gettempdir()) / "s32k" / f"{build_dir.parent.name}_{name}_project"
    for old in (build_dir, root):
        shutil.rmtree(old, ignore_errors=True)
    failures: list[str] = []

    board = make_board(root / "board")
    project_file = make_project(root / name, name, board)
    project = ProjectBuild(project_file, build_dir, repo=REPO)
    for m in project.messages:
        print(" ", m)
    if project.errors:
        failures.append(f"nxconfig reports {len(project.errors)} error(s) in the copy")
    elif [ev.input for ev in project.config.pin_events] != [INPUT]:
        failures.append(f"pin events of the copy: {[ev.input for ev in project.config.pin_events]}")

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
        headers = list(project.build_dir.glob("**/integration/sbcUja113xConfiguration.h"))
        header = headers[0].read_text(encoding="utf-8") if headers else ""
        for line in HEADER_LINES:
            if line + "\n" not in header:
                failures.append(f"generated sbcUja113xConfiguration.h without '{line}'")
        elfs = list(project.build_dir.glob("**/app.ecu.elf"))
        if not elfs:
            failures.append(f"app.ecu.elf missing in {project.build_dir}")
        else:
            check_image(elfs[0], failures)
        timing = subprocess.run([sys.executable, str(TOOLS / "elfcheck"), "timing", "--project", str(project_file),
                                 "--build", str(project.build_dir)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        print(timing.stdout.strip()[-3000:])
        if timing.returncode != 0 or not re.search(r"elfcheck timing: 0 errors", timing.stdout):
            failures.append(f"elfcheck timing: {timing.stdout.strip()[-800:]} {timing.stderr.strip()[-800:]}")

    for f in failures:
        print("FAIL", f)
    print(f"target build with INTN1: 5 checks, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
