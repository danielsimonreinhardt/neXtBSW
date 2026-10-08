"""Host tests of the workspace environment (TOOL-63, gap N8).

Run: python -m unittest tools/setup/test_setup.py

Nothing is downloaded and no package is installed: the tests drive the mechanics with a synthetic workspace and a
synthetic neXtBSW tree in %TEMP% (VERSION, requirements, constraints - not a board, not the repository) and with a
recorded `pip`. What is checked is the decision, not the installation:

  - the split of tools/requirements.txt: core without Qt, Studio packages in their own file, no overlap
  - first run creates, second run runs nothing at all (repeatable)
  - a changed requirements file of the tree makes the marker stale and is named
  - a broken, a foreign and an unmarked environment are named instead of being overwritten
  - the marker is written last, so an interrupted installation is never taken for a complete one

One test creates a real (empty) venv with the running interpreter - local, offline, no package.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from tools.setup import setup_env as se  # noqa: E402

CORE_PINS = {"jinja2": "3.1.6", "pyyaml": "6.0.3"}
STUDIO_PINS = {"pyside6": "6.11.2"}


class FakePip:
    """Records every command and plays the part `python -m venv` and `pip install` would play."""

    def __init__(self, installs: dict[str, str] | None = None, fail_on: str | None = None):
        self.calls: list[list[str]] = []
        self.installed = dict(installs or {})
        self.fail_on = fail_on

    def __call__(self, argv: list[str]) -> None:
        self.calls.append(list(argv))
        if self.fail_on and self.fail_on in " ".join(argv):
            raise se.SetupError("pip endete mit 1 (Test)")
        if "venv" in argv and argv[1] == "-m":
            target = se.venv_python(Path(argv[-1]))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")

    @property
    def pip_calls(self) -> list[list[str]]:
        return [c for c in self.calls if "pip" in c]


class FakeProbe:
    """Answers the two questions `inspect` asks another interpreter, without starting one."""

    def __init__(self, packages: dict[str, str], version: str = "3.12.10"):
        self.packages = dict(packages)
        self.version = version

    def __call__(self, argv: list[str]) -> str:
        code = argv[-1]
        if "importlib.metadata" in code:
            return json.dumps(self.packages)
        return self.version


class Synthetic(unittest.TestCase):
    """A synthetic workspace and a synthetic tree in %TEMP% - never the repository, never a board."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.ws = root / "acme_bcm"
        self.tree = self.ws / "nextbsw"
        (self.tree / "tools" / "ci").mkdir(parents=True)
        (self.tree / "tools" / "nxconfig").mkdir(parents=True)
        (self.tree / "VERSION").write_text("1.4.2\n", encoding="utf-8")
        self.requirements = self.tree / "tools" / "requirements.txt"
        self.requirements.write_text("jinja2~=3.1\nPyYAML~=6.0\n", encoding="utf-8")
        (self.tree / "tools" / "requirements-studio.txt").write_text("PySide6~=6.11\n", encoding="utf-8")
        (self.tree / "tools" / "ci" / "constraints.txt").write_text(
            "pip==25.0.1\nJinja2==3.1.6\nPyYAML==6.0.3\nPySide6==6.11.2\n", encoding="utf-8")
        (self.tree / "tools" / "ci" / ".python-version").write_text("3.12.10\n", encoding="utf-8")
        (self.ws / "nextbsw.yaml").write_text("nextbsw:\n  version: 1.4.2\n  path: nextbsw\n", encoding="utf-8")

    def make(self, profile: str = se.CORE, packages: dict[str, str] | None = None, **kw):
        """One `venv` run with a recorded pip; returns (exit code, status, pip)."""
        pins = dict(CORE_PINS if profile == se.CORE else {**CORE_PINS, **STUDIO_PINS})
        pip = FakePip()
        probe = FakeProbe(pins if packages is None else packages)
        code, status = se.ensure(self.ws, self.tree, profile, runner=pip, probe=probe,
                                 base_python="python", out=lambda *_: None, **kw)
        return code, status, pip

    def state(self, profile: str = se.CORE, packages: dict[str, str] | None = None) -> se.Status:
        pins = dict(CORE_PINS if profile == se.CORE else {**CORE_PINS, **STUDIO_PINS})
        return se.inspect(self.ws, self.tree, profile, probe=FakeProbe(pins if packages is None else packages))


class RequirementSplit(unittest.TestCase):
    """The real files of the repository (TOOL-63: core without Qt)."""

    def test_core_has_no_studio_package(self):
        core = se.ci().requirement_names(REPO / "tools" / "requirements.txt")
        for name in ("pyside6", "pytest", "pytest-qt", "pyinstaller"):
            self.assertNotIn(name, core, "a workspace that only builds must not install Qt")

    def test_studio_file_holds_exactly_the_studio_packages(self):
        studio = se.ci().requirement_names(REPO / "tools" / "requirements-studio.txt")
        self.assertEqual(studio, {"pyside6", "pytest", "pytest-qt", "pyinstaller"})

    def test_core_and_studio_do_not_overlap(self):
        core = se.ci().requirement_names(REPO / "tools" / "requirements.txt")
        studio = se.ci().requirement_names(REPO / "tools" / "requirements-studio.txt")
        self.assertEqual(core & studio, set())
        self.assertTrue({"jinja2", "pyyaml", "pydantic", "cantools", "ziglang"} <= core)

    def test_profiles_differ(self):
        self.assertNotEqual(se.fingerprint(REPO, se.CORE), se.fingerprint(REPO, se.STUDIO))
        self.assertEqual(se.fingerprint(REPO, se.CORE), se.fingerprint(REPO, se.CORE))

    def test_hint_names_a_command_for_every_state(self):
        for state in (se.MISSING, se.BROKEN, se.UNMARKED, se.OTHER_TREE, se.STALE, se.INCOMPLETE):
            self.assertIn("python -m tools.setup venv", se.hint(state, "C:/ws"), state)
        self.assertEqual(se.hint(se.CURRENT, "C:/ws"), "")


class Creating(Synthetic):

    def test_first_run_creates_and_installs(self):
        code, status, pip = self.make()
        self.assertEqual(code, 0, status.text)
        self.assertEqual(status.state, se.CURRENT)
        self.assertTrue(se.venv_python(status.venv).is_file())
        self.assertTrue(se.marker_file(status.venv).is_file())
        install = pip.pip_calls[-1]
        self.assertIn(str(self.requirements), install)
        self.assertIn(str(self.tree / "tools" / "ci" / "constraints.txt"), install)
        self.assertNotIn(str(self.tree / "tools" / "requirements-studio.txt"), install)

    def test_marker_names_tree_version_and_files(self):
        self.make()
        marker = json.loads(se.marker_file(se.venv_dir(self.ws)).read_text(encoding="utf-8"))
        self.assertEqual(marker["nextbsw_version"], "1.4.2")
        self.assertEqual(marker["profile"], se.CORE)
        self.assertEqual(marker["fingerprint"], se.fingerprint(self.tree, se.CORE))
        self.assertEqual(Path(marker["tree"]), self.tree.resolve())
        self.assertIn("tools/requirements.txt", marker["files"])

    def test_second_run_changes_nothing(self):
        self.make()
        before = se.marker_file(se.venv_dir(self.ws)).read_text(encoding="utf-8")
        code, status, pip = self.make()
        self.assertEqual(code, 0)
        self.assertEqual(status.state, se.CURRENT)
        self.assertEqual(pip.calls, [], "a second run must not start pip again")
        self.assertEqual(se.marker_file(se.venv_dir(self.ws)).read_text(encoding="utf-8"), before)

    def test_studio_profile_adds_the_second_file(self):
        code, status, pip = self.make(profile=se.STUDIO)
        self.assertEqual(code, 0, status.text)
        self.assertIn(str(self.tree / "tools" / "requirements-studio.txt"), pip.pip_calls[-1])
        # the core environment of the same workspace is a different environment, not the same one
        self.assertEqual(self.state(se.CORE).state, se.STALE)

    def test_pip_is_pinned_before_the_packages(self):
        _, _, pip = self.make()
        self.assertIn("pip==25.0.1", pip.pip_calls[0])


class Repairing(Synthetic):

    def test_changed_requirements_make_the_marker_stale(self):
        self.make()
        self.requirements.write_text("jinja2~=3.1\nPyYAML~=6.0\npydantic~=2.13\n", encoding="utf-8")
        status = self.state()
        self.assertEqual(status.state, se.STALE)
        self.assertIn("veraltet", status.text)
        code, after, pip = self.make(packages={**CORE_PINS, "pydantic": "2.13.5"})
        self.assertEqual(code, 0, after.text)
        self.assertEqual(after.state, se.CURRENT)
        self.assertTrue(pip.pip_calls, "a stale environment is installed over")

    def test_missing_package_is_named_and_installed(self):
        self.make()
        status = self.state(packages={"jinja2": "3.1.6"})
        self.assertEqual(status.state, se.INCOMPLETE)
        self.assertEqual(status.problems, ["pyyaml fehlt"])
        self.assertIn("python -m tools.setup venv", se.hint(status.state, self.ws))

    def test_wrong_version_is_named(self):
        self.make()
        status = self.state(packages={**CORE_PINS, "pyyaml": "5.4.1"})
        self.assertEqual(status.state, se.INCOMPLETE)
        self.assertEqual(status.problems, ["pyyaml 5.4.1 statt 6.0.3"])

    def test_venv_without_interpreter_is_named_not_overwritten(self):
        self.make()
        se.venv_python(se.venv_dir(self.ws)).unlink()
        status = self.state()
        self.assertEqual(status.state, se.BROKEN)
        self.assertIn("keinen Interpreter", status.text)
        code, _, pip = self.make()
        self.assertEqual(code, 1)
        self.assertEqual(pip.calls, [], "a broken environment must not be silently rebuilt")
        self.assertTrue(se.venv_dir(self.ws).is_dir(), "and must not be deleted either")

    def test_recreate_rebuilds_a_broken_environment(self):
        self.make()
        se.venv_python(se.venv_dir(self.ws)).unlink()
        code, status, pip = self.make(recreate=True)
        self.assertEqual(code, 0, status.text)
        self.assertEqual(status.state, se.CURRENT)
        self.assertTrue(pip.pip_calls)

    def test_unreadable_marker_is_named(self):
        self.make()
        se.marker_file(se.venv_dir(self.ws)).write_text("{not json", encoding="utf-8")
        status = self.state()
        self.assertEqual(status.state, se.BROKEN)
        self.assertIn("nicht lesbar", status.text)

    def test_unknown_marker_schema_is_named(self):
        self.make()
        se.marker_file(se.venv_dir(self.ws)).write_text('{"schema": 99}', encoding="utf-8")
        self.assertEqual(self.state().state, se.BROKEN)

    def test_environment_of_another_tree_is_named(self):
        self.make()
        path = se.marker_file(se.venv_dir(self.ws))
        marker = json.loads(path.read_text(encoding="utf-8"))
        marker["tree"] = "C:/somewhere/else/nextbsw"
        path.write_text(json.dumps(marker), encoding="utf-8")
        status = self.state()
        self.assertEqual(status.state, se.OTHER_TREE)
        code, _, pip = self.make()
        self.assertEqual(code, 1)
        self.assertEqual(pip.calls, [])

    def test_foreign_environment_is_not_touched_without_adopt(self):
        self.make()
        se.marker_file(se.venv_dir(self.ws)).unlink()
        status = self.state()
        self.assertEqual(status.state, se.UNMARKED)
        code, _, pip = self.make()
        self.assertEqual(code, 1)
        self.assertEqual(pip.calls, [])
        self.assertIn("--adopt", se.hint(status.state, self.ws))

    def test_adopt_checks_before_it_takes_over(self):
        self.make()
        se.marker_file(se.venv_dir(self.ws)).unlink()
        code, status, pip = self.make(adopt=True)
        self.assertEqual(code, 0, status.text)
        self.assertEqual(status.state, se.CURRENT)
        self.assertEqual(pip.calls, [], "adopt checks, it does not install")

    def test_adopt_refuses_an_environment_that_does_not_fit(self):
        self.make()
        se.marker_file(se.venv_dir(self.ws)).unlink()
        code, _, pip = self.make(adopt=True, packages={"jinja2": "2.0.0"})
        self.assertEqual(code, 1)
        self.assertEqual(pip.calls, [])
        self.assertFalse(se.marker_file(se.venv_dir(self.ws)).is_file())

    def test_failed_installation_leaves_no_marker(self):
        pip = FakePip(fail_on="requirements.txt")
        with self.assertRaises(se.SetupError):
            se.ensure(self.ws, self.tree, se.CORE, runner=pip, probe=FakeProbe(CORE_PINS),
                      base_python="python", out=lambda *_: None)
        self.assertFalse(se.marker_file(se.venv_dir(self.ws)).is_file())
        self.assertEqual(self.state().state, se.UNMARKED)

    def test_installation_that_leaves_a_package_missing_fails(self):
        pip = FakePip()
        code, status = se.ensure(self.ws, self.tree, se.CORE, runner=pip, probe=FakeProbe({"jinja2": "3.1.6"}),
                                 base_python="python", out=lambda *_: None)
        self.assertEqual(code, 1)
        self.assertFalse(se.marker_file(se.venv_dir(self.ws)).is_file())


class Discovery(Synthetic):

    def test_walk_up_finds_the_pin(self):
        deep = self.ws / "projects" / "bcm_main"
        deep.mkdir(parents=True)
        self.assertEqual(se.find_workspace(deep), self.ws.resolve())
        self.assertEqual(se.find_workspace(deep / "project.yaml"), self.ws.resolve())

    def test_walk_stops_at_a_tree(self):
        inside = self.tree / "projects" / "demo"
        inside.mkdir(parents=True)
        self.assertTrue(se.is_tree(self.tree))
        self.assertIsNone(se.find_workspace(inside))

    def test_walk_stops_at_a_repository_without_a_pin(self):
        other = self.ws.parent / "plain_repo"
        (other / ".git").mkdir(parents=True)
        (other / "src").mkdir()
        self.assertIsNone(se.find_workspace(other / "src"))

    def test_the_repository_itself_is_a_tree(self):
        self.assertTrue(se.is_tree(REPO))


class RealVenv(unittest.TestCase):
    """One real, empty environment - local and offline, no package is fetched."""

    def test_layout_of_a_real_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            venv = se.venv_dir(workspace)
            done = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                                  capture_output=True, text=True, timeout=300)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertTrue(se.venv_python(venv).is_file(), "venv_python must match this platform's layout")
            status = se.inspect(workspace, REPO, se.CORE, probe=lambda argv: "")
            self.assertEqual(status.state, se.UNMARKED, status.text)
            se.remove_tree(venv)
            self.assertFalse(venv.exists())


if __name__ == "__main__":
    unittest.main()
