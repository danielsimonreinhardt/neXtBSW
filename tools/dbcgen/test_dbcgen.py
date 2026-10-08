"""Unit and host tests for tools/dbcgen.

The pack/unpack test generates C code from test/pack_test.dbc (Intel/Motorola, signed, 1..32 bit, factor/offset,
extended ID), compiles it together with a generated test driver (zig cc, -Wconversion -Werror) and compares it with
cantools as independent reference for the bit layout. The conversions are compared with the exact Python model
(fractions) that is itself checked against cantools.

Run: .venv/Scripts/python -m unittest tools/dbcgen/test_dbcgen.py
"""

import math
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import cantools

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import dbcgen  # noqa: E402

PACK_DBC = HERE / "test" / "pack_test.dbc"
# PLAT-62: the demo CAN matrix of the own test profile (tools/nxconfig/test/profiles/projects/testproject_full) instead
# of the eval board project's DBC - the eval board stays private (PLAT-46 F10). The eval board's DBC is checked as well
# where it exists (private tree), the exported open tree skips that part.
DEMO_DBC = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "TestFull.dbc"
EVAL_DBC = REPO / "projects" / "evalboard_s32k144_demo" / "EvalBoard.dbc"

DBC_HEADER = """VERSION ""
NS_ :
BS_:
BU_: ECU TESTER
"""


def c_int(value: int) -> str:
    if value < 0:
        return f"(INT64_C({value + 1}) - 1)"
    return f"INT64_C({value})"


def c_double(value: float) -> str:
    if math.isnan(value):
        return "NAN"
    if math.isinf(value):
        return "INFINITY" if value > 0 else "-INFINITY"
    return repr(value)


class DbcErrorsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load_text(self, body):
        path = self.tmp / "t.dbc"
        path.write_text(DBC_HEADER + body, encoding="utf-8")
        return dbcgen.load(path)

    def expect_problem(self, body, fragment):
        db = self.load_text(body)
        with self.assertRaises(dbcgen.DbcError) as ctx:
            dbcgen.render(db.messages, "t.dbc")
        self.assertIn(fragment, str(ctx.exception))

    def test_range_outside_encodable(self):
        self.expect_problem('BO_ 1 M: 8 ECU\n SG_ s : 0|8@1+ (1,0) [0|300] "" TESTER\n',
                            "does not fit 8 bit unsigned")

    def test_signed_range_with_offset(self):
        self.expect_problem('BO_ 1 M: 8 ECU\n SG_ s : 0|8@1- (0.5,10) [-60|100] "" TESTER\n',
                            "encodable: -54..73.5")

    def test_too_long_signal(self):
        self.expect_problem('BO_ 1 M: 8 ECU\n SG_ s : 0|33@1+ (1,0) [0|0] "" TESTER\n', "at most 32 bit")

    def test_multiplexed_message(self):
        self.expect_problem('BO_ 1 M: 8 ECU\n SG_ sel M : 0|8@1+ (1,0) [0|0] "" TESTER\n'
                            ' SG_ a m1 : 8|8@1+ (1,0) [0|0] "" TESTER\n', "multiplexed messages are not supported")

    def test_float_signal(self):
        self.expect_problem('BO_ 1 M: 8 ECU\n SG_ s : 0|32@1+ (1,0) [0|0] "" TESTER\n'
                            'SIG_VALTYPE_ 1 s : 1;\n', "IEEE float signals are not supported")

    def test_start_value_out_of_range(self):
        self.expect_problem('BO_ 1 M: 8 ECU\n SG_ s : 0|4@1+ (1,0) [0|0] "" TESTER\n'
                            'BA_DEF_ SG_  "GenSigStartValue" FLOAT -1000 1000;\nBA_DEF_DEF_  "GenSigStartValue" 0;\n'
                            'BA_ "GenSigStartValue" SG_ 1 s 16;\n', "GenSigStartValue 16")

    def test_problem_only_matters_when_generated(self):
        db = self.load_text('BO_ 1 Bad: 8 TESTER\n SG_ s : 0|40@1+ (1,0) [0|0] "" TESTER\n'
                            'BO_ 2 Good: 8 ECU\n SG_ s : 0|8@1+ (1,0) [0|0] "" TESTER\n')
        rendered = dbcgen.render(db.for_node("ECU"), "t.dbc")
        self.assertIn("CanDb_Good_Pack", rendered["CanDb.c"])
        self.assertNotIn("Bad", rendered["CanDb.c"])

    def test_overlapping_signals(self):
        with self.assertRaises(dbcgen.DbcError) as ctx:
            self.load_text('BO_ 1 M: 8 ECU\n SG_ a : 0|8@1+ (1,0) [0|0] "" TESTER\n'
                           ' SG_ b : 4|8@1+ (1,0) [0|0] "" TESTER\n')
        self.assertIn("overlap", str(ctx.exception))

    def test_unknown_node(self):
        db = dbcgen.load(DEMO_DBC)
        with self.assertRaises(dbcgen.DbcError) as ctx:
            db.for_node("XYZ")
        self.assertIn("node XYZ is not defined", str(ctx.exception))

    def test_missing_file(self):
        with self.assertRaises(dbcgen.DbcError):
            dbcgen.load(self.tmp / "missing.dbc")


class DemoDbcTest(unittest.TestCase):
    def test_demo_dbc(self):
        self.check_demo_matrix(DEMO_DBC)

    @unittest.skipUnless(EVAL_DBC.is_file(), "private eval board project not in this tree (exported open tree, PLAT-62)")
    def test_eval_board_dbc(self):
        self.check_demo_matrix(EVAL_DBC)

    def check_demo_matrix(self, path):
        db = dbcgen.load(path)
        status = db.message("EVB_Status")
        self.assertEqual((status.frame_id, status.cycle_ms, status.senders), (0x110, 100, ["EVB"]))
        command = db.message("PC_Command")
        self.assertEqual((command.cycle_ms, command.timeout_ms), (100, 500))
        setpoint = command.signal("Setpoint_mV")
        self.assertEqual((setpoint.factor, setpoint.offset, setpoint.initial_raw), (Fraction(1, 2), 1000, 3000))
        self.assertEqual(setpoint.to_physical(setpoint.initial_raw), 2500)
        self.assertEqual({m.name for m in db.for_node("EVB")}, {"EVB_Status", "EVB_Analog", "PC_Command"})
        # no IDs of the bootloader/UDS (bl_config.h) and the wake-up test frame
        self.assertFalse({m.frame_id for m in db.messages} & {0x7DF, 0x7E0, 0x7E8, 0x7FF})

    def test_generation_is_stable(self):
        db = dbcgen.load(DEMO_DBC)
        self.assertEqual(dbcgen.render(db.messages, "x"), dbcgen.render(dbcgen.load(DEMO_DBC).messages, "x"))

    def test_reference_model_matches_cantools(self):
        """Signal.to_raw/to_physical (reference for the C tests) agree with cantools inside the value range."""
        rng = random.Random(7)
        ours = dbcgen.load(PACK_DBC)
        theirs = cantools.database.load_file(str(PACK_DBC))
        for m in ours.messages:
            ref = theirs.get_message_by_name(m.name)
            for s in m.signals:
                rs = ref.get_signal_by_name(s.name)
                for _ in range(200):
                    raw = rng.randint(s.raw_lo, s.raw_hi)
                    self.assertAlmostEqual(float(s.to_physical(raw)), rs.conversion.raw_to_scaled(raw), places=6)
                    # physical values away from .5 steps: cantools rounds half to even
                    phys = (raw + rng.choice((-0.3, -0.1, 0.0, 0.2, 0.4))) * float(s.factor) + float(s.offset)
                    if s.minimum <= Fraction(phys) <= s.maximum:
                        self.assertEqual(s.to_raw(phys), rs.conversion.scaled_to_raw(phys), (m.name, s.name, phys))


class PackHostTest(unittest.TestCase):
    """Generated C against cantools (bit layout) and the fraction model (conversions)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build_driver(self, db, ref):
        rng = random.Random(2026)
        lines = ['#include "CanDb.h"', "#include <math.h>", "#include <stdio.h>", "#include <string.h>", "",
                 "static unsigned checks, failures;",
                 "#define CHECK(c, ...) do { ++checks; if (!(c)) { ++failures; printf(\"FAIL line %d: \", __LINE__);"
                 " printf(__VA_ARGS__); printf(\"\\n\"); } } while (0)", ""]
        body = []
        for m in db.messages:
            rm = ref.get_message_by_name(m.name)
            p = f"CanDb_{m.name}"
            # pack: random raw values over the full bit range, expected bytes from cantools
            vectors = []
            for i in range(60):
                values = {}
                for s in m.signals:
                    pick = rng.random()
                    values[s.name] = s.bit_min if pick < 0.1 else s.bit_max if pick < 0.2 else \
                        rng.randint(s.bit_min, s.bit_max)
                data = rm.encode(values, scaling=False, strict=False, padding=False)
                vectors.append((values, data))
            lines.append(f"static int {m.name}_equal({p}_t const* a, {p}_t const* b)")
            lines.append("{ return 1" + "".join(f" && (a->{s.name} == b->{s.name})" for s in m.signals) + "; }")
            lines.append(f"static {p}_t const {m.name}_raw[] = {{")
            for values, _ in vectors:
                lines.append("    {" + ", ".join(f"({s.ctype}){c_int(values[s.name])}" for s in m.signals) + "},")
            lines.append("};")
            lines.append(f"static uint8_t const {m.name}_bytes[][{m.length}] = {{")
            for _, data in vectors:
                lines.append("    {" + ", ".join(f"0x{b:02X}U" for b in data) + "},")
            lines.append("};")
            # unpack of arbitrary bytes (also bits outside of signals set)
            frames = [bytes(rng.getrandbits(8) for _ in range(m.length)) for _ in range(60)]
            decoded = [rm.decode(f, scaling=False, decode_choices=False) for f in frames]
            lines.append(f"static uint8_t const {m.name}_random[][{m.length}] = {{")
            for f in frames:
                lines.append("    {" + ", ".join(f"0x{b:02X}U" for b in f) + "},")
            lines.append("};")
            lines.append(f"static {p}_t const {m.name}_random_raw[] = {{")
            for d in decoded:
                lines.append("    {" + ", ".join(f"({s.ctype}){c_int(int(d[s.name]))}" for s in m.signals) + "},")
            lines.append("};")
            body += [
                f"    for (unsigned i = 0U; i < {len(vectors)}U; ++i) {{",
                f"        uint8_t data[{m.length}]; {p}_t msg;",
                f"        memset(data, 0xA5, sizeof(data));",
                f"        {p}_Pack(&{m.name}_raw[i], data);",
                f'        CHECK(memcmp(data, {m.name}_bytes[i], sizeof(data)) == 0, "{m.name} pack vector %u", i);',
                f"        {p}_Unpack(&msg, {m.name}_bytes[i]);",
                f'        CHECK({m.name}_equal(&msg, &{m.name}_raw[i]), "{m.name} unpack vector %u", i);',
                f"        {p}_Unpack(&msg, {m.name}_random[i]);",
                f'        CHECK({m.name}_equal(&msg, &{m.name}_random_raw[i]), "{m.name} random %u", i);',
                "    }",
            ]
            init = {s.name: s.initial_raw for s in m.signals}
            body += [f"    {{ {p}_t msg; {p}_Init(&msg);"]
            for s in m.signals:
                body.append(f'      CHECK(msg.{s.name} == ({s.ctype}){c_int(init[s.name])}, "{s.name} init");')
            body.append("    }")

            for s in m.signals:
                f = f"{p}_{s.name}"
                raws = [s.bit_min, s.bit_max, 0, s.raw_lo, s.raw_hi] + [rng.randint(s.bit_min, s.bit_max)
                                                                           for _ in range(40)]
                for raw in raws:
                    phys = s.to_physical(raw)
                    if s.integer:
                        body.append(f'    CHECK({f}_ToInt(({s.ctype}){c_int(raw)}) == {c_int(int(phys))}, '
                                    f'"{f}_ToInt({raw})");')
                    body.append(f"    CHECK(fabs({f}_ToDouble(({s.ctype}){c_int(raw)}) - {c_double(float(phys))}) "
                                f'<= 1e-9 * (1.0 + fabs({c_double(float(phys))})), "{f}_ToDouble({raw})");')
                ints = [-(2**63), 2**63 - 1, 0, math.floor(s.minimum) - 5, math.ceil(s.maximum) + 5,
                        math.floor(s.minimum), math.ceil(s.maximum)]
                ints += [rng.randint(math.floor(s.minimum) - 10, math.ceil(s.maximum) + 10) for _ in range(60)]
                for v in ints:
                    body.append(f'    CHECK({f}_FromInt({c_int(v)}) == ({s.ctype}){c_int(s.to_raw(v))}, '
                                f'"{f}_FromInt({v}) = %lld", (long long){f}_FromInt({c_int(v)}));')
                doubles = [float(s.minimum) - 1e6, float(s.maximum) + 1e6, float(s.minimum), float(s.maximum)]
                for _ in range(60):
                    k = rng.randint(s.raw_lo - 3, s.raw_hi + 3)
                    frac = rng.choice((0.0, 0.1, 0.3, 0.45, 0.55, 0.7, 0.9))
                    doubles.append((k + frac) * float(s.factor) + float(s.offset))
                for v in doubles:
                    body.append(f'    CHECK({f}_FromDouble({c_double(v)}) == ({s.ctype}){c_int(s.to_raw(v))}, '
                                f'"{f}_FromDouble({v!r}) = %lld", (long long){f}_FromDouble({c_double(v)}));')
                body.append(f'    CHECK({f}_FromDouble(NAN) == ({s.ctype}){c_int(s.raw_lo)}, "{f} NaN");')
        lines += ["", "int main(void)", "{"] + body + [
            '    printf("%u checks, %u failures\\n", checks, failures);', "    return failures ? 1 : 0;", "}", ""]
        return "\n".join(lines)

    def test_pack_unpack_and_conversions(self):
        db = dbcgen.load(PACK_DBC)
        ref = cantools.database.load_file(str(PACK_DBC))
        out = self.tmp / "gen"
        dbcgen.write_files(out, dbcgen.render(db.messages, PACK_DBC.name))
        header = (out / "CanDb.h").read_text(encoding="utf-8")
        self.assertIn("#define CANDB_MOTOROLAMSG_ID         0x98FF1234UL", header)
        self.assertIn("#define CANDB_MOTOROLAMSG_TIMEOUT_MS 200U", header)
        self.assertIn("#define CANDB_INTELMSG_TIMEOUT_MS 60U", header)
        (out / "driver.c").write_text(self.build_driver(db, ref), encoding="utf-8")
        exe = out / "pack_test.exe"
        compile_cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Wconversion",
                       "-Wpedantic", "-Werror", "-Wno-overlength-strings", f"-I{out}", str(out / "CanDb.c"),
                       str(out / "driver.c"), "-o", str(exe)]
        result = subprocess.run(compile_cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + run.stdout.strip().splitlines()[-1] if run.stdout else "", file=sys.stderr)
        self.assertEqual(run.returncode, 0, run.stdout[-4000:])
        self.assertIn(", 0 failures", run.stdout)


if __name__ == "__main__":
    unittest.main()
