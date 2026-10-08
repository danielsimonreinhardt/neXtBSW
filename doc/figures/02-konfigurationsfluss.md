<!-- SPDX-License-Identifier: Apache-2.0 -->

# Bild 2: Konfigurationsfluss

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart LR
    DER[("Derivative<br/>mcudb/family/derivative.yaml<br/>+ pinmux.yaml")]
    BRD[("board.yaml<br/>boards/board/")]
    PRJ[("project.yaml<br/>projects/project/")]
    RTEY[("rte.yaml + *.dbc")]
    SLX("SWC model .slx")
    MAT("swc_build.m<br/>MATLAB, by hand")
    ARX(["swc generated<br/>src + arxml"])

    NXC(("nxconfig<br/>load + check"))

    subgraph GEN["Generators, CMake configure"]
        BSW(["bswgen<br/>board headers"])
        BLG(["blgen<br/>memory layout,<br/>bootloader board part"])
        RTG(["rtegen + dbcgen<br/>RTE, tasks, CAN"])
    end

    BUILD(["CMake + Ninja<br/>arm-none-eabi-gcc<br/>-Werror"])
    HEX[("app.ecu.hex + record<br/>bootloader.hex")]
    PROBE("flash-bootloader, flash<br/>debug probe")
    CANF("flash-can<br/>canflash, UDS over CAN")

    PRJ -- "project.board" --> BRD -- "board.mcu" --> DER
    PRJ -- "project.rte" --> RTEY
    DER --> NXC
    BRD --> NXC
    PRJ --> NXC
    RTEY --> RTG
    SLX --> MAT --> ARX --> RTG
    NXC --> BSW
    NXC --> BLG
    NXC --> RTG
    BSW --> BUILD
    BLG --> BUILD
    RTG --> BUILD
    BUILD --> HEX
    HEX --> PROBE
    HEX --> CANF

    classDef own fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#0f172a
    classDef gen fill:#fef3c7,stroke:#b45309,stroke-width:1.5px,color:#0f172a
    classDef data fill:#f1f5f9,stroke:#94a3b8,stroke-width:1.5px,color:#0f172a
    classDef app fill:#ede9fe,stroke:#6d28d9,stroke-width:1.5px,color:#0f172a
    classDef grp fill:#f8fafc,stroke:#cbd5e1,rx:8,ry:8
    class PROBE,CANF own
    class ARX,NXC,BSW,BLG,RTG,BUILD gen
    class DER,BRD,PRJ,RTEY,HEX data
    class SLX app
    class GEN grp
```

**Legende**

- Hellgrau (Zylinder): eingecheckte Konfiguration, je Wert genau eine Ebene: Derivat (Herstellerfakten), Board (was bestückt und
  verdrahtet ist), Projekt (was diese ECU nutzt). Gewählt wird beim Bauen nur das Projekt.
- Kreis: `tools/nxconfig` liest Projekt → Board → Derivat, prüft über alle Ebenen und meldet jeden Fehler mit Datei,
  Zeile und YAML-Pfad; ein Fehler bricht den Configure-Lauf ab.
- Gelb: erzeugt nach `<build>/generated/`, nie eingecheckt (Pille: ein Werkzeuglauf). Violett: das Simulink-Modell.
  Ausnahme: der Code einer Simulink-Komponente (`swc/<Swc>/generated`) wird eingecheckt, weil MATLAB nicht frei ist; ein Hash meldet einen veralteten Stand.
- Ergebnis (Zylinder) und Flash-Wege (türkis). `flash-bootloader` und `flash` gehen über den Debug-Probe des Boards, `flash-can`
  über den Bootloader (Bild 4).

Quellen: [Architekturüberblick, Abschnitte 3 bis 6](../architecture/overview.md#3-the-three-configuration-levels),
[Build and flash, Schritt 7](../guide/build-and-flash.md#step-7-flash).
