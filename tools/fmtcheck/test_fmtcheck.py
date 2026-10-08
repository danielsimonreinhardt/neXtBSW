# SPDX-License-Identifier: Apache-2.0
"""Host tests of tools/fmtcheck (IO-60): no conversion util::format reads differently than C.

Parts:
  1. Scanner model: what PrintfFormatScanner reads wrongly ('l' as 64 bit, 'hh'/'z'/'j'/'t', floating point) is an
     error, what it reads like C (%u %d %x %hx %c %s %p %% %llu, width/precision/'*') is not.
  2. Synthetic sources (own test profile, not the repository): Logger calls, member printf, conditional and
     adjacent literals, macros between literals, PRI* macros, comments, wrappers forwarding to util::format or to
     the C library, functions with the printf format attribute, format constants that go nowhere known.
  3. Deliberately inserted faults: the former lines of COM-39 (sbc sleep) and IO-54/IO-60 (pinevent show/config)
     are found at their lines.
  4. The repository: no finding; the IO-60 sites are classified as found by hand (pinevent console: util::format;
     inject/HardFault dump/clock check/reset loop: C library).

    python -m unittest tools/fmtcheck/test_fmtcheck.py
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.fmtcheck import fmtcheck as fc  # noqa: E402
from tools.logcheck.logcheck import strip_comments  # noqa: E402


def check(source: str, file: str = "src/x.cpp", extra: dict[str, str] | None = None) -> list[fc.Finding]:
    """Findings of one synthetic source; extra files take part in the wrapper search."""
    sources = [(file, strip_comments(textwrap.dedent(source)))]
    for name, text in (extra or {}).items():
        sources.append((name, strip_comments(textwrap.dedent(text))))
    sinks = fc.Sinks()
    fc.find_wrappers(sources, sinks)
    return fc.check_source(sources[0][1], file, sinks)


def errors(source: str, **kw) -> list[tuple[int, str]]:
    return [(f.line, e) for f in check(source, **kw) for e in f.errors]


class ScannerModel(unittest.TestCase):
    def test_accepted(self):
        for fmt in ("%u %d %i %x %X %o", "%hu %hx", "%c %s %p %%", "%08x %-4d %+d % d %#x", "%*d %.*s %5.2s",
                    "%llu %llx %lld", "plain text", "%S"):
            with self.subTest(fmt=fmt):
                self.assertEqual(fc.format_errors(fmt), [])

    def test_rejected(self):
        cases = {"%lu": "64 bit", "%lx": "64 bit", "%08lx": "64 bit", "%ld": "64 bit", "%-3li": "64 bit",
                 "%hhx": "'hh'", "%zu": "'z'", "%jd": "'j'", "%td": "'t'", "%Ld": "'L'",
                 "%f": "'f'", "%5.2e": "'e'", "%g": "'g'", "abc %": "end of format"}
        for fmt, text in cases.items():
            with self.subTest(fmt=fmt):
                found = fc.format_errors(fmt)
                self.assertEqual(len(found), 1, found)
                self.assertIn(text, found[0])

    def test_every_conversion_counted(self):
        self.assertEqual(len(fc.format_errors("%lu %u %lx %x %hhu")), 3)


class SyntheticSources(unittest.TestCase):
    def test_console_member_printf(self):
        found = errors('''
            void f(Out& out, uint32_t v)
            {
                (void)out.printf("value %u\\r\\n", v);
                (void)out.printf(
                    "value %lu\\r\\n",
                    static_cast<unsigned long>(v));
                ptr->printf("0x%08lx", v);
            }''')
        self.assertEqual([line for line, _ in found], [5, 8])
        self.assertIn("64 bit", found[0][1])

    def test_logger_calls(self):
        found = errors('''
            Logger::info(CAN, "ok %u", a);
            Logger::warn(CAN, "bad %lu", a);
            ::util::logger::Logger::error(CAN, "bad %lx", a);
            Logger::log(CAN, LEVEL_INFO, "bad %ld", a);
            Logger::log(CAN, LEVEL_INFO, "fine %u", a);''')
        self.assertEqual([line for line, _ in found], [3, 4, 5])

    def test_conditional_adjacent_and_macros(self):
        found = errors('''
            out.printf(armed ? "a %u" : "b %lu", x);
            out.printf("split %" "lu", x);
            out.printf("bus " NX_BUS_NAME " id 0x%x", x);
            out.printf("n %" PRIu32 "\\r\\n", x);
            out.printf("size %zu", sizeof(x));
            out.printf("volt %f", v);''')
        lines = [line for line, _ in found]
        self.assertEqual(lines, [2, 3, 5, 6, 7])
        self.assertIn("PRIu32", found[2][1])

    def test_comments_are_ignored(self):
        self.assertEqual(errors('''
            // out.printf("old %lu", x);
            /* Logger::info(X, "old %lx", x); */
            out.printf("new %u", x);'''), [])

    def test_c_library_keeps_long(self):
        findings = check('''
            (void)printf("reset %lu\\r\\n", static_cast<unsigned long>(x));
            std::snprintf(buf, sizeof(buf), "core %lu Hz", hz);
            printf("n %" PRIu32 "\\r\\n", n);''')
        self.assertEqual([f.sink for f in findings], [fc.LIBC] * 3)
        self.assertTrue(all(not f.errors for f in findings))

    def test_format_attribute_function_is_c_library(self):
        findings = check('''
            __attribute__((format(printf, 2, 3)))
            void writeLine(Writer w, char const* const format, ...)
            {
                va_list args;
                va_start(args, format);
                (void)vsnprintf(line, sizeof(line), format, args);
                va_end(args);
            }
            void g() { writeLine(w, "PC 0x%08lx", pc); }''')
        self.assertEqual([(f.sink, f.errors) for f in findings if f.fmt and "PC" in f.fmt], [(fc.LIBC, [])])

    def test_wrapper_to_util_format(self):
        wrapper = '''
            extern "C" void log_bridge(char const* message, ...)
            {
                va_list ap;
                va_start(ap, message);
                Logger::log(LWIP, LEVEL_INFO, message, ap);
                va_end(ap);
            }'''
        found = errors('''
            void g()
            {
                log_bridge("ok %u", a);
                log_bridge("bad %lu", a);
            }''', extra={"src/bridge.cpp": wrapper})
        self.assertEqual([line for line, _ in found], [5])

    def test_wrapper_of_wrapper(self):
        extra = {
            "src/a.cpp": '''
                void inner(int level, char const* fmt, ...)
                {
                    va_list ap; va_start(ap, fmt); Logger::log(X, level, fmt, ap); va_end(ap);
                }''',
            "src/b.cpp": '''
                void outer(char const* text, ...)
                {
                    inner(1, text);
                }''',
        }
        self.assertEqual([line for line, _ in errors('outer("bad %lx", v);', extra=extra)], [1])

    def test_literal_without_known_destination(self):
        found = errors('''
            static constexpr char const* FORMAT = "%lu events";
            static constexpr char const* OK = "%u events";
            char const* const TEXT = "50 % load";''')
        self.assertEqual([line for line, _ in found], [2])
        self.assertIn("no known printf", found[0][1])

    def test_format_not_a_literal_is_listed(self):
        findings = check('Logger::info(CAN, ::can::CanFrameLog::FORMAT, text);')
        self.assertEqual(len(findings), 1)
        self.assertIsNone(findings[0].fmt)
        self.assertEqual(findings[0].errors, [])


class InsertedFaults(unittest.TestCase):
    """The lines that went wrong on the board must be found."""

    COM39 = '''
        (void)out.printf(
            "SBC Sleep in %u ms, wake-up by CAN frame 0x%lx mask 0x%lx\\r\\n",
            static_cast<unsigned>(SLEEP_DELAY_MS),
            static_cast<unsigned long>(filter->id),
            static_cast<unsigned long>(filter->idCompareMask));'''

    IO60 = '''
        template<class Out>
        void writeShow(Out& out, PinEvents const& events)
        {
            (void)out.printf(
                "%s: count %lu, last %lu us, qlf %s (%u), trips %lu, bounces %lu, glitches %lu, ",
                name, a, b, c, d, e, f, g);
            (void)out.printf("off, re-arm in %lu ms\\r\\n", static_cast<unsigned long>(x));
        }
        template<class Out>
        void writeConfig(Out& out, PinEvents const& events)
        {
            (void)out.printf(
                "%s: edge %s, debounce %lu us, limit %lu per %lu us, re-arm after %lu us, detector %s\\r\\n",
                a, b, c, d, e, f, g);
        }'''

    def test_com39_sbc_sleep_line(self):
        found = errors(self.COM39, file="platforms/x/SbcSystem.cpp")
        self.assertEqual([line for line, _ in found], [2, 2])

    def test_io60_pinevent_lines(self):
        found = errors(self.IO60, file="libs/bsp/bspPinEvent/include/pinEvent/PinEventConsole.h")
        per_line = {}
        for line, _ in found:
            per_line[line] = per_line.get(line, 0) + 1
        # show: count, last, trips, bounces, glitches; re-arm; config: debounce, limit, per, re-arm
        self.assertEqual(per_line, {5: 5, 8: 1, 13: 4})

    def test_repository_copy_with_fault_is_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "libs" / "bsp" / "x" / "include"
            src.mkdir(parents=True)
            (src / "Console.h").write_text(textwrap.dedent(self.IO60), encoding="utf-8")
            (Path(tmp) / "libs" / "bsp" / "x" / "test").mkdir()
            # test directories are not scanned (host tests print with the C library)
            (Path(tmp) / "libs" / "bsp" / "x" / "test" / "T.cpp").write_text('out.printf("%lu", x);',
                                                                           encoding="utf-8")
            findings = fc.scan_repo(Path(tmp))
            self.assertEqual({f.file for f in findings if f.errors}, {"libs/bsp/x/include/Console.h"})
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(fc.main(["--repo", tmp]), 1)
            self.assertIn("Console.h:5: %lu: util::format reads 'l' as 64 bit", out.getvalue())


class Repository(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.findings = fc.scan_repo(REPO)

    def test_no_finding(self):
        bad = [f"{f.file}:{f.line}: {e}" for f in self.findings for e in f.errors]
        self.assertEqual(bad, [], "\n".join(bad))

    def test_io60_sites_classified(self):
        def sinks_of(file: str) -> set[str]:
            return {f.sink for f in self.findings if f.file == file}

        self.assertEqual(sinks_of("libs/bsp/bspPinEvent/include/pinEvent/PinEventConsole.h"), {fc.SINK})
        self.assertEqual(sinks_of("platforms/s32k1xx/port/main/src/integration/SafetyFaultInjectionCommand.cpp"),
                         {fc.LIBC})
        self.assertEqual(sinks_of("platforms/s32k1xx/hardFaultHandler/src/HardFaultDump.cpp"), {fc.LIBC})
        self.assertEqual(sinks_of("platforms/common/clockCheck/src/nxClockCheckReport.cpp"), {fc.LIBC})
        sbc = [f for f in self.findings if f.file == "platforms/s32k1xx/port/main/src/systems/SbcSystem.cpp"
               and f.fmt and "wake-up by CAN frame" in f.fmt]
        self.assertEqual([(f.sink, f.errors) for f in sbc], [(fc.SINK, [])])

    def test_console_and_logger_calls_found(self):
        self.assertGreater(sum(1 for f in self.findings if f.sink == fc.SINK and f.via.startswith("Logger::")), 50)
        self.assertGreater(sum(1 for f in self.findings if f.sink == fc.SINK and f.via == ".printf"), 100)


if __name__ == "__main__":
    unittest.main()
