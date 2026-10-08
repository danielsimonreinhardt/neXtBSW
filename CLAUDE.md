<!-- Bereinigte Fassung: die Abschnitte zur internen Arbeitsweise (Agenten, Koordinator, eigenes Board,
     Hardware-Freigaben) sind entfernt. -->

# neXtBSW – Regeln für Claude und Agenten

AUTOSAR-ähnliche, per Generator konfigurierte Basis-Software neXtBSW (Fokus NXP S32K1) auf Eclipse OpenBSW (FreeRTOS),
Anwendungen als Simulink-Modelle (AUTOSAR Classic SWC + eigene RTE). Übersicht: `README.md`,
Verlauf und Messwerte: `CHANGELOG.md`, offene Arbeit: `doc/todo.yaml`.

## Leitprinzip neXtBSW (verbindlich, Nutzer 2026-09-15)

Ziel ist eine **universell verwendbare Basis-Software (Projektname neXtBSW)**. Das Eval Board S32K144 ist nur das
erste Beispiel und darf **keine architekturrelevanten Entscheidungen auslösen**. Zielplattformen: Fokus S32K1-Familie,
konkret auch S32K3, STM32 und TI RM4x, grundsätzlich ARM Cortex-M und Cortex-R (32 Bit, Little Endian, D11). Board-Treiber, die am Eval Board entstehen,
bleiben als wiederverwendbare Module im Repo; Board- und Projektdaten werden davon getrennt (Aufgaben ARCH-*). Für jede
Änderung gilt:

- Board-Fakten (Pins, Quarz, bestückte Bausteine, Flash-/RAM-Größen, CAN-Instanzen, SBC ja/nein) gehören in
  Konfiguration (Derivat-, Board-, Projektebene), nie als Konstante in wiederverwendbaren Code, Bootloader oder Tools.
- Keine „nur CAN_0“/„nur ein Task“/„nur S32K144“-Beschränkung in Generatoren ohne ausdrückliche Prüfung mit Fehlermeldung
  und Todo-Eintrag; Datenmodelle so anlegen, dass mehrere Instanzen möglich sind.
- Generator-Tests mit eigenen Test-Profilen, nicht nur gegen das Eval-Board-Profil.
- Wird eine Board-Eigenheit doch in die Architektur gezwungen: abbrechen und dem Koordinator/Nutzer melden.
- **Verbindliches Zielbild: [`doc/architecture/README.md`](doc/architecture/README.md)** (abgenommen 2026-09-15): Schichten,
  Rollen und Modul-Manifeste, Konfigurationsebenen mit Zuständigkeit je Wert, Repo-Aufbau, Entscheidungen D1–D14.
  Die Prüffragen aus Abschnitt 11 gelten für jede Änderung und stehen in jedem Agenten-Auftrag.
- **Granularität von Gerätetreibern: [`doc/architecture/treiber-granularitaet.md`](doc/architecture/treiber-granularitaet.md)**
  (Entscheidung des Nutzers 2026-09-16): Die Grenze verläuft am **Protokoll**, nicht am Baustein. Varianz nur in Zahlen
  → generisches Verhaltensmodul, Baustein in die `parts:`-Liste; eigenes Registermodell oder eigene Sequenz →
  hardwarenaher Treiber je Bausteinfamilie; gleiches Verhalten über anderen Zugriffsweg → dünner Adapter, kein weiterer
  Parameter. Schnittstellen über Bausteine erst ab dem **zweiten** Exemplar. Projektparameter in physikalischen
  Einheiten der Funktion (mA Laststrom), nicht in Messgrößen des Bauteilpfads (mV an IS). Gehört in jeden
  Agenten-Auftrag zu einem Gerätetreiber.

## Bauen und testen

```sh
.venv/Scripts/python tools/test --list                    # alle Host-Tests und Target-Builds (tools/test/tests.yaml)
.venv/Scripts/python tools/test --group all --jobs 4      # vollstaendiger Lauf (ohne --slow: ohne Mutationslaeufe)
.venv/Scripts/python tools/test -k nxconfig --jobs 4      # Auswahl
```
Compiler: `arm-none-eabi-gcc` aus dem `PATH` oder der Benutzer-Umgebungsvariable `NX_ARM_GCC_BIN`. Standard ist der
deinstalliert). Ein Build-Ordner mit altem Compilerpfad im CMake-Cache braucht `cmake --preset <name> --fresh`;
`tools/test` erkennt das selbst. Projekte und Presets: `projects/README.md`.
SBC-Treiber-Host-Test und DemoSwc-SIL-Test: `tools/test -k uja113x` bzw. `tools/test -k demoswc-sil` (Einzelheiten in `tools/test/README.md` und `swc/README.md`).
Windows: Python immer aus `.venv`, `PYTHONUTF8=1`. PowerShell 5.1 schreibt mit `Set-Content -Encoding utf8` eine BOM – Dateien mit dem Write-Tool oder Python schreiben.

## Konventionen

- Changelog auf Deutsch und ausführlich (was, warum, Messwerte, Fehlerbilder). Immer angeben, wie verifiziert wurde:
  **Mock/Host** oder **am Board**. Nicht Verifiziertes ausdrücklich als offen nennen.
- **Sprache der Dokumentation (Nutzer 2026-09-17):** Veröffentlichte Dokumentation wird auf **Englisch** geschrieben –
  `README.md`, alles unter `doc/` außer den unten genannten Dateien, die Modul-Dokumentation unter `libs/*/doc/` und die
  READMEs neben Werkzeugen, Boards und Projekten. Grund: internationales Publikum, und die Fachsprache (AUTOSAR,
  OpenBSW, Datenblätter) ist ohnehin englisch. **Deutsch bleiben** `CHANGELOG.md` samt den Fragmenten in
  `doc/changelog.d/` (das Arbeitsprotokoll des Projekts, ausführlich mit Messwerten und Verifikation), die internen
  Planungsdateien `doc/todo.yaml`, `doc/TODO.md`, `doc/koordinator-stand.md` und diese Datei sowie die Agenten-Aufträge
  und Berichte. Code-Kommentare sind wie bisher englisch. Bestehende deutsche Dokumente werden nicht nebenbei übersetzt
  (das ist DOC-05); neu geschriebene oder neu gefasste Teile sind ab sofort englisch. Einstieg in die Dokumentation:
  [`doc/README.md`](doc/README.md), Regeln für Beiträge: [`CONTRIBUTING.md`](CONTRIBUTING.md).
- Code-Kommentare englisch und knapp, Stil des umgebenden Codes. Das Submodul `external/openbsw` wird nie geändert.
- Konfiguration in drei Ebenen (`tools/nxconfig`): Derivat `mcudb/<familie>/<derivat>.yaml`, Board
  `boards/<board>/board.yaml` (nur Hardware), Projekt `projects/<projekt>/project.yaml` (Nutzung, Frequenzen, Safety-Pins,
  ECU-Beschreibung). Neue Werte in genau die zuständige Ebene (Architektur Abschnitt 4); Prüfungen über Einträge und
  Ebenen gehören nach `tools/nxconfig/checks.py`, familienspezifische (Register, Pinmux) in den Generator.
- Generierter Code: Board-Konfiguration aus dem Projekt (`tools/bswgen`), RTE aus der ECU-Beschreibung des Projekts
  (`projects/<projekt>/rte.yaml`, `tools/rtegen`), Komponenten-Code aus der `.slx` (`tools/swc/swc_build.m`, eingecheckt). Generierte
  Dateien nicht von Hand ändern, sondern Generator/Modell.
- Neue Treiber im OpenBSW-Aufbau: Schnittstellen `libs/bsp`, Gerätetreiber `libs/bsw`, MCU-Treiber
  `platforms/s32k1xx/bsp`, Board-spezifisches unter `ecu/platforms/<board>`. Hardwareunabhängige Teile bekommen Host-Tests
  (Registermodell wie `libs/bsw/sbcUja113x/test`), die mit gezielt eingebauten Fehlern auf Aussagekraft geprüft werden.
