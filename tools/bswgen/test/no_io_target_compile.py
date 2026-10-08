"""Target compile of every I/O driver against a project without any I/O channel (PLAT-94).

A project without a digital input did not compile: the generated inputConfiguration.h indexed the only row of an
empty table (&sfDigitalInputConfigurations[0][0], -Werror=array-bounds). The warning comes from the optimiser, so
only a real target compile with the flags of the target build sees it - a syntax check or the host compiler did not.

A complete image of a project without any I/O cannot be linked on s32k1xx today: the family template reads the
internal ADC channels and needs a safety section with two outputs (finding of TOOL-59, PLAT-95). The linked image
without digital input, PWM group and capture channel is the target build of the empty new project
(target-new-project). This script covers the rest - no output and no ADC channel as well - with the real sources and
the real compiler command lines of that build:

  1. from <build>/compile_commands.json and the dependency log of ninja, every translation unit that includes a
     generated I/O header (digital input/output, pin table, ADC, PWM, capture, hall);
  2. bswgen renders the own profile tools/bswgen/test/profiles/projects/testproject_no_io (the board offers one
     channel of every kind, the project uses none of them);
  3. each of these translation units is compiled again with that output in place of <build>/generated/board -
     no error and no warning allowed;
  4. the check must see the original fault: with the template of before PLAT-94 (row indexed with [0][0]) the
     digital input and output drivers must fail with -Warray-bounds.

    python tools/bswgen/test/no_io_target_compile.py <build directory of an s32k1xx target build>

Run by tools/test as target-no-io after target-new-project ({build}/np59). Needs ninja and the compiler of that build.
"""

import contextlib
import io
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BSWGEN = HERE.parent
REPO = BSWGEN.parents[1]
sys.path.insert(0, str(BSWGEN))
sys.path.insert(0, str(HERE))

import bswgen  # noqa: E402
import reference_output as ref  # noqa: E402

PROJECT = HERE / "profiles" / "projects" / "testproject_no_io" / "project.yaml"
GENERATED = "generated/board"
# generated headers whose tables may be empty (below GENERATED)
IO_HEADERS = ("bsp/io/io/", "bsp/io/input/", "bsp/io/output/", "bsp/io/outputPwm/", "bsp/adc/", "bsp/capture/",
              "bsp/hall/", "bsp/pinIrq/", "bsp/pinEvent/")
# drivers that must be among the translation units, otherwise the selection went wrong
REQUIRED = ("DigitalInput.cpp", "Output.cpp", "OutputPwm.cpp", "AnalogInput.cpp", "Io.cpp", "InputCapture.cpp",
            "PinIrqConfig.cpp")
# Translation units of the family template and the application that need more than the I/O tables: what the
# project of the build has and a project without any I/O cannot have. Their I/O use is compiled by the target builds
# themselves (with inputs, and without input, PWM and capture in target-new-project).
NOT_FOR_A_PROJECT_WITHOUT_IO = {
    "platforms/s32k1xx/port/safety/safeSystem/src/safeSystem/SafeSystem.cpp":
        "reads the internal ADC channels BANDGAP/ADCINTERNAL0 (family requirement, PLAT-95)",
    "platforms/s32k1xx/port/safety/safeIo/src/safeIo/SafeIo.cpp": "needs the safety section (PLAT-95)",
    "platforms/s32k1xx/port/safety/safeIo/src/safeIo/SafeState.cpp": "needs the safety section (PLAT-95)",
    "ecu/consoleCommands/src/safety/console/SafetyCommand.cpp": "needs the safety section (PLAT-95)",
    "platforms/s32k1xx/port/main/src/main.cpp": "integration: CAN bus, interrupt ranks and safety of the project",
    "platforms/s32k1xx/port/main/src/integration/Integration.cpp": "integration: console and modules of the project",
    "platforms/s32k1xx/port/main/src/systems/CanSystem.cpp": "integration: the CAN bus of the project",
    "ecu/application/src/app/app.cpp": "application: bootloader memory layout of the project",
}
# template of before PLAT-94: the row of an empty table indexed like a filled one
MUTANTS = (("inputConfiguration.h.j2", "{% if inputs %}\n    return &sfDigitalInputConfigurations[0][0];",
            "{% if true %}\n    return &sfDigitalInputConfigurations[0][0];", "DigitalInput.cpp"),
           ("outputConfiguration.h.j2", "{% if outputs %}\n    return &sfOutputConfigurations[0][0];",
            "{% if true %}\n    return &sfOutputConfigurations[0][0];", "Output.cpp"))


def io_objects(build: Path) -> set[str]:
    """Objects (paths relative to the build directory) whose recorded dependencies include a generated I/O header."""
    deps = subprocess.run(["ninja", "-C", str(build), "-t", "deps"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if deps.returncode != 0:
        raise SystemExit(f"ninja -t deps failed in {build}: {deps.stderr[-2000:]}")
    marker = f"/{GENERATED}/"
    objects, current = set(), None
    for line in deps.stdout.splitlines():
        if line and not line[0].isspace():
            current = line.split(":", 1)[0].strip().replace("\\", "/")
        elif current and line.strip():
            path = line.strip().replace("\\", "/")
            if marker in path and path.split(marker, 1)[1].startswith(IO_HEADERS):
                objects.add(current)
    return objects


def selected_commands(build: Path) -> tuple[list[dict], list[str]]:
    """The translation units to compile, and what is wrong with the two lists above."""
    entries = json.loads((build / "compile_commands.json").read_text(encoding="utf-8"))
    objects = io_objects(build)
    chosen, seen, excluded = [], set(), set()
    for e in entries:
        m = re.search(r" -o (\S+) -c ", e["command"])
        obj = m.group(1).replace("\\", "/") if m else None
        source = Path(e["file"]).resolve()
        rel = source.relative_to(REPO).as_posix() if source.is_relative_to(REPO) else source.as_posix()
        if obj not in objects or obj in seen:
            continue
        seen.add(obj)
        if rel in NOT_FOR_A_PROJECT_WITHOUT_IO:
            excluded.add(rel)
        else:
            chosen.append(e)
    names = {Path(e["file"]).name for e in chosen}
    problems = [f"{n}: not among the translation units with a generated I/O header" for n in REQUIRED
                if n not in names]
    problems += [f"{rel}: excluded, but no such translation unit in the build - remove the entry or fix the path"
                 for rel in NOT_FOR_A_PROJECT_WITHOUT_IO if rel not in excluded]
    return chosen, problems


def generate(out: Path) -> None:
    shutil.rmtree(out, ignore_errors=True)
    with contextlib.redirect_stderr(io.StringIO()):
        bswgen.generate(PROJECT, out)


def compile_one(entry: dict, build: Path, gen: Path, obj: Path) -> str:
    """'' when the translation unit compiles without warning with `gen` as generated board directory, else the
    compiler output."""
    command = entry["command"]
    old = (build / GENERATED).as_posix()
    if old not in command:
        return f"{old} not on the command line"
    command = command.replace(old, gen.as_posix())
    command = re.sub(r" -o \S+ -c ", f" -o {obj.as_posix()} -c ", command).replace("-fdiagnostics-color=always", "")
    # no shell: cmd.exe cuts command lines at 8191 characters. Windows paths get forward slashes so that the POSIX
    # split keeps them; \" stays the escaped quote of a define (-DCMAKE_INTDIR=\"RelWithDebInfo\").
    argv = shlex.split(command.replace('\\"', "\0").replace("\\", "/").replace("\0", '\\"'))
    r = subprocess.run(argv, cwd=entry["directory"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    text = r.stdout + r.stderr
    return text if r.returncode != 0 or re.search(r"\bwarning:", text) else ""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    build = Path(sys.argv[1]).resolve()
    if not (build / "compile_commands.json").is_file():
        print(f"FAIL no compile_commands.json in {build}: run the target build first (tools/test -k target-new-project)")
        return 1
    commands, failures = selected_commands(build)
    names = sorted({Path(e["file"]).name for e in commands})
    print(f"{len(commands)} translation units include a generated I/O header: {', '.join(names)}")
    work = Path(tempfile.mkdtemp(prefix="nx_noio_"))
    try:
        gen = work / "gen"
        generate(gen)
        for i, entry in enumerate(commands):
            output = compile_one(entry, build, gen, work / f"{i}.o")
            if output:
                failures.append(f"{entry['file']}:\n{output[-3000:]}")
        # the check must see the fault PLAT-94 removed
        for template, old, new, source in MUTANTS:
            with ref.mutated_template(template, old, new):
                generate(work / "mutant")
            entry = next((e for e in commands if Path(e["file"]).name == source), None)
            output = compile_one(entry, build, work / "mutant", work / "mutant.o") if entry else ""
            if "array-bounds" not in output:
                failures.append(f"mutant of {template} (empty row indexed with [0][0]) not detected in {source}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for f in failures:
        print("FAIL", f)
    print(f"no-I/O project against {len(commands)} translation units and {len(MUTANTS)} mutants: "
          f"{len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
