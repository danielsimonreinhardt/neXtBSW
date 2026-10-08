<!-- SPDX-License-Identifier: Apache-2.0 -->

# Beiträge an Eclipse OpenBSW (Kurzseite)

Status: **planned (after R1)**. Vorbereitet sind Fehlermeldungen und Patches für
[Eclipse OpenBSW](https://github.com/eclipse-openbsw/openbsw) (PLAT-53, PLAT-60, PLAT-65, PLAT-110); **nichts ist
eingereicht**. Eingereicht wird durch den Nutzer (Entscheidung 2026-09-17, ARCH-30 F4): Agenten bereiten vor, der
Nutzer signiert das Eclipse Contributor Agreement (dieselbe E-Mail-Adresse wie `user.email` in Git) und öffnet Issue
und Pull Request mit seinem GitHub-Konto. Einreichungsreihenfolge je Paket: Issue zuerst, dann Patch mit `git am`
auf `upstream/main`, `Resolves:` mit Issue-Nummer, `pre-commit run --all-files`, Pull Request.

Der Langtext (Issue-Texte, `pr.md`, `tests.md`, `einreichen.md` je Paket, `upstream-stand.md` mit der Recherche vom
2026-09-17/18) steht in der Git-Historie dieser Ordner (Commit vor dem Kürzungs-Commit zu DOC-75). Die Patches
(`fix.patch`, Basis OpenBSW `3b6e702`) und `12-spi-adc-contracts/issue.md` (verlinkt aus `libs/bsp/bspSpi`) bleiben hier als Arbeitsmaterial. Hintergrund und Auswahl:
[`openbsw-naehe.md`](../architecture/openbsw-naehe.md), Überblick: [`openbsw.md`](../architecture/openbsw.md) Abschnitt 7.

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart LR
    F("finding in neXtBSW<br/>(workaround or copy)") --> I[("issue text + fix.patch<br/>against OpenBSW main")]
    I --> U("user: ECA, issue, pull request")
    U --> A("accepted upstream")
    A --> R("workaround deleted,<br/>OpenBSW file used unchanged")

    classDef own fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#0f172a
    classDef third fill:#e2e8f0,stroke:#64748b,stroke-width:1.5px,color:#0f172a
    classDef data fill:#f1f5f9,stroke:#94a3b8,stroke-width:1.5px,color:#0f172a
    class R,F own
    class A third
    class I data
```

## Pakete

| Ordner | Thema |
|---|---|
| `01-mpu-region-count` | MPU: Regionenzahl je Derivat, `isGlobalEnabled()` nur `CESR[VLD]`, `slot <` statt `slot <=` |
| `02-flexcan-bus-state` | FlexCAN: Busstatus und Fehlerzähler mit Feldextraktion statt Setter-Makro |
| `03-hard-fault-handler` | Hard Fault: `printf` vor dem Sprung entfernt (naked), Dump-Ort aus `__NOINIT_DATA` |
| `04-cmsis-include-path` | `bspMcu`: CMSIS-Pfad nicht mehr über `CMAKE_SOURCE_DIR` |
| `05-mismatched-tag` | `DiagnosisConfiguration` als `struct` vorwärtsdeklariert (`-Wmismatched-tags`) |
| `06-system-timer-dwt-remainder` | `SystemTimer::updateTicks()` verwirft den DWT-Divisionsrest |
| `07-s32k1xx-derivative-guards` | `pllOff()`/`isSoscValid()` außerhalb `PLL_AVAILABLE`, `SYS_GetActive()` auf ARMv6-M, `cache.cpp` ohne LMEM |
| `08-adc-reference-check` | Division durch den ungeprüften Bandgap-Rohwert, Referenzfenster als benannte Konstanten |
| `09-flash-config-field` | `.FlashConfig` fehlt: gelöschtes FCF, FSEC `0xFF` = gesichert |
| `10-cmsis-core-cm0plus` | `core_cm0plus.h` in `libs/3rdparty/cmsis` (nur Issue, kein Patch) |
| `11-safety-event-context` | gemessene mV als Kontext von `ADC_REFERENCE_ERROR`/`INTERNAL_SUPPLIES_ERROR` |
| `12-spi-adc-contracts` | hardwareneutrale SPI- und ADC-Verträge in `libs/bsp` (nur Issue, kein Patch) |
| `13-console-tester-ranges` | Konsolen-Tester `out`/`pwm`: Zahl wird vor der Bereichsprüfung auf `OutputId` gekürzt (`out set 259 1` schreibt Ausgang 3); neXtBSW hat eigene geprüfte Tester (SYS-150, PLAT-110) |

Lokale Branches im Fork-Clone (PLAT-65) für die Pakete 01 bis 09 und 11 sind nur lokal vorbereitet; es wurde nichts
gepusht.
