# SPDX-License-Identifier: Apache-2.0
"""Site sanitizer (SITE-CLEAN): internal task IDs and CLAUDE.md references out of the rendered website pages.

One test per rule with real lines of the published pages, and counter-tests: real document identifiers (SSR-, SM-,
AoU-, DF-, DFA-, AN-, CM-, CR-, EW-, F-, BT-, B16, FM-RTE-01, versions, registers) and code stay untouched; a sanitizer
that removes them makes these tests fail (CounterTests checks that the checks themselves bite).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.docgen import sanitize          # noqa: E402

clean = sanitize.clean_text


def page(text: str) -> str:
    return sanitize.clean_page(text)


class Brackets(unittest.TestCase):
    """Rule 1 and 2: brackets of IDs go, mixed brackets lose their ID items."""

    def test_id_only_brackets_go_with_the_space(self):
        self.assertEqual(clean("Hooks per instance (ARCH-67): an instance"), "Hooks per instance: an instance")
        self.assertEqual(clean("precondition is iso-q3 (SYS-60) unless marked"), "precondition is iso-q3 unless marked")
        self.assertEqual(clean("the move (PLAT-10, ARCH-24/25)."), "the move.")
        self.assertEqual(clean("Status: 2026-09-17 (**DOC-07**). Written"), "Status: 2026-09-17. Written")
        self.assertEqual(clean("the hold for now (task SYS-118). That is"), "the hold for now. That is")
        self.assertEqual(clean("a range (SYS-237..240) and more"), "a range and more")
        self.assertEqual(clean("Standby (IO-68). Einzelheiten", "de"), "Standby. Einzelheiten")
        self.assertEqual(clean("decided (decision SYS-75, 2026-10-04)."), "decided.")

    def test_mixed_brackets_keep_the_rest(self):
        self.assertEqual(clean("checked (PLAT-53, C2) here"), "checked (C2) here")
        self.assertEqual(clean("Issue 2 of the DFA (draft, SYS-195, 2026-10-10, on dev-motor)"),
                         "Issue 2 of the DFA (draft, 2026-10-10, on the development branch)")
        self.assertEqual(clean("## Release publication (TOOL-69, gap N15)"), "## Release publication (gap N15)")
        self.assertEqual(clean("(S32K116, architecture section 4, IO-18 decision 2)"), "(S32K116, architecture section 4)")
        self.assertEqual(clean("(release 2026-09-30, DOC-38);"), "(release 2026-09-30);")
        self.assertEqual(clean("H9; the LVD **reset** is used since SYS-125, SM-59"),
                         "H9; the LVD **reset** is used since an earlier change, SM-59")
        self.assertEqual(clean("(TOOL-46: right after opening, the VN1610 delivers a burst)"),
                         "(right after opening, the VN1610 delivers a burst)")

    def test_brackets_over_two_lines(self):
        self.assertEqual(clean("the safety plan (SYS-129, SYS-130,\n  SYS-135, SM-59), and more"),
                         "the safety plan (SM-59), and more")


class Prose(unittest.TestCase):
    """Rule 4 and 5: labels go, IDs in running text go or become a neutral phrase."""

    def test_labels_at_line_and_item_start(self):
        self.assertEqual(clean("- **SYS-28:** SPI-1 and SPI-2 (section 6)"), "- SPI-1 and SPI-2 (section 6)")
        self.assertEqual(clean("- TOOL-272: `statistics()`, `reportedKinds()`"), "- `statistics()`, `reportedKinds()`")
        self.assertEqual(clean("IO-79: since TOOL-219 a C component states"),
                         "Since an earlier change a C component states")
        self.assertEqual(clean("- SYS-113 limits: the held safe states"), "- Limits: the held safe states")

    def test_neutral_phrases(self):
        self.assertEqual(clean("issue 1 was **SYS-19**, issue 2"), "issue 1 was an earlier change, issue 2")
        self.assertEqual(clean("of SYS-60 (safety plan)"), "of the safety plan")
        self.assertEqual(clean("section 3.7 gives the values since SYS-40."),
                         "section 3.7 gives the values since an earlier change.")
        self.assertEqual(clean("moves into a configuration level with SYS-25; then B applies"),
                         "moves into a configuration level with a separate task; then B applies")
        self.assertEqual(clean("*closed by SYS-133 (2026-09-30):* text"), "*closed by an earlier change (2026-09-30):* text")
        self.assertEqual(clean("ARCH-25 moved them there without changing their code."),
                         "An earlier change moved them there without changing their code.")
        self.assertEqual(clean("When SYS-61, SYS-71 or SYS-92 of this wave add tables"),
                         "When related tasks of this work step add tables")
        self.assertEqual(clean("Stand vor IO-90.", "de"), "Stand vor einer späteren Änderung.")
        self.assertEqual(clean("Since BL-42 (safety review 2026-10-07) the hook"),
                         "Since an earlier change (safety review 2026-10-07) the hook")
        self.assertEqual(clean("7.2.1, Table 8). Before BL-41 `leave` left it alone"),
                         "7.2.1, Table 8). Before a later change `leave` left it alone")
        self.assertEqual(clean("descriptors: SYS-21 must fit the model"), "descriptors: a related task must fit the model")
        self.assertEqual(clean("SYS-111 (booked done) makes pincheck report"),
                         "A related task (booked done) makes pincheck report")

    def test_id_with_description_in_a_list(self):
        self.assertEqual(clean("Implementation: PLAT-54 (move of the family port), PLAT-55 (startup in one place)."),
                         "Implementation: move of the family port, startup in one place.")
        self.assertEqual(clean("Implementation: PLAT-55 (startup in one place), ARCH-27 (hard\nfault handler), PLAT-35 (CRx)."),
                         "Implementation: startup in one place, hard\nfault handler, CRx.")

    def test_claude_inside_a_bracket_over_two_lines(self):
        self.assertEqual(clean("workaround (guiding\nprinciple in `CLAUDE.md`, review questions in `x.md`, section 11)."),
                         "workaround (guiding\nprinciple in the project rules, review questions in `x.md`, section 11).")

    def test_ids_in_lists_and_nouns(self):
        self.assertEqual(clean("follow-up task IO-97; implemented by IO-97"),
                         "follow-up task; implemented by an earlier change")
        self.assertEqual(clean("Extend the `PLAT-91` list of safety units"), "Extend the list of safety units")
        self.assertEqual(clean("RTE42-1 / TOOL-257 (stack of the motor task)"), "stack of the motor task")
        self.assertEqual(clean("**Board check SYS-204 (formulated, not run).**"), "**Board check (formulated, not run).**")
        self.assertEqual(clean("**SYS-144 P2** (an observation)"), "**P2** (an observation)")
        self.assertEqual(clean("on board and DF-51"), "on board and DF-51")

    def test_pre_prefix(self):
        self.assertEqual(clean("counter-test with the pre-SYS-37 code"), "counter-test with the earlier code")

    def test_finding_suffix_goes_with_the_id(self):
        self.assertEqual(clean("the proposal (SYS-188-P2) holds"), "the proposal holds")


class Claude(unittest.TestCase):
    """Rule 6: CLAUDE.md references."""

    def test_reason_brackets_go(self):
        self.assertEqual(clean("Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17)."),
                         "Language: English, like the rest of `doc/`.")
        self.assertEqual(clean("the submodule is never changed, CLAUDE.md). A change"),
                         "the submodule is never changed, CLAUDE.md). A change".replace(", CLAUDE.md", ""))
        self.assertEqual(clean("on by the user (CLAUDE.md), and only after"), "on by the maintainer, and only after")
        self.assertEqual(clean("partition changed (CLAUDE.md hardware rule) |"), "partition changed (hardware rule) |")
        self.assertEqual(clean("breakpoint (J-Link rules of CLAUDE.md)"), "breakpoint (J-Link rules of the project)")

    def test_mention_in_a_sentence(self):
        self.assertEqual(clean("principle in [`CLAUDE.md`](../../CLAUDE.md), review questions"),
                         "principle in the project rules, review questions")
        self.assertEqual(clean("This is a project rule in `CLAUDE.md` (\"Das Submodul\")"),
                         "This is a project rule (\"Das Submodul\")")
        self.assertEqual(clean("before this task started, as CLAUDE.md requires) and"),
                         "before this task started, as the project rules require) and")
        self.assertEqual(clean("the written project rules `CLAUDE.md` and more"), "the written project rules and more")

    def test_lists_of_internal_files(self):
        self.assertEqual(clean("| Internal planning files (`doc/todo.yaml`, `doc/TODO.md`, `CLAUDE.md`) | German |"),
                         "| Internal planning files | German |")
        self.assertEqual(clean("`todo.yaml`, `TODO.md`, `koordinator-stand.md` and `../CLAUDE.md` are internal"),
                         "the task list and the project rules are internal")

    def test_branch_and_folder_names(self):
        self.assertEqual(clean("(branch `agent/<ID>` or `claude/<id>`)"), "(a task branch or a session branch)")
        self.assertNotIn("claude", clean("in a worktree under `.claude/worktrees/…` it is").lower())


class Tables(unittest.TestCase):
    """Rule 3: cells lose their IDs, emptied cells show '–', an ID-only column goes."""

    TABLE = ("| AN | Source | Task | Finding | State |\n"
             "|----|--------|------|---------|-------|\n"
             "| AN-087 | `cm-plan.md G3` | SYS-68 | Release tags are not signed (SYS-111) | review |\n"
             "| AN-161 | `reviews/CR-02-1 F-09` | DOC-55 | no longer fits since SYS-24/SYS-153 (M7) | closed |\n"
             "| AN-200 | `dfa.md DFA-10` | — | DF-55 holds | open |\n")

    def test_id_only_column_is_removed(self):
        out = page(self.TABLE)
        self.assertNotIn("Task", out)
        self.assertEqual(out.splitlines()[2], "| AN-087 | `cm-plan.md G3` | Release tags are not signed | review |")
        self.assertIn("| AN-161 | `reviews/CR-02-1 F-09` | no longer fits since earlier changes (M7) | closed |", out)
        self.assertIn("| AN-200 | `dfa.md DFA-10` | DF-55 holds | open |", out)
        self.assertEqual(out.splitlines()[1].count("|"), 5)

    def test_emptied_cell_shows_a_dash(self):
        out = page("| Row | Closed by | Note |\n|---|---|---|\n| R1 | SYS-68 | text |\n| R2 | open | x |\n")
        self.assertIn("| R1 | – | text |", out)
        self.assertIn("| Closed by |", out)

    def test_pipes_in_code_survive(self):
        out = page("| A | B |\n|---|---|\n| `a | b` | SYS-1 x |\n")
        self.assertIn("| `a | b` | X |", out)


class Page(unittest.TestCase):
    def test_heading_keeps_its_anchor(self):
        out = page("# Title\n\n## Release publication (TOOL-69, gap N15)\n")
        self.assertIn("## Release publication (gap N15) {#release-publication-tool-69-gap-n15}", out)

    def test_heading_with_only_ids(self):
        out = page("# T\n\n### 7.5 SYS-28, SYS-74, DOC-19\n")
        self.assertIn("### 7.5 Further items {#75-sys-28-sys-74-doc-19}", out)
        out = page("# T\n\n### 7.2 SYS-50 and SYS-55 — clock plan\n")
        self.assertIn("### 7.2 Clock plan {#", out)

    def test_code_blocks_stay(self):
        text = "Text (SYS-1).\n\n```sh\npython x.py  # SYS-97: guard\n```\n"
        self.assertEqual(page(text), "Text.\n\n```sh\npython x.py  # SYS-97: guard\n```\n")

    def test_inline_code_and_link_targets_stay(self):
        text = "See [`../proposals/IO-32-x.md`](../proposals/IO-32-x.md) and `test/host/test_io67_mutations.py`.\n"
        self.assertEqual(page(text), "See [a design note](../proposals/IO-32-x.md) and `test/host/test_io67_mutations.py`.\n")
        self.assertEqual(page("Run `tools/test -k SYS-75-board` now.\n"), "Run `tools/test -k SYS-75-board` now.\n")

    def test_inline_code_named_after_a_task(self):
        self.assertEqual(clean("Board proof open (checklist in `doc/changelog.d/IO-67.md`)."),
                         "Board proof open (checklist in a change-log fragment).")
        self.assertEqual(clean("The dependency list `[SYS-19, SYS-20, PLAT-74]` is right"), "The dependency list is right")
        self.assertEqual(clean("merge `dev-motor` → `agent/SYS-204`"), "merge the development branch → a task branch")

    def test_code_span_over_two_lines(self):
        self.assertEqual(clean("stops with `needs --yes when not\ninteractive` before anything is sent (TOOL-53). An"),
                         "stops with `needs --yes when not\ninteractive` before anything is sent. An")

    def test_mermaid_labels(self):
        text = ('```mermaid\nflowchart LR\n  A --> SUP("Safe Supervisor<br/>HW_SHUTOFF je Timer, IO-86")\n'
                '  P -. "PLAT-18" .-> F("flash")\n  opt Filter gesetzt (COM-07/COM-39)\n```\n')
        out = page(text)
        self.assertIn('SUP("Safe Supervisor<br/>HW_SHUTOFF je Timer")', out)
        self.assertIn("P -.-> F(\"flash\")", out)
        self.assertIn("  opt Filter gesetzt\n", out)

    def test_mermaid_sequence_message_and_dash_items(self):
        text = "```mermaid\nsequenceDiagram\n  R-->>H: lock released (reset and hold never return, no line - SYS-175)\n```\n"
        self.assertIn("  R-->>H: lock released (reset and hold never return, no line)\n", page(text))
        self.assertEqual(clean("the window (no line – SYS-175) stays"), "the window (no line) stays")
        self.assertEqual(clean("the window (no line, SYS-175) stays"), "the window (no line) stays")
        self.assertEqual(clean("the read-back - SYS-175) stays"), "the read-back) stays")

    def test_html_comment_with_ids_goes(self):
        self.assertEqual(page("a\n<!-- DOC-61, tag v1.0.0-rc1 -->\nb\n"), "a\nb\n")


class FindingIds(unittest.TestCase):
    """Stage 2 (1): finding IDs derived from a task, review points and lower-case board IDs."""

    def test_derived_finding_ids(self):
        self.assertEqual(clean("the limit of 2 cycles decided a (S211-2, safety review Motor 12, 2026-10-11), nothing"),
                         "the limit of 2 cycles decided a (safety review Motor 12, 2026-10-11), nothing")
        self.assertEqual(clean("(safety review IO77-S2 b): the target"), "(safety review): the target")
        self.assertEqual(clean("Extending the set is a review point (SYS192-P2)."), "Extending the set is a review point.")
        self.assertIn("| – | text |", page("| P | B |\n|---|---|\n| TOOL257-1 … TOOL257-3 | text |\n| x | y |\n"))
        self.assertEqual(clean("startup holds off\" (S202-2 a), \"the window"), "startup holds off\", \"the window")

    def test_review_points_with_a_decision_letter(self):
        self.assertEqual(clean("review „Paket K“ 2026-10-13: K-2 a S1 + S2 for a deviation"),
                         "review „Paket K“ 2026-10-13: S1 + S2 for a deviation")
        self.assertEqual(clean("accepted (S-1 a/S-6 a; S-2 a no event)"), "accepted (no event)")

    def test_board_ids(self):
        self.assertEqual(clean("pending (cloud-1001-sys-63) | 1"), "pending | 1")
        self.assertEqual(clean("1. On a release candidate (decision tool229-cm-regeln-1011 a of the user; replaces it"),
                         "1. On a release candidate (a project decision; replaces it")
        self.assertEqual(clean("Superseded in part on 2026-09-29 (decision `csec-pgmpart-rueckweg`, CLAUDE.md):"),
                         "Superseded in part on 2026-09-29:")
        self.assertEqual(clean("Report only, by decision `sys37-postfach-abschaltend` (a report never switches off)"),
                         "Report only, by a project decision (a report never switches off)")
        self.assertEqual(clean("A baseline is - or, **[Decided tool229-cm-regeln-1011 a]** a candidate"),
                         "A baseline is - or, **[Decided]** a candidate")

    def test_board_item_in_plain_text(self):
        self.assertEqual(clean("Decision rtos-u1-anfrage: stay on FreeRTOS and qualify it."),
                         "Decision: stay on FreeRTOS and qualify it.")
        self.assertEqual(clean("the decision fail-safe stays"), "the decision fail-safe stays")

    def test_test_names_and_versions_stay(self):
        self.assertEqual(clean("release decision `v1.0.0-rc2`: the tag"), "release decision `v1.0.0-rc2`: the tag")
        self.assertEqual(clean("checked by `safe-io-mutations` and `fpu-stacking-check`"),
                         "checked by `safe-io-mutations` and `fpu-stacking-check`")
        self.assertEqual(clean("the resources R-02, R-03 and R-17"), "the resources R-02, R-03 and R-17")


class WorkOrganisation(unittest.TestCase):
    """Stage 2 (a)-(e): roles, persons, the decision board, private links and the planning files."""

    def test_roles_and_branches(self):
        self.assertEqual(clean("| Close | the coordinator at the merge of the closing task |"),
                         "| Close | the maintainer at the merge of the closing task |")
        self.assertEqual(clean("on `dev-motor`, not checked in the accepted issue"),
                         "on the development branch, not checked in the accepted issue")
        self.assertEqual(clean("| 1 (update) | 2026-10-03 | integ/r1 (`55bdbe08`) | text |"),
                         "| 1 (update) | 2026-10-03 | an integration branch (`55bdbe08`) | text |")
        self.assertEqual(clean("Readers: the coordinator, every agent that writes C"),
                         "Readers: the maintainer, every contributor that writes C")
        self.assertEqual(clean("| Author | DOC-19 (agent session of wave 2026-09-30, written on the user's behalf — author side) |"),
                         "| Author | (written on the maintainer's behalf — author side) |")
        self.assertEqual(clean("the safety review of the night 2026-10-01, point NVM12-S1 b: one lock"),
                         "the safety review of 2026-10-01, point: one lock")

    def test_night_and_role_formats(self):
        self.assertEqual(clean("consistency after the night merges: BEF-004"), "consistency after the merges: BEF-004")
        self.assertEqual(clean("Board proofs for the mechanisms merged in the night of 2026-09-21: a load test"),
                         "Board proofs for the mechanisms merged on 2026-09-21: a load test")
        self.assertEqual(clean("board 2026-10-01 night passed: DID 0xF1F1"), "board 2026-10-01 passed: DID 0xF1F1")
        self.assertEqual(clean("written in the\nnight without the user"), "written without the maintainer")
        self.assertEqual(clean("The documents of the running cloud wave (`a.md`) start"),
                         "The documents of the current work step (`a.md`) start")
        self.assertEqual(clean('"Reviewer" = `coordinator (merge <task>)` for a closure'),
                         '"Reviewer" = `maintainer (merge <task>)` for a closure')
        self.assertEqual(clean("statuses after the cloud waves (P2-03)"), "statuses after the earlier work steps (P2-03)")
        self.assertEqual(clean("the TD2 route with validation is the **user's** (TN2)."),
                         "the TD2 route with validation is the **maintainer's** (TN2).")

    def test_role_outside_the_safety_documents(self):
        text = "OpenBSW does not have it. The user decided to stay close instead.\n"
        self.assertEqual(sanitize.clean_page(text, path="doc/architecture/openbsw.md"),
                         "OpenBSW does not have it. The maintainer decided to stay close instead.\n")
        self.assertEqual(sanitize.clean_page(text, path="doc/safety/concept.md"),
                         "OpenBSW does not have it. The safety manager decided to stay close instead.\n")
        self.assertEqual(clean("written by an agent on the user's behalf"), "written by a contributor on the maintainer's behalf")
        self.assertEqual(clean("To keep the parallel merges of wave cloud-0930 free of conflicts"),
                         "To keep the parallel merges of the work step free of conflicts")
        self.assertEqual(clean("clamp the negative half wave). The estimator"), "clamp the negative half wave). The estimator")

    def test_persons_become_roles(self):
        self.assertEqual(clean("Readers: the user as safety manager (decides the closure)"),
                         "Readers: the safety manager (decides the closure)")
        self.assertEqual(clean("re-check of CM-02 by the user (D-01, without independence)"),
                         "re-check of CM-02 by the safety manager (D-01, without independence)")
        self.assertEqual(clean("Closed by the re-check of CR-02-1 (user, D-01, 2026-10-04, R5)"),
                         "Closed by the re-check of CR-02-1 (safety manager, D-01, 2026-10-04, R5)")
        self.assertEqual(clean("**Software (decision of the user 2026-09-18): no restart after a debug halt.**"),
                         "**Software (a project decision): no restart after a debug halt.**")
        self.assertEqual(clean("**Endstufe generisch (Nutzer 2026-09-16):** Der Treiber", "de"),
                         "**Endstufe generisch:** Der Treiber")
        self.assertEqual(clean("Der Nutzer entschied am 2026-09-23, dass", "de"), "Der Maintainer entschied am 2026-09-23, dass")
        self.assertEqual(clean("the pin cases **only with the user** (MOT1_GD_DRV_EN)"),
                         "the pin cases **only with the maintainer** (MOT1_GD_DRV_EN)")

    def test_users_that_are_no_person_stay(self):
        for line in ("the first DMA user of the bus", "its user guide", "a user interface", "per-user application data",
                     "The user test asked for edge and priority", "| Interface | Provider → user | Form |"):
            self.assertEqual(clean(line), line)

    def test_uds_sessions_and_issue_dates_stay(self):
        for line in ("Name of the session, unique in the file", "### 6.4 Flow back from an XCP session",
                     "| Issue | 2, 2026-10-04 (issue 1, 2026-09-30) |", "a programming session (10 02)"):
            self.assertEqual(clean(line), line)

    def test_decision_board(self):
        self.assertEqual(clean("| Accept a residual | the user as safety manager on the decision board |"),
                         "| Accept a residual | the safety manager |")
        self.assertEqual(clean("a row in section 4 and an item on the decision board; the safety manager decides"),
                         "a row in section 4 and a decision item; the safety manager decides")
        self.assertEqual(clean("(decision board, F1/F3, RM46 option A)"), "(F1/F3, RM46 option A)")

    def test_private_links(self):
        self.assertEqual(clean("the safety review Motor 12 (VUvnHk7QPZNqDUgAAYoMjr) decided all 11 points"),
                         "the safety review Motor 12 decided all 11 points")
        self.assertEqual(clean("(DOC-93, safety review DFA issue 2, artefact MHjJPvfsxuWteo8iU8JMXf)."),
                         "(safety review DFA issue 2).")
        self.assertEqual(clean("see [the board](https://claude" ".ai/artifact/AbCdEfGhIjKlMnOpQrStUv) for it"),
                         "see the board for it")
        self.assertEqual(clean("`gdTmc6200Configuration` and `customHardFaultHandler`"),
                         "`gdTmc6200Configuration` and `customHardFaultHandler`")
        self.assertEqual(clean("measurement scripts used to live in the scratchpad; when"),
                         "measurement scripts used to live in a private folder; when")

    def test_planning_files(self):
        self.assertEqual(clean("| Decisions | `doc/todo.yaml`, `CHANGELOG.md` | permanent |"),
                         "| Decisions | `CHANGELOG.md` | permanent |")
        self.assertEqual(clean("owner = an open task of `doc/todo.yaml`, or a role"), "owner = an open task of the task list, or a role")
        self.assertEqual(clean("(transcribed into `doc/todo.yaml`; P7 for merge and tag)"),
                         "(transcribed into the task list; P7 for merge and tag)")
        out = page("- **Planning and workflow** — `todo.yaml`, `TODO.md` and `../CLAUDE.md` are internal working\n"
                   "  files, not documentation for readers.\n- **Hardware documents** — datasheets.\n")
        self.assertEqual(out, "- **Hardware documents** — datasheets.\n")


class Untouched(unittest.TestCase):
    """Rule 7: real document identifiers and everything else stay."""

    LINES = [
        "| SSR-002 | AN-015 | SM-59 | AoU-44 | DF-05 | DFA-12 | CM-05 | CR-05-1 | EW-01 | F-01 | BT-S1-03 | B16 |",
        "FM-RTE-01, FM-NVM-02 and FM-BL-04 (AoU-ASA-c) are failure modes; ISO 26262-6 clause 9.4.3.",
        "Version v1.0.0-rc1, bootloader 0.3.11, register SIM_FTMOPT0, CRC-16/CCITT, S32K144, UJA1131.",
        "Decision Z7 of CM-02 (SSR-081/SSR-084 build checks) (AN-130, DF-51)",
        "A IO pin, the SYS clock and an RTE task (no ID: no number)",
    ]

    def test_lines_without_internal_ids_are_unchanged(self):
        for line in self.LINES:
            self.assertEqual(clean(line), line)
            self.assertEqual(page(line + "\n"), line + "\n")

    def test_only_the_eleven_prefixes(self):
        self.assertEqual(sanitize.PREFIXES, ("ARCH", "BL", "COM", "DOC", "IO", "NVM", "PLAT", "RTE", "SYS", "TOOL", "VER"))
        self.assertEqual(clean("(SSR-002, SYS-75, AN-015)"), "(SSR-002, AN-015)")


class CounterTests(unittest.TestCase):
    """The checks bite: a sanitizer that removes real identifiers fails Untouched, one that leaves IDs fails."""

    def test_overeager_sanitizer_is_caught(self):
        greedy = lambda text, lang="en", cell=False: re.sub(r"\b[A-Z]{2,4}-\d+\b", "", text)  # noqa: E731
        failed = [line for line in Untouched.LINES if greedy(line) != line]
        self.assertTrue(failed, "a greedy sanitizer must change the protected lines")

    def test_lazy_sanitizer_is_caught(self):
        self.assertTrue(sanitize.ID_RE.search("checked (PLAT-53, C2) here"))
        self.assertFalse(sanitize.ID_RE.search(clean("checked (PLAT-53, C2) here")))

    def test_check_pattern_spares_failure_mode_ids(self):
        self.assertFalse(sanitize.CHECK_RE.search("FM-RTE-01 and FM-BL-04"))
        self.assertTrue(sanitize.CHECK_RE.search("closed by SYS-133"))
        self.assertTrue(sanitize.CHECK_RE.search("**SYS-19**"))


if __name__ == "__main__":
    unittest.main()
