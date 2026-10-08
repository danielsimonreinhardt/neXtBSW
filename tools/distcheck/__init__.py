# SPDX-License-Identifier: Apache-2.0
"""Pruefwerkzeug fuer den offenen Stand (PLAT-50, Konzept doc/architecture/lizenz-und-release.md Abschnitt 5.3).

`distcheck` prueft einen Baum, *bevor* etwas exportiert wird: Klassifizierung jeder Datei, SPDX gegen eine
Allowlist, Textmuster aus NDA-Unterlagen, Geheimnisse, Verweise, die aus dem offenen Baum herauszeigen. Das
Export-Skript erzeugt daraus einen frischen Baum ohne Git-Historie und weist ihn nach, indem es dort die
Host-Tests und einen Target-Build laufen laesst.

    python tools/distcheck check   --profile open [--references] [--json]
    python tools/distcheck report  [--format text|markdown|json]
    python tools/distcheck export  --out <dir> [--verify --openbsw <dir>] [--git-init]
    python tools/distcheck verify  --tree <dir> [--openbsw <dir>]
    python tools/distcheck patterns [--out <datei>]

Kernlogik ohne Qt (distcheck.py, exporter.py), Kommandozeile in __main__.py.
"""

from .distcheck import (  # noqa: F401
    CLASSES, PROFILES, Finding, Report, Rules, RulesError, Tree, build_tree, check, format_text, list_files,
    load_private_patterns, load_rules,
)
from .exporter import ExportError, ExportResult, export, verify  # noqa: F401
