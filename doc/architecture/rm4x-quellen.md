<!-- SPDX-License-Identifier: Apache-2.0 -->

# rm4x: Quellen- und Lizenzprüfung (Kurzseite)

Status: **planned (after R1)**. Die Familie `rm4x` (TI RM46L852, Cortex-R4F) ist kein Teil von R1. Die Prüfung
(PLAT-44, 2026-09-17, Grundlage der Entscheidung D14) steht mit allen Einzelbefunden und Quellen in der Git-Historie
dieser Datei (Commit vor DOC-75). **Keine Rechtsberatung:** bewertet ist, was die Lizenztexte und offiziellen Seiten
wörtlich sagen. Herkunft der Familie und Kernprofile: [`non-cortex-m-audit.md`](non-cortex-m-audit.md),
[`README.md`](README.md) (D11 bis D14, Plattformregel in Abschnitt 6).

## Bild

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart LR
    subgraph REPO["in the repository"]
        FRT("FreeRTOS port ARM_CRx_No_GIC<br/>(MIT)")
        GEN(["register headers<br/>generated from the TRM"])
    end
    subgraph WS["workstation level"]
        F021("F021 flash API<br/>NX_TI_F021_DIR")
        PDF("TI PDFs<br/>checksum only")
        TOOLS("UniFlash/dslite, OpenOCD<br/>XDS110")
    end
    FRT --> PLAT("platforms/rm4x")
    GEN --> PLAT
    F021 -. "no redistribution" .-> PLAT

    classDef own fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#0f172a
    classDef third fill:#e2e8f0,stroke:#64748b,stroke-width:1.5px,color:#0f172a
    classDef gen fill:#fef3c7,stroke:#b45309,stroke-width:1.5px,color:#0f172a
    classDef grp fill:#f8fafc,stroke:#cbd5e1,rx:8,ry:8
    class PLAT own
    class FRT,F021,PDF,TOOLS third
    class GEN gen
    class REPO,WS grp
```

## Legende

| Quelle | Lizenz | ins Repository? | Empfehlung |
|---|---|---|---|
| HALCoGen (Werkzeug) | TI-Lizenz im Installer, Text nicht geprüft | nein | nur Werkzeug am Arbeitsplatz zum Nachschlagen |
| HALCoGen-Ausgabe (`reg_*.h`, `sys_core.S`, ...) | BSD-3-Clause im Dateikopf | erlaubt | nur als Referenz; Registerheader selbst aus dem TRM erzeugen |
| F021 Flash API (Binärbibliothek) | eigene Lizenz, Text nicht öffentlich | unklar, vorerst nein | Arbeitsplatz-Abhängigkeit `NX_TI_F021_DIR`; Linken mit GCC machbar, ABI-Risiko niedrig |
| FreeRTOS `portable/GCC/ARM_CRx_No_GIC` (V10.6.2, V11.2.0+) | MIT | erlaubt | verwenden; V11.2.0+ technisch besser, Mischung mit dem OpenBSW-Kernel prüfen |
| FreeRTOS-Demo für RM46, ältere Cortex-R4-Ports | MIT, TI BSD-3 | nicht nötig | Demo nur als Referenz, CCS-Ports verwerfen |
| TI-Dokumente (TRM, Datenblatt, F021-Guide, LaunchPad-Guide) | TI-Hinweis, Vervielfältigung eingeschränkt | unklar | nur Verweis und Prüfsumme im Repository |
| Registerheader selbst aus dem TRM erzeugen | eigene Arbeit | erlaubt | empfohlen (kein SVD für Hercules vorhanden) |
| UniFlash/`dslite`, OpenOCD + XDS110 | frei verfügbar, GPL-2.0-or-later | nein (Werkzeuge) | Arbeitsplatz-Werkzeuge; OpenOCD hat keinen F021-Flashtreiber, Flashen über UniFlash oder den eigenen Bootloader |

Fazit: Rechtlich unkritisch und ins Repository übernehmbar sind nur FreeRTOS (MIT) und selbst erzeugte Registerheader.
Die F021-Bibliothek bleibt wie das S32K116-SVD auf Arbeitsplatzebene, bis der Nutzer den Lizenztext im Installer
gelesen hat.
