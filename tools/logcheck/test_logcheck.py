# SPDX-License-Identifier: Apache-2.0
"""Host tests of tools/logcheck (SYS-16): every logger call fits one entry of the buffered logger (128 bytes).

Parts:
  1. Size model (format scanner, argument sizes) against the numbers COM-25 measured through the real serializer:
     a "%s" entry carries 112 characters (128 bytes), 113 are too many; the former "can log" call needs 129 bytes.
  2. Parser on synthetic sources (own test profile, not the repository): comments, multi-line calls, adjacent
     literals, conditional expressions, macros in the format, argument count, unbounded strings, %l / %hh.
  3. Proofs of bounds.yaml on a synthetic tree: every proof kind accepts its good case and rejects a built-in fault
     (longer literal, non-literal return, missing static_assert, a second user of a confined type, stale rule).
  4. Deliberately inserted faults: the former lines of IO-25 (TMC6200 ready), COM-25 (can log) and SYS-16 (CAN open,
     MC33771C ready, SBC ready) are reported as too long.
  5. The repository: every call is bounded and fits, every rule is proven and used.
  6. The real OpenBSW path (needs ziglang and the submodule): every call of the repository and the faults of part 4
     with worst-case arguments through EntrySerializer, EntryBuffer and PrintfFormatter - the serialized size equals
     the model byte for byte, repository lines print complete, the faults end in "<?>".

    python -m unittest tools/logcheck/test_logcheck.py
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.logcheck import logcheck as lc  # noqa: E402

OPENBSW = REPO / "external" / "openbsw"


def calls_of(source: str, bounds: lc.Bounds | None = None, file: str = "x.cpp") -> list[lc.Call]:
    return lc.parse_calls(textwrap.dedent(source), file, bounds)


def one(source: str, bounds: lc.Bounds | None = None, file: str = "x.cpp") -> lc.Call:
    calls = calls_of(source, bounds, file)
    assert len(calls) == 1, calls
    return calls[0]


def rule(expr: str, max_len: int, *proofs: dict, files: list[str] | None = None) -> lc.Rule:
    return lc.Rule("string", lc.normalize(expr), max_len, "test", list(proofs), files or [])


# Former lines, as they were before the fix (fault injection for parts 4 and 6).
OLD_LINES = {
    # COM-25: can log with eight separate bytes, bus "CAN0" (board line "... 00 <?>")
    "com25_can_log": ('Logger::info(CAN, "%s rx 0x%x%s [%u] %02x %02x %02x %02x %02x %02x %02x %02x", "CAN0", id, '
                      '"", len, d0, d1, d2, d3, d4, d5, d6, d7);', 129),
    # IO-25: TMC6200 ready with seven arguments, label "MOT1_GD" (board line "<NULL> TMC6200 ready: ... 0x<?>")
    "io25_tmc6200_ready": ('Logger::info(BSP, "%s TMC6200 ready: version 0x%02x, GSTAT 0x%04x, GCONF 0x%02x, '
                           'SHORT_CONF 0x%08x, DRV_CONF 0x%05x, " "%u register(s) written, DRV_EN off", "MOT1_GD", '
                           'v, g, c, s, d, w);', 183),
    # SYS-16: CAN open line with "CTRL1 timing" and two 16-character names
    "sys16_can_open": ('Logger::info(CAN, "%s open: %s %u bit/s, CTRL1 timing 0x%08x, bus off recovery %s", '
                       '"BUS_NAME_16_CHAR", "DEVICE_16_CHARS_", b, r, a ? "automatic" : "manual");', 135),
    # SYS-16: SBC ready line with the longest reset source name (37 characters)
    "sys16_sbc_ready": ('Logger::info(BSP, "SBC UJA113x ready: ID 0x%02x, reset source %u (%s), was %s, SPI %u Hz", '
                        'id, rss, "wake-up from forced sleep preparation", "invalid", hz);', 146),
}


class SizeModelTest(unittest.TestCase):
    """Part 1: the model against the measured entry limit (COM-25 host test can-frame-log)."""

    def test_scanner_datatypes(self):
        kinds = [p.datatype for p in lc.scan_format("%c %d %u %x %X %hx %lu %lld %p %s %% %5.2f %08x %-4d %*d")]
        self.assertEqual(kinds, [lc.UINT8, lc.SINT32, lc.UINT32, lc.UINT32, lc.UINT32, lc.UINT16, lc.UINT64,
                                 lc.SINT64, lc.VOIDPTR, lc.CHARPTR, lc.UINT32, lc.SINT32, lc.SINT32])
        self.assertEqual(lc.scan_format("%*d")[0].star_args, 1)
        self.assertEqual(lc.scan_format("%*.*s")[0].star_args, 2)
        self.assertEqual(lc.scan_format("100%% done"), [])

    def test_payload_limit_of_a_string_entry(self):
        self.assertEqual(one('Logger::info(X, "%s", "' + "a" * 112 + '");').entry_bytes(), 128)
        self.assertEqual(one('Logger::info(X, "%s", "' + "a" * 113 + '");').entry_bytes(), 129)
        self.assertEqual(one('Logger::info(X, "' + "a" * 118 + '");').entry_bytes(), 128)

    def test_argument_sizes(self):
        base = lc.HEADER_BYTES + lc.string_bytes(2, 4)
        self.assertEqual(one('Logger::info(X, "%u", 1U);').entry_bytes(), base + 5)
        self.assertEqual(one('Logger::info(X, "%c", c);').entry_bytes(), base + 2)
        self.assertEqual(one('Logger::info(X, "%hx", c);').entry_bytes(), base + 1 + 3)
        self.assertEqual(one('Logger::info(X, "%llu", c);').entry_bytes(), base + 2 + 9)
        self.assertEqual(one('Logger::info(X, "%p", p);').entry_bytes(), base + 5)
        self.assertEqual(one('Logger::info(X, "%p", p);').entry_bytes(8), base + 9)
        self.assertEqual(one('Logger::info(X, "%*d", w, v);').entry_bytes(), base + 1 + 5 + 5)

    def test_former_can_log_line(self):
        self.assertEqual(one(OLD_LINES["com25_can_log"][0]).entry_bytes(), 129)


class ParserTest(unittest.TestCase):
    """Part 2: calls in synthetic sources."""

    def test_multi_line_call_with_comments(self):
        call = one("""
            void f() {
                // Logger::info(X, "commented out %s", name);
                /* Logger::warn(X, "block comment"); */
                ::util::logger::Logger::warn(
                    LIFECYCLE,   // component
                    "step %s (limit %u ms): "
                    "%s",        /* adjacent literals */
                    cond ? "done" : "failed",
                    static_cast<unsigned>(f(1, 2)),
                    "x, y");
            }
            """)
        self.assertEqual(call.method, "warn")
        self.assertEqual(call.line, 5)
        self.assertEqual(call.fmt, "step %s (limit %u ms): %s")
        self.assertEqual([a.max_len for a in call.args if a.param.datatype == lc.CHARPTR], [6, 4])
        self.assertEqual(call.errors, [])
        self.assertEqual(call.entry_bytes(), 6 + (25 + 4) + (6 + 4) + 5 + (4 + 4))

    def test_nested_conditional_and_escapes(self):
        call = one(r'Logger::info(X, "a\tb\x41 %s", a ? (b ? "one" : "three") : "\"q\"");')
        self.assertEqual(call.fmt, "a\tbA %s")
        self.assertEqual(call.args[0].max_len, 5)

    def test_unbounded_string_is_an_error(self):
        call = one('Logger::info(X, "%s ready", _names.label);')
        self.assertIsNone(call.entry_bytes())
        self.assertIn("without bound: _names.label", call.errors[0])
        bounded = one('Logger::info(X, "%s ready", _names . label);',
                      lc.Bounds(strings=[rule("_names.label", 16, files=["x.cpp"])]))
        self.assertEqual(bounded.errors, [])
        self.assertEqual(bounded.args[0].max_len, 16)
        other_file = one('Logger::info(X, "%s ready", _names.label);',
                         lc.Bounds(strings=[rule("_names.label", 16, files=["y.cpp"])]))
        self.assertTrue(other_file.errors, "a rule for another file must not apply")

    def test_conditional_of_literal_and_bound(self):
        bounds = lc.Bounds(strings=[rule("flush->getName()", 16)])
        call = one('Logger::info(X, "%s", (flush != nullptr) ? flush->getName() : "-");', bounds)
        self.assertEqual(call.args[0].max_len, 16)

    def test_macro_in_format(self):
        src = 'Logger::info(UDS, "UDS on " NX_UDS_BUS_NAME " (0x%x)", id);'
        self.assertIn("NX_UDS_BUS_NAME without bound", one(src).errors[0])
        call = one(src, lc.Bounds(macros=[lc.Rule("macro", "NX_UDS_BUS_NAME", 16)]))
        self.assertEqual(call.fmt, "UDS on " + "M" * 16 + " (0x%x)")
        self.assertEqual(call.errors, [])

    def test_argument_count_and_unsupported_lengths(self):
        self.assertIn("1 argument(s) for 2", one('Logger::info(X, "%u %u", a);').errors[0])
        self.assertIn("64 bit", one('Logger::info(X, "%lu", a);').errors[0])
        self.assertEqual(one('Logger::info(X, "%llu", a);').errors, [])
        self.assertTrue(any("hh" in e for e in one('Logger::info(X, "%hhx", a);').errors))

    def test_non_literal_format_and_exemption(self):
        self.assertIn("format uses text without bound", one("Logger::info(X, text);").errors[0])
        self.assertIn("not a string literal", one("Logger::info(X, 42);").errors[0])
        self.assertIn("va_list", one("Logger::log(X, LEVEL_INFO, message, ap);").errors[0])
        exempt = lc.Bounds(exempt=[lc.Rule("exempt", "message", 0, "lwIP", [], ["x.cpp"])])
        call = one("Logger::log(X, LEVEL_INFO, message, ap);", exempt)
        self.assertIs(call.exempt, exempt.exempt[0])
        self.assertEqual(call.errors, [])

    def test_declarations_are_no_calls(self):
        self.assertEqual(calls_of("static void Logger::info(uint8_t component);"), [])


class RuleProofTest(unittest.TestCase):
    """Part 3: proofs on a synthetic tree - each kind with its good case and a built-in fault."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="logcheck_"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")

    def verify(self, r: lc.Rule) -> list[str]:
        return lc.verify_rule(r, self.root)

    def test_function(self):
        self.write("a/Names.cpp", """
            char const* Dev::stateName(State s);
            char const* Dev::stateName(State const s)
            {
                static char const* const names[] = {"one", "three"};
                switch (s) { case A: return "idle"; default: return (s < 2U) ? names[s] : "?"; }
            }
            """)
        proof = {"kind": "function", "file": "a/Names.cpp", "pattern": r"Dev::stateName\(",
                 "allowed_returns": ["names[s]"]}
        self.assertEqual(self.verify(rule("x", 5, proof)), [])
        self.assertIn("5 > 4", self.verify(rule("x", 4, proof))[0])
        self.assertIn("not a string literal", self.verify(rule("x", 5, {**proof, "allowed_returns": []}))[0])
        self.assertIn("no definition", self.verify(rule("x", 5, {**proof, "pattern": r"Dev::other\("}))[0])
        self.assertIn("missing", self.verify(rule("x", 5, {**proof, "file": "a/None.cpp"}))[0])

    def test_functions_over_the_tree(self):
        self.write("a/A.h", 'struct A : I { char const* getName() const override { return "alpha"; } };')
        self.write("b/B.h", 'struct B : I { char const* getName() const override { return "beta"; } };')
        self.write("b/test/Fake.h", 'struct F : I { char const* getName() const override { return _n; } };')
        proof = {"kind": "functions", "pattern": r"\bgetName\(\)\s*const\s*override\b"}
        self.assertEqual(self.verify(rule("x", 5, proof)), [], "test directories are not production code")
        self.write("c/C.h", 'struct C : I { char const* getName() const override { return _name; } };')
        self.assertIn("return _name", self.verify(rule("x", 5, proof))[0])

    def test_assigned(self):
        self.write("s/Step.cpp", 'void S::f() { _reason = "no transceiver"; if (x) { _reason = "-"; } '
                                 'bool b = (_reason == nullptr); }')
        proof = {"kind": "assigned", "file": "s/Step.cpp", "variable": "_reason"}
        self.assertEqual(self.verify(rule("reason", 14, proof)), [])
        self.assertTrue(self.verify(rule("reason", 13, proof)))
        self.write("s/Step.cpp", 'void S::f() { _reason = buffer; }')
        self.assertIn("not a string literal", self.verify(rule("reason", 14, proof))[0])

    def test_contains_ignores_comments_and_whitespace(self):
        self.write("i/Integration.cpp", """
            #define DATA(device) \\
                static_assert(sizeof(#device) - 1U <= LABEL_MAX, \\
                              "too long");
            // static_assert(commented_out);
            """)
        proof = {"kind": "contains", "file": "i/Integration.cpp",
                 "text": ["static_assert(sizeof(#device) - 1U <= LABEL_MAX,"]}
        self.assertEqual(self.verify(rule("x", 16, proof)), [])
        self.assertTrue(self.verify(rule("x", 16, {**proof, "text": "static_assert(commented_out);"})))
        self.write("gen.py", "lines = [f'#define NAME \"{bus}\"']  # don't strip\n")
        raw = {"kind": "contains", "file": "gen.py", "raw": True, "text": "f'#define NAME \"{bus}\"'"}
        self.assertEqual(self.verify(rule("x", 16, raw)), [])

    def test_confined(self):
        self.write("p/CellMonitorSystem.cpp", "CellMonitorNames const _names;")
        proof = {"kind": "confined", "pattern": r"\bCellMonitorNames\b", "files": ["p/CellMonitorSystem.cpp"]}
        self.assertEqual(self.verify(rule("x", 16, proof)), [])
        self.write("p/test/Host.cpp", "CellMonitorNames names{};")
        self.assertEqual(self.verify(rule("x", 16, proof)), [])
        self.write("p/Integration.cpp", 'constexpr CellMonitorNames N = {"bcc", "A_VERY_LONG_LABEL", "x"};')
        self.assertIn("p/Integration.cpp uses", self.verify(rule("x", 16, proof))[0])

    def test_rule_without_proof(self):
        self.assertIn("no proof", self.verify(rule("x", 16))[0])

    def test_stale_rule_and_limit_in_check(self):
        self.write("src/A.cpp", 'void f() { Logger::info(X, "%s", _name); }')
        self.write("bounds.yaml", textwrap.dedent("""
            strings:
              - expr: _name
                max: 120
                proof: {kind: contains, file: src/A.cpp, text: "_name"}
              - expr: _gone
                max: 4
                proof: {kind: contains, file: src/A.cpp, text: "_name"}
            """))
        report = lc.check(self.root, 128, self.root / "bounds.yaml")
        findings = report.findings()
        self.assertTrue(any("'_gone' is not used" in f for f in findings), findings)
        self.assertTrue(any("src/A.cpp:1: entry 136 > 128" in f for f in findings), findings)


class InsertedFaultTest(unittest.TestCase):
    """Part 4: the former too long lines are found (fault injection)."""

    def test_former_lines_are_reported(self):
        for name, (source, size) in OLD_LINES.items():
            with self.subTest(name):
                call = one(source)
                self.assertEqual(call.errors, [])
                self.assertEqual(call.entry_bytes(), size)
                report = lc.Report([call], [], [], lc.MAX_ENTRY_BYTES)
                self.assertTrue(any("> 128 bytes" in f for f in report.findings()))


class RepositoryTest(unittest.TestCase):
    """Part 5: the real calls of the repository with the real bounds.yaml."""

    @classmethod
    def setUpClass(cls):
        cls.report = lc.check()

    def test_no_findings(self):
        self.assertEqual(self.report.findings(), [])

    def test_every_call_is_evaluated_and_fits(self):
        self.assertGreaterEqual(len(self.report.calls), 90)
        for c in self.report.calls:
            with self.subTest(f"{c.file}:{c.line}"):
                if c.exempt is None:
                    self.assertIsNotNone(c.entry_bytes())
                    self.assertLessEqual(c.entry_bytes(), lc.MAX_ENTRY_BYTES)
        self.assertLessEqual(self.report.largest(), lc.MAX_ENTRY_BYTES)

    def test_scope(self):
        files = {c.file for c in self.report.calls}
        self.assertFalse(any(f.startswith("external/") for f in files))
        self.assertFalse(any("/test/" in f for f in files))
        for expected in ("platforms/s32k1xx/port/main/src/systems/SbcSystem.cpp",
                         "libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitchSystem.cpp",
                         "ecu/application/src/systems/EcuStateSystem.cpp"):
            self.assertIn(expected, files)

    def test_cli(self):
        out = subprocess.run([sys.executable, str(REPO / "tools" / "logcheck"), "check"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", check=False)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("0 finding(s)", out.stdout)


@unittest.skipUnless(importlib.util.find_spec("ziglang") is not None, "ziglang missing (tools/requirements.txt)")
@unittest.skipUnless((OPENBSW / "libs/bsw/logger/src/logger/EntrySerializer.cpp").is_file(),
                     "external/openbsw missing (git submodule update --init external/openbsw)")
class RealSerializerTest(unittest.TestCase):
    """Part 6: model = real OpenBSW entry path, byte for byte (host, pointer size 8)."""

    def test_model_matches_openbsw(self):
        report = lc.check()
        calls = [c for c in report.calls if c.exempt is None]
        faults = [one(source, file=f"fault/{name}") for name, (source, _) in OLD_LINES.items()]
        tmp = Path(tempfile.mkdtemp(prefix="logcheck_host_"))
        try:
            src = tmp / "LogEntriesHostTest.cpp"
            src.write_text(lc.host_test_source(calls + faults), encoding="utf-8")
            exe = tmp / "log_entries.exe"
            bsw = OPENBSW / "libs" / "bsw"
            argv = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
                    "-Wno-sign-conversion", "-Werror",
                    f"-isystem{bsw / 'logger' / 'include'}", f"-isystem{bsw / 'util' / 'include'}",
                    f"-isystem{OPENBSW / 'libs' / '3rdparty' / 'etl' / 'include'}", str(src),
                    str(bsw / "logger/src/logger/EntrySerializer.cpp"),
                    str(bsw / "util/src/util/format/PrintfFormatter.cpp"),
                    str(bsw / "util/src/util/format/PrintfFormatScanner.cpp"),
                    str(bsw / "util/src/util/format/PrintfArgumentReader.cpp"), "-o", str(exe)]
            build = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   check=False, timeout=900)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True, check=False, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        rows = {}
        for line in run.stdout.splitlines():
            index, size, expected, complete, where = line.split(" ", 4)
            rows[int(index)] = (int(size), int(expected), complete == "1", where)
        self.assertEqual(len(rows), len(calls) + len(faults))
        largest = 0
        for index, (size, expected, complete, where) in rows.items():
            with self.subTest(where):
                if where.startswith("fault/"):
                    # past the end the serializer cuts a string to "<?>" and counts only what it would have
                    # written after that, so the sizes agree only up to the limit; both are over it
                    self.assertFalse(complete, "a former line must end in <?>")
                    self.assertGreater(size, lc.MAX_ENTRY_BYTES)
                    self.assertGreater(expected, lc.MAX_ENTRY_BYTES)
                    if where.startswith("fault/com25_can_log"):
                        self.assertEqual(size, 129, "COM-25 measured 129 bytes for this call")
                else:
                    self.assertEqual(size, expected, "model and EntrySerializer differ")
                    self.assertTrue(complete, "a repository line is cut")
                    largest = max(largest, size)
        print(f"\n  real serializer: {len(calls)} repository calls complete, largest entry {largest} bytes; "
              f"{len(faults)} former lines cut")


if __name__ == "__main__":
    unittest.main()
