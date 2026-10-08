# S32K116.svd – optionale Gegenprobe des S32K116-Derivat-Headers (PLAT-11, seit PLAT-32)

> **Seit PLAT-32 (2026-09-17) nicht mehr Quelle des Builds.** Der S32K116-Header ist eingecheckt: NXP-RTD-Header aus
> Zephyr hal_nxp (BSD-3-Clause) mit Anpassungsschicht, `platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/rtd/README.md`.
> Build, CI und alle Tests laufen ohne `NX_SVD_DIR`. Die SVD dient nur noch als unabhängige zweite NXP-Quelle:
> `platforms/s32k1xx/bsp/bspMcu/test/test_device_header.py` (`SvdCrossCheckTest`) vergleicht mit gesetztem `NX_SVD_DIR`
> jedes von den Treibern genutzte Symbol beider Header. Der Rest dieser Seite beschreibt die Einrichtung dafür.

**Weitergabe: nein** – in diesem Ordner liegt nur diese Anleitung. Die SVD-Datei bleibt auf **Arbeitsplatz-Ebene**
(Architektur Abschnitt 4), weil ihre Lizenzlage nicht eindeutig ist (siehe Lizenzbefund). Ebenso der daraus erzeugte
Header `S32K116.h`: er wird beim Bauen bzw. von Hand erzeugt und nicht eingecheckt.

## Herkunft

| Feld | Wert |
|---|---|
| Paket | `Keil.S32K116_SDK_DFP` Version **1.2.1** (2024-02-12, vom Hersteller abgekündigt: „no longer maintained“) |
| Adresse | <https://www.keil.com/pack/Keil.S32K116_SDK_DFP.1.2.1.pack> (CMSIS-Pack-Index <https://www.keil.com/pack/index.pidx>) |
| Datei im Paket | `platform/devices/S32K116/S32K116.svd` |
| SVD-Version | `<name>S32K116</name>`, `<version>1.6</version>`, CMSIS-SVD-Schema 1.1, `<cpu>` `CM0PLUS`, `nvicPrioBits` 2 |
| SHA-256 der SVD | `3e1865846ea598ad68fa86564f9ef3135e0ab1187a26ca242119c400b2986aa9` |
| Abgerufen | 2026-09-16 (PLAT-11) |

Dieselbe SHA-256 steht in `mcudb/tools/device_header_rules/s32k1xx.yaml` (`derivatives.S32K116.svd.sha256`); der Generator
bricht ab, wenn eine andere Fassung untergeschoben wird.

## Lizenzbefund (2026-09-16, keine Rechtsauskunft)

- Die SVD-Datei selbst trägt in `<licenseText>` den **BSD-3-Clause-Text von Freescale/NXP** („Redistribution and use in
  source and binary forms … are permitted“), **aber ohne Copyright-Zeile**.
- Das Paket verweist mit `<license>LICENSE</license>` auf eine Sammellizenz:
  `SPDX-License-Identifier: Apache-2.0 AND LA_OPT_NXP_Software_License`, wobei „Software Development Kit (SDK)“ – und
  dort liegt die SVD-Datei (`platform/devices/…`) – unter **LA_OPT_NXP_Software_License v24 (June 2021)** steht. Diese
  erlaubt Weitergabe nur mit ausdrücklicher „Additional Distribution License“ und verlangt Vertraulichkeit (Abschnitt
  2.2, 2.3, 16). Die C-Header im selben Paketordner (`S32K116.h`, `S32K116_features.h`) tragen entsprechend einen
  Vertraulichkeitsvermerk von NXP. Sie liegen nicht im Repo; dieser Ordner enthält nur diese Anleitung.
- **Folge:** Datei- und Paketlizenz widersprechen sich. Wir checken die SVD **nicht** ein und behandeln sie wie die
  S32DS-Kopie (PLAT-02-Muster: Hersteller-Datei bleibt lokal). Weitergegeben wird nur, was wir selbst geschrieben haben
  (Generator und Regeldatei).
- **Entschieden 2026-09-16 (Nutzer), umgesetzt mit PLAT-32:** zweite Option (RTD-Header aus hal_nxp). Ursprüngliche
  Fragestellung: NXP nach einer BSD-3-Fassung der
  S32K11x-Header/SVD fragen (für S32K148 gibt es sie: `S32K148.h`/`S32K148_M4.svd` rev. 1.1a, BSD-3-Clause, im
  OpenBSW-Submodul) **oder** die BSD-3-Kopie der RTD-Header aus Zephyr `hal_nxp`
  (`s32/drivers/s32k1/BaseNXP/header/S32K116*.h`, `SPDX-License-Identifier: BSD-3-Clause`) einchecken. Letztere sind
  von Zephyr für die MCUX-Treiber umbenannt (FTM `CnSC` → `CSC`, FlexCAN, ADC) und passen deshalb nicht ohne
  Anpassung zu den OpenBSW-Treibern. (Befund PLAT-32: beim S32K116 betrifft das FlexCAN; FTM heißt dort bereits
  `CnSC`, die ADC-Umbenennung gilt nur für S32K148. Die Anpassung übernimmt `mcudb/tools/rtd_adapter.py`.)

## Einrichtung am Arbeitsplatz

1. Paket herunterladen und die Lizenz (`LICENSE` im Paket) selbst prüfen; das Paket ist eine ZIP-Datei:

   ```sh
   curl -LO https://www.keil.com/pack/Keil.S32K116_SDK_DFP.1.2.1.pack
   unzip -o Keil.S32K116_SDK_DFP.1.2.1.pack platform/devices/S32K116/S32K116.svd -d .
   ```

2. Die SVD an einen Ort außerhalb des Repositorys legen und `NX_SVD_DIR` darauf zeigen lassen
   (auf diesem Rechner: `%LOCALAPPDATA%\neXtBSW\svd\S32K116.svd`):

   ```sh
   setx NX_SVD_DIR "%LOCALAPPDATA%\neXtBSW\svd"
   sha256sum "%NX_SVD_DIR%/S32K116.svd"      # muss zur Tabelle oben passen
   ```

3. Header erzeugen (Ausgabeordner ebenfalls außerhalb des Repositorys oder im Build-Verzeichnis):

   ```sh
   .venv/Scripts/python mcudb/tools/svd2header.py generate --derivative S32K116 --out %TEMP%/s32k/deviceheader
   .venv/Scripts/python mcudb/tools/svd2header.py generate --derivative S32K116 --out %TEMP%/s32k/deviceheader --check
   ```

   `--check` prüft die SHA-256 der SVD und der erzeugten Datei gegen `device_header_rules/s32k1xx.yaml` und meldet einen
   veralteten Header. Der Build-Pfad zum Header ist `NX_DEVICE_HEADER_DIR`
   (`platforms/s32k1xx/bsp/bspMcu/CMakeLists.txt`).

## Was die SVD liefert und was nicht

- Aus der SVD kommen Adressen, Registerlayout, Bitfelder, Instanzen und IRQ-Nummern.
- **Namen** folgen den Regeln in `mcudb/tools/device_header_rules/s32k1xx.yaml` (Ziel: Namen der OpenBSW-Header
  `S32K148*.h`, IRQ-Namen der RTD-Header).
- Bekannte Abweichungen der SVD 1.6 gegenüber Reference Manual Rev. 14: LMEM ist noch enthalten (S32K11x hat keinen
  LMEM, [RM] 33.1) und `<cpu><fpuPresent>` ist `true` (der Cortex-M0+ hat keine FPU). Beides wird in den Regeln
  ausdrücklich korrigiert, mit Beleg im Kommentar.
