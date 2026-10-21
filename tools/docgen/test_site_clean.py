# SPDX-License-Identifier: Apache-2.0
"""Website without internal traces (SITE-CLEAN): the real site, built from the open export, has no internal task ID
and no CLAUDE reference in its visible text.

The site is exported and built like the published one (tools/docgen/site.py, strict). Every HTML page of both
languages is read: outside code blocks (<pre>) the visible text, the page title, the navigation and HTML comments
must hold 0 task IDs (sanitize.CHECK_RE: the eleven planning prefixes; FM-RTE-01 and the like are failure-mode IDs of
the safety analysis and do not count) and 0 "Claude" in any case. Mermaid diagrams (<pre class="mermaid">) are visible
text too and must be clean. Code blocks are left as written (rule of the sanitizer): their hits are counted and
printed, not failed. Counter-tests: the counting itself finds an ID in text, in a heading, in a comment and in a
Mermaid label, keeps code blocks apart and spares real document identifiers.

Skipped without the packages of tools/requirements-site.txt (tests.yaml: requires mkdocs).
"""

from __future__ import annotations

import html as htmllib
import importlib.util
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.docgen import sanitize, site          # noqa: E402

PACKAGES = ("mkdocs", "material", "mkdocs_static_i18n", "pymdownx")
HAVE_MKDOCS = all(importlib.util.find_spec(name) is not None for name in PACKAGES)
SKIP = "mkdocs, mkdocs-material, mkdocs-static-i18n missing: pip install -r tools/requirements-site.txt"

_PRE = re.compile(r"<pre\b([^>]*)>(.*?)</pre>", re.S)
_DROP = re.compile(r"<script\b.*?</script>|<style\b.*?</style>", re.S)
_COMMENT = re.compile(r"<!--(.*?)-->", re.S)
_TAG = re.compile(r"<[^>]+>")


def traces(page: str) -> dict[str, list[str]]:
    """Hits of one HTML page: 'text' (visible text and comments outside <pre>), 'mermaid' and 'code' (<pre>)."""
    found: dict[str, list[str]] = {"text": [], "mermaid": [], "code": []}

    def hits(text: str) -> list[str]:
        text = htmllib.unescape(text)
        return sanitize.CHECK_RE.findall(text) + sanitize.CLAUDE_RE.findall(text)

    def pre(match: re.Match) -> str:
        kind = "mermaid" if "mermaid" in match.group(1) else "code"
        found[kind].extend(hits(_TAG.sub("", match.group(2))))
        return " "
    rest = _PRE.sub(pre, _DROP.sub(" ", page))
    for comment in _COMMENT.findall(rest):
        found["text"].extend(hits(comment))
    found["text"].extend(hits(_TAG.sub(" ", _COMMENT.sub(" ", rest))))
    return found


_P = "|".join(sanitize.PREFIXES)
# stage 2: one check per category of internal traces (visible text and Mermaid; code blocks are kept apart)
CATEGORIES = {
    "derived": re.compile(rf"(?<![\w-])(?:(?:{_P})\d+|[SP]\d{{3}}|B-(?:{_P})\d+)-[A-Z]{{0,3}}\d+\b"),
    "board_ids": sanitize.LOW_RE,
    "roles": re.compile(r"\bcoordinator|\bKoordinator|\bagents?\b|\bAgent(?:en)?\b|cloud[- ]session|"
                        r"session vscode-workspace|\bwave[ -](?:\d|cloud)|\b(?:this|cloud) waves?\b|\bcloud\s+wave|\bWelle\b|\bnight\d*\b|"
                        r"(?<![\w/])integ/|dev-motor"),
    "persons": re.compile(r"\b[Tt]he\s+user\b(?!\s+(?:test|guide|interface|manual|code|space|mode|data|program)|-)|"
                          r"\buser,?\s+\d{4}-\d{2}-\d{2}|\(user[,)]|\bNutzer\w*"),
    "decision_board": re.compile(r"decision board|Entscheidungsboard|\(order \d+\)|\[Decided [a-z]"),
    "private_links": re.compile(r"claude\.ai/|scratchpad|agent-<id>|<worktree name>|"
                                r"\b(?=[A-Za-z0-9]{22}\b)(?=(?:[^\W\d_]*\d))(?=(?:[^A-Z\s]*[A-Z]){4})[A-Za-z0-9]{22}\b"),
    "planning_files": re.compile(r"todo\.yaml|TODO\.md|koordinator-stand|internal working files"),
}


def category_traces(page: str) -> dict[str, list[str]]:
    """Hits per stage-2 category in the visible text, comments and Mermaid diagrams of one HTML page."""
    parts: list[str] = []

    def pre(match: re.Match) -> str:
        if "mermaid" in match.group(1):
            parts.append(_TAG.sub("", match.group(2)))
        return " "
    rest = _PRE.sub(pre, _DROP.sub(" ", page))
    parts.extend(_COMMENT.findall(rest))
    parts.append(_TAG.sub(" ", _COMMENT.sub(" ", rest)))
    text = htmllib.unescape("\n".join(parts))
    found = {name: rx.findall(text) for name, rx in CATEGORIES.items()}
    # a 22-character word is a private page ID only if it is no camelCase identifier (sanitize._is_artefact_id)
    found["private_links"] = [w for w in found["private_links"] if len(w) != 22 or sanitize._is_artefact_id(w)]
    return found


class CountingCategories(unittest.TestCase):
    """Stage 2: each category check finds its trace (counter-test) and spares the real document words."""

    BAD = {
        "derived": "<p>decided a (S211-2, review) and SYS188-P2, B-IO44-4</p>",
        "board_ids": "<p>pending (cloud-1001-sys-63), decision tool229-cm-regeln-1011</p>",
        "roles": "<p>the coordinator merged it on dev-motor in wave cloud-0930</p>",
        "persons": "<p>accepted by the user (user, 2026-10-04)</p>",
        "decision_board": "<p>an item on the decision board</p>",
        "private_links": "<p>artefact VUvnHk7QPZNqDUgAAYoMjr, https://claude" ".ai/artifact/x</p>",
        "planning_files": "<p>open in <code>doc/todo.yaml</code></p>",
    }
    GOOD = ("<p>the first DMA user, a user interface, the user test; UDS session 10 02; issue 2, 2026-10-04; "
            "R-02, R-17, SSR-002, AN-015, FM-RTE-01, D-01; <code>customHardFaultHandler</code>, "
            "<code>gdTmc6200Configuration</code>, <code>Tmc6200BindingHostTest</code>, <code>tmc6200-io67-mutations</code>; the maintainer, the safety "
            "manager, the development branch, the negative half wave</p>")

    def test_each_check_finds_its_trace(self):
        for name, page in self.BAD.items():
            self.assertTrue(category_traces(page)[name], name)

    def test_checks_spare_real_words(self):
        self.assertEqual({k: v for k, v in category_traces(self.GOOD).items() if v}, {})

    def test_code_blocks_are_not_counted(self):
        self.assertEqual({k: v for k, v in category_traces("<pre><code>coordinator SYS188-P2</code></pre>").items()
                          if v}, {})


class Counting(unittest.TestCase):
    """The counting bites (counter-tests) and spares what is no internal trace."""

    def test_finds_internal_traces(self):
        self.assertEqual(traces("<p>closed by SYS-133</p>")["text"], ["SYS-133"])
        self.assertEqual(traces("<h2 id='x'>Release (TOOL-69)</h2>")["text"], ["TOOL-69"])
        self.assertEqual(traces("<!-- DOC-61 tag --><p>x</p>")["text"], ["DOC-61"])
        self.assertEqual(traces("<p>rules in <code>CLAUDE.md</code></p>")["text"], ["CLAUDE"])
        self.assertEqual(traces("<p>a claude/x branch</p>")["text"], ["claude"])
        self.assertEqual(traces('<pre class="mermaid">A("IO-86")</pre>')["mermaid"], ["IO-86"])
        self.assertEqual(traces("<title>ARCH-24/25 | neXtBSW</title>")["text"], ["ARCH-24"])

    def test_keeps_code_apart_and_spares_real_identifiers(self):
        found = traces("<pre><code># SYS-97: guard</code></pre><p>SSR-002, AN-015, FM-RTE-01, SM-59, DF-05</p>")
        self.assertEqual(found, {"text": [], "mermaid": [], "code": ["SYS-97"]})
        self.assertEqual(traces('<a href="#release-tool-69">x</a>')["text"], [])


@unittest.skipUnless(HAVE_MKDOCS, SKIP)
class PublicSiteIsClean(unittest.TestCase):
    """The real site of the open export: no internal task ID, no CLAUDE reference outside code blocks."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nx-site-clean-"))
        cls.tree = REPO if site.is_export(REPO) else site.export_tree(REPO, cls.tmp / "export")
        cls.out = cls.tmp / "site"
        cls.done = site.build(cls.tree, cls.out, strict=True)
        cls.pages, cls.categories = {}, {}
        for path in sorted(cls.out.rglob("*.html")):
            page = path.read_text(encoding="utf-8")
            rel = path.relative_to(cls.out).as_posix()
            cls.pages[rel] = traces(page)
            cls.categories[rel] = category_traces(page)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_build_succeeds(self):
        self.assertEqual(self.done.returncode, 0, (self.done.stdout + self.done.stderr)[-3000:])
        self.assertGreater(len(self.pages), 200)

    def _report(self, kind: str) -> dict[str, list[str]]:
        return {rel: found[kind] for rel, found in self.pages.items() if found[kind]}

    def test_no_trace_in_the_text(self):
        bad = self._report("text")
        self.assertEqual(bad, {}, "\n".join(f"{rel}: {hits[:8]}" for rel, hits in bad.items()))

    def test_no_trace_in_mermaid_diagrams(self):
        bad = self._report("mermaid")
        self.assertEqual(bad, {}, "\n".join(f"{rel}: {hits}" for rel, hits in bad.items()))

    def _category(self, name: str) -> None:
        bad = {rel: found[name] for rel, found in self.categories.items() if found[name]}
        self.assertEqual(bad, {}, "\n".join(f"{rel}: {hits[:8]}" for rel, hits in bad.items()))

    def test_no_derived_finding_id(self):
        self._category("derived")

    def test_no_board_id(self):
        self._category("board_ids")

    def test_no_role_of_the_work_organisation(self):
        self._category("roles")

    def test_no_person_or_personal_date(self):
        self._category("persons")

    def test_no_decision_board(self):
        self._category("decision_board")

    def test_no_private_link(self):
        self._category("private_links")

    def test_no_internal_planning_file(self):
        self._category("planning_files")

    def test_code_blocks_are_counted(self):
        code = self._report("code")
        total = sum(len(h) for h in code.values())
        sys.stderr.write(f"\n[site-clean] hits left in code blocks (unchanged by rule): {total} "
                         f"on {len(code)} page(s)\n")
        for rel, hits in sorted(code.items()):
            if not rel.startswith("en/"):
                sys.stderr.write(f"  {rel}: {len(hits)}\n")
        self.assertFalse([h for hits in code.values() for h in hits if h.lower() == "claude"])


if __name__ == "__main__":
    unittest.main()
