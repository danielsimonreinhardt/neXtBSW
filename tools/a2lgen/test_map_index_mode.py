"""TOOL-196: the index mode of a map (FNC_VALUES ROW_DIR / COLUMN_DIR), cross-checked against the numbers.

a2lgen keeps a map as one block per X breakpoint with the values over Y, Y running fastest (the order of
<Swc>_data.m, the RTE and Simulink's column-major matrix ny x nx). Up to TOOL-196 it called that ROW_DIR.

The A2L view of a map, as the free A2L readers read it: the X axis runs across the columns, the Y axis down the rows.
ROW_DIR stores the table row by row (X running fastest), COLUMN_DIR column by column (Y running fastest):
  - objutils (christoph2, used by asamint): read_asam_ndarray turns the A2L shape (X, Y) into the numpy shape
    (Y, X) and reads ROW_DIR in C order (last index = X fastest), COLUMN_DIR in Fortran order (Y fastest).
  - calibrationReader (asap2Go): the documentation of the index modes draws the X breakpoints above the table and
    stores ROW_DIR row after row, COLUMN_DIR column after column; ALTERNATE_WITH_X alternates the X breakpoint with
    the column of values of that X.
So a2lgen's order is COLUMN_DIR.

This test does not trust a2lgen's own reading: a small reader of its own takes the MAP objects from the A2L that
a2lgen writes for the component tools/studio/tests/profiles/MapImpSwc (TOOL-193: a Simulink model with a 2-D lookup
on the matrix K_map over K_speed (X) and K_load (Y), and a Simulink.LookupTable K_lut with own breakpoints), reads
the stored values from the ELF under each of the two readings and compares them with MapImpSwc_data.m and with the
MIL result of TOOL-193 (load 50, speed 1500 -> K_map interpolated 6.5). Only the reading of the index mode that
a2lgen writes matches; the other one gives different values (the test would notice a swapped mode).
"""

import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROFILES = REPO / "tools" / "studio" / "tests" / "profiles"
COMPONENT = PROFILES / "MapImpSwc"
for _path in (str(HERE), str(REPO / "tools" / "rtegen"), str(REPO / "tools" / "swc")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import a2lgen  # noqa: E402
import a2lcheck  # noqa: E402
from elf32 import Elf32  # noqa: E402

PRM = "MapImpSwc.CalPrm."
XCP_HEADER = """#pragma once
#define NX_XCP_BUS           CAN0
#define NX_XCP_BUS_NAME      "CAN0"
#define NX_XCP_CAN_ID_MASTER 0x7F0UL
#define NX_XCP_CAN_ID_SLAVE  0x7F1UL
"""
BIT_TIMING = "constexpr uint32_t const CAN0_BITRATE = 500000U;\n"
DATATYPES = {"UBYTE": ("B", 1), "SBYTE": ("b", 1), "UWORD": ("H", 2), "SWORD": ("h", 2), "ULONG": ("I", 4),
             "SLONG": ("i", 4), "FLOAT32_IEEE": ("f", 4), "FLOAT64_IEEE": ("d", 8)}


# ---- the reader of this test: independent of a2lgen and xcp_master --------------------------------------------

def table(flat: list, nx: int, ny: int, mode: str) -> list:
    """z[ix][iy] of a map from its stored values under the A2L reading of the index mode (X across the columns,
    Y down the rows): ROW_DIR row after row = X running fastest, COLUMN_DIR column after column = Y fastest."""
    if len(flat) != nx * ny:
        raise ValueError(f"{len(flat)} values for {nx} x {ny}")
    if mode == "ROW_DIR":
        return [[flat[iy * nx + ix] for iy in range(ny)] for ix in range(nx)]
    if mode == "COLUMN_DIR":
        return [[flat[ix * ny + iy] for iy in range(ny)] for ix in range(nx)]
    raise ValueError(mode)


def _block(text: str, kind: str, name: str) -> str:
    m = re.search(rf"/begin {kind} {re.escape(name)}\s(.*?)/end {kind}", text, re.S)
    if m is None:
        raise AssertionError(f"{kind} {name} not in the A2L")
    return m.group(1)


def _read(elf: Elf32, address: int, dtype: str, count: int, little: bool) -> list:
    code, size = DATATYPES[dtype]
    return list(struct.unpack(("<" if little else ">") + code * count, elf.read(address, size * count)))


def _align(offset: int, dtype: str) -> int:
    size = DATATYPES[dtype][1]
    return -(-offset // size) * size


def read_map(text: str, elf: Elf32, name: str, mode: str | None = None) -> dict:
    """MAP `name` of the A2L: {'x': [...], 'y': [...], 'z': z[ix][iy], 'mode': index mode of its record layout}.
    `mode` reads the values under another index mode than the one the A2L names (for the cross-check)."""
    little = "BYTE_ORDER MSB_LAST" in text
    body = _block(text, "CHARACTERISTIC", name)
    head = re.match(r'\s*"[^"]*"\s+MAP (\S+) (\S+)', body)
    address, layout_name = int(head.group(1), 0), head.group(2)
    layout = _block(text, "RECORD_LAYOUT", layout_name)
    parts = sorted((int(pos), key, rest.split()) for key, pos, rest in
                   re.findall(r"^\s*(FNC_VALUES|AXIS_PTS_X|AXIS_PTS_Y) (\d+) ([^\n]*)", layout, re.M))
    axes = re.findall(r"/begin AXIS_DESCR (\S+) \S+ \S+ (\d+) \S+ \S+(.*?)/end AXIS_DESCR", body, re.S)
    assert len(axes) == 2, body
    points = {}
    for letter, (attribute, count, rest) in zip("XY", axes):
        if attribute == "COM_AXIS":
            ref = re.search(r"AXIS_PTS_REF (\S+)", rest).group(1)
            pts = re.match(r'\s*"[^"]*"\s+(\S+) \S+ (\S+) \S+ \S+ (\d+)', _block(text, "AXIS_PTS", ref))
            dtype = re.search(r"AXIS_PTS_X \d+ (\S+)", _block(text, "RECORD_LAYOUT", pts.group(2))).group(1)
            points[letter] = _read(elf, int(pts.group(1), 0), dtype, int(pts.group(3)), little)
        else:
            points[letter] = None                          # own breakpoints: in the record, read below
        points[f"n{letter}"] = int(count)
    offset, values, stored_mode = 0, None, None
    for _pos, key, args in parts:                          # the record in the order of the positions
        dtype = args[0]
        offset = _align(offset, dtype)
        if key == "FNC_VALUES":
            stored_mode = args[1]
            count = points["nX"] * points["nY"]
            values = _read(elf, address + offset, dtype, count, little)
        else:
            letter = key[-1]
            count = points[f"n{letter}"]
            points[letter] = _read(elf, address + offset, dtype, count, little)
        offset += DATATYPES[dtype][1] * count
    return {"x": points["X"], "y": points["Y"], "mode": stored_mode,
            "z": table(values, points["nX"], points["nY"], mode or stored_mode)}


def bilinear(xs: list, ys: list, z: list, x: float, y: float) -> float:
    def locate(pts, v):
        v = min(max(v, pts[0]), pts[-1])
        i = max(k for k in range(len(pts) - 1) if pts[k] <= v) if v < pts[-1] else len(pts) - 2
        return i, (v - pts[i]) / (pts[i + 1] - pts[i])
    i, fx = locate(xs, x)
    j, fy = locate(ys, y)
    low = z[i][j] + (z[i + 1][j] - z[i][j]) * fx
    high = z[i][j + 1] + (z[i + 1][j + 1] - z[i][j + 1]) * fx
    return low + (high - low) * fy


def data_file_rows(name: str) -> list:
    """Assignment `name = [a b c; d e f; ...]` of MapImpSwc_data.m: one row per X breakpoint."""
    text = (COMPONENT / "MapImpSwc_data.m").read_text(encoding="utf-8")
    m = re.search(rf"^{name} = \[([^\]]*)\];", text, re.M)
    return [[float(v) for v in row.split()] for row in m.group(1).split(";")]


class IndexModeReading(unittest.TestCase):
    """The reading of the index modes itself, on a table that is not square (so X and Y cannot be mixed up)."""

    def test_row_dir_runs_x_fastest_column_dir_y_fastest(self):
        # 3 X breakpoints (columns), 2 Y breakpoints (rows): the table as displayed
        #          x0  x1  x2
        #     y0 [ 11  21  31 ]
        #     y1 [ 12  22  32 ]
        z = [[11, 12], [21, 22], [31, 32]]                  # z[ix][iy]
        self.assertEqual(table([11, 21, 31, 12, 22, 32], 3, 2, "ROW_DIR"), z)
        self.assertEqual(table([11, 12, 21, 22, 31, 32], 3, 2, "COLUMN_DIR"), z)

    def test_a2lgen_names_column_dir_for_maps_only(self):
        def parameter(kind):
            return a2lgen.Parameter("m", "", "FLOAT32", 0, 12, (0, 1), 1, kind=kind)
        self.assertEqual([a2lgen.index_mode(parameter(k)) for k in ("PLAIN", "CURVE", "MAP")],
                         ["ROW_DIR", "ROW_DIR", "COLUMN_DIR"])


def _zig_available() -> bool:
    try:
        import ziglang  # noqa: F401
    except ImportError:
        return False
    return True


def zig(*args: str) -> None:
    r = subprocess.run([sys.executable, "-m", "ziglang", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"zig {args[0]}: {r.stderr[-3000:]}")


@unittest.skipUnless(_zig_available(), "ziglang not installed (.venv: pip install ziglang)")
# PLAT-62: the profile lies with the Studio's tests, and the Studio is not distributed (F12)
@unittest.skipUnless((PROFILES / "rte_t193.yaml").is_file(),
                     "tools/studio not in this tree (exported open tree, PLAT-62)")
class MapImpSwcMaps(unittest.TestCase):
    """RTE of profiles/rte_t193.yaml + component code of MapImpSwc, cross-compiled; A2L of a2lgen; own reader."""

    @classmethod
    def setUpClass(cls):
        import rtegen
        cls.tmp = Path(tempfile.mkdtemp(prefix="t196_a2l_"))
        gen = cls.tmp / "gen"
        project = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
        rtegen.generate(PROFILES / "rte_t193.yaml", project, gen, REPO,
                        REPO / "ecu" / "asyncCoreConfiguration" / "tasks.yaml")
        (cls.tmp / "start.c").write_text("void _start(void) { for (;;) {} }\n")
        src = COMPONENT / "generated" / "src"
        includes = [f"-I{gen}", f"-I{gen / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                    f"-I{REPO / 'tools/rtegen/test/can_host/stubs'}", f"-I{src}"]
        objects = []
        for source in (gen / "Rte.c", *sorted(src.glob("*.c")), cls.tmp / "start.c", gen / "RteConfig.cpp"):
            obj = cls.tmp / f"{source.stem}.o"
            lang = ["c++", "-std=c++17", "-fno-exceptions", "-fno-rtti"] if source.suffix == ".cpp" \
                else ["cc", "-std=c99"]
            zig(*lang, "-target", "arm-linux-musleabi", "-O1", "-ffreestanding", *includes, "-c", str(source),
                "-o", str(obj))
            objects.append(str(obj))
        elf = cls.tmp / "mapimp.elf"
        zig("ld.lld", "--unresolved-symbols=ignore-all", "-e", "_start", *objects, "-o", str(elf))
        xcp = cls.tmp / "xcpCanConfiguration.h"
        xcp.write_text(XCP_HEADER, encoding="utf-8")
        timing = cls.tmp / "canBitTimingConfiguration.h"
        timing.write_text(BIT_TIMING, encoding="utf-8")
        out = cls.tmp / "mapimp.a2l"
        a2lgen.generate(elf=elf, output=out, rte_config=gen / "RteConfig.h", xcp_config=xcp, bit_timing=timing,
                        name="t196_test")
        cls.text = out.read_text(encoding="utf-8")
        cls.elf = Elf32(elf)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_a2l_is_valid_and_names_column_dir_for_the_maps(self):
        self.assertEqual(a2lcheck.check_text(self.text), [], "A2L not formally valid")
        for name in ("K_map", "K_lut"):
            self.assertEqual(read_map(self.text, self.elf, PRM + name)["mode"], "COLUMN_DIR", name)
        # one-dimensional objects keep ROW_DIR (both modes give the same order there)
        self.assertIn("/begin RECORD_LAYOUT RL_FLOAT32_IEEE\n      FNC_VALUES 1 FLOAT32_IEEE ROW_DIR DIRECT",
                      self.text)

    def test_the_maps_read_as_the_a2l_says_equal_the_data_file(self):
        for name, own in (("K_map", False), ("K_lut", True)):
            m = read_map(self.text, self.elf, PRM + name)
            self.assertEqual(m["x"], [0, 1000, 2000, 3000], name)
            self.assertEqual(m["y"], [0, 50, 100], name)
            self.assertEqual(m["z"], data_file_rows(name), name)            # one row per X = z[ix][iy]

    def test_the_other_reading_does_not(self):
        for name in ("K_map", "K_lut"):
            m = read_map(self.text, self.elf, PRM + name, mode="ROW_DIR")
            self.assertNotEqual(m["z"], data_file_rows(name), name)

    def test_interpolation_equals_the_mil_result_of_tool_193(self):
        """TOOL-193, MATLAB MIL: load 50 (Y, 1st lookup input), speed 1500 (X, 2nd input) -> K_map 6.5 (x 1.5 =
        9.75) and K_lut 65."""
        kmap = read_map(self.text, self.elf, PRM + "K_map")
        self.assertAlmostEqual(bilinear(kmap["x"], kmap["y"], kmap["z"], 1500, 50), 6.5, places=5)
        lut = read_map(self.text, self.elf, PRM + "K_lut")
        self.assertAlmostEqual(bilinear(lut["x"], lut["y"], lut["z"], 1500, 50), 65.0, places=5)
        # further points against MapImpSwc_data.m, and the other reading of the index mode at the same points
        # (at the MIL point itself it happens to give 6.5 as well, so it is not used for that)
        rows = data_file_rows("K_map")
        wrong = read_map(self.text, self.elf, PRM + "K_map", mode="ROW_DIR")
        for speed, load in ((2500, 0), (1000, 100), (0, 25), (3000, 75)):
            expected = bilinear(kmap["x"], kmap["y"], rows, speed, load)
            self.assertAlmostEqual(bilinear(kmap["x"], kmap["y"], kmap["z"], speed, load), expected, places=5)
        self.assertNotAlmostEqual(bilinear(wrong["x"], wrong["y"], wrong["z"], 2500, 0),
                                  bilinear(kmap["x"], kmap["y"], rows, 2500, 0), places=3)


if __name__ == "__main__":
    unittest.main()
