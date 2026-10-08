# SPDX-License-Identifier: Apache-2.0
"""Target build of the RTE-20 test profile: a hand-written C component in a real firmware image.

The profile has no project of its own: this script derives one from the open build test project
projects/buildtest_s32k148_min (S32K148, no SBC, two CAN buses) with the ECU description tools/rtegen/test/c_swc/
rte.yaml instead of the project's, configures it with the preset buildtest-s32k148-min (-DNX_PROJECT) and builds it.

Checked: configure and build pass, the build log has no warning (like every target build of tools/test), app.ecu.elf
exists and holds the runnables of both components (C: LevelMonSwc_Init/_Run10ms/_Run100ms, Simulink: DemoSwc_Init/
Run10ms/Run100ms) and the RAM storage of the C component's calibration parameters, and the C source is compiled as
application code - with -Werror (CMAKE_COMPILE_WARNING_AS_ERROR) and -Wall -Wextra, while the Embedded Coder source
of DemoSwc keeps its exception (no -Werror, ecu/rte/CMakeLists.txt).

    python tools/rtegen/test/c_swc/target_build.py <build directory>

Needs cmake, ninja and arm-none-eabi-gcc (PATH or NX_ARM_GCC_BIN), like the target group of tools/test.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
BASE = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
PRESET = "buildtest-s32k148-min"
SYMBOLS = ("LevelMonSwc_Init", "LevelMonSwc_Run10ms", "LevelMonSwc_Run100ms", "DemoSwc_Init", "Run10ms", "Run100ms",
           "Rte_Prm_LevelMonSwc_CalPrm_Gain_data", "Rte_Prm_LevelMonSwc_CalPrm_Weights_data")


def project_file(build: Path, rte: Path = HERE / "rte.yaml") -> Path:
    """buildtest_s32k148_min with the ECU description `rte` (default: this profile's), board and rte absolute;
    written to <build>/project/project.yaml. Also used by tools/rtegen/test_rtegen_c_swc.py (validate)."""
    yaml = YAML(typ="rt")
    yaml.width = 4096                    # long absolute paths stay on one line
    data = yaml.load(BASE.read_text(encoding="utf-8"))
    data["project"]["name"] = "test_c_swc"
    data["project"]["board"] = (BASE.parent / data["project"]["board"]).resolve().as_posix()
    data["project"]["rte"] = Path(rte).resolve().as_posix()
    data["project"]["description"] = "RTE-20 test profile: C component LevelMonSwc next to DemoSwc (no hardware)"
    target = build / "project" / "project.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as out:
        yaml.dump(data, out)
    return target


def run(argv: list[str], env: dict) -> str:
    print("$", subprocess.list2cmdline(argv), flush=True)
    result = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    output = result.stdout + result.stderr
    if result.returncode != 0:
        print(output[-6000:])
        raise SystemExit(f"FAILED ({result.returncode}): {argv[0]} {argv[1]}")
    return output


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    build = Path(sys.argv[1]).resolve()
    env = {**os.environ, "PYTHONUTF8": "1"}
    env["PATH"] = os.pathsep.join(p for p in (env.get("NX_ARM_GCC_BIN"), env.get("PATH")) if p)
    env.setdefault("CC", "arm-none-eabi-gcc")
    env.setdefault("CXX", "arm-none-eabi-g++")
    project = project_file(build)
    # the build directory is kept between runs: a cache whose compiler moved or vanished is configured fresh
    # (tools/test/build_dirs.py, VERIFY-0928)
    sys.path.insert(0, str(REPO / "tools" / "test"))
    from build_dirs import fresh_args
    fresh, stale = fresh_args(build, env["PATH"])
    if stale:
        print(f"configured fresh: {stale}", flush=True)
    run(["cmake", "--preset", PRESET, *fresh, "-B", str(build), f"-DNX_PROJECT={project.as_posix()}",
         f"-DPython3_EXECUTABLE={sys.executable}"], env)
    log = run(["cmake", "--build", str(build), "--config", "RelWithDebInfo"], env)
    failures = []
    warnings = [line for line in log.splitlines() if re.search(r"\bwarning:", line, re.I)]
    if warnings:
        failures.append(f"{len(warnings)} warning(s) in the build log:\n" + "\n".join(warnings[:40]))

    elf = next(iter(sorted(build.glob("**/app.ecu.elf"))), None)
    if elf is None:
        failures.append("app.ecu.elf missing")
    else:
        nm = shutil.which("arm-none-eabi-nm", path=env["PATH"])
        names = set(subprocess.run([nm, str(elf)], capture_output=True, text=True).stdout.split()) if nm else set()
        missing = [s for s in SYMBOLS if s not in names]
        if not nm:
            failures.append("arm-none-eabi-nm not found")
        elif missing:
            failures.append(f"symbols missing in {elf.name}: {', '.join(missing)}")

    commands = json.loads((build / "compile_commands.json").read_text(encoding="utf-8"))
    def command_of(name: str) -> str:
        for entry in commands:
            if Path(entry["file"]).name == name and "RelWithDebInfo" in entry.get("output", "RelWithDebInfo"):
                return entry.get("command") or " ".join(entry.get("arguments", []))
        return ""
    c_cmd, simulink_cmd = command_of("LevelMonSwc.c"), command_of("DemoSwc.c")
    if not c_cmd:
        failures.append("LevelMonSwc.c is not compiled")
    elif not all(flag in c_cmd.split() for flag in ("-Werror", "-Wall", "-Wextra")):
        failures.append(f"LevelMonSwc.c is not compiled as application code (-Wall -Wextra -Werror): {c_cmd}")
    if simulink_cmd and "-Werror" in simulink_cmd.split():
        failures.append("DemoSwc.c (Embedded Coder) is compiled with -Werror, the exception of ecu/rte is gone")

    for f in failures:
        print("FAIL", f)
    print(f"target build test_c_swc: {len(SYMBOLS) + 3} checks, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
