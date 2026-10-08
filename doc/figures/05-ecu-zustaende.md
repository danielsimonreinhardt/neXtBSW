<!-- SPDX-License-Identifier: Apache-2.0 -->

# Bild 5: ECU-Zustandsautomat

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
stateDiagram-v2
    state "ecuState" as ECU {
        [*] --> Startup
        Startup --> Run: lifecycle level 9
        Run --> Standby: sleep request, min_run_ms
        Standby --> Run: request withdrawn
        Standby --> Rundown: post_run_ms
        Rundown --> Shutdown: NVM, CAN_STOP, SAFE_OUTPUTS done
        Rundown --> Restart: step failed or timeout
        Shutdown --> Sleep: lifecycle level 1
        Shutdown --> Restart: shutdown_timeout_ms or wake-up
        Sleep --> Restart: refused or sleep_timeout_ms
        Sleep --> [*]: MCU unpowered
        Restart --> Startup: lifecycle level 0, reset
    }

    S1: S1 outputs off
    HELD: S1 held, no reset
    S1S2: S1 + S2 reset
    SD: SD groups degraded

    [*] --> ECU: reset or wake-up
    ECU --> S1: SM-22
    ECU --> HELD: SM-07, SM-14, SM-50, SM-34
    ECU --> S1S2: most SM events
    ECU --> SD: mapped DEVICE_FAULT, HW_SHUTOFF
    S1S2 --> ECU: restart
    S1S2 --> HELD: reset-loop limit
    SD --> S1: last group
    HELD --> [*]: power cycle
```

**Legende**

- Kasten `ecuState`: Normalbetrieb nach [`ecuState.md`](../../libs/bsw/ecuState/doc/ecuState.md).
  **Nachlauf** = Standby (Anwendung, CAN und RTE laufen weiter) und Rundown (Schritte NVM, CAN_STOP, SAFE_OUTPUTS in
  fester Reihenfolge, je Schritt eine Zeitgrenze). Ab Rundown gibt es keinen Rückweg nach Run.
- `Restart` heißt Lifecycle-Ebene 0 und damit Software-Reset; der neue Start läuft über den Bootloader (Bild 3).
- Außen: Reaktionen des Safe Supervisors, unabhängig vom ECU-Zustand (Bild 6). SD gibt es nur, wenn das Projekt
  Ereignisse auf Ausgangsgruppen abbildet; die letzte Gruppe führt zu S1 ohne Reset.
- Was nach einem Watchdog-Reset passiert, ist einstellbar (`modules.ecuState.after_watchdog_reset: sleep`, SM-50).

Quellen: [`ecuState.md`, Zustände und Übergänge](../../libs/bsw/ecuState/doc/ecuState.md#zustände-und-übergänge),
[Sicherheitskonzept Ausgabe 3, Abschnitt 3](../safety/concept.md#3-safe-states),
[Sicherheitshandbuch, Abschnitt 4](../safety/safety-manual.md#4-safe-states-and-reactions).
