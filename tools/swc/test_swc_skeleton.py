# SPDX-License-Identifier: Apache-2.0
"""Host tests of the code skeleton and source check of hand-written C components (RTE-20), without MATLAB.

  SkeletonTest     create() writes src/<Swc>.c once and never again (not over a changed file, not next to another
                   source), refuses Simulink components; the skeleton compiles against the RTE rtegen generates for
                   it (-Wall -Wextra -Werror), and so do its commented example accesses once uncommented - every port
                   type, parameters as scalar, vector and fixed point
  SourceCheckTest  check(): missing, static, duplicate and wrongly declared runnable functions, external void(void)
                   functions no runnable belongs to, comments and strings ignored, never a change to the file; the
                   command line (exit codes, --strict)
  ImplementationTest  the implementation key of swc.yaml (default simulink, unknown value, C keywords, 'Init',
                   swc_yaml.py json refuses a C component, implementation_of for rtegen)

Run: .venv/Scripts/python -m unittest tools/swc/test_swc_skeleton.py
"""

import contextlib
import io
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "rtegen"))

import swc_skeleton  # noqa: E402
import swc_yaml  # noqa: E402

ALL_TYPES = """version: 1
name: AllSwc
description: every port type and parameter form
implementation: c
runnables:
  - { name: Fast, period_ms: 5 }
  - { name: Slow, period_ms: 1000 }
ports:
"""


def all_types_swc(folder: Path) -> Path:
    """C component AllSwc: an in and an out port of every platform type, parameters scalar/vector/fixed point."""
    lines = [ALL_TYPES]
    for t in swc_yaml.TYPES:
        lines.append(f"  - {{ name: In_{t}, direction: in, type: {t}, runnables: [Fast, Slow] }}\n")
        lines.append(f"  - {{ name: Out_{t}, direction: out, type: {t}, runnables: [Fast] }}\n")
    # RTE-25: array ports, one with the expression of the init data it was resolved from
    lines.append("  - { name: In_Cells, direction: in, type: uint16, runnables: [Slow], dimension: 4 }\n"
                 "  - { name: Out_Cells, direction: out, type: float32, runnables: [Slow], dimension: 3, "
                 "dimension_source: pack.n }\n")
    lines.append("parameters:\n"
                 "  - { name: Limit, type: float32, runnables: [Fast] }\n"
                 "  - { name: Curve, type: sint16, dim: 3, runnables: [Slow] }\n"
                 "  - { name: Temp, type: sint16, lsb: 0.1, offset: -40, runnables: [Fast, Slow] }\n")
    folder.mkdir(parents=True)
    (folder / "swc.yaml").write_text("".join(lines), encoding="utf-8")
    (folder / "AllSwc_data.m").write_text("Limit = 2.5;\nCurve = [1 -2 3];\nTemp = 20;\n", encoding="utf-8")
    return folder


class SkeletonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_skel_"))
        self.folder = all_types_swc(self.tmp / "AllSwc")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_created_once_never_overwritten(self):
        result = swc_skeleton.create(self.folder)
        self.assertTrue(result.created and result.ok, result.note)
        self.assertEqual(result.file, self.folder / "src" / "AllSwc.c")
        text = result.file.read_text(encoding="utf-8")
        for needle in ('#include "Rte_AllSwc.h"', "void AllSwc_Init(void)", "void AllSwc_Fast(void)",
                       "void AllSwc_Slow(void)", "(void)Rte_Read_In_uint8_In_uint8(&in_uint8);",
                       "(void)Rte_Write_Out_boolean_Out_boolean(FALSE);", "(void)Rte_Write_Out_float32_Out_float32(0.0F);",
                       "float32 const limit = Rte_Prm_CalPrm_Limit();",
                       "sint16 const* const curve = Rte_Prm_CalPrm_Curve();   (3 elements)",
                       "physical = stored * 0.1 + -40", "every 5 ms", "every 1 s"):
            self.assertIn(needle, text)
        self.assertNotIn("/*", "".join(re.findall(r"/\*(.*?)\*/", text, re.S)), "no comment inside a comment")
        self.assertTrue(all(len(line) <= 120 for line in text.splitlines()))
        # the developer edits it; create again: nothing is written
        result.file.write_text("/* mine */\n", encoding="utf-8")
        again = swc_skeleton.create(self.folder)
        self.assertFalse(again.created)
        self.assertIn("already in src/", again.note)
        self.assertEqual(result.file.read_text(encoding="utf-8"), "/* mine */\n")
        # another file name in src/: no second skeleton next to it
        result.file.rename(self.folder / "src" / "other.c")
        self.assertFalse(swc_skeleton.create(self.folder).created)
        self.assertFalse(result.file.exists())

    def test_simulink_component_is_refused(self):
        text = (self.folder / "swc.yaml").read_text(encoding="utf-8").replace("implementation: c\n", "")
        (self.folder / "swc.yaml").write_text(text, encoding="utf-8")
        result = swc_skeleton.create(self.folder)
        self.assertFalse(result.created or result.ok)
        self.assertEqual(result.messages[-1].path, ("implementation",))
        self.assertIn("not in C", result.messages[-1].text)
        self.assertFalse((self.folder / "src").exists())

    def test_description_errors_are_reported_not_written(self):
        (self.folder / "swc.yaml").write_text("name: AllSwc\nimplementation: c\nruns: 1\n", encoding="utf-8")
        result = swc_skeleton.create(self.folder)
        self.assertFalse(result.ok)
        self.assertIn(("runs",), [m.path for m in result.messages])
        self.assertFalse((self.folder / "src").exists())

    def compile(self, source: Path, extra: list[str]) -> subprocess.CompletedProcess:
        import rtegen
        project = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
        ecu = self.tmp / "rte.yaml"
        connections = {f"AllSwc.In_{t}.In_{t}": "rte.init" for t in swc_yaml.TYPES}
        connections.update({f"AllSwc.Out_{t}.Out_{t}": "rte" for t in swc_yaml.TYPES})
        connections.update({"AllSwc.In_Cells.In_Cells": "rte.init", "AllSwc.Out_Cells.Out_Cells": "rte"})
        import yaml
        # RTE-22: Format 2, die Komponente liegt neben dieser rte.yaml
        ecu.write_text(yaml.safe_dump({"schema_version": 2,
                                       "components": [{"name": "AllSwc", "path": "AllSwc"}],
                                       "connections": connections}), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            rtegen.generate(ecu, project, self.tmp / "gen", self.tmp,
                            REPO / "ecu" / "asyncCoreConfiguration" / "tasks.yaml")
        return subprocess.run([sys.executable, "-m", "ziglang", "cc", "-std=c99", "-Wall", "-Wextra", "-Werror",
                               "-pedantic", *extra, f"-I{self.tmp / 'gen'}",
                               f"-I{REPO / 'libs/bsw/autosarBase/include'}", "-c", str(source),
                               "-o", str(self.tmp / "skeleton.o")], capture_output=True, text=True)

    def test_skeleton_and_its_examples_compile_against_the_generated_rte(self):
        source = swc_skeleton.create(self.folder).file
        r = self.compile(source, [])
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        # uncomment the example accesses: the calls are the API of Rte_AllSwc.h
        text = source.read_text(encoding="utf-8")
        text = re.sub(r"    /\* Example accesses, replace them by the logic of the runnable:\n((?:     \*     .*\n)+)"
                      r"     \*/\n",
                      lambda m: "".join("    " + re.sub(r"   \(.*\)$", "", line[len("     *     "):]) + "\n"
                                        for line in m.group(1).splitlines()), text)
        self.assertNotIn("Example accesses", text)
        self.assertIn("    (void)Rte_Read_In_sint32_In_sint32(&in_sint32);\n", text)
        self.assertIn("    uint16 in_Cells[4] = {0U};\n    (void)Rte_Read_In_Cells_In_Cells(in_Cells);\n", text)
        self.assertIn("    float32 out_Cells[3] = {0.0F};\n"
                      "    (void)Rte_Write_Out_Cells_Out_Cells(out_Cells);\n", text)
        uncommented = self.tmp / "uncommented.c"
        uncommented.write_text(text, encoding="utf-8")
        r = self.compile(uncommented, ["-Wno-unused-variable", "-Wconversion"])
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])


class SourceCheckTest(unittest.TestCase):
    SWC = ("name: CheckSwc\nimplementation: c\nrunnables:\n  - { name: Run10ms, period_ms: 10 }\n"
           "  - { name: Run20ms, period_ms: 20 }\n")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_skelchk_"))
        self.folder = self.tmp / "CheckSwc"
        (self.folder / "src").mkdir(parents=True)
        (self.folder / "swc.yaml").write_text(self.SWC, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, **sources) -> list:
        for name, text in sources.items():
            (self.folder / "src" / f"{name}.c").write_text(text, encoding="utf-8")
        before = {p: p.read_bytes() for p in (self.folder / "src").glob("*.c")}
        messages = swc_skeleton.check(self.folder)
        self.assertEqual(before, {p: p.read_bytes() for p in (self.folder / "src").glob("*.c")}, "never rewrites")
        return messages

    def test_matching_source_has_no_finding(self):
        self.assertEqual(self.check(CheckSwc="void CheckSwc_Init(void) {}\nvoid CheckSwc_Run10ms(void)\n{\n}\n"
                                             "void\nCheckSwc_Run20ms ( void ) { if (1) { } }\n"
                                             "static void helper(void) {}\nint compute(int x) { return x; }\n"), [])

    def test_findings(self):
        messages = self.check(
            CheckSwc='/* void CheckSwc_Run20ms(void) { } is only a comment */\n'
                     'static const char* text = "void CheckSwc_Run20ms(void) {";\n'
                     "void CheckSwc_Init(void) {}\n"
                     "static void CheckSwc_Run10ms(void) {}\n"
                     "void Run5ms(void) {}\n"
                     "#define DEF void CheckSwc_Run20ms(void) {\n",
            second="int CheckSwc_Init(void) { return 0; }\n")
        texts = [(m.severity, m.file.name, m.path, m.line, m.text) for m in messages]
        self.assertTrue(all(m.severity == "warning" for m in messages), texts)
        by_text = {m.text: m for m in messages}
        missing = next(m for t, m in by_text.items() if "runnable Run20ms" in t)
        self.assertIn("function void CheckSwc_Run20ms(void) is missing", missing.text)
        self.assertEqual((missing.file.name, missing.path, missing.line), ("swc.yaml", ("runnables", 1, "name"), 5))
        static = next(m for t, m in by_text.items() if "is static" in t)
        self.assertEqual((static.file.name, static.line), ("CheckSwc.c", 4))
        twice = next(m for t, m in by_text.items() if "defined 2 times" in t)
        self.assertEqual(twice.path, ("name",))
        wrong = next(m for t, m in by_text.items() if "must be defined as void CheckSwc_Init(void)" in t)
        self.assertEqual((wrong.file.name, wrong.line), ("second.c", 1))
        extra = next(m for t, m in by_text.items() if "function Run5ms(void) is no runnable" in t)
        self.assertEqual((extra.file.name, extra.line), ("CheckSwc.c", 5))
        self.assertEqual(len(messages), 5, texts)

    def test_empty_parameter_list_is_no_runnable_form(self):
        messages = self.check(CheckSwc="void CheckSwc_Init() {}\nvoid CheckSwc_Run10ms(void) {}\n"
                                       "void CheckSwc_Run20ms(void) {}\n")
        self.assertEqual(len(messages), 1)
        self.assertIn("not void CheckSwc_Init()", messages[0].text)

    def test_no_source(self):
        messages = self.check()
        self.assertEqual(len(messages), 1)
        self.assertIn("no C source in src/", messages[0].text)
        self.assertIn("swc_skeleton.py create", messages[0].text)

    def test_command_line(self):
        (self.folder / "src" / "CheckSwc.c").write_text("void CheckSwc_Init(void) {}\n", encoding="utf-8")
        run = [sys.executable, str(HERE / "swc_skeleton.py")]
        r = subprocess.run(run + ["check", str(self.folder)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.count("warning:"), 2)
        r = subprocess.run(run + ["check", "--strict", str(self.folder / "swc.yaml")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        (self.folder / "swc.yaml").write_text("name: CheckSwc\nimplementation: c\n", encoding="utf-8")
        r = subprocess.run(run + ["check", str(self.folder)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("(runnables): error:", r.stderr)
        r = subprocess.run(run + ["create", str(self.folder)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)


class ImplementationTest(unittest.TestCase):
    BASE = {"name": "ImplSwc", "runnables": [{"name": "Run10ms", "period_ms": 10}],
            "ports": [{"name": "In", "direction": "in", "type": "uint8", "runnables": ["Run10ms"]}]}

    def check(self, **changes):
        data = {**self.BASE, **changes}
        return swc_yaml.check(data)

    def test_default_is_simulink(self):
        swc, _ = self.check()
        self.assertEqual(swc.implementation, "simulink")
        self.assertEqual(swc.symbol_of("Run10ms"), "Run10ms")
        swc, _ = self.check(implementation="c")
        self.assertEqual((swc.implementation, swc.symbol_of("Run10ms"), swc.init_runnable),
                         ("c", "ImplSwc_Run10ms", "ImplSwc_Init"))

    def test_unknown_value(self):
        swc, messages = self.check(implementation="rust")
        self.assertIsNone(swc)
        self.assertEqual([(m.path, m.severity) for m in messages], [(("implementation",), "error")])

    def test_c_keywords_and_init(self):
        swc, messages = self.check(runnables=[{"name": "switch", "period_ms": 10}], ports=[])
        self.assertIsNone(swc)
        self.assertIn("reserved word of C", messages[0].text)
        swc, messages = self.check(implementation="c", runnables=[{"name": "Init", "period_ms": 10}], ports=[])
        self.assertIsNone(swc)
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.path for m in errors], [("runnables", 0, "name")])
        self.assertIn("would be the init runnable ImplSwc_Init", errors[0].text)
        swc, _ = self.check(runnables=[{"name": "Init", "period_ms": 10}], ports=[])   # Simulink: a free name
        self.assertIsNotNone(swc)

    def test_implementation_of(self):
        tmp = Path(tempfile.mkdtemp(prefix="nx_impl_"))
        try:
            self.assertEqual(swc_yaml.implementation_of(tmp), ("simulink", []))
            (tmp / "swc.yaml").write_text("name: X\nports: [broken\n", encoding="utf-8")
            implementation, messages = swc_yaml.implementation_of(tmp)
            self.assertEqual(messages[0].severity, "error")
            (tmp / "swc.yaml").write_text("name: X\nimplementation: c\nports: 5\n", encoding="utf-8")
            self.assertEqual(swc_yaml.implementation_of(tmp), ("c", []))   # only the key is read
            (tmp / "swc.yaml").write_text("name: X\n\nimplementation: C\n", encoding="utf-8")
            implementation, messages = swc_yaml.implementation_of(tmp)
            self.assertEqual((messages[0].path, messages[0].line), (("implementation",), 3))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_json_refuses_a_c_component(self):
        tmp = Path(tempfile.mkdtemp(prefix="nx_impl_"))
        try:
            folder = tmp / "ImplSwc"
            folder.mkdir()
            (folder / "swc.yaml").write_text("name: ImplSwc\nimplementation: c\nrunnables:\n"
                                             "  - { name: Run10ms, period_ms: 10 }\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(swc_yaml.main(["json", str(folder)]), 1)
                self.assertEqual(swc_yaml.main(["check", str(folder)]), 0)
            self.assertIn("has no Simulink model", err.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_template_describes_the_key(self):
        text = (REPO / "swc" / "template" / "swc.yaml").read_text(encoding="utf-8")
        self.assertIn("implementation:", text)
        swc, _ = swc_yaml.load(REPO / "swc" / "template" / "swc.yaml", check_folder=False)
        self.assertEqual(swc.implementation, "simulink")


if __name__ == "__main__":
    unittest.main()
