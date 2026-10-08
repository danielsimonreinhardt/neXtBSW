# SPDX-License-Identifier: Apache-2.0
"""SYS-90 (calibration K6): generated plausibility checks of the safety-relevant calibration parameters.

Test profile tools/rtegen/test/calib (C component CalMapSwc on buildtest_s32k148_min, not the eval board): a shared
axis, a map with an own Y axis and a value of class asil_b next to QM parameters.

  ParameterCheckGeneratorTest  rtegen: one row of rte::PARAMETER_CHECKS per parameter with a safety class other than
                               qm (row, count, axis element counts, rules), the rules are those of check_stored_values
                               (value_rules), the axes of a checked curve/map are checked; RteParameterChecks.md with
                               limits and the values of the build (defaults or dataset); a profile without such a
                               parameter gets neither the table nor the report (generated files as before)
  PlausibilityHostTest         compiles the generated RTE with ecu/calibration Plausibility.cpp,
                               PlausibilityMonitor.cpp and CalibrationSegment.cpp and runs
                               ecu/calibration/test/host/PlausibilityHostTest.cpp (zig): limits, ascending axes,
                               overlay (segment candidate, XCP write before it takes effect), start-up reaction, log
                               lines, DEM report, console text; variants with a float32 asil_b parameter (NaN,
                               infinity) and with a broken check table (FORM); Gegenproben: an exclusive upper
                               limit, equal breakpoints accepted, the overlay ignored, NaN inside the limits and the
                               count not compared, each built into a copy of Plausibility.cpp, make it fail

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_plausibility.py
"""

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
import target_build  # noqa: E402  (test/c_swc: a derived project for an rte.yaml)

CALIB = HERE / "test" / "calib"
C_SWC = HERE / "test" / "c_swc"
BT148 = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
TASKS = REPO / "ecu" / "asyncCoreConfiguration" / "tasks.yaml"
HOST_TEST = REPO / "ecu" / "calibration" / "test" / "host" / "PlausibilityHostTest.cpp"
CAL_SRC = REPO / "ecu" / "calibration" / "src" / "calibration"


def copy_profile(tmp: Path) -> Path:
    """The calib profile in a scratch folder (without host test sources); returns its rte.yaml."""
    target = tmp / "calib"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(CALIB, target, ignore=shutil.ignore_patterns("*.cpp"))
    return target / "rte.yaml"


def with_float_gain(ecu_file: Path) -> None:
    """Variant FLOAT_GAIN: Gain (float32, RTE-03 parameter of the profile) becomes an asil_b value 0 .. 2."""
    swc = ecu_file.parent / "CalMapSwc" / "swc.yaml"
    text = swc.read_text(encoding="utf-8")
    old = "  - name: Gain                      # RTE-03 parameter without calibration keys: stays as it was\n" \
          "    type: float32\n"
    assert old in text
    swc.write_text(text.replace(old, "  - name: Gain\n    kind: value\n    type: float32\n    min: 0\n    max: 2\n"
                                     "    safety: asil_b\n"), encoding="utf-8")


class ParameterCheckGeneratorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_sys90_gen_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generate(self, ecu_file: Path, name: str = "out"):
        out = self.tmp / name
        components = rtegen.generate(ecu_file, BT148, out, REPO, TASKS)[0]
        return components, {p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
                            for p in out.rglob("*") if p.is_file()}

    def test_checks_of_the_profile(self):
        components, files = self.generate(CALIB / "rte.yaml")
        checks = rtegen.parameter_checks(components)
        self.assertEqual([(c.row, c.parameter.element, c.parameter.dim, c.shape, c.rules) for c in checks], [
            (0, "SpeedAxis_rpm", 5, (0, 0), ["form", "limits", "ascending"]),
            (1, "TorqueLimit_Nm_Y", 3, (0, 0), ["form", "limits", "ascending"]),
            (2, "TorqueLimit_Nm", 15, (5, 3), ["form", "limits"]),
            (6, "MaxTorque_Nm", 1, (0, 0), ["form", "limits"]),
        ])
        params = [p for c in components.values() for p in c.parameters.values()]
        # every parameter with a safety class other than qm, nothing else
        self.assertEqual({id(c.parameter) for c in checks}, {id(p) for p in params if p.safety != "qm"})
        # the axes of a checked curve/map are checked themselves (swc.yaml: an axis has at least the class of its users)
        checked = {id(c.parameter) for c in checks}
        for c in checks:
            self.assertTrue(all(id(a) in checked for a in c.parameter.axes), c.parameter.name)
        # the run-time rules are the value rules of the build and of tools/calgen
        for c in checks:
            self.assertEqual(c.rules, ["form"] + rtegen.value_rules(c.parameter))
        header, config = files["RteConfig.h"], files["RteConfig.cpp"]
        self.assertIn("#define NX_RTE_HAS_PARAMETER_CHECKS 1", header)
        self.assertIn("constexpr size_t PARAMETER_CHECK_COUNT = 4U;", header)
        self.assertIn("constexpr uint8_t CHECK_ASCENDING = 0x04U;", header)
        self.assertIn("    { 2U, 15U, { 5U, 3U }, CHECK_FORM | CHECK_LIMITS }, // CalMapSwc.CalPrm.TorqueLimit_Nm (asil_b)",
                      config)
        self.assertIn("    { 0U, 5U, { 0U, 0U }, CHECK_FORM | CHECK_LIMITS | CHECK_ASCENDING }, "
                      "// CalMapSwc.CalPrm.SpeedAxis_rpm (asil_b)", config)
        # the rows named by the table are the rows of rte::PARAMETERS
        rows = re.findall(r'^    \{"([^"]+)", "[^"]*", ', config[config.index("Parameter const PARAMETERS[]"):], re.M)
        for c in checks:
            self.assertEqual(rows[c.row], c.parameter.name)

    def test_report(self):
        _components, files = self.generate(CALIB / "rte.yaml")
        report = files[rtegen.PLAUSIBILITY_FILE]
        self.assertIn("Values of this build: defaults of <Swc>_data.m", report)
        self.assertIn("| 2 | `CalMapSwc.CalPrm.TorqueLimit_Nm` | asil_b | map | sint16 | 5 x 3 | Nm | 0 .. 250 | "
                      "0 .. 2500 | form, limits | 120 110 90 150 140 120 200 180 150 180 170 140 150 140 120 |", report)
        self.assertIn("| 1 | `CalMapSwc.CalPrm.TorqueLimit_Nm_Y` | asil_b | own axis | sint8 | 3 | degC | -40 .. 125 | "
                      "-40 .. 125 | form, limits, ascending | -20 25 90 |", report)
        self.assertNotIn("BoostCurve", report)       # QM
        self.assertEqual(report.count("\n| "), 5)    # header + four checks

    def test_report_names_the_dataset(self):
        """With a calibration dataset (TOOL-145) the report carries its values and names it."""
        project = target_build.project_file(self.tmp / "p", CALIB / "rte.yaml")
        # asil_b parameters need their own calibration segment with on_invalid refuse (nxconfig, SYS-96); the same
        # section as the calib-dev target build (ecu/calibration/test/target_build.py)
        data = yaml.safe_load(project.read_text(encoding="utf-8"))
        data.setdefault("memory", {})["calibration"] = {"region": "PFLASH", "size": 0x1000, "on_invalid": "refuse",
                                                       "profile": "development"}
        project.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        shutil.copytree(CALIB / "calibration", project.parent / "calibration")
        config = rtegen.nxconfig.load(project)
        out = self.tmp / "ds"
        rtegen.generate(Path(config.rte_file), config, out, REPO, TASKS)
        report = (out / rtegen.PLAUSIBILITY_FILE).read_text(encoding="utf-8")
        self.assertRegex(report, r"Values of this build: dataset \S+ \(values [0-9a-f]{8}\)")

    def test_float_parameter_and_new_check(self):
        ecu_file = copy_profile(self.tmp)
        with_float_gain(ecu_file)
        components, files = self.generate(ecu_file)
        checks = rtegen.parameter_checks(components)
        self.assertEqual((checks[-1].parameter.element, checks[-1].rules), ("Gain", ["form", "limits"]))
        self.assertIn("constexpr size_t PARAMETER_CHECK_COUNT = 5U;", files["RteConfig.h"])

    def test_no_safety_parameter_no_table_no_report(self):
        """A profile without a safety-relevant parameter keeps its generated files (rtegen-reference: byte equal)."""
        for ecu_file in (C_SWC / "rte.yaml", REPO / "projects" / "buildtest_s32k148_min" / "rte.yaml"):
            with self.subTest(ecu_file=ecu_file.parent.name):
                _components, files = self.generate(ecu_file, ecu_file.parent.name)
                self.assertNotIn(rtegen.PLAUSIBILITY_FILE, files)
                self.assertNotIn("PARAMETER_CHECK", files["RteConfig.h"] + files["RteConfig.cpp"])

    def test_generator_refuses_what_the_ecu_would_refuse(self):
        """The same rule at both ends: a default outside the limits stops the build (check_stored_values), so an
        image never carries a value its own start-up check would find implausible."""
        ecu_file = copy_profile(self.tmp)
        data = ecu_file.parent / "CalMapSwc" / "CalMapSwc_data.m"
        data.write_text(data.read_text(encoding="utf-8").replace("SpeedAxis_rpm = [0 1000 2000 4000 6000];",
                                                                 "SpeedAxis_rpm = [0 1000 1000 4000 6000];"),
                        encoding="utf-8")
        with self.assertRaises(rtegen.RteError) as ctx:
            self.generate(ecu_file)
        self.assertIn("SpeedAxis_rpm are not strictly ascending", str(ctx.exception))

    def test_dem_event_is_a_bsw_name(self):
        self.assertIn("calibration.plausibility", rtegen.DEM_BSW_REPORTERS)


def zig(*args: str) -> list[str]:
    return [sys.executable, "-m", "ziglang", *args]


class PlausibilityHostTest(unittest.TestCase):
    """Generated RTE + ecu/calibration production sources + PlausibilityHostTest.cpp, compiled with zig."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_sys90_host_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_host_test(self, ecu_file: Path, defines: list[str], mutate=None, check_source: Path | None = None,
                      expect_pass: bool = True) -> str:
        out = self.tmp / "gen"
        shutil.rmtree(out, ignore_errors=True)
        rtegen.generate(ecu_file, BT148, out, REPO, TASKS)
        if mutate is not None:
            mutate(out)
        includes = [f"-I{REPO / 'ecu/calibration/test/host/stubs'}", f"-I{out}", f"-I{out / 'tasks'}",
                    f"-I{REPO / 'libs/bsw/autosarBase/include'}", f"-I{HERE / 'test' / 'can_host' / 'stubs'}",
                    f"-I{REPO / 'ecu/calibration/include'}", f"-I{REPO / 'libs/bsw/dem/include'}"]
        base = ["-O1", "-Wall", "-Wextra", "-Werror"]
        objects = []
        for src in (out / "Rte.c", ecu_file.parent / "CalMapSwc" / "src" / "CalMapSwc.c"):
            obj = self.tmp / (src.stem + ".o")
            r = subprocess.run(zig("cc", "-std=c99", *base, *includes, "-c", str(src), "-o", str(obj)),
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            objects.append(str(obj))
        # the production sources as strict as their target build and more (-Wconversion, -Wsign-conversion)
        strict = ["-Wconversion", "-Wsign-conversion", "-pedantic"]
        cpp = ["c++", "-std=c++17", *base, "-DNX_RTE_HAS_DEM=1", *defines, *includes]
        check_source = check_source or CAL_SRC / "Plausibility.cpp"
        for src, extra in ((check_source, strict), (CAL_SRC / "PlausibilityMonitor.cpp", strict),
                           (CAL_SRC / "CalibrationSegment.cpp", strict), (out / "RteConfig.cpp", []),
                           (HOST_TEST, [])):
            obj = self.tmp / (src.stem + "_cpp.o")
            r = subprocess.run(zig(*cpp, *extra, "-c", str(src), "-o", str(obj)), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{src.name}: {r.stderr[-3000:]}")
            objects.append(str(obj))
        exe = self.tmp / "plausibility_host_test.exe"
        r = subprocess.run(zig("c++", *objects, "-o", str(exe)), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        if not expect_pass:
            self.assertNotEqual(r.returncode, 0, "the deliberate fault was not found")
            self.assertNotIn(", 0 failures", r.stdout)
            return r.stdout
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertIn(", 0 failures", r.stdout)
        return r.stdout

    def test_profile(self):
        stdout = self.run_host_test(CALIB / "rte.yaml", [])
        self.assertGreater(int(re.search(r"(\d+) checks", stdout).group(1)), 60)

    def test_float_parameter(self):
        ecu_file = copy_profile(self.tmp)
        with_float_gain(ecu_file)
        self.run_host_test(ecu_file, ["-DFLOAT_GAIN=1"])

    def test_broken_check_table_is_form(self):
        def mutate(out: Path):
            config = out / "RteConfig.cpp"
            text = config.read_text(encoding="utf-8")
            for old, new in (("{ 2U, 15U, { 5U, 3U }", "{ 2U, 15U, { 5U, 4U }"), ("{ 6U, 1U, {", "{ 6U, 2U, {")):
                self.assertIn(old, text)
                text = text.replace(old, new)
            config.write_text(text, encoding="utf-8")
        self.run_host_test(CALIB / "rte.yaml", ["-DFORM_BROKEN=1"], mutate)

    def test_deliberate_faults_are_found(self):
        """Gegenproben: each fault built into a copy of Plausibility.cpp must make a variant of the host test fail."""
        source = (CAL_SRC / "Plausibility.cpp").read_text(encoding="utf-8")
        float_profile = copy_profile(self.tmp / "float")
        with_float_gain(float_profile)
        faults = [
            ("upper limit exclusive", "(lo.integer <= v.integer) && (v.integer <= hi.integer)",
             "(lo.integer <= v.integer) && (v.integer < hi.integer)", CALIB / "rte.yaml", []),
            ("equal breakpoints accepted", "return v.integer <= previous.integer;",
             "return v.integer < previous.integer;", CALIB / "rte.yaml", []),
            ("overlay ignored (XCP write and segment judged by the old values)",
             "bool const replaced  = (overlay.length != 0U)", "bool const replaced  = false && (overlay.length != 0U)",
             CALIB / "rte.yaml", []),
            ("NaN passes the limits", "return (lo.real <= v.real) && (v.real <= hi.real);",
             "return !(v.real < lo.real) && !(v.real > hi.real);", float_profile, ["-DFLOAT_GAIN=1"]),
        ]
        for what, old, new, ecu_file, defines in faults:
            with self.subTest(fault=what):
                self.assertIn(old, source)
                broken = self.tmp / "mutant" / "Plausibility.cpp"
                broken.parent.mkdir(parents=True, exist_ok=True)
                broken.write_text(source.replace(old, new), encoding="utf-8")
                self.run_host_test(ecu_file, defines, check_source=broken, expect_pass=False)
        # form: the count of the value no longer compared - the broken table of the FORM variant is not found
        old = "if ((p.count != c.count) || (p.count == 0U)"
        self.assertIn(old, source)
        broken = self.tmp / "mutant" / "Plausibility.cpp"
        broken.write_text(source.replace(old, "if ((p.count == 0U)"), encoding="utf-8")

        def mutate(out: Path):
            config = out / "RteConfig.cpp"
            config.write_text(config.read_text(encoding="utf-8").replace("{ 2U, 15U, { 5U, 3U }", "{ 2U, 15U, { 5U, 4U }")
                              .replace("{ 6U, 1U, {", "{ 6U, 2U, {"), encoding="utf-8")
        self.run_host_test(CALIB / "rte.yaml", ["-DFORM_BROKEN=1"], mutate, check_source=broken, expect_pass=False)


if __name__ == "__main__":
    unittest.main()
