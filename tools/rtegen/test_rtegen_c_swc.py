# SPDX-License-Identifier: Apache-2.0
"""Host tests of hand-written C components in the RTE generator (RTE-20).

Test profile tools/rtegen/test/c_swc: the C component LevelMonSwc (swc.yaml implementation: c) next to the Simulink
component DemoSwc on the build test project buildtest_s32k148_min (no SBC, two CAN buses) - not the eval board.

  CSwcModelTest   rtegen builds the same component model from swc.yaml as from the ARXML (DemoSwc described as C:
                  every generate byte-equal except the C function names of the runnables), a Simulink component with
                  swc.yaml stays on the ARXML, swc.yaml findings with their own file, YAML path and line in validate(),
                  the connection, task and parameter rules of the ARXML path, duplicate C functions, no model hash
                  warning, rte_components.cmake (C sources apart, unchanged file without C components)
  CSwcHostTest    compiles the generated RTE with LevelMonSwc.c and the Embedded Coder code of DemoSwc and runs
                  test/c_swc/rte_c_swc_host_test.cpp

  CalibrationTest RTE-26: calibration objects of swc.yaml (profile test/calib: axis, curves, map, value table,
                  limits, safety class) - records of own axes, stored limits, rows of rte::PARAMETERS,
                  RteCalibration.json, layout identifier, rounding errors; a Simulink component (test/param) whose
                  swc.yaml definition is checked against its ARXML (type, dimension, scaling, missing parameter)

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_c_swc.py
"""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "test" / "c_swc"))

import rtegen  # noqa: E402
import target_build  # noqa: E402  (test/c_swc: the derived project of the profile)

PROFILE = HERE / "test" / "c_swc"
ECU = rtegen.with_resolved_paths(
    yaml.safe_load((PROFILE / "rte.yaml").read_text(encoding="utf-8")), PROFILE / "rte.yaml")
BT148_FILE = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
BT148_RTE = BT148_FILE.parent / "rte.yaml"
BT148 = rtegen.nxconfig.load(BT148_FILE)
TASKS = REPO / "ecu" / "asyncCoreConfiguration" / "tasks.yaml"
FIXTURE = REPO / "tools" / "swc" / "test" / "fixtures" / "DemoSwc"   # DemoSwc as swc.yaml (RTE-07)
LEVELMON = PROFILE / "LevelMonSwc"


def bt148_ecu() -> dict:
    """The ECU description of the build-test project, its paths pinned to the components they name (RTE-22)."""
    return rtegen.with_resolved_paths(yaml.safe_load(BT148_RTE.read_text(encoding="utf-8")), BT148_RTE)


def components_under(ecu: dict, root: Path) -> dict:
    """The description with its components looked up under `root`, the way `--repo` did before RTE-22.

    Paths are relative to the rte.yaml now, and these tests write the description into a scratch folder while
    varying the *root* the components lie under (once the tree, once a copy in %TEMP%). A relative path is taken
    as relative to that root, as it was before; a path inside the tree is moved to the same place under it, so a
    copied component is found where the test put it. The result states the format, so the fixtures below stay
    about components and connections and do not each repeat `schema_version`.
    """
    result = {"schema_version": rtegen.FILE_RELATIVE_PATHS, **copy.deepcopy(ecu)}
    for entry in result.get("components") or []:
        here = Path(str(entry.get("path", "")))
        if not here.is_absolute():
            entry["path"] = (Path(root) / here).as_posix()
            continue
        try:
            entry["path"] = (Path(root) / here.relative_to(REPO)).as_posix()
        except ValueError:
            entry["path"] = here.as_posix()
    return result

C_SWC = """version: 1
name: {name}
implementation: c
runnables:
  - {{ name: {runnable}, period_ms: 10 }}
ports:
  - {{ name: In, direction: in, type: {type}, runnables: [{runnable}] }}
  - {{ name: Out, direction: out, type: uint16, runnables: [{runnable}] }}
"""


def c_component(root: Path, name: str, runnable: str = "Run10ms", type_: str = "uint16", source: bool = True) -> Path:
    """A small C component <root>/<name> (swc.yaml and, with source, a complete src/<name>.c)."""
    folder = root / name
    (folder / "src").mkdir(parents=True)
    (folder / "swc.yaml").write_text(C_SWC.format(name=name, runnable=runnable, type=type_), encoding="utf-8")
    if source:
        (folder / "src" / f"{name}.c").write_text(
            f'#include "Rte_{name}.h"\nvoid {name}_Init(void) {{}}\n'
            f"void {name}_{runnable}(void)\n{{\n    {type_} v = 0;\n    (void)Rte_Read_In_In(&v);\n"
            f"    (void)Rte_Write_Out_Out((uint16)v);\n}}\n", encoding="utf-8")
    return folder


class CSwcModelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_cswc_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generate(self, ecu: dict, repo: Path = REPO, out: str = "out"):
        path = self.tmp / f"ecu_{out}" / "rte.yaml"     # same file name: the generates name their source
        path.parent.mkdir(exist_ok=True)
        path.write_text(yaml.safe_dump(components_under(ecu, repo), sort_keys=False), encoding="utf-8")
        return rtegen.generate(path, BT148, self.tmp / out, repo, TASKS)

    def files(self, out: str) -> dict:
        root = self.tmp / out
        return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()}

    def expect_error(self, ecu: dict, fragment: str, repo: Path = REPO):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.generate(ecu, repo)
        self.assertIn(fragment, str(ctx.exception))

    def two_components(self, **extra) -> dict:
        """ECU with the C components A and B (repo root self.tmp), B reads A."""
        ecu = {"components": [{"name": "A", "path": "A"}, {"name": "B", "path": "B"}],
               "connections": {"A.In.In": "rte.init", "A.Out.Out": "rte", "B.In.In": "A.Out.Out", "B.Out.Out": "rte"}}
        ecu.update(extra)
        return ecu

    # ---- TOOL-134: names are limited by distinctness, not by length ----
    def test_long_port_names_generate(self):
        """A 32-character port (user test step 04) gives RTE functions of more than 63 characters: allowed."""
        long_in, long_out = "SysNvm_ti_OperatingMinutesNvRead", "SysNvm_ti_OperatingMinutesNvWrite"
        folder = self.tmp / "LongSwc"
        (folder / "src").mkdir(parents=True)
        (folder / "swc.yaml").write_text(
            C_SWC.format(name="LongSwc", runnable="Run10ms", type="uint16").replace("name: In,", f"name: {long_in},")
            .replace("name: Out,", f"name: {long_out},"), encoding="utf-8")
        (folder / "src" / "LongSwc.c").write_text(
            '#include "Rte_LongSwc.h"\nvoid LongSwc_Init(void) {}\nvoid LongSwc_Run10ms(void)\n{\n    uint16 v = 0;\n'
            f"    (void)Rte_Read_{long_in}_{long_in}(&v);\n    (void)Rte_Write_{long_out}_{long_out}(v);\n}}\n",
            encoding="utf-8")
        ecu = {"components": [{"name": "LongSwc", "path": "LongSwc"}],
               "connections": {f"LongSwc.{long_in}.{long_in}": "rte.init", f"LongSwc.{long_out}.{long_out}": "rte"}}
        self.generate(ecu, self.tmp)
        header = self.files("out")["Rte_LongSwc.h"]
        name = f"Rte_Write_LongSwc_{long_out}_{long_out}"
        self.assertGreater(len(name), 63)
        self.assertIn(f"#define Rte_Write_{long_out}_{long_out} {name}", header)

    def test_identifiers_must_be_distinct_in_63_characters(self):
        """The rule of swc_yaml over everything rtegen generates (C/C++ only), with both names and lengths."""
        a, b = "Rte_Cpy_" + "x" * 60 + "_A", "Rte_Cpy_" + "x" * 60 + "_B"
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.check_identifiers({"Rte.c": f"int {a};\nint {b};\n"})
        self.assertIn(f"{b} (70 characters) is not distinct from {a} (70 characters) in the first 63",
                      str(ctx.exception))
        rtegen.check_identifiers({"Rte_Qlf.m": f"{a} = 1;\n{b} = 2;\n"})     # MATLAB files are no C

    # ---- the same model as from the ARXML ----
    def test_demoswc_as_c_component_gives_the_same_rte(self):
        """DemoSwc from swc.yaml (fixture of RTE-07, implementation: c) against DemoSwc from its ARXML: every
        generated file is byte-equal except the C function names of the periodic runnables (C: <Swc>_<Runnable>)
        and the source list of rte_components.cmake."""
        folder = self.tmp / "swc" / "DemoSwc"
        folder.mkdir(parents=True)
        text = (FIXTURE / "swc.yaml").read_text(encoding="utf-8")
        (folder / "swc.yaml").write_text(text.replace("runnables:\n", "implementation: c\nrunnables:\n", 1),
                                         encoding="utf-8")
        shutil.copy(FIXTURE / "DemoSwc_data.m", folder)
        ecu = bt148_ecu()
        self.generate(ecu, REPO, "arxml")
        components, _, _ = self.generate(ecu, self.tmp, "c")
        self.assertEqual({r.symbol for r in components["DemoSwc"].runnables},
                         {"DemoSwc_Init", "DemoSwc_Run10ms", "DemoSwc_Run100ms"})
        arxml, c = self.files("arxml"), self.files("c")
        self.assertEqual(set(arxml), set(c))
        rename = {"&Run10ms,": "&DemoSwc_Run10ms,", "&Run100ms,": "&DemoSwc_Run100ms,",
                  "void Run10ms(void)": "void DemoSwc_Run10ms(void)", "void Run100ms(void)": "void DemoSwc_Run100ms(void)"}
        for name in sorted(arxml):
            if name == "rte_components.cmake":
                continue
            expected = arxml[name]
            for old, new in rename.items():
                expected = expected.replace(old, new)
            with self.subTest(file=name):
                self.assertEqual(c[name], expected)
        self.assertIn("RTE_C_COMPONENT_SOURCE_DIRS", c["rte_components.cmake"])
        self.assertIn(f'"{(folder / "src").as_posix()}"', c["rte_components.cmake"])

    def test_simulink_component_with_swc_yaml_stays_on_the_arxml(self):
        """implementation: simulink (or no key): the ARXML counts, the rest of swc.yaml is not even checked (after
        swc_create the model is the master) - the generate is byte-equal to the one without swc.yaml."""
        ecu = bt148_ecu()
        self.generate(ecu, REPO, "plain")
        folder = self.tmp / "swc" / "DemoSwc"
        shutil.copytree(REPO / "swc" / "DemoSwc" / "generated", folder / "generated")
        shutil.copy(REPO / "swc" / "DemoSwc" / "DemoSwc_data.m", folder)
        for i, text in enumerate(("implementation: simulink\nname: Stale\nports: 5\n", "name: Stale\n")):
            (folder / "swc.yaml").write_text(text, encoding="utf-8")
            self.generate(ecu, self.tmp, f"sim{i}")
            plain, sim = self.files("plain"), self.files(f"sim{i}")
            plain.pop("rte_components.cmake")
            self.assertIn("generated/src", sim.pop("rte_components.cmake"))
            self.assertEqual(sim, plain)

    def test_profile_model(self):
        components, buffers, tasks = self.generate(ECU)
        comp = components["LevelMonSwc"]
        self.assertEqual([(r.name, r.symbol, r.period_ms) for r in comp.runnables],
                         [("LevelMonSwc_Init", "LevelMonSwc_Init", None),
                          ("Run10ms", "LevelMonSwc_Run10ms", 10), ("Run100ms", "LevelMonSwc_Run100ms", 100)])
        self.assertTrue(comp.access_points)
        self.assertEqual(comp.runnables[1].accesses, {"Level_mV.Level_mV", "Filtered_mV.Filtered_mV",
                                                      "Above.Above", "LevelQlf.LevelQlf"})
        self.assertEqual(comp.runnables[2].accesses, {"Crossings.Crossings", "Scaled.Scaled"})
        gain = comp.parameters["CalPrm.Gain"]
        self.assertEqual((gain.type.native, gain.numerator, gain.denominator, gain.raw), ("uint16", (0, 1), 1000, [1500]))
        self.assertEqual(comp.parameters["CalPrm.Weights"].raw, [4, 2, 1, 1])
        self.assertEqual(comp.parameters["CalPrm.Gain"].runnables, {"Run100ms"})
        # Simulink -> C over the task boundary: a copy per task
        self.assertTrue(buffers["DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV"].shared)
        header = (self.tmp / "out" / "Rte_LevelMonSwc.h").read_text(encoding="utf-8")
        for line in ("#define Rte_Read_Level_mV_Level_mV Rte_Read_LevelMonSwc_Level_mV_Level_mV",
                     "extern Std_ReturnType Rte_Write_LevelMonSwc_Above_Above(boolean data);",
                     "extern uint8* Rte_Prm_LevelMonSwc_CalPrm_Weights(void); /* 4 elements */",
                     "extern void LevelMonSwc_Run100ms(void);"):
            self.assertIn(line, header)

    def test_fixed_point_with_offset(self):
        folder = c_component(self.tmp, "A")
        text = (folder / "swc.yaml").read_text(encoding="utf-8")
        (folder / "swc.yaml").write_text(text + "parameters:\n  - { name: Temp, type: sint16, lsb: 0.1, offset: -40, "
                                                "runnables: [Run10ms] }\n", encoding="utf-8")
        (folder / "A_data.m").write_text("Temp = 25.04;\n", encoding="utf-8")
        components, _, _ = self.generate({"components": [{"name": "A", "path": "A"}],
                                          "connections": {"A.In.In": "rte.init", "A.Out.Out": "rte"}}, self.tmp)
        temp = components["A"].parameters["CalPrm.Temp"]
        # physical = (-400 + stored) / 10: 25.04 -> 650.4 -> 650
        self.assertEqual((temp.numerator, temp.denominator, temp.raw), ((-400, 1), 10, [650]))

    # ---- the rules of the ARXML path apply ----
    def test_task_rules(self):
        ecu = copy.deepcopy(ECU)
        ecu["tasks"][0]["runnables"] = ecu["tasks"][0]["runnables"][:2]
        self.expect_error(ecu, "tasks: runnable LevelMonSwc.Run100ms is not assigned to a task")
        ecu = copy.deepcopy(ECU)
        ecu["tasks"][0]["runnables"][2] = {"runnable": "LevelMonSwc.Run100ms", "offset_ms": 105}
        self.expect_error(ecu, "tasks[0].runnables[2].offset_ms: 105 ms is no multiple of the period 10 ms")

    def test_connection_rules(self):
        ecu = copy.deepcopy(ECU)
        ecu["connections"]["DemoSwc.PcCommandQlf.PcCommandQlf"] = "LevelMonSwc.Crossings.Crossings"
        self.expect_error(ecu, "connection DemoSwc.PcCommandQlf.PcCommandQlf -> LevelMonSwc.Crossings.Crossings: "
                               "type uint8 != uint16")
        ecu = copy.deepcopy(ECU)
        del ecu["connections"]["LevelMonSwc.Level_mV.Level_mV"]
        # RTE-27: no error any more - a warning, the port reads its init value from a constant buffer
        self.assertTrue(self.generate(ecu)[1]["LevelMonSwc.Level_mV.Level_mV"].constant)
        ecu = copy.deepcopy(ECU)
        ecu["connections"]["LevelMonSwc.CalPrm.Gain"] = "rte"
        self.expect_error(ecu, "CalPrm.Gain is a calibration parameter; it is not connected, its value comes from "
                               "LevelMonSwc_data.m")

    def test_parameter_values_are_checked(self):
        folder = self.tmp / "LevelMonSwc"
        shutil.copytree(LEVELMON, folder)
        data = folder / "LevelMonSwc_data.m"
        data.write_text(data.read_text(encoding="utf-8").replace("Gain = 1.5;", "Gain = 70;"), encoding="utf-8")
        ecu = copy.deepcopy(ECU)
        ecu["components"][1]["path"] = "LevelMonSwc"
        shutil.copytree(REPO / "swc" / "DemoSwc", self.tmp / "swc" / "DemoSwc")
        self.expect_error(ecu, "70 does not fit uint16", self.tmp)

    def test_duplicate_c_function(self):
        c_component(self.tmp, "A_B", runnable="Run")
        c_component(self.tmp, "A", runnable="B_Run")
        ecu = {"components": [{"name": "A_B", "path": "A_B"}, {"name": "A", "path": "A"}],
               "connections": {"A_B.In.In": "rte.init", "A_B.Out.Out": "rte", "A.In.In": "rte.init", "A.Out.Out": "rte"}}
        self.expect_error(ecu, "components: runnable A.B_Run and A_B.Run have the same C function A_B_Run", self.tmp)

    def test_name_must_match(self):
        c_component(self.tmp, "A")
        ecu = {"components": [{"name": "Other", "path": "A"}], "connections": {}}
        self.expect_error(ecu, "A/swc.yaml describes component A", self.tmp)

    def test_swc_yaml_errors_carry_their_file_path_and_line(self):
        c_component(self.tmp, "A", type_="uint12")
        ecu_file = self.tmp / "rte.yaml"
        ecu_file.write_text(yaml.safe_dump({"schema_version": 2,      # RTE-22: A lies next to this file
                                            "components": [{"name": "A", "path": "A"}],
                                            "connections": {"A.In.In": "rte.init", "A.Out.Out": "rte"}}),
                            encoding="utf-8")
        project = target_build.project_file(self.tmp / "p", ecu_file)
        messages = rtegen.validate(project, self.tmp, tasks_manifest=TASKS)
        found = [m for m in messages if m.severity == "error"]
        self.assertEqual(len(found), 1, [str(m) for m in messages])
        self.assertEqual(found[0].file, self.tmp / "A" / "swc.yaml")
        self.assertEqual(found[0].path, ("ports", 0, "type"))
        self.assertEqual(found[0].line, 7)
        # an unknown implementation: same, at the key
        (self.tmp / "A" / "swc.yaml").write_text("name: A\nimplementation: rust\n", encoding="utf-8")
        found = [m for m in rtegen.validate(project, self.tmp, tasks_manifest=TASKS) if m.severity == "error"]
        self.assertEqual([(m.file.name, m.path, m.line) for m in found], [("swc.yaml", ("implementation",), 2)])
        self.assertIn("implementation 'rust' must be one of simulink, c", found[0].text)

    def test_validate_of_the_profile_is_clean_and_reports_source_findings(self):
        project = target_build.project_file(self.tmp / "p")
        self.assertEqual([str(m) for m in rtegen.validate(project) if m.severity == "error"], [])
        # a runnable function missing in the C source: a warning with the swc.yaml line, never an error
        folder = self.tmp / "LevelMonSwc"
        shutil.copytree(LEVELMON, folder)
        source = folder / "src" / "LevelMonSwc.c"
        source.write_text(source.read_text(encoding="utf-8").replace("void LevelMonSwc_Run100ms(void)",
                                                                     "static void LevelMonSwc_Report(void)"),
                          encoding="utf-8")
        shutil.copytree(REPO / "swc" / "DemoSwc", self.tmp / "swc" / "DemoSwc")
        ecu = copy.deepcopy(ECU)
        ecu["components"][1]["path"] = "LevelMonSwc"
        (self.tmp / "rte.yaml").write_text(yaml.safe_dump(ecu), encoding="utf-8")
        project = target_build.project_file(self.tmp / "q", self.tmp / "rte.yaml")
        messages = rtegen.validate(project, self.tmp, tasks_manifest=TASKS)
        self.assertEqual([m for m in messages if m.severity == "error"], [])
        missing = [m for m in messages if "LevelMonSwc_Run100ms" in m.text]
        self.assertEqual(len(missing), 1, [str(m) for m in messages])
        self.assertEqual((missing[0].severity, missing[0].file, missing[0].path),
                         ("warning", folder / "swc.yaml", ("runnables", 1, "name")))

    def test_no_model_hash_warning_for_c_components(self):
        """A stray .slx with a stale model.sha256 in a C component folder is no reason for the swc_build hint."""
        folder = c_component(self.tmp, "A")
        (folder / "A.slx").write_bytes(b"model")
        (folder / "generated").mkdir()
        (folder / "generated" / "model.sha256").write_text("0" * 64 + "\n", encoding="ascii")
        self.assertIs(rtegen.model_is_current(folder, "A"), False)
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.generate({"components": [{"name": "A", "path": "A"}],
                           "connections": {"A.In.In": "rte.init", "A.Out.Out": "rte"}}, self.tmp)
        self.assertNotIn("swc_build", err.getvalue())

    # ---- build integration ----
    def test_rte_components_cmake(self):
        c_component(self.tmp, "A")
        c_component(self.tmp, "B")
        self.generate(self.two_components(), self.tmp)
        text = (self.tmp / "out" / "rte_components.cmake").read_text(encoding="utf-8")
        self.assertIn("set(RTE_COMPONENT_SOURCE_DIRS\n)\n", text)     # nothing for ecu/rte (Embedded Coder rules)
        self.assertIn(f'set(RTE_C_COMPONENT_SOURCE_DIRS\n    "{(self.tmp / "A" / "src").as_posix()}"\n'
                      f'    "{(self.tmp / "B" / "src").as_posix()}"\n)\n', text)
        self.assertIn(f'    "{(self.tmp / "A" / "swc.yaml").as_posix()}"\n', text)
        # a project without C components: the file as before RTE-20
        self.generate(bt148_ecu(), REPO, "sim")
        text = (self.tmp / "sim" / "rte_components.cmake").read_text(encoding="utf-8")
        self.assertNotIn("RTE_C_COMPONENT", text)
        self.assertTrue(text.endswith("set(RTE_APPLICATION_TX_GATE FALSE)\n"))

    def test_application_cmake_builds_c_components_as_application_code(self):
        text = (REPO / "ecu" / "application" / "CMakeLists.txt").read_text(encoding="utf-8")
        block = text[text.index("if (RTE_C_COMPONENT_SOURCE_DIRS)"):]
        block = block[:block.index("endif ()\n\n") + 8]
        self.assertIn('file(GLOB _sources CONFIGURE_DEPENDS "${dir}/*.c")', block)
        self.assertIn("target_sources(app.ecu PRIVATE $<TARGET_OBJECTS:swcCComponents>)", block)
        self.assertIn("CMAKE_CONFIGURE_DEPENDS ${RTE_C_COMPONENT_INPUTS}", block)
        self.assertNotIn("COMPILE_WARNING_AS_ERROR", block)     # the rule of the application: warnings are errors


class CSwcHostTest(unittest.TestCase):
    """Generated RTE + LevelMonSwc.c + DemoSwc (Embedded Coder) + test/c_swc/rte_c_swc_host_test.cpp."""

    def test_c_component_runtime(self):
        tmp = Path(tempfile.mkdtemp(prefix="nx_cswc_host_"))
        try:
            out = tmp / "gen"
            rtegen.generate(PROFILE / "rte.yaml", BT148, out, REPO)
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{REPO / 'swc/DemoSwc/generated/src'}"]
            objects = []
            # the hand-written component is held to more than the target build: -Wconversion and -pedantic too
            for src, extra in ((out / "Rte.c", []), (REPO / "swc/DemoSwc/generated/src/DemoSwc.c", []),
                               (LEVELMON / "src" / "LevelMonSwc.c", ["-Wconversion", "-Wsign-conversion", "-pedantic"])):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *extra, *includes,
                                          "-c", str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))
            exe = tmp / "rte_c_swc_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", *includes,
                                      str(out / "RteConfig.cpp"), str(PROFILE / "rte_c_swc_host_test.cpp"),
                                      *objects, "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


ARRAYS = HERE / "test" / "arrays"          # RTE-25: array ports between two C components


def zig_host_test(test: unittest.TestCase, out: Path, tmp: Path, sources: list, cpp: Path, includes: list) -> str:
    """Compiles the generated RTE, the component sources and a host test with zig and runs it; returns stdout."""
    zig = [sys.executable, "-m", "ziglang"]
    includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                f"-I{HERE / 'test' / 'can_host' / 'stubs'}", *includes]
    objects = []
    strict = ["-Wconversion", "-Wsign-conversion", "-pedantic"]      # hand-written components, as for LevelMonSwc
    for src, extra in [(out / "Rte.c", [])] + [(s, strict) for s in sources]:
        obj = tmp / (src.stem + ".o")
        r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *extra, *includes,
                                  "-c", str(src), "-o", str(obj)], capture_output=True, text=True)
        test.assertEqual(r.returncode, 0, r.stderr[-3000:])
        objects.append(str(obj))
    exe = tmp / (cpp.stem + ".exe")
    r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", *includes,
                              str(out / "RteConfig.cpp"), str(cpp), *objects, "-o", str(exe)],
                       capture_output=True, text=True)
    test.assertEqual(r.returncode, 0, r.stderr[-3000:])
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    print("\n" + r.stdout.strip(), file=sys.stderr)
    test.assertEqual(r.returncode, 0, r.stdout[-3000:])
    return r.stdout


class CArrayTest(unittest.TestCase):
    """RTE-25: array ports (swc.yaml dimension, a number or a name of the init data) - profile test/arrays."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_arrays_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def copy_profile(self) -> Path:
        """The profile in a scratch folder, so a test may change a swc.yaml; returns its rte.yaml."""
        shutil.copytree(ARRAYS, self.tmp / "arrays", ignore=shutil.ignore_patterns("*.cpp"))
        return self.tmp / "arrays" / "rte.yaml"

    def refused(self, ecu_file: Path) -> tuple[str, list]:
        """(RteError of generate, error messages of validate) - both must say the same (RTE-24)."""
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.generate(ecu_file, BT148, self.tmp / "out", REPO, TASKS)
        project = target_build.project_file(self.tmp / "p", ecu_file)
        errors = [m for m in rtegen.validate(project, REPO, tasks_manifest=TASKS) if m.severity == "error"]
        self.assertEqual([m.text for m in errors], [str(ctx.exception)])
        return str(ctx.exception), errors

    def test_model_of_the_profile(self):
        components, buffers, _tasks = rtegen.generate(ARRAYS / "rte.yaml", BT148, self.tmp / "out", REPO, TASKS)
        src = components["CellSrcSwc"].ports["CellVoltages.CellVoltages"].type
        sink = components["CellSinkSwc"].ports["CellVoltages.CellVoltages"].type
        # the symbolic dimension pack.number_cells (2 * 3 in CellSrcSwc_init.m) is a number in the model
        self.assertEqual((src.category, src.size, src.name, src.element.native), ("ARRAY", 6, "rt_Array_uint16_6",
                                                                                  "uint16"))
        self.assertEqual(sink.signature(), src.signature())
        buffer = buffers["CellSrcSwc.CellVoltages.CellVoltages"]
        self.assertTrue(buffer.array and buffer.shared)      # crosses the task boundary as a copy
        files = {p.name: p.read_text(encoding="utf-8") for p in (self.tmp / "out").glob("*.*")}
        self.assertIn("typedef uint16 rt_Array_uint16_6[6];", files["Rte_Type.h"])
        self.assertNotIn("number_cells", "".join(files.values()))     # the generate has the number only
        self.assertIn("Rte_Write_CellSrcSwc_CellVoltages_CellVoltages(const uint16* data);", files["Rte_CellSrcSwc.h"])
        self.assertIn("Rte_Read_CellSinkSwc_CellVoltages_CellVoltages(uint16* data);", files["Rte_CellSinkSwc.h"])

    def test_different_dimension_is_refused(self):
        ecu_file = self.copy_profile()
        sink = ecu_file.parent / "CellSinkSwc" / "swc.yaml"
        sink.write_text(sink.read_text(encoding="utf-8").replace("runnables: [Run20ms], dimension: 6 }",
                                                                 "runnables: [Run20ms], dimension: 5 }", 1),
                        encoding="utf-8")
        text, errors = self.refused(ecu_file)
        self.assertIn("connection CellSinkSwc.CellVoltages.CellVoltages -> CellSrcSwc.CellVoltages.CellVoltages: "
                      "dimension 5 of the required port does not match dimension 6 of the provided port", text)
        self.assertEqual(errors[0].yaml_path, "connections.CellSinkSwc.CellVoltages.CellVoltages")
        # a scalar reader of the array: the same rule
        sink.write_text(sink.read_text(encoding="utf-8").replace("runnables: [Run20ms], dimension: 5 }",
                                                                 "runnables: [Run20ms] }", 1), encoding="utf-8")
        text, _ = self.refused(ecu_file)
        self.assertIn("dimension 1 of the required port does not match dimension 6", text)

    def test_array_on_a_can_signal_is_refused(self):
        ecu_file = self.copy_profile()
        ecu = yaml.safe_load(ecu_file.read_text(encoding="utf-8"))
        ecu["connections"]["CellSinkSwc.CellEcho.CellEcho"] = "can.tx.CellEcho"
        ecu_file.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        text, _ = self.refused(ecu_file)
        self.assertIn("is an array port with 6 elements, a CAN signal carries one scalar value. Next step: give each "
                      "element that goes on the bus its own scalar port", text)

    def test_symbolic_dimension_without_value_is_refused(self):
        ecu_file = self.copy_profile()
        init = ecu_file.parent / "CellSrcSwc" / "CellSrcSwc_init.m"
        init.write_text("load('pack.mat');   % the value comes from a .mat file\n", encoding="utf-8")
        with self.assertRaises(rtegen.SwcDescriptionError) as ctx:
            rtegen.generate(ecu_file, BT148, self.tmp / "out", REPO, TASKS)
        errors = [m for m in ctx.exception.messages if m.severity == "error"]
        self.assertEqual(len(errors), 1, [str(m) for m in ctx.exception.messages])
        message = errors[0]
        self.assertEqual((message.file.name, message.path), ("swc.yaml", ("ports", 1, "dimension")))
        self.assertIn("dimension 'pack.number_cells' of port CellVoltages cannot be resolved without MATLAB: "
                      "pack.number_cells is not assigned in the init scripts", message.text)
        self.assertIn("dimension: <n>, dimension_source: pack.number_cells", message.text)

    def test_simulink_component_dimensions_against_the_arxml(self):
        """The ArrayType of the ARXML (TypeSwc: Scaled and Samples rt_Array_uint16_4) against swc.yaml: a different
        number is a warning at the swc.yaml line - the RTE follows the model, which Embedded Coder built from."""
        comp = rtegen.parse_component("TypeSwc", HERE / "test" / "types" / "TypeSwc" / "generated" / "arxml")
        self.assertEqual(rtegen.port_dimension(comp.ports["Scaled.Scaled"].type), 4)
        folder = self.tmp / "TypeSwc"
        folder.mkdir()
        (folder / "TypeSwc_init.m").write_text("types.n_samples = 4;\n", encoding="utf-8")
        (folder / "swc.yaml").write_text(
            "name: TypeSwc\nports:\n"
            "  - { name: Samples, direction: in, type: uint16, runnables: [Run10ms], dimension: types.n_samples }\n"
            "  - { name: Scaled, direction: out, type: uint16, runnables: [Run10ms], dimension: 5, "
            "dimension_source: types.n_scaled }\n"
            "  - { name: Temp, direction: in, type: uint16, runnables: [Run10ms], dimension: 1 }\n",
            encoding="utf-8")
        found = rtegen.arxml_dimension_findings(comp, folder)
        self.assertEqual([(m.severity, m.file.name, m.path, m.line) for m in found],
                         [("warning", "swc.yaml", ("ports", 1, "dimension"), 4)])
        self.assertIn("port Scaled: swc.yaml gives dimension 5 (types.n_scaled), the ARXML of the model 4 elements "
                      "(rt_Array_uint16_4); the RTE follows the model", found[0].text)

    def test_array_host_runtime(self):
        out = self.tmp / "gen"
        rtegen.generate(ARRAYS / "rte.yaml", BT148, out, REPO, TASKS)
        stdout = zig_host_test(self, out, self.tmp, [ARRAYS / "CellSrcSwc/src/CellSrcSwc.c",
                                                     ARRAYS / "CellSinkSwc/src/CellSinkSwc.c"],
                               ARRAYS / "rte_arrays_host_test.cpp", [])
        self.assertIn(", 0 failures", stdout)


class IdentifierCheckInValidateTest(unittest.TestCase):
    """RTE-24: the identifier check of TOOL-134 runs in the model phase - validate() refuses what generate() does."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_ident_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clash_across_components_is_reported_by_validate(self):
        # Rte_Buf_A_<p1>_<p1> and Rte_Buf_A_B_<p2>_<p2> agree in their first 63 characters; each swc.yaml alone is
        # fine, only the whole generate shows the clash
        p1 = "B_" + "x" * 30
        p2 = "x" * 30 + "_B_" + "x" * 20 + "Z"
        for name, port in (("A", p1), ("A_B", p2)):
            folder = self.tmp / name
            (folder / "src").mkdir(parents=True)
            (folder / "swc.yaml").write_text(
                f"version: 1\nname: {name}\nimplementation: c\nrunnables:\n  - {{ name: Run10ms, period_ms: 10 }}\n"
                f"ports:\n  - {{ name: {port}, direction: out, type: uint8, runnables: [Run10ms] }}\n",
                encoding="utf-8")
            (folder / "src" / f"{name}.c").write_text(
                f'#include "Rte_{name}.h"\nvoid {name}_Init(void) {{}}\nvoid {name}_Run10ms(void) {{}}\n',
                encoding="utf-8")
        ecu_file = self.tmp / "rte.yaml"
        ecu_file.write_text(yaml.safe_dump({"schema_version": 2,
                                            "components": [{"name": "A", "path": "A"}, {"name": "A_B", "path": "A_B"}],
                                            "connections": {f"A.{p1}.{p1}": "rte", f"A_B.{p2}.{p2}": "rte"}}),
                            encoding="utf-8")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.generate(ecu_file, BT148, self.tmp / "out", REPO, TASKS)
        self.assertIn("is not distinct from", str(ctx.exception))
        project = target_build.project_file(self.tmp / "p", ecu_file)
        errors = [m for m in rtegen.validate(project, REPO, tasks_manifest=TASKS) if m.severity == "error"]
        self.assertEqual([m.text for m in errors], [str(ctx.exception)])
        self.assertFalse((self.tmp / "out").exists())      # refused before anything was written


CALIB = HERE / "test" / "calib"
PARAM = HERE / "test" / "param"


class CalibrationTest(unittest.TestCase):
    """RTE-26 (calibration K1): definitions of swc.yaml in the RTE - profile test/calib (C component CalMapSwc) and
    the Simulink component CalSwc of test/param (ARXML) with a swc.yaml that defines some of its parameters."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_calib_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def copy(self, profile: Path) -> Path:
        shutil.rmtree(self.tmp / profile.name, ignore_errors=True)
        shutil.copytree(profile, self.tmp / profile.name, ignore=shutil.ignore_patterns("*.cpp"))
        return self.tmp / profile.name / "rte.yaml"

    def test_model_of_the_profile(self):
        components, _buffers, _tasks = rtegen.generate(CALIB / "rte.yaml", BT148, self.tmp / "out", REPO, TASKS)
        prm = components["CalMapSwc"].parameters
        tmap, derate, axis = prm["CalPrm.TorqueLimit_Nm"], prm["CalPrm.DerateCurve"], prm["CalPrm.SpeedAxis_rpm"]
        self.assertEqual((tmap.kind, tmap.dim, [a.element for a in tmap.axes]),
                         ("map", 15, ["SpeedAxis_rpm", "TorqueLimit_Nm_Y"]))
        self.assertEqual((tmap.limits, tmap.safety, tmap.unit), ((0, 2500), "asil_b", "Nm"))
        # own breakpoints live in the record of their owner (x/y in front of v), shared ones on their own
        self.assertEqual((tmap.expr, prm["CalPrm.TorqueLimit_Nm_Y"].expr),
                         ("Rte_Prm_CalMapSwc_CalPrm_TorqueLimit_Nm_data.v", "Rte_Prm_CalMapSwc_CalPrm_TorqueLimit_Nm_data.y"))
        self.assertEqual((derate.expr, prm["CalPrm.DerateCurve_X"].expr, axis.expr),
                         ("Rte_Prm_CalMapSwc_CalPrm_DerateCurve_data.v", "Rte_Prm_CalMapSwc_CalPrm_DerateCurve_data.x",
                          "Rte_Prm_CalMapSwc_CalPrm_SpeedAxis_rpm_data"))
        self.assertEqual(prm["CalPrm.DerateCurve_X"].limits, (-400, 1500))       # lsb 0.1
        self.assertIsNone(prm["CalPrm.Gain"].limits)
        files = {p.name: p.read_text(encoding="utf-8") for p in (self.tmp / "out").glob("*.*")}
        self.assertIn("typedef struct\n{\n    sint8 y[3];\n    sint16 v[15];\n} Rte_CalRec_CalMapSwc_CalPrm_TorqueLimit_Nm;",
                      files["Rte_Internal.h"])
        self.assertIn("static sint16 const Rte_Lim_CalMapSwc_CalPrm_TorqueLimit_Nm[2] = { 0, 2500 };",
                      files["RteConfig.cpp"])
        self.assertIn('{"CalMapSwc.CalPrm.TorqueLimit_Nm", "Nm", Rte_Prm_CalMapSwc_CalPrm_TorqueLimit_Nm_data.v, '
                      'SignalType::SINT16, 15U, { 0, 1 }, 10, Rte_Lim_CalMapSwc_CalPrm_TorqueLimit_Nm, { 0U, 1U }, '
                      'ParameterKind::MAP, SafetyClass::ASIL_B},', files["RteConfig.cpp"])
        self.assertIn("Rte_Prm_CalMapSwc_CalPrm_DerateCurve_data.x, SignalType::SINT16, 4U, { 0, 1 }, 10, "
                      "Rte_Lim_CalMapSwc_CalPrm_DerateCurve_X, { 5U, NO_AXIS }, ParameterKind::OWN_AXIS",
                      files["RteConfig.cpp"])
        # an RTE-03 parameter keeps the row of before RTE-26 (defaults of the struct)
        self.assertIn('{"CalMapSwc.CalPrm.Gain", "", &Rte_Prm_CalMapSwc_CalPrm_Gain_data, SignalType::FLOAT32, 1U, '
                      '{ 0, 1 }, 1},', files["RteConfig.cpp"])
        description = json.loads(files["RteCalibration.json"])
        self.assertEqual(description["format"], "nextbsw-rte-calibration 1")
        self.assertRegex(description["layout"], r"^[0-9a-f]{8}$")
        mode = description["parameters"]["CalMapSwc.CalPrm.Mode"]
        self.assertEqual((mode["description"], mode["value_table"]), ("Operating mode", [[0, "Off"], [1, "Eco"],
                                                                                          [2, "Sport"]]))
        self.assertEqual(description["parameters"]["CalMapSwc.CalPrm.DerateCurve_X"]["owner"],
                         "CalMapSwc.CalPrm.DerateCurve")

    def test_layout_identifier_follows_the_layout_not_the_values(self):
        first = rtegen.calibration_layout_id(rtegen.generate(CALIB / "rte.yaml", BT148, self.tmp / "a", REPO, TASKS)[0])
        ecu_file = self.copy(CALIB)
        data = ecu_file.parent / "CalMapSwc" / "CalMapSwc_data.m"
        data.write_text(data.read_text(encoding="utf-8").replace("MaxTorque_Nm = 250;", "MaxTorque_Nm = 240;"),
                        encoding="utf-8")
        second = rtegen.calibration_layout_id(rtegen.generate(ecu_file, BT148, self.tmp / "b", REPO, TASKS)[0])
        self.assertEqual(first, second)
        swc = ecu_file.parent / "CalMapSwc" / "swc.yaml"
        swc.write_text(swc.read_text(encoding="utf-8").replace("dim: 3, type: sint8", "dim: 3, type: sint16"),
                       encoding="utf-8")
        third = rtegen.calibration_layout_id(rtegen.generate(ecu_file, BT148, self.tmp / "c", REPO, TASKS)[0])
        self.assertNotEqual(first, third)

    def test_validate_of_the_profile_is_clean(self):
        project = target_build.project_file(self.tmp / "p", CALIB / "rte.yaml")
        # asil_b parameters need their own calibration segment with on_invalid refuse (nxconfig, SYS-96); the same
        # section as the calib-dev target build (ecu/calibration/test/target_build.py)
        data = yaml.safe_load(project.read_text(encoding="utf-8"))
        data.setdefault("memory", {})["calibration"] = {"region": "PFLASH", "size": 0x1000, "on_invalid": "refuse",
                                                       "profile": "development"}
        project.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        errors = [m for m in rtegen.validate(project, REPO, tasks_manifest=TASKS) if m.severity == "error"]
        self.assertEqual(errors, [])

    def test_stored_limits_and_breakpoints(self):
        """Rounding onto the grid of the scaling: a default inside min/max may be stored outside the stored limits,
        and two breakpoints may become one - both are errors of the generator."""
        ecu_file = self.copy(CALIB)
        swc = ecu_file.parent / "CalMapSwc" / "swc.yaml"
        data = ecu_file.parent / "CalMapSwc" / "CalMapSwc_data.m"
        text, values = swc.read_text(encoding="utf-8"), data.read_text(encoding="utf-8")
        swc.write_text(text.replace("    max: 300\n", "    max: 249.96\n"), encoding="utf-8")
        data.write_text(values.replace("MaxTorque_Nm = 250;", "MaxTorque_Nm = 249.96;"), encoding="utf-8")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.generate(ecu_file, BT148, self.tmp / "out", REPO, TASKS)
        self.assertIn("CalMapSwc.CalPrm.MaxTorque_Nm: 249.96 is stored as 2500, outside the stored limits 0 .. 2499",
                      str(ctx.exception))
        swc.write_text(text, encoding="utf-8")
        data.write_text(values.replace("DerateCurve_X = [-40 60 100 150];", "DerateCurve_X = [-40 60 100 100.04];"),
                        encoding="utf-8")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.generate(ecu_file, BT148, self.tmp / "out", REPO, TASKS)
        self.assertIn("CalMapSwc.CalPrm.DerateCurve_X: breakpoints are not strictly ascending as stored values "
                      "(1000 at 3, 1000 at 4", str(ctx.exception))

    def test_swc_yaml_errors_reach_rtegen_with_their_path(self):
        ecu_file = self.copy(CALIB)
        swc = ecu_file.parent / "CalMapSwc" / "swc.yaml"
        swc.write_text(swc.read_text(encoding="utf-8").replace("axis: SpeedAxis_rpm", "axis: SpeedAxis"),
                       encoding="utf-8")
        with self.assertRaises(rtegen.SwcDescriptionError) as ctx:
            rtegen.generate(ecu_file, BT148, self.tmp / "out", REPO, TASKS)
        errors = [m for m in ctx.exception.messages if m.severity == "error"]
        self.assertEqual([(m.file.name, m.path) for m in errors], [("swc.yaml", ("parameters", 2, "axis"))])
        self.assertIn("BoostCurve: unknown axis 'SpeedAxis'", errors[0].text)

    # ---- Simulink: swc.yaml defines, the ARXML proves -------------------------------------------------------------

    CALSWC = """version: 1
name: CalSwc
runnables:
  - {{ name: Run10ms, period_ms: 10 }}
parameters:
  - {{ name: Table, kind: axis, type: {table_type}, dim: {table_dim}, unit: V, min: 0, max: 100, runnables: [Run10ms] }}
  - {{ name: Temp, type: sint16, lsb: {lsb}, offset: -40, min: -40, max: 125, safety: asil_b,
      description: Coolant temperature, runnables: [Run10ms] }}
  - {{ name: Gain, type: uint16, value_table: {{0: Off, 3: High}}, runnables: [Run10ms] }}
"""

    def simulink(self, **kwargs) -> Path:
        ecu_file = self.copy(PARAM)
        values = dict(table_type="uint16", table_dim=4, lsb=0.1)
        values.update(kwargs)
        (ecu_file.parent / "CalSwc" / "swc.yaml").write_text(self.CALSWC.format(**values), encoding="utf-8")
        return ecu_file

    def test_simulink_definition_against_the_arxml(self):
        ecu_file = self.simulink()
        loaded = rtegen.load_component("CalSwc", ecu_file.parent / "CalSwc", REPO)
        prm = loaded.component.parameters
        self.assertEqual((prm["CalPrm.Table"].kind, prm["CalPrm.Table"].limits, prm["CalPrm.Table"].unit),
                         ("axis", (0, 100), "V"))
        # physical = (-400 + stored) / 10: -40 .. 125 degC is stored 0 .. 1650
        self.assertEqual((prm["CalPrm.Temp"].limits, prm["CalPrm.Temp"].safety, prm["CalPrm.Temp"].description),
                         ((0, 1650), "asil_b", "Coolant temperature"))
        self.assertEqual(prm["CalPrm.Gain"].value_table, {0: "Off", 3: "High"})
        warned = sorted(m.text.split()[1] for m in loaded.warnings if "is not defined in swc.yaml" in m.text)
        self.assertEqual(warned, ["Alpha", "Enable", "Factor"])

    def refused(self, ecu_file: Path) -> list:
        with self.assertRaises(rtegen.SwcDescriptionError) as ctx:
            rtegen.load_component("CalSwc", ecu_file.parent / "CalSwc", REPO)
        return [(m.path, m.text) for m in ctx.exception.messages if m.severity == "error"]

    def test_simulink_type_dimension_and_scaling_must_fit(self):
        errors = self.refused(self.simulink(table_type="uint32"))
        self.assertEqual(errors[0][0], ("parameters", 0, "type"))
        self.assertIn("Table is uint32 in swc.yaml, the model has uint16", errors[0][1])
        ecu_file = self.simulink(table_dim=5)
        data = ecu_file.parent / "CalSwc" / "CalSwc_data.m"
        data.write_text(data.read_text(encoding="utf-8").replace("30 40]", "30 40 50]"), encoding="utf-8")
        errors = self.refused(ecu_file)
        self.assertEqual(errors[0][0], ("parameters", 0, "dim"))
        self.assertIn("dimension of Table does not fit: swc.yaml gives 5 value(s), the model (ARXML) 4", errors[0][1])
        errors = self.refused(self.simulink(lsb=0.2))
        self.assertEqual(errors[0][0], ("parameters", 1, "lsb"))
        self.assertIn("scaling of Temp differs", errors[0][1])

    def test_simulink_parameter_missing_in_the_model(self):
        ecu_file = self.simulink()
        swc = ecu_file.parent / "CalSwc" / "swc.yaml"
        swc.write_text(swc.read_text(encoding="utf-8") + "  - { name: Ghost, type: uint8, runnables: [Run10ms] }\n",
                       encoding="utf-8")
        data = ecu_file.parent / "CalSwc" / "CalSwc_data.m"
        data.write_text(data.read_text(encoding="utf-8") + "Ghost = 1;\n", encoding="utf-8")
        errors = self.refused(ecu_file)
        self.assertEqual(errors[0][0], ("parameters", 3, "name"))
        self.assertIn("Ghost is not a calibration parameter of the model CalSwc (ARXML)", errors[0][1])


#: TOOL-143: measurements for CellSrcSwc of the profile test/arrays - a scalar with unit and description, and an
#: array whose dimension is the name of the init data the array port uses (pack.number_cells = 6)
MEASUREMENTS = """measurements:
  - { name: CellSrc_u_Max, type: float32, unit: V, description: highest cell voltage }
  - { name: CellSrc_n_Cells, type: uint16, dimension: pack.number_cells }
"""
MEASURING_CODE = """/* TOOL-143: the measurements the RTE defines and declares in Rte_CellSrcSwc.h */
static void measure(void)
{
    uint8 i;
    CellSrc_u_Max = 4.2F;
    for (i = 0U; i < 6U; ++i)
    {
        CellSrc_n_Cells[i] = (uint16)(100U + i);
    }
}

void CellSrcSwc_Run10ms(void)
"""


def arrays_with_measurements(root: Path) -> Path:
    """Copy of the profile test/arrays whose CellSrcSwc has measurements (and code writing them); its rte.yaml."""
    shutil.copytree(ARRAYS, root / "arrays", ignore=shutil.ignore_patterns("*.cpp"))
    swc = root / "arrays" / "CellSrcSwc" / "swc.yaml"
    swc.write_text(swc.read_text(encoding="utf-8") + MEASUREMENTS, encoding="utf-8")
    code = root / "arrays" / "CellSrcSwc" / "src" / "CellSrcSwc.c"
    text = code.read_text(encoding="utf-8").replace("void CellSrcSwc_Run10ms(void)\n", MEASURING_CODE, 1)
    code.write_text(text.replace("    (void)Rte_Write_CellVoltages_CellVoltages(cells);\n",
                                 "    (void)Rte_Write_CellVoltages_CellVoltages(cells);\n    measure();\n", 1),
                    encoding="utf-8")
    return root / "arrays" / "rte.yaml"


class MeasurementTest(unittest.TestCase):
    """TOOL-143: `measurements:` of swc.yaml - rows of rte::SIGNALS (XCP read access, A2L MEASUREMENT)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_meas_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_c_component_measurements_are_defined_by_the_rte_and_listed(self):
        ecu_file = arrays_with_measurements(self.tmp)
        out = self.tmp / "gen"
        components, _buffers, _tasks = rtegen.generate(ecu_file, BT148, out, REPO, TASKS)
        rows = [(m.label, m.native, m.dim, m.text) for m in components["CellSrcSwc"].measurements]
        self.assertEqual(rows, [("CellSrcSwc.CellSrc_u_Max", "float32", 1, "measurement [V] highest cell voltage"),
                                ("CellSrcSwc.CellSrc_n_Cells", "uint16", 6, "measurement")])
        files = {p.name: p.read_text(encoding="utf-8") for p in out.glob("*.*")}
        self.assertIn("float32 CellSrc_u_Max = 0.0F;", files["Rte.c"])
        self.assertIn("uint16 CellSrc_n_Cells[6] = { 0U };", files["Rte.c"])
        self.assertIn("extern float32 CellSrc_u_Max; /* V highest cell voltage */", files["Rte_CellSrcSwc.h"])
        self.assertIn("extern uint16 CellSrc_n_Cells[6];", files["Rte_CellSrcSwc.h"])
        self.assertNotIn("CellSrc_u_Max", files["Rte_CellSinkSwc.h"])
        self.assertIn("extern uint16 CellSrc_n_Cells[6]; /* CellSrcSwc.CellSrc_n_Cells, defined by Rte.c */",
                      files["Rte_Internal.h"])
        self.assertIn('{"CellSrcSwc.CellSrc_u_Max", "measurement [V] highest cell voltage", SignalType::FLOAT32, '
                      '&CellSrc_u_Max},', files["RteConfig.cpp"])
        self.assertIn('{"CellSrcSwc.CellSrc_n_Cells[5]", "measurement", SignalType::UINT16, &CellSrc_n_Cells[5]},',
                      files["RteConfig.cpp"])
        self.assertNotIn("pack.number_cells", "".join(files.values()))
        # compiled and linked with the component that writes them (declaration of Rte_CellSrcSwc.h, definition of
        # Rte.c, addresses in rte::SIGNALS), and the host test of the profile still passes
        stdout = zig_host_test(self, out, self.tmp, [ecu_file.parent / "CellSrcSwc/src/CellSrcSwc.c",
                                                     ecu_file.parent / "CellSinkSwc/src/CellSinkSwc.c"],
                               ARRAYS / "rte_arrays_host_test.cpp", [])
        self.assertIn(", 0 failures", stdout)

    def test_a_wrong_measurement_is_a_finding_of_the_swc_yaml(self):
        ecu_file = arrays_with_measurements(self.tmp)
        swc = ecu_file.parent / "CellSrcSwc" / "swc.yaml"
        swc.write_text(swc.read_text(encoding="utf-8").replace("type: float32, unit: V", "type: real, unit: V"),
                       encoding="utf-8")
        with self.assertRaises(rtegen.SwcDescriptionError) as ctx:
            rtegen.generate(ecu_file, BT148, self.tmp / "out", REPO, TASKS)
        errors = [m for m in ctx.exception.messages if m.severity == "error"]
        self.assertEqual([(m.path, m.file.name) for m in errors], [(("measurements", 0, "type"), "swc.yaml")])

    def simulink(self, name: str, measurements: str, code: str) -> Path:
        folder = self.tmp / name
        (folder / "generated" / "src").mkdir(parents=True)
        (folder / "swc.yaml").write_text(f"version: 1\nname: {name}\n{measurements}", encoding="utf-8")
        (folder / "generated" / "src" / f"{name}.c").write_text(code, encoding="utf-8")
        return folder

    def test_simulink_measurements_are_checked_against_the_generated_code(self):
        """A Simulink component: Embedded Coder defines the variable (ExportedGlobal), the RTE only declares it -
        swc.yaml must name what the model code has."""
        ok = self.simulink("HvsCellMod", "measurements:\n  - { name: HvsCellMod_u_Max, type: float32, unit: V }\n"
                                         "  - { name: HvsCellMod_u_Cells, type: float32, dimension: 12 }\n",
                           '#include "HvsCellMod.h"\n\n/* Exported block signals */\nreal32_T HvsCellMod_u_Max;\n'
                           "real32_T HvsCellMod_u_Cells[12];\n\nvoid Run10ms(void)\n{\n  real32_T rtb_x;\n}\n")
        found, warnings = rtegen.model_measurements("HvsCellMod", ok)
        self.assertEqual([(m.name, m.native, m.dim, m.rte_defined) for m in found],
                         [("HvsCellMod_u_Max", "float32", 1, False), ("HvsCellMod_u_Cells", "float32", 12, False)])
        self.assertEqual(warnings, [])
        missing = self.simulink("NoExport", "measurements:\n  - { name: NoExport_x, type: uint8 }\n",
                                "static uint8_T NoExport_x;\n")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.model_measurements("NoExport", missing)
        self.assertIn("defines no global variable NoExport_x", str(ctx.exception))
        self.assertIn("ExportedGlobal or ExportToFile", str(ctx.exception))
        other = self.simulink("Other", "measurements:\n  - { name: Other_x, type: uint8, dimension: 4 }\n",
                              "uint16_T Other_x[4];\n")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.model_measurements("Other", other)
        self.assertIn("swc.yaml gives uint8 Other_x[4], the generated code defines uint16_T[4] Other_x",
                      str(ctx.exception))
        # one global symbol per ECU
        a, b = rtegen.Component("A"), rtegen.Component("B")
        a.measurements = [rtegen.Measurement("A", "Shared", "uint8")]
        b.measurements = [rtegen.Measurement("B", "Shared", "uint8")]
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.check_measurement_symbols({"A": a, "B": b})
        self.assertIn("measurement B.Shared and A.Shared are the same global variable Shared", str(ctx.exception))

    def test_measurement_errors_point_to_taking_the_description_over(self):
        """TOOL-166: a measurement the code lacks stays an error, the text names the Studio action (TOOL-165)."""
        missing = self.simulink("Gone", "measurements:\n  - { name: Gone_x, type: uint8 }\n", "uint8_T Other;\n")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.model_measurements("Gone", missing)
        self.assertIn("'Beschreibung aus Modell übernehmen'", str(ctx.exception))
        self.assertNotIn("import the model again", str(ctx.exception))
        other = self.simulink("Wide", "measurements:\n  - { name: Wide_x, type: uint8 }\n", "uint16_T Wide_x;\n")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.model_measurements("Wide", other)
        self.assertIn("'Beschreibung aus Modell übernehmen'", str(ctx.exception))
        self.assertNotIn("import the model again", str(ctx.exception))


#: DemoSwc as the swc.yaml fixture of tools/swc describes it - the same as its ARXML (swc/DemoSwc/generated/arxml)
DEMO_SWC_YAML = FIXTURE / "swc.yaml"
#: Embedded Coder output of a model with two exported signals (ExportedGlobal) next to its own structures
EXPORTING_CODE = """#include "DemoSwc.h"

/* Exported block signals */
real32_T DemoSwc_u_Filt;               /* '<S4>/Gain' */
uint16_T DemoSwc_n_Hist[8];            /* '<S4>/Delay' */

/* Exported block states */
uint8_T DemoSwc_cnt;                   /* '<S3>/Output' */

/* Exported block parameters */
real32_T DemoSwc_k = 0.5F;             /* parameters are no measurements */

/* PublicStructure Variables for Internal Data */
ARID_DEF_DemoSwc_T DemoSwc_ARID_DEF;   /* '<S1>/FilterState' */

void Run10ms(void)
{
  uint8 rtb_Output;
}
"""


class DescriptionFindingsTest(unittest.TestCase):
    """TOOL-166: the swc.yaml of a Simulink component against its ARXML and code, field by field - warnings with
    file, YAML path and line of the swc.yaml (the Studio shows them there), text naming the Studio action
    'Beschreibung aus Modell übernehmen' (TOOL-165). DemoSwc (ARXML of swc/DemoSwc) with the tools/swc fixture."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_desc_"))
        self.folder = self.tmp / "swc" / "DemoSwc"
        shutil.copytree(REPO / "swc" / "DemoSwc", self.folder)
        self.comp = rtegen.parse_component("DemoSwc", self.folder / "generated" / "arxml")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def describe(self, text: str | None = None, edit=None) -> list:
        swc = self.folder / "swc.yaml"
        text = DEMO_SWC_YAML.read_text(encoding="utf-8") if text is None else text
        for old, new in (edit or []):
            self.assertIn(old, text)
            text = text.replace(old, new, 1)
        swc.write_text(text, encoding="utf-8")
        return rtegen.description_findings(self.comp, self.folder)

    @staticmethod
    def where(found: list) -> list:
        return [(m.severity, m.file.name, m.path, m.line) for m in found]

    def test_matching_description_and_no_description_give_no_finding(self):
        self.assertEqual(rtegen.description_findings(self.comp, self.folder), [])      # no swc.yaml at all
        self.assertEqual([str(m) for m in self.describe()], [])
        # sections swc.yaml does not have are not compared; a C component is not compared with an ARXML
        self.assertEqual(self.describe("version: 1\nname: DemoSwc\n"), [])
        self.assertEqual(self.describe(edit=[("version: 1\n", "version: 1\nimplementation: c\n"),
                                             ("period_ms: 10", "period_ms: 20")]), [])
        self.assertEqual(self.describe("name: [broken\n"), [])

    def test_runnables_and_periods(self):
        found = self.describe(edit=[("period_ms: 100", "period_ms: 50"), ("name: Run10ms\n", "name: Run20ms\n")])
        self.assertEqual(self.where(found), [
            ("warning", "swc.yaml", ("runnables", 0, "name"), 8),
            ("warning", "swc.yaml", ("runnables", 1, "period_ms"), 11),
            ("warning", "swc.yaml", ("runnables",), 7),
        ])
        texts = [m.text for m in found]
        self.assertIn("runnable Run20ms: named in swc.yaml, the model (ARXML) has no periodic runnable Run20ms",
                      texts[0])
        self.assertIn("runnable Run100ms: swc.yaml gives period 50 ms, the model (ARXML) 100 ms", texts[1])
        self.assertIn("runnable Run10ms (10 ms) of the model (ARXML) is missing in swc.yaml", texts[2])
        for text in texts:
            self.assertIn("the RTE follows the model - take the description over from the model (Studio, page "
                          "Komponenten: 'Beschreibung aus Modell übernehmen')", text)

    def test_ports_name_direction_type_and_runnables(self):
        found = self.describe(edit=[
            ("{ name: Button1, direction: in, type: boolean,", "{ name: Button2, direction: in, type: boolean,"),
            ("{ name: MotorNtc_mV, direction: in, type: uint32,", "{ name: MotorNtc_mV, direction: in, type: uint16,"),
            ("{ name: PcCommandQlf, direction: in,", "{ name: PcCommandQlf, direction: out,"),
            ("{ name: Led1, direction: out, type: boolean, runnables: [Run10ms] }",
             "{ name: Led1, direction: out, type: boolean, runnables: [Run100ms] }"),
        ])
        self.assertEqual(self.where(found), [
            ("warning", "swc.yaml", ("ports", 0, "name"), 13),
            ("warning", "swc.yaml", ("ports", 1, "type"), 14),
            ("warning", "swc.yaml", ("ports", 2, "direction"), 15),
            ("warning", "swc.yaml", ("ports", 3, "runnables"), 16),
            ("warning", "swc.yaml", ("ports",), 12),
        ])
        texts = [m.text for m in found]
        self.assertIn("port Button2: named in swc.yaml, the model (ARXML) has no sender-receiver port Button2",
                      texts[0])
        self.assertIn("port MotorNtc_mV: swc.yaml gives type uint16, the model (ARXML) uint32", texts[1])
        self.assertIn("port PcCommandQlf: swc.yaml gives direction out, the model (ARXML) in (required port)",
                      texts[2])
        self.assertIn("port Led1: swc.yaml gives runnables [Run100ms], in the model (ARXML) [Run10ms] access it",
                      texts[3])
        self.assertIn("port Button1 (in) of the model (ARXML) is missing in swc.yaml", texts[4])
        self.assertTrue(all("'Beschreibung aus Modell übernehmen'" in t for t in texts))

    def test_dimension_findings_are_part_of_it(self):
        found = self.describe(edit=[("{ name: MotorNtc_mV, direction: in, type: uint32, runnables: [Run100ms] }",
                                     "{ name: MotorNtc_mV, direction: in, type: uint32, runnables: [Run100ms], "
                                     "dimension: 3 }")])
        self.assertEqual(self.where(found), [("warning", "swc.yaml", ("ports", 1, "dimension"), 14)])
        self.assertIn("port MotorNtc_mV: swc.yaml gives dimension 3, the ARXML of the model a scalar", found[0].text)
        self.assertIn("'Beschreibung aus Modell übernehmen'", found[0].text)

    def test_exported_variables_swc_yaml_does_not_name(self):
        (self.folder / "generated" / "src" / "DemoSwc.c").write_text(EXPORTING_CODE, encoding="utf-8")
        self.assertEqual(rtegen.exported_variables(self.folder / "generated" / "src"),
                         {"DemoSwc_u_Filt": ("real32_T", 1), "DemoSwc_n_Hist": ("uint16_T", 8),
                          "DemoSwc_cnt": ("uint8_T", 1)})
        found = self.describe()             # no measurements: section - the hint has no entry to point at
        self.assertEqual([(m.severity, m.path) for m in found], [("warning", ())] * 3)
        self.assertIn("the generated code exports the variable uint16 DemoSwc_n_Hist[8] (exporting storage class), "
                      "swc.yaml does not name it under measurements", found[1].text)
        text = DEMO_SWC_YAML.read_text(encoding="utf-8") + (
            "measurements:\n  - { name: DemoSwc_u_Filt, type: float32 }\n"
            "  - { name: DemoSwc_n_Hist, type: uint16, dimension: 8 }\n")
        found = self.describe(text)
        self.assertEqual(self.where(found), [("warning", "swc.yaml", ("measurements",), 21)])
        self.assertIn("exports the variable uint8 DemoSwc_cnt ", found[0].text)
        self.assertIn("'Beschreibung aus Modell übernehmen'", found[0].text)

    def test_load_component_and_validate_report_them(self):
        """The same path as the RTE-25 dimensions: load_component warnings -> build_model -> validate (the Studio
        check); never an error, the generate stays the ARXML's."""
        self.describe(edit=[("period_ms: 100", "period_ms: 50")])
        loaded = rtegen.load_component("DemoSwc", self.folder, REPO)
        self.assertEqual([m.path for m in loaded.warnings], [("runnables", 1, "period_ms")])
        self.assertEqual(next(r for r in loaded.component.runnables if r.name == "Run100ms").period_ms, 100)
        shutil.copytree(LEVELMON, self.tmp / "LevelMonSwc")
        ecu = copy.deepcopy(ECU)
        ecu["components"][0]["path"] = self.folder.as_posix()
        ecu["components"][1]["path"] = "LevelMonSwc"
        (self.tmp / "rte.yaml").write_text(yaml.safe_dump(ecu), encoding="utf-8")
        project = target_build.project_file(self.tmp / "q", self.tmp / "rte.yaml")
        messages = rtegen.validate(project, self.tmp, tasks_manifest=TASKS)
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        stale = [m for m in messages if m.file == self.folder / "swc.yaml"]
        self.assertEqual([(m.severity, m.path, m.line) for m in stale], [("warning", ("runnables", 1, "period_ms"), 11)])


if __name__ == "__main__":
    unittest.main()
