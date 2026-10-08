# SPDX-License-Identifier: Apache-2.0
"""Host tests of the static analysis (PLAT-74) - synthetic example, no installed cppcheck/clang-tidy needed.

A small invented repository (own code, an OpenBSW copy from its own copies.yaml, the submodule, generated code and a
build directory) with a compile database and raw tool output (cppcheck XML, clang-tidy text) checks: splitting the
compile commands, scope, parsers, deviations, baseline without line numbers, verdicts, exit codes and the check of the
real configuration files. Only if cppcheck is found, one extra test runs it on the synthetic C file.
SYS-62: modified copies in the safety path are gated (``scope.gating_copies``), checked synthetically and against the
module classes of doc/safety/coexistence.md.

    python -m unittest tools/misra/test_misra.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from tools.misra import analysis as A  # noqa: E402

RULES = {
    "version": 1,
    "scope": {"exclude": ["gen/generated/**", "**/test/**"], "prefer_config": "RelWithDebInfo",
              "context": ["outside", "excluded"], "source_roots": ["lib"]},
    "cppcheck": {"args": []},
    "clang_tidy": {"checks": ["bugprone-*"]},
    "notices": ["normalCheckLevelMaxBranches"],
    "notices_cpp": ["misra-config", "misra-c2012-*"],
    "keep_cpp": ["misra-c2012-17.2"],
    "rules": {"misra-c2012-15.5": {"category": "advisory", "summary": "ein Ausgang"},
              "misra-c2012-11.4": {"category": "advisory", "summary": "Zeiger/Zahl"},
              "misra-c2012-17.2": {"category": "required", "summary": "keine Rekursion"},
              "bugprone-branch-clone": {"category": "check", "summary": "gleiche Zweige"},
              "uninitvar": {"category": "check", "summary": "nicht initialisiert"}},
}

DEVIATIONS = {
    "version": 1,
    "deviations": [{
        "id": "DEV-001", "kind": "deviation", "rule": "misra-c2012-11.4", "files": ["lib/**"],
        "pattern": r"\bREG->", "limit": 2, "reason": "Registerzugriff", "raised_by": "Test",
        "date": "2026-09-21", "approved_by": "Nutzer",
    }],
}

A_C = textwrap.dedent("""\
    #include "a.h"
    int f(int x)
    {
        if (x > 0) { return 1; }
        REG->CTRL = 1U;
        REG->CTRL = 2U;
        REG->CTRL = 3U;
        return 0;
    }
    """)


def cppcheck_xml(repo: Path, build: Path) -> str:
    a_c, a_h = repo / "lib" / "a.c", repo / "lib" / "a.h"
    b_cpp, copy = repo / "lib" / "b.cpp", repo / "ecu" / "copy.cpp"

    def err(rule, loc, line, unit=None, sev="style", msg="misra violation"):
        unit_attr = f' file0="{unit}"' if unit else ""
        return (f'<error id="{rule}" severity="{sev}" msg="{msg}"{unit_attr}>'
                f'<location file="{loc}" line="{line}" column="5"/></error>')

    errors = [
        err("misra-c2012-15.5", a_c, 4),                       # baseline
        err("misra-c2012-15.5", a_c, 8),                       # new (not in the baseline)
        err("misra-c2012-11.4", a_c, 5),                       # deviated
        err("misra-c2012-11.4", a_c, 6),                       # deviated
        err("misra-c2012-11.4", a_c, 7),                       # over the limit -> new
        err("misra-c2012-11.4", a_c, 2),                       # same rule and file, pattern does not match -> new
        err("misra-c2012-15.5", a_h, 2, unit=b_cpp),           # header from C++ ...
        err("misra-c2012-15.5", a_h, 2, unit=a_c),             # ... and from C: C wins, counted once
        err("misra-c2012-12.3", b_cpp, 3, unit=b_cpp),         # C checker on C++: notice
        err("misra-c2012-17.2", b_cpp, 4, unit=b_cpp),         # kept in C++
        err("normalCheckLevelMaxBranches", a_c, 0, sev="information", msg="limited"),
        err("uninitvar", copy, 2, sev="error", msg="Uninitialized variable"),   # OpenBSW copy
        err("uninitvar", repo / "external" / "openbsw" / "x.c", 1, sev="error"),  # submodule: dropped
        err("misra-c2012-8.7", build / "Rte.c", 1),            # generated in the build dir: dropped
    ]
    return ('<?xml version="1.0"?><results version="2"><cppcheck version="2.17.1"/><errors>'
            + "".join(errors) + "</errors></results>")


def make_repo(root: Path) -> tuple[Path, Path]:
    # a blank in the path like "S32K14x basic software": quoting in the commands, relative include paths
    repo, build = root / "my repo", root / "build"
    (repo / "lib").mkdir(parents=True)
    (repo / "lib" / "module.yaml").write_text("name: lib\n", encoding="utf-8")
    (repo / "lib" / "a.c").write_text(A_C, encoding="utf-8")
    (repo / "lib" / "a.h").write_text("#pragma once\nstatic inline int g(void) { return 0; }\n", encoding="utf-8")
    (repo / "lib" / "b.cpp").write_text("// b\nint h();\ntemplate<class A, class B> struct S {};\n"
                                        "int h() { return h(); }\n", encoding="utf-8")
    (repo / "lib" / "unused.c").write_text("int u;\n", encoding="utf-8")      # not in the compile database
    (repo / "lib" / "test").mkdir()
    (repo / "lib" / "test" / "t.cpp").write_text("int t;\n", encoding="utf-8")
    (repo / "ecu").mkdir()
    (repo / "ecu" / "copy.cpp").write_text("int c;\nint d = c;\n", encoding="utf-8")
    (repo / "ecu" / "mine.cpp").write_text("int m;\n", encoding="utf-8")
    (repo / "external" / "openbsw" / "ref").mkdir(parents=True)
    (repo / "external" / "openbsw" / "ref" / "copy.cpp").write_text("int c;\nint d = c;\n", encoding="utf-8")
    (repo / "external" / "openbsw" / "x.c").write_text("int x;\n", encoding="utf-8")
    (repo / "gen" / "generated").mkdir(parents=True)
    (repo / "gen" / "generated" / "g.c").write_text("int g1;\n", encoding="utf-8")
    copies = {"version": 1, "openbsw": {"path": "external/openbsw", "commit": "0" * 40},
              "roots": [{"name": "ecu", "path": "ecu", "original": "ref",
                         "files": {"copy.cpp": {"status": "identical", "upstream": "0" * 16},
                                   "mine.cpp": {"status": "new"}}}]}
    (repo / "copies.yaml").write_text(yaml.safe_dump(copies), encoding="utf-8")
    build.mkdir()
    (build / "RelWithDebInfo").mkdir()
    (build / "RelWithDebInfo" / "Rte.c").write_text("int r;\n", encoding="utf-8")
    gcc = r"C:\tool chain\bin\arm-none-eabi-gcc.exe" if os.name == "nt" else "/opt/tool chain/bin/arm-none-eabi-gcc"
    q = '\\"'

    def entry(path: Path, config: str) -> dict:
        inc = str(repo / "lib").replace("\\", "/")
        return {"directory": str(build), "file": str(path),
                "output": f"{config}/{path.stem}.o",
                "command": f'"{gcc}" -DCONFIG={q}{config}{q} -I"{inc}" '
                           f'-mcpu=cortex-m4 -mthumb -fno-exceptions -std=gnu99 -c "{path}"'}

    db = [entry(repo / "lib" / "a.c", "Debug"), entry(repo / "lib" / "a.c", "RelWithDebInfo"),
          entry(repo / "lib" / "b.cpp", "RelWithDebInfo"), entry(repo / "ecu" / "copy.cpp", "RelWithDebInfo"),
          entry(repo / "ecu" / "mine.cpp", "RelWithDebInfo"),
          entry(repo / "external" / "openbsw" / "x.c", "RelWithDebInfo"),
          entry(repo / "gen" / "generated" / "g.c", "RelWithDebInfo"),
          entry(build / "RelWithDebInfo" / "Rte.c", "RelWithDebInfo"),
          entry(build / "Debug" / "Rte.c", "Debug")]           # not built: skipped as context
    (build / "compile_commands.json").write_text(json.dumps(db), encoding="utf-8")
    raw = root / "raw"
    raw.mkdir()
    (raw / "cppcheck.xml").write_text(cppcheck_xml(repo, build), encoding="utf-8")
    (raw / "clang-tidy.txt").write_text(
        f"### lib/b.cpp (exit 0)\n{repo / 'lib' / 'b.cpp'}:4:1: warning: repeated branch body "
        f"[bugprone-branch-clone]\n    int h()\n    ^\n", encoding="utf-8")
    for name, data in (("rules.yaml", RULES), ("deviations.yaml", DEVIATIONS)):
        (root / name).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return repo, build


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.repo, self.build = make_repo(self.root)
        self.classifier = A.Classifier(self.repo, RULES["scope"]["exclude"], self.repo / "copies.yaml")

    def tearDown(self):
        self.tmp.cleanup()

    def opts(self, **kw) -> A.RunOptions:
        base = dict(compile_db=self.build / "compile_commands.json", out=self.root / "out",
                    raw_dir=self.root / "raw", repo=self.repo, rules_file=self.root / "rules.yaml",
                    deviations_file=self.root / "deviations.yaml", baseline_file=self.root / "baseline.yaml",
                    defects_file=self.root / "defects.yaml", copies_file=self.repo / "copies.yaml")
        base.update(kw)
        return A.RunOptions(**base)

    # --------------------------------------------------------------------------- compile database and scope

    def test_split_windows_keeps_paths_and_string_defines(self):
        argv = A.split_windows(r'C:\gcc\bin\gcc.exe -DV=\"0.1.0\" -I"C:/a b/inc" -DX=1 "C:/a b/x.c"')
        self.assertEqual(argv, [r"C:\gcc\bin\gcc.exe", '-DV="0.1.0"', "-IC:/a b/inc", "-DX=1", "C:/a b/x.c"])

    def test_parse_entry(self):
        parsed = A.parse_entry({"directory": str(self.build), "file": "x.cpp", "arguments": [
            "gcc", "-DA=1", "-D", "B", "-UC", "-I", "inc", "-isystem", "sys", "-include", "pre.h", "-std=c++17",
            "-mcpu=cortex-m4", "-mthumb", "-fno-rtti", "-O2", "-c", "x.cpp"]})
        self.assertEqual(parsed["defines"], ["A=1", "B"])
        self.assertEqual(parsed["undefines"], ["C"])
        self.assertEqual(parsed["includes"], [str((self.build / "inc").resolve())])
        self.assertEqual(parsed["system_includes"], [str((self.build / "sys").resolve())])
        self.assertEqual(parsed["arch"], ["-mcpu=cortex-m4", "-mthumb"])
        self.assertEqual(parsed["flags"], ["-fno-rtti"])
        self.assertEqual((parsed["std"], parsed["language"]), ("c++17", "c++"))

    def test_scope_comes_from_copies_yaml(self):
        c = self.classifier
        self.assertEqual(c.scope("lib/a.c"), "own")
        self.assertEqual(c.scope("ecu/copy.cpp"), "copy")             # identical in copies.yaml
        self.assertEqual(c.scope("ecu/mine.cpp"), "own")              # status new: our code
        self.assertEqual(c.scope("external/openbsw/x.c"), "external")
        self.assertEqual(c.scope("gen/generated/g.c"), "excluded")
        self.assertEqual(c.scope("lib/test/t.cpp"), "excluded")
        self.assertEqual(c.scope(c.relative(self.build / "RelWithDebInfo" / "Rte.c")), "outside")
        self.assertEqual(c.module("lib/a.c"), "lib")                  # module.yaml
        self.assertEqual(c.module("ecu/mine.cpp"), "ecu")             # fallback: path prefix

    def test_select_units(self):
        units, stats = A.select_units(self.build / "compile_commands.json", self.classifier, "RelWithDebInfo",
                                      ("outside", "excluded"))
        by_file = {u.file: u for u in units}
        self.assertEqual({"lib/a.c", "lib/b.cpp", "ecu/copy.cpp", "ecu/mine.cpp", "gen/generated/g.c"},
                         {f for f in by_file if not os.path.isabs(f)})
        self.assertIn('CONFIG="RelWithDebInfo"', by_file["lib/a.c"].defines)   # preferred configuration
        self.assertEqual(by_file["lib/a.c"].language, "c")
        self.assertEqual(stats["units"]["own"], 3)
        self.assertEqual(stats["units"]["copy"], 1)
        self.assertEqual(stats["units"]["outside"], 1)                # Debug/Rte.c does not exist
        self.assertNotIn("external/openbsw/x.c", by_file)
        gaps = A.uncovered_sources(self.classifier, [u for u in units if u.scope in A.SCOPES], ["lib"])
        self.assertEqual(gaps, {"lib": ["lib/unused.c"]})

    def test_build_dir_inside_repo_is_outside(self):
        """REL-020: tools/ci builds into build/ci of the tree - the updater lists upd_blob.c of every configuration
        in the compile database, only the built one exists; generated sources there are context, not own code."""
        build = self.repo / "build" / "ci" / "demo"
        (build / "RelWithDebInfo").mkdir(parents=True)
        (build / "RelWithDebInfo" / "upd_blob.c").write_text("int b;\n", encoding="utf-8")
        db = [{"directory": str(build), "file": str(build / config / "upd_blob.c"), "output": f"{config}/b.o",
               "command": f"gcc -DCONFIG={config} -c \"{build / config / 'upd_blob.c'}\""}
              for config in ("RelWithDebInfo", "Debug", "Release")]
        db.append({"directory": str(build), "file": str(self.repo / "lib" / "a.c"), "output": "a.o",
                   "command": f'gcc -c "{self.repo / "lib" / "a.c"}"'})
        (build / "compile_commands.json").write_text(json.dumps(db), encoding="utf-8")
        c = A.Classifier(self.repo, RULES["scope"]["exclude"], self.repo / "copies.yaml", None, [build])
        self.assertEqual(c.scope(c.relative(build / "Debug" / "upd_blob.c")), "outside")
        self.assertEqual(c.scope(c.relative(self.repo / "lib" / "a.c")), "own")
        units, stats = A.select_units(build / "compile_commands.json", c, "RelWithDebInfo", ("outside", "excluded"))
        self.assertEqual([u.path for u in units if u.scope == "outside"],
                         [Path(os.path.normpath(build / "RelWithDebInfo" / "upd_blob.c"))])
        self.assertTrue(all(u.path.is_file() for u in units))
        self.assertEqual(stats["units"]["own"], 1)
        # without the build directory the generat would be own code - and Debug/upd_blob.c a missing unit
        plain = A.Classifier(self.repo, RULES["scope"]["exclude"], self.repo / "copies.yaml")
        self.assertEqual(plain.scope(plain.relative(build / "Debug" / "upd_blob.c")), "own")

    def test_cppcheck_database_avoids_blanks(self):
        units, _ = A.select_units(self.build / "compile_commands.json", self.classifier, "RelWithDebInfo")
        a = next(u for u in units if u.file == "lib/a.c")
        self.assertIn(" ", a.includes[0])                        # absolute path below "my repo"
        args = A.cppcheck_database([a])[0]["arguments"]
        self.assertIn("-I.", args)                                # relative to the unit's directory
        self.assertIn('-DCONFIG=\\"RelWithDebInfo\\"', args)      # cppcheck re-splits: quotes escaped

    # --------------------------------------------------------------------------- parsers

    def test_parsers(self):
        cpp = A.parse_cppcheck_xml((self.root / "raw" / "cppcheck.xml").read_text(encoding="utf-8"))
        self.assertEqual(len(cpp), 14)
        self.assertEqual(cpp[0].rule, "misra-c2012-15.5")
        self.assertTrue(cpp[6].unit.endswith("b.cpp"))
        tidy = A.parse_clang_tidy("### lib/x.cpp (exit 1)\nC:/r/x.cpp:3:7: warning: m [bugprone-a,cert-b]\n"
                                  "/r/y.c:1:1: error: unknown type name 'foo'\n  note line\n")
        self.assertEqual([(f.rule, f.unit) for f in tidy],
                         [("bugprone-a", "lib/x.cpp"), ("cert-b", "lib/x.cpp"), ("clang-diagnostic-error",
                                                                                 "lib/x.cpp")])
        self.assertEqual((tidy[0].file, tidy[0].line, tidy[0].column), ("C:/r/x.cpp", 3, 7))

    # --------------------------------------------------------------------------- verdicts

    def run_analysis(self, **kw):
        return A.analyse(self.opts(**kw), log=lambda *_: None)[0]

    def test_inventory_without_baseline_everything_own_is_new(self):
        result = self.run_analysis()
        verdicts = {(f.file, f.line, f.rule): f.verdict for f in result.findings}
        self.assertEqual(verdicts[("lib/a.c", 5, "misra-c2012-11.4")], "deviated")
        self.assertEqual(verdicts[("lib/a.c", 6, "misra-c2012-11.4")], "deviated")
        self.assertEqual(verdicts[("lib/a.c", 7, "misra-c2012-11.4")], "new")      # limit 2 reached
        self.assertEqual(verdicts[("lib/a.c", 2, "misra-c2012-11.4")], "new")      # line is no REG-> access
        self.assertEqual(verdicts[("lib/b.cpp", 3, "misra-c2012-12.3")], "notice")  # C checker on C++
        self.assertEqual(verdicts[("lib/b.cpp", 4, "misra-c2012-17.2")], "new")     # keep_cpp
        self.assertEqual(verdicts[("lib/a.c", 0, "normalCheckLevelMaxBranches")], "notice")
        self.assertEqual(verdicts[("ecu/copy.cpp", 2, "uninitvar")], "copy")
        header = [f for f in result.findings if f.file == "lib/a.h"]
        self.assertEqual(len(header), 1)
        self.assertTrue(header[0].in_c and header[0].verdict == "new")              # C view wins
        self.assertFalse(any(f.file.startswith("external/") for f in result.findings))
        self.assertEqual(A.exit_code(result, A.TOOLS), A.EXIT_NEW)
        first = next(f for f in result.findings if f.file == "lib/a.c" and f.line == 4)
        self.assertEqual(first.text, "if (x > 0) { return 1; }")                    # location = source line
        self.assertEqual(first.module, "lib")
        inv = A.inventory(result)
        self.assertEqual(inv["copy_total"], 1)
        self.assertEqual(inv["own_rule_module"]["misra-c2012-15.5"], {"lib": 3})

    def test_baseline_turns_run_green_and_new_finding_breaks_it(self):
        result = self.run_analysis()
        data = A.build_baseline(result, {}, {"cppcheck": "2.17.1"})
        (self.root / "baseline.yaml").write_text(A.dump_baseline(data), encoding="utf-8")
        green = self.run_analysis()
        self.assertEqual(green.new, [])
        self.assertEqual(A.exit_code(green, A.TOOLS), A.EXIT_OK)
        # moving code (a line inserted above: every finding one line lower) is no new finding
        xml = (self.root / "raw" / "cppcheck.xml").read_text(encoding="utf-8")
        a_c = str(self.repo / "lib" / "a.c")
        (self.repo / "lib" / "a.c").write_text("/* moved */\n" + A_C, encoding="utf-8")
        moved = re.sub(re.escape(f'file="{a_c}" line="') + r"(\d+)",
                       lambda m: f'file="{a_c}" line="{int(m.group(1)) + 1}', xml)
        self.assertNotEqual(moved, xml)
        (self.root / "raw" / "cppcheck.xml").write_text(moved, encoding="utf-8")
        self.assertEqual(self.run_analysis().new, [])
        (self.repo / "lib" / "a.c").write_text(A_C, encoding="utf-8")
        # a second violation with the same source line is new (counted), and so is one of another rule
        extra = (f'<error id="misra-c2012-15.5" severity="style" msg="m"><location file="{a_c}" line="4" '
                 f'column="9"/></error><error id="uninitvar" severity="error" msg="u"><location file="{a_c}" '
                 f'line="2" column="1"/></error>')
        (self.root / "raw" / "cppcheck.xml").write_text(xml.replace("</errors>", extra + "</errors>"),
                                                        encoding="utf-8")
        broken = self.run_analysis()
        self.assertEqual(sorted((f.line, f.rule) for f in broken.new), [(2, "uninitvar"), (4, "misra-c2012-15.5")])
        self.assertEqual(A.exit_code(broken, A.TOOLS), A.EXIT_NEW)

    def test_resolved_findings_are_reported(self):
        result = self.run_analysis()
        data = A.build_baseline(result, {}, {})
        data["entries"]["lib/a.c"].append({"tool": "cppcheck", "rule": "misra-c2012-15.5", "count": 2,
                                           "text": "gone();"})
        (self.root / "baseline.yaml").write_text(A.dump_baseline(data), encoding="utf-8")
        again = self.run_analysis()
        self.assertEqual(again.resolved, [{"file": "lib/a.c", "rule": "misra-c2012-15.5", "text": "gone();",
                                           "count": 2}])
        self.assertIn("Behoben seit der Baseline (2)", A.render_markdown(again, RULES))

    def test_missing_tool_is_skipped_not_green(self):
        empty = self.root / "no-tools"
        empty.mkdir()
        result = self.run_analysis(raw_dir=None, tool_dir=empty)
        self.assertEqual(set(result.skipped_tools), set(A.TOOLS))
        self.assertIn("nicht gefunden", result.skipped_tools["cppcheck"])
        self.assertEqual(A.exit_code(result, A.TOOLS), A.EXIT_SKIPPED)
        self.assertEqual(result.findings, [])

    def test_baseline_of_a_skipped_tool_survives(self):
        result = self.run_analysis()
        data = A.build_baseline(result, {}, {})
        tidy_entries = [i for items in data["entries"].values() for i in items if i["tool"] == "clang-tidy"]
        self.assertEqual(len(tidy_entries), 1)
        (self.root / "baseline.yaml").write_text(A.dump_baseline(data), encoding="utf-8")
        partial = self.run_analysis(tools=("cppcheck",))
        self.assertIn("clang-tidy", partial.skipped_tools)
        self.assertEqual(partial.resolved, [])
        kept = A.build_baseline(partial, data, {})
        self.assertEqual(kept["entries"]["lib/b.cpp"], data["entries"]["lib/b.cpp"])

    def test_defects_are_listed_separately(self):
        (self.root / "defects.yaml").write_text(yaml.safe_dump({"version": 1, "defects": [{
            "id": "BEF-001", "rule": "misra-c2012-17.2", "files": ["lib/b.cpp"], "symbol": "h",
            "note": "Rekursion", "owner": "X-1", "raised_by": "Test", "date": "2026-09-21"}]}), encoding="utf-8")
        result = self.run_analysis()
        self.assertEqual(result.defects[0]["matched"], 1)
        self.assertEqual(result.defects[0]["places"], ["lib/b.cpp:4"])
        self.assertIn("BEF-001", A.render_markdown(result, RULES))
        self.assertEqual([f.verdict for f in result.findings if f.defect], ["new"])  # not suppressed

    def test_cli_end_to_end(self):
        from tools.misra.__main__ import main

        argv = ["--compile-db", str(self.build / "compile_commands.json"), "--from-raw", str(self.root / "raw"),
                "--out", str(self.root / "cli"), "--rules", str(self.root / "rules.yaml"),
                "--deviations", str(self.root / "deviations.yaml"), "--baseline", str(self.root / "b.yaml"),
                "--defects", str(self.root / "none.yaml")]
        import contextlib
        import io

        # the CLI classifies against the real repository: the synthetic files lie outside it -> config error
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["run", *argv]), A.EXIT_CONFIG)

    def disapply_15_5(self) -> dict:
        rules = json.loads(json.dumps(RULES))
        rules["rules"]["misra-c2012-15.5"].update({"revised_category": "disapplied", "reason": "fruehe Rueckkehr",
                                                   "date": "2026-09-21", "approved_by": "Nutzer"})
        (self.root / "rules.yaml").write_text(yaml.safe_dump(rules, allow_unicode=True), encoding="utf-8")
        return rules

    def test_disapplied_rule_is_counted_apart_neither_new_nor_baseline(self):
        # baseline written while 15.5 was still applied: it holds the 15.5 findings
        old = A.build_baseline(self.run_analysis(), {}, {})
        self.assertTrue(any(i["rule"] == "misra-c2012-15.5" for items in old["entries"].values() for i in items))
        rules = self.disapply_15_5()
        result = self.run_analysis()
        mine = [f for f in result.findings if f.rule == "misra-c2012-15.5"]
        self.assertEqual(sorted((f.file, f.line) for f in mine), [("lib/a.c", 4), ("lib/a.c", 8), ("lib/a.h", 2)])
        self.assertEqual({f.verdict for f in mine}, {"disapplied"})
        self.assertFalse(any(f.rule == "misra-c2012-15.5" for f in result.new))
        # other rules keep their verdicts (deviation, limit, keep_cpp)
        self.assertEqual(sorted((f.line, f.rule) for f in result.new),
                         [(2, "misra-c2012-11.4"), (4, "bugprone-branch-clone"), (4, "misra-c2012-17.2"),
                          (7, "misra-c2012-11.4")])
        inv = A.inventory(result)
        self.assertEqual(inv["disapplied_total"], 3)
        self.assertEqual(inv["disapplied_rule_module"], {"misra-c2012-15.5": {"lib": 3}})
        self.assertNotIn("misra-c2012-15.5", inv["own_by_rule"])
        report = A.render_markdown(result, rules)
        self.assertIn("## Neueinstufungsplan", report)
        self.assertIn("| misra-c2012-15.5 | advisory | disapplied | 3 | lib 3 | Nutzer |", report)
        # a rewritten baseline no longer holds the rule; an old one is flagged with its YAML path
        new = A.build_baseline(result, old, {})
        self.assertFalse(any(i["rule"] == "misra-c2012-15.5" for items in new["entries"].values() for i in items))
        texts = [str(m) for m in A.validate_baseline(old, Path("baseline.yaml"), A.disapplied_rules(rules))]
        self.assertEqual(len(texts), 1, texts)                            # one message per rule
        self.assertRegex(texts[0], r"\(entries\.lib/a\.c\[\d\]\.rule und 2 weitere Eintraege\): "
                                   r"WARNUNG misra-c2012-15\.5 ist disapplied")
        (self.root / "baseline.yaml").write_text(A.dump_baseline(new), encoding="utf-8")
        again = self.run_analysis()
        self.assertEqual(again.resolved, [])
        self.assertEqual(A.inventory(again)["disapplied_total"], 3)
        self.assertEqual(json.loads(json.dumps(A.to_json(again, rules)))["inventory"]["disapplied_total"], 3)

    # --------------------------------------------------------------------------- configuration checks

    def test_only_advisory_rules_may_be_disapplied(self):
        def rule(category, revised, **extra):
            return {"category": category, "summary": "s", "revised_category": revised, "reason": "r",
                    "date": "2026-09-21", "approved_by": "Nutzer", **extra}

        data = {"version": 1, "clang_tidy": {"checks": ["x"]}, "rules": {
            "misra-c2012-15.5": rule("advisory", "disapplied"),              # allowed
            "misra-c2012-8.7": rule("advisory", "required"),                 # stricter: allowed
            "misra-c2012-17.2": rule("required", "disapplied"),              # required cannot be disapplied
            "misra-c2012-17.3": rule("mandatory", "disapplied"),
            "misra-c2012-10.4": rule("required", "advisory"),                # weaker: not allowed
            "bugprone-branch-clone": rule("check", "disapplied"),            # not a MISRA rule
            "misra-c2012-2.5": rule("advisory", "egal"),
            "misra-c2012-2.3": rule("advisory", "disapplied", reason="", approved_by="", date="21.09.2026"),
        }}
        messages = A.validate_rules(data, Path("rules.yaml"))
        errors = [str(m) for m in messages if m.severity == "error"]
        warnings = [str(m) for m in messages if m.severity == "warning"]
        for needle in ("(rules.misra-c2012-17.2.revised_category): FEHLER nur advisory-Regeln duerfen disapplied",
                       "(rules.misra-c2012-17.3.revised_category): FEHLER nur advisory-Regeln duerfen disapplied",
                       "(rules.misra-c2012-10.4.revised_category): FEHLER required darf nicht zu advisory",
                       "(rules.bugprone-branch-clone.revised_category): FEHLER nur MISRA-Regeln",
                       "(rules.misra-c2012-2.5.revised_category): FEHLER unbekannte Einstufung",
                       "(rules.misra-c2012-2.3.reason): FEHLER", "(rules.misra-c2012-2.3.date): FEHLER"):
            self.assertTrue(any(needle in t for t in errors), (needle, errors))
        self.assertEqual(len(errors), 7, errors)
        self.assertEqual(len(warnings), 1)
        self.assertIn("(rules.misra-c2012-2.3.approved_by): WARNUNG", warnings[0])
        # only a valid advisory -> disapplied entry takes effect in the evaluation
        self.assertEqual(A.disapplied_rules(data), {"misra-c2012-15.5", "misra-c2012-2.3"})

    def test_deviation_needs_place_reason_who_and_when(self):
        bad = {"version": 1, "deviations": [
            {"id": "DEV-1", "rule": "x", "files": ["a.c"], "reason": ""},
            {"id": "DEV-002", "rule": "x", "files": ["a.c"], "pattern": "(", "reason": "r", "raised_by": "w",
             "date": "21.09.2026", "kind": "egal"}]}
        texts = [str(m) for m in A.validate_deviations(bad, Path("deviations.yaml"))]
        for needle in ("(deviations[0].id)", "(deviations[0]): FEHLER Ort nur ueber Datei",
                       "(deviations[0].reason)", "(deviations[0].raised_by)", "(deviations[1].pattern)",
                       "(deviations[1].date)", "(deviations[1].kind)", "noch nicht freigegeben"):
            self.assertTrue(any(needle in t for t in texts), (needle, texts))


class SyntheticWorkspace(unittest.TestCase):
    """TOOL-217 (customer-project.md gap N19): the own code of a customer workspace with its own ci/misra/ files.

    The workspace `omicron` is synthetic, lies in %TEMP% and pins this tree; raw tool output stands in for the tools.
    Only files of the workspace are own code - a finding in the tree, in the build root or in a tree nested in the
    workspace (submodule) is never judged."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.ws = ws = root / "omicron"
        version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        (ws / "ci").mkdir(parents=True)
        (ws / "nextbsw.yaml").write_text(
            "# synthetic workspace of tools/misra/test_misra.py - test data, no real customer\n"
            f"schema_version: 1\nnextbsw:\n  version: {version}\n  source: submodule\n"
            f"  path: {Path(os.path.relpath(REPO, ws)).as_posix()}\n  profile: open\nsearch:\n  modules: [swc]\n",
            encoding="utf-8")
        (ws / "ci" / "pipeline.toml").write_text(
            "schema = 1\n[static_analysis]\nenabled = true\ncompile_db = \"main/compile_commands.json\"\n",
            encoding="utf-8")
        src = ws / "swc" / "Omicron" / "src"
        src.mkdir(parents=True)
        self.own = src / "omicron.c"
        self.own.write_text("int omicron(int x)\n{\n    if (x > 0) { return 1; }\n    return 0;\n}\n",
                            encoding="utf-8")
        (src / "omicron_unused.c").write_text("int u;\n", encoding="utf-8")     # compiled by no build
        self.nested = ws / "nextbsw" / "libs" / "n.c"                            # a tree as submodule
        self.nested.parent.mkdir(parents=True)
        self.nested.write_text("int n;\n", encoding="utf-8")
        build = ws / "build" / "nxtest" / "main"
        build.mkdir(parents=True)
        (build / "Rte.c").write_text("int r;\n", encoding="utf-8")
        tree_file = REPO / "tools" / "test" / "fixtures" / "coverage_probe.c"
        self.build = build
        # the nested folder is no pinned tree of this run (that is REPO): it only appears in test_nested_tree_...
        units = [self.own, build / "Rte.c", tree_file]
        db = [{"directory": str(build), "file": str(p), "output": f"{p.stem}.o",
               "command": f'gcc -mcpu=cortex-m4 -std=gnu99 -c "{p}"'} for p in units]
        (build / "compile_commands.json").write_text(json.dumps(db), encoding="utf-8")

        def err(path: Path, line: int) -> str:
            return (f'<error id="misra-c2012-10.4" severity="style" msg="misra violation">'
                    f'<location file="{path}" line="{line}" column="5"/></error>')

        self.raw = root / "raw"
        self.raw.mkdir()
        (self.raw / "cppcheck.xml").write_text(
            '<?xml version="1.0"?><results version="2"><cppcheck version="2.17.1"/><errors>'
            + err(self.own, 3) + err(build / "Rte.c", 1) + err(tree_file, 13)
            + "</errors></results>", encoding="utf-8")
        (self.raw / "clang-tidy.txt").write_text("", encoding="utf-8")
        self.out = root / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, command: str, *extra: str) -> tuple[int, str]:
        import contextlib
        import io

        from tools.misra.__main__ import main

        argv = [command, "--workspace", str(self.ws)]
        if command != "check":
            argv += ["--from-raw", str(self.raw), "--out", str(self.out)]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main([*argv, *extra])
        return code, buffer.getvalue()

    def write_deviations(self):
        template = REPO / "tools" / "ci" / "templates" / "workspace-misra-deviations.yaml"
        target = self.ws / "ci" / "misra" / "deviations.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")

    def test_without_deviations_the_template_is_named(self):
        code, text = self.cli("run")
        self.assertEqual(code, A.EXIT_CONFIG, text)
        self.assertIn("template misra-deviations", text)

    def test_own_code_only_new_then_baseline_then_green(self):
        self.write_deviations()
        code, text = self.cli("run")
        self.assertEqual(code, A.EXIT_NEW, text)
        result = json.loads((self.out / "result.json").read_text(encoding="utf-8"))
        own = [f for f in result["findings"] if f["scope"] == "own"]
        self.assertEqual([(f["file"], f["verdict"]) for f in own], [("swc/Omicron/src/omicron.c", "new")],
                         "nur der Workspace ist eigener Code - Baum, Bau-Wurzel und Submodul nie")
        self.assertEqual(result["scope"]["uncovered"], {"swc/Omicron": ["swc/Omicron/src/omicron_unused.c"]})
        code, text = self.cli("baseline")
        self.assertEqual(code, A.EXIT_OK, text)
        baseline = yaml.safe_load((self.ws / "ci" / "misra" / "baseline.yaml").read_text(encoding="utf-8"))
        self.assertEqual(list(baseline["entries"]), ["swc/Omicron/src/omicron.c"])
        self.assertEqual(self.cli("run")[0], A.EXIT_OK)
        code, text = self.cli("check")
        self.assertEqual(code, A.EXIT_OK, text)
        # the tree's own baseline is untouched
        self.assertNotIn("swc/Omicron/src/omicron.c", A.read_yaml(A.BASELINE_FILE)["entries"])

    def test_compile_db_comes_from_the_pipeline_of_the_workspace(self):
        self.write_deviations()
        (self.ws / "ci" / "pipeline.toml").write_text("schema = 1\n[static_analysis]\nenabled = true\n",
                                                      encoding="utf-8")
        code, text = self.cli("run")
        self.assertEqual(code, A.EXIT_CONFIG, text)
        self.assertIn("static_analysis.compile_db", text)

    def test_nested_tree_is_context_not_own_code(self):
        """A workspace with the tree as submodule (`nextbsw/`): outside_dirs makes it context like a build folder."""
        self.write_deviations()
        db_file = self.build / "compile_commands.json"
        db = json.loads(db_file.read_text(encoding="utf-8"))
        db.append({"directory": str(self.build), "file": str(self.nested), "output": "n.o",
                   "command": f'gcc -mcpu=cortex-m4 -std=gnu99 -c "{self.nested}"'})
        db_file.write_text(json.dumps(db), encoding="utf-8")
        xml = self.raw / "cppcheck.xml"
        xml.write_text(xml.read_text(encoding="utf-8").replace(
            "</errors>", f'<error id="misra-c2012-10.4" severity="style" msg="misra violation">'
                         f'<location file="{self.nested}" line="1" column="5"/></error></errors>'), encoding="utf-8")

        def own_files(outside: tuple) -> list[str]:
            opts = A.RunOptions(compile_db=db_file, out=self.out, raw_dir=self.raw, repo=self.ws, copies_file=None,
                                deviations_file=self.ws / "ci" / "misra" / "deviations.yaml",
                                baseline_file=self.ws / "ci" / "misra" / "baseline.yaml",
                                defects_file=self.ws / "ci" / "misra" / "defects.yaml",
                                outside_dirs=outside, source_roots=["swc"])
            result, _ = A.analyse(opts, log=lambda *_: None)
            return sorted({f.file for f in result.findings if f.scope == "own"})

        self.assertEqual(own_files((self.ws / "nextbsw",)), ["swc/Omicron/src/omicron.c"])
        self.assertEqual(own_files(()), ["nextbsw/libs/n.c", "swc/Omicron/src/omicron.c"])   # counter-check

    def test_own_rules_of_the_workspace_win(self):
        self.write_deviations()
        rules = A.read_yaml(A.RULES_FILE)
        rules["scope"]["exclude"] = list(rules["scope"]["exclude"]) + ["swc/Omicron/**"]
        (self.ws / "ci" / "misra" / "rules.yaml").write_text(yaml.safe_dump(rules, allow_unicode=True),
                                                             encoding="utf-8")
        code, text = self.cli("run")
        self.assertEqual(code, A.EXIT_CONFIG, text)       # nothing own left: the run says so instead of green
        self.assertIn("keine eigene Uebersetzungseinheit", text)


class RealConfiguration(unittest.TestCase):
    """The checked-in rule set, deviation list, defect list and baseline are valid and consistent."""

    def test_files_validate(self):
        messages = (A.validate_rules(A.read_yaml(A.RULES_FILE)) + A.validate_deviations(A.read_yaml(A.DEVIATIONS_FILE))
                    + A.validate_baseline(A.read_yaml(A.BASELINE_FILE))
                    + A.validate_defects(A.read_yaml(A.DEFECTS_FILE), A.DEFECTS_FILE))
        errors = [str(m) for m in messages if m.severity == "error"]
        self.assertEqual(errors, [])

    def test_approvals_and_reclassification_are_recorded(self):
        # decision plat74-abweichungen (user 2026-09-21): DEV-001..014 approved, 15.5 disapplied project-wide
        rules = A.read_yaml(A.RULES_FILE)
        messages = (A.validate_rules(rules) + A.validate_deviations(A.read_yaml(A.DEVIATIONS_FILE))
                    + A.validate_baseline(A.read_yaml(A.BASELINE_FILE), A.BASELINE_FILE, A.disapplied_rules(rules)))
        self.assertEqual([str(m) for m in messages], [])                  # no open approval, nothing stale
        self.assertEqual(A.disapplied_rules(rules), {"misra-c2012-15.5"})
        for rule, info in rules["rules"].items():
            if info.get("revised_category") == "disapplied":
                self.assertEqual(info["category"], "advisory", rule)
                self.assertTrue(info.get("reason") and info.get("approved_by"), rule)

    def test_every_rule_in_baseline_and_lists_has_an_own_summary(self):
        rules = A.read_yaml(A.RULES_FILE)
        used = {i["rule"] for items in A.read_yaml(A.BASELINE_FILE)["entries"].values() for i in items}
        for doc in (A.read_yaml(A.DEVIATIONS_FILE)["deviations"], A.read_yaml(A.DEFECTS_FILE)["defects"]):
            for d in doc:
                used |= set(d["rule"] if isinstance(d["rule"], list) else [d["rule"]])
        missing = sorted(r for r in used if not A.summary_of(rules, r))
        self.assertEqual(missing, [])
        # own short descriptions only - a copied rule text would be much longer
        long = [r for r, i in rules["rules"].items() if len(i["summary"]) > 110]
        self.assertEqual(long, [])

    def test_baseline_only_names_own_code(self):
        scope = A.read_yaml(A.RULES_FILE)["scope"]
        classifier = A.Classifier(REPO, scope["exclude"], A.COPIES_FILE, scope.get("gating_copies"))
        wrong = [p for p in A.read_yaml(A.BASELINE_FILE)["entries"] if classifier.scope(p) != "own"]
        self.assertEqual(wrong, [])

    def test_deviations_do_not_hide_copies_or_whole_files(self):
        for dev in A.read_yaml(A.DEVIATIONS_FILE)["deviations"]:
            self.assertTrue(dev.get("pattern") or dev.get("symbol"), dev["id"])
            self.assertFalse(any(f.startswith("external/") for f in dev["files"]), dev["id"])

    def test_tool_versions_are_pinned_once(self):
        pins = A.pinned_versions()
        self.assertIn("cppcheck", pins)
        self.assertIn("clang-tidy", pins)


# --------------------------------------------------------------------------- SYS-62: gated copies
#
# A modified copy that matches scope.gating_copies is own code - its finding breaks the run until it is in the
# baseline; a modified copy outside the list and an identical copy inside it stay "copy"; baseline --only takes in
# the gated file and nothing else. The real list hits exactly the modified copies of the ASIL B modules of
# doc/safety/coexistence.md (classes B-SM, B-SEP, B-DEP, longest path prefix as there); counter-checks with a
# pattern removed and a QM pattern added show that the comparison sees both faults.
COEXISTENCE_TEST = REPO / "doc" / "safety" / "test" / "test_coexistence.py"
ASIL_B_CLASSES = ("B-SM", "B-SEP", "B-DEP")
GATE = ["ecu/safe/**"]


def err(rule: str, path: Path, line: int) -> str:
    return (f'<error id="{rule}" severity="error" msg="m"><location file="{path}" line="{line}" column="1"/>'
            f'</error>')


class SyntheticGating(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.repo, self.build = make_repo(self.root)
        (self.repo / "ecu" / "safe").mkdir()
        (self.repo / "ecu" / "qm").mkdir()
        for rel in ("ecu/safe/mod.cpp", "ecu/safe/same.cpp", "ecu/qm/mod.cpp"):
            (self.repo / rel).write_text("int a;\nint b = a;\n", encoding="utf-8")
        copies = yaml.safe_load((self.repo / "copies.yaml").read_text(encoding="utf-8"))
        copies["roots"][0]["files"].update({
            "safe/mod.cpp": {"status": "modified", "upstream": "0" * 16, "task": ["SYS-1"], "reason": "r"},
            "safe/same.cpp": {"status": "identical", "upstream": "0" * 16},
            "qm/mod.cpp": {"status": "modified", "upstream": "0" * 16, "task": ["SYS-1"], "reason": "r"}})
        (self.repo / "copies.yaml").write_text(yaml.safe_dump(copies), encoding="utf-8")
        raw = self.root / "raw" / "cppcheck.xml"
        extra = "".join(err("uninitvar", self.repo / rel, 2)
                        for rel in ("ecu/safe/mod.cpp", "ecu/safe/same.cpp", "ecu/qm/mod.cpp"))
        raw.write_text(raw.read_text(encoding="utf-8").replace("</errors>", extra + "</errors>"), encoding="utf-8")
        self.write_rules(GATE)

    def tearDown(self):
        self.tmp.cleanup()

    def write_rules(self, gate: list[str] | None) -> None:
        rules = yaml.safe_load(yaml.safe_dump(RULES))
        if gate is not None:
            rules["scope"]["gating_copies"] = gate
        (self.root / "rules.yaml").write_text(yaml.safe_dump(rules, allow_unicode=True), encoding="utf-8")

    def analyse(self) -> A.Result:
        opts = A.RunOptions(compile_db=self.build / "compile_commands.json", out=self.root / "out",
                            raw_dir=self.root / "raw", repo=self.repo, rules_file=self.root / "rules.yaml",
                            deviations_file=self.root / "deviations.yaml", baseline_file=self.root / "baseline.yaml",
                            defects_file=self.root / "defects.yaml", copies_file=self.repo / "copies.yaml")
        return A.analyse(opts, log=lambda *_: None)[0]

    @staticmethod
    def verdicts(result: A.Result) -> dict:
        return {f.file: f.verdict for f in result.findings if f.file.startswith("ecu/") and f.rule == "uninitvar"}

    def test_classifier(self):
        c = A.Classifier(self.repo, [], self.repo / "copies.yaml", GATE)
        self.assertEqual(c.scope("ecu/safe/mod.cpp"), "own")           # modified, in the safety path
        self.assertEqual(c.scope("ecu/safe/same.cpp"), "copy")         # identical: pre-existing, route 8-12
        self.assertEqual(c.scope("ecu/qm/mod.cpp"), "copy")            # modified, QM
        self.assertEqual(c.gated, {"ecu/safe/mod.cpp"})
        # counter-check: without the list the modified copy is not gated
        self.assertEqual(A.Classifier(self.repo, [], self.repo / "copies.yaml").scope("ecu/safe/mod.cpp"), "copy")

    def test_gated_copy_breaks_the_run_until_it_is_in_the_baseline(self):
        result = self.analyse()
        self.assertEqual(self.verdicts(result), {"ecu/copy.cpp": "copy", "ecu/safe/mod.cpp": "new",
                                                 "ecu/safe/same.cpp": "copy", "ecu/qm/mod.cpp": "copy"})
        self.assertEqual(result.scope["gated_copies"], ["ecu/safe/mod.cpp"])
        self.assertEqual(A.inventory(result)["gated_total"], 1)
        self.assertIn("geaenderte OpenBSW-Kopien im Sicherheitspfad", A.render_markdown(result, RULES))
        # baseline --only: the gated file enters, the unrelated new findings of lib/ do not
        data = A.build_baseline(result, {}, {}, only=GATE)
        self.assertEqual(list(data["entries"]), ["ecu/safe/mod.cpp"])
        (self.root / "baseline.yaml").write_text(A.dump_baseline(data), encoding="utf-8")
        again = self.analyse()
        self.assertEqual(self.verdicts(again)["ecu/safe/mod.cpp"], "baseline")
        self.assertTrue(again.new)
        self.assertFalse(any(f.file.startswith("ecu/") for f in again.new))
        # --only keeps the entries of every other file as they were
        kept = A.build_baseline(again, {"entries": {"lib/a.c": [{"tool": "cppcheck", "rule": "x", "count": 1,
                                                                   "text": "t"}]}}, {}, only=GATE)
        self.assertEqual(kept["entries"]["lib/a.c"], [{"tool": "cppcheck", "rule": "x", "count": 1, "text": "t"}])

    def test_without_the_list_the_copy_is_not_gated(self):
        self.write_rules(None)
        self.assertEqual(self.verdicts(self.analyse())["ecu/safe/mod.cpp"], "copy")

    def test_list_is_validated_with_yaml_path(self):
        rules = yaml.safe_load(yaml.safe_dump(RULES))
        rules["scope"]["gating_copies"] = "ecu/**"
        self.assertEqual([m.where for m in A.validate_rules(rules)], ["tools/misra/rules.yaml (scope.gating_copies)"])
        rules["scope"]["gating_copies"] = ["ecu/**", ""]
        self.assertEqual(len(A.validate_rules(rules)), 1)


def load_coexistence():
    spec = importlib.util.spec_from_file_location("nx_test_coexistence", COEXISTENCE_TEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gated_and_expected(patterns: list[str]) -> tuple[set, set]:
    """(modified copies the patterns gate, modified copies in ASIL B modules of coexistence.md)"""
    tc = load_coexistence()
    doc = tc.Coexistence(tc.DOC.read_text(encoding="utf-8"), {}, set(), tc.load_copies())
    modified = [(path, pattern) for path, status, pattern in doc.copies if status == "modified"]
    expected = {path for path, pattern in modified if doc.class_of_copy(path, pattern) in ASIL_B_CLASSES}
    classifier = A.Classifier(REPO, [], A.COPIES_FILE, patterns)
    return classifier.gated, expected


@unittest.skipUnless(COEXISTENCE_TEST.is_file(), "doc/safety/test/test_coexistence.py fehlt")
class RealGatingList(unittest.TestCase):
    """rules.yaml scope.gating_copies against the module classes of doc/safety/coexistence.md."""

    @classmethod
    def setUpClass(cls):
        cls.patterns = A.read_yaml(A.RULES_FILE)["scope"]["gating_copies"]

    def test_list_hits_exactly_the_modified_copies_of_the_asil_b_modules(self):
        gated, expected = gated_and_expected(self.patterns)
        self.assertEqual(sorted(expected - gated), [], "geaenderte Kopie eines ASIL-B-Moduls nicht sperrend")
        self.assertEqual(sorted(gated - expected), [], "Muster trifft eine geaenderte Kopie eines QM-Moduls")
        self.assertEqual(len(gated), 53)            # 47 at SYS-62, +3 by SYS-98, +1 SafetySystem.h (SYS-74), +2 modified by SYS-97/SYS-116

    def test_every_pattern_hits_a_copy(self):
        classifier = A.Classifier(REPO, [], A.COPIES_FILE, self.patterns)
        idle = [p for p in self.patterns if not any(A.glob_match(g, [p]) for g in classifier.gated)]
        self.assertEqual(idle, [])

    def test_counter_checks_see_a_missing_and_a_qm_pattern(self):
        gated, expected = gated_and_expected([p for p in self.patterns if p != "ecu/safety/safeWatchdog/**"])
        self.assertTrue(expected - gated)
        gated, expected = gated_and_expected(self.patterns + ["ecu/application/src/logger/**"])
        self.assertIn("ecu/application/src/logger/logger.cpp", gated - expected)



@unittest.skipUnless(A.find_tool("cppcheck")[0], "cppcheck nicht installiert (python -m tools.misra setup)")
class RealCppcheck(unittest.TestCase):
    """With cppcheck present: the MISRA add-on reports on the synthetic C file through run_cppcheck."""

    def test_misra_addon_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            repo, build = make_repo(root)
            classifier = A.Classifier(repo, RULES["scope"]["exclude"], repo / "copies.yaml")
            units, _ = A.select_units(build / "compile_commands.json", classifier, "RelWithDebInfo")
            units = [u for u in units if u.file == "lib/a.c"]
            for u in units:
                u.compiler = "no-such-compiler"      # predefines stay empty; the test needs no ARM compiler
            exe = A.find_tool("cppcheck")[0]
            # the arguments of the real rule set (MISRA findings have severity style: --enable=style is needed)
            args = A.read_yaml(A.RULES_FILE)["cppcheck"]["args"]
            xml = A.run_cppcheck(exe, units, {"args": args}, root / "work", 1, lambda *_: None)
            rules = {f.rule for f in A.parse_cppcheck_xml(xml)}
            self.assertIn("misra-c2012-15.5", rules)        # two returns in f()


if __name__ == "__main__":
    unittest.main()
