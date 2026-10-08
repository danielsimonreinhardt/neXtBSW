"""Tests of the unified test call itself (TOOL-04).

The runner is exercised with invented groups and invented tests, never with the real test list: green, red,
skipped (missing tool and missing precondition), timeout, a real ziglang host test and one that must not compile.
Checked are the status per test, the exit code for CI, the JSON file, the selection options and the completeness
check against the test files of the repository.

Run: .venv/Scripts/python -m unittest tools/test/test_run_tests.py
"""

import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_tests  # noqa: E402

# Invented test list. The groups (gruen, rot, misc) exist nowhere else in the repository.
FIXTURE = """
version: 1
groups: [gruen, rot, misc]
tools:
  gibtsnicht:
    kind: env
    name: NX_TOOL_THAT_DOES_NOT_EXIST
    hint: erfundenes Werkzeug
  ziglang:
    kind: python_module
    module: ziglang
defaults:
  zig:
    driver: cc
    std: c99
    flags: [-Wall, -Werror]
    requires: [ziglang]
    timeout: 300
inventory:
  patterns: ["test_*.py"]
  skip_dirs: [nicht_hier]
  ignore:
    - path: unterverzeichnis/test_bewusst_ignoriert.py
      comment: Beispiel fuer einen bewusst nicht gelisteten Fund
tests:
  - name: gruen-einfach
    group: gruen
    kind: command
    comment: laeuft durch und liefert Exit-Code 0
    argv: ["{python}", -c, "print('7 checks, 0 failures')"]
    covers: []
  - name: gruen-erwartung
    group: gruen
    kind: command
    comment: erwartete Ausgabe ist vorhanden
    argv: ["{python}", -c, "print('alles gut')"]
    expect: "alles gut"
    covers: []
  - name: gruen-nach-abhaengigkeit
    group: gruen
    kind: command
    comment: laeuft erst nach gruen-einfach
    argv: ["{python}", -c, "print('spaeter')"]
    after: [gruen-einfach]
    covers: []
  - name: rot-exitcode
    group: rot
    kind: command
    comment: Exit-Code 3 muss rot sein
    argv: ["{python}", -c, "import sys; sys.exit(3)"]
    covers: []
  - name: rot-erwartung
    group: rot
    kind: command
    comment: Exit-Code 0, aber die erwartete Ausgabe fehlt
    argv: ["{python}", -c, "print('etwas anderes')"]
    expect: "genau das hier"
    covers: []
  - name: rot-fehlerzeile
    group: rot
    kind: command
    comment: Host-Test-Ausgabe mit Fehlern muss rot sein
    argv: ["{python}", -c, "print('7 checks, 1 failures')"]
    expect: "\\\\d+ checks, 0 failures"
    covers: []
  - name: rot-zeit
    group: rot
    kind: command
    comment: Zeitueberschreitung muss rot sein
    argv: ["{python}", -c, "import time; time.sleep(60)"]
    timeout: 2
    covers: []
  - name: rot-eintrag-kaputt
    group: rot
    kind: command
    comment: Eintrag ohne argv muss rot sein, nicht gruen
    covers: []
  - name: uebers-werkzeug
    group: misc
    kind: command
    comment: fehlendes Werkzeug fuehrt zu uebersprungen
    argv: ["{python}", -c, "print('nie')"]
    requires: [gibtsnicht]
    covers: []
  - name: uebers-datei
    group: misc
    kind: command
    comment: fehlende Vorbedingung fuehrt zu uebersprungen
    argv: ["{python}", -c, "print('nie')"]
    needs_files: ["{tmp}/gibtsnicht.dat"]
    covers: []
  - name: langsam
    group: misc
    kind: command
    comment: nur mit --slow
    argv: ["{python}", -c, "print('langsam')"]
    slow: true
    covers: []
"""

ZIG_FIXTURE = """
version: 1
groups: [misc]
tools:
  ziglang:
    kind: python_module
    module: ziglang
defaults:
  zig:
    driver: cc
    std: c99
    flags: [-Wall, -Werror]
    requires: [ziglang]
    timeout: 300
inventory:
  patterns: []
tests:
  - name: zig-gruen
    group: misc
    kind: zig
    comment: echter ziglang-Host-Test mit der erwarteten Schlusszeile
    sources: [gut.c]
    covers: []
  - name: zig-fehler
    group: misc
    kind: zig
    comment: Host-Test mit Fehlern in der Schlusszeile
    sources: [schlecht.c]
    covers: []
  - name: zig-uebersetzt-nicht
    group: misc
    kind: zig
    comment: Quelle mit Syntaxfehler - Uebersetzen muss rot sein
    sources: [kaputt.c]
    covers: []
"""

GOOD_C = '#include <stdio.h>\nint main(void) { printf("5 checks, 0 failures\\n"); return 0; }\n'
BAD_C = '#include <stdio.h>\nint main(void) { printf("5 checks, 2 failures\\n"); return 1; }\n'
BROKEN_C = "int main(void) { return nonsense; }\n"


def run(argv, tests, repo):
    """Runs the runner in this process and returns (exit code, output)."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = run_tests.main([*argv, "--tests", str(tests), "--repo", str(repo)])
    return code, buffer.getvalue()


class FixtureRun(unittest.TestCase):
    """One complete run of the invented list; every status must appear exactly as expected."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nxtest_self_"))
        cls.list_file = cls.tmp / "tests.yaml"
        cls.list_file.write_text(FIXTURE, encoding="utf-8")
        cls.repo = cls.tmp / "repo"
        (cls.repo / "unterverzeichnis").mkdir(parents=True)
        cls.json = cls.tmp / "ergebnis.json"
        cls.code, cls.out = run(["--json", str(cls.json), "--jobs", "4"], cls.list_file, cls.repo)
        cls.data = json.loads(cls.json.read_text(encoding="utf-8"))
        cls.status = {t["name"]: t for t in cls.data["tests"]}

    def test_exit_code_is_red(self):
        self.assertEqual(self.code, 1, self.out)

    def test_green_tests_are_green(self):
        for name in ("gruen-einfach", "gruen-erwartung", "gruen-nach-abhaengigkeit"):
            self.assertEqual(self.status[name]["status"], "pass", self.status[name])

    def test_exit_code_of_a_test_decides(self):
        self.assertEqual(self.status["rot-exitcode"]["status"], "fail")
        self.assertEqual(self.status["rot-exitcode"]["returncode"], 3)

    def test_missing_expected_output_is_red(self):
        self.assertEqual(self.status["rot-erwartung"]["status"], "fail")
        self.assertIn("erwartete Ausgabe fehlt", self.status["rot-erwartung"]["reason"])

    def test_host_test_with_failures_is_red(self):
        # exit code 0 but "1 failures" in the last line: only the expected pattern catches this
        self.assertEqual(self.status["rot-fehlerzeile"]["status"], "fail")
        self.assertEqual(self.status["rot-fehlerzeile"]["returncode"], 0)

    def test_timeout_is_red_and_named(self):
        entry = self.status["rot-zeit"]
        self.assertEqual(entry["status"], "timeout")
        self.assertIn("Zeitüberschreitung", entry["reason"])
        self.assertLess(entry["duration_s"], 30, "the process must be killed, not waited out")

    def test_broken_entry_is_red_not_green(self):
        self.assertEqual(self.status["rot-eintrag-kaputt"]["status"], "fail")
        self.assertIn("nicht ausführbar", self.status["rot-eintrag-kaputt"]["reason"])

    def test_missing_tool_is_skipped(self):
        entry = self.status["uebers-werkzeug"]
        self.assertEqual(entry["status"], "skip")
        self.assertIn("NX_TOOL_THAT_DOES_NOT_EXIST", entry["reason"])

    def test_missing_precondition_is_skipped(self):
        entry = self.status["uebers-datei"]
        self.assertEqual(entry["status"], "skip")
        self.assertIn("Vorbedingung fehlt", entry["reason"])

    def test_slow_test_is_not_run_without_slow(self):
        self.assertNotIn("langsam", self.status)

    def test_summary_counts(self):
        summary = self.data["summary"]
        self.assertEqual(summary["pass"], 3)
        self.assertEqual(summary["fail"], 4)
        self.assertEqual(summary["timeout"], 1)
        self.assertEqual(summary["skip"], 2)
        self.assertEqual(summary["total"], 10)

    def test_output_of_failed_tests_is_kept_and_of_green_ones_not(self):
        self.assertIn("output", self.status["rot-exitcode"])
        self.assertEqual(self.status["rot-exitcode"]["output"].strip(), "")  # exit(3) prints nothing
        self.assertIn("etwas anderes", self.status["rot-erwartung"]["output"])
        self.assertEqual(self.status["gruen-einfach"]["output"], "")

    def test_json_has_group_and_duration_per_test(self):
        for entry in self.data["tests"]:
            self.assertIn(entry["group"], ("gruen", "rot", "misc"))
            self.assertIsInstance(entry["duration_s"], float)
            self.assertTrue(entry["command"] or entry["status"] in ("skip", "fail"))
        self.assertGreater(self.data["duration_s"], 0)

    def test_report_names_every_red_test(self):
        for name in ("rot-exitcode", "rot-erwartung", "rot-fehlerzeile", "rot-zeit"):
            self.assertRegex(self.out, rf"ROT:.*|{re.escape(name)}")
        self.assertIn("Ergebnis: FEHLER", self.out)

    def test_number_of_test_cases_is_read_from_the_output(self):
        self.assertEqual(self.status["gruen-einfach"]["cases"], 7)


class Selection(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxtest_sel_"))
        self.list_file = self.tmp / "tests.yaml"
        self.list_file.write_text(FIXTURE, encoding="utf-8")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()

    def test_group_selection(self):
        code, out = run(["--group", "gruen"], self.list_file, self.repo)
        self.assertEqual(code, 0, out)
        self.assertIn("3 Test(s)", out)
        self.assertNotIn("rot-exitcode", out)

    def test_unknown_group_is_an_error(self):
        code, out = run(["--group", "gibtsnicht"], self.list_file, self.repo)
        self.assertEqual(code, 2)

    def test_pattern_selection(self):
        code, out = run(["-k", "uebers"], self.list_file, self.repo)
        self.assertEqual(code, 0, out)
        self.assertIn("2 Test(s)", out)

    def test_glob_pattern_selection(self):
        code, out = run(["-k", "gruen-*", "--list"], self.list_file, self.repo)
        self.assertEqual(code, 0)
        self.assertIn("3 Einträge", out)

    def test_slow_flag_adds_the_slow_test(self):
        code, out = run(["--slow", "-k", "langsam"], self.list_file, self.repo)
        self.assertEqual(code, 0, out)
        self.assertIn("langsam", out)

    def test_list_does_not_run_anything(self):
        code, out = run(["--list"], self.list_file, self.repo)
        self.assertEqual(code, 0)
        self.assertIn("10 Einträge", out)
        self.assertNotIn("Ergebnis:", out)

    def test_empty_selection_is_an_error(self):
        code, _ = run(["-k", "nichts-passt"], self.list_file, self.repo)
        self.assertEqual(code, 2)

    def test_timeout_option_overrides_the_list(self):
        code, out = run(["-k", "gruen-einfach", "--timeout", "120"], self.list_file, self.repo)
        self.assertEqual(code, 0, out)

    def test_dependency_runs_first_even_in_parallel(self):
        code, out = run(["--group", "gruen", "--jobs", "4"], self.list_file, self.repo)
        self.assertEqual(code, 0, out)
        order = [line for line in out.splitlines() if line.startswith("[")]
        names = [re.search(r"(gruen-[\w-]+)", line).group(1) for line in order]
        self.assertLess(names.index("gruen-einfach"), names.index("gruen-nach-abhaengigkeit"))


class Inventory(unittest.TestCase):
    """The list must stay complete: a test file in the repository that is in no `covers` makes the run red."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxtest_inv_"))
        self.list_file = self.tmp / "tests.yaml"
        self.list_file.write_text(FIXTURE, encoding="utf-8")
        self.repo = self.tmp / "repo"
        (self.repo / "unterverzeichnis").mkdir(parents=True)
        (self.repo / "nicht_hier").mkdir()

    def test_unlisted_test_file_is_reported_and_makes_the_run_red(self):
        (self.repo / "test_neu.py").write_text("x = 1\n", encoding="utf-8")
        code, out = run(["--group", "gruen"], self.list_file, self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("NICHT IN tools/test/tests.yaml", out)
        self.assertIn("test_neu.py", out)

    def test_ignored_and_covered_files_are_not_reported(self):
        (self.repo / "unterverzeichnis" / "test_bewusst_ignoriert.py").write_text("x = 1\n", encoding="utf-8")
        (self.repo / "nicht_hier" / "test_ausgeschlossen.py").write_text("x = 1\n", encoding="utf-8")
        code, out = run(["--group", "gruen"], self.list_file, self.repo)
        self.assertEqual(code, 0, out)
        self.assertNotIn("NICHT IN", out)

    def test_covered_file_is_not_reported(self):
        data = run_tests.load_list(self.list_file)
        data["tests"][0].covers = ["test_gedeckt.py"]
        (self.repo / "test_gedeckt.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(run_tests.inventory(data, self.repo), [])

    def test_no_inventory_switch(self):
        (self.repo / "test_neu.py").write_text("x = 1\n", encoding="utf-8")
        code, out = run(["--group", "gruen", "--no-inventory"], self.list_file, self.repo)
        self.assertEqual(code, 0, out)


class ZigKind(unittest.TestCase):
    """The zig kind is the one that compiles and runs; it must catch both a failure and a broken source."""

    @classmethod
    def setUpClass(cls):
        try:
            import ziglang  # noqa: F401
        except ImportError:  # pragma: no cover - ziglang is in tools/requirements.txt
            raise unittest.SkipTest("ziglang not installed")
        cls.tmp = Path(tempfile.mkdtemp(prefix="nxtest_zig_"))
        cls.repo = cls.tmp / "repo"
        cls.repo.mkdir()
        (cls.repo / "gut.c").write_text(GOOD_C, encoding="utf-8")
        (cls.repo / "schlecht.c").write_text(BAD_C, encoding="utf-8")
        (cls.repo / "kaputt.c").write_text(BROKEN_C, encoding="utf-8")
        cls.list_file = cls.tmp / "tests.yaml"
        cls.list_file.write_text(ZIG_FIXTURE, encoding="utf-8")
        cls.json = cls.tmp / "zig.json"
        cls.code, cls.out = run(["--json", str(cls.json)], cls.list_file, cls.repo)
        cls.status = {t["name"]: t for t in json.loads(cls.json.read_text(encoding="utf-8"))["tests"]}

    def test_green_host_test(self):
        self.assertEqual(self.status["zig-gruen"]["status"], "pass", self.out)
        self.assertEqual(self.status["zig-gruen"]["cases"], 5)
        self.assertIn("5 Prüfungen, 0 Fehler", self.status["zig-gruen"]["note"])

    def test_host_test_with_failures_is_red(self):
        self.assertEqual(self.status["zig-fehler"]["status"], "fail")

    def test_source_that_does_not_compile_is_red(self):
        entry = self.status["zig-uebersetzt-nicht"]
        self.assertEqual(entry["status"], "fail")
        self.assertIn("Übersetzen fehlgeschlagen", entry["reason"])

    def test_run_is_red_overall(self):
        self.assertEqual(self.code, 1)


class ListChecks(unittest.TestCase):
    """A wrong list must be refused with exit code 2 instead of running something half way."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxtest_list_"))
        self.path = self.tmp / "tests.yaml"

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def test_missing_comment(self):
        with self.assertRaises(run_tests.ListError):
            run_tests.load_list(self.write(
                "version: 1\ngroups: [a]\ntests:\n  - {name: x, group: a, kind: command, argv: [x]}\n"))

    def test_unknown_kind(self):
        with self.assertRaises(run_tests.ListError):
            run_tests.load_list(self.write(
                "version: 1\ngroups: [a]\ntests:\n  - {name: x, group: a, kind: zauber, comment: c}\n"))

    def test_unknown_group(self):
        with self.assertRaises(run_tests.ListError):
            run_tests.load_list(self.write(
                "version: 1\ngroups: [a]\ntests:\n  - {name: x, group: b, kind: command, comment: c}\n"))

    def test_duplicate_name(self):
        with self.assertRaises(run_tests.ListError):
            run_tests.load_list(self.write(
                "version: 1\ngroups: [a]\ntests:\n"
                "  - {name: x, group: a, kind: command, comment: c}\n"
                "  - {name: x, group: a, kind: command, comment: c}\n"))

    def test_unknown_dependency(self):
        with self.assertRaises(run_tests.ListError):
            run_tests.load_list(self.write(
                "version: 1\ngroups: [a]\ntests:\n"
                "  - {name: x, group: a, kind: command, comment: c, after: [y]}\n"))

    def test_unsupported_version(self):
        with self.assertRaises(run_tests.ListError):
            run_tests.load_list(self.write("version: 2\ntests: []\n"))

    def test_broken_list_gives_exit_code_2(self):
        code, _ = run([], self.write("version: 1\ngroups: [a]\ntests:\n  - {name: x}\n"), self.tmp)
        self.assertEqual(code, 2)


class ModulesField(unittest.TestCase):
    """DOC-18: `modules` names modules with a manifest; anything else is an error of the list (exit code 2)."""

    MANIFESTS = {
        "libs/bsw/drvA/module.yaml": "module: drvA\n",
        "libs/bsw/noManifest/CMakeLists.txt": "",
        "libs/bsw/drvA/sub/deep/module.yaml": "module: deep\n",          # below a libs module: not a module
        "libs/bsp/parked/module.yaml.pending": "module: parked\n",
        "platforms/fam/port/src/bsp/cap/module.yaml": "module: cap\n",
        "platforms/fam/port/a/twin/module.yaml": "module: twin\n",
        "ecu/platforms/boardB/x/twin/module.yaml": "module: twin\n",
        "platforms/fam/bsp/outside/module.yaml": "module: outside\n",   # not a port directory
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxtest_modules_"))
        self.repo = self.tmp / "repo"
        for name, text in self.MANIFESTS.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.list_file = self.tmp / "tests.yaml"

    def listing(self, modules):
        self.list_file.write_text(
            "version: 1\ngroups: [a]\ntests:\n"
            f"  - {{name: x, group: a, kind: command, comment: c, argv: [x], modules: {modules}}}\n"
            "  - {name: y, group: a, kind: command, comment: c, argv: [x]}\n", encoding="utf-8")
        return run_tests.load_list(self.list_file)

    def test_manifests_found_like_the_catalogue(self):
        self.assertEqual(run_tests.manifest_modules(self.repo), {
            "cap": ["platforms/fam/port/src/bsp/cap"],
            "drvA": ["libs/bsw/drvA"],
            "parked": ["libs/bsp/parked"],
            "twin": ["ecu/platforms/boardB/x/twin", "platforms/fam/port/a/twin"],
        })

    def test_field_is_optional_and_read(self):
        data = self.listing("[drvA, cap]")
        self.assertEqual([t.modules for t in data["tests"]], [["drvA", "cap"], []])
        self.assertNotIn("modules", data["tests"][0].spec)

    def test_known_names_and_directories_pass(self):
        for modules in ("[drvA, parked, cap]", "[platforms/fam/port/a/twin, ecu/platforms/boardB/x/twin/]", "[]"):
            self.assertEqual(run_tests.module_problems(self.listing(modules), self.repo), [], modules)

    def test_unknown_module_is_an_error(self):
        for modules, word in (("[nixda]", "'nixda'"), ("[noManifest]", "'noManifest'"), ("[deep]", "'deep'"),
                              ("[outside]", "'outside'"), ("[libs/bsw/noManifest]", "libs/bsw/noManifest")):
            problems = run_tests.module_problems(self.listing(modules), self.repo)
            self.assertEqual(len(problems), 1, modules)
            self.assertTrue(problems[0].startswith("x: "), problems)
            self.assertIn(word, problems[0])

    def test_ambiguous_name_is_an_error(self):
        problems = run_tests.module_problems(self.listing("[twin]"), self.repo)
        self.assertEqual(len(problems), 1)
        self.assertIn("not unique", problems[0])
        self.assertIn("platforms/fam/port/a/twin", problems[0])

    def test_wrong_type_is_refused(self):
        for modules in ("drvA", "[1]", "['']", "null"):
            with self.assertRaises(run_tests.ListError, msg=modules):
                self.listing(modules)

    def test_run_with_unknown_module_gives_exit_code_2(self):
        self.listing("[nixda]")
        errors = io.StringIO()
        with redirect_stderr(errors):
            code, out = run(["--list"], self.list_file, self.repo)
        self.assertEqual(code, 2)
        self.assertIn("Feld modules", errors.getvalue())
        self.assertIn("nixda", errors.getvalue())
        self.assertNotIn("Einträge", out)                                  # nothing listed, nothing run
        self.listing("[drvA]")
        code, out = run(["--list"], self.list_file, self.repo)
        self.assertEqual(code, 0, out)


class CoversAgainstArgs(unittest.TestCase):
    """TOOL-100: a test file in `covers` that the entry never hands over must make the list red (exit code 2).

    Otherwise the inventory counts the file as listed while pytest never runs it - a check confirming itself."""

    HOST_PY = "import subprocess\nSOURCES = ['host/AlphaHostTest.cpp']\n"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxtest_covers_"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        for name, text in {"t/test_a.py": "", "t/test_b.py": "", "t/helper.py": "", "t/test_host.py": self.HOST_PY,
                           "host/AlphaHostTest.cpp": "", "host/BetaHostTest.cpp": ""}.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(text, encoding="utf-8")
        self.list_file = self.tmp / "tests.yaml"

    def listing(self, *entries: str):
        self.list_file.write_text(
            "version: 1\ngroups: [a]\ninventory:\n  patterns: ['test_*.py', '*HostTest.cpp']\ntests:\n"
            + "".join(f"  - {{name: e{i}, group: a, comment: c, {e}}}\n" for i, e in enumerate(entries)),
            encoding="utf-8")
        return run_tests.load_list(self.list_file)

    def problems(self, *entries: str) -> list[str]:
        return run_tests.covers_problems(self.listing(*entries), self.repo)

    def test_covered_file_missing_from_args_is_red(self):
        for kind in ("pytest", "unittest"):
            problems = self.problems(f"kind: {kind}, args: [t/test_a.py, -q], covers: [t/test_a.py, t/test_b.py]")
            self.assertEqual(len(problems), 1, (kind, problems))
            self.assertTrue(problems[0].startswith("e0: "), problems)
            self.assertIn("t/test_b.py", problems[0])
            self.assertIn("nie ausgefuehrt", problems[0])

    def test_run_is_refused_before_anything_runs(self):
        self.listing("kind: pytest, args: [t/test_a.py], covers: [t/test_a.py, t/test_b.py]")
        errors = io.StringIO()
        with redirect_stderr(errors):
            code, out = run(["--no-inventory"], self.list_file, self.repo)
        self.assertEqual(code, 2)
        self.assertIn("covers gegen args", errors.getvalue())
        self.assertIn("t/test_b.py", errors.getvalue())
        self.assertNotIn("Ergebnis", out)                                  # nothing ran
        with redirect_stderr(io.StringIO()):
            code, _ = run(["--list"], self.list_file, self.repo)           # also --list says the list is wrong
        self.assertEqual(code, 2)

    def test_matching_lists_and_partial_runs_pass(self):
        # -k keeps the whole file in args: several entries may split one file, each names it (elfcheck-*)
        self.assertEqual(self.problems(
            "kind: unittest, args: [t/test_a.py, -k, One], covers: [t/test_a.py]",
            "kind: unittest, args: [t/test_a.py, -k, Two, -k, Three], covers: []",
            "kind: pytest, args: ['{repo}/t/test_b.py::Case', -q], covers: [t/test_b.py]",
            "kind: script, args: [t/test_a.py, '{tmp}'], covers: [t/test_a.py]",
            "kind: zig, sources: [host/BetaHostTest.cpp, x.cpp], covers: [host/BetaHostTest.cpp]"), [])

    def test_other_kinds_are_checked_against_what_they_hand_over(self):
        problems = self.problems("kind: script, args: [t/test_a.py], covers: [t/test_b.py]",
                                 "kind: zig, sources: [x.cpp], covers: [host/BetaHostTest.cpp]")
        self.assertEqual([p.split(":")[0] for p in problems], ["e0", "e1"])
        self.assertIn("aber sources nicht", problems[1])

    def test_named_exception_host_test_driven_by_the_python_test(self):
        # bswgen, rtegen, ecustate ...: the Python test compiles the host test itself - only if it names it
        self.assertEqual(self.problems(
            "kind: unittest, args: [t/test_host.py], covers: [t/test_host.py, host/AlphaHostTest.cpp]"), [])
        problems = self.problems(
            "kind: unittest, args: [t/test_host.py], covers: [t/test_host.py, host/BetaHostTest.cpp]")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("host/BetaHostTest.cpp", problems[0])
        self.assertIn("keine Python-Datei aus args", problems[0])

    def test_files_that_are_no_test_files_are_not_checked(self):
        # the module under test next to its test (nxconfig-pin: pincheck.py) is not inventoried - nothing to hide
        self.assertEqual(self.problems("kind: unittest, args: [t/test_a.py], covers: [t/test_a.py, t/helper.py]"),
                         [])

    def test_command_is_not_checked(self):
        # an arbitrary command line: the runner cannot know what it runs (swc-matlab-*, fixture of test_workspace)
        self.assertEqual(self.problems("kind: command, argv: [x], covers: [t/test_a.py]"), [])

    def test_real_list_has_no_gap(self):
        """Counter-check: every entry of the repository's list passes the rule."""
        self.assertEqual(run_tests.covers_problems(run_tests.load_list(run_tests.LIST_FILE)), [])

    def test_real_list_turns_red_when_a_studio_file_is_dropped_from_args(self):
        """The case TOOL-100 was opened for: a file moved between the Studio parts, but only in one field."""
        data = run_tests.load_list(run_tests.LIST_FILE)
        # the Studio parts where they exist (not in the open export), else any Python entry that runs its covers
        candidates = sorted((t for t in data["tests"] if t.kind in ("pytest", "unittest")
                             and any(c.endswith(".py") and c in t.spec.get("args", []) for c in t.covers)),
                            key=lambda t: t.kind != "pytest")
        self.assertTrue(candidates, "no Python entry with covers in the list")
        victim = candidates[0]
        dropped = [c for c in victim.covers if c.endswith(".py") and c in victim.spec["args"]][-1]
        victim.spec["args"] = [a for a in victim.spec["args"] if a != dropped]
        problems = run_tests.covers_problems(data)
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith(f"{victim.name}: covers nennt {dropped}"), problems)


def declared_groups(list_file):
    """The `groups:` line of a test list as written, before load_list fills in a missing one."""
    return list(yaml.safe_load(Path(list_file).read_text(encoding="utf-8")).get("groups") or [])


def group_problems(list_file):
    """Everything wrong with the groups of a test list, as readable lines; empty means consistent.

    The expected groups come from the list itself, never from a fixed set: the full repository has five groups,
    the open export of tools/distcheck (PLAT-50) has no Studio and therefore no `pytest` group. What must hold in
    both: the list declares its groups, none twice, the runner knows exactly these, and every group has at least
    one test (a group without a test would make `--group <name>` run nothing)."""
    declared = declared_groups(list_file)
    data = run_tests.load_list(Path(list_file))
    used = {test.group for test in data["tests"]}
    problems = []
    if not declared:
        problems.append("keine Zeile groups: in der Liste")
    doubles = sorted({g for g in declared if declared.count(g) > 1})
    if doubles:
        problems.append(f"Gruppe(n) doppelt: {', '.join(doubles)}")
    if declared and data["groups"] != declared:
        problems.append(f"Laeufer kennt {data['groups']}, die Liste nennt {declared}")
    unused = [g for g in dict.fromkeys(data["groups"]) if g not in used]
    if unused:
        problems.append(f"Gruppe(n) ohne Test: {', '.join(unused)}")
    undeclared = sorted(used - set(data["groups"]))
    if undeclared:
        problems.append(f"Test(s) in nicht genannter Gruppe: {', '.join(undeclared)}")
    return problems


# Two synthetic lists shaped like the repository: the full tree with the Studio (group pytest) and the tree as the
# open export of tools/distcheck writes it - Studio entry dropped and pytest gone from `groups:`.
GROUPS_FULL = """
version: 1
groups: [python, pytest, host]
tests:
  - {name: gen, group: python, kind: unittest, comment: Generator, args: [tools/gen/test_gen.py]}
  - {name: studio, group: pytest, kind: pytest, comment: Studio, args: [tools/studio, -q]}
  - {name: sbc, group: host, kind: zig, comment: SBC, sources: [sbc.c]}
"""

GROUPS_EXPORT = """
version: 1
groups: [python, host]
tests:
  - {name: gen, group: python, kind: unittest, comment: Generator, args: [tools/gen/test_gen.py]}
  # (Eintrag studio beim Export entfernt: Inhalt nicht im Profil)
  - {name: sbc, group: host, kind: zig, comment: SBC, sources: [sbc.c]}
"""


class GroupsFromList(unittest.TestCase):
    """The group check reads the groups from the list: green for the full tree and for the export, red for a
    group without a test (PLAT-63)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxtest_groups_"))

    def write(self, name, text):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_full_tree_with_studio_is_consistent(self):
        path = self.write("voll.yaml", GROUPS_FULL)
        self.assertEqual(group_problems(path), [])
        self.assertEqual(run_tests.load_list(path)["groups"], ["python", "pytest", "host"])

    def test_export_without_studio_is_consistent(self):
        path = self.write("export.yaml", GROUPS_EXPORT)
        self.assertEqual(group_problems(path), [])
        self.assertEqual(run_tests.load_list(path)["groups"], ["python", "host"])

    def test_group_without_test_is_found(self):
        # counter-check: the export dropped the Studio entry but forgot to drop its group
        path = self.write("kaputt.yaml",
                          GROUPS_EXPORT.replace("groups: [python, host]", "groups: [python, pytest, host]"))
        self.assertEqual(group_problems(path), ["Gruppe(n) ohne Test: pytest"])

    def test_duplicate_group_is_found(self):
        path = self.write("doppelt.yaml",
                          GROUPS_EXPORT.replace("groups: [python, host]", "groups: [python, host, host]"))
        self.assertIn("Gruppe(n) doppelt: host", group_problems(path))

    def test_missing_groups_line_is_found(self):
        path = self.write("ohne.yaml", GROUPS_EXPORT.replace("groups: [python, host]\n", ""))
        self.assertIn("keine Zeile groups: in der Liste", group_problems(path))

    def test_repository_list_without_its_studio_entries_is_consistent(self):
        # the real list, reduced the way the export reduces it: every pytest entry and the group itself removed
        raw = yaml.safe_load(run_tests.LIST_FILE.read_text(encoding="utf-8"))
        if "pytest" not in raw["groups"]:
            self.skipTest("Liste ohne Gruppe pytest (bereits exportierter Baum)")
        raw["tests"] = [t for t in raw["tests"] if t["group"] != "pytest"]
        raw["groups"] = [g for g in raw["groups"] if g != "pytest"]
        path = self.write("repo_export.yaml", yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
        self.assertEqual(group_problems(path), [])
        self.assertNotIn("pytest", run_tests.load_list(path)["groups"])


NEEDS_FIXTURE = """
version: 1
groups: [bau, pruef]
inventory:
  patterns: []
tests:
  - name: erzeuger
    group: bau
    kind: command
    comment: baut die Bibliothek frisch (wie bl-host-dll)
    argv: ["{python}", -c, "import pathlib; pathlib.Path(r'{build}/lib.txt').write_text('frisch')"]
    covers: []
  - name: kaputter-erzeuger
    group: bau
    kind: command
    comment: Bau schlaegt fehl
    argv: ["{python}", -c, "import sys; sys.exit(1)"]
    covers: []
  - name: nutzer-neu
    group: pruef
    kind: command
    comment: braucht die frische Bibliothek und sagt es mit needs
    argv: ["{python}", -c, "import pathlib, sys; sys.exit(pathlib.Path(r'{build}/lib.txt').read_text() != 'frisch')"]
    needs: [erzeuger]
    needs_files: ["{build}/lib.txt"]
    covers: []
  - name: nutzer-alt
    group: pruef
    kind: command
    comment: dieselbe Pruefung nur mit after - so war es vor TOOL-52
    argv: ["{python}", -c, "import pathlib, sys; sys.exit(pathlib.Path(r'{build}/lib.txt').read_text() != 'frisch')"]
    after: [erzeuger]
    needs_files: ["{build}/lib.txt"]
    covers: []
  - name: nutzer-kaputt
    group: pruef
    kind: command
    comment: braucht einen Bau, der rot ist
    argv: ["{python}", -c, "print('darf nicht laufen')"]
    needs: [kaputter-erzeuger]
    covers: []
"""


class DeclaredNeeds(unittest.TestCase):
    """TOOL-52: `-k canflash-info-host` alone ran against a stale bl_host.dll in the build folder (BL 0.3.2 instead
    of 0.3.4) and was red. An entry names the entries that build what it uses under `needs`; a single -k run then
    builds it first, on a fresh state and on a stale one."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxtest_needs_"))
        self.list_file = self.tmp / "tests.yaml"
        self.list_file.write_text(NEEDS_FIXTURE, encoding="utf-8")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.build = self.tmp / "build"
        self.build.mkdir()

    def run_k(self, pattern, *extra):
        json_file = self.tmp / "ergebnis.json"
        code, out = run(["-k", pattern, "--build-root", str(self.build), "--json", str(json_file), *extra],
                        self.list_file, self.repo)
        data = json.loads(json_file.read_text(encoding="utf-8"))
        return code, out, [(t["name"], t["status"]) for t in data["tests"]], {t["name"]: t for t in data["tests"]}

    def test_single_run_on_a_fresh_state_builds_first(self):
        code, out, order, _ = self.run_k("nutzer-neu")
        self.assertEqual(code, 0, out)
        self.assertEqual(order, [("erzeuger", "pass"), ("nutzer-neu", "pass")])
        self.assertIn("Vorbedingungen (needs), laufen zuerst mit: erzeuger", out)

    def test_single_run_on_a_stale_state_rebuilds(self):
        (self.build / "lib.txt").write_text("veraltet", encoding="utf-8")
        code, out, order, _ = self.run_k("nutzer-neu", "--jobs", "4")
        self.assertEqual(code, 0, out)
        self.assertEqual(order, [("erzeuger", "pass"), ("nutzer-neu", "pass")])
        self.assertEqual((self.build / "lib.txt").read_text(encoding="utf-8"), "frisch")

    def test_after_alone_runs_against_the_stale_state(self):
        """The finding itself: `after` orders only what is selected, so the stale library is used."""
        (self.build / "lib.txt").write_text("veraltet", encoding="utf-8")
        code, out, order, _ = self.run_k("nutzer-alt")
        self.assertEqual(code, 1, out)
        self.assertEqual(order, [("nutzer-alt", "fail")])

    def test_red_build_skips_the_user(self):
        code, _, order, entries = self.run_k("nutzer-kaputt")
        self.assertEqual(code, 1)
        self.assertEqual(order, [("kaputter-erzeuger", "fail"), ("nutzer-kaputt", "skip")])
        self.assertIn("Vorbedingung nicht gruen: kaputter-erzeuger rot", entries["nutzer-kaputt"]["reason"])

    def test_group_selection_pulls_the_build_of_another_group(self):
        code, out = run(["--group", "pruef", "--list"], self.list_file, self.repo)
        self.assertEqual(code, 0)
        self.assertIn("5 Einträge", out)             # three users plus both builds
        self.assertIn("[nach Eintrag: erzeuger]", out)

    def test_unknown_need_is_an_error(self):
        path = self.tmp / "falsch.yaml"
        path.write_text("version: 1\ngroups: [a]\ntests:\n"
                        "  - {name: x, group: a, kind: command, comment: c, needs: [y]}\n", encoding="utf-8")
        with self.assertRaises(run_tests.ListError):
            run_tests.load_list(path)

    def test_cycle_in_needs_does_not_hang(self):
        path = self.tmp / "kreis.yaml"
        path.write_text("version: 1\ngroups: [a]\ninventory: {patterns: []}\ntests:\n"
                        "  - {name: x, group: a, kind: command, comment: c, argv: ['{python}', -c, '1'], needs: [y]}\n"
                        "  - {name: y, group: a, kind: command, comment: c, argv: ['{python}', -c, '1'], needs: [x]}\n",
                        encoding="utf-8")
        code, out = run(["-k", "x"], path, self.repo)
        self.assertEqual(code, 1)
        self.assertIn("nicht auflösbar", out)


def produced_files(test) -> set[str]:
    """Placeholder paths under {build} an entry of the build group writes (its arguments)."""
    return {a for a in test.spec.get("args", []) if isinstance(a, str) and a.startswith("{build}/")}


class RealList(unittest.TestCase):
    """The list of the repository itself must be loadable and consistent."""

    def test_users_of_a_built_file_name_its_build(self):
        """TOOL-52: whoever reads a file another entry builds (needs_files, env) has that entry under `needs`."""
        data = run_tests.load_list(run_tests.LIST_FILE)
        producers = {path: test.name for test in data["tests"] if test.group == "build"
                     for path in produced_files(test)}
        # PLAT-62: the exported open tree drops bl-host-dll (built from the private eval board project) and with it
        # its users; the rule below still holds for every other producer there
        exported = not any(t.name == "bl-host-dll" for t in data["tests"])
        if not exported:
            self.assertIn("{build}/host/bl_host.dll", producers)
        missing = []
        for test in data["tests"]:
            used = set(test.needs_files) | {v for v in test.env.values() if isinstance(v, str)}
            for path in sorted(used & producers.keys()):
                if producers[path] != test.name and producers[path] not in test.needs:
                    missing.append(f"{test.name}: {path} -> needs: [{producers[path]}]")
        self.assertEqual(missing, [])
        users = {t.name for t in data["tests"] if "bl-host-dll" in t.needs}
        if not exported:
            self.assertLessEqual({"app-uds-host", "canflash-info-host"}, users)

    def test_repository_list_loads(self):
        data = run_tests.load_list(run_tests.LIST_FILE)
        self.assertGreaterEqual(len(data["tests"]), 40)
        # expected groups from the list itself: five in the full tree, four in the open export (no Studio)
        self.assertEqual(data["groups"], declared_groups(run_tests.LIST_FILE))

    def test_every_covered_file_exists(self):
        data = run_tests.load_list(run_tests.LIST_FILE)
        for test in data["tests"]:
            for covered in test.covers:
                self.assertTrue((run_tests.REPO / covered).is_file(), f"{test.name}: {covered} fehlt")

    def test_every_group_is_used(self):
        # no group without a test and no test outside the named groups - whatever groups the tree has
        self.assertEqual(group_problems(run_tests.LIST_FILE), [])

    def test_repository_list_is_complete(self):
        data = run_tests.load_list(run_tests.LIST_FILE)
        self.assertEqual(run_tests.inventory(data), [],
                         "Testdateien ohne Eintrag in tools/test/tests.yaml")

    def test_every_named_module_has_a_manifest(self):
        data = run_tests.load_list(run_tests.LIST_FILE)
        self.assertEqual(run_tests.module_problems(data), [])
        # DOC-18: the port modules whose host tests lie outside their directory are named
        named = {m for test in data["tests"] for m in test.modules}
        self.assertLessEqual({"capture", "hall"}, named)

    def test_build_root_is_short_in_a_worktree(self):
        worktree = Path("C:/repo/.claude/worktrees/agent-XY") if sys.platform == "win32" \
            else Path("/repo/.claude/worktrees/agent-XY")
        self.assertNotIn(".claude", run_tests.default_build_root(worktree).as_posix())
        self.assertEqual(run_tests.default_build_root(Path("/repo")).name, "build")


class DemoBuildDir(unittest.TestCase):
    """PLAT-07: host tests find the demo target build the way tools/test builds it, no fixed build folder."""

    def setUp(self):
        import os
        from unittest import mock

        import build_dirs

        self.build_dirs = build_dirs
        self.tmp = Path(tempfile.mkdtemp(prefix="nxtest_builddir_"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        env = {k: v for k, v in os.environ.items() if k != build_dirs.ENV_DEMO_BUILD}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()

    def configured(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        (path / "CMakeCache.txt").write_text("", encoding="utf-8")
        return path

    def test_nothing_built_names_the_tools_test_place(self):
        self.assertEqual(self.build_dirs.demo_build_dir(self.repo), self.repo / "build" / "demo")

    def test_preset_build_is_found(self):
        preset = self.configured(self.repo / "build" / "evalboard-s32k144-demo")
        self.assertEqual(self.build_dirs.demo_build_dir(self.repo), preset)
        self.assertEqual(self.build_dirs.bootloader_hex(self.repo),
                         preset / "bootloader" / "RelWithDebInfo" / "bootloader.hex")

    def test_tools_test_build_wins_over_preset(self):
        self.configured(self.repo / "build" / "evalboard-s32k144-demo")
        demo = self.configured(self.repo / "build" / "demo")
        self.assertEqual(self.build_dirs.demo_build_dir(self.repo), demo)

    def test_environment_wins(self):
        import os

        self.configured(self.repo / "build" / "demo")
        os.environ[self.build_dirs.ENV_DEMO_BUILD] = str(self.tmp / "elsewhere")
        self.assertEqual(self.build_dirs.demo_build_dir(self.repo), self.tmp / "elsewhere")

    def test_worktree_uses_the_short_directory_of_the_preset(self):
        worktree = self.tmp / ".claude" / "worktrees" / "agent-XY"
        self.assertNotIn(".claude", self.build_dirs.preset_build_dir(worktree).as_posix())
        self.assertEqual(self.build_dirs.preset_build_dir(worktree).name, "agent-XY")
        self.assertEqual(self.build_dirs.demo_build_candidates(worktree)[0],
                         self.build_dirs.default_build_root(worktree) / "demo")

    def test_runner_exports_its_demo_build(self):
        import os

        root = self.tmp / "root"
        runner = run_tests.Runner(self.repo, root, sys.executable)
        test = SimpleNamespace(env={})                                   # environment() only reads the entry's env
        key = self.build_dirs.ENV_DEMO_BUILD
        self.assertNotIn(key, runner.environment(test, self.tmp))       # nothing built: nothing exported
        demo = self.configured(root / "demo")
        self.assertEqual(runner.environment(test, self.tmp)[key], str(demo))
        os.environ[key] = str(self.tmp / "explicit")                     # explicit value wins
        self.assertEqual(runner.environment(test, self.tmp)[key], str(self.tmp / "explicit"))


class StaleCompiler(unittest.TestCase):
    """VERIFY-0928: a CMake cache whose compiler moved or vanished is configured fresh, never red on the old path."""

    def setUp(self):
        import build_dirs

        self.build_dirs = build_dirs
        self.tmp = Path(tempfile.mkdtemp(prefix="nxtest_stalecc_"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.exe = ".exe" if sys.platform == "win32" else ""

    def toolchain(self, name: str) -> Path:
        """Directory with an (empty, executable) arm-none-eabi-gcc."""
        bin_dir = self.tmp / name / "bin"
        bin_dir.mkdir(parents=True)
        gcc = bin_dir / f"arm-none-eabi-gcc{self.exe}"
        gcc.write_bytes(b"")
        gcc.chmod(0o755)
        return bin_dir

    def cache(self, compiler: Path | str) -> Path:
        build = self.tmp / "build"
        build.mkdir(exist_ok=True)
        compiler = Path(compiler).as_posix()
        (build / "CMakeCache.txt").write_text(
            f"CMAKE_ASM_COMPILER:FILEPATH={compiler}\nCMAKE_C_COMPILER:FILEPATH={compiler}\n"
            "CMAKE_BUILD_TYPE:STRING=\n", encoding="utf-8")
        return build

    def test_no_cache_is_not_stale(self):
        self.assertEqual(self.build_dirs.fresh_args(self.tmp / "nothing", ""), ([], ""))

    def test_current_compiler_is_kept(self):
        bin_dir = self.toolchain("xpack")
        build = self.cache(bin_dir / f"arm-none-eabi-gcc{self.exe}")
        self.assertEqual(self.build_dirs.fresh_args(build, str(bin_dir)), ([], ""))

    def test_vanished_compiler_is_configured_fresh(self):
        bin_dir = self.toolchain("xpack")
        build = self.cache("C:/ide/S32DS.3.6.9/S32DS/build_tools/gcc_v11.4/gcc-11.4-arm32-eabi/bin/"
                           "arm-none-eabi-gcc.exe")
        fresh, reason = self.build_dirs.fresh_args(build, str(bin_dir))
        self.assertEqual(fresh, ["--fresh"])
        self.assertIn("existiert nicht mehr", reason)

    def test_other_compiler_on_the_search_path_is_configured_fresh(self):
        old = self.toolchain("old")
        new = self.toolchain("new")
        build = self.cache(old / f"arm-none-eabi-gcc{self.exe}")
        fresh, reason = self.build_dirs.fresh_args(build, str(new))
        self.assertEqual(fresh, ["--fresh"])
        self.assertIn("nicht der aktuelle Compiler", reason)

    def test_host_compiler_is_only_checked_for_existence(self):
        host = self.tmp / "host" / f"cc{self.exe}"
        host.parent.mkdir()
        host.write_bytes(b"")
        build = self.cache(host)
        self.assertEqual(self.build_dirs.fresh_args(build, str(self.toolchain("xpack"))), ([], ""))

    def test_cmake_preset_kind_passes_fresh(self):
        from unittest import mock

        build = self.cache("C:/gone/arm-none-eabi-gcc.exe")
        runner = run_tests.Runner(self.tmp, self.tmp / "root", sys.executable)
        test = SimpleNamespace(name="target-x", group="target", kind="cmake_preset", env={}, timeout=5,
                               spec={"preset": "p", "binary_dir": str(build)}, origin=None)
        calls = []

        def spawn(argv, *_):
            calls.append(argv)
            return 1, "", False                                         # stop after configure

        with mock.patch.object(runner, "spawn", side_effect=spawn):
            result = run_tests.kind_cmake_preset(runner, test, self.tmp)
        self.assertIn("--fresh", calls[0])
        self.assertIn("neu konfiguriert", result.output)



# ---------------------------------------------------------------------------- TOOL-79: --since

import shutil  # noqa: E402
import subprocess  # noqa: E402

import since  # noqa: E402

# Invented tree for the mapping file -> entry. Every name is made up (zeta, zgen, zstudio, zdrv); the two entries
# zeta-safety-tools and zeta-studio rebuild the two cases of the coordinator's finding 2026-09-23 on a small scale.
SINCE_TREE = {
    "CMakeLists.txt": "include(cmake/NxZeta.cmake)\n",
    "cmake/NxZeta.cmake": "# build helper\n",
    "README.md": "# zeta\n",
    "doc/safety/test/test_zeta_tools.py": "import unittest\n",
    "doc/safety/zeta-tools.md": "| Tool | TCL |\n",
    # a generator and the Studio core that uses it - like rtegen and tools/studio/core/components.py
    "tools/zgen/__init__.py": "",
    "tools/zgen/zgen.py": "def paths_base():\n    return '.'\n",
    "tools/zconf/__init__.py": "",
    "tools/zconf/model.py": "SCHEMA = 2\n",
    "tools/zstudio/__init__.py": "",
    "tools/zstudio/core/__init__.py": "",
    "tools/zstudio/core/helper.py": "X = 1\n",
    "tools/zstudio/core/components.py": "import os\nfrom zgen import zgen\nfrom . import helper\n",
    "tools/zstudio/tests/conftest.py": "import zconf.model\n",
    "tools/zstudio/tests/test_zcore.py": "from zstudio.core import components\n",
    # a C++ module with a host test
    "libs/zdrv/include/zdrv/Zdrv.h": "#pragma once\n",
    "libs/zdrv/src/Zdrv.cpp": "#include \"zdrv/Zdrv.h\"\n",
    "libs/zdrv/test/ZdrvHostTest.cpp": "int main() { return 0; }\n",
    # a tool started as a module
    "tools/zlint/__init__.py": "",
    "tools/zlint/__main__.py": "from . import rules\n",
    "tools/zlint/rules.py": "",
    "tools/zorphan.py": "print('no test anywhere')\n",
}

SINCE_LIST = """
version: 1
groups: [python, pytest, host, target]
tests:
  - name: zeta-safety-tools
    group: python
    kind: unittest
    comment: reads every CMakeLists.txt/*.cmake of the tree (like safety-tools) - only watch can say so
    args: [doc/safety/test/test_zeta_tools.py]
    covers: [doc/safety/test/test_zeta_tools.py]
    watch: ["*CMakeLists.txt", "*.cmake", doc/safety/]
  - name: zeta-studio
    group: pytest
    kind: pytest
    comment: Studio core test - depends on zgen through an import of the core (like studio-core and rtegen)
    args: [tools/zstudio/tests/test_zcore.py, -q]
    covers: [tools/zstudio/tests/test_zcore.py]
  - name: zeta-drv-host
    group: host
    kind: zig
    comment: host test of zdrv
    sources: [libs/zdrv/test/ZdrvHostTest.cpp, libs/zdrv/src/Zdrv.cpp]
    include: [libs/zdrv/include]
    covers: [libs/zdrv/test/ZdrvHostTest.cpp]
  - name: zeta-lint
    group: python
    kind: command
    comment: a tool started with -m, the sub-command 'check' is no path
    argv: ["{python}", -m, tools.zlint, check, "{tmp}/out.txt"]
    covers: []
  - name: zeta-target
    group: target
    kind: cmake_preset
    comment: target build - the whole tree is its source, no mapping
    preset: zeta
    covers: []
"""


def since_tree(root: Path, files: dict = SINCE_TREE) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / "tests.yaml").write_text(SINCE_LIST, encoding="utf-8")
    return root


def git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-c", "user.name=nxtest", "-c", "user.email=nxtest@example.invalid",
                           "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *argv],
                          cwd=repo, check=True, capture_output=True, text=True).stdout


class SinceMapping(unittest.TestCase):
    """TOOL-79: which entry a changed file triggers, with an invented file set (no git needed)."""

    def setUp(self):
        self.repo = since_tree(Path(tempfile.mkdtemp(prefix="nxtest_since_")))
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.tests = run_tests.load_list(self.repo / "tests.yaml")["tests"]

    def mapped(self, changed, tests=None):
        return since.map_changes(tests or self.tests, changed, self.repo, "main", "abc")

    def test_claims_of_each_field(self):
        by_name = {t.name: t for t in self.tests}
        drv = {(c.field, c.pattern) for c in since.direct_claims(by_name["zeta-drv-host"], self.repo)}
        self.assertIn(("include", "libs/zdrv/include"), drv)
        self.assertIn(("sources", "libs/zdrv/src/Zdrv.cpp"), drv)
        lint = [(c.field, c.pattern) for c in since.direct_claims(by_name["zeta-lint"], self.repo)]
        # -m tools.zlint becomes the folder; {python}, {tmp}/... and the sub-command 'check' are no claims
        self.assertEqual(lint, [("argv", "tools/zlint")])
        self.assertEqual(since.direct_claims(by_name["zeta-target"], self.repo), [])

    def test_a_file_below_a_named_directory_triggers(self):
        sel = self.mapped(["libs/zdrv/include/zdrv/Zdrv.h", "tools/zlint/rules.py"])
        self.assertEqual(sel.triggers["zeta-drv-host"][0][1].field, "include")
        self.assertEqual(sel.triggers["zeta-lint"][0][0], "tools/zlint/rules.py")
        self.assertEqual(sel.orphans, [])

    def test_case_rte22_generator_change_reaches_the_studio_through_imports(self):
        """Gegenprobe RTE-22: the generator changed, the Studio test names only its own file - the import of the
        core pulls the generator in, so the Studio entry is chosen and the file is named."""
        sel = self.mapped(["tools/zgen/zgen.py"])
        self.assertIn("zeta-studio", sel.names)
        path, claim = sel.triggers["zeta-studio"][0]
        self.assertEqual((path, claim.field), ("tools/zgen/zgen.py", "import"))
        self.assertEqual(claim.via, "tools/zstudio/tests/test_zcore.py")
        # conftest.py counts for pytest, and a relative import inside the core too
        self.assertIn("zeta-studio", self.mapped(["tools/zconf/model.py"]).triggers)
        self.assertIn("zeta-studio", self.mapped(["tools/zstudio/core/helper.py"]).triggers)

    def test_case_arch57_root_cmake_change_reaches_the_tool_classification(self):
        """Gegenprobe ARCH-57: the root CMakeLists.txt changed - chosen through watch; without watch the entry
        stays out and the file is reported as code without a test (the finding that would have pointed there)."""
        changed = ["CMakeLists.txt", "cmake/NxZeta.cmake"]
        sel = self.mapped(changed)
        self.assertIn("zeta-safety-tools", sel.triggers)
        self.assertEqual(sel.triggers["zeta-safety-tools"][0][1].pattern, "*CMakeLists.txt")
        self.assertEqual(sel.orphans, [])
        for test in self.tests:
            test.watch = []
        sel = self.mapped(changed)
        self.assertNotIn("zeta-safety-tools", sel.triggers)
        self.assertEqual(sel.orphans_code(), changed)

    def test_entries_without_mapping_run_whenever_something_changed(self):
        sel = self.mapped(["tools/zorphan.py"])
        self.assertEqual(sel.unmapped_entries, ["zeta-target"])
        self.assertEqual(sel.names, {"zeta-target"})
        self.assertEqual(self.mapped([]).names, set())

    def test_files_without_entry_code_apart_from_documentation(self):
        sel = self.mapped(["README.md", "tools/zorphan.py", "doc/safety/zeta-tools.md"])
        self.assertEqual(sel.orphans_code(), ["tools/zorphan.py"])
        self.assertEqual(sel.orphans_doc(), ["README.md"])          # the safety document is watched
        text = "\n".join(since.report_lines(sel, ["zeta-safety-tools", "zeta-target"], []))
        self.assertIn("Code ohne Test (1)", text)
        self.assertIn("keine Zuordnung - sicherheitshalber", text)
        self.assertIn("doc/safety/zeta-tools.md  [watch: doc/safety]", text)

    def test_watch_must_be_a_list_of_paths(self):
        bad = SINCE_LIST.replace('watch: ["*CMakeLists.txt", "*.cmake", doc/safety/]', "watch: CMakeLists.txt")
        (self.repo / "bad.yaml").write_text(bad, encoding="utf-8")
        with self.assertRaises(run_tests.ListError):
            run_tests.load_list(self.repo / "bad.yaml")


@unittest.skipUnless(shutil.which("git"), "git fehlt")
class SinceGit(unittest.TestCase):
    """TOOL-79: the changed files come from git - committed, uncommitted, untracked, a range, an unknown ref."""

    def setUp(self):
        self.repo = since_tree(Path(tempfile.mkdtemp(prefix="nxtest_since_git_")))
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "branch", "basis")

    def test_committed_uncommitted_and_untracked_changes_count(self):
        (self.repo / "libs/zdrv/src/Zdrv.cpp").write_text("// changed\n", encoding="utf-8")
        git(self.repo, "commit", "-q", "-am", "drv")
        (self.repo / "tools/zgen/zgen.py").write_text("# uncommitted\n", encoding="utf-8")
        (self.repo / "tools/zneu.py").write_text("# untracked\n", encoding="utf-8")
        base, changed = since.changed_files(self.repo, "basis")
        self.assertEqual(changed, ["libs/zdrv/src/Zdrv.cpp", "tools/zgen/zgen.py", "tools/zneu.py"])
        self.assertEqual(base, git(self.repo, "rev-parse", "basis").strip())
        # a range: only what the commits between them changed, the working tree does not count
        _, changed = since.changed_files(self.repo, "basis..HEAD")
        self.assertEqual(changed, ["libs/zdrv/src/Zdrv.cpp"])

    def test_unknown_reference_is_an_error(self):
        with self.assertRaises(since.SinceError):
            since.changed_files(self.repo, "gibtsnicht")
        code, out = self.cli(["--since", "gibtsnicht", "--list"])
        self.assertEqual(code, 2)

    def cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = run_tests.main([*argv, "--tests", str(self.repo / "tests.yaml"), "--repo", str(self.repo)])
        return code, out.getvalue() + err.getvalue()

    def test_command_line_names_the_trigger_and_the_orphans(self):
        (self.repo / "tools/zgen/zgen.py").write_text("# changed\n", encoding="utf-8")
        (self.repo / "tools/zorphan.py").write_text("# changed\n", encoding="utf-8")
        report = self.repo / "liste.json"
        code, out = self.cli(["--since", "basis", "--list", "--json", str(report)])
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"zeta-studio\s+tools/zgen/zgen.py\s+\[import ueber tools/zstudio/tests/test_zcore.py\]")
        self.assertRegex(out, r"zeta-target\s+keine Zuordnung")
        self.assertIn("Code ohne Test (1):\n  tools/zorphan.py", out)
        self.assertIn("2 Einträge", out)
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(sorted(t["name"] for t in data["tests"]), ["zeta-studio", "zeta-target"])
        self.assertEqual(data["since"]["files_without_entry"], ["tools/zorphan.py"])
        self.assertEqual(data["since"]["triggered"]["zeta-studio"][0]["via"], "tools/zstudio/tests/test_zcore.py")
        # the filters still narrow: what they leave out is named
        code, out = self.cli(["--since", "basis", "--list", "--group", "target"])
        self.assertIn("nicht ausgewaehlt (1): zeta-studio", out)
        self.assertIn("1 Einträge", out)

    def test_nothing_changed_selects_nothing(self):
        code, out = self.cli(["--since", "basis"])
        self.assertEqual(code, 0, out)
        self.assertIn("Kein Eintrag betroffen", out)

    def test_an_edited_entry_is_triggered_by_the_list(self):
        text = (self.repo / "tests.yaml").read_text(encoding="utf-8")
        (self.repo / "tests.yaml").write_text(text.replace("host test of zdrv", "host test of zdrv, longer"),
                                              encoding="utf-8")
        code, out = self.cli(["--since", "basis", "--list"])
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"zeta-drv-host\s+tests.yaml\s+\[eigener Eintrag geaendert in tests.yaml\]")
        self.assertNotRegex(out, r"zeta-studio\s")
        self.assertIn("Code ohne Test (0)", out)


class RepeatedPattern(unittest.TestCase):
    """Finding of the coordinator 2026-09-28: with several -k only the last one counted. Now: any of them."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxtest_k_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.list_file = self.tmp / "tests.yaml"
        self.list_file.write_text(FIXTURE, encoding="utf-8")

    def test_several_patterns_are_a_union(self):
        code, out = run(["-k", "gruen-einfach", "-k", "rot-exit*", "--list"], self.list_file, self.tmp)
        self.assertEqual(code, 0)
        self.assertIn("2 Einträge", out)
        self.assertIn("gruen-einfach", out)
        self.assertIn("rot-exitcode", out)


class SinceRealList(unittest.TestCase):
    """TOOL-79 against the real list: the two cases of 2026-09-23 with the files their merges changed."""

    # a part of `git diff --name-only 2950431e^1 2950431e` (merge of RTE-22)
    RTE22 = ["tools/rtegen/rtegen.py", "tools/nxconfig/model.py", "tools/nxconfig/diag.py",
             "tools/nxconfig/newproject.py", "projects/evalboard_s32k144_demo/rte.yaml"]
    # a part of `git diff --name-only 8b0fe2fd^1 8b0fe2fd` (merge of ARCH-57)
    ARCH57 = ["CMakeLists.txt", "cmake/NxPresets.json", "bootloader/CMakeLists.txt",
              "platforms/s32k1xx/port/main/CMakeLists.txt"]
    # the watch proposed for safety-tools (the test reads every CMakeLists.txt/*.cmake known to git)
    SAFETY_TOOLS_WATCH = ["*CMakeLists.txt", "*.cmake", "doc/safety/", "tools/test/tests.yaml"]

    @classmethod
    def setUpClass(cls):
        cls.tests = run_tests.load_list(run_tests.LIST_FILE)["tests"]

    def test_rte22_selects_every_studio_part(self):
        sel = since.map_changes(self.tests, self.RTE22, run_tests.REPO)
        studio = {t.name for t in self.tests if t.name.startswith("studio-")}
        if not studio and not (run_tests.REPO / "tools" / "studio").is_dir():
            self.skipTest("no Studio entries: tools/studio is not in this tree (exported open tree, PLAT-62)")
        self.assertTrue(studio)
        self.assertLessEqual(studio, set(sel.triggers))

    def test_arch57_selects_safety_tools_with_its_watch(self):
        by_name = {t.name: t for t in self.tests}
        tool = by_name["safety-tools"]
        if not tool.watch:              # until the coordinator takes the proposal into tests.yaml
            tool.watch = list(self.SAFETY_TOOLS_WATCH)
        sel = since.map_changes(self.tests, self.ARCH57, run_tests.REPO)
        self.assertIn("safety-tools", sel.triggers)
        self.assertEqual(sel.triggers["safety-tools"][0][0], "CMakeLists.txt")

    def test_only_target_builds_are_without_mapping(self):
        sel = since.map_changes(self.tests, ["README.md"], run_tests.REPO)
        kinds = {t.name: t.kind for t in self.tests}
        self.assertTrue(sel.unmapped_entries)
        self.assertEqual({kinds[n] for n in sel.unmapped_entries}, {"cmake_preset"})


if __name__ == "__main__":
    unittest.main()
