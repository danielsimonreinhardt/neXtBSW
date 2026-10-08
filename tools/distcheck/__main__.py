# SPDX-License-Identifier: Apache-2.0
"""Pruefwerkzeug und Export fuer den offenen Stand (PLAT-50).

    python tools/distcheck check   [--profile open] [--references] [--json] [--strict]   Exit 1 bei Fehlern
    python tools/distcheck report  [--format text|markdown|json]                         Klassifizierung je Pfad
    python tools/distcheck export  --out <dir> [--force] [--git-init] [--verify [--verify-timeout S]]  frischer Baum ohne Historie
    python tools/distcheck verify  --tree <dir> [--openbsw <dir>] [--timeout S] [--jobs N]  R9: bauen und testen
    python tools/distcheck patterns [--out <datei>]                                      Vorlage der privaten Liste

Gemeinsame Optionen: --repo <Wurzel>, --rules <distribution.yaml>, --patterns <private Musterliste>.
Die private Musterliste (NDA-Texte) gehoert nicht ins Repository; Vorgabe ist der Pfad aus der Regeldatei
(pattern_file, Vorgabe doc/confidential/distcheck-patterns.yaml - dieser Ordner ist in .gitignore). Fehlt sie,
laeuft die Pruefung weiter und meldet ausdruecklich, dass R3 nur mit der oeffentlichen Grundliste lief.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    # started as a script (python tools/distcheck): make the package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "tools.distcheck"

from tools.distcheck import distcheck as core          # noqa: E402
from tools.distcheck import exporter                   # noqa: E402

DEFAULT_REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def _rules(args) -> core.Rules:
    path = args.rules if args.rules is not None else HERE / "distribution.yaml"
    return core.load_rules(path)


def _patterns(args, rules: core.Rules) -> Path | None:
    if args.patterns is not None:
        return args.patterns
    if rules.pattern_file:
        return args.repo / rules.pattern_file
    return None


def _report(args, rules: core.Rules, root: Path, references: bool, history: str | None = None) -> core.Report:
    return core.check(root, rules, args.profile, references=references, private_patterns=_patterns(args, rules),
                      history=history, skip_rules=args.skip_rule or [],
                      exported_tree=getattr(args, "exported", False),
                      use_git=not getattr(args, "exported", False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python tools/distcheck", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", type=Path, default=DEFAULT_REPO, help="Wurzel des zu pruefenden Repositoriums")
    common.add_argument("--rules", type=Path, default=None, help="Regeldatei (Vorgabe tools/distcheck/distribution.yaml)")
    common.add_argument("--patterns", type=Path, default=None, help="private Musterliste fuer NDA-Texte (R3)")
    common.add_argument("--profile", choices=core.PROFILES, default="open", help="zu pruefendes Profil")
    common.add_argument("--skip-rule", action="append", metavar="Rn", help="Regel auslassen (mehrfach moeglich)")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", parents=[common], help="Baum pruefen")
    c.add_argument("--tree", type=Path, default=None, help="zu pruefender Baum (Vorgabe: --repo)")
    c.add_argument("--references", action="store_true", help="auch Verweise pruefen (CMake, #include, Manifeste)")
    c.add_argument("--history", metavar="BEREICH", default=None, help="Blobs eines Revisionsbereichs pruefen (R8)")
    c.add_argument("--json", action="store_true", help="Bericht als JSON")
    c.add_argument("--strict", action="store_true", help="Warnungen zaehlen als Fehler")
    c.add_argument("--exported", action="store_true", help="der Baum ist bereits ein Export (bereinigte Dateien)")
    c.add_argument("--max-per-rule", type=int, default=20, help="Textausgabe je Regel kuerzen (0 = alle)")

    r = sub.add_parser("report", parents=[common], help="Klassifizierung je Pfad")
    r.add_argument("--format", choices=("text", "markdown", "json"), default="text")

    e = sub.add_parser("export", parents=[common], help="offenen Stand als frischen Baum schreiben")
    e.add_argument("--out", type=Path, required=True, help="Zielordner (leer oder --force)")
    e.add_argument("--force", action="store_true", help="Zielordner vorher loeschen")
    e.add_argument("--git-init", action="store_true", help="im Export ein Repository mit genau einem Commit anlegen")
    e.add_argument("--allow-findings", action="store_true", help="trotz Fehlern exportieren (nur fuer Probelaeufe)")
    e.add_argument("--verify", action="store_true", help="nach dem Export im Export bauen und testen (R9)")
    e.add_argument("--openbsw", type=Path, default=None, help="Checkout von Eclipse OpenBSW fuer den Target-Build")
    e.add_argument("--groups", default="python,host", help="Testgruppen fuer --verify")
    e.add_argument("--verify-timeout", type=int, default=exporter.VERIFY_TIMEOUT, metavar="S",
                   help="Zeitgrenze je verify-Schritt in Sekunden (0 = ohne Grenze)")
    e.add_argument("--verify-jobs", type=int, default=exporter.VERIFY_JOBS, metavar="N",
                   help="parallele Testgruppen fuer --verify")
    e.add_argument("--report", type=Path, default=None, help="Bericht (JSON) in diese Datei schreiben")

    v = sub.add_parser("verify", parents=[common], help="R9: im exportierten Baum bauen und testen")
    v.add_argument("--tree", type=Path, required=True, help="exportierter Baum")
    v.add_argument("--openbsw", type=Path, default=None, help="Checkout von Eclipse OpenBSW fuer den Target-Build")
    v.add_argument("--groups", default="python,host", help="Testgruppen")
    v.add_argument("--timeout", type=int, default=exporter.VERIFY_TIMEOUT, metavar="S",
                   help="Zeitgrenze je Schritt in Sekunden (0 = ohne Grenze)")
    v.add_argument("--jobs", type=int, default=exporter.VERIFY_JOBS, metavar="N", help="parallele Testgruppen")
    v.add_argument("--json", action="store_true", help="Ergebnis als JSON")

    p = sub.add_parser("patterns", parents=[common], help="Vorlage der privaten Musterliste ausgeben")
    p.add_argument("--out", type=Path, default=None, help="Vorlage dorthin schreiben (sonst nach stdout)")

    args = parser.parse_args(argv)
    args.repo = args.repo.resolve()

    try:
        rules = _rules(args)
    except core.RulesError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2

    if args.command == "patterns":
        template = HERE / "patterns.example.yaml"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(template, args.out)
            print(f"Vorlage geschrieben: {args.out}\n"
                  f"Diese Datei gehoert NICHT ins Repository (Vorgabe {rules.pattern_file or 'doc/confidential/'}).")
        else:
            sys.stdout.write(template.read_text(encoding="utf-8"))
        return 0

    try:
        if args.command == "check":
            report = _report(args, rules, (args.tree or args.repo).resolve(), args.references, args.history)
            print(report.to_json() if args.json else core.format_text(report, args.max_per_rule))
            return 1 if report.errors or (args.strict and report.warnings) else 0

        if args.command == "report":
            tree = core.build_tree(args.repo, rules)
            sys.stdout.write(_classification(tree, rules, args.profile, args.format))
            return 0

        if args.command == "verify":
            steps = exporter.verify(args.tree.resolve(), rules, openbsw=args.openbsw, groups=args.groups,
                                   timeout=args.timeout, jobs=args.jobs)
            return _print_verify(steps, getattr(args, "json", False))

        # export
        report = _report(args, rules, args.repo, references=True)
        print(core.format_text(report, max_per_rule=10), file=sys.stderr)
        if report.errors and not args.allow_findings:
            print(f"FEHLER: {len(report.errors)} Befund(e) - kein Export. --allow-findings nur fuer Probelaeufe.",
                  file=sys.stderr)
            return 1
        tree = core.build_tree(args.repo, rules)
        result = exporter.export(args.repo, rules, args.out, args.profile, force=args.force,
                                 git_init=args.git_init, tree=tree)
        print(f"Export nach {result.out}: {result.copied} Datei(en), {result.dropped} nicht im Profil, "
              f"bereinigt: {', '.join(result.cleaned) or '-'}, erzeugt: {', '.join(result.generated) or '-'}, "
              f"Ersetzungen in: {', '.join(result.substituted) or '-'}")
        # DOC-73: links to unpublished files, replaced by "not published, available on request"; the full list
        # (file, count, every place) lies next to the tree for the sample check of the release
        print(f"Verweise auf nicht veröffentlichte Dateien ersetzt: {sum(result.links.values())} in "
              f"{len(result.links)} Datei(en); Ersetzungsliste: {result.replacement_report}")
        for rel, count in sorted(result.links.items()):
            print(f"  {count:4d}  {rel}")
        # DOC-74: private sections of published documents (export:private-begin/-end), listed in the same report
        print(f"Private Abschnitte entfernt: {sum(result.sections.values())} in {len(result.sections)} Datei(en)")
        for rel, count in sorted(result.sections.items()):
            print(f"  {count:4d}  {rel}")
        for note in result.notes:
            print(f"  {note}")
        after = core.check(Path(result.out), rules, args.profile, references=True,
                           private_patterns=_patterns(args, rules), use_git=False, exported_tree=True)
        print(core.format_text(after, max_per_rule=10))
        steps = []
        if args.verify:
            steps = exporter.verify(Path(result.out), rules, openbsw=args.openbsw, groups=args.groups,
                                   timeout=args.verify_timeout, jobs=args.verify_jobs)
            _print_verify(steps, False)
        if args.report:
            args.report.write_text(json.dumps({"before": json.loads(report.to_json()),
                                               "export": json.loads(result.to_json()),
                                               "after": json.loads(after.to_json()),
                                               "verify": [step.__dict__ for step in steps]},
                                              indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Bericht: {args.report}")
        if after.errors:
            return 1
        return 1 if any(step.status == "fail" for step in steps) else 0
    except (core.RulesError, exporter.ExportError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2


def _print_verify(steps, as_json: bool) -> int:
    if as_json:
        print(json.dumps([step.__dict__ for step in steps], indent=2, ensure_ascii=False))
    else:
        print("\nR9 Nachweis im exportierten Baum:")
        for step in steps:
            print(f"  {step}")
            if step.status != "pass" and step.tail:
                print("    " + step.tail.strip().replace("\n", "\n    ")[-2000:])
    return 1 if any(step.status == "fail" for step in steps) else 0


def _classification(tree: core.Tree, rules: core.Rules, profile: str, fmt: str) -> str:
    """Overview: which glob classified how many files, and which files nothing classified."""
    rows: dict[str, dict[str, int]] = {}
    unclassified = []
    for path in tree.files:
        cls = tree.cls[path]
        key = tree.source_of.get(path) or "(ohne Regel)"
        rows.setdefault(key, {}).setdefault(cls, 0)
        rows[key][cls] += 1
        if cls == core.UNCLASSIFIED:
            unclassified.append(path)
    if fmt == "json":
        return json.dumps({"profile": profile, "files": len(tree.files), "rows": rows,
                           "unclassified": unclassified}, indent=2, ensure_ascii=False) + "\n"
    lines = []
    if fmt == "markdown":
        lines += ["| Regel | Klasse | Dateien |", "|---|---|---|"]
        for key in sorted(rows):
            for cls, count in sorted(rows[key].items()):
                lines.append(f"| `{key}` | {core.CLASS_DE.get(cls, cls)} | {count} |")
    else:
        for key in sorted(rows):
            for cls, count in sorted(rows[key].items()):
                lines.append(f"{count:5d}  {core.CLASS_DE.get(cls, cls):18s} {key}")
    if unclassified:
        lines.append(f"\nnicht klassifiziert ({len(unclassified)}):")
        lines += [f"  {path}" for path in unclassified[:50]]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
