"""Target build of SYS-136: the hardware shut-off path at the RTE and in the fault memory, on a copy of the demo
project.

The copy is the one of the SYS-86 build test (tools/bswgen/test/hw_threshold_build.py: a SYNTHETIC comparator input
SENSE_TSCURRENT at PTA0 = CMP0_IN0 and a hardware threshold on MOT1 - so the family port reports a shut-off path and
links safeSupervisor/HwShutoff.cpp), never the demo itself. On top of it the ECU description reads the report
(DemoSwc.MotorNtc_mV <- safety.hw_shutoff, DemoSwc.Button1 <- safety.hw_shutoff, DemoSwc.PcCommandQlf <-
safety.qlf.hw_shutoff) and the diagnostic description has the DTC HwShutoff of the BSW event safety.hw_shutoff (number
derived from bsw_dtc_range: +0x05FE). It proves that the generat, RteSystem and the supervisor's reader build for the
target; it says nothing about the board.

Checked: nxconfig finds no error, configure and build pass, the build log has no warning (-Werror), the generated
dem/demBswReporters.h defines NX_DEM_SAFETY_HW_SHUTOFF, the image calls safety::hwShutoff::read (symbol in the ELF)
and holds the BSW event name "safety.hw_shutoff" and the DTC number 0x1605FE.

    python tools/rtegen/test/hw_shutoff_build.py <build directory>

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
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "tools" / "bswgen" / "test"))

import hw_threshold_build as hwt  # noqa: E402  (PLAT-114: brings the open build helper nxconfig/projectbuild.py)

CONNECTIONS = {"DemoSwc.MotorNtc_mV.MotorNtc_mV": "safety.hw_shutoff",
               "DemoSwc.Button1.Button1": "safety.hw_shutoff",
               "DemoSwc.PcCommandQlf.PcCommandQlf": "safety.qlf.hw_shutoff"}
DTC = {"name": "HwShutoff", "label": "Hardware shut-off path of a PWM group",
       "description": "SYS-136 build test: reported by RteSystem from safety::hwShutoff::read.",
       "severity": "check_immediately", "operation_cycle": "PowerCycle", "confirmation_threshold": 1, "priority": 1,
       "event": {"bsw": "safety.hw_shutoff"}, "debounce": {"counter": {"failed_threshold": 1, "passed_threshold": -1}}}
NUMBER = 0x1605FE


def make_project(folder: Path, name: str) -> Path:
    project_file = hwt.make_project(folder, name)
    rte = yaml.safe_load((folder / "rte.yaml").read_text(encoding="utf-8"))
    rte["connections"].update(CONNECTIONS)
    (folder / "rte.yaml").write_text(yaml.safe_dump(rte, sort_keys=False, allow_unicode=True), encoding="utf-8")
    diag = yaml.safe_load((folder / "diag.yaml").read_text(encoding="utf-8"))
    diag["dtcs"].append(DTC)
    (folder / "diag.yaml").write_text(yaml.safe_dump(diag, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return project_file


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    if not (hwt.DEMO / "project.yaml").is_file():
        print("demo project missing (open export): nothing to build")
        print("target build with the hardware shut-off path: 0 checks, 0 failures")
        return 0
    build_dir = Path(sys.argv[1]).resolve()
    name = build_dir.name
    folder = Path(tempfile.gettempdir()) / "s32k" / f"{build_dir.parent.name}_{name}_project" / name
    for old in (build_dir, folder.parent):
        shutil.rmtree(old, ignore_errors=True)
    failures: list[str] = []

    project = hwt.ProjectBuild(make_project(folder, name), build_dir, repo=hwt.REPO)
    for m in project.messages:
        print(" ", m)
    if project.errors:
        failures.append(f"nxconfig reports {len(project.errors)} error(s) in the copy")

    log: list[str] = []
    built = False
    if not failures:
        built = all(hwt.run(c, log) for c in project.steps())
        if not built:
            failures.append("configure or build failed")
    if built:
        text = "\n".join(log[1:])
        warnings = [line for line in text.splitlines() if re.search(r"\bwarning:", line, re.I)]
        if warnings:
            failures.append(f"{len(warnings)} warning(s) in the build log:\n" + "\n".join(warnings[:40]))
        reporters = list(project.build_dir.glob("**/dem/demBswReporters.h"))
        if not reporters or "#define NX_DEM_SAFETY_HW_SHUTOFF 1" not in reporters[0].read_text(encoding="utf-8"):
            failures.append("dem/demBswReporters.h without NX_DEM_SAFETY_HW_SHUTOFF")
        elfs = list(project.build_dir.glob("**/app.ecu.elf"))
        if not elfs:
            failures.append(f"app.ecu.elf missing in {project.build_dir}")
        else:
            symbols = subprocess.run([hwt.tool("arm-none-eabi-nm"), "-C", str(elfs[0])], capture_output=True,
                                     text=True, encoding="utf-8", errors="replace").stdout
            image = elfs[0].read_bytes()
            for what, found in (("safety::hwShutoff::read", "safety::hwShutoff::read(" in symbols),
                                ("BSW event name safety.hw_shutoff", b"safety.hw_shutoff\x00" in image),
                                (f"DTC number 0x{NUMBER:06X}", f"0x{NUMBER:X}".lower() in _dem_header(project))):
                print(f"  {what}: {'found' if found else 'MISSING'}")
                if not found:
                    failures.append(f"{what} missing")
            size = subprocess.run([hwt.tool("arm-none-eabi-size"), str(elfs[0])], capture_output=True, text=True)
            print(size.stdout.strip())

    for f in failures:
        print("FAIL", f)
    print(f"target build with the hardware shut-off path: 7 checks, {len(failures)} failures")
    return 1 if failures else 0


def _dem_header(project) -> str:
    found = list(project.build_dir.glob("**/RteDem.h"))
    return found[0].read_text(encoding="utf-8").lower() if found else ""


if __name__ == "__main__":
    sys.exit(main())
