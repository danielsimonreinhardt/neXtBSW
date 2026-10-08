<!-- SPDX-License-Identifier: Apache-2.0 -->

# Bild 6: Safety-Kette

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart LR
    subgraph DET["Detection"]
        D1("program flow, time<br/>SM-02 ... SM-05, SM-07")
        D2("memory<br/>SM-11 ... SM-16, SM-55")
        D3("periphery, supply, registers<br/>SM-18 ... SM-22")
        D4("device reports<br/>SM-34")
        D5("start reason<br/>SM-50")
    end

    H(("SafeSupervisor<br/>handle()"))

    subgraph REA["Reaction"]
        S3("S3<br/>log, continue")
        SD("SD<br/>mapped groups only")
        S1("S1<br/>no reset")
        HELD("S1 held<br/>no reset")
        S1S2("S1 + S2<br/>software reset")
    end

    subgraph SAFE["Safe state"]
        TAB("safe outputs table SM-23<br/>PWM groups inactive,<br/>safe_off outputs off")
        RST("reset, restart")
        HOLD("held until power cycle")
    end

    DIRECT("direct, without handle():<br/>fault handlers SM-17,<br/>halt deadman SM-06,<br/>WDOG warning IRQ SM-01")

    D1 --> H
    D2 --> H
    D3 --> H
    D4 --> H
    D5 --> H
    H -- "SM-05, SM-34 report" --> S3
    H -- "SM-34 fault, mapped" --> SD
    H -- "SM-22" --> S1
    H -- "SM-07, SM-14, SM-50, SM-34" --> HELD
    H -- "most events" --> S1S2
    SD --> TAB
    S1 --> TAB
    HELD --> TAB --> HOLD
    S1S2 -- "SM-25: S1 first" --> TAB
    S1S2 --> RST
    DIRECT --> TAB
    DIRECT --> RST

    classDef own fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#0f172a
    classDef safety fill:#fee2e2,stroke:#b91c1c,stroke-width:1.5px,color:#0f172a
    classDef grp fill:#f8fafc,stroke:#cbd5e1,rx:8,ry:8
    class D1,D2,D3,D4,D5,H,DIRECT own
    class S3,SD,S1,HELD,S1S2,TAB,RST,HOLD safety
    class DET,REA,SAFE grp
```

**Legende**

- Rot: Reaktion und sicherer Zustand. Türkis: Erkennung und die Entscheidung in `handle()` (Kreis).
- Drei Ebenen: Erkennung meldet nur ein Ereignis, entschieden wird an genau einer Stelle (`SafeSupervisor::handle()`),
  danach folgt die Wiederherstellung (Start nach dem Reset).
- S1: Ausgänge stromlos über die eine Tabelle des Projekts. S2: Software-Reset, immer erst nach S1 (SM-25).
  S1 held: S1 ohne Reset, alle Interrupts gesperrt, die Tabelle wird zyklisch erneut geschrieben, Ende nur durch
  Aus- und Einschalten. S3: nur Log. SD: nur die abgebildeten Ausgangsgruppen, ohne Reset.
- `SM-34` erscheint dreimal: Meldung → S3, Fehler der Klasse `IDENTITY` → S1 held, andere Klassen → S1 + S2 oder,
  wenn das Projekt sie abbildet, SD.
- Ohne `handle()`: Fault-Handler (SM-17), Halt-Deadman (SM-06) und der Watchdog-Warn-Interrupt (SM-01) schreiben die
  Tabelle direkt, weil der Code, der sonst entscheidet, in diesem Moment nicht vertrauenswürdig ist.
- Nicht über `handle()`: SM-30/SM-31 (die Anwendung entscheidet), SM-32 (Senden gesperrt), SM-51 (Bootloader startet
  ein ungültiges Abbild nicht).

Quellen: [Sicherheitskonzept Ausgabe 3, Abschnitte 3 und 5](../safety/concept.md#5-how-the-mechanisms-are-organised),
[Sicherheitshandbuch, Abschnitt 4](../safety/safety-manual.md#4-safe-states-and-reactions),
Mechanismen-Katalog [`mechanisms.sdoc`](../requirements/safety/mechanisms.sdoc) (Feld `REACTION`).
