<!-- SPDX-License-Identifier: Apache-2.0 -->

# Bilder zum Sicherheitskonzept (Konzept Ausgabe 3)

Ergänzende Darstellung **ohne inhaltliche Änderung** des freigegebenen
[Sicherheitskonzepts](../concept.md), Ausgabe 3 (DOC-51, freigegeben 2026-10-04). Maßgeblich ist immer das Konzept;
bei einem Widerspruch gilt der Text dort, und das Bild ist ein Befund. In das Konzept selbst kommen die Bilder erst mit
dessen nächster Ausgabe.

R1 ist eine Entwicklungsfreigabe **ohne Sicherheitsanspruch**.

## K1: Sicherheitsziele → Mechanismen der Stufe 1

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart LR
    A1("ASG-01<br/>unintended torque")
    A2("ASG-02<br/>switch-off path available")
    A3("ASG-03<br/>battery values outside SOA")
    A4("ASG-04<br/>unintended cell balancing")
    A5("ASG-05<br/>corrupted, stale, repeated data")
    A6("ASG-06<br/>control software failure")
    A7("ASG-07<br/>intended image only")

    M1("safe outputs<br/>SM-23 ... SM-25")
    M2("fault handlers, halt deadman<br/>SM-17, SM-06")
    M3("program flow, memory<br/>6.1, 6.2")
    M4("register lock, check<br/>SM-21, SM-22")
    M5("ADC reference, supplies<br/>SM-18 ... SM-20")
    M6("qualifier, E2E, CAN gate<br/>SM-30, SM-31, SM-32")
    M7("sections 6.1 ... 6.5")
    M8("bootloader image check SM-51<br/>ROM check SM-14")
    M0("no stage-1 mechanism<br/>gap G4")

    A1 --> M1
    A1 --> M2
    A1 --> M3
    A2 --> M1
    A2 --> M2
    A2 --> M4
    A3 --> M5
    A3 --> M6
    A4 --> M0
    A5 --> M6
    A6 --> M7
    A7 --> M8

    classDef ok fill:#dcfce7,stroke:#15803d,stroke-width:1.5px,color:#0f172a
    classDef part fill:#fef3c7,stroke:#b45309,stroke-width:1.5px,color:#0f172a
    classDef no fill:#fee2e2,stroke:#b91c1c,stroke-width:1.5px,color:#0f172a
    class A2,A5,A6 ok
    class A1,A7 part
    class A3,A4 no
```

**Legende**

- Farbe = Abdeckung in Stufe 1 (Feld `COVERAGE`): grün `limited` (abgedeckt mit Grenzen), gelb `partial`,
  rot `none`. Alle Ziele sind angenommen, ASIL B.
- Pfeile = Mechanismen, die das Ziel in Stufe 1 anrechnet (Feld `STAGE1_CONTRIBUTION`). Beiträge der Bausteintreiber
  (Stufe 2, SM-40 … SM-43) zählen nicht und fehlen deshalb.
- ASG-03: SM-30/SM-31 schützen den eigenen Messpfad und den Transport, nicht die Zellwerte (Abdeckung `none`).

Quelle: Konzept Ausgabe 3, [Abschnitt 2.2](../concept.md#22-assumed-safety-goals); die Ziele selbst stehen in
[`goals.sdoc`](../../requirements/safety/goals.sdoc).

## K2: Reaktionen und sichere Zustände

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
stateDiagram-v2
    state "normal operation" as RUN
    S3: S3 log and continue
    SD: SD mapped groups degraded
    S1: S1 outputs off
    HELD: S1 held
    S1S2: S1 + S2

    [*] --> RUN
    RUN --> S3: SM-05, SM-34 report
    S3 --> RUN
    RUN --> SD: degradable event, mapped
    SD --> SD: more groups
    SD --> S1: last working group
    RUN --> S1: SM-22
    S1 --> RUN: leaveSafeState, indicator only
    RUN --> HELD: SM-07, SM-14, SM-50, SM-34
    RUN --> S1S2: most events
    S1S2 --> RUN: reset, restart
    S1S2 --> HELD: reset-loop limit
    HELD --> [*]: power cycle
    SD --> [*]: power cycle
```

**Legende**

- Eine Stelle entscheidet: `SafeSupervisor::handle()`. Ein Mechanismus meldet nur.
- S1 ist eine Einbahnstraße nach oben: `leaveSafeState()` gibt nur die Anzeige frei, eine Leistungsstufe kommt nur
  über ein ausdrückliches `enable()` ihres Treibers zurück.
- S1 held: alle Interrupts gesperrt, Tabelle zyklisch neu geschrieben, MCU-Watchdog bedient; Ende nur durch Aus- und
  Einschalten oder Reset von außen.
- SD: nur Ereignisse der geschlossenen Liste (`DEVICE_FAULT` außer `IDENTITY`, Auslösung eines Hardware-Abschaltpfads
  `HW_SHUTOFF`), nur wenn das Projekt sie abbildet; Fehler der MCU selbst nie. Gruppen sammeln sich, nur Aus- und
  Einschalten beendet SD.
- S3 ist nur zulässig, wo die Analyse zeigt, dass innerhalb der FTTI kein Sicherheitsziel verletzt wird (SM-05:
  Reaktion bei der Anwendung, AoU-18).

Quelle: Konzept Ausgabe 3, [Abschnitt 3](../concept.md#3-safe-states).

## K3: Erkennung, Reaktion, Wiederherstellung

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart LR
    subgraph E1["1 Detection"]
        MON("monitor of the safe supervisor<br/>trigger, check, hit<br/>+ context word")
    end
    subgraph E2["2 Reaction"]
        HAN(("handle()"))
        R("S3, SD, S1,<br/>S1 held, S1 + S2")
    end
    subgraph E3["3 Recovery"]
        REC("start reason, ECU state,<br/>SBC reset counter,<br/>after_watchdog_reset")
    end
    BYP("assembler fault handlers,<br/>halt deadman interrupt")
    TAB("safe outputs table<br/>address, value, clock gate")

    MON --> HAN --> R --> REC
    BYP -- "bypass 1 and 2" --> TAB
    R --> TAB

    classDef own fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#0f172a
    classDef safety fill:#fee2e2,stroke:#b91c1c,stroke-width:1.5px,color:#0f172a
    classDef grp fill:#f8fafc,stroke:#cbd5e1,rx:8,ry:8
    class MON,HAN,REC,BYP own
    class R,TAB safety
    class E1,E2,E3 grp
```

**Legende**

- Ebene 1 erkennt und meldet ein Ereignis, Ebene 2 entscheidet, Ebene 3 regelt den Start nach dem Reset.
- Fault-Handler und Halt-Deadman überspringen Ebene 1 und 2 absichtlich: der Code, der sonst entscheidet, ist in
  diesem Moment nicht vertrauenswürdig. Deshalb ist die Tabelle ein schlichtes Feld aus Adresse, Wert und
  Takt-Gate-Wort statt Treiberaufrufen.

Quelle: Konzept Ausgabe 3, [Abschnitt 5](../concept.md#5-how-the-mechanisms-are-organised).

## K4: Zeitbudget – längste Fehlerbehandlungszeit (FHTI) je Klasse

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
xychart-beta
    title "FHTI worst case to de-energised, guaranteed, ms"
    x-axis ["SM-06 halt", "safety task checks", "SM-20 supply round", "SM-19 ADC limit", "SM-01 watchdog"]
    y-axis "ms" 0 --> 300
    bar [10, 17, 102, 170, 283]
```

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart LR
    FTTI("FTTI of the item<br/>integrator, AoU-01")
    FHTI("FHTI of the element<br/>concept 7.2")
    CHK(("FTTI longer than FHTI?<br/>AoU-05"))
    OK("mechanism may serve the goal")
    NO("own measure or no claim")
    FTTI --> CHK
    FHTI --> CHK
    CHK -- yes --> OK
    CHK -- no --> NO
```

**Legende**

- Balken: garantierte Werte der Tabelle 7.2 bis „Ausgänge stromlos“, jeweils zuzüglich des Tabellendurchlaufs
  (< 1 µs gerechnet, am Board ≤ 21 µs begrenzt). Nominal sind die Prüfungen im Safety-Task 10 ms, SM-19 100 ms,
  SM-20 60 ms; SM-01 sind 250 ms bei nominalem LPO, bis etwa 283 ms über den LPO-Bereich.
- Nicht im Balken: Restrisiko DF-05 (SIRC verloren): kein Tabellendurchlauf vor dem Watchdog-Reset nachgewiesen,
  höchstens etwa 283 ms. SM-05 schaltet nichts ab (nur Log).
- Grundwerte (Demo-Projekt): Safety-Task 10 ms, Watchdog-Bedienung alle 70 ms, Watchdog-Zeitablauf 250 ms; geprüft wird
  3 × 70 ms = 210 ms ≤ 230,2 ms (Zeitablauf beim schnellsten LPO).
- Ein Reset ist nicht sofort: der Neustart dauert am Eval Board bis 1 077 ms (Watchdog-Weg) bzw. 858 ms (MPU-Fehler).
- Das Element legt keine FTTI fest; der Integrator vergleicht je Ziel (AoU-05).

Quelle: Konzept Ausgabe 3, [Abschnitt 7.1](../concept.md#71-where-the-times-come-from),
[Abschnitt 7.2](../concept.md#72-worst-case-time-to-the-reaction-per-class) und
[Abschnitt 7.3](../concept.md#73-what-this-means-for-the-integrator).
