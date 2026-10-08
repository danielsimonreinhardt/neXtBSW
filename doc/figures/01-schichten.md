<!-- SPDX-License-Identifier: Apache-2.0 -->

# Bild 1: Schichten

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart TB
    subgraph L5["L5 Application"]
        SWC("SWC<br/>swc/, projects/p/swc/<br/>Simulink, AUTOSAR Classic")
    end
    subgraph L4["L4 RTE"]
        RTE(["RTE, tasks, qualifiers<br/>tools/rtegen, generated/rte"])
    end
    subgraph L3["L3 ECU services"]
        CANS("canSystem")
        ECUS("ecuState")
        UDSA("ecu/uds")
        CONS("ecu/consoleCommands")
        SUP("safety supervisor")
        OMW("uds, docan, cpp2can, transport<br/>lifecycle, async, logger")
    end
    subgraph L2["L2 Device drivers"]
        DRV("libs/bsw<br/>sbcUja113x, gdTmc6200,<br/>highSideSwitch, canPhyPassive, ...")
    end
    subgraph L1["L1 MCU abstraction"]
        CON("libs/bsp<br/>contracts: bspSpi, bspAdcAccess, ...")
        FAM("platforms/family/bsp")
        OBSP("openbsw libs/bsp<br/>CanPhy, SystemTimer, DigitalOutput, ...")
        OPLT("openbsw platforms<br/>s32k1xx, stm32")
        RTOS("FreeRTOS")
    end
    subgraph L0["L0 Hardware, described only"]
        MCUDB[("mcudb: derivative")]
        BRD[("boards: board.yaml")]
    end
    BL("bootloader/<br/>C, own HAL per family,<br/>no OpenBSW")

    L5 --> L4 --> L3 --> L2 --> L1 --> L0
    BL -.- L0

    classDef own fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#0f172a
    classDef third fill:#e2e8f0,stroke:#64748b,stroke-width:1.5px,color:#0f172a
    classDef gen fill:#fef3c7,stroke:#b45309,stroke-width:1.5px,color:#0f172a
    classDef data fill:#f1f5f9,stroke:#94a3b8,stroke-width:1.5px,color:#0f172a
    classDef app fill:#ede9fe,stroke:#6d28d9,stroke-width:1.5px,color:#0f172a
    classDef grp fill:#f8fafc,stroke:#cbd5e1,rx:8,ry:8
    class CANS,ECUS,UDSA,CONS,CON,SUP,DRV,FAM,BL own
    class OMW,OBSP,OPLT,RTOS third
    class RTE gen
    class MCUDB,BRD data
    class SWC app
    class L5,L4,L3,L2,L1,L0 grp
```

**Legende**

- Grau: Fremdcode, aus Eclipse OpenBSW (`external/openbsw`, Submodul, unverändert) und FreeRTOS. Türkis: neXtBSW.
  Violett: Anwendung (SWC). Gelb: generiert, nie eingecheckt. Hellgrau (Zylinder): nur beschrieben (YAML), kein Code.
- Pfeile: Abhängigkeiten zeigen nur nach unten; keine Schicht kennt ein Board.
- Der Bootloader steht bewusst außerhalb des Schichtenstapels (Entscheidung D6) und liest dieselbe Speicheraufteilung
  wie die Anwendung.
- `ecu/uds` nutzt die OpenBSW-Bibliothek `uds`; `ecu/` selbst ist aus der OpenBSW-Referenzanwendung abgeleitet.

Quellen: [Architekturüberblick, Abschnitt 2](../architecture/overview.md#2-layers),
[neXtBSW und OpenBSW, Abschnitte 2 und 3](../architecture/openbsw.md#2-what-nextbsw-takes).
