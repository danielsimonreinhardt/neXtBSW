# SPDX-License-Identifier: Apache-2.0
"""Host test of the uniform board-rule marker in console help texts (SYS-17).

Commands that may only be used with a person at the board carry one marker at the end of their help text,
"(board rule)". tools/docgen reads it for the "Board rule" column of doc/reference/console.md and reports two
kinds of gap: a help text that still uses one of the wordings from before SYS-17, and a command that a board
README lists as a board rule while its help text carries no marker at all - on the console that rule would be
invisible.

The synthetic source tree of test_console.py is reused and varied per case. One run checks the real sources: the
restricted commands of the eval board carry the marker, and no neXtBSW help text uses an old wording any more.

    .venv/Scripts/python tools/docgen/test_console_marker.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.docgen import console  # noqa: E402
from tools.docgen.test_console import PROJECTS, TREE, make_tree  # noqa: E402

FOO = "platforms/fam/port/main/src/systems/FooSystem.cpp"
BAR = "libs/bsw/barDrv/src/BarSystem.cpp"
# Board rules of boards/evalboard_s32k144/README.md that are still without the marker in their help text. The
# help text of `ecu sleep` lives in ecu/application/src/systems/EcuStateSystem.cpp, outside the paths of SYS-17
# (reported to the coordinator). The test passes before and after that one line is changed: nothing else may
# appear here.
KNOWN_UNMARKED = {"ecu sleep"}


def collect_tree(root: Path, *changes: tuple[str, str, str]) -> console.ConsoleDoc:
    """Collect the test tree with single help texts replaced: (file, old text, new text) per change."""
    tree = dict(TREE)
    for rel, old, new in changes:
        assert old in tree[rel], old
        tree[rel] = tree[rel].replace(old, new)
    return console.collect(make_tree(root, tree), PROJECTS)


SLEEP_MARKED = (FOO, "enter sleep (supply off)", "enter sleep, supply off (board rule)")
ZAP_MARKED = (FOO, "zap - only with the user at the board", "zap (board rule)")
ON_MARKED = (BAR, "switch on (load supply! board rules apply)", "switch on, load supply (board rule)")


def marker_gaps(doc: console.ConsoleDoc) -> list[tuple[str, str]]:
    return [(w, t) for w, t in console.gaps(doc) if "board rule" in t and "marker" in t]


class MarkerTest(unittest.TestCase):
    def test_marker_of_prefers_the_uniform_marker(self):
        self.assertEqual(console.marker_of("switch on, load supply (board rule)"), console.BOARD_RULE_MARKER)
        self.assertEqual(console.marker_of("zap - only with the user at the board"),
                         "only with the user at the board")
        self.assertIsNone(console.marker_of("switch off"))
        self.assertIsNone(console.marker_of(""))

    def test_legacy_marker_only_without_the_uniform_one(self):
        self.assertEqual(console.legacy_marker_of("switch on (load supply! board rules apply)"),
                         "board rules apply")
        # a help text that carries the marker is never reported, whatever else it says
        self.assertIsNone(console.legacy_marker_of("board rules apply, so: (board rule)"))
        self.assertIsNone(console.legacy_marker_of("switch off"))

    def test_uniform_marker_is_read_and_leaves_no_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = collect_tree(Path(tmp), SLEEP_MARKED, ZAP_MARKED, ON_MARKED)
            text = console.render(doc)
            self.assertIn("| `sleep` | `foo sleep` | enter sleep, supply off (board rule) |  | yes (help text);",
                          text)
            self.assertIn("| `on` | `<bar> on` | switch on, load supply (board rule) |  | yes (help text);", text)
            self.assertEqual(marker_gaps(doc), [])
            # the page explains the marker, and names the old wordings as reported
            self.assertIn(f"“{console.BOARD_RULE_MARKER}”", text)
            for legacy in console.LEGACY_MARKERS:
                self.assertIn(f"“{legacy}”", text)

    def test_old_wording_still_marks_but_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = collect_tree(Path(tmp))                    # the tree as it is: both old wordings
            self.assertIn("yes (help text)", console.render(doc))
            reported = {w.split()[-1]: t for w, t in marker_gaps(doc)}
            self.assertIn("zap", reported)                   # "only with the user at the board"
            self.assertIn("on", reported)                    # "board rules apply"
            self.assertIn("not as the marker `(board rule)`", reported["on"])
            self.assertIn(BAR, [w.split(":")[0] for w, _ in marker_gaps(doc)])

    def test_board_rule_without_a_marker_in_the_help_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = collect_tree(Path(tmp))
            # `foo sleep` is in the list of board b1, its help text says nothing
            found = [(w, t) for w, t in marker_gaps(doc) if w.endswith("foo sleep")]
            self.assertEqual(len(found), 1, marker_gaps(doc))
            where, what = found[0]
            self.assertTrue(where.startswith(FOO + ":"), where)
            self.assertIn("board rule `foo sleep` of `b1`", what)
            self.assertIn("on the console the rule is invisible", what)
            # the page still marks it from the README, the gap is only about the help text
            self.assertIn("yes on [`b1`](../../boards/b1/README.md) as `foo sleep`", console.render(doc))

    def test_no_gap_once_the_marker_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = collect_tree(Path(tmp), SLEEP_MARKED)
            self.assertEqual([w for w, _ in marker_gaps(doc) if w.endswith("foo sleep")], [])


@unittest.skipUnless((REPO / console.OPENBSW / "libs").is_dir(), "external/openbsw not checked out")
class RealMarkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = console.collect_real()

    def help_of(self, group: str, command: str) -> str:
        g = next(g for g in self.doc.groups if (g.name or g.module) == group)
        return next(c for c in g.commands if c.name == command).help

    def test_restricted_commands_carry_the_marker(self):
        for group, command in (("sbc", "sleep"), ("sbc", "failstart"),
                               ("highSideSwitch", "on"), ("gdTmc6200", "enable")):
            text = self.help_of(group, command)
            self.assertIn(console.BOARD_RULE_MARKER, text, f"{group} {command}: {text}")

    def test_no_neXtBSW_help_text_uses_an_old_wording(self):
        offenders = [(g.source, c.name, c.help) for g in self.doc.groups if g.origin == "neXtBSW"
                     for c in g.commands if console.legacy_marker_of(c.help)]
        self.assertEqual(offenders, [], "SYS-17: use the marker `(board rule)` instead")

    def test_every_board_rule_of_a_readme_is_visible_on_the_console(self):
        unmarked = set()
        for rules in self.doc.boards:
            for item, (anchor, name) in rules.resolved.items():
                g = next(g for g in self.doc.groups if g.anchor == anchor)
                cmd = next(c for c in g.commands if c.name == name)
                if not console.marker_of(cmd.help):
                    unmarked.add(item)
        self.assertLessEqual(unmarked, KNOWN_UNMARKED,
                             "a command a board README restricts has no marker in its help text: "
                             + ", ".join(sorted(unmarked - KNOWN_UNMARKED)))


if __name__ == "__main__":
    unittest.main()
