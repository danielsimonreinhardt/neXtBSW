# SPDX-License-Identifier: Apache-2.0
"""Host tests of the component description swc.yaml (RTE-07), without MATLAB.

Run: .venv/Scripts/python -m unittest tools/swc/test_swc_yaml.py
"""

import copy
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "rtegen"))

import mdata  # noqa: E402
import swc_yaml  # noqa: E402

FIXTURES = HERE / "test" / "fixtures"
TEMPLATE = REPO / "swc" / "template" / "swc.yaml"

VALID = {
    "version": 1,
    "name": "TestSwc",
    "runnables": [{"name": "Run5ms", "period_ms": 5}, {"name": "Run1000ms", "period_ms": 1000}],
    "ports": [
        {"name": "In1", "direction": "in", "type": "sint16", "runnables": ["Run5ms", "Run1000ms"]},
        {"name": "Out1", "direction": "out", "type": "float32", "runnables": ["Run1000ms"]},
    ],
}


class SwcYamlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def errors(self, data, folder=None):
        swc, messages = swc_yaml.check(data, folder_name=folder)
        errors = [m for m in messages if m.severity == "error"]
        if errors:
            self.assertIsNone(swc)
        return errors

    def expect_error(self, data, yaml_path, fragment):
        errors = self.errors(data)
        self.assertTrue(any(m.yaml_path == yaml_path and fragment in m.text for m in errors),
                        f"expected '{fragment}' at {yaml_path}, got: {[str(m) for m in errors]}")

    # ---- valid descriptions ----
    def test_valid_description_is_normalized(self):
        swc, messages = swc_yaml.check(copy.deepcopy(VALID))
        self.assertEqual(messages, [])
        out = swc.to_json()
        self.assertEqual([(r["name"], r["period_ms"], r["sample_time"]) for r in out["runnables"]],
                         [("Run5ms", 5, "0.005"), ("Run1000ms", 1000, "1")])
        self.assertEqual([(p["name"], p["simulink_type"], p["runnables"]) for p in out["ports"]],
                         [("In1", "int16", ["Run5ms", "Run1000ms"]), ("Out1", "single", ["Run1000ms"])])

    def test_port_unit_and_engineering_limits_are_optional(self):
        """TOOL-150: unit/min/max of a port (A2L PHYS_UNIT/LOWER_LIMIT/UPPER_LIMIT) are optional, like a
        measurement's or a parameter's - checked the same way (_check_limits: min <= max, inside the type range)."""
        data = copy.deepcopy(VALID)
        data["ports"][0]["unit"] = "rpm"
        data["ports"][0]["min"], data["ports"][0]["max"] = -100, 100
        swc, messages = swc_yaml.check(data)
        self.assertEqual(messages, [])
        self.assertEqual((swc.ports[0].unit, swc.ports[0].min, swc.ports[0].max), ("rpm", -100, 100))
        self.assertEqual((swc.ports[1].unit, swc.ports[1].min, swc.ports[1].max), ("", None, None))
        # min > max, and a limit outside the type range - checked exactly like a parameter's
        data = copy.deepcopy(VALID)
        data["ports"][0]["min"], data["ports"][0]["max"] = 500, 100
        self.expect_error(data, "ports[0].min", "min 500 is greater than max 100")
        data = copy.deepcopy(VALID)
        data["ports"][0]["type"] = "uint8"
        data["ports"][0]["min"] = -1
        self.expect_error(data, "ports[0].min", "outside the range of uint8")

    def test_all_platform_types_map_to_simulink(self):
        expected = {"boolean": "boolean", "uint8": "uint8", "uint16": "uint16", "uint32": "uint32",
                    "sint8": "int8", "sint16": "int16", "sint32": "int32", "float32": "single",
                    "float64": "double"}
        self.assertEqual(swc_yaml.TYPES, expected)

    def test_template_is_valid(self):
        swc, messages = swc_yaml.load(TEMPLATE, check_folder=False)
        self.assertEqual([m for m in messages if m.severity == "error"], [])
        self.assertEqual(swc.name, "NewSwc")

    def test_demoswc_fixture_matches_the_checked_in_arxml(self):
        """The DemoSwc fixture describes the same runnables and ports as swc/DemoSwc (rtegen reads the ARXML)."""
        import rtegen
        swc, _ = swc_yaml.load(FIXTURES / "DemoSwc" / "swc.yaml")
        comp = rtegen.parse_component("DemoSwc", REPO / "swc" / "DemoSwc" / "generated" / "arxml")
        self.assertEqual({r.name: r.period_ms for r in comp.runnables if r.period_ms is not None},
                         {r.name: r.period_ms for r in swc.runnables})
        self.assertEqual({key.split(".")[0]: (p.direction, p.type.native) for key, p in comp.ports.items()},
                         {p.name: (p.direction, p.type) for p in swc.ports})
        # RTE-03: calibration parameters of the ParameterReceiver port CalPrm, values from the same data file
        self.assertEqual({key: (p.type.native, p.dim) for key, p in comp.parameters.items()},
                         {f"{swc_yaml.PARAMETER_PORT}.{p.name}": (p.type, p.dim) for p in swc.parameters})
        fixture = mdata.load(FIXTURES / "DemoSwc" / "DemoSwc_data.m")
        real = mdata.load(REPO / "swc" / "DemoSwc" / "DemoSwc_data.m")
        self.assertEqual({n: fixture.get(n) for n in fixture.names}, {n: real.get(n) for n in real.names})

    def test_fixtures_are_valid(self):
        for name in ("DemoSwc", "QlfTestSwc", "PortTestSwc"):
            with self.subTest(name):
                swc, messages = swc_yaml.load(FIXTURES / name / "swc.yaml")
                self.assertEqual(messages, [])
                self.assertEqual(swc.name, name)

    # ---- errors with YAML path ----
    def test_missing_runnables(self):
        data = copy.deepcopy(VALID)
        del data["runnables"]
        self.expect_error(data, "runnables", "at least one runnable")

    def test_period_must_be_positive_whole_ms(self):
        for period in (0, -10, 2.5, "10", True):
            with self.subTest(period=period):
                data = copy.deepcopy(VALID)
                data["runnables"][0]["period_ms"] = period
                self.expect_error(data, "runnables[0].period_ms", "whole number of milliseconds")

    def test_names_are_identifiers(self):
        data = copy.deepcopy(VALID)
        data["runnables"][1]["name"] = "Run 1s"
        data["ports"][0]["runnables"] = ["Run5ms", "Run 1s"]   # reported once at the runnable, not per port
        data["ports"][1]["runnables"] = ["Run 1s"]
        data["ports"][0]["name"] = "1In"
        data["name"] = "Test-Swc"
        errors = self.errors(data)
        self.assertEqual({m.yaml_path for m in errors}, {"name", "runnables[1].name", "ports[0].name"})

    def test_port_name_length_is_the_autosar_short_name(self):
        """TOOL-134: a port name is limited by the AUTOSAR SHORT-NAME (128), not by its Rte_Write call."""
        data = copy.deepcopy(VALID)
        data["ports"][1]["name"] = "O" * (swc_yaml.MAX_SHORT_NAME + 1)
        self.expect_error(data, "ports[1].name", "(129 characters) is longer than 128 characters (AUTOSAR SHORT-NAME")

    def test_long_real_port_names_are_accepted(self):
        """TOOL-134, user test step 04: a 32-character port was refused with "longer than 26 characters"."""
        data = copy.deepcopy(VALID)
        data["ports"][0]["name"] = "SysNvm_ti_OperatingMinutesNvRead"
        data["ports"][1]["name"] = "SysNvm_ti_OperatingMinutesNvWrite"
        self.assertEqual(self.errors(data), [])
        swc = swc_yaml.check(data)[0]
        longest = max(swc_yaml.generated_identifiers(swc.name, [((), p) for p in swc.ports], [], [],
                                                     swc.implementation), key=len)
        self.assertGreater(len(longest), swc_yaml.SIGNIFICANT_CHARS)   # longer than 63 is fine while distinct

    def test_generated_identifiers_must_be_distinct_in_63_characters(self):
        """TOOL-134: MISRA C:2012 5.1/5.2 - two ports whose RTE calls agree in the first 63 characters clash."""
        data = copy.deepcopy(VALID)
        base = "P" * 60
        data["ports"][0]["name"] = base + "A"
        data["ports"][0]["direction"] = "out"
        data["ports"][0]["runnables"] = ["Run5ms"]
        data["ports"][1]["name"] = base + "B"
        errors = self.errors(data)
        self.assertEqual({m.yaml_path for m in errors}, {"ports[1].name"})
        text = errors[0].text
        self.assertIn(f"Rte_Write_{base}B_{base}B (133 characters)", text)
        self.assertIn(f"from Rte_Write_{base}A_{base}A", text)
        self.assertIn("first 63 characters", text)

    def test_identifier_clashes(self):
        a, b = "x" * 63 + "a", "x" * 63 + "b"
        self.assertEqual(swc_yaml.identifier_clashes([a, b, a, "x" * 62, "short"]), [(a, b)])
        self.assertEqual(swc_yaml.identifier_clashes(["x" * 63, a]), [("x" * 63, a)])
        self.assertEqual(swc_yaml.identifier_clashes(["y" * 62 + "a", "y" * 62 + "b"]), [])

    def test_duplicates_across_runnables_and_ports(self):
        data = copy.deepcopy(VALID)
        data["runnables"].append({"name": "Run5ms", "period_ms": 10})
        data["ports"].append({"name": "Run1000ms", "direction": "in", "type": "uint8", "runnables": ["Run5ms"]})
        data["ports"].append({"name": "In1", "direction": "in", "type": "uint8", "runnables": ["Run5ms"]})
        self.expect_error(data, "runnables[2].name", "already defined at runnables[0].name")
        self.expect_error(data, "ports[2].name", "already used at runnables[1].name")
        self.expect_error(data, "ports[3].name", "already used at ports[0].name")

    def test_init_runnable_is_reserved(self):
        data = copy.deepcopy(VALID)
        data["runnables"][0]["name"] = "TestSwc_Init"
        self.expect_error(data, "runnables[0].name", "init runnable")

    def test_port_fields(self):
        data = copy.deepcopy(VALID)
        data["ports"][0]["direction"] = "inout"
        data["ports"][1]["type"] = "int16"
        self.expect_error(data, "ports[0].direction", "must be one of in, out")
        self.expect_error(data, "ports[1].type", "no supported platform type")

    def test_port_runnables(self):
        data = copy.deepcopy(VALID)
        data["ports"][0]["runnables"] = ["Run5ms", "RunUnknown"]
        data["ports"][1]["runnables"] = ["Run5ms", "Run1000ms"]
        self.expect_error(data, "ports[0].runnables[1]", "'RunUnknown' is not defined")
        self.expect_error(data, "ports[1].runnables", "exactly one runnable may write it")
        data = copy.deepcopy(VALID)
        data["ports"][0]["runnables"] = []
        self.expect_error(data, "ports[0].runnables", "must list the runnables")

    def test_unknown_keys_are_errors(self):
        data = copy.deepcopy(VALID)
        data["perods"] = 1
        data["runnables"][0]["offset_ms"] = 2
        data["ports"][0]["init"] = 0
        errors = self.errors(data)
        self.assertEqual({m.yaml_path for m in errors}, {"perods", "runnables[0].offset_ms", "ports[0].init"})

    def test_version(self):
        data = copy.deepcopy(VALID)
        data["version"] = 2
        self.expect_error(data, "version", "not supported")

    def test_unused_runnable_is_a_warning(self):
        data = copy.deepcopy(VALID)
        data["runnables"].append({"name": "Idle", "period_ms": 50})
        swc, messages = swc_yaml.check(data)
        self.assertIsNotNone(swc)
        self.assertEqual([(m.severity, m.yaml_path) for m in messages], [("warning", "runnables[2].name")])

    def test_folder_name_must_match(self):
        errors = self.errors(copy.deepcopy(VALID), folder="OtherSwc")
        self.assertEqual([m.yaml_path for m in errors], ["name"])

    # ---- file and command line ----
    def test_messages_carry_file_and_line(self):
        folder = self.tmp / "TestSwc"
        folder.mkdir()
        file = folder / "swc.yaml"
        file.write_text("name: TestSwc\nrunnables:\n  - name: Run5ms\n    period_ms: 0\n", encoding="utf-8")
        with self.assertRaises(swc_yaml.SwcErrors) as ctx:
            swc_yaml.load(file)
        message = ctx.exception.messages[0]
        self.assertEqual((message.file, message.line, message.yaml_path), (file, 4, "runnables[0].period_ms"))
        self.assertIn("swc.yaml:4 (runnables[0].period_ms): error:", str(message))

    def test_command_line_json(self):
        folder = self.tmp / "TestSwc"
        folder.mkdir()
        (folder / "swc.yaml").write_text(yaml.safe_dump(VALID), encoding="utf-8")
        out = self.tmp / "out.json"
        self.assertEqual(swc_yaml.main(["json", str(folder), "-o", str(out)]), 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "TestSwc")
        self.assertEqual(data["ports"][0]["runnables"], ["Run5ms", "Run1000ms"])
        (folder / "swc.yaml").write_text("name: TestSwc\nrunnables: []\n", encoding="utf-8")
        self.assertEqual(swc_yaml.main(["check", str(folder)]), 1)


class QualifierConstantsTest(unittest.TestCase):
    """The MATLAB constants used by the SensorQualifier tests must be the ones rtegen generates (RTE-07)."""

    PROPOSED_RANGE = ("ERROR_RANGE", 32,
                      "value outside its valid range, longer than debounce_ms (SensorQualifier of a component, RTE-07)")

    def test_fixture_equals_rtegen_output_with_the_range_value(self):
        import rtegen
        values = list(rtegen.QLF_VALUES)
        if "ERROR_RANGE" in rtegen.QLF:
            # proposal merged into tools/rtegen: value and precedence position must be the decided ones
            self.assertEqual(rtegen.QLF["ERROR_RANGE"], self.PROPOSED_RANGE[1])
        else:
            values.append(self.PROPOSED_RANGE)
        original = rtegen.QLF_VALUES
        try:
            rtegen.QLF_VALUES = values
            expected = rtegen.render_qlf_matlab()
        finally:
            rtegen.QLF_VALUES = original
        fixture = (FIXTURES / "Rte_Qlf.m").read_text(encoding="utf-8")
        self.assertEqual(fixture.strip(), expected.strip())

    def test_range_value_is_free_and_an_error_value(self):
        import rtegen
        existing = {value for name, value, _ in rtegen.QLF_VALUES if name != "ERROR_RANGE"}
        value = self.PROPOSED_RANGE[1]
        self.assertNotIn(value, existing)
        self.assertGreater(value, rtegen.QLF["OK"])        # "qualifier > QLF_OK" means error (RteCan.cpp)
        self.assertLessEqual(value, 255)                   # Rte_QlfType is uint8
        self.assertEqual(value & (value - 1), 0)           # one bit, like the other error values

    def test_library_script_uses_the_constant_names(self):
        script = (HERE / "swc_lib_create.m").read_text(encoding="utf-8")
        for name in ("QLF_OK", "QLF_DEBOUNCE", "QLF_ERROR_RANGE"):
            self.assertRegex(script, rf"'{name}'")
        self.assertIsNone(re.search(r"'Value', '\d+'.*'uint8'", script), "no qualifier value as a number")


WITH_PARAMETERS = dict(copy.deepcopy(VALID), parameters=[
    {"name": "Gain", "type": "uint16", "runnables": ["Run5ms"]},
    {"name": "Table", "type": "sint16", "dim": 4, "runnables": ["Run5ms", "Run1000ms"]},
    {"name": "Factor", "type": "uint16", "lsb": 0.001, "runnables": ["Run1000ms"]},
    {"name": "Temp", "type": "sint16", "lsb": 0.1, "offset": -40, "runnables": ["Run1000ms"]},
])


class SwcYamlParameterTest(unittest.TestCase):
    """RTE-03: parameters: of swc.yaml (description only) and the values in <Swc>_data.m."""

    setUp, tearDown = SwcYamlTest.setUp, SwcYamlTest.tearDown
    errors, expect_error = SwcYamlTest.errors, SwcYamlTest.expect_error

    def write(self, data, values):
        folder = self.tmp / data["name"]
        folder.mkdir()
        (folder / "swc.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        if values is not None:
            (folder / f"{data['name']}_data.m").write_text(values, encoding="utf-8")
        return folder / "swc.yaml"

    def test_parameters_are_normalized(self):
        swc, messages = swc_yaml.check(copy.deepcopy(WITH_PARAMETERS))
        self.assertEqual(messages, [])
        out = swc.to_json()
        self.assertEqual(out["parameter_port"], "CalPrm")
        self.assertEqual([(p["name"], p["simulink_type"], p["dim"]) for p in out["parameters"]],
                         [("Gain", "uint16", 1), ("Table", "int16", 4), ("Factor", "fixdt(0,16,0.001,0)", 1),
                          ("Temp", "fixdt(1,16,0.1,-40)", 1)])

    def test_values_come_from_the_data_file(self):
        file = self.write(WITH_PARAMETERS, "Gain = 3;\nTable = [1 2 3 4];\nFactor = 1.5;\nTemp = 25;\n")
        swc, messages = swc_yaml.load(file)
        self.assertEqual(messages, [])
        self.assertEqual([p.values for p in swc.parameters], [[3], [1, 2, 3, 4], [1.5], [25]])
        self.assertEqual(swc.to_json()["parameters"][1]["values"], [1, 2, 3, 4])

    def test_missing_data_file_and_value(self):
        file = self.write(WITH_PARAMETERS, None)
        with self.assertRaises(swc_yaml.SwcErrors) as ctx:
            swc_yaml.load(file)
        self.assertIn("no data file TestSwc_data.m", str(ctx.exception))
        (file.parent / "TestSwc_data.m").write_text("Gain = 3;\nTable = [1 2 3];\nFactor = 1;\n", encoding="utf-8")
        with self.assertRaises(swc_yaml.SwcErrors) as ctx:
            swc_yaml.load(file)
        paths = {m.yaml_path: m.text for m in ctx.exception.messages}
        self.assertIn("Table has 3 value(s), dim is 4", paths["parameters[1].dim"])
        self.assertIn("Temp is not set in", paths["parameters[3].name"])

    def test_parameter_errors(self):
        data = copy.deepcopy(WITH_PARAMETERS)
        data["parameters"][0]["lsb"] = 0.5
        data["parameters"][0]["type"] = "float32"
        self.expect_error(data, "parameters[0].lsb", "fixed point (lsb) needs an integer type")
        data = copy.deepcopy(WITH_PARAMETERS)
        data["parameters"][1]["dim"] = 0
        self.expect_error(data, "parameters[1].dim", "must be a whole number >= 1")
        data = copy.deepcopy(WITH_PARAMETERS)
        data["parameters"][2]["runnables"] = ["Run7ms"]
        self.expect_error(data, "parameters[2].runnables[0]", "runnable 'Run7ms' is not defined")
        data = copy.deepcopy(WITH_PARAMETERS)
        data["parameters"][0]["name"] = "In1"
        self.expect_error(data, "parameters[0].name", "name 'In1' is already used")
        data = copy.deepcopy(WITH_PARAMETERS)
        data["parameters"][0]["offset"] = 3
        self.expect_error(data, "parameters[0].offset", "offset needs lsb")
        data = copy.deepcopy(WITH_PARAMETERS)
        data["parameters"][0]["value"] = 3
        self.expect_error(data, "parameters[0].value", "unknown key 'value'")    # values only in <Swc>_data.m
        data = copy.deepcopy(WITH_PARAMETERS)
        data["ports"][0]["name"] = "CalPrm"
        self.expect_error(data, "parameters", "'CalPrm' is the name of the parameter port")

    def test_runnable_reading_only_a_parameter_is_no_warning(self):
        data = copy.deepcopy(WITH_PARAMETERS)
        data["runnables"].append({"name": "Run20ms", "period_ms": 20})
        data["parameters"][0]["runnables"] = ["Run20ms"]
        swc, messages = swc_yaml.check(data)
        self.assertEqual(messages, [])


def with_dimension(dimension=None, source=None) -> dict:
    """VALID with the dimension (and dimension_source) at its provided port Out1."""
    data = copy.deepcopy(VALID)
    if dimension is not None:
        data["ports"][1]["dimension"] = dimension
    if source is not None:
        data["ports"][1]["dimension_source"] = source
    return data


class PortDimensionTest(unittest.TestCase):
    """RTE-25: array ports - `dimension` as a number or an expression of the init data, resolved without MATLAB."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, data: dict, init: str | None = None, extra: dict | None = None) -> Path:
        folder = self.tmp / "TestSwc"
        folder.mkdir(exist_ok=True)
        file = folder / "swc.yaml"
        file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        if init is not None:
            (folder / "TestSwc_init.m").write_text(init, encoding="utf-8")
        for name, text in (extra or {}).items():
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(text, encoding="utf-8")
        return file

    def test_number(self):
        swc, messages = swc_yaml.check(with_dimension(8))
        self.assertEqual(messages, [])
        self.assertEqual((swc.ports[1].dimension, swc.ports[1].array, swc.ports[0].dimension), (8, True, 1))
        self.assertEqual(swc.to_json()["ports"][1]["dimension"], 8)
        for bad, fragment in ((0, "between 1 (scalar)"), (swc_yaml.MAX_DIMENSION + 1, "between 1 (scalar)"),
                              (True, "whole number >= 1 or an expression"),
                              (2.5, "whole number >= 1 or an expression"),
                              ("[4 4]", "matrix [4 4]"), ("-1", "inherited")):
            with self.subTest(bad=bad):
                errors = [m for m in swc_yaml.check(with_dimension(bad))[1] if m.severity == "error"]
                self.assertEqual([m.yaml_path for m in errors], ["ports[1].dimension"], [str(m) for m in errors])
                self.assertIn(fragment, errors[0].text)

    def test_simulink_texts(self):
        cases = {"1": (1, ""), "[1 1]": (1, ""), "8": (8, ""), "[1 8]": (8, ""), "[8 1]": (8, ""),
                 "hvscmc.number_cell_voltages": (None, "hvscmc.number_cell_voltages"),
                 "[1 hvscmc.number_cell_voltages]": (None, "hvscmc.number_cell_voltages"), "": (1, "")}
        for text, (value, expression) in cases.items():
            with self.subTest(text=text):
                d = swc_yaml.parse_port_dimensions(text)
                self.assertEqual((d.value, d.expression, d.error), (value, expression, ""))
        self.assertTrue(swc_yaml.parse_port_dimensions("-1").inherited)
        self.assertIn("matrix", swc_yaml.parse_port_dimensions("[3 4]").error)
        self.assertIn("matrix unless one side is 1", swc_yaml.parse_port_dimensions("[a.n b.m]").error)
        self.assertIn("one", swc_yaml.parse_port_dimensions("[1 2 3]").error)
        self.assertEqual(swc_yaml.parse_port_dimensions("[1 8]").yaml_value(), 8)
        self.assertEqual(swc_yaml.parse_port_dimensions("[1 a.n]").yaml_value(), "a.n")

    def test_symbolic_dimension_is_resolved_from_the_init_data(self):
        """The case of the user test: PortDimensions hvscmc.number_cell_voltages, set up by the init script."""
        init = ("% neXtBSW init v1\nnx_here = fileparts(mfilename('fullpath'));\n"
                "run(fullfile(nx_here, 'init_data', 'hvscmc_param.m'));\n")
        param = ("hvscmc = struct();  % cell monitoring\nhvscmc.number_modules = 8;\n"
                 "hvscmc.cells_per_module = 12; hvscmc.number_cell_voltages = hvscmc.number_modules * ...\n"
                 "    hvscmc.cells_per_module;\nif false\n  other.n = 3;\nend\n"
                 "%{\nhvscmc.number_cell_voltages = 1;   % old value, commented out as a block\n%}\n"
                 "function unused()\nhvscmc.number_cell_voltages = 2;\nend\n")
        file = self.write(with_dimension("hvscmc.number_cell_voltages"), init,
                          {"init_data/hvscmc_param.m": param})
        swc, messages = swc_yaml.load(file)
        self.assertEqual(messages, [])
        self.assertEqual((swc.ports[1].dimension, swc.ports[1].dimension_source), (96, "hvscmc.number_cell_voltages"))
        self.assertEqual(swc.to_json()["ports"][1]["dimension"], 96)
        # arithmetic and Simulink.Parameter
        file = self.write(with_dimension("cfg.n + 2"), "cfg.n = Simulink.Parameter;\ncfg.n.Value = int32(4);\n")
        self.assertEqual(swc_yaml.load(file)[0].ports[1].dimension, 6)

    def test_symbolic_dimension_that_cannot_be_resolved(self):
        for init, why in (("load('cells.mat');\n", "is not assigned in the init scripts"),
                          ("if x\n  pack.n = 4;\nend\n", "is assigned inside a block"),
                          ("pack.n = 4;\npack.n = 5;\n", "is assigned different values"),
                          ("pack.n = numel(v);\n", "contains something other than numbers, names"),
                          ("pack.n = 2.5;\n", "not a whole number"),
                          (None, "there is no TestSwc_init.m")):
            with self.subTest(why=why):
                shutil.rmtree(self.tmp / "TestSwc", ignore_errors=True)
                file = self.write(with_dimension("pack.n"), init)
                with self.assertRaises(swc_yaml.SwcErrors) as ctx:
                    swc_yaml.load(file)
                errors = [m for m in ctx.exception.messages if m.severity == "error"]
                self.assertEqual([m.yaml_path for m in errors], ["ports[1].dimension"])
                self.assertIn("cannot be resolved without MATLAB", errors[0].text)
                self.assertIn(why, errors[0].text)
                self.assertIn("dimension: <n>, dimension_source: pack.n", errors[0].text)
                self.assertIsNotNone(errors[0].line)

    def test_number_with_its_source(self):
        # the number the import writes, with the expression it came from: checked where the init data tells
        file = self.write(with_dimension(4, "pack.n"), "pack.n = 4;\n")
        self.assertEqual(swc_yaml.load(file)[1], [])
        file = self.write(with_dimension(4, "pack.n"), "pack.n = 5;   % one more cell\n")
        with self.assertRaises(swc_yaml.SwcErrors) as ctx:
            swc_yaml.load(file)
        self.assertIn("has dimension 4, but its source 'pack.n' = 5 (TestSwc_init.m:1) - the init data changed; "
                      "set dimension: 5", str(ctx.exception))
        # not resolvable without MATLAB: the number is used, a warning says it was not checked
        file = self.write(with_dimension(4, "pack.n"), "load('pack.mat');\n")
        swc, messages = swc_yaml.load(file)
        self.assertEqual(swc.ports[1].dimension, 4)
        self.assertEqual([(m.severity, m.yaml_path) for m in messages], [("warning", "ports[1].dimension_source")])
        # a source goes with a number only
        errors = [m for m in swc_yaml.check(with_dimension("pack.n", "pack.n"))[1] if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in errors], ["ports[1].dimension_source"])

    def test_declared_dimensions_of_a_simulink_component(self):
        data = with_dimension("pack.n")
        data["ports"][0]["dimension"] = 3
        self.write(data, "pack.n = 7;\n")
        file, found = swc_yaml.declared_dimensions(self.tmp / "TestSwc")
        self.assertEqual([(d.port, d.value, d.source, d.path) for d in found],
                         [("In1", 3, "", ("ports", 0, "dimension")),
                          ("Out1", 7, "pack.n", ("ports", 1, "dimension"))])
        self.assertEqual(file.name, "swc.yaml")


# ---- RTE-26: calibration objects (stage K1) ------------------------------------------------------------------------

CALIB = REPO / "tools" / "rtegen" / "test" / "calib" / "CalMapSwc"

WITH_CALIBRATION = dict(copy.deepcopy(VALID), parameters=[
    {"name": "Speed", "kind": "axis", "type": "uint16", "dim": 4, "unit": "rpm", "min": 0, "max": 6000,
     "safety": "asil_b", "runnables": ["Run5ms"]},
    {"name": "Limit", "kind": "map", "type": "sint16", "lsb": 0.1, "unit": "Nm", "min": 0, "max": 200,
     "safety": "asil_b", "description": "Torque limit",
     "axes": {"x": "Speed", "y": {"dim": 2, "type": "sint8", "min": -40, "max": 125}}, "runnables": ["Run5ms"]},
    {"name": "Boost", "kind": "curve", "type": "uint16", "axis": "Speed", "runnables": ["Run5ms"]},
    {"name": "Derate", "kind": "curve", "type": "uint8", "axis": {"dim": 3, "type": "sint16"},
     "runnables": ["Run5ms"]},
    {"name": "Mode", "type": "uint8", "value_table": {0: "Off", 1: "On"}, "runnables": ["Run5ms"]},
])
CALIBRATION_VALUES = ("Speed = [0 1000 3000 6000];\nLimit_Y = [-20 80];\n"
                      "Limit = [100 90; 150 140; 200 180; 120 110];\nBoost = [0 100 200 300];\n"
                      "Derate_X = [0 50 100];\nDerate = [100 80 50];\nMode = 1;\n")


class CalibrationDefinitionTest(unittest.TestCase):
    """RTE-26: kinds, axes, unit, limits, safety class, description and value table in `parameters:`."""

    setUp, tearDown = SwcYamlTest.setUp, SwcYamlTest.tearDown
    errors, expect_error = SwcYamlTest.errors, SwcYamlTest.expect_error
    write = SwcYamlParameterTest.write

    def data(self):
        return copy.deepcopy(WITH_CALIBRATION)

    def load_errors(self, data, values) -> dict:
        shutil.rmtree(self.tmp / data["name"], ignore_errors=True)
        file = self.write(data, values)
        with self.assertRaises(swc_yaml.SwcErrors) as ctx:
            swc_yaml.load(file)
        return {m.yaml_path: m.text for m in ctx.exception.messages if m.severity == "error"}

    def test_definition_is_resolved(self):
        swc, messages = swc_yaml.check(self.data())
        self.assertEqual(messages, [])
        by_name = {p.name: p for p in swc.parameters}
        # own breakpoints are objects of kind axis in front of their owner
        self.assertEqual([p.name for p in swc.parameters], ["Speed", "Limit_Y", "Limit", "Boost", "Derate_X", "Derate",
                                                            "Mode"])
        limit = by_name["Limit"]
        self.assertEqual((limit.shape, limit.dim, limit.safety, limit.unit), ((4, 2), 8, "asil_b", "Nm"))
        self.assertEqual([a.name for a in limit.axes], ["Speed", "Limit_Y"])
        self.assertEqual((by_name["Limit_Y"].own_of, by_name["Limit_Y"].safety, by_name["Limit_Y"].dim),
                         ("Limit", "asil_b", 2))
        self.assertEqual((by_name["Boost"].shape, by_name["Derate"].shape), ((4,), (3,)))
        self.assertEqual(by_name["Mode"].value_table, {0: "Off", 1: "On"})
        self.assertEqual((by_name["Mode"].cal_kind, by_name["Mode"].calibration), ("value", True))
        out = {p["name"]: p for p in swc.to_json()["parameters"]}
        self.assertEqual((out["Limit"]["kind"], out["Limit"]["shape"], out["Limit"]["min"]), ("map", [4, 2], 0))

    def test_rte03_parameters_stay_as_they_were(self):
        swc, messages = swc_yaml.check(copy.deepcopy(WITH_PARAMETERS))
        self.assertEqual(messages, [])
        self.assertFalse(any(p.calibration for p in swc.parameters))
        self.assertEqual([(p.cal_kind, p.dim) for p in swc.parameters],
                         [("value", 1), ("array", 4), ("value", 1), ("value", 1)])

    def test_values_of_the_profile(self):
        """tools/rtegen/test/calib: every kind, loaded with its values (a map as matrix, own axes as _X/_Y)."""
        swc, messages = swc_yaml.load(CALIB / "swc.yaml")
        self.assertEqual(messages, [])
        values = {p.name: p.values for p in swc.parameters}
        self.assertEqual(values["TorqueLimit_Nm"][:3], [120, 110, 90])
        self.assertEqual(values["TorqueLimit_Nm_Y"], [-20, 25, 90])
        self.assertEqual(values["DerateCurve_X"], [-40, 60, 100, 150])

    def test_min_greater_than_max(self):
        data = self.data()
        data["parameters"][2]["min"], data["parameters"][2]["max"] = 500, 100
        self.expect_error(data, "parameters[2].min", "min 500 is greater than max 100")
        data = self.data()
        data["parameters"][1]["axes"]["y"]["min"] = 130
        self.expect_error(data, "parameters[1].axes.y.min", "min 130 is greater than max 125")

    def test_limits_inside_the_type(self):
        data = self.data()
        data["parameters"][1]["max"] = 4000           # sint16 with lsb 0.1 ends at 3276.7
        self.expect_error(data, "parameters[1].max", "max 4000 is outside the range of sint16 with lsb 0.1")
        data = self.data()
        data["parameters"][4]["min"] = -1
        self.expect_error(data, "parameters[4].min", "outside the range of uint8")

    def test_axis_not_monotonic(self):
        errors = self.load_errors(self.data(), CALIBRATION_VALUES.replace("Speed = [0 1000 3000 6000]",
                                                                          "Speed = [0 3000 1000 6000]"))
        self.assertIn("breakpoints of axis Speed are not strictly ascending (3000 at 2, 1000 at 3)",
                      errors["parameters[0]"])
        errors = self.load_errors(self.data(), CALIBRATION_VALUES.replace("Derate_X = [0 50 100]",
                                                                          "Derate_X = [0 50 50]"))
        self.assertIn("breakpoints of axis Derate_X are not strictly ascending", errors["parameters[3].axis"])

    def test_dimension_does_not_fit(self):
        # a map as a matrix of the wrong shape (2 x 4 instead of 4 x 2): same count, other layout
        errors = self.load_errors(self.data(), CALIBRATION_VALUES.replace(
            "Limit = [100 90; 150 140; 200 180; 120 110]", "Limit = [100 90 150 140; 200 180 120 110]"))
        self.assertIn("Limit is a 2 x 4 matrix; the axes give 4 x 2", errors["parameters[1].axes"])
        # a curve with fewer values than its own breakpoints
        errors = self.load_errors(self.data(), CALIBRATION_VALUES.replace("Derate = [100 80 50]", "Derate = [100 80]"))
        self.assertIn("Derate has 2 value(s), dim is 3", " ".join(errors.values()))
        # dim on a curve, a value with dim, an array or axis without dim >= 2
        data = self.data()
        data["parameters"][2]["dim"] = 4
        self.expect_error(data, "parameters[2].dim", "a curve takes its size from its axes")
        data = self.data()
        data["parameters"][4]["kind"], data["parameters"][4]["dim"] = "value", 3
        self.expect_error(data, "parameters[4].dim", "a value has one element")
        data = self.data()
        data["parameters"][0]["dim"] = 1
        self.expect_error(data, "parameters[0].dim", "kind axis needs dim >= 2")

    def test_unknown_axis(self):
        data = self.data()
        data["parameters"][2]["axis"] = "Sped"
        self.expect_error(data, "parameters[2].axis", "Boost: unknown axis 'Sped' (shared axes of this component: Speed")
        data = self.data()
        data["parameters"][1]["axes"]["x"] = "Mode"
        self.expect_error(data, "parameters[1].axes.x", "'Mode' is no shared axis (kind value)")
        data = self.data()
        data["parameters"][1]["axes"] = {"x": "Speed"}
        self.expect_error(data, "parameters[1].axes", "a map needs axes: {x: <axis>, y: <axis>}")
        data = self.data()
        del data["parameters"][2]["axis"]
        self.expect_error(data, "parameters[2].kind", "a curve needs axis")
        data = self.data()
        data["parameters"][4]["axis"] = "Speed"
        self.expect_error(data, "parameters[4].axis", "axis belongs to kind: curve")

    def test_unused_shared_axis_is_a_warning(self):
        data = self.data()
        data["parameters"][1]["axes"]["x"] = {"dim": 4, "type": "uint16", "min": 0, "max": 6000}
        data["parameters"][2]["axis"] = {"dim": 4, "type": "uint16"}
        swc, messages = swc_yaml.check(data)
        self.assertIsNotNone(swc)
        self.assertEqual([m.text for m in messages], ["shared axis Speed is used by no curve or map"])

    def test_safety_class(self):
        data = self.data()
        data["parameters"][1]["safety"] = "asil_d"
        self.expect_error(data, "parameters[1].safety", "ASIL C and D are not supported yet")
        data = self.data()
        del data["parameters"][1]["max"]
        self.expect_error(data, "parameters[1].safety", "safety asil_b needs max")
        data = self.data()
        del data["parameters"][1]["axes"]["y"]["min"]
        self.expect_error(data, "parameters[1].axes.y", "own breakpoints of Limit (safety asil_b) need min and max")
        # the shared axis is part of the value: at least the class of its user
        data = self.data()
        data["parameters"][0]["safety"] = "qm"
        self.expect_error(data, "parameters[1].axes.x", "uses axis Speed of safety qm")
        data = self.data()
        data["parameters"][0]["safety"] = "asil_a"
        self.expect_error(data, "parameters[1].axes.x", "uses axis Speed of safety asil_a")

    def test_value_table(self):
        data = self.data()
        data["parameters"][4]["value_table"] = {0: "Off", 1: "Off"}
        self.expect_error(data, "parameters[4].value_table[1]", "text 'Off' is already used for value 0")
        data = self.data()
        data["parameters"][4]["value_table"] = {256: "Big"}
        self.expect_error(data, "parameters[4].value_table[256]", "must be a whole number of uint8")
        data = self.data()
        data["parameters"][4]["lsb"] = 0.5
        self.expect_error(data, "parameters[4].value_table", "exclude each other")
        errors = self.load_errors(self.data(), CALIBRATION_VALUES.replace("Mode = 1", "Mode = 2"))
        self.assertIn("Mode = 2 is no entry of its value table (0, 1)", errors["parameters[4].value_table"])

    def test_default_values_inside_the_limits(self):
        errors = self.load_errors(self.data(), CALIBRATION_VALUES.replace("200 180", "200 280"))
        self.assertIn("Limit[6] = 280 is outside min 0 .. max 200", errors["parameters[1].max"])

    def test_own_axis_name_is_reserved(self):
        data = self.data()
        data["parameters"].append({"name": "Derate_X", "type": "uint8", "runnables": ["Run5ms"]})
        self.expect_error(data, "parameters[5].name", "name 'Derate_X' is already used at parameters[3].axis")

    def test_simulink_component_definition_without_ports(self):
        """declared_calibration reads only parameters: (and its values) of a Simulink component's swc.yaml."""
        data = self.data()
        data["ports"] = "not checked here"
        folder = self.write(data, CALIBRATION_VALUES).parent
        file, tree, parameters, messages = swc_yaml.declared_calibration(folder)
        self.assertEqual([m for m in messages if m.severity == "error"], [])
        self.assertEqual(len(parameters), 7)
        self.assertEqual(parameters[2].values[:2], [100, 90])
        self.assertEqual(swc_yaml.declared_calibration(self.tmp)[2], [])      # no swc.yaml: nothing


class MatrixDataTest(unittest.TestCase):
    """RTE-26: mdata reads a map as a matrix, row by row, with its shape."""

    def test_matrix(self):
        data = mdata.parse("M = [1 2 3; 4, 5, 6];\nV = [1 2];\nA = M;\nBad = [1 2; 3];\nS = 4;\n", Path("x_data.m"))
        self.assertEqual((data.get("M"), data.entries["M"].shape), ([1, 2, 3, 4, 5, 6], (2, 3)))
        self.assertEqual(data.entries["V"].shape, (1, 2))
        self.assertEqual(data.entries["A"].shape, (2, 3))
        self.assertIsNone(data.entries["Bad"].values)          # rows of different length: not understood
        self.assertIsNone(data.entries["S"].shape)


class MeasurementTest(unittest.TestCase):
    """TOOL-143: `measurements:` - internal values an XCP tool may read; the init data read without MATLAB."""

    setUp, tearDown, write = PortDimensionTest.setUp, PortDimensionTest.tearDown, PortDimensionTest.write

    def data(self, *items) -> dict:
        data = copy.deepcopy(VALID)
        data["measurements"] = list(items)
        return data

    def test_measurements_are_checked_with_their_yaml_path(self):
        swc, messages = swc_yaml.check(self.data({"name": "CellMax", "type": "float32", "unit": "V",
                                                   "description": "highest cell"},
                                                  {"name": "Cells", "type": "uint16", "dimension": 12}))
        self.assertEqual(messages, [])
        self.assertEqual([(m.name, m.type, m.dimension, m.unit, m.description) for m in swc.measurements],
                         [("CellMax", "float32", 1, "V", "highest cell"), ("Cells", "uint16", 12, "", "")])
        # a measurement is a signal name, not a block: it may equal a port name
        self.assertEqual(swc_yaml.check(self.data({"name": "In1", "type": "sint16"}))[1], [])
        for item, path, fragment in (
                ({"name": "X", "type": "real"}, "measurements[0].type", "no supported platform type"),
                ({"name": "1X", "type": "uint8"}, "measurements[0].name", "is no identifier"),
                ({"name": "X", "type": "uint8", "dimension": 0}, "measurements[0].dimension", "between 1"),
                ({"name": "X", "type": "uint8", "unit": ["V"]}, "measurements[0].unit", "must be text"),
                ({"name": "X", "type": "uint8", "address": 4}, "measurements[0].address", "unknown key")):
            with self.subTest(fragment=fragment):
                errors = [m for m in swc_yaml.check(self.data(item))[1] if m.severity == "error"]
                self.assertEqual([m.yaml_path for m in errors], [path], [str(m) for m in errors])
                self.assertIn(fragment, errors[0].text)
        errors = [m for m in swc_yaml.check(self.data({"name": "X", "type": "uint8"}, {"name": "X", "type": "uint8"}))[1]
                  if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in errors], ["measurements[1].name"])

    def test_measurement_engineering_limits_are_optional_and_checked(self):
        """TOOL-150: min/max of a measurement (A2L LOWER_LIMIT/UPPER_LIMIT) are optional, checked like a port's or
        a parameter's (_check_limits: min <= max, inside the type range)."""

        def errors_of(item):
            return [m for m in swc_yaml.check(self.data(item))[1] if m.severity == "error"]

        swc, messages = swc_yaml.check(self.data({"name": "CellMax", "type": "float32", "min": -10, "max": 10}))
        self.assertEqual(messages, [])
        self.assertEqual((swc.measurements[0].min, swc.measurements[0].max), (-10, 10))
        self.assertEqual(errors_of({"name": "X", "type": "uint8", "min": 5, "max": 5}), [])   # min == max: fine
        errors = errors_of({"name": "X", "type": "uint8", "min": 5, "max": 1})
        self.assertEqual([m.yaml_path for m in errors], ["measurements[0].min"])
        self.assertIn("min 5 is greater than max 1", errors[0].text)
        errors = errors_of({"name": "X", "type": "uint8", "max": 300})
        self.assertEqual([m.yaml_path for m in errors], ["measurements[0].max"])
        self.assertIn("outside the range of uint8", errors[0].text)

    def test_symbolic_dimension_of_a_measurement_and_the_lenient_reader_for_rtegen(self):
        file = self.write(self.data({"name": "Cells", "type": "uint16", "dimension": "pack.n"}), "pack.n = 7;\n")
        swc, messages = swc_yaml.load(file)
        self.assertEqual((messages, swc.measurements[0].dimension, swc.measurements[0].dimension_source),
                         ([], 7, "pack.n"))
        found_file, found, messages = swc_yaml.declared_measurements(file.parent)
        self.assertEqual(([m.dimension for m in found], messages, found_file), ([7], [], file))
        (file.parent / "TestSwc_init.m").write_text("load('pack.mat');\n", encoding="utf-8")
        _file, found, messages = swc_yaml.declared_measurements(file.parent)
        self.assertEqual(found, [])
        self.assertEqual([(m.severity, m.yaml_path) for m in messages], [("error", "measurements[0].dimension")])
        self.assertIn("dimension: <n>, dimension_source: pack.n", messages[0].text)

    def test_storage_classes_that_mark_a_measurement(self):
        for storage_class, marks in (("ExportedGlobal", True), ("ExportToFile", True), ("Auto", False),
                                     ("Model default", False), ("ImportedExtern", False), ("FileScope", False),
                                     ("", False)):
            with self.subTest(storage_class=storage_class):
                self.assertEqual(swc_yaml.is_measurement_mark(storage_class), marks)

    def test_signal_objects_of_the_init_data(self):
        import initdata
        init = ("Max = Simulink.Signal;\nMax.CoderInfo.StorageClass = 'Custom';\n"
                "Max.CoderInfo.CustomStorageClass = 'ExportToFile';\nMax.DataType = 'single';\n"
                "Max.Dimensions = [1 4];\nMax.Description = 'it''s the max';\n"
                "Plain = mpt.Signal();\nif x\n  Plain.DataType = 'uint8';\nend\n"
                "Twice = Simulink.Signal; Twice = Simulink.Signal;\nnot_a_signal = 3;\n")
        file = self.write(copy.deepcopy(VALID), init)
        found = initdata.signal_objects(file.parent, "TestSwc")
        self.assertEqual(sorted(found), ["Max"])          # Plain: changed inside a block; Twice: created twice
        m = found["Max"]
        self.assertEqual((m.effective_storage_class, m.data_type, m.dimensions, m.description, m.line),
                         ("ExportToFile", "single", "[1 4]", "it's the max", 1))

    def test_prefix_check_is_a_hook_without_a_table(self):
        swc, _messages = swc_yaml.check(self.data({"name": "b_Flag", "type": "uint8"}))
        self.assertEqual(swc_yaml.measurement_prefix_findings(swc), [])
        found = swc_yaml.measurement_prefix_findings(swc, {"b_": ("boolean",)})
        self.assertEqual([(m.severity, m.yaml_path) for m in found], [("warning", "measurements[0].type")])

    def test_prefix_is_recognized_after_one_component_prefix_too(self):
        """TOOL-150: before this fix, measurement_prefix_findings() only recognized a prefix at the very start of
        the name (m.name.startswith(prefix)) - unlike studio.core.portnaming.NamingRules.suggest(), which already
        looked after one component prefix as well (TOOL-149). So `HvsCellMod_pct_X` (pct_ after the component
        prefix HvsCellMod_) got no "not allowed" warning for float32, although pct_ only allows uint8
        (doc/naming/praefix-tabelle.yaml). Both now share swc_yaml.find_prefix()."""
        swc, _messages = swc_yaml.check(self.data({"name": "HvsCellMod_pct_X", "type": "float32"}))
        found = swc_yaml.measurement_prefix_findings(swc, {"pct_": ("uint8",)})
        self.assertEqual([(m.severity, m.yaml_path) for m in found], [("warning", "measurements[0].type")])
        self.assertEqual(found[0].text,
                         "measurement HvsCellMod_pct_X: prefix pct_ expects uint8, the type is float32")
        # a plain, unprefixed name still matches at the start, as before
        swc, _ = swc_yaml.check(self.data({"name": "pct_Y", "type": "float32"}))
        self.assertEqual(len(swc_yaml.measurement_prefix_findings(swc, {"pct_": ("uint8",)})), 1)
        # the longest prefix wins at one position, exactly like NamingRules.suggest()
        swc, _ = swc_yaml.check(self.data({"name": "C_b_x_Flag", "type": "uint8"}))
        self.assertEqual(swc_yaml.measurement_prefix_findings(swc, {"b_": ("boolean",), "b_x_": ("uint8",)}), [])
        self.assertEqual(swc_yaml.find_prefix("C_b_x_Flag", {"b_": "x", "b_x_": "y"}), "b_x_")
        self.assertIsNone(swc_yaml.find_prefix("Speed", {"b_": "x"}))


class PublicCheckApiTest(unittest.TestCase):
    """TOOL-194: check_measurements()/check_parameters() - the public API of the section checkers, for a caller
    that only has the entries of one `measurements:`/`parameters:` list, not a whole swc.yaml (the Studio's
    measurement and calibration editors, tools/studio/core/swcdetails.py); they replace reaching into the private
    _Checker/_check_measurements/_check_parameters (TOOL-190 finding 2026-09-29)."""

    def test_check_measurements_finds_the_same_as_check_on_the_whole_file(self):
        entries = [{"name": "CellMax", "type": "float32", "unit": "V"}, {"name": "X", "type": "real"}]
        result, messages = swc_yaml.check_measurements(entries)
        self.assertEqual([m.name for m in result], ["CellMax"])          # the entry with the error is left out
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in errors], ["measurements[1].type"])
        self.assertIn("no supported platform type", errors[0].text)
        data = copy.deepcopy(VALID)
        data["measurements"] = entries
        _swc, full_messages = swc_yaml.check(data)
        self.assertEqual([(m.severity, m.yaml_path, m.text) for m in messages],
                         [(m.severity, m.yaml_path, m.text) for m in full_messages])

    def test_check_measurements_without_a_tree_has_the_yaml_path_but_no_line(self):
        _result, messages = swc_yaml.check_measurements([{"name": "1X", "type": "uint8"}])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].yaml_path, "measurements[0].name")
        self.assertIsNone(messages[0].line)

    def test_check_parameters_finds_the_same_as_check_on_the_whole_file(self):
        entries = copy.deepcopy(WITH_PARAMETERS["parameters"])
        runnable_names = {r["name"] for r in VALID["runnables"]}
        result, messages = swc_yaml.check_parameters(entries, runnable_names)
        self.assertEqual(messages, [])
        self.assertEqual([p.name for p in result], ["Gain", "Table", "Factor", "Temp"])
        _swc, full_messages = swc_yaml.check(copy.deepcopy(WITH_PARAMETERS))
        self.assertEqual(full_messages, [])

    def test_check_parameters_reports_unknown_runnable(self):
        entries = [{"name": "Gain", "type": "uint16", "runnables": ["RunXms"]}]
        result, messages = swc_yaml.check_parameters(entries, {"Run5ms"})
        self.assertEqual(result, [])
        self.assertEqual([m.yaml_path for m in messages], ["parameters[0].runnables[0]"])
        self.assertIn("runnable 'RunXms' is not defined", messages[0].text)

    def test_check_parameters_seen_marks_a_name_used_elsewhere_in_the_file_as_taken(self):
        # `seen` lets a caller that only checks one section (like the Studio) still catch a name already used in
        # another section (here: a port) without checking that section again itself.
        entries = [{"name": "In1", "type": "uint16", "runnables": ["Run5ms"]}]
        seen = {"In1": ("ports", 0, "name")}
        result, messages = swc_yaml.check_parameters(entries, {"Run5ms"}, seen=seen)
        self.assertEqual(result, [])
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in errors], ["parameters[0].name"])
        self.assertIn("already used at ports[0].name", errors[0].text)
        # the caller's dict is not mutated (a copy is checked)
        self.assertEqual(seen, {"In1": ("ports", 0, "name")})


TYPE_FIXTURE = FIXTURES / "TypeYamlSwc"
TYPESWC_ARXML = REPO / "tools" / "rtegen" / "test" / "types" / "TypeSwc" / "generated" / "arxml"

WITH_TYPES = {
    "version": 1,
    "name": "TySwc",
    "runnables": [{"name": "Run10ms", "period_ms": 10}],
    "types": [
        {"name": "TySwc_Mode", "kind": "enum", "literals": ["Off", "On", {"Fault": 7}]},
        {"name": "TySwc_Level", "kind": "fixed", "base": "uint16", "lsb": 0.5, "offset": -10, "unit": "mm"},
        {"name": "TySwc_Rec", "kind": "record",
         "elements": [{"name": "Mode", "type": "TySwc_Mode"}, {"name": "Level", "type": "TySwc_Level"},
                      {"name": "Raw", "type": "uint8", "dimension": 3}]},
    ],
    "ports": [
        {"name": "ModeIn", "direction": "in", "type": "TySwc_Mode", "runnables": ["Run10ms"]},
        {"name": "Level", "direction": "in", "type": "TySwc_Level", "runnables": ["Run10ms"]},
        {"name": "Out", "direction": "out", "type": "TySwc_Rec", "runnables": ["Run10ms"]},
    ],
}


def rational(lsb, offset):
    """(numerator, denominator) of physical = (n0 + n1 * stored) / d, the form of rtegen.DataType."""
    from fractions import Fraction
    from math import lcm
    a, c = Fraction(str(lsb)), Fraction(str(offset))
    d = lcm(a.denominator, c.denominator)
    return (int(c * d), int(a * d)), d


class SwcTypesTest(unittest.TestCase):
    """RTE-15: named data types of swc.yaml (types:) - records, enumerations, fixed point."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, data, implementation=None):
        data = copy.deepcopy(data)
        if implementation:
            data["implementation"] = implementation
        return swc_yaml.check(data)

    def expect_error(self, data, yaml_path, fragment):
        swc, messages = swc_yaml.check(data)
        errors = [m for m in messages if m.severity == "error"]
        self.assertIsNone(swc)
        self.assertTrue(any(m.yaml_path == yaml_path and fragment in m.text for m in errors),
                        f"expected '{fragment}' at {yaml_path}, got: {[str(m) for m in errors]}")

    def edited(self, **changes):
        data = copy.deepcopy(WITH_TYPES)
        for key, value in changes.items():
            data[key] = value
        return data

    # ---- valid ----
    def test_types_are_resolved(self):
        swc, messages = self.check(WITH_TYPES)
        self.assertEqual(messages, [])
        mode, level, rec = swc.types
        self.assertEqual((mode.kind, mode.base, mode.literals, mode.default),
                         ("enum", "uint8", [("Off", 0), ("On", 1), ("Fault", 7)], "Off"))
        self.assertEqual((level.base, level.lsb, level.offset, level.unit), ("uint16", 0.5, -10, "mm"))
        self.assertEqual([(e.name, e.kind, e.dimension, e.simulink_type) for e in rec.elements],
                         [("Mode", "enum", 1, "Enum: TySwc_Mode"), ("Level", "fixed", 1, "TySwc_Level"),
                          ("Raw", "platform", 3, "uint8")])
        self.assertEqual([(p.type_kind, p.simulink_type) for p in swc.ports],
                         [("enum", "Enum: TySwc_Mode"), ("fixed", "TySwc_Level"), ("record", "Bus: TySwc_Rec")])
        out = swc.to_json()
        self.assertEqual([t["name"] for t in out["types"]], ["TySwc_Mode", "TySwc_Level", "TySwc_Rec"])
        self.assertEqual(out["types_file"]["name"], "TySwc_types.m")
        self.assertEqual(out["types_file"]["text"], swc_yaml.types_m(swc))
        self.assertEqual([p["type_kind"] for p in out["ports"]], ["enum", "fixed", "record"])
        # a component without types: has no types file (swc_create writes nothing)
        plain, _ = swc_yaml.check(copy.deepcopy(VALID))
        self.assertEqual((plain.to_json()["types"], plain.to_json()["types_file"]), ([], None))

    def test_order_is_free_and_dependencies_come_first(self):
        swc, messages = swc_yaml.load(TYPE_FIXTURE / "swc.yaml")
        self.assertEqual([m for m in messages if m.severity == "error"], [])
        order = [t.name for t in swc.types]
        self.assertEqual(order, ["TypeYamlSwc_Mode", "TypeYamlSwc_Speed", "TypeYamlSwc_CmdBus",
                                 "TypeYamlSwc_StatBus", "TypeYamlSwc_Temp"])
        text = swc_yaml.types_m(swc)
        at = {name: text.index(f"assignin('base', '{name}'") for name in order if "Mode" not in name}
        self.assertLess(text.index("defineEnum('TypeYamlSwc_Mode'"), at["TypeYamlSwc_CmdBus"])
        self.assertLess(at["TypeYamlSwc_Speed"], at["TypeYamlSwc_CmdBus"])
        self.assertLess(at["TypeYamlSwc_CmdBus"], at["TypeYamlSwc_StatBus"])

    def test_fixture_describes_the_rte04_model(self):
        """The swc.yaml fixture has the port types of TypeSwc (RTE-04, built by hand): same layout, element types,
        scaling and literals as rtegen reads them from its ARXML (names TypeYamlSwc_* for TypeSwc_*). SwcTypesTest.m
        proves the same for the model swc_create makes from it."""
        import rtegen
        comp = rtegen.parse_component("TypeSwc", TYPESWC_ARXML)
        swc, _ = swc_yaml.load(TYPE_FIXTURE / "swc.yaml")
        named = {t.name: t for t in swc.types}

        def signature(type_name, kind="platform", dimension=1):
            if dimension > 1:
                return ("ARRAY", dimension, signature(type_name, kind))
            if kind == "platform":
                return ("VALUE", type_name, (0, 1), 1, ())
            t = named[type_name]
            if kind == "enum":
                return ("VALUE", t.base, (0, 1), 1, tuple((t.literal_macro(n), v) for n, v in t.literals))
            if kind == "fixed":
                return ("VALUE", t.base, *rational(t.lsb, t.offset), ())
            return ("STRUCTURE", t.name, tuple((e.name, signature(e.type, e.kind, e.dimension)) for e in t.elements))

        def renamed(value):
            return json.loads(json.dumps(value).replace("TypeYamlSwc", "TypeSwc"))

        for port in swc.ports:
            with self.subTest(port=port.name):
                arxml = comp.ports[f"{port.name}.{port.name}"]
                self.assertEqual(arxml.direction, port.direction)
                self.assertEqual(renamed(signature(port.type, port.type_kind, port.dimension)),
                                 renamed(arxml.type.signature()))
        self.assertEqual(sorted(p.name for p in swc.ports), sorted(p.name for p in comp.ports.values()))

    def test_types_file_text(self):
        swc, _ = self.check(WITH_TYPES)
        text = swc_yaml.types_m(swc)
        self.assertTrue(text.startswith("function TySwc_types()\n"))
        self.assertIn("defineEnum('TySwc_Mode', {'Off', 'On', 'Fault'}, [0 1 7], 'uint8', 'Off', '');", text)
        self.assertIn("'HeaderFile', 'Rte_Type.h', 'AddClassNameToEnumNames', true);", text)
        self.assertIn("t.DataTypeMode = 'Fixed-point: slope and bias scaling';\nt.Signedness = 'Unsigned';\n"
                      "t.WordLength = 16;\nt.Slope = 0.5;\nt.Bias = -10;\nt.IsAlias = true;\nt.DataScope = "
                      "'Imported';\nt.HeaderFile = 'Rte_Type.h';", text)
        self.assertIn("    element('Raw', 'uint8', 3, '', '')];", text)
        self.assertIn("b.DataScope = 'Imported';\nb.HeaderFile = 'Rte_Type.h';", text)
        # binary point where the lsb is a power of two without offset (as TypeSwc_types.m of RTE-04)
        for lsb, fraction in ((0.0625, 4), (1, 0), (4, -2), (0.1, None), (3, None)):
            self.assertEqual(swc_yaml._binary_point(lsb, 0), fraction, lsb)
        self.assertIsNone(swc_yaml._binary_point(0.5, 1))
        # quotes in texts are MATLAB-escaped
        data = self.edited()
        data["types"][2]["description"] = "it's a record"
        swc, _ = self.check(data)
        self.assertIn("b.Description = 'it''s a record';", swc_yaml.types_m(swc))

    def test_types_file_is_written_and_checked(self):
        folder = self.tmp / "TySwc"
        folder.mkdir()
        (folder / "swc.yaml").write_text(yaml.safe_dump(WITH_TYPES, sort_keys=False), encoding="utf-8")
        target = folder / "TySwc_types.m"
        _swc, messages = swc_yaml.load(folder / "swc.yaml")
        self.assertEqual(messages, [])                     # no model yet: nothing to say
        (folder / "TySwc.slx").write_bytes(b"model")
        _swc, messages = swc_yaml.load(folder / "swc.yaml")
        self.assertEqual([(m.severity, m.yaml_path) for m in messages], [("warning", "types")])
        self.assertIn("TySwc_types.m is missing next to the model", messages[0].text)
        self.assertEqual(swc_yaml.main(["types", str(folder), "--check"]), 1)
        self.assertEqual(swc_yaml.main(["types", str(folder)]), 0)
        self.assertTrue(target.is_file())
        self.assertEqual(swc_yaml.main(["types", str(folder), "--check"]), 0)
        _swc, messages = swc_yaml.load(folder / "swc.yaml")
        self.assertEqual(messages, [])
        data = self.edited()
        data["types"][0]["literals"].append("Extra")
        (folder / "swc.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        _swc, messages = swc_yaml.load(folder / "swc.yaml")
        self.assertEqual(len(messages), 1)
        self.assertIn("TySwc_types.m does not match types: of swc.yaml", messages[0].text)
        self.assertEqual(swc_yaml.main(["types", str(folder), "--check"]), 1)

    def test_declared_description_names_the_platform_type(self):
        """rtegen compares the port types of swc.yaml with the ARXML, which shows an enumeration or fixed-point type
        by its platform type: declared_description gives that, a record keeps its name."""
        folder = self.tmp / "TySwc"
        folder.mkdir()
        (folder / "swc.yaml").write_text(yaml.safe_dump(WITH_TYPES, sort_keys=False), encoding="utf-8")
        desc = swc_yaml.declared_description(folder)
        self.assertEqual([(p.name, p.type) for p in desc.ports],
                         [("ModeIn", "uint8"), ("Level", "uint16"), ("Out", "TySwc_Rec")])

    def test_unused_type_is_a_warning(self):
        data = self.edited(ports=WITH_TYPES["ports"][:2])
        swc, messages = swc_yaml.check(data)
        self.assertIsNotNone(swc)
        self.assertEqual([(m.severity, m.yaml_path, m.text) for m in messages],
                         [("warning", "types[2].name", "type 'TySwc_Rec' is used by no port")])
        # a type used inside a used record counts as used
        data = self.edited(ports=WITH_TYPES["ports"][2:])
        swc, messages = swc_yaml.check(data)
        self.assertEqual(messages, [])

    # ---- errors ----
    def test_c_component_refuses_types(self):
        swc, messages = self.check(WITH_TYPES, "c")
        self.assertIsNone(swc)
        self.assertEqual([m.yaml_path for m in messages if m.severity == "error"], ["types"])
        self.assertIn("available to Simulink components only", messages[0].text)

    def test_type_definition_errors(self):
        cases = [
            ({"name": "uint8", "kind": "enum", "literals": ["A"]}, "types[0].name", "platform or Simulink type"),
            ({"name": "T", "kind": "union"}, "types[0].kind", "must be one of enum, fixed, record"),
            ({"name": "T", "kind": "enum", "literals": ["A"], "lsb": 1}, "types[0].lsb", "unknown key 'lsb'"),
            ({"name": "T", "kind": "enum", "base": "uint32", "literals": ["A"]}, "types[0].base", "must be one of"),
            ({"name": "T", "kind": "enum", "literals": []}, "types[0].literals", "must list the literals"),
            ({"name": "T", "kind": "enum", "literals": ["A", {"B": 0}]}, "types[0].literals[1]",
             "value 0 is already the value of A"),
            ({"name": "T", "kind": "enum", "literals": ["A", "A"]}, "types[0].literals[1]", "already defined"),
            ({"name": "T", "kind": "enum", "literals": [{"A": 256}]}, "types[0].literals[0]", "outside the range"),
            ({"name": "T", "kind": "enum", "base": "sint8", "literals": [{"A": -129}]}, "types[0].literals[0]",
             "outside the range of sint8"),
            ({"name": "T", "kind": "enum", "literals": [{"A": 1.5}]}, "types[0].literals[0]", "whole number"),
            ({"name": "T", "kind": "enum", "literals": ["int"]}, "types[0].literals[0]", "no identifier"),
            ({"name": "T", "kind": "enum", "literals": ["A"], "default": "B"}, "types[0].default", "no literal"),
            ({"name": "T", "kind": "fixed", "base": "float32", "lsb": 1}, "types[0].base", "integer platform type"),
            ({"name": "T", "kind": "fixed", "base": "uint8", "lsb": 0}, "types[0].lsb", "must be a number > 0"),
            ({"name": "T", "kind": "fixed", "base": "uint8"}, "types[0].lsb", "must be a number > 0"),
            ({"name": "T", "kind": "fixed", "base": "uint8", "lsb": 1, "offset": "x"}, "types[0].offset",
             "must be a number"),
            ({"name": "T", "kind": "record", "elements": []}, "types[0].elements", "needs elements"),
            ({"name": "T", "kind": "record", "elements": [{"name": "a", "type": "int32"}]},
             "types[0].elements[0].type", "no platform type"),
            ({"name": "T", "kind": "record", "elements": [{"name": "a", "type": "uint8", "dimension": "n"}]},
             "types[0].elements[0].dimension", "no symbolic dimension"),
            ({"name": "T", "kind": "record", "elements": [{"name": "a", "type": "uint8"},
                                                          {"name": "a", "type": "uint8"}]},
             "types[0].elements[1].name", "already defined"),
        ]
        for entry, path, fragment in cases:
            with self.subTest(entry=entry):
                data = copy.deepcopy(VALID)
                data["types"] = [entry]
                self.expect_error(data, path, fragment)

    def test_record_structure_errors(self):
        data = self.edited()
        data["types"].append({"name": "TySwc_Outer", "kind": "record",
                              "elements": [{"name": "Recs", "type": "TySwc_Rec", "dimension": 2}]})
        self.expect_error(data, "types[3].elements[0].dimension", "an array of records is not supported")
        data = self.edited()
        data["types"] += [{"name": "TySwc_A", "kind": "record", "elements": [{"name": "b", "type": "TySwc_B"}]},
                          {"name": "TySwc_B", "kind": "record", "elements": [{"name": "a", "type": "TySwc_A"}]}]
        self.expect_error(data, "types[3].elements", "record TySwc_A contains itself (TySwc_A -> TySwc_B -> TySwc_A)")
        data = self.edited()
        data["types"].append(copy.deepcopy(data["types"][0]))
        self.expect_error(data, "types[3].name", "type 'TySwc_Mode' is already defined at types[0].name")

    def test_broken_type_is_reported_once(self):
        """A port (or record) using a type with an error of its own gets no second message."""
        data = self.edited()
        data["types"][0]["literals"] = ["A", "A"]
        swc, messages = swc_yaml.check(data)
        self.assertIsNone(swc)
        self.assertEqual([m.yaml_path for m in messages if m.severity == "error"], ["types[0].literals[1]"])

    def test_port_rules_of_named_types(self):
        data = self.edited()
        data["ports"][0]["dimension"] = 2
        self.expect_error(data, "ports[0].dimension", "must have dimension 1")
        data = self.edited()
        data["ports"][2]["min"] = 0
        self.expect_error(data, "ports[2].min", "engineering limits need a number type")
        data = self.edited()
        data["ports"][1]["max"] = 40000            # uint16 * 0.5 - 10: at most 32757.5
        self.expect_error(data, "ports[1].max", "outside the range of uint16 with lsb 0.5")
        data = self.edited()
        data["ports"][1].update(min=-10, max=32757.5)
        swc, messages = swc_yaml.check(data)
        self.assertEqual(messages, [])
        data = self.edited()
        data["ports"][0]["type"] = "TySwc_Nothing"
        self.expect_error(data, "ports[0].type", "no supported platform type (boolean, uint8")

    def test_literal_macros_are_generated_identifiers(self):
        """TOOL-134: <Type>_<Literal> is a macro of Rte_Type.h - distinct from the others in 63 characters."""
        data = self.edited()
        long = "L" * 60
        data["types"][0]["literals"] = [f"{long}A", f"{long}B"]
        data["types"][0]["name"] = "TySwc_M"
        for port in data["ports"]:
            if port["type"] == "TySwc_Mode":
                port["type"] = "TySwc_M"
        data["types"][2]["elements"][0]["type"] = "TySwc_M"
        self.expect_error(data, "types[0].literals[1]", "is not distinct from")


if __name__ == "__main__":
    unittest.main()
