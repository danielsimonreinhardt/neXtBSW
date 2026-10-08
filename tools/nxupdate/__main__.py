"""Command line:
    python tools/nxupdate <workspace> --to <version> [--write [--commit]] [--release] [--offline]
                          [--tree <path>] [--python <exe>] [--report <file>] [--no-diff] [--keep-scratch]

Moves a workspace's pin (`nextbsw.yaml`) to a new neXtBSW release (TOOL-64, doc/architecture/customer-project.md
gap N9, section 5.5): generates every project with the old and the new tree, checks the target tag's provenance
(TOOL-65), runs the new tree's format migrations, and writes a report of the generated-file diff and the changes
that matter to a fielded ECU. The default is a dry run: nothing in the workspace changes, and the report is only
printed. `--write` moves the pin and applies the migration; `--commit` (implies `--write`) also commits the
result - this tool never pushes.

    python tools/nxupdate acme-bcm --to 1.5.0                         # dry run, full report on stdout
    python tools/nxupdate acme-bcm --to 1.5.0 --write                 # moves the pin, writes the update report
    python tools/nxupdate acme-bcm --to 1.5.0 --write --commit        # ... and commits it (never pushes)
    python tools/nxupdate acme-bcm --to 1.5.0 --release               # a customer release: a development target
                                                                       # tag (no release tag yet) is an error
"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from nxupdate import update  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="nxupdate", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workspace", type=Path, help="workspace folder or its nextbsw.yaml")
    parser.add_argument("--to", metavar="VERSION", required=True,
                        help="target neXtBSW version MAJOR.MINOR.PATCH, or a release candidate tag vX.Y.Z-rcN "
                             "(TOOL-232: the tree goes to that tag, the pin gets X.Y.Z)")
    parser.add_argument("--tree", type=Path,
                        help="use this already-materialised tree as the target instead of the pinned "
                             "submodule's own history (the source: archive fallback, customer-project.md 4.4; "
                             "nxupdate never downloads an archive itself)")
    parser.add_argument("--write", action="store_true",
                        help="move the pin and apply the migration (default: dry run, nothing changes)")
    parser.add_argument("--commit", action="store_true",
                        help="commit the result in the workspace repository (implies --write; never pushes)")
    parser.add_argument("--release", action="store_true",
                        help="release strictness of the target tag (TOOL-65): a development pin is an error, "
                             "not a warning")
    parser.add_argument("--offline", action="store_true",
                        help="never run git fetch; the target tag must already be present locally")
    parser.add_argument("--python", help="interpreter used for the generators and nxconfig of both trees "
                                         "(default: this interpreter)")
    parser.add_argument("--report", type=Path, help="also write the full report to this file")
    parser.add_argument("--keep-scratch", action="store_true", help="do not delete the scratch directory "
                                                                     "(before/after generation, a dry-run clone "
                                                                     "of the target tree) - for inspection")
    args = parser.parse_args(argv)

    try:
        result = update.run(args.workspace, args.to, tree=args.tree, write=args.write, commit=args.commit,
                            release=args.release, offline=args.offline, python=args.python,
                            keep_scratch=args.keep_scratch)
    except update.UpdateError as e:
        print(f"nxupdate: error: {e}", file=sys.stderr)
        return 1

    print(result.report_text)
    print(update.render_summary(result), file=sys.stderr)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(result.report_text, encoding="utf-8", newline="")
        print(f"report also written to {args.report}", file=sys.stderr)

    if args.write and not result.written:
        return 1                                    # --write was asked for and refused (see the report: why)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
