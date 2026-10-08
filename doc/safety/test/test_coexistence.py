"""SYS-61: consistency of the coexistence argument doc/safety/coexistence.md with the image, the copies and the tasks.

Checks
- classes: the module table uses only the classes of section 1.2; module IDs are unique;
- paths: every path of column "Paths" exists in the tree (paths below external/openbsw only when the submodule is
  checked out);
- image: every archive and object file the linker map of the demo build (`application.map`, found through
  tools/test/build_dirs.py) places in flash or RAM belongs to exactly one module, and every image part of the table is
  in the map (skipped without a demo build);
- copies: every entry of tools/openbswsync/copies.yaml belongs to a module (longest path prefix), and the counts per
  class and status of section 6 match;
- arguments: IDs unique; module is a QM-C module; kind and status from their vocabularies; every QM-C module with a
  part in the image has a row for memory, timing and information; an `open` row names a task that is not done in
  doc/todo.yaml or a finding CX-n; an `argued` row names evidence, and every test name in backticks exists in
  tools/test/tests.yaml;
- findings: IDs unique and used; every carrier is a task of doc/todo.yaml or a proposed task N-CXn;
- numbers: section 2.2 is the sum of the module rows, and section 0 repeats the counts and sums of sections 2, 3 and 6;
- licence rule for ISO 26262 (user 2026-09-22): part and clause only, no table or figure, no recommendation marks.
Counter-checks with deliberately broken copies of the document show that each rule sees its fault.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_coexistence.py
Measure (prints sizes per module and class and the copy counts, to update the document after a baseline change):
     .venv/Scripts/python doc/safety/test/test_coexistence.py --measure
"""

import collections
import importlib.util
import pathlib
import re
import sys
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[3]
DOC = REPO / "doc" / "safety" / "coexistence.md"
COPIES = REPO / "tools" / "openbswsync" / "copies.yaml"
TESTS = REPO / "tools" / "test" / "tests.yaml"
TODO = REPO / "doc" / "todo.yaml"

CLASSES = ("B-SM", "B-SEP", "B-DEP", "QM-C")
B_CLASSES = ("B-SM", "B-SEP", "B-DEP")
KINDS = ("memory", "timing", "information", "resource")
REQUIRED_KINDS = ("memory", "timing", "information")
ARG_STATUS = ("argued", "open", "AoU")
COPY_STATUS = ("identical", "modified", "new")
TASK_RE = re.compile(r"\b(?:SYS|IO|COM|TOOL|PLAT|DOC|ARCH|NVM)-\d+\b")
PROPOSED_RE = re.compile(r"\bN-CX\d+\b")
RAM_START = 0x1FFF0000                      # S32K1: SRAM_L starts here; flash, IFR and FlexNVM lie below

MODULE_COLUMNS = ("ID", "Module", "Class", "Paths", "Image parts", "Flash B", "RAM B")
SHARE_COLUMNS = ("Class", "Modules", "Flash B", "Flash %", "RAM B", "RAM %")
ARG_COLUMNS = ("ID", "Module", "Kind", "Interference (own words)", "Measure", "Status", "Evidence / task")
COPY_COLUMNS = ("Class", "identical", "modified", "new", "total")
FINDING_COLUMNS = ("ID", "Finding", "Where", "Effect", "Proposed measure", "Carrier")


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def table(text: str, marker: str, columns: tuple) -> list:
    """Rows of the table between <!-- marker:start --> and <!-- marker:end --> as dicts; ValueError if malformed."""
    m = re.search(rf"<!-- {marker}:start -->(.*?)<!-- {marker}:end -->", text, re.S)
    if not m:
        raise ValueError(f"markers {marker}:start/end missing")
    lines = [ln for ln in m.group(1).splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2 or tuple(_cells(lines[0])) != columns:
        raise ValueError(f"table {marker}: header must be {columns}")
    rows = []
    for ln in lines[2:]:
        cells = _cells(ln)
        if len(cells) != len(columns):
            raise ValueError(f"table {marker}: row with {len(cells)} cells: {ln[:60]}")
        rows.append(dict(zip(columns, cells)))
    return rows


def ticks(cell: str) -> list:
    return re.findall(r"`([^`]+)`", cell)


def number(cell: str) -> int:
    return int(cell.replace(" ", "").replace(" ", ""))


def fmt(n: int) -> str:
    """Thousands with a space, as the document writes them."""
    return f"{n:,}".replace(",", " ")


def pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}" if whole else "0.0"


def part_key(entry: str) -> tuple:
    """'libmain.a(Integration.cpp.obj)' -> ('libmain.a', 'Integration.cpp.obj'); 'libx.a' -> ('libx.a', None)."""
    m = re.fullmatch(r"(.+\.a)\((.+)\)", entry)
    return (m.group(1), m.group(2)) if m else (entry, None)


# --- demo build ----------------------------------------------------------------------------------------------------

def demo_map() -> pathlib.Path | None:
    spec = importlib.util.spec_from_file_location("build_dirs", REPO / "tools" / "test" / "build_dirs.py")
    build_dirs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_dirs)
    path = build_dirs.demo_build_dir(REPO) / "application.map"
    return path if path.is_file() else None


def map_parts(text: str) -> dict:
    """{(archive or object, object or None): [flash, ram]} of the allocated input sections of a GNU ld map."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("Linker script and memory map"))
    sizes = collections.defaultdict(lambda: [0, 0])
    out_section, pending = None, None
    for ln in lines[start:]:
        head = re.match(r"^(\.[\w.$\-]+)", ln)
        if head:
            out_section, pending = head.group(1), None
            continue
        if out_section is None or out_section.startswith((".debug", ".comment", ".ARM.attributes")):
            continue
        alone = re.match(r"^ (\.[\w.$\-]+|COMMON)\s*$", ln)
        if alone:
            pending = alone.group(1)
            continue
        m = re.match(r"^ (\.[\w.$\-]+|COMMON)?\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+(\S.*)$", ln)
        if not m or not (m.group(1) or pending):
            continue
        pending = None
        address, size, source = int(m.group(2), 16), int(m.group(3), 16), m.group(4).strip()
        if size == 0 or address == 0:
            continue
        am = re.search(r"([^/\\]+\.a)\(([^)]+)\)$", source)
        key = (am.group(1), am.group(2)) if am else (re.split(r"[/\\]", source)[-1], None)
        sizes[key][0 if address < RAM_START else 1] += size
    return dict(sizes)


# --- the document --------------------------------------------------------------------------------------------------

class Coexistence:
    def __init__(self, text: str, tasks: dict, test_names: set, copies: list, exists=None):
        self.text = text
        self.tasks = tasks
        self.test_names = test_names
        self.copies = copies                 # [(repo path, status)]
        self.exists = exists or (lambda p: (REPO / p).exists())
        self.classes = [r["Class"].strip("`") for r in table(text, "classes", ("Class", "Meaning", "Development"))]
        self.modules = table(text, "modules", MODULE_COLUMNS)
        self.shares = table(text, "shares", SHARE_COLUMNS)
        self.arguments = table(text, "arguments", ARG_COLUMNS)
        self.copy_table = table(text, "copies", COPY_COLUMNS)
        self.findings = table(text, "findings", FINDING_COLUMNS)
        self.by_id = {m["ID"]: m for m in self.modules}

    # classes and paths
    def module_problems(self) -> list:
        problems = []
        if tuple(self.classes) != CLASSES:
            problems.append(f"classes of section 1.2 are {self.classes}, expected {list(CLASSES)}")
        seen = set()
        submodule = self.exists("external/openbsw/libs")
        for m in self.modules:
            where = f"module {m['ID']}"
            if m["ID"] in seen:
                problems.append(f"{where}: ID twice")
            seen.add(m["ID"])
            if m["Class"] not in CLASSES:
                problems.append(f"{where}: class '{m['Class']}' not in {CLASSES}")
            paths = ticks(m["Paths"])
            if not paths:
                problems.append(f"{where}: no path")
            for p in paths:
                if p.startswith("external/openbsw/") and not submodule:
                    continue
                if not self.exists(p):
                    problems.append(f"{where}: path {p} does not exist")
            try:
                number(m["Flash B"]), number(m["RAM B"])
            except ValueError:
                problems.append(f"{where}: sizes are not numbers")
        return problems

    def parts(self) -> dict:
        """{image part entry: module ID}; entries named twice are reported by image_problems."""
        return {p: m["ID"] for m in self.modules for p in ticks(m["Image parts"])}

    def image_problems(self, linked: dict) -> list:
        problems = []
        entries = collections.Counter(p for m in self.modules for p in ticks(m["Image parts"]))
        problems += [f"image part {p} named in {n} modules" for p, n in entries.items() if n > 1]
        whole = {part_key(p)[0] for p in entries if part_key(p)[1] is None}
        split = {part_key(p) for p in entries if part_key(p)[1] is not None}
        for archive, obj in sorted(linked, key=str):
            if (archive, obj) in split or (obj is None and archive in whole):
                continue
            if obj is not None and archive in whole:
                if any(a == archive for a, _ in split):
                    problems.append(f"{archive}: named whole and split")
                continue
            name = f"{archive}({obj})" if obj else archive
            problems.append(f"linked part {name} belongs to no module")
        linked_archives = {a for a, _ in linked}
        for p in entries:
            archive, obj = part_key(p)
            if (obj is None and archive not in linked_archives) or (obj is not None and (archive, obj) not in linked):
                problems.append(f"image part {p} is not in the linker map")
        return problems

    def module_of_path(self, path: str) -> str | None:
        best, best_len = None, -1
        for m in self.modules:
            for p in ticks(m["Paths"]):
                if (path == p or path.startswith(p.rstrip("/") + "/")) and len(p) > best_len:
                    best, best_len = m["ID"], len(p)
        return best

    def class_of_copy(self, path: str, pattern: bool) -> str | None:
        """Class of the module owning the copy; a pattern takes the highest class of every module below it."""
        module = self.module_of_path(path)
        if module is not None:
            return self.by_id[module]["Class"]
        if not pattern:
            return None
        below = [m["Class"] for m in self.modules for p in ticks(m["Paths"])
                 if p.startswith(path.rstrip("/") + "/") and m["Class"] in CLASSES]
        return min(below, key=CLASSES.index) if below else None

    def copy_counts(self) -> tuple:
        counts = {c: collections.Counter() for c in CLASSES}
        problems = []
        for path, status, pattern in self.copies:
            cls = self.class_of_copy(path, pattern)
            if cls is None:
                problems.append(f"copy {path} belongs to no module")
                continue
            counts[cls][status] += 1
        return counts, problems

    def copy_problems(self) -> list:
        counts, problems = self.copy_counts()
        rows = {r["Class"]: r for r in self.copy_table}
        expected = dict(counts)
        expected["total"] = sum(counts.values(), collections.Counter())
        for cls, c in expected.items():
            row = rows.get(cls)
            if row is None:
                problems.append(f"section 6: no row for {cls}")
                continue
            for s in COPY_STATUS:
                if number(row[s]) != c[s]:
                    problems.append(f"section 6 {cls} {s}: {row[s]}, counted {c[s]}")
            if number(row["total"]) != sum(c[s] for s in COPY_STATUS):
                problems.append(f"section 6 {cls} total: {row['total']}, counted {sum(c.values())}")
        return problems

    # arguments and findings
    def finding_ids(self) -> set:
        return {f["ID"] for f in self.findings}

    def open_carrier(self, cell: str) -> bool:
        tasks = [t for t in TASK_RE.findall(cell) if self.tasks.get(t, "done") != "done"]
        return bool(tasks) or bool(PROPOSED_RE.search(cell)) or any(
            re.search(rf"\b{re.escape(f)}\b", cell) for f in self.finding_ids())

    def argument_problems(self) -> list:
        problems, seen = [], set()
        kinds = collections.defaultdict(set)
        for a in self.arguments:
            where = f"argument {a['ID']}"
            if a["ID"] in seen:
                problems.append(f"{where}: ID twice")
            seen.add(a["ID"])
            module = self.by_id.get(a["Module"])
            if module is None:
                problems.append(f"{where}: unknown module {a['Module']}")
            elif module["Class"] != "QM-C":
                problems.append(f"{where}: module {a['Module']} is {module['Class']}, not QM-C")
            if a["Kind"] not in KINDS:
                problems.append(f"{where}: kind '{a['Kind']}' not in {KINDS}")
            kinds[a["Module"]].add(a["Kind"])
            if a["Status"] not in ARG_STATUS:
                problems.append(f"{where}: status '{a['Status']}' not in {ARG_STATUS}")
            evidence = a["Evidence / task"]
            if a["Status"] == "open" and not self.open_carrier(evidence):
                problems.append(f"{where}: open, but no open task, proposed task or finding carries it")
            if a["Status"] == "argued" and evidence in ("", "—"):
                problems.append(f"{where}: argued without evidence")
            for name in ticks(evidence):
                if re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) and name not in self.test_names:
                    problems.append(f"{where}: test entry {name} not in tools/test/tests.yaml")
            for t in TASK_RE.findall(evidence):
                if t not in self.tasks:
                    problems.append(f"{where}: task {t} not in doc/todo.yaml")
        for m in self.modules:
            if m["Class"] == "QM-C" and ticks(m["Image parts"]):
                missing = [k for k in REQUIRED_KINDS if k not in kinds[m["ID"]]]
                if missing:
                    problems.append(f"module {m['ID']}: no argument for {', '.join(missing)}")
        return problems

    def finding_problems(self) -> list:
        problems, seen = [], set()
        rest = self.text
        for f in self.findings:
            where = f"finding {f['ID']}"
            if f["ID"] in seen:
                problems.append(f"{where}: ID twice")
            seen.add(f["ID"])
            carrier = f["Carrier"]
            named = TASK_RE.findall(carrier)
            if not named and not PROPOSED_RE.search(carrier):
                problems.append(f"{where}: no carrier task")
            problems += [f"{where}: task {t} not in doc/todo.yaml" for t in named if t not in self.tasks]
            if rest.count(f["ID"]) < 2:
                problems.append(f"{where}: not used outside its row")
        return problems

    # numbers
    def class_sums(self) -> dict:
        sums = {c: [0, 0, 0] for c in CLASSES}
        for m in self.modules:
            if m["Class"] in sums:
                s = sums[m["Class"]]
                s[0] += 1
                s[1] += number(m["Flash B"])
                s[2] += number(m["RAM B"])
        return sums

    def fully_argued(self) -> list:
        status = collections.defaultdict(list)
        for a in self.arguments:
            status[a["Module"]].append(a["Status"])
        return [m["ID"] for m in self.modules if m["Class"] == "QM-C" and ticks(m["Image parts"])
                and status[m["ID"]] and all(s != "open" for s in status[m["ID"]])]

    def summary_problems(self) -> list:
        problems = []
        sums = self.class_sums()
        flash = sum(s[1] for s in sums.values())
        ram = sum(s[2] for s in sums.values())
        rows = {r["Class"]: r for r in self.shares}
        for cls, (n, f, r) in list(sums.items()) + [("total", (len(self.modules), flash, ram))]:
            row = rows.get(cls)
            if row is None:
                problems.append(f"section 2.2: no row for {cls}")
                continue
            want = (str(n), fmt(f), pct(f, flash), fmt(r), pct(r, ram))
            got = (row["Modules"], row["Flash B"], row["Flash %"], row["RAM B"], row["RAM %"])
            if want != got:
                problems.append(f"section 2.2 {cls}: {got}, computed {want}")
        m = re.search(r"## 0\. Result in one page(.*?)\n---", self.text, re.S)
        result = m.group(1) if m else ""
        b_flash = sum(sums[c][1] for c in B_CLASSES)
        b_ram = sum(sums[c][2] for c in B_CLASSES)
        n_qm = sums["QM-C"][0]
        in_image = [x for x in self.modules if x["Class"] == "QM-C" and ticks(x["Image parts"])]
        status = collections.Counter(a["Status"] for a in self.arguments)
        counts, _ = self.copy_counts()
        b_copies = sum(sum(counts[c].values()) for c in B_CLASSES)
        b_modified = sum(counts[c]["modified"] for c in B_CLASSES)
        all_modified = sum(counts[c]["modified"] for c in CLASSES)
        argued = self.fully_argued()
        expected = [
            f"**{len(self.modules)} modules**",
            f"**{len(self.copies)}** OpenBSW copy entries",
            f"**{sum(sums[c][0] for c in B_CLASSES)} modules**: {sums['B-SM'][0]} safety mechanisms",
            f"{sums['B-SEP'][0]} separating elements",
            f"{sums['B-DEP'][0]} dependencies",
            f"**{n_qm} modules** (`QM-C`)",
            f"flash **ASIL B {fmt(b_flash)} B ({pct(b_flash, flash)} %)**, QM {fmt(sums['QM-C'][1])} B "
            f"({pct(sums['QM-C'][1], flash)} %)",
            f"RAM ASIL B {fmt(b_ram)} B ({pct(b_ram, ram)} %), QM {fmt(sums['QM-C'][2])} B ({pct(sums['QM-C'][2], ram)} %)",
            f"**{len(self.arguments)}** rows",
            f"**{status['argued']} argued**",
            f"**{status['open']} open**",
            f"**{status['AoU']} AoU**",
            f"**{len(argued)} of {len(in_image)}**",
            f"the other {len(in_image) - len(argued)} count as **ASIL B",
            f"**{b_copies}** belong to ASIL B modules",
            f"**{b_modified} of the {all_modified} modified copies**",
            f"only {all_modified - b_modified} modified copies are QM",
            f"**CX-1 … CX-{len(self.findings)}**",
        ]
        problems += [f"section 0 does not say: {e}" for e in expected if e not in result]
        return problems

    def all_problems(self, linked=None) -> list:
        return (self.module_problems() + (self.image_problems(linked) if linked is not None else [])
                + self.copy_problems() + self.argument_problems() + self.finding_problems()
                + self.summary_problems() + licence_problems(self.text))


def licence_problems(text: str) -> list:
    """References that go beyond part and clause: tables or figures, and the recommendation marks of its tables."""
    problems = [f"reference to a table or figure: '{m}'" for m in re.findall(r"\b(?:Table|Figure)\s+[A-Z]?\d+", text)]
    problems += [f"recommendation mark copied: '{line.strip()[:60]}'" for line in text.splitlines()
                 if re.search(r"(^|[\s|])(\+\+|\+|o)\s*\|\s*(\+\+|\+|o)(\s|\|)", line)]
    return problems


def load_tasks() -> dict:
    data = yaml.safe_load(TODO.read_text(encoding="utf-8"))
    return {t["id"]: t.get("status", "open") for t in data["tasks"]}


def load_test_names() -> set:
    data = yaml.safe_load(TESTS.read_text(encoding="utf-8"))
    return {t["name"] for t in data["tests"]}


def load_copies() -> list:
    data = yaml.load(COPIES.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    copies = []
    for root in data["roots"]:
        for key, entry in (root.get("files") or {}).items():
            path = f"{root['path'].strip('/')}/{key}"
            wildcard = re.search(r"[*?\[]", path)
            if wildcard:
                path = path[:wildcard.start()].rstrip("/")
            copies.append((path, entry["status"], bool(wildcard)))
    return copies


class CoexistenceConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.tasks = load_tasks()
        cls.test_names = load_test_names()
        cls.copies = load_copies()
        cls.doc = cls.of(cls.text)

    @classmethod
    def of(cls, text, tasks=None, copies=None, exists=None):
        return Coexistence(text, cls.tasks if tasks is None else tasks, cls.test_names,
                           cls.copies if copies is None else copies, exists)

    def test_modules_and_paths(self):
        self.assertEqual(self.doc.module_problems(), [])

    def test_image_inventory(self):
        path = demo_map()
        if path is None:
            self.skipTest("no demo build (application.map); run tools/test -k target-demo first")
        linked = map_parts(path.read_text(encoding="utf-8", errors="replace"))
        self.assertGreater(len(linked), 50)
        self.assertEqual(self.doc.image_problems(linked), [])

    def test_copies(self):
        self.assertGreater(len(self.copies), 100)
        self.assertEqual(self.doc.copy_problems(), [])

    def test_arguments(self):
        self.assertEqual(self.doc.argument_problems(), [])

    def test_findings(self):
        self.assertEqual(self.doc.finding_problems(), [])

    def test_summary(self):
        self.assertEqual(self.doc.summary_problems(), [])

    def test_licence(self):
        self.assertEqual(licence_problems(self.text), [])

    def test_map_parser(self):
        sample = "\n".join([
            "Linker script and memory map", "",
            ".text           0x00000410    0x1000",
            " .text.a        0x00000410       0x20 build/libsafeIo.a(SafeIo.cpp.obj)",
            " .text.long_name_of_a_function",
            "                0x00000430       0x10 build/libmain.a(Integration.cpp.obj)",
            " .text.c        0x00000440       0x08 obj/app.cpp.obj",
            ".bss            0x20000000     0x100",
            " .bss.x         0x20000000       0x40 build/libsafeIo.a(SafeIo.cpp.obj)",
            ".debug_info     0x00000000    0x9999",
            " .debug_info    0x00000123      0x500 build/libsafeIo.a(SafeIo.cpp.obj)",
        ])
        self.assertEqual(map_parts(sample), {("libsafeIo.a", "SafeIo.cpp.obj"): [0x20, 0x40],
                                             ("libmain.a", "Integration.cpp.obj"): [0x10, 0],
                                             ("app.cpp.obj", None): [0x08, 0]})

    # Counter-checks: every rule must see a broken document or a changed repository
    def test_counter_checks(self):
        def sub(source, old, new, count=1):
            """str.replace that fails when the edit does not hit, so no counter-check passes vacuously."""
            self.assertIn(old, source)
            return source.replace(old, new, count)

        text = self.text
        # classes and paths
        self.assertTrue(self.of(sub(text, "| M-SYS | Safe system (ADC reference, internal supplies) | B-SM |",
                                    "| M-SYS | Safe system (ADC reference, internal supplies) | B-X |"))
                        .module_problems())
        self.assertTrue(self.of(text, exists=lambda p: p != "libs/bsw/e2e").module_problems())
        self.assertTrue(self.of(sub(text, "| M-WDG |", "| M-SUP |")).module_problems())
        # image: an unclassified linked part, a stale entry, a part named twice
        doc = self.doc
        linked = {part_key(p) if part_key(p)[1] else (p, None): [1, 0] for p in doc.parts()}
        self.assertEqual(doc.image_problems(linked), [])
        self.assertTrue(doc.image_problems({**linked, ("libnew.a", None): [4, 0]}))
        self.assertTrue(doc.image_problems({k: v for k, v in linked.items() if k != ("libe2e.a", None)}))
        self.assertTrue(self.of(sub(text, "`libe2e.a` |", "`libe2e.a`, `libsafeSystem.a` |")).image_problems(linked))
        # copies: a copy outside every module, a wrong count
        self.assertTrue(self.of(text, copies=self.copies + [("tools/unknown/x.cpp", "modified", False)]).copy_problems())
        self.assertTrue(self.of(text, copies=self.copies + [("libs/bsw/e2e/x.cpp", "modified", False)]).copy_problems())
        # arguments: unknown module, B module, missing kind, open without task, unknown test entry, bad status
        self.assertTrue(self.of(sub(text, "| CA-17 | Q-XCP |", "| CA-17 | Q-NONE |")).argument_problems())
        self.assertTrue(self.of(sub(text, "| CA-17 | Q-XCP |", "| CA-17 | M-SUP |")).argument_problems())
        self.assertTrue(self.of(sub(text, "| CA-18 | Q-XCP | timing |", "| CA-18 | Q-XCP | resource |"))
                        .argument_problems())
        # open rows carried only by SYS-21 (CA-13, CA-26, CA-27) lose their carrier; SYS-74, the carrier used
        # before, is done since 2026-09-29 and CA-05 is carried by CX-9 now (DOC-35)
        self.assertTrue(self.of(text, tasks={**self.tasks, "SYS-21": "done"}).argument_problems())
        self.assertTrue(self.of(sub(text, "`xcp-host`, `calibration-plausibility`", "`xcp-hostx`"))
                        .argument_problems())
        self.assertTrue(self.of(sub(text, "| argued | `can-phy-gate`, SSR-042 |", "| maybe | `can-phy-gate` |"))
                        .argument_problems())
        self.assertTrue(self.of(sub(text, "| CA-02 |", "| CA-01 |")).argument_problems())
        # findings: carrier without task, unknown task
        self.assertTrue(self.of(sub(text, "| SYS-30 (exists) |", "| later |")).finding_problems())
        self.assertTrue(self.of(sub(text, "| SYS-30 (exists) |", "| SYS-9999 |")).finding_problems())
        # numbers: a module size, a count of section 0
        self.assertTrue(self.of(sub(text, "| `libsafeSystem.a` | 572 |", "| `libsafeSystem.a` | 573 |"))
                        .summary_problems())
        self.assertTrue(self.of(sub(text, "**36 modules**", "**35 modules**")).summary_problems())
        # licence rule
        self.assertEqual(licence_problems(text + "\nsee 6-7.4.8\n"), [])
        self.assertTrue(licence_problems(text + "\nsee ISO 26262-6, Table 3\n"))
        self.assertTrue(licence_problems(text + "\n| 1a | Inspection | + | ++ | ++ | ++ |\n"))
        # malformed tables
        with self.assertRaises(ValueError):
            self.of(sub(text, "<!-- arguments:start -->", ""))
        with self.assertRaises(ValueError):
            self.of(sub(text, "| ID | Module | Class | Paths |", "| ID | Name | Class | Paths |"))


def measure() -> int:
    """Print sizes per module and class from the demo build and the copy counts per class."""
    path = demo_map()
    if path is None:
        print("no demo build (application.map)")
        return 1
    linked = map_parts(path.read_text(encoding="utf-8", errors="replace"))
    doc = Coexistence(DOC.read_text(encoding="utf-8"), load_tasks(), load_test_names(), load_copies())
    for problem in doc.image_problems(linked):
        print("!", problem)
    by_archive = collections.defaultdict(lambda: [0, 0])
    for (archive, obj), (f, r) in linked.items():
        by_archive[archive][0] += f
        by_archive[archive][1] += r
    sums = {c: [0, 0, 0] for c in CLASSES}
    for m in doc.modules:
        f = r = 0
        for p in ticks(m["Image parts"]):
            archive, obj = part_key(p)
            f += linked.get((archive, obj), [0, 0])[0] if obj else by_archive.get(archive, [0, 0])[0]
            r += linked.get((archive, obj), [0, 0])[1] if obj else by_archive.get(archive, [0, 0])[1]
        sums[m["Class"]][0] += 1
        sums[m["Class"]][1] += f
        sums[m["Class"]][2] += r
        print(f"{m['ID']:7} {m['Class']:6} flash {fmt(f):>8}  ram {fmt(r):>7}")
    flash = sum(s[1] for s in sums.values())
    ram = sum(s[2] for s in sums.values())
    for c, (n, f, r) in sums.items():
        print(f"{c:6} {n:3} flash {fmt(f):>8} {pct(f, flash):>5} %  ram {fmt(r):>7} {pct(r, ram):>5} %")
    print(f"total flash {fmt(flash)}  ram {fmt(ram)}  parts {len({a for a, _ in linked})}")
    counts, problems = doc.copy_counts()
    for problem in problems:
        print("!", problem)
    for c in CLASSES:
        print(c, dict(counts[c]))
    return 0


if __name__ == "__main__":
    if "--measure" in sys.argv:
        sys.exit(measure())
    sys.exit(unittest.main())
