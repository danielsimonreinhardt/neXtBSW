"""Where the build outputs are: one rule for tools/test and for the host tests that read target builds (PLAT-07).

tools/test builds the target profiles below its build root ({build} in tests.yaml): `build/` in the repository, in an
agent worktree under `.claude/worktrees/` `%TEMP%/s32k/<worktree name>` (Windows 260 character limit, PLAT-06). The
demo project lands in `{build}/demo`.

Host tests that check a real target build (application hex, bootloader hex) find the demo build with
`demo_build_dir()`:

1. `NX_BUILD_DIR` (environment) - the build directory of the demo project, set explicitly or by tools/test;
2. `<build root>/demo` of tools/test, if it exists;
3. the binary directory of the CMake preset: `build/evalboard-s32k144-demo`, in a worktree
   `%TEMP%/s32k/<worktree name>` (preset `evalboard-s32k144-demo-worktree`), if it exists.

If none exists, the first candidate is returned so that the skip message names the expected place.

`stale_compiler()` / `fresh_args()` detect a CMake cache whose compiler moved or vanished (the target builds then
configure with `--fresh` instead of failing).
Standard library only: the tests import this file by path.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

ENV_DEMO_BUILD = "NX_BUILD_DIR"
DEMO_SUBDIR = "demo"                        # binary_dir of target-demo in tests.yaml: {build}/demo
DEMO_PRESET = "evalboard-s32k144-demo"      # configure preset of the demo project (CMakePresets.json)


def is_worktree(repo: Path) -> bool:
    parts = Path(repo).as_posix().split("/")
    return ".claude" in parts and "worktrees" in parts


def default_build_root(repo: Path) -> Path:
    """Build root of tools/test: short directory in an agent worktree (Windows 260 character limit, PLAT-06)."""
    if is_worktree(repo):
        return Path(tempfile.gettempdir()) / "s32k" / Path(repo).name
    return Path(repo) / "build"


def preset_build_dir(repo: Path) -> Path:
    """binaryDir of the demo preset (`nx-arm-gcc` resp. `nx-worktree` in CMakePresets.json)."""
    if is_worktree(repo):
        return Path(tempfile.gettempdir()) / "s32k" / Path(repo).name
    return Path(repo) / "build" / DEMO_PRESET


def demo_build_candidates(repo: Path, build_root: Path | None = None) -> list[Path]:
    env = os.environ.get(ENV_DEMO_BUILD)
    if env:
        return [Path(env)]
    return [(build_root or default_build_root(repo)) / DEMO_SUBDIR, preset_build_dir(repo)]


def demo_build_dir(repo: Path, build_root: Path | None = None) -> Path:
    """Build directory of the demo project (see module docstring); the first existing candidate."""
    candidates = demo_build_candidates(repo, build_root)
    for path in candidates:
        if (path / "CMakeCache.txt").is_file():
            return path
    return candidates[0]


def bootloader_hex(repo: Path) -> Path:
    return demo_build_dir(repo) / "bootloader" / "RelWithDebInfo" / "bootloader.hex"


# ---------------------------------------------------------------------------- stale compiler in a CMake cache
# CMake resolves CC=arm-none-eabi-gcc once, on the first configure, and keeps the absolute path in CMakeCache.txt.
# A later change of the compiler (NX_ARM_GCC_BIN pointed elsewhere, old installation removed - S32DS replaced by the
# xPack GCC of tools/setup, 2026-09) is never picked up again: every configure of that build directory fails or
# still uses the old compiler. The target builds of tools/test therefore configure such a directory with --fresh.

ARM_GCC = "arm-none-eabi-gcc"
_COMPILER_KEYS = ("CMAKE_C_COMPILER", "CMAKE_CXX_COMPILER", "CMAKE_ASM_COMPILER")


def compiler_search_path(environ: dict[str, str] | None = None) -> str:
    """PATH with NX_ARM_GCC_BIN in front, as the presets (cmake/NxPresets.json) build it."""
    environ = os.environ if environ is None else environ
    return os.pathsep.join(p for p in (environ.get("NX_ARM_GCC_BIN"), environ.get("PATH")) if p)


def cached_compilers(binary_dir: Path) -> dict[str, str]:
    """{CMAKE_C_COMPILER: path, ...} from the CMakeCache.txt of binary_dir; empty without a cache."""
    cache = Path(binary_dir) / "CMakeCache.txt"
    found: dict[str, str] = {}
    try:
        lines = cache.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return found
    for line in lines:
        key, sep, value = line.partition("=")
        name = key.split(":", 1)[0]
        if sep and name in _COMPILER_KEYS and value:
            found[name] = value
    return found


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except OSError:
        return False


def stale_compiler(binary_dir: Path, search_path: str | None = None) -> str:
    """Why the CMake cache of binary_dir must be configured fresh, or "" if it is usable (or does not exist).

    Stale is a cached compiler that no longer exists, and an arm-none-eabi compiler other than the one found now on
    `search_path` (default: NX_ARM_GCC_BIN in front of PATH). Other compilers (host builds) are only checked for
    existence."""
    compilers = cached_compilers(binary_dir)
    if not compilers:
        return ""
    for name, path in compilers.items():
        if os.path.isabs(path) and not os.path.isfile(path):
            return f"{name} {path} existiert nicht mehr"
    import shutil
    current = shutil.which(ARM_GCC, path=compiler_search_path() if search_path is None else search_path)
    cached = compilers.get("CMAKE_C_COMPILER", "")
    if current and os.path.basename(cached).lower().startswith("arm-none-eabi-") and not _same_file(cached, current):
        return f"CMAKE_C_COMPILER {cached} ist nicht der aktuelle Compiler {Path(current).as_posix()}"
    return ""


def fresh_args(binary_dir: Path, search_path: str | None = None) -> tuple[list[str], str]:
    """(["--fresh"], reason) for `cmake --preset` if the cache of binary_dir is stale, else ([], "")."""
    reason = stale_compiler(binary_dir, search_path)
    return (["--fresh"], reason) if reason else ([], "")


# ---------------------------------------------------------------------------- configure-only build of a test profile
# TOOL-245: the target compile probes (bspPinEvent, module binders) need the compiler lines and the generat of a real
# build, not its objects. A project of the tree - also a test profile without a preset of its own - is configured
# with the preset of an open project and NX_PROJECT pointing to it; compile_commands.json and the generat of bswgen
# and rtegen are written at configure time. Exported with the open tree, unlike the private eval board project.
OPEN_PRESET = "buildtest-s32k148-min"


def configure_project(repo: Path, project: Path, binary_dir: Path, python: str, preset: str = OPEN_PRESET):
    """Configure `project` into `binary_dir` (no build); the subprocess.CompletedProcess of `cmake --preset`."""
    import subprocess
    env = {**os.environ, "PYTHONUTF8": "1", "PATH": compiler_search_path()}
    env.setdefault("CC", "arm-none-eabi-gcc")
    env.setdefault("CXX", "arm-none-eabi-g++")
    fresh, _ = fresh_args(binary_dir, env["PATH"])
    return subprocess.run(["cmake", "--preset", preset, *fresh, "-B", str(binary_dir),
                           f"-DNX_PROJECT={Path(project).resolve().as_posix()}", f"-DPython3_EXECUTABLE={python}"],
                          cwd=str(repo), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
