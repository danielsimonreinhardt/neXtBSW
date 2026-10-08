# SPDX-License-Identifier: Apache-2.0
"""Reads the generic base preset from CMakePresets.json, so a project is configured with -DNX_PROJECT and the same
settings as every project preset, without knowing a project preset name.

PLAT-114: moved here from tools/studio/core/presets.py. The configure call of a project build is shared by the Studio
(studio/core/commands.py) and the open target build checks (nxconfig/projectbuild.py); the open side must not import
the Studio, which is not distributed (decision F12), so the Studio re-exports this module instead.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

BASE_PRESET = "nx-arm-gcc"                  # hidden base of all project presets (projects/README.md)


@dataclass
class PresetSettings:
    generator: str = "Ninja Multi-Config"
    cache: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)   # already expanded against the process environment
    environment_display: dict[str, str] = field(default_factory=dict)   # $penv{X} as %X% / $X (command line text)
    found: bool = False


def _load(repo: Path, seen: set[Path] | None = None) -> list[dict]:
    """The configure presets of CMakePresets.json **and of the files it includes**.

    ARCH-57: the base presets (nx-arm-gcc, nx-worktree) live in cmake/NxPresets.json, which the root preset file
    and the preset file of a workspace outside the tree include - one source for both.
    """
    file = (repo / "CMakePresets.json") if repo.is_dir() else Path(repo)
    file = file.resolve()
    seen = set() if seen is None else seen
    if file in seen:
        return []
    seen.add(file)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = [p for p in data.get("configurePresets", []) if isinstance(p, dict)]
    for include in data.get("include", []):
        if isinstance(include, str):
            found += _load(file.parent / include, seen)
    return found


def _expand(value: str, repo: Path, preset_name: str, env: dict[str, str], penv: dict[str, str]) -> str:
    macros = {"sourceDir": repo.as_posix(), "sourceDirName": repo.name, "presetName": preset_name,
              "pathListSep": os.pathsep, "dollar": "$", "hostSystemName": _host_system()}
    value = re.sub(r"\$\{(\w+)\}", lambda m: macros.get(m.group(1), m.group(0)), value)
    value = re.sub(r"\$penv\{(\w+)\}", lambda m: penv.get(m.group(1), ""), value)
    return re.sub(r"\$env\{(\w+)\}", lambda m: env.get(m.group(1), penv.get(m.group(1), "")), value)


def _penv_names(environment: dict[str, str]) -> set[str]:
    return {m for v in environment.values() for m in re.findall(r"\$p?env\{(\w+)\}", v)}


def _host_system() -> str:
    import platform
    return {"Windows": "Windows", "Darwin": "Darwin"}.get(platform.system(), "Linux")


def _chain(presets: dict[str, dict], name: str, seen=()) -> list[dict]:
    """Preset and its ancestors, nearest first (CMake: earlier inherits win)."""
    preset = presets.get(name)
    if preset is None or name in seen:
        return []
    inherits = preset.get("inherits", [])
    inherits = [inherits] if isinstance(inherits, str) else inherits
    result = [preset]
    for parent in inherits:
        result += _chain(presets, parent, seen + (name,))
    return result


def preset_settings(repo: Path, name: str = BASE_PRESET, preset_name: str = "",
                    penv: dict[str, str] | None = None) -> PresetSettings:
    presets = {p.get("name"): p for p in _load(repo)}
    chain = _chain(presets, name)
    if not chain:
        return PresetSettings()
    penv = dict(os.environ if penv is None else penv)
    out = PresetSettings(found=True)
    for preset in reversed(chain):          # ancestors first, nearer presets override
        if "generator" in preset:
            out.generator = preset["generator"]
        for key, value in (preset.get("cacheVariables") or {}).items():
            if isinstance(value, dict):
                value = value.get("value", "")
            out.cache[key] = str(value)
        for key, value in (preset.get("environment") or {}).items():
            if value is not None:
                out.environment[key] = str(value)
    preset_name = preset_name or name
    placeholders = {k: (f"%{k}%" if os.name == "nt" else f"${k}") for k in set(penv) | _penv_names(out.environment)}
    out.environment_display = {k: _expand(v, repo, preset_name, {}, placeholders) for k, v in out.environment.items()}
    out.environment = {k: _expand(v, repo, preset_name, out.environment, penv) for k, v in out.environment.items()}
    out.cache = {k: _expand(v, repo, preset_name, out.environment, penv) for k, v in out.cache.items()}
    return out


def visible_presets(repo: Path) -> list[str]:
    return [p["name"] for p in _load(repo) if p.get("name") and not p.get("hidden")]


def presets_for_project(repo: Path, project_file: Path) -> list[str]:
    """Visible presets whose NX_PROJECT selects `project_file` (optional use in the Studio)."""
    result, project_file = [], Path(project_file).resolve()
    for name in visible_presets(repo):
        value = preset_settings(repo, name).cache.get("NX_PROJECT", "")
        if not value:
            continue
        path = Path(value)
        path = (path if path.is_absolute() else repo / path).resolve()
        if path.is_dir():
            path = path / "project.yaml"
        if path == project_file:
            result.append(name)
    return result


#: Cache values the configure call always sets itself (the project and the interpreter of the generators)
OWN_CACHE = ("NX_PROJECT", "Python3_EXECUTABLE")


def configure_args(base: PresetSettings, source_dir: Path, build_dir: Path, project_file: Path, python: str,
                   extra: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Arguments of `cmake` that configure `project_file` in `build_dir` with the base preset `base`:
    `-S/-B/-G`, the cache values of the preset, then -DNX_PROJECT, -DPython3_EXECUTABLE and `extra`."""
    cache = {k: v for k, v in base.cache.items() if k not in OWN_CACHE}
    args = ["-S", str(source_dir), "-B", str(build_dir), "-G", base.generator]
    args += [f"-D{k}={v}" for k, v in cache.items()]
    args += [f"-DNX_PROJECT={Path(project_file).as_posix()}", f"-DPython3_EXECUTABLE={Path(python).as_posix()}"]
    return args + list(extra)
