# SPDX-License-Identifier: Apache-2.0
"""Host tests of distcheck and the export (PLAT-50).

Every test builds its own small tree with its own rules (test profile, not the repository): allowed and forbidden
examples, and leaks built in on purpose - each rule has to find its leak with path and line, and the clean tree has
to pass. The last class runs the real rules against the real repository (classification complete, no crash).

    python -m unittest tools/distcheck/test_distcheck.py
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.distcheck import __main__ as cli            # noqa: E402
from tools.distcheck import distcheck as core          # noqa: E402
from tools.distcheck import exporter                   # noqa: E402

RULES = r"""
version: 1
pattern_file: private/patterns.yaml
profiles:
  open: { licenses: [Apache-2.0, MIT, BSD-3-Clause] }
  nda:  { licenses: [Apache-2.0, MIT, BSD-3-Clause, LicenseRef-Test-NDA] }
suffixes:
  source: [.c, .h, .py, .cmake]
  binary: [.pdf, .bin]
  no_text_scan: [.pdf, .bin]
content_patterns:
  - { id: confidential, regex: "(?<!doc/)\\bconfidential\\b(?!/)", text: "stamp" }
secrets:
  - { id: key, regex: "SECRET_KEY\\s*=?\\s*0x[0-9A-Fa-f]{4,}", text: "key" }
  - { id: pem, regex: "-----BEGIN [A-Z ]*PRIVATE KEY-----", text: "pem" }
  - { id: home, regex: "[A-Za-z]:[\\\\/]Users[\\\\/]", text: "home", severity: warning }
evidence:
  files: ["db/**/*.yaml"]
  forbidden_sources:
    - { id: vendor_ide, regex: "\\[IDE\\]", text: "vendor IDE files" }
paths:
  - { glob: "**", class: private }
  - { glob: "src/**", class: open, license: Apache-2.0 }
  - { glob: "src/third/**", class: open, license: MIT }
  - { glob: "secret/**", class: nda, license: LicenseRef-Test-NDA }
  - { glob: "internal/**", class: private }
  - { glob: "db/**", class: open, license: Apache-2.0 }
  - { glob: "docs/*.pdf", class: open, license: Apache-2.0, binary_ok: true }
  - { glob: "docs/**", class: open, license: Apache-2.0 }
  - { glob: "boards/**", class: private }
  - { glob: "boards/example/**", class: open, license: Apache-2.0 }
  - { glob: "projects/**", class: open, license: Apache-2.0 }
  - { glob: "projects/own/**", class: private }
  - { glob: "tests.yaml", class: open, license: Apache-2.0 }
  - { glob: "Presets.json", class: open, license: Apache-2.0, cleaned: true }
  - { glob: "CHANGELOG.md", class: private, cleaned: true }
  - { glob: "NOTES.md", class: private, cleaned: true }
  - { glob: "todo.yaml", class: private, cleaned: true }
  - { glob: "LICENSE", class: open, license: Apache-2.0 }
  - { glob: "NOTICE", class: open, license: Apache-2.0 }
  - { glob: "CMakeLists.txt", class: open, license: Apache-2.0 }
export:
  project: TestBSW
  copyright: "2026 Tester"
  license: Apache-2.0
  required_files: [LICENSE, NOTICE]
  generate:
    LICENSE: { text: "Apache License 2.0 (test)" }
    NOTICE: { kind: notice }
  clean:
    - { file: CHANGELOG.md, mode: replace, text: "# Changelog %%PROJECT%%\n" }
    - file: NOTES.md
      mode: sections
      note: "<!-- cleaned -->"
      drop_headings: ["Internal"]
      drop_bullets: ["board room"]
      drop_lines: ["workstation"]
    - file: todo.yaml
      mode: todo
      keep_fields: [id, title]
      drop_status: [done]
      drop_tasks_matching: ["NDA"]
    - file: Presets.json
      mode: json_drop
      arrays: [configurePresets]
      drop_matching: ["projects/own"]
    - file: tests.yaml
      mode: tests_yaml
      drop_matching: ["preset: own"]
"""

CLEAN_FILES = {
    "src/main.c": "// SPDX-License-Identifier: Apache-2.0\n#include \"api/api.h\"\nint main(void) { return 0; }\n",
    "src/api/api.h": "// SPDX-License-Identifier: Apache-2.0\n#pragma once\n",
    "src/third/lib.c": "/* SPDX-License-Identifier: MIT */\nint lib;\n",
    "src/tool.py": "# SPDX-License-Identifier: Apache-2.0\nimport json\n",
    "src/CMakeLists.txt": "add_library(core STATIC main.c)\n",
    "CMakeLists.txt": "add_subdirectory(src)\n",
    "secret/CMakeLists.txt": "add_library(nda_drv STATIC drv.c)\n",
    "secret/drv.c": "// SPDX-License-Identifier: LicenseRef-Test-NDA\nint drv;\n",
    "secret/include/nda/drv.h": "// SPDX-License-Identifier: LicenseRef-Test-NDA\n",
    "internal/plan.md": "internal planning, confidential\n",
    "db/chip.yaml": "flash: { size: 1024, source: \"[RM] 3.1\" }\n",
    "docs/guide.md": "Local NDA papers belong in doc/confidential/ only.\n",
    "docs/manual.pdf": "%PDF-1.4 not a real pdf\n",
    "boards/example/board.yaml": "board: { name: example }\n",
    "boards/own/board.yaml": "board: { name: own }\n",
    "projects/demo/project.yaml": "project: { name: demo }\nboard: example\n",
    "projects/own/project.yaml": "project: { name: own }\nboard: own\n",
    "CHANGELOG.md": "# Changelog\n\nsecret measurement of the own board\n",
    "NOTES.md": textwrap.dedent("""\
        # Notes

        ## Build

        ```sh
        # not a heading inside a fence
        make
        ```

        - keep this bullet
        - drop this bullet, it names the board room
          and continues here
        - line with workstation path is dropped

        ## Internal rules

        everything below goes
        """),
    "todo.yaml": textwrap.dedent("""\
        areas: { A: Area }
        tasks:
          - { id: A-1, title: open work, status: open, touches: [src/main.c], owner: agent }
          - { id: A-2, title: own board, status: open, touches: [boards/own] }
          - { id: A-3, title: derived from NDA sheet, status: open, touches: [src] }
          - { id: A-4, title: finished, status: done, touches: [src] }
        """),
    "Presets.json": json.dumps({"version": 6, "configurePresets": [
        {"name": "base", "hidden": True},
        {"name": "demo", "inherits": "base", "cacheVariables": {"P": "projects/demo/project.yaml"}},
        {"name": "own", "inherits": "base", "cacheVariables": {"P": "projects/own/project.yaml"}},
        {"name": "own-worktree", "inherits": ["own"]}]}, indent=4),
    "tests.yaml": textwrap.dedent("""\
        # list of tests
        groups: [python, gui]
        inventory:
          ignore: []

        tests:

          # --- group python
          - name: tool
            group: python
            args: [src/tool.py]

          - name: nda-driver
            group: gui
            sources: [secret/drv.c]

          - name: uses-driver-output
            args: [src/tool.py]
            after: [nda-driver]
            covers:
              - src/tool.py

          - name: target-own
            preset: own
        """),
}


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


class TreeCase(unittest.TestCase):
    """A fresh synthetic tree and rules per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()
        write_tree(self.root, CLEAN_FILES)
        self.rules_file = Path(self._tmp.name) / "rules.yaml"
        self.rules_file.write_text(RULES, encoding="utf-8")
        self.rules = core.load_rules(self.rules_file)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check(self, **kwargs) -> core.Report:
        kwargs.setdefault("use_git", False)
        kwargs.setdefault("private_patterns", self.root / "private" / "patterns.yaml")
        return core.check(self.root, self.rules, "open", **kwargs)

    def findings(self, report: core.Report, rule: str, severity: str = "error") -> list[core.Finding]:
        return [f for f in report.findings if f.rule == rule and f.severity == severity]

    def assert_found(self, report: core.Report, rule: str, path: str, line: int | None = None,
                     severity: str = "error") -> core.Finding:
        hits = [f for f in self.findings(report, rule, severity) if f.path == path and (line is None or f.line == line)]
        self.assertTrue(hits, f"{rule} {severity} in {path}:{line} erwartet, gefunden: "
                              + "; ".join(str(f) for f in report.findings if f.rule == rule))
        return hits[0]


class CleanTreeTest(TreeCase):
    def test_clean_tree_has_no_errors(self):
        report = self.check()
        self.assertEqual([], [str(f) for f in report.errors])
        self.assertEqual(len(CLEAN_FILES), report.files)

    def test_json_report_is_machine_readable(self):
        report = self.check()
        data = json.loads(report.to_json())
        self.assertEqual({"profile", "tree", "files", "exported", "by_class", "ran", "skipped", "errors", "warnings",
                          "findings"}, set(data))
        for finding in data["findings"]:
            self.assertEqual({"severity", "rule", "text", "path", "line", "detail"}, set(finding))


class ClassificationTest(TreeCase):
    def test_most_specific_glob_wins(self):
        tree = core.build_tree(self.root, self.rules, use_git=False)
        self.assertEqual("open", tree.cls["src/main.c"])
        self.assertEqual("MIT", tree.license_of["src/third/lib.c"])
        self.assertEqual("private", tree.cls["projects/own/project.yaml"])
        self.assertEqual("open", tree.cls["projects/demo/project.yaml"])
        self.assertEqual("nda", tree.cls["secret/drv.c"])

    def test_fallback_only_is_reported(self):
        write_tree(self.root, {"stray/file.txt": "x\n"})
        report = self.check()
        self.assert_found(report, "R1", "stray/file.txt", severity="warning")
        self.assertNotIn("stray/file.txt", core.build_tree(self.root, self.rules, use_git=False).exported("open"))

    def test_without_fallback_file_is_unclassified_error(self):
        self.rules.entries = [e for e in self.rules.entries if e.glob != "**"]
        write_tree(self.root, {"stray/file.txt": "x\n"})
        report = self.check()
        self.assert_found(report, "R1", "stray/file.txt")

    def test_manifest_beats_table(self):
        write_tree(self.root, {"src/drv/module.yaml": "module: drv\ndistribution: nda\n",
                               "src/drv/drv.c": "// SPDX-License-Identifier: Apache-2.0\n"})
        tree = core.build_tree(self.root, self.rules, use_git=False)
        self.assertEqual("nda", tree.cls["src/drv/drv.c"])
        self.assertIn("module.yaml", tree.source_of["src/drv/drv.c"])

    def test_bad_rules_are_rejected(self):
        self.rules_file.write_text("version: 1\npaths:\n  - { glob: 'a/**', class: secretish }\n", encoding="utf-8")
        with self.assertRaises(core.RulesError):
            core.load_rules(self.rules_file)


class SpdxTest(TreeCase):
    def test_forbidden_licence(self):
        write_tree(self.root, {"src/gpl.c": "// SPDX-License-Identifier: GPL-3.0-only\n"})
        finding = self.assert_found(self.check(), "R2", "src/gpl.c", 1)
        self.assertIn("GPL-3.0-only", finding.text)

    def test_license_ref_in_open_profile(self):
        write_tree(self.root, {"src/ref.c": "\n// SPDX-License-Identifier: LicenseRef-Test-NDA\n"})
        self.assert_found(self.check(), "R2", "src/ref.c", 2)

    def test_mention_in_prose_is_not_a_header(self):
        write_tree(self.root, {"src/readme.py": "# SPDX-License-Identifier: Apache-2.0\n"
                                                "x = 'SPDX-License-Identifier: GPL-2.0` in a quote'\n"})
        self.assertEqual([], self.findings(self.check(), "R2"))

    def test_missing_header_warns_with_table_licence(self):
        write_tree(self.root, {"src/plain.c": "int plain;\n"})
        report = self.check()
        self.assert_found(report, "R2", "src/plain.c", severity="warning")
        self.assertEqual([], self.findings(report, "R2"))


class GeneratorExceptionTest(TreeCase):
    """PLAT-120: a profile that allows the SPDX addition of the generator output exception needs its NOTICE section."""

    ADDITION = "AdditionRef-Test-Generator-Exception"

    def with_addition(self, notice: str) -> core.Report:
        self.rules_file.write_text(RULES.replace("open: { licenses: [Apache-2.0,",
                                                 f"open: {{ licenses: [{self.ADDITION}, Apache-2.0,"),
                                   encoding="utf-8")
        self.rules = core.load_rules(self.rules_file)
        write_tree(self.root, {"LICENSE": "Apache License 2.0 (test)\n", "NOTICE": notice})
        return self.check()

    def test_header_with_the_addition_passes_r2_when_allowed(self):
        self.rules_file.write_text(RULES.replace("open: { licenses: [Apache-2.0,",
                                                 f"open: {{ licenses: [{self.ADDITION}, Apache-2.0,"),
                                   encoding="utf-8")
        self.rules = core.load_rules(self.rules_file)
        write_tree(self.root, {"src/gen.h": f"// SPDX-License-Identifier: Apache-2.0 WITH {self.ADDITION}\n"})
        self.assertEqual([], [f for f in self.check().errors if f.rule == "R2"])

    def test_header_with_the_addition_is_refused_when_not_allowed(self):
        write_tree(self.root, {"src/gen.h": f"// SPDX-License-Identifier: Apache-2.0 WITH {self.ADDITION}\n"})
        self.assert_found(self.check(), "R2", "src/gen.h")

    def test_notice_without_the_section_is_an_error(self):
        report = self.with_addition("Apache-2.0 MIT BSD-3-Clause\n")
        self.assert_found(report, "R10", "NOTICE")

    def test_notice_with_the_section_passes(self):
        report = self.with_addition(f"Apache-2.0 MIT BSD-3-Clause\n## Generator output exception ({self.ADDITION})\n")
        self.assertEqual([], [f for f in report.errors if f.rule == "R10"])


class ContentTest(TreeCase):
    def test_public_pattern_with_line(self):
        write_tree(self.root, {"src/leak.c": "// SPDX-License-Identifier: Apache-2.0\n\n/* NXP Confidential */\n"})
        self.assert_found(self.check(), "R3", "src/leak.c", 3)

    def test_folder_name_is_no_stamp(self):
        self.assertEqual([], self.findings(self.check(), "R3"))    # docs/guide.md names doc/confidential/

    def test_allow_list_with_reason(self):
        write_tree(self.root, {"src/leak.c": "confidential\n"})
        self.rules.entries.append(core.PathRule(glob="src/leak.c", cls="open", license="Apache-2.0",
                                                allow=["confidential"], index=999))
        self.assertEqual([], self.findings(self.check(), "R3"))

    def test_private_list_missing_is_reported_not_silent(self):
        report = self.check()
        self.assertTrue(any("R3" in s and "fehlt" in s and "unvollständig" in s for s in report.skipped),
                        report.skipped)
        text = core.format_text(report)
        self.assertIn("UEBERSPRUNGEN: R3", text)

    def test_private_list_finds_document_numbers(self):
        write_tree(self.root, {"private/patterns.yaml": "patterns:\n  - { id: docno, regex: 'AN99\\d{3}', "
                                                        "text: 'NDA document' }\n",
                               "docs/notes.md": "see AN99123 chapter 4\n"})
        report = self.check()
        self.assert_found(report, "R3", "docs/notes.md", 1)
        self.assertFalse(any("Musterliste" in s for s in report.skipped))
        self.assertIn("R3: private Musterliste mit 1 Muster(n) geladen", report.ran)

    def test_private_list_is_not_exported(self):
        write_tree(self.root, {"private/patterns.yaml": "patterns: []\n"})
        tree = core.build_tree(self.root, self.rules, use_git=False)
        self.assertNotIn("private/patterns.yaml", tree.exported("open"))


class BinaryTest(TreeCase):
    def test_binary_without_entry(self):
        (self.root / "src" / "blob.bin").write_bytes(b"\0\1\2")
        self.assert_found(self.check(), "R4", "src/blob.bin")

    def test_pdf_with_entry_is_allowed(self):
        self.assertEqual([], [f for f in self.findings(self.check(), "R4") if f.path == "docs/manual.pdf"
                              and "Eintrag" in f.text])


class ReferenceTest(TreeCase):
    def test_include_into_nda_module(self):
        write_tree(self.root, {"src/use.c": "// SPDX-License-Identifier: Apache-2.0\n\n#include \"nda/drv.h\"\n"})
        finding = self.assert_found(self.check(), "R5", "src/use.c", 3)
        self.assertIn("secret/include/nda/drv.h", finding.detail)

    def test_ambiguous_include_is_not_reported(self):
        write_tree(self.root, {"src/include/nda/drv.h": "// SPDX-License-Identifier: Apache-2.0\n",
                               "src/use.c": "#include \"nda/drv.h\"\n"})
        self.assertEqual([], [f for f in self.findings(self.check(), "R5") if f.path == "src/use.c"])

    def test_link_to_nda_target_with_line(self):
        write_tree(self.root, {"src/CMakeLists.txt": "add_library(core STATIC main.c)\n\n"
                                                     "target_link_libraries(core\n  PRIVATE nda_drv)\n"})
        finding = self.assert_found(self.check(), "R5", "src/CMakeLists.txt", 3)
        self.assertIn("nda_drv", finding.text)

    def test_target_also_defined_in_the_profile_is_not_reported(self):
        """TOOL-244: a stand-in of an open build test defines the target as well - it exists in the export."""
        write_tree(self.root, {"src/CMakeLists.txt": "add_library(core STATIC main.c)\ntarget_link_libraries(core PRIVATE nda_drv)\n",
                               "src/stub/CMakeLists.txt": "add_library(nda_drv INTERFACE)\n"})
        self.assertEqual([], [f for f in self.findings(self.check(), "R5") if f.path == "src/CMakeLists.txt"])

    def test_external_target_is_not_reported(self):
        """TOOL-244: external_targets of the rules come from Eclipse OpenBSW in the real build."""
        write_tree(self.root, {"src/CMakeLists.txt": "add_library(core STATIC main.c)\ntarget_link_libraries(core PRIVATE nda_drv)\n"})
        self.assert_found(self.check(), "R5", "src/CMakeLists.txt", 2)
        self.rules.external_targets = {"nda_drv"}
        self.assertEqual([], [f for f in self.findings(self.check(), "R5") if f.path == "src/CMakeLists.txt"])

    def test_add_subdirectory_to_private_folder(self):
        write_tree(self.root, {"src/CMakeLists.txt": "add_library(core STATIC main.c)\nadd_subdirectory(../internal)\n"})
        self.assert_found(self.check(), "R5", "src/CMakeLists.txt", 2)

    def test_python_import_of_private_module(self):
        write_tree(self.root, {"internal/studio.py": "x = 1\n",
                               "src/gui.py": "# SPDX-License-Identifier: Apache-2.0\nfrom internal.studio import x\n"})
        self.assert_found(self.check(), "R5", "src/gui.py", 2)

    def test_project_on_private_board(self):
        write_tree(self.root, {"projects/demo/project.yaml": "project: { name: demo }\nboard: own\n"})
        self.assert_found(self.check(), "R5", "projects/demo/project.yaml", 2)

    def test_code_naming_a_private_directory(self):
        """A test that loads the project of the own board as fixture breaks in the export - warn before."""
        write_tree(self.root, {"projects/own_board_demo/project.yaml": "project: { name: x }\n",
                               "src/test_x.py": "# SPDX-License-Identifier: Apache-2.0\n\n"
                                                "DEMO = ROOT / 'projects' / 'own_board_demo'\n"})
        self.rules.entries.append(core.PathRule(glob="projects/own_board_demo/**", cls="private", index=998))
        self.assert_found(self.check(), "R5", "src/test_x.py", 3, severity="warning")

    def test_link_through_target_name_if_exists_is_conditional(self):
        """PLAT-114: the family port links the NDA cell monitor as $<TARGET_NAME_IF_EXISTS:...> - CMake leaves it out
        when the target does not exist (exported tree), so it is no reference out of the profile; a plain link is."""
        write_tree(self.root, {"src/CMakeLists.txt": "add_library(core STATIC main.c)\n"
                                                     "target_link_libraries(core PRIVATE $<TARGET_NAME_IF_EXISTS:nda_drv>)\n"})
        self.assertEqual([], [f for f in self.findings(self.check(), "R5") if f.path == "src/CMakeLists.txt"])

    def test_references_only_on_request(self):
        write_tree(self.root, {"src/use.c": "#include \"nda/drv.h\"\n"})
        report = self.check(references=False)
        self.assertEqual([], self.findings(report, "R5"))
        self.assertTrue(any(s.startswith("R5") for s in report.skipped))


class SecretAndEvidenceTest(TreeCase):
    def test_key_with_value(self):
        write_tree(self.root, {"src/cfg.h": "// SPDX-License-Identifier: Apache-2.0\n#define SECRET_KEY 0x5EC0B007UL\n"})
        self.assert_found(self.check(), "R7", "src/cfg.h", 2)

    def test_key_name_without_value_is_fine(self):
        write_tree(self.root, {"src/cfg.h": "// SPDX-License-Identifier: Apache-2.0\n/* SECRET_KEY comes from the "
                                            "project */\n"})
        self.assertEqual([], self.findings(self.check(), "R7"))

    def test_private_key_block(self):
        write_tree(self.root, {"docs/key.md": "-----BEGIN RSA PRIVATE KEY-----\n"})
        self.assert_found(self.check(), "R7", "docs/key.md", 1)

    def test_workstation_path_is_warning(self):
        write_tree(self.root, {"docs/setup.md": "install to C:/Users/me/tools\n"})
        self.assert_found(self.check(), "R7", "docs/setup.md", 1, severity="warning")

    def test_evidence_without_public_source(self):
        write_tree(self.root, {"db/chip.yaml": "flash: { size: 1024, source: \"[RM] 3.1\" }\n"
                                               "ram: { size: 64, source: \"[IDE] features.h:12\" }\n"})
        self.assert_found(self.check(), "R11", "db/chip.yaml", 2)

    def test_unverified_value_is_an_error_with_key(self):
        """TOOL-241 (F8): `verify: true` in an exported value file aborts the export with the list (key in detail)."""
        self.rules.evidence["unverified"] = {"severity": "error", "accepted": []}
        write_tree(self.root, {"db/chip.yaml": "clock:\n  osc: { min_hz: 4, verify: true }   # comment\n"
                                               "memory:\n  - { name: FLASH, size: 8, verify: true }\n"
                                               "uid:\n  size: 4\n  verify: true\n"
                                               "ok: { size: 1, source: \"[RM] 1\" }\n"
                                               "# verify: true only in a comment\n"})
        report = self.check()
        hits = self.findings(report, "R11")
        self.assertEqual([("db/chip.yaml", 2, "clock.osc"), ("db/chip.yaml", 4, "memory.FLASH"),
                          ("db/chip.yaml", 7, "uid")], [(f.path, f.line, f.detail) for f in hits])

    def test_unverified_rule_counterpart_and_release(self):
        """Counter test: values with evidence are green; a per-file decision (`accepted`) releases a file; without
        the `unverified` section the rule is off."""
        write_tree(self.root, {"db/chip.yaml": "flash: { size: 1, source: \"[RM] 3.1\" }\n"})
        self.rules.evidence["unverified"] = {"severity": "error", "accepted": []}
        self.assertEqual([], self.findings(self.check(), "R11"))
        write_tree(self.root, {"db/chip.yaml": "flash: { size: 1, verify: true }\n"})
        self.assert_found(self.check(), "R11", "db/chip.yaml", 1)
        self.rules.evidence["unverified"]["accepted"] = [{"glob": "db/chip.yaml", "reason": "decision"}]
        self.assertEqual([], self.findings(self.check(), "R11"))
        del self.rules.evidence["unverified"]
        self.assertEqual([], self.findings(self.check(), "R11"))

    def test_checksum_mismatch(self):
        write_tree(self.root, {"src/third/lib.sha256": "0" * 64 + "  lib.c\n"})
        self.assert_found(self.check(), "R6", "src/third/lib.sha256", 1)


class ExportTest(TreeCase):
    def export(self, **kwargs) -> tuple[Path, exporter.ExportResult]:
        out = Path(self._tmp.name) / "export"
        result = exporter.export(self.root, self.rules, out, "open", use_git=False, **kwargs)
        return out, result

    def test_only_open_files_and_no_history(self):
        out, result = self.export()
        self.assertFalse((out / ".git").exists())
        self.assertTrue((out / "src/main.c").is_file())
        for private in ("secret/drv.c", "internal/plan.md", "boards/own/board.yaml", "projects/own/project.yaml"):
            self.assertFalse((out / private).exists(), private)
        self.assertEqual(sorted(["LICENSE", "NOTICE"]), sorted(result.generated))

    def test_cleaned_files(self):
        out, _ = self.export()
        self.assertEqual("# Changelog TestBSW\n", (out / "CHANGELOG.md").read_text(encoding="utf-8"))
        notes = (out / "NOTES.md").read_text(encoding="utf-8")
        self.assertTrue(notes.startswith("<!-- cleaned -->"))
        self.assertIn("# not a heading inside a fence", notes)
        self.assertIn("- keep this bullet", notes)
        self.assertNotIn("board room", notes)
        self.assertNotIn("continues here", notes)
        self.assertNotIn("workstation", notes)
        self.assertNotIn("Internal", notes)
        self.assertNotIn("everything below", notes)

    def test_cleaned_todo(self):
        out, _ = self.export()
        text = (out / "todo.yaml").read_text(encoding="utf-8")
        self.assertIn("A-1", text)
        for dropped in ("A-2", "A-3", "A-4", "owner", "touches"):
            self.assertNotIn(dropped, text)

    def test_presets_of_private_projects_are_dropped_with_heirs(self):
        out, _ = self.export()
        names = [p["name"] for p in json.loads((out / "Presets.json").read_text(encoding="utf-8"))["configurePresets"]]
        self.assertEqual(["base", "demo"], names)

    def test_test_list_keeps_only_exported_tests(self):
        out, _ = self.export()
        text = (out / "tests.yaml").read_text(encoding="utf-8")
        self.assertIn("name: tool", text)
        self.assertIn("# list of tests", text)
        self.assertNotIn("name: nda-driver", text)
        self.assertNotIn("name: uses-driver-output", text)     # runs after a dropped entry
        self.assertIn("# (Eintrag uses-driver-output beim Export entfernt", text)
        self.assertIn("  ignore: [src/tool.py]", text)             # its test file stays, the runner must know
        self.assertIn("groups: [python]\n", text)                 # gui lost its only entry
        self.assertNotIn("name: target-own", text)

    def test_exported_tree_passes_its_own_check(self):
        out, _ = self.export()
        report = core.check(out, self.rules, "open", use_git=False, exported_tree=True)
        self.assertEqual([], [str(f) for f in report.errors])
        tree = core.build_tree(out, self.rules, use_git=False, exported=True)
        self.assertEqual("open", tree.cls["CHANGELOG.md"])
        notice = (out / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("MIT", notice)
        self.assertIn("src/third/**", notice)

    def test_exported_tree_without_license_is_an_error(self):
        out, _ = self.export()
        (out / "LICENSE").unlink()
        report = core.check(out, self.rules, "open", use_git=False, exported_tree=True)
        self.assert_found(report, "R10", "LICENSE")

    def test_target_not_empty_needs_force(self):
        self.export()
        with self.assertRaises(exporter.ExportError):
            self.export()
        self.export(force=True)

    @unittest.skipIf(shutil.which("git") is None, "git fehlt")
    def test_git_init_gives_exactly_one_commit(self):
        out, result = self.export(git_init=True)
        self.assertTrue(any("genau einem Commit" in note for note in result.notes), result.notes)
        count = subprocess.run(["git", "-C", str(out), "rev-list", "--count", "HEAD"], capture_output=True,
                               text=True).stdout.strip()
        self.assertEqual("1", count)
        report = core.check(out, self.rules, "open", history="HEAD", exported_tree=True)
        self.assertEqual([], self.findings(report, "R8") + self.findings(report, "R8", "warning"))

    def test_leak_built_into_export_is_found(self):
        """Mutation: a key slipped into an open file must stop the export check."""
        out, _ = self.export()
        (out / "src" / "main.c").write_text("// SPDX-License-Identifier: Apache-2.0\nSECRET_KEY = 0xDEADBEEF\n",
                                            encoding="utf-8")
        report = core.check(out, self.rules, "open", use_git=False, exported_tree=True)
        self.assert_found(report, "R7", "src/main.c", 2)

    def _board_link_substitution(self) -> None:
        """PLAT-114: the export substitution that neutralises review page links in docs/."""
        self.rules.substitutions.append(core.Substitution.parse(
            {"glob": "docs/**/*.md", "regex": "https://board\\.example/page/[A-Za-z0-9]+", "with": "(internal page)"},
            "test"))

    def test_substitution_is_checked_as_exported_and_applied_in_the_export(self):
        record = "# Record\r\n\r\n| Signature | answers on https://board.example/page/Ab12 (21 verdicts) |\r\n"
        write_tree(self.root, {"docs/records/r1.md": record, "src/notes.md": "see https://board.example/page/Zz9\n"})
        self.rules.secret_patterns.append(core.Pattern.parse(
            {"id": "board", "regex": "board\\.example/page/", "text": "internal page"}, "test"))
        report = self.check()
        self.assert_found(report, "R7", "docs/records/r1.md", 3)          # without the substitution: a leak
        self._board_link_substitution()
        report = self.check()
        self.assertEqual([], [f for f in self.findings(report, "R7") if f.path == "docs/records/r1.md"])
        self.assert_found(report, "R7", "src/notes.md", 1)               # outside the glob it stays a finding
        self.assertTrue(any("export.substitute" in r for r in report.ran), report.ran)
        write_tree(self.root, {"src/notes.md": "no link\n"})
        out, result = self.export()
        self.assertEqual(["docs/records/r1.md"], result.substituted)
        self.assertEqual(record.replace("https://board.example/page/Ab12", "(internal page)").encode("utf-8"),
                         (out / "docs/records/r1.md").read_bytes())    # line ends kept, only the link replaced
        self.assertEqual((self.root / "docs/guide.md").read_bytes(), (out / "docs/guide.md").read_bytes())
        report = core.check(out, self.rules, "open", use_git=False, exported_tree=True)
        self.assertEqual([], [str(f) for f in report.errors])

    def test_reuse_annotations_without_exported_file_are_dropped(self):
        """PLAT-114: REUSE.toml of the export names only licences of exported files (no NDA/Studio annotation)."""
        reuse = textwrap.dedent("""\
            # head comment
            version = 1

            # the whole tree
            [[annotations]]
            path = "**"
            SPDX-License-Identifier = "Apache-2.0"

            # derived from vendor material, never open
            [[annotations]]
            path = ["secret/**", "internal/plan.md"]
            SPDX-License-Identifier = "LicenseRef-Test-NDA"

            [[annotations]]
            path = "src/third/**"
            SPDX-License-Identifier = "MIT"
            """)
        write_tree(self.root, {"REUSE.toml": reuse})
        self.rules.entries.append(core.PathRule(glob="REUSE.toml", cls="open", license="Apache-2.0", index=997))
        self.rules.export["clean"].append({"file": "REUSE.toml", "mode": "reuse_toml", "profile": "open"})
        out, result = self.export()
        self.assertIn("REUSE.toml", result.cleaned)
        text = (out / "REUSE.toml").read_text(encoding="utf-8")
        self.assertNotIn("LicenseRef-Test-NDA", text)
        self.assertNotIn("vendor material", text)
        self.assertIn("# head comment", text)
        self.assertIn('path = "src/third/**"', text)
        import tomllib
        self.assertEqual(["**", "src/third/**"], [a["path"] for a in tomllib.loads(text)["annotations"]])

    def test_bad_substitution_is_rejected(self):
        with self.assertRaises(core.RulesError):
            core.Substitution.parse({"glob": "docs/**", "regex": "("}, "test")

    def test_verify_reports_skipped_target_build(self):
        out, _ = self.export()
        steps = exporter.verify(out, self.rules, groups="none", timeout=60)
        target = [s for s in steps if s.name.startswith("Target-Build")]
        self.assertEqual("skip", target[0].status)
        self.assertTrue(target[0].tail)

    def test_verify_without_time_limit_and_jobs(self):
        # timeout 0 means no limit; the job count reaches the host test command (PLAT-114)
        out, _ = self.export()
        steps = exporter.verify(out, self.rules, groups="none", timeout=0, jobs=2)
        host = [s for s in steps if s.name == "Host-Tests"][0]
        self.assertEqual("2", host.command[host.command.index("--jobs") + 1])
        self.assertNotIn("TimeoutExpired", host.tail)


GUIDE_WITH_LINKS = textwrap.dedent("""\
    # Guide

    The plan is [the internal plan](../internal/plan.md), see also [step 2](../internal/plan.md#step-2).
    The interface is [api.h](../src/api/api.h) and the notes are [NOTES](../NOTES.md).
    Folder: [internal](../internal/), web: [plan online](https://example.org/internal/plan.md), [top](#guide).
    ![board photo](../internal/photo.png "photo")
    Reference style: [see the plan][plan] and [plan][].

    ```md
    [example](../internal/plan.md)
    ```

    [plan]: ../internal/plan.md "internal"
    """)


@unittest.skipIf(shutil.which("git") is None, "git fehlt")
class GitlinkExportTest(TreeCase):
    """TOOL-236: the export carries the gitlink of a submodule, so that a clone of it can fetch the submodule.

    Everything is local (a small upstream repository, protocol.file.allow=always) - neither the real external/openbsw
    nor the network is involved."""

    def setUp(self) -> None:
        super().setUp()
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.org",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org", GIT_CONFIG_GLOBAL=os.devnull)
        self.rules = core.load_rules(self._rules_with_submodule())
        base = Path(self._tmp.name)
        self.upstream = base / "upstream"
        self.upstream.mkdir()
        self.git(self.upstream, "init", "-q")
        (self.upstream / "lib.c").write_text("int lib_v1;\n", encoding="utf-8")
        self.git(self.upstream, "add", "-A")
        self.git(self.upstream, "commit", "-q", "-m", "v1")
        self.pinned = self.git(self.upstream, "rev-parse", "HEAD").strip()
        # the source tree pins v1; upstream moves on, so "the tip" and "the pinned commit" differ
        self.git(self.root, "init", "-q")
        self.git(self.root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(self.upstream),
                 "ext/sub")
        (self.upstream / "lib.c").write_text("int lib_v2;\n", encoding="utf-8")
        self.git(self.upstream, "commit", "-q", "-a", "-m", "v2")
        self.tip = self.git(self.upstream, "rev-parse", "HEAD").strip()
        self.assertNotEqual(self.pinned, self.tip)
        self.git(self.root, "add", "-A")
        self.git(self.root, "commit", "-q", "-m", "source")
        self.out = base / "export"

    def _rules_with_submodule(self) -> Path:
        text = RULES.replace("export:\n  project:", '  - { glob: ".gitmodules", class: open, license: Apache-2.0 }\n'
                             '  - { glob: "ext/**", class: open, license: Apache-2.0 }\nexport:\n  project:', 1)
        self.assertIn("ext/**", text)
        path = Path(self._tmp.name) / "rules-sub.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def git(self, cwd: Path, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
                              env=self.env)
        self.assertEqual(0, done.returncode, f"git {' '.join(args)}: {done.stderr}")
        return done.stdout

    def clone_and_update(self, name: str) -> Path:
        clone = Path(self._tmp.name) / name
        self.git(Path(self._tmp.name), "clone", "-q", str(self.out), str(clone))
        self.git(clone, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
        return clone

    def export(self, **kwargs) -> exporter.ExportResult:
        old = os.environ.copy()
        os.environ.update({k: v for k, v in self.env.items() if k.startswith("GIT_")})
        try:
            return exporter.export(self.root, self.rules, self.out, "open", **kwargs)
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_clone_of_export_checks_out_the_pinned_commit(self):
        result = self.export(git_init=True)
        self.assertEqual({"ext/sub": self.pinned}, result.gitlinks)
        self.assertEqual([], exporter.gitlink_problems(self.out))
        clone = self.clone_and_update("clone")
        self.assertEqual(self.pinned, self.git(clone / "ext" / "sub", "rev-parse", "HEAD").strip())
        self.assertEqual("int lib_v1;\n", (clone / "ext" / "sub" / "lib.c").read_text(encoding="utf-8"))

    def test_folder_export_records_the_gitlink_next_to_the_tree(self):
        result = self.export()
        self.assertFalse((self.out / ".git").exists())
        self.assertEqual(f"160000 {self.pinned}\text/sub\n",
                         Path(result.gitlink_file).read_text(encoding="utf-8"))
        self.assertTrue(any("index-info" in note for note in result.notes), result.notes)
        # the recorded file is exactly what puts the link into the index of a later commit
        self.git(self.out, "init", "-q")
        self.git(self.out, "add", "-A")
        subprocess.run(["git", "-C", str(self.out), "update-index", "--index-info"], check=True, env=self.env,
                       input=Path(result.gitlink_file).read_bytes())
        self.git(self.out, "commit", "-q", "-m", "export")
        self.assertEqual([], exporter.gitlink_problems(self.out))
        clone = self.clone_and_update("clone2")
        self.assertEqual(self.pinned, self.git(clone / "ext" / "sub", "rev-parse", "HEAD").strip())

    def test_export_without_gitlink_is_detected(self):
        """Counter-check (mutation): the old behaviour - .gitmodules but no gitlink - must turn red."""
        original = exporter.source_gitlinks
        exporter.source_gitlinks = lambda root, paths: {}
        try:
            with self.assertRaises(exporter.ExportError) as ctx:
                self.export(git_init=True)
        finally:
            exporter.source_gitlinks = original
        self.assertIn("ext/sub", str(ctx.exception))
        self.assertIn("kein Gitlink", str(ctx.exception))
        # the committed result really is what the README promised not to give: a clone gets no submodule
        self.assertTrue(exporter.gitlink_problems(self.out))
        clone = self.clone_and_update("clone-without")
        self.assertFalse((clone / "ext" / "sub" / "lib.c").exists())

    def test_gitlink_without_gitmodules_entry_is_detected(self):
        self.export(git_init=True)
        self.git(self.out, "rm", "-q", "--cached", "-f", ".gitmodules")
        self.git(self.out, "commit", "-q", "-m", "mutation")
        self.assertTrue(any("ohne Eintrag in .gitmodules" in p for p in exporter.gitlink_problems(self.out)))


class UnpublishedLinkTest(TreeCase):
    """DOC-73: links from exported documents to unpublished files are replaced in the export, not in the source."""

    def setUp(self) -> None:
        super().setUp()
        write_tree(self.root, {"docs/links.md": GUIDE_WITH_LINKS, "internal/photo.png": "png\n"})

    def export(self) -> tuple[Path, exporter.ExportResult]:
        out = Path(self._tmp.name) / "export"
        return out, exporter.export(self.root, self.rules, out, "open", use_git=False)

    def test_links_to_private_files_are_replaced_and_open_links_stay(self):
        out, result = self.export()
        text = (out / "docs/links.md").read_text(encoding="utf-8")
        note = core.NOT_PUBLISHED
        self.assertIn(f"The plan is the internal plan ({note}), see also step 2 ({note}).", text)   # anchor link too
        self.assertIn("[api.h](../src/api/api.h)", text)              # open file: link stays
        self.assertIn("[NOTES](../NOTES.md)", text)                   # cleaned file is published: link stays
        self.assertIn(f"Folder: internal ({note})", text)             # folder without exported file
        self.assertIn("[plan online](https://example.org/internal/plan.md)", text)
        self.assertIn("[top](#guide)", text)
        self.assertIn(f"board photo ({note})", text)
        self.assertIn(f"Reference style: see the plan ({note}) and plan ({note}).", text)
        self.assertNotIn("[plan]: ../internal/plan.md", text)          # the definition goes with its uses
        self.assertIn("[example](../internal/plan.md)", text)         # code block: an example, not a link
        self.assertEqual(GUIDE_WITH_LINKS, (self.root / "docs/links.md").read_text(encoding="utf-8"))   # source kept
        self.assertFalse((out / "internal/plan.md").exists())
        self.assertEqual({"docs/links.md": 7}, result.links)

    def test_replacement_list_next_to_the_export(self):
        out, result = self.export()
        report = Path(result.replacement_report)
        self.assertEqual(out.parent, report.parent)                   # beside the tree, never inside it
        self.assertFalse(any(p.name.endswith(".replacements.md") for p in out.rglob("*")))
        text = report.read_text(encoding="utf-8")
        self.assertIn("| docs/links.md | 7 | 0 | 7 |", text)
        self.assertIn("| **total (1 files)** | **7** | **0** | **7** |", text)
        rows = [line for line in text.splitlines() if line.startswith("| docs/links.md | ") and "internal/" in line]
        self.assertEqual(7, len(rows), text)
        self.assertIn("| docs/links.md | 3 | internal/plan.md | `[step 2](../internal/plan.md#step-2)` |", text)
        self.assertIn("| docs/links.md | 5 | internal | `[internal](../internal/)` |", text)
        self.assertFalse(any("api.h" in row or "NOTES" in row for row in rows))

    def test_mutation_open_target_or_private_class_changes_the_result(self):
        """Counter checks: a published target is never replaced, a newly private one is."""
        index = core.LinkIndex(["a/doc.md", "a/open.md", "b/private.md"], ["a/doc.md", "a/open.md"])
        text = "[o](open.md) [p](../b/private.md#x) [q](missing.md)\n"
        new, found = core.rewrite_unpublished_links("a/doc.md", text, index)
        self.assertEqual(f"[o](open.md) p ({core.NOT_PUBLISHED}) [q](missing.md)\n", new)   # unknown: not judged
        self.assertEqual(["b/private.md"], [f.target for f in found])
        index = core.LinkIndex(["a/doc.md", "a/open.md", "b/private.md"], ["a/doc.md"])
        self.assertEqual(2, len(core.rewrite_unpublished_links("a/doc.md", text, index)[1]))
        self.assertEqual((text, []), core.rewrite_unpublished_links("a/doc.py", text, index))   # only Markdown
        crlf = "x [p](../b/private.md)\r\ny\r\n"
        self.assertEqual(f"x p ({core.NOT_PUBLISHED})\r\ny\r\n", core.rewrite_unpublished_links("a/doc.md", crlf,
                                                                                                index)[0])

    def test_check_scans_the_text_the_export_writes(self):
        report = self.check()
        self.assertTrue(any(r.startswith("Export-Links: 7 Verweis(e) in 1 Datei(en)") for r in report.ran), report.ran)
        self.assertIn("EXPORT: 7 Verweis(e)", core.format_text(report))
        out, _ = self.export()
        after = core.check(out, self.rules, "open", use_git=False, exported_tree=True)
        self.assertEqual([], [str(f) for f in after.errors])

    def test_test_entries_reading_unpublished_files(self):
        """tests_yaml: `reads` drops an entry that loads an unpublished file, `narrow` replaces its selection."""
        write_tree(self.root, {"tests.yaml": textwrap.dedent("""\
            groups: [python]
            tests:
              - name: plan-check
                group: python
                args: [src/tool.py]
              - name: tool-all
                group: python
                args: [src/tool.py, -k, AllTest]
              - name: tool-open
                group: python
                args: [src/tool.py, -k, OpenTest]
            """)})
        entry = next(e for e in self.rules.export["clean"] if e["file"] == "tests.yaml")
        entry["reads"] = {"plan-check": ["internal/plan.md"], "tool-open": ["src/tool.py"]}
        entry["narrow"] = [{"test": "tool-all", "reads": "internal", "replace": "-k, AllTest]",
                            "with": "-k, \"*AllTest.test_[!p]*\"]"}]
        out, _ = self.export()
        text = (out / "tests.yaml").read_text(encoding="utf-8")
        self.assertNotIn("name: plan-check", text)
        self.assertIn("args: [src/tool.py, -k, \"*AllTest.test_[!p]*\"]", text)
        self.assertIn("args: [src/tool.py, -k, OpenTest]", text)      # reads only open files: stays unchanged
        source = (self.root / "tests.yaml").read_text(encoding="utf-8")
        files = core.build_tree(self.root, self.rules, use_git=False).files
        self.assertEqual([], exporter.tests_yaml_rule_problems(source, entry, files))
        # cleaning the exported tree again changes nothing (entry and file are gone there already)
        tree = core.build_tree(out, self.rules, use_git=False, exported=True)
        self.assertEqual(text, exporter.clean_file("tests.yaml", tree, entry, self.rules))
        # counter checks: a stale rule is reported for the development tree
        entry["narrow"][0]["replace"] = "-k, Missing]"
        entry["reads"]["renamed-entry"] = ["internal/plan.md"]
        entry["reads"]["plan-check"] = ["internal/renamed.md"]
        problems = exporter.tests_yaml_rule_problems(source, entry, files)
        self.assertEqual(3, len(problems), problems)
        self.assertTrue(any("renamed-entry" in p for p in problems) and any("renamed.md" in p for p in problems)
                        and any("Missing" in p for p in problems), problems)


CONCEPT_WITH_PRIVATE_PARTS = textwrap.dedent("""\
    # Concept

    ## 1. Part A

    Part A is published; see [part B](#2-part-b) and [the open part](#1-part-a).

    ## 2. Part B

    <!-- export:private-begin Sections 2.1 and 2.2 -->

    ### 2.1 Mechanism

    secret detail of part B, see [the internal plan](../internal/plan.md) and <a id="b-table"></a>the table.

    ```md
    <!-- export:private-end -->
    # not a heading, the end marker above is an example
    ```

    ### 2.2 Limits

    <!-- export:private-end -->

    ## 3. Proposals

    <!-- export:private-begin -->
    unlabelled private paragraph
    <!-- export:private-end -->

    3.1 stays.
    """)

GUIDE_TO_PRIVATE_PARTS = textwrap.dedent("""\
    # Guide

    Mechanism: [2.1](concept.md#21-mechanism), limits: [2.2](concept.md#21-mechanism "x") [2.2b][lim],
    table: [table](concept.md#B-Table), part A: [1](concept.md#1-part-a), file: [concept](concept.md),
    unknown anchor: [x](concept.md#nowhere).

    [lim]: concept.md#22-limits
    """)


class UnpublishedListTest(TreeCase):
    """DOC-85: the export lists what it leaves out on purpose - private files of a scope, removed test entries and
    tasks - so the consistency tests of the exported tree accept exactly those as absent."""

    def setUp(self) -> None:
        super().setUp()
        rules = RULES.replace('  - { glob: "LICENSE", class: open, license: Apache-2.0 }\n',
                              '  - { glob: "LICENSE", class: open, license: Apache-2.0 }\n'
                              '  - { glob: "docs/unpublished.yaml", class: open, license: Apache-2.0 }\n', 1)
        rules = rules.replace("  clean:\n", "  unpublished:\n    file: docs/unpublished.yaml\n"
                              '    files: ["internal/**", "secret/**", "docs/**"]\n'
                              "    folders: [boards/own]\n"
                              "    tests: tests.yaml\n    tasks: todo.yaml\n  clean:\n", 1)
        self.assertNotEqual(rules, RULES)
        self.rules_file.write_text(rules, encoding="utf-8")
        self.rules = core.load_rules(self.rules_file)
        from tools.distcheck import unpublished
        self.unpublished = unpublished

    def export(self) -> tuple[Path, exporter.ExportResult]:
        out = Path(self._tmp.name) / "export"
        return out, exporter.export(self.root, self.rules, out, "open", use_git=False)

    def test_list_names_private_files_removed_tests_and_tasks(self):
        out, result = self.export()
        self.assertIn("docs/unpublished.yaml", result.generated)
        listed = self.unpublished.load(out, "docs/unpublished.yaml")
        self.assertEqual({"internal/plan.md"}, set(listed.files))                # private, in scope
        text = (out / "docs/unpublished.yaml").read_text(encoding="utf-8")
        for nda in ("secret/drv.c", "secret/CMakeLists.txt", "secret/include"):        # NDA: not even named
            self.assertNotIn(nda, text)
        self.assertNotIn("docs/guide.md", listed.files)                           # published: never listed
        self.assertEqual({"nda-driver", "uses-driver-output", "target-own"}, set(listed.tests))
        self.assertEqual({"A-2": "open", "A-3": "open", "A-4": "done"}, listed.tasks)
        self.assertTrue(listed.file("internal/plan.md") and not listed.file("docs/guide.md"))
        self.assertEqual({"boards/own"}, set(listed.folders))                     # named once, as a whole
        self.assertTrue(listed.file("boards/own/board.yaml") and listed.file("boards/own"))
        self.assertTrue(listed.file("internal") and not listed.file("boards/example/board.yaml"))
        self.assertEqual({"CHANGELOG.md", "NOTES.md", "todo.yaml", "Presets.json", "tests.yaml"}, set(listed.cleaned))
        self.assertTrue(listed.content_hidden("todo.yaml") and not listed.content_hidden("docs/guide.md"))
        report = core.check(out, self.rules, "open", use_git=False, exported_tree=True)
        self.assertEqual([], [str(f) for f in report.findings if f.severity == "error"])

    def test_folder_with_published_or_nda_file_is_refused(self):
        for folder in ("boards", "secret"):          # boards/example is open, secret/ is NDA
            text = self.rules_file.read_text(encoding="utf-8").replace("folders: [boards/own]", f"folders: [{folder}]")
            self.rules_file.write_text(text, encoding="utf-8")
            self.rules = core.load_rules(self.rules_file)
            with self.assertRaises(exporter.ExportError, msg=folder):
                exporter.export(self.root, self.rules, Path(self._tmp.name) / f"export-{folder}", "open",
                                use_git=False)
            self.rules_file.write_text(text.replace(f"folders: [{folder}]", "folders: [boards/own]"), encoding="utf-8")

    def test_development_tree_has_no_list_and_a_foreign_list_is_refused(self):
        self.assertFalse(self.unpublished.load(self.root, "docs/unpublished.yaml"))
        (self.root / "docs/unpublished.yaml").write_text("files: [docs/guide.md]\n", encoding="utf-8")
        with self.assertRaises(self.unpublished.UnpublishedError):
            self.unpublished.load(self.root, "docs/unpublished.yaml")

    def test_without_the_rule_no_list(self):
        self.rules_file.write_text(RULES, encoding="utf-8")
        self.rules = core.load_rules(self.rules_file)
        out, result = self.export()
        self.assertFalse((out / "docs/unpublished.yaml").exists())
        self.assertNotIn("docs/unpublished.yaml", result.generated)


class PrivateSectionTest(TreeCase):
    """DOC-74: private sections of a published document are cut by the export, links to their anchors replaced."""

    def setUp(self) -> None:
        super().setUp()
        write_tree(self.root, {"docs/concept.md": CONCEPT_WITH_PRIVATE_PARTS, "docs/guide.md": GUIDE_TO_PRIVATE_PARTS})

    def export(self) -> tuple[Path, exporter.ExportResult]:
        out = Path(self._tmp.name) / "export"
        return out, exporter.export(self.root, self.rules, out, "open", use_git=False)

    def test_sections_and_anchors(self):
        cuts = core.private_sections("docs/concept.md", CONCEPT_WITH_PRIVATE_PARTS)
        self.assertEqual([(9, 22, "Sections 2.1 and 2.2"), (26, 28, "")], [(c.begin, c.end, c.label) for c in cuts])
        self.assertEqual(("21-mechanism", "b-table", "22-limits"), cuts[0].anchors)   # not the fenced '# ...'
        self.assertEqual((), cuts[1].anchors)

    def test_export_cuts_the_sections_and_keeps_the_rest(self):
        out, result = self.export()
        text = (out / "docs/concept.md").read_text(encoding="utf-8")
        note = core.NOT_PUBLISHED
        self.assertNotIn("secret detail", text)
        self.assertNotIn("### 2.2 Limits", text)
        self.assertNotIn("unlabelled private paragraph", text)
        self.assertNotIn("export:private", text)
        self.assertIn(f"## 2. Part B\n\n*Sections 2.1 and 2.2 ({note})*\n\n## 3. Proposals\n\n*({note})*\n\n3.1 stays.",
                      text)
        self.assertIn("see [part B](#2-part-b) and [the open part](#1-part-a)", text)   # headings outside stay
        self.assertEqual(CONCEPT_WITH_PRIVATE_PARTS, (self.root / "docs/concept.md").read_text(encoding="utf-8"))
        self.assertEqual({"docs/concept.md": 2}, result.sections)
        self.assertNotIn("docs/concept.md", result.links)   # the link to internal/ went with its section

    def test_links_to_private_anchors_are_replaced_and_listed(self):
        out, result = self.export()
        text = (out / "docs/guide.md").read_text(encoding="utf-8")
        note = core.NOT_PUBLISHED
        self.assertIn(f"Mechanism: 2.1 ({note}), limits: 2.2 ({note}) 2.2b ({note}),", text)
        self.assertIn(f"table: table ({note}), part A: [1](concept.md#1-part-a), file: [concept](concept.md),", text)
        self.assertIn("unknown anchor: [x](concept.md#nowhere).", text)     # not judged, as unknown paths
        self.assertNotIn("[lim]:", text)
        self.assertEqual({"docs/guide.md": 5}, result.links)
        report = Path(result.replacement_report).read_text(encoding="utf-8")
        self.assertIn("| docs/guide.md | 3 | docs/concept.md#21-mechanism | `[2.1](concept.md#21-mechanism)` |",
                      report)
        self.assertIn("| docs/guide.md | 7 | docs/concept.md#22-limits | `[lim]: concept.md#22-limits` |", report)
        self.assertIn("## Cut sections", report)
        self.assertIn("| docs/concept.md | 9-22 | Sections 2.1 and 2.2 | 3 |", report)
        self.assertIn("| docs/concept.md | 26-28 | - | 0 |", report)

    def test_check_reports_the_cut_and_the_exported_tree_passes(self):
        report = self.check()
        self.assertIn("EXPORT: 2 privater Abschnitt(e) in 1 Datei(en)", core.format_text(report))
        self.assertEqual([], self.findings(report, "R12"))
        out, _ = self.export()
        after = core.check(out, self.rules, "open", use_git=False, exported_tree=True)
        self.assertEqual([], [str(f) for f in after.errors])
        index = core.LinkIndex.of(core.build_tree(out, self.rules, use_git=False, exported=True), self.rules, "open")
        self.assertEqual({}, index.private_anchors)                       # nothing left to cut in the export

    def test_counter_checks_without_markers_or_private_class(self):
        """Gegenprobe: without markers nothing is cut or replaced; a private file still wins over an anchor."""
        plain = CONCEPT_WITH_PRIVATE_PARTS.replace("<!-- export:private-begin Sections 2.1 and 2.2 -->", "") \
            .replace("<!-- export:private-begin -->", "").replace("<!-- export:private-end -->", "")
        write_tree(self.root, {"docs/concept.md": plain})
        out, result = self.export()
        self.assertEqual({}, result.sections)
        self.assertIn("secret detail of part B", (out / "docs/concept.md").read_text(encoding="utf-8"))
        guide = (out / "docs/guide.md").read_text(encoding="utf-8")
        self.assertIn("[2.1](concept.md#21-mechanism)", guide)
        self.assertNotIn("docs/guide.md", result.links)
        self.assertNotIn("## Cut sections", Path(result.replacement_report).read_text(encoding="utf-8"))
        index = core.LinkIndex(["a/doc.md", "b/p.md"], ["a/doc.md"], {"b/p.md": ["x"]})
        new, found = core.rewrite_unpublished_links("a/doc.md", "[p](../b/p.md#x) [q](#x)\n", index)
        self.assertEqual(["b/p.md"], [f.target for f in found])          # unpublished file: target without anchor
        self.assertEqual(f"p ({core.NOT_PUBLISHED}) [q](#x)\n", new)     # own anchor x is not private
        self.assertEqual(("[q](concept.md#x)\n", []),
                         core.cut_private_sections("a/doc.py", "[q](concept.md#x)\n"))   # only Markdown is cut

    def test_unusable_markers_are_an_error_and_block_the_export(self):
        cases = {"begin twice": ("<!-- export:private-begin -->\n<!-- export:private-begin -->\nx\n"
                                 "<!-- export:private-end -->\n", 2),
                 "end alone": ("x\n<!-- export:private-end -->\n", 2),
                 "no end": ("<!-- export:private-begin a -->\n\n# H\n", 1),
                 "typo": ("<!-- export:private-begni -->\n", 1),
                 "end in a fence": ("<!-- export:private-begin -->\n```\n<!-- export:private-end -->\n```\n", 1)}
        for name, (text, line) in cases.items():
            with self.subTest(name):
                with self.assertRaises(core.PrivateSectionError) as raised:
                    core.private_sections("d.md", text)
                self.assertEqual(line, raised.exception.line)
        write_tree(self.root, {"docs/concept.md": "# C\n\n<!-- export:private-begin -->\nsecret\n"})
        self.assert_found(self.check(), "R12", "docs/concept.md", 3)
        with self.assertRaises(exporter.ExportError):
            self.export()


SIMULINK_HEADER = textwrap.dedent("""\
    /*
     * File: Model.c
     *
     * Code generated for Simulink model 'Model'.
     *
     * Model version                  : 1.4
     * Simulink Coder version         : 24.1 (R2024a) 19-Nov-2023
     */
    """)

OVERLAY_RULES = {
    "paths:\n": "paths:\n  - { glob: \"src/model/generated/**\", class: private }\n",
    "content_patterns:\n": "content_patterns:\n"
                           "  - { id: simulink_generated, regex: \"(Code generated for Simulink model|Simulink Coder "
                           "version\\\\s*:|Auto generated XML Component Description for model)\", text: \"Simulink\" }\n",
    "  clean:\n": "  overlay:\n"
                 "    - replaces: src/model/generated\n"
                 "      files: { src/model/swc.yaml: src/substitute/swc.yaml, src/model/src/Model.c: src/substitute/Model.c }\n"
                 "  clean:\n",
}


class SimulinkOverlayTest(TreeCase):
    """PLAT-119 (decision F9 A): code and ARXML that Simulink Coder generated under a student licence stay private,
    the R3 pattern stops an open file that still carries their header, and the export writes the hand-written
    substitute (export.overlay) where the generated code was - checked with the rule R13."""

    def setUp(self) -> None:
        super().setUp()
        text = RULES
        for old, new in OVERLAY_RULES.items():
            self.assertIn(old, text)
            text = text.replace(old, new, 1)
        self.rules_file.write_text(text, encoding="utf-8")
        self.rules = core.load_rules(self.rules_file)
        write_tree(self.root, {
            "src/model/Model.slx": "model\n",
            "src/model/Model_data.m": "% SPDX-License-Identifier: Apache-2.0\nAlpha = 0.5;\n",
            "src/model/generated/src/Model.c": SIMULINK_HEADER + "void Run10ms(void) {}\n",
            "src/model/generated/arxml/Model.arxml": "<!--\nAuto generated XML Component Description for model Model\n"
                                                     "-->\n<AUTOSAR/>\n",
            "src/substitute/swc.yaml": "# SPDX-License-Identifier: Apache-2.0\nname: Model\nimplementation: c\n",
            "src/substitute/Model.c": "// SPDX-License-Identifier: Apache-2.0\nvoid Model_Run10ms(void) {}\n",
        })

    def export(self, **kwargs) -> tuple[Path, exporter.ExportResult]:
        out = Path(self._tmp.name) / "export"
        return out, exporter.export(self.root, self.rules, out, "open", use_git=False, **kwargs)

    def test_generated_code_is_private_and_clean_tree_passes(self):
        report = self.check()
        self.assertEqual([], [str(f) for f in report.errors])
        self.assertIn("R13", report.ran)
        tree = core.build_tree(self.root, self.rules, use_git=False)
        self.assertEqual("private", tree.cls["src/model/generated/src/Model.c"])
        self.assertEqual(["src/model/generated"], [o.replaces for o in core.active_overlays(tree, self.rules, "open")])

    def test_counter_check_simulink_header_in_an_open_file_stops_the_export(self):
        """Gegenprobe: a copy of generated code (C header or ARXML) in an open file is an R3 error, and the export
        refuses to run."""
        write_tree(self.root, {"src/copied.c": "// SPDX-License-Identifier: Apache-2.0\n" + SIMULINK_HEADER,
                               "docs/types.arxml": "<!-- copy -->\n<!--\nAuto generated XML Component Description "
                                                   "for model Model\n-->\n"})
        report = self.check()
        self.assert_found(report, "R3", "src/copied.c", 5)
        self.assert_found(report, "R3", "docs/types.arxml", 3)
        out = Path(self._tmp.name) / "cli-export"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(["export", "--repo", str(self.root), "--rules", str(self.rules_file), "--out", str(out)])
        self.assertEqual(1, code)
        self.assertIn("kein Export", stderr.getvalue())
        self.assertIn("simulink_generated", stdout.getvalue() + stderr.getvalue())
        self.assertFalse(out.exists())

    def test_export_writes_the_substitute_in_place_of_the_generated_code(self):
        out, result = self.export()
        self.assertFalse((out / "src/model/generated").exists())
        self.assertEqual({"src/model/swc.yaml": "src/substitute/swc.yaml",
                          "src/model/src/Model.c": "src/substitute/Model.c"}, result.overlaid)
        self.assertEqual((self.root / "src/substitute/Model.c").read_bytes(),
                         (out / "src/model/src/Model.c").read_bytes())
        self.assertTrue((out / "src/model/Model_data.m").is_file())             # model and parameters stay
        self.assertTrue(any("src/model/generated fehlt" in n for n in result.notes), result.notes)
        report = core.check(out, self.rules, "open", use_git=False, exported_tree=True)
        self.assertEqual([], [str(f) for f in report.errors])

    def test_counter_check_exported_substitute_that_differs_is_an_error(self):
        out, _ = self.export()
        (out / "src/model/src/Model.c").write_text("// SPDX-License-Identifier: Apache-2.0\nint x;\n", encoding="utf-8")
        self.assert_found(core.check(out, self.rules, "open", use_git=False, exported_tree=True), "R13",
                          "src/model/src/Model.c")
        (out / "src/model/src/Model.c").unlink()
        self.assert_found(core.check(out, self.rules, "open", use_git=False, exported_tree=True), "R13",
                          "src/model/src/Model.c")

    def test_counter_check_target_that_exists_or_private_source(self):
        write_tree(self.root, {"src/model/swc.yaml": "# SPDX-License-Identifier: Apache-2.0\nname: Model\n"})
        self.assert_found(self.check(), "R13", "src/model/swc.yaml")
        with self.assertRaises(exporter.ExportError):
            self.export()
        (self.root / "src/model/swc.yaml").unlink()
        self.rules.entries.append(core.PathRule(glob="src/substitute/Model.c", cls="private", index=999))
        self.assert_found(self.check(), "R13", "src/substitute/Model.c")

    def test_counter_check_stale_overlay_is_an_error(self):
        shutil.rmtree(self.root / "src/model/generated")
        self.assert_found(self.check(), "R13", "tools/distcheck/distribution.yaml")

    def test_overlay_is_inactive_while_the_replaced_path_is_exported(self):
        """Mutation of the classification: with the generated code open (and its header allowed) nothing is
        replaced - the overlay only stands in for what the profile lacks."""
        self.rules.entries.append(core.PathRule(glob="src/model/generated/**", cls="open", license="Apache-2.0",
                                                allow=["simulink_generated"], index=999))
        out, result = self.export()
        self.assertEqual({}, result.overlaid)
        self.assertTrue((out / "src/model/generated/src/Model.c").is_file())
        self.assertFalse((out / "src/model/swc.yaml").exists())

    def test_narrow_can_take_a_private_file_out_of_covers(self):
        """PLAT-119: `narrow` runs before the drop - an entry that covers a private host test stays in the export
        when a rule removes that line and narrows the selection; without the rule the entry is dropped."""
        text = textwrap.dedent("""\
            tests:
              - name: gen
                kind: unittest
                args: [src/tool.py]
                covers:
                  - src/tool.py
                  - src/model/generated/src/Model.c   # host test of the generated code
            """)
        tree = core.build_tree(self.root, self.rules, use_git=False)
        inside = set(tree.exported("open"))
        rules = [{"test": "gen", "reads": "src/model/generated/src/Model.c", "replace": "kind: unittest",
                  "with": "kind: pytest"},
                 {"test": "gen", "reads": "src/model/generated/src/Model.c", "replace": "args: [src/tool.py]",
                  "with": 'args: [src/tool.py, -k, "not Generated"]'},
                 {"test": "gen", "reads": "src/model/generated/src/Model.c",
                  "replace": "- src/model/generated/src/Model.c", "with": "# (private)"}]
        out = exporter._clean_tests(text, {"narrow": rules}, tree, inside)
        self.assertIn("kind: pytest", out)
        self.assertIn('args: [src/tool.py, -k, "not Generated"]', out)
        self.assertNotIn("src/model/generated", out)
        self.assertEqual([], exporter.tests_yaml_rule_problems(text, {"narrow": rules}, tree.files))
        dropped = exporter._clean_tests(text, {"narrow": rules[:2]}, tree, inside)     # covers line kept: dropped
        self.assertIn("# (Eintrag gen beim Export entfernt", dropped)

    def test_bad_overlay_is_rejected(self):
        for raw in ({"replaces": "a"}, {"files": {"a": "b"}}, {"replaces": "a", "files": {}},
                    {"replaces": "a", "files": "b"}):
            with self.assertRaises(core.RulesError):
                core.Overlay.parse(raw, "test")


class CommandLineTest(TreeCase):
    def run_cli(self, *args: str, stdout_only: bool = False) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([args[0], "--repo", str(self.root), "--rules", str(self.rules_file), *args[1:]])
        return code, out.getvalue() if stdout_only else out.getvalue() + err.getvalue()

    def test_check_json_and_exit_code(self):
        # the synthetic tree is no git repository: list_files walks it
        code, text = self.run_cli("check", "--json", "--references", stdout_only=True)
        self.assertEqual(0, code, text)
        self.assertEqual(0, json.loads(text)["errors"])
        write_tree(self.root, {"src/cfg.h": "#define SECRET_KEY 0x12345678\n"})
        code, text = self.run_cli("check", "--json", stdout_only=True)
        self.assertEqual(1, code)
        data = json.loads(text)
        hit = [f for f in data["findings"] if f["rule"] == "R7"][0]
        self.assertEqual(("src/cfg.h", 1), (hit["path"], hit["line"]))

    def test_verify_options_reach_verify(self):
        seen = {}

        def fake_verify(tree, rules, **kwargs):
            seen.update(kwargs)
            return []
        original = cli.exporter.verify
        cli.exporter.verify = fake_verify
        try:
            code, text = self.run_cli("verify", "--tree", str(self.root), "--timeout", "0", "--jobs", "6")
        finally:
            cli.exporter.verify = original
        self.assertEqual(0, code, text)
        self.assertEqual((0, 6), (seen["timeout"], seen["jobs"]))

    def test_export_refuses_findings(self):
        write_tree(self.root, {"src/cfg.h": "#define SECRET_KEY 0x12345678\n"})
        out = Path(self._tmp.name) / "cli-export"
        code, text = self.run_cli("export", "--out", str(out))
        self.assertEqual(1, code)
        self.assertIn("kein Export", text)
        self.assertFalse(out.exists())


class RealRepositoryTest(unittest.TestCase):
    """The real rules against the real repository: every tracked file has its own class."""

    @classmethod
    def setUpClass(cls):
        cls.rules = core.load_rules(REPO / "tools" / "distcheck" / "distribution.yaml")
        cls.tree = core.build_tree(REPO, cls.rules)

    def test_every_file_classified(self):
        fallback = [p for p in self.tree.files if self.tree.rule_of[p] is None or self.tree.rule_of[p].fallback]
        self.assertEqual([], fallback, "Dateien ohne eigene Klassifizierung - Eintrag in distribution.yaml ergaenzen")

    def test_generator_exception_wording_is_exact_in_notice_and_licence_text(self):
        """PLAT-120: NOTICE and LICENSES/ carry the decision of the user byte for byte as the generators' constant
        has it; LICENSE (the exported Apache-2.0 text) stays unchanged and does not mention the exception."""
        sys.path.insert(0, str(REPO / "tools"))
        from nxconfig import genlicense
        text = genlicense.EXCEPTION_TEXT
        self.assertTrue(text.startswith("Generator output exception: The output of the neXtBSW generators (source code, "))
        self.assertTrue(text.endswith("This exception does not apply to the generators and templates themselves."))
        notice = (REPO / "NOTICE").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(1, notice.count(text.encode("utf-8")))
        licence = (REPO / "LICENSES" / f"{genlicense.EXCEPTION_ID}.txt").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(text.encode("utf-8") + b"\n", licence)
        self.assertIn(genlicense.EXCEPTION_ID.encode(), notice)
        template = (REPO / "tools" / "distcheck" / "templates" / "LICENSE.Apache-2.0.txt").read_bytes()
        self.assertNotIn(b"Generator output exception", template)
        self.assertNotIn(b"Generator output exception", (REPO / "LICENSES" / "Apache-2.0.txt").read_bytes())

    def test_real_rules_allow_the_exception_and_notice_names_it(self):
        sys.path.insert(0, str(REPO / "tools"))
        from nxconfig import genlicense
        for profile in ("open", "licensed", "nda"):
            self.assertIn(genlicense.EXCEPTION_ID, self.rules.licenses[profile])
        findings = core._r10_notice(self.tree, self.rules, "open", self.tree.exported("open"))
        self.assertEqual([], [str(f) for f in findings if "Ausnahme" in f.text or "NOTICE nennt" in f.text])

    def test_private_material_stays_out(self):
        exported = set(self.tree.exported("open"))
        for path in self.tree.files:
            if path.startswith(("tools/studio/", "doc/hardware/", "boards/evalboard_s32k144/",
                                "projects/evalboard_s32k144", "libs/bsw/bccMc33771/", "doc/changelog.d/")):
                self.assertNotIn(path, exported, path)
        for path in ("CHANGELOG.md", "CLAUDE.md", "doc/todo.yaml"):
            self.assertNotIn(path, exported)

    def test_docs_workflow_is_exported_in_the_open_profile(self):
        """DOC-87: the workflow that publishes the website runs in the release repository, so it is open."""
        path = ".github/workflows/docs.yml"
        self.assertIn(path, self.tree.files)
        rule = self.tree.rule_of[path]
        self.assertFalse(rule.fallback)
        self.assertEqual("open", rule.cls)
        self.assertEqual("Apache-2.0", rule.license)
        self.assertEqual(path, rule.glob)               # its own entry, not just the .github/** catch-all
        self.assertIn(path, set(self.tree.exported("open")))

    def test_templates_and_clean_rules_are_usable(self):
        for entry in self.rules.export.get("clean") or []:
            exporter.clean_file(str(entry["file"]), self.tree, entry, self.rules)

    def test_private_repository_pattern_names_only_the_private_repositories(self):
        """PLAT-114: since 2026-10-02 the development repository is neXtBSW_development and the Studio has its own;
        the open repository neXtBSW and the public OpenBSW fork of the account may be named."""
        pattern = next(p for p in self.rules.secret_patterns if p.id == "private_repo")
        account = "github.com/" + "danielsimonreinhardt"
        for private in ("/neXtBSW_development.git", "/neXtBSW-Studio.git", "/nextbsw_development"):
            self.assertTrue(pattern.regex.search(account + private), private)
        for public in ("/neXtBSW.git", "/neXtBSW", "/openbsw.git"):
            self.assertFalse(pattern.regex.search(account + public), public)

    def test_review_page_links_are_neutral_in_exported_docs(self):
        board = next(p for p in self.rules.secret_patterns if p.id == "decision_board")
        line = "| Signature | answers in the review artifact https://" + "claude.ai/artifact/EAB12xy-_9 (21 verdicts) |"
        text = core.substituted(self.rules, "doc/safety/reviews/records/CR-99-1.md", line)
        self.assertIsNotNone(text)
        self.assertIsNone(board.regex.search(text), text)
        self.assertIn("(21 verdicts)", text)
        self.assertIsNone(core.substituted(self.rules, "tools/x/y.py", line))   # only documentation is rewritten

    def test_k1_documents_are_private_and_their_links_replaced(self):
        """DOC-73 (decision K1 of 2026-10-04): review records, gap analysis, migration plan, RTOS comparison,
        traceability matrix and the stack report of the private demo project stay in the development repository."""
        for path in ("doc/safety/reviews/README.md", "doc/safety/reviews/records/CR-07-1.md",
                     "doc/safety/reviews/code-inspection/INS-SUP-1.md", "doc/safety/reviews/reviewlib.py",
                     "doc/safety/iso26262-gap.md", "doc/safety/rtos-comparison.md", "doc/safety/traceability.md",
                     "doc/requirements/migration-plan.md", "tools/stackuse/reports/evalboard_s32k144_demo.md"):
            self.assertEqual("private", self.rules.rule_for(path).cls, path)    # also holds in an exported tree
        for path in ("doc/safety/safety-case.md", "doc/safety/partner-and-degraded.md", "tools/stackuse/README.md"):
            self.assertEqual("open", self.rules.rule_for(path).cls, path)
        exported = set(self.tree.exported("open"))
        for path in self.tree.files:
            if path.startswith("doc/safety/reviews/") or path.endswith(("iso26262-gap.md", "traceability.md")):
                self.assertNotIn(path, exported, path)
        index = core.LinkIndex.of(self.tree, self.rules, "open")
        for path in sorted(p for p in exported if p.endswith(".md")):
            new, _ = core.rewrite_unpublished_links(path, self.tree.text(path) or "", index)
            for match in core.MD_LINK_RE.finditer(new):
                target = core.link_target(path, match.group(3))
                self.assertFalse(target is not None and index.unpublished(target), f"{path}: {match.group(0)}")

    def test_safety_requirements_are_open_the_other_requirements_private(self):
        """DOC-80 (decision CR05-SDOC 'publish' of the re-check of CR-05-1, 2026-10-04): the safety requirements and
        catalogues under doc/requirements/safety are published with Release R1; the rest of doc/requirements stays in
        the development repository. DOC-85: the grammar and the StrictDoc configuration go with them - without them
        StrictDoc refuses the published .sdoc ('@nextbsw' [schema]); the proof of concept (poc/, its own grammar and
        configuration) and the migration plan stay private."""
        for path in ("doc/requirements/safety/ssr.sdoc", "doc/requirements/safety/goals.sdoc",
                     "doc/requirements/safety/assumptions.sdoc", "doc/requirements/safety/mechanisms.sdoc",
                     "doc/requirements/nextbsw.sgra", "doc/requirements/strictdoc_config.py"):
            rule = self.rules.rule_for(path)
            self.assertEqual(("open", "Apache-2.0"), (rule.cls, rule.license), path)
        for path in ("doc/requirements/migration-plan.md", "doc/requirements/poc/ssr.sdoc",
                     "doc/requirements/poc/nextbsw.sgra", "doc/requirements/poc/strictdoc_config.py"):
            self.assertEqual("private", self.rules.rule_for(path).cls, path)

    def test_unpublished_list_rule(self):
        """DOC-85: the export writes doc/unpublished.yaml - an open file of its own that the development repository
        never tracks (there every absent source must stay an error); its scope lists the K1 documents and never NDA
        or workstation material. In an exported tree: the list is there, was written by the export, and nothing it
        names is in the tree."""
        from tools.distcheck import unpublished
        spec = self.rules.export["unpublished"]
        self.assertEqual("doc/unpublished.yaml", spec["file"])
        self.assertEqual("open", self.rules.rule_for(spec["file"]).cls)
        self.assertEqual({"tools/boardtest/captures", "tools/studio", "tools/todo"}, set(spec["folders"]))
        if not (REPO / "doc" / "TODO.md").is_file():                  # exported tree (full_tree missing, PLAT-62)
            listed = unpublished.load(REPO)
            self.assertTrue(listed.files and listed.folders and listed.tests and listed.tasks)
            self.assertEqual([], sorted(p for p in listed.files | listed.folders if (REPO / p).exists()))
            return
        self.assertNotIn(spec["file"], self.tree.files)
        for rel in (spec["tests"], spec["tasks"]):
            self.assertIn(rel, self.tree.files)
        out = Path(tempfile.mkdtemp())
        try:
            listing = exporter.unpublished_list(self.tree, self.rules, self.tree.exported("open"), out)
            self.assertIsNotNone(listing)
            (out / "doc").mkdir()
            (out / spec["file"]).write_text(listing[1], encoding="utf-8")
            files = unpublished.load(out).files
        finally:
            shutil.rmtree(out, ignore_errors=True)
        for path in ("doc/safety/iso26262-gap.md", "doc/safety/traceability.md", "doc/safety/rtos-comparison.md",
                     "doc/safety/reviews/records/CR-07-1.md"):
            self.assertIn(path, files)
        for path in files:
            self.assertEqual("private", self.tree.cls[path], path)
        self.assertNotIn("doc/requirements/nextbsw.sgra", files)
        self.assertNotIn("doc/safety/fmea.md", files)

    def test_partner_and_degraded_parts_b_and_c_are_cut(self):
        """DOC-74 (decision K1 of 2026-10-04): parts B and C of partner-and-degraded.md stay in the development
        repository, part A is published; section 7.1 stays, it is the source of AoU-31 and AoU-33 that
        doc/safety/test/test_safety_manual.py reads. Holds in the development tree and in the exported tree."""
        path = "doc/safety/partner-and-degraded.md"
        text, cuts = core.cut_private_sections(path, self.tree.text(path) or "")
        for kept in ("## 3. Part A", "### 3.11 What this does not cover", "## 4. Part B", "## 5. Part C",
                     "**A — Partner supervision (SYS-115)**", "### 7.1 `concept.md`", "*AoU-31*", "*AoU-33*"):
            self.assertIn(kept, text)
        for gone in ("**Part B is implemented by SYS-117**", "**C2 is implemented by SYS-139", "### 4.1 Definition",
                     "### 4.7 ", "### 5.1 Why", "### 5.11 ", "**B — Degraded reaction", "**C6 Who selects**",
                     "export:private"):
            self.assertNotIn(gone, text)
        if (REPO / "doc" / "TODO.md").is_file():                       # development tree: the markers are there
            self.assertEqual(4, len(cuts))
            self.assertIn("510-start-up-test-of-the-short", {a for c in cuts for a in c.anchors})
        index = core.LinkIndex.of(self.tree, self.rules, "open")
        for doc in sorted(p for p in self.tree.exported("open") if p.endswith(".md")):
            new, _ = core.rewrite_unpublished_links(doc, core.cut_private_sections(doc, self.tree.text(doc) or "")[0],
                                                    index)
            for match in core.MD_LINK_RE.finditer(new):
                target = core.link_target(doc, match.group(3)) or doc
                self.assertFalse(index.private_anchor(target, core.link_anchor(match.group(3))),
                                 f"{doc}: {match.group(0)}")

    @unittest.skipUnless((REPO / "doc" / "TODO.md").is_file(), "only in the development tree (full_tree, PLAT-62)")
    def test_paths_of_the_test_list_rules_exist(self):
        """DOC-73: reads/narrow of the tests.yaml clean rule name paths of the development tree - a renamed file would
        otherwise silently count as unpublished and drop or narrow a test of the export."""
        entry = next(e for e in self.rules.export["clean"] if e["file"] == "tools/test/tests.yaml")
        self.assertTrue(entry.get("reads") and entry.get("narrow"))
        text = (REPO / "tools" / "test" / "tests.yaml").read_text(encoding="utf-8")
        self.assertEqual([], exporter.tests_yaml_rule_problems(text, entry, self.tree.files))

    def test_application_hint_name_is_allowed_content_is_not(self):
        """TOOL-240: AH1506/AH1507 are not public - the name alone passes, references to their content (table,
        chapter, figure, section) do not; public base list and template of the private list agree. The samples are
        assembled so that this file does not match itself."""
        name = "AH15" + "06"
        other = "AH15" + "07"
        template = core.load_private_patterns(REPO / "tools" / "distcheck" / "patterns.example.yaml")
        for patterns in ([p for p in self.rules.content_patterns if p.id == "app_hint_content"],
                         [p for p in template if p.id == "app_hint_content"]):
            self.assertEqual(1, len(patterns))
            regex = patterns[0].regex
            for bad in (f"{name} Tabelle 10", f"{name} 5.1.4", f"{other} Fig. 3", f"table 10 of {name}",
                        f"({name}, Kapitel 4)", f"{other} Rev. 1.0 Abschnitt 2", f"Abbildung 3 in {other}",
                        f"{name} section 3", f"{name.lower()} table 2"):
                self.assertTrue(regex.search(bad), bad)
            for good in (f"{name} and {other} are not public", f"{name}/{other}", f"sourced from the data sheet; "
                         f"{name} is only named", f"the table is {name}", f"(see {other}).", "AH1501 Tabelle 10",
                         f"{name} Rev. 2 is not public"):
                self.assertIsNone(regex.search(good), good)

    def test_simulink_output_is_private_and_substituted(self):
        """PLAT-119 (decision F9 A): no file of the open profile carries a Simulink Coder header, the generated code
        of DemoSwc and of the test model TypeSwc is private, the models stay open, and the export writes the C
        substitute into swc/DemoSwc. Development tree: the overlay is applicable; exported tree: it was applied."""
        marker = next(p for p in self.rules.content_patterns if p.id == "simulink_generated")
        exported = self.tree.exported("open")
        hits = [p for p in exported if Path(p).suffix.lower() not in self.rules.skip_scan_suffixes
                and "simulink_generated" not in (self.tree.rule_of[p].allow if self.tree.rule_of[p] else [])
                and marker.regex.search(self.tree.text(p) or "")]
        self.assertEqual([], hits)
        for path in ("swc/DemoSwc/generated/src/DemoSwc.c", "swc/DemoSwc/generated/arxml/DemoSwc_component.arxml",
                     "tools/rtegen/test/types/TypeSwc/generated/src/TypeSwc.c",
                     "tools/rtegen/test/types/TypeSink/generated/arxml/TypeSink_datatype.arxml"):
            self.assertEqual("private", self.rules.rule_for(path).cls, path)
        for path in ("swc/DemoSwc/DemoSwc.slx", "swc/DemoSwc/DemoSwc_data.m", "swc/DemoSwc/DemoSwc_create.m",
                     "swc/DemoSwcSubstitute/src/DemoSwc.c", "tools/rtegen/test/types/TypeSwc/TypeSwc.slx",
                     "tools/rtegen/test/types/TypeSink/generated/src/TypeSink.c"):
            self.assertEqual("open", self.rules.rule_for(path).cls, path)
        overlays = core.active_overlays(self.tree, self.rules, "open")
        self.assertEqual(["swc/DemoSwc/generated"], [o.replaces for o in overlays])
        development = (REPO / "doc" / "TODO.md").is_file()
        self.assertEqual([], [str(f) for f in core.overlay_findings(self.tree, self.rules, "open",
                                                                    exported_tree=not development)])

    def test_check_runs_on_the_repository(self):
        report = core.check(REPO, self.rules, "open", private_patterns=None)
        self.assertEqual([], [f for f in report.findings if f.rule == "R1" and f.severity == "error"])


if __name__ == "__main__":
    unittest.main()
