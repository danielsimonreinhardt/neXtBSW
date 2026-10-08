"""Reference output of the safety-relevant generated files (PLAT-78, tool-classification.md proposal B, finding TF1).

Shared by tools/bswgen/test_reference_output.py (bswgen with blgen's memory layout) and
tools/rtegen/test_reference_output.py (rtegen with dbcgen, diaggen). A test profile renders its generated files into a
temporary directory; the files named by the profile are normalised and compared with the reviewed copies under
`<tool>/test/reference/<profile>/`. A difference fails with a unified diff; `--update` rewrites the copies, and the
diff of the reference is reviewed in the commit like code.

Normalisation removes what depends on the machine and not on the configuration: line endings, the repository root and
the temporary output directory (as absolute paths in either slash direction), time stamps. Nothing else is touched,
so a changed template, a changed generator or a changed configuration shows up as a difference.

No Qt, no generator import here: the callers render, this module only compares and writes.
"""

from __future__ import annotations

import contextlib
import difflib
import io
import re
import unittest.mock
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import jinja2

REPO = Path(__file__).resolve().parents[3]

# ISO date with time (2026-09-22 03:14, 2026-09-22T03:14:15Z): no generator writes one today; if one ever does, the
# reference must not change with every run
TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")

MAX_DIFF_LINES = 80          # per file in the failure message


@dataclass
class Profile:
    """One test profile: `render(out_dir)` writes the generated files below out_dir, `files` names those checked.

    `available` is False when an input is missing (a private project in an open export): the profile is skipped.
    A file of `files` that the profile does not generate is simply not part of its reference (e.g. CanDb.c of an ECU
    without CAN) - but a file present on one side only is a difference.
    """
    name: str
    render: Callable[[Path], None]
    files: tuple[str, ...]
    comment: str = ""
    inputs: tuple[Path, ...] = field(default_factory=tuple)

    @property
    def available(self) -> bool:
        return all(p.exists() for p in self.inputs)


def _path_forms(path: Path) -> list[str]:
    """Absolute spellings of `path` a generator could write (posix, native, lower-case drive), longest first."""
    p = Path(path).resolve()
    forms = {p.as_posix(), str(p)}
    for form in list(forms):
        if len(form) > 1 and form[1] == ":":
            forms.add(form[0].lower() + form[1:])
            forms.add(form[0].upper() + form[1:])
    return sorted(forms, key=len, reverse=True)


def normalise(text: str, out_dir: Path, repo: Path = REPO) -> str:
    text = text.replace("\r\n", "\n")
    for form in _path_forms(out_dir):
        text = text.replace(form, "<out>")
    for form in _path_forms(repo):
        text = text.replace(form, "<repo>")
    return TIMESTAMP.sub("<timestamp>", text)


def render(profile: Profile, work: Path) -> dict[str, str]:
    """Normalised text of the profile's checked files, keyed by path relative to the output directory."""
    out = Path(work) / profile.name
    out.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stderr(io.StringIO()):      # generator warnings are not part of the reference
        profile.render(out)
    result = {}
    for rel in profile.files:
        path = out / rel
        if path.is_file():
            result[rel] = normalise(path.read_text(encoding="utf-8"), out)
    return result


def stored(reference_dir: Path, profile: Profile) -> dict[str, str]:
    base = Path(reference_dir) / profile.name
    if not base.is_dir():
        return {}
    return {p.relative_to(base).as_posix(): p.read_text(encoding="utf-8").replace("\r\n", "\n")
            for p in sorted(base.rglob("*")) if p.is_file()}


def differences(expected: dict[str, str], actual: dict[str, str], profile: str) -> list[str]:
    """Readable differences, one block per file; empty if equal."""
    out = []
    for rel in sorted(set(expected) | set(actual)):
        if rel not in actual:
            out.append(f"{profile}/{rel}: in the reference, but no longer generated")
            continue
        if rel not in expected:
            out.append(f"{profile}/{rel}: generated, but not in the reference")
            continue
        if expected[rel] == actual[rel]:
            continue
        diff = list(difflib.unified_diff(expected[rel].splitlines(), actual[rel].splitlines(),
                                         f"reference/{profile}/{rel}", f"generated/{profile}/{rel}", lineterm=""))
        if len(diff) > MAX_DIFF_LINES:
            diff = diff[:MAX_DIFF_LINES] + [f"... ({len(diff) - MAX_DIFF_LINES} more diff lines)"]
        out.append("\n".join(diff))
    return out


def write(reference_dir: Path, profile: Profile, actual: dict[str, str]) -> list[str]:
    """Replaces the stored reference of the profile by `actual`; returns the changed paths (for the report)."""
    base = Path(reference_dir) / profile.name
    before = stored(reference_dir, profile)
    changed = []
    for rel in before:
        if rel not in actual:
            (base / rel).unlink()
            changed.append(f"removed  {profile.name}/{rel}")
    for rel, text in sorted(actual.items()):
        if before.get(rel) != text:
            target = base / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            changed.append(f"{'updated ' if rel in before else 'added   '} {profile.name}/{rel}")
    for d in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    return changed


def mutated_template(name: str, old: str, new: str):
    """Context manager: every Jinja FileSystemLoader serves template `name` with `old` replaced by `new`.

    The generators create a fresh environment per run, so the change reaches exactly the next rendering. Fails if
    `old` is not in the template (the mutant would test nothing).
    """
    original = jinja2.FileSystemLoader.get_source

    def get_source(self, environment, template):
        source, filename, uptodate = original(self, environment, template)
        if template == name:
            if old not in source:
                raise AssertionError(f"mutant of {name}: '{old}' not found in the template")
            source = source.replace(old, new, 1)
        return source, filename, uptodate

    return unittest.mock.patch.object(jinja2.FileSystemLoader, "get_source", get_source)


def update_main(argv: list[str], profiles: list[Profile], reference_dir: Path, work: Path) -> int:
    """`--update [profile ...]`: rewrite the reference of the named (default: all available) profiles."""
    names = [a for a in argv if not a.startswith("-")]
    unknown = [n for n in names if n not in {p.name for p in profiles}]
    if unknown:
        print(f"unknown profile(s): {', '.join(unknown)}; known: {', '.join(p.name for p in profiles)}")
        return 2
    changed = []
    for profile in profiles:
        if names and profile.name not in names:
            continue
        if not profile.available:
            print(f"{profile.name}: skipped (input missing: "
                  f"{', '.join(str(p) for p in profile.inputs if not p.exists())})")
            continue
        changed += write(reference_dir, profile, render(profile, work))
    rel = Path(reference_dir).resolve().relative_to(REPO).as_posix()
    print("\n".join(changed) if changed else f"{rel}: reference unchanged")
    if changed:
        print(f"review the difference before committing: git diff -- {rel}")
    return 0


# ---------------------------------------------------------------------------------------- licence line (PLAT-120)

def licence_gaps(out_dir: Path, exempt: tuple[str, ...] = ()) -> list[str]:
    """Files below out_dir that lack the SPDX line of the generator output exception (nxconfig/genlicense.py).

    Files of a format without comments (JSON) and the names in `exempt` are not expected to carry it."""
    import sys
    sys.path.insert(0, str(REPO / "tools"))
    from nxconfig import genlicense
    gaps = []
    for path in sorted(Path(out_dir).rglob("*")):
        rel = path.relative_to(out_dir).as_posix()
        if not path.is_file() or rel in exempt or genlicense.style_of(rel) is None:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()[:6]
        if not any(f"{genlicense.TAG}: {genlicense.IDENTIFIER}" in line for line in lines):
            gaps.append(rel)
    return gaps
