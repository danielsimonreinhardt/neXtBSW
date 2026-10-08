"""dbcgen back-to-back over every DBC of the repository (PLAT-81, tool-classification.md proposal E, finding TF8).

test_dbcgen.py compares the generated pack/unpack code with cantools on the test DBC only. This test finds every DBC
of the projects and test profiles by itself and compares the generated C code with cantools as independent decoder:

  discovery   every *.dbc under projects/ and tools/ (git ls-files, else a directory walk). The ECU descriptions
              (every YAML with a section can: dbc:) name the node of the DBC; the used messages are the ones the
              node sends or receives (rtegen generates a subset of them). A DBC that no description names is used
              with all messages. Messages dbcgen cannot generate (multiplexed, CAN FD, float) are skipped and listed;
              a description that uses one of them fails the test.
  values      per signal: minimum and maximum of the bit field, the raw values of the DBC minimum and maximum,
              raw 0, physical 0 (nearest encodable value), and random values (fixed seed per DBC)
  bytes       Pack of the raw values against cantools encode (scaling off), Unpack of these bytes and of random
              frames against cantools decode
  physics     physical value -> FromDouble -> Pack against cantools encode (scaling on); Unpack of cantools' bytes ->
              ToDouble (and ToInt for integer scaling) against cantools decode. Raw values outside the DBC range are
              clamped by the generated code (documented behaviour), there the value must lie inside [min, max]
  E2E         the signals named as crc, counter and data id nibble in can.qualifiers.<Message>.e2e are tested like
              every other signal; in addition the bit offset rtegen derives for the E2E library (rtegen.e2e_field)
              must be the byte or nibble where cantools puts the signal
  mutants     mutations of the generated code of the demo DBC (bit position, byte order, sign extension, length,
              factor, offset, clamp limit, padding, E2E field) must turn the comparison red

Run: .venv/Scripts/python -m unittest tools/dbcgen/test_project_dbcs.py
"""

import math
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import cantools
import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "rtegen"))

import dbcgen  # noqa: E402
import rtegen  # noqa: E402  (only e2e_field, the offset rtegen hands to the E2E library)

DBC_ROOTS = ("projects", "tools")
YAML_ROOTS = ("projects", "tools", "ecu")
# PLAT-62: the demo CAN matrix of the own full test profile (in every tree) carries the mutants; the eval board's DBC
# and the Studio's test DBC are private (PLAT-46 F10/F12) and only expected where they exist
DEMO_DBC = "tools/nxconfig/test/profiles/projects/testproject_full/TestFull.dbc"
DEMO_RTE = "tools/nxconfig/test/profiles/projects/testproject_full/rte.yaml"
PRIVATE_DBCS = ("projects/evalboard_s32k144_demo/EvalBoard.dbc", "tools/studio/tests/profiles/studio_test.dbc")
RANDOM_VALUES = 24           # random raw values per signal
RANDOM_FRAMES = 40           # random frames per message for Unpack
REL_TOL = 1e-9


def c_int(value: int) -> str:
    if value < 0:
        return f"(INT64_C({value + 1}) - 1)"
    return f"INT64_C({value})"


def c_double(value: float) -> str:
    text = repr(float(value))
    return text if ("e" in text or "." in text) else text + ".0"


# ---------------------------------------------------------------------------------------------------- discovery

def tracked(roots, patterns) -> list[Path]:
    """Files under the roots that git tracks; a directory walk if git is not available."""
    try:
        out = subprocess.run(["git", "ls-files", "--", *[f"{r}/{p}" for r in roots for p in patterns]],
                             cwd=REPO, capture_output=True, text=True, check=True).stdout
        files = [REPO / line for line in out.splitlines() if line.strip()]
        if files:
            return sorted(files)
    except (OSError, subprocess.CalledProcessError):
        pass
    skip = {"build", "external", ".venv", ".claude", "__pycache__"}
    return sorted(p for r in roots for pat in patterns for p in (REPO / r).rglob(pat)
                  if not skip & set(p.relative_to(REPO).parts))


@dataclass
class Use:
    """One ECU description (rte.yaml or test profile) that uses a DBC."""
    source: str                                   # YAML file, repo relative
    node: str
    e2e: dict = field(default_factory=dict)       # message -> {kind: signal}
    messages: set = field(default_factory=set)    # messages named in connections, monitor, qualifiers


@dataclass
class ProjectDbc:
    path: Path
    uses: list[Use] = field(default_factory=list)

    @property
    def rel(self) -> str:
        return self.path.relative_to(REPO).as_posix()


def uses_of(yaml_path: Path) -> tuple[Path, Use] | None:
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        return None
    can = data.get("can") if isinstance(data, dict) else None
    if not isinstance(can, dict) or not isinstance(can.get("dbc"), str):
        return None
    dbc = REPO / can["dbc"]
    if not dbc.is_file():
        dbc = yaml_path.parent / can["dbc"]
    use = Use(yaml_path.relative_to(REPO).as_posix(), str(can.get("node", "")))
    use.messages.update(can.get("monitor") or [])
    for name, entry in (can.get("qualifiers") or {}).items():
        use.messages.add(name)
        e2e = (entry or {}).get("e2e") if isinstance(entry, dict) else None
        if isinstance(e2e, dict):
            use.e2e[name] = {kind: e2e[key] for kind, key in (("crc", "crc"), ("counter", "counter"),
                                                              ("data id nibble", "data_id_nibble")) if key in e2e}
    for key, value in (data.get("connections") or {}).items():
        for target in [key] + (value if isinstance(value, list) else [value]):
            m = re.match(r"can\.(?:rx|tx|qlf)\.(\w+)", str(target))
            if m:
                use.messages.add(m.group(1))
    return dbc.resolve(), use


def discover() -> list[ProjectDbc]:
    dbcs = {p.resolve(): ProjectDbc(p.resolve()) for p in tracked(DBC_ROOTS, ["*.dbc"])}
    for y in tracked(YAML_ROOTS, ["*.yaml", "*.yml"]):
        found = uses_of(y)
        if found is None:
            continue
        path, use = found
        dbcs.setdefault(path, ProjectDbc(path)).uses.append(use)
    return [dbcs[k] for k in sorted(dbcs, key=lambda p: p.relative_to(REPO).as_posix())]


# ---------------------------------------------------------------------------------------------------- selection

@dataclass
class Selection:
    messages: list                          # dbcgen.Message, generated and compared
    skipped: dict                           # message -> reason
    e2e: dict                               # (message, signal) -> (kind, rtegen offset)
    errors: list


def select(pdbc: ProjectDbc) -> Selection:
    db = dbcgen.load(pdbc.path)
    errors = []
    if pdbc.uses:
        names = set()
        for use in pdbc.uses:
            try:
                names |= {m.name for m in db.for_node(use.node)}
            except dbcgen.DbcError as e:
                errors.append(f"{use.source}: {e}")
            for name in sorted(use.messages):
                if name not in {m.name for m in db.messages}:
                    errors.append(f"{use.source}: message {name} is not in {pdbc.rel}")
        wanted = [m for m in db.messages if m.name in names]
    else:
        wanted = list(db.messages)
    messages = [m for m in wanted if not m.problem]
    skipped = {m.name: m.problem for m in wanted if m.problem}
    for use in pdbc.uses:
        for name in sorted(use.messages & set(skipped)):
            errors.append(f"{use.source}: uses {name}, which dbcgen cannot generate: {skipped[name]}")
    e2e = {}
    for use in pdbc.uses:
        for msg_name, fields in use.e2e.items():
            msg = next((m for m in messages if m.name == msg_name), None)
            if msg is None:
                errors.append(f"{use.source}: E2E message {msg_name} is not generated")
                continue
            for kind, sig_name in fields.items():
                bits = 8 if kind == "crc" else 4
                try:
                    offset = rtegen.e2e_field(msg.signal(sig_name), kind, bits, use.source)
                except (dbcgen.DbcError, rtegen.RteError) as e:
                    errors.append(f"{use.source}: {e}")
                    continue
                e2e[(msg_name, sig_name)] = (kind, offset)
    return Selection(messages, skipped, e2e, errors)


# ---------------------------------------------------------------------------------------------------- vectors

def raw_points(s, rng) -> list[int]:
    """Raw test values of one signal: limits of the bit field and of the DBC range, zero, random."""
    points = [s.bit_min, s.bit_max, s.raw_lo, s.raw_hi, 0]
    zero = (0 - s.offset) / s.factor              # raw value of physical 0, nearest encodable
    points.append(min(max(dbcgen.round_half_away(zero), s.raw_lo), s.raw_hi))
    points += [rng.randint(s.bit_min, s.bit_max) for _ in range(RANDOM_VALUES // 2)]
    points += [rng.randint(s.raw_lo, s.raw_hi) for _ in range(RANDOM_VALUES // 2)]
    return list(dict.fromkeys(points))


def in_range(rs, phys: float) -> bool:
    lo = rs.minimum if rs.minimum is not None else -math.inf
    hi = rs.maximum if rs.maximum is not None else math.inf
    if lo == 0 and hi == 0:                      # [0|0]: no range in the DBC
        return True
    tol = REL_TOL * (1 + abs(phys))
    return lo - tol <= phys <= hi + tol


def build_driver(messages, ref, rng, e2e) -> tuple[str, dict]:
    lines = ['#include "CanDb.h"', "#include <math.h>", "#include <stdio.h>", "#include <string.h>", "",
             "static unsigned checks, failures;",
             "#define CHECK(c, ...) do { ++checks; if (!(c)) { ++failures; if (failures <= 40U) {"
             " printf(\"FAIL \"); printf(__VA_ARGS__); printf(\"\\n\"); } } } while (0)",
             "static int near(double a, double b) { return fabs(a - b) <= 1e-9 * (1.0 + fabs(b)); }", ""]
    body = []
    stats = {"signals": 0, "values": 0, "e2e": 0}
    for m in messages:
        rm = ref.get_message_by_name(m.name)
        p = f"CanDb_{m.name}"
        n = m.length
        points = {s.name: raw_points(s, rng) for s in m.signals}
        stats["signals"] += len(m.signals)
        stats["values"] += sum(len(v) for v in points.values())
        # one vector per value of every signal; the other signals take random raw values
        vectors = []
        for s in m.signals:
            for raw in points[s.name]:
                values = {o.name: rng.randint(o.bit_min, o.bit_max) for o in m.signals}
                values[s.name] = raw
                vectors.append(values)
        if not m.signals:
            vectors.append({})

        # bytes: raw values -> Pack against cantools, cantools bytes -> Unpack
        raw_bytes = [rm.encode(v, scaling=False, strict=False, padding=False) for v in vectors]
        members = [s.name for s in m.signals]
        lines.append(f"static int {m.name}_equal({p}_t const* a, {p}_t const* b)")
        lines.append("{ (void)a; (void)b; return 1" + "".join(f" && (a->{x} == b->{x})" for x in members) + "; }")
        lines.append(f"static {p}_t const {m.name}_raw[] = {{")
        for v in vectors:
            lines.append("    {" + (", ".join(f"({s.ctype}){c_int(v[s.name])}" for s in m.signals) or "0U") + "},")
        lines.append("};")
        lines.append(f"static uint8_t const {m.name}_raw_bytes[][{n}] = {{")
        for data in raw_bytes:
            lines.append("    {" + ", ".join(f"0x{b:02X}U" for b in data) + "},")
        lines.append("};")
        body += [
            f"    for (unsigned i = 0U; i < {len(vectors)}U; ++i) {{",
            f"        uint8_t data[{n}]; {p}_t msg;",
            "        memset(data, 0xA5, sizeof(data));",
            f"        {p}_Pack(&{m.name}_raw[i], data);",
            f'        CHECK(memcmp(data, {m.name}_raw_bytes[i], sizeof(data)) == 0, "{m.name} pack raw vector %u", i);',
            f"        {p}_Unpack(&msg, {m.name}_raw_bytes[i]);",
            f'        CHECK({m.name}_equal(&msg, &{m.name}_raw[i]), "{m.name} unpack raw vector %u", i);',
            "    }",
        ]
        # unpack of random frames (bits outside of signals set as well)
        frames = [bytes(rng.getrandbits(8) for _ in range(n)) for _ in range(RANDOM_FRAMES)]
        lines.append(f"static uint8_t const {m.name}_frames[][{n}] = {{")
        for f in frames:
            lines.append("    {" + ", ".join(f"0x{b:02X}U" for b in f) + "},")
        lines.append("};")
        lines.append(f"static {p}_t const {m.name}_frames_raw[] = {{")
        for f in frames:
            d = rm.decode(f, scaling=False, decode_choices=False)
            lines.append("    {" + (", ".join(f"({s.ctype}){c_int(int(d[s.name]))}" for s in m.signals) or "0U") + "},")
        lines.append("};")
        body += [
            f"    for (unsigned i = 0U; i < {RANDOM_FRAMES}U; ++i) {{",
            f"        {p}_t msg; {p}_Unpack(&msg, {m.name}_frames[i]);",
            f'        CHECK({m.name}_equal(&msg, &{m.name}_frames_raw[i]), "{m.name} unpack random frame %u", i);',
            "    }",
        ]

        # physics per signal and value: cantools' physical value -> FromDouble -> Pack == cantools encode (scaled);
        # Unpack(cantools bytes) -> ToDouble / ToInt == cantools decode
        for s in m.signals:
            rs = rm.get_signal_by_name(s.name)
            f = f"{p}_{s.name}"
            for raw in points[s.name]:
                values = {o.name: 0 for o in m.signals}
                values[s.name] = raw
                data = rm.encode(values, scaling=False, strict=False, padding=False)
                phys = rm.decode(data, scaling=True, decode_choices=False)[s.name]
                phys = float(phys)
                bytes_c = "{" + ", ".join(f"0x{b:02X}U" for b in data) + "}"
                tag = f"{m.name}.{s.name} raw {raw}"
                body.append(f"    {{ static uint8_t const exp[{n}] = {bytes_c}; {p}_t msg;")
                body.append(f"      {p}_Unpack(&msg, exp);")
                if in_range(rs, phys):
                    enc = rm.encode({**{o.name: rm.get_signal_by_name(o.name).conversion.raw_to_scaled(0)
                                        for o in m.signals}, s.name: phys},
                                    scaling=True, strict=False, padding=False)
                    enc_c = "{" + ", ".join(f"0x{b:02X}U" for b in enc) + "}"
                    body.append(f"      {{ static uint8_t const enc[{n}] = {enc_c}; uint8_t data[{n}];")
                    body += [f"        memset(&msg, 0, sizeof(msg)); msg.{s.name} = {f}_FromDouble({c_double(phys)});",
                             "        memset(data, 0x5A, sizeof(data));",
                             f"        {p}_Pack(&msg, data);",
                             f'        CHECK(memcmp(data, enc, sizeof(data)) == 0, "{tag}: FromDouble+Pack bytes"); }}',
                             f"      {p}_Unpack(&msg, exp);",
                             f'      CHECK(near({f}_ToDouble(msg.{s.name}), {c_double(phys)}), '
                             f'"{tag}: ToDouble %.17g, cantools {phys!r}", {f}_ToDouble(msg.{s.name}));']
                    if s.integer:
                        body.append(f'      CHECK({f}_ToInt(msg.{s.name}) == {c_int(round(phys))}, '
                                    f'"{tag}: ToInt %lld, cantools {phys!r}", (long long){f}_ToInt(msg.{s.name}));')
                else:
                    # outside [min, max]: the generated conversion clamps to the DBC range
                    lo = float(s.minimum) - REL_TOL * (1 + abs(float(s.minimum)))
                    hi = float(s.maximum) + REL_TOL * (1 + abs(float(s.maximum)))
                    body.append(f'      CHECK({f}_ToDouble(msg.{s.name}) >= {c_double(lo)} && {f}_ToDouble(msg.{s.name})'
                                f' <= {c_double(hi)}, "{tag}: ToDouble %.17g not clamped", {f}_ToDouble(msg.{s.name}));')
                body.append("    }")
            # E2E: the field offset rtegen gives the E2E library is where cantools puts the signal
            if (m.name, s.name) in e2e:
                kind, offset = e2e[(m.name, s.name)]
                stats["e2e"] += 1
                bits = 8 if kind == "crc" else 4
                for value in range(1 << bits):
                    data = rm.encode({**{o.name: 0 for o in m.signals}, s.name: value},
                                     scaling=False, strict=False, padding=False)
                    field_value = (data[offset // 8] >> (offset % 8)) & ((1 << bits) - 1)
                    others = bytes(b if i != offset // 8 else b & ~(((1 << bits) - 1) << (offset % 8)) & 0xFF
                                   for i, b in enumerate(data))
                    if field_value != value or any(others):
                        raise AssertionError(f"{m.name}.{s.name}: E2E {kind} offset {offset} (rtegen) is not where "
                                             f"cantools puts the signal (value {value}: {data.hex()})")
    lines += ["", "int main(void)", "{"] + body + [
        '    printf("%u checks, %u failures\\n", checks, failures);', "    return failures ? 1 : 0;", "}", ""]
    return "\n".join(lines), stats


def compile_and_run(out: Path) -> subprocess.CompletedProcess:
    exe = out / "b2b.exe"
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Wpedantic", "-Werror", "-Wno-overlength-strings", "-Wno-unused-function", f"-I{out}",
           str(out / "CanDb.c"), str(out / "driver.c"), "-o", str(exe)]
    built = subprocess.run(cmd, capture_output=True, text=True)
    if built.returncode != 0:
        return built
    return subprocess.run([str(exe)], capture_output=True, text=True)


@dataclass
class Prepared:
    pdbc: ProjectDbc
    sel: Selection
    out: Path
    stats: dict


def prepare(pdbc: ProjectDbc, root: Path) -> Prepared:
    sel = select(pdbc)
    out = root / re.sub(r"[^A-Za-z0-9]+", "_", pdbc.rel)
    rng = random.Random(zlib.crc32(pdbc.rel.encode()))        # fixed seed per DBC
    ref = cantools.database.load_file(str(pdbc.path), database_format="dbc", strict=True)
    dbcgen.write_files(out, dbcgen.render(sel.messages, pdbc.path.name))
    driver, stats = build_driver(sel.messages, ref, rng, sel.e2e)
    (out / "driver.c").write_text(driver, encoding="utf-8")
    return Prepared(pdbc, sel, out, stats)


# ---------------------------------------------------------------------------------------------------- tests

class DiscoveryTest(unittest.TestCase):
    def test_finds_project_and_profile_dbcs(self):
        found = {p.rel: p for p in discover()}
        for rel in (DEMO_DBC, "tools/rtegen/test/can_host/qualifier.dbc",
                    *(rel for rel in PRIVATE_DBCS if (REPO / rel).is_file())):
            self.assertIn(rel, found)
        demo = found[DEMO_DBC]
        self.assertIn(DEMO_RTE, [u.source for u in demo.uses])
        self.assertTrue(any(u.e2e for u in demo.uses), "E2E signals of the demo not found")
        # every project that has a DBC is covered
        for dbc in (REPO / "projects").glob("*/*.dbc"):
            self.assertIn(dbc.relative_to(REPO).as_posix(), found)


class ProjectDbcBackToBackTest(unittest.TestCase):
    """Generated pack/unpack code of every DBC against cantools."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_dbc(self):
        dbcs = discover()
        self.assertGreaterEqual(len(dbcs), 2)
        report = []
        for pdbc in dbcs:
            with self.subTest(dbc=pdbc.rel):
                prep = prepare(pdbc, self.tmp)
                self.assertEqual(prep.sel.errors, [])
                self.assertTrue(prep.sel.messages, f"{pdbc.rel}: no message to compare")
                run = compile_and_run(prep.out)
                self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-4000:])
                self.assertIn(", 0 failures", run.stdout)
                nodes = ", ".join(sorted({u.node for u in pdbc.uses})) or "all messages"
                skipped = f", skipped {', '.join(sorted(prep.sel.skipped))}" if prep.sel.skipped else ""
                report.append(f"  {pdbc.rel} ({nodes}): {len(prep.sel.messages)} messages, "
                              f"{prep.stats['signals']} signals ({prep.stats['e2e']} E2E), "
                              f"{prep.stats['values']} values, {run.stdout.strip().splitlines()[-1]}{skipped}")
        print("\n" + "\n".join(report), file=sys.stderr)


# mutants of the generated code of the demo DBC: (name, file, regex, replacement); every regex must match once
MUTANTS = [
    ("E2E crc one bit later (pack)", "CanDb.c", r"PutIntel\(data, 24U, 8U,", "PutIntel(data, 25U, 8U,"),
    ("E2E counter length 3 (unpack)", "CanDb.c", r"GetIntel\(data, 40U, 4U\)(?=;\s*\n\}\s*\n\s*/\* ---- EVB_Analog)",
     "GetIntel(data, 40U, 3U)"),
    ("byte order Intel instead of Motorola (pack)", "CanDb.c", r"PutMotorola\(data, 7U, 12U,",
     "PutIntel(data, 7U, 12U,"),
    ("sign extension dropped", "CanDb.c",
     r"\(int16_t\)CanDb_SignExtend\((CanDb_GetIntel\(data, 16U, 12U\)), 12U\)", r"(int16_t)\1"),
    ("factor 0.5 -> 0.25 in ToDouble", "CanDb.h",
     r"(CanDb_EVB_Analog_MotorNtcFine_mV_ToDouble\(int16_t raw\)\s*\{[^}]*\* )0\.5\)", r"\g<1>0.25)"),
    ("offset 1000 -> 1001 in FromDouble", "CanDb.h",
     r"(CanDb_PC_Command_Setpoint_mV_FromDouble\(double value\)\s*\{\s*double x = \(value - )1000\.0\)",
     r"\g<1>1001.0)"),
    ("clamp limit one raw step too low", "CanDb.h",
     r"(CanDb_PC_Command_Offset_mV_ClampRaw\(int64_t raw\)\s*\{[^}]*?\(raw > )INT64_C\(2047\)\) \? INT64_C\(2047\)",
     r"\g<1>INT64_C(2046)) ? INT64_C(2046)"),
    ("unused bits not cleared", "CanDb.c", r"(void CanDb_EVB_Status_Pack[^{]*\{[^}]*?)data\[i\] = 0U;",
     r"\g<1>data[i] = (uint8_t)(data[i] & 0x80U);"),
]


class MutantTest(unittest.TestCase):
    """A wrong generator output must turn the back-to-back comparison red (K6)."""

    def test_mutants_are_detected(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            pdbc = next(p for p in discover() if p.rel == DEMO_DBC)
            prep = prepare(pdbc, tmp)
            self.assertIn(", 0 failures", compile_and_run(prep.out).stdout)
            originals = {name: (prep.out / name).read_text(encoding="utf-8") for name in ("CanDb.c", "CanDb.h")}
            for name, file, pattern, replacement in MUTANTS:
                with self.subTest(mutant=name):
                    mutated, count = re.subn(pattern, replacement, originals[file])
                    self.assertEqual(count, 1, f"mutant '{name}' does not match the generated {file} once")
                    (prep.out / file).write_text(mutated, encoding="utf-8")
                    run = compile_and_run(prep.out)
                    (prep.out / file).write_text(originals[file], encoding="utf-8")
                    self.assertNotEqual(run.returncode, 0, f"mutant '{name}' survived:\n{run.stdout[-2000:]}")
                    self.assertIn("FAIL", run.stdout, f"mutant '{name}' did not compile:\n{run.stderr[-2000:]}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
