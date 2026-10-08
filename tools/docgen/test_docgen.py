# SPDX-License-Identifier: Apache-2.0
"""Host test of the reference generator (DOC-03) and the module catalogue (DOC-04).

A small test schema of its own (below) carries every case the generator has to handle - description from Field,
trailing comment, comment block, inherited field, object docstring, gaps, units from name and from Field, alias,
enumerations, ranges, lengths, patterns, lists, maps, optional objects, a discriminated union with nested objects.
One run checks the checked-in doc/reference against the real tools/nxconfig schema. The module catalogue is
tested on a small libs/ tree of its own (below) and against the real libs/ and tools/test/tests.yaml. The console
page (DOC-09) has its own test, test_console.py; the real-reference checks here include it and are skipped without
external/openbsw.

    .venv/Scripts/python tools/docgen/test_docgen.py
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Annotated, Literal

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from tools.docgen import console, modulemap, modules, reference, render, schema  # noqa: E402
from tools.docgen.__main__ import main as cli_main  # noqa: E402
from tools.test import run_tests  # noqa: E402

# The real reference includes the console page (DOC-09), which needs the OpenBSW sources.
HAS_OPENBSW = (REPO / console.OPENBSW / "libs").is_dir()

# ----------------------------------------------------------------------------------------------- test schema

Ident = Annotated[str, Field(min_length=1, pattern=r"^[a-z]+$")]


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Facts(Base):
    source: str | None = None                        # where the values come from
    verify: bool = False


class Pin(Base):
    """A pin of the test chip.

    Second paragraph with a list:

    - first item
      continued
    - second item
    """

    pin: str                                         # pin name, e.g. <port>_<n>
    pad: int | None = None


class Wire(Facts):
    """Wire sensor."""

    kind: Literal["wire"]
    length_mm: Annotated[int, Field(gt=0)]           # length of the wire
    pin: Pin


class Coil(Facts):
    """Coil sensor."""

    kind: Literal["coil"]
    turns: Annotated[int, Field(ge=1, le=1000)]      # number of turns
    pin: Pin


Sensor = Annotated[Wire | Coil, Field(discriminator="kind")]


class Top(Base):
    schema_version: Literal[1]
    name: Ident = Field(description="Name from Field(description).")
    class_: Literal["a", "b"] = Field(alias="class")   # class with an alias
    clock_hz: Annotated[int, Field(gt=0)]          # system clock
    # comment block above the field,
    # two lines
    gain: int = 1
    ratio: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.5
    width: int = Field(default=8, json_schema_extra={"unit": "bit"})    # unit from Field
    tags: list[Ident] = []                          # tags | with a pipe
    bounds: Annotated[list[int], Field(min_length=2, max_length=2)] = [1, 2]
    limits: dict[Ident, int] = {}                   # key: domain
    pin: Pin | None = None
    pins: list[Pin] = Field(min_length=1, default_factory=lambda: [Pin(pin="A1")])
    sensors: dict[Ident, Sensor] = {}               # sensors by name
    int32: Annotated[int, Field(ge=-(1 << 31), le=(1 << 31) - 1)] = 0
    mode: Literal["x"] | list[Ident] = []           # all or a list
    label: Annotated[str, Field(min_length=1, max_length=24)] = "top"
    undocumented: str | None = None


def page(model=Top, name="test.md") -> reference.Page:
    return reference.Page(name, "Test description", "Test", "Intro text.",
                          [reference.Root(model, "test/<x>/top.yaml", "Test file")])


def by_path(sections, path):
    return next(f for s in sections for f in s.fields if f.path == path)


# ----------------------------------------------------------------------------------------------- tests


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.sections = schema.document(Top)

    def test_sections_pre_order_with_unique_anchors(self):
        titles = [(s.path, s.variant) for s in self.sections]
        self.assertEqual(titles, [
            ("", None), ("pin", None), ("pins[]", None),
            ("sensors.<key>", "kind: wire"), ("sensors.<key>.pin", None),
            ("sensors.<key>", "kind: coil"), ("sensors.<key>.pin", None),
        ])
        anchors = [s.anchor for s in self.sections]
        self.assertEqual(len(anchors), len(set(anchors)), anchors)
        self.assertEqual([s.depth for s in self.sections], [0, 1, 1, 1, 2, 1, 2])

    def test_links_point_to_existing_sections(self):
        anchors = {s.anchor for s in self.sections}
        for s in self.sections:
            for f in s.fields:
                for t in f.type.models():
                    self.assertIn(t.section, anchors, f.path)

    def test_description_sources(self):
        cases = {
            "name": ("Name from Field(description).", "field"),
            "class": ("class with an alias", "comment"),
            "gain": ("comment block above the field, two lines", "comment"),
            "pin": ("A pin of the test chip.", "type"),
            "undocumented": (None, None),
            "schema_version": (None, None),
        }
        for path, expected in cases.items():
            f = by_path(self.sections, path)
            self.assertEqual((f.description, f.description_source), expected, path)

    def test_inherited_field_keeps_comment_and_owner(self):
        wire = next(s for s in self.sections if s.variant == "kind: wire")
        source = next(f for f in wire.fields if f.key == "source")
        verify = next(f for f in wire.fields if f.key == "verify")
        self.assertEqual((source.description, source.owner), ("where the values come from", "Facts.source"))
        self.assertTrue(verify.missing_description)
        self.assertEqual(verify.owner, "Facts.verify")

    def test_units_and_gaps(self):
        self.assertEqual((by_path(self.sections, "clock_hz").unit, by_path(self.sections, "clock_hz").unit_source),
                         ("Hz", "name"))
        self.assertEqual((by_path(self.sections, "width").unit, by_path(self.sections, "width").unit_source),
                         ("bit", "field"))
        self.assertTrue(by_path(self.sections, "gain").missing_unit)
        self.assertTrue(by_path(self.sections, "limits").missing_unit)          # map value is numeric
        self.assertFalse(by_path(self.sections, "name").missing_unit)          # strings need no unit
        self.assertFalse(by_path(self.sections, "class").missing_unit)         # enumerations need no unit
        wire = next(s for s in self.sections if s.variant == "kind: wire")
        self.assertEqual(next(f for f in wire.fields if f.key == "length_mm").unit, None)   # mm: not in table
        self.assertEqual(schema.unit_from_name("r_top_ohm"), "Ω")
        self.assertEqual(schema.unit_from_name("gate_resistance_mohm"), "mΩ")
        self.assertIsNone(schema.unit_from_name("bitrate"))

    def test_required_and_defaults(self):
        expected = {
            "schema_version": (True, None), "clock_hz": (True, None), "gain": (False, "1"),
            "ratio": (False, "0.5"), "tags": (False, "[]"), "pin": (False, "null"), "pins": (False, "[{}]"),
            "label": (False, '"top"'), "bounds": (False, "[1, 2]"), "limits": (False, "{}"),
        }
        for path, (required, default) in expected.items():
            f = by_path(self.sections, path)
            self.assertEqual((f.required, f.default), (required, default), path)
        self.assertEqual(by_path(self.sections, "class").key, "class")

    def test_allowed_values(self):
        def allowed(path):
            return "; ".join(render.allowed(by_path(self.sections, path).type))

        self.assertEqual(allowed("schema_version"), "`1`")
        self.assertEqual(allowed("class"), "`a`, `b`")
        self.assertEqual(allowed("clock_hz"), "> 0")
        self.assertEqual(allowed("ratio"), "0 to 1, finite")
        self.assertEqual(allowed("name"), "at least 1 character, pattern `^[a-z]+$`")
        self.assertEqual(allowed("tags"), "item: at least 1 character, pattern `^[a-z]+$`")
        self.assertEqual(allowed("bounds"), "exactly 2 items")
        self.assertEqual(allowed("pins"), "at least 1 item")
        self.assertEqual(allowed("int32"), "-2^31 to 2^31-1")
        self.assertEqual(allowed("label"), "1 to 24 characters")
        self.assertEqual(allowed("mode"), "`x`; item: at least 1 character, pattern `^[a-z]+$`")

    def test_type_text(self):
        def text(path):
            return render.type_text(by_path(self.sections, path).type)

        self.assertEqual(text("gain"), "integer")
        self.assertEqual(text("ratio"), "number")
        self.assertEqual(text("class"), "string")
        self.assertEqual(text("tags"), "list of string")
        self.assertEqual(text("limits"), "map of string to integer")
        self.assertEqual(text("pin"), "[object](#f-pin)")
        self.assertEqual(text("mode"), "string or list of string")
        self.assertEqual(text("sensors"), "map of string to one of [object (kind: wire)](#f-sensors-key-kind-wire) or "
                                          "[object (kind: coil)](#f-sensors-key-kind-coil), selected by `kind`")


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.pages = reference.build_pages([page()])
        self.text = self.pages["test.md"]

    def test_files(self):
        self.assertEqual(sorted(self.pages), ["README.md", "test.md"])
        for text in self.pages.values():
            self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))
            self.assertNotIn("\r", text)

    def test_gaps_are_visible(self):
        self.assertIn(f"| `undocumented` | string |  | no | `null` |  | {render.MISSING_DESCRIPTION} |", self.text)
        self.assertIn(f"| `gain` | integer | {render.MISSING_UNIT} | no | `1` |  | comment block above the field, "
                      "two lines |", self.text)

    def test_markdown_safety(self):
        self.assertIn("tags \\| with a pipe", self.text)                 # pipe does not split the table
        self.assertIn("pin name, e.g. &lt;port>_&lt;n>", self.text)       # not taken for an HTML tag
        self.assertIn("\n- first item continued\n- second item\n", self.text)   # docstring list kept
        for line in self.text.splitlines():
            if line.startswith("| `"):
                self.assertEqual(line.replace("\\|", "").count("|"), len(render.HEADER) + 1, line)

    def test_every_field_has_a_row(self):
        sections = schema.document(Top)
        for s in sections:
            for f in s.fields:
                self.assertIn(f"| `{f.path}` |", self.text)
        self.assertIn('<a id="f-sensors-key-pin-kind-coil"></a>\n\n### `sensors.<key>.pin` (kind: coil)\n', self.text)

    def test_deterministic(self):
        again = reference.build_pages([page()])
        self.assertEqual(again, self.pages)
        real1, real2 = reference.build_pages(), reference.build_pages()
        self.assertEqual(real1, real2)

    def test_core_without_qt(self):
        self.assertFalse([m for m in sys.modules if m.startswith(("PySide", "PyQt"))])

    def test_readme_counts(self):
        readme = self.pages["README.md"]
        fields = [f for s in schema.document(Top) for f in s.fields]
        n, nd, nu = reference.stats(fields)
        self.assertIn(f"| [Test description](test.md) | Test | `test/<x>/top.yaml` | {n} | 7 |", readme)
        self.assertEqual((nd, nu), (sum(f.description is None for f in fields),
                                    sum(f.numeric and f.unit is None for f in fields)))


class Leg(Base):
    """One leg."""

    length_mm: int = Field(1, description="length")
    width_mm: int = Field(1, description="width")
    label: str = Field("", description="label")
    note: str = Field("", description="note")


class Animal(Base):
    front: Leg = Field(default_factory=Leg, description="front leg")
    back: Leg = Field(default_factory=Leg, description="back leg")
    wing: Leg = Field(default_factory=Leg, description="power stage wing")


class ShortFormTest(unittest.TestCase):
    """DOC-76: identical tables become a pointer, objects after R1 a short entry; anchors stay."""

    def setUp(self):
        self.sections = schema.document(Animal)

    def test_identical_table_is_a_pointer(self):
        plan = {s.path: (kind, first) for kind, s, first, _ in reference.layout(self.sections, "x.md")}
        self.assertEqual(plan["front"][0], "full")
        self.assertEqual(plan["back"], ("same", self.sections[1]))

    def test_after_r1_entry_keeps_the_anchor(self):
        self.assertTrue(reference.is_after_r1("board.md", "pwm[].power_stage.gate"))
        self.assertFalse(reference.is_after_r1("board.md", "pwm_other"))
        self.assertFalse(reference.is_after_r1("board.md", "input_capture[]"))
        stub = "\n".join(render.stub_after_r1(self.sections[3], []))
        self.assertIn(f'<a id="{self.sections[3].anchor}"></a>', stub)
        self.assertIn("(after R1)", stub)
        self.assertIn("`length_mm`", stub)


class CheckTest(unittest.TestCase):
    def test_check_detects_every_kind_of_drift(self):
        generated = reference.build_pages([page()])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertEqual(reference.check(out, generated), ["README.md: missing", "test.md: missing"])
            self.assertEqual(sorted(reference.write(out, generated)), ["README.md", "test.md"])
            self.assertEqual(reference.check(out, generated), [])
            self.assertEqual(reference.write(out, generated), [])           # nothing rewritten
            # CRLF from a Windows checkout (core.autocrlf) is not a difference
            (out / "test.md").write_bytes(generated["test.md"].replace("\n", "\r\n").encode("utf-8"))
            self.assertEqual(reference.check(out, generated), [])
            # hand edit of a generated page
            (out / "test.md").write_text(generated["test.md"].replace("Intro text.", "Edited."), encoding="utf-8")
            report = reference.check(out, generated)
            self.assertEqual(report[0], "test.md: differs from its sources")
            self.assertIn("    -Edited.", report)
            self.assertIn("    +Intro text.", report)

    def test_schema_change_is_detected(self):
        class Grown(Top):
            new_field_ms: int = 5                     # a field added to the schema

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            reference.write(out, reference.build_pages([page(Top)]))
            report = reference.check(out, reference.build_pages([page(Grown)]))
            self.assertIn("test.md: differs from its sources", report)
            self.assertTrue(any("new_field_ms" in line and line.startswith("    +") for line in report), report)

    @unittest.skipUnless(HAS_OPENBSW, "external/openbsw not checked out (console page)")
    def test_cli_check_exit_code_and_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(cli_main(["--check", "--out", tmp]), 1)
            self.assertIn("README.md: missing", stdout.getvalue())
            self.assertIn(f"Regenerate it with:  {reference.REGENERATE}", stderr.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli_main(["--out", tmp]), 0)
                self.assertEqual(cli_main(["--check", "--out", tmp]), 0)

    def test_gaps_list_each_schema_field_once(self):
        desc, unit = reference.gaps([page()])
        owners = [o for o, _ in desc]
        self.assertEqual(len(owners), len(set(owners)))
        self.assertIn(("Top.undocumented", "test.md: undocumented"), desc)
        self.assertIn(("Facts.verify", "test.md: sensors.<key>.verify"), desc)   # once, though in two variants
        self.assertIn(("Top.gain", "test.md: gain"), unit)
        self.assertNotIn("Top.clock_hz", [o for o, _ in unit])


@unittest.skipUnless(HAS_OPENBSW, "external/openbsw not checked out (console page)")
class RealSchemaTest(unittest.TestCase):
    def test_checked_in_reference_matches_schema(self):
        report = reference.check(reference.OUT_DIR, reference.build_pages())
        self.assertEqual(report, [], "doc/reference is stale - regenerate with "
                                     f"{reference.REGENERATE} and commit:\n" + "\n".join(report))

    def test_real_pages(self):
        pages = reference.build_pages()
        self.assertEqual(sorted(pages), ["README.md", "board.md", "console.md", "derivative.md", "diag.md",
                                         "modules.md", "project.md", "rte.md"])
        self.assertIn("`clock.system_hz` | integer | Hz | yes", pages["project.md"])
        # ARCH-34: the ECU description has its schema (tools/nxconfig/model.py EcuDescription)
        self.assertNotIn("No schema model exists for this file yet", pages["rte.md"])
        self.assertIn("Schema: `EcuDescription` in", pages["rte.md"])
        self.assertIn("| `tasks[].period_ms` | integer | ms | yes |", pages["rte.md"])
        self.assertIn("| `can.qualifiers.<key>.on_error` | string |  | no | `\"hold\"` | `hold`, `init` |",
                      pages["rte.md"])
        rte = next(p for p in reference.build() if p.file == "rte.md")
        self.assertEqual([f.path for f in rte.fields() if f.missing_description], [])
        self.assertEqual({f.description_source for f in rte.fields()}, {"field"})   # Field(description=...)


# ----------------------------------------------------------------------------------------------- module catalogue

DRIVER = """\
module: chipDrv
kind: device_driver
description: >
  Test chip driver; second clause
parts: [CHIP1*, CHIP2]
requires: { spi_device: 1 }
connect:
  en:    { kind: digital_output, safe_off: true, description: "enable | pin" }
  sense: { kind: analog_input }
parameters:
  cycle_ms: { type: int, min: 1, max: 100, default: 10, unit: ms, description: "period <n>" }
  limit_mv: { type: int, min: 0, max: 5000, unit: mV }
constraints: [limit_mv > cycle_ms]
provides: [can_phy, thing]
lifecycle: { level: 1, task: bsp }
cmake_target: chipDrv
console: instance
"""

SERVICE = """\
module: svc
kind: service
description: A service
project_parameters: true
parameters:
  buses: { type: str, pattern: "all|none", names: can_bus, default: all, description: buses }
verification: mock
"""

PARKED = """\
module: parked
kind: device_driver
description: Parked driver
parts: []
requires: { spi_device: 1 }
connect:
  reset: { kind: digital_output, optional: true }
"""

TESTS = """\
tests:
  - name: chip
    group: host
    covers: [libs/bsw/chipDrv/test/host/ChipHostTest.cpp, tools/x/test_x.py]
  - name: shared
    group: python
    covers:
      - libs/bsw/chipDrv/test/host/SharedHostTest.cpp
      - libs/bsp/contract/test/host/ContractHostTest.cpp
  - name: other
    group: host
    covers: [ecu/other/OtherHostTest.cpp]
"""


def make_libs(root: Path) -> Path:
    """A libs/ tree with every case: driver, service with project parameters and verification, parked manifest,
    module without manifest but with documentation, contract with nothing but a header."""
    files = {
        "libs/bsw/chipDrv/module.yaml": DRIVER,
        "libs/bsw/chipDrv/doc/chipDrv.md": "# chip\n",
        "libs/bsw/chipDrv/doc/aaa.md": "# other page\n",
        "libs/bsw/chipDrv/include/chip/Chip.h": "",
        "libs/bsw/chipDrv/include/chip/IChipIo.h": "",
        "libs/bsw/svc/module.yaml": SERVICE,
        "libs/bsw/parked/module.yaml.pending": PARKED,
        "libs/bsw/Plain/doc/notes.md": "# notes\n",
        "libs/bsw/Plain/CMakeLists.txt": "",
        "libs/bsp/contract/include/c/IContract.h": "",
        "tools/test/tests.yaml": TESTS,
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class ModulesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = make_libs(Path(self._tmp.name))
        self.mods = modules.collect(root / "libs", root / "tools" / "test" / "tests.yaml")
        self.by = {m.name: m for m in self.mods}
        self.text = modules.render_catalogue(self.mods)

    def tearDown(self):
        self._tmp.cleanup()

    def test_every_directory_once_in_layer_order(self):
        self.assertEqual([(m.layer, m.name) for m in self.mods],
                         [("bsw", "chipDrv"), ("bsw", "parked"), ("bsw", "Plain"), ("bsw", "svc"),
                          ("bsp", "contract")])
        for m in self.mods:
            self.assertEqual(self.text.count(f'<a id="{m.anchor}"></a>'), 1)
            self.assertIn(f"](#{m.anchor})", self.text)

    def test_manifest_doc_headers(self):
        chip, parked, plain = self.by["chipDrv"], self.by["parked"], self.by["Plain"]
        self.assertEqual((chip.manifest_file, parked.manifest_file, plain.manifest_file),
                         ("module.yaml", "module.yaml.pending", None))
        self.assertTrue(parked.pending)
        self.assertEqual(chip.doc, "libs/bsw/chipDrv/doc/chipDrv.md")       # <module>.md before the first file
        self.assertEqual(plain.doc, "libs/bsw/Plain/doc/notes.md")
        self.assertIsNone(self.by["svc"].doc)
        self.assertEqual(chip.headers, ["chip/Chip.h", "chip/IChipIo.h"])
        self.assertIn("[chipDrv.md](../../libs/bsw/chipDrv/doc/chipDrv.md)", self.text)

    def test_host_tests_from_covers(self):
        chip = self.by["chipDrv"]
        self.assertEqual([(t.name, t.group, t.files) for t in chip.tests],
                         [("chip", "host", ["test/host/ChipHostTest.cpp"]),
                          ("shared", "python", ["test/host/SharedHostTest.cpp"])])
        self.assertEqual([t.name for t in self.by["contract"].tests], ["shared"])
        self.assertEqual(self.by["svc"].tests, [])
        self.assertRegex(self.text, r"\| \[chipDrv\][^\n]*`chip`, `shared`")      # DOC-76: names in the table only
        self.assertNotIn("`other`", self.text)

    def test_configuration_keys(self):
        board = self.by["chipDrv"].board_keys()
        self.assertTrue(board[0].startswith("`devices[].part` matching `CHIP1*`, `CHIP2`"))
        self.assertIn("`devices[].connect.en` -> an entry of `dio.outputs[]` (digital_output, switched off first by "
                      "the fault handlers): enable | pin", board)
        self.assertIn("`devices[].connect.sense` -> an entry of `adc[]` (analog_input)", board)
        self.assertTrue(any(k.startswith("`spi[].device`") for k in board))
        self.assertTrue(any(k.startswith("`can[].phy`") for k in board))
        project = self.by["chipDrv"].project_keys()
        self.assertTrue(project[0].startswith("`modules.<device>.<parameter>`"))
        self.assertTrue(any(k.startswith("`use.spi`") for k in project))
        self.assertEqual(self.by["svc"].board_keys(), [])
        self.assertTrue(self.by["svc"].project_keys()[0].startswith("`modules.svc.<parameter>`"))
        self.assertTrue(any(k.startswith("`use.can`") for k in self.by["svc"].project_keys()))
        parked = self.by["parked"].board_keys()
        self.assertIn("no board device can use the module yet", parked[0])
        self.assertIn("(digital_output, optional)", parked[1])
        self.assertIn("- **Configuration:** not derivable (no manifest)", self.text)

    def test_parameters_and_rules(self):
        self.assertIn("| `cycle_ms` | int | ms | `10` | 1 .. 100 | period &lt;n> |", self.text)
        self.assertIn("| `limit_mv` | int | mV | required | 0 .. 5000 | _missing in manifest_ |", self.text)
        self.assertIn("| `buses` | str |  | `all` | pattern `all\\|none`, names: can_bus | buses |", self.text)
        self.assertIn("Rules between the parameters (checked by `tools/nxconfig`): `limit_mv > cycle_ms`.", self.text)

    def test_purpose_and_verification_only_from_the_manifest(self):
        self.assertIn("| [chipDrv](#m-chipdrv) | libs/bsw | `device_driver` | Test chip driver | `module.yaml` | "
                      "`chip`, `shared` | _not stated_ |", self.text)
        self.assertIn("| [svc](#m-svc) | libs/bsw | `service` | A service | `module.yaml` | none | mock | none |",
                      self.text)
        self.assertIn("| [Plain](#m-plain) | libs/bsw | _not stated_ | _not stated_ | none | none | _not stated_ |",
                      self.text)
        self.assertIn("Purpose: _not stated_ (no manifest).", self.text)
        self.assertIn("The manifest is parked as `module.yaml.pending`", self.text)
        self.assertEqual(self.by["svc"].verification, "mock")
        self.assertIsNone(self.by["chipDrv"].verification)

    def test_gaps(self):
        gaps = modules.gaps(self.mods)
        self.assertIn(("libs/bsw/Plain", "no module.yaml (purpose, kind, configuration unknown)"), gaps)
        self.assertIn(("libs/bsw/parked", "manifest parked as module.yaml.pending"), gaps)
        self.assertIn(("libs/bsw/chipDrv", "verification not stated (manifest field verification)"), gaps)
        self.assertNotIn("libs/bsw/svc", [p for p, what in gaps if what.startswith("verification")])
        self.assertIn(("libs/bsp/contract", "no documentation under doc/"), gaps)
        self.assertIn(("libs/bsw/svc", "no host test in tools/test/tests.yaml"), gaps)

    def test_markdown_and_determinism(self):
        self.assertEqual(modules.render_catalogue(self.mods), self.text)
        header = next(line for line in self.text.splitlines() if line.startswith("| Module |"))
        for line in self.text.splitlines():
            if line.startswith("| ["):
                self.assertEqual(line.replace("\\|", "").count("|"), header.count("|"), line)
        self.assertNotIn("\r", self.text)

    def test_catalogue_in_pages_and_check(self):
        generated = reference.build_pages([page()], self.mods)
        self.assertEqual(sorted(generated), ["README.md", "modules.md", "test.md"])
        self.assertIn("[Modules](modules.md) - the catalogue of the 4 modules in `libs/bsw` (device drivers, "
                      "services, libraries) and the 1 in `libs/bsp` (contracts, MCU drivers)", generated["README.md"])
        self.assertIn("2 of the 5 modules have no manifest, the others have one, 1 of them parked "
                      "(`module.yaml.pending`). Verification stated: `mock` 1; not stated: 4.", generated["README.md"])
        self.assertNotIn("in the ports", generated["README.md"])        # no port in this tree
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            reference.write(out, generated)
            self.assertEqual(reference.check(out, generated), [])
            # a manifest change makes the checked-in catalogue stale
            self.by["svc"].manifest["description"] = "Changed service"
            report = reference.check(out, reference.build_pages([page()], self.mods))
            self.assertIn("modules.md: differs from its sources", report)
            self.assertTrue(any(line.startswith("    +") and "Changed service" in line for line in report), report)


FAMILY_PORT_MODULE = """\
module: famCapture
kind: mcu_driver
description: Capture of the family
verification: hardware
verification_note: >
  Confirmed on a board;
  the error path <x> is covered by host tests only.
"""

BOARD_PORT_MODULE = """\
module: famCapture
kind: service
description: Board peculiarity with the same name
"""

PORT_TESTS = TESTS + """\
  - name: port-host
    group: host
    covers:
      - platforms/fam1/port/src/bsp/famCapture/test/CaptureHostTest.cpp
      - platforms/fam1/port/src/bsp/famCaptureX/test/NotThisModule.cpp
"""


def make_ports(root: Path) -> Path:
    """make_libs plus a family port and a board port, both with a module `famCapture`, and a port directory
    without manifest."""
    make_libs(root)
    files = {
        "platforms/fam1/port/src/bsp/famCapture/module.yaml": FAMILY_PORT_MODULE,
        "platforms/fam1/port/src/bsp/famCapture/doc/famCapture.md": "# capture\n",
        "platforms/fam1/port/src/bsp/noManifest/Driver.cpp": "",
        "platforms/fam1/bsp/notAPort/module.yaml": FAMILY_PORT_MODULE,        # outside platforms/<family>/port
        "ecu/platforms/boardB/special/famCapture/module.yaml": BOARD_PORT_MODULE,
        "tools/test/tests.yaml": PORT_TESTS,
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class VerificationAndGapsTest(unittest.TestCase):
    """DOC-16: verification_note, the data-driven introduction, the section Gaps and the port modules."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_ports(Path(self._tmp.name))
        self.mods = modules.collect(self.root / "libs", self.root / "tools" / "test" / "tests.yaml")
        self.by = {m.path: m for m in self.mods}
        self.text = modules.render_catalogue(self.mods)

    def tearDown(self):
        self._tmp.cleanup()

    def test_port_modules_collected_after_libs(self):
        self.assertEqual([(m.layer, m.path) for m in self.mods[5:]],
                         [("port", "platforms/fam1/port/src/bsp/famCapture"),
                          ("port", "ecu/platforms/boardB/special/famCapture")])
        self.assertEqual([m.layer for m in self.mods[:5]], ["bsw"] * 4 + ["bsp"])
        fam, board = self.mods[5], self.mods[6]
        self.assertEqual((fam.root, fam.port_kind, fam.anchor), ("platforms/fam1/port", "family port",
                                                                 "m-fam1-famcapture"))
        self.assertEqual((board.root, board.port_kind, board.anchor), ("ecu/platforms/boardB", "board port",
                                                                       "m-boardb-famcapture"))
        self.assertEqual(fam.doc, "platforms/fam1/port/src/bsp/famCapture/doc/famCapture.md")
        # host tests only from files in the module directory, not from a directory with the same prefix
        self.assertEqual([(t.name, t.files) for t in fam.tests], [("port-host", ["test/CaptureHostTest.cpp"])])
        self.assertEqual(board.tests, [])
        for m in self.mods:
            self.assertEqual(self.text.count(f'<a id="{m.anchor}"></a>'), 1, m.anchor)
        self.assertIn("## Port modules (`platforms/<family>/port`, `ecu/platforms/<board>`)", self.text)
        self.assertIn("| [famCapture](#m-fam1-famcapture) | platforms/fam1/port | `mcu_driver` |", self.text)
        self.assertIn("- **Port:** family port [`platforms/fam1/port`](../../platforms/fam1/port) - loaded by "
                      "`tools/nxconfig` for the projects whose derivative belongs to this family", self.text)
        self.assertIn("- **Port:** board port [`ecu/platforms/boardB`](../../ecu/platforms/boardB)", self.text)
        self.assertNotIn("notAPort", self.text)
        self.assertNotIn("noManifest", self.text)
        # without ports: only libs, as before
        self.assertEqual(len(modules.collect(self.root / "libs", self.root / "tools" / "test" / "tests.yaml",
                                             ports=False)), 5)

    def test_verification_note_in_section_and_table(self):
        fam = self.by["platforms/fam1/port/src/bsp/famCapture"]
        self.assertEqual(fam.verification_note, "Confirmed on a board; the error path <x> is covered by host tests "
                                                "only.")
        self.assertIn("- **Verification:** `hardware` - Confirmed on a board; the error path &lt;x> is covered by "
                      "host tests only.", self.text)
        self.assertIn("| hardware ([note](#m-fam1-famcapture)) |", self.text)
        self.assertIn("- **Verification:** `mock`\n", self.text)                      # svc: level, no note
        self.assertIn("| [svc](#m-svc) | libs/bsw | `service` | A service | `module.yaml` | none | mock | none |",
                      self.text)
        self.assertIn("- **Verification:** _not stated_\n", self.text)

    def test_introduction_follows_the_data(self):
        self.assertIn("2 of the 7 modules have no manifest, the others have one, 1 of them parked "
                      "(`module.yaml.pending`). Verification stated: `hardware` 1, `mock` 1; not stated: 5. What is "
                      "missing per module: [Gaps](#gaps).", self.text)
        self.assertNotIn("No manifest states it yet", self.text)
        readme = reference.build_pages([page()], self.mods)["README.md"]
        self.assertIn("and the 1 in `libs/bsp` (contracts, MCU drivers), plus the 2 with a manifest in the ports",
                      readme)
        self.assertIn(modules.summary(self.mods), readme)
        # other data, other sentences - nothing is a fixed text
        stated = [m for m in self.mods if m.manifest is not None]
        for m in stated:
            m.manifest["verification"] = "mock"
        self.assertEqual(modules.summary(stated), "All 5 modules have a manifest, 1 of them parked "
                                                  "(`module.yaml.pending`). Verification stated: `mock` 5; every "
                                                  "module states it.")
        for m in stated:
            m.manifest.pop("verification")
        self.assertTrue(modules.summary(stated).endswith("No manifest states its verification yet."))
        self.assertEqual(modules.summary(stated[:1]).split(".")[0], "The module has a manifest")

    def test_gaps_section_generated_from_gaps(self):
        section = self.text[self.text.index("## Gaps"):]
        self.assertIn('<a id="gaps"></a>', self.text)
        self.assertIn("- **No manifest (purpose, kind and configuration unknown)** (2): [Plain](#m-plain), "
                      "[contract](#m-contract)", section)
        self.assertIn("- **No module documentation under `doc/`** (4): [parked](#m-parked), [svc](#m-svc), "
                      "[contract](#m-contract), [famCapture](#m-boardb-famcapture)", section)
        # every entry of gaps() is in the section, linked to its module
        for path, what in modules.gaps(self.mods):
            mod = self.by[path]
            line = next(x for x in section.splitlines() if x.startswith(f"- **{modules.GAP_TITLES[what]}**"))
            self.assertIn(f"[{mod.name}](#{mod.anchor})", line)
        # a module whose gap is closed disappears from the section
        self.by["libs/bsw/svc"].doc = "libs/bsw/svc/doc/svc.md"
        doc_line = next(x for x in modules.render_gaps(self.mods) if x.startswith("- **No module documentation"))
        self.assertTrue(doc_line.startswith("- **No module documentation under `doc/`** (3): "), doc_line)
        self.assertNotIn("[svc](#m-svc)", doc_line)
        self.assertEqual(modules.render_gaps([])[-2:], ["None.", ""])


NAMED_TESTS = PORT_TESTS + """\
  - name: gen
    group: python
    covers: [tools/gen/test_gen.py, tools/gen/test/svc_host/svc_host_test.cpp]
    modules: [svc, ecu/platforms/boardB/special/famCapture/]
  - name: both
    group: host
    covers: [libs/bsw/chipDrv/test/host/BothHostTest.cpp]
    modules: [chipDrv]
  - name: prefix-only
    group: host
    covers: [tools/gen/test/other_host_test.cpp]
    modules: [sv, famCaptureX]
"""


class NamedModulesTest(unittest.TestCase):
    """DOC-18: an entry of tools/test/tests.yaml names a module under `modules` although its test files lie outside
    the module directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_ports(Path(self._tmp.name))
        (self.root / "tools/test/tests.yaml").write_text(NAMED_TESTS, encoding="utf-8")
        self.mods = modules.collect(self.root / "libs", self.root / "tools" / "test" / "tests.yaml")
        self.by = {m.path: m for m in self.mods}
        self.text = modules.render_catalogue(self.mods)

    def tearDown(self):
        self._tmp.cleanup()

    def host_tests(self, path):
        return [(t.name, t.files, t.named) for t in self.by[path].tests]

    def test_named_module_gets_the_entry(self):
        self.assertEqual(self.host_tests("libs/bsw/svc"), [("gen", [], True)])
        self.assertIn("| [svc](#m-svc) | libs/bsw | `service` | A service | `module.yaml` | `gen` | mock | none |",
                      self.text)

    def test_directory_form_picks_one_of_two_modules_with_the_same_name(self):
        self.assertEqual(self.host_tests("ecu/platforms/boardB/special/famCapture"), [("gen", [], True)])
        self.assertEqual(self.host_tests("platforms/fam1/port/src/bsp/famCapture"),
                         [("port-host", ["test/CaptureHostTest.cpp"], False)])

    def test_covers_and_name_together(self):
        self.assertEqual(self.host_tests("libs/bsw/chipDrv"),
                         [("chip", ["test/host/ChipHostTest.cpp"], False),
                          ("shared", ["test/host/SharedHostTest.cpp"], False),
                          ("both", ["test/host/BothHostTest.cpp"], True)])
        self.assertRegex(self.text, r"\| \[chipDrv\][^\n]*`chip`, `shared`, `both`")

    def test_only_exact_names_count(self):
        self.assertNotIn("`prefix-only`", self.text)                 # `sv`, `famCaptureX` name no module here
        self.assertEqual(self.host_tests("libs/bsw/parked"), [])

    def test_gap_closed_by_the_name(self):
        before = modules.collect(self.root / "libs", self.root / "tools" / "test" / "tests.yaml.missing")
        missing = {p for p, what in modules.gaps(before) if what == "no host test in tools/test/tests.yaml"}
        self.assertIn("libs/bsw/svc", missing)
        after = {p for p, what in modules.gaps(self.mods) if what == "no host test in tools/test/tests.yaml"}
        self.assertEqual(after, {"libs/bsw/parked", "libs/bsw/Plain"})
        self.assertIn("- **No host test in `tools/test/tests.yaml` (no covered file in the module directory, module "
                      "not named under `modules`)** (2): [parked](#m-parked), [Plain](#m-plain)", self.text)
        self.assertIn("or name the module under `modules`", self.text)


@unittest.skipUnless(HAS_OPENBSW, "external/openbsw not checked out (console page)")
class RealModulesTest(unittest.TestCase):
    def test_real_catalogue(self):
        mods = modules.collect()
        names = {(m.layer, m.name) for m in mods if m.layer in modules.LAYERS}
        dirs = {(layer, d.name) for layer in modules.LAYERS for d in (modules.LIBS / layer).iterdir() if d.is_dir()}
        self.assertEqual(names, dirs)                    # every directory of libs/bsw and libs/bsp, nothing else
        # every port manifest nxconfig can read (tools/nxconfig/config.py port_module_dirs), nothing else
        ports = {m.path for m in mods if m.layer == modules.PORT}
        manifests = {p.parent.relative_to(REPO).as_posix() for pattern in ("platforms/*/port/**/module.yaml",
                                                                          "ecu/platforms/*/**/module.yaml")
                     for p in REPO.glob(pattern)}
        self.assertEqual(ports, manifests)
        self.assertIn("platforms/s32k1xx/port/safety/safeSystem", ports)
        # DOC-14/DOC-16: every manifest states its verification, `hardware` always with a note
        for mod in mods:
            self.assertIn(mod.verification, ("none", "mock", "hardware"), mod.path)
            if mod.verification == "hardware":
                self.assertTrue(mod.verification_note, mod.path)
        text = reference.build_pages()["modules.md"]
        for mod in mods:
            self.assertIn(f'<a id="{mod.anchor}"></a>', text)
            if mod.doc:
                self.assertTrue((REPO / mod.doc).is_file())
        # every link into the repository points to an existing file or directory
        for target in re.findall(r"\]\((\.\./\.\./[^)#]+)\)", text):
            self.assertTrue((reference.OUT_DIR / target).resolve().exists(), target)
        self.assertIn("| [ecuState](#m-ecustate) | libs/bsw | `service` |", text)
        self.assertIn("`modules.ecuState.<parameter>` - one entry per project", text)
        self.assertRegex(text, r"\| \[sbcUja113x\][^\n]*`uja113x`")
        # DOC-18: tools/test checks `modules` against the same manifests the catalogue shows
        with_manifest = {m.path for m in mods if m.manifest is not None}
        self.assertEqual({p for ps in run_tests.manifest_modules().values() for p in ps}, with_manifest)
        by = {m.name: m for m in mods}
        for name in ("capture", "hall"):
            self.assertIn("bswgen", [t.name for t in by[name].tests if t.named], name)
        self.assertIn("[Modules](modules.md)", reference.build_pages()["README.md"])


class ModuleMapTest(unittest.TestCase):
    """DOC-64: the module map doc/figures/07-modulkarte.md, generated from the module list of modules.md."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_ports(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def collect(self) -> list[modules.ModuleDoc]:
        return modules.collect(self.root / "libs", self.root / "tools" / "test" / "tests.yaml")

    def test_every_module_once_with_its_verification(self):
        mods = self.collect()
        text = modulemap.render(mods)
        block = text.split("```mermaid\n", 1)[1].split("\n```", 1)[0]
        classes = {}
        for line in block.splitlines():
            m = re.match(r"^\s*class\s+([\w,]+)\s+(\w+)$", line)
            if m and m.group(2) != "grp":
                classes.update({node: m.group(2) for node in m.group(1).split(",")})
        # the same module name in a family port and a board port: two nodes, two rows
        self.assertEqual(classes, {
            "m_svc": "part", "m_fam1_famcapture": "ok"})
        for mod in mods:
            self.assertEqual(text.count(f"](../reference/modules.md#{mod.anchor})"), 1, mod.anchor)
        self.assertIn('m_parked("parked (pending)")', block)
        self.assertIn('subgraph G3["platforms/fam1/port"]', block)
        self.assertIn('subgraph G4["ecu/platforms/boardB"]', block)
        self.assertIn("| [svc](../reference/modules.md#m-svc) | libs/bsw | `service` | 🟨 Mock (`mock`) |", text)
        self.assertIn("| [Plain](../reference/modules.md#m-plain) | libs/bsw | – | ❔ nicht angegeben |", text)
        self.assertIn("- Stand: 1 × `hardware`, 1 × `mock`, 5 × nicht angegeben, 7 Module.", text)
        self.assertIn("ohne Statusfarbe", text)
        self.assertNotIn("classDef no ", block)                      # only the levels that occur
        self.assertEqual(text, modulemap.render(self.collect()))      # deterministic

    def test_changed_verification_makes_the_check_fail(self):
        """Counter-test: a manifest changes its verification, the checked-in map is stale."""
        out = self.root / "figures"
        reference.write(out, {modulemap.FILE: modulemap.render(self.collect())})
        self.assertEqual(reference.check(out, {modulemap.FILE: modulemap.render(self.collect())}), [])
        manifest = self.root / "platforms/fam1/port/src/bsp/famCapture/module.yaml"
        manifest.write_text(FAMILY_PORT_MODULE.replace("verification: hardware", "verification: mock"),
                            encoding="utf-8")
        report = reference.check(out, {modulemap.FILE: modulemap.render(self.collect())})
        self.assertEqual(report[0], f"{modulemap.FILE}: differs from its sources")
        self.assertIn("    +    class m_fam1_famcapture part", report)
        self.assertIn("    -    class m_fam1_famcapture ok", report)

    @unittest.skipUnless(HAS_OPENBSW, "external/openbsw not checked out (console page)")
    def test_cli_checks_and_writes_the_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, fig = Path(tmp) / "ref", Path(tmp) / "fig"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli_main(["--out", str(out), "--figures", str(fig)]), 0)
            self.assertTrue((fig / modulemap.FILE).is_file())
            (fig / modulemap.FILE).write_text("# by hand\n", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli_main(["--check", "--out", str(out), "--figures", str(fig)]), 1)
            self.assertIn(f"{modulemap.FILE}: differs from its sources", stdout.getvalue())

    def test_checked_in_map_matches_the_manifests(self):
        current = (modulemap.OUT_DIR / modulemap.FILE).read_bytes().decode("utf-8").replace("\r\n", "\n")
        expected = modulemap.render(modules.collect()).rstrip("\n") + "\n"
        self.assertEqual(current, expected, "doc/figures/07-modulkarte.md is stale - regenerate with "
                                            f"{reference.REGENERATE} and commit")


if __name__ == "__main__":
    unittest.main()
