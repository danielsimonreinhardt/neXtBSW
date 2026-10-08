#!/usr/bin/env python3
"""Tests of the coverage measurement (TOOL-40).

Two kinds: the aggregation, the thresholds and the module assignment are checked with invented data, and one
test walks the whole chain for real - `zig cc --coverage`, the gcov runtime, gcov - over the fixture
`fixtures/coverage_probe.c`, whose uncovered lines and untaken branch have to show up as such. A coverage tool
that reports everything as covered would pass every other test here.

    .venv/Scripts/python -m unittest tools/test/test_coverage.py
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import coverage as cov  # noqa: E402
import run_tests as rt  # noqa: E402


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def lines(*spec: tuple[int, bool, list[bool]]) -> dict[int, tuple[bool, list[bool]]]:
    return {number: (executed, branches) for number, executed, branches in spec}


class ThresholdFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nxcovcfg_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_reads_defaults_and_modules(self) -> None:
        path = write(self.tmp / "c.yaml", "version: 1\ndefaults: {lines: 70, branches: 55}\n"
                                          "exclude: ['external/**']\n"
                                          "modules:\n  libs/bsw/e2e: {lines: 95, branches: 90, reason: ASIL}\n")
        thresholds = cov.load_thresholds(path)
        self.assertEqual((70.0, 55.0), (thresholds.lines, thresholds.branches))
        self.assertEqual(95, thresholds.modules["libs/bsw/e2e"]["lines"])
        self.assertEqual(["external/**"], thresholds.exclude)
        self.assertFalse(thresholds.enforce)

    def test_wrong_version_is_an_error(self) -> None:
        path = write(self.tmp / "c.yaml", "version: 2\n")
        with self.assertRaises(rt.ListError):
            cov.load_thresholds(path)

    def test_threshold_without_reason_is_an_error(self) -> None:
        path = write(self.tmp / "c.yaml", "version: 1\nmodules:\n  libs/bsw/e2e: {lines: 95}\n")
        with self.assertRaises(rt.ListError):
            cov.load_thresholds(path)

    def test_unknown_field_is_an_error(self) -> None:
        path = write(self.tmp / "c.yaml",
                     "version: 1\nmodules:\n  libs/bsw/e2e: {mcdc: 100, reason: spaeter}\n")
        with self.assertRaises(rt.ListError):
            cov.load_thresholds(path)

    def test_percentage_out_of_range_is_an_error(self) -> None:
        path = write(self.tmp / "c.yaml",
                     "version: 1\nmodules:\n  libs/bsw/e2e: {lines: 150, reason: zu viel}\n")
        with self.assertRaises(rt.ListError):
            cov.load_thresholds(path)

    def test_messages_name_the_path_inside_the_file(self) -> None:
        """A message without a YAML path cannot be shown next to the value (Studio, review question 8)."""
        path = write(self.tmp / "c.yaml", "version: 1\nmodules:\n  libs/bsw/e2e: {lines: 95}\n")
        with self.assertRaises(rt.ListError) as caught:
            cov.load_thresholds(path)
        self.assertIn("modules.libs/bsw/e2e", str(caught.exception))
        self.assertIn("c.yaml", str(caught.exception))


class ModuleAssignmentTest(unittest.TestCase):
    directories = ["platforms/s32k1xx/port/safety/safeSystem", "libs/bsw/canSystem", "libs/bsw/e2e"]

    def test_manifest_directory_wins(self) -> None:
        self.assertEqual("libs/bsw/e2e", cov.module_of("libs/bsw/e2e/src/e2e/E2E_P01.c", self.directories))
        self.assertEqual("platforms/s32k1xx/port/safety/safeSystem",
                         cov.module_of("platforms/s32k1xx/port/safety/safeSystem/src/safeSystem/SafeSystem.cpp",
                                       self.directories))

    def test_without_manifest_the_component_directory(self) -> None:
        self.assertEqual("ecu/application", cov.module_of("ecu/application/src/systems/RteSystem.cpp", []))
        self.assertEqual("platforms/s32k1xx/bsp/bspLpspi",
                         cov.module_of("platforms/s32k1xx/bsp/bspLpspi/src/spi/Lpspi.cpp", []))

    def test_a_similar_prefix_is_not_the_module(self) -> None:
        self.assertNotEqual("libs/bsw/e2e", cov.module_of("libs/bsw/e2eGateway/src/G.cpp", self.directories))

    def test_the_repository_really_has_these_modules(self) -> None:
        directories = cov.module_directories(cov.REPO)
        self.assertIn("libs/bsw/e2e", directories)
        self.assertIn("libs/bsw/canSystem", directories)


class MergeTest(unittest.TestCase):
    def test_executed_and_branches_are_or_ed(self) -> None:
        into = {"a.c": lines((7, False, [False, True]))}
        cov.merge(into, {"a.c": lines((7, True, [True, False]))})
        self.assertEqual((True, [True, True]), into["a.c"][7])

    def test_different_branch_counts_keep_every_edge(self) -> None:
        into = {"a.c": lines((7, True, [True]))}
        cov.merge(into, {"a.c": lines((7, False, [False, False, True]))})
        self.assertEqual([True, False, True], into["a.c"][7][1])

    def test_new_file_is_taken_over(self) -> None:
        into: dict = {}
        cov.merge(into, {"b.c": lines((1, True, []))})
        self.assertEqual({1: (True, [])}, into["b.c"])


class SummaryTest(unittest.TestCase):
    files = {
        "libs/bsw/e2e/src/e2e/E2E.c": lines((1, True, [True, False]), (2, True, []), (3, False, [])),
        "libs/bsw/e2e/test/host/E2eHostTest.c": lines((1, True, []), (2, True, [])),
        "external/openbsw/libs/x/src/X.cpp": lines((1, False, [])),
        "libs/bsw/hallDecoder/src/hallDecoder/HallDecoder.cpp": lines((1, True, []), (2, True, [])),
    }

    def summarise(self, **kwargs) -> dict:
        thresholds = cov.Thresholds(exclude=["external/**", "**/test/**"], **kwargs)
        return cov.summarise(self.files, thresholds, cov.REPO)

    def test_excluded_paths_do_not_count(self) -> None:
        payload = self.summarise()
        self.assertEqual(["libs/bsw/e2e", "libs/bsw/hallDecoder"], [m["module"] for m in payload["modules"]])
        self.assertEqual(5, payload["total"]["lines_total"])

    def test_percentages(self) -> None:
        e2e = self.summarise()["modules"][0]
        self.assertEqual(66.7, e2e["line_percent"])     # 2 of 3 lines
        self.assertEqual(50.0, e2e["branch_percent"])   # 1 of 2 edges

    def test_module_below_its_threshold(self) -> None:
        payload = self.summarise(modules={"libs/bsw/e2e": {"lines": 95, "branches": 90, "reason": "ASIL"}})
        e2e = payload["modules"][0]
        self.assertEqual(cov.UNDER, e2e["verdict"])
        self.assertTrue(e2e["own_threshold"])
        self.assertEqual(cov.NO_RULE, payload["modules"][1]["verdict"], "ohne Schwelle kein Urteil")

    def test_module_at_its_threshold_is_ok(self) -> None:
        payload = self.summarise(modules={"libs/bsw/e2e": {"lines": 66.7, "branches": 50, "reason": "x"}})
        self.assertEqual(cov.OK, payload["modules"][0]["verdict"])

    def test_default_threshold_applies_to_every_module(self) -> None:
        payload = self.summarise(lines=90.0)
        self.assertEqual(cov.UNDER, payload["modules"][0]["verdict"])
        self.assertEqual(cov.OK, payload["modules"][1]["verdict"])
        self.assertFalse(payload["modules"][0]["own_threshold"])


class MarkdownTest(unittest.TestCase):
    def payload(self) -> dict:
        thresholds = cov.Thresholds(modules={"libs/bsw/e2e": {"lines": 95, "branches": 90, "reason": "ASIL"}})
        payload = cov.summarise({"libs/bsw/e2e/src/e2e/E2E.c": lines((1, True, []), (2, False, []))},
                                thresholds, cov.REPO)
        payload.update(gcov="arm-none-eabi-gcov", duration_s=12.0, enforced=False,
                       entries={"total": 2, "pass": 1, "fail": 1, "skip": 0,
                                "items": [{"name": "e2e", "status": "pass", "reason": ""},
                                          {"name": "kaputt", "status": "fail", "reason": "Binden\nmehr"}]})
        return payload

    def test_table_and_problems(self) -> None:
        text = cov.render_markdown(self.payload())
        self.assertIn("| `libs/bsw/e2e` |", text)
        self.assertIn("50.0 %", text)
        self.assertIn("95/90", text)
        self.assertIn(cov.UNDER, text)
        self.assertIn("kaputt (fail): Binden", text)
        self.assertIn("noch nicht erzwungen", text)


class SelectionTest(unittest.TestCase):
    def test_only_zig_entries(self) -> None:
        data = rt.load_list()
        chosen = cov.select(data["tests"], None)
        self.assertTrue(chosen)
        self.assertEqual({"zig"}, {t.kind for t in chosen})
        self.assertEqual(["e2e"], [t.name for t in cov.select(data["tests"], "e2e") if t.name == "e2e"])


class RealMeasurementTest(unittest.TestCase):
    """The whole chain over fixtures/coverage_probe.c - the only test that proves the numbers are real."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import ziglang  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the workstation
            raise unittest.SkipTest("ziglang fehlt")
        if not cov.find_gcov():
            raise unittest.SkipTest("kein gcov (NX_ARM_GCC_BIN nicht gesetzt?)")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nxcovreal_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def measure(self) -> dict:
        probe = (HERE / "fixtures" / "coverage_probe.c").relative_to(cov.REPO).as_posix()
        tests = write(self.tmp / "tests.yaml",
                      "version: 1\ngroups: [host]\ntests:\n"
                      "  - name: coverage-probe\n    group: host\n    kind: zig\n"
                      "    comment: Fixture der Abdeckungsmessung\n    driver: cc\n"
                      f"    sources: [{probe}]\n    covers: []\n")
        thresholds = write(self.tmp / "coverage.yaml",
                           "version: 1\ndefaults: {lines: 100, branches: 100}\nexclude: ['external/**']\n")
        result = self.tmp / "coverage.json"
        with redirect_stdout(io.StringIO()):  # the report belongs in the file, not in the test output
            code = cov.main(["--tests", str(tests), "--thresholds", str(thresholds), "--json", str(result),
                             "--jobs", "1", "--out", str(self.tmp / "work")])
        self.assertTrue(result.is_file(), "kein Ergebnis geschrieben")
        self.payload = json.loads(result.read_text(encoding="utf-8"))
        self.code = code
        return self.payload

    def probe_file(self, payload: dict) -> dict:
        for module in payload["modules"]:
            for entry in module["files"]:
                if entry["file"].endswith("fixtures/coverage_probe.c"):
                    return entry
        self.fail(f"coverage_probe.c fehlt im Bericht: {[m['module'] for m in payload['modules']]}")

    def test_uncovered_code_is_reported_as_uncovered(self) -> None:
        payload = self.measure()
        self.assertEqual(1, payload["entries"]["pass"], payload["entries"]["items"])
        entry = self.probe_file(payload)
        self.assertGreater(entry["lines_total"], entry["lines_covered"],
                           "never_called() darf nicht als abgedeckt gelten")
        self.assertGreater(entry["branches_total"], entry["branches_covered"],
                           "die nie genommene Bedingung darf nicht als abgedeckt gelten")
        self.assertGreater(entry["lines_covered"], 0, "der ausgefuehrte Teil muss abgedeckt sein")

    def test_threshold_not_reached_without_enforce_is_green(self) -> None:
        self.measure()
        self.assertEqual(0, self.code, "ohne --enforce macht eine verfehlte Schwelle den Lauf nicht rot")

    def test_threshold_not_reached_with_enforce_is_red(self) -> None:
        probe = (HERE / "fixtures" / "coverage_probe.c").relative_to(cov.REPO).as_posix()
        tests = write(self.tmp / "tests.yaml",
                      "version: 1\ngroups: [host]\ntests:\n"
                      "  - name: coverage-probe\n    group: host\n    kind: zig\n"
                      "    comment: Fixture der Abdeckungsmessung\n    driver: cc\n"
                      f"    sources: [{probe}]\n    covers: []\n")
        thresholds = write(self.tmp / "coverage.yaml",
                           "version: 1\ndefaults: {lines: 100, branches: 100}\nexclude: ['external/**']\n")
        with redirect_stdout(io.StringIO()):
            code = cov.main(["--tests", str(tests), "--thresholds", str(thresholds), "--jobs", "1",
                             "--out", str(self.tmp / "work2"), "--enforce"])
        self.assertEqual(1, code)


class RepositoryThresholdTest(unittest.TestCase):
    """The threshold file of the repository is loadable and names only modules that exist."""

    def test_file_loads(self) -> None:
        thresholds = cov.load_thresholds()
        self.assertGreaterEqual(thresholds.lines, 0)
        directories = set(cov.module_directories(cov.REPO))
        for name in thresholds.modules:
            with self.subTest(module=name):
                self.assertTrue(name in directories or (cov.REPO / name).is_dir(),
                                f"{name} ist kein Modulordner des Repositoriums")


PROBE_LOGIC = """\
/* synthetic customer component of tools/test/test_coverage.py (TOOL-217) - test data, no real customer */
int sigma_clamp(int value)
{
    if (value > 100) { return 100; }
    if (value < -100) { return -100; }   /* never taken by the host test */
    return value;
}

int sigma_unused(int value)             /* never called */
{
    return value * 3;
}
"""

PROBE_TEST = """\
#include <stdio.h>
int sigma_clamp(int value);
int main(void)
{
    if (sigma_clamp(150) != 100 || sigma_clamp(7) != 7) { printf("1 checks, 1 failures\\n"); return 1; }
    printf("2 checks, 0 failures\\n");
    return 0;
}
"""


def make_workspace(tmp: Path, thresholds: bool = True) -> Path:
    """Synthetic customer repository `sigma` pinned to this tree (TOOL-217): one C component with a host test."""
    ws = tmp / "sigma"
    version = (cov.REPO / "VERSION").read_text(encoding="utf-8").strip()
    relative = Path(__import__("os").path.relpath(cov.REPO, ws)).as_posix()
    write(ws / "nextbsw.yaml", "# synthetic workspace of tools/test/test_coverage.py - test data, no real customer\n"
                               f"schema_version: 1\nnextbsw:\n  version: {version}\n  source: submodule\n"
                               f"  path: {relative}\n  profile: open\nsearch:\n  modules: [swc]\n")
    write(ws / "swc" / "SigmaClamp" / "module.yaml", "name: SigmaClamp\n")
    write(ws / "swc" / "SigmaClamp" / "src" / "sigma_clamp.c", PROBE_LOGIC)
    write(ws / "swc" / "SigmaClamp" / "test" / "SigmaClampHostTest.c", PROBE_TEST)
    write(ws / "ci" / "tests.yaml",
          "version: 1\ngroups: [host]\n"
          "tools:\n  ziglang:\n    kind: python_module\n    module: ziglang\n"
          "tests:\n"
          "  - name: sigma-clamp\n    group: host\n    kind: zig\n    driver: cc\n"
          "    comment: erfundener Host-Test des Kunden\n"
          "    sources: [swc/SigmaClamp/src/sigma_clamp.c, swc/SigmaClamp/test/SigmaClampHostTest.c]\n"
          "    requires: [ziglang]\n"
          "    covers: [swc/SigmaClamp/test/SigmaClampHostTest.c]\n")
    if thresholds:
        write(ws / "ci" / "coverage.yaml",
              "version: 1\ndefaults: {lines: 50, branches: 0}\nexclude: ['**/test/**']\n"
              "modules:\n  swc/SigmaClamp: {lines: 100, branches: 100, reason: erfundene Sicherheitsfunktion}\n")
    return ws


class WorkspaceConfigurationTest(unittest.TestCase):
    """TOOL-217 (gap N19): what a workspace run measures - without compiling anything."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nxcovws_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_modules_are_the_manifest_folders_of_the_workspace(self) -> None:
        ws = make_workspace(self.tmp)
        support = cov.ws_support.open_workspace(ws, cov.REPO)
        self.assertEqual(["swc/SigmaClamp"], cov.workspace_module_directories(support))

    def test_only_files_of_the_workspace_outside_the_tree_are_instrumented(self) -> None:
        root = (self.tmp / "ws").resolve()
        nested_tree = root / "nextbsw"
        write(root / "swc" / "a.c", "int a;\n")
        write(nested_tree / "libs" / "b.c", "int b;\n")
        self.assertTrue(cov._instrumented_own(root / "swc" / "a.c", root, [nested_tree]))
        self.assertFalse(cov._instrumented_own(nested_tree / "libs" / "b.c", root, [nested_tree]))
        self.assertFalse(cov._instrumented_own(cov.REPO / "tools" / "test" / "fixtures" / "coverage_probe.c",
                                               root, [nested_tree]))

    def test_missing_thresholds_are_named_with_the_template(self) -> None:
        ws = make_workspace(self.tmp, thresholds=False)
        err = io.StringIO()
        from contextlib import redirect_stderr
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(2, cov.main(["--workspace", str(ws), "--jobs", "1"]))
        self.assertIn("coverage.yaml", err.getvalue())
        self.assertIn("template coverage", err.getvalue())

    def test_the_tree_template_loads(self) -> None:
        thresholds = cov.load_thresholds(cov.REPO / "tools" / "ci" / "templates" / "workspace-coverage.yaml")
        self.assertFalse(thresholds.enforce)
        self.assertEqual({}, thresholds.modules)


class WorkspaceMeasurementTest(unittest.TestCase):
    """TOOL-217: a real measurement of a synthetic customer component - own code counted, thresholds his own."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import ziglang  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the workstation
            raise unittest.SkipTest("ziglang fehlt")
        if not cov.find_gcov():
            raise unittest.SkipTest("kein gcov (NX_ARM_GCC_BIN nicht gesetzt?)")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nxcovwsreal_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_own_component_measured_against_own_thresholds(self) -> None:
        ws = make_workspace(self.tmp)
        result = self.tmp / "coverage.json"
        with redirect_stdout(io.StringIO()):
            code = cov.main(["--workspace", str(ws), "--json", str(result), "--jobs", "1",
                             "--out", str(self.tmp / "work"), "--build-root", str(self.tmp / "bau")])
        payload = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual(1, payload["entries"]["pass"], payload["entries"]["items"])
        self.assertEqual(str(ws.resolve()), payload["workspace"])
        self.assertEqual(["swc/SigmaClamp"], [m["module"] for m in payload["modules"]])
        module = payload["modules"][0]
        self.assertEqual(["swc/SigmaClamp/src/sigma_clamp.c"], [f["file"] for f in module["files"]],
                         "nur eigener Code ohne Testcode, nie der Baum")
        self.assertGreater(module["lines_total"], module["lines_covered"], "sigma_unused darf nicht abgedeckt sein")
        self.assertEqual(100.0, module["threshold_lines"], "die Schwelle kommt aus ci/coverage.yaml des Workspace")
        self.assertEqual(cov.UNDER, module["verdict"])
        self.assertEqual(0, code, "nicht erzwungen: verfehlte Schwelle ist kein rotes Ergebnis")
        with redirect_stdout(io.StringIO()):
            code = cov.main(["--workspace", str(ws), "--jobs", "1", "--enforce",
                             "--out", str(self.tmp / "work2"), "--build-root", str(self.tmp / "bau")])
        self.assertEqual(1, code)


if __name__ == "__main__":
    unittest.main()
