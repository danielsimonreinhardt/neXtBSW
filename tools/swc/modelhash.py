# SPDX-License-Identifier: Apache-2.0
"""Reads generated/model.sha256 of a Simulink component: which inputs of the last code generation changed (TOOL-169).

tools/swc/swc_build.m writes one line per input file the generated code depends on, in the format of sha256sum:

    <sha256>  <path relative to the component folder, '/' separated, '../' allowed>

  * <Swc>.slx first (an old reader that only looks at the first hash still compares the model),
  * <Swc>_types.m (RTE-15: the named data types of swc.yaml the model uses),
  * <Swc>_init.m and every file under init_data/ (the workspace variables the model takes constants from),
  * libraries and referenced models the MATLAB dependency analysis finds inside the repository (e.g.
    ../template/NxSwcLib.slx); files in matlabroot or outside the repository are not recorded.
  * a last line `# nx-model-inputs 2`: the record lists all inputs, so an _init.m or init_data file that is not
    recorded is new since the build.
<Swc>_data.m is no input of the code: the RTE reads the calibration values from it (RTE-03).

A record of the swc_build.m before TOOL-169 has only the .slx line (or only a hash): it still counts - its lines are
compared, new files are not reported, because that record never listed them.

Used by the Studio (component table, state dot of the model step) and tools/rtegen (warning), so all three name the
same files. No Qt, no MATLAB.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

RECORD = "model.sha256"
GENERATED = "generated"
MARKER = "# nx-model-inputs 2"
INIT_SUFFIX = "_init.m"
TYPES_SUFFIX = "_types.m"          # RTE-15, the same name as swc_yaml.TYPES_SUFFIX
INIT_DATA = "init_data"
LINE = re.compile(r"^([0-9a-fA-F]{64})(?:\s[ *]?(.*))?$")

CHANGED = "changed"
MISSING = "missing"
NEW = "new"

#: German words for the Studio ("geändert: DemoSwc_init.m")
GERMAN = {CHANGED: "geändert", MISSING: "fehlt", NEW: "neu"}


@dataclass(frozen=True)
class Change:
    path: str           # relative to the component folder, '/' separated
    kind: str           # CHANGED, MISSING or NEW


def record_file(folder: Path) -> Path:
    return Path(folder) / GENERATED / RECORD


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def read_record(folder: Path, name: str | None = None) -> tuple[dict[str, str], bool] | None:
    """(relative path -> recorded hash, complete) of generated/model.sha256; None if there is none or it is unreadable.

    `complete` is True for a record with the marker line (swc_build.m since TOOL-169). A line with a hash but no path
    (very old records, tests) stands for <name>.slx, or for the only .slx of the folder."""
    folder = Path(folder)
    try:
        text = record_file(folder).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    recorded: dict[str, str] = {}
    complete = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            complete = complete or line == MARKER
            continue
        match = LINE.match(raw.rstrip("\r\n"))
        if match is None:
            continue
        digest, path = match.group(1).lower(), (match.group(2) or "").strip()
        if not path:
            if name is None:
                models = sorted(folder.glob("*.slx"))
                if len(models) != 1:
                    continue
                path = models[0].name
            else:
                path = f"{name}.slx"
        recorded[path.replace("\\", "/")] = digest
    return recorded, complete


def watched_inputs(folder: Path, name: str | None = None) -> list[str]:
    """The inputs swc_build.m always records if they exist: the models of the folder, <Swc>_types.m (RTE-15),
    <Swc>_init.m, init_data/**."""
    folder = Path(folder)
    models = [f"{name}.slx"] if name is not None else [m.name for m in sorted(folder.glob("*.slx"))]
    files = [m for m in models if (folder / m).is_file()]
    stems = [name] if name is not None else [Path(m).stem for m in models]
    inits = [f"{stem}{suffix}" for stem in stems for suffix in (TYPES_SUFFIX, INIT_SUFFIX)]
    files += [i for i in inits if (folder / i).is_file()]
    data = folder / INIT_DATA
    if data.is_dir():
        files += sorted(p.relative_to(folder).as_posix() for p in data.rglob("*") if p.is_file())
    return files


def model_changes(folder: Path, name: str | None = None) -> list[Change] | None:
    """Inputs of the component whose content differs from the record of the last code generation.

    None if there is no record (never generated). Otherwise the changed and missing recorded files in record order,
    then the models of the folder that the record does not name at all (changed: the code was not built from them)
    and - only for a complete record - an _init.m or init_data file that came after the build (new).
    `name` is the component name; without it every .slx of the folder counts as a model (Studio table)."""
    folder = Path(folder)
    read = read_record(folder, name)
    if read is None:
        return None
    recorded, complete = read
    changes: list[Change] = []
    for path, digest in recorded.items():
        now = _sha256(folder / path)
        if now is None:
            changes.append(Change(path, MISSING))
        elif now != digest:
            changes.append(Change(path, CHANGED))
    known = {p.lower() for p in recorded}
    for path in watched_inputs(folder, name):
        if path.lower() in known:
            continue
        if path.lower().endswith(".slx"):
            changes.append(Change(path, CHANGED))
        elif complete:
            changes.append(Change(path, NEW))
    return changes


def describe(changes: list[Change], words: dict[str, str] = GERMAN) -> str:
    """'geändert: A.slx, A_init.m; fehlt: ../lib/L.slx' - the files grouped by kind, in the order of `changes`."""
    groups: dict[str, list[str]] = {}
    for c in changes:
        groups.setdefault(c.kind, []).append(c.path)
    return "; ".join(f"{words.get(kind, kind)}: {', '.join(paths)}" for kind, paths in groups.items())


def main(argv: list[str] | None = None) -> int:
    """python tools/swc/modelhash.py <component folder> [<name>]: 'aktuell', the changed inputs or 'kein Code
    erzeugt'. Exit code 0 when current, 1 when an input changed, 2 without a record."""
    import sys
    args = sys.argv[1:] if argv is None else argv
    if not 1 <= len(args) <= 2:
        print(main.__doc__)
        return 2
    changes = model_changes(Path(args[0]), args[1] if len(args) > 1 else None)
    if changes is None:
        print("kein Code erzeugt")
        return 2
    print(describe(changes) if changes else "aktuell")
    return 1 if changes else 0


if __name__ == "__main__":
    raise SystemExit(main())
