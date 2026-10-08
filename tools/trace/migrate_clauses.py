# SPDX-License-Identifier: Apache-2.0
"""One-off conversion of the lettered clauses of the SSR into StrictDoc nodes of their own (DOC-21 step 3b, DOC-43).

Kept as the record of the conversion (migration plan section 19); it is not part of the check. It reads
doc/requirements/safety/ssr.sdoc as it was before step 3b and writes it back with

  - one CLAUSE node per lettered clause `(a) ...` of an SSR, UID `SSR-nnn(a)`, its text verbatim (the label removed,
    the indentation of the continuation lines removed), a plain Parent relation to its SSR; the SSR keeps no
    STATEMENT (tools/trace and the safety model rebuild the text from the clauses);
  - per clause its VERIFICATION (inherited from the SSR, narrowed only where the SSR's note says so, VERIFY below)
    and its TESTS: the entries of tools/test/tests.yaml that name the SSR and whose subject is that clause
    (ALLOCATION below - read from the test comments and, where unclear, from the test sources; a test of the SSR
    that matches no clause is left unallocated and reported by tools/trace as `test-not-allocated`);
  - REVIEW_STATUS per SSR (and per clause where it differs) from the map `requirements:` of the review record, whose
    `status` keys are removed (the field is the only place now);
  - the findings R3, R4 and R5 of the inspection INS-SSR-01 (anomalies AN-048, AN-049, AN-050), CHANGES below.

    python tools/trace/migrate_clauses.py --source-rev <commit>            convert ssr.sdoc of that commit
    python tools/trace/migrate_clauses.py --verify --source-rev <commit>   compare the old SSR with the new model

Run once on 2026-10-02 against integ/night11 baa1d837 (records in doc/changelog.d/DOC-43.md).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tracecheck  # noqa: E402

REPO = tracecheck.REPO
SSR_FILE = "doc/requirements/safety/ssr.sdoc"

# Test entries of tools/test/tests.yaml per clause (only entries that name the SSR; the check refuses others).
ALLOCATION: dict[str, dict[str, list[str]]] = {
    "SSR-001": {"a": ["kernel-fault-reaction", "kernel-fault-reaction-mutations", "no-heap-operator-delete"],
                "b": ["safe-supervisor", "safe-supervisor-bare", "safe-supervisor-degraded", "safety-reset-console",
                      "safety-reset-console-mutations", "safe-console"],
                "c": []},
    "SSR-003": {"a": ["safe-supervisor", "safe-supervisor-bare"], "b": ["safe-supervisor"],
                "c": ["safe-supervisor", "safe-state-hold"], "d": ["safe-supervisor"], "e": ["safe-console"],
                "f": ["safe-supervisor", "safe-supervisor-bare"]},
    "SSR-004": {"a": ["safe-state"], "b": ["safe-state-hold"]},
    "SSR-005": {"a": ["bswgen"], "b": ["safe-outputs-order", "safe-state"], "c": ["safe-outputs-order"],
                "d": ["safe-state"], "e": ["safe-state", "safe-state-hold"], "f": []},
    "SSR-006": {"a": [], "b": ["safe-state"]},
    "SSR-010": {"a": ["nxconfig-watchdog"], "b": [], "c": ["nxconfig-watchdog"], "d": []},
    "SSR-011": {c: ["nxconfig-watchdog"] for c in "abcde"},
    "SSR-014": {c: ["safety-cycle"] for c in "abc"},
    "SSR-015": {"a": ["rte-deadline"], "b": ["rte-deadline"], "c": ["rte-deadline"], "d": ["rte-deadline"],
                "e": ["safety-context", "safety-context-elf", "safe-supervisor-mpu"], "f": []},
    "SSR-016": {"a": ["lpit-halt-deadman"], "b": ["safe-outputs-order"], "c": ["debug-halt-guard", "safe-off-equivalence"]},
    "SSR-017": {"a": [], "b": ["safe-supervisor"]},
    "SSR-018": {"a": ["lptmr-cycle-timer", "cycle-period-check", "safety-cycle"],
                "b": ["cycle-period-check", "cycle-period-constants", "safety-cycle"], "c": ["cycle-period-check"],
                "d": ["cycle-timer-off", "lptmr-cycle-timer"], "e": ["cycle-period-check", "cycle-timer-off"]},
    "SSR-020": {"a": ["safe-supervisor-mpu", "safety-context-elf", "bswgen-mpu"], "b": [], "c": [],
                "d": ["safety-context", "safety-context-elf", "safe-supervisor-mpu"]},
    "SSR-021": {c: ["safety-gate", "safety-gate-mutations"] for c in "ab"},
    "SSR-024": {"a": ["rom-check-image", "application-linker", "target-calibration"],
                "b": ["rom-check-image", "application-linker"], "c": ["rom-check"],
                "d": ["rom-check", "calibration-segment"], "e": ["rom-check", "calibration-segment"]},
    "SSR-025": {"a": [], "b": [], "c": [], "d": ["rom-check"]},
    "SSR-028": {"a": ["safe-outputs-order", "freertos-config-elf"], "b": ["safe-outputs-order"]},
    "SSR-033": {"a": ["safe-io", "bswgen-safe-io"], "b": ["safe-io", "safe-io-mutations"]},
    "SSR-034": {"a": ["safe-io", "bswgen-safe-io", "safe-io-mutations"], "b": ["safe-state-hold", "safe-supervisor"]},
    "SSR-040": {"a": ["rtegen", "bswgen"], "b": ["rtegen"], "c": ["rtegen"], "d": ["rtegen"]},
    "SSR-041": {"a": ["e2e-ref", "e2e-host", "e2e-mutations", "e2e", "rtegen"],
                "b": ["e2e-ref", "e2e-host", "e2e-mutations", "e2e", "rtegen"], "c": ["rtegen", "can-bus-state"]},
    "SSR-042": {"a": ["can-bus-state"], "b": ["can-phy-gate"]},
    "SSR-050": {"a": ["ecustate", "bsw-providers-sbc", "bsw-providers-nosbc", "bsw-providers-nosbc-nomcu",
                      "rcm-start-line"], "b": [], "c": ["ecustate", "bsw-providers-sbc"]},
    "SSR-051": {"a": ["bootloader-host", "bl-update-host", "bl-update-mutations"], "b": ["bootloader-host", "bl-s3-host"]},
    "SSR-052": {c: ["ecustate"] for c in "abc"},
    "SSR-060": {"a": ["bswgen-safe-readback", "safe-io"], "b": ["safe-io"]},
    # open requirements (not implemented, no test expected yet)
    "SSR-061": {c: [] for c in "ab"}, "SSR-062": {c: [] for c in "ab"}, "SSR-063": {c: [] for c in "abcd"},
    "SSR-082": {c: [] for c in "ab"},
    "SSR-064": {"a": ["safe-supervisor"], "b": ["safe-supervisor"], "c": [], "d": ["safe-supervisor"],
                "e": ["safe-supervisor"], "f": []},
    "SSR-065": {c: ["safe-supervisor"] for c in "abc"},
    "SSR-066": {"a": ["safe-watchdog"], "b": ["lpit-deadman-check", "safety-cycle"]},
    "SSR-067": {"a": ["rom-check"], "b": ["rom-check", "rom-check-mutations"],
                "c": ["rom-check", "rom-check-mutations", "nvm-flash-write-bracket"]},
    "SSR-068": {"a": ["safe-memory-ecc"], "b": ["safe-memory-ecc"], "c": []},
    "SSR-081": {"a": ["bswgen-reference", "rtegen-reference"],
                "b": ["elfcheck-timing", "elfcheck-timing-host", "elfcheck-timing-demo", "elfcheck-timing-bt148"]},
    "SSR-086": {"a": ["safe-memory-ecc-latent", "safe-memory-ecc-latent-s32k11x", "safe-memory-ecc-latent-mutations",
                      "safe-memory-ecc-latent-elf"], "b": ["safe-memory-ecc-latent", "safe-memory-ecc-latent-mutations"]},
    "SSR-087": {"a": ["safe-supervisor-degraded", "safe-supervisor-degraded-mutations", "safe-state-degraded"],
                **{c: ["safe-supervisor-degraded", "safe-supervisor-degraded-mutations"] for c in "bcdef"},
                "g": ["safe-supervisor-degraded", "rtegen-safety"]},
    "SSR-088": {"a": ["nxconfig-degraded"], "b": ["nxconfig-degraded"], "c": ["safe-supervisor-degraded-mutations"]},
    "SSR-089": {"a": ["safe-state-select", "bswgen-safe-state"], "b": ["safe-state-select"], "c": ["safe-state-select"],
                "d": ["safe-state-select", "bswgen-safe-state"], "e": ["safe-state-select"], "f": ["safe-state-select"],
                "g": ["safe-state-select"], "h": ["bswgen-safe-state", "safe-state-select"]},
    "SSR-090": {c: ["nxconfig-safestate"] for c in "abc"},
    "SSR-091": {"a": ["safe-supervisor-bridge-fault", "safe-supervisor-bridge-fault-mutations"],
                "b": ["safe-supervisor-bridge-fault", "safe-supervisor-bridge-fault-mutations"],
                "c": ["safe-supervisor-bridge-fault", "safe-supervisor-bridge-fault-mutations"],
                "d": ["nxconfig-safestate"], "e": []},
    "SSR-092": {"a": ["short-hold", "short-hold-mutations"], "b": ["short-hold", "short-hold-mutations"],
                "c": ["short-hold"], "d": [], "e": []},
}

# Verification per clause where the SSR's note narrows it (otherwise the clause inherits the SSR's methods).
VERIFY = {
    "SSR-001(a)": "test", "SSR-001(b)": "test", "SSR-001(c)": "review",   # note: "Clause (c) is verified by review"
    "SSR-017(a)": "board",   # note: "The test covers the reaction ... (board proof of the reaction only)"
    "SSR-025(a)": "review", "SSR-025(b)": "review", "SSR-025(c)": "review",  # note: "calling context ... review only"
    "SSR-025(d)": "test",
}

# Findings of INS-SSR-01 carried out here (they change text, title and test links: safety responsible's acceptance).
WITHDRAWN = {"SSR-010(d)": "Withdrawn by DOC-43 (finding R3 of INS-SSR-01, AN-048): it repeated SSR-028(a) for the "
                            "watchdog warning; the test link safe-outputs-order belongs to SSR-028."}
TEXTS = {"SSR-024(d)": ("At run time the ROM check shall compute the CRC over the listed areas once per start and "
                        "then with the re-check period of SSR-067, and check the area with its own reference in "
                        "the same pass through its owner.",
                        "At run time the ROM check shall compute the CRC over the listed areas once per start and "
                        "check the area with its own reference in the same pass through its owner; the repetition "
                        "during operation is SSR-067.")}
TITLES = {"SSR-007": ("One power-stage adapter per safe_off output, enforced by the build",
                      "One power-stage adapter per switched-off output, enforced by the build")}
# review status of clauses that differ from their SSR
CLAUSE_REVIEW = {"SSR-024(d)": "proposed"}       # new wording of R4, not inspected yet
REVIEW_AFTER = {"SSR-007": "inspected", "SSR-010": "inspected", "SSR-024": "inspected"}   # R5, R3, R4 closed here

LABEL = re.compile(r"^\(([a-z])\) (.*)$")


def git_show(rev: str, rel: str) -> str:
    return subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def split_clauses(statement: str) -> list[tuple[str, str]]:
    """'(a) x\\n    y\\n(b) z' -> [('a', 'x\\ny'), ('b', 'z')]; [] for a statement without labels."""
    out: list[tuple[str, list[str]]] = []
    for line in statement.splitlines():
        match = LABEL.match(line)
        if match:
            out.append((match.group(1), [match.group(2)]))
        elif out and line.startswith("    "):
            out[-1][1].append(line[4:])
        elif out:
            raise ValueError(f"unexpected line in a clause list: {line!r}")
    letters = [c for c, _ in out]
    if letters and letters != [chr(ord("a") + i) for i in range(len(letters))]:
        raise ValueError(f"clause letters not consecutive: {letters}")
    return [(c, "\n".join(lines)) for c, lines in out]


def review_map(text: str) -> dict:
    block = text[text.index("TITLE: Review status and inspection record"):]
    code = block[block.index(".. code-block:: yaml") + len(".. code-block:: yaml"):block.index("\n<<<")]
    data = yaml.safe_load(textwrap.dedent(code))
    return data["review"]["requirements"]


def value_block(name: str, text: str) -> list[str]:
    if "\n" in text or len(text) > 100:
        return [f"{name}: >>>", *text.splitlines(), "<<<"]
    return [f"{name}: {text}"]


def clause_node(rid: str, letter: str, text: str, verification: str, tests: list[str], status: str | None,
                review: str | None, note: str | None) -> list[str]:
    cid = f"{rid}({letter})"
    lines = ["[CLAUSE]", f"UID: {cid}", *value_block("STATEMENT", text)]
    if status:
        lines.append(f"STATUS: {status}")
    lines.append(f"VERIFICATION: {verification}")
    if tests:
        lines.append(f"TESTS: {', '.join(tests)}")
    if review:
        lines.append(f"REVIEW_STATUS: {review}")
    if note:
        lines += value_block("NOTE", textwrap.fill(note, 116))
    lines += ["RELATIONS:", "- TYPE: Parent", f"  VALUE: {rid}"]
    return lines


def convert_block(block: str, reviews: dict) -> str:
    """One `[SSR]` block (without the trailing blank line) -> the block with REVIEW_STATUS and its clause nodes."""
    rid = re.search(r"^UID: (\S+)$", block, re.M).group(1)
    review = REVIEW_AFTER.get(rid) or reviews[rid]["status"]
    clause_reviews = {k: v for k, v in (reviews[rid].get("clauses") or {}).items()}
    verification = re.search(r"^VERIFICATION: (.*)$", block, re.M).group(1)
    if rid in TITLES:
        old, new = TITLES[rid]
        assert f"TITLE: {old}\n" in block, rid
        block = block.replace(f"TITLE: {old}\n", f"TITLE: {new}\n")
    block = re.sub(r"^(VERIFICATION: .*)$", rf"\1\nREVIEW_STATUS: {review}", block, count=1, flags=re.M)
    match = re.search(r"^STATEMENT: >>>\n(.*?)\n<<<\n", block, re.M | re.S)
    clauses = split_clauses(match.group(1)) if match else []
    if not clauses:
        assert rid not in ALLOCATION, rid
        return block
    alloc = ALLOCATION[rid]
    assert sorted(alloc) == [c for c, _ in clauses], (rid, sorted(alloc))
    block = block[:match.start()] + block[match.end():]
    nodes = []
    for letter, text in clauses:
        cid = f"{rid}({letter})"
        if cid in TEXTS:
            old, new = TEXTS[cid]
            assert " ".join(text.split()) == old, cid
            text = textwrap.fill(new, 112)
        withdrawn = cid in WITHDRAWN
        tests = [] if withdrawn else alloc[letter]
        nodes.append(clause_node(rid, letter, text, VERIFY.get(cid, verification), tests,
                                 "withdrawn" if withdrawn else None,
                                 CLAUSE_REVIEW.get(cid) or clause_reviews.get(letter), WITHDRAWN.get(cid)))
    return block + "\n\n" + "\n\n".join("\n".join(n) for n in nodes)


RECORD = """\
        - id: DOC-43
          date: 2026-10-02
          baseline: integ/night11 baa1d837 (before), branch agent/DOC-43 (after)
          scope: >-
            all 59 requirements - lettered clauses as nodes of their own with verification and test entries per
            clause (R7), the findings R3, R4 and R5 of INS-SSR-01, the review status as the field REVIEW_STATUS
          method: >-
            conversion by tools/trace/migrate_clauses.py with an equivalence check (--verify: every text equal word
            for word after the clauses are joined again, every other field equal, apart from the changes listed
            under result); the allocation of the test entries to the clauses read from the test comments of
            tools/test/tests.yaml and, where unclear, from the test sources
          inspector: agent session DOC-43 (author side, like the authors of the SSR)
          independence: >-
            none - the closures of R3, R4, R5 and R7 and the allocation of tests to clauses wait for the acceptance
            of the safety responsible (safety review of DOC-43)
          result:
            requirements: 59
            clause_nodes: 149 (148 in force, SSR-010(d) withdrawn)
            closes: [R3, R4, R5, R7]
            changes: >-
              R3 SSR-010(d) withdrawn (repeated SSR-028(a)); R4 SSR-024(d) states the pass once per start and leaves
              the repetition to SSR-067 (new wording, REVIEW_STATUS proposed); R5 title of SSR-007 without the
              configuration keyword; R7 VERIFICATION and TESTS per clause, tools/trace warns about a clause whose
              verification names test without a test entry and about a test entry of a requirement that no clause
              claims
          open: []
"""


def strip_review_status(text: str) -> str:
    """The map `requirements:` of the review record without its status keys (the field REVIEW_STATUS holds them),
    the findings closed here marked as closed, and the record of this step."""
    head, sep, tail = text.partition("      requirements:\n")
    lines = []
    for line in tail.splitlines(keepends=True):
        if re.match(r"\s+SSR-\d{3}: \{", line):
            line = re.sub(r"\{status: \w+, ", "{", line)
            line = re.sub(r"clauses: \{[^}]*\}, ", "", line)
            line = re.sub(r"open: \[(R[345])\]", r"closed: [\1 by DOC-43]", line)
        lines.append(line)
    head = head.replace(
        "Moved verbatim from the section ``review`` of doc/safety/requirements.yaml (SYS-72). tools/trace does not "
        "read it.\n",
        "Moved verbatim from the section ``review`` of doc/safety/requirements.yaml (SYS-72). tools/trace does not "
        "read it.\nSince DOC-43 (step 3b) the review status of every requirement is the field REVIEW_STATUS of its "
        "node (of a clause\nonly where it differs from its requirement); the map ``requirements`` below keeps the "
        "changes and remarks.\n", 1)
    return head + RECORD + sep + "".join(lines)


def convert(text: str) -> str:
    if "[CLAUSE]" in text:
        raise SystemExit(f"{SSR_FILE} already has clause nodes")
    reviews = review_map(text)
    parts = re.split(r"(?m)^(?=\[SSR\]\n)", text)
    out = [parts[0]]
    for part in parts[1:]:
        # the block ends at the first blank line followed by a node, section end or section start
        match = re.search(r"\n\n(?=\[)", part)
        block = part[:match.start()] if match else part.rstrip("\n")
        rest = part[len(block):]
        out.append(convert_block(block, reviews) + rest)
    text = "".join(out).replace("requirements.yaml follows unchanged.\n", "requirements.yaml follows unchanged.\n\n"
                                + HEADER_UPDATE, 1)
    return strip_review_status(text)


HEADER_UPDATE = """\
Update DOC-43 (step 3b, 2026-10-02): every lettered clause is a CLAUSE node of its own with the UID SSR-nnn(a), its
text, VERIFICATION and TESTS (entries of tools/test/tests.yaml that name the requirement and verify this clause),
and a Parent relation to its SSR; a requirement with clauses has no STATEMENT of its own. A clause is never
renumbered; a dropped clause keeps its UID with STATUS withdrawn. REVIEW_STATUS (proposed, rework, inspected,
accepted) is a field of every SSR, and of a clause where it differs. tools/trace checks the clause nodes (UID,
parent, letters in order, verification within the requirement's, every test entry known and naming the
requirement) and warns about a clause verified by test without a test entry and about a test entry that no clause
claims.
"""


def verify(rev: str) -> int:
    """The old SSR of `rev` against the new StrictDoc model: every field equal, every text equal word for word after
    the clauses are joined again - except the changes of CHANGES above, which are listed."""
    old_text = git_show(rev, SSR_FILE)
    old = {}
    for part in re.split(r"(?m)^(?=\[SSR\]\n)", old_text)[1:]:
        rid = re.search(r"^UID: (\S+)$", part, re.M).group(1)
        statement = re.search(r"^STATEMENT: >>>\n(.*?)\n<<<$", part, re.M | re.S)
        statement = statement.group(1) if statement else re.search(r"^STATEMENT: (.*)$", part, re.M).group(1)
        fields = {k: v for k, v in re.findall(r"^(FINDINGS|STAGE|ASIL|STATUS|VERIFICATION): (.*)$", part, re.M)}
        title = re.search(r"^TITLE: (.*)$", part, re.M).group(1)
        old[rid] = {"title": title, "text": statement, **fields}
    model = tracecheck.safety_model()
    new = {r["id"]: r for r in model.requirements}
    problems, changes = [], []
    if set(old) != set(new):
        problems.append(f"requirement IDs differ: {sorted(set(old) ^ set(new))}")
    for rid in sorted(set(old) & set(new)):
        o, n = old[rid], new[rid]
        clauses = split_clauses(o["text"])
        expected = " ".join(o["text"].split())
        if clauses:
            parts = []
            for letter, text in clauses:
                cid = f"{rid}({letter})"
                if cid in WITHDRAWN:
                    changes.append(f"{cid} withdrawn")
                    continue
                if cid in TEXTS:
                    changes.append(f"{cid} text: {TEXTS[cid][1]}")
                    text = TEXTS[cid][1]
                parts.append(f"({letter}) {text}")
            expected = " ".join(" ".join(parts).split())
            got_ids = [c["id"] for c in n.get("clauses", [])]
            if got_ids != [f"{rid}({c})" for c, _ in clauses]:
                problems.append(f"{rid}: clause nodes {got_ids}")
        if n["text"] != expected:
            problems.append(f"{rid}: text differs\n  old: {expected}\n  new: {n['text']}")
        title = TITLES.get(rid, (None, o["title"]))[1]
        if rid in TITLES:
            changes.append(f"{rid} title: {title}")
        if n["title"] != title:
            problems.append(f"{rid}: title {n['title']!r} != {title!r}")
        for key, name in (("findings", "FINDINGS"), ("verification", "VERIFICATION")):
            if n[key] != [v.strip() for v in o.get(name, "").split(",") if v.strip()]:
                problems.append(f"{rid}: {key} {n[key]} != {o.get(name)}")
        for key, name in (("stage", "STAGE"), ("asil", "ASIL"), ("status", "STATUS")):
            if str(n[key]) != o[name]:
                problems.append(f"{rid}: {key} {n[key]} != {o[name]}")
    clauses = sum(len(r.get("clauses", [])) for r in model.requirements)
    print(f"{len(new)} requirements, {clauses} clause nodes; {len(changes)} intended changes:")
    for line in changes:
        print("  " + line)
    for line in problems:
        print("DIFFERENCE " + line)
    print("identical apart from the intended changes" if not problems else f"{len(problems)} differences")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source-rev", required=True, help="git revision with ssr.sdoc before step 3b")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    if args.verify:
        return verify(args.source_rev)
    (REPO / SSR_FILE).write_text(convert(git_show(args.source_rev, SSR_FILE)), encoding="utf-8", newline="\n")
    print(f"{SSR_FILE} written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
