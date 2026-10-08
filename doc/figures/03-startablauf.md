<!-- SPDX-License-Identifier: Apache-2.0 -->

# Bild 3: Startablauf

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart TD
    RST(["Reset"])

    subgraph BL["Bootloader, bl_main.c"]
        INIT("bl_init()<br/>HAL, transport, UDS")
        BREQ(("boot request?"))
        VALID(("app_image_valid()<br/>record, compat, entry, CRC-32"))
        SKIP(("reset of the<br/>WDOG fast test?"))
        WIN("startup window<br/>BL_STARTUP_WINDOW_MS")
        CONF(("app_image_confirm()<br/>record re-read"))
        STAY("stay in bootloader<br/>UDS reachable")
        JUMP("hal_jump_to_app()")
    end

    subgraph APP["Application, s32k1xx port main.cpp"]
        E1("integration::earlyInit()")
        E2("MPU init, safety shell<br/>limp home, WDOG fast test SM-07,<br/>WDOG on")
        E3("preBspInit(), staticBsp.init()")
        E4("hardfault::reportAndClearDump()")
        E5("app_main(), FreeRTOS, startApp()")
    end

    subgraph LC["Lifecycle levels"]
        LC1("1: platform, runtime,<br/>safety, ecu state")
        LC2("2: CAN")
        LC3("3: uds")
        LC8("8: sysadmin")
        LC9("9: rte")
    end

    ST("ecuState: Startup → Run")

    RST --> INIT --> BREQ
    BREQ -- yes --> STAY
    BREQ -- no --> VALID
    VALID -- no --> STAY
    VALID -- yes --> SKIP
    SKIP -- "yes: 0 ms" --> CONF
    SKIP -- no --> WIN
    WIN -- "10 02 received" --> STAY
    WIN -- "window over" --> CONF
    CONF -- no --> STAY
    CONF -- yes --> JUMP --> E1 --> E2 --> E3 --> E4 --> E5
    E2 -.->|2 resets| RST
    E5 --> LC1 --> LC2 --> LC3 --> LC8 --> LC9 --> ST

    classDef own fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#0f172a
    classDef grp fill:#f8fafc,stroke:#cbd5e1,rx:8,ry:8
    class ST,INIT,BREQ,VALID,SKIP,WIN,CONF,STAY,JUMP,E1,E2,E3,E4,E5,LC1,LC2,LC3,LC8,LC9 own
    class BL,APP,LC grp
```

**Legende**

- Der Bootloader läuft nach jedem Reset zuerst. Er bleibt bei Boot-Anforderung (Anwendung hat `10 02` beantwortet)
  oder bei ungültigem Abbild; sonst wartet er das Startfenster ab (Eval Board: 200 ms) und springt.
- Die beiden Resets des Watchdog-Schnelltests (SM-07) erkennt die Familie (`hal_reset_skips_startup_window()`, BL-33):
  dann ohne Startfenster. Die CRC-32 über das Abbild läuft einmal je Start (BL-38); vor dem Sprung prüft
  `app_image_confirm()` Record, Kompatibilität und Einsprung erneut und vergleicht die CRC aus `bl_init()`.
- Der Bootloader läuft ohne MCU-Watchdog (Sicherheitshandbuch Abschnitt 4).
- Lifecycle: neun Ebenen (`MaxNumLevels = 9`); die RTE startet bewusst zuletzt. `ecuState` wechselt nach Run, wenn
  Ebene 9 erreicht ist (Bild 5).
- Türkis: neXtBSW-Code. Kreis: Entscheidung.
- Gestrichelt: der Schnelltest löst absichtlich zwei Resets aus; danach läuft derselbe Weg erneut.

Quellen: [Architekturüberblick, Abschnitt 7](../architecture/overview.md#7-start-up-sequence-on-the-device),
[`bootloader/src/core/bl_main.c`](../../bootloader/src/core/bl_main.c),
[Bootloader-Doku, Abschnitt 4](../../bootloader/doc/README.md#4-security-access-identification-and-fingerprint-bl-03-bl-04-bl-10),
[Sicherheitshandbuch, Abschnitt 4](../safety/safety-manual.md#4-safe-states-and-reactions).
