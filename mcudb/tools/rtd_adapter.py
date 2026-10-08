"""Adapter header over the NXP RTD device headers checked in from Zephyr hal_nxp (PLAT-32).

The RTD headers (`s32/drivers/s32k1/BaseNXP/header/<derivative>*.h`, BSD-3-Clause) describe the same registers as
the S32K148.h the OpenBSW drivers were written against, but a few names and layouts differ (FlexCAN is `CAN_Type`
with `CAN_*` macros and message buffers as `MB[]`, the ADC has `SC1_*` instead of `SC1A_*`, ...). The drivers are
reused code and are not touched. Instead this tool writes one adapter header per derivative,
`<include>/3rdparty/nxp/<derivative>.h`, that

  - includes the unchanged vendor headers,
  - corrects values the vendor header states wrongly (with a source per value, e.g. `__NVIC_PRIO_BITS`),
  - removes instances the derivative does not have (`IP_<instance>*`),
  - adds register layouts under the names the drivers use (a copy of the vendor layout with members replaced by
    others of the same size; offsets are proven equal against the vendor layout when generating),
  - aliases vendor macros to the driver names (`FLEXCAN_<x>` -> `CAN_<x>`), module-wide, not per used symbol.

The rules live in `mcudb/tools/device_header_rules/<family>_rtd.yaml` together with origin and SHA-256 of every
vendor file. `check` proves that the vendor files are the recorded ones, that the adapter in the repository is up to
date and that every register symbol the drivers use resolves (or is listed as absent on the derivative, with reason).

    rtd_adapter.py generate --derivative S32K116          # writes the adapter named in the rules
    rtd_adapter.py check    --derivative S32K116          # exit 1: vendor files changed, adapter outdated, symbols
    rtd_adapter.py symbols  --derivative S32K116          # driver symbols and how they resolve
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import svd2header as s2h  # noqa: E402  (header parser and driver symbol scan, shared with the SVD path)

REPO = s2h.REPO
GENERATOR = "mcudb/tools/rtd_adapter.py"


class RtdError(Exception):
    """Invalid rules, changed vendor files or a layout that does not match."""


def rules_path(family: str) -> Path:
    return HERE / "device_header_rules" / f"{family}_rtd.yaml"


def load_rules(path: Path) -> dict:
    from ruamel.yaml import YAML

    data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise RtdError(f"{path.name}: schema_version 1 expected")
    for key in data:
        if key not in ("schema_version", "family", "origin", "include_dir", "vendor_dir", "modules", "derivatives"):
            raise RtdError(f"{path.name}: unknown key {key!r}")
    for name, d in (data.get("derivatives") or {}).items():
        for key in d:
            if key not in ("header", "include", "core", "absent", "compat", "not_on_derivative", "files"):
                raise RtdError(f"{path.name}: derivatives.{name}: unknown key {key!r}")
    for name, m in (data.get("modules") or {}).items():
        for key in m:
            if key not in ("why", "file", "vendor_type", "type", "members", "append", "macro_prefix"):
                raise RtdError(f"{path.name}: modules.{name}: unknown key {key!r}")
    return data


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _derivative(rules: dict, derivative: str) -> dict:
    d = (rules.get("derivatives") or {}).get(derivative)
    if d is None:
        raise RtdError(f"no entry derivatives.{derivative} in the rules")
    return d


def vendor_dir(rules: dict) -> Path:
    return REPO / rules["include_dir"] / rules["vendor_dir"]


def adapter_path(rules: dict, derivative: str) -> Path:
    return REPO / rules["include_dir"] / _derivative(rules, derivative)["header"]


def verify_vendor_files(rules: dict, derivative: str) -> list[str]:
    """Problems with the vendor files of the derivative: missing, changed or not recorded."""
    folder = vendor_dir(rules)
    recorded = dict(_derivative(rules, derivative).get("files") or {})
    license_file = rules["origin"].get("license_file")
    if license_file:
        recorded[license_file["name"]] = license_file["sha256"]
    problems = []
    for name, sha in recorded.items():
        path = folder / name
        if not path.is_file():
            problems.append(f"{path.relative_to(REPO).as_posix()}: missing")
        elif _sha256(path) != sha:
            problems.append(f"{path.relative_to(REPO).as_posix()}: SHA-256 {_sha256(path)} differs from {sha}")
    for path in sorted(folder.glob(f"{derivative}*.h")):
        if path.name not in recorded:
            problems.append(f"{path.relative_to(REPO).as_posix()}: not recorded in the rules (origin unknown)")
    return problems


# ----------------------------------------------------------------------------------------------- vendor parsing


def _defines(text: str) -> list[tuple[str, str | None]]:
    """(name, parameter list or None) of every #define of a header, in order."""
    code = s2h._strip_comments(text)
    return [(m.group(1), m.group(2)) for m in re.finditer(r"^[ \t]*#define[ \t]+(\w+)(\([^)]*\))?", code, re.M)]


def _typedef_body(text: str, name: str) -> str:
    """Body text of `typedef struct { ... } <name>` (comments kept)."""
    for m in re.finditer(r"typedef\s+struct\s*\{", text):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        tail = re.match(r"\s*(\w+)", text[i:])
        if tail and tail.group(1) == name:
            return text[m.end():i - 1]
    raise RtdError(f"typedef struct {name} not found in the vendor headers")


def _flat(body: str, counts: dict) -> list[tuple[str, int, int]]:
    flat: list[tuple[str, int, int]] = []
    s2h._parse_struct(s2h._strip_comments(body), 0, "", flat, counts)
    return flat


# ----------------------------------------------------------------------------------------------- rendering


def _comment(text: str) -> str:
    """C block comment, wrapped at 120 columns."""
    lines = textwrap.wrap(" ".join(text.split()), 113)
    if len(lines) == 1:
        return f"/* {lines[0]} */"
    return "/* " + "\n * ".join(lines) + " */"


def effective_macros(text: str) -> set[str]:
    """Macro names defined after reading the header top to bottom (#undef removes a name again)."""
    names: set[str] = set()
    code = s2h._strip_comments(text)
    for m in re.finditer(r"^[ \t]*#(define|undef)[ \t]+(\w+)", code, re.M):
        (names.add if m.group(1) == "define" else names.discard)(m.group(2))
    return names


def _layout_type(module: str, spec: dict, vendor_text: str, counts: dict) -> tuple[list[str], set[str]]:
    """Copy of the vendor layout under the driver name, members replaced; offsets proven against the vendor."""
    vendor_type, new_type = spec["vendor_type"], spec["type"]
    body = _typedef_body(vendor_text, vendor_type)
    original = _flat(body, counts)
    replaced_paths: set[str] = set()
    new_body = body
    for member in spec.get("members") or []:
        pattern = re.compile(member["match"], re.S)
        found = pattern.findall(new_body)
        if len(found) != 1:
            raise RtdError(f"modules.{module}.members {member['match']!r}: {len(found)} matches in {vendor_type}")
        old = pattern.search(new_body).group(0)
        old_flat = _flat(old, counts)
        new_flat = _flat(member["replace"], counts)
        old_size = max(o + s for _, o, s in old_flat)
        new_size = max(o + s for _, o, s in new_flat)
        if old_size != new_size:
            raise RtdError(f"modules.{module}: replacement {member['replace']!r} has {new_size} bytes, "
                           f"the vendor member {old_size}")
        replaced_paths |= {re.sub(r"[\[.].*", "", p) for p, _, _ in old_flat}
        new_body = new_body.replace(old, member["replace"].strip() + "  /* neXtBSW: " +
                                    " ".join(member["why"].split()) + " */", 1)
    vendor_size = max(o + s for _, o, s in original)
    for member in spec.get("append") or []:
        new_body = new_body.rstrip() + "\n  " + member["decl"].strip() + "  /* neXtBSW: " + \
            " ".join(member["why"].split()) + " */\n"
    adapted = _flat(new_body, counts)
    # every vendor member that was not replaced keeps name, offset and size; the type keeps its size unless
    # members are appended behind the vendor layout
    kept = {p: (o, s) for p, o, s in adapted}
    for path, off, size in original:
        if re.sub(r"[\[.].*", "", path) in replaced_paths:
            continue
        if kept.get(path) != (off, size):
            raise RtdError(f"modules.{module}: member {path} moved in {new_type} ({kept.get(path)} != {(off, size)})")
    if not spec.get("append") and max(o + s for _, o, s in adapted) != vendor_size:
        raise RtdError(f"modules.{module}: {new_type} and {vendor_type} differ in size")
    for member in spec.get("append") or []:
        offset = member.get("offset")
        name = re.search(r"(\w+)\s*(?:\[[^\]]*\])?\s*;\s*$", member["decl"].strip()).group(1)
        got = [o for p, o, _ in adapted if re.sub(r"[\[.].*", "", p) == name]
        if offset is None or not got or min(got) != offset or offset < vendor_size:
            raise RtdError(f"modules.{module}: appended member {name} lies at {got and hex(min(got))}, "
                           f"rules say {offset} (vendor layout ends at {vendor_size:#x})")
    # The layout gets the driver name; the vendor type name is redirected to it, so the unchanged vendor instance
    # macros (IP_FLEXCAN0, IP_PTA, GPIO_BASE_PTRS, ...) point at the adapted layout.
    struct_name = new_type if new_type != vendor_type else f"NX_RTD_{new_type}"
    lines = [f"/* {struct_name}: layout of the vendor {vendor_type} under the names the drivers use */",
             "typedef struct {" + new_body.rstrip() + "\n} " + struct_name + ";",
             f"#define {vendor_type} {struct_name}"]
    return lines, {p for p, _, _ in adapted}


def render(rules: dict, derivative: str, rules_file: Path) -> str:
    d = _derivative(rules, derivative)
    folder = vendor_dir(rules)
    vendor_rel = rules["vendor_dir"]
    includes = d["include"]
    vendor_text = "".join(s2h.read_header(folder / name) for name in includes)
    counts = s2h.header_layout(vendor_text)["macros"]
    counts = {k: int(re.fullmatch(r"\(?\s*(0x[0-9A-Fa-f]+|\d+)[uU]?\s*\)?", v).group(1), 0)
              for k, v in counts.items() if re.fullmatch(r"\(?\s*(0x[0-9A-Fa-f]+|\d+)[uU]?\s*\)?", v)}
    origin = rules["origin"]
    o = [
        "/*",
        f" * Adapter header of the {derivative} device header for the neXtBSW/OpenBSW s32k1xx drivers (PLAT-32).",
        f" * Generated by {GENERATOR} from {rules_file.relative_to(REPO).as_posix()} - do not edit.",
        " *",
        f" * Vendor headers: {vendor_rel}/{derivative}*.h, unchanged copies of {origin['repository']}",
        f" * {origin['path']} at commit {origin['commit']} ({origin['license']}),",
        f" * origin and license: {rules['include_dir']}/{vendor_rel}/README.md.",
        " * The register layouts below are copies of those vendor layouts with members renamed, so this file keeps",
        " * their license:",
        " *",
        " * Copyright 1997-2016 Freescale Semiconductor, Inc.",
        " * Copyright 2016-2022 NXP",
        " *",
        " * SPDX-License-Identifier: BSD-3-Clause",
        " */",
        "",
        f"#ifndef NX_RTD_ADAPTER_{derivative}_H",
        f"#define NX_RTD_ADAPTER_{derivative}_H",
        "",
    ]
    # relative to the adapter: found next to it first, whatever include path the build uses
    local = Path(os.path.relpath(folder, adapter_path(rules, derivative).parent)).as_posix()
    o += [f'#include "{local}/{name}"' for name in includes]
    corrections = d.get("core") or {}
    if corrections:
        o += ["", "/* ---- values the vendor header states differently from the derivative ---- */"]
        for name, spec in corrections.items():
            o.append(_comment(spec["source"]))
            o += [f"#undef {name}", f"#define {name} {spec['value']}"]
    absent = d.get("absent") or {}
    if absent:
        o += ["", "/* ---- instances the vendor header describes but the derivative does not have ---- */"]
        for instance, why in absent.items():
            names = [n for n, _ in _defines(vendor_text) if n == f"IP_{instance}" or n.startswith(f"IP_{instance}_")]
            if not names:
                raise RtdError(f"derivatives.{derivative}.absent.{instance}: no IP_{instance} in the vendor headers")
            o.append(_comment(why))
            o += [f"#undef {n}" for n in names]
    defined = {n for n, _ in _defines(vendor_text)}
    compat = d.get("compat") or {}
    if compat:
        o += ["", "/* ---- reserved on this derivative, but written by shared family code (open point each) ---- */"]
        for name, spec in compat.items():
            if name in defined:
                raise RtdError(f"derivatives.{derivative}.compat.{name}: the vendor header defines it (remove)")
            o += [_comment(spec["why"]), f"#define {name} {spec['value']}"]
            defined.add(name)
    for module, spec in (rules.get("modules") or {}).items():
        file = folder / f"{derivative}_{spec.get('file', module)}.h"
        if not file.is_file():
            continue                            # module not on this derivative
        text = file.read_text(encoding="utf-8")
        o += ["", _comment(f"---- {module}: {spec['why']}")]
        if "type" in spec:
            lines, _ = _layout_type(module, spec, text, counts)
            o += lines
        aliases = []
        for alias in spec.get("macro_prefix") or []:
            vendor_prefix, driver_prefix = alias["vendor"], alias["driver"]
            for name, params in _defines(text):
                if not name.startswith(vendor_prefix):
                    continue
                new = driver_prefix + name[len(vendor_prefix):]
                if new in defined:
                    continue                    # the vendor header already has the driver name
                if params is None:
                    aliases.append(f"#define {new} {name}")
                else:
                    aliases.append(f"#define {new}{params} {name}{params}")
                defined.add(new)
        o += aliases
    o += ["", f"#endif /* NX_RTD_ADAPTER_{derivative}_H */", ""]
    return "\n".join(o)


# ----------------------------------------------------------------------------------------------- symbols

# the symbol scan of svd2header covers the whole bootloader; ports of other families are not s32k1xx users
OTHER_FAMILY = re.compile(r"^bootloader/platforms/(?!s32k1xx/)")


def symbol_report(rules: dict, derivative: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """(symbol, status, first file) of every register symbol the drivers use, and unused absent patterns.

    status: ok | remap (short name from mcu.h) | absent: <reason> | MISSING
    """
    d = _derivative(rules, derivative)
    header = adapter_path(rules, derivative)
    reference = s2h.read_header(REPO / s2h.OPENBSW_S32K148)
    used = s2h.used_symbols(reference)
    text = s2h.read_header(header)
    layout = s2h.header_layout(text)
    macros = effective_macros(text)
    members = set()
    for flat in layout["types"].values():
        for path, _, _ in flat:
            members |= {re.sub(r"\[\d+\]", "", part) for part in path.split(".")}
    expected = [(re.compile(p["match"]), p["why"]) for p in d.get("not_on_derivative") or []]
    hit = [False] * len(expected)
    report = []
    for sym, files in used.items():
        files = [f for f in files if not OTHER_FAMILY.match(f)]
        if not files:
            continue
        if sym.startswith("."):
            ok = sym[1:] in members
        elif sym in s2h.REMAP:
            report.append((sym, "remap", files[0]))
            continue
        else:
            ok = sym in macros or sym in layout["irqs"] or sym in layout["types"]
        status = "ok"
        if not ok:
            status = "MISSING"
            for i, (pattern, why) in enumerate(expected):
                if pattern.fullmatch(sym):
                    status, hit[i] = f"absent: {' '.join(why.split())}", True
                    break
        report.append((sym, status, files[0]))
    stale = [p.pattern for (p, _), h in zip(expected, hit) if not h]
    return report, stale


# ----------------------------------------------------------------------------------------------- command line


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "check", "symbols"):
        p = sub.add_parser(name)
        p.add_argument("--derivative", required=True)
        p.add_argument("--family", default="s32k1xx")
        p.add_argument("--rules", help="rules file (default mcudb/tools/device_header_rules/<family>_rtd.yaml)")
        if name == "generate":
            p.add_argument("--out", help="output file (default: the adapter path of the rules)")
    args = parser.parse_args(argv)
    rules_file = Path(args.rules) if args.rules else rules_path(args.family)
    try:
        rules = load_rules(rules_file)
        if args.command == "symbols":
            report, stale = symbol_report(rules, args.derivative)
            for sym, status, file in report:
                print(f"{sym}\t{status}\t{file}")
            missing = sum(status == "MISSING" for _, status, _ in report)
            print(f"# {len(report)} symbols, {missing} missing, stale absent patterns: {stale}", file=sys.stderr)
            return 1 if missing or stale else 0
        problems = verify_vendor_files(rules, args.derivative)
        if problems:
            raise RtdError("vendor headers:\n  " + "\n  ".join(problems))
        text = render(rules, args.derivative, rules_file)
        target = adapter_path(rules, args.derivative)
        if args.command == "generate":
            out = Path(args.out) if args.out else target
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8", newline="\n")
            print(f"{out} ({len(text)} bytes)")
            return 0
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            problems.append(f"{target.relative_to(REPO).as_posix()} missing or outdated (run generate)")
        report, stale = symbol_report(rules, args.derivative)
        problems += [f"driver symbol {sym} ({file}) does not resolve" for sym, status, file in report
                     if status == "MISSING"]
        problems += [f"not_on_derivative pattern {p!r} matches no missing symbol (remove it)" for p in stale]
        for p in problems:
            print(p, file=sys.stderr)
        return 1 if problems else 0
    except (RtdError, s2h.SvdError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
