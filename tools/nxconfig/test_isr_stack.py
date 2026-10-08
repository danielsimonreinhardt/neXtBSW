"""SYS-132: interrupt stack (MSP) and its MPU guard as project values (memory.application.isr_stack_size /
isr_stack_guard_size) instead of the family default of the linker template (safety review SYS-97 S8 2026-09-30).

- nxconfig check with the synthetic profile testproject_s32k142_bl (28 KB application RAM, bootloader, no SBC) copied
  into a temporary tree: both values reach the MemoryLayout, stack alignment and MPU granularity are errors with file,
  line and YAML path, a value below the template default is a warning (proof with tools/stackuse), the RAM sum of
  tools/nxconfig/bswtasks.py counts the project value
- blgen: memoryLayout.h carries NX_MEMORY_APP_ISR_STACK_SIZE / NX_MEMORY_APP_ISR_STACK_GUARD_SIZE only when the
  project sets them; without them the header is byte for byte what it was (eval board demo against the reviewed
  reference of tools/bswgen/test/reference), so the linker template keeps its defaults and the image stays the same

PLAT-112 (safety review group A 2026-10-01, S132-1 b, S132-2 a, S132-4 a; IsrStackProofTest):
- below the default without proof note (memory.application.isr_stack_evidence / isr_stack_guard_evidence): error
  in an ASIL project (project.integrity left out or asil_*), warning in a QM project; with a note no message, but the
  stack / guard must hold its used_bytes; the guard only with method stackuse
- an ASIL project that sets a value needs a stackuse profile of its own (project folder stackuse.yaml or
  tools/stackuse/profiles/<project>.yaml); the tests of IsrStackProjectValueTest give the project one
- a value the family linker template does not read (family stm32, rm4x: no such template) is an error

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_isr_stack.py
"""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(REPO / "tools" / "bswgen"))

import blgen  # noqa: E402

import nxconfig  # noqa: E402
from nxconfig import bswtasks  # noqa: E402
from nxconfig import memory  # noqa: E402

PROFILES = HERE / "test" / "profiles"
PROJECT = "projects/testproject_s32k142_bl/project.yaml"
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_PROJECT and is skipped where it is missing.
DEMO_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
DEMO_REFERENCE = REPO / "tools" / "bswgen" / "test" / "reference" / "evalboard_s32k144_demo" / "memory" / "memoryLayout.h"
APP_PATH = ("memory", "application")


def defines(text: str) -> dict[str, int]:
    return {n: int(v, 0) for n, v in re.findall(r"#define\s+(\w+)\s+(0x[0-9A-Fa-f]+|\d+)\b", text)}


class IsrStackProjectValueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for kind, name in (("boards", "testboard_s32k142_bl"), ("projects", "testproject_s32k142_bl")):
            shutil.copytree(PROFILES / kind / name, self.tmp / kind / name)
        # PLAT-112 (S132-2 a): the project is an ASIL project (integrity left out) and needs a stackuse profile of
        # its own for a project value; nxconfig only asks whether it exists
        (self.tmp / PROJECT).parent.joinpath(memory.STACKUSE_PROFILE_IN_PROJECT).write_text(
            "# stackuse profile placeholder of the test (tools/nxconfig/test_isr_stack.py)\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, project=None, **application):
        path = self.tmp / PROJECT
        yaml = YAML()
        data = yaml.load(path.read_text(encoding="utf-8"))
        data["memory"]["application"].update(application)
        data["project"].update(project or {})
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f)
        return nxconfig.check(path)

    @staticmethod
    def findings(messages, severity, key):
        return [m for m in messages if m.severity == severity and m.path == APP_PATH + (key,)]

    def test_left_out_keeps_the_template_default(self):
        config, messages = self.check()
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual((config.memory.isr_stack_size, config.memory.isr_stack_guard_size), (None, None))
        header = blgen.memory_files(config)[blgen.APP_LINKER_HEADER]
        self.assertNotIn("ISR_STACK", header)

    def test_project_value_reaches_layout_and_header(self):
        config, messages = self.check(isr_stack_size=0x1800, isr_stack_guard_size=0x600)
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual([str(m) for m in messages if m.path[:1] == ("memory",)], [])
        self.assertEqual((config.memory.isr_stack_size, config.memory.isr_stack_guard_size), (0x1800, 0x600))
        values = defines(blgen.memory_files(config)[blgen.APP_LINKER_HEADER])
        self.assertEqual(values["NX_MEMORY_APP_ISR_STACK_SIZE"], 0x1800)
        self.assertEqual(values["NX_MEMORY_APP_ISR_STACK_GUARD_SIZE"], 0x600)

    def test_only_one_of_the_two(self):
        config, _ = self.check(isr_stack_guard_size=0x800)
        values = defines(blgen.memory_files(config)[blgen.APP_LINKER_HEADER])
        self.assertNotIn("NX_MEMORY_APP_ISR_STACK_SIZE", values)
        self.assertEqual(values["NX_MEMORY_APP_ISR_STACK_GUARD_SIZE"], 0x800)

    def test_alignment_and_granularity_are_errors_with_path(self):
        for key, value, fragment in (("isr_stack_size", 0x1004, "must be a multiple of 8"),
                                     ("isr_stack_guard_size", 0x410, "multiple of the MPU granularity of 32"),
                                     ("isr_stack_guard_size", 0x10, "multiple of the MPU granularity of 32")):
            with self.subTest(key=key, value=value):
                config, messages = self.check(**{key: value})
                errors = self.findings(messages, "error", key)
                self.assertEqual(len(errors), 1, [str(m) for m in messages])
                self.assertIn(fragment, errors[0].text)
                self.assertEqual(errors[0].file.name, "project.yaml")
                self.assertIsInstance(errors[0].line, int)
                self.assertIsNone(config)
        _, messages = self.check(isr_stack_size=0)
        self.assertTrue([m for m in messages if m.severity == "error" and m.path == APP_PATH + ("isr_stack_size",)],
                        [str(m) for m in messages])

    def test_below_the_default_is_a_warning(self):
        # PLAT-112: a warning only in a QM project (in an ASIL project an error, IsrStackProofTest)
        config, messages = self.check({"integrity": "qm"}, isr_stack_size=0xC00, isr_stack_guard_size=0x200)
        self.assertIsNotNone(config, [str(m) for m in messages])
        stack = self.findings(messages, "warning", "isr_stack_size")
        guard = self.findings(messages, "warning", "isr_stack_guard_size")
        self.assertEqual((len(stack), len(guard)), (1, 1), [str(m) for m in messages])
        self.assertIn("tools/stackuse --strict", stack[0].text)
        self.assertIn("jump over it", guard[0].text)
        # the default itself and anything above it: no message
        _, messages = self.check(isr_stack_size=bswtasks.DEFAULT_ISR_STACK_SIZE,
                                 isr_stack_guard_size=bswtasks.DEFAULT_ISR_STACK_GUARD_SIZE)
        self.assertEqual([str(m) for m in messages if m.path[:2] == APP_PATH], [])

    def test_ram_sum_counts_the_project_value(self):
        # 28 KB application RAM: with the defaults it fits (tools/nxconfig/test_bswtasks.py); a 12 KB interrupt
        # stack no longer does - the RAM sum of bswtasks.py takes the project value
        _, messages = self.check(isr_stack_size=0x3000)
        sums = [m for m in messages if "exceed the application RAM" in m.text]
        self.assertEqual(len(sums), 1, [str(m) for m in messages])
        self.assertIn("interrupt stack 12288 B and guard 1024 B", sums[0].text)


def evidence(used_bytes, method="stackuse"):
    return {"method": method, "used_bytes": used_bytes, "reference": "tools/stackuse report of the test image"}


class IsrStackProofTest(unittest.TestCase):
    """PLAT-112: proof note (S132-1 b), stackuse profile of an ASIL project (S132-2 a), family support (S132-4 a)."""

    setUp = IsrStackProjectValueTest.setUp
    tearDown = IsrStackProjectValueTest.tearDown
    check = IsrStackProjectValueTest.check
    findings = staticmethod(IsrStackProjectValueTest.findings)

    def test_below_the_default_without_proof_is_an_error_in_an_asil_project(self):
        for project in ({}, {"integrity": "asil_b"}):          # left out counts as ASIL (PLAT-107)
            with self.subTest(project=project):
                config, messages = self.check(project, isr_stack_size=0xC00, isr_stack_guard_size=0x200)
                self.assertIsNone(config)
                stack = self.findings(messages, "error", "isr_stack_size")
                self.assertEqual(len(stack), 1, [str(m) for m in messages])
                self.assertIn("without a proof note in an ASIL project", stack[0].text)
                self.assertIn("memory.application.isr_stack_evidence", stack[0].text)
                self.assertEqual(stack[0].file.name, "project.yaml")
                self.assertIsInstance(stack[0].line, int)
                guard = self.findings(messages, "error", "isr_stack_guard_size")
                self.assertEqual(len(guard), 1, [str(m) for m in messages])
                self.assertIn("memory.application.isr_stack_guard_evidence", guard[0].text)

    def test_with_proof_no_message_and_the_value_takes_effect(self):
        config, messages = self.check(isr_stack_size=0xC00, isr_stack_guard_size=0x380,
                                      isr_stack_evidence=evidence(2852, "high_water_mark"),
                                      isr_stack_guard_evidence=evidence(864))
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual([str(m) for m in messages if m.path[:2] == APP_PATH], [])
        self.assertEqual((config.memory.isr_stack_size, config.memory.isr_stack_guard_size), (0xC00, 0x380))
        values = defines(blgen.memory_files(config)[blgen.APP_LINKER_HEADER])
        self.assertEqual(values["NX_MEMORY_APP_ISR_STACK_SIZE"], 0xC00)

    def test_the_stack_must_hold_the_proven_bytes(self):
        # project value too small for its proof, and the default too small for a proof without a project value
        for application, key, fragment in (
                ({"isr_stack_size": 0xC00, "isr_stack_evidence": evidence(3200)}, "isr_stack_evidence",
                 "interrupt stack 0xC00 (project value) is smaller than the 3200 B"),
                ({"isr_stack_guard_evidence": evidence(1100)}, "isr_stack_guard_evidence",
                 "interrupt stack guard 0x400 (default of the linker template) is smaller than the 1100 B")):
            with self.subTest(key=key):
                config, messages = self.check(**application)
                errors = [m for m in messages if m.severity == "error" and m.path == APP_PATH + (key, "used_bytes")]
                self.assertEqual(len(errors), 1, [str(m) for m in messages])
                self.assertIn(fragment, errors[0].text)
                self.assertIsNone(config)

    def test_the_guard_is_proven_only_by_stackuse(self):
        config, messages = self.check(isr_stack_guard_size=0x200,
                                      isr_stack_guard_evidence=evidence(400, "high_water_mark"))
        errors = [m for m in messages if m.severity == "error"
                  and m.path == APP_PATH + ("isr_stack_guard_evidence", "method")]
        self.assertEqual(len(errors), 1, [str(m) for m in messages])
        self.assertIn("only by tools/stackuse", errors[0].text)
        self.assertIsNone(config)

    def test_qm_project_without_proof_warns_and_keeps_the_value(self):
        config, messages = self.check({"integrity": "qm"}, isr_stack_size=0xC00)
        self.assertIsNotNone(config, [str(m) for m in messages])
        warnings = self.findings(messages, "warning", "isr_stack_size")
        self.assertEqual(len(warnings), 1, [str(m) for m in messages])
        self.assertIn("a QM project", warnings[0].text)
        self.assertEqual(config.memory.isr_stack_size, 0xC00)

    def test_asil_project_value_needs_a_stackuse_profile(self):
        (self.tmp / PROJECT).parent.joinpath(memory.STACKUSE_PROFILE_IN_PROJECT).unlink()
        for application, key in (({"isr_stack_size": 0x1800, "isr_stack_guard_size": None}, "isr_stack_size"),
                                 ({"isr_stack_size": None, "isr_stack_guard_size": 0x800}, "isr_stack_guard_size")):
            with self.subTest(key=key):
                config, messages = self.check(**application)
                errors = self.findings(messages, "error", key)
                self.assertEqual(len(errors), 1, [str(m) for m in messages])
                self.assertIn("needs a stackuse profile of its own", errors[0].text)
                self.assertIn("tools/stackuse/profiles/testproject_s32k142_bl.yaml", errors[0].text)
                self.assertIsNone(config)
        # QM project: no profile needed; left out: the template default, backed by the demo profile - no message
        config, messages = self.check({"integrity": "qm"}, isr_stack_size=0x1800)
        self.assertIsNotNone(config, [str(m) for m in messages])
        config, messages = self.check({"integrity": None}, isr_stack_size=None, isr_stack_guard_size=None)
        self.assertEqual([str(m) for m in messages if m.path[:2] == APP_PATH], [])

    def test_profiles_are_looked_for_in_the_project_and_the_running_tree(self):
        cfg = SimpleNamespace(project_file=self.tmp / PROJECT, running_tree=self.tmp,
                              project=SimpleNamespace(project=SimpleNamespace(name="testproject_s32k142_bl")))
        in_project, in_tree = memory.stackuse_profile_candidates(cfg)
        self.assertEqual(memory.stackuse_profiles(cfg), (in_project,))
        in_project.unlink()
        self.assertEqual(memory.stackuse_profiles(cfg), ())
        in_tree.parent.mkdir(parents=True)
        in_tree.write_text("# profile\n", encoding="utf-8")
        self.assertEqual(memory.stackuse_profiles(cfg), (in_tree,))
        self.assertEqual(in_tree, self.tmp / "tools" / "stackuse" / "profiles" / "testproject_s32k142_bl.yaml")
        # the real tree: the demo has its profile there
        demo = SimpleNamespace(project_file=DEMO_PROJECT, running_tree=REPO,
                               project=SimpleNamespace(project=SimpleNamespace(name="evalboard_s32k144_demo")))
        self.assertEqual(memory.stackuse_profiles(demo),
                         (REPO / "tools" / "stackuse" / "profiles" / "evalboard_s32k144_demo.yaml",))

    def test_family_without_a_template_that_reads_the_value_is_an_error(self):
        for read, refused in ((frozenset(), ("isr_stack_size", "isr_stack_guard_size")),
                              (frozenset({memory.ISR_STACK_MACRO}), ("isr_stack_guard_size",))):
            with self.subTest(read=read), mock.patch.object(memory, "family_linker_macros", return_value=read):
                config, messages = self.check(isr_stack_size=0x1800, isr_stack_guard_size=0x800)
                self.assertIsNone(config)
                for key in refused:
                    errors = self.findings(messages, "error", key)
                    self.assertEqual(len(errors), 1, [str(m) for m in messages])
                    self.assertIn("not supported by the family s32k1xx", errors[0].text)
        # without a project value the templates are not even read
        with mock.patch.object(memory, "family_linker_macros", side_effect=AssertionError("read")):
            config, messages = self.check(isr_stack_size=None, isr_stack_guard_size=None)
            self.assertIsNotNone(config, [str(m) for m in messages])

    def test_family_linker_macros_of_the_tree(self):
        def cfg(tree, family):
            return SimpleNamespace(running_tree=tree,
                                   derivative=SimpleNamespace(derivative=SimpleNamespace(family=family)))
        both = frozenset({memory.ISR_STACK_MACRO, memory.ISR_STACK_GUARD_MACRO})
        self.assertEqual(memory.family_linker_macros(cfg(REPO, "s32k1xx")), both)
        for family in ("stm32", "rm4x", "nofamily"):          # no application linker template that reads them
            with self.subTest(family=family):
                self.assertEqual(memory.family_linker_macros(cfg(REPO, family)), frozenset())
        # own tree: a template that reads only the stack, one outside port/**/linkerscript does not count
        script = self.tmp / "platforms" / "fam" / "port" / "main" / "linkerscript" / "application.ld.in"
        script.parent.mkdir(parents=True)
        script.write_text(f"#ifdef {memory.ISR_STACK_MACRO}\n#endif\n", encoding="utf-8")
        (self.tmp / "platforms" / "fam" / "README.md").write_text(memory.ISR_STACK_GUARD_MACRO, encoding="utf-8")
        self.assertEqual(memory.family_linker_macros(cfg(self.tmp, "fam")), frozenset({memory.ISR_STACK_MACRO}))


class DemoUnchangedTest(unittest.TestCase):
    """The eval board demo sets no project value: memoryLayout.h is the reviewed reference byte for byte."""

    # PLAT-62: the reference of the eval board is private (distribution.yaml, PLAT-78), the exported tree skips this
    @unittest.skipUnless(EVAL_DEMO_PROJECT.is_file() and DEMO_REFERENCE.is_file(),
                         "private eval board project and reference not in this tree (exported open tree, PLAT-62)")
    def test_demo_header_unchanged(self):
        config = nxconfig.load(EVAL_DEMO_PROJECT, nxconfig.MCUDB)
        self.assertEqual((config.memory.isr_stack_size, config.memory.isr_stack_guard_size), (None, None))
        header = blgen.memory_files(config, "projects/evalboard_s32k144_demo/project.yaml (board evalboard_s32k144)")
        self.assertEqual(header[blgen.APP_LINKER_HEADER], DEMO_REFERENCE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
