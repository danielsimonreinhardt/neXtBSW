"""COM-45: status values of the basic software as sources of a data identifier (bsw.rte.*, bsw.calibration.*,
tools/rtegen BSW_STATUS) - for the diagnosis only, read through the readers of the integration (bswstatus::rte,
ecu/application systems/RteSystem.cpp).

Own profile test/diag_status/diag.yaml with the ECU description of test/diag (synthetic components of
test/multitask) and the project buildtest_s32k148_min, not the eval board. The host test compiles the generated RTE
with stand-ins of the readers and runs test/diag_status/rte_diag_status_host_test.cpp, then built-in faults.

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_diag_status.py
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

import rtegen  # noqa: E402
import rteapi  # noqa: E402  (tools/diaggen, put on the path by rtegen)

DIAG_DIR = HERE / "test" / "diag"
STATUS_DIR = HERE / "test" / "diag_status"
MULTI = HERE / "test" / "multitask"
MULTI_TASKS = MULTI / "tasks.yaml"
PROJECT_FILE = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
PROJECT = rtegen.nxconfig.load(PROJECT_FILE)
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_FILE and is skipped where it is missing.
DEMO_FILE = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_FILE = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
STATUS = yaml.safe_load((STATUS_DIR / "diag.yaml").read_text(encoding="utf-8"))


def description(data: dict, tmp: Path):
    path = tmp / "diag.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    desc, messages, _ = rtegen.nxconfig.config.read_diag(path)
    assert desc is not None, messages
    return desc


class RteDiagStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, diag):
        with contextlib.redirect_stderr(io.StringIO()):
            return rtegen.generate(DIAG_DIR / "rte.yaml", PROJECT, self.tmp / "out", REPO, MULTI_TASKS,
                                   diag=description(diag, self.tmp))

    def generated(self, name="RteConfig.cpp"):
        return (self.tmp / "out" / name).read_text(encoding="utf-8")

    def element(self, did: int, element: int, **values):
        data = copy.deepcopy(STATUS)
        data["data_identifiers"][did]["data"][element].update(values)
        return data

    def expect_error(self, fragment, diag):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(diag)
        self.assertIn(fragment, str(ctx.exception))

    def test_functions_read_the_status(self):
        self.run_with(STATUS)
        header = self.generated("RteDiag.h")
        self.assertIn("bool readDid_RteStatus(uint32_t& violations, uint16_t& violations16);", header)
        self.assertIn("bool readDid_CalibrationState(uint32_t& layout, uint32_t& dataset, uint8_t& status);", header)
        config = self.generated()
        # declarations of the readers, only because the description needs them
        self.assertIn("namespace bswstatus", config)
        self.assertIn("void readRteStatus(uint32_t& rte_deadline_violations);", config)
        self.assertIn("void readCalibrationStatus(uint32_t& cal_layout, uint32_t& cal_dataset, uint8_t& cal_status);",
                      config)
        self.assertIn("violations = static_cast<uint32_t>(bsw_rte_deadline_violations); "
                      "// bsw.rte.deadline_violations", config)
        # no BSW service hand-over for them
        self.assertNotIn("::bswservice::rte::", config)

    def test_safe_off_take_backs(self):
        """SYS-196: bsw.safeoff.* read through the reader of the family port's integration, with the signature its
        header declares (platforms/s32k1xx/port/main include/integration/SafeOffTakeBackReport.h) - the generated
        declaration and the definition must name the same function, or the image does not link."""
        data = copy.deepcopy(STATUS)
        data["data_identifiers"].append({
            "identifier": 0x0140, "name": "SafeOffTakeBack", "read": True, "read_sessions": ["Default"],
            "data": [{"name": "output", "type": "uint8", "source": {"bsw": "bsw.safeoff.output"}},
                     {"name": "outputTakeBacks", "type": "uint32", "source": {"bsw": "bsw.safeoff.output_take_backs"}},
                     {"name": "takeBacks", "type": "uint16", "source": {"bsw": "bsw.safeoff.take_backs"}}]})
        self.run_with(data)
        config = self.generated()
        declared = ("void readSafeOffStatus(uint8_t& safeoff_output, uint32_t& safeoff_output_take_backs, "
                    "uint32_t& safeoff_take_backs);")
        self.assertIn(declared, config)
        self.assertIn("::bswstatus::rte::readSafeOffStatus(bsw_safeoff_output, bsw_safeoff_output_take_backs, "
                      "bsw_safeoff_take_backs);", config)
        self.assertIn("// bsw.safeoff.take_backs", config)
        port = (REPO / "platforms" / "s32k1xx" / "port" / "main" / "include" / "integration"
                / "SafeOffTakeBackReport.h").read_text(encoding="utf-8")
        def types(text):
            return re.findall(r"(uint\d+_t)&", text.split("readSafeOffStatus(", 1)[1].split(")", 1)[0])
        self.assertEqual(types(port), types(declared))
        self.assertEqual(len(types(port)), 3)
        self.assertIn("namespace bswstatus", port)

    def test_without_status_values_nothing_is_declared(self):
        data = copy.deepcopy(STATUS)
        data["data_identifiers"] = data["data_identifiers"][:2]
        self.run_with(data)
        self.assertNotIn("bswstatus", self.generated())

    def test_unknown_value(self):
        self.expect_error("unknown value deadlines of the BSW status rte (known: deadline_violations)",
                          self.element(2, 0, source={"bsw": "bsw.rte.deadlines"}))
        self.expect_error("service one of power, reset, time, rte, calibration",
                          self.element(2, 0, source={"bsw": "bsw.status"}))

    def test_identities_are_whole(self):
        """The layout and dataset identifiers are identities - a clamped or cut identity would be a wrong one."""
        self.expect_error("bsw.calibration.layout", self.element(3, 0, type="uint16"))
        self.expect_error("bsw.calibration.dataset", self.element(3, 1, type="float32"))

    def test_not_writable(self):
        data = copy.deepcopy(STATUS)
        data["data_identifiers"][2]["write"] = True
        with self.assertRaises(Exception):
            self.run_with(data)

    def test_values_of_the_project(self):
        """diag_bsw_values (Studio): every project offers the status values, whatever its board."""
        for project in (PROJECT, rtegen.nxconfig.load(DEMO_FILE)):
            values = rtegen.diag_bsw_values(project)
            for name in ("bsw.rte.deadline_violations", "bsw.calibration.layout", "bsw.calibration.dataset",
                         "bsw.calibration.status"):
                self.assertIn(name, values)
            self.assertEqual(values["bsw.calibration.layout"]["types"], ("uint32",))
            self.assertNotIn("float32", values["bsw.rte.deadline_violations"]["types"])
            self.assertTrue(all(rteapi.is_rte_bsw(name) for name in values))

    def test_status_is_no_service_of_the_components(self):
        """bsw.rte.* is for the diagnosis: a component port cannot be connected to it (no hand-over per task)."""
        self.assertNotIn("bsw.rte.deadline_violations", rtegen.BSW_SERVICE_KINDS)
        self.assertFalse(set(rtegen.BSW_STATUS) & set(rtegen.BSW_SERVICES))

    def test_calibration_status_numbers(self):
        """The numbers of bsw.calibration.status are calibration::Status (ecu/calibration), in the generator's unit
        and in the text table of the template tools/diaggen/standard_diag.yaml."""

        header = (REPO / "ecu" / "calibration" / "include" / "calibration" / "CalibrationSegment.h").read_text(
            encoding="utf-8")
        body = header.split("enum class Status : uint8_t")[1].split("};")[0]
        names = re.findall(r"^\s*([A-Z_]+),", body, re.M)
        self.assertEqual(names[0], "VALID")
        unit = rtegen.BSW_STATUS["calibration"]["values"]["status"]["unit"]
        template = yaml.safe_load((REPO / "tools" / "diaggen" / "standard_diag.yaml").read_text(encoding="utf-8"))
        state = next(d for d in template["data_identifiers"] if d["name"] == "CalibrationState")
        table = {v["value"]: v["text"] for v in state["data"][2]["values"]}
        for number, name in enumerate(names):
            self.assertIn(f"{number} {name}", unit)
            self.assertEqual(table[number], name)
        self.assertEqual((table[0xFE], table[0xFF]), ("NOT_CHECKED", "NO_SEGMENT"))

    def test_readers_are_in_the_integration(self):
        """The readers the generated code declares are defined by the application (RteSystem.cpp) - SYS-196: the
        take-backs of the safe_off hold by the integration of the family port, where the hold lives."""
        application = (REPO / "ecu" / "application" / "src" / "systems" / "RteSystem.cpp").read_text(encoding="utf-8")
        port = (REPO / "platforms" / "s32k1xx" / "port" / "main" / "src" / "integration"
                / "Integration.cpp").read_text(encoding="utf-8")
        self.assertIn("namespace bswstatus", application)
        self.assertIn("namespace bswstatus", port)
        for status, spec in rtegen.BSW_STATUS.items():
            source = port if status == "safeoff" else application
            params = ", ".join(f"{ctype}& \\w+" for ctype, _ in spec["locals"])
            self.assertRegex(source, rf"void {spec['read']}\({params}\)")
        # defined once: the port's reader is not also in the application
        self.assertNotIn("readSafeOffStatus", application)


class RteDiagStatusHostTest(unittest.TestCase):
    """COM-45: compiles the generated RTE of test/diag_status and runs test/diag_status/rte_diag_status_host_test.cpp;
    then built-in faults."""

    def test_status_runtime(self):
        sys.path.insert(0, str(REPO / "tools" / "bswgen"))
        import bswgen  # noqa: E402

        tmp = Path(tempfile.mkdtemp())
        try:
            out, board = tmp / "gen", tmp / "board"
            desc = description(STATUS, tmp)
            with contextlib.redirect_stderr(io.StringIO()):
                rtegen.generate(DIAG_DIR / "rte.yaml", PROJECT, out, REPO, MULTI_TASKS, diag=desc)
                bswgen.generate(PROJECT_FILE, board)
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{MULTI / 'MotorSwc/generated/src'}",
                        f"-I{MULTI / 'AppSwc/generated/src'}", f"-I{board}"]
            objects = []
            for src in (out / "Rte.c", MULTI / "MotorSwc/generated/src/MotorSwc.c",
                        MULTI / "AppSwc/generated/src/AppSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *includes, "-c",
                                          str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))

            def build_and_run(config_cpp: Path, name: str):
                exe = tmp / f"{name}.exe"
                r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
                                          *includes, str(config_cpp),
                                          str(STATUS_DIR / "rte_diag_status_host_test.cpp"), *objects, "-o", str(exe)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                return subprocess.run([str(exe)], capture_output=True, text=True)

            r = build_and_run(out / "RteConfig.cpp", "rte_diag_status_host_test")
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)

            # built-in faults: the test must notice each of them
            config = (out / "RteConfig.cpp").read_text(encoding="utf-8")
            lock = "    ::async::LockType const lock;\n"
            read = ("    uint32_t bsw_rte_deadline_violations = 0U;\n"
                    "    ::bswstatus::rte::readRteStatus(bsw_rte_deadline_violations);\n"
                    "    violations = static_cast<uint32_t>(bsw_rte_deadline_violations); "
                    "// bsw.rte.deadline_violations\n")
            mutants = {
                "not clamped": ("(bsw_rte_deadline_violations > 65535U) ? static_cast<uint16_t>(65535)",
                                "(bsw_rte_deadline_violations > 65535U) ? static_cast<uint16_t>(bsw_rte_deadline_"
                                "violations)"),
                "wrong value of the calibration status": ("dataset = static_cast<uint32_t>(bsw_cal_dataset);",
                                                          "dataset = static_cast<uint32_t>(bsw_cal_layout);"),
                "status read under the RTE lock": (read + lock, lock + read),
            }
            for index, (what, (old, new)) in enumerate(mutants.items()):
                with self.subTest(mutant=what):
                    self.assertEqual(config.count(old), 1, what)
                    mutant = out / f"RteConfig_mutant{index}.cpp"
                    mutant.write_text(config.replace(old, new), encoding="utf-8")
                    r = build_and_run(mutant, f"mutant{index}")
                    self.assertNotEqual(r.returncode, 0, f"mutant not detected: {what}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
