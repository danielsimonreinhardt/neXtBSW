# SPDX-License-Identifier: Apache-2.0
"""Target compile of bspPinEvent (IO-53) with the compiler command line of a real target build.

The module is not linked by any project yet (the integration comes with IO-54), so no target build compiles it.
This script takes the command line of a translation unit of the build that sees OpenBSW util (the console of the
high-side switch, libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitchSystem.cpp, or any other one with
util/include on its include path), puts the module's include directory in front and compiles

  - src/pinEvent/PinEvent.cpp         (the module: ISR and task side)
  - src/pinEvent/PinEventCommand.cpp  (console group "pinevent", OpenBSW GroupCommand)

with the same flags (-Werror included): no error and no warning allowed. It prints the code size of the ISR
entry PinEvent::onEdge and of each object (arm-none-eabi-nm / size next to the compiler).

    python libs/bsp/bspPinEvent/test/target_compile.py <build directory of a target build>
    python libs/bsp/bspPinEvent/test/target_compile.py <build directory> --configure <project.yaml>

With --configure the script first configures the project into the build directory (no build: the compile lines are
written at configure time, tools/test/build_dirs.py configure_project). Run by tools/test as pinevent-target-compile
on the synthetic full-featured test profile testproject_full (PLAT-62, S32K144 with the high-side switch whose
console unit is PREFERRED) - an open profile, so the probe also runs in the exported open tree, which lacks the
private eval board project (TOOL-245; before: the demo build {build}/demo).
"""

import json
import re
import shlex
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent
REPO = MODULE.parents[2]
SOURCES = ("src/pinEvent/PinEvent.cpp", "src/pinEvent/PinEventCommand.cpp")
PREFERRED = "libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitchSystem.cpp"


def argv_of(command: str) -> list[str]:
    return shlex.split(command.replace('\\"', "\0").replace("\\", "/").replace("\0", '\\"'))


def pick_entry(entries: list[dict]) -> dict | None:
    """The entry of PREFERRED, else the first C++ entry with OpenBSW util on its include path."""
    fallback = None
    for e in entries:
        source = Path(e["file"]).resolve()
        rel = source.relative_to(REPO).as_posix() if source.is_relative_to(REPO) else source.as_posix()
        command = e.get("command", "").replace("\\", "/")
        if rel == PREFERRED:
            return e
        if fallback is None and rel.endswith(".cpp") and "libs/bsw/util/include" in command:
            fallback = e
    return fallback


def main() -> int:
    args = sys.argv[1:]
    if len(args) not in (1, 3) or (len(args) == 3 and args[1] != "--configure"):
        print(__doc__)
        return 2
    build = Path(args[0]).resolve()
    if len(args) == 3:
        sys.path.insert(0, str(REPO / "tools" / "test"))
        from build_dirs import configure_project
        project = Path(args[2]) if Path(args[2]).is_absolute() else REPO / args[2]
        r = configure_project(REPO, project, build, sys.executable)
        if r.returncode != 0:
            print(f"FAIL configure of {args[2]}:\n{(r.stdout + r.stderr)[-3000:]}")
            return 1
    database = build / "compile_commands.json"
    if not database.is_file():
        print(f"FAIL no compile_commands.json in {build}: run the target build first (tools/test -k target-demo)")
        return 1
    entry = pick_entry(json.loads(database.read_text(encoding="utf-8")))
    if entry is None:
        print("FAIL no translation unit with OpenBSW util on its include path in the build")
        return 1
    base = argv_of(entry["command"])
    try:
        c = base.index("-c")
        o = base.index("-o")
    except ValueError:
        print(f"FAIL unexpected command line: {entry['command'][:300]}")
        return 1
    print(f"flags of {Path(entry['file']).name}: {' '.join(a for a in base[1:] if a.startswith(('-m', '-O', '-W', '-std')))}")
    compiler = Path(base[0])
    tools = compiler.parent
    prefix = compiler.name[: -len("g++")] if compiler.name.endswith("g++") else compiler.name[: -len("g++.exe")]
    work = Path(tempfile.mkdtemp(prefix="nx_pinevent_target_"))
    failures = 0
    try:
        objects = []
        for rel in SOURCES:
            obj = work / (Path(rel).stem + ".o")
            argv = list(base)
            argv[c + 1] = str((MODULE / rel).as_posix())
            argv[o + 1] = obj.as_posix()
            argv = [a for a in argv if a != "-fdiagnostics-color=always"]
            argv.insert(1, f"-I{(MODULE / 'include').as_posix()}")
            r = subprocess.run(argv, cwd=entry["directory"], capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            text = r.stdout + r.stderr
            if r.returncode != 0 or re.search(r"\bwarning:", text):
                failures += 1
                print(f"FAIL {rel}:\n{text[-3000:]}")
                continue
            print(f"OK   {rel}")
            objects.append(obj)
        size_tool = tools / f"{prefix}size{compiler.suffix}"
        nm_tool = tools / f"{prefix}nm{compiler.suffix}"
        if objects and size_tool.exists():
            r = subprocess.run([str(size_tool), *[str(p) for p in objects]], capture_output=True, text=True)
            print(r.stdout.strip())
        if objects and nm_tool.exists():
            r = subprocess.run([str(nm_tool), "-C", "--size-sort", "-S", str(objects[0])], capture_output=True,
                               text=True)
            for line in r.stdout.splitlines():
                if "PinEvent::onEdge" in line or "PinEvents::cycle" in line or "PinEvent::snapshot" in line:
                    size = int(line.split()[1], 16)
                    print(f"{line.split(' ', 3)[3]}: {size} bytes")
            # the hand-over must not need libatomic (ARMv6-M has no exclusive access, the toolchain no libatomic)
            r = subprocess.run([str(nm_tool), "-u", str(objects[0])], capture_output=True, text=True)
            atomics = [s for s in r.stdout.split() if s.startswith(("__atomic", "__sync"))]
            if atomics:
                failures += 1
                print(f"FAIL PinEvent.o needs libatomic: {', '.join(atomics)}")
            else:
                print("no libatomic symbol referenced")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"{len(SOURCES)} objects, {failures} failures")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
