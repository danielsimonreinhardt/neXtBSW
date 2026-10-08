# Projekte

Ein Projekt ist eine ECU-Software auf einem Board (Architektur `doc/architecture/README.md`, Abschnitte 4 und 8).
Beim Bauen ist das Projekt die **einzige Auswahl**: Board, Derivat, Familie und Kern liest CMake über
`tools/nxconfig` aus der Konfiguration.

```
projects/<projekt>/
  project.yaml        Nutzung des Boards, Takte, Bitraten, Safety, Verweise auf Board (project.board) und ECU-Beschreibung (project.rte)
  rte.yaml            ECU-Beschreibung für tools/rtegen: Anwendungs-Tasks (Periode, Priorität, Stack, Runnables mit
                      Versatz und Reihenfolge), Komponenten, Verbindungen, CAN (DBC, Knoten, Bus, Task,
                      Signal-Qualifier je Botschaft)
  <Name>.dbc          CAN-Datenbank des Projekts
  swc/<Swc>/          projektspezifische Komponenten (wiederverwendbare liegen unter swc/)
  calibration/        optional: calibration dataset nextbsw-cal 1 (base/, variants/<v>/); when present, the build takes
                      the parameter values from it instead of <Swc>_data.m (TOOL-145, tools/calgen/README.md)
```

Path rules (RTE-22): every path is relative to **the file it stands in**. `project.board` and `project.rte` are
relative to `project.yaml`, and the paths inside `rte.yaml` (`components[].path`, `can.dbc`) are relative to that
`rte.yaml`. So a project folder can be moved, copied or kept outside the neXtBSW tree without rewriting it: a
customer workspace has no repository root the paths could be based on (`doc/architecture/customer-project.md`,
gap N3).

The ECU description carries the format it is written in:

```yaml
schema_version: 2       # paths relative to this file
components:
  - { name: DemoSwc, path: ../../swc/DemoSwc }
can:
  dbc: MyEcu.dbc        # next to the rte.yaml
```

A file without the key is format 1, whose paths were relative to the repository root (`rtegen --repo`). Such a
file is refused with the command that raises it; `--repo` is only that fallback and is ignored for format 2:

```sh
.venv/Scripts/python tools/nxconfig migrate <workspace>            # shows the change
.venv/Scripts/python tools/nxconfig migrate <workspace> --write    # writes it, after a backup
```

The migration edits the lines it changes and leaves comments, alignment and order as they are.

Tasks (RTE-02): Die Anwendungs-Tasks stehen im Projekt (`rte.yaml`, `tasks:`), die BSW-Tasks der ECU-Anwendung mit ihren
Prioritätsschlüsseln in `ecu/asyncCoreConfiguration/tasks.yaml`. rtegen ordnet beide ein und erzeugt `async/Config.h`
und `RteTasks.h` (Format, Prüfungen und Datenkonsistenz: `swc/README.md`, Abschnitt „Tasks“).

## Neues Projekt (TOOL-59)

Ein neues Projekt entsteht im Studio („Neues Projekt …“ auf der Seite Projekt oder im Menü **Projekt**, Strg+N) oder
auf der Kommandozeile mit demselben Kern (`tools/nxconfig/newproject.py`, ohne Qt):

```sh
.venv/Scripts/python tools/nxconfig new --list                                   # Boards und Projekte zum Starten
.venv/Scripts/python tools/nxconfig new <name> --board <board> [--diag] [--dry-run]   # leer für ein Board
    [--safe-state-output <ausgang> --locked-pin <ausgang> --test-pin <pin>]         # Safety-Pins (TOOL-110)
.venv/Scripts/python tools/nxconfig new --safety-candidates --board <board>      # was das Board dafür anbietet
.venv/Scripts/python tools/nxconfig new <name> --copy <projekt> [--dir <ordner>]      # Kopie eines Projekts
```

Ablauf im Studio: **Ablage** → **Start** (leer oder Kopie) → **Name und Ordner** → **Board** bzw. **Vorlage** →
[**Sicherheit**] → **Zusammenfassung** → **Anlegen** → das Projekt öffnet sich. Vor „Anlegen“ wird nichts geschrieben; die
Zusammenfassung nennt jede Datei und jeden abgeleiteten Wert.

- **Ablage:** heute der Entwicklungsfall im neXtBSW-Repository (`doc/architecture/customer-project.md`, Abschnitt 6.4):
  Vorgabe `projects/<name>`, der Ordner ist frei wählbar (neu oder leer, nicht unter `build/`, `external/`, `boards/`,
  `mcudb/`). Das eigene Repository je ECU-Projekt mit fester neXtBSW-Version folgt mit TOOL-60.
- **Name:** Buchstabe, dann Buchstaben, Ziffern oder `_` (höchstens 48 Zeichen); C-Bezeichner und Ordnername zugleich,
  keine C-Schlüsselwörter, keine Windows-Gerätenamen, kein Name eines anderen Projekts in `projects/`.
- **Leer für ein Board** (das Derivat folgt aus dem Board): `project.yaml` und `rte.yaml`, optional eine leere
  `diag.yaml`. Jeder Wert wird aus Board und Derivat abgeleitet und von `nxconfig` bestätigt, nie aus einem Beispielprojekt
  übernommen: Kerntakt (höchster Wert, den das Taktmodell mit Quarz und Bitraten erreicht), Speicheraufteilung (32 KB
  Bootloader ab 256 KB Code-Flash, sonst ein Achtel, mindestens 10 KB, in ganzen Sektoren), Bootloader und
  Diagnoseadressen (`0x7E0`/`0x7E8`) auf dem ersten CAN-Bus, die erste UART als Konsole (115200 Bd, CAN 500 kbit/s),
  Interrupt-Ränge. **Nicht abgeleitet (TOOL-110): der Abschnitt `safety`.** Welcher Ausgang eine Anzeige ist und welcher
  Pin unbeschaltet, steht nur im Schaltplan; der Anwender wählt (Studio-Seite **Sicherheit** ohne Vorauswahl,
  Kommandozeile nur mit allen drei Optionen). Ohne Wahl steht der Abschnitt auskommentiert mit Kandidaten in
  `project.yaml`, und für eine Familie, deren Port ihn liest (S32K1), meldet `nxconfig` einen Fehler bei `safety`.
  `rte.yaml` hat `schema_version: 2`, einen Task mit den Vorgaben von `ecu/asyncCoreConfiguration/tasks.yaml` (10 ms)
  und `components: []`.
- **Kopie eines Projekts:** alle Dateien des Projektordners außer Build-Ausgaben (`build/`, `generated/`, `out/`,
  `__pycache__`, `slprj`, `*_ert_rtw`, `*.slxc`, …). Neu geschrieben werden `project.name`, `project.board`, die Pfade in
  `rte.yaml` und Verweise auf den alten Ordner in Kommentaren; Layout und Kommentare bleiben. Projektspezifische
  Komponenten und die DBC werden mitkopiert, wiederverwendbare (`swc/`) bleiben referenziert. Diagnose-, CAN- und
  XCP-IDs sind die der Vorlage; ändern, wenn beide ECUs einen Bus teilen.

Geschrieben wird in einen Hilfsordner neben dem Ziel, der mit einem Schritt an seinen Platz kommt; bricht das Schreiben ab,
bleibt nichts zurück. Danach prüft `nxconfig check` das Projekt; das Projekt bleibt auch mit Fehlern angelegt.

Grenzen: Boards ohne Board-Port (`ecu/platforms/<board>`) lassen sich prüfen und generieren, aber nicht bauen (ARCH-05,
N5). Die Familienvorlage `s32k1xx` liest die internen ADC-Kanäle `BANDGAP` und `ADCINTERNAL0` und braucht `safety`; das
prüft `nxconfig` noch nicht, der Assistent trägt es ein. Nachweis: der Target-Build-Test `target-new-project`.

## neXtBSW version and workspace pin (ARCH-55, ARCH-56)

The version of this tree is the one line in [`VERSION`](../VERSION) (SemVer `MAJOR.MINOR.PATCH`); the root CMake
(`project(VERSION)`, reported by the application in DID 0xF181), `nxconfig`, the Studio and the release check of
`tools/ci` (tag `vX.Y.Z` must equal it) read that file only. `tools/nxconfig version [<project>]` prints it with
`git describe` and, for a project, the workspace pin found from there.

The projects in this folder are the development case: they have **no `nextbsw.yaml`**, so every tool uses the tree it
runs from - nothing changes for them. A project in a customer repository finds the pin `nextbsw.yaml` by walking up
from `project.yaml` (stopping at a neXtBSW tree or a git repository root); its `search.mcudb`, `search.modules` and
`search.boards` are searched before the folders of the tree. Format and rules:
[`doc/architecture/customer-project.md`](../doc/architecture/customer-project.md), section 3.1. Building such a
workspace outside the tree follows with N4 (ARCH-57).

## Ein Board, mehrere Projekte (IO-04)

Ein Board-Profil beschreibt **Hardware**, ein Projekt ihre **Nutzung** (Architektur Abschnitt 4). Deshalb tragen
mehrere Projekte dasselbe Board, auch wenn sie dieselbe Peripherie **unterschiedlich** nutzen. Beispiel am FlexTimer
FTM0 (schematisch, zwei Projekte auf einem Board mit Motorbrücke und Capture-Eingang):

| | Projekt A (Motor) | Projekt B (Laden) |
|---|---|---|
| FTM0 | PWM-Gruppe (`pwm.<Gruppe>`) | Input Capture (`use.input_capture`) |
| `pwm`-Abschnitt | die Gruppe | **keiner** → `NX_PWM_GROUP_COUNT 0` (ARCH-17) |
| Modulparameter | eigene Werte (`modules.capture`) | eigene Werte |
| Komponente, Task | eigene Komponente und Task | eigene Komponente und Task |
| Board-Profil | identisch – **keine** Sonderliste, kein `exclusive_groups` (ARCH-02) | identisch |

Beides in **einem** Projekt (FTM0 als PWM und als Capture) lehnen `nxconfig check` und `bswgen` mit Datei, Zeile und
YAML-Pfad ab: `FTM0 counter is used by input_capture PILOT_DUTY and by pwm MOT1` (`use.input_capture`). Gleich
bleiben müssen zwischen Projekten desselben Boards nur die Werte, die der **geflashte Bootloader** kennt:
`memory`, `bootloader.can` und `diagnostics` (sonst ist ein Bootloader-Update nötig, ARCH-08/COM-11). Alles andere –
Tasks, Komponenten, genutzte Peripherie, Modulparameter – entscheidet jedes Projekt für sich.

## Bauen

Einmalig je Rechner (Arbeitsplatz-Ebene, nicht eingecheckt): `arm-none-eabi-gcc` in den `PATH` legen oder die
Umgebungsvariable `NX_ARM_GCC_BIN` auf dessen `bin`-Ordner setzen. Standard ist der gepinnte xPack GCC 11.3.1
(`tools/ci/versions.toml`), den `tools/ci` in den Werkzeug-Cache holt:

```sh
.venv/Scripts/python -m tools.ci setup                  # xPack GCC 11.3.1 in den Werkzeug-Cache (NX_CI_CACHE)
setx NX_ARM_GCC_BIN "%LOCALAPPDATA%/NeXtBSW/ci-cache/arm_gcc-11.3.1/xpack-arm-none-eabi-gcc-11.3.1-1.1/bin"   # danach neue Shell
```

Wechselt der Compiler, hält CMake den alten Pfad fest: einmal mit `cmake --preset <name> --fresh` neu konfigurieren
(`tools/test` tut das selbst).

Dann mit dem Preset des Projekts:

```sh
cmake --preset buildtest-s32k148-min && cmake --build --preset buildtest-s32k148-min
```

Ein Projekt braucht **kein** Preset: Das Studio baut jedes Projekt mit dem versteckten Basis-Preset `nx-arm-gcc` und
`-DNX_PROJECT=<project.yaml>` in `<build-root>/<projekt>`. Auf der Kommandozeile gleichwertig: ein vorhandenes Preset mit
anderem Projekt, z. B. `cmake --preset buildtest-s32k148-min -B build/<projekt> -DNX_PROJECT=projects/<projekt>`.
Ein eigenes Preset in `CMakePresets.json` ist nur für CI-Bauprofile nötig. Ein Build-Ordner gehört zu einem Kern und einer
FPU-Variante; bei anderem Kern einen neuen Ordner verwenden.

Host-Tests, die einen echten Target-Build prüfen, erwarten keinen festen Ordner (PLAT-07): `NX_BUILD_DIR`, sonst der
Demo-Build von `tools/test` (`{build}/demo`), sonst das binaryDir des Demo-Presets (Regel in `tools/test/build_dirs.py`,
`tools/test/README.md`, „Build directory“).

Beim Konfigurieren schreibt `tools/nxconfig cmake <projekt> <build>/generated/nxconfig/project.cmake` die Werte
`NX_BOARD`, `NX_DERIVATIVE`, `NX_FAMILY`, `NX_CORE`, Kern-Optionen, Pfade und Debug-Einstellungen. Daraus folgen:

| Wert | Quelle | Verwendung |
|---|---|---|
| Board-Port `ecu/platforms/<board>` | `board.name` | handgeschriebene Integration bis ARCH-05 |
| Plattform der Familie | `derivative.family` | `platforms/<familie>/CMakeLists.txt`, wenn vorhanden (bindet die OpenBSW-Module der Familie selbst ein oder bringt alles mit); sonst `external/openbsw/platforms/<familie>` (ARCH-25) |
| FreeRTOS-Port, CMSIS-Pfade | Architektur `NX_CORE_ARCHITECTURE` aus `derivative.core` (Kern-Tabelle unten) | `platforms/common/3rdparty/freertosPort`; CMSIS-Include-Pfade nur für das M-Profil (ARCH-25) |
| Kern-Optionen `NX_FPU`, `NX_MCPU`, `NX_MFLOAT_ABI`, `NX_MFPU` | `derivative.core`, `derivative.fpu` (Kern-Tabelle unten) | `cmake/toolchains/NxArmNoneEabi-gcc.cmake`, eine Datei für alle Kerne |
| Debug-Probe und Flash-Werkzeuge `NX_DEBUG_PROBE_KIND`, `NX_FLASH_TOOLS` | `board.debug.probe` (ohne Angabe J-Link) | Ziele `flash`, `flash-bootloader`, Abschnitt [Flash-Ziele je Debug-Probe](#flash-ziele-je-debug-probe-plat-18) |
| J-Link-Gerät | `derivative.jlink_device`, sonst `derivative.name` | dito (Probe J-Link) |
| pyOCD-Zieltyp, OpenOCD-Zielskript | `derivative.pyocd_target` (sonst Name klein), `derivative.openocd_target` (ohne Vorgabe) | dito (pyOCD, OpenOCD) |
| Debug-Schnittstelle | `board.debug.interface` (`JTAG`, `SWD`, `cJTAG`) | dito, ohne `board.debug` Ziele mit Fehlermeldung |
| Debug-Takt | `board.debug.speed_khz`, ohne Angabe 1000 kHz | dito |
| Configure-Abhängigkeiten `NX_CONFIG_FILES` | Projekt, Board, Derivat, `rte.yaml`, Pin-Mux-Tabelle des Gehäuses, Derivat des Device-Headers | neu konfigurieren bei Änderung |

Kern-Tabelle (`tools/nxconfig/config.py`, `CORES`). `derivative.fpu` darf fehlen, wenn der Kern nur eine Variante hat;
unbekannte Kerne und unpassende Varianten meldet `nxconfig check` mit YAML-Pfad.

| `derivative.core` | `derivative.fpu` | `-mcpu` | `-mfloat-abi` | `-mfpu` | Architektur | FreeRTOS-Port |
|---|---|---|---|---|---|---|
| `cortex-m0plus` | `none` | `cortex-m0plus` | `soft` | – | `armv6m` | `platforms/s32k1xx/3rdparty/freertos_cm0_sysTick` |
| `cortex-m4f` | `sp` | `cortex-m4` | `hard` | `fpv4-sp-d16` | `armv7m` | OpenBSW CM4F (Ziele der Familienplattform) |
| `cortex-m7f` | `sp` oder `dp` (Pflicht) | `cortex-m7` | `hard` | `fpv5-sp-d16` bzw. `fpv5-d16` | `armv7m` | OpenBSW CM4F (ungeprüft) |
| `cortex-m33` | `sp` oder `none` (Pflicht) | `cortex-m33` | `hard` bzw. `soft` | `fpv5-sp-d16` bzw. – | `armv8m` | fehlt (Abbruch mit Meldung) |
| `cortex-r4f` | `dp` | `cortex-r4f` | `hard` | `vfpv3-d16` | `armv7r` | fehlt (Abbruch mit Meldung; geplant `ARM_CRx_No_GIC` aus FreeRTOS V11.3.1 mit Kern V10.6.2, PLAT-44) |

Die FreeRTOS-Portauswahl richtet sich nach der Architektur, nicht nach dem Kernnamen; fehlt der Port, bricht das
Konfigurieren mit „port missing“ ab. Startup, Fault-Handler und Familienvorlage gibt es für `cortex-m0plus` (S32K116) und
`cortex-m4f` (S32K14x, STM32F446); für die übrigen Kerne gelingt nur das Konfigurieren der Toolchain.

## Flash-Ziele je Debug-Probe (PLAT-18)

Die Ziele `flash` (Anwendung und Gültigkeitsdatensatz) und `flash-bootloader` gibt es für jedes Projekt. Das Board
nennt seine Probe (`board.debug.probe`), `tools/nxconfig` (`config.PROBES`) leitet daraus die möglichen
Implementierungen in Vorzugsreihenfolge ab; welches Werkzeug installiert ist und wo, entscheidet der Arbeitsplatz.

| `board.debug.probe` (Schreibweise frei, Anfang zählt) | Art | Werkzeuge in Vorzugsreihenfolge | Schnittstellen |
|---|---|---|---|
| `J-Link`, `J-Link EDU Mini`, ohne Angabe | `jlink` | `jlink` (J-Link Commander) | JTAG, SWD, cJTAG |
| `ST-LINK`, `ST-LINK/V2-1`, `STLINK-V3` | `stlink` | `stm32cubeprogrammer` (`STM32_Programmer_CLI`), `openocd` | SWD, JTAG |
| `OpenSDA`, `CMSIS-DAP`, `DAPLink` | `cmsis-dap` | `pyocd`, `openocd` | SWD, JTAG |
| `XDS110` | `xds110` | `dslite` (UniFlash-Kommandozeile; Debuggen später über OpenOCD) | JTAG, cJTAG, SWD |

Eine unbekannte Probe oder eine nicht bediente Schnittstelle meldet `nxconfig` als Warnung. OpenSDA geht nur mit
CMSIS-DAP-/DAPLink-Firmware (die PEmicro-Firmware neuer Boards spricht weder pyOCD noch OpenOCD).

Arbeitsplatz-Ebene (nicht eingecheckt), je Werkzeug eine der drei Möglichkeiten: Cache-Variable mit dem Programmpfad,
Umgebungsvariable mit dessen Ordner, oder `PATH`.

| Werkzeug | Cache-Variable | Umgebungsvariable (Ordner) | Zusätzlich nötig |
|---|---|---|---|
| `jlink` | `JLINK_EXE` | `NX_JLINK_BIN` (sonst auch `C:/Program Files/SEGGER/JLink`) | – |
| `stm32cubeprogrammer` | `NX_STM32_PROGRAMMER_CLI` | `NX_STM32_PROGRAMMER_BIN` | – |
| `openocd` | `NX_OPENOCD_EXE` | `NX_OPENOCD_BIN` | `derivative.openocd_target` im Derivat |
| `pyocd` | `NX_PYOCD_EXE` | `NX_PYOCD_BIN` | Zieltyp in pyOCD bekannt (ggf. `pyocd pack install <ziel>`) |
| `dslite` | `NX_DSLITE_EXE` | `NX_UNIFLASH_DIR` | `NX_DSLITE_CCXML`: UniFlash-Zielkonfiguration (XDS110 und Baustein) |

`NX_FLASH_TOOL` (Cache) erzwingt eine Implementierung der Probe (z. B. `-DNX_FLASH_TOOL=openocd` bei einem ST-LINK),
`NX_DEBUG_PROBE_SERIAL` wählt bei mehreren Probes eine aus. Das Konfigurieren nennt das gewählte Werkzeug; fehlt es, entsteht
das Ziel trotzdem und bricht beim Aufruf mit der Begründung ab.

Jede Implementierung lässt die MCU am Ende laufen (Befehlsdatei neben den Abbildern, `<build>/<modul>/<Konfiguration>/<ziel>.<endung>`):

| Werkzeug | Befehl |
|---|---|
| `jlink` | `JLink [-USB <sn>] -ExitOnError 1 -CommandFile flash.jlink` |
| `openocd` | `openocd -f flash.openocd.cfg` (Adapter, Zielskript, je Abbild `flash write_image erase` und `verify_image`, `reset run`) |
| `stm32cubeprogrammer` | `STM32_Programmer_CLI -c port=SWD freq=<kHz> [sn=<sn>] -w <abbild> -v ... -rst` |
| `pyocd` | `pyocd flash -t <ziel> -f <Hz> -O dap_protocol=swd [-u <sn>] <abbilder>` |
| `dslite` | `dslite --mode flash --config=<ccxml> --flash --verify <abbilder>` |

Geprüft ist das gegen die erzeugten Befehle (`tools/nxconfig/test_flash_targets.py`, keine Werkzeuge ausgeführt). Am Board
bestätigt sind der J-Link-Weg und der ST-LINK-Weg am Nucleo F446RE; die übrigen Befehlszeilen sind beim ersten Einsatz zu
prüfen.

## Takte und Timings

Jeder Wert steht in genau einer Ebene, alles Abgeleitete berechnet das Taktmodell (`tools/nxconfig/clock.py` mit den
familienneutralen Rechenkernen `timing.py`); `nxconfig check` meldet Fehler mit YAML-Pfad, bswgen erzeugt daraus
`bsp/clock/boardClock.h`, `bsp/can/canBitTimingConfiguration.h` und `bsp/uart/uartConfiguration.h`.

| Ebene | Eintrag | Beispiel |
|---|---|---|
| Derivat (`mcudb`) | `clock`: interne Oszillatoren, Oszillatorbereich, PLL-Grenzen (Referenz, VCO, Teiler, Multiplikator, Ausgangsteiler), Höchstwerte je Taktbereich | S32K144: FIRC 48 MHz, VCO 180–320 MHz, Kern 80 / Bus 48 / Flash 26,67 MHz |
| Board | `board.clock.sosc_hz`: bestückter Quarz (fehlt: kein Quarz) | 40 MHz |
| Projekt | `clock.system_hz` (Kerntakt); `can.<Bus>`: `bitrate`, `sample_point_percent` (Vorgabe 87,5), optional `sjw_tq`, `prop_seg_tq`; `uart.<UART>`: `baudrate` – Pflicht für jeden genutzten Bus | 80 MHz; CAN0 500 kbit/s, 81,25 %; UART 115200 Bd |

Familie s32k1xx: Systemtaktquelle FIRC, SOSC oder SIRC mit DIVCORE, sonst SPLL; FlexCAN am SOSCDIV2 (braucht Quarz), FTM
am Kerntakt, LPUART/LPSPI am FIRCDIV2. CAN: exakte Bitrate, nächster Abtastpunkt (Warnung ab 1, Fehler ab 2,5
Prozentpunkten Abweichung). UART: kleinster Baudratenfehler (Warnung über 1 %, Fehler über 2 %). Ohne Taktmodell (andere
Familien) prüft nxconfig nur Oszillatorbereich, Kerntakt-Höchstwert und Pflichtangaben.

## Kommunikationskanäle: CAN-Busse der Anwendung (ARCH-07)

Die Anwendung bedient **jeden CAN-Bus des Boards, den das Projekt nutzt** (`use.can`, höchstens 8). Je Bus entstehen Bus-ID
(`busid::<Bus>`), Transceiver-Objekt mit der Rolle `can_phy`, Bit-Timing, Interrupt-Anbindung und ein Eintrag der
Konsolengruppe `can`. Der Busname kommt aus `board.yaml` (`can[].name`, frei wählbar außer `SELFDIAG`, `ETH_0`, `ETH_1`).
Jeder genutzte Bus braucht einen Interrupt-Rang (`interrupts.ranks`), sonst lehnt `nxconfig check` das Projekt ab:
`project.yaml:99 (interrupts.ranks): error: CAN bus CAN0 is used, but has no interrupt rank ...` (ARCH-33; gilt für Derivate
mit Interrupt-Rängen).

| Ebene | Eintrag | Beispiel |
|---|---|---|
| Board | `can[]`: `name`, `instance`, `rx`/`tx`, `phy` (Baustein mit Rolle `can_phy`) | `CAN0: { instance: FlexCAN0, rx: PTC2, tx: PTC3, phy: SBC }` |
| Projekt | `use.can`: Busse der Anwendung; `can.<Bus>`: Bitrate und Abtastpunkt (auch für Busse, die nur der Bootloader nutzt) | `use: { can: [CAN0] }` |
| Projekt | `interrupts.ranks`: ein Interrupt-Rang **je genutztem Bus** (PLAT-21) | `- { source: can, name: CAN0, rank: 3 }` |
| Projekt (`rte.yaml`) | `can.bus`: Bus der DBC-Signale (Vorgabe: erster Bus); `uds.bus`: Bus des Diagnose-Stacks (Vorgabe: `can.bus`) | `can: { bus: CAN0 }`, `uds: { bus: CAN0 }` |
| Projekt (`rte.yaml`) | `can.qualifiers.<Botschaft>`: Überwachung je Botschaft (`timeout_ms`, `debounce_ms`, `on_error`, `e2e`), Qualifier-Ziel `can.qlf.<Botschaft>` (COM-03, [`doc/architecture/signal-qualifier.md`](../doc/architecture/signal-qualifier.md)) | `qualifiers: { PC_Command: { debounce_ms: 200, e2e: { profile: P01, data_id: 0x0210, crc: PcCrc, counter: PcCounter } } }` |

Generat: `busid/busIdConfiguration.h` (SELFDIAG 1, CAN-Busse ab 2, danach Ethernet) und `bsp/can/canDeviceConfiguration.h`
(`NX_CAN_BUSES(X)`, je Bus `<Bus>_DEVICE_CONFIG`), dazu `NX_CAN_PHY_BUSES_<MODUL>` in `integration/moduleConfiguration.h`.
Konsole: `can status [bus]`, `can send [bus] <id> [byte …]`, `can log 0|1 [bus]`. Grenzen: Hat ein Bus im Vektor des
Board-Ports keinen Vektor, bleiben seine Interrupts aus (Fehlermeldung beim Start); die Nachrichtenpuffer-Aufteilung
(14/2/15) setzt 32 Message-Buffer je Instanz voraus.

## PWM-Gruppen (komplementäre Endstufen-PWM, ARCH-17)

Eine PWM-Gruppe ist ein FlexTimer mit komplementären Kanalpaaren (Hoch-/Tiefseite mit Totzeit) für eine
Endstufe. **Eine PWM-Gruppe ist optional:** Ein Board ohne Motorbrücke beschreibt keine, ein Projekt nutzt
keine, und Anwendung und Bootloader bauen und linken trotzdem (Nachweis: Bau-Profil `buildtest_s32k148_min`).

| Ebene | Eintrag | Beispiel |
|---|---|---|
| Board | `pwm[]`: `name`, `instance` (FTM), `mode: complementary`, `active` (Pegel je Seite), `pairs` (Name, `high_side`, `low_side`), `channels` (Name, FTM-Kanal `2k`/`2k+1`, Pin) | `MOT1` auf `FTM0`, drei Paare `MOT1_U/V/W` |
| Projekt | `pwm.<Gruppe>`: `frequency_hz`, `deadtime_ns`, optional `verify` – **nur die Gruppen, die das Projekt nutzt** (nicht über `use:`) | `MOT1: { frequency_hz: 20000, deadtime_ns: 1000 }` |

Abgeleitet (Taktmodell und `tools/bswgen`): FTM-Zählertakt, Vorteiler `SC.PS`, `MOD`, `DEADTIME`, `COMBINE`/`COMP`/`DTEN` je
Paar, `POL` aus den aktiven Pegeln, Pin-Funktion je Kanal. `nxconfig check` meldet mit YAML-Pfad: Paarungsregel `2k`/`2k+1`,
Kanal oder Pin doppelt, Pin ohne FTM-Funktion, FTM-Zähler zugleich von `input_capture` genutzt, Frequenz zu hoch (unter 100
Ticks je Periode) oder zu tief (Vorteiler 128), Totzeit über dem FTM-Maximum (63 x 16 Takte) oder nicht kürzer als eine halbe
Periode (`pwm.<Gruppe>.frequency_hz`, `pwm.<Gruppe>.deadtime_ns`, ARCH-33). Die Mindesttotzeit der Endstufe (`power_stage`,
IO-24) ist eine eigene Prüfung: sie sagt, wie lang die Totzeit sein muss, diese hier, ob der Zähler sie darstellen kann.

Generat `bsp/io/outputPwm/` (`outputPwmConfiguration.h`, `outputPwmConfigurationStrings.h`, `ftmPwmConfiguration.h`); der
letzte Header ist immer einbindbar und nennt die Gruppen:

```c
#define NX_PWM_GROUP_COUNT 1
#define NX_PWM_GROUP_INSTANCES(X) X(MOT1)      /* ohne Gruppe: 0 und eine leere Liste */
```

Ohne Gruppe entsteht kein `ComplementaryPwm` und keine FTM-Registerwerte; die Konsole `pwm` bleibt in jedem Projekt.
Grenze: Der Treiber bedient **eine** Gruppe je Projekt; bei zwei Gruppen bricht bswgen mit einer Meldung ab.

## Speicheraufteilung und Bootloader

Eine Quelle für Bootloader-Linker, Updater-Linker, App-Linker, `bl_config.h` und `canflash` (Architektur Abschnitt 7,
ARCH-08). Rechnung und Prüfung in `tools/nxconfig/memory.py`, Generat von `tools/bswgen/blgen.py`.

| Ebene | Eintrag | Beispiel |
|---|---|---|
| Derivat (`mcudb`) | `memory[]`: Regionen mit `start`, `size`; Flash mit `sector_size` (oder Sektor-Tabelle `sectors: [{size, count}]`) und `program_size` (kleinste programmierbare Einheit) | `PFLASH` 512 KB, Sektor 4 KB, Phrase 8 Byte; `SRAM_L`, `SRAM_U` |
| Projekt | `memory.flash` (Code-Flash-Region), `memory.bootloader`: `size` (ganze Sektoren ab Flash-Anfang), `ram`, optional `ram_size`, `stack_size`; `memory.application`: `ram` (zusammenhängende Regionen in Adressreihenfolge), `noinit_size` | `size: 0x8000`, `ram: SRAM_L`; `ram: [SRAM_L, SRAM_U]`, `noinit_size: 0x400` |
| Projekt | `bootloader.can` (Board-CAN-Bus, Bit-Timing aus `can.<Bus>`), optional `bootloader.init` (Bausteine, die der Bootloader vor dem CAN-Start einschaltet) | `can: CAN0` |

Abgeleitet (Konvention der Bootloader-Schnittstelle, hält einen geflashten Bootloader kompatibel): Anwendung ab Bootloader-Ende
bis zum letzten Sektor, Gültigkeitsrecord im letzten Sektor, No-Init-Bereich am Ende der letzten App-RAM-Region, Boot-Request
0x10 Byte unter dem RAM-Ende, Plausibilitätsbereich des Stack-Pointers = App-RAM. `nxconfig check` meldet mit YAML-Pfad
unbekannte Regionen, Bootloader-Ende innerhalb eines Sektors, keinen Platz für die Anwendung, fehlendes `program_size`,
ungleiche Sektoren (PLAT-16), zu großes oder überlappendes Bootloader-RAM, nicht zusammenhängendes App-RAM, unbekannten
Bootloader-Bus, fehlende Bitrate und fehlende Hooks.

Generat unter `<build>/generated/board/`: `bootloader/bl_memory.h` (auch `BL_TARGET_ID`, BL-02), `bootloader/bl_memory.ld`,
`memory/memoryLayout.h` und `bootloader/bl_diag.h`; beim Konfigurieren des Bootloaders zusätzlich `bl_board.h` und
`bl_board.cmake` (Familie s32k1xx: Reset-Takt, CAN-Instanz, Pins, CTRL1, Baustein-Hooks `bootloader/devices/<modul>`). Anderer
Bus für einen Build: `cmake --preset <p> -DBOOTLOADER_CAN_BUS=CAN1`.

`canflash` findet die Speicheraufteilung und (seit COM-11) die Diagnose-Adressen im Build-Ordner der Hex-Datei, sonst über
`--layout <build>/generated/board/bootloader/bl_memory.h` oder `--project projects/<projekt>/project.yaml` (auch
`NX_PROJECT`/`NX_BL_LAYOUT`); das gilt auch für `canflash info`.

## Diagnose-Adressen (UDS, ISO-TP, Bootloader-Kennung)

CAN-IDs und UDS-Adressen sind Projektsache (Architektur Abschnitt 4). Bootloader (`bootloader/src/core`),
App-UDS-Stack (`ecu/uds`) und `canflash` lesen dieselben generierten Werte, damit ein Tester beide bedient
(COM-11). Prüfung in `tools/nxconfig/diag.py`, Generat von `tools/bswgen/blgen.py`.

| Ebene | Eintrag | Beispiel |
|---|---|---|
| Projekt | `diagnostics.request_id`, `response_id` (Pflicht), `functional_id` (Vorgabe 0x7DF) | `0x7E0`, `0x7E8`, `0x7DF` |
| Projekt | `diagnostics.tester_address`, `ecu_address` (1-Byte-Transportadressen) | `0xF1`, `0x2A` |
| Projekt | `diagnostics.isotp`: `padding`, `block_size`, `stmin_ms`, `n_cr_ms`, `n_bs_ms` | `0xCC`, `0`, `0`, `1000`, `1000` |
| Projekt | `diagnostics.bootloader.version_prefix` (Kennung in `BL_VERSION_STRING`, Vorgabe `nextbsw-bl`; die Versionsnummer steht in `bl_config.h`). Zusätzlich angenommen wird die frühere Kennung `BL_VERSION_PREFIX_FORMER` (`S32K14x-BL`, `bl_config.h`, PLAT-08) - von Anwendung (DID 0xF180), Updater und `canflash`. Verglichen wird byteweise, also mit Groß-/Kleinschreibung (PLAT-51): `NEXTBSW-BL` oder `s32k14x-bl` gelten als fremde Kennung | `nextbsw-bl` → `nextbsw-bl 0.1.0` |
| Projekt | `diagnostics.bootloader.scan_start` (optional): erste Adresse, die die Anwendung nach der Kennung durchsucht | entfällt |
| Projekt | `diagnostics.bootloader.security` (BL-03, optional): `method` (heute nur `hmac-sha256`), `key` (Name des Hauptschlüssels am Arbeitsplatz, Vorgabe: Boardname), `allow_insecure_test_key` (nur Testprofile) | entfällt → `hmac-sha256`, Schlüssel = Boardname |
| Derivat (`mcudb`) | `uid`: Adresse und Länge der Chip-UID (BL-03, Geräteschlüssel und DID 0xF18C) | S32K1xx `0x40048054`, 16 Byte |
| Familie | Flash am Anfang der Region, der nicht durchsucht wird (`diag.FAMILY_SCAN_OFFSET`): s32k1xx 0x410 (Vektortabelle + Flash Configuration Field) | `0x410` |
| ECU-Beschreibung (`rte.yaml`) | `uds.bus`: CAN-Bus des App-Stacks (ARCH-07); der Bootloader hört auf `bootloader.can` | `CAN0` |

`nxconfig check` meldet mit Datei, Zeile und YAML-Pfad: IDs außerhalb des 11-Bit-Bereichs, gleiche IDs, Kollision mit einer
Botschaft der DBC auf demselben Bus, Suchbereich außerhalb des Bootloader-Flash, STmin über 127 ms, N_Cr/N_Bs kürzer als STmin;
als Warnung: eine ID auf dem Statusrahmen des Updaters (0x7EE), sehr langsames STmin, Bootloader und Anwendung auf
verschiedenen Bussen.

Generat `<build>/generated/board/bootloader/bl_diag.h`: `BL_CAN_ID_REQUEST_PHYS/_FUNC/_RESPONSE`,
`BL_ISOTP_PADDING/_BLOCK_SIZE/_STMIN_MS/_N_CR_MS/_N_BS_MS`, `BL_VERSION_PREFIX`, `NX_DIAG_TESTER_ADDRESS/_ECU_ADDRESS`,
`NX_DIAG_BL_SCAN_START/_END`. Nicht konfigurierbar sind die Data- und Routine-Identifier der UDS-Dienste: Protokollkonstanten,
auf die sich Bootloader, Anwendung (`ecu/uds/.../AppDataIdentifiers.h`) und `canflash` einigen.

**Bootloader security access and identification (BL-03, BL-10).** `blgen` writes `bootloader/bl_security.h` (method, chip UID
address and length, name of the master key - never key material) and the key sector `BL_KEY_ADDR`/`BL_KEY_AREA_SIZE` into
`bl_memory.h` (the sector below the validity record). The master key is a workstation value
(`%LOCALAPPDATA%/neXtBSW/keys/<name>.key`, or `NX_BL_KEY`/`NX_BL_KEY_FILE` for CI); `canflash keygen <name>` creates one,
`tools/canflash/provision.py` writes the device key of one chip. A project that names the insecure test key without
`allow_insecure_test_key: true` fails in `nxconfig check`. `bl_memory.h` also carries `BL_HARDWARE_ID` and `BL_FAMILY`; a
build directory generated before BL-10 must be configured again.

## Module: Bausteine, Anschlüsse, Parameter

Wiederverwendbare Treiber beschreiben sich in `libs/bsw/<modul>/module.yaml` (Architektur Abschnitt 5). Welche Werte
wo stehen:

| Ebene | Eintrag | Beispiel |
|---|---|---|
| Modul (`module.yaml`) | bediente Teile `parts`, Anschlüsse `connect` mit Art (`digital_output`, `digital_input`, `analog_input`), Parameterschema `parameters` (Typ `int`/`bool`/`str`, `min`/`max` Pflicht bei `int`, `pattern`, `default`, `unit`), Regeln `constraints` (`fault_ma > overcurrent_ma`) | `highSideSwitch` |
| Board (`board.yaml`) | `devices[].module` (optional, sonst über `parts`) und `devices[].connect`: Anschluss → Board-Eintrag | `KL30B_SW: { module: highSideSwitch, connect: { in: EN_SW_KL30B, den: DIAG_EN_SW_KL30B, is: CURRENTSENSE_KL30B } }` |
| Projekt (`project.yaml`) | `modules.<Baustein>`: Parameterwerte, `verify: true` für unbestätigte Werte | `modules: { KL30B_SW: { overcurrent_ma: 3633, …, console: kl30b, verify: true } }` |

Instanziert wird ein Baustein mit Bus (`spi[].device`, `can[].phy`), wenn das Projekt einen seiner Busse nutzt; ein
Baustein nur mit `connect`, wenn `modules.<Baustein>` im Projekt steht. `nxconfig check` meldet mit Datei, Zeile und
YAML-Pfad: unbekanntes oder mehrdeutiges Modul, Teil nicht vom Modul bedient, fehlende/unbekannte Anschlüsse, Anschluss
an einen Eintrag falscher Art oder doppelt belegt, unbekannte, fehlende oder falsch getypte Parameter, Bereich, Muster,
verletzte `constraints`, doppelte Konsolennamen, Manifestfehler. Nutzt das Projekt ein angeschlossenes Signal nicht
(`use:`), gibt es eine Warnung und die Instanz entfällt.

**Module ohne Baustein** (Dienste wie `ecuState`, `bspAdcAccess`, `safeSystem`) setzen `project_parameters: true` im
Manifest; ihre Werte stehen einmal je Projekt unter `modules.<modul>`, fehlende kommen aus den Standardwerten. Ein
`str`-Parameter mit `names:` ist eine Namensliste (`all`, `none` oder kommagetrennt, kein Name doppelt); bei
`names: can_bus` muss jeder Name ein CAN-Bus aus `use.can` sein, bei `names: any` genügt das Muster.

### ECU-Zustandsverwaltung (`modules.ecuState`, SYS-01/SYS-02/SYS-08)

Zeiten, Richtlinien und Aufweckquellen der Zustandsverwaltung sind Projektwerte unter `modules.ecuState`; ohne Eintrag gelten
die Standardwerte. Parameter, Standardwerte und Bereiche: [`ecuState.md`, Parameter](../libs/bsw/ecuState/doc/ecuState.md#parameter),
alle Felder: Referenz `doc/reference/project.md`. `nxconfig check` meldet mit YAML-Pfad, z. B.
`project.yaml:170 (modules.ecuState.wakeup_can): error: wakeup_can: CAN7 is no CAN bus of the project (use.can: CAN0)`.
Ein Abschnitt `ecu_state:` in der `rte.yaml` ist ein Fehler von rtegen. Aus den geprüften Werten entsteht
`generated/rte/EcuStateConfig.h`; die Versorgung wählt die Integration aus den Bausteinen (`sbcUja113x`: dessen
`Uja113xPowerSupply`, sonst `PermanentPowerSupply`). Die RTE-Ziele `ecu.*` beschreibt `swc/README.md`.

bswgen erzeugt daraus `generated/board/integration/`: `moduleConfiguration.h` (Instanzen je Modul, CAN-Rollen),
`<modul>Configuration.h` je Modul mit Anschlüssen oder Parametern (`NX_MODULE_<MODUL>_INSTANCES(X)`,
`NX_DEVICE_<BAUSTEIN>_IO_<ANSCHLUSS>`, `_PARAM_<PARAMETER>`, `_PART`, `_LABEL`) und `modules.cmake`. Die
Integration im Board-Port setzt die Instanzen per X-Makro zusammen; Werte stehen dort keine.
