# Synthetische Speicheraufteilungen für Bootloader-Host-Tests (PLAT-16)

Diese Dateien beschreiben je eine **Speicheraufteilung als Testdaten** – ein Derivat plus Aufteilung, für das es
(noch) kein Projektprofil gibt. `tools/canflash/hostbuild.py` (`write_test_layout`) erzeugt daraus genau die Makros
der generierten `bootloader/bl_memory.h`; der C-Code merkt keinen Unterschied. Der Sektortabellen-Block entsteht
mit derselben Funktion (`sector_table_macros`), die auch die Ausgabe eines Projekts ergänzt und die der Vorschlag
für `tools/bswgen/blgen.py` übernimmt – die beiden Wege können nicht auseinanderlaufen.

**Kein Ersatz für ein Profil.** Sobald `mcudb/stm32/STM32F446RE.yaml` und
`tools/nxconfig/test/profiles/projects/buildtest_stm32f446_bl/` existieren (Vorschläge in
`doc/changelog.d/PLAT-16.md`, beide Pfade waren für PLAT-16 gesperrt), gehören diese Aufteilungen dorthin und die
Host-Tests laufen wie die anderen über `blgen`. Bis dahin sind die Werte hier **Annahmen** aus den Belegen, die
in jedem Eintrag unter `source` stehen.

| Datei | Zweck |
|---|---|
| `s32k116_2k.yaml` | gleich große 2-KB-Sektoren, kleinstes Derivat der Fokusfamilie (`mcudb/s32k1xx/S32K116.yaml`) |
| `stm32f446_unequal.yaml` | **ungleiche** Sektoren 16/64/128 KB, Record **unter** der Anwendung (wählbare Record-Position) |

Felder: `flash` (`start`, `size`, `program_size`, `sector_size` **oder** `sectors: [{size, count}]` ab
Regionsanfang), `bootloader_size`, `record_address`, `app_start`, `app_end` (exklusiv), `ram` (`start`, `end`),
`boot_request`, `family` (MCU-Familie: wählt die Familien-Hooks `bootloader/platforms/<family>` des Host-Baus und
steht als `BL_FAMILY` in `bl_memory.h`, BL-08), `target` (Board- und Derivatname für `BL_TARGET_ID`) und
`diagnostics` (COM-11: `request_id`,
`functional_id`, `response_id`, `padding`, `block_size`, `stmin_ms`, `n_cr_ms`, `n_bs_ms`, `tester_address`,
`ecu_address`, `version_prefix`, `scan_start` – Vorgaben in `hostbuild.DEFAULT_DIAG`). Aus `diagnostics` entsteht
`bootloader/bl_diag.h` mit derselben Vorlage, die `blgen` für ein Projekt benutzt (`blgen.diag_header`).
