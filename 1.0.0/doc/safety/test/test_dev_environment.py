"""DOC-22: consistency of the development environment doc/safety/dev-environment.md with the repository.

Checks
- the header carries the state line of cm-plan.md section 6 (`Baseline: vX.Y.Z` or `Working state: dev at <commit>
  (after vX.Y.Z)`);
- tool table (markers tools:start/end): every version is the one pinned in the file the row names
  (tools/ci/versions.toml, tools/ci/.python-version, tools/ci/constraints*.txt, tools/misra/requirements.txt, the
  submodule gitlink), and every tool of versions.toml has a row with its version;
- NXCPP table (markers nxcpp:start/end): IDs numbered without gaps; the enforcement class is `tool`, `tool + review`
  or `review`; a tool rule names at least one enforcing item and every item is active - a compiler option in the
  toolchain file, a warning in cmake/NxPresets.json with warnings as errors, the language level in the root
  CMakeLists.txt, a clang-tidy check selected by tools/misra/rules.yaml, a MISRA add-on rule kept for C++, or a scan
  of this test; a review rule names a proposal defined in section 4.6; the counts stated in the document match;
- code scan over the own C++ code (bootloader, ecu, libs, platforms without test/tests/3rdparty/generated): no
  exceptions, no RTTI, no heap (placement new allowed), no goto, standard headers only from the allow-list;
- licence rule for ISO 26262 (user 2026-09-22): cited by part and clause only - no reference to one of its tables or
  figures.
Counter-checks with deliberately broken copies of the document and synthetic source snippets show that each rule sees
its fault.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_dev_environment.py
"""

import fnmatch
import pathlib
import re
import subprocess
import tomllib
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[3]
DOC = REPO / "doc" / "safety" / "dev-environment.md"
TOOLCHAIN = REPO / "cmake" / "toolchains" / "NxArmNoneEabi-gcc.cmake"
PRESETS = REPO / "cmake" / "NxPresets.json"
ROOT_CMAKE = REPO / "CMakeLists.txt"
RULES = REPO / "tools" / "misra" / "rules.yaml"
VERSIONS = REPO / "tools" / "ci" / "versions.toml"

SCAN_ROOTS = ("bootloader", "ecu", "libs", "platforms")
SCAN_SKIP_DIRS = {"test", "tests", "3rdparty", "generated"}
SCAN_SUFFIXES = {".cpp", ".hpp", ".h"}
SCANS = ("exceptions", "rtti", "heap", "goto", "headers")
ENFORCEMENT = ("tool", "tool + review", "review")
NXCPP_COLUMNS = ("ID", "Rule (own words)", "Topic", "Enforcement", "Enforced by", "State 2026-09-28")
TOOL_COLUMNS = ("Tool", "Version", "Pinned in", "Phase", "TCL today")


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _block(text: str, name: str) -> str:
    try:
        return text.split(f"<!-- {name}:start -->", 1)[1].split(f"<!-- {name}:end -->", 1)[0]
    except IndexError:
        raise ValueError(f"markers {name}:start/end missing") from None


def table(text: str, name: str, columns: tuple) -> list:
    """Rows of the marked table as dicts; raises ValueError on a malformed table."""
    lines = [line for line in _block(text, name).splitlines() if line.startswith("|")]
    if len(lines) < 3 or tuple(_cells(lines[0])) != columns:
        raise ValueError(f"table {name}: header is not {columns}")
    rows = []
    for line in lines[2:]:
        cells = _cells(line)
        if len(cells) != len(columns):
            raise ValueError(f"table {name}: row with {len(cells)} cells: {line[:60]}")
        rows.append(dict(zip(columns, cells)))
    return rows


# ---------------------------------------------------------------------------------------------------------- header

STATE_LINE = re.compile(r"^(Baseline: v\d+\.\d+\.\d+|Working state: dev at ([0-9a-f]{7,40}) \(after v\d+\.\d+\.\d+\))$",
                        re.M)


def state_line_problems(text: str) -> list:
    head = text.split("\n---", 1)[0]
    if not STATE_LINE.search(head):
        return ["header without the state line of cm-plan.md section 6"]
    return []


# ---------------------------------------------------------------------------------------------------------- tools

def _pinned_text(path: str) -> str:
    file = REPO / path
    return file.read_text(encoding="utf-8") if file.is_file() else ""


def _gitlink(path: str) -> str:
    try:
        out = subprocess.run(["git", "ls-tree", "HEAD", path], cwd=REPO, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return ""
    parts = out.stdout.split()
    return parts[2] if len(parts) >= 3 else ""


def tool_problems(rows: list) -> list:
    problems = []
    for row in rows:
        version = row["Version"].strip("`")
        pinned = re.findall(r"`([^`]+)`", row["Pinned in"])
        if not pinned:
            problems.append(f"{row['Tool']}: no file named in 'Pinned in'")
            continue
        where = pinned[0]
        if where == "external/openbsw":
            link = _gitlink(where)
            if row["Tool"].startswith("Eclipse OpenBSW"):
                if link and not link.startswith(version):
                    problems.append(f"{row['Tool']}: gitlink is {link[:7]}, table says {version}")
            continue  # versions inside the submodule are checked by the test below when it is checked out
        text = _pinned_text(where)
        if not text:
            problems.append(f"{row['Tool']}: pinning file {where} does not exist")
        elif where.endswith(".python-version"):
            if text.strip() != version:
                problems.append(f"{row['Tool']}: {where} pins {text.strip()}, table says {version}")
        elif not re.search(rf"(==|\"|\s){re.escape(version)}(\"|\s|$)", text, re.M):
            problems.append(f"{row['Tool']}: version {version} not pinned in {where}")
    return problems


def versions_toml_problems(rows: list) -> list:
    """Every tool of versions.toml has a row naming versions.toml with exactly that version."""
    data = tomllib.loads(VERSIONS.read_text(encoding="utf-8"))
    names = {"arm_gcc": "arm-none-eabi-gcc", "cmake": "CMake", "ninja": "Ninja", "actionlint": "actionlint"}
    problems = []
    for key, spec in data.get("tools", {}).items():
        label = names.get(key, key)
        matching = [r for r in rows if r["Tool"].startswith(label) and "versions.toml" in r["Pinned in"]]
        if not matching:
            problems.append(f"versions.toml tool {key} has no row")
        elif matching[0]["Version"].strip("`") != spec["version"]:
            problems.append(f"versions.toml tool {key}: {spec['version']}, table says {matching[0]['Version']}")
    return problems


# ---------------------------------------------------------------------------------------------------------- NXCPP

def active_tidy_checks() -> list:
    """The clang-tidy check globs of rules.yaml in order, as (negated, pattern)."""
    checks = yaml.safe_load(RULES.read_text(encoding="utf-8"))["clang_tidy"]["checks"]
    return [(c.startswith("-"), c.lstrip("-")) for c in checks]


def tidy_active(check: str, globs: list) -> bool:
    active = False
    for negated, pattern in globs:
        if fnmatch.fnmatchcase(check, pattern):
            active = not negated
    return active


def _toolchain_cxx_flags() -> str:
    text = TOOLCHAIN.read_text(encoding="utf-8")
    common = re.search(r"set\(_CC_CXX_COMMON(.*?)\)", text, re.S)
    cxx = re.search(r"set\(_CXX_FLAGS(.*?)\)", text, re.S)
    return (common.group(1) if common else "") + (cxx.group(1) if cxx else "")


def _preset_cache() -> dict:
    import json
    data = json.loads(PRESETS.read_text(encoding="utf-8"))
    base = [p for p in data["configurePresets"] if p["name"] == "nx-arm-gcc"]
    return base[0]["cacheVariables"] if base else {}


def item_problem(item: str, globs: list) -> str:
    """'' if the enforcing item is active, else the problem."""
    kind, _, value = item.partition(":")
    kind, value = kind.strip(), value.strip()
    if kind == "compiler":
        return "" if value in _toolchain_cxx_flags().split() else f"compiler option {value} not in the toolchain file"
    if kind == "warning":
        cache = _preset_cache()
        if cache.get("CMAKE_COMPILE_WARNING_AS_ERROR") != "ON":
            return "warnings are not errors in cmake/NxPresets.json"
        return "" if value in cache.get("CMAKE_CXX_FLAGS", "").split() else f"warning {value} not in the C++ flags"
    if kind == "cmake":
        name, _, level = value.partition(" ")
        found = re.search(rf"^set\({re.escape(name)} {re.escape(level)}\)", ROOT_CMAKE.read_text(encoding="utf-8"), re.M)
        return "" if found else f"{value} not set in the root CMakeLists.txt"
    if kind == "clang-tidy":
        return "" if tidy_active(value, globs) else f"clang-tidy check {value} not active in rules.yaml"
    if kind == "cppcheck-addon":
        rules = yaml.safe_load(RULES.read_text(encoding="utf-8"))
        if "--addon=misra" not in rules["cppcheck"]["args"]:
            return "cppcheck MISRA add-on not enabled"
        return "" if value in rules.get("keep_cpp", []) else f"add-on rule {value} not kept for C++ (keep_cpp)"
    if kind == "scan":
        return "" if value in SCANS else f"unknown scan {value}"
    return f"unknown enforcing item {item}"


def nxcpp_problems(text: str, rows: list) -> list:
    problems = []
    globs = active_tidy_checks()
    proposals = set(re.findall(r"^\| (P\d+) \|", text.split("### 4.6", 1)[-1].split("\n---", 1)[0], re.M))
    for number, row in enumerate(rows, 1):
        rid = row["ID"]
        if rid != f"NXCPP-{number:02d}":
            problems.append(f"{rid}: expected NXCPP-{number:02d}")
        enforcement = row["Enforcement"]
        if enforcement not in ENFORCEMENT:
            problems.append(f"{rid}: enforcement '{enforcement}' not one of {ENFORCEMENT}")
            continue
        items = [i for i in re.findall(r"`([^`]+)`", row["Enforced by"]) if ":" in i]
        named = set(re.findall(r"\bP\d+\b", row["Enforced by"]))
        if enforcement == "review":
            if items:
                problems.append(f"{rid}: review rule names a tool item {items}")
            if not named or not named <= proposals:
                problems.append(f"{rid}: review rule without a proposal of section 4.6")
            continue
        if not items:
            problems.append(f"{rid}: tool rule without an enforcing item")
        for item in items:
            problem = item_problem(item, globs)
            if problem:
                problems.append(f"{rid}: {problem}")
        if not named <= proposals:
            problems.append(f"{rid}: proposal {sorted(named - proposals)} not defined in section 4.6")
    return problems


def count_problems(text: str, rows: list) -> list:
    """The counts in section 0 and below the NXCPP table match the table."""
    tool = sum(1 for r in rows if r["Enforcement"] in ("tool", "tool + review"))
    partial = sum(1 for r in rows if r["Enforcement"] == "tool + review")
    review = sum(1 for r in rows if r["Enforcement"] == "review")
    problems = []
    summary = text.split("\n## 1.", 1)[0]
    for phrase in (f"{len(rows)} rules", f"**{tool} enforced by a tool**", f"**{review} by review**"):
        if phrase not in summary:
            problems.append(f"section 0 does not state '{phrase}'")
    counted = text.split("<!-- nxcpp:end -->", 1)[-1][:600]
    for phrase in (f"{len(rows)} rules", f"**{tool} by tool**", f"{tool - partial} fully", f"{partial}\nas tool + review",
                   f"**{review} by review**"):
        if phrase not in counted and phrase.replace("\n", " ") not in counted:
            problems.append(f"the count below the NXCPP table does not state '{phrase}'")
    return problems


# ---------------------------------------------------------------------------------------------------------- scan

_TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|R"([^(\s]*)\(.*?\)\1"|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)


def strip_code(source: str) -> str:
    """Source without comments and string/char literals (line structure kept)."""
    def blank(match):
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))
    return _TOKEN.sub(blank, source)


def allowed_headers(text: str) -> set:
    return set(re.findall(r"`([a-z_]+)`", _block(text, "nxcpp-headers")))


def scan_source(source: str, allowed: set) -> list:
    """Scan findings in one C++ source as (scan, line number, text)."""
    findings = []
    headers = re.finditer(r"^\s*#\s*include\s*<([A-Za-z_]+)>", source, re.M)  # before stripping: <...> is no string
    for match in headers:
        if match.group(1) not in allowed:
            findings.append(("headers", source.count("\n", 0, match.start()) + 1, match.group(0).strip()))
    code = strip_code(source)
    patterns = {
        "exceptions": r"\b(throw|try|catch)\b",
        "rtti": r"\b(dynamic_cast|typeid)\b",
        "goto": r"\bgoto\b",
        "heap": r"\b(malloc|calloc|realloc|free)\s*\(|\bnew\s+(?!\()[A-Za-z_:]|(?<!operator)(?<!=)\s\bdelete\b(?!\s*;)"
                r"|^\s*delete\b",
    }
    for scan, pattern in patterns.items():
        for match in re.finditer(pattern, code, re.M):
            line = code.count("\n", 0, match.start()) + 1
            text = source.splitlines()[line - 1].strip()
            if scan == "heap" and re.search(r"=\s*delete\s*;", text):
                continue
            findings.append((scan, line, text))
    return findings


def scan_repository(allowed: set) -> list:
    findings = []
    for root in SCAN_ROOTS:
        for path in sorted((REPO / root).rglob("*")):
            if path.suffix not in SCAN_SUFFIXES or not path.is_file():
                continue
            if SCAN_SKIP_DIRS & set(path.relative_to(REPO).parts[:-1]):
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            for scan, line, text in scan_source(source, allowed):
                if re.search(r"NXCPP-\d+ deviation: DEV-\d+", text):
                    continue
                findings.append(f"{path.relative_to(REPO).as_posix()}:{line}: {scan}: {text}")
    return findings


# ---------------------------------------------------------------------------------------------------------- licence

def licence_problems(text: str) -> list:
    problems = []
    for pattern in (r"\bTable\s+\d", r"\bFigure\s+\d", r"\bTable [A-Z]\.\d"):
        for match in re.finditer(pattern, text):
            problems.append(f"reference to a table or figure of the standard: '{match.group(0)}'")
    return problems


# ---------------------------------------------------------------------------------------------------------- tests

class DevEnvironmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.tools = table(cls.text, "tools", TOOL_COLUMNS)
        cls.nxcpp = table(cls.text, "nxcpp", NXCPP_COLUMNS)

    def test_state_line(self):
        self.assertEqual(state_line_problems(self.text), [])

    def test_tool_versions_match_the_pins(self):
        self.assertEqual(tool_problems(self.tools), [])
        self.assertEqual(versions_toml_problems(self.tools), [])

    def test_submodule_versions(self):
        freertos = REPO / "external" / "openbsw" / "libs" / "3rdparty" / "freeRtos"
        etl = REPO / "external" / "openbsw" / "libs" / "3rdparty" / "etl" / "include" / "etl" / "version.h"
        if not freertos.is_dir() or not etl.is_file():
            self.skipTest("submodule external/openbsw not checked out")
        rows = {r["Tool"].split(" (")[0]: r["Version"] for r in self.tools}
        kernel = set()
        for path in freertos.rglob("*.h"):
            kernel.update(re.findall(r"FreeRTOS Kernel V(\d+\.\d+\.\d+)", path.read_text(errors="replace")))
        self.assertEqual(kernel, {rows["FreeRTOS"]})
        parts = dict(re.findall(r"#define ETL_VERSION_(MAJOR|MINOR|PATCH)\s+(\d+)", etl.read_text()))
        self.assertEqual(f"{parts['MAJOR']}.{parts['MINOR']}.{parts['PATCH']}", rows["ETL"])

    def test_nxcpp_rules_are_enforced_as_stated(self):
        self.assertEqual(nxcpp_problems(self.text, self.nxcpp), [])
        self.assertEqual(count_problems(self.text, self.nxcpp), [])

    def test_code_scan(self):
        self.assertEqual(scan_repository(allowed_headers(self.text)), [])

    def test_licence_rule(self):
        self.assertEqual(licence_problems(self.text), [])


class CounterChecks(unittest.TestCase):
    """Each rule sees a fault built into a copy of the document or into a synthetic source."""

    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")

    def _nxcpp(self, text):
        return nxcpp_problems(text, table(text, "nxcpp", NXCPP_COLUMNS))

    def test_missing_state_line(self):
        broken = re.sub(r"^(Working state|Baseline): .*$", "", self.text, count=1, flags=re.M)  # REL-020: candidate carries Baseline
        self.assertTrue(state_line_problems(broken))

    def test_wrong_version(self):
        broken = self.text.replace("| CMake | 4.4.3 |", "| CMake | 4.4.2 |")
        rows = table(broken, "tools", TOOL_COLUMNS)
        self.assertTrue(tool_problems(rows))
        self.assertTrue(versions_toml_problems(rows))

    def test_missing_versions_toml_row(self):
        broken = re.sub(r"^\| Ninja \|.*\n", "", self.text, flags=re.M)
        self.assertTrue(versions_toml_problems(table(broken, "tools", TOOL_COLUMNS)))

    def test_wrong_python(self):
        broken = self.text.replace("| Python | 3.12.10 |", "| Python | 3.12.9 |")
        self.assertTrue(tool_problems(table(broken, "tools", TOOL_COLUMNS)))

    def test_inactive_clang_tidy_check(self):
        broken = self.text.replace("`clang-tidy: hicpp-signed-bitwise`", "`clang-tidy: modernize-use-auto`")
        self.assertTrue(any("modernize-use-auto" in p for p in self._nxcpp(broken)))

    def test_excluded_clang_tidy_check(self):
        # bugprone-* is active, but this one is switched off in rules.yaml
        broken = self.text.replace("`clang-tidy: bugprone-narrowing-conversions`",
                                   "`clang-tidy: bugprone-easily-swappable-parameters`")
        self.assertTrue(any("easily-swappable" in p for p in self._nxcpp(broken)))

    def test_unknown_compiler_option(self):
        broken = self.text.replace("`compiler: -fno-rtti`", "`compiler: -fno-everything`")
        self.assertTrue(any("-fno-everything" in p for p in self._nxcpp(broken)))

    def test_unknown_warning(self):
        broken = self.text.replace("`warning: -Wvla`", "`warning: -Wconversion`")
        self.assertTrue(any("-Wconversion" in p for p in self._nxcpp(broken)))

    def test_addon_rule_not_kept(self):
        broken = self.text.replace("`cppcheck-addon: misra-c2012-17.2`", "`cppcheck-addon: misra-c2012-15.1`")
        self.assertTrue(any("15.1" in p for p in self._nxcpp(broken)))

    def test_review_rule_without_proposal(self):
        broken = self.text.replace("| review | P1 |", "| review | later |")
        self.assertTrue(any("NXCPP-26" in p for p in self._nxcpp(broken)))

    def test_tool_rule_without_item(self):
        broken = self.text.replace("| tool | `scan: goto` |", "| tool | the scan |")
        self.assertTrue(any("NXCPP-19" in p for p in self._nxcpp(broken)))

    def test_gap_in_numbering(self):
        broken = self.text.replace("| NXCPP-12 |", "| NXCPP-13 |", 1)
        self.assertTrue(self._nxcpp(broken))

    def test_wrong_count(self):
        broken = self.text.replace("**20 by tool**", "**19 by tool**")
        self.assertTrue(count_problems(broken, table(broken, "nxcpp", NXCPP_COLUMNS)))

    def test_table_reference(self):
        self.assertTrue(licence_problems(self.text + "\nsee ISO 26262-6 Table 1\n"))

    def test_scan_finds_each_construct(self):
        allowed = allowed_headers(self.text)
        cases = {
            "exceptions": "void f() { throw 1; }",
            "rtti": "Base* b = nullptr; auto* d = dynamic_cast<Derived*>(b);",
            "heap": "int* p = new int[4];",
            "goto": "void f() { goto end; end: return; }",
            "headers": "#include <vector>\n",
        }
        for scan, source in cases.items():
            with self.subTest(scan=scan):
                self.assertIn(scan, [f[0] for f in scan_source(source, allowed)])
        self.assertIn("heap", [f[0] for f in scan_source("void f(int* p) { delete p; }", allowed)])
        self.assertIn("heap", [f[0] for f in scan_source("void f() { void* p = malloc(4U); }", allowed)])

    def test_scan_accepts_what_the_subset_allows(self):
        allowed = allowed_headers(self.text)
        source = (
            "#include <cstdint>\n#include <etl/vector.h>\n#include <stdint.h>\n"
            "// throw away the half measurement, try again, goto next\n"
            "/* delete the entry, new value, typeid */\n"
            "char const* s = \"try new delete goto\";\n"
            "struct A { A(A const&) = delete; A& operator=(A const&) = delete; };\n"
            "void g(void* mem) { new (mem) A(); }\n"
            "inline void* operator new(unsigned int, void* ptr) { return ptr; }\n"
            "bool isNewer = true; int trying = 0; int deleted = 0;\n")
        self.assertEqual(scan_source(source, allowed), [])


if __name__ == "__main__":
    unittest.main()
