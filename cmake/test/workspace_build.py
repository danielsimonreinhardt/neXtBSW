"""Target build of a synthetic workspace outside the neXtBSW tree (ARCH-57, customer-project.md gap N4).

    python cmake/test/workspace_build.py [<parent directory>]

Builds a **customer repository** that lies outside this tree and owns everything an ECU decides: its own
derivative (`ACMEMCU176`, a renamed copy of a family derivative), its own board (`acme_ctl_b1`) with its own board
port, its own project, its own module catalogue entry and the pin `nextbsw.yaml` that names this tree. Nothing of
the workspace is a neXtBSW board: the eval board is never the yardstick (CLAUDE.md, guiding principle).

What this proves (done_when of N4):

  - the workspace root holds the thin `CMakeLists.txt` and the `CMakePresets.json` of the templates in
    `cmake/workspace/`; the build itself is the root `CMakeLists.txt` of the tree, which separates `NX_ROOT`
    (tree) from `NX_WORKSPACE` (customer repository);
  - the base presets of the tree are includable: the workspace's preset file includes `<tree>/cmake/NxPresets.json`
    and inherits `nx-arm-gcc`;
  - build directory and `.venv` come from the **workspace** (`${sourceDir}` of the included presets);
  - the board port is found in the workspace, so a customer board needs no change in the tree;
  - the target image (`app.ecu.elf`, `bootloader.elf`) builds **warning-free** with -Werror;
  - the longest object path is measured against the Windows limit of 260 characters, together with the length a
    submodule segment (`<workspace>/nextbsw/...`) adds to the source paths.

The parent directory defaults to `<temp>/s32k`: a workspace below the repository's `build/` would push the object
paths over the Windows limit (the number is in the report of this script). Needs cmake, ninja and
arm-none-eabi-gcc (PATH or NX_ARM_GCC_BIN), like the target group of tools/test.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TEMPLATES = REPO / "cmake" / "workspace"

WINDOWS_PATH_LIMIT = 260

# the workspace: invented names throughout, short folder names so the object paths stay below the Windows limit
WORKSPACE_DIR = "acme"                      # the customer repository
DERIVATIVE = "ACMEMCU176"                   # own name of a derivative of the family (renamed copy)
SOURCE_DERIVATIVE = "S32K148"               # the family derivative it is copied from (mcudb of the tree)
FAMILY = "s32k1xx"
BOARD = "acme_ctl_b1"
SOURCE_BOARD = "buildtest_s32k148"          # board and project the copy starts from (never the eval board)
SOURCE_PROJECT = "buildtest_s32k148_min"
PROJECT = "acme_main"
MODULE = "acmeLevelMon"
PRESET = "main"                             # short: part of every object path


def write(file: Path, text: str) -> Path:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")
    return file


def read(file: Path) -> str:
    return file.read_text(encoding="utf-8")


def relpath(target: Path, start: Path) -> str:
    try:
        return Path(os.path.relpath(target, start)).as_posix()
    except ValueError:                      # other drive: an absolute path is allowed too
        return target.as_posix()


def create_workspace(ws: Path) -> None:
    """The customer repository: pin, own derivative, own board with its port, own project, own module."""
    tree = relpath(REPO, ws)
    version = (REPO / "VERSION").read_text(encoding="utf-8").strip()

    write(ws / "nextbsw.yaml",
          "# synthetic workspace of cmake/test/workspace_build.py (ARCH-57) - test data, no real customer\n"
          "schema_version: 1\n"
          "nextbsw:\n"
          f"  version: {version}\n"
          "  source: submodule\n"
          f"  path: {tree}\n"
          "  profile: open\n"
          "search:\n"
          "  mcudb: [mcudb]\n"
          "  modules: [libs]\n"
          "  boards: [boards]\n")

    # own derivative: a renamed copy of a derivative of the family, with its pin multiplexing tables. Only the
    # name changes - the device header file of the family stays the one of the tree (mcudb device_header.file).
    # device_header.describes has to name the derivative itself: nxconfig looks a described derivative up **next
    # to the derivative file** only (checks.described_derivative), so a workspace derivative cannot point at a
    # derivative of the tree. Open point for tools/nxconfig, noted in doc/changelog.d/ARCH-57.md.
    source = REPO / "mcudb" / FAMILY / f"{SOURCE_DERIVATIVE}.yaml"
    text = re.sub(rf"^  name: {SOURCE_DERIVATIVE}$", f"  name: {DERIVATIVE}", read(source), flags=re.M)
    text = text.replace(f"describes: {SOURCE_DERIVATIVE}", f"describes: {DERIVATIVE}")
    assert f"name: {DERIVATIVE}" in text, "derivative name not replaced"
    write(ws / "mcudb" / FAMILY / f"{DERIVATIVE}.yaml", text)
    for mux in sorted((REPO / "mcudb" / FAMILY).glob(f"{SOURCE_DERIVATIVE}_*.pinmux.yaml")):
        shutil.copyfile(mux, ws / "mcudb" / FAMILY / mux.name)

    # own board on that derivative
    text = read(REPO / "boards" / SOURCE_BOARD / "board.yaml")
    text = text.replace(f"name: {SOURCE_BOARD}", f"name: {BOARD}").replace(f"mcu: {SOURCE_DERIVATIVE}",
                                                                          f"mcu: {DERIVATIVE}")
    write(ws / "boards" / BOARD / "board.yaml", text)

    # own board port: the feature switches of the customer board, in the customer repository (N5 moves them into
    # the project; until then the workspace brings its own port and the tree stays untouched)
    write(ws / "ecu" / "platforms" / BOARD / "Options.cmake",
          f"# Board port of {BOARD}, in the workspace - the neXtBSW tree knows nothing about this board.\n"
          + read(REPO / "ecu" / "platforms" / SOURCE_BOARD / "Options.cmake"))

    # own project on that board
    text = read(REPO / "projects" / SOURCE_PROJECT / "project.yaml")
    text = text.replace(f"name: {SOURCE_PROJECT}", f"name: {PROJECT}").replace(f"../../boards/{SOURCE_BOARD}",
                                                                              f"../../boards/{BOARD}")
    write(ws / "projects" / PROJECT / "project.yaml", text)
    shutil.copyfile(REPO / "projects" / SOURCE_PROJECT / "rte.yaml", ws / "projects" / PROJECT / "rte.yaml")
    # RTE-22: the paths of an rte.yaml are relative to the file itself since format 2, so the component has
    # to live in the workspace - which is what a customer project looks like anyway: its own SWC beside its
    # own project, not one borrowed from the neXtBSW tree.
    shutil.copytree(REPO / "swc" / "DemoSwc", ws / "swc" / "DemoSwc", dirs_exist_ok=True)

    # own module of the workspace (catalogue entry: found through search.modules, linked by nothing here)
    write(ws / "libs" / MODULE / "module.yaml",
          f"module: {MODULE}\nkind: library\ndescription: level monitor of the customer\n")

    # root files from the templates the wizard uses (TOOL-59)
    write(ws / "CMakeLists.txt", read(TEMPLATES / "CMakeLists.txt.in").replace("@NX_TREE@", tree))
    presets = read(TEMPLATES / "CMakePresets.json.in")
    presets = (presets.replace("@NX_TREE@", tree).replace("@NX_PRESET@", PRESET)
                      .replace("@NX_PROJECT_DIR@", PROJECT))
    write(ws / "CMakePresets.json", presets)


def link_venv(ws: Path) -> bool:
    """`<workspace>/.venv` -> the environment this script runs in, so the preset's ${sourceDir}/.venv exists.

    A workspace gets its own environment from the requirements of the pinned tree (gap N8); until that command
    exists the link stands in for it. False if no link could be made (then the interpreter is passed explicitly).
    """
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
    """Removes the workspace - the link <workspace>/.venv first, and never its content."""
    link = ws / ".venv"
    if link.is_symlink() or (link.exists() and os.path.islink(str(link))):
        try:
            os.rmdir(link)                  # a directory junction/symlink: removes the link, not the target
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
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return done.returncode, done.stdout + done.stderr


def longest_object_path(build: Path) -> tuple[int, Path | None]:
    longest, where = 0, None
    for obj in build.rglob("*.obj"):
        if len(str(obj)) > longest:
            longest, where = len(str(obj)), obj
    for obj in build.rglob("*.o"):
        if len(str(obj)) > longest:
            longest, where = len(str(obj)), obj
    return longest, where


def main() -> int:
    parent = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "s32k"
    ws = parent / WORKSPACE_DIR
    remove_workspace(ws)
    ws.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    checks = 0

    create_workspace(ws)
    print(f"workspace {ws} (tree {REPO})")

    # the base presets of the tree are includable, and their macros point at the workspace
    checks += 1
    base = next(p for p in json.loads(read(REPO / "cmake" / "NxPresets.json"))["configurePresets"]
                if p["name"] == "nx-arm-gcc")
    if base["binaryDir"] != "${sourceDir}/build/${presetName}" or \
            base["cacheVariables"]["Python3_EXECUTABLE"] != "${sourceDir}/.venv/Scripts/python.exe":
        failures.append("nx-arm-gcc does not take build directory and .venv from ${sourceDir} (the workspace)")

    argv = ["cmake", "--preset", PRESET]
    if not link_venv(ws):
        print("note: no .venv could be linked into the workspace, passing the interpreter explicitly")
        argv.append(f"-DPython3_EXECUTABLE={sys.executable}")

    code, out = run(argv, ws)
    print(out[-4000:] if code else out[-1500:])
    checks += 1
    if code != 0:
        failures.append("configure of the workspace failed")
        print(f"workspace target build: {checks} checks, {len(failures)} failures")
        return 1

    checks += 1
    if f"neXtBSW tree {REPO.as_posix()}" not in out.replace("\\", "/"):
        failures.append("the configure run does not report workspace and tree separately (NX_WORKSPACE/NX_ROOT)")
    checks += 1
    if f"board {BOARD}" not in out or DERIVATIVE not in out:
        failures.append(f"nxconfig did not use the board {BOARD} on {DERIVATIVE} of the workspace")

    build = ws / "build" / PRESET
    checks += 1
    if not (build / "CMakeCache.txt").is_file():
        failures.append(f"the build directory is not {build} (it must come from the workspace)")
    checks += 1
    if (REPO / "build" / PRESET).exists():
        failures.append(f"the build wrote into the tree ({REPO / 'build' / PRESET})")

    code, out = run(["cmake", "--build", "--preset", PRESET], ws)
    checks += 1
    if code != 0:
        print(out[-8000:])
        failures.append("build of the workspace failed")
    else:
        warnings = [line for line in out.splitlines() if re.search(r"\bwarning:|\bWarnung\b", line, re.I)]
        checks += 1
        if warnings:
            failures.append(f"{len(warnings)} warning(s) in the build log:\n" + "\n".join(warnings[:40]))
        for image in ("app.ecu.elf", "bootloader.elf"):
            checks += 1
            if not any(build.glob(f"**/{image}")):
                failures.append(f"{image} missing in {build}")

    # Windows 260-character limit (PLAT-06): the object paths of this build and the source paths a submodule
    # segment produces in a real workspace (the tree below the workspace as <workspace>/nextbsw).
    longest, where = longest_object_path(build)
    if where is not None:
        tail = longest - len(str(build))
        print(f"longest object path: {longest} characters ({where})")
        print(f"  build directory {build}: {len(str(build))} characters, below it {tail}")
        print(f"  => the build directory of a workspace may be at most {WINDOWS_PATH_LIMIT - 1 - tail} characters "
              "long")
    openbsw = REPO / "external" / "openbsw"
    deepest = max((len(str(p.relative_to(REPO))) for p in openbsw.rglob("*.cpp")), default=0)
    submodule_root = ws / "nextbsw"
    print(f"longest source path below the tree root: {deepest} characters (submodule external/openbsw); "
          f"with the tree as a submodule at {submodule_root} that is {len(str(submodule_root)) + 1 + deepest} "
          f"characters, {WINDOWS_PATH_LIMIT - 1 - deepest} characters are left for the workspace root")
    checks += 1
    if longest >= WINDOWS_PATH_LIMIT:
        failures.append(f"longest object path {longest} >= {WINDOWS_PATH_LIMIT} characters: {where}")

    for f in failures:
        print("FAIL", f)
    print(f"workspace target build ({BOARD} on {DERIVATIVE}): {checks} checks, {len(failures)} failures")
    if not failures:
        remove_workspace(ws)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
