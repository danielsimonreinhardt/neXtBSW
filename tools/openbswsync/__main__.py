# SPDX-License-Identifier: Apache-2.0
"""Kopien-Abgleich gegen OpenBSW (TOOL-21).

    python -m tools.openbswsync check    [--strict] [--json]          Exit 1 bei Fehlern (mit --strict auch bei Warnungen)
    python -m tools.openbswsync report   [--format text|markdown|json] [--all]   Bericht je Datei, Exit 0
    python -m tools.openbswsync accept   [--commit] [--all-upstream] [PFAD ...]  geprüften Upstream-Stand eintragen
    python -m tools.openbswsync skeleton                                 Einträge für noch nicht gelistete Dateien

Gemeinsame Optionen: --repo <Wurzel des Repositorys> (Standard: aus der Lage dieses Pakets),
--list <copies.yaml> (Standard: tools/openbswsync/copies.yaml des Repositorys).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    # started as a script (python tools/openbswsync): make the package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "tools.openbswsync"

from tools.openbswsync import sync  # noqa: E402

DEFAULT_REPO = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.openbswsync", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", type=Path, default=DEFAULT_REPO, help="Wurzel des Repositorys")
    common.add_argument("--list", type=Path, default=None, help="Kopienliste (Standard tools/openbswsync/copies.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", parents=[common], help="Repository gegen Liste und Submodul prüfen")
    c.add_argument("--strict", action="store_true", help="Warnungen zählen als Fehler")
    c.add_argument("--json", action="store_true", help="Bericht als JSON")
    r = sub.add_parser("report", parents=[common], help="Bericht je Datei (identisch, geändert, neu)")
    r.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    r.add_argument("--all", action="store_true", help="auch nicht übernommene Dateien des Originals auflisten")
    a = sub.add_parser("accept", parents=[common], help="Hash des aktuellen Originals als geprüft eintragen")
    a.add_argument("paths", nargs="*", help="Repository-Pfade der Kopien (z. B. ecu/application/src/main.cpp)")
    a.add_argument("--all-upstream", action="store_true", help="alle Einträge mit geändertem Original")
    a.add_argument("--commit", action="store_true", help="Commit des Submoduls als Erfassungsstand eintragen")
    sub.add_parser("skeleton", parents=[common], help="YAML-Einträge für Dateien ohne Eintrag ausgeben")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    list_file = args.list if args.list is not None else repo / "tools" / "openbswsync" / "copies.yaml"
    try:
        if args.command == "accept":
            if not (args.paths or args.all_upstream or args.commit):
                parser.error("accept braucht Pfade, --all-upstream oder --commit")
            for line in sync.accept(repo, list_file, args.paths, args.all_upstream, args.commit):
                print(line)
            return 0
        copy_list = sync.load_list(list_file)
        if args.command == "skeleton":
            sys.stdout.write(sync.skeleton(repo, copy_list))
            return 0
        report = sync.check(repo, copy_list)
    except sync.ListError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2

    if args.command == "check":
        print(report.to_json() if args.json else sync.format_text(report, findings_only=True))
        return 1 if report.errors or (args.strict and report.warnings) else 0
    if args.format == "json":
        print(report.to_json())
    elif args.format == "markdown":
        sys.stdout.write(sync.format_markdown(report, args.all))
    else:
        print(sync.format_text(report, args.all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
