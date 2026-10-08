# Board-Ports (`ecu/platforms/<board>`)

Ein Board-Port ist **nur noch das, was dieses Board von anderen Boards seiner Familie unterscheidet** - und seit
**ARCH-58** ist das in aller Regel *nichts*: ein Board braucht überhaupt keinen Board-Port mehr, um gebaut zu
werden. Der handgeschriebene Port selbst ist seit **PLAT-23** eine Familienvorlage unter `platforms/<familie>/port`
(Befund ARCH-09 B6, Architektur Abschnitte 5 und 8):

| Ort | Inhalt |
|---|---|
| `platforms/<familie>/port/bspConfiguration` | L1-Konfiguration der Familie (ADC, CAN, UART, SystemTimer, stdIo, PWM) |
| `platforms/<familie>/port/safety` | `safeLifecycle`, `safeMemory` (NXP-MPU), `safeSystem`, `safeIo`, `adcSafetyConsole` |
| `platforms/<familie>/port/main` | `startUp.S`, `bootHeader.S`, Linker-Vorlage, ISRs, `StaticBsp`, `BspSystem`, Integration |
| `platforms/<familie>/port/freeRtosCoreConfiguration` | `FreeRtosPlatformConfig.h` der Familie |
| `platforms/<familie>/port/port.yaml` | was die Vorlage vom Projekt, vom Board und vom Derivat **braucht** (PLAT-95) |
| `platforms/<familie>/bsp/bspMcu` | `mcu/mcu.h` der Familie (eine Datei, kein Board-Name im Include-Pfad) |
| `ecu/platforms/<familie>/features.yaml` | was die Vorlage **bietet**: Funktionsschalter, die nur der Port beantworten kann (Watchdog, MPU) - ARCH-58 |
| `ecu/platforms/<board>/Options.cmake` | **optional**: begründete Ausnahme dieses Boards; wird nach den abgeleiteten Schaltern eingebunden und darf einen überschreiben |
| `ecu/platforms/<board>/CMakeLists.txt` | **optional**: Board-Eigenheiten und begründete Ausnahmen |

## Funktionsschalter (`PLATFORM_SUPPORT_*`, ARCH-58)

Bis ARCH-58 setzte jedes baubare Board die Schalter von Hand in `ecu/platforms/<board>/Options.cmake`, und die
Wurzel-`CMakeLists.txt` verweigerte ein Board ohne diese Datei. Für ein Kunden-Board war das eine Pflichtdatei an
einer Stelle, die dem Kunden nicht gehört, und ihr Inhalt wiederholte, was `project.yaml` schon sagt (Architektur
Abschnitt 9, Zeile 2). Jetzt werden sie abgeleitet - `tools/nxconfig/features.py` schreibt sie als
`NX_FEATURE_<NAME>` samt Begründung ins Generat, die Wurzel-`CMakeLists.txt` setzt daraus `PLATFORM_SUPPORT_<NAME>`:

| Schalter | Ebene | Quelle |
|---|---|---|
| `OPENBSW_PLATFORM` | Derivat | die Familie des Derivats (`NX_FAMILY`) |
| `IO` | Projekt | das Projekt nutzt mindestens einen I/O-Eintrag des Boards (`use.dio`, `use.adc`, `use.input_capture`, eine `pwm`-Gruppe) oder einen internen ADC-Kanal (`adc_internal`) |
| `CAN` | Projekt | das Projekt nutzt mindestens einen CAN-Bus des Boards (`use.can`) |
| `TRANSPORT` | Projekt | das Projekt hat einen Abschnitt `diagnostics` (die Transportschicht trägt den Diagnosestapel; DoIP kommt mit Ethernet dazu) |
| `UDS` | Projekt | das Projekt hat einen Abschnitt `diagnostics` |
| `ETHERNET` | Bau-Option | `NX_ETHERNET` (PLAT-56), gegen das Derivat geprüft - keine Board-Aussage |
| `WATCHDOG`, `MPU` | Familien-Port | `ecu/platforms/<familie>/features.yaml`: hat der Port den Treiber überhaupt? |

Nicht abgeleitet und deshalb nirgends gesetzt: `STORAGE`, `ROM_CHECK`, `MIDDLEWARE` und die übrigen Schalter der
OpenBSW-Referenzanwendung. Sie haben in neXtBSW keinen Leser (nur `external/openbsw/executables/referenceApp`, das
dieser Baum nicht baut) und standen in jedem Board-Port auf `OFF`.

**Kein Board-Port zeigt auf einen anderen Board-Port.** Wer für ein neues Board Code aus einem anderen Board-Port
braucht, hat einen Architektur-Befund gefunden: melden (Prüffragen, Architektur Abschnitt 11), nicht kopieren und
nicht per `add_subdirectory` einbinden.

## Was ein Board wirklich unterscheidet

Diese Unterschiede kommen heute **ohne Codekopie** aus der Konfiguration (Derivat `mcudb/<familie>/<derivat>.yaml`,
Board `boards/<board>/board.yaml`, Projekt `projects/<projekt>/project.yaml`) und aus dem Generat von
`tools/bswgen`/`tools/blgen`. Sie sind die Grundlage für das Board-Profil ohne Codekopie (ARCH-09/PLAT-12):

| Unterschied | Quelle | Weg in den Code |
|---|---|---|
| Linkerskript-Werte (Flash-/RAM-Bereiche, Bootloader-/App-Aufteilung, Gültigkeitsrecord, No-Init-Bereich) | Derivat (Größen) + Projekt (`memory`) | `memory/memoryLayout.h` → `main/linkerscript/application.dld.in` (ARCH-08, BL-05) |
| Pins und Pin-Funktionen (Konsole, CAN, LEDs, Taster, ADC-Kanäle, PWM-Ausgänge, SPI/CS/INT) | Board (`pins`, `devices`, `can`) + Derivat (Pin-Mux-Tabelle je Gehäuse) | `bsp/io/io/ioConfiguration.h`, `bsp/adc/*`, `bsp/io/outputPwm/ftmPwmConfiguration.h` |
| Bestückte Module (SBC ja/nein, passiver Transceiver, High-Side-Schalter, Gate-Treiber) | Board (`devices` mit `module`) + Projekt (`modules`) | `integration/modules.cmake` (`NX_MODULES`), `integration/moduleConfiguration.h` (ARCH-05/ARCH-12) |
| Takt (Quarz, Oszillator-Bereich, PLL, Kern-/Bus-/Peripherietakte, CAN-Bit-Timing, UART-Teiler) | Board (`clock.sosc_hz`) + Projekt (Systemtakt, Bitraten, Baudrate) | `bsp/clock/boardClock.h`, `bsp/can/canBitTimingConfiguration.h`, `bsp/uart/uartConfiguration.h` (ARCH-06, ARCH-13, ARCH-15) |
| Debug-Schnittstelle (J-Link-Gerät, SWD/JTAG, Takt) | Board (`debug`) + Derivat (`jlink_device`) | `NX_JLINK_*` der Wurzel-`CMakeLists.txt`, Flash-Ziele |
| Derivat-Fakten (MPU-Regionen, NVIC-Prioritätsbits, Peripherie-Instanzen, Gehäuse) | Derivat | `mcu/derivativeFacts.h`, `mcu/mcuDerivative.h`, `interrupts/interruptPriorities.h` (ARCH-03, ARCH-16) |

Bleibt ein Unterschied übrig, der **Code** braucht (z. B. die Umgehung eines Platinenfehlers), gehört er als
eigenes Modul mit Manifest in den Board-Port und wird im Changelog begründet (Architektur Abschnitt 3, Regel 2).
