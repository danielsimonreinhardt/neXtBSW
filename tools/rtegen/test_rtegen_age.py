# SPDX-License-Identifier: Apache-2.0
"""SYS-32: age supervision of provided ports (age.qlf.*, gap G3 of doc/safety/concept.md) - generator, messages,
the DTC sampling and the host test.

Own test profile test/age: two hand-written C components (CellPkgSwc stands for a device package that writes only new
values, CellAppSwc reads them in another task) on the build test project buildtest_s32k148_min (no SBC), BSW task
profile test/multitask/tasks.yaml. The implicit and mode checks use the synthetic components of test/implicit and
test/modes. Not the eval board.

  RteAgeGenTest       generated code (flag in Rte_Write of the supervised ports only, deadline in cycles derived from
                      the period of the writer and its task, hand-over to the reading task), no age code without an
                      age target, the messages with their reason
  RteAgeDemTest       diag.yaml event.bsw age.qlf.* and event.port of a reading port become qualifier samples
  RteAgeHostTest      test/age/rte_age_host_test.cpp against the generated RTE, and the same test against deliberately
                      broken generated code (each mutation must be found)

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_age.py
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

import rtegen  # noqa: E402
import target_build  # noqa: E402  (test/c_swc: a project on buildtest_s32k148_min with another ECU description)

PROFILE = HERE / "test" / "age"
ECU = rtegen.with_resolved_paths(
    yaml.safe_load((PROFILE / "rte.yaml").read_text(encoding="utf-8")), PROFILE / "rte.yaml")
MULTI = HERE / "test" / "multitask"
MULTI_TASKS = MULTI / "tasks.yaml"      # own BSW task profile, not ecu/asyncCoreConfiguration/tasks.yaml
PROJECT = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
ZIG = [sys.executable, "-m", "ziglang"]
CELLS = "age.qlf.CellPkgSwc.Cells_mV.Cells_mV"
CURRENT = "age.qlf.CellPkgSwc.Current_mA.Current_mA"


class AgeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_age_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, ecu=None, diag=None, out="out"):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu or ECU, sort_keys=False), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rtegen.generate(path, PROJECT, self.tmp / out, REPO, MULTI_TASKS, diag=diag)
        self.stderr = err.getvalue()
        return self.tmp / out

    def expect_error(self, fragment, ecu=None, diag=None):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu, diag)
        self.assertIn(fragment, str(ctx.exception))
        return str(ctx.exception)

    @staticmethod
    def ecu(**changes):
        ecu = copy.deepcopy(ECU)
        for key, value in changes.items():
            if key == "tasks":
                ecu["tasks"] = value
            else:
                ecu["connections"][key.replace("__", ".")] = value
        return ecu


class RteAgeGenTest(AgeCase):
    def test_generated_code(self):
        out = self.run_with()
        rte_c = (out / "Rte.c").read_text(encoding="utf-8")
        config = (out / "RteConfig.cpp").read_text(encoding="utf-8")
        internal = (out / "Rte_Internal.h").read_text(encoding="utf-8")
        # the flag is set by Rte_Write of the supervised ports only - Runs is written in every run and not supervised
        writes = dict(re.findall(r"Std_ReturnType (Rte_Write_\w+)\([^)]*\)\n\{\n(.*?)\n\}", rte_c, re.S))
        self.assertIn("Rte_Age_CellPkgSwc_Cells_mV_Cells_mV = 1u;", writes["Rte_Write_CellPkgSwc_Cells_mV_Cells_mV"])
        self.assertIn("Rte_Age_CellPkgSwc_Current_mA_Current_mA = 1u;",
                      writes["Rte_Write_CellPkgSwc_Current_mA_Current_mA"])
        self.assertNotIn("Rte_Age_", writes["Rte_Write_CellPkgSwc_Runs_Runs"])
        self.assertEqual(len(re.findall(r"Rte_Age_\w+ = 1u;", rte_c)), 2)
        self.assertIn("extern uint8 Rte_Age_CellPkgSwc_Cells_mV_Cells_mV;", internal)
        # deadline: Run20ms in the 10 ms task dev = 2 cycles, counted in writeOutputs_dev before the publication
        dev = config[config.index("void writeOutputs_dev()"):]
        dev = dev[:dev.index("\n}\n")]
        self.assertIn("Rte_Cpy_dev_Age_CellPkgSwc_Cells_mV_Cells_mV = ageQualifier("
                      "Rte_Age_CellPkgSwc_Cells_mV_Cells_mV, ageStates[0], 2U);", dev)
        self.assertLess(dev.index("ageQualifier("), dev.index("publish the values of this cycle"))
        self.assertIn("Rte_Buf_Age_CellPkgSwc_Cells_mV_Cells_mV = Rte_Cpy_dev_Age_CellPkgSwc_Cells_mV_Cells_mV;", dev)
        app = config[config.index("void readInputs_app()"):]
        self.assertIn("Rte_Cpy_app_Age_CellPkgSwc_Cells_mV_Cells_mV = Rte_Buf_Age_CellPkgSwc_Cells_mV_Cells_mV;",
                      app[:app.index("\n}\n")])
        self.assertNotIn("ageQualifier(", config[config.index("void writeOutputs_app()"):]
                         [:config[config.index("void writeOutputs_app()"):].index("\n}\n")])
        # one qualifier per supervised port, both readers share nothing else
        self.assertEqual(config.count("= ageQualifier("), 2)
        self.assertIn("AgeState ageStates[2] = {};", config)

    def test_deadline_follows_periods(self):
        """The deadline is the period of the writing runnable in cycles of its task: another task period, another
        runnable period - no value of its own."""
        tasks = copy.deepcopy(ECU["tasks"])
        tasks[1]["period_ms"] = 20                           # dev every 20 ms: Run20ms every cycle
        out = self.run_with(self.ecu(tasks=tasks))
        self.assertIn("ageStates[0], 1U);", (out / "RteConfig.cpp").read_text(encoding="utf-8"))
        tasks[1]["period_ms"] = 5                            # dev every 5 ms: 4 cycles
        out = self.run_with(self.ecu(tasks=tasks), out="out5")
        self.assertIn("ageStates[0], 4U);", (out / "RteConfig.cpp").read_text(encoding="utf-8"))

    def test_same_task_and_several_readers(self):
        """Writer and reader in one task: no copies, the reader sees the buffer; several ports may read it."""
        tasks = [{"name": "app", "period_ms": 10, "priority": 25, "stack_bytes": 1024,
                  "runnables": ["CellPkgSwc.Run20ms", "CellAppSwc.Run10ms"]}]
        out = self.run_with(self.ecu(tasks=tasks, CellAppSwc__CurrentAge__CurrentAge=CELLS))
        config = (out / "RteConfig.cpp").read_text(encoding="utf-8")
        rte_c = (out / "Rte.c").read_text(encoding="utf-8")
        self.assertEqual(config.count("= ageQualifier("), 1)
        self.assertIn("Rte_Buf_Age_CellPkgSwc_Cells_mV_Cells_mV = ageQualifier(", config)
        self.assertNotIn("Rte_Cpy_", rte_c)
        self.assertIn("*data = (uint16)Rte_Buf_Age_CellPkgSwc_Cells_mV_Cells_mV;", rte_c)
        self.assertNotIn("Rte_Age_CellPkgSwc_Current_mA_Current_mA", rte_c)   # no longer supervised

    def test_without_age_target_nothing_changes(self):
        ecu = self.ecu(CellAppSwc__CellsAge__CellsAge="rte.init", CellAppSwc__CurrentAge__CurrentAge="rte.init")
        out = self.run_with(ecu)
        for name in ("Rte.c", "Rte_Internal.h", "RteConfig.cpp"):
            text = (out / name).read_text(encoding="utf-8")
            self.assertNotIn("SYS-32", text, name)
            self.assertNotIn("Rte_Age", text, name)
            self.assertNotIn("ageQualifier", text, name)

    def test_messages(self):
        cases = [
            ({"CellAppSwc__CellsAge__CellsAge": "age.qlf.CellPkgSwc.Cells_mV"},
             "expected age.qlf.<Component>.<Port>.<Element>"),
            ({"CellAppSwc__CellsAge__CellsAge": "age.CellPkgSwc.Cells_mV.Cells_mV.x"},
             "expected age.qlf.<Component>.<Port>.<Element>"),
            ({"CellAppSwc__CellsAge__CellsAge": "age.qlf.CellPkgSwc.Nope.Nope"},
             "CellPkgSwc.Nope.Nope is not a port element of the ECU description"),
            ({"CellAppSwc__CellsAge__CellsAge": "age.qlf.CellAppSwc.CurrentAge.CurrentAge"},
             "CellAppSwc.CurrentAge.CurrentAge is a required port"),
            ({"CellAppSwc__Cells_mV__Cells_mV": CELLS}, "is an array, BSW and CAN targets take unscaled scalars"),
            ({"CellPkgSwc__Runs__Runs": CELLS}, "output port cannot use age.qlf"),
        ]
        for changes, fragment in cases:
            with self.subTest(fragment=fragment):
                self.expect_error(fragment, self.ecu(**changes))

    def test_writer_rules(self):
        """The writer must be one periodic runnable; a reading port must carry a qualifier type. A C component
        cannot have two writers (swc.yaml refuses it), an ARXML component can - age_link names both runnables."""
        pkg = rtegen.load_component("CellPkgSwc", PROFILE / "CellPkgSwc", REPO).component
        reader = rtegen.load_component("CellAppSwc", PROFILE / "CellAppSwc", REPO).component.ports["CellsAge.CellsAge"]
        task = rtegen.Task("dev", 35, "tasks[1]", period_ms=10)
        for r in pkg.runnables:
            r.task = task
        pkg.runnables.append(rtegen.Runnable("Run100ms", "CellPkgSwc_Run100ms", 100, accesses={"Current_mA.Current_mA"},
                                             explicit={"Current_mA.Current_mA"}, task=task))
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.age_link({"CellPkgSwc": pkg}, {}, "X.Q.Q", reader, CURRENT)
        self.assertIn("CellPkgSwc.Current_mA.Current_mA is written by several periodic runnables "
                      "(Run20ms 20 ms, Run100ms 100 ms)", str(ctx.exception))
        buffers = {}
        rtegen.age_link({"CellPkgSwc": pkg}, buffers, "X.Q.Q", reader, CELLS)   # the cells keep their one writer
        self.assertEqual((buffers[CELLS].age.runnable, buffers[CELLS].age.limit), ("CellPkgSwc.Run20ms", 2))
        wrong = rtegen.Port("Q", "in", "", "Q", pkg.ports["Current_mA.Current_mA"].type, "0")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.age_link({"CellPkgSwc": pkg}, {}, "X.Q.Q", wrong, CELLS)
        self.assertIn("type sint32 does not fit age.qlf (uint16, uint32, uint8)", str(ctx.exception))

    def test_writer_in_several_tasks(self):
        """A supervised port whose writers run in several tasks (a mode switch runnable in another task than the
        periodic writer) is refused by the task plan: the flag of Rte_Write would be taken from two tasks."""
        ecu = rtegen.ecu_description(ECU)
        components = {c.name: rtegen.load_component(c.name, Path(c.path), REPO).component for c in ecu.components}
        manifest = rtegen.load_task_manifest(MULTI_TASKS)
        app, _ = rtegen.build_tasks(ECU, components, manifest, None)
        buffers, port_buffer = rtegen.build_rte(ECU, components, rtegen.board_signals(PROJECT))
        other = rtegen.Runnable("OnRunEntry", "CellPkgSwc_OnRunEntry", None, accesses={"Cells_mV.Cells_mV"},
                                explicit={"Cells_mV.Cells_mV"}, task=app[0], mode_events=["entry"])
        components["CellPkgSwc"].runnables.append(other)
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.plan_tasks(components, buffers, port_buffer, app, None)
        self.assertIn(f"{CELLS}: CellPkgSwc.Cells_mV.Cells_mV is written from several tasks", str(ctx.exception))

    def test_implicit_and_mode_writers(self):
        """A port written with implicit access (RTE-09) or only by mode switch runnables (RTE-08) has no age of its
        own to supervise - age_link says why (components of test/implicit and test/modes)."""
        reader = rtegen.load_component("CellAppSwc", PROFILE / "CellAppSwc", REPO).component.ports["CellsAge.CellsAge"]
        producer = rtegen.load_component("ProducerSwc", HERE / "test" / "implicit" / "ProducerSwc", REPO).component
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.age_link({"ProducerSwc": producer}, {}, "X.Q.Q", reader, "age.qlf.ProducerSwc.Count.Count")
        self.assertIn("with implicit access (RTE-09)", str(ctx.exception))
        self.assertIn("SM-05", str(ctx.exception))
        mode = rtegen.load_component("ModeSwc", HERE / "test" / "modes" / "ModeSwc", REPO).component
        writers = [r for r in mode.runnables if "EntryCount.EntryCount" in r.accesses]
        self.assertTrue(writers and all(not r.period_ms for r in writers), "fixture: written by mode runnables")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.age_link({"ModeSwc": mode}, {}, "X.Q.Q", reader, "age.qlf.ModeSwc.EntryCount.EntryCount")
        self.assertIn("no periodic runnable of ModeSwc writes ModeSwc.EntryCount.EntryCount", str(ctx.exception))

    def test_validate_reports_with_yaml_path(self):
        """The Studio check (validate) reports a wrong age target like generate, at the YAML path of the connection;
        the profile itself is clean."""
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ECU, sort_keys=False), encoding="utf-8")
        project = target_build.project_file(self.tmp / "ok", path)
        self.assertEqual([str(m) for m in rtegen.validate(project, REPO, tasks_manifest=MULTI_TASKS)
                          if m.severity == "error"], [])
        path.write_text(yaml.safe_dump(self.ecu(CellPkgSwc__Runs__Runs=CELLS), sort_keys=False), encoding="utf-8")
        project = target_build.project_file(self.tmp / "bad", path)
        errors = [m for m in rtegen.validate(project, REPO, tasks_manifest=MULTI_TASKS) if m.severity == "error"]
        self.assertEqual(len(errors), 1, [str(m) for m in errors])
        self.assertIn("output port cannot use age.qlf", errors[0].text)
        self.assertEqual((errors[0].file, errors[0].path), (path, ("connections", "CellPkgSwc.Runs.Runs")))


DIAG = {
    "schema_version": 1,
    "operation_cycles": [{"name": "Power", "kind": "power"}],
    "fault_memory": {"max_entries": 2, "overflow": "replace_oldest", "cycle_ms": 10, "write_interval_ms": 500},
    "dtcs": [
        {"number": 0x100010, "name": "CellsStale", "operation_cycle": "Power",
         "event": {"bsw": CELLS}, "debounce": {"counter": {"failed_threshold": 1, "passed_threshold": -1}}},
        {"number": 0x100011, "name": "CurrentStale", "operation_cycle": "Power",
         "event": {"port": "CellAppSwc.CurrentAge.CurrentAge"},
         "debounce": {"counter": {"failed_threshold": 1, "passed_threshold": -1}}},
    ],
}


class RteAgeDemTest(AgeCase):
    """The optional event of the age supervision: a DTC samples the age qualifier (NVM-03 fault memory)."""

    def description(self, data):
        path = self.tmp / "diag.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        desc, messages, _ = rtegen.nxconfig.config.read_diag(path)
        self.assertIsNotNone(desc, [str(m) for m in messages])
        return desc

    def test_dtc_samples_the_age(self):
        out = self.run_with(diag=self.description(DIAG))
        config = (out / "RteConfig.cpp").read_text(encoding="utf-8")
        sample = config[config.index("uint8_t demSample("):]
        sample = sample[:sample.index("\n}\n")]
        ok, debounce, failed, passed, none = (rtegen.QLF["OK"], rtegen.QLF["DEBOUNCE"], rtegen.DEM_RESULT["PREFAILED"],
                                              rtegen.DEM_RESULT["PREPASSED"], rtegen.DEM_RESULT["NONE"])
        for buffer in ("Rte_Buf_Age_CellPkgSwc_Cells_mV_Cells_mV", "Rte_Buf_Age_CellPkgSwc_Current_mA_Current_mA"):
            self.assertIn(f"return ({buffer} == {ok}U) ? {passed}U : ((({buffer} == {debounce}U) || "
                          f"({buffer} == {rtegen.QLF['NOT_AVAILABLE']}U)) ? {none}U : {failed}U);", sample)
        self.assertIn(f"// CellsStale: {CELLS} (qualifier)", sample)
        self.assertIn("// CurrentStale: CellAppSwc.CurrentAge.CurrentAge (qualifier)", sample)

    def test_unsupervised_port_is_named(self):
        diag = copy.deepcopy(DIAG)
        diag["dtcs"][0]["event"] = {"bsw": "age.qlf.CellPkgSwc.Runs.Runs"}
        self.expect_error("the age of CellPkgSwc.Runs.Runs is not supervised", diag=self.description(diag))


# Deliberately broken generated code: (file, original, replacement, what the host test must notice). The saturation
# of the cycle counter is not among them: without it the counter would wrap only after 2^32 cycles.
MUTATIONS = [
    ("RteConfig.cpp", "if (state.cycles >= deadline)", "if (state.cycles > deadline)",
     "stale one cycle late (deadline exclusive)"),
    ("RteConfig.cpp", "        flag          = 0U;\n", "",
     "flag not taken back: one write keeps the value fresh forever"),
    ("RteConfig.cpp", "    return state.written ? QLF_OK : QLF_DEBOUNCE;", "    return QLF_OK;",
     "never written reported as valid"),
    ("Rte.c", "    Rte_Age_CellPkgSwc_Cells_mV_Cells_mV = 1u; /* SYS-32: a new value, the age starts again */\n", "",
     "Rte_Write does not mark the value"),
    ("RteConfig.cpp", "ageStates[0], 2U);", "ageStates[0], 3U);", "deadline not the period of the writer"),
    ("RteConfig.cpp", "        state.cycles  = 0U;\n", "", "age not restarted by a write"),
]


class RteAgeHostTest(unittest.TestCase):
    """Generated RTE of test/age + CellPkgSwc.c + CellAppSwc.c + test/age/rte_age_host_test.cpp."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nx_age_host_"))
        cls.out = cls.tmp / "gen"
        rtegen.generate(PROFILE / "rte.yaml", PROJECT, cls.out, REPO, MULTI_TASKS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build_and_run(self, out: Path, name: str) -> subprocess.CompletedProcess:
        includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                    f"-I{HERE / 'test' / 'can_host' / 'stubs'}"]
        build = self.tmp / name
        build.mkdir()
        strict = ["-Wconversion", "-Wsign-conversion", "-pedantic"]      # hand-written components, as for LevelMonSwc
        objects = []
        for src, extra in ((out / "Rte.c", []), (PROFILE / "CellPkgSwc/src/CellPkgSwc.c", strict),
                           (PROFILE / "CellAppSwc/src/CellAppSwc.c", strict)):
            obj = build / (src.stem + ".o")
            r = subprocess.run(ZIG + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *extra, *includes, "-c",
                                      str(src), "-o", str(obj)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            objects.append(str(obj))
        exe = build / "rte_age_host_test.exe"
        r = subprocess.run(ZIG + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
                                  "-Wno-sign-conversion", "-Werror", *includes, str(out / "RteConfig.cpp"),
                                  str(PROFILE / "rte_age_host_test.cpp"), *objects, "-o", str(exe)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        return subprocess.run([str(exe)], capture_output=True, text=True)

    def test_age_runtime(self):
        r = self.build_and_run(self.out, "ok")
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertIn(", 0 failures", r.stdout)

    def test_mutations_are_found(self):
        """Each deliberately broken variant of the generated code fails the host test (the test is not blind)."""
        for i, (name, original, replacement, what) in enumerate(MUTATIONS):
            with self.subTest(what=what):
                out = self.tmp / f"mut{i}"
                shutil.copytree(self.out, out)
                text = (out / name).read_text(encoding="utf-8")
                self.assertEqual(text.count(original), 1, f"mutation anchor not found once: {original!r}")
                (out / name).write_text(text.replace(original, replacement), encoding="utf-8")
                r = self.build_and_run(out, f"mut{i}_build")
                self.assertNotEqual(r.returncode, 0, f"mutation not found: {what}\n{r.stdout[-2000:]}")
                self.assertIn("FAIL", r.stdout)


if __name__ == "__main__":
    unittest.main()
