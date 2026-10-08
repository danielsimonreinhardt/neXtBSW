# SPDX-License-Identifier: Apache-2.0
"""Host tests of the calibration dataset nextbsw-cal 1 and tools/calgen (TOOL-145, stage K2).

Profile: tools/rtegen/test/calib (RTE-26, C component CalMapSwc: shared axis, map with a shared and an own axis,
curves, value table, limits, safety class) with its dataset calibration/ (base + variant sport) - generic, no board.

  FormatTest     reading, writing, format errors with file and line, variant as delta, resolve, diff
  CheckTest      the checks of the build (complete, grid, limits, ascending axes, shape, value table), layout
                 identifier (software and a build), status rules of a release, delta equal to base
  MigrateTest    dataset from the <Swc>_data.m defaults, adding only missing values, rebind
  ExchangeTest   DCM and CDFX round trip, status/author/reason through CDFX, import into a variant (delta only),
                 refused imports
  BuildTest      rtegen with and without the dataset: without it every file stays byte for byte; with it the
                 values of base or of build_variant, RteCalibration.json names the dataset, rte_components.cmake
                 lists its files; an invalid dataset is refused before anything is written; values JSON for MATLAB

Run: .venv/Scripts/python -m unittest tools/calgen/test_calgen.py
"""

import difflib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "rtegen" / "test" / "c_swc"))

import caldata  # noqa: E402
import calgen  # noqa: E402
import exchange  # noqa: E402
import rtegen  # noqa: E402
import target_build  # noqa: E402  (a project around an ECU description, as the RTE-20 tests use it)

CALIB = REPO / "tools" / "rtegen" / "test" / "calib"
TASKS = REPO / "ecu" / "asyncCoreConfiguration" / "tasks.yaml"


class ProfileCase(unittest.TestCase):
    """A copy of the profile (component, rte.yaml, dataset) in a scratch folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_calgen_"))
        shutil.copytree(CALIB, self.tmp / "calib", ignore=shutil.ignore_patterns("*.cpp"))
        self.rte = self.tmp / "calib" / "rte.yaml"
        self.root = self.tmp / "calib" / "calibration"
        self.file = self.root / "base" / "CalMapSwc.cal.yaml"
        self.sport = self.root / "variants" / "sport" / "CalMapSwc.cal.yaml"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def edit(self, file: Path, old: str, new: str):
        text = file.read_text(encoding="utf-8")
        self.assertIn(old, text)
        file.write_text(text.replace(old, new, 1), encoding="utf-8")

    def software(self) -> calgen.Software:
        return calgen.open_target(self.rte)

    def errors(self, name=caldata.BASE, **kwargs) -> list[str]:
        sw = self.software()
        return [f.text for f in calgen.check(sw, caldata.load(self.root), name, **kwargs) if f.severity == "error"]


class FormatTest(ProfileCase):
    def test_example_reads_and_resolves(self):
        ds = caldata.load(CALIB / "calibration")
        self.assertEqual((ds.layout, ds.build_variant, list(ds.variants)), (ds.layout, "", ["sport"]))
        self.assertRegex(ds.layout, r"^[0-9a-f]{8}$")
        self.assertEqual(ds.base.count(), 10)
        self.assertEqual(ds.variants["sport"].count(), 4)
        sport = ds.resolve("sport")
        self.assertEqual(sport.count(), 10)
        self.assertEqual((sport.entry("CalMapSwc", "MaxTorque_Nm").value, sport.entry("CalMapSwc", "MaxTorque_Nm").origin),
                         (280, "sport"))
        self.assertEqual(sport.entry("CalMapSwc", "SpeedAxis_rpm").origin, "base")
        entry = ds.base.entry("CalMapSwc", "TorqueLimit_Nm")
        self.assertEqual((entry.value[0], len(entry.value), entry.status, entry.line), ([120, 110, 90], 5, "released", 15))
        self.assertEqual(ds.files_of("sport")[-1].name, "CalMapSwc.cal.yaml")
        with self.assertRaises(caldata.CalDataError) as ctx:
            ds.resolve("eco")
        self.assertIn("variant eco does not exist (known: base, sport)", str(ctx.exception))

    def test_writer_reproduces_the_example(self):
        """render_component writes the files of the example byte for byte (row comments included)."""
        sw = self.software()
        ds = caldata.load(CALIB / "calibration")
        labels = calgen.row_labels(sw, ds.resolve(caldata.BASE))
        text = caldata.render_component("CalMapSwc", ds.base.components["CalMapSwc"], caldata.BASE, labels["CalMapSwc"])
        self.assertEqual(text, (CALIB / "calibration" / "base" / "CalMapSwc.cal.yaml").read_text(encoding="utf-8"))
        text = caldata.render_component("CalMapSwc", ds.variants["sport"].components["CalMapSwc"], "sport",
                                        calgen.row_labels(sw, ds.resolve("sport"))["CalMapSwc"])
        self.assertEqual(text, self.sport.read_text(encoding="utf-8"))
        self.assertEqual(caldata.render_index(ds.layout), (CALIB / "calibration" / "dataset.yaml").read_text(encoding="utf-8"))
        # a layout of digits only (or with an e) stays text
        self.assertEqual(caldata.render_index("12e45678").splitlines()[-1], 'layout: "12e45678"')

    def test_format_errors_name_file_and_line(self):
        self.edit(self.file, "    status: checked\n    author: \"neXtBSW example\"\n    reason: \"default of the component type (CalMapSwc_data.m)\"\n  DerateCurve_X:",
                  "    status: approved\n  DerateCurve_X:")
        self.edit(self.file, "  Gain:\n    value: 0.8", "  Gain:\n    value: 0.8\n    note: x")
        with self.assertRaises(caldata.CalDataError) as ctx:
            caldata.load(self.root)
        found = [(f.file.name, f.line, f.text) for f in ctx.exception.findings if f.severity == "error"]
        self.assertEqual(found, [
            ("CalMapSwc.cal.yaml", 25, "BoostCurve: status must be one of draft, checked, released, not 'approved'"),
            ("CalMapSwc.cal.yaml", 53, "Gain: unknown key(s) note (value, status, author, reason)")])

    def test_variant_only_changes_base_values(self):
        self.edit(self.sport, "  MaxTorque_Nm:", "  Ghost:\n    value: 1\n  MaxTorque_Nm:")
        (self.root / "dataset.yaml").write_text((self.root / "dataset.yaml").read_text(encoding="utf-8")
                                                + "build_variant: eco\n", encoding="utf-8")
        with self.assertRaises(caldata.CalDataError) as ctx:
            caldata.load(self.root)
        texts = [f.text for f in ctx.exception.findings]
        self.assertIn("CalMapSwc.Ghost: a variant only changes values of base, and base has none", texts)
        self.assertIn("build_variant eco is no variant (known: sport)", texts)

    def test_missing_index_and_wrong_component(self):
        (self.root / "dataset.yaml").unlink()
        with self.assertRaisesRegex(caldata.CalDataError, "no dataset.yaml"):
            caldata.load(self.root)
        caldata.write_index(self.root, "a5f879e0")
        self.edit(self.file, "component: CalMapSwc", "component: Other")
        with self.assertRaisesRegex(caldata.CalDataError, r"component must be CalMapSwc \(the file name\)"):
            caldata.load(self.root)

    def test_diff(self):
        ds = caldata.load(CALIB / "calibration")
        changes = caldata.diff(ds.resolve(caldata.BASE), ds.resolve("sport"))
        shown = [str(c) for c in changes]
        self.assertIn("~ CalMapSwc.MaxTorque_Nm: value: 250 -> 280", shown)
        self.assertIn("~ CalMapSwc.MaxTorque_Nm status: released -> checked", shown)
        self.assertIn("~ CalMapSwc.BoostCurve: [2]: 400 -> 500, [3]: 900 -> 1100, [4]: 1500 -> 1800, [5]: 1800 -> 2100",
                      shown)
        self.assertIn("~ CalMapSwc.Mode: value: Eco -> Sport", shown)
        cells = next(c for c in changes if c.element == "TorqueLimit_Nm" and c.what == "value").cells
        self.assertEqual(cells[0], ("[1][1]", 120, 130))
        self.assertEqual(len(cells), 15)
        self.assertEqual(caldata.diff(ds.resolve("sport"), ds.resolve("sport")), [])
        # 250 and 250.0 are the same value
        a = caldata.Part("a", {"C": {"x": caldata.Entry(250)}})
        b = caldata.Part("b", {"C": {"x": caldata.Entry(250.0)}})
        self.assertEqual(caldata.diff(a, b), [])
        self.assertEqual([str(c) for c in calgen.diff_specs(str(CALIB / "rte.yaml"), f"{CALIB / 'calibration'}@sport")],
                         shown)


class CheckTest(ProfileCase):
    def test_example_passes(self):
        sw = self.software()
        ds = caldata.load(CALIB / "calibration")
        self.assertEqual(ds.layout, sw.layout)
        for name in ds.names():
            self.assertEqual(calgen.check(sw, ds, name), [], name)

    def test_complete_and_known(self):
        self.edit(self.file, "  Offsets_Nm:\n    value: [-5, 0, 5]", "  Offsets:\n    value: [-5, 0, 5]")
        self.assertEqual(self.errors(), [
            "CalMapSwc.CalPrm.Offsets_Nm: no value in the calibration dataset (base)",
            f"{self.file.as_posix()}:50: CalMapSwc.Offsets is no calibration parameter of CalMapSwc"])

    def test_values_are_judged_as_the_build_does(self):
        cases = [
            ("    value: 250\n", "    value: 301\n",
             "CalMapSwc.CalPrm.MaxTorque_Nm: 301 is stored as 3010, outside the stored limits 0 .. 3000"),
            ("value: [-40, 60, 100, 150]", "value: [-40, 60, 100, 100.04]",
             "CalMapSwc.CalPrm.DerateCurve_X: breakpoints are not strictly ascending as stored values (1000 at 3, 1000 at 4"),
            ("      - [120, 110, 90]  ", "      - [120, 110]  ",
             "a map of 5 x 3 values is written as 5 rows of 3 values (one row per X breakpoint)"),
            ('value: "Eco"', 'value: "Turbo"', "'Turbo' is not in the value table (0: Off, 1: Eco, 2: Sport)"),
            ("value: [-5, 0, 5]", "value: [-5, 0]", "3 values expected as one list, the dataset has 2 values"),
            ("value: [0, 400, 900, 1500, 1800]", "value: [0, 400, 900, 1500, 1800.5]",
             "1800.5 is no whole number, but BoostCurve is uint16 without scaling"),
            ("    value: 0.8\n", "    value: [0.8]\n", "a scalar is expected, not a list"),
        ]
        original = self.file.read_text(encoding="utf-8")
        for old, new, expected in cases:
            with self.subTest(expected):
                self.file.write_text(original.replace(old, new, 1), encoding="utf-8")
                errors = self.errors()
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(expected, errors[0])
                self.assertIn("CalMapSwc.cal.yaml:", errors[0])

    def test_value_table_text_or_number(self):
        self.edit(self.file, 'value: "Eco"', "value: 2")
        self.assertEqual(self.errors(), [])

    def test_layout_of_software_and_build(self):
        self.edit(self.root / "dataset.yaml", caldata.load(self.root).layout, "0badf00d")
        errors = self.errors()
        self.assertEqual(len(errors), 1)
        self.assertRegex(errors[0], r"^the dataset is bound to layout 0badf00d, this software has layout [0-9a-f]{8}")
        build = self.tmp / "RteCalibration.json"
        build.write_text(json.dumps({"layout": "12345678"}), encoding="utf-8")
        errors = self.errors(build=build)
        self.assertIn("the build has layout 12345678, the dataset is bound to 0badf00d", errors)

    def test_release_status(self):
        self.assertEqual(self.errors(release=True), [])
        errors = self.errors("sport", release=True)
        self.assertEqual(errors, [
            "CalMapSwc.CalPrm.TorqueLimit_Nm: safety class asil_b, status checked; a release needs status released",
            "CalMapSwc.CalPrm.MaxTorque_Nm: safety class asil_b, status checked; a release needs status released"])
        warnings = [f.text for f in calgen.check(self.software(), caldata.load(self.root), release=True)
                    if f.severity == "warning"]
        self.assertIn("CalMapSwc.CalPrm.Gain: status draft in a release", warnings)

    def test_delta_equal_to_base_is_a_warning(self):
        self.edit(self.sport, "    value: 280\n", "    value: 250\n")
        findings = calgen.check(self.software(), caldata.load(self.root), "sport")
        self.assertEqual([(f.severity, f.text) for f in findings],
                         [("warning", "CalMapSwc.MaxTorque_Nm: same value as base - not a delta, remove it from the "
                                      "variant")])

    def test_cli_exit_codes(self):
        self.assertEqual(calgen.main(["check", str(self.rte), "--all"]), 0)
        self.assertEqual(calgen.main(["check", str(self.rte), "--all", "--release"]), 1)
        self.edit(self.file, "    value: 250\n", "    value: 400\n")
        self.assertEqual(calgen.main(["check", str(self.rte)]), 1)


class MigrateTest(ProfileCase):
    def test_migration_takes_the_defaults(self):
        shutil.rmtree(self.root)
        sw = self.software()
        report = calgen.migrate(sw, author="A. Person")
        self.assertEqual(report[0], f"created 10 value(s) in {self.root.as_posix()}/base")
        ds = caldata.load(self.root)
        self.assertEqual(ds.layout, sw.layout)
        base = ds.base.components["CalMapSwc"]
        self.assertEqual(list(base), [p.element for p in sw.components["CalMapSwc"].parameters.values()])
        self.assertEqual((base["Gain"].value, base["Mode"].value, base["MaxTorque_Nm"].value), (0.75, "Eco", 250))
        self.assertEqual(base["TorqueLimit_Nm"].value[2], [200, 180, 150])
        self.assertEqual((base["Gain"].status, base["Gain"].author, base["Gain"].reason),
                         ("draft", "A. Person", "default of the component type (CalMapSwc_data.m)"))
        self.assertEqual(calgen.check(sw, ds), [])
        # the migrated values are exactly the defaults: every stored value as from <Swc>_data.m
        comps = sw.fresh()
        self.assertEqual(rtegen.apply_calibration_dataset(comps, ds.resolve()), [])
        for p in comps["CalMapSwc"].parameters.values():
            self.assertEqual(p.raw, sw.components["CalMapSwc"].parameters[p.key].raw, p.name)

    def test_migration_adds_only_missing_values(self):
        self.edit(self.file, "  Gain:\n    value: 0.8\n    status: draft\n    author: \"neXtBSW example\"\n"
                             "    reason: \"first bench run, differs from the default 0.75 on purpose\"\n", "")
        report = calgen.migrate(self.software())
        self.assertEqual(report, [f"added 1 value(s) in {self.root.as_posix()}/base", "  + CalMapSwc.CalPrm.Gain"])
        base = caldata.load(self.root).base.components["CalMapSwc"]
        self.assertEqual((base["Gain"].value, base["MaxTorque_Nm"].value, base["MaxTorque_Nm"].status),
                         (0.75, 250, "released"))

    def test_rebind(self):
        swc = self.tmp / "calib" / "CalMapSwc" / "swc.yaml"
        self.edit(swc, "dim: 3, type: sint8", "dim: 3, type: sint16")
        sw = self.software()
        self.assertNotEqual(caldata.load(self.root).layout, sw.layout)
        self.assertEqual(len(self.errors()), 1)
        self.assertIn(f"-> {sw.layout}", calgen.rebind(sw))
        self.assertEqual(caldata.load(self.root).layout, sw.layout)
        self.assertEqual(self.errors(), [])
        # values that do not fit are not rebound
        self.edit(self.sport, "    value: 280\n", "    value: 900\n")
        caldata.write_index(self.root, "0badf00d")
        with self.assertRaises(calgen.CalgenError) as ctx:
            calgen.rebind(self.software())
        self.assertIn("MaxTorque_Nm: 900", ctx.exception.findings[0].text)
        self.assertEqual(caldata.load(self.root).layout, "0badf00d")

    def test_resolve_to_a_complete_dataset(self):
        out = self.tmp / "resolved"
        calgen.resolve_to(self.software(), "sport", out)
        ds = caldata.load(out)
        self.assertEqual(ds.variants, {})
        self.assertEqual(caldata.diff(ds.resolve(), caldata.load(self.root).resolve("sport")), [])


class ExchangeTest(ProfileCase):
    def export(self, fmt: str, name: str = "sport") -> Path:
        out = self.tmp / f"{name}.{fmt}"
        calgen.export(self.software(), name, fmt, out, date="2026-09-28")
        return out

    def test_dcm_text(self):
        text = self.export("dcm").read_text(encoding="utf-8")
        self.assertIn("KONSERVIERUNG_FORMAT 2.0", text)
        self.assertIn("GRUPPENKENNFELD CalMapSwc.CalPrm.TorqueLimit_Nm 5 3\n*SSTX CalMapSwc.CalPrm.SpeedAxis_rpm\n", text)
        # per Y breakpoint the values over X
        self.assertIn("   ST/X 0   1000   2000   4000   6000\n   ST/Y -20\n   WERT 130   170   230   210   170\n   ST/Y 25\n",
                      text)
        self.assertIn("KENNLINIE CalMapSwc.CalPrm.DerateCurve 4\n", text)
        self.assertIn("   ST/X -40   60   100   150\n   WERT 100   100   80   50\nEND", text)
        self.assertIn("FESTWERT CalMapSwc.CalPrm.Mode\n   LANGNAME \"Operating mode\"\n   EINHEIT_W \"\"\n   TEXT \"Sport\"\nEND", text)
        self.assertIn("STUETZSTELLENVERTEILUNG CalMapSwc.CalPrm.SpeedAxis_rpm 5\n", text)
        self.assertIn("FESTWERTEBLOCK CalMapSwc.CalPrm.Offsets_Nm 3\n", text)
        self.assertNotIn("DerateCurve_X", text)                  # own breakpoints are part of their curve

    def round_trip(self, fmt: str):
        exported = self.export(fmt)
        before = caldata.load(self.root)
        # change some values of the variant and of base, then import the export back into the variant
        self.edit(self.sport, "    value: 280\n", "    value: 270\n")
        self.edit(self.sport, "      - [130, 120, 95]", "      - [131, 121, 96]")
        self.edit(self.file, "value: [-40, 60, 100, 150]", "value: [-40, 50, 100, 150]")
        changes, warnings = calgen.import_file(self.software(), exported, "sport", author="B. Person")
        self.assertEqual(warnings, [])
        self.assertEqual(sorted(c.split(":")[0] for c in changes),
                         ["CalMapSwc.CalPrm.DerateCurve_X", "CalMapSwc.CalPrm.MaxTorque_Nm",
                          "CalMapSwc.CalPrm.TorqueLimit_Nm"])
        after = caldata.load(self.root)
        values = lambda part: {(c, e): caldata.normalise(x.value) for c, es in part.components.items()  # noqa: E731
                               for e, x in es.items()}
        self.assertEqual(values(after.resolve("sport")), values(before.resolve("sport")))
        # the own breakpoints differ from base: they became part of the delta, base stays as edited
        self.assertEqual(after.variants["sport"].entry("CalMapSwc", "DerateCurve_X").value, [-40, 60, 100, 150])
        self.assertEqual(after.base.entry("CalMapSwc", "DerateCurve_X").value, [-40, 50, 100, 150])
        return after

    def test_dcm_round_trip(self):
        after = self.round_trip("dcm")
        entry = after.variants["sport"].entry("CalMapSwc", "MaxTorque_Nm")
        self.assertEqual((entry.status, entry.author, entry.reason), ("draft", "B. Person", "imported from sport.dcm"))

    def test_cdfx_round_trip_keeps_status_author_reason(self):
        after = self.round_trip("cdfx")
        entry = after.variants["sport"].entry("CalMapSwc", "MaxTorque_Nm")
        self.assertEqual((entry.status, entry.author, entry.reason), ("checked", "neXtBSW example", "sport variant"))

    def test_cdfx_into_an_empty_base(self):
        exported = self.export("cdfx", caldata.BASE)
        original = caldata.load(self.root).base
        for f in (self.root / "base").iterdir():
            f.unlink()
        shutil.rmtree(self.root / "variants")         # a delta needs its base values
        changes, _warnings = calgen.import_file(self.software(), exported)
        self.assertEqual(len(changes), 10)
        self.assertEqual(caldata.diff(original, caldata.load(self.root).base), [])

    def test_import_back_to_base_value_leaves_the_delta(self):
        exported = self.export("dcm", caldata.BASE)
        changes, _ = calgen.import_file(self.software(), exported, "sport")
        self.assertEqual(sorted(changes), sorted(f"CalMapSwc.CalPrm.{n}: back to the base value" for n in
                                                 ("TorqueLimit_Nm", "BoostCurve", "MaxTorque_Nm", "Mode")))
        self.assertFalse(self.sport.exists())                   # an empty delta has no file
        self.assertEqual(caldata.load(self.root).variants["sport"].count(), 0)

    def test_refused_imports_write_nothing(self):
        original = self.file.read_text(encoding="utf-8")
        dcm = self.export("dcm", caldata.BASE)
        text = dcm.read_text(encoding="utf-8")
        cases = [
            (text.replace("FESTWERT CalMapSwc.CalPrm.Gain", "FESTWERT CalMapSwc.CalPrm.Ghost"),
             "CalMapSwc.CalPrm.Ghost is no calibration object of calib"),
            (text.replace("   WERT 250\n", "   WERT 400\n"), "400 is stored as 4000, outside the stored limits"),
            (text.replace("FESTWERTEBLOCK CalMapSwc.CalPrm.Offsets_Nm 3\n", "KENNLINIE CalMapSwc.CalPrm.Offsets_Nm 3\n   ST/X 1 2 3\n"),
             "CalMapSwc.CalPrm.Offsets_Nm is a array in the software, the file has a curve"),
        ]
        for content, expected in cases:
            with self.subTest(expected):
                dcm.write_text(content, encoding="utf-8")
                with self.assertRaises(calgen.CalgenError) as ctx:
                    calgen.import_file(self.software(), dcm)
                self.assertIn(expected, "; ".join(f.text for f in ctx.exception.findings))
                self.assertEqual(self.file.read_text(encoding="utf-8"), original)

    def test_dcm_reader(self):
        objects, warnings = exchange.read_dcm(
            "* comment\nKONSERVIERUNG_FORMAT 2.0\nFUNKTIONEN\n  FKT F \"\" \"\"\nEND\n"
            "KENNFELD M 2 3\n  ST/X 1 2\n  ST/Y 10\n  WERT 1 2\n  ST/Y 20\n  WERT 3 4\n  ST/Y 30\n  WERT 5\n  WERT 6\nEND\n"
            "FESTWERT T\n  TEXT \"On\"\nEND\n", "x.dcm")
        self.assertEqual(warnings, ["x.dcm:3: block FUNKTIONEN skipped"])
        self.assertEqual((objects[0].values, objects[0].shape, objects[0].axes), ([1, 3, 5, 2, 4, 6], (2, 3),
                                                                                 [[1, 2], [10, 20, 30]]))
        self.assertEqual(objects[1].values, ["On"])
        for text, expected in [("FESTWERT A\nWERT 1\nEND\n", "x.dcm:1: KONSERVIERUNG_FORMAT 2.0 must come first"),
                               ("KONSERVIERUNG_FORMAT 2.0\nFESTWERT A\nWERT 1\n", "x.dcm:2: FESTWERT A has no END"),
                               ("KONSERVIERUNG_FORMAT 2.0\nKENNLINIE A 3\nST/X 1 2 3\nWERT 1 2\nEND\n",
                                "x.dcm:2: KENNLINIE A: 3 breakpoints, 2 values"),
                               ("KONSERVIERUNG_FORMAT 2.0\nFESTWERT A\nWERT x\nEND\n", "x.dcm:3: 'x' is no number"),
                               ("KONSERVIERUNG_FORMAT 2.0\nTEXTSTRING A\nEND\n", "x.dcm:2: 'TEXTSTRING' is not understood")]:
            with self.subTest(expected), self.assertRaises(exchange.ExchangeError) as ctx:
                exchange.read_dcm(text, "x.dcm")
            self.assertIn(expected, str(ctx.exception))

    def test_cdfx_reader_states(self):
        text = self.export("cdfx", caldata.BASE).read_text(encoding="utf-8")
        self.assertIn('<!DOCTYPE MSRSW PUBLIC "-//ASAM//DTD CALIBRATION DATA FORMAT:V2.0.0:LAI:IAI:XML:CDF200.XSD//EN"', text)
        objects, _ = exchange.read_cdfx(text)
        by_name = {o.name: o for o in objects}
        tl = by_name["CalMapSwc.CalPrm.TorqueLimit_Nm"]
        self.assertEqual((tl.kind, tl.shape, tl.values[:3], tl.axis_refs, tl.axes[1]),
                         ("map", (5, 3), [120, 110, 90], ["CalMapSwc.CalPrm.SpeedAxis_rpm", ""], [-20, 25, 90]))
        self.assertEqual((tl.status, tl.author), ("released", "neXtBSW example"))
        self.assertEqual(by_name["CalMapSwc.CalPrm.Mode"].values, ["Eco"])
        self.assertEqual(by_name["CalMapSwc.CalPrm.Gain"].status, "draft")
        objects, warnings = exchange.read_cdfx(text.replace("<STATE>completed</STATE>", "<STATE>frozen</STATE>", 1))
        self.assertIn("state frozen unknown, taken as draft", warnings[0])


class BuildTest(ProfileCase):
    def project(self, with_dataset: bool, build_variant: str = "") -> "rtegen.nxconfig.Config":
        project = target_build.project_file(self.tmp / "p", self.rte)
        folder = project.parent / "calibration"
        shutil.rmtree(folder, ignore_errors=True)
        if with_dataset:
            shutil.copytree(self.root, folder)
            if build_variant:
                caldata.write_index(folder, caldata.load(folder).layout, build_variant)
        return rtegen.nxconfig.load(project)

    def generate(self, config, out: str, **kwargs) -> dict:
        rtegen.generate(config.rte_file, config, self.tmp / out, REPO, TASKS, **kwargs)
        return {f.relative_to(self.tmp / out).as_posix(): f.read_text(encoding="utf-8")
                for f in sorted((self.tmp / out).rglob("*")) if f.is_file()}

    def test_without_dataset_nothing_changes(self):
        """No calibration folder: every generated file equals the one of a run that knows no dataset at all."""
        config = self.project(False)
        self.assertIsNone(rtegen.dataset_of(config, config.rte_file))
        self.assertEqual(self.generate(config, "a"), self.generate(config, "b", calibration=None))
        self.assertNotIn("dataset", json.loads(self.generate(config, "c")["RteCalibration.json"]))

    def test_dataset_of_defaults_gives_the_same_sources(self):
        """A dataset holding exactly the defaults: every C/C++ file byte-equal to the build without it."""
        shutil.rmtree(self.root)
        calgen.migrate(self.software())
        without = self.generate(self.project(False), "without")
        with_ = self.generate(self.project(True), "with")
        self.assertEqual(set(without), set(with_))
        differ = sorted(n for n in without if without[n] != with_[n])
        # SYS-90: the report of the plausibility checks names where the values come from (same values, other source)
        # PLAT-79: the identity manifest of rtegen names the dataset among its inputs
        self.assertEqual(differ, ["RteCalibration.json", "RteParameterChecks.md", "identity/rtegen.sha256",
                                  "rte_components.cmake"])
        def checked(report: str) -> list[str]:
            return [line for line in report.splitlines() if not line.startswith("- Values of this build:")]
        self.assertEqual(checked(with_["RteParameterChecks.md"]), checked(without["RteParameterChecks.md"]))
        self.assertIn("- Values of this build: dataset", with_["RteParameterChecks.md"])
        self.assertTrue(with_["rte_components.cmake"].startswith(without["rte_components.cmake"]))
        self.assertIn("set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS", with_["rte_components.cmake"])
        self.assertIn("/calibration/base/CalMapSwc.cal.yaml\"", with_["rte_components.cmake"])

    def test_values_of_base_and_build_variant(self):
        base = self.generate(self.project(True), "base")
        sport = self.generate(self.project(True, "sport"), "sport")
        self.assertIn("Rte_Prm_CalMapSwc_CalPrm_MaxTorque_Nm_data = 2500;", base["Rte.c"])
        self.assertIn("Rte_Prm_CalMapSwc_CalPrm_Gain_data = 0.8F;", base["Rte.c"])      # default 0.75
        self.assertIn("Rte_Prm_CalMapSwc_CalPrm_MaxTorque_Nm_data = 2800;", sport["Rte.c"])
        self.assertIn("Rte_Prm_CalMapSwc_CalPrm_Mode_data = 2U;", sport["Rte.c"])
        dataset = json.loads(sport["RteCalibration.json"])["dataset"]
        self.assertEqual(dataset["name"], "sport")
        self.assertIn("/variants/sport/CalMapSwc.cal.yaml", sport["rte_components.cmake"])
        # the identifier of the values is what calgen values reports (MATLAB, segment of K3)
        result = calgen.values_of(self.software(), "sport")
        self.assertEqual(result["values_id"], dataset["values"])
        self.assertEqual(result["components"]["CalMapSwc"]["TorqueLimit_Nm"]["shape"], [5, 3])
        self.assertEqual(result["components"]["CalMapSwc"]["MaxTorque_Nm"]["stored"], [2800])

    def test_invalid_dataset_is_refused_before_writing(self):
        config = self.project(True)
        target = Path(config.project_file).parent / "calibration" / "base" / "CalMapSwc.cal.yaml"
        self.edit(target, "    value: 250\n", "    value: 400\n")
        caldata.write_index(target.parents[1], "0badf00d")
        with self.assertRaises(rtegen.RteError) as ctx:
            self.generate(config, "out")
        text = str(ctx.exception)
        self.assertIn("the dataset is bound to layout 0badf00d, this software has layout", text)
        self.assertIn("CalMapSwc.CalPrm.MaxTorque_Nm: 400 is stored as 4000", text)
        self.assertFalse((self.tmp / "out").exists())
        # the Studio check (validate) reports the same error
        errors = [m for m in rtegen.validate(config.project_file, REPO, tasks_manifest=TASKS) if m.severity == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("MaxTorque_Nm: 400 is stored as 4000", errors[0].text)

    def test_values_json_for_matlab(self):
        out = self.tmp / "values.json"
        self.assertEqual(calgen.main(["values", str(self.rte), "--variant", "sport", "--json", "-o", str(out)]), 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        tl = data["components"]["CalMapSwc"]["TorqueLimit_Nm"]
        self.assertEqual((tl["name"], tl["shape"], tl["value"][:3]), ("CalMapSwc.CalPrm.TorqueLimit_Nm", [5, 3],
                                                                     [130, 120, 95]))
        self.assertEqual(data["components"]["CalMapSwc"]["Mode"]["value"], [2])
        self.assertEqual(data["components"]["CalMapSwc"]["Gain"]["shape"], [])


class PatchTest(ProfileCase):
    """TOOL-198: caldata.patch_component changes only the lines of changed entries and keeps the comments."""

    COMMENTED = ("  # measured on the bench 2026-09-01\n  BoostCurve:\n", 'value: "Eco"        # Eco for the demo')

    def entries(self, file: Path) -> dict:
        part = caldata.Part(caldata.BASE)
        caldata.read_component_file(file, part, [])
        return part.components["CalMapSwc"]

    def test_changed_entries_only_comments_stay(self):
        self.edit(self.file, "  BoostCurve:\n", self.COMMENTED[0])
        self.edit(self.file, 'value: "Eco"', self.COMMENTED[1])
        text = self.file.read_text(encoding="utf-8")
        entries = self.entries(self.file)
        entries["Mode"] = caldata.Entry("Sport", "draft", "T. Tester", "")          # reason goes, author changes
        entries["BoostCurve"] = caldata.Entry([0, 400, 950, 1500, 1800], "draft", "T. Tester", "bench")
        rows = [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12], [13, 14, 15]]
        entries["TorqueLimit_Nm"] = caldata.Entry(rows, "released", "neXtBSW example",
                                                  "default of the component type (CalMapSwc_data.m)")
        labels = {"TorqueLimit_Nm": [f"SpeedAxis_rpm = {x} rpm" for x in (0, 1000, 2000, 4000, 6000)]}
        patched = caldata.patch_component(text, "CalMapSwc", entries, labels)
        self.assertIsNotNone(patched)
        self.assertIn("  # measured on the bench 2026-09-01\n  BoostCurve:\n    value: [0, 400, 950, 1500, 1800]\n",
                      patched)
        self.assertIn('    value: "Sport"        # Eco for the demo\n    status: draft\n    author: "T. Tester"\n'
                      '  Offsets_Nm:', patched)
        self.assertIn("      - [13, 14, 15]    # SpeedAxis_rpm = 6000 rpm\n    status: released", patched)
        old, new = text.splitlines(), patched.splitlines()
        diff = list(difflib.unified_diff(old, new, lineterm="", n=0))
        added = [line for line in diff if line.startswith("+") and not line.startswith("+++")]
        removed = [line for line in diff if line.startswith("-") and not line.startswith("---")]
        self.assertEqual((len(added), len(removed)), (12, 13), diff)   # map 5 rows, Mode 3 (+ reason), Boost 4
        self.file.write_text(patched, encoding="utf-8")
        again = self.entries(self.file)
        for element, entry in entries.items():
            self.assertTrue(again[element].same_value(entry), element)
            self.assertEqual((again[element].status, again[element].author, again[element].reason),
                             (entry.status, entry.author, entry.reason), element)

    def test_new_and_removed_entries_of_a_variant(self):
        self.edit(self.sport, "  MaxTorque_Nm:\n", "  # sport: more torque\n  MaxTorque_Nm:\n")
        text = self.sport.read_text(encoding="utf-8")
        part = caldata.Part("sport")
        caldata.read_component_file(self.sport, part, [])
        entries = dict(part.components["CalMapSwc"])
        del entries["BoostCurve"]                                  # back at base: leaves the delta
        new = {}
        for element, entry in entries.items():
            new[element] = entry
            if element == "TorqueLimit_Nm":
                new["DerateCurve"] = caldata.Entry([100, 90, 80, 50], "draft", "T", "bench")
        patched = caldata.patch_component(text, "CalMapSwc", new)
        self.assertNotIn("BoostCurve", patched)
        self.assertIn("  # sport: more torque\n  MaxTorque_Nm:", patched)
        self.assertLess(patched.index("DerateCurve"), patched.index("MaxTorque_Nm"))
        self.assertIn("  DerateCurve:\n    value: [100, 90, 80, 50]\n    status: draft\n    author: \"T\"\n", patched)

    def test_other_forms_are_left_to_the_writer(self):
        entries = {"Mode": caldata.Entry("Eco", "draft")}
        self.assertIsNone(caldata.patch_component("format: nextbsw-cal 1\ncomponent: CalMapSwc\nvalues: {}\n",
                                                  "CalMapSwc", entries))
        self.assertIsNone(caldata.patch_component("format: nextbsw-cal 1\ncomponent: CalMapSwc\nvalues:\n"
                                                  "  Mode: {value: 1}\n", "CalMapSwc", entries))
        self.assertIsNone(caldata.patch_component("values: [", "CalMapSwc", entries))
        self.assertIsNone(caldata.patch_component(self.file.read_text(encoding="utf-8"), "Other", entries))


if __name__ == "__main__":
    unittest.main()
