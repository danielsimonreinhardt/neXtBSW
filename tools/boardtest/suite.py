"""Board test suite per SSR (SYS-75): procedures of the operating cases, run at the board, evaluated on transcripts.

A suite file (suites/<project>.yaml) holds the board test cases of one project. Every case is a procedure: steps that
act on the board (console command, console capture, CAN probe, UDS request, wake frame, an external tool such as
canflash, an instruction for the person at the board) and checks on the transcript the steps leave behind. The
specification that explains the cases per software safety requirement is doc/safety/board-test-spec.md; its tables
are generated from the suite file and doc/requirements/safety/ssr.sdoc (`boardtest suite matrix`).

One evaluator for both worlds: a live run writes a transcript (console traffic in the capture format of captures/,
plus lines '#> ...' for the results of CAN steps, operator answers and external tools), and the evaluator matches the
checks of the procedure in order against that text. The host tests feed the same evaluator with board captures
recorded before (captures/), so a check that no longer matches the firmware's output turns red without a board.

Check items (in the order of the steps, matched in order through the transcript):
  expect   regex that must match after the previous match; named groups become values of the run
  absent   regex that must not match between the previous and the next matched `expect` (or the end)
  optional `expect` that may be missing (its values are recorded when it matches)
  equals / differs   {value: text} constraints on values captured so far
  delta    {from, to, value | project, tolerance}: difference of two numeric values against a fixed or a project
           value (e.g. two ROM-check log stamps against modules.safeMemory.rom_check_period_ms)
A check whose regex starts with '#>' reads a tool line; replaying a console capture skips it (it is not in there).
"""

import hashlib
import json
import re
import subprocess
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
SUITES = HERE / "suites"
CAPTURES = HERE / "captures"
SSR_SDOC = ROOT / "doc" / "requirements" / "safety" / "ssr.sdoc"
SPEC_DOC = ROOT / "doc" / "safety" / "board-test-spec.md"

# operating cases of SYS-75 (iso26262-gap.md section 6 no. 20) plus normal operation
OPERATING_CASES = {
    "START": "start-up (power-on, reset pin, software reset, flashing reset)",
    "S1": "safe state without reset (S1)",
    "S1H": "safe state held without reset (S1 held)",
    "S2": "safety reset (S1 + S2) and restart",
    "SLEEP": "sleep and wake-up",
    "FLASH": "flashing over CAN through the bootloader",
    "DIAG": "diagnostic session",
    "RUN": "normal operation (cyclic supervision, communication, load)",
}
# what a procedure needs at the board; scope, 24 V and motor are the columns the user asked for (SYS-75)
EQUIPMENT = {
    "console": "serial console of the ECU",
    "can": "CAN adapter (python-can, e.g. VN1610)",
    "jlink": "debugger (J-Link): halt, register read/write, breakpoint",
    "scope": "oscilloscope",
    "24v": "24 V supply of the power stage",
    "motor": "motor at the power stage (standstill or turned by hand)",
    "source": "adjustable source on an analogue input (lab supply, function generator)",
    "user": "a person at the board: power cycle, reset button, cable, or a command the board rules reserve to the user",
}
# which firmware a procedure runs on
IMAGES = {
    "dev": "standard development image of the tag (test console commands present)",
    "product": "product image (NX_PRODUCT_BUILD)",
    "test": "development image built with the test options named in `build`",
    "special": "an image or a board state prepared as `build` describes (e.g. a corrupted image)",
}
STEP_KINDS = ("operator", "confirm", "ask", "send", "listen", "canprobe", "can_silent", "uds", "wake", "qualifier",
              "run", "wait")

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# time stamps that other recorders put in front of a line: "06:49:21  " (session logger), "  13.719 " / "  14.016 CON "
# (coordinator scripts); a firmware log stamp "279177: RefApp" has no fraction and stays
STAMP = re.compile(r"^(?:\d\d:\d\d:\d\d  |\s*\d+\.\d{3} (?:(?:CON|JLK) )?)")


class SuiteError(Exception):
    """The suite file is not consistent (unknown kind, unknown SSR, missing reference capture ...)."""


# ------------------------------------------------------------------------------------------------ SSR document

@dataclass
class Requirement:
    uid: str                      # SSR-003 or SSR-003(a)
    title: str
    verification: tuple
    status: str = ""
    parent: str | None = None     # SSR of a clause

    @property
    def board(self) -> bool:
        return "board" in self.verification and self.status != "withdrawn"


def read_ssr(path: Path = SSR_SDOC) -> dict[str, Requirement]:
    """SSR and CLAUSE nodes of the StrictDoc file (UID, TITLE, STATUS, VERIFICATION) - a reader of its own, without
    StrictDoc, so the board suite needs nothing beyond the repository."""
    nodes: dict[str, Requirement] = {}
    kind, fields = None, {}

    def flush():
        if kind in ("SSR", "CLAUSE") and "UID" in fields:
            uid = fields["UID"]
            verification = tuple(v.strip() for v in fields.get("VERIFICATION", "").split(",") if v.strip())
            parent = uid.split("(")[0] if kind == "CLAUSE" else None
            title = fields.get("TITLE", "") if kind == "SSR" else ""
            nodes[uid] = Requirement(uid, title, verification, fields.get("STATUS", ""), parent)

    multi = False
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if multi:
            if line.strip() == "<<<":
                multi = False
            continue
        if line.startswith("[") and line.rstrip().endswith("]"):
            flush()
            kind, fields = line.strip("[] \t"), {}
            continue
        match = re.match(r"^([A-Z_]+): ?(.*)$", line)
        if match:
            key, value = match.groups()
            if value.strip() == ">>>":
                multi = True
            elif key not in fields:
                fields[key] = value.strip()
    flush()
    for node in nodes.values():
        if node.parent and node.parent in nodes:
            node.title = nodes[node.parent].title
    return nodes


def board_items(nodes: dict[str, Requirement]) -> list[str]:
    """Every clause verified on the board, and every SSR without clauses that is: what the suite must cover."""
    with_clauses = {n.parent for n in nodes.values() if n.parent}
    items = []
    for uid, node in nodes.items():
        if node.parent is None and uid in with_clauses:
            continue
        if node.board:
            items.append(uid)
    return items


def ssr_of(uid: str) -> str:
    return uid.split("(")[0]


# ------------------------------------------------------------------------------------------------ suite model

@dataclass
class Check:
    kind: str                     # expect, optional, absent, equals, differs, delta
    pattern: str = ""
    ssr: tuple = ()
    data: dict = field(default_factory=dict)
    step: str = ""

    @property
    def tool(self) -> bool:
        return self.pattern.startswith("#>")


@dataclass
class Step:
    sid: str
    kind: str
    spec: dict
    checks: list


@dataclass
class Procedure:
    pid: str
    title: str
    oc: str
    covers: tuple
    image: str
    build: str
    equipment: tuple
    steps: list
    references: list              # list of lists of capture paths (relative to captures/<project>/)
    reference_note: str
    evidence: list                # earlier board records: capture paths or "fmea B<n>" style references
    runnable: bool
    not_runnable: str
    notes: str

    @property
    def checks(self) -> list[Check]:
        return [c for step in self.steps for c in step.checks]


@dataclass
class Suite:
    path: Path
    project: str
    captures: Path
    procedures: dict
    exemptions: dict              # SSR item -> reason it has no board case on this project

    def procedure(self, pid: str) -> Procedure:
        if pid not in self.procedures:
            raise SuiteError(f"{self.path.name}: no procedure {pid} (known: {', '.join(self.procedures)})")
        return self.procedures[pid]

    def covering(self, item: str) -> list[Procedure]:
        """Procedures that cover a clause or SSR: a procedure covering SSR-003 covers every clause of it."""
        return [p for p in self.procedures.values()
                if item in p.covers or ssr_of(item) in p.covers or any(ssr_of(c) == item for c in p.covers)]


def _as_list(value) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _checks(raw, where: str, sid: str) -> list[Check]:
    checks = []
    for index, item in enumerate(_as_list(raw)):
        if not isinstance(item, dict):
            raise SuiteError(f"{where}.check[{index}]: a mapping expected")
        ssr = tuple(_as_list(item.get("ssr")))
        kinds = [k for k in ("expect", "optional", "absent", "equals", "differs", "delta") if k in item]
        if len(kinds) != 1:
            raise SuiteError(f"{where}.check[{index}]: exactly one of expect/optional/absent/equals/differs/delta")
        kind = kinds[0]
        value = item[kind]
        if kind in ("expect", "optional", "absent"):
            try:
                re.compile(value, re.MULTILINE)
            except re.error as e:
                raise SuiteError(f"{where}.check[{index}].{kind}: {e}") from None
            checks.append(Check(kind, value, ssr, {}, sid))
        else:
            if not isinstance(value, dict):
                raise SuiteError(f"{where}.check[{index}].{kind}: a mapping expected")
            checks.append(Check(kind, "", ssr, dict(value), sid))
    return checks


def load_suite(path: Path) -> Suite:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise SuiteError(f"{path}: {e}") from None
    project = str(data.get("project", ""))
    captures = CAPTURES / Path(project).name
    procedures = {}
    for pid, raw in (data.get("procedures") or {}).items():
        where = f"{path.name}: procedures.{pid}"
        if not isinstance(raw, dict):
            raise SuiteError(f"{where}: a mapping expected")
        steps = []
        for index, step in enumerate(_as_list(raw.get("steps"))):
            sid = f"s{index + 1}"
            kinds = [k for k in STEP_KINDS if k in step]
            if len(kinds) > 1 and "listen" in kinds:
                kinds.remove("listen")       # operator/run/wake with `listen`: the console is read after the action
            if not kinds:
                raise SuiteError(f"{where}.steps[{index}]: no action ({', '.join(STEP_KINDS)})")
            if len(kinds) > 1 or (step.get("listen") is not None and kinds[0] not in ("listen", "operator", "run",
                                                                                     "wake")):
                raise SuiteError(f"{where}.steps[{index}]: one action per step ({', '.join(kinds)})")
            steps.append(Step(sid, kinds[0], dict(step), _checks(step.get("check"), f"{where}.steps[{index}]", sid)))
        refs = []
        for ref in _as_list(raw.get("references")):
            refs.append([str(r) for r in _as_list(ref)])
        procedures[pid] = Procedure(
            pid, str(raw.get("title", "")), str(raw.get("oc", "")), tuple(_as_list(raw.get("covers"))),
            str(raw.get("image", "dev")), str(raw.get("build", "")), tuple(_as_list(raw.get("equipment"))), steps,
            refs, str(raw.get("reference_note", "")), [str(e) for e in _as_list(raw.get("evidence"))],
            bool(raw.get("runnable", True)), str(raw.get("not_runnable", "")), str(raw.get("notes", "")))
    exemptions = {str(k): str(v) for k, v in (data.get("no_board_case") or {}).items()}
    return Suite(path, project, captures, procedures, exemptions)


def suite_for(project: Path) -> Path:
    project = Path(project)
    name = project.name if project.is_dir() or project.suffix != ".yaml" else project.parent.name
    return SUITES / f"{name}.yaml"


def validate(suite: Suite, nodes: dict[str, Requirement] | None = None) -> list[str]:
    """Messages (empty = consistent): vocabulary, SSR ids, references and evidence on disk, every board item of the
    SSR document covered by a procedure or exempted with a reason, no exemption for a covered item."""
    nodes = read_ssr() if nodes is None else nodes
    problems = []
    for pid, proc in suite.procedures.items():
        where = f"{suite.path.name}: procedures.{pid}"
        if not re.fullmatch(r"BT-[A-Z0-9]+-\d\d", pid):
            problems.append(f"{where}: id must look like BT-<OC>-<nn>")
        if proc.oc not in OPERATING_CASES:
            problems.append(f"{where}.oc: '{proc.oc}' is not one of {', '.join(OPERATING_CASES)}")
        if proc.image not in IMAGES:
            problems.append(f"{where}.image: '{proc.image}' is not one of {', '.join(IMAGES)}")
        if proc.image in ("test", "special") and not proc.build:
            problems.append(f"{where}.build: image '{proc.image}' needs a description of the build or board state")
        for item in proc.equipment:
            if item not in EQUIPMENT:
                problems.append(f"{where}.equipment: '{item}' is not one of {', '.join(EQUIPMENT)}")
        if not proc.title:
            problems.append(f"{where}.title: missing")
        if not proc.covers:
            problems.append(f"{where}.covers: no SSR")
        for uid in proc.covers:
            if uid not in nodes:
                problems.append(f"{where}.covers: {uid} is not in {SSR_SDOC.name}")
        for check in proc.checks:
            for uid in check.ssr:
                if uid not in nodes:
                    problems.append(f"{where}: check names {uid}, not in {SSR_SDOC.name}")
                elif uid not in proc.covers and ssr_of(uid) not in proc.covers:
                    problems.append(f"{where}: check names {uid}, which the procedure does not cover")
        if not proc.runnable and not proc.not_runnable:
            problems.append(f"{where}.not_runnable: a procedure that cannot run on this project needs the reason")
        if not proc.references and not proc.reference_note:
            problems.append(f"{where}: neither references nor reference_note (why there is no recorded capture yet)")
        for ref in proc.references:
            for name in ref:
                if not (suite.captures / name).is_file():
                    problems.append(f"{where}.references: {name} not under {suite.captures.relative_to(ROOT)}")
        for item in proc.evidence:
            if not item.startswith(("fmea ", "CHANGELOG ")) and not (suite.captures / item).exists():
                problems.append(f"{where}.evidence: {item} not under {suite.captures.relative_to(ROOT)}")
        consoles = {"send", "listen", "qualifier"}
        cans = {"canprobe", "can_silent", "uds", "wake", "qualifier"}
        kinds = {s.kind for s in proc.steps}
        if kinds & consoles and "console" not in proc.equipment:
            problems.append(f"{where}.equipment: console steps without 'console'")
        if kinds & cans and "can" not in proc.equipment:
            problems.append(f"{where}.equipment: CAN steps without 'can'")
        if any(s.spec.get("can_gap") for s in proc.steps) and "can" not in proc.equipment:
            problems.append(f"{where}.equipment: can_gap without 'can'")
    for item in board_items(nodes):
        covered = suite.covering(item)
        if not covered and item not in suite.exemptions and ssr_of(item) not in suite.exemptions:
            problems.append(f"{suite.path.name}: {item} is verified on the board ({SSR_SDOC.name}) but no procedure "
                            "covers it and no_board_case gives no reason")
    for item, reason in suite.exemptions.items():
        if item not in nodes:
            problems.append(f"{suite.path.name}: no_board_case.{item} is not in {SSR_SDOC.name}")
        if not reason.strip():
            problems.append(f"{suite.path.name}: no_board_case.{item} without a reason")
    return problems


# ------------------------------------------------------------------------------------------------ evaluation

def normalise(text: str) -> str:
    """Transcript text as the checks see it: one line ending, no colour codes, no recorder time stamps."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = ANSI.sub("", text)
    return "\n".join(STAMP.sub("", line) for line in text.split("\n"))


@dataclass
class Outcome:
    check: Check
    status: str                   # passed, failed, skipped, missing (optional not found)
    detail: str = ""
    span: tuple = ()              # expect: the match; absent: the window searched (normalised text)


@dataclass
class Evaluation:
    passed: bool
    outcomes: list
    values: dict

    def failures(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == "failed"]

    def lines(self) -> list[str]:
        out = []
        for o in self.outcomes:
            what = o.check.pattern or f"{o.check.kind} {o.check.data}"
            ssr = f" [{', '.join(o.check.ssr)}]" if o.check.ssr else ""
            out.append(f"  {o.status:7} {o.check.step} {o.check.kind} {what}{ssr}" + (f": {o.detail}" if o.detail
                                                                                      else ""))
        return out


def project_value(project_file: Path, dotted: str):
    """Value of a project parameter, `modules.<module>.<name>`: the project's own value, otherwise the default of the
    module's manifest (module.yaml parameters.<name>.default) - the same two levels nxconfig resolves."""
    data = yaml.safe_load(Path(project_file).read_text(encoding="utf-8")) or {}
    node = data
    for key in dotted.split("."):
        node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            break
    if node is not None:
        return node
    parts = dotted.split(".")
    if len(parts) == 3 and parts[0] == "modules":
        for manifest in sorted(ROOT.glob(f"**/{parts[1]}/module.yaml")):
            if "external" in manifest.parts:
                continue
            spec = (yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("parameters") or {}
            if parts[2] in spec and "default" in spec[parts[2]]:
                return spec[parts[2]]["default"]
    raise SuiteError(f"{project_file}: no value for {dotted} (project or module manifest)")


def evaluate(proc: Procedure, text: str, tool_checks: bool = True, project_file: Path | None = None) -> Evaluation:
    """Matches the checks of `proc` in order against the transcript `text`. tool_checks=False (replay of a console
    capture) skips checks on '#>' lines, which only a live run writes."""
    text = normalise(text)
    values: dict = {}
    outcomes: list[Outcome] = []
    cursor = 0
    pending: list[tuple[Check, int]] = []

    def close_absent(end: int):
        for check, start in pending:
            hit = re.compile(check.pattern, re.MULTILINE).search(text, start, end)
            if hit:
                stop = text.find("\n", hit.end())
                line = text[text.rfind("\n", 0, hit.start()) + 1:stop if stop >= 0 else len(text)]
                outcomes.append(Outcome(check, "failed", f"found '{line.strip()[:120]}'", (start, end)))
            else:
                outcomes.append(Outcome(check, "passed", "", (start, end)))
        pending.clear()

    for check in proc.checks:
        if check.tool and not tool_checks:
            outcomes.append(Outcome(check, "skipped", "tool line, not in a console capture"))
            continue
        if check.kind == "absent":
            pending.append((check, cursor))
            continue
        if check.kind in ("expect", "optional"):
            hit = re.compile(check.pattern, re.MULTILINE).search(text, cursor)
            if hit is None:
                if check.kind == "optional":
                    outcomes.append(Outcome(check, "missing", "optional, not found"))
                else:
                    outcomes.append(Outcome(check, "failed", "not found after the previous match"))
                continue
            close_absent(hit.start())
            values.update({k: v for k, v in hit.groupdict().items() if v is not None})
            outcomes.append(Outcome(check, "passed", hit.group(0).strip()[:120], hit.span()))
            cursor = hit.end()
            continue
        if check.kind in ("equals", "differs"):
            bad, skipped = [], []
            for name, want in check.data.items():
                have = values.get(name)
                if have is None:
                    if any(o.status == "skipped" and f"?P<{name}>" in o.check.pattern for o in outcomes):
                        skipped.append(name)
                    else:
                        bad.append(f"{name} not captured")
                elif (str(have) == str(want)) != (check.kind == "equals"):
                    bad.append(f"{name} = {have}, {check.kind} {want}")
            status = "failed" if bad else ("skipped" if skipped else "passed")
            outcomes.append(Outcome(check, status, "; ".join(bad) or ("from a tool line" if skipped else "")))
            continue
        if check.kind == "delta":
            data = check.data
            if data.get("from") not in values or data.get("to") not in values:
                skipped = any(o.status == "skipped" for o in outcomes)
                outcomes.append(Outcome(check, "skipped" if skipped else "failed", "values not captured"))
                continue
            got = float(values[data["to"]]) - float(values[data["from"]])
            if "project" in data:
                if project_file is None:
                    raise SuiteError(f"{proc.pid}: delta against {data['project']} needs the project")
                want = float(project_value(project_file, data["project"]))
            else:
                want = float(data["value"])
            tol = float(data.get("tolerance", 0))
            name = data.get("name", f"{data['to']}-{data['from']}")
            values[name] = f"{got:g}"
            ok = abs(got - want) <= tol
            outcomes.append(Outcome(check, "passed" if ok else "failed", f"{name} = {got:g}, expected {want:g} +- {tol:g}"))
            continue
        raise SuiteError(f"{proc.pid}: unknown check kind {check.kind}")
    close_absent(len(text))
    passed = not any(o.status == "failed" for o in outcomes)
    return Evaluation(passed, outcomes, values)


def reference_text(suite: Suite, ref: list[str]) -> str:
    return "\n".join((suite.captures / name).read_text(encoding="utf-8", errors="replace") for name in ref)


def replay(suite: Suite, proc: Procedure, project_file: Path | None = None) -> list[tuple[list[str], Evaluation]]:
    """Every reference capture set of the procedure through the evaluator (tool lines skipped)."""
    return [(ref, evaluate(proc, reference_text(suite, ref), tool_checks=False, project_file=project_file))
            for ref in proc.references]


# ------------------------------------------------------------------------------------------------ matrix (doc)

def _flags(procs: list[Procedure]) -> dict[str, str]:
    eq = {item for p in procs for item in p.equipment}
    return {k: ("yes" if k in eq else "-") for k in ("scope", "24v", "motor", "jlink", "user")}


def matrix(suite: Suite, nodes: dict[str, Requirement] | None = None) -> str:
    """The two tables of doc/safety/board-test-spec.md section 5 and 6, generated (the test compares them)."""
    nodes = read_ssr() if nodes is None else nodes
    out = ["| SSR | Title | Board items (`ssr.sdoc`) | Board cases | Operating cases | Scope | 24 V | Motor | J-Link "
           "| User |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    ssrs = [uid for uid, n in nodes.items() if n.parent is None]
    items = board_items(nodes)
    for uid in ssrs:
        mine = [i for i in items if ssr_of(i) == uid]
        procs = []
        for item in mine:
            for p in suite.covering(item):
                if p not in procs:
                    procs.append(p)
        for p in suite.procedures.values():          # a case that covers the SSR beyond its board clauses
            if (uid in p.covers or any(ssr_of(c) == uid for c in p.covers)) and p not in procs:
                procs.append(p)
        if not mine and not procs:
            continue
        exempt = [i for i in mine if not suite.covering(i)]
        cases = ", ".join(p.pid + ("" if p.runnable else " (not on this project)") for p in procs) or "-"
        if exempt:
            cases += "; no board case: " + ", ".join(exempt)
        ocs = ", ".join(sorted({p.oc for p in procs})) or "-"
        flags = _flags(procs)
        board = ", ".join(i[len(uid):] or "whole" for i in mine) or "- (beyond the method)"
        out.append(f"| {uid} | {nodes[uid].title} | {board} | {cases} | {ocs} | {flags['scope']} | {flags['24v']} | "
                   f"{flags['motor']} | {flags['jlink']} | {flags['user']} |")
    out += ["", "| Case | Title | OC | Covers | Image | Equipment | Reference capture | Earlier records |",
            "|---|---|---|---|---|---|---|---|"]
    for p in suite.procedures.values():
        refs = "; ".join(" + ".join(r) for r in p.references) or "none yet"
        image = p.image + ("" if p.runnable else ", not on this project")
        out.append(f"| {p.pid} | {p.title} | {p.oc} | {', '.join(p.covers)} | {image} | {', '.join(p.equipment)} | "
                   f"{refs} | {'; '.join(p.evidence) or '-'} |")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------------------------ live run

class Operator:
    """The person at the board: reads an instruction, presses Enter, answers a question. The host tests replace it."""

    def __init__(self, inp=input, out=print):
        self.inp, self.out = inp, out

    def instruct(self, text: str) -> None:
        self.out(f"\n>>> {text}")
        self.inp("    press Enter when done (or to start) ")

    def confirm(self, question: str) -> bool:
        while True:
            answer = self.inp(f"\n??? {question} [y/n] ").strip().lower()
            if answer in ("y", "yes", "j", "ja"):
                return True
            if answer in ("n", "no", "nein"):
                return False

    def ask(self, question: str) -> str:
        return self.inp(f"\n??? {question}: ").strip()


@dataclass
class ProcedureRun:
    pid: str
    transcript: Path
    evaluation: Evaluation | None
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.evaluation is not None and self.evaluation.passed and not self.error


class Runner:
    """Executes the steps of a procedure and records the transcript, then evaluates it. Transports come from the
    caller (cli.py opens the serial port and the CAN adapter; the host tests hand in simulations)."""

    def __init__(self, suite: Suite, setup, open_console, open_bus, operator: Operator, out_dir: Path,
                 clock=time.monotonic, sleep=time.sleep, run_command=None, image: Path | None = None):
        self.suite, self.setup = suite, setup
        self.open_console, self.open_bus = open_console, open_bus
        self.operator, self.out_dir = operator, Path(out_dir)
        self.clock, self.sleep = clock, sleep
        self.run_command = run_command or _run_command
        self.image = image

    # each procedure gets its own transcript; the console is opened per procedure, the CAN bus per step
    def run(self, proc: Procedure) -> ProcedureRun:
        import console as con

        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{proc.pid}.console.txt"
        recorder = None
        try:
            recorder = con.Recorder(self.open_console(), path)
            recorder.note(f"# {proc.pid}: {proc.title}")
            recorder.note(f"# covers {', '.join(proc.covers)}; image {proc.image}"
                          + (f" ({proc.build})" if proc.build else ""))
            console = con.Console(recorder, clock=self.clock, sleep=self.sleep)
            for step in proc.steps:
                self._step(proc, step, recorder, console)
        except KeyboardInterrupt:
            if recorder is not None:
                recorder.note("#> aborted by the operator")
            return ProcedureRun(proc.pid, path, None, "aborted by the operator")
        except Exception as e:  # noqa: BLE001 - a broken step is reported with the procedure, the suite goes on
            if recorder is not None:
                recorder.note(f"#> error {type(e).__name__}: {e}")
            return ProcedureRun(proc.pid, path, None, f"{type(e).__name__}: {e}")
        finally:
            if recorder is not None:
                recorder.close()
        evaluation = evaluate(proc, path.read_text(encoding="utf-8"), project_file=self.setup.project)
        return ProcedureRun(proc.pid, path, evaluation)

    def _step(self, proc: Procedure, step: Step, rec, console) -> None:
        spec = step.spec
        rec.note(f"# step {step.sid}: {step.kind}")
        if step.kind == "operator":
            rec.note(f"# operator: {spec['operator']}")
            self.operator.instruct(spec["operator"])
            if "listen" in spec:
                self._listen(rec, console, spec, float(spec["listen"]))
        elif step.kind == "confirm":
            ok = self.operator.confirm(spec["confirm"])
            rec.note(f"#> confirm {step.sid}: {'yes' if ok else 'no'} - {spec['confirm']}")
        elif step.kind == "ask":
            answer = self.operator.ask(spec["ask"])
            rec.note(f"#> answer {step.sid}: {answer}")
        elif step.kind == "send":
            console.command(str(spec["send"]), float(spec.get("timeout", 1.5)))
        elif step.kind == "listen":
            self._listen(rec, console, spec, float(spec["listen"]))
        elif step.kind == "canprobe":
            import checks

            bus = self.open_bus()
            try:
                result = checks.can_probe(bus, self.setup, float(spec["canprobe"]), clock=self.clock)
            finally:
                bus.shutdown()
            for line in result.lines:
                rec.note(f"#> {line}")
            rec.note(f"#> canprobe: {'passed' if result.passed else 'FAILED'}")
        elif step.kind == "can_silent":
            seconds = float(spec["can_silent"])
            ids = {m.frame_id for m in self.setup.tx.values()}
            bus = self.open_bus()
            try:
                count, end = 0, self.clock() + seconds
                while self.clock() < end:
                    msg = bus.recv(0.05)
                    if msg is not None and not msg.is_error_frame and msg.arbitration_id in ids:
                        count += 1
            finally:
                bus.shutdown()
            rec.note(f"#> can: {count} frame(s) from the ECU in {seconds:g} s")
        elif step.kind == "uds":
            bus = self.open_bus()
            try:
                for request in _as_list(spec["uds"]):
                    answer = uds_request(bus, self.setup.diag, bytes.fromhex(str(request)), self.clock)
                    rec.note(f"#> uds {_hex(bytes.fromhex(str(request)))} -> "
                             + (_hex(answer) if answer is not None else "none"))
            finally:
                bus.shutdown()
        elif step.kind == "wake":
            import can
            import checks

            wake = spec["wake"] or {}
            data = bytes.fromhex(str(wake.get("data", "")))
            if wake.get("id", "diag") == "diag":
                frame_id = self.setup.diag.request_id
                data = data or bytes([0x02, 0x3E, 0x00]) + bytes([self.setup.diag.padding] * 5)
            else:
                frame_id = int(str(wake["id"]), 0)
            frame = can.Message(arbitration_id=frame_id, data=data, is_extended_id=False)
            result = checks.send_wake_frames(self.open_bus, frame, int(wake.get("count", 3)),
                                             float(wake.get("gap", 0.2)), self.sleep)
            rec.note(f"#> wake: {result.values.get('attempts', 0)} frame(s) 0x{frame_id:X} sent")
            if "listen" in spec:
                self._listen(rec, console, spec, float(spec["listen"]))
        elif step.kind == "qualifier":
            import checks

            q = spec["qualifier"] or {}
            bus = self.open_bus()
            try:
                result = checks.qualifier_phases(console, bus, self.setup, q.get("message"), None, q.get("observe"),
                                                 bool(q.get("expect_gated", False)), float(q.get("window", 4.0)))
            finally:
                bus.shutdown()
            for line in result.lines:
                rec.note(f"#> {line}")
            rec.note(f"#> qualifier: {'passed' if result.passed else 'FAILED'}")
        elif step.kind == "run":
            argv = [_fill(a, self) for a in _as_list(spec["run"])]
            rec.note(f"# run: {' '.join(argv)}")
            self.operator.instruct("run: " + " ".join(argv))
            code, output = self.run_command(argv)
            for line in output.splitlines():
                rec.note(f"#| {line}")
            rec.note(f"#> run exit {code}")
            if "listen" in spec:
                self._listen(rec, console, spec, float(spec["listen"]))
        elif step.kind == "wait":
            rec.note(f"# wait {spec['wait']} s")
            self.sleep(float(spec["wait"]))
        else:
            raise SuiteError(f"{proc.pid}.{step.sid}: unknown step {step.kind}")

    def _listen(self, rec, console, spec: dict, seconds: float) -> None:
        command = str(spec.get("command", ""))
        if not spec.get("can_gap"):
            console.capture(seconds, command)
            return
        ids = {m.frame_id for m in self.setup.tx.values()}
        bus = self.open_bus()
        stamps: list[float] = []
        stop = threading.Event()

        def collect():
            drain_end = None
            while True:
                if stop.is_set() and drain_end is None:
                    drain_end = time.monotonic() + 0.2       # frames already queued at the end still count
                msg = bus.recv(0.02)
                if msg is None and drain_end is not None:
                    break
                if drain_end is not None and time.monotonic() > drain_end:
                    break
                if msg is not None and not msg.is_error_frame and msg.arbitration_id in ids:
                    stamps.append(msg.timestamp or self.clock())

        thread = threading.Thread(target=collect, daemon=True)
        thread.start()
        try:
            console.capture(seconds, command)
        finally:
            stop.set()
            thread.join(2.0)
            bus.shutdown()
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        gap = max(gaps) if gaps else 0.0
        rec.note(f"#> can gap {gap * 1000:.0f} ms, {len(stamps)} ECU frame(s) in {seconds:g} s")


def _hex(data: bytes) -> str:
    return data.hex(" ")


def _fill(arg, runner: Runner) -> str:
    return (str(arg).replace("{python}", sys.executable).replace("{root}", str(ROOT))
            .replace("{project}", str(runner.setup.project))
            .replace("{image}", str(runner.image) if runner.image else "<--image not given>"))


def _run_command(argv: list[str]) -> tuple[int, str]:
    done = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def uds_request(bus, diag, request: bytes, clock=time.monotonic, timeout_s: float = 1.0, pending_s: float = 5.0):
    """One UDS request over ISO-TP on the project's diagnostic addresses (single frame request, single or
    segmented answer with a flow control); 7F xx 78 (response pending) extends the wait. None without an answer."""
    import can

    if diag is None:
        raise SuiteError("the project has no diagnostic addresses (project diagnostics)")
    if len(request) > 7:
        raise SuiteError("uds: requests longer than 7 bytes need segmentation, not supported by the suite")
    pad = diag.padding
    frame = bytes([len(request)]) + request
    bus.send(can.Message(arbitration_id=diag.request_id, data=frame + bytes([pad] * (8 - len(frame))),
                         is_extended_id=False))
    end = clock() + timeout_s
    while clock() < end:
        msg = bus.recv(0.05)
        if msg is None or msg.arbitration_id != diag.response_id:
            continue
        data = bytes(msg.data)
        pci = data[0] >> 4
        if pci == 0:
            payload = data[1:1 + (data[0] & 0x0F)]
            if len(payload) == 3 and payload[0] == 0x7F and payload[2] == 0x78:
                end = clock() + pending_s
                continue
            return payload
        if pci == 1:
            total = ((data[0] & 0x0F) << 8) | data[1]
            payload = bytearray(data[2:])
            bus.send(can.Message(arbitration_id=diag.request_id, data=bytes([0x30, 0, 0]) + bytes([pad] * 5),
                                 is_extended_id=False))
            end = clock() + timeout_s
            while len(payload) < total and clock() < end:
                cf = bus.recv(0.05)
                if cf is not None and cf.arbitration_id == diag.response_id and (cf.data[0] >> 4) == 2:
                    payload += bytes(cf.data[1:])
            return bytes(payload[:total]) if len(payload) >= total else None
    return None


# ------------------------------------------------------------------------------------------------ run record

def image_identity(image: Path | None) -> dict:
    if image is None:
        return {}
    data = Path(image).read_bytes()
    return {"path": str(image), "sha256": hashlib.sha256(data).hexdigest(), "crc32_of_file": f"0x{zlib.crc32(data):08X}"}


def git_describe() -> str:
    try:
        done = subprocess.run(["git", "describe", "--always", "--dirty", "--tags"], cwd=ROOT, capture_output=True,
                              text=True, timeout=20)
        return done.stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


def write_record(out_dir: Path, suite: Suite, runs: list[ProcedureRun], baseline: str, image: Path | None,
                 tester: str, started: str) -> Path:
    """suite-run.json and summary.md next to the transcripts: what ran on which image, the result per procedure and
    the SSRs it covers - the board record that the verification report of a baseline links."""
    out_dir = Path(out_dir)
    results = []
    for run in runs:
        proc = suite.procedure(run.pid)
        ev = run.evaluation
        results.append({
            "procedure": run.pid, "title": proc.title, "oc": proc.oc, "covers": list(proc.covers),
            "result": "passed" if run.passed else ("error" if run.error else "failed"),
            "error": run.error, "values": ev.values if ev else {},
            "failed_checks": [f"{o.check.step} {o.check.kind} {o.check.pattern or o.check.data}: {o.detail}"
                              for o in (ev.failures() if ev else [])],
            "transcript": run.transcript.name})
    record = {"suite": str(suite.path.relative_to(ROOT)).replace("\\", "/"), "project": suite.project,
              "started": started, "finished": datetime.now().isoformat(timespec="seconds"),
              "baseline": baseline, "repository": git_describe(), "image": image_identity(image), "tester": tester,
              "results": results}
    (out_dir / "suite-run.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    lines = [f"# Board test suite run {started}", "",
             f"Project `{suite.project}`, baseline `{baseline or '-'}`, repository `{record['repository']}`, "
             f"tester {tester or '-'}.", ""]
    if record["image"]:
        lines += [f"Image `{record['image']['path']}`, SHA-256 `{record['image']['sha256']}`.", ""]
    lines += ["| Case | Result | Covers | Values |", "|---|---|---|---|"]
    for r in results:
        values = ", ".join(f"{k}={v}" for k, v in r["values"].items())
        lines.append(f"| {r['procedure']} | {r['result']} | {', '.join(r['covers'])} | {values} |")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_dir / "suite-run.json"
