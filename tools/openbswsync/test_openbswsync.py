# SPDX-License-Identifier: Apache-2.0
"""Host test of the OpenBSW copy check (TOOL-21).

Synthetic mini trees (a fake submodule with an application and a board port, a repository with copies) carry
every finding as a built-in fault; one run checks the real repository against tools/openbswsync/copies.yaml.

    .venv/Scripts/python tools/openbswsync/test_openbswsync.py
"""

from __future__ import annotations

import contextlib
import copy
import io
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.openbswsync import sync  # noqa: E402
from tools.openbswsync.__main__ import main as cli_main  # noqa: E402

ORIGINALS = {
    "app/CMakeLists.txt": "add_subdirectory(platforms/board)\n",
    "app/src/main.cpp": "int main() { return 0; }\n",
    "app/src/app.cpp": "void app() {}\n",
    "app/src/demo.cpp": "void demo() {}\n",                   # not taken over
    "app/platforms/board/bsp/Config.h": "#define CLOCK 80000000\n",
    "app/platforms/board/safety/mpu.h": "enum { REGIONS = 16 };\n",
    "app/platforms/board/eth/Eth.cpp": "void eth() {}\n",     # not taken over
}
COPIES = {
    "ecu/CMakeLists.txt": "add_subdirectory(${FAMILY_PORT})\n",  # modified
    "ecu/src/main.cpp": ORIGINALS["app/src/main.cpp"],           # identical
    "ecu/src/app.cpp": ORIGINALS["app/src/app.cpp"],             # identical
    "ecu/src/rte.cpp": "void rte() {}\n",                        # new, exact entry
    "ecu/uds/Diag.cpp": "void diag() {}\n",                      # new, pattern
    "port/bsp/Config.h": "#define CLOCK NX_CLOCK_CORE_HZ\n",     # modified
    "port/safety/mpu.h": ORIGINALS["app/platforms/board/safety/mpu.h"],  # identical
    "port/test/MpuHostTest.cpp": "int main() {}\n",              # new, pattern
}


def write(path: Path, text: str, newline: str = "\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline=newline) as fh:
        fh.write(text)


class MiniRepo:
    """Repository with fake submodule and a complete, clean copies.yaml."""

    def __init__(self, base: Path):
        self.root = base
        for rel, text in ORIGINALS.items():
            write(base / "external/openbsw" / rel, text)
        for rel, text in COPIES.items():
            write(base / rel, text)
        self.list = base / "copies.yaml"
        h = lambda rel: sync.normalized_hash(base / "external/openbsw" / rel)  # noqa: E731
        write(self.list, textwrap.dedent(f"""\
            # test list
            version: 1
            openbsw:
              path: external/openbsw
            roots:
              - name: ecu
                path: ecu
                original: app
                files:
                  CMakeLists.txt:
                    status: modified
                    task: [PLAT-23]
                    reason: family port template
                    upstream: {h("app/CMakeLists.txt")}
                  src/main.cpp: {{status: identical, upstream: {h("app/src/main.cpp")}}}
                  src/app.cpp: {{status: identical, upstream: {h("app/src/app.cpp")}}}
                  src/rte.cpp: {{status: new, task: RTE-02}}
                  "uds/**": {{status: new, reason: own UDS integration}}
              - name: port
                path: port
                original: app/platforms/board
                files:
                  bsp/Config.h:
                    status: modified
                    task: ARCH-13
                    reason: clock from the generated clock plan
                    upstream: {h("app/platforms/board/bsp/Config.h")}
                  safety/mpu.h: {{status: identical, upstream: {h("app/platforms/board/safety/mpu.h")}}}
                  "**/test/**": {{status: new}}
            """))

    def check(self) -> sync.Report:
        return sync.check(self.root, sync.load_list(self.list))

    def orig(self, rel: str) -> Path:
        return self.root / "external/openbsw" / rel

    def cli(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli_main([args[0], "--repo", str(self.root), "--list", str(self.list), *args[1:]])
        return code, out.getvalue()

    def edit_list(self, old: str, new: str) -> None:
        text = self.list.read_text(encoding="utf-8")
        assert old in text, old
        self.list.write_text(text.replace(old, new), encoding="utf-8")

    def drop_line(self, fragment: str) -> None:
        lines = self.list.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [line for line in lines if fragment not in line]
        assert len(kept) == len(lines) - 1, fragment
        self.list.write_text("".join(kept), encoding="utf-8")


def codes(report: sync.Report, severity: str | None = None) -> list[tuple[str, str | None]]:
    return sorted((f.code, f.path) for f in report.findings if severity is None or f.severity == severity)


class SyntheticTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = MiniRepo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    # ---------------------------------------------------------------- clean state and report

    def test_clean_tree_has_no_findings_and_counts_per_root(self):
        report = self.repo.check()
        self.assertEqual(codes(report), [])
        ecu, port = report.roots
        self.assertEqual((ecu.identical, ecu.modified, ecu.new), (2, 1, 2))
        self.assertEqual((port.identical, port.modified, port.new), (1, 1, 1))
        # the board port subtree is the original of the port root, not "not taken" by the ecu root
        self.assertEqual(ecu.not_taken, ["src/demo.cpp"])
        self.assertEqual(port.not_taken, ["eth/Eth.cpp"])
        code, out = self.repo.cli("check")
        self.assertEqual(code, 0, out)
        self.assertIn("0 Fehler, 0 Warnungen", out)

    def test_report_formats_name_status_task_and_reason(self):
        code, text = self.repo.cli("report", "--all")
        self.assertEqual(code, 0)
        self.assertIn("~ bsp/Config.h  [ARCH-13] clock from the generated clock plan", text)
        self.assertIn("= src/main.cpp", text)
        self.assertIn("+ uds/Diag.cpp  (Muster uds/**)", text)
        self.assertIn("- eth/Eth.cpp", text)
        _, md = self.repo.cli("report", "--format", "markdown")
        self.assertIn("| ecu | `ecu` | `external/openbsw/app` | 2 | 1 | 2 | 1 |", md)
        self.assertIn("| `CMakeLists.txt` | geändert | PLAT-23 | family port template |", md)
        _, js = self.repo.cli("report", "--format", "json")
        self.assertIn('"error_count": 0', js)

    def test_crlf_copy_counts_as_identical(self):
        write(self.repo.root / "ecu/src/main.cpp", ORIGINALS["app/src/main.cpp"], newline="\r\n")
        self.assertEqual(codes(self.repo.check()), [])

    def test_hash_of_digits_stays_a_string(self):
        h = sync.normalized_hash(self.repo.orig("app/src/app.cpp"))
        self.repo.edit_list(f"upstream: {h}", "upstream: 0123456789012345")
        self.assertEqual(codes(self.repo.check()), [("hash-outdated", "ecu/src/app.cpp")])
        self.repo.edit_list("upstream: 0123456789012345", "upstream: 1234567890e12345")
        self.assertEqual(codes(self.repo.check()), [("hash-outdated", "ecu/src/app.cpp")])

    # ---------------------------------------------------------------- built-in faults: red

    def test_changed_identical_copy_without_reason_is_red(self):
        write(self.repo.root / "ecu/src/main.cpp", "int main() { return 1; }\n")
        report = self.repo.check()
        self.assertEqual(codes(report), [("identical-changed", "ecu/src/main.cpp")])
        self.assertEqual(report.errors[0].yaml, 'roots[0].files["src/main.cpp"].status')
        code, out = self.repo.cli("check")
        self.assertEqual(code, 1)
        self.assertIn("geänderte Kopie ohne Begründung", out)

    def test_modified_copy_without_reason_or_task_is_red(self):
        self.repo.drop_line("reason: family port template")
        self.assertEqual(codes(self.repo.check()), [("modified-without-reason", "ecu/CMakeLists.txt")])
        self.repo.drop_line("task: ARCH-13")
        self.assertEqual(codes(self.repo.check()), [("modified-without-reason", "ecu/CMakeLists.txt"),
                                                    ("modified-without-reason", "port/bsp/Config.h")])

    def test_upstream_change_of_identical_copy_is_red_until_accepted(self):
        write(self.repo.orig("app/platforms/board/safety/mpu.h"), "enum { REGIONS = NX_MPU_REGIONS };\n")
        report = self.repo.check()
        self.assertEqual(codes(report), [("upstream-changed", "port/safety/mpu.h")])
        self.assertIn("accept port/safety/mpu.h", report.errors[0].hint)
        # accept refuses while the identical copy still differs
        code, out = self.repo.cli("accept", "port/safety/mpu.h")
        self.assertEqual(code, 2, out)
        # copy follows upstream: only the outdated record is left (warning, red with --strict)
        shutil.copyfile(self.repo.orig("app/platforms/board/safety/mpu.h"), self.repo.root / "port/safety/mpu.h")
        self.assertEqual(codes(self.repo.check()), [("hash-outdated", "port/safety/mpu.h")])
        self.assertEqual(self.repo.cli("check", "--strict")[0], 1)
        code, out = self.repo.cli("accept", "port/safety/mpu.h")
        self.assertEqual(code, 0, out)
        self.assertEqual(codes(self.repo.check()), [])
        self.assertIn("# test list", self.repo.list.read_text(encoding="utf-8"))  # comments kept

    def test_upstream_and_local_change_of_identical_copy_is_red(self):
        write(self.repo.orig("app/src/app.cpp"), "void app() { upstream(); }\n")
        write(self.repo.root / "ecu/src/app.cpp", "void app() { local(); }\n")
        self.assertEqual(codes(self.repo.check()), [("upstream-and-copy-changed", "ecu/src/app.cpp")])

    def test_upstream_change_of_modified_copy_is_red_until_accepted(self):
        write(self.repo.orig("app/CMakeLists.txt"), "add_subdirectory(platforms/board)\nadd_subdirectory(rust)\n")
        self.assertEqual(codes(self.repo.check()), [("upstream-changed-modified", "ecu/CMakeLists.txt")])
        before = self.repo.list.read_text(encoding="utf-8").splitlines()
        code, out = self.repo.cli("accept", "--all-upstream")
        self.assertEqual(code, 0, out)
        self.assertIn("ecu/CMakeLists.txt: upstream", out)
        self.assertEqual(codes(self.repo.check()), [])
        after = self.repo.list.read_text(encoding="utf-8").splitlines()
        changed = [(a, b) for a, b in zip(before, after) if a != b]
        self.assertEqual(len(before), len(after))
        self.assertEqual(len(changed), 1)                                     # only the one hash line
        self.assertEqual(changed[0][1].strip(), f"upstream: {sync.normalized_hash(self.repo.orig('app/CMakeLists.txt'))}")
        # the commit is added to the openbsw section when the list had none
        self.assertEqual(self.repo.cli("accept", "--commit")[0], 2)          # synthetic submodule: no git checkout

    def test_unlisted_copy_is_red(self):
        write(self.repo.root / "ecu/src/demo.cpp", ORIGINALS["app/src/demo.cpp"])
        write(self.repo.root / "port/eth/Eth.cpp", "void eth() { changed(); }\n")
        report = self.repo.check()
        self.assertEqual(codes(report), [("unlisted-copy", "ecu/src/demo.cpp"), ("unlisted-copy", "port/eth/Eth.cpp")])
        self.assertIn("identische Kopie", report.errors[0].text)
        self.assertIn("weicht", report.errors[1].text)
        # a pattern for new files does not excuse a file that has an original
        write(self.repo.orig("app/uds/Diag.cpp"), "void diag() {}\n")
        self.assertIn(("unlisted-copy", "ecu/uds/Diag.cpp"), codes(self.repo.check()))

    def test_stale_entry_and_vanished_original_are_red(self):
        (self.repo.root / "ecu/src/rte.cpp").unlink()
        self.repo.orig("app/src/main.cpp").unlink()
        self.assertEqual(codes(self.repo.check(), "error"),
                         [("original-gone", "ecu/src/main.cpp"), ("stale-entry", "ecu/src/rte.cpp")])

    def test_new_entry_that_gets_an_original_is_red(self):
        write(self.repo.orig("app/src/rte.cpp"), "void rte() {}\n")
        self.assertEqual(codes(self.repo.check()), [("new-has-original", "ecu/src/rte.cpp")])

    def test_invalid_list_entries_are_red_with_yaml_path(self):
        self.repo.edit_list("task: ARCH-13", "task: clock")
        self.repo.edit_list("src/rte.cpp: {status: new, task: RTE-02}", "src/rte.cpp: {status: novel}")
        report = self.repo.check()
        yaml_paths = {f.yaml for f in report.errors if f.code == "list-invalid"}
        self.assertIn('roots[1].files["bsp/Config.h"].task', yaml_paths)
        self.assertIn('roots[0].files["src/rte.cpp"].status', yaml_paths)
        self.assertEqual(self.repo.cli("check")[0], 1)

    def test_missing_upstream_hash_and_missing_submodule_are_red(self):
        text = self.repo.list.read_text(encoding="utf-8")
        self.repo.list.write_text(text.replace("src/main.cpp: {status: identical, upstream: ",
                                               "src/main.cpp: {status: identical, old: "), encoding="utf-8")
        self.assertIn(('roots[0].files["src/main.cpp"].upstream'),
                      {f.yaml for f in self.repo.check().errors})
        shutil.rmtree(self.repo.root / "external/openbsw")
        report = self.repo.check()
        self.assertTrue(any("git submodule update" in f.hint for f in report.errors))

    # ---------------------------------------------------------------- warnings

    def test_new_file_without_entry_is_a_warning_only(self):
        write(self.repo.root / "port/main/Integration.cpp", "void integration() {}\n")
        report = self.repo.check()
        self.assertEqual(codes(report), [("unlisted-new", "port/main/Integration.cpp")])
        self.assertEqual(report.errors, [])
        self.assertEqual(self.repo.cli("check")[0], 0)
        self.assertEqual(self.repo.cli("check", "--strict")[0], 1)
        code, out = self.repo.cli("skeleton")
        self.assertEqual(code, 0)
        self.assertIn('"main/Integration.cpp": {status: new}', out)

    def test_modified_copy_back_to_original_and_unused_pattern_are_warnings(self):
        shutil.copyfile(self.repo.orig("app/CMakeLists.txt"), self.repo.root / "ecu/CMakeLists.txt")
        shutil.rmtree(self.repo.root / "ecu/uds")
        self.assertEqual(codes(self.repo.check()), [("modified-now-identical", "ecu/CMakeLists.txt"),
                                                    ("unused-pattern", "ecu/uds/**")])

    # ---------------------------------------------------------------- PLAT-54: moving a root

    def test_moving_the_port_below_ecu_only_changes_the_root_path(self):
        shutil.move(str(self.repo.root / "port"), str(self.repo.root / "ecu/platforms/family"))
        report = self.repo.check()
        self.assertIn(("list-invalid", None), codes(report))                  # root directory port is gone
        self.assertIn(("unlisted-new", "ecu/platforms/family/bsp/Config.h"), codes(report))
        self.repo.edit_list("path: port\n", "path: ecu/platforms/family\n")
        report = self.repo.check()
        self.assertEqual(codes(report), [])
        ecu, port = report.roots
        self.assertEqual((ecu.identical, ecu.modified, ecu.new), (2, 1, 2))   # nested root is not counted twice
        self.assertEqual((port.identical, port.modified, port.new), (1, 1, 1))


@unittest.skipUnless((REPO / "external/openbsw/executables/referenceApp").is_dir(),
                     "Submodul external/openbsw fehlt (git submodule update --init external/openbsw)")
class RealRepositoryTest(unittest.TestCase):
    """The checked-in list against this repository and its submodule."""

    LIST = REPO / "tools/openbswsync/copies.yaml"

    def test_repository_matches_the_list(self):
        report = sync.check(REPO, sync.load_list(self.LIST))
        print("\n" + sync.format_text(report, findings_only=True), file=sys.stderr)
        self.assertEqual([str(f) for f in report.errors], [])
        names = [r.name for r in report.roots]
        self.assertEqual(names, ["ecu", "port-s32k1xx", "bspClock-s32k1xx", "bspFlexCan-s32k1xx"])
        for summary in report.roots:
            # COM-26/PLAT-85: both files of the driver copies (FlexCAN, clock) are changed
            if summary.name not in ("bspFlexCan-s32k1xx", "bspClock-s32k1xx"):
                self.assertGreater(summary.identical, 0, summary.name)
            self.assertGreater(summary.modified, 0, summary.name)

    def test_every_modified_copy_has_task_and_reason(self):
        copy_list = sync.load_list(self.LIST)
        for root in copy_list.roots:
            for entry in root.entries.values():
                if entry.status == "modified":
                    self.assertTrue(entry.task and len(entry.reason) > 20, f"{root.name}: {entry.key}")

    def test_built_in_faults_against_the_real_list(self):
        """Faults injected into the loaded list (the repository stays untouched) must turn the check red."""
        base = sync.load_list(self.LIST)
        root = base.roots[0]
        identical = next(e for e in root.entries.values() if e.status == "identical")
        modified = next(e for e in root.entries.values() if e.status == "modified")

        faulty = copy.deepcopy(base)
        faulty.roots[0].entries[modified.key].status = "identical"            # changed copy claimed identical
        self.assertIn(("identical-changed", f"{root.path}/{modified.key}"), codes(sync.check(REPO, faulty)))

        faulty = copy.deepcopy(base)
        faulty.roots[0].entries[modified.key].reason = ""                     # reason missing
        self.assertIn(("modified-without-reason", f"{root.path}/{modified.key}"), codes(sync.check(REPO, faulty)))

        faulty = copy.deepcopy(base)
        faulty.roots[0].entries[modified.key].upstream = "0" * sync.HASH_LEN   # original changed since record
        self.assertIn(("upstream-changed-modified", f"{root.path}/{modified.key}"),
                      codes(sync.check(REPO, faulty)))

        faulty = copy.deepcopy(base)
        del faulty.roots[0].entries[identical.key]                            # copy not listed
        self.assertIn(("unlisted-copy", f"{root.path}/{identical.key}"), codes(sync.check(REPO, faulty)))

    def test_simulated_submodule_update_of_the_real_reference_app(self):
        """Real ecu/ and referenceApp copied to a temporary tree; an upstream edit of one original that ecu/
        copies identically must be red, the other copies stay green."""
        base = sync.load_list(self.LIST)
        root = next(r for r in base.roots if r.name == "ecu")
        identical = next(e for e in root.entries.values() if e.status == "identical")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_repo = Path(tmp)
            shutil.copytree(REPO / root.path, tmp_repo / root.path)
            shutil.copytree(REPO / base.submodule / root.original, tmp_repo / base.submodule / root.original)
            original = tmp_repo / base.submodule / root.original / identical.key
            original.write_bytes(original.read_bytes() + b"\n// upstream change\n")
            only_ecu = copy.deepcopy(base)
            only_ecu.roots = [copy.deepcopy(root)]
            only_ecu.roots[0].index = 0
            report = sync.check(tmp_repo, only_ecu)
        self.assertEqual([(f.code, f.path) for f in report.errors],
                         [("upstream-changed", f"{root.path}/{identical.key}")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
