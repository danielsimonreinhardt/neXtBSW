"""PLAT-26: tools/nxconfig/bswtasks.py - stacks and merges of the BSW tasks as project values.

- resolve() against an own task and stack profile (names comm/io/guard like tools/rtegen/test/multitask, not the
  tasks of the ECU application): defaults, floor, granularity, allowed merges, no merge for or into a task with
  above_application, no chain, unknown names, a warning below the default and for every merge
- the repository's stack manifest (ecu/configuration/taskStacks.yaml) against the BSW task manifest and the values
  the image had before PLAT-26 (6 x 2048 B + timer 1024 B + safety 2048 B)
- nxconfig check with the synthetic profile testproject_s32k142_bl (28 KB application RAM, bootloader, no SBC) copied into a
  temporary tree: messages with file, line and YAML path, the RAM sum as error once the project decides its stacks,
  as warning without bsw_tasks; the eval board demo without bsw_tasks gets the defaults and no message
- PLAT-107 (safety review 2026-10-01 on PLAT-26): a stack below the default without proof note (evidence) is an
  error in an ASIL project (project.integrity left out or not qm) and a warning in a QM project; the stack must hold
  the used bytes of its proof; integrity qm with an ASIL NvM block is refused; in a project with a power stage or
  gate driver only the merges of run_in_power_stage are allowed (background -> bsp refused, own profile and a copy of
  the eval board demo, which drives a PWM group with a gate driver)

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_bswtasks.py
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import bswtasks  # noqa: E402
from nxconfig.model import BswTaskUse  # noqa: E402

PROFILES = HERE / "test" / "profiles"
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_PROJECT and is skipped where it is missing.
DEMO_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
PROJECT = "projects/testproject_s32k142_bl/project.yaml"

# own profile: BSW tasks comm, io, guard (above_application)
TEST_TASKS = [("comm", False), ("io", False), ("guard", True)]
TEST_STACKS = """
schema_version: 1
default: { stack_bytes: 1024, min_stack_bytes: 256 }
tasks:
  idle: { stack_bytes: 768, min_stack_bytes: 256 }
  timer: { stack_bytes: 512, min_stack_bytes: 256 }
  comm: { stack_bytes: 1536, min_stack_bytes: 512, run_in: [io, guard], note: "comm then waits behind io" }
  guard: { stack_bytes: 1024, min_stack_bytes: 800, multiple_of: 32, run_in: [io] }
"""


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        path = self.tmp / "taskStacks.yaml"
        path.write_text(TEST_STACKS, encoding="utf-8")
        self.stacks = bswtasks.load_stack_manifest(path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def resolve(self, **values):
        errors, warnings = [], []
        plan = bswtasks.resolve(TEST_TASKS, self.stacks, {k: BswTaskUse(**v) for k, v in values.items()},
                                lambda p, t: errors.append((p, t)), lambda p, t: warnings.append((p, t)))
        return plan, errors, warnings

    def test_defaults(self):
        plan, errors, warnings = self.resolve()
        self.assertEqual((errors, warnings), ([], []))
        self.assertEqual([(t.name, t.stack_bytes, t.own) for t in plan.tasks],
                         [("idle", 768, True), ("timer", 512, True), ("comm", 1536, True), ("io", 1024, True),
                          ("guard", 1024, True)])
        self.assertEqual(plan.stack_bytes, 768 + 512 + 1536 + 1024 + 1024)

    def test_stack_value(self):
        plan, errors, warnings = self.resolve(io={"stack_bytes": 2048}, idle={"stack_bytes": 512})
        self.assertEqual(errors, [])
        self.assertEqual(plan.task("io").stack_bytes, 2048)
        self.assertEqual(plan.task("idle").stack_bytes, 512)
        # below the default: a warning with its path, above: none
        self.assertEqual([p for p, _ in warnings], [("bsw_tasks", "idle", "stack_bytes")])
        self.assertIn("below the default 768 B", warnings[0][1])

    def test_floor_and_granularity(self):
        plan, errors, _ = self.resolve(io={"stack_bytes": 128}, guard={"stack_bytes": 1000},
                                       comm={"stack_bytes": 1028})
        self.assertEqual([p for p, _ in errors], [("bsw_tasks", "comm", "stack_bytes"),
                                                  ("bsw_tasks", "io", "stack_bytes"),
                                                  ("bsw_tasks", "guard", "stack_bytes")])
        self.assertIn("no multiple of 8", errors[0][1])
        self.assertIn("below the minimum 256 B", errors[1][1])
        self.assertIn("no multiple of 32 (MPU region of its own)", errors[2][1])
        # a refused value keeps the default
        self.assertEqual(plan.task("io").stack_bytes, 1024)

    def test_merge(self):
        plan, errors, warnings = self.resolve(comm={"run_in": "io"})
        self.assertEqual(errors, [])
        comm = plan.task("comm")
        self.assertEqual((comm.own, comm.run_in, comm.stack_bytes), (False, "io", 0))
        self.assertEqual(plan.stack_bytes, 768 + 512 + 1024 + 1024)
        self.assertEqual([p for p, _ in warnings], [("bsw_tasks", "comm", "run_in")])
        self.assertIn("comm then waits behind io", warnings[0][1])

    def test_merge_refused(self):
        cases = {
            "guard": ({"guard": {"run_in": "io"}}, "cannot run in another task (above_application"),
            "into_guard": ({"comm": {"run_in": "guard"}}, "no task may run in guard"),
            "not_allowed": ({"io": {"run_in": "comm"}}, "may not run in comm (allowed by taskStacks.yaml: none)"),
            "unknown": ({"comm": {"run_in": "nosuch"}}, "nosuch is no BSW task"),
            "os_task": ({"idle": {"run_in": "io"}}, "cannot run in another task (OpenBSW task)"),
        }
        for name, (values, fragment) in cases.items():
            with self.subTest(name):
                plan, errors, _ = self.resolve(**values)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(fragment, errors[0][1])
                self.assertTrue(all(t.own for t in plan.tasks))

    def test_chain_refused(self):
        stacks = self.tmp / "chain.yaml"
        stacks.write_text(TEST_STACKS.replace("tasks:\n", "tasks:\n  io: { stack_bytes: 1024, min_stack_bytes: 256, "
                                                        "run_in: [comm] }\n"), encoding="utf-8")
        self.stacks = bswtasks.load_stack_manifest(stacks)
        _, errors, _ = self.resolve(comm={"run_in": "io"}, io={"run_in": "comm"})
        self.assertTrue(errors)
        self.assertTrue(all("runs in" in t and "itself" in t for _, t in errors), errors)

    def test_unknown_task(self):
        _, errors, _ = self.resolve(swc={"stack_bytes": 1024})
        self.assertEqual(errors[0][0], ("bsw_tasks", "swc"))
        self.assertIn("rte.yaml tasks[].stack_bytes", errors[0][1])

    def test_schema_stack_or_merge(self):
        with self.assertRaises(ValueError):
            BswTaskUse(stack_bytes=1024, run_in="io")

    def test_broken_manifest(self):
        for text, fragment in (("schema_version: 1\ntasks: {}\n", "default"),
                               (TEST_STACKS.replace("min_stack_bytes: 800", "min_stack_bytes: 2048"),
                                "not above the default"),
                               (TEST_STACKS.replace("  idle: { stack_bytes: 768, min_stack_bytes: 256 }\n", ""),
                                "tasks.idle: missing")):
            with self.subTest(fragment):
                path = self.tmp / "broken.yaml"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(bswtasks.StackManifestError) as ctx:
                    bswtasks.load_stack_manifest(path)
                self.assertIn(fragment, str(ctx.exception))


# PLAT-107: a fourth BSW task aux; comm may run in io or aux, with a power stage only in aux
POWER_TASKS = TEST_TASKS + [("aux", False)]
POWER_STACKS = TEST_STACKS.replace('run_in: [io, guard], note: "comm then waits behind io"',
                                   'run_in: [io, aux], run_in_power_stage: [aux], note: "comm then waits behind io"')
EVIDENCE = {"method": "stackuse", "used_bytes": 400, "reference": "tools/stackuse report of image v1.2.3"}


class EvidenceAndPowerStageTest(unittest.TestCase):
    """PLAT-107: proof note below the default (S3) and merges in a project with a power stage (S2)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        path = self.tmp / "taskStacks.yaml"
        path.write_text(POWER_STACKS, encoding="utf-8")
        self.stacks = bswtasks.load_stack_manifest(path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def resolve(self, asil=False, power_stage=None, **values):
        errors, warnings = [], []
        plan = bswtasks.resolve(POWER_TASKS, self.stacks, {k: BswTaskUse(**v) for k, v in values.items()},
                                lambda p, t: errors.append((p, t)), lambda p, t: warnings.append((p, t)),
                                asil=asil, power_stage=power_stage)
        return plan, errors, warnings

    def test_asil_without_proof_note_is_an_error(self):
        plan, errors, warnings = self.resolve(asil=True, idle={"stack_bytes": 512})
        self.assertEqual([p for p, _ in errors], [("bsw_tasks", "idle", "stack_bytes")])
        self.assertIn("below the default 768 B without a proof note in an ASIL project", errors[0][1])
        self.assertIn("bsw_tasks.idle.evidence", errors[0][1])
        self.assertEqual(warnings, [])
        self.assertEqual(plan.task("idle").stack_bytes, 768)           # refused: the default stays

    def test_qm_without_proof_note_is_a_warning(self):
        plan, errors, warnings = self.resolve(idle={"stack_bytes": 512})
        self.assertEqual(errors, [])
        self.assertEqual([p for p, _ in warnings], [("bsw_tasks", "idle", "stack_bytes")])
        self.assertIn("note it in bsw_tasks.idle.evidence", warnings[0][1])
        self.assertEqual(plan.task("idle").stack_bytes, 512)

    def test_proof_note_accepts_the_value(self):
        for asil in (True, False):
            with self.subTest(asil=asil):
                plan, errors, warnings = self.resolve(asil=asil, idle={"stack_bytes": 512, "evidence": EVIDENCE})
                self.assertEqual((errors, warnings), ([], []))
                self.assertEqual(plan.task("idle").stack_bytes, 512)
        # at or above the default no note is needed, not even in an ASIL project
        _, errors, warnings = self.resolve(asil=True, io={"stack_bytes": 1024}, comm={"stack_bytes": 2048})
        self.assertEqual((errors, warnings), ([], []))

    def test_stack_must_hold_its_proof(self):
        evidence = dict(EVIDENCE, used_bytes=520)
        _, errors, _ = self.resolve(asil=True, idle={"stack_bytes": 512, "evidence": evidence})
        self.assertEqual([p for p, _ in errors], [("bsw_tasks", "idle", "evidence", "used_bytes")])
        self.assertIn("uses 520 B according to its proof (stackuse: tools/stackuse report of image v1.2.3), more "
                      "than its stack of 512 B", errors[0][1])
        # without stack_bytes the default is the stack the proof is held against
        _, errors, _ = self.resolve(io={"evidence": dict(EVIDENCE, used_bytes=1100)})
        self.assertEqual([p for p, _ in errors], [("bsw_tasks", "io", "evidence", "used_bytes")])
        _, errors, _ = self.resolve(io={"evidence": dict(EVIDENCE, used_bytes=1024)})
        self.assertEqual(errors, [])

    def test_schema(self):
        with self.assertRaisesRegex(ValueError, "evidence and run_in exclude each other"):
            BswTaskUse(run_in="io", evidence=EVIDENCE)
        for bad in ({"method": "guess", "used_bytes": 1, "reference": "x"},
                    {"method": "stackuse", "used_bytes": 0, "reference": "x"},
                    {"method": "stackuse", "used_bytes": 1, "reference": ""}):
            with self.subTest(bad), self.assertRaises(ValueError):
                BswTaskUse(stack_bytes=512, evidence=bad)

    def test_power_stage_narrows_the_merges(self):
        # without a power stage both merges of run_in
        for target in ("io", "aux"):
            _, errors, _ = self.resolve(comm={"run_in": target})
            self.assertEqual(errors, [], target)
        # with one only run_in_power_stage; the message names the reason, the consequence and the decision
        plan, errors, _ = self.resolve(power_stage="PWM group MOT1", comm={"run_in": "io"})
        self.assertEqual([p for p, _ in errors], [("bsw_tasks", "comm", "run_in")])
        self.assertIn("may not run in io in a project with a power stage or gate driver (PWM group MOT1): comm then "
                      "waits behind io", errors[0][1])
        self.assertIn("PLAT-26/S2", errors[0][1])
        self.assertIn("run_in_power_stage: aux", errors[0][1])
        self.assertTrue(plan.task("comm").own)
        _, errors, warnings = self.resolve(power_stage="PWM group MOT1", comm={"run_in": "aux"})
        self.assertEqual(errors, [])
        self.assertEqual([p for p, _ in warnings], [("bsw_tasks", "comm", "run_in")])
        # a task without run_in_power_stage keeps run_in (guard -> io is refused for above_application anyway)
        self.assertIsNone(self.stacks.spec("guard").run_in_power_stage)
        self.assertEqual(self.stacks.spec("guard").merges(True), ("io",))

    def test_manifest_run_in_power_stage(self):
        for text, fragment in ((POWER_STACKS.replace("run_in_power_stage: [aux]", "run_in_power_stage: [guard]"),
                                "guard is not in run_in"),
                               (POWER_STACKS.replace("run_in_power_stage: [aux]", "run_in_power_stage: aux"),
                                "run_in_power_stage: expected a list")):
            with self.subTest(fragment):
                path = self.tmp / "broken.yaml"
                path.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(bswtasks.StackManifestError, fragment):
                    bswtasks.load_stack_manifest(path)

    def test_asil_project(self):
        from types import SimpleNamespace

        class Rep:
            def __init__(self):
                self.errors = []

            def error(self, level, path, text):
                self.errors.append((level, path, text))

        def cfg(integrity, blocks=None):
            nvm = None if blocks is None else SimpleNamespace(
                blocks={n: SimpleNamespace(integrity=i) for n, i in blocks.items()})
            return SimpleNamespace(project=SimpleNamespace(project=SimpleNamespace(integrity=integrity), nvm=nvm))

        for integrity in (None, "asil_a", "asil_b", "asil_d"):
            self.assertTrue(bswtasks.asil_project(cfg(integrity)), integrity)
        rep = Rep()
        self.assertFalse(bswtasks.asil_project(cfg("qm", {"Hours": "qm", "Count": None}), rep))
        self.assertEqual(rep.errors, [])
        self.assertTrue(bswtasks.asil_project(cfg("qm", {"Hours": "qm", "Cal": "asil_b"}), rep))
        self.assertEqual([(lvl, path) for lvl, path, _ in rep.errors], [("project", ("project", "integrity"))])
        self.assertIn("NvM block Cal has the integrity asil_b", rep.errors[0][2])


class RepositoryManifestTest(unittest.TestCase):
    """ecu/configuration/taskStacks.yaml: every entry names a task of the BSW task manifest (or idle/timer), and the
    defaults are the values of the image before PLAT-26 (same image without project values)."""

    def test_defaults_are_the_image_before(self):
        stacks = bswtasks.load_stack_manifest(bswtasks.task_stacks_manifest())
        bsw = bswtasks._bsw_tasks_of(REPO.joinpath(*bswtasks.TASKS_MANIFEST_NAMES))
        names = [n for n, _ in bsw]
        self.assertEqual(sorted(set(stacks.tasks) - set(bswtasks.OS_TASKS)), sorted(names))
        plan = bswtasks.resolve(bsw, stacks, {}, self.fail, self.fail)
        self.assertEqual({t.name: t.stack_bytes for t in plan.tasks},
                         {"idle": 2048, "timer": 1024, "background": 2048, "bsp": 2048, "can": 2048,
                          "sysadmin": 2048, "safety": 2048})
        # safety: MPU region (32), never merged; every allowed merge target is a task without above_application
        above = {n for n, a in bsw if a}
        self.assertEqual(stacks.spec("safety").multiple_of, 32)
        for name, spec in stacks.tasks.items():
            with self.subTest(name):
                self.assertFalse(name in above and spec.run_in)
                self.assertFalse(set(spec.run_in) & above)
                self.assertTrue(set(spec.run_in) <= set(names))
        # PLAT-107 (PLAT-26/S2): with a power stage background no longer runs in bsp; sysadmin keeps both
        self.assertEqual(stacks.spec("background").merges(True), ("can",))
        self.assertEqual(stacks.spec("background").merges(False), ("can", "bsp"))
        self.assertEqual(stacks.spec("sysadmin").merges(True), ("can", "bsp"))


class ProjectCheckTest(unittest.TestCase):
    """nxconfig check: messages with file, line and YAML path of project.yaml."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for kind, name in (("boards", "testboard_s32k142_bl"), ("projects", "testproject_s32k142_bl")):
            shutil.copytree(PROFILES / kind / name, self.tmp / kind / name)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, bsw_tasks=None, noinit=None, integrity=None):
        path = self.tmp / PROJECT
        yaml = YAML()
        data = yaml.load(path.read_text(encoding="utf-8"))
        if bsw_tasks is not None:
            data["bsw_tasks"] = bsw_tasks
        else:
            data.pop("bsw_tasks", None)
        if integrity is not None:
            data["project"]["integrity"] = integrity
        if noinit is not None:
            data["memory"]["application"]["noinit_size"] = noinit
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f)
        return nxconfig.check(path)

    def findings(self, messages, severity):
        return [m for m in messages if m.severity == severity and m.path[:1] in (("bsw_tasks",), ("memory",))]

    def test_without_values(self):
        config, messages = self.check()
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual(self.findings(messages, "warning") + self.findings(messages, "error"), [])
        self.assertEqual(config.bsw_tasks.stack_bytes, 13312)
        self.assertIn(bswtasks.task_stacks_manifest(), config.code_inputs)

    def test_error_with_path_and_line(self):
        _, messages = self.check({"can": {"stack_bytes": 100}})
        errors = self.findings(messages, "error")
        self.assertEqual(len(errors), 1, [str(m) for m in messages])
        self.assertEqual((errors[0].file.name, errors[0].path), ("project.yaml", ("bsw_tasks", "can",
                                                                                      "stack_bytes")))
        self.assertIsInstance(errors[0].line, int)

    def test_merge_warning(self):
        config, messages = self.check({"background": {"run_in": "can"}, "sysadmin": {"run_in": "bsp"}})
        self.assertIsNotNone(config, [str(m) for m in messages])
        warnings = self.findings(messages, "warning")
        self.assertEqual([m.path for m in warnings], [("bsw_tasks", "background", "run_in"),
                                                           ("bsw_tasks", "sysadmin", "run_in")])
        self.assertIn("NvM", warnings[0].text)
        self.assertEqual(config.bsw_tasks.stack_bytes, 13312 - 2 * 2048)

    def test_ram_sum(self):
        # 28 KB application RAM (SRAM_L, SRAM_U): 6 x 2 KB + 1 KB + interrupt stack 4 KB + guard 1 KB + RAM functions 4 KB + no-init
        _, messages = self.check({"bsp": {"stack_bytes": 12288}})
        errors = self.findings(messages, "error")
        self.assertEqual([m.path for m in errors], [("bsw_tasks",)], [str(m) for m in messages])
        self.assertIn("exceed the application RAM of 28672 B", errors[0].text)
        # the same sum without bsw_tasks: a warning at the RAM of the application (a profile that does not link yet
        # still configures), here forced by a large no-init area
        _, messages = self.check(noinit=0x3000)
        self.assertEqual([m.path for m in self.findings(messages, "warning")],
                         [("memory", "application", "ram")])

    def test_proof_note_with_path_and_line(self):
        """PLAT-107: the test profile states no integrity (= ASIL): below the default without a note is an error with
        file, line and YAML path; qm makes it a warning; a note accepts the value."""
        _, messages = self.check({"idle": {"stack_bytes": 1024}})
        errors = self.findings(messages, "error")
        self.assertEqual([(m.file.name, m.path) for m in errors], [("project.yaml", ("bsw_tasks", "idle",
                                                                                      "stack_bytes"))])
        self.assertIsInstance(errors[0].line, int)
        self.assertIn("without a proof note in an ASIL project", errors[0].text)
        config, messages = self.check({"idle": {"stack_bytes": 1024}}, integrity="qm")
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual([m.path for m in self.findings(messages, "warning")], [("bsw_tasks", "idle", "stack_bytes")])
        config, messages = self.check({"idle": {"stack_bytes": 1024, "evidence": dict(EVIDENCE, used_bytes=900)}})
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual(self.findings(messages, "warning") + self.findings(messages, "error"), [])
        self.assertEqual(config.bsw_tasks.task("idle").stack_bytes, 1024)
        _, messages = self.check({"idle": {"stack_bytes": 1024, "evidence": dict(EVIDENCE, used_bytes=1100)}})
        errors = self.findings(messages, "error")
        self.assertEqual([m.path for m in errors], [("bsw_tasks", "idle", "evidence", "used_bytes")])
        self.assertIsInstance(errors[0].line, int)
        # no power stage in this profile: background may run in bsp
        self.assertIsNone(bswtasks.power_stage_project(config))
        config, messages = self.check({"background": {"run_in": "bsp"}})
        self.assertIsNotNone(config, [str(m) for m in messages])

    def test_power_stage_of_the_demo(self):
        """PLAT-107 (PLAT-26/S2): the eval board demo drives a PWM group with a gate driver - background -> bsp is
        refused with file, line and YAML path, background -> can stays a warning."""
        tmp = self.tmp / "demo"
        tmp.mkdir()
        yaml = YAML()
        data = yaml.load(DEMO_PROJECT.read_text(encoding="utf-8"))
        for key in ("board", "rte", "diag"):
            data["project"][key] = (DEMO_PROJECT.parent / data["project"][key]).resolve().as_posix()
        path = tmp / "project.yaml"

        def check(bsw_tasks):
            data["bsw_tasks"] = bsw_tasks
            with path.open("w", encoding="utf-8") as f:
                yaml.dump(data, f)
            return nxconfig.check(path)

        config, messages = check({"background": {"run_in": "bsp"}})
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.path for m in errors], [("bsw_tasks", "background", "run_in")], [str(m) for m in errors])
        self.assertIsInstance(errors[0].line, int)
        self.assertIn("project with a power stage or gate driver (PWM group", errors[0].text)
        self.assertIn("deadman in bsp", errors[0].text)
        config, messages = check({"background": {"run_in": "can"}})
        self.assertIsNotNone(config, [str(m) for m in messages if m.severity == "error"])
        self.assertEqual([m.path for m in messages if m.path[:1] == ("bsw_tasks",)],
                         [("bsw_tasks", "background", "run_in")])

    def test_demo_unchanged(self):
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertIsNotNone(config, [str(m) for m in messages if m.severity == "error"])
        self.assertEqual([m for m in messages if m.path[:1] == ("bsw_tasks",)], [])
        self.assertEqual(config.bsw_tasks.stack_bytes, 6 * 2048 + 1024)
        self.assertTrue(all(t.own for t in config.bsw_tasks.tasks))


if __name__ == "__main__":
    unittest.main()
