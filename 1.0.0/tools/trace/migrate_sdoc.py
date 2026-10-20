# SPDX-License-Identifier: Apache-2.0
"""One-off conversion of the safety requirements and the safety catalogue to StrictDoc (DOC-21 step 3).

Kept as the record of the migration (migration plan section 13.1); it is not part of the check. It reads the sources
as they were before step 3 - doc/safety/requirements.yaml, the tables of doc/safety/concept.md (2.2 goals, 4.1-4.5
assumptions of use, 6.1-6.7 mechanisms, the item tables of 13.3-13.6) and the proposed assumptions of fmea.md
section 6 - and writes

    doc/requirements/safety/goals.sdoc         ASG  (concept 2.2)
    doc/requirements/safety/assumptions.sdoc   AOU  (concept 4.1-4.5, fmea.md 6 with status proposed)
    doc/requirements/safety/mechanisms.sdoc    SM   (concept 6.1-6.7, 13.3-13.6 as stage 2)
    doc/requirements/safety/ssr.sdoc           SSR  (requirements.yaml, sections as its comment blocks, the review
                                                     section verbatim as a text node at the end)

Text is moved verbatim (Markdown as in doc/safety, wrapped at 116 columns; whitespace is not significant for the
checks). The short titles of the goals and assumptions of use are new (TITLES below, for the user's review).

    python tools/trace/migrate_sdoc.py --source-rev <commit> [--out DIR]   convert the sources of that commit
    python tools/trace/migrate_sdoc.py --verify --source-rev <commit>      equivalence: tools/trace on the old
                                                                           sources against the new .sdoc files

Run once on 2026-10-01 against dev c935972e (records in doc/changelog.d/DOC-21.md).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tracecheck  # noqa: E402

REPO = tracecheck.REPO
OUT = "doc/requirements/safety"
WIDTH = 116
BASELINE = "Baseline: v0.2.0"

# New short titles (migration plan 13.1 point 2): written by the agent from the first clause of the statement.
TITLES = {
    "ASG-01": "Unintended torque prevented or terminated",
    "ASG-02": "Switch-off path of the power stage available",
    "ASG-03": "Battery values outside the safe operating area detected",
    "ASG-04": "No unintended cell balancing",
    "ASG-05": "No use of corrupted, stale or repeated data",
    "ASG-06": "Control software failure leads to a defined safe state",
    "ASG-07": "Only the intended image starts and runs",
    "AoU-01": "Hazard analysis, safety goals and fault tolerance times by the integrator",
    "AoU-02": "Assumed safety goals checked against the item's own",
    "AoU-03": "Target ASIL B, no claim for ASIL C and D",
    "AoU-04": "Safe states of the element acceptable for the item",
    "AoU-05": "Fault tolerance time longer than the worst-case reaction time",
    "AoU-32": "Degraded mapping safe and its groups independent",
    "AoU-35": "Integrator's safety management covers the element's open process items",
    "AoU-06": "Every output whose energy matters is declared",
    "AoU-07": "Inactive levels correct for the integrator's circuit",
    "AoU-08": "Task set lets the safety task run at its period",
    "AoU-09": "Hardware side of the safety argument by the integrator",
    "AoU-10": "Configuration files under change control",
    "AoU-11": "Safety-related signals chosen and protected by E2E",
    "AoU-12": "Application evaluates the signal qualifier",
    "AoU-30": "The three safety role pins used only in their role",
    "AoU-13": "Independent time base needs an external watchdog",
    "AoU-14": "Watchdog trigger follows one task only",
    "AoU-15": "Hardware reset counter budget survives the start-up",
    "AoU-16": "Debug interface decided for the product",
    "AoU-34": "SBC watchdog taken over by the application after the bootloader",
    "AoU-36": "MCU manufacturer's safety manual obtained and applied",
    "AoU-37": "Hardware threshold inputs away from the comparator supply at start",
    "AoU-38": "Conditions of the start-up test of the active short",
    "AoU-39": "Supply kept above the low-voltage detect threshold",
    "AoU-17": "ASIL of modules and of the integrator's software",
    "AoU-18": "Application respects its configured periods",
    "AoU-19": "Cause of a resetting event stored by the integrator",
    "AoU-20": "Non-qualified RTOS, compiler and libraries accepted or replaced",
    "AoU-21": "Device safety manuals obtained and the driver use checked",
    "AoU-22": "Static analysis of the integrator's own code and configuration",
    "AoU-23": "No stage-1 claim for ASG-03 and ASG-04, partial for ASG-01",
    "AoU-26": "Image only through the build, tool checks on the own configuration",
    "AoU-27": "Studio display is not evidence",
    "AoU-28": "Calibration and diagnostic write access controlled",
    "AoU-29": "Code generator of the application classified by the integrator",
    "AoU-24": "ADC reference tolerance fits the accuracy needed",
    "AoU-25": "Restart or re-check interval fits the latent-fault interval",
}
# concept.md 2.2: bold lead word of "Coverage at stage 1" -> COVERAGE choice (the full cell stays in COVERAGE_NOTE)
COVERAGE = {"partial": "partial", "covered with limits": "limited", "not covered": "none",
            "covered for CAN data": "limited"}
# Stage-2 device packages (concept section 13): the driver diagnostics exist, the encapsulated package does not
STAGE2_NOTE = ("Stage 2 (concept section 13): the driver's diagnostics exist; the encapsulated safety package "
               "(SYS-28) and the manufacturer's safety documentation are open (concept section 13.2).")


# ---------------------------------------------------------------------------------------------- reading the sources


def git_show(rev: str, rel: str) -> str:
    return subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    j = text.index(end, i + len(start))
    return text[i:j]


def table_rows(text: str, id_pattern: str, columns: int) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if re.match(rf"^\|\s*{id_pattern}\s*\|", line):
            row = cells(line)
            if len(row) != columns:
                raise ValueError(f"{row[0]}: {len(row)} cells, expected {columns}")
            rows.append(row)
    return rows


def first_bold(cell: str) -> tuple[str, str]:
    m = re.match(r"^\*\*(.+?)\*\*\s*(.*)$", cell)
    if not m:
        raise ValueError(f"no bold lead: {cell[:60]}")
    return m.group(1), m.group(2)


def goals(concept: str) -> list[dict]:
    out = []
    for row in table_rows(section(concept, "### 2.2 ", "\n## 3."), r"ASG-\d\d", 6):
        uid, goal, asil, stage1, coverage, later = row
        lead = first_bold(coverage)[0].rstrip(".:").strip()
        out.append({"UID": uid, "TITLE": TITLES[uid], "STATEMENT": goal, "ASIL": asil, "COVERAGE": COVERAGE[lead],
                    "COVERAGE_NOTE": coverage, "STAGE1_CONTRIBUTION": stage1 if stage1 not in ("—", "-") else "",
                    "LATER_CONTRIBUTION": later if later not in ("—", "-") else "", "STATUS": "assumed"})
    return out


def assumptions(concept: str, fmea: str) -> list[tuple[str, list[dict]]]:
    part = section(concept, "## 4. Assumptions of use", "\n## 5.")
    sections = []
    for m in re.finditer(r"^### (4\.\d) (.+)$", part, flags=re.MULTILINE):
        number, title = m.group(1), m.group(2).strip()
        body = part[m.end():]
        nxt = re.search(r"^### ", body, flags=re.MULTILINE)
        body = body[: nxt.start()] if nxt else body
        nodes = [{"UID": uid, "TITLE": TITLES[uid], "STATEMENT": text, "STATUS": "concept",
                  "SOURCE": f"concept.md section {number}"}
                 for uid, text in table_rows(body, r"AoU-\d\d", 2)]
        sections.append((f"{number} {title}", nodes))
    proposed = []
    for row in table_rows(section(fmea, "## 6. ", "\n## 7."), r"AoU-\d\d \(proposed\)", 2):
        uid = row[0].split()[0]
        proposed.append({"UID": uid, "TITLE": TITLES[uid], "STATEMENT": row[1], "STATUS": "proposed",
                         "SOURCE": "fmea.md section 6 (proposed by the failure analysis, SYS-20)"})
    sections.append(("Proposed by the failure analysis (fmea.md section 6)", proposed))
    return sections


def mechanisms(concept: str) -> list[tuple[str, list[dict]]]:
    part = section(concept, "## 6. Catalogue of safety mechanisms", "\n## 7.")
    sections = []
    for m in re.finditer(r"^### (6\.\d) (.+)$", part, flags=re.MULTILINE):
        number, title = m.group(1), m.group(2).strip()
        body = part[m.end():]
        nxt = re.search(r"^### ", body, flags=re.MULTILINE)
        body = body[: nxt.start()] if nxt else body
        header = next((cells(line) for line in body.splitlines() if line.startswith("| ID |")), None)
        if header is None:
            continue
        nodes = []
        for row in table_rows(body, r"SM-\d\d", len(header)):
            uid, mech, fault, detection, reaction = row[:5]
            name, rest = first_bold(mech)
            stage2 = "device-dependent" in "|".join(row)
            node = {"UID": uid, "TITLE": name, "STAGE": "2" if stage2 else "1", "STATUS": "implemented"}
            if not stage2 and "*not credited*" in "|".join(row):
                node["CREDITED"] = "no"
            loc = re.fullmatch(r"\((`[^`]+`(?:, `[^`]+`)*)\)", rest)
            if loc:
                node["LOCATION"] = loc.group(1)
            node.update({"FAULT_MODEL": fault, "DETECTION": detection, "REACTION": reaction,
                         "WORST_CASE": row[5] if len(row) > 5 else ""})
            if rest and not loc:
                node["NOTE"] = rest
            nodes.append(node)
        sections.append((f"{number} {title}", nodes))
    stage2 = []
    for m in re.finditer(r"^### (13\.\d) (SM-\d\d) \S+ (.+?) \((`[^`]+`)\)$", concept, flags=re.MULTILINE):
        number, uid, title, location = m.groups()
        body = concept[m.end():]
        body = body[: re.search(r"^### ", body, flags=re.MULTILINE).start()]
        table = [cells(line) for line in body.splitlines() if line.startswith("| ") and not line.startswith("|---")]
        statement = [".. list-table::", "   :header-rows: 1", ""]
        for item, content in table:
            statement += [f"   * - {item}", f"     - {content}"]
        stage2.append({"UID": uid, "TITLE": title, "STAGE": "2", "STATUS": "partial", "LOCATION": location,
                       "STATEMENT": "\n".join(statement), "NOTE": STAGE2_NOTE + f" Was concept.md section {number}."})
    sections.append(("13 Device drivers - stage 2 (concept section 13)", stage2))
    return sections


def raw_blocks(text: str) -> dict[tuple[str, str], str]:
    """(SSR id, key) -> the raw lines of a folded block scalar (text, note), dedented, line breaks kept."""
    out: dict[tuple[str, str], str] = {}
    rid, key, lines = None, None, []

    def flush():
        if rid and key and lines:
            out[(rid, key)] = textwrap.dedent("\n".join(lines)).strip("\n")

    for line in text.splitlines():
        if key and (not line.strip() or line.startswith("      ")):
            lines.append(line)
            continue
        flush()
        key, lines = None, []
        m = re.match(r"^  - id: (SSR-\d{3})\s*$", line)
        if m:
            rid = m.group(1)
            continue
        k = re.match(r"^    (text|note):\s*>-?\s*$", line)
        if k:
            key = k.group(1)
    flush()
    return out


def ssr_sections(text: str) -> list[tuple[str, list[str], list[dict]]]:
    """(section title, comment lines, requirement records) in the order of requirements.yaml."""
    data = yaml.safe_load(text)
    by_id = {r["id"]: r for r in data["requirements"]}
    raw = raw_blocks(text)
    sections: list[tuple[str, list[str], list[dict]]] = []
    body = text.split("\nrequirements:\n", 1)[1].split("\n# ====", 1)[0]
    comment: list[str] = []
    for line in body.splitlines():
        head = re.match(r"^  # -+ (.+?)\s*$", line)
        if head:
            title = head.group(1)
            sections.append((title[0].upper() + title[1:], [], []))
            comment = sections[-1][1]
            continue
        if re.match(r"^  # ", line) and sections and not sections[-1][2]:
            comment.append(line[4:])
            continue
        m = re.match(r"^  - id: (SSR-\d{3})", line)
        if m:
            rec = dict(by_id[m.group(1)])
            for key in ("text", "note"):
                if (m.group(1), key) in raw:
                    rec[key] = raw[(m.group(1), key)]
            sections[-1][2].append(rec)
    return sections


# ---------------------------------------------------------------------------------------------- writing .sdoc


def wrap(text: str) -> str:
    """Wrap every line at WIDTH columns (lines of a list-table are kept); never a line that is exactly '<<<'."""
    out = []
    for line in str(text).strip().splitlines():
        if line.lstrip().startswith(("* - ", "- ", ".. ", ":header")) or line.startswith("    ") or len(line) <= WIDTH:
            out.append(line.rstrip())
            continue
        indent = re.match(r"^\s*", line).group(0)
        out += textwrap.wrap(line.strip(), WIDTH, initial_indent=indent, subsequent_indent=indent,
                             break_long_words=False, break_on_hyphens=False)
    return "\n".join(out)


def field(name: str, value: str, multi: bool = False) -> list[str]:
    value = str(value).strip()
    if not value:
        return []
    if multi or "\n" in value:
        return [f"{name}: >>>", wrap(value), "<<<"]
    return [f"{name}: {value}"]


def document(title: str, intro: str) -> list[str]:
    return ["[DOCUMENT]", f"TITLE: {title}", "OPTIONS:", "  VIEW_STYLE: Table", "", "[GRAMMAR]",
            "IMPORT_FROM_FILE: @nextbsw", "", "[TEXT]", "STATEMENT: >>>", BASELINE, "", wrap(intro), "<<<", ""]


def node(tag: str, values: dict, order: list[str], multi: tuple[str, ...] = ()) -> list[str]:
    lines = [f"[{tag}]"]
    for key in order:
        if key in values:
            lines += field(key, values[key], key in multi)
    return lines + [""]


GOAL_FIELDS = ["UID", "TITLE", "STATEMENT", "ASIL", "COVERAGE", "COVERAGE_NOTE", "STAGE1_CONTRIBUTION",
               "LATER_CONTRIBUTION", "STATUS", "NOTE"]
AOU_FIELDS = ["UID", "TITLE", "STATEMENT", "STATUS", "SOURCE", "NOTE"]
SM_FIELDS = ["UID", "TITLE", "STAGE", "CREDITED", "STATUS", "LOCATION", "FAULT_MODEL", "DETECTION", "REACTION",
             "WORST_CASE", "STATEMENT", "NOTE"]


def write_goals(concept: str) -> str:
    out = document("Assumed safety goals (ASG)", (
        "The assumed safety goals of the Safety Element out of Context, moved from doc/safety/concept.md section 2.2 "
        "by DOC-21 step 3 (content unchanged; the short titles are new). How to read them - assumptions about the "
        "item, the strict separation of stage 1 and stage 2, and what changed against issue 1 - stays in concept.md "
        "section 2.2. COVERAGE is the lead word of the stage-1 coverage (covered, limited = covered with limits, "
        "partial, none = not covered), COVERAGE_NOTE the full statement of the concept. Field texts are Markdown "
        "as in doc/safety."))
    for g in goals(concept):
        out += node("ASG", g, GOAL_FIELDS, multi=("STATEMENT", "COVERAGE_NOTE", "STAGE1_CONTRIBUTION",
                                                  "LATER_CONTRIBUTION"))
    return "\n".join(out).rstrip() + "\n"


def write_assumptions(concept: str, fmea: str) -> str:
    out = document("Assumptions of use (AoU)", (
        "The assumptions of use of the Safety Element out of Context - the duties neXtBSW hands to the integrator - "
        "moved from doc/safety/concept.md section 4 (sections 4.1 to 4.5) and the proposed ones from "
        "doc/safety/fmea.md section 6 by DOC-21 step 3 (content unchanged; the short titles are new). STATUS concept: "
        "part of the safety concept's list (an entry that is still open for acceptance by the safety responsible "
        "says so in its text); proposed: proposed by an analysis and not yet carried into the concept. The safety "
        "manual (doc/safety/safety-manual.md) gives every entry its place and how to fulfil it. Field texts are "
        "Markdown as in doc/safety."))
    for title, nodes in assumptions(concept, fmea):
        out += ["[[SECTION]]", f"TITLE: {title}", ""]
        for a in nodes:
            out += node("AOU", a, AOU_FIELDS, multi=("STATEMENT",))
        out += ["[[/SECTION]]", ""]
    return "\n".join(out).rstrip() + "\n"


def write_mechanisms(concept: str) -> str:
    out = document("Safety mechanisms (SM)", (
        "The catalogue of safety mechanisms, moved from doc/safety/concept.md sections 6.1 to 6.7 (stage 1, and the "
        "device-dependent rows as stage 2) and the item tables of sections 13.3 to 13.6 (device drivers, stage 2) by "
        "DOC-21 step 3 (content unchanged). How to read the fields - fault model, detection, reaction (safe states "
        "S1, S2, S3, SD of concept section 3), worst case to reaction - and the notes about several mechanisms stay "
        "in concept.md section 6; the failure analysis per mechanism is doc/safety/fmea.md section 3. STAGE 2 = "
        "device-dependent, never credited by a stage-1 requirement; CREDITED no = a stage-1 mechanism that is "
        "described but claims nothing. Field texts are Markdown as in doc/safety."))
    for title, nodes in mechanisms(concept):
        out += ["[[SECTION]]", f"TITLE: {title}", ""]
        for m in nodes:
            out += node("SM", m, SM_FIELDS, multi=("FAULT_MODEL", "DETECTION", "REACTION", "WORST_CASE", "STATEMENT",
                                                    "NOTE"))
        out += ["[[/SECTION]]", ""]
    return "\n".join(out).rstrip() + "\n"


def ssr_header(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line[2:] if line.startswith("# ") else line[1:])
    return "\n".join(lines)


def clauses(text: str) -> str:
    """Lettered clauses as an RST enumerated list: the continuation lines of a clause indented by four columns (the
    width of "(a) "), so that StrictDoc's HTML export accepts the list; the words are unchanged."""
    out, in_clause = [], False
    for line in text.splitlines():
        if re.match(r"^\([a-z]\) ", line):
            in_clause = True
            out.append(line)
        else:
            out.append(("    " + line) if in_clause and line.strip() else line)
    return "\n".join(out)


def write_ssr(text: str) -> str:
    header = ssr_header(text).replace("Baseline: v0.2.0\n\n", "")
    out = document("Software safety requirements (SSR)", (
        "Moved from doc/safety/requirements.yaml by DOC-21 step 3 (content unchanged, decision Q2: this document is "
        "the only source). Relations: Parent with the role DerivedFrom = derived_from (assumed safety goal ASG-* or "
        "assumption of use AoU-*), Parent with the role Specifies = mechanisms (SM-*), a Parent without a role = the "
        "BSW requirement the SSR constrains (none yet). FINDINGS, CODE (one 'file :: symbol' per line) and NOTE as "
        "before. Check and matrix: python tools/trace check, python tools/trace matrix (doc/safety/traceability.md), "
        "CI entry safety-trace. The header of requirements.yaml follows unchanged.\n\n.. code-block:: text\n\n"
        + "\n".join(("    " + line).rstrip() for line in header.splitlines())))
    for title, comment, records in ssr_sections(text):
        out += ["[[SECTION]]", f"TITLE: {title}", ""]
        if comment:
            out += ["[TEXT]", "STATEMENT: >>>", wrap("\n".join(comment)), "<<<", ""]
        for r in records:
            out += [f"[SSR]", f"UID: {r['id']}"]
            out += field("TITLE", r["title"])
            out += field("STATEMENT", clauses(r["text"]), multi=True)
            if r.get("findings"):
                out += [f"FINDINGS: {', '.join(r['findings'])}"]
            out += [f"STAGE: {r['stage']}", f"ASIL: {r['asil']}", f"STATUS: {r['status']}",
                    f"VERIFICATION: {', '.join(r['verification'])}"]
            if r.get("code"):
                out += ["CODE: >>>"] + [f"{c['file']} :: {c['symbol']}" for c in r["code"]] + ["<<<"]
            if r.get("note"):
                out += field("NOTE", r["note"], multi=True)
            out += ["RELATIONS:"]
            for target in r.get("derived_from") or []:
                out += ["- TYPE: Parent", f"  VALUE: {target}", "  ROLE: DerivedFrom"]
            for target in r.get("mechanisms") or []:
                out += ["- TYPE: Parent", f"  VALUE: {target}", "  ROLE: Specifies"]
            out += [""]
        out += ["[[/SECTION]]", ""]
    review = "# ====" + text.split("\n# ====", 1)[1]
    out += ["[[SECTION]]", "TITLE: Review status and inspection record", "", "[TEXT]", "STATEMENT: >>>",
            "Moved verbatim from the section ``review`` of doc/safety/requirements.yaml (SYS-72). tools/trace does not "
            "read it.", "", ".. code-block:: yaml", ""]
    out += [("    " + line).rstrip() if line.strip() else "" for line in review.rstrip().splitlines()]
    out += ["<<<", "", "[[/SECTION]]", ""]
    return "\n".join(out).rstrip() + "\n"


def convert(rev: str) -> dict[str, str]:
    concept = git_show(rev, "doc/safety/concept.md")
    fmea = git_show(rev, "doc/safety/fmea.md")
    req = git_show(rev, "doc/safety/requirements.yaml")
    return {"goals.sdoc": write_goals(concept), "assumptions.sdoc": write_assumptions(concept, fmea),
            "mechanisms.sdoc": write_mechanisms(concept), "ssr.sdoc": write_ssr(req)}


# ---------------------------------------------------------------------------------------------- equivalence


def verify(rev: str) -> int:
    """tools/trace on the old sources (a copy of `rev` with requirements.yaml) against the .sdoc files of the work
    tree: same requirement records, same catalogue, same messages apart from the location."""
    with tempfile.TemporaryDirectory(prefix="nx_doc21_old_") as tmp:
        old_root = Path(tmp)
        subprocess.run(["git", "worktree", "add", "--detach", str(old_root / "wt"), rev], cwd=REPO, check=True,
                       capture_output=True)
        try:
            old = tracecheck.check(old_root / "wt", req_file=tracecheck.REQUIREMENTS, verify_matrix=False)
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(old_root / "wt")], cwd=REPO, check=True,
                           capture_output=True)
    new = tracecheck.check(REPO, sdoc=tracecheck.SDOC_ROOT, verify_matrix=False)
    problems = []
    old_reqs = {r.id: vars(r) for r in old.requirements}
    new_reqs = {r.id: vars(r) for r in new.requirements}
    for rid in sorted(set(old_reqs) | set(new_reqs)):
        a, b = old_reqs.get(rid), new_reqs.get(rid)
        if a is None or b is None:
            problems.append(f"{rid}: only in {'new' if a is None else 'old'}")
            continue
        for key in a:
            va, vb = a[key], b[key]
            if key == "code":
                va, vb = [vars(c) for c in va], [vars(c) for c in vb]
            if va != vb:
                problems.append(f"{rid}.{key}: {va!r} != {vb!r}")
    oc, nc = old.catalogue, new.catalogue
    if sorted(oc.goals) != sorted(nc.goals):
        problems.append(f"goals {sorted(oc.goals)} != {sorted(nc.goals)}")
    for gid in oc.goals:
        if " ".join(oc.goals[gid].split()) != nc.goals.get(gid):
            problems.append(f"{gid}: goal text differs")
    if oc.aous != nc.aous:
        problems.append(f"aous {oc.aous} != {nc.aous}")
    # the old table parse named a section-13 mechanism with its module path and left `credited` at its default -
    # both without effect, since the rules look at `credited` and the name of stage-1 mechanisms only
    def mech(v):
        return v.stage1, v.stage1 and v.credited, re.sub(r" \(`[^`]+`\)$", "", v.name)
    om = {k: mech(v) for k, v in oc.mechanisms.items()}
    nm = {k: mech(v) for k, v in nc.mechanisms.items()}
    if om != nm:
        problems.append(f"mechanisms differ: {sorted(set(om.items()) ^ set(nm.items()))}")
    if oc.findings != nc.findings or oc.evidence_tests != nc.evidence_tests:
        problems.append("findings or evidence tests differ")
    om_msgs = sorted((m.severity, m.kind, m.text) for m in old.messages)
    nm_msgs = sorted((m.severity, m.kind, m.text) for m in new.messages)
    if om_msgs != nm_msgs:
        problems.append(f"messages differ: old only {sorted(set(om_msgs) - set(nm_msgs))[:5]}, "
                        f"new only {sorted(set(nm_msgs) - set(om_msgs))[:5]}")
    print(f"old: {len(old.requirements)} requirements, {len(oc.goals)} goals, {len(oc.aous)} AoU, "
          f"{len(oc.mechanisms)} mechanisms, {len(old.messages)} messages")
    print(f"new: {len(new.requirements)} requirements, {len(nc.goals)} goals, {len(nc.aous)} AoU, "
          f"{len(nc.mechanisms)} mechanisms, {len(new.messages)} messages")
    for p in problems:
        print("DIFF", p)
    print("equivalence:", "identical" if not problems else f"{len(problems)} differences")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source-rev", required=True, help="git revision with requirements.yaml and the tables")
    parser.add_argument("--out", type=Path, default=REPO / OUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    if args.verify:
        return verify(args.source_rev)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in convert(args.source_rev).items():
        (args.out / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"{args.out / name}: {len(re.findall(r'^UID: ', text, flags=re.MULTILINE))} nodes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
