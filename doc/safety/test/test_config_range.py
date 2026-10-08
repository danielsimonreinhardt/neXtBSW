"""SYS-92 (N3): consistency of doc/safety/config-range.md with the module manifests and the repository.

Checks
- every project parameter of the stage-1 safety modules (module manifests with `project_parameters: true` under
  platforms/*/port/safety) is a row of the value register with YAML path modules.<module>.<parameter>, and the
  declared range and default of the row are the ones of the manifest;
- every row has a level (derivative, board, project, or code for a constant), a route (range or data set), named
  mechanisms and a relation column; a row with route "range" names at least one check that enforces a relation
  (a checks.py function, a static_assert, a generator refusal, an image check);
- every check function named "`name` in `checks.py`" exists in tools/nxconfig/checks.py; every test named exists in
  tools/test/tests.yaml; every repository path named exists;
- findings, assumptions of use and proposals refer to each other consistently; proposals have a real "done when";
- the counts of section 0 (values, range/data-set split, levels) match the register;
- once doc/safety/safety-manual.md exists (DOC-19) it references this document (proposal C4);
- licence rule for ISO 26262 (part and clause only).
Counter-checks with deliberately broken copies and changed manifests show that each rule sees its fault.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_config_range.py
"""

import pathlib
import re
import sys
import unittest

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_safety_plan import licence_problems, table  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
DOC = REPO / "doc" / "safety" / "config-range.md"
MANUAL = REPO / "doc" / "safety" / "safety-manual.md"
CHECKS = REPO / "tools" / "nxconfig" / "checks.py"
TESTS = REPO / "tools" / "test" / "tests.yaml"

VALUE_COLUMNS = ("ID", "Value", "YAML path", "Level", "Declared", "Mechanisms", "Relation checked", "Project verified",
                 "Board", "Route")
FINDING_COLUMNS = ("ID", "Finding", "Consequence", "Proposal")
AOU_COLUMNS = ("ID", "Assumption of use", "Values")
PROPOSAL_COLUMNS = ("ID", "Title", "Done when", "Prio")
LEVELS = ("derivative", "board", "project", "code")
ROUTES = ("range", "data set")
RANGE_EVIDENCE = re.compile(r"static_assert|#error|refuse|`[_a-z]+` in `|checks\.py|calibration\.py|memory\.py|"
                            r"image|rtegen|bswgen", re.IGNORECASE)


def manifest_parameters(repo: pathlib.Path = REPO) -> dict:
    """modules.<module>.<parameter> -> spec of the stage-1 safety modules with project parameters."""
    result = {}
    for path in sorted(repo.glob("platforms/*/port/safety/*/module.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not data.get("project_parameters"):
            continue
        for name, spec in (data.get("parameters") or {}).items():
            result[f"modules.{data['module']}.{name}"] = spec
    return result


def annex_licence_problems(text: str) -> list:
    """The shared licence rule plus references to annex tables and figures ("Table C.1"), which its pattern misses."""
    return licence_problems(text) + [f"reference to an annex table or figure: '{m}'"
                                     for m in re.findall(r"\b(?:Table|Figure)\s+[A-Z]\.\d+", text)]


def numbers(cell: str) -> str:
    """Cell text with thousands separators removed (86 400 000 -> 86400000)."""
    return re.sub(r"(?<=\d)[   ](?=\d{3}\b)", "", cell)


class Register:
    def __init__(self, text: str, params: dict, tests: set, checks_text: str, exists=None, manual=None):
        self.text = text
        self.params = params
        self.tests = tests
        self.checks_text = checks_text
        self.exists = exists or (lambda p: (REPO / p).exists())
        self.manual = manual
        self.values = table(text, "cfg-values", VALUE_COLUMNS)
        self.findings = table(text, "cfg-findings", FINDING_COLUMNS)
        self.aou = table(text, "cfg-aou", AOU_COLUMNS)
        self.proposals = table(text, "cfg-proposals", PROPOSAL_COLUMNS)

    def configuration_values(self) -> list:
        return [r for r in self.values if r["Level"] != "code"]

    def manifest_problems(self) -> list:
        problems = []
        by_path = {}
        for row in self.values:
            for path in re.findall(r"`(modules\.[^`]+)`", row["YAML path"]):
                by_path[path] = row
        for path, spec in self.params.items():
            row = by_path.get(path)
            if row is None:
                problems.append(f"manifest parameter {path} is not in the value register")
                continue
            declared = numbers(row["Declared"])
            for key in ("min", "max", "default"):
                if key in spec and not re.search(rf"(?<![\d.]){spec[key]}(?![\d.])", declared):
                    problems.append(f"{row['ID']}: {key} {spec[key]} of the manifest not in '{row['Declared']}'")
        for path in by_path:
            if path not in self.params:
                problems.append(f"register names {path}, which no stage-1 manifest declares")
        return problems

    def row_problems(self) -> list:
        problems = []
        ids = [r["ID"] for r in self.values]
        if ids != [f"CV-{n:02d}" for n in range(1, len(ids) + 1)]:
            problems.append(f"value IDs not CV-01 ... in order: {ids}")
        for row in self.values:
            where = row["ID"]
            if row["Level"] not in LEVELS:
                problems.append(f"{where}: level '{row['Level']}' not in {LEVELS}")
            if row["Route"] not in ROUTES:
                problems.append(f"{where}: route '{row['Route']}' not in {ROUTES}")
            if not re.search(r"SM-\d+|SSR-\d+", row["Mechanisms"]):
                problems.append(f"{where}: no mechanism or requirement named")
            if row["Route"] == "range" and not RANGE_EVIDENCE.search(row["Relation checked"]):
                problems.append(f"{where}: route range without a check that enforces the relation")
            for name in re.findall(r"`(_?[a-z][a-z_]*)` in `(?:tools/nxconfig/)?checks\.py`", row["Relation checked"]):
                if not re.search(rf"^def {name}\(", self.checks_text, flags=re.MULTILINE):
                    problems.append(f"{where}: check function {name} not in tools/nxconfig/checks.py")
            for cell in (row["Relation checked"], row["Project verified"]):
                for token in re.findall(r"`([^`]+)`", cell):
                    if "/" in token and not token.startswith(("modules.", "memory.")):
                        if not self.exists(token.split(" ")[0]):
                            problems.append(f"{where}: path {token} does not exist")
                    elif re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", token) and token not in self.tests:
                        problems.append(f"{where}: test {token} not in tools/test/tests.yaml")
        return problems

    def reference_problems(self) -> list:
        problems = []
        proposals = {p["ID"] for p in self.proposals}
        aou = {a["ID"] for a in self.aou}
        values = {v["ID"] for v in self.values}
        used = set()
        for row in self.findings:
            for ref in re.findall(r"\b(?:C\d+|AoU-CFG-\d+)\b", row["Proposal"]):
                used.add(ref)
                if ref not in proposals | aou:
                    problems.append(f"{row['ID']}: {ref} not defined")
            if not re.findall(r"\b(?:C\d+|AoU-CFG-\d+)\b", row["Proposal"]):
                problems.append(f"{row['ID']}: finding without proposal or assumption")
        for row in self.aou:
            if not re.fullmatch(r"AoU-CFG-\d+", row["ID"]):
                problems.append(f"{row['ID']}: not of the form AoU-CFG-n")
            named = re.findall(r"CV-\d\d", row["Values"])
            if not named and row["Values"] != "all":
                problems.append(f"{row['ID']}: names no value")
            for ref in named:
                if ref not in values:
                    problems.append(f"{row['ID']}: value {ref} not defined")
        before = self.text.split("<!-- cfg-proposals:start -->", 1)[0]
        for prop in self.proposals:
            if len(prop["Done when"]) < 40:
                problems.append(f"proposal {prop['ID']}: 'done when' too short")
            if not re.search(rf"\b{prop['ID']}\b", before):
                problems.append(f"proposal {prop['ID']} is used nowhere")
        return problems

    def summary_problems(self) -> list:
        problems = []
        config = self.configuration_values()
        line = next((ln for ln in self.text.splitlines() if "<!-- cfg-counts -->" in ln), "")
        levels = {lv: sum(1 for r in config if r["Level"] == lv) for lv in LEVELS}
        expected = [f"**{len(config)}** configuration values",
                    f"{levels['derivative']} derivative, {levels['board']} board, {levels['project']} project level"]
        problems += [f"section 0 does not say '{e}'" for e in expected if e not in line]
        ranged = sum(1 for r in config if r["Route"] == "range")
        for phrase in (f"{ranged} of {len(config)} values", f"{len(config) - ranged} of {len(config)} values"):
            if phrase not in self.text:
                problems.append(f"section 0 does not say '{phrase}'")
        last = self.aou[-1]["ID"] if self.aou else ""
        if f"AoU-CFG-1 … {last}" not in self.text:
            problems.append(f"the text does not name AoU-CFG-1 … {last}")
        return problems

    def manual_problems(self) -> list:
        if self.manual is not None and "config-range.md" not in self.manual:
            return ["safety manual exists but does not reference config-range.md (proposal C4)"]
        return []


class ConfigRangeConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.params = manifest_parameters()
        cls.tests = {t["name"] for t in yaml.safe_load(TESTS.read_text(encoding="utf-8"))["tests"]}
        cls.checks_text = CHECKS.read_text(encoding="utf-8")
        cls.manual = MANUAL.read_text(encoding="utf-8") if MANUAL.exists() else None
        cls.reg = Register(cls.text, cls.params, cls.tests, cls.checks_text, manual=cls.manual)

    def reg_of(self, text=None, params=None, exists=None, manual=None):
        return Register(self.text if text is None else text, self.params if params is None else params,
                        self.tests, self.checks_text, exists, manual)

    def test_manifests_found(self):
        for path in ("modules.safeLifecycle.task_period_ms", "modules.safeLifecycle.watchdog_timeout_ms",
                     "modules.safeSupervisor.reset_loop_limit", "modules.safeMemory.rom_check_period_ms"):
            self.assertIn(path, self.params)

    def test_manifests(self):
        self.assertEqual(self.reg.manifest_problems(), [])

    def test_rows(self):
        self.assertEqual(self.reg.row_problems(), [])

    def test_references(self):
        self.assertEqual(self.reg.reference_problems(), [])

    def test_summary(self):
        self.assertEqual(self.reg.summary_problems(), [])

    def test_safety_manual_reference(self):
        self.assertEqual(self.reg.manual_problems(), [])

    def test_standard_cited_by_part_and_clause_only(self):
        self.assertEqual(annex_licence_problems(self.text), [])

    def test_counter_checks(self):
        def sub(source, old, new, count=-1):
            self.assertTrue(old in source, f"counter-check edit does not hit: {old[:80]}")
            return source.replace(old, new, count)

        text = self.text
        row11 = next(ln for ln in text.splitlines() if ln.startswith("| CV-11 |"))
        row02 = next(ln for ln in text.splitlines() if ln.startswith("| CV-02 |"))
        # manifests: a new parameter, a changed maximum, a row for a parameter that does not exist
        params = dict(self.params, **{"modules.safeLifecycle.new_time_ms": {"min": 1, "max": 5, "default": 2}})
        self.assertTrue(any("new_time_ms" in p for p in self.reg_of(params=params).manifest_problems()))
        params = dict(self.params)
        params["modules.safeLifecycle.watchdog_timeout_ms"] = dict(params["modules.safeLifecycle.watchdog_timeout_ms"],
                                                                    max=511)
        self.assertTrue(self.reg_of(params=params).manifest_problems())
        self.assertTrue(self.reg_of(sub(text, "`modules.safeMemory.rom_check_period_ms`",
                                         "`modules.safeMemory.rom_period_ms`", 1)).manifest_problems())
        self.assertTrue(self.reg_of(sub(text, row11 + "\n", "", 1)).manifest_problems())
        # rows: level, route, range without check, unknown check function, unknown test, missing path, ID order
        self.assertTrue(self.reg_of(sub(text, row11, sub(row11, "| project |", "| ecu |"), 1)).row_problems())
        self.assertTrue(self.reg_of(sub(text, row11, sub(row11, "| data set |", "| maybe |"), 1)).row_problems())
        broken = sub(text, row11, sub(row11, "| data set |", "| range |"), 1)
        self.assertTrue(any("without a check" in p for p in self.reg_of(broken).row_problems()))
        self.assertTrue(self.reg_of(sub(text, "`_safety_roles` in `checks.py`", "`_safety_rolez` in `checks.py`", 1))
                        .row_problems())
        self.assertTrue(self.reg_of(sub(text, row02, sub(row02, "`nxconfig-watchdog`", "`nxconfig-watchdogs`"), 1))
                        .row_problems())
        exists = lambda p: p != "platforms/s32k1xx/port/main/src/main.cpp" and (REPO / p).exists()   # noqa: E731
        self.assertTrue(self.reg_of(exists=exists).row_problems())
        self.assertTrue(self.reg_of(sub(text, "| CV-11 |", "| CV-21 |", 1)).row_problems())
        # references: finding without proposal, undefined proposal, unused proposal, AoU naming an unknown value
        self.assertTrue(self.reg_of(sub(text, "| C2, AoU-CFG-4 |", "| — |", 1)).reference_problems())
        self.assertTrue(self.reg_of(sub(text, "| C2, AoU-CFG-4 |", "| C7 |", 1)).reference_problems())
        self.assertTrue(self.reg_of(sub(text, "| C3 | Halt-deadman", "| C9 | Halt-deadman", 1)).reference_problems())
        self.assertTrue(self.reg_of(sub(text, "| CV-20 |\n", "| CV-29 |\n", 1)).reference_problems())
        # summary
        self.assertTrue(self.reg_of(sub(text, "**19** configuration values", "**18** configuration values", 1))
                        .summary_problems())
        self.assertTrue(self.reg_of(sub(text, row11, sub(row11, "| data set |", "| range |"), 1)).summary_problems())
        # safety manual, licence rule, malformed tables
        self.assertTrue(self.reg_of(manual="# Safety manual\n").manual_problems())
        self.assertTrue(annex_licence_problems(text + "\nsee ISO 26262-6, Table C.1\n"))
        self.assertTrue(annex_licence_problems(text + "\nsee ISO 26262-6, Table 13\n"))
        with self.assertRaises(ValueError):
            self.reg_of(sub(text, "<!-- cfg-values:start -->", "", 1))


if __name__ == "__main__":
    sys.exit(unittest.main())
