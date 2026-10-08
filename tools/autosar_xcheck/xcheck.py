# SPDX-License-Identifier: Apache-2.0
"""Cross-check of neXtBSW parameters against the AUTOSAR ECUC parameter definitions (ARCH-68, proposals A to C of
doc/architecture/autosar-ecuc-evaluation.md section 6).

The AUTOSAR ECUC parameter definitions (one ARXML file inside the release archive
AUTOSAR_CP_MOD_ECUConfigurationParameters.zip) are never part of the repository. The path of the ZIP comes from the
environment variable `NXCONFIG_AUTOSAR_ECUC_ZIP` (or `--zip`; `--arxml` takes an unpacked file). Without it the
script says so and exits with 2; the pytest of this folder works with a small synthetic file instead.

For every entry of `map.yaml` the script checks that
  - the AUTOSAR module, container and parameter still exist under that name (a miss means a renamed or removed
    parameter in a new release - exit code 1), and
  - our limit lies inside the AUTOSAR limit (our values come from the pydantic model of tools/nxconfig, scaled to
    the AUTOSAR unit; `OUTSIDE` - exit code 1).
Further results are informational: `OK` equal limits, `NARROWER` ours is narrower, `UNBOUNDED` ours has no upper
limit where AUTOSAR has one, `KNOWN` an accepted difference of the map. The report holds names and numbers only;
description texts of the file are never read or printed.

Run:  NXCONFIG_AUTOSAR_ECUC_ZIP=<path>/AUTOSAR_CP_MOD_ECUConfigurationParameters.zip python -m autosar_xcheck
      (from tools/)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import typing
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Optional

HERE = Path(__file__).resolve().parent
MAP_FILE = HERE / "map.yaml"
ENV_ZIP = "NXCONFIG_AUTOSAR_ECUC_ZIP"

NS = "{http://autosar.org/schema/r4.0}"
CONTAINER_TAGS = (NS + "ECUC-PARAM-CONF-CONTAINER-DEF", NS + "ECUC-CHOICE-CONTAINER-DEF")
FAIL = ("MISSING", "OUTSIDE")


@dataclass(frozen=True)
class Result:
    status: str          # OK, NARROWER, UNBOUNDED, KNOWN, MISSING, OUTSIDE
    entry: dict
    detail: str

    @property
    def failed(self) -> bool:
        return self.status in FAIL


# ------------------------------------------------------------------------------------------------ ECUC file


def _name(element) -> Optional[str]:
    short = element.find(NS + "SHORT-NAME")
    return short.text if short is not None else None


def _limit(element, tag: str) -> Optional[float]:
    """MIN or MAX of a parameter definition; absent or INF is unbounded (None)."""
    node = element.find(NS + tag)
    if node is None or node.text is None:
        return None
    text = node.text.strip()
    if text.upper() in ("INF", "-INF"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


class Ecuc:
    """Index of the definitions: module -> container -> parameter -> (min, max). Names and limits only."""

    def __init__(self, stream: IO[bytes]):
        self.modules: dict[str, dict[str, dict[str, tuple[Optional[float], Optional[float]]]]] = {}
        root = ET.parse(stream).getroot()
        for module in root.iter(NS + "ECUC-MODULE-DEF"):
            containers: dict[str, dict[str, tuple[Optional[float], Optional[float]]]] = {}
            for container in module.iter():
                if container.tag not in CONTAINER_TAGS:
                    continue
                params = containers.setdefault(_name(container) or "", {})
                for group in container:
                    if group.tag in (NS + "PARAMETERS", NS + "REFERENCES"):
                        for param in group:
                            params[_name(param) or ""] = (_limit(param, "MIN"), _limit(param, "MAX"))
            self.modules[_name(module) or ""] = containers

    def find(self, module: str, container: str, parameter: str):
        """(found, message, (min, max)); the message names the level that is missing."""
        if module not in self.modules:
            return False, f"module {module} does not exist", (None, None)
        if container not in self.modules[module]:
            return False, f"container {module}.{container} does not exist", (None, None)
        params = self.modules[module][container]
        if parameter not in params:
            return False, f"parameter {module}.{container}.{parameter} does not exist", (None, None)
        return True, "", params[parameter]


def open_ecuc(zip_path: Optional[str] = None, arxml_path: Optional[str] = None) -> tuple[Ecuc, str]:
    """Reads the ARXML from a release ZIP or an unpacked file; returns the index and a source line (name and
    SHA-256 of what was read, for the report)."""
    if arxml_path:
        data = Path(arxml_path)
        with data.open("rb") as stream:
            ecuc = Ecuc(stream)
        return ecuc, f"{data.name} sha256 {hashlib.sha256(data.read_bytes()).hexdigest()}"
    assert zip_path
    path = Path(zip_path)
    with zipfile.ZipFile(path) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith(".arxml")]
        if len(members) != 1:
            raise ValueError(f"{path.name}: expected exactly one .arxml file in the archive, found "
                             f"{len(members)}")
        with archive.open(members[0]) as stream:
            ecuc = Ecuc(stream)
    return ecuc, f"{path.name} sha256 {hashlib.sha256(path.read_bytes()).hexdigest()}"


# ------------------------------------------------------------------------------------------------ our side


def load_map(path: Path = MAP_FILE) -> list[dict]:
    from ruamel.yaml import YAML
    with path.open(encoding="utf-8") as stream:
        return list(YAML(typ="safe").load(stream)["entries"])


def _constraints(metadata, found: dict) -> None:
    for item in metadata:
        if hasattr(item, "metadata") and not hasattr(item, "ge"):      # FieldInfo of an Annotated
            _constraints(item.metadata, found)
        for key in ("ge", "gt", "le", "lt"):
            value = getattr(item, key, None)
            if value is not None:
                found[key] = value


def model_limits(spec: str) -> tuple[Optional[float], Optional[float]]:
    """(min, max) of `Class.field` in the nxconfig model, in our unit; None = no limit. A strict bound of an
    integer counts as the next integer."""
    sys.path.insert(0, str(HERE.parent))
    try:
        from nxconfig import model
    finally:
        sys.path.pop(0)
    class_name, field_name = spec.split(".")
    info = getattr(model, class_name).model_fields[field_name]
    found: dict = {}
    _constraints(info.metadata, found)

    def walk(annotation):
        for arg in typing.get_args(annotation):
            if hasattr(arg, "__metadata__"):
                _constraints(arg.__metadata__, found)
            walk(arg)
    walk(info.annotation)
    low = found.get("ge", found["gt"] + 1 if "gt" in found else None)
    high = found.get("le", found["lt"] - 1 if "lt" in found else None)
    return low, high


def our_limits(entry: dict) -> tuple[Optional[float], Optional[float]]:
    """Limits of an entry in AUTOSAR units: the explicit `ours` of the map or the model's, times `scale`."""
    if "ours" in entry:
        low, high = entry["ours"]
        return low, high
    low, high = model_limits(entry["nxconfig"])
    scale = entry.get("scale", 1)
    return (None if low is None else round(low * scale, 9)), (None if high is None else round(high * scale, 9))


# ------------------------------------------------------------------------------------------------ comparison


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return str(int(value)) if float(value).is_integer() else repr(value)


def compare(entry: dict, ecuc: Ecuc) -> Result:
    found, message, (a_low, a_high) = ecuc.find(entry["module"], entry["container"], entry["parameter"])
    if not found:
        return Result("MISSING", entry, message)
    low, high = our_limits(entry)
    span = f"ours [{_fmt(low)}, {_fmt(high)}] AUTOSAR [{_fmt(a_low)}, {_fmt(a_high)}]"
    below = low is not None and a_low is not None and low < a_low
    above = high is not None and a_high is not None and high > a_high
    unbounded = high is None and a_high is not None
    unbounded_low = low is None and a_low is not None
    if entry.get("known") and (below or above or unbounded or unbounded_low):
        return Result("KNOWN", entry, f"{span}: {entry['known']}")
    if below or above:
        return Result("OUTSIDE", entry, span)
    if unbounded or unbounded_low:
        return Result("UNBOUNDED", entry, span)
    if low == a_low and high == a_high:
        return Result("OK", entry, span)
    return Result("NARROWER", entry, span)


def run(ecuc: Ecuc, entries: list[dict]) -> list[Result]:
    return [compare(entry, ecuc) for entry in entries]


def report(results: list[Result], source: str) -> str:
    lines = [f"AUTOSAR ECUC cross-check, source {source}"]
    for result in results:
        entry = result.entry
        name = f"{entry['module']}.{entry['parameter']}"
        lines.append(f"[{result.status}] {entry['yaml']} <-> {name}: {result.detail}")
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    lines.append("summary: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="autosar_xcheck", description=__doc__.split("\n\n")[0])
    parser.add_argument("--zip", default=os.environ.get(ENV_ZIP), help=f"release ZIP (default: ${ENV_ZIP})")
    parser.add_argument("--arxml", help="unpacked ECUC ARXML instead of the ZIP")
    parser.add_argument("--map", default=str(MAP_FILE), help="mapping file (default: map.yaml next to the script)")
    args = parser.parse_args(argv)
    if not args.zip and not args.arxml:
        print(f"autosar_xcheck: no ECUC file - set {ENV_ZIP} to the path of "
              "AUTOSAR_CP_MOD_ECUConfigurationParameters.zip (never stored in the repository)", file=sys.stderr)
        return 2
    try:
        ecuc, source = open_ecuc(args.zip, args.arxml)
        entries = load_map(Path(args.map))
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile) as error:
        print(f"autosar_xcheck: {error}", file=sys.stderr)
        return 2
    results = run(ecuc, entries)
    print(report(results, source))
    return 1 if any(r.failed for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
