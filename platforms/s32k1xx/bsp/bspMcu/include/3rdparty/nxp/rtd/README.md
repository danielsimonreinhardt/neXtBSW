# NXP-RTD-Derivat-Header S32K116 aus Zephyr hal_nxp (PLAT-32)

**Weitergabe: ja** (BSD-3-Clause). Entscheidung des Nutzers 2026-09-16 (Option b aus PLAT-11): Der S32K116-Derivat-Header
kommt aus den NXP Real-Time Drivers in Zephyr `hal_nxp`, nicht mehr aus der SVD des Keil-Pakets (deren Lizenzlage
widersprüchlich ist, `mcudb/s32k1xx/sources/keil_s32k116_dfp/README.md`).

## Herkunft

| Feld | Wert |
|---|---|
| Repository | <https://github.com/zephyrproject-rtos/hal_nxp> |
| Commit | `bb740b2e6dd0a192130caf76d45f1a8f40050346` (2026-09-09; derselbe Stand wie die Pinmux-Daten, `mcudb/s32k1xx/sources/hal_nxp/README.md`) |
| Ordner | `s32/drivers/s32k1/BaseNXP/header/` |
| Dateien | alle 40 `S32K116*.h` (`S32K116.h`, `S32K116_COMMON.h` und 38 Modul-Header), **unverändert** |
| Stand laut Dateikopf | `@version 1.1`, `@date 2022-01-21`; Import in hal_nxp „Based on S32K1_S32M24_-_R21-11_RTD_2_0_0_D2308“ |
| Abgerufen | 2026-09-17 (PLAT-32), `raw.githubusercontent.com/zephyrproject-rtos/hal_nxp/<Commit>/…` |
| SHA-256 je Datei | `mcudb/tools/device_header_rules/s32k1xx_rtd.yaml`, `derivatives.S32K116.files` (von `rtd_adapter.py check` geprüft) |

## Lizenz

- Jede der 40 Dateien trägt `Copyright 1997-2016 Freescale Semiconductor, Inc.`, `Copyright 2016-2021 NXP` bzw.
  `Copyright 2016-2022 NXP` und `SPDX-License-Identifier: BSD-3-Clause` (geprüft für alle 40 Dateien, keine andere
  Kennung). Anders als die hal_nxp-Pinmux-Daten (Apache-2.0, PLAT-46) sind diese Header BSD-3-Clause.
- Lizenztext: `LICENSE.BSD-3`, unverändert aus der Wurzel von hal_nxp (gleicher Commit),
  SHA-256 `85fe643c33c6ea5d81a6c5db68cbb2fdcc811a63233febf367a2574eb1c5d9b3`.
- `s32/README` von hal_nxp nennt für den Ordner `License: BSD-3-Clause`.
- **Eigene Datei in diesem Ordner:** `BasicTypes.h` (Apache-2.0, neXtBSW). Die RTD-Header binden `"BasicTypes.h"` des
  RTD-Basispakets nur für die `uint*_t`-Typen ein; der Ersatz bindet `<stdint.h>` ein und zieht keine RTD-Plattformtypen
  (`PlatformTypes.h`, `Std_Types.h`) in den Build.

## Änderungen durch Zephyr gegenüber NXP RTD (laut `s32/README`, „Patch List for S32K1“, und Commit-Historie)

| Patch | Folge für neXtBSW |
|---|---|
| `__MPU_PRESENT` 0 (S32K1 hat die NXP-SYSMPU, keine Arm-MPU) | passt: `mcu/mcu.h` bindet `core_cm0plus.h` ohne Arm-MPU-Typen ein |
| FTM `CONTROLS[].CSC/CV` → `CnSC/CnV` | passt zu den OpenBSW-Treibern (Namen wie `S32K148.h`) |
| FlexCAN `FLEXCAN_` → `CAN_`, `RAMn[]` → `MB[]`, Hilfsmakros `MCR_WAKSRC` u. a. ergänzt (im S32K1 reserviert) | Anpassungsschicht bildet `FLEXCAN_Type`/`FLEXCAN_*`/`RAMn[]` zurück |

## Nutzung

Die Treiber binden die Header nicht direkt ein, sondern `3rdparty/nxp/S32K116.h` eine Ebene höher: eine mit
`mcudb/tools/rtd_adapter.py` erzeugte, eingecheckte Anpassungsschicht (Regeln `mcudb/tools/device_header_rules/s32k1xx_rtd.yaml`)
mit Namen im Stil von `S32K148.h` (`FLEXCAN_*`, `ADC_SC1A_*`, `ERM EARn[].EAR`, `GPIO PIDR`), Korrektur
`__NVIC_PRIO_BITS` 4 → 2 und ohne die LMEM-Instanz, die der S32K11x nicht hat. Neu erzeugen und prüfen:

```sh
.venv/Scripts/python mcudb/tools/rtd_adapter.py generate --derivative S32K116
.venv/Scripts/python mcudb/tools/rtd_adapter.py check --derivative S32K116     # Dateien, Aktualität, Treiber-Symbole
```

Neue Dateien aus hal_nxp (anderer Commit, weiteres Derivat) nur zusammen mit neuen SHA-256 in den Regeln und einer
Zeile in dieser README übernehmen.
