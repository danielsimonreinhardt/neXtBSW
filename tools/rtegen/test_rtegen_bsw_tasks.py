"""PLAT-26: stacks and merges of the BSW tasks in tools/rtegen - tasks/BswTasks.h, the context aliases in
async/Config.h and the task plan without the merged tasks.

Own test profile: the BSW task profile test/multitask/tasks.yaml (comm, io, guard with above_application) with an own
stack manifest written per test; the ECU description is a plain mapping without components. The reference output
of a whole project with merges is test_reference_output.py (profile test_bsw_tasks); the checks with file, line
and YAML path are tools/nxconfig/test_bswtasks.py.

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_bsw_tasks.py
"""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import rtegen  # noqa: E402
from nxconfig.model import BswTaskUse  # noqa: E402

MULTI_TASKS = HERE / "test" / "multitask" / "tasks.yaml"
STACKS = """
schema_version: 1
default: { stack_bytes: 1024, min_stack_bytes: 256 }
tasks:
  idle: { stack_bytes: 768, min_stack_bytes: 256 }
  timer: { stack_bytes: 512, min_stack_bytes: 256 }
  comm: { stack_bytes: 1536, min_stack_bytes: 512, run_in: [io] }
"""
ECU = {"tasks": [{"name": "app", "period_ms": 10, "priority": 30, "stack_bytes": 1024}], "components": []}


def config(**values):
    """Stand-in for a checked project: only project.bsw_tasks is read."""
    return SimpleNamespace(project=SimpleNamespace(bsw_tasks={k: BswTaskUse(**v) for k, v in values.items()}))


class BswTaskPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.stacks = self.tmp / "taskStacks.yaml"
        self.stacks.write_text(STACKS, encoding="utf-8")
        self.manifest = rtegen.load_task_manifest(MULTI_TASKS)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plan(self, cfg=None):
        return rtegen.load_bsw_task_plan(cfg, self.manifest, REPO, self.stacks)

    def test_header_defaults(self):
        text = rtegen.render_bsw_tasks(self.plan(), "rte.yaml")
        self.assertEqual(re.findall(r"#define (NX_BSW_TASK_\w+) (\w+)", text), [
            ("NX_BSW_TASK_STACK_IDLE", "768U"), ("NX_BSW_TASK_STACK_TIMER", "512U"),
            ("NX_BSW_TASK_STACK_COMM", "1536U"), ("NX_BSW_TASK_OWN_COMM", "1"),
            ("NX_BSW_TASK_STACK_IO", "1024U"), ("NX_BSW_TASK_OWN_IO", "1"),
            ("NX_BSW_TASK_STACK_GUARD", "1024U"), ("NX_BSW_TASK_OWN_GUARD", "1"),
            ("NX_BSW_TASK_STACK_BYTES", f"{768 + 512 + 1536 + 1024 + 1024}U")])

    def test_header_merge_and_value(self):
        text = rtegen.render_bsw_tasks(self.plan(config(comm={"run_in": "io"}, guard={"stack_bytes": 2048})),
                                       "rte.yaml")
        self.assertNotIn("NX_BSW_TASK_STACK_COMM", text)
        self.assertIn("#define NX_BSW_TASK_OWN_COMM 0 /* runs in io */", text)
        self.assertIn("#define NX_BSW_TASK_STACK_GUARD 2048U /* guard (default 1024) */", text)
        self.assertIn(f"#define NX_BSW_TASK_STACK_BYTES {768 + 512 + 1024 + 2048}U", text)

    def test_project_error_raises_with_project_path(self):
        with self.assertRaisesRegex(rtegen.RteError, r"^project\.yaml bsw_tasks\.guard\.run_in: task guard cannot"):
            self.plan(config(guard={"run_in": "io"}))
        with self.assertRaisesRegex(rtegen.RteError, r"^project\.yaml bsw_tasks\.io\.stack_bytes: .* minimum 256"):
            self.plan(config(io={"stack_bytes": 128}))

    def test_merged_task_has_no_priority(self):
        app, ordered = rtegen.build_tasks(ECU, {}, self.manifest, None, {"comm": "io"})
        self.assertEqual([(t.name, t.os_priority) for t in ordered], [("app", 1), ("io", 2), ("guard", 3)])
        self.assertEqual([t.name for t in app], ["app"])
        # without the merge the key of comm (20) orders it first
        _, ordered = rtegen.build_tasks(ECU, {}, self.manifest, None)
        self.assertEqual([t.name for t in ordered], ["comm", "app", "io", "guard"])

    def test_merged_name_stays_taken(self):
        ecu = {"tasks": [{"name": "comm", "period_ms": 10, "priority": 30, "stack_bytes": 1024}], "components": []}
        with self.assertRaisesRegex(rtegen.RteError, r"^tasks\[0\]\.name: comm is already used by BSW task"):
            rtegen.build_tasks(ecu, {}, self.manifest, None, {"comm": "io"})

    def test_repository_manifest_without_project(self):
        """Without a project (config None) the repository manifest gives every task of the profile its default."""
        plan = rtegen.load_bsw_task_plan(None, self.manifest, REPO)
        self.assertEqual({t.name: t.stack_bytes for t in plan.tasks},
                         {"idle": 2048, "timer": 1024, "comm": 2048, "io": 2048, "guard": 2048})


class DemoHeaderTest(unittest.TestCase):
    """The eval board demo without bsw_tasks: the stacks of the image before PLAT-26, no alias in async/Config.h."""

    def test_demo(self):
        # PLAT-62: the same for the own full test profile, which runs in the exported open tree as well
        self.check(REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml")

    def test_eval_board_demo(self):
        demo = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
        if not demo.is_file():
            raise unittest.SkipTest("private project missing")
        self.check(demo)

    def check(self, demo):
        tmp = Path(tempfile.mkdtemp())
        try:
            rtegen.generate_project(demo, tmp, REPO)
            text = (tmp / "tasks" / "BswTasks.h").read_text(encoding="utf-8")
            config_h = (tmp / "tasks" / "async" / "Config.h").read_text(encoding="utf-8")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        stacks = dict(re.findall(r"#define NX_BSW_TASK_STACK_(\w+) (\d+)U", text))
        self.assertEqual(stacks, {"IDLE": "2048", "TIMER": "1024", "BACKGROUND": "2048", "BSP": "2048", "CAN": "2048",
                                  "SYSADMIN": "2048", "SAFETY": "2048", "BYTES": str(6 * 2048 + 1024)})
        self.assertNotIn("PLAT-26", config_h)


if __name__ == "__main__":
    unittest.main()
