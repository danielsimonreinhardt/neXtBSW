"""RTE-18: connection targets of module instances from the module manifests (module.yaml rte), not from tables here.

Before, the targets gd.*, hall.* and capture.* were fixed tables of tools/rtegen (GD_VALUES, HALL_VALUES,
CAPTURE_VALUES): a new driver that wanted to give values to the application needed a change of the generator. Now
the manifest says which values a driver offers (name, types, unit, meaning, which one is the qualifier) and how the
RTE reads them; rtegen forms the targets <prefix>.<Instance>.<value> from it.

Test profile of its own: a copy of the build test project S32K148 whose board carries two instances of a synthetic
module tempProbe (test/device_values/modules/tempProbe/module.yaml, generic reader contract access reading) - a
driver tools/rtegen has never heard of. Covered: targets and checks from the manifest, generated reader calls per
task (compiled and run on the host against a stub reader), the qualifier warning, the manifest checks of
tools/nxconfig (YAML path), and that the existing targets keep their names and values from their manifests.

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_device_values.py
"""

import contextlib
import io
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

import rtegen  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402
from nxconfig import model as nxmodel  # noqa: E402

PROFILE = HERE / "test" / "device_values"
MODULES = PROFILE / "modules"
ECU_FILE = PROFILE / "rte.yaml"
BT148 = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
BOARD = REPO / "boards" / "buildtest_s32k148" / "board.yaml"
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"

ALARM = "DemoSwc.Button1.Button1"            # boolean, Run10ms (app10)
QLF = "DemoSwc.PcCommandQlf.PcCommandQlf"    # uint8, Run10ms
OHM = "DemoSwc.MotorNtc_mV.MotorNtc_mV"      # uint32, Run100ms (app100)
PROVIDED = "DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV"   # uint32 provided port


def write_profile(tmp: Path, devices=("PROBE_A", "PROBE_B"), module_dirs=None, rte=ECU_FILE):
    """Build test board with the probes as board devices, the project with an entry modules.<probe> each."""
    board = yaml.safe_load(BOARD.read_text(encoding="utf-8"))
    board["devices"] += [{"name": name, "part": "TPROBE-A1", "module": "tempProbe"} for name in devices]
    board_dir = tmp / "boards" / "buildtest_s32k148"
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "board.yaml").write_text(yaml.safe_dump(board, sort_keys=False), encoding="utf-8")
    project = yaml.safe_load(BT148.read_text(encoding="utf-8"))
    project["project"]["board"] = board_dir.as_posix()
    project["project"]["rte"] = Path(rte).as_posix()
    project["modules"] = {**(project.get("modules") or {}), **{name: {} for name in devices}}
    folder = tmp / "project"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project.yaml").write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    dirs = nxcfg.MODULE_DIRS + tuple(module_dirs if module_dirs is not None else (MODULES,))
    return folder / "project.yaml", dirs


def load_profile(tmp: Path, **kwargs):
    project_file, dirs = write_profile(tmp, **kwargs)
    return rtegen.nxconfig.load(project_file, module_dirs=dirs)


class DeviceValueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = Path(tempfile.mkdtemp())
        cls.config = load_profile(cls.shared)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.shared, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ecu(self, **connections):
        ecu = rtegen.with_resolved_paths(yaml.safe_load(ECU_FILE.read_text(encoding="utf-8")), ECU_FILE)
        ecu["connections"].update(connections)
        return ecu

    def generate(self, ecu):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = rtegen.generate(path, self.config, self.tmp / "out", REPO)
        return result, stderr.getvalue()

    def expect_error(self, fragment, **connections):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.generate(self.ecu(**connections))
        self.assertIn(fragment, str(ctx.exception))

    def generated(self):
        return (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")

    # ------------------------------------------------------------------ targets from the manifest

    def test_instances_and_values_come_from_the_manifest(self):
        signals = rtegen.board_signals(self.config)
        self.assertEqual(signals["tprobe"], {"PROBE_A", "PROBE_B"})
        source = signals[rtegen.DEVICE_SOURCES]["tprobe"]
        self.assertEqual((source.module, source.access, source.qualifier), ("tempProbe", "reading", "qlf"))
        self.assertEqual(list(source.values), ["temp_c10", "resistance_ohm", "alarm", "qlf"])
        self.assertEqual(source.values["temp_c10"]["types"], {"sint16", "sint32"})
        self.assertEqual(source.values["temp_c10"]["field"], "temperature")
        self.assertEqual(source.values["alarm"]["field"], "alarm")          # left out: the value name
        values, check = rtegen.measured_targets(signals)["tprobe"]
        self.assertIs(values, source.values)
        self.assertTrue(set(source.kinds) <= set(rtegen.IO_INPUT_KINDS))

    def test_the_generator_does_not_know_the_module(self):
        """The whole point: nothing in tools/rtegen names the new driver - its manifest is enough."""
        for file in (HERE / "rtegen.py", *sorted((HERE / "templates").glob("*.j2"))):
            text = file.read_text(encoding="utf-8")
            for word in ("tempProbe", "tprobe", "TempProbe"):
                self.assertNotIn(word, text, file.name)

    def test_existing_targets_keep_their_names_from_their_manifests(self):
        """gd.*, hall.* and capture.* now come from libs/bsw/gdTmc6200 and the family port manifests."""
        sources = rtegen.board_signals(rtegen.nxconfig.load(DEMO))[rtegen.DEVICE_SOURCES]
        expected = {
            "gd": ("gdTmc6200", "gate_driver", ["faults", "enabled", "qlf"]),
            "hall": ("hall", "hall", ["state", "sector", "period_us", "mech_period_us", "speed_rpm", "direction",
                                      "qlf"]),
            "capture": ("capture", "input_capture", ["freq_mhz", "period_us", "duty_permille", "qlf"]),
        }
        for prefix, (module, access, values) in expected.items():
            with self.subTest(prefix=prefix):
                source = sources[prefix]
                self.assertEqual((source.module, source.access, list(source.values)), (module, access, values))
                self.assertEqual(source.qualifier, "qlf")
                self.assertEqual(source.values["qlf"]["field"], "quality")
                fields = {spec["field"] for spec in source.values.values()}
                self.assertLessEqual(fields, set(rtegen.DEVICE_ACCESS_FIELDS[access]))

    def test_reserved_prefixes_cover_the_fixed_kinds(self):
        """A module prefix must never shadow a target kind of the generator (tools/nxconfig RTE_RESERVED_PREFIXES)."""
        fixed = {kind.split(".")[0] for kind in (*rtegen.BSW_KINDS, *rtegen.CAN_KINDS, rtegen.QLF_KIND,
                                                 rtegen.ECU_MODE_TARGET)}
        fixed |= {rtegen.BSW_SERVICE_PREFIX, rtegen.NVM_PREFIX, rtegen.AGE_PREFIX, rtegen.DEM_PREFIX,
                  rtegen.PINEVENT_PREFIX, "rte", *rtegen.DIAG_PREFIXES}
        self.assertLessEqual(fixed, set(nxmodel.RTE_RESERVED_PREFIXES))

    # ------------------------------------------------------------------ generated code

    def test_one_reader_per_instance_and_task(self):
        _, buffers, _ = self.generate(self.ecu())[0]
        self.assertEqual((buffers[ALARM].bsw, buffers[ALARM].bsw_signal), ("tprobe.alarm", "PROBE_A"))
        config = self.generated()
        self.assertIn("#include <tempProbe/TempProbeRte.h>", config)
        self.assertIn("void readTempProbe_PROBE_A(::tempprobe::Reading& reading);", config)
        self.assertIn("void readTempProbe_PROBE_B(::tempprobe::Reading& reading);", config)
        self.assertEqual(config.count("::integration::rte::readTempProbe_PROBE_A("), 1)   # alarm and qlf: one call
        self.assertEqual(config.count("::integration::rte::readTempProbe_PROBE_B("), 1)
        self.assertIn("Rte_Buf_DemoSwc_Button1_Button1 = tprobe_PROBE_A.alarm ? 1U : 0U;", config)
        self.assertIn("Rte_Buf_DemoSwc_PcCommandQlf_PcCommandQlf = static_cast<uint8>(tprobe_PROBE_A.quality);",
                      config)
        self.assertIn("Rte_Buf_DemoSwc_MotorNtc_mV_MotorNtc_mV = static_cast<uint32>(tprobe_PROBE_B.resistance);",
                      config)
        app10, app100 = config.split("// ---- task app100")
        self.assertIn("readTempProbe_PROBE_A(tprobe_PROBE_A)", app10)
        self.assertNotIn("readTempProbe_PROBE_B(tprobe_PROBE_B)", app10)
        self.assertIn("readTempProbe_PROBE_B(tprobe_PROBE_B)", app100)
        self.assertIn('"tprobe.PROBE_A.alarm"', config)                                   # console "rte signals"

    def test_an_instance_read_in_two_tasks_is_read_in_each(self):
        self.generate(self.ecu(**{OHM: "tprobe.PROBE_A.resistance_ohm"}))
        self.assertEqual(self.generated().count("::integration::rte::readTempProbe_PROBE_A("), 2)
        self.assertEqual(self.generated().count("void readTempProbe_PROBE_A("), 1)
        self.assertNotIn("readTempProbe_PROBE_B", self.generated())    # an instance nobody reads is not declared

    def test_without_a_connection_no_code(self):
        ecu = self.ecu(**{ALARM: "dio.in.BUTTON1", QLF: "rte.init", OHM: "adc.mv.POTI"})
        self.generate(ecu)
        for absent in ("tempProbe", "tprobe", "readTempProbe"):
            self.assertNotIn(absent, self.generated())

    def test_missing_qualifier_is_a_warning(self):
        _, stderr = self.generate(self.ecu())
        self.assertIn("connections: tprobe.PROBE_B.qlf is not connected, so a component cannot tell a measured "
                      "temperature from a held one (probe not answering): connect it to a required port", stderr)
        self.assertNotIn("tprobe.PROBE_A.qlf is not connected", stderr)

    # ------------------------------------------------------------------ checks with the words of the manifest

    def test_connection_errors(self):
        cases = [
            ({ALARM: "tprobe.PROBE_A"}, "expected tprobe.<Probe>.<value> with value one of temp_c10, resistance_ohm, "
                                        "alarm, qlf"),
            ({ALARM: "tprobe.PROBE_A.alarms"}, "unknown tprobe value alarms (known: temp_c10, resistance_ohm, alarm, "
                                               "qlf)"),
            ({ALARM: "tprobe.PROBE_C.alarm"}, "PROBE_C is no temperature probe of the project (board devices with "
                                              "module tempProbe the project uses: PROBE_A, PROBE_B)"),
            ({OHM: "tprobe.PROBE_B.temp_c10"}, "type uint32 does not fit tprobe.PROBE_B.temp_c10 (sint16, sint32, "
                                               "0.1 degC)"),
            ({PROVIDED: "tprobe.PROBE_A.resistance_ohm"}, "output port cannot use tprobe.PROBE_A.resistance_ohm "
                                                          "(a measured value is an input)"),
            ({ALARM: "tprobe.probe_a.alarm"}, "is no temperature probe of the project"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect_error(fragment, **connections)

    def test_project_without_the_module_has_no_such_target(self):
        """Without the manifest (another project) tprobe is no target kind: the target is unknown, not guessed."""
        config = load_profile(self.tmp / "none", devices=(), module_dirs=())
        signals = rtegen.board_signals(config)
        self.assertNotIn("tprobe", signals)
        self.assertNotIn("tprobe", rtegen.measured_targets(signals))

    # ------------------------------------------------------------------ manifest checks (tools/nxconfig)

    def manifest_messages(self, rte: str, kind: str = "device_driver", second: str | None = None):
        """Messages of loading the profile with a changed tempProbe manifest (rte section replaced)."""
        folder = self.tmp / "mods"
        text = (MODULES / "tempProbe" / "module.yaml").read_text(encoding="utf-8")
        head = text.split("\nrte:\n")[0].replace("kind: device_driver", f"kind: {kind}")
        (folder / "tempProbe").mkdir(parents=True, exist_ok=True)
        (folder / "tempProbe" / "module.yaml").write_text(head + "\n" + rte, encoding="utf-8")
        if second is not None:
            (folder / "otherProbe").mkdir(parents=True, exist_ok=True)
            (folder / "otherProbe" / "module.yaml").write_text(second, encoding="utf-8")
        project_file, dirs = write_profile(self.tmp / "p", module_dirs=(folder,))
        _config, messages = nxcfg.check(project_file, module_dirs=dirs)
        return [m for m in messages if m.severity == "error"]

    def test_manifest_errors_have_a_yaml_path(self):
        values = "  values:\n    t: { types: [sint16], unit: 0.1 degC }\n"
        reading = "  include: a/B.h\n  reading: ::a::B\n  read: readB\n"
        cases = [
            ("rte:\n  prefix: adc\n" + reading + values, ("rte", "prefix"), "is a target kind of tools/rtegen"),
            ("rte:\n  prefix: tp\n" + values, ("rte",), "access reading needs include, reading and read"),
            ("rte:\n  prefix: tp\n  access: hall\n" + reading + values, ("rte",), "belong to access reading"),
            ("rte:\n  prefix: tp\n" + reading + "  values:\n    q: { types: [sint16], unit: x, qualifier: true }\n",
             ("rte",), "qualifier value q takes uint8, uint16 or uint32 only"),
            ("rte:\n  prefix: tp\n" + reading + "  values: {}\n", ("rte", "values"), "at least 1 item"),
        ]
        for rte, path, fragment in cases:
            with self.subTest(fragment=fragment):
                errors = self.manifest_messages(rte)
                match = [m for m in errors if fragment in m.text]
                self.assertTrue(match, [str(m) for m in errors])
                self.assertEqual(match[0].file.name, "module.yaml")
                self.assertEqual(tuple(match[0].path[:len(path)]), path)

    def test_a_prefix_is_given_once(self):
        other = ("module: otherProbe\nkind: device_driver\nparts: [OPROBE-*]\nrte:\n  prefix: tprobe\n"
                 "  include: o/O.h\n  reading: ::o::R\n  read: readO\n  values:\n    t: { types: [uint8], unit: x }\n")
        text = (MODULES / "tempProbe" / "module.yaml").read_text(encoding="utf-8").split("\nrte:\n")[1]
        errors = self.manifest_messages("rte:\n" + text, second=other)
        match = [m for m in errors if "RTE prefix tprobe is also the one of module" in m.text]
        self.assertTrue(match, [str(m) for m in errors])
        self.assertEqual(tuple(match[0].path), ("rte", "prefix"))

    def test_devices_need_a_device_driver(self):
        errors = self.manifest_messages("rte:\n  prefix: tp\n  include: a/B.h\n  reading: ::a::B\n  read: readB\n"
                                        "  values:\n    t: { types: [sint16], unit: x }\n", kind="service")
        self.assertTrue([m for m in errors if "instances devices needs a device driver" in m.text],
                        [str(m) for m in errors])

    def test_access_code_members_are_checked(self):
        """An existing access code reads the members its driver API has; a manifest cannot invent another one."""
        manifest = nxmodel.Manifest.model_validate({
            "module": "gdX", "kind": "device_driver",
            "rte": {"prefix": "gdx", "access": "gate_driver",
                    "values": {"temp": {"types": ["uint8"], "unit": "x", "field": "temperature"}}}})
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.device_source(manifest, set(), "module.yaml")
        self.assertIn("rte.values: temp (temperature) is no member the access code gate_driver reads", str(ctx.exception))


class DeviceValueHostTest(unittest.TestCase):
    """Compiles the generated RTE of the profile against a stub reader and runs test/device_values."""

    def test_reader_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            config = load_profile(tmp / "profile")
            out = tmp / "gen"
            with contextlib.redirect_stderr(io.StringIO()):
                rtegen.generate(ECU_FILE, config, out, REPO)
            host = HERE / "test"
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{PROFILE / 'stubs'}", f"-I{host / 'can_host' / 'stubs'}",
                        f"-I{REPO / 'swc/DemoSwc/generated/src'}"]
            objects = []
            for src in (out / "Rte.c", REPO / "swc/DemoSwc/generated/src/DemoSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *includes, "-c",
                                          str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))
            exe = tmp / "rte_device_values_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", *includes,
                                      str(out / "RteConfig.cpp"), str(PROFILE / "rte_device_values_host_test.cpp"),
                                      *objects, "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn("0 failures", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
