# SPDX-License-Identifier: Apache-2.0
"""Generates the reference in doc/reference from the tools/nxconfig schema (DOC-03), from libs/ (DOC-04) and
from the console command registration in the sources (DOC-09), and the module map doc/figures/07-modulkarte.md
from the same module list as doc/reference/modules.md (DOC-64).

    python tools/docgen            write doc/reference/*.md and the module map (only files that changed)
    python tools/docgen --check    exit 1 if a checked-in page differs from its sources, with a diff
    python tools/docgen --gaps     list schema fields without description or unit, module and console gaps

Options: --out <directory> (default: doc/reference of this repository), --figures <directory> for the module map
(default: doc/figures with the default --out, otherwise the --out directory).
Exit code 2: a source is missing (external/openbsw not checked out).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    # started as a script (python tools/docgen): make the package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "tools.docgen"

from tools.docgen import console, modulemap, modules, reference  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python tools/docgen", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=reference.OUT_DIR, help="output directory")
    parser.add_argument("--figures", type=Path, default=None,
                        help="output directory of the module map (default: doc/figures, or --out if given)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="compare instead of writing; exit 1 on a difference")
    mode.add_argument("--gaps", action="store_true",
                      help="list schema fields without description or unit, modules with gaps in libs/ and "
                           "console commands with gaps")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except console.SourceMissing as exc:
        print(f"docgen: {exc}", file=sys.stderr)
        return 2


def _run(args) -> int:
    if args.gaps:
        desc, unit = reference.gaps()
        print(f"Fields without description ({len(desc)}):")
        for owner, where in desc:
            print(f"  {owner:45} {where}")
        print(f"Numeric fields without unit ({len(unit)}):")
        for owner, where in unit:
            print(f"  {owner:45} {where}")
        module_gaps = modules.gaps(modules.collect())
        print(f"Module gaps ({len(module_gaps)}):")
        for path, what in module_gaps:
            print(f"  {path:45} {what}")
        console_gaps = console.gaps(console.collect_real())
        print(f"Console gaps ({len(console_gaps)}):")
        for where, what in console_gaps:
            print(f"  {where:60} {what}")
        return 0

    catalogue = modules.collect()
    generated = reference.build_pages(catalogue=catalogue, console_doc=console.collect_real())
    # the module map (DOC-64) lives in doc/figures; with another --out it goes there too, unless --figures is given
    figures_dir = args.figures or (modulemap.OUT_DIR if args.out == reference.OUT_DIR else args.out)
    figures = {modulemap.FILE: modulemap.render(catalogue)}
    if args.check:
        report = reference.check(args.out, generated) + reference.check(figures_dir, figures)
        if report:
            print("\n".join(report))
            print(f"\nThe reference in {args.out} or the module map in {figures_dir} does not match its sources "
                  f"({reference.SCHEMA_FILE}, {modules.SOURCES}, {console.SOURCES}).\n"
                  f"Regenerate it with:  {reference.REGENERATE}\nand commit the changed pages.", file=sys.stderr)
            return 1
        print(f"reference up to date: {len(generated)} pages in {args.out}, module map {figures_dir / modulemap.FILE}")
        return 0
    changed = reference.write(args.out, generated)
    changed_fig = reference.write(figures_dir, figures)
    print(f"{len(changed)} of {len(generated)} pages written to {args.out}" +
          (": " + ", ".join(changed) if changed else " (all up to date)"))
    print(f"module map {figures_dir / modulemap.FILE}: " + ("written" if changed_fig else "up to date"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
