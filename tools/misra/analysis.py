# SPDX-License-Identifier: Apache-2.0
"""Core of the static analysis of neXtBSW (PLAT-74): MISRA C:2012 via the cppcheck add-on, clang-tidy for C++.

No Qt, no CI provider. The command line is ``python -m tools.misra`` (``__main__.py``); the CI stage calls it.

Flow
----
1. **Scope.** The compile database of a target build (``compile_commands.json`` of the demo project) is the only
   source of translation units, include paths and defines. Every file is classified:

   ``own``       neXtBSW code - analysed, gating
   ``copy``      an OpenBSW copy (``identical``/``modified`` in ``tools/openbswsync/copies.yaml``) - analysed and
                 reported separately, not gating (the list lives there only, SYS-19 section 10.4)
                 Exception (SYS-62): a ``modified`` copy matching ``scope.gating_copies`` in ``rules.yaml`` (the
                 ASIL B modules of doc/safety/coexistence.md) is own development and classified ``own`` - gating
   ``external``  below the submodule ``external/`` - not analysed
   ``excluded``  generated code, the Simulink output, host tests (``scope.exclude`` in ``rules.yaml``)
   ``outside``   not in the repository (build directory: generated board and RTE code). A build directory
                 inside the repository (``build/ci`` of tools/ci, ``build/`` of tools/test) counts as outside as
                 well: the directory of the compile database is never source (REL-020)

2. **Tools.** cppcheck (core checks + MISRA add-on) and clang-tidy, both from pip wheels (``requirements.txt``).
   A missing tool is skipped with a message - never reported as clean.
3. **Verdict per finding** (own code): ``disapplied`` (the rule is not applied in this project - guideline
   re-categorization plan in ``rules.yaml``, PLAT-76), ``notice`` (tool message, ``rules.yaml``), ``deviated``
   (justified entry in ``deviations.yaml``), ``baseline`` (known from the inventory, ``baseline.yaml``) or ``new``.
   A single ``new`` finding fails the run. Findings of a disapplied rule are neither new nor part of the baseline;
   the report counts them separately.

Findings are matched without line numbers: file + rule + the stripped source line, counted as a multiset, so that
moving code does not produce new findings while a second copy of the same violation does.
"""

from __future__ import annotations

import collections
import datetime as _dt
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RULES_FILE = HERE / "rules.yaml"
DEVIATIONS_FILE = HERE / "deviations.yaml"
BASELINE_FILE = HERE / "baseline.yaml"
DEFECTS_FILE = HERE / "defects.yaml"
REQUIREMENTS_FILE = HERE / "requirements.txt"
COPIES_FILE = REPO / "tools" / "openbswsync" / "copies.yaml"
ENV_TOOLS = "NX_MISRA_TOOLS"      # directory holding cppcheck/clang-tidy executables
ENV_VENV = "NX_MISRA_VENV"        # venv created by `python -m tools.misra setup`
TOOLS = ("cppcheck", "clang-tidy")
SCOPES = ("own", "copy")
VERDICTS = ("new", "baseline", "deviated", "notice", "disapplied")
# MISRA Compliance:2020, guideline re-categorization plan: categories in ascending strictness. A rule may only be
# made stricter - except that an advisory rule may be disapplied (not applied at all).
MISRA_CATEGORIES = ("advisory", "required", "mandatory")
REVISED_CATEGORIES = ("disapplied", *MISRA_CATEGORIES)
EXE = ".exe" if os.name == "nt" else ""

EXIT_OK, EXIT_NEW, EXIT_CONFIG, EXIT_SKIPPED = 0, 1, 2, 3


class MisraError(Exception):
    """Configuration or usage error: the run cannot say anything."""


# ------------------------------------------------------------------------------------------------ data


@dataclass
class Message:
    severity: str        # error | warning
    where: str           # file plus YAML path
    text: str

    def __str__(self) -> str:
        return f"{self.where}: {'FEHLER' if self.severity == 'error' else 'WARNUNG'} {self.text}"


@dataclass
class Unit:
    file: str            # repository path (posix)
    path: Path
    language: str        # c | c++
    compiler: str
    arch: list[str]
    defines: list[str]
    undefines: list[str]
    includes: list[str]
    system_includes: list[str]
    force_includes: list[str]
    std: str | None
    scope: str
    module: str
    flags: list[str] = field(default_factory=list)


@dataclass
class Finding:
    tool: str
    rule: str
    file: str            # repository path (posix)
    line: int
    column: int
    severity: str
    message: str
    text: str = ""       # stripped source line (the location key, not the line number)
    scope: str = "own"
    module: str = ""
    verdict: str = ""
    deviation: str = ""
    unit: str = ""       # translation unit that produced it (decides C or C++ for a shared header)
    defect: str = ""     # BEF-nnn of defects.yaml: a real fault, listed separately

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.file, self.rule, self.text)

    @property
    def in_c(self) -> bool:
        return (self.unit or self.file).lower().endswith(".c")


@dataclass
class Result:
    findings: list[Finding] = field(default_factory=list)
    resolved: list[dict] = field(default_factory=list)       # baseline entries no longer found
    messages: list[Message] = field(default_factory=list)
    skipped_tools: dict[str, str] = field(default_factory=dict)
    tool_versions: dict[str, str] = field(default_factory=dict)
    scope: dict = field(default_factory=dict)
    deviations: list[dict] = field(default_factory=list)      # id, matched, approved
    defects: list[dict] = field(default_factory=list)

    def of(self, scope: str | None = None, verdict: str | None = None) -> list[Finding]:
        return [f for f in self.findings
                if (scope is None or f.scope == scope) and (verdict is None or f.verdict == verdict)]

    @property
    def new(self) -> list[Finding]:
        return self.of("own", "new")


# ------------------------------------------------------------------------------------------------ yaml


def read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise MisraError(f"{path}: Datei fehlt")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise MisraError(f"{path}: kein gueltiges YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise MisraError(f"{path}: erwartet eine YAML-Abbildung")
    return data


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return path.as_posix()


def validate_rules(data: dict, file: Path = RULES_FILE) -> list[Message]:
    out: list[Message] = []
    where = _rel(file)

    def err(path: str, text: str, severity: str = "error") -> None:
        out.append(Message(severity, f"{where} ({path})", text))

    if data.get("version") != 1:
        err("version", "erwartet version: 1")
    scope = data.get("scope") or {}
    if not isinstance(scope.get("exclude", []), list):
        err("scope.exclude", "erwartet eine Liste von Mustern")
    gating = scope.get("gating_copies", [])
    if not isinstance(gating, list) or not all(isinstance(g, str) and g.strip() for g in gating):
        err("scope.gating_copies", "erwartet eine Liste von Mustern (Pfade der geaenderten Kopien im Sicherheitspfad)")
    cpp = data.get("cppcheck") or {}
    if not isinstance(cpp.get("args", []), list):
        err("cppcheck.args", "erwartet eine Liste")
    tidy = data.get("clang_tidy") or {}
    if not isinstance(tidy.get("checks", []), list) or not tidy.get("checks"):
        err("clang_tidy.checks", "erwartet eine nicht leere Liste von Pruefungen")
    for name in ("notices", "notices_cpp"):
        if not isinstance(data.get(name, []), list):
            err(name, "erwartet eine Liste von Regel-Kennungen")
    rules = data.get("rules") or {}
    if not isinstance(rules, dict):
        err("rules", "erwartet eine Abbildung Regel -> Beschreibung")
        rules = {}
    for rule, info in rules.items():
        if not isinstance(info, dict) or not str(info.get("summary", "")).strip():
            err(f"rules.{rule}.summary", "Kurzbeschreibung fehlt (eigene Worte, kein Regeltext)")
            continue
        category = info.get("category")
        if category not in ("mandatory", "required", "advisory", "check", "tool"):
            err(f"rules.{rule}.category", f"unbekannte Kategorie {category!r} "
                                          "(mandatory, required, advisory, check, tool)")
            continue
        if "revised_category" in info:
            _validate_revision(str(rule), info, category, err)
    return out


def _validate_revision(rule: str, info: dict, category: str, err) -> None:
    """Guideline re-categorization plan (MISRA Compliance:2020): stricter only, advisory may be disapplied."""
    p = f"rules.{rule}"
    revised = info.get("revised_category")
    if category not in MISRA_CATEGORIES:
        err(f"{p}.revised_category", f"nur MISRA-Regeln (mandatory, required, advisory) koennen neu eingestuft "
                                     f"werden, nicht Kategorie {category!r}")
        return
    if revised not in REVISED_CATEGORIES:
        err(f"{p}.revised_category", f"unbekannte Einstufung {revised!r} ({', '.join(REVISED_CATEGORIES)})")
        return
    if revised == "disapplied":
        if category != "advisory":
            err(f"{p}.revised_category", f"nur advisory-Regeln duerfen disapplied werden (MISRA Compliance:2020), "
                                         f"diese Regel ist {category}")
            return
    elif MISRA_CATEGORIES.index(revised) < MISRA_CATEGORIES.index(category):
        err(f"{p}.revised_category", f"{category} darf nicht zu {revised} abgeschwaecht werden - nur strenger "
                                     f"(oder advisory -> disapplied)")
        return
    if revised == category:
        return
    if not str(info.get("reason", "") or "").strip():
        err(f"{p}.reason", "Begruendung der Neueinstufung fehlt (eigene Worte, kein Regeltext)")
    if info.get("date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(info["date"])):
        err(f"{p}.date", "Datum als JJJJ-MM-TT")
    if not str(info.get("approved_by", "") or "").strip():
        err(f"{p}.approved_by", "Neueinstufung noch nicht freigegeben - Freigabe durch den Nutzer offen", "warning")


def disapplied_rules(rules: dict) -> set[str]:
    """Rules whose revised category is `disapplied` (checked by validate_rules: advisory only)."""
    return {str(rule) for rule, info in (rules.get("rules") or {}).items()
            if isinstance(info, dict) and info.get("revised_category") == "disapplied"
            and info.get("category") == "advisory"}


def validate_deviations(data: dict, file: Path = DEVIATIONS_FILE) -> list[Message]:
    out: list[Message] = []
    where = _rel(file)

    def err(path: str, text: str, severity: str = "error") -> None:
        out.append(Message(severity, f"{where} ({path})", text))

    if data.get("version") != 1:
        err("version", "erwartet version: 1")
    entries = data.get("deviations") or []
    if not isinstance(entries, list):
        err("deviations", "erwartet eine Liste")
        return out
    seen: set[str] = set()
    for i, dev in enumerate(entries):
        p = f"deviations[{i}]"
        if not isinstance(dev, dict):
            err(p, "erwartet eine Abbildung")
            continue
        dev_id = str(dev.get("id", ""))
        if not re.fullmatch(r"DEV-\d{3}", dev_id):
            err(f"{p}.id", "Kennung DEV-nnn fehlt oder ist ungueltig")
        elif dev_id in seen:
            err(f"{p}.id", f"{dev_id} doppelt")
        seen.add(dev_id)
        rules = dev.get("rule")
        if not rules or not (isinstance(rules, str) or
                             (isinstance(rules, list) and all(isinstance(r, str) for r in rules))):
            err(f"{p}.rule", "Regel-Kennung (oder Liste) fehlt")
        files = dev.get("files")
        if not files or not isinstance(files, list):
            err(f"{p}.files", "Liste von Dateien/Mustern fehlt")
        if not dev.get("pattern") and not dev.get("symbol"):
            err(p, "Ort nur ueber Datei reicht nicht: pattern (Quellzeile) oder symbol angeben")
        if dev.get("pattern"):
            try:
                re.compile(dev["pattern"])
            except re.error as exc:
                err(f"{p}.pattern", f"kein gueltiger regulaerer Ausdruck: {exc}")
        for key in ("reason", "raised_by", "date"):
            if not str(dev.get(key, "") or "").strip():
                err(f"{p}.{key}", "Pflichtfeld fehlt (Begruendung, Wer, Wann)")
        if dev.get("date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(dev["date"])):
            err(f"{p}.date", "Datum als JJJJ-MM-TT")
        if "limit" in dev and not (isinstance(dev["limit"], int) and dev["limit"] > 0):
            err(f"{p}.limit", "positive ganze Zahl erwartet")
        if dev.get("kind", "deviation") not in ("deviation", "false-positive"):
            err(f"{p}.kind", "deviation oder false-positive")
    pending = [str(d.get("id")) for d in entries if isinstance(d, dict) and not d.get("approved_by")]
    if pending:
        err("deviations[*].approved_by", f"{len(pending)} Abweichungen noch nicht freigegeben "
                                         f"({', '.join(pending)}) - Freigabe durch den Nutzer offen", "warning")
    return out


def validate_defects(data: dict, file: Path) -> list[Message]:
    out: list[Message] = []
    where = _rel(file)
    if data.get("version") != 1:
        out.append(Message("error", f"{where} (version)", "erwartet version: 1"))
    entries = data.get("defects") or []
    if not isinstance(entries, list):
        return out + [Message("error", f"{where} (defects)", "erwartet eine Liste")]
    for i, d in enumerate(entries):
        p = f"defects[{i}]"
        if not isinstance(d, dict):
            out.append(Message("error", f"{where} ({p})", "erwartet eine Abbildung"))
            continue
        if not re.fullmatch(r"BEF-\d{3}", str(d.get("id", ""))):
            out.append(Message("error", f"{where} ({p}.id)", "Kennung BEF-nnn fehlt oder ist ungueltig"))
        for key in ("rule", "files", "note", "owner", "raised_by", "date"):
            if not d.get(key):
                out.append(Message("error", f"{where} ({p}.{key})", "Pflichtfeld fehlt"))
        if not d.get("pattern") and not d.get("symbol"):
            out.append(Message("error", f"{where} ({p})", "pattern oder symbol angeben"))
    return out


def validate_baseline(data: dict, file: Path = BASELINE_FILE, disapplied: set[str] | frozenset = frozenset()
                      ) -> list[Message]:
    out: list[Message] = []
    where = _rel(file)
    if data.get("version") != 1:
        out.append(Message("error", f"{where} (version)", "erwartet version: 1"))
    entries = data.get("entries") or {}
    if not isinstance(entries, dict):
        out.append(Message("error", f"{where} (entries)", "erwartet eine Abbildung Datei -> Liste"))
        return out
    stale: dict[str, list[str]] = {}
    for path, items in entries.items():
        if not isinstance(items, list):
            out.append(Message("error", f"{where} (entries.{path})", "erwartet eine Liste"))
            continue
        for i, item in enumerate(items):
            if not isinstance(item, dict) or not item.get("rule") or "text" not in item:
                out.append(Message("error", f"{where} (entries.{path}[{i}])", "rule und text sind Pflicht"))
            elif not isinstance(item.get("count", 1), int) or item.get("count", 1) < 1:
                out.append(Message("error", f"{where} (entries.{path}[{i}].count)", "positive ganze Zahl"))
            elif item["rule"] in disapplied:
                stale.setdefault(item["rule"], []).append(f"entries.{path}[{i}].rule")
    for rule, paths in sorted(stale.items()):
        # one message per rule (the first place as YAML path), not one per entry
        more = f" und {len(paths) - 1} weitere Eintraege" if len(paths) > 1 else ""
        out.append(Message("warning", f"{where} ({paths[0]}{more})",
                           f"{rule} ist disapplied (rules.yaml) und gehoert nicht in die Baseline - "
                           f"`python -m tools.misra baseline` schreibt sie neu"))
    return out


# ------------------------------------------------------------------------------------------------ scope


def glob_match(rel: str, patterns: list[str]) -> bool:
    """fnmatch with ``**`` crossing directories and ``*`` staying inside one."""
    for pattern in patterns:
        rx = ""
        i = 0
        while i < len(pattern):
            if pattern.startswith("**/", i):
                rx += "(?:.*/)?"
                i += 3
            elif pattern.startswith("**", i):
                rx += ".*"
                i += 2
            elif pattern[i] == "*":
                rx += "[^/]*"
                i += 1
            elif pattern[i] == "?":
                rx += "[^/]"
                i += 1
            else:
                rx += re.escape(pattern[i])
                i += 1
        if re.fullmatch(rx, rel):
            return True
    return False


class Classifier:
    """own / copy / external / excluded / outside - copies come from tools/openbswsync/copies.yaml only.

    A ``modified`` copy that matches one of ``gating_copies`` is own development (SYS-62): scope ``own``, listed in
    ``gated``. Identical copies stay ``copy`` whatever the patterns say - they are pre-existing software."""

    def __init__(self, repo: Path, exclude: list[str], copies_file: Path | None,
                 gating_copies: list[str] | None = None, build_dirs: list[Path] | None = None):
        self.repo = repo.resolve()
        # REL-020: a build directory below the repository (tools/ci builds into build/ci) holds generated code only -
        # board and RTE generat, the updater's upd_blob.c of every configuration - and is classified ``outside``
        # like a build directory elsewhere: read as context if the file exists, never judged
        self.build_dirs = [os.path.normcase(os.path.normpath(str(Path(d).resolve()))) for d in build_dirs or []]
        self.exclude = exclude
        self.copies: set[str] = set()
        self.gated: set[str] = set()
        self.submodule = "external"
        if copies_file is not None and copies_file.is_file():
            sys.path.insert(0, str(REPO))
            from tools.openbswsync.sync import load_list

            copy_list = load_list(copies_file)
            self.submodule = copy_list.submodule or "external"
            for root in copy_list.roots:
                for key, entry in root.entries.items():
                    if entry.status in ("identical", "modified") and not entry.pattern:
                        rel = f"{root.path}/{key}"
                        if entry.status == "modified" and glob_match(rel, list(gating_copies or [])):
                            self.gated.add(rel)
                        else:
                            self.copies.add(rel)
        self._module_cache: dict[str, str] = {}

    def relative(self, path: str | Path) -> str | None:
        p = Path(path)
        if not p.is_absolute():
            p = self.repo / p
        norm = os.path.normcase(os.path.normpath(str(p)))
        base = os.path.normcase(str(self.repo))
        if norm == base or not norm.startswith(base + os.sep):
            return None
        if any(norm == d or norm.startswith(d + os.sep) for d in self.build_dirs):
            return None
        real = os.path.normpath(str(p))[len(str(self.repo)) + 1:]
        return real.replace(os.sep, "/")

    def scope(self, rel: str | None) -> str:
        if rel is None:
            return "outside"
        if rel.startswith("external/") or rel.startswith(self.submodule + "/"):
            return "external"
        if glob_match(rel, self.exclude):
            return "excluded"
        if rel in self.copies:
            return "copy"
        return "own"

    def module(self, rel: str) -> str:
        """Nearest directory with a module manifest (module.yaml), else a path prefix."""
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if parent in self._module_cache:
            return self._module_cache[parent]
        parts = parent.split("/") if parent else []
        found = None
        for n in range(len(parts), 0, -1):
            d = self.repo.joinpath(*parts[:n])
            if (d / "module.yaml").is_file() or (d / "module.yaml.pending").is_file():
                found = "/".join(parts[:n])
                break
        if found is None:
            depth = 3 if parts[:1] == ["platforms"] or parts[:1] == ["libs"] else 2
            found = "/".join(parts[:depth]) or "(root)"
        self._module_cache[parent] = found
        return found


def split_command(command: str) -> list[str]:
    """Split a compile_commands `command` like the compiler's C runtime does (Windows) or a POSIX shell."""
    return split_windows(command) if os.name == "nt" else shlex.split(command)


def split_windows(command: str) -> list[str]:
    """CommandLineToArgvW rules: backslashes are literal unless they precede a quote (Ninja on Windows writes
    `C:\\NXP\\...\\gcc.exe -DX=\\"1\\" -I"C:/a b"`)."""
    args: list[str] = []
    cur: list[str] = []
    in_quotes = False
    have = False
    i = 0
    while i < len(command):
        c = command[i]
        if c == "\\":
            n = 0
            while i < len(command) and command[i] == "\\":
                n += 1
                i += 1
            if i < len(command) and command[i] == '"':
                cur.append("\\" * (n // 2))
                if n % 2:
                    cur.append('"')
                    i += 1
                have = True
                continue
            cur.append("\\" * n)
            have = True
            continue
        if c == '"':
            in_quotes = not in_quotes
            have = True
        elif c in " \t" and not in_quotes:
            if have:
                args.append("".join(cur))
                cur, have = [], False
        else:
            cur.append(c)
            have = True
        i += 1
    if have:
        args.append("".join(cur))
    return args


ARCH_PREFIXES = ("-mcpu=", "-march=", "-mfpu=", "-mfloat-abi=", "-mthumb", "-marm", "-mlittle-endian",
                 "-mbig-endian", "-mabi=")
# flags that change the language the code is written in (clang-tidy needs them, cppcheck ignores them)
LANGUAGE_FLAGS = ("-fno-exceptions", "-fno-rtti", "-funsigned-char", "-fsigned-char", "-fshort-enums",
                  "-fshort-wchar")


def parse_entry(entry: dict) -> dict:
    args = entry.get("arguments") or split_command(entry["command"])
    directory = Path(entry.get("directory", "."))
    out = {"compiler": args[0], "arch": [], "defines": [], "undefines": [], "includes": [],
           "system_includes": [], "force_includes": [], "std": None, "language": None, "flags": []}

    def absolute(p: str) -> str:
        return str((directory / p).resolve()) if not Path(p).is_absolute() else p

    i = 1
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a.startswith("-D"):
            value = a[2:] or nxt
            if not a[2:]:
                i += 1
            out["defines"].append(value)
        elif a.startswith("-U"):
            out["undefines"].append(a[2:] or nxt)
            if not a[2:]:
                i += 1
        elif a == "-isystem" and nxt:
            out["system_includes"].append(absolute(nxt))
            i += 1
        elif a.startswith("-isystem"):
            out["system_includes"].append(absolute(a[8:]))
        elif a == "-I" and nxt:
            out["includes"].append(absolute(nxt))
            i += 1
        elif a.startswith("-I"):
            out["includes"].append(absolute(a[2:]))
        elif a == "-include" and nxt:
            out["force_includes"].append(absolute(nxt))
            i += 1
        elif a.startswith("-std="):
            out["std"] = a[5:]
        elif a == "-x" and nxt:
            out["language"] = "c++" if "c++" in nxt else "c"
            i += 1
        elif a.startswith(ARCH_PREFIXES):
            out["arch"].append(a)
        elif a in LANGUAGE_FLAGS:
            out["flags"].append(a)
        i += 1
    if out["language"] is None:
        suffix = Path(entry["file"]).suffix.lower()
        out["language"] = "c" if suffix == ".c" else "c++" if suffix in (".cpp", ".cc", ".cxx", ".c++") else None
    return out


CONFIG_DIR = re.compile(r"[\\/](Debug|Release|MinSizeRel|RelWithDebInfo)[\\/]")


def select_units(compile_db: Path, classifier: Classifier, prefer: str = "",
                 context: tuple[str, ...] = ()) -> tuple[list[Unit], dict]:
    """Translation units of own code and OpenBSW copies from a compile database, one entry per file.

    `context` scopes (e.g. the generated RTE code in the build directory) are analysed as well, so that cppcheck
    sees the callers of a library function; their findings are dropped later."""
    try:
        entries = json.loads(compile_db.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MisraError(f"Compile-Datenbank nicht lesbar: {compile_db}: {exc}") from exc
    chosen: dict[str, tuple[dict, dict]] = {}
    stats: dict = {"compile_db": str(compile_db), "entries": len(entries),
                   "files": collections.Counter()}
    seen_files: set[str] = set()
    for entry in entries:
        path = Path(entry.get("directory", ".")) / entry["file"]
        rel = classifier.relative(path)
        parsed = parse_entry(entry)
        key = rel or os.path.normcase(str(path))
        first = key not in seen_files
        seen_files.add(key)
        scope = classifier.scope(rel)
        if parsed["language"] is None:
            if first:
                stats["files"]["assembler/other"] += 1
            continue
        if first:
            stats["files"][scope] += 1
        if scope not in SCOPES and scope not in context:
            continue
        if scope not in SCOPES:
            # context: generated files exist only for the configuration that was built
            if not path.is_file() or (prefer and CONFIG_DIR.search(str(path)) and prefer not in str(path)):
                continue
        previous = chosen.get(key)
        output = entry.get("output", "") + " " + entry.get("command", "")
        if previous is None or (prefer and prefer in output and prefer not in previous[1]["output"]):
            chosen[key] = (parsed, {"output": output, "path": path, "scope": scope})
    units = []
    for key, (p, extra) in sorted(chosen.items()):
        path = classifier.repo / key if extra["scope"] in SCOPES else Path(os.path.normpath(extra["path"]))
        units.append(Unit(file=key, path=path, language=p["language"], compiler=p["compiler"],
                          arch=p["arch"], defines=p["defines"], undefines=p["undefines"], includes=p["includes"],
                          system_includes=p["system_includes"], force_includes=p["force_includes"], std=p["std"],
                          scope=extra["scope"], module=classifier.module(key) if extra["scope"] in SCOPES else "",
                          flags=p["flags"]))
    stats["files"] = dict(stats["files"])
    stats["units"] = {s: sum(1 for u in units if u.scope == s) for s in (*SCOPES, *context)}
    # SYS-62: modified copies in the safety path, analysed as own code (units and headers, by file)
    stats["gated_copies"] = sorted(classifier.gated)
    stats["gated_units"] = sum(1 for u in units if u.file in classifier.gated)
    stats["languages"] = dict(collections.Counter(f"{u.scope}:{u.language}" for u in units))
    return units, stats


def uncovered_sources(classifier: Classifier, units: list[Unit], roots: list[str]) -> dict[str, list[str]]:
    """Own C/C++ sources that no translation unit of the compile database compiles (analysis gap), per module."""
    compiled = {u.file for u in units}
    out: dict[str, list[str]] = collections.defaultdict(list)
    for root in roots:
        base = classifier.repo / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix.lower() not in (".c", ".cpp") or not path.is_file():
                continue
            rel = classifier.relative(path)
            if rel is None or rel in compiled or classifier.scope(rel) != "own":
                continue
            out[classifier.module(rel)].append(rel)
    return dict(out)


# ------------------------------------------------------------------------------------------------ tools


def default_venv() -> Path:
    if os.environ.get(ENV_VENV):
        return Path(os.environ[ENV_VENV])
    sys.path.insert(0, str(REPO))
    from tools.ci.ci import default_cache

    return default_cache() / "misra-venv"


def venv_bin(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def find_tool(name: str, tool_dir: Path | None = None) -> tuple[Path | None, list[str]]:
    """Explicit directory, NX_MISRA_TOOLS, the venv of `setup` (NX_MISRA_VENV or the CI cache), then PATH."""
    tried: list[str] = []
    dirs: list[Path] = []
    if tool_dir is not None:
        dirs.append(tool_dir)
    elif os.environ.get(ENV_TOOLS):
        dirs.append(Path(os.environ[ENV_TOOLS]))
    else:
        dirs.append(venv_bin(default_venv()))
    for d in dirs:
        candidate = d / f"{name}{EXE}"
        tried.append(str(candidate))
        if candidate.is_file():
            return candidate, tried
    if tool_dir is None:
        found = shutil.which(name)
        tried.append(f"PATH ({name})")
        if found:
            return Path(found), tried
    return None, tried


def tool_version(exe: Path) -> str:
    try:
        proc = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"? ({exc})"
    text = proc.stdout + proc.stderr
    m = re.search(r"Cppcheck\s+(\S+)", text) or re.search(r"LLVM version\s+(\S+)", text)
    return m.group(1) if m else text.strip().splitlines()[0] if text.strip() else "?"


def pinned_versions() -> dict[str, str]:
    """Package pins of requirements.txt (the one place where the tool versions are fixed)."""
    pins = {}
    if REQUIREMENTS_FILE.is_file():
        for line in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_.-]+)==(\S+)", line)
            if m:
                pins[m.group(1).lower()] = m.group(2)
    return pins


# --------------------------------------------------------------------- compiler facts (predefines, system dirs)


_STD_MACROS = re.compile(r"^(__STDC|__cplusplus|__cpp_|__GXX_|__OBJC|__STRICT_ANSI__|__GNUC_STDC_INLINE__|"
                         r"__GNUG__|__EXCEPTIONS|__DEPRECATED|__GFORTRAN)")


def compiler_predefines(compiler: str, arch: list[str]) -> list[str]:
    """`#define` lines the target compiler sets by itself for these architecture flags (cppcheck does not know
    them: without __ARM_ARCH_7EM__ etc. the MCU headers hit their #error)."""
    try:
        proc = subprocess.run([compiler, *arch, "-dM", "-E", "-x", "c", os.devnull], capture_output=True,
                              text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = []
    for line in proc.stdout.splitlines():
        m = re.match(r"#define (\S+)", line)
        if m and not _STD_MACROS.match(m.group(1)):
            lines.append(line)
    return sorted(lines)


def compiler_system_dirs(compiler: str, arch: list[str], language: str) -> list[str]:
    """System include directories of the target compiler (newlib, libstdc++) for clang-tidy - without the
    compiler's own intrinsic headers (clang brings its own)."""
    cc = compiler
    if language == "c++":
        cc = re.sub(r"gcc(\.exe)?$", lambda m: "g++" + (m.group(1) or ""), compiler)
    try:
        proc = subprocess.run([cc, *arch, "-x", "c++" if language == "c++" else "c", "-E", "-v", "-"],
                              input="", capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return []
    dirs, inside = [], False
    for line in proc.stderr.splitlines():
        if line.startswith("#include <...> search starts here"):
            inside = True
        elif line.startswith("End of search list"):
            break
        elif inside and line.startswith(" "):
            d = os.path.normpath(line.strip())
            if re.search(r"[\\/]lib[\\/]gcc[\\/][^\\/]+[\\/][^\\/]+[\\/]include(-fixed)?$", d):
                continue
            dirs.append(d)
    return dirs


# ------------------------------------------------------------------------------------------------ cppcheck


def _no_blank(path: str, directory: Path) -> str:
    """cppcheck 2.17 splits include paths of a compile database at blanks (the repository lies below
    ".../S32K14x basic software"): such a path is given relative to the entry's directory instead."""
    if " " not in path:
        return path
    try:
        rel = os.path.relpath(path, directory)
    except ValueError:        # other drive
        return path
    return rel.replace(os.sep, "/") if " " not in rel else path


def cppcheck_database(units: list[Unit]) -> list[dict]:
    out = []
    for u in units:
        d = u.path.parent
        # string defines (-DAPP_SOFTWARE_VERSION="0.1.0") keep their quotes only escaped: cppcheck re-splits
        args = [u.compiler, *u.arch, *("-D" + x.replace('"', '\\"') for x in u.defines),
                *(f"-U{x}" for x in u.undefines),
                *(f"-I{_no_blank(p, d)}" for p in u.includes),
                *(a for p in u.system_includes for a in ("-isystem", _no_blank(p, d))),
                *(a for p in u.force_includes for a in ("-include", _no_blank(p, d)))]
        if u.std:
            args.append(f"-std={u.std}")
        args += ["-c", str(u.path)]
        out.append({"directory": str(u.path.parent), "arguments": args, "file": str(u.path)})
    return out


def run_cppcheck(exe: Path, units: list[Unit], cfg: dict, work: Path, jobs: int, log) -> str:
    """One cppcheck run per architecture group (predefines differ); returns the merged XML (version 2)."""
    work.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple, list[Unit]] = collections.defaultdict(list)
    for u in units:
        groups[(u.compiler, tuple(u.arch))].append(u)
    errors = []
    version = ""
    for n, ((compiler, arch), members) in enumerate(sorted(groups.items())):
        predef = work / f"predefines_{n}.h"
        predef.write_text("/* generated by tools/misra: predefines of the target compiler */\n" +
                          "\n".join(compiler_predefines(compiler, list(arch))) + "\n", encoding="utf-8")
        db = work / f"compile_commands_{n}.json"
        db.write_text(json.dumps(cppcheck_database(members), indent=1), encoding="utf-8")
        build_dir = work / f"cppcheck-build-{n}"
        build_dir.mkdir(exist_ok=True)
        xml_out = work / f"cppcheck_{n}.xml"
        argv = [str(exe), f"--project={db}", "--xml", "--xml-version=2", f"--output-file={xml_out}",
                f"--include={predef}", f"--cppcheck-build-dir={build_dir}", f"-j{jobs}", "--quiet",
                *[str(a) for a in cfg.get("args", [])]]
        log("$ " + subprocess.list2cmdline(argv))
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not xml_out.is_file():
            raise MisraError(f"cppcheck endete mit {proc.returncode}: {(proc.stdout + proc.stderr)[-2000:]}")
        root = ET.fromstring(xml_out.read_text(encoding="utf-8", errors="replace"))
        tag = root.find("cppcheck")
        version = tag.get("version", "") if tag is not None else version
        errors.extend(root.iter("error"))
    merged = ET.Element("results", version="2")
    ET.SubElement(merged, "cppcheck", version=version)
    holder = ET.SubElement(merged, "errors")
    for e in errors:
        holder.append(e)
    return ET.tostring(merged, encoding="unicode")


def parse_cppcheck_xml(text: str) -> list[Finding]:
    """Findings of a cppcheck XML report (version 2); the first location is where the finding is."""
    root = ET.fromstring(text)
    out = []
    for e in root.iter("error"):
        loc = e.find("location")
        if loc is None:
            continue   # e.g. the checkers report without a place
        rule = e.get("id", "")
        out.append(Finding(tool="cppcheck", rule=rule, file=loc.get("file", ""), line=int(loc.get("line", 0)),
                           column=int(loc.get("column", 0)), severity=e.get("severity", ""),
                           message=e.get("msg", ""), unit=e.get("file0") or loc.get("file", "")))
    return out


# ------------------------------------------------------------------------------------------------ clang-tidy


TIDY_LINE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+): (?P<sev>warning|error): (?P<msg>.*?)"
                       r"(?: \[(?P<check>[^\]]+)\])?$")


TIDY_UNIT = re.compile(r"^### (?P<unit>.+?) \(exit -?\d+\)$")


def parse_clang_tidy(text: str) -> list[Finding]:
    """clang-tidy output; `### <unit> (exit n)` lines (written by run_clang_tidy) name the translation unit."""
    out = []
    unit = ""
    for line in text.splitlines():
        u = TIDY_UNIT.match(line.rstrip())
        if u:
            unit = u.group("unit")
            continue
        m = TIDY_LINE.match(line.rstrip())
        if not m:
            continue
        checks = (m.group("check") or ("clang-diagnostic-error" if m.group("sev") == "error" else
                                       "clang-diagnostic-warning")).split(",")
        for check in checks:
            out.append(Finding(tool="clang-tidy", rule=check.strip(), file=m.group("file"),
                               line=int(m.group("line")), column=int(m.group("col")), severity=m.group("sev"),
                               message=m.group("msg"), unit=unit))
    return out


def clang_args(u: Unit, cfg: dict, system_dirs: list[str]) -> list[str]:
    args = [f"--target={cfg.get('target', 'arm-none-eabi')}", *u.arch, *u.flags, "-nostdlibinc",
            "-x", "c++" if u.language == "c++" else "c"]
    if u.std:
        args.append(f"-std={u.std}")
    args += [f"-D{d}" for d in u.defines] + [f"-U{d}" for d in u.undefines]
    args += [f"-I{p}" for p in u.includes]
    for p in [*u.system_includes, *system_dirs]:
        args += ["-isystem", p]
    for p in u.force_includes:
        args += ["-include", p]
    args += [str(a) for a in cfg.get("extra_args", [])]
    return args


def run_clang_tidy(exe: Path, units: list[Unit], cfg: dict, work: Path, jobs: int, log) -> str:
    work.mkdir(parents=True, exist_ok=True)
    checks = ",".join(["-*", *cfg.get("checks", [])])
    sysdirs: dict[tuple, list[str]] = {}
    for u in units:
        key = (u.compiler, tuple(u.arch), u.language)
        if key not in sysdirs:
            sysdirs[key] = compiler_system_dirs(u.compiler, list(u.arch), u.language)
    exclude = cfg.get("exclude_header_filter", r".*[/\\]external[/\\].*")

    # explicit configuration: no .clang-tidy of a parent directory (external/openbsw has one) takes part
    config = json.dumps({"Checks": checks, "CheckOptions": [{"key": k, "value": str(v)} for k, v in
                                                            sorted((cfg.get("options") or {}).items())]})

    def one(u: Unit) -> str:
        argv = [str(exe), "--quiet", f"--config={config}", "--header-filter=.*",
                f"--exclude-header-filter={exclude}", str(u.path), "--",
                *clang_args(u, cfg, sysdirs[(u.compiler, tuple(u.arch), u.language)])]
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=cfg.get("timeout_s", 600))
        return f"### {u.file} (exit {proc.returncode})\n{proc.stdout}\n{proc.stderr}\n"

    log(f"$ clang-tidy ueber {len(units)} Uebersetzungseinheiten ({jobs} parallel), Pruefungen: {checks}")
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        chunks = list(pool.map(one, units))
    return "".join(chunks)


# ------------------------------------------------------------------------------------------------ verdicts


class SourceLines:
    def __init__(self, repo: Path):
        self.repo = repo
        self.cache: dict[str, list[str]] = {}

    def text(self, rel: str, line: int) -> str:
        if rel not in self.cache:
            try:
                self.cache[rel] = (self.repo / rel).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                self.cache[rel] = []
        lines = self.cache[rel]
        if 1 <= line <= len(lines):
            return " ".join(lines[line - 1].split())[:200]
        return ""


def locate(findings: list[Finding], classifier: Classifier) -> list[Finding]:
    """Repository-relative paths, scope, module and source line; duplicates (a header seen by several
    translation units) are counted once. Findings outside own code and copies are dropped."""
    sources = SourceLines(classifier.repo)
    out, seen = [], set()
    # a header seen from a C and a C++ unit: the C view counts (the MISRA add-on is a C checker)
    for f in sorted(findings, key=lambda f: not f.in_c):
        rel = classifier.relative(f.file) if f.file else None
        scope = classifier.scope(rel)
        if scope not in SCOPES:
            continue
        ident = (f.tool, f.rule, rel, f.line, f.column, f.message)
        if ident in seen:
            continue
        seen.add(ident)
        f.file, f.scope, f.module = rel, scope, classifier.module(rel)
        f.text = sources.text(rel, f.line)
        out.append(f)
    return sorted(out, key=lambda f: (f.file, f.line, f.column, f.rule))


def deviation_matches(dev: dict, f: Finding) -> bool:
    rules = dev["rule"] if isinstance(dev["rule"], list) else [dev["rule"]]
    if not any(fnmatch.fnmatchcase(f.rule, r) for r in rules):
        return False
    if not glob_match(f.file, dev.get("files", [])):
        return False
    if dev.get("pattern") and not re.search(dev["pattern"], f.text):
        return False
    if dev.get("symbol"):
        word = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(dev["symbol"]) + r"(?![A-Za-z0-9_])")
        if not (word.search(f.text) or word.search(f.message)):
            return False
    return True


def evaluate(findings: list[Finding], rules: dict, deviations: dict, baseline: dict,
             skipped_tools: dict[str, str] | None = None, defects: dict | None = None) -> Result:
    """Assign a verdict to every finding and collect what the baseline no longer needs. `defects` marks
    findings that are real faults (defects.yaml): they keep their verdict and are listed in the report."""
    result = Result(findings=findings, skipped_tools=dict(skipped_tools or {}))
    notices = list(rules.get("notices", []))
    notices_cpp = list(rules.get("notices_cpp", []))
    keep_cpp = list(rules.get("keep_cpp", []))
    disapplied = disapplied_rules(rules)

    def is_notice(f: Finding) -> bool:
        if any(fnmatch.fnmatchcase(f.rule, p) for p in notices):
            return True
        # MISRA C add-on on a C++ unit: it reads C++ syntax as C (template/base lists as comma operators,
        # using-declarations as typedefs) - a tool limit, except for the rules listed in keep_cpp
        return (not f.in_c and any(fnmatch.fnmatchcase(f.rule, p) for p in notices_cpp)
                and not any(fnmatch.fnmatchcase(f.rule, p) for p in keep_cpp))

    devs = deviations.get("deviations") or []
    matched = collections.Counter()
    remaining: collections.Counter = collections.Counter()
    tools_of: dict[tuple, str] = {}
    for path, items in (baseline.get("entries") or {}).items():
        for item in items:
            key = (path, item["rule"], item.get("text", "") or "")
            remaining[key] += int(item.get("count", 1))
            tools_of[key] = item.get("tool", "")
    for f in findings:
        if f.scope != "own":
            f.verdict = "copy"
            continue
        if f.rule in disapplied:
            # not applied in this project (re-categorization plan): neither new nor baseline, counted apart
            f.verdict = "disapplied"
            continue
        if is_notice(f):
            f.verdict = "notice"
            continue
        hit = next((d for d in devs if deviation_matches(d, f)
                    and (not d.get("limit") or matched[d["id"]] < d["limit"])), None)
        if hit is not None:
            matched[hit["id"]] += 1
            f.verdict, f.deviation = "deviated", hit["id"]
            continue
        if remaining[f.key] > 0:
            remaining[f.key] -= 1
            f.verdict = "baseline"
            continue
        f.verdict = "new"
    skipped = set(result.skipped_tools)
    for (path, rule, text), count in sorted(remaining.items()):
        if count > 0 and tools_of.get((path, rule, text), "") not in skipped:
            result.resolved.append({"file": path, "rule": rule, "text": text, "count": count})
    for d in devs:
        result.deviations.append({"id": d["id"], "rule": d["rule"], "matched": matched[d["id"]],
                                  "limit": d.get("limit"), "approved": bool(d.get("approved_by"))})
        if matched[d["id"]] == 0 and not any(t in skipped for t in _tools_of_rule(d["rule"])):
            result.messages.append(Message("warning", f"deviations.yaml ({d['id']})",
                                           "Abweichung trifft keinen Befund mehr - entfernen oder anpassen"))
    for entry in (defects or {}).get("defects") or []:
        hits = [f for f in findings if f.scope == "own" and f.verdict in ("new", "baseline")
                and deviation_matches(entry, f)]
        for f in hits:
            f.defect = entry["id"]
        result.defects.append({"id": entry["id"], "rule": entry["rule"], "note": entry.get("note", ""),
                               "owner": entry.get("owner", ""), "matched": len(hits),
                               "places": sorted({f"{f.file}:{f.line}" for f in hits})})
        if not hits and not any(t in skipped for t in _tools_of_rule(entry["rule"])):
            result.messages.append(Message("warning", f"defects.yaml ({entry['id']})",
                                           "Befund nicht mehr gefunden - behoben? Eintrag entfernen"))
    return result


def tool_of_rule(rule: str) -> str:
    """cppcheck ids are camelCase or misra-c2012-*, clang-tidy ids carry a group prefix (bugprone-...)."""
    return "cppcheck" if rule.startswith("misra-") or "-" not in rule else "clang-tidy"


def _tools_of_rule(rule) -> set[str]:
    return {tool_of_rule(r) for r in (rule if isinstance(rule, list) else [rule])}


# ------------------------------------------------------------------------------------------------ baseline


def build_baseline(result: Result, previous: dict, tool_versions: dict[str, str],
                   only: list[str] | None = None) -> dict:
    """Baseline from the findings that are neither notice nor deviation.

    `only` (glob patterns) rewrites the entries of matching files and keeps every other entry of `previous` as it
    is - so that a newly gated file (SYS-62) enters the baseline without taking in unrelated new findings."""
    counts: collections.Counter = collections.Counter()
    tool_of: dict[tuple, str] = {}
    for f in result.findings:
        if f.scope == "own" and f.verdict in ("new", "baseline") and (not only or glob_match(f.file, only)):
            counts[f.key] += 1
            tool_of[f.key] = f.tool
    # a tool that did not run keeps its old entries, and so does every file outside `only`
    for path, items in (previous.get("entries") or {}).items():
        for item in items:
            if item.get("tool") in result.skipped_tools or (only and not glob_match(path, only)):
                key = (path, item["rule"], item.get("text", "") or "")
                counts[key] = int(item.get("count", 1))
                tool_of[key] = item["tool"]
    entries: dict[str, list[dict]] = {}
    for (path, rule, text) in sorted(counts):
        entries.setdefault(path, []).append({"tool": tool_of[(path, rule, text)], "rule": rule,
                                             "count": counts[(path, rule, text)], "text": text})
    return {"version": 1, "tools": dict(sorted(tool_versions.items())),
            "updated": _dt.date.today().isoformat(), "entries": entries}


BASELINE_HEADER = """\
# Baseline der statischen Analyse (PLAT-74) - bekannte Befunde aus der Bestandsaufnahme ueber den eigenen Code.
# Erzeugt mit `python -m tools.misra baseline`; nicht von Hand erweitern. Ein Befund, der weder hier noch in
# deviations.yaml steht, bricht den Lauf. Zuordnung ohne Zeilennummer: Datei + Regel + Quellzeile (text), gezaehlt.
# Echte Fehler unter diesen Befunden stehen in defects.yaml (mit Aufgabe), nicht hier und nicht als Abweichung.
# Faellt ein Befund weg, meldet der Lauf ihn als "behoben"; dann neu schreiben (die Baseline wird nur kleiner).
"""


def dump_baseline(data: dict) -> str:
    # one line per entry: reviewable diffs when the baseline shrinks
    return BASELINE_HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=160,
                                            default_flow_style=None)


# ------------------------------------------------------------------------------------------------ reports


def inventory(result: Result) -> dict:
    """Numbers per rule and module (own code) and per rule (copies)."""
    own = [f for f in result.findings if f.scope == "own" and f.verdict not in ("notice", "disapplied")]
    disapplied = [f for f in result.findings if f.scope == "own" and f.verdict == "disapplied"]
    copies = [f for f in result.findings if f.scope == "copy"]
    gated = set((result.scope or {}).get("gated_copies") or [])
    disapplied_module: dict[str, dict[str, int]] = collections.defaultdict(lambda: collections.Counter())
    for f in disapplied:
        disapplied_module[f.rule][f.module] += 1
    per_rule_module: dict[str, dict[str, int]] = collections.defaultdict(lambda: collections.Counter())
    for f in own:
        per_rule_module[f.rule][f.module] += 1
    return {
        "own_total": len(own),
        "own_by_verdict": dict(collections.Counter(f.verdict for f in result.findings if f.scope == "own")),
        "own_by_rule": dict(sorted(collections.Counter(f.rule for f in own).items(),
                                   key=lambda kv: (-kv[1], kv[0]))),
        "own_by_module": dict(sorted(collections.Counter(f.module for f in own).items(),
                                     key=lambda kv: (-kv[1], kv[0]))),
        "own_rule_module": {r: dict(m) for r, m in sorted(per_rule_module.items())},
        "notices": dict(collections.Counter(f.rule for f in result.findings
                                            if f.scope == "own" and f.verdict == "notice")),
        # rules not applied in this project (re-categorization plan): counted apart, never gating
        "disapplied_total": len(disapplied),
        "disapplied_by_rule": dict(sorted(collections.Counter(f.rule for f in disapplied).items())),
        "disapplied_rule_module": {r: dict(m) for r, m in sorted(disapplied_module.items())},
        "copy_total": len(copies),
        # SYS-62: findings in modified copies of the safety path, part of the own-code numbers above
        "gated_total": sum(1 for f in own if f.file in gated),
        "copy_by_rule": dict(sorted(collections.Counter(f.rule for f in copies).items(),
                                    key=lambda kv: (-kv[1], kv[0]))),
        "copy_by_file": dict(sorted(collections.Counter(f.file for f in copies).items(),
                                    key=lambda kv: (-kv[1], kv[0]))),
    }


def to_json(result: Result, rules: dict) -> dict:
    return {
        "tool_versions": result.tool_versions,
        "skipped_tools": result.skipped_tools,
        "scope": result.scope,
        "inventory": inventory(result),
        "new": [asdict(f) for f in result.new],
        "resolved": result.resolved,
        "deviations": result.deviations,
        "defects": result.defects,
        "messages": [asdict(m) for m in result.messages],
        "findings": [asdict(f) for f in result.findings],
    }


def summary_of(rules: dict, rule: str) -> str:
    info = (rules.get("rules") or {}).get(rule) or {}
    return str(info.get("summary", "")).strip()


def render_markdown(result: Result, rules: dict, limit_new: int = 200) -> str:
    inv = inventory(result)
    lines = ["# Statische Analyse (PLAT-74)", ""]
    versions = ", ".join(f"{k} {v}" for k, v in sorted(result.tool_versions.items())) or "-"
    lines.append(f"Werkzeuge: {versions}")
    for tool, why in result.skipped_tools.items():
        lines.append(f"- **{tool} uebersprungen:** {why}")
    scope = result.scope or {}
    if scope:
        lines += ["", f"Umfang: {scope.get('units', {}).get('own', 0)} eigene Uebersetzungseinheiten, "
                      f"{scope.get('units', {}).get('copy', 0)} OpenBSW-Kopien "
                      f"(Compile-Datenbank `{scope.get('compile_db', '?')}`)."]
        if scope.get("gated_copies"):
            lines.append(f"Davon als eigener Code sperrend (SYS-62, `scope.gating_copies`): "
                         f"{len(scope['gated_copies'])} geaenderte OpenBSW-Kopien im Sicherheitspfad, "
                         f"{scope.get('gated_units', 0)} davon als Uebersetzungseinheit; Befunde darin: "
                         f"{inventory(result)['gated_total']}.")
        gaps = scope.get("uncovered") or {}
        if gaps:
            n = sum(len(v) for v in gaps.values())
            lines.append(f"Nicht im Target-Build uebersetzt und daher nicht analysiert: {n} eigene Quelldateien in "
                         f"{len(gaps)} Modulen ({', '.join(sorted(gaps))}).")
    verdicts = inv["own_by_verdict"]
    lines += ["", "## Ergebnis", "",
              "| Urteil | Anzahl |", "|---|---:|"]
    for v in ("new", "baseline", "deviated", "notice"):
        lines.append(f"| {v} | {verdicts.get(v, 0)} |")
    lines.append(f"| disapplied (Regel nicht angewendet, getrennt, nicht sperrend) | {inv['disapplied_total']} |")
    lines.append(f"| OpenBSW-Kopien (getrennt, nicht sperrend) | {inv['copy_total']} |")
    new = result.new
    if new:
        lines += ["", f"## Neue Befunde ({len(new)}) - brechen den Lauf", ""]
        for f in new[:limit_new]:
            lines.append(f"- `{f.file}:{f.line}` **{f.rule}** {f.message} - `{f.text}`")
        if len(new) > limit_new:
            lines.append(f"- ... und {len(new) - limit_new} weitere (result.json)")
    if result.resolved:
        lines += ["", f"## Behoben seit der Baseline ({sum(r['count'] for r in result.resolved)})", "",
                  "Die Baseline kann verschaerft werden: `python -m tools.misra baseline`.", ""]
        for r in result.resolved[:50]:
            lines.append(f"- `{r['file']}` {r['rule']} x{r['count']} - `{r['text']}`")
    lines += ["", "## Bestandsaufnahme eigener Code je Regel", "",
              "| Regel | Kurzbeschreibung (eigene Worte) | Befunde | Module |", "|---|---|---:|---|"]
    for rule, n in inv["own_by_rule"].items():
        modules = ", ".join(f"{m} {c}" for m, c in sorted(inv["own_rule_module"][rule].items(),
                                                            key=lambda kv: (-kv[1], kv[0])))
        lines.append(f"| {rule} | {summary_of(rules, rule)} | {n} | {modules} |")
    lines += ["", "## Je Modul", "", "| Modul | Befunde |", "|---|---:|"]
    for module, n in inv["own_by_module"].items():
        lines.append(f"| {module} | {n} |")
    if result.deviations:
        lines += ["", "## Abweichungen", "", "| Kennung | Regel | Treffer | Freigegeben |", "|---|---|---:|---|"]
        for d in result.deviations:
            rule = d["rule"] if isinstance(d["rule"], str) else ", ".join(d["rule"])
            lines.append(f"| {d['id']} | {rule} | {d['matched']} | {'ja' if d['approved'] else 'offen'} |")
    if result.defects:
        lines += ["", "## Befunde, die echte Fehler oder Absicherungsluecken sind (defects.yaml)", "",
                  "Nicht weg-begruendet und nicht im Code repariert: sie gehoeren in die genannte Aufgabe.", ""]
        for d in result.defects:
            rule = d["rule"] if isinstance(d["rule"], str) else ", ".join(d["rule"])
            lines.append(f"- **{d['id']}** ({rule}, {d['matched']} Stellen, Vorschlag: {d['owner']}): {d['note']}")
            for place in d["places"][:8]:
                lines.append(f"  - `{place}`")
    revised = {r: i for r, i in (rules.get("rules") or {}).items()
               if isinstance(i, dict) and i.get("revised_category") and i.get("revised_category") != i.get("category")}
    if revised or inv["disapplied_total"]:
        lines += ["", "## Neueinstufungsplan (rules.yaml) und nicht angewendete Regeln", "",
                  "Befunde einer disapplied-Regel sind weder neu noch Teil der Baseline; sie stehen nur hier.", "",
                  "| Regel | Kategorie | neu eingestuft | Befunde | Module | Freigabe |", "|---|---|---|---:|---|---|"]
        for rule in sorted(set(revised) | set(inv["disapplied_by_rule"])):
            info = revised.get(rule) or (rules.get("rules") or {}).get(rule) or {}
            modules = ", ".join(f"{m} {c}" for m, c in sorted(inv["disapplied_rule_module"].get(rule, {}).items(),
                                                                key=lambda kv: (-kv[1], kv[0])))
            lines.append(f"| {rule} | {info.get('category', '?')} | {info.get('revised_category', '?')} | "
                         f"{inv['disapplied_by_rule'].get(rule, 0)} | {modules or '-'} | "
                         f"{info.get('approved_by') or 'offen'} |")
    if inv["notices"]:
        lines += ["", "## Werkzeughinweise (nicht sperrend)", ""]
        for rule, n in sorted(inv["notices"].items()):
            lines.append(f"- {rule}: {n}")
    if inv["copy_total"]:
        lines += ["", "## OpenBSW-Kopien (tools/openbswsync/copies.yaml, getrennt ausgewiesen)", "",
                  "| Regel | Befunde |", "|---|---:|"]
        for rule, n in inv["copy_by_rule"].items():
            lines.append(f"| {rule} | {n} |")
    if result.messages:
        lines += ["", "## Meldungen", ""] + [f"- {m}" for m in result.messages]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------ run


@dataclass
class RunOptions:
    compile_db: Path
    out: Path
    tools: tuple[str, ...] = TOOLS
    tool_dir: Path | None = None
    jobs: int = 4
    raw_dir: Path | None = None        # reuse cppcheck.xml / clang-tidy.txt of an earlier run
    repo: Path = REPO
    rules_file: Path = RULES_FILE
    deviations_file: Path = DEVIATIONS_FILE
    baseline_file: Path = BASELINE_FILE
    defects_file: Path = DEFECTS_FILE
    copies_file: Path | None = COPIES_FILE
    # TOOL-217 (gap N19): folders below `repo` that are not own code - in a customer workspace the pinned tree (a
    # submodule), its build root and .venv. Classified ``outside`` like a build directory: read as context, never
    # judged.
    outside_dirs: tuple[Path, ...] = ()
    # where own sources without a translation unit are looked for; None = scope.source_roots of rules.yaml
    source_roots: list[str] | None = None


def analyse(opts: RunOptions, log=print) -> tuple[Result, dict]:
    rules = read_yaml(opts.rules_file)
    deviations = read_yaml(opts.deviations_file)
    baseline = read_yaml(opts.baseline_file) if opts.baseline_file.is_file() else {"version": 1, "entries": {}}
    defects = read_yaml(opts.defects_file) if opts.defects_file.is_file() else {"version": 1, "defects": []}
    messages = (validate_rules(rules, opts.rules_file) + validate_deviations(deviations, opts.deviations_file)
                + validate_baseline(baseline, opts.baseline_file, disapplied_rules(rules))
                + validate_defects(defects, opts.defects_file))
    errors = [m for m in messages if m.severity == "error"]
    if errors:
        raise MisraError("Konfiguration fehlerhaft:\n  " + "\n  ".join(str(m) for m in errors))
    scope_cfg = rules.get("scope") or {}
    classifier = Classifier(opts.repo, list(scope_cfg.get("exclude", [])), opts.copies_file,
                            list(scope_cfg.get("gating_copies", [])),
                            [opts.compile_db.parent, *opts.outside_dirs])
    if not opts.compile_db.is_file():
        raise MisraError(f"Compile-Datenbank fehlt: {opts.compile_db} (vorher den Target-Build bauen, "
                         f"z. B. tools/test -k target-demo)")
    units, stats = select_units(opts.compile_db, classifier, scope_cfg.get("prefer_config", ""),
                                tuple(scope_cfg.get("context", [])))
    stats["uncovered"] = uncovered_sources(classifier, [u for u in units if u.scope in SCOPES],
                                           list(scope_cfg.get("source_roots", []) if opts.source_roots is None
                                                else opts.source_roots))
    if not any(u.scope == "own" for u in units):
        raise MisraError(f"keine eigene Uebersetzungseinheit in {opts.compile_db} - falsche Compile-Datenbank?")
    opts.out.mkdir(parents=True, exist_ok=True)
    raw: list[Finding] = []
    skipped: dict[str, str] = {}
    versions: dict[str, str] = {}
    for tool in TOOLS:
        if tool not in opts.tools:
            skipped[tool] = "nicht ausgewaehlt (--tool)"
            continue
        raw_name = "cppcheck.xml" if tool == "cppcheck" else "clang-tidy.txt"
        if opts.raw_dir is not None:
            source = opts.raw_dir / raw_name
            if not source.is_file():
                skipped[tool] = f"keine Rohdaten {source}"
                continue
            text = source.read_text(encoding="utf-8", errors="replace")
            versions[tool] = "(Rohdaten)"
        else:
            exe, tried = find_tool(tool, opts.tool_dir)
            if exe is None:
                skipped[tool] = ("nicht gefunden (gesucht: " + "; ".join(tried) +
                                 ") - `python -m tools.misra setup` oder NX_MISRA_TOOLS setzen")
                log(f"UEBERSPRUNGEN: {tool} {skipped[tool]}")
                continue
            versions[tool] = tool_version(exe)
            work = opts.out / "work"
            if tool == "cppcheck":
                text = run_cppcheck(exe, units, rules.get("cppcheck") or {}, work, opts.jobs, log)
            else:
                # per translation unit: context units (generated code) would only add dropped findings
                text = run_clang_tidy(exe, [u for u in units if u.scope in SCOPES], rules.get("clang_tidy") or {},
                                      work, opts.jobs, log)
            (opts.out / raw_name).write_text(text, encoding="utf-8")
        raw += parse_cppcheck_xml(text) if tool == "cppcheck" else parse_clang_tidy(text)
    findings = locate(raw, classifier)
    result = evaluate(findings, rules, deviations, baseline, skipped, defects)
    result.messages = messages + result.messages
    undescribed = sorted({f.rule for f in result.findings if f.scope == "own" and f.verdict != "notice"
                          and not summary_of(rules, f.rule)})
    if undescribed:
        result.messages.append(Message("warning", f"{_rel(opts.rules_file)} (rules)",
                                       "ohne eigene Kurzbeschreibung: " + ", ".join(undescribed)))
    result.tool_versions = versions
    result.scope = stats
    for tool in TOOLS:
        old = (baseline.get("tools") or {}).get(tool)
        if old and versions.get(tool) and versions[tool] != old and opts.raw_dir is None:
            result.messages.append(Message("warning", "baseline.yaml (tools)",
                                           f"{tool} {versions[tool]} statt {old} der Baseline - neue Befunde "
                                           f"koennen vom Werkzeug kommen"))
    return result, {"rules": rules, "deviations": deviations, "baseline": baseline}


def write_reports(result: Result, rules: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(to_json(result, rules), indent=1, ensure_ascii=False),
                                     encoding="utf-8")
    (out / "report.md").write_text(render_markdown(result, rules), encoding="utf-8")


def exit_code(result: Result, wanted: tuple[str, ...]) -> int:
    if result.new:
        return EXIT_NEW
    if all(t in result.skipped_tools for t in wanted):
        return EXIT_SKIPPED
    return EXIT_OK


def default_compile_db(repo: Path = REPO) -> Path:
    sys.path.insert(0, str(repo))
    from tools.test.build_dirs import demo_build_dir

    return demo_build_dir(repo) / "compile_commands.json"


def temp_out() -> Path:
    return Path(tempfile.gettempdir()) / "nextbsw-misra"
