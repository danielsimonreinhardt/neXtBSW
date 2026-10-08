"""Tests of the provider-neutral CI pipeline (TOOL-03).

Run: python -m unittest tools/ci/test_ci.py

Checks the pinned configuration against the repository (every tool pinned for Windows and Linux, constraints cover
tools/requirements.txt, exceptions name existing tests and presets), the tool cache with invented archives served
from file:// URLs (checksum, marker, interrupted installation), profile coverage with invented presets, the verdict
rules (strict/non-strict, allowed and unexpected skips), a real `run` through tools/test with an invented test list,
and that the GitHub workflow stays a thin hull.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from tools.ci import ci  # noqa: E402

WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def quiet(*_args, **_kwargs) -> None:
    pass


def provenance_finding(text: str) -> bool:
    """True for a finding of the provenance half of the pin stage (TOOL-65).

    It is the half that carries file **and line** of `nextbsw.yaml`, so `nextbsw.yaml:<n>` tells the two apart.
    The identity half (TOOL-67, `tools/test/workspace.pin_findings`) always measures against the tree the run
    starts from, which in these tests is this repository; whether that tree happens to be tagged and clean right
    now is not what they are about.
    """
    head = "nextbsw.yaml:"
    return text.startswith(head) and text[len(head):len(head) + 1].isdigit()


class PinnedConfiguration(unittest.TestCase):
    """The real tools/ci configuration files."""

    def test_every_tool_is_pinned_for_windows_and_linux(self):
        versions = ci.load_toml(ci.VERSIONS_FILE)
        for os_name in ("windows", "linux"):
            tools = ci.pinned_tools(versions, os_name)
            for name in ("arm_gcc", "cmake", "ninja"):
                self.assertIn(name, tools, os_name)
                self.assertIn(tools[name].version, tools[name].url, f"{name}: URL does not match the version")
                self.assertTrue(tools[name].url.startswith("https://"), name)

    def test_python_version_is_exact(self):
        self.assertRegex(ci.pinned_python(), r"^\d+\.\d+\.\d+$")

    def test_constraints_cover_requirements_exactly(self):
        pins = ci.read_constraints()
        names = ci.requirement_names() | ci.requirement_names(ci.STUDIO_REQUIREMENTS)
        missing = sorted(names - set(pins))
        self.assertEqual(missing, [], "packages of tools/requirements*.txt without an exact pin")

    def test_core_requirements_stay_free_of_the_studio_packages(self):
        # TOOL-63 (gap N8): a workspace that only builds and checks must not have to install Qt
        core = ci.requirement_names()
        studio = ci.requirement_names(ci.STUDIO_REQUIREMENTS)
        self.assertEqual(core & studio, set())
        self.assertEqual(studio, {"pyside6", "pytest", "pytest-qt", "pyinstaller"})
        for name in studio:
            self.assertNotIn(name, core)

    def test_check_packages_reports_core_and_studio_separately(self):
        report = ci.Report()
        ci.check_packages(ci.read_constraints(), ci.WARN, report)
        self.assertEqual([f.area for f in report.findings], ["Python-Pakete", "Studio-Pakete"])
        self.assertFalse(report.failed, "a package finding must be a warning without --strict")

    def test_stale_environment_marker_is_a_warning_with_the_repairing_command(self):
        # TOOL-63: a .venv built by tools/setup carries a marker; after a pin move `check` names the way back
        from unittest import mock

        setup = ci.setup_module()
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "workspace" / ".venv"
            python = setup.venv_python(venv)
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
            setup.write_marker(venv, REPO, setup.CORE, "3.12.10")
            with mock.patch.object(sys, "executable", str(python)):
                report = ci.Report()
                ci.check_environment(report)
                self.assertEqual([(f.area, f.status) for f in report.findings], [("Umgebung", ci.OK)])

                marker = setup.marker_file(venv)
                data = json.loads(marker.read_text(encoding="utf-8"))
                data["fingerprint"] = "sha256:0"
                marker.write_text(json.dumps(data), encoding="utf-8")
                report = ci.Report()
                ci.check_environment(report)
                self.assertEqual([f.status for f in report.findings], [ci.WARN])
                self.assertIn("python -m tools.setup venv", report.findings[0].text)
                self.assertFalse(report.failed, "a stale marker never makes a run red")

    def test_environment_without_a_marker_stays_silent(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            python = ci.setup_module().venv_python(Path(tmp) / ".venv")
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
            with mock.patch.object(sys, "executable", str(python)):
                report = ci.Report()
                ci.check_environment(report)
                self.assertEqual(report.findings, [], "an environment installed by hand has no marker")

    def test_strictdoc_constraints(self):
        # DOC-21: StrictDoc pinned exactly, every dependency pinned, and a package in both constraint files has the
        # same version in both (the StrictDoc venv and the .venv must not drift apart where they overlap)
        docs = ci.read_constraints(ci.DOCS_CONSTRAINTS)
        self.assertEqual(sorted(ci.requirement_names(ci.DOCS_REQUIREMENTS) - set(docs)), [])
        self.assertIn("strictdoc==0.30.1", ci.DOCS_REQUIREMENTS.read_text(encoding="utf-8").splitlines())
        self.assertEqual(docs["strictdoc"], "0.30.1")
        sys.path.insert(0, str(REPO / "tools" / "trace"))
        import sdocmodel
        self.assertEqual(sdocmodel.STRICTDOC_VERSION, docs["strictdoc"])
        main = ci.read_constraints()
        self.assertEqual({n: (main[n], docs[n]) for n in set(main) & set(docs) if main[n] != docs[n]}, {})
        # the Studio build and the .venv stay free of it
        self.assertNotIn("strictdoc", ci.requirement_names())

    def test_constraints_reject_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.txt"
            path.write_text("PyYAML~=6.0\n", encoding="utf-8")
            with self.assertRaises(ci.CiError):
                ci.read_constraints(path)

    def test_pipeline_exceptions_name_existing_tests_and_presets(self):
        pipeline = ci.load_toml(ci.PIPELINE_FILE)
        names = {t["name"] for t in ci.load_test_list(REPO / "tools" / "test" / "tests.yaml")}
        for name in pipeline.get("allowed_skips", {}):
            self.assertIn(name, names, "allowed_skips names a test that tools/test/tests.yaml does not have")
        presets = {p["name"] for p in json.loads((REPO / "CMakePresets.json").read_text(encoding="utf-8"))
                   ["configurePresets"]}
        for name, entry in pipeline["presets"]["not_built"].items():
            self.assertIn(name, presets)
            self.assertTrue(entry["reason"].strip(), name)
        for entry in pipeline.get("outside", []):
            self.assertTrue(entry["reason"].strip(), entry["name"])

    def test_every_visible_profile_of_the_repository_has_a_decision(self):
        report = ci.Report()
        ci.check_presets(ci.load_test_list(REPO / "tools" / "test" / "tests.yaml"),
                         ci.load_toml(ci.PIPELINE_FILE), REPO, report)
        self.assertFalse(report.failed, [f.text for f in report.findings if f.status == ci.FAIL])


def make_zip(folder: Path, files: dict[str, bytes]) -> tuple[Path, str]:
    path = folder / "tool.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


class ToolCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cache = self.tmp / "cache"
        self.served = self.tmp / "served"
        self.served.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def tool(self, sha: str, archive: Path, bin_dir: str = "bin") -> ci.Tool:
        return ci.Tool("fake", "1.2.3", ["fakecc", "--version"], bin_dir, archive.as_uri(), sha)

    def test_install_extracts_marks_and_reuses(self):
        archive, sha = make_zip(self.served, {"fake-1.2.3/bin/fakecc" + ci.EXE: b"x",
                                              "fake-1.2.3/lib/deep/bin/fakecc" + ci.EXE: b"y"})
        tool = self.tool(sha, archive)
        self.assertIsNone(ci.installed_bin(self.cache, tool))
        found = ci.install(self.cache, tool, quiet)
        self.assertEqual(found, self.cache / "fake-1.2.3" / "fake-1.2.3" / "bin")   # shortest match
        self.assertEqual(ci.installed_bin(self.cache, tool), found)
        archive.unlink()                                                            # no second download
        self.assertEqual(ci.install(self.cache, tool, quiet), found)

    def test_wrong_checksum_is_an_error_and_leaves_nothing(self):
        archive, _ = make_zip(self.served, {"bin/fakecc" + ci.EXE: b"x"})
        tool = self.tool("0" * 64, archive)
        with self.assertRaisesRegex(ci.CiError, "SHA-256"):
            ci.install(self.cache, tool, quiet)
        self.assertIsNone(ci.installed_bin(self.cache, tool))
        self.assertEqual(list((self.cache / "downloads").iterdir()), [])

    def test_missing_executable_is_an_error(self):
        archive, sha = make_zip(self.served, {"bin/othercc" + ci.EXE: b"x"})
        with self.assertRaisesRegex(ci.CiError, "not found"):
            ci.install(self.cache, self.tool(sha, archive), quiet)

    def test_interrupted_installation_is_not_taken_as_complete(self):
        archive, sha = make_zip(self.served, {"bin/fakecc" + ci.EXE: b"x"})
        tool = self.tool(sha, archive)
        root = ci.tool_dir(self.cache, tool)
        (root / "bin").mkdir(parents=True)
        (root / "bin" / ("fakecc" + ci.EXE)).write_bytes(b"half")        # tree without marker
        self.assertIsNone(ci.installed_bin(self.cache, tool))
        (root / ci.MARKER).write_text("f" * 64 + "\nbin\n", encoding="utf-8")  # marker of another archive
        self.assertIsNone(ci.installed_bin(self.cache, tool))
        self.assertEqual(ci.install(self.cache, tool, quiet), root / "bin")

    def test_pinned_environment_puts_tools_in_front(self):
        archive, sha = make_zip(self.served, {"bin/arm-none-eabi-gcc" + ci.EXE: b"x"})
        versions = {"tools": {"arm_gcc": {"version": "9.9.9", "probe": ["arm-none-eabi-gcc", "-dumpversion"],
                                          "bin": "bin", ci.host_os(): {"url": archive.as_uri(), "sha256": sha}}}}
        with self.assertRaisesRegex(ci.CiError, "setup"):
            ci.tool_environment("pinned", self.cache, versions, {"PATH": "/usr/bin"})
        env = ci.tool_environment("pinned", self.cache, versions, {"PATH": "/usr/bin"}, quiet, install_missing=True)
        gcc_bin = str(self.cache / "arm_gcc-9.9.9" / "bin")
        self.assertEqual(env["NX_ARM_GCC_BIN"], gcc_bin)
        self.assertTrue(env["PATH"].startswith(gcc_bin + os.pathsep))
        local = ci.tool_environment("local", self.cache, versions, {"PATH": "/usr/bin"})
        self.assertNotIn("NX_ARM_GCC_BIN", local)
        self.assertEqual(local["PATH"], "/usr/bin")
        local = ci.tool_environment("local", self.cache, versions, {"PATH": "/usr/bin", "NX_ARM_GCC_BIN": "/gcc"})
        self.assertEqual(local["PATH"], "/gcc" + os.pathsep + "/usr/bin")    # probed like tools/test sees it

    def test_strictdoc_venv_is_announced_and_checked(self):
        # DOC-21: tools/test sees StrictDoc through NX_STRICTDOC_VENV, set only when the venv exists in the cache
        self.assertEqual(ci.strictdoc_venv(self.cache), self.cache / "strictdoc-venv")
        env = ci.tool_environment("local", self.cache, {"tools": {}}, {"PATH": "/usr/bin"})
        self.assertNotIn("NX_STRICTDOC_VENV", env)
        report = ci.Report()
        ci.check_docs_packages(env, ci.WARN, report)
        self.assertEqual([(f.area, f.status) for f in report.findings], [("StrictDoc", ci.WARN)])
        python = ci.venv_python(ci.strictdoc_venv(self.cache))
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")
        env = ci.tool_environment("local", self.cache, {"tools": {}}, {"PATH": "/usr/bin"})
        self.assertEqual(env["NX_STRICTDOC_VENV"], str(self.cache / "strictdoc-venv"))
        env = ci.tool_environment("local", self.cache, {"tools": {}}, {"PATH": "/usr/bin", "NX_STRICTDOC_VENV": "x"})
        self.assertEqual(env["NX_STRICTDOC_VENV"], "x")   # an explicit setting wins

    def test_clean_build_root_is_wiped_but_never_the_repository(self):
        root = self.tmp / "build" / "ci"
        (root / "demo" / "CMakeFiles").mkdir(parents=True)
        (root / "demo" / "CMakeCache.txt").write_text("CMAKE_C_COMPILER=/old/gcc\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            ci.wipe_build_root(root, repo=self.tmp / "repo")
        self.assertFalse(root.exists())
        for forbidden in (self.tmp / "repo", self.tmp):
            with self.assertRaisesRegex(ci.CiError, "refusing"):
                ci.wipe_build_root(forbidden, repo=self.tmp / "repo")

    def test_tool_without_download_for_this_os(self):
        versions = {"tools": {"x": {"version": "1", "probe": ["x"], "bin": ".",
                                    "windows": {"url": "https://a/x-1.zip", "sha256": "a" * 64}}}}
        with self.assertRaisesRegex(ci.CiError, "no download for linux"):
            ci.pinned_tools(versions, "linux")
        versions["tools"]["x"]["optional"] = True
        self.assertEqual(ci.pinned_tools(versions, "linux"), {})


class Profiles(unittest.TestCase):
    """Profile coverage with invented presets and test lists."""

    PRESETS = {"configurePresets": [
        {"name": "base", "hidden": True},
        {"name": "wt", "hidden": True},
        {"name": "alpha", "inherits": "base", "cacheVariables": {"NX_PROJECT": "p/alpha.yaml"}},
        {"name": "alpha-alias", "inherits": ["alpha"]},
        {"name": "alpha-wt", "inherits": ["wt", "alpha"]},
        {"name": "beta", "inherits": "base", "cacheVariables": {"NX_PROJECT": {"type": "FILEPATH",
                                                                               "value": "p/beta.yaml"}}},
        {"name": "gamma", "cacheVariables": {"NX_PROJECT": "p/gamma.yaml"}},
    ]}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        (self.repo / "CMakePresets.json").write_text(json.dumps(self.PRESETS), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_worktree_presets_are_ignored_and_aliases_grouped(self):
        projects = ci.visible_projects(self.repo / "CMakePresets.json", ["wt"])
        self.assertEqual(projects, {"p/alpha.yaml": ["alpha", "alpha-alias"], "p/beta.yaml": ["beta"],
                                    "p/gamma.yaml": ["gamma"]})

    def test_profile_without_decision_is_red(self):
        pipeline = {"presets": {"worktree_bases": ["wt"],
                                "not_built": {"beta": {"reason": "needs something"}}}}
        report = ci.Report()
        skipped = ci.check_presets([{"name": "t", "kind": "cmake_preset", "preset": "alpha-alias"}],
                                   pipeline, self.repo, report)
        self.assertEqual([s["name"] for s in skipped], ["beta"])
        red = [f.text for f in report.findings if f.status == ci.FAIL]
        self.assertEqual(len(red), 1)
        self.assertIn("gamma", red[0])
        self.assertIn("ohne CI-Entscheidung", red[0])

    def test_preset_with_own_cache_variable_is_a_profile_of_its_own(self):
        # PLAT-71: a variant of a project (NX_ETHERNET, BUILD_TARGET_RTOS) counts separately, so a dropped build
        # test of the variant turns red even though the project itself is still built
        presets = json.loads(json.dumps(self.PRESETS))
        presets["configurePresets"][0]["cacheVariables"] = {"BUILD_TARGET_RTOS": "FREERTOS"}
        presets["configurePresets"] += [
            {"name": "alpha-eth", "inherits": "alpha", "cacheVariables": {"NX_ETHERNET": "ON"}},
            {"name": "alpha-tx", "inherits": "alpha", "cacheVariables": {"BUILD_TARGET_RTOS": "THREADX"}},
            {"name": "alpha-tx-wt", "inherits": ["wt", "alpha-tx"]},
            {"name": "alpha-same", "inherits": "alpha", "cacheVariables": {"BUILD_TARGET_RTOS": "FREERTOS"}},
        ]
        (self.repo / "CMakePresets.json").write_text(json.dumps(presets), encoding="utf-8")
        projects = ci.visible_projects(self.repo / "CMakePresets.json", ["wt"])
        self.assertEqual(projects, {"p/alpha.yaml": ["alpha", "alpha-alias", "alpha-same"],
                                    "p/alpha.yaml [NX_ETHERNET=ON]": ["alpha-eth"],
                                    "p/alpha.yaml [BUILD_TARGET_RTOS=THREADX]": ["alpha-tx"],
                                    "p/beta.yaml": ["beta"], "p/gamma.yaml": ["gamma"]})
        pipeline = {"presets": {"worktree_bases": ["wt"]}}
        tests = [{"name": n, "kind": "cmake_preset", "preset": n} for n in ("alpha", "alpha-eth", "beta", "gamma")]
        report = ci.Report()
        ci.check_presets(tests, pipeline, self.repo, report)
        red = [f.text for f in report.findings if f.status == ci.FAIL]
        self.assertEqual(len(red), 1)
        self.assertIn("BUILD_TARGET_RTOS=THREADX", red[0])
        self.assertIn("alpha-tx", red[0])
        report = ci.Report()
        ci.check_presets(tests + [{"name": "t", "kind": "cmake_preset", "preset": "alpha-tx"}], pipeline,
                         self.repo, report)
        self.assertFalse(report.failed)

    def test_exception_for_unknown_preset_is_red(self):
        pipeline = {"presets": {"worktree_bases": ["wt"], "not_built": {"delta": {"reason": "x"}}}}
        report = ci.Report()
        tests = [{"name": n, "kind": "cmake_preset", "preset": n} for n in ("alpha", "beta", "gamma")]
        ci.check_presets(tests, pipeline, self.repo, report)
        self.assertEqual([f.text for f in report.findings if f.status == ci.FAIL],
                         ["pipeline.toml nennt unbekanntes Preset delta"])


class Verdict(unittest.TestCase):
    RESULT = {"summary": {"pass": 1, "fail": 0, "timeout": 0, "skip": 2, "total": 3, "cases": 5},
              "per_group": {}, "unlisted_test_files": [], "exit_code": 0,
              "tests": [{"name": "a", "group": "g", "status": "pass", "reason": ""},
                        {"name": "svd", "group": "g", "status": "skip", "reason": "NX_SVD_DIR fehlt"},
                        {"name": "zig", "group": "g", "status": "skip", "reason": "ziglang fehlt"}]}
    PIPELINE = {"allowed_skips": {"svd": "SVD nicht im Repository"}}

    def judge(self, strict: bool, result: dict | None = None) -> ci.Report:
        report = ci.Report()
        ci.judge_tests(result or self.RESULT, self.PIPELINE, argparse.Namespace(strict=strict), report)
        return report

    def test_unexpected_skip_is_red_only_when_strict(self):
        strict = self.judge(True)
        self.assertTrue(strict.failed)
        self.assertIn("zig unerwartet", [f.text for f in strict.findings if f.status == ci.FAIL][0])
        loose = self.judge(False)
        self.assertFalse(loose.failed)
        self.assertEqual([f.status for f in loose.findings], [ci.SKIP, ci.WARN])

    def test_red_test_unlisted_file_and_empty_run(self):
        result = json.loads(json.dumps(self.RESULT))
        result["tests"][0].update(status="timeout", reason="Zeitueberschreitung")
        result["unlisted_test_files"] = ["x/test_new.py"]
        texts = [f.text for f in self.judge(False, result).findings if f.status == ci.FAIL]
        self.assertEqual(len(texts), 2)
        empty = {**self.RESULT, "tests": [], "summary": {**self.RESULT["summary"], "total": 0}}
        self.assertTrue(self.judge(False, empty).failed)

    def test_summary_names_outside_steps_and_skips(self):
        payload = {"verdict": "fail", "platform": "P", "toolchain": "pinned", "strict": True, "duration_s": 3.0,
                   "stages": [{"name": "S", "status": "ok", "duration_s": 1.0}],
                   "findings": [{"area": "Test", "status": ci.SKIP, "text": "svd: weg"}],
                   "tests": self.RESULT["summary"], "per_group": {"g": {"pass": 1, "fail": 0, "skip": 2,
                                                                         "duration_s": 2.0}},
                   "artifacts": [{"file": "artifacts/t/app.elf", "bytes": 10, "text": 1, "data": 2, "bss": 3}],
                   "outside": [{"name": "MATLAB/Simulink", "reason": "eingecheckt"}]}
        text = ci.render_summary(payload)
        for needle in ("neXtBSW CI: ROT", "| g | 1 | 0 | 2 |", "svd: weg", "MATLAB/Simulink: eingecheckt",
                       "| artifacts/t/app.elf | 10 | 1 | 2 | 3 |"):
            self.assertIn(needle, text)


class Coverage(unittest.TestCase):
    """The coverage stage (TOOL-40): when it runs and how its result is judged."""

    PAYLOAD = {
        "total": {"lines_total": 100, "lines_covered": 90, "branches_total": 50, "branches_covered": 20,
                  "line_percent": 90.0, "branch_percent": 40.0},
        "modules": [{"module": "libs/bsw/e2e", "verdict": "unter Schwelle", "line_percent": 90.0,
                     "branch_percent": 40.0, "threshold_lines": 95.0, "threshold_branches": 70.0,
                     "own_threshold": True},
                    {"module": "libs/bsw/hallDecoder", "verdict": "ok", "line_percent": 99.0,
                     "branch_percent": 60.0, "threshold_lines": 80.0, "threshold_branches": 50.0,
                     "own_threshold": False}],
        "entries": {"total": 3, "pass": 1, "fail": 1, "skip": 1,
                    "items": [{"name": "e2e", "status": "pass", "reason": ""},
                              {"name": "kaputt", "status": "fail", "reason": "Binden fehlgeschlagen\nmehr"},
                              {"name": "demoswc-sil", "status": "skip", "reason": "Vorbedingung fehlt"}]},
        "enforced": False,
    }

    def judge(self, payload: dict | None, pipeline: dict | None = None) -> ci.Report:
        report = ci.Report()
        ci.judge_coverage(payload, pipeline if pipeline is not None else {"coverage": {}}, report)
        return report

    def test_below_threshold_is_a_warning_while_not_enforced(self):
        report = self.judge(self.PAYLOAD)
        self.assertFalse(report.failed)
        warnings = [f.text for f in report.findings if f.status == ci.WARN]
        self.assertTrue(any("libs/bsw/e2e" in text for text in warnings))
        self.assertTrue(any("Schwellen werden berichtet" in f.text for f in report.findings))

    def test_below_threshold_is_red_when_enforced(self):
        report = self.judge({**self.PAYLOAD, "enforced": True})
        self.assertTrue(report.failed)
        self.assertIn("libs/bsw/e2e", [f.text for f in report.findings if f.status == ci.FAIL][0])

    def test_broken_measurement_is_a_warning_and_with_strict_red(self):
        self.assertFalse(self.judge(self.PAYLOAD).failed)
        strict = self.judge(self.PAYLOAD, {"coverage": {"strict": True}})
        self.assertTrue(strict.failed)
        self.assertIn("kaputt", [f.text for f in strict.findings if f.status == ci.FAIL][0])

    def test_skipped_entry_is_only_reported(self):
        statuses = {f.text.split(":")[0]: f.status for f in self.judge(self.PAYLOAD).findings}
        self.assertEqual(ci.SKIP, statuses["demoswc-sil"])

    def test_no_result_at_all(self):
        self.assertFalse(self.judge(None).failed)
        self.assertTrue(self.judge(None, {"coverage": {"strict": True}}).failed)

    def test_when_the_stage_runs(self):
        enabled = {"coverage": {"enabled": True}}
        plain = argparse.Namespace(no_coverage=False, k=None, group=None)
        self.assertTrue(ci.coverage_wanted(enabled, plain))
        self.assertFalse(ci.coverage_wanted({"coverage": {"enabled": False}}, plain))
        self.assertFalse(ci.coverage_wanted({}, plain))
        self.assertFalse(ci.coverage_wanted(enabled, argparse.Namespace(no_coverage=True, k=None, group=None)),
                         "--no-coverage schaltet ab")
        self.assertFalse(ci.coverage_wanted(enabled, argparse.Namespace(no_coverage=False, k="e2e", group=None)),
                         "eine Auswahl misst nicht die ganze Liste")

    def test_pipeline_of_the_repository_switches_it_on_but_does_not_enforce(self):
        settings = ci.load_toml(ci.PIPELINE_FILE).get("coverage", {})
        self.assertTrue(settings.get("enabled"))
        self.assertFalse(settings.get("enforce"), "Schwellen erst nach Abnahme erzwingen (SYS-19)")

    def test_summary_shows_the_table(self):
        payload = {"verdict": "pass", "platform": "P", "toolchain": "pinned", "strict": False,
                   "duration_s": 3.0, "stages": [], "findings": [], "tests": None, "per_group": {},
                   "artifacts": [], "outside": [], "coverage": self.PAYLOAD}
        text = ci.render_summary(payload)
        self.assertIn("Testabdeckung", text)
        self.assertIn("| `libs/bsw/e2e` | 90.0 % | 40.0 % | 95/70 | unter Schwelle |", text)
        self.assertIn("| `libs/bsw/hallDecoder` | 99.0 % | 60.0 % | (Vorgabe) | ok |", text)
        self.assertIn("Schwellen nur berichtet", text)


class EndToEnd(unittest.TestCase):
    """`run` through the real tools/test with an invented list: report files, verdict, exit code."""

    def test_run_with_invented_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tests = tmp / "tests.yaml"
            tests.write_text(
                "version: 1\ngroups: [python]\n"
                "tools:\n  nothing:\n    kind: executable\n    name: nx-ci-tool-that-does-not-exist\n"
                "tests:\n"
                "  - name: green\n    group: python\n    kind: command\n    comment: invented\n"
                "    argv: ['{python}', '-c', 'print(\"3 checks, 0 failures\")']\n"
                "  - name: needs-tool\n    group: python\n    kind: command\n    comment: invented\n"
                "    argv: ['{python}', '-c', 'pass']\n    requires: [nothing]\n",
                encoding="utf-8")
            out = tmp / "out"
            argv = [sys.executable, "-m", "tools.ci", "run", "--toolchain", "local", "--no-inventory",
                    "--jobs", "1", "--out", str(out), "--build-root", str(tmp / "build"), "--tests", str(tests)]
            proc = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                                  env={**os.environ, "PYTHONUTF8": "1"}, timeout=600)
            self.assertEqual(proc.returncode, 1, proc.stdout[-3000:] + proc.stderr[-3000:])
            payload = json.loads((out / "ci-result.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["tests"]["pass"], 1)
            self.assertEqual(payload["tests"]["skip"], 1)
            # SYS-67: the report names the state it judged, at the start and at the end of the run. PLAT-62: an
            # exported tree without git repository (distcheck export without --git-init) has no commit to name -
            # the report says so (empty), the rest of the run is checked all the same
            in_git = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--git-dir"], capture_output=True).returncode == 0
            if in_git:
                self.assertRegex(payload["provenance"]["commit"] or "", r"^[0-9a-f]{40}$")
                self.assertRegex(payload["provenance"]["tree"] or "", r"^[0-9a-f]{40}$")
            else:
                self.assertFalse(payload["provenance"]["commit"])
            self.assertEqual(payload["provenance"]["commit"], payload["provenance"]["end"]["commit"])
            self.assertIsNone(payload["workspace_provenance"])
            self.assertFalse(payload["slow"])
            texts = [f["text"] for f in payload["findings"]]
            # the invented list builds no profile: every visible preset of the repository lacks a decision
            self.assertTrue(any("ohne CI-Entscheidung" in t for t in texts), texts)
            self.assertTrue(any("needs-tool unerwartet uebersprungen" in t for t in texts), texts)
            summary = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn("neXtBSW CI: ROT", summary)
            self.assertTrue((out / "tests.log").is_file())
            # TOOL-40: the coverage stage ran as part of the pipeline; the invented list has no zig entry, so
            # the report is empty - but it exists, is read back and appears in the summary
            self.assertTrue((out / "coverage.json").is_file(), "Abdeckungsbericht fehlt")
            self.assertEqual(0, payload["coverage"]["entries"]["total"])
            self.assertIn("Testabdeckung", summary)
            self.assertFalse(any("Abdeckung" in t and "Messlauf ohne Ergebnis" in t for t in texts), texts)
            # PLAT-74: the static analysis stage ran; the invented list built no target, so it says so (not green)
            self.assertTrue(any(f["area"] == "Statik" and "Compile-Datenbank fehlt" in f["text"]
                                for f in payload["findings"]), texts)

    def test_strict_refuses_partial_runs(self):
        with redirect_stdout(io.StringIO()):
            code = ci.main(["run", "--strict", "-k", "nxconfig", "--toolchain", "local"])
        self.assertEqual(code, 2)


class WorkspacePipeline(unittest.TestCase):
    """`run`/`check` against a customer workspace (TOOL-67, gap N12).

    The workspace is synthetic and lies in %TEMP%, with invented names throughout (the customer repository
    `omega`, the board `omega_valve_d2`): the eval board is never the yardstick. The pinned tree is **this**
    tree, because the pin check has to compare a real VERSION against a real nextbsw.yaml.
    """

    BOARD = "omega_valve_d2"

    def make_workspace(self, tmp: Path, version: str | None = None, with_pipeline: bool = True) -> Path:
        ws = tmp / "omega"
        (ws / "ci").mkdir(parents=True)
        relative = Path(os.path.relpath(REPO, ws)).as_posix()
        version = version or (REPO / "VERSION").read_text(encoding="utf-8").strip()
        (ws / "nextbsw.yaml").write_text(
            "# synthetic workspace of tools/ci/test_ci.py - test data, no real customer\n"
            f"schema_version: 1\nnextbsw:\n  version: {version}\n  source: submodule\n"
            f"  path: {relative}\n  profile: open\n", encoding="utf-8")
        (ws / "CMakePresets.json").write_text(json.dumps({
            "version": 6,
            "configurePresets": [{"name": "omega-main", "binaryDir": "${sourceDir}/build/main",
                                  "cacheVariables": {"NX_PROJECT": "${sourceDir}/projects/omega_main"}}],
        }), encoding="utf-8")
        (ws / "ci" / "tests.yaml").write_text(
            "version: 1\ngroups: [python]\n"
            "inventory:\n  patterns: ['test_*.py']\n  skip_dirs: [build]\n  ignore: []\n"
            "tests:\n"
            "  - name: omega-konfiguration\n    group: python\n    kind: command\n"
            "    comment: erfundener Test des Kunden\n"
            "    argv: ['{python}', '-c', 'print(\"2 checks, 0 failures\")']\n    covers: []\n",
            encoding="utf-8")
        if with_pipeline:
            (ws / "ci" / "pipeline.toml").write_text(
                "schema = 1\n[run]\ngroups = ['all']\nclean = false\n[allowed_skips]\n"
                "[presets]\nworktree_bases = []\n"
                "[presets.not_built.omega-main]\nreason = 'erfundenes Profil, in diesem Test nicht gebaut'\n"
                "[[outside]]\nname = 'Hardware'\nreason = 'kein Flashen'\n"
                "[artifacts]\npatterns = ['**/*.elf']\n", encoding="utf-8")
        return ws

    def target(self, ws: Path, **extra):
        args = argparse.Namespace(workspace=ws, tests=REPO / "tools" / "test" / "tests.yaml",
                                  out=None, build_root=None, **extra)
        return ci.resolve_target(args)

    def test_target_takes_pipeline_presets_and_build_root_from_the_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self.make_workspace(Path(tmp))
            target = self.target(ws)
            self.assertTrue(target.is_workspace)
            self.assertEqual(target.pipeline_file, ws / "ci" / "pipeline.toml")
            self.assertEqual(target.root, ws.resolve())
            self.assertTrue(target.build_root.is_relative_to(ws.resolve()))
            self.assertTrue(target.out.is_relative_to(ws.resolve()))

    def test_without_its_own_pipeline_the_trees_defaults_are_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self.make_workspace(Path(tmp), with_pipeline=False)
            self.assertEqual(self.target(ws).pipeline_file, ci.PIPELINE_FILE)

    def test_a_run_in_the_tree_is_unchanged(self):
        args = argparse.Namespace(workspace=None, tests=REPO / "tools" / "test" / "tests.yaml",
                                  out=None, build_root=None)
        target = ci.resolve_target(args)
        self.assertFalse(target.is_workspace)
        self.assertEqual(target.root, REPO)
        self.assertEqual(target.pipeline_file, ci.PIPELINE_FILE)
        self.assertEqual(target.out, (REPO / "build" / "ci-out").resolve())

    def identity(self, report: ci.Report) -> list:
        """The identity half of the pin stage - the provenance half (TOOL-65) is judged in ReleaseStrictness."""
        return [f for f in report.findings if f.area == "Pin" and not provenance_finding(f.text)]

    def test_matching_pin_is_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = ci.Report()
            ci.check_pin(self.target(self.make_workspace(Path(tmp))), report)
            findings = self.identity(report)
            self.assertEqual([f.status for f in findings], [ci.OK], [f.text for f in findings])
            self.assertFalse(report.failed, "the provenance half never stops a run without --release")

    def test_pin_of_another_version_is_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = ci.Report()
            ci.check_pin(self.target(self.make_workspace(Path(tmp), version="99.0.0")), report)
            self.assertTrue(report.failed)
            self.assertTrue(any("99.0.0" in f.text for f in self.identity(report)),
                            [f.text for f in report.findings])

    def test_presets_of_the_workspace_decide_not_the_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self.make_workspace(Path(tmp))
            target = self.target(ws)
            report = ci.Report()
            skipped = ci.check_presets(target.entries(), ci.load_toml(target.pipeline_file), target.root, report)
            texts = [f.text for f in report.findings]
            self.assertFalse(report.failed, texts)
            self.assertEqual([s["name"] for s in skipped], ["omega-main"])
            self.assertFalse(any("evalboard" in t for t in texts), texts)

    def test_entries_are_the_workspaces_own_plus_the_inherited_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = self.target(self.make_workspace(Path(tmp))).entries()
            names = {e["name"] for e in entries}
            self.assertIn("omega-konfiguration", names)
            self.assertNotIn("target-demo", names)       # a target build of the tree's own board
            self.assertTrue(all(e.get("in_workspace") for e in entries if e["name"] != "omega-konfiguration"))

    def test_a_build_root_inside_the_tree_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self.make_workspace(Path(tmp))
            args = argparse.Namespace(workspace=ws, tests=REPO / "tools" / "test" / "tests.yaml",
                                      out=None, build_root=REPO / "build" / "kunde")
            with self.assertRaises(ci.CiError) as caught:
                ci.resolve_target(args)
            self.assertIn("liest den Baum nur", str(caught.exception))

    def test_a_folder_without_a_pin_is_no_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(workspace=Path(tmp), tests=REPO / "tools" / "test" / "tests.yaml",
                                      out=None, build_root=None)
            with self.assertRaises(ci.CiError) as caught:
                ci.resolve_target(args)
            self.assertIn("nextbsw.yaml", str(caught.exception))

    def test_complete_run_against_the_workspace(self):
        """The whole pipeline: pin, tests of the workspace, report - and the tree is unchanged afterwards."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = self.make_workspace(Path(tmp))
            out = Path(tmp) / "ci-out"
            argv = [sys.executable, "-m", "tools.ci", "run", "--toolchain", "local", "--workspace", str(ws),
                    "--jobs", "1", "--out", str(out), "--build-root", str(Path(tmp) / "bau")]
            proc = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                                  env={**os.environ, "PYTHONUTF8": "1"}, timeout=900)
            output = proc.stdout[-4000:] + proc.stderr[-3000:]
            payload = json.loads((out / "ci-result.json").read_text(encoding="utf-8"))
            texts = [f["text"] for f in payload["findings"]]
            self.assertEqual(proc.returncode, 0, output + "\n" + "\n".join(texts))
            self.assertEqual(payload["workspace"], str(ws.resolve()))
            self.assertEqual(payload["tree"], str(REPO))
            self.assertEqual(payload["tests"]["fail"], 0, texts)
            self.assertTrue(any(f["area"] == "Pin" and f["status"] == ci.OK for f in payload["findings"]), texts)
            # TOOL-217: the pipeline of this workspace does not switch the stages for its own code on - they are
            # named as not run, never green and never measured against the tree's thresholds
            self.assertEqual(sum("im Workspace-Lauf ausgeschaltet" in t for t in texts), 2, texts)
            self.assertIsNone(payload["coverage"])
            summary = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn(str(ws.resolve()), summary)
            self.assertIn("gruen", summary)
            # the customer's own entry ran, the tree's target builds did not
            names = {t["name"] for t in json.loads((out / "tests.json").read_text(encoding="utf-8"))["tests"]}
            self.assertIn("omega-konfiguration", names)
            self.assertNotIn("target-demo", names)


class WorkspaceOwnCode(unittest.TestCase):
    """TOOL-217 (customer-project.md gap N19): coverage and static analysis of the workspace's own code.

    Reuses the synthetic workspace `omega`; the two tools are replaced by recorders - their own measurement is tested
    in tools/test/test_coverage.py and tools/misra/test_misra.py with synthetic workspaces of their own."""

    make_workspace = WorkspacePipeline.make_workspace      # same synthetic workspace, without rerunning its tests
    target = WorkspacePipeline.target

    ON = {"coverage": {"enabled": True, "enforce": False},
          "static_analysis": {"enabled": True, "enforce": True, "compile_db": "main/compile_commands.json"}}

    def args(self, **extra):
        values = dict(no_coverage=False, no_static_analysis=False, k=None, group=None, strict=False, jobs=1,
                      tests=REPO / "tools" / "test" / "tests.yaml")
        values.update(extra)
        return argparse.Namespace(**values)

    def problems(self, ws: Path, pipeline: dict, **extra) -> dict:
        target = self.target(ws)
        return {section: ci.workspace_stage_problem(target, section, pipeline, self.args(**extra))
                for section in ("coverage", "static_analysis")}

    def test_stages_switched_off_are_named_as_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            found = self.problems(self.make_workspace(Path(tmp)), {})
            for section, (status, text) in found.items():
                self.assertEqual(status, ci.SKIP, section)
                self.assertIn("ci/pipeline.toml", text)
                self.assertIn(f"[{section}] enabled", text)

    def test_switched_on_without_files_is_red_with_the_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            found = self.problems(self.make_workspace(Path(tmp)), self.ON)
            self.assertEqual(found["coverage"][0], ci.FAIL)
            self.assertIn("template coverage", found["coverage"][1])
            self.assertEqual(found["static_analysis"][0], ci.FAIL)
            self.assertIn("template misra-deviations", found["static_analysis"][1])

    def test_switched_on_with_files_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self.make_workspace(Path(tmp))
            self.write_own_files(ws)
            self.assertEqual(self.problems(ws, self.ON), {"coverage": None, "static_analysis": None})
            no_db = {**self.ON, "static_analysis": {"enabled": True}}
            status, text = self.problems(ws, no_db)["static_analysis"]
            self.assertEqual(status, ci.FAIL)
            self.assertIn("compile_db", text)
            # a partial run measures nothing meaningful: named, not red
            self.assertEqual(self.problems(ws, self.ON, k="omega")["coverage"][0], ci.SKIP)

    def write_own_files(self, ws: Path) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(ci.main(["template", "coverage", "--out", str(ws)]), 0)
            self.assertEqual(ci.main(["template", "misra-deviations", "--out", str(ws)]), 0)
        self.assertTrue((ws / "ci" / "coverage.yaml").is_file())
        self.assertTrue((ws / "ci" / "misra" / "deviations.yaml").is_file())

    def test_both_tools_are_called_for_the_workspace(self):
        """The argv the stages hand to coverage.py and tools.misra: --workspace, never the tree's files."""
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if "tools.misra" in argv:
                out = Path(argv[argv.index("--out") + 1])
                out.mkdir(parents=True, exist_ok=True)
                (out / "result.json").write_text(json.dumps({"new": [], "resolved": []}), encoding="utf-8")
            else:
                Path(argv[argv.index("--json") + 1]).write_text(json.dumps({"modules": []}), encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            ws = self.make_workspace(Path(tmp))
            build = Path(tmp) / "bau"
            (build / "main").mkdir(parents=True)
            (build / "main" / "compile_commands.json").write_text("[]", encoding="utf-8")
            out = Path(tmp) / "out"
            out.mkdir()
            original = ci.subprocess.run
            ci.subprocess.run = fake_run
            try:
                with redirect_stdout(io.StringIO()):
                    ci.measure_coverage(self.ON, self.args(), out, build, dict(os.environ), ws)
                    ci.run_static_analysis(self.ON, self.args(), out, build, dict(os.environ), Path(tmp), ws)
            finally:
                ci.subprocess.run = original
        coverage, misra = calls
        self.assertEqual(coverage[coverage.index("--workspace") + 1], str(ws))
        self.assertEqual(misra[misra.index("--workspace") + 1], str(ws))
        self.assertEqual(misra[misra.index("--compile-db") + 1], str(build / "main" / "compile_commands.json"))
        self.assertNotIn("--baseline", misra)          # the workspace's own baseline, chosen by tools.misra

    def test_pipeline_template_switches_both_on_for_own_code(self):
        pipeline = ci.load_toml(ci.TEMPLATE_DIR / "workspace-pipeline.toml")
        self.assertTrue(pipeline["coverage"]["enabled"])
        self.assertTrue(pipeline["static_analysis"]["enabled"])
        self.assertTrue(pipeline["static_analysis"]["compile_db"])
        self.assertNotIn("noch offen", ci.template_text("pipeline"))

    def test_misra_template_is_a_valid_empty_list(self):
        from tools.misra import analysis

        path = ci.TEMPLATE_DIR / "workspace-misra-deviations.yaml"
        messages = analysis.validate_deviations(analysis.read_yaml(path), path)
        self.assertEqual([m for m in messages if m.severity == "error"], [])


class WorkspaceTemplates(unittest.TestCase):
    """The CI files a customer copies into his workspace (TOOL-67): they exist, parse and call the two commands."""

    def test_every_template_exists_and_names_where_it_belongs(self):
        for name, (file, place, purpose) in ci.TEMPLATES.items():
            self.assertTrue((ci.TEMPLATE_DIR / file).is_file(), name)
            self.assertTrue(place and purpose, name)
            self.assertTrue(ci.template_text(name).strip(), name)

    def test_provider_templates_are_yaml_and_carry_submodule_and_credentials(self):
        import yaml

        for name in ("github", "gitlab"):
            text = ci.template_text(name)
            self.assertIsInstance(yaml.safe_load(text), dict, name)
            self.assertIn("submodule", text.lower(), name)
            self.assertIn("NEXTBSW_TOKEN", text, f"{name}: no credential for a private tree")
            self.assertIn("tools.ci run --strict --workspace", text, name)
            self.assertIn("tools.ci setup", text, name)
            self.assertIn("tools/setup venv", text, name)

    def test_the_workspace_list_template_loads_with_the_runner(self):
        sys.path.insert(0, str(REPO / "tools" / "test"))
        import run_tests

        data = run_tests.load_list(ci.TEMPLATE_DIR / "workspace-tests.yaml", run_tests.WORKSPACE)
        self.assertTrue(data["tests"])
        self.assertTrue(all(t.origin == run_tests.WORKSPACE for t in data["tests"]))

    def test_the_pipeline_template_is_toml_and_complete(self):
        pipeline = ci.load_toml(ci.TEMPLATE_DIR / "workspace-pipeline.toml")
        self.assertEqual(pipeline["schema"], 1)
        for section in ("run", "allowed_skips", "presets", "artifacts"):
            self.assertIn(section, pipeline)

    def test_writing_a_template_into_a_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(ci.main(["template", "pipeline", "--out", tmp]), 0)
            written = Path(tmp) / "ci" / "pipeline.toml"
            self.assertTrue(written.is_file())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(ci.main(["template", "pipeline", "--out", tmp]), 2)   # never silently overwritten
                self.assertEqual(ci.main(["template", "pipeline", "--out", tmp, "--force"]), 0)

    def test_listing_the_templates(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(ci.main(["template"]), 0)
        for name in ci.TEMPLATES:
            self.assertIn(name, buffer.getvalue())


class StaticAnalysisStage(unittest.TestCase):
    """PLAT-74: the verdict of the static analysis stage - new findings red, missing tools never green."""

    RESULT = {"tool_versions": {"cppcheck": "2.17.1", "clang-tidy": "22.1.8"}, "skipped_tools": {},
              "scope": {"units": {"own": 55, "copy": 38}},
              "inventory": {"own_by_verdict": {"baseline": 3, "deviated": 2, "notice": 1}, "copy_total": 4},
              "new": [], "resolved": [], "deviations": [], "defects": [{"id": "BEF-001"}], "messages": [],
              "exit_code": 0}

    def judge(self, static, strict=False, pipeline=None):
        report = ci.Report()
        pipeline = pipeline or {"static_analysis": {"enabled": True, "enforce": True, "strict_tools": True}}
        ci.judge_static_analysis(static, pipeline, argparse.Namespace(strict=strict), report)
        return report

    def test_clean_run_is_green(self):
        self.assertEqual(self.judge(self.RESULT).findings, [])

    def test_new_finding_is_red(self):
        static = {**self.RESULT, "new": [{"file": "libs/x.c", "line": 3, "rule": "misra-c2012-15.5"}],
                  "exit_code": 1}
        report = self.judge(static)
        self.assertTrue(report.failed)
        self.assertIn("libs/x.c:3 misra-c2012-15.5", report.findings[0].text)
        relaxed = self.judge(static, pipeline={"static_analysis": {"enforce": False}})
        self.assertFalse(relaxed.failed)

    def test_missing_tool_is_skip_or_red_never_green(self):
        static = {**self.RESULT, "skipped_tools": {"cppcheck": "nicht gefunden"}}
        self.assertEqual([f.status for f in self.judge(static).findings], [ci.SKIP])
        self.assertTrue(self.judge(static, strict=True).failed)

    def test_missing_compile_database_and_crash(self):
        self.assertEqual(self.judge({"error": "Compile-Datenbank fehlt"}).findings[0].status, ci.WARN)
        self.assertTrue(self.judge({"error": "Compile-Datenbank fehlt"}, strict=True).failed)
        self.assertTrue(self.judge(None).failed)

    def test_pipeline_enforces_and_summary_shows_it(self):
        settings = ci.load_toml(ci.PIPELINE_FILE)["static_analysis"]
        self.assertTrue(settings["enabled"] and settings["enforce"] and settings["strict_tools"])
        payload = {"verdict": "pass", "platform": "P", "toolchain": "pinned", "strict": True, "duration_s": 1.0,
                   "stages": [], "findings": [], "static_analysis": self.RESULT}
        text = ci.render_summary(payload)
        self.assertIn("Statische Analyse (PLAT-74, clang-tidy 22.1.8, cppcheck 2.17.1)", text)
        self.assertIn("neu 0, Baseline 3", text)

    def test_python_packages_include_the_website_packages(self):
        """TOOL-232: `setup --python-packages` installs tools/requirements-site.txt (DOC-71) as well - otherwise
        docgen-site is skipped, and under `run --strict` a skipped entry is red. Also with --core-only."""
        import contextlib
        import io
        import unittest.mock as mock

        site = str(ci.SITE_REQUIREMENTS)
        self.assertEqual(ci.SITE_REQUIREMENTS, REPO / "tools" / "requirements-site.txt")
        self.assertTrue(ci.SITE_REQUIREMENTS.is_file())
        for core_only in (False, True):
            with self.subTest(core_only=core_only):
                files = ci.python_package_files(core_only)
                self.assertIn(site, files)
                self.assertEqual(files[files.index(site) - 1], "-r")
                self.assertEqual(str(ci.STUDIO_REQUIREMENTS) in files, not core_only)
                calls = []
                args = ci.build_parser().parse_args(["setup", "--python-packages", "--tool", "cmake"]
                                                    + (["--core-only"] if core_only else []))
                with mock.patch.object(ci, "install"), mock.patch.object(ci, "run_logged", calls.append), \
                        contextlib.redirect_stdout(io.StringIO()):
                    ci.cmd_setup(args)
                install = [c for c in calls if "-c" in c and str(ci.CONSTRAINTS_FILE) in c]
                self.assertEqual(len(install), 1, calls)
                self.assertIn(site, install[0])
        # every website package is pinned exactly and none contradicts a pin of constraints.txt (one pip call)
        pins = ci.read_constraints()
        lines = [ln.split("#", 1)[0].strip() for ln in ci.SITE_REQUIREMENTS.read_text(encoding="utf-8").splitlines()]
        lines = [ln for ln in lines if ln]
        self.assertTrue(lines)
        for line in lines:
            self.assertRegex(line, r"^[A-Za-z0-9_.\-]+==[^=\s]+$", line)
            name, version = line.split("==")
            norm = {re.sub(r"[-_.]+", "-", k).lower(): v for k, v in pins.items()}
            key = re.sub(r"[-_.]+", "-", name).lower()
            if key in norm:
                self.assertEqual(norm[key], version, f"{name}: requirements-site.txt and constraints.txt differ")

    def test_setup_installs_the_pinned_wheels_into_the_cache(self):
        self.assertEqual(ci.misra_venv(Path("c")), Path("c") / "misra-venv")
        pins = ci.MISRA_REQUIREMENTS.read_text(encoding="utf-8")
        self.assertRegex(pins, r"(?m)^cppcheck==\d")
        self.assertRegex(pins, r"(?m)^clang-tidy==\d")


class WorkflowHull(unittest.TestCase):
    """The GitHub workflow only calls tools/ci; the logic stays provider neutral."""

    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_calls_only_tools_ci(self):
        import yaml

        workflow = yaml.safe_load(self.text)
        runs = [step["run"] for job in workflow["jobs"].values() for step in job["steps"] if "run" in step]
        self.assertTrue(runs)
        for command in runs:
            self.assertRegex(command.strip(), r"^python -m tools\.ci ", command)
        self.assertIn("python -m tools.ci run --strict", self.text)

    def test_versions_come_from_tools_ci(self):
        self.assertIn("python-version-file: tools/ci/.python-version", self.text)
        self.assertIn("hashFiles('tools/ci/versions.toml')", self.text)
        for tool in ("arm-none-eabi", "cmake-", "ninja-"):
            self.assertNotIn(tool, self.text.replace("tools/ci", ""), "tool versions belong into versions.toml")

    def test_triggers_do_not_waste_minutes(self):
        import yaml

        workflow = yaml.safe_load(self.text)
        triggers = workflow[True] if True in workflow else workflow["on"]  # PyYAML reads `on` as boolean
        # user decision 2026-09-17 (TOOL-22): pull requests on dev/master plus manual runs, never every push
        self.assertIn(sorted(triggers), (["workflow_dispatch"], ["pull_request", "workflow_dispatch"]))
        if "pull_request" in triggers:
            pull_request = triggers["pull_request"]
            self.assertEqual(pull_request["branches"], ["dev", "master"])
            # planning and changelog commits alone must not start a run
            for path in ("CHANGELOG.md", "doc/changelog.d/**", "doc/TODO.md", "doc/todo.yaml", "doc/architecture/**"):
                self.assertIn(path, pull_request.get("paths-ignore", []))
        self.assertTrue(workflow["concurrency"]["cancel-in-progress"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})


class ReleaseVersion(unittest.TestCase):
    """ARCH-55/SYS-68: VERSION == release tag vX.Y.Z when the commit carries one, else only a note."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxci-version-"))
        (self.tmp / "VERSION").write_text("1.4.2\n", encoding="utf-8")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, tags=None) -> ci.Report:
        report = ci.Report()
        ci.check_release_version(self.tmp, report, tags)
        return report

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                        "-c", "tag.gpgsign=false", "-C", str(self.tmp), *args], check=True, capture_output=True)

    def test_repository_version_is_valid(self):
        report = ci.Report()
        ci.check_release_version(ci.REPO, report)
        self.assertFalse(report.failed, [f.text for f in report.findings])
        self.assertEqual([f.area for f in report.findings], ["Version"])

    def test_matching_tag_is_green(self):
        report = self.check(["v1.4.2"])
        self.assertEqual([f.status for f in report.findings], [ci.OK])
        self.assertIn("v1.4.2", report.findings[0].text)

    def test_other_tag_is_red_and_named(self):
        report = self.check(["v1.4.2", "v1.5.0"])
        self.assertTrue(report.failed)
        self.assertIn("v1.5.0", report.findings[0].text)
        self.assertIn("1.4.2", report.findings[0].text)

    def test_without_tag_only_a_note(self):
        report = self.check([])
        self.assertFalse(report.failed)
        self.assertIn("kein Release-Tag", report.findings[0].text)

    def test_malformed_version_is_red(self):
        for text in ("1.4\n", "v1.4.2\n", "01.4.2\n", "1.4.2-rc.1\n", "1.4.2\n1.4.3\n", ""):
            with self.subTest(text=text):
                (self.tmp / "VERSION").write_text(text, encoding="utf-8")
                self.assertTrue(self.check([]).failed)
        (self.tmp / "VERSION").unlink()
        self.assertTrue(self.check([]).failed)

    def test_tags_from_git(self):
        """The real query: only release tags vX.Y.Z on HEAD count, other tags are ignored."""
        self.git("init", "-q")
        self.git("add", "VERSION")
        self.git("commit", "-q", "-m", "x")
        self.assertIn("kein Release-Tag", self.check().findings[0].text)
        self.git("tag", "-a", "-m", "not a release", "board-test-1")
        self.assertIn("kein Release-Tag", self.check().findings[0].text)
        self.git("tag", "-a", "-m", "release", "v1.4.2")
        self.assertEqual([f.status for f in self.check().findings], [ci.OK])
        self.git("tag", "-a", "-m", "wrong", "v1.4.3")
        self.assertTrue(self.check().failed)

    def test_release_candidate_of_version_is_green(self):
        """TOOL-232: v1.4.2-rcN on VERSION 1.4.2 is a release tag; the release itself wins if both are there."""
        for tags, named in ((["v1.4.2-rc1"], "v1.4.2-rc1"), (["v1.4.2-rc1", "v1.4.2-rc2"], "v1.4.2-rc2"),
                            (["v1.4.2-rc1", "v1.4.2"], "v1.4.2"), (["board-test-1", "v1.4.2-rc12"], "v1.4.2-rc12")):
            with self.subTest(tags=tags):
                report = self.check(tags)
                self.assertEqual([f.status for f in report.findings], [ci.OK], report.findings[0].text)
                self.assertIn(f"Release-Tag {named}", report.findings[0].text)
        self.assertIn("Release-Kandidat", self.check(["v1.4.2-rc1"]).findings[0].text)

    def test_wrong_release_candidates_are_red(self):
        """Counter-checks: another spelling or another version base is no release tag of this tree."""
        for tags in (["v1.4.2-rc"], ["v1.4.2-beta1"], ["v1.4.2-rc0"], ["v1.4.2-rc01"], ["v1.4.2-rc.1"],
                     ["v1.4.3-rc1"], ["v1.4.1-rc1"], ["v1.4.2", "v1.4.3-rc1"]):
            with self.subTest(tags=tags):
                report = self.check(tags)
                self.assertTrue(report.failed, report.findings[0].text)
                self.assertIn(next(t for t in tags if t != "v1.4.2"), report.findings[0].text)

    def test_the_tag_a_release_run_checks(self):
        self.assertEqual(ci.release_tag_of_tree(self.tmp, "1.4.2", []), "v1.4.2")
        self.assertEqual(ci.release_tag_of_tree(self.tmp, "1.4.2", ["v1.4.2-rc1"]), "v1.4.2-rc1")
        self.assertEqual(ci.release_tag_of_tree(self.tmp, "1.4.2", ["v1.4.2-rc1", "v1.4.2"]), "v1.4.2")
        for other in (["v1.4.3-rc1"], ["v1.4.2-rc"], ["v1.4.2-beta1"]):
            self.assertEqual(ci.release_tag_of_tree(self.tmp, "1.4.2", other), "v1.4.2")

class ReleaseStrictness(unittest.TestCase):
    """TOOL-65 (gap N10): `--release` is the switch between daily work and a handed-over image.

    The tree and the workspace are synthetic and lie in %TEMP%: an invented neXtBSW tree (a `VERSION` file and a
    `tools/nxconfig` folder) in its own git repository, pinned by an invented customer workspace. Nothing here
    looks at this repository, so the verdict does not depend on the state it happens to be in.
    """

    VERSION = "3.1.4"
    TAG = "v3.1.4"
    IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxci-release-")).resolve()
        self.ws = self.tmp / "omega"
        self.tree = self.ws / "nextbsw"
        (self.tree / "tools" / "nxconfig").mkdir(parents=True)
        (self.tree / "VERSION").write_text(self.VERSION + "\n", encoding="utf-8")
        (self.tree / "tools" / "nxconfig" / "marker.txt").write_text("synthetic\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("checkout", "-q", "-B", "master")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "synthetic release")
        (self.ws / "nextbsw.yaml").write_text(
            "# synthetic workspace of tools/ci/test_ci.py - test data, no real customer\n"
            "schema_version: 1\nnextbsw:\n"
            "  version: " + self.VERSION + "\n  source: submodule\n"
            "  path: nextbsw\n  profile: open\n", encoding="utf-8")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.tree), *self.IDENTITY, *args], check=True, capture_output=True)

    def report(self, release: bool) -> ci.Report:
        """The pin stage of a workspace run. Only the provenance half is looked at here: `resolve_target` hands
        the identity half the tree this test runs from, which is never the synthetic one."""
        report = ci.Report()
        args = argparse.Namespace(workspace=self.ws, tests=REPO / "tools" / "test" / "tests.yaml",
                                  out=None, build_root=None)
        ci.check_pin(ci.resolve_target(args), report, release=release)
        return report

    def texts(self, report: ci.Report, status: str) -> list[str]:
        return [f.text for f in report.findings
                if f.status == status and f.area == "Pin" and provenance_finding(f.text)]

    def test_the_flag_exists_on_run_and_check(self):
        for command in ("run", "check"):
            self.assertTrue(ci.build_parser().parse_args([command, "--release"]).release, command)
            self.assertFalse(ci.build_parser().parse_args([command]).release, command)

    def test_a_development_pin_only_warns_without_release(self):
        report = self.report(release=False)
        self.assertTrue(any("Entwicklungsstand" in t for t in self.texts(report, ci.WARN)),
                        [f.text for f in report.findings])
        self.assertEqual(self.texts(report, ci.FAIL), [])

    def test_a_development_pin_fails_a_release_run(self):
        report = self.report(release=True)
        self.assertTrue(report.failed)
        self.assertTrue(any("Entwicklungsstand" in t for t in self.texts(report, ci.FAIL)),
                        [f.text for f in report.findings])
        self.assertEqual(self.texts(report, ci.WARN), [])

    def test_a_dirty_tree_is_named_in_every_run_and_fails_only_a_release(self):
        """The finding is the same sentence either way; only `--release` lets it stop the pipeline."""
        self.git("tag", "-a", self.TAG, "-m", "release")
        self.git("checkout", "-q", self.TAG)
        (self.tree / "VERSION").write_text(self.VERSION + "\n\n", encoding="utf-8")
        self.assertTrue(any("schmutzig" in t for t in self.texts(self.report(release=False), ci.WARN)))
        self.assertTrue(any("schmutzig" in t for t in self.texts(self.report(release=True), ci.FAIL)))

    def test_no_provenance_finding_can_stop_a_run_without_the_flag(self):
        """A workspace run builds with the tree it runs from, so a working copy in the middle of a change must
        not turn the pipeline red - the words are printed, the verdict waits for `--release`."""
        self.git("tag", self.TAG)                                   # lightweight, and the tree is left dirty
        (self.tree / "VERSION").write_text(self.VERSION + "\n\n", encoding="utf-8")
        report = self.report(release=False)
        self.assertEqual(self.texts(report, ci.FAIL), [])
        self.assertTrue(len(self.texts(report, ci.WARN)) >= 2, [f.text for f in report.findings])
        self.assertTrue(len(self.texts(self.report(release=True), ci.FAIL)) >= 2)

    def test_a_released_pin_is_green_in_a_release_run(self):
        self.git("tag", "-a", self.TAG, "-m", "release")
        self.git("checkout", "-q", self.TAG)
        report = self.report(release=True)
        self.assertEqual(self.texts(report, ci.FAIL), [])
        self.assertEqual(self.texts(report, ci.WARN), [])
        self.assertTrue(any(self.TAG in t for t in self.texts(report, ci.OK)))

    def test_a_lightweight_tag_fails_a_release_run(self):
        self.git("tag", self.TAG)
        self.git("checkout", "-q", self.TAG)
        self.assertTrue(any("annotierten Tag" in t for t in self.texts(self.report(release=True), ci.FAIL)))

    def test_every_finding_names_file_line_and_yaml_path(self):
        for text in self.texts(self.report(release=True), ci.FAIL):
            self.assertIn("nextbsw.yaml:", text)
            self.assertRegex(text, r"\(nextbsw\.(version|path)\)")

    def test_the_tree_itself_is_only_judged_with_release(self):
        """`check_release_tree` is the same rule for a run without --workspace - silent without the flag."""
        report = ci.Report()
        ci.check_release_tree(self.tree, report, release=False)
        self.assertEqual(report.findings, [])
        ci.check_release_tree(self.tree, report, release=True)
        self.assertTrue(report.failed)
        self.assertEqual([f.area for f in report.findings], ["Release"])

    def tree_release(self) -> ci.Report:
        report = ci.Report()
        ci.check_release_tree(self.tree, report, release=True)
        return report

    def test_a_release_candidate_passes_the_release_run_of_the_tree(self):
        """TOOL-232 (cm-plan step 7 for Release R1): VERSION 3.1.4 on the annotated tag v3.1.4-rc1 is a release."""
        self.git("tag", "-a", "v3.1.4-rc1", "-m", "release candidate")
        self.git("checkout", "-q", "v3.1.4-rc1")
        report = self.tree_release()
        self.assertFalse(report.failed, [f.text for f in report.findings])
        self.assertEqual([f.status for f in report.findings], [ci.OK])
        self.assertIn("v3.1.4-rc1", report.findings[0].text)
        version = ci.Report()
        ci.check_release_version(self.tree, version)
        self.assertEqual([f.status for f in version.findings], [ci.OK], version.findings[0].text)

    def test_a_release_candidate_obeys_the_same_rule(self):
        """Counter-checks: lightweight, not on master, dirty - the candidate is judged like v<VERSION>."""
        self.git("tag", "v3.1.4-rc1")                                         # lightweight
        self.git("checkout", "-q", "v3.1.4-rc1")
        self.assertTrue(any("v3.1.4-rc1" in f.text and "annotierten Tag" in f.text
                            for f in self.tree_release().findings if f.status == ci.FAIL))
        self.git("checkout", "-q", "-b", "side")
        (self.tree / "side.txt").write_text("x\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "side branch")
        self.git("tag", "-a", "v3.1.4-rc2", "-m", "candidate on a side branch")
        self.assertTrue(self.tree_release().failed)                            # not reachable from master
        (self.tree / "side.txt").write_text("y\n", encoding="utf-8")
        self.assertTrue(any("schmutzig" in f.text for f in self.tree_release().findings if f.status == ci.FAIL))

    def test_wrong_candidate_tags_fail_the_release_run(self):
        """Counter-checks: a malformed candidate or one of another version is no release tag of this tree."""
        for tag in ("v3.1.4-rc", "v3.1.4-beta1", "v3.1.5-rc1"):
            with self.subTest(tag=tag):
                self.git("tag", "-a", tag, "-m", "not a release of 3.1.4")
                report = self.tree_release()
                self.assertTrue(report.failed, [f.text for f in report.findings])
                self.assertTrue(any("Entwicklungsstand" in f.text for f in report.findings if f.status == ci.FAIL))
                version = ci.Report()
                ci.check_release_version(self.tree, version)
                self.assertTrue(version.failed, version.findings[0].text)
                self.git("tag", "-d", tag)

    def test_the_rule_is_loaded_from_tools_nxconfig(self):
        """One rule, three callers (CI, Studio status line, Studio git page) - not a copy per tool."""
        module = ci.pincheck_module()
        for name in ("check", "check_tree", "check_workspace", "ERROR", "WARNING", "OK"):
            self.assertTrue(hasattr(module, name), name)
        self.assertIs(module, ci.pincheck_module())


class ReportProvenance(unittest.TestCase):
    """SYS-67 (cm-plan.md gap G1): the report names the commit, tree hash and cleanliness of the state it judged,
    and says when that state moved during the run. Synthetic git repository in %TEMP%, never this one."""

    IDENTITY = ReleaseStrictness.IDENTITY

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxci-provenance-")).resolve()
        (self.tmp / "VERSION").write_text("2.7.1\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "synthetic")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.tmp), *self.IDENTITY, *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def test_clean_checkout_names_commit_tree_and_version(self):
        p = ci.provenance(self.tmp)
        self.assertEqual(self.git("rev-parse", "HEAD"), p["commit"])
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), p["tree"])
        self.assertRegex(p["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual("2.7.1", p["version"])
        self.assertIs(False, p["dirty"])
        self.assertIsNone(p["openbsw"])                  # no submodule in the synthetic tree
        self.assertNotIn("dirty", p["describe"])

    def test_changes_make_it_dirty_and_are_named(self):
        (self.tmp / "VERSION").write_text("2.7.2\n", encoding="utf-8")
        (self.tmp / "new.txt").write_text("x\n", encoding="utf-8")
        p = ci.provenance(self.tmp)
        self.assertIs(True, p["dirty"])
        self.assertEqual(2, p["changes_total"])
        self.assertTrue(p["describe"].endswith("-dirty"), p["describe"])

    def test_ignored_files_do_not_count(self):
        """build/ lies in the tree but is ignored - it must not make every report unbindable."""
        (self.tmp / ".gitignore").write_text("build/\n", encoding="utf-8")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "ignore build")
        (self.tmp / "build").mkdir()
        (self.tmp / "build" / "out.o").write_text("x\n", encoding="utf-8")
        self.assertIs(False, ci.provenance(self.tmp)["dirty"])

    def test_not_a_checkout(self):
        with tempfile.TemporaryDirectory() as plain:
            p = ci.provenance(Path(plain))
        self.assertIsNone(p["commit"])
        self.assertIsNone(p["dirty"])
        report = ci.Report()
        ci.judge_provenance(p, p, True, report)
        self.assertEqual([ci.WARN], [f.status for f in report.findings])

    def test_unchanged_clean_run_has_no_finding(self):
        p = ci.provenance(self.tmp)
        report = ci.Report()
        ci.judge_provenance(p, ci.provenance(self.tmp), True, report)
        self.assertEqual([], report.findings)

    def test_moved_head_is_red_only_when_strict(self):
        start = ci.provenance(self.tmp)
        (self.tmp / "VERSION").write_text("2.7.2\n", encoding="utf-8")
        self.git("commit", "-q", "-am", "moved during the run")
        end = ci.provenance(self.tmp)
        strict, loose = ci.Report(), ci.Report()
        ci.judge_provenance(start, end, True, strict)
        ci.judge_provenance(start, end, False, loose)
        self.assertTrue(strict.failed)
        self.assertIn("bewegt", strict.findings[0].text)
        self.assertFalse(loose.failed)
        self.assertEqual([ci.WARN], [f.status for f in loose.findings])

    def test_dirty_start_and_new_changes_are_warnings(self):
        (self.tmp / "a.txt").write_text("x\n", encoding="utf-8")
        start = ci.provenance(self.tmp)
        (self.tmp / "b.txt").write_text("leak\n", encoding="utf-8")
        report = ci.Report()
        ci.judge_provenance(start, ci.provenance(self.tmp), True, report)
        self.assertFalse(report.failed)
        texts = [f.text for f in report.findings]
        self.assertTrue(any("beim Start" in t for t in texts), texts)
        self.assertTrue(any("b.txt" in t for t in texts), texts)

    def test_summary_names_the_state(self):
        payload = {"verdict": "pass", "platform": "P", "toolchain": "pinned", "strict": True, "slow": True,
                   "duration_s": 3.0, "stages": [], "findings": [], "provenance": ci.provenance(self.tmp)}
        text = ci.render_summary(payload)
        self.assertIn(self.git("rev-parse", "HEAD"), text)
        self.assertIn(self.git("rev-parse", "HEAD^{tree}"), text)
        self.assertIn("sauber", text)
        self.assertIn("--slow", text)
        payload["provenance"] = ci.provenance(Path(tempfile.gettempdir()) / "nx-no-such-dir")
        self.assertIn("an keinen Commit gebunden", ci.render_summary(payload))


if __name__ == "__main__":
    unittest.main()
