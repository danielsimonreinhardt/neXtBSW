<!-- SPDX-License-Identifier: Apache-2.0 -->

# RM46 / rm4x: Audit der Cortex-M-Annahmen (Kurzseite)

Status: **planned (after R1)**. Die Familie `rm4x` (TI Hercules RM4x, Cortex-R4F, Little Endian, Nachweisziel
LAUNCHXL2-RM46) ist kein Teil von R1. Der Audit (ARCH-19, 2026-09-16) mit Fundstellenliste, Messbelegen und
Review-Fragen steht in der Git-Historie dieser Datei (Commit vor DOC-75). Seine Entscheidungen sind verbindlich in
[`README.md`](README.md): **D11** Kernprofile, **D12** Bootloader ohne VTOR, **D13** Familienname, **D14**
Quellenverfahren (Abschnitt 10), dazu die Plattformregel (Abschnitt 6).

## Bild

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart LR
    subgraph M["Cortex-M (today)"]
        NVIC("NVIC, BASEPRI") --- ST("SysTick") --- VT("vector table, VTOR") --- MPUM("ARMv7-M MPU") --- FLX("FlexCAN, FTFC")
    end
    subgraph R["Cortex-R4F (rm4x)"]
        VIM("VIM, IRQ/FIQ") --- RTI("RTI tick") --- FWD("vectors at 0 + forwarding table") --- MPUR("ARMv7-R MPU") --- DCAN("DCAN, F021")
    end
    NVIC -. "NX_CORE_ARCHITECTURE" .-> VIM
    ST -.-> RTI
    VT -.-> FWD
    MPUM -.-> MPUR
    FLX -.-> DCAN

    classDef third fill:#e2e8f0,stroke:#64748b,stroke-width:1.5px,color:#0f172a
    classDef grp fill:#f8fafc,stroke:#cbd5e1,rx:8,ry:8
    class NVIC,ST,VT,MPUM,FLX,VIM,RTI,FWD,MPUR,DCAN third
    class M,R grp
```

## Legende

- **Die Grundannahmen halten:** ILP32, Little Endian, AAPCS mit VFP; Bootloader-Kern und hardwareunabhängige Module
  übersetzen für `-mcpu=cortex-r4f` ohne Meldung; die Toolchain (`arm-none-eabi-gcc`) genügt.
- **Neutral zu machen** war: das Kernmodell in `nxconfig` (NVIC/BASEPRI), die Architekturerkennung (nie über
  `__ARM_ARCH`, es ist 7 für R und M), die Auswahl des FreeRTOS-Ports, Bootloader/Updater/`canflash` (Image-Anfang
  ist beim R4F kein Cortex-M-Vektor), `FreeRtosAdcLock.h` und das Dump-Layout.
- **Unterschiede des RM46:** VIM statt NVIC, RTI statt SysTick, Modus-Stacks und Vektoren ab 0 statt VTOR,
  ARMv7-R-MPU, FreeRTOS-Port `ARM_CRx_No_GIC`, DCAN statt FlexCAN, F021-Flash-API, XDS110 statt J-Link.
- Die Quellen- und Lizenzprüfung steht auf [`rm4x-quellen.md`](rm4x-quellen.md).

Verweise: [Zielbild](README.md), [Plattformen](plattformen.md), [Bootloader](../../bootloader/doc/README.md).
