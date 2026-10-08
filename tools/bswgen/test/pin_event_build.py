"""Target build of a project with pin events (IO-54): a copy of the demo project, never the demo itself.

The demo project (eval board S32K144) is copied into a temporary folder outside the tree - project.yaml, rte.yaml,
diag.yaml and the DBC - and given
  - pin events on its buttons: BUTTON1 activate with 2 ms debounce (detector: both edges), BUTTON2 deactivate
    without debounce (detector: rising, the board input is active low), rank 2;
  - the Hall sensor HALL1 of the board with rank 2 as well: its signal PTA1 shares the vector PORTA with the buttons,
    PTB8/PTB9 need PORTB - two consumers of the dispatcher on one vector;
  - a component port reading pinevent.BUTTON1.count and one reading pinevent.BUTTON2.qlf.
It is configured and built the way the Studio builds (base preset plus -DNX_PROJECT, nxconfig/projectbuild.py).

Checked: nxconfig finds no error in the copy, configure and build pass, the build log has no warning (-Werror), the
image has the pin-detect handlers of PORTA and PORTB (the other ports stay weak), the adapter, the task side of the
module, the start and cycle of the port and the console group; the pin events, the adapter and the dispatcher are
constant initialized (in .data, no static constructor); the generated RTE reads and cycles the events;
tools/elfcheck timing finds no error (interrupt ranks and NVIC levels of the image against the project).

    python tools/bswgen/test/pin_event_build.py <build directory>

The last folder of <build directory> is also the project name (the Studio builds in <build root>/<project>); the
project itself lies in the system temp folder (s32k/<parent>_<name>_project/<name>).

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
EVENTS = {
    "BUTTON1": {"edge": "activate", "debounce_us": 2000, "max_events": 20, "window_ms": 100, "rearm_ms": 500},
    "BUTTON2": {"edge": "deactivate", "debounce_us": 0, "max_events": 50, "window_ms": 100, "rearm_ms": 200},
}
HALL1 = {"arrangement": "deg120", "pole_pairs": 4, "min_speed_rpm": 60, "max_speed_rpm": 6000}
CONNECTIONS = {"DemoSwc.MotorNtc_mV.MotorNtc_mV": "pinevent.BUTTON1.count",
               "DemoSwc.PcCommandQlf.PcCommandQlf": "pinevent.BUTTON2.qlf"}
# (demangled symbol, nm types that are right): handlers of the vectors in use, the rest weak
SYMBOLS = [
    ("PORTA_IRQHandler", "T"), ("PORTB_IRQHandler", "T"), ("PORTC_IRQHandler", "W"), ("PORTD_IRQHandler", "W"),
    ("PORTE_IRQHandler", "W"),
    ("bios::PinEventAdapter::onEdge(unsigned short, unsigned long)", "T"),
    ("bios::PinIrqDispatcher::setEdge(bsp::pinedge::IPinEdgeConsumer const&, unsigned short, "
     "bios::pinirq::Edge)", "T"),
    ("pinevent::PinEvents::start()", "T"), ("pinevent::PinEvents::cycle()", "T"),
    ("bsp::startPinInterrupts()", "T"), ("bsp::cyclePinEvents()", "T"),
    ("pinevent::PinEventCommand::executeCommand(util::command::CommandContext&, unsigned char)", "T"),
    ("bios::hallEdgeConsumer", "DdBb"),
    # constant initialized: an object with a dynamic initializer would be in .bss (B/b)
    ("bsp::pinirqconfig::events", "Dd"), ("bsp::pinirqconfig::pinEventAdapter", "Dd"),
    ("bsp::pinirqconfig::dispatcher", "Dd"),
]


def make_project(folder: Path, name: str) -> Path:
    """The copy of the demo project with pin events, the Hall sensor and two connections."""
    folder.mkdir(parents=True)
    for file in DEMO.iterdir():
        if file.is_file():
            shutil.copy(file, folder / file.name)
    doc = yaml.safe_load((folder / "project.yaml").read_text(encoding="utf-8"))
    doc["project"]["name"] = name
    doc["project"]["board"] = (REPO / "boards" / "evalboard_s32k144").as_posix()
    doc["pin_events"] = EVENTS
    doc["modules"]["HALL1"] = HALL1
    doc["interrupts"]["ranks"] += [{"source": "dio", "name": n, "rank": 2}
                                   for n in (*EVENTS, "HALL_UX", "HALL_V", "HALL_WY")]
    (folder / "project.yaml").write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    rte = yaml.safe_load((folder / "rte.yaml").read_text(encoding="utf-8"))
    for component in rte["components"]:
        component["path"] = (DEMO / component["path"]).resolve().as_posix()
    rte["connections"].update(CONNECTIONS)
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
    if not (DEMO / "project.yaml").is_file():
        print("demo project missing (open export): nothing to build")
        print("target build with pin events: 0 checks, 0 failures")
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
    if project.errors:
        failures.append(f"nxconfig reports {len(project.errors)} error(s) in the copy")
    elif [ev.input for ev in project.config.pin_events] != list(EVENTS):
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
        elfs = list(project.build_dir.glob("**/app.ecu.elf"))
        if not elfs:
            failures.append(f"app.ecu.elf missing in {project.build_dir}")
        else:
            check_image(elfs[0], failures)
        rte = project.build_dir / "generated" / "rte" / "RteConfig.cpp"
        config = rte.read_text(encoding="utf-8") if rte.is_file() else ""
        for expected in ("::bsp::startPinInterrupts();", "::bsp::cyclePinEvents();",
                         "::bsp::pinEvents().read(::pinevent::project::Pe_BUTTON1, pinevent_BUTTON1)",
                         "static_cast<uint8>(pinevent_BUTTON2.quality);"):
            if expected not in config:
                failures.append(f"generated RTE ({rte}) without {expected}")
        timing = subprocess.run([sys.executable, str(TOOLS / "elfcheck"), "timing", "--project", str(project_file),
                                 "--build", str(project.build_dir)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        print(timing.stdout.strip()[-3000:])
        if timing.returncode != 0 or not re.search(r"elfcheck timing: 0 errors", timing.stdout):
            failures.append(f"elfcheck timing: {timing.stdout.strip()[-800:]} {timing.stderr.strip()[-800:]}")

    for f in failures:
        print("FAIL", f)
    print(f"target build with pin events: 6 checks, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
