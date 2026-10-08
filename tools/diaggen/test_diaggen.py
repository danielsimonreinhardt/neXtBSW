"""Tests of tools/diaggen (COM-28) with the own test profile test/profile/diag.yaml, not only the eval board.

Every check is provoked once with a deliberately wrong description and compared with its YAML path, because a
message at the wrong line is worth as little in the Studio as no message. The generated C++ is compiled and run
by ecu/uds/test/host/test_app_uds_host.py (AppUdsProfileTest, AppUdsTest).

Run: .venv/Scripts/python -m unittest tools/diaggen/test_diaggen.py
"""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import diaggen  # noqa: E402
import rteapi  # noqa: E402

PROFILE = HERE / "test" / "profile" / "diag.yaml"
PROFILE_DATA = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
BSW_PROFILE = HERE / "test" / "bsw" / "diag.yaml"      # COM-32: values of the basic software
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
MIN = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"


class DiagGenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, data: dict, uses_rte: bool = True) -> diaggen.Description:
        path = self.tmp / "diag.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return diaggen.load_file(path, uses_rte)

    def changed(self, change) -> dict:
        data = copy.deepcopy(PROFILE_DATA)
        change(data)
        return data

    def findings(self, data: dict, uses_rte: bool = True) -> list[tuple[tuple, str]]:
        d = self.load(data, uses_rte)
        diaggen.check(d)
        return [(m.path, m.text) for m in d.messages]

    def expect(self, change, path: tuple, fragment: str, uses_rte: bool = True):
        found = self.findings(self.changed(change), uses_rte)
        self.assertTrue(any(p == path and fragment in t for p, t in found), found)
        # the finding carries a line of the file, so the Studio can jump there
        d = self.load(self.changed(change), uses_rte)
        diaggen.check(d)
        self.assertTrue(all(m.line for m in d.messages if m.path == path))

    # ------------------------------------------------------------------------------------ the profile itself

    def test_profile_is_accepted(self):
        self.assertEqual(self.findings(PROFILE_DATA), [])

    def test_tables_of_the_profile(self):
        generated = diaggen.plan(diaggen.load_file(PROFILE))
        cpp, config = generated.source_cpp, generated.config_h
        # session timing for DiagnosticSessionControl: P2 40 ms, P2* 2000 ms in 10 ms units
        self.assertIn("#define NX_DIAG_P2_MS 40U", config)
        self.assertIn("#define NX_DIAG_P2_STAR_10MS 200U", config)
        self.assertIn("DIAG_SEED_BYTES_ARRAY             = 4U", config)
        self.assertIn("DIAG_MAX_DIDS_PER_REQUEST         = 7U", config)
        self.assertIn("uint8_t const SESSIONS[] = {0x01U, 0x03U};", cpp)
        # MotorState: every session, no security; Setpoint: read everywhere behind Factory (bit 1), written in
        # Service (bit 1 of the sessions) behind Workshop (bit 0)
        self.assertIn("{0x0200U, 9U, 0U, 0x03U, 0x00U, 0x00U, 0x00U, read_MotorState, nullptr}", cpp)
        self.assertIn("{0x0201U, 3U, 3U, 0x03U, 0x02U, 0x02U, 0x01U, read_Setpoint, write_Setpoint}", cpp)
        self.assertIn("{0x0202U, 1U, 0U, 0x02U, 0x00U, 0x00U, 0x00U, read_ServiceOnly, nullptr}", cpp)
        # levels: Factory without sessions gets every session but the default one
        self.assertIn("{0x03U, 2U, 3U, 2U, 1000U, 0x02U, 1U}, // Workshop (hmac-sha256)", cpp)
        self.assertIn("{0x11U, 4U, 4U, 1U, 500U, 0x02U, 1U}, // Factory (hmac-sha256)", cpp)
        # routines: Calibrate without stop, Flush in every session without security
        self.assertIn("{0x0400U, 0x02U, 0x01U, {{2U, check_Calibrate_start, run_Calibrate_start}, "
                      "{0U, nullptr, nullptr}, {0U, nullptr, run_Calibrate_results}}}", cpp)
        self.assertIn("{0x0401U, 0x03U, 0x00U, {{0U, nullptr, run_Flush_start}", cpp)
        self.assertEqual(generated.cmake.splitlines()[-1], "set(DIAG_USES_RTE TRUE)")

    def test_encoding_of_values(self):
        cpp = diaggen.plan(diaggen.load_file(PROFILE)).source_cpp
        # constant uint16 little endian, text filled with NUL
        self.assertIn("static uint8_t const revision_bytes[] = {0x34U, 0x12U};", cpp)
        self.assertIn("static uint8_t const code_bytes[] = {0x4EU, 0x58U, 0x00U, 0x00U};", cpp)
        # port values: little endian int16, big endian float, session byte of the stack
        self.assertIn("putUnsigned(response, static_cast<uint32_t>(current), 2U, false)", cpp)
        self.assertIn("putFloat(response, temperature, true)", cpp)
        self.assertIn("putUnsigned(response, static_cast<uint32_t>(values.activeSession), 1U, true)", cpp)
        # writes: signed decode, range and text table checked before the RTE is called
        self.assertIn("int16_t const setpoint = static_cast<int16_t>(signExtend(getUnsigned(&data[0U], 2U, true), "
                      "16U));", cpp)
        self.assertIn("if ((setpoint < -500) || (setpoint > 500)) { return DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE; }",
                      cpp)
        self.assertIn("if ((mode != 0U) && (mode != 1U) && (mode != 3U))", cpp)
        write = cpp.split("DiagReturnCode::Type write_Setpoint")[1].split("return DiagReturnCode::OK")[0]
        self.assertLess(write.index("ISO_REQUEST_OUT_OF_RANGE"), write.index("::rte::diag::writeDid_Setpoint"))

    def test_constant_encoding(self):
        class E:
            def __init__(self, type, value, width, order="big"):
                self.type, self.width_bytes, self.byte_order = type, width, order
                self.source = type and __import__("types").SimpleNamespace(constant=value)
        self.assertEqual(diaggen.encode_constant(E("int16", -2, 2)), [0xFF, 0xFE])
        self.assertEqual(diaggen.encode_constant(E("int16", -2, 2, "little")), [0xFE, 0xFF])
        self.assertEqual(diaggen.encode_constant(E("float32", 1.0, 4)), [0x3F, 0x80, 0x00, 0x00])
        self.assertEqual(diaggen.encode_constant(E("ascii", "AB", 3)), [0x41, 0x42, 0x00])

    def test_rte_functions_of_the_profile(self):
        desc = diaggen.load_file(PROFILE).desc
        self.assertEqual([f.signature() for f in rteapi.functions(desc)], [
            "bool readDid_MotorState(int16_t& current, float& temperature)",
            "bool readDid_Setpoint(int16_t& setpoint, uint8_t& mode)",
            "void writeDid_Setpoint(int16_t setpoint, uint8_t mode)",
            "void routine_Calibrate_start(uint16_t target, int8_t& status)",
            "void routine_Calibrate_results(int32_t& offset)",
            "void routine_Flush_start()",
        ])
        stub = diaggen.plan(diaggen.load_file(PROFILE)).rte_stub
        self.assertIn('"did.MotorState.current"', stub)
        self.assertIn('"routine.Flush.start"', stub)

    # ------------------------------------------------------------------------------------ projects

    def test_demo_project(self):
        d = diaggen.load_project(DEMO)
        self.assertFalse(d.default)
        cpp = diaggen.plan(d).source_cpp
        self.assertIn("{0xFD00U, 4U, 4U, 0x03U, 0x02U, 0x00U, 0x01U, read_RamTestValue, write_RamTestValue}", cpp)
        self.assertIn("if (!::rte::diag::readDid_MotorNtcFiltered(motorNtcFiltered)) "
                      "{ return DiagReturnCode::ISO_CONDITIONS_NOT_CORRECT; }", cpp)
        self.assertIn("::rte::diag::routine_LedSelfTest_start(durationCycles, accepted);", cpp)
        self.assertEqual(diaggen.validate(DEMO), [])

    def test_project_without_description_gets_the_default(self):
        """buildtest_s32k148_min names no diag.yaml: the identifiers of before COM-28, without RTE access."""
        d = diaggen.load_project(MIN)
        self.assertTrue(d.default)
        self.assertEqual(d.file, diaggen.DEFAULT_DESCRIPTION)
        generated = diaggen.plan(d)
        self.assertEqual([hex(x.identifier) for x in d.desc.data_identifiers], ["0xf180", "0xf181", "0xf186", "0xfd00"])
        self.assertIn("{0xFD00U, 4U, 4U, 0x03U, 0x02U, 0x00U, 0x00U, read_RamTestValue, write_RamTestValue}",
                      generated.source_cpp)
        self.assertNotIn("SECURITY_LEVELS", generated.source_cpp)
        self.assertEqual(generated.cmake.splitlines()[-1], "set(DIAG_USES_RTE FALSE)")
        self.assertNotIn("#include <RteDiag.h>", generated.source_cpp)

    def test_default_matches_the_demo_identifiers(self):
        """The default description serves F180/F181/F186/FD00 exactly like the demo's description, except the
        security level of the demo's FD00 write (no levels in the default)."""
        default = {d.identifier: d for d in diaggen.load_file(diaggen.DEFAULT_DESCRIPTION).desc.data_identifiers}
        demo = {d.identifier: d for d in diaggen.load_project(DEMO).desc.data_identifiers}
        for identifier, did in default.items():
            with self.subTest(hex(identifier)):
                other = demo[identifier]
                self.assertEqual([(e.type, e.width_bytes, e.source) for e in did.data],
                                 [(e.type, e.width_bytes, e.source) for e in other.data])
                self.assertEqual((did.read, did.write), (other.read, other.write))

    def test_write_files(self):
        diaggen.write(diaggen.plan(diaggen.load_file(PROFILE)), self.tmp / "out", rte_stub=True)
        for name in ("diag/DiagConfig.h", "DiagDescription.cpp", "diag_components.cmake", "RteDiag.h",
                     "DiagRteStub.cpp", "diag/DiagDtcGroups.h"):
            self.assertTrue((self.tmp / "out" / name).is_file(), name)
        before = (self.tmp / "out" / "DiagDescription.cpp").stat().st_mtime_ns
        diaggen.write(diaggen.plan(diaggen.load_file(PROFILE)), self.tmp / "out")
        self.assertEqual((self.tmp / "out" / "DiagDescription.cpp").stat().st_mtime_ns, before, "rewritten unchanged")

    # ------------------------------------------------------------------------------------ DTC groups (NVM-09)

    def test_dtc_groups_of_the_demo(self):
        header = diaggen.plan(diaggen.load_project(DEMO)).dtc_groups_h
        self.assertIn("constexpr size_t DIAG_DTC_GROUP_COUNT = 2U;", header)
        self.assertIn("inline ::etl::span<DtcGroup const> diagDtcGroups()", header)
        self.assertIn("        {0x800000U, 0x800000U, 0xBFFFFFU}, // Body\n"
                      "        {0xC00000U, 0xC00000U, 0xFFFEFFU}, // Network\n", header)
        self.assertIn('#include "uds/app/FaultMemoryServices.h"', header)

    def test_dtc_groups_in_the_order_of_the_description(self):
        def change(d):
            d["dtc_groups"] = [{"name": "Zeta", "group": 0xFFFFD0, "first": 0x123456, "last": 0x123456},
                               {"name": "Alpha", "group": 0x0A0000, "first": 0x0A0000, "last": 0x0AFFFF}]
        header = diaggen.plan(self.load(self.changed(change))).dtc_groups_h
        self.assertIn("        {0xFFFFD0U, 0x123456U, 0x123456U}, // Zeta\n"
                      "        {0x0A0000U, 0x0A0000U, 0x0AFFFFU}, // Alpha\n", header)

    def test_without_dtc_groups_the_table_is_empty(self):
        """The default description and a description without `dtc_groups`: 14 clears all DTCs or one."""
        for d in (diaggen.load_project(MIN), diaggen.load_file(PROFILE)):
            with self.subTest(d.file.name):
                header = diaggen.plan(d).dtc_groups_h
                self.assertIn("constexpr size_t DIAG_DTC_GROUP_COUNT = 0U;", header)
                self.assertIn("{\n    return {};\n}", header)
                self.assertNotIn("GROUPS[]", header)

    # ------------------------------------------------------------------------------------ findings

    def test_session_the_stack_cannot_enter(self):
        def change(d):
            d["sessions"][1]["id"] = 0x40
        self.expect(change, ("sessions", 1, "id"), "enters only 0x01 (default) and 0x03 (extended)")

    def test_sessions_with_different_timing(self):
        def change(d):
            d["sessions"][1]["p2_ms"] = 60
        self.expect(change, ("sessions", 1, "p2_ms"), "answers one pair for every application session")

    def test_p2_star_in_units_of_10_ms(self):
        def change(d):
            for s in d["sessions"]:
                s["p2_star_ms"] = 2005
        self.expect(change, ("sessions", 0, "p2_star_ms"), "units of 10 ms")

    def test_unknown_value_of_the_stack(self):
        def change(d):
            d["data_identifiers"][0]["data"][3]["source"] = {"bsw": "app.gain"}
        self.expect(change, ("data_identifiers", 0, "data", 3, "source", "bsw"),
                    "neither a value of the diagnostic stack")
        text = [t for _, t in self.findings(self.changed(change))][0]
        self.assertIn("ecu.*, adc.*, bsw.*", text)

    # ------------------------------------------------------------------ COM-32: values of the basic software

    def test_bsw_profile_is_accepted(self):
        data = yaml.safe_load(BSW_PROFILE.read_text(encoding="utf-8"))
        self.assertEqual(self.findings(data), [])

    def test_bsw_values_through_the_rte(self):
        """The RTE delivers ecu.*, adc.*, bsw.* values with the ports of the same identifier (one read function);
        constants and values of the stack stay in the job, writes carry ports only."""
        desc = diaggen.load_file(BSW_PROFILE).desc
        self.assertEqual([f.signature() for f in rteapi.functions(desc)], [
            "bool readDid_EcuStatus(uint8_t& state, uint32_t& wakeup, uint8_t& canWake)",
            "bool readDid_Supply(uint16_t& voltage, uint8_t& quality, uint32_t& uptime)",
            "bool readDid_Poti(uint16_t& voltage, int32_t& value, uint8_t& quality, uint16_t& speed)",
            "bool readDid_Bandgap(float& voltage)",
            "bool readDid_Setpoint(uint16_t& setpoint)",
            "void writeDid_Setpoint(uint16_t setpoint)",
        ])
        self.assertEqual([p.bsw for p in rteapi.functions(desc)[2].params],
                         ["adc.mv.POTI", "adc.phys.POTI", "adc.qlf.POTI", None])
        self.assertTrue(rteapi.is_rte_bsw("bsw.power.ok"))
        self.assertFalse(rteapi.is_rte_bsw("uds.session"))
        self.assertFalse(any(rteapi.is_rte_bsw(name) for name in diaggen.STACK_VALUES))
        cpp = diaggen.plan(diaggen.load_file(BSW_PROFILE)).source_cpp
        read = cpp.split("read_Poti(DiagValues")[1].split("return DiagReturnCode::OK")[0]
        # a value the RTE cannot read now is NRC 0x22, not a made up value
        self.assertIn("if (!::rte::diag::readDid_Poti(voltage, value, quality, speed)) "
                      "{ return DiagReturnCode::ISO_CONDITIONS_NOT_CORRECT; }", read)
        self.assertIn("if (!putUnsigned(response, static_cast<uint32_t>(value), 4U, true))", read)
        self.assertIn("values.activeSession", read)
        # in the order of the description: constant, the RTE values, the port, the session
        order = [read.index(s) for s in ("revision_bytes", "(voltage)", "(value)", "(quality)", "(speed)",
                                        "values.activeSession")]
        self.assertEqual(order, sorted(order))
        self.assertIn("{0x0402U, 11U, 0U, 0x03U, 0x00U, 0x00U, 0x00U, read_Poti, nullptr}", cpp)
        stub = diaggen.plan(diaggen.load_file(BSW_PROFILE)).rte_stub
        self.assertIn('"did.EcuStatus.state"', stub)
        self.assertIn("    return available;", stub)
        self.assertIn("DIAG_STUB_EXPORT void diag_stub_available(int const yes)", stub)

    def test_bsw_value_needs_an_rte(self):
        def change(d):
            d["data_identifiers"][0]["data"][3]["source"] = {"bsw": "ecu.state"}
        self.expect(change, ("data_identifiers", 0, "data", 3, "source", "bsw"), "project has no ECU description",
                    uses_rte=False)

    def test_bsw_value_is_a_scalar(self):
        def change(d):
            d["data_identifiers"][2]["data"][0]["source"] = {"bsw": "adc.mv.POTI"}
        self.expect(change, ("data_identifiers", 2, "data", 0, "type"), "a scalar; type ascii")

    def test_bsw_value_is_not_writable(self):
        def change(d):
            d["data_identifiers"][1]["data"][1]["source"] = {"bsw": "ecu.state"}
        self.expect(change, ("data_identifiers", 1, "write"), "the diagnosis only reads")

    def test_value_of_the_stack_with_the_wrong_type(self):
        def change(d):
            d["data_identifiers"][0]["data"][3]["type"] = "float32"
        self.expect(change, ("data_identifiers", 0, "data", 3, "type"), "uds.session")

    def test_read_only_value_of_the_stack_is_not_writable(self):
        def change(d):
            d["data_identifiers"][3]["write"] = True
            d["data_identifiers"][3]["data"][0]["source"] = {"bsw": "uds.session"}
        self.expect(change, ("data_identifiers", 3, "write"), "which only the stack sets")

    def test_text_of_the_stack_has_a_fixed_width(self):
        """COM-33: a text of the stack always fills its length_bytes (NUL behind a shorter text, a longer one cut),
        so it may stand anywhere in an identifier and the table length is exact."""
        cpp = diaggen.plan(diaggen.load_file(PROFILE)).source_cpp
        self.assertIn("{0xF180U, 25U, 0U, 0x03U, 0x00U, 0x00U, 0x00U, read_BootSoftwareIdentification, nullptr}",
                      cpp)
        self.assertIn("{0x0203U, 21U, 0U, 0x03U, 0x00U, 0x00U, 0x00U, read_BuildInfo, nullptr}", cpp)
        read = cpp.split("read_BuildInfo(DiagValues")[1].split("return DiagReturnCode::OK")[0]
        self.assertIn("if (response.getAvailableDataLength() < 20U) { return DiagReturnCode::ISO_RESPONSE_TOO_LONG; }",
                      read)
        self.assertIn("size_t const application_length = (values.applicationSoftwareId.size() < 20U) "
                      "? values.applicationSoftwareId.size() : 20U;", read)
        self.assertIn("for (size_t i = application_length; i < 20U; ++i) { (void)response.appendUint8(0x00U); }",
                      read)
        # the text first, the session byte behind it at a fixed offset
        self.assertLess(read.index("applicationSoftwareId.data()"), read.index("values.activeSession"))
        # a text in front of other elements is no finding any more (until COM-33 it had to be the last one)
        self.assertEqual(self.findings(PROFILE_DATA), [])
        self.assertEqual(diaggen.nxmodel.TEXT_FILL_BYTE, 0x00)

    def test_ram_test_value_backs_one_element(self):
        def change(d):
            for did in d["data_identifiers"][2:4]:
                did["data"] = [{"name": "raw", "type": "uint8", "source": {"bsw": "uds.ram_test"}}]
        self.expect(change, ("data_identifiers", 3, "data", 0, "source", "bsw"), "backs one element only")

    # COM-30: chip UID for the tester and the method of the security levels
    def test_chip_uid_of_the_stack(self):
        cpp = diaggen.plan(diaggen.load_file(PROFILE)).source_cpp
        self.assertIn("{0xF18CU, 16U, 0U, 0x03U, 0x00U, 0x00U, 0x00U, read_EcuSerialNumber, nullptr}", cpp)
        self.assertIn("if (values.chipUid.empty()) { return DiagReturnCode::ISO_CONDITIONS_NOT_CORRECT; }", cpp)

    def test_chip_uid_is_raw_bytes(self):
        def change(d):
            d["data_identifiers"][4]["data"][0]["type"] = "ascii"
        self.expect(change, ("data_identifiers", 4, "data", 0, "type"), "chip.uid")

    def test_chip_uid_has_the_length_of_the_derivative(self):
        d = self.load(PROFILE_DATA)
        d.uid_bytes = 12                               # e.g. an STM32 with a 96-bit UID
        diaggen.check(d)
        self.assertTrue(any(m.path == ("data_identifiers", 4, "data", 0, "length_bytes") and "12 bytes" in m.text
                            for m in d.messages), [str(m) for m in d.messages])
        self.assertEqual(diaggen.load_project(DEMO).uid_bytes, 16)   # S32K144: mcudb uid.size

    def test_unknown_security_method_is_refused(self):
        with self.assertRaises(diaggen.DiagGenError) as ctx:
            self.load(self.changed(lambda d: d["security"][0].update(method="xor")))
        self.assertIn("security[0].method", str(ctx.exception))

    def test_port_source_is_a_scalar(self):
        def change(d):
            d["data_identifiers"][2]["data"][0]["source"] = {"port": "TestSwc.Code.Code"}
        self.expect(change, ("data_identifiers", 2, "data", 0, "source", "port"), "carries a scalar")

    def test_port_source_needs_an_rte(self):
        self.expect(lambda d: None, ("data_identifiers", 0, "data", 1, "source", "port"),
                    "needs an ECU description", uses_rte=False)
        self.expect(lambda d: None, ("routines", 0), "the project has no ECU description", uses_rte=False)

    def test_bsw_routine_call(self):
        def change(d):
            d["routines"][1]["start"]["call"] = {"bsw": "bsw.reset.request"}
        self.expect(change, ("routines", 1, "start", "call", "bsw"), "no BSW routine service yet")

    def test_routine_parameter_is_a_scalar(self):
        def change(d):
            d["routines"][0]["results"]["response"].append({"name": "log", "type": "bytes", "length_bytes": 4})
        self.expect(change, ("routines", 0, "results", "response", 1, "type"), "routine parameters are scalars")

    def test_deliberate_errors_are_errors(self):
        """Significance: a wrong description stops the generator (plan raises), a right one does not."""
        def change(d):
            d["sessions"][1]["id"] = 0x40
        with self.assertRaises(diaggen.DiagGenError) as ctx:
            diaggen.plan(self.load(self.changed(change)))
        self.assertIn("sessions[1].id", str(ctx.exception))
        diaggen.plan(self.load(PROFILE_DATA))


if __name__ == "__main__":
    unittest.main()
