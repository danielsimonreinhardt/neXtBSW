# SPDX-License-Identifier: Apache-2.0
"""The component folder DemoSwc as the export writes it (PLAT-119, decision F9 A).

The export leaves out swc/DemoSwc/generated (Simulink Coder output under a MATLAB student licence) and writes the
files of the overlay in tools/distcheck/distribution.yaml (export.overlay) into swc/DemoSwc: swc.yaml and
src/DemoSwc.c of this substitute. assemble() builds the same folder from the tree it runs in - the development
repository or an exported tree - by reading the same overlay, so the tests of the substitute check what is
published and the rule cannot drift from the tests.

    python swc/DemoSwcSubstitute/test/assemble.py <target folder>     writes <target folder>/DemoSwc
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from tools.distcheck import distcheck as core  # noqa: E402

COMPONENT = "swc/DemoSwc"
REPLACED = "swc/DemoSwc/generated"


def overlay() -> core.Overlay:
    """The overlay of distribution.yaml that stands in for the generated code of DemoSwc."""
    rules = core.load_rules(REPO / "tools" / "distcheck" / "distribution.yaml")
    found = [o for o in rules.overlays if o.replaces == REPLACED]
    if len(found) != 1:
        raise SystemExit(f"distribution.yaml: export.overlay needs exactly one entry for {REPLACED}, found {len(found)}")
    return found[0]


def assemble(target: Path) -> Path:
    """<target>/DemoSwc: swc/DemoSwc without its generated code and without earlier overlay files, plus the overlay."""
    entry = overlay()
    folder = Path(target) / "DemoSwc"
    if folder.exists():
        shutil.rmtree(folder)
    source = REPO / COMPONENT
    for path in sorted(source.rglob("*")):
        rel = f"{COMPONENT}/{path.relative_to(source).as_posix()}"
        if not path.is_file() or entry.covers(rel) or rel in entry.files:
            continue
        dest = folder / path.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    for target_rel, source_rel in entry.files.items():
        if not target_rel.startswith(COMPONENT + "/"):
            raise SystemExit(f"distribution.yaml: overlay target {target_rel} is not inside {COMPONENT}")
        dest = folder / target_rel[len(COMPONENT) + 1:]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / source_rel, dest)
    return folder


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    print(assemble(Path(sys.argv[1])))
