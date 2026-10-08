"""Tests of tools/a2lgen (TOOL-01).

The images are real 32 bit ELF files, little and big endian: the RTE of the RTE-03 test profile
(tools/rtegen/test/param, component CalSwc: scalar, array, fixed point with slope and with bias, float32, boolean)
is generated with tools/rtegen and cross-compiled with zig for arm-linux-musleabi / armeb-linux-musleabi. So the
tables a2lgen reads have exactly the layout the generator produces; the addresses in the A2L are compared with the
symbols of the storage in the same image.

RTE-26: CalibrationA2lTest builds the calibration profile tools/rtegen/test/calib the same way, with the EPK of
ecu/xcp (XcpEpk.cpp, header from epk.py), and checks CURVE/MAP/AXIS_PTS, record layouts, limits, value tables,
FUNCTION and EPK - formally with a2lcheck.py and by reading the values back from the image.

Run: python tools/a2lgen/test_a2lgen.py   (needs ziglang from the .venv)
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
# PLAT-62: the own full test profile (demo component, demo CAN matrix) instead of the private eval board
# project (PLAT-46 F10), which is missing in the exported open tree
FULL_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "rtegen"))

import a2lcheck  # noqa: E402  (RTE-26: formal check of the A2L)
import a2lgen  # noqa: E402
from elf32 import Elf32  # noqa: E402
from xcp_master import A2l  # noqa: E402

PARAM = REPO / "tools" / "rtegen" / "test" / "param"
TARGETS = {"le": "arm-linux-musleabi", "be": "armeb-linux-musleabi"}
XCP_HEADER = """#pragma once
#define NX_XCP_BUS           CAN0
#define NX_XCP_BUS_NAME      "CAN0"
#define NX_XCP_CAN_ID_MASTER {master}UL
#define NX_XCP_CAN_ID_SLAVE  {slave}UL
"""
BIT_TIMING = "constexpr uint32_t const CAN0_BITRATE = 500000U;\nconstexpr uint32_t const CAN1_BITRATE = 250000U;\n"


def zig(*args: str) -> None:
    r = subprocess.run([sys.executable, "-m", "ziglang", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"zig {args[0]}: {r.stderr[-3000:]}")


class A2lGenTest(unittest.TestCase):
    tmp: Path
    images: dict[str, Path]

    @classmethod
    def setUpClass(cls):
        import rtegen
        cls.tmp = Path(tempfile.mkdtemp(prefix="a2lgen_"))
        gen = cls.tmp / "gen"
        project = rtegen.nxconfig.load(FULL_PROJECT)
        rtegen.generate(PARAM / "rte.yaml", project, gen, REPO)
        cls.rte_config = gen / "RteConfig.h"
        (cls.tmp / "start.c").write_text("void _start(void) { for (;;) {} }\n")
        includes = [f"-I{gen}", f"-I{gen / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                    f"-I{REPO / 'tools/rtegen/test/can_host/stubs'}", f"-I{PARAM / 'CalSwc/generated/src'}"]
        cls.images = {}
        for key, target in TARGETS.items():
            objects = []
            for src in (gen / "Rte.c", PARAM / "CalSwc/generated/src/CalSwc.c", cls.tmp / "start.c",
                        gen / "RteConfig.cpp"):
                obj = cls.tmp / f"{src.stem}.{key}.o"
                lang = ["c++", "-std=c++17", "-fno-exceptions", "-fno-rtti"] if src.suffix == ".cpp" \
                    else ["cc", "-std=c99"]
                zig(*lang, "-target", target, "-O1", "-ffreestanding", *includes, "-c", str(src), "-o", str(obj))
                objects.append(str(obj))
            elf = cls.tmp / f"app.{key}.elf"
            # the stubs of the BSW calls stay unresolved: only the data of the image matters here
            zig("ld.lld", "--unresolved-symbols=ignore-all", "-e", "_start", *objects, "-o", str(elf))
            cls.images[key] = elf
        cls.xcp_config = cls.write(cls.tmp / "xcpCanConfiguration.h", XCP_HEADER.format(master="0x7F0", slave="0x7F1"))
        cls.bit_timing = cls.write(cls.tmp / "canBitTimingConfiguration.h", BIT_TIMING)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def write(path: Path, text: str) -> Path:
        path.write_text(text, encoding="utf-8")
        return path

    def generate(self, key: str = "le", **kwargs) -> str:
        out = self.tmp / f"out.{key}.a2l"
        args = dict(elf=self.images[key], output=out, rte_config=self.rte_config, xcp_config=self.xcp_config,
                    bit_timing=self.bit_timing, name="calswc_test")
        args.update(kwargs)
        a2lgen.generate(**args)
        return out.read_text(encoding="utf-8")

    def check_image(self, key: str, little: bool):
        text = self.generate(key)
        a2l = A2l(text)
        self.assertEqual(a2l.little_endian, little)
        self.assertIn(f"BYTE_ORDER {'MSB_LAST' if little else 'MSB_FIRST'}", text)
        self.assertIn(f"BYTE_ORDER_{'MSB_LAST' if little else 'MSB_FIRST'}", text)
        elf = Elf32(self.images[key])
        for name in ("Gain", "Table", "Factor", "Temp", "Alpha", "Enable"):
            obj = a2l.get(f"CalSwc.CalPrm.{name}")
            self.assertEqual(obj.kind, "CHARACTERISTIC")
            self.assertEqual(obj.address, elf.symbols[f"Rte_Prm_CalSwc_CalPrm_{name}_data"].value, name)
        self.assertEqual(a2l.get("CalSwc.CalPrm.Table").count, 4)
        self.assertEqual(a2l.get("CalSwc.CalPrm.Alpha").datatype, "FLOAT32_IEEE")
        self.assertEqual(a2l.get("CalSwc.CalPrm.Enable").datatype, "UBYTE")
        temp = a2l.get("CalSwc.CalPrm.Temp")
        self.assertEqual((temp.datatype, temp.unit), ("SWORD", "degC"))
        # physical = (-400 + stored) / 10: stored = (10 * phys + 400) / 1
        self.assertEqual(temp.coeffs, (0, 10, 400, 0, 0, 1))
        self.assertRegex(text, r"VALUE 0x[0-9A-F]{8} RL_SWORD 0 CM_RAT_M400_1_10 -3316.8 3236.7")
        self.assertRegex(text, r"CM_RAT_0_1_1000 0.0 65.535")
        # the two RTE buffers of the profile, read only
        signals = [o for o in a2l.objects.values() if o.kind == "MEASUREMENT"]
        self.assertEqual(sorted(o.name for o in signals), ["CalSwc.FOut.FOut", "CalSwc.Out.Out"])
        for o in signals:
            symbol = "Rte_Buf_" + o.name.replace(".", "_")
            self.assertEqual(o.address, elf.symbols[symbol].value, symbol)
        # IF_DATA from the configuration header and the bit timing
        self.assertEqual((a2l.master_id, a2l.slave_id, a2l.bitrate), (0x7F0, 0x7F1, 500000))
        self.assertEqual(text.count("/begin "), text.count("/end "))
        # RTE-26: formally valid, RTE-03 parameters unchanged (no limits, no axes), one FUNCTION for CalSwc
        self.assertEqual(a2lcheck.check_text(text), [])
        self.assertNotIn("EXTENDED_LIMITS", text)
        self.assertNotIn("MOD_PAR", text)             # this image has no xcp::EPK
        self.assertIn("/begin FUNCTION CalSwc", text)
        # RTE-29: no fixed point signal in this profile - no rte::SIGNAL_SCALINGS, no conversion at a MEASUREMENT
        self.assertFalse(a2lgen.has_signal_scaling(self.rte_config))
        self.assertNotIn(a2lgen.SCALING_SYMBOL, elf.symbols)
        self.assertTrue(all(o.coeffs == (0, 1, 0, 0, 0, 1) for o in signals))

    def test_little_endian_image(self):
        self.check_image("le", True)

    def test_big_endian_image(self):
        self.check_image("be", False)

    def test_extended_ids_and_aml(self):
        cfg = self.write(self.tmp / "ext.h", XCP_HEADER.format(master="0x80012345", slave="0x80012346"))
        text = self.generate(xcp_config=cfg, aml=Path("C:/xcp/XCP_common.aml"))
        self.assertIn("CAN_ID_MASTER 0x80012345", text)
        self.assertIn('/include "C:/xcp/XCP_common.aml"', text)

    def test_optional_commands_are_what_the_slave_can_do_without_page_switching(self):
        """TOOL-151: the A2L announces only commands of ecu/xcp and nothing of page switching - the slave has one
        page (SET_CAL_PAGE page 1 -> ERR_PAGE_NOT_VALID), so a tool must not expect a reference/working page."""
        text = self.generate("le")
        announced = re.findall(r"^\s*OPTIONAL_CMD (\S+)", text, re.M)
        self.assertEqual(announced, list(a2lgen.OPTIONAL_COMMANDS))
        slave = (REPO / "ecu/xcp/include/xcp/XcpSlave.h").read_text(encoding="utf-8")
        implemented = set(re.findall(r"CMD_([A-Z_]+)\s*=", slave))
        self.assertIn("SET_CAL_PAGE", implemented)  # the slave still answers it (page 0) - just not announced
        self.assertLessEqual(set(announced), implemented)
        for page_command in ("SET_CAL_PAGE", "GET_CAL_PAGE", "COPY_CAL_PAGE", "GET_PAG_PROCESSOR_INFO",
                             "GET_SEGMENT_INFO", "GET_PAGE_INFO", "SET_SEGMENT_MODE", "GET_SEGMENT_MODE"):
            self.assertNotIn(page_command, text)
        # no page model either: no SEGMENT/PAGE in IF_DATA XCP, no MEMORY_SEGMENT
        self.assertNotRegex(text, r"/begin (SEGMENT|PAGE|MEMORY_SEGMENT)\b")
        self.assertNotIn("PAG_PROCESSOR", text)

    # ---- messages with the path of the file -------------------------------------------------------------------

    def assertError(self, pattern: str, **kwargs):
        with self.assertRaises(a2lgen.A2lError) as ctx:
            self.generate(**kwargs)
        self.assertRegex(str(ctx.exception), pattern)

    def test_bad_xcp_config(self):
        cfg = self.write(self.tmp / "bad11.h", XCP_HEADER.format(master="0x800", slave="0x7F1"))
        self.assertError(re.escape(str(cfg)) + r".*NX_XCP_CAN_ID_MASTER 0x800 exceeds the 11 bit range", xcp_config=cfg)
        cfg = self.write(self.tmp / "same.h", XCP_HEADER.format(master="0x7F1", slave="0x7F1"))
        self.assertError(r"are both 0x7F1", xcp_config=cfg)
        cfg = self.write(self.tmp / "missing.h", "#define NX_XCP_BUS_NAME \"CAN0\"\n")
        self.assertError(re.escape(str(cfg)) + r": #define NX_XCP_CAN_ID_MASTER missing", xcp_config=cfg)

    def test_missing_bit_timing(self):
        cfg = self.write(self.tmp / "can2.h", XCP_HEADER.format(master="0x7F0", slave="0x7F1").replace("CAN0", "CAN2"))
        self.assertError(r"CAN2_BITRATE not found", xcp_config=cfg)

    # ---- free bus names (ARCH-61, TOOL-112) ----------------------------------------------------------------------

    # Bit timing header as tools/bswgen writes it for a synthetic board with the buses `chassis` and `BodyCan`
    # (canBitTimingConfiguration.h.j2: constants named after the C identifier, the board name only in comments)
    FREE_BIT_TIMING = ("// chassis on FlexCAN2: 250000 bit/s\n"
                       "constexpr uint32_t const CHASSIS_BITRATE              = 250000U;\n"
                       "// BodyCan on FlexCAN0: 125000 bit/s\n"
                       "constexpr uint32_t const BODYCAN_BITRATE              = 125000U;\n")

    def test_free_bus_names(self):
        """XCP on a lower-case and on a mixed-case bus: the bit rate is found under the C identifier."""
        import rtegen
        timing = self.write(self.tmp / "freeTiming.h", self.FREE_BIT_TIMING)
        for bus, bitrate in (("chassis", 250000), ("BodyCan", 125000)):
            with self.subTest(bus=bus):
                # the header of the generator itself (tools/rtegen), not a hand-written copy
                cfg = self.write(self.tmp / f"xcp_{bus}.h",
                                 rtegen.render_xcp_config(rtegen.XcpCan(bus, 0x7F0, 0x7F1), "test"))
                self.assertEqual(a2lgen.load_xcp_config(cfg).bus, bus)
                self.assertEqual(a2lgen.load_bitrate(timing, bus), bitrate)
                a2l = A2l(self.generate(xcp_config=cfg, bit_timing=timing))
                self.assertEqual((a2l.master_id, a2l.slave_id, a2l.bitrate), (0x7F0, 0x7F1, bitrate))

    def test_free_bus_name_without_bit_timing(self):
        """The message names the constant that is looked for (the C identifier) and the bus as the board names it."""
        timing = self.write(self.tmp / "chassisOnly.h", self.FREE_BIT_TIMING.split("// BodyCan")[0])
        with self.assertRaises(a2lgen.A2lError) as ctx:
            a2lgen.load_bitrate(timing, "BodyCan")
        self.assertIn("BODYCAN_BITRATE not found (bus BodyCan of XCP", str(ctx.exception))

    def test_same_identifier_rule_as_the_generators(self):
        """a2lgen takes the identifier from nxconfig.can.c_name; the generators of the headers it reads must agree:
        tools/bswgen (canBitTimingConfiguration.h, modules.identifier), tools/rtegen (NX_XCP_BUS, c_name)."""
        import rtegen
        sys.path.insert(0, str(REPO / "tools" / "bswgen"))
        import modules as bswgen_modules
        for bus, ident in (("chassis", "CHASSIS"), ("BodyCan", "BODYCAN"), ("CAN0", "CAN0"), ("body-can", "BODY_CAN"),
                           ("2nd", "SIG_2ND")):
            with self.subTest(bus=bus):
                self.assertEqual(a2lgen.nxcan.c_name(bus), ident)
                self.assertEqual(bswgen_modules.identifier(bus), ident)
                self.assertEqual(rtegen.c_name(bus), ident)
                header = rtegen.render_xcp_config(rtegen.XcpCan(bus, 0x7F0, 0x7F1), "test")
                self.assertIn(f"#define NX_XCP_BUS           {ident} ", header)

    def test_not_an_image(self):
        self.assertError(re.escape(str(self.rte_config)) + r": not an ELF file", elf=self.rte_config)
        start = self.tmp / "start.le.elf"
        zig("ld.lld", "-e", "_start", str(self.tmp / "start.le.o"), "-o", str(start))
        self.assertError(re.escape(str(start)) + r": symbol _ZN3rte7SIGNALSE \(rte::SIGNALS\) not found", elf=start)

    def test_bad_rte_config(self):
        cfg = self.write(self.tmp / "RteConfigBroken.h", "namespace rte {}\n")
        self.assertError(re.escape(str(cfg)) + r": enum class SignalType not found", rte_config=cfg)

    def test_identifier_collisions_with_project(self):
        project_dir = self.tmp / "proj"
        project_dir.mkdir(exist_ok=True)
        self.write(project_dir / "rte.yaml", "uds:\n  bus: CAN0\n")
        project = self.write(project_dir / "project.yaml",
                             "project: {rte: rte.yaml}\nuse: {can: [CAN0, CAN1]}\n"
                             "diagnostics: {request_id: 0x7F0, functional_id: 0x7DF, response_id: 0x7E8}\n")
        self.assertError(r"XCP master id 0x7F0 on CAN0 is already used by .*project.yaml: diagnostics.request_id",
                         project=project)
        # the same ids on another bus do not collide
        cfg = self.write(self.tmp / "can1.h", XCP_HEADER.format(master="0x7F0", slave="0x7F1").replace("CAN0", "CAN1"))
        self.generate(xcp_config=cfg, project=project)
        # a bus the project does not use
        other = self.write(project_dir / "other.yaml", "project: {rte: rte.yaml}\nuse: {can: [CAN1]}\n")
        self.assertError(re.escape(str(other)) + r": use.can: XCP bus CAN0 .* is not a CAN bus of the project",
                         project=other)

    def test_use_can_all(self):
        """use.can: all (build-test profile S32K148) is not a list of buses: no bus check, no error."""
        project = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
        self.assertEqual(a2lgen.check_project(a2lgen.XcpCan("CAN0", 0x7F0, 0x7F1, None, "test"), project), [])
        project_dir = self.tmp / "proj_all"
        project_dir.mkdir(exist_ok=True)
        self.write(project_dir / "rte.yaml", "uds:\n  bus: CAN0\n")
        one = self.write(project_dir / "project.yaml", "project: {rte: rte.yaml}\nuse: {can: CAN1}\n")
        with self.assertRaisesRegex(a2lgen.A2lError, r"XCP bus CAN0 .* is not a CAN bus of the project \(CAN1\)"):
            a2lgen.check_project(a2lgen.XcpCan("CAN0", 0x7F0, 0x7F1, None, "test"), one)

    # ---- RTE-25: array ports as one MEASUREMENT with MATRIX_DIM -------------------------------------------------

    def test_group_arrays(self):
        S = a2lgen.Signal
        rows = [S("A.X.X[0]", "", "UINT16", 0x100), S("A.X.X[1]", "", "UINT16", 0x102),
                S("A.X.X[2]", "", "UINT16", 0x104), S("B.Y.Y", "", "UINT8", 0x200),
                S("C.Gap.Gap[0]", "", "UINT16", 0x300), S("C.Gap.Gap[1]", "", "UINT16", 0x304),     # not contiguous
                S("D.Mix.Mix[0]", "", "UINT16", 0x400), S("D.Mix.Mix[1]", "", "SINT16", 0x402),     # other type
                S("E.One.One[0]", "", "UINT8", 0x500),                                              # one element
                S("F.S.S.Limits[0]", "", "UINT8", 0x600), S("F.S.S.Limits[1]", "", "UINT8", 0x601)]
        grouped = a2lgen.group_arrays(rows)
        self.assertEqual([(s.name, s.dim, s.address) for s in grouped],
                         [("A.X.X", 3, 0x100), ("B.Y.Y", 1, 0x200), ("C.Gap.Gap[0]", 1, 0x300),
                          ("C.Gap.Gap[1]", 1, 0x304), ("D.Mix.Mix[0]", 1, 0x400), ("D.Mix.Mix[1]", 1, 0x402),
                          ("E.One.One[0]", 1, 0x500), ("F.S.S.Limits", 2, 0x600)])

    # ---- RTE-28: signal qualifiers with the value table of QLF_* (QLF_NOT_AVAILABLE) -----------------------------

    def test_qualifier_measurement_has_the_qlf_value_table(self):
        import rtegen
        S = a2lgen.Signal
        rows = [S("DemoSwc.PcCommandQlf.PcCommandQlf", "can.qlf.PC_Command", "UINT8", 0x100),
                S("SinkSwc.LevelQualifier.LevelQualifier", "", "UINT16", 0x102),
                S("nvm.BootCount.Qlf", "nvm.qlf.BootCount", "UINT8", 0x104),
                S("SinkSwc.Level_mV.Level_mV", "", "UINT16", 0x106),         # no qualifier name
                S("SinkSwc.GainQlf.GainQlf", "", "FLOAT32", 0x108)]          # not an unsigned integer
        self.assertEqual([a2lgen.is_qualifier_signal(s) for s in rows], [True, True, True, False, False])
        xcp = a2lgen.XcpCan("CAN0", 0x7F0, 0x7F1, 500000)
        text = a2lgen.render(a2lgen.Image(rows, [], True), xcp, "qlf")
        self.assertEqual(a2lcheck.check_text(text), [], "A2L not formally valid")
        vtab = text[text.index("/begin COMPU_VTAB VTAB_QLF"):text.index("/end COMPU_VTAB")]
        self.assertIn(f"TAB_VERB {len(rtegen.QLF_VALUES)}", vtab)
        self.assertIn('64 "QLF_NOT_AVAILABLE"', vtab)
        self.assertIn('8 "QLF_ERROR_TIMEOUT"', vtab)
        self.assertIn("UBYTE CM_QLF 0 0 0 255", text)
        self.assertIn("UWORD CM_QLF 0 0 0 65535", text)
        self.assertIn("UWORD CM_IDENTITY 0 0 0 65535", text)                 # Level_mV keeps the identity
        self.assertEqual(text.count("CM_QLF 0 0"), 3)
        # without a qualifier no value table: the A2L of such an image stays as before
        plain = a2lgen.render(a2lgen.Image(rows[3:4], [], True), xcp, "qlf")
        self.assertNotIn("CM_QLF", plain)

    def test_array_port_is_one_measurement_with_matrix_dim(self):
        """Profile tools/rtegen/test/arrays: uint16[6] (dimension from the init data) between two C components."""
        import rtegen
        arrays = REPO / "tools" / "rtegen" / "test" / "arrays"
        gen = self.tmp / "gen_arrays"
        project = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
        rtegen.generate(arrays / "rte.yaml", project, gen, REPO,
                        REPO / "ecu" / "asyncCoreConfiguration" / "tasks.yaml")
        includes = [f"-I{gen}", f"-I{gen / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                    f"-I{REPO / 'tools/rtegen/test/can_host/stubs'}"]
        objects = []
        for src in (gen / "Rte.c", arrays / "CellSrcSwc/src/CellSrcSwc.c", arrays / "CellSinkSwc/src/CellSinkSwc.c",
                    self.tmp / "start.c", gen / "RteConfig.cpp"):
            obj = self.tmp / f"{src.stem}.arrays.o"
            lang = (["c++", "-std=c++17", "-fno-exceptions", "-fno-rtti"] if src.suffix == ".cpp"
                    else ["cc", "-std=c99"])
            zig(*lang, "-target", TARGETS["le"], "-O1", "-ffreestanding", *includes, "-c", str(src), "-o", str(obj))
            objects.append(str(obj))
        elf = self.tmp / "app.arrays.elf"
        zig("ld.lld", "--unresolved-symbols=ignore-all", "-e", "_start", *objects, "-o", str(elf))
        text = self.generate(elf=elf, rte_config=gen / "RteConfig.h")
        a2l = A2l(text)
        image = Elf32(elf)
        for name, count in (("CellSrcSwc.CellVoltages.CellVoltages", 6), ("CellSinkSwc.CellEcho.CellEcho", 6),
                            ("CellSinkSwc.MaxCell_mV.MaxCell_mV", 1)):
            obj = a2l.get(name)
            self.assertEqual((obj.kind, obj.datatype, obj.count), ("MEASUREMENT", "UWORD", count), name)
            self.assertEqual(obj.address, image.symbols["Rte_Buf_" + name.replace(".", "_")].value, name)
            self.assertEqual(a2l.size(obj), 2 * count)
        self.assertEqual(text.count("MATRIX_DIM 6"), 2)
        self.assertNotIn("CellVoltages[0]", text)     # no element MEASUREMENT next to the array one

    def test_measurements_of_a_component_are_measurements_at_their_symbol(self):
        """TOOL-143: `measurements:` of swc.yaml (what the Studio import writes for a Simulink.Signal with an
        exporting storage class) - one MEASUREMENT each at the address of the variable, MATRIX_DIM for an array,
        PHYS_UNIT and description from the table row; next to the array ports of the profile test/arrays."""
        import rtegen
        from test_rtegen_c_swc import arrays_with_measurements
        ecu_file = arrays_with_measurements(self.tmp / "meas")
        gen = self.tmp / "gen_meas"
        project = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
        rtegen.generate(ecu_file, project, gen, REPO, REPO / "ecu" / "asyncCoreConfiguration" / "tasks.yaml")
        includes = [f"-I{gen}", f"-I{gen / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                    f"-I{REPO / 'tools/rtegen/test/can_host/stubs'}"]
        objects = []
        for src in (gen / "Rte.c", ecu_file.parent / "CellSrcSwc/src/CellSrcSwc.c",
                    ecu_file.parent / "CellSinkSwc/src/CellSinkSwc.c", self.tmp / "start.c", gen / "RteConfig.cpp"):
            obj = self.tmp / f"{src.stem}.meas.o"
            lang = (["c++", "-std=c++17", "-fno-exceptions", "-fno-rtti"] if src.suffix == ".cpp"
                    else ["cc", "-std=c99"])
            zig(*lang, "-target", TARGETS["le"], "-O1", "-ffreestanding", *includes, "-c", str(src), "-o", str(obj))
            objects.append(str(obj))
        elf = self.tmp / "app.meas.elf"
        zig("ld.lld", "--unresolved-symbols=ignore-all", "-e", "_start", *objects, "-o", str(elf))
        text = self.generate(elf=elf, rte_config=gen / "RteConfig.h")
        self.assertEqual(a2lcheck.check_text(text), [], "A2L not formally valid")
        a2l, image = A2l(text), Elf32(elf)
        for name, symbol, dtype, count in (("CellSrcSwc.CellSrc_u_Max", "CellSrc_u_Max", "FLOAT32_IEEE", 1),
                                           ("CellSrcSwc.CellSrc_n_Cells", "CellSrc_n_Cells", "UWORD", 6)):
            obj = a2l.get(name)
            self.assertEqual((obj.kind, obj.datatype, obj.count), ("MEASUREMENT", dtype, count), name)
            self.assertEqual(obj.address, image.symbols[symbol].value, name)
        self.assertIn('/begin MEASUREMENT CellSrcSwc.CellSrc_u_Max "highest cell voltage"', text)
        block = text.split("/begin MEASUREMENT CellSrcSwc.CellSrc_u_Max", 1)[1].split("/end MEASUREMENT", 1)[0]
        self.assertIn('PHYS_UNIT "V"', block)
        block = text.split("/begin MEASUREMENT CellSrcSwc.CellSrc_n_Cells", 1)[1].split("/end MEASUREMENT", 1)[0]
        self.assertIn("MATRIX_DIM 6", block)
        self.assertNotIn("PHYS_UNIT", block)
        self.assertEqual(text.count("MATRIX_DIM 6"), 3)       # the two array ports and the measurement
        self.assertNotIn("CellSrc_n_Cells[0]", text)
        # the long identifier of a row that is no measurement stays its connection text
        signal = a2lgen.Signal("A.X.X", "dio.in.KEY", "UINT8", 0)
        self.assertEqual(a2lgen.measurement_texts(signal), ("dio.in.KEY", ""))
        self.assertEqual(a2lgen.measurement_texts(a2lgen.Signal("A.m", "measurement", "UINT8", 0)),
                         ("measurement A.m", ""))

    def test_demo_project_ids_do_not_collide(self):
        a2lgen.check_project(a2lgen.XcpCan("CAN0", 0x7F0, 0x7F1, None, "test"),
                             FULL_PROJECT)
        with self.assertRaises(a2lgen.A2lError) as ctx:  # PC_Command of TestFull.dbc is 0x210
            a2lgen.check_project(a2lgen.XcpCan("CAN0", 0x210, 0x7F1, None, "test"),
                                 FULL_PROJECT)
        self.assertIn("TestFull.dbc: message PC_Command", str(ctx.exception))


# ---- RTE-26: calibration objects of stage K1 at a real image -------------------------------------------------------

CALIB = REPO / "tools" / "rtegen" / "test" / "calib"


class CalibrationA2lTest(unittest.TestCase):
    """Profile tools/rtegen/test/calib (CalMapSwc: shared axis, map with a shared and an own axis, curves over a
    shared and over own breakpoints, value, array, value table, RTE-03 parameter), generated by tools/rtegen and
    cross-compiled with zig together with ecu/xcp/src/systems/XcpEpk.cpp and the header of tools/a2lgen/epk.py.
    The A2L is checked formally (a2lcheck), against the symbols of the image and by reading the values back through
    the reader of xcp_master.py from the image."""

    tmp: Path

    @classmethod
    def setUpClass(cls):
        import rtegen
        import epk
        cls.tmp = Path(tempfile.mkdtemp(prefix="a2lgen_calib_"))
        cls.gen = gen = cls.tmp / "gen"
        project = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
        rtegen.generate(CALIB / "rte.yaml", project, gen, REPO, REPO / "ecu" / "asyncCoreConfiguration" / "tasks.yaml")
        cls.epk_text = "calib_test neXtBSW 0.1.0 v0.1.0-1-gabcdef0"
        epk.write(cls.tmp / "epk" / "xcp" / "xcpEpk.h", cls.epk_text)
        (cls.tmp / "start.c").write_text("void _start(void) { for (;;) {} }\n")
        includes = [f"-I{gen}", f"-I{gen / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                    f"-I{REPO / 'tools/rtegen/test/can_host/stubs'}", f"-I{cls.tmp / 'epk'}"]
        cls.images = {}
        for key, target in TARGETS.items():
            objects = []
            for src in (gen / "Rte.c", CALIB / "CalMapSwc/src/CalMapSwc.c", cls.tmp / "start.c", gen / "RteConfig.cpp",
                        REPO / "ecu/xcp/src/systems/XcpEpk.cpp"):
                obj = cls.tmp / f"{src.stem}.{key}.o"
                lang = (["c++", "-std=c++17", "-fno-exceptions", "-fno-rtti"] if src.suffix == ".cpp"
                        else ["cc", "-std=c99"])
                zig(*lang, "-target", target, "-O1", "-ffreestanding", "-Wall", "-Wextra", "-Werror", *includes,
                    "-c", str(src), "-o", str(obj))
                objects.append(str(obj))
            elf = cls.tmp / f"calib.{key}.elf"
            zig("ld.lld", "--unresolved-symbols=ignore-all", "-e", "_start", *objects, "-o", str(elf))
            cls.images[key] = elf
        cls.xcp_config = A2lGenTest.write(cls.tmp / "xcpCanConfiguration.h",
                                          XCP_HEADER.format(master="0x7F0", slave="0x7F1"))
        cls.bit_timing = A2lGenTest.write(cls.tmp / "canBitTimingConfiguration.h", BIT_TIMING)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def generate(self, key: str = "le", **kwargs) -> str:
        out = self.tmp / f"calib.{key}.a2l"
        args = dict(elf=self.images[key], output=out, rte_config=self.gen / "RteConfig.h",
                    xcp_config=self.xcp_config, bit_timing=self.bit_timing, name="calib_test")
        args.update(kwargs)
        self.messages = a2lgen.generate(**args)
        return out.read_text(encoding="utf-8")

    @staticmethod
    def block(text: str, kind: str, name: str) -> str:
        m = re.search(rf"/begin {kind} {re.escape(name)}\s.*?/end {kind}", text, re.S)
        if m is None:
            raise AssertionError(f"{kind} {name} not in the A2L")
        return m.group(0)

    def check(self, key: str, little: bool):
        text = self.generate(key)
        self.assertEqual(a2lcheck.check_text(text), [], "A2L not formally valid")
        elf = Elf32(self.images[key])
        a2l = A2l(text)
        prm = "CalMapSwc.CalPrm."
        # EPK: the string of the image and its address
        self.assertIn(f'EPK "{self.epk_text}"', text)
        self.assertIn(f"ADDR_EPK 0x{elf.symbols[a2lgen.EPK_SYMBOL].value:08X}", text)
        self.assertEqual(a2l.epk, self.epk_text)
        # shared axis: AXIS_PTS with engineering limits and the type range as EXTENDED_LIMITS
        axis = self.block(text, "AXIS_PTS", prm + "SpeedAxis_rpm")
        address = elf.symbols["Rte_Prm_CalMapSwc_CalPrm_SpeedAxis_rpm_data"].value
        self.assertIn(f'"Speed breakpoints"\n      0x{address:08X} NO_INPUT_QUANTITY RL_AXIS_UWORD 0 CM_IDENTITY 5 0 '
                      f'8000', axis)
        self.assertIn("EXTENDED_LIMITS 0 65535", axis)
        self.assertIn('PHYS_UNIT "rpm"', axis)
        # map: record of the own Y axis (sint8[3], one byte of padding) and the values, X over the shared axis
        record = elf.symbols["Rte_Prm_CalMapSwc_CalPrm_TorqueLimit_Nm_data"].value
        tmap = self.block(text, "CHARACTERISTIC", prm + "TorqueLimit_Nm")
        self.assertIn(f"MAP 0x{record:08X} RL_MAP_Y3SBYTE_V15SWORD 0 CM_RAT_0_1_10 0 250", tmap)
        self.assertRegex(tmap, r"/begin AXIS_DESCR COM_AXIS NO_INPUT_QUANTITY CM_IDENTITY 5 0 8000\s+"
                               rf"AXIS_PTS_REF {re.escape(prm)}SpeedAxis_rpm")
        self.assertRegex(tmap, r"/begin AXIS_DESCR STD_AXIS NO_INPUT_QUANTITY CM_IDENTITY 3 -40 125\s+"
                               r'PHYS_UNIT "degC"')
        self.assertIn("EXTENDED_LIMITS -3276.8 3276.7", tmap)
        layout = self.block(text, "RECORD_LAYOUT", "RL_MAP_Y3SBYTE_V15SWORD")
        # TOOL-196: one block per X breakpoint, Y running fastest = column by column in the A2L view
        self.assertIn("FIX_NO_AXIS_PTS_Y 3\n      AXIS_PTS_Y 1 SBYTE INDEX_INCR DIRECT\n"
                      "      FNC_VALUES 2 SWORD COLUMN_DIR DIRECT", layout)
        # curves: over the shared axis (COM_AXIS) and over own breakpoints in front of the values (STD_AXIS)
        boost = self.block(text, "CHARACTERISTIC", prm + "BoostCurve")
        self.assertIn(f"CURVE 0x{elf.symbols['Rte_Prm_CalMapSwc_CalPrm_BoostCurve_data'].value:08X} RL_UWORD 0 "
                      f"CM_IDENTITY 0 2500", boost)
        derate = self.block(text, "CHARACTERISTIC", prm + "DerateCurve")
        self.assertIn(f"CURVE 0x{elf.symbols['Rte_Prm_CalMapSwc_CalPrm_DerateCurve_data'].value:08X} "
                      f"RL_STD_X4SWORD_V4UBYTE 0 CM_RAT_0_1_2 0 100", derate)
        self.assertIn("STD_AXIS NO_INPUT_QUANTITY CM_RAT_0_1_10 4 -40 150", derate)
        # value with limits and safety class, value table, array, RTE-03 parameter as before
        self.assertIn('"Absolute torque limit"\n      VALUE', text)
        self.assertRegex(self.block(text, "CHARACTERISTIC", prm + "MaxTorque_Nm"),
                         r"VALUE 0x[0-9A-F]{8} RL_SWORD 0 CM_RAT_0_1_10 0 300")
        vtab = self.block(text, "COMPU_VTAB", "VTAB_CalMapSwc_CalPrm_Mode")
        self.assertIn('TAB_VERB 3\n      0 "Off"\n      1 "Eco"\n      2 "Sport"', vtab)
        self.assertIn("VALUE", self.block(text, "CHARACTERISTIC", prm + "Mode"))
        self.assertIn("CM_TAB_CalMapSwc_CalPrm_Mode 0 255", self.block(text, "CHARACTERISTIC", prm + "Mode"))
        self.assertRegex(self.block(text, "CHARACTERISTIC", prm + "Offsets_Nm"),
                         r"VAL_BLK 0x[0-9A-F]{8} RL_SWORD 0 CM_IDENTITY -20 20\s+NUMBER 3")
        self.assertIn(f'/begin CHARACTERISTIC {prm}Gain "RTE calibration parameter {prm}Gain"\n'
                      f"      VALUE 0x{elf.symbols['Rte_Prm_CalMapSwc_CalPrm_Gain_data'].value:08X} RL_FLOAT32_IEEE 0 "
                      f"CM_IDENTITY", text)
        # own breakpoints are no object of their own; FUNCTION lists the component's objects
        self.assertNotIn(f"CHARACTERISTIC {prm}DerateCurve_X", text)
        function = self.block(text, "FUNCTION", "CalMapSwc")
        for name in ("SpeedAxis_rpm", "TorqueLimit_Nm", "BoostCurve", "DerateCurve", "MaxTorque_Nm", "Mode",
                     "Offsets_Nm", "Gain"):
            self.assertIn(f"        {prm}{name}\n", function)
        self.assertNotIn("_X\n", function)
        # the values behind the A2L addresses, read from the image through the reader of xcp_master
        expected = {
            "SpeedAxis_rpm": [0, 1000, 2000, 4000, 6000],
            "TorqueLimit_Nm_Y": [-20, 25, 90],
            "TorqueLimit_Nm": [120, 110, 90, 150, 140, 120, 200, 180, 150, 180, 170, 140, 150, 140, 120],
            "BoostCurve": [0, 400, 900, 1500, 1800],
            "DerateCurve_X": [-40, 60, 100, 150],
            "DerateCurve": [100, 100, 80, 50],
            "MaxTorque_Nm": [250], "Mode": [1], "Offsets_Nm": [-5, 0, 5], "Gain": [0.75],
        }
        for name, values in expected.items():
            obj = a2l.get(prm + name)
            got = a2l.decode(obj, elf.read(obj.address, a2l.size(obj)))
            self.assertEqual([round(v, 6) for v in got], values, name)
        self.assertEqual(a2l.get(prm + "TorqueLimit_Nm").limits, (0.0, 250.0))
        # stored limits in the image, as the run time sees them (rte::PARAMETERS limits)
        image = a2lgen.read_image(self.images[key], a2lgen.load_signal_types(self.gen / "RteConfig.h"))
        rows = {p.name: p for p in image.parameters}
        self.assertEqual((rows[prm + "TorqueLimit_Nm"].limits, rows[prm + "TorqueLimit_Nm"].safety), ((0, 2500), "ASIL_B"))
        self.assertEqual(rows[prm + "DerateCurve_X"].kind, "OWN_AXIS")
        self.assertIsNone(rows[prm + "Gain"].limits)
        self.assertEqual(elf.little_endian, little)

    def test_little_endian_image(self):
        self.check("le", True)

    def test_big_endian_image(self):
        self.check("be", False)

    def test_description_file_must_match_the_image(self):
        """RteCalibration.json and image name the same objects of the same kind; otherwise a2lgen refuses."""
        import json
        original = (self.gen / "RteCalibration.json").read_text(encoding="utf-8")
        try:
            data = json.loads(original)
            del data["parameters"]["CalMapSwc.CalPrm.Mode"]
            (self.gen / "RteCalibration.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(a2lgen.A2lError, r"RteCalibration.json: parameter CalMapSwc.CalPrm.Mode of "
                                                         r"the image .* is missing"):
                self.generate()
            data = json.loads(original)
            data["parameters"]["CalMapSwc.CalPrm.BoostCurve"]["kind"] = "map"
            (self.gen / "RteCalibration.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(a2lgen.A2lError, r"BoostCurve is map, the image says CURVE"):
                self.generate()
            data = json.loads(original)
            data["parameters"]["CalMapSwc.CalPrm.Ghost"] = {"kind": "value"}
            (self.gen / "RteCalibration.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(a2lgen.A2lError, r"CalMapSwc.CalPrm.Ghost not in the image"):
                self.generate()
        finally:
            (self.gen / "RteCalibration.json").write_text(original, encoding="utf-8")

    def test_a2lcheck_finds_broken_files(self):
        """The formal check is not blind: each broken copy of the generated A2L gives its finding."""
        text = self.generate()
        prm = "CalMapSwc.CalPrm."
        cases = [
            (text.replace("/end AXIS_DESCR", "/end AXIS_DESCRX", 1), "closes /begin AXIS_DESCR"),
            (text.replace(f"AXIS_PTS_REF {prm}SpeedAxis_rpm", "AXIS_PTS_REF Nowhere", 1), "Nowhere is no AXIS_PTS"),
            (text.replace("RL_STD_X4SWORD_V4UBYTE 0", "RL_UBYTE 0", 1), "record layout RL_UBYTE has no AXIS_PTS_X"),
            (text.replace("CM_RAT_0_1_10 0 300", "CM_RAT_0_1_10 300 0", 1), "lower limit 300 > upper limit 0"),
            (text.replace("EXTENDED_LIMITS 0 65535", "EXTENDED_LIMITS 100 65535", 1), "not inside EXTENDED_LIMITS"),
            (text.replace("COMPU_TAB_REF VTAB_CalMapSwc_CalPrm_Mode", "COMPU_TAB_REF VT_None", 1),
             "COMPU_TAB_REF VT_None is not defined"),
            (text.replace(f"        {prm}Gain\n", "        Nothing\n", 1), "Nothing is no CHARACTERISTIC or AXIS_PTS"),
            (text.replace("FIX_NO_AXIS_PTS_Y 3", "FIX_NO_AXIS_PTS_Y 4", 1), "the record layout fixes 4"),
            (text.replace("ASAP2_VERSION 1 61", "ASAP2 1 61", 1), "must start with ASAP2_VERSION"),
        ]
        for broken, finding in cases:
            with self.subTest(finding=finding):
                found = a2lcheck.check_text(broken)
                self.assertTrue(any(finding in f for f in found), found)


class SwcLimitsTest(unittest.TestCase):
    """TOOL-150: unit/min/max of a port or a measurement (A2L PHYS_UNIT/LOWER_LIMIT/UPPER_LIMIT) come from swc.yaml,
    found through --project -> rte.yaml -> the component's own swc.yaml - no ELF, no rtegen generation, no MATLAB:
    load_swc_limits() and render() are exercised directly (unlike A2lGenTest, which needs the real, compiled RTE
    tables to test what a2lgen reads from the image itself; here nothing but text files and in-memory objects)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="a2lgen_swclimits_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def write(path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def block(text: str, kind: str, name: str) -> str:
        m = re.search(rf"/begin {kind} {re.escape(name)}\s.*?/end {kind}", text, re.S)
        if m is None:
            raise AssertionError(f"{kind} {name} not in the A2L")
        return m.group(0)

    def test_load_swc_limits_reads_unit_and_engineering_limits_from_the_components_swc_yaml(self):
        """(unit, min, max) of every port and measurement of the project's components - best effort, like
        check_project(): a component or a swc.yaml a2lgen cannot read simply contributes nothing."""
        project_dir = self.tmp / "proj"
        self.write(project_dir / "swc" / "CellSrcSwc" / "swc.yaml",
                  "name: CellSrcSwc\n"
                  "runnables: [{name: Run10ms, period_ms: 10}]\n"
                  "ports:\n"
                  "  - {name: CellVoltage, direction: out, type: uint16, runnables: [Run10ms], unit: mV, min: 0, "
                  "max: 4200}\n"
                  "  - {name: Plain, direction: out, type: boolean, runnables: [Run10ms]}\n"
                  "measurements:\n"
                  "  - {name: CellSrc_u_Max, type: float32, unit: V, min: 0.0, max: 5.0}\n")
        self.write(project_dir / "rte.yaml",
                  "schema_version: 2\ncomponents: [{name: CellSrcSwc, path: swc/CellSrcSwc}, "
                  "{name: Unknown, path: swc/Unknown}]\nconnections: {}\n")
        project = self.write(project_dir / "project.yaml", "project: {rte: rte.yaml}\n")
        limits = a2lgen.load_swc_limits(project, REPO)
        self.assertEqual(limits, {"CellSrcSwc.CellVoltage": ("mV", 0, 4200),
                                  "CellSrcSwc.CellSrc_u_Max": ("V", 0.0, 5.0)})
        # a component whose swc.yaml is missing (Unknown above), a project without project.rte, and a project.yaml
        # a2lgen cannot even read are all just "nothing" - never an error (the A2L is generated from the image
        # regardless of --project)
        self.assertEqual(a2lgen.load_swc_limits(self.write(project_dir / "bare.yaml", "{}\n"), REPO), {})
        self.assertEqual(a2lgen.load_swc_limits(project_dir / "missing.yaml", REPO), {})

    def test_measurement_gets_engineering_limits_and_phys_unit_from_swc_yaml(self):
        """LOWER_LIMIT/UPPER_LIMIT of a MEASUREMENT (port or measurement) come from swc.yaml min/max when given,
        else the type range as always; PHYS_UNIT of a port comes from swc.yaml unit (a port never had one before
        TOOL-150) - a measurement's own bsw-text unit (TOOL-143, from the image) still wins, so a component without
        --project keeps behaving exactly as it always did."""
        image = a2lgen.Image(
            signals=[a2lgen.Signal("CellSrcSwc.CellVoltage.CellVoltage", "connected to CellSinkSwc.In.In",
                                   "UINT16", 0x100),
                     a2lgen.Signal("CellSrcSwc.CellSrc_u_Max", "measurement [V] highest cell voltage",
                                   "FLOAT32", 0x200)],
            parameters=[], little_endian=True)
        xcp = a2lgen.XcpCan("CAN0", 0x7F0, 0x7F1, None, "test")
        limits = {"CellSrcSwc.CellVoltage": ("mV", 0, 4200), "CellSrcSwc.CellSrc_u_Max": ("A", -1.0, 1.0)}
        text = a2lgen.render(image, xcp, "test", limits=limits)
        self.assertEqual(a2lcheck.check_text(text), [], "A2L not formally valid")
        port_block = self.block(text, "MEASUREMENT", "CellSrcSwc.CellVoltage.CellVoltage")
        self.assertIn("UWORD CM_IDENTITY 0 0 0 4200", port_block)
        self.assertIn('PHYS_UNIT "mV"', port_block)
        meas_block = self.block(text, "MEASUREMENT", "CellSrcSwc.CellSrc_u_Max")
        self.assertIn("FLOAT32_IEEE CM_IDENTITY 0 0 -1.0 1.0", meas_block)
        self.assertIn('PHYS_UNIT "V"', meas_block)      # the bsw text unit (TOOL-143) still wins over swc.yaml's "A"
        # without limits (no --project, or nothing given): the type range as before, no PHYS_UNIT for the port -
        # existing profiles are unaffected, byte for byte
        plain = a2lgen.render(image, xcp, "test")
        self.assertEqual(a2lcheck.check_text(plain), [])
        plain_port = self.block(plain, "MEASUREMENT", "CellSrcSwc.CellVoltage.CellVoltage")
        self.assertIn("UWORD CM_IDENTITY 0 0 0 65535", plain_port)
        self.assertNotIn("PHYS_UNIT", plain_port)

    def test_signal_key_matches_a_port_and_a_measurement_label(self):
        """RTE-07: a port's label is 'Component.Port.Element' (data element named like its port); a measurement's
        (TOOL-143, rtegen.Measurement.label) is already 'Component.Name' - both give the same key."""
        self.assertEqual(a2lgen._signal_key("CellSrcSwc.CellVoltages.CellVoltages"), "CellSrcSwc.CellVoltages")
        self.assertEqual(a2lgen._signal_key("CellSrcSwc.CellSrc_u_Max"), "CellSrcSwc.CellSrc_u_Max")
        self.assertIsNone(a2lgen._signal_key("NoDot"))


TYPES = REPO / "tools" / "rtegen" / "test" / "types"
DEGC_UNIT = ('<UNIT UUID="00000000-0000-0000-0000-000000000029"><SHORT-NAME>degC</SHORT-NAME>'
             '<DISPLAY-NAME>degC</DISPLAY-NAME><FACTOR-SI-TO-UNIT>1</FACTOR-SI-TO-UNIT>'
             '<OFFSET-SI-TO-UNIT>0</OFFSET-SI-TO-UNIT></UNIT>')


class SignalScalingA2lTest(unittest.TestCase):
    """RTE-29: fixed point signals. The RTE-04 profile tools/rtegen/test/types (TypeSwc: record member Speed in
    1/16 rpm, temperatures (-400 + stored) / 10, here with the unit degC in the compu method of TypeSwc) is generated by
    tools/rtegen and cross-compiled with zig for both byte orders, with the Embedded Coder code of TypeSwc and the
    receiver TypeSink. a2lgen reads rte::SIGNAL_SCALINGS from the image: COMPU_METHOD RAT_FUNC, converted limits and
    PHYS_UNIT of the MEASUREMENT; xcp_master.py decodes the stored values of the image to physical ones."""

    tmp: Path

    @classmethod
    def setUpClass(cls):
        import rtegen
        cls.tmp = Path(tempfile.mkdtemp(prefix="a2lgen_scaling_"))
        types = cls.tmp / "types"
        for name in ("TypeSwc", "TypeSink"):
            shutil.copytree(TYPES / name / "generated", types / name / "generated")
        shutil.copy(TYPES / "rte.yaml", types / "rte.yaml")
        datatype = types / "TypeSwc" / "generated" / "arxml" / "TypeSwc_datatype.arxml"
        text = datatype.read_text(encoding="utf-8")
        text, n = re.subn(r"(<SHORT-NAME>COMPU_UFIX16_SP1_BN40</SHORT-NAME>\s*<CATEGORY>LINEAR</CATEGORY>\s*"
                          r"<UNIT-REF DEST=\"UNIT\">)/DataTypes/Units/NoUnit", r"\1/DataTypes/Units/degC", text)
        text, m = re.subn(r"(<SHORT-NAME>Units</SHORT-NAME>\s*<ELEMENTS>)", r"\1" + DEGC_UNIT, text)
        assert (n, m) == (1, 1), (n, m)
        datatype.write_text(text, encoding="utf-8")
        cls.gen = gen = cls.tmp / "gen"
        project = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
        rtegen.generate(types / "rte.yaml", project, gen, REPO, REPO / "tools/rtegen/test/multitask/tasks.yaml")
        (cls.tmp / "start.c").write_text("void _start(void) { for (;;) {} }\n")
        src = {name: types / name / "generated" / "src" for name in ("TypeSwc", "TypeSink")}
        includes = [f"-I{gen}", f"-I{gen / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                    f"-I{REPO / 'tools/rtegen/test/can_host/stubs'}", f"-I{src['TypeSwc']}", f"-I{src['TypeSink']}"]
        cls.images = {}
        for key, target in TARGETS.items():
            objects = []
            for source in (gen / "Rte.c", src["TypeSwc"] / "TypeSwc.c", src["TypeSink"] / "TypeSink.c",
                           cls.tmp / "start.c", gen / "RteConfig.cpp"):
                obj = cls.tmp / f"{source.stem}.{key}.o"
                lang = (["c++", "-std=c++17", "-fno-exceptions", "-fno-rtti"] if source.suffix == ".cpp"
                        else ["cc", "-std=c99"])
                zig(*lang, "-target", target, "-O1", "-ffreestanding", "-Wall", "-Wextra", "-Werror", *includes,
                    "-c", str(source), "-o", str(obj))
                objects.append(str(obj))
            elf = cls.tmp / f"types.{key}.elf"
            zig("ld.lld", "--unresolved-symbols=ignore-all", "-e", "_start", *objects, "-o", str(elf))
            cls.images[key] = elf
        cls.xcp_config = A2lGenTest.write(cls.tmp / "xcpCanConfiguration.h",
                                          XCP_HEADER.format(master="0x7F0", slave="0x7F1"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def generate(self, key: str = "le", **kwargs) -> str:
        out = self.tmp / f"types.{key}.a2l"
        args = dict(elf=self.images[key], output=out, rte_config=self.gen / "RteConfig.h",
                    xcp_config=self.xcp_config, name="types_test")
        args.update(kwargs)
        a2lgen.generate(**args)
        return out.read_text(encoding="utf-8")

    def check(self, key: str):
        text = self.generate(key)
        self.assertEqual(a2lcheck.check_text(text), [])
        block = CalibrationA2lTest.block
        temp = block(text, "MEASUREMENT", "TypeSwc.Temp.Temp")
        # stored 0..65535 -> -40.0 .. 6513.5 degC
        self.assertIn("UWORD CM_RAT_M400_1_10 0 0 -40.0 6513.5", temp)
        self.assertIn('PHYS_UNIT "degC"', temp)
        speed = block(text, "MEASUREMENT", "TypeSwc.Stat.Stat.Echo.Speed")
        self.assertIn("SWORD CM_RAT_0_1_16 0 0 -2048.0 2047.9375", speed)
        self.assertNotIn("PHYS_UNIT", speed)                       # NoUnit in the model
        last = block(text, "MEASUREMENT", "TypeSink.LastTemp.LastTemp")
        self.assertIn("CM_RAT_M400_1_10", last)
        self.assertNotIn("PHYS_UNIT", last)                        # TypeSink's own compu method has no unit
        # one COMPU_METHOD per function: physical = (-400 + stored) / 10 <=> stored = (10 * phys + 400) / 1
        self.assertEqual(text.count("/begin COMPU_METHOD CM_RAT_M400_1_10 "), 1)
        self.assertIn("COEFFS 0 10 400 0 0 1", block(text, "COMPU_METHOD", "CM_RAT_M400_1_10"))
        # an enumeration, an array of plain integers and a boolean stay as they were
        self.assertIn("UBYTE CM_IDENTITY", block(text, "MEASUREMENT", "TypeSwc.ModeOut.ModeOut"))
        self.assertIn("UWORD CM_IDENTITY", block(text, "MEASUREMENT", "TypeSwc.Scaled.Scaled"))
        # the PC side shows the init values of the image physically: Temp 400 stored = 0 degC, LastTemp 655 = 25.5
        a2l, elf = A2l(text), Elf32(self.images[key])
        for name, physical in (("TypeSwc.Temp.Temp", 0.0), ("TypeSink.LastTemp.LastTemp", 25.5)):
            obj = a2l.get(name)
            self.assertEqual(a2l.decode(obj, elf.read(obj.address, a2l.size(obj))), [physical], name)
        self.assertEqual(a2l.get("TypeSwc.Temp.Temp").unit, "degC")
        self.assertEqual(a2l.encode(a2l.get("TypeSwc.Stat.Stat.Echo.Speed"), [-1.0]),
                         (-16).to_bytes(2, "little" if key == "le" else "big", signed=True))

    def test_little_endian_image(self):
        self.check("le")

    def test_big_endian_image(self):
        self.check("be")

    def test_header_and_image_must_agree(self):
        """An image without rte::SIGNAL_SCALINGS against a header that declares it (the linker removed the table, or
        another build), and the reverse: an error instead of stored values shown as physical ones."""
        plain_header = self.tmp / "plain" / "RteConfig.h"
        plain_header.parent.mkdir(exist_ok=True)
        plain_header.write_text((self.gen / "RteConfig.h").read_text(encoding="utf-8").replace(
            "#define NX_RTE_HAS_SIGNAL_SCALING 1", ""), encoding="utf-8")
        with self.assertRaisesRegex(a2lgen.A2lError, "rte::SIGNAL_SCALINGS in the image, but RteConfig.h has no "
                                                     "NX_RTE_HAS_SIGNAL_SCALING"):
            self.generate(rte_config=plain_header)
        broken = self.tmp / "noscaling.elf"
        name = a2lgen.SCALING_SYMBOL.encode() + b"\0"
        data = self.images["le"].read_bytes()
        self.assertIn(name, data)
        broken.write_bytes(data.replace(name, b"X" + name[1:]))     # the symbol is gone for a2lgen
        with self.assertRaisesRegex(a2lgen.A2lError, "rte::SIGNAL_SCALINGS missing although RteConfig.h defines"):
            self.generate(elf=broken)

    def test_scaled_rows_of_the_image(self):
        """Exactly the five fixed point rows carry a conversion; the array of plain integers next to them none."""
        image = a2lgen.read_image(self.images["be"], a2lgen.load_signal_types(self.gen / "RteConfig.h"), True)
        self.assertEqual(sorted(s.name for s in image.signals if s.scaled), [
            "TypeSink.LastTemp.LastTemp", "TypeSwc.Cmd.Cmd.Speed", "TypeSwc.Stat.Stat.Echo.Speed",
            "TypeSwc.Temp.Temp", "TypeSwc.TempOut.TempOut"])
        self.assertEqual({(s.numerator, s.denominator) for s in image.signals if "Temp" in s.name}, {((-400, 1), 10)})


if __name__ == "__main__":
    unittest.main()
