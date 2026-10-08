# SPDX-License-Identifier: Apache-2.0
"""The list of what an export leaves out on purpose (DOC-85) - written by the export, read by the consistency tests.

The safety documents check each other and the repository: the anomaly register needs the findings of the gap
analysis and of the review records, the document register names the traceability matrix, the safety case cites board
captures, test entries, tasks and decisions in doc/todo.yaml, the DFA and the FMEA cite test entries. Since decision
K1 (2026-10-04) some of these stay in the development repository, the export removes test entries that need private
files, and the public todo.yaml has neither done tasks nor internal fields. In the exported tree those checks must
still run - an absent source is only acceptable when the export says that it left it out on purpose:

    doc/unpublished.yaml      (path: export.unpublished.file in tools/distcheck/distribution.yaml)

    generator: tools/distcheck export
    files: [doc/safety/iso26262-gap.md, ...]    unpublished private files of the scope export.unpublished.files
    folders: [tools/studio, ...]                folders of export.unpublished.folders, wholly private and unpublished
    cleaned: [doc/todo.yaml, ...]               files published only in a cleaned form (their text is not the source)
    tests: [elfcheck-demo, ...]                 entries of tools/test/tests.yaml the export removed
    tasks: {SYS-19: done, ...}                  tasks of doc/todo.yaml the export removed, with their status

The development repository has no such file: there `load()` returns an empty list, so every absent source is an
error. In the export a source missing from the list (a public file that did not get there) stays an error as well.
Files are listed only for the scope (never NDA or workstation material: their names are not published either).

This module has no relative imports, so the tests load it by its path without the package.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LIST = "doc/unpublished.yaml"          # default; the export takes the path from export.unpublished.file
GENERATOR = "tools/distcheck export"


class UnpublishedError(Exception):
    """The list exists but is not one the export wrote."""


@dataclass(frozen=True)
class Unpublished:
    files: frozenset[str] = field(default_factory=frozenset)
    folders: frozenset[str] = field(default_factory=frozenset)
    cleaned: frozenset[str] = field(default_factory=frozenset)
    tests: frozenset[str] = field(default_factory=frozenset)
    tasks: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.files or self.folders or self.cleaned or self.tests or self.tasks)

    def file(self, rel: str) -> bool:
        """`rel` (repository relative, posix) is a file or folder the export left out on purpose: a listed file, a
        path inside a listed folder, a listed folder, or a folder that holds a listed file."""
        rel = rel.strip().rstrip("/")
        if not rel:
            return False
        if rel in self.files or rel in self.folders:
            return True
        if any(rel.startswith(folder + "/") for folder in self.folders):
            return True
        return any(f.startswith(rel + "/") for f in self.files | self.folders)

    def content_hidden(self, rel: str) -> bool:
        """`rel` is published only in a cleaned form: a text expected in the original need not be in it."""
        return rel.strip().rstrip("/") in self.cleaned

    def under(self, folder: str, pattern: str = "*") -> list[str]:
        """Listed files directly in `folder` (repository relative) whose name matches `pattern`."""
        folder = folder.rstrip("/") + "/"
        return sorted(f for f in self.files if f.startswith(folder) and "/" not in f[len(folder):]
                      and fnmatch.fnmatchcase(f[len(folder):], pattern))


def load(repo: Path, rel: str = LIST) -> Unpublished:
    """The list of the tree at `repo`; empty in the development repository (no file)."""
    path = Path(repo) / rel
    if not path.is_file():
        return Unpublished()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or data.get("generator") != GENERATOR:
        raise UnpublishedError(f"{rel}: not written by {GENERATOR} (generator missing)")
    tasks = data.get("tasks") or {}
    if not isinstance(tasks, dict):
        raise UnpublishedError(f"{rel}: tasks is not a mapping ID -> status")

    def paths(key: str) -> frozenset[str]:
        return frozenset(str(p).rstrip("/") for p in data.get(key) or [])

    return Unpublished(files=paths("files"), folders=paths("folders"), cleaned=paths("cleaned"),
                       tests=frozenset(str(t) for t in data.get("tests") or []),
                       tasks={str(k): str(v) for k, v in tasks.items()})


def render(files: list[str], tests: list[str], tasks: dict[str, str], scope: list[str],
           folders: list[str] = (), cleaned: list[str] = ()) -> str:
    head = ("# What this tree leaves out on purpose (written by tools/distcheck export, DOC-85). The documents of the\n"
            "# tree may cite these files, folders, test entries and tasks; they stay in the development repository\n"
            "# (not published, available on request). The consistency tests accept an absent source only if it is\n"
            f"# listed here. Files are listed for the scope {', '.join(scope) or '-'} only; `cleaned` files are\n"
            "# published in a cleaned form only.\n")
    body = yaml.safe_dump({"generator": GENERATOR, "files": sorted(files), "folders": sorted(folders),
                           "cleaned": sorted(cleaned), "tests": sorted(tests), "tasks": dict(sorted(tasks.items()))},
                          allow_unicode=True, sort_keys=False, width=120)
    return head + body
