# SPDX-License-Identifier: Apache-2.0
"""Consistency of doc/safety/verification-plan.md with the repository (SYS-67).

The plan states facts that live in other files; this test keeps them equal, so the plan cannot drift silently:

- levels: every group of tools/test/tests.yaml has at least one row in the level table (section 2), and the table
  names no group the list does not know;
- tools: every version of section 4.1 equals its pin file (versions.toml, .python-version, constraints.txt,
  tools/misra/requirements.txt);
- switches: every row of section 3.2 equals pipeline.toml or coverage.yaml;
- derivation: every `derivation` value in tests.yaml is a method of section 5.1, only on entries with
  `requirements:`, every entry with `requirements:` carries it (SYS-166), and the example table of section 5.3 is
  exactly the entries that carry the field;
- provenance: every field of section 7.1 is written by `tools.ci.ci.provenance`;
- counter-checks (DOC-54, CM-04 F-09): the list of section 3.5 is exactly the safety entries without
  `fault-injection` in `derivation`;
- counts (DOC-54, CM-04 F-04): the table of section 3.6 equals tests.yaml and the summary of traceability.md.

Every rule has a counter-check on a broken copy of the plan or of the list.

    python -m unittest tools/ci/test_verification_plan.py
"""

from __future__ import annotations

import re
import sys
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from tools.ci import ci  # noqa: E402

PLAN = REPO / "doc" / "safety" / "verification-plan.md"
TESTS = REPO / "tools" / "test" / "tests.yaml"


def block(text: str, name: str) -> list[dict]:
    """Rows of the Markdown table between `<!-- vp-<name>:start -->` and `<!-- vp-<name>:end -->`."""
    match = re.search(rf"<!-- vp-{name}:start -->\n(.*?)\n<!-- vp-{name}:end -->", text, re.S)
    if not match:
        raise AssertionError(f"block vp-{name} missing in {PLAN.name}")
    lines = [ln for ln in match.group(1).splitlines() if ln.startswith("|")]
    head = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    return [dict(zip(head, [c.strip() for c in ln.strip().strip("|").split("|")])) for ln in lines[2:]]


def ticks(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def level_problems(plan: str, groups: list[str]) -> list[str]:
    named = [g for row in block(plan, "levels") for g in ticks(row["tests.yaml groups"])]
    return ([f"group {g} of tests.yaml has no level in section 2" for g in groups if g not in named]
            + [f"section 2 names group {g}, which tests.yaml does not know" for g in named if g not in groups])


def pinned_versions(repo: Path = REPO) -> dict[str, str]:
    versions = tomllib.loads((repo / "tools" / "ci" / "versions.toml").read_text(encoding="utf-8"))["tools"]
    pins = {
        "ARM GCC (xPack)": versions["arm_gcc"]["version"],
        "CMake": versions["cmake"]["version"],
        "Ninja": versions["ninja"]["version"],
        "Python": (repo / "tools" / "ci" / ".python-version").read_text(encoding="utf-8").strip(),
    }
    constraints = ci.read_constraints(repo / "tools" / "ci" / "constraints.txt")
    pins["ziglang (zig cc/c++, clang)"] = constraints.get("ziglang", "?")
    misra = ci.read_constraints(repo / "tools" / "misra" / "requirements.txt")
    pins["cppcheck (pip wheel)"] = misra.get("cppcheck", "?")
    pins["clang-tidy (pip wheel)"] = misra.get("clang-tidy", "?")
    return pins


def tool_problems(plan: str, pins: dict[str, str]) -> list[str]:
    rows = {row["Tool"]: row["Version"] for row in block(plan, "tools")}
    problems = [f"section 4.1: {tool} is {rows.get(tool)}, pinned {version}" for tool, version in pins.items()
                if rows.get(tool) != version]
    return problems + [f"section 4.1 names {t}, which has no pin rule in this test" for t in rows if t not in pins]


def switch_values(repo: Path = REPO) -> dict[tuple[str, str], str]:
    pipeline = tomllib.loads((repo / "tools" / "ci" / "pipeline.toml").read_text(encoding="utf-8"))
    coverage = yaml.safe_load((repo / "tools" / "test" / "coverage.yaml").read_text(encoding="utf-8"))
    values = {}
    for section in ("run", "coverage", "static_analysis"):
        for key, value in pipeline.get(section, {}).items():
            if isinstance(value, bool):
                values[(f"{section}.{key}", "tools/ci/pipeline.toml")] = str(value).lower()
    values[("enforce", "tools/test/coverage.yaml")] = str(bool(coverage.get("enforce"))).lower()
    return values


def switch_problems(plan: str, values: dict[tuple[str, str], str]) -> list[str]:
    problems = []
    for row in block(plan, "switches"):
        key = (ticks(row["Setting"])[0], ticks(row["File"])[0])
        stated = ticks(row["Value"])[0]
        if key not in values:
            problems.append(f"section 3.2: {key[0]} in {key[1]} does not exist")
        elif values[key] != stated:
            problems.append(f"section 3.2: {key[0]} in {key[1]} is {values[key]}, the plan says {stated}")
    return problems


def derivation_problems(plan: str, tests: list[dict]) -> list[str]:
    methods = {ticks(row["Value"])[0] for row in block(plan, "derivation")}
    examples = {ticks(row["Entry"])[0]: [v.strip() for v in row["derivation"].split(",")]
                for row in block(plan, "derivation-examples")}
    problems = []
    carrying = {}
    for test in tests:
        values = test.get("derivation")
        if values is None:
            if test.get("requirements"):
                problems.append(f"{test['name']}: safety entry (requirements) without derivation")
            continue
        name = test["name"]
        if not isinstance(values, list) or not values:
            problems.append(f"{name}: derivation must be a non-empty list")
            continue
        carrying[name] = values
        if not test.get("requirements"):
            problems.append(f"{name}: derivation without requirements (only safety test entries carry it)")
        problems += [f"{name}: derivation value {v!r} is not a method of section 5.1" for v in values
                     if v not in methods]
    for name, values in carrying.items():
        if examples.get(name) != values:
            problems.append(f"{name}: section 5.3 says {examples.get(name)}, tests.yaml {values}")
    problems += [f"section 5.3 names {n}, which carries no derivation in tests.yaml" for n in examples
                 if n not in carrying]
    return problems


def without_fault_injection(tests: list[dict]) -> list[str]:
    """Safety entries (with `requirements:`) whose derivation names no fault injection, in list order."""
    return [t["name"] for t in tests if t.get("requirements") and "fault-injection" not in (t.get("derivation") or [])]


def counter_check_problems(plan: str, tests: list[dict]) -> list[str]:
    """DOC-54 (CM-04 F-09): section 3.5 lists exactly the safety entries without `fault-injection`."""
    listed = [ticks(row["Entry"])[0] for row in block(plan, "no-counter-check")]
    wanted = without_fault_injection(tests)
    problems = [f"section 3.5 lists {n} twice" for n in sorted({n for n in listed if listed.count(n) > 1})]
    problems += [f"section 3.5 misses {n} (safety entry without fault-injection)" for n in wanted if n not in listed]
    problems += [f"section 3.5 lists {n}, which is no safety entry without fault-injection" for n in listed
                 if n not in wanted]
    problems += [f"section 3.5: marker of {row['Entry']} is {row['Marker']!r}, not 'found' or 'none found'"
                 for row in block(plan, "no-counter-check") if row["Marker"] not in ("found", "none found")]
    return problems


def trace_summary(trace: str) -> dict[str, int]:
    """Counts of the summary table of traceability.md."""
    req = re.search(r"^\| Requirements \| (\d+) \(\d+ implemented, \d+ partial, (\d+) open\) \|", trace, re.M)
    tested = re.search(r"^\| Requirements with at least one test \| (\d+) \|", trace, re.M)
    if not req or not tested:
        raise AssertionError("traceability.md summary lines not found")
    return {"SSR": int(req.group(1)), "SSR open": int(req.group(2)), "SSR with a test entry": int(tested.group(1))}


def count_problems(plan: str, tests: list[dict], trace: dict[str, int]) -> list[str]:
    """DOC-54 (CM-04 F-04): the counts of section 3.6 equal tests.yaml and the traceability summary."""
    actual = {
        "entries": len(tests),
        "safety entries": sum(1 for t in tests if t.get("requirements")),
        "mutation entries": sum(1 for t in tests if t["name"].endswith("-mutations")),
        "safety entries without fault-injection": len(without_fault_injection(tests)),
        **trace,
    }
    rows = {row["Count"]: row["Value"] for row in block(plan, "counts")}
    problems = [f"section 3.6 has no row {k!r}" for k in actual if k not in rows]
    problems += [f"section 3.6 row {k!r} is not counted by the test" for k in rows if k not in actual]
    problems += [f"section 3.6: {k} is {rows[k]}, the repository says {v}" for k, v in actual.items()
                 if k in rows and rows[k] != str(v)]
    return problems


def provenance_problems(plan: str, written: dict) -> list[str]:
    fields = [ticks(row["Field of `provenance`"])[0] for row in block(plan, "provenance")]
    known = set(written) | {"end"}
    return [f"section 7.1 names field {f}, which tools/ci does not write" for f in fields if f not in known] + \
        [f"tools/ci writes provenance field {f}, which section 7.1 does not explain" for f in written
         if f not in fields and f != "changes_total"]


class VerificationPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = PLAN.read_text(encoding="utf-8").replace("\r\n", "\n")
        cls.data = yaml.safe_load(TESTS.read_text(encoding="utf-8"))
        cls.tests = cls.data["tests"]

    def test_levels(self):
        self.assertEqual([], level_problems(self.plan, self.data["groups"]))

    def test_tools(self):
        self.assertEqual([], tool_problems(self.plan, pinned_versions()))

    def test_switches(self):
        self.assertEqual([], switch_problems(self.plan, switch_values()))

    def test_derivation(self):
        self.assertEqual([], derivation_problems(self.plan, self.tests))

    def test_provenance(self):
        self.assertEqual([], provenance_problems(self.plan, ci.provenance(REPO)))

    def test_counter_check_list(self):
        self.assertEqual([], counter_check_problems(self.plan, self.tests))

    def test_counts(self):
        trace = trace_summary((REPO / "doc" / "safety" / "traceability.md").read_text(encoding="utf-8"))
        self.assertEqual([], count_problems(self.plan, self.tests, trace))

    def test_state_line(self):
        head = self.plan.split("\n## ", 1)[0]
        self.assertRegex(head, r"\n(Baseline: v\d+\.\d+\.\d+|Working state: dev at [0-9a-f]{7,40} \(after v\d+\.\d+\.\d+\))\n")

    # ------------------------------------------------------------------ counter-checks on broken copies

    def test_counter_checks(self):
        plan = self.plan
        # a group of the list without a level, and an invented group in the table
        self.assertTrue(level_problems(plan, self.data["groups"] + ["newgroup"]))
        self.assertTrue(level_problems(plan.replace("| Unit | `pytest` |", "| Unit | `pyunit` |"), self.data["groups"]))
        # a version that drifted from its pin
        pins = pinned_versions()
        self.assertTrue(tool_problems(plan, {**pins, "CMake": "9.9.9"}))
        self.assertTrue(tool_problems(plan.replace(f"| {pins['Ninja']} |", "| 0.0.1 |"), pins))
        # a switch turned on in the file but not in the plan
        values = switch_values()
        flipped = {**values, ("coverage.enforce", "tools/ci/pipeline.toml"): "true"}
        self.assertTrue(switch_problems(plan, flipped))
        self.assertTrue(switch_problems(plan.replace("`static_analysis.enforce` | `tools/ci/pipeline.toml` | `true`",
                                                     "`static_analysis.enforce` | `tools/ci/pipeline.toml` | `false`"),
                                        values))
        # derivation: unknown method, field on a non-safety entry, example table out of step
        tests = [dict(t) for t in self.tests]
        safety = next(t for t in tests if t.get("derivation"))
        other = next(t for t in tests if not t.get("requirements"))
        self.assertTrue(derivation_problems(plan, [*tests[:0], {**safety, "derivation": ["guesswork"]}]))
        self.assertTrue(derivation_problems(plan, [{**other, "derivation": ["boundary-values"]}]))
        self.assertTrue(derivation_problems(plan, [{**safety, "derivation": ["fault-injection", "boundary-values"]}]
                                            + [t for t in tests if t is not safety]))
        self.assertTrue(derivation_problems(plan, [t for t in tests if t is not safety]))
        # a safety entry without the field (required since SYS-166)
        bare = {k: v for k, v in safety.items() if k != "derivation"}
        self.assertTrue(derivation_problems(plan, [bare if t is safety else t for t in tests]))
        # a broken copy of the plan: one row of the example table removed
        row = next(r for r in plan.split("\n") if r.startswith(f"| `{safety['name']}` |")
                   and not r.endswith("found |"))  # the row of 5.3, not the one of the list in 3.5
        self.assertTrue(derivation_problems(plan.replace(row + "\n", ""), tests))
        # provenance: a field the tool does not write, and a written field the plan does not explain
        written = ci.provenance(REPO)
        self.assertTrue(provenance_problems(plan, {k: v for k, v in written.items() if k != "openbsw"}))
        self.assertTrue(provenance_problems(plan, {**written, "branch": "dev"}))
        # DOC-54: the list of section 3.5 loses a row, or an entry gains fault-injection
        listed = next(t for t in tests if t.get("requirements") and "fault-injection" not in t["derivation"])
        row = next(r for r in plan.split("\n") if r.startswith(f"| `{listed['name']}` | ") and "found" in r)
        self.assertTrue(counter_check_problems(plan.replace(row + "\n", ""), tests))
        self.assertTrue(counter_check_problems(
            plan, [{**t, "derivation": [*t["derivation"], "fault-injection"]} if t is listed else t for t in tests]))
        # DOC-54: a count of section 3.6 that drifted, and a new entry in tests.yaml
        trace = trace_summary((REPO / "doc" / "safety" / "traceability.md").read_text(encoding="utf-8"))
        self.assertTrue(count_problems(plan, tests + [{"name": "new-mutations"}], trace))
        self.assertTrue(count_problems(plan, tests, {**trace, "SSR open": trace["SSR open"] + 1}))
        self.assertTrue(count_problems(plan.replace("| safety entries | ", "| safety entries | 9"), tests, trace))


if __name__ == "__main__":
    unittest.main()
