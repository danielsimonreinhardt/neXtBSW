"""Host tests of the AUTOSAR cross-check (ARCH-68).

Only a small synthetic ECUC excerpt written for this test (own module and parameter names in the ECUC file
structure, own numbers, no AUTOSAR text) - the real file is never part of the repository and never needed here.
The map is checked against the nxconfig model, so a renamed model field or a drifted limit turns the test red.

Run: .venv/Scripts/python -m unittest tools/autosar_xcheck/test_xcheck.py
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from autosar_xcheck import xcheck  # noqa: E402

SENTINEL = "SENTINEL-DESCRIPTION-TEXT"


def param(kind: str, name: str, low=None, high=None) -> str:
    limits = ""
    if low is not None:
        limits += f"<MIN>{low}</MIN>"
    if high is not None:
        limits += f"<MAX>{high}</MAX>"
    return (f"<{kind}><SHORT-NAME>{name}</SHORT-NAME><DESC><L-2 L=\"EN\">{SENTINEL}</L-2></DESC>"
            f"{limits}</{kind}>")


SYNTHETIC = f"""<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="http://autosar.org/schema/r4.0"><AR-PACKAGES><AR-PACKAGE><ELEMENTS>
<ECUC-MODULE-DEF><SHORT-NAME>Mod</SHORT-NAME><CONTAINERS>
  <ECUC-PARAM-CONF-CONTAINER-DEF><SHORT-NAME>Box</SHORT-NAME>
    <PARAMETERS>
      {param("ECUC-INTEGER-PARAM-DEF", "BoxEqual", 1, 255)}
      {param("ECUC-INTEGER-PARAM-DEF", "BoxWide", 0, 65535)}
      {param("ECUC-INTEGER-PARAM-DEF", "BoxTight", 10, 20)}
      {param("ECUC-FLOAT-PARAM-DEF", "BoxSeconds", 0, 1)}
      {param("ECUC-FLOAT-PARAM-DEF", "BoxOpen", 0, "INF")}
    </PARAMETERS>
    <REFERENCES>{param("ECUC-REFERENCE-DEF", "BoxRef")}</REFERENCES>
    <SUB-CONTAINERS>
      <ECUC-CHOICE-CONTAINER-DEF><SHORT-NAME>Inner</SHORT-NAME>
        <PARAMETERS>{param("ECUC-INTEGER-PARAM-DEF", "InnerValue", 0, 7)}</PARAMETERS>
      </ECUC-CHOICE-CONTAINER-DEF>
    </SUB-CONTAINERS>
  </ECUC-PARAM-CONF-CONTAINER-DEF>
</CONTAINERS></ECUC-MODULE-DEF>
</ELEMENTS></AR-PACKAGE></AR-PACKAGES></AUTOSAR>
"""


def entry(parameter, ours, container="Box", module="Mod", **extra):
    return {"nxconfig": "X.y", "yaml": "x.yaml a.b", "module": module, "container": container,
            "parameter": parameter, "ours": ours, **extra}


class Compare(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.arxml = Path(cls.tmp.name) / "synthetic.arxml"
        cls.arxml.write_text(SYNTHETIC, encoding="utf-8")
        cls.ecuc, cls.source = xcheck.open_ecuc(arxml_path=str(cls.arxml))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def status(self, **kwargs):
        return xcheck.compare(entry(**kwargs), self.ecuc).status

    def test_index_and_nested_container(self):
        self.assertEqual(sorted(self.ecuc.modules["Mod"]), ["Box", "Inner"])
        self.assertEqual(self.ecuc.find("Mod", "Inner", "InnerValue"), (True, "", (0.0, 7.0)))
        self.assertEqual(self.ecuc.find("Mod", "Box", "BoxOpen")[2], (0.0, None))      # INF = unbounded
        self.assertTrue(self.ecuc.find("Mod", "Box", "BoxRef")[0])                       # a reference counts

    def test_ok_and_narrower(self):
        self.assertEqual(self.status(parameter="BoxEqual", ours=[1, 255]), "OK")
        self.assertEqual(self.status(parameter="BoxWide", ours=[0, 255]), "NARROWER")
        self.assertEqual(self.status(parameter="BoxOpen", ours=[0, 5]), "NARROWER")

    def test_outside_is_a_failure(self):
        for ours in ([0, 255], [1, 256]):
            result = xcheck.compare(entry("BoxEqual", ours), self.ecuc)
            self.assertEqual(result.status, "OUTSIDE")
            self.assertTrue(result.failed)
        self.assertEqual(self.status(parameter="BoxTight", ours=[5, 15]), "OUTSIDE")

    def test_unbounded_is_a_warning_not_a_failure(self):
        result = xcheck.compare(entry("BoxSeconds", [0.001, None]), self.ecuc)
        self.assertEqual(result.status, "UNBOUNDED")
        self.assertFalse(result.failed)
        self.assertEqual(self.status(parameter="BoxOpen", ours=[0, None]), "OK")

    def test_known_difference(self):
        result = xcheck.compare(entry("BoxEqual", [0, 300], known="reason text"), self.ecuc)
        self.assertEqual((result.status, result.failed), ("KNOWN", False))
        self.assertIn("reason text", result.detail)

    def test_missing_names_the_level(self):
        for kwargs, text in ((dict(parameter="Gone", ours=[0, 1]), "Mod.Box.Gone"),
                             (dict(parameter="BoxEqual", ours=[0, 1], container="Nope"), "Mod.Nope"),
                             (dict(parameter="BoxEqual", ours=[0, 1], module="Other"), "module Other")):
            result = xcheck.compare(entry(**kwargs), self.ecuc)
            self.assertEqual(result.status, "MISSING")
            self.assertTrue(result.failed)
            self.assertIn(text, result.detail)

    def test_report_has_no_description_text(self):
        results = xcheck.run(self.ecuc, [entry("BoxEqual", [1, 255]), entry("Gone", [0, 1])])
        text = xcheck.report(results, self.source)
        self.assertNotIn(SENTINEL, text)
        self.assertIn("[OK] x.yaml a.b <-> Mod.BoxEqual", text)
        self.assertIn("[MISSING]", text)
        self.assertIn("summary: MISSING 1, OK 1", text)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.arxml = self.dir / "synthetic.arxml"
        self.arxml.write_text(SYNTHETIC, encoding="utf-8")
        self.map = self.dir / "map.yaml"

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, args, env=None):
        old = os.environ.get(xcheck.ENV_ZIP)
        os.environ.pop(xcheck.ENV_ZIP, None)
        if env:
            os.environ[xcheck.ENV_ZIP] = env
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = xcheck.main(args)
        finally:
            os.environ.pop(xcheck.ENV_ZIP, None)
            if old is not None:
                os.environ[xcheck.ENV_ZIP] = old
        return code, out.getvalue(), err.getvalue()

    def write_map(self, parameter, ours):
        self.map.write_text("entries:\n  - {nxconfig: X.y, yaml: \"x.yaml a\", module: Mod, container: Box, "
                            f"parameter: {parameter}, ours: {ours}}}\n", encoding="utf-8")

    def test_no_file_gives_exit_2_and_names_the_variable(self):
        code, _, err = self.run_main([])
        self.assertEqual(code, 2)
        self.assertIn(xcheck.ENV_ZIP, err)

    def test_zip_from_environment_variable(self):
        archive = self.dir / "release.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.write(self.arxml, "folder/ecuc.arxml")
            zf.writestr("xml.xsd", "<x/>")
        self.write_map("BoxEqual", "[1, 255]")
        code, out, _ = self.run_main(["--map", str(self.map)], env=str(archive))
        self.assertEqual(code, 0)
        self.assertIn("release.zip sha256", out)
        self.assertIn("[OK]", out)

    def test_exit_1_on_outside_and_missing(self):
        self.write_map("BoxEqual", "[0, 255]")
        self.assertEqual(self.run_main(["--arxml", str(self.arxml), "--map", str(self.map)])[0], 1)
        self.write_map("Renamed", "[0, 255]")
        self.assertEqual(self.run_main(["--arxml", str(self.arxml), "--map", str(self.map)])[0], 1)

    def test_broken_zip_gives_exit_2(self):
        bad = self.dir / "bad.zip"
        bad.write_bytes(b"not a zip")
        code, _, err = self.run_main([], env=str(bad))
        self.assertEqual(code, 2)
        self.assertIn("autosar_xcheck:", err)

    def test_zip_without_exactly_one_arxml(self):
        archive = self.dir / "empty.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("xml.xsd", "<x/>")
        code, _, err = self.run_main([], env=str(archive))
        self.assertEqual(code, 2)
        self.assertIn("exactly one .arxml", err)


class MapAgainstModel(unittest.TestCase):
    """The map is the vocabulary page of proposal A: every entry has to point at a real model field."""

    @classmethod
    def setUpClass(cls):
        cls.entries = xcheck.load_map()

    def test_entries_are_complete_and_unique(self):
        keys = {"nxconfig", "yaml", "module", "container", "parameter"}
        seen = set()
        for e in self.entries:
            self.assertTrue(keys <= set(e), e)
            self.assertLessEqual(set(e) - keys, {"scale", "ours", "known", "note"}, e)
            self.assertNotIn(e["nxconfig"], seen)
            seen.add(e["nxconfig"])

    def test_every_nxconfig_field_exists_and_limits_resolve(self):
        for e in self.entries:
            low, high = xcheck.our_limits(e)             # raises on an unknown class or field
            if low is not None and high is not None:
                self.assertLessEqual(low, high, e["nxconfig"])

    def test_the_reference_page_lists_every_entry(self):
        page = (HERE.parents[1] / "doc/architecture/autosar-ecuc-map.md").read_text(encoding="utf-8")
        for e in self.entries:
            self.assertIn(f"`{e['module']}.{e['parameter']}`", page, e["nxconfig"])
            self.assertIn(f"`{e['yaml']}`", page, e["nxconfig"])

    def test_diagdesc_warns_at_the_limits_of_the_map(self):
        """Proposal C: the warning constants of diagdesc.py carry the AUTOSAR limits of the map."""
        source = (HERE.parents[0] / "nxconfig" / "diagdesc.py").read_text(encoding="utf-8")
        for limit in ("P2_WARN_MS = 1000", "P2_STAR_WARN_MS = 100_000", "TIME_DEBOUNCE_WARN_MS = 3_600_000",
                      "ROWS_WARN = 31"):
            self.assertIn(limit, source)

    def test_limits_come_from_the_model(self):
        self.assertEqual(xcheck.model_limits("DiagSession.id"), (1, 126))
        self.assertEqual(xcheck.model_limits("DiagSession.p2_ms"), (1, None))            # PositiveInt
        self.assertEqual(xcheck.model_limits("DtcCounterDebounce.passed_threshold"), (-128, -1))
        self.assertEqual(xcheck.model_limits("CanUse.sjw_tq"), (1, None))                # optional PositiveInt
        self.assertEqual(xcheck.model_limits("DiagFaultMemory.snapshot_records_max"), (0, 254))
        by_name = {e["nxconfig"]: e for e in self.entries}
        self.assertEqual(xcheck.our_limits(by_name["DiagSession.p2_ms"]), (0.001, None))
        self.assertEqual(xcheck.our_limits(by_name["CanBusOff.max_pause_ms"]), (0, 65.535))

    def test_explicit_limits_agree_with_the_code(self):
        from nxconfig import nvm
        by_name = {e["nxconfig"]: e for e in self.entries}
        self.assertEqual(by_name["NvmBlockUse.length"]["ours"][1], nvm.MAX_BLOCK_BYTES)
        # AUTOSAR level = (requestSeed sub-function + 1) / 2 for the odd sub-functions 0x01..0x7D
        (low, high) = xcheck.model_limits("DiagSecurityLevel.level")
        self.assertEqual(by_name["DiagSecurityLevel.level"]["ours"], [(low + 1) // 2, (high + 1) // 2])


if __name__ == "__main__":
    unittest.main()
