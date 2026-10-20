"""SYS-92 (N2): consistency of the unit register of doc/safety/unit-design.md with the source tree and PLAT-91.

Checks
- the register lists every translation unit (.c, .cpp, .S; tests excluded) below the source paths of the safety
  units of PLAT-91 (its `touches` in doc/todo.yaml that are code directories: E2E, ecu/safety, the family safety
  port) exactly once, and every extra unit it lists exists;
- the units that N2 names — watchdog handling, fault handlers, safety task — are all in the register and described
  in unit-design.md: every unit of the components C-WDG, C-LCY and C-HFH of sw-architecture.md section 1 and
  SafetySystem;
- a `described` row names existing design documents, each of which names the unit; `partial` and `missing` rows
  have a follow-up that is a task of doc/todo.yaml or a proposal of section 7, and every proposal is used or
  explains itself; statuses are one of described/partial/missing;
- the three designs UD-WDG, UD-HFH, UD-STK each have interface, behaviour, data and verification parts;
- the counts of section 0 match the register and the tree;
- consistency table K1..: every row says "agrees" (a disagreement is a finding for the architecture or HSI);
- licence rule for ISO 26262 (part and clause only).
Counter-checks with deliberately broken copies and a changed tree show that each rule sees its fault.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_unit_design.py
"""

import pathlib
import re
import sys
import unittest

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_safety_plan import _cells, licence_problems, load_tasks, table  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
DOC = REPO / "doc" / "safety" / "unit-design.md"
ARCH = REPO / "doc" / "safety" / "sw-architecture.md"

REGISTER_COLUMNS = ("Unit", "Source", "Component", "Design description", "Status", "Follow-up")
PROPOSAL_COLUMNS = ("ID", "Title", "Done when", "Prio")
CONSISTENCY_COLUMNS = ("#", "Statement", "Source", "Code", "Result")
STATUS = ("described", "partial", "missing")
UNIT_SUFFIXES = (".c", ".cpp", ".S")
# N2: the units whose design this document provides
N2_COMPONENTS = ("C-WDG", "C-LCY", "C-HFH")
N2_EXTRA_UNITS = ("SafetySystem",)
DESIGNS = ("UD-WDG", "UD-HFH", "UD-STK")
DESIGN_PARTS = ("Interfaces", "Behaviour", "Data", "Verification")


def paths(cell: str) -> list:
    return [p for p in re.findall(r"`([^`]+)`", cell) if "/" in p]


def plat91_roots(todo: dict) -> list:
    """Source directories of the safety units decided for PLAT-91 (its touches that are code, not rules or docs)."""
    task = next(t for t in todo["tasks"] if t["id"] == "PLAT-91")
    return [t.rstrip("/") for t in task.get("touches", [])
            if not t.startswith(("doc/", "tools/")) and not pathlib.PurePosixPath(t).suffix]


def tree_units(roots: list, repo: pathlib.Path = REPO) -> set:
    units = set()
    for root in roots:
        for path in (repo / root).rglob("*"):
            rel = path.relative_to(repo).as_posix()
            if path.is_file() and path.suffix in UNIT_SUFFIXES and "/test/" not in f"/{rel}/":
                units.add(rel)
    return units


def architecture_units(arch_text: str, components: tuple) -> dict:
    """component ID -> unit names of the 'Units (main)' column of sw-architecture.md section 1."""
    result = {}
    for line in arch_text.splitlines():
        if not line.startswith("| C-"):
            continue
        cells = _cells(line)
        if cells[0] in components:
            names = re.findall(r"`([^`]+)`", cells[2])
            result[cells[0]] = [pathlib.PurePosixPath(n).stem for n in names]
    return result


class Register:
    def __init__(self, text: str, tasks: dict, plat91: set, arch: dict, exists=None, read=None):
        self.text = text
        self.tasks = tasks
        self.plat91 = plat91
        self.arch = arch
        self.exists = exists or (lambda p: (REPO / p).exists())
        self.read = read or (lambda p: (REPO / p).read_text(encoding="utf-8"))
        self.rows = table(text, "unit-register", REGISTER_COLUMNS)
        self.proposals = table(text, "unit-proposals", PROPOSAL_COLUMNS)
        self.consistency = table(text, "unit-consistency", CONSISTENCY_COLUMNS)

    def source(self, row) -> str:
        found = paths(row["Source"])
        return found[0] if found else ""

    def tree_problems(self) -> list:
        problems = []
        sources = [self.source(r) for r in self.rows]
        for dup in sorted({s for s in sources if sources.count(s) > 1}):
            problems.append(f"unit listed twice: {dup}")
        for unit in sorted(self.plat91 - set(sources)):
            problems.append(f"safety unit of PLAT-91 missing in the register: {unit}")
        for src in sources:
            if not src or not self.exists(src):
                problems.append(f"register names a unit that does not exist: '{src}'")
            elif not src.endswith(UNIT_SUFFIXES):
                problems.append(f"{src} is not a translation unit")
        return problems

    def status_problems(self) -> list:
        problems = []
        proposals = {p["ID"] for p in self.proposals}
        used = set()
        for row in self.rows:
            where = self.source(row) or row["Unit"]
            status = row["Status"]
            if status not in STATUS:
                problems.append(f"{where}: status '{status}' not in {STATUS}")
                continue
            docs = paths(row["Design description"])
            for doc in docs:
                if not self.exists(doc):
                    problems.append(f"{where}: design document {doc} does not exist")
            follow = [f.strip() for f in row["Follow-up"].split(",") if f.strip() not in ("", "—")]
            used.update(follow)
            if status == "described":
                if not docs:
                    problems.append(f"{where}: described without a design document")
                elif not any(self.exists(d) and re.search(rf"\b{re.escape(row['Unit'])}\b", self.read(d))
                             for d in docs):
                    problems.append(f"{where}: no design document names the unit {row['Unit']}")
            else:
                if not follow:
                    problems.append(f"{where}: {status} without a follow-up")
                for ref in follow:
                    if ref not in proposals and ref not in self.tasks:
                        problems.append(f"{where}: follow-up {ref} is neither a task nor a proposal")
        for prop in self.proposals:
            if len(prop["Done when"]) < 40:
                problems.append(f"proposal {prop['ID']}: 'done when' too short")
            if prop["ID"] not in used and prop["ID"] not in self.text.split("<!-- unit-proposals:start -->")[0]:
                problems.append(f"proposal {prop['ID']} is used nowhere")
        return problems

    def n2_problems(self) -> list:
        problems = []
        by_unit = {}
        for row in self.rows:
            by_unit.setdefault(row["Unit"], []).append(row)
        wanted = [(c, u) for c, units in self.arch.items() for u in units] + [("C-INT", u) for u in N2_EXTRA_UNITS]
        for component in N2_COMPONENTS:
            if component not in self.arch:
                problems.append(f"component {component} not found in sw-architecture.md section 1")
        for component, unit in wanted:
            rows = by_unit.get(unit)
            # header-only parts of a component (safeOutputs.h) are described with the unit that owns them
            if rows is None:
                if not re.search(rf"`{re.escape(unit)}(\.h)?`", self.text):
                    problems.append(f"{component}: unit {unit} of the architecture is neither in the register "
                                    f"nor named in unit-design.md")
                continue
            for row in rows:
                if row["Status"] != "described" or "doc/safety/unit-design.md" not in row["Design description"]:
                    problems.append(f"{component}: unit {unit} is not described in unit-design.md")
        for design in DESIGNS:
            match = re.search(rf"^## \d+\. {design} .*?(?=^## )", self.text, flags=re.MULTILINE | re.DOTALL)
            if not match:
                problems.append(f"design {design} missing")
                continue
            for part in DESIGN_PARTS:
                if not re.search(rf"^### \d+\.\d+ {part}", match.group(0), flags=re.MULTILINE):
                    problems.append(f"design {design} has no part '{part}'")
        return problems

    def consistency_problems(self) -> list:
        problems = []
        if not self.consistency:
            problems.append("consistency table empty")
        for row in self.consistency:
            if not row["Result"].startswith("agrees"):
                problems.append(f"{row['#']}: result '{row['Result']}' - a disagreement is a finding to resolve")
        return problems

    def summary_problems(self) -> list:
        rows = self.rows
        inside = [r for r in rows if self.source(r) in self.plat91]
        count = {s: sum(1 for r in rows if r["Status"] == s) for s in STATUS}
        expected = (f"**{len(rows)}** translation units: {len(inside)} under the PLAT-91 paths",
                    f"and {len(rows) - len(inside)} outside them",
                    f"Design status: {count['described']} described, {count['partial']} partial, "
                    f"{count['missing']} missing")
        line = next((ln for ln in self.text.splitlines() if "<!-- unit-counts -->" in ln), "")
        problems = [f"section 0 does not say '{e}'" for e in expected if e not in line]
        described_here = [r for r in rows if "doc/safety/unit-design.md" in r["Design description"]
                          and r["Status"] == "described"]
        if f"**{len(described_here)}** translation units in three designs" not in self.text:
            problems.append(f"section 0 does not say '**{len(described_here)}** translation units in three designs'")
        consistency = len(self.consistency)
        if f"{consistency} statements" not in self.text or f"{consistency} agree" not in self.text:
            problems.append(f"section 0 does not count {consistency} consistency statements")
        return problems


def load_all():
    todo = yaml.safe_load((REPO / "doc" / "todo.yaml").read_text(encoding="utf-8"))
    tasks = {t["id"]: t.get("status", "open") for t in todo["tasks"]}
    plat91 = tree_units(plat91_roots(todo))
    arch = architecture_units(ARCH.read_text(encoding="utf-8"), N2_COMPONENTS)
    return todo, tasks, plat91, arch


class UnitDesignConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.todo, cls.tasks, cls.plat91, cls.arch = load_all()
        cls.reg = Register(cls.text, cls.tasks, cls.plat91, cls.arch)

    def reg_of(self, text=None, plat91=None, arch=None, exists=None, tasks=None):
        return Register(self.text if text is None else text, self.tasks if tasks is None else tasks,
                        self.plat91 if plat91 is None else plat91, self.arch if arch is None else arch, exists)

    def test_plat91_roots(self):
        roots = plat91_roots(self.todo)
        for expected in ("libs/bsw/e2e", "ecu/safety", "platforms/s32k1xx/port/safety"):
            self.assertIn(expected, roots)
        self.assertNotIn("tools/misra/rules.yaml", roots)
        self.assertGreaterEqual(len(self.plat91), 20)

    def test_architecture_components_found(self):
        self.assertEqual(sorted(self.arch), sorted(N2_COMPONENTS))
        self.assertIn("SafeWatchdog", self.arch["C-WDG"])
        self.assertIn("SafetyManager", self.arch["C-LCY"])

    def test_tree(self):
        self.assertEqual(self.reg.tree_problems(), [])

    def test_status(self):
        self.assertEqual(self.reg.status_problems(), [])

    def test_n2_units_described(self):
        self.assertEqual(self.reg.n2_problems(), [])

    def test_consistency(self):
        self.assertEqual(self.reg.consistency_problems(), [])

    def test_summary(self):
        self.assertEqual(self.reg.summary_problems(), [])

    def test_standard_cited_by_part_and_clause_only(self):
        self.assertEqual(licence_problems(self.text), [])

    def test_counter_checks(self):
        def sub(source, old, new, count=-1):
            self.assertTrue(old in source, f"counter-check edit does not hit: {old[:80]}")
            return source.replace(old, new, count)

        text = self.text
        row_wdg = next(ln for ln in text.splitlines() if ln.startswith("| SafeWatchdog |"))
        row_io = next(ln for ln in text.splitlines() if ln.startswith("| SafeIo |"))
        # tree: a unit of PLAT-91 dropped, a new file in the tree, a unit that does not exist, a duplicate
        self.assertTrue(self.reg_of(sub(text, row_io + "\n", "", 1)).tree_problems())
        extra = set(self.plat91) | {"ecu/safety/safeWatchdog/src/safeWatchdog/NewUnit.cpp"}
        self.assertTrue(any("NewUnit.cpp" in p for p in self.reg_of(plat91=extra).tree_problems()))
        self.assertTrue(self.reg_of(sub(text, "safeWatchdog/SafeWatchdog.cpp`", "safeWatchdog/Gone.cpp`", 1))
                        .tree_problems())
        self.assertTrue(self.reg_of(sub(text, row_io, row_io + "\n" + row_io, 1)).tree_problems())
        # tree scan: tests are no units, sources are
        units = tree_units(["ecu/safety/safeWatchdog"])
        self.assertIn("ecu/safety/safeWatchdog/src/safeWatchdog/CyclePeriodCheck.cpp", units)
        self.assertFalse(any("/test/" in u for u in units))
        # status: unknown word, missing without follow-up, unknown follow-up, described in a doc not naming it,
        # design document missing
        self.assertTrue(self.reg_of(sub(text, row_io, sub(row_io, "| missing |", "| todo |"), 1)).status_problems())
        self.assertTrue(self.reg_of(sub(text, row_io, sub(row_io, "| U1 |", "| — |"), 1)).status_problems())
        self.assertTrue(self.reg_of(sub(text, row_io, sub(row_io, "| U1 |", "| U9 |"), 1)).status_problems())
        broken = sub(text, row_io, "| SafeIo | `platforms/s32k1xx/port/safety/safeIo/src/safeIo/SafeIo.cpp` | C-SIO | "
                                   "`libs/bsw/e2e/doc/e2e.md` | described | — |", 1)
        self.assertTrue(any("names the unit" in p for p in self.reg_of(broken).status_problems()))
        exists = lambda p: p != "libs/bsw/e2e/doc/e2e.md" and (REPO / p).exists()   # noqa: E731
        self.assertTrue(self.reg_of(exists=exists).status_problems())
        # N2: a watchdog unit only partial, a unit of the architecture missing, a design part missing
        self.assertTrue(self.reg_of(sub(text, row_wdg, sub(row_wdg, "| described | — |", "| partial | U1 |"), 1))
                        .n2_problems())
        arch = dict(self.arch, **{"C-WDG": self.arch["C-WDG"] + ["WatchdogWindow"]})
        self.assertTrue(any("WatchdogWindow" in p for p in self.reg_of(arch=arch).n2_problems()))
        self.assertTrue(self.reg_of(sub(text, "### 4.5 Data", "### 4.5 Memory", 1)).n2_problems())
        self.assertTrue(self.reg_of(sub(text, "## 5. UD-STK", "## 5. Safety task", 1)).n2_problems())
        # consistency: a disagreement is reported
        self.assertTrue(self.reg_of(sub(text, "| `SafetySystem.cpp` | agrees (observation D2) |",
                                         "| `SafetySystem.cpp` | disagrees |", 1)).consistency_problems())
        # summary numbers
        units = int(re.search(r"\*\*(\d+)\*\* translation units: ", text).group(1))  # SYS-73: not a fixed count
        self.assertTrue(self.reg_of(sub(text, f"**{units}** translation units: ", f"**{units + 1}** translation units: ",
                                        1)).summary_problems())
        self.assertTrue(self.reg_of(sub(text, row_io, sub(row_io, "| missing | U1 |", "| partial | U1 |"), 1))
                        .summary_problems())
        # licence rule and malformed tables
        self.assertTrue(licence_problems(text + "\nsee ISO 26262-6, Table 8\n"))
        with self.assertRaises(ValueError):
            self.reg_of(sub(text, "<!-- unit-register:start -->", "", 1))


if __name__ == "__main__":
    sys.exit(unittest.main())
