"""Reference output of the safety-relevant RTE generates (PLAT-78, tool-classification.md proposal B, TF1, SSR-081).

rtegen (with dbcgen for CanDb.c/h) and diaggen render each profile; the files of section 4 of
doc/safety/tool-classification.md and the diagnostic and fault memory tables are compared with the reviewed copies in
test/reference/<profile>/:

  RteTasks.h, tasks/async/Config.h   task list, priorities, stacks (scheduling; SM-01 relation)
  tasks/BswTasks.h                   stacks of the BSW, idle and timer task objects, merged BSW tasks (PLAT-26;
                                     own profile test_bsw_tasks with two merges and smaller stacks)
  RteConfig.cpp                      runnable periods and offsets (= SM-05 deadlines), qualifier limits (SM-30), E2E
                                     configuration (SM-31), copies at task boundaries
  CanDb.c, CanDb.h                   CAN pack/unpack of every used signal
  RteDem.h, RteDiag.h                fault memory sources and RTE functions of the diagnostic jobs
  diag/DiagConfig.h, DiagDescription.cpp   diaggen: identifier, routine and security tables of the UDS server

Profiles: three own test profiles (test/dem: two tasks with the BSW task profile test/multitask/tasks.yaml, fault
memory and diagnostic description on the build test project; can_host/qualifier_rte.yaml: E2E profiles P01/P02/P11
and qualifiers with the test DBC; test/c_swc: the hand-written C component LevelMonSwc next to DemoSwc, RTE-20, with
its component header, Rte.c and Rte_Type.h as well; test/calib: calibration objects of RTE-26 with Rte.c,
Rte_Internal.h and RteCalibration.json, and with RteConfig.h and RteParameterChecks.md for the plausibility checks
of SYS-90; test/types: records, arrays, enumerations and fixed point of RTE-04 with RteConfig.h/.cpp and Rte_Type.h -
the signal table and, RTE-29, rte::SIGNAL_SCALINGS of the fixed point rows), the open build test S32K148 as a
project, and the eval board demo
(private project: skipped when it is missing, e.g. in the open export). COM-36: the receive filter, UDS bus and XCP
bus headers of a project with uds.bus CAN1 - own profile on the build test S32K148, and the eval board demo with its
diagnostic CAN FlexCAN2 (16 buffers) and a 29-bit XCP command id in buffers 6/7 (the board check of COM-36).

  ReferenceOutputTest    every profile against its reference, a difference fails with a unified diff
  StabilityTest          a second process with another hash seed, working directory and output directory renders
                         the same reference
  MutantTest             a changed template (rtegen, dbcgen) or changed generator code (fault memory header,
                         diaggen) turns the comparison red
  ReferenceInventoryTest every profile has a reference and every reference a profile; no absolute path inside

Update after a reviewed change of generator, template, component or profile (then review `git diff` of test/reference):
    .venv/Scripts/python tools/rtegen/test_reference_output.py --update [profile ...]
Run: .venv/Scripts/python -m unittest tools/rtegen/test_reference_output.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "bswgen" / "test"))
sys.path.insert(0, str(REPO / "tools" / "diaggen"))

import yaml  # noqa: E402
import rtegen  # noqa: E402
import diaggen  # noqa: E402
import reference_output as ref  # noqa: E402

REFERENCE = HERE / "test" / "reference"
TEST = HERE / "test"
BT148 = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
# PLAT-62: full-featured own test profile - the eval board demo is private and skipped in the open export
FULL = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
MULTI_TASKS = TEST / "multitask" / "tasks.yaml"
UPDATE = "tools/rtegen/test_reference_output.py --update"

FILES = (
    "RteTasks.h",
    "tasks/async/Config.h",
    "RteConfig.cpp",
    "CanDb.c",
    "CanDb.h",
    "RteDem.h",
    "RteDiag.h",
    "diag/DiagConfig.h",
    "DiagDescription.cpp",
    "tasks/BswTasks.h",          # PLAT-26: stacks of the BSW task objects, merged BSW tasks
)
# RTE-20: what rtegen builds from the swc.yaml of a C component - its API header, buffers, access functions and
# parameter storage (Rte.c) and the types (checked for the C profile only)
C_SWC_FILES = ("Rte_LevelMonSwc.h", "Rte.c", "Rte_Type.h")


def _render_dem(out: Path):
    config = rtegen.nxconfig.load(BT148)
    d = diaggen.load_file(TEST / "dem" / "diag.yaml")
    rtegen.generate(TEST / "dem" / "rte.yaml", config, out, REPO, MULTI_TASKS, diag=d.desc)
    diaggen.write(diaggen.plan(d), out)


def _render_qualifier(out: Path):
    rtegen.generate(TEST / "can_host" / "qualifier_rte.yaml", rtegen.nxconfig.load(BT148), out, REPO)


def _render_c_swc(out: Path):
    rtegen.generate(TEST / "c_swc" / "rte.yaml", rtegen.nxconfig.load(BT148), out, REPO)


def _render_calib(out: Path):
    rtegen.generate(TEST / "calib" / "rte.yaml", rtegen.nxconfig.load(BT148), out, REPO)


def _render_types(out: Path):
    rtegen.generate(TEST / "types" / "rte.yaml", rtegen.nxconfig.load(BT148), out, REPO, MULTI_TASKS)


# RTE-29: the signal table with the conversion of the fixed point rows (rte::SIGNAL_SCALINGS) and the types it uses
TYPES_FILES = ("RteConfig.h", "RteConfig.cpp", "Rte_Type.h")


# COM-36: receive filter, diagnostic bus and XCP bus of a project with uds.bus CAN1 (board 2026-09-22 round 3)
RX_FILTER_FILES = ("can/canRxFilterConfiguration.h", "uds/udsBusConfiguration.h", "xcp/xcpCanConfiguration.h")


def _render_uds_can1(project: Path, xcp: dict, edit=None):
    """The project with UDS on CAN1 and `xcp` as project value: both files written next to `out`, paths absolute."""
    def render(out: Path):
        src = out.parent / f"{out.name}_input"
        src.mkdir(parents=True, exist_ok=True)
        data = yaml.safe_load(project.read_text(encoding="utf-8"))
        ecu_file = project.parent / data["project"]["rte"]
        ecu = rtegen.with_resolved_paths(yaml.safe_load(ecu_file.read_text(encoding="utf-8")), ecu_file)
        ecu["uds"] = {"bus": "CAN1"}
        (src / "rte.yaml").write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        data["project"]["board"] = (project.parent / data["project"]["board"]).resolve().as_posix()
        data["project"]["rte"] = "rte.yaml"
        if "diag" in data["project"]:
            data["project"]["diag"] = (project.parent / data["project"]["diag"]).resolve().as_posix()
        data["xcp"] = xcp
        if edit is not None:
            edit(data)
        (src / "project.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        rtegen.generate_project(src / "project.yaml", out, REPO)
    return render


# PLAT-26: stacks and merges of the BSW tasks as project values - two merged tasks (context aliases in async/Config.h,
# no priority of their own), smaller idle/timer/safety stacks (tasks/BswTasks.h)
# PLAT-107: a stack below the default needs a proof note in an ASIL project (the build test has no project.integrity)
_EVIDENCE = {"method": "stackuse", "reference": "test profile test_bsw_tasks (no image, no proof)"}
BSW_TASKS = {"background": {"run_in": "can"}, "sysadmin": {"run_in": "bsp"},
             "idle": {"stack_bytes": 1024, "evidence": dict(_EVIDENCE, used_bytes=512)},
             "timer": {"stack_bytes": 768, "evidence": dict(_EVIDENCE, used_bytes=512)},
             "safety": {"stack_bytes": 1600, "evidence": dict(_EVIDENCE, used_bytes=1208)}}


def _render_bsw_tasks(project: Path, bsw_tasks: dict):
    """The project with `bsw_tasks` as project value, written next to `out` with absolute paths."""
    def render(out: Path):
        src = out.parent / f"{out.name}_input"
        src.mkdir(parents=True, exist_ok=True)
        data = yaml.safe_load(project.read_text(encoding="utf-8"))
        ecu_file = project.parent / data["project"]["rte"]
        ecu = rtegen.with_resolved_paths(yaml.safe_load(ecu_file.read_text(encoding="utf-8")), ecu_file)
        (src / "rte.yaml").write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        data["project"]["board"] = (project.parent / data["project"]["board"]).resolve().as_posix()
        data["project"]["rte"] = "rte.yaml"
        if "diag" in data["project"]:
            data["project"]["diag"] = (project.parent / data["project"]["diag"]).resolve().as_posix()
        data["bsw_tasks"] = bsw_tasks
        (src / "project.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        rtegen.generate_project(src / "project.yaml", out, REPO)
    return render


def _render_project(project: Path):
    def render(out: Path):
        rtegen.generate_project(project, out, REPO)
        diaggen.write(diaggen.plan(diaggen.load_project(project)), out)
    return render


PROFILES = [
    ref.Profile("test_dem", _render_dem, FILES,
                "own: two tasks (BSW profile test/multitask), fault memory and diagnostic description, no SBC",
                (TEST / "dem" / "rte.yaml", TEST / "dem" / "diag.yaml", BT148)),
    ref.Profile("test_can_qualifier", _render_qualifier, FILES,
                "own: qualifiers and E2E P01/P02/P11 on the test DBC can_host/qualifier.dbc",
                (TEST / "can_host" / "qualifier_rte.yaml", BT148)),
    ref.Profile("test_c_swc", _render_c_swc, FILES + C_SWC_FILES,
                "own (RTE-20): C component LevelMonSwc (swc.yaml) and Simulink DemoSwc (ARXML) in two tasks",
                (TEST / "c_swc" / "rte.yaml", TEST / "c_swc" / "LevelMonSwc" / "swc.yaml",
                 TEST / "c_swc" / "LevelMonSwc" / "LevelMonSwc_data.m", BT148)),
    ref.Profile("test_calib", _render_calib, FILES + ("Rte.c", "Rte_Internal.h", "RteCalibration.json", "RteConfig.h",
                                                     "RteParameterChecks.md"),
                "own (RTE-26): calibration objects of stage K1 - axis, curves, map, limits, safety class; SYS-90: "
                "plausibility checks rte::PARAMETER_CHECKS and their report",
                (TEST / "calib" / "rte.yaml", TEST / "calib" / "CalMapSwc" / "swc.yaml",
                 TEST / "calib" / "CalMapSwc" / "CalMapSwc_data.m", BT148)),
    ref.Profile("test_types", _render_types, TYPES_FILES,
                "own (RTE-04/RTE-29): records, arrays, enumerations and fixed point of TypeSwc/TypeSink; SIGNALS "
                "element by element and SIGNAL_SCALINGS (conversion, unit, decimals of the fixed point rows)",
                (TEST / "types" / "rte.yaml", TEST / "types" / "TypeSwc" / "generated" / "arxml",
                 TEST / "types" / "TypeSink" / "generated" / "arxml", BT148)),
    ref.Profile("buildtest_s32k148_min", _render_project(BT148), FILES,
                "open build test project (S32K148, no SBC, default diagnostic description)", (BT148,)),
    ref.Profile("testproject_full", _render_project(FULL), FILES,
                "own (PLAT-62): demo component with E2E, qualifiers, diagnostic description and fault memory on the "
                "full-featured S32K144 test profile, in place of the private eval board in the open tree", (FULL,)),
    ref.Profile("evalboard_s32k144_demo", _render_project(DEMO), FILES,
                "demo of the eval board (private project)", (DEMO,)),
    ref.Profile("test_rx_filter_uds_can1", _render_uds_can1(BT148, {"master_id": 0x6A0, "slave_id": 0x6A1}),
                RX_FILTER_FILES,
                "own (COM-36): build test S32K148 with UDS and XCP on CAN1 (32 buffers: 14 standard, 2 extended); "
                "each id once, repeated over the standard buffers",
                (BT148,)),
    ref.Profile("evalboard_s32k144_uds_can1",
                _render_uds_can1(DEMO, {"master_id": 0x98DA00F1, "slave_id": 0x98DAF100}),   # CAN1 in use.can (COM-05)
                RX_FILTER_FILES,
                "COM-36 board check (private project): demo with UDS on CAN1 = FlexCAN2 (16 buffers: 6 standard, "
                "2 extended) and a 29-bit XCP command id in buffers 6/7",
                (DEMO,)),
    ref.Profile("test_bsw_tasks", _render_bsw_tasks(BT148, BSW_TASKS),
                ("RteTasks.h", "tasks/async/Config.h", "tasks/BswTasks.h"),
                "own (PLAT-26): build test S32K148 with background merged into can, sysadmin into bsp (aliases, no "
                "priority of their own) and smaller idle, timer and safety stacks",
                (BT148, REPO / "ecu" / "configuration" / "taskStacks.yaml")),
]
BY_NAME = {p.name: p for p in PROFILES}

# (template, old, new, profile, file that must differ)
MUTANTS = [
    ("RteTasks.h.j2", "{{ task.stack_bytes }}U){{", "{{ task.stack_bytes // 2 }}U){{", "test_dem", "RteTasks.h"),
    ("Config.h.j2", "{{ task.context }}{{ \" = 1\" if loop.first else \"\" }}",
     "{{ task.context }}{{ \" = 2\" if loop.first else \"\" }}", "test_dem", "tasks/async/Config.h"),
    ("RteConfig.cpp.j2", "{{ r.period_ms }}U, {{ r.offset_ms }}U,", "{{ r.offset_ms }}U, {{ r.period_ms }}U,",
     "test_dem", "RteConfig.cpp"),
    ("CanDb.c.j2", "(data, {{ s.start }}U, {{ s.length }}U, (uint32_t)msg->",
     "(data, {{ s.start + 1 }}U, {{ s.length }}U, (uint32_t)msg->", "test_can_qualifier", "CanDb.c"),
    ("CanDb.h.j2", "#define {{ P }}_TIMEOUT_MS {{ m.timeout_ms }}U", "#define {{ P }}_TIMEOUT_MS {{ m.cycle_ms }}U",
     "test_can_qualifier", "CanDb.h"),
    # SYS-90: the generated plausibility check of a safety-relevant parameter with another count
    ("RteConfig.cpp.j2", "{ {{ c.row }}U, {{ c.parameter.dim }}U,", "{ {{ c.row }}U, {{ c.parameter.dim + 1 }}U,",
     "test_calib", "RteConfig.cpp"),
    # RTE-29: the conversion of a fixed point row of the signal table with another offset
    ("RteConfig.cpp.j2", "{{ s.numerator[0] }}, {{ s.numerator[1] }}", "{{ s.numerator[0] + 1 }}, {{ s.numerator[1] }}",
     "test_types", "RteConfig.cpp"),
    # PLAT-26: a merged BSW task aliased to the wrong context
    ("Config.h.j2", "{{ alias }} = {{ target }},", "{{ alias }} = {{ target }} - 1,", "test_bsw_tasks",
     "tasks/async/Config.h"),
]


def _skip_unless(profile: ref.Profile):
    if not profile.available:
        raise unittest.SkipTest(f"{profile.name}: input missing ({', '.join(map(str, profile.inputs))})")


class ReferenceTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_ref_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertDetected(self, profile: ref.Profile, rel: str, actual: dict):
        diffs = ref.differences(ref.stored(REFERENCE, profile), actual, profile.name)
        self.assertTrue(any(d.startswith(f"--- reference/{profile.name}/{rel}") for d in diffs),
                        f"change not detected in {rel}: {diffs}")


class ReferenceOutputTest(ReferenceTestBase):
    def test_profiles_match_reference(self):
        for profile in PROFILES:
            with self.subTest(profile=profile.name):
                _skip_unless(profile)
                actual = ref.render(profile, self.tmp)
                diffs = ref.differences(ref.stored(REFERENCE, profile), actual, profile.name)
                if diffs:
                    self.fail(f"generated files of {profile.name} differ from the reviewed reference "
                              f"(tools/rtegen/test/reference/{profile.name}).\nIf the change is intended: "
                              f"{UPDATE} {profile.name}, then review the diff.\n\n" + "\n\n".join(diffs))


class LicenceLineTest(ReferenceTestBase):
    """PLAT-120: every file of rtegen, dbcgen and diaggen carries the SPDX line of the generator output exception."""

    PROFILE_NAMES = ("test_dem", "test_can_qualifier", "test_c_swc", "test_calib", "buildtest_s32k148_min")

    def test_every_generated_file_carries_the_line(self):
        for name in self.PROFILE_NAMES:
            with self.subTest(profile=name):
                profile = BY_NAME[name]
                _skip_unless(profile)
                ref.render(profile, self.tmp)
                out = self.tmp / name
                self.assertGreater(len([p for p in out.rglob("*") if p.is_file()]), 8)
                # RteCalibration.json has no comment syntax (genlicense.style_of is None for JSON)
                self.assertEqual([], ref.licence_gaps(out))

    def test_without_the_stamp_the_check_finds_every_file(self):
        """Counter-probe: generators that do not stamp are reported (the check above is not vacuous)."""
        from nxconfig import genlicense
        profile = BY_NAME["test_dem"]
        with unittest.mock.patch.object(genlicense, "stamp", lambda text, path, style=None: text):
            ref.render(profile, self.tmp)
        out = self.tmp / profile.name
        files = [p for p in out.rglob("*") if p.is_file() and genlicense.style_of(p.name) is not None]
        self.assertEqual(len(files), len(ref.licence_gaps(out)))


class StabilityTest(ReferenceTestBase):
    def test_other_process_renders_the_same(self):
        """Another hash seed, working directory, output directory and without NX_* variables: same bytes."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("NX_")}
        env.update(PYTHONHASHSEED="815", PYTHONUTF8="1")
        dump = self.tmp / "dump"
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--dump", str(dump)],
                                cwd=self.tmp, env=env, capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for profile in PROFILES:
            if not profile.available:
                continue
            with self.subTest(profile=profile.name):
                diffs = ref.differences(ref.stored(REFERENCE, profile), ref.stored(dump, profile), profile.name)
                self.assertEqual(diffs, [], "\n\n".join(diffs))


class MutantTest(ReferenceTestBase):
    def test_changed_template_turns_red(self):
        for i, (template, old, new, name, rel) in enumerate(MUTANTS):
            profile = BY_NAME[name]
            with self.subTest(template=template, new=new):
                _skip_unless(profile)
                with ref.mutated_template(template, old, new):
                    actual = ref.render(profile, self.tmp / f"m{i}")
                self.assertDetected(profile, rel, actual)

    def test_every_checked_template_has_a_mutant(self):
        templates = {p.name for d in (HERE / "templates", REPO / "tools" / "dbcgen" / "templates")
                     for p in d.glob("*.j2")}
        rendered_by = {Path(f).name + ".j2" for f in FILES} & templates
        self.assertEqual(rendered_by, {m[0] for m in MUTANTS})

    def test_changed_fault_memory_generator_turns_red(self):
        original = rtegen.render_dem_header

        def mutant(plan, source):
            return original(plan, source).replace("U", "UL", 1)

        profile = BY_NAME["test_dem"]
        with unittest.mock.patch.object(rtegen, "render_dem_header", mutant):
            actual = ref.render(profile, self.tmp)
        self.assertDetected(profile, "RteDem.h", actual)

    def test_changed_diag_generator_turns_red(self):
        original = diaggen.generate_code

        def mutant(d):
            g = original(d)
            g.source_cpp = g.source_cpp.replace("0x", "0X", 1)
            return g

        profile = BY_NAME["test_dem"]
        with unittest.mock.patch.object(diaggen, "generate_code", mutant):
            actual = ref.render(profile, self.tmp)
        self.assertDetected(profile, "DiagDescription.cpp", actual)

    def test_changed_c_component_reader_turns_red(self):
        """RTE-20: the model rtegen builds from swc.yaml is covered - here the C function of a runnable."""
        profile = BY_NAME["test_c_swc"]
        with unittest.mock.patch.object(rtegen.swc_yaml.Swc, "symbol_of", lambda self, runnable: f"{runnable}_{self.name}"):
            actual = ref.render(profile, self.tmp)
        self.assertDetected(profile, "RteConfig.cpp", actual)
        self.assertDetected(profile, "Rte_LevelMonSwc.h", actual)

    def test_changed_rte_diag_generator_turns_red(self):
        original = rtegen.rteapi.render_header

        def mutant(*args, **kwargs):
            return original(*args, **kwargs) + "// changed\n"

        profile = BY_NAME["test_dem"]
        with unittest.mock.patch.object(rtegen.rteapi, "render_header", mutant):
            actual = ref.render(profile, self.tmp)
        self.assertDetected(profile, "RteDiag.h", actual)


    def test_changed_rx_filter_generator_turns_red(self):
        """COM-36: filters without their repeats (the queue of equal ids) change the reference of both CAN1 profiles."""
        original = rtegen.can_rx_filters

        def mutant(ids, std=rtegen.FLEXCAN_RX_BUFS_STD, ext=rtegen.FLEXCAN_RX_BUFS_EXT):
            return [f for f in original(ids, std, ext) if f.repeats is None]

        for name in ("test_rx_filter_uds_can1", "evalboard_s32k144_uds_can1"):
            profile = BY_NAME[name]
            with self.subTest(profile=name):
                _skip_unless(profile)
                with unittest.mock.patch.object(rtegen, "can_rx_filters", mutant):
                    actual = ref.render(profile, self.tmp / name)
                self.assertDetected(profile, "can/canRxFilterConfiguration.h", actual)


class ReferenceInventoryTest(unittest.TestCase):
    def test_every_profile_has_a_reference_and_back(self):
        stored = {p.name for p in REFERENCE.iterdir() if p.is_dir()}
        self.assertEqual(stored - set(BY_NAME), set(), "reference without profile")
        for profile in PROFILES:
            if profile.available:
                self.assertIn(profile.name, stored, f"no reference - run {UPDATE} {profile.name}")

    def test_every_checked_file_is_in_some_reference(self):
        present = {rel for p in PROFILES for rel in ref.stored(REFERENCE, p)}
        self.assertEqual(set(FILES) - present, set())

    def test_no_machine_path_in_the_reference(self):
        pattern = re.compile(r"[A-Za-z]:[\\/]|/home/|/tmp/|<out>|<repo>")
        for path in REFERENCE.rglob("*"):
            if path.is_file():
                with self.subTest(path=path.relative_to(REFERENCE).as_posix()):
                    self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")))


def main(argv: list[str]) -> int:
    work = Path(tempfile.mkdtemp(prefix="nx_ref_"))
    try:
        if argv[:1] == ["--update"]:
            return ref.update_main(argv[1:], PROFILES, REFERENCE, work)
        if argv[:1] == ["--dump"] and len(argv) == 2:
            for profile in PROFILES:
                if profile.available:
                    ref.write(Path(argv[1]), profile, ref.render(profile, work))
            return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)
    unittest.main(argv=[sys.argv[0], *argv])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
