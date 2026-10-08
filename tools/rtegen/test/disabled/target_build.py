# SPDX-License-Identifier: Apache-2.0
"""Target build of the RTE-23 test profile: a disabled component and a disabled runnable in a real firmware image.

Like tools/rtegen/test/c_swc/target_build.py (whose project derivation it uses): the open build test project
projects/buildtest_s32k148_min (S32K148, no SBC) with the ECU description tools/rtegen/test/disabled/rte.yaml,
configured with the preset buildtest-s32k148-min (-DNX_PROJECT) and built.

Checked: configure and build pass, the build log has no warning, app.ecu.elf holds the runnables that run
(AuxSrcSwc_RunMain10ms, SinkSwc_Run10ms and their inits) and the constant buffers of the ports fed by a disabled part,
and it does NOT hold the code of the disabled parts (LevelSrcSwc_Init, LevelSrcSwc_Run10ms, AuxSrcSwc_RunAux10ms):
the components stay compiled, nothing calls that code any more, the linker drops it (--gc-sections).

    python tools/rtegen/test/disabled/target_build.py <build directory>

Needs cmake, ninja and arm-none-eabi-gcc (PATH or NX_ARM_GCC_BIN), like the target group of tools/test.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "c_swc"))

import target_build as c_swc  # noqa: E402  (project derivation and command runner of the RTE-20 target build)

PRESET = c_swc.PRESET
PRESENT = ("AuxSrcSwc_Init", "AuxSrcSwc_RunMain10ms", "SinkSwc_Init", "SinkSwc_Run10ms",
           "Rte_Buf_SinkSwc_Level_mV_Level_mV", "Rte_Buf_SinkSwc_Aux_Aux", "Rte_Buf_SinkSwc_LevelQlf_LevelQlf")
ABSENT = ("LevelSrcSwc_Init", "LevelSrcSwc_Run10ms", "AuxSrcSwc_RunAux10ms")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    build = Path(sys.argv[1]).resolve()
    env = {**os.environ, "PYTHONUTF8": "1"}
    env["PATH"] = os.pathsep.join(p for p in (env.get("NX_ARM_GCC_BIN"), env.get("PATH")) if p)
    env.setdefault("CC", "arm-none-eabi-gcc")
    env.setdefault("CXX", "arm-none-eabi-g++")
    project = c_swc.project_file(build, HERE / "rte.yaml")
    sys.path.insert(0, str(c_swc.REPO / "tools" / "test"))
    from build_dirs import fresh_args
    fresh, stale = fresh_args(build, env["PATH"])
    if stale:
        print(f"configured fresh: {stale}", flush=True)
    c_swc.run(["cmake", "--preset", PRESET, *fresh, "-B", str(build), f"-DNX_PROJECT={project.as_posix()}",
               f"-DPython3_EXECUTABLE={sys.executable}"], env)
    log = c_swc.run(["cmake", "--build", str(build), "--config", "RelWithDebInfo"], env)
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
        missing = [s for s in PRESENT if s not in names]
        if missing:
            failures.append(f"symbols missing in {elf.name}: {', '.join(missing)}")
        kept = [s for s in ABSENT if s in names]
        if kept:
            failures.append(f"code of a disabled part is still in {elf.name}: {', '.join(kept)}")
        # not blind: the disabled components are compiled - their objects hold the functions the linker dropped
        objects = sorted(str(p) for name in ("LevelSrcSwc", "AuxSrcSwc") for p in build.glob(f"**/{name}.c.obj"))
        compiled = set(subprocess.run([nm, *objects], capture_output=True, text=True).stdout.split()) \
            if objects else set()
        missing = [s for s in ABSENT if s not in compiled]
        if missing:
            failures.append(f"the disabled components are not compiled as expected ({', '.join(missing)} missing "
                            f"in {len(objects)} object file(s))")
        print(f"{elf.name}: {len(PRESENT)} symbols of the running parts present, the {len(ABSENT)} functions of "
              f"the disabled parts {'absent' if not kept else 'NOT absent'}")

    for f in failures:
        print("FAIL", f)
    print(f"target build test_disabled: {len(PRESENT) + len(ABSENT) + 2} checks, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
