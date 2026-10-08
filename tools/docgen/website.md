# Doku-Website

Aus denselben Markdown- und Mermaid-Quellen, die im Repository liegen, entsteht eine Website mit
[MkDocs Material](https://squidfunk.github.io/mkdocs-material/): Suche, Sprachumschalter Deutsch/Englisch, Mermaid
direkt im Browser gezeichnet und nach der Veröffentlichung eine Dokuversion je Release (`mike`). Die Konfiguration
ist [`mkdocs.yml`](../../mkdocs.yml) in der Wurzel, die Pakete stehen in `tools/requirements-site.txt` – getrennt von
`tools/requirements.txt`, damit sich die Bau- und Testpakete nicht ändern (DOC-71).

```sh
pip install -r tools/requirements-site.txt
.venv/Scripts/python -m tools.docgen.site build --out %TEMP%/nx-site     # exportieren, dort bauen, strict
.venv/Scripts/python -m tools.docgen.site build --tree <export> --out <dir>   # einen schon exportierten Baum bauen
.venv/Scripts/python -m tools.docgen.site pages                          # welche Seiten und Dateien bereitgestellt würden
.venv/Scripts/python tools/docgen/test_site.py                           # Eintrag docgen-site in tools/test/tests.yaml
```

## Welche Seiten

Nicht nur `doc/`: auch `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, die Moduldokus neben dem Code (`libs/**/doc`,
`bootloader/doc`, Port-Module) und die READMEs der Werkzeuge sind Seiten. MkDocs braucht genau einen `docs_dir`.
Deshalb stellt der Hook `tools/docgen/mkdocs_hooks.py` die veröffentlichten Seiten mit ihren Repository-Pfaden in
einen Temp-Ordner und macht diesen zum `docs_dir`; jeder relative Link funktioniert dort wie auf dem Code-Host.

Was veröffentlicht wird, entscheidet nicht die Website, sondern die Export-Klassifizierung
(`tools/distcheck/distribution.yaml`, Profil `open`). Was nicht ausdrücklich offen ist, kommt nicht hinein, und
`doc/confidential` ist zusätzlich ausdrücklich ausgeschlossen. Dateien ohne Markdown, auf die eine Seite verweist
(`NOTICE`, eine YAML- oder Quelldatei), werden als einfache Datei mitkopiert.

## Gebaut wird im Export

Die öffentliche Website entsteht im exportierten Baum; `site.py build` exportiert dafür zuerst in einen Temp-Ordner.
Begründung: Nur dort sind Verweise auf nicht veröffentlichte Seiten durch *not published, available on request*
ersetzt (DOC-73), private Abschnitte herausgeschnitten (DOC-74) und interne Review-Links ersetzt (PLAT-114). Die
Website zeigt damit genau den Text, der veröffentlicht wird, und es gibt nur eine Stelle, die über „öffentlich“
entscheidet. Eine zweite Ausschlussliste in `mkdocs.yml` (`exclude_docs`) müsste dieselbe Entscheidung nachbauen,
würde die Verweise auf ausgeschlossene Seiten tot lassen und die privaten Abschnitte nicht schneiden.

Ein Bau des Entwicklungsbaums geht als Vorschau (`mkdocs serve`), scheitert mit `--strict` aber an seinen Links auf
private Seiten – mit Absicht.

Nicht als Seite, Navigationseintrag oder Suchtreffer erscheinen außerdem (`SITE_EXCLUDE` in `site.py`, gespiegelt
durch `exclude_docs` in `mkdocs.yml`, DOC-83): `CHANGELOG.md` und `CLAUDE.md` (bleiben bereinigt im Release-Repository)
sowie die STM32-Detailseiten (`platforms/stm32`, `mcudb/stm32`, `boards/nucleo_f446re`). Von STM32 bleibt nur die
Kurzseite [`stm32-nucleo.md`](../../doc/architecture/stm32-nucleo.md) mit dem Status *planned*. Ein Link auf diese Dateien
wird wie jeder Link ins Repository zu Text plus Pfad.

## Links ohne Seite

- Ein Link auf einen Ordner geht auf dessen `README.md`, wenn der Ordner eine hat.
- Jeder andere Link in den veröffentlichten Baum, der keine Seite ist (ein Code-Ordner, `external/openbsw/...`),
  zeigt seinen Text mit dem Repository-Pfad. Sobald die Website veröffentlicht wird, verweist er stattdessen auf
  `extra.nextbsw.source_url` in `mkdocs.yml` (Baum-Adresse des Release-Tags).
- Ein Link auf einen Pfad, den es nicht gibt, bleibt stehen; der strikte Bau meldet ihn.

## Navigation und Sprachen

Die Navigation folgt den Lesepfaden aus [`doc/README.md`](../../doc/README.md): Integrator, Safety-Assessor,
Contributor. Seiten außerhalb der Navigation werden trotzdem gebaut und sind über Links erreichbar. Jede Seite steht
nur einmal in der Navigation; der Integrator-Pfad endet im Sicherheitshandbuch unter *Safety-Assessor*.

`mkdocs-static-i18n` mit Suffix-Struktur: Heute gibt es jede Seite in einer Sprache, beide Bäume (`/` deutsch,
`/en/` englisch) zeigen sie. Eine Übersetzung kommt mit DOC-70 als `<name>.en.md` bzw. `<name>.de.md` neben die
Seite. Die generierte Referenz bleibt in beiden Bäumen englisch.

## Veröffentlichung (DOC-87)

Der Workflow [`.github/workflows/docs.yml`](../../.github/workflows/docs.yml) (Name *Docs*) läuft nur von Hand
(`workflow_dispatch`, wie CI) und nur auf dem Zweig `main` des Release-Repositorys; im Entwicklungsbaum bricht er ab,
denn die Website entsteht nur aus dem Export. Der Ablauf:

1. Job *build* (Recht `contents: read`): Checkout mit `external/openbsw`, `pip install -r tools/requirements-site.txt`,
   `python -m tools.docgen.site build --out site` (strict, Profil `open` der Export-Klassifizierung – anderes Profil
   gibt es nicht).
2. Job *publish* (nur er hat `contents: write`, mike schreibt ausschließlich den Zweig `gh-pages`): `mike deploy --push
   --update-aliases <Version> latest` mit der Version aus der Datei `VERSION`, danach `mike set-default --push latest`.
   Eine Dokuversion je Release; ein weiterer Lauf mit derselben Version ersetzt sie, `latest` zeigt auf die neueste.
3. Nach F15/F16 schaltet der Nutzer GitHub Pages frei (Einstellung *Pages*, Quelle: Zweig `gh-pages`) und trägt in
   `mkdocs.yml` `extra.nextbsw.source_url` (Baum-Adresse des Release-Tags) ein. Vorher läuft der Workflow nicht.

Der Workflow gehört mit Klasse `open` (Apache-2.0) in den Export (`tools/distcheck/distribution.yaml`, Test
`test_docs_workflow_is_exported_in_the_open_profile`). Er wurde nicht auf GitHub ausgeführt.

## Mermaid

`pymdownx.superfences` mit dem Custom Fence `mermaid`: Ein Block kommt als `<pre class="mermaid">` samt
B1-Kopfzeile (`%%{init: ...}%%`) im HTML an, Material lädt Mermaid im Browser. Ob die Rundung der Gruppen (`rx`/`ry`
in `classDef grp`) greift, lässt sich nur im Browser sehen; der Test prüft das HTML.

## Prüfung

`tools/test -k docgen-site` (übersprungen ohne die Pakete): Export in einen Temp-Ordner, dort `mkdocs build
--strict` – kein toter Link, kein toter Anker, keine Warnung. Im HTML: Mermaid als `pre.mermaid` mit Kopfzeile,
private Seiten und `doc/confidential` fehlen, Verweise darauf tragen den DOC-73-Hinweis, Sprachumschalter vorhanden,
jede Seite der Navigation ist veröffentlicht und steht nur einmal darin. Gegenproben auf einem kleinen Baum mit
derselben `mkdocs.yml`: kaputter Link, kaputter Anker, Link auf eine private Seite und ein Navigationseintrag einer
unveröffentlichten Seite lassen den Bau scheitern. Dauer: etwa 1 min Export und 1–3 min Bau (zwei Sprachbäume).

## Werkzeuge und Lizenzen

MkDocs (BSD-2-Clause), MkDocs Material (MIT), mkdocs-static-i18n (MIT), mike (BSD-3-Clause), pymdown-extensions
(MIT); mike wird vom Workflow *Docs* benutzt; die übrigen Abhängigkeiten mit Lizenz stehen in `tools/requirements-site.txt`. Die Werkzeuge gehen nicht in den
Export, nur ihre Konfiguration. MkDocs Material warnt bei jedem Bau, dass MkDocs 2.0 das Plugin-System abschafft;
die Pakete sind deshalb exakt auf MkDocs 1.6.1 und Material 9.7.7 festgelegt, ein Update ist eine eigene Aufgabe.
