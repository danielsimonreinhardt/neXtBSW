# SPDX-License-Identifier: Apache-2.0
"""calgen - calibration datasets of a project (TOOL-145, stage K2 of doc/architecture/kalibrierung.md).

The values of the calibration parameters live in a dataset folder (format nextbsw-cal 1, tools/calgen/caldata.py),
normally projects/<p>/calibration/ next to project.yaml. The definitions (kind, type, axes, limits, safety class)
stay in swc.yaml; the defaults of a component type stay in <Swc>_data.m. This tool reads the software the way
tools/rtegen does (same loader, same conversion, same checks) and works on the dataset without MATLAB or Qt:

    calgen.py migrate <target> [--author A] [--status S]      create the dataset from the <Swc>_data.m defaults;
                                                               on an existing dataset add only missing values
    calgen.py check   <target> [--variant V | --all] [--release] [--build DIR]
                                                               complete, type/grid/limits/ascending axes as in the
                                                               build, layout identifier, status (release: every
                                                               safety-relevant value released)
    calgen.py rebind  <target>                                 bind the dataset to the layout of this software
    calgen.py resolve <target> --variant V [-o DIR]            base + delta as one complete dataset
    calgen.py diff    <A>[@V] <B>[@V]                          compare two datasets (folder, project.yaml, rte.yaml)
    calgen.py export  <target> [--variant V] --format dcm|cdfx -o FILE
    calgen.py import  <target> FILE [--variant V] [--author A] [--reason R]
    calgen.py values  <target> [--variant V] [--json] [-o FILE]   resolved values (MATLAB nx_cal_load, segment)

<target> is a project.yaml (dataset: <project dir>/calibration, ECU description: project.rte) or an ECU description
rte.yaml (dataset next to it, for test profiles without a project); --dataset DIR names another folder.
The build (tools/rtegen) takes the values of the dataset instead of the defaults when the project has the folder,
and refuses a dataset with an error before it generates. Release of a dataset: an annotated Git tag, see README.md.
"""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "rtegen"))

import caldata  # noqa: E402
import exchange  # noqa: E402
import rtegen  # noqa: E402

Finding = caldata.Finding


class CalgenError(Exception):
    """A request that cannot be done; carries the findings when there are several."""

    def __init__(self, text: str, findings: list | None = None):
        self.findings = findings or []
        super().__init__(text)


@dataclass
class Software:
    """The calibration objects of one ECU software, loaded as tools/rtegen loads them (defaults of <Swc>_data.m)."""
    ecu_file: Path
    dataset_dir: Path
    name: str
    components: dict
    layout: str

    def parameters(self):
        for comp in self.components.values():
            for p in comp.parameters.values():
                yield comp, p

    def fresh(self) -> dict:
        return copy.deepcopy(self.components)


def open_target(target: Path, dataset: Path | None = None) -> Software:
    """Software and dataset folder of a project.yaml or an ECU description."""
    target = Path(target).resolve()
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise CalgenError(f"{target}: {e}") from None
    if isinstance(data, dict) and isinstance(data.get("project"), dict):
        rte = data["project"].get("rte")
        if not rte:
            raise CalgenError(f"{target}: project.rte (ECU description) is not set")
        ecu_file = (target.parent / rte).resolve()
        name = str(data["project"].get("name") or target.parent.name)
    else:
        ecu_file, name = target, target.parent.name
    ecu = rtegen.load_ecu(ecu_file)
    components = {}
    for entry in ecu.components:
        loaded = rtegen.load_component(entry.name, rtegen.path_of(ecu, ecu_file, REPO, entry.path), REPO)
        components[loaded.component.name] = loaded.component
    folder = Path(dataset).resolve() if dataset else target.parent / caldata.DATASET_DIR
    return Software(ecu_file, folder, name, components, rtegen.calibration_layout_id(components))


def load_dataset(sw: Software) -> caldata.Dataset:
    return caldata.load(sw.dataset_dir)


# ---- shapes ------------------------------------------------------------------------------------------------------

def shaped(p, flat: list, texts: bool = True):
    """Flat physical values as a dataset value: scalar, list, or rows of a map (one per X breakpoint)."""
    values = []
    for v in flat:
        if texts and p.value_table and isinstance(v, (int, float)) and float(v).is_integer():
            v = p.value_table.get(int(v), v)
        values.append(caldata.normalise(v))
    if len(p.shape) == 2:
        nx, ny = p.shape
        return [values[i * ny:(i + 1) * ny] for i in range(nx)]
    return values[0] if p.dim == 1 else values


def canonical(p, value):
    """Stored values of a dataset value (texts of the value table resolved), None if it cannot be converted."""
    try:
        phys = rtegen.dataset_values(p, caldata.Entry(value))
        return [rtegen._to_raw(p, v, p.name, "") for v in phys]
    except rtegen.RteError:
        return None


def row_labels(sw: Software, resolved: caldata.Part) -> dict:
    """Comment per map row: the X breakpoint it belongs to (component -> element -> labels)."""
    labels = {}
    for comp, p in sw.parameters():
        if len(p.shape) != 2 or not p.axes:
            continue
        axis = p.axes[0]
        entry = resolved.entry(comp.name, axis.element)
        xs = entry.value if entry is not None and isinstance(entry.value, list) else axis.phys
        labels.setdefault(comp.name, {})[p.element] = [
            f"{axis.element} = {caldata.format_number(x)}" + (f" {axis.unit}" if axis.unit else "") for x in xs]
    return labels


def ordered(sw: Software, part: caldata.Part):
    """Entries of each component in the order of its parameters (unknown ones after them)."""
    for comp in sw.components.values():
        entries = part.components.get(comp.name)
        if entries is None:
            continue
        order = [p.element for p in comp.parameters.values()]
        part.components[comp.name] = {e: entries[e] for e in sorted(entries, key=lambda e: (
            order.index(e) if e in order else len(order), e))}


# ---- migrate, check, rebind --------------------------------------------------------------------------------------

def migrate(sw: Software, author: str = "", status: str = "draft", reason: str = "") -> list[str]:
    """Creates the dataset from the defaults, or adds the values an existing dataset misses; returns what it did."""
    root = sw.dataset_dir
    exists = (root / caldata.INDEX_FILE).is_file()
    dataset = caldata.load(root) if exists else caldata.Dataset(root, sw.layout)
    added = []
    for comp, p in sw.parameters():
        entries = dataset.base.components.setdefault(comp.name, {})
        if p.element in entries:
            continue
        entries[p.element] = caldata.Entry(shaped(p, p.phys), status, author,
                                           reason or f"default of the component type ({comp.name}_data.m)")
        added.append(p.name)
    ordered(sw, dataset.base)
    if not exists:
        caldata.write_index(root, sw.layout)
    caldata.write_part(root, dataset.base, row_labels(sw, dataset.resolve(caldata.BASE)))
    report = [f"{'added' if exists else 'created'} {len(added)} value(s) in {root.as_posix()}/base"]
    if exists and dataset.layout != sw.layout:
        report.append(f"the dataset is bound to layout {dataset.layout}, the software has {sw.layout}: check, then "
                      f"calgen rebind")
    return report + [f"  + {name}" for name in added]


def value_findings(sw: Software, resolved: caldata.Part) -> tuple[list[Finding], dict]:
    """The build's checks of every value (rtegen.apply_calibration_dataset); returns findings and the components."""
    comps = sw.fresh()
    return [Finding("error", text) for text in rtegen.apply_calibration_dataset(comps, resolved)], comps


def build_layout(build: Path) -> str:
    """Layout identifier of a build: RteCalibration.json, or a build directory that contains it."""
    build = Path(build)
    if build.is_dir():
        found = sorted(build.rglob(rtegen.CALIBRATION_FILE))
        if not found:
            raise CalgenError(f"{build}: no {rtegen.CALIBRATION_FILE} (tools/rtegen writes it into generated/rte)")
        build = found[0]
    try:
        return json.loads(build.read_text(encoding="utf-8"))["layout"]
    except (OSError, ValueError, KeyError) as e:
        raise CalgenError(f"{build}: no layout identifier ({e})") from None


def check(sw: Software, dataset: caldata.Dataset, name: str = caldata.BASE, release: bool = False,
          build: Path | None = None) -> list[Finding]:
    """Everything the build refuses, plus the layout of a given build and the status rules of a release."""
    resolved = dataset.resolve(name)
    findings, comps = value_findings(sw, resolved)
    if dataset.layout != sw.layout:
        findings.insert(0, Finding("error", f"the dataset is bound to layout {dataset.layout or '-'}, this software "
                                            f"has layout {sw.layout} (check the values, then calgen rebind)",
                                   dataset.index_file))
    if build is not None:
        built = build_layout(build)
        if built != dataset.layout:
            findings.append(Finding("error", f"the build has layout {built}, the dataset is bound to "
                                             f"{dataset.layout or '-'}", Path(build)))
    for comp in comps.values():
        for p in comp.parameters.values():
            entry = resolved.entry(comp.name, p.element)
            if entry is None or entry.status == "released":
                continue
            if release and p.safety != "qm":
                findings.append(Finding("error", f"{p.name}: safety class {p.safety}, status {entry.status}; a "
                                                 f"release needs status released", entry.file, entry.line))
            elif release:
                findings.append(Finding("warning", f"{p.name}: status {entry.status} in a release", entry.file,
                                        entry.line))
    if name != caldata.BASE:
        for comp, entries in dataset.part(name).components.items():
            for element, entry in entries.items():
                base = dataset.base.entry(comp, element)
                if base is not None and base.same_value(entry):
                    findings.append(Finding("warning", f"{comp}.{element}: same value as base - not a delta, "
                                                       f"remove it from the variant", entry.file, entry.line))
    return findings


def status_summary(resolved: caldata.Part) -> str:
    counts = {s: 0 for s in caldata.STATUSES}
    for entries in resolved.components.values():
        for entry in entries.values():
            counts[entry.status] += 1
    return ", ".join(f"{n} {s}" for s, n in counts.items())


def rebind(sw: Software) -> str:
    """Binds the dataset to this software's layout, after base and every variant pass the value checks."""
    dataset = load_dataset(sw)
    findings = []
    for name in dataset.names():
        findings += [f for f in value_findings(sw, dataset.resolve(name))[0]]
    if findings:
        raise CalgenError("the values do not fit this software, nothing rebound", findings)
    old = dataset.layout
    caldata.write_index(sw.dataset_dir, sw.layout, dataset.build_variant)
    return f"layout {old or '-'} -> {sw.layout}" if old != sw.layout else f"layout {sw.layout} unchanged"


# ---- resolve, diff, values ---------------------------------------------------------------------------------------

def resolve_to(sw: Software, name: str, out: Path) -> list[Path]:
    """Writes base + delta of `name` as a complete dataset (index + base) into `out`."""
    dataset = load_dataset(sw)
    resolved = dataset.resolve(name)
    part = caldata.Part(caldata.BASE, {c: dict(e) for c, e in resolved.components.items()})
    caldata.write_index(out, dataset.layout)
    return caldata.write_part(out, part, row_labels(sw, resolved))


def dataset_spec(spec: str) -> tuple[Path, str]:
    """`<folder|project.yaml|rte.yaml>[@variant]` -> dataset folder and variant."""
    path, _, variant = spec.rpartition("@") if "@" in spec else (spec, "", "")
    folder = Path(path)
    if folder.is_file():
        folder = folder.parent / caldata.DATASET_DIR
    return folder, variant or caldata.BASE


def diff_specs(a: str, b: str) -> list[caldata.Change]:
    (fa, va), (fb, vb) = dataset_spec(a), dataset_spec(b)
    return caldata.diff(caldata.load(fa).resolve(va), caldata.load(fb).resolve(vb))


def values_of(sw: Software, name: str) -> dict:
    """Resolved values of base or a variant, checked, flat in memory order with stored values (MATLAB, K3)."""
    dataset = load_dataset(sw)
    resolved = dataset.resolve(name)
    findings, comps = value_findings(sw, resolved)
    if findings:
        raise CalgenError(f"the dataset ({name}) does not fit this software", findings)
    result = {"format": caldata.FORMAT, "layout": dataset.layout, "dataset": name,
              "values_id": rtegen.calibration_values_id(comps), "components": {}}
    for comp in comps.values():
        result["components"][comp.name] = {
            p.element: {"name": p.name, "value": [caldata.normalise(v) for v in p.phys],
                        "shape": list(p.shape) or ([p.dim] if p.dim > 1 else []),
                        "stored": [caldata.normalise(v) for v in p.raw]}
            for p in comp.parameters.values()}
    return result


# ---- export, import ----------------------------------------------------------------------------------------------

def objects(sw: Software, resolved: caldata.Part) -> list[exchange.CalObject]:
    findings, comps = value_findings(sw, resolved)
    if findings:
        raise CalgenError(f"the dataset ({resolved.name}) does not fit this software; nothing exported", findings)
    result = []
    for comp in comps.values():
        for p in comp.parameters.values():
            if p.own_of:
                continue                              # part of its curve/map
            entry = resolved.entry(comp.name, p.element)
            kind = p.kind or ("value" if p.dim == 1 else "array")
            shape = tuple(p.shape) or ((p.dim,) if p.dim > 1 else ())
            axes = [exchange.AxisPart(a.element, "" if a.own_of else a.name, a.unit, list(a.phys)) for a in p.axes]
            result.append(exchange.CalObject(p.name, comp.name, p.element, kind, list(p.phys), shape, p.unit,
                                             p.description, dict(p.value_table), axes, entry.status, entry.author,
                                             entry.reason))
    return result


def export(sw: Software, name: str, fmt: str, out: Path, date: str | None = None) -> str:
    dataset = load_dataset(sw)
    objs = objects(sw, dataset.resolve(name))
    if fmt == "dcm":
        text = exchange.write_dcm(objs, [f"ECU software {sw.name}, dataset {name}, layout {dataset.layout}",
                                         "values physical; status/author/reason as comments (not read back)"])
    else:
        text = exchange.write_cdfx(objs, sw.name, name, date or datetime.date.today().isoformat())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    return f"{len(objs)} object(s) of {name} -> {out.as_posix()} ({fmt.upper()})"


def read_exchange(file: Path) -> tuple[list[exchange.Imported], list[str]]:
    text = file.read_text(encoding="utf-8", errors="replace")
    suffix = file.suffix.lower()
    if suffix == ".dcm":
        return exchange.read_dcm(text, file.as_posix())
    if suffix in (".cdfx", ".cdf", ".xml"):
        return exchange.read_cdfx(text, file.as_posix())
    raise CalgenError(f"{file}: unknown format (.dcm, .cdfx)")


def import_file(sw: Software, file: Path, name: str = caldata.BASE, author: str = "", reason: str = "",
                write: bool = True) -> tuple[list[str], list[str]]:
    """Takes the values of a DCM/CDFX file into base or a variant; returns (changes, warnings).

    Only changed values are written: status from the file (CDFX) or draft (DCM, and a CDFX without state), author
    and reason from the file or the arguments. Into a variant only a value that differs from base is written; a
    value back at the base value leaves the delta. Unknown objects, wrong kinds or sizes and values the build
    would refuse are errors, and then nothing is written.
    """
    imported, warnings = read_exchange(file)
    dataset = load_dataset(sw)
    target = dataset.part(name)
    resolved = dataset.resolve(name)
    by_name = {p.name: (comp, p) for comp, p in sw.parameters() if not p.own_of}
    errors, updates = [], []
    for o in imported:
        hit = by_name.get(o.name)
        if hit is None:
            errors.append(Finding("error", f"{o.where}: {o.name} is no calibration object of {sw.name}"))
            continue
        comp, p = hit
        kind = p.kind or ("value" if p.dim == 1 else "array")
        if o.kind != kind:
            errors.append(Finding("error", f"{o.where}: {o.name} is a {kind} in the software, the file has a "
                                           f"{o.kind}"))
            continue
        if len(o.values) != p.dim or (len(p.shape) == 2 and tuple(o.shape) != tuple(p.shape)):
            errors.append(Finding("error", f"{o.where}: {o.name} has {len(o.values)} value(s) "
                                           f"{'x'.join(map(str, o.shape))}, the software "
                                           f"{'x'.join(map(str, p.shape)) or p.dim}"))
            continue
        updates.append((comp, p, shaped(p, o.values, texts=False), o))
        for axis, values, ref in zip(p.axes, o.axes, o.axis_refs or [""] * len(o.axes)):
            if axis.own_of:
                if values is None or len(values) != axis.dim:
                    errors.append(Finding("error", f"{o.where}: {o.name}: own breakpoints {axis.element} need "
                                                   f"{axis.dim} values"))
                else:
                    updates.append((comp, axis, shaped(axis, values), o))
            elif values:
                current = resolved.entry(comp.name, axis.element)
                if current is None or canonical(axis, current.value) != canonical(axis, values):
                    warnings.append(f"{o.where}: breakpoints of the shared axis {axis.name} differ from the "
                                    f"dataset; the axis object itself counts")
    if errors:
        raise CalgenError(f"{file.name}: not imported", errors)
    changes = []
    for comp, p, value, o in updates:
        current = resolved.entry(comp.name, p.element)
        if current is not None and canonical(p, current.value) == canonical(p, value):
            continue
        entry = caldata.Entry(value, o.status or "draft", o.author or author, o.reason or reason
                              or f"imported from {file.name}")
        entries = target.components.setdefault(comp.name, {})
        base = dataset.base.entry(comp.name, p.element)
        if name != caldata.BASE and base is not None and canonical(p, base.value) == canonical(p, value):
            entries.pop(p.element, None)
            changes.append(f"{p.name}: back to the base value")
        else:
            entries[p.element] = entry
            changes.append(f"{p.name}: {caldata._show(current.value) if current else '-'} -> {caldata._show(value)}")
    after = dataset.resolve(name)
    findings = value_findings(sw, after)[0]
    if findings:
        raise CalgenError(f"{file.name}: the imported values do not pass the checks; nothing written", findings)
    if write and changes:
        ordered(sw, target)
        caldata.write_part(sw.dataset_dir, target, row_labels(sw, after))
    return changes, warnings


# ---- command line ------------------------------------------------------------------------------------------------

def _variants(dataset: caldata.Dataset, args) -> list[str]:
    if getattr(args, "all", False):
        return dataset.names()
    return [args.variant or caldata.BASE]


def _print_findings(findings: list[Finding]) -> bool:
    cwd = Path.cwd().resolve()
    for f in findings:
        if f.file is not None and f.file.is_absolute() and f.file.is_relative_to(cwd):
            f = Finding(f.severity, f.text, f.file.relative_to(cwd), f.line)
        print(f"calgen: {f}", file=sys.stderr)
    return any(f.severity == "error" for f in findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def target(p):
        p.add_argument("target", type=Path, help="project.yaml or ECU description (rte.yaml)")
        p.add_argument("--dataset", type=Path, help="dataset folder (default: calibration/ next to the target)")

    p = sub.add_parser("migrate", help="create the dataset from the <Swc>_data.m defaults, or add missing values")
    target(p)
    p.add_argument("--author", default="")
    p.add_argument("--status", default="draft", choices=caldata.STATUSES)
    p.add_argument("--reason", default="")
    p = sub.add_parser("check", help="check the dataset as the build does, plus layout and status")
    target(p)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--variant", default="")
    group.add_argument("--all", action="store_true", help="base and every variant")
    p.add_argument("--release", action="store_true", help="safety-relevant values must be released")
    p.add_argument("--build", type=Path, help="build directory or RteCalibration.json to compare the layout with")
    p = sub.add_parser("rebind", help="bind the dataset to the layout of this software")
    target(p)
    p = sub.add_parser("resolve", help="base + delta of a variant as one complete dataset")
    target(p)
    p.add_argument("--variant", required=True)
    p.add_argument("-o", "--out", type=Path, help="folder for the complete dataset (default: print)")
    p = sub.add_parser("diff", help="compare two datasets: <folder|project.yaml|rte.yaml>[@variant]")
    p.add_argument("a")
    p.add_argument("b")
    p = sub.add_parser("export", help="write DCM or CDFX")
    target(p)
    p.add_argument("--variant", default="")
    p.add_argument("--format", required=True, choices=("dcm", "cdfx"))
    p.add_argument("-o", "--out", type=Path, required=True)
    p.add_argument("--date", help="CDFX: date of the CS-ENTRY (default: today)")
    p = sub.add_parser("import", help="read DCM or CDFX into base or a variant")
    target(p)
    p.add_argument("file", type=Path)
    p.add_argument("--variant", default="")
    p.add_argument("--author", default="")
    p.add_argument("--reason", default="")
    p.add_argument("--dry-run", action="store_true", help="only report what would change")
    p = sub.add_parser("values", help="resolved and checked values (JSON for MATLAB nx_cal_load and the segment)")
    target(p)
    p.add_argument("--variant", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("-o", "--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "diff":
            changes = diff_specs(args.a, args.b)
            for c in changes:
                print(c)
            print(f"calgen: {len(changes)} difference(s)", file=sys.stderr)
            return 0
        sw = open_target(args.target, args.dataset)
        if args.command == "migrate":
            for line in migrate(sw, args.author, args.status, args.reason):
                print(line)
            return 0
        if args.command == "rebind":
            print(rebind(sw))
            return 0
        dataset = load_dataset(sw)
        variant = getattr(args, "variant", "") or ""
        if args.command == "check":
            failed = False
            for name in _variants(dataset, args):
                findings = check(sw, dataset, name, args.release, args.build)
                failed |= _print_findings(findings)
                errors = sum(f.severity == "error" for f in findings)
                print(f"calgen: {name}: {dataset.resolve(name).count()} value(s) "
                      f"({status_summary(dataset.resolve(name))}), layout {dataset.layout}, {errors} error(s), "
                      f"{len(findings) - errors} warning(s)")
            return 1 if failed else 0
        if args.command == "resolve":
            if args.out:
                written = resolve_to(sw, args.variant, args.out)
                print(f"calgen: {args.variant} -> {args.out.as_posix()} ({len(written)} file(s) written)")
            else:
                resolved = dataset.resolve(args.variant)
                labels = row_labels(sw, resolved)
                for comp, entries in resolved.components.items():
                    print(f"---\n{caldata.render_component(comp, entries, caldata.BASE, labels.get(comp))}", end="")
            return 0
        if args.command == "export":
            print(export(sw, variant or caldata.BASE, args.format, args.out, args.date))
            return 0
        if args.command == "import":
            changes, warnings = import_file(sw, args.file, variant or caldata.BASE, args.author, args.reason,
                                            write=not args.dry_run)
            for w in warnings:
                print(f"calgen: warning: {w}", file=sys.stderr)
            for c in changes:
                print(f"  {c}")
            print(f"calgen: {len(changes)} value(s) {'would change' if args.dry_run else 'changed'} in "
                  f"{variant or caldata.BASE}")
            return 0
        if args.command == "values":
            result = values_of(sw, variant or dataset.build_variant or caldata.BASE)
            if args.json:
                text = json.dumps(result, indent=1, ensure_ascii=False) + "\n"
            else:
                text = "".join(f"{v['name']} = {caldata._show(v['value'] if len(v['value']) > 1 else v['value'][0])}\n"
                               for comp in result["components"].values() for v in comp.values())
            if args.out:
                args.out.write_text(text, encoding="utf-8", newline="\n")
            else:
                sys.stdout.write(text)
            return 0
    except CalgenError as e:
        _print_findings(e.findings)
        print(f"calgen: error: {e}", file=sys.stderr)
        return 1
    except caldata.CalDataError as e:
        _print_findings(e.findings)
        return 1
    except (rtegen.RteError, exchange.ExchangeError, OSError) as e:
        print(f"calgen: error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
