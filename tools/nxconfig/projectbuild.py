# SPDX-License-Identifier: Apache-2.0
"""Target build of a project copy for the build checks of the tree (PLAT-114).

Several checks (libs/bsw/sbcUja113x/test/intn_build.py, tools/bswgen/test/pin_event_build.py, ...) copy a project
into a temporary folder, change one aspect, check it with nxconfig and build it the way the Studio builds: the base
preset `nx-arm-gcc` plus -DNX_PROJECT, then `cmake --build`. They used to borrow the Studio's project and command
classes for that; the Studio is not distributed (decision F12), so an open check must not import it. This module is
the open form of exactly these steps, and the Studio takes the configure arguments from the same place
(nxconfig/presets.py, studio/core/commands.py).

What it does not do: build profiles and options of a workstation, the tool cache of the Studio (toolenv) and
presets of a project. arm-none-eabi-gcc comes from PATH with NX_ARM_GCC_BIN in front of it, as in every preset and
in tools/test.

    build = ProjectBuild(project_file, build_dir, repo=REPO)
    if not build.errors:
        ok = all(run(step, log) for step in build.steps())
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import check
from .presets import BASE_PRESET, configure_args, preset_settings

#: Build configuration of the checks (the default of the presets, CMAKE_DEFAULT_BUILD_TYPE)
BUILD_CONFIG = "RelWithDebInfo"


@dataclass
class Step:
    """One command of a build: what runs, where, with which variables on top of the process environment."""

    title: str
    argv: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)

    def command_line(self) -> str:
        line = subprocess.list2cmdline(self.argv) if os.name == "nt" else shlex.join(self.argv)
        if not self.env:
            return line
        if os.name == "nt":
            return " && ".join([f'set "{k}={v}"' for k, v in self.env.items()] + [line])
        return " ".join(f"{k}={shlex.quote(v)}" for k, v in self.env.items()) + " " + line

    def environment(self, base: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ if base is None else base)
        env.update(self.env)
        return env


class ProjectBuild:
    """A project checked by nxconfig and the commands that build it in `build_dir`.

    `repo` is the neXtBSW tree (CMake source directory, mcudb, module folders); `python` the interpreter CMake runs
    the generators with (default: this one).
    """

    def __init__(self, project_file: Path, build_dir: Path, repo: Path, python: str | None = None,
                 build_config: str = BUILD_CONFIG):
        self.file = Path(project_file).resolve()
        self.build_dir = Path(build_dir)
        self.repo = Path(repo)
        self.python = python or sys.executable
        self.build_config = build_config
        self.config, self.messages = check(self.file, self.repo / "mcudb",
                                           (self.repo / "libs" / "bsw", self.repo / "libs" / "bsp"),
                                           running_tree=self.repo)

    @property
    def errors(self) -> list:
        return [m for m in self.messages if m.severity == "error"]

    def configure(self) -> Step:
        base = preset_settings(self.repo, BASE_PRESET, self.build_dir.name)
        args = configure_args(base, self.repo, self.build_dir, self.file, self.python)
        return Step("CMake konfigurieren", [shutil.which("cmake") or "cmake", *args], self.repo, base.environment)

    def build(self, env: dict[str, str] | None = None) -> Step:
        return Step("Bauen", [shutil.which("cmake") or "cmake", "--build", str(self.build_dir),
                              "--config", self.build_config], self.repo, dict(env or {}))

    def steps(self) -> list[Step]:
        """Configure (always: the checks start from an empty build directory) and build, same environment."""
        configure = self.configure()
        return [configure, self.build(configure.env)]


def tool(name: str) -> str:
    """A tool of the ARM toolchain: NX_ARM_GCC_BIN first, then PATH, else the bare name."""
    folder = os.environ.get("NX_ARM_GCC_BIN")
    if folder:
        for candidate in (Path(folder) / f"{name}.exe", Path(folder) / name):
            if candidate.is_file():
                return str(candidate)
    return shutil.which(name) or name


def run(step: Step, log: list[str]) -> bool:
    """Run one step, keep its output in `log`, print the tail on failure."""
    print("$", step.command_line(), flush=True)
    env = step.environment()
    env["PATH"] = os.pathsep.join(p for p in (env.get("NX_ARM_GCC_BIN"), env.get("PATH")) if p)
    result = subprocess.run(step.argv, cwd=step.cwd, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    output = result.stdout + result.stderr
    log.append(output)
    if result.returncode != 0:
        print(output[-6000:])
        print(f"FAILED ({result.returncode}): {step.title}")
        return False
    return True
