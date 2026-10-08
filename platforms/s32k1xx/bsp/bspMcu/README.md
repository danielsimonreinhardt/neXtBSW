# bspMcu (Familie s32k1xx) – Derivat-Header und `mcu/mcu.h` (PLAT-11)

Ergänzung zum OpenBSW-Modul `external/openbsw/platforms/s32k1xx/bsp/bspMcu`. OpenBSW liefert dort genau **einen**
Derivat-Header (`3rdparty/nxp/S32K148.h`) und eine `mcu/mcu.h`, die ihn fest einbindet. Bis PLAT-23 lag je
Board-Port eine Kopie dieser Datei (`ecu/platforms/<board>/mcu/include/mcu/mcu.h`, davor mit
`#undef PCC_<Instanz>_INDEX`-Kette). Für ein anderes Derivat mit anderem Kern und anderen IRQ-Nummern
(S32K116, Cortex-M0+) trägt das nicht.

**Seit PLAT-23 ist die `mcu/mcu.h` dieses Moduls die einzige der Familie:** die Wurzel-`CMakeLists.txt` stellt
`platforms/<familie>/bsp/bspMcu/include` dem OpenBSW-Modul voran (kein Board-Name mehr im Include-Pfad).

## Konzept: eine Familien-`mcu.h`, Derivat-Fakten aus der Datenbank

```
mcudb/s32k1xx/<Derivat>.yaml            device_header: Datei + für welches Derivat sie geschrieben ist
        |                               (ARCH-03; S32K144 -> S32K148.h, S32K116 -> S32K116.h)
        v
tools/bswgen  ->  mcu/mcuDerivative.h   Include des Hersteller-Headers, plus #undef nur für Peripherie,
        |                               die das Derivat nicht hat (nur nötig, wenn der Header ein größeres
        |                               Derivat beschreibt; beim S32K116 entfällt die Kette ganz)
        v
platforms/s32k1xx/bsp/bspMcu/include/mcu/mcu.h
        - bindet mcu/mcuDerivative.h ein (kein Derivatname im Code)
        - wählt den CMSIS-Kern-Header aus der Zielarchitektur des Compilers:
          __ARM_ARCH_6M__ -> core_cm0plus.h (platforms/common/3rdparty/cmsis, PLAT-10),
          __ARM_ARCH_7EM__ -> core_cm4.h (OpenBSW); passt der Header nicht zum Kern, bricht der Build ab
        - FEATURE_NVIC_PRIO_BITS = __NVIC_PRIO_BITS des Headers (Prüfung gegen mcudb: PLAT-10,
          platforms/common/interruptPriority)
        - OpenBSW-Kurznamen (LMEM, WDOG, FTM0, PCC, ...) nur per #ifdef IP_<Instanz>, also nur für Peripherie,
          die dieses Derivat wirklich hat
```

Damit gibt es keine `#undef`-Kette mehr: Was der Derivat-Header nicht kennt, wird auch nicht umbenannt, und die
Umbenennungen sind familienweit dieselbe Datei.

CMake: `nxBspMcu` (INTERFACE) setzt die Include-Reihenfolge; `NX_DEVICE_HEADER_DIR` zeigt auf den Ordner mit dem
Hersteller-Header (`3rdparty/nxp/<Derivat>.h`), `NX_GENERATED_INCLUDE_DIR` auf das Generat von bswgen. Ohne
`NX_DEVICE_HEADER_DIR` gilt der OpenBSW-Ordner (S32K148-Header, BSD-3-Clause).

## Derivat-Header S32K116

Seit PLAT-32 (Nutzerentscheidung 2026-09-16) ist der S32K116-Header **eingecheckt** und braucht keine
Arbeitsplatzdateien:

```
include/3rdparty/nxp/rtd/S32K116*.h    NXP-RTD-Header aus Zephyr hal_nxp @ bb740b2, unverändert, BSD-3-Clause
                                       (Herkunft, Lizenztext, Zephyr-Patches: include/3rdparty/nxp/rtd/README.md)
include/3rdparty/nxp/S32K116.h         Anpassungsschicht, erzeugt mit mcudb/tools/rtd_adapter.py aus
                                       mcudb/tools/device_header_rules/s32k1xx_rtd.yaml
```

Die Anpassungsschicht bildet die Stellen ab, an denen die RTD-Header anders heißen als der `S32K148.h`, gegen den die
OpenBSW-Treiber geschrieben sind – je Modul, nicht je genutztem Symbol, und ohne Eingriff in die Treiber:

| Stelle | RTD (hal_nxp) | Treiber erwarten | Lösung |
|---|---|---|---|
| FlexCAN | `CAN_Type`, `CAN_*`, `MB[32]` (Zephyr-Patch für MCUX) | `FLEXCAN_Type`, `FLEXCAN_*`, `RAMn[]` | Layout-Kopie mit `RAMn[CAN_RAMn_COUNT]` (gleiche 0x200 Byte), Makro-Aliase |
| ADC | `ADC_SC1_*` | `ADC_SC1A_*` | Makro-Aliase |
| ERM | `EAR[]` | `EARn[].EAR` | Layout-Kopie |
| GPIO | ohne `PIDR` | `PIDR` (bspIo) | Layout-Kopie mit `PIDR` bei 0x18 ([RM] 13.3.1.1) |
| `__NVIC_PRIO_BITS` | 4 | 2 (Cortex-M0+, [RM] 8.2.1) | korrigiert |
| LMEM | beschrieben | S32K11x hat keinen ([RM] 33.1) | `IP_LMEM*` entfernt |
| `SIM_CHIPCTL_PDB_BB_SEL_MASK` | fehlt (Bit beim S32K11x reserviert) | `bsp/adc/AnalogInput.cpp` löscht das Bit | kein Makro: der Port löscht das Bit nur `#ifdef` (PLAT-48) |

Layout-Kopien behalten Offsets und Größe des Herstellerlayouts (vom Generator gegen das RTD-Layout geprüft); der
Herstellertypname wird auf die Kopie umgeleitet (`#define GPIO_Type NX_RTD_GPIO_Type`), damit die unveränderten
Instanz-Makros (`IP_PTA`, `GPIO_BASE_PTRS`, `IP_FLEXCAN0`) auf sie zeigen. `rtd_adapter.py check` prüft SHA-256 der
Herstellerdateien, Aktualität der Anpassungsschicht und dass **jedes** Registersymbol, das die s32k1xx-Treiber nutzen
(`svd2header.used_symbols`), aufgelöst wird oder mit Grund als beim Derivat nicht vorhanden eingetragen ist.

Der frühere SVD-Weg (`mcudb/tools/svd2header.py`, `NX_SVD_DIR`, PLAT-11/12) bleibt als **optionale Gegenprobe**:
`test/test_device_header.py` (`SvdCrossCheckTest`) vergleicht mit gesetztem `NX_SVD_DIR` jedes von den Treibern genutzte
Symbol beider Header (Offsets, Größen, Makrowerte, Interruptnummern).

## Test

`test/test_device_header.py` übersetzt OpenBSW-Treiberquellen für `-mcpu=cortex-m0plus` gegen den eingecheckten
Header, ohne Board-Port (Taktwerte als Testdaten im Test). Zwei Tests halten Befunde für PLAT-12 fest:

| Stelle | Befund |
|---|---|
| `bspCore/src/cache/cache.cpp` | nutzt `LMEM` unbedingt; der S32K116 hat keinen LMEM (mcudb-Feature `lmem_cache`) |
| `safety/safeBspMcuWatchdog/.../Watchdog.cpp` | nutzt `WDOG_EWM_IRQn`; beim S32K116 heißt der Interrupt `WDOG_IRQn` |
| `bspClock/src/clockConfig.cpp` | `PLL_AVAILABLE`-Schalter ist unvollständig: `pllOff()` ruft `isSpllSysClk()`/`spllStop()` auch ohne SPLL (Meldung an OpenBSW, PLAT-03) |

Aufruf:

```sh
.venv/Scripts/python -m unittest platforms/s32k1xx/bsp/bspMcu/test/test_device_header.py
```

(braucht nur `arm-none-eabi-g++`; die SVD-Gegenprobe läuft zusätzlich, wenn `NX_SVD_DIR` gesetzt ist.) Die
Anpassungsschicht selbst prüft `mcudb/tools/test_rtd_adapter.py`.
