# SPDX-License-Identifier: Apache-2.0
"""Target build of the open demo projects with the C substitute of DemoSwc instead of the Simulink code (PLAT-119).

Builds projects/buildtest_s32k148_min (preset buildtest-s32k148-min, the verify preset of the export) with the
component folder the export writes (assemble.py: swc/DemoSwc without generated/, plus swc.yaml and src/DemoSwc.c
of the substitute). The project is derived in <build>/project: its rte.yaml points DemoSwc at the assembled folder,
nothing else changes. The source tree stays as it is.

Checked: configure and build pass, the build log has no warning, app.ecu.elf holds DemoSwc_Init, DemoSwc_Run10ms,
DemoSwc_Run100ms and the RAM copy of FilterAlpha but not the Embedded Coder entry points Run10ms/Run100ms, DemoSwc.c
of the substitute is compiled as application code (-Wall -Wextra -Werror) and no file of swc/DemoSwc/generated is
compiled.

    python swc/DemoSwcSubstitute/test/target_build.py <build directory>

Needs cmake, ninja and arm-none-eabi-gcc (PATH or NX_ARM_GCC_BIN), like the target group of tools/test.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))

import assemble  # noqa: E402

BASE = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
PRESET = "buildtest-s32k148-min"
SYMBOLS = ("DemoSwc_Init", "DemoSwc_Run10ms", "DemoSwc_Run100ms", "Rte_Prm_DemoSwc_CalPrm_FilterAlpha_data")
GONE = ("Run10ms", "Run100ms")          # Embedded Coder names of the model's runnables


def derived_project(build: Path) -> Path:
    """buildtest_s32k148_min with the assembled DemoSwc; board and rte absolute, written to <build>/project."""
    folder = assemble.assemble(build / "swc")
    yaml = YAML(typ="rt")
    yaml.width = 4096                    # long absolute paths stay on one line
    ecu = yaml.load((BASE.parent / "rte.yaml").read_text(encoding="utf-8"))
    entries = [c for c in ecu["components"] if c["name"] == "DemoSwc"]
    if len(entries) != 1:
        raise SystemExit(f"{BASE.parent / 'rte.yaml'}: expected one component DemoSwc, found {len(entries)}")
    entries[0]["path"] = folder.as_posix()
    target = build / "project"
    target.mkdir(parents=True, exist_ok=True)
    with (target / "rte.yaml").open("w", encoding="utf-8", newline="\n") as out:
        yaml.dump(ecu, out)
    data = yaml.load(BASE.read_text(encoding="utf-8"))
    data["project"]["board"] = (BASE.parent / data["project"]["board"]).resolve().as_posix()
    data["project"]["rte"] = (target / "rte.yaml").as_posix()
    with (target / "project.yaml").open("w", encoding="utf-8", newline="\n") as out:
        yaml.dump(data, out)
    return target / "project.yaml"


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
    project = derived_project(build)
    # the build directory is kept between runs: a cache whose compiler moved or vanished is configured fresh
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
    nm = shutil.which("arm-none-eabi-nm", path=env["PATH"])
    if elf is None:
        failures.append("app.ecu.elf missing")
    elif not nm:
        failures.append("arm-none-eabi-nm not found")
    else:
        names = set(subprocess.run([nm, str(elf)], capture_output=True, text=True).stdout.split())
        missing = [s for s in SYMBOLS if s not in names]
        if missing:
            failures.append(f"symbols missing in {elf.name}: {', '.join(missing)}")
        present = [s for s in GONE if s in names]
        if present:
            failures.append(f"Embedded Coder entry points in {elf.name}: {', '.join(present)}")

    commands = json.loads((build / "compile_commands.json").read_text(encoding="utf-8"))
    # multi-config generator: one entry per configuration, the build above is RelWithDebInfo
    commands = [e for e in commands if "RelWithDebInfo" in e.get("output", "RelWithDebInfo")]
    files = [Path(e["file"]).resolve().as_posix() for e in commands]
    substitute = [e for e in commands if Path(e["file"]).name == "DemoSwc.c"]
    if len(substitute) != 1 or not Path(substitute[0]["file"]).resolve().is_relative_to(build / "swc"):
        failures.append(f"DemoSwc.c of the assembled folder not compiled exactly once: "
                        f"{[e['file'] for e in substitute]}")
    else:
        command = substitute[0].get("command") or " ".join(substitute[0].get("arguments", []))
        if not all(flag in command.split() for flag in ("-Werror", "-Wall", "-Wextra")):
            failures.append(f"DemoSwc.c is not compiled as application code (-Wall -Wextra -Werror): {command}")
    generated = [f for f in files if "/swc/DemoSwc/generated/" in f]
    if generated:
        failures.append(f"Simulink code compiled: {generated}")

    for f in failures:
        print("FAIL", f)
    print(f"target build demoswc substitute: {len(SYMBOLS) + len(GONE) + 4} checks, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
