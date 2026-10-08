# SPDX-License-Identifier: Apache-2.0
"""Host test of the open user guide (DOC-11, PLAT-46 F10): no page of `doc/guide` points at private material.

The guide is part of the open tree. What the distribution rules (`tools/distcheck/distribution.yaml`) classify as
private - the files of the maintainers' own board and its projects, the schematic, internal planning - must not be
the target of a link or of a path in `doc/guide`, and the name of such a board or project must not appear in the
text, because the reader of the exported tree cannot open it and the example would lose its meaning. Every example in
the guide has to be reproducible from the open files.

`distcheck` itself (rule R5) looks at code, CMake and YAML; it does not read Markdown, so this test closes that gap
for the guide. The helper `find_leaks` is also tested with leaks built in on purpose (test profile, not the
repository), so a regression of the check shows up here and not in an export.

    python -m unittest doc/guide/test_guide_open.py
"""

from __future__ import annotations

import posixpath
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.distcheck import distcheck as core          # noqa: E402

GUIDE = Path(__file__).resolve().parent

# Pages that may name a private board although it is not a link: the output of a real run on the maintainers' tree.
# DOC-36 owns this page; once its sample output is made reproducible from the open tree, remove the entry.
NAME_EXEMPT = {"customer-project.md": "DOC-36: listing of a real run (boards available) names the maintainers' board"}

# Known private targets of a page, tolerated until its owner changes them: page -> paths (and why).
# customer-project.md (DOC-36) describes the proprietary Studio of the maintainers and links its sources.
PATH_EXEMPT = {
    "customer-project.md": {"tools/studio", "tools/studio/core/releasearchive.py"},
    # not board material: names of the proprietary Studio and an internal table, to be decided with the licence work
    "swc/README.md": {"doc/naming/praefix-tabelle.yaml", "tools/studio/core/portnaming.py"},
}

# Further open files that point readers at examples (DOC-11): checked like the pages of the guide.
EXTRA_FILES = (
    "projects/README.md",
    "swc/README.md",
    "projects/buildtest_s32k148_min/rte.yaml",
    # TOOL-243c: projects/s32k116evb_minimal/rte.yaml left this list - board and project are private until PLAT-13
)

LINK = re.compile(r"\]\(([^)\s]+)\)")
SPAN = re.compile(r"`([^`\n]+)`")


def _repo_path(page: str) -> str:
    """A page name of doc/guide, or a repository path of one of the extra files."""
    return page if "/" in page else f"doc/guide/{page}"


class Leak:
    def __init__(self, page: str, line: int, what: str):
        self.page, self.line, self.what = page, line, what

    def __str__(self) -> str:
        return f"{_repo_path(self.page)}:{self.line}: {self.what}"


def private_names(tree: core.Tree) -> dict[str, str]:
    """Board and project folders in which no file is open: name (and its preset spelling) -> folder."""
    names: dict[str, str] = {}
    for top in ("boards", "projects"):
        folders: dict[str, list[str]] = {}
        for path in tree.files:
            parts = path.split("/")
            if parts[0] == top and len(parts) > 2:
                folders.setdefault(parts[1], []).append(path)
        for name, files in folders.items():
            if not any(tree.cls.get(p) == "open" for p in files):
                names[name] = f"{top}/{name}"
                names[name.replace("_", "-")] = f"{top}/{name}"
    return names


def _candidates(line: str) -> list[tuple[str, bool]]:
    """(text, is_link_target) of every link target and every inline code span of a line."""
    out = [(m.group(1), True) for m in LINK.finditer(line)]
    out += [(m.group(1), False) for m in SPAN.finditer(line)]
    return out


def _resolve(text: str, is_link: bool, page_dir: str, tops: set[str]) -> str | None:
    text = text.split("#", 1)[0].strip().rstrip(".,;:")
    if not text or text.startswith(("http://", "https://", "mailto:")) or re.search(r"[\s<>*{}|$]", text):
        return None
    if text.startswith("/") or ":" in text:
        return None
    if is_link or text.startswith("../"):
        path = posixpath.normpath(posixpath.join(page_dir, text))
    elif text.split("/", 1)[0] in tops:
        path = posixpath.normpath(text)
    else:
        return None
    return None if path.startswith("..") else path


def find_leaks(page: str, text: str, tree: core.Tree, names: dict[str, str]) -> list[Leak]:
    """Links and paths of one page that lead to files none of which is open, and names of private boards."""
    tops = {p.split("/", 1)[0] for p in tree.files}
    page_dir = posixpath.dirname(_repo_path(page))
    name_re = None
    if names and page not in NAME_EXEMPT:
        name_re = re.compile(r"(?<![A-Za-z0-9_-])(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                             + r")(?![A-Za-z0-9_-])")
    out: list[Leak] = []
    for number, line in enumerate(text.splitlines(), start=1):
        before = len(out)
        for candidate, is_link in _candidates(line):
            path = _resolve(candidate, is_link, page_dir, tops)
            if path is None:
                continue
            if path in tree.cls:
                files = [path]
            else:
                files = [p for p in tree.files if p.startswith(path.rstrip("/") + "/")]
            if files and not any(tree.cls.get(p) == "open" for p in files) and path not in PATH_EXEMPT.get(page, ()):
                klass = tree.cls.get(files[0], "?")
                out.append(Leak(page, number, f"points at {path} (class {klass}, not in the open tree)"))
        if name_re is not None and len(out) == before:     # one finding per line is enough
            for match in name_re.finditer(re.sub(r"https?://\S+", "", line)):
                out.append(Leak(page, number, f"names {names[match.group(1)]} (private board or project)"))
                break
    return out


RULES = r"""
version: 1
profiles:
  open: { licenses: [Apache-2.0] }
paths:
  - { glob: "**", class: private }
  - { glob: "doc/guide/**", class: open, license: Apache-2.0 }
  - { glob: "boards/open_board/**", class: open, license: Apache-2.0 }
  - { glob: "boards/own_board/**", class: private }
  - { glob: "projects/own_demo/**", class: private }
"""

FILES = [
    "doc/guide/page.md",
    "boards/open_board/board.yaml",
    "boards/own_board/board.yaml",
    "boards/own_board/README.md",
    "projects/own_demo/project.yaml",
]


class FindLeaksTest(unittest.TestCase):
    """Synthetic tree with its own rules: each kind of leak is found with page and line, clean text passes."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._dir = tempfile.TemporaryDirectory()
        path = Path(cls._dir.name) / "distribution.yaml"
        path.write_text(RULES, encoding="utf-8")
        rules = core.load_rules(path)
        cls.tree = core.build_tree(Path(cls._dir.name), rules, use_git=False, files=FILES)
        cls.names = private_names(cls.tree)

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def leaks(self, text: str, page: str = "page.md") -> list[Leak]:
        return find_leaks(page, text, self.tree, self.names)

    def test_names_come_from_the_rules(self):
        self.assertEqual({"own_board", "own-board", "own_demo", "own-demo"}, set(self.names))

    def test_clean_text_passes(self):
        text = "See [the board](../../boards/open_board/board.yaml) and `boards/open_board`.\nPreset `open-board-min`.\n"
        self.assertEqual([], self.leaks(text))

    def test_link_to_private_file_is_found(self):
        found = self.leaks("x\n[board](../../boards/own_board/README.md)\n")
        self.assertEqual([2], [leak.line for leak in found])
        self.assertIn("boards/own_board/README.md", str(found[0]))

    def test_path_in_code_span_is_found(self):
        self.assertEqual(1, len(self.leaks("Excerpt of `projects/own_demo/project.yaml`:\n")))

    def test_private_folder_is_found(self):
        self.assertEqual(1, len(self.leaks("The folder `boards/own_board` holds it.\n")))

    def test_name_and_preset_spelling_are_found(self):
        self.assertEqual(1, len(self.leaks("Run nxconfig on own_board now.\n")))
        self.assertEqual(1, len(self.leaks("cmake --preset own-demo\n")))

    def test_longer_names_do_not_match(self):
        self.assertEqual([], self.leaks("my_own_board_x and own_boards\n"))

    def test_exempt_page_may_name_a_board(self):
        page = next(iter(NAME_EXEMPT))
        self.assertEqual([], self.leaks("available: own_board\n", page))
        self.assertEqual(1, len(self.leaks("[b](../../boards/own_board/README.md)\n", page)))

    def test_external_and_anchor_targets_are_ignored(self):
        self.assertEqual([], self.leaks("[a](https://example.org/boards/own_board/x) and [b](#top)\n"))


class RealGuideTest(unittest.TestCase):
    """The pages of doc/guide against the real classification of the repository."""

    @classmethod
    def setUpClass(cls):
        rules = core.load_rules(REPO / "tools" / "distcheck" / "distribution.yaml")
        cls.tree = core.build_tree(REPO, rules, exported=True)
        cls.names = private_names(cls.tree)

    def test_private_names_are_known(self):
        # PLAT-62: in the exported open tree there is no private board left whose name the rules could mark
        if not (REPO / "boards" / "evalboard_s32k144").is_dir():
            self.skipTest("private eval board not in this tree (exported open tree, PLAT-62)")
        self.assertIn("evalboard_s32k144", self.names, "the rules no longer mark the own board private: update this test")

    def test_pages_are_open(self):
        pages = sorted(p.name for p in GUIDE.glob("*.md"))
        self.assertTrue(pages)
        for page in pages:
            self.assertEqual("open", self.tree.cls.get(f"doc/guide/{page}"), page)

    def test_no_leaks(self):
        leaks: list[Leak] = []
        for page in sorted(GUIDE.glob("*.md")):
            leaks += find_leaks(page.name, page.read_text(encoding="utf-8"), self.tree, self.names)
        for extra in EXTRA_FILES:
            self.assertEqual("open", self.tree.cls.get(extra), extra)
            leaks += find_leaks(extra, (REPO / extra).read_text(encoding="utf-8"), self.tree, self.names)
        self.assertEqual([], [str(leak) for leak in leaks])


if __name__ == "__main__":
    unittest.main()
