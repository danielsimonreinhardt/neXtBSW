"""Tests of the pre-flight before the release CI (TOOL-209, verification-plan.md 8.2, T-VP3).

Run: python -m unittest tools/ci/test_preflight.py

The whole sequence PF-1 .. PF-5 is driven with an invented runner that answers like the real commands (result
files of tools.test, tools.misra, coverage.py): green run, a red test, a step timeout, a test timeout that is
repeated once, a new static-analysis finding, a failed coverage measurement, --only/--skip/--fail-fast, the report
files, the baseline tag choice and the command line. No real tool is started.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from tools.ci import ci, preflight  # noqa: E402

CLEAN = {"commit": "a" * 40, "tree": "b" * 40, "describe": "v0.2.0-5-gaaaaaaa", "version": "0.2.0", "dirty": False,
         "changes": [], "changes_total": 0}


def test_entry(name: str, status: str = "pass", reason: str = "", group: str = "python") -> dict:
    return {"name": name, "group": group, "status": status, "reason": reason, "duration_s": 1.0}


def tests_json(*entries: dict) -> dict:
    count = lambda s: sum(e["status"] == s for e in entries)  # noqa: E731
    return {"summary": {"pass": count("pass"), "fail": count("fail"), "timeout": count("timeout"),
                        "skip": count("skip"), "total": len(entries)},
            "duration_s": 3.0, "tests": list(entries), "unlisted_test_files": []}


class FakeRunner:
    """Answers like the commands of the plan; `plan` maps a key to (exit code, timed_out) or to a payload."""

    def __init__(self, work: Path, increment=None, target=None, safety=None, misra=None, coverage=None,
                 timeouts=(), codes=None, retries=None) -> None:
        self.work = work
        self.calls: list[list[str]] = []
        self.increment = increment or tests_json(test_entry("a"))
        self.target = target or tests_json(test_entry("target-demo", group="target"))
        self.safety = safety or tests_json(test_entry("safety-gate"))
        self.misra = misra if misra is not None else {"new": [], "skipped_tools": {}}
        self.coverage = coverage if coverage is not None else {"entries": {"items": []}, "modules": []}
        self.timeouts = set(timeouts)
        self.codes = codes or {}
        self.retries = retries or {}

    @staticmethod
    def after(argv: list[str], flag: str) -> str:
        return argv[argv.index(flag) + 1]

    def __call__(self, argv, env, cwd, timeout, log_path):
        self.calls.append(argv)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("log\n", encoding="utf-8")
        key = self.key(argv)
        if key in self.timeouts:
            return 1, True
        payload, code = None, self.codes.get(key, 0)
        if key == "list":
            payload = {"tests": [{"name": "a"}], "since": {"files_without_entry": ["x.py"]}}
        elif key == "increment":
            payload = self.increment
        elif key == "target-demo":
            payload = self.target
            (self.work / "build" / "demo").mkdir(parents=True, exist_ok=True)
            (self.work / "build" / "demo" / "compile_commands.json").write_text("[]", encoding="utf-8")
        elif key == "safety":
            payload = self.safety
        elif key.startswith("retry:"):
            payload = self.retries.get(key[6:], tests_json(test_entry(key[6:])))
        if payload is not None and "--json" in argv:
            Path(self.after(argv, "--json")).write_text(json.dumps(payload), encoding="utf-8")
        if key == "misra":
            out = Path(self.after(argv, "--out"))
            out.mkdir(parents=True, exist_ok=True)
            (out / "result.json").write_text(json.dumps(self.misra), encoding="utf-8")
        if key == "coverage":
            Path(self.after(argv, "--json")).write_text(json.dumps(self.coverage), encoding="utf-8")
        return code, False

    @staticmethod
    def key(argv: list[str]) -> str:
        if "check" in argv and "tools.ci" in argv:
            return "check"
        if "tools.misra" in argv:
            return "misra"
        if any(a.endswith("coverage.py") for a in argv):
            return "coverage"
        if "--list" in argv:
            return "list"
        if "-k" in argv:
            pattern = argv[argv.index("-k") + 1]
            if pattern == "target-demo":
                return "target-demo"
            if pattern == "safety":
                return "safety"
            return "retry:" + pattern.replace("[", "").replace("]", "", 1)
        return "increment"


class PreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.repo = base / "tree"
        self.repo.mkdir()
        self.work = base / "work"

    def context(self, **kw) -> preflight.Context:
        values = dict(repo=self.repo, work=self.work, since="v0.2.0", jobs=2, env={}, python="python")
        values.update(kw)
        return preflight.Context(**values)

    def run_flight(self, runner: FakeRunner | None = None, provenance=lambda _r: CLEAN, **kw) -> preflight.Preflight:
        runner = runner or FakeRunner(self.work)
        flight = preflight.Preflight(self.context(**kw), runner=runner, provenance=provenance)
        flight.execute()
        flight.runner = runner
        return flight

    def step(self, flight: preflight.Preflight, step_id: str) -> preflight.Step:
        return next(s for s in flight.steps if s.id == step_id)

    # ------------------------------------------------------------------ the green path and the plan

    def test_green_run_follows_the_plan(self) -> None:
        flight = self.run_flight()
        self.assertTrue(flight.green, [s.findings for s in flight.steps])
        self.assertEqual([s.id for s in flight.steps], list(preflight.PF_IDS))
        self.assertTrue(all(s.status == preflight.OK for s in flight.steps))
        self.assertTrue(any("ohne Eintrag" in n for n in self.step(flight, "PF-3").notes))
        joined = [" ".join(c) for c in flight.runner.calls]
        order = [next(i for i, c in enumerate(joined) if marker in c) for marker in (
            "tools.ci check --strict", "--since v0.2.0 --list", "--since v0.2.0 --jobs 2", "-k target-demo",
            "tools.misra run --compile-db", "coverage.py", "-k safety")]
        self.assertEqual(order, sorted(order), "PF-2 .. PF-5 in the order of verification-plan.md 8.2")

    def test_slow_and_clean_reach_the_increment_only(self) -> None:
        flight = self.run_flight(slow=True, clean=True)
        run = next(c for c in flight.runner.calls if "--since" in c and "--list" not in c)
        self.assertIn("--slow", run)
        self.assertIn("--clean", run)
        for call in flight.runner.calls:
            if "--since" not in call:
                self.assertNotIn("--slow", call)

    def test_report_files(self) -> None:
        flight = self.run_flight()
        json_path, md_path = flight.write_report()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(data["verdict"], "gruen")
        self.assertEqual([s["id"] for s in data["steps"]], list(preflight.PF_IDS))
        self.assertEqual(data["candidate"]["commit"], CLEAN["commit"])
        text = md_path.read_text(encoding="utf-8")
        self.assertIn("GRUEN", text)
        self.assertIn("PF-4", text)

    # ------------------------------------------------------------------ PF-1

    def test_dirty_tree_is_red_unless_allowed(self) -> None:
        dirty = {**CLEAN, "dirty": True, "changes": [" M tools/ci/ci.py"], "changes_total": 1}
        flight = self.run_flight(provenance=lambda _r: dirty)
        self.assertEqual(self.step(flight, "PF-1").status, preflight.FAIL)
        self.assertIn("nicht sauber", self.step(flight, "PF-1").findings[0])
        self.assertFalse(flight.green)
        allowed = self.run_flight(provenance=lambda _r: dirty, allow_dirty=True)
        self.assertEqual(self.step(allowed, "PF-1").status, preflight.OK)
        self.assertTrue(any("allow-dirty" in n for n in self.step(allowed, "PF-1").notes))

    def test_work_dir_inside_the_tree_is_red(self) -> None:
        flight = self.run_flight(work=self.repo / "build" / "pf")
        findings = " ".join(self.step(flight, "PF-1").findings)
        self.assertIn("Bau-Wurzel", findings)
        self.assertIn("Berichtsordner", findings)

    def test_build_folder_in_the_tree_is_red(self) -> None:
        (self.repo / "build").mkdir()
        flight = self.run_flight()
        self.assertIn("build", " ".join(self.step(flight, "PF-1").findings))

    def test_wrong_commit_and_no_checkout(self) -> None:
        flight = self.run_flight(commit="deadbeef")
        self.assertIn("erwartet deadbeef", self.step(flight, "PF-1").findings[0])
        flight = self.run_flight(provenance=lambda _r: {"commit": None})
        self.assertIn("kein git-Checkout", self.step(flight, "PF-1").findings[0])

    # ------------------------------------------------------------------ PF-2 .. PF-5

    def test_check_failure(self) -> None:
        flight = self.run_flight(FakeRunner(self.work, codes={"check": 1}))
        self.assertEqual(self.step(flight, "PF-2").status, preflight.FAIL)
        self.assertIn("check --strict", self.step(flight, "PF-2").findings[0])

    def test_red_test_in_increment(self) -> None:
        runner = FakeRunner(self.work, increment=tests_json(test_entry("calgen", "fail", "Exit 1"),
                                                            test_entry("ok")), codes={"increment": 1})
        flight = self.run_flight(runner)
        step = self.step(flight, "PF-3")
        self.assertEqual(step.status, preflight.FAIL)
        self.assertTrue(any(f.startswith("calgen (python)") for f in step.findings))
        self.assertFalse(flight.green)

    def test_missing_baseline_tag(self) -> None:
        flight = self.run_flight(since=None)
        self.assertIn("--since", self.step(flight, "PF-3").findings[0])

    def test_step_timeout_is_reported_and_kills_nothing_else(self) -> None:
        flight = self.run_flight(FakeRunner(self.work, timeouts={"increment"}))
        step = self.step(flight, "PF-3")
        self.assertEqual(step.status, preflight.FAIL)
        self.assertIn("Zeitueberschreitung", step.findings[0])
        self.assertTrue(step.commands[-1].timed_out)
        self.assertEqual(self.step(flight, "PF-4").status, preflight.OK, "later steps still run")

    def test_timeout_factor_scales_the_limit(self) -> None:
        seen = []

        def runner(argv, env, cwd, timeout, log):
            seen.append(timeout)
            return FakeRunner(self.work)(argv, env, cwd, timeout, log)

        flight = preflight.Preflight(self.context(timeout_factor=2.0), runner=runner, provenance=lambda _r: CLEAN)
        flight.execute()
        self.assertIn(preflight.STEP_TIMEOUT["check"] * 2.0, seen)
        self.assertIn(preflight.STEP_TIMEOUT["increment"] * 2.0, seen)

    def test_test_timeout_repeated_once_green(self) -> None:
        runner = FakeRunner(self.work, increment=tests_json(test_entry("logcheck", "timeout", "Zeit"),
                                                            test_entry("x")), codes={"increment": 1})
        flight = self.run_flight(runner)
        step = self.step(flight, "PF-3")
        self.assertEqual(step.status, preflight.OK, step.findings)
        self.assertTrue(any("einzeln wiederholt gruen" in n for n in step.notes))
        repeated = [c for c in runner.calls if "-k" in c and "[l]ogcheck" in c]
        self.assertEqual(len(repeated), 1, "exact-name pattern, once")

    def test_test_timeout_repeated_still_red(self) -> None:
        runner = FakeRunner(self.work, increment=tests_json(test_entry("logcheck", "timeout", "Zeit")),
                            codes={"increment": 1},
                            retries={"logcheck": tests_json(test_entry("logcheck", "timeout", "wieder Zeit"))})
        flight = self.run_flight(runner)
        step = self.step(flight, "PF-3")
        self.assertEqual(step.status, preflight.FAIL)
        self.assertTrue(any("auch einzeln wiederholt" in f for f in step.findings))

    def test_other_red_entry_stays_red_next_to_a_repeated_timeout(self) -> None:
        runner = FakeRunner(self.work, increment=tests_json(test_entry("logcheck", "timeout", "Zeit"),
                                                            test_entry("mcudb", "fail", "kaputt")),
                            codes={"increment": 1})
        step = self.step(self.run_flight(runner), "PF-3")
        self.assertEqual(step.status, preflight.FAIL)
        self.assertTrue(any(f.startswith("mcudb") for f in step.findings))

    def test_new_static_findings_are_red(self) -> None:
        misra = {"new": [{"file": "build/x.c", "line": 3, "rule": "R1"}], "skipped_tools": {}}
        step = self.step(self.run_flight(FakeRunner(self.work, misra=misra)), "PF-4")
        self.assertEqual(step.status, preflight.FAIL)
        self.assertIn("build/x.c:3 R1", " ".join(step.findings))

    def test_skipped_static_tool_is_red(self) -> None:
        misra = {"new": [], "skipped_tools": {"cppcheck": "nicht installiert"}}
        step = self.step(self.run_flight(FakeRunner(self.work, misra=misra)), "PF-4")
        self.assertIn("cppcheck", " ".join(step.findings))

    def test_failed_target_build_skips_static_analysis_with_reason(self) -> None:
        runner = FakeRunner(self.work, target=tests_json(test_entry("target-demo", "fail", "Warnung", "target")),
                            codes={"target-demo": 1})
        # no compile database is written when the build is red
        original = runner.__call__

        def no_db(argv, env, cwd, timeout, log):
            code = original(argv, env, cwd, timeout, log)
            db = self.work / "build" / "demo" / "compile_commands.json"
            if runner.key(argv) == "target-demo":
                db.unlink()
            return code

        flight = preflight.Preflight(self.context(), runner=no_db, provenance=lambda _r: CLEAN)
        flight.execute()
        step = self.step(flight, "PF-4")
        text = " ".join(step.findings)
        self.assertIn("Compile-Datenbank fehlt", text)
        self.assertFalse(any("tools.misra" in " ".join(c.argv) for c in step.commands))

    def test_coverage_without_result_is_red_but_threshold_is_a_note(self) -> None:
        runner = FakeRunner(self.work, coverage={"entries": {"items": []},
                                                 "modules": [{"module": "m", "verdict": "unter Schwelle"}]})
        step = self.step(self.run_flight(runner), "PF-4")
        self.assertEqual(step.status, preflight.OK)
        self.assertTrue(any("unter Schwelle" in n for n in step.notes))
        step = self.step(self.run_flight(FakeRunner(self.work, coverage={"entries": {"items": [
            {"name": "e", "status": "fail", "reason": "Messung gescheitert\nmehr"}]}, "modules": []})), "PF-4")
        self.assertEqual(step.status, preflight.FAIL)
        self.assertIn("Abdeckung e: Messung gescheitert", step.findings[0])

    def test_safety_step_red(self) -> None:
        runner = FakeRunner(self.work, safety=tests_json(test_entry("safety-trace", "fail", "Stand")),
                            codes={"safety": 1})
        self.assertEqual(self.step(self.run_flight(runner), "PF-5").status, preflight.FAIL)

    # ------------------------------------------------------------------ selection

    def test_only_and_skip(self) -> None:
        flight = self.run_flight(only=["pf-2"])
        self.assertEqual([s.status for s in flight.steps],
                         [preflight.SKIP, preflight.OK, preflight.SKIP, preflight.SKIP, preflight.SKIP])
        self.assertTrue(flight.green)
        flight = self.run_flight(skip=["PF-4", "pf-5"])
        self.assertEqual(self.step(flight, "PF-4").status, preflight.SKIP)
        self.assertEqual(self.step(flight, "PF-3").status, preflight.OK)

    def test_nothing_selected_is_not_green(self) -> None:
        self.assertFalse(self.run_flight(only=["pf-9"]).green)

    def test_fail_fast_stops_after_the_first_red_step(self) -> None:
        dirty = {**CLEAN, "dirty": True, "changes": ["x"], "changes_total": 1}
        flight = self.run_flight(provenance=lambda _r: dirty, fail_fast=True)
        self.assertEqual(self.step(flight, "PF-1").status, preflight.FAIL)
        self.assertEqual(self.step(flight, "PF-2").status, preflight.SKIP)
        self.assertEqual(flight.runner.calls, [])
        flight = self.run_flight(provenance=lambda _r: dirty)
        self.assertEqual(self.step(flight, "PF-2").status, preflight.OK, "without --fail-fast everything runs")

    def test_exception_in_a_step_is_a_red_step(self) -> None:
        def broken(argv, env, cwd, timeout, log):
            raise OSError("kein Python")

        flight = preflight.Preflight(self.context(), runner=broken, provenance=lambda _r: CLEAN)
        flight.execute()
        self.assertEqual(self.step(flight, "PF-2").status, preflight.FAIL)
        self.assertIn("kein Python", self.step(flight, "PF-2").findings[0])

    # ------------------------------------------------------------------ helpers and command line

    def test_previous_release_skips_the_tag_on_head(self) -> None:
        answers = {("tag", "--list"): "v0.3.0\nv0.2.0\nv0.1.0", ("tag", "--points-at"): "v0.3.0"}

        def git(*args):
            return answers[args[:2]]

        self.assertEqual(preflight.previous_release(self.repo, git), "v0.2.0")
        answers[("tag", "--points-at")] = ""
        self.assertEqual(preflight.previous_release(self.repo, git), "v0.3.0")
        answers[("tag", "--list")] = ""
        self.assertIsNone(preflight.previous_release(self.repo, git))

    def test_default_work_dir_is_outside_the_tree(self) -> None:
        work = preflight.default_work_dir(REPO, "abcdef1234")
        self.assertFalse(preflight.is_inside(work, REPO))
        self.assertTrue(work.name == "abcdef12")

    def test_real_runner_times_out_and_returns_exit_code(self) -> None:
        log = Path(self.tmp.name) / "r.log"
        code, timed = preflight.run_streaming([sys.executable, "-c", "print('hallo')"], {}, self.repo, 60, log)
        self.assertEqual((code, timed), (0, False))
        self.assertIn("hallo", log.read_text(encoding="utf-8"))
        code, timed = preflight.run_streaming([sys.executable, "-c", "import time; time.sleep(30)"], {}, self.repo,
                                              1, log)
        self.assertTrue(timed)
        self.assertIn("Zeitueberschreitung", log.read_text(encoding="utf-8"))

    def test_command_line(self) -> None:
        args = ci.build_parser().parse_args(["preflight", "--since", "v0.2.0", "--jobs", "3", "--slow",
                                             "--only", "pf-3", "--timeout-factor", "1.5", "--allow-dirty"])
        self.assertIs(args.func, ci.cmd_preflight)
        self.assertEqual((args.since, args.jobs, args.slow, args.only, args.timeout_factor, args.allow_dirty),
                         ("v0.2.0", 3, True, ["pf-3"], 1.5, True))


if __name__ == "__main__":
    unittest.main()
