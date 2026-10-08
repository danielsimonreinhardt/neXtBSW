"""ARCH-58 (customer-project.md N5): a board **without any board port** is built.

    python tools/nxconfig/test/no_board_port_build.py [<parent directory>]

Until ARCH-58 every buildable board needed a hand-written `ecu/platforms/<board>/Options.cmake` with
`OPENBSW_PLATFORM` and ten `PLATFORM_SUPPORT_*` switches, and the root `CMakeLists.txt` refused to configure a
board without one. A customer board therefore needed a file at a place that is not the customer's - and its content
repeated what `project.yaml` already says (architecture section 9, row 2).

What this script proves (done_when of N5):

  - a customer repository with its **own derivative, own board and own project and no `ecu/` folder at all**
    configures and builds - nothing of the workspace and nothing of the tree names this board;
  - the feature switches the build uses are the derived ones (`PLATFORM_SUPPORT_IO/CAN/TRANSPORT/UDS/WATCHDOG/MPU`
    on, `ETHERNET` off), each with the sentence that says where it comes from;
  - the target images (`app.ecu.elf`, `bootloader.elf`) build **warning-free** with -Werror.

Own profile, never the eval board: the derivative `NBPMCU176` is a renamed copy of a family derivative, the board
`nbp_ctl` a renamed copy of the build-test board (no SBC, two passive transceivers, two CAN buses) and the project
a renamed copy of its minimal project. The sibling script `cmake/test/workspace_build.py` (ARCH-57) builds the same
kind of workspace **with** a board port; the only difference here is that there is none.

The parent directory defaults to `<temp>/s32k` (Windows 260 character limit, PLAT-06). Needs cmake, ninja and
arm-none-eabi-gcc (PATH or NX_ARM_GCC_BIN), like the target group of tools/test.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
TEMPLATES = REPO / "cmake" / "workspace"

WORKSPACE_DIR = "nbp"                       # short: part of every object path
DERIVATIVE = "NBPMCU176"                    # own derivative of the customer (renamed copy)
SOURCE_DERIVATIVE = "S32K148"
FAMILY = "s32k1xx"
BOARD = "nbp_ctl"
SOURCE_BOARD = "buildtest_s32k148"          # never the eval board
SOURCE_PROJECT = "buildtest_s32k148_min"
PROJECT = "nbp_main"
PRESET = "main"

# What the derivation has to produce for this project (its use.can, use.dio/adc and its diagnostics section, plus
# the statement of the family in ecu/platforms/s32k1xx/features.yaml). Written out here on purpose: the numbers
# are the point of the test, not something it should read back from the code under test.
EXPECTED = {"IO": True, "CAN": True, "ETHERNET": False, "TRANSPORT": True, "UDS": True,
            "WATCHDOG": True, "MPU": True}


def write(file: Path, text: str) -> Path:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")
    return file


def read(file: Path) -> str:
    return file.read_text(encoding="utf-8")


def relpath(target: Path, start: Path) -> str:
    try:
        return Path(os.path.relpath(target, start)).as_posix()
    except ValueError:
        return target.as_posix()


def create_workspace(ws: Path) -> None:
    """Pin, own derivative, own board, own project - and deliberately no `ecu/` folder."""
    tree = relpath(REPO, ws)
    version = read(REPO / "VERSION").strip()
    write(ws / "nextbsw.yaml",
          "# synthetic workspace of tools/nxconfig/test/no_board_port_build.py (ARCH-58) - test data\n"
          "schema_version: 1\n"
          "nextbsw:\n"
          f"  version: {version}\n"
          "  source: submodule\n"
          f"  path: {tree}\n"
          "  profile: open\n"
          "search:\n"
          "  mcudb: [mcudb]\n"
          "  boards: [boards]\n")

    # own derivative: a renamed copy, like the workspace of ARCH-57 (the device header of the family stays)
    source = REPO / "mcudb" / FAMILY / f"{SOURCE_DERIVATIVE}.yaml"
    text = re.sub(rf"^  name: {SOURCE_DERIVATIVE}$", f"  name: {DERIVATIVE}", read(source), flags=re.M)
    text = text.replace(f"describes: {SOURCE_DERIVATIVE}", f"describes: {DERIVATIVE}")
    assert f"name: {DERIVATIVE}" in text, "derivative name not replaced"
    write(ws / "mcudb" / FAMILY / f"{DERIVATIVE}.yaml", text)
    for mux in sorted((REPO / "mcudb" / FAMILY).glob(f"{SOURCE_DERIVATIVE}_*.pinmux.yaml")):
        shutil.copyfile(mux, ws / "mcudb" / FAMILY / mux.name)

    text = read(REPO / "boards" / SOURCE_BOARD / "board.yaml")
    text = text.replace(f"name: {SOURCE_BOARD}", f"name: {BOARD}").replace(f"mcu: {SOURCE_DERIVATIVE}",
                                                                          f"mcu: {DERIVATIVE}")
    write(ws / "boards" / BOARD / "board.yaml", text)

    text = read(REPO / "projects" / SOURCE_PROJECT / "project.yaml")
    text = text.replace(f"name: {SOURCE_PROJECT}", f"name: {PROJECT}").replace(f"../../boards/{SOURCE_BOARD}",
                                                                              f"../../boards/{BOARD}")
    write(ws / "projects" / PROJECT / "project.yaml", text)
    # the ECU description, with its component path pointed at the tree: the workspace brings no component of its
    # own (that is ARCH-57's subject, not this one), and component paths are relative to rte.yaml (N3)
    rte = read(REPO / "projects" / SOURCE_PROJECT / "rte.yaml")
    rte = rte.replace("../../swc/", relpath(REPO / "swc", ws / "projects" / PROJECT) + "/")
    write(ws / "projects" / PROJECT / "rte.yaml", rte)

    write(ws / "CMakeLists.txt", read(TEMPLATES / "CMakeLists.txt.in").replace("@NX_TREE@", tree))
    presets = (read(TEMPLATES / "CMakePresets.json.in").replace("@NX_TREE@", tree)
               .replace("@NX_PRESET@", PRESET).replace("@NX_PROJECT_DIR@", PROJECT))
    write(ws / "CMakePresets.json", presets)


def link_venv(ws: Path) -> bool:
    """`<workspace>/.venv` -> the environment this script runs in (gap N8 has no command for it yet)."""
    venv = Path(sys.executable).resolve().parents[1]
    if not (venv / "pyvenv.cfg").is_file():
        return False
    try:
        if os.name == "nt":
            done = subprocess.run(["cmd", "/c", "mklink", "/J", str(ws / ".venv"), str(venv)],
                                  capture_output=True, text=True)
            if done.returncode != 0:
                return False
        else:
            os.symlink(venv, ws / ".venv", target_is_directory=True)
    except OSError:
        return False
    return (ws / ".venv" / Path(sys.executable).parent.name / Path(sys.executable).name).exists()


def remove_workspace(ws: Path) -> None:
    link = ws / ".venv"
    if link.exists() and os.path.islink(str(link)):
        try:
            os.rmdir(link)
        except OSError:
            os.unlink(link)
    shutil.rmtree(ws, ignore_errors=True)


def run(argv: list[str], cwd: Path) -> tuple[int, str]:
    print("$", " ".join(argv), flush=True)
    env = dict(os.environ)
    gcc_bin = env.get("NX_ARM_GCC_BIN")
    if gcc_bin:
        env["PATH"] = os.pathsep.join([gcc_bin, env.get("PATH", "")])
    env["PYTHONUTF8"] = "1"
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env)
    return done.returncode, done.stdout + done.stderr


def main() -> int:
    parent = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "s32k"
    ws = parent / WORKSPACE_DIR
    remove_workspace(ws)
    ws.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    checks = 0

    create_workspace(ws)
    print(f"workspace {ws} (tree {REPO})")

    # the point of the whole exercise: nobody wrote a board port, here or in the tree
    checks += 1
    if (ws / "ecu").exists():
        failures.append(f"the workspace has an ecu/ folder - this test is about a board without a board port")
    checks += 1
    if (REPO / "ecu" / "platforms" / BOARD).exists():
        failures.append(f"the tree knows the board {BOARD} - it must not")

    argv = ["cmake", "--preset", PRESET]
    if not link_venv(ws):
        print("note: no .venv could be linked into the workspace, passing the interpreter explicitly")
        argv.append(f"-DPython3_EXECUTABLE={sys.executable}")

    code, out = run(argv, ws)
    print(out[-4000:] if code else out[-2000:])
    checks += 1
    if code != 0:
        failures.append("configure of the workspace failed - a board without a board port does not configure")
        print(f"no board port: {checks} checks, {len(failures)} failures")
        return 1

    # the derived switches, as the configure run reports them ("-- PLATFORM_SUPPORT_X ON" for every one that is on)
    reported = set(re.findall(r"PLATFORM_SUPPORT_([A-Z_]+) ON", out))
    for name, expected in EXPECTED.items():
        checks += 1
        if (name in reported) != expected:
            failures.append(f"PLATFORM_SUPPORT_{name} is {'off' if expected else 'on'}, expected "
                            f"{'on' if expected else 'off'} (derived from the project and the family)")
    checks += 1
    if f"board {BOARD}" not in out or DERIVATIVE not in out:
        failures.append(f"nxconfig did not use the board {BOARD} on {DERIVATIVE} of the workspace")

    build = ws / "build" / PRESET
    code, out = run(["cmake", "--build", str(build)], ws)
    warnings = [line for line in out.splitlines() if "warning" in line.lower()]
    print(out[-4000:] if code else out[-1200:])
    checks += 1
    if code != 0:
        failures.append("the build of a board without a board port failed")
    checks += 1
    if warnings:
        failures.append(f"{len(warnings)} warning(s) in the build log, first: {warnings[0]}")
    for what in ("app.ecu.elf", "bootloader.elf"):
        checks += 1
        if not list(build.rglob(what)):
            failures.append(f"{what} was not built")

    for failure in failures:
        print(f"FAIL: {failure}")
    print(f"no board port: {checks} checks, {len(failures)} failures")
    if not failures:
        remove_workspace(ws)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
