"""Tests of the ODX/PDX export (TOOL-43) with the own profile test/odx/diag.yaml, the other diaggen profiles and
the demo project.

There is no freely available ODX schema (the XSD belongs to the ASAM standard), so the check has three parts:
  1. Round trip: tools/diaggen/odxread.py reads the export back - from the wire content and the references of the
     document, not from the YAML - and every session, security level, identifier (number, coding, conversion,
     limits, unit, text table), routine, DTC (number, display code, severity, snapshot and extended records) and
     DTC group has to come back identical to the description (odxread.expected).
  2. Structure in place of the schema: every ID once, every ID-REF resolves, the order of the layer's children,
     short names unique per collection, the parameters of a message cover its bytes without gap or overlap and
     every coded constant fits its bit length; the request bytes of a service are the UDS bytes.
  3. Mutations of the document (identifier, DTC number, bit length, text table) have to change the summary.
With odxtools installed (not a requirement of the repository) the export is also loaded and en-/decoded by it.

Run: .venv/Scripts/python -m unittest tools/diaggen/test_odxexport.py
"""

import copy
import datetime
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import diaggen  # noqa: E402
import odxexport  # noqa: E402
import odxread  # noqa: E402

ODX_PROFILE = HERE / "test" / "odx" / "diag.yaml"
PROFILES = {"odx": ODX_PROFILE, "profile": HERE / "test" / "profile" / "diag.yaml",
            "bsw": HERE / "test" / "bsw" / "diag.yaml"}
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
MIN = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
DAY = datetime.date(2026, 9, 30)
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

# children of a base variant in the order the export writes them (the order of the ODX 2.2 diagnostic layer)
LAYER_ORDER = ("SHORT-NAME", "LONG-NAME", "DESC", "ADMIN-DATA", "COMPANY-DATAS", "FUNCT-CLASSS",
               "DIAG-DATA-DICTIONARY-SPEC", "DIAG-COMMS", "REQUESTS", "POS-RESPONSES", "NEG-RESPONSES",
               "GLOBAL-NEG-RESPONSES", "IMPORT-REFS", "STATE-CHARTS", "ADDITIONAL-AUDIENCES", "SUB-COMPONENTS",
               "LIBRARYS", "SDGS", "COMPARAM-REFS")
DICTIONARY_ORDER = ("ADMIN-DATA", "DTC-DOPS", "ENV-DATA-DESCS", "DATA-OBJECT-PROPS", "STRUCTURES", "STATIC-FIELDS",
                    "DYNAMIC-LENGTH-FIELDS", "DYNAMIC-ENDMARKER-FIELDS", "END-OF-PDU-FIELDS", "MUXS", "ENV-DATAS",
                    "UNIT-SPEC", "TABLES")


def delivery() -> odxexport.Delivery:
    return odxexport.Delivery(revision="7", date=DAY, company="Muster GmbH")


def export_profile(path: Path = ODX_PROFILE, name: str = "odxprofile") -> tuple:
    d = diaggen.load_file(path)
    return d, odxexport.export_description(d, delivery=delivery(), name=name)


class RoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assert_round_trip(self, d, export, kind: str):
        path = odxexport.write(export, self.tmp / f"{export.name}.{'pdx' if kind == 'pdx' else 'odx-d'}", kind)
        got = odxread.comparable(odxread.load(path))
        want = odxread.expected(d)
        for key in want:
            self.assertEqual(want[key], got[key], f"{export.name} {kind}: {key}")
        return odxread.load(path)

    def test_own_profile_comes_back_identical_as_odx_and_pdx(self):
        d, export = export_profile()
        for kind in ("odx", "pdx"):
            back = self.assert_round_trip(d, export, kind)
        # what the profile is there for, in plain numbers
        self.assertEqual(sorted(back["dids"]), [0x0300, 0x0301, 0xF18C, 0xF190])
        self.assertEqual(sorted(back["routines"]), [0x0500, 0x0501])
        self.assertEqual(sorted(back["routines"][0x0500]["steps"]), ["results", "start", "stop"])
        derived = 0x160000 + 0x0100            # rte.deadline in the default neXtBSW range (TOOL-178)
        self.assertEqual(sorted(back["dtcs"]), [derived, 0x912345])
        self.assertEqual(back["dtcs"][derived]["display"], "P160100")
        self.assertEqual(back["dtcs"][0x912345]["display"], "B112345")
        self.assertEqual(back["dtcs"][0x912345]["snapshots"], {0x01: ("BatteryState", "Vin"), 0x10: ("Limits",)})
        self.assertEqual(back["dtcs"][0x912345]["extended"][0x02],
                         (("occurrences", None, "occurrence_counter"), ("fdc", None, "fault_detection_counter"),
                          ("limits", "Limits", None)))
        self.assertEqual(back["dtc_groups"], {"Chassis": (0x900000, 0x900000, 0x9FFFFF),
                                              "NeXtBsw": (0x160000, 0x160000, 0x16FFFF)})
        voltage, current, temperature = back["dids"][0x0300]["elements"][:3]
        self.assertEqual(voltage[1:], ("uint16", 2, "big", 0.001, 0.0, "V", 0.0, 65.535, None))
        self.assertEqual(current[1:], ("int16", 2, "little", 0.01, -2.5, "A", -300.0, None, None))
        self.assertEqual(temperature[1:], ("float32", 4, "big", 1.0, 0.0, "degC", None, 125.5, None))
        self.assertEqual(back["security"]["Plant"], (0x41, 2, 2, "hmac-sha256", 1, 60000))

    def test_other_profiles_and_projects_come_back_identical(self):
        for name, path in PROFILES.items():
            d, export = export_profile(path, name)
            self.assert_round_trip(d, export, "odx")
        for project in (DEMO, MIN):
            export = odxexport.export_project(project, delivery=delivery())
            self.assert_round_trip(diaggen.load_project(project), export, "pdx")

    def test_addresses_come_from_the_project(self):
        export = odxexport.export_project(DEMO, delivery=delivery())
        comparams = odxread.summary(export.text)["comparams"]
        project = yaml.safe_load(DEMO.read_text(encoding="utf-8"))
        self.assertEqual(comparams["ISO_15765_2.CP_CanPhysReqId"], str(project["diagnostics"]["request_id"]))
        self.assertEqual(comparams["ISO_15765_2.CP_CanRespUSDTId"], str(project["diagnostics"]["response_id"]))
        self.assertEqual(comparams["ISO_15765_2.CP_CanFuncReqId"], str(project["diagnostics"]["functional_id"]))
        self.assertEqual(comparams["ISO_15765_3.CP_P2Max"], "50000")          # 50 ms of diag.yaml, in us
        self.assertEqual(comparams["ISO_15765_3.CP_P2Star"], "5000000")
        self.assertEqual(comparams["ISO_11898_2_DWCAN.CP_Baudrate"], "500000")  # rte.yaml uds.bus CAN0
        # the minimal project names other ISO-TP values - they follow the project, not a default
        other = odxread.summary(odxexport.export_project(MIN, delivery=delivery()).text)["comparams"]
        self.assertNotEqual(other["ISO_15765_2.CP_CanPhysReqId"], comparams["ISO_15765_2.CP_CanPhysReqId"])

    def test_description_alone_has_no_addresses(self):
        _d, export = export_profile()
        self.assertEqual(odxread.summary(export.text)["comparams"], {})


class DocumentTest(unittest.TestCase):
    """Structure checks in place of the (not freely available) ODX schema."""

    @classmethod
    def setUpClass(cls):
        cls.documents = {name: export_profile(path, name)[1].text for name, path in PROFILES.items()}
        cls.documents["demo"] = odxexport.export_project(DEMO, delivery=delivery()).text

    def test_ids_are_unique_and_every_reference_resolves(self):
        for name, text in self.documents.items():
            root = ET.fromstring(text)
            ids = [e.get("ID") for e in root.iter() if e.get("ID") is not None]
            self.assertEqual(len(ids), len(set(ids)), name)
            for e in root.iter():
                target = e.get("ID-REF")
                if target is not None and e.get("DOCREF") is None:
                    self.assertIn(target, ids, f"{name}: {e.tag} {target}")
            for e in root.iter():
                if e.get("ID") is not None:
                    self.assertRegex(e.get("ID"), r"^[A-Za-z_][A-Za-z0-9_.\-]*$", name)
                    self.assertIsNotNone(e.find("SHORT-NAME"), f"{name}: {e.tag} {e.get('ID')} without SHORT-NAME")

    def test_order_of_the_layer_and_the_dictionary(self):
        for name, text in self.documents.items():
            root = ET.fromstring(text)
            self.assertEqual(root.get("MODEL-VERSION"), "2.2.0")
            layer = root.find("./DIAG-LAYER-CONTAINER/BASE-VARIANTS/BASE-VARIANT")
            tags = [c.tag for c in layer]
            self.assertEqual(tags, sorted(tags, key=LAYER_ORDER.index), name)
            tags = [c.tag for c in layer.find("DIAG-DATA-DICTIONARY-SPEC")]
            self.assertEqual(tags, sorted(tags, key=DICTIONARY_ORDER.index), name)

    def test_short_names_are_unique_per_collection(self):
        for name, text in self.documents.items():
            root = ET.fromstring(text)
            for collection in root.iter():
                if collection.tag.endswith("S") and len(collection):
                    names = [_text(c, "SHORT-NAME") for c in collection if c.find("SHORT-NAME") is not None]
                    self.assertEqual(len(names), len(set(names)), f"{name}: {collection.tag}")

    def test_parameters_cover_each_message_without_gap_and_constants_fit(self):
        for name, text in self.documents.items():
            root = ET.fromstring(text)
            ids = {e.get("ID"): e for e in root.iter() if e.get("ID") is not None}
            for tag in ("REQUEST", "POS-RESPONSE", "STRUCTURE"):
                for message in root.iter(tag):
                    position = 0
                    for param in message.findall("./PARAMS/PARAM"):
                        where = f"{name}: {tag} {_text(message, 'SHORT-NAME')}.{_text(param, 'SHORT-NAME')}"
                        self.assertEqual(int(_text(param, "BYTE-POSITION")), position, where)
                        width = _width(param, ids)
                        if width is None:              # a list up to the end of the message is the last one
                            self.assertIs(param, message.findall("./PARAMS/PARAM")[-1], where)
                            break
                        if param.get(XSI_TYPE) == "CODED-CONST":
                            self.assertLess(int(_text(param, "CODED-VALUE")), 1 << (8 * width), where)
                        position += width
                    size = message.find("BYTE-SIZE")
                    if size is not None:
                        self.assertEqual(int(size.text), position, f"{name}: {_text(message, 'SHORT-NAME')}")

    def test_requests_are_the_uds_bytes(self):
        root = ET.fromstring(self.documents["odx"])
        requests = {_text(r, "SHORT-NAME"): r for r in root.iter("REQUEST")}
        expect = {"Session_Workshop": "1003", "RequestSeed_Plant": "2741", "Read_BatteryState": "220300",
                  "Read_Vin": "22f190", "Balance_Stop": "31020500", "Balance_Results": "31030500",
                  "Ping_Start": "31010501", "ClearDTC_All": "14ffffff", "ClearDTC_Group_Chassis": "14900000",
                  "ClearDTC_Group_NeXtBsw": "14160000"}
        for name, wire in expect.items():
            params = requests[name].findall("./PARAMS/PARAM")
            self.assertTrue(all(p.get(XSI_TYPE) == "CODED-CONST" for p in params), name)
            data = b"".join(int(_text(p, "CODED-VALUE")).to_bytes(_width(p, {}), "big") for p in params)
            self.assertEqual(data.hex(), wire, name)

    def test_text_names_its_origin_and_no_foreign_text(self):
        text = self.documents["demo"]
        self.assertIn("<TOOL>neXtBSW tools/diaggen (TOOL-43)</TOOL>", text)
        self.assertIn("<DATE>2026-09-30T00:00:00</DATE>", text)
        self.assertIn("<LONG-NAME>Muster GmbH</LONG-NAME>", text)


def _text(element, tag):
    child = element.find(tag)
    return child.text if child is not None else None


def _width(param, ids) -> int | None:
    """Bytes of a parameter, None for a list up to the end of the message."""
    if param.get(XSI_TYPE) == "CODED-CONST":
        return int(_text(param.find("DIAG-CODED-TYPE"), "BIT-LENGTH")) // 8
    if param.get(XSI_TYPE) == "MATCHING-REQUEST-PARAM":
        return int(_text(param, "BYTE-LENGTH"))
    dop = ids[param.find("DOP-REF").get("ID-REF")]
    if dop.tag in ("DATA-OBJECT-PROP", "DTC-DOP"):
        return int(_text(dop.find("DIAG-CODED-TYPE"), "BIT-LENGTH")) // 8
    if dop.tag == "STRUCTURE":
        return int(_text(dop, "BYTE-SIZE"))
    return None                                   # END-OF-PDU-FIELD, ENV-DATA-DESC


class MessagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, change) -> diaggen.Description:
        data = yaml.safe_load(ODX_PROFILE.read_text(encoding="utf-8"))
        change(data)
        path = self.tmp / "diag.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return diaggen.load_file(path)

    def test_unknown_unit_is_a_warning_with_yaml_path_and_line(self):
        _d, export = export_profile()
        found = [m for m in export.messages if m.path == ("data_identifiers", 0, "data", 3, "unit")]
        self.assertEqual(len(found), 1, export.messages)
        self.assertEqual(found[0].severity, "warning")
        self.assertIn("SoH-points", found[0].text)
        self.assertIsNotNone(found[0].line)
        self.assertEqual(len(export.messages), 1, export.messages)     # nothing else to report
        self.assertIn("<DISPLAY-NAME>SoH-points</DISPLAY-NAME>", export.text)   # still shown to the tester

    def test_a_description_the_generator_refuses_is_not_exported(self):
        def change(data):
            data["sessions"][1]["id"] = 0x40             # ecu/uds enters 0x01 and 0x03 only (diaggen.check)
        with self.assertRaises(odxexport.OdxExportError) as caught:
            odxexport.export_description(self.load(change))
        self.assertIn(("sessions", 1, "id"), [m.path for m in caught.exception.messages])

    def test_colliding_odx_names_stop_the_export(self):
        def change(data):
            data["routines"][1]["name"] = "Read"          # Read_Start: routine Read, step start ...
            data["data_identifiers"][2]["name"] = "Start"  # ... and reading the identifier Start
        with self.assertRaises(odxexport.OdxExportError) as caught:
            odxexport.export_description(self.load(change))
        self.assertIn("Read_Start", str(caught.exception))

    def test_a_routine_parameter_with_unknown_unit_is_reported_at_its_path(self):
        def change(data):
            data["routines"][0]["results"]["response"][1]["unit"] = "digits"
        export = odxexport.export_description(self.load(change))
        self.assertIn(("routines", 0, "results", "response", 1, "unit"), [m.path for m in export.messages])


class MutationTest(unittest.TestCase):
    """A wrong document must not read back as the description (the reader really compares)."""

    MUTATIONS = {
        "identifier": (r"(<SHORT-NAME>DataIdentifier</SHORT-NAME>\s*<BYTE-POSITION>1</BYTE-POSITION>\s*"
                       r"<CODED-VALUE>)768(<)", r"\g<1>769\2"),
        "dtc number": (r"<TROUBLE-CODE>9511749<", "<TROUBLE-CODE>9511748<"),
        "bit length": (r"(<SHORT-NAME>BatteryState_current</SHORT-NAME>[\s\S]*?<BIT-LENGTH>)16(<)", r"\g<1>32\2"),
        "text table": (r"<VT>precharge</VT>", "<VT>pre-charge</VT>"),
        "record": (r"(<SHORT-NAME>Record10</SHORT-NAME>[\s\S]*?<LOWER-LIMIT[^>]*>)16(<)", r"\g<1>17\2"),
        "byte order": (r'(BASE-DATA-TYPE="A_INT32") IS-HIGHLOW-BYTE-ORDER="false"', r"\1"),
    }

    def test_every_mutation_is_noticed(self):
        d, export = export_profile()
        want = odxread.expected(d)
        self.assertEqual(odxread.comparable(odxread.summary(export.text)), want)
        for name, (pattern, replacement) in self.MUTATIONS.items():
            text, count = re.subn(pattern, replacement, export.text, count=1)
            self.assertEqual(count, 1, name)
            try:
                got = odxread.comparable(odxread.summary(text))
            except (odxread.OdxReadError, KeyError, ValueError):
                continue                              # a broken document is noticed as well
            self.assertNotEqual(got, want, name)


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pdx_package_and_reproducibility(self):
        _d, export = export_profile()
        first = odxexport.write(export, self.tmp / "a.pdx")
        second = odxexport.write(copy.deepcopy(export), self.tmp / "b.pdx")
        self.assertEqual(first.read_bytes(), second.read_bytes())          # same input, same bytes
        self.assertEqual(odxread.pdx_files(first), {"odxprofile.odx-d": odxexport.ODX_D_MIME})
        with zipfile.ZipFile(first) as archive:
            self.assertEqual(sorted(archive.namelist()), ["index.xml", "odxprofile.odx-d"])
            self.assertEqual(archive.read("odxprofile.odx-d").decode("utf-8"), export.text)
            index = ET.fromstring(archive.read("index.xml"))
        self.assertEqual(index.get("F-DTD-VERSION"), "ODX-2.2.0")
        self.assertEqual(index.find(".//FILE").get("CREATION-DATE"), "2026-09-30T00:00:00")
        again = odxexport.export_description(diaggen.load_file(ODX_PROFILE), delivery=delivery(), name="odxprofile")
        self.assertEqual(again.text, export.text)
        self.assertFalse(list(self.tmp.glob("*.tmp")))

    def test_command_line(self):
        target = self.tmp / "demo.pdx"
        run = subprocess.run([sys.executable, "-m", "tools.diaggen", "pdx", str(DEMO.parent), "-o", str(target),
                              "--date", "2026-09-30", "--revision", "3"], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("PDX written", run.stdout)
        self.assertEqual(sorted(odxread.load(target)["dids"]), sorted(odxread.expected(
            diaggen.load_project(DEMO))["dids"]))
        target = self.tmp / "demo.odx-d"
        run = subprocess.run([sys.executable, "-m", "tools.diaggen", "odx", str(DEMO), "-o", str(target)],
                             cwd=REPO, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(target.read_text(encoding="utf-8").startswith("<?xml"))
        # a project that does not load: exit 1 and the message on stderr
        broken = self.tmp / "project.yaml"
        broken.write_text("project: {name: x}\n", encoding="utf-8")
        run = subprocess.run([sys.executable, "-m", "tools.diaggen", "odx", str(broken), "-o",
                              str(self.tmp / "x.odx-d")], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn("diaggen odx:", run.stderr)
        self.assertFalse((self.tmp / "x.odx-d").exists())


try:
    import odxtools  # noqa: F401
except ImportError:
    odxtools = None


@unittest.skipIf(odxtools is None, "odxtools not installed (optional cross check, not a requirement)")
class OdxtoolsTest(unittest.TestCase):
    """The export as a foreign library reads it: loaded strictly, requests encoded, responses decoded. The
    communication parameter subsets are the tester's; here they are stood in by minimal subsets of our own."""

    def database(self, text: str):
        from odxtools.database import Database

        database = Database(use_weakrefs=False)
        subsets = {}
        for subset, name in re.findall(r'ID-REF="([A-Z0-9_]+)\.(CP_\w+)"', text):
            subsets.setdefault(subset, []).append(name)
        for subset, names in subsets.items():
            params = "".join(f'<COMPARAM ID="{subset}.{n}" PARAM-CLASS="COM" CPTYPE="STANDARD" CPUSAGE="ECU-COMM">'
                             f'<SHORT-NAME>{n}</SHORT-NAME><PHYSICAL-DEFAULT-VALUE>0</PHYSICAL-DEFAULT-VALUE>'
                             f'<DATA-OBJECT-PROP-REF ID-REF="{subset}.DOP"/></COMPARAM>' for n in names)
            database.add_odx_xml_tree(ET.fromstring(
                f'<ODX xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" MODEL-VERSION="2.2.0">'
                f'<COMPARAM-SUBSET ID="{subset}.CS" CATEGORY="X"><SHORT-NAME>{subset}</SHORT-NAME>'
                f'<COMPARAMS>{params}</COMPARAMS><DATA-OBJECT-PROPS><DATA-OBJECT-PROP ID="{subset}.DOP">'
                f'<SHORT-NAME>U32</SHORT-NAME><COMPU-METHOD><CATEGORY>IDENTICAL</CATEGORY></COMPU-METHOD>'
                f'<DIAG-CODED-TYPE BASE-DATA-TYPE="A_UINT32" xsi:type="STANDARD-LENGTH-TYPE"><BIT-LENGTH>32'
                f'</BIT-LENGTH></DIAG-CODED-TYPE><PHYSICAL-TYPE BASE-DATA-TYPE="A_UINT32"/></DATA-OBJECT-PROP>'
                f'</DATA-OBJECT-PROPS></COMPARAM-SUBSET></ODX>'))
        database.add_odx_xml_tree(ET.fromstring(text.encode("utf-8")))
        database.refresh()
        return {s.short_name: s for s in database.diag_layers[0].services}

    def test_encode_and_decode(self):
        services = self.database(export_profile()[1].text)
        self.assertEqual(services["Write_Limits"].request.encode(Limits={"maxCurrent": -5000}).hex(),
                         "2e0301ffffec78")
        self.assertEqual(services["Balance_Start"].request.encode(targetVoltage=3700, mode="active").hex(),
                         "310105000e7402")
        values = services["Read_BatteryState"].decode_message(
            bytes.fromhex("620300" "3039" "fa00" "42c80000" "07" "02" "78563412")).param_dict["BatteryState"]
        self.assertEqual(values, {"voltage": 12.345, "current": 0.0, "temperature": 100.0, "health": 7,
                                  "contactor": "closed", "counter": 0x12345678})
        # 19 04 with record FF: both records of the DTC, each selected by its number
        record = services["ReadDTC_Snapshot"].decode_message(bytes.fromhex(
            "5904" "912345" "09" "01" "02" "0300" "3039" "fa00" "42c80000" "07" "02" "78563412" "f190"
            + b"WNXBSW00000000001".hex() + "10" "01" "0301" "ffffec78")).param_dict["Record"]
        cases = [r["DTCSnapshotRecord"][0] for r in record["DTCSnapshotRecords"]]
        self.assertEqual(cases, ["Record01", "Record10"])
        services = self.database(odxexport.export_project(DEMO, delivery=delivery()).text)
        self.assertEqual(services["Read_ActiveDiagnosticSession"].decode_message(
            bytes.fromhex("62f18603")).param_dict["ActiveDiagnosticSession"], {"activeSession": "Extended"})


if __name__ == "__main__":
    unittest.main()
