# SPDX-License-Identifier: Apache-2.0
"""RTE-23: components and runnables switched off in the ECU description - generator, messages, host test.

Own test profile test/disabled: three hand-written C components on the build test project buildtest_s32k148_min (no
SBC), BSW task profile test/multitask/tasks.yaml. LevelSrcSwc is disabled (components[].enabled: false),
AuxSrcSwc.RunAux10ms is disabled (tasks[].runnables[].enabled: false), SinkSwc stays enabled and reads their values.
The Simulink-side checks use the synthetic components of test/types (ARXML with INIT-VALUE), the safety checks
CalMapSwc of test/calib (asil_b), the NvM check test/nvm, the BSW targets the demo project. Not only the eval board.

  RteDisabledGenTest    generated code and model: disabled parts in no task and not initialised, const buffers with
                        the value of disabled_values / the ARXML init value / QLF_NOT_AVAILABLE, the age qualifier of a
                        disabled writer, BSW targets of a disabled port, the warnings; nothing changes while enabled
  RteDisabledRuleTest   what is refused, with its YAML path: safety-relevant component and runnable, a safety input
                        fed by a disabled part, no value anywhere (never a silent 0), a value in two places, an NvM
                        writer; the same message in validate() (Studio check) with file and line
  RteDisabledHostTest   test/disabled/rte_disabled_host_test.cpp against the generated RTE, and against deliberately
                        broken generated code and a broken generator (each mutation must be found)
  RteDisabledSchedTest  tools/schedana analyses only the runnables that run

The target build of the profile (firmware image, the code of the disabled parts dropped by the linker) is the group
target-rte-disabled: tools/rtegen/test/disabled/target_build.py.

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_disabled.py
"""

import contextlib
import copy
import io
import re
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
sys.path.insert(0, str(REPO / "tools"))

import rtegen  # noqa: E402
import target_build  # noqa: E402  (test/c_swc: a project on buildtest_s32k148_min with another ECU description)

PROFILE = HERE / "test" / "disabled"
ECU = rtegen.with_resolved_paths(
    yaml.safe_load((PROFILE / "rte.yaml").read_text(encoding="utf-8")), PROFILE / "rte.yaml")
TYPES = HERE / "test" / "types"
TYPES_ECU = rtegen.with_resolved_paths(
    yaml.safe_load((TYPES / "rte.yaml").read_text(encoding="utf-8")), TYPES / "rte.yaml")
CALIB = HERE / "test" / "calib"
NVM = HERE / "test" / "nvm"
MULTI_TASKS = HERE / "test" / "multitask" / "tasks.yaml"
PROJECT = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
# PLAT-62: the own full test profile instead of the private eval board project (PLAT-46 F10)
DEMO = rtegen.nxconfig.load(REPO / "tools/nxconfig/test/profiles/projects/testproject_full/project.yaml")
ZIG = [sys.executable, "-m", "ziglang"]


class DisabledCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_disabled_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, ecu=None, project=PROJECT, tasks=MULTI_TASKS, out="out", diag=rtegen.DIAG_TARGET):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu if ecu is not None else ECU, sort_keys=False), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = rtegen.generate(path, project, self.tmp / out, REPO, tasks, diag=diag)
        self.warnings = [line for line in err.getvalue().splitlines() if "rtegen: warning:" in line]
        return result

    def expect_error(self, fragment, ecu, **kwargs):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu, **kwargs)
        self.assertIn(fragment, str(ctx.exception))
        return str(ctx.exception)

    @staticmethod
    def component(ecu, name):
        return next(c for c in ecu["components"] if c["name"] == name)


class RteDisabledGenTest(DisabledCase):
    def test_profile(self):
        components, buffers, tasks = self.run_with()
        self.assertFalse(components["LevelSrcSwc"].enabled)
        self.assertTrue(components["AuxSrcSwc"].enabled)
        aux = {r.name: r for r in components["AuxSrcSwc"].runnables}
        self.assertEqual((aux["RunAux10ms"].enabled, aux["RunAux10ms"].task), (False, None))
        self.assertEqual(aux["RunAux10ms"].disabled_where, "tasks[1].runnables[2].enabled")
        by_name = {t.name: [f"{c.name}.{r.name}" for c, r in t.runnables] for t in tasks}
        self.assertEqual(by_name, {"fast": [], "slow": ["AuxSrcSwc.RunMain10ms", "SinkSwc.Run10ms"]})
        # const buffers of the ports fed by a disabled part, with the value and where it comes from
        disabled = {k: (b.init, b.disabled_by) for k, b in buffers.items() if b.init_only == rtegen.INIT_DISABLED}
        self.assertEqual(disabled, {
            "SinkSwc.Level_mV.Level_mV": ("1234", "LevelSrcSwc.Level_mV.Level_mV (components[0].enabled)"),
            "SinkSwc.LevelQlf.LevelQlf": ("64U", "LevelSrcSwc.LevelQlf.LevelQlf (components[0].enabled)"),
            "SinkSwc.Aux.Aux": ("77", "AuxSrcSwc.Aux.Aux (tasks[1].runnables[2].enabled)"),
            "age.qlf.LevelSrcSwc.Level_mV.Level_mV": ("64U", "LevelSrcSwc.Level_mV.Level_mV (components[0].enabled)"),
        })
        self.assertIsNone(buffers["age.qlf.LevelSrcSwc.Level_mV.Level_mV"].age)     # no supervision
        self.assertFalse(buffers["SinkSwc.Main.Main"].constant if "SinkSwc.Main.Main" in buffers else False)
        rte_c = (self.tmp / "out" / "Rte.c").read_text(encoding="utf-8")
        self.assertIn("uint32 const Rte_Buf_SinkSwc_Level_mV_Level_mV = 1234; /* disabled (RTE-23)", rte_c)
        self.assertIn("uint8 const Rte_Buf_SinkSwc_LevelQlf_LevelQlf = 64U;", rte_c)
        self.assertIn("*data = (uint32)Rte_Buf_SinkSwc_Level_mV_Level_mV;", rte_c)
        config = (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")
        self.assertNotIn("LevelSrcSwc_Init", config)
        self.assertNotIn("&LevelSrcSwc_Run10ms", config)
        self.assertNotIn("&AuxSrcSwc_RunAux10ms", config)
        self.assertIn('{"AuxSrcSwc.AuxSrcSwc_Init", &AuxSrcSwc_Init', config)
        # the component stays in the RTE: its header, its port buffers and API (the code is compiled, not called)
        self.assertTrue((self.tmp / "out" / "Rte_LevelSrcSwc.h").is_file())
        self.assertIn("Std_ReturnType Rte_Write_LevelSrcSwc_Level_mV_Level_mV(uint32 data)", rte_c)

    def test_warnings(self):
        self.run_with()
        self.assertEqual(self.warnings, [
            "rtegen: warning: components[0].enabled: component LevelSrcSwc is disabled: its init and its runnables "
            "(Run10ms) are not called",
            "rtegen: warning: tasks[1].runnables[2].enabled: runnable AuxSrcSwc.RunAux10ms is disabled: it is not "
            "called",
            "rtegen: warning: required port SinkSwc.LevelAge.LevelAge reads QLF_NOT_AVAILABLE (64) from "
            "age.qlf.LevelSrcSwc.Level_mV.Level_mV: the writer LevelSrcSwc.Level_mV.Level_mV (components[0].enabled) "
            "is disabled, its age is not supervised",
            "rtegen: warning: required port SinkSwc.Level_mV.Level_mV reads the constant value 1234 from a constant "
            "buffer: its source LevelSrcSwc.Level_mV.Level_mV (components[0].enabled) is disabled",
            "rtegen: warning: required port SinkSwc.LevelQlf.LevelQlf reads QLF_NOT_AVAILABLE (64, no source) from "
            "a constant buffer: its source LevelSrcSwc.LevelQlf.LevelQlf (components[0].enabled) is disabled",
            "rtegen: warning: required port SinkSwc.Aux.Aux reads the constant value 77 from a constant buffer: its "
            "source AuxSrcSwc.Aux.Aux (tasks[1].runnables[2].enabled) is disabled",
        ])

    def test_nothing_changes_while_enabled(self):
        """Enabled parts (explicit `enabled: true` and disabled_values that wait for their use) generate the same
        bytes as the description without the keys - the existing profiles stay as they are (rtegen-reference)."""
        plain = copy.deepcopy(ECU)
        for entry in plain["components"]:
            entry.pop("enabled", None)
            entry.pop("disabled_values", None)
        plain["tasks"][1]["runnables"][2] = "AuxSrcSwc.RunAux10ms"
        plain["connections"]["SinkSwc.LevelAge.LevelAge"] = "rte.init"   # the age of a running writer: test/age
        self.run_with(plain, out="plain")
        switched = copy.deepcopy(plain)
        self.component(switched, "LevelSrcSwc")["enabled"] = True
        self.component(switched, "LevelSrcSwc")["disabled_values"] = {"Level_mV.Level_mV": 1234}
        self.component(switched, "AuxSrcSwc")["disabled_values"] = {"Aux.Aux": 77}
        switched["tasks"][1]["runnables"][2] = {"runnable": "AuxSrcSwc.RunAux10ms", "enabled": True}
        self.run_with(switched, out="switched")
        self.assertEqual(self.warnings, [])
        files = sorted(p.relative_to(self.tmp / "plain").as_posix() for p in (self.tmp / "plain").rglob("*")
                       if p.is_file())
        for name in files:
            with self.subTest(file=name):
                self.assertEqual((self.tmp / "plain" / name).read_bytes(), (self.tmp / "switched" / name).read_bytes())

    def test_simulink_init_values(self):
        """TypeSwc (ARXML with INIT-VALUE) disabled: its readers get the init values of its provided ports - a record
        and a fixed-point array - without any disabled_values; a value there is refused (one place)."""
        ecu = copy.deepcopy(TYPES_ECU)
        self.component(ecu, "TypeSwc")["enabled"] = False
        components, buffers, _tasks = self.run_with(ecu)
        for key, source in (("TypeSink.Stat.Stat", "TypeSwc.Stat.Stat"), ("TypeSink.Scaled.Scaled",
                                                                        "TypeSwc.Scaled.Scaled")):
            b = buffers[key]
            self.assertEqual((b.init_only, b.init), (rtegen.INIT_DISABLED, components["TypeSwc"].ports[
                source.partition(".")[2]].init))
            self.assertTrue(components["TypeSwc"].ports[source.partition(".")[2]].init_declared)
        self.component(ecu, "TypeSwc")["disabled_values"] = {"Stat.Stat": [0, 0, 0]}
        self.expect_error("components[0].disabled_values: TypeSwc.Stat.Stat has an init value of its own", ecu)

    def test_disabled_values_types(self):
        """disabled_values follow the port type: whole numbers and range of an integer port, lists for arrays."""
        ecu = copy.deepcopy(ECU)
        self.component(ecu, "LevelSrcSwc")["disabled_values"] = {"Level_mV.Level_mV": 1.5}
        self.expect_error("components[0].disabled_values: LevelSrcSwc.Level_mV.Level_mV: init value 1.5 is no whole "
                          "number", ecu)
        self.component(ecu, "LevelSrcSwc")["disabled_values"] = {"Level_mV.Level_mV": -1}
        self.expect_error("does not fit", ecu)
        self.component(ecu, "LevelSrcSwc")["disabled_values"] = {"Level_mV.Level_mV": [1, 2]}
        self.expect_error("is a scalar, not a list", ecu)
        self.component(ecu, "LevelSrcSwc")["disabled_values"] = {"Nope.Nope": 1}
        self.expect_error("components[0].disabled_values: Nope.Nope is no provided port of LevelSrcSwc", ecu)

    def test_bsw_targets_of_the_demo(self):
        """DemoSwc disabled in the demo project: its BSW targets (dio.out, can.tx, ...) get the ARXML init value of
        the port and a warning each at the YAML path of the connection; the build test of the demo stays enabled."""
        self.assertEqual(rtegen.component_safety(REPO / "swc" / "DemoSwc"), "qm")
        ecu = rtegen.with_resolved_paths(yaml.safe_load(DEMO.rte_file.read_text(encoding="utf-8")), DEMO.rte_file)
        self.component(ecu, "DemoSwc")["enabled"] = False
        components, buffers, tasks = self.run_with(ecu, project=DEMO, tasks=None, diag=None)
        self.assertEqual([r for t in tasks for r in t.runnables if r[0].name == "DemoSwc"], [])
        driven = [k for k, b in buffers.items() if b.disabled_by and b.links and not b.init_only]
        self.assertTrue(driven)
        for key in driven:
            with self.subTest(port=key):
                self.assertEqual(buffers[key].init, components["DemoSwc"].ports[key.partition(".")[2]].init)
                self.assertTrue(any(w.startswith(f"rtegen: warning: connection {key} -> ") and "the constant value"
                                    in w for w in self.warnings), self.warnings)


class RteDisabledRuleTest(DisabledCase):
    def test_no_value_is_no_silent_zero(self):
        ecu = copy.deepcopy(ECU)
        del self.component(ecu, "LevelSrcSwc")["disabled_values"]
        text = self.expect_error("required port SinkSwc.Level_mV.Level_mV reads LevelSrcSwc.Level_mV.Level_mV, which "
                                 "is disabled (components[0].enabled), and neither port has an init value", ecu)
        self.assertIn("components[0].disabled_values (Level_mV.Level_mV) - never a silent 0", text)
        # the qualifier port needs no value: without the Level_mV connection it generates
        ecu["connections"].pop("SinkSwc.Level_mV.Level_mV")
        ecu["connections"].pop("SinkSwc.LevelAge.LevelAge")
        self.run_with(ecu)

    def test_disabled_runnable_needs_a_value(self):
        ecu = copy.deepcopy(ECU)
        del self.component(ecu, "AuxSrcSwc")["disabled_values"]
        self.expect_error("required port SinkSwc.Aux.Aux reads AuxSrcSwc.Aux.Aux, which is disabled "
                          "(tasks[1].runnables[2].enabled)", ecu)

    def test_bsw_target_needs_a_value(self):
        ecu = copy.deepcopy(ECU)
        del self.component(ecu, "LevelSrcSwc")["disabled_values"]
        for key in ("SinkSwc.Level_mV.Level_mV", "SinkSwc.LevelAge.LevelAge"):
            ecu["connections"].pop(key)
        ecu["connections"]["LevelSrcSwc.Level_mV.Level_mV"] = "did.Level"
        self.expect_error("connection LevelSrcSwc.Level_mV.Level_mV -> did.Level: LevelSrcSwc.Level_mV.Level_mV is "
                          "disabled (components[0].enabled) and has no init value of its own", ecu, diag=None)

    def test_safety_component_cannot_be_disabled(self):
        """CalMapSwc (asil_b parameters): neither the component nor its runnable can be switched off, and no part
        may feed one of its inputs while disabled."""
        ecu = rtegen.with_resolved_paths(yaml.safe_load((CALIB / "rte.yaml").read_text(encoding="utf-8")),
                                         CALIB / "rte.yaml")
        self.run_with(ecu, tasks=None)
        off = copy.deepcopy(ecu)
        self.component(off, "CalMapSwc")["enabled"] = False
        self.expect_error("components[0].enabled: component CalMapSwc cannot be disabled: CalMapSwc is safety "
                          "relevant (swc.yaml safety asil_b) - a disabled part would silently switch off a safety "
                          "function", off, tasks=None)
        off = copy.deepcopy(ecu)
        off["tasks"][0]["runnables"] = [{"runnable": "CalMapSwc.Run10ms", "enabled": False}]
        self.expect_error("tasks[0].runnables[0].enabled: runnable CalMapSwc.Run10ms cannot be disabled", off,
                          tasks=None)
        # a disabled QM source of a safety input
        feed = copy.deepcopy(ecu)
        feed["components"].append({"name": "AuxSrcSwc", "path": (PROFILE / "AuxSrcSwc").as_posix(),
                                   "disabled_values": {"Aux.Aux": 5}})
        feed["tasks"][0]["runnables"] = ["CalMapSwc.Run10ms", "AuxSrcSwc.RunMain10ms",
                                         {"runnable": "AuxSrcSwc.RunAux10ms", "enabled": False}]
        feed["connections"]["CalMapSwc.Speed_rpm.Speed_rpm"] = "AuxSrcSwc.Aux.Aux"
        self.expect_error("required port CalMapSwc.Speed_rpm.Speed_rpm reads AuxSrcSwc.Aux.Aux, which is disabled "
                          "(tasks[0].runnables[2].enabled): CalMapSwc is safety relevant", feed, tasks=None)

    def test_nvm_writer_cannot_be_disabled(self):
        ecu = rtegen.with_resolved_paths(yaml.safe_load((NVM / "rte.yaml").read_text(encoding="utf-8")),
                                         NVM / "rte.yaml")
        self.component(ecu, "TypeSwc")["enabled"] = False
        self.expect_error("TypeSwc.Scaled.Scaled is disabled (components[1].enabled), but it writes a non-volatile "
                          "block", ecu)

    def test_duplicate_entry_of_a_disabled_runnable(self):
        ecu = copy.deepcopy(ECU)
        ecu["tasks"][0]["runnables"] = ["AuxSrcSwc.RunAux10ms"]
        self.expect_error("tasks[1].runnables[2]: AuxSrcSwc.RunAux10ms is already assigned to task fast", ecu)

    def test_validate_reports_with_yaml_path(self):
        """The Studio check (validate) reports like generate, with file, YAML path and line."""
        path = self.tmp / "rte.yaml"
        path.write_text((PROFILE / "rte.yaml").read_text(encoding="utf-8").replace("path: ", "path: " +
                        PROFILE.as_posix() + "/"), encoding="utf-8")
        project = target_build.project_file(self.tmp / "ok", path)
        messages = rtegen.validate(project, REPO, tasks_manifest=MULTI_TASKS)
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        places = {m.path: m.line for m in messages if m.severity == "warning" and m.file == path}
        self.assertIn(("components", 0, "enabled"), places)
        self.assertIn(("tasks", 1, "runnables", 2, "enabled"), places)
        self.assertIn(("connections", "SinkSwc.Level_mV.Level_mV"), places)
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[places[("components", 0, "enabled")] - 1].strip(), "enabled: false")
        text = path.read_text(encoding="utf-8").replace("      Level_mV.Level_mV: 1234\n", "")
        text = text.replace("    disabled_values:\n  - name: AuxSrcSwc", "  - name: AuxSrcSwc")
        path.write_text(text, encoding="utf-8")
        errors = [m for m in rtegen.validate(project, REPO, tasks_manifest=MULTI_TASKS) if m.severity == "error"]
        self.assertEqual(len(errors), 1, [str(m) for m in errors])
        self.assertIn("never a silent 0", errors[0].text)
        self.assertEqual((errors[0].file, errors[0].path), (path, ("connections", "SinkSwc.Level_mV.Level_mV")))


class RteDisabledHostTest(unittest.TestCase):
    """Generated RTE of test/disabled + the three C components + test/disabled/rte_disabled_host_test.cpp."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nx_disabled_host_"))
        cls.out = cls.tmp / "gen"
        with contextlib.redirect_stderr(io.StringIO()):
            rtegen.generate(PROFILE / "rte.yaml", PROJECT, cls.out, REPO, MULTI_TASKS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build_and_run(self, out: Path, name: str, must_build: bool = True):
        includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                    f"-I{HERE / 'test' / 'can_host' / 'stubs'}"]
        build = self.tmp / name
        build.mkdir()
        strict = ["-Wconversion", "-Wsign-conversion", "-pedantic"]      # hand-written components
        objects = []
        for src, extra in ((out / "Rte.c", []), (PROFILE / "LevelSrcSwc/src/LevelSrcSwc.c", strict),
                           (PROFILE / "AuxSrcSwc/src/AuxSrcSwc.c", strict), (PROFILE / "SinkSwc/src/SinkSwc.c", strict)):
            obj = build / (src.stem + ".o")
            r = subprocess.run(ZIG + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *extra, *includes, "-c",
                                      str(src), "-o", str(obj)], capture_output=True, text=True)
            if r.returncode and not must_build:
                return r
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            objects.append(str(obj))
        exe = build / "rte_disabled_host_test.exe"
        r = subprocess.run(ZIG + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
                                  "-Wno-sign-conversion", "-Werror", *includes, str(out / "RteConfig.cpp"),
                                  str(PROFILE / "rte_disabled_host_test.cpp"), *objects, "-o", str(exe)],
                           capture_output=True, text=True)
        if r.returncode and not must_build:
            return r
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        return subprocess.run([str(exe)], capture_output=True, text=True)

    def test_disabled_runtime(self):
        r = self.build_and_run(self.out, "ok")
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertIn(", 0 failures", r.stdout)

    MUTATIONS = [
        ("Rte.c", "Rte_Buf_SinkSwc_LevelQlf_LevelQlf = 64U;", "Rte_Buf_SinkSwc_LevelQlf_LevelQlf = 1U;",
         "the qualifier of a disabled source looks like QLF_OK"),
        ("Rte.c", "Rte_Buf_SinkSwc_Aux_Aux = 77;", "Rte_Buf_SinkSwc_Aux_Aux = 0;",
         "the value of disabled_values is replaced by a silent 0"),
        ("RteConfig.cpp", "&SinkSwc_Init,", "&LevelSrcSwc_Init,", "the init of the disabled component runs"),
        ("RteConfig.cpp", "&AuxSrcSwc_RunMain10ms,", "&AuxSrcSwc_RunAux10ms,", "the disabled runnable runs"),
    ]

    def test_mutations_are_found(self):
        """Each deliberately broken variant of the generated code fails the host test (the test is not blind)."""
        for i, (name, original, replacement, what) in enumerate(self.MUTATIONS):
            with self.subTest(what=what):
                out = self.tmp / f"mut{i}"
                shutil.copytree(self.out, out)
                text = (out / name).read_text(encoding="utf-8")
                self.assertEqual(text.count(original), 1, f"mutation anchor not found once: {original!r}")
                (out / name).write_text(text.replace(original, replacement), encoding="utf-8")
                r = self.build_and_run(out, f"mut{i}_build")
                self.assertNotEqual(r.returncode, 0, f"mutation not found: {what}\n{r.stdout[-2000:]}")
                self.assertIn("FAIL", r.stdout)

    def test_generator_mutation_is_found(self):
        """A generator that forgets the disabled source (the reader keeps the never written buffer of LevelSrcSwc):
        the generated code no longer passes - the const checks or the values of the host test fail."""
        original = rtegen.disable_ports
        rtegen.disable_ports = lambda *args: None
        try:
            out = self.tmp / "mut_gen"
            with contextlib.redirect_stderr(io.StringIO()):
                rtegen.generate(PROFILE / "rte.yaml", PROJECT, out, REPO, MULTI_TASKS)
        finally:
            rtegen.disable_ports = original
        r = self.build_and_run(out, "mut_gen_build", must_build=False)
        self.assertNotEqual(r.returncode, 0, "generator mutation not found")


class RteDisabledSchedTest(unittest.TestCase):
    """tools/schedana (and the Studio's timeline, which uses it) sees only the runnables that run."""

    def test_schedule_analysis(self):
        sys.path.insert(0, str(REPO / "tools"))
        from schedana import rteload
        model = rteload.load_rte(PROFILE / "rte.yaml", PROJECT, repo=REPO, tasks_manifest=MULTI_TASKS)
        names = sorted(r.name for r in model.runnables())
        self.assertEqual(names, ["AuxSrcSwc.RunMain10ms", "SinkSwc.Run10ms"])
        texts = [f.message for f in model.findings]
        self.assertTrue(any("component LevelSrcSwc is disabled" in t for t in texts), texts)
        self.assertTrue(any("runnable AuxSrcSwc.RunAux10ms is disabled" in t for t in texts), texts)


if __name__ == "__main__":
    unittest.main()
